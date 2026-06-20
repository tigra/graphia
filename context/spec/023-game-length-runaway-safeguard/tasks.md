# Tasks: Games Run to a Natural Conclusion (Runaway-Only Day Cap) (Spec 023)

Gameplay-influencing → the Day cap is a **tunable value** (default = 12 Days), so prior behavior is reproducible for A/B (ADR 011).

> **Note:** no `technical-considerations.md` (fast-tracked past `/awos:tech`); approach folded in.
> **Verified mechanism (corrected):** measured games are cut today by `blunder_eval`'s
> `if rounds >= max_rounds` where `rounds` counts the human's **Day-speaking turns**
> (default `max_rounds=10` → ~mid-Day-2), **not** the `cycle >= 20` in-game cap (never
> reached). The fix makes the **in-game Day cap** the single limit and lets the eval drive
> to natural end. A "Day" is the existing `cycle` / "Day N" counter.

Functional spec: `./functional-spec.md`

---

- [ ] **Slice 1: The game-length limit is a single day-denominated runaway safeguard (default 12 Days); games run to their natural end**
  - [ ] Add a tunable Day cap: a config setting in `src/graphia/config.py` — env `GRAPHIA_MAX_DAYS`, default **12** (parsed like the other counts) — and replace the hardcoded `cycle >= 20` safeguard in `src/graphia/nodes/night.py` with `cycle >= max_days`, threaded in consistently with the project's existing config wiring. Update the day-cap user-facing text accordingly: the "The game has reached 20 cycles without a resolution." line and `ENDGAME_WINNER_DRAW` (`prompts.py`, "…draw after 20 cycles.") to read in **Days**, and the stale `graph.py` comment ("when night_open detects cycle >= 20"). **[Agent: langgraph-agentic]**
  - [ ] Make measured games run to their natural conclusion: in `src/graphia/tools/blunder_eval.py`, **remove the `if rounds >= max_rounds: break` human-Day-turn cut** (and the `rounds` counter) so the drive ends only on `not snapshot.next` (natural win/loss, or the in-game Day cap routing to `end_screen`); resize the outer loop backstop (currently `range(max_rounds * 12 + 20)`) to a generous bound derived from the Day cap (≈ `max_days` × worst-case super-steps per Day), purely as an anti-hang guard. **[Agent: langgraph-agentic]**
  - [ ] Re-express the control var as days, completing the rename across its uses: the eval's `--max-rounds` CLI flag → a day-denominated `--max-days` (overrides `GRAPHIA_MAX_DAYS` for a run); the recorded ledger setting (`settings.max_rounds`) → the Day cap, and the run-summary string. **Check the other consumers found** — `eval_dialogue.py` and `ollama_smoke.py` also use `max_rounds`: align them to the day-denominated cap if it's the same game-length control, or leave them and add a one-line note distinguishing their sampling cap from the game Day cap. Keep already-committed ledger records readable (the spec-012 viewer reads heterogeneous records defensively; old records keep their `max_rounds` field). **[Agent: langgraph-agentic]**
  - [ ] Flag a cap-triggered game distinctly as **runaway / unresolved**, not a legitimate "draw": adjust the end-of-game outcome (`src/graphia/nodes/endgame.py`) and the eval outcome recording (`blunder_eval.py` outcomes block) so a Day-cap hit is a distinct runaway/unresolved bucket, visibly separate from a real Law-abiding / Mafia win. **[Agent: langgraph-agentic]**
  - [ ] Tests: config default (12) + `GRAPHIA_MAX_DAYS` / `--max-days` override; the cap triggers at the configured Day (update the existing `cycle >= 20` draw test to the configured value); a cap-hit records as runaway/unresolved, distinct from a real win; a long-but-natural game runs through to a real win/loss without being cut off mid-Day; update the provenance/lineup tests (`tests/test_blunder_eval_provenance.py`, `tests/test_lineup_recording.py`, `tests/test_blunder_eval.py`) for the renamed setting. **[Agent: testing]**
  - [ ] Verification: `uv run pytest -q` green; then (effort-not-results, per CR 005) run `make blunder-eval ARGS="--provider ollama --games 10"`, confirm games now run to completion (the `no_winner` share drops sharply) and compare win-rate-by-side against the baseline `2026-06-19T18-33-37`; record the hypothesis (does finishing games lift the town's win-rate?) as confirmed or refuted. **[Agent: testing]**
