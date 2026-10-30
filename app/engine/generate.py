"""Generation backends.

One interface, three implementations:

* ``ollama``  — local, no key, no cost. The default when it is reachable.
* ``openai``  — any OpenAI-compatible endpoint (OpenAI, Groq, Together, vLLM,
                LM Studio, llama.cpp server). Configured by base URL + key.
* ``none``    — **no model is called.** Returns a clearly labelled simulated
                reply and sets ``simulated=True``.

The ``none`` backend exists because the alternative — inventing plausible text
and presenting it as a model output — teaches the user the wrong thing about
their prompt. A playground that lies is worse than a playground that says
"nothing is connected".
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from .compiler import PromptBundle

DEFAULT_TIMEOUT = 120.0


@dataclass
class Generation:
    text: str
    backend: str
    model: str
    simulated: bool
    latency_ms: int = 0
    error: str | None = None


class BackendError(RuntimeError):
    """Raised when a configured backend fails. Never swallowed silently."""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def active_backend() -> str:
    """Which backend will be used, given the current environment."""
    explicit = _env("LLM_BACKEND", "auto").lower()
    if explicit in {"ollama", "openai", "none"}:
        return explicit
    if _env("OPENAI_BASE_URL") and _env("OPENAI_API_KEY"):
        return "openai"
    if _env("OLLAMA_BASE_URL"):
        return "ollama"
    return "none"


def _simulated_reply(bundle: PromptBundle, reason: str) -> Generation:
    """A reply that is honest about not being a reply.

    It is deterministic (seeded on the prompt fingerprint) so the same prompt
    always produces the same text — which makes it useless as a fake model and
    useful as a fixture.
    """
    seed = int(bundle.fingerprint[:8], 16)

    openers = [
        "No model is connected, so this is not a real reply.",
        "Nothing is generating this — no backend is configured.",
        "This text is a placeholder, not a model output.",
    ]
    opener = openers[seed % len(openers)]

    return Generation(
        text=(
            f"{opener}\n\n"
            f"Your prompt compiled to {bundle.fit.used} tokens across "
            f"{len([b for b in bundle.blocks if not b.empty])} blocks, with "
            f"{bundle.fit.history_messages_kept} history messages kept and "
            f"{bundle.fit.history_messages_dropped} dropped.\n\n"
            f"Reason: {reason}\n\n"
            f"Connect Ollama or an OpenAI-compatible endpoint to get a real "
            f"response. The prompt inspector above shows exactly what would "
            f"have been sent."
        ),
        backend="none",
        model="none",
        simulated=True,
    )


async def _generate_ollama(bundle: PromptBundle) -> Generation:
    base = _env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = _env("OLLAMA_MODEL", "llama3.2")

    payload = {
        "model": model,
        "messages": bundle.messages,
        "stream": False,
        "options": {"temperature": float(_env("LLM_TEMPERATURE", "0.8"))},
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(f"{base}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise BackendError(f"Ollama request failed: {exc}") from exc

    text = (data.get("message") or {}).get("content", "").strip()
    if not text:
        raise BackendError("Ollama returned an empty response.")

    return Generation(text=text, backend="ollama", model=model, simulated=False)


async def _generate_openai(bundle: PromptBundle) -> Generation:
    base = _env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = _env("OPENAI_MODEL", "gpt-4o-mini")
    key = _env("OPENAI_API_KEY")

    if not key:
        raise BackendError("OPENAI_API_KEY is not set.")

    payload = {
        "model": model,
        "messages": bundle.messages,
        "temperature": float(_env("LLM_TEMPERATURE", "0.8")),
        "max_tokens": int(_env("LLM_MAX_TOKENS", "512")),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise BackendError(f"OpenAI-compatible request failed: {exc}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise BackendError("Backend returned no choices.")

    text = (choices[0].get("message") or {}).get("content", "").strip()
    if not text:
        raise BackendError("Backend returned an empty message.")

    return Generation(text=text, backend="openai", model=model, simulated=False)


async def generate(bundle: PromptBundle) -> Generation:
    """Generate a reply using whichever backend is configured."""
    backend = active_backend()

    if backend == "none":
        return _simulated_reply(
            bundle,
            "LLM_BACKEND resolved to 'none' — no Ollama URL and no "
            "OPENAI_BASE_URL/OPENAI_API_KEY pair is set.",
        )
    if backend == "ollama":
        return await _generate_ollama(bundle)
    return await _generate_openai(bundle)


async def stream(bundle: PromptBundle) -> AsyncIterator[str]:
    """Yield reply chunks.

    For the ``none`` backend we yield the simulated text in word-sized chunks so
    the streaming path is exercised honestly rather than special-cased away.
    """
    backend = active_backend()

    if backend == "none":
        result = _simulated_reply(bundle, "No backend configured.")
        for word in result.text.split(" "):
            yield word + " "
        return

    if backend == "ollama":
        base = _env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        model = _env("OLLAMA_MODEL", "llama3.2")
        payload = {
            "model": model,
            "messages": bundle.messages,
            "stream": True,
            "options": {"temperature": float(_env("LLM_TEMPERATURE", "0.8"))},
        }
        try:
            async with (
                httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client,
                client.stream("POST", f"{base}/api/chat", json=payload) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    import json

                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    piece = (chunk.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
        except httpx.HTTPError as exc:
            raise BackendError(f"Ollama stream failed: {exc}") from exc
        return

    # OpenAI-compatible streaming
    base = _env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = _env("OPENAI_MODEL", "gpt-4o-mini")
    key = _env("OPENAI_API_KEY")
    if not key:
        raise BackendError("OPENAI_API_KEY is not set.")

    payload = {
        "model": model,
        "messages": bundle.messages,
        "temperature": float(_env("LLM_TEMPERATURE", "0.8")),
        "max_tokens": int(_env("LLM_MAX_TOKENS", "512")),
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    import json

    try:
        async with (
            httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client,
            client.stream(
                "POST", f"{base}/chat/completions", json=payload, headers=headers
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except ValueError:
                    continue
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content", "")
                    if piece:
                        yield piece
    except httpx.HTTPError as exc:
        raise BackendError(f"OpenAI-compatible stream failed: {exc}") from exc


def fingerprint_of(text: str) -> str:
    """Short hash used for logging a reply without storing it."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]
