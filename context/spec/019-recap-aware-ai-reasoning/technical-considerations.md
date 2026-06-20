# Technical Specification: Recap-Aware AI Reasoning

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

A **pure local-LangGraph prompt change** — no AgentCore, no new node, no new LLM call site, no state field, no RNG. The day-round recap's standings are already computed (spec 018's `render_day_round_recap`); today they only reach an AI player incidentally, buried in the scrolling 30-message context window where they compete with chatter and scroll out of view. This change factors the standings text into a reusable pure helper and injects it as a **dedicated, front-and-center block** into each AI player's Day-speech and vote prompts, so the standings are a guaranteed decision input rather than incidental history.

Because the behavioral effect (a more decisive AI town) is non-deterministic, acceptance follows the **effort-not-results** principle (architecture §6 / CR 005): the automated suite verifies the *structural* injection and the non-leak invariant; the behavioral effect is measured **out-of-suite** by running `make blunder-eval` against the committed baseline and recording the confirmed-or-refuted result in the quality ledger.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component Breakdown (`src/graphia/nodes/day.py`, `src/graphia/prompts.py`)

- **`_render_standings(state) -> str`** *(new, pure, in `day.py`)* — factored out of `render_day_round_recap`. Returns the **decision-relevant standings text only**: the `{law_clause} and {mafia_clause} remain` side-counts sentence, the votes-called-today clause, and the executed-today clause. It carries **no clock** (that is spec 020's, recap-only) and **no `"Day N status:"` framing prefix** (that stays in the recap composer). The existing singular/plural and votes/executed clause logic moves wholesale from `render_day_round_recap` into this helper. Reads `cycle` (for `_executed_this_cycle`), the insertion-ordered `players` dict, and `day_votes_initiated`; mutates nothing; no RNG; no hash-ordered `set` iteration.
- **`render_day_round_recap`** *(refactor)* — becomes a thin composer: it calls `_render_standings(state)` for the body, keeps its `"Day {day} …"` framing (and, once spec 020 lands, the clock), and wraps the result in the public `SystemMessage`. Net behavior of the public recap is unchanged by spec 019 (same text); the standings shown publicly and the standings fed to the AI are now the same string by construction.
- **`DAY_SPEAK_USER_TEMPLATE` / `AI_VOTE_USER_TEMPLATE`** *(`prompts.py`)* — add a dedicated `{standings}` slot to each, placed **prominently**: immediately after the role / win-condition / team / persona line and **before** `Recent public discussion:`. Frame it so the model reads it as live state to act on, e.g. a short labeled block `Current standings (act on these):\n{standings}`. The vote template gains the higher-value injection — today `AI_VOTE_USER_TEMPLATE` carries no standings at all, so a voter decides Yes/No from only the rolling context.
- **`_ai_day_action` / `_ai_ballot`** *(call sites)* — add `standings=_render_standings(state)` to their existing `.format(...)` calls. Both already have `state` in scope; no new plumbing.

No `state.py` change; no `graph.py` change (rides the existing `day_turn`/`day_close` wiring); no new node; no new LLM call site.

### Logic / contracts

- `_render_standings` is the single source of the standings text; `render_day_round_recap` (public recap) and the two AI prompts all consume it, so they can never drift.
- Placement decision: front-and-center (before the scrolling context), so the standings are a reliable input even when the recap has scrolled out of the 30-message window.
- Day-number framing stays out of `_render_standings` (kept in the recap composer) so the AI prompt's standings block is clean decision-facts; the day number still reaches the AI via the scrolling context and the day-open victim reveal.

### Measurement (operational, not code)

Acceptance's behavioral check is run, not coded: after the change, run `make blunder-eval` (provider of choice) and compare Day-decisiveness indicators — vote-initiation rate, share of games reaching a win/loss vs `no_winner`, town win-rate — against the committed baseline record `2026-06-19T18-33-37` in `evals/blunder-ledger.yaml`; log the hypothesis confirmed or refuted (both satisfy acceptance, per CR 005).

---

## 3. Impact and Risk Analysis

- **System dependencies:** none new. Reuses spec 018's recap computation and the existing AI-turn invokes. Composes cleanly with spec 020 — after both land, `render_day_round_recap` (public recap) carries the clock while `_render_standings` (prompt-fed) does not; the decoupling is intended.
- **Existing-test impact (the main risk):** adding the `{standings}` slot makes every direct `.format()` of the speak/vote templates that omits `standings=` raise `KeyError`. ~5 test helpers/sites must each add a `standings=` kwarg: `tests/test_behavioral_integrity.py` (`_render_day_prompt`; `test_ai_vote_template_supplies_relationship_placeholder`), `tests/test_blunder_eval_detectors.py` (`_day_prompt` + the inline non-day-speak render), `tests/test_instrument_capture.py` (`_day_prompt`), `tests/test_personas.py` (`_render_day_prompt`). **Mitigation:** fix all sites as part of this work; covered by the suite going green.
- **Day-speaker resolver coupling:** `tests/test_behavioral_integrity.py::_DAY_SPEAKER_RE` anchors on the prose around `{speaker}`. Placing `{standings}` *after* the role line (not between `{speaker}` and its anchor) keeps the regex valid; re-verify after the edit.
- **Prompt duplication:** the standings can appear twice in one prompt (front-and-center + incidentally in `{context}`). Acceptable and intended — do not dedup.
- **Non-leak:** `_render_standings` emits only aggregate living counts by side, the votes count, and the executed (already-public) player's side — never a living player's secret side. Same disclosure posture as the public recap.
- **Determinism:** pure, no RNG; the byte-equal dual-mode smoke and seeded fakes are unaffected (the faked LLM's output does not depend on prompt text; prompts are not in the public log).

---

## 4. Testing Strategy

Structural-invariant only (architecture §6); never asserts verbatim LLM text; the behavioral effect is eval-measured out-of-suite. `safe_llm` needs no extension (no new call site).

- **Pure `_render_standings`** over hand-built `GameState` (reuse `tests/test_slice_day_round_recap.py` `_roster`/`_player`): living counts by side (singular/plural, space-pinned word boundary); votes-called-today sweep (0/1/N); executed-today (named + side; none; stale prior-cycle execution excluded); **no clock tokens** (`"AM"`/`"PM"`/`"midnight"` absent — the 019↔020 boundary); purity (no state mutation).
- **Non-leak invariant:** over a roster with a living Mafioso and living Citizen, assert no living player's name co-occurs with a side label in the standings — only aggregate counts (and the executed dead player's already-public side).
- **Injection seam:** capture the prompt at the faked-LLM boundary (the existing `_CapturingDayFake` pattern in `tests/test_personas.py`) — drive the real `_ai_day_action` and `_ai_ballot`, assert the captured prompt contains `_render_standings(state)`. Plus a template-slot guard: `.format(...)` each template with all kwargs (incl. `standings=`) renders without `KeyError` and includes the standings text.
- **No new fixtures / RNG.** New tests land in `tests/test_slice_day_round_recap.py` (or a sibling), reusing the spec-018 helpers.
