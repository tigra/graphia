# Tasks: End-of-Round Day-Dynamics Recap (Spec 018)

Vertical slices for the **Day-Round Moderator Recap**. Each slice leaves the app
runnable and is independently testable. Slice 1 delivers the visible value (the
day-round recap appears, on by default); Slice 2 adds the ablation off-switch.

Functional spec: `./functional-spec.md` · Technical considerations: `./technical-considerations.md`

---

- [x] **Slice 1: The day-round recap appears at every Day round boundary (on by default)**
  - [x] Add `day_votes_initiated: int` to `GameState` (plain replace); reset to `0` in `day_open`; increment by 1 at both vote-initiation sites in `day_turn` (the human `/vote` branch and the AI `DayAction(kind="vote")` branch). **[Agent: langgraph-agentic]**
  - [x] Add `DAY_ROUND_RECAP_TEMPLATE` to `prompts.py` (brief, present-tense Moderator voice, singular/plural handling); lift the executed-this-cycle predicate from `day_close` into a shared `_executed_this_cycle(kill_log, cycle)`; add the pure `render_day_round_recap(state) -> SystemMessage` (day number from `cycle`, alive counts by role from `players`, votes from `day_votes_initiated`, executed-clause via `_role_label`; no mutation, no RNG). **[Agent: langgraph-agentic]**
  - [x] Add `_round_complete_update(state, rounds, *, recap_enabled, extra=None)` and route all three round-wrap return sites in `day_turn` through it; append the recap iff `recap_enabled AND new_rounds < DAY_MAX_ROUNDS`; add `recap_enabled: bool = True` kwarg to `day_turn`. **[Agent: langgraph-agentic]**
  - [x] Add `recap_enabled: bool = True` to `day_close`; append `render_day_round_recap(state)` after its existing close-line logic when enabled. Confirm the recap is never posted on the win path (which routes to `end_screen`, bypassing `day_close`) — the one-recap-per-boundary table in the tech spec holds. **[Agent: langgraph-agentic]**
  - [x] Add `tests/test_slice_day_round_recap.py`: pure-renderer cases (no-execution; with-execution names player + revealed side; singular vs plural role counts; `day_votes_initiated` = 0·1·3 for the votes line); `day_turn` appends exactly one recap on a continuing round-wrap and none mid-round; `day_close` posts the closing recap (execution case names player + side; no-execution variant); no double-post at the round-cap boundary; `day_votes_initiated` increments on human `/vote` and AI vote and resets in `day_open`; eval-isolation regression (recap text absent from `blunder_eval._ai_lines_with_names` / `repetition_experiment._ai_speeches` / `eval_dialogue` extraction). **[Agent: testing]**
  - [x] Verification: `uv run pytest -q` green, including the unchanged `tests/test_slice8_endgame.py` "last message" assertions and `tests/test_slice6_day.py` round-cap test; drive a full Day (scripted / `fake_large`, pinned `_shuffle_order`) and confirm a recap appears at the end of each round and at Day close with correct counts. **[Agent: testing]**

- [x] **Slice 2: Ablation off-switch — `GRAPHIA_DAY_ROUND_RECAP` disables the recap**
  - [x] Add `_env_flag(name, *, default) -> bool` to `config.py` (default-on semantics over the existing `_TRUTHY` set); add `GraphiaConfig.day_round_recap_enabled: bool = True`; parse `day_round_recap_enabled = _env_flag("GRAPHIA_DAY_ROUND_RECAP", default=True)` in `load_config()`. **[Agent: python-backend]**
  - [x] Thread the flag: add a `recap_enabled: bool` parameter to `graph.py::_assemble_graph`, bind it into `day_turn` and `day_close` via `partial`; pass `config.day_round_recap_enabled` from `build_graph`, and add a `day_round_recap_enabled` parameter to `runtime/graph_builder.py::build_runtime_graph` passed from config by the Runtime entrypoint (both builders, anti-drift). **[Agent: langgraph-agentic]**
  - [x] Tests: `load_config()` reads `GRAPHIA_DAY_ROUND_RECAP` with default-on semantics; off-switch end-to-end (flag off ⇒ zero recap messages anywhere, all other Day behavior unchanged); both `build_graph` and `build_runtime_graph` honor the flag. **[Agent: testing]**
  - [x] Verification: `uv run pytest -q` green; run a Day with `GRAPHIA_DAY_ROUND_RECAP=0` and confirm no recap lines appear; confirm `tests/test_dual_mode_smoke.py` stays byte-equal. **[Agent: testing]**
