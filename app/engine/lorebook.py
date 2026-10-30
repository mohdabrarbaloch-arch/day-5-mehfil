"""Lorebook: keyword-triggered context injection.

A lorebook is a set of entries that are injected into the prompt only when
they are relevant. It is how you give a character a large world without
paying for that world on every single turn.

Three behaviours matter, and most implementations get at least one wrong:

1. **Word-boundary matching.** A key of "art" must not fire inside "start".
   Substring matching is the single most common lorebook bug: the character
   suddenly knows about a place nobody mentioned.
2. **`selective` means AND, not OR.** With selective set, every key must
   appear. Without it, any one key is enough.
3. **`constant` always fires**, regardless of the conversation.

Recursive scanning is supported (an injected entry can trigger another) but
bounded, so a cycle of entries cannot loop forever.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .tokenizer import count_tokens

# How many rounds of "an injected entry triggers another entry" we allow.
MAX_RECURSION_DEPTH = 3

# A single entry is capped so one bad entry cannot eat the whole budget.
MAX_ENTRY_CHARS = 8_000


def _word_pattern(key: str) -> re.Pattern[str]:
    """Compile a key into a word-boundary pattern.

    \\b is wrong for keys that start or end with punctuation, so we use
    explicit lookarounds on word characters instead.
    """
    escaped = re.escape(key.strip())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


@dataclass
class LoreEntry:
    """One lorebook entry."""

    keys: list[str] = field(default_factory=list)
    content: str = ""
    comment: str = ""
    selective: bool = False
    constant: bool = False
    case_sensitive: bool = False
    priority: int = 0
    identifier: str = ""

    @property
    def tokens(self) -> int:
        return count_tokens(self.content)

    def matches(self, text: str) -> list[str]:
        """Return the keys that fired, or an empty list.

        For a selective entry every key must fire; we return all of them so
        the UI can show the full trigger set.
        """
        if self.constant:
            return ["<constant>"]
        if not self.keys:
            return []

        haystack = text if self.case_sensitive else text.lower()
        fired: list[str] = []
        for key in self.keys:
            needle = key if self.case_sensitive else key.lower()
            if not needle.strip():
                continue
            if self.case_sensitive:
                pattern = _word_pattern(key)
                if pattern.search(haystack):
                    fired.append(key)
            else:
                pattern = _word_pattern(needle)
                if pattern.search(haystack):
                    fired.append(key)

        if self.selective:
            # AND semantics: every key must have fired.
            return fired if len(fired) == len([k for k in self.keys if k.strip()]) else []
        return fired


@dataclass
class LoreMatch:
    """An entry that fired, plus the keys that caused it."""

    identifier: str
    comment: str
    matched_keys: list[str]
    tokens: int
    content: str

    def as_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "comment": self.comment,
            "matched_keys": list(self.matched_keys),
            "tokens": self.tokens,
        }


def parse_entries(book: dict[str, Any] | None) -> list[LoreEntry]:
    """Turn a character_book dict into LoreEntry objects.

    Tolerant on purpose: cards in the wild use several spellings for the same
    field, and rejecting a card over a key name would be unhelpful.
    """
    if not book or not isinstance(book, dict):
        return []

    raw_entries = book.get("entries")
    if not isinstance(raw_entries, list):
        return []

    entries: list[LoreEntry] = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            continue

        keys = raw.get("keys") or raw.get("key") or []
        if isinstance(keys, str):
            keys = [part.strip() for part in keys.split(",") if part.strip()]
        if not isinstance(keys, list):
            keys = []
        keys = [str(k).strip() for k in keys if str(k).strip()]

        content = raw.get("content") or raw.get("value") or ""
        content = str(content)[:MAX_ENTRY_CHARS]

        entries.append(
            LoreEntry(
                keys=keys,
                content=content,
                comment=str(raw.get("comment") or raw.get("name") or ""),
                selective=bool(raw.get("selective", False)),
                constant=bool(raw.get("constant", False)),
                case_sensitive=bool(raw.get("case_sensitive", False)),
                priority=int(raw.get("priority", 0) or 0),
                identifier=str(raw.get("id") or f"entry-{index}"),
            )
        )

    return entries


def scan(
    entries: list[LoreEntry],
    text: str,
    *,
    token_budget: int | None = None,
    max_depth: int = MAX_RECURSION_DEPTH,
) -> list[LoreMatch]:
    """Find the entries that should be injected for this text.

    Entries are considered in priority order (highest first), then by
    position. If a token budget is given, we stop adding once it is reached
    rather than silently overflowing the context.
    """
    if not entries:
        return []

    ordered = sorted(entries, key=lambda e: (-e.priority, entries.index(e)))

    matched: list[LoreMatch] = []
    seen: set[str] = set()
    used_tokens = 0

    # The haystack grows as entries fire, which is what makes recursion work:
    # an injected entry can mention a keyword that triggers another entry.
    haystack = text

    for _depth in range(max_depth):
        fired_this_round = False
        for entry in ordered:
            if entry.identifier in seen:
                continue
            keys = entry.matches(haystack)
            if not keys:
                continue

            if token_budget is not None and used_tokens + entry.tokens > token_budget:
                continue

            seen.add(entry.identifier)
            used_tokens += entry.tokens
            matched.append(
                LoreMatch(
                    identifier=entry.identifier,
                    comment=entry.comment,
                    matched_keys=keys,
                    tokens=entry.tokens,
                    content=entry.content,
                )
            )
            haystack = f"{haystack}\n{entry.content}"
            fired_this_round = True

        if not fired_this_round:
            break

    return matched


def render(matches: list[LoreMatch]) -> str:
    """Render matched entries as one prompt block."""
    if not matches:
        return ""
    parts = [m.content.strip() for m in matches if m.content.strip()]
    return "\n\n".join(parts)
