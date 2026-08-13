"""Authentication routes."""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, status

from docflow.api.deps import CurrentPrincipal, SessionDep, SettingsDep
from docflow.api.schemas import (
    LoginRequest,
    OrganizationSummary,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    TokenResponse,
    UserSummary,
)
from docflow.db.repositories import OrganizationRepository, UserRepository
from docflow.domain.enums import OrgRole, PlanTier
from docflow.domain.errors import (
    ConflictError,
    InvalidCredentialsError,
    ResourceNotFoundError,
    ValidationRequestError,
)
from docflow.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from docflow.security.tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

FREE_PLAN_QUOTA = 50


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and organization",
)
async def register(
    payload: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    problems = validate_password_strength(payload.password)
    if problems:
        raise ValidationRequestError(
            "Password does not meet requirements", detail={"problems": problems}
        )

    users = UserRepository(session)
    if await users.get_by_email(payload.email):
        # This does leak that an account exists. The alternative — accepting the
        # registration and emailing "you already have an account" — needs a mail
        # pipeline that does not exist yet, and a signup form that silently
        # succeeds without creating an account is a support burden. Documented as
        # a known trade-off in docs/SECURITY.md.
        raise ConflictError("An account with this email already exists")

    user = await users.create(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )

    orgs = OrganizationRepository(session)
    organization = await orgs.create(
        name=payload.organization_name,
        slug=await _unique_slug(orgs, payload.organization_name),
        plan=PlanTier.FREE.value,
        quota=FREE_PLAN_QUOTA,
    )
    # The creator owns the organization they just created.
    await orgs.add_member(
        organization_id=organization.id, user_id=user.id, role=OrgRole.OWNER.value
    )
    await session.flush()

    logger.info(
        "auth.registered",
        user_id=str(user.id),
        organization_id=str(organization.id),
    )
    return _token_response(user, organization, OrgRole.OWNER, settings)


@router.post("/login", response_model=TokenResponse, summary="Sign in")
async def login(
    payload: LoginRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    users = UserRepository(session)
    user = await users.get_by_email(payload.email)

    if user is None:
        # Spend the same CPU as a real verification so response time does not
        # reveal whether the account exists.
        dummy_verify()
        raise InvalidCredentialsError("Incorrect email or password")

    if not verify_password(payload.password, user.hashed_password):
        raise InvalidCredentialsError("Incorrect email or password")
    if not user.is_active:
        raise InvalidCredentialsError("This account has been deactivated")

    # Transparently upgrade the hash when the cost parameters have been raised.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(payload.password)

    memberships = await users.memberships(user.id)
    if not memberships:
        raise ResourceNotFoundError("This account is not a member of any organization")

    await users.touch_login(user)
    membership = memberships[0]
    return _token_response(
        user, membership.organization, OrgRole(membership.role), settings
    )


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token")
async def refresh(
    payload: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    claims = decode_token(
        payload.refresh_token, settings=settings.security, expected_type="refresh"
    )
    import uuid

    users = UserRepository(session)
    user = await users.get(uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise InvalidCredentialsError("This session is no longer valid")

    # Re-check membership on every refresh. A user removed from an organization
    # must lose access at the next refresh at the latest, not in 14 days.
    membership = await users.membership_in(user.id, uuid.UUID(claims["org"]))
    if membership is None:
        raise InvalidCredentialsError("You no longer have access to this organization")

    return _token_response(
        user, membership.organization, OrgRole(membership.role), settings
    )


@router.get("/session", response_model=SessionResponse, summary="Current session")
async def current_session(
    session: SessionDep, principal: CurrentPrincipal
) -> SessionResponse:
    users = UserRepository(session)
    if principal.user_id is None:
        raise InvalidCredentialsError("This endpoint requires a user session, not an API key")

    user = await users.get(principal.user_id)
    if user is None:
        raise ResourceNotFoundError("User not found")

    memberships = await users.memberships(user.id)
    current = next(
        (m for m in memberships if m.organization_id == principal.organization_id), None
    )
    if current is None:
        raise ResourceNotFoundError("Organization membership not found")

    return SessionResponse(
        user=UserSummary.model_validate(user),
        organization=_org_summary(current.organization, OrgRole(current.role)),
        organizations=[_org_summary(m.organization, OrgRole(m.role)) for m in memberships],
        role=current.role,
    )


# ------------------------------------------------------------------- internals


def _token_response(user, organization, role: OrgRole, settings) -> TokenResponse:
    access, expires_in = create_access_token(
        user_id=user.id,
        organization_id=organization.id,
        role=role,
        email=user.email,
        settings=settings.security,
    )
    refresh_token, _jti = create_refresh_token(
        user_id=user.id, organization_id=organization.id, settings=settings.security
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=UserSummary.model_validate(user),
        organization=_org_summary(organization, role),
    )


def _org_summary(organization, role: OrgRole) -> OrganizationSummary:
    return OrganizationSummary(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        plan=organization.plan,
        monthly_document_quota=organization.monthly_document_quota,
        role=role.value,
    )


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


async def _unique_slug(repo: OrganizationRepository, name: str) -> str:
    import secrets

    base = _SLUG_STRIP.sub("-", name.lower()).strip("-")[:60] or "org"
    if await repo.get_by_slug(base) is None:
        return base
    # A random suffix rather than an incrementing counter: counting requires a
    # query per attempt and races under concurrent signups.
    return f"{base}-{secrets.token_hex(3)}"
