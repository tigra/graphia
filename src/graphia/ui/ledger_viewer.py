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
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Static

from graphia.eval_ledger import (
    LedgerParseError,
    METRIC_ORDER,
    TableModel,
    build_table_model,
    load_ledger,
)
from graphia.tools.blunder_eval import LEDGER_PATH

# The empty-ledger copy shown in #empty-state when the ledger is missing or has
# no records. (Slice 2 adds a *distinct* no-matches message for a non-empty
# query that filters everything out — kept separate on purpose.)
_EMPTY_LEDGER_MESSAGE = "No runs recorded yet."

# The friendly copy shown when the ledger file exists but is malformed YAML
# (``load_ledger`` raised ``LedgerParseError``) — a readable error state in the
# same #empty-state slot instead of a traceback escaping to the terminal.
_PARSE_ERROR_PREFIX = "Could not read the ledger:"


class LedgerTableScreen(Screen):
    """The table screen: a scrollable :class:`DataTable` of the flattened ledger.

    Composes the ``#ledger-table`` :class:`DataTable` at ``height: 1fr`` plus a
    hidden ``#empty-state`` :class:`Static` shown in its place when there is
    nothing to render (empty/missing ledger, or a parse error). The numeric
    metric columns are wrapped in a right-justified Rich :class:`Text` at
    ``add_row`` time; the fixed identity columns stay plain strings.

    Slice 2's search ``Input`` + match-count and Slice 3's row-select →
    detail-screen drill-down slot in here later; this builds the structure
    (a top dock region for the input, ``cursor_type="row"`` already set) but
    implements neither.
    """

    DEFAULT_CSS = """
    LedgerTableScreen {
        layout: vertical;
    }

    /* Reserved top dock region for the Slice 2 search Input + match-count.
       Empty for now (no child docked here yet) so the table fills the screen;
       the Input slots in here without disturbing the table layout. */
    #search-region {
        dock: top;
        height: auto;
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

    def compose(self) -> ComposeResult:
        # The reserved (currently empty) top region the Slice 2 search Input
        # docks into. Mounted now so adding the Input later is a localized change.
        yield Static(id="search-region")
        # fixed_columns=0 is deliberate (everything scrolls together);
        # fixed_columns=1 would pin the date column.
        yield DataTable(id="ledger-table", cursor_type="row")
        # Hidden by default; shown (and the table hidden) for the empty/error
        # states in on_mount.
        yield Static(self._empty_message(), id="empty-state")

    def on_mount(self) -> None:
        """Populate the table, or switch to the #empty-state for no-data states."""
        table = self.query_one("#ledger-table", DataTable)
        empty = self.query_one("#empty-state", Static)

        if self._model is None or not self._model.rows:
            # Empty/missing ledger or a parse error: hide the grid, show the
            # message. (The Static already carries the right copy from compose.)
            table.display = False
            empty.display = True
            return

        empty.display = False
        self._populate(table, self._model)

    def _populate(self, table: DataTable, model: TableModel) -> None:
        """Fill the DataTable from the model, right-justifying the metric cells.

        The fixed leading columns (``⚠``, Date, Provider, models, Games) stay
        plain strings; the trailing metric columns are wrapped in a
        right-justified Rich :class:`Text` so the numeric rates line up under
        their headers. The split point is the fixed-column count derived from the
        model shape, so it tracks an added/removed fixed column upstream.
        """
        table.add_columns(*model.columns)
        fixed = len(model.columns) - len(METRIC_ORDER)
        for cells in model.rows:
            rendered = [
                cell if i < fixed else Text(cell, justify="right")
                for i, cell in enumerate(cells)
            ]
            table.add_row(*rendered)

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
