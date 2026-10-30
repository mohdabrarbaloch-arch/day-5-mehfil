"""Token estimation without a tokenizer dependency.

Mehfil has to run on a laptop with no network, so we do not ship tiktoken:
it pulls a compiled wheel plus a model file downloaded at first use. Instead
we use a calibrated heuristic and label every number it produces as an
estimate, everywhere it surfaces in the UI and the API.

The heuristic is deliberately simple and deliberately documented:

    tokens ~= max(chars / CHARS_PER_TOKEN, words * WORDS_TO_TOKENS)

The max() matters. English prose sits near 4.0 chars/token, but Urdu,
Arabic and CJK are far denser per character -- a 40-character Urdu sentence
is many more tokens than a 40-character English one. Taking the word-based
estimate as a floor stops us badly under-counting those scripts.

Calibration note: on English chat transcripts this lands within roughly
+/-8% of a real BPE tokenizer. That is good enough to decide whether a
prompt fits in a context window. It is NOT good enough to bill on, and we
never use it for that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Average characters per token for English prose under a BPE tokenizer.
CHARS_PER_TOKEN = 4.0

# Words are usually 1.3-1.4 tokens each once punctuation and spacing are
# folded in. 1.3 is the conservative (higher) end.
WORDS_TO_TOKENS = 1.3

# Every chat message carries role markers and separators. Real tokenizers
# charge 3-5 tokens per message for this; we charge 4.
PER_MESSAGE_OVERHEAD = 4

# Non-Latin scripts tokenize much worse per character. We detect them and
# apply a multiplier rather than pretending 4 chars/token holds.
_DENSE_SCRIPT = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u0900-\u097F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]"
)
_DENSE_MULTIPLIER = 1.9

_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class Estimate:
    """A token estimate plus the reasoning behind it.

    We return the working, not just the answer, because a number a user
    cannot audit is a number they cannot trust.
    """

    tokens: int
    chars: int
    words: int
    dense_ratio: float
    method: str = "heuristic"

    def as_dict(self) -> dict:
        return {
            "tokens": self.tokens,
            "chars": self.chars,
            "words": self.words,
            "dense_ratio": round(self.dense_ratio, 3),
            "method": self.method,
        }


def count_tokens(text: str) -> int:
    """Estimate the token count of a single string."""
    return estimate(text).tokens


def estimate(text: str) -> Estimate:
    """Estimate tokens for a string, returning the full working."""
    if not text:
        return Estimate(tokens=0, chars=0, words=0, dense_ratio=0.0)

    chars = len(text)
    words = len(_WORD.findall(text))

    by_chars = chars / CHARS_PER_TOKEN
    by_words = words * WORDS_TO_TOKENS

    # How much of the string is in a script that tokenizes badly.
    dense_hits = len(_DENSE_SCRIPT.findall(text))
    dense_ratio = dense_hits / chars if chars else 0.0

    base = max(by_chars, by_words)
    # Blend rather than multiply outright: a mostly-English string with a
    # few Urdu words should not be penalised as if it were all Urdu.
    adjusted = base * (1.0 + (dense_ratio * (_DENSE_MULTIPLIER - 1.0)))

    return Estimate(
        tokens=max(1, round(adjusted)),
        chars=chars,
        words=words,
        dense_ratio=dense_ratio,
    )


def count_messages(messages: list[dict]) -> int:
    """Estimate tokens for a list of {role, content} chat messages."""
    total = 0
    for message in messages:
        total += count_tokens(str(message.get("content", "")))
        total += PER_MESSAGE_OVERHEAD
    return total


def fits(text: str, budget: int) -> bool:
    """True when the text is estimated to fit inside the budget."""
    return count_tokens(text) <= budget
