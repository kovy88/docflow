"""API integration tests: auth, upload, review, and tenant isolation.

The isolation class is the most important in the suite. A multi-tenant SaaS that
leaks one customer's documents to another is not a product with a bug; it is a
product that is over. Every document endpoint is asserted against a cross-tenant
caller, and the assertion is deliberately `404` rather than `403`.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

REGISTRATION = {
    "email": "founder@example.com",
    "password": "a-sufficiently-long-password",
    "full_name": "Founder",
    "organization_name": "Example s.r.o.",
}


async def register(client: AsyncClient, **overrides) -> dict:
    payload = {**REGISTRATION, **overrides}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def upload(client: AsyncClient, token: str, content: bytes, name="invoice.txt") -> dict:
    response = await client.post(
        "/api/v1/documents",
        headers=auth(token),
        files={"file": (name, content, "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()


class TestAuth:
    async def test_registration_creates_an_owner(self, client):
        body = await register(client)
        assert body["organization"]["role"] == "owner"
        assert body["user"]["email"] == REGISTRATION["email"]
        assert body["access_token"]

    async def test_duplicate_email_is_rejected(self, client):
        await register(client)
        response = await client.post("/api/v1/auth/register", json=REGISTRATION)
        assert response.status_code == 409

    async def test_weak_password_is_rejected(self, client):
        response = await client.post(
            "/api/v1/auth/register", json={**REGISTRATION, "password": "short"}
        )
        assert response.status_code == 422

    async def test_login_returns_a_working_token(self, client):
        await register(client)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        assert (await client.get("/api/v1/documents", headers=auth(token))).status_code == 200

    async def test_wrong_password_is_rejected(self, client):
        await register(client)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTRATION["email"], "password": "wrong-but-long-enough"},
        )
        assert response.status_code == 401

    async def test_unknown_email_gives_the_same_error_as_a_wrong_password(self, client):
        """Identical response, so the endpoint is not a user-enumeration oracle."""
        await register(client)
        wrong_password = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTRATION["email"], "password": "wrong-but-long-enough"},
        )
        unknown_user = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-but-long-enough"},
        )
        assert wrong_password.status_code == unknown_user.status_code == 401
        assert wrong_password.json()["error"]["code"] == unknown_user.json()["error"]["code"]

    async def test_refresh_token_cannot_be_used_as_an_access_token(self, client):
        body = await register(client)
        response = await client.get("/api/v1/documents", headers=auth(body["refresh_token"]))
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "header",
        [None, "Bearer garbage", "Bearer dfk_notarealkey000000000000", "Basic abc"],
    )
    async def test_bad_credentials_are_rejected(self, client, header):
        headers = {"Authorization": header} if header else {}
        response = await client.get("/api/v1/documents", headers=headers)
        assert response.status_code in (401, 403)

    async def test_session_reports_the_caller(self, client):
        body = await register(client)
        response = await client.get("/api/v1/auth/session", headers=auth(body["access_token"]))
        assert response.status_code == 200
        assert response.json()["user"]["email"] == REGISTRATION["email"]

    async def test_refresh_issues_a_new_working_access_token(self, client):
        body = await register(client)
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
        )
        assert response.status_code == 200
        new_access = response.json()["access_token"]
        assert (await client.get("/api/v1/documents", headers=auth(new_access))).status_code == 200

    async def test_logout_revokes_the_refresh_token(self, client):
        """The gap this closes: `create_refresh_token` mints a `jti` "to support
        revocation" (its own docstring), but nothing checked it before `/auth/logout`
        existed — a leaked refresh token was valid for its full 14-day lifetime with
        no way to kill it."""
        body = await register(client)
        refresh_token = body["refresh_token"]

        logout_response = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh_token}
        )
        assert logout_response.status_code == 204

        reuse_response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert reuse_response.status_code == 401

    async def test_logout_is_idempotent(self, client):
        body = await register(client)
        refresh_token = body["refresh_token"]
        first = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
        second = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
        assert first.status_code == second.status_code == 204

    async def test_logout_with_an_already_invalid_token_does_not_error(self, client):
        response = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": "not-a-real-token"}
        )
        assert response.status_code == 204

    async def test_access_token_is_not_revoked_by_logout(self, client):
        """Documented, deliberate scope: logout revokes the refresh token so it
        can't mint further access tokens. It does not, and by design cannot
        cheaply, revoke an access token already issued — see security/tokens.py."""
        body = await register(client)
        await client.post("/api/v1/auth/logout", json={"refresh_token": body["refresh_token"]})
        response = await client.get("/api/v1/documents", headers=auth(body["access_token"]))
        assert response.status_code == 200


class TestUpload:
    async def test_upload_returns_202_immediately(self, client, sample_invoice):
        token = (await register(client))["access_token"]
        body = await upload(client, token, sample_invoice)
        assert body["status"] == "queued"
        assert body["duplicate"] is False
        assert uuid.UUID(body["document_id"])

    async def test_identical_content_is_deduplicated(self, client, sample_invoice):
        """The property that stops a retry becoming a second LLM bill."""
        token = (await register(client))["access_token"]
        first = await upload(client, token, sample_invoice)
        second = await upload(client, token, sample_invoice)

        assert second["duplicate"] is True
        assert second["document_id"] == first["document_id"]

    async def test_idempotency_key_reuses_the_job(self, client, sample_invoice):
        token = (await register(client))["access_token"]
        headers = {**auth(token), "Idempotency-Key": "same-key-twice"}
        first = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("a.txt", sample_invoice, "text/plain")},
        )
        second = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("a.txt", sample_invoice, "text/plain")},
        )
        assert first.json()["job_id"] == second.json()["job_id"]

    async def test_unsupported_file_type_is_rejected(self, client):
        token = (await register(client))["access_token"]
        response = await client.post(
            "/api/v1/documents",
            headers=auth(token),
            files={"file": ("evil.exe", b"MZ\x90\x00\x03binary", "application/pdf")},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_file_type"

    async def test_empty_file_is_rejected(self, client):
        token = (await register(client))["access_token"]
        response = await client.post(
            "/api/v1/documents",
            headers=auth(token),
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert response.status_code == 400

    async def test_upload_requires_authentication(self, client, sample_invoice):
        response = await client.post(
            "/api/v1/documents",
            files={"file": ("a.txt", sample_invoice, "text/plain")},
        )
        assert response.status_code in (401, 403)


class TestTenantIsolation:
    """Organization B must not be able to observe or affect organization A."""

    @pytest.fixture
    async def two_tenants(self, client, sample_invoice):
        a = await register(client, email="a@example.com", organization_name="Org A")
        b = await register(client, email="b@example.com", organization_name="Org B")
        document = await upload(client, a["access_token"], sample_invoice)
        return a, b, document["document_id"]

    @pytest.mark.parametrize(
        "path",
        ["", "/status", "/extraction", "/timeline", "/download"],
    )
    async def test_cross_tenant_reads_return_404(self, client, two_tenants, path):
        _a, b, document_id = two_tenants
        response = await client.get(
            f"/api/v1/documents/{document_id}{path}", headers=auth(b["access_token"])
        )
        # 404 not 403: a 403 would confirm the document exists.
        assert response.status_code == 404, f"{path} leaked existence"

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("patch", "/extraction", {"edits": [{"field_path": "total", "value": "1.00"}]}),
            ("post", "/approve", {}),
            ("post", "/reject", {"reason": "malicious"}),
            ("post", "/reprocess", None),
            ("delete", "", None),
        ],
    )
    async def test_cross_tenant_writes_return_404(self, client, two_tenants, method, path, payload):
        _a, b, document_id = two_tenants
        call = getattr(client, method)
        kwargs = {"headers": auth(b["access_token"])}
        if payload is not None:
            kwargs["json"] = payload
        response = await call(f"/api/v1/documents/{document_id}{path}", **kwargs)
        assert response.status_code == 404, f"{method} {path} was not isolated"

    async def test_document_lists_are_disjoint(self, client, two_tenants):
        a, b, _document_id = two_tenants
        listing_a = (await client.get("/api/v1/documents", headers=auth(a["access_token"]))).json()
        listing_b = (await client.get("/api/v1/documents", headers=auth(b["access_token"]))).json()
        assert listing_a["total"] == 1
        assert listing_b["total"] == 0

    async def test_forged_organization_header_is_rejected(self, client, two_tenants):
        """A valid token plus someone else's org id must not grant access."""
        a, b, _document_id = two_tenants
        response = await client.get(
            "/api/v1/documents",
            headers={
                **auth(b["access_token"]),
                "X-Organization-Id": a["organization"]["id"],
            },
        )
        assert response.status_code == 403

    async def test_dashboards_are_isolated(self, client, two_tenants):
        _a, b, _document_id = two_tenants
        dashboard = (await client.get("/api/v1/dashboard", headers=auth(b["access_token"]))).json()
        assert dashboard["total_documents"] == 0

    async def test_export_is_isolated(self, client, two_tenants):
        _a, b, _document_id = two_tenants
        response = await client.get("/api/v1/export?format=json", headers=auth(b["access_token"]))
        assert response.json() == []


class TestRoleEnforcement:
    """`principal.can(...)` role gates (documents.py, settings_routes.py).

    Untestable through `client` alone: `POST /auth/register` always makes the
    registrant OWNER (routes/auth.py), and there is no invite/add-member
    endpoint — so there was previously no way to reach these checks with a
    non-owner caller. `viewer_client` (conftest.py) exists for exactly this,
    built from fixtures (`viewer_principal`) that were added but never used
    until now.
    """

    @pytest.fixture
    async def document_in_org(self, session, organization) -> str:
        from docflow.db.repositories import DocumentRepository
        from docflow.domain.enums import DocumentStatus

        documents = DocumentRepository(session, organization.id)
        document = await documents.create(
            filename="viewer-test.txt",
            content_type="text/plain",
            size_bytes=10,
            checksum_sha256=uuid.uuid4().hex,
            storage_key="orig/viewer-test.txt",
            status=DocumentStatus.COMPLETED.value,
        )
        await session.flush()
        return str(document.id)

    async def test_viewer_cannot_upload(self, viewer_client, sample_invoice):
        response = await viewer_client.post(
            "/api/v1/documents",
            files={"file": ("invoice.txt", sample_invoice, "text/plain")},
        )
        assert response.status_code == 403

    async def test_viewer_cannot_reprocess(self, viewer_client, document_in_org):
        response = await viewer_client.post(f"/api/v1/documents/{document_in_org}/reprocess")
        assert response.status_code == 403

    async def test_viewer_cannot_delete(self, viewer_client, document_in_org):
        response = await viewer_client.delete(f"/api/v1/documents/{document_in_org}")
        assert response.status_code == 403

    async def test_viewer_can_still_read(self, viewer_client, document_in_org):
        """The gate is on writes, not on the role existing at all."""
        response = await viewer_client.get(f"/api/v1/documents/{document_in_org}")
        assert response.status_code == 200

    async def test_viewer_cannot_create_api_key(self, viewer_client):
        response = await viewer_client.post(
            "/api/v1/settings/api-keys", json={"name": "should-fail"}
        )
        assert response.status_code == 403

    async def test_viewer_cannot_register_webhook(self, viewer_client):
        response = await viewer_client.post(
            "/api/v1/settings/webhooks",
            json={"url": "https://example.com/hook", "events": ["document.processed"]},
        )
        assert response.status_code == 403


class TestPreviouslyUncoveredEndpoints:
    """Endpoints the coverage audit found with zero tests of any kind."""

    async def test_review_queue_starts_empty(self, client):
        token = (await register(client))["access_token"]
        response = await client.get("/api/v1/reviews/queue", headers=auth(token))
        assert response.status_code == 200
        assert response.json() == []

    async def test_usage_summary_starts_at_zero(self, client):
        token = (await register(client))["access_token"]
        response = await client.get("/api/v1/usage", headers=auth(token))
        assert response.status_code == 200
        body = response.json()
        assert body["documents"] == 0
        assert body["quota"] == 50
        assert body["plan"] == "free"

    async def test_corrections_analytics_starts_empty(self, client):
        token = (await register(client))["access_token"]
        response = await client.get("/api/v1/analytics/corrections", headers=auth(token))
        assert response.status_code == 200
        assert response.json() == []

    async def test_readiness_reports_ready_when_dependencies_are_up(self, client):
        response = await client.get("/readiness")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"] == {
            "database": True,
            "redis": True,
            "storage": True,
            "llm_provider": True,
        }

    async def test_session_refresh_and_logout_use_the_current_session(self, client):
        """auth/refresh, auth/session and auth/logout are also covered directly
        in TestAuth; this is the one place they're exercised back-to-back as a
        session lifecycle rather than each in isolation."""
        body = await register(client)
        session_resp = await client.get("/api/v1/auth/session", headers=auth(body["access_token"]))
        assert session_resp.status_code == 200

        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
        )
        assert refreshed.status_code == 200

        logged_out = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": refreshed.json()["refresh_token"]}
        )
        assert logged_out.status_code == 204


class TestErrorContract:
    async def test_errors_use_the_documented_envelope(self, client):
        token = (await register(client))["access_token"]
        response = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=auth(token))
        assert response.status_code == 404
        body = response.json()
        assert set(body) == {"error", "request_id"}
        assert set(body["error"]) == {"code", "category", "message", "detail"}
        assert body["error"]["code"] == "not_found"

    async def test_every_response_carries_a_request_id(self, client):
        response = await client.get("/health")
        assert response.headers.get("X-Request-Id")

    async def test_an_inbound_request_id_is_preserved(self, client):
        response = await client.get("/health", headers={"X-Request-Id": "trace-me-123"})
        assert response.headers["X-Request-Id"] == "trace-me-123"

    async def test_security_headers_are_set(self, client):
        headers = (await client.get("/health")).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in headers

    async def test_malformed_uuid_is_a_422_not_a_500(self, client):
        token = (await register(client))["access_token"]
        response = await client.get("/api/v1/documents/not-a-uuid", headers=auth(token))
        assert response.status_code == 422


class TestSystemEndpoints:
    async def test_health_is_liveness_only(self, client):
        body = (await client.get("/health")).json()
        assert body["status"] == "ok"

    async def test_openapi_is_generated(self, client):
        spec = (await client.get("/openapi.json")).json()
        assert "/api/v1/documents" in spec["paths"]
        assert spec["info"]["title"] == "Docflow API"

    async def test_document_types_are_listed(self, client):
        token = (await register(client))["access_token"]
        types = (await client.get("/api/v1/document-types", headers=auth(token))).json()
        keys = {t["key"] for t in types}
        assert {"invoice", "contract", "purchase_order", "receipt"} <= keys
        invoice = next(t for t in types if t["key"] == "invoice")
        assert invoice["required_fields"]
        assert invoice["critical_fields"]


class TestLocalStorageDownload:
    """`GET /api/v1/storage/{key}` — what `LocalStorage.presigned_url()` points at.

    This route intentionally does not use the normal bearer-auth dependency:
    the URL it serves is handed to a plain browser navigation (`window.open`,
    see documents/[id]/page.tsx), which carries no Authorization header. Its
    own HMAC signature *is* the auth check — these tests are exactly the
    "possession of a key is never sufficient" property from storage/base.py.
    """

    async def test_valid_signed_url_downloads_the_object(self, client, storage):
        await storage.put("org/x/original.txt", b"hello world", content_type="text/plain")
        url = await storage.presigned_url("org/x/original.txt", filename="invoice.txt")

        response = await client.get(url)

        assert response.status_code == 200
        assert response.content == b"hello world"
        assert 'filename="invoice.txt"' in response.headers["content-disposition"]

    async def test_tampered_signature_is_rejected(self, client, storage):
        await storage.put("org/x/original.txt", b"hello world", content_type="text/plain")
        url = await storage.presigned_url("org/x/original.txt")

        tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
        response = await client.get(tampered)

        assert response.status_code == 401

    async def test_expired_url_is_rejected(self, client, storage):
        await storage.put("org/x/original.txt", b"hello world", content_type="text/plain")
        url = await storage.presigned_url("org/x/original.txt", expires_in=-1)

        response = await client.get(url)

        assert response.status_code == 401

    async def test_signed_url_for_one_key_does_not_grant_another(self, client, storage):
        await storage.put("org/x/a.txt", b"file a", content_type="text/plain")
        await storage.put("org/x/b.txt", b"file b", content_type="text/plain")
        url_for_a = await storage.presigned_url("org/x/a.txt")

        redirected = url_for_a.replace("a.txt", "b.txt")
        response = await client.get(redirected)

        assert response.status_code == 401


class TestApiKeys:
    async def test_key_is_returned_once_and_then_works(self, client, sample_invoice):
        token = (await register(client))["access_token"]
        created = await client.post(
            "/api/v1/settings/api-keys", headers=auth(token), json={"name": "CI"}
        )
        assert created.status_code == 201
        api_key = created.json()["api_key"]
        assert api_key.startswith("dfk_")

        # The key authenticates.
        listing = await client.get("/api/v1/documents", headers=auth(api_key))
        assert listing.status_code == 200

        # And it is never returned again.
        keys = (await client.get("/api/v1/settings/api-keys", headers=auth(token))).json()
        assert "api_key" not in keys[0]

    async def test_revoked_key_stops_working(self, client):
        token = (await register(client))["access_token"]
        created = (
            await client.post(
                "/api/v1/settings/api-keys", headers=auth(token), json={"name": "temp"}
            )
        ).json()

        assert (
            await client.get("/api/v1/documents", headers=auth(created["api_key"]))
        ).status_code == 200
        await client.delete(f"/api/v1/settings/api-keys/{created['id']}", headers=auth(token))
        assert (
            await client.get("/api/v1/documents", headers=auth(created["api_key"]))
        ).status_code == 401

    async def test_cross_tenant_cannot_revoke_another_orgs_key(self, client):
        """docs/SECURITY.md claims isolation is tested for documents, extractions,
        *and settings* — this is the settings half, previously missing (every
        prior api-keys test used one org for both creation and deletion)."""
        a = await register(client, email="a@example.com", organization_name="Org A")
        b = await register(client, email="b@example.com", organization_name="Org B")
        created = (
            await client.post(
                "/api/v1/settings/api-keys", headers=auth(a["access_token"]), json={"name": "a-key"}
            )
        ).json()

        response = await client.delete(
            f"/api/v1/settings/api-keys/{created['id']}", headers=auth(b["access_token"])
        )
        assert response.status_code == 404

        # And it still works — the cross-tenant delete did not revoke it.
        assert (
            await client.get("/api/v1/documents", headers=auth(created["api_key"]))
        ).status_code == 200


class TestWebhookRegistration:
    async def test_ssrf_url_is_rejected(self, client):
        token = (await register(client))["access_token"]
        response = await client.post(
            "/api/v1/settings/webhooks",
            headers=auth(token),
            json={"url": "http://169.254.169.254/latest/meta-data/", "events": []},
        )
        assert response.status_code == 400

    async def test_public_url_is_accepted_and_secret_shown_once(self, client):
        token = (await register(client))["access_token"]
        response = await client.post(
            "/api/v1/settings/webhooks",
            headers=auth(token),
            json={"url": "https://example.com/hook", "events": ["document.processed"]},
        )
        assert response.status_code == 201
        assert response.json()["secret"].startswith("whsec_")

        listing = (await client.get("/api/v1/settings/webhooks", headers=auth(token))).json()
        assert "secret" not in listing[0]

    async def test_cross_tenant_cannot_delete_another_orgs_webhook(self, client):
        """Previously untested in any form — not just cross-tenant: this route
        was never called by any test at all."""
        a = await register(client, email="a@example.com", organization_name="Org A")
        b = await register(client, email="b@example.com", organization_name="Org B")
        created = (
            await client.post(
                "/api/v1/settings/webhooks",
                headers=auth(a["access_token"]),
                json={"url": "https://example.com/hook", "events": ["document.processed"]},
            )
        ).json()

        response = await client.delete(
            f"/api/v1/settings/webhooks/{created['id']}", headers=auth(b["access_token"])
        )
        assert response.status_code == 404

        # Still there — owner's own listing is unaffected by the cross-tenant attempt.
        listing = (
            await client.get("/api/v1/settings/webhooks", headers=auth(a["access_token"]))
        ).json()
        assert len(listing) == 1

    async def test_owner_can_delete_their_own_webhook(self, client):
        token = (await register(client))["access_token"]
        created = (
            await client.post(
                "/api/v1/settings/webhooks",
                headers=auth(token),
                json={"url": "https://example.com/hook", "events": ["document.processed"]},
            )
        ).json()

        response = await client.delete(
            f"/api/v1/settings/webhooks/{created['id']}", headers=auth(token)
        )
        assert response.status_code == 204

        listing = (await client.get("/api/v1/settings/webhooks", headers=auth(token))).json()
        assert listing == []
