"""Mehfil engine — character cards, prompt compilation, generation, fidelity.

The engine is deliberately free of FastAPI, SQLAlchemy and any I/O beyond the
model backend. That means it can be tested in isolation, reused in a CLI, and
reasoned about without a database.
"""

from .cards import Card, CardError, LoreEntryData, parse_card, validate_card
from .compiler import (
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_MAX_REPLY,
    Block,
    FitReport,
    PromptBundle,
    build,
)
from .fidelity import FidelityResult, StyleCard, extract_style, score
from .generate import BackendError, Generation, active_backend, generate, stream
from .lorebook import LoreResult, scan
from .tokenizer import estimate_messages, estimate_tokens, truncate_to_budget

__all__ = [
    "Card",
    "CardError",
    "LoreEntryData",
    "parse_card",
    "validate_card",
    "Block",
    "FitReport",
    "PromptBundle",
    "build",
    "DEFAULT_CONTEXT_BUDGET",
    "DEFAULT_MAX_REPLY",
    "StyleCard",
    "FidelityResult",
    "extract_style",
    "score",
    "Generation",
    "BackendError",
    "active_backend",
    "generate",
    "stream",
    "LoreResult",
    "scan",
    "estimate_tokens",
    "estimate_messages",
    "truncate_to_budget",
]
