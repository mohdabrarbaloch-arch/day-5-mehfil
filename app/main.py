"""Mehfil application entrypoint.

Security posture, stated plainly:
  - CORS is an explicit allowlist from settings, never "*".
  - Rate limiting is applied to the whole API, tighter on auth.
  - Static files are mounted only if the directory exists, so a missing
    static dir cannot crash the API.
  - The 404 handler returns JSON, not HTML, so the SPA's fetch calls can
    parse it instead of choking on a page.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings, warn_on_weak_config
from app.database import init_db
from app.deps import rate_limit
from app.routers import auth, characters, chat, health

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mehfil")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables and log any weak configuration on startup."""
    init_db()
    for warning in warn_on_weak_config():
        logger.warning(warning)
    logger.info("Mehfil %s ready (env=%s)", __version__, settings.environment)
    yield


app = FastAPI(
    title="Mehfil",
    version=__version__,
    description=(
        "An AI character platform where the prompt is a first-class object: "
        "compiled in a fixed block order, fingerprinted, and inspectable."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Every API route is rate limited. Auth adds a tighter limit on top.
api_dependencies = [Depends(rate_limit)]

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(characters.router, prefix="/api", dependencies=api_dependencies)
app.include_router(chat.router, prefix="/api", dependencies=api_dependencies)


@app.exception_handler(404)
async def not_found(_request: Request, exc):  # noqa: ANN001, ANN201
    """JSON 404 for API paths, the SPA for everything else."""
    detail = getattr(exc, "detail", "Not found")
    return JSONResponse(status_code=404, content={"detail": detail})


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str) -> FileResponse:
        """Serve index.html for client-side routes.

        Anything that looks like a file is 404'd rather than served as HTML,
        so a missing asset fails loudly instead of returning a page.
        """
        candidate = STATIC_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        if "." in Path(path).name:
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(STATIC_DIR / "index.html")
