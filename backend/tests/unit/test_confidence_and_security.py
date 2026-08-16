"""Confidence scoring, schema handling, and security primitives."""

from __future__ import annotations

import pytest

from docflow.domain.confidence import (
    HIGH_THRESHOLD,
    MEDIUM_THRESHOLD,
    ConfidenceSignals,
    aggregate,
    band_for,
    grounding_score,
    normalise_for_matching,
    score_field,
)
from docflow.domain.enums import ConfidenceBand, OrgRole
from docflow.llm.schema import ensure_nullable, normalize_schema
from docflow.schemas.registry import (
    build_dynamic_spec,
    get_registry,
    validate_custom_definition,
)
from docflow.security.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from docflow.security.tokens import (
    api_key_prefix,
    generate_api_key,
    hash_api_key,
    looks_like_api_key,
)


class TestGrounding:
    SOURCE = "Faktura cislo: 2024-0412\nCelkem k uhrade: 39 930,00 CZK\nACME s.r.o."

    def test_verbatim_value_scores_full(self):
        assert grounding_score("2024-0412", self.SOURCE) == 1.0

    def test_normalised_number_matches_formatted_source(self):
        """`39930.00` extracted vs `39 930,00` printed — the same number."""
        assert grounding_score("39930.00", self.SOURCE) == 1.0

    def test_absent_value_scores_lowest(self):
        assert grounding_score("999999.99", self.SOURCE) == 0.10

    def test_hallucinated_company_scores_lowest(self):
        assert grounding_score("Nonexistent Holdings Ltd", self.SOURCE) == 0.10

    def test_partial_token_match_scores_between(self):
        score = grounding_score("ACME Nonexistent", self.SOURCE)
        assert 0.10 < score < 1.0

    def test_none_scores_lowest(self):
        assert grounding_score(None, self.SOURCE) == 0.10

    def test_precomputed_normalised_source_matches_inline(self):
        pre = normalise_for_matching(self.SOURCE)
        assert grounding_score("2024-0412", self.SOURCE, source_normalised=pre) == 1.0


class TestConfidenceComposition:
    def test_unknown_signals_are_excluded_not_zeroed(self):
        """A missing signal must not be scored as evidence of a problem."""
        only_grounding = ConfidenceSignals(grounding=1.0).weighted()
        assert only_grounding == pytest.approx(1.0)

    def test_no_signals_lands_on_the_review_boundary(self):
        assert ConfidenceSignals().weighted() == MEDIUM_THRESHOLD

    def test_failed_validation_drags_score_down(self):
        good = score_field(
            "total",
            signals=ConfidenceSignals(
                grounding=1.0, format_cleanliness=1.0, validation=1.0, context=0.95
            ),
        )
        bad = score_field(
            "total",
            signals=ConfidenceSignals(
                grounding=1.0, format_cleanliness=1.0, validation=0.05, context=0.95
            ),
        )
        assert bad.score < good.score
        assert good.band is ConfidenceBand.HIGH

    def test_ungrounded_value_falls_out_of_the_high_band(self):
        result = score_field(
            "supplier.name",
            signals=ConfidenceSignals(
                grounding=0.10, format_cleanliness=1.0, validation=1.0, context=0.95
            ),
        )
        assert result.band is not ConfidenceBand.HIGH
        assert result.needs_review is True

    @pytest.mark.parametrize(
        ("score", "band"),
        [
            (1.0, ConfidenceBand.HIGH),
            (HIGH_THRESHOLD, ConfidenceBand.HIGH),
            (0.7, ConfidenceBand.MEDIUM),
            (MEDIUM_THRESHOLD, ConfidenceBand.MEDIUM),
            (0.3, ConfidenceBand.LOW),
            (0.0, ConfidenceBand.LOW),
        ],
    )
    def test_band_boundaries(self, score, band):
        assert band_for(score) is band

    def test_forced_review_overrides_a_high_band(self):
        result = score_field("due_date", signals=ConfidenceSignals(grounding=1.0))
        assert result.needs_review is False
        result.forced_review = True
        assert result.needs_review is True


class TestAggregate:
    def test_one_bad_required_field_drags_the_document_down(self):
        """20 good fields plus a wrong bank account is not a 95% good document."""
        fields = [score_field(f"f{i}", signals=ConfidenceSignals(grounding=1.0)) for i in range(20)]
        fields.append(score_field("bank_details.iban", signals=ConfidenceSignals(grounding=0.1)))
        overall = aggregate(fields, required_paths={"bank_details.iban"})
        assert overall <= 0.2

    def test_without_required_paths_it_is_the_mean(self):
        fields = [
            score_field("a", signals=ConfidenceSignals(grounding=1.0)),
            score_field("b", signals=ConfidenceSignals(grounding=0.0)),
        ]
        assert aggregate(fields) == pytest.approx(0.5, abs=0.01)

    def test_empty_is_zero(self):
        assert aggregate([]) == 0.0


class TestSchemaNormalisation:
    @pytest.mark.parametrize("key", ["invoice", "contract", "purchase_order", "receipt", "generic"])
    def test_every_builtin_type_produces_a_provider_safe_schema(self, key):
        import json

        raw = get_registry().resolve(key).json_schema()
        normalized = normalize_schema(raw)
        text = json.dumps(normalized)

        for banned in ("minLength", "maxLength", "minimum", "maximum", "multipleOf", "pattern"):
            assert banned not in text, f"{banned} survived normalisation for {key}"
        assert "docflow" not in text, "internal metadata leaked into the provider schema"
        assert normalized["additionalProperties"] is False

    def test_field_hints_become_descriptions(self):
        schema = normalize_schema(get_registry().resolve("invoice").json_schema())
        assert "description" in schema["properties"]["total"]
        assert "including tax" in schema["properties"]["total"]["description"]

    def test_nested_objects_also_forbid_additional_properties(self):
        schema = normalize_schema(get_registry().resolve("invoice").json_schema())
        for definition in schema.get("$defs", {}).values():
            if definition.get("type") == "object":
                assert definition["additionalProperties"] is False

    def test_strict_mode_requires_all_properties_and_allows_null(self):
        raw = get_registry().resolve("invoice").json_schema()
        strict = ensure_nullable(normalize_schema(raw, require_all_properties=True))
        assert set(strict["required"]) == set(strict["properties"])
        total = strict["properties"]["total"]
        assert any(option.get("type") == "null" for option in total.get("anyOf", [])), (
            "optional fields must remain expressible as null under strict mode"
        )


class TestCustomDocumentTypes:
    def test_a_valid_definition_compiles_into_a_working_spec(self):
        spec = build_dynamic_spec(
            key="delivery_note",
            name="Delivery note",
            description="Goods received",
            version=1,
            definition={
                "fields": [
                    {"name": "note_number", "type": "identifier", "required": True},
                    {"name": "delivered_on", "type": "date"},
                    {"name": "total_items", "type": "number"},
                ]
            },
        )
        assert spec.key == "delivery_note"
        assert "note_number" in spec.required_paths
        model = spec.model.model_validate({"note_number": "DN-1", "delivered_on": "14.03.2024"})
        assert str(model.delivered_on) == "2024-03-14"

    @pytest.mark.parametrize(
        "definition",
        [
            {"fields": []},
            {"fields": [{"name": "1bad", "type": "string"}]},
            {"fields": [{"name": "__proto__", "type": "string"}]},
            {"fields": [{"name": "ok", "type": "executable"}]},
            {"fields": [{"name": "dup", "type": "string"}, {"name": "dup", "type": "string"}]},
            {"fields": [{"name": f"f{i}", "type": "string"} for i in range(100)]},
        ],
    )
    def test_hostile_definitions_are_rejected(self, definition):
        from docflow.domain.errors import ValidationRequestError

        with pytest.raises(ValidationRequestError):
            validate_custom_definition(definition)


class TestPasswords:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("a-sufficiently-long-password")
        assert verify_password("a-sufficiently-long-password", hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_hashes_are_salted(self):
        assert hash_password("same-password-here") != hash_password("same-password-here")

    def test_hash_is_argon2id(self):
        assert hash_password("a-sufficiently-long-password").startswith("$argon2id$")

    def test_verify_never_raises_on_a_malformed_hash(self):
        assert verify_password("anything", "not-a-hash") is False

    def test_current_hash_does_not_need_rehashing(self):
        assert needs_rehash(hash_password("a-sufficiently-long-password")) is False

    @pytest.mark.parametrize("password", ["short", "  padded-password  ", "password123"])
    def test_weak_passwords_are_rejected(self, password):
        assert validate_password_strength(password) != []

    def test_a_long_passphrase_is_accepted(self):
        assert validate_password_strength("correct horse battery staple") == []


class TestApiKeys:
    def test_generated_key_shape(self):
        generated = generate_api_key()
        assert generated.plaintext.startswith("dfk_")
        assert looks_like_api_key(generated.plaintext)
        assert generated.prefix == api_key_prefix(generated.plaintext)
        assert len(generated.hashed) == 64

    def test_plaintext_is_not_recoverable_from_the_stored_digest(self):
        generated = generate_api_key()
        assert generated.plaintext not in generated.hashed
        assert hash_api_key(generated.plaintext) == generated.hashed

    def test_keys_are_unique(self):
        assert len({generate_api_key().plaintext for _ in range(50)}) == 50

    def test_a_jwt_is_not_mistaken_for_an_api_key(self):
        assert looks_like_api_key("eyJhbGciOiJIUzI1NiJ9.abc.def") is False


class TestTokens:
    def test_refresh_token_is_rejected_where_an_access_token_is_required(self):
        """Without the `typ` check a refresh token is a 14-day access token."""
        import uuid

        from docflow.config import SecuritySettings
        from docflow.domain.errors import AuthenticationError
        from docflow.security.tokens import create_refresh_token, decode_token

        settings = SecuritySettings(jwt_secret="x" * 40)
        token, _ = create_refresh_token(
            user_id=uuid.uuid4(), organization_id=uuid.uuid4(), settings=settings
        )
        with pytest.raises(AuthenticationError):
            decode_token(token, settings=settings, expected_type="access")

    def test_a_token_signed_with_another_key_is_rejected(self):
        import uuid

        from docflow.config import SecuritySettings
        from docflow.domain.errors import AuthenticationError
        from docflow.security.tokens import create_access_token, decode_token

        attacker = SecuritySettings(jwt_secret="a" * 40)
        real = SecuritySettings(jwt_secret="b" * 40)
        token, _ = create_access_token(
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            role=OrgRole.OWNER,
            email="x@example.com",
            settings=attacker,
        )
        with pytest.raises(AuthenticationError):
            decode_token(token, settings=real, expected_type="access")

    def test_expired_token_raises_expired_not_generic(self):
        import uuid

        from docflow.config import SecuritySettings
        from docflow.domain.errors import TokenExpiredError
        from docflow.security.tokens import create_access_token, decode_token

        settings = SecuritySettings(jwt_secret="x" * 40, access_token_ttl_seconds=-10)
        token, _ = create_access_token(
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            role=OrgRole.OWNER,
            email="x@example.com",
            settings=settings,
        )
        with pytest.raises(TokenExpiredError):
            decode_token(token, settings=settings, expected_type="access")


class TestRoles:
    def test_role_hierarchy(self):
        assert OrgRole.OWNER.at_least(OrgRole.ADMIN) is True
        assert OrgRole.ADMIN.at_least(OrgRole.MEMBER) is True
        assert OrgRole.MEMBER.at_least(OrgRole.VIEWER) is True
        assert OrgRole.VIEWER.at_least(OrgRole.MEMBER) is False
        assert OrgRole.MEMBER.at_least(OrgRole.ADMIN) is False


class TestUploadValidation:
    def test_type_is_sniffed_not_trusted(self):
        """A .pdf name and a PDF content-type header do not make it a PDF."""
        import io

        from docflow.config import UploadSettings
        from docflow.documents.validation import validate_upload
        from docflow.domain.errors import UnsupportedFileTypeError

        with pytest.raises(UnsupportedFileTypeError):
            validate_upload(
                io.BytesIO(b"\x00\x01\x02\x03binary-garbage\xff\xfe"),
                filename="invoice.pdf",
                declared_content_type="application/pdf",
                settings=UploadSettings(),
            )

    def test_oversized_upload_is_rejected_during_the_read(self):
        import io

        from docflow.config import UploadSettings
        from docflow.documents.validation import validate_upload
        from docflow.domain.errors import FileTooLargeError

        with pytest.raises(FileTooLargeError):
            validate_upload(
                io.BytesIO(b"a" * 5000),
                filename="big.txt",
                declared_content_type="text/plain",
                settings=UploadSettings(max_bytes=1000),
            )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Directory components are stripped entirely, leaving the basename.
            ("../../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32", "system32"),
            ("normal invoice.pdf", "normal invoice.pdf"),
            ("", "document"),
            ("...", "document"),
        ],
    )
    def test_filenames_are_sanitised(self, raw, expected):
        from docflow.documents.validation import sanitize_filename

        assert sanitize_filename(raw) == expected

    def test_checksum_is_content_addressed(self):
        import io

        from docflow.config import UploadSettings
        from docflow.documents.validation import validate_upload

        settings = UploadSettings()
        first, _ = validate_upload(
            io.BytesIO(b"same bytes"),
            filename="a.txt",
            declared_content_type="text/plain",
            settings=settings,
        )
        second, _ = validate_upload(
            io.BytesIO(b"same bytes"),
            filename="b.txt",
            declared_content_type="text/plain",
            settings=settings,
        )
        assert first.checksum_sha256 == second.checksum_sha256


class TestWebhookSecurity:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/hook",
            "http://127.0.0.1:8000/hook",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://10.0.0.5/hook",
            "http://192.168.1.1/hook",
            "ftp://example.com/hook",
        ],
    )
    def test_ssrf_targets_are_rejected(self, url):
        from docflow.domain.errors import ValidationRequestError
        from docflow.services.webhook_service import validate_webhook_url

        with pytest.raises(ValidationRequestError):
            validate_webhook_url(url)

    def test_signature_roundtrip(self):
        from docflow.services.webhook_service import sign_payload, verify_signature

        secret, body = "whsec_test", '{"event":"document.processed"}'
        timestamp = str(__import__("time").time().__int__())
        signature = sign_payload(secret, timestamp, body)
        assert verify_signature(secret, timestamp, body, f"sha256={signature}") is True
        assert verify_signature("wrong", timestamp, body, f"sha256={signature}") is False

    def test_old_timestamps_are_rejected(self):
        from docflow.services.webhook_service import sign_payload, verify_signature

        secret, body = "whsec_test", "{}"
        old = str(int(__import__("time").time()) - 10_000)
        assert (
            verify_signature(secret, old, body, f"sha256={sign_payload(secret, old, body)}")
            is False
        )


class TestQueueJobIdTenantIsolation:
    """Regression test for a real bug found by manual browser testing.

    `queue_job_id` seeds arq's `_job_id`, and arq's job-id uniqueness is a global
    Redis key — not scoped to a tenant. The idempotency key for an unkeyed upload
    is derived purely from content (`auto:{checksum}`), so if the queue id were
    computed from that key alone, two different organizations uploading
    byte-identical content would collide on one arq job id. The second org's
    `enqueue_job` call would return `None` (arq treats it as an already-queued
    duplicate), and that organization's document would sit at `queued` forever —
    no worker job ever created for it, no error surfaced anywhere.

    This is exactly what happened when this suite was manually exercised through
    the browser: an org uploading a file already processed under a different org
    earlier in the session never left the queue. Nothing in the automated suite
    caught it, because every existing idempotency test uploads from a single
    organization.
    """

    def test_identical_content_from_different_orgs_yields_different_queue_ids(self):
        import uuid

        from docflow.services.document_service import queue_job_id

        # Same idempotency key (as content-derived keys are, across tenants),
        # different organizations.
        key = "auto:" + "a" * 32
        org_a, org_b = uuid.uuid4(), uuid.uuid4()

        assert queue_job_id(org_a, key) != queue_job_id(org_b, key)

    def test_same_org_and_key_is_deterministic(self):
        """The idempotency property this whole mechanism exists for must still hold."""
        import uuid

        from docflow.services.document_service import queue_job_id

        org = uuid.uuid4()
        key = "auto:" + "b" * 32
        assert queue_job_id(org, key) == queue_job_id(org, key)
