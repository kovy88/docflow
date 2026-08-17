"""Concurrent-reprocess race (`DocumentService.reprocess` row locking).

Two requests to reprocess the same document at nearly the same time must not
both succeed — the second must see the first's `QUEUED` status and be
refused, not silently queue a second, wasted pipeline run.

This needs two genuinely independent Postgres connections/transactions, not
the shared savepoint-nested `session` fixture used elsewhere (its commits
never leave the outer, always-rolled-back transaction, so a second, separate
connection could never see them) and not two sessions sharing one physical
connection (`SELECT ... FOR UPDATE` only blocks across actually-separate
Postgres transactions, which requires actually-separate connections).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from docflow.config import get_settings
from docflow.db.models import Document, Organization, ProcessingJob
from docflow.db.repositories import JobRepository
from docflow.domain.enums import ActorType, DocumentStatus, OrgRole
from docflow.domain.errors import ConflictError
from docflow.security.tokens import AuthPrincipal
from docflow.services.document_service import DocumentService

pytestmark = pytest.mark.integration


async def test_concurrent_reprocess_only_one_wins(engine, storage):
    """Without `get_for_update`, both plain `SELECT`s read the pre-race status
    under READ COMMITTED and both proceed — this test fails against that
    version of the code, which is what makes it a real regression test rather
    than a tautology."""
    settings = get_settings()

    setup_conn = await engine.connect()
    setup_session = async_sessionmaker(bind=setup_conn, expire_on_commit=False)()
    org = Organization(
        name="Reprocess Race Org", slug=f"race-{uuid.uuid4().hex[:8]}", plan="business"
    )
    setup_session.add(org)
    await setup_session.flush()
    document = Document(
        organization_id=org.id,
        filename="race.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum_sha256=uuid.uuid4().hex,
        storage_key="orig/race.txt",
        status=DocumentStatus.COMPLETED.value,
    )
    setup_session.add(document)
    await setup_session.flush()
    document_id = document.id
    await setup_session.commit()
    await setup_session.close()
    await setup_conn.close()

    principal = AuthPrincipal(
        actor_type=ActorType.USER,
        user_id=uuid.uuid4(),
        organization_id=org.id,
        role=OrgRole.OWNER,
        email="race@example.com",
    )

    conn_a, conn_b = await engine.connect(), await engine.connect()
    session_a = async_sessionmaker(bind=conn_a, expire_on_commit=False)()
    session_b = async_sessionmaker(bind=conn_b, expire_on_commit=False)()

    async def _attempt(session):
        service = DocumentService(session, principal=principal, storage=storage, settings=settings)
        try:
            job = await service.reprocess(document_id)
        except ConflictError:
            await session.rollback()
            return "conflict"
        else:
            await session.commit()
            return job.id

    try:
        results = await asyncio.gather(_attempt(session_a), _attempt(session_b))
    finally:
        await session_a.close()
        await session_b.close()
        await conn_a.close()
        await conn_b.close()

    outcomes = ["conflict" if r == "conflict" else "ok" for r in results]
    assert sorted(outcomes) == ["conflict", "ok"], (
        f"expected exactly one caller refused, got {results}"
    )

    check_conn = await engine.connect()
    check_session = async_sessionmaker(bind=check_conn, expire_on_commit=False)()
    try:
        count = await check_session.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.document_id == document_id)
        )
        assert count == 1, "exactly one job should have been created, not two"

        job_repo = JobRepository(check_session, org.id)
        winner_job_id = next(r for r in results if r != "conflict")
        job = await job_repo.get(winner_job_id)
        assert job is not None
        assert job.document_id == document_id
    finally:
        await check_session.close()
        await check_conn.close()
