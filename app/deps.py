"""Shared FastAPI dependencies: current user, ownership checks, rate limiting.

Two decisions worth stating:

1. **Ownership failures return 404, not 403.** A 403 confirms the row exists,
   which lets someone enumerate ids and learn how many characters exist.
   404 says nothing at all.
2. **The rate limiter is in-process.** That is correct for one instance and
   wrong for a fleet -- each worker would keep its own counter. The docstring
   says so rather than pretending otherwise; swap in Redis before scaling out.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Character, Chat, User
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


# --- Authentication ---------------------------------------------------------


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to a user, or 401."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    subject = decode_access_token(credentials.credentials)
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(subject)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        ) from None

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
        )
    return user


# --- Ownership --------------------------------------------------------------


def get_owned_character(character_id: int, user: User, db: Session) -> Character:
    """Fetch a character the user owns, or 404."""
    character = db.get(Character, character_id)
    if character is None or character.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")
    return character


def get_visible_character(character_id: int, user: User, db: Session) -> Character:
    """Fetch a character the user may read: their own, or a public one."""
    character = db.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")
    if character.owner_id != user.id and not character.is_public:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")
    return character


def get_owned_chat(chat_id: int, user: User, db: Session) -> Chat:
    """Fetch a chat the user owns, or 404."""
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


# --- Rate limiting ----------------------------------------------------------


class SlidingWindowLimiter:
    """A per-key sliding window, held in memory.

    Correct for a single process. For more than one worker, back this with
    Redis (INCR + EXPIRE, or a sorted set) so the limit is shared.
    """

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        bucket = self._hits[key]

        while bucket and now - bucket[0] > self.window:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry_after = int(self.window - (now - bucket[0])) + 1
            return False, max(1, retry_after)

        bucket.append(now)
        return True, 0

    def reset(self) -> None:
        self._hits.clear()


general_limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)
auth_limiter = SlidingWindowLimiter(settings.auth_rate_limit_per_minute)


def _client_key(request: Request) -> str:
    """Identify the caller.

    X-Forwarded-For is only trusted because the app is expected to sit behind
    a proxy that sets it. Behind no proxy, request.client is the real peer.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    """General per-IP limit, applied to the API as a whole."""
    allowed, retry_after = general_limiter.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Slow down.",
            headers={"Retry-After": str(retry_after)},
        )


def auth_rate_limit(request: Request) -> None:
    """Tighter limit for login and register, to blunt credential stuffing."""
    allowed, retry_after = auth_limiter.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )
