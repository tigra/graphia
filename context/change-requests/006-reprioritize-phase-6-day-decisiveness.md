# Change Request: Reprioritize Phase 6 toward Day-Decisiveness & Per-AI Reasoning; split Phase 6a

- **CR ID:** 006
- **Date:** 2026-06-19
- **Author:** Alexey Tigarev
- **Status:** Accepted

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [ ] `context/product/product-definition.md` — section: [name]
- [x] `context/product/roadmap.md` — phase / item: Phase 6 / new Phase 6a
- [ ] `context/spec/[NNN-slug]/functional-spec.md` — section: [name]
- [ ] Other: [describe]

**Context (1–2 sentences):** The Phase 6 ordering and grouping in `roadmap.md` were changed immediately after Spec 018 (Day-Round Moderator Recap) completed and was evaluated. Near-term work is pulled toward Day-decisiveness and per-AI reasoning, and two previously-Phase-6 features are regrouped into a new deferred Phase 6a.

---

## 2. Summary of Change

Phase 6 is reordered so the near-term work makes the AI town *act on what it already knows*. A new **Recap-Driven Day Decisiveness** capability (feed the day-round recap directly into AI players' reasoning; add the in-Day round/time to the recap), **Browsable-Transcript Round Labels**, and the existing **Per-AI Day-Round Private Thoughts** now lead the phase. **Asynchronous Day Chat** and **End-of-Game Payoff** are regrouped into a new, deferred **Phase 6a**. Nothing is removed — the deferred items keep their scope, just later.

---

## 3. Driver (Why This Change?)

**Primary driver (pick one):**

- [ ] **User / stakeholder feedback**
- [x] **Implementation learnings** — something discovered while building that invalidated an earlier assumption
- [ ] **New external constraint** — regulatory, vendor change, deprecation, cost, deadline
- [ ] **Strategic pivot** — product-level direction change
- [ ] **Error correction** — the earlier decision was wrong on its own terms
- [ ] **Scope adjustment** — descope or rescope based on capacity / priority
- [ ] **Other:** [describe]

**What was the previously-agreed assumption?** After the day-round recap, the next Phase-6 priority was the richer "feels-alive" experience (per-game memory, then Asynchronous Day Chat, then End-of-Game Payoff), with the recap itself expected to aid town coordination.

**What changed about that assumption?** A measured eval showed the recap alone does not move town coordination, so the near-term priority becomes making the AI town actually act on what it knows (Day-decisiveness + per-AI reasoning), ahead of the richer async-chat / endgame experience.

**Detailed reasoning:** The n=10 ollama eval (run `2026-06-19T18-33-37`, recorded in the quality ledger at commit `9e05ceb`) plus a read of all ten preserved transcripts found that the day-round recap (Spec 018) is **accurate at every round but never acted upon** by the AI players; the **AI town wins 0/10**; **lexical repetition is 43.6%** (the only statistically solid metric); and **70% of games stall to `no_winner`** — the model loops placid filler past the per-game step budget without ever executing anyone, never converting even a *correct* suspicion into a vote. Reading and "playing through" the games confirmed the qualitative picture behind the numbers. This evidence redirected the near-term roadmap toward decisiveness and per-AI reasoning, and made the richer async-chat / endgame experience lower priority for now. (Consistent with [CR 005](005-ai-behaviour-acceptance-effort-not-results.md)'s effort-not-results posture: the recap shipped as a *candidate aid* whose effect we committed to measure; the eval refuted the coordination effect, which is exactly the signal meant to drive a follow-up.)

**Could this have been anticipated earlier?** Partly. Spec 018's functional spec explicitly framed the recap as a "candidate aid" for the standing town-coordination weakness whose effect "we will want to measure" — so the possibility that it would not suffice on its own was anticipated; the eval confirmed it and quantified how far short the town falls.

---

## 4. Nature of Change

- [x] **Additive** — adds new behaviour without altering old (rare reason for a CR; usually a fresh spec covers this instead)
- [ ] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope** — withdraws a previously-agreed requirement

_Logged as a CR (rather than only a fresh spec) because it records a deliberate **reprioritization decision** and its eval-grounded rationale; the individual new capabilities will each get their own functional spec as reached. Nothing previously-agreed is removed — the Phase-6a items retain their full scope._

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section)                                   | What changes                                                                                                                                                                                                 | Already implemented? |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------- |
| `context/product/roadmap.md` — Phase 6 / new Phase 6a                | New near-term capabilities added and ordered first (Recap-Driven Day Decisiveness; Browsable-Transcript Round Labels); Per-AI Day-Round Private Thoughts prioritized; Asynchronous Day Chat + End-of-Game Payoff regrouped into a new deferred Phase 6a (still in scope) | Partially            |
| `context/spec/018-day-round-moderator-recap/` (Completed)            | Reframed as the foundation the new "feed the round recap into AI reasoning" work builds on; not invalidated                                                                                                   | Yes                  |
| `context/product/product-definition.md` §2.1 Core Features           | Unaffected — async Day chat, end-of-game story, and private diaries all remain in scope (now under Phase 6a / later in Phase 6)                                                                                | No                   |

**Rework / migration required (if any "Yes" or "Partially" above):**

- None. The completed work (Spec 018 day-round recap, Spec 016 personas) remains valid and is *extended*, not redone, by the near-term items. The deferred Phase-6a items keep their existing scope and acceptance intent; only their position in the sequence changed.

---

## 6. Decision

- **Decision:** Accepted
- **Decided by:** Alexey Tigarev
- **Decided on:** 2026-06-19
- **Rationale:** The n=10 eval evidence (recap accurate but unused; town 0/10; 70% `no_winner`) makes Day-decisiveness + per-AI reasoning the higher-value near-term work; the richer async-chat / endgame experience is deferred behind it without descoping.

---

## 7. Follow-up Actions

- [x] Update affected `roadmap.md` (Phase 6 reordered; Phase 6a split out — commit `a70ac4a`)
- [ ] Run `/awos:spec` for the first Phase-6 item, "Feed the Round Recap into AI Reasoning", when ready
- [ ] Give each remaining new capability (Game-Time in the Recap, Browsable-Transcript Round Labels, Per-AI Day-Round Private Thoughts) its own spec as reached
- [ ] No `/awos:verify` or `/awos:tasks` rerun needed — no completed acceptance criteria shifted
