---
spec: 007-fair-day-speaking-order
spec_title: Fair Day Speaking Order
introduced_on: 2026-06-03
---

# Concepts introduced in this increment

## Testing — proving an invariant by seeded sampling

- **Seeded multi-sample from one master seed** (`seeded-multi-sample-from-master`) — Derive N child seeds from a single `random.Random(BASE_SEED)` master and re-seed the module RNG before each draw, turning a non-deterministic helper into a *fixed, reproducible* sample so statistical assertions can't flake.
- **Structural invariance by same-seed equality** (`same-seed-input-invariance`) — Prove a function *ignores* certain inputs by running it under the same seed sequence twice, varying only those inputs (here: reassigning `role` / `is_human`), and asserting the outputs are byte-identical — an exact proof, not a sampled one.
- **Reproducible statistical uniformity within a σ-justified band** (`reproducible-statistical-uniformity`) — Assert a distribution is even by checking each cell against an expectation with a tolerance sized as a multiple of the binomial standard deviation; the fixed base seed makes it pass identically every run. Includes *per-capita* (population-proportional) role fairness rather than a naïve 50/50.

## Testing — strategy & altitude

- **Chokepoint + integration two-altitude testing** (`chokepoint-plus-integration-altitude`) — Lock an invariant both at the single producing function (cheap, high sample count) *and* at the assembled-graph level (modest sample), so a refactor that stops routing through the producer is still caught.
- **Guard-rail tests for an already-correct invariant** (`guard-rail-tests-no-production-change`) — A spec that ships *only* tests — no production change — to pin behaviour that is currently correct so a future edit cannot silently break it.

## Testing — integration-harness mechanics

- **Unbounded structured-output LLM stub** (`unbounded-structured-output-stub`) — A stateless fake that returns a generic, schema-appropriate value for *any* number of calls (dispatching on the schema bound via `with_structured_output`), so an open-ended game loop runs to completion offline — unlike a finite-queue fake that would exhaust.
- **Per-game checkpoint isolation under a coarse thread-id clock** (`per-test-checkpoint-isolation`) — Because the graph builder derives its `thread_id` / checkpoint filename from `datetime.now()` at second precision, many sub-second test games collide and resume each other; giving each game its own `GRAPHIA_CHECKPOINT_DIR` isolates them (and surfaces a latent production edge).
- **Stable-label aggregation across varying identities** (`stable-label-aggregation`) — When per-game identities (UUIDs) differ but a stable label (display name) repeats, key the cross-game tally on the label and re-read each game's role deal, so a distribution aggregated over many runs buckets correctly despite the churn.
