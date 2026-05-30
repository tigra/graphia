# ADR 008: Long-Term Memory via the Self-Managed Pipeline (Supersedes ADR 007)

- **ADR Number:** 008
- **Title:** Long-Term Memory via the Self-Managed Pipeline (Supersedes ADR 007)
- **Status:** Accepted
- **Date:** 2026-05-30
- **Authors:** Alexey Tigarev

---

## 1. Context

Phase 3 / spec 006 (*Cross-Game Career Stats*) persists a cross-game career aggregate as AgentCore long-term Memory records. **ADR 007** (*Two-Tier Long-Term Memory — Exact-Counter Records Now, Semantic Memory Later*) chose Alternative 1: provision a self-managed AgentCore Memory strategy plus its required S3 bucket + SNS topic + IAM role payload-delivery scaffolding, and have the Runtime write exact `CareerStats` records *directly* via `BatchCreateMemoryRecords` / `BatchUpdateMemoryRecords`, bypassing the strategy's delivery pipeline.

A **live audit on 2026-05-29** of the deployed remote game made the cost visible: the SNS topic has no subscribers, the S3 bucket no consumers, no Lambda exists to process delivered payloads — the Runtime writes records directly. ADR 007 §5 *Future Implications* acknowledged this in technical terms (*"real standing infrastructure... auto-extraction trigger is never fired"*) but did not surface it as an Alternative 1 con in its §2 head-to-head comparison; a reader weighing alternatives would not see the dead-infra cost at the choice point. More fundamentally: **the long-term-memory-strategy idea CR 002 mandated — to demonstrate the long-term cross-session Memory pattern end-to-end — was never actually built. The infra was an empty shell.**

This violates Graphia's **design-driven-by-realistic-needs principle**: provisioned infrastructure must serve a real architectural or player need. Reconsidering the choice this turn surfaced two fixes for the gap — Alternative 3 (remove the scaffolding) closes it by deletion; this ADR's Alternative 1 closes it by **building the consumer pipeline AWS intended**, making the scaffolding load-bearing for a real feature. The latter is chosen because it finally implements the strategy idea CR 002 asked for.

---

## 2. Alternatives Considered

### Alternative 1: Self-managed pipeline as designed *(chosen)*

Rewire the cross-game stats persistence to use the self-managed strategy's intended end-to-end flow. The Runtime emits per-game events via `create_event` on AgentCore Memory; the self-managed strategy's `invocationConfiguration` delivers matching events to the existing S3 bucket / SNS topic; a **new consumer Lambda** — modeled on the Phase 2 diary Lambda pattern (ADR 005, *Gateway Tools via Lambda Targets*) — subscribes to the SNS topic, reads delivered payloads from S3, runs the `fold` consolidation, and writes the updated career record back via `BatchCreateMemoryRecords` / `BatchUpdateMemoryRecords`. Client-side reads remain `ListMemoryRecords` by namespace (unchanged).

- **Pros:**
  - **Scaffolding becomes load-bearing** — S3/SNS/IAM are no longer empty shells; they're on the actual data path. Closes the realistic-needs-principle gap (no provisioned-but-unused infra).
  - **Full self-managed pattern demonstrated** — exercises the intended AWS pattern end-to-end (events → delivery → consumer → consolidated records), not a bypass. Closer to how a real product would use AgentCore Memory; the long-term-memory-strategy IDEA is actually built.
- **Cons:**
  - **Larger operational surface** — adds a consumer Lambda (deployment artifact, IAM trust, monitoring, version pin) on top of the Runtime + Memory.
  - **Eventually consistent updates** — event → delivery → Lambda → record-write is asynchronous; the long-term record lags the originating event slightly. The greeting at next launch is still correct (no read-after-write gap there), but the panel can no longer assert "aggregate just-written before the next read."
  - **Throws away working code** — the direct-batch-write path in `AgentCoreLongTermStatsStore` (Slice 6) gets removed even though it works end-to-end — a sunk cost of ADR 007's implementation.

### Alternative 2: Direct writes, inert scaffolding *(ADR 007 Alt 1, status quo — superseded)*

What we built in Slice 6: the Runtime calls `batch_create_memory_records` / `batch_update_memory_records` directly, passing `memoryStrategyId`, while the S3/SNS/IAM delivery pipeline sits unused. The implementation this ADR supersedes — recorded so the rejection is explicit.

- **Pros:**
  - Already implemented & working — code exists (`AgentCoreLongTermStatsStore`), the strategy was created live (`graphia_demo_career-AGTxmvCJKy`), end-to-end batch-record write/read works. Sunk cost preserved if kept.
  - Synchronous writes — the long-term record updates atomically with the panel render; no event-delivery delay.
  - Lower operational surface — no consumer Lambda; just Runtime → Memory. Fewer moving parts.
- **Cons:**
  - Scaffolding sits inert — provisions infra (S3 bucket, SNS topic, IAM role) that does nothing on the data path. The realistic-needs-principle violation that forced this supersedence.
  - Strategy idea unimplemented — long-term records are used as a structured key-value store; the long-term-memory-strategy IDEA (the conceptual heart of ADR 007 and CR 002) is provisioned but never realised.
  - Bypasses intended AWS pattern — uses a self-managed strategy without using its delivery/consolidation flow — closer to misuse than demonstration.

### Alternative 3: Drop strategy + scaffolding entirely *(ADR 007 Alt 2, reconsidered)*

Write long-term records by namespace with NO strategy and therefore no S3/SNS/IAM scaffolding. Research this turn (botocore service models + AWS docs) confirmed feasibility: `memoryStrategyId` is optional on `BatchCreateMemoryRecords`; a Memory can exist with zero strategies; direct writes are independent of the extraction pipeline. Rejected because it closes the realistic-needs gap **by removal** rather than by implementing the long-term-memory-strategy idea.

- **Pros:**
  - Simplest implementation — no S3/SNS/IAM, no consumer Lambda, no strategy at all. Smallest Terraform + smallest code path.
  - No dead infra — closes the realistic-needs gap by deletion: nothing provisioned-but-unused. Lowest operational footprint and cost.
  - Honest about what we actually use — records-by-namespace IS what the runtime actually exercises; the strategy was always overhead for our exact-counter case.
- **Cons:**
  - Long-term-memory-strategy unbuilt — same conceptual gap as Alternative 2: the strategy idea is removed, not implemented. The supersedence driver ("actually build the long-term-memory-strategy idea") isn't satisfied — just sidestepped.
  - Weaker Memory demonstration — records as a KV store with no strategy/consolidation; closer to "long-term records exist" than "the long-term-memory pattern is exercised end-to-end."

---

## 3. Decision

Adopt **Alternative 1**: rewire cross-game stats persistence to use the self-managed strategy's intended pipeline end-to-end.

Concretely:

- **Runtime** stops calling `batch_create_memory_records` / `batch_update_memory_records` directly. It emits per-game events via `create_event` on AgentCore Memory, carrying the per-game summary payload.
- **Self-managed strategy** (already provisioned) keeps its existing `invocationConfiguration` (the S3 bucket + SNS topic) — now actually used: AgentCore delivers matching events to S3 and notifies via SNS.
- **New consumer Lambda** subscribes to the SNS topic, reads the delivered payload from S3, runs the `fold` consolidation (the same pure-function aggregation used today, moved server-side into the Lambda), and writes the updated career record back via `BatchCreateMemoryRecords` (first ever) / `BatchUpdateMemoryRecords` (subsequent). It follows the Phase 2 diary Lambda packaging/IAM pattern from ADR 005.
- **Client reads** stay `ListMemoryRecords` by namespace — unchanged. The greeting at launch is unaffected by the new write path.
- **Slice 6's `AgentCoreLongTermStatsStore` direct-batch-write code** is thrown away. Per the user's directive, **a new slice in `context/spec/006-cross-game-career-stats/tasks.md` replaces the old Slice 6** (rather than refactoring spec 006's `technical-considerations.md` in place) — the original slice is marked superseded but kept for history.
- **ADR 007's two-tier framing** (Layer 1 exact records now; Layer 2 semantic later) is no longer in force. The successor framing is single: build the long-term-memory-strategy pattern end-to-end now via the consumer pipeline.

Alternatives 2 (status quo) and 3 (drop scaffolding) are rejected: Alt 2 leaves the conceptual gap that drove this supersedence; Alt 3 closes the realistic-needs gap by deletion, not by implementing the strategy idea CR 002 mandated.

---

## 4. Decision Rationale

**Primary rationale category: demonstration / reference value.** Graphia is a reference project; its demonstration value depends on actually exercising AWS patterns end-to-end, not on convenient bypasses. The self-managed strategy with payload delivery is **the AWS-intended pattern** — until the consumer pipeline exists, the long-term-memory-strategy idea is provisioned but never built, and the scaffolding is an empty shell. Building the consumer-Lambda pipeline turns that shell into the demonstration CR 002 (*Long-Term AgentCore Memory In*) asked for.

This rationale also rests squarely on the **design-driven-by-realistic-needs principle**: the infrastructure now serves a real architectural feature instead of standing idle. Alternative 2 leaves the scaffolding inert and the strategy unimplemented; Alternative 3 honours realistic-needs by *removing* the scaffolding but at the cost of never building the long-term-memory-strategy idea — neither matches the reference-value rationale at the heart of CR 002.

The choice was *not* driven by lowest cost (Alt 3 wins there), nor by lowest implementation effort (Alt 2 wins, since it already exists). It was driven by the project's purpose: faithfully demonstrate the long-term cross-session Memory pattern.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- **Larger operational surface** — adds a consumer Lambda (deployment artifact, IAM trust, monitoring, version pin) on top of the Runtime + Memory.
- **Eventually consistent updates** — event → S3/SNS delivery → consumer Lambda → record write is asynchronous; the long-term record lags the originating event slightly. The greeting at next launch is unaffected (no read-after-write gap there).
- **Sunk cost of Slice 6** — `AgentCoreLongTermStatsStore`'s direct-batch-write code + its boto3-mocked tests + the equivalence test get removed/replaced. Working code thrown away because it implemented the wrong design.

**Future implications:**

- **Consumer Lambda follows the diary Lambda pattern** — Phase 2's diary Lambdas (ADR 005, *Gateway Tools via Lambda Targets*) establish the zip-deployed Python tooling + IAM pattern; the consumer is one more handler in that established mold, not a new infrastructure category.
- **Implementation lands as a new slice, not an in-place refactor** — spec 006's `tasks.md` gains a new slice that supersedes Slice 6 (*AgentCore long-term-record remote backend + equivalence*); the original slice is marked superseded but kept for history (the new slice's commit message and the slice header itself name the supersession explicitly).

**Technical debt incurred:**

- **Increased implementation complexity compared to direct writes** — the end-to-end pipeline (event-emit + delivery configuration + consumer Lambda + consolidation + write-back) is more code, more deployment surface, and more moving parts than the prior direct-batch-write path. Mitigated by following the established Phase 2 diary Lambda pattern (ADR 005), but the complexity is real and is the deliberate cost of the faithful demonstration.

---

## 6. References

- Architecture: `context/product/architecture.md` — §2 State & Persistence.
- Related ADRs:
  - `context/adr/007-two-tier-long-term-memory-stats.md` — ADR 007 (*Two-Tier Long-Term Memory — Exact-Counter Records Now, Semantic Memory Later*). **Superseded by this ADR.** Its decision (Alt 1: direct writes with inert scaffolding) is no longer in force.
- Related CRs:
  - `context/change-requests/002-long-term-memory-for-cross-game-stats.md` — CR 002 (*Long-Term AgentCore Memory In; AI Tool-Use Demoted to Further Improvements*); the original stakeholder mandate to demonstrate long-term cross-session Memory. This ADR finally satisfies that mandate end-to-end via the self-managed pipeline.
- Related specs:
  - `context/spec/006-cross-game-career-stats/` — spec 006 (*Cross-Game Career Stats*); a new slice in its `tasks.md` replaces the old Slice 6 (which implemented the direct-write approach this ADR supersedes).