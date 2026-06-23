# Technical Specification: Role-Specific Day Guidance for AI Players

- **Functional Specification:** `./functional-spec.md`
- **Status:** Completed *(verified 2026-06-23 — effort-not-results measurement recorded in the 2026-06-22 ledger runs; [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md))*
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

A **pure local-LangGraph prompt change**, in the same mould as spec 019 (Recap-Aware AI Reasoning) — no AgentCore, no new node, no new LLM call site, no state field, no RNG. Today the AI Day prompts end with a generic, side-agnostic closing instruction ("take your turn… / cast your ballot…"). This change injects a **role-matched closing guidance block** at the very end of both Day prompts (the speaking turn and the vote), spelling out the concrete plays for the actor's side — Law-abiding or Mafioso.

The block is added as a new `{role_guidance}` slot at the **tail** (recency position) of `DAY_SPEAK_USER_TEMPLATE` and `AI_VOTE_USER_TEMPLATE`, *after* the existing role grounding / persona / standings / discussion and *before or at* the final "take your turn / cast your vote" sentence. A small pure builder in `nodes/day.py` returns the role-matched text, or `""` when the feature flag is off (reverting to the exact pre-024 prompt).

Like every gameplay-influencing change in this project (ADR 011 — Ablatable Gameplay Feature Flags), it is gated by a **default-on env flag** (`GRAPHIA_ROLE_GUIDANCE`), parsed in `config.py` and threaded into the two AI-decision nodes via the established `_assemble_graph` partial-injection pattern — through **both** `build_graph` (local) and `build_runtime_graph` (Runtime) so the modes can't drift.

Because the behavioral effect (a more decisive, better-coordinated town) is non-deterministic, acceptance follows the **effort-not-results** principle (architecture §6 "Determinism Posture & Testing Conventions" / CR 005): the automated suite verifies the *structural* injection, role-matching, and the never-reveal invariant; the behavioral effect is measured **out-of-suite** via `make blunder-eval` against the committed baseline and logged confirmed-or-refuted.

This spec shares its edit surface with **spec 025 (Fuller Multi-Day Discussion Window)** — both touch `_ai_day_action` / `_ai_ballot`, the two prompt templates, `config.py`, and the graph threading. They are additive to *different* parts of those functions; see §3 "Cross-spec coordination with spec 025" for the shared surface and the recommended implementation order.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component Breakdown

**`src/graphia/prompts.py` — new role-guidance text constants**

- Two new module-level string constants, one per side, holding the concrete action menus. Worded faithfully to the functional spec's requirements:
  - **Law-abiding action menu** — the town wins *only* by executing Mafiosos; so: watch for a likely Mafioso; voice / openly accuse that suspicion; put a *genuine* suspect up for a vote-to-execute before the Day ends; and the explicit caution — do **not** get fellow Law-abiding Citizens executed, do **not** accuse without a reason (gather information instead when there's no real lead). Phrased as a menu of plays, not a bare "you must vote."
  - **Mafioso action menu** — hold the public cover persona; cast suspicion onto Law-abiding Citizens; protect and quietly coordinate with fellow Mafiosos; steer votes toward Citizens and away from the Mafia. Under a standing rule it **never** instructs revealing the role, naming teammates, or dropping the cover (this reinforces, and must not contradict, the existing `_persona_block` never-reveal line for a Mafioso).
- A short shared **label/framing prefix** (analogous to spec 019's `"Current standings (act on these):"`) so the block reads as a directive — e.g. a "Your side's plays right now:" header — concrete wording chosen at implementation. The label is the structural marker the tests assert on and the flag toggles.

**`src/graphia/nodes/day.py` — a pure role-guidance builder**

- **`_role_guidance_block(role: str, *, enabled: bool) -> str`** *(new, pure)* — mirrors `_standings_prompt_block`'s shape exactly:
  - `enabled=False` ⇒ returns `""` so the `{role_guidance}` slot collapses and the prompt reverts to its pre-024 form (no label, no body, no stray blank line). This is the ADR-011 ablation seam.
  - `enabled=True` ⇒ returns the labelled block for the actor's side: the Mafioso menu when `role == "mafia"`, the Law-abiding menu otherwise (matching the `_role_label` / `_team_line` role-branch convention already in the file).
  - Pure: no state read beyond the passed `role`, no RNG, no LLM, no `set` iteration — so the dual-mode byte-equal smoke (`tests/test_dual_mode_smoke.py`) is unaffected.
- **`_ai_day_action` / `_ai_ballot`** *(call sites)* — each gains a new keyword-only param `role_guidance_enabled: bool = True` (defaulted, mirroring the existing `recap_aware_reasoning_enabled` param) and adds `role_guidance=_role_guidance_block(speaker.role, enabled=role_guidance_enabled)` (resp. `voter.role`) to its existing `.format(...)` call. Both already have the actor's `PlayerState` in scope; no new plumbing.

**`src/graphia/prompts.py` — template slots**

- Add a `{role_guidance}` slot to the **end** of both `DAY_SPEAK_USER_TEMPLATE` and `AI_VOTE_USER_TEMPLATE`, positioned after `Recent public discussion:\n{context}` and the existing never-reveal line, and at/just-before the final "take your turn" / "cast your ballot" instruction. Placement is deliberately **last** for recency — it is the most salient thing the model reads before acting (the functional-spec rationale).

**`src/graphia/config.py` — the flag**

- New field `role_guidance_enabled: bool = True` on `GraphiaConfig` (defaulted so direct test construction stays valid — same convention as `recap_aware_reasoning_enabled`).
- In `load_config`, parse via the existing default-aware helper: `role_guidance_enabled = _env_flag("GRAPHIA_ROLE_GUIDANCE", default=True)`, and pass it into the returned config. Default-on; an explicit falsy value (`0`/`false`/`no`/`off`) reverts to the pre-024 baseline for A/B.

**`src/graphia/graph.py` — threading (anti-drift)**

- `_assemble_graph` gains a `role_guidance_enabled: bool` keyword param. It is bound into the **two AI-decision nodes** via the existing `partial(...)` composition — alongside `recap_aware_reasoning_enabled`:
  - into `day_turn` (which calls `_ai_day_action`), composed onto its existing `emit(...)` + `recap_enabled` + `recap_aware_reasoning_enabled` binding;
  - into `collect_votes` (which calls `_ai_ballot`), alongside its existing `recap_aware_reasoning_enabled` binding.
  - `day_turn` and `collect_votes` forward the new param down to `_ai_day_action` / `_ai_ballot` respectively.
- `build_graph` passes `role_guidance_enabled=config.role_guidance_enabled` into `_assemble_graph`.
- **`src/graphia/runtime/graph_builder.py`** — `build_runtime_graph` gains a `role_guidance_enabled: bool = True` param (defaulted, matching the config default) and forwards it to `_assemble_graph`; the production Runtime entrypoint passes `load_config().role_guidance_enabled`. This is the named anti-drift requirement (the module docstring's prior-incident note): **both** builders must thread the flag or local and remote diverge.

No `state.py` change; no new node; no new LLM call site; `safe_llm` needs no extension (no new call site).

### Logic / contracts

- **Role-matching is exhaustive and exclusive.** `_role_guidance_block` returns exactly one side's menu, keyed on the actor's true `role`; a Citizen never receives Mafia text and vice-versa. This is the knowledge-boundary posture already enforced for `_team_line` (a Citizen's is `""`) — applied here to the directive content.
- **Composes, does not replace.** The block is additive: the spec-013 role grounding, the spec-016 persona, and the spec-019 standings all remain; this only appends the role-specific directive at the close.
- **Never-reveal consistency.** The Mafioso menu must not contradict `_persona_block`'s standing "never reveal you are Mafia or that your persona is a front" instruction — it reinforces it. The Mafioso menu contains no instruction to disclose side, name teammates, or drop cover.
- **Single source of guidance text.** The two side-menus live as the prompt constants; both call sites consume them through the one `_role_guidance_block` builder, so the speak-prompt and vote-prompt guidance can never drift.

### Measurement (operational, not code)

After the change, run `make blunder-eval` (provider of choice) and compare against the recorded baseline in `evals/blunder-ledger.yaml`: town win-rate by side, **votes initiated**, share of games **resolved vs `no_winner`**, and a watch on **Law-abiding-executed-by-Law-abiding** (so a lift in decisiveness isn't bought by citizens executing each other — the functional spec's explicit guard). Log the hypothesis confirmed or refuted in the ledger (both satisfy acceptance, per CR 005). The Bedrock path costs tokens; the Ollama path is free. Per the `blunder-eval` contract, run per provider for comparable records, and clean-or-commit transcripts before the measured run so the eval doesn't stamp `code.dirty: true`.

---

## 3. Impact and Risk Analysis

- **System dependencies:** none new. Reuses the existing AI-turn invokes and the established ADR-011 flag + partial-injection pattern; no AgentCore, no Bedrock-schema change, no checkpoint-serde change.

- **Existing-test impact (the main risk, identical in shape to spec 019):** adding the `{role_guidance}` slot makes every direct `.format()` of the speak/vote templates that omits `role_guidance=` raise `KeyError`. The same cluster of test helpers/sites that spec 019 had to touch for `standings=` must each add a `role_guidance=` kwarg — at minimum: `tests/test_behavioral_integrity.py` (`_render_day_prompt`, the vote-template placeholder test), `tests/test_blunder_eval_detectors.py` (`_day_prompt` + inline non-day render), `tests/test_instrument_capture.py` (`_day_prompt`), `tests/test_personas.py` (`_render_day_prompt`), and the spec-019 template-slot guards in `tests/test_recap_aware_reasoning.py`. **Mitigation:** update all sites as part of this work; covered by the suite going green. Enumerate the live set with a grep for the two template names at implementation, since spec 025 may add/relocate sites concurrently.

- **Day-speaker resolver coupling:** `tests/test_behavioral_integrity.py::_DAY_SPEAKER_RE` anchors on prose around `{speaker}` near the top of `DAY_SPEAK_USER_TEMPLATE`. Appending `{role_guidance}` at the **tail** (not between `{speaker}` and its anchor) keeps the regex valid; re-verify after the edit — same caution spec 019 noted.

- **Prompt-budget interaction with spec 025:** the role-guidance block is small (a handful of lines), but it competes for the same prompt budget the spec-025 window expands. With both landed, the guidance block sits at the prompt tail and the discussion window in the middle; the spec-025 token-budget cap (its R3 defensive bound) trims the *discussion history*, never the role grounding or this tail guidance. Confirm the two don't double-trim — see spec 025's `technical-considerations.md` §3.

- **Cross-spec coordination with spec 025 (shared edit surface):** both specs edit `_ai_day_action` / `_ai_ballot` signatures, both templates, `config.py` (`GraphiaConfig` + `load_config`), and the `_assemble_graph` / `build_graph` / `build_runtime_graph` threading. They are **additive to different parts**: 024 appends a tail slot and a `role`-keyed builder + a flag; 025 changes the windowing inside `_render_context` and adds a window-size setting + (potentially) a provider-tier change. **Recommended order: land 024 first** (smaller, pure prompt + flag, no provider question), then 025 (which carries the verify-at-implementation Ollama-context investigation). When 025 lands second it re-touches the same template `.format()` test sites and the same threading params — adding its window param next to this spec's `role_guidance_enabled`. Each new keyword param is appended to the partials independently, so the two changes merge without conflict if landed in this order.

- **Determinism:** pure builder, no RNG; the byte-equal dual-mode smoke and seeded fakes are unaffected (the faked LLM's output does not depend on prompt text; prompts are not in the public JSONL log).

- **Non-leak:** the block contains only the actor's own side's generic strategy text — no other player's name, role, or allegiance — so it discloses nothing. The Mafioso never-reveal rule is reinforced, not weakened.

---

## 4. Testing Strategy

Structural-invariant only (architecture §6 "Determinism Posture & Testing Conventions"); never asserts verbatim LLM text; the behavioral effect is eval-measured out-of-suite. New tests land in a dedicated file (e.g. `tests/test_role_guidance.py`), mirroring the structure of `tests/test_recap_aware_reasoning.py`.

- **Pure `_role_guidance_block`:** `role="law_abiding"` returns the Law-abiding menu and not Mafia text; `role="mafia"` returns the Mafioso menu and not Law-abiding text; `enabled=False` returns `""` for both roles. Assert on the distinctive marker phrases (e.g. the never-reveal phrase appears only in the Mafia menu; the "don't get fellow Law-abiding executed" caution appears only in the Citizen menu).
- **Per-role guidance reaches BOTH prompts (injection seam):** drive the REAL `_ai_day_action` and `_ai_ballot` through a content-recording `get_large()` fake (patched at `graphia.nodes.day.get_large` after the autouse `safe_llm` — the `_CapturingDayFake` pattern), once with a Citizen actor and once with a Mafioso actor; assert the captured HumanMessage contains that actor's side menu — proving the block reaches the speak prompt and the vote prompt for both sides (the functional spec's "all four cases").
- **Never the other side's guidance / never reveal:** a Citizen prompt never contains any Mafia-menu marker phrase; a Mafioso prompt never contains any Law-abiding-menu marker phrase; the Mafioso prompt contains no instruction to disclose side, name teammates, or drop cover (assert the absence of reveal-shaped phrasing).
- **Tail placement (recency):** assert the role-guidance block appears **after** `Recent public discussion:` in the rendered prompt (the contrast to spec 019's standings, which sit *before* it) — locking in the recency placement the functional-spec rationale depends on.
- **Flag-off parity:** with `role_guidance_enabled=False` passed through the real nodes, both prompts revert to the pre-024 form — no label, no menu body — for both sides; the flag-on default keeps the block (the ADR-011 ablation-parity pair, exactly as `tests/test_recap_aware_reasoning.py` does for spec 019).
- **`load_config()` default-on semantics:** unset/blank ⇒ on; truthy ⇒ on; explicit falsy ⇒ off — the `GRAPHIA_ROLE_GUIDANCE` unit, mirroring the spec-018/019 config tests.
- **Template-slot guard:** each template `.format(...)` with all required kwargs (incl. `role_guidance=`) renders without `KeyError`, includes the guidance text, and leaves no `{role_guidance}` placeholder.
- **Threading anti-drift:** a test compiling `build_runtime_graph(...)` with the flag off behaves like the local graph with the flag off (or, lighter-weight: assert `build_runtime_graph`'s signature carries `role_guidance_enabled` and forwards it — matching the spec-019 anti-drift coverage).
- **No new fixtures / RNG; `safe_llm` needs no extension** (no new call site).
