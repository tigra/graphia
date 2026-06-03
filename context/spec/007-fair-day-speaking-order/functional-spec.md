# Functional Specification: Fair Day Speaking Order (Role- and Player-Type-Independent)

- **Roadmap Item:** Not a roadmap feature — a quality/integrity guarantee on the existing Day phase, requested ad hoc. (Roadmap order is unaffected; Phase 4 remains the next feature.)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

During the Day phase, players take turns speaking. **The order in which they speak is a real advantage in a social-deduction game** — speaking first lets a player frame the day's suspicion; speaking last lets them react to what everyone else has said. That later advantage is concrete, not theoretical: a player who speaks later in a round can see the messages earlier players submitted in that same round, so position carries an information edge (the visibility behavior itself is the subject of a companion spec, [008 — Same-Round Message Visibility](../008-same-round-message-visibility/functional-spec.md)). Because position confers advantage, the order must never systematically favor a particular *role* (Mafia vs Law-abiding) or favor the *human* over the AI players (or vice-versa).

Today the order is drawn impartially, but **nothing guarantees it stays that way** — a future change could quietly introduce a bias (e.g. always seating the human last, or letting Mafia open), and there is no test that would catch it.

**Desired outcome:** the day speaking order is decided impartially — every surviving player is equally likely to occupy any speaking slot, regardless of role or whether they are human or AI — and that impartiality is permanently enforced by automated tests so any future regression fails the build.

**Success is measured by:** the standard automated test suite includes tests that (a) prove the order is decided without reference to role or player-type, and (b) confirm over many simulated games that every role and every player-type lands in every position about evenly — and these tests fail loudly if the order ever becomes biased.

---

## 2. Functional Requirements (The "What")

### 2.1 The day speaking order is impartial

- **As a** player, **I want** the speaking order to never favor any role or favor human vs AI, **so that** no one gains an unfair advantage from *when* they speak.
  - **Acceptance Criteria:**
    - [ ] Each still-alive player is equally likely to occupy any given speaking position (the order is a uniformly random arrangement of the living players).
    - [ ] No role is favored: a player's chance of landing in any position does not depend on whether they are Mafia or Law-abiding.
    - [ ] The human player has the same chance of any position as any comparable AI player — being the human neither advances nor delays their turn.
    - [ ] This holds for **every** position, not only who speaks first.
    - [ ] This holds on **every** speaking round within a day, including after the order is freshly drawn part-way through a day.
    - [ ] As players are eliminated, the impartiality continues to hold among the players who are still alive.

### 2.2 The guarantee is proven by automated tests

- **As the** project maintainer, **I want** this impartiality locked in by automated tests in the standard suite, **so that** any future change that introduces bias is caught immediately.
  - **Acceptance Criteria:**
    - [ ] A deterministic test confirms that changing **only** the players' roles (everything else identical) does not change which speaking orders are possible or how likely they are.
    - [ ] A deterministic test confirms that changing **only** whether a player is human or AI does not change which speaking orders are possible or how likely they are.
    - [ ] A large-sample test draws the speaking order many times and confirms each role and each player-type appears in each position within an accepted tolerance of an even spread.
    - [ ] These tests run as part of the normal test command and fail loudly if the order ever becomes role- or player-type-dependent.
    - [ ] The tests reach no external or cloud service (they run fully offline).

### 2.3 No change to how the Day looks or plays

- **As a** player, **I want** the Day to behave exactly as before, **so that** this is purely a safety guarantee, not a feature change.
  - **Acceptance Criteria:**
    - [ ] The Day phase plays as it does today — same prompts, same turn flow, same number of rounds and vote rules.
    - [ ] No new on-screen text, setting, or control is introduced.

---

## 3. Scope and Boundaries

### In-Scope

- The order in which players take their **speaking turn during the Day phase**.
- Impartiality with respect to **role** (Mafia / Law-abiding) and **player type** (human / AI), across all positions, all rounds within a day, and the surviving subset as players are eliminated.
- Automated tests — a structural guarantee plus a large-sample sanity check — that enforce this in the standard suite.

### Out-of-Scope

- **What a speaker can see when it is their turn** (the same-round message-visibility behavior) — covered by the companion spec [008 — Same-Round Message Visibility](../008-same-round-message-visibility/functional-spec.md).
- **Night-phase ordering** — Mafia pointing/consensus order or kill resolution. This spec is Day-speaking-order only.
- **Vote/ballot ordering and tie-break** behavior (e.g. who votes in what order, random tie-breaks).
- Any change to **who may speak, how many rounds occur, or the voting rules**.
- Making the order **reproducible/seeded** across runs — the order stays naturally random run-to-run; the project's determinism posture is unchanged.
- **Other roadmap items**, each its own spec: Phase 4 (AI Provider Flexibility), Phase 5 (Configurable Role Counts; Multi-Round Mafia Consensus), Phase 6 (AI Personas & Per-Game Memory; Async Day Chat), Phase 7 (AI Tool-Use; Expanded Roles).
