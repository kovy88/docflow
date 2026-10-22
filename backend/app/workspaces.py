"""Workspaces: one per customer firm in the web app, each with its own access code,
documents, extractions and templates. One deployment serves several pilots at once."""

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.db import Workspace, WorkspaceTemplate, default_workspace
from app.runtime import SessionDep
from app.templates import Template


def hash_code(code: str) -> str:
    # Codes are random (create_workspace), so a fast hash is enough, as for API keys.
    return hashlib.sha256(code.encode()).hexdigest()


def create_workspace(
    session: Session, name: str, daily_quota: int | None = None
) -> tuple[Workspace, str]:
    """New workspace; the plain access code is returned only here and never stored."""
    code = "dfw_" + secrets.token_urlsafe(18)
    workspace = Workspace(name=name, code_hash=hash_code(code), daily_quota=daily_quota)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace, code


def current_workspace(
    session: SessionDep, x_access_code: Annotated[str | None, Header()] = None
) -> Workspace:
    """Everything that touches documents or calls the model runs in the workspace of the
    access code (header X-Access-Code). ACCESS_CODE opens the default workspace; without
    ACCESS_CODE configured (local development) requests without a code use it too."""
    code = (x_access_code or "").strip()
    if code:
        workspace = session.exec(
            select(Workspace).where(Workspace.code_hash == hash_code(code))
        ).first()
        if workspace is not None:
            if workspace.revoked_at is not None:
                raise HTTPException(401, "Přístup do tohoto pracovního prostoru byl ukončen.")
            return workspace
    if settings.access_code and not secrets.compare_digest(
        code.encode(), settings.access_code.encode()
    ):
        raise HTTPException(401, "Neplatný přístupový kód")
    return default_workspace(session)


WorkspaceDep = Annotated[Workspace, Depends(current_workspace)]


def custom_templates(session: Session, workspace_id: int) -> dict[str, Template]:
    """Templates the firm built in the editor, for the web app and its API keys."""
    query = (
        select(WorkspaceTemplate)
        .where(WorkspaceTemplate.workspace_id == workspace_id)
        .order_by(WorkspaceTemplate.template_id)
    )
    return {row.template_id: Template.model_validate(row.data) for row in session.exec(query)}
