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

import importlib
import textwrap
from pathlib import Path

import pytest
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
)

from graphia.eval_ledger import (
    METRIC_ORDER,
    SEARCH_SCOPE_ALL,
    build_table_model,
    list_transcripts,
    load_ledger,
)
from graphia.ui.ledger_viewer import (
    DetailScreen,
    LedgerTableScreen,
    LedgerViewerApp,
    TranscriptScreen,
)

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


# An ollama record whose NOTE deliberately mentions 'bedrock' — the
# scoped-search disambiguation anchor (Slice 5). A bare free-text 'bedrock'
# substring then appears in BOTH this row's note-derived blob AND the real
# bedrock record's provider, so a free-text 'bedrock' keeps two rows while the
# scoped 'provider:bedrock' keeps only the genuine bedrock row (scoped <
# free-text).
_OLLAMA_NOTE_BEDROCK_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-12'
      games: 4
      metrics_version: 1
    provider:
      name: 'ollama'
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
    quality:
      games_attempted: 4
      games_completed: 4
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.5
        count: 10
        denominator: 20
    notes: 'local rerun cross-checked against the bedrock baseline'
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


# ===========================================================================
# B4. Live search filter (Slice 2) — typing filters rows, #match-count tracks,
#     clearing restores, a no-match query swaps in the distinct empty copy
# ===========================================================================
#
# The two module fixtures give deterministic, non-overlapping match targets:
#   _PRE_PROVENANCE_DOC → provider 'ollama', date '2026-06-10', no notes;
#   _FULL_WITH_CI_DOC   → provider 'bedrock', date '2026-06-11', notes
#                         'reliable baseline n=20 plus Wilson CI'.
# So 'ollama' / 'bedrock' each survive exactly one row, '2026-06' both, and
# 'baseline' only the full record — substrings the data layer's lowercased
# search blobs are asserted to carry by tests/test_ledger_model.py.

# The distinct no-match copy the viewer shows when a non-empty query survives
# zero rows. Mirrors the module helper ``_no_match_message`` (asserted by value,
# echoing the ORIGINAL-case query) — kept separate from the empty-ledger copy.
def _no_match_message(query: str) -> str:
    return f"No runs match '{query}'."


async def _drive_search(pilot, query: str) -> None:
    """Set the #search Input value and pump the event loop so the rebuild lands.

    Setting ``Input.value`` fires ``Input.Changed`` → the screen's
    ``on_input_changed`` filter; a single ``pilot.pause()`` lets that handler
    rebuild the DataTable and update #match-count before assertions read them.
    """
    pilot.app.screen.query_one("#search", Input).value = query
    await pilot.pause()


async def _set_field_scope(pilot, field: str) -> None:
    """Set the #field-select scope value and pump the loop so the refilter lands.

    Setting ``Select.value`` fires ``Select.Changed`` → the screen's
    ``on_select_changed`` filter; a single ``pilot.pause()`` lets that handler
    rebuild the DataTable and #match-count before assertions read them.
    """
    pilot.app.screen.query_one("#field-select", Select).value = field
    await pilot.pause()


async def test_search_filters_rows_and_updates_match_count(tmp_path: Path) -> None:
    """A matching query drops row_count to the matching subset; #match-count tracks.

    'bedrock' is present in exactly one record's search blob, so the table keeps
    one row and #match-count reads "Showing 1 of 2".
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        match_count = app.screen.query_one("#match-count", Static)

        # Initial render shows every row: "Showing N of N".
        assert table.row_count == 2
        assert plain_text(match_count) == "Showing 2 of 2"

        await _drive_search(pilot, "bedrock")

        # Only the bedrock record survives; the table stays visible.
        assert table.row_count == 1
        assert table.display is True
        assert plain_text(match_count) == "Showing 1 of 2"
        # The surviving row really is the bedrock record (its Date cell).
        date_col = build_table_model(load_ledger(ledger)).columns.index("Date")
        assert str(table.get_cell_at((0, date_col))) == "2026-06-11"
        # The no-match empty-state stayed hidden (this query matched).
        assert app.screen.query_one("#empty-state", Static).display is False


async def test_search_matching_multiple_records_keeps_all_matches(
    tmp_path: Path,
) -> None:
    """A query common to several records keeps exactly those rows.

    '2026-06' is a substring of both records' dates, so both rows survive and
    #match-count reads "Showing 2 of 2"; the narrower 'baseline' (only in the
    full record's notes) drops it to one.
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        match_count = app.screen.query_one("#match-count", Static)

        await _drive_search(pilot, "2026-06")
        assert table.row_count == 2
        assert plain_text(match_count) == "Showing 2 of 2"

        # A notes-only substring present in just the full record.
        await _drive_search(pilot, "baseline")
        assert table.row_count == 1
        assert plain_text(match_count) == "Showing 1 of 2"


async def test_search_is_case_insensitive(tmp_path: Path) -> None:
    """An upper-case query matches the lowercased blob (provider 'ollama')."""
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)

        await _drive_search(pilot, "OLLAMA")
        assert table.row_count == 1
        assert plain_text(app.screen.query_one("#match-count", Static)) == (
            "Showing 1 of 2"
        )


async def test_clearing_search_restores_all_rows(tmp_path: Path) -> None:
    """Emptying the Input restores every row and resets #match-count to N of N."""
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        match_count = app.screen.query_one("#match-count", Static)

        # Narrow down first...
        await _drive_search(pilot, "bedrock")
        assert table.row_count == 1

        # ...then clear: all rows return, the grid is shown, count is full.
        await _drive_search(pilot, "")
        assert table.row_count == 2
        assert table.display is True
        assert plain_text(match_count) == "Showing 2 of 2"
        assert app.screen.query_one("#empty-state", Static).display is False


async def test_no_match_query_hides_table_and_shows_distinct_copy(
    tmp_path: Path,
) -> None:
    """A guaranteed-no-match query hides the table and shows the no-match copy.

    The #empty-state Static must read "No runs match '<query>'." echoing the
    ORIGINAL-case query — distinct from the empty-ledger "No runs recorded yet."
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        empty = app.screen.query_one("#empty-state", Static)

        await _drive_search(pilot, "zzz-no-such-run")

        # Table hidden, the distinct no-match empty-state shown.
        assert table.display is False
        assert empty.display is True
        assert plain_text(empty) == "No runs match 'zzz-no-such-run'."
        # It is NOT the empty-ledger copy — the two states stay distinct.
        assert plain_text(empty) != _EMPTY_LEDGER_MESSAGE


async def test_no_match_copy_echoes_original_case_query(tmp_path: Path) -> None:
    """The no-match message echoes the query verbatim (original case preserved)."""
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        empty = app.screen.query_one("#empty-state", Static)

        await _drive_search(pilot, "ZZ-NoSuchRun")
        assert plain_text(empty) == _no_match_message("ZZ-NoSuchRun")


async def test_no_match_then_clear_restores_the_grid(tmp_path: Path) -> None:
    """Recovering from a no-match query: clearing re-shows the table, hides empty."""
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        empty = app.screen.query_one("#empty-state", Static)

        await _drive_search(pilot, "zzz-no-such-run")
        assert table.display is False
        assert empty.display is True

        await _drive_search(pilot, "")
        assert table.display is True
        assert empty.display is False
        assert table.row_count == 2
        assert plain_text(app.screen.query_one("#match-count", Static)) == (
            "Showing 2 of 2"
        )


# ===========================================================================
# B5. Full-record drill-down (Slice 3) — row-select pushes a DetailScreen
#     showing that row's note; escape pops back with the cursor restored.
# ===========================================================================
#
# Each record below carries a DISTINCT notes string so the DetailScreen's
# #detail-body can be matched back to the exact row that was selected. The
# drill-down is exercised through the REAL RowSelected path. The table is focused
# by default on mount; _focus_table re-asserts it defensively so a test that
# previously moved focus to the search box still drives the row-select path.


def _doc_with_note(date: str, note: str) -> str:
    """A minimal full-shape ledger document carrying a distinct date + note."""
    return textwrap.dedent(
        f"""\
        run:
          date: '{date}'
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
        notes: '{note}'
        """
    )


async def _focus_table(pilot) -> DataTable:
    """Return the #ledger-table, re-asserting focus so key presses reach it.

    The table holds focus by default on mount; this re-focuses it defensively
    (a no-op there) so a test that first moved focus to the search box still
    drives the real ``RowSelected`` key path.
    """
    table = pilot.app.screen.query_one("#ledger-table", DataTable)
    table.focus()
    await pilot.pause()
    return table


async def test_row_select_opens_detail_screen_with_that_rows_note(
    tmp_path: Path,
) -> None:
    """Cursor → a NON-first row, ``enter`` → a DetailScreen showing that note.

    Three distinct-note records; the cursor is moved down to row 1 (the second
    record), then ``enter`` is pressed via the real RowSelected path. The pushed
    screen must be a ``DetailScreen`` and its ``#detail-body`` must carry that
    row's note text (and not another row's).
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_note("2026-06-01", "alpha note for the first record"),
        _doc_with_note("2026-06-02", "bravo note for the second record"),
        _doc_with_note("2026-06-03", "charlie note for the third record"),
        name="drilldown.yaml",
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = await _focus_table(pilot)
        assert table.row_count == 3

        # Move the cursor to a known NON-first row (row index 1).
        await pilot.press("down")
        assert table.cursor_row == 1

        # Open the detail screen via the real RowSelected key path.
        await pilot.press("enter")
        await pilot.pause()

        # A DetailScreen is now on top of the stack...
        assert isinstance(app.screen, DetailScreen)
        body = app.screen.query_one("#detail-body", Static)
        body_text = plain_text(body)
        # ...showing the SELECTED row's note (the second record's).
        assert "bravo note for the second record" in body_text
        # ...and not a sibling row's note (the drill-down resolved the right record).
        assert "alpha note for the first record" not in body_text
        assert "charlie note for the third record" not in body_text


async def test_escape_pops_detail_and_restores_table_cursor(tmp_path: Path) -> None:
    """``escape`` pops the DetailScreen back to the table with the cursor restored.

    After drilling into row 1, ``escape`` returns to the ``LedgerTableScreen`` and
    ``on_screen_resume`` moves the cursor back to the row that was drilled into.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_note("2026-06-01", "alpha note for the first record"),
        _doc_with_note("2026-06-02", "bravo note for the second record"),
        _doc_with_note("2026-06-03", "charlie note for the third record"),
        name="restore.yaml",
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = await _focus_table(pilot)

        # Drill into row 1.
        await pilot.press("down")
        assert table.cursor_row == 1
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)

        # Escape pops back to the table screen...
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, LedgerTableScreen)

        # ...with the cursor restored to the row that was drilled into.
        restored = app.screen.query_one("#ledger-table", DataTable)
        assert restored.cursor_row == 1


# ===========================================================================
# B6. Read-only guarantee (functional-spec §2.5) — a full browsing session
#     (filter + drill-down + back) leaves the ledger file byte-identical.
# ===========================================================================


async def test_full_session_leaves_ledger_byte_identical(tmp_path: Path) -> None:
    """A full session (filter → clear → drill-down → escape) never touches the file.

    The viewer is strictly read-only (functional-spec §2.5): after applying a
    filter, clearing it, opening a detail screen and escaping back — all in one
    ``run_test`` session — the on-disk ledger bytes must be IDENTICAL to before.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_note("2026-06-01", "alpha note for the first record"),
        _doc_with_note("2026-06-02", "bravo note for the second record"),
        name="readonly.yaml",
    )
    before = ledger.read_bytes()

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Apply a filter, then clear it.
        await _drive_search(pilot, "bravo")
        assert app.screen.query_one("#ledger-table", DataTable).row_count == 1
        await _drive_search(pilot, "")

        # Drill into a row and escape back.
        table = await _focus_table(pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, LedgerTableScreen)

    after = ledger.read_bytes()
    assert before == after


# ===========================================================================
# B6. Focus model — the table is focused by default so navigation works; the
#     search box is opt-in (``/``), and escape inside it backs out to the table
#     instead of quitting.
# ===========================================================================


async def test_table_is_focused_on_mount_not_the_search_input(
    tmp_path: Path,
) -> None:
    """Navigation keys must reach the table immediately — the table holds focus.

    Regression guard: the docked search ``Input`` must NOT grab initial focus,
    otherwise arrow keys / Enter are swallowed as search text and the table is
    unnavigable.
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)
    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        assert table.has_focus
        assert not app.screen.query_one("#search", Input).has_focus

        # Arrow keys drive the cursor (proof the table receives navigation keys).
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 1


async def test_slash_focuses_search_and_escape_returns_to_table(
    tmp_path: Path,
) -> None:
    """``/`` jumps to the search box; ``escape`` there backs out to the table.

    The app binds ``escape`` to quit, but inside the search box it must instead
    return focus to the table (and leave the viewer running) — so a user who
    opened search can get back to the rows without killing the viewer.
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)
    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        search = app.screen.query_one("#search", Input)

        await pilot.press("slash")
        await pilot.pause()
        assert search.has_focus

        # A printable key typed in the box is captured as text, not a quit.
        await pilot.press("b")
        await pilot.pause()
        assert search.value == "b"
        assert app.is_running

        # Escape backs out to the table WITHOUT quitting.
        await pilot.press("escape")
        await pilot.pause()
        assert table.has_focus
        assert app.is_running


async def test_escape_on_table_quits_the_viewer(tmp_path: Path) -> None:
    """With the table focused (the default), ``escape`` quits — the app binding."""
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)
    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#ledger-table", DataTable).has_focus
        await pilot.press("escape")
        await pilot.pause()
    assert not app.is_running
    assert app.return_code == 0


# ===========================================================================
# B7. Selector-scoped search (Slice 6) — the #field-select dropdown scopes the
#     #search value to one field, distinct from the "All" free-text behaviour.
# ===========================================================================
#
# The typed ``field:value`` syntax is GONE: scope is the #field-select Select's
# job. The fixture deliberately overlaps a bare 'bedrock' substring across two
# rows:
#   _FULL_WITH_CI_DOC        → provider 'bedrock' (genuine);
#   _OLLAMA_NOTE_BEDROCK_DOC → provider 'ollama' but its NOTE mentions 'bedrock'.
# So under "All" a 'bedrock' value keeps BOTH rows, while with the selector on
# ``provider`` the same value keeps only the genuine bedrock row — proving the
# scoped match checks the provider field, not the whole blob (scoped < All).
# Both drive through the real widgets via _set_field_scope / _drive_search.


async def test_provider_scope_narrows_to_the_named_field(
    tmp_path: Path,
) -> None:
    """Selector on ``provider`` + value 'bedrock' keeps only the genuine bedrock row.

    The ollama row's note mentions 'bedrock', so under "All" a bare 'bedrock'
    would keep it too — but with the selector on ``provider`` the value checks the
    provider field and drops it. The surviving row is the real bedrock record.
    """
    ledger = _write_ledger(tmp_path, _OLLAMA_NOTE_BEDROCK_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        match_count = app.screen.query_one("#match-count", Static)

        assert table.row_count == 2
        assert plain_text(match_count) == "Showing 2 of 2"

        # Pick the provider scope, then type the value into #search.
        await _set_field_scope(pilot, "provider")
        await _drive_search(pilot, "bedrock")

        # Only the genuine bedrock row survives the scoped value.
        assert table.row_count == 1
        assert table.display is True
        assert plain_text(match_count) == "Showing 1 of 2"
        # The surviving row is the real bedrock record (date '2026-06-11').
        date_col = build_table_model(load_ledger(ledger)).columns.index("Date")
        assert str(table.get_cell_at((0, date_col))) == "2026-06-11"
        assert app.screen.query_one("#empty-state", Static).display is False


async def test_all_scope_is_wider_than_the_provider_scope(
    tmp_path: Path,
) -> None:
    """Under "All" 'bedrock' keeps BOTH rows; under ``provider`` it keeps one.

    The note-mention overlap makes the two scopes differ: the same typed value
    'bedrock' hits the ollama row's note-derived blob under "All" (2 rows), but
    checks the provider field only when the selector is on ``provider`` (1 row).
    """
    ledger = _write_ledger(tmp_path, _OLLAMA_NOTE_BEDROCK_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        match_count = app.screen.query_one("#match-count", Static)

        # Selector defaults to "All": 'bedrock' matches the provider AND the note
        # mention → 2 rows.
        assert (
            app.screen.query_one("#field-select", Select).value == SEARCH_SCOPE_ALL
        )
        await _drive_search(pilot, "bedrock")
        assert table.row_count == 2
        assert plain_text(match_count) == "Showing 2 of 2"

        # Switching the selector to ``provider`` narrows to the provider field → 1 row.
        await _set_field_scope(pilot, "provider")
        assert table.row_count == 1
        assert plain_text(match_count) == "Showing 1 of 2"


async def test_select_changed_refilters_live(tmp_path: Path) -> None:
    """Changing the selector with a value present refilters live (row_count updates).

    With 'bedrock' already typed, flipping the selector All → provider → All
    re-runs the filter on each Select.Changed: provider drops to 1 row, back to
    All restores 2 — proving the selector drives the live refilter.
    """
    ledger = _write_ledger(tmp_path, _OLLAMA_NOTE_BEDROCK_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)

        await _drive_search(pilot, "bedrock")
        assert table.row_count == 2  # All scope

        await _set_field_scope(pilot, "provider")
        assert table.row_count == 1  # scoped refilter, value unchanged

        await _set_field_scope(pilot, SEARCH_SCOPE_ALL)
        assert table.row_count == 2  # back to All, refiltered again


async def test_clearing_value_after_a_scoped_search_restores_all_rows(
    tmp_path: Path,
) -> None:
    """Emptying the value after a scoped search restores every row."""
    ledger = _write_ledger(tmp_path, _OLLAMA_NOTE_BEDROCK_DOC, _FULL_WITH_CI_DOC)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        match_count = app.screen.query_one("#match-count", Static)

        await _set_field_scope(pilot, "provider")
        await _drive_search(pilot, "bedrock")
        assert table.row_count == 1

        await _drive_search(pilot, "")
        assert table.row_count == 2
        assert table.display is True
        assert plain_text(match_count) == "Showing 2 of 2"
        assert app.screen.query_one("#empty-state", Static).display is False


# ===========================================================================
# B8. Boundary arrow nav + Backspace drill-back (Slice 6) — the field selector
#     and value Input hand focus across the search-row boundary, and Backspace
#     returns from the detail screen.
# ===========================================================================


async def test_right_on_field_select_focuses_the_value_input(
    tmp_path: Path,
) -> None:
    """``right`` on the collapsed selector jumps focus into the #search Input.

    The right half of the boundary-jump nav: with the field selector focused
    (collapsed), pressing ``right`` hands focus to the value Input on its right.
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)
    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        select = app.screen.query_one("#field-select", Select)
        search = app.screen.query_one("#search", Input)

        # Focus the (collapsed) selector, then press right.
        select.focus()
        await pilot.pause()
        assert select.has_focus
        assert not select.expanded

        await pilot.press("right")
        await pilot.pause()
        assert search.has_focus


async def test_left_at_input_start_focuses_the_field_select(
    tmp_path: Path,
) -> None:
    """``left`` at the value Input's start edge jumps focus back to the selector.

    The left half of the boundary-jump nav: with the value Input focused and its
    caret at position 0 (empty value), pressing ``left`` hands focus to the field
    selector on its left rather than being a no-op.
    """
    ledger = _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_WITH_CI_DOC)
    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        select = app.screen.query_one("#field-select", Select)
        search = app.screen.query_one("#search", Input)

        # Focus the value Input with an empty value → caret at position 0.
        search.focus()
        await pilot.pause()
        assert search.has_focus
        assert search.cursor_position == 0

        await pilot.press("left")
        await pilot.pause()
        assert select.has_focus


async def test_backspace_returns_from_the_detail_screen(tmp_path: Path) -> None:
    """``backspace`` on a DetailScreen pops back to the LedgerTableScreen.

    Opens the drill-down via the real RowSelected path (the table must be focused
    first), then presses ``backspace`` — the DetailScreen's new ``backspace``→
    close binding returns to the table screen.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_note("2026-06-01", "alpha note for the first record"),
        _doc_with_note("2026-06-02", "bravo note for the second record"),
        name="backspace.yaml",
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus_table(pilot)

        # Open the detail screen via the real RowSelected key path.
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)

        # Backspace returns from the drill-down to the table screen.
        await pilot.press("backspace")
        await pilot.pause()
        assert isinstance(app.screen, LedgerTableScreen)
        assert app.is_running


async def test_detail_screen_shows_viewer_chrome_and_back_hint(
    tmp_path: Path,
) -> None:
    """The DetailScreen frames the record with a Header + Footer back hint.

    So a full-window record stays recognisably the ledger viewer and shows how to
    return: a Header carrying the viewer name + a "back" subtitle, and a Footer
    surfacing the Esc/Backspace → "Back" bindings.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_note("2026-06-01", "alpha note for the first record"),
        name="chrome.yaml",
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus_table(pilot)
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DetailScreen)

        # Header (with the viewer name + a back-hint subtitle) and a Footer frame
        # the record.
        assert screen.query(Header)
        assert screen.query(Footer)
        assert screen.title == "Graphia eval ledger"
        assert "back" in (screen.sub_title or "").lower()

        # Esc AND Backspace are surfaced as shown "Back" bindings (the Footer's
        # source of truth).
        shown_back = {
            entry.binding.key
            for entry in screen.active_bindings.values()
            if entry.binding.show and entry.binding.description == "Back"
        }
        assert {"escape", "backspace"} <= shown_back


# ===========================================================================
# B9. Cell cursor auto-scroll — the highlighted cell is scrolled fully into
#     view as it moves, instead of nudging the viewport a character at a time.
# ===========================================================================


def _cell_fully_visible(table: DataTable) -> bool:
    """Is the highlighted cell entirely within the table's scrolled viewport?

    Compares the cursor cell's region (in the table's virtual content space,
    via ``_get_cell_region`` on the pinned Textual 8.2.4) against the visible
    window offset by the current ``scroll_offset`` — true only when the whole
    cell, both axes, falls inside the viewport.
    """
    region = table._get_cell_region(table.cursor_coordinate)
    offset = table.scroll_offset
    window = table.scrollable_content_region
    return (
        region.x >= offset.x
        and region.x + region.width <= offset.x + window.width
        and region.y >= offset.y
        and region.y + region.height <= offset.y + window.height
    )


async def test_cell_cursor_scrolls_the_highlighted_cell_fully_into_view(
    tmp_path: Path,
) -> None:
    """Moving the cell cursor right scrolls the highlighted cell entirely into view.

    The table uses a **cell** cursor (not a row cursor), and a wide table in a
    narrow viewport keeps the highlighted cell fully visible as the cursor pans —
    the requested behaviour, vs. the old character-wise horizontal scroll.
    """
    # Two full-width rows (the model always emits all metric columns), in a
    # viewport too narrow to show them all → horizontal scroll is required.
    ledger = _write_ledger(
        tmp_path,
        _doc_with_note("2026-06-01", "alpha"),
        _doc_with_note("2026-06-02", "bravo"),
        name="wide.yaml",
    )
    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=(40, 16)) as pilot:
        await pilot.pause()
        table = await _focus_table(pilot)
        assert table.cursor_type == "cell"

        last_col = len(table.ordered_columns) - 1
        # Pan to the last column; the highlight must stay fully visible at the end
        # (and it had to scroll right to get there, since the table is wider).
        for _ in range(last_col):
            await pilot.press("right")
        await pilot.pause()
        assert table.cursor_coordinate.column == last_col
        assert table.scroll_offset.x > 0  # the viewport followed the cursor
        assert _cell_fully_visible(table)

        # Pan back to the first column; it scrolls home so that cell is visible.
        for _ in range(last_col):
            await pilot.press("left")
        await pilot.pause()
        assert table.cursor_coordinate.column == 0
        assert table.scroll_offset.x == 0
        assert _cell_fully_visible(table)


# ===========================================================================
# B10. Transcript browse (spec 017, Slice 2 — PORTED onto the panel by spec 037,
#      Slice 3) — from a run's DetailScreen, ``t`` moves focus into the
#      always-present transcript PANEL (it no longer opens a screen of its own);
#      selecting a game pushes a read-only TranscriptScreen showing that file's
#      text; Esc/Backspace/q step back out transcript → record → table, with
#      nothing in between. A run with NO preserved games shows the plain
#      "No transcripts for this run." message in the panel. The viewer never
#      writes.
# ===========================================================================
#
# WHAT MOVED, AND WHY THESE TESTS DID NOT SIMPLY GO AWAY. Spec 037 deleted the
# intermediate ``TranscriptListScreen``, so every assertion below that read
# ``isinstance(app.screen, TranscriptListScreen)`` lost the thing it was written
# against. What those tests *proved*, though — the run's games are listed in
# sorted order, picking one opens the RIGHT game's text verbatim, the empty state
# shows a plain message, the back-out keys step back out, and a browse session
# never writes to a file — are all still requirements (tech-spec §3). Each is
# re-expressed here against the panel: the ids are the panel's
# (``#transcript-panel-list`` / ``#transcript-panel-empty``, deliberately not the
# deleted screen's), every "a screen opened" assertion became a "NO screen
# opened" one, and the back-out path is one step shorter.
#
# Two guarantees the new surface needs are NOT ports and live in B13 at the end
# of this file (after the helpers they share with B11/B12): the highlighted game
# surviving the round trip into a transcript and back, and no key path reaching a
# screen whose only content is a list of games.
#
# THE HELPERS B11/B12 INTRODUCED ARE REUSED, NOT DUPLICATED — ``_open_detail_for_row``,
# ``_panel_labels``, ``_ledger_with_games``, ``_PANEL_TERMINAL_SIZE``,
# ``_EXPECTED_PANE_GEOMETRY``, ``_DETAIL_STACK_DEPTH``. They are *defined below*
# this section but called from it, which Python resolves at call time.
#
# The transcript store lives in the ledger's SIBLING ``transcripts/<run-id>/``
# dir (the layout ``blunder_eval`` writes), so each record below names its run
# via ``run.transcript_dir`` and the matching dir is created under ``tmp_path``
# — the committed ``evals/transcripts/`` is NEVER touched. The transcript body
# carries a recognizable ``<transcript>`` tag plus a unique marker string, so
# the rendered TranscriptScreen body can be matched back to the exact file.

# The plain "no transcripts" copy the panel shows when a run has none. Asserted
# by value (echoing the module's ``_NO_TRANSCRIPTS_MESSAGE``) so the test pins
# the user-visible string, not a private name.
_NO_TRANSCRIPTS_MESSAGE = "No transcripts for this run."

# A unique marker woven into the synthetic transcript body, so the rendered
# TranscriptScreen can be matched back to this exact file (never asserted on
# real game prose — this is a fabricated transcript fixture).
_TRANSCRIPT_MARKER = "UNIQUE-MARKER-Zx42"

_TRANSCRIPT_BODY = (
    "<transcript>\n"
    "  <setup>\n"
    f"    Alice — Mafioso (legend: baker / true_self: {_TRANSCRIPT_MARKER})\n"
    "  </setup>\n"
    "  <night>\n"
    "    round 1: Alice points at Bob\n"
    "  </night>\n"
    "</transcript>\n"
)


def _numbered_transcript_marker(game: int) -> str:
    """A marker unique to game ``N``, so an opened transcript identifies itself.

    ``_TRANSCRIPT_MARKER`` proves *a* file's text reached the screen; this proves
    **which** file did. Needed because the panel now resolves a selection through
    an index-parallel entries list (tech-spec §2 C), so "selecting a game opens
    the right game" is a claim about index arithmetic that identical bodies
    cannot test — every game would pass.
    """
    return f"UNIQUE-GAME-{game:02d}-MARKER"


def _numbered_transcript_body(game: int) -> str:
    """The synthetic transcript body for game ``N``, carrying its own marker."""
    return f"{_numbered_transcript_marker(game)}\n{_TRANSCRIPT_BODY}"


# The app's boot Screen + LedgerTableScreen + DetailScreen + TranscriptScreen.
# That is ``_DETAIL_STACK_DEPTH`` (3, B12 below) plus exactly ONE, and the "plus
# one" is the whole point of spec 037: the route to a game is
# table → detail → transcript, with NO screen in between. A depth of 5 here would
# mean the intermediate list screen was back. Pinned as an ABSOLUTE depth, like
# B12's, rather than as a before/after comparison.
_TRANSCRIPT_STACK_DEPTH = 4


def _doc_with_transcript_dir(date: str, run_id: str | None) -> str:
    """A minimal full-shape ledger doc; carries ``run.transcript_dir`` when set.

    ``run_id`` None → NO ``transcript_dir`` field (an older pre-017 record, the
    "no transcripts" case); a string → the run-id NAME the viewer resolves
    against the ledger's sibling ``transcripts/`` dir.
    """
    transcript_line = f"  transcript_dir: '{run_id}'\n" if run_id is not None else ""
    return textwrap.dedent(
        f"""\
        run:
          date: '{date}'
          metrics_version: 1
        """
    ) + transcript_line + textwrap.dedent(
        """\
        provider:
          name: 'ollama'
          large_model: 'qwen3-coder:30b'
          small_model: 'qwen2.5:3b'
        quality:
          games_attempted: 2
          games_completed: 2
          games_failed_early: 0
        metrics:
          repetition:
            rate: 0.5
            count: 10
            denominator: 20
        notes: 'run with browsable transcripts'
        """
    )


def _write_run_transcripts(
    ledger_path: Path, run_id: str, files: dict[str, str]
) -> Path:
    """Create the ledger's SIBLING ``transcripts/<run-id>/`` dir + game files.

    Mirrors ``blunder_eval``'s on-disk layout (``<ledger>/../transcripts/``),
    entirely inside the caller's ``tmp_path`` — the committed
    ``evals/transcripts/`` is never touched. Returns the run dir.
    """
    run_dir = ledger_path.parent / "transcripts" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (run_dir / name).write_text(body, encoding="utf-8")
    return run_dir


def _transcript_body_text(screen: TranscriptScreen) -> str:
    """The TranscriptScreen body's text, read via the widget API (not bytes).

    The body is a ``markup=False`` ``Static`` whose ``render()`` returns a
    Textual ``Content`` of the verbatim file text. Read it back through that
    renderable (preferring its ``.plain``) so the literal ``<transcript>`` tags
    survive — the shared ``plain_text`` helper's ``Text.from_markup`` fallback
    would mangle the ``<...>`` tokens — asserting model→widget fidelity, not
    rendered geometry.
    """
    body = screen.query_one("#transcript-body", Static)
    rendered = body.render()
    return rendered.plain if hasattr(rendered, "plain") else str(rendered)


async def _open_detail_for_first_row(pilot) -> DetailScreen:
    """Focus the table and drill into row 0 via the real CellSelected key path."""
    table = pilot.app.screen.query_one("#ledger-table", DataTable)
    table.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(pilot.app.screen, DetailScreen)
    return pilot.app.screen


async def _highlight_game(pilot, index: int) -> ListView:
    """Move the panel's highlight to row ``index`` using only real keystrokes.

    Enters the panel with ``t`` (the reviewer's shortcut) and walks down with
    ``down`` — never by assigning ``ListView.index``, so the walk goes through
    the same key routing the feature relies on. Asserts it arrived, so a caller's
    later "the right game opened" assertion cannot be satisfied by a highlight
    that never moved.
    """
    await pilot.press("t")
    await pilot.pause()
    listing = pilot.app.screen.query_one("#transcript-panel-list", ListView)
    assert pilot.app.screen.focused is listing
    assert listing.index == 0
    for _ in range(index):
        await pilot.press("down")
    await pilot.pause()
    assert listing.index == index
    return listing


async def test_t_on_detail_focuses_the_panel_listing_the_runs_games(
    tmp_path: Path,
) -> None:
    """``t`` moves focus into the panel listing the run's games — and opens no screen.

    Ported from ``test_t_on_detail_opens_transcript_list_of_the_runs_games``,
    which asserted ``t`` pushed a ``TranscriptListScreen``. Under spec 037 the
    same key is a shortcut *into* the always-present panel (functional-spec §2.4):
    the run has two transcripts, ``t`` puts focus on the list of exactly them
    (game-01, game-02, in sorted order) with the "no transcripts" message still
    hidden, **no new screen is pushed**, and the run's figures stay visible beside
    the list rather than being covered up.
    """
    ledger = _write_ledger(
        tmp_path, _doc_with_transcript_dir("2026-06-18", "run-A"), name="tlist.yaml"
    )
    _write_run_transcripts(
        ledger,
        "run-A",
        {"game-02.txt": "second " + _TRANSCRIPT_BODY, "game-01.txt": _TRANSCRIPT_BODY},
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_first_row(pilot)
        listing = screen.query_one("#transcript-panel-list", ListView)

        # Premise: focus starts on the figures, so the press below is provably a
        # MOVE rather than a lucky match with where focus already was.
        assert screen.focused is screen.query_one("#detail-scroll", VerticalScroll)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH

        # Press ``t`` to reach the run's transcripts.
        await pilot.press("t")
        await pilot.pause()

        # It moved focus into the panel and pushed NOTHING: the same DetailScreen
        # object is still on top, at the same absolute stack depth.
        assert screen.focused is listing
        assert listing.has_focus is True
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH

        # The list carries one ListItem per game, sorted game-01 before game-02.
        assert _panel_labels(screen) == ["game-01", "game-02"]
        # The list is shown; the "no transcripts" message is hidden.
        assert listing.display is True
        assert screen.query_one("#transcript-panel-empty", Static).display is False

        # Nothing was covered up (functional-spec §2.4): the figures are still
        # displayed beside the list, with the record's own text in them, and both
        # panes still measure the pinned layout — an absolute check, because two
        # panes that had both collapsed would satisfy "still displayed".
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        assert scroll.display is True
        assert "run with browsable transcripts" in plain_text(
            screen.query_one("#detail-body", Static)
        )
        assert _pane_geometry(screen) == _EXPECTED_PANE_GEOMETRY


@pytest.mark.parametrize("picked", [0, 1, 2])
async def test_selecting_a_game_shows_its_transcript_text(
    tmp_path: Path, picked: int
) -> None:
    """Selecting a game pushes a TranscriptScreen showing THAT file's text.

    Ported from the same-named test, which selected the only game of a one-game
    run from the deleted list screen. Two things changed and both are now pinned:
    the selection is made in the panel (so the push comes from
    ``DetailScreen.on_list_view_selected``), and the run has **three** games with
    per-game markers, so "the right game" is a real claim — the original could
    not distinguish a correct index from any other.

    Each case highlights its game with real keystrokes, opens it, and asserts the
    marker of that game is on screen while the other two games' markers are not.
    The literal ``<transcript>`` tags must survive too (the body is
    ``markup=False``).
    """
    ledger = _ledger_with_games(tmp_path, 3, name="tshow.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_first_row(pilot)
        assert _panel_labels(screen) == ["game-01", "game-02", "game-03"]

        # Highlight the game under test via the real key path, then select it.
        await _highlight_game(pilot, picked)
        await pilot.press("enter")
        await pilot.pause()

        # A TranscriptScreen is now on top — pushed straight from the
        # DetailScreen, at the absolute depth that leaves no room for a menu.
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH

        # ...showing the picked file's verbatim text, and no other game's.
        body_text = _transcript_body_text(app.screen)
        assert _numbered_transcript_marker(picked + 1) in body_text
        for other in (1, 2, 3):
            if other != picked + 1:
                assert _numbered_transcript_marker(other) not in body_text
        # The literal tags survive (the body is markup=False).
        assert "<transcript>" in body_text


async def test_back_out_transcript_to_record_to_table(tmp_path: Path) -> None:
    """Stepping back out: transcript → the run's DetailScreen → the table.

    Ported from ``test_back_out_transcript_to_list_to_record``, which stepped
    transcript → list → record. The middle screen is gone, so the same two keys
    now cover one step each of a shorter path: ``escape`` on the TranscriptScreen
    returns **directly** to the run's figures, and ``backspace`` there returns to
    the table. Each step is pinned by absolute stack depth as well as screen type,
    so a screen that was pushed and stayed could not pass.
    """
    ledger = _write_ledger(
        tmp_path, _doc_with_transcript_dir("2026-06-18", "run-C"), name="tback.yaml"
    )
    _write_run_transcripts(ledger, "run-C", {"game-01.txt": _TRANSCRIPT_BODY})

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_first_row(pilot)

        await _highlight_game(pilot, 0)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
        # The game really is on screen before we back out of it (the ported
        # "the file's text reached the screen verbatim" assertion, kept here for
        # the plain `_TRANSCRIPT_BODY` fixture), so the escape below is a step
        # back out of something rather than off an empty screen.
        assert _TRANSCRIPT_MARKER in _transcript_body_text(app.screen)

        # escape: transcript → the run's figures, in ONE step. The screen behind
        # is the very DetailScreen we came from, with its figures on screen.
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        assert "run with browsable transcripts" in plain_text(
            screen.query_one("#detail-body", Static)
        )

        # backspace: the run's DetailScreen → the table.
        await pilot.press("backspace")
        await pilot.pause()
        assert isinstance(app.screen, LedgerTableScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH - 1
        assert app.is_running


async def test_back_out_to_record_then_table_restores_cursor(tmp_path: Path) -> None:
    """Popping the whole transcript stack lands back on the run's row in the table.

    Drill into row 1 (a non-first row), step into the panel, read a game, then
    step all the way out — which now takes **two** escapes rather than three:
    transcript → DetailScreen → table, with the table cursor restored to row 1
    (spec 012's return-on-resume pattern, unperturbed by the transcript detour).

    Ported from the same-named test; the assertion that the middle escape landed
    on a ``TranscriptListScreen`` became the assertion that it lands on the run's
    figures instead, and the escape that used to be needed to leave that screen
    is gone.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-01", None),
        _doc_with_transcript_dir("2026-06-02", "run-D"),
        name="tcursor.yaml",
    )
    _write_run_transcripts(ledger, "run-D", {"game-01.txt": _TRANSCRIPT_BODY})

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()

        # Drill into row 1 (the run WITH transcripts).
        screen = await _open_detail_for_row(pilot, 1)

        # Into the panel, read a game.
        await _highlight_game(pilot, 0)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH

        # Step all the way out: transcript → detail → table. TWO escapes, not
        # three (functional-spec §2.6: back from a game lands on the figures, not
        # on a list screen that then has to be left as well).
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, LedgerTableScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH - 1

        # The table cursor is restored to the row that was drilled into.
        restored = app.screen.query_one("#ledger-table", DataTable)
        assert restored.cursor_row == 1
        assert app.is_running


@pytest.mark.parametrize(
    ("case", "run_id", "make_dir"),
    [
        pytest.param("no transcript_dir field", None, False, id="pre-017-record"),
        pytest.param("named dir absent locally", "run-not-pulled", False, id="not-pulled"),
        pytest.param("named dir present but empty", "run-empty-dir", True, id="empty-dir"),
    ],
)
async def test_run_with_no_games_shows_the_plain_message_in_the_panel(
    tmp_path: Path, case: str, run_id: str | None, make_dir: bool
) -> None:
    """Every "this run has no games" cause shows the plain message in the panel.

    Ports **two** tests at once — ``test_run_with_no_transcripts_shows_plain_message``
    (a pre-017 record with no ``transcript_dir`` at all) and
    ``test_run_with_missing_transcript_dir_shows_plain_message`` (a record naming
    a dir that was never pulled) — which asserted the identical state on the
    deleted list screen, and adds the third cause the panel's docstring claims
    (a dir that exists but is empty). All three land on ``list_transcripts``
    returning ``[]``, and all three must show the plain message rather than an
    error.

    Pressing ``t`` on such a run must still move focus into the panel and push
    nothing, and selecting in the hidden, row-less list must do nothing at all —
    the ported "no crash, the viewer is still running" assertion, now with the
    keystroke that could crash it actually pressed.
    """
    ledger = _write_ledger(
        tmp_path, _doc_with_transcript_dir("2026-06-18", run_id), name="tnone.yaml"
    )
    if make_dir:
        assert run_id is not None
        _write_run_transcripts(ledger, run_id, {})

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_first_row(pilot)

        await pilot.press("t")
        await pilot.pause()

        # No screen was opened by the key that used to open one.
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH

        # The list is hidden; the plain "no transcripts" message is shown.
        listing = screen.query_one("#transcript-panel-list", ListView)
        empty = screen.query_one("#transcript-panel-empty", Static)
        assert listing.display is False
        assert empty.display is True
        assert plain_text(empty) == _NO_TRANSCRIPTS_MESSAGE
        assert _panel_labels(screen) == [], case
        # The panel still took focus even though its list is hidden, so the
        # "select" below is pressed somewhere it could actually land.
        assert screen.focused is listing

        # Selecting nothing does nothing: no screen, no error, no crash.
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        assert app.is_running


async def test_browsing_transcripts_leaves_files_byte_unchanged(
    tmp_path: Path,
) -> None:
    """The full transcript browse session leaves the transcript files byte-identical.

    The viewer is strictly read-only (functional-spec §2.2/§2.5): capture each
    game file's bytes before, drive the full open → read → back-out flow across
    two games, and assert every file is byte-identical afterwards. The viewer
    never writes.

    Ported from the same-named test — the loop now backs out to the run's
    DetailScreen instead of the deleted list screen, and moves the highlight with
    ``down`` instead of assigning ``ListView.index``. Two premises were added,
    because the original's ``after == before`` would have held just as well over
    an empty dict or files that were never opened: the fixture really has two
    non-empty files, and each iteration really rendered *that* game's text.
    """
    ledger = _ledger_with_games(tmp_path, 2, name="treadonly.yaml")
    run_dir = ledger.parent / "transcripts" / _GAMES_RUN_ID
    before = {p.name: p.read_bytes() for p in sorted(run_dir.glob("game-*.txt"))}
    # PREMISE: there is something to leave unchanged (an empty mapping, or files
    # nothing ever read, would satisfy the final equality vacuously).
    assert sorted(before) == ["game-01.txt", "game-02.txt"]
    assert all(body for body in before.values())

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_first_row(pilot)
        listing = await _highlight_game(pilot, 0)
        assert len(listing.children) == 2

        # Open each game in turn, reading it, then backing out to the figures.
        for index in range(len(before)):
            if index:
                await pilot.press("down")
                await pilot.pause()
            assert listing.index == index
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, TranscriptScreen)
            # Read the body (a pure read; must not mutate the file) — and prove
            # the read reached THIS game, so the loop cannot pass by opening
            # nothing.
            assert _numbered_transcript_marker(index + 1) in _transcript_body_text(
                app.screen
            )
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, DetailScreen)
            assert app.screen is screen

    after = {p.name: p.read_bytes() for p in sorted(run_dir.glob("game-*.txt"))}
    assert after == before
    # Nor did browsing add or remove a file (a new sibling would leave every
    # captured file byte-identical and still break the read-only guarantee).
    assert sorted(p.name for p in run_dir.iterdir()) == sorted(before)


# ===========================================================================
# B11. Transcript side panel (spec 037, Slice 1) — a run's games are listed in
#      a narrow panel BESIDE its figures, for every run. Both panes are on
#      screen with nothing pressed, focus starts on the figures, and the
#      details pane keeps an IDENTICAL measured width whether the run has
#      preserved games or not (the no-layout-shift requirement).
# ===========================================================================
#
# Everything here is asserted on widget identity + GEOMETRY read through the
# pilot API — never on rendered colour. The focus indication is a border colour
# drawn from theme variables (tech-spec §3): theme-dependent, reviewed by eye,
# and a poor test subject. What a test *can* pin is which widget holds focus and
# how many columns each pane occupies.
#
# The panel's width is the load-bearing number of the design: a FIXED 22
# columns rather than `width: auto`, precisely so the details pane cannot change
# width between a run with games and a run without. The measurements below were
# taken by hand at a 100x30 terminal and are pinned here so that a later switch
# to a content-derived width (`width: auto`, `width: 1fr`, a label-length
# calculation) fails loudly instead of quietly reintroducing the layout jump.
#
# NOTE the panel-prefixed ids — `#transcript-panel-list` / `#transcript-panel-empty`
# — deliberately NOT the `#transcript-list` / `#transcript-empty` of the
# intermediate screen Slice 3 has since deleted. The two surfaces coexisted while
# this slice landed, and identical ids on two live screens would have made B10's
# assertions ambiguous; the panel-prefixed ids are what B10 targets now.

# The terminal the panel measurements below were taken at. Fixed explicitly (not
# the harness default) because every width assertion in this section is a
# concrete column count derived from it: 100 - 22 = 78 for the details pane.
_PANEL_TERMINAL_SIZE = (100, 30)

# The panel's fixed column count (the single place it lives in production CSS is
# `#transcript-panel { width: 22; }`).
_PANEL_OUTER_WIDTH = 22
# ...of which the round border eats two columns: Textual sizes border-box, so
# `outer 22 / inner 20` is the proof of that (a content-box implementation would
# report 24/22 outer).
_PANEL_INNER_WIDTH = 20

# The details pane takes "whatever the fixed panel leaves" (`width: 1fr` in a
# horizontal layout), so at a 100-column terminal it is exactly 100 - 22.
_DETAILS_OUTER_WIDTH = 78
_DETAILS_INNER_WIDTH = 76


def _pane_geometry(screen: DetailScreen) -> dict[str, int]:
    """Measure both panes of a :class:`DetailScreen` as plain column counts.

    Returned as a dict so two runs' layouts can be compared in **one** equality
    assertion (and so a failure names the measurement that moved). ``outer_size``
    is the border-box width Textual laid the pane out at; ``size`` is the inner
    content width; ``region.x`` is where the pane starts, which is what proves
    the two panes sit side by side rather than stacked.
    """
    scroll = screen.query_one("#detail-scroll", VerticalScroll)
    panel = screen.query_one("#transcript-panel", Vertical)
    return {
        "details_x": scroll.region.x,
        "details_outer": scroll.outer_size.width,
        "details_inner": scroll.size.width,
        "panel_x": panel.region.x,
        "panel_outer": panel.outer_size.width,
        "panel_inner": panel.size.width,
    }


# The geometry every run must produce, whatever its transcripts. Written out in
# full (rather than derived) so the numbers are readable at the assertion site.
_EXPECTED_PANE_GEOMETRY = {
    "details_x": 0,
    "details_outer": _DETAILS_OUTER_WIDTH,
    "details_inner": _DETAILS_INNER_WIDTH,
    "panel_x": _DETAILS_OUTER_WIDTH,  # the panel starts where the details end
    "panel_outer": _PANEL_OUTER_WIDTH,
    "panel_inner": _PANEL_INNER_WIDTH,
}


def _panel_labels(screen: DetailScreen) -> list[str]:
    """The panel list's row labels, read through the widget API."""
    listing = screen.query_one("#transcript-panel-list", ListView)
    return [plain_text(item.query_one(Label)) for item in listing.query(ListItem)]


async def _open_detail_for_row(pilot, row: int) -> DetailScreen:
    """Focus the table, move to ``row``, and drill in via the real key path.

    The row-general form of :func:`_open_detail_for_first_row` — needed here
    because the no-layout-shift test compares a run WITH transcripts against one
    WITHOUT **in the same ledger and the same session**, so it must reach the
    second row.
    """
    table = pilot.app.screen.query_one("#ledger-table", DataTable)
    table.focus()
    await pilot.pause()
    for _ in range(row):
        await pilot.press("down")
    await pilot.pause()
    assert table.cursor_row == row
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(pilot.app.screen, DetailScreen)
    return pilot.app.screen


async def test_opening_a_run_shows_both_panes_with_nothing_pressed(
    tmp_path: Path,
) -> None:
    """Opening a run puts the figures and the games list on screen together.

    Nothing is pressed after the drill-down: no ``t``, no ``right``. Both panes
    must already be displayed, side by side (the panel starting exactly where
    the details pane ends), with the run's figures in the left pane and the run's
    games listed in the right one.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-20", "run-panel"),
        name="panel-both.yaml",
    )
    _write_run_transcripts(
        ledger,
        "run-panel",
        {"game-02.txt": "second " + _TRANSCRIPT_BODY, "game-01.txt": _TRANSCRIPT_BODY},
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)

        # The horizontal split holds both panes, and both are displayed.
        assert screen.query_one("#detail-split")
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        panel = screen.query_one("#transcript-panel", Vertical)
        assert scroll.display is True
        assert panel.display is True

        # Side by side, not stacked: same row, and the panel starts exactly
        # where the details pane ends (so neither covers the other).
        assert panel.region.y == scroll.region.y
        assert panel.region.x == scroll.region.x + scroll.region.width

        # The figures are in the left pane...
        assert "run with browsable transcripts" in plain_text(
            screen.query_one("#detail-body", Static)
        )
        # ...and this run's games in the right one, in the pure layer's order.
        assert _panel_labels(screen) == ["game-01", "game-02"]
        assert screen.query_one("#transcript-panel-list", ListView).display is True
        assert screen.query_one("#transcript-panel-empty", Static).display is False

        # No extra screen was summoned to reveal the list — the DetailScreen is
        # still the top of the stack.
        assert app.screen is screen


async def test_panel_is_present_for_a_run_with_no_transcripts(tmp_path: Path) -> None:
    """A run with no preserved games keeps the panel, showing the short note.

    The list is hidden, the plain "No transcripts for this run." message is
    displayed, and the panel itself is still laid out in the same place with its
    border intact — asserted as a two-column border gutter, not as a colour.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-20", None),
        name="panel-empty.yaml",
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)

        panel = screen.query_one("#transcript-panel", Vertical)
        listing = screen.query_one("#transcript-panel-list", ListView)
        empty = screen.query_one("#transcript-panel-empty", Static)

        # The panel is present and in place...
        assert panel.display is True
        assert panel.region.x == _DETAILS_OUTER_WIDTH
        assert panel.outer_size.width == _PANEL_OUTER_WIDTH
        # ...with its border still drawn: a round edge consuming exactly the two
        # columns that keep `outer 22 / inner 20`. (Only the border's COLOUR
        # changes with focus, so nothing reflows when focus moves — the border
        # itself is unconditional, which is what this pins.)
        assert panel.styles.border.top[0] == "round"
        assert panel.outer_size.width - panel.size.width == 2

        # The list is hidden and the short note is shown instead.
        assert listing.display is False
        assert empty.display is True
        assert plain_text(empty) == _NO_TRANSCRIPTS_MESSAGE
        assert _panel_labels(screen) == []
        # No error, no crash.
        assert app.is_running


async def test_details_pane_width_is_identical_with_and_without_transcripts(
    tmp_path: Path,
) -> None:
    """THE no-layout-shift test: the details pane measures the same for both runs.

    One ledger, two runs — row 0 has two preserved games, row 1 has none — opened
    in turn in the **same** session at the **same** terminal size. Every measured
    column count must match, so the reviewer moving between such runs sees no
    jump. This is the criterion the fixed 22-column panel exists to satisfy: a
    content-derived width (`width: auto`) would give the empty run's 28-character
    message a different width from the 7-character ``game-01`` labels and this
    equality would break.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-20", "run-has-games"),
        _doc_with_transcript_dir("2026-06-21", None),
        name="panel-noshift.yaml",
    )
    _write_run_transcripts(
        ledger,
        "run-has-games",
        {"game-01.txt": _TRANSCRIPT_BODY, "game-02.txt": "second " + _TRANSCRIPT_BODY},
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()

        # The run WITH games.
        screen = await _open_detail_for_row(pilot, 0)
        assert _panel_labels(screen) == ["game-01", "game-02"]
        with_transcripts = _pane_geometry(screen)

        # Back out to the table, then open the run WITHOUT games.
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, LedgerTableScreen)
        screen = await _open_detail_for_row(pilot, 1)
        assert _panel_labels(screen) == []
        assert screen.query_one("#transcript-panel-empty", Static).display is True
        without_transcripts = _pane_geometry(screen)

    # The layout did not move — every measurement is identical...
    assert with_transcripts == without_transcripts
    # ...and it is the pinned fixed-width layout, not merely two equal accidents
    # (a details pane that had collapsed to 0 in both cases would satisfy the
    # equality above but not this).
    assert with_transcripts == _EXPECTED_PANE_GEOMETRY


@pytest.mark.parametrize(
    ("case", "transcript_files"),
    [
        ("no games", []),
        ("one game", ["game-01.txt"]),
        ("thirty games", [f"game-{i:02d}.txt" for i in range(1, 31)]),
        (
            "a label far wider than the panel",
            ["game-01-an-extremely-long-label-far-wider-than-the-panel.txt"],
        ),
    ],
)
async def test_panel_width_is_fixed_not_derived_from_its_content(
    tmp_path: Path, case: str, transcript_files: list[str]
) -> None:
    """The panel measures 22 columns whatever it contains — width is never content.

    Sweeps the content shapes a content-derived width would size differently:
    nothing (the 28-character empty-state note), one short ``game-01`` label,
    thirty labels (vertical overflow), and one label 56 characters wide. All four
    must produce the identical pinned geometry. This is the assertion that fails
    if `#transcript-panel`'s ``width: 22`` is ever relaxed to ``auto`` — the
    guarantee is by construction, and this is what defends it.
    """
    run_id = "run-widths"
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-20", run_id if transcript_files else None),
        name="panel-widths.yaml",
    )
    if transcript_files:
        _write_run_transcripts(
            ledger, run_id, {name: _TRANSCRIPT_BODY for name in transcript_files}
        )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)

        # The content really is the shape this case intends (so a fixture that
        # silently listed nothing could not pass the geometry check by accident).
        assert len(_panel_labels(screen)) == len(transcript_files)
        assert _pane_geometry(screen) == _EXPECTED_PANE_GEOMETRY, case


@pytest.mark.parametrize("has_transcripts", [True, False])
async def test_focus_starts_on_the_details_pane(
    tmp_path: Path, has_transcripts: bool
) -> None:
    """Focus starts on the figures, for a run with games and one without alike.

    So the reviewer's first ``up``/``down`` scrolls the figures they came to read
    rather than moving a highlight in the panel. Asserted as the focused
    **widget's identity** (never a rendered colour): ``#detail-scroll`` holds
    focus and the panel's list does not.
    """
    run_id = "run-focus"
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-20", run_id if has_transcripts else None),
        name="panel-focus.yaml",
    )
    if has_transcripts:
        _write_run_transcripts(ledger, run_id, {"game-01.txt": _TRANSCRIPT_BODY})

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)

        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        assert scroll.has_focus is True
        assert screen.focused is scroll
        assert listing.has_focus is False


class _DetailScreenWithLeadingFocusable(DetailScreen):
    """Test-only :class:`DetailScreen` with a focusable widget composed FIRST.

    A harness for one regression guard, not a production shape. Textual's
    auto-focus takes the first focusable widget in composition order, and in the
    real screen that happens to be ``#detail-scroll`` — so a "focus starts on the
    details" assertion would pass even if the explicit ``on_mount`` focus were
    deleted. Putting a focusable :class:`~textual.widgets.Input` ahead of the
    split moves that accident: auto-focus now lands on the probe, and only an
    explicit focus-by-name can still put focus on the details pane.

    Textual dispatches ``on_mount`` to every class in the MRO that defines it, so
    the inherited :meth:`DetailScreen.on_mount` still runs — this subclass adds a
    widget and overrides nothing.
    """

    def compose(self):
        yield Input(id="probe-focusable")
        yield from super().compose()


async def test_initial_focus_is_explicit_not_composition_order(
    tmp_path: Path,
) -> None:
    """Initial focus is set BY NAME, so reordering compose cannot move it.

    The guard behind the previous test. With a focusable probe composed ahead of
    the split, Textual's auto-focus picks the probe (asserted below via the focus
    chain, so the premise cannot rot) — yet focus still ends on
    ``#detail-scroll``, which is only possible because ``on_mount`` names it.
    Delete that line and this test fails while the plain focus test would not.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-20", "run-probe"),
        name="panel-probe.yaml",
    )
    _write_run_transcripts(ledger, "run-probe", {"game-01.txt": _TRANSCRIPT_BODY})
    record = load_ledger(ledger)[0]
    entries = list_transcripts(record, ledger)
    assert [entry.label for entry in entries] == ["game-01"]

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        app.push_screen(_DetailScreenWithLeadingFocusable(record, entries))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, _DetailScreenWithLeadingFocusable)

        # The premise: the probe really is what auto-focus would reach first.
        chain = [widget.id for widget in screen.focus_chain]
        assert chain[0] == "probe-focusable"
        assert "detail-scroll" in chain

        # The guarantee: focus is on the details pane regardless.
        assert screen.query_one("#detail-scroll", VerticalScroll).has_focus is True
        assert screen.query_one("#probe-focusable", Input).has_focus is False


# ===========================================================================
# B12. Pane focus movement (spec 037, Slice 2) — `right` moves focus into the
#      games panel and `left` moves it back to the figures, non-wrapping and
#      pushing no screen; `up`/`down` are deliberately UNBOUND on the screen,
#      so Textual delivers them to whichever pane holds focus.
# ===========================================================================
#
# Continues B11's posture: every assertion here is on the **focused widget's
# identity** (or a plain integer read through the widget API), never on a
# rendered colour. The focus indication is a theme-variable border colour
# (tech-spec §3) — reviewed by eye, useless as a test subject; which widget
# Textual considers focused is exact and observable.
#
# Every keystroke goes through `pilot.press(...)`, never a direct `.focus()`
# call: the *bindings* are what this slice added, so bypassing them would test
# the two one-line actions and none of the wiring that reaches them.
#
# TWO TRAPS these tests are shaped around:
#
# 1. **"Focus is unchanged" is satisfied by a keystroke that went nowhere.**
#    If the bindings were deleted entirely, `right` on the already-focused panel
#    would still leave focus exactly where it was — the no-op assertion passes
#    while the feature is gone. So the no-op test *spies on the action* to prove
#    the binding fired, and every no-op test also asserts a *move* worked in the
#    same session.
# 2. **"X did not change" is satisfied when X could not have changed.**
#    (B11 learned this the hard way: an equality between two runs held while the
#    layout was wrecked.) "The details did not scroll" means nothing if the
#    record fitted on screen, and "the highlight did not move" means nothing
#    with one row in the list. Each such test therefore pins the *premise*
#    (`max_scroll_y > 0` AND `allow_vertical_scroll is True`, a 3-row list)
#    alongside an *absolute* post-state (`scroll_y == 0`, `index == 0`) rather
#    than a before/after comparison. Both halves of the scroll premise are
#    load-bearing: `overflow-y: hidden` on the figures pane leaves
#    `max_scroll_y` at 20 while `allow_vertical_scroll` goes False — measured,
#    and it turned four of these tests vacuous until the second half was added.

# The DetailScreen sits on the app's boot Screen + the LedgerTableScreen, so a
# stack of exactly 3 means "the DetailScreen is on top and nothing was pushed
# over it". Pinned as an ABSOLUTE depth rather than a before/after comparison —
# a pushed-and-still-there screen would satisfy "unchanged" at both ends.
_DETAIL_STACK_DEPTH = 3


# The run-id `_ledger_with_games` names, and therefore the sibling
# `transcripts/<run-id>/` dir it writes under `tmp_path`. Named here rather than
# inline so a test can find the files it wrote (B10's read-only test reads their
# bytes back).
_GAMES_RUN_ID = "run-pane-nav"


def _ledger_with_games(tmp_path: Path, count: int, *, name: str) -> Path:
    """Write a one-run ledger whose single run has ``count`` preserved games.

    ``count == 0`` writes the run with **no** ``transcript_dir`` at all (the
    empty-panel case); otherwise the sibling ``transcripts/<run-id>/`` dir gets
    ``game-01 … game-NN``, entirely inside ``tmp_path``.

    Each game's body carries its own :func:`_numbered_transcript_marker`, so a
    test that opens one can prove **which** game it got — B10's ported
    selection tests and B13's round trip both depend on that; the focus tests
    here only count labels and never read a body.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-22", _GAMES_RUN_ID if count else None),
        name=name,
    )
    if count:
        _write_run_transcripts(
            ledger,
            _GAMES_RUN_ID,
            {
                f"game-{i:02d}.txt": _numbered_transcript_body(i)
                for i in range(1, count + 1)
            },
        )
    return ledger


async def test_right_focuses_the_panel_and_left_returns_to_the_details(
    tmp_path: Path,
) -> None:
    """One keypress each way: ``right`` into the games list, ``left`` back out.

    The core of Slice 2. Asserted as widget identity at each step — and the
    starting state (focus on the figures) is asserted first, so the two presses
    below are provably *moves* rather than a lucky match with wherever focus
    already was.
    """
    ledger = _ledger_with_games(tmp_path, 3, name="nav-both-ways.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        # Premise: focus starts on the figures (Slice 1's guarantee).
        assert screen.focused is scroll
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH

        # right → the games list is the focused widget.
        await pilot.press("right")
        await pilot.pause()
        assert screen.focused is listing
        assert listing.has_focus is True
        assert scroll.has_focus is False

        # left → focus is back on the figures.
        await pilot.press("left")
        await pilot.pause()
        assert screen.focused is scroll
        assert scroll.has_focus is True
        assert listing.has_focus is False

        # Neither direction ever left the screen: same DetailScreen object, same
        # absolute stack depth as before the presses.
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


@pytest.mark.parametrize(
    ("pre_presses", "key", "action_name", "expected_focus_id"),
    [
        pytest.param(
            ("right",),
            "right",
            "action_focus_panel",
            "transcript-panel-list",
            id="right-while-already-on-the-panel",
        ),
        pytest.param(
            (),
            "left",
            "action_focus_details",
            "detail-scroll",
            id="left-while-already-on-the-details",
        ),
    ],
)
async def test_pressing_a_direction_already_there_changes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pre_presses: tuple[str, ...],
    key: str,
    action_name: str,
    expected_focus_id: str,
) -> None:
    """``right`` on the panel / ``left`` on the details: no move, no new screen.

    Non-wrapping is currently *free* (each action focuses one named widget, so
    there is no cycle to wrap) rather than defended, which is exactly why it
    needs pinning: a later refactor to ``focus_next()``/``focus_previous()``
    would wrap to the other pane and this would fail.

    The trap: "focus is unchanged" would ALSO hold if the binding did not exist
    at all — the keystroke would fall through the pane's inherited scroll action
    and off the end of the chain, changing nothing. So the action is **spied**:
    the binding must provably fire, *and* leave everything as it was. Neither
    half alone is worth much.
    """
    ledger = _ledger_with_games(tmp_path, 3, name="nav-no-op.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        # Get to the pane under test (the details pane already has focus, so the
        # `left` case presses nothing) and confirm we are really there.
        for press in pre_presses:
            await pilot.press(press)
        await pilot.pause()
        already_focused = screen.focused
        assert already_focused is not None
        assert already_focused.id == expected_focus_id

        # Spy on the action so a passing test cannot mean "the keystroke went
        # nowhere". Patching the METHOD works because Textual resolves an
        # action by ``getattr`` at dispatch time; patching ``BINDINGS`` would
        # not — those are merged once, at class creation.
        calls: list[str] = []
        original = getattr(DetailScreen, action_name)

        def _spy(self: DetailScreen, *, _original=original) -> None:
            calls.append(action_name)
            _original(self)

        monkeypatch.setattr(DetailScreen, action_name, _spy)

        await pilot.press(key)
        await pilot.pause()

        # The binding fired...
        assert calls == [action_name]
        # ...and nothing moved: same focused widget, and no wrap to the other
        # pane (spelled out, since a wrap is the specific failure feared).
        assert screen.focused is already_focused
        assert screen.focused.id == expected_focus_id
        # No screen was pushed — absolute depth, not "the same as before".
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        # And the direction key was not quietly re-routed into the panes' own
        # state either: absolute post-values, both still at their mount state —
        # non-vacuous, because the list has rows to highlight and the figures
        # pane both overruns its viewport and accepts a scroll.
        assert len(listing.children) == 3
        assert scroll.allow_vertical_scroll is True
        assert listing.index == 0
        assert scroll.scroll_y == 0


async def test_down_up_with_the_panel_focused_move_the_highlight_only(
    tmp_path: Path,
) -> None:
    """With the games list focused, ``down``/``up`` move the highlight, not the figures.

    ``up``/``down`` are unbound on the screen on purpose, so this is really a
    test that Textual routes them to the focused pane. The "figures did not
    scroll" half is only meaningful because the premise below pins that the
    figures pane genuinely *has* somewhere to scroll to.
    """
    ledger = _ledger_with_games(tmp_path, 3, name="nav-panel-keys.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        await pilot.press("right")
        await pilot.pause()
        assert screen.focused is listing

        # PREMISES, all absolute: the record overruns its pane AND that pane
        # accepts a vertical scroll — so "the figures did not scroll" below is an
        # observation, not an inevitability. (Both halves are needed. Measured:
        # `overflow-y: hidden` on the pane leaves `max_scroll_y` at 20 while
        # `allow_vertical_scroll` goes False, i.e. the pane silently cannot
        # scroll at all and every "it did not scroll" assertion turns vacuous.)
        assert scroll.max_scroll_y > 0
        assert scroll.allow_vertical_scroll is True
        assert scroll.scroll_y == 0
        assert len(listing.children) == 3
        assert listing.index == 0

        # down, down, up: the highlight walks 0 → 1 → 2 → 1 and the figures
        # stay pinned at the top throughout.
        for expected_index in (1, 2):
            await pilot.press("down")
            await pilot.pause()
            assert listing.index == expected_index
            assert scroll.scroll_y == 0

        await pilot.press("up")
        await pilot.pause()
        assert listing.index == 1
        assert scroll.scroll_y == 0


async def test_down_up_with_the_details_focused_scroll_it_only(
    tmp_path: Path,
) -> None:
    """With the figures focused, ``down``/``up`` scroll them and leave the highlight.

    The mirror of the previous test, and the reason focus starts here: the
    reviewer's first ``down`` moves the record they came to read. The "highlight
    did not move" half is meaningful because the premise pins a three-row list —
    with one row an unchanged index would prove nothing.
    """
    ledger = _ledger_with_games(tmp_path, 3, name="nav-details-keys.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        # Nothing pressed: focus is on the figures already.
        assert screen.focused is scroll
        assert scroll.max_scroll_y > 0
        assert scroll.allow_vertical_scroll is True
        assert scroll.scroll_y == 0
        assert len(listing.children) == 3
        assert listing.index == 0

        # down, down, up: the figures scroll 0 → 1 → 2 → 1 and the highlighted
        # game never moves off the first row.
        for expected_offset in (1, 2):
            await pilot.press("down")
            await pilot.pause()
            assert scroll.scroll_y == expected_offset
            assert listing.index == 0

        await pilot.press("up")
        await pilot.pause()
        assert scroll.scroll_y == 1
        assert listing.index == 0


async def test_empty_run_accepts_focus_in_the_panel_and_up_down_do_nothing(
    tmp_path: Path,
) -> None:
    """A run with no games still lets focus into the panel; ``up``/``down`` raise nothing.

    **This is the ``focus_next()`` guard.** On an empty run the list is
    ``display = False``, and a hidden widget is excluded from the screen's
    focus chain — asserted below, so the premise cannot rot. The chain therefore
    holds the details pane ALONE, and any cycle-based implementation
    (``focus_next()``/``focus_previous()``) has nowhere to go and would leave
    focus exactly where it started. Only focusing the list **by name** can put
    focus where this test says it must go, which is what makes this the test
    that fails if the actions are ever refactored into a cycle.
    """
    ledger = _ledger_with_games(tmp_path, 0, name="nav-empty.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        # The empty state really is in force (else this would just be the
        # populated case under a different fixture name).
        assert listing.display is False
        assert screen.query_one("#transcript-panel-empty", Static).display is True
        assert _panel_labels(screen) == []

        # THE guard: the hidden list is not in the focus chain, so a cycle has
        # no second stop to reach.
        assert [widget.id for widget in screen.focus_chain] == ["detail-scroll"]

        # ...and yet `right` puts focus on it — only focus-by-name does that.
        await pilot.press("right")
        await pilot.pause()
        assert screen.focused is listing
        assert scroll.has_focus is False
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH

        # up/down over a hidden, row-less list: nothing happens, nothing raises,
        # and the figures behind it do not scroll either — they genuinely could
        # have, which is what these two premises pin (the pane overruns its
        # viewport AND accepts a vertical scroll).
        assert scroll.max_scroll_y > 0
        assert scroll.allow_vertical_scroll is True
        assert listing.index is None
        await pilot.press("down")
        await pilot.press("up")
        await pilot.pause()
        assert listing.index is None
        assert scroll.scroll_y == 0
        assert screen.focused is listing
        assert app.is_running is True

        # `left` still gets back out of the empty panel.
        await pilot.press("left")
        await pilot.pause()
        assert screen.focused is scroll


async def test_pane_navigation_survives_a_horizontal_scrollbar_on_the_panes(
    tmp_path: Path,
) -> None:
    """left/right stay pane navigation even when a pane CAN scroll sideways.

    **This is the ``priority=True`` guard**, and without it the bindings only
    work by accident. Both panes are scrollable containers, and
    ``ScrollableContainer.BINDINGS`` already binds ``left``→``scroll_left`` /
    ``right``→``scroll_right``; Textual checks the *focused widget's* bindings
    before the screen's. A plain screen binding is reached only because
    ``action_scroll_right`` raises ``SkipAction`` while
    ``allow_horizontal_scroll`` is False — i.e. only while no pane has a
    horizontal scrollbar, which is a fact about today's *content*.

    So: force a real horizontal scrollbar onto each pane, removing the
    fall-through, and assert focus still moves. Drop ``priority=True`` from the
    bindings and this is the test that fails; every other test in B12 keeps
    passing, because with no horizontal scrollbar the accident still holds.

    (``monkeypatch.setattr(DetailScreen, "BINDINGS", …)`` cannot express this —
    Textual merges ``BINDINGS`` at class-creation time, so a later swap is a
    no-op. The condition has to be created on the widgets instead.)
    """
    ledger = _ledger_with_games(tmp_path, 3, name="nav-priority.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        scroll = screen.query_one("#detail-scroll", VerticalScroll)
        listing = screen.query_one("#transcript-panel-list", ListView)

        # The accident, stated: with this content neither pane can scroll
        # sideways, so each pane's own left/right raises SkipAction and the
        # screen gets a turn even without priority.
        assert scroll.allow_horizontal_scroll is False
        assert listing.allow_horizontal_scroll is False

        # Now remove the accident: a real horizontal scrollbar on each pane, so
        # each pane's inherited left/right becomes a live action that would
        # consume the key.
        scroll.styles.overflow_x = "scroll"
        listing.styles.overflow_x = "scroll"
        await pilot.pause()
        await pilot.pause()
        assert scroll.allow_horizontal_scroll is True
        assert scroll.show_horizontal_scrollbar is True
        assert listing.allow_horizontal_scroll is True
        assert listing.show_horizontal_scrollbar is True

        # Focus still moves both ways — because the screen's bindings carry
        # `priority=True` and are therefore checked App-down, ahead of the
        # focused pane's own.
        assert screen.focused is scroll
        await pilot.press("right")
        await pilot.pause()
        assert screen.focused is listing

        await pilot.press("left")
        await pilot.pause()
        assert screen.focused is scroll

        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


# ===========================================================================
# B13. The round trip, and the vanished screen (spec 037, Slice 3) — the two
#      guarantees the panel needs that are NOT ports of the deleted screen's
#      tests: returning from a game leaves the SAME game highlighted, and no key
#      path anywhere in the viewer reaches a screen whose only content is a list
#      of a run's games.
# ===========================================================================
#
# Both are here because they are claims about the *new* surface. The ported
# browse tests (B10) cover what the deleted screen used to prove; these two cover
# what only a panel can get wrong.
#
# 1. "The same game is still highlighted" is the spec's one **framework
#    assumption** (tech-spec §3): Textual keeps the panel's `ListView` alive
#    across the push/pop of a `TranscriptScreen`, so its index *should* survive
#    with no state-saving code at all. That is exactly the kind of assumption
#    that fails quietly — a later `on_screen_resume` that repopulates the list,
#    or a switch from `push_screen` to `switch_screen`, resets the highlight and
#    nothing else in the suite would notice. So it is asserted, not reasoned
#    about, and asserted on a game that is NOT the first: an index of 0 is what a
#    freshly built list would also report, so game-01 would pass while broken.
#
# 2. "No key path reaches a bare list screen" is asserted two ways, because
#    neither alone is enough. STRUCTURALLY — the module defines exactly three
#    screen classes, so there is no such screen for any key to reach, and
#    importing the deleted name fails. BEHAVIOURALLY — every key `DetailScreen`
#    binds (plus the ones a reviewer would try) is pressed and the resulting
#    screen checked. And because "no screen matched the bad shape" is worthless
#    if the detector cannot match anything, the detector itself is tested against
#    a deliberately reintroduced list screen first.

# The only screens the viewer may ever present. A fourth would be either a
# regression (the intermediate list screen returning) or a feature that owes this
# list an update.
_ALLOWED_SCREEN_TYPES = (LedgerTableScreen, DetailScreen, TranscriptScreen)

# The keys swept by the no-key-path walk below: every key `DetailScreen` binds
# (rot-guarded at the assertion site against `DetailScreen.BINDINGS`, so a new
# binding cannot be added without being swept) plus the movement/activation keys
# a reviewer would try on a two-pane screen.
_DETAIL_KEYS_SWEPT = (
    "t",
    "down",
    "enter",
    "escape",
    "right",
    "left",
    "up",
    "space",
    "tab",
    "home",
    "end",
    "pageup",
    "pagedown",
    "backspace",
    "q",
)


def _is_bare_transcript_list_screen(screen: Screen) -> bool:
    """Is this screen one whose ONLY content is a list of a run's games?

    The shape functional-spec §2.6 forbids the reviewer from ever arriving at.
    Identified by content rather than by class name, so a *renamed*
    reintroduction is caught too: a `ListView` present, with none of the three
    things the legitimate screens carry — the run's figures (`#detail-body`), the
    table of runs (`#ledger-table`), or a game's text (`#transcript-body`).

    Verified to actually fire by
    :func:`test_the_bare_list_screen_detector_catches_a_reintroduced_list_screen`
    below — a predicate that can never return True would make every use of it
    vacuous.
    """
    return bool(screen.query(ListView)) and not (
        screen.query("#detail-body")
        or screen.query("#ledger-table")
        or screen.query("#transcript-body")
    )


class _BareTranscriptListScreen(Screen):
    """Test-only stand-in for the screen spec 037 deleted — the positive control.

    Reproduces the shape (and the ids) of the removed `TranscriptListScreen`:
    a full screen whose only content is a run's games. Never pushed by production
    code; mounted by one test purely to prove
    :func:`_is_bare_transcript_list_screen` recognises it.
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="transcript-list-wrapper"):
            yield ListView(ListItem(Label("game-01")), id="transcript-list")
            yield Static(_NO_TRANSCRIPTS_MESSAGE, id="transcript-empty")


async def test_the_bare_list_screen_detector_catches_a_reintroduced_list_screen(
    tmp_path: Path,
) -> None:
    """The forbidden-shape detector fires on a list screen and not on the real ones.

    The non-vacuity guard for the walk below. A predicate that returned False for
    everything would make "no key path reaches a bare list screen" pass on an
    empty universe, so it is exercised against a deliberately reintroduced list
    screen (which it must flag) and against the run's figures-plus-panel screen —
    which also holds a `ListView`, and must **not** be flagged, because the
    figures are right there beside it.
    """
    ledger = _ledger_with_games(tmp_path, 2, name="detector.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)

        # The real screens are not the forbidden shape...
        assert _is_bare_transcript_list_screen(screen) is False
        assert bool(screen.query(ListView)) is True  # ...and not by lacking a list
        assert _is_bare_transcript_list_screen(app.screen_stack[1]) is False

        # ...but a screen whose only content is a run's games is.
        app.push_screen(_BareTranscriptListScreen())
        await pilot.pause()
        reintroduced = app.screen
        assert isinstance(reintroduced, _BareTranscriptListScreen)
        assert _is_bare_transcript_list_screen(reintroduced) is True

        app.pop_screen()
        await pilot.pause()
        assert app.screen is screen


async def test_returning_from_a_game_leaves_the_same_entry_highlighted(
    tmp_path: Path,
) -> None:
    """Back from a game: the same game is still highlighted, in the same live list.

    Functional-spec §2.5. The run has four games and the test opens **game-03** —
    deliberately not the first, because a rebuilt list would report index 0 and a
    game-01 case would pass while the highlight had been reset. Then it does what
    the spec's last criterion describes: moves down one and opens the next game,
    proving the two-keypress hop between games with nothing in between (the
    absolute stack depth is what pins "nothing in between").

    The mechanism this defends is a framework assumption, so the assertions are
    deliberately concrete: the DetailScreen we come back to is the *same object*,
    its `ListView` is the *same widget*, its rows are unchanged, and its index is
    still 2.
    """
    ledger = _ledger_with_games(tmp_path, 4, name="highlight.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        assert _panel_labels(screen) == ["game-01", "game-02", "game-03", "game-04"]

        # Highlight game-03 (index 2) with real keystrokes — `_highlight_game`
        # asserts the highlight started at 0 and arrived at 2, so the index below
        # is provably a moved highlight and not the mount default.
        listing = await _highlight_game(pilot, 2)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert _numbered_transcript_marker(3) in _transcript_body_text(app.screen)

        # Back out of the game.
        await pilot.press("escape")
        await pilot.pause()

        # The very same screen and the very same list widget came back...
        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        assert screen.query_one("#transcript-panel-list", ListView) is listing
        # ...with its rows untouched...
        assert _panel_labels(screen) == ["game-01", "game-02", "game-03", "game-04"]
        # ...and game-03 STILL highlighted (2, not the 0 or None a rebuilt list
        # would report).
        assert listing.index == 2
        # The reviewer is still standing in the panel, so the `down` below is the
        # next keystroke they would actually make.
        assert screen.focused is listing

        # Functional-spec §2.5, last criterion: down + enter reaches the NEXT
        # game, with no menu in between (the stack depth is the "no menu" proof).
        await pilot.press("down")
        await pilot.pause()
        assert listing.index == 3
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
        next_body = _transcript_body_text(app.screen)
        assert _numbered_transcript_marker(4) in next_body
        assert _numbered_transcript_marker(3) not in next_body


def test_the_intermediate_transcript_list_screen_is_gone_from_the_module() -> None:
    """`TranscriptListScreen` no longer exists, and no fourth screen replaced it.

    Functional-spec §2.6, asserted structurally: importing the deleted name must
    fail, the module must not carry it under any other guise, and the set of
    screen classes the module defines must be exactly the three legitimate ones.
    That last assertion is what generalises the behavioural walk below to *every*
    key path, including ones no test drives — a screen that does not exist cannot
    be reached from anywhere.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")

    with pytest.raises(ImportError):
        from graphia.ui.ledger_viewer import TranscriptListScreen  # noqa: F401

    assert not hasattr(module, "TranscriptListScreen")

    defined_screens = {
        name
        for name, obj in vars(module).items()
        if isinstance(obj, type)
        and issubclass(obj, Screen)
        and obj.__module__ == module.__name__
    }
    assert defined_screens == {"LedgerTableScreen", "DetailScreen", "TranscriptScreen"}


async def test_no_key_path_reaches_a_bare_transcript_list_screen(
    tmp_path: Path,
) -> None:
    """Pressing every key on the run's screens never lands on a bare games list.

    The behavioural half of functional-spec §2.6 (the structural half is the test
    above, restated as this walk's premise). Every key `DetailScreen` binds is
    pressed — rot-guarded below against `DetailScreen.BINDINGS`, so a future
    binding cannot be added without being swept here — along with the
    movement/activation keys a reviewer would try, in one continuous session so
    the presses compose (the panel focused from an earlier `t` is what makes a
    later `enter` open a game).

    After each press: the screen must be one of the three legitimate ones and must
    not be the forbidden shape. Whenever a press opens a game, backing out of it
    must land on the run's **figures** — the second §2.6 criterion — and whenever
    a press returns to the table, the walk drills back in and carries on.

    The counters at the end keep the sweep honest: a walk that never opened a game
    and never came back out would satisfy every assertion above while testing
    nothing.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    assert not hasattr(module, "TranscriptListScreen")

    # ROT GUARD: every key the screen binds is in the swept set.
    assert {binding.key for binding in DetailScreen.BINDINGS} <= set(
        _DETAIL_KEYS_SWEPT
    )
    assert {binding.key for binding in TranscriptScreen.BINDINGS} <= set(
        _DETAIL_KEYS_SWEPT
    )

    ledger = _ledger_with_games(tmp_path, 3, name="no-list-screen.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        await _open_detail_for_row(pilot, 0)

        games_opened = 0
        returns_to_table = 0

        for key in _DETAIL_KEYS_SWEPT:
            await pilot.press(key)
            await pilot.pause()

            assert app.is_running, key
            # The criterion itself first (so a failure names the shape that was
            # reached), then the broader belt-and-braces: it is one of the three
            # screens the viewer is allowed to present at all.
            assert _is_bare_transcript_list_screen(app.screen) is False, key
            assert isinstance(app.screen, _ALLOWED_SCREEN_TYPES), key

            if isinstance(app.screen, TranscriptScreen):
                games_opened += 1
                # Going back from a game lands on the run's FIGURES, not on a
                # list screen that then has to be left as well.
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, DetailScreen), key
                assert _is_bare_transcript_list_screen(app.screen) is False, key
                assert len(app.screen_stack) == _DETAIL_STACK_DEPTH, key

            if isinstance(app.screen, LedgerTableScreen):
                returns_to_table += 1
                await _open_detail_for_row(pilot, 0)

        # The sweep really did traverse the interesting routes.
        assert games_opened > 0
        assert returns_to_table > 0


async def test_an_out_of_range_selection_pushes_nothing(tmp_path: Path) -> None:
    """A selection whose index has no entry is a no-op, not a crash.

    The defensive guard `DetailScreen.on_list_view_selected` documents: the panel
    of a run with no games keeps a hidden, empty `ListView` that can still take
    focus, so a `Selected` arriving with an index outside the entries list must do
    nothing. Driven by posting the real `ListView.Selected` message to the screen
    (the handler is reached through Textual's normal dispatch), because no
    keystroke can produce this state — which is precisely why the guard needs a
    test of its own.
    """
    ledger = _ledger_with_games(tmp_path, 0, name="out-of-range.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_detail_for_row(pilot, 0)
        listing = screen.query_one("#transcript-panel-list", ListView)
        # PREMISE: there really are no entries to resolve against.
        assert _panel_labels(screen) == []

        screen.post_message(ListView.Selected(listing, ListItem(), 7))
        await pilot.pause()

        assert app.screen is screen
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        assert app.is_running
