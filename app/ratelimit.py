"""Rate limiting as a FastAPI dependency.

Why not slowapi: its decorator wraps the endpoint and FastAPI then cannot see
the original signature, so request bodies silently become "missing". A
dependency-based limiter composes cleanly with FastAPI's own validation and is
trivial to test.

This is an in-process sliding window. It is correct for a single instance and
is the right default for a self-hosted app. Behind multiple instances, swap the
store for Redis — the interface is one function.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

# rule -> (max_calls, window_seconds)
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_LOCK = threading.Lock()


def parse_rule(rule: str) -> tuple[int, int]:
    """Parse ``"30/minute"`` into ``(30, 60)``."""
    units = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }
    try:
        count_part, unit_part = rule.split("/")
        count = int(count_part)
        unit = unit_part.strip().lower().rstrip("s")
        window = units[unit]
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Invalid rate limit rule: {rule!r}") from exc
    if count <= 0:
        raise ValueError(f"Rate limit count must be positive: {rule!r}")
    return count, window


def _client_key(request: Request) -> str:
    """Identify the caller.

    Prefers the authenticated user id when present so one user behind a shared
    NAT does not throttle their colleagues; falls back to the client IP.
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        # Cheap, non-verifying identity for bucketing only. Authorisation is
        # still enforced by the auth dependency.
        return f"tok:{auth[7:][-24:]}"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


def check(rule: str, key: str) -> None:
    """Record a hit for ``key`` under ``rule``; raise 429 when over budget."""
    max_calls, window = parse_rule(rule)
    bucket_key = f"{rule}|{key}"
    now = time.monotonic()

    with _LOCK:
        bucket = _BUCKETS[bucket_key]
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= max_calls:
            retry_after = int(window - (now - bucket[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)


def rate_limit(rule: str):
    """Return a FastAPI dependency enforcing ``rule``."""

    def dependency(request: Request) -> None:
        check(rule, _client_key(request))

    return Depends(dependency)


def reset() -> None:
    """Clear all buckets. Used by tests."""
    with _LOCK:
        _BUCKETS.clear()
