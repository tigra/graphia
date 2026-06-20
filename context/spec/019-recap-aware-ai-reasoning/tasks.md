# Tasks: Recap-Aware AI Reasoning (Spec 019)

One vertical slice — make the current standings a front-and-center input to each
AI player's Day speech and votes. Pure prompt change; no state/RNG/new-node.

> **Implementation order:** 019 and 020 both refactor `render_day_round_recap`,
> so they must NOT be implemented concurrently. Do **019 first** (it factors
> `_render_standings`, leaving the recap output unchanged), then 020.

Functional spec: `./functional-spec.md` · Technical considerations: `./technical-considerations.md`

---

- [x] **Slice 1: AI Day speech and votes reason from the current standings**
  - [x] Factor a pure `_render_standings(state) -> str` out of `render_day_round_recap` (the decision-relevant standings text only — living counts by side, votes-called-today, executed-today; **no clock, no "Day N status:" prefix**), leaving the public recap's output byte-unchanged. Add a front-and-center `{standings}` slot to `DAY_SPEAK_USER_TEMPLATE` **and** `AI_VOTE_USER_TEMPLATE` (placed after the role/persona line, before "Recent public discussion:"); populate it at the `_ai_day_action` and `_ai_ballot` call sites with `_render_standings(state)`. Update the ~5 existing test `.format()` sites that the new slot breaks (`tests/test_behavioral_integrity.py`, `tests/test_blunder_eval_detectors.py`, `tests/test_instrument_capture.py`, `tests/test_personas.py`) to pass `standings=`, so the suite stays green. **[Agent: langgraph-agentic]**
  - [x] Add tests (`tests/test_slice_day_round_recap.py` or a sibling): pure `_render_standings` over hand-built state (counts by side singular/plural; votes 0·1·N; executed-today named+side, none, and stale-prior-cycle excluded; **no clock tokens** present; purity — no state mutation); the non-leak invariant (no living player's name co-occurs with a side label — only aggregate counts); and prompt-injection capture (the standings block appears in both the rendered Day-speech and vote prompts) plus a template-slot guard. **[Agent: testing]**
  - [x] Verification: `uv run pytest -q` green (including the updated `.format()` sites and the unchanged `test_dual_mode_smoke.py`); drive one AI speaking turn and one AI ballot and confirm the standings block is present in both prompts. **[Agent: testing]**
  - [x] Effort-not-results measurement (out-of-suite, per CR 005): run `make blunder-eval ARGS="--provider ollama --games N"`, then compare the Day-decisiveness indicators (vote-initiation rate, share of games reaching a win/loss vs `no_winner`, town win-rate) against the committed baseline record `2026-06-19T18-33-37` in `evals/blunder-ledger.yaml`; record the hypothesis as confirmed or refuted (both satisfy acceptance). **[Agent: testing]**
