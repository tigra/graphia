# Technical Specification: AI Collusion Awareness (Copycat Messages Are Suspicious)

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

This is a **prompt-only** change. The AI's Day speaking behaviour is governed by one system prompt, `DAY_SPEAK_SYSTEM` in `src/graphia/prompts.py`, consumed solely by the day-speak path (`_ai_day_action` in `src/graphia/nodes/day.py`, which builds `SystemMessage(content=DAY_SPEAK_SYSTEM)`). The entire implementation is to append **one light awareness sentence** to that string telling the AI that identical or near-identical messages from different players can hint at collusion.

No logic, schema, graph, UI, or test change. The signal is *interpretive only* — the AI already **sees** other players' recent messages (delivered by spec 008's widened context window), so it has the raw material; this spec just names the tell. Reaction is left emergent (functional-spec §2.2): the line informs, it does not instruct.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The one prompt change — `src/graphia/prompts.py`

- Append a single sentence to the `DAY_SPEAK_SYSTEM` string. **Shipped wording (revised 2026-06-10):**

  > Say something new on your turn — don't repeat or echo a point another player has already made.

  The original wording — *"Identical or near-identical messages from different players can hint at collusion."* — backfired into a repetition spiral on Nova; the experiment ([`repetition-experiment-design.md`](./repetition-experiment-design.md) §13) selected the anti-parroting phrasing above (name-masked near-dup 0.57 → 0.15, below the pre-spec baseline). Still a single observational/imperative line in the speak prompt only.

- It is added as an **observation**, not an imperative — no "call it out", no "accuse them", no fixed phrasing — satisfying functional-spec §2.1 (light awareness) and §2.2 (emergent behaviour). It joins the existing "nervous, observant villager" framing already in the prompt.

### 2.2 Explicit non-changes

- **`AI_VOTE_SYSTEM` is untouched.** The awareness applies to **spoken turns only**, not the Yes/No execution ballot (functional-spec §2.1 third criterion, §3 Out-of-Scope). `_ai_ballot` keeps building its prompt from the unchanged `AI_VOTE_SYSTEM`.
- **No mechanical detection.** No code compares messages for similarity; the AI judges similarity itself (functional-spec §2.2, §3). `_render_context` and the message channels are unchanged.
- **No `DAY_SPEAK_USER_TEMPLATE` change.** The per-turn user template (roster + recent discussion) already supplies the messages the AI inspects; only the system prompt's *guidance* changes.

---

## 3. Impact and Risk Analysis

- **System Dependencies:** `DAY_SPEAK_SYSTEM` is imported only by `src/graphia/nodes/day.py` and used at exactly one call site (the day-speak `SystemMessage`). Changing the string affects only AI spoken-turn generation. No other module reads it.
- **Determinism (architecture §6):** unaffected. AI dialogue is already accepted as non-reproducible; this nudge changes *what* the model is told, not the determinism posture. No RNG, no seed, no replay surface is touched.
- **Potential Risks & Mitigations:**
  - *Over-steering → robotic call-outs.* If the line were phrased as a command ("accuse copycats"), every AI might parrot the same accusation. *Mitigation:* the wording is a flat observation with no mandated reaction (§2.2); behaviour stays emergent.
  - *Scope creep into the vote prompt.* Easy to "also" add it to `AI_VOTE_SYSTEM`. *Mitigation:* §2.2 above makes the vote-prompt exclusion explicit; it is an acceptance criterion, not an oversight.
  - *Token cost.* One sentence; negligible.

---

## 4. Testing Strategy

- **No automated test** (functional-spec §2.3, §3 Out-of-Scope). The behaviour is emergent and AI output is non-deterministic (architecture §6), so asserting a specific reaction would be either flaky or fake. Verification is **play-testing only**: run `uv run python -m graphia`, observe Day discussion, and confirm AI villagers occasionally treat near-identical messages with suspicion without forced or repetitive phrasing.
- **Regression guard (existing suite):** the standard `uv run pytest -q` must stay green — it confirms the prompt edit didn't break import/usage of `DAY_SPEAK_SYSTEM` or any day-node wiring. No new test is added.
