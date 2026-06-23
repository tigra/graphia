# Functional Specification: Distinct AI Personas Across the Roster

- **Roadmap Item:** Follow-up to **spec 016 (AI Character Personas)** §2.1 (*Every AI player gets a fresh, distinct, persistent persona*) — backlog item *"No cross-call persona-diversity guarantee"*, chosen approach **(b)**. Not a new roadmap feature.
- **Status:** Completed *(verified 2026-06-23 — effort-not-results measurement recorded in the 2026-06-22 ledger runs; [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md))*
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Spec 016 gave each AI player a persona and asked that "no two AI players feel like the same character." In practice they do. Because every persona is created in isolation — one independent request per player, told only that player's name — the creative model, left to its own devices, keeps reaching for the same safe character: a calm, reasonable, observant townsperson. A measured review found the roster collapsing into one placid voice, with distinct setups (a handyman, a librarian) producing no distinct speech. The cast that was meant to feel like a room of real people reads like one person repeated.

This change makes the AI cast **noticeably distinct from one another**. The agreed approach (backlog option **(b)**): when each AI character is created, the characters **already created for that same game are available to the creator**, so the new one can be made deliberately different from them — instead of every character being invented from a blank slate. (Concrete mechanics are the technical spec's job.)

Because the result is produced by a creative model, it is **non-deterministic**, so success follows the project's **effort-not-results** principle ([CR 005](../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)): we put the mechanism in place, add a **persona-distinctiveness metric** computed over the **generated persona text**, record it as a standing number in the quality ledger, and compare it before vs after on a comparable set of games — the work is accepted whether the metric improves **or** not (a confirmed *or* refuted hypothesis both count as done). It does **not** promise that every roster will be vividly varied.

**Desired outcome:** each new AI character is created with the game's already-created characters in view, so it can be made different from them; the cast's voices and temperaments are measurably less alike than before; and none of spec 016's guarantees (no allegiance leak, rules untouched, no human persona, the Mafioso two-layer cover) are weakened.

**Success looks like:** a persona-distinctiveness metric over the generated persona text is recorded as a standing ledger number and compared against the pre-change baseline under effort-not-results; the differentiation is visible in a game's transcript (characters can be told apart by voice); and every spec-016 invariant still holds.

---

## 2. Functional Requirements (The "What")

### 2.1 Each AI character is created to differ from the ones already created

- When a game's AI characters are set up, each new character is created **with the characters already created for that game available**, so it can be made deliberately distinct from them — rather than invented in isolation.
  - **Acceptance Criteria:**
    - [x] Given a new game with several AI players, when their characters are created in turn, then each character after the first is created with the earlier characters of that same game available to its creation — verifiable in the recorded game data, not merely asserted.
    - [x] Given the very first AI character of a game, when it is created, then it is created normally (there is nothing yet to differ from) and setup still completes.
    - [x] Given character creation fails or returns nothing usable for a player, when setup continues, then that player still receives a valid fallback character and the game still starts (no blocking, same safety net as today).

### 2.2 The roster's characters are noticeably distinct from one another

- Across a game, the AI characters differ from each other in personality and manner of speaking, so the table reads like distinct people — extending spec 016 §2.1's "no two feel the same" from a nominal guarantee to a measured one.
  - **Acceptance Criteria:**
    - [x] Given a finished game, when its AI characters' voices are compared, then they are recognizably different from one another (observed in the transcript) — understood as *possible, not guaranteed* on any single game, per effort-not-results.
    - [x] Given distinctiveness is about the **table-facing** character, when a Mafioso is among the cast, then it is the Mafioso's **public cover** that is made distinct from the others; its hidden true self is never exposed to the rest of the table by this change.

### 2.3 A persona-distinctiveness metric is added to the eval ledger (effort-not-results)

- A persona-distinctiveness indicator — computed over the **generated persona text** (each AI character's personality, manner, and backstory) across a game's roster — becomes a **standing metric recorded with every measured run** in the quality ledger, so distinctiveness is a tracked, comparable property over time. The change is evaluated against the pre-change baseline using this metric.
  - **Acceptance Criteria:**
    - [x] Given a measured run, when its record is written to the quality ledger, then it carries a **persona-distinctiveness metric** computed over that run's generated persona texts, alongside the existing metrics.
    - [x] Given a comparable measured run before and after the change, when their persona-distinctiveness metrics are compared, then the comparison is recorded and reviewed.
    - [x] Given that comparison, when reviewed, then the change is accepted whether the metric improved or not; the hypothesis ("creating each character aware of the others makes the roster more distinct") is logged as confirmed or refuted, and a refuted result is a valid, complete outcome.

### 2.4 Spec 016's guarantees are preserved

- This change only affects how distinct the characters are from each other; everything spec 016 fixed stays fixed.
  - **Acceptance Criteria:**
    - [x] The **human player still has no persona**; only AI players do.
    - [x] A character — Citizen's honest self or a Mafioso's legend — **still never hints at allegiance**: an attentive human cannot tell Mafia from Citizen by character alone.
    - [x] Personas **still never change the rules, turn order, or any action** an AI must take; they remain an expressive layer only.
    - [x] Each AI character is still created **fresh per game** and stays **fixed for the whole game** (no drift mid-game), and the **end-of-game reveal** still shows every AI's true character (a Mafioso's legend vs its true self).

---

## 3. Scope and Boundaries

### In-Scope

- Creating each AI character **aware of the characters already created in that game** (backlog option **(b)**), so it can be made distinct from them.
- Adding a **persona-distinctiveness metric** (computed over the **generated persona text**) as a **standing number in the eval ledger/summary**, recorded with every measured run and used to compare before vs after under the effort-not-results acceptance principle.
- Preserving every spec-016 invariant (no human persona, no allegiance leak, rules/turn-structure untouched, fresh-per-game + fixed-in-game, end-of-game reveal).

### Out-of-Scope

- The **other generation approaches** to the same gap (generate the whole roster in one shot; pre-assign fixed archetypes/temperaments) — this spec commits to approach **(b)** only; the others remain backlog options.
- The **other persona-realism fixes** in the backlog — stronger persona salience in the Day-speech prompt, a wider temperament range (vigilant / brash / assertive archetypes), behaviour-under-stress, and persona-backstory bleed — which are separate items.
- The **Mafioso dark-triad** true-self direction (separate backlog item) — that changes *what* a Mafioso's hidden self is, not how distinct the roster is.
- **Measuring distinctiveness over Day *speech*** (rather than the generated persona text) — the metric here is computed over the persona text, the direct test of option (b); a speech-level distinctiveness measure, confounded by the separate persona-salience gap, is a separate idea.
- A **regenerate-on-collision** corrective step (the other half of backlog option d) — this spec adds the distinctiveness *metric* and the option-(b) generation change, but does not automatically re-roll colliding personas.
- Any change to **persona content for its own sake**, the **human's** experience, or the game's **rules / win conditions / turn structure**.
- All other roadmap items and specs.
