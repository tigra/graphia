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
from textual.content import Content
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
    KIND_ATTR,
    KIND_ATTR_LAW_ABIDING,
    KIND_ATTR_MAFIA,
    KIND_DIARY,
    KIND_FIELD_LABEL,
    KIND_MARKER,
    KIND_PLAIN,
    KIND_RECAP,
    KIND_SPEAKER,
    KIND_SPEAKER_HUMAN,
    KIND_SPEAKER_LAW_ABIDING,
    KIND_SPEAKER_LAW_ABIDING_HUMAN,
    KIND_SPEAKER_MAFIA,
    KIND_SPEAKER_MAFIA_HUMAN,
    KIND_SPEECH,
    KIND_SPEECH_HUMAN,
    KIND_SPEECH_LAW_ABIDING,
    KIND_SPEECH_LAW_ABIDING_HUMAN,
    KIND_SPEECH_MAFIA,
    KIND_SPEECH_MAFIA_HUMAN,
    KIND_THOUGHT,
    METRIC_ORDER,
    SEARCH_SCOPE_ALL,
    TRANSCRIPT_KINDS,
    build_table_model,
    list_transcripts,
    load_ledger,
    tokenize_transcript,
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


# ===========================================================================
# B14. Spec 037 §2.1 — a list longer than the panel is tall stays reachable.
#
# Added at verification: the criterion "given a run with many games, when the
# list is longer than the panel is tall, then the list can be moved through to
# reach every game" had no test. Slice 1's 30-game case asserted only WIDTH.
#
# It is not hypothetical. The committed ledger holds runs of 49 and 50 games,
# and at the standard 100x30 terminal the panel shows 26 rows — so reaching the
# last game already requires the list to scroll. `ListView` inherits that from
# `VerticalScroll`, which is why it works; this pins it, so a future change to
# row rendering (e.g. the backlog's matchup-titled labels, which could make rows
# taller) cannot break reachability silently.
# ===========================================================================


async def test_a_list_longer_than_the_panel_can_be_walked_to_its_last_game(
    tmp_path: Path,
) -> None:
    """Every game is reachable by `down`, and the last one opens."""
    games = 50
    ledger = _ledger_with_games(tmp_path, games, name="long-run")
    app = LedgerViewerApp(path=ledger)

    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        screen = await _open_detail_for_row(pilot, 0)
        listing = screen.query_one("#transcript-panel-list", ListView)
        panel_height = screen.query_one("#transcript-panel", Vertical).size.height

        # The premise: the list really is taller than the panel. Without this the
        # walk below would pass on a list that fits, proving nothing.
        assert len(listing.children) == games
        assert panel_height < games, (
            f"panel is {panel_height} rows for {games} entries — the list must "
            "overflow for this test to mean anything"
        )
        assert listing.max_scroll_y > 0, "the list must actually need to scroll"

        await pilot.press("right")
        for _ in range(games):
            await pilot.press("down")
        await pilot.pause()

        last = games - 1
        assert listing.index == last, f"stopped at {listing.index}, wanted {last}"
        assert listing.scroll_y > 0, "the list did not scroll to follow the highlight"

        # And the last entry actually opens — reachable means openable.
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH


# ===========================================================================
# B14. Colour-coded transcript reading view (spec 038, Slices 1-2) — the WIDGET
#      half of each slice's test task. `TranscriptScreen.compose` now builds a
#      styled `rich.text.Text` from the pure tokenizer's spans instead of
#      passing the file's string straight through, so four things have to be
#      pinned at the widget: the spans really do survive into the rendered
#      renderable, the rendered PLAIN TEXT is still the file's text exactly (the
#      colour-unavailable fallback of functional-spec §2 — no stray formatting
#      characters), text containing `[bold]` still renders literally, and
#      scrolling plus the escape/backspace/q back-out keys behave exactly as they
#      did before.
#
#      SLICE 2 CHANGED THE EXPECTED RUNS, not the properties. The tokenizer now
#      splits a detail-carrying tag into marker / attr / marker and lifts the
#      cast list's `Label:` off its prose, so a single `<player …>` line arrives
#      as FIVE painted runs and a `Personality: …` line as one. Everything the
#      Slice 1 tests proved still has to hold — it just holds over a longer list
#      of shorter runs, which is why the pins below are stated as the complete
#      ordered run list rather than as a count.
#
#      SLICE 3 GREW THE FIXTURE, and that was a deliberate choice over adding a
#      second one. As written for Slices 1-2, `_HIGHLIGHT_BODY` contained no
#      `<thought>` line and no `<recap>` line, so BOTH of the slice's new inline
#      kinds were unexercised at the widget level entirely — the pure tokenizer
#      pinned them and nothing checked that they survived into the renderable.
#      A separate fixture would have needed its own ledger, its own
#      `_open_the_only_game` round trip and its own absolute run list to prove
#      the same properties this one already proves, so the two lines were added
#      here instead and every absolute pin below was recomputed against them.
#      A `Moderator:` preamble line came with them: it is content the skeleton
#      must recede behind AND the widget-level guard on the speaker rule's
#      exclusion set (`Moderator: …` must not paint as speech), which is what
#      keeps `_HIGHLIGHT_UNSTYLED_SUBSTRINGS` meaningful now that the spoken line
#      it used to name is legitimately painted.
#
#      SLICE 4 GREW IT ONCE MORE, and this time the fixture's CAST LIST is what
#      the growth is for. The tokenizer is no longer per-line: it reads
#      `<setup>` into a `name → side` map first, so the same body tokenizes
#      differently depending on who its cast list names. As Slice 3 left it,
#      `_HIGHLIGHT_BODY`'s only cast entry carried `role="Mafioso"` — a label
#      Graphia never emits — so SIX of the fourteen kinds, the entire headline of
#      spec 038, could not reach the renderable from any test in this file and
#      the six new CSS rules were asserted only as entries in a dict. Two cast
#      entries and two spoken lines were added; the `Mafioso` entry was KEPT, so
#      the fixture now covers a Mafia, a Law-abiding and an unrecognised role at
#      once, and Alice's neutral line is the widget-level statement of "never
#      guess a side". Nothing about Slices 1-3's pins changed except their
#      positions in the list.
# ===========================================================================
#
# WHY THESE LIVE HERE AND NOT IN `tests/test_transcript_highlight.py`. That file
# is the spec's tokenizer file and stays terminal-free — a pure-function suite
# with no Textual import, mirroring `eval_ledger`'s own no-Rich/no-Textual
# property. The `App.run_test()` harness, and every helper a TranscriptScreen
# test needs (`_write_ledger`, `_doc_with_transcript_dir`,
# `_write_run_transcripts`, `_open_detail_for_first_row`, `_highlight_game`,
# `_transcript_body_text`, `_PANEL_TERMINAL_SIZE`, `_TRANSCRIPT_STACK_DEPTH`),
# already live in THIS file — `tasks.md` names it as the project's established
# convention for exactly this. Putting the widget tests here reuses all of it and
# sets the "behaves exactly as before" assertions next to B10's back-out tests
# they regress against, instead of standing up a second parallel harness.
#
# WHAT IS DELIBERATELY NOT ASSERTED: rendered colour. It is theme-dependent
# (`$text-muted` resolves differently on `textual-dark`, `textual-light` and
# `textual-ansi`), spec 037 established that it is a poor test subject, and
# appearance is reviewed by eye. What is asserted is span STRUCTURE — which
# character ranges carry a style at all — because that is what "the markers are
# distinguishable from the content" reduces to once the palette is a CSS concern.
#
# Every fixture is synthetic and written under `tmp_path`, except B14's two
# real-corpus cases, which COPY a committed transcript into `tmp_path` and read
# it there. The committed `evals/transcripts/` is only ever read.

# The run id these tests' ledgers name, and therefore the sibling
# `transcripts/<run-id>/` dir written under `tmp_path`. Distinct from
# `_GAMES_RUN_ID` so a B14 fixture can never be confused with B11/B12's.
_HIGHLIGHT_RUN_ID = "run-highlight"

# A small full-shape game: the metadata header, a cast entry with a persona
# field, the moderator's opening line, an inline `<kill>`, the pre-022 era's
# indented `  <round>` / `  Round 1.`, one line of speech, one private thought,
# one moderator recap, and NO trailing newline (all 298 committed transcripts end
# on `>`). Small enough that the complete expected span list below can be checked
# by eye.
#
# Slice 3 added three lines — the `Moderator:` preamble, the `<thought>` and the
# `<recap>` — so that every kind the tokenizer emits is exercised at the widget
# and not only in `tests/test_transcript_highlight.py`.
#
# SLICE 4 GREW IT AGAIN, for the same reason and with the same trade-off, and
# this time the CAST LIST is the fixture's design rather than scenery: the
# tokenizer now reads `<setup>` into a `name → side` map, so which roles appear
# here decides which kinds the whole body can produce. All three possibilities
# are present at once:
#
#   Alice — `role="Mafioso"`, a role string the writer NEVER emits. Kept exactly
#           as Slices 1-3 wrote it, and now doing a second job: she is absent
#           from the side map, so `Alice:` is the NEUTRAL `speaker`/`speech` and
#           her `<thought player="Alice">` owner name stays achromatic `attr`.
#           Every Slice-3 pin below therefore survives untouched, and the
#           widget-level statement of "never guess a side" comes free.
#   Bo    — `role="Mafia"`, so his line paints `speaker-mafia`/`speech-mafia` and
#           his role text `attr-mafia`.
#   Cleo  — `role="Law-abiding Citizen"`, the other hue.
#
# Without a real Mafia and a real Law-abiding in the cast, six of the fourteen
# kinds — the entire headline of spec 038 — would reach the renderable in no test
# in this file, and the six new CSS rules would be asserted only as map entries.
_HIGHLIGHT_BODY = (
    "Game 1 | provider=ollama | large_model=qwen3-coder:30b | games=2\n"
    "<transcript>\n"
    "<setup>\n"
    '<player name="Alice" role="Mafioso">\n'
    "Personality: brisk and sly\n"
    "</player>\n"
    '<player name="Bo" role="Mafia">(no persona recorded)</player>\n'
    '<player name="Cleo" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    "</setup>\n"
    "<preamble>\n"
    "Moderator: A new game begins. Welcome, Alice.\n"
    "</preamble>\n"
    "<night>\n"
    "<kill>Avery — Law-abiding Citizen</kill>\n"
    "</night>\n"
    "<day>\n"
    "  <round>\n"
    "  Round 1.\n"
    "Alice: I saw nothing last night.\n"
    "Bo: Nor did I.\n"
    "Cleo: Bo is lying.\n"
    '<thought player="Alice">Bo suspects me.</thought>\n'
    "<recap>Alive: Alice, Bo.</recap>\n"
    "  </round>\n"
    # SPEC 039 ADDED THIS LINE, and where it sits is half of what it says: a
    # diary is the DAY'S TRAILER, so `_render_phases` puts it between the last
    # `</round>` and `</day>` — "between the day it was written about and the
    # Night that followed". Bo is the owner because he is the fixture's
    # `role="Mafia"` cast entry, so the tag also carries the side-bearing owner
    # (`attr-mafia`) beside an achromatic Day value, which is spec 039's own
    # split in a single tag.
    '<diary player="Bo" day="1">Cleo folded under pressure.</diary>\n'
    "</day>\n"
    "</transcript>"
)

# THE ABSOLUTE PIN for `_HIGHLIGHT_BODY`: the text of every styled run in the
# rendered body, in order. Hand-written, not derived from the tokenizer, so this
# list is an independent statement of what a reviewer's eye should be drawn to.
#
# It is an OFFSET assertion as well as a text one, because `_rendered_runs` reads
# each entry by SLICING the rendered plain text at the span's own start/end — a
# span whose boundaries drifted by one character would produce a different
# string here. Note what is absent: the persona PROSE, the speech line, and the
# two-space indents, all of which must carry no style at all.
#
# Renamed from `_HIGHLIGHT_EXPECTED_MARKER_TEXTS` in Slice 2: since the tokenizer
# learned `attr` and `field-label`, not every painted run is a marker. The `#`
# comments name each run's kind, which `_rendered_runs` cannot see — it reports
# offsets, deliberately, because rendered COLOUR is theme-dependent and is
# asserted nowhere in this file. Which kind produced which run is pinned in
# `tests/test_transcript_highlight.py`; what is pinned here is that the widget
# paints exactly these character ranges and no others.
_HIGHLIGHT_EXPECTED_STYLED_TEXTS = [
    "Game 1 | provider=ollama | large_model=qwen3-coder:30b | games=2",  # marker
    "<transcript>",  # marker
    "<setup>",  # marker
    # The cast entry splits five ways: the value is lifted out of the
    # punctuation, and the `name=` key, the quotes and the brackets stay marker.
    '<player name="',  # marker
    "Alice",  # attr
    '" role="',  # marker
    # `Mafioso` is a role label nothing in Graphia emits, so it stays ACHROMATIC
    # `attr` while the two real labels below it take a side. That contrast, on
    # three consecutive cast entries, is the widget-level statement of "never
    # guess a side".
    "Mafioso",  # attr
    '">',  # marker
    # The label carries its colon; the description after it does not.
    "Personality:",  # field-label
    "</player>",  # marker
    # Slice 4: the ROLE carries the side colour and the NAME beside it stays
    # achromatic — the ratified narrow reading, painted.
    '<player name="',  # marker
    "Bo",  # attr
    '" role="',  # marker
    "Mafia",  # attr-mafia
    '">',  # marker
    "</player>",  # marker
    '<player name="',  # marker
    "Cleo",  # attr
    '" role="',  # marker
    "Law-abiding Citizen",  # attr-law-abiding
    '">',  # marker
    "</player>",  # marker
    "</setup>",  # marker
    "<preamble>",  # marker
    # NOTHING between the two preamble tags: `Moderator: …` is the writer's own
    # voice, excluded from the speaker rule, so the whole line stays unpainted.
    # Its absence from this list is a Slice 3 assertion in its own right.
    "</preamble>",  # marker
    "<night>",  # marker
    # `<kill>`'s content is marker too, so the whole element is ONE run.
    "<kill>Avery — Law-abiding Citizen</kill>",  # marker
    "</night>",  # marker
    "<day>",  # marker
    # The indent is NOT part of the marker.
    "<round>",  # marker
    "Round 1.",  # marker
    # Slice 3: the spoken line is painted now, in two runs. The colon closes the
    # speaker; the separating space opens the speech.
    #
    # Slice 4 splits these three lines by SIDE, and the three of them together
    # are functional-spec §2's headline requirement at the widget: "Mafia lines
    # and Law-abiding lines are visibly different colours", and "the speaker's
    # name and the words they spoke share that speaker's side colour". Alice's
    # pair stays NEUTRAL — her role label is one the writer never emits, so she
    # has no known side and her line is painted as speech without a hue.
    "Alice:",  # speaker
    " I saw nothing last night.",  # speech
    "Bo:",  # speaker-mafia
    " Nor did I.",  # speech-mafia
    "Cleo:",  # speaker-law-abiding
    " Bo is lying.",  # speech-law-abiding
    # ...and the private thought: tag, owner, tag, BODY, tag. The body is its own
    # run and the tags around it are still markers.
    '<thought player="',  # marker
    # Still achromatic `attr`, and since Slice 4 that is a RESULT rather than a
    # gap: a thought owner's side is a map lookup, and Alice's unrecognised role
    # keeps her out of the map. A known owner's name does take their side — see
    # `test_a_thought_owner_in_the_cast_list_takes_their_side` in
    # `tests/test_transcript_highlight.py`.
    "Alice",  # attr
    '">',  # marker
    # Never side-tinted, whoever the owner is: a private reflection is not an act
    # of allegiance, and the moderator has no side.
    "Bo suspects me.",  # thought
    "</thought>",  # marker
    # ...and the moderator's status recap, the same shape without an attribute.
    "<recap>",  # marker
    "Alive: Alice, Bo.",  # recap
    "</recap>",  # marker
    "</round>",  # marker
    # ...and spec 039's diary, in the Day's trailer AFTER that `</round>`. Seven
    # runs: three head pieces, the owner, the Day, the body and the closing tag.
    # The two attribute values sit side by side in one tag and take DIFFERENT
    # kinds — the owner's name carries Bo's side, the Day carries none — which is
    # the widget-level statement of spec 039's attribute rule.
    '<diary player="',  # marker
    "Bo",  # attr-mafia
    '" day="',  # marker
    "1",  # attr
    '">',  # marker
    # Never side-tinted although its owner is: a private reflection is not an act
    # of allegiance. Its rule is `thought`'s plus `underline` — asserted as a
    # RELATION in `test_the_diary_style_is_the_thoughts_rule_plus_underline`,
    # never as a colour.
    "Cleo folded under pressure.",  # diary
    "</diary>",  # marker
    "</day>",  # marker
    "</transcript>",  # marker
]

# Substrings of `_HIGHLIGHT_BODY` that must carry NO style at all: they are the
# game's content, which the skeleton has to recede behind rather than join.
#
# Slice 2 narrowed the first entry from the whole `Personality: brisk and sly`
# line to its description. The label IS painted now — that is the requirement —
# so the assertion moved to the half that must still not be: the prose, and the
# space in front of it, which belongs to the description rather than the label.
#
# Slice 3 REPLACED the second entry. `Alice: I saw nothing last night.` was the
# unpainted spoken line; the whole point of the slice is that it is painted now,
# and it has no unstyled remainder left at all. The moderator's line takes its
# place and is the stronger case: it is shaped EXACTLY like the spoken line above
# and must still carry no style, so a speaker rule that dropped its exclusion set
# fails here at the widget as well as in the tokenizer's own suite.
_HIGHLIGHT_UNSTYLED_SUBSTRINGS = (
    " brisk and sly",
    "Moderator: A new game begins. Welcome, Alice.",
)

# The pre-022 era's indented lines. The two-space indent is LAYOUT, not skeleton,
# so each marker span must start exactly two characters into the line — the
# property that lets the old indented form tokenize identically to the flush-left
# spec-022 one with no era branch.
_HIGHLIGHT_INDENTED_LINES = ("  <round>", "  Round 1.", "  </round>")

# The keys `TranscriptScreen` binds to back out. Named here so the parametrized
# test below can be rot-guarded against `TranscriptScreen.BINDINGS`.
_TRANSCRIPT_BACK_KEYS = ("escape", "backspace", "q")

# The long-game scroll fixture, pinned as absolute geometry rather than as a
# before/after comparison (spec 037's mutation finding: "X equals Y between two
# states" can hold with both sides broken).
_LONG_GAME_PROSE_LINES = 80
_LONG_GAME_LINES = _LONG_GAME_PROSE_LINES + 2  # the two `<transcript>` tags
_LONG_GAME_TERMINAL_SIZE = (100, 24)
# 24 terminal rows minus the Header and the Footer.
_LONG_GAME_VIEWPORT_ROWS = 22
_LONG_GAME_MAX_SCROLL_Y = _LONG_GAME_LINES - _LONG_GAME_VIEWPORT_ROWS

_LONG_GAME_BODY = (
    "<transcript>\n"
    + "\n".join(f"Alice: line {i}" for i in range(_LONG_GAME_PROSE_LINES))
    + "\n</transcript>"
)

# The three pre-spec-022 run dirs (tech-spec §2 Component B). Named so B14's
# real-corpus cases cover BOTH transcript formats: the old indented
# `Name — Role` cast list and the spec-022 `<player name=… role=…>` one.
_PRE_022_RUN_DIRS = frozenset(
    {"2026-06-19T18-33-37", "2026-06-20T14-17-09", "2026-06-20T18-18-52"}
)

# Resolved from THIS FILE, never the CWD (the idiom
# `tests/test_transcript_highlight.py` and `tests/test_lambda_zip_contents.py`
# both use). READ-ONLY: the corpus is a curated, committed artifact.
_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "evals" / "transcripts"


def _one_real_transcript_per_era() -> dict[str, Path]:
    """One committed transcript from each of the two formats, or ``{}``.

    Deterministic (the first file of the first run dir of each era, in sorted
    order) so a failure is reproducible, and discovered rather than hard-coded so
    it survives the corpus growing with every measured eval run.
    """
    if not _CORPUS_ROOT.is_dir():
        return {}
    chosen: dict[str, Path] = {}
    for path in sorted(p for p in _CORPUS_ROOT.rglob("*.txt") if p.is_file()):
        era = "pre-022" if path.parent.name in _PRE_022_RUN_DIRS else "spec-022"
        chosen.setdefault(era, path)
    return chosen


_REAL_TRANSCRIPT_PARAMS = [
    pytest.param(path, id=era)
    for era, path in sorted(_one_real_transcript_per_era().items())
] or [
    pytest.param(
        None,
        marks=pytest.mark.skip(
            reason=f"no committed transcripts under {_CORPUS_ROOT}"
        ),
        id="no-corpus",
    )
]


def _ledger_with_body(tmp_path: Path, body: str, *, name: str) -> tuple[Path, Path]:
    """A one-run, one-game ledger whose only transcript is exactly ``body``.

    Returns ``(ledger path, game file path)`` — the second so a test can compare
    the rendered body against the bytes on disk rather than against the literal
    it passed in. Entirely under ``tmp_path``.
    """
    ledger = _write_ledger(
        tmp_path,
        _doc_with_transcript_dir("2026-06-22", _HIGHLIGHT_RUN_ID),
        name=name,
    )
    run_dir = _write_run_transcripts(ledger, _HIGHLIGHT_RUN_ID, {"game-01.txt": body})
    return ledger, run_dir / "game-01.txt"


async def _open_the_only_game(pilot) -> TranscriptScreen:
    """Table → the run's DetailScreen → its single game, by real keystrokes only.

    Reuses B10/B11's helpers so the route is the reviewer's actual one, and pins
    the absolute stack depth so a test cannot pass against a screen that was
    never pushed.
    """
    await _open_detail_for_first_row(pilot)
    await _highlight_game(pilot, 0)
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(pilot.app.screen, TranscriptScreen)
    assert len(pilot.app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
    return pilot.app.screen


def _transcript_body_content(screen: TranscriptScreen) -> Content:
    """The body's rendered renderable — the SPANS, not just the text.

    ``_transcript_body_text`` (B10) returns only ``.plain``, which is what the
    pre-038 tests needed. Spec 038 needs the span structure too.

    Asserts the renderable's type, which pins a measured fact about the installed
    Textual (8.2.4): a Rich ``Text`` handed to ``Static`` comes back out of
    ``render()`` as Textual's own ``Content``, via ``Content.from_rich_text``,
    with the spans preserved. If a future Textual stopped converting, the
    ``.spans`` reads below would be measuring something else.
    """
    rendered = screen.query_one("#transcript-body", Static).render()
    assert isinstance(rendered, Content), (
        f"body rendered a {type(rendered).__name__}, expected textual Content"
    )
    return rendered


def _rendered_runs(content: Content) -> list[tuple[int, int, str]]:
    """Every styled run as ``(start, end, the text those offsets cover)``.

    The text is read by slicing ``content.plain`` at the span's own boundaries,
    which is what makes an assertion on the returned strings an assertion about
    the offsets.
    """
    return [
        (span.start, span.end, content.plain[span.start : span.end])
        for span in content.spans
    ]


def _tokenizer_styled_runs(text: str) -> list[tuple[int, int, str]]:
    """The styled runs the PURE tokenizer says ``text`` has, as offsets.

    The independent cross-check for `_rendered_runs`: the widget must paint
    exactly the runs the pure layer marked, no more and no fewer.

    Every kind except `plain` counts as styled, so this tracks the tokenizer's
    vocabulary automatically — Slice 2's `attr` and `field-label` runs are
    included without an edit here, and Slice 3's will be too.
    """
    runs: list[tuple[int, int, str]] = []
    offset = 0
    for span_text, kind in tokenize_transcript(text):
        if kind != KIND_PLAIN:
            runs.append((offset, offset + len(span_text), span_text))
        offset += len(span_text)
    return runs


# ---------------------------------------------------------------------------
# B14.1 Markup parsing stays off
# ---------------------------------------------------------------------------


async def test_the_transcript_body_keeps_markup_parsing_off(tmp_path: Path) -> None:
    """`markup=False` survives the change to a styled renderable.

    Not made redundant by passing a renderable: the flag is the guarantee for the
    `str` path a future refactor might take, and console markup is `[…]` — one `[`
    in model-generated persona prose would, with markup on, either swallow the
    text as a style tag or raise on an unclosed one.

    On the installed Textual (8.2.4) `Static` exposes no public `markup`
    attribute — the constructor kwarg lands on `Widget` as `_render_markup` — so
    that is what is read, and the absence of the public name is pinned too so a
    future Textual that adds one is noticed rather than silently making this
    assertion read a stale field.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="hl-markup.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        widget = screen.query_one("#transcript-body", Static)

        assert widget._render_markup is False
        with pytest.raises(AttributeError):
            getattr(widget, "markup")


# ---------------------------------------------------------------------------
# B14.2 The spans survive into the rendered body
# ---------------------------------------------------------------------------


async def test_the_rendered_body_carries_a_span_per_styled_run_at_exact_offsets(
    tmp_path: Path,
) -> None:
    """Each styled run arrives as a span over exactly its own characters.

    THE central structural assertion of the reading view: "the skeleton is
    visually distinct from its content", and "markers that carry details show
    those details distinguishably" (functional-spec §2), both reduce — once
    colour is a CSS concern — to *which character ranges carry a style at all*.

    Asserted five ways, because each catches something the others do not:

    * against `_HIGHLIGHT_EXPECTED_STYLED_TEXTS`, a hand-written absolute list —
      so both the widget and the tokenizer breaking together is still a failure;
    * against the pure tokenizer's own styled offsets — so the widget cannot
      paint a run the pure layer never marked, nor drop one it did;
    * against the content that must stay unstyled — so "everything is a marker"
      cannot pass;
    * against the indentation, which is layout and never skeleton;
    * and, new in Slice 2, against a run **boundary** inside a single line: the
      cast tag's `Alice` and `Mafioso` must be their own runs, not merged into
      the tag around them and not extended over the quotes that hold them;
    * and, new in Slice 3, against the two INLINE BODIES — a thought's and a
      recap's — each of which must be a run of exactly its own characters with a
      marker run on either side of it. That is the widget-level statement of
      "a thought's content is `thought` and its surrounding tag stays `marker`",
      and until this slice extended `_HIGHLIGHT_BODY` neither kind reached the
      widget at all.

    Renamed from `…a_span_per_marker…`: since Slice 2 not every painted run is a
    marker.
    """
    ledger, game_file = _ledger_with_body(
        tmp_path, _HIGHLIGHT_BODY, name="hl-spans.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        content = _transcript_body_content(screen)
        runs = _rendered_runs(content)

        # (a) The absolute pin: exactly these runs, in this order.
        assert [text for _, _, text in runs] == _HIGHLIGHT_EXPECTED_STYLED_TEXTS

        # (b) ...and they are the pure layer's runs, offsets included.
        assert runs == _tokenizer_styled_runs(
            game_file.read_text(encoding="utf-8")
        )

        # (c) ...and the content between them carries no style whatsoever. A
        # single "everything is one marker span" run would satisfy (a)-style
        # membership checks but not this.
        covered = {
            offset for start, end, _ in runs for offset in range(start, end)
        }
        for substring in _HIGHLIGHT_UNSTYLED_SUBSTRINGS:
            start = content.plain.index(substring)
            overlap = covered & set(range(start, start + len(substring)))
            assert not overlap, (
                f"{substring!r} is styled at offsets {sorted(overlap)} — content "
                "must not be painted as skeleton"
            )

        # (d) ...and a marker's span starts AFTER the line's indentation, never
        # at it. Both halves are asserted: the two indent columns are unstyled
        # and the character right after them is styled — "nothing is styled"
        # would otherwise satisfy the first half alone.
        for line in _HIGHLIGHT_INDENTED_LINES:
            start = content.plain.index(line)
            assert not covered & {start, start + 1}, (
                f"the indent before {line.strip()!r} is styled"
            )
            assert start + 2 in covered, (
                f"{line.strip()!r} itself is not styled"
            )

        # (e) ...and the two attribute values really are painted as runs of their
        # own, ending where the value ends. Read out of the rendered offsets, so
        # a widget that merged the tag back into one run — or stretched a value
        # over its closing quote — fails here with the offending run named.
        tag_start = content.plain.index('<player name="Alice"')
        by_start = {start: (end, text) for start, end, text in runs}
        for value in ("Alice", "Mafioso"):
            start = content.plain.index(f'"{value}"', tag_start) + 1
            assert start in by_start, (
                f"{value!r} at offset {start} is not the start of a painted run; "
                f"runs near it: {[r for r in runs if abs(r[0] - start) < 40]}"
            )
            assert by_start[start] == (start + len(value), value)

        # (f) ...and each inline BODY is a run of exactly its own characters,
        # with a painted marker run ending immediately before it and another
        # beginning immediately after. Read out of the rendered offsets, so a
        # renderable that let a body's style run over its closing tag — or that
        # never painted the body at all — fails here naming the element.
        by_end = {end: (start, text) for start, end, text in runs}
        for opening, body, closing in (
            ('<thought player="Alice">', "Bo suspects me.", "</thought>"),
            ("<recap>", "Alive: Alice, Bo.", "</recap>"),
            # Spec 039's third inline body, and the one whose opening tag ends in
            # an `attr` run rather than a bare `>` — so `start in by_end` is
            # checking that the tag's LAST piece stops at the body, not that some
            # single-span tag does.
            (
                '<diary player="Bo" day="1">',
                "Cleo folded under pressure.",
                "</diary>",
            ),
        ):
            element = content.plain.index(opening + body + closing)
            start = element + len(opening)
            end = start + len(body)
            assert by_start.get(start) == (end, body), (
                f"the body of {opening} is not a run of its own; runs near it: "
                f"{[r for r in runs if abs(r[0] - start) < 40]}"
            )
            # The tags on both sides are painted too, and stop at the body.
            assert start in by_end, f"{opening} does not end where its body starts"
            assert by_start.get(end) == (end + len(closing), closing)


async def test_no_rendered_span_covers_a_newline(tmp_path: Path) -> None:
    """A marker's style can never bleed to the end of a terminal row.

    The tokenizer emits line separators as spans of their own; this is that
    guarantee re-checked where it actually matters — on the renderable the
    terminal paints. The premise (the count) is pinned absolutely, so a body that
    rendered with no spans at all could not pass by having no newline to carry.

    Slice 2 made this matter more, not less: the count moved from 15 runs to 20
    because a line is now split *within* itself, and a splitter that mis-set one
    boundary is exactly the bug that would let a run swallow the newline at the
    end of its line. Slice 3 moved it again, to 32 — the spoken line is two runs
    now, the thought is five and the recap three, and the two elements are the
    LAST thing on their line, which is precisely where a body whose extent was
    computed from the wrong end would take the separator with it.

    Slice 4 moved it to 48: two more cast entries (six painted runs each, since
    `(no persona recorded)` is content) and two more spoken lines. No boundary
    moved — the slice re-kinds spans and never re-splits one — so the growth was
    entirely the longer fixture, and a count that came out at anything other than
    48 would have meant the side split had touched a boundary after all.

    Spec 039 moves it to 55, and the arithmetic is the same argument one more
    time: seven runs, all from the single `<diary player="Bo" day="1">…</diary>`
    line in the Day's trailer, and not one existing boundary touched. The diary
    is the LAST thing on its line — the same position that made Slice 3's thought
    and recap the sharp cases here — so a body whose extent were computed from
    the wrong end would take the separator with it and be caught by the
    `offenders` check below rather than by the count.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="hl-newline.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        runs = _rendered_runs(_transcript_body_content(screen))

        assert len(runs) == len(_HIGHLIGHT_EXPECTED_STYLED_TEXTS) == 55
        offenders = [text for _, _, text in runs if "\n" in text]
        assert not offenders, f"styled runs carrying a newline: {offenders}"


async def test_a_kind_the_style_map_has_never_heard_of_renders_unstyled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kind absent from the style map renders as plain text and never raises.

    This is the forward-compatibility contract the ratified Slice 1 note names,
    and it is why `plain` deliberately has NO entry in
    `_TRANSCRIPT_KIND_COMPONENTS`: its absence *is* the fallback working. Without
    it, a later slice that taught the tokenizer a kind before teaching the map
    would take the whole reading view down rather than render one run undecorated.

    Driven by patching the tokenizer at the UI's own call site (the module-level
    name `TranscriptScreen.compose` resolves), because no real transcript can
    produce a kind this slice does not know.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")

    # `plain` must not be in the map — the fallback is the mechanism, not a gap.
    assert KIND_PLAIN not in module._TRANSCRIPT_KIND_COMPONENTS
    assert KIND_MARKER in module._TRANSCRIPT_KIND_COMPONENTS

    def _future_kinds(text: str) -> list[tuple[str, str]]:
        return [("hello ", "a-kind-from-a-later-slice"), ("<day>", KIND_MARKER)]

    monkeypatch.setattr(module, "tokenize_transcript", _future_kinds)

    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="hl-future.yaml")
    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        content = _transcript_body_content(screen)

        # Every character is still there...
        assert content.plain == "hello <day>"
        # ...and only the kind the map knows is painted.
        assert _rendered_runs(content) == [(6, 11, "<day>")]


def test_every_declared_kind_but_plain_has_a_style_and_a_component_class() -> None:
    """The style map covers the tokenizer's vocabulary, and `plain` alone is out.

    The test above pins the *fallback* — a kind the map has never heard of
    degrades to unstyled rather than raising. That fallback is a safety net, not
    a plan: a kind the tokenizer emits and the map forgets is invisible in the
    viewer and silent in the suite, which is precisely how a slice ships half
    done. This is the other half, and it is why it belongs beside it.

    Slice 2 is the first slice with two kinds to forget, so the guard is written
    now rather than after the first miss. It fails deliberately in the middle of
    a later slice — between that slice's tokenizer task and its styling task —
    which is the documented shape of a slice, not a defect (`tasks.md`, "A slice
    goes red in the middle, and that is correct").

    `COMPONENT_CLASSES` is checked too because a class named in the map but not
    declared on the screen resolves to nothing: `get_component_rich_style` would
    hand back a default style and the run would render undecorated while every
    map-level assertion still passed.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    mapping = module._TRANSCRIPT_KIND_COMPONENTS

    assert set(mapping) == set(TRANSCRIPT_KINDS) - {KIND_PLAIN}, (
        "every kind the tokenizer declares needs a style entry, except `plain`, "
        "whose absence IS the unstyled fallback"
    )
    # Spelled out for the kinds Slices 2-4 added, so the failure names them.
    assert KIND_ATTR in mapping
    assert KIND_FIELD_LABEL in mapping
    assert KIND_SPEAKER in mapping
    assert KIND_SPEECH in mapping
    assert KIND_THOUGHT in mapping
    assert KIND_RECAP in mapping
    # Slice 4's six — two hues, but `speaker`, `speech` and `attr` each split two
    # ways, so SIX component classes and six rules rather than two. The neutral
    # three above are untouched by that: Slice 4 adds, it never edits, and they
    # survive as the appearance of a line whose side is unknown.
    assert KIND_SPEAKER_MAFIA in mapping
    assert KIND_SPEAKER_LAW_ABIDING in mapping
    assert KIND_SPEECH_MAFIA in mapping
    assert KIND_SPEECH_LAW_ABIDING in mapping
    assert KIND_ATTR_MAFIA in mapping
    assert KIND_ATTR_LAW_ABIDING in mapping

    assert set(mapping.values()) <= set(TranscriptScreen.COMPONENT_CLASSES)
    # ...and every class is distinct, so two kinds cannot share one CSS rule by
    # accident and become indistinguishable on screen.
    assert len(set(mapping.values())) == len(mapping)
    # Slice 5's six — the reviewer's own seat, one per dialogue kind. `attr`
    # gains none, deliberately: its two side forms already carry bold, so bold
    # within the side would be invisible there.
    assert KIND_SPEAKER_HUMAN in mapping
    assert KIND_SPEECH_HUMAN in mapping
    assert KIND_SPEAKER_MAFIA_HUMAN in mapping
    assert KIND_SPEECH_MAFIA_HUMAN in mapping
    assert KIND_SPEAKER_LAW_ABIDING_HUMAN in mapping
    assert KIND_SPEECH_LAW_ABIDING_HUMAN in mapping


# The six (side-mate kind, seat kind) pairs Slice 5 added, as the RELATION each
# pair's two CSS rules must hold: same colour, and bold on exactly one of them.
# Written as pairs rather than as six expected hues because colour is
# theme-dependent and a poor test subject (spec 037's finding, restated in
# `tasks.md`) — the relation is not.
_SEAT_STYLE_TWINS = (
    (KIND_SPEAKER, KIND_SPEAKER_HUMAN),
    (KIND_SPEECH, KIND_SPEECH_HUMAN),
    (KIND_SPEAKER_MAFIA, KIND_SPEAKER_MAFIA_HUMAN),
    (KIND_SPEECH_MAFIA, KIND_SPEECH_MAFIA_HUMAN),
    (KIND_SPEAKER_LAW_ABIDING, KIND_SPEAKER_LAW_ABIDING_HUMAN),
    (KIND_SPEECH_LAW_ABIDING, KIND_SPEECH_LAW_ABIDING_HUMAN),
)


async def test_each_seat_style_is_its_side_mates_colour_plus_bold(
    tmp_path: Path,
) -> None:
    """functional-spec §2's headline contrast, asserted as a relation between rules.

    "The seat the person plays is shown in its side's colour, but **bold**, so it
    stands out from the other players on the same side" — and, in the acceptance
    criteria, "other Law-abiding players' lines are the same colour but **not
    bold**". Both halves are one claim about a PAIR of styles, and neither half
    means anything alone: bold-on-the-seat is satisfied by a stylesheet that
    bolds every player's line, and same-colour is satisfied by a stylesheet that
    bolds nothing.

    Asserted through `_kind_styles`, which is where the CSS actually becomes the
    style a span carries, and asserted as an EQUALITY between two resolved
    colours rather than against a literal hue: the palette is theme-dependent (a
    figure pinned here would be wrong under `textual-light` and meaningless under
    `textual-ansi`), while "these two rules resolve to the same colour" holds
    under every theme. That is the shape `tasks.md` ratifies for this test in as
    many words.

    The last assertion is the non-vacuity guard the pairs cannot supply: the
    three side groups must resolve to three DIFFERENT colours, or "the seat is in
    its side's colour" would hold trivially on a stylesheet where every kind is
    the body colour.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="hl-seat-style.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        styles = screen._kind_styles()

        for side_mate, seat in _SEAT_STYLE_TWINS:
            assert styles[seat].color == styles[side_mate].color, (
                f"{seat} is not its side-mate {side_mate}'s colour — the seat must "
                "be marked WITHIN its side, not as a third side"
            )
            assert styles[seat].color is not None, (
                f"{seat} resolved to no colour at all, so the equality above is "
                "vacuous"
            )
            assert styles[seat].bold is True, f"{seat} is not bold"
            assert styles[side_mate].bold is not True, (
                f"{side_mate} is bold too, so bold no longer distinguishes the "
                "reviewer's seat from the rest of its side — functional-spec §2 "
                "requires same colour, NOT bold, for a side-mate"
            )

        # ...and the three side groups really are three different colours, so
        # "the seat's own side colour" is saying something.
        assert (
            len(
                {
                    styles[KIND_SPEAKER].color,
                    styles[KIND_SPEAKER_MAFIA].color,
                    styles[KIND_SPEAKER_LAW_ABIDING].color,
                }
            )
            == 3
        )


async def test_the_diary_style_is_the_thoughts_rule_plus_underline(
    tmp_path: Path,
) -> None:
    """Spec 039's rule, asserted as a RELATION to the rule it is derived from.

    Tech-spec 039 §2.8 does not describe the diary's appearance in colours; it
    describes it as an operation on an existing rule — "the body style is
    `thought`'s rule plus `underline`", the same shape Slice 5 used for the
    reviewer's seat ("its side's colour plus bold, and nothing else"). So that is
    what is asserted, and asserting anything else would be worse: rendered colour
    is theme-dependent (`$text-muted` resolves differently on `textual-dark`,
    `textual-light` and `textual-ansi`), spec 037 established it is a poor test
    subject, and a hue pinned here would be wrong under one theme and meaningless
    under another. The RELATION holds under all three.

    Read through `_kind_styles`, which is where the CSS actually becomes the
    style a span carries — so a rule that parsed but resolved to nothing (Textual
    silently drops `overline`, the axis this design measured dead and rejected
    for exactly that reason) fails here rather than looking right in the
    stylesheet and painting nothing.

    Four claims, and the third is the one the whole design turns on:

    * **same colour as a thought**, and not None — a diary is a thought's
      sibling, both private and neither an act of allegiance, so the family trait
      is inherited rather than merely asserted;
    * **both are italic** — the second half of that inheritance, and what makes a
      diary read as a thought's sibling at all;
    * **the diary is underlined and the thought is NOT.** This is the entire
      differentia. The hard problem is not telling a diary from body text; it is
      telling it from a `thought`, the one kind it shares a colour and a register
      with. Under `textual-ansi` both colours collapse to the terminal default
      and this single SGR flag is *all* that separates them;
    * **exactly one axis**, so `bold` (Slice 5's, on a player's line) and
      `reverse` (`recap`'s scroll landmark) are both absent. Without this the
      third claim would pass on a stylesheet that piled on every flag it could
      and re-collided the diary with two other kinds.

    The last assertion is the non-vacuity guard the pairing cannot supply: a
    thought's colour must differ from a side's, or "the diary is a thought's
    colour" would hold trivially on a stylesheet where every kind is the body
    colour.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    # The class name as a WRITTEN-OUT LITERAL, because the mapping's own guard
    # (`set(mapping.values()) <= set(TranscriptScreen.COMPONENT_CLASSES)`) reads
    # two production tables against each other and would agree with itself
    # through a rename of both. The resolved-style assertions below are the real
    # protection — a class the stylesheet does not name resolves to a default
    # style and fails them — and this line is what makes the failure say *which*
    # of the three spellings drifted.
    assert module._TRANSCRIPT_KIND_COMPONENTS[KIND_DIARY] == "transcript--diary"

    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="hl-diary-style.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        styles = screen._kind_styles()

        diary, thought = styles[KIND_DIARY], styles[KIND_THOUGHT]

        assert diary.color == thought.color, (
            "a diary is a thought's sibling and must inherit its colour — a "
            "private reflection is not an act of allegiance, whichever kind it is"
        )
        assert diary.color is not None, (
            "the diary rule resolved to no colour at all, so the equality above "
            "is vacuous"
        )
        assert diary.italic is True and thought.italic is True, (
            "italic is the inherited half; dropping it from either would stop a "
            "diary reading as a thought's sibling"
        )
        assert diary.underline is True, "the diary is not underlined"
        assert thought.underline is not True, (
            "a thought is underlined too, so underline no longer separates the "
            "day trailer from the round bodies above it — which is the one thing "
            "a diary being its own kind exists to say"
        )
        # Exactly ONE axis: the two flags already spoken for stay unspoken here.
        assert diary.bold is not True, (
            "bold is the reviewer's own seat (Slice 5); a diary body wearing it "
            "puts a player's private note in the seat's register"
        )
        assert diary.reverse is not True, (
            "reverse is `recap`'s scroll landmark; a second inverted block files "
            "the moderator's posted fact and a player's private note as one look"
        )

        # ...and a thought's colour really is not a side's, so "the diary is a
        # thought's colour" is saying something.
        assert thought.color != styles[KIND_SPEECH_MAFIA].color
        assert thought.color != styles[KIND_SPEECH_LAW_ABIDING].color


# ---------------------------------------------------------------------------
# B14.3 The rendered plain text is the file's text, exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "body"),
    [
        pytest.param("full-shape", _HIGHLIGHT_BODY, id="full-shape"),
        pytest.param("one-line", "<transcript></transcript>", id="one-line"),
        pytest.param(
            "unicode-and-em-dashes",
            "<night>\n<kill>Avery — Law-abiding Citizen</kill>\n"
            "Alice: café, naïve, «quoted»\n</night>",
            id="unicode",
        ),
        pytest.param(
            "blank-lines-and-trailing-newline",
            "<preamble>\n\nModerator: A new game begins. Welcome, Bo.\n\n</preamble>\n",
            id="blank-lines",
        ),
        pytest.param("empty-file", "", id="empty-file"),
    ],
)
async def test_the_rendered_body_plain_text_equals_the_files_text_exactly(
    tmp_path: Path, case: str, body: str
) -> None:
    """The colour-unavailable fallback of functional-spec §2, asserted at the widget.

    "Where colour cannot be shown the full text is readable as plain text with no
    leftover formatting characters" — so the renderable's plain text must be the
    file's text, character for character. The tokenizer's own round-trip test
    cannot see this: it compares its output to its input, and everything the UI
    does afterwards (building a `rich.text.Text`, `Static`'s conversion to
    `Content`) happens downstream of it.

    Compared against BOTH the bytes on disk and the literal this test wrote, so a
    fixture-writing bug cannot make the comparison agree while being wrong, and
    the length is pinned separately so a difference is reported as a count rather
    than as two walls of text.
    """
    ledger, game_file = _ledger_with_body(tmp_path, body, name=f"hl-plain-{case}.yaml")
    on_disk = game_file.read_text(encoding="utf-8")
    assert on_disk == body, "fixture sanity: the file holds what the test wrote"

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        content = _transcript_body_content(screen)

        assert len(content.plain) == len(on_disk)
        assert content.plain == on_disk
        # And through B10's own reader, so the two paths agree.
        assert _transcript_body_text(screen) == on_disk


@pytest.mark.parametrize("source", _REAL_TRANSCRIPT_PARAMS)
async def test_a_real_committed_game_renders_as_its_exact_text(
    tmp_path: Path, source: Path
) -> None:
    """One real game per transcript format, rendered character for character.

    The synthetic cases above are written by the same hand that writes the
    expectations. This one takes a **committed** game — thousands of characters
    of model-generated prose, em-dashes, quotation marks and whatever else 15 eval
    runs produced — copies it into `tmp_path`, and asserts the widget shows all of
    it and marks exactly the runs the pure tokenizer marked.

    Both eras are covered: the pre-022 indented `Name — Role` cast list and the
    spec-022 `<player name=… role=…>` form. The committed corpus is only READ.
    """
    text = source.read_text(encoding="utf-8")
    # The premise: a game big enough for this to mean something.
    assert len(text) > 5000, f"{source} is too small to be a real game"
    assert text.count("\n") > 50

    ledger, game_file = _ledger_with_body(tmp_path, text, name="hl-real.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        content = _transcript_body_content(screen)

        assert len(content.plain) == len(text)
        assert content.plain == text

        runs = _rendered_runs(content)
        # The premise again: a real game has plenty of skeleton, so "no spans at
        # all" cannot pass the comparison below by both sides being empty.
        assert len(runs) > 20, f"only {len(runs)} styled runs in a real game"
        assert runs == _tokenizer_styled_runs(game_file.read_text(encoding="utf-8"))


async def test_text_containing_square_brackets_renders_literally(
    tmp_path: Path,
) -> None:
    """`[bold]` in a game's prose is shown, not parsed as console markup.

    The corpus contains no `[` today, but every word in a transcript is
    model-generated and persona prose is one eval run away from carrying one. With
    markup parsing on, `[bold]` would vanish into a style tag and `[unclosed`
    would raise — either way the reviewer would be reading something other than
    the preserved game.

    Both halves are asserted: the brackets survive in the text, AND no styled run
    covers them (markup parsing that consumed them would leave a span where the
    tag used to be).

    The bracketed prose sits behind a `Personality:` / `Manner:` field label on
    purpose since Slice 2: the label is now lifted off the line, so the run list
    below says in one place that the label IS painted and that the bracketed
    description after it is NOT. A markup parser that ate `[bold]` would shift
    every offset after it and change this list.
    """
    body = (
        "<setup>\n"
        '<player name="Alice" role="Mafioso">\n'
        "Personality: says [bold] a lot, and [/bold], and [not a real tag]\n"
        "Manner: writes [unclosed brackets\n"
        "</player>\n"
        "</setup>"
    )
    ledger, game_file = _ledger_with_body(tmp_path, body, name="hl-brackets.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        content = _transcript_body_content(screen)

        assert content.plain == game_file.read_text(encoding="utf-8")
        for literal in ("[bold]", "[/bold]", "[not a real tag]", "[unclosed"):
            assert literal in content.plain

        runs = _rendered_runs(content)
        assert [text for _, _, text in runs] == [
            "<setup>",  # marker
            '<player name="',  # marker
            "Alice",  # attr
            '" role="',  # marker
            "Mafioso",  # attr
            '">',  # marker
            "Personality:",  # field-label
            "Manner:",  # field-label
            "</player>",  # marker
            "</setup>",  # marker
        ]
        # ...and not one of the four bracket literals is inside a painted run.
        covered = {
            offset for start, end, _ in runs for offset in range(start, end)
        }
        for literal in ("[bold]", "[/bold]", "[not a real tag]", "[unclosed"):
            start = content.plain.index(literal)
            assert not covered & set(range(start, start + len(literal))), (
                f"{literal!r} is painted — bracketed prose is content, not a label"
            )


async def test_the_rich_text_path_drops_the_five_control_codes(
    tmp_path: Path,
) -> None:
    """TRIPWIRE, not an endorsement: BEL/BS/VT/FF/CR are lost on the way to screen.

    `TranscriptScreen._styled_body` builds a `rich.text.Text`, and `Text.append`
    runs Rich's `strip_control_codes`, which silently removes 0x07, 0x08, 0x0b,
    0x0c and 0x0d. A transcript containing one would therefore render one
    character short of its file — precisely the guarantee the view exists to keep
    — and the tokenizer's own round-trip test cannot see it, because the loss
    happens downstream of the tokenizer.

    This test pins the CURRENT, MEASURED behaviour so the boundary is visible:

    * it does not fire on any real game — `tests/test_transcript_highlight.py`'s
      `test_no_committed_transcript_contains_a_rich_stripped_control_code` is the
      guard that says so, over all 298 committed files;
    * **when this test fails, read why before touching it.** A change that made
      the body lossless closes the hole, and this test should then be inverted to
      assert equality. A change that made MORE characters disappear means the
      renderable got worse.

    MEASURED CORRECTION to tech-spec §2 Component C (and to the docstring on
    `TranscriptScreen._styled_body`, which repeats it): switching from Rich's
    `Text` to Textual's native `Content` does **not**, on its own, close this
    hole. `textual.content` carries its own `_STRIP_CONTROL_CODES = [7, 8, 11,
    12, 13]` — the identical five — and `Content.__init__` applies it by default.
    Verified on Textual 8.2.4: `Content("a\x07b").plain == "ab"`. Closing the
    hole takes an explicit `strip_control_codes=False`, whichever renderable is
    used; a `Content` refactor that omits it changes nothing here. (Confirmed by
    mutation: a `Content(..., strip_control_codes=False)` body is the one change
    that makes this test fail.)

    Note the second, separate mechanism this makes visible: `read_transcript` uses
    `Path.read_text`, whose universal-newline translation turns a lone CR into a
    LF before the tokenizer ever sees it. So CR is not "dropped" here — it is
    already an LF — and the comparison below is against the text the reader
    returns, not the file's raw bytes.
    """
    raw = "<transcript>\nAlice: bell\x07 back\x08 vt\x0b ff\x0c end\n</transcript>"
    ledger, game_file = _ledger_with_body(tmp_path, raw, name="hl-control.yaml")
    as_read = game_file.read_text(encoding="utf-8")

    # The pure layer keeps every one of them — which is exactly why only a
    # widget-level assertion can see the loss.
    assert "".join(t for t, _ in tokenize_transcript(as_read)) == as_read

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        plain = _transcript_body_content(screen).plain

        stripped = "\x07\x08\x0b\x0c"
        assert plain != as_read, (
            "the control codes now survive to the widget — if the `Content` "
            "refactor landed, invert this test to assert equality"
        )
        assert len(plain) == len(as_read) - len(stripped)
        for char in stripped:
            assert char in as_read
            assert char not in plain
        # Everything else is intact: only those characters went missing.
        assert plain == as_read.translate({ord(char): None for char in stripped})
        # `\n` and `\t` are NOT in Rich's strip list, so the game's line
        # structure is untouched.
        assert plain.count("\n") == as_read.count("\n")


# ---------------------------------------------------------------------------
# B14.4 Scrolling and the back-out keys behave exactly as before
# ---------------------------------------------------------------------------


async def test_scrolling_a_long_game_behaves_as_before(tmp_path: Path) -> None:
    """A styled renderable scrolls exactly like the plain string it replaced.

    The risk spec 038 introduces here is geometric, not visual: `compose` now
    yields a `rich.text.Text` where it used to yield a `str`, and a renderable
    that wrapped differently, or reported a different height, would change how far
    a game scrolls and how much of it is reachable.

    Pinned as ABSOLUTE geometry — a 202-line game in a 22-row viewport is 180 rows
    of scroll — plus the offset each key produces. `virtual_size.height` equalling
    the game's line count is the "no unexpected wrapping" claim: one wrapped line
    would make it 203.

    Then the interaction that matters most: the scroll container holds focus here,
    so `escape` has to reach the screen's binding past it.
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="hl-scroll.yaml")
    assert _LONG_GAME_BODY.count("\n") + 1 == _LONG_GAME_LINES

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        # The premise: the game really does overflow, and the keys will land on
        # the scroll container.
        assert screen.focused is scroller
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.virtual_size.height == _LONG_GAME_LINES
        assert scroller.max_scroll_y == _LONG_GAME_MAX_SCROLL_Y
        assert scroller.scroll_offset.y == 0

        await pilot.press("down")
        await pilot.pause()
        assert scroller.scroll_offset.y == 1

        await pilot.press("pagedown")
        await pilot.pause()
        assert scroller.scroll_offset.y == 1 + _LONG_GAME_VIEWPORT_ROWS

        await pilot.press("end")
        await pilot.pause()
        assert scroller.scroll_offset.y == _LONG_GAME_MAX_SCROLL_Y

        await pilot.press("home")
        await pilot.pause()
        assert scroller.scroll_offset.y == 0

        # The last line of the game really is reachable, and it is the game's.
        await pilot.press("end")
        await pilot.pause()
        assert _transcript_body_text(screen).endswith("</transcript>")

        # ...and backing out still works from a scrolled position, with the
        # scroll container focused.
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


@pytest.mark.parametrize("key", _TRANSCRIPT_BACK_KEYS)
async def test_each_back_out_key_pops_the_transcript_screen(
    tmp_path: Path, key: str
) -> None:
    """`escape`, `backspace` and `q` all step back out to the run's figures.

    B10 covers `escape` as part of the full back-out path; this sweeps all three
    keys the screen binds, from the transcript itself, at an absolute stack depth.
    Rot-guarded against `TranscriptScreen.BINDINGS`, so a key added to the screen
    without being swept here fails rather than going untested.
    """
    assert {binding.key for binding in TranscriptScreen.BINDINGS} == set(
        _TRANSCRIPT_BACK_KEYS
    )

    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name=f"hl-back-{key}.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        detail = await _open_detail_for_first_row(pilot)
        await _highlight_game(pilot, 0)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH

        await pilot.press(key)
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        assert app.screen is detail
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
        assert app.is_running
