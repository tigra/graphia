# System Architecture Overview: Graphia

---

## 1. Application & Technology Stack

- **Language & Runtime:** Python 3.10+ (LangGraph 1.x drops 3.9; modern union syntax is useful).
- **Dependency & Project Management:** `uv` with `pyproject.toml` and `uv.lock`. Scripts run via `uv run python -m graphia`; no PEP 723 inline script metadata.
- **Orchestration Framework:** LangGraph 1.x (`StateGraph`, `interrupt()`/`Command(resume=…)`, reducers, structured-output schemas via `with_structured_output`). `ToolNode` and `bind_tools` are **deferred to Phase 7** — v1.x uses structured output rather than agentic tool calls (per CR 002 amendment, applying the *design-driven-by-realistic-needs* principle: Mafia game-design tool-call cases are mostly degenerate vs. structured output).
- **LLM Client:** `langchain-aws` `ChatBedrockConverse`. Two singletons (Sonnet 4.5 + Haiku 4.5) — see §4.
- **Console UI:** Textual (TUI framework on top of Rich). Chosen for the Phase 6 requirement that AI players "type" into a shared chat panel while the human types into a pinned input line without stream collisions.
- **Concurrency Model:**
  - **Phases 1–5:** synchronous LangGraph execution (`graph.invoke`, `graph.stream`). Because Textual runs its own asyncio event loop, sync LangGraph calls are dispatched via `asyncio.to_thread` so they don't block the UI.
  - **Phase 6:** native async (`graph.astream`) with per-AI-player async tasks publishing to a shared in-process message bus (`asyncio.Queue` + a `messages` state reducer). A vote-open signal closes the bus and transitions all players into a synchronous vote step.
- **Configuration Loader:** `python-dotenv` for `.env` files.
- **Randomness:** `random.Random` seeded per-run (seed optionally from env) so night-kill tie-breaks and AI turn ordering can be reproduced when debugging.
- **Infrastructure-as-Code:** Terraform module (delivered with Phase 2 / v1.1) provisions the AgentCore Runtime + Gateway + Memory + Observability set with one `terraform apply`.

---

## 2. State & Persistence

- **Game State Shape:** A single LangGraph `TypedDict` with reducers — e.g. `messages: Annotated[list[AnyMessage], add_messages]`, `alive_players: list[str]` (replace), `day_index`, `phase`, `vote_ballots: dict[str, str]`, `night_kill_votes: dict[str, str]`, `night_kill_round: int`, `winner: str | None`.
- **Private Per-Player State:** Character sheets and (in local mode) diaries live in a nested `players: dict[player_id, PlayerState]` map, where each `PlayerState` holds that player's `character_sheet`, `is_alive`, `role` (and in local mode, `diary_entries`). Access is restricted at the node level — only the Moderator node and the owning player's nodes read/write a given entry. This keeps "private" information compartmentalized by convention rather than by runtime isolation, sufficient for a single-process educational demo.
- **Per-Game Diary Store (Phase 2 scope):** Diary entries — written before each Night by surviving AI players, re-read by the owning agent during play, revealed to the Moderator at end-of-game — are persisted via two parallel implementations of the same `DiaryStore` interface:
  - **Remote mode:** AgentCore Memory under a per-player namespace, scoped to the game's lifetime, accessed through an AgentCore Gateway-fronted MCP surface (this Gateway-fronted surface is the v1.x Gateway demonstration; the richer tool surface deferred per §1).
  - **Local mode:** in-process LangGraph state (`PlayerState.diary_entries: list[str]`); no Gateway, no AWS calls.
- **Long-Term Cross-Game Stats Store (Phase 3 scope):** End-of-game stats summaries — night-kill initiations and votes, day-execution initiations and votes, game outcomes, role-broken-down counts, human-player career data — are persisted across game sessions via two parallel implementations:
  - **Remote mode:** AgentCore Memory at long-term scope (the explicit demonstration of cross-session AgentCore Memory).
  - **Local mode:** a small file in the game's local data directory.
  - **Stored data is bounded:** counters and outcome summaries only. Full game transcripts, diaries, and vote-by-vote replays remain non-persistent across sessions (per product-definition §3.2).
  - **The local file is the only persistent state that crosses sessions in local mode** — game state, checkpoints, and per-game diaries are all wiped between runs.
- **Checkpointer:** `SqliteSaver` pointed at a per-run file (`./.graphia/checkpoints/<thread_id>.sqlite`). Enables `interrupt()`/resume on the human's turns and crash/Ctrl-C recovery within a single game's lifetime. No user-facing save/load of past games; old checkpoint files are safe to delete between runs.
- **Logs as State Artifacts:** Day-chat messages, night-kill vote records, and execution-vote records are kept inside graph state for the duration of the game so the Moderator's end-of-game recap can read them directly, then discarded with the checkpoint file when the game ends.

---

## 3. Runtime & Execution Environment

Graphia ships with **two parallel run modes**, selected via a `--remote` flag at launch. Both modes share the same LangGraph topology, game logic, and structured-output schemas; they differ only in where the runtime executes and where state lives.

- **Local mode (default):** Single Python process on the developer's laptop (macOS / Linux / Windows terminals). Textual TUI in the foreground, LangGraph driving in the background, Bedrock model calls reaching out to AWS for inference, but **no AgentCore involvement**. Used for game-mechanics development and offline-of-AgentCore play.
  - Entry point: `uv run python -m graphia`.
  - Filesystem footprint: `./.graphia/` for checkpoint sqlite + JSONL trace log; the local cross-game stats file lives in the game's local data directory.
- **Remote mode (Phase 2 / v1.1 scope):** The game-engine core runs as a Bedrock AgentCore Runtime workload in `us-east-1`; the local Textual UI invokes the deployed runtime through the AgentCore client. Tools, Memory, and observability are all AgentCore-managed.
  - Entry point: `uv run python -m graphia --remote`.
  - Pre-flight: `terraform apply` from the included Terraform module to provision Runtime + Gateway + Memory + Observability.
  - **Scale:** AgentCore Runtime is consumption-based (per-second CPU + memory; idle / IO-wait free) — scale-to-zero by default in `us-east-1`. No fixed monthly floor.
- **Terminal Requirements (both modes):** UTF-8 terminal with ANSI support (Textual requires this). `stdin/stdout/stderr` are reconfigured to `encoding="utf-8", errors="replace"` at startup to defend against non-UTF-8 default locales.
- **Secrets & Config via `.env`:**
  - `AWS_BEARER_TOKEN_BEDROCK` (current default for Bedrock model invocation) **or** `AWS_PROFILE=my-aws-profile` (SSO path — either works for Bedrock).
  - `AWS_REGION=us-east-1`.
  - `GRAPHIA_LOG_FILE` — path for the streaming trace log (default `./.graphia/graphia.log`).
  - `GRAPHIA_SEED` (optional) — seeds the game's `Random` for reproducible sessions.
  - `GRAPHIA_CHECKPOINT_DIR` (optional) — overrides the checkpoint sqlite location.
  - `--remote` CLI flag toggles remote-mode invocation.
- **AWS Account & Profile:** AWS account `123456789012`, accessed via the AWS CLI profile `my-aws-profile` (IAM Identity Center, AdministratorAccess role). `aws sso login --profile my-aws-profile` is required before AgentCore deployment / remote-mode invocation; not needed for local mode if the bearer-token Bedrock auth path is used.

---

## 4. External Services & APIs

- **LLM Provider:** AWS Bedrock, region `us-east-1` (US inference profiles). Two `ChatBedrockConverse` instances:
  - **Primary (Sonnet 4.5):** model ID `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Used for all gameplay roles — Moderator narrative announcements, AI player turns (pointing, speaking, voting), character-sheet generation (Phase 6), and the end-of-game creative recap (Phase 6).
  - **Secondary (Haiku 4.5):** model ID `us.anthropic.claude-haiku-4-5-20251001-v1:0`. Used only for short, mechanical calls where Sonnet latency/cost is overkill. Current use: start-of-game AI player name generation in a single call.

  Behavioral variation within each tier comes from system prompts, temperature, and structured-output schemas — **not** from adding more models. Two models is the cap for this project.

- **Bedrock AgentCore (remote mode only — Phase 2 + Phase 3 scope):**
  - **AgentCore Runtime:** hosts the LangGraph game-engine core; consumption-based per-second pricing, scale-to-zero in `us-east-1`.
  - **AgentCore Gateway:** fronts the per-game diary read/write surface over MCP for the agents in the hosted runtime. This Gateway-fronted diary surface is the v1.x AgentCore Gateway demonstration. The richer tool surface (investigation tool, evidence-builder tool, Moderator helper tools) is **deferred to Phase 7** per CR 002 amendment.
  - **AgentCore Memory:** two parallel use-patterns within one managed service —
    - *Per-game diary store* — per-player namespace, game-lifetime scope (Phase 2).
    - *Long-term cross-game stats store* — per-player career-data namespace, persists across sessions (Phase 3).
  - **AgentCore Observability:** emits structured traces from the hosted runtime to CloudWatch Logs. Default retention; tune via Terraform if needed.

- **Authentication:**
  - **Bedrock model invocation:** Bearer-token (`AWS_BEARER_TOKEN_BEDROCK`) — auto-picked up by boto3 ≥ 1.39's `bedrock-runtime` client. SSO profile `my-aws-profile` is the alternative path; both work.
  - **AgentCore deployment & runtime invocation:** SSO profile `my-aws-profile` (`aws sso login --profile my-aws-profile` before each session). Bearer tokens are not used for AgentCore.

- **No Other External Services:** No standalone database service, no external message broker, no object storage, no auth provider, no email/notification channels. AgentCore Memory is the only managed-state service (and only in remote mode). Local mode hits AWS only for Bedrock model invocation.

- **Web / external research tools:** explicitly out of scope — all in-game data access reads game state only, keeping the game self-contained and deterministic to reason about (per product-definition §3.2).

---

## 5. Observability & Debugging

- **Local mode trace:** `graph.stream(..., stream_mode="updates")` output is written to `GRAPHIA_LOG_FILE` (JSONL), capturing which node fired, state deltas, and structured-output decisions. Serves both as a debug log and as an educational artifact — a reader can diff the log against the code to trace execution. Opened in append mode; one line per super-step.
- **Remote mode trace:** AgentCore Observability emits structured traces from the hosted runtime to CloudWatch Logs. The same `graph.stream` events feed both the local JSONL (when running locally) and the CloudWatch trace (when running remotely); the divergence is just the sink.
- **Console Reserved for Gameplay:** The Textual UI is the player's view — no framework logging, no LangGraph traces, no warnings render into the game panes. All diagnostic output goes to the log file (local mode) or CloudWatch (remote mode) so the game experience stays clean.
- **Error Handling:**
  - **Local mode:** Unhandled exceptions inside graph nodes are caught at the Textual app boundary, written to the log file with a full traceback, and surfaced to the user as a modal with a short friendly message plus the log file path.
  - **Remote mode:** The runtime's full traceback is wired to CloudWatch; a short failure summary is returned to the local client and surfaced via the same Textual modal pattern with a CloudWatch log link.
- **No External Telemetry beyond CloudWatch:** LangSmith / OpenTelemetry / metrics backends are intentionally not wired up. If a user wants LangSmith tracing, they can enable `LANGSMITH_API_KEY` manually; the code does not assume its presence. Bedrock Guardrails was deliberately descoped (per CR 001 amendment) — no content-filtering layer is wired into the model calls in v1.x.
