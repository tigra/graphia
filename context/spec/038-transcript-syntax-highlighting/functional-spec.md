# Functional Specification: Colour-Coded Transcript Reading View

- **Roadmap Item:** Tooling / eval-review ergonomics — making a preserved game readable at a glance. Complements the transcripts produced by **spec 017** (*Eval Transcript Preservation*), their structured shape from **spec 022** (*Structured Transcript Format*), and the reading view built by **spec 012** (*Eval Ledger Viewer*). Not a distinct roadmap phase item.
- **Status:** Completed *(verified 2026-09-01 — all 24 acceptance criteria met; suite green at 1561 passed / 1 skipped, +272 tests. One criterion (the person's seat identifiable without inference) was **reworded in place** rather than waived: the marker satisfies it for every transcript written from now on, and the 298 already-committed ones are resolved by a documented fallback that never guesses. Three gaps between §1's prose and what §2 specifies are recorded in `tasks.md` — none is an acceptance criterion.)*
- **Author:** Alexey Tigarev

> **Sibling in-flight specs.** **037 (Transcript List as a Side Panel)** changes how a game is *reached* and explicitly leaves the reading view untouched — this spec is its exact complement, changing only how a game *reads* once opened. **036 (Persona-Generation Measurements Join the Tracked Quality History)** concerns what a results entry contains, and does not overlap.

> **A deliberate trade the author accepted.** Tinting dialogue by the speaker's side makes deception visible while reading, which means a reviewer can no longer read a game as the players experienced it — judging whether an accusation was fair on the evidence available becomes harder, because the answer is now colour-coded. This was raised and the author chose visible sides anyway: spotting a Mafioso steering a vote, or a citizen being laundered, is the more common reviewing task, and the transcript already discloses every role in its cast list and ending.

---

## 1. Overview and Rationale (The "Why")

A preserved game is a long wall of plain text. It mixes things a reviewer reads in completely different ways: the skeleton that marks where a Night ends and a Day begins; seven people talking in turn; each player's private thoughts, which nobody at the table could see; the moderator's factual status recaps; and an opening cast list describing who everyone is.

Right now all of that is the same undifferentiated colour. Finding the moment a vote was called means scanning for it by eye. Telling a spoken line from a private thought means reading far enough into the line to notice. Following a conversation across seven speakers means reading every name prefix. Reviewers do this constantly — reading transcripts is how the measured numbers get their meaning — and the text fights them the whole way.

This change gives each kind of content its own look, so the structure of a game is apparent before any of it is read. The skeleton recedes, speech stands out, private thoughts are unmistakably private, and — the author's explicit choice — **each side is coloured**, so who is deceiving whom is visible rather than deduced. The reviewer's own seat is marked more strongly still, because "what did I see at the time" is the question most often asked of a game.

Nothing about the stored game changes. This is entirely about how it is displayed.

**Success looks like:** a reviewer opens a game and can see, without reading a word, where the Nights and Days fall, which lines are speech and which are private thought, who spoke in sequence, which side each speaker is on, and which seat was their own.

---

## 2. Functional Requirements (The "What")

- **The game's skeleton is visually distinct from its content, and recedes behind it.**
  - The markers that open and close each section — the whole game, the opening cast list, the scene-setting preamble, each Night, each Day, each speaking round, the ending — are shown in a look clearly different from the text inside them, and a quieter one, so they read as scaffolding rather than competing with the game.
  - The single information line at the very top of a game, and the plain round markers inside a Day, are treated as skeleton too.
  - **Acceptance Criteria:**
    - [x] Given a reviewer opens a game, when they look at it without reading, then the section markers are immediately distinguishable from the content between them.
    - [x] Given a reviewer scrolls through a long game, when they scan for where a Night begins, then they can find it by appearance alone, without reading the words.
    - [x] Given the markers and the content are on screen together, when the reviewer reads the content, then the markers are quieter than it and do not draw the eye first.

- **Markers that carry details show those details distinguishably.**
  - Some markers carry specifics — who a cast entry describes and their role, whose private thought follows, who called a vote and against whom. Those details are shown differently from the marker itself, so the specifics are readable at a glance rather than buried in punctuation.
  - **Acceptance Criteria:**
    - [x] Given a cast entry naming a player and their role, when the reviewer looks at it, then the name and the role are distinguishable from the surrounding marker text.
    - [x] Given a vote marker naming who called it and against whom, when the reviewer looks at it, then both names are picked out from the marker.

- **Each side has its own colour, applied to a speaker's name and to what they say.**
  - Mafia players and Law-abiding players are given two clearly different colours. A speaker's name and the words they speak both carry their side's colour, wherever they appear.
  - **Acceptance Criteria:**
    - [x] Given a Day round in which both sides speak, when the reviewer looks at the round, then Mafia lines and Law-abiding lines are visibly different colours.
    - [x] Given a single spoken line, when the reviewer looks at it, then the speaker's name and the words they spoke share that speaker's side colour.
    - [x] Given the same player speaks in several rounds across the game, when the reviewer compares those lines, then that player's colour is the same every time.
    - [x] Given the reviewer looks at the opening cast list, when they read the roles there, then the side colours used in the cast list match the ones used for dialogue.

- **The reviewer's own seat is marked more strongly than the rest of its side.**
  - The seat the person plays is shown in its side's colour, but **bold**, so it stands out from the other players on the same side. In a measured run that seat is occupied by an automated stand-in; it is marked the same way, since it holds the same position in the game.
  - For this to be possible, a game must make its own person's seat identifiable to a reader. Today it is only inferable — from the opening welcome, from a private "you are…" line, and from that seat being the one with no character description. Making it plainly identifiable is part of this change.
  - **Acceptance Criteria:**
    - [x] Given a game in which the person's seat was Law-abiding, when the reviewer looks at that seat's lines, then they are in the Law-abiding colour and bold, while other Law-abiding players' lines are the same colour but not bold.
    - [x] Given a game in which the person's seat was Mafia, when the reviewer looks at that seat's lines, then they are in the Mafia colour and bold.
    - [x] Given a game preserved from this change onward, when a reviewer reads it, then which seat belonged to the person is **stated outright in the file**, without inference or guesswork. *(Reworded in place at verification. It originally read "Given **any** preserved game". The 298 transcripts committed before this change carry no such statement and cannot gain one — this spec's own Out-of-Scope forbids altering a stored game — so for those the seat is resolved from the preamble's `Moderator: A new game begins. Welcome, <name>.` line, verified present exactly once in all 298 and inside `<preamble>` in every one. That fallback identifies a seat in all 298 and never guesses: a seat identifiable by neither route is simply not marked.)*
    - [x] Given a measured run, where the person's seat is played by an automated stand-in, when the reviewer reads it, then that seat is marked in the same bold way.

- **Private thoughts are unmistakably private.**
  - A player's private end-of-round reflection — something no one at the table could see — is given a look that sets it apart from spoken dialogue at a glance, so it can never be mistaken for something said aloud. The owner's name within it follows the same side colouring as their speech.
  - **Acceptance Criteria:**
    - [x] Given a round containing both spoken lines and private thoughts, when the reviewer looks at it, then the thoughts are distinguishable from the speech without reading either.
    - [x] Given a private thought, when the reviewer looks at whose it is, then the owner's name carries that player's side colour.

- **The moderator's status recaps are set apart from opinion.**
  - The moderator's end-of-round status summary is fact — who is alive, how many on each side, what has happened today — and is given its own look, distinct from both the players' speech and their private thoughts.
  - **Acceptance Criteria:**
    - [x] Given a round ending with a moderator status recap, when the reviewer looks at the round, then the recap is visibly a different kind of content from the surrounding speech.
    - [x] Given several rounds each ending in a recap, when the reviewer scrolls the game, then the recaps can be picked out and used as landmarks.

- **The opening cast list's field labels are set apart from their descriptions.**
  - In the cast list each character is described under labels — personality, manner, the public story they tell, and for a Mafioso the hidden truth. Those labels are given their own look so each entry can be skimmed by field rather than read as a paragraph.
  - **Acceptance Criteria:**
    - [x] Given a cast entry with several described fields, when the reviewer looks at it, then the field labels are distinguishable from the descriptions that follow them.
    - [x] Given a Mafioso's entry, which carries both a public story and a hidden truth, when the reviewer looks at it, then those two are separately identifiable.

- **The stored game is never altered, and the view stays readable everywhere.**
  - Colour is applied only to the display. The preserved game itself is untouched — the same words in the same order, byte for byte. The colours must be legible on both dark and light terminals, and where colour is unavailable the game must remain fully readable as plain text rather than showing stray formatting characters.
  - **Acceptance Criteria:**
    - [x] Given a game is opened and read in the viewer, when the stored game is compared with what it was before, then it is unchanged.
    - [x] Given a reviewer uses a light terminal and another uses a dark one, when each reads the same game, then every kind of content is legible for both.
    - [x] Given a setting where colour cannot be shown, when the reviewer opens a game, then the full text is readable as plain text with no leftover formatting characters.

- **Everything else about reading a game is unchanged.**
  - Scrolling, the keys used to move and to leave, and the words on the page all behave exactly as before. Only the appearance changes.
  - **Acceptance Criteria:**
    - [x] Given a reviewer opens a game, when they scroll and then leave, then those actions work exactly as they did before this change.
    - [x] Given a reviewer knew this view before the change, when they use it after, then the only difference is how it looks.

---

## 3. Scope and Boundaries

### In-Scope

- Giving each kind of transcript content its own appearance: section markers, the details those markers carry, spoken lines, private thoughts, moderator status recaps, and cast-list field labels.
- Colouring by side, applied to both a speaker's name and their words, consistently across the game and matching the cast list.
- Marking the person's own seat in bold within its side's colour, and making that seat plainly identifiable in a preserved game.
- Legibility on dark and light terminals, and a clean plain-text fallback where colour is unavailable.

### Out-of-Scope

- **Changing the words, order, or structure of a preserved game** — this is display only. (The one exception is making the person's seat identifiable, which the bold requirement depends on.)
- **Changing how a game is reached or listed** — that is spec 037 (*Transcript List as a Side Panel*).
- **Labelling games by matchup or outcome** in any list — a separate backlog item (*Titled, stats-labelled transcripts*).
- **Colouring anything other than a game's text** — the results table and the record detail view keep their current appearance.
- **Searching, filtering, or folding sections** within a game — separate backlog items.
- **Letting the reviewer choose or configure the colours.**
- **Anything about how games are measured, recorded, or preserved.**
- All other roadmap items, which are automatically out-of-scope for this specification.
