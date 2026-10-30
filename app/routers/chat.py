"""Chat routes: conversations, messages, streaming."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_owned_conversation
from ..engine import BackendError, build, extract_style, generate, scan, score, stream
from ..models import Character, Conversation, Message, User
from ..schemas import (
    ConversationDetail,
    ConversationIn,
    ConversationOut,
    FidelityOut,
    MessageIn,
    MessageOut,
    SendMessageResponse,
)

router = APIRouter(prefix="/api/conversations", tags=["chat"])

HISTORY_LIMIT = 40


def _to_card(character: Character):
    from .characters import _to_card as convert

    return convert(character)


def _history(db: Session, conversation_id: int) -> list[dict[str, str]]:
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationOut]:
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.owner_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    ).all()
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    character = db.get(Character, payload.character_id)
    if character is None or (character.owner_id != user.id and not character.is_public):
        raise HTTPException(status_code=404, detail="Character not found.")

    conversation = Conversation(
        owner_id=user.id,
        character_id=character.id,
        title=(payload.title or f"Chat with {character.name}")[:120],
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    # Seed with the character's opening line so the chat is never blank.
    if character.first_mes:
        db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=character.first_mes,
                backend="card",
                model="first_mes",
                simulated=False,
            )
        )
        db.commit()
        db.refresh(conversation)

    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in conversation.messages],
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation: Conversation = Depends(get_owned_conversation),
) -> ConversationDetail:
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in conversation.messages],
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_conversation(
    conversation: Conversation = Depends(get_owned_conversation),
    db: Session = Depends(get_db),
) -> Response:
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    payload: MessageIn,
    conversation: Conversation = Depends(get_owned_conversation),
    db: Session = Depends(get_db),
) -> SendMessageResponse:
    character = conversation.character
    card = _to_card(character)

    history = _history(db, conversation.id)
    lore = scan(card.lore, payload.content, budget=settings.lore_budget)
    bundle = build(
        card,
        history=history,
        user_text=payload.content,
        lore=lore,
        context_budget=settings.context_budget,
        max_reply_tokens=settings.max_reply_tokens,
    )

    started = time.perf_counter()
    error_text = ""
    try:
        result = await generate(bundle)
        reply_text = result.text
        backend = result.backend
        model = result.model
        simulated = result.simulated
    except BackendError as exc:
        # A configured backend failed. We record the failure rather than
        # pretending a reply happened.
        reply_text = (
            "The configured model backend failed, so no reply was generated.\n\n" f"Error: {exc}"
        )
        backend = "error"
        model = ""
        simulated = True
        error_text = str(exc)

    latency = int((time.perf_counter() - started) * 1000)

    style = extract_style(card.mes_example)
    # Only score a reply that a model actually produced. Scoring the "no backend"
    # notice against the character's voice would report a meaningless number —
    # and a meaningless number is worse than none.
    fidelity = score(reply_text, style) if not simulated else None

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        prompt_tokens=bundle.fit.used,
        prompt_fingerprint=bundle.fingerprint,
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_text,
        backend=backend,
        model=model,
        simulated=simulated,
        fidelity_score=fidelity.score if fidelity else None,
        fidelity_label=fidelity.label if fidelity else None,
        prompt_fingerprint=bundle.fingerprint,
        prompt_tokens=bundle.fit.used,
        latency_ms=latency,
        error=error_text,
    )
    db.add_all([user_message, assistant_message])
    db.commit()
    db.refresh(assistant_message)

    return SendMessageResponse(
        message=MessageOut.model_validate(assistant_message),
        fidelity=FidelityOut(**fidelity.to_dict()) if fidelity else None,
        fit=bundle.fit.to_dict(),
        fingerprint=bundle.fingerprint,
        backend=backend,
        simulated=simulated,
    )


@router.post("/{conversation_id}/stream")
async def stream_message(
    payload: MessageIn,
    conversation: Conversation = Depends(get_owned_conversation),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-sent events.

    Emits ``token`` events as text arrives, then a final ``meta`` event with the
    fidelity score and fit report. The meta event is what makes streaming
    honest — you still learn whether the reply drifted.
    """
    character = conversation.character
    card = _to_card(character)
    history = _history(db, conversation.id)
    lore = scan(card.lore, payload.content, budget=settings.lore_budget)
    bundle = build(
        card,
        history=history,
        user_text=payload.content,
        lore=lore,
        context_budget=settings.context_budget,
        max_reply_tokens=settings.max_reply_tokens,
    )

    conversation_id = conversation.id
    user_text = payload.content
    fingerprint = bundle.fingerprint
    fit = bundle.fit.to_dict()
    style = extract_style(card.mes_example)

    async def event_source():
        collected: list[str] = []
        backend = "none"
        error_text = ""

        try:
            async for chunk in stream(bundle):
                collected.append(chunk)
                yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"
        except BackendError as exc:
            error_text = str(exc)
            backend = "error"
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

        full_text = "".join(collected).strip()
        # Same rule as the non-streaming path: never score a non-model message.
        fidelity = score(full_text, style) if backend != "none" else None

        # Persist outside the request session's lifetime is unsafe, so we open
        # a fresh session for the write.
        from ..database import SessionLocal

        write_db = SessionLocal()
        try:
            write_db.add(
                Message(
                    conversation_id=conversation_id,
                    role="user",
                    content=user_text,
                    prompt_tokens=fit["used"],
                    prompt_fingerprint=fingerprint,
                )
            )
            write_db.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_text,
                    backend=backend,
                    simulated=backend == "none",
                    fidelity_score=fidelity.score if fidelity else None,
                    fidelity_label=fidelity.label if fidelity else None,
                    prompt_fingerprint=fingerprint,
                    prompt_tokens=fit["used"],
                    error=error_text,
                )
            )
            write_db.commit()
        finally:
            write_db.close()

        meta = {
            "fidelity": fidelity.to_dict() if fidelity else None,
            "fit": fit,
            "fingerprint": fingerprint,
            "backend": backend,
            "simulated": backend == "none",
        }
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
