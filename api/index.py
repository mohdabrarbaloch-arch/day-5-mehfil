"""Vercel serverless entrypoint.

Vercel's Python runtime looks for a module-level `app` (or `handler`) here.
The real application lives in `app/main.py`; this file only re-exports it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repository root importable so `app` resolves as a package.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

__all__ = ["app"]
