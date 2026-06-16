---
spec: 012-eval-ledger-viewer
spec_title: Eval Ledger Viewer
introduced_on: 2026-06-14
---

# Concepts introduced in this increment

## Architecture (pure core / thin shell)

- **Pure data layer under a thin Textual shell** (`pure-data-layer-vs-thin-tui`) — All parsing, the column model, cell formatting, search, and the detail render live in a Textual-free module that emits plain strings; the Textual viewer is a thin presentation on top — so the bulk of the logic is unit-testable without ever driving a TUI.
- **A second standalone Textual app** (`standalone-secondary-textual-app`) — A separate `App` from the game's `GraphiaApp` that needs only a file path (it never calls `load_config`, so no AWS/checkpoint/model env is required), launched by its own `python -m` entry and `make view-ledger` target.

## Data (reading a messy append-only file)

- **Read-only-by-construction multi-document parse** (`read-only-multidoc-yaml-parse`) — The `---`-separated ledger is read with `yaml.safe_load_all` (multi-document, data-only, no object construction), so the viewer structurally *cannot* mutate or execute anything — and this is the YAML-parser dependency spec 011 deliberately deferred to "the reader".
- **Defensive dotted-get absorbs record heterogeneity** (`defensive-dotted-get-heterogeneity`) — Every field read goes through `_dig(record, "a.b.c", default)`, so the ledger's mixed shapes (pre-provenance records with no `code`/`settings`/CI vs full records) flatten without a `KeyError` ever reaching the UI; one function is the single place heterogeneity is absorbed.

## Model (one shape the UI can trust)

- **Index-parallel table model** (`index-parallel-table-model`) — `rows[i]`, `search_blobs[i]`, `search_fields[i]`, and `records[i]` all describe the *same* run; index-parallelism is the contract that resolves a (possibly filtered) selected row back to its raw record.
- **Column order as a single source of truth** (`metric-order-single-source`) — One `METRIC_ORDER` tuple of `(dotted_key, label)` drives the column count, the headers, every cell, and the detail section, so a future metric surfaces by appending exactly one tuple.

## UI (Textual presentation)

- **Cell-cursor pan-into-view over a big table** (`datatable-cell-cursor-scroll`) — `DataTable` with `cursor_type="cell"` auto-scrolls the highlighted cell fully into view on every move, so panning a wide/tall table is "move the highlight, the cell follows"; `fixed_columns=0` keeps the whole grid scrolling together under a pinned header.
- **Push/pop screen drill-down with cursor restore** (`screen-stack-drilldown-cursor-restore`) — Selecting a row pushes a full-window `DetailScreen`; popping it fires `on_screen_resume`, which restores the stashed cell coordinate via `move_cursor`, so the round-trip lands the reader on the exact cell they drilled into.
- **Table-first focus model** (`table-first-focus-model`) — The table holds focus on mount (not the docked search box) so arrows/Enter navigate immediately; `/` reaches search and `escape` *inside* search returns to the table rather than quitting — enforced by an `on_key` that only intercepts while a search widget is focused.
- **Selector-scoped search without a query syntax** (`selector-scoped-search`) — A `Select` beside the box chooses the haystack (the whole free-text blob, or one named field); the pure `row_matches_field` lowercases/whitespace-splits/ANDs the typed terms, so there is no `field:value` mini-language and a colon in a model id matches literally.
- **Boundary-jump keyboard nav between widgets** (`boundary-jump-widget-nav`) — A `SearchInput(Input)` subclass posts a message when `left` is pressed at caret position 0 so focus jumps to the selector on its left, while `right` on the collapsed selector jumps into the input — arrow keys cross the widget boundary instead of dead-ending.
