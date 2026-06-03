# Functional Specification: AI Collusion Awareness (Copycat Messages Are Suspicious)

- **Roadmap Item:** Not a roadmap feature — a small enrichment of AI Day-phase behavior, requested ad hoc. Builds on [008 — Same-Round Message Visibility](../008-same-round-message-visibility/functional-spec.md). (Roadmap order unaffected; Phase 4 remains next.)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

In a social-deduction game, two players posting **identical or near-identical statements** is a tell — it can mean they're coordinating (colluding). A perceptive villager would notice and treat it with suspicion. Today the AI villagers are told to be "nervous, observant" but are given **no hint** that copycat messages are meaningful, so they tend to ignore the signal.

This change adds a **light awareness line** to the guidance the AI players receive during the Day: that identical or very similar messages from different players can be a suspicious sign of collusion. It is deliberately gentle — the AI is made *aware* of the signal but is **not** told to react in any particular way. Whether an AI then avoids parroting others, calls out a copycat, or simply weighs it privately is left to emerge naturally.

This rests on the visibility delivered by spec 008: an AI can only notice copycatting because it already sees the other players' recent messages when it speaks. Spec 008 provides the eyes; this spec provides a light interpretive nudge.

**Desired outcome:** Day discussion feels a notch more perceptive — copycatting carries a little risk because some AI may find it suspicious — while the variety of AI behavior stays natural rather than scripted.

**Success is measured by:** in play-testing, the AI villagers' Day guidance reflects the collusion signal, and AI dialogue is observed to occasionally treat near-identical messages with suspicion — without any forced or repetitive phrasing. There is **no automated test** for this behavior (see §2.3); it is confirmed by playing the game.

---

## 2. Functional Requirements (The "What")

### 2.1 AI players are made aware that copycat messages can signal collusion

- **As a** player, **I want** the AI villagers to treat suspiciously-similar messages as a possible collusion tell, **so that** the Day discussion feels more perceptive and copycatting carries some risk.
  - **Acceptance Criteria:**
    - [ ] The guidance given to AI players during the Day includes the idea that identical or near-identical messages from different players may indicate collusion and are worth suspicion.
    - [ ] The guidance is **light**: AI players are made aware of the signal but are not instructed to react in any specific, mandatory way.
    - [ ] This awareness applies to AI players' **spoken turns** during the Day. It is **not** added to their execution-vote (Yes/No ballot) decisions.

### 2.2 The resulting behavior stays emergent

- **As a** player, **I want** the AI's response to copycatting to feel natural, **so that** the game doesn't become robotic or repetitive.
  - **Acceptance Criteria:**
    - [ ] No automatic/mechanical detection flags duplicate messages; whether similarity is noticed or mentioned is left entirely to the AI's own judgment.
    - [ ] The AI is not forced into a fixed phrase or a mandatory call-out — an AI may ignore the similarity, mention it, or simply factor it into whom it suspects.
    - [ ] Players may also, emergently, *avoid* copycatting once the signal is in play — this is allowed to arise on its own, not enforced.

### 2.3 No automated test; no other change to the Day

- **As the** maintainer, **I want** this kept to a light guidance change, **so that** it adds flavor without new mechanics or test overhead.
  - **Acceptance Criteria:**
    - [ ] The behavior is confirmed by **play-testing only**; there is no automated test asserting it (AI output is non-deterministic, and the intent is emergent behavior, not a guaranteed reaction).
    - [ ] No new on-screen control, setting, or message is introduced — the only observable change is potentially richer AI Day dialogue.
    - [ ] Turn flow, number of rounds, and vote rules are unchanged.

---

## 3. Scope and Boundaries

### In-Scope

- A light addition to the **AI players' Day speaking guidance**: that identical or near-identical messages from different players may signal collusion and warrant suspicion.
- AI players' **spoken turns** during the Day phase.

### Out-of-Scope

- **AI execution-vote (ballot) decisions** — the awareness is not added to how AIs vote Yes/No.
- **Mechanical or automatic detection** of duplicate/similar messages — no system-side flagging; the AI judges similarity itself.
- **Any forced or scripted AI reaction** — behavior stays emergent; no mandatory call-out, no fixed wording.
- **Automated tests** for this behavior — confirmed by play-testing only.
- **The human player** — this guides the AI players only; the human speaks however they choose.
- **Night phase / private Mafia communication** — unrelated to this Day-speech nudge.
- **The visibility that makes noticing possible** — that is [spec 008](../008-same-round-message-visibility/functional-spec.md); this spec only adds the interpretive nudge on top of it.
- **Other roadmap items** (Phase 4 AI Provider Flexibility; Phase 5 Configurable Role Counts & Multi-Round Mafia Consensus; Phase 6 Personas & Async Day Chat; Phase 7 Tool-Use & Expanded Roles) — each its own spec.
