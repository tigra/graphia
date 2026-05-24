# ADR 007: Cross-Game Stats as Self-Authored AgentCore Long-Term Memory Records

- **ADR Number:** 007
- **Title:** Cross-Game Stats as Self-Authored AgentCore Long-Term Memory Records
- **Status:** Accepted
- **Date:** 2026-05-24
- **Authors:** Alexey Tigarev

---

## 1. Context

Spec 006 (`context/spec/006-cross-game-career-stats/`) persists a human player's **exact** career counters across game sessions — night-kill initiations and votes, day-execution initiations and votes, games played, wins/losses, broken down by role. CR 002 (`context/change-requests/002-long-term-memory-for-cross-game-stats.md`) put this in Phase 3 hard scope for one stated reason: it is **the** explicit demonstration of cross-session AgentCore **long-term Memory** in the reference project. ADR 001 already committed to a parallel-impl `StatsStore` (local file vs. AgentCore Memory) behind one interface; this ADR decides **how the remote impl actually stores the data inside AgentCore Memory**.

The forcing tension surfaced during research into AgentCore Memory's data model. Memory has two distinct tiers, and "long-term scope" is not a free-text dial:

- **Short-term events** — raw turns written with `CreateEvent`, read back deterministically by actor/session with `ListEvents`. Retention is configurable (up to 365 days) but this is the *short-term* tier, not the long-term-Memory feature.
- **Long-term memory records** — persistent, namespace-organized records produced by a **strategy**. Built-in strategies (SEMANTIC / SUMMARIZATION / USER_PREFERENCE) extract insights with an LLM and are retrieved by **semantic search**. A **self-managed (custom)** strategy instead lets the application author records directly via the batch-record APIs and read them back deterministically by namespace.

The requirement is **exact integer counters with exact round-trip** — which collides head-on with the built-in long-term strategies (LLM extraction can neither guarantee exact integers nor be retrieved deterministically). So the decision is a genuine trade-off between "use a tier that's exact but arguably not the long-term feature" and "use the long-term feature in the one mode that preserves exactness."

This decision also corrects a wording bug it inherited: architecture.md §2 and ADR 001 both say "AgentCore Memory at long-term scope," conflating the long-term-Memory **feature** (records + strategies + namespaces) with merely long-lived data. This ADR pins the precise mechanism; architecture.md §2 is updated to match.

---

## 2. Alternatives Considered

### Alternative A: Short-term events as a rolling aggregate

Store the career aggregate as a short-term event: `record()` reads the newest `career_aggregate` event with `ListEvents`, folds the just-finished game in, and writes a new event with `CreateEvent` under a **stable** actor id (`"human-career"`) and constant session id. The diary store already uses exactly this `create_event`/`list_events` surface, so it's the lowest-friction reuse.

- **Pros:**
  - Reuses the exact boto3 Memory surface the diary store already exercises (`diary_store.py:160-186`) — zero new API learning, no new strategy resource.
  - Deterministic and exact — `ListEvents` by actor/session returns the authoritative newest aggregate; no LLM in the path.
  - No extra infrastructure — no self-managed strategy, no S3/SNS/IAM payload scaffolding.
- **Cons:**
  - **Does not demonstrate the long-term-Memory feature** — it's the short-term tier with a long retention window, which is precisely what CR 002 / Phase 3 exist to show. The reference value (the whole point) goes unrealized.
  - Append-only event log grows one event per game forever for a single logical value — read-newest works but the store accumulates dead events.
  - Misrepresents practice — a reader studying "how do I use AgentCore long-term Memory" would find short-term events wearing a long-term label.

### Alternative B: Self-authored long-term memory records *(chosen)*

Store the career aggregate as a **single long-term memory record** under a **self-managed (custom) strategy**. `load()` reads it with `ListMemoryRecords` filtered to a stable career namespace (`/career/human-career/`); `record()` folds the game in and writes with `BatchUpdateMemoryRecords` (or `BatchCreateMemoryRecords` on first write), authoring the `CareerStats` JSON as the record `content`. Identity is stable across games (`actor_id="human-career"`, fixed namespace).

- **Pros:**
  - **Genuinely exercises long-term Memory** — records, namespaces, batch-record APIs, a self-managed strategy — which is exactly the Phase 3 / CR 002 demonstration mandate.
  - **Exact round-trip** — because the application authors the record content directly, the LLM-extraction path that built-in strategies use is bypassed; integers survive verbatim.
  - **Deterministic read** — `ListMemoryRecords` by namespace returns the one authoritative record; no semantic-search ranking in the authoritative path.
  - Single record updated in place (rolling aggregate) — no per-game accumulation of dead entries.
- **Cons:**
  - **New infrastructure** — requires a self-managed strategy on the Memory resource, plus the payload-delivery scaffolding (S3 bucket + SNS topic + IAM role) the strategy config requires, even though the auto-extraction trigger is unused.
  - **New API surface** — `batch_create/update_memory_records`, `list_memory_records`; the vendored `bedrock_agentcore` SDK (1.9.0) wraps events but may not wrap batch records, possibly forcing direct boto3 `bedrock-agentcore` data-plane calls.
  - **Eventual consistency** — long-term record writes may not be immediately readable; the design must not read-after-write within one game.

### Alternative C: Built-in long-term strategy (SEMANTIC / SUMMARIZATION)

Feed game outcomes as events and let a built-in long-term strategy extract "memories," retrieved with `RetrieveMemoryRecords` (semantic search).

- **Pros:**
  - Zero custom record authoring — AgentCore's managed extraction does the work; this is the "intended" built-in long-term path.
  - Showcases the headline managed-Memory feature (automatic insight extraction) most directly.
- **Cons:**
  - **Cannot meet the exact-counter requirement** — LLM extraction does not guarantee exact integers, and semantic retrieval is ranked/approximate, not an authoritative key-value read. A career-stats panel that must show *17 night-kills* cannot be backed by approximate recall.
  - Non-deterministic, untestable as exact data — equivalence with the local-file impl (ADR 001's mandate) becomes impossible.
  - Adds per-write LLM cost and latency for data we already hold exactly in memory.

### Alternative D: A separate exact store (DynamoDB / S3 object), not Memory

Persist the aggregate to a purpose-built exact store in remote mode and leave AgentCore Memory out of the stats path entirely.

- **Pros:**
  - Exact, deterministic, mature — a single-item DynamoDB table or one S3 JSON object is the textbook fit for one small rolling aggregate.
  - No strategy/extraction complexity, no eventual-consistency surprises (with strongly-consistent reads).
- **Cons:**
  - **Fails the demonstration mandate** — CR 002 / Phase 3 exist specifically to show cross-session AgentCore *Memory*; hand-rolling DynamoDB/S3 sidesteps the very feature the increment is meant to teach, and contradicts ADR 001's "no hand-rolled equivalents" stance.
  - Introduces a second persistence technology and its IAM/Terraform surface for one tiny value — more moving parts than the managed Memory resource already in the architecture.

---

## 3. Decision

Adopt **Alternative B**: in remote mode, persist the human's career aggregate as a **single self-authored long-term memory record** under a **self-managed (custom) AgentCore Memory strategy**, written with `BatchCreate/UpdateMemoryRecords` and read deterministically with `ListMemoryRecords` by a stable career namespace. Local mode keeps the file-backed impl (ADR 001). Both sit behind the one `StatsStore` interface and are covered by a parallel-impl equivalence test.

Concretely:

- **Identity:** stable `actor_id="human-career"` (NOT the per-game player id, which is random each game) and fixed namespace `/career/human-career/`, so one logical record accumulates across all games.
- **Read:** `ListMemoryRecords` by namespace → the one record's `content` (zeroed `CareerStats` if absent).
- **Write:** fold the finished game's summary into the loaded aggregate, then `BatchUpdateMemoryRecords` (or `BatchCreateMemoryRecords` first time).
- **Exactness:** the application authors the record `content` directly; no built-in extraction strategy is used for stats.
- **Consistency rule:** never read-after-write within one game — the post-game panel renders the aggregate just folded in memory; the greeting reads at the *next* launch.
- **SDK:** prefer the `bedrock_agentcore` wrapper; fall back to the boto3 `bedrock-agentcore` data-plane client if batch-record methods aren't wrapped (confirm at implementation).

---

## 4. Decision Rationale

Ranked by weight:

1. **Best fit for the realistic-needs case under a hard constraint.** Two requirements had to hold at once: (a) exact, deterministic counters, and (b) a faithful demonstration of cross-session long-term Memory. Alt C satisfies (b) but fails (a) outright. Alts A and D satisfy (a) but fail (b) — A by using the short-term tier under a long-term label, D by not using Memory at all. Only Alt B satisfies both, and that intersection is the decision.
2. **Consistency with existing system / stakeholder mandate.** CR 002 put long-term Memory in Phase 3 *to demonstrate it*, and ADR 001 committed to "no hand-rolled equivalents." Alt D directly contradicts both; Alt B honors them.
3. **Lowest representational risk for a reference project.** Graphia's purpose is to *model real practice*. Alt A would teach a reader the wrong thing (short-term events mislabeled as long-term Memory). Alt B shows the actual long-term-records-with-a-self-managed-strategy pattern a practitioner needs.

The choice was explicitly **not** driven by lowest cost or lowest operational risk — Alt A (no new infra) and Alt D (mature, strongly-consistent) both win those axes. The new strategy + S3/SNS/IAM scaffolding and the eventual-consistency handling are accepted as the deliberate price of a faithful demonstration with exact data.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- New remote infrastructure for one small value — a self-managed strategy plus S3/SNS/IAM payload-delivery scaffolding the strategy config requires, even though the auto-extraction trigger is never fired. Terraform (Phase 2 module) must add it.
- New, less-trodden API surface — batch-record + list/retrieve calls not previously exercised; possible direct boto3 data-plane use if the SDK wrapper lags.
- Eventual consistency designed around, not away — the no-read-after-write rule (panel renders the folded value; greeting reads next launch) is now a correctness invariant, not just a nicety.

**Future implications:**

- Establishes the project's pattern for **exact** long-term Memory data: self-managed strategy + self-authored records + deterministic namespace reads. Any future exact cross-session value should follow this, reserving built-in SEMANTIC/SUMMARIZATION strategies for genuinely fuzzy recall.
- The deferred "Moderator recalls similar past games / unique city names" direction (spec 006 Out-of-Scope) is the natural place built-in summarization strategies *would* fit — that future CR can layer narrative recall on top of this exact-counter record without disturbing it.
- The self-managed strategy resource now exists on the Memory instance and could host additional self-authored record types later at no extra strategy cost.

**Technical debt / follow-ups incurred:**

- **architecture.md §2 wording corrected** by this ADR (line: "AgentCore Memory at long-term scope" → precise records/self-managed-strategy phrasing). ADR 001 §3's identical loose phrasing remains as historical record, scoped by this ADR.
- The "SDK wraps batch records?" question is deferred to implementation; if it doesn't, a thin boto3 fallback shim is owed.
- The exact `selfManagedConfiguration` shape (whether S3/SNS are strictly mandatory when the trigger is unused) must be confirmed against the live API during the Terraform work; the cost estimate assumes they are required.

**What would force a revisit:**

- AgentCore adding a first-class exact/key-value long-term record mode without a self-managed strategy (would drop the S3/SNS scaffolding).
- The deferred summarization/recall feature landing and wanting to *unify* exact stats and narrative recall under one strategy.

---

## 6. References

- Architecture: `context/product/architecture.md` — §2 State & Persistence (Long-Term Cross-Game Stats Store); wording corrected by this ADR.
- Related ADRs: 001 (parallel-impl `StatsStore`, "AgentCore Memory at long-term scope" — refined here).
- Related CRs: `context/change-requests/002-long-term-memory-for-cross-game-stats.md` (put long-term Memory demonstration in Phase 3 scope).
- Related specs: `context/spec/006-cross-game-career-stats/` — functional-spec + technical-considerations (§2.1 store impls, §3 risk).
- External docs:
  - [Amazon Bedrock AgentCore Memory — long-term memory & strategies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-getting-started.html)
  - [AgentCore Memory data-plane API (BatchCreateMemoryRecords / ListMemoryRecords)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)
