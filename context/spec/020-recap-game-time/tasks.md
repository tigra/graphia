# Tasks: Game-Time in the Recap (Spec 020)

One vertical slice — add an in-world clock to the day-round recap that advances
from morning toward midnight. Pure, display-only; no mechanics/state/RNG change.

> **Implementation order:** 019 and 020 both refactor `render_day_round_recap`,
> so they must NOT be implemented concurrently. Do **019 first**, then **020**
> (which adds the clock to the thin composer 019 leaves behind, plus the
> `day_round` parameter). The crux is the round-number each call site passes —
> see the technical considerations.

Functional spec: `./functional-spec.md` · Technical considerations: `./technical-considerations.md`

---

- [x] **Slice 1: The recap shows an in-world clock that advances each round (9 AM → midnight)**
  - [x] Add a pure `_round_clock(day_round) -> str` mapping the 1-based round to a time (1 → 9 AM, 2 → 12 PM, 3 → 3 PM, 4 → 6 PM, 5 → 9 PM, ≥6 → 12 AM/midnight; clamp both ends). Add a `{clock}` slot to `DAY_ROUND_RECAP_TEMPLATE` beside the day number, keeping the `" status:"` detection marker stable and the standings/votes/executed clauses byte-unchanged. Make `render_day_round_recap` take a required keyword-only `day_round`. Wire the two call sites: `_round_complete_update` passes `new_rounds` (the just-completed round); `day_close` passes `ended_on_round = day_rounds if day_rounds >= DAY_MAX_ROUNDS else day_rounds + 1` (round-cap close → midnight; early execution/vote-cap close → the round it stopped on). Update the existing recap-test call sites for the new `day_round` arg and split the `"Day 2 status:"` literal assertion, keeping the suite green. **[Agent: langgraph-agentic]**
  - [x] Add tests: pure `_round_clock` mapping (rounds 1–6) + out-of-range clamps (`<1` → 9 AM, `>6` → midnight); `render_day_round_recap` shows the right clock for a given round with the standings clauses unchanged; round-wrap clock via `_round_complete_update` (round 1 → 9 AM, round 2 → 12 PM — catches the `rounds`/`new_rounds` off-by-one); `day_close` early-end (Day ends at round 3 → closing recap shows 3 PM and **not** midnight) and round-cap (→ midnight); end-to-end progression 9 AM → 12 PM → 3 PM → 6 PM → 9 PM → 12 AM over a six-round Day. **[Agent: testing]**
  - [x] Verification: `uv run pytest -q` green; confirm `test_dual_mode_smoke.py` stays byte-equal (deterministic recap text); drive a full six-round Day and an early-ending Day, confirming the clock advances and an early end shows the round it stopped on. **[Agent: testing]**
