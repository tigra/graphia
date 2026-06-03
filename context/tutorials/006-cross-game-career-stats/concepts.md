---
spec: 006-cross-game-career-stats
spec_title: Long-Term Cross-Game Memory & Career Stats
introduced_on: 2026-06-03
---

# Concepts introduced in this increment

## AgentCore Memory architecture

- **Two-tier Memory: events are truth, the record is the fold** (`two-tier-memory-events-and-record`) — One dedicated AgentCore Memory carries both tiers: per-action *events* are the durable source of truth, and a single long-term *record* (the rolling `CareerStats`) is their consolidation — the increment's clean demonstration of the two Memory tiers cooperating.
- **A dedicated career Memory resource** (`dedicated-career-memory-resource`) — Career stats get their *own* AgentCore Memory, separate from the existing diary Memory, so the self-managed strategy delivers only career events and there is no cross-feature event drift to filter out.
- **Self-managed memory strategy** (`self-managed-memory-strategy`) — A `SELF_MANAGED` strategy hands raw events to an S3 bucket + SNS topic for the application to consolidate itself (instead of LLM extraction); the AWS Terraform provider can't express it, so it's created out-of-band via `make create-stats-strategy` and fed back as a variable.
- **Event-sourced consolidation via a pure fold** (`event-sourced-fold-consolidation`) — A game's career contribution is reconstructed by replaying its per-action events into a `GameSummary`, then `fold`ed into the rolling aggregate; only the two *finalizer* events (`game_ended` / `game_abandoned`) trigger consolidation.

## Shared core & cross-mode equivalence

- **One shared `fold` for local↔remote equivalence** (`shared-fold-for-local-remote-equivalence`) — Local mode (`summarize`) and remote mode (`build_summary`) each produce the *same* `GameSummary`, then both call the *same* pure `fold`; sharing the data model + function (vendored verbatim into the Lambda zip) is what makes the two backends provably identical.
- **Flat event wire model** (`flat-event-wire-model`) — One frozen `CareerEvent` dataclass carries every kind's fields as `X | None` and is dispatched on its `kind`; `to_json` drops the `None`s and `from_json` is forward-tolerant, so a consumer that never imports the originating types can still parse one shape. Extends the project's flat-Pydantic convention to the event bus.

## LangGraph orchestration

- **Service injection via `functools.partial`** (`service-injection-via-partial`) — The career emitter and the per-game id are bound into each emitting node with `partial(...)` at graph-assembly time, so node bodies emit events without reaching for module-level singletons — the same injection shape the diary store already uses.
- **One shared `_assemble_graph` kills builder drift** (`shared-graph-assembly-kills-drift`) — Local `build_graph` and the Runtime's `build_runtime_graph` both delegate to a single private `_assemble_graph(...)` that wires the topology *and* the emitter `partial`s, so a feature added once lands in both modes by construction instead of by hand-mirroring (the drift that left the deployed Runtime emitting nothing for several cycles).
- **Emitter Protocol: no-op vs live** (`emitter-protocol-noop-vs-live`) — A `CareerEventEmitter` Protocol has a `NoOpCareerEventEmitter` (local mode / tests, reaches no AWS) and an `AgentCoreCareerEventEmitter` (remote, lazy boto3 `create_event`), chosen by a `make_career_emitter(config)` factory keyed on `career_memory_id`.

## Async consolidation pipeline (AWS)

- **SNS-triggered consumer Lambda** (`sns-triggered-consumer-lambda`) — A zip-packaged Python Lambda subscribed to the stats SNS topic does the consolidation: it returns early on non-finalizer events and only on a finalizer lists the session, builds the summary, folds, and writes the record. Modeled on the diary Lambdas but with its own least-privilege IAM role.
- **`games_folded` idempotency sidecar** (`games-folded-idempotency-sidecar`) — A `games_folded: list[str]` set kept *inside* the long-term record itself defends against SNS at-least-once redelivery folding the same game twice; because it lives with the record, there's no second source of truth to keep in sync.
- **S3-envelope payload extraction** (`s3-envelope-payload-extraction`) — The strategy delivers each event as an S3 object referenced by the SNS message's `s3PayloadLocation`; the original `CareerEvent` JSON rides in the envelope's `currentContext[*].content.text`, and the Lambda extracts it defensively so a schema drift logs a warning rather than crashing.

## Correctness & failure posture

- **Post-game panel reads materialised state, not a synthesised fold** (`panel-from-materialised-state`) — In remote mode `record()` returns `load()` *unchanged* — it does not fold the just-finished game in process. The panel shows the state the pipeline has actually written; the earlier "+1 this game" in-memory fold was removed because it masked a broken write pipeline as a cosmetic delay.
- **Loud-fail, no silent fallback in remote mode** (`loud-fail-no-silent-fallback`) — Remote emit / load propagate boto3 errors instead of swallowing them, and `make_stats_store` selects the remote store strictly on `career_memory_id` with *no* automatic fallback to the local file — so an IAM/network/API gap crashes loud instead of silently rendering a zeroed career.

## Testing & live verification

- **Closing the local-vs-Lambda boto3 gap** (`local-vs-lambda-boto3-gap`) — The developer's local boto3 is not the Lambda runtime's bundled snapshot; the increment pins current boto3 into the Lambda zip (the runtime snapshot lacked `batch_*_memory_records`) and guards the seam with two tests — a contract test that walks `service_model` to assert every operation+parameter we call exists (the `includePayloads` typo class), and a zip-contents test that asserts the vendored APIs are present.
- **Six-stage live-deploy verification harness** (`live-deploy-verification-harness`) — `make verify-pipeline` walks the *real* deploy end-to-end (image tag vs git HEAD, env wiring, the `human-career` actor, an ACTIVE strategy, a clean Lambda log stream, and a client read that matches the raw record) and exits non-zero on the first red, replacing post-mortem-by-CLI with one composable check.
