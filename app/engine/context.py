"""Context planning: make the prompt fit, and say what you dropped.

Every chat app silently truncates when the context window fills up. That is
why a character "forgets" the start of a conversation with no explanation,
and it is the single most confusing failure mode in this whole category.

fit_prompt evicts in a documented order and returns a report naming every
block it touched. The UI shows that report, so "it forgot" becomes "it
dropped 6 messages to fit 4096 tokens".

Eviction order, cheapest loss first:

    1. oldest history messages   (the conversation is the most disposable)
    2. examples                  (style guidance, not facts)
    3. lore                      (recoverable -- it re-triggers on keywords)
    4. scenario                  (setting, but the persona still carries it)

Never evicted: system and persona. A character that loses its own description
is not a character any more.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compiler import CompiledPrompt, PromptBlock
from .tokenizer import count_tokens

# Blocks that may be trimmed, in the order we are willing to lose them.
EVICTABLE = ("history", "examples", "lore", "scenario")

# Blocks we will never drop.
PROTECTED = ("system", "persona", "post_history")

# When trimming a text block, keep at least this much so it stays meaningful.
MIN_BLOCK_TOKENS = 24


@dataclass
class Eviction:
    """One thing we removed or shortened, and why."""

    block: str
    action: str  # "dropped" | "truncated" | "messages_removed"
    detail: str
    tokens_freed: int

    def as_dict(self) -> dict:
        return {
            "block": self.block,
            "action": self.action,
            "detail": self.detail,
            "tokens_freed": self.tokens_freed,
        }


@dataclass
class FitReport:
    """What fitting did, in plain language."""

    budget: int
    before: int
    after: int
    evictions: list[Eviction] = field(default_factory=list)
    dropped_messages: int = 0

    @property
    def fits(self) -> bool:
        return self.after <= self.budget

    @property
    def overflow(self) -> int:
        return max(0, self.after - self.budget)

    def summary(self) -> str:
        if not self.evictions:
            return f"Fits in {self.budget} tokens with {self.budget - self.after} to spare."
        parts = [f"Trimmed {self.before} -> {self.after} tokens to fit {self.budget}."]
        if self.dropped_messages:
            parts.append(f"Dropped {self.dropped_messages} old message(s).")
        for eviction in self.evictions:
            if eviction.action != "messages_removed":
                parts.append(f"{eviction.block}: {eviction.detail}.")
        if not self.fits:
            parts.append(f"Still {self.overflow} tokens over budget.")
        return " ".join(parts)

    def as_dict(self) -> dict:
        return {
            "budget": self.budget,
            "before": self.before,
            "after": self.after,
            "fits": self.fits,
            "overflow": self.overflow,
            "dropped_messages": self.dropped_messages,
            "evictions": [e.as_dict() for e in self.evictions],
            "summary": self.summary(),
        }


def _truncate_to_tokens(text: str, target_tokens: int) -> str:
    """Cut text down to roughly target_tokens, on a word boundary.

    We cut from the end and add an ellipsis, because the beginning of a
    description is almost always the most important part.
    """
    if target_tokens <= 0:
        return ""
    if count_tokens(text) <= target_tokens:
        return text

    # 4 chars/token is the documented average; walk back from there.
    approx_chars = max(1, int(target_tokens * 4))
    cut = text[:approx_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip() + " ..."

    # The estimate can be off; tighten until it genuinely fits.
    guard = 0
    while count_tokens(cut) > target_tokens and guard < 20:
        cut = cut[: max(1, len(cut) - max(8, len(cut) // 10))]
        cut = cut.rstrip() + " ..."
        guard += 1
    return cut


def fit_prompt(
    prompt: CompiledPrompt,
    budget: int,
    *,
    history: list[dict] | None = None,
) -> tuple[CompiledPrompt, FitReport]:
    """Trim the prompt until it fits, recording every change.

    Returns a new CompiledPrompt (the input is not mutated) and a FitReport.
    If `history` is passed, dropped messages are also removed from the
    returned prompt's `messages` list, so what you inspect is what you send.
    """
    before = prompt.total_tokens
    evictions: list[Eviction] = []
    dropped_messages = 0

    blocks = [PromptBlock(**b.as_dict()) for b in prompt.blocks]
    messages = list(prompt.messages)

    def total() -> int:
        return sum(b.tokens for b in blocks)

    # 1. History first: drop oldest messages until we fit or run out.
    history_block = next((b for b in blocks if b.name == "history"), None)
    if history_block is not None and total() > budget:
        # messages[0] is the system message; history starts at index 1.
        while total() > budget and len(messages) > 1:
            removed = messages.pop(1)
            dropped_messages += 1
            freed = count_tokens(str(removed.get("content", ""))) + 4
            history_block.tokens = max(0, history_block.tokens - freed)
            history_block.content = f"{len(messages) - 1} message(s)"
        if dropped_messages:
            evictions.append(
                Eviction(
                    block="history",
                    action="messages_removed",
                    detail=f"dropped {dropped_messages} oldest message(s)",
                    tokens_freed=before - total(),
                )
            )

    # 2. Then the evictable text blocks, in order.
    for name in EVICTABLE:
        if total() <= budget:
            break
        if name == "history":
            continue
        block = next((b for b in blocks if b.name == name), None)
        if block is None or not block.content.strip():
            continue

        overflow = total() - budget
        target = max(MIN_BLOCK_TOKENS, block.tokens - overflow)
        original_tokens = block.tokens

        if target <= MIN_BLOCK_TOKENS and block.tokens > MIN_BLOCK_TOKENS:
            # Not worth keeping a stub; drop it entirely.
            block.content = ""
            block.tokens = 0
            evictions.append(
                Eviction(
                    block=name,
                    action="dropped",
                    detail="removed entirely to fit the budget",
                    tokens_freed=original_tokens,
                )
            )
        else:
            block.content = _truncate_to_tokens(block.content, target)
            block.tokens = count_tokens(block.content)
            evictions.append(
                Eviction(
                    block=name,
                    action="truncated",
                    detail=f"shortened from {original_tokens} to {block.tokens} tokens",
                    tokens_freed=original_tokens - block.tokens,
                )
            )

    # Rebuild the system message from the surviving blocks.
    if messages:
        system_parts = [
            b.content for b in blocks if b.name != "history" and b.content.strip()
        ]
        messages[0] = {"role": "system", "content": "\n\n".join(system_parts)}

    fitted = CompiledPrompt(
        blocks=blocks,
        messages=messages,
        fingerprint=prompt.fingerprint,
        lore_matches=prompt.lore_matches,
    )

    report = FitReport(
        budget=budget,
        before=before,
        after=fitted.total_tokens,
        evictions=evictions,
        dropped_messages=dropped_messages,
    )

    return fitted, report
