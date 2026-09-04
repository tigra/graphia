# Functional Specification: A Starter Table With Room For One Mistake

- **Roadmap Item:** Phase 5 — Setup Flexibility, **Configurable Role Counts**. This changes the default that item left standing; the configurability itself is untouched. Authorised by **[CR 007](../../change-requests/007-starter-lineup-balance-claim-and-default.md)** (Accepted 2026-09-03), which also withdrew the Phase 1 claim that the old default was "reasonably balanced toward the Law-abiding side".
- **Status:** Completed *(verified 2026-09-04 — 11 of 12 criteria verified mechanically; the twelfth is `[?]` awaiting the first post-change measured run, tracked as CR 007 action 107)*
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

### The problem

A player who launches Graphia and configures nothing gets a table of five Law-abiding Citizens and two Mafiosos — seven players, themselves among them. That table gives the law-abiding side **no room to be wrong even once.**

The first night takes a citizen. If the town then votes out a citizen rather than a Mafioso, the next night's kill brings the two sides level and the game ends — **the town never reaches a second day to recover.** One mistaken vote on the first day is fatal, so the law-abiding side has to be right about every single execution to win at all.

That was never the intent. The roadmap described this table as leaving the game "reasonably balanced toward the Law-abiding side", and it does not: a side voting at random wins **roughly one game in twelve.**

> **A caveat on that figure, deliberately kept honest.** "One in twelve" is computed for a side voting *uniformly at random*, with someone executed every day. The games actually measured are played against an automated stand-in in the human seat which, when it is Law-abiding, will supply the vote a correct majority needs — so the real figure for a measured game is somewhat kinder than one in twelve. The direction of the argument does not depend on it: **no room for error is no room for error**, whatever the exact odds. The number should be read as an order of magnitude, not a measurement.

### The desired outcome

A player who configures nothing gets a table where the law-abiding side can be **wrong once and still win**. Nothing else about setup changes: the counts stay something a player sets before launching, the roles are still dealt at random including the player's own, and anyone who chooses their own counts sees no difference whatsoever.

### Why one more citizen, and not two

Adding citizens does not simply make the game easier in a straight line. The room for error only widens with every *second* citizen added, while each extra player makes any given vote *less* likely to land on a Mafioso. The result is that a table of seven citizens is a **worse** deal for the law-abiding side than a table of six: six-and-two roughly triples the random-play win rate, and seven-and-two is below six-and-two. Six is the efficient choice, and "more citizens is better" would have been the wrong instinct.

### What this costs

Eight players at the table means more people speak in every round, so a game takes longer to play and longer to evaluate. That is accepted rather than mitigated.

It also means the numbers recorded for a measured game are **not directly comparable with the ones recorded before this change** — the game itself got easier, and nearly every recorded figure counts something that grows with the size of the table. No ceremony is proposed for this: every recorded run already states the table it used, so a reader can see it. What this specification does add is a plain note on the **first** run recorded at the new table, so nobody has to work that out for themselves.

### How we will measure success

- Launching with nothing configured seats eight players — six Law-abiding Citizens and two Mafiosos — with roles dealt at random, and a full game runs to a natural end without errors.
- A game in which the town votes out one citizen by mistake and is right thereafter can still be won by the law-abiding side. At the old table it could not.
- A player who chooses their own counts, including the old five-and-two, is unaffected in every respect.
- The first measured run at the new table carries a note explaining how to read its numbers against the earlier ones.
- The description of how a player sets up a game matches what the game actually does.

---

## 2. Functional Requirements (The "What")

- **As** someone launching Graphia without configuring anything, **I want** a table that lets my side be wrong once, **so that** a single bad vote does not decide the game before I get another turn.

  - **Acceptance Criteria:**
    - [x] Given nothing is configured, when the game is launched, then eight players take the table — six Law-abiding Citizens and two Mafiosos — with the launching player one of the eight.
    - [x] Given nothing is configured, when the game is launched, then the launching player's own role is still dealt at random, and may be either side.
    - [x] Given the new table, when the town votes out one citizen by mistake on the first day and then votes out only Mafiosos, then the law-abiding side still wins the game.
    - [x] Given the old table of five citizens and two Mafiosos, when the town votes out one citizen by mistake on the first day, then the mafia win — confirming the difference the change is making, and that the old behaviour is still reachable for anyone who asks for it.

- **As** a player who sets my own table size, **I want** this change to pass me by entirely, **so that** my chosen game is exactly the game I get.

  - **Acceptance Criteria:**
    - [x] Given a player has chosen their own counts of Law-abiding Citizens and Mafiosos, when the game is launched, then it seats exactly those counts and the new default is not applied.
    - [x] Given a player has chosen the old five-and-two table, when the game is launched, then it plays exactly as it did before this change.
    - [x] Given a player has chosen a table that cannot make a game, when they launch, then the game still refuses it up front with a plain explanation, exactly as it did before.

- **As** someone reading the record of a measured game, **I want** to know when the table changed underneath the numbers, **so that** I do not compare two runs that were playing different games.

  - **Acceptance Criteria:**
    - [x] Given a measured run recorded after this change, when its record is read, then the record states the table it used, as every record already does.
    - [?] Given the **first** measured run recorded at the new table, when its record is read, then it carries a note saying that the table changed, that its figures are not directly comparable with runs recorded before it, and that the earlier runs remain valid records of the game as it then was. **AWAITING EVIDENCE (2026-09-04), not met and not waived.** No record dated on or after the change exists yet, because CR 007 defers the re-baseline until the Moderator Creative Recap has also landed — so this criterion cannot be satisfied by anything in this spec's scope. The obligation is carried by **CR 007 follow-up action 107** ("Record the re-baseline boundary in the quality ledger"), which is unticked and tracked there. Recording the flaw honestly: writing an acceptance criterion whose satisfaction depends on an event an accepted CR postpones was a spec-design error, and the criterion duplicates a follow-up action that already existed.
    - [x] Given a measured run recorded before this change, when its record is read, then nothing about it has been altered.

- **As** someone reading the product description to understand how a game starts, **I want** it to describe what actually happens, **so that** I am not looking for a step that does not exist.

  - **Acceptance Criteria:**
    - [x] Given the product description, when the account of starting a game is read, then it does not say the player is asked at launch for the number of Law-abiding Citizens and Mafiosos — because they are not, and were deliberately never meant to be.
    - [x] Given the product description, when the same passage is read, then it says the table is chosen before launch and states what a player gets if they choose nothing.

---

## 3. Scope and Boundaries

### In-Scope

- The **default table** a player gets when they configure nothing: six Law-abiding Citizens and two Mafiosos, whole-table counts including the player.
- A **note on the first measured run at the new table**, explaining how to read its figures against the earlier ones.
- Correcting the **product description** of how a game is set up, which currently describes a step at launch that does not exist and was explicitly ruled out when the counts were made configurable.

### Out-of-Scope

- **How the table is chosen.** It stays something set before launching, not something the game asks about while starting. Interactive setup prompts were considered and rejected when the counts were made configurable, and that decision stands.
- **The number of Mafiosos**, and the win conditions, both unchanged.
- **Going beyond six citizens.** Seven measures worse than six, for the reason given in §1; anyone who wants a larger table can still configure one.
- **Re-running the earlier measured games** at the new table. Deferred by CR 007 until the end-of-game Moderator story also lands, so it happens once rather than twice.
- **Editing any existing record.** The recorded history is added to, never rewritten.
- **A recorded "how would random play have done?" figure.** Considered and declined: every record already states the table it used, which is what a reader needs to interpret it.
- **Making the game easier to win in any other way.** This changes the size of the table and nothing about how the AI players think, speak or vote. It is not a fix for the AI town's decision-making, and it should not be read as one — that work has its own place on the roadmap.
- All other roadmap items, which are addressed by their own specifications — **Asynchronous Day Chat** and its three parts, the **Moderator Creative Recap** (Phase 6a), and every Phase 7 item (AI tool-use demonstration, evidence citation, expanded role roster, LLM-as-Judge game-quality evaluation).
