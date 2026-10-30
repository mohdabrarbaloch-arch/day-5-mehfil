"""Vercel serverless entrypoint.

Vercel's Python runtime looks for a module-level `app` (or `handler`) in the
file named by `builds[].src`. This shim exists so the application package
stays at the repo root where it belongs, rather than being moved into api/.

Two deployment notes that matter:

1. The filesystem is read-only except /tmp, and /tmp is wiped between cold
   starts. SQLite therefore works but is ephemeral — accounts and chats
   disappear. Set MEHFIL_DATABASE_URL to Postgres for anything real.
2. MEHFIL_SECRET_KEY must be set in the project's environment variables. The
   app refuses to start in production with the default key.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo root importable so `app` resolves.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Default to a writable path when no database URL is configured.
os.environ.setdefault("MEHFIL_DATABASE_URL", "sqlite:////tmp/mehfil.db")

from app.main import app  # noqa: E402

# Vercel's Python runtime accepts either name.
handler = app
