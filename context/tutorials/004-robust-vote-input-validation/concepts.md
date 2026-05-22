---
spec: 004-robust-vote-input-validation
spec_title: Robust /vote Input Validation
introduced_on: 2026-05-22
---

# Concepts introduced in this increment

## Orchestration

- **One interrupt per node execution** (`single-interrupt-per-node-contract`) — A node may call `interrupt()` at most once per execution; the driver delivers exactly one resume value per super-step, so a node that interrupts twice strands the resume pump.
- **Re-prompt via a state channel, not a second interrupt** (`reprompt-via-state-channel`) — Reject-and-retry is modelled by returning an error on a `day_turn_error` channel and looping the conditional edge back to a fresh node execution, rather than looping `interrupt()` inside one node.
- **Empty `next` can mask a pending interrupt** (`driver-empty-next-masks-interrupt`) — The driver's "no next nodes → game over" check fires before it inspects pending interrupts, so a second in-node interrupt (which empties `snapshot.next`) is misread as the graph terminating.

## Testing

- **Testing through the real resume pump** (`driver-level-resume-test`) — Driving the actual `drive_graph()` loop (not a hand-rolled `graph.stream()` walk) is what surfaces resume-pump bugs that node-level tests structurally cannot see.
- **Forcing vote outcomes with a seeded ballot queue** (`forced-ballot-outcome-test`) — Pre-loading the fake LLM's `Ballot` queue with uniform yes/no answers pins a vote to a deterministic pass or fail outcome, so both branches can be exercised end-to-end.
