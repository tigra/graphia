# Functional Specification: Configurable Role Counts

- **Roadmap Item:** Phase 5 — Setup Flexibility → **Configurable Role Counts**. (The sibling Phase 5 item, *Multi-Round Mafia Consensus by Pointing*, is a separate spec.)
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today every game starts with the same fixed lineup — **seven players: five Law-abiding Citizens and two Mafiosos**, one of whom is the human. That makes every run structurally identical and, now that the project measures AI behaviour across batches of games (the quality-ledger work), it means every measured run is locked to one lineup — we can't study how behaviour or outcomes shift with table size or Mafia density.

This increment lets the player **choose the lineup** — how many Law-abiding Citizens and how many Mafiosos sit at the table — and then deals the roles **randomly**, so each run starts fresh. The chosen lineup is also **recorded with each measured (eval) run and shown in the ledger viewer**, so a run's results can be read in light of the table it was played on.

The lineup is set through **pre-launch configuration** — the same place the player already chooses their provider and (for dev/test) their role — so launch stays non-interactive, and an unworkable lineup is refused at startup with a clear message, consistent with the project's fail-fast-before-the-game-starts posture.

**Desired outcome:** a player can configure a table of their chosen size and Mafia density before launching, the game deals roles randomly to match, an unworkable lineup is refused up front with a plain explanation, and the lineup that was played is visible in every eval record and in the viewer.

**Success is measured by:** with a valid lineup configured, a game starts with exactly that many Citizens and Mafiosos (roles randomly assigned, including the human's); with nothing configured, the game starts with today's default five-plus-two; with an invalid lineup, the game exits at startup with a clear message and never opens the table; and an eval run's record and the viewer both show the lineup that was used.

---

## 2. Functional Requirements (The "What")

### 2.1 Choose the lineup before launch

- **As a** player, **I want** to set how many Law-abiding Citizens and Mafiosos are at the table before I launch, **so that** I can play (and measure) tables other than the fixed five-plus-two.
  - **Acceptance Criteria:**
    - [x] The lineup is set through **pre-launch configuration** (the same mechanism the player already uses to select provider / pin a role) — **not** an in-game prompt. Launch stays non-interactive.
    - [x] The two configured numbers are the **counts for the whole table**: the total Law-abiding Citizens and the total Mafiosos, **including the human** (e.g. a 5-and-2 lineup is seven players total, the human being one of them).
    - [x] Given **nothing is configured**, when the player launches, then the game uses the **current default lineup — five Law-abiding Citizens and two Mafiosos** — so existing behaviour is unchanged for anyone who sets nothing.

### 2.2 An unworkable lineup is refused at startup

- **As a** player, **I want** a clearly broken lineup rejected before the game starts, **so that** I never sit through a game that was decided before it began or crash on a typo.
  - **Acceptance Criteria:**
    - [x] A lineup is valid only when there is **at least one Mafioso** and **strictly fewer Mafiosos than Law-abiding Citizens** (otherwise the Mafia start at or above the parity that wins them the game, or there are no Mafia to find — the game is over before it starts), with at least the minimum table that rule implies.
    - [x] Given an **invalid** lineup (no Mafiosos, Mafiosos greater than or equal to Citizens, zero/negative, or non-numeric), when the player launches, then the game **exits at startup with a clear, plain-language message** naming the rule that was broken — **no game table opens and no stack trace is shown**.

### 2.3 Roles are dealt randomly to match the lineup

- **As a** player, **I want** roles assigned at random each run, **so that** every game starts fresh rather than with a predetermined seating.
  - **Acceptance Criteria:**
    - [x] Given a valid lineup, when the game starts, then exactly that many Citizens and Mafiosos are in play and **each player's role — including the human's — is assigned at random** within those counts; the human is not guaranteed any particular side.
    - [x] The existing win conditions apply unchanged, now relative to the configured counts: Law-abiding win when all Mafiosos are eliminated; Mafia win when their number reaches the number of Law-abiding players.

### 2.4 The lineup is recorded and visible in measurement

- **As the** maintainer, **I want** each eval run to record and the ledger viewer to show the lineup it was played on, **so that** a run's behaviour rates and outcomes can be read in light of the table size and Mafia density.
  - **Acceptance Criteria:**
    - [x] An eval run's recorded result includes the **lineup it used** (the count of each role).
    - [x] The ledger viewer **displays the lineup for each run** (at least in the full-record drill-down, and surfaced in the table where it fits), alongside the existing run facts.

---

## 3. Scope and Boundaries

### In-Scope

- **Pre-launch configuration** of the table's Law-abiding-Citizen and Mafioso counts (whole-table, human included), with a documented **default** (today's five-plus-two) when unset.
- **Startup validation** of the lineup with a **fail-fast exit and a clear message** on anything unworkable (no Mafia, Mafia ≥ Citizens, zero/negative, non-numeric).
- **Random role assignment** (including the human's) to match the configured counts, fresh each run; win conditions unchanged but relative to the counts.
- **Recording the lineup** in each eval run's result and **showing it in the ledger viewer**.

### Out-of-Scope

- **Interactive in-game prompts** for the counts — the lineup is set through pre-launch configuration instead.
- **The human choosing their own side** at startup — the human's role stays randomly assigned within the counts; the existing behind-the-scenes role-pin (a dev/test affordance) is preserved as-is, not surfaced as a player choice.
- **More than two roles** — exactly Law-abiding Citizen and Mafioso, as today; no new roles.
- **Multi-Round Mafia Consensus by Pointing** — the sibling Phase 5 item; its own spec. (Note: it becomes more meaningful once tables routinely have several Mafiosos, which this spec enables.)
- **All other roadmap items** (Phase 6 Personas & Async Day Chat; Phase 7 Tool-Use & Expanded Roles) — each its own spec.
