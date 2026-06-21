# Functional Specification: Structured Eval-Transcript Format

- **Roadmap Item:** Eval-transcript tooling refinement — follows **Eval Transcript Preservation (spec 017)** and **Browsable-Transcript Round Labels (spec 021)**; not a distinct roadmap phase item.
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The preserved transcript of each measured (eval) game is read two ways: by a **human reviewer** scanning what the AI actually did, and — in a later phase — by an **automated game-quality judge** that parses the same file. Today the format is uneven and partly relies on whitespace:

- A **vote** is a flat run of doubly-prefixed Moderator lines — `Moderator: Lila has called for a vote to execute Nima.`, then `Moderator: Milo: No`, `Moderator: Hana: No`, …, `Moderator: The tally: 0 Yes, 6 No.`, `Moderator: The vote fails.` — with no single delimited block to point at.
- The **setup roster** and the **end-of-game** results are loose lines, and structure is conveyed by **2- and 4-space alignment indentation**.

This makes a vote (or a kill, or the endgame) tedious to locate for a human and brittle to parse for the judge. This change gives the transcript a **consistent, clearly-delimited block structure**: distinct event types each appear in their own labeled block; formatting is uniform with **no alignment spaces**; and player utterances stay as plain `Name: text` lines. It is **display-only** — nothing about how the game plays changes.

**Success looks like:** a reader (or a parser) can locate each vote, each night kill, each end-of-round recap, the setup, and the endgame as a self-contained labeled block; vote ballots read as plain `Name: Yes/No` lines with no redundant prefix; nothing depends on alignment whitespace; and player utterances are unchanged `Name: text` lines.

**Target shape (illustrative — exact tag names are a build detail):**

```
<vote initiator="Lila" target="Nima">
Milo: No
Hana: No
Ismail: No
Lila: No
Nima: No
Zara: No
tally: 0 Yes, 6 No
outcome: failed
</vote>
```

```
<kill>Nima — Law-abiding Citizen</kill>          (a one-line section, written inline)
<recap>Day 2, 12 PM — 3 Law-abiding Citizens and 1 Mafioso remain. 1 execution vote called today. No one has been executed today.</recap>
```

---

## 2. Functional Requirements (The "What")

- **A vote is a single delimited block.**
  - The block identifies who called the vote and on whom; lists each surviving player's ballot as a plain `Name: Yes/No` line (no `Moderator:` prefix); and states the tally and the outcome (the vote failed, or the executed player and their revealed side).
  - **Acceptance Criteria:**
    - [x] Given a Day in which a vote was called, when the reviewer reads the transcript, then that vote appears as one labeled block containing each player's `Name: Yes/No` ballot, the tally, and the outcome — with no `Moderator:` prefix on those lines.
    - [x] Given a vote that executed someone, when the reviewer reads the vote block, then its outcome names the executed player and their revealed side.

- **The night-kill outcome is its own labeled block.**
  - The overnight result — the killed player and their revealed side — appears as its own labeled (single-line, inline) block within the night, distinct from the pointing rounds.
  - **Acceptance Criteria:**
    - [x] Given a night that ended in a kill, when the reviewer reads that night, then the killed player and their revealed side appear as their own labeled block, separate from the pointing rounds.

- **The end-of-round recap is its own labeled element.**
  - The day-round status recap appears as its own labeled element (distinguishable from an ordinary player or Moderator line), carrying the same content it does today (day, in-world time, living counts by side, votes called today, executed-today).
  - **Acceptance Criteria:**
    - [x] Given a completed Day round, when the reviewer reads the round, then its recap appears as a labeled recap element (not an ordinary `Moderator:` line) and still carries the day, time, counts, votes, and executed-today content.

- **The setup roster is structured per player.**
  - Each player appears as a structured entry (name, role, and persona fields) with no deep alignment indentation.
  - **Acceptance Criteria:**
    - [x] Given the setup, when the reviewer reads it, then each player is a structured entry showing name, role, and persona, with no 2-/4-space alignment indentation.

- **The endgame is one labeled block.**
  - The winner, the full roster, and the end-of-game persona reveal are grouped inside a single labeled endgame block.
  - **Acceptance Criteria:**
    - [x] Given a finished game, when the reviewer reads the end, then the winner, the full roster, and the persona reveal all appear inside one labeled endgame block.

- **Formatting is uniform, with no alignment spaces.**
  - Content lines are flush-left (no decorative/alignment indentation). A section whose content is a single line is written inline as `<tag>…</tag>`; a multi-line section opens and closes on its own lines with flush-left content between.
  - **Acceptance Criteria:**
    - [x] Given any block, when the reviewer reads it, then its content lines start flush-left with no alignment spaces, and any single-line section is written inline as `<tag>…</tag>`.

- **Player utterances are unchanged.**
  - A player's spoken turn remains a plain `Name: text` line.
  - **Acceptance Criteria:**
    - [x] Given any player's speaking turn, when the reviewer reads it, then it appears as a `Name: text` line, exactly as before.

- **No information is lost.**
  - The restructure changes only structure and formatting — every speech, vote, kill, recap, and reveal that appears today still appears.
  - **Acceptance Criteria:**
    - [x] Given the same game's events, when its transcript is rendered after this change, then the same speeches, ballots, kills, recaps, and persona reveals are all present — only their grouping and formatting differ.

---

## 3. Scope and Boundaries

### In-Scope

- Restructuring the preserved eval transcript into consistent, clearly-delimited labeled blocks: a vote block, a night-kill outcome block, an end-of-round recap element, a structured per-player setup, and a single endgame block.
- Uniform formatting: flush-left with no alignment spaces; one-line sections written inline as `<tag>…</tag>`; multi-line sections delimited by their own opening/closing lines.
- Keeping player utterances as `Name: text` lines and rendering vote ballots as `Name: Yes/No` lines (no `Moderator:` prefix).
- Applies to transcripts produced from this change forward.

### Out-of-Scope

- **Changing any in-game text or behavior** — this is display-only (the game plays identically), so it carries **no ablation flag** (display-only changes are exempt under ADR 011).
- Re-rendering eval transcripts already committed to the repository — they keep their current text; the new format applies to transcripts generated from now on.
- The **wording or content** of recaps, speeches, votes, kills, or reveals — only their structure and formatting changes (e.g. the in-world clock from Game-Time in the Recap (spec 020) is reproduced as-is).
- The eval-ledger viewer, the metrics it shows, and how transcripts are linked from it.
- The Phase-7 game-quality judge that will read these transcripts (this spec only makes the transcript it reads structurally cleaner).
- All other roadmap items, which are automatically out-of-scope for this specification.
