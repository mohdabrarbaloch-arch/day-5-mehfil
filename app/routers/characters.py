"""Character routes: CRUD, card import/export, style card, prompt preview."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_owned_character
from ..engine import (
    Card,
    CardError,
    LoreEntryData,
    build,
    extract_style,
    parse_card,
    scan,
    validate_card,
)
from ..models import Character, LoreEntry, User
from ..schemas import (
    CardImportRequest,
    CardImportResponse,
    CharacterIn,
    CharacterOut,
    CharacterSummary,
    LoreEntryOut,
    PreviewRequest,
    PreviewResponse,
    StyleCardOut,
)

router = APIRouter(prefix="/api/characters", tags=["characters"])


# ---------------------------------------------------------------- helpers


def _to_card(character: Character) -> Card:
    """Build an engine Card from the ORM row."""
    return Card(
        name=character.name,
        description=character.description,
        personality=character.personality,
        scenario=character.scenario,
        first_mes=character.first_mes,
        mes_example=character.mes_example,
        creator_notes=character.creator_notes,
        system_prompt=character.system_prompt,
        post_history_instructions=character.post_history_instructions,
        alternate_greetings=_split_lines(character.alternate_greetings),
        tags=_split_csv(character.tags),
        creator=character.creator,
        character_version=character.character_version,
        lore=[
            LoreEntryData(
                keys=_split_csv(entry.keys),
                content=entry.content,
                priority=entry.priority,
                enabled=entry.enabled,
                constant=entry.constant,
                case_sensitive=entry.case_sensitive,
                name=entry.name,
            )
            for entry in character.lore
        ],
    )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _split_lines(value: str | None) -> list[str]:
    if not value:
        return []
    return [line for line in value.split("\n") if line.strip()]


def _apply_card(character: Character, card: Card) -> None:
    character.name = card.name
    character.description = card.description
    character.personality = card.personality
    character.scenario = card.scenario
    character.first_mes = card.first_mes
    character.mes_example = card.mes_example
    character.creator_notes = card.creator_notes
    character.system_prompt = card.system_prompt
    character.post_history_instructions = card.post_history_instructions
    character.alternate_greetings = "\n".join(card.alternate_greetings)
    character.tags = ",".join(card.tags)
    character.creator = card.creator
    character.character_version = card.character_version
    character.style_json = json.dumps(extract_style(card.mes_example).to_dict())


def _apply_lore(character: Character, entries: list[LoreEntryData]) -> None:
    character.lore.clear()
    for entry in entries:
        character.lore.append(
            LoreEntry(
                name=entry.name,
                keys=",".join(entry.keys),
                content=entry.content,
                priority=entry.priority,
                enabled=entry.enabled,
                constant=entry.constant,
                case_sensitive=entry.case_sensitive,
            )
        )


def _lore_from_payload(payload: CharacterIn) -> list[LoreEntryData]:
    return [
        LoreEntryData(
            keys=entry.keys,
            content=entry.content,
            priority=entry.priority,
            enabled=entry.enabled,
            constant=entry.constant,
            case_sensitive=entry.case_sensitive,
            name=entry.name,
        )
        for entry in payload.lore
    ]


def _card_from_payload(payload: CharacterIn) -> Card:
    return Card(
        name=payload.name,
        description=payload.description,
        personality=payload.personality,
        scenario=payload.scenario,
        first_mes=payload.first_mes,
        mes_example=payload.mes_example,
        creator_notes=payload.creator_notes,
        system_prompt=payload.system_prompt,
        post_history_instructions=payload.post_history_instructions,
        alternate_greetings=payload.alternate_greetings,
        tags=payload.tags,
        creator=payload.creator,
        character_version=payload.character_version,
    )


def _serialise(character: Character) -> CharacterOut:
    """Build the response model explicitly.

    ``tags`` and ``alternate_greetings`` are stored as delimited strings, so
    ``model_validate`` on the ORM row would fail before we could convert them.
    Constructing field by field keeps the conversion in one obvious place.
    """
    return CharacterOut(
        id=character.id,
        name=character.name,
        description=character.description,
        personality=character.personality,
        scenario=character.scenario,
        first_mes=character.first_mes,
        mes_example=character.mes_example,
        creator_notes=character.creator_notes,
        system_prompt=character.system_prompt,
        post_history_instructions=character.post_history_instructions,
        alternate_greetings=_split_lines(character.alternate_greetings),
        tags=_split_csv(character.tags),
        creator=character.creator,
        character_version=character.character_version,
        is_public=character.is_public,
        card_version=character.card_version,
        created_at=character.created_at,
        updated_at=character.updated_at,
        lore=[
            LoreEntryOut(
                id=entry.id,
                name=entry.name,
                keys=_split_csv(entry.keys),
                content=entry.content,
                priority=entry.priority,
                enabled=entry.enabled,
                constant=entry.constant,
                case_sensitive=entry.case_sensitive,
            )
            for entry in character.lore
        ],
    )


# ---------------------------------------------------------------- routes


@router.get("", response_model=list[CharacterSummary])
def list_characters(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    include_public: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[CharacterSummary]:
    condition = Character.owner_id == user.id
    if include_public:
        condition = or_(condition, Character.is_public.is_(True))

    rows = db.scalars(
        select(Character)
        .where(condition)
        .order_by(Character.updated_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return [
        CharacterSummary(
            id=row.id,
            name=row.name,
            description=row.description,
            tags=_split_csv(row.tags),
            is_public=row.is_public,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.post("", response_model=CharacterOut, status_code=status.HTTP_201_CREATED)
def create_character(
    payload: CharacterIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterOut:
    count = db.scalar(
        select(func.count()).select_from(Character).where(Character.owner_id == user.id)
    )
    if (count or 0) >= settings.max_characters_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Character limit reached ({settings.max_characters_per_user}).",
        )

    character = Character(owner_id=user.id)
    _apply_card(character, _card_from_payload(payload))
    _apply_lore(character, _lore_from_payload(payload))
    character.is_public = payload.is_public

    db.add(character)
    db.commit()
    db.refresh(character)
    return _serialise(character)


@router.get("/{character_id}", response_model=CharacterOut)
def get_character(
    character: Character = Depends(get_owned_character),
) -> CharacterOut:
    return _serialise(character)


@router.put("/{character_id}", response_model=CharacterOut)
def update_character(
    payload: CharacterIn,
    character: Character = Depends(get_owned_character),
    db: Session = Depends(get_db),
) -> CharacterOut:
    _apply_card(character, _card_from_payload(payload))
    _apply_lore(character, _lore_from_payload(payload))
    character.is_public = payload.is_public

    db.commit()
    db.refresh(character)
    return _serialise(character)


@router.delete(
    "/{character_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_character(
    character: Character = Depends(get_owned_character),
    db: Session = Depends(get_db),
) -> Response:
    db.delete(character)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{character_id}/style", response_model=StyleCardOut)
def get_style(
    character: Character = Depends(get_owned_character),
) -> StyleCardOut:
    """The measured voice fingerprint, extracted from example dialogue."""
    if character.style_json:
        try:
            return StyleCardOut(**json.loads(character.style_json))
        except (ValueError, TypeError):
            pass
    return StyleCardOut(**extract_style(character.mes_example).to_dict())


@router.get("/{character_id}/export")
def export_card(
    character: Character = Depends(get_owned_character),
) -> dict:
    """Export as a spec-shaped Character Card V2."""
    return _to_card(character).to_v2()


@router.post("/import", response_model=CardImportResponse, status_code=status.HTTP_201_CREATED)
def import_card(
    payload: CardImportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardImportResponse:
    try:
        card = parse_card(payload.card)
    except CardError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    warnings = validate_card(card)

    character = Character(owner_id=user.id)
    _apply_card(character, card)
    _apply_lore(character, card.lore)
    character.card_version = "2.0"

    db.add(character)
    db.commit()
    db.refresh(character)
    return CardImportResponse(character=_serialise(character), warnings=warnings)


@router.post("/{character_id}/preview", response_model=PreviewResponse)
def preview_prompt(
    payload: PreviewRequest,
    character: Character = Depends(get_owned_character),
) -> PreviewResponse:
    """Compile the prompt without calling a model.

    This is the inspector: it shows exactly what would be sent, block by block,
    with token counts and what got dropped.
    """
    card = _to_card(character)
    lore = scan(card.lore, payload.content, budget=settings.lore_budget)
    bundle = build(
        card,
        history=[],
        user_text=payload.content or "(empty turn)",
        lore=lore,
        context_budget=payload.context_budget or settings.context_budget,
        max_reply_tokens=settings.max_reply_tokens,
    )
    return PreviewResponse(
        blocks=[
            {
                "name": block.name,
                "content": block.content,
                "tokens": block.tokens,
                "truncated": block.truncated,
            }
            for block in bundle.blocks
        ],
        fit=bundle.fit.to_dict(),
        fingerprint=bundle.fingerprint,
        rendered=bundle.render(),
    )
