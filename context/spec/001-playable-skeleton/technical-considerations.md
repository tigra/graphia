# Technical Specification: Playable Skeleton

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** (TBD)

---

## 1. High-Level Technical Approach

A single Textual app owns the asyncio event loop and drives a single LangGraph `StateGraph` that represents the entire Mafia game. Every gameplay phase — name entry, roster introduction, Night pointing, Night resolution, Day round-robin speaking, Day vote — is modeled as one or more graph nodes. Human input happens via `interrupt()`/`Command(resume=…)`; AI decisions happen via a Bedrock `ChatBedrockConverse` instance using `with_structured_output(<Pydantic schema>)` so every AI action is extracted as a typed object rather than free text. Per-player identity (role, alive/dead, Mafia team) lives in a `players: dict[player_id, PlayerState]` sub-structure in graph state. A `SqliteSaver` checkpointer enables interrupt/resume and crash recovery within a single run. Per-node streaming updates (`graph.stream(stream_mode="updates")`) are written as JSONL to the log file — never to the console.

Phase 1 stays **synchronous**: the Textual app `await`s `asyncio.to_thread(lambda: graph.stream(...))` for each super-step, appends the resulting messages to the chat-log widget, and loops until the graph reaches `END`. The asyncio-native variant (`graph.astream`, per-player tasks, async message bus) is deferred to Phase 3.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Architecture changes

No new external services. Two amendments to `context/product/architecture.md` landed alongside this spec:
1. **Two-model LLM tier:** Sonnet 4.5 primary for gameplay; Haiku 4.5 secondary for mechanical/short calls (start-of-game name generation).
2. **Structured-output contract:** AI player decisions are emitted via Pydantic schemas on `ChatBedrockConverse.with_structured_output`.

### 2.2 Source layout

New package, created as a directory alongside the existing `adventure.py`:

| Path                                   | Responsibility                                                                                   |
| -------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `src/graphia/__init__.py`              | Package marker.                                                                                  |
| `src/graphia/__main__.py`              | Entry point; loads `.env`, reconfigures UTF-8, instantiates the Textual app, runs it.            |
| `src/graphia/config.py`                | Reads env vars (`AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION`, `GRAPHIA_LOG_FILE`, `GRAPHIA_SEED`). Validates them; raises actionable errors. |
| `src/graphia/state.py`                 | `GameState` TypedDict, `PlayerState` dataclass, reducers. See §2.3.                              |
| `src/graphia/llm.py`                   | Lazy constructors for the two `ChatBedrockConverse` instances (Sonnet + Haiku) and the Pydantic response schemas used by `with_structured_output`. |
| `src/graphia/prompts.py`               | All system prompts (Moderator narration, AI player role prompts, name-gen prompt). Constants only. |
| `src/graphia/graph.py`                 | Graph assembly: nodes, edges, routers, checkpointer wiring. See §2.4.                            |
| `src/graphia/nodes/setup.py`           | Nodes: `collect_name`, `generate_roster`, `assign_roles`, `introduce_roster`, `reveal_role`.     |
| `src/graphia/nodes/night.py`           | Nodes: `first_night_mafia_intros`, `night_open`, `mafia_pointing`, `resolve_night_kill`, `night_close`. |
| `src/graphia/nodes/day.py`             | Nodes: `day_open`, `day_turn`, `vote_prompt`, `collect_votes`, `resolve_vote`, `day_close`.       |
| `src/graphia/nodes/endgame.py`         | Nodes: `check_win_condition`, `end_screen`.                                                      |
| `src/graphia/ui/app.py`                | Textual `App` subclass. Owns widgets, drives graph stepping.                                     |
| `src/graphia/ui/widgets.py`            | `PublicLog`, `PrivateLog`, `InputBar`, `VoteModal`, `PointingModal`.                              |
| `src/graphia/logging.py`               | JSONL streaming-trace writer.                                                                    |
| `tests/…`                              | pytest suite (see §4).                                                                           |

The existing `adventure.py` and `main.py` are untouched.

### 2.3 Data model (graph state)

A single `TypedDict` `GameState`:

| Field             | Type                                           | Reducer            | Notes                                                                                  |
| ----------------- | ---------------------------------------------- | ------------------ | -------------------------------------------------------------------------------------- |
| `messages`        | `list[AnyMessage]`                             | `add_messages`     | Full public Moderator + AI transcript. Drives the public chat-log widget.              |
| `players`         | `dict[str, PlayerState]` (keyed by player id)  | `replace`          | Full roster. Mutations go through node-local copies returned in node updates.          |
| `human_id`        | `str`                                          | `replace`          | Which key in `players` is the human.                                                   |
| `phase`           | `Literal["setup","night","day","end"]`         | `replace`          | Current top-level phase.                                                               |
| `cycle`           | `int`                                          | `replace`          | 1-based Day/Night cycle count; increments on Day→Night transition.                     |
| `night_picks`     | `dict[mafia_id, target_id]`                    | `replace`          | Reset at each Night open.                                                              |
| `day_order`       | `list[player_id]`                              | `replace`          | Shuffled order for the current Day round.                                              |
| `day_turn_index`  | `int`                                          | `replace`          | Pointer into `day_order`.                                                              |
| `day_rounds`      | `int`                                          | `replace`          | Full round-robin rounds completed in the current Day (for the 6-round safety cap).     |
| `day_votes_called`| `int`                                          | `replace`          | Votes initiated in the current Day (hard cap = 3).                                      |
| `active_vote`     | `ActiveVote \| None`                           | `replace`          | `{initiator, target, ballots: dict[voter_id, "yes"\|"no"], pending: list[voter_id]}`.   |
| `kill_log`        | `list[KillRecord]`                             | `operator.add`     | `{cycle, name, cause: "night"\|"execution", role}`. Role filled in at reveal time.      |
| `winner`          | `Literal["law_abiding","mafia","draw"] \| None`| `replace`          | Set by `check_win_condition`.                                                          |

`PlayerState` dataclass:

| Field            | Type                                    | Notes                                                         |
| ---------------- | --------------------------------------- | ------------------------------------------------------------- |
| `id`             | `str` (UUID4 at creation)               | Stable identity for the life of the game.                     |
| `name`           | `str`                                   | Display name.                                                 |
| `role`           | `Literal["mafia","law_abiding"]`        | Never changes after assignment.                               |
| `is_human`       | `bool`                                  | Exactly one player has this `True`.                           |
| `is_alive`       | `bool`                                  | Flips to `False` at Night kill / Day execution.               |

Checkpointing: `SqliteSaver.from_conn_string(f".graphia/checkpoints/{thread_id}.sqlite")`. `thread_id` = ISO8601 UTC timestamp at launch, e.g. `2026-04-22T18-30-05`.

### 2.4 Graph topology

```
START
  │
  ▼
collect_name ──(interrupt for human name)
  │
  ▼
generate_roster (Haiku call → 6 AI names)
  │
  ▼
assign_roles (seeded RNG: 2 mafia + 5 law-abiding, shuffled)
  │
  ▼
introduce_roster (public Moderator message)
  │
  ▼
reveal_role (private message to human)
  │
  ▼
first_night_mafia_intros (private teammate list to each mafia; skipped after cycle 1)
  │
  ▼
night_open ──► mafia_pointing (fan-out per alive mafia)
                │
                ▼
           resolve_night_kill (tally picks → victim, update kill_log, flip is_alive)
                │
                ▼
           check_win_condition ── winner? ──► end_screen ──► END
                │ no
                ▼
          night_close → day_open (announces victim + victim's role)
                │
                ▼
          shuffle day_order → day_turn
                │
                ▼
          day_turn ── speak? ──► append message → advance turn_index
                   │
                   └─ vote? ──► vote_prompt → collect_votes (fan-out) → resolve_vote
                                   │
                                   ├─ executed? ──► reveal role, kill_log, check_win_condition → (winner? end : night_open)
                                   └─ failed?  ──► day_votes_called++, reshuffle day_order, back to day_turn
  │
  ▼ (round ends without vote)
day_rounds++ ── hit safety caps? (3 votes used OR 6 rounds done) ──► night_open
                else reshuffle day_order → day_turn
```

**Conditional edges (routers):**

- `route_after_night_kill`: `winner is not None → end_screen ; else → night_close`
- `route_day_turn`: `active_vote is None and speak → day_turn ; active_vote set → collect_votes`
- `route_after_vote`: `executed and winner → end_screen ; executed → night_open ; failed and caps hit → night_open ; failed → day_turn`

### 2.5 Node-level logic highlights

- **`collect_name`** uses `interrupt({"kind":"name"})` as its first statement; the Textual app resumes it via `graph.stream(Command(resume=<name>))` after the InputBar emits a Submit event.
- **`generate_roster`** calls the Haiku instance with `with_structured_output(Roster)` where `Roster: BaseModel { names: list[str] }`. Validates: exactly 6 names, all distinct, all non-empty.
- **`assign_roles`** uses a seeded `random.Random(config.seed)` to shuffle `[M, M, L, L, L, L, L]`, zipping against the roster + human slot.
- **`mafia_pointing`** has two paths: the human-mafia path uses `interrupt({"kind":"point","options":[...law-abiding names]})`; AI-mafia paths call Sonnet with `with_structured_output(Pointing)` where `Pointing: BaseModel { target_id: str }` and the system prompt includes the alive-law-abiding roster. Validator rejects non-alive-law-abiding ids and retries once.
- **`resolve_night_kill`** counts `Counter(night_picks.values())`, picks the mode; on tie, `rng.choice(tied)`.
- **`day_turn`** for the human: `interrupt({"kind":"day_turn","options":[...alive names]})`; Textual accepts free text OR `/vote <name>` from InputBar. For AI: Sonnet with `with_structured_output(DayAction)` where `DayAction = Union[Speak(text: str), CallVote(target_id: str)]` (discriminated union; Bedrock Converse handles tagged unions fine when given a JSON schema).
- **`collect_votes`** iterates `pending` list: for the human, `interrupt({"kind":"vote","target":target_name})`; for AI, Sonnet with `with_structured_output(Ballot)` where `Ballot: BaseModel { yes: bool }`. Each vote is immediately announced via a public `SystemMessage` before moving on.
- **`resolve_vote`**: `yes_count > len(alive_players) / 2` executes (strict majority). On execution, roles revealed immediately via a Moderator `SystemMessage`; `is_alive=False`; `kill_log` appended.
- **`day_open`**: emits a Moderator `SystemMessage` that, on cycles ≥ 2, includes the **role reveal of the last night's victim**. On cycle 1, no reveal (nobody killed yet).
- **`end_screen`** composes the summary (winner, chronological kill_log with roles, full roster reveal). Written to `messages`, then graph routes to `END`. The Textual app waits for any keypress, then exits.

### 2.6 UI components

| Widget           | Type                               | Responsibility                                                                                    |
| ---------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------- |
| `PublicLog`      | `RichLog` (bottom-scrolling)        | Renders every public Moderator / AI `SystemMessage`/`AIMessage` as it streams in.                 |
| `PrivateLog`     | `RichLog` (smaller, styled border)  | Renders the human's private-only messages: role reveal, Mafia teammate list, night-pointing lead-in. |
| `InputBar`       | `Input` (bottom)                    | Single input line. Interpreted contextually: name on `collect_name`, free text or `/vote <name>` on `day_turn`, `y`/`n` on vote. Disabled when it's not the human's turn. |
| `PointingModal`  | `ModalScreen` with `Select` list    | Shown only during a Night when the human is Mafia and alive. Lists alive Law-abiding names.       |
| `VoteModal`      | `ModalScreen` with Yes/No buttons   | Shown when the human is polled to vote.                                                            |

The app implements a single coroutine `drive_graph()` that runs `async for event in astream_wrapper(graph)` — where `astream_wrapper` wraps `graph.stream` via `asyncio.to_thread` and yields events one at a time. The loop dispatches each event:
- `messages` updates → append to `PublicLog` / `PrivateLog`.
- `interrupt` payloads → show the relevant modal or focus InputBar, await user input, then resume with `Command(resume=...)`.

### 2.7 Logging & observability

- `logging.py` provides `StreamTraceLogger(log_path)` with one method `record(event: dict)`. Each super-step's update payload is serialized as one JSONL line (timestamp, node, keys-updated, message types).
- Unhandled exceptions inside nodes are caught at the `drive_graph()` boundary, logged with full traceback to the same JSONL file, and surfaced via an `ErrorModal` pointing at the log path.
- Nothing ever writes to stdout/stderr once Textual has taken over the terminal.

### 2.8 Dependencies

Additions to `pyproject.toml` via `uv add`:

- `textual>=0.80` — TUI framework.
- `langgraph-checkpoint-sqlite` — provides `SqliteSaver`.
- `pydantic>=2.7` (already a transitive dep of langchain-core; pin explicit for clarity).
- `pytest>=8`, `pytest-asyncio>=0.23`, `pytest-textual-snapshot` — testing (dev group).

`requires-python` stays at `>=3.10`.

### 2.9 Configuration

Env vars consumed at startup (via `config.py`):

| Name                        | Required | Default                         | Purpose                                      |
| --------------------------- | -------- | ------------------------------- | -------------------------------------------- |
| `AWS_BEARER_TOKEN_BEDROCK`  | yes      | —                               | Bedrock auth.                                |
| `AWS_REGION`                | no       | `us-west-2`                     | Bedrock region.                              |
| `GRAPHIA_LOG_FILE`          | no       | `./.graphia/graphia.log`        | JSONL streaming trace file.                  |
| `GRAPHIA_SEED`              | no       | current time (ns) cast to int   | Seeds `random.Random` for reproducibility.   |
| `GRAPHIA_CHECKPOINT_DIR`    | no       | `./.graphia/checkpoints`        | Sqlite checkpoint directory.                 |

---

## 3. Impact and Risk Analysis

### System Dependencies

- **AWS Bedrock** — Sonnet 4.5 and Haiku 4.5 inference profiles must be available in `us-west-2`. If either is unavailable, the game cannot start.
- **Textual** — requires a UTF-8 terminal with ANSI support and a TTY. Running under PyCharm's "Run" console (non-TTY) will not work; the launcher must detect this and print a clear message directing the user to use a real terminal.
- **LangGraph 1.x** — `SqliteSaver` comes from the separate `langgraph-checkpoint-sqlite` package, not the core `langgraph` wheel. If the user upgrades only `langgraph` without this sibling, checkpointing breaks.

### Potential Risks & Mitigations

| Risk                                                                                                   | Mitigation                                                                                                             |
| ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| **Bedrock Converse rejects complex tool / structured-output schemas.** `ChatBedrockConverse` is known to be strict; tagged unions can fail silently. | Keep all Pydantic fields as primitives (`str`, `bool`, `int`). For the `DayAction` union, use a flat schema `{"kind": Literal["speak","vote"], "text": str \| None, "target_id": str \| None}` instead of a discriminated union. Validate & retry once on validation error; on a second failure, fall back to "speak: <empty>" for AI so the game never stalls. |
| **Textual event loop blocking on sync LangGraph calls.** If a synchronous `graph.stream` pass takes seconds, the UI freezes. | All graph stepping is wrapped in `asyncio.to_thread`. The UI shows a subtle "Moderator is thinking…" status while a step is in flight. |
| **Interrupt replay re-executes a whole node.** If a human-facing node does any pre-work before `interrupt()`, that work happens twice on resume. | Every human-facing node has `interrupt(...)` as its **first statement**. Enforced by code review and by a pytest rule that greps the node files. |
| **Human types `/vote <name>` with a name that doesn't match.** | Fuzzy-match against alive-player names; if no unique match, prompt "No such player. Try again." without consuming the turn. |
| **AI never calls a vote, leading to endless Day.** | The 6-round-without-vote safety cap in the functional spec handles this. Additionally, the AI system prompt nudges toward proposing a vote on rounds ≥ 3 when evidence is weak. |
| **Checkpoint sqlite file accumulates across runs, consuming disk.** | `__main__.py` prunes `.graphia/checkpoints/` entries older than 7 days on startup. Each game is safe to interrupt+resume within its session; we do not promise cross-session resume. |
| **Non-UTF-8 terminal locale.** | `__main__.py` reconfigures `sys.stdin/stdout/stderr` to `encoding="utf-8", errors="replace"` before Textual starts (same workaround already validated in `adventure.py`). |
| **Ctrl-C during a Bedrock call.** `asyncio.to_thread` can swallow `KeyboardInterrupt`. | Textual's default `Ctrl+C` binding sets a cancellation flag that `drive_graph()` checks between super-steps. A "Game aborted." banner renders and the app exits cleanly. |

---

## 4. Testing Strategy

### Unit tests (pure logic, no LLM)

- `tests/test_state.py` — reducer behavior on `GameState`; `PlayerState` invariants.
- `tests/test_night_resolution.py` — `resolve_night_kill` over synthetic `night_picks` maps: unanimous, plurality, tie.
- `tests/test_vote_resolution.py` — `resolve_vote` over synthetic ballots: strict majority, tie-does-not-execute, minimum-and-maximum voter counts.
- `tests/test_win_condition.py` — `check_win_condition` for every boundary: all-mafia-dead, mafia≥law, 1-1, 1-0.
- `tests/test_day_caps.py` — Day ends at 3 votes, 6 no-vote rounds, or successful execution; whichever first.

### Node tests (graph nodes called directly; LLM stubbed)

- Each AI-facing node is tested with a `FakeChatModel` whose `with_structured_output(Schema).invoke(messages)` returns a scripted `Schema` instance.
- Human-facing nodes (`collect_name`, `mafia_pointing` for human Mafia, `day_turn` for human, `collect_votes` when human is polled) are tested by driving `graph.invoke`/`graph.stream` with `Command(resume=<scripted_value>)`.
- Seeded RNG (`GRAPHIA_SEED=42`) ensures deterministic role assignment and tie-breaks.

### End-to-end scenarios (graph + stubbed LLM, Textual headless)

- `tests/e2e/test_law_abiding_win.py` — script the LLM to always point at the same Law-abiding target, vote yes on every vote; verify the game ends with `winner="law_abiding"`.
- `tests/e2e/test_mafia_win.py` — script the LLM to no-op on votes; verify Mafia eventually satisfies the `mafia >= law_abiding` condition.
- `tests/e2e/test_draw_safety_cap.py` — script the LLM to never call votes and always pick in a way that doesn't reduce roster; verify the 20-cycle draw fires.
- `tests/e2e/test_human_mafia_flow.py` — drive the graph as a human Mafioso; assert private-channel messages appear and disappear correctly.

### UI smoke tests

- `tests/ui/test_app_boots.py` — Textual `App.run_test()` harness: app boots, welcome message is shown, InputBar is focused.
- `tests/ui/test_pointing_modal.py` — when `interrupt({"kind":"point", "options":[...]})` fires, the PointingModal opens with exactly those options.

### Deterministic live-LLM smoke (manual, not in CI)

- A single manual test: `uv run python -m graphia` with `GRAPHIA_SEED=1234` and a single known bearer token; a full game should complete in under 2 minutes. Excluded from automated runs to avoid Bedrock cost in CI.

### Coverage bar

- Aim for 100% line coverage on `src/graphia/nodes/**` and `src/graphia/state.py`. UI widgets are covered by smoke tests only. Excluded from coverage: `llm.py` (external boundary), `config.py` (exercised by integration tests).