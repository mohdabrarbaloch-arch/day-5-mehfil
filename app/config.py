"""Application settings, read from the environment only.

No secret has a usable default. ``SECRET_KEY`` falls back to a random value at
import time, which means tokens do not survive a restart — a loud, safe failure
rather than a silent one with a hardcoded key.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


class Settings:
    """Runtime configuration."""

    def __init__(self) -> None:
        self.app_name = _env("APP_NAME", "Mehfil")
        self.environment = _env("ENVIRONMENT", "development").lower()
        self.debug = _env_bool("DEBUG", self.environment != "production")

        # Auth
        self.secret_key = _env("SECRET_KEY") or secrets.token_urlsafe(48)
        self.secret_key_is_ephemeral = not _env("SECRET_KEY")
        self.algorithm = "HS256"
        self.access_token_expire_minutes = _env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 1440)
        self.bcrypt_rounds = _env_int("BCRYPT_ROUNDS", 12)

        # Database — SQLite in /tmp on serverless, Postgres in compose.
        default_db = "sqlite:////tmp/mehfil.db" if _env("VERCEL") else "sqlite:///./mehfil.db"
        self.database_url = _env("DATABASE_URL", default_db)
        # Heroku-style URLs need the driver prefix rewritten.
        if self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace("postgres://", "postgresql://", 1)

        # CORS
        raw_origins = _env("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
        self.cors_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

        # Rate limits
        self.rate_limit_auth = _env("RATE_LIMIT_AUTH", "10/minute")
        self.rate_limit_messages = _env("RATE_LIMIT_MESSAGES", "30/minute")
        self.rate_limit_default = _env("RATE_LIMIT_DEFAULT", "120/minute")

        # Engine
        self.context_budget = _env_int("CONTEXT_BUDGET", 4096)
        self.max_reply_tokens = _env_int("MAX_REPLY_TOKENS", 512)
        self.lore_budget = _env_int("LORE_BUDGET", 600)

        # Limits
        self.max_characters_per_user = _env_int("MAX_CHARACTERS_PER_USER", 100)
        self.max_message_chars = _env_int("MAX_MESSAGE_CHARS", 8000)
        self.max_card_bytes = _env_int("MAX_CARD_BYTES", 512_000)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def warnings(self) -> list[str]:
        """Configuration problems worth surfacing at startup."""
        issues: list[str] = []
        if self.secret_key_is_ephemeral:
            issues.append(
                "SECRET_KEY is not set — a random key was generated. Tokens will "
                "not survive a restart. Set SECRET_KEY in production."
            )
        if self.is_production and "*" in self.cors_origins:
            issues.append("CORS_ORIGINS contains '*' in production.")
        if self.database_url.startswith("sqlite") and self.is_production:
            issues.append(
                "SQLite in production: on serverless platforms the file lives in "
                "/tmp and is wiped between cold starts. Set DATABASE_URL to Postgres."
            )
        return issues


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
