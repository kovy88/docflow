"""Password hashing.

Argon2id, the current password-hashing recommendation and the winner of the
Password Hashing Competition. It is memory-hard, which is what defeats GPU and
ASIC cracking in a way that PBKDF2 and bcrypt cannot.

Parameters follow the OWASP baseline (19 MiB, 2 iterations, 1 degree of
parallelism). They are configuration-free by design: making them tunable invites
someone to tune them down to make a login endpoint feel snappier.
"""

from __future__ import annotations

import contextlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP Password Storage Cheat Sheet, Argon2id baseline.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,  # KiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256  # bounded so a huge input cannot be used to burn CPU


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time verification. Never raises on a wrong password."""
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when a stored hash used weaker parameters than the current policy.

    Called after a successful login so that raising the cost parameters silently
    upgrades every active user's hash on their next sign-in, without a migration
    and without anyone needing to reset a password.
    """
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn a verification's worth of CPU against a throwaway hash.

    Called when a login is attempted for an email that does not exist. Without it,
    "no such user" returns in microseconds while "wrong password" takes ~50 ms, and
    that timing difference is a free user-enumeration oracle for anyone with a
    stopwatch.

    The verification is *expected* to fail — that is the point — so the mismatch
    must be swallowed. Letting it propagate turned the 401 into a 500, which is a
    louder enumeration signal than the timing difference this exists to hide.
    """
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher.verify(_DUMMY_HASH, "not-the-password")


# Pre-computed once at import so the dummy path costs the same as a real verify.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def validate_password_strength(password: str) -> list[str]:
    """Return a list of problems; empty means acceptable.

    Length is the requirement that actually correlates with resistance to
    cracking. Composition rules ("must contain a symbol") push users toward
    `Password1!` and are not enforced — NIST 800-63B advises against them.
    """
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        problems.append("Password must not begin or end with whitespace")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("This password is too common")
    return problems


# A token gesture, not a breach corpus. Real deployments should check against Have
# I Been Pwned's k-anonymity range API; that is a network call and belongs in an
# integration, not in this module. Documented as a gap in docs/SECURITY.md.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "1234567890",
        "qwertyuiop",
        "letmein123",
        "welcome123",
        "admin12345",
        "iloveyou1",
        "docflow123",
        "changeme123",
    }
)
