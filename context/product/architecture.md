# System Architecture Overview: Graphia

---

## 1. Application & Technology Stack

- **Language & Runtime:** Python 3.10+ (LangGraph 1.x drops 3.9; modern union syntax is useful).
- **Dependency & Project Management:** `uv` with `pyproject.toml` and `uv.lock`. Scripts run via `uv run python -m graphia`; no PEP 723 inline script metadata.
- **Orchestration Framework:** LangGraph 1.x (`StateGraph`, `interrupt()`/`Command(resume=…)`, reducers, structured-output schemas via `with_structured_output`). `ToolNode` and `bind_tools` are **deferred to Phase 7** — v1.x uses structured output rather than agentic tool calls (per CR 002 amendment, applying the *design-driven-by-realistic-needs* principle: Mafia game-design tool-call cases are mostly degenerate vs. structured output).
- **LLM Client:** a small **provider abstraction** (per ADR 009) — an abstract provider interface with two implementations, selected by a config setting at the existing two-tier factory: **Bedrock** (`langchain-aws` `ChatBedrockConverse`, the default) and **Ollama** (local-only; reached through Ollama's Anthropic-compatible `/v1/messages` endpoint via `langchain-anthropic`, per ADR 010). Each provider exposes the same two-tier pattern — a heavyweight LLM for gameplay and a lightweight LLM for short mechanical calls — see §4. A third, **eval-harness-only** embeddings client sits deliberately *outside* this seam (always Bedrock, whatever the selected provider) — it measures generated output rather than producing gameplay; see §4.
- **Console UI:** Textual (TUI framework on top of Rich). Chosen for the Phase 6 requirement that AI players "type" into a shared chat panel while the human types into a pinned input line without stream collisions.
- **Concurrency Model:**
  - **Phases 1–5:** synchronous LangGraph execution (`graph.invoke`, `graph.stream`). Because Textual runs its own asyncio event loop, sync LangGraph calls are dispatched via `asyncio.to_thread` so they don't block the UI.
  - **Phase 6:** native async (`graph.astream`) with per-AI-player async tasks publishing to a shared in-process message bus (`asyncio.Queue` + a `messages` state reducer). A vote-open signal closes the bus and transitions all players into a synchronous vote step.
- **Configuration Loader:** `python-dotenv` for `.env` files.
- **Infrastructure-as-Code:** Terraform module (delivered with Phase 2 / v1.1) provisions the AgentCore Runtime + Gateway + Memory + Observability set with one `terraform apply`.

---

## 2. State & Persistence

- **Game State Shape:** A single LangGraph `TypedDict` with reducers — e.g. `messages: Annotated[list[AnyMessage], add_messages]`, `alive_players: list[str]` (replace), `day_index`, `phase`, `vote_ballots: dict[str, str]`, `night_kill_votes: dict[str, str]`, `night_kill_round: int`, `winner: str | None`.
- **Private Per-Player State:** Two different shapes, and the distinction matters because the document previously conflated them. **Per-player attributes** live in a nested `players: dict[player_id, PlayerState]` map, where each `PlayerState` holds `id`, `name`, `role`, `is_human`, `is_alive` and `persona`. **Private per-player narrative channels are top-level `GameState` keys, not fields on `PlayerState`** — `private_thoughts` (spec 028) and `private_diaries` (spec 039), each a `dict[player_id, list[...]]` behind its own merge reducer so a fan-out of per-player writes composes into one delta. There is no `PlayerState.diary_entries`; earlier revisions of this document named one, and no such field has ever existed. Access is restricted at the node level — only the Moderator node and the owning player's nodes read/write a given entry. This keeps "private" information compartmentalized by convention rather than by runtime isolation, sufficient for a single-process educational demo.
- **Per-Game Diary Store (Phase 2 scope):** Diary entries — written before each Night by surviving AI players, re-read by the owning agent during play, revealed to the Moderator at end-of-game — are persisted via two parallel implementations of the same `DiaryStore` interface:
  - **Remote mode:** AgentCore Memory under a per-player namespace, scoped to the game's lifetime, accessed through an AgentCore Gateway-fronted MCP surface (this Gateway-fronted surface is the v1.x Gateway demonstration; the richer tool surface deferred per §1).
  - **Local mode:** the in-process `GameState.private_diaries` channel — `dict[player_id, list[DiaryRecord]]` behind a merge reducer, where a `DiaryRecord` carries the entry text, its Day, and a cursor into that player's thoughts; no Gateway, no AWS calls.
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

- **Local mode (default):** Single Python process on the developer's laptop (macOS / Linux / Windows terminals). Textual TUI in the foreground, LangGraph driving in the background, **no AgentCore involvement**. Model inference goes to the configured LLM provider (§4): **Bedrock** (the default — the one AWS touchpoint of local mode) or **local Ollama**, under which local-mode **gameplay** is **fully offline** — no AWS account, credentials, network, or per-token cost (ADR 009/010, spec 010). Used for game-mechanics development and offline play. The one exception is the developer-side **eval harness**, not the game: a *measured* run additionally reaches Bedrock for the embeddings-based persona-similarity metric, and omits that metric (never fails) when no AWS credentials are present — see §4.
  - Entry point: `uv run python -m graphia`.
  - Filesystem footprint: `./.graphia/` for checkpoint sqlite + JSONL trace log; the local cross-game stats file lives in the game's local data directory.
- **Remote mode (Phase 2 / v1.1 scope):** The game-engine core runs as a Bedrock AgentCore Runtime workload in `us-east-1`; the local Textual UI invokes the deployed runtime through the AgentCore client. Tools, Memory, and observability are all AgentCore-managed.
  - Entry point: `uv run python -m graphia --remote`.
  - Pre-flight: `terraform apply` from the included Terraform module to provision Runtime + Gateway + Memory + Observability.
  - **Scale:** AgentCore Runtime is consumption-based (per-second CPU + memory; idle / IO-wait free) — scale-to-zero by default in `us-east-1`. No fixed monthly floor.
- **Terminal Requirements (both modes):** UTF-8 terminal with ANSI support (Textual requires this). `stdin/stdout/stderr` are reconfigured to `encoding="utf-8", errors="replace"` at startup to defend against non-UTF-8 default locales.
- **Secrets & Config via `.env`:**
  - `AWS_BEARER_TOKEN_BEDROCK` (legacy default for Bedrock model invocation) **or** `AWS_PROFILE=<your-aws-profile>` (SSO path — either works for Bedrock; SSO is now the canonical path). Not needed when the Ollama provider is selected.
  - `AWS_REGION=us-east-1`.
  - `GRAPHIA_LLM_PROVIDER` — `bedrock` (default) | `bedrock-claude` (ADR 012) | `ollama`; selects the LLM provider implementation (ADR 009). An unknown value is refused at startup rather than defaulted. With `ollama`: `GRAPHIA_OLLAMA_BASE_URL` (default `http://localhost:11434`) plus per-tier model overrides (`GRAPHIA_OLLAMA_LARGE_MODEL` / `GRAPHIA_OLLAMA_SMALL_MODEL`). Ollama is **local-mode only** — combining it with `--remote` is a config contradiction rejected at startup.
  - `GRAPHIA_LOG_FILE` — path for the streaming trace log (default `./.graphia/graphia.log`).
  - `GRAPHIA_CHECKPOINT_DIR` (optional) — overrides the checkpoint sqlite location.
  - `GRAPHIA_NUM_CITIZENS` / `GRAPHIA_NUM_MAFIA` (optional) — the table's **whole-table** counts, the human's seat included, so their sum is the number of players. Default **6 and 2** — eight players — per [CR 007](../change-requests/007-starter-lineup-balance-claim-and-default.md); it was 5 and 2 until 2026-09-03, and a measured run's comparability turns on which lineup it used, so every eval record stamps `settings.lineup`. A lineup that cannot make a game (mafia at or above citizens, or a table over the size cap) is refused at startup, before any table appears.
  - `--remote` CLI flag toggles remote-mode invocation.
- **AWS Account & Profile:** The developer's AWS account, accessed via an AWS CLI SSO profile they configure once with `aws configure sso` (and set as `AWS_PROFILE=<your-profile>` in `.env`). The account ID is derived from the active profile (`aws sts get-caller-identity` / `data.aws_caller_identity`), not pinned in source. `aws sso login --profile <your-profile>` is required before AgentCore deployment / remote-mode invocation; not needed for local mode if the bearer-token Bedrock auth path is used.

---

## 4. External Services & APIs

- **LLM Provider:** pluggable behind a provider abstraction (ADR 009), selected by `GRAPHIA_LLM_PROVIDER`; **three providers**, each exposing the same two tiers:
  - **Bedrock (default):** AWS Bedrock, region `us-east-1` (US inference profiles), via `ChatBedrockConverse`. The only provider usable in remote mode.
  - **Ollama (local mode only):** a model served locally by Ollama, reached through Ollama's **Anthropic-compatible `/v1/messages`** endpoint via `langchain-anthropic` with a local `base_url` (ADR 010 — chosen over the native and OpenAI-compatible surfaces to keep a path back to a single Anthropic client if the Nova cost-detour of ADR 003 is ever reversed). Fully offline; structured output via Anthropic tool-use. **ADR 010's verify gate has since returned a split result, and [ADR 013](../adr/013-native-ollama-structured-output.md) is the revisit it called for:** tool use over that endpoint proved reliable for six of the game's seven schemas and unreliable for the seventh, because the endpoint accepts `tool_choice` and silently drops it, so a model handed the deliberately open diary prompt answered in prose about half the time and the entry was discarded. ADR 013 therefore **decides** to move this provider to Ollama's **native API with a JSON-schema `format` grammar** — constrained decoding, where non-conforming output is unrepresentable at the token level rather than merely requested. **That decision is accepted but NOT yet implemented** (spec 041 carries it, and this document describes the system as it stands): the provider still constructs `ChatAnthropic`, and what ships today is an application-layer repair that recovers the prose when the model skips the tool call, measured at 0 of 72 diary entries falling back where the unrepaired path lost 45 of 90.

  The two tiers, per provider:
  - **Primary (heavyweight LLM):** used for all gameplay roles — Moderator narrative announcements, AI player turns (pointing, speaking, voting), character-sheet generation (Phase 6), and the end-of-game creative recap (Phase 6).
  - **Secondary (lightweight LLM):** used only for short, mechanical calls where the heavyweight tier's latency/cost is overkill. Current use: start-of-game AI player name generation in a single call.

  Behavioural variation within each tier comes from system prompts, temperature, and structured-output schemas — **not** from adding more models. Two LLM tiers is the cap for the **gameplay** path, and **three providers** is the current provider set — Bedrock Nova, Bedrock Claude (ADR 012) and local Ollama — a fourth meaning another implementation of the ADR-009 interface, justified by its own ADR; the specific model identities (family, version, region-prefix, Ollama model names) are operational and cost choices captured in code and in the relevant ADR, not architectural pins of this document.

  Outside those two tiers, and outside the ADR-009 provider seam entirely:

  - **Embeddings instrument (eval harness only):** the persona-similarity measurement (spec 033) has to judge *meaning* rather than wording, so the eval harness builds a third Bedrock client — an **embeddings model** in `us-east-1` via `langchain-aws` `BedrockEmbeddings` (currently Amazon Titan Text Embeddings v2) — beside the two gameplay tiers. It is **deliberately always Bedrock**: it does *not* route through the ADR-009 provider abstraction and ignores `GRAPHIA_LLM_PROVIDER`, because a measuring instrument must stay **fixed** for its numbers to be comparable across Ollama and Bedrock gameplay runs — a metric that changed with the model under test would measure nothing. It is **never in the gameplay path** (no game node calls it; only the out-of-suite eval harness does, one batched call per measured game), which is why it sits outside the seam rather than becoming a third tier. **Consequence:** a measured run needs AWS credentials and a small embedding cost even under the otherwise-offline Ollama provider; when they are absent the harness **omits the metric and the run completes normally**. The specific embedding model id is an operational choice captured in code, on the same footing as the tier model identities above.

- **Bedrock AgentCore (remote mode only — Phase 2 + Phase 3 scope):**
  - **AgentCore Runtime:** hosts the LangGraph game-engine core; consumption-based per-second pricing, scale-to-zero in `us-east-1`.
  - **AgentCore Gateway:** fronts the per-game diary read/write surface over MCP for the agents in the hosted runtime. This Gateway-fronted diary surface is the v1.x AgentCore Gateway demonstration. The richer tool surface (investigation tool, evidence-builder tool, Moderator helper tools) is **deferred to Phase 7** per CR 002 amendment.
  - **AgentCore Memory:** two parallel use-patterns within one managed service —
    - *Per-game diary store* — per-player namespace, game-lifetime scope (Phase 2).
    - *Long-term cross-game stats store* — per-player career-data namespace, persists across sessions (Phase 3).
  - **AgentCore Observability:** emits structured traces from the hosted runtime to CloudWatch Logs. Default retention; tune via Terraform if needed.

- **Authentication:**
  - **Bedrock model invocation:** Bearer-token (`AWS_BEARER_TOKEN_BEDROCK`) — auto-picked up by boto3 ≥ 1.39's `bedrock-runtime` client. The developer's SSO profile is the alternative (and now canonical) path; both work.
  - **AgentCore deployment & runtime invocation:** the developer's SSO profile (`aws sso login --profile <your-profile>` before each session). Bearer tokens are not used for AgentCore.
  - **Ollama provider:** no authentication — the Anthropic client sends a dummy api-key to the local endpoint; no AWS credentials are read.

- **No Other External Services:** No standalone database service, no external message broker, no object storage, no auth provider, no email/notification channels. AgentCore Memory is the only managed-state service (and only in remote mode). Local mode hits AWS only for Bedrock model invocation — and not at all when the **Ollama** provider is selected: local-mode **gameplay** is then fully offline, the only external endpoint being the player's own local Ollama server. The developer-side eval harness is the exception noted above — it reaches Bedrock for the embeddings instrument whatever the gameplay provider, or omits that one metric.

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

---

## 6. Determinism Posture & Testing Conventions

- **LLM outputs are accepted as variable.** Graphia's AI player behaviour comes from the heavyweight LLM and the start-of-game AI roster names come from the lightweight LLM (see §4). Both are inherently non-reproducible across runs — even pinning `temperature` to `0` only *lowers* the variance, it does not eliminate it. The project does not attempt to bridge this gap: there is no replay-from-transcript layer, no LLM-output caching for determinism, no temperature-zero shim that pretends to deliver replay-determinism. Two runs of the same game are *expected* to produce different AI names, different dialogue, and different outcomes. Tests and assertions therefore must not depend on textual equality of LLM-generated content; behavioural tests assert structural invariants (a vote was opened, exactly one player was executed, the winner field holds a valid value) rather than verbatim transcripts.

- **Direct intent expression in automated tests over fragile mechanisms.** Test scenarios are expressed by directly setting the state the test cares about — for example, setting the `GRAPHIA_ROLE` developer-appliance env var to pin which side the human is on — not by tunnelling intent through unrelated mechanisms that happen to have the desired side-effect (e.g., picking a stdlib-RNG seed value that incidentally deals the desired role assignment). The mechanism a test uses must read, at the call site, as what it does; the test's intent must be visible without one indirection into a magic-constant lookup. The cross-cutting principle: tunnelling intent through unrelated mechanisms causes opacity, opacity causes fragility under refactor, and fragility causes coupled-tests-that-pretend-to-be-independent. See ADR-006 "Test role-pinning convention: `GRAPHIA_ROLE` replaces magic-seed-for-role" for the concrete instantiation in spec 005's Slice 3.

- **Mechanical decisions use stdlib `random` (module-global).** Night-kill tie-breaks (when two pointing-vote tallies tie), the mafia-pointing fallback round, and per-cycle day-speaking order are decided by `random.shuffle(...)` / `random.choice(...)` calls against the module-global RNG. Their outcomes are accepted as non-replayable across runs on the same footing as LLM outputs above. There is no `GRAPHIA_SEED` env var, no `config.seed` field, no per-call seed-salt arithmetic — the project carries no env-var protocol for determinism. Tests that need a specific mechanical outcome **pin it via targeted monkeypatching of the RNG-using helper** — substitute the tie-break selector with a deterministic stub, replace `_shuffle_order` with a hand-written sequence, or inject a test-double for the surrounding function. This extends the project's existing `fake_*` / `dynamic_*` fixture pattern (see `tests/conftest.py`'s LLM-boundary fakes such as `fake_sonnet_pointing` and `target_human_pointing`) from the LLM boundary down to the stdlib-RNG layer. The one test that needs cross-run-deterministic RNG output — `tests/test_dual_mode_smoke.py`, which asserts byte-equal cross-mode game transcripts — calls `random.seed(SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY)` once at the start of each mode's run, locally and explicitly, not via an env var.

- **Ablatable gameplay features (ADR 011).** Every gameplay-influencing change ships behind its own **default-on** environment flag (`GRAPHIA_<FEATURE>`, e.g. `GRAPHIA_DAY_ROUND_RECAP`) so it can be toggled off to reproduce prior behaviour — enabling single-build A/B measurement against the blunder-eval ledger and per-model experimentation (a tweak made to coax one model isn't permanently forced on another). Display-only / non-gameplay changes are exempt; each such flag carries a flag-off parity test. See [ADR 011](../adr/011-ablatable-gameplay-feature-flags.md).
