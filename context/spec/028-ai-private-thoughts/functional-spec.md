# Functional Specification: Per-AI Day-Round Private Thoughts

- **Roadmap Item:** Phase 6 — **AI Personas & Per-Game Memory → Per-AI Day-Round Private Thoughts** (the next incomplete sub-item). Relates to the **Town-coordination / Day-decisiveness** and **per-AI reasoning** threads.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The AI town struggles to act decisively on what it knows. Earlier changes gave each AI player better *inputs* — the standing situational summary (spec 019), a role-specific directive (spec 024), and a fuller view of the recent discussion (spec 025) — but the player still has nowhere to **reason for itself** between turns: each Day turn it speaks or votes cold, with no private train of thought carried forward.

This change gives every surviving **AI** player a private space to think. At the **end of each Day round**, the player privately reflects — a short note, **seen by no one else** (not the other players, not the human) — taking stock of the conversation and the game so far and planning its own next move. These notes **accumulate** into a running private train of thought that is fed back to **that same** player, in the order they were written, in its later **Day-speech, vote, and (for a Mafioso) Night-pointing** decisions — so each move is grounded in the player's own evolving read rather than starting fresh.

The reflection is deliberately **mild**: it invites the player to think and plan in its own voice, **without steering it toward any particular strategy** (a contrast to the directive role-guidance of spec 024 — the two coexist: one gives the player a private place to reason, the other a closing nudge). Whether a private reasoning channel actually improves play is an open question this lets us test, not a promise.

This is a **within-game working scratchpad**, distinct from the separate **Per-AI Private Diaries** roadmap item (the before-Night entries surfaced at end-of-game). It is only for the **LLM AI players** — the deterministic eval scripted stand-in (spec 026) does not reflect. For analysis, each private thought is **preserved in the eval transcript** as that player's private, annotated note — so a reviewer can compare what the AI *thought* against what it *did* — but it is **never shown to other players or in live human play**.

**Success looks like:** each surviving AI player writes a short private reflection at the end of every Day round; that player's own accumulated thoughts inform its later speech, vote, and (Mafioso) Night pointing, in order; no player ever sees another's thoughts and the human never sees them in play; the reviewer can read each thought in the preserved eval transcript; and a measured comparison records whether the channel improves play, confirmed or refuted.

---

## 2. Functional Requirements (The "What")

- **Each surviving AI player writes a short private thought at the end of each Day round.**
  - At the close of every Day round, each surviving AI player produces a brief reflection — taking stock of the conversation and the game and planning its own move. The prompt for it is **mild**: it invites thinking and planning, without prescribing a strategy. Dead players and the human do not produce one.
  - **Acceptance Criteria:**
    - [ ] Given the end of a Day round, when it completes, then each surviving AI player has produced one short private thought.
    - [ ] Given a player who is dead (or the human), then no private thought is produced for them.
    - [ ] Given the reflection it is asked for, then it is an open invitation to take stock and plan — not an instruction toward a particular move.

- **A private thought is seen by no one else during play.**
  - A player's thought is never shown to any other player, and never shown to the human during play.
  - **Acceptance Criteria:**
    - [ ] Given any player's private thought, when other players take their turns, then that thought is not part of what they see.
    - [ ] Given live play, when the human plays, then they never see another player's private thought.

- **A player's own thoughts accumulate and feed back into its later decisions, in order.**
  - The running list of a player's own prior thoughts is woven, in the order written, into that same player's later **Day-speech**, its **vote**, and — for a Mafioso — its **Night pointing**, grounding its next move. A player receives only its **own** thoughts, never another player's.
  - **Acceptance Criteria:**
    - [ ] Given an AI player that reflected in earlier rounds, when it next speaks, votes, or (as a Mafioso) points at Night, then its own accumulated thoughts (in event order) are part of what informs that decision.
    - [ ] Given two AI players, then neither ever receives the other's thoughts.

- **Each private thought is preserved in the eval transcript as a private note.**
  - In the preserved transcript of a measured (eval) game, each thought appears attributed to its author as a **private, annotated element** — visibly distinct from public speech — so a reviewer can compare a player's thoughts against its actions. It is never rendered to other players or in the live human display.
  - **Acceptance Criteria:**
    - [ ] Given a measured game's preserved transcript, when the reviewer reads a round, then each surviving AI player's private thought for that round appears as its own private/annotated element, attributed to that player.
    - [ ] Given the same thought, then it never appears in another player's view or in the live human display.

- **The feature is an adjustable setting (so the change is ablatable).**
  - Per-AI private thoughts can be turned off to reproduce the prior behavior for a side-by-side comparison; on by default (per the ablation-flag convention, [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md)).
  - **Acceptance Criteria:**
    - [ ] Given the setting at its default, when games are played, then players reflect and their thoughts feed back as above.
    - [ ] Given the setting turned off, when games are played, then no private thoughts are produced and the players' prompts revert to their pre-028 form (for A/B).

- **The effect is measured, not assumed (effort-not-results).**
  - Whether the private reasoning channel improves play (coherence, decisiveness, win-rate) is measured against the recorded baseline and logged, confirmed or refuted.
  - **Acceptance Criteria:**
    - [ ] Given a measured run after this change, when its outcomes are compared with the baseline, then the comparison is recorded and the hypothesis logged confirmed or refuted — either being a complete result.

---

## 3. Scope and Boundaries

### In-Scope

- A short, **mild** per-AI private reflection at the end of each Day round, for every surviving AI player.
- Keeping each thought **private** (no other player, no human in play) while **accumulating** it and feeding a player's own thoughts, in order, into its later **Day-speech, vote, and Mafioso Night-pointing** decisions.
- **Preserving** each thought in the eval transcript as a private, annotated element for analysis.
- Making the feature an adjustable, default-on setting for ablation, and measuring its effect under effort-not-results.

### Out-of-Scope

- The **Per-AI Private Diaries** (the separate Phase-6 roadmap item) — before-Night entries surfaced at end-of-game and stored in long-term memory; this spec is the within-game scratchpad only.
- The **human** player (writes no reflection) and the **deterministic eval scripted stand-in** (spec 026, no model call, no reflection).
- **Cross-game / long-term memory** — thoughts live only within the one game.
- **Showing** a player's thoughts to other players or in the live human display — they are private in play (the eval transcript is the only place they surface, for the reviewer).
- Changing any **game rule, win condition, or the wording of public speech/recaps/votes**.
- All other roadmap items, which are automatically out-of-scope for this specification.
