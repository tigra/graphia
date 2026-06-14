"""Standalone Textual viewer for the eval quality ledger (spec 012, Slice 1).

A *second, separate* Textual app — not the game's :class:`~graphia.ui.app.GraphiaApp`
— that reads ``evals/blunder-ledger.yaml`` (the provenance-stamped quality
ledger ``blunder_eval`` appends to) and presents it as a scrollable table. The
heavy lifting — parsing the heterogeneous multi-document YAML and flattening it
into a stable, index-parallel column model — lives in the pure, Textual-free
:mod:`graphia.eval_ledger` data layer; this module is the thin presentation on
top (a :class:`~textual.widgets.DataTable` rendering that model).

**This viewer needs only a file**, so — unlike the game app — it never calls
:func:`graphia.config.load_config`: no AWS / checkpoint / model env is required
to read a ledger. The ledger path is injected via the app constructor (the same
DI seam the game app uses for its stores), defaulting to
:data:`~graphia.tools.blunder_eval.LEDGER_PATH` (the single source of truth for
where the harness writes), so tests can point it at a temp file.

Search (Slice 2) and the full-record drill-down (Slice 3) are later increments;
the structure here leaves clean seams for both (a top dock region for the search
input, ``cursor_type="row"`` for the eventual row-select) without implementing
them yet.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Select, Static

from graphia.eval_ledger import (
    LedgerParseError,
    METRIC_ORDER,
    RawRecord,
    SEARCH_FIELDS,
    SEARCH_SCOPE_ALL,
    TableModel,
    build_table_model,
    load_ledger,
    render_detail,
    row_matches_field,
)
from graphia.tools.blunder_eval import LEDGER_PATH

# The empty-ledger copy shown in #empty-state when the ledger is missing or has
# no records. (Slice 2's distinct no-matches message — built by
# :func:`_no_match_message` — handles a non-empty query that filters everything
# out — kept separate on purpose.)
_EMPTY_LEDGER_MESSAGE = "No runs recorded yet."


def _no_match_message(query: str) -> str:
    """The #empty-state copy when a non-empty query survives zero rows.

    Distinct from :data:`_EMPTY_LEDGER_MESSAGE` (the empty/missing ledger): this
    is shown when the ledger *has* runs but none match the live filter, and it
    echoes the query so the user sees exactly what filtered everything out.
    """
    return f"No runs match '{query}'."

# The friendly copy shown when the ledger file exists but is malformed YAML
# (``load_ledger`` raised ``LedgerParseError``) — a readable error state in the
# same #empty-state slot instead of a traceback escaping to the terminal.
_PARSE_ERROR_PREFIX = "Could not read the ledger:"


class SearchInput(Input):
    """The value :class:`~textual.widgets.Input`, with a boundary-jump left edge.

    A plain ``Input`` consumes ``left`` via its own ``cursor_left`` binding, so a
    screen-level ``on_key`` never sees a ``left`` press to act on. This subclass
    overrides :meth:`action_cursor_left` so that when the caret is already at the
    **start** of the text (``cursor_position == 0``) the keystroke jumps focus
    *out* to the field selector on its left (the boundary-jump nav, requirement
    C) instead of being a no-op; anywhere else inside the text it falls through
    to the normal caret move. The jump is announced via the
    :class:`FocusFieldSelect` message the screen handles, keeping the widget
    decoupled from the selector's id.
    """

    class FocusFieldSelect(Message):
        """Posted when ``left`` is pressed with the caret at the input's start.

        The screen handles it by moving focus to the ``#field-select`` selector —
        the left half of the boundary-jump (the selector's ``right`` jumps back).
        """

    def action_cursor_left(self, select: bool = False) -> None:
        """Move the caret left, or jump to the field selector at the start edge.

        Mirrors ``Input.action_cursor_left(select=False)`` (Textual 8.2.4). At
        ``cursor_position == 0`` there is nothing to the left, so instead of the
        normal no-op we post :class:`FocusFieldSelect` to hand focus to the
        selector; otherwise we defer to the base caret move (preserving the
        optional ``select`` extend-selection behaviour).
        """
        if self.cursor_position == 0:
            self.post_message(self.FocusFieldSelect())
            return
        super().action_cursor_left(select=select)


class DetailScreen(Screen):
    """The full-record drill-down: one ledger record rendered in a scroller.

    Wraps the pure data layer's :func:`~graphia.eval_ledger.render_detail` output
    (a long, sectioned plain string — ``run`` → ``code`` → … → ``notes``) in a
    :class:`~textual.containers.VerticalScroll` so the whole record is reachable
    even when it overruns the viewport. **No formatting lives here** — the string
    comes verbatim from the data layer, mirroring the table model's plain-string
    contract. ``escape``/``q``/``backspace`` pop back to the table (its own
    bindings, so they take precedence over the app-level quit while this screen
    is active).

    A :class:`~textual.widgets.Header` (the viewer name + a "run detail · Esc /
    Backspace to go back" subtitle) and a :class:`~textual.widgets.Footer` (the
    ``Back`` key hints) frame the record, so it stays obvious that this is the
    same ledger viewer and how to return — a full-window record can otherwise
    read like a different program with no visible way out.
    """

    # Drives the Header band: the app name (so the detail view is unmistakably
    # still the ledger viewer) plus a subtitle spelling out the back keys.
    TITLE = "Graphia eval ledger"
    SUB_TITLE = "run detail · Esc / Backspace to go back"

    DEFAULT_CSS = """
    DetailScreen {
        layout: vertical;
    }

    #detail-scroll {
        height: 1fr;
        width: 1fr;
    }

    #detail-body {
        padding: 0 1;
        width: 1fr;
    }
    """

    # Esc / Backspace / q pop back to the table screen — NOT quit the app.
    # Because these are screen-level bindings, they are consulted before the
    # app's escape→quit while the DetailScreen is the active screen, so the first
    # escape returns to the table and the next escape (handled by the app
    # binding) quits. Esc and Backspace are shown in the Footer ("Back"); q is the
    # quiet third option.
    BINDINGS = [
        Binding("escape", "close", "Back", show=True),
        Binding("backspace", "close", "Back", show=True),
        Binding("q", "close", "Back", show=False),
    ]

    def __init__(self, record: RawRecord) -> None:
        super().__init__()
        self._record = record

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="detail-scroll"):
            yield Static(render_detail(self._record), id="detail-body")
        yield Footer()

    def action_close(self) -> None:
        """Pop this screen, returning to the table (which restores its cursor)."""
        self.app.pop_screen()


class LedgerTableScreen(Screen):
    """The table screen: a scrollable :class:`DataTable` of the flattened ledger.

    Composes the ``#ledger-table`` :class:`DataTable` at ``height: 1fr`` plus a
    hidden ``#empty-state`` :class:`Static` shown in its place when there is
    nothing to render (empty/missing ledger, or a parse error). The numeric
    metric columns are wrapped in a right-justified Rich :class:`Text` at
    ``add_row`` time; the fixed identity columns stay plain strings.

    A docked search ``Input`` + match-count (Slice 2) filters the rows, and
    selecting the highlighted cell opens that row's :class:`DetailScreen`
    drill-down (Slice 3). The cursor is a single **cell** the ``DataTable``
    auto-scrolls fully into view as it moves, so a wide table pans by moving the
    highlight rather than nudging the viewport character by character.

    **Focus belongs to the table by default** so the arrow keys / Enter drive
    row navigation and drill-down the moment the viewer opens — the search box
    is opt-in (``/`` or Tab to reach it). While the search box has focus,
    ``escape`` returns focus to the table (rather than quitting), and ``Enter``
    commits the filter and jumps to the results; a printable ``q``/``/`` typed
    there is captured by the Input, not the app/screen bindings.
    """

    DEFAULT_CSS = """
    LedgerTableScreen {
        layout: vertical;
    }

    /* Top dock region for the search Input + match-count (Slice 2). Docked top
       at auto height so it pins above the table without disturbing the
       table's 1fr fill. */
    #search-region {
        dock: top;
        height: auto;
    }

    /* The selector + value Input sit side by side on one row; #match-count
       sits below them (still inside #search-region). */
    #search-row {
        height: auto;
        width: 1fr;
    }

    #field-select {
        width: 22;
    }

    #search {
        width: 1fr;
    }

    #match-count {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #ledger-table {
        height: 1fr;
        width: 1fr;
    }

    #empty-state {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    # ``/`` jumps focus to the search box (the table holds focus by default, so
    # this is how you start filtering without reaching for the mouse). Shown in
    # the Footer; while the search Input itself is focused the keystroke is
    # captured as text, so it only fires from the table.
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
    ]

    def __init__(self, model: TableModel | None, error: str | None = None) -> None:
        """Build the screen from a flattened model (or an error / empty state).

        ``model`` is the index-parallel :class:`TableModel` from the data layer;
        ``None`` together with a non-``None`` ``error`` represents the parse-error
        state (the ledger existed but was malformed). An empty model (no rows)
        and a ``None`` model both resolve to the ``#empty-state`` path — the
        difference is only the message shown.
        """
        super().__init__()
        self._model = model
        self._error = error
        # Maps each currently-displayed table row → its index in the model's
        # index-parallel lists (rows/search_blobs/records). Rebuilt on every
        # filter so a later row-select (Slice 3) resolves to the right raw
        # record even when the visible set is a filtered subset.
        self._visible_indices: list[int] = []
        # The cursor cell stashed when a cell is selected into the DetailScreen, so
        # it can be restored when that screen pops and this one resumes — the
        # maintainer returns to exactly the cell they drilled into. ``None`` until
        # the first drill-down.
        self._stashed_cursor: Coordinate | None = None

    def compose(self) -> ComposeResult:
        # The top dock region carrying the field selector + value Input on one
        # row, with the match-count below. The selector (defaulting to "All" /
        # SEARCH_SCOPE_ALL) picks the field to scope on, so the maintainer never
        # types a field name into the value box.
        with Vertical(id="search-region"):
            with Horizontal(id="search-row"):
                yield Select(
                    [("All", SEARCH_SCOPE_ALL)] + [(f, f) for f in SEARCH_FIELDS],
                    value=SEARCH_SCOPE_ALL,
                    allow_blank=False,
                    id="field-select",
                )
                yield SearchInput(id="search", placeholder="Filter runs…")
            yield Static(id="match-count")
        # cursor_type="cell": a single highlighted cell that the DataTable
        # auto-scrolls **fully into view** on every move (both axes), so panning a
        # wide table is "move the highlight, the cell comes into view" rather than
        # nudging the viewport a character at a time. fixed_columns=0 is
        # deliberate (everything scrolls together); fixed_columns=1 would pin the
        # date column.
        yield DataTable(id="ledger-table", cursor_type="cell")
        # Hidden by default; shown (and the table hidden) for the empty/error
        # states in on_mount.
        yield Static(self._empty_message(), id="empty-state")
        # Key hints (Search / Quit) so the table-first focus model is discoverable.
        yield Footer()

    def on_mount(self) -> None:
        """Populate the table, or switch to the #empty-state for no-data states."""
        table = self.query_one("#ledger-table", DataTable)
        empty = self.query_one("#empty-state", Static)

        if self._model is None or not self._model.rows:
            # Empty/missing ledger or a parse error: hide the grid, show the
            # message. (The Static already carries the right copy from compose.)
            table.display = False
            empty.display = True
            # Nothing to search; leave the visible map empty (filter is a no-op).
            return

        empty.display = False
        # Initial render shows every row, so the visible map is the full range.
        table.add_columns(*self._model.columns)
        self._set_visible(table, list(range(len(self._model.rows))))
        # Focus the table (not the search Input) so the arrow keys / Enter drive
        # navigation immediately — otherwise the docked Input grabs initial focus
        # and swallows every navigation keystroke as text.
        table.focus()

    def action_focus_search(self) -> None:
        """Move focus to the search box (the ``/`` binding). No-op without rows."""
        if self._model is None or not self._model.rows:
            return
        self.query_one("#search", Input).focus()

    def on_key(self, event: events.Key) -> None:
        """Boundary-jump nav + ``escape`` back-out for the search controls.

        Three cases handled here (all from the search region; the table's own
        keys are untouched):

        - **``right`` on the collapsed field selector** → focus the value
          ``Input`` (the right half of the boundary-jump; the Input's left edge
          jumps back, handled in :class:`SearchInput`). Guarded by
          ``not expanded`` so arrows are NOT stolen while the dropdown overlay is
          open.
        - **``escape`` inside the value ``Input``** → back out to the table
          rather than letting the app's ``escape``→quit fire.
        - **``escape`` on the collapsed field selector** → also back out to the
          table; while the selector is *expanded* we let its own ``escape`` close
          the dropdown instead.
        """
        select = self.query_one("#field-select", Select)

        # right on the collapsed selector → jump into the value Input.
        if (
            event.key == "right"
            and select.has_focus
            and not select.expanded
        ):
            self.query_one("#search", Input).focus()
            event.stop()
            event.prevent_default()
            return

        if event.key != "escape":
            return

        search = self.query_one("#search", Input)
        # escape inside the value box, or on the collapsed selector, returns to
        # the table; an expanded selector keeps escape for closing its overlay.
        if search.has_focus or (select.has_focus and not select.expanded):
            self.query_one("#ledger-table", DataTable).focus()
            event.stop()
            event.prevent_default()

    def on_search_input_focus_field_select(
        self, event: SearchInput.FocusFieldSelect
    ) -> None:
        """``left`` at the value Input's start edge → focus the field selector.

        The left half of the boundary-jump: :class:`SearchInput` posts this when
        ``left`` is pressed with the caret at position 0, and we hand focus to
        the ``#field-select`` selector on its left.
        """
        self.query_one("#field-select", Select).focus()
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the search box commits the filter and jumps to the results."""
        if event.input.id != "search":
            return
        table = self.query_one("#ledger-table", DataTable)
        if table.display:
            table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-run the filter whenever the value box changes (live filtering)."""
        if event.input.id != "search":
            return
        self._run_filter()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Re-run the filter whenever the scope field selector changes."""
        if event.select.id != "field-select":
            return
        self._run_filter()

    def _run_filter(self) -> None:
        """Live-filter the table to rows matching the selector field + value.

        The single filter path both widgets drive: it reads the scope ``field``
        from the ``#field-select`` :class:`~textual.widgets.Select` and the typed
        ``value`` from the ``#search`` ``Input``, then keeps each model index
        ``i`` where the pure matcher
        :func:`~graphia.eval_ledger.row_matches_field` returns True for that row's
        ``search_blobs[i]`` / per-field ``search_fields[i]``. With the selector on
        :data:`~graphia.eval_ledger.SEARCH_SCOPE_ALL` (the default) the value hits
        the whole free-text blob; on a named field it scopes to that field only.
        The matcher lowercases/splits/ANDs the value, so an empty/whitespace value
        matches every row (restoring all rows). A non-empty value that survives
        zero rows hides the table and shows the distinct "No runs match" copy
        echoing the original-case **value**; clearing it (or a value/field that
        matches again) restores the grid. The empty/missing-ledger case has no
        rows, so this is a no-op there.
        """
        if self._model is None or not self._model.rows:
            return

        table = self.query_one("#ledger-table", DataTable)
        empty = self.query_one("#empty-state", Static)
        field = self.query_one("#field-select", Select).value
        value = self.query_one("#search", Input).value

        indices = [
            i
            for i in range(len(self._model.rows))
            if row_matches_field(
                field,
                value,
                self._model.search_blobs[i],
                self._model.search_fields[i],
            )
        ]

        if not indices:
            # Non-empty value that matched nothing: hide the grid, show the
            # distinct no-match copy echoing the (original-case) value.
            self._set_visible(table, indices)
            table.display = False
            empty.update(_no_match_message(value))
            empty.display = True
            return

        empty.display = False
        table.display = True
        self._set_visible(table, indices)

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Drill into the highlighted cell's row → full record via the DetailScreen.

        With the cell cursor, selecting *any* cell opens that row's record.
        Stashes the selected cell coordinate (restored on resume so the maintainer
        returns to the exact cell), resolves the raw record through the
        ``_visible_indices`` map — so a filtered subset still points at the right
        record — and pushes the :class:`DetailScreen`. A no-op when there is no
        model or the row is out of range (the empty/error states have no rows).
        """
        if self._model is None or not self._model.records:
            return
        row = event.coordinate.row
        if not 0 <= row < len(self._visible_indices):
            return
        self._stashed_cursor = event.coordinate
        record = self._model.records[self._visible_indices[row]]
        self.app.push_screen(DetailScreen(record))

    def on_screen_resume(self) -> None:
        """Restore the cursor when the DetailScreen pops back to this screen.

        Fires (via Textual's ``ScreenResume`` event) when this screen becomes
        active again after the pushed :class:`DetailScreen` is popped. Moves the
        cursor back to the stashed cell — scrolling it into view — so the
        drill-down round-trip lands the maintainer exactly where they were. Only
        acts when a cell was actually stashed and a populated table exists.
        """
        if self._stashed_cursor is None:
            return
        if self._model is None or not self._model.rows:
            return
        table = self.query_one("#ledger-table", DataTable)
        row, column = self._stashed_cursor.row, self._stashed_cursor.column
        if 0 <= row < table.row_count:
            table.move_cursor(row=row, column=column, scroll=True)

    def _set_visible(self, table: DataTable, indices: list[int]) -> None:
        """Rebuild the table body to show exactly ``indices`` and record the map.

        Clears the rows (keeping the already-added columns) and re-adds one row
        per model index in ``indices``, then stores ``indices`` as
        ``_visible_indices`` so a displayed row → model index lookup stays
        correct. Reuses :meth:`_render_row` for the metric right-justification.
        """
        assert self._model is not None  # callers guard the no-model state
        table.clear(columns=False)
        for i in indices:
            table.add_row(*self._render_row(self._model.rows[i]))
        self._visible_indices = list(indices)
        self._update_match_count(len(indices))

    def _render_row(self, cells: list[str]) -> list[str | Text]:
        """Render one model row's cells, right-justifying the metric columns.

        The fixed leading columns (``⚠``, Date, Provider, models, Games, Notes)
        stay plain strings; the trailing metric columns are wrapped in a
        right-justified Rich :class:`Text` so the numeric rates line up under
        their headers. The split point is the fixed-column count derived from the
        model shape, so it tracks an added/removed fixed column upstream. Shared
        by the initial populate and every filter rebuild.
        """
        assert self._model is not None
        fixed = len(self._model.columns) - len(METRIC_ORDER)
        return [
            cell if i < fixed else Text(cell, justify="right")
            for i, cell in enumerate(cells)
        ]

    def _update_match_count(self, visible: int) -> None:
        """Update #match-count to ``Showing X of N`` (N = total model rows)."""
        total = len(self._model.rows) if self._model is not None else 0
        self.query_one("#match-count", Static).update(f"Showing {visible} of {total}")

    def _empty_message(self) -> str:
        """The copy for #empty-state — a friendly parse error, or the empty hint."""
        if self._error is not None:
            return f"{_PARSE_ERROR_PREFIX} {self._error}"
        return _EMPTY_LEDGER_MESSAGE


class LedgerViewerApp(App[None]):
    """The standalone ledger-viewer app — reads a ledger file, shows the table.

    Constructed with the ledger :class:`~pathlib.Path` (default
    :data:`~graphia.tools.blunder_eval.LEDGER_PATH`); the path is the only input
    it needs, so it **never calls** :func:`graphia.config.load_config`. On mount
    it loads the ledger via :func:`~graphia.eval_ledger.load_ledger` (catching
    :class:`~graphia.eval_ledger.LedgerParseError` to show a friendly error
    state, not a traceback), flattens it with
    :func:`~graphia.eval_ledger.build_table_model`, and pushes the
    :class:`LedgerTableScreen`.
    """

    TITLE = "Graphia eval ledger"

    # Esc (and q) quit the viewer — it's a read-only browser with nothing to
    # save, so a single keystroke exit is the expected affordance.
    BINDINGS = [
        Binding("escape", "quit", "Quit", show=True),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        super().__init__()
        self._path = Path(path)

    def on_mount(self) -> None:
        """Load + flatten the ledger and push the table screen.

        A missing or empty ledger yields ``[]`` (a normal empty state, not an
        error); malformed YAML raises :class:`LedgerParseError`, which we catch
        and turn into a readable error screen rather than letting the traceback
        escape.
        """
        try:
            records = load_ledger(self._path)
        except LedgerParseError as exc:
            self.push_screen(LedgerTableScreen(model=None, error=str(exc)))
            return
        model = build_table_model(records)
        self.push_screen(LedgerTableScreen(model=model))


def main(argv: list[str] | None = None) -> None:
    """CLI entry: ``python -m graphia.ui.ledger_viewer [--path FILE]``.

    ``--path`` points the viewer at an alternate ledger (default
    :data:`~graphia.tools.blunder_eval.LEDGER_PATH`), so a maintainer can inspect
    a ledger somewhere other than the repo-committed one. Mirrors the ``tools/``
    argparse idiom.
    """
    parser = argparse.ArgumentParser(
        prog="graphia.ui.ledger_viewer",
        description="Browse the eval quality ledger (evals/blunder-ledger.yaml) "
        "as a scrollable table.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=LEDGER_PATH,
        help="Path to the ledger YAML to view "
        "(default: the repo-committed evals/blunder-ledger.yaml).",
    )
    args = parser.parse_args(argv)
    LedgerViewerApp(path=args.path).run()


if __name__ == "__main__":
    main()
