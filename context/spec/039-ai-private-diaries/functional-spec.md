# Functional Specification: Per-AI Private Diaries

- **Roadmap Item:** Phase 6 — **AI Personas & Per-Game Memory → Per-AI Private Diaries** (the last incomplete sub-item of that group, and of Phase 6). Sits beside the completed **Per-AI Day-Round Private Thoughts** (spec 028) in the *per-AI reasoning* thread.
- **Status:** Completed *(verified 2026-09-02 — all 36 acceptance criteria met; suite green at 1809 passed / 1 skipped. The measured comparison is recorded across six runs on one clean commit: no arm improved and no pair separated (Fisher exact p = 1.000 on all three), and the design limit is the finding — against an off arm of 2/10 the on arm would have needed 8/10 to reach p<0.05, so **no decrease of any size was detectable at n=10**. Accepted under CR 005's effort-not-results principle: the honest measurement is the deliverable. **One arm is disqualified and says so in its own record:** the ollama on-arm is 50% deterministic fallback (45 of 90 entries), so it is not a diaries result — a defect the run-health signal added mid-spec caught, and which a warm-up call did not prevent.)*
- **Author:** Alexey Tigarev

> **The feature that was going to read these diaries is not being built here.** The **Moderator Creative Recap** — the end-of-game story that draws on the diaries and reveals hidden twists — is a separate, deliberately deferred item in **Phase 6a** (*End-of-Game Payoff*). This specification therefore writes the diaries and puts them to work during play; it shows the person playing **nothing** at end-of-game. That is the author's explicit decision, taken with the alternative (a plain end-of-game reveal) on the table.

> **A near neighbour that already exists.** Spec 028 gives every AI player a private reflection at the end of each Day *round*, accumulated and fed back into its own later decisions. A diary is also a private note about suspicions and plans, so the two could easily read as duplicates. What separates them is stated throughout below: a diary is written **once per day cycle, before Night**, it sums up **a whole day** rather than one round, and it is deliberately **longer**. Both channels feed the same player; neither is visible to anyone else during play.

---

## 1. Overview and Rationale (The "Why")

An AI player currently reasons in short bursts. At the end of every Day round it writes a brief private reflection (spec 028) — a reaction to the conversation just had. Useful, but small-grained: nothing in the game asks a player to stop at the end of a day, take in **the whole day**, and set down where it now stands and what it means to do about it.

This change gives each surviving AI player a private diary. **Before each Night falls**, it writes one entry — its suspicions, how they changed today, and what it plans — and that entry is carried into its later decisions alongside its running thoughts.

Two things make the diary worth having beside the thoughts rather than instead of them. The first is **grain**: a day's summing-up is a different act from a round's reaction, and a player that has written one carries a settled read rather than a running commentary. The second is **voice**: the prompt is left open on purpose, so different characters use the diary differently — one plans the night ahead, another broods on what has already happened, another does both. That variation is the point, not a side effect; it is where a persona shows itself when nobody is watching.

**Who sees a diary:** during play, only the player who wrote it — never another player, never the person playing. At end-of-game, still nobody. The entries are preserved in the **records kept of measured games**, each appearing at the moment it was written, in sequence with everything else that happened. That is where the value lands for now: someone reviewing a measured game can read what a player privately committed to before a Night and compare it against what that player then did. Turning the diaries into an end-of-game story for the person playing is the separate Phase 6a item.

**Success looks like:** every surviving AI player writes one diary entry before each Night; those entries inform that same player's later speech, votes and Night choices; no player ever sees another's diary and the person playing never sees any of them; a reviewer reading a preserved game finds each entry at the point it was written; the entries read as varied across characters — some forward-looking, some backward-looking — rather than as one repeated formula; and a measured comparison of games played with and without diaries is recorded in the tracked quality history, so the effect on how the town fares is written down rather than assumed.

---

## 2. Functional Requirements (The "What")

- **Each surviving AI player writes one private diary entry before each Night.**
  - As the day closes and before Night begins, every AI player still in the game writes a single diary entry. Players who have been killed or executed do not write one, and neither does the person playing. In a measured game the seat played by the automated stand-in does not write one either — it makes no such decisions.
  - **Acceptance Criteria:**
    - [x] Given a day has ended and Night is about to begin, when the transition happens, then each surviving AI player has written exactly one diary entry for that day.
    - [x] Given a player who has been killed or executed, when Night begins, then no diary entry is written for them.
    - [x] Given the person playing, and given the automated stand-in in a measured game, when Night begins, then no diary entry is written for either.
    - [x] Given a game that runs several days, when it ends, then each surviving AI player has one entry per Night **that followed a Day they survived**, and no more. Night 1 has no preceding day to sum up, so no entry precedes it.
    - [x] Given the Day on which the game is won, when the game ends, then no diary entries were written for that day — the game finished before any Night followed it.

- **The diary asks an open question, so different characters answer it differently.**
  - The player is invited to write a diary entry about the day just ended — where things stand, who they suspect, what they mean to do. It is not steered toward planning ahead or toward looking back; a character may do either or both. As with the existing private reflection, the invitation is mild and prescribes no strategy.
  - **Acceptance Criteria:**
    - [x] Given the invitation the player is given, when it is read, then it asks for a diary entry about the day and leaves the character free to reflect, to plan, or to do both — it does not require any particular one.
    - [x] Given the invitation, when it is read, then it does not push the player toward a specific move, suspicion, or target.
    - [x] Given several different characters in the same game, when their entries are compared, then they are free to differ in what they dwell on — some looking ahead, some looking back — rather than all following one shape.

- **A diary entry is longer than a round's reflection, but bounded.**
  - Because it sums up a whole day rather than one round, a diary entry is allowed to be longer than the short reflection written at the end of each Day round. It is still capped, so no entry can grow without limit.
  - **Acceptance Criteria:**
    - [x] Given the invitation the player is given, when it is read, then it states an upper bound on the length of the entry.
    - [x] Given that upper bound, when it is compared with the one used for a Day-round reflection, then the diary's is the larger of the two.
    - [x] Given a written diary entry, when its length is checked, then it does not exceed the stated bound — **enforced when the entry is taken in, not merely asked for**. A bound stated only in the invitation is a request, and this criterion requires it to hold for every entry actually written.

- **A player's own recent diaries inform its later decisions.**
  - Each player carries its own recent diary entries into its later Day speech, its votes, and — for a Mafioso — its Night choice of target, in the same way its Day-round reflections already are. A player only ever sees its own.
  - **Acceptance Criteria:**
    - [x] Given a player that has written at least one diary entry, when it next speaks during a Day, then its own recent entries are among what it is working from.
    - [x] Given the same player, when it casts a vote, then its own recent entries are among what it is working from.
    - [x] Given a Mafioso that has written at least one diary entry, when it chooses a Night target, then its own recent entries are among what it is working from.
    - [x] Given any player, when it makes any of those decisions, then it is working from **only its own** entries — never another player's.
    - [x] Given a player's own diary entries and its own Day-round reflections together, when they are put in front of it, then they appear in the order the events happened, so its private record reads as one train of thought rather than two disconnected lists.

- **Only the three most recent entries are carried forward.**
  - A player carries its three most recent diary entries into its later decisions. Once it has written a fourth, the oldest drops out of what it is working from. Every entry ever written is still kept in the record of the game — dropping out affects only what the player is reasoning from, not what is preserved.
  - **Acceptance Criteria:**
    - [x] Given a player that has written three or fewer diary entries, when it next makes a decision, then all of them are among what it is working from.
    - [x] Given a player that has written four entries, when it next makes a decision, then the three most recent are among what it is working from and the oldest is not.
    - [x] Given a player whose oldest entry has dropped out of its working set, when the preserved record of the game is read, then that oldest entry is still there in full.

- **A diary is private during play, and stays private at the end.**
  - No player ever sees another player's diary. The person playing never sees any diary — not during the game, and not when it ends. Nothing about the game's visible messages, announcements or end-of-game screen changes because diaries are being written.
  - **Acceptance Criteria:**
    - [x] Given a game in progress, when the person playing looks at anything on screen, then no diary entry from any player is shown.
    - [x] Given a game that has just ended, when the person playing looks at the closing screens, then no diary entry is shown and nothing new about diaries appears.
    - [x] Given any AI player, when it takes its turn, then it has been given no other player's diary.
    - [x] Given a person who played before this change, when they play after it, then the game looks and reads exactly as it did.

- **Every entry is preserved in the record of a measured game, where it was written.**
  - The records kept of measured games include each diary entry, marked as that player's private entry, positioned at the point in the game where it was written — between the day it sums up and the Night that follows — so a reviewer reads it in sequence with everything else.
  - **Acceptance Criteria:**
    - [x] Given a measured game in which diaries were written, when its preserved record is read, then every entry appears in it.
    - [x] Given an entry in that record, when a reviewer looks at it, then it is clearly marked as a private diary entry and as belonging to a named player.
    - [x] Given an entry in that record, when a reviewer looks at where it sits, then it is between the day it was written about and the Night that followed — not gathered together at the end.
    - [x] Given a reviewer reading a preserved game, when they compare a player's entry with what that player did next, then both are readable in one pass without jumping around the record.

- **The diaries can be turned off, so games with and without them can be compared.**
  - Diaries are on by default. There is a way to run the game with them switched off entirely — no entries written, nothing carried into any decision, nothing added to the preserved record — so that two otherwise-identical sets of games can be played and compared. With diaries off, **everything a player or a reviewer can see** behaves exactly as it did before this change. One thing does not come back: the game used to file a meaningless placeholder note against each player every night, and that stops for good — switching diaries off does not restore it.
  - **Acceptance Criteria:**
    - [x] Given diaries are switched off, when a game is played, then no diary entry is written by any player.
    - [x] Given diaries are switched off, when a player makes a decision, then no diary is among what it is working from.
    - [x] Given diaries are switched off, when the preserved record of the game is read, then it contains no diary entries and otherwise reads exactly as a record made before this change.
    - [x] Given nothing has been switched off, when a game is played, then diaries are written — being on is the default.

- **A measured comparison of games with and without diaries is run and recorded.**
  - Games are played with diaries on and with diaries off, and the results of both are added to the tracked quality history the project keeps with the code — including how often the town won in each case, with the usual honest error range. The comparison is run for each of the **three models** the game can be played with, so the result is not tied to one of them, at **ten games per side** — six runs of ten games in all.
  - The spec commits to **running and recording** the comparison, not to a particular outcome: a result showing diaries help, a result showing they hurt, and a result showing no discernible difference all satisfy this requirement equally. This follows the project's standing rule for changes to AI behaviour, where the honest measurement is the deliverable.
  - **Acceptance Criteria:**
    - [x] Given the comparison has been run, when the tracked quality history is read, then it holds a record for games played with diaries on and a record for games played with diaries off, for each of the three models.
    - [x] Given one of those records, when it is read, then it states how often the town won, together with the error range around that figure.
    - [x] Given one of those records, when it is read, then it says whether diaries were on or off for that run, so the two sides of a comparison can be told apart.
    - [x] Given the comparison has been run, when its result is written up, then it states what was found — whether the town fared better, worse, or indistinguishably — without the finding being required to come out any particular way.
    - [x] Given the recorded comparison, when a reader looks at it, then it is clear how many games each side rests on, so the weight of the finding can be judged.

> **What these numbers can and cannot show — stated so nobody later reads more into them than they hold.** A town win is the rarest thing the project measures: recent baselines put it around **8% of games**, and some games end with nobody winning at all. At ten games a side, the error range around a figure like that spans almost the whole plausible field — so this comparison can reveal a several-fold **increase** and essentially nothing else. **It cannot even reveal a collapse** — at those base rates the *diaries-off* arm alone records zero town wins 43–54% of the time, so a diaries-on arm reading zero is indistinguishable from an ordinary run. (An earlier draft of this note claimed a collapse was detectable; it is not, and the arithmetic is above.) Telling a genuine halving of the win rate from ordinary run-to-run noise would take roughly **550 games per side**, about two weeks of continuous play. That measurement was considered and deliberately not taken. This requirement is therefore a **recorded observation, not a guarantee of no harm**, and the record should be read that way.


---

## 3. Scope and Boundaries

### In-Scope

- One private diary entry per surviving AI player, written before each Night.
- An open invitation that lets different characters use the diary differently — planning, reflecting, or both.
- A stated length bound for an entry, larger than the one used for a Day-round reflection.
- Feeding a player's three most recent entries into its own Day speech, votes and Night target choice, interleaved with its Day-round reflections in the order events happened.
- Keeping every entry, including those that have dropped out of a player's working set, in the preserved record of a measured game — each at the point it was written.
- A way to switch the diaries off entirely, so games with and without them can be compared.
- Running that comparison across all three models and recording both sides in the tracked quality history.

### Out-of-Scope

- **Showing any diary to the person playing** — during the game or at its end. Deliberate: the end-of-game payoff is the separate Phase 6a item.
- **The Moderator's end-of-game creative recap** (Phase 6a, *End-of-Game Payoff*) — the story that reads the diaries and reveals hidden twists.
- **Changing the Day-round private reflections** (spec 028) — they keep their cadence, their length and their own place in the player's reasoning; the diary is added beside them, not in place of them.
- **Showing a player another player's diary**, in any circumstance.
- **Carrying diaries between games** — a diary belongs to the one game it was written in.
- **Any diary for the person playing or for the automated stand-in** used in measured games.
- **Changing any game rule, win condition, or the wording of public speech, recaps or votes.**
- **Any promise that the diaries improve play.** The comparison above is run and recorded; the spec does not commit to the town faring better, and a result showing no difference — or a worse one — closes this specification just as well.
- **Gating the change on the measured result.** No threshold has to be cleared for this work to be accepted (see the note above on what the numbers can and cannot show).
- All other roadmap items, which are automatically out-of-scope for this specification.
