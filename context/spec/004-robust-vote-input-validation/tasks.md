# Tasks: Robust /vote Input Validation

- **Functional Specification:** `context/spec/004-robust-vote-input-validation/functional-spec.md`
- **Technical Considerations:** `context/spec/004-robust-vote-input-validation/technical-considerations.md`
- **Status:** Draft

## Slice 1 — Strict slash-command parsing + Usage error

After this slice, `/voted` / `/votefor Alice` are treated as speech; bare `/vote` shows "Usage: /vote <name>"; existing `/vote <alive-player>` still works. Self-vote behaviour unchanged in this slice (covered by Slice 2).

- [x] **Sub 1.1:** In `src/graphia/nodes/day.py` `day_turn`, replace the `if lowered.startswith("/vote"):` parse with a strict variant — match only when `/vote` is followed by whitespace or is the entire token. When the remainder (after stripping) is empty, emit error `"Usage: /vote <name>"` and re-prompt. When the remainder is non-empty but `_fuzzy_match_alive` returns None, keep emitting the existing `"No such player. Try again."` error. **[Agent: python-backend]**
- [x] **Sub 1.2:** Add `tests/test_vote_validation.py` with three cases for Slice 1: (a) `test_vote_empty_name_shows_usage_hint` — parametrised over `/vote`, `/vote `, `/vote\t\t` — asserts the re-interrupt payload's `error` field is `"Usage: /vote <name>"`; (b) `test_voted_yesterday_is_speech` — `/voted yesterday` is captured as the human's speech (no `active_vote`); (c) `test_votefor_alice_is_speech` — same for `/votefor Alice`. **[Agent: testing]**
- [x] **Sub 1.3:** `uv run pytest -q` — full suite green. **[Agent: testing]**
- [x] **USER:** Manual smoke — start a game, advance to Day chat. Type `/voted yesterday` and submit; confirm it appears as your speech in the public log. On your next turn, type bare `/vote` and submit; confirm the "Usage: /vote <name>" hint appears and the turn isn't consumed. _(Confirmed by user.)_

## Slice 2 — Self-vote end-to-end coverage (both branches)

After this slice, the two scenarios spec §2.2 mandates are pinned by tests. Will surface any real self-vote bug (if one exists) and confirm the user's report otherwise.

- [x] **Sub 2.1:** In `tests/test_vote_validation.py`, add `test_vote_against_self_passes_executes_human` — sets up a Day 1 state where the human is alive, drives `graph.stream()` through self-`/vote` → all-AI-Yes → resolution. Asserts: (a) `kill_log` gains exactly one execution record with `name == human.name`, (b) `players[human.id].is_alive is False`, (c) `active_vote is None` after resolution, (d) `check_win_day` evaluates exactly once (graph reaches END or day_close, not a duplicate fire). Use `fake_sonnet_day` (or monkeypatch `_ai_ballot`) to force `Ballot(yes=True)` for every AI. **[Agent: testing]** _(Passed first run — confirms no `initiator==target` bug.)_
- [x] **Sub 2.2:** In the same file, add `test_vote_against_self_fails_human_survives` — symmetric setup with all-AI-No. Asserts: (a) `kill_log` unchanged, (b) human's `is_alive` still True, (c) `active_vote is None`, (d) `day_votes_called` incremented by 1, (e) graph routes back to `day_turn`. **[Agent: testing]** _(Passed first run.)_
- [x] **Sub 2.3:** `uv run pytest -q` — full suite green. If either test fails, surface the failure to the user with a one-line diagnosis (likely candidate: ballot collision, win-check double-fire, pending-list anomaly); the fix becomes Sub 2.4. **[Agent: testing]** _(121 passed, 1 skipped — both self-vote tests green; no bug surfaced. User's "ends game immediately" report confirmed as legitimate win-check behaviour after a self-execution.)_
- [x] **Sub 2.4 (contingent):** If 2.1 or 2.2 fails, fix the underlying production bug in `src/graphia/nodes/day.py` (or wherever the diagnosis points). Add a regression test specific to the root cause. Re-run pytest. **[Agent: langgraph-agentic]** _(Not triggered — both self-vote tests passed first run.)_
- [x] **USER:** Manual smoke — start a game. On Day 1, type `/vote <your-own-name>` and submit; observe whether the vote proceeds (every alive player polls) or ends the game weirdly. Either outcome — confirm matches the test result from Sub 2.3. _(Confirmed by user — vote proceeds normally.)_

## Slice 1b — Re-prompt restructure (discovered during USER smoke)

_Discovered after Slice 1: bare `/vote` and `/vote zzz` ended the game in the real app even though the parser was correct. Root cause: `day_turn`'s re-prompt loop called `interrupt()` twice per node execution; on the second in-node interrupt LangGraph reports `snapshot.next == ()`, and `drive_graph` returns on empty `next` before checking interrupts → app treats it as game-over. The hand-driven graph.stream tests missed it. See technical-considerations §2.4._

- [x] **Sub 1b.1:** Restructure `day_turn`'s human branch to issue exactly one `interrupt()` per node execution — carry the error via a new `day_turn_error: str | None` state channel (`src/graphia/state.py`), return `{"day_turn_error": <msg>}` on invalid `/vote` (turn not consumed), and add a guard in `route_day_turn_or_vote` that routes back to a fresh `day_turn` while an error is pending. Clear the error on accepted speech / valid vote / `day_open`. **[Agent: langgraph-agentic]**
- [x] **Sub 1b.2:** Add `tests/test_vote_driver.py` — a driver-level regression test that runs the REAL `drive_graph()` and asserts the game continues (re-prompts) after a bad `/vote`. Verified to fail pre-fix, pass post-fix. **[Agent: langgraph-agentic]**
- [x] **Sub 1b.3:** Also fixed by a separate UI change: `_request_resume`'s `day_turn` branch now renders `payload["error"]` as a bold-red line (the error was being stored but never displayed). **[Agent: textual-tui]**
- [x] **Sub 1b.4:** `uv run pytest -q` — 125 passed, 1 skipped. **[Agent: testing]**
- [x] **USER:** Manual smoke — start a game, reach your Day turn. Type bare `/vote` → confirm "Usage: /vote <name>" appears and the game CONTINUES (you're re-prompted). Type `/vote zzz` → confirm "No such player. Try again." and the game continues. Type `/vote <self>` → confirm the vote runs. Confirm a normal speech turn still works. _(Confirmed by user — re-prompt fix verified.)_

## Slice 3 — Remaining validation tests (locks existing behaviour)

After this slice, the rest of the §2 acceptance criteria are pinned: nonexistent name re-prompts, dead-player target re-prompts.

- [x] **Sub 3.1:** In `tests/test_vote_validation.py`, add `test_vote_nonexistent_name_reprompts` — drive `day_turn` via interrupt-replay; after submitting `/vote zzz` the next interrupt's payload contains `{"error": "No such player. Try again."}` and the turn isn't consumed. **[Agent: testing]**
- [x] **Sub 3.2:** Add `test_vote_dead_player_reprompts` — modify initial state to set one AI's `is_alive=False`; submitting `/vote <that-dead-name>` re-prompts with the same "No such player" error. **[Agent: testing]** _(Implemented via `_fuzzy_match_alive` monkeypatch — `graph.update_state` clears pending interrupts, so state-patch wasn't viable; helper-level shadow tests the same filtering logic.)_
- [x] **Sub 3.3:** `uv run pytest -q` — full suite green. **[Agent: testing]** _(123 passed, 1 skipped.)_
- [x] **USER:** Manual smoke — start a game, advance to Day 1. Type `/vote zzz` (an obviously fake name); confirm the "No such player. Try again." message appears and you can still type your turn afterwards. _(Confirmed by user via the Slice 1b smoke.)_
