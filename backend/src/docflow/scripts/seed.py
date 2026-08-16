"""Demo data seed script — `uv run docflow-seed` (or `docker compose exec api docflow-seed`).

Creates one demo organization with a handful of documents already run through the
real pipeline, so a fresh checkout has something to look at immediately instead of
an empty dashboard. Idempotent: re-running it after the demo account already
exists just prints the login and exits.

Deliberately in-process rather than HTTP: it calls `DocumentService.upload()` and
`worker.tasks.process_document()` directly against the database, the same code
path the API and worker use, without needing either one running. `arq`/Redis are
not touched — `process_document(ctx={}, ...)` works standalone because the worker
only reads `ctx` for its own retry bookkeeping (`job_try`), not for anything the
seed script needs.
"""

from __future__ import annotations

import asyncio
import io
import random
import re
import secrets

import structlog

from docflow.config import get_settings
from docflow.db.repositories import OrganizationRepository, UserRepository
from docflow.db.session import session_scope
from docflow.domain.enums import ActorType, OrgRole, PlanTier
from docflow.eval.dataset import (
    GroundTruth,
    generate_contract,
    generate_invoice,
    generate_purchase_order,
    generate_receipt,
)
from docflow.security.passwords import hash_password
from docflow.security.tokens import AuthPrincipal
from docflow.services.document_service import DocumentService
from docflow.storage import get_storage
from docflow.worker.tasks import process_document

logger = structlog.get_logger(__name__)

DEMO_EMAIL = "demo@docflow.dev"
DEMO_PASSWORD = "DocflowDemo2026!"
DEMO_ORG_NAME = "Docflow Demo"
# Matches the free-tier default in api/routes/auth.py's registration flow — kept
# as a separate literal rather than an import to avoid a script depending on a
# route module's internals for an 8-line helper.
DEMO_QUOTA = 50

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


async def _unique_slug(orgs: OrganizationRepository, name: str) -> str:
    base = _SLUG_STRIP.sub("-", name.lower()).strip("-")[:60] or "org"
    if await orgs.get_by_slug(base) is None:
        return base
    return f"{base}-{secrets.token_hex(3)}"


def _demo_corpus() -> list[GroundTruth]:
    """One of each built-in schema plus a second invoice, deterministically seeded.

    Explicit generator calls rather than `build_corpus()`'s weighted random mix —
    the point of a demo corpus is guaranteed coverage of every document type, not
    a realistic production distribution.
    """
    rng = random.Random(20260816)  # noqa: S311 — reproducibility, not security
    return [
        generate_invoice(rng, 1),
        generate_purchase_order(rng, 1),
        generate_receipt(rng, 1),
        generate_contract(rng, 1),
        generate_invoice(rng, 2),
    ]


async def _seed() -> None:
    settings = get_settings()

    async with session_scope() as session:
        users = UserRepository(session)
        existing = await users.get_by_email(DEMO_EMAIL)
        if existing is not None:
            print(f"Demo account already exists — log in with {DEMO_EMAIL} / {DEMO_PASSWORD}")
            return

        user = await users.create(
            email=DEMO_EMAIL,
            hashed_password=hash_password(DEMO_PASSWORD),
            full_name="Demo User",
        )
        orgs = OrganizationRepository(session)
        organization = await orgs.create(
            name=DEMO_ORG_NAME,
            slug=await _unique_slug(orgs, DEMO_ORG_NAME),
            plan=PlanTier.FREE.value,
            quota=DEMO_QUOTA,
        )
        await orgs.add_member(
            organization_id=organization.id, user_id=user.id, role=OrgRole.OWNER.value
        )
        org_id, user_id = organization.id, user.id

    print(f"Created demo org {DEMO_ORG_NAME!r} and user {DEMO_EMAIL}")

    principal = AuthPrincipal(
        actor_type=ActorType.USER,
        user_id=user_id,
        organization_id=org_id,
        role=OrgRole.OWNER,
        email=DEMO_EMAIL,
    )
    storage = get_storage()
    corpus = _demo_corpus()

    job_ids: list[tuple[str, str]] = []
    async with session_scope() as session:
        service = DocumentService(session, principal=principal, storage=storage, settings=settings)
        for i, gt in enumerate(corpus, start=1):
            stream = io.BytesIO(gt.text.encode("utf-8"))
            result = await service.upload(
                stream,
                filename=f"{gt.document_type}-{i}.txt",
                declared_content_type="text/plain",
                source="seed",
            )
            job_ids.append((str(result.job.id), gt.document_type))

    print(f"Uploaded {len(job_ids)} sample documents, running them through the pipeline...")

    for job_id, doc_type in job_ids:
        outcome = await process_document({}, job_id=job_id, organization_id=str(org_id))
        print(f"  {doc_type}: {outcome.get('status')}")

    print()
    print("Demo ready. Log in at http://localhost:3000/login with:")
    print(f"  email:    {DEMO_EMAIL}")
    print(f"  password: {DEMO_PASSWORD}")


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
