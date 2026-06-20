# Technical Specification: Game-Time in the Recap

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

A **pure, display-only** addition to the day-round recap — no AgentCore, no new node, no new LLM call site, no state field, no RNG, and **no change to any Day mechanic** (the round cap, vote allowance, and round loop are untouched). Each Day round maps to an in-world clock time that advances from morning toward midnight; the recap shows that time beside the day number so the Day visibly burns down toward Night and players feel the pressure to act before it ends. The clock is a pure function of the round number — a value already in state — so the whole feature is one small mapping helper plus a recap-template slot, with the one subtlety being **which round number each recap call site passes**.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component Breakdown (`src/graphia/nodes/day.py`, `src/graphia/prompts.py`)

- **`_round_clock(day_round: int) -> str`** *(new, pure, in `day.py`)* — maps the 1-based round to an in-world time:

  | `day_round` | returns |
  | ----------- | ------- |
  | 1 | `9 AM` |
  | 2 | `12 PM` |
  | 3 | `3 PM` |
  | 4 | `6 PM` |
  | 5 | `9 PM` |
  | ≥ 6 | `12 AM (midnight)` |
  | < 1 | clamp to `9 AM` |

  Implement as a 6-element sequence indexed by `max(1, min(day_round, 6)) - 1` (clamps both ends; never runs past midnight). Keep the `(midnight)` parenthetical so the "Night falls" reading is clear.
- **`render_day_round_recap(state, *, day_round: int)`** *(signature change)* — gains a **required keyword-only `day_round`** and passes it through `_round_clock`. Required (no default) deliberately, so every call site must consciously supply the correct round — the load-bearing decision below.
- **`DAY_ROUND_RECAP_TEMPLATE`** *(`prompts.py`)* — add a `{clock}` slot beside the day number, in the Moderator's neutral voice, e.g. `"Day {day}, {clock} — {law_clause} and {mafia_clause} remain. {votes_clause} {executed_clause}"`. The standings/votes/executed clauses stay byte-identical (satisfies "changes nothing else"). **Preserve a stable recap-detection marker** for the tests (keep the `" status:"` scaffold — e.g. `"Day {day}, {clock}, status: …"` — or update the test marker once; see §3).

### Logic: round-number sourcing (the crux — resolved against the routing)

`day_rounds` is a count of **completed** rounds: `day_open` sets it to 0; `_round_complete_update` bumps it to `new_rounds = rounds + 1` only on a round *wrap*; a mid-round vote initiation and both `resolve_vote` paths leave it untouched. So while *speaking in* round N, `day_rounds == N − 1`; after round N *completes*, `day_rounds == N`. `render_day_round_recap` must therefore be **told** the round, not read `day_rounds` (at the round-wrap call site the recap renders before `day_rounds` is committed).

| Call site | Round to pass | Why |
| --------- | ------------- | --- |
| `_round_complete_update` (continuing round) | `new_rounds` | the round that just completed (1-based); posted only for rounds 1–5 → `9 AM … 9 PM` |
| `day_close` (Day ends) | `ended_on_round = day_rounds if day_rounds >= DAY_MAX_ROUNDS else day_rounds + 1` | round-cap close: round 6 *completed* → `day_rounds == 6` → midnight. Early close (execution / vote-cap, mid-round): round in progress → `day_rounds + 1` (e.g. round 3 → 3 PM), never jumping to midnight |

`day_close` already reads `cycle` and `kill_log`; the only new logic there is reading `day_rounds` and the one-line `ended_on_round` derivation. The `_round_clock` clamp makes both terms safe (a vote during round 6 has `day_rounds == 5` → `5 + 1 == 6` → midnight; the speaking loop can't exceed 6). Net clock: round-loop recaps `9 AM (r1) … 9 PM (r5)`; a full-course closing recap `12 AM (r6)`; an early close shows the round it ended on.

No `state.py` change; no `graph.py` change; no mechanics change.

---

## 3. Impact and Risk Analysis

- **System dependencies:** rides spec 018's recap; composes with spec 019 (which feeds `_render_standings`, *without* the clock, into prompts — the clock stays recap-only). Independent of order; if 020 lands first, the `_render_standings` extraction in 019 is purely additive.
- **Primary risk — wrong offset on the closing recap.** Mitigated by the `ended_on_round` rule (distinguish round-cap vs early close) plus the `_round_clock` clamp. Covered by targeted tests for all three closes (round-cap → midnight; execution at round 3 → 3 PM; vote-cap mid-round → round-it-ended-on).
- **Existing-test impact:**
  - **Recap-detection marker:** `tests/test_slice_day_round_recap.py` uses `RECAP_MARKER = " status:"`. Keep the `" status:"` scaffold in the template so every recap-count assertion stays green; otherwise update `RECAP_MARKER` once (a single change fixes all count-based tests, which all route through `_is_recap`).
  - **Literal recap assertion:** `test_render_recap_no_execution_states_day_counts_votes_and_no_exec` asserts `"Day 2 status:"`. If the clock lands between `"Day 2"` and `"status:"`, split it into `"Day 2" in text` and `" status:" in text` (or assert the new scaffold).
  - **Signature change:** every `render_day_round_recap(state)` call in `tests/test_slice_day_round_recap.py` (§1/§3/§5/§6) must pass `day_round=`. With 019 factoring `_render_standings`, the §1 standings-clause tests move to testing `_render_standings` directly (no round needed), so only the genuinely clock-asserting recap tests carry a `day_round=`.
- **Determinism:** pure integer→string map; no RNG, no LLM, **no wall-clock** (the time is in-world, derived from the round). The byte-equal dual-mode smoke stays green — the recap text is identical in both modes; seeded fakes are unaffected.
- **Display-only guarantee:** `DAY_MAX_ROUNDS` and all Day routing are unchanged; the clock only *reads* the round the Day is already on.

---

## 4. Testing Strategy

Structural (architecture §6); assert the in-world time *token* for a round, never verbatim prose. `safe_llm` unchanged; no new fixtures/RNG.

- **Pure `_round_clock`:** parametrize rounds 1–6 → their tokens; out-of-range clamps (`0`/`<1` → `9 AM`; `>6` → midnight). Pin the exact midnight literal the code emits.
- **`render_day_round_recap` clock-by-round:** over hand-built state, rounds 1–6 → their tokens, asserting the standings clauses are unchanged; round 6 → midnight.
- **Caller-passes-correct-round (load-bearing):**
  - `_round_complete_update` round-wrap (monkeypatch `_shuffle_order` to avoid RNG): wrapping round 1 → recap clock `9 AM`; round 2 → `12 PM` (catches an off-by-one between `rounds` and `new_rounds`).
  - `day_close` early-end: a Day ending at round 3 → closing recap shows `3 PM` and **not** midnight; a Day reaching `DAY_MAX_ROUNDS` → closing recap shows midnight.
- **End-to-end progression (extend the existing six-round drive):** assert the recap clocks read `9 AM → 12 PM → 3 PM → 6 PM → 9 PM → 12 AM` in message order.
- **Dual-mode smoke** stays byte-equal (verified — deterministic recap text, faked LLM, in-world clock).
