# Functional Specification: Multi-Round Mafia Consensus by Pointing

- **Roadmap Item:** Phase 5 — **Richer Night Resolution** → **Multi-Round Mafia Consensus by Pointing**. (The sibling Phase 5 item, *Configurable Role Counts*, shipped as [Spec 014](../014-configurable-role-counts/functional-spec.md).)
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today every Night resolves in a single round: each Mafioso points once at a Law-abiding target, the most-pointed-at player dies, and a tie is broken at random. That is the deliberately-simplest Phase 1 rule, and it means the Mafiosos never actually *coordinate* — two Mafiosos can split their picks and a coin-flip decides the kill. That reads as arbitrary, and it skips the most interesting beat of the Night: a secret team trying to agree on who dies.

This increment turns the Night kill into a real act of **team consensus**. The Mafiosos point in turn over a small number of rounds, each one able to see the choices their teammates have already made, and try to converge on a single victim. When they all land on the same target, that target dies and the Night ends — no wasted rounds. Only if they are still split after the round cap does the game fall back to today's majority-with-random-tie-break, so the Night always resolves and never stalls.

It is a small mechanic with an outsized payoff: it makes the Mafia feel like a coordinating cell rather than independent dice, and it gives a human Mafioso a genuine "read where my teammates are leaning and steer the kill" moment that the single-round rule never allowed.

**Desired outcome:** on a Night with two or more Mafiosos, they iterate private pointing rounds, visibly moving toward one target, and kill that target the moment they agree — with a graceful fallback that still produces exactly one victim when they cannot.

**Success is measured by:** with two or more Mafiosos alive, a Night that reaches agreement kills the agreed target and plays no more rounds than it needed; a Night where they stay split for the full cap still resolves to a single victim by majority (random tie-break) and the morning proceeds normally; a lone surviving Mafioso's pick is the victim immediately; and nothing a Law-abiding player (including a Law-abiding human) sees about the Night changes.

---

## 2. Functional Requirements (The "What")

### 2.1 The Mafiosos converge on a victim over several rounds

- **As the** Mafia team, **we want** to point at a target over several rounds and agree on one victim, **so that** the Night kill reflects our coordination rather than a coin-flip.
  - **Acceptance Criteria:**
    - [x] Given two or more Mafiosos are alive, when the Night kill begins, then the living Mafiosos point at a living Law-abiding target **in turn, in a fresh random order each round** — the same fair-rotation pattern as the Day speaking order ([Spec 007 — Fair Day Speaking Order](../007-fair-day-speaking-order/functional-spec.md)); a round is complete once every living Mafioso has pointed once.
    - [x] The pointing order is **re-randomized every round** rather than fixed, so the last-to-point position (the pointer who, under the unanimity rule, sees all the others' picks before choosing) falls to whoever the random order places last that round, not systematically to the same Mafioso. This is a fair shuffle, not a guaranteed rotation — a Mafioso may, by chance, point last on more than one round.
    - [x] When, at the end of a round, **every living Mafioso has pointed at the same target**, then that target becomes the victim and **no further rounds are played** (agreement reached).
    - [x] When a round ends **without** all living Mafiosos on the same target, and the round cap has not been reached, then another round of pointing is played.
    - [x] The Mafiosos point with **no chat and no free-text discussion** — pointing is the only communication between them.

### 2.2 Each Mafioso sees the choices already made

- **As a** Mafioso, **I want** to see my teammates' choices before I point, **so that** I can move toward a shared target.
  - **Acceptance Criteria:**
    - [x] Given it is a Mafioso's turn to point within a round, when they are asked to point, then they can see **each teammate's pick that has already been made** — both from earlier rounds and from earlier in the current round — shown **by teammate name** (the Mafiosos already know one another from the first-Night introductions).
    - [x] The very first Mafioso to point in the first round has no prior picks to see and simply points; from then on, every pointer sees the running set of choices made before their turn.
    - [x] **No player outside the Mafia** sees any of this pointing, the choices, or the rounds.

### 2.3 A guaranteed single victim when they can't agree

- **As the** game, **I want** a single victim even when the Mafia stay split, **so that** the Night always resolves and never stalls.
  - **Acceptance Criteria:**
    - [x] The Mafiosos get **up to three rounds** to reach agreement.
    - [x] Given the Mafiosos have played the full three rounds **without** all pointing at the same target, when the final round ends, then the victim is decided by **majority of that final round's picks** (the most-pointed-at target) — exactly today's single-round rule, applied to the last round.
    - [x] Given that final round's majority is **tied** between two or more players, when the victim is decided, then **one of the tied targets is chosen at random**.
    - [x] In all cases **exactly one** living Law-abiding player is killed whenever a target exists (the Mafia always kill on a Night with a valid target; there is no "skip the kill").

### 2.4 The lone-Mafioso and human cases

- **As a** human Mafioso, **I want** to take my turn in each round seeing what my teammates have chosen, **so that** I can steer the kill.
  - **Acceptance Criteria:**
    - [x] Given the human is a Mafioso, when it is their turn to point in a round, then they are shown the picks made so far (by teammate name) and choose a living Law-abiding target — **exactly as the AI Mafiosos do**, taking their turn at whatever position that round's random order places them (so on some rounds they point early and on others late).
    - [x] The human Mafioso participates in **each** round until agreement is reached or the cap ends the pointing.
    - [x] Given **only one Mafioso is alive**, when the Night kill begins, then **that single pick is the victim immediately**, with no further rounds (a lone Mafioso trivially agrees with themselves).

### 2.5 Nothing else about the Night changes

- **As a** Law-abiding player (including the human), **I want** the Night and morning to look exactly as they do today, **so that** the consensus change stays the Mafia's private business.
  - **Acceptance Criteria:**
    - [x] Given the human is **not** a Mafioso, when the Night happens, then they see only the existing brief "night falls" / morning messages — no sign of the rounds or the choices.
    - [x] When the victim is decided (whether by agreement or by fallback), then the **morning announcement names who was killed exactly as today**.
    - [x] The human's career **night-kill statistics keep their current meaning**: a human Mafioso's participation counts as **one kill attempt** for the Night regardless of how many rounds were played, and counts as a **success** when the victim is the target the human pointed at in the **deciding round** (the round that produced the victim).

---

## 3. Scope and Boundaries

### In-Scope

- **Multi-round private pointing** among the living Mafiosos: in turn, in a **fresh random order each round** (the Day-speaking-order fair-rotation pattern), each pointer seeing the choices already made (by name), converging on a single victim by **unanimous agreement**.
- A **round cap of three**, with a **fallback** to the most-pointed-at target of the **final round** (random tie-break) when agreement is not reached.
- The **lone-Mafioso** trivial case (immediate pick), and the **human Mafioso** taking their turn each round with the same visibility as the AI.
- Preserving the existing **morning announcement**, the existing **no-target / no-kill** handling, and the **human's night-kill career-stat** semantics.

### Out-of-Scope

- **Any chat or free-text discussion** among the Mafiosos — pointing is the only communication.
- **Changing what non-Mafia players see**, or revealing the consensus process at end-of-game (the creative recap that draws on night-kill logs is the Phase 6 **End-of-Game Payoff** item).
- A **configurable number of rounds** — the cap is fixed at three for this increment.
- The **Day-phase vote-to-execute** (unchanged), the **first-Night teammate introductions** (already shipped in Phase 1), and **per-AI private diaries** (Phase 6).
- **All other roadmap items:** AI Personas & Per-Game Memory, Asynchronous Day Chat, End-of-Game Payoff (Phase 6); AI Tool-Use Demonstration and Expanded Role Roster (Phase 7).
