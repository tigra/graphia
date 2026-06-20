# ADR 011: Default-on feature flags make gameplay-influencing changes ablatable

- **ADR Number:** 011
- **Title:** Default-on feature flags make gameplay-influencing changes ablatable
- **Status:** Accepted
- **Date:** 2026-06-20
- **Authors:** Alexey Tigarev

---

## 1. Context

Graphia treats AI quality as a *measured* property: acceptance for non-deterministic AI-behaviour changes is effort-not-results ([CR 005](../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)), and results are tracked in the repo-committed blunder-eval ledger ("baby MLOps"). To attribute a gameplay change's effect we need to measure play **with and without exactly that change**.

Two forces sharpen this. First, we want **measurable per-feature impact**. Second, we expect to **experiment with different gameplay models**, and different models react differently to the same feature — so a tweak made to coax an *older* model into playing well must not be permanently baked on for a *newer* model just historically. We want to evaluate each model **with or without** a given feature, and to test features both **independently and in combination**.

Spec 018 (Day-Round Moderator Recap) introduced an ad-hoc off-switch (`GRAPHIA_DAY_ROUND_RECAP`) "for a future ablation study," but specs 019 (Recap-Aware AI Reasoning) and 020 (Game-Time in the Recap) — also gameplay-influencing — shipped without flags. The practice is real but inconsistent and undocumented; this ADR standardizes it.

---

## 2. Alternatives Considered

### Alternative 1: Baseline-as-ablation only (compare git commits)

Ablate a feature by comparing a feature-on commit against the prior baseline commit recorded in the eval ledger; no per-feature flag.

- **Pros:** Zero per-feature code/config overhead.
- **Cons:** Confounded comparison — an older baseline commit differs in more than the one feature, so it isn't clean attribution.

### Alternative 2: Per-feature default-on environment flag (CHOSEN)

Each gameplay-influencing feature ships behind its own default-on env flag (e.g. `GRAPHIA_<FEATURE>`); ablate by toggling it off; combine features by setting multiple flags.

- **Pros:**
  - Single-build A/B isolation — ablate one feature in a single build, everything else identical.
  - Model portability — each model can be evaluated with/without a feature, so a tweak made for an old model isn't permanently forced on a new one.
  - Features can be tested both independently and in combination.
- **Cons:**
  - Flag sprawl — `GRAPHIA_*` flags accumulate and need periodic pruning.
  - Flag drift — flags can begin to signify different things over time / defaults drift from intent.

### Alternative 3: General experiment-config framework

A structured variant system: a config/registry of named experiment "arms" (feature combinations), threaded through the game, with the eval harness sweeping a variant matrix automatically.

- **Pros:**
  - Centralized variant management.
  - Automatic variant-matrix sweeps in the eval harness.
  - Scales to many simultaneous experiments.
- **Cons:**
  - High build cost for little near-term value that the env flags already deliver.
  - Against the design-driven-by-realistic-needs principle (capability for its own sake).
  - Too much complexity / a maintenance burden — machinery to maintain even for features we're sure aren't needed; we'd rather build incrementally.

---

## 3. Decision

Adopt **Alternative 2**: every gameplay-influencing change ships behind its own **default-on** environment flag (`GRAPHIA_<FEATURE>`, mirroring spec 018's `GRAPHIA_DAY_ROUND_RECAP`), so it can be toggled off to reproduce prior behaviour. **Display-only / non-gameplay changes are exempt** (e.g. the spec-020 in-world clock, spec-021 transcript labels — they don't change how the game plays). Spec 019 will be retrofitted to comply.

---

## 4. Decision Rationale

Best fit for the realistic needs. It directly serves the two drivers — measurable per-feature impact, and cross-model experimentation (evaluating each model with/without a feature, testing features independently and combined) — at minimal cost: one config field + threading + a flag-off parity test per feature. Alternative 1's sole advantage (zero overhead) is outweighed by its **confounded** multi-feature commit comparison. Alternative 3 would deliver centralized sweeps but at a build/maintenance cost unjustified for a single-player reference project and contrary to design-driven-by-realistic-needs; the incremental env-flag approach can be revisited toward a framework only if flag sprawl ever demands it.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- Per-feature flag overhead — a config field, threading, and a flag-off parity test for each gameplay feature.
- An accumulating set of `GRAPHIA_*` flags to prune over time.
- Probable feature drift — flags can begin to signify different things over time as the system evolves.

**Future implications:**

- Every future gameplay-influencing feature ships behind a default-on flag plus a flag-off parity test.
- The blunder-eval can compare flag-on vs flag-off in one build, and test feature combinations by setting multiple flags.
- Display-only / non-gameplay changes are exempt — only what changes how the game plays gets a flag.
- If flag combinations ever become unwieldy, revisit toward the variant-framework (Alternative 3).

**Technical debt:**

- Flag-drift risk — defaults must stay correct over time; mitigated by the per-feature flag-off parity test.

---

## 6. References

- Architecture: `context/product/architecture.md` — §6 Determinism Posture & Testing Conventions (one-line pointer to this convention to be added)
- Related ADRs: _none_
- Related CRs: `context/change-requests/005-ai-behaviour-acceptance-effort-not-results.md`
- Related specs: `context/spec/018-day-round-moderator-recap/` (the precedent flag), `context/spec/019-recap-aware-ai-reasoning/` (to be retrofitted)
- External docs: _none_
