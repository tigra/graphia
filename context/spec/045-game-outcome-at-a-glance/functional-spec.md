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
  - [x] Given a reviewer has opened the 50-game local run of 2026-09-14, when they look at the list of its games, then each row shows the game's number, a day count, and two survivor counts.
  - [x] Given that run's list, when the reviewer looks at the first six rows, then they read `3` days `2-2`, `2` days `2-2`, `3` days `2-2`, `3` days `2-2`, `2` days `4-0`, `3` days `2-2` — in that order.
  - [x] Given that run's game 5 ended with four Law-abiding Citizens and no Mafiosos alive, when the reviewer looks at its row, then the Law-abiding count is shown first and reads 4, and the Mafia count is shown second and reads 0.
  - [x] Given that run's game 1 played three Days and then ended during a fourth Night, when the reviewer looks at its row, then the day count reads 3.
  - [x] Given any run's list of games, when the reviewer looks at the rows, then no row contains the word "game" — each is named by its number alone.
  - [x] Given a reviewer looks at the screen before and after this change, when they compare the panel of run details beside the list, then it occupies exactly the same width in both.

### 2.2 The winning side's count is the one that stands out

- On each row, **the winning side's survivor count is shown in that side's colour** — the same red the reading view gives Mafia lines, and the same blue it gives Law-abiding lines — and **the losing side's count is dimmed** to the muted grey the list already uses for its "no transcripts" note. A reviewer can therefore read the winner off the row without reading a word.
- The colours are not this list's invention: they are the two side colours a reviewer already learned in the reading view they are about to open. A game that reads red in the list opens into a transcript whose winning side's lines are red.
- **The winning side's count is also bold.** Colour and weight together, so the mark survives a terminal or a screenshot that loses one of them. **AMENDED 2026-09-15, at the author's direction during technical design.** This bullet originally read that colour was the row's single statement of the winner; measurement showed that claim could not hold on the row the reviewer is sitting on (see the next bullet), and the author chose to add weight rather than weaken the highlight.
- **On the row the reviewer is currently on, weight carries the winner by itself.** The list marks the reviewer's current row the way it always has, and that mark repaints the row in its own colours — so on that one row the two side colours give way and the winning side's count is picked out by being bold. Every other row shows colour and weight both. The signal degrades from two marks to one, never to none.
- **This is the one place a row states the winner.** No extra letter, marker or word names the winning side.
- **Acceptance Criteria:**
  - [x] Given a game the Mafia won, when the reviewer looks at its row, then the Mafia survivor count is in the Mafia colour and the Law-abiding count is dimmed.
  - [x] Given a game the Law-abiding Citizens won, when the reviewer looks at its row, then the Law-abiding survivor count is in the Law-abiding colour and the Mafia count is dimmed.
  - [x] Given a game that ended at two Law-abiding against two Mafiosos — where both counts are the same number — when the reviewer looks at its row, then they can still tell which side won, because only one of the two 2s is coloured.
  - [x] Given a reviewer opens a game from the list, when they compare the colour that was highlighted on its row with the colours inside the game's text, then the side that was highlighted is the side whose spoken lines carry that same colour.
  - [x] Given any row, when the reviewer reads it, then the winner is named nowhere on it in words or letters.
  - [x] Given a game the Mafia won, when the reviewer looks at its row, then the Mafia survivor count is bold and the Law-abiding count is not.
  - [x] Given the reviewer moves onto a row, when they look at it, then exactly one of its two survivor counts is bold — and it is the winning side's.
  - [x] Given the reviewer moves onto a row, when they look at its two survivor counts, then both are still legible against the mark on that row.

### 2.3 The three columns are labelled once, at the top of the list

- Because a row is now `01  3d  2-2` rather than a name, the list carries **one header line above the rows** naming its three columns — the game number, the day count, and the Law-abiding–Mafia survivors. The header scrolls with nothing; it sits at the top of the list and stays there.
- **The strip carries its own title, "Games"** — the same word the key hints at the bottom of the screen already use for it — so a list of bare numbers is still identifiable as a list of games. **ADDED 2026-09-15, at the author's direction during technical design**, alongside the header line rather than instead of it: the title names the strip, the header names the columns.
- Beyond those two, there is no legend, no help text, and no explanation of the colours.
- **Acceptance Criteria:**
  - [x] Given a reviewer opens a run's list of games for the first time, when they look at the top of the list, then a header line names the three columns.
  - [x] Given the reviewer scrolls down a 50-game list, when they look at the top of the list, then the header is still visible and no game row has scrolled underneath it and been lost.
  - [x] Given the header line, when the reviewer counts the rows below it, then every one of the run's games is still listed — the header has taken the place of no game.
  - [x] Given a reviewer looks at the strip the games are listed in, when they read its title, then it reads "Games".

### 2.4 A game with no recorded ending says so rather than guessing

- A handful of preserved games have **no recorded ending at all**: they hit the Day cap of their era and their text simply stops, with no closing announcement and no final roster. Ten of the 583 preserved games are like this, across two runs of June 2026.
- Such a row shows **its day count**, which is genuinely readable, and **a dash where the survivor counts would be**. Nothing is highlighted, because no side won. The row never shows a pair of numbers that could be mistaken for final standings.
- **Acceptance Criteria:**
  - [x] Given the reviewer has opened the 10-game local run of 2026-06-19, when they look at the rows for games 3, 4 and 5, then each shows a day count and a dash in place of the survivor counts.
  - [x] Given one of those rows, when the reviewer looks at it, then no part of it is coloured as a winning side.
  - [x] Given one of those rows, when the reviewer looks at it, then it shows no pair of survivor numbers at all.
  - [x] Given the same run's game 2, which does have a recorded ending, when the reviewer looks at its row, then it shows survivor counts normally — the dash appears only on the games that lack an ending.

  > **VERIFICATION NOTE 2026-09-16 (Task 4.3) — the second criterion above holds by construction and by measurement, but NOTHING IN THE SUITE WITNESSES IT.** `_panel_row` gives a no-ending row's dash the classes `g-cell g-dim` and never `g-law` / `g-mafia`, and the two side-colour rules key only off those two classes, so the dash cannot be painted as a winner. Measured on both truecolour themes: the dash resolves to the dim grey (`#a8a8a8` dark / `#525252` light) — the same `$text-muted` a losing count takes — and is not bold. **But this was mutation-checked and the gap is real**: changing the dash cell's classes to `g-cell g-mafia`, so a game with no recorded ending claims a Mafia win in the Mafia hue, leaves `tests/test_ledger_viewer.py` and `tests/test_game_summary.py` fully green (401 passed, 4 skipped). B16.4 pins the row's segment *structure* and its *text*, never its colour, and the B16.5–B16.9 colour readings are all taken on rows that have a winner. The criterion is therefore marked as met but is **not counted as suite coverage** — a candidate for Task 4.4's backlog routing.

### 2.5 Every game already preserved gets its numbers, with nothing re-run

- The three numbers appear for **all 583 games preserved so far**, not only for games measured after this change. A reviewer opening the oldest run in the tracked history sees the same three numbers as one opening the newest.
- No measured run has to be repeated, and no existing preserved game has to be rewritten, for its row to carry its numbers.
- **Acceptance Criteria:**
  - [x] Given a reviewer opens the oldest run in the history that has preserved games, when they look at its list, then its rows carry day counts and survivor counts like any other run's.
  - [x] Given this change is made, when the reviewer checks the preserved games, then none of their text has been altered.
  - [x] Given this change is made, when the reviewer checks the tracked history of runs, then no measured run has been re-run and no run's recorded numbers have changed.

  > **VERIFICATION FINDING 2026-09-16 (Task 4.3) — the three criteria above are met, but the bullet's "all 583" framing is not deliverable and never was.** Measured today against the committed store: **583** preserved games in **41** run directories, and the derivation reads all 583 — corpus oracles (b) *the win rule* and (d) *day-count agreement* each sweep every one of the 41 directories, 0 violations. But the **viewer** can only ever list **578** of them. It reaches a run's games through that run's **ledger record** (`run.transcript_dir` → `list_transcripts`), and **two committed directories are named by no record at all**: `2026-06-22T08-38-09` (4 games) and `2026-09-10T14-10-36` (1 game). Those 5 games have no row to carry numbers, because they have no run to open.
  >
  > **This is a pre-existing spec-017 corpus property, not something this spec introduced or could fix** — a row cannot exist for a run the viewer cannot reach — and it is the same pair of directories that makes 4 of `tests/test_game_summary.py`'s 222 items skip (oracles (a) *the ledger as oracle* and (c) *the recorded lineup* need a record, and say so in their skip reason rather than passing vacuously).
  >
  > **The three criteria are judged on their own words and each holds.** None of them says 583. The **oldest** run in the history that has preserved games is `2026-06-19T18-33-37`, which *is* named by a record and *is* reachable — the two unnamed directories are both later — so the first criterion is unaffected by the gap. The prose bullet is left as written, per the convention of not rewriting a spec's requirements during verification; the correction is recorded here. **Open for the author:** whether to amend "all 583" to "every preserved game the viewer can reach", and whether the 5 unreachable games are worth a backlog item of their own.

### 2.6 The list is still the list it was

- Everything a reviewer already does with this list keeps working, unchanged: moving between rows, opening the picked game, coming back, and moving focus between the list and the run's details beside it. The rows are in the same order they were.
- A run with no preserved games still shows its "no transcripts" note, exactly as it does today — this change adds nothing to that state.
- **Acceptance Criteria:**
  - [x] Given a reviewer is on a run's list of games, when they move up and down the rows and open one, then the game that opens is the one whose row they were on.
  - [x] Given a reviewer has opened a game and comes back, when they look at the list, then they are on the row they left from.
  - [x] Given a run's list of games, when the reviewer reads the rows top to bottom, then the games are in ascending number order, as before.
  - [x] Given a run with no preserved games, when the reviewer opens it, then they see the same "no transcripts for this run" note as before, with no day counts, survivor counts or header.

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

---

## 4. Verification

**WALKED 2026-09-16, Slice 4 Task 4.3.** All **29** acceptance criteria are marked from a named witness, not from inspection. Suite at the walk: **3590 passed, 14 skipped** — `tests/test_game_summary.py` contributes 222 items (218 passed, 4 skipped) and section `B16` of `tests/test_ledger_viewer.py` contributes 13.

**What counts as a witness here.** A test that would actually go red if the behaviour broke, or a measurement taken against the real committed corpus. Where a criterion holds by construction with nothing in the suite to see it, that is said plainly and **not** counted as coverage — the convention spec 040 followed for its two unwitnessed criteria. Where a suite witness runs against a **fixture** rather than against the real corpus, the table says so and names the real-data verification beside it, because the two are not the same evidence.

**Test-name shorthand used below.** `B16.n` is the numbered sub-section of `tests/test_ledger_viewer.py`'s section B16; `B10` / `B12` / `B13` are the pre-existing panel sections that carry §2.6. A witness that predates this spec still counts as a witness, and the table names which ones do.

### 4.1 The per-criterion table

| # | Criterion (abbreviated) | Witness | Kind |
|---|---|---|---|
| **§2.1.1** | Each row shows number, day count, two survivor counts | `B16.1 test_a_row_carries_its_day_count_and_both_survivor_counts` — reads the row segment by segment as `["05   2d  ", "4", "-", "0"]` | suite (fixture) + real corpus (§4.2) |
| **§2.1.2** | The six-row golden of the 50-game run of 2026-09-14 | `B16.3 test_the_first_six_rows_read_as_the_spec_says_they_do` — one equality over all six rows in order | suite (fixture reproducing the run's shape) + real corpus (§4.2) |
| **§2.1.3** | Game 5: Law-abiding shown first and reads 4, Mafia second and reads 0 | `B16.1` — the fixture is shaped as that game precisely because `4-0` is the one outcome whose two counts differ, so the order is a claim the row can fail | suite (fixture) + real corpus (§4.2) |
| **§2.1.4** | Game 1 played three Days then ended in a fourth Night → reads 3 | `test_game_summary.py::test_days_counts_the_days_played_not_the_nights`, plus corpus oracle (d) `test_the_day_count_agrees_with_the_moderators_own_announcements` — 41 items, every game in every run dir, checking `<day>` markers against the moderator's own `Day N begins.` | suite (synthetic + **all 583 real games**) + real corpus (§4.2) |
| **§2.1.5** | No row contains the word "game" | `B16.1` (one row) and `B16.3` (the whole list) | suite |
| **§2.1.6** | The run-details pane occupies exactly the same width before and after | `test_panel_width_is_fixed_not_derived_from_its_content` — **byte-identical to the merge base**, 4 parametrised content shapes — and `test_details_pane_width_is_identical_with_and_without_transcripts`, whose geometry assertions and the pinned `_EXPECTED_PANE_GEOMETRY` are byte-identical (see §4.4 for its one changed line) | suite (before/after pinned as an unchanged constant) |
| **§2.2.1** | Mafia win → Mafia count in the Mafia colour, Law-abiding dimmed | `B16.5 test_an_ordinary_row_colours_the_winner_and_dims_the_loser` [`textual-dark`, `textual-light`] — composited cell colours via `Screen.get_style_at`, both focus states | suite |
| **§2.2.2** | Law-abiding win → Law-abiding count coloured, Mafia dimmed | `B16.5` — the same mount carries both winners, so a rule colouring only one side is caught | suite |
| **§2.2.3** | A `2-2` row still says who won | `B16.8 test_a_two_all_row_still_says_which_side_won` — asserts the *Mafia* cell is the bold/coloured one, so a marking on the wrong cell fails rather than passing | suite |
| **§2.2.4** | A row highlighted in a side's colour opens into a transcript whose winning side's lines are that colour | `B16.9 test_the_list_and_the_reading_view_speak_the_same_two_colours` [both themes] — reads the winning cell's **resolved** colour off an ordinary row, opens a game by the real key route, resolves `TranscriptScreen._kind_styles()` for the same side and compares the two hexes, with a non-vacuity guard that the two sides differ | suite (crosses the seam between two stylesheets) |
| **§2.2.5** | The winner is named nowhere on the row in words or letters | `B16.5` asserts the rows' text is exactly what Slice 1 left (`_panel_labels == _MARKING_ROWS`), so the marking added no glyph; `B16.1` / `B16.3` add the "game" absence | suite |
| **§2.2.6** | Mafia win → Mafia count bold, Law-abiding count not | `B16.5` — `winner.bold` **and** `_bold_cells([prefix, loser, separator]) == []` | suite |
| **§2.2.7** | On the row the reviewer moves onto, exactly one count is bold, and it is the winner's | `B16.6 test_exactly_one_count_is_bold_on_the_row_the_reviewer_is_on` [both themes, **both focus states**] — trap (i)'s witness, mutation-verified: deleting the `text-style: not bold` clears takes the focused row from 1-of-4 bold to 4-of-4. `B16.8` supplies the parity case where the digit alone cannot identify the cell | suite (mutation-verified) |
| **§2.2.8** | Both counts stay legible against the mark | `B16.7 test_both_counts_stay_legible_on_the_row_the_reviewer_is_on` [both themes, both focus states] — every segment of the marked row at ≥3:1, with a guard that the mark is genuinely painted; trap (ii)'s witness, mutation-verified at 2.57:1 | suite (mutation-verified) |
| **§2.3.1** | A header line names the three columns | `B16.10 test_the_header_labels_sit_over_the_columns_they_name` — asserts the header's `L`, `-`, `M` land on the very screen columns Textual laid the row's cells out in, over two rows chosen as the grid's extremes | suite |
| **§2.3.2** | After scrolling a 50-game list the header is still visible and no row is lost | `B16.11 test_the_header_survives_a_scroll_and_displaces_no_game` — walks to the last of 50 games by the reviewer's keys, asserts the scroll really moved, the header's region is unchanged, and it overlaps the list nowhere | suite — **and by construction**: the header is a `Static` **sibling** of the `ListView`, so it is not in the scrolling region at all |
| **§2.3.3** | Every game is still listed; the header displaced none | `B16.11` — `_panel_labels == expected_rows` over all 50, plus the length equality | suite |
| **§2.3.4** | The strip's title reads "Games" | `B16.13 test_the_games_strip_is_titled_games` — asserted in **both** states, which pins a reading of a genuine ambiguity: §2.3 asks for the title on "the strip the games are listed in" while §2.6's last criterion enumerates what an empty run must not show and names the title neither way. The implementation sets it unconditionally and this test pins that, so dropping it from an empty run later is a deliberate change rather than a surprise failure | suite (pins an ambiguity, see §4.3) |
| **§2.4.1** | Games 3, 4 and 5 of the 2026-06-19 run show a day count and a dash | `B16.4 test_a_game_with_no_recorded_ending_shows_a_dash_not_a_pair` — all three row shapes in one mount, because the claim is that they *differ* | suite (fixture) + real corpus (§4.2) |
| **§2.4.2** | No part of such a row is coloured as a winning side | **Holds by construction, and measured — but no suite witness.** See the verification note under §2.4: mutation-proven that painting the dash in the Mafia hue leaves the suite green | **by construction + measurement; NOT coverage** |
| **§2.4.3** | Such a row shows no pair of survivor numbers at all | `B16.4` — an explicit `not any(char.isdigit() …)` over what follows the day count, so a `0-0` that would read as real standings is caught | suite |
| **§2.4.4** | Game 2 of the same run shows counts normally | `B16.4` — the ordinary row sits beside the dash row in the same equality | suite (fixture) + real corpus (§4.2) |
| **§2.5.1** | The oldest run with preserved games carries the numbers like any other | Real corpus, measured today (§4.2): the oldest is `2026-06-19T18-33-37`, it is named by a record and reachable, and all 10 of its rows carry a day count. Verified by eye in the real viewer at Slice 1 Task 1.7(c) | real corpus + by-eye (no suite witness — a test of the corpus rather than of the panel, deliberately, per B16's banner) |
| **§2.5.2** | No preserved game's text has been altered | `git diff 9489e18..HEAD -- evals/` is **empty** across all four of this spec's commits, and `git status` is clean. Suite: `B10 test_browsing_transcripts_leaves_files_byte_unchanged` — materially stronger evidence now than before, because `list_transcripts` reads every file it lists, which it never did until this spec | git evidence + suite |
| **§2.5.3** | No run re-run, no recorded numbers changed | The same empty `git diff` over `evals/` — `evals/blunder-ledger.yaml` is untouched | git evidence |
| **§2.6.1** | Moving up and down the rows and opening one opens the row they were on | `B10 test_selecting_a_game_shows_its_transcript_text[0,1,2]` (pre-existing, now running against the segmented row) — highlights by real keystrokes and asserts the opened game's marker is present and the other two are not; `B12 test_down_up_with_the_panel_focused_move_the_highlight_only` carries the **`up`** half | suite (pre-existing) |
| **§2.6.2** | Coming back leaves them on the row they left | `B13 test_returning_from_a_game_leaves_the_same_entry_highlighted` (pre-existing) — on game 3 of 4 deliberately, since index 0 is what a rebuilt list would also report | suite (pre-existing) |
| **§2.6.3** | The games are in ascending number order | `B16.11` over 50 rows and `B16.3` over the six-row golden, both as ordered equalities | suite |
| **§2.6.4** | A run with no preserved games shows the same note, and no header | `B16.12 test_a_run_with_no_games_shows_the_note_and_no_header` — pins the header as taking **no row** rather than painting blank, by asserting the note sits in the panel's first inner row | suite |

### 4.2 What was checked against the real corpus rather than against a fixture

B16's fixtures are built to a stated outcome and never copied out of `evals/transcripts/`, deliberately: a panel test that read the corpus would skip wherever the corpus was absent and would go red the day somebody curated a run away. That makes the *golden* rows fixture-shaped, so the real games behind them were re-derived directly today, through `list_transcripts` on the committed ledger:

- **The 50-game run `2026-09-14T19-40-56-df63a2`** reads `01 3d 2-2`, `02 2d 2-2`, `03 3d 2-2`, `04 3d 2-2`, `05 2d 4-0`, `06 3d 2-2` — §2.1's golden, exactly. Game 5's winner derives as `law_abiding`; every other one of the six as `mafia`.
- **Game 1 of that run** carries **3** `<day>` blocks and **4** `<night>` blocks, with `• Night 4:` among its death bullets — three Days played, then a fourth Night. Its row reads `3`. §2.1's fourth criterion, on the real game the criterion names.
- **The 10-game run `2026-06-19T18-33-37`** reads `01 2d 2-2`, `02 2d 2-2`, then `2d —` for games **03 through 09**, and `10 2d 2-2`. Games 3, 4 and 5 are dash rows and game 2 shows counts normally — §2.4's first and fourth criteria, on the real run.
- **The no-ending games number exactly 10 across the whole store**, in two June-2026 runs (`2026-06-19T18-33-37` ×7 and `2026-06-20T14-17-09` ×3) — which is what §2.4's prose claims, confirmed.

**Two things in the implementation have no real-data instance at all, and are witnessed by fixtures only.** Neither is the subject of an acceptance criterion; both are named so that they are not mistaken for tested-against-reality:

- **A two-digit day count.** The longest preserved game is **4** Days (distribution over the reachable store: 1 Day ×80, 2 ×261, 3 ×218, 4 ×19). The 12-Day rows in `B16.2` and `B16.10`, which are what prove the number and day columns are right-aligned rather than ragged, exist nowhere in the corpus.
- **The "no summary at all → the number alone" row.** **Zero** preserved games are in that state; every one of the 583 yields at least a day count. `B16.4`'s third row reaches it with `_TRANSCRIPT_BODY`, which carries no `<day>` marker. It is a real defensive path (an unreadable or truncated file lands there through `read_transcript` → `""` → `None`) but it has never fired on real data.

### 4.3 Judgements recorded rather than implied

- **§2.5's "all 583" is not deliverable; its three criteria still are.** The full finding is in the verification note under §2.5: 583 games on disk, all 583 read by the derivation, **578** reachable by the viewer, 5 stranded in two directories no ledger record names. Recorded there rather than here because a reader of the requirement needs to meet it there.
- **§2.4's second criterion is met but uncovered.** Recorded in the note under §2.4; the mutation that proves the gap is stated with it. This is the single criterion in this spec that spec 040's "holds by construction, logged rather than counted" treatment applies to.
- **§2.3's fourth criterion pins a reading of an ambiguity the spec does not settle** — whether the "Games" title survives a run with no preserved games. `B16.13` pins *unconditional*, and says so in its own docstring.
- **§2.6's witnesses are largely pre-existing**, and that is the point: §2.6 asks that nothing a reviewer already does broke. `B10`, `B12` and `B13` were written for specs 017 and 037 and now run against the segmented row unchanged in substance.

### 4.4 The one test surface that did not stay byte-identical, and why it does not weaken §2.1.6

Slice 1's standing rule asked that `test_details_pane_width_is_identical_with_and_without_transcripts` and `test_panel_width_is_fixed_not_derived_from_its_content` stay byte-identical and green. `test_panel_width_is_fixed_not_derived_from_its_content` **is** byte-identical — it does not appear in `git diff 9489e18..HEAD` at all. The other has **exactly one line changed**:

```
-        assert _panel_labels(screen) == ["game-01", "game-02"]
+        assert _panel_labels(screen) == ["01", "02"]
```

That is the test's *premise* — that the run really has two listed games — restated in the row shape this spec introduced, and it is one of the seven `_panel_labels` call sites the technical considerations' risk table enumerated in advance. **Both geometry assertions and the pinned `_EXPECTED_PANE_GEOMETRY` constant are untouched**, which is what §2.1's last criterion actually rests on: the before-and-after comparison is expressed as a constant that did not move while the tests went on passing on both sides of the change.

### 4.5 The counts

| | |
|---|---|
| Acceptance criteria | **29** |
| Marked `[x]` | **29** |
| Witnessed by the suite | **28** |
| …of which the suite witness runs against the **real corpus** | 1 (§2.1.4, via oracle (d) over all 583 games) |
| …of which the suite witness runs against a **fixture**, with the real corpus verified separately | 6 (§2.1.1, §2.1.2, §2.1.3, §2.4.1, §2.4.4, and §2.1.4's synthetic half) |
| …of which the witness **predates this spec** | 3 (§2.1.6, §2.6.1, §2.6.2) |
| Also true **by construction**, with a suite witness besides | 1 (§2.3.2 — the header is the list's sibling) |
| **By construction and measurement only, NOT counted as coverage** | **1 (§2.4.2)** |
| Carried by **git evidence** rather than by the suite | 2 (§2.5.2 — with a suite witness beside it — and §2.5.3) |
| Carried by **real-corpus measurement and by-eye verification**, no suite witness | 1 (§2.5.1) |
| Left unmarked | **0** |

**Nothing was left unmarked.** The two places where an honest answer is not a simple tick — §2.4's uncovered second criterion and §2.5's undeliverable "583" — are recorded as notes at the requirements themselves rather than as failures, because in both cases the criterion as written is true and it is the *evidence* or the *surrounding prose* that needed stating.
