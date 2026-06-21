# Functional Specification: Randomized Night-Pointing Roster Order

- **Roadmap Item:** Gameplay-fairness + eval-validity fix (the roster-primacy bug found in the spec-026/027 Nova run); relates to the **Town-coordination / Day-decisiveness** thread. Not a distinct roadmap phase item.
- **Status:** Draft — implementation verified (all deterministic-shuffle criteria `[x]`); the measured-debias criterion (§2, "first-position advantage is removed") is `[?]`, pending the deferred `make blunder-eval` run. Reaches Completed once that eval is logged.
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

During the Night, the Mafia are shown the list of living Law-abiding players and pick one to eliminate. Today that list is always in the **same fixed order, with the same player first** — and on the first Night there is no discussion or behavior to reason from, so the choice collapses to **position**: the Mafia disproportionately pick the **first-listed** player.

This was caught in the spec-026/027 Nova run. The automated stand-in (the human's seat) is always created first, so it is always listed first; across 20 measured games it was killed on **Night 1 in ~50%** of the games it was Law-abiding — versus the ~1-in-6 (≈17%) a fair process would give any one Citizen. That over-targeting has two costs:

- **Live-play unfairness** — a real human player is singled out and removed early, through no choice of their own.
- **Eval validity** — the active scripted seat is killed *before it can act* in about half its games, which biases the 026/027 results **low** (the lone town win, game-14, happened *after* the seat had already died Night 1).

This change presents the living Law-abiding candidates to the Mafia in a **randomized order each Night**, so no seat holds a permanent position advantage or disadvantage. It uses the game's existing mechanical randomness — the same kind that already shuffles the Day speaking order — so the order **varies run-to-run yet is reproducible under a fixed eval seed**, keeping seeded comparisons stable. It is **ablatable** (on by default; a toggle reproduces the old fixed order) so we can A/B and **measure** how much the positional bias was distorting outcomes (per the ablation-flag convention, [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md)).

**Success looks like:** the Mafia's Night candidate list is shuffled each Night (the same set of candidates, only the order differs); no seat is consistently first; over many games the first-created seat (the human's) is no longer killed Night 1 more often than chance; the old fixed order is reproducible via the toggle; and nothing else about the kill — who is eligible, the agreement mechanic, the win conditions — changes.

---

## 2. Functional Requirements (The "What")

- **The Mafia's Night candidate list is presented in a randomized order each Night.**
  - The living Law-abiding players the Mafia choose from are listed in a shuffled order; no player holds a fixed position from Night to Night or game to game.
  - **Acceptance Criteria:**
    - [x] Given the same set of living Law-abiding players across repeated Nights/games, when the Mafia are shown the candidate list, then the order they appear in varies (not always the same player first).
    - [x] Given a fixed eval seed, when a game is replayed, then the Night candidate order is reproduced identically (so seeded comparisons stay stable).

- **Randomizing the order changes only the presentation, not who is eligible or how the kill resolves.**
  - The candidate list contains exactly the living Law-abiding players (none added or removed); the Mafia still pick exactly one; the way the Mafia converge on a shared target and the resulting kill are unchanged — only the order differs.
  - **Acceptance Criteria:**
    - [x] Given the living Law-abiding players, when the randomized list is shown, then it contains exactly those players — none missing, none extra.
    - [x] Given a completed Night, when the kill resolves, then it resolves by the same rules as before (the chosen target is one of the listed candidates).

- **The first-position advantage is removed.**
  - No seat is systematically targeted on Night 1 because of where it sits in the list; over many games the first-created seat (the human's) is killed Night 1 at roughly the chance rate, not far above it.
  - **Acceptance Criteria:**
    - [?] Given many measured games with the randomization on, when Night-1 targets are tallied, then the first-created seat's Night-1 death rate (when it is Law-abiding) is consistent with chance rather than the ~50% seen before. _(verification pending — deferred measured-debias eval; deterministic shuffle behaviour all unit-verified.)_

- **The randomization is an adjustable setting (ablatable).**
  - It is on by default; a setting reproduces the old fixed (first-created-first) order for a side-by-side comparison, so the impact of the bias can be measured (per ADR 011).
  - **Acceptance Criteria:**
    - [x] Given the setting at its default, when the Mafia are shown the candidate list, then the order is randomized.
    - [x] Given the setting turned off, when the Mafia are shown the candidate list, then it reverts to the prior fixed order (for A/B).

- **Reproducible under a seed (determinism posture).**
  - With a fixed eval seed the shuffled order is deterministic and reproducible (like the existing shuffled speaking order); with no seed it varies freely.
  - **Acceptance Criteria:**
    - [x] Given a fixed seed, when two runs play the same game, then the Night candidate orders match.
    - [x] Given no seed, when games are played, then the order varies across games.

---

## 3. Scope and Boundaries

### In-Scope

- Randomizing the **order** of the living Law-abiding candidates presented to the Mafia for **Night pointing**, on by default.
- An adjustable toggle that reproduces the prior fixed order for A/B, with seeded reproducibility preserved.

### Out-of-Scope

- The **Day roster shown to all players** (the alive-players list in the Day-speech and vote prompts) — it has the *same* first-position bias and would benefit from the same fix, but it is a **separate, broader surface** (shown to everyone, not just the Mafia) and is left to its own follow-up spec.
- Changing the **candidate set**, the **kill/agreement mechanic**, or the **win conditions** — only the presentation order changes.
- The Mafia's **strategy or prompt wording** beyond the order of the candidate list.
- Re-scoring games already recorded (this applies to games played from this change forward).
- All other roadmap items, which are automatically out-of-scope for this specification.
