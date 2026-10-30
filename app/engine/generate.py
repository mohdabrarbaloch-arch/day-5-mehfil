"""Generation backends: Ollama, OpenAI-compatible, and none.

Three backends behind one interface, chosen by MEHFIL_BACKEND.

The "none" backend is the important one, and it is the reason this project
exists in the shape it does. With no model connected, Mehfil does NOT invent
a reply. It returns a clearly-labelled placeholder and sets simulated=true on
the message. Every message row stores which backend produced it, so the
history can never quietly claim a model answered when none did.

A playground that fabricates plausible text teaches you the wrong thing about
your own prompt. Better to say "no model was called" and show the prompt.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.config import settings


class BackendError(RuntimeError):
    """A backend was reachable but returned something unusable."""


@dataclass
class GenerationResult:
    """One completed generation."""

    text: str
    backend: str
    model: str
    simulated: bool = False


SIMULATED_NOTICE = (
    "[No model is connected, so this is a placeholder rather than a reply.\n\n"
    "The prompt above is real and complete — that is the part worth inspecting. "
    "To get actual replies, set MEHFIL_BACKEND to 'ollama' (with Ollama running "
    "locally) or 'openai' (with an API key), then restart."
)


def active_backend() -> str:
    """Which backend is configured, normalised."""
    name = (settings.backend or "none").strip().lower()
    if name not in {"none", "ollama", "openai"}:
        return "none"
    return name


def backend_status() -> dict:
    """What the UI shows in the header. Never claims more than is true."""
    name = active_backend()
    if name == "ollama":
        return {
            "backend": "ollama",
            "model": settings.ollama_model,
            "configured": True,
            "simulated": False,
            "note": f"Ollama at {settings.ollama_url}",
        }
    if name == "openai":
        configured = bool(settings.openai_api_key)
        return {
            "backend": "openai",
            "model": settings.openai_model,
            "configured": configured,
            "simulated": not configured,
            "note": (
                f"OpenAI-compatible endpoint at {settings.openai_base_url}"
                if configured
                else "No API key set — replies will be simulated"
            ),
        }
    return {
        "backend": "none",
        "model": "",
        "configured": False,
        "simulated": True,
        "note": "No model connected. Prompts are real; replies are placeholders.",
    }


def _simulated(messages: list[dict]) -> GenerationResult:
    """The honest placeholder.

    It echoes the last user message so the UI has something to render, and
    says plainly that no model was called.
    """
    last_user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last_user = str(message.get("content", ""))
            break

    echo = f'You said: "{last_user[:200]}"' if last_user else ""
    text = f"{SIMULATED_NOTICE}\n\n{echo}".strip()
    return GenerationResult(text=text, backend="none", model="", simulated=True)


def _ollama_payload(messages: list[dict], stream: bool) -> dict:
    return {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": stream,
        "options": {
            "temperature": settings.temperature,
            "num_predict": settings.max_tokens,
        },
    }


def _openai_payload(messages: list[dict], stream: bool) -> dict:
    return {
        "model": settings.openai_model,
        "messages": messages,
        "stream": stream,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }


async def generate(messages: list[dict]) -> GenerationResult:
    """Non-streaming generation. Used by tests and by the compare view."""
    name = active_backend()

    if name == "none":
        return _simulated(messages)

    if name == "ollama":
        url = f"{settings.ollama_url.rstrip('/')}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                response = await client.post(url, json=_ollama_payload(messages, False))
        except httpx.HTTPError as exc:
            raise BackendError(f"Could not reach Ollama at {settings.ollama_url}: {exc}") from exc

        if response.status_code != 200:
            raise BackendError(f"Ollama returned HTTP {response.status_code}: {response.text[:300]}")

        try:
            data = response.json()
            text = data["message"]["content"]
        except (KeyError, ValueError) as exc:
            raise BackendError("Ollama returned an unexpected response shape") from exc

        return GenerationResult(text=text, backend="ollama", model=settings.ollama_model)

    # openai-compatible
    if not settings.openai_api_key:
        return _simulated(messages)

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(
                url, json=_openai_payload(messages, False), headers=headers
            )
    except httpx.HTTPError as exc:
        raise BackendError(f"Could not reach the endpoint at {settings.openai_base_url}: {exc}") from exc

    if response.status_code != 200:
        raise BackendError(f"Endpoint returned HTTP {response.status_code}: {response.text[:300]}")

    try:
        data = response.json()
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise BackendError("Endpoint returned an unexpected response shape") from exc

    return GenerationResult(text=text, backend="openai", model=settings.openai_model)


async def stream(messages: list[dict]) -> AsyncIterator[str]:
    """Yield text chunks as they arrive.

    Both real backends stream; the simulated one yields its whole notice in
    one chunk so the client code path is identical.
    """
    name = active_backend()

    if name == "none":
        yield _simulated(messages).text
        return

    if name == "ollama":
        url = f"{settings.ollama_url.rstrip('/')}/api/chat"
        try:
            async with (
                httpx.AsyncClient(timeout=settings.request_timeout) as client,
                client.stream("POST", url, json=_ollama_payload(messages, True)) as response,
            ):
                if response.status_code != 200:
                    body = await response.aread()
                    raise BackendError(
                        f"Ollama returned HTTP {response.status_code}: "
                        f"{body.decode('utf-8', 'replace')[:300]}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise BackendError(f"Could not reach Ollama at {settings.ollama_url}: {exc}") from exc
        return

    # openai-compatible
    if not settings.openai_api_key:
        yield _simulated(messages).text
        return

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    try:
        async with (
            httpx.AsyncClient(timeout=settings.request_timeout) as client,
            client.stream(
                "POST", url, json=_openai_payload(messages, True), headers=headers
            ) as response,
        ):
            if response.status_code != 200:
                body = await response.aread()
                raise BackendError(
                    f"Endpoint returned HTTP {response.status_code}: "
                    f"{body.decode('utf-8', 'replace')[:300]}"
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                piece = choices[0].get("delta", {}).get("content", "")
                if piece:
                    yield piece
    except httpx.HTTPError as exc:
        raise BackendError(
            f"Could not reach the endpoint at {settings.openai_base_url}: {exc}"
        ) from exc
