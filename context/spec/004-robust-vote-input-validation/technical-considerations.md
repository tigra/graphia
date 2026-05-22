# Technical Specification: Robust /vote Input Validation

- **Functional Specification:** `context/spec/004-robust-vote-input-validation/functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Two changes confined to `src/graphia/nodes/day.py` and one new test file:

1. **Tighten the slash-command parser** in `day_turn` so `/vote` is only treated as a command when (a) it equals `/vote` exactly, (b) is followed by whitespace, or (c) is followed by whitespace AND a non-empty target. The empty/bare case maps to a distinct "Usage: /vote <name>" error to distinguish from a wrong-name miss.
2. **Add deterministic test coverage** in a new file `tests/test_vote_validation.py` for every input shape required by the spec — most importantly two end-to-end scenarios for self-vote (pass-branch and fail-branch) using the existing `fake_sonnet_day` fixture to force AI ballots.

A code-read of `_begin_vote` / `collect_votes` / `resolve_vote` / the `check_win_day` conditional edge found **no red flags for `initiator==target`**: ballots are keyed by `voter.id` (no collision when voter==target), tally uses strict majority on a single ballots dict, win-check runs once via a single conditional edge. So the user's "self-vote ends the game immediately" report is almost certainly legitimate behaviour (a self-execution that tips citizen/mafia balance into a real win condition). The §2.2 tests are designed to confirm this — if either test fails, that failure pinpoints the actual bug and a follow-on fix gets scoped into this same slice.

No new files in `src/`. No graph topology changes. No state schema changes.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Strict slash-command parsing (`src/graphia/nodes/day.py` `day_turn`)

Current parse, lines ~338:

```python
lowered = text.lower()
if lowered.startswith("/vote"):
    remainder = text[len("/vote"):].strip()
    target_id = _fuzzy_match_alive(players, remainder)
    if target_id is None:
        payload = {**base_payload, "error": "No such player. Try again."}
        continue
    ...
```

That accepts `/votefoo`, `/voted`, `/votefor Alice` as the slash command and tries to match weird remainders. Change to: only treat as `/vote` when the next character is whitespace or end-of-input.

| Input | After fix |
|---|---|
| `/vote Alice` | matched → target=Alice (existing flow) |
| `/vote` (alone) | matched → empty remainder → **"Usage: /vote <name>"** error |
| `/vote ` (trailing whitespace) | matched → empty remainder → **"Usage: /vote <name>"** |
| `/vote\t\t` (tab) | matched → empty remainder → **"Usage: /vote <name>"** |
| `/vote zzz` | matched → no fuzzy match → **"No such player. Try again."** |
| `/vote <my-own-name>` | matched → fuzzy match self → `_begin_vote(self, self, players)` (allowed per §2.2) |
| `/vote <dead-name>` | matched → no fuzzy match in alive-players → **"No such player. Try again."** |
| `/voted yesterday` | **not matched** → treated as speech (`text` becomes the spoken line) |
| `/votefor Alice` | **not matched** → treated as speech |

Implementation shape (illustrative; implementing agent picks idiomatic form):

```python
# Strict: /vote at end-of-input OR followed by whitespace.
tokens = lowered.split(maxsplit=1)
if tokens and tokens[0] == "/vote":
    remainder = text[len("/vote"):].strip()
    if not remainder:
        payload = {**base_payload, "error": "Usage: /vote <name>"}
        continue
    target_id = _fuzzy_match_alive(players, remainder)
    if target_id is None:
        payload = {**base_payload, "error": "No such player. Try again."}
        continue
    active = _begin_vote(player.id, target_id, players)
    return {"active_vote": active}
```

The two error branches (empty remainder vs no fuzzy match) emit distinct messages so the player gets actionable feedback per §2.4.

### 2.2 Test coverage — `tests/test_vote_validation.py` (new file)

A single new test file. Uses existing fixtures from `tests/conftest.py` (`fake_sonnet`, `fake_haiku`, `fake_sonnet_day`, `dynamic_night_pointing`, `target_human_pointing`) and follows the pattern in `tests/test_slice7_vote.py`.

| Test | What it asserts | Setup |
|---|---|---|
| `test_vote_against_self_passes_executes_human` | Self-vote → all AIs vote Yes → human executed, role revealed, Day ends, kill_log gains one execution record. `check_win_day` runs ONCE; END only if a real win condition holds. | `dynamic_night_pointing` keeps human alive into Day 1; `fake_sonnet_day` forced to return Ballot(yes) on every AI poll. |
| `test_vote_against_self_fails_human_survives` | Self-vote → all AIs vote No → vote fails, human survives, `day_votes_called` increments, speaking round resumes. | Same setup; `fake_sonnet_day` forced to Ballot(no). |
| `test_vote_nonexistent_name_reprompts` | `/vote zzz` causes a re-interrupt with payload `{"error": "No such player. Try again."}`. Turn not consumed. | Use interrupt-replay machinery to feed the rejected `/vote zzz` then a follow-up valid speech. |
| `test_vote_empty_name_shows_usage_hint` | Bare `/vote`, `/vote `, `/vote\t\t` all cause a re-interrupt with payload `{"error": "Usage: /vote <name>"}`. Parametrised over the three input shapes. |
| `test_vote_dead_player_reprompts` | Pre-kill an AI; `/vote <dead-name>` re-prompts with "No such player. Try again." | Modify initial state to set one AI's `is_alive=False` before Day 1. |
| `test_voted_yesterday_is_speech` | `/voted yesterday` returns from `day_turn` with the human's message (no `active_vote`). | One interrupt() reply with that text. |
| `test_votefor_alice_is_speech` | Same shape for `/votefor Alice`. |

**Critical for §2.2 of the functional spec:** The pass-branch and fail-branch tests must drive the **full Day flow** through `vote_prompt` → `collect_votes` → `resolve_vote` → conditional edge. They use `graph.stream()` end-to-end. They are NOT unit-mock checks against individual functions.

If `fake_sonnet_day` cannot be made to force a uniform Yes/No across all polls, fall back to monkeypatching `_ai_ballot` directly for the test duration — investigate the fixture first.

### 2.3 Conditional bug-fix slot (contingent)

If either §2.2 self-vote test fails on first run, the failure surfaces the actual bug. Likely candidates (none currently red-flagged by the code-read): a ballot key collision when voter==target, win-check double-firing, `_begin_vote` pending-list anomaly. Fix gets scoped into a §2.4 follow-on. If both tests pass on first run, the user's report is confirmed as legitimate behaviour — no production change beyond §2.1's parser tightening.

_Outcome: both self-vote tests passed first run. No `initiator==target` bug. The "self-vote ends the game" report was legitimate win-check behaviour after a self-execution._

### 2.4 Re-prompt restructure (the real "ends-game-on-bad-vote" bug — found during USER smoke)

The original re-prompt loop in `day_turn` called `interrupt()` **a second time within the same node execution** on an invalid `/vote` (the `while True: ... continue` pattern). This is the root cause of the user-reported "bare `/vote` / `/vote zzz` ends the game immediately":

- When the graph re-pauses on that second in-node interrupt, LangGraph reports `snapshot.next == ()` (empty) while the pending interrupt still lives on `snapshot.tasks[0].interrupts`.
- `src/graphia/driver.py` `drive_graph` checks `if not next_nodes: return` **before** it inspects `interrupts`, so it misreads the empty `next` as game-over and returns → the app treats the session as ended.
- The §2.1/§2.3/§2.5 tests missed this because they hand-drive `graph.stream(...)` and read interrupts off `snapshot.tasks`; they never route through `drive_graph` (the real app path).

**Fix — one `interrupt()` per node execution.** `day_turn`'s human branch now:
- Reads any prior `day_turn_error` from state into the interrupt payload's `error` field, calls `interrupt()` **once**, validates the input, and on an invalid `/vote` returns `{"day_turn_error": <msg>}` (turn not consumed) instead of re-`interrupt()`ing.
- The conditional edge `route_day_turn_or_vote` gains a guard: when `day_turn_error` is set, always route back to a fresh `day_turn` (so a round-cap can't close the Day on a rejected, unconsumed turn).
- Accepted speech / valid vote / `day_open` clear `day_turn_error` back to `None`.

New state channel: `day_turn_error: str | None` in `src/graphia/state.py` (replace-semantics, like the other scalar channels).

**Latent driver fragility (noted, not fixed here):** `drive_graph`'s `if not next_nodes: return` ordering will mis-handle ANY future node that calls `interrupt()` more than once per execution. We fixed the symptom upstream by making `day_turn` single-interrupt. If another multi-interrupt node is ever added, the driver should be hardened to check `interrupts` before treating empty `next` as terminal. Candidate for a follow-up ADR / driver hardening task.

**New driver-level regression test:** `tests/test_vote_driver.py` drives the REAL `drive_graph()` with stubbed callbacks and asserts the game continues (re-prompts) after a bad `/vote`. Verified to fail on pre-fix code, pass after.

---

## 3. Impact and Risk Analysis

**System Dependencies**

- `_fuzzy_match_alive`, `_begin_vote`, `_alive_ids_in_roster_order` helpers in `day.py` — unchanged.
- `interrupt()` / resume re-prompt loop — already exercised by the existing no-such-player path; new parsing change extends but doesn't alter the mechanism.
- Conditional edge `resolve_vote → check_win_day → END | day_turn` — unchanged.

**Potential Risks & Mitigations**

| Risk | Mitigation |
|---|---|
| Tightening the slash-command rule breaks a passing spec-001 test. | Run `pytest -q` after the parsing change. Spec 001 §2.6 baseline tests are pure-vote-against-other-player flows; they should be unaffected. |
| `fake_sonnet_day` can't force a uniform Yes/No outcome. | Inspect the fixture first; fall back to monkeypatching `_ai_ballot` for the test duration. |
| §2.2 tests reveal a real bug. | That IS the goal — fix it, add a regression test, mark the original tests green. |
| `Usage: /vote <name>` wording confuses non-English players. | One-line text; can be tweaked in a follow-on without spec change. Out-of-scope for this slice. |

No impact on graph topology, persistence, AgentCore Runtime / Memory / Gateway code paths, or remote-mode plumbing.

---

## 4. Testing Strategy

All tests are pytest cases in the new `tests/test_vote_validation.py` file, using the all-mocked fixture conventions (`safe_llm` autouse + per-test `fake_haiku` / `fake_sonnet` / `fake_sonnet_day`). No Bedrock calls; deterministic via `GRAPHIA_SEED`.

The §2.2 pass-branch and fail-branch tests drive a full Day segment (Day 1 turn → vote initiation → polling → resolution) and assert observable end-state — `kill_log` contents, player `is_alive` flags, `active_vote=None`, `day_votes_called` count, and which graph node fires next (END vs day_turn).

Existing `tests/test_slice7_vote.py` stays as-is and continues to cover the baseline vote-against-other-player flow.
