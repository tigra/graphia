# Technical Specification: Fair Day Speaking Order

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

The day speaking order has a single producer: `_shuffle_order(players)` in `src/graphia/nodes/day.py`, which returns `random.shuffle`-d ids of the **alive** players. It reads only `p.id` and `p.is_alive` — it never consults `p.role` or `p.is_human` — and it is the order producer at **all three** sites (`day_open` initial order, the `day_turn` round-wrap reshuffle, and the `resolve_vote` post-failed-vote reshuffle). The order is therefore already uniform and role/type-blind.

So this spec adds **no production change**; it adds automated tests that *lock that fairness in* so a future edit can't silently bias it.

Per functional-spec §2.2 the verification is **structural + statistical**, realized here as a **seeded multi-sample** design:

- One **stable base seed** deterministically derives N child seeds (a `random.Random(BASE_SEED)` master generates the children); each child seeds the module RNG for one order draw. Aggregating positions across the N draws yields the position-distribution.
- That single sample supports **both** required checks: a *deterministic* role/type-independence assertion (same child seeds + permuted role/human assignment ⇒ identical order sequence) **and** a *statistical* uniformity assertion (each player/role/type lands in each position within tolerance of an even spread).
- Because the whole sample flows from one fixed base seed, the run is **reproducible — it cannot flake** — which is exactly the project's determinism posture (architecture §6: the one RNG-deterministic test seeds `random.seed(...)` once, locally and explicitly, à la `test_dual_mode_smoke`).

Tests run at **two altitudes**: the cheap `_shuffle_order` chokepoint (high sample count) and a **full-graph integration test** that drives real Day rounds with an LLM stub (a smaller sample, end-to-end fidelity — fairness as the player actually experiences it).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 No production change — **[Agent: langgraph-agentic]**

`_shuffle_order` already depends solely on `p.id` / `p.is_alive`. Confirm by reading; do **not** edit it. If a future reviewer wants belt-and-suspenders, the structural test in §2.3a is the guard. (Attest "no production change" as this task's outcome.)

### 2.2 Seeded multi-sample harness (test helper) — **[Agent: testing]**

A small pure helper in the test module: given a `BASE_SEED`, an alive-player set, and `N`, build `master = random.Random(BASE_SEED)`, derive `N` child seeds, and for each child `random.seed(child)` then call `_shuffle_order`, accumulating a `position_counts[player_id][position]` matrix. Reproducible and framework-free. Used by §2.3.

### 2.3 Chokepoint tests — `_shuffle_order` — **[Agent: testing]**

- **(a) Role/type independence (deterministic).** Build a 7-player set (2 Mafia / 5 Law-abiding, one human). Run the multi-sample, then run it again with the **same child seeds** but role *and* `is_human` reassigned to different ids; assert the produced order sequences are **identical** — proving the order is a pure function of ids + alive-ness and never consults role or type (functional-spec §2.2, first two criteria).
- **(b) Uniformity (statistical).** Over the N-sample, assert each player lands in each of the 7 positions within an accepted tolerance of `N/7`; aggregate by role (each role's per-position share ∝ its alive population) and by player-type (the human's per-position rate ≈ any AI's). Guards against a biased shuffle that ignores role yet still favors a position (functional-spec §2.1).
- **(c) Survivors.** Repeat (b) with reduced alive sets (e.g. 5 alive, then 3) so the guarantee is shown to hold among the still-living as players are eliminated (functional-spec §2.1 last criterion).

### 2.4 Full-graph integration test — **[Agent: testing]**

Drive the real graph (`build_graph`, local mode) through Day rounds across **M** games whose seeds derive from the same stable base seed, and assert the same fairness distribution on the **actual** `day_order` the graph produces (read via `graph.get_state(run_config).values["day_order"]` at `day_open` and each reshuffle). Requirements:

- **LLM stub:** a fake Sonnet that returns a generic `DayAction(kind="speak")` for *any* call, so rounds run to completion without exhausting a fixed queue (extends the existing `fake_sonnet` boundary fake). No real Bedrock.
- **Human:** auto-respond the human's day turn with a generic speech so games advance unattended.
- **M is modest** (each full game is far heavier than a chokepoint draw); the high-N statistical weight lives in §2.3b, while this test confirms the wiring carries the fairness end-to-end.

### 2.5 Determinism & reproducibility

All randomness flows from one stable `BASE_SEED` via `random.Random` / `random.seed` **in the test body** — no env var, no production seed, no `GRAPHIA_SEED` resurrection (architecture §6; mirrors `test_dual_mode_smoke`). Re-running the suite yields identical sample results, so the statistical assertions are reproducible rather than probabilistic-flaky. All tests are offline (LLM pinned at the fake boundary).

---

## 3. Impact and Risk Analysis

- **System dependencies / behavior:** none — this is a guard-rail-tests-only change. No production code, state shape, graph topology, or UI is touched. Zero runtime risk.
- **Risk — statistical flakiness:** mitigated by the stable base seed (the sample is deterministic) plus a tolerance sized to the chosen N. The structural test (§2.3a) is exact equality, not sampling, so it never flakes.
- **Risk — integration-test cost:** full games are heavy; keep M modest and let §2.3b carry the high-N statistical confidence. If M-game runtime is material, cap M and `log`/comment the trade-off.
- **Risk — a biased shuffle slipping past a loose tolerance:** choose a tolerance tight enough to catch a meaningful positional skew at the chosen N (a chi-square test or a ±X% per-cell bound); the exact figure is set at implementation and documented in the test.
- **Determinism posture:** in-test seeding only; the §6 convention is honored, not circumvented.

---

## 4. Testing Strategy

- **Chokepoint (primary, high-N):** role/type independence (deterministic equality) + per-position uniformity (within tolerance) + survivors (reduced alive sets) — all on `_shuffle_order` via the §2.2 multi-sample harness.
- **Integration (end-to-end, modest M):** real Day rounds with an LLM stub + auto human; assert order fairness on the captured `day_order`.
- All offline, one stable base seed, under `uv run pytest`. The exact test file (new slice-named module vs. appended to the day test file) is decided at `/awos:tasks`.
