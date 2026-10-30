"""Health and backend-status endpoints."""

Health reports the active backend and whether replies are simulated, so the
UI can say "no model connected" instead of pretending otherwise.
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import settings
from app.engine.generate import backend_status
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness plus an honest statement of what will answer."""
    return HealthOut(
        status="ok",
        version=__version__,
        backend=backend_status(),
        context_budget=settings.context_budget,
    )
