# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Graphia** — a single-player console Mafia game built as a personal reference implementation of advanced LangGraph patterns (multi-agent orchestration, private per-agent state, human-in-the-loop interrupts, async streaming). The package lives in `src/graphia/`; entry point is `python -m graphia`.

The repo also predates Graphia: `adventure.py`, `support_agent.py`, `main.py`, and `TUTORIAL.md` are unrelated learning artifacts kept for reference. New work should go inside `src/graphia/` unless explicitly told otherwise.

## Running and testing

- **Run the game:** `uv run python -m graphia` (Textual TUI; needs a real terminal, not PyCharm's "Run" console).
- **Tests:** `uv run pytest -q` (fast, all-mocked) — the autouse `safe_llm` fixture in `tests/conftest.py` patches every LLM call site to fail loudly if a test forgets to install `fake_haiku` / `fake_sonnet*`. Real Bedrock must never be reached from the suite.
- **Single test:** `uv run pytest tests/test_slice7_vote.py::test_name -q`.
- **Deterministic seed:** `GRAPHIA_SEED=1234 uv run python -m graphia` for reproducible role assignment and tie-breaks.
- **Required env (in `.env`):** `AWS_BEARER_TOKEN_BEDROCK` (required), `AWS_REGION` (default `eu-north-1`), `GRAPHIA_LOG_FILE`, `GRAPHIA_CHECKPOINT_DIR`, `GRAPHIA_SEED`.
- **Dependencies:** `uv add <pkg>` into `pyproject.toml`. Do **not** use PEP 723 inline `# /// script` headers — even though `main.py` (a leftover) still uses one, all new code goes through the standard uv project flow.

## Architecture (the parts that span files)

**One Textual app drives one LangGraph `StateGraph`.** `GraphiaApp` (`src/graphia/ui/app.py`) owns the asyncio event loop. It launches a worker (`_drive`) that calls `drive_graph()` (`src/graphia/driver.py`), which iterates `graph.stream(..., stream_mode="updates")` *inside `asyncio.to_thread`* so synchronous LangGraph execution does not block Textual's loop. Phase 3 (per the roadmap) will switch to native `graph.astream`; until then, do not introduce `await graph.invoke` directly.

**Graph topology lives in `src/graphia/graph.py`.** Nodes are grouped by phase under `src/graphia/nodes/{setup,night,day,endgame}.py`. The same pure read-only `check_win_condition` is registered twice (`check_win_night`, `check_win_day`) so each fan-out site owns its own conditional edge. Adding a new phase usually means: add nodes in the right `nodes/<phase>.py`, re-export through `nodes/__init__.py`, and wire edges in `build_graph`.

**State shape is in `src/graphia/state.py`** — one `GameState` `TypedDict` with reducers (`add_messages` for `messages`, `operator.add` for `kill_log`, replace for everything else). Per-player data lives in `players: dict[str, PlayerState]`. "Private" channels are a *convention*: messages carry `additional_kwargs={"private_to": player_id}` and the UI silently drops anything not addressed to the human.

**Human-in-the-loop uses `interrupt()`** as the **first statement** of any human-facing node (interrupt replay re-executes the whole node, so any pre-work would happen twice on resume). The driver pumps `Command(resume=<value>)` after Textual collects input via `_request_resume`, dispatched by `payload["kind"]` (`"name"`, `"day_turn"`, `"point"`, `"vote"`).

**Checkpointing:** `SqliteSaver` writes to `./.graphia/checkpoints/<thread_id>.sqlite`. The DB connection is opened directly (not via the context manager) because the graph owns its lifetime for the whole game. There is no cross-session save/load — each run is a fresh thread id; old files are safe to delete.

**LLMs:** Two singletons in `src/graphia/llm.py` — Sonnet 4.5 (gameplay) and Haiku 4.5 (mechanical, e.g. roster name generation). Both via `langchain-aws` `ChatBedrockConverse` against the `eu-north-1` Bedrock inference profile. Behavioral variation comes from prompts and temperature, **not** from adding more models. Pydantic schemas (`Roster`, `Pointing`, `Ballot`, `DayAction`) are kept *flat* with primitive fields because Bedrock Converse rejects discriminated unions.

**Logs vs UI:** `graph.stream` deltas go to `GRAPHIA_LOG_FILE` (JSONL, via `StreamTraceLogger`). Nothing diagnostic is ever printed to the Textual panes — exceptions surface as a banner pointing at the log file path.

## AWOS workflow (slash commands)

This project uses **AWOS** (provectus AI workflow) for spec-driven development. The chained commands under `.awos/commands/` (mirrored as `/awos:<name>` slash commands) are:

| Stage          | Command              | Reads                                               | Writes                                              |
| -------------- | -------------------- | --------------------------------------------------- | --------------------------------------------------- |
| Product        | `/awos:product`      | —                                                   | `context/product/product-definition.md`             |
| Roadmap        | `/awos:roadmap`      | product-definition                                  | `context/product/roadmap.md`                        |
| Architecture   | `/awos:architecture` | product-definition + roadmap                        | `context/product/architecture.md`                   |
| Hire agents    | `/awos:hire`         | architecture + tech specs                           | `.claude/agents/*.md`, installs skills/MCPs         |
| Functional spec| `/awos:spec`         | roadmap (next `[ ]`)                                | `context/spec/NNN-<slug>/functional-spec.md`        |
| Tech spec      | `/awos:tech`         | functional-spec + architecture + code               | `context/spec/NNN-<slug>/technical-considerations.md`|
| Tasks          | `/awos:tasks`        | functional-spec + technical-considerations          | `context/spec/NNN-<slug>/tasks.md` (vertical slices)|
| Implement      | `/awos:implement`    | next `[ ]` task — delegates to assigned subagent    | code + flips `[ ]` → `[x]` in `tasks.md`            |
| Verify         | `/awos:verify`       | tasks all `[x]` + functional-spec acceptance        | flips Status → Completed in spec + roadmap          |

Important rules baked into these commands:

- **`/awos:implement` does not write code.** It identifies the next unchecked task in the lowest-numbered spec dir, extracts the `**[Agent: <name>]**` tag from the task line, and dispatches to that subagent with full context. Mark only the specific sub-item complete; promote the parent to `[x]` only when all its children are.
- **`/awos:tasks` enforces vertical slicing** — every slice must leave the app runnable, not horizontal "do all DB then all API" layers. Each sub-task gets `**[Agent: <agent-name>]**`.
- **`/awos:hire` uses the `awos-recruitment` MCP** (configured in `.mcp.json`) to install skills/MCPs/agents from the registry, then generates agent files from `.awos/templates/agent-template.md` only for roles with no registry hit.

## Specialist subagents (delegate, don't reimplement)

`.claude/agents/` defines four Graphia-specific agents. Use them via the Agent tool when work clearly falls in their lane:

- **`langgraph-agentic`** — StateGraph design, reducers, `interrupt()`/resume, `SqliteSaver`, `ChatBedrockConverse`. Ignore the AgentCore-specific guidance from its bundled `langgraph-agentcore` skill; Graphia is local-only.
- **`textual-tui`** — Textual app/widgets, async event-loop integration, vote-locks-chat transitions. No specialist Textual skill is installed; agent uses the `context7` MCP to fetch current Textual docs when uncertain.
- **`python-backend`** — uv/pyproject, type hints, asyncio patterns, `python-dotenv`, non-LangGraph/non-UI Python.
- **`testing`** — pytest suites, fixtures, mocking Bedrock at the `ChatBedrockConverse` boundary, deterministic runs via `GRAPHIA_SEED`, Textual `App.run_test()` snapshots.

## Test conventions worth knowing

- The `safe_llm` autouse fixture (`tests/conftest.py`) replaces `get_haiku` / `get_sonnet` at every call site (`graphia.nodes.setup`, `graphia.nodes.night`, `graphia.nodes.day`) with a `_LoudFailureLLM`. If you add a new module that calls an LLM, extend `safe_llm` to patch it too — otherwise a forgotten stub will fall through to real boto3 and hang pytest teardown on retry loops.
- Per-test fixtures (`fake_haiku`, `fake_sonnet`, `fake_sonnet_pointing`, `fake_sonnet_day`, `dynamic_night_pointing`, `target_human_pointing`) re-monkeypatch the same surface; they run after `safe_llm`.
- The slice-numbered test files (`test_slice2_roster.py` … `test_slice9_polish.py`) map 1:1 onto the vertical slices in `context/spec/001-playable-skeleton/tasks.md`.
