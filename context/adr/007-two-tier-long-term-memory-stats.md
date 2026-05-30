# ADR 007: Two-Tier Long-Term Memory — Exact-Counter Records Now, Semantic Memory Later

- **ADR Number:** 007
- **Title:** Two-Tier Long-Term Memory — Exact-Counter Records Now, Semantic Memory Later
- **Status:** Superseded by [ADR 008](./008-self-managed-memory-pipeline.md) (*Long-Term Memory via the Self-Managed Pipeline*) — 2026-05-30. Live audit revealed the self-managed strategy's S3/SNS/IAM payload-delivery scaffolding sat inert (no subscriber, no consumer) and the long-term-memory-strategy idea was never built end-to-end. ADR 008 rewires the pipeline as AWS intended (events → S3/SNS → consumer Lambda → records), making the scaffolding load-bearing; the two-tier framing in this ADR is no longer in force.
- **Date:** 2026-05-28
- **Authors:** Alexey Tigarev

---

## 1. Context

Phase 3 — Long-Term Cross-Game Memory & Career Stats (per CR 002, *Long-Term AgentCore Memory In; AI Tool-Use Demoted to Further Improvements*) — exists to demonstrate the **long-term cross-session AgentCore Memory** pattern, a distinct capability from the per-game diary Memory pattern delivered in Phase 2. Spec 006 (*Cross-Game Career Stats*) is the vehicle: a persistent career aggregate surfaced via a pre-game greeting and a post-game panel.

The forcing function is a **collision between the demonstration mandate and an exactness requirement**:

- The reference-project mandate (CR 002) is to genuinely exercise the long-term-Memory **tier** — short-term events (the ≤365-day raw-event tier) would not exercise it, so they were not carried as an alternative here.
- AgentCore Memory's *built-in* long-term strategies (`SEMANTIC` / `SUMMARIZATION`) extract insights via an LLM and are retrieved by **semantic search** — inherently non-deterministic, so they cannot serve the **exact integer counters** spec 006's acceptance criteria require (games played, win rate by role, kills attempted vs. successful, votes called/cast).

The only path that satisfies both constraints at once is to author long-term memory **records** directly (bypassing the LLM-extraction path) under a **self-managed (custom)** strategy, and read them back deterministically by namespace. The decision below also fixes the **sequencing** of the genuinely *meaningful* (semantic) use of long-term Memory, which is real but not needed by the exact-counter feature.

---

## 2. Alternatives Considered

### Alternative 1: Self-managed (custom) long-term Memory strategy — author exact records *(chosen for Layer 1)*

Persist the career aggregate as self-authored long-term memory **records** via the batch-record APIs (`BatchCreateMemoryRecords` / `BatchUpdateMemoryRecords`) under a self-managed strategy, read back deterministically with `ListMemoryRecords` by namespace. Authoring record content directly bypasses the LLM-extraction path the built-in strategies use.

- **Pros:**
  - Exact counter read-back — authoring the record content directly keeps the integers precise, meeting spec 006's acceptance criteria.
  - Deterministic namespace reads — `ListMemoryRecords` by namespace, no semantic-search fuzziness.
  - Demonstrates the long-term-Memory **tier mechanics** (records + namespaces) — *but only the mechanics; this is not a faithful demonstration of the realistic, meaningful use of the feature, which is bypassed here and deferred to Layer 2 (Alt 3).*
- **Cons:**
  - The feature is used as a structured **key-value store** — its distinctive consolidation capability is set aside.
  - Higher infrastructure footprint / cost than a plain table or bare records.
  - Most complex path; the batch-record APIs **may or may not** be wrapped by the vendored `bedrock_agentcore` SDK (1.9.0) — this must be **checked at implementation, not guessed**.

### Alternative 2: Just create long-term records via the API (no formal custom-consolidation strategy / scaffolding)

Write long-term records via the API in the most minimal way it allows, avoiding the full custom-strategy apparatus.

- **Pros:**
  - Sidesteps the S3/SNS/IAM payload-delivery scaffolding that a configured strategy requires (see Alt 1) — viable only if long-term records can be written without configuring a strategy at all.
  - Still exercises the persistent long-term-record tier + namespaces, with exact read-back.
  - Smaller Terraform surface than provisioning a full self-managed strategy.
- **Cons:**
  - Weaker demonstration — closer to raw record CRUD than the intended Memory-strategy pattern a real product would use.

*Relationship to Alt 1:* a configured strategy **requires** the S3/SNS/IAM payload-delivery scaffolding — a known fact about AgentCore Memory, not an open question. Alt 2's lighter footprint therefore holds *only* if long-term records can be written without configuring a strategy at all; if they cannot, Alt 2 reduces to Alt 1. (Whether the vendored SDK wraps the batch-record APIs remains the one item to verify at implementation.)

### Alternative 3: Built-in SUMMARIZATION / SEMANTIC strategy *(the meaningful use — deferred to Layer 2, a later phase)*

Use the built-in long-term strategies: LLM-consolidated records retrieved by semantic search.

- **Pros:**
  - Exercises the feature's **distinctive capability** (LLM consolidation + semantic retrieval) — the genuinely meaningful long-term-Memory use.
  - Enables a richer, player-facing **characterization** greeting instead of templated counters.
  - Natural substrate for the future narrative-recall direction (per-game recaps, city-name identity, Moderator mid-game recall).
- **Cons:**
  - **Non-deterministic** — cannot guarantee the exact counters spec 006 requires (the disqualifier for using it as Layer 1 now).
  - Larger scope — touches the Phase 6 Moderator-recap surface and introduces per-game city-name identity.
  - Premature — the realistic career-stats shape (exact numbers) doesn't need semantic memory yet.

### Alternative 4: Forgo Memory entirely — store counters in S3 / DynamoDB

Skip AgentCore Memory for stats; persist the aggregate in S3 or DynamoDB, updated directly from the AgentCore Runtime.

- **Pros:**
  - Easiest to implement — trivial read-modify-write from the Runtime against well-trodden services.
  - Exact, deterministic, and cheap — no Memory data-model or eventual-consistency constraints.
  - No strategy/extraction scaffolding to provision.
- **Cons:**
  - **No Memory demonstration** — fails Phase 3's entire stated purpose (CR 002).
  - Adds DynamoDB/S3 as a separate persistence service outside the AgentCore Memory story.
  - Sidesteps the very pattern the phase exists to show, in a reference project.

---

## 3. Decision

Adopt a **two-tier long-term-Memory roadmap** for cross-game career data, rather than a single one-shot choice:

- **Layer 1 — exact-counter records (now, spec 006).** In remote mode, persist the exact `CareerStats` aggregate via **Alternative 1**: a self-managed (custom) long-term Memory strategy, authoring records directly and reading them deterministically by namespace. Local mode persists the same data shape to a file (per ADR 001's dual-mode store pattern). This delivers the exact-counter career stats the functional spec requires while exercising the long-term-Memory tier's mechanics.
- **Layer 2 — semantic / narrative memory (a later phase).** The genuinely *meaningful* use of long-term Memory — **Alternative 3**'s built-in `SUMMARIZATION` / `SEMANTIC` strategies driving a characterization greeting and the deferred narrative-recall feature (per-game recaps, city-name identity, Moderator mid-game recall) — is committed to a later phase, layered onto the same Memory resource.

Alternatives 2 and 4 are rejected for Layer 1: Alt 4 forgoes the Memory demonstration outright; Alt 2 is viable and lighter but a weaker demonstration of the supported strategy pattern (and may, pending verification, turn out to be the same implementation as Alt 1).

---

## 4. Decision Rationale

**Primary rationale category: stakeholder mandate (CR 002).** The stakeholder asked for a hands-on demonstration of the long-term cross-session Memory pattern, specifically distinct from the per-game diary pattern. That mandate eliminates Alt 4 (forgo Memory) immediately. Alt 3 (semantic now) would be the most *meaningful* demonstration but cannot meet spec 006's exact-counter acceptance criteria, so it is sequenced into Layer 2 rather than dropped. Alt 2 (bare records) satisfies the mandate more lightly but is a weaker showing of the supported strategy pattern.

Alternative 1 is the only option that satisfies **both** the demonstration mandate **and** the exactness requirement. Its costs — more complexity than the simplest path, higher infrastructure footprint, and using the feature as a key-value store for now — are accepted precisely because the meaningful semantic use is not abandoned but explicitly scheduled as Layer 2. The choice was *not* driven by lowest cost (Alt 4) or lowest complexity (Alt 2 / Alt 4).

---

## 5. Decision Consequences

**Trade-offs accepted:**

- More implementation complexity than the simplest path (Alt 4's table, or Alt 2's bare records).
- Long-term Memory is used as a structured **key-value store** for now — the distinctive consolidation capability is set aside until Layer 2.
- Higher infrastructure footprint / cost than an S3/DynamoDB table or bare records.

**Future implications:**

- **Layer 2 is a committed direction** — semantic / summarization narrative memory (per-game recaps, city-name identity, Moderator recall) lands in a later phase, on the same Memory resource.
- The stats record shape is **constrained by AgentCore Memory's data model** — bounded record size, per-actor namespaces, eventual-consistency semantics (inherits the constraint from ADR 001).
- A configured strategy **requires** the S3/SNS/IAM payload-delivery scaffolding (known fact), even though the auto-extraction trigger is never fired for our self-authored records — this is real standing infrastructure. What remains to **verify at implementation**: whether the vendored SDK wraps the batch-record APIs, and whether long-term records can be written without configuring a strategy at all (which is what would distinguish Alt 2 from Alt 1).

**Technical debt incurred:**

- Possible **direct boto3 data-plane fallback** (`batch_create_memory_records` / `batch_update_memory_records` / `list_memory_records`) if the vendored SDK doesn't wrap them — hand-rolled calls to maintain.
- **Equivalence tests owed** — local-file vs. long-term-record store equivalence, per the parallel-impl mandate in ADR 001.

---

## 6. References

- Architecture: `context/product/architecture.md` — §2 State & Persistence
- Related ADRs: `context/adr/001-hosted-agentcore-with-local-mode.md` — ADR 001 (*Hosted AgentCore Runtime with Preserved Local Mode*); establishes the dual-mode store pattern and the AgentCore Memory data-model constraint this decision inherits.
- Related CRs: `context/change-requests/002-long-term-memory-for-cross-game-stats.md` — CR 002 (*Long-Term AgentCore Memory In; AI Tool-Use Demoted to Further Improvements*); the stakeholder mandate driving this decision.
- Related specs: `context/spec/006-cross-game-career-stats/` — spec 006 (*Cross-Game Career Stats*); the functional spec + technical considerations this decision implements.
