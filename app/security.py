"""Password hashing, JWT issuing/verification, and API key hashing.

bcrypt directly rather than passlib: passlib 1.7.4 reads `bcrypt.__about__`,
which bcrypt 4.x removed, and the resulting warning has been mistaken for a
failure by more than one person. Calling bcrypt ourselves is four lines and
has no version coupling.

bcrypt silently truncates input at 72 bytes. We reject longer passwords
instead of truncating, because two different long passwords hashing to the
same value is a real (if unlikely) security bug.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from .config import settings

BCRYPT_MAX_BYTES = 72
MIN_PASSWORD_LENGTH = 8


class PasswordError(ValueError):
    """Raised when a password cannot be hashed safely."""


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    if not isinstance(password, str) or not password:
        raise PasswordError("password must be a non-empty string")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise PasswordError(
            f"password is {len(encoded)} bytes; bcrypt's limit is {BCRYPT_MAX_BYTES}. "
            "Use a shorter password or a passphrase under 72 bytes."
        )

    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash. Never raises on bad input."""
    if not password or not password_hash:
        return False
    try:
        encoded = password.encode("utf-8")
        if len(encoded) > BCRYPT_MAX_BYTES:
            return False
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed hash in the database must not 500 the login endpoint.
        return False


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    """Issue a signed JWT for a user id."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        "iss": "mehfil",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.resolved_secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT. Returns None for any invalid token."""
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            settings.resolved_secret_key,
            algorithms=[settings.algorithm],
            issuer="mehfil",
        )
    except jwt.PyJWTError:
        return None


def generate_api_key() -> tuple[str, str, str]:
    """Create an API key. Returns (plaintext, sha256_hash, display_prefix).

    The plaintext is returned exactly once, to be shown to the user. Only
    the hash is stored.
    """
    plaintext = "mhf_" + secrets.token_urlsafe(32)
    return plaintext, hash_api_key(plaintext), plaintext[:12]


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking length or content via timing."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
