# Functional Specification: Games Run to a Natural Conclusion (Runaway-Only Day Cap)

- **Roadmap Item:** AI-quality / eval follow-up — investigates whether early game-ends suppress the town's win-rate; relates to the **Town-coordination / Day-decisiveness** backlog thread. Not a distinct roadmap phase item.
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

In measured (eval) runs the AI town wins **0 of 10** games, and a large share of games end with **no winner** — the game is stopped before either side actually wins, often **part-way through a Day**. The working hypothesis is that the town's 0 wins is partly an artifact of **games being cut off early**, not proof the town could never win given the full game.

The early stop is **not** the game's own length limit. A measured game today is cut off after the automated player has taken a fixed number of Day-speaking turns (about 10) — which lands roughly **mid-way through the second Day** — and the result is recorded as "no winner." The game's own safety limit (a forced draw after a large number of Days) is essentially never reached, because the measured run stops the game long before then.

A Mafia game ends on its own: every night removes one player and the Mafia win once they reach the Law-abiding count, so a game **cannot run beyond a bounded number of Days** — roughly `(starting Law-abiding − starting Mafia)` Days, which at the largest allowed table (12 players) is about **10–12 Days**, and for a default 5+2 game only ~3. A length limit therefore should **not** be the thing that ends a normal game; it should exist only as a **safeguard** that stops a game that is somehow stuck or looping without progressing (a broken-logic "runaway" game).

This change makes the game's length limit a single, clearly day-denominated safeguard, set above the longest natural game, and makes a measured game **run to that same natural conclusion** instead of being chopped after a fixed number of speaking turns. The limit is **12 Days** for now — just above the ~10-Day worst case at the maximum table, leaving reserve for a larger roster and to flag genuine runaways.

**Success looks like:** a measured game runs until a side actually wins (or, only in the runaway case, the Day cap), and is no longer recorded as "no winner" because it was chopped mid-Day; a normal game of any allowed table size finishes on a real win/loss well within the Day cap; and if a game ever does hit the cap, it is flagged as an unresolved/runaway game, distinct from a real result.

---

## 2. Functional Requirements (The "What")

- **The game's length limit is a single day-denominated runaway safeguard.**
  - The limit is expressed in **Days** (the same "Day N" the game already counts), set to a fixed **12 Days** by default — comfortably above the ~10-Day longest natural game at the maximum table — so a normal game never reaches it. It applies the same way in real play and in measured runs.
  - **Acceptance Criteria:**
    - [x] Given a normal game at any allowed table size, when it is played to the end, then it finishes on a real win (Law-abiding or Mafia) and never stops at the Day cap.
    - [x] Given the largest allowed table, when the longest realistic game is played, then it still finishes within the 12-Day cap.

- **Measured (eval) games run to their natural conclusion.**
  - A measured run no longer stops a game after a fixed number of speaking turns; each measured game runs until a side wins (or, only in the runaway case, the Day cap). Games are no longer recorded as "no winner" merely because the speaking-turn budget was reached mid-Day.
  - **Acceptance Criteria:**
    - [x] Given a batch of measured games, when the run completes, then every game that would naturally resolve is recorded with a real winner — none are recorded as "no winner" due to being cut off mid-Day.
    - [x] Given a measured game that previously ended mid-Day-2 as "no winner", when re-run under this change, then it plays on to a real win/loss.

- **Hitting the Day cap is flagged as a runaway, not a legitimate result.**
  - This game has no natural draw — players always thin out to a winner — so reaching the Day cap always signals an anomaly. When it triggers, the game is recorded distinctly as an unresolved/runaway game (an attention signal), not as a normal "draw".
  - **Acceptance Criteria:**
    - [x] Given a game that somehow reaches the 12-Day cap, when it is recorded, then it is marked as a runaway/unresolved game, visibly distinct from a real Law-abiding/Mafia win.

- **The Day cap is an adjustable setting (so the change is ablatable).**
  - The Day cap is a configurable value defaulting to 12; it can be set to another value to reproduce the old behavior or explore longer games for a side-by-side comparison (per the project's ablation-flag convention).
  - **Acceptance Criteria:**
    - [x] Given the cap left at its default, when games are played, then the 12-Day safeguard applies.
    - [x] Given the cap set to a different value, when games are played, then that value governs the safeguard (for A/B comparison).

- **The effect on the town's win-rate is measured, not assumed (effort-not-results).**
  - Whether finishing games actually changes the town's win-rate is an open question this change lets us test, not a promise. A measured comparison against the recorded baseline is run and the result recorded, confirmed or refuted.
  - **Acceptance Criteria:**
    - [x] Given a measured run after this change, when its outcomes are compared with the recorded baseline (win-rate by side, share of games resolved vs unresolved), then the comparison is recorded and the hypothesis logged as confirmed or refuted — either outcome being a complete result.

---

## 3. Scope and Boundaries

### In-Scope

- A single day-denominated game-length safeguard (default 12 Days), applied consistently in real play and measured runs.
- Making measured games run to their natural conclusion — removing the fixed speaking-turn cut-off that currently ends them mid-Day as "no winner".
- Flagging a cap-triggered game distinctly as a runaway/unresolved game rather than a legitimate draw.
- Making the Day cap an adjustable, clearly day-named setting (default 12) so prior behavior is reproducible for ablation.
- Measuring the effect on the town's win-rate against the recorded baseline, under effort-not-results.

### Out-of-Scope

- **The per-Day discussion-round limit** (a Day ending after its rounds with no execution) — unchanged; this spec concerns only the whole-game Day cap.
- The **other Day-decisiveness levers** (force-a-vote / Day-termination, rule-awareness prompts) in the backlog — separate work; this is complementary.
- Changing any other gameplay rule, the vote allowance, or the win conditions themselves.
- Re-rendering or re-scoring already-recorded games; this applies to games played from this change forward.
- All other roadmap items, which are automatically out-of-scope for this specification.
