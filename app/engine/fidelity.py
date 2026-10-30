"""Style card extraction and fidelity scoring.

"Does this sound like my character?" is usually answered with a vibe. This
module answers it with a number, and — more usefully — with the specific
features that moved.

The style card is extracted from the character's own example dialogue, because
that is the only place a card author *demonstrates* the voice instead of
describing it. A description saying "she is terse and sarcastic" is a claim; an
example where she says "Fine. Whatever." is evidence.

Scoring is deterministic counting, not an LLM judge. An LLM judge would be a
second model with its own drift, costing money per message and giving a
different answer on a re-run. Counting is cheap, reproducible, and auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Feature tolerances: how far a reply may drift from the card before it counts
# as a miss. Expressed as a fraction of the card's own value, with a floor so
# tiny values do not produce absurd ratios.
TOLERANCE = 0.45
FLOOR = 0.02

CONTRACTIONS = re.compile(
    r"\b(?:don't|can't|won't|isn't|aren't|didn't|doesn't|"
    r"i'm|i've|i'll|i'd|you're|you've|you'll|it's|that's|"
    r"we're|they're|let's|there's|what's|who's)\b",
    re.I,
)
FIRST_PERSON = re.compile(r"\b(?:i|me|my|mine|myself)\b", re.I)
SECOND_PERSON = re.compile(r"\b(?:you|your|yours|yourself)\b", re.I)
EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\u2190-\u21ff\u2b00-\u2bff]"
)
WORD = re.compile(r"[A-Za-z']+")

# Common English words excluded from signature-word detection, so a character
# is not "signatured" on "the" and "and".
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "then",
    "than",
    "so",
    "as",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "with",
    "from",
    "by",
    "about",
    "into",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "am",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "their",
    "our",
    "not",
    "no",
    "yes",
    "just",
    "very",
    "really",
    "too",
    "also",
    "there",
    "here",
    "what",
    "which",
    "who",
    "when",
    "where",
    "why",
    "how",
    "all",
    "any",
    "some",
    "more",
    "most",
}


@dataclass
class StyleCard:
    """A measurable fingerprint of a character's voice."""

    avg_sentence_len: float = 0.0
    question_rate: float = 0.0
    exclaim_rate: float = 0.0
    ellipsis_rate: float = 0.0
    emoji_rate: float = 0.0
    contraction_rate: float = 0.0
    first_person_rate: float = 0.0
    second_person_rate: float = 0.0
    avg_word_len: float = 0.0
    caps_ratio: float = 0.0
    comma_rate: float = 0.0
    signature_words: list[str] = field(default_factory=list)
    sample_sentences: int = 0
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "avg_sentence_len": round(self.avg_sentence_len, 2),
            "question_rate": round(self.question_rate, 3),
            "exclaim_rate": round(self.exclaim_rate, 3),
            "ellipsis_rate": round(self.ellipsis_rate, 3),
            "emoji_rate": round(self.emoji_rate, 3),
            "contraction_rate": round(self.contraction_rate, 3),
            "first_person_rate": round(self.first_person_rate, 3),
            "second_person_rate": round(self.second_person_rate, 3),
            "avg_word_len": round(self.avg_word_len, 2),
            "caps_ratio": round(self.caps_ratio, 3),
            "comma_rate": round(self.comma_rate, 3),
            "signature_words": list(self.signature_words),
            "sample_sentences": self.sample_sentences,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class Finding:
    feature: str
    expected: float
    actual: float
    direction: str
    severity: str

    def to_dict(self) -> dict:
        return {
            "feature": self.feature,
            "expected": round(self.expected, 3),
            "actual": round(self.actual, 3),
            "direction": self.direction,
            "severity": self.severity,
        }


@dataclass
class FidelityResult:
    score: int
    label: str
    findings: list[Finding]
    features_checked: int

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "label": self.label,
            "findings": [f.to_dict() for f in self.findings],
            "features_checked": self.features_checked,
        }


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _strip_speaker_tags(text: str) -> str:
    """Remove {{char}}: / {{user}}: prefixes so they do not skew the counts."""
    return re.sub(r"\{\{\s*(?:char|user)\s*\}\}\s*:", " ", text, flags=re.I)


def extract_style(text: str) -> StyleCard:
    """Extract a :class:`StyleCard` from example dialogue."""
    cleaned = _strip_speaker_tags(text or "")
    sentences = _sentences(cleaned)
    words = WORD.findall(cleaned)
    word_count = max(1, len(words))
    char_count = max(1, len(cleaned))
    sentence_count = max(1, len(sentences))

    if not cleaned.strip():
        return StyleCard(confidence=0.0)

    question = sum(1 for s in sentences if s.endswith("?"))
    exclaim = sum(1 for s in sentences if s.endswith("!"))
    ellipsis = cleaned.count("...")
    emoji = len(EMOJI.findall(cleaned))
    contractions = len(CONTRACTIONS.findall(cleaned))
    first = len(FIRST_PERSON.findall(cleaned))
    second = len(SECOND_PERSON.findall(cleaned))
    commas = cleaned.count(",")
    upper = sum(1 for c in cleaned if c.isupper())
    letters = sum(1 for c in cleaned if c.isalpha())

    # Signature words: over-represented content words. We compare against a
    # simple frequency threshold rather than a corpus, because a card's example
    # dialogue is short and a corpus comparison would be noise.
    counts: dict[str, int] = {}
    for word in words:
        low = word.lower()
        if low in STOPWORDS or len(low) < 4:
            continue
        counts[low] = counts.get(low, 0) + 1
    threshold = max(2, int(word_count * 0.02))
    signature = sorted(
        (w for w, c in counts.items() if c >= threshold),
        key=lambda w: -counts[w],
    )[:8]

    # Confidence scales with sample size — 20+ sentences is a solid read.
    confidence = min(1.0, len(sentences) / 20.0)

    return StyleCard(
        avg_sentence_len=word_count / sentence_count,
        question_rate=question / sentence_count,
        exclaim_rate=exclaim / sentence_count,
        ellipsis_rate=ellipsis / sentence_count,
        emoji_rate=emoji * 100 / char_count,
        contraction_rate=contractions * 100 / word_count,
        first_person_rate=first * 100 / word_count,
        second_person_rate=second * 100 / word_count,
        avg_word_len=sum(len(w) for w in words) / word_count,
        caps_ratio=upper / max(1, letters),
        comma_rate=commas / sentence_count,
        signature_words=signature,
        sample_sentences=len(sentences),
        confidence=confidence,
    )


def _compare(
    feature: str,
    expected: float,
    actual: float,
    findings: list[Finding],
    weight: float = 1.0,
) -> float:
    """Compare one feature. Returns a 0..1 similarity for that feature."""
    band = max(FLOOR, abs(expected) * TOLERANCE)
    delta = actual - expected
    if abs(delta) <= band:
        return 1.0

    # Similarity decays linearly past the band, hitting 0 at 3x the band.
    overshoot = (abs(delta) - band) / (band * 2)
    similarity = max(0.0, 1.0 - overshoot)

    severity = "minor" if overshoot < 0.5 else "major"
    findings.append(
        Finding(
            feature=feature,
            expected=expected,
            actual=actual,
            direction="higher" if delta > 0 else "lower",
            severity=severity,
        )
    )
    return similarity * weight


def score(reply: str, card: StyleCard) -> FidelityResult:
    """Score ``reply`` against ``card``. Returns 0-100 plus findings."""
    if not reply.strip():
        return FidelityResult(score=0, label="off_voice", findings=[], features_checked=0)

    if card.confidence <= 0:
        # No example dialogue to compare against — we cannot honestly score.
        return FidelityResult(
            score=0,
            label="unscored",
            findings=[],
            features_checked=0,
        )

    actual = extract_style(reply)
    findings: list[Finding] = []

    # Weights reflect how much each feature contributes to "sounding like them".
    # Sentence rhythm and question habit dominate; emoji and caps are loud but
    # rarer, so they get less weight.
    checks = [
        ("avg_sentence_len", card.avg_sentence_len, actual.avg_sentence_len, 1.4),
        ("question_rate", card.question_rate, actual.question_rate, 1.2),
        ("exclaim_rate", card.exclaim_rate, actual.exclaim_rate, 0.8),
        ("ellipsis_rate", card.ellipsis_rate, actual.ellipsis_rate, 0.6),
        ("contraction_rate", card.contraction_rate, actual.contraction_rate, 1.0),
        ("first_person_rate", card.first_person_rate, actual.first_person_rate, 0.9),
        ("second_person_rate", card.second_person_rate, actual.second_person_rate, 0.9),
        ("avg_word_len", card.avg_word_len, actual.avg_word_len, 0.7),
        ("comma_rate", card.comma_rate, actual.comma_rate, 0.6),
        ("emoji_rate", card.emoji_rate, actual.emoji_rate, 0.5),
        ("caps_ratio", card.caps_ratio, actual.caps_ratio, 0.4),
    ]

    total_weight = sum(weight for _, _, _, weight in checks)
    earned = 0.0
    for feature, expected, observed, weight in checks:
        earned += _compare(feature, expected, observed, findings, weight)

    raw = earned / total_weight if total_weight else 0.0
    value = int(round(raw * 100))

    if value >= 75:
        label = "on_voice"
    elif value >= 50:
        label = "drifting"
    else:
        label = "off_voice"

    return FidelityResult(
        score=value,
        label=label,
        findings=findings,
        features_checked=len(checks),
    )
