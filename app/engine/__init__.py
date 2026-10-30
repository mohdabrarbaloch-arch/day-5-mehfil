"""Mehfil's prompt engine.

Everything in this package is pure: no database, no network, no FastAPI.
That is deliberate -- it means the whole engine is testable without a
server, and the same code runs in the API and in the CLI.
"""

from .cards import CardError, CharacterCard, parse_card, validate_card
from .compiler import CompiledPrompt, PromptBlock, compile_prompt, substitute
from .context import Eviction, FitReport, fit_prompt
from .lorebook import LoreEntry, LoreMatch, parse_entries, render, scan
from .tokenizer import count_messages, count_tokens, estimate

__all__ = [
    "CardError",
    "CharacterCard",
    "CompiledPrompt",
    "Eviction",
    "FitReport",
    "LoreEntry",
    "LoreMatch",
    "PromptBlock",
    "compile_prompt",
    "count_messages",
    "count_tokens",
    "estimate",
    "fit_prompt",
    "parse_card",
    "parse_entries",
    "render",
    "scan",
    "substitute",
    "validate_card",
]
