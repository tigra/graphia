# Functional Specification: Scripted-Player's-Side Win Rate in Evals

- **Roadmap Item:** Eval-measurement metric — the headline KPI for the **Active Scripted Player (spec 026)** experiment; relates to the **Town-coordination / Day-decisiveness** thread. Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

A measured (eval) run already records a win rate **by side** (how often the Law-abiding won; how often the Mafia won). With the active scripted stand-in (spec 026) now playing a real side — and run per-side for clean attribution (a Law-abiding batch, a Mafia batch) — the question that actually matters for the experiment is: **did the side the scripted stand-in was on win?** Today answering that means knowing which side the stand-in was pinned to and then reading the matching by-side rate by hand; and a Law-abiding batch and a Mafia batch can't be compared with a single number.

This change records, in each measured run, the **win rate of the scripted stand-in's own side** — the fraction of the run's games in which the side the stand-in was on won — as a first-class result with the same reliability band (confidence interval) the other rates carry, labeled with which side that was. It reads directly as "the scripted player's side won X%," and it is the **one comparable number** across a Law-abiding batch and a Mafia batch — the natural way to read whether the active policy helps its own side (the spec-026 effort-not-results question).

It is **pure measurement** — nothing about how a game plays changes; this only adds a recorded number. It complements (does not replace) the existing by-side rates, and it is meaningful for the passive stand-in too (it would simply report the passive side's near-zero win rate).

**Success looks like:** every measured run reports, in its summary and its recorded result, the scripted stand-in's-side win rate with its confidence band and the side it refers to; the existing by-side rates remain; and older records that predate this metric still read cleanly.

---

## 2. Functional Requirements (The "What")

- **Each measured run records the win rate of the scripted stand-in's own side.**
  - Alongside the existing by-side win rates, the run records the **scripted-side win rate** — the fraction of the run's games in which the side the scripted stand-in was on won — with the same confidence band the other rates use, and a label of **which side** that was (Law-abiding or Mafia).
  - **Acceptance Criteria:**
    - [ ] Given a completed measured run, when its result is read, then it includes the scripted-side win rate, its confidence band, and the side the stand-in was on.
    - [ ] Given a run where the stand-in was Law-abiding, then the recorded scripted-side win rate equals the share of games the Law-abiding side won; given a Mafia stand-in, it equals the share the Mafia side won.

- **The rate is counted over all games, with a no-result game counting as a non-win.**
  - The rate is scripted-side wins divided by **all** games in the run; a game that ended with no real winner (no winner, or the runaway safeguard) counts as a **non-win** for the scripted side — exactly how the existing by-side rates are counted. It is computed per game (so it stays correct even if a run's stand-in side were to vary game to game).
  - **Acceptance Criteria:**
    - [ ] Given a game the scripted stand-in's side won, when the rate is computed, then that game counts as a win.
    - [ ] Given a game that ended with no winner or at the runaway cap, when the rate is computed, then that game counts toward the total but not as a win.

- **The metric is surfaced in the run summary and complements the existing by-side rates.**
  - The scripted-side win rate appears in the run summary as well as the recorded result; the existing Law-abiding and Mafia by-side rates remain unchanged.
  - **Acceptance Criteria:**
    - [ ] Given a finished run, when the summary is shown, then the scripted-side win rate (with its side) appears in it.
    - [ ] Given the recorded result, then the existing by-side Law-abiding and Mafia rates are still present.

- **Older records without this metric still read cleanly.**
  - Records produced before this metric existed simply omit it; reading them (including in the ledger viewer) does not break, and they are not retro-filled.
  - **Acceptance Criteria:**
    - [ ] Given a record that predates this metric, when it is opened in the ledger viewer, then it displays without error and without the scripted-side field.

- **Pure measurement — no change to how a game plays.**
  - Adding/recording this metric changes nothing about gameplay or outcomes; it only reports an additional number.
  - **Acceptance Criteria:**
    - [ ] Given the same games, when they are played with and without this metric recorded, then the game outcomes themselves are identical — only the recorded result gains the new number.

---

## 3. Scope and Boundaries

### In-Scope

- Recording, per measured run, the **scripted stand-in's-side win rate** (with its confidence band and the side label), counted over all games with no-result games as non-wins, computed per game.
- Surfacing it in the run summary and the recorded result, alongside (not replacing) the existing by-side rates.
- Reading older records that lack the metric without error.

### Out-of-Scope

- The scripted stand-in's **policy / behavior** itself — that is spec 026; this spec only *measures* the outcome of whichever side it plays.
- Changing the existing **by-side win rates** or any other recorded metric (the vote-activity-by-side and engagement metrics are separate backlog items).
- **Re-scoring or retro-filling** games already recorded.
- Changing any **game rule, win condition, or behavior** — this is measurement only.
- All other roadmap items, which are automatically out-of-scope for this specification.
