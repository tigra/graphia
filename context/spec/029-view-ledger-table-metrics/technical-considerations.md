# Technical Specification: Show Newer Eval Metrics in the view-ledger List

- **Functional Specification:** `context/spec/029-view-ledger-table-metrics/functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

This is a **small, display-only** change to the `make view-ledger` viewer (spec 012, _Eval Ledger Viewer_). It adds a **curated set of list columns** for tracked metrics the table doesn't yet surface — the scripted-side win rate, the stand-in mode (active/passive), and the full game-resolution outcome (unresolved/runaway, not only wins-by-side) — alongside the existing columns. Nothing about what the eval records, how metrics are computed, or how the game plays changes.

**Verified — where columns actually live.** The table's column set is declared as **data in the pure layer `src/graphia/eval_ledger.py`**, not in the Textual viewer:

- `_FIXED_COLUMNS` (a module tuple) holds the leading identity/game-dynamics headers (`⚠`, `Date`, `Provider`, `Large model`, `Small model`, `Games`, `Wins (LA/M)`, `Votes (LA/M)`, `Lineup`, `Notes`); `METRIC_ORDER` holds the trailing per-metric `(dotted_key, label)` pairs.
- `build_table_model` assembles `columns = [*_FIXED_COLUMNS, *(label for _, label in METRIC_ORDER)]` and, per record, calls `_row_cells`, which builds the cell list **in the same fixed order** — each fixed cell from a dedicated extractor (`_outcomes_cell`, `_vote_activity_cell`, `_lineup_cell`, `_note_cell`, …), then one `_metric_cell` per `METRIC_ORDER` entry. Rows are emitted as **plain strings** on the index-parallel `TableModel`.
- The Textual viewer `src/graphia/ui/ledger_viewer.py` (`LedgerTableScreen`) is purely a consumer: `on_mount` calls `table.add_columns(*self._model.columns)` and `_set_visible` → `_render_row` adds rows. **The viewer never names a column.** `_render_row` splits fixed-vs-metric cells **by position** using `fixed = len(self._model.columns) - len(METRIC_ORDER)`, right-justifying only the trailing metric cells; the leading fixed cells stay left-justified plain strings.

**Consequence for this change.** A new **fixed game-dynamics column** is added by (a) writing a new cell extractor in `eval_ledger.py` mirroring `_outcomes_cell`, and (b) declaring its header in `_FIXED_COLUMNS` **before** the `Notes`/metric block. The viewer recompiles its layout automatically: `add_columns(*model.columns)` picks up the new header, and the `len(columns) - len(METRIC_ORDER)` split keeps the new column on the **left-justified fixed side** (the right-justify split keys off the metric *tail*, so adding a head column doesn't disturb it — this is the same seam specs 013 and 014 used to add `Votes (LA/M)` and `Lineup`). **No `ledger_viewer.py` edit is required** unless we choose to revisit CSS widths (see §3). The detail view (`render_detail` and its `_render_*` section helpers) is **untouched** — it already renders `outcomes.scripted_side`, `outcomes.runaway`/`no_winner`, and `settings.scripted_player`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

All work is in the pure layer `src/graphia/eval_ledger.py`. The pattern is fixed by the three existing extractors (`_outcomes_cell`, `_vote_activity_cell`, `_lineup_cell`): a small function that reads through the defensive `_dig` (and the `_MISSING` sentinel for the present-vs-absent distinction), returns a compact plain string, and returns the **empty string** for a pre-metric record so older runs render blank (the back-compat contract every fixed cell already honours).

### 2.1 New cell extractors (responsibilities + contracts)

Three new private extractors, each mirroring `_outcomes_cell`'s shape (read via `_dig`; `_MISSING`-guard the present-vs-absent boundary; blank for absent):

- **`_scripted_side_cell(record) -> str`** — the scripted stand-in's-side win rate.
  - Reads `outcomes.scripted_side` (spec 027). **Absent block → `""`** (pre-027 record, or any run that resolved no seat side — `_dig(record, "outcomes.scripted_side", _MISSING) is _MISSING`).
  - When present: render the side it refers to **and** the rate, compactly — e.g. `LA .55` / `M .30`. Reuse the existing `_table_rate` helper (leading-dot two-decimal, `—` for a missing rate on the `games == 0` path) so it matches `Wins (LA/M)`'s number style. Side is the abbreviation `LA`/`M` derived from `outcomes.scripted_side.side` (`law_abiding`/`mafia`); an unrecognised/absent side defends to a blank-or-`—` rather than raising.
  - Header label (proposed): **`Scripted (side)`** — see §2.3 open decision.

- **`_stand_in_cell(record) -> str`** — which stand-in ran (active vs passive).
  - Reads `settings.scripted_player` (spec 026). Per the record contract (`evals/README.md`), this field is **omitted on pre-026 records and read as the prior default `passive`**. So this extractor's back-compat is a **defaulting** one, not a blank one: `_dig(record, "settings.scripted_player", default="passive")`, then render a **compact label** — proposed `active` / `passive` (or `act`/`pas` if width forces it). This is the one new column that deliberately does **not** blank for older records — the field has a well-defined historical meaning (the functional spec's acceptance criterion explicitly allows "prior default (passive) or blank").
  - Header label (proposed): **`Stand-in`**.

- **`_resolution_cell(record) -> str`** — the non-side game-resolution counts.
  - Reads `outcomes.runaway` (spec 023) and `outcomes.no_winner` (spec 013) via `_dig`, coerced through the existing `_vote_count` int-coercion. **Absent `outcomes` block → `""`** (same `_MISSING` guard as `_outcomes_cell`, since these live under `outcomes`).
  - When present: render the two non-side buckets compactly — proposed `R{n} N{n}` (runaway, no-winner), e.g. `R 1 N 2`; both default to `0` when the bucket key is absent but the block is present (a resolved run reads `R 0 N 0`, satisfying the "zero unresolved/runaway" criterion). `draw` is intentionally **not** added to the table (it's already implied by the partition `LA.wins + M.wins + runaway + draw + no_winner == games` and is in the detail view); keeping the column to the two "didn't resolve to a side" buckets keeps it narrow.
  - Header label (proposed): **`Unres (R/N)`** — see §2.3 open decision (separate column vs enriching `Wins (LA/M)`).

### 2.2 Column declarations (wiring into the table)

- Add the three new headers to **`_FIXED_COLUMNS`**, positioned **before `Notes`** so the right-justify metric split (`len(columns) - len(METRIC_ORDER)`) is undisturbed and they sit beside the other game-dynamics columns. Proposed order: `… "Games", "Wins (LA/M)", "Scripted (side)", "Unres (R/N)", "Votes (LA/M)", "Stand-in", "Lineup", "Notes"` — grouping `Scripted`/`Unres` next to `Wins`, and `Stand-in` (a settings fact) next to `Lineup` (the other settings fact).
- Add the three new extractor calls to **`_row_cells`** at the **matching positions** in the cell list (cell order must stay 1:1 with `_FIXED_COLUMNS`; this is the one invariant a column addition must not break — `test_table_model_lists_are_index_parallel` asserts `len(row) == len(columns)`).
- **No change to `ledger_viewer.py`.** Verified: `add_columns(*model.columns)` and the positional `_render_row` split both derive from the model shape, so the new fixed columns appear left-justified automatically.

### 2.3 Readability (column count / width)

The table is already wide and pans horizontally via the cell cursor (`cursor_type="cell"` auto-scrolls the highlighted cell into view), so adding three columns does not break navigation — but it does grow the off-screen tail. Mitigations baked into the cell formats above: compact labels (`LA .55`, `R 1 N 2`, `active`), reuse of `_table_rate`'s leading-dot two-decimal, and **dropping `draw`** from the resolution cell. The two open decisions in §3 are both about keeping the count down.

### 2.4 What is explicitly NOT changed

- **The detail view** (`render_detail` + `_render_outcomes_section` / `_render_settings_section`) — already renders all three facts in full; untouched.
- **`METRIC_ORDER`** and the metric cells — unchanged; these are the spec-011 blunder family, orthogonal to the new game-dynamics columns.
- **The recorded data** — no writer (`blunder_eval.render_record`), no metric, no `metrics_version`, no gameplay path is touched. This is read-side display only.
- **Search** (`_search_blob` / `_search_fields` / `row_matches_field`) — out of scope; the functional spec scopes this to the list's **columns** only. (A future spec could add a `stand-in` scoped-search field; not here.)

---

## 3. Impact and Risk Analysis

- **System Dependencies:** The change is confined to `src/graphia/eval_ledger.py` (the pure, Textual-free data layer). It depends only on the record shape documented in `evals/README.md` (`outcomes.scripted_side`, `outcomes.runaway`, `outcomes.no_winner`, `settings.scripted_player`) — all already written by `blunder_eval.render_record` and all read defensively. The viewer (`ledger_viewer.py`) consumes the model unchanged.
- **Potential Risks & Mitigations:**
  - **Heterogeneous/pre-metric records (the headline risk this layer was built to absorb).** Every new read goes through `_dig` + the `_MISSING` sentinel, so an absent `outcomes`/`scripted_side` block, or a record predating any of these specs, resolves to blank (or the documented `passive` default for the stand-in) — never a `KeyError`. The autouse-safe `_dig` discipline is the same one specs 012–014/027 relied on.
  - **Column-count / cell-order drift.** A new fixed column must be added to **both** `_FIXED_COLUMNS` and `_row_cells` at the same position; a mismatch is caught immediately by the existing `len(row) == len(columns)` parallelism assertion. The position-before-`Notes` rule preserves the metric right-justify split.
  - **Readability regression** (the only real product risk): three more columns widen the off-screen tail. Mitigated by compact formats and the open decisions in §3 below. Acceptable because the cell-cursor already pans wide tables.
- **Open Decisions (for review):**
  1. **`Unres (R/N)` as a separate column vs enriching `Wins (LA/M)`.** Proposed: a **separate** column, keeping `Wins (LA/M)` purely about the two winning sides and isolating "didn't resolve to a side" in its own narrow cell — clearer to scan and trivially blank-able for pre-013 records. The alternative (append `R/N` into the `_outcomes_cell` string) saves a column but crowds one cell with two unrelated meanings (who won vs whether it resolved) and complicates that cell's blank/zero contract. Recommend separate; confirm.
  2. **Exact column set / labels.** Proposed three columns with labels `Scripted (side)`, `Unres (R/N)`, `Stand-in`. Open whether to also surface `draw` (recommend not — it's derivable and in the detail view) and whether to abbreviate `Stand-in` values to `act`/`pas` if the row proves too wide in practice.

---

## 4. Testing Strategy

All tests are pure-layer unit tests added to `tests/test_ledger_model.py` (the existing home for `build_table_model` / cell-extractor coverage), driving through `build_table_model` over hand-written records the same way the spec-013/014/027 column tests do. No real model, no AWS, no eval run. Intents:

- **Present → value.** A record carrying `outcomes.scripted_side` → the scripted cell shows the rate and side (e.g. `LA .55`); a record with `settings.scripted_player: active` → the stand-in cell shows `active`; a record with `outcomes.runaway`/`no_winner` counts → the resolution cell shows them (e.g. `R 1 N 2`).
- **Absent → blank (back-compat).** A pre-027 record (no `outcomes.scripted_side`) → scripted cell `""`; a pre-013 record (no `outcomes` block) → resolution cell `""`; **and** the **defaulting** exception: a pre-026 record (no `settings.scripted_player`) → stand-in cell reads `passive` (not blank), per the record contract.
- **Zero-resolution distinct from absent.** A present `outcomes` block whose games all resolved to a side → resolution cell `R 0 N 0` (present-zero), staying distinct from the absent-block blank — mirroring the `_vote_activity_cell` present-zero-vs-absent test.
- **Table includes the new columns, in order.** `build_table_model([]).columns` contains the three new headers at their declared positions before `Notes`, and the existing `test_columns_are_fixed_columns_then_metric_labels` assertion (fixed columns then `METRIC_ORDER` labels) is updated to include them — confirming the metric split point still tracks `METRIC_ORDER`.
- **Old records render without error.** A heterogeneous mix (pre-013, pre-026, pre-027, and a full record) flattens via `build_table_model` with every `len(row) == len(columns)` — the existing parallelism test extended to cover the new columns; no `KeyError`.
- **Display-only.** No writer/metric test changes; the detail-view tests (`render_detail`) stay green unchanged, asserting the detail surface is byte-identical (it already shows these fields). The recorded-data-unchanged criterion is covered by the absence of any `blunder_eval` edit.
