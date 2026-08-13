"""JWT access/refresh tokens and API keys.

## Why not Supabase Auth

The product is designed to *run on* Supabase (Postgres, storage) without being
*coupled to* it. Auth is the tightest coupling on offer: adopting Supabase Auth
means the user table lives in another schema, local tests need a running Supabase,
and moving off it later is a migration rather than a config change.

Self-issued JWTs are ~150 lines, testable without infrastructure, and portable.
The `AuthPrincipal` abstraction below is the seam: an adapter that validates a
Supabase (or Auth0, or Entra) token and produces the same principal slots in
without touching a single route handler. See ADR-010.

## Token design

* **Access tokens are short-lived (30 min) and stateless.** No database lookup on
  the hot path.
* **Refresh tokens are long-lived (14 days) and carry a `jti`**, so they can be
  revoked. A stateless refresh token that cannot be revoked is a 14-day window for
  a stolen credential.
* **`typ` is checked on every decode.** Without it, a refresh token is a valid
  access token — a real and frequently-shipped vulnerability.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import jwt

from docflow.config import SecuritySettings
from docflow.domain.enums import ActorType, OrgRole
from docflow.domain.errors import AuthenticationError, TokenExpiredError

TokenType = Literal["access", "refresh"]

API_KEY_PREFIX = "dfk"
API_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth scheme name, not a secret
    expires_in: int = 0


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    """The authenticated caller.

    Produced by any authentication method — JWT or API key — so authorization code
    downstream never branches on how the caller proved who they are.
    """

    actor_type: ActorType
    user_id: uuid.UUID | None
    organization_id: uuid.UUID
    role: OrgRole
    email: str | None = None
    api_key_id: uuid.UUID | None = None
    scopes: tuple[str, ...] = ()

    @property
    def actor_id(self) -> uuid.UUID | None:
        return self.user_id if self.actor_type is ActorType.USER else self.api_key_id

    @property
    def label(self) -> str:
        if self.actor_type is ActorType.USER:
            return self.email or str(self.user_id)
        return f"api_key:{self.api_key_id}"

    def can(self, required: OrgRole) -> bool:
        return self.role.at_least(required)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def create_access_token(
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role: OrgRole,
    email: str,
    settings: SecuritySettings,
) -> tuple[str, int]:
    expires_in = settings.access_token_ttl_seconds
    issued = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(organization_id),
        "role": role.value,
        "email": email,
        "typ": "access",
        "iat": int(issued.timestamp()),
        "exp": int((issued + dt.timedelta(seconds=expires_in)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def create_refresh_token(
    *, user_id: uuid.UUID, organization_id: uuid.UUID, settings: SecuritySettings
) -> tuple[str, str]:
    """Returns `(token, jti)`. Store the jti to support revocation."""
    issued = _now()
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": str(user_id),
        "org": str(organization_id),
        "typ": "refresh",
        "iat": int(issued.timestamp()),
        "exp": int((issued + dt.timedelta(seconds=settings.refresh_token_ttl_seconds)).timestamp()),
        "jti": jti,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), jti


def decode_token(
    token: str, *, settings: SecuritySettings, expected_type: TokenType
) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("This session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token") from exc

    # Without this check a refresh token authenticates API calls for its full
    # 14-day lifetime, which defeats the point of short access tokens.
    if payload.get("typ") != expected_type:
        raise AuthenticationError("Invalid token type for this operation")

    return payload


def principal_from_access_token(payload: dict[str, Any]) -> AuthPrincipal:
    try:
        return AuthPrincipal(
            actor_type=ActorType.USER,
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            role=OrgRole(payload.get("role", OrgRole.MEMBER.value)),
            email=payload.get("email"),
        )
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed authentication token") from exc


# --------------------------------------------------------------------- api keys


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    #: Shown to the user exactly once. Never stored.
    plaintext: str
    #: Non-secret lookup handle, stored and displayed (`dfk_a1b2c3d4…`).
    prefix: str
    #: SHA-256 of the plaintext. What actually goes in the database.
    hashed: str


def generate_api_key() -> GeneratedApiKey:
    """Mint an API key.

    Format: `dfk_<43 url-safe base64 chars>` — a recognisable prefix (so it can be
    spotted in a leaked log or a git push protection scan) plus 256 bits of entropy.

    Hashing is plain SHA-256, not Argon2, and that is correct: the key is already
    256 bits of uniform randomness, so there is no dictionary to attack and a slow
    KDF would only add latency to every authenticated request. Argon2 exists to
    compensate for low-entropy human passwords.
    """
    secret = secrets.token_urlsafe(API_KEY_BYTES)
    plaintext = f"{API_KEY_PREFIX}_{secret}"
    return GeneratedApiKey(
        plaintext=plaintext,
        prefix=plaintext[: len(API_KEY_PREFIX) + 1 + 8],
        hashed=hash_api_key(plaintext),
    )


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def api_key_prefix(plaintext: str) -> str:
    return plaintext[: len(API_KEY_PREFIX) + 1 + 8]


def looks_like_api_key(value: str) -> bool:
    return value.startswith(f"{API_KEY_PREFIX}_")
