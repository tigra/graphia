# Technical Specification: AI Character Personas

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

A persona is a small structured record attached to each AI player, generated once at game start by the **heavyweight (creative) LLM tier** (architecture §4 names "character-sheet generation" as a primary-tier use) in a **new setup node placed after role assignment** — so it can tailor a Mafioso's cover-legend-plus-true-self differently from a Citizen's single honest persona. It is **pure in-game state**: like roles, it lives on `PlayerState` for the game's lifetime and is **not persisted across sessions** — no AgentCore Memory store, no `DiaryStore`-style abstraction (personas are simpler than diaries, which need cross-Night read-back).

During play the persona is **never broadcast**; it is injected only into that AI's **own** Day-speech prompt, composing with the spec-013 role/identity grounding already assembled there — persona is the *voice/temperament* layer atop the *role-facts* layer. A Mafioso's prompt carries **both** its true self and its public legend plus an explicit "stay in cover" instruction; the legend never leaks allegiance, and the true self appears only in that Mafioso's own prompt (knowledge-boundary preserved, per spec 013). The rules, turn structure, win conditions, and vote/pointing mechanics are **untouched** — temperament colours style and inclination within the existing flow. At game end, `end_screen` appends a **public persona-reveal** section, contrasting each Mafioso's legend with its true self.

Primary stack: **LangGraph orchestration** (`langgraph-agentic`) — the `PlayerState` field, the generation node, the prompt injection, the reveal; **testing** (`testing`) — the offline suite + a persona LLM-fake. No new dependency, no new technology → **no `/awos:hire`**.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Persona data on `PlayerState` + a flat generation schema — `src/graphia/state.py`, `src/graphia/llm.py` — **[Agent: langgraph-agentic]**

- `PlayerState` (a `@dataclass`) gains one optional field, `persona: PlayerPersona | None = None`, where `PlayerPersona` is a small frozen dataclass: `personality`, `manner` (manner of speaking), `public_persona` (the face shown to the table — a Mafioso's legend / a Citizen's honest self), and `true_self` (a Mafioso's real backstory; empty for Citizens). Defaulted to `None` so existing direct constructions stay valid. (Personas are used *inside* the runtime for prompts + the end message; they don't depend on the UI-side `repr` round-trip that motivated lifting `human_role` to top-level.)
- **Rebuild-site safety.** `PlayerState` is reconstructed wholesale at several sites (`assign_roles`, `resolve_vote`, `resolve_night_kill`). Switch those rebuilds to **`dataclasses.replace(player, <changed fields>)`** so `persona` (and any future field) carries over automatically — avoiding the "forgot to thread the new field" class of bug.
- A flat Pydantic **`Persona`** schema in `llm.py` for the LLM call: `personality: str`, `manner: str`, `public_backstory: str`, `secret_backstory: str` (Citizens leave `secret_backstory` empty). **Flat primitives only** — same Bedrock-Converse constraint as `Roster`/`Ballot`/`DayAction`.

### 2.2 Persona-generation node — `src/graphia/nodes/setup.py`, `src/graphia/prompts.py`, `src/graphia/graph.py` — **[Agent: langgraph-agentic]**

- New node **`generate_personas(state)`** wired **between `assign_roles` and `introduce_roster`** (edge `assign_roles → generate_personas → introduce_roster`). For each **AI** player (skip the human), call `get_large().with_structured_output(Persona)` with a **role-tailored** prompt — a Citizen prompt asks for one honest persona; a Mafioso prompt asks for a **public legend (cover)** plus a **true Mafioso backstory** — anchored on the player's existing name for distinctness. Apply the project's **validation-retry-then-fallback** (one corrective retry on a malformed/wrong-shape response; then a deterministic minimal-persona fallback so a flaky model never blocks setup — mirroring `_generate_names`). Convert the flat `Persona` → `PlayerPersona` and store it on the player.
- **Per-player generation** (N ≤ 11 heavyweight calls at startup, one-time). *Rejected alternative:* one batched call returning all personas — a list-of-persona-objects schema is exactly the nested shape Bedrock Converse rejects, and batching dilutes distinctness; per-player keeps the flat-schema contract and the role-tailored prompt clean.
- New prompt constants: `PERSONA_SYSTEM`, `PERSONA_CITIZEN_USER_TEMPLATE`, `PERSONA_MAFIA_USER_TEMPLATE`.
- **Distinctness** is *encouraged* (the distinct name anchor + a "make this character distinct" instruction), **not hard-enforced** on free prose — a documented relaxation versus the `Roster`'s exact-distinct names (you can't string-dedup a personality).

### 2.3 Inject the persona into the Day-speech prompt — `src/graphia/nodes/day.py`, `src/graphia/prompts.py` — **[Agent: langgraph-agentic]**

- `DAY_SPEAK_USER_TEMPLATE` gains a `{persona}` slot (placed alongside the role grounding). In `_ai_day_action`, build the persona block from the speaker's `PlayerState.persona`: for **all** AIs, the personality + manner + the `public_persona` it projects; for a **Mafioso**, *additionally* its `true_self` plus an explicit *"this is your secret — stay in your cover, never reveal you are Mafia"* instruction. Composes with the existing spec-013 grounding (`_role_label` / `_win_condition_line` / `_team_line`) — persona is the voice layer, grounding the role-facts layer.
- The persona is injected **only into that speaker's own prompt** (never broadcast); the Mafioso's `true_self` appears only there. The existing `_render_context` `private_to` filter is unchanged.

### 2.4 (Secondary) Persona in Night pointing — `src/graphia/nodes/night.py`, `src/graphia/prompts.py` — **[Agent: langgraph-agentic]**

- Optionally thread a Mafioso's `personality`/`manner` into `MAFIA_POINT_USER_TEMPLATE` (via `_ai_pick_target`) so its private pointing reasoning stays in character. **Secondary / light** — Night pointing is silent (no public speech), so persona's effect here is minor; this is a small enhancement, not a required behaviour. Can be dropped without affecting the core feature.

### 2.5 End-of-game persona reveal — `src/graphia/nodes/endgame.py`, `src/graphia/prompts.py` — **[Agent: langgraph-agentic]**

- `end_screen` appends a **public** persona-reveal section (no `private_to` → every player sees it) after the existing winner + kill-log + role-reveal sections. For each AI player: name, role, and persona (personality + backstory). For a **Mafioso**: contrast the **public legend** it performed against its **true self**. New prompt constants for the reveal header + line format.
- Covers **all** AI players (survivors and eliminated). Shown **only** at end — the persona never appears in any message before the outcome. This is a **plain** reveal; the richer Moderator creative recap (separate Phase 6 *End-of-Game Payoff* item) may later weave personas into a narrative — out of scope here.

---

## 3. Impact and Risk Analysis

- **Blast radius:** `state.py` (`PlayerState` field + the `dataclasses.replace` rebuild sites), `llm.py` (`Persona` schema), `setup.py` + `graph.py` (new node + edge), `prompts.py` (persona + reveal templates), `day.py` (speak-prompt injection), `night.py` (optional), `endgame.py` (reveal). **No change** to win conditions, turn structure, or vote/pointing mechanics.
- **Risk — a rebuild site silently drops the persona.** *Mitigation:* `dataclasses.replace` at the rebuild sites (§2.1) carries every field forward; a test asserts a player's persona survives a role change / a kill.
- **Risk — a Mafioso persona leaks allegiance** (the legend "sounds Mafia", or the true self bleeds into public speech). *Mitigation:* the `public_persona`/legend is generated as a cover with no Mafia signal; the `true_self` is injected only into the Mafioso's own prompt with an explicit non-disclosure instruction. This is **non-deterministic** LLM behaviour — a measured *effort*, not a guarantee (architecture §6 / [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)). Tests assert the **wiring** (true self + cover instruction present in the Mafioso's prompt; true self absent from every other prompt and every in-play public message); the model's discretion is best-effort.
- **Risk — persona warps game balance.** *Mitigation:* persona is injected as voice/temperament guidance only; the rules/turn/vote/pointing code is untouched and the role goal + win conditions are unchanged (functional §2.2).
- **Risk — startup cost/latency** (N heavyweight calls at game start). *Mitigation:* N ≤ 11 (table cap), one-time at setup; acceptable. Batching was rejected for the schema reason in §2.2.
- **Determinism posture unchanged** (architecture §6): personas are LLM-generated and non-reproducible; tests mock the persona call and never assert persona prose verbatim. No `GRAPHIA_SEED`. **No AgentCore Memory** — personas are transient in-state, consistent with transcripts/diaries being non-persistent across sessions.
- **`safe_llm` net:** `generate_personas` introduces a `get_large()` call **in `setup.py`** (today only `get_small` is called there). The autouse `safe_llm` fixture must be extended to patch `graphia.nodes.setup.get_large`, or a forgotten stub falls through to real Bedrock (per the CLAUDE.md convention). Flagged for the testing task.

---

## 4. Testing Strategy — **[Agent: testing]**

Offline and model-free: extend the unified large-model fake (`FakeLargeUnified` in `tests/conftest.py`) to handle the new `Persona` schema (a `personas=[...]` queue dispatched on schema, like `day_actions`/`pointings`), and **extend `safe_llm` to patch `graphia.nodes.setup.get_large`** (§3). Never assert persona prose verbatim (architecture §6) — assert structural presence.

- **Generation:** after setup, every **AI** player has a `persona` populated and the **human has none**; a Mafioso's stored persona has both a `public_persona` and a non-empty `true_self`; a Citizen's has an honest `public_persona` and empty secret. Validation path: a malformed/wrong-shape persona response → one retry → deterministic fallback persona (setup never blocks).
- **Day-speak injection:** the Day-speech prompt for an AI contains its persona's personality/manner/`public_persona`; a **Mafioso's** speak prompt *also* contains its `true_self` + the stay-in-cover instruction, while a **Citizen's** does not; the persona block is **absent from other players' prompts** (private to the speaker). (Reuse the prompt-capture pattern added for spec 015's convergence tests.)
- **No leak / knowledge-boundary:** a Mafioso's `public_persona` text carries no allegiance tell (assert the legend string does not contain its `true_self` / "mafia"); the `true_self` never appears in any non-owner prompt or any public message during play.
- **Reveal:** `end_screen`'s public message includes every AI player's persona; for a Mafioso it contrasts legend vs true self; **nothing persona-related appears in any message before the end**. Covers eliminated players too.
- **Mechanics untouched (regression):** a default game still runs to completion with the new node in the graph; turn/vote/pointing counts and win-condition outcomes are unchanged. The persona survives a `dataclasses.replace` rebuild (role change + kill).
- No new dependency; the persona call site lives in the already-patched `setup.py` module (just add the `get_large` binding to `safe_llm`).
