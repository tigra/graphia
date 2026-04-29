# System Architecture Overview: Graphia

---

## 1. Application & Technology Stack

- **Language & Runtime:** Python 3.10+ (LangGraph 1.x drops 3.9; modern union syntax is useful).
- **Dependency & Project Management:** `uv` with `pyproject.toml` and `uv.lock`. Scripts run via `uv run graphia.py`; no PEP 723 inline script metadata.
- **Orchestration Framework:** LangGraph 1.x (`StateGraph`, `ToolNode`, `interrupt()`/`Command(resume=…)`, reducers).
- **LLM Client:** `langchain-aws` `ChatBedrockConverse`, `bind_tools` for tool-calling agents.
- **Console UI:** Textual (TUI framework on top of Rich). Chosen for the Phase 3 requirement that AI players "type" into a shared chat panel while the human types into a pinned input line without stream collisions.
- **Concurrency Model:**
  - **Phases 1–2:** synchronous LangGraph execution (`graph.invoke`, `graph.stream`). Because Textual runs its own asyncio event loop, sync LangGraph calls are dispatched via `asyncio.to_thread` so they don't block the UI.
  - **Phase 3:** native async (`graph.astream`) with per-AI-player async tasks publishing to a shared in-process message bus (asyncio.Queue + a `messages` state reducer). A vote-open signal closes the bus and transitions all players into a synchronous vote step.
- **Configuration Loader:** `python-dotenv` for `.env` files.
- **Randomness:** `random.Random` seeded per-run (seed optionally from env) so night-kill tie-breaks and AI turn ordering can be reproduced when debugging.

---

## 2. State & Persistence

- **Game State Shape:** A single LangGraph `TypedDict` with reducers — e.g. `messages: Annotated[list[AnyMessage], add_messages]`, `alive_players: list[str]` (replace), `day_index`, `phase`, `vote_ballots: dict[str, str]`, `night_kill_votes: dict[str, str]`, `night_kill_round: int`, `winner: str | None`.
- **Private Per-Player State:** Character sheets and diaries live in a nested `players: dict[player_id, PlayerState]` map, where each `PlayerState` holds that player's `character_sheet`, `diary_entries: list[str]`, `is_alive`, `role`. Access is restricted at the node level — only the Moderator node and the owning player's nodes read/write a given entry. This keeps "private" information compartmentalized by convention rather than by runtime isolation, which is sufficient for a single-process educational demo.
- **Checkpointer:** `SqliteSaver` pointed at a per-run file (`./.graphia/checkpoints/<thread_id>.sqlite`). Enables `interrupt()`/resume on the human's turns and allows recovery after a crash or Ctrl-C within the lifetime of a single game. There is no user-facing save/load of past games; old checkpoint files are safe to delete between runs.
- **Cross-Session History:** None. Each run starts fresh by design (per the product definition's out-of-scope list).
- **Logs as State Artifacts:** Day-chat messages, night-kill vote records, and execution-vote records are kept inside graph state for the duration of the game so the Moderator's end-of-game recap can draw on them directly, then discarded with the checkpoint file when the game ends.

---

## 3. Runtime & Execution Environment

- **Target Platform:** Single Python process on the developer's local machine (macOS/Linux/Windows terminals). No server, no container, no cloud deployment.
- **Entry Point:** `uv run graphia.py` (single-file or `src/graphia/__main__.py` if the project grows). A top-level `asyncio.run(app.run_async())` boots the Textual app, which in turn drives LangGraph.
- **Terminal Requirements:** UTF-8 terminal with ANSI support (Textual requires this). `stdin/stdout/stderr` are reconfigured to `encoding="utf-8", errors="replace"` at startup to defend against non-UTF-8 default locales (same workaround already proven in `adventure.py`).
- **Secrets & Config via `.env`:**
  - `AWS_BEARER_TOKEN_BEDROCK` — auto-detected by boto3 for Bedrock calls. Also `AWS_REGION`.
  - `GRAPHIA_LOG_FILE` — path for the streaming trace log (default `./.graphia/graphia.log`).
  - `GRAPHIA_SEED` (optional) — seeds the game's `Random` for reproducible sessions.
- **Filesystem Footprint:** `./.graphia/` holds ephemeral per-run artifacts (checkpoint sqlite, log file). The folder is gitignored and safe to wipe.

---

## 4. External Services & APIs

- **LLM Provider:** AWS Bedrock, region `eu-north-1` (EU inference profiles). Two `ChatBedrockConverse` instances:
  - **Primary (Sonnet 4.5):** model ID `eu.anthropic.claude-sonnet-4-5-20250929-v1:0`. Used for all gameplay roles — Moderator narrative announcements, AI player turns (pointing, speaking, voting), future character-sheet generation (Phase 3), and the future end-of-game creative recap.
  - **Secondary (Haiku 4.5):** model ID `eu.anthropic.claude-haiku-4-5-20251001-v1:0`. Used only for short, mechanical calls where Sonnet latency/cost is overkill. Current use: start-of-game AI player name generation (5 distinct names in a single call).
  Behavioral variation within each tier comes from system prompts, temperature, and structured-output schemas — not from adding more models. Two models is the cap for this project.
- **Authentication:** Bearer-token (`AWS_BEARER_TOKEN_BEDROCK`), auto-picked up by boto3 ≥ 1.39's `bedrock-runtime` client. No IAM roles or SigV4 signing configured.
- **No Other External Services:** No database service, no message broker, no object storage, no auth provider, no email/notification channels. Every non-LLM concern is handled in-process.

---

## 5. Observability & Debugging

- **Streaming Graph Trace to File:** `graph.stream(..., stream_mode="updates")` output is written to `GRAPHIA_LOG_FILE` (JSONL), capturing which node fired, state deltas, and tool calls. This serves both as a debug log and as an educational artifact — a reader can diff the log against the code to trace execution. The file is opened in append mode; one line per super-step.
- **Console Reserved for Gameplay:** The Textual UI is the player's view — no framework logging, no LangGraph traces, no warnings render into the game panes. All diagnostic output goes to the log file so the game experience stays clean.
- **Error Handling:** Unhandled exceptions inside graph nodes are caught at the Textual app boundary, written to the log file with a full traceback, and surfaced to the user as a modal with a short friendly message plus the log file path.
- **No External Telemetry:** LangSmith / OpenTelemetry / metrics backends are intentionally not wired up. If a user wants remote tracing, they can enable `LANGSMITH_API_KEY` manually; the code does not assume its presence.