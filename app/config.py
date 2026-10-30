"""Application settings, read from the environment only.

No secret has a usable default. If the signing key is missing in production
the app refuses to start rather than quietly signing tokens with a known
string.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEHFIL_", env_file=".env", extra="ignore")

    # --- Core -------------------------------------------------------------
    app_name: str = "Mehfil"
    environment: str = "development"
    debug: bool = False

    # --- Security ---------------------------------------------------------
    # Deliberately empty. `resolved_secret_key` below mints an ephemeral one
    # in development and refuses to run in production without a real value.
    secret_key: str = ""
    access_token_expire_minutes: int = 60 * 24 * 7
    algorithm: str = "HS256"

    # --- Database ---------------------------------------------------------
    # SQLite by default so `uvicorn app.main:app` works with zero setup.
    # docker-compose overrides this with a Postgres URL.
    database_url: str = "sqlite:///./mehfil.db"

    # --- CORS -------------------------------------------------------------
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # --- Rate limiting ----------------------------------------------------
    rate_limit_per_minute: int = 60
    auth_rate_limit_per_minute: int = 10

    # --- Generation backend ----------------------------------------------
    backend: str = "none"  # none | ollama | openai
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    temperature: float = 0.85
    max_tokens: int = 512
    request_timeout: float = 120.0

    # --- Context ----------------------------------------------------------
    context_budget: int = 4096

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def resolved_secret_key(self) -> str:
        """The key actually used to sign tokens.

        In production an empty key is a hard error. In development we mint a
        random one per process, which means tokens do not survive a restart
        -- annoying, but far better than shipping a hardcoded default that
        anyone can forge tokens with.
        """
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError(
                "MEHFIL_SECRET_KEY is not set. Refusing to start in production with an "
                "ephemeral signing key. See .env.example for how to generate one."
            )
        return _EPHEMERAL_KEY


_EPHEMERAL_KEY = secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def warn_on_weak_config() -> list[str]:
    """Startup warnings the app logs. Returned so tests can assert on them."""
    warnings: list[str] = []
    if not settings.secret_key and not settings.is_production:
        warnings.append(
            "MEHFIL_SECRET_KEY is unset; using an ephemeral key. Sessions will not survive "
            "a restart. Set it in .env for local development."
        )
    if settings.backend == "openai" and not settings.openai_api_key:
        warnings.append(
            "MEHFIL_BACKEND=openai but MEHFIL_OPENAI_API_KEY is empty. "
            "Chat will return the simulated placeholder."
        )
    if settings.database_url.startswith("sqlite") and settings.is_production:
        warnings.append(
            "SQLite in production: on serverless hosts the filesystem is ephemeral, so data "
            "is lost between cold starts. Point MEHFIL_DATABASE_URL at Postgres."
        )
    if os.environ.get("VERCEL") and settings.database_url.startswith("sqlite:///./"):
        warnings.append(
            "Running on Vercel with a relative SQLite path. Use sqlite:////tmp/mehfil.db "
            "or Postgres, or writes will fail."
        )
    return warnings
