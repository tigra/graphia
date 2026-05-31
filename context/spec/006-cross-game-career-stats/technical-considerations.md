<!--
HOW to build the feature at an architectural level. Not a copy-paste guide.
Rewritten 2026-05-31 to match ADR 008. The prior version is preserved in git.
-->

# Technical Specification: Long-Term Cross-Game Career Stats

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft (rewritten per [ADR 008](../../adr/008-self-managed-memory-pipeline.md))
- **Supersedes:** the prior direct-batch-write design (Slice 6, ADR 007 — now Superseded)
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Remote-mode career stats use **the self-managed AgentCore Memory pipeline end-to-end**, per ADR 008. This rewrite *replaces* the prior direct-batch-write design (`AgentCoreLongTermStatsStore` → `BatchCreateMemoryRecords` from the Runtime) — that path bypassed the strategy's delivery flow and is removed.

Two AgentCore Memory resources now coexist:

| Memory resource | Tier(s) used | Contents | Provisioned in |
|---|---|---|---|
| **Existing diary Memory** | short-term events (`create_event`/`list_events`) | per-game diary entries (Phase 2) — unchanged | Phase 2 Terraform |
| **NEW dedicated career Memory** | **both** short-term events AND a long-term record under a self-managed strategy | per-action career events (the raw log) + the rolling `CareerStats` aggregate (the consolidation) | Phase 3 Terraform (new) |

The career Memory becomes a clean, self-contained demonstration of *both* AgentCore Memory tiers cooperating: events ARE the source of truth, the long-term record IS the consolidation.

**Flow per game (remote mode):**

1. **Runtime emits per-action career events.** Wherever the Slice 3/4 in-state counters bumped (`day_turn`/`collect_votes`/`resolve_vote`/`resolve_night_kill`/setup-end/quit-confirm), the node now also calls `MemoryClient.create_event(memoryId=career_memory_id, actorId="human-career", sessionId=<game_id>, …)` carrying a small typed event payload (kind + minimal context). One game ⇒ a handful to a few dozen events tagged with the same `sessionId`.
2. **Self-managed strategy delivers** matching events to the existing S3 bucket / SNS topic (the scaffolding from Slice 7 — now load-bearing).
3. **Consumer Lambda** (new — modeled on the Phase 2 diary Lambdas, [ADR 005](../../adr/005-gateway-tools-via-lambda-targets.md)) is invoked per delivery. On a **non-finalizer** event it returns immediately (the event sits in Memory as the durable log; nothing to consolidate yet). On a **finalizer** event (`game_ended` or `game_abandoned`) it:
   - reads the full session log via `list_events(memoryId, actorId="human-career", sessionId=<game_id>)`,
   - reconstructs the `GameSummary` from those events using a shared `career_events` module,
   - loads the current `CareerStats` long-term record via `list_memory_records`,
   - `fold`s the summary in (the same pure `fold` shared with local mode),
   - **idempotency:** if `<game_id>` is already in the record's `games_folded` set, skips,
   - otherwise writes the updated record via `BatchCreate/UpdateMemoryRecords` with `<game_id>` added to `games_folded`.
4. **Client reads** (the launch greeting) stay `list_memory_records` by namespace on the career Memory — *unchanged behaviour vs. ADR 007*.

**Flow in local mode — unchanged.** `LocalFileStatsStore` continues to do its in-process `summarize(_latest_state, …) → fold → atomic file write` once per game (normal end via `_record_career`, abandoned via `_on_quit_decision`). Equivalence with remote mode is preserved by **sharing the `fold` function and the `GameSummary` data model** between the local store and the Lambda's `career_events` builder.

---

## 2. Proposed Solution & Implementation Plan

### 2.1 Dedicated career Memory + self-managed strategy (Terraform)

`infra/terraform/main.tf` gains a second `aws_bedrockagentcore_memory` resource (`career`) alongside the existing one (`this`, diary). The existing Slice 7 stats scaffolding (`aws_s3_bucket.stats_payload`, `aws_sns_topic.stats_payload`, `aws_iam_role.memory_stats` + policy, `time_sleep.wait_memory_stats_role`) moves to attach to **the career Memory** (`memory_execution_role_arn = aws_iam_role.memory_stats.arn` on the new resource). The self-managed strategy itself is still created **out-of-band** via `make create-stats-strategy` (provider gap; ADR 007 §2 Alt 2 relationship note — fact unchanged) and fed back via `var.stats_strategy_id`.

**New outputs:** `career_memory_id` (in addition to the existing `memory_id` which keeps its diary meaning). New env var on the Runtime: `GRAPHIA_CAREER_MEMORY_ID`. `GRAPHIA_STATS_NAMESPACE` and `GRAPHIA_STATS_STRATEGY_ID` keep their semantics.

The S3 bucket / SNS topic resource declarations don't change shape; only what they're attached to (the career Memory) does. **Open verify-item:** confirm the existing time-sleep + role-trust pattern works the same when the role attaches to a brand-new Memory in the same apply (the same IAM-propagation race may fire — the time-sleep should still cover it; flag this for the Slice author).

### 2.2 The consumer Lambda

A new zip-deployed Python Lambda, packaged exactly like `diary_write`/`diary_read`:

- **Handler:** `lambda_function.lambda_handler` (SNS event source).
- **Payload:** SNS message wrapping the `payloadDeliveryBucketName` S3 object key for each delivered event.
- **Per-delivery logic:** download the S3 payload (a single AgentCore-delivered event), decode the event content; **return early** unless `kind in {"game_ended", "game_abandoned"}`.
- **On finalizer:** call `list_events(memoryId=CAREER_MEMORY_ID, actorId="human-career", sessionId=<event.session_id>)`, reconstruct `GameSummary` via the shared `career_events` module, `list_memory_records` to read the current `CareerStats`, check `games_folded`, fold, write back.
- **IAM trust:** `lambda.amazonaws.com`. **Inline policy:** S3 GetObject on the stats-payload bucket; `bedrock-agentcore:ListEvents`/`ListMemoryRecords`/`BatchCreateMemoryRecords`/`BatchUpdateMemoryRecords` scoped to the career Memory ARN; CloudWatch Logs write to the function's log group.
- **SNS subscription:** `aws_sns_topic_subscription` of the stats SNS topic → the new Lambda; plus `aws_lambda_permission` allowing SNS to invoke. (This is the wiring that turns the existing topic from no-subscriber into actually-subscribed.)
- **Concurrency:** default unreserved is fine — finalizer events are rare (one per game).
- **Cold-start cost:** acceptable (we tolerate a few seconds of finalize latency; the panel doesn't depend on remote state — see §2.6).

### 2.3 The shared `career_events` module

A new `src/graphia/career_events.py` (NOT in the Lambda directory; the Lambda **vendors** it into its zip, mirroring how the diary Lambdas vendor the SDK) defines:

- `CareerEvent` (frozen dataclass): `kind: str`, `session_id: str`, plus the minimal kind-specific fields needed for stats consolidation. **Event kinds** (initial set):
  - `game_started` — `human_role`. Lets the Lambda associate the session with a role early; not strictly required if `game_ended` also carries it.
  - `vote_initiated` — `initiator_is_human: bool`. → contributes to `human_votes_called` when True.
  - `ballot_cast` — `voter_is_human: bool`. → `human_ballots_cast`.
  - `vote_resolved` — `was_executed: bool`. → `execution_count` (game-wide).
  - `night_resolved` — `victim_died: bool`, `human_was_mafia_picker: bool`, `human_picked_victim: bool`. → `night_victim_count` / `human_night_attempts` / `human_night_successes`.
  - `game_ended` (**finalizer**) — `outcome ∈ {law_abiding_win, mafia_win, draw}`, `human_role`, `rounds`.
  - `game_abandoned` (**finalizer**) — `human_role`, `rounds_so_far`.
- `build_summary(events: Iterable[CareerEvent]) -> GameSummary` — pure aggregation from a session's events into the existing `GameSummary` shape, so the Lambda then calls the EXISTING `fold(aggregate, summary)` unchanged. Reusing `GameSummary`/`CareerStats`/`fold` is what gives us local-vs-remote equivalence for free.
- `to_json(event) -> dict` / `from_json(dict) -> CareerEvent` — wire format.

### 2.4 Runtime node emissions

In remote mode, the same nodes that today increment in-state counters also call `memory_client.create_event(...)` with the appropriate `CareerEvent` payload:

| Node | Emits |
|---|---|
| `setup.assign_roles` | `game_started(human_role)` |
| `day.day_turn` (human-vote success path) | `vote_initiated(initiator_is_human=True)` |
| `day.day_turn` (AI-vote success path) | `vote_initiated(initiator_is_human=False)` *(needed for completeness; the Lambda may not need it for stats but the event log is the whole story)* |
| `day.collect_votes` (human ballot) | `ballot_cast(voter_is_human=True)` |
| `day.collect_votes` (AI ballot) | `ballot_cast(voter_is_human=False)` |
| `day.resolve_vote` (executed) | `vote_resolved(was_executed=True)` |
| `day.resolve_vote` (no-execution) | `vote_resolved(was_executed=False)` |
| `night.resolve_night_kill` (victim-died path) | `night_resolved(victim_died=True, human_was_mafia_picker=…, human_picked_victim=…)` |
| `endgame` (winner path) | `game_ended(outcome, human_role, rounds=cycle)` |
| `ui.app._on_quit_decision` (abandoned path) | `game_abandoned(human_role, rounds_so_far=cycle)` |

The `session_id` is the LangGraph thread id (a stable per-game identifier already in `_latest_state` / `RunnableConfig`). The actor id is the constant `"human-career"` (same as today's record actor — keeps the read path stable).

**In LOCAL mode, no events are emitted.** A small helper `maybe_emit_career_event(state, event)` is wired through the same module that decides which `StatsStore` to instantiate — local mode no-ops; remote mode hits the career Memory client.

### 2.5 Client store split

`src/graphia/stats_store.py`:

- `LocalFileStatsStore` — **unchanged.**
- `AgentCoreLongTermStatsStore` (Slice 6 direct-batch-write) — **removed.** Its tests (`tests/test_stats_store_remote.py`) and the equivalence test there are removed/rewritten.
- New `AgentCoreCareerEventStore(memory_id, strategy_id, namespace, region)`:
  - `load() -> CareerStats` — **unchanged behaviour from the old class**: `list_memory_records` by namespace on the career Memory (the Lambda's writes show up here for subsequent reads). Same tolerance: zeroed on missing/parse failure.
  - `record(summary) -> CareerStats` — **becomes a thin emit-finalizer + return-derived-aggregate**: it does NOT write the record itself. Instead it emits the `game_ended` (or `game_abandoned`) event via `create_event` and **returns `fold(self.load(), summary)` computed locally** so the post-game panel can render immediately with the just-folded numbers (see §2.6). The actual long-term-record write happens shortly afterward in the Lambda.
- `make_stats_store(config)` — selects `AgentCoreCareerEventStore` when `config.career_memory_id` is set; else `LocalFileStatsStore(config.stats_file)`.

### 2.6 UI / panel correctness (the async gap)

The panel renders from the value `record()` *returns* (a local `fold(self.load(), summary)`), not from a re-read after the Lambda finishes. So the panel is correct immediately; the Lambda's record-write is the durable persistence that the *next* launch's `load()` will see. This is the same "panel from in-memory fold" trick the prior design used; it remains correct under the async pipeline. The greeting at next launch reads the long-term record (now written by the Lambda), so the read-after-write gap is bounded by the time between two app launches — never a problem in practice.

**Idempotency note:** because the `games_folded` set is the Lambda's defense against double-folding, and because the Runtime always emits exactly one finalizer per game, double-counting requires SNS to deliver the finalizer twice (at-least-once). The set guards against that.

### 2.7 Config

`src/graphia/config.py` gains:

| Field | Env var | Notes |
|---|---|---|
| `career_memory_id: str \| None` | `GRAPHIA_CAREER_MEMORY_ID` | new; selects remote mode for the career store. Distinct from `memory_id` (which is the diary Memory). |
| `stats_namespace: str \| None` | `GRAPHIA_STATS_NAMESPACE` | unchanged; namespace for the career long-term record. |
| `stats_strategy_id: str \| None` | `GRAPHIA_STATS_STRATEGY_ID` | unchanged; the self-managed strategy id on the career Memory (provider-gap workflow unchanged: `make create-stats-strategy`). |

The Runtime no longer ever needs to set `memoryStrategyId` on `BatchCreate`/`BatchUpdate` (those calls move to the Lambda); the Runtime's career path is purely `create_event` + `list_memory_records`.

### 2.8 Slice migration

Per ADR 008's directive, the prior **Slice 6** (*AgentCore long-term-record remote backend + equivalence*) is **not refactored in place**. A new slice in `tasks.md` replaces it, with all the work described above (Lambda, Terraform second Memory, node emissions, new client store, shared `career_events` module, tests). The old Slice 6 stays in the file marked as superseded, for history.

---

## 3. Impact and Risk Analysis

- **Eventual consistency** — the long-term record is updated by the Lambda after the game's finalizer event is delivered. The post-game panel rides on the in-memory local fold and is correct immediately; the *next launch's* greeting reads from the Lambda-written record, so the relevant read-after-write gap is bounded by minutes-to-days, never a real problem.
- **Idempotent finalization** — `games_folded` set in the long-term record is the primary defense; without it, an SNS redelivery of the finalizer would double-count.
- **Event order doesn't matter** — `build_summary` is associative over the session's events; in-flight reordering by SNS is benign as long as the finalizer eventually arrives.
- **IAM-propagation race** — the Slice 7 `time_sleep` on the `memory_stats` role applies to the new career Memory's `memory_execution_role_arn`; verify it covers the new attachment.
- **Lambda-per-event invocation cost** — the strategy delivers every event, so the Lambda gets a few-to-many invocations per game even though only finalizers do work. Acceptable at our scale; revisit only if invocation cost dominates.
- **Drift from local mode** — sharing `fold` + `GameSummary` between the local store and the Lambda's `career_events.build_summary` is what makes local-vs-remote equivalence true; if either drifts independently the equivalence test catches it.
- **Lost finalizer ⇒ never-recorded game** — if the `game_ended`/`game_abandoned` event delivery never reaches the Lambda (or the Lambda errors out beyond DLQ retries), the game's record never lands. Acceptable for v1 (matches the "best-effort on remote-abandoned" stance already accepted in spec §2.7), but worth a CloudWatch alarm on Lambda errors as a follow-up.
- **Slice 7 deployed infrastructure stays** — the existing strategy `graphia_demo_career-AGTxmvCJKy` and its S3/SNS/IAM continue to be relevant; they just *move attachment* to the new career Memory once it's provisioned. No destroy.
- **Deploy ordering** — Lambda must be deployed and subscribed BEFORE the Runtime starts emitting events. `make deploy-stats` (already in place) absorbs this naturally: tf-apply (creates career Memory + Lambda + subscription) → create-stats-strategy → wire-env (pins `GRAPHIA_CAREER_MEMORY_ID` + strategy id) → tf-apply (Runtime gets the env vars and switches to event-emit).

---

## 4. Testing Strategy

- **Pure functions (no LLM, no AWS):**
  - `career_events.build_summary` — round-trips a synthesized event stream into the same `GameSummary` that `summarize` would produce for the equivalent end-of-game state. **This is the equivalence anchor** for local↔remote correctness.
  - `fold` — unchanged; already tested.
  - `to_json`/`from_json` — round-trip.
- **`LocalFileStatsStore`** — unchanged tests.
- **`AgentCoreCareerEventStore`:** mock at the boto3 `bedrock-agentcore` data-plane boundary; assert `record()` calls `create_event` with the right finalizer payload + returns the locally-folded aggregate; assert `load()` lists records by namespace exactly as today.
- **Consumer Lambda (unit, no AWS):** synthesize an SNS event → S3 payload chain, mock `list_events` + `list_memory_records` + `BatchUpdate`, run the handler, assert it builds the right `GameSummary`, folds, and writes the right record content. Cover the idempotency guard (a duplicate finalizer in a session already in `games_folded` ⇒ no write).
- **Local-vs-remote equivalence (rewritten):** drive the SAME sequence of game actions through a stub that captures both the local-mode emit-sequence (state-based) and the synthetic events the remote nodes would emit; run `build_summary + fold` on the events and compare to local-mode `summarize + fold`. Assert identical `CareerStats`. (Per ADR 001 parallel-impl mandate, restated.)
- **Node emission tests:** drive `day_turn`/`collect_votes`/`resolve_vote`/`resolve_night_kill`/`setup`/`endgame`/`_on_quit_decision` with a fake `memory_client` capturing emissions; assert the right `CareerEvent`s fire with the right fields (and that local mode emits nothing).
- **UI tests** — `App.run_test()`-driven full games stay in local mode (no AWS); the post-game panel correctness test stays valid (panel from local fold).
- **No real boto3 reached** — same invariant as before; mocks at the data-plane boundary, plus a defensive `boto3.client` patch on Lambda tests.