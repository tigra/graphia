---
spec: 008-same-round-message-visibility
spec_title: Same-Round Message Visibility
introduced_on: 2026-06-09
---

# Concepts introduced in this increment

## Visibility (Day phase)

- **Asymmetric context: bounded AI, unbounded human** (`asymmetric-context-bounded-ai-unbounded-human`) — The same "see the round" guarantee is delivered by two different mechanisms: the AI's view is a *windowed* render of the last N messages, while the human's view is the append-only on-screen log that is never truncated.
- **One context chokepoint feeds speak and vote** (`shared-context-chokepoint-speak-and-vote`) — `_render_context` is the single recent-discussion feed for *both* the AI day-speak prompt and the AI vote prompt, so widening one module constant widens both AI views at once.
- **Window sized to cover a full round** (`window-sized-to-cover-a-round`) — The window size (30) is chosen against a domain invariant: a full round (≤7 speeches + the day-open announcement + any vote announcements) must fit, with headroom for larger future lineups, while keeping the prompt bounded.
- **Interrupt payload omits discussion** (`interrupt-payload-omits-discussion`) — The human's `day_turn` interrupt payload deliberately carries no discussion text; the screen *is* the discussion channel, which is why the human side of this guarantee needed zero production code.

## Testing

- **Constant-floor guard assertion** (`constant-floor-guard-assertion`) — A pure unit test ties a magic configuration constant to the domain floor it must clear (`_CONTEXT_WINDOW >= FULL_ROUND_MESSAGES`), so a future shrink below a round's worth of messages fails loudly instead of silently dropping the round's earliest speaker.
