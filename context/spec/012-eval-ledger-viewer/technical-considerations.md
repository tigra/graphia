# Technical Specification: Eval Ledger Viewer

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed *(verified 2026-06-14)*
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

A **second, standalone Textual app** — separate from the game's `GraphiaApp` — that reads `evals/blunder-ledger.yaml` and presents it as a scrollable, searchable table with a full-record drill-down. Two clean layers:

1. **A pure, Textual-free data layer** (`src/graphia/eval_ledger.py`): parse the multi-document ledger and flatten each heterogeneous record into a stable table model. No Textual import here — this is where *all* the parsing, the column model, the cell formatting, the search blobs, and the detail rendering live, so the bulk of the logic is unit-testable without driving a TUI.
2. **A thin Textual viewer** (`src/graphia/ui/ledger_viewer.py`): a `DataTable`-based table screen + a filter input + a detail screen, consuming the data layer. Launched by `make view-ledger` → `python -m graphia.ui.ledger_viewer`; the ledger path is injected via the app constructor (the same DI seam the game app uses) so tests point it at a temp file.

This increment **takes on the YAML-parser dependency that spec 011 deliberately avoided** — 011 hand-rendered the ledger write-only precisely because "*that* increment [the reader] is the one that takes on the YAML-parser dependency" (`evals/README.md`). It reads with `pyyaml` `safe_load_all` (multi-document, no code execution — read-only by construction).

All Textual APIs below were **verified against the installed Textual 8.2.4** (via the `textual-tui` specialist + context7): built-in pinned header on vertical scroll, both-axis scroll, `cursor_type="row"` + `DataTable.RowSelected`, `move_cursor` for cursor restore, `Input.Changed`, `push_screen`/`pop_screen`, and `App.run_test()`/`Pilot`. No spec requirement collides with a Textual limitation.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Pure data layer — `src/graphia/eval_ledger.py` — **[Agent: python-backend]**

No Textual import. Public seam:

- `load_ledger(path: Path) -> list[RawRecord]` — `yaml.safe_load_all` over the `---`-separated stream. Returns `[]` for a **missing or empty** file; skips `None` documents (a trailing `---`); raises a `LedgerParseError` on malformed YAML so the UI shows a friendly message, not a traceback. `safe_load_all` (never `load`) — the ledger is data-only and read-only.
- `build_table_model(records) -> TableModel` where `TableModel` carries **index-parallel** lists: `columns` (header labels), `rows` (per-row formatted cells), `search_blobs` (one lowercased searchable string per row — the free-text haystack), `search_fields` (one lowercased **per-field** mapping per row — the field-scoped haystacks; see §2.4), and `records` (the raw record per row). Index-parallelism is the contract the UI relies on to resolve a selected row back to its raw record.
- `row_matches_field(field, value, blob, fields) -> bool` (§2.4) — the pure matcher: the UI's field **selector** chooses the haystack (`SEARCH_SCOPE_ALL` → the whole `blob`; any `SEARCH_FIELDS` name → `fields[field]`), and the typed `value` is lowercased + whitespace-split into ANDed terms. **No `field:value` text parsing** — scoping is the selector's job, so a colon in the value is literal. Lives in the data layer so it's unit-testable without a TUI.
- `SEARCH_FIELDS` — the scopeable field names in dropdown order, and `SEARCH_SCOPE_ALL` — the "All" sentinel (the no-scope default). The single source of truth shared by `search_fields` construction, `row_matches_field`, and the UI's selector options.
- `render_detail(record) -> str` (or a Rich renderable) — the readable, sectioned full-record view (§2.5), kept here so the detail layout is unit-testable.
- `METRIC_ORDER: tuple[tuple[str, str], ...]` — the canonical `(dotted_key, header_label)` family order (`repetition`, `third_person_self_talk`, `self_vote.initiation`, `self_vote.yes`, `peer_vote.initiation`, `peer_vote.yes`), the single source of truth for column order; a future 011 metric surfaces by appending one tuple.

**Heterogeneity is absorbed here (the headline risk — see §3).** The committed ledger already mixes shapes: early records have no `code` block, no `settings` block, no `ci_low`/`ci_high`, and carry games under `run.games` (later records use `settings.games`). Every field read is a **defensive dotted-get with default** (`settings.games ?? run.games`, missing `code` → blank, missing CI → omit the band). The vote metrics are stored as **flat dotted string keys** under `metrics` (`metrics["self_vote.initiation"]` is one literal key — the harness's `render_record` emits the scorer's dotted name verbatim), *not* nested maps; the flattener resolves the flat literal key first with a nested-path fallback for forward-compatibility, and a `KeyError` must never reach the UI. *(Corrected during Slice 1 — the original assumption of nested maps was wrong against the real ledger.)*

### 2.2 The Textual viewer — `src/graphia/ui/ledger_viewer.py` — **[Agent: textual-tui]**

- `LedgerViewerApp(App[None])` — constructor takes the ledger `Path` (default = `LEDGER_PATH` imported from `graphia.tools.blunder_eval`, single source of truth). Does **not** call `load_config()` (no AWS/checkpoint env needed — it only needs a file).
- `LedgerTableScreen(Screen)` — `Input(id="search")` docked top; a `Static(id="match-count")` ("Showing X of N"); a `DataTable(id="ledger-table", cursor_type="row")` at `height: 1fr`; a `Static(id="empty-state")` toggled (`display`) in place of the table for the empty-ledger / no-matches cases; a `Footer` for key hints.
- **Focus model (the table is the default focus, not the search box).** The docked search widgets must **not** grab initial focus — otherwise the arrow keys / Enter are swallowed as search text and the table is unnavigable. `on_mount` calls `table.focus()` after populating; a `/` screen binding (`action_focus_search`) moves focus to the value `Input`; an `on_key` handler intercepts `escape` **only while a search widget is focused** (the `Input`, or the `Select` when collapsed) and returns focus to the table (stopping the event so the app-level `escape`→quit does not fire), so `escape` on the table still quits while `escape` in search backs out; `on_input_submitted` (Enter) commits the filter and jumps focus to the results. Printable keys (`q`, `/`) typed in the focused `Input` are captured as text, not bindings. A `Footer` makes `/ Search` and `esc Quit` discoverable.
- **Selector ↔ input arrow nav (boundary jump).** The `Select` sits to the left of the value `Input`. From the collapsed `Select`, **right** focuses the `Input` (handled in `on_key`, guarded by `not select.expanded` so the dropdown's own arrows are untouched). The reverse needs care: the `Input` consumes `left` via its `cursor_left` binding, so a plain screen `on_key` never sees it — a `SearchInput(Input)` subclass overrides the left-cursor action and, **only when `cursor_position == 0`**, posts a message the screen handles to focus the `Select` (otherwise normal caret motion). So `left` at the start of the box jumps to the selector; inside the text it still moves the caret.
- `DetailScreen(Screen)` — a full-window drill-down (a plain `Screen`, push/pop — not a modal overlay — so "open detail / return to the table in the same place" is a natural page transition).
- **Scrolling & headers (verified):** `DataTable` pins the header band on vertical scroll built-in; with content wider/taller than the viewport it scrolls **both axes**, headers tracking their columns. **`fixed_columns` is left at 0** → the whole grid scrolls together, no frozen identity column (the functional choice). *Known one-line extension point:* `fixed_columns=1` would pin the date column if a later increment wants it.
- **`cursor_type="cell"` (not `"row"`).** The cursor is a single highlighted **cell** that the `DataTable` **auto-scrolls fully into view** on every move (its built-in `move_cursor(..., scroll=True)` / scroll-cursor-into-view), so panning a wide table is "move the highlight, the cell comes into view" rather than character-wise viewport nudging. Consequence: the drill-down fires on `DataTable.CellSelected` (resolve the row via `event.coordinate.row` → `_visible_indices`), and the cursor restored on resume is the full **cell coordinate** (`move_cursor(row=…, column=…)`), so a round-trip lands on the exact cell.

### 2.3 Columns & cell formatting — **[Agent: python-backend]** (model) + **[Agent: textual-tui]** (render)

Fixed leading columns then the six metric columns in `METRIC_ORDER`:

| Column | Source (defensive) |
| --- | --- |
| `⚠` dirty marker | `code.dirty` → `"⚠"` when true, else `""` |
| Date | `run.date` |
| Provider | `provider.name` |
| Large / Small model | `settings.* ?? provider.*` |
| Games | `settings.games ?? run.games` |
| Notes | `notes` — collapsed to one line, truncated to `_NOTES_CELL_MAXLEN` with an ellipsis |
| 6 × metric | `metrics.<dotted-key>` |

- **Cell format:** `rate [ci_low–ci_high] m/n` (e.g. `0.45 [0.36–0.55] 49/108`); **omit the bracketed band** for pre-CI records that legitimately lack it (`0.45 49/108`); **absent metric → empty cell** — the spec's "distinguishable from a genuine zero" (a clean `0.0` still renders `0.00 [..] 0/108`, visibly different from blank).
- **Dirty marker = a dedicated `⚠` column, not row styling.** Justified: a plain-`Text` marker cell is reliable across Textual versions, scrolls with the grid, survives the `clear()`/`add_row` filter-rebuild with no extra bookkeeping, is greppable by the search blob, and is assertable in `run_test`. (DataTable exposes no first-class "style a whole row" API; per-cell row styling would need reapplying after every rebuild and reads ambiguously against the cursor highlight.)
- The pure layer emits **plain strings**; the UI wraps the numeric metric columns in `Text(..., justify="right")` at `add_row` time (cleaner string-based test assertions; Rich stays a UI concern).
- **Notes is a *fixed* (left-justified) column placed *before* the metric block** (last of the fixed columns, after Games). Deliberate: it keeps the UI's metric right-justification split (`len(columns) - len(METRIC_ORDER)`) undisturbed, and — since the 6 wide metric columns scroll off-screen regardless — it keeps the note in the initial viewport at no cost to the metrics. Its purpose is to make a **note-match visible** (notes are in the search blob, §2.4), not a phantom hit; `_note_cell` collapses whitespace/newlines to one line and truncates to `_NOTES_CELL_MAXLEN`. The full, verbatim note is the drill-down's job (§2.5).

### 2.4 Search / filter — **[Agent: textual-tui]**

`Input.Changed` (`on_input_changed`) fires per keystroke → lowercase the query, keep the row indices where the row matches, `table.clear(columns=False)` then re-`add_row` the survivors, and maintain a `visible_indices` list so `RowSelected.cursor_row` still maps to the correct raw record after filtering. Update `#match-count` to "Showing X of N". Empty query → all rows return. **No-matches state:** non-empty query with zero survivors → hide the table, show `#empty-state` with **"No runs match '<query>'."** (distinct copy from the empty-ledger message). `search_blobs` (built in §2.1) concatenates date, provider, both model ids, commit, branch, a dirty/clean keyword, and the **full notes** text.

**Selector-scoped matching (pure, in the data layer).** A field **selector** — `Select(id="field-select")`, default "All" — chooses *which* field the typed value filters on; there is **no `field:value` text syntax** (it was awkward: typing the field name matched nothing until the colon). The per-row keep test is the pure `row_matches_field(field, value, blob, fields)` (§2.1), so the UI stays a thin caller:

- `field == SEARCH_SCOPE_ALL` → the value is matched against the whole free-text `blob` (every fact); any `field` in `SEARCH_FIELDS` → against only that row's `search_fields[field]`.
- The typed `value` is lowercased and whitespace-split into **terms** that are ANDed (every term a substring of the chosen haystack); an empty/all-whitespace value keeps all rows.
- Because there is no colon parsing, a value containing a colon (a model id `qwen3-coder:30b`) is matched **literally** — the selector, not a prefix, does the scoping.
- `SEARCH_FIELDS` (`provider`, `date`, `model` [both ids joined], `commit`, `branch`, `games`, `note`, `state` [clean/dirty keyword]) is the dropdown order and the `search_fields` keys; `SEARCH_SCOPE_ALL` is the default. `search_fields[field]` values are lowercased like the blob, so matching is case-insensitive. Both the `Input.Changed` and `Select.Changed` events re-run the shared filter (`_run_filter`), so changing either the value or the scope refilters live; `#match-count` and the no-match `#empty-state` (echoing the typed value) are unchanged.

### 2.5 Detail screen — **[Agent: textual-tui]**

`on_data_table_cell_selected` → stash the cell `coordinate`, resolve the raw record via `visible_indices` (from `coordinate.row`), `push_screen(DetailScreen(record))`. The screen is a `VerticalScroll` (records are long) rendering the record **as readable sectioned text** (not a YAML re-dump), grouped by the canonical order `run → code → provider → settings → quality → metrics → notes`: every provenance field (commit, branch, **clean/dirty**, model ids + ollama digests/`server_version` or bedrock `note`, settings, `metrics_version`), **every present metric's full-precision** rate + `[ci_low–ci_high]` + `count/denominator`, run-quality counts, and the **complete free-text note verbatim** (newlines preserved). The section layout is produced by `render_detail` in the pure layer (unit-testable). `escape`/`q`/`backspace` ("back") → `pop_screen`; the table screen restores the stashed cursor row via `move_cursor` on resume. A `Header` (the viewer name via the screen's `TITLE`, plus a `SUB_TITLE` spelling out "run detail · Esc / Backspace to go back") and a `Footer` (the `Back` key hints — `escape` and `backspace` bound `show=True`) frame the `VerticalScroll`, so a full-window record stays recognisably the same viewer with a visible exit rather than looking like a separate program.

### 2.6 Dependency, CLI, Makefile — **[Agent: python-backend]**

- `uv add pyyaml` (runtime) + `uv add --dev types-pyyaml` (type-checker). The multi-document read this increment introduces; foreseen by 011 / `evals/README.md`.
- CLI: `argparse` (repo `tools/` idiom) with an optional `--path <file>` (default `LEDGER_PATH`), so a maintainer can point at an alternate ledger; `python -m graphia.ui.ledger_viewer` via an `if __name__ == "__main__"` entry.
- `Makefile` target `view-ledger` (beside `inspect-diary`/`blunder-eval`, matching the comment-then-target style): `uv run python -m graphia.ui.ledger_viewer $(ARGS)`; add to `.PHONY` + a help/README line.

---

## 3. Impact and Risk Analysis

- **Blast radius:** two new modules (`eval_ledger.py`, `ui/ledger_viewer.py`), one new dependency, a Makefile target, two test files. **No change to the game, the harness, or the ledger format** — strictly a reader.
- **Risk — heterogeneous records (the real one, not Textual).** The committed ledger already mixes pre-provenance (`run.games`, no `code`, no CI) and full records. *Mitigation:* the defensive dotted-get flattener in `eval_ledger.py` is the single place this is absorbed, and it's the highest-value thing to unit-test (a pre-provenance fixture must flatten with blank `code`/CI cells and the `run.games` fallback, never raising).
- **Risk — strictly read-only (functional-spec §2.5).** `safe_load_all` (no object construction); the viewer only reads. *Mitigation:* a test asserts the ledger file is **byte-identical** after a full filter + drill-down + back session.
- **Deliberate consequence, not a gap:** with `fixed_columns=0` the identity columns scroll off when panning right — the maintainer's chosen "everything scrolls together". Recorded here with the `fixed_columns=1` future toggle.
- **Architectural note — new vendor dependency + reversing 011's "no YAML lib" stance.** Small, ubiquitous, and *foreseen* (011 explicitly deferred the parser to this increment), so it's the planned realization rather than a surprise. An **ADR is optional** — worth `/buddah:adr` if you want the pyyaml/`safe_load_all` choice and the reversal recorded; skippable given 011 pre-decided it.
- **Determinism / suite posture unchanged:** the viewer never imports `load_config` or reaches a model, so the mocked suite and `safe_llm` are untouched; all viewer tests are offline against a temp ledger.

---

## 4. Testing Strategy — **[Agent: testing]**

- **Pure unit tests (`tests/test_ledger_model.py`, no Textual):** `load_ledger` over a multi-doc fixture → N records; missing/empty file → `[]`; trailing-`---`/`None` doc skipped; malformed YAML → `LedgerParseError`. A **pre-provenance fixture** flattens without raising (blank `code`/CI, `run.games` fallback). `build_table_model`: column order = fixed (incl. **Notes**) + canonical metric order; **absent metric → empty cell** vs clean `0.0` → non-empty cell (the blank-vs-zero contract); exact cell format; the **Notes cell** previews a short note verbatim, truncates a long one (one line + ellipsis), empty when absent; `search_blobs` include date/provider/model/commit/branch/**notes**; `search_fields` carry each scoped field's text; index parallelism. `row_matches`: a plain term hits the blob; `provider:bedrock` scopes to the provider; an over-matching value (e.g. `note:ollama` vs `provider:ollama`) is disambiguated; **multiple terms AND**; an **unrecognised `x:y` prefix** falls back to free-text so a colon-bearing value (`qwen3-coder:30b`) still matches; empty query → keep all. `render_detail` includes every present metric's exact counts + the full multi-line note.
- **Pilot UI tests (`tests/test_ledger_viewer.py`, mirror `test_app_boot.py`):** table render (row count + a known cell); filter (`#search` keystrokes → row count drops, `#match-count` reads "Showing X of N", clear restores); **field-scoped filter** (`provider:bedrock` narrows where a bare `bedrock`-in-notes would not); no-matches (table hidden, `#empty-state` copy); **focus model** (table focused on mount, arrow-key navigation works, `/` focuses search, `escape` in search returns to the table without quitting, `escape` on the table quits); row-select → `DetailScreen` shows the note → `escape` → cursor restored to the same row; empty/missing ledger → "No runs recorded yet", no crash; **read-only** byte-identical assertion. Ledger path injected via constructor → `tmp_path`; never touches the committed ledger. No LLM/network/AWS.
