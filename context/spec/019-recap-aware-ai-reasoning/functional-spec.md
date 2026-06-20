# Functional Specification: Recap-Aware AI Reasoning

- **Roadmap Item:** Phase 6 → Recap-Driven Day Decisiveness → **Feed the Round Recap into AI Reasoning**
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The day-round recap already gives every player a clear, shared read of where the game stands — how many of each side are alive, how many votes have been called today, who's been executed. But a measured 10-game review found the AI players **never act on it**. They talk as if each Day starts fresh: they eulogize the dead ("who would want to hurt Nora?"), almost never name how many of each side remain, and rarely turn a suspicion into an actual vote. Across that review the AI town won **0 of 10** games and **7 of 10 games stalled out with no resolution** — the town simply never converts what it collectively knows into a decision.

The recap *is* part of the running conversation, but it sits in the scrolling chat history where it competes with chatter and scrolls out of view, so it functions as wallpaper rather than as something the players reason from.

This change makes the **current standings a front-and-center input to every AI player's turn** — both when it speaks and when it casts or calls a vote — so that an AI player reasons from where the game actually stands and the urgency that implies. As the Mafia close on parity, a player working from the standings should feel the pressure to act rather than drift.

Because this is non-deterministic AI behavior, success is framed as a **measured effort, not a guaranteed outcome** (the project's effort-not-results acceptance principle, CR 005): we already hold a committed 10-game baseline; after the change we run a comparable measured set and compare Day-decisiveness indicators. The work is accepted whether the indicators improve **or** not — a confirmed *or* refuted hypothesis both count as done. The town is **not** required to start winning for this to be accepted.

**Success looks like:** the current standings are placed front-and-center in what each AI player considers on its turn; the change's effect on Day-decisiveness is measured against the existing baseline and recorded; and no living player's hidden side is ever exposed in the process.

---

## 2. Functional Requirements (The "What")

- **Each AI player reasons from the current standings on its turn.**
  - When a surviving AI player takes a Day turn — to speak, to call a vote, or to cast one — the latest standings (how many of each side are alive, how much of the Day's vote budget is spent, who has been executed today) are placed front-and-center for that player, rather than only appearing somewhere back in the scrolling conversation.
  - **Acceptance Criteria:**
    - [ ] Given this change is in place, when a measured set of games is played, then the latest standings are part of what each AI player is given at its speaking and voting turns — verifiable in the recorded game data, not merely present somewhere earlier in the chat.
    - [ ] Given a game in progress with the Mafia near parity, when an AI player speaks or votes late in a Day, then its contribution *can* reflect the standings (for example, noting how many remain, or that the town must act before it loses its majority) — observed in the game transcript, understood as possible-not-guaranteed behavior.

- **The decisiveness effect is measured against the committed baseline (effort-not-results).**
  - The change is evaluated, not assumed. The existing 10-game baseline is the "before"; a comparable run is the "after"; the Day-decisiveness indicators are compared and the result recorded.
  - **Acceptance Criteria:**
    - [ ] Given the committed baseline and a comparable post-change measured run, when their Day-decisiveness indicators are compared in the quality ledger — how often players call votes, the share of games that reach a win/loss instead of stalling with no result, and the town's win rate — then the comparison is recorded.
    - [ ] Given that comparison, when the result is reviewed, then the change is accepted whether the indicators improved or not; the hypothesis ("surfacing the standings front-and-center makes the AI town more decisive") is logged as confirmed or refuted, and a refuted result is a valid, complete outcome.

- **The standings input stays truthful and reveals nothing hidden.**
  - The standings an AI player reasons from are exactly the public, already-derivable facts the recap shows — never another player's secret side.
  - **Acceptance Criteria:**
    - [ ] Given an AI player reasons from the standings, when it does so, then it is given only the public recap facts (living counts by side, votes used today, who was executed today) and never the secret side of any living player.

---

## 3. Scope and Boundaries

### In-Scope

- Making the current standings a front-and-center input to each surviving AI player's Day speech and votes.
- Measuring the change's effect on Day-decisiveness against the committed baseline and recording the result, under the effort-not-results acceptance principle.

### Out-of-Scope

- The other Day-decisiveness levers identified in the review — forcing a vote when a Day runs long, and strengthening the players' grasp of the rules / how to win — which remain in the backlog.
- Adding the in-world clock to the recap — that is the sibling spec, **Game-Time in the Recap** (020).
- The human player's experience — the human already sees the recap; this concerns the AI players' use of it.
- The separate **Per-AI Day-Round Private Thoughts** roadmap item (a private reflection scratchpad), which is distinct from reasoning from the public standings.
- Any guarantee that the AI town will win, or that decisiveness will measurably improve — the outcome is measured, not promised.
- All other roadmap items, which are automatically out-of-scope for this specification.
