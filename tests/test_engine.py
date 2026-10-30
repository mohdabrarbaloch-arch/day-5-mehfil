"""Engine tests — tokenizer, cards, lorebook, compiler, fidelity."""

from __future__ import annotations

import pytest

from app.engine import (
    Card,
    CardError,
    LoreEntryData,
    build,
    estimate_tokens,
    extract_style,
    parse_card,
    scan,
    score,
    truncate_to_budget,
    validate_card,
)

# ───────────────────────────── tokenizer ─────────────────────────────


def test_estimate_tokens_empty_is_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("hello there")
    long = estimate_tokens("hello there " * 50)
    assert long > short > 0


def test_estimate_tokens_never_undercounts_words():
    # 100 words should never estimate below 100 tokens.
    text = " ".join(["word"] * 100)
    assert estimate_tokens(text) >= 100


def test_truncate_to_budget_respects_budget():
    text = "alpha beta gamma delta epsilon zeta eta theta " * 20
    trimmed = truncate_to_budget(text, 20)
    assert estimate_tokens(trimmed) <= 20
    assert len(trimmed) < len(text)


def test_truncate_to_budget_noop_when_fits():
    assert truncate_to_budget("short text", 500) == "short text"


# ───────────────────────────── cards ─────────────────────────────


def test_parse_v2_card():
    card = parse_card(
        {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "Zainab",
                "description": "Chai stall owner.",
                "first_mes": "You again.",
                "tags": ["sarcastic"],
            },
        }
    )
    assert card.name == "Zainab"
    assert card.tags == ["sarcastic"]


def test_parse_v1_flat_card_upgrades():
    card = parse_card({"name": "Old Card", "description": "From the V1 era."})
    assert card.name == "Old Card"
    assert card.description == "From the V1 era."


def test_parse_card_requires_name():
    with pytest.raises(CardError):
        parse_card({"description": "no name here"})


def test_parse_card_requires_some_content():
    with pytest.raises(CardError):
        parse_card({"name": "Empty"})


def test_parse_card_rejects_non_object():
    with pytest.raises(CardError):
        parse_card(["not", "a", "card"])


def test_card_roundtrip_preserves_fields():
    original = parse_card(
        {
            "data": {
                "name": "Roundtrip",
                "description": "Desc",
                "personality": "Person",
                "mes_example": "{{char}}: hi",
                "tags": ["a", "b"],
            }
        }
    )
    again = parse_card(original.to_v2())
    assert again.name == original.name
    assert again.tags == original.tags
    assert again.mes_example == original.mes_example


def test_validate_card_warns_without_examples():
    card = Card(name="Bare", description="Only a description.")
    warnings = validate_card(card)
    assert any("example dialogue" in w.lower() for w in warnings)


def test_validate_card_clean_when_complete():
    card = Card(
        name="Complete",
        description="Has everything.",
        first_mes="Hello.",
        mes_example="{{char}}: Hello there, friend.",
    )
    assert validate_card(card) == []


# ───────────────────────────── lorebook ─────────────────────────────


def _lore(keys, content, priority=10, constant=False, enabled=True):
    return LoreEntryData(
        keys=keys, content=content, priority=priority, constant=constant, enabled=enabled
    )


def test_lore_fires_on_any_key():
    entries = [_lore(["karachi", "rain"], "It rains a lot in Karachi in July.")]
    # Any single key is enough — AND semantics almost never fire in practice.
    assert len(scan(entries, "I am in Karachi").entries) == 1
    assert len(scan(entries, "Karachi rain again").entries) == 1
    # No key present -> no match.
    assert scan(entries, "I am in Lahore").entries == []


def test_lore_constant_always_injects():
    entries = [_lore([], "The year is 1998.", constant=True)]
    assert len(scan(entries, "anything at all").entries) == 1


def test_lore_disabled_never_injects():
    entries = [_lore(["chai"], "Chai lore.", enabled=False)]
    assert scan(entries, "chai chai chai").entries == []


def test_lore_respects_budget():
    entries = [
        _lore(["x"], "word " * 400, priority=50),
        _lore(["x"], "short entry", priority=10),
    ]
    result = scan(entries, "x", budget=50)
    assert result.tokens_used <= 50
    assert result.dropped  # the big one was dropped


def test_lore_priority_orders_injection():
    entries = [
        _lore(["x"], "low priority", priority=1),
        _lore(["x"], "high priority", priority=90),
    ]
    result = scan(entries, "x", budget=500)
    assert result.entries[0].content == "high priority"


def test_lore_word_boundary_prevents_false_match():
    entries = [_lore(["art"], "Art lore.")]
    # "start" contains "art" but must not trigger.
    assert scan(entries, "let us start").entries == []


# ───────────────────────────── compiler ─────────────────────────────


def _card(**overrides):
    base = {
        "name": "Zainab",
        "description": "Chai stall owner in Karachi.",
        "personality": "Terse and sarcastic.",
        "scenario": "A rainy night.",
        "first_mes": "You again.",
        "mes_example": "{{user}}: Hi\n{{char}}: Fine.",
    }
    base.update(overrides)
    return Card(**base)


def test_compiler_block_order_is_fixed():
    bundle = build(_card(), history=[], user_text="hello")
    names = [b.name for b in bundle.blocks]
    assert names == [
        "system",
        "persona",
        "scenario",
        "lore",
        "examples",
        "history",
        "post_history",
        "user",
    ]


def test_compiler_substitutes_macros():
    bundle = build(_card(), history=[], user_text="hi {{char}}")
    user_block = next(b for b in bundle.blocks if b.name == "user")
    assert "{{char}}" not in user_block.content
    assert "Zainab" in user_block.content


def test_compiler_fingerprint_is_stable():
    first = build(_card(), history=[], user_text="hello")
    second = build(_card(), history=[], user_text="hello")
    assert first.fingerprint == second.fingerprint


def test_compiler_fingerprint_changes_with_content():
    first = build(_card(), history=[], user_text="hello")
    second = build(_card(), history=[], user_text="goodbye")
    assert first.fingerprint != second.fingerprint


def test_compiler_drops_oldest_history_first():
    history = [{"role": "user", "content": f"message number {i} " * 20} for i in range(30)]
    bundle = build(_card(), history=history, user_text="now", context_budget=600)
    assert bundle.fit.history_messages_dropped > 0
    assert bundle.fit.history_messages_kept < 30


def test_compiler_reserves_reply_budget():
    bundle = build(_card(), history=[], user_text="hi", context_budget=1000, max_reply_tokens=200)
    assert bundle.fit.reserved_for_reply == 200
    assert bundle.fit.used + bundle.fit.reserved_for_reply <= 1000


def test_compiler_messages_have_roles():
    bundle = build(
        _card(),
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}],
        user_text="how are you",
    )
    messages = bundle.messages
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "how are you"


# ───────────────────────────── fidelity ─────────────────────────────


def test_extract_style_counts_questions():
    card = extract_style("Are you okay? Why? What happened? Fine.")
    assert card.question_rate > 0.5


def test_extract_style_empty_has_zero_confidence():
    assert extract_style("").confidence == 0.0


def test_extract_style_confidence_grows_with_sample():
    small = extract_style("One sentence here.")
    large = extract_style(" ".join(["A sentence of reasonable length."] * 25))
    assert large.confidence > small.confidence


def test_score_identical_text_is_on_voice():
    text = (
        "Fine. Why wouldn't I be. It's the rain. Obviously. "
        "Don't ask stupid questions. You always ask stupid questions."
    )
    card = extract_style(text)
    result = score(text, card)
    assert result.score >= 90
    assert result.label == "on_voice"


def test_score_very_different_text_is_off_voice():
    card = extract_style("Fine. No. Whatever. Don't care.")
    reply = (
        "I would be delighted to assist you with that request! "
        "Please allow me to explain the various options available, "
        "as there are several considerations we should review together."
    )
    result = score(reply, card)
    assert result.score < 60


def test_score_without_examples_is_unscored():
    result = score("anything", extract_style(""))
    assert result.label == "unscored"
    assert result.score == 0


def test_score_reports_findings_with_direction():
    card = extract_style("Fine. No. Whatever.")
    reply = "Absolutely! I am so incredibly excited to help you today!!"
    result = score(reply, card)
    assert result.findings
    assert all(f.direction in {"higher", "lower"} for f in result.findings)


def test_score_is_deterministic():
    card = extract_style("Fine. No. Whatever. Don't care.")
    reply = "Sure, I can help with that."
    assert score(reply, card).score == score(reply, card).score
