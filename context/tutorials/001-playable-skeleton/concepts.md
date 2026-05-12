---
spec: 001-playable-skeleton
spec_title: Playable Skeleton
introduced_on: 2026-05-11
---

# Concepts introduced in this increment

## Orchestration (LangGraph)

- **Typed state with field-level reducers** (`typed-state-with-reducers`) — A single `TypedDict` describes the whole game's state, with field-level reducers (`add_messages`, `operator.add`, replace) controlling how each field merges across super-steps.
- **Replay-safe interrupt placement** (`interrupt-replay-first-statement`) — `interrupt()` is placed as the very first statement of any node that prompts the human, because resume re-executes the whole node and any pre-work would run twice.
- **Resume via Command payload** (`command-resume-payload`) — The driver reinjects the human's input by calling the graph again with `Command(resume=value)`, which lands as the return value of the prior `interrupt()`.
- **Conditional edges with a routing function** (`conditional-edges-via-routing-fn`) — Branching edges are defined by `add_conditional_edges` paired with a small router function that returns the destination node's key.
- **One function, two node names for fan-out** (`same-fn-registered-twice-for-fan-out`) — The same pure-read win-condition function is registered under two distinct node names so each fan-out site (after Night, after Day) owns its own conditional edge.
- **Per-game sqlite checkpointer** (`sqlite-saver-per-thread`) — `SqliteSaver` is opened directly (not via the context manager) on a per-thread sqlite file so the checkpoint store lives for the whole game and survives node-by-node iteration.
- **Streaming graph updates** (`streaming-updates-mode`) — `graph.stream(..., stream_mode="updates")` emits one `{node_name: update}` dict per super-step, the format the driver consumes incrementally.
- **Day → Night cycle-closing edge** (`night-cycle-tail-edge`) — The Day → Night loop closes via a terminal `day_close → night_open` edge; `night_open` bumps the cycle counter on re-entry.
- **Side-channel state snapshots** (`graph-state-snapshot-read`) — `graph.get_state(run_config)` lets the UI/driver peek at the live state outside the stream — used for the per-super-step trace, the once-per-Day vote-hint cycle check, and the spectator-transition detector.

## Bedrock LLM integration

- **LLM client singletons** (`chatbedrockconverse-singleton`) — Two `ChatBedrockConverse` singletons (Sonnet for gameplay, Haiku for mechanical work) are configured once and reused; behavioural variation comes from prompts and temperature, not different models.
- **Flat structured-output schemas** (`structured-output-flat-pydantic`) — `with_structured_output(<schema>)` binds a Pydantic schema to the LLM call; schemas stay deliberately flat (no discriminated unions) because Bedrock Converse rejects them.
- **Single retry on validation error** (`validation-retry-once-with-feedback`) — If the structured output fails Pydantic validation, the call is retried once with a corrective message inserted into the conversation so the model can self-correct.
- **Regional inference-profile prefix** (`regional-inference-profile-prefix`) — Bedrock model IDs use the `us.anthropic.…` regional inference-profile prefix, which routes inference through the US regional family rather than a specific single-region endpoint.

## UI (Textual)

- **Dual public/private log panes** (`dual-richlog-panes-private-vs-public`) — The console UI has two `RichLog` panes (public + private); messages tagged with `additional_kwargs={"private_to": player_id}` route to the private pane only, the rest to the public pane.
- **Async-to-thread sync-stream bridge** (`async-to-thread-bridges-sync-stream`) — A worker started with `asyncio.to_thread` runs the synchronous LangGraph stream off the Textual event loop; chunks are piped back through an `asyncio.Queue` for the UI to consume without blocking.
- **Interrupt payload → modal dispatch** (`interrupt-payload-dispatch-to-modal`) — When the driver receives an `interrupt` value, its `kind` field selects which modal (name-prompt, pointing, vote) the UI presents to the human.
- **Message-ID dedup on render** (`seen-message-ids-dedup`) — The driver tracks which message IDs have already been rendered so the UI never double-shows a message that surfaces in more than one stream chunk.

## Python project layout

- **`.env` config with typed validation** (`env-config-via-dotenv-with-validation`) — `python-dotenv` loads `.env` at startup; a typed `GraphiaConfig` dataclass exposes the values and raises an actionable error when a required variable is missing.
- **UTF-8 stdio reconfig at entry** (`utf8-stream-reconfig-at-entry`) — `__main__.py` reconfigures `stdin/stdout/stderr` to UTF-8 *before* importing anything else, so Unicode narration never trips on the host's default code page.

## Observability

- **JSONL super-step trace logger** (`jsonl-stream-trace-logger`) — A `StreamTraceLogger` appends one JSONL line per super-step to `GRAPHIA_LOG_FILE`; the file is the canonical post-mortem artifact and the educational reference for "what just happened on this turn".
