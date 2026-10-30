# ── Mehfil ───────────────────────────────────────────────────────────────────
# Single-stage on purpose: the app has no build step (vanilla JS, no bundler),
# so a multi-stage build would add complexity for no size win.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so a code change doesn't invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY pyproject.toml README.md LICENSE ./

# Run as a non-root user. The app writes SQLite to /data, which is the only
# path it needs to own.
RUN useradd --create-home --uid 10001 mehfil \
    && mkdir -p /data \
    && chown -R mehfil:mehfil /app /data

USER mehfil

ENV MEHFIL_DATABASE_URL=sqlite:////data/mehfil.db \
    MEHFIL_HOST=0.0.0.0 \
    MEHFIL_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
