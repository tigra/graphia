# Functional Specification: Searching Inside a Game Transcript

- **Roadmap Item:** _None — tooling._ This is the eval-review layer, not the game; it adds no player-facing capability, so the roadmap is deliberately left untouched, following the convention of specs 041 (*Reliable Structured Decoding*) and 043 (*Keeping What a Stopped Measurement Run Produced*). The request has been logged since **2026-06-20** in `context/product/backlog.md` as *"Search in `view-ledger` (`/` to find)"*, and spec 037 (*Transcript List as a Side Panel in the Eval-Results Viewer*) named *"searching or filtering within the viewer"* as a separate item when it pushed it out of scope. This spec is that item, narrowed to **one surface**: reading a single game.
- **Status:** Draft
- **Author:** Alexey Tigarev

> **Sibling in-flight spec.** **043 (Keeping What a Stopped Measurement Run Produced)** is also open. It changes **which runs leave an entry** in the tracked quality history and how an entry says it ended early; this spec changes **how a reviewer reads one game's text** once it is open. They meet only in the sense that both are reached through the same viewer — 043 never touches the reading view, and this spec never touches an entry.

> **Builds directly on two completed specs.** Spec 017 (*Eval Transcript Preservation*) is what makes a game readable at all; spec 038 (*Colour-Coded Transcript Reading View*) is what gives every line its meaning-carrying colour. This change must not cost the reviewer any of that colour — see §2.3.

---

## 1. Overview and Rationale (The "Why")

A preserved game is long. Eight players speak in turn across several Days, each of them also records private thoughts nobody at the table could see, the moderator posts a status recap at the end of every round, and the whole thing opens with a cast list. Reading one end to end takes real time — and reviewers almost never want to.

What a reviewer actually wants is a **moment**. *Where did anyone first accuse Mira?* *Did the word "vote" appear before Day 2?* *Show me every line that mentions the player who was killed on the first Night.* Today the only way to answer any of those is to scroll and read, watching for the word to go past. On a game this long that is slow, and it is unreliable in the way scanning always is — the answer can scroll past while the eye is elsewhere, and a reviewer who finds nothing cannot tell whether the game contains nothing or whether they simply missed it.

This matters more than ordinary reading convenience, because **reading games is how the measured numbers get their meaning**. A rate is only interpretable once someone has gone into a game and seen the behaviour it counted. Every review task that starts with "find where this happened" currently starts with a scroll.

The tool that already solves this for long text is the one reviewers reach for outside this viewer: press `/`, type, and watch the matches light up. This change brings that into the game-reading view — and keeps the two properties that make it feel immediate rather than modal: **matches appear as you type**, before you commit to anything, and **the game keeps scrolling under you while you type**, so searching never takes the view hostage.

**Success looks like:** a reviewer with a question about a game presses one key, types a few letters, sees every matching moment light up where it sits, and reaches the one they want — without ever losing their place, and without reading a line they did not want to read.

---

## 2. Functional Requirements (The "What")

### 2.1 One keypress opens a search, and the game does not move

- While reading a game, pressing **`/`** opens a small search prompt. The game stays exactly where it was scrolled to — opening a search never moves the text, and nothing is hidden behind the prompt that the reviewer was mid-way through reading.
- **The key is advertised.** The strip of key hints already along the bottom of the screen gains a "Search" entry beside the existing "Back", so a reviewer who does not know the feature exists can discover it. The `n` and `N` keys below are not advertised there.
- **Acceptance Criteria:**
  - [ ] Given a reviewer is reading a game, when they look at the strip of key hints at the bottom of the screen, then it offers "Search" alongside the existing "Back".
  - [ ] Given a reviewer is reading a game, when they press `/`, then a search prompt appears and whatever they type goes into it.
  - [ ] Given the reviewer has scrolled into the middle of a game, when they press `/`, then the text stays exactly where it was — it does not jump to the top or move at all.
  - [ ] Given the search prompt is open, when the reviewer looks at the game text, then the lines they could read before are still readable — the prompt has not covered the passage they were on.

### 2.2 Matches light up as the reviewer types

- Every time the reviewer adds or removes a character, the highlighting updates immediately to match what is now typed. Whatever matches are currently on screen are marked straight away; the view does not move to hunt for matches elsewhere, so what lights up is what was already in front of the reviewer.
- The search looks at everything the game shows: what players said, their private thoughts, the moderator's lines, the section markers, and the opening cast list.
- **Acceptance Criteria:**
  - [ ] Given the search prompt is open and the visible text contains the word "vote", when the reviewer types `vot`, then every occurrence of those letters on screen is highlighted, without the reviewer pressing anything else.
  - [ ] Given the reviewer has typed `vot` and matches are highlighted, when they type a further `e`, then the highlighting narrows to the occurrences of `vote` only.
  - [ ] Given the reviewer has typed `vote`, when they press Backspace, then the highlighting widens again to the occurrences of `vot`.
  - [ ] Given the reviewer is typing, when matches exist further down the game that are not on screen, then the view still does not move — typing alone never scrolls the game.
  - [ ] Given a reviewer searches for a word that appears in a private thought, when they type it, then that occurrence is highlighted like any other — private thoughts are searched too.
  - [ ] Given a reviewer searches for a player's name that appears in the opening cast list, when they type it, then the cast-list occurrence is highlighted like any other.

### 2.3 Highlighting sits on top of the game's colours, never instead of them

- A highlighted match must stay recognisable as whatever it already was. A Mafioso's line keeps its side's colour, a private thought still reads as private, the reviewer's own seat stays marked as theirs — the match is marked **on top of** that, not by replacing it.
- **Acceptance Criteria:**
  - [ ] Given a match falls inside a line spoken by a Mafia player, when it is highlighted, then the reviewer can still tell that line is a Mafia line.
  - [ ] Given matches fall in lines of different kinds — a spoken line, a private thought, a section marker — when they are highlighted, then the reviewer can still tell the three apart.
  - [ ] Given a match is highlighted, when the reviewer looks at it, then the matched text is legible — it is never marked in a way that makes the characters hard to read.

### 2.4 The game still scrolls while the reviewer is typing

- Searching never takes the view hostage. With the search prompt open and text already typed, **up, down, PageUp, PageDown, Home and End all scroll the game**, and the typed search text is untouched by them. The reviewer can type a word, scroll to a part of the game they had in mind, and see the matches there — all without closing the search.
- Typed characters and Backspace edit the search text; that is the whole of the editing the prompt offers.
- **Acceptance Criteria:**
  - [ ] Given the search prompt is open with `vote` typed, when the reviewer presses PageDown, then the game scrolls down by a screen and the search text still reads `vote`.
  - [ ] Given the reviewer has scrolled with PageDown while searching, when they look at the newly revealed text, then its matches are highlighted too — they did not have to retype anything.
  - [ ] Given the search prompt is open, when the reviewer presses the down key, then the game scrolls down — the key is not consumed by the search prompt.
  - [ ] Given the search prompt is open, when the reviewer presses Home, then the game scrolls to its beginning and the search text is unchanged.
  - [ ] Given the reviewer has scrolled anywhere while searching, when they then type another character, then the search text grows by that character — scrolling did not cost them the prompt.

### 2.5 Enter puts the first match at the top of the screen

- Pressing **Enter** jumps the game so that the **first match at or below the reviewer's current position** sits on the **top line of the screen**. A match already on the top line counts as "at" the current position and is where Enter lands — it does not skip ahead.
- If there is no match at or below the current position, the jump wraps around to the first match in the game, and the reviewer is told that it wrapped, so a jump backwards is never silent.
- After the jump, the search prompt closes and the matches stay highlighted, so the reviewer is reading normally again with every match still visible.
- **Near the end of a game the top line cannot always be reached.** A game stops scrolling when its last line is on screen, so a match inside that final screenful comes to rest as close to the top as the game allows, rather than at the top itself. This is the same limit any reader has — there is nothing after the end of the game to scroll into view.
- **Acceptance Criteria:**
  - [ ] Given the reviewer has typed a word that matches further down the game, when they press Enter, then the game scrolls so that the first match below their current position is on the top line of the screen — or, if that match lies within the last screenful of the game, as close to the top as the game can scroll.
  - [ ] Given a match is already on the top line of the screen, when the reviewer presses Enter, then the game does not move — that match is the one Enter lands on.
  - [ ] Given the reviewer has scrolled past every match in the game, when they press Enter, then the view jumps back to the first match in the game and a message tells them the search wrapped around.
  - [ ] Given the reviewer presses Enter and the jump happens, when they look at the screen, then the search prompt is gone and the matches are still highlighted.
  - [ ] Given the reviewer presses Enter with nothing typed, then nothing moves and the search simply closes.

### 2.6 `n` and `N` walk through the matches afterwards

- With the search closed and matches still highlighted, **`n`** moves to the next match and **`N`** moves to the previous one, each landing on the top line of the screen exactly as Enter does, and subject to the same end-of-game limit described above.
- **A press never stops on the line it started from.** Because several matches can fall on the same line of the screen, `n` and `N` skip past any that share the line the reviewer is already on and go to the next line that has a match — so a press is never spent moving between two matches the reviewer can already see together. (The one place a press still produces no movement is the last screenful of the game, where there is nothing left to scroll into view.) The consequence is deliberate: where a line holds several matches the running count jumps (from "match 4" straight to "match 7"), and the second and later matches on a line are highlighted but are never themselves the current one.
- Both wrap: `n` past the last match returns to the first, `N` before the first match goes to the last, and the reviewer is told when a wrap happens.
- The match the reviewer last landed on is marked more strongly than the others, so they can see which one of the highlighted matches they are currently on.
- **Acceptance Criteria:**
  - [ ] Given the reviewer has jumped to a match with Enter, when they press `n`, then the game moves to the following match with it on the top line.
  - [ ] Given the reviewer has pressed `n` several times, when they press `N`, then the game moves back to the previous match with it on the top line.
  - [ ] Given two matches fall on the same line of the screen and the reviewer is on the first of them, when they press `n`, then the game moves on to the next line that has a match — the view always moves.
  - [ ] Given the reviewer is on the last match in the game, when they press `n`, then the view moves to the first match and a message tells them the search wrapped around.
  - [ ] Given the reviewer is on the first match in the game, when they press `N`, then the view moves to the last match and a message tells them the search wrapped around.
  - [ ] Given the reviewer is sitting on one match among several highlighted on screen, when they look at the screen, then they can tell which one they are currently on.
  - [ ] Given the reviewer has scrolled by hand away from the match they were on, when they press `n`, then the view moves to the first match below where they have scrolled to — `n` follows where they are, not where they last jumped from.
  - [ ] Given no search has been made since the game was opened, when the reviewer presses `n`, then nothing happens and no error appears.

### 2.7 Capital letters narrow the search; all-lowercase matches anything

- A search typed entirely in lowercase matches text in any casing, so `mira` finds *Mira*. Typing any capital letter makes the search exact about case, so `Mira` finds only *Mira* and not *mira*.
- **Acceptance Criteria:**
  - [ ] Given a game containing the name *Mira*, when the reviewer types `mira`, then the occurrences of *Mira* are highlighted.
  - [ ] Given a game containing both *Mira* and the lowercase word *mira* somewhere, when the reviewer types `Mira`, then only the capitalised occurrences are highlighted.
  - [ ] Given the reviewer has typed `mira` and matches are highlighted, when they clear it and type `MIRA`, then only occurrences in that exact casing are highlighted.

### 2.8 The reviewer always knows how the search is doing

- The search prompt shows how many matches the typed text has in this game, and which one the reviewer is currently on once they have jumped. A search with no matches says so plainly.
- The running count is a position among **all** matches, so where a line of the screen holds several it can jump by more than one at a time — the consequence of `n` always moving the view, described above.
- **Acceptance Criteria:**
  - [ ] Given the reviewer has typed a word with several matches in the game, when they look at the prompt, then it tells them how many matches there are.
  - [ ] Given the reviewer has typed something that appears nowhere in the game, when they look at the prompt, then it says there are no matches, nothing is highlighted, and the game has not moved.
  - [ ] Given the reviewer has jumped to a match and pressed `n` twice, when they look at the screen, then they can tell which match of the total they are on.
  - [ ] Given the reviewer types a word with no matches and then presses Enter, then nothing moves and no error appears.

### 2.9 Leaving the search, and leaving the game

- Pressing **Esc** while the search prompt is open closes it and removes the highlighting. The game stays exactly where the reviewer has scrolled it to — Esc abandons the search, not their place in the text.
- Esc with no search open still leaves the game and returns to the run, exactly as it does today. The first Esc closes a search; the next one leaves the game.
- Leaving a game and opening it again starts with no search in progress and nothing highlighted.
- **Acceptance Criteria:**
  - [ ] Given the search prompt is open with text typed and matches highlighted, when the reviewer presses Esc, then the prompt closes, the highlighting is gone, and the game is still at the same place on screen.
  - [ ] Given the reviewer has closed a search with Esc, when they press Esc again, then they leave the game and return to the run they came from — as before this change.
  - [ ] Given no search prompt is open, when the reviewer presses Esc, then they leave the game immediately — the change costs them no extra keypress.
  - [ ] Given the reviewer has searched in a game and left it, when they open that game again, then no search is in progress and nothing is highlighted.
  - [ ] Given the reviewer has jumped to a match with Enter and the prompt has closed, when they press Esc, then the highlighting is cleared and they stay in the game; a further Esc leaves it.

### 2.10 Everything else about reading a game is unchanged

- A reviewer who never presses `/` sees no difference at all. The colours, the scrolling, the way the game is laid out, and the keys that leave it all behave exactly as they do today, and nothing about the stored game changes.
- **Acceptance Criteria:**
  - [ ] Given a reviewer opens a game and never presses `/`, when they read and scroll it, then it looks and behaves exactly as it did before this change.
  - [ ] Given a reviewer opens a game, when they look at it before pressing anything, then nothing about searching is taking up space they were using to read.
  - [ ] Given a reviewer has searched inside a game, when the game is looked at outside the viewer, then its stored text is unchanged.
  - [ ] Given the reviewer is on the list of runs or looking at a run's figures, when they use the viewer there, then it behaves exactly as before — this change reaches only the reading view.

---

## 3. Scope and Boundaries

### In-Scope

- Searching within the full-screen reading view of **one preserved game**.
- `/` to open a search prompt without moving the game.
- Live highlighting of matches as the text is typed, across everything the game shows.
- Scrolling the game with the arrow, page and Home/End keys while the search prompt is open.
- Enter jumping to the first match at or below the current position, landing it on the top line.
- `n` / `N` to step forward and backward through matches afterwards, with wrap-around and a message when a wrap happens.
- Smart case: lowercase matches anything, a capital makes the search exact.
- A match count, a current-match indicator, and a plain "no matches" state.
- Esc to abandon a search without losing the reviewer's place.

### Out-of-Scope

- **The existing filter on the list of runs.** The runs list already has its own `/` box that narrows which runs are listed; it is untouched, and this is a different thing — it searches inside one game's text rather than filtering a list.
- **Searching the run's figures.** The panel of numbers beside a run gains no search.
- **Searching across several games at once** — finding which games mention a word is a separate capability, not this one.
- **Hiding non-matching lines.** This highlights and jumps; it never filters the game down to matching lines only.
- **Pattern searches** — wildcards, regular expressions, whole-word-only, and field-scoped searches (e.g. "only in private thoughts") are all excluded; the reviewer types plain text and it matches plain text.
- **Remembering searches** — no history of past searches, and nothing carries from one game to the next or from one session to the next.
- **Changing how a game is coloured or laid out** (spec 038's reading view) or **how games are reached** (spec 037's side panel).
- **Anything about how games are measured, recorded, or preserved** — this only changes how one is read.
- All other roadmap items, which are automatically out-of-scope for this specification.
