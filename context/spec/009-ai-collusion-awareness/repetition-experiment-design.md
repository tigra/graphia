# Experiment Design: AI Day-Dialogue Repetition

- **Status:** Run (N=10, 2026-06-10) — decision: adopt `antiparrot`. See §13.
- **Author:** Alexey Tigarev
- **Context:** Spec 009's collusion-awareness nudge + spec 008's wider context window appear to drive an AI Day-dialogue **repetition spiral** on the Nova Pro gameplay model. A first pass (`make eval-dialogue`, n=2 games/condition) gave a suggestive but **unreliable** ranking — a same-config replication swung 33% → 47% near-dup, i.e. the run-to-run noise was as large as the effects. This document specifies a rigorous replacement experiment.

---

## 1. Objective

Rank a fixed set of candidate fixes (and the two anchors — the pre-spec baseline and the current HEAD regression) by how much they reduce AI Day-speech repetition, with **enough statistical power that the ranking is robust to the LLM's non-determinism**, and without being fooled by confounds (game length, template echo, similarity threshold).

Concretely, decide: *which combination of the 009-line variant, 008-window size, and gameplay temperature best restores dialogue diversity while preserving the game's design intent (same-round visibility, light collusion flavor)?*

---

## 2. What the pilot taught us (problems this design must fix)

1. **Non-determinism dominates at small n.** The mechanical-RNG seed pins the *game structure* (role deal, speaking order, tie-breaks) but **not** the LLM dialogue. At n=2 the same config varied ±~15 pts. → need many more games per condition, and a **paired** design.
2. **Length confound.** Some conditions produced ~88 speeches/game vs ~43 (less-decisive games run longer; longer Day chat accumulates more echo). The near-dup *rate* is not fully length-invariant. → **cap speeches per game** to a fixed K.
3. **Template echo evades a naïve metric — and is the real failure mode.** The model reuses a sentence skeleton with the player name swapped ("I've been watching everyone, and it seems like *[name]*'s behavior…"). → add a **name-masked** similarity metric and a corpus-level **self-BLEU**.
4. **Single threshold is brittle.** difflib ratio ≥ 0.85 is one arbitrary cut. → report a **threshold sweep**.
5. **Confounded multi-factor conditions.** → clean, declarative **factor isolation**.

---

## 3. Factors (independent variables)

| Factor | Levels | Notes |
|---|---|---|
| **F1 — 009 line** | `collusion` (HEAD), `none`, `anti-parrot` | the `DAY_SPEAK_SYSTEM` nudge variant |
| **F2 — 008 window** | `30` (HEAD), `15`, `10` | `_CONTEXT_WINDOW` in `nodes/day.py` |
| **F3 — temperature** | `0.7` (HEAD), `1.0` | `get_large` temperature in `llm.py` |

Full factorial (3×3×2 = 18) is more than we need. Use a **screening set**: the two anchors, one-factor-at-a-time off HEAD, and three promising combinations.

### Conditions (the experiment's rows)

| id | F1 line | F2 window | F3 temp | role |
|----|---------|-----------|---------|------|
| `HEAD`      | collusion   | 30 | 0.7 | anchor (the regression) |
| `BASE`      | none        | 10 | 0.7 | anchor (pre-spec) |
| `noline`    | none        | 30 | 0.7 | OFAT: drop the 009 line |
| `antiparrot`| anti-parrot | 30 | 0.7 | OFAT: reworded 009 line |
| `win15`     | collusion   | 15 | 0.7 | OFAT: shrink window |
| `temp1`     | collusion   | 30 | 1.0 | OFAT: raise temperature |
| `noline+win15` | none     | 15 | 0.7 | combo |
| `noline+temp1` | none     | 30 | 1.0 | combo |
| `sink`      | none        | 15 | 1.0 | combo (all levers) |

(9 conditions. A **pilot** may run the 6 highest-value ids first; see §8.)

---

## 4. Metrics (dependent variables)

Computed **per game** (so we get a distribution, not a point), over the **first K AI Day speeches** of that game (length control, §6).

- **Primary: name-masked near-dup rate.** Replace every alive player name (and the speaker's own) with a single `<NAME>` token, normalize (lowercase, collapse whitespace, strip trailing punctuation), then fraction of speeches that have ≥1 neighbor with difflib ratio ≥ **0.85**. This is the metric the decision rule (§7) uses — it captures template echo, which is the actual failure.
- **Secondary, reported alongside:**
  - raw (un-masked) near-dup rate at θ ∈ {0.80, 0.85, 0.90} — threshold sweep.
  - exact-duplicate rate; distinct ratio (unique/total).
  - **self-BLEU** (mean sentence-BLEU of each speech vs the rest of that game) — standard corpus-diversity measure, complements difflib.
  - largest near-dup cluster size.
- **Covariate / guardrail (not a fix target): game decisiveness** — speeches-to-natural-end and executions/game. A "fix" that only works by making games drag is not a real win; we report this so the length tradeoff is explicit.

Pre-register the **primary metric + the primary comparison** (each fix vs `HEAD`) before running, to avoid metric/threshold shopping after the fact.

---

## 5. Sample size, pairing, power

- **Paired design.** A fixed seed set `S = {seed_base + i | i in 0..N-1}` is reused **identically across all conditions**. Game `i` therefore has the same role deal, speaking order, and Night trajectory in every condition — so a condition's effect is measured against matched structures, not luck of the deal. Residual LLM variance is averaged over N.
- **N per condition: start at 20.** Rationale: pilot per-game near-dup SD looked ≈15–20 pts; SEM = SD/√N, so N=20 → SEM ≈ 3.5–4.5 pts, enough to resolve the ~10–15 pt differences we care about. **Adaptive option:** keep adding games (in batches of 5) to a condition until its primary-metric 95% CI half-width < 5 pts or a cap (N=40) is hit; log where each condition stopped.
- Report **mean ± 95% CI** (bootstrap or t-based) for every metric, never a bare point estimate.

---

## 6. Length control

- Drive each game but **collect only the first K = 24 AI Day speeches** (≈4 rounds for a 6-alive table), then stop that game. Every game contributes exactly K to the denominator → rate is comparable across conditions and games.
- Games that end (a side wins) before K speeches are **discarded and resampled** with the next unused seed, so each condition still yields N games of exactly K speeches. Log the discard count per condition (itself a decisiveness signal).
- The scripted human (neutral, varied lines, excluded from the metric) is **held constant across all conditions**, so it cannot bias comparisons.

---

## 7. Analysis & decision rule (pre-registered)

- For each candidate fix vs `HEAD`: **Wilcoxon signed-rank test** on the paired per-game primary-metric values (non-parametric — near-dup is bounded and likely non-normal). Report the median paired difference + 95% CI and the p-value.
- **Multiple-comparison control:** Holm–Bonferroni across the (#fixes) comparisons to `HEAD`.
- **Adopt a fix** iff: (a) it lowers the primary metric vs `HEAD` with Holm-adjusted p < 0.05 and the CI excludes 0; **and** (b) it does not materially worsen decisiveness (§4 guardrail) — or the length tradeoff is explicitly accepted.
- Among qualifying fixes, **prefer the one that preserves the most design intent** (keep 008's same-round visibility and a light 009 flavor if a cheaper lever suffices) at the best metric/visibility tradeoff. Ties broken by simplicity (fewest knobs changed).
- Also report each condition vs `BASE` to see how close to the Nova floor (~14% in the pilot) each fix gets.

---

## 8. Procedure

1. **Harness upgrades** (to `src/graphia/tools/eval_dialogue.py`) — see §9.
2. **Orchestrator** (`scripts/repetition_experiment.py` or a make target) holds the condition table declaratively. For each condition it: applies the config edit (window const / `DAY_SPEAK_SYSTEM` variant / `get_large` temp), runs N games over the shared seed set with `--max-speeches K`, captures per-game metrics, then **`git checkout --`** restores the source. It writes incremental JSON per condition (so a long run survives interruption).
3. **Pilot first:** run ids `HEAD, BASE, noline, antiparrot, win15, temp1` at N=8 to sanity-check the harness, variance estimate, and discard rates. Then the **full run** at N=20 (adaptive to 40) across all 9 conditions.
4. **Report:** one table (mean ± CI per metric per condition), the paired-test results vs HEAD, the self-BLEU and threshold-sweep panels, and the decisiveness guardrail. Plus the raw per-game JSON.

---

## 9. Harness changes required

`eval_dialogue.py` (and a thin orchestrator) must gain:

- `--max-speeches K` — stop a game after K AI Day speeches; discard+resample games that end early.
- **Per-game** metric records (not just a pooled aggregate) → enables variance/CI and paired tests.
- **Name-masking** pass before similarity (mask all roster names + speaker).
- **Threshold sweep** (compute near-dup at {0.80, 0.85, 0.90}).
- **self-BLEU** metric (no heavy deps — a small n-gram BLEU is fine).
- Mean ± 95% CI reporting; full per-game JSON emit.
- Orchestrator: declarative condition table; applies/reverts the three factor edits via the existing git-checkout pattern; shared seed set; incremental per-condition JSON; final comparison table + Wilcoxon vs HEAD.

---

## 10. Cost & runtime

- Per game ≈ K speeches + Night/setup ≈ ~30 real Nova calls. Full run = 9 conditions × 20 games × ~30 ≈ **~5,400 calls** (plus discards) → tens of minutes, real token spend. Knobs to bound it: condition subset, N, K. Run in background; incremental JSON makes partial results usable.
- Pilot (6 conditions × 8 games) is ~1/5 the cost — run it first and re-estimate before committing to the full sweep.

---

## 11. Threats to validity

- **Endpoint/model drift:** run all conditions in **one session** so a Nova version change can't confound across conditions.
- **Lexical ≠ semantic diversity:** name-masking + self-BLEU mitigate template echo, but paraphrase that's lexically distinct yet semantically identical will read as "diverse." A semantic (embedding) metric is a future extension, intentionally out of scope here (cost + determinism).
- **Scripted-human artificiality:** held constant across conditions, so it biases the *absolute* numbers but not the *comparisons*.
- **Generalization:** results bind to Nova Pro at these settings; a model swap (or temperature ceiling differences) invalidates the ranking and the experiment must be re-run.
- **Multiplicity / garden-of-forking-paths:** mitigated by pre-registering the primary metric + comparison and Holm-correcting the rest.

---

## 12. Deliverables

- The upgraded `eval_dialogue.py` + orchestrator.
- A results report (condition table with CIs, paired tests vs HEAD, self-BLEU/threshold panels, decisiveness guardrail) committed under this spec dir.
- A one-line **decision** (which fix is adopted, per §7) feeding the actual `DAY_SPEAK_SYSTEM` / `_CONTEXT_WINDOW` / temperature change, with the report as its evidence.

---

## 13. Results (N=10, 2026-06-10)

Run: `make repetition-experiment ARGS="--games 10 --json context/spec/009-ai-collusion-awareness/repetition_experiment.json"`. Real Nova, 9 conditions × 10 paired games × 24 speeches. **Zero errors, zero early-ends** (all games hit the K cap, so pairing held across every condition). Primary metric = name-masked near-dup @0.85, mean [95% CI]; raw per-game data in `repetition_experiment.json`.

| condition (line / win / temp) | primary [95% CI] | exact-dup | self-BLEU | Δ vs HEAD | Holm p |
|---|---|---|---|---|---|
| HEAD (collusion/30/0.7) | **0.57** [.49,.65] | .23 | .78 | — | — |
| noline (none/30/0.7) | 0.44 [.35,.53] | .12 | .68 | −0.13 | .034 |
| win15 (collusion/15/0.7) | 0.33 [.25,.43] | .11 | .64 | −0.24 | .002 |
| temp1 (collusion/30/1.0) | 0.23 [.15,.32] | .06 | .55 | −0.34 | <.001 |
| BASE (none/10/0.7) | **0.20** [.10,.28] | .06 | .56 | −0.38 | <.001 |
| noline+temp1 (none/30/1.0) | 0.18 [.09,.26] | .05 | .56 | −0.40 | <.001 |
| **antiparrot (anti/30/0.7)** | **0.15** [.09,.20] | .03 | .53 | **−0.42** | <.001 |
| sink (none/15/1.0) | 0.10 [.05,.15] | .02 | .46 | −0.47 | <.001 |

**Decision — adopt `antiparrot`** (per the §7 rule: significant vs HEAD *and* preserves the most design intent). It reaches **0.15 — below the pre-spec BASE (0.20)** — while keeping 008's full window (30) and temp (0.7); only the 009 line text changes. `sink` (0.10) is marginally lower but sacrifices the window, the collusion flavor, and raises temperature for a 0.05 gain.

**Headline finding — the rigor mattered.** The n=2 pilot ranked `antiparrot` a *failure* (51% near-dup, worse than HEAD). At N=10 with the masked, length-capped, paired metric it is the **best design-preserving fix**. The pilot was wrong because of run-to-run noise + a length confound (its anti-parrot games happened to run ~2× long) + names hiding template echo — exactly the three failure modes this design was built to remove. Secondary: **removing the collusion line alone (`noline`, 0.44) barely helps (Δ−0.13)** — you must *instruct against* parroting, not merely stop priming it.

**Reproduce:** `make repetition-experiment ARGS="--games 10"` (subset via `--conditions HEAD,antiparrot`; quick single-config check via `make eval-dialogue`).
