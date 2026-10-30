"""Mehfil — application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .engine import active_backend
from .routers import auth, characters, chat
from .schemas import HealthResponse

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
logger = logging.getLogger("mehfil")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    for warning in settings.warnings():
        logger.warning("CONFIG: %s", warning)
    logger.info("Mehfil up — backend=%s env=%s", active_backend(), settings.environment)
    yield


app = FastAPI(
    title="Mehfil",
    description=(
        "A character platform where the persona is a measurable contract, "
        "not a paragraph. Compile, inspect, generate, and score voice fidelity."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(characters.router)
app.include_router(chat.router)


@app.get("/api/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    db_kind = "postgresql" if settings.database_url.startswith("postgres") else "sqlite"
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        environment=settings.environment,
        backend=active_backend(),
        database=db_kind,
        warnings=settings.warnings(),
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):  # noqa: ANN001
    """JSON 404 for API paths, SPA fallback for everything else."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found."})
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"detail": "Not found."})


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")


# Vercel's Python runtime imports `app` from api/index.py.
handler = app
