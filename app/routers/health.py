"""Health and backend-status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings, warn_on_weak_config
from ..database import get_db
from ..engine import generate as gen
from ..schemas import BackendOut, HealthOut

router = APIRouter(prefix="/api", tags=["health"])

VERSION = "1.0.0"


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    """Liveness plus an honest report of what is actually configured.

    `status` is "degraded" when the database is unreachable or no model is
    connected. A health check that always says "ok" is decoration.
    """
    warnings = warn_on_weak_config()

    database = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        database = f"error: {type(exc).__name__}"

    backend = gen.describe()
    degraded = database != "ok" or backend["simulated"]

    return HealthOut(
        status="degraded" if degraded else "ok",
        version=VERSION,
        backend=BackendOut(**backend),
        database=database,
        warnings=warnings,
    )


@router.get("/backend", response_model=BackendOut)
def backend_status() -> BackendOut:
    """Which generation backend is active, and whether it is real."""
    return BackendOut(**gen.describe())


@router.get("/config")
def public_config() -> dict:
    """Non-secret configuration the UI needs.

    Deliberately narrow: no keys, no URLs that could leak internal topology
    beyond the model endpoint the user configured themselves.
    """
    return {
        "app_name": settings.app_name,
        "version": VERSION,
        "context_budget": settings.context_budget,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "backend": gen.describe(),
    }
