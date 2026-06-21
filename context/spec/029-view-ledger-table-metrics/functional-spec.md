# Functional Specification: Show Newer Eval Metrics in the view-ledger List

- **Roadmap Item:** Eval-tooling refinement to the ledger viewer (`make view-ledger`, spec 012); not a distinct roadmap phase item.
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The ledger viewer (`make view-ledger`) presents each measured run two ways: a **scrolling list** where every run is one row of compact, at-a-glance columns, and a **per-record detail view** that already shows the run's **complete** recorded data. The detail view is fine — everything tracked is visible there.

The **list**, though, has a fixed column set that predates several metrics the eval now records. So newer tracked numbers — the **scripted-player's-side win rate**, **which stand-in was used (active vs passive)**, and the **full game-resolution outcome (unresolved / runaway, not just wins by side)** — don't appear as columns; to compare them across runs you have to open each run's detail one by one. As metrics get added, the list silently falls further behind what's recorded.

This change **expands the list** with a **curated, explicitly-maintained** set of columns covering the tracked metrics it doesn't yet show, so a reviewer can **scan and compare them at a glance** across runs. The list stays a deliberate, readable summary — each column is a labeled choice, not an auto-dump of every field (the detail view already covers completeness). It is **display-only**: nothing about what the eval records or how the game plays changes.

**Success looks like:** scanning the run list, a reviewer can see and compare — without opening any record — the scripted-player's-side win rate, whether each run used the active or passive stand-in, and whether games resolved (including unresolved/runaway), alongside the existing columns; older runs that predate a metric show blank in that column; and the list stays readable.

---

## 2. Functional Requirements (The "What")

- **The run list shows the scripted-player's-side win rate.**
  - Each run's row includes the win rate of the side the scripted stand-in was on (with the side it refers to), alongside the existing win-by-side and repetition columns.
  - **Acceptance Criteria:**
    - [x] Given a run that recorded a scripted-side win rate, when the reviewer reads the list, then that run's row shows it (rate and the side).
    - [x] Given a run that predates that metric, when the reviewer reads the list, then that run's row shows it as blank, and the row is otherwise intact.

- **The run list shows which stand-in was used (active or passive).**
  - Each run's row indicates whether the active rule-based stand-in or the passive baseline was used — the context needed to interpret that run's outcomes.
  - **Acceptance Criteria:**
    - [x] Given a run recorded with the active (or passive) stand-in, when the reviewer reads the list, then that run's row shows which one.
    - [x] Given a run that predates that setting, when the reviewer reads the list, then that run's row reads as the prior default (passive) or blank, without breaking the row.

- **The run list shows the full game-resolution outcome, not just wins by side.**
  - Each run's row surfaces how its games resolved — wins by side **and** the counts of unresolved (no-winner) and runaway games — so a reviewer sees at a glance whether games actually resolved.
  - **Acceptance Criteria:**
    - [x] Given a run with unresolved and/or runaway games, when the reviewer reads the list, then those counts are visible in that run's row (not only the wins by side).
    - [x] Given a run whose games all resolved to a winner, when the reviewer reads the list, then the row reflects zero unresolved/runaway.

- **The added columns are a curated, explicitly-maintained set.**
  - The list shows a deliberate, labeled set of columns — not every recorded field. Surfacing a future metric in the list is a deliberate follow-up; completeness lives in the detail view, which already shows the whole record.
  - **Acceptance Criteria:**
    - [x] Given the run list, when the reviewer reads it, then each column is clearly labeled and the set is a chosen summary (the list is not an auto-dump of every recorded field).
    - [x] Given the per-record detail view, when this change ships, then it is unchanged (it already shows the complete record).

- **Older records and readability are preserved.**
  - A run that predates any shown metric renders blank in that column rather than erroring, and the list remains readable (columns don't overflow or garble the layout).
  - **Acceptance Criteria:**
    - [x] Given a mix of older and newer runs, when the reviewer scrolls the list, then every row renders without error, missing metrics show blank, and the columns stay aligned and readable.

- **Display-only — no change to recorded data or gameplay.**
  - This only changes what the viewer displays; it does not change what the eval records, the metrics themselves, or how the game plays.
  - **Acceptance Criteria:**
    - [x] Given the same ledger file, when it is opened before and after this change, then the recorded data is identical — only the list's displayed columns differ.

---

## 3. Scope and Boundaries

### In-Scope

- Expanding the `make view-ledger` **list** with a curated, explicitly-maintained set of columns for the tracked metrics it doesn't yet show — at least the **scripted-player's-side win rate**, the **stand-in mode (active/passive)**, and the **game-resolution counts (unresolved / runaway)** — alongside the existing columns.
- Graceful blanks for runs predating a metric, and a list that stays readable.

### Out-of-Scope

- The per-record **detail view** — it already shows the complete record; unchanged.
- **Auto-generating** columns from whatever a record contains — the list is a curated set; future metrics are surfaced deliberately, one at a time.
- Adding any **new eval metric**, or changing how metrics are computed or recorded (those are the metrics' own specs; this only displays what's already recorded).
- The **transcript browser / search** and other viewer areas — this concerns the run list's columns only.
- Any **gameplay** change.
- All other roadmap items, which are automatically out-of-scope for this specification.
