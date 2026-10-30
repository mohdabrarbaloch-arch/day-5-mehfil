"""Password hashing and JWT handling.

bcrypt directly rather than passlib — passlib's bcrypt backend has a known
version-detection break with bcrypt 4.x, and pinning around it is more fragile
than calling bcrypt ourselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from .config import settings

ALGORITHM = settings.algorithm


def hash_password(password: str) -> str:
    """Hash a password with bcrypt. Truncates to 72 bytes per the algorithm."""
    if not password:
        raise ValueError("Password must not be empty.")
    encoded = password.encode("utf-8")[:72]
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish verification. Returns False on any malformed input."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str | int, expires_minutes: int | None = None) -> str:
    """Issue a signed JWT for ``subject`` (the user id)."""
    minutes = expires_minutes or settings.access_token_expire_minutes
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "iss": settings.app_name,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode and verify a JWT. Returns None when invalid or expired."""
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
