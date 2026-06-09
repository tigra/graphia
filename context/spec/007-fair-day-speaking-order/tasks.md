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

Surfaced after 007 was verified: vote tests intermittently raised
`GraphRecursionError` (`recursion_limit=50`) — ~1/8 in isolation, more often in
the full suite (RNG entry-state shifts with collection order). **Root cause
(traced, not guessed):** the tests' Night-pointing override resolved its target
via `graph.get_state(run_config)` read **re-entrantly mid-stream**, which
returns a STALE pre-`assign_roles` snapshot (every player still `law_abiding`).
So it named the first AI — sometimes actually Mafia — which `_ai_pick_target`
rejects, falling back to `random.choice(alive_law_abiding)`, a set that
**includes the human**. A night-killed human stops interrupting, so the
Night→Day drive free-runs with no `interrupt()` and blows past 50 super-steps.
Test-harness bug only; no production change. **[Agent: testing]**

- [x] **Fix the stale-target override (`tests/test_slice7_vote.py`)** — replace all 6 `graph.get_state()`-based Night-pointing overrides with one staleness-proof helper, `_ai_point_target_from_prompt(messages)`, that parses the live `name: id` roster `mafia_pointing` rendered into the prompt and picks the first AI by name — never the human, never a stale snapshot. (An earlier `eb51582` change to `test_three_failed_votes_ends_day` had bounded that drive to halt after `day_close`; that's retained and consistent, but it was aimed at the wrong location — this override fix is the real root cause.)
- [x] **Fix the same pattern (`tests/test_vote_validation.py`)** — route its `_advance_until_human_day_turn` Night-pointing override through the same helper, and **remove the now-vestigial `random.seed(2024)` band-aid** (plus its misleading comment, the `SEED_…` constant, and the unused `import random`) — the root-cause fix supersedes it.
- [x] Stress-verify: `test_slice7_vote.py` ×60 and `test_vote_validation.py` ×50 (unseeded) in isolation, plus the full suite ×10 — **0 failures across 132 runs** (was ~1/8). Suite stays green (243 passed, 1 skipped).

_Known latent risk (not fixed here): `tests/test_slice8_endgame.py` carries the same stale-`get_state` Night-pointing pattern in 4 overrides. It did not surface in any of the 132 runs (its win-checks terminate before a free-run) and its overrides are entangled with win/loss assertions, so it's deferred to a separate careful pass rather than changed on a hunch._
