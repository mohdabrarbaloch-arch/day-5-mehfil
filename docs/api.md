# API Reference

Base URL: `http://localhost:8000`

All endpoints except `/api/health`, `/api/auth/register` and `/api/auth/login`
require `Authorization: Bearer <token>`.

Errors are always `{"detail": "..."}`. Validation errors are FastAPI's standard
422 shape with a `detail` array.

---

## Meta

### `GET /api/health`

No auth. Reports the active backend and any configuration warnings.

```json
{
  "status": "ok",
  "app": "Mehfil",
  "environment": "development",
  "backend": "none",
  "database": "sqlite",
  "warnings": []
}
```

`backend` is one of `none`, `ollama`, `openai`. `warnings` is where a missing
`SECRET_KEY` shows up.

---

## Auth

### `POST /api/auth/register`

```json
{ "email": "you@example.com", "password": "at-least-8-chars", "display_name": "Optional" }
```

**201** → `{ "access_token": "...", "token_type": "bearer", "expires_in": 86400, "user": {...} }`

**409** if the email exists. **422** if the password is under 8 characters.

### `POST /api/auth/login`

```json
{ "email": "you@example.com", "password": "..." }
```

**200** → same shape as register.

**401** with `"Email or password is incorrect."` — deliberately identical for
unknown email and wrong password, so the endpoint can't be used to enumerate
accounts.

### `GET /api/auth/me`

**200** → the current user.

---

## Characters

### `GET /api/characters`

Query: `include_public` (bool, default false), `limit` (1–200, default 50),
`offset` (default 0).

**200** → array of summaries:

```json
[{ "id": 1, "name": "Zainab", "description": "...", "tags": ["sarcastic"],
   "is_public": false, "created_at": "...", "updated_at": "..." }]
```

### `POST /api/characters`

```json
{
  "name": "Zainab",
  "description": "Runs a chai stall in Karachi.",
  "personality": "Terse. Sarcastic.",
  "scenario": "A rainy night at the stall.",
  "first_mes": "*looks up* You again.",
  "mes_example": "{{user}}: Are you okay?\n{{char}}: Fine.",
  "system_prompt": "",
  "post_history_instructions": "",
  "alternate_greetings": [],
  "tags": ["sarcastic", "karachi"],
  "creator": "",
  "character_version": "",
  "is_public": false,
  "lore": [
    { "name": "The stall", "keys": ["stall", "chai"],
      "content": "Three generations.", "priority": 20,
      "enabled": true, "constant": false, "case_sensitive": false }
  ]
}
```

**201** → the full character. **429** if you've hit `MAX_CHARACTERS_PER_USER`.

### `GET /api/characters/{id}`

**200** → full character. **404** if it doesn't exist **or** belongs to someone
else — a 403 would confirm the id exists.

### `PUT /api/characters/{id}`

Same body as create. **200** → updated character.

### `DELETE /api/characters/{id}`

**204** on success. Cascades to lore entries and conversations.

### `GET /api/characters/{id}/style`

The measured voice fingerprint.

```json
{
  "avg_sentence_len": 2.5,
  "question_rate": 0.167,
  "exclaim_rate": 0.0,
  "ellipsis_rate": 0.0,
  "emoji_rate": 0.0,
  "contraction_rate": 0.0,
  "first_person_rate": 6.667,
  "second_person_rate": 13.333,
  "avg_word_len": 3.87,
  "caps_ratio": 0.121,
  "comma_rate": 0.0,
  "signature_words": [],
  "sample_sentences": 6,
  "confidence": 0.3
}
```

`confidence` scales with `sample_sentences`. Below ~8 exchanges it stays low and
the fidelity score should be treated as indicative only.

### `GET /api/characters/{id}/export`

**200** → a spec-shaped Character Card V2:

```json
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": {
    "name": "...", "description": "...", "personality": "...",
    "scenario": "...", "first_mes": "...", "mes_example": "...",
    "creator_notes": "...", "system_prompt": "...",
    "post_history_instructions": "...",
    "alternate_greetings": [],
    "character_book": { "entries": [ ... ] },
    "tags": [], "creator": "", "character_version": ""
  }
}
```

### `POST /api/characters/import`

```json
{ "card": { "spec": "chara_card_v2", "spec_version": "2.0", "data": { ... } } }
```

V1 flat cards work too. **201** →

```json
{ "character": { ... }, "warnings": ["Lore entry 'X' has no keys and is not constant — it will never trigger."] }
```

**422** if the card has no name, or a name but no content.

### `POST /api/characters/{id}/preview`

Compile the prompt **without calling a model**. This is the inspector.

```json
{ "content": "tell me about the stall", "context_budget": 4096 }
```

**200** →

```json
{
  "blocks": [
    { "name": "system", "content": "...", "tokens": 12, "truncated": false },
    { "name": "persona", "content": "...", "tokens": 40, "truncated": false },
    { "name": "lore", "content": "Three generations.", "tokens": 11, "truncated": false }
  ],
  "fit": {
    "budget": 4096, "used": 115, "reserved_for_reply": 512,
    "remaining": 3469, "utilisation": 0.028,
    "history_messages_kept": 0, "history_messages_dropped": 0,
    "truncated_blocks": [], "dropped_lore": []
  },
  "fingerprint": "ed2c1b96d4327c09...",
  "rendered": "..."
}
```

Block order is always:
`system → persona → scenario → lore → examples → history → post_history → user`

---

## Conversations

### `POST /api/conversations`

```json
{ "character_id": 1, "title": "Optional" }
```

**201** → the conversation, seeded with the character's `first_mes` as the first
assistant message.

### `GET /api/conversations`

**200** → your conversations, newest first.

### `GET /api/conversations/{id}`

**200** → full history. **404** if not yours.

### `POST /api/conversations/{id}/messages`

```json
{ "content": "Are you okay?" }
```

**200** →

```json
{
  "message": {
    "id": 12, "role": "assistant", "content": "...",
    "simulated": true, "backend": "none", "model": "none",
    "fidelity_score": null, "fidelity_label": null,
    "prompt_fingerprint": "ed2c1b96d4327c09", "prompt_tokens": 115,
    "latency_ms": 0, "created_at": "..."
  },
  "simulated": true,
  "backend": "none",
  "fit": { "budget": 4096, "used": 115, "reserved_for_reply": 512,
           "remaining": 3469, "utilisation": 0.153,
           "history_messages_kept": 1, "history_messages_dropped": 0,
           "truncated_blocks": [], "dropped_lore": [] },
  "fingerprint": "ed2c1b96d4327c09",
  "fidelity": null
}
```

**`simulated: true` means no model was called.** The text says so explicitly.
This is a contract, not a fallback — see
`test_send_message_without_backend_is_labelled_simulated`.

**`fidelity` is `null` whenever `simulated` is true.** Scoring the "no backend"
notice against the character's style card would emit a number that looks
meaningful and isn't. When a real model replies, the shape is:

```json
{
  "score": 87,
  "label": "on_voice",
  "features_checked": 11,
  "findings": [
    { "feature": "avg_sentence_len", "expected": 2.5, "actual": 6.1,
      "direction": "higher", "severity": "major" }
  ]
}
```

`label` is one of `on_voice` (≥75), `drifting` (50–74), `off_voice` (<50), or
`unscored` when the card has no example dialogue to measure against.

**422** on empty content. **429** on rate limit.

### `POST /api/conversations/{id}/stream`

Server-Sent Events. Same turn, streamed. Note this is `POST`, not `GET` — the
turn text is in the body.

```
event: token
data: {"text": "Fine."}

event: token
data: {"text": " Why wouldn't I be."}

event: meta
data: {"fidelity": {...}, "fit": {...}, "fingerprint": "...",
       "backend": "ollama", "simulated": false}

event: done
data: {}
```

The `meta` event is what makes streaming honest: you still get the fidelity
score and fit report even though the text arrived incrementally. On a backend
failure you get `event: error` with `{"error": "..."}` before `meta`.

### `DELETE /api/conversations/{id}`

**204** on success.

---

## Rate limits

| Scope | Default |
|---|---|
| Auth (`register`, `login`) | 10/minute per client |
| Chat (send message) | 30/minute per client |
| Everything else | 120/minute per client |

Configurable via `RATE_LIMIT_AUTH`, `RATE_LIMIT_MESSAGES`, `RATE_LIMIT_DEFAULT`.

Exceeding a limit returns **429** with a `Retry-After` header.

The limiter is an in-process sliding window. Behind multiple instances, swap the
store in `app/ratelimit.py` for Redis — the interface is one function.

---

## Status codes used

| Code | When |
|---|---|
| 200 | Success |
| 201 | Created |
| 204 | Deleted, no body |
| 401 | Missing or invalid token |
| 403 | Account disabled |
| 404 | Not found, or not yours |
| 409 | Email already registered |
| 422 | Validation failed |
| 429 | Rate limited, or character quota reached |
