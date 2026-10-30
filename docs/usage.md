# Usage

## The five-minute tour

1. **Register** at `/`. You get a JWT stored in `localStorage`.
2. **Create a character.** The form has the usual fields plus a lorebook editor.
3. **Open the style card.** It's measured from your `mes_example` — the more
   example dialogue you give it, the higher the confidence.
4. **Preview the prompt.** This is the inspector: every block, its token count,
   and what got dropped.
5. **Chat.** Each reply comes back with a fidelity score.

## Writing a character that holds up

The single biggest factor is `mes_example`. It's not decoration — it's the
few-shot demonstration the model pattern-matches against, and it's what the
style card is measured from.

**Bad example dialogue:**

```
{{user}}: Hi
{{char}}: Hello! How can I help you today?
```

That teaches the model to be a customer service bot. It also gives the style
card almost nothing to measure — you'll see `confidence: 0.1`.

**Good example dialogue:**

```
{{user}}: Are you okay?
{{char}}: Fine. Why wouldn't I be.
{{user}}: You're crying.
{{char}}: It's the rain. Obviously.
{{user}}: It's not raining.
{{char}}: Then it's the kettle. Don't ask stupid questions.
```

Six short lines, and the style card now knows: sentences average 2.5 words,
questions come back at 17%, contractions are rare, and the character deflects
with sarcasm. That's a voice you can score against.

**Aim for 8–15 exchanges.** Below that, confidence stays low and the fidelity
score is noise.

## The prompt inspector

Open it from any chat. It shows:

- **Blocks in order** — `system`, `persona`, `scenario`, `lore`, `examples`,
  `history`, `post_history`, `user`
- **Token count per block** and the total against your budget
- **Truncated blocks** — which ones got cut, and by how much
- **Dropped lore** — entries that matched but didn't fit
- **Fingerprint** — a hash of the block contents

The fingerprint is the useful part. If a character starts behaving differently
and nobody edited the card, compare fingerprints. If they match, the prompt
didn't change and the problem is the model or the sampling. If they differ,
something edited the card — and you can diff the blocks to find it.

## Reading the fidelity score

Each reply is scored against the character's style card:

| Verdict | Meaning |
|---|---|
| **on-voice** | Within tolerance on most features |
| **drifting** | Two or more features moved noticeably |
| **off-voice** | Several features far outside range |

The findings are plain English, e.g.:

> *"Sentences are 2.4× longer than this character's usual 2.5 words."*
> *"Question-back rate dropped from 17% to 0% — this character usually answers a question with a question."*

**A low score isn't always wrong.** A character can legitimately get serious in
a serious moment. The score is a signal to look, not a verdict to obey.

## Lorebook

Entries inject only when their keys appear in the conversation.

| Field | What it does |
|---|---|
| `keys` | Words that trigger the entry. **Any** match fires it. |
| `content` | What gets injected |
| `priority` | Higher wins when the budget is tight |
| `constant` | Always inject, no keys needed |
| `case_sensitive` | Off by default |
| `enabled` | Off means never inject |

**Matching is word-boundary aware.** A key of `art` will not fire on `start`.

**Recursion is bounded.** An injected entry can trigger another entry, but the
chain stops at a fixed depth so a self-referencing pair can't loop.

**Budget is enforced.** If entries don't fit, the lowest-priority ones are
dropped and reported in the inspector — not silently truncated.

## Importing cards

`POST /api/characters/import` accepts both formats:

```json
{ "card": { "spec": "chara_card_v2", "spec_version": "2.0", "data": { ... } } }
```

or a flat V1 card:

```json
{ "card": { "name": "...", "description": "...", "first_mes": "..." } }
```

The response includes `warnings` — things that parsed but won't work well, like
a lore entry with no keys.

## Exporting

`GET /api/characters/{id}/export` returns a spec-shaped V2 card. Round-trips
cleanly: export, import elsewhere, import back, same character.

## Keyboard

| Key | Action |
|---|---|
| `Enter` | Send |
| `Shift+Enter` | Newline |
| `Esc` | Close dialog |
