# Mehfil — Architecture

> **Mehfil** (محفل) — Urdu for *a gathering*. A place where characters hold a conversation.

## The problem this actually solves

Every "build your own AI character" tool fails in the same three ways, and none of
them throw an error:

1. **The persona drifts.** You write a character, chat for twenty turns, and it
   slowly turns into a generic assistant. You *feel* it but you cannot point at it.
2. **The prompt is a black box.** Something gets assembled from your description,
   your examples, your lorebook and the chat history — and you have no idea what
   the model actually received, or in what order.
3. **The context silently truncates.** You hit the window, the oldest messages
   vanish, and the character forgets the thing that made it interesting.

Mehfil is built around making all three **visible and measurable**.

## The core idea: a persona is a contract, not a paragraph

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Character Card V2 (import / export, spec-compatible)            │
  │  name · description · personality · scenario · first_mes         │
  │  mes_example · system_prompt · post_history_instructions         │
  │  alternate_greetings · character_book (lorebook) · tags          │
  └──────────────────────────────┬───────────────────────────────────┘
                                 │  compile()
                                 ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  PROMPT BUNDLE  — fixed block order, every block inspectable     │
  │                                                                  │
  │   1. system        (identity + rules)                            │
  │   2. persona       (description + personality)                   │
  │   3. scenario      (situational frame)                           │
  │   4. lore          (keyword-triggered world facts)               │
  │   5. examples      (mes_example, few-shot)                       │
  │   6. history       (evicted oldest-first under budget)           │
  │   7. post_history  (jailbreak-style reinforcement)               │
  │   8. user          (the actual turn)                             │
  │                                                                  │
  │  + fingerprint (SHA-256 of the rendered prompt)                  │
  │  + FitReport   (what fit, what was dropped, and why)             │
  └──────────────────────────────┬───────────────────────────────────┘
                                 │  generate()
                                 ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  BACKEND  (one interface, three implementations)                 │
  │   · ollama        — local, no key, no cost                       │
  │   · openai        — any OpenAI-compatible endpoint               │
  │   · none          — NO MODEL CALLED. Returns a simulated reply   │
  │                     and labels it `simulated: true`              │
  └──────────────────────────────┬───────────────────────────────────┘
                                 │
                                 ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  FIDELITY SCORER                                                 │
  │  Scores the reply against the SAME style card extracted from     │
  │  the character's own example dialogue.                           │
  │  → on_voice / drifting / off_voice  + the features that moved    │
  └──────────────────────────────────────────────────────────────────┘
```

## Why the fidelity scorer is the point

"Does this sound like my character?" is not a vibe, it is a **distance**.

Mehfil extracts a **style card** from the character's `mes_example` — the only
place a card author actually *demonstrates* the voice rather than describing it:

| Feature | What it measures |
|---|---|
| `avg_sentence_len` | words per sentence |
| `question_rate` | fraction of sentences ending in `?` |
| `exclaim_rate` | fraction ending in `!` |
| `ellipsis_rate` | `...` per sentence |
| `emoji_rate` | emoji per 100 chars |
| `contraction_rate` | `don't`/`can't`/`I'm` per 100 words |
| `first_person_rate` | `I`/`me`/`my` per 100 words |
| `second_person_rate` | `you`/`your` per 100 words |
| `avg_word_len` | vocabulary weight |
| `signature_words` | words the character over-uses vs. English baseline |
| `caps_ratio` | SHOUTING |
| `comma_rate` | clause density |

Each feature is normalised against a tolerance band, and the reply gets a
**0–100 score** plus a label. Crucially it also returns **which features moved
and by how much** — so "drifting" comes with a reason, not just a number.

This is deliberately **not** an LLM judge. An LLM judge would be another model
with its own drift, and it would cost money per message. Counting is cheap,
deterministic, and reproducible — the same reply always scores the same.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI 0.115 | async, typed, OpenAPI for free |
| ORM | SQLAlchemy 2.0 (typed `Mapped[]`) | Postgres in prod, SQLite fallback |
| Validation | Pydantic v2 | request/response schemas, strict |
| Auth | JWT (HS256) + bcrypt (12 rounds) | stateless, no session store |
| DB | PostgreSQL 16 / SQLite | compose for prod, `/tmp` SQLite on serverless |
| Frontend | vanilla JS SPA, no build step | ships as static, zero toolchain |
| Tests | pytest | 100+ tests on engine + API |
| Lint | ruff + black | enforced in CI |

**No build step on the frontend is a deliberate choice.** A character platform's
frontend is a form, a list and a chat pane. Adding Vite + React + a bundler buys
nothing here and costs a build stage in every deploy.

## Data model

```
User ──1:N── Character ──1:N── LoreEntry
  │              │
  │              └──1:N── Conversation ──1:N── Message
  └──1:N──────────────────┘
```

- `Character` stores the card fields flat (not as a JSON blob) so they are
  queryable and indexable, plus `card_version` for import provenance.
- `LoreEntry` is a separate table because lorebooks are edited entry-by-entry
  and need per-entry enable/priority.
- `Message` stores `backend`, `simulated`, `fidelity_score`, `fidelity_label`
  and `prompt_fingerprint` — **every message carries the provenance of how it
  was produced.** History can never lie about whether a model was called.

## Request flow — sending a message

```
POST /api/conversations/{id}/messages
  │
  ├─ auth: decode JWT → user
  ├─ ownership: conversation.user_id == user.id  (else 404, not 403)
  ├─ rate limit: 30 messages / minute / user
  │
  ├─ load character + enabled lore entries
  ├─ lorebook.scan(user_text)      → matched entries, budget-capped
  ├─ compiler.build(...)           → PromptBundle + FitReport + fingerprint
  ├─ generate(bundle)              → reply, backend, simulated
  ├─ fidelity.score(reply, card)   → score, label, findings
  ├─ persist user msg + assistant msg (with provenance)
  └─ 201 { reply, fidelity, fit_report, fingerprint }
```

Streaming variant: `POST /api/conversations/{id}/stream` returns
`text/event-stream` with `token` events, then a final `meta` event carrying the
same fidelity + fit payload. **The meta event is what makes streaming honest** —
you get the drift score even when the text arrived incrementally.

## Security posture

| Concern | Mitigation |
|---|---|
| Password storage | bcrypt, 12 rounds, never logged |
| Token theft | HS256, 24h expiry, `SECRET_KEY` from env only |
| Enumeration | login returns the same error for bad email and bad password |
| Cross-tenant reads | every query filters by `owner_id`; foreign resources 404 |
| Abuse | slowapi rate limits: 10/min auth, 30/min messages |
| Injection into prompts | user text is never concatenated into the system block |
| XSS | frontend renders via `textContent`, never `innerHTML` for model output |
| Secrets | `.env` only; `.env.example` documents every key; no defaults for `SECRET_KEY` in prod |
| CORS | explicit allowlist from `CORS_ORIGINS`, not `*` |

## Scaling notes

**Now (single box):** uvicorn + SQLite. Fine to a few hundred users.

**Next (compose):** Postgres 16, connection pool 10/20, uvicorn behind nginx.
The only change is `DATABASE_URL`.

**Then:**
- **Reads dominate.** Character lists and public galleries are the hot path →
  cache `GET /api/characters?public=true` in Redis with a 60s TTL, invalidate on
  write. Chat is write-heavy but per-conversation, so it shards cleanly by
  `conversation_id`.
- **Generation is the bottleneck, not the DB.** It is I/O-bound on the model
  backend. Run it in a worker pool with a per-user concurrency cap of 1 so one
  user cannot occupy every slot.
- **Prompt compilation is CPU-cheap and pure** — it can be memoised on
  `(character_id, character.updated_at, lore_hash, history_len)`. In practice
  the fingerprint already gives you the cache key.
- **Fidelity scoring is O(n) in reply length** and runs in ~1ms. No need to
  offload it.
- **Serverless caveat:** on Vercel, SQLite lives in `/tmp` and is wiped between
  cold starts. Set `DATABASE_URL` to Postgres for anything persistent. This is
  documented in the README rather than hidden.

## What this deliberately does not do

- **No model training.** "Train a character" in this space means "write a good
  card". Pretending otherwise would be dishonest.
- **No LLM-as-judge fidelity.** Deterministic counting beats a second model's
  opinion, and it is free.
- **No fake replies.** With no backend configured, Mehfil returns a clearly
  labelled simulated response and records `simulated: true`. It never invents
  text and presents it as a model output.
