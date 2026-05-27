# ADR 008: Client-Owned Cross-Game Stats via Running-Total GameState Counters

- **ADR Number:** 008
- **Title:** Client-Owned Cross-Game Stats via Running-Total GameState Counters
- **Status:** Proposed
- **Date:** 2026-05-24
- **Authors:** Alexey Tigarev

---

## 1. Context

Spec 006 persists the human's career counters across sessions through a `StatsStore` (mechanism decided in ADR 007). Two questions remain about *how the running game feeds that store*:

1. **Where does the store I/O live** — read the prior aggregate at startup (for the greeting) and write the updated aggregate at end-of-game? Inside the LangGraph graph, in the driver, or in the UI/client layer?
2. **How are the per-game numbers computed** so that an end-of-game snapshot is authoritative — given the driver consumes the graph with `stream_mode="updates"` (deltas, not full state)?

The constraints: ADR 001 mandates that **the LangGraph topology and game logic stay mode-agnostic** (the same graph runs local and remote; only the stores differ). Store I/O is inherently mode-specific — a local file vs. AgentCore Memory calls needing AWS creds/config the **client** already holds (`config.memory_id`, region). The UI already keeps `_latest_state`, the last state snapshot, and already renders the end-of-game recap and the startup screen — both natural surfaces for "your career so far" and "this game added…". And the `stream_mode="updates"` posture (architecture, `driver.py`) means whoever computes the per-game totals must either aggregate deltas, replay history, or read totals that the graph already maintains.

This ADR decides the ownership seam and the counting mechanism together because they're coupled: a clean client-owned seam is only simple if the graph hands the client an authoritative end-of-game snapshot.

---

## 2. Alternatives Considered

### Alternative 1: Client-owned at the graph's edges + running-total `GameState` counters *(chosen)*

The UI/client layer (`ui/app.py`) owns store I/O: it reads the aggregate once at startup (greeting) and writes the folded aggregate once at end-of-game, using `_latest_state` as the source. The graph stays pure — but nodes maintain **running-total counters** in `GameState` (replace semantics): setup zero-initializes them, and the day/night nodes increment them as actions happen, so `_latest_state` is a complete, authoritative end-of-game snapshot.

- **Pros:**
  - **Keeps the graph topology mode-agnostic** (ADR 001) — no mode-specific I/O or store creds inside any node; local/remote differ only in which `StatsStore` the client constructs.
  - Store I/O lives where its inputs already are — the client holds config/creds and already owns `_latest_state`, the greeting screen, and the recap panel.
  - Graph stays pure and unit-testable — nodes have no I/O side effects; counters are plain state transitions.
  - `_latest_state` is authoritative — the client persists exactly what it reads, no delta-aggregation or history replay.
- **Cons:**
  - Stats persistence is **not** checkpointed with the graph — a client crash between graph-end and the write loses that game's record (accepted: best-effort, matches the abandon-handling posture).
  - Two hook points to maintain in `ui/app.py` (startup read, end-of-game write) plus the store seam.
  - Six new `GameState` fields, and a `stream_mode="updates"` correctness invariant: each counter must have a **single writer per step** (documented; if ever violated, the counter must move to a reducer).

### Alternative 2: In-graph dedicated stats node(s)

Add graph nodes that read the aggregate (a setup-time node) and write it (a terminal node) directly against the `StatsStore`.

- **Pros:**
  - Persistence is checkpointed with the graph — survives a client crash; the write is part of the recorded run.
  - Co-located with game lifecycle — the write node sits naturally at the end of the graph.
- **Cons:**
  - **Breaks mode-agnosticism (ADR 001)** — pushes mode-specific I/O (local file vs. AgentCore Memory creds) into the graph; the topology now differs by mode or carries an injected store.
  - Node tests must mock the store everywhere — every graph-level test inherits a stats dependency.
  - The startup read feeds a **UI greeting**, not graph logic — modeling a pure-render concern as a graph node is awkward; the value still has to cross back to the UI to be shown.

### Alternative 3: Driver-layer ownership

The driver (`driver.py`), which pumps `graph.stream`, also reads/writes the store between the graph and the UI.

- **Pros:**
  - Keeps graph pure (like Alt 1) while centralizing I/O in one non-UI place.
  - One module owns the stream→store flow.
- **Cons:**
  - The driver is deliberately thin (stream pump) — adding persistence and the startup-read coupling broadens its role.
  - The **greeting display is a UI concern**; splitting "read aggregate" (driver) from "render greeting" (UI) fragments one feature across two layers for no gain.
  - Still needs the per-game totals to be authoritative — so it *also* relies on running counters or must aggregate deltas; it doesn't avoid Alt 1's counting question, only relocates the I/O.

### Alternative 4: Post-hoc reconstruction (no running counters)

Skip new `GameState` counters; at end-of-game, reconstruct per-game numbers by replaying the message log / `kill_log` and parsing who initiated and who voted.

- **Pros:**
  - No new `GameState` fields; no `stream_mode="updates"` single-writer concern.
  - Counting logic lives in one end-of-game function.
- **Cons:**
  - **Fragile** — initiations, day-votes, and role-broken-down counts aren't all cleanly recoverable from existing logs without parsing dialogue/structured-but-incomplete records; a phrasing change breaks the count.
  - Re-derives at end-of-game what the nodes already know at action time — more code and more failure modes than incrementing a counter when the action happens.
  - No clean per-role breakdown without structured counting at the moment of action anyway.

---

## 3. Decision

Adopt **Alternative 1**: cross-game stats are **owned by the UI/client layer at the graph's edges**, and the graph hands the client an authoritative end-of-game snapshot via **running-total `GameState` counters**.

Concretely:

- **Counters in `GameState`** (replace semantics): six fields covering night-kill initiations/votes, day-execution initiations/votes, and the per-role/outcome breakdown. Setup zero-initializes them; the day/night nodes increment them as actions occur.
- **Single-writer invariant:** under `stream_mode="updates"`, each counter has exactly one writer per step, so replace is safe. Documented in the tech spec; a future second concurrent writer must convert that counter to a reducer.
- **Client I/O at edges:** `ui/app.py` reads the prior aggregate once at startup (greeting) and writes the folded aggregate once at end-of-game, reading from `_latest_state`. The store itself is `make_stats_store(config)` (local file vs. AgentCore long-term records, per ADR 007).
- **Best-effort persistence:** the end-of-game write is best-effort (timeout/try-except, console-clean per architecture §5); a crash or abandon may drop that game's record.

---

## 4. Decision Rationale

Ranked by weight:

1. **Consistency with the existing system (ADR 001's mode-agnostic graph).** The single hardest constraint is that the graph runs identically local and remote, differing only in stores. Alt 2 violates this by putting mode-specific I/O in nodes; Alts 1 and 3 preserve it. This eliminated Alt 2 outright.
2. **Best fit for where the data and surfaces already live.** The client already holds store config/creds, `_latest_state`, the greeting screen, and the recap panel. Alt 1 puts the I/O where its inputs and outputs already are; Alt 3 fragments one user-facing feature across driver and UI. This broke the tie between the two pure-graph options in favor of Alt 1.
3. **Lowest correctness risk for the counting.** Incrementing a counter at action time (Alt 1) is robust; reconstructing counts from logs (Alt 4) is fragile and re-derives what nodes already know. Running totals also make `_latest_state` authoritative, which is what makes the client seam trivial.

The choice was **not** driven by durability — Alt 2 is strictly better there (checkpointed writes). Best-effort persistence is accepted because a single-player educational game losing one abandoned game's stats is harmless, and the alternative costs the architecture's mode-agnosticism.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- **Non-checkpointed persistence** — a crash between graph-end and the client write loses that game's record. Acceptable and consistent with the existing best-effort abandon handling.
- **`stream_mode="updates"` single-writer invariant** — now a standing correctness rule for the six counters; it must be honored by any future code that touches them, or the counter moves to a reducer.
- **Surface area in `ui/app.py`** — two hook points + the store seam live in the UI layer; the UI is now the integration point for cross-game persistence.

**Future implications:**

- Establishes the pattern: **graph computes (pure, in-state); client persists (at edges).** Any future cross-session value (e.g., the deferred Moderator-recall feature) should follow the same seam rather than adding in-graph I/O.
- The post-game panel renders the **aggregate folded in memory** (the value just written), never a re-read — this dovetails with ADR 007's eventual-consistency rule and is now a shared invariant across both ADRs.
- New per-game stats are cheap to add: one counter field + one increment site + include it in the fold; no graph-topology or driver change.

**Technical debt incurred:**

- The single-writer invariant is enforced by convention/documentation, not by a type or test — a guard test (assert each counter is written by one node per step) would harden it and is owed if the counter set grows.
- Best-effort write has no retry/queue — if richer durability is ever wanted, this ADR (and the abandon posture) would be revisited, likely pulling toward Alt 2's checkpointed write.

**What would force a revisit:**

- A requirement that **no** completed game may ever be lost (would push toward in-graph checkpointed writes, Alt 2).
- Introducing concurrent writers to a counter (async per-AI tasks writing the same field) — would force reducers for those counters.

---

## 6. References

- Architecture: `context/product/architecture.md` — §2 State & Persistence (Long-Term Cross-Game Stats Store), and the driver/UI split (`driver.py`, `ui/app.py`, `_latest_state`).
- Related ADRs: 007 (the `StatsStore` mechanism this ADR feeds — long-term memory records); 001 (mode-agnostic graph + parallel store impls).
- Related CRs: `context/change-requests/002-long-term-memory-for-cross-game-stats.md`.
- Related specs: `context/spec/006-cross-game-career-stats/` — functional-spec (acceptance) + technical-considerations (§2.1 counters, §2.2 config, §3 `stream_mode="updates"` risk).
