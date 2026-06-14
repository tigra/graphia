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

from textual.widgets import DataTable, Footer, Header, Input, Select, Static

from graphia.eval_ledger import (
    METRIC_ORDER,
    SEARCH_SCOPE_ALL,
    build_table_model,
    load_ledger,
)
from graphia.ui.ledger_viewer import (
    DetailScreen,
    LedgerTableScreen,
    LedgerViewerApp,
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
