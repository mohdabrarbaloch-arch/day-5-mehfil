"""Password hashing and JWT issuing/verification.

bcrypt is called directly rather than through passlib. passlib is
unmaintained and its bcrypt backend breaks on bcrypt 4.x with a noisy
warning; calling bcrypt.hashpw is three lines and has no such problem.

bcrypt truncates input at 72 bytes. Rather than silently losing the tail of
a long passphrase, the schema caps password length and says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.config import settings

# bcrypt's hard limit. Enforced in the schema too, so the error is friendly.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """Hash a password with bcrypt at the default cost."""
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password exceeds {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash. Never raises on bad input."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str | int) -> str:
    """Issue a signed JWT for a user id."""
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(subject), "exp": expire, "iat": datetime.now(UTC)}
    return jwt.encode(payload, settings.resolved_secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> str | None:
    """Return the subject from a valid token, or None.

    Returning None rather than raising keeps the dependency simple: every
    failure mode is the same 401 to the client.
    """
    try:
        payload = jwt.decode(
            token, settings.resolved_secret_key, algorithms=[settings.algorithm]
        )
    except JWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject is not None else None
