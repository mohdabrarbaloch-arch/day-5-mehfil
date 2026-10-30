"""Prompt compiler: card + lore + history -> one inspectable prompt.

The whole point of Mehfil is that this step is visible. Most character
platforms assemble a prompt somewhere inside a library and never show it to
you, which makes "why did the character change?" unanswerable.

Here the prompt is a list of named blocks, each with its own token count, in
a fixed order. The order is fixed on purpose: models are sensitive to where
instructions sit, so a prompt that reshuffles between turns produces a
character that drifts for no visible reason.

Block order (and why):

    1. system        - the card's system_prompt, or a generated default
    2. persona       - name, description, personality
    3. scenario      - the setting
    4. lore          - triggered lorebook entries
    5. examples      - mes_example, the strongest style signal
    6. history       - the conversation so far
    7. post_history  - the card's post_history_instructions, last word

post_history goes last because it is the instruction the model reads most
recently, and cards use it for "stay in character" style nudges.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .cards import CharacterCard
from .lorebook import LoreMatch, render
from .tokenizer import count_tokens

# The order is load-bearing. Do not reorder without a reason you can defend.
BLOCK_ORDER = (
    "system",
    "persona",
    "scenario",
    "lore",
    "examples",
    "history",
    "post_history",
)

DEFAULT_SYSTEM = (
    "You are {{char}}. Stay in character at all times. Write in the first person, "
    "as {{char}}, and never speak or act for {{user}}. Keep replies to a few "
    "sentences unless the scene calls for more."
)


@dataclass
class PromptBlock:
    """One named section of the compiled prompt."""

    name: str
    content: str
    tokens: int
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "content": self.content,
            "tokens": self.tokens,
            "source": self.source,
        }


@dataclass
class CompiledPrompt:
    """A compiled prompt, ready to send and ready to inspect."""

    blocks: list[PromptBlock] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    fingerprint: str = ""
    lore_matches: list[LoreMatch] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(block.tokens for block in self.blocks)

    @property
    def system_text(self) -> str:
        """Everything that goes in the system message."""
        parts = [b.content for b in self.blocks if b.name != "history" and b.content.strip()]
        return "\n\n".join(parts)

    def as_dict(self) -> dict:
        return {
            "blocks": [b.as_dict() for b in self.blocks],
            "total_tokens": self.total_tokens,
            "fingerprint": self.fingerprint,
            "lore_matches": [m.as_dict() for m in self.lore_matches],
            "messages": self.messages,
        }


def substitute(text: str, char_name: str, user_name: str) -> str:
    """Replace {{char}} and {{user}} in a single pass.

    One pass, not a loop: a character literally named "{{user}}" would
    otherwise cause a second round of substitution and mangle the prompt.
    """
    if not text:
        return ""
    return text.replace("{{char}}", char_name).replace("{{user}}", user_name)


def _fingerprint(blocks: list[PromptBlock]) -> str:
    """A short, stable hash of the prompt's structure and content."""
    digest = hashlib.sha256()
    for block in blocks:
        digest.update(block.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(block.content.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


def compile_prompt(
    card: CharacterCard,
    history: list[dict] | None = None,
    *,
    user_name: str = "User",
    lore_matches: list[LoreMatch] | None = None,
    extra_system: str = "",
) -> CompiledPrompt:
    """Assemble the prompt for one turn.

    `history` is a list of {role, content} dicts, oldest first, and should
    NOT include the current user message -- that is appended by the caller
    after fitting, so the budget check sees the real final prompt.
    """
    char_name = card.name or "Character"
    history = history or []
    lore_matches = lore_matches or []

    blocks: list[PromptBlock] = []

    # 1. system
    system_source = card.system_prompt.strip() or DEFAULT_SYSTEM
    system_text = substitute(system_source, char_name, user_name)
    if extra_system.strip():
        system_text = f"{system_text}\n\n{substitute(extra_system, char_name, user_name)}"
    blocks.append(
        PromptBlock(
            name="system",
            content=system_text,
            tokens=count_tokens(system_text),
            source="card.system_prompt" if card.system_prompt.strip() else "default",
        )
    )

    # 2. persona
    persona_parts: list[str] = []
    if card.description.strip():
        persona_parts.append(substitute(card.description.strip(), char_name, user_name))
    if card.personality.strip():
        persona_parts.append(
            f"Personality: {substitute(card.personality.strip(), char_name, user_name)}"
        )
    persona_text = "\n\n".join(persona_parts)
    blocks.append(
        PromptBlock(
            name="persona",
            content=persona_text,
            tokens=count_tokens(persona_text),
            source="card.description+personality",
        )
    )

    # 3. scenario
    scenario_text = substitute(card.scenario.strip(), char_name, user_name)
    blocks.append(
        PromptBlock(
            name="scenario",
            content=scenario_text,
            tokens=count_tokens(scenario_text),
            source="card.scenario",
        )
    )

    # 4. lore
    lore_text = substitute(render(lore_matches), char_name, user_name)
    blocks.append(
        PromptBlock(
            name="lore",
            content=lore_text,
            tokens=count_tokens(lore_text),
            source=f"{len(lore_matches)} entr{'y' if len(lore_matches) == 1 else 'ies'}",
        )
    )

    # 5. examples
    examples_text = substitute(card.mes_example.strip(), char_name, user_name)
    blocks.append(
        PromptBlock(
            name="examples",
            content=examples_text,
            tokens=count_tokens(examples_text),
            source="card.mes_example",
        )
    )

    # 6. history -- counted but not merged into the system message.
    history_tokens = 0
    for message in history:
        history_tokens += count_tokens(str(message.get("content", ""))) + 4
    blocks.append(
        PromptBlock(
            name="history",
            content=f"{len(history)} message(s)",
            tokens=history_tokens,
            source="conversation",
        )
    )

    # 7. post_history
    post_text = substitute(card.post_history_instructions.strip(), char_name, user_name)
    blocks.append(
        PromptBlock(
            name="post_history",
            content=post_text,
            tokens=count_tokens(post_text),
            source="card.post_history_instructions",
        )
    )

    messages: list[dict] = [{"role": "system", "content": CompiledPrompt(
        blocks=blocks
    ).system_text}]
    messages.extend(history)

    return CompiledPrompt(
        blocks=blocks,
        messages=messages,
        fingerprint=_fingerprint(blocks),
        lore_matches=lore_matches,
    )
