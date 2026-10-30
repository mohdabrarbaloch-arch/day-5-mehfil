"""API tests — auth, characters, chat, security boundaries."""

from __future__ import annotations

# ───────────────────────────── auth ─────────────────────────────


def test_health_reports_backend(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["backend"] in {"none", "ollama", "openai"}


def test_register_returns_token(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "a-good-password"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "new@example.com"


def test_register_rejects_duplicate_email(client):
    payload = {"email": "dupe@example.com", "password": "a-good-password"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_register_rejects_short_password(client):
    response = client.post(
        "/api/auth/register", json={"email": "short@example.com", "password": "abc"}
    )
    assert response.status_code == 422


def test_register_rejects_all_digit_password(client):
    response = client.post(
        "/api/auth/register", json={"email": "digits@example.com", "password": "12345678"}
    )
    assert response.status_code == 422


def test_login_wrong_password_is_401(client, auth):
    headers, user = auth
    response = client.post(
        "/api/auth/login", json={"email": user["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_unknown_email_same_error_as_wrong_password(client, auth):
    _, user = auth
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever1"}
    )
    wrong = client.post("/api/auth/login", json={"email": user["email"], "password": "whatever1"})
    # Identical message — no account enumeration.
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_garbage_token(client):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_me_returns_current_user(client, auth):
    headers, user = auth
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == user["id"]


# ───────────────────────────── characters ─────────────────────────────


def test_create_character(client, sample_character):
    assert sample_character["name"] == "Zainab"
    assert len(sample_character["lore"]) == 1


def test_list_characters_requires_auth(client):
    assert client.get("/api/characters").status_code == 401


def test_list_characters_returns_own(client, auth, sample_character):
    headers, _ = auth
    response = client.get("/api/characters", headers=headers)
    assert response.status_code == 200
    assert any(c["name"] == "Zainab" for c in response.json())


def test_get_character(client, auth, sample_character):
    headers, _ = auth
    response = client.get(f"/api/characters/{sample_character['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == sample_character["id"]


def test_other_users_character_is_404_not_403(client, auth, sample_character):
    """A 403 would confirm the id exists. We return 404 to avoid enumeration."""
    import uuid

    other_email = f"other-{uuid.uuid4().hex[:10]}@example.com"
    other = client.post(
        "/api/auth/register",
        json={"email": other_email, "password": "correct-horse-battery"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    response = client.get(f"/api/characters/{sample_character['id']}", headers=other_headers)
    assert response.status_code == 404


def test_update_character(client, auth, sample_character):
    headers, _ = auth
    payload = {
        "name": "Zainab Updated",
        "description": "Still runs the stall.",
        "mes_example": "{{char}}: Fine.",
    }
    response = client.put(
        f"/api/characters/{sample_character['id']}", json=payload, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Zainab Updated"


def test_delete_character(client, auth, sample_character):
    headers, _ = auth
    response = client.delete(f"/api/characters/{sample_character['id']}", headers=headers)
    assert response.status_code == 204
    assert (
        client.get(f"/api/characters/{sample_character['id']}", headers=headers).status_code == 404
    )


def test_style_card_is_measured(client, auth, sample_character):
    headers, _ = auth
    response = client.get(f"/api/characters/{sample_character['id']}/style", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["sample_sentences"] > 0
    assert body["confidence"] > 0


def test_export_card_is_v2_shaped(client, auth, sample_character):
    headers, _ = auth
    response = client.get(f"/api/characters/{sample_character['id']}/export", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["spec"] == "chara_card_v2"
    assert body["data"]["name"] == "Zainab"


def test_import_card(client, auth):
    headers, _ = auth
    card = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "Imported",
            "description": "Came from a file.",
            "first_mes": "Hello.",
            "mes_example": "{{char}}: Hello there.",
        },
    }
    response = client.post("/api/characters/import", json={"card": card}, headers=headers)
    assert response.status_code == 201
    assert response.json()["character"]["name"] == "Imported"


def test_import_invalid_card_is_422(client, auth):
    headers, _ = auth
    response = client.post(
        "/api/characters/import", json={"card": {"description": "no name"}}, headers=headers
    )
    assert response.status_code == 422


def test_preview_shows_blocks_without_calling_model(client, auth, sample_character):
    headers, _ = auth
    response = client.post(
        f"/api/characters/{sample_character['id']}/preview",
        json={"content": "tell me about the stall"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    names = [b["name"] for b in body["blocks"]]
    assert names[0] == "system"
    assert names[-1] == "user"
    assert body["fingerprint"]
    # The lore entry should have triggered on "stall".
    lore_block = next(b for b in body["blocks"] if b["name"] == "lore")
    assert "three generations" in lore_block["content"]


# ───────────────────────────── chat ─────────────────────────────


def test_create_conversation_seeds_first_message(client, auth, sample_character):
    headers, _ = auth
    response = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"


def test_send_message_without_backend_is_labelled_simulated(client, auth, sample_character):
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Are you okay?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    # The critical assertion: no model was called, and it says so.
    assert body["simulated"] is True
    assert body["backend"] == "none"
    assert body["message"]["simulated"] is True
    assert (
        "not a real reply" in body["message"]["content"].lower()
        or "no model" in body["message"]["content"].lower()
        or "no backend" in body["message"]["content"].lower()
    )


def test_send_message_returns_fit_report(client, auth, sample_character):
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "hello"},
        headers=headers,
    )
    body = response.json()
    assert body["fit"]["budget"] > 0
    assert body["fit"]["used"] > 0
    assert body["fingerprint"]


def test_send_message_rejects_empty_content(client, auth, sample_character):
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": ""},
        headers=headers,
    )
    assert response.status_code == 422


def test_other_users_conversation_is_404(client, auth, sample_character):
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()

    import uuid

    other_email = f"other-{uuid.uuid4().hex[:10]}@example.com"
    other = client.post(
        "/api/auth/register",
        json={"email": other_email, "password": "correct-horse-battery"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    response = client.get(f"/api/conversations/{conversation['id']}", headers=other_headers)
    assert response.status_code == 404


def test_conversation_history_persists(client, auth, sample_character):
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()
    client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "first turn"},
        headers=headers,
    )
    detail = client.get(f"/api/conversations/{conversation['id']}", headers=headers).json()
    # first_mes + user + assistant
    assert len(detail["messages"]) == 3
    assert detail["messages"][1]["content"] == "first turn"


def test_delete_conversation(client, auth, sample_character):
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()
    assert (
        client.delete(f"/api/conversations/{conversation['id']}", headers=headers).status_code
        == 204
    )
    assert (
        client.get(f"/api/conversations/{conversation['id']}", headers=headers).status_code == 404
    )


def test_api_404_returns_json(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found."


def test_spa_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Mehfil" in response.text


def test_simulated_reply_is_not_scored(client, auth, sample_character):
    """A reply no model produced must not carry a voice-fidelity score.

    Scoring the "no backend" notice against the character's style card would
    emit a number that looks meaningful and isn't. None is the honest answer.
    """
    headers, _ = auth
    conversation = client.post(
        "/api/conversations",
        json={"character_id": sample_character["id"]},
        headers=headers,
    ).json()

    body = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Are you okay?"},
        headers=headers,
    ).json()

    assert body["simulated"] is True
    assert body["fidelity"] is None
    assert body["message"]["fidelity_score"] is None
    assert body["message"]["fidelity_label"] is None
