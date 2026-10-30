"""Prompt compiler — turn a card + history into an inspectable bundle.

The whole point of this module is that the output is *not* a string. It is a
list of named blocks with token counts, so the UI can show exactly what the
model will receive, in what order, and what got dropped to make it fit.

Block order is fixed and intentional:

    system → persona → scenario → lore → examples → history → post_history → user

`post_history_instructions` sits *after* the history on purpose: it is the last
thing the model reads before the user's turn, which is where reinforcement
actually has effect. Putting it in the system block (as many tools do) buries
it under thousands of tokens of chat.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .cards import Card
from .lorebook import LoreResult
from .tokenizer import estimate_tokens, truncate_to_budget

DEFAULT_CONTEXT_BUDGET = 4096
DEFAULT_MAX_REPLY = 512

# Share of the budget each block may claim before history gets squeezed.
# These are ceilings, not reservations — unused space flows to history.
BLOCK_CEILINGS = {
    "system": 0.10,
    "persona": 0.30,
    "scenario": 0.10,
    "lore": 0.20,
    "examples": 0.25,
    "post_history": 0.08,
}


@dataclass
class Block:
    name: str
    content: str
    tokens: int
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not self.content.strip()


@dataclass
class FitReport:
    """What fit, what did not, and why. Surfaced in the UI and the API."""

    budget: int
    used: int
    reserved_for_reply: int
    history_messages_kept: int
    history_messages_dropped: int
    truncated_blocks: list[str] = field(default_factory=list)
    dropped_lore: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used - self.reserved_for_reply)

    @property
    def utilisation(self) -> float:
        if self.budget <= 0:
            return 0.0
        return round((self.used + self.reserved_for_reply) / self.budget, 3)

    def to_dict(self) -> dict:
        return {
            "budget": self.budget,
            "used": self.used,
            "reserved_for_reply": self.reserved_for_reply,
            "remaining": self.remaining,
            "utilisation": self.utilisation,
            "history_messages_kept": self.history_messages_kept,
            "history_messages_dropped": self.history_messages_dropped,
            "truncated_blocks": self.truncated_blocks,
            "dropped_lore": self.dropped_lore,
        }


@dataclass
class PromptBundle:
    blocks: list[Block]
    fit: FitReport
    fingerprint: str

    @property
    def messages(self) -> list[dict[str, str]]:
        """Flatten to a chat-completions style message list."""
        system_parts = [
            block.content
            for block in self.blocks
            if block.name in {"system", "persona", "scenario", "lore", "examples"}
            and not block.empty
        ]
        messages: list[dict[str, str]] = []
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        for block in self.blocks:
            if block.name == "history" and not block.empty:
                messages.extend(_split_history(block.content))
            elif block.name == "post_history" and not block.empty:
                messages.append({"role": "system", "content": block.content})
            elif block.name == "user" and not block.empty:
                messages.append({"role": "user", "content": block.content})
        return messages

    def render(self) -> str:
        """Human-readable rendering, used by the prompt inspector."""
        parts = []
        for block in self.blocks:
            if block.empty:
                continue
            parts.append(f"### {block.name.upper()} ({block.tokens} tok)\n{block.content}")
        return "\n\n".join(parts)


def _split_history(rendered: str) -> list[dict[str, str]]:
    """Split a rendered history block back into role-tagged messages."""
    messages: list[dict[str, str]] = []
    for chunk in rendered.split("\n\n"):
        if chunk.startswith("{{user}}:"):
            messages.append({"role": "user", "content": chunk[len("{{user}}:") :].strip()})
        elif chunk.startswith("{{char}}:"):
            messages.append({"role": "assistant", "content": chunk[len("{{char}}:") :].strip()})
    return messages


def _substitute(text: str, char_name: str, user_name: str) -> str:
    """Replace the {{char}} / {{user}} macros used by the card spec."""
    return (
        text.replace("{{char}}", char_name)
        .replace("{{Char}}", char_name)
        .replace("{{user}}", user_name)
        .replace("{{User}}", user_name)
    )


def _fit_block(name: str, content: str, budget: int, ceiling: float) -> tuple[Block, bool]:
    """Trim a block to its ceiling. Returns (block, was_truncated)."""
    if not content.strip():
        return Block(name=name, content="", tokens=0), False

    cap = max(1, int(budget * ceiling))
    tokens = estimate_tokens(content)
    if tokens <= cap:
        return Block(name=name, content=content, tokens=tokens), False

    trimmed = truncate_to_budget(content, cap)
    return (
        Block(name=name, content=trimmed, tokens=estimate_tokens(trimmed), truncated=True),
        True,
    )


def build(
    card: Card,
    history: list[dict[str, str]],
    user_text: str,
    lore: LoreResult | None = None,
    context_budget: int = DEFAULT_CONTEXT_BUDGET,
    max_reply_tokens: int = DEFAULT_MAX_REPLY,
    user_name: str = "User",
) -> PromptBundle:
    """Compile a card, history and new turn into a :class:`PromptBundle`."""
    char_name = card.name
    budget = max(256, context_budget)
    reserved = min(max_reply_tokens, budget // 2)
    working = budget - reserved

    truncated: list[str] = []
    blocks: list[Block] = []

    # 1. system — identity and hard rules
    system_text = card.system_prompt or (
        f"You are {char_name}. Stay in character at all times. "
        f"Never mention that you are an AI, a model, or a program. "
        f"Never break character to offer help as an assistant. "
        f"Write only {char_name}'s next reply — no narration of {user_name}'s actions."
    )
    block, cut = _fit_block(
        "system", _substitute(system_text, char_name, user_name), working, BLOCK_CEILINGS["system"]
    )
    blocks.append(block)
    if cut:
        truncated.append("system")

    # 2. persona — description + personality
    persona_parts = [part for part in (card.description, card.personality) if part.strip()]
    persona_text = "\n\n".join(persona_parts)
    block, cut = _fit_block(
        "persona",
        _substitute(persona_text, char_name, user_name),
        working,
        BLOCK_CEILINGS["persona"],
    )
    blocks.append(block)
    if cut:
        truncated.append("persona")

    # 3. scenario
    block, cut = _fit_block(
        "scenario",
        _substitute(card.scenario, char_name, user_name),
        working,
        BLOCK_CEILINGS["scenario"],
    )
    blocks.append(block)
    if cut:
        truncated.append("scenario")

    # 4. lore — already budget-capped by the lorebook
    lore_text = lore.text if lore else ""
    block, cut = _fit_block(
        "lore", _substitute(lore_text, char_name, user_name), working, BLOCK_CEILINGS["lore"]
    )
    blocks.append(block)
    if cut:
        truncated.append("lore")

    # 5. examples — few-shot voice demonstration
    block, cut = _fit_block(
        "examples",
        _substitute(card.mes_example, char_name, user_name),
        working,
        BLOCK_CEILINGS["examples"],
    )
    blocks.append(block)
    if cut:
        truncated.append("examples")

    # 6. history — gets whatever is left, newest first
    used_so_far = sum(b.tokens for b in blocks)
    history_budget = max(0, working - used_so_far)

    kept: list[str] = []
    kept_count = 0
    dropped_count = 0
    running = 0
    for message in reversed(history):
        role = message.get("role", "user")
        content = (message.get("content") or "").strip()
        if not content:
            continue
        speaker = "{{char}}" if role == "assistant" else "{{user}}"
        rendered = f"{speaker}: {content}"
        cost = estimate_tokens(rendered)
        if running + cost > history_budget:
            dropped_count += 1
            continue
        running += cost
        kept.append(rendered)
        kept_count += 1

    kept.reverse()
    history_text = "\n\n".join(kept)
    blocks.append(Block(name="history", content=history_text, tokens=running))

    # 7. post_history — last thing before the user's turn
    block, cut = _fit_block(
        "post_history",
        _substitute(card.post_history_instructions, char_name, user_name),
        working,
        BLOCK_CEILINGS["post_history"],
    )
    blocks.append(block)
    if cut:
        truncated.append("post_history")

    # 8. user — the actual turn, never truncated silently
    user_block = Block(
        name="user",
        content=_substitute(user_text, char_name, user_name),
        tokens=estimate_tokens(user_text),
    )
    blocks.append(user_block)

    total_used = sum(b.tokens for b in blocks)
    fit = FitReport(
        budget=budget,
        used=total_used,
        reserved_for_reply=reserved,
        history_messages_kept=kept_count,
        history_messages_dropped=dropped_count,
        truncated_blocks=truncated,
        dropped_lore=list(lore.dropped) if lore else [],
    )

    fingerprint = _fingerprint(blocks)
    return PromptBundle(blocks=blocks, fit=fit, fingerprint=fingerprint)


def _fingerprint(blocks: list[Block]) -> str:
    """SHA-256 over the rendered prompt.

    Two prompts with the same fingerprint are byte-identical, which makes
    "did my edit actually change what the model sees?" a one-line check.
    """
    digest = hashlib.sha256()
    for block in blocks:
        digest.update(block.name.encode())
        digest.update(b"\x00")
        digest.update(block.content.encode())
        digest.update(b"\x01")
    return digest.hexdigest()[:16]
