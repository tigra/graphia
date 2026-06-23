<!--
Technical considerations for spec 032 — Continuous Persona-Similarity Metrics (Average + Peak).
HOW the average/peak lexical metrics + the value-type viewer rendering + the backfill are built.
-->

# Technical Specification: Continuous Persona-Similarity Metrics (Average + Peak)

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> **Shared surface with spec 033 (semantic):** 032 and 033 both add a persona-similarity metric, a viewer column, and a backfill. 032 is **lexical/deterministic** and introduces the reusable pieces (the *value-type* metric shape, the value-type viewer rendering, the transcript backfill harness); 033 is **semantic/model-dependent** and reuses them. **Implement 032 first**, then 033 builds on it. Both are additive — no `METRICS_VERSION` bump, no ablation flag (measurement-only; ADR-011 / architecture §6 exempt).

---

## 1. High-Level Technical Approach

Spec 031 added `persona_near_dup` — a pure scorer (`score_persona_near_dup` in `tools/blunder_eval.py`) that counts AI-persona pairs with `difflib` ratio ≥ 0.85, recorded as a `_facets` `{rate, count, denominator}` block. This change reuses the **same masked-text + pairwise machinery** to additionally produce two **continuous** numbers per run — the **average** pairwise similarity and the **peak** (max) pairwise similarity — recorded as a new **value-type** metric shape `{mean|peak, denominator}` (a mean is not a proportion, so it gets no rate/count and no Wilson CI). The ledger viewer and detail renderer gain a small **value-type branch** (they currently assume `rate`/`count`/`denominator`). Past transcript-preserved runs are backfilled with a one-off harness that reuses the live scorer over committed transcripts (the approach already validated by reproducing the spec-031 numbers exactly).

Affected files: `src/graphia/tools/blunder_eval.py` (scorer + `run_eval` aggregation), `src/graphia/eval_ledger.py` (`METRIC_ORDER` + value-type rendering in `_metric_cell` / `render_detail`), tests, and a backfill harness. **Unchanged:** `score_persona_near_dup`'s contract, game rules, the `Persona` schema, `METRICS_VERSION`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — the scorer (`tools/blunder_eval.py`)

- **New pure fn `score_persona_sim_stats(players) -> {"sim_sum": float, "sim_max": float, "denominator": int}`.** Mirrors `score_persona_near_dup` EXACTLY for the inputs: AI players only (skip human / `persona is None`), table-facing text `personality + " " + manner + " " + public_persona` (never `true_self`), `_spec009_mask_names`(AI names) + `_spec009_normalize`, unordered `combinations` pairs. Over the pairs it returns `sim_sum` = Σ of all `difflib.SequenceMatcher(...).ratio()`, `sim_max` = the max ratio, `denominator` = `C(n,2)`. `<2` AI personas → `{0.0, 0.0, 0}`.
- To avoid two passes over the same pairs, the shared "build masked AI-persona texts" step **may** be factored into a small helper that both `score_persona_near_dup` and the new fn call — but `score_persona_near_dup`'s signature and return value must not change (its spec-031 tests stand). Recomputing the pairs in a second fn is also acceptable (personas are ≤ ~7, pairs ≤ 21).

### Component B — aggregation (`run_eval`)

- Alongside the existing `persona_total {count, denominator}` accumulator, track `sim_sum_total` (running sum), `sim_max_run` (running **max** across games — the run peak is the max over all pairs, i.e. the max of per-game maxes), and reuse the same `denominator` total.
- After the game loop, when `denominator_total > 0`, record **two value-type metrics**:
  - `result.metrics["persona_mean_sim"] = {"mean": sim_sum_total / denominator_total, "denominator": denominator_total}`
  - `result.metrics["persona_peak_sim"] = {"peak": sim_max_run, "denominator": denominator_total}`
- These carry **no `rate`/`count`** → ensure `_attach_ci` does **not** attach a Wilson band (it iterates metrics expecting a proportion; make it skip any facet lacking `count`, leaving the existing rate-metrics untouched).

### Component C — ledger record + viewer (`eval_ledger.py`)

| Concern | Change |
| --- | --- |
| `METRIC_ORDER` | append `("persona_mean_sim", "persona sim~")` and `("persona_peak_sim", "persona max~")` after `persona_near_dup` |
| `_metric_cell` (list) | add a **value-type branch**: when facets has `"mean"` (or `"peak"`) and no `"rate"`, render `~{value:.2f} (n={denominator})`; else the existing `rate [ci] count/denom` path; absent metric → "" (blank) as today |
| `render_detail` | same value-type branch on its per-`METRIC_ORDER` line |
| `_metric_facets` | already resolves a flat `metrics[key]`; no change |

The `~` prefix signals "a similarity value, not a near-dup rate", and `(n=…)` carries the pair count for sample-size context.

### Component D — backfill harness (one-off, additive)

- Reuse the **validated transcript parser** (parses each game's `<setup>` `<player>` blocks — tagged, indented, and flush-left formats — into reconstructed persona objects, skipping the personaless human) + the **live** `score_persona_sim_stats`, aggregated per run exactly as `run_eval` does, to compute `persona_mean_sim` / `persona_peak_sim` for each transcript-preserved ledger record that lacks them.
- Edit them in as a **purely additive** text insertion into each target record's `metrics:` block (the same surgical, formatting-preserving approach already used for the `persona_near_dup` backfill — splits on `---` doc boundaries, inserts at the metrics-block end, never rewrites other records, validated to parse + diff additive-only).
- **Faithfulness gate:** before trusting the backfill, recompute a run that recorded these live and confirm the transcript-derived values match (the spec-031 backfill proved this approach reproduces live numbers exactly).

---

## 3. Impact and Risk Analysis

- **System dependencies:** none new — pure-Python, lexical, no model, no network. Same determinism posture as `repetition` / `persona_near_dup` (architecture §6: cheap-deterministic lexical metric).
- **Risk — viewer regression.** `_metric_cell` / `render_detail` are spec-012/029 tested surfaces. *Mitigation:* the value-type branch is strictly additive (new `if "mean"/"peak"` arm); the existing rate path and its tests are untouched; add explicit value-type render tests.
- **Risk — `_attach_ci` mis-attaching CI to a mean.** *Mitigation:* gate CI attachment on the presence of `count`; covered by a test asserting `persona_mean_sim` has no `ci_low`/`ci_high`.
- **Risk — backfill corrupting curated ledger data.** *Mitigation:* additive-only text surgery + YAML-parse + additive-diff validation + the faithfulness gate; never touches non-target records (the proven spec-031 procedure).
- **`METRICS_VERSION`** deliberately **not** bumped (additive/orthogonal metric — the `outcomes`/`vote_activity`/`persona_near_dup` precedent); flagged so a reviewer doesn't "fix" it.

---

## 4. Testing Strategy

All-mocked, no model, no RNG (architecture §6); never assert verbatim LLM prose.

- **Pure scorer** over hand-built `players`: distinct roster → low `sim_sum`, low `sim_max`; a collapsed pair → `sim_max == 1.0` while the mean stays low (the case the peak exists to catch); `<2` AI personas → `denominator 0`; human excluded; `true_self` never participates; name-masking confirmed.
- **Aggregation:** mean = Σ ratios / total pairs across games; peak = max across games; `<2`-everywhere → metrics omitted.
- **Record + viewer:** a mocked `run_eval` writes `persona_mean_sim` / `persona_peak_sim` as `{mean|peak, denominator}`; `_metric_cell` and `render_detail` render the `~value (n=…)` form; absent → blank; `persona_mean_sim` has no Wilson band; `METRICS_VERSION` unchanged. Reuse the suite-wide ledger/transcript redirect.
- **Backfill harness:** faithfulness test (transcript-derived == live for a known run); additive-only (record count unchanged, YAML parses, only the two keys added).
- **Regression:** full `uv run pytest -q` green incl. spec-031 persona tests, spec-012/029 viewer tests, and `tests/test_dual_mode_smoke.py` (byte-equal; the persona metrics are outside its public-log scope).
