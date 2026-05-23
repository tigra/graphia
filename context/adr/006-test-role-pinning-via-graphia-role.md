# ADR 006: Test role-pinning convention: `GRAPHIA_ROLE` replaces magic-seed-for-role

- **ADR Number:** 006
- **Title:** Test role-pinning convention: `GRAPHIA_ROLE` replaces magic-seed-for-role
- **Status:** Proposed
- **Date:** 2026-05-23
- **Authors:** Alexey Tigarev

---

## 1. Context

Graphia's test suite (10 files, ~33 call sites) pins the human's role for role-dependent scenarios by setting `GRAPHIA_SEED` to a magic value — module-level constants `SEED_MAFIA=3` / `SEED_LAW_ABIDING=0` / `SEED_MAFIA_HUMAN=3` — that happen to make the seven-card role-deck shuffle deal the desired role to the human. Until 2026-05-23, this was the only mechanism available. Spec 005 "Play-As-Role via Environment Variable" introduced `GRAPHIA_ROLE`, a developer / manual-testing launch-time appliance that directly pins the human's role.

**Foundational observation: the seed cannot deliver game reproducibility, only mechanical-RNG determinism.** Graphia's AI players (Sonnet for gameplay, Haiku for name generation) are LLM-driven; LLM output is inherently non-reproducible, and even pinning temperature to `0` only *lowers* the variance — it doesn't eliminate it. `GRAPHIA_SEED` therefore controls the stdlib `random.Random` shuffles (role deck, day-speech order, mafia-pointing target, vote tie-breaks) and nothing more. The magic-seed-for-role pattern dressed a narrow mechanical guarantee as if it were a general "this seed reproduces this scenario" promise; the dress-up was always a leaky abstraction, and acknowledging that explicitly is the foundation under everything else this ADR records.

Three proximate forces converged to surface the pattern as a problem worth fixing now:

1. `GRAPHIA_ROLE`'s arrival in spec 005 made a self-documenting alternative possible.
2. Drafting spec 005's §2.3 (originally "Reproducibility under a seed", since removed) exposed that the requirement had no real client — no test asserts which specific AI is dealt the second Mafia card; every role-dependent test role-filters. The investigation surfaced the opacity of magic-seed-for-role as the broader problem.
3. The accumulated copy-paste cost across ~10 test files narrowed the cleanup window with every new role-dependent test added.

---

## 2. Alternatives Considered

### Alternative 1: Keep the magic-seed-for-role pattern (status quo)

Continue using `SEED_MAFIA` / `SEED_LAW_ABIDING` constants and `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_*))` to control the human's role.

- **Pros:**
  - Already works — zero implementation cost.
  - Single-mechanism consistency: one env var (`GRAPHIA_SEED`) covers all RNG-related test determinism.
  - Side-effect coupling: the seed value also pins some downstream RNG behaviour incidentally.
- **Cons:**
  - Opaque intent — `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_MAFIA))` doesn't read as "the human is Mafia"; readers must consult the constant definition.
  - Fragile under `assign_roles` refactors — changing how the deck shuffle consumes the RNG silently breaks the seed-→role mapping.
  - Doesn't scale to additional roles — Phase 7 (Detective, Protector) would need a new magic seed per role.
  - Conflates role-pinning with mechanical-RNG pinning — same mechanism, two different intents, indistinguishable at the call site.

### Alternative 2 (Chosen): `GRAPHIA_ROLE` in tests

Tests set `monkeypatch.setenv("GRAPHIA_ROLE", "mafia")` (or `"law-abiding"`) directly. `GRAPHIA_SEED` is dropped from migrated tests entirely; it's set explicitly only when a test asserts mechanical-RNG behaviour, with an intent-naming comment.

- **Pros:**
  - Self-documenting — `monkeypatch.setenv("GRAPHIA_ROLE", "mafia")` reads as "the human is Mafia".
  - Decouples role-pinning from RNG-pinning; refactor-resilient as a consequence.
  - Scales to additional roles — Phase 7 (Detective, Protector) extends the enum without inventing new magic seeds.
- **Cons:**
  - Migration scope — ~33 call sites across 10 test files to rewrite.
  - Risk of silently dropping a load-bearing seed value: tests relying on the seed for downstream mechanical behaviour without saying so will lose that coupling; pytest red-bar is the only signal.
  - Migrated tests drop `GRAPHIA_SEED` entirely; tests that genuinely need a stable seed for RNG-pinning must reintroduce it with an explicit intent-naming comment.

---

## 3. Decision

Adopt **Alternative 2**. Migrate every existing test that uses `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constants for role-pinning to `monkeypatch.setenv("GRAPHIA_ROLE", "<role>")`. Drop `GRAPHIA_SEED` from migrated tests entirely unless a test asserts mechanical-RNG behaviour (speech order, tie-breaks, mafia-pointing target), in which case retain the setenv with a comment explaining what the specific seed value pins.

The single deliberate exception is `tests/test_slice4_role_reveal.py`, where the parametrisation on `(SEED_LAW_ABIDING=0, "Law-abiding Citizen", ...)` and `(SEED_MAFIA=3, "Mafia", ...)` IS the test — it's the regression-guard for the unset-path seed-→role mapping that spec 005 §2.3 "Default behaviour unchanged when the variable is unset" promises. Its constants stay; an inline comment explains why.

---

## 4. Decision Rationale

**Primary category: lowest operational risk going forward.** The migration is a one-time, finite cost (~33 call sites, 10 files); the magic-seed pattern's costs were recurring and growing per new test file. Each of opacity, fragility, scalability-blockage, and intent-conflation was paid every time a contributor wrote a new role-dependent test. `GRAPHIA_ROLE` breaks the trajectory: future role-dependent tests inherit a clear convention with no per-test magic-seed hunting and no exposure to internal `assign_roles` RNG-consumption changes.

Two facets reinforce this. First, with `GRAPHIA_ROLE` having landed in spec 005 as a developer launch-time appliance, tests using a parallel mechanism would have introduced a second axis where one suffices. Second, Graphia's eventual Phase 7 role roster (Detective, Protector) would have required per-role magic-seed curation under the old convention; under the new convention, adding a role to `GRAPHIA_ROLE`'s enum is the only change needed.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- One-time migration cost (~33 call sites across 10 files) accepted in exchange for self-documenting tests.
- Tests with genuine `GRAPHIA_SEED` needs must now justify the value with an intent-naming comment.
- `GRAPHIA_ROLE` composes only one human role; per-AI role pinning still requires a different mechanism if ever needed.

**Future implications:**

- Phase 7 roles (Detective, Protector) extend `GRAPHIA_ROLE`'s enum; tests gain role-pinning for them immediately, with no per-role seed hunt.
- `assign_roles` can be refactored freely without breaking role-dependent tests — the contract is "this role lands on the human", not "this seed produces this shuffle order".
- Per-AI role pinning (force AI X to be Mafia) needs a different mechanism if a future spec wants it; `GRAPHIA_ROLE` is scoped to the human's seat.

**Technical debt incurred:**

- No material debt — once Slice 3 of spec 005 ships, the test suite is in its target state.

---

## 6. References

- Architecture: `context/product/architecture.md` §6 "Determinism Posture & Testing Conventions" — captures the two principles this ADR concretely instantiates (LLM outputs accepted as variable; direct intent expression in tests over fragile mechanisms).
- Related specs: `context/spec/005-play-as-role/` — the spec that introduces `GRAPHIA_ROLE` and whose Slice 3 "Migrate magic-seed role-pinning tests to `GRAPHIA_ROLE`" executes this migration.
- Related code: `tests/conftest.py` — the `safe_llm` autouse fixture and the default `GRAPHIA_SEED` delenv that establishes "seed is unset unless a test opts in" as the existing convention.
