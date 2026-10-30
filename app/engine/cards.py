"""Character Card V2 parsing, validation and export.

The V2 spec is what the character-card community actually exchanges, so
Mehfil speaks it natively rather than inventing a private format. That means
a card exported from here opens in SillyTavern, and a card downloaded from
a card site imports here without a conversion step.

Spec reference: https://github.com/malfoyslastname/character-card-spec-v2

We also accept the older flat "v1" shape, because a large share of cards in
circulation are still v1 and refusing them would be user-hostile. v1 cards
are upgraded on import and always exported as v2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SPEC_NAME = "chara_card_v2"
SPEC_VERSION = "2.0"

# Hard caps. These exist so a malicious or broken card cannot exhaust memory
# or blow the context budget before the user has even opened it.
MAX_FIELD_CHARS = 20_000
MAX_ALTERNATE_GREETINGS = 20
MAX_LORE_ENTRIES = 200
MAX_TAGS = 20


class CardError(ValueError):
    """Raised when a card cannot be parsed or fails validation."""


@dataclass
class CharacterCard:
    """A normalised character card, always in v2 shape internally."""

    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    creator_notes: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    creator: str = ""
    character_version: str = ""
    character_book: dict[str, Any] | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_v2(self) -> dict[str, Any]:
        """Serialise back to a spec-compliant v2 card."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "alternate_greetings": list(self.alternate_greetings),
            "tags": list(self.tags),
            "creator": self.creator,
            "character_version": self.character_version,
            "extensions": dict(self.extensions),
        }
        if self.character_book is not None:
            data["character_book"] = self.character_book
        return {"spec": SPEC_NAME, "spec_version": SPEC_VERSION, "data": data}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_v2(), ensure_ascii=False, indent=indent)


def _as_str(value: Any, field_name: str) -> str:
    """Coerce a field to a bounded string, rejecting non-scalar junk."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        raise CardError(f"field '{field_name}' must be a string, got {type(value).__name__}")
    text = str(value)
    if len(text) > MAX_FIELD_CHARS:
        raise CardError(
            f"field '{field_name}' is {len(text)} characters, over the {MAX_FIELD_CHARS} limit"
        )
    return text


def _as_str_list(value: Any, field_name: str, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # Some v1 cards store tags as a comma-separated string.
        return [part.strip() for part in value.split(",") if part.strip()][:limit]
    if not isinstance(value, list):
        raise CardError(f"field '{field_name}' must be a list")
    out: list[str] = []
    for item in value[:limit]:
        if isinstance(item, (dict, list)):
            continue
        text = str(item).strip()
        if text:
            out.append(text[:500])
    return out


def parse_card(payload: str | dict[str, Any]) -> CharacterCard:
    """Parse a v1 or v2 card from JSON text or an already-decoded dict."""
    if isinstance(payload, str):
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise CardError(f"card is not valid JSON: {exc.msg} at line {exc.lineno}") from exc
    elif isinstance(payload, dict):
        raw = payload
    else:
        raise CardError("card must be a JSON object or a JSON string")

    if not isinstance(raw, dict):
        raise CardError("card must be a JSON object")

    # v2 wraps everything in "data"; v1 is flat.
    if raw.get("spec") == SPEC_NAME or isinstance(raw.get("data"), dict):
        data = raw.get("data")
        if not isinstance(data, dict):
            raise CardError("v2 card is missing its 'data' object")
    else:
        data = raw

    name = _as_str(data.get("name"), "name").strip()
    if not name:
        raise CardError("card is missing a 'name'")

    book = data.get("character_book")
    if book is not None and not isinstance(book, dict):
        raise CardError("'character_book' must be an object")
    if isinstance(book, dict):
        entries = book.get("entries")
        if isinstance(entries, list) and len(entries) > MAX_LORE_ENTRIES:
            raise CardError(
                f"character_book has {len(entries)} entries, over the {MAX_LORE_ENTRIES} limit"
            )

    extensions = data.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        extensions = {}

    return CharacterCard(
        name=name,
        description=_as_str(data.get("description"), "description"),
        personality=_as_str(data.get("personality"), "personality"),
        scenario=_as_str(data.get("scenario"), "scenario"),
        first_mes=_as_str(data.get("first_mes"), "first_mes"),
        mes_example=_as_str(data.get("mes_example"), "mes_example"),
        creator_notes=_as_str(data.get("creator_notes"), "creator_notes"),
        system_prompt=_as_str(data.get("system_prompt"), "system_prompt"),
        post_history_instructions=_as_str(
            data.get("post_history_instructions"), "post_history_instructions"
        ),
        alternate_greetings=_as_str_list(
            data.get("alternate_greetings"), "alternate_greetings", MAX_ALTERNATE_GREETINGS
        ),
        tags=_as_str_list(data.get("tags"), "tags", MAX_TAGS),
        creator=_as_str(data.get("creator"), "creator"),
        character_version=_as_str(data.get("character_version"), "character_version"),
        character_book=book,
        extensions=extensions or {},
    )


def validate_card(card: CharacterCard) -> list[str]:
    """Return human-readable warnings. An empty list means the card is clean.

    These are warnings, not errors: a card with no description still works,
    it just produces a thin persona. We surface the problem instead of
    silently accepting it.
    """
    warnings: list[str] = []

    if not card.description.strip() and not card.personality.strip():
        warnings.append(
            "No description and no personality: the model has almost nothing to go on. "
            "Add at least a few sentences about who this character is."
        )
    if not card.first_mes.strip():
        warnings.append("No first message: the chat will open empty instead of in character.")
    if not card.scenario.strip():
        warnings.append("No scenario: the character has no setting to react to.")
    if card.mes_example.strip() and "{{char}}" not in card.mes_example:
        warnings.append(
            "mes_example does not use {{char}} or {{user}} macros, so the model may not "
            "understand who is speaking in the examples."
        )
    if len(card.description) > 8_000:
        warnings.append(
            f"Description is {len(card.description)} characters. Long descriptions eat the "
            "context budget on every single turn; consider moving detail into the lorebook."
        )
    if card.character_book and not card.character_book.get("entries"):
        warnings.append("character_book is present but has no entries.")

    return warnings
