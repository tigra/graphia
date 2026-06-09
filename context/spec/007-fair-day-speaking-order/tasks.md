# Tasks: Fair Day Speaking Order (Spec 007)

Vertical slices for [spec 007](./functional-spec.md) per its
[technical-considerations](./technical-considerations.md). 007 is **test-only** —
no production change — so the app stays runnable throughout; each slice is a
self-contained, pytest-verified guard-rail. All randomness flows from one stable
base seed in-test (architecture §6), so the statistical assertions are
reproducible, not flaky. Agents: `langgraph-agentic` (attest the no-change
finding), `testing` (the suite).

- [x] **Slice 1: Lock fairness at the order producer (`_shuffle_order`)**
  - [x] Confirm and attest that `_shuffle_order` in `src/graphia/nodes/day.py` reads only `p.id` / `p.is_alive` and never `p.role` / `p.is_human` — i.e. no production change is required. Report the finding; do **not** edit the function. **[Agent: langgraph-agentic]**
  - [x] Add the seeded multi-sample test harness — a `random.Random(BASE_SEED)` master deriving N child seeds; per child, `random.seed(child)` then call `_shuffle_order`, accumulating a `position_counts[id][position]` matrix — and the three chokepoint tests: **(a) role/type independence** — same child seeds with `role` *and* `is_human` reassigned to different ids ⇒ identical order sequence; **(b) uniformity** — each player, each role, and each player-type lands in each position within an accepted tolerance of `N/7`; **(c) survivors** — repeat (b) with reduced alive sets (e.g. 5 alive, then 3). Offline (no real Bedrock). **[Agent: testing]**
  - [x] Run `uv run pytest -q`; confirm the new tests pass and the existing suite stays green. **[Agent: testing]**

- [x] **Slice 2: Lock fairness end-to-end (full-graph integration)**
  - [x] Add a full-graph integration test: drive `build_graph` (local mode) through Day rounds across **M** games seeded from the same stable base seed, with an **LLM stub** that returns a generic `DayAction(kind="speak")` for *any* call (extend the `fake_sonnet` boundary fake so rounds complete without exhausting a fixed queue) and an auto-responding human; capture each game's `day_order` (via `graph.get_state(run_config).values["day_order"]` at `day_open` and each reshuffle) and assert the same fairness distribution end-to-end. Keep **M** modest (full games are heavy; §2.3b carries the high-N weight). **[Agent: testing]**
  - [x] Run `uv run pytest -q`; confirm the integration test passes and the full suite is green. **[Agent: testing]**

---

_Determinism: every test seeds `random` in-test from one stable `BASE_SEED` (no env var, no production seed; architecture §6) — re-runs are identical, so the statistical assertions can't flake. Tolerance / N (and M) are chosen at implementation, tight enough to catch a meaningful positional skew (functional-spec §2.1, §2.2)._

---

## Post-completion hardening (test-suite determinism)

Surfaced after 007 was verified: two pre-existing vote tests drove the real
graph past Day 1 into the **unpinned Night-RNG tail**, so the super-steps a
single `graph.stream` consumed before pausing were RNG-trajectory dependent —
on some trajectories exceeding `recursion_limit=50`, making the tests pass in
isolation but flake intermittently in the full suite (RNG state at entry shifts
with collection order). Same determinism-posture domain as 007; recorded here.
No production change. **[Agent: testing]**

- [x] **Deflake `test_three_failed_votes_ends_day` (`tests/test_slice7_vote.py`)** — bound the drive to halt right after `day_close` and assert only on durable Day-1 `messages` markers (the three "vote fails" lines + the no-execution line), dropping the `swallow_recursion=True` safety net and the Night-2 `cycle>=2` / "Night falls" assertions. Same Slice-7 intent (three failed votes end the Day without an execution), now deterministic by construction.
- [x] **Deflake `test_vote_dead_player_reprompts` (`tests/test_vote_validation.py`)** — pin the mechanical-RNG trajectory (role deal + Night-1 kill tie-break/fallback) with a single local `random.seed(2024)` before the graph is built and driven, per the architecture §6-sanctioned in-test seed (not a `GRAPHIA_SEED` env protocol). The dead-target re-prompt behaviour (functional-spec 004 §2.5) holds identically under the seed.
- [x] Run `uv run pytest -q`; confirm the full suite is green (243 passed, 1 skipped).
