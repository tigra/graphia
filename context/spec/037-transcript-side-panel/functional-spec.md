# Functional Specification: Transcript List as a Side Panel in the Eval-Results Viewer

- **Roadmap Item:** Tooling / eval-review ergonomics — the **"Split-panel view-ledger (transcripts beside the YAML)"** backlog item graduating to a spec. Refines the reviewing experience built by **spec 012** (*Eval Ledger Viewer*) over the transcripts preserved by **spec 017** (*Eval Transcript Preservation*). Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

> **Sibling in-flight spec:** **036 (Persona-Generation Measurements Join the Tracked Quality History)** is also open. 036 adds a new *kind of entry* to the results history; this spec changes how any entry's **transcripts** are reached. They touch the same viewer but not the same surface — 036 concerns what a row contains, this concerns the layout beside it.

---

## 1. Overview and Rationale (The "Why")

Reviewing a measured run means moving constantly between two things: the **numbers** for the run, and the **games** those numbers came from. A rate like "more than half of all lines were near-duplicates" only becomes meaningful once you read a game and see it happening.

Today those two things live on separate screens stacked one behind the other. From a run's details you press a key to open a **list of that run's games**, pick one, and read it; to get back to the numbers you retreat two screens. The list screen in the middle earns nothing — it is a menu you pass through, and it hides the numbers you were just looking at while you use it. Comparing several games against the same figures means walking that path repeatedly and holding the numbers in your head.

This change removes the middle screen. A run's games become a **narrow list permanently visible alongside the run's details**, so the numbers and the games that produced them are on screen together. Moving into the list is one keypress, moving back is one keypress, and the numbers never leave view.

**Success looks like:** a reviewer opens a measured run, sees its games listed beside its figures without pressing anything, steps into the list and back with single keypresses, opens any game to read it and returns to exactly where they were — and never passes through an intermediate menu again.

---

## 2. Functional Requirements (The "What")

- **A run's games are listed in a narrow panel beside its details, always visible.**
  - When a reviewer opens a measured run, the run's preserved games are listed in a narrow strip down the **right-hand side**, with the run's figures filling the wider area to its left. Nothing needs pressing to reveal it.
  - **Acceptance Criteria:**
    - [ ] Given a run with preserved games, when the reviewer opens that run, then its games are listed in a narrow panel on the right and the run's figures are shown to the left of it, both visible at once.
    - [ ] Given the reviewer is looking at a run, when they have pressed nothing since opening it, then the panel is already there — it is not something they have to summon.
    - [ ] Given a run with many games, when the list is longer than the panel is tall, then the list can be moved through to reach every game.

- **One keypress moves between the figures and the list, in both directions.**
  - Focus starts on the run's figures. Pressing **right** moves it into the games list; pressing **left** moves it back to the figures. Pressing right while already in the list, or left while already on the figures, does nothing — it never wraps around or leaves the screen.
  - **Acceptance Criteria:**
    - [ ] Given focus is on the run's figures, when the reviewer presses right, then focus moves to the games list and it is visibly the active area.
    - [ ] Given focus is on the games list, when the reviewer presses left, then focus returns to the run's figures and they are visibly the active area.
    - [ ] Given focus is already on the games list, when the reviewer presses right, then nothing happens — focus stays in the list and the screen does not change.
    - [ ] Given focus is already on the run's figures, when the reviewer presses left, then nothing happens — focus stays on the figures and the screen does not change.

- **Up and down act on whichever area has focus.**
  - With focus on the games list, up and down move the highlighted game. With focus on the run's figures, up and down scroll the figures. The same two keys serve both, according to where focus is.
  - **Acceptance Criteria:**
    - [ ] Given focus is on the games list, when the reviewer presses down then up, then the highlighted game moves down one and back up one, and the figures do not scroll.
    - [ ] Given focus is on the run's figures and they are taller than the screen, when the reviewer presses down then up, then the figures scroll down and back up, and the highlighted game does not change.

- **The dedicated transcripts key still works, as a shortcut into the panel.**
  - The key that used to open the separate games list now moves focus straight into the panel — the same place the right key reaches. It no longer opens a screen of its own.
  - **Acceptance Criteria:**
    - [ ] Given focus is on the run's figures, when the reviewer presses the transcripts key, then focus moves into the games list and no new screen opens.
    - [ ] Given the reviewer presses the transcripts key, when they look at the screen, then the run's figures are still visible beside the list — nothing has been covered up.

- **Opening a game shows it full-screen, and coming back restores the reviewer's place.**
  - Choosing a game from the list opens that game's full text using the whole screen, exactly as it does today — a game is long, and reading it deserves the full width and height. Leaving that view returns to the figures-plus-panel view with the same game still highlighted.
  - **Acceptance Criteria:**
    - [ ] Given focus is on the games list with a game highlighted, when the reviewer chooses it, then that game's full text fills the screen.
    - [ ] Given the reviewer is reading a game's full text, when they go back, then they return to the run's figures with the games panel visible and **the same game still highlighted**.
    - [ ] Given the reviewer returns from a game and moves down the list and opens the next game, then that next game's text is shown — moving between games takes two keypresses, with no menu in between.

- **The intermediate games-list screen is gone.**
  - There is no longer a separate screen that only lists a run's games. The panel replaces it entirely, so no route through the viewer passes through such a screen.
  - **Acceptance Criteria:**
    - [ ] Given the reviewer is anywhere in the viewer, when they navigate by any available key, then they never arrive at a screen whose only content is a list of a run's games.
    - [ ] Given the reviewer opens a game and goes back, then they land on the run's figures — not on a list screen they then have to leave as well.

- **Runs with no preserved games keep the panel, with a short explanation.**
  - Older runs were measured before games were preserved, so they have none. For those the panel stays exactly where it is and shows a brief note saying this run has no preserved games. The layout does not change as the reviewer moves between runs that have games and runs that do not.
  - **Acceptance Criteria:**
    - [ ] Given a run with no preserved games, when the reviewer opens it, then the panel is present in the same place and shows a short note that this run has no preserved games.
    - [ ] Given a run with no preserved games, when the reviewer moves focus into the panel and presses up or down, then nothing happens and no error appears.
    - [ ] Given the reviewer moves between a run with games and a run without, then the width of the figures area stays the same — the layout does not jump.

- **Everything else about the viewer is unchanged.**
  - The list of runs, the figures shown for each run, and the way a game's text is presented all behave exactly as before. This changes only how a run's games are reached.
  - **Acceptance Criteria:**
    - [ ] Given the reviewer is on the list of runs, when they look at it, then it shows the same runs and the same columns as before this change.
    - [ ] Given the reviewer opens a game's full text, when they read and scroll it, then it looks and behaves exactly as it did before this change.
    - [ ] Given a reviewer used the viewer before this change, when they use it after, then the only difference they encounter is that a run's games are beside the figures rather than behind a menu.

---

## 3. Scope and Boundaries

### In-Scope

- Showing a run's preserved games as a narrow, always-visible list beside that run's figures.
- Moving focus between the figures and the list with single left/right keypresses, with up and down acting on whichever has focus.
- Keeping the existing transcripts key as a shortcut that moves focus into the panel.
- Opening a chosen game full-screen and returning with the reviewer's place preserved.
- Removing the separate games-list screen.
- A short in-panel note for runs that have no preserved games.

### Out-of-Scope

- **Changing how a game's text is presented** once opened — the reading view is untouched.
- **Changing what the list of runs shows**, including which figures appear as columns.
- **Changing how games are labelled in the list** — making an entry identifiable by matchup or per-game outcome instead of a plain number is a separate backlog item (*Titled, stats-labelled transcripts*).
- **Searching or filtering** within the viewer — a separate backlog item.
- **Showing a game's text in the panel's neighbouring pane** instead of full-screen; the reading view stays full-screen.
- **Anything about how games are measured, recorded, or preserved** — this only changes how they are browsed.
- All other roadmap items, which are automatically out-of-scope for this specification.
