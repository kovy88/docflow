"""FastAPI dependencies.

This is the authentication and authorization boundary. Every protected route
depends on `CurrentPrincipal`, which is the only place a request turns into an
identity, and `require_role` is the only place a role is checked.

Concentrating it here is what makes the security model reviewable: to audit
"can a viewer delete a document?", you read one file, not forty route handlers.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import Settings, get_settings
from docflow.db.repositories import ApiKeyRepository, UserRepository
from docflow.db.session import get_sessionmaker
from docflow.domain.enums import ActorType, OrgRole
from docflow.domain.errors import AuthenticationError, AuthorizationError
from docflow.security.tokens import (
    AuthPrincipal,
    decode_token,
    hash_api_key,
    looks_like_api_key,
    principal_from_access_token,
)
from docflow.storage import get_storage
from docflow.storage.base import StorageBackend

logger = structlog.get_logger(__name__)

# `auto_error=False` so a missing header produces our own 401 envelope rather than
# FastAPI's, keeping the error contract uniform for clients.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token or API key")


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session.

    Commits on success, rolls back on any exception. Handlers do not manage
    transactions — a handler that returns normally has its work committed, and one
    that raises has it discarded, with no partial writes either way.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_storage_backend() -> StorageBackend:
    return get_storage()


StorageDep = Annotated[StorageBackend, Depends(get_storage_backend)]


async def get_principal(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    x_organization_id: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
) -> AuthPrincipal:
    """Authenticate the caller — JWT or API key — and resolve their organization."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication is required")

    token = credentials.credentials.strip()

    if looks_like_api_key(token):
        principal = await _principal_from_api_key(token, session)
    else:
        payload = decode_token(token, settings=settings.security, expected_type="access")
        principal = principal_from_access_token(payload)
        # A user may belong to several organizations. The header selects which one
        # this request acts in; membership is re-checked against the database
        # rather than trusted from the token, so a stale token cannot carry
        # access to an organization the user has since been removed from.
        if x_organization_id:
            principal = await _switch_organization(principal, x_organization_id, session)

    request.state.principal = principal
    return principal


async def _principal_from_api_key(token: str, session: AsyncSession) -> AuthPrincipal:
    repo = ApiKeyRepository(session)
    key = await repo.find_active_by_hash(hash_api_key(token))
    if key is None:
        # Deliberately identical message for absent, revoked and expired keys.
        raise AuthenticationError("Invalid or revoked API key")

    await repo.touch(key)
    return AuthPrincipal(
        actor_type=ActorType.API_KEY,
        user_id=None,
        organization_id=key.organization_id,
        # API keys act at MEMBER level: enough to upload, read and correct
        # documents, not enough to manage members or mint further keys. Privilege
        # escalation via a leaked key is bounded by design.
        role=OrgRole.MEMBER,
        api_key_id=key.id,
        scopes=tuple(key.scopes or ()),
    )


async def _switch_organization(
    principal: AuthPrincipal, organization_id: str, session: AsyncSession
) -> AuthPrincipal:
    try:
        target = uuid.UUID(organization_id)
    except ValueError:
        raise AuthorizationError("Invalid organization identifier") from None

    if target == principal.organization_id:
        return principal

    assert principal.user_id is not None
    membership = await UserRepository(session).membership_in(principal.user_id, target)
    if membership is None:
        raise AuthorizationError("You are not a member of this organization")

    return AuthPrincipal(
        actor_type=principal.actor_type,
        user_id=principal.user_id,
        organization_id=target,
        role=OrgRole(membership.role),
        email=principal.email,
    )


CurrentPrincipal = Annotated[AuthPrincipal, Depends(get_principal)]


def require_role(minimum: OrgRole):
    """Dependency factory enforcing a minimum role."""

    async def _guard(principal: CurrentPrincipal) -> AuthPrincipal:
        if not principal.can(minimum):
            raise AuthorizationError(f"This action requires the {minimum.value} role or higher")
        return principal

    return _guard


RequireAdmin = Annotated[AuthPrincipal, Depends(require_role(OrgRole.ADMIN))]
RequireMember = Annotated[AuthPrincipal, Depends(require_role(OrgRole.MEMBER))]


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


RequestId = Annotated[str, Depends(get_request_id)]
