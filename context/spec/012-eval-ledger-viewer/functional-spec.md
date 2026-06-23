# Functional Specification: Eval Ledger Viewer

- **Roadmap Item:** Not a roadmap feature — the **reader that spec 011 (AI Blunder Tracking) deliberately deferred**. The quality ledger was built append-only and "no viewer yet"; this increment is that viewer. Off-roadmap tooling, like 011 itself. (Roadmap order unaffected; Phase 5 — Configurable Role Counts / Multi-Round Mafia Consensus — remains the next *feature*.)
- **Status:** Completed *(verified 2026-06-14 — all acceptance criteria met across Slices 1–7; full suite 465 passed; live `make view-ledger` walk passed)*
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The project records every AI-quality measurement as a run in a growing ledger — when it ran, on which model, and the rate (with a confidence interval and sample size) of each watched AI blunder. The point of keeping that history is to *compare*: is repetition worse on the local model? did last week's prompt change move the needle? has a behavior drifted?

Today the only way to read that history is to scroll the raw ledger file top to bottom. Each run is a tall block of indented text, so comparing two runs means scrolling back and forth and holding numbers in your head, and finding a specific run (a particular date, provider, or note) means eyeballing the whole file. The accumulated history is technically there but practically illegible — the payoff of tracking quality over time is locked behind a format built for appending, not for reading.

This increment gives the maintainer a **table view of the ledger inside the terminal**: one row per run, columns for the run's key facts and one column per watched behavior, with headers, scrolling in both directions, a search that narrows the list, and the ability to open any run's complete record. It turns the ledger from a file you append to into a history you can actually *read and scan* — the "baby MLOps" loop made visible.

**Desired outcome:** a maintainer opens the viewer with a single command and can, at a glance, scan every recorded run as a table, scroll a history larger than the screen, search to the runs they care about, and drill into any one run for its full detail — without ever opening the raw file.

**Success is measured by:** with several runs recorded, the maintainer can open the viewer, read the runs as a scrollable table with labelled columns, filter to a subset by typing a query, and open a selected run's full record — all in the terminal, with the ledger left unchanged.

---

## 2. Functional Requirements (The "What")

### 2.1 See every recorded run as a table

- **As the** maintainer, **I want** the ledger shown as a table — one row per run — **so that** I can scan and compare runs at a glance instead of reading indented text blocks.
  - **Acceptance Criteria:**
    - [x] Given the ledger has recorded runs, when the maintainer opens the viewer, then a table fills the terminal with **one row per run**, and every column has a **labelled header**.
    - [x] The columns include the run's **identifying facts** — when it ran, the provider, the model(s), and how many games — and **one column per watched behavior**.
    - [x] Each behavior cell shows, for that run, the **rate, its confidence interval, and the sample** (matches out of total) — together, as available. A behavior the run never had the chance to exercise shows an **empty cell** (distinguishable from a genuine zero).
    - [x] A run recorded from a **modified working copy** (its numbers not tied to a clean recorded version) is **visibly marked** in its row, so a less-trustworthy run is obvious at a glance.
    - [x] A **preview of the run's free-text note** is shown in its own column (truncated to a single line), so a run surfaced by searching its note text is visibly explained at a glance rather than appearing to match on nothing — the complete note remains in the full-record drill-down.
    - [x] The column headers stay **visible while the maintainer scrolls** down through the runs.

### 2.2 Scroll a table larger than the screen

- **As the** maintainer, **I want** to scroll the table in both directions, **so that** a long history or a wide set of columns is fully reachable without truncation.
  - **Acceptance Criteria:**
    - [x] Given more runs than fit on screen, when the maintainer scrolls **down/up**, then earlier/later runs come into view and the headers remain in place.
    - [x] Given more columns than fit on screen, when the maintainer moves the **highlighted cell** left/right (or up/down), then the viewer **scrolls just enough to bring that cell entirely into view** — navigation is "move the highlight, the cell follows into view", not nudging the viewport a character at a time. Headers move in step with their columns.
    - [x] The whole grid scrolls together (there is no separately frozen column) — a deliberate simplicity choice for this increment.
    - [x] The **run table is keyboard-navigable the moment the viewer opens** — the run list (not the search field) receives the arrow keys, so the maintainer can move through runs and open one without first clicking. Reaching the search field is a **deliberate action**, and leaving the search field **returns to the table** rather than closing the viewer.

### 2.3 Search to narrow the runs

- **As the** maintainer, **I want** to type a query and see only the matching runs, **so that** I can focus on a provider, a date, a commit, or a note without hunting.
  - **Acceptance Criteria:**
    - [x] When the maintainer types a search query, then the table shows **only the runs whose details contain that text** (across their facts — e.g. provider, model, date, code version, note) and **hides the rest**.
    - [x] The maintainer can **scope the search to a single field** via a **field selector** beside the search box, defaulting to **All** (every fact). Choosing a field (provider, date, model, commit, branch, games, note, state) restricts matching to **only that field**, so a value that also appears elsewhere (e.g. "ollama" mentioned in a note) no longer over-matches. The field is **chosen, not typed** — there is no need to type a field name into the box (which previously matched nothing until completed).
    - [x] The **selector and the search box are reachable by keyboard** — left/right move between them (the selector sits to the left of the value box) — and the typed value filters live as before. The value is matched literally, so a value that contains a colon (e.g. a model id like `qwen3-coder:30b`) searches as written.
    - [x] The viewer shows **how many runs match** out of the total, so the maintainer knows the filter is active and how much it narrowed.
    - [x] When the maintainer **clears** the query, then **all runs reappear**.
    - [x] Given a query that matches nothing, when it is applied, then the maintainer sees a clear **"no runs match"** indication rather than an empty, unexplained table.

### 2.4 Open a run's full record

- **As the** maintainer, **I want** to open any run's complete record, **so that** I can read everything the row can't fit — full provenance, exact counts, and the note.
  - **Acceptance Criteria:**
    - [x] Given the table is shown, when the maintainer **selects a run**, then a **full-record view** opens showing **everything recorded for that run**: all provenance (code version and clean/dirty state, model fingerprints and server version, the settings used, the metric-definitions version), **every behavior's rate, confidence interval, and exact counts**, the run-quality counts, and the **complete free-text note**.
    - [x] The full-record view is **plainly still the same viewer** (it shows the viewer's name/identity) and **shows how to return** (the back key is visible), so a full-window record never reads like a separate program with no way out.
    - [x] When the maintainer **leaves** the full-record view (by pressing Escape, `q`, or Backspace — "back"), then they **return to the table** in the same place they left it.

### 2.5 Sensible when there is nothing (or little) to show

- **As the** maintainer, **I want** clear behaviour when the ledger is empty or unreadable, **so that** I'm never left staring at a blank screen wondering if it broke.
  - **Acceptance Criteria:**
    - [x] Given the ledger has **no runs yet**, when the maintainer opens the viewer, then a clear **"no runs recorded yet"** message is shown instead of an empty table or a crash.
    - [x] The viewer **never changes the ledger** — opening, scrolling, searching, and drilling in all leave the recorded runs exactly as they were (it is a read-only view).

---

## 3. Scope and Boundaries

### In-Scope

- A **terminal table view** of the eval ledger: one row per recorded run, labelled column headers, the run's identifying facts plus one column per watched behavior (each cell showing rate + confidence interval + sample, when present; empty when the behavior wasn't exercised), a visible mark for runs recorded from a modified working copy, and a single-line **note-preview column** (full note in the drill-down).
- **Vertical and horizontal scrolling** of a table larger than the screen, with headers that stay put vertically and track their columns horizontally, and **keyboard navigation of the table by default** (the run list holds focus; the search field is reached deliberately and exited back to the table).
- **Search that filters** the rows to those matching a typed value, with a **field selector** (default **All** = every fact) to scope matching to one field, keyboard-reachable beside the box — with a match-count indicator and a clear "no matches" state.
- A **full-record drill-down** for a selected run (all provenance, every metric's exact figures, and the full note).
- Clear handling of the **empty ledger**, and a strictly **read-only** posture.

### Out-of-Scope

- **Editing** the ledger or any run from the viewer — it only displays (read-only).
- **Computed comparisons** between runs — diffs, statistical tests, "is A significantly better than B", trend lines, or any chart/graph. The maintainer compares by eye; the viewer's job is to show the numbers (and their confidence intervals) legibly.
- **Sorting, reordering, hiding, or resizing columns**, or saving custom layouts — runs appear in their recorded (chronological) order.
- A **frozen/pinned identity column** during horizontal scroll (the maintainer chose "everything scrolls together").
- **Live auto-refresh** while an eval run is still writing — the viewer is opened to read completed history.
- Viewing **arbitrary files** — this views the project's **eval ledger** specifically, not a general YAML browser.
- **All roadmap items** (Phase 5 Configurable Role Counts & Multi-Round Mafia Consensus; Phase 6 Personas & Async Day Chat; Phase 7 Tool-Use & Expanded Roles) — each its own spec.
