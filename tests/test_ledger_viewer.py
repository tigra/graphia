"""Pilot tests for the standalone eval-ledger viewer (spec 012, Slice 1).

Drives :class:`graphia.ui.ledger_viewer.LedgerViewerApp` through Textual's
``async with app.run_test() as pilot:`` harness (the same convention as
``tests/test_app_boot.py``), asserting on **widget/model state via the pilot
API** — never against live-rendered bytes. Three states are covered:

1. a populated ledger (2-3 real-shaped records) → the ``#ledger-table``
   ``DataTable`` has one row per record, the column headers match the data
   layer's model, and a known cell (a date, a formatted metric string) is
   present;
2. an empty / missing ledger (the app pointed at a non-existent ``tmp_path``
   file) → ``#empty-state`` is displayed with "No runs recorded yet.", the
   table is hidden, and there is no crash;
3. a tall fixture (30 records) renders structurally without error (exercises
   the scrollable grid path).

The ledger path is injected via the app constructor and always points at a
``tmp_path`` file — the committed ``evals/blunder-ledger.yaml`` is **never**
touched. This viewer never imports ``load_config``, so no LLM / AWS / env
setup is needed (the autouse ``safe_llm`` net is irrelevant here).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from textual.widgets import DataTable, Static

from graphia.eval_ledger import METRIC_ORDER, build_table_model, load_ledger
from graphia.ui.ledger_viewer import LedgerViewerApp

from conftest import plain_text

# The empty-ledger copy the viewer shows in #empty-state. Mirrors the module
# constant ``_EMPTY_LEDGER_MESSAGE`` (asserted by value, not imported, so the
# test pins the user-visible string rather than coupling to a private name).
_EMPTY_LEDGER_MESSAGE = "No runs recorded yet."


# A pre-provenance record (run.games, no code/settings/CI) — exercises the
# heterogeneity end-to-end through the viewer.
_PRE_PROVENANCE_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-10'
      games: 3
      metrics_version: 1
    provider:
      name: 'ollama'
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
    quality:
      games_attempted: 3
      games_completed: 3
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.45384615384615384
        count: 59
        denominator: 130
    """
)

# A full record with the CI band, a flat dotted vote-metric key, and notes.
_FULL_WITH_CI_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-11'
      duration_seconds: 1072.842
      metrics_version: 1
    code:
      commit: 'e7dd42c90d1ea581f3836103addf50842037a592'
      branch: 'main'
      dirty: true
    provider:
      name: 'bedrock'
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
    settings:
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
      games: 20
      seed: null
    quality:
      games_attempted: 20
      games_completed: 20
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.5541740674955595
        count: 624
        denominator: 1126
        ci_low: 0.525005635243927
        ci_high: 0.5829741028007689
      self_vote.initiation:
        rate: 0.0
        count: 0
        denominator: 13
        ci_low: 0.0
        ci_high: 0.22810184305529166
    notes: 'reliable baseline n=20 plus Wilson CI'
    """
)


def _write_ledger(tmp_path: Path, *docs: str, name: str = "ledger.yaml") -> Path:
    """Write a ``---``-separated multi-document ledger to a temp file."""
    text = "".join(f"---\n{doc}" for doc in docs)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ===========================================================================
# B1. Populated ledger — row count, headers, a known cell present
# ===========================================================================


async def test_viewer_populates_table_from_real_shaped_ledger(tmp_path: Path) -> None:
    """A 2-record ledger fills the DataTable: one row per record, headers match.

    Asserts via the pilot/widget API — ``row_count``, the column-header labels,
    and that a known cell value (a date) is present — never against rendered
    bytes.
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)
    expected = build_table_model(load_ledger(ledger))

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The viewer pushes a LedgerTableScreen on mount, so the widgets live on
        # the top of the screen stack — query the current screen, not the app's
        # base default screen.
        table = app.screen.query_one("#ledger-table", DataTable)

        # One row per ledger record.
        assert table.row_count == 2

        # The DataTable's column labels match the data-layer model's columns.
        header_labels = [str(col.label) for col in table.columns.values()]
        assert header_labels == expected.columns

        # A known cell is present: the first row's Date column carries the
        # pre-provenance record's date.
        date_col_index = expected.columns.index("Date")
        rows = list(table.rows.keys())
        first_date = table.get_cell_at((0, date_col_index))
        assert str(first_date) == "2026-06-10"
        # And the empty-state is hidden when there are rows to show.
        assert app.screen.query_one("#empty-state", Static).display is False
        # Sanity: the row keys really cover both records.
        assert len(rows) == 2


async def test_viewer_renders_a_formatted_metric_cell(tmp_path: Path) -> None:
    """The CI-banded metric string survives into a DataTable cell.

    The metric cells are wrapped in a right-justified Rich ``Text`` by the
    viewer, so the cell is read back and its ``.plain`` compared to the model's
    formatted string — asserting model→cell fidelity, not rendered geometry.
    """
    ledger = _write_ledger(tmp_path, _FULL_WITH_CI_DOC)
    model = build_table_model(load_ledger(ledger))
    repetition_col = model.columns.index("repetition")
    expected_cell = model.rows[0][repetition_col]

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        cell = table.get_cell_at((0, repetition_col))

    # The metric column is a right-justified Rich Text; compare its plain text.
    plain = cell.plain if hasattr(cell, "plain") else str(cell)
    assert plain == expected_cell
    # The on-disk shape really does produce the banded format.
    assert expected_cell == "0.55 [0.53–0.58] 624/1126"


async def test_viewer_finds_the_data_table_by_type(tmp_path: Path) -> None:
    """The screen exposes exactly one DataTable, locatable by widget type."""
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.id == "ledger-table"
        assert table.row_count == 2


# ===========================================================================
# B2. Empty / missing ledger — empty-state shown, table hidden, no crash
# ===========================================================================


async def test_viewer_missing_ledger_shows_empty_state(tmp_path: Path) -> None:
    """A non-existent ledger file → #empty-state displayed, table hidden, no crash."""
    missing = tmp_path / "no-such-ledger.yaml"
    assert not missing.exists()

    app = LedgerViewerApp(path=missing)
    async with app.run_test() as pilot:
        await pilot.pause()
        empty = app.screen.query_one("#empty-state", Static)
        table = app.screen.query_one("#ledger-table", DataTable)

        # The friendly empty-ledger copy is shown...
        assert _EMPTY_LEDGER_MESSAGE in plain_text(empty)
        # ...the message is visible and the grid is hidden.
        assert empty.display is True
        assert table.display is False
        # No rows were ever added.
        assert table.row_count == 0

    # The app shut down cleanly (no crash / unhandled exception).
    assert app.is_running is False


async def test_viewer_empty_file_shows_empty_state(tmp_path: Path) -> None:
    """An existing-but-empty ledger file also resolves to the empty state."""
    empty_file = tmp_path / "empty.yaml"
    empty_file.write_text("", encoding="utf-8")

    app = LedgerViewerApp(path=empty_file)
    async with app.run_test() as pilot:
        await pilot.pause()
        empty = app.screen.query_one("#empty-state", Static)
        table = app.screen.query_one("#ledger-table", DataTable)
        assert empty.display is True
        assert _EMPTY_LEDGER_MESSAGE in plain_text(empty)
        assert table.display is False


# ===========================================================================
# B3. Tall fixture — 30 records render structurally without error
# ===========================================================================


async def test_viewer_renders_a_tall_ledger_without_error(tmp_path: Path) -> None:
    """A 30-record ledger populates every row (exercises the scrollable grid)."""
    docs = [_FULL_WITH_CI_DOC if i % 2 else _PRE_PROVENANCE_DOC for i in range(30)]
    ledger = _write_ledger(tmp_path, *docs, name="tall.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        assert table.row_count == 30
        # The empty-state is hidden; the grid is shown.
        assert app.screen.query_one("#empty-state", Static).display is False
        # Structurally scroll to the bottom — must not raise.
        table.move_cursor(row=29)
        await pilot.pause()

    assert app.is_running is False
