"""Character Card V2 — parse, validate, export.

The V2 spec is the de-facto interchange format for character cards. Supporting
it means a user can import a card from anywhere and keep their work, and export
back out without lock-in.

Spec shape::

    {
      "spec": "chara_card_v2",
      "spec_version": "2.0",
      "data": {
        "name": "...", "description": "...", "personality": "...",
        "scenario": "...", "first_mes": "...", "mes_example": "...",
        "creator_notes": "...", "system_prompt": "...",
        "post_history_instructions": "...",
        "alternate_greetings": ["..."],
        "character_book": {"entries": [...]},
        "tags": ["..."], "creator": "...", "character_version": "..."
      }
    }

We also accept the older flat V1 shape and upgrade it, because a lot of cards
in the wild are still V1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SPEC_NAME = "chara_card_v2"
SPEC_VERSION = "2.0"

MAX_NAME = 80
MAX_TEXT = 20_000
MAX_TAGS = 20
MAX_GREETINGS = 20


class CardError(ValueError):
    """Raised when a card cannot be parsed or fails validation."""


@dataclass
class LoreEntryData:
    keys: list[str]
    content: str
    priority: int = 10
    enabled: bool = True
    constant: bool = False
    case_sensitive: bool = False
    name: str = ""


@dataclass
class Card:
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
    lore: list[LoreEntryData] = field(default_factory=list)

    def to_v2(self) -> dict[str, Any]:
        """Serialise back to a spec-shaped V2 dict."""
        return {
            "spec": SPEC_NAME,
            "spec_version": SPEC_VERSION,
            "data": {
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
                "character_book": {
                    "entries": [
                        {
                            "keys": list(entry.keys),
                            "content": entry.content,
                            "priority": entry.priority,
                            "enabled": entry.enabled,
                            "constant": entry.constant,
                            "case_sensitive": entry.case_sensitive,
                            "name": entry.name,
                        }
                        for entry in self.lore
                    ]
                },
                "tags": list(self.tags),
                "creator": self.creator,
                "character_version": self.character_version,
            },
        }


def _as_str(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:limit]


def _as_str_list(value: Any, limit: int) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _as_str(item, 200)
        if text:
            out.append(text)
    return out[:limit]


def _parse_lore(raw: Any) -> list[LoreEntryData]:
    """Parse a character_book. Accepts both V2 and V1 entry shapes."""
    if not isinstance(raw, dict):
        return []
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return []

    parsed: list[LoreEntryData] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        # V1 used `key` (list) and `keysecondary`; V2 uses `keys`.
        keys = _as_str_list(item.get("keys") or item.get("key"), 20)
        content = _as_str(item.get("content"))
        if not keys or not content:
            continue
        try:
            priority = int(item.get("priority", item.get("order", 10)))
        except (TypeError, ValueError):
            priority = 10
        parsed.append(
            LoreEntryData(
                keys=keys,
                content=content,
                priority=max(0, min(100, priority)),
                enabled=bool(item.get("enabled", True)),
                constant=bool(item.get("constant", False)),
                case_sensitive=bool(item.get("case_sensitive", False)),
                name=_as_str(item.get("name"), 120),
            )
        )
    return parsed


def parse_card(payload: Any) -> Card:
    """Parse a V2 (or legacy V1) card dict into a :class:`Card`.

    Raises :class:`CardError` on anything unusable.
    """
    if not isinstance(payload, dict):
        raise CardError("Card must be a JSON object.")

    # V2 wraps everything in `data`; V1 is flat.
    data = payload["data"] if isinstance(payload.get("data"), dict) else payload

    name = _as_str(data.get("name"), MAX_NAME)
    if not name:
        raise CardError("Card is missing a name.")

    card = Card(
        name=name,
        description=_as_str(data.get("description")),
        personality=_as_str(data.get("personality")),
        scenario=_as_str(data.get("scenario")),
        first_mes=_as_str(data.get("first_mes") or data.get("first_message")),
        mes_example=_as_str(data.get("mes_example") or data.get("example_dialogue")),
        creator_notes=_as_str(data.get("creator_notes")),
        system_prompt=_as_str(data.get("system_prompt")),
        post_history_instructions=_as_str(data.get("post_history_instructions")),
        alternate_greetings=_as_str_list(data.get("alternate_greetings"), MAX_GREETINGS),
        tags=_as_str_list(data.get("tags"), MAX_TAGS),
        creator=_as_str(data.get("creator"), 120),
        character_version=_as_str(data.get("character_version"), 40),
        lore=_parse_lore(data.get("character_book")),
    )

    if not any([card.description, card.personality, card.scenario, card.first_mes]):
        raise CardError(
            "Card has a name but no content — at least one of description, "
            "personality, scenario or first_mes is required."
        )

    return card


def validate_card(card: Card) -> list[str]:
    """Return a list of human-readable warnings. Empty list means clean."""
    warnings: list[str] = []

    if not card.description and not card.personality:
        warnings.append(
            "No description or personality — the character has nothing to be "
            "built from and will read as a generic assistant."
        )
    if not card.mes_example:
        warnings.append("No example dialogue — voice fidelity cannot be measured without it.")
    if not card.first_mes:
        warnings.append("No first message — the chat will open empty.")
    if len(card.name) > 40:
        warnings.append("Name is long; it will be truncated in the character list.")
    if card.description and len(card.description) > 4000:
        warnings.append(
            "Description is very long and will consume a large share of the "
            "context budget on every turn."
        )
    for entry in card.lore:
        if entry.enabled and not entry.keys and not entry.constant:
            warnings.append(
                f"Lore entry '{entry.name or entry.content[:30]}' has no keys and "
                "is not constant — it will never trigger."
            )
    return warnings
