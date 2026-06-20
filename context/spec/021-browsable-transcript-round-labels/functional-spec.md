# Functional Specification: Browsable-Transcript Round Labels

- **Roadmap Item:** Phase 6 → Browsable-Transcript Round Labels → **Per-Round Transcript Labels**
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

When a measured (eval) game is played, its full game transcript is preserved as a readable text file and made browsable next to that game's recorded metrics. A human reviewer opens it to read what the AI players actually said during a measured game, and the future game-quality judge will read the same files.

During the Day phase a game runs several **speaking rounds**. Each round is one full cycle in which every surviving player speaks once, and the Moderator closes each round with a short public status recap of where the game stands.

Today the transcript collapses all of a Day's speaking rounds into a **single block labeled "Round 1."** A Day that actually ran six rounds reads as one oversized "Round 1" with six recaps stacked inside it. This misrepresents the game's structure: a reviewer cannot tell how many rounds the Day really took or which recap belongs to which round, and an automated judge reading these files would be misled in the same way.

**Desired outcome:** the preserved transcript's round structure matches the game's real round structure one-to-one. Each speaking round appears as its own labeled block carrying its true round number, and each Moderator recap sits inside the round it summarizes.

**Success measure:** for any preserved transcript, a reviewer can count the round labels in a Day and get the actual number of speaking rounds that Day ran, and every recap is attributable to a specific round.

---

## 2. Functional Requirements (The "What")

### 2.1 Each speaking round is its own labeled block

- Within a Day, every speaking round (one full cycle where each surviving player speaks once) appears as its own labeled block with a "Round N" label.
- Round numbers **reset to 1 at the start of each Day** and increase by one for each subsequent speaking round in that Day.
  - **Acceptance Criteria:**
    - [x] Given a preserved transcript of a Day that ran six speaking rounds before ending, when the reviewer reads that Day's section, then they see six separate round blocks labeled Round 1 through Round 6.
    - [x] Given a transcript with more than one Day, when the reviewer reads the second Day's section, then its first round is labeled Round 1 (numbering restarts each Day).
    - [x] Given any Day section, when the reviewer counts the "Round N" labels, then the count equals the number of speaking rounds the game actually played that Day.

### 2.2 Each round block holds only that round's events

- A round block contains the player speeches of that one speaking cycle, plus any execution vote called during that round and its outcome.
  - **Acceptance Criteria:**
    - [x] Given a round in which no vote was called, when the reviewer reads that round's block, then it contains exactly one turn of speech per surviving player and nothing from any other round.
    - [x] Given a round during which a player called an execution vote, when the reviewer reads that round's block, then the vote and its result appear inside that same round block.

### 2.3 Each Moderator recap closes the round it summarizes

- The end-of-round Moderator status recap appears as the **last line inside the round block whose state it describes**.
- This spec governs only *where* the recap sits, not its wording. The recap's content — including any in-world time marker added by **Game-Time in the Recap (spec 020)** — is owned by the recap and reproduced as-is.
  - **Acceptance Criteria:**
    - [x] Given a round that ended with a Moderator status recap, when the reviewer reads that round's block, then the recap is the final line of that block (after that round's speeches).
    - [x] Given a Day with multiple rounds, when the reviewer reads the recaps, then there is at most one closing recap per round block and no round block contains another round's recap.

### 2.4 Day-opening announcement stays before the first round

- The Moderator's start-of-Day announcement (who was killed overnight and their revealed role) appears before the first round block, not inside any round.
  - **Acceptance Criteria:**
    - [x] Given the start of a Day, when the reviewer reads the Day section, then the overnight-death announcement appears above the Round 1 block.

### 2.5 Day endings land in the final round

- When a Day ends because an execution vote passed, the deciding vote and the execution announcement appear inside the final round's block (the round during which the vote passed); no empty round block follows.
- When a Day ends with no execution, the "Day ends with no one executed" line and the Day's final status recap appear inside the final round's block.
  - **Acceptance Criteria:**
    - [x] Given a Day that ended in an execution, when the reviewer reads the last round block, then the deciding vote and the execution announcement are inside it, and no empty round block follows.
    - [x] Given a Day that ended with no execution, when the reviewer reads the last round block, then the "Day ends with no one executed" line and the final recap are inside it.

---

## 3. Scope and Boundaries

### In-Scope

- Per-round labels and per-round blocks for **Day-phase speaking rounds** in the preserved eval transcripts, with round numbers reset per Day.
- Placing each end-of-round Moderator recap inside the round it summarizes, reproducing the recap's wording as the game emits it.
- Correct placement of the Day-opening announcement and the Day-ending lines relative to round blocks.
- Applies to transcripts produced by future measured (eval) runs.

### Out-of-Scope

- **Night pointing rounds** — these are already labeled per round ("Pointing round 1", "Pointing round 2", …) and are unchanged.
- **Re-rendering transcript files already committed to the repository** — existing preserved transcripts keep their current text; the new labeling applies to transcripts generated from this point forward.
- **The wording or content of the recap, speeches, votes, or roles** shown in the transcript — only the round labeling/grouping changes. In particular, the in-world clock from **Game-Time in the Recap (spec 020)** is reproduced as-is, not defined or altered here.
- **Feeding the recap into AI reasoning** — that is the sibling spec, **Recap-Aware AI Reasoning (spec 019)**, which concerns what AI players are given in their prompts, not how the transcript is displayed.
- The eval-ledger viewer itself, the metrics it shows, and how transcripts are linked from it.
- The Phase-7 game-quality judge that will later read these transcripts (this spec only makes the transcripts it will read structurally accurate).
- All other roadmap items, which are automatically out-of-scope for this specification.
