# Functional Specification: Eval Transcript Preservation

- **Roadmap Item:** Phase 6 — **Eval Transcript Preservation** → **Preserved, Browsable Eval Transcripts**. (Eval/quality tooling, in the same family as the AI Blunder Tracking ledger and the Eval Ledger Viewer; the **LLM-as-Judge** that will *read* these transcripts is a separate Phase 7 item.)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

When the project measures AI quality, it plays a batch of real games and records the *numbers* — repetition rate, the blunder counts, win-rate by side — as one row per run in the committed quality ledger. But the games that produced those numbers are thrown away: only the rates survive. So when a number looks off — repetition stuck near a half, the town winning zero of twenty — there is no way to go read *why*. You have the verdict but not the trial.

For honest **human evaluation**, that gap is the whole problem. The open questions about AI quality are answerable only by *reading the games*: is the dialogue genuinely repetitive, or is the metric over-counting? Did the Mafiosos actually hold their cover, or give themselves away? Why does the town never coordinate a winning execution? None of that is in the rates — it's in the conversation.

This increment **preserves the full transcript of each measured (eval) game** and makes it **browsable from the eval-ledger viewer**, right next to that run's metrics. A reviewer opens a run, picks one of its games, and reads the whole thing — the public Day discussion and votes *and* the normally-hidden layers (who was really Mafia, what the Mafiosos privately chose at Night, each player's persona and a Mafioso's true self behind its cover) — because the game is over and the evaluator should see everything. It is a deliberate, narrow **eval-only** exception to the project's rule that full game transcripts are not persisted across sessions; normal play keeps nothing. It also lays the groundwork for the future automated judge (a separate item) by producing exactly the artifact that judge will read.

**Desired outcome:** after a measured run, a reviewer can open the eval-ledger viewer, drill into the run, and read each game's complete, human-readable transcript; the transcripts are kept within the project so the reviewer can choose to share the noteworthy ones with the team.

**Success is measured by:** running a measured batch and then, in the viewer, drilling into that run and reading any one of its games end-to-end — with the hidden roles, private Night choices, and personas all visible — laid out so the Day/Round/Night structure is easy to follow; a run that has no preserved transcripts (an older record) drills in cleanly with a plain "no transcripts" indication rather than an error.

---

## 2. Functional Requirements (The "What")

### 2.1 Each measured game's full transcript is preserved

- **As the** maintainer, **I want** every game in a measured run kept in full, **so that** I can read what actually happened, not just the rates it produced.
  - **Acceptance Criteria:**
    - [ ] Given a measured (eval) run of N games completes, then **each of the N games' full transcripts is preserved** (one transcript per game), associated with that run's ledger record.
    - [ ] A transcript captures the **complete game**: every Day utterance, every Moderator announcement, each vote (who initiated it, every ballot, the outcome), and each Night kill — **and** the normally-hidden material: each player's **true role**, the Mafiosos' **private Night choices**, and the **personas** (a Mafioso's public legend *and* its true self). The reviewer sees everything; nothing is redacted.
    - [ ] A transcript is **human-readable**, organized with clear structural markers — a single transcript wrapper, with nested **Day / Round / Night** sections (e.g. `<transcript>`, `<day>`, `<round>`, `<night>` tags) — so a reader can follow the phase structure at a glance, while the content inside each section reads as plain prose.
    - [ ] All events (utterances, vote initiation, votes, pointing etc.) are preserved in a strict chronological order, how did they happen in a game.
    - [ ] **Eval-only:** transcripts are preserved **only for measured eval runs**. A normal game (`make play`) still preserves nothing across sessions — the standing non-persistence rule is unchanged outside the eval path.

### 2.2 Browse a run's transcripts in the eval-ledger viewer

- **As the** maintainer, **I want** to read a run's game transcripts from the same viewer that shows its metrics, **so that** I can move directly from a number to the game behind it.
  - **Acceptance Criteria:**
    - [ ] Given the eval-ledger viewer is open and a run **with preserved transcripts** is selected, when the maintainer drills into that run, then they can see the run has per-game transcripts and **choose one of its N games to read**.
    - [ ] When the maintainer opens a game's transcript, then the **full transcript is shown in a scrollable, read-only view**, reachable from that run's record (alongside its metrics).
    - [ ] When the maintainer **leaves** the transcript view, then they **return to the run's record** where they were.
    - [ ] Given a run with **no** preserved transcripts (e.g. an older record written before this feature), when the maintainer drills into it, then the viewer shows plainly that **there are no transcripts for that run** — no error, no crash.

### 2.3 Transcripts are shareable at the developer's choice

- **As the** maintainer, **I want** transcripts kept where I can choose to share the noteworthy ones, **so that** a teammate (or, later, an automated judge) can read the same games — without every routine run forcing its transcripts into the shared record.
  - **Acceptance Criteria:**
    - [ ] Preserved transcripts are written to a location **within the project** (alongside the eval results), so the developer can choose to include specific transcripts in the **shared project record**.
    - [ ] The relationship of games to transcripts is easily identifiable from naming; single eval run has its own directory.
    - [ ] The measured run does **not** automatically add transcripts to the shared record — they are written as ordinary files that simply remain until the developer **commits** them or **deletes** them; including a run in the shared record is a **deliberate developer action**.
    - [ ] There is a **one-command cleanup** for transcripts (so the few-game smoke runs don't accumulate): the rule of thumb is to **commit the full, clean runs** worth keeping and **delete the smoke runs** (after confirming they hold no important findings). After a smoke run, the assistant prompts the developer to delete that run's transcripts.
    - [ ] A transcript the developer chooses to share can be read by anyone with the project — both in the viewer and as a plain, readable file on its own.

---

## 3. Scope and Boundaries

### In-Scope

- **Preserving each measured game's full transcript** — the complete game including the hidden layers (true roles, private Night choices, personas with a Mafioso's cover *and* true self), human-readable, organized with `<transcript>` / `<day>` / `<round>` / `<night>` structural markers.
- **Browsing per-game transcripts from the eval-ledger viewer**, reachable from a run's record next to its metrics; scrollable, read-only; with a clean "no transcripts" state for older runs that have none.
- **Keeping transcripts within the project so the developer can choose to share them** (the noteworthy ones), without routine runs auto-forcing them into the shared record.
- **Eval-only:** applies to the measured runs that write the quality ledger; normal play (`make play`) is unchanged.

### Out-of-Scope

- **The LLM-as-Judge** (Phase 7) — this increment only *preserves and surfaces* transcripts; an automated model reading and scoring them is a separate item. (The transcript format is chosen so it can serve that judge later, but no judging is built here.)
- **Preserving transcripts of normal gameplay** — `make play` games remain non-persistent across sessions; this is a narrow eval-only exception to that rule. *(Note: the product definition §3.2 lists full-transcript persistence as out-of-scope; this exception is eval-tooling-only, like the committed quality ledger itself — a product-definition footnote may be warranted later, but the rule for actual gameplay stands.)*
- **The other eval harnesses** (`eval-dialogue`, `repetition-experiment`) — transcript preservation attaches to the ledger-writing eval whose records the viewer shows; the other harnesses are not in scope here.
- **Analytics over transcripts** — searching, diffing, side-by-side comparison, or aggregate analysis across transcripts. This is *read the game*, nothing more.
- **All other roadmap items** — the Day-Round Moderator Recap, AI Personas (private thoughts / diaries), Asynchronous Day Chat, End-of-Game Payoff (Phase 6); and the rest of Phase 7.

---

## 4. Notes

- **Transcripts are visible, not ignored — curated by convention.** Transcripts are written into the project tree as ordinary files (not hidden by an ignore rule) and remain until the developer commits or deletes them. The convention: **commit the full, clean runs** (e.g. the n=20 baselines) worth keeping; **delete the few-game smoke runs** before committing, after confirming they hold no important findings — with the assistant prompting for that cleanup after a smoke run, and a one-command cleanup to make it easy. The deliberate trade-off is **visibility + curation** over a silent ignore. The consequence to manage (see technical spec §3): an uncommitted transcript run left in the tree makes the *next* measured run stamp `dirty: true`, so commit-or-delete *before the next run* keeps eval provenance clean.
