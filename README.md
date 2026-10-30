# Mehfil

**A character platform where the persona is a measurable contract, not a paragraph.**

Most "create an AI character" tools give you a text box and hope. You paste in a
personality, chat for ten minutes, and slowly notice the voice drifting — but you
can't say *when* it drifted or *why*, because "sounds a bit off" isn't a number.

Mehfil treats the character as something you can inspect and measure:

- **Extract a style card** from example dialogue — 12 counted features, with a
  confidence score that tells you when you haven't given it enough to work with.
- **Compile the prompt** into named blocks with token counts, so you can see
  exactly what the model receives, in what order, and what got dropped to fit.
- **Score every reply** against the same style card — on-voice, drifting, or
  off-voice, with the specific features that moved.

The name is Urdu for *a gathering* — the kind where everyone has a role and
everyone knows whose turn it is.

---

## Why this exists

I built this after watching a character bot go from sharp to bland over about
forty messages. The prompt hadn't changed. The card hadn't changed. Something in
the middle had, and there was no way to find it.

Three things were missing, and they're the three things Mehfil is built around:

| Problem | What most tools do | What Mehfil does |
|---|---|---|
| **The prompt is a black box** | Send a string, hope | Named blocks, token counts, fingerprint, eviction report |
| **"Off-voice" is a feeling** | Nothing | 12 measured features, 11 scored per reply |
| **Lore bloats the context** | Inject everything | Keyword triggers, priority, budget, recursion bound |

---

## Features

### Character cards
- Full **Character Card V2** parse, validate and export — the format SillyTavern
  and friends use, so your characters aren't trapped here
- **V1 flat cards** import too, upgraded on the way in
- Validation that warns instead of failing: a lore entry with no keys and no
  `constant` flag will never fire, and it says so

### The prompt compiler
Block order is fixed and deliberate:

```
system → persona → scenario → lore → examples → history → post_history → user
```

`post_history_instructions` sits **after** the history on purpose. It's the last
thing the model reads before your turn, which is where reinforcement actually
lands. Putting it in the system block — as most tools do — buries it under
thousands of tokens of chat.

Every compile returns a **SHA-256 fingerprint** of the block contents. Change one
word in the card and the fingerprint changes. That's how you catch a prompt
drifting without anyone editing it.

### The lorebook
- Keyword triggers with **word-boundary matching**, so `art` doesn't fire on
  `start`
- **Any** matching key fires the entry (OR, not AND — AND almost never fires in
  practice, which is why most lorebooks feel dead)
- Priority ordering, token budget, and **bounded recursion** so an injected
  entry can trigger another without looping
- Constant entries always inject, no keys needed

### Voice fidelity
The style card counts, per character:

`avg_sentence_len` · `question_rate` · `exclaim_rate` · `ellipsis_rate` ·
`emoji_rate` · `contraction_rate` · `first_person_rate` · `second_person_rate` ·
`avg_word_len` · `caps_ratio` · `comma_rate` · `signature_words`

Then scores each reply against it and returns **on-voice / drifting / off-voice**
plus plain-English findings — *"sentences are 2.4× longer than the character's
usual"* rather than a bare number.

### Three backends, one interface
- **Ollama** — local, no key, no cost
- **OpenAI-compatible** — any endpoint that speaks the chat-completions shape
- **none** — and this is the important one

With no model connected, Mehfil **does not fake a reply**. It runs the real
analysis — token estimate, rendered template, budget check, structural findings —
then says plainly that no model was called. Every run stores `backend` and
`simulated`, so history can't quietly lie to you later.

A playground that invents plausible text teaches you the wrong thing about your
own prompt.

---

## Screenshots

| Character gallery | Chat with the inspector open |
|---|---|
| ![Gallery](https://static.teamily.ai/sites/ea0f1ffd-df51-41ac-a1bc-f060d5d4c7c2/documents/gallery/gallery.png) | ![Chat](https://static.teamily.ai/sites/ea0f1ffd-df51-41ac-a1bc-f060d5d4c7c2/documents/chat/chat.png) |

| Prompt inspector | Mobile |
|---|---|
| ![Inspector](https://static.teamily.ai/sites/ea0f1ffd-df51-41ac-a1bc-f060d5d4c7c2/documents/inspector/inspector.png) | ![Mobile](https://static.teamily.ai/sites/ea0f1ffd-df51-41ac-a1bc-f060d5d4c7c2/documents/mobile/mobile.png) |

*Captured from a real running instance — the "no backend configured" notice in
the chat shot is the honest fallback, not a mockup.*

---

## Live demo

**Not deployed.** There's no Vercel token in the environment this was built in,
so there's no live URL rather than a dead one. `vercel.json` and the serverless
entrypoint are in the repo — it's a two-minute deploy once a token exists.

⚠️ **One thing to know before you deploy to Vercel:** SQLite on Vercel is
ephemeral (`/tmp` is wiped between cold starts). Point `DATABASE_URL` at Postgres
for anything you want to keep.

---

## Quick start

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-5-mehfil.git
cd day-5-mehfil

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — at minimum set SECRET_KEY

uvicorn app.main:app --reload
```

Open <http://localhost:8000>. API docs at `/api/docs`.

### With Docker

```bash
cp .env.example .env
docker compose up --build
```

That brings up the app plus Postgres. The app waits for the database to be
healthy before starting.

### With a local model

```bash
ollama pull llama3.2
ollama serve
```

Then in `.env`:

```env
LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

Restart, and replies become real. The `simulated` flag flips to `false` and the
fidelity score starts meaning something.

---

## How it works

```
┌──────────────┐
│  Browser SPA │  vanilla JS, no build step
└──────┬───────┘
       │ JSON + SSE
┌──────▼────────────────────────────────────┐
│  FastAPI                                  │
│  ├── /api/auth        JWT + bcrypt        │
│  ├── /api/characters  CRUD, import/export │
│  └── /api/conversations  chat + stream    │
└──────┬────────────────────────────────────┘
       │
┌──────▼────────────────────────────────────┐
│  Engine (pure Python, no I/O)             │
│  tokenizer → cards → lorebook             │
│           → compiler → generate           │
│           → fidelity                      │
└──────┬────────────────────────────────────┘
       │
┌──────▼──────┐   ┌──────────────────┐
│ SQLite/Postgres │   │ Ollama / OpenAI  │
└──────────────┘   └──────────────────┘
```

The engine has no database or network dependency, which is why it's testable
without mocks. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture.

---

## Tests

```bash
pytest -q          # 66 tests
ruff check .       # clean
black --check .    # clean
```

The suite covers the engine (tokenizer, card parsing, lorebook matching and
budgeting, compiler block order and eviction, fidelity scoring) and the API
(auth, ownership boundaries, card import/export, the simulated-reply contract).

Two tests are worth calling out because they encode decisions:

- `test_send_message_without_backend_is_labelled_simulated` — asserts that with
  no backend, `simulated` is `True` and the text says so. If someone ever
  "improves" the fallback into a fake reply, this fails.
- `test_other_users_character_is_404_not_403` — a 403 confirms the id exists.
  We return 404 to avoid enumeration.

---

## API

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/auth/register` | Create an account, returns a JWT |
| `POST` | `/api/auth/login` | Log in |
| `GET` | `/api/auth/me` | Current user |
| `GET` | `/api/characters` | List your characters |
| `POST` | `/api/characters` | Create one |
| `GET` | `/api/characters/{id}` | Fetch one |
| `PUT` | `/api/characters/{id}` | Update |
| `DELETE` | `/api/characters/{id}` | Delete |
| `GET` | `/api/characters/{id}/style` | Measured style card |
| `GET` | `/api/characters/{id}/export` | Export as Card V2 |
| `POST` | `/api/characters/import` | Import a V1 or V2 card |
| `POST` | `/api/characters/{id}/preview` | Compile without calling a model |
| `POST` | `/api/conversations` | Start a chat |
| `GET` | `/api/conversations` | List chats |
| `GET` | `/api/conversations/{id}` | Full history |
| `POST` | `/api/conversations/{id}/messages` | Send a turn |
| `POST` | `/api/conversations/{id}/stream` | SSE stream |
| `DELETE` | `/api/conversations/{id}` | Delete a chat |
| `GET` | `/api/health` | Status, backend, warnings |

Full reference in [docs/api.md](docs/api.md).

---

## Security

- **bcrypt** password hashing (12 rounds by default, configurable down for tests)
- **JWT** with HS256, 24-hour expiry, `sub` claim only
- **Ownership checks** on every character and conversation — 404, not 403
- **Rate limiting** on auth and chat, in-process sliding window
- **CORS** restricted to configured origins
- **No secrets in code** — everything through the environment; a missing
  `SECRET_KEY` generates a random one at boot and logs a warning, so tokens
  never silently survive on a hardcoded default
- **XSS-safe rendering** — model output goes through `textContent`, never
  `innerHTML`

---

## Configuration

Every variable is documented in [.env.example](.env.example). The ones that
matter:

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | *(random at boot)* | **Must** be set in production |
| `DATABASE_URL` | `sqlite:///./mehfil.db` | Postgres URL works as-is |
| `LLM_BACKEND` | `auto` | `auto` / `none` / `ollama` / `openai` |
| `CONTEXT_BUDGET` | `4096` | Total tokens per prompt |
| `MAX_REPLY_TOKENS` | `512` | Reserved for the response |
| `LORE_BUDGET` | `600` | Ceiling for injected lore |

---

## License

MIT — see [LICENSE](LICENSE).

---

Built as Day 5 of a 30-day build challenge. The other days are on
[the same account](https://github.com/mohdabrarbaloch-arch).
