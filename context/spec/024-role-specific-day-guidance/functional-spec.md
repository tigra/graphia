# Functional Specification: Role-Specific Day Guidance for AI Players

- **Roadmap Item:** Day-decisiveness follow-up — realizes the **rule-awareness / situation-recognition lever** explicitly parked from Phase 6's *Recap-Driven Day Decisiveness* (whose note reads "the complementary force-a-vote and rule-awareness levers stay in the backlog"); relates to the **Town-coordination / Day-decisiveness** thread. Not a distinct roadmap phase item.
- **Status:** Completed *(verified 2026-06-23 — effort-not-results measurement recorded in the 2026-06-22 ledger runs; [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md))*
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The AI town has never won a measured game — **0/N on both providers**. The spec-019 change already hands each AI player an accurate, standing read of the game ("N Law-abiding vs M Mafia remain, votes called today, who was executed"); the spec-023 verification then confirmed that letting games run to their natural end drives `no_winner` from 70% to **0%** — but the town still wins **0/10** and the Mafia now win **100%**. So the town isn't losing because games are cut short; it's losing because **the AI players don't act on what they're told**: Law-abiding players drift, comment on the dead, rarely name a suspect or call a vote, and the Mafia pick them off uncontested.

Today the AI's Day prompt names its role and win condition, but its **closing instruction** — the last and most salient thing it reads before acting — is generic and **identical for both sides**: "take your turn — speak, or call a vote." It never tells a player, in concrete terms, what its *side* should be doing right now.

This change adds a strong, **role-specific** guidance block at the **end** of an AI player's Day prompts (both its speaking turn and its vote), spelling out the **concrete plays** available to its side:

- **A Law-abiding Citizen** is reminded the town wins **only** by executing Mafiosos, and is given the concrete moves: watch for a likely Mafioso, voice that suspicion / accuse them openly, and put a genuine suspect up for a vote-to-execute before the Day ends — while taking care **not** to get fellow Law-abiding Citizens executed.
- **A Mafioso** is directed to stay behind its cover persona, cast suspicion onto Law-abiding Citizens, protect and quietly coordinate with fellow Mafiosos, and steer votes toward Citizens and away from the Mafia — **never** revealing its role, its teammates, or that its persona is a front.

This composes with — does not replace — the existing role grounding and the standing situational summary: those give the player the *facts*; this tells it, by role, *what to do about them*.

**Success looks like:** at each Day decision, an AI player is told in plain, role-specific terms what its side is trying to achieve and the concrete actions to get there; and a measured run records **whether** this lifts town engagement (suspicions raised, votes initiated) and decisiveness — without merely trading inaction for Law-abiding players executing each other. Under the effort-not-results principle ([CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)), a confirmed **or** refuted result is a complete outcome.

---

## 2. Functional Requirements (The "What")

- **A Law-abiding Citizen receives concrete, action-oriented guidance to advance the town.**
  - At its Day speaking turn and at its vote, a Law-abiding Citizen is given the concrete plays for its side: the town wins **only** by executing Mafiosos; so watch for a likely Mafioso, name/accuse the suspicion in the open, and put a genuine suspect up for a vote-to-execute before the Day ends — while being careful not to get fellow Law-abiding Citizens executed (don't accuse without a reason). The guidance lists actions to take, rather than simply ordering the player to vote.
  - **Acceptance Criteria:**
    - [x] Given a Law-abiding Citizen's Day turn, when it decides, then its closing guidance names concrete town actions — identify a suspected Mafioso, voice/accuse that suspicion, and call a vote on a genuine suspect — together with the caution against getting fellow Law-abiding Citizens executed.
    - [x] Given a Law-abiding Citizen who holds a specific suspicion, then the guidance directs turning it into an open accusation and/or a vote, rather than passive commentary on the dead or the situation.
    - [x] Given a Law-abiding Citizen with no real lead, then the guidance does **not** push a baseless accusation — it gathers information instead.

- **A Mafioso receives concrete guidance to win by deception.**
  - At its Day speaking turn and at its vote, a Mafioso is given the concrete plays for its side: maintain the public cover persona, cast suspicion onto Law-abiding Citizens, protect and quietly coordinate with fellow Mafiosos, and steer votes toward Citizens and away from the Mafia — all under a standing rule never to reveal its role, its teammates, or that the persona is a front.
  - **Acceptance Criteria:**
    - [x] Given a Mafioso's Day turn, when it decides, then its closing guidance names concrete Mafia actions — deflect suspicion onto a Law-abiding Citizen, defend or avoid exposing a teammate, and push a vote toward a Citizen.
    - [x] Given a Mafioso, then the guidance never instructs it to disclose its side, name its teammates, or drop its cover.

- **The guidance is matched to the player's own role and sits at the close of the prompt.**
  - Each player sees only the directive for its own secret role (Mafioso *or* Law-abiding Citizen), never the other side's; it appears as the **final** guidance at decision time, complementing the existing role grounding and the standing situational summary rather than replacing them.
  - **Acceptance Criteria:**
    - [x] Given any AI player, when it takes a Day action, then the closing directive matches its true role and contains no guidance written for the other side.
    - [x] Given the existing role grounding and the standing situational summary, when this change is in effect, then both remain present and the role-specific directive is added at the end.

- **The guidance applies to both Day decision points.**
  - The role-specific closing directive is present both when the player speaks and when it casts a vote — the two points at which a Day decision is actually made.
  - **Acceptance Criteria:**
    - [x] Given a Law-abiding Citizen and a Mafioso, when each takes a speaking turn **and** when each casts a vote, then the role-specific closing directive is present in all four cases.

- **The role-specific guidance is an adjustable setting (so the change is ablatable).**
  - The guidance can be turned off to reproduce the prior behavior for a side-by-side comparison; it is on by default (per the project's ablation-flag convention, [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md)).
  - **Acceptance Criteria:**
    - [x] Given the setting left at its default, when AI players take Day actions, then they receive the role-specific guidance.
    - [x] Given the setting turned off, when AI players take Day actions, then the Day prompts revert to their prior generic closing instruction (the pre-change baseline, for A/B).

- **The effect is measured, not assumed (effort-not-results).**
  - Whether concrete role guidance actually lifts the town's engagement and decisiveness is an open question this change lets us test, not a promise. A measured comparison against the recorded baseline is run and recorded, confirmed or refuted.
  - **Acceptance Criteria:**
    - [x] Given a measured run after this change, when its outcomes are compared with the recorded baseline — win-rate by side, **votes initiated**, share of games **resolved vs `no_winner`**, and a watch on **Law-abiding-executed-by-Law-abiding** (so a lift in decisiveness isn't bought by citizens executing each other) — then the comparison is recorded and the hypothesis logged as confirmed or refuted, either being a complete result.

---

## 3. Scope and Boundaries

### In-Scope

- A strong, role-specific guidance block at the **close** of an AI player's Day speaking-turn **and** vote prompts, differentiated for a Mafioso vs a Law-abiding Citizen, each listing the concrete actions available to that side.
- Keeping the existing role grounding and standing situational summary — this guidance is **additive**.
- Making the role-specific guidance an adjustable, default-on setting so prior behavior is reproducible for ablation.
- Measuring the effect on town engagement / decisiveness against the recorded baseline, under effort-not-results.

### Out-of-Scope

- The **mechanical force-a-vote / Day-termination** lever (auto-nominate the most-discussed living player when the Day stalls) — the complementary backlog Day-decisiveness lever; separate work, and this is prompt-level, not flow-level.
- **Night pointing / the Mafia Night prompt** — this change is the **Day** decision surface only (the speaking turn and the vote).
- The **human** player — this guidance is for AI players; the human decides for themselves.
- **Per-AI Day-round private thoughts / private diaries** (Phase 6 roadmap items) — a separate per-AI reasoning channel.
- Changing any win condition, the vote allowance, or any other game rule.
- Re-scoring or re-running games already recorded in the ledger.
- The **wording of personas, recaps, or Moderator text** — only the AI players' own closing Day guidance changes.
- All other roadmap items, which are automatically out-of-scope for this specification.
