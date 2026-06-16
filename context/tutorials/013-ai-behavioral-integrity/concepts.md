---
spec: 013-ai-behavioral-integrity
spec_title: AI Behavioral Integrity & Outcome Tracking
introduced_on: 2026-06-16
status: under test (spec Draft — one hypothesis confirmed, one refuted, one open)
---

# Concepts introduced in this increment

## Evaluation methodology

- **A behaviour fix is a tested hypothesis, not a deliverable** (`behavior-fix-as-hypothesis`) — a prompt nudge for a non-deterministic agent is a *hypothesis* the before/after measurement confirms or refutes; refutation is a valid, recorded outcome that informs the next attempt, not a spec failure. The spec's acceptance is written in those terms ("we attempt X; the measurement tests whether it worked").
- **Baseline-before-fix within one spec** (`baseline-before-fix-within-spec`) — even when tracking and the fix ship in one increment, the slice order lands the new measurement and **commits a baseline on the unchanged behaviour first**, on a clean tree, so the fix is judged against a real before-picture rather than asserted.
- **Outcome + activity tracking, not just blunder rates** (`outcome-and-activity-tracking`) — recording per run *who won* (win-rate by side) and *how much vote activity each side generated, by game-day* turns a behaviour's downstream consequence (and a dead Day phase) into a visible number, so "did the fix matter?" is answerable, not just "did the rate move?".
- **Making absence speak: explicit-zero vs absent-omission** (`explicit-zero-vs-absent-omission`) — `vote_activity` *always* emits its zero (a silent Day reads as a committed `0/0`), the deliberate inverse of the blunder metrics' "omit a no-opportunity rate" rule — because here the *absence* of activity is itself the signal being measured.
- **Prompt wording couples to measurement parsing** (`prompt-anchor-measurement-coupling`) — when a metric parses the model's own prompts (the prompt-derived speaker resolver), rewording a gameplay prompt can silently break attribution; a behaviour change must be checked against the measurement code that reads prompt text.

## Prompt design

- **Role/identity grounding in the prompt** (`role-identity-prompt-grounding`) — injecting the actor's secret role, win condition, (for Mafia) teammates, and an explicit ballot relationship-flag ("this is YOU" / "your fellow Mafioso") *directly into every speak/ballot prompt* — rather than relying on a one-time intro that scrolls out of the context window — to stop self- and team-defeating votes.
- **Knowledge-boundary invariant in grounding** (`role-knowledge-boundary-invariant`) — the grounding discloses only what an actor's role legitimately knows: a Mafioso learns its teammates; a Law-abiding Citizen learns its own role and goal but **never any other player's allegiance**. A "for symmetry" law-abiding teammate list would collapse the deduction game.
