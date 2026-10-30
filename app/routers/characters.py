"""Character CRUD plus Character Card V2 import/export."""

Import accepts v1 or v2 and returns validation warnings rather than rejecting
a thin card. A card with no description still works -- it just produces a weak
persona -- and the user should be told that instead of blocked.

Export always emits v2, so a card made here opens in SillyTavern.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_owned_character, get_visible_character
from app.engine.cards import CardError, CharacterCard, parse_card, validate_card
from app.models import Character, User
from app.schemas import (
    CardImportRequest,
    CharacterCreate,
    CharacterDetail,
    CharacterOut,
    CharacterUpdate,
)

router = APIRouter(prefix="/characters", tags=["characters"])


def _tags(character: Character) -> list[str]:
    try:
        parsed = json.loads(character.tags_json or "[]")
    except json.JSONDecodeError:
        return []
    return [str(t) for t in parsed] if isinstance(parsed, list) else []


def _book(character: Character) -> dict[str, Any] | None:
    if not character.character_book_json:
        return None
    try:
        parsed = json.loads(character.character_book_json)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _to_out(character: Character) -> CharacterOut:
    return CharacterOut(
        id=character.id,
        owner_id=character.owner_id,
        name=character.name,
        description=character.description,
        personality=character.personality,
        scenario=character.scenario,
        first_mes=character.first_mes,
        mes_example=character.mes_example,
        system_prompt=character.system_prompt,
        post_history_instructions=character.post_history_instructions,
        tags=_tags(character),
        is_public=character.is_public,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


def _to_detail(character: Character) -> CharacterDetail:
    base = _to_out(character)
    card = CharacterCard(
        name=character.name,
        description=character.description,
        personality=character.personality,
        scenario=character.scenario,
        first_mes=character.first_mes,
        mes_example=character.mes_example,
        system_prompt=character.system_prompt,
        post_history_instructions=character.post_history_instructions,
        tags=_tags(character),
        character_book=_book(character),
    )
    return CharacterDetail(
        **base.model_dump(),
        character_book=_book(character),
        warnings=validate_card(card),
    )


@router.get("", response_model=list[CharacterOut])
def list_characters(
    mine: bool = Query(default=False, description="Only characters you own"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CharacterOut]:
    """Your characters, plus everyone's public ones."""
    if mine:
        statement = select(Character).where(Character.owner_id == user.id)
    else:
        statement = select(Character).where(
            or_(Character.owner_id == user.id, Character.is_public.is_(True))
        )
    statement = statement.order_by(Character.updated_at.desc()).limit(200)
    return [_to_out(c) for c in db.scalars(statement).all()]


@router.post("", response_model=CharacterDetail, status_code=status.HTTP_201_CREATED)
def create_character(
    payload: CharacterCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterDetail:
    """Create a character."""
    character = Character(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        personality=payload.personality,
        scenario=payload.scenario,
        first_mes=payload.first_mes,
        mes_example=payload.mes_example,
        system_prompt=payload.system_prompt,
        post_history_instructions=payload.post_history_instructions,
        tags_json=json.dumps(payload.tags),
        character_book_json=(
            json.dumps(payload.character_book) if payload.character_book else ""
        ),
        is_public=payload.is_public,
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return _to_detail(character)


@router.get("/{character_id}", response_model=CharacterDetail)
def get_character(
    character_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterDetail:
    """One character you own or that is public."""
    return _to_detail(get_visible_character(character_id, user, db))


@router.patch("/{character_id}", response_model=CharacterDetail)
def update_character(
    character_id: int,
    payload: CharacterUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterDetail:
    """Update fields you own. Only the fields you send are touched."""
    character = get_owned_character(character_id, user, db)

    data = payload.model_dump(exclude_unset=True)
    if "tags" in data and data["tags"] is not None:
        character.tags_json = json.dumps(data.pop("tags"))
    if "character_book" in data:
        book = data.pop("character_book")
        character.character_book_json = json.dumps(book) if book else ""

    for field, value in data.items():
        if value is not None:
            setattr(character, field, value)

    db.commit()
    db.refresh(character)
    return _to_detail(character)


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(
    character_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a character and every chat attached to it."""
    character = get_owned_character(character_id, user, db)
    db.delete(character)
    db.commit()


@router.post("/import", response_model=CharacterDetail, status_code=status.HTTP_201_CREATED)
def import_card(
    payload: CardImportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterDetail:
    """Import a Character Card V2 (or v1) payload."""
    try:
        card = parse_card(payload.card)
    except CardError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    character = Character(
        owner_id=user.id,
        name=card.name,
        description=card.description,
        personality=card.personality,
        scenario=card.scenario,
        first_mes=card.first_mes,
        mes_example=card.mes_example,
        system_prompt=card.system_prompt,
        post_history_instructions=card.post_history_instructions,
        tags_json=json.dumps(card.tags),
        character_book_json=(
            json.dumps(card.character_book) if card.character_book else ""
        ),
        is_public=payload.is_public,
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return _to_detail(character)


@router.get("/{character_id}/export")
def export_card(
    character_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Export as a spec-compliant Character Card V2."""
    character = get_visible_character(character_id, user, db)
    card = CharacterCard(
        name=character.name,
        description=character.description,
        personality=character.personality,
        scenario=character.scenario,
        first_mes=character.first_mes,
        mes_example=character.mes_example,
        system_prompt=character.system_prompt,
        post_history_instructions=character.post_history_instructions,
        tags=_tags(character),
        character_book=_book(character),
        creator=user.display_name if character.owner_id == user.id else "",
    )
    return card.to_v2()
