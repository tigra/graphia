<!--
Technical considerations for spec 031 — Distinct AI Personas Across the Roster.
HOW the option-(b) threading + the standing persona-distinctiveness metric are built.
-->

# Technical Specification: Distinct AI Personas Across the Roster

- **Functional Specification:** `./functional-spec.md`
- **Status:** Completed *(verified 2026-06-23 — effort-not-results measurement recorded in the 2026-06-22 ledger runs; [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md))*
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Two independent changes, both small and additive:

- **(A) Roster-aware persona generation (option b).** Today `generate_personas` (`src/graphia/nodes/setup.py`) loops the AI players and calls `_generate_one_persona(player)` in isolation — each prompt carries only that player's name. The change accumulates the **already-created personas of this game** and feeds their **table-facing** descriptions into each subsequent generation call as a "make this character clearly different from these" instruction, so the creative model differentiates instead of reaching for the same modal townsperson. A pure-prompt change to the existing setup node — no new node, no model, no `Persona` schema change.

- **(B) A standing persona-distinctiveness metric in the eval ledger.** A new **pure** scorer reuses the spec-009 lexical near-dup machinery (`_spec009_mask_names` / `_spec009_normalize` + `difflib` ratio ≥ `_NEAR_DUP_THRESHOLD`, already imported in `blunder_eval.py`) to measure how near-duplicate a game's AI personas are to **each other**, recorded with every `make blunder-eval` run in the `metrics` block (same `rate`/`count`/`denominator` + Wilson-CI shape as the other metrics) and surfaced in `make view-ledger` via `METRIC_ORDER`. This is the **measurement vehicle** for the effort-not-results acceptance (CR 005), now made a standing tracked number.

Lexical/cheap-deterministic by design, consistent with the existing `repetition` metric and the determinism posture (architecture §6, *Determinism Posture & Testing Conventions*). The behavioural effect (a more distinct roster) is non-deterministic and measured out-of-suite against the pre-change baseline.

Affected files: `src/graphia/nodes/setup.py`, `src/graphia/prompts.py`, `src/graphia/tools/blunder_eval.py`, `src/graphia/eval_ledger.py`, plus tests. **Unchanged:** the `Persona` schema (`llm.py`), the graph topology, game rules/turn order, the human, and the end-of-game reveal.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — Roster-aware persona generation (`nodes/setup.py`, `prompts.py`)

- **`generate_personas`** — as it loops AI players (insertion order), accumulate the personas created **so far this game** and pass them to each call: `_generate_one_persona(player, prior_personas)`. The human is still skipped; the whole `players` map is still returned (plain-replace channel).
- **`_generate_one_persona(player, prior_personas)`** — builds the prompt as today (`PERSONA_SYSTEM` + the role-tailored user template), and **when `prior_personas` is non-empty, appends one additional `HumanMessage`**: a "differentiate from these already-created characters" block. Appending a separate message (rather than adding a `{...}` slot to the two persona templates) is the deliberate choice — it keeps `PERSONA_CITIZEN_USER_TEMPLATE` / `PERSONA_MAFIA_USER_TEMPLATE` and **every existing `.format()` call site untouched** (avoids the spec-019-style `KeyError` blast radius). The corrective **retry path must include the same block**, so a retried persona still differentiates. The first AI player gets no block (nothing to differ from yet); the deterministic `_fallback_persona` is unchanged (best-effort, last resort).
- **What is threaded — table-facing fields only.** Each prior persona contributes its **`personality` + `manner` + `public_persona`** (the face shown at the table). A Mafioso's **`true_self` is never threaded** into another character's generation — the differentiation target is the public character (functional-spec §2.2), and excluding the secret keeps hidden content out of every other generation by construction (reinforces the §2.4 / spec-016 allegiance-hiding invariant).
- **Prompt text in `prompts.py`** — add a small `PERSONA_DISTINCT_FROM_TEMPLATE` (e.g. *"You are creating one of several characters for the same game. Make this one clearly different in temperament and voice from the characters already created:\n{others}\nAvoid reusing their personality, manner, or backstory."*), formatted in `setup.py` with the prior personas rendered one-per-line. Keeps prompt prose in `prompts.py` per house convention.
- **Verifiability (functional-spec §2.1 AC1).** The generation prompt is **not** part of the public game stream/transcript, so "verifiable in recorded data" is realised as a **prompt-capture test at the LLM boundary** (the fake `get_large` captures the messages; assert the distinct-from block lists the earlier characters for the 2nd+ player and is absent for the 1st) — the same seam spec 019 used for the standings injection.

### Component B — Persona-distinctiveness metric (`tools/blunder_eval.py`, `eval_ledger.py`)

- **New pure scorer** `score_persona_near_dup(players: dict[str, PlayerState]) -> dict` in `blunder_eval.py`:
  - Take the **AI** players (skip the human); for each, build its table-facing persona text `personality + " " + manner + " " + public_persona`, then `_spec009_mask_names(text, ai_names)` + `_spec009_normalize(...)` (same masking/normalising as `repetition`, so a self-name token can't inflate similarity).
  - Over all **unordered pairs** of AI personas, count pairs whose `difflib.SequenceMatcher` ratio ≥ `_NEAR_DUP_THRESHOLD` (0.85). Returns the `_facets`-shaped `{rate, count, denominator}` where **`denominator` = number of pairs (`C(n,2)`)** and **`count` = near-duplicate pairs**; `denominator == 0` (a roster with < 2 AI personas) → `rate = None`, exactly like the action metrics.
- **Direction / naming.** The recorded rate is a **near-duplication** rate (higher = personas more alike = *less* distinct), matching the existing badness-rate family (`repetition`, `self_vote.*`). Ledger key proposed: **`persona_near_dup`**; read "distinctiveness" as `1 − rate`. (Naming/direction is the one thing worth confirming — see §3.)
- **Aggregation** — follows the **action-metric pattern** (not the pooled-lines `repetition` pattern): score per game over `cap.players`, sum `count` and `denominator` across games into a batch total in `run_eval`, then `_facets(total_count, total_denominator)` and the existing `_attach_ci(result.metrics)` adds the Wilson band. Personas are available per game on `_GameCapture.players` (final roster, `persona` populated).
- **Ledger surface** — append one tuple to `METRIC_ORDER` in `eval_ledger.py` (e.g. `("persona_near_dup", "persona dup")`); `render_detail` and the `view-ledger` table then pick it up automatically. The metric lands in the record's existing `metrics:` block alongside `repetition` etc.
- **`metrics_version` — NOT bumped.** Per the documented rule, the version guards comparability of existing metrics' *rules/denominators*; a brand-new, orthogonal metric is **additive** (old records simply lack the key, shared metrics stay comparable) — the same treatment `outcomes` / `vote_activity` got. Leave `METRICS_VERSION` at its current value.

### What does NOT change

- The `Persona` Pydantic schema and `PlayerPersona` dataclass — threading is input-side only.
- Game rules, turn order, win conditions; the human (no persona); fresh-per-game + fixed-in-game; the end-of-game reveal (functional-spec §2.4).
- The byte-equal dual-mode smoke — persona generation is faked there (canned output regardless of the extra prompt message), and the new metric is pure and not part of the transcript.

---

## 3. Impact and Risk Analysis

- **Prompt blast radius** — mitigated by appending a separate `HumanMessage` instead of adding a template slot, so no existing `.format()` site breaks. (The alternative — a `{distinct_from}` slot in both templates — would force fixing every direct-format test site, as in spec 019.)
- **Prompt growth** — the N-th persona call carries N−1 short table-facing descriptions; bounded by the table cap (≤ ~7 players) and one-time at setup, so negligible.
- **Mafioso privacy** — only table-facing fields are threaded; `true_self` never enters another generation prompt, and the metric is computed over table-facing text only. No new allegiance-leak surface.
- **Metric direction/naming** — a "distinctiveness" goal expressed as a near-duplication *badness* rate (higher = worse) is consistent with the metric family but can read backwards; the `persona_near_dup` key + a "lower is more distinct" note is the mitigation. **Confirm the key name/direction.**
- **`metrics_version`** — deliberately not bumped (additive metric); flagged so a reviewer doesn't "fix" it.
- **Determinism** — the scorer is pure, lexical, no RNG (architecture §6); the dual-mode byte-equal smoke is unaffected.
- **Effort-not-results honesty** — the metric is over *persona text*, the direct test of option (b); whether distinct personas translate to distinct *speech* also depends on the separate persona-salience gap (out of scope), so a flat speech read would not refute this spec.

---

## 4. Testing Strategy

Structural/pure-unit tests in the all-mocked suite (architecture §6); never assert verbatim LLM prose. The behavioural effect is measured **out-of-suite**.

- **Pure scorer** (`score_persona_near_dup`) over hand-built `players` maps: a roster of clearly-different personas → `count == 0` (rate 0.0); near-identical personas → high `count`/rate; a single AI persona (or none) → `denominator == 0`, `rate is None`; the human is excluded; name-masking confirmed (personas differing only by embedded name are still counted near-dup). No model, no RNG.
- **Threading prompt-capture** (mirror spec 019's injection-seam test with the persona fake): drive `generate_personas` with a capturing fake `get_large`; assert the **2nd+** player's generation messages contain the distinct-from block listing the earlier characters' table-facing text, the **1st** player's do not, and that **no `true_self`** text appears in any generation prompt. The corrective-retry path also carries the block.
- **Existing persona tests stay green** — spec 016's generation/fallback/reveal tests; the `_generate_one_persona` retry-then-fallback contract is preserved (a flaky/missing model still yields a valid fallback and never blocks setup).
- **Ledger integration** — a mocked `run_eval` writes a record whose `metrics` block carries `persona_near_dup` (shape: `rate`/`count`/`denominator`/`ci_low`/`ci_high`); `METRIC_ORDER` surfaces it in `render_detail`. `metrics_version` unchanged. Reuse the suite-wide ledger/transcript redirect so the real ledger is untouched.
- **Verification** — `uv run pytest -q` green (incl. byte-equal `test_dual_mode_smoke.py`).
- **Effort-not-results measurement** (out-of-suite, CR 005): run `make blunder-eval ARGS="--provider ollama --games N"` before vs after, compare the `persona_near_dup` metric against the committed baseline, and record the hypothesis confirmed or refuted (both valid outcomes).
