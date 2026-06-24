<!--
Technical considerations for spec 034 — Diversified Persona Generation (Randomized + Regenerate-on-Collision).
HOW the randomization seams + the lexical collision/regeneration loop are built, behind a default-on flag.
-->

# Technical Specification: Diversified Persona Generation (Randomized + Regenerate-on-Collision)

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> Builds on **spec 031** (`generate_personas` / `_generate_one_persona` / `_distinct_from_message` / `PERSONA_DISTINCT_FROM_TEMPLATE` / `_fallback_persona` in `nodes/setup.py` + `prompts.py`) and reuses the **spec-009 lexical machinery** (`_spec009_mask_names` / `_spec009_normalize` + `difflib`) already used by `score_persona_near_dup`. **Gameplay-influencing → behind a default-on flag (ADR 011).** No new dependency (lexical, free, local).

---

## 1. High-Level Technical Approach

Three additions to the persona-generation path in `nodes/setup.py` (+ `prompts.py`, `config.py`, `llm.py`, and the two graph builders), all gated by one default-on flag:

1. **Randomize the creation** — before building each `_generate_one_persona` prompt: (a) **shuffle** the already-created personas fed into the distinct-from block (currently insertion order), (b) **draw a target temperament** at random (without replacement within the game) from a broad archetype pool and inject it as a "lean toward this" hint, (c) generate at a **higher temperature** than gameplay default.
2. **Detect collisions lexically** — after a persona is produced, compute its `difflib` similarity (reusing the spec-009 mask/normalise) against each already-**accepted** persona's table-facing text; max ≥ the configurable bar = a collision.
3. **Regenerate on collision** — re-run `_generate_one_persona` (re-shuffled, a fresh archetype) up to an attempt cap; keep the **least-similar** attempt if the cap is hit; `_fallback_persona` is the final last resort. Setup never blocks.

All three are seeded via the **module-global `random`** (architecture §6 — seedable for evals, non-reproducible otherwise), so seeded runs (incl. the dual-mode byte-equal smoke) stay reproducible.

Affected files: `nodes/setup.py`, `prompts.py`, `config.py`, `llm.py`, `graph.py` + `runtime/graph_builder.py` (thread the flag, both builders — anti-drift), plus tests. **Unchanged:** the `Persona`/`PlayerPersona` schema, game rules, the end-of-game reveal, `METRICS_VERSION`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — randomization seams (`nodes/setup.py`, `prompts.py`, `llm.py`)

- **Shuffle prior personas:** a new seam `_shuffle_personas(prior, *, enabled)` over the module-global `random` (mirroring `_shuffle_order` / `_shuffle_night_roster` — seedable, monkeypatchable; **when disabled returns `list(prior)` BEFORE any `random.*` call** so flag-off preserves the seeded trajectory byte-for-byte). Applied to the list `_distinct_from_message` renders.
- **Archetype pool + draw:** a new constant `PERSONA_ARCHETYPES` in `prompts.py` — a broad list of temperaments deliberately wider than the model's default (e.g. *vigilant, brash, dry/sardonic, warm, anxious, eccentric, gruff, earnest, aloof, …*). In `generate_personas`, draw one per AI player **without replacement within the game** (`random.sample` / shuffle-then-pop over the module-global RNG) so each player targets a distinct temperament; inject via a small `PERSONA_ARCHETYPE_HINT_TEMPLATE` ("Lean toward a **{archetype}** temperament — distinct from the others.") **appended as a separate `HumanMessage`** (NOT a `{...}` slot — same anti-`KeyError` discipline as spec 031). The hint applies even to the **first** player (no prior to differ from, but still steered to a random temperament).
- **Higher temperature:** persona generation (spec 031 uses `get_large()`) runs at a higher temperature than gameplay. Add a temperature override to the model factory (e.g. `get_large(temperature=...)` building a fresh instance when overridden, or a dedicated `get_persona_model()`); default persona temp a config value (~1.0 vs the gameplay ~0.7). **Verify at implementation** that `ChatBedrockConverse` and the Ollama `ChatAnthropic` path both accept a per-instance temperature (they do via constructor — confirm against the installed SDK).

### Component B — lexical collision check + regeneration loop (`nodes/setup.py`)

- **Collision helper** `_persona_collision(candidate, accepted) -> float`: builds the candidate's and each accepted persona's table-facing text (`personality + " " + manner + " " + public_persona`, never `true_self`), `_spec009_mask_names`(AI names) + `_spec009_normalize`, and returns the **max** `difflib.SequenceMatcher` ratio against the accepted set. Pure, deterministic, no model — reuses the exact spec-009 helpers behind `score_persona_near_dup` so "collision" and the recorded `persona_lex_*` metric speak the same language.
- **Loop** in `generate_personas`: for each AI player, generate; if `_persona_collision(...) >= threshold`, regenerate (fresh shuffle + fresh archetype) up to `persona_regen_attempts`; track the lowest-collision attempt seen and, if the cap is exhausted, **accept the least-similar attempt** (only falling to `_fallback_persona` if generation itself failed, per the existing spec-031 contract). The accepted persona is then added to the `accepted`/`prior` list the next player sees.

### Component C — config + flag wiring (`config.py`, `graph.py`, `runtime/graph_builder.py`)

| Setting | Env | Default | Purpose |
| --- | --- | --- | --- |
| `persona_diversity_enabled` | `GRAPHIA_PERSONA_DIVERSITY` (`_env_flag`) | `True` | master flag (ADR 011) — gates A + B; off ⇒ spec-031 behavior (insertion order, no archetype hint, gameplay temp, no regen) |
| `persona_collision_threshold` | `GRAPHIA_PERSONA_COLLISION_THRESHOLD` | **~0.6** | the lexical bar (see below) |
| `persona_regen_attempts` | `GRAPHIA_PERSONA_REGEN_ATTEMPTS` | **2** | retries per colliding persona before keeping the least-similar |
| `persona_temperature` | `GRAPHIA_PERSONA_TEMPERATURE` | **~1.0** | higher-than-gameplay creative latitude |

Threaded via `_assemble_graph` partials into the setup node in **both** `build_graph` and `build_runtime_graph`.

**The default threshold (~0.6) — calibration.** From the spec-032/033 data: two *genuinely-different* personas sit at ~0.3 lexical (≈0.36 semantic), the recurring same-archetype twins ("honest, slow-talking librarian", identical personality+manner, different backstory) at ~0.5–0.65 lexical (≈0.84 semantic), and verbatim copies at 1.0. A bar of **~0.6** therefore reaches into the same-archetype band (per the functional spec's aggressive default) while staying above genuinely-different pairs. It is **fuzzier than the semantic measure** at this level (some genuinely-different-but-similar-format pairs may trip it) — acceptable, since a false-positive just yields another valid regenerated persona. Confirm the exact value against a quick read of the committed transcripts' pair distribution at implementation.

---

## 3. Impact and Risk Analysis

- **Mode-seeking re-collision (the core risk).** A blind re-roll re-hits "honest librarian." *Mitigation:* regeneration re-applies the randomization — a **fresh random archetype** (the strongest lever — steers to a different region), a fresh shuffle, and high temperature — so a retry diverges; the **attempt cap + least-similar fallback** guarantees termination even on a stubborn local model.
- **Lexical fuzziness at an aggressive bar.** ~0.6 can flag not-truly-same pairs. *Mitigation:* harmless (regen yields another valid persona); the bar is configurable; calibrate the default against real transcripts.
- **Determinism / dual-mode smoke (architecture §6).** The shuffle + archetype-draw consume the module-global RNG; seeded once (the dual-mode smoke's `random.seed(0)`), **both modes draw the identical sequence**, and the faked persona output is identical in both modes → the collision decision + any regeneration are deterministic and identical across modes → **byte-equality preserved** (the spec-030 pattern; flag-off path takes no RNG draw). Re-verify `test_dual_mode_smoke` stays green; if seed 0 happens to drive a divergent regen, document a new seed — do **not** special-case the flag.
- **Cost / setup latency.** Regeneration adds persona-gen calls (capped at `attempts`). Ollama is free but slower per call; bounded by the cap × roster size, and persona gen is a one-time setup cost (small vs a full game). Bedrock cost is negligible (persona gen is a handful of calls).
- **`safe_llm`.** The regen loop issues more `get_large` calls; the persona fake must supply enough outputs (replay-last-when-drained, matching the existing per-schema fakes) so a flag-on full-setup test never reaches real Bedrock.
- **`METRICS_VERSION` / ablation.** No metric change. Gameplay-influencing → default-on flag with a flag-off parity test (ADR 011).

---

## 4. Testing Strategy

All-mocked (architecture §6); never assert verbatim LLM prose.

- **Pure collision helper** `_persona_collision`: identical table-facing text → 1.0; same-archetype (identical personality/manner, different backstory) → above the default bar; genuinely-different → below it; `true_self` never participates; name-masking applied.
- **Randomization (prompt-capture, mirror spec 031/019):** with a capturing fake `get_large`, the prior personas appear in a **shuffled** order (monkeypatch the shuffle seam → differs from insertion order), each player's prompt carries a **random archetype hint**, the archetypes drawn are **distinct within a game** (no-replacement), and the persona model is built at the **higher temperature**.
- **Regeneration:** a fake that returns a colliding persona first then a distinct one → the collided persona is **replaced**, the distinct one kept; cap-exhaustion (fake always collides) → the **least-similar** attempt is kept and setup completes; generation failure still falls to `_fallback_persona`.
- **Flag-off parity:** `GRAPHIA_PERSONA_DIVERSITY=0` → spec-031 behavior exactly (insertion order, no archetype hint, gameplay temperature, no regeneration); no RNG draw on the disabled shuffle.
- **Determinism + anti-drift:** seeded run reproduces the same archetypes/shuffle; `build_runtime_graph` threads the flag (anti-drift); **`test_dual_mode_smoke` stays byte-equal**; full `uv run pytest -q` green.
- **Out-of-suite (effort-not-results, CR 005):** `make blunder-eval` before/after with the flag, comparing `persona_lex_mean`/`persona_lex_peak` and `persona_sem_mean`/`persona_sem_peak` against the recorded baseline; log the hypothesis (randomization + regenerate-on-collision lowers persona sameness) confirmed or refuted.
