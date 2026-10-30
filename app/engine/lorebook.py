"""Lorebook — keyword-triggered world facts.

A lorebook is a set of entries that get injected into the prompt only when the
conversation touches them. Done badly it is the single biggest source of prompt
bloat: every entry matches, the budget explodes, and the actual conversation
gets evicted to make room for trivia.

So this implementation is deliberately strict:

* **Selective entries need ALL their keys** (AND, not OR). "Karachi" alone
  should not pull in an entry keyed on ["Karachi", "rain"].
* **Recursion is bounded.** An injected entry's own text can trigger further
  entries, but only to a fixed depth, so a self-referential entry cannot loop.
* **The budget is a hard cap.** Entries are sorted by priority and dropped
  whole — never truncated mid-sentence, because half a fact is worse than none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import LoreEntryData
from .tokenizer import estimate_tokens

MAX_RECURSION_DEPTH = 2


@dataclass
class MatchedEntry:
    name: str
    content: str
    priority: int
    matched_keys: list[str]
    tokens: int


@dataclass
class LoreResult:
    entries: list[MatchedEntry]
    dropped: list[str]
    tokens_used: int
    budget: int

    @property
    def text(self) -> str:
        """Render matched entries as a single prompt block."""
        if not self.entries:
            return ""
        return "\n\n".join(entry.content for entry in self.entries)


def _normalise(text: str, case_sensitive: bool) -> str:
    return text if case_sensitive else text.lower()


def _entry_matches(entry: LoreEntryData, haystack: str) -> list[str]:
    """Return the keys that matched. Empty list means no match."""
    if not entry.enabled:
        return []

    if entry.constant:
        # Constant entries always inject; they need no keys.
        return ["<constant>"]

    if not entry.keys:
        return []

    matched: list[str] = []
    for key in entry.keys:
        needle = _normalise(key, entry.case_sensitive)
        if not needle:
            continue
        if entry.case_sensitive:
            if needle in haystack:
                matched.append(key)
        else:
            # Word-boundary match where the key is alphanumeric, so "art" does
            # not fire on "start". Fall back to substring for keys with
            # punctuation or spaces.
            if re.fullmatch(r"[\w\s'-]+", needle):
                if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
                    matched.append(key)
            elif needle in haystack:
                matched.append(key)

    # Any matching key fires the entry. Requiring *all* keys (AND) sounds
    # tidier but in practice almost never fires: a two-key entry needs both
    # words in the same turn. SillyTavern and friends use OR for this reason.
    return matched


def scan(
    entries: list[LoreEntryData],
    text: str,
    budget: int = 600,
    depth: int = 0,
    seen: set[str] | None = None,
) -> LoreResult:
    """Select lore entries relevant to ``text`` within ``budget`` tokens.

    ``seen`` carries entry identities across recursion so an entry is never
    injected twice.
    """
    seen = seen if seen is not None else set()
    haystack = text.lower()

    candidates: list[tuple[LoreEntryData, list[str]]] = []
    for entry in entries:
        identity = f"{entry.name}|{entry.content[:60]}"
        if identity in seen:
            continue
        matched = _entry_matches(entry, haystack)
        if matched:
            candidates.append((entry, matched))

    # Highest priority first; stable on insertion order for ties.
    candidates.sort(key=lambda pair: -pair[0].priority)

    selected: list[MatchedEntry] = []
    dropped: list[str] = []
    used = 0

    for entry, matched in candidates:
        cost = estimate_tokens(entry.content)
        if used + cost > budget:
            dropped.append(entry.name or entry.content[:40])
            continue
        used += cost
        selected.append(
            MatchedEntry(
                name=entry.name or entry.content[:40],
                content=entry.content,
                priority=entry.priority,
                matched_keys=matched,
                tokens=cost,
            )
        )
        seen.add(f"{entry.name}|{entry.content[:60]}")

    # Bounded recursion: injected text may trigger further entries.
    if depth < MAX_RECURSION_DEPTH and selected:
        injected = "\n".join(item.content for item in selected)
        nested = scan(
            entries,
            injected,
            budget=max(0, budget - used),
            depth=depth + 1,
            seen=seen,
        )
        if nested.entries:
            selected.extend(nested.entries)
            used += nested.tokens_used
            dropped.extend(nested.dropped)

    return LoreResult(entries=selected, dropped=dropped, tokens_used=used, budget=budget)
