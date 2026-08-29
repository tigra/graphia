# Functional Specification: Persona-Generation Measurements Join the Tracked Quality History

- **Roadmap Item:** Eval-measurement — closing the gap opened by **spec 034** (Diversified Persona Generation), whose isolated character-generation check deliberately records nothing. Relates to the **persona-realism** thread and to the tracked-quality-history discipline established by **spec 011** and browsed by **spec 012**. Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

> **Sibling in-flight spec:** **035 (Bedrock Claude Provider)** is also open. It changes which model the game talks to; this spec changes what gets written into the quality history. They do not overlap, but a measurement recorded after 035 lands should be able to say which model produced it — which the existing record already covers.

> **Adjacent open question (backlog):** *"Display partially-complete eval runs"* asks how a run with missing pieces should be surfaced rather than hidden. A character-generation measurement is structurally the same shape — a record where most game-related figures are simply absent — so the labelling decision here should be the one that item builds on, not a competing answer.

---

## 1. Overview and Rationale (The "Why")

Graphia treats AI quality as a **tracked property of the project**: each measured run adds one dated entry to a quality history that is kept with the code, so quality can be compared over time rather than re-argued from memory. Today only a **full played game** earns an entry.

Spec 034 added a much faster way to measure one specific thing — how varied the AI cast is — by generating characters in isolation, without playing a game at all. It takes about half a minute per cast instead of twenty minutes per game. It was built deliberately to record nothing: it prints its findings to the screen and exits.

That gap has now cost something real. A ten-cast-per-side comparison was run, using both the free word-level measurement and the paid meaning-based one, and it settled a question that had been open across three previous specs: with the variety feature on, casts containing a near-duplicate character fell from **two in ten to none in ten**. That result now exists **only as prose inside a specification document**. It cannot be found in the quality viewer, it carries no record of which version of the game or which model produced it, and **no future measurement can be compared against it**. The single measurement that finally answered the question is the one the project does not remember.

This change lets a character-generation measurement take its place in the tracked history, on the same footing as a played game: dated, attributed to a version of the game and a model, and readable later beside everything else.

Three deliberate choices shape it. Recording is **something the developer asks for**, not something that happens automatically — the speed of this check is the point, most runs are throwaway experiments, and automatic recording would bury the rare real measurement under noise. A recorded measurement is **clearly marked as a character-generation measurement**, so a reader immediately understands why its game-related figures are blank instead of mistaking it for an interrupted game. And the **paid meaning-based measurement stays optional** — an entry can carry the free word-level figures alone, keeping the whole path usable by someone with no cloud access, which the product explicitly values.

**Success looks like:** a developer runs a character-generation measurement worth keeping, asks for it to be recorded, and finds it in the quality viewer afterwards — labelled for what it is, showing the variety figures it took, blank where it took none, carrying the date, the version of the game, and the model that produced it; and a later measurement can be read directly against it.

---

## 2. Functional Requirements (The "What")

- **A character-generation measurement can be added to the tracked quality history on request.**
  - The developer can ask for a particular character-generation run to be kept. When they do, it adds one dated entry to the same quality history that played games write to. When they do not ask, the run behaves exactly as it does today — it reports its findings on screen and leaves the history untouched.
  - **Acceptance Criteria:**
    - [ ] Given a character-generation run the developer has asked to keep, when it finishes, then one new entry for it appears in the quality history, and every entry that was already there is unchanged.
    - [ ] Given a character-generation run the developer has not asked to keep, when it finishes, then it reports its findings on screen and the quality history is completely untouched.
    - [ ] Given several kept runs one after another, when the history is read, then each has added its own entry and none has replaced or rewritten an earlier one.

- **A recorded measurement is clearly marked as a character-generation measurement, not a game.**
  - Because no game was played, the game-related figures — who won, how decisively players behaved, how the voting went — do not exist for this kind of entry. The entry says plainly what kind of measurement it is, so a reader understands the blanks are expected rather than missing.
  - **Acceptance Criteria:**
    - [ ] Given a recorded character-generation measurement, when it is read in the quality viewer, then it is identifiable as a character-generation measurement rather than a played game.
    - [ ] Given a mix of played-game entries and character-generation entries, when the reviewer scrolls the history, then both kinds are readable in the same list, the character-generation ones show blanks where game figures would be, and nothing errors or misaligns.
    - [ ] Given a character-generation entry, when a reader looks at its blank game figures, then it is clear these are absent because no game was played — not because a game failed part-way.

- **The entry records the variety figures that were actually taken, and shows nothing for those that were not.**
  - A measurement always takes the free word-level variety figures. It takes the paid meaning-based ones only when the developer chose to. The entry carries whichever were taken and simply shows nothing for the others.
  - **Acceptance Criteria:**
    - [ ] Given a measurement that took only the free word-level figures, when its entry is read, then those figures are present and the meaning-based ones show nothing — not a zero, and not an error.
    - [ ] Given a measurement that took both the free and the paid figures, when its entry is read, then all of them are present.
    - [ ] Given a measurement where the paid figures could not be taken because cloud access was unavailable, when the run finishes, then it still completes, still reports its findings, and can still be recorded — the missing figures are simply absent.

- **The entry records how many casts were measured, and how many contained an over-similar pair.**
  - A variety figure is meaningless without knowing how much was measured. The entry says how many casts the measurement covered and how many of them ended up containing a pair of characters judged too alike — the count that carried the decisive result in the spec-034 comparison.
  - **Acceptance Criteria:**
    - [ ] Given a recorded measurement, when its entry is read, then it states how many casts were measured.
    - [ ] Given a recorded measurement, when its entry is read, then it states how many of those casts contained an over-similar pair.

- **The entry records the conditions the measurement ran under, so two measurements can be compared honestly.**
  - An entry says which model generated the characters, whether the variety feature was on or off, and the sensitivity setting used to judge two characters too alike — because a comparison between two measurements only means something when these match, and a side-by-side comparison is only readable as a pair when each side says which side it was.
  - **Acceptance Criteria:**
    - [ ] Given two entries from a side-by-side comparison, when they are read, then each states whether the variety feature was on or off for that run.
    - [ ] Given a recorded measurement, when its entry is read, then it states which model generated the characters and the sensitivity setting used to judge characters too alike.
    - [ ] Given a recorded measurement, when its entry is read, then it carries the date and the version of the game that produced it, matching what a played-game entry carries.

- **The developer can attach a note explaining what a measurement was for.**
  - Numbers alone do not say why a measurement was taken. The developer can attach a short note — the same way they can for a played game — so a reader months later knows what question it was answering.
  - **Acceptance Criteria:**
    - [ ] Given a measurement recorded with a note, when its entry is read, then the note is shown with it.
    - [ ] Given a measurement recorded without a note, when its entry is read, then it reads normally with no note and nothing missing.

- **Recording never disturbs what is already in the history, and never happens by accident.**
  - The quality history is kept with the project and is only ever added to. A character-generation measurement must not rewrite, reorder, or damage anything already recorded — and a routine test run of the project must never add anything to it.
  - **Acceptance Criteria:**
    - [ ] Given an existing history, when a character-generation measurement is recorded, then every earlier entry is byte-for-byte unchanged and only the new entry has been added.
    - [ ] Given the project's routine tests are run, when they finish, then the real quality history is completely untouched.
    - [ ] Given a recorded measurement, when the history is opened in the quality viewer afterwards, then it loads without error and displays every entry, old and new.

---

## 3. Scope and Boundaries

### In-Scope

- Letting a character-generation measurement add one entry to the tracked quality history, **only when the developer asks for it**.
- Marking that entry clearly as a character-generation measurement so its blank game figures read as expected rather than broken.
- Recording the variety figures actually taken (free word-level always; paid meaning-based when chosen), how many casts were measured, and how many contained an over-similar pair.
- Recording the conditions — model, variety feature on or off, sensitivity setting — plus the date and version of the game, and an optional note.
- Showing these entries in the existing quality viewer alongside played-game entries.

### Out-of-Scope

- **Changing how characters are generated, or how varied they are** — spec 034 owns that. This only records what a measurement found.
- **Changing the existing variety measurements themselves** (specs 031, 032, 033) — this records them, it does not redefine them.
- **Recording automatically**, whether always or above some size threshold — recording is something the developer asks for, deliberately.
- **Filling in past measurements** already taken and written up in specification documents, including the spec-034 comparison that motivated this. Those stay where they are; this changes what happens from now on. *(Whether to backfill that one comparison is a separate decision.)*
- **Changing how played-game entries look or what they contain.**
- **A separate history** for character-generation measurements — they join the existing one.
- **Judging or acting on** what a measurement shows — no alerting, no pass/fail gate, no automatic regeneration triggered by a recorded figure.
- **How runs that were interrupted part-way should be surfaced** — a related open question in the backlog, deliberately left to be answered on its own.
- All other roadmap items, which are automatically out-of-scope for this specification.
