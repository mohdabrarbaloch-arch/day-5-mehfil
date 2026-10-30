# Setup

## Requirements

- Python 3.11+
- SQLite (bundled) or PostgreSQL 16
- Optional: Ollama, or any OpenAI-compatible endpoint

## Local, no Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Open `.env` and set at minimum:

```env
SECRET_KEY=<generate one>
```

Generate a key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then:

```bash
uvicorn app.main:app --reload
```

- App: <http://localhost:8000>
- API docs: <http://localhost:8000/api/docs>
- Health: <http://localhost:8000/api/health>

The database is created on first start. No migration step — the schema is
created from the models.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres and the app. The app waits for the database healthcheck
before booting, so the first start takes a few seconds longer.

To stop and keep data: `docker compose down`
To stop and wipe data: `docker compose down -v`

## Connecting a model

### Ollama (local, free)

```bash
ollama pull llama3.2
ollama serve
```

```env
LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

### OpenAI-compatible

Works with OpenAI, Together, Groq, vLLM, LM Studio, or anything else that
speaks the chat-completions shape.

```env
LLM_BACKEND=openai
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### None

```env
LLM_BACKEND=none
```

The app runs fully. Replies are labelled `simulated: true` and say plainly that
no model was called. The prompt inspector still works, and that's the point —
you can design and debug a character before you have a model.

## Production notes

1. **Set `SECRET_KEY`.** Without it the app generates a random key at boot and
   logs a warning — tokens then stop working after every restart. It does not
   refuse to start, because a hard failure on a missing env var is a worse
   experience than a loud warning; but in production you must set it.
2. **Use Postgres.** SQLite is fine for one process; it is not fine for
   serverless or multiple workers.
3. **Set `CORS_ORIGINS`** to your real domain. The default is localhost only.
4. **Rate limiting is in-process.** Behind more than one instance, move the
   store in `app/ratelimit.py` to Redis. The interface is one function.

## Troubleshooting

**`SECRET_KEY is not set`** — expected in dev, fatal in production. Set it.

**Replies say "No model is connected"** — `LLM_BACKEND` is `none`, or the
backend is unreachable. Check `/api/health`; it reports the active backend.

**`sqlite3.OperationalError: unable to open database file`** — the directory in
`DATABASE_URL` doesn't exist or isn't writable. On Vercel, use `/tmp` or
Postgres.

**Lore entries never fire** — check the entry has keys, or set `constant: true`.
The import validator warns about this.
