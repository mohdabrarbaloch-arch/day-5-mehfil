"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile

import pytest

# Point the app at a throwaway SQLite file BEFORE importing anything that reads
# settings, so tests never touch a real database.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="mehfil-test-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB}")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LLM_BACKEND", "none")
os.environ.setdefault("BCRYPT_ROUNDS", "4")  # keep the suite fast

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    # Rate-limit buckets are process-global; clear them so one test's traffic
    # never throttles the next.
    from app import ratelimit

    ratelimit.reset()
    with TestClient(app) as test_client:
        yield test_client
    ratelimit.reset()


@pytest.fixture()
def auth(client):
    """Register a fresh user and return (headers, user_dict)."""
    import uuid

    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-battery", "display_name": "Tester"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]


@pytest.fixture()
def sample_character(client, auth):
    headers, _ = auth
    payload = {
        "name": "Zainab",
        "description": "Runs a chai stall in Karachi. Not impressed by anyone.",
        "personality": "Terse. Sarcastic. Deflects with jokes when uncomfortable.",
        "scenario": "A late-night chai stall. It is raining.",
        "first_mes": "*looks up from the kettle* You again.",
        "mes_example": (
            "{{user}}: Are you okay?\n"
            "{{char}}: Fine. Why wouldn't I be.\n"
            "{{user}}: You're crying.\n"
            "{{char}}: It's the rain. Obviously.\n"
            "{{user}}: It's not raining.\n"
            "{{char}}: Then it's the kettle. Don't ask stupid questions."
        ),
        "tags": ["sarcastic", "karachi"],
        "lore": [
            {
                "name": "The stall",
                "keys": ["stall", "chai"],
                "content": "The stall has been in her family for three generations.",
                "priority": 20,
            }
        ],
    }
    response = client.post("/api/characters", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()
