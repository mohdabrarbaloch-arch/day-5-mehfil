"""Chat: create chats, send messages, stream replies, inspect the prompt.

The prompt preview endpoint is the feature this project exists for. It
compiles the prompt for a hypothetical next message and returns every block
with its token count, the lore entries that fired and which keyword fired
them, the fit report, and the fingerprint -- without calling a model.

Streaming is SSE. The assistant message is written after the stream
completes, with backend and simulated recorded, so a disconnect mid-stream
does not leave a half-message in history.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.deps import get_current_user, get_owned_chat, get_visible_character
from app.engine.cards import CharacterCard
from app.engine.compiler import compile_prompt
from app.engine.context import fit_prompt
from app.engine.generate import BackendError, active_backend, stream
from app.engine.lorebook import parse_entries, scan
from app.models import Chat, Character, Message, User
from app.schemas import (
    ChatCreate,
    ChatDetail,
    ChatOut,
    MessageOut,
    PromptPreviewOut,
    PromptPreviewRequest,
    SendMessageRequest,
)

router = APIRouter(prefix="/chats", tags=["chats"])

# How many past messages we consider before fitting. The fitter trims further.
HISTORY_WINDOW = 40


def _card_from(character: Character) -> CharacterCard:
    book = None
    if character.character_book_json:
        try:
            parsed = json.loads(character.character_book_json)
            book = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            book = None
    return CharacterCard(
        name=character.name,
        description=character.description,
        personality=character.personality,
        scenario=character.scenario,
        first_mes=character.first_mes,
        mes_example=character.mes_example,
        system_prompt=character.system_prompt,
        post_history_instructions=character.post_history_instructions,
        character_book=book,
    )


def _history(db: Session, chat_id: int) -> list[dict]:
    """The last N messages, oldest first, as {role, content}."""
    rows = db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_WINDOW)
    ).all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def _build(
    db: Session, chat: Chat, character: Character, user_text: str, user_name: str
) -> tuple[list[dict], object, object]:
    """Compile and fit the prompt for one turn.

    Returns (messages_to_send, compiled_prompt, fit_report).
    """
    card = _card_from(character)
    history = _history(db, chat.id)

    entries = parse_entries(card.character_book)
    # Scan against the recent conversation plus the new message, so a keyword
    # mentioned three turns ago still keeps its entry alive.
    haystack = "\n".join([m["content"] for m in history[-6:]] + [user_text])
    matches = scan(entries, haystack, token_budget=settings.context_budget // 3)

    compiled = compile_prompt(
        card,
        history=history,
        user_name=user_name,
        lore_matches=matches,
    )

    fitted, report = fit_prompt(compiled, settings.context_budget, history=history)

    messages = list(fitted.messages)
    messages.append({"role": "user", "content": user_text})
    return messages, fitted, report


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
def create_chat(
    payload: ChatCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatOut:
    """Start a chat with a character you can see."""
    character = get_visible_character(payload.character_id, user, db)

    chat = Chat(user_id=user.id, character_id=character.id, title=payload.title)
    db.add(chat)
    db.commit()
    db.refresh(chat)

    # Seed the opening line so the chat doesn't start empty.
    if character.first_mes.strip():
        db.add(
            Message(
                chat_id=chat.id,
                role="assistant",
                content=character.first_mes.strip(),
                backend="card",
                simulated=False,
            )
        )
        db.commit()

    return ChatOut.model_validate(chat)


@router.get("", response_model=list[ChatOut])
def list_chats(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ChatOut]:
    """Your chats, most recently updated first."""
    rows = db.scalars(
        select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at.desc()).limit(100)
    ).all()
    return [ChatOut.model_validate(c) for c in rows]


@router.get("/{chat_id}", response_model=ChatDetail)
def get_chat(
    chat_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatDetail:
    """One chat with its full message history."""
    chat = get_owned_chat(chat_id, user, db)
    character = db.get(Character, chat.character_id)
    return ChatDetail(
        **ChatOut.model_validate(chat).model_dump(),
        messages=[MessageOut.model_validate(m) for m in chat.messages],
        character_name=character.name if character else "",
    )


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a chat and its messages."""
    chat = get_owned_chat(chat_id, user, db)
    db.delete(chat)
    db.commit()


@router.post("/{chat_id}/preview", response_model=PromptPreviewOut)
def preview_prompt(
    chat_id: int,
    payload: PromptPreviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PromptPreviewOut:
    """Compile the prompt for a hypothetical next message. Calls no model."""
    chat = get_owned_chat(chat_id, user, db)
    character = db.get(Character, chat.character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")

    messages, compiled, report = _build(db, chat, character, payload.content, user.display_name)

    return PromptPreviewOut(
        blocks=[b.as_dict() for b in compiled.blocks],
        total_tokens=compiled.total_tokens,
        fingerprint=compiled.fingerprint,
        lore_matches=[m.as_dict() for m in compiled.lore_matches],
        fit=report.as_dict(),
        messages=messages,
    )


@router.post("/{chat_id}/messages")
def send_message(
    chat_id: int,
    payload: SendMessageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Send a message and stream the reply as SSE.

    Events:
      meta  - {backend, simulated, prompt_tokens, fingerprint, fit}
      delta - {text}
      done  - {message_id, latency_ms}
      error - {detail}
    """
    chat = get_owned_chat(chat_id, user, db)
    character = db.get(Character, chat.character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")

    messages, compiled, report = _build(db, chat, character, payload.content, user.display_name)

    # Persist the user's message before streaming, so it survives a disconnect.
    user_message = Message(chat_id=chat.id, role="user", content=payload.content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    backend_name = active_backend()
    simulated = backend_name == "none" or (
        backend_name == "openai" and not settings.openai_api_key
    )
    prompt_tokens = compiled.total_tokens
    fingerprint = compiled.fingerprint
    fit_dict = report.as_dict()
    chat_id_value = chat.id

    async def event_stream() -> AsyncIterator[str]:
        started = time.perf_counter()
        collected: list[str] = []

        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield sse(
            "meta",
            {
                "backend": backend_name,
                "simulated": simulated,
                "prompt_tokens": prompt_tokens,
                "fingerprint": fingerprint,
                "fit": fit_dict,
            },
        )

        try:
            async for chunk in stream(messages):
                collected.append(chunk)
                yield sse("delta", {"text": chunk})
        except BackendError as exc:
            yield sse("error", {"detail": str(exc)})
            return

        text = "".join(collected).strip()
        latency_ms = int((time.perf_counter() - started) * 1000)

        # Write the reply in its own session: the request-scoped one is
        # already closed by the time a streaming response finishes.
        session = SessionLocal()
        try:
            reply = Message(
                chat_id=chat_id_value,
                role="assistant",
                content=text,
                backend=backend_name,
                simulated=simulated,
                prompt_tokens=prompt_tokens,
                latency_ms=latency_ms,
                prompt_fingerprint=fingerprint,
            )
            session.add(reply)
            session.commit()
            session.refresh(reply)
            message_id = reply.id

            parent = session.get(Chat, chat_id_value)
            if parent is not None:
                parent.updated_at = reply.created_at
                session.commit()
        finally:
            session.close()

        yield sse("done", {"message_id": message_id, "latency_ms": latency_ms})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
