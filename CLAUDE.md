# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Graphia** — a single-player console Mafia game built as a personal reference implementation of advanced LangGraph patterns (multi-agent orchestration, private per-agent state, human-in-the-loop interrupts, async streaming). The package lives in `src/graphia/`; entry point is `python -m graphia`.

All code lives under `src/graphia/`; new work should go there unless explicitly told otherwise.

## Running and testing

- **Prefer `make <target>` for any action that has one.** The repo-root `Makefile` is the canonical task-runner (it wraps `./tf`, the container runtime, and `aws`). When an action you need maps to a target, use it instead of re-deriving the raw commands — e.g. `make build` / `make run` / `make play` (local) / `make play-remote` (deployed), `make deploy` / `make redeploy` / `make destroy`, `make tf-plan` / `make tf-apply`, `make build-lambdas`, `make verify-pipeline`, `make inspect-diary`. Run `make help` for the full list. Fall back to raw commands only when no target fits.
- **Run the game:** `uv run python -m graphia` (Textual TUI; needs a real terminal, not PyCharm's "Run" console). Equivalently `make play`.
- **Tests:** `uv run pytest -q` (fast, all-mocked) — the autouse `safe_llm` fixture in `tests/conftest.py` patches every LLM call site to fail loudly if a test forgets to install `fake_small` / `fake_large*`. Real Bedrock must never be reached from the suite.
- **Single test:** `uv run pytest tests/test_slice7_vote.py::test_name -q`.
- **AI-quality evals (real model, NOT in `pytest`):** three make-gated harnesses that reach a real gameplay model and live outside the mocked suite. `make eval-dialogue` plays N real games with a scripted human and scores AI Day-speech repetition (lexical near-dup); `make repetition-experiment` runs the rigorous paired A/B that ranks prompt/window/temperature fixes with bootstrap CIs (design + results: `context/spec/009-ai-collusion-awareness/repetition-experiment-design.md`); `make blunder-eval` (spec 011) plays N games against a chosen provider (`ARGS="--provider ollama|bedrock --games N [--note '…']"`), counts the AI **self-consistency blunder** family (self-vote / Mafioso peer-vote / third-person self-talk, each split initiation-vs-Yes for votes) **plus** repetition — each rate with a **Wilson CI** — and appends one **provenance-stamped record** (git commit + dirty flag, model digests, settings, `metrics_version`) to the **repo-committed quality ledger** `evals/blunder-ledger.yaml` (append-only; run per provider for comparable records — "baby MLOps"; contract in `evals/README.md`). The Bedrock path costs tokens; the Ollama path is free. Use `eval-dialogue`/`repetition-experiment` to A/B a dialogue change, and `blunder-eval` to grow the tracked baseline over time. `make view-ledger` (spec 012) opens a standalone Textual viewer over that same ledger — a scrollable read-only table of the records the harnesses write (no model/AWS; `ARGS="--path <file>"` for an alternate ledger).
- **Determinism posture:** there is no seed env var. Mechanical-RNG decisions (role deal, day-speech order, tie-breaks) use module-global `random` and vary across runs; LLM-driven decisions (AI dialogue, name generation) are inherently non-reproducible. See architecture §6.
- **Required env (in `.env`):** `AWS_BEARER_TOKEN_BEDROCK` or `AWS_PROFILE` (one is required for Bedrock), `AWS_REGION` (default `us-east-1`), `GRAPHIA_LOG_FILE`, `GRAPHIA_CHECKPOINT_DIR`.
- **Dependencies:** `uv add <pkg>` into `pyproject.toml`. Do **not** use PEP 723 inline `# /// script` headers; all code goes through the standard uv project flow.

## Architecture (the parts that span files)

**One Textual app drives one LangGraph `StateGraph`.** `GraphiaApp` (`src/graphia/ui/app.py`) owns the asyncio event loop. It launches a worker (`_drive`) that calls `drive_graph()` (`src/graphia/driver.py`), which iterates `graph.stream(..., stream_mode="updates")` *inside `asyncio.to_thread`* so synchronous LangGraph execution does not block Textual's loop. Phase 3 (per the roadmap) will switch to native `graph.astream`; until then, do not introduce `await graph.invoke` directly.

**Graph topology lives in `src/graphia/graph.py`.** Nodes are grouped by phase under `src/graphia/nodes/{setup,night,day,endgame}.py`. The same pure read-only `check_win_condition` is registered twice (`check_win_night`, `check_win_day`) so each fan-out site owns its own conditional edge. Adding a new phase usually means: add nodes in the right `nodes/<phase>.py`, re-export through `nodes/__init__.py`, and wire edges in `build_graph`.

**State shape is in `src/graphia/state.py`** — one `GameState` `TypedDict` with reducers (`add_messages` for `messages`, `operator.add` for `kill_log`, replace for everything else). Per-player data lives in `players: dict[str, PlayerState]`. "Private" channels are a *convention*: messages carry `additional_kwargs={"private_to": player_id}` and the UI silently drops anything not addressed to the human.

**Human-in-the-loop uses `interrupt()`** as the **first statement** of any human-facing node (interrupt replay re-executes the whole node, so any pre-work would happen twice on resume). The driver pumps `Command(resume=<value>)` after Textual collects input via `_request_resume`, dispatched by `payload["kind"]` (`"name"`, `"day_turn"`, `"point"`, `"vote"`).

**Checkpointing:** `SqliteSaver` writes to `./.graphia/checkpoints/<thread_id>.sqlite`. The DB connection is opened directly (not via the context manager) because the graph owns its lifetime for the whole game. There is no cross-session save/load — each run is a fresh thread id; old files are safe to delete.

**LLMs:** Two capability tiers in `src/graphia/llm.py` — `get_large()` (gameplay: AI dialogue, votes, pointing) and `get_small()` (mechanical, e.g. roster name generation). The tier names are **model-agnostic on purpose**; both currently resolve to **Amazon Nova** — Nova Pro (large) and Nova Lite (small) — via `langchain-aws` `ChatBedrockConverse` in **`us-east-1`**. ADR-003 swapped Claude→Nova long ago; the old `get_sonnet`/`get_haiku` names (and any "Sonnet 4.5 / Haiku 4.5 / eu-north-1" references) are **retired and were misleading** — there is no Claude in the gameplay path, local or remote. Behavioral variation comes from prompts and temperature, **not** from adding more models. Pydantic schemas (`Roster`, `Pointing`, `Ballot`, `DayAction`) are kept *flat* with primitive fields because Bedrock Converse rejects discriminated unions.

**Logs vs UI:** `graph.stream` deltas go to `GRAPHIA_LOG_FILE` (JSONL, via `StreamTraceLogger`). Nothing diagnostic is ever printed to the Textual panes — exceptions surface as a banner pointing at the log file path.

## AWOS workflow (slash commands)

This project uses **AWOS** (provectus AI workflow) for spec-driven development. The core chained commands live under `.awos/commands/` (mirrored as `/awos:<name>` slash commands); the three logging/learning skills (ADR, change-request, tutorial) are no longer local — they're provided by the **`buddah` plugin** (from the AWOS marketplace) and invoked as `/buddah:<name>`. The core chain:

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
| Tutorial       | `/buddah:tutorial`   | completed spec + prior `concepts.md`s + git history | `context/tutorials/NNN-<slug>/{tutorial.md, concepts.md}` |

(`/awos:tutorial` is shown as the chain's last stage for continuity, but it's the **buddah** skill `/buddah:tutorial`.)

Two **optional logging skills** sit alongside the main flow — both from the **`buddah` plugin** — invoked from the chained commands (or directly):

- **`/buddah:change-request`** — logs a CR under `context/change-requests/NNN-<slug>.md` whenever a previously-agreed *requirement* shifts (scope, roadmap order, success criteria). Auto-offered after `/awos:product`, `/awos:roadmap`, and `/awos:spec` updates.
- **`/buddah:adr`** — logs an Architecture Decision Record under `context/adr/NNN-<slug>.md` whenever an *architectural* choice is made or revised (tech stack, deployment target, region, data store, security posture, vendor lock-in trade-off). Auto-offered after `/awos:architecture` and `/awos:tech`, and listed as a follow-up suggestion in `/buddah:change-request` when the change touches architecture. ADRs capture *Context, Alternatives, Decision, Rationale, Consequences*; they live below the architecture doc and survive its rewrites.

> **Note:** the project's own local copies of these three commands were removed once the `buddah` plugin superseded them — only `spec`, `tech`, `tasks`, `implement`, `verify`, `roadmap`, `product`, `architecture`, `hire` remain under `.awos/commands/`.

Important rules baked into these commands:

- **`/awos:implement` does not write code.** It identifies the next unchecked task in the lowest-numbered spec dir, extracts the `**[Agent: <name>]**` tag from the task line, and dispatches to that subagent with full context. Mark only the specific sub-item complete; promote the parent to `[x]` only when all its children are.
- **`/awos:tasks` enforces vertical slicing** — every slice must leave the app runnable, not horizontal "do all DB then all API" layers. Each sub-task gets `**[Agent: <agent-name>]**`.
- **`/awos:hire` uses the `awos-recruitment` MCP** (configured in `.mcp.json`) to install skills/MCPs/agents from the registry, then generates agent files from `.awos/templates/agent-template.md` only for roles with no registry hit.

## Specialist subagents (delegate, don't reimplement)

`.claude/agents/` defines five agents. Use them via the Agent tool when work clearly falls in their lane:

- **`langgraph-agentic`** — StateGraph design, reducers, `interrupt()`/resume, `SqliteSaver`, `ChatBedrockConverse` (us-east-1 / `us.` inference profile), and the **AgentCore application-side code** (Runtime entrypoint, Gateway-fronted diary tools, AgentCore Memory schemas for per-game diaries and long-term cross-game stats). Bundled `langgraph-agentcore` skill is **active** as of ADR 001; the prior "Graphia is local-only" stance is gone — both local and remote modes are first-class.
- **`textual-tui`** — Textual app/widgets, async event-loop integration, vote-locks-chat transitions. No specialist Textual skill is installed; agent uses the `context7` MCP to fetch current Textual docs when uncertain.
- **`python-backend`** — uv/pyproject, type hints, asyncio patterns, `python-dotenv`, non-LangGraph/non-UI Python.
- **`testing`** — pytest suites, fixtures, mocking Bedrock at the `ChatBedrockConverse` boundary, monkeypatching `_shuffle_order` / pointing helpers for deterministic test trajectories, Textual `App.run_test()` snapshots.
- **`terraform-aws`** — Research → Design → Implement → Validate workflow for Terraform code that provisions Graphia's AgentCore Runtime / Gateway / Memory / Observability resources in `us-east-1`. Bundled `terraform-conventions` skill enforces the configured IaC house style. Uses the `terraform-mcp-server` (Registry lookup), `aws-knowledge-mcp-server` (AWS docs / Well-Architected), and `aws-api-mcp-server` (live API ground-truth) MCPs. Always plans before applying.

## Test conventions worth knowing

- The `safe_llm` autouse fixture (`tests/conftest.py`) replaces `get_small` / `get_large` at every call site (`graphia.nodes.setup`, `graphia.nodes.night`, `graphia.nodes.day`) with a `_LoudFailureLLM`. If you add a new module that calls an LLM, extend `safe_llm` to patch it too — otherwise a forgotten stub will fall through to real boto3 and hang pytest teardown on retry loops.
- Per-test fixtures (`fake_small`, `fake_large`, `fake_large_pointing`, `fake_large_day`, `dynamic_night_pointing`, `target_human_pointing`) re-monkeypatch the same surface; they run after `safe_llm`.
- The slice-numbered test files (`test_slice2_roster.py` … `test_slice9_polish.py`) map 1:1 onto the vertical slices in `context/spec/001-playable-skeleton/tasks.md`.
