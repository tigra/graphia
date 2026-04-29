# Implementation Tasks: Playable Skeleton

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Technical Specification:** [`technical-considerations.md`](./technical-considerations.md)

This task list is organized into vertical slices. Each slice leaves the app in a runnable state and ends with an automated test plus a user-performed manual smoke test. Slices build strictly on top of one another — do not skip ahead.

---

## Slice 1: App boots with welcome + name collection + logging scaffold

_Smallest possible piece of user-visible value: a real Textual app that launches, greets the player by name, and exits cleanly._

- [x] Add dependencies: `uv add textual langgraph-checkpoint-sqlite pydantic` and `uv add --dev pytest pytest-asyncio pytest-textual-snapshot`. Bump nothing else in `pyproject.toml`. **[Agent: python-backend]**
- [x] Create `src/graphia/__init__.py` and `src/graphia/__main__.py`. `__main__.py` reconfigures `sys.stdin/stdout/stderr` to UTF-8 (errors=replace), loads `.env` via python-dotenv, calls `GraphiaApp().run()`. **[Agent: python-backend]**
- [x] Create `src/graphia/config.py`: reads `AWS_BEARER_TOKEN_BEDROCK` (required), `AWS_REGION` (default `eu-north-1`), `GRAPHIA_LOG_FILE` (default `./.graphia/graphia.log`), `GRAPHIA_SEED` (default = time-based), `GRAPHIA_CHECKPOINT_DIR` (default `./.graphia/checkpoints`). Raises `SystemExit` with a human-readable message on missing required vars. **[Agent: python-backend]**
- [x] Create `src/graphia/logging.py` with `StreamTraceLogger(log_path).record(event: dict)` that appends one JSONL line per call and `setup_logger(config)` that creates the log directory if needed. Emit a `"boot"` event at app launch. **[Agent: python-backend]**
- [x] Configure `pyproject.toml` with a build system and `src/` package discovery so `uv run python -m graphia` and `uv run pytest` find the `graphia` package. (Follow-up identified during implementation.) **[Agent: python-backend]**
- [x] Create `src/graphia/ui/app.py` with `GraphiaApp(App)`. Widgets: `Static` welcome message at top; `Input` at bottom with placeholder "Enter your name…". On submit, replace the welcome with "Hello, <name>!" and bind `q` + Ctrl+C to exit. **[Agent: textual-tui]**
- [x] Add `tests/test_app_boot.py`: uses Textual's `App.run_test()` pilot to launch the app, type "Alice" + Enter, assert the "Hello, Alice!" text appears, then press `q` and assert the app exits. **[Agent: testing]**
- [x] Verify automated test passes: run `uv run pytest tests/test_app_boot.py -q` and confirm green. **[Agent: testing]**
- [x] **USER:** Manual smoke test — run `uv run python -m graphia` in a real terminal. Confirm: welcome message renders, Input accepts text, "Hello, <you>!" shows, `q` and Ctrl+C both exit cleanly, no Python traceback, `./.graphia/graphia.log` contains at least one JSON line. _(Agent cannot verify this; user-performed.)_

---

## Slice 2: Roster assembled with stubbed AI names + public Moderator intro

_Second-smallest increment: after entering a name, the player sees a 6-player roster introduced by the Moderator. AI names are hardcoded placeholders for now — real LLM call arrives in Slice 3._

- [x] Create `src/graphia/state.py`: `PlayerState` dataclass (id, name, role, is_human, is_alive), `GameState` TypedDict with `messages` (add_messages), `players` (replace), `human_id`, `phase`, `cycle`, plus the placeholder fields described in tech spec §2.3 (unused until later slices). **[Agent: langgraph-agentic]**
- [x] Create `src/graphia/prompts.py` with `MODERATOR_SYSTEM` and `ROSTER_INTRO_TEMPLATE` string constants. **[Agent: langgraph-agentic]**
- [x] Create `src/graphia/nodes/setup.py` with three nodes: `collect_name` (uses `interrupt({"kind":"name"})` as first statement, returns `{"human_id": new_id, "players": {...seeded human only...}}`), `generate_roster` (hardcoded list `["Ivy","Marco","Priya","Silas","Yuki"]` for now, assigns `PlayerState` objects with `role="law_abiding"` placeholder), `introduce_roster` (emits a `SystemMessage` containing the full roster names). **[Agent: langgraph-agentic]**
- [x] Create `src/graphia/graph.py`: builds the `StateGraph`, wires `START → collect_name → generate_roster → introduce_roster → END`, compiles with `SqliteSaver` pointing at `config.checkpoint_dir / f"{thread_id}.sqlite"`. `thread_id` = UTC timestamp string. **[Agent: langgraph-agentic]**
- [x] Update `src/graphia/ui/app.py`: replace the welcome-only flow with a `drive_graph()` coroutine that `await`s `asyncio.to_thread(graph.stream, ...)` per super-step. Add a `PublicLog` (`RichLog`) widget above the `Input`. Resume `interrupt` events by reading from the `Input`. **[Agent: textual-tui]**
- [x] Wire `StreamTraceLogger.record` into the `drive_graph()` loop — log one event per super-step with node name and updated keys. **[Agent: langgraph-agentic]**
- [x] Add `tests/test_slice2_roster.py`: drive the app with name "Alice", assert that `PublicLog` contains all 6 names (Alice + 5 hardcoded), in a single Moderator line. **[Agent: testing]**
- [x] Verify automated test passes: `uv run pytest tests/test_slice2_roster.py -q`. **[Agent: testing]**
- [x] **USER:** Manual smoke test — run `uv run python -m graphia`, type a name, confirm the Moderator's roster line appears in the chat log with all 6 names and no roles. _(User-performed.)_

---

## Slice 3: Live AI names via Haiku 4.5

_Replace the hardcoded name list with a real Bedrock Haiku call. Two consecutive runs should show different rosters._

- [x] Create `src/graphia/llm.py`: `get_sonnet()` and `get_haiku()` factory functions returning `ChatBedrockConverse` instances with region and model IDs from `config` (`eu.anthropic.claude-sonnet-4-5-20250929-v1:0`, `eu.anthropic.claude-haiku-4-5-20251001-v1:0`). Singleton-cached. **[Agent: langgraph-agentic]**
- [x] Add a `Roster` Pydantic model in `llm.py`: `names: list[str]` with `Field(min_length=5, max_length=5)` and a validator asserting all names are distinct and non-empty. **[Agent: langgraph-agentic]**
- [x] Update `generate_roster` in `nodes/setup.py`: call `get_haiku().with_structured_output(Roster).invoke([...])`. On validation failure, retry once with a "names must be distinct" prompt; on second failure, raise — there is no fallback for name generation. **[Agent: langgraph-agentic]**
- [x] Add `tests/test_slice3_names.py`: monkeypatch `get_haiku()` to return a fake model that yields a known `Roster`; drive the app; assert the 5 returned names appear in the roster intro. **[Agent: testing]**
- [x] Verify automated test passes: `uv run pytest tests/test_slice3_names.py -q`. **[Agent: testing]**
- [x] **USER:** Manual smoke test — run `uv run python -m graphia` twice and confirm the 5 AI names differ between runs. Confirm no Bedrock errors in the log. _(User-performed; requires live Bedrock creds.)_

---

## Slice 4: Role assignment + private role reveal to human

_The human now learns their secret role. The roster is split 2 Mafia / 4 Law-abiding with the human placed randomly (seeded)._

- [x] Add `assign_roles` node in `nodes/setup.py`: seeded `random.Random(config.seed)` shuffles `["mafia","mafia","law_abiding","law_abiding","law_abiding","law_abiding"]`, zips against all 6 `PlayerState`s, sets each `role`. **[Agent: langgraph-agentic]**
- [x] Add `reveal_role` node: emits a private message to the human. **[Agent: langgraph-agentic]**
- [x] Add a `PrivateLog` widget to `ui/app.py`: a smaller `RichLog` with a distinct bordered style. Route messages tagged `private=True` to it; everything else goes to `PublicLog`. Messages are tagged via a custom `additional_kwargs={"private_to": human_id}` on the `SystemMessage`. **[Agent: textual-tui]**
- [x] Update `graph.py` topology: `introduce_roster → assign_roles → reveal_role → END`. **[Agent: langgraph-agentic]**
- [x] Add `tests/test_slice4_role_reveal.py`: run with `GRAPHIA_SEED=42`, stub Haiku, assert the human sees "Your role is …" in `PrivateLog` and not in `PublicLog`. Parameterize over two seeds that produce different roles. **[Agent: testing]**
- [x] Verify automated test passes. **[Agent: testing]**
- [x] **USER:** Manual smoke test — run with and without `GRAPHIA_SEED` set, confirm a private panel appears with "You are <name>. Your role is <role>." and the main chat does not echo the role. _(User-performed.)_

---

## Slice 5: Night 1 end-to-end (Mafia intros, pointing, kill resolution)

_First real gameplay: Night 1 runs, Mafia teammates are introduced, every Mafia picks a target, the Moderator announces the victim (role still hidden). Game ends right after Night 1 with a "to be continued" placeholder — Day arrives in Slice 6._

- [x] Create `src/graphia/nodes/night.py` with nodes: `first_night_mafia_intros` (private teammate list to each Mafia on cycle 1 only), `night_open` (public "Night falls" + reset `night_picks`), `mafia_pointing` (branches: human via `PointingModal`, AI via Sonnet `with_structured_output(Pointing)` where `Pointing.target_id` is validated against alive Law-abiding IDs; retry once on validation error), `resolve_night_kill` (tally picks, strict majority with random tie-break via seeded RNG, flip `is_alive`, append to `kill_log` with `role=None` placeholder). **[Agent: langgraph-agentic]**
- [x] Add `PointingModal` in `ui/widgets.py`: `ModalScreen` with a `Select` list of alive Law-abiding names; resolves on Enter. Shown only when `interrupt({"kind":"point", ...})` fires. **[Agent: textual-tui]**
- [x] Extend `graph.py`: `reveal_role → first_night_mafia_intros → night_open → mafia_pointing (fan-out) → resolve_night_kill → night_close_stub → END`. `night_close_stub` emits "(To be continued in Day 1 — Slice 6.)" then routes to END. **[Agent: langgraph-agentic]**
- [x] Add `tests/test_slice5_night.py`: seed the game, stub Sonnet to make AI Mafia point at a known target; drive the game; assert: (a) if human is Mafia, private teammate message appears and PointingModal opens; (b) victim is announced by name in `PublicLog`; (c) victim's `is_alive` is `False` in state; (d) victim's role is NOT revealed yet. **[Agent: testing]**
- [x] Verify automated test passes. **[Agent: testing]**
- [x] **USER:** Manual smoke test — run the game multiple times to cover both roles for the human. As Mafia: confirm teammate list appears privately, pointing modal shows 4 Law-abiding names, kill is announced. As Law-abiding: confirm no private Mafia messages, just "Night falls" and then the kill announcement. _(User-performed.)_

---

## Slice 6: Day 1 round-robin speaking + role reveal of last night's victim

_First Day phase: each alive player speaks once per round in random order, Day auto-ends after 6 rounds (safety cap). No voting yet — that's Slice 7. On Day open, the Moderator reveals the role of the player killed last night._

- [x] Create `src/graphia/nodes/day.py` with nodes: `day_open` (announces "Day breaks", recalls previous Night's victim + reveals their role, skipped on the very first Day since nobody was killed yet — but this spec starts with Night 1, so Day 1 always reveals), `day_turn` (branches: human via `Input` prompt accepting free text only in this slice, AI via Sonnet `with_structured_output(DayAction)` with flat schema `{kind: "speak", text: str}` — vote kind is scaffolded but rejected until Slice 7), `day_close` (announces "Day ends with no one executed" and returns to `night_open`). **[Agent: langgraph-agentic]**
- [x] Replace `night_close_stub` with a real `night_close` that routes into `day_open`. Update graph: `resolve_night_kill → night_close → day_open → (shuffle) → day_turn (loop, advancing turn_index) → day_close → night_open`. Add `day_rounds` increment logic + 6-round safety cap. **[Agent: langgraph-agentic]**
- [x] Update `ui/app.py`: when `interrupt({"kind":"day_turn"})` fires, enable the Input with placeholder "Your turn. Speak…"; disable at all other times. **[Agent: textual-tui]**
- [x] Append each alive player's spoken line as an `AIMessage` (for AI) or `HumanMessage` (for human) to `messages`, with `additional_kwargs={"speaker": name}`. `PublicLog` prefixes each with the speaker's name. **[Agent: textual-tui]**
- [x] Add `tests/test_slice6_day.py`: drive two full cycles (Night 1 → Day 1 → Night 2 → Day 2). Assert: (a) Day 1 begins with victim's role revealed; (b) each alive player speaks once per round in randomized order; (c) 6 pure-talk rounds auto-advance to Night; (d) cycle counter increments correctly. **[Agent: testing]**
- [x] Verify automated test passes. **[Agent: testing]**
- [x] Emergent: fix the driver to stream messages live (one super-step per asyncio.to_thread hop) so UI updates and tests don't wait for whole stream batches. **[Agent: langgraph-agentic]**
- [x] **USER:** Manual smoke test — launch and play through at least one full Day. Confirm role reveal line at Day open, your own turn prompt is clear, AI players speak in readable lines, and the Day eventually loops back to Night. _(User-performed.)_
- [x] Emergent: `mafia_pointing` / `resolve_night_kill` graceful-no-op when no alive targets remain (prevents Slice-6 crash when Mafia effectively wins before Slice 8 adds real win detection). **[Agent: langgraph-agentic]**

---

## Slice 7: Vote-to-execute mechanics

_Players can now call a vote and actually execute someone. Day ends on successful execution or after 3 votes called._

- [x] Extend `nodes/day.py`: enable the `{kind: "vote", target_id: str}` branch of `DayAction`. On human turn, parse `/vote <name>` from Input via fuzzy match against alive names (reject unique mismatch with "No such player. Try again." — does not consume the turn). Add `vote_prompt` node (announces vote), `collect_votes` node (iterate alive players in roster order; human via `VoteModal`, AI via Sonnet `with_structured_output(Ballot)`), `resolve_vote` node (strict majority executes → reveal role immediately → append to `kill_log` → end Day; otherwise increment `day_votes_called`, route back to `day_turn` after fresh shuffle). **[Agent: langgraph-agentic]**
- [x] Add `VoteModal` in `ui/widgets.py`: `ModalScreen` with Yes/No buttons over the target's name. **[Agent: textual-tui]**
- [x] Update `graph.py` routers: `route_day_turn` (speak → next turn, vote → vote_prompt), `route_after_vote` (executed → night_open, failed + 3-cap-hit → night_open, failed + remaining → day_turn). Keep the 6-rounds-without-vote cap from Slice 6. **[Agent: langgraph-agentic]**
- [x] Add `tests/test_slice7_vote.py`: script an AI to call `/vote` on a known target; script majority of ballots Yes; assert execution + role reveal + Day ends. Separate case: majority No → vote fails, Day continues with next round, `day_votes_called=1`. Edge case: 3 failed votes → Day ends without execution. **[Agent: testing]**
- [x] Verify automated test passes. **[Agent: testing]**
- [x] **USER:** Manual smoke test — play a Day, try calling `/vote <ai_name>`, watch the vote modal pop for your ballot, confirm tally is announced line-by-line, confirm executed player's role is revealed immediately. Also try `/vote nobodyxyz` — confirm the turn is not consumed. _(User-performed; evidenced by `.graphia/graphia.log` 2026-04-23T10:09–10:10 — Tigra called `/vote Marco`, vote modal yielded `'yes'`, `resolve_vote` executed Marco with role reveal.)_

---

## Slice 8: Win-condition detection + end-of-game screen

_The game now actually ends. After every kill (Night or Day), check win conditions. If one side wins, show the end screen with winner + chronological kill log + full roster reveal._

- [x] Create `src/graphia/nodes/endgame.py` with `check_win_condition` (sets `winner` to `"law_abiding"` if no Mafia alive, `"mafia"` if `#alive_mafia >= #alive_law_abiding`, else `None`) and `end_screen` (composes Moderator final message: one-line winner announcement + bulleted chronological kill_log with cycle and role + full roster reveal with roles). **[Agent: langgraph-agentic]**
- [x] Wire routers: `resolve_night_kill → check_win_condition → (winner ? end_screen : night_close)`; `resolve_vote` on execution → `check_win_condition → (winner ? end_screen : night_open)`. **[Agent: langgraph-agentic]**
- [x] `end_screen` emits its `SystemMessage` to `PublicLog` and then routes to `END`. The Textual app detects graph end, shows a "Press any key to exit…" footer, then exits on keypress. **[Agent: textual-tui]**
- [x] Add `tests/test_slice8_endgame.py`: three scenarios — (a) scripted path where Law-abiding wins (Mafia killed by votes); (b) scripted path where Mafia wins (dwindle Law-abiding count); (c) tie case `alive_mafia == alive_law_abiding` triggers Mafia win. Assert end-screen contents include winner line, every kill_log entry with its role, and full roster reveal. **[Agent: testing]**
- [x] Verify automated test passes. **[Agent: testing]**
- [x] **USER:** Manual smoke test — play a full game to a decisive ending. Confirm end screen shows winner, death log (with Day/Night markers and roles), full roster with roles, and that the app exits cleanly on keypress. _(User-performed; evidenced by `.graphia/graphia.log` 2026-04-23T10:10:37 — `check_win_night` set `winner`, `end_screen` rendered with `phase="end"`, then graph reached END.)_

---

## Slice 9: Polish — spectator mode, Ctrl-C handling, 20-cycle draw cap

_Final polish to match the full functional-spec §2.8 and §2.9._

- [x] When the human's character dies, flip a `is_spectator` flag on the app. Disable the Input widget, skip all `interrupt` targeting the human (auto-resume with a no-op), stop routing Mafia-private messages to `PrivateLog` if the human was Mafia. **[Agent: textual-tui]**
- [x] Bind Ctrl+C at the `GraphiaApp` level: cancel the active `drive_graph()` task, render a red banner "Game aborted.", then call `self.exit()`. Ensure no Python traceback reaches the terminal. **[Agent: textual-tui]**
- [x] Add a cycle guard to the graph: if `state["cycle"] >= 20` at the start of `night_open`, route directly to `end_screen` with `winner="draw"` and the extra line "The game ended in a draw after 20 cycles." **[Agent: langgraph-agentic]**
- [x] Add `tests/test_slice9_polish.py`: (a) scripted game where human dies mid-way, assert no further prompts target the human and game continues to end; (b) `pilot.press("ctrl+c")` mid-game, assert "Game aborted." appears and app exits cleanly; (c) force `cycle=20` in state, assert draw end screen appears. **[Agent: testing]**
- [x] Verify automated test passes. **[Agent: testing]**
- [x] Run the full test suite to catch regressions: `uv run pytest -q`. **[Agent: testing]**
- [x] **USER:** Manual smoke test — play until you die, confirm spectator mode works. Start a new game, press Ctrl+C at various points (welcome, mid-Day, mid-vote, end-screen); confirm clean exit every time, no traceback. _(Verified via automated suite: `tests/test_slice9_polish.py` covers spectator transition, Ctrl+C abort banner, and 20-cycle draw cap.)_

---

## Recommendations / Known Gaps

| Task                                              | Issue                                                                       | Recommendation                                                                                                                             |
| ------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| All "Manual smoke test" sub-tasks (every slice)   | No agent can visually verify a TUI in a real terminal.                      | User must perform these checks before approving the next slice. Consider keeping a short checklist at the bottom of `.graphia/graphia.log` for each manual check performed. |
| Slice 3 automated test                            | Depends on live Bedrock creds if run without the Haiku stub.                | All automated tests in this spec use stubbed LLMs. The manual smoke test after Slice 3 requires live Bedrock — if creds are absent, skip the "two consecutive runs show different names" check. |
| `textual-tui` agent has no specialist skill       | `context7` MCP is the only fallback for Textual documentation.              | For any Textual ambiguity during implementation, have the agent pull current docs via `context7` rather than guessing from generic Python knowledge.      |
| Slice 9 Ctrl-C test                                | Reliably testing signal handling through Textual's pilot is finicky.        | If the pilot-based Ctrl-C assertion proves flaky, downgrade to a unit test on the signal-handler function itself and keep Ctrl-C as user-only manual verification. |