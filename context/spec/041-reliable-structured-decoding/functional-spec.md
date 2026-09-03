# Functional Specification: Reliable Answers from the Local Model

- **Roadmap Item:** _None — maintenance._ This completes a capability the roadmap already agreed and marked shipped ("**Local Ollama Provider**", Phase 4 — AI Provider Flexibility: play "entirely against a local Ollama-served model… offline at zero per-token cost"). It adds no new player-facing capability, so the roadmap is deliberately left untouched. The trigger is **ADR 013 — Native Ollama Structured Output over Anthropic Tool Use**.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

### What went wrong

Graphia can be played entirely against a local model, so someone with no cloud access can develop, demo and play offline at no cost. On that configuration, **about half of every AI player's private diary entries were not written by that player at all.** They were one identical stand-in sentence, substituted quietly whenever the game could not read the model's answer:

> *"Nothing much more to set down tonight. I will sleep on what I heard today and see how it reads in the morning."*

Across a measured ten-game run, **45 of 90 diary entries** were that sentence. The players had in fact written their entries — in their own voices, about that day's events — and the game discarded them because they did not arrive in the shape it expected. The same substitution happened on Day 1 as on Day 2, to every player, in every game.

### Why it matters, twice over

**The end-of-game payoff loses its material.** The product promises that when the game ends, the Moderator draws on the dead players' diaries to reveal twists the human could not see. A diary that reads "nothing much more to set down tonight" for half its nights carries nothing to reveal. The still-unbuilt **Moderator Creative Recap** (spec 040) depends entirely on this material being real.

**The quality record became untrustworthy.** Graphia keeps a permanent record of measured games so quality can be compared over time. The run above was recorded, looked complete, and was read as a result about diaries — when half of what it measured was the stand-in sentence. It was caught only because that run happened to also record how often the substitution fired. **A silent substitution is a hazard to the project's memory before it is a hazard to any single game.**

### The deeper problem

The diary is only where this surfaced. The game asks each AI player for several things — its diary entry, its private end-of-round thought, who to point at during the Night, how to vote, what to say during the Day, and its generated personality. Every one of them arrives by the same route, and on the local model **that route is a request the model may decline rather than a rule it must follow.** The other five have simply not been provoked yet. Worse, the diary was the only one that could be rescued after the fact; for the others there is nothing to recover, because a vote or a pointing choice has no readable form outside the shape the game asked for.

What separated the failing case from the working ones was not the model, the amount of context, or any limit being hit — all of those were measured and ruled out. It was the *wording* of the request. The diary invites a player to write freely, in its own voice, with no set form — deliberately, so that different personalities use a diary differently. The end-of-round thought asks for a terse one-or-two-sentence note, and never failed once in 391 attempts on the same model in the same games. **A prompt written in a literary register is enough to break the route, which means the fault will recur wherever the next open-ended prompt is written.**

### Desired outcome

On the local model, every answer the game asks an AI player for comes back readable **because it cannot come back any other way** — not because the game rescued it afterwards. The stopgap repair currently doing that rescuing is withdrawn, so exactly one mechanism is responsible and it is obvious which. Where an answer genuinely cannot be produced, the game still finishes rather than crashing, and the substitution is **visible to a human reading that game** instead of hiding inside it.

### How we will measure success

- A measured ten-game run on the local model records a substitution rate of **zero** for diary entries, against the 0.50 that disqualified the earlier run.
- The same run records a substitution rate for **every** kind of answer the game asks for, and all of them read zero.
- A reviewer reading a preserved game can tell, without consulting any numbers, whether a diary entry was the player's own writing.
- A full game still finishes on the local model with no stack traces, and games on the cloud configurations finish exactly as they do today.
- The five kinds of answer that already worked on the local model still work — this change must not buy the diary's reliability at their expense.

---

## 2. Functional Requirements (The "What")

- **As** someone playing or reviewing a game on the local model, **I want** every AI player's private diary entry to be that player's own writing, **so that** the end-of-game story has real material and the game's record can be trusted.

  - **Acceptance Criteria:**
    - [ ] Given a game played on the local model, when the game reaches its end, then no diary entry is the stand-in sentence.
    - [ ] Given a measured ten-game run on the local model, when its record is read, then the recorded diary substitution rate is zero.
    - [ ] Given any diary entry from such a game, when it is read alongside that player's personality and the day's events, then it is recognisably about that day and in that player's voice.

- **As** the returning author, **I want** the same guarantee to cover every answer the game asks an AI player for — not only diaries — **so that** the decisions that cannot be rescued after the fact are not left waiting to fail.

  - **Acceptance Criteria:**
    - [ ] Given a game played on the local model, when the game asks a player for its private end-of-round thought, who to point at during the Night, how to vote, what to say during the Day, or its generated personality, then a readable answer is produced for each.
    - [ ] Given a measured run on the local model, when its record is read, then a substitution rate is recorded for each of those kinds of answer, and each reads zero.
    - [ ] Given the same measured run, when its recorded gameplay measurements are compared with those from before this change, then the five kinds of answer that already worked show no new failures.

- **As** the returning author, **I want** exactly one mechanism responsible for producing a readable answer, **so that** it is never ambiguous which one saved a given entry.

  - **Acceptance Criteria:**
    - [ ] Given a game on the local model, when a diary entry is produced, then it comes from the reliable route and not from the stopgap rescue, which is no longer present.
    - [ ] Given the game's own automated checks, when they are run, then the checks that described the withdrawn rescue are gone rather than left passing against nothing.

- **As** someone reviewing a preserved game, **I want** a substituted answer to be marked where I read it, **so that** I can see at a glance that it was not the player's writing.

  This covers the three kinds of answer that appear as text a reviewer reads: a player's **diary entry**, its **private end-of-round thought**, and what it **said during the Day**. The remaining kinds — who it pointed at, how it voted, its generated personality — leave nothing in a preserved game that could carry a mark, so they are covered by the recorded counts above rather than by a visible mark.

  The mark must come from **what the game recorded when it substituted**, never from recognising the stand-in wording. Wording can be matched by accident, and a stand-in that stopped being recognised would read as genuine — which is worse than no mark at all.

  - **Acceptance Criteria:**
    - [ ] Given a preserved game in which a substitution occurred, when it is read in the browsable transcript, then that answer is marked as a substitution and is visually distinguishable from a player's own writing.
    - [ ] Given a preserved game in which no substitution occurred, when it is read, then no answer carries that marking, and the game reads exactly as it does today.
    - [ ] Given a marked answer, when the transcript reading view displays it, then the marking is legible there too and does not disturb the surrounding text.
    - [ ] Given substituted answers of more than one kind in the same game, when they are read, then a substituted diary entry, a substituted thought and a substituted piece of Day speech remain distinguishable from one another.
    - [ ] Given a game in which the stand-in wording is never produced by a player, when the transcript is read, then nothing is marked — the mark tracks what the game recorded, not what the text says.

- **As** someone playing on the local model, **I want** a single failed answer never to end my game, **so that** twenty minutes of play is not lost to one hiccup.

  - **Acceptance Criteria:**
    - [ ] Given a game on the local model, when one answer cannot be read despite the reliable route, then the game substitutes the stand-in for that one answer and continues to its natural end.
    - [ ] Given that same game, when the substitution happens, then it is recorded in the run's numbers and marked in the preserved transcript rather than passing silently.

- **As** someone playing on a cloud configuration, **I want** nothing about my game to change, **so that** the baseline the project measures against stays comparable.

  - **Acceptance Criteria:**
    - [ ] Given a game played on either cloud configuration, when it is played through to the end, then it behaves as it did before this change.
    - [ ] Given a measured run on a cloud configuration, when its record is compared with earlier records, then the recorded gameplay measurements remain comparable.

---

## 3. Scope and Boundaries

### In-Scope

- Making every answer the game asks an AI player for on the **local model** reliably readable by construction: the diary entry, the private end-of-round thought, the Night pointing choice, the vote, the Day speech, and the generated personality.
- **Withdrawing the stopgap rescue** that currently recovers a diary entry when the model writes it as ordinary prose, along with the automated checks that described it, so one mechanism is responsible.
- **Marking a substituted answer visibly** in the preserved, browsable transcript — for a diary entry, a private end-of-round thought, and a piece of Day speech — in both the stored transcript and the reading view that displays it, each kind distinguishable from the others, and driven by what the game recorded rather than by recognising the stand-in wording.
- Recording, for each measured run, **how often a substitution occurred for each kind of answer**, so a run's trustworthiness is readable from its own record.
- Confirming the five kinds of answer that already worked on the local model are unaffected.

### Out-of-Scope

- **Changing what the diary invites.** The open, formless invitation — no set form, no headings, no prescribed order — stays exactly as it is. It is what makes different personalities use a diary differently, and rewriting it to be terse was considered and rejected: it would buy reliability by deleting the feature's point.
- **The cloud configurations' behaviour.** They already produce readable answers (measured: one failure in 101 attempts, and none in 66) and are deliberately left alone.
- **Re-measuring the disqualified run.** Replacing the earlier ten-game record is separate work, already under way independently of this change.
- **Anything about how the game reaches the model, expressed here.** The mechanism belongs to the technical considerations and to ADR 013; this specification commits only to the outcome.
- All other roadmap items, which are addressed by their own specifications — **Asynchronous Day Chat** and its three parts (rate-limited concurrent AI chatter, concurrent human typing with live display, vote-opens-lock-chat handoff); **Moderator Creative Recap** (Phase 6a, drafted as spec 040 — it consumes the diaries this change makes real, but is not delivered here); and every Phase 7 item (AI tool-use demonstration, evidence citation, expanded role roster, LLM-as-Judge game-quality evaluation).
