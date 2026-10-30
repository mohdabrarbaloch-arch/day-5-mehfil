"""Token estimation.

We deliberately do not ship a real tokenizer. Every model family tokenizes
differently — running tiktoken over a Llama prompt would give a confident,
wrong number, which is worse than an honest estimate.

So: a calibrated heuristic, and we say it is a heuristic everywhere it surfaces.
The estimate is intentionally *conservative* (it rounds up), because
under-counting a prompt means the backend silently truncates it and the
character loses context without anyone noticing.
"""

from __future__ import annotations

import math
import re

# Roughly: English prose runs ~4 characters per token, and ~1.33 tokens per
# whitespace-delimited word. We take the larger of the two so we never
# under-estimate, then add a small per-message overhead for role framing.
_CHARS_PER_TOKEN = 4.0
_TOKENS_PER_WORD = 1.33
_MESSAGE_OVERHEAD = 4

_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def estimate_tokens(text: str | None) -> int:
    """Estimate the token count of a string.

    Returns 0 for empty/None input. Never returns a negative number.
    """
    if not text:
        return 0

    chars = len(text)
    words = len(text.split())

    by_chars = chars / _CHARS_PER_TOKEN
    by_words = words * _TOKENS_PER_WORD

    # CJK text packs far more meaning per character; the char heuristic badly
    # under-counts it, so weight those characters up.
    cjk_chars = len(_CJK.findall(text))
    if cjk_chars:
        by_chars += cjk_chars * 0.6

    return max(1, math.ceil(max(by_chars, by_words)))


def estimate_messages(messages: list[dict[str, str]]) -> int:
    """Estimate tokens for a list of ``{"role": ..., "content": ...}`` dicts."""
    total = 0
    for message in messages:
        total += estimate_tokens(message.get("content", "")) + _MESSAGE_OVERHEAD
    return total


def fits(text: str, budget: int) -> bool:
    """True when ``text`` is estimated to fit inside ``budget`` tokens."""
    return estimate_tokens(text) <= budget


def truncate_to_budget(text: str, budget: int) -> str:
    """Trim ``text`` from the end until it fits ``budget`` tokens.

    Trims on a word boundary where possible so we do not cut mid-word.
    """
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text

    # Binary search on character length — estimate_tokens is monotonic in length.
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1

    cut = text[:low]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip()
