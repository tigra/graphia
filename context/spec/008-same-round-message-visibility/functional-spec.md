# Functional Specification: Same-Round Message Visibility

- **Roadmap Item:** Not a roadmap feature — a Day-phase integrity guarantee (with one supporting behavior change), requested ad hoc. Companion to [007 — Fair Day Speaking Order](../007-fair-day-speaking-order/functional-spec.md). (Roadmap order unaffected; Phase 4 remains next.)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

In the Day phase, players speak in turn, and the point of taking turns is that **later speakers react to what earlier players said** — accusing, defending, building on suspicion. That only works if a speaker is actually shown the earlier messages from the current round. (This is also the information advantage that makes speaking *order* matter — see companion spec 007.)

Today a speaker is shown only a small slice of the most recent discussion (the last ~10 messages). Once the day's opening announcement and a full round of up to seven speeches are counted, that slice can be **too small to cover the whole round**, so the earliest speakers of a round can fall out of view before later players respond to them.

**Desired outcome:** a speaker reliably sees the prior messages from the current round — for both AI players and the human — and this is locked in by automated tests. To make a full round fit comfortably, the amount of recent discussion shown is increased to the **last 30 messages**.

**Success is measured by:** the standard automated test suite confirms (offline) that an AI player's speaking turn is given the earlier same-round messages, that the human sees them on screen before their turn, and that the shown extent is large enough to cover a full round — and these tests fail if visibility regresses.

---

## 2. Functional Requirements (The "What")

### 2.1 A speaker sees the current round's earlier messages

- **As a** player taking my turn, **I want** to see what others said earlier this round, **so that** I can respond to the live discussion.
  - **Acceptance Criteria:**
    - [ ] When an AI player takes its speaking turn, the messages other players submitted earlier in the same round are included in what the AI is given to respond to.
    - [ ] When it is the human's turn, the messages submitted earlier in the same round are visible in the on-screen discussion before the human speaks.
    - [ ] A message from an earlier speaker this round is present for a later speaker in the same round (verified at a representative later position, for both an AI and the human).

### 2.2 Enough recent discussion is shown to cover a full round

- **As a** player, **I want** the shown discussion to be large enough that I don't miss earlier speakers from this round.
  - **Acceptance Criteria:**
    - [ ] The amount of recent discussion shown to a speaker is the **last 30 messages** (increased from the previous smaller amount).
    - [ ] For a normal-sized game, this is enough to include **every** earlier speaker from the current round (a full round of speeches plus the day's opening announcement fits within the shown discussion).
    - [ ] Messages older than the shown extent (e.g. from several rounds earlier) are **not guaranteed** to be visible — this is an acknowledged limit, not a defect.

### 2.3 The visibility is proven by automated tests

- **As the** project maintainer, **I want** this locked in by tests in the standard suite, **so that** a future change can't silently stop showing players the round's discussion.
  - **Acceptance Criteria:**
    - [ ] A test drives a Day round and confirms a later AI speaker is given an earlier same-round speaker's message.
    - [ ] A test confirms the human's on-screen discussion shows earlier same-round messages before the human's turn.
    - [ ] A test confirms the shown extent covers a full round for the standard lineup.
    - [ ] The tests run as part of the normal test command and reach no external or cloud service.

### 2.4 No other change to how the Day plays

- **As a** player, **I want** the Day to behave as before apart from seeing more of the discussion.
  - **Acceptance Criteria:**
    - [ ] Apart from showing more recent discussion, the Day plays as today — same turn flow, prompts, number of rounds, vote rules.
    - [ ] No new on-screen control or setting is introduced.

---

## 3. Scope and Boundaries

### In-Scope

- Ensuring — and testing — that a speaker (AI **and** human) sees the current round's earlier messages during the Day phase.
- Increasing the amount of recent discussion shown to a speaker to the **last 30 messages**. This also widens the recent discussion an AI is shown when it considers a vote (the same recent-discussion view).

### Out-of-Scope

- **Fairness/impartiality of the speaking order** — companion spec [007](../007-fair-day-speaking-order/functional-spec.md).
- **Night-phase** visibility or Mafia private communication.
- Showing the **entire game history** to a speaker — only the recent extent (30 messages) is guaranteed.
- **Persisting or replaying** discussion across games.
- **Other roadmap items** (Phase 4 AI Provider Flexibility; Phase 5 Configurable Role Counts & Multi-Round Mafia Consensus; Phase 6 Personas & Async Day Chat; Phase 7 Tool-Use & Expanded Roles) — each its own spec.
