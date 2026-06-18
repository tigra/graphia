---
spec: 015-multi-round-mafia-consensus
spec_title: Multi-Round Mafia Consensus by Pointing
introduced_on: 2026-06-17
---

# Concepts introduced in this increment

## Orchestration (the consensus loop)

- **Per-pointer consensus loop** (`per-pointer-consensus-loop`) — Model "N actors converge over up to K rounds" as a self-looping node that handles **exactly one actor per visit**, with a conditional router choosing next-pointer / next-round / resolve — rather than one node that loops over all actors internally. Committing each pick as its own super-step is what makes the loop both replay-safe and self-documenting in the checkpoint.
- **Shuffle as its own super-step (replay-safe non-determinism)** (`shuffle-as-own-superstep`) — Because LangGraph re-executes a node wholesale on resume, the round's one piece of non-determinism (the order shuffle) is isolated in a *no-`interrupt()`* node committed *before* any human pointer is prompted, so a human-pointer resume re-derives state and recomputes nothing — the concrete application of replay-safe interrupt placement to a node that mixes RNG and a human turn.
- **Round-scoped loop state in committed channels** (`round-scoped-loop-state`) — The loop's progress (round number, the round's shuffled order, the cursor, the current round's picks, the completed-rounds log) lives in plain-replace `GameState` channels reset at phase entry, so each one-actor visit reads where it is from committed state and the router is a pure read — no in-node iteration that a resume would repeat.
- **Unanimity-or-cap termination, deciding-round resolution** (`unanimity-or-cap-termination`) — The loop ends the moment a round is unanimous, or when a fixed round cap is hit; the victim is then resolved from the **deciding round's** picks by the unchanged plurality-with-random-tie-break rule — so early agreement and the split-to-the-end fallback share one resolution path, and the lone-actor case falls out as trivially unanimous.
- **Per-round fair re-shuffle** (`per-round-fair-reshuffle`) — Re-randomizing the actor order every round (reusing the project's fair-shuffle helper) so the last-to-act advantage — acute here because the last pointer sees everyone else's pick under the unanimity rule — rotates by chance rather than sticking to one actor.

## Prompt design (inducing convergence)

- **Running state in the prompt to induce convergence** (`running-state-prompt-convergence`) — Feed each AI actor a by-name summary of its teammates' picks so far (completed rounds + the current round before its turn) plus a "converge" instruction, so independent stochastic agents move toward agreement **without any chat channel** — pointing is still the only communication.

## UI

- **Mirroring the agent's context to the human** (`mirror-agent-context-to-human`) — Surface the human actor the *same* information the AI receives (the round number and the by-name picks-so-far, built from the same helper) through the interrupt payload, so a human plays the consensus on equal footing rather than blind.
- **Content-sized modal** (`content-sized-modal`) — Size a modal to its content (`height: auto` + a `max-height` screen cap, list at `height: auto`) so a variable-length list shows every item instead of collapsing to a sliver under a fixed-fraction height when extra chrome is added — scroll only when the content genuinely can't fit.
