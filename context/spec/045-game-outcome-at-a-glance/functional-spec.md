# Functional Specification: Reading a Game's Outcome Without Opening It

- **Roadmap Item:** _None — tooling._ This is the eval-review layer, not the game; it adds no player-facing capability, so the roadmap is deliberately left untouched, following the convention of specs 041 (*Reliable Structured Decoding*), 043 (*Keeping What a Stopped Measurement Run Produced*) and 044 (*Searching Inside a Game Transcript*). The request has been logged since **2026-06-20** in `context/product/backlog.md` as the viewer half of *"Titled, stats-labelled transcripts (render + viewer)"* — *"list each game by that title plus per-game stats (outcome/winner at minimum; rounds, votes, blunders if available) rather than `game-NN`"*. This spec takes the **stats** half of that item and narrows it to **three numbers plus the winner**; the matchup title and the transcript-side final-standings recap stay open (§3).
- **Status:** Draft
- **Author:** Alexey Tigarev

> **No sibling in-flight spec.** 043 and 044 are both Completed, so this spec has the viewer to itself. It does, however, **build on three completed specs**: 017 (*Eval Transcript Preservation*) is what preserves the games this list names; 037 (*Transcript List as a Side Panel in the Eval-Results Viewer*) is the list itself; and 038 (*Colour-Coded Transcript Reading View*) is where the two side colours this spec reuses were established. A reviewer who picks a game out of this list lands in 038's coloured reading view and 044's search — so the colours the list speaks must be the colours that view speaks.

---

## 1. Overview and Rationale (The "Why")

A measured run's games are listed beside its numbers as a bare column of names: `game-01`, `game-02`, … `game-50`. Every row looks exactly like every other row. Nothing about the list says which of those fifty games is worth a reviewer's next twenty minutes.

That is the whole problem. **Reading games is how the measured rates get their meaning** — a repetition rate of 0.64 means nothing until someone has been inside a game and seen the looping it counted — but a reviewer facing fifty identical rows has no way to choose, so they open game 1, then game 2, then give up. The interesting game is not usually the first one.

What makes a game interesting is almost always its shape: **how long it ran**, **who was left standing**, and **which side won**. Those three facts separate the games at a glance. Across the **583** games preserved so far, the shape is startlingly uniform — the Mafia win **471** of them, and **432** of those end at exactly two Law-abiding Citizens against two Mafiosos, the parity the Mafia win on. In **253** of those the town never executed anybody at all; every single death was a Night kill. Against that wall of sameness, the game where the town actually convicted somebody stands out immediately once the numbers are on the row. In the newest fifty-game local run, game 5 is that game: it ended on a Day, after two executions, **four Law-abiding alive and no Mafiosos left**. Every other game in its neighbourhood reads `2-2`. A reviewer scanning the list would go straight to it; a reviewer scanning today's list has no reason to pick it over any other.

The same three numbers also answer the questions a reviewer asks *about the run as a whole* without opening anything: did the town ever win? Do the long games differ from the short ones? Is this run's uniformity real or did I only look at the first two games? Those are the questions the tracked history is *for*, and they currently require opening games one at a time and reading to the end of each.

**Success looks like:** a reviewer opening a run of fifty games can tell, without opening a single one, how long each game ran, how many players of each side survived it, and who won — and can find the one game that went differently in a few seconds of scanning.

---

## 2. Functional Requirements (The "What")

### 2.1 Every game in the list carries its length and its final standings

- Each row in the list of a run's games shows three things beside the game's number: **how many Days the game lasted**, and **how many Law-abiding Citizens** and **how many Mafiosos** were still alive when it ended.
- The two survivor counts are always in the same order — **Law-abiding first, Mafiosos second** — separated by a hyphen, so a reviewer learns the order once and can read every row after that without a label.
- **The game's number stands alone.** A row names the game by its number only (`01`, `02`, … `50`) rather than by the longer `game-01` form it uses today. The word "game" on every row of a list of games is dead weight, and the room it frees is what the three new numbers use.
- **Days means Days that were actually played.** Most games end on a Mafia Night kill, which leaves them one Night longer than they are Days — in the newest local run, game 1 ran three full Days and then ended in its fourth Night. Such a game reads **3**, not 4. The number a reviewer sees is a Day the moderator announced out loud inside the game ("Day 3, 9 AM status…"), so it is a number they can go and find.
- **The list keeps its present width.** The strip the games are listed in is no wider and no narrower than it is today, so the panel of run details beside it neither shrinks nor moves. Nothing outside the list changes position.
- **Acceptance Criteria:**
  - [ ] Given a reviewer has opened the 50-game local run of 2026-09-14, when they look at the list of its games, then each row shows the game's number, a day count, and two survivor counts.
  - [ ] Given that run's list, when the reviewer looks at the first six rows, then they read `3` days `2-2`, `2` days `2-2`, `3` days `2-2`, `3` days `2-2`, `2` days `4-0`, `3` days `2-2` — in that order.
  - [ ] Given that run's game 5 ended with four Law-abiding Citizens and no Mafiosos alive, when the reviewer looks at its row, then the Law-abiding count is shown first and reads 4, and the Mafia count is shown second and reads 0.
  - [ ] Given that run's game 1 played three Days and then ended during a fourth Night, when the reviewer looks at its row, then the day count reads 3.
  - [ ] Given any run's list of games, when the reviewer looks at the rows, then no row contains the word "game" — each is named by its number alone.
  - [ ] Given a reviewer looks at the screen before and after this change, when they compare the panel of run details beside the list, then it occupies exactly the same width in both.

### 2.2 The winning side's count is the one that stands out

- On each row, **the winning side's survivor count is shown in that side's colour** — the same red the reading view gives Mafia lines, and the same blue it gives Law-abiding lines — and **the losing side's count is dimmed** to the muted grey the list already uses for its "no transcripts" note. A reviewer can therefore read the winner off the row without reading a word.
- The colours are not this list's invention: they are the two side colours a reviewer already learned in the reading view they are about to open. A game that reads red in the list opens into a transcript whose winning side's lines are red.
- **The winning side's count is also bold.** Colour and weight together, so the mark survives a terminal or a screenshot that loses one of them. **AMENDED 2026-09-15, at the author's direction during technical design.** This bullet originally read that colour was the row's single statement of the winner; measurement showed that claim could not hold on the row the reviewer is sitting on (see the next bullet), and the author chose to add weight rather than weaken the highlight.
- **On the row the reviewer is currently on, weight carries the winner by itself.** The list marks the reviewer's current row the way it always has, and that mark repaints the row in its own colours — so on that one row the two side colours give way and the winning side's count is picked out by being bold. Every other row shows colour and weight both. The signal degrades from two marks to one, never to none.
- **This is the one place a row states the winner.** No extra letter, marker or word names the winning side.
- **Acceptance Criteria:**
  - [ ] Given a game the Mafia won, when the reviewer looks at its row, then the Mafia survivor count is in the Mafia colour and the Law-abiding count is dimmed.
  - [ ] Given a game the Law-abiding Citizens won, when the reviewer looks at its row, then the Law-abiding survivor count is in the Law-abiding colour and the Mafia count is dimmed.
  - [ ] Given a game that ended at two Law-abiding against two Mafiosos — where both counts are the same number — when the reviewer looks at its row, then they can still tell which side won, because only one of the two 2s is coloured.
  - [ ] Given a reviewer opens a game from the list, when they compare the colour that was highlighted on its row with the colours inside the game's text, then the side that was highlighted is the side whose spoken lines carry that same colour.
  - [ ] Given any row, when the reviewer reads it, then the winner is named nowhere on it in words or letters.
  - [ ] Given a game the Mafia won, when the reviewer looks at its row, then the Mafia survivor count is bold and the Law-abiding count is not.
  - [ ] Given the reviewer moves onto a row, when they look at it, then exactly one of its two survivor counts is bold — and it is the winning side's.
  - [ ] Given the reviewer moves onto a row, when they look at its two survivor counts, then both are still legible against the mark on that row.

### 2.3 The three columns are labelled once, at the top of the list

- Because a row is now `01  3d  2-2` rather than a name, the list carries **one header line above the rows** naming its three columns — the game number, the day count, and the Law-abiding–Mafia survivors. The header scrolls with nothing; it sits at the top of the list and stays there.
- **The strip carries its own title, "Games"** — the same word the key hints at the bottom of the screen already use for it — so a list of bare numbers is still identifiable as a list of games. **ADDED 2026-09-15, at the author's direction during technical design**, alongside the header line rather than instead of it: the title names the strip, the header names the columns.
- Beyond those two, there is no legend, no help text, and no explanation of the colours.
- **Acceptance Criteria:**
  - [ ] Given a reviewer opens a run's list of games for the first time, when they look at the top of the list, then a header line names the three columns.
  - [ ] Given the reviewer scrolls down a 50-game list, when they look at the top of the list, then the header is still visible and no game row has scrolled underneath it and been lost.
  - [ ] Given the header line, when the reviewer counts the rows below it, then every one of the run's games is still listed — the header has taken the place of no game.
  - [ ] Given a reviewer looks at the strip the games are listed in, when they read its title, then it reads "Games".

### 2.4 A game with no recorded ending says so rather than guessing

- A handful of preserved games have **no recorded ending at all**: they hit the Day cap of their era and their text simply stops, with no closing announcement and no final roster. Ten of the 583 preserved games are like this, across two runs of June 2026.
- Such a row shows **its day count**, which is genuinely readable, and **a dash where the survivor counts would be**. Nothing is highlighted, because no side won. The row never shows a pair of numbers that could be mistaken for final standings.
- **Acceptance Criteria:**
  - [ ] Given the reviewer has opened the 10-game local run of 2026-06-19, when they look at the rows for games 3, 4 and 5, then each shows a day count and a dash in place of the survivor counts.
  - [ ] Given one of those rows, when the reviewer looks at it, then no part of it is coloured as a winning side.
  - [ ] Given one of those rows, when the reviewer looks at it, then it shows no pair of survivor numbers at all.
  - [ ] Given the same run's game 2, which does have a recorded ending, when the reviewer looks at its row, then it shows survivor counts normally — the dash appears only on the games that lack an ending.

### 2.5 Every game already preserved gets its numbers, with nothing re-run

- The three numbers appear for **all 583 games preserved so far**, not only for games measured after this change. A reviewer opening the oldest run in the tracked history sees the same three numbers as one opening the newest.
- No measured run has to be repeated, and no existing preserved game has to be rewritten, for its row to carry its numbers.
- **Acceptance Criteria:**
  - [ ] Given a reviewer opens the oldest run in the history that has preserved games, when they look at its list, then its rows carry day counts and survivor counts like any other run's.
  - [ ] Given this change is made, when the reviewer checks the preserved games, then none of their text has been altered.
  - [ ] Given this change is made, when the reviewer checks the tracked history of runs, then no measured run has been re-run and no run's recorded numbers have changed.

### 2.6 The list is still the list it was

- Everything a reviewer already does with this list keeps working, unchanged: moving between rows, opening the picked game, coming back, and moving focus between the list and the run's details beside it. The rows are in the same order they were.
- A run with no preserved games still shows its "no transcripts" note, exactly as it does today — this change adds nothing to that state.
- **Acceptance Criteria:**
  - [ ] Given a reviewer is on a run's list of games, when they move up and down the rows and open one, then the game that opens is the one whose row they were on.
  - [ ] Given a reviewer has opened a game and comes back, when they look at the list, then they are on the row they left from.
  - [ ] Given a run's list of games, when the reviewer reads the rows top to bottom, then the games are in ascending number order, as before.
  - [ ] Given a run with no preserved games, when the reviewer opens it, then they see the same "no transcripts for this run" note as before, with no day counts, survivor counts or header.

---

## 3. Scope and Boundaries

### In-Scope

- Three facts per game — Days played, Law-abiding survivors, Mafia survivors — on every row of a run's list of games.
- The winning side stated by colouring its count and dimming the other's, reusing the two side colours the reading view already established.
- A one-line column header above the rows.
- An explicit "no recorded ending" appearance for the games that have none.
- All 583 games already preserved, retroactively, with no run repeated.

### Out-of-Scope

- **The matchup title.** The other half of the originating backlog item — naming a game by its cast, "Mafia: Bo, Dario — vs — Law-abiding: Mira, Avery, …", both in the preserved text and as the row's label — is **not** included. This spec's rows are numbers, not names. That item stays open.
- **A final-standings recap inside the preserved text.** The related backlog item asking each preserved game to carry its own end-of-game standings — the survivors *by name and side*, including when the game ends on a Night kill — stays open. This spec puts three numbers on a row; it does not change a single preserved game's text.
- **Survivors by name.** A row says *how many* of each side lived, never *which* players. Names are what opening the game is for.
- **Any other per-game statistic.** The originating item mentions votes and blunders as candidates ("if available"); they are deliberately left out. Three numbers is the whole of it.
- **Sorting, filtering or grouping the list** — by outcome, by length, or by anything else. The list stays in game order. (Searching *within* a game is spec 044's, and already done; searching *across* games is neither.)
- **The table of runs.** The screen that lists measured runs gains no column and loses none. Nothing about a run's own recorded numbers changes.
- **The three run-identity details the viewer still does not show** — a run's own identifier, the counter that would reveal a lost update to its record, and the folder its preserved games live in. A separate open backlog item, untouched here.
- **Live progress for a run still in flight**, and **special presentation for runs whose individual games failed early** — the two senses of the partial-runs backlog item that spec 043 deliberately left open. Neither is this.
- **Every remaining roadmap item**, automatically: the Phase 6a Day-experience work (Asynchronous Day Chat — rate-limited concurrent AI chatter, concurrent human typing with live display, the vote-opens-lock-chat handoff) and all of Phase 7 (AI Tool-Use Demonstration, Evidence Citation, the Expanded Role Roster, and the LLM-as-Judge whole-transcript evaluation). None of them is touched by a change to how a list of games is labelled.
