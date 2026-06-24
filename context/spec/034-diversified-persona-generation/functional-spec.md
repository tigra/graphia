# Functional Specification: Diversified Persona Generation (Randomized + Regenerate-on-Collision)

- **Roadmap Item:** Persona-realism fix — the deferred half of **spec 016/031**'s backlog option (d) ("regenerate-on-collision"), now actionable because specs 031/032/033 give us the persona-similarity measures to detect a collision. Relates to the **persona-realism** thread. Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Spec 031 made each AI character aware of the others already created ("make this one different"), and specs 032/033 then *measured* how alike the cast actually is. The measurements were damning: the persona model is strongly **mode-seeking** — it keeps reaching for the same character ("an honest, slow-talking librarian who reads mystery novels"). The semantic measure put pre-031 rosters at ~0.6 similarity (vs ~0.36 for two genuinely-different characters), and the transcripts showed the failure in the raw: distinct players with the **same archetype**, and even **verbatim copies** — one game generated a character whose entire description was another player's, name and all. Worse, 031's "make this different from these" prompt sometimes made the weak local model *echo* the character it was shown rather than diverge.

This change attacks the sameness from both ends:

1. **Randomize the creation so the cast starts varied.** The root problem is that every creation gravitates to one mode. So each new character is created with deliberate variety injected: the already-created characters are shown in a **shuffled order** (no fixed anchor), the creation is **steered toward a character type drawn at random from a broad range of temperaments** (deliberately wider than the model's narrow default — vigilant, brash, dry, warm, anxious, eccentric, …), and it is given **more creative latitude**. The cast is pushed apart *before* anyone collides.

2. **Regenerate the ones that still collide.** As a safety net, when a freshly-created character is still **too similar** to one already created this game, it is thrown away and created again (with the randomization re-applied so it lands somewhere different), up to a bounded number of attempts. "Too similar" is judged by a **word-level (lexical) similarity** comparison — free, local, no model or cloud needed — with a **configurable bar**. A naïve re-roll would just hit the same mode again, so regeneration must diverge, not repeat; and it must never block the game from starting.

It is **ablatable** (on by default; a toggle reproduces the prior behavior) and **measured** under the **effort-not-results** principle ([CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)): whether it actually lowers the persona-similarity measures (031/032/033) is the open question, confirmed or refuted.

**Success looks like:** across games, the AI cast spans a noticeably broader range of temperaments instead of converging on one; no roster ships with same-type, near-duplicate, or copied characters (or, if the model is too stubborn after the attempt cap, the least-similar attempt is kept and the game still starts); and a measured run records whether the persona-similarity numbers drop — confirmed or refuted.

---

## 2. Functional Requirements (The "What")

- **Character creation is randomized so the cast starts varied.**
  - When each AI character is created with others already present, the creation injects variety three ways: the already-created characters are presented in a **shuffled order**; the new character is **steered toward a character type drawn at random from a broad, varied range** of temperaments (wider than the model's default); and the creation is given **more creative latitude** so it explores rather than repeats.
  - **Acceptance Criteria:**
    - [ ] Given several AI characters created in one game, when their creations are inspected in the recorded game data, then each is created with the existing characters shown in a varied (not fixed) order and steered toward a randomly-chosen distinct character type — verifiable in the recorded data, not merely asserted.
    - [ ] Given many games, when the cast's temperaments are reviewed across them, then they span a broad range (not predominantly the one calm/observant type) — understood as *more varied, not guaranteed perfect* on any single game, per effort-not-results.

- **A character too similar to one already created is regenerated.**
  - When a freshly-created character is too word-level-similar to a character already created this game, it is discarded and created again (with the randomization re-applied so it diverges), repeating up to a bounded number of attempts.
  - **Acceptance Criteria:**
    - [ ] Given a newly-created character that is the same type as / near-identical to one already created, when the roster is finalized, then that character has been replaced by a regenerated one (or, if the attempts ran out, by the least-similar attempt) — not left as the over-similar one.
    - [ ] Given a regeneration, when it runs, then it is steered to diverge from the character it collided with (a different type / the colliding traits avoided), not a blind repeat of the same character.

- **The "too alike" bar is a configurable setting with an aggressive default.**
  - The threshold above which two characters count as a collision is configurable. Its default is set **aggressively — to flag *same-type* characters (strongly overlapping in temperament and manner), not only near-identical copies**: well above the similarity of two genuinely-different characters, but low enough to catch the same-archetype pairs the measurements exposed (the recurring "honest, slow-talking librarian"), not just verbatim duplicates. It can be loosened (only egregious copies) or tightened for A/B. (Because the comparison is word-level, an aggressive bar may occasionally flag a not-truly-same pair; that is acceptable — a regeneration simply yields another valid character.)
  - **Acceptance Criteria:**
    - [ ] Given the bar at its default, when two genuinely-different characters are created, then neither is flagged; when two characters are the **same type / strongly overlapping** (whether or not one is a verbatim copy), then the later one is flagged and regenerated.
    - [ ] Given the bar set looser or tighter, when characters are created, then the collision sensitivity changes accordingly (for A/B comparison).

- **Regeneration is bounded and never blocks the game.**
  - Regeneration is capped at a small number of attempts per character. If the model keeps colliding past the cap, the **least-similar** attempt is kept (falling back to the deterministic safety persona only as the last resort). Setup always completes with a full, valid roster.
  - **Acceptance Criteria:**
    - [ ] Given a stubborn model that collides on every attempt, when setup runs, then it still completes with a full roster (the least-similar attempt / safety fallback) and the game starts — it never hangs or fails.

- **The change is an adjustable setting (ablatable) and its effect is measured (effort-not-results).**
  - On by default; a toggle reproduces the prior (non-diversified, no-regeneration) behavior for a side-by-side comparison (per [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md)). Whether it lowers the persona-similarity measures is measured against the recorded baseline.
  - **Acceptance Criteria:**
    - [ ] Given the setting at its default, when games are played, then randomization and regeneration are active; given it turned off, then characters are created the prior way (for A/B).
    - [ ] Given a measured run after this change, when its persona-similarity measures (031/032/033) are compared with the recorded baseline, then the comparison is recorded and the hypothesis (*randomization + regenerate-on-collision lowers persona sameness*) is logged confirmed or refuted — either a complete result.

- **Spec 016/031 invariants are preserved.**
  - The human still has no character; a character (Citizen's honest self or a Mafioso's cover) still never hints at allegiance; characters still never change rules/turn-order; each is still created fresh per game and fixed for the game; the end-of-game reveal still shows every AI's true character; and a Mafioso's hidden self is never exposed to or fed into another character's creation.
  - **Acceptance Criteria:**
    - [ ] Given any character created or regenerated, when it is finalized, then all of the above spec-016/031 guarantees still hold (no human character, no allegiance leak, fresh-per-game + fixed, reveal intact, hidden self never exposed).

---

## 3. Scope and Boundaries

### In-Scope

- Injecting variety into character creation: shuffled order of the already-created characters, a randomly-chosen target character type from a broad range, and more creative latitude.
- Detecting an over-similar new character by **word-level (lexical)** comparison (free, local, no model) and regenerating it, with a **configurable** collision bar (sensible default) and divergence-steered retries.
- Bounding regeneration with a least-similar / safety fallback so setup never blocks.
- Making the whole change ablatable (default-on) and measuring its effect against the persona-similarity measures, under effort-not-results.

### Out-of-Scope

- A **meaning-based (semantic / embedding)** collision check — the collision detector here is deliberately the free, local, word-level one; the semantic measure (spec 033) remains the *measurement*, not the in-loop generation gate.
- Changing the persona-similarity **measures** themselves (specs 031/032/033) — this *uses* them to judge success, it does not modify them.
- **Cross-game** variety (making game B's cast differ from game A's) — this diversifies *within* a single game's roster; cross-game sameness is a separate concern.
- The **human** player, game rules, win conditions, turn structure, or any non-persona behavior.
- Re-generating or re-scoring **already-recorded** games.
- All other roadmap items, which are automatically out-of-scope for this specification.
