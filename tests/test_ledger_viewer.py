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
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from rich._wrap import divide_line as rich_divide_line
from rich.style import Style as RichStyle
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
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

# The Footer's rendered cells (spec 044, Slice 6). Private module, and
# deliberately: `textual.widgets` exports `Footer` alone, so there is no public
# name for the widget a reviewer actually reads — and reading `BINDINGS` instead
# is the tautology B15.8 exists to avoid, because `show=bool(description and
# show)` means only the rendered side can see a blank description. Pinned to
# Textual 8.2.4 like `rich._wrap.divide_line` in B15.6, and failing at COLLECTION
# time if a Textual upgrade moves it.
from textual.widgets._footer import FooterKey

from graphia.eval_ledger import (
    KIND_ATTR,
    KIND_ATTR_LAW_ABIDING,
    KIND_ATTR_MAFIA,
    KIND_DIARY,
    KIND_DIARY_SUBSTITUTED,
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
    KIND_SPEECH_SUBSTITUTED,
    KIND_THOUGHT,
    KIND_THOUGHT_SUBSTITUTED,
    METRIC_ORDER,
    SEARCH_SCOPE_ALL,
    TRANSCRIPT_KINDS,
    build_table_model,
    divide_line,
    find_matches,
    list_transcripts,
    load_ledger,
    row_for_offset,
    tokenize_transcript,
    wrap_row_starts,
)
from graphia.ui.ledger_viewer import (
    DetailScreen,
    LedgerTableScreen,
    LedgerViewerApp,
    TranscriptBody,
    TranscriptScreen,
    TranscriptSearchInput,
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
    # `slash` opens the search bar on `TranscriptScreen` (spec 044). Swept here
    # so the walk presses it on the run's figures and the table of runs as well,
    # where it must still never reach a bare games list (functional-spec §2.10
    # AC4).
    "slash",
    # `n`/`N` step through a game's matches (spec 044, Slice 4), and joined
    # `slash` here for the same free coverage: the walk presses them on the run's
    # figures and the table of runs, where they are bound to nothing at all and
    # must stay inert. The rot guard below — `TranscriptScreen.BINDINGS` is a
    # subset of this tuple — is what makes adding them here obligatory rather
    # than optional.
    "n",
    "N",
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

# The keys `TranscriptScreen` binds for searching inside the game (spec 044).
# SPLIT FROM THE BACK-OUT KEYS ABOVE RATHER THAN FOLDED INTO THEM, because the
# two tuples mean different things: these keys stay in the game. The rot guard
# below asserts the screen's bindings are exactly the UNION of the two, so a
# binding added to the screen that is declared in neither tuple still fails
# there rather than going untested — the guard is widened, not weakened.
# Slice 4 spent the one line this was left open for: `n`/`N` step through the
# matches and, like `slash`, keep the reviewer in the game rather than backing
# out of it.
#
# SLICE 6 ADDED `enter`, and it belongs in THIS tuple for the tuple's own
# reason: it keeps the reviewer in the game. It is a SYNONYM for `n` — the same
# `next_match` action under a second key, so that repeated Enter with the prompt
# CLOSED walks the matches instead of resting on the one the reviewer is already
# standing on (functional-spec §2.6, amended 2026-09-14).
#
# `enter` sitting here says nothing about Enter INSIDE the open prompt: while
# the bar is focused the `Input` consumes the key and posts `Input.Submitted`,
# so §2.5's inclusive jump never reaches a screen binding at all. B15.8 drives
# that distinction by keystroke rather than leaving it to this comment.
#
# `n`/`N` are `show=True` as of Slice 6 and the Footer guard below names them;
# `enter` is `show=False`, so that guard does not see it — which is itself the
# assertion that the synonym has not taken `n`'s Footer cell.
_TRANSCRIPT_SEARCH_KEYS = ("slash", "n", "N", "enter")

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


# THE THREE THEMES THIS RULE HAS TO HOLD UNDER, swept rather than sampled.
# `textual-dark` is the app's default and was the only one this test ran under
# when it was written; the other two are the ones a `$text-muted` redefinition
# or a Textual palette change could break in isolation while the default case
# stayed green. `textual-ansi` is last because it is the thinnest case and the
# one whose failure would matter most: there `$text-muted` collapses to the
# terminal default for BOTH diary kinds, so the strike is the whole differentia.
_TRANSCRIPT_STYLE_THEMES = ["textual-dark", "textual-light", "textual-ansi"]


@pytest.mark.parametrize("theme", _TRANSCRIPT_STYLE_THEMES)
async def test_the_substituted_diary_style_is_the_diarys_rule_plus_strike(
    tmp_path: Path, theme: str
) -> None:
    """Spec 041's rule, asserted as a RELATION — the third use of the shape.

    The direct sibling of
    :func:`test_the_diary_style_is_the_thoughts_rule_plus_underline`, and
    written to the same pattern for the same reason: tech-spec 041 does not
    describe a substituted entry's appearance in colours, it describes it as an
    operation on an existing rule — "the `diary` rule plus `strike`". So that is
    what is asserted. Slice 5 stated the seat as "its side's colour plus bold,
    and nothing else" and spec 039 the diary as "`thought`'s rule plus
    `underline`"; this is the third rule in the stylesheet built that way, and
    the derivation IS the design: a substituted entry can only ever read as "a
    diary, and struck", never as some fourth unrelated kind.

    Resolved through the PILOT API rather than pinned as a snapshot, which is
    the choice tasks.md ratifies and which matters more here than anywhere
    else in this section. This rule stacks THREE `text-style` flags
    (`italic underline strike`) on `$text-muted`, and how two thin horizontal
    rules compose with sheared glyphs is the terminal font's business — a
    snapshot would pin one font's answer to a question the app does not own,
    and would go red on a machine where nothing had changed. `_kind_styles` is
    where the CSS actually becomes the style a span carries, so what is asserted
    here is what the span really gets. That distinction is not academic: this
    design measured `overline` DEAD at exactly that hop (it parses, `o` is in
    Textual's `VALID_STYLE_FLAGS`, and `textual.style.Style.from_rich_style` on
    the installed 8.2.4 has no `overline` parameter, so the span renders
    undecorated), which is why the axis is `strike` and why reading the resolved
    style rather than the stylesheet text is the whole protection.

    SWEPT OVER ALL THREE THEMES rather than run under the default, which is the
    one thing the relational shape does not give for free. Every claim below is
    a relation between two resolved styles, and a relation can hold on one
    palette and break on another: `$text-muted` is a different value on each of
    `textual-dark` (`#a0a0a0`), `textual-light` (`#595959`) and `textual-ansi`
    (the terminal default), and only the last of those is a colour this
    stylesheet cannot reason about at all. Under a single-theme test a future
    palette change — or a redefinition of `$text-muted` in one theme — could
    quietly break a theme nobody's test ran, and the suite would stay green.

    THE THEME IS SET BEFORE THE TRANSCRIPT SCREEN IS OPENED, and that ordering
    is a measured fact rather than a style choice. `_styled_body` bakes the
    resolved Rich styles into the ``Static``'s renderable at **compose** time,
    so switching the theme on an already-open transcript re-themes the chrome
    but leaves the body's spans painted in the colours of the theme that
    composed them. `_kind_styles` itself re-resolves live either way — so the
    assertions below would pass under either ordering — but composing under the
    theme is what a reviewer actually gets, and it keeps this test's subject and
    the pixels on their screen the same thing.

    Four claims, and the third is the differentia:

    * **the whole `diary` family look is inherited** — same colour, and italic
      AND underline both still set. A substituted entry is still a diary:
      still private, still filed at the Day->Night hinge, still not an act of
      allegiance. Asserted against the `diary` rule itself, not against
      literals, so the two cannot drift apart;
    * **the colour is not None**, or the inheritance equality is vacuous;
    * **it is struck and an honest diary is NOT.** The entire differentia, and
      the only thing separating the two under `textual-ansi`, where
      `$text-muted` collapses to the terminal default and the SGR flags are all
      that remain;
    * **exactly one axis**, so `bold` (the reviewer's seat) and `reverse`
      (`recap`'s scroll landmark) stay absent. Without this the third claim
      would pass on a stylesheet that piled on every flag it could and
      re-collided the marked entry with two other kinds.

    The last assertion is the non-vacuity guard the pairing cannot supply: a
    diary's colour must differ from a side's, or "a substituted diary is a
    diary's colour" would hold trivially on a stylesheet where every kind is
    the body colour.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    # The class name as a WRITTEN-OUT LITERAL, exactly as the diary test above
    # does it and for the same reason: the mapping's own guard reads two
    # production tables against each other and would agree with itself through
    # a rename of both. This line is what makes a drift report WHICH of the
    # three spellings moved.
    assert (
        module._TRANSCRIPT_KIND_COMPONENTS[KIND_DIARY_SUBSTITUTED]
        == "transcript--diary-substituted"
    )

    ledger, _ = _ledger_with_body(
        tmp_path, _HIGHLIGHT_BODY, name=f"hl-diary-substituted-style-{theme}.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        # The assignment itself is the guard for a theme that stopped existing:
        # `App.theme` is a validated reactive and raises `InvalidThemeError` on
        # a name Textual has not registered, so a renamed or removed built-in
        # fails loudly here rather than silently leaving this case sampling the
        # default palette a second time.
        pilot.app.theme = theme
        await pilot.pause()

        screen = await _open_the_only_game(pilot)
        # Resolved against the theme this case names — the whole point of the
        # sweep is that these are three different resolutions, not one.
        styles = screen._kind_styles()

        substituted, diary = styles[KIND_DIARY_SUBSTITUTED], styles[KIND_DIARY]

        assert substituted.color == diary.color, (
            "a substituted entry is still a diary and must inherit its colour "
            "— the marking is about authorship, not about allegiance or kind"
        )
        assert substituted.color is not None, (
            "the substituted rule resolved to no colour at all, so the "
            "equality above is vacuous"
        )
        # THE NON-VACUITY GUARD FOR THE SWEEP ITSELF, and it is the reason the
        # parametrization is worth more than three copies of one assertion: it
        # pins that `textual-ansi` really does resolve this family to the
        # TERMINAL DEFAULT while the other two resolve it to a concrete hue. Not
        # a colour literal — the hue is deliberately unpinned, exactly as the
        # relations below are — but a statement about which KIND of colour
        # arrives, which is what makes the ansi case the thin one. Without it a
        # future change that stopped the theme taking effect would leave all
        # three cases asserting the dark palette and all three passing.
        if theme == "textual-ansi":
            assert substituted.color.is_default, (
                "the ansi theme resolved this family to a concrete hue, so the "
                "thinnest case — where the SGR flags are the whole separation "
                "— is no longer being tested"
            )
        else:
            assert not substituted.color.is_default, (
                f"{theme} resolved this family to the terminal default, so its "
                "measured contrast is no longer a fact about this stylesheet"
            )
        assert substituted.italic is True and diary.italic is True, (
            "italic is inherited from `thought` through `diary`; dropping it "
            "from either would stop a substituted entry reading as a diary"
        )
        assert substituted.underline is True and diary.underline is True, (
            "underline is `diary`'s own differentia; a substituted entry keeps "
            "it, because it is still an entry filed on the record"
        )
        assert substituted.strike is True, (
            "the substituted body is not struck, so nothing in the rendered "
            "body says the words are not the player's — functional-spec 041's "
            "fourth requirement is unmet"
        )
        assert diary.strike is not True, (
            "an honest diary is struck too, so strike no longer separates a "
            "substituted entry from a real one — which is the one thing this "
            "kind exists to say, and all that separates them under "
            "`textual-ansi`"
        )
        # Exactly ONE axis beyond `diary`'s: the two flags already spoken for
        # stay unspoken here.
        assert substituted.bold is not True, (
            "bold is the reviewer's own seat (Slice 5); a substituted body "
            "wearing it puts a stand-in sentence in the seat's register"
        )
        assert substituted.reverse is not True, (
            "reverse is `recap`'s scroll landmark; a second inverted block "
            "files the moderator's posted fact and a void entry as one look"
        )

        # ...and a diary's colour really is not a side's, so "a substituted
        # diary is a diary's colour" is saying something.
        assert diary.color != styles[KIND_SPEECH_MAFIA].color
        assert diary.color != styles[KIND_SPEECH_LAW_ABIDING].color


@pytest.mark.parametrize("theme", _TRANSCRIPT_STYLE_THEMES)
async def test_the_substituted_thought_style_is_the_thoughts_rule_plus_strike(
    tmp_path: Path, theme: str
) -> None:
    """Spec 041 Slice 9's rule, asserted as a RELATION — the fourth use of the shape.

    The direct sibling of
    :func:`test_the_substituted_diary_style_is_the_diarys_rule_plus_strike`, and
    written to the same pattern for the same reason: tech-spec 041 does not
    describe a substituted body's appearance in colours, it describes it as an
    operation on an existing rule — here "the `thought` rule plus `strike`". So
    that is what is asserted. Slice 5 stated the seat as "its side's colour plus
    bold, and nothing else", spec 039 the diary as "`thought`'s rule plus
    `underline`", Slice 8 the marked diary as "the `diary` rule plus `strike`";
    this is the fourth rule in the stylesheet built that way.

    THE DERIVATION IS FROM `thought`, NOT FROM `diary-substituted`, which is the
    whole design of the marking family and the reason this test is not a copy of
    the diary's with two names swapped. Each marked kind is *its own honest kind
    plus the one flag*, so the two marked kinds differ by exactly the flag their
    honest forms already differ by (`underline`), and a reviewer who has learnt
    "struck = nobody wrote this" reads both without learning a second thing.
    Deriving this rule from `diary-substituted` would have had to SUBTRACT
    `underline` — an operation the family has never needed — and would leave a
    substituted thought looking like a retreat from a diary rather than a
    thought that was stood in for. This one is therefore the stylesheet's one
    place where the later kind is the QUIETER one: two flags (`italic strike`)
    against the marked diary's three.

    Resolved through the PILOT API rather than pinned as a snapshot, for the
    reason the diary sibling records: `_kind_styles` is where the CSS actually
    becomes the style a span carries, and that hop is where this design measured
    `overline` DEAD on the installed Textual 8.2.4 (it parses, and `o` is in
    ``VALID_STYLE_FLAGS``, but ``rich.style.Style.parse("overline")`` returns an
    empty ``Style()`` and ``textual.style.Style`` has no ``overline`` field for
    ``from_rich_style`` to carry — so the declaration reads correctly and paints
    nothing). Reading the resolved style rather than the stylesheet text is the
    whole protection against a flag that silently does not arrive.

    SWEPT OVER ALL THREE THEMES rather than run under the default, because every
    claim below is a relation between two resolved styles and a relation can
    hold on one palette and break on another: ``$text-muted`` is a different
    value on each of ``textual-dark``, ``textual-light`` and ``textual-ansi``,
    and only the last is a colour this stylesheet cannot reason about at all.

    THE THEME IS SET BEFORE THE TRANSCRIPT SCREEN IS OPENED, which is Task 8.6's
    measured finding rather than a style choice: `_styled_body` bakes the
    resolved Rich styles into the ``Static``'s renderable at **compose** time, so
    switching the theme on an already-open transcript re-themes the chrome and
    leaves the body's spans painted in the palette that composed them.
    `_kind_styles` re-resolves live either way, so the assertions would pass
    under either ordering — but composing under the theme keeps this test's
    subject and the pixels on a reviewer's screen the same thing.

    The claims, and the LAST ONE IS THE LOAD-BEARING ADDITION the diary sibling
    does not make:

    * **the whole `thought` look is inherited** — same colour, and italic still
      set. A substituted thought is still a thought: still private, still an
      aside, still not an act of allegiance. Asserted against the `thought` rule
      itself, not against literals, so the two cannot drift apart;
    * **the colour is not None**, or the inheritance equality is vacuous, plus
      the sweep's own non-vacuity guard on WHICH KIND of colour arrives;
    * **it is struck and an honest thought is NOT** — the entire differentia,
      and all that separates them under ``textual-ansi``;
    * **exactly one axis**, so `underline` (`diary`'s differentia), `bold` (the
      reviewer's seat) and `reverse` (`recap`'s scroll landmark) stay absent.
      Without this the third claim would pass on a stylesheet that piled on
      every flag it could and re-collided the marked thought with three other
      kinds;
    * **and the two MARKED kinds stay tellable apart from each other.** Task 9.4
      proved that at the TOKENIZER level
      (``test_the_four_marked_and_authored_body_kinds_are_four_distinct_kinds``);
      this is the style-level half of the same claim, and it is the one thing
      the module's completeness equality
      (``_TRANSCRIPT_KIND_COMPONENTS == TRANSCRIPT_KINDS - {plain}``) cannot
      give — that equality proves a CLASS EXISTS, not that its rule says
      anything different from the other marked kind's. ``textual-ansi`` is where
      the claim is thinnest: both kinds resolve to ``$text-muted``, which there
      collapses to the terminal default, so the SGR flags are the ENTIRE
      separation. A struck line in a preserved game has to say WHICH private
      channel the model failed to answer on — the per-run counts (Slices 6 and
      7) already report how many failed and cannot report which — so a reviewer
      who has learnt "struck = nobody wrote this" must still be able to tell a
      voided reflection from a voided diary entry. Asserted as the difference
      being PRECISELY ``underline``, axis by axis, rather than as a bare
      inequality: a bare ``!=`` would also be satisfied by two rules that
      diverged on hue or on `bold`, which would break the family's one-meaning
      -per-flag accounting while passing the test.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    # The class name as a WRITTEN-OUT LITERAL, exactly as the two rules above do
    # it and for the same reason: the mapping's own guard reads two production
    # tables against each other and would agree with itself through a rename of
    # both. This line is what makes a drift report WHICH of the three spellings
    # moved.
    assert (
        module._TRANSCRIPT_KIND_COMPONENTS[KIND_THOUGHT_SUBSTITUTED]
        == "transcript--thought-substituted"
    )

    ledger, _ = _ledger_with_body(
        tmp_path, _HIGHLIGHT_BODY, name=f"hl-thought-substituted-style-{theme}.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        # The assignment itself is the guard for a theme that stopped existing:
        # `App.theme` is a validated reactive and raises `InvalidThemeError` on
        # a name Textual has not registered, so a renamed or removed built-in
        # fails loudly here rather than silently leaving this case sampling the
        # default palette a second time.
        pilot.app.theme = theme
        await pilot.pause()

        screen = await _open_the_only_game(pilot)
        # Resolved against the theme this case names — the whole point of the
        # sweep is that these are three different resolutions, not one.
        styles = screen._kind_styles()

        substituted, thought = styles[KIND_THOUGHT_SUBSTITUTED], styles[KIND_THOUGHT]

        assert substituted.color == thought.color, (
            "a substituted thought is still a thought and must inherit its "
            "colour — the marking is about authorship, not about allegiance "
            "or kind"
        )
        assert substituted.color is not None, (
            "the substituted rule resolved to no colour at all, so the "
            "equality above is vacuous"
        )
        # THE NON-VACUITY GUARD FOR THE SWEEP ITSELF, and the reason the
        # parametrization is worth more than three copies of one assertion: it
        # pins that `textual-ansi` really does resolve this family to the
        # TERMINAL DEFAULT while the other two resolve it to a concrete hue. Not
        # a colour literal — the hue is deliberately unpinned, exactly as the
        # relations below are — but a statement about which KIND of colour
        # arrives, which is what makes the ansi case the thin one. Without it a
        # future change that stopped the theme taking effect would leave all
        # three cases asserting the dark palette and all three passing.
        if theme == "textual-ansi":
            assert substituted.color.is_default, (
                "the ansi theme resolved this family to a concrete hue, so the "
                "thinnest case — where the SGR flags are the whole separation "
                "— is no longer being tested"
            )
        else:
            assert not substituted.color.is_default, (
                f"{theme} resolved this family to the terminal default, so its "
                "measured contrast is no longer a fact about this stylesheet"
            )
        assert substituted.italic is True and thought.italic is True, (
            "italic is `thought`'s own differentia and the inherited half here; "
            "dropping it from either would stop a substituted thought reading "
            "as a thought"
        )
        assert substituted.strike is True, (
            "the substituted body is not struck, so nothing in the rendered "
            "body says the words are not the player's — functional-spec 041's "
            "fourth requirement is unmet on the channel a reviewer actually "
            "reads (10,706 thought bodies in the committed corpus against 376 "
            "diary ones)"
        )
        assert thought.strike is not True, (
            "an honest thought is struck too, so strike no longer separates a "
            "substituted thought from a real one — which is the one thing this "
            "kind exists to say, and all that separates them under "
            "`textual-ansi`"
        )
        # Exactly ONE axis beyond `thought`'s, so this rule stays TWO flags to
        # the marked diary's three. `underline` is named first because it is not
        # merely "a flag someone else owns" here: it is the flag that tells the
        # two MARKED kinds apart, so a substituted thought wearing it is the
        # collision the section below exists to catch.
        assert substituted.underline is not True, (
            "underline is `diary`'s differentia; a substituted thought wearing "
            "it derives from `diary-substituted` rather than from `thought`, "
            "and collides with a voided diary entry under `textual-ansi`"
        )
        assert substituted.bold is not True, (
            "bold is the reviewer's own seat (Slice 5); a substituted body "
            "wearing it puts a stand-in sentence in the seat's register"
        )
        assert substituted.reverse is not True, (
            "reverse is `recap`'s scroll landmark; a second inverted block "
            "files the moderator's posted fact and a voided thought as one look"
        )

        # THE STYLE-LEVEL HALF OF THE DISTINGUISHABILITY CLAIM — the assertion
        # this test exists for and the one the diary sibling cannot make, since
        # it is a claim about the PAIR. Task 9.4 settled it at the tokenizer;
        # here it is settled at the pixels, on the theme where the colours give
        # nothing.
        diary_substituted, diary = (
            styles[KIND_DIARY_SUBSTITUTED],
            styles[KIND_DIARY],
        )

        # First the premise the separation rests on: the two marked kinds share
        # a colour (both `$text-muted`), so under NO theme can hue be what tells
        # them apart — and under `textual-ansi` that shared colour is the
        # terminal default, so the flags are literally all there is.
        assert substituted.color == diary_substituted.color, (
            "the two marked kinds no longer share a colour, so this test's "
            "premise has changed: check the flag ledger above "
            "`_TRANSCRIPT_KIND_COMPONENTS` before relaxing anything below"
        )
        if theme == "textual-ansi":
            assert (
                substituted.color.is_default and diary_substituted.color.is_default
            ), (
                "the ansi case no longer collapses both marked kinds to the "
                "terminal default, so the hardest form of distinguishability "
                "is no longer being tested"
            )

        # THE DIFFERENCE IS PRECISELY `underline`. Asserted axis by axis rather
        # than as `substituted != diary_substituted`, because a bare inequality
        # would also be satisfied by two rules that had diverged on hue or on a
        # flag the family has spent elsewhere — which would tell them apart
        # while breaking the one-meaning-per-flag accounting the family is for.
        assert diary_substituted.underline is True, (
            "a voided diary entry lost `underline`, so the two marked kinds now "
            "resolve to the same style: a struck line in a preserved game no "
            "longer says WHICH private channel the model failed to answer on"
        )
        assert substituted.underline is not diary_substituted.underline, (
            "the two marked kinds agree on `underline`, which is the only axis "
            "separating them — see the assertion above"
        )
        # ...and they agree on everything else, which is what makes `underline`
        # the WHOLE difference rather than one of several.
        assert substituted.strike is diary_substituted.strike is True, (
            "the two marked kinds disagree on `strike`, so the flag no longer "
            "carries one meaning — *this body is not the player's writing* — "
            "across the stylesheet"
        )
        assert substituted.italic is diary_substituted.italic is True
        assert substituted.bold is diary_substituted.bold
        assert substituted.reverse is diary_substituted.reverse

        # ...and the separation really is the one the HONEST forms already draw,
        # which is why a reviewer needs no second thing to learn: `diary` is
        # underlined and `thought` is not, and the marked pair inherits exactly
        # that.
        assert diary.underline is True and thought.underline is not True, (
            "the honest pair no longer differs by `underline`, so the marked "
            "pair's separation is an unrelated fact a reviewer has to learn "
            "twice rather than the family's own derivation showing through"
        )

        # ...and a thought's colour really is not a side's, so "a substituted
        # thought is a thought's colour" is saying something.
        assert thought.color != styles[KIND_SPEECH_MAFIA].color
        assert thought.color != styles[KIND_SPEECH_LAW_ABIDING].color


@pytest.mark.parametrize("theme", _TRANSCRIPT_STYLE_THEMES)
async def test_the_substituted_speech_style_is_the_speechs_rule_plus_strike(
    tmp_path: Path, theme: str
) -> None:
    """Spec 041 Slice 10's rule, asserted as a RELATION — the fifth use of the shape.

    The third and last member of the marking family, and the sibling of
    :func:`test_the_substituted_diary_style_is_the_diarys_rule_plus_strike` and
    :func:`test_the_substituted_thought_style_is_the_thoughts_rule_plus_strike`.
    Written to the same pattern for the same reason: tech-spec 041 does not
    describe a substituted body's appearance in colours, it describes it as an
    operation on an existing rule — here "the `speech` rule plus `strike`".
    Slice 5 stated the seat as "its side's colour plus bold, and nothing else",
    spec 039 the diary as "`thought`'s rule plus `underline`", Slice 8 the
    marked diary and Slice 9 the marked thought; this is the fifth rule in the
    stylesheet built that way, and the LAST — `_ai_day_action`, `_ai_reflect`
    and `day_diary` are the three substitution sites that produce
    reader-visible prose.

    THE DERIVATION IS FROM THE *NEUTRAL* ``speech``, WHICH IS THE SIDE-TINTING
    DECISION MADE VISIBLE, and it is why this test is not either sibling with a
    name swapped. Speech is the only channel in the family whose honest forms
    are side-bearing (``speech-mafia``, ``speech-law-abiding``, and the neutral
    for a line whose side is unknown), so this rule had a real choice its two
    predecessors did not: inherit a SIDE, or inherit the body colour. It
    inherits the body colour, because colour has meant *side* since Slice 4, a
    side is a fact about the SPEAKER, and the words of a substituted line have
    no author to have a side.

    So the assertion that would be redundant on either sibling is load-bearing
    here: **the marked body's colour is the neutral ``speech`` rule's and NOT
    either side's.** A rule written as ``speech-mafia`` plus ``strike`` would
    pass every other claim below.

    THE PLAINEST FORM THE MARKING TAKES ANYWHERE: one flag, against
    ``thought-substituted``'s two and ``diary-substituted``'s three, because the
    honest form it derives from carries none. The stack shrinks as the honest
    forms do, so the last kind added is the quietest — which is worth asserting
    rather than reading as an oversight, and is what the three-way separation
    below rests on.

    Resolved through the PILOT API rather than pinned as a snapshot, for the
    reason both siblings record: ``_kind_styles`` is where the CSS actually
    becomes the style a span carries, and that hop is where this design
    measured ``overline`` DEAD on the installed Textual (it parses, and ``o`` is
    in ``VALID_STYLE_FLAGS``, but ``rich.style.Style.parse("overline")`` returns
    an empty ``Style()``). Reading the resolved style rather than the stylesheet
    text is the whole protection against a flag that silently does not arrive.

    SWEPT OVER ALL THREE THEMES rather than run under the default, because every
    claim below is a relation between two resolved styles and a relation can
    hold on one palette and break on another. ``textual-ansi`` is the thin case
    for a different reason here than for the two private kinds: ``$text``
    collapses to the terminal default there, so all three marked kinds resolve
    to the same colour and the SGR flags are the entire separation — and this
    kind is separated from BOTH of the others by the ABSENCE of ``italic``,
    which no theme can take away.

    The claims, and the last two are this kind's own:

    * **the whole neutral ``speech`` look is inherited** — same colour,
      asserted against the ``speech`` rule itself rather than against a literal,
      so the two cannot drift apart;
    * **the colour is not None**, or that equality is vacuous, plus the sweep's
      own non-vacuity guard on WHICH KIND of colour arrives;
    * **it is struck and an honest line is NOT** — the entire differentia;
    * **exactly ONE axis**, so ``italic`` (``thought``'s), ``underline``
      (``diary``'s), ``bold`` (the seat) and ``reverse`` (``recap``'s) all stay
      absent. This is the claim that keeps it the plainest of the three;
    * **the body is NOT a side's colour**, which is the side-tinting decision;
    * **and all THREE marked kinds stay tellable apart from one another.**
      Sections 6-8 of ``test_spec041_substitution_marking`` prove that at the
      TOKENIZER; this is the style-level half, and it is the one thing the
      module's completeness equality (``_TRANSCRIPT_KIND_COMPONENTS`` ==
      ``TRANSCRIPT_KINDS - {plain}``) cannot give — that equality proves a CLASS
      EXISTS, not that its rule says anything different from the other two.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    # The class name as a WRITTEN-OUT LITERAL, exactly as the rules above do it
    # and for the same reason: the mapping's own guard reads two production
    # tables against each other and would agree with itself through a rename of
    # both. This line is what makes a drift report WHICH spelling moved.
    assert (
        module._TRANSCRIPT_KIND_COMPONENTS[KIND_SPEECH_SUBSTITUTED]
        == "transcript--speech-substituted"
    )

    ledger, _ = _ledger_with_body(
        tmp_path, _HIGHLIGHT_BODY, name=f"hl-speech-substituted-style-{theme}.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_PANEL_TERMINAL_SIZE) as pilot:
        # The assignment itself is the guard for a theme that stopped existing:
        # `App.theme` is a validated reactive and raises `InvalidThemeError` on
        # a name Textual has not registered.
        pilot.app.theme = theme
        await pilot.pause()

        screen = await _open_the_only_game(pilot)
        styles = screen._kind_styles()

        substituted, speech = styles[KIND_SPEECH_SUBSTITUTED], styles[KIND_SPEECH]

        assert substituted.color == speech.color, (
            "a substituted line is still Day speech and must inherit the "
            "neutral `speech` colour — the marking is about authorship, not "
            "about allegiance or kind"
        )
        assert substituted.color is not None, (
            "the substituted rule resolved to no colour at all, so the "
            "equality above is vacuous"
        )
        # THE NON-VACUITY GUARD FOR THE SWEEP ITSELF, the same one both
        # siblings carry: it pins that `textual-ansi` really does resolve this
        # family to the TERMINAL DEFAULT while the other two resolve it to a
        # concrete hue. Not a colour literal — the hue is deliberately unpinned
        # — but a statement about which KIND of colour arrives, which is what
        # makes the ansi case the thin one.
        if theme == "textual-ansi":
            assert substituted.color.is_default, (
                "the ansi theme resolved `$text` to a concrete hue, so the "
                "thinnest case — where the SGR flags are the whole separation "
                "— is no longer being tested"
            )
        else:
            assert not substituted.color.is_default, (
                f"{theme} resolved `$text` to the terminal default, so its "
                "measured contrast is no longer a fact about this stylesheet"
            )
        assert substituted.strike is True, (
            "the substituted body is not struck, so nothing in the rendered "
            "body says the words are not the player's — functional-spec 041's "
            "fourth requirement is unmet on the only PUBLIC channel, the one "
            "whose failure the other players at the table actually heard"
        )
        assert speech.strike is not True, (
            "an honest line is struck too, so strike no longer separates a "
            "substituted line from a real one — which is the one thing this "
            "kind exists to say, and all that separates them under "
            "`textual-ansi`"
        )
        # EXACTLY ONE AXIS, and on this kind that means one flag TOTAL rather
        # than one flag beyond an inherited stack — `speech` carries none. This
        # is what keeps it the plainest of the three, and `italic` is named
        # first because it is not merely "a flag someone else owns": it is what
        # separates this kind from BOTH private marked kinds under
        # `textual-ansi`, where no colour separates anything.
        assert substituted.italic is not True, (
            "italic is `thought`'s differentia and both private marked kinds "
            "wear it; a substituted LINE wearing it collides with both of them "
            "on the one theme where the flags are the whole separation"
        )
        assert substituted.underline is not True, (
            "underline is `diary`'s differentia; a substituted line wearing it "
            "derives from `diary-substituted` rather than from `speech`"
        )
        assert substituted.bold is not True, (
            "bold is the reviewer's own seat (Slice 5); a substituted body "
            "wearing it puts a stand-in sentence in the seat's register — and "
            "a substituted line can never BE the seat's, since the human "
            "branch never calls `_ai_day_action`"
        )
        assert substituted.reverse is not True, (
            "reverse is `recap`'s scroll landmark; a second inverted block "
            "files the moderator's posted fact and a voided turn as one look"
        )

        # THE SIDE-TINTING DECISION, at the pixels. The body inherits the
        # NEUTRAL speech colour and not either side's — which is the assertion
        # that would be trivial on the two private kinds (their honest forms
        # were never tinted) and is the whole open question on this one.
        assert substituted.color != styles[KIND_SPEECH_MAFIA].color, (
            "the marked body resolved to the Mafia side's colour, so this rule "
            "derives from `speech-mafia` rather than from the neutral `speech`: "
            "the words of a substituted line have no author to have a side"
        )
        assert substituted.color != styles[KIND_SPEECH_LAW_ABIDING].color
        # ...and the two side rules really are different colours from each
        # other on this theme, so the two assertions above are not both passing
        # because every speech kind resolved to one value.
        assert (
            styles[KIND_SPEECH_MAFIA].color != styles[KIND_SPEECH_LAW_ABIDING].color
        ), (
            "the two side colours collapsed on this theme, so 'not either "
            "side's colour' is no longer a distinguishing claim"
        )

        # THE STYLE-LEVEL HALF OF THE THREE-WAY DISTINGUISHABILITY CLAIM — the
        # assertion this test exists for, and the one neither sibling can make
        # since it is a claim about the TRIPLE. Task 10.3 settled it at the
        # tokenizer; here it is settled at the pixels, including on the theme
        # where the colours give nothing.
        diary_substituted = styles[KIND_DIARY_SUBSTITUTED]
        thought_substituted = styles[KIND_THOUGHT_SUBSTITUTED]

        # All three carry the flag with ONE meaning — "this body is not the
        # player's writing" — which is the accounting the family exists to keep.
        assert (
            substituted.strike
            is diary_substituted.strike
            is thought_substituted.strike
            is True
        ), (
            "the three marked kinds disagree on `strike`, so the flag no longer "
            "carries one meaning across the stylesheet"
        )
        # ...and they are separated by the flags their HONEST forms already
        # differ by, so a reviewer who has learnt "struck = nobody wrote this"
        # needs no second thing to learn. Asserted as the exact flag TRIPLE per
        # kind rather than as three inequalities: a bare `!=` would also be
        # satisfied by two rules that diverged on hue or on `bold`, which would
        # tell them apart while breaking the one-meaning-per-flag accounting.
        def _flags(style: RichStyle) -> tuple[bool, bool, bool]:
            return (
                style.italic is True,
                style.underline is True,
                style.strike is True,
            )

        assert _flags(substituted) == (False, False, True)
        assert _flags(thought_substituted) == (True, False, True)
        assert _flags(diary_substituted) == (True, True, True)
        # Three distinct flag stacks, which is the claim in one line — and the
        # one that holds under `textual-ansi`, where all three colours are the
        # terminal default.
        assert len(
            {
                _flags(substituted),
                _flags(thought_substituted),
                _flags(diary_substituted),
            }
        ) == 3, (
            "two of the three marked kinds resolve to the same text style, so a "
            "struck line in a preserved game no longer says which of the three "
            "answers the model failed to give — and under `textual-ansi` the "
            "flags are all a reviewer has"
        )
        # ...and this one really is the PLAINEST of the three, which is the
        # ledger's "the stack shrinks as the honest forms do" claim.
        assert sum(_flags(substituted)) == 1
        assert sum(_flags(thought_substituted)) == 2
        assert sum(_flags(diary_substituted)) == 3


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

    Pinned as ABSOLUTE geometry — an 82-line game in a 22-row viewport is 60 rows
    of scroll — plus the offset each key produces. `virtual_size.height` equalling
    the game's line count is the "no unexpected wrapping" claim: one wrapped line
    would make it 83.

    (Those three figures read 202, 180 and 203 until spec 044, and no constant in
    this file has ever produced them: `_LONG_GAME_PROSE_LINES` is 80, so the body
    is 82 lines and `max_scroll_y` is 60. The error was harmless — every assertion
    below reads the symbolic constants, never the prose — but it is corrected here
    rather than left to mislead the next reader. Measured at runtime through this
    very test: `size.height` 22, `virtual_size.height` 82, `max_scroll_y` 60.)

    EVERY SCROLL KEY IS SETTLED through `_settle_scroll` before its row is read,
    for the reason that helper's docstring sets out at length: all six scroll keys
    animate, and the row a test wants does not exist at the moment the key is
    released. This test is the MOST exposed to that race in the file, not the
    least — it holds the longest travels there are, `end` running 23 → 60 and
    `home` running 60 → 0, each over the fixed 1.0 s `scroll_end`/`scroll_home`
    duration. That it passed on `main` with only `pilot.pause()` between press and
    assertion is the accident `_settle_scroll` describes: `App._press_keys`'
    incidental `wait_until_complete()` and `wait_for_idle(0)` happened to absorb
    the animation, and `pilot.pause()` itself returns in ~22 ms, covering none of
    it. Remove those two incidental waits — changing nothing else — and the
    committed press-then-pause sequence reads row 9 of the `pagedown` travel, row
    31 of the `end` travel and row 37 of the `home` travel, where the assertions
    below say 23, 60 and 0.

    No assertion is weakened, added or removed by that settle; it only makes the
    absolute ones reliable. `escape` below keeps a plain `pilot.pause()` — it
    pops a screen rather than scrolling, so there is no animation to settle.

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
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == 1

        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == 1 + _LONG_GAME_VIEWPORT_ROWS

        await pilot.press("end")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _LONG_GAME_MAX_SCROLL_Y

        await pilot.press("home")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == 0

        # The last line of the game really is reachable, and it is the game's.
        await pilot.press("end")
        await _settle_scroll(pilot)
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
    back-out keys, from the transcript itself, at an absolute stack depth, with no
    search open.

    Rot-guarded against `TranscriptScreen.BINDINGS` as the UNION of the back-out
    keys and the search keys (spec 044), so a key added to the screen that is
    declared in neither tuple fails here rather than going untested.
    """
    assert {binding.key for binding in TranscriptScreen.BINDINGS} == set(
        _TRANSCRIPT_BACK_KEYS
    ) | set(_TRANSCRIPT_SEARCH_KEYS)

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


# ===========================================================================
# B15. Searching inside a game (spec 044, Slices 1-5) — the bar opens, takes
#      text, lights up what it finds, the game keeps scrolling underneath
#      it, Enter brings a match to the top row, `n`/`N` walk the rest, and
#      the whole thing clears away without costing the reviewer their place.
#
#      KEY ROUTING IS THE ENTIRE RISK OF THIS SLICE, which is why every test
#      below drives REAL KEYSTROKES through `pilot.press(...)` and never
#      assigns `Input.value`. The feature adds a second focusable widget to a
#      screen that has only ever had one, and almost everything that can go
#      wrong is a question about where a keypress LANDS: does `/` reach the
#      screen or get eaten; does `home` reach the game or get claimed by the
#      focused box; does `q` leave the game or become a character. A helper
#      that set `value` directly — `_drive_search` (B4) is exactly such a
#      helper, for the runs-list filter on a different screen — would make
#      every one of these tests pass while answering none of those questions.
#      `_type_transcript_search` below exists to keep the two apart, and says
#      so in its own docstring so the next reader does not "consolidate" them.
#
#      THE CLOSED BAR IS PINNED AS ABSOLUTE GEOMETRY, not as a before/after
#      comparison, following spec 037's mutation finding (an assertion of the
#      form "X equals Y between two states" holds happily with both sides
#      broken). Functional-spec §2.10 AC2 — "nothing about searching is taking
#      up space they were using to read" — is the criterion, and the assertion
#      that carries it is THE SCROLLER'S HEIGHT, not the bar's own size.
#
#      That distinction is worth writing down, because the obvious assertion is
#      the wrong one. `#search-row` does measure `(0, 0)` while closed — but
#      MUTATION-TESTED, `(0, 0)` DOES NOT SEPARATE `display: none` FROM
#      `visibility: hidden`. An invisible widget is absent from the screen's
#      widget map, so `Widget.region` catches `NoWidget` and falls back to
#      `NULL_REGION` under either rule (Textual 8.2.4, `widget.py`). And
#      `visibility: hidden` nonetheless RESERVES the row: measured, the
#      scroller drops 22 rows to 21, so a reviewer who never presses `/` would
#      silently pay a line of game forever while the bar still measured
#      `(0, 0)`. The scroller's height is the assertion that catches that, and
#      the `(0, 0)` beside it says the narrower thing it can say — the bar is
#      not on screen — which is what fails when a `-open` rule leaks its
#      `display: block` into the base selector.
#
#      WHAT IS DELIBERATELY NOT READ: `TranscriptScreen._search`. The state
#      field is the implementation of the Esc machine, not its promise; what
#      the reviewer is owed is that the first Esc closes the bar and keeps
#      them in the game and the second one leaves, which is asserted here
#      through screens and stack depth. Reading the private field would pin
#      the mechanism and leave the promise untested.
#
#      SLICE 5's B15.7 IS THE ONE PLACE THAT DOES READ IT, and that exception
#      is forced rather than a change of mind. Task 5.2a's finding is that two
#      of §2.9's states are IDENTICAL ON SCREEN — after Enter on an empty query
#      and after Enter on a query that matched nothing, nothing is lit, the bar
#      is hidden and the header is back at its resting hint — so the field is
#      the only thing that separates them at the moment of the press. Every
#      state assertion there is PAIRED with the observable consequence (how
#      many Escs it then takes to leave), so the promise is still what is
#      tested and the field is only what makes the two cases nameable.
#
#      `_LONG_GAME_BODY` IS REUSED rather than joined by a fixture of its own.
#      B14.4 already pins its geometry absolutely (82 lines, a 22-row
#      viewport, `max_scroll_y` 60), so the one row the open bar costs is
#      visible as arithmetic on constants that already have a witness, and
#      every scroll assertion below can be stated as a position in a game
#      whose shape is already proven. Its lines are short enough never to
#      wrap at this terminal width, which is what makes "row" and "line" the
#      same thing here and the expected offsets readable at all.
#
#      SLICE 2 ADDED B15.2, and it asks a different question from the rest of
#      this section: not where a keypress LANDS, but what the game LOOKS LIKE
#      once one has. Its assertions are therefore about SPANS — which character
#      ranges carry a style, and in what ORDER — keeping B14's convention of
#      never pinning rendered colour, with the single exception the tech spec
#      names (a highlight has to differ from the ground it sits on or there is
#      nothing to see). Two facts about the implementation are what make those
#      assertions possible at all, and both are worth knowing before reading
#      one: the match spans are APPENDED to spec 038's kind spans rather than
#      merged with them, so a highlighted body's runs split cleanly into "what
#      the tokenizer said" and "what the search added" (`_search_runs`), and
#      the repaint is DEBOUNCED, so every read has to wait out
#      `_SEARCH_SETTLE_SECONDS` first or it measures the previous keystroke.
#
#      SLICE 3 ADDED B15.6, AND IT IS THE ONE SUB-SECTION HERE THAT IS NOT
#      ABOUT THIS FEATURE AT ALL. Everything above asks what the viewer does;
#      B15.6 asks whether `wrap_row_starts` still agrees with the wrapping
#      Textual actually performs. The two agree BY CONSTRUCTION today — the
#      mirror calls the very `rich._wrap.divide_line` that `textual/content.py`
#      calls — and that is exactly why it needs a pin: a Rich or Textual upgrade
#      that folds lines differently does not crash and does not look wrong. It
#      moves every row boundary, and the only symptom is that Enter, `n` and `N`
#      quietly land on the wrong line for the rest of the program's life. The
#      three legs are what turn that into a red test. Its banner explains each.
#
#      SLICE 3 ALSO ADDED B15.4, WHICH IS WHERE THE FEATURE STOPS BEING A
#      COLOURING TOOL AND BECOMES A NAVIGATION ONE. Its question is neither
#      where a keypress landed nor what lit up, but WHERE THE GAME CAME TO
#      REST — so every assertion in it is an ABSOLUTE ROW, and its fixtures
#      exist so that those rows can be written down. Two of its tests would
#      be vacuous without a fixture built for them: a jump has to be measured
#      from somewhere with matches on BOTH sides of it, and the end-of-game
#      clamp will happily produce the right-looking answer for a match in the
#      tail however wrong the arithmetic behind it was. Its banner says which
#      game answers which criterion.
#
#      SLICE 4 ADDED B15.5, AND IT ASKS THE QUESTION B15.4 COULD NOT: what
#      happens on the presses AFTER the prompt has closed. `n` and `N` are the
#      first keys in this feature that exist only in the state Enter leaves
#      behind, and the header is the first thing this screen says that has to
#      outlive a widget. So B15.5 reads two channels B15.1-B15.4 never touch —
#      the LIVE `Header`, through `HeaderTitle.render_line(0)` rather than off
#      the `sub_title` attribute, and WHICH match carries the stronger marker,
#      through the combined style at its first character.
#
#      THE REASON IT READS BOTH IS MEASURED, not tidiness. Inside the last
#      screenful of a game the scroll CLAMPS at `max_scroll_y`, so successive
#      presses move the view not at all — and the marker and the count are then
#      the whole of what tells the reviewer the key did anything. A sub-section
#      that asserted only `scroll_offset.y` would be blind exactly where the
#      feature is hardest, which is not hypothetical: Task 4.4a found `n`
#      dead-ending in that screenful after Slice 4's first round of probes
#      passed, because every one of them used the query `vote`, whose last
#      match on the 88 KB committed game sits on row 1030 against a clamp of
#      1160. `vote` cannot witness the clamp or the wrap. The three tests below
#      that touch the end of a game therefore use `the` on the largest
#      committed game, and each ASSERTS ITS FIXTURE'S PREMISE — that the last
#      match's row is past `max_scroll_y` — before it asserts any behaviour, so
#      that a curated corpus can never make them pass by being blind.
#
#      B15.5 also closes the half-criterion B15.4 left open: §2.5 AC3 asks that
#      a wrapped jump be ANNOUNCED as well as performed, and the channel for
#      that announcement did not exist when B15.4 was written.
#      `test_a_wrapped_enter_announces_the_wrap_in_the_header` is that half.
# ===========================================================================

# The search bar is one row and it comes out of the SCROLLER's height, not out
# of the reviewer's scroll position. Both halves are asserted: the viewport
# loses a row, `max_scroll_y` gains one, and `scroll_offset.y` does not move
# (functional-spec §2.1 AC3).
_SEARCH_OPEN_VIEWPORT_ROWS = _LONG_GAME_VIEWPORT_ROWS - 1
_SEARCH_OPEN_MAX_SCROLL_Y = _LONG_GAME_LINES - _SEARCH_OPEN_VIEWPORT_ROWS

# `#search-row` measured in each of its two states. Closed is `(0, 0)`: no
# region at all — which says the bar is not on screen, and deliberately not
# more than that (see the banner on why it cannot tell `display: none` from
# `visibility: hidden`). Open it spans the full terminal width and is exactly
# one row tall — a default `Input` is three, so the height override in
# `TranscriptScreen.DEFAULT_CSS` is load-bearing and this is its witness.
_SEARCH_ROW_CLOSED_SIZE = (0, 0)
_SEARCH_ROW_OPEN_SIZE = (_LONG_GAME_TERMINAL_SIZE[0], 1)

# The body's content width, closed and open alike. Pinned because equality
# across the two states is what says OPENING THE SEARCH DOES NOT REWRAP THE
# GAME: a bar that stole columns instead of a row would re-fold every line,
# and from Slice 3 onward it would silently invalidate the offset→row map that
# Enter jumps by. 96 rather than 98 because the overflowing game already shows
# a vertical scrollbar, which eats two cells.
_LONG_GAME_BODY_WIDTH = 96

# Where the six scroll keys are measured from. Chosen so that all six land on
# six DISTINCT rows, none of them clamped: `home` and `pageup` would be
# indistinguishable from any start below one page, and `pagedown` would clamp
# at `max_scroll_y` from any start above 40.
_SEARCH_SCROLL_START_Y = 30

# The six keys functional-spec §2.4 promises still scroll the game while the
# reviewer is typing, each with the row it lands on from `_SEARCH_SCROLL_START_Y`
# with the bar OPEN — so the page keys move by 21 rows, not 22.
#
# Stated as arithmetic on the fixture's constants rather than as bare numbers,
# so a fixture that grows re-derives them instead of rotting into six magic
# integers. `end` is `max_scroll_y` with the bar open, which is one MORE than
# the reading view's own `_LONG_GAME_MAX_SCROLL_Y` — the row the bar took.
_SEARCH_SCROLL_KEY_CASES = (
    ("down", _SEARCH_SCROLL_START_Y + 1),
    ("up", _SEARCH_SCROLL_START_Y - 1),
    ("pagedown", _SEARCH_SCROLL_START_Y + _SEARCH_OPEN_VIEWPORT_ROWS),
    ("pageup", _SEARCH_SCROLL_START_Y - _SEARCH_OPEN_VIEWPORT_ROWS),
    ("home", 0),
    ("end", _SEARCH_OPEN_MAX_SCROLL_Y),
)

# A game SHORTER than the viewport, and that is its whole purpose. All six of
# the scroller's actions open with `if not self.allow_vertical_scroll: raise
# SkipAction()`, and `allow_vertical_scroll` is false exactly when the content
# fits — so in the search bar of a game like this one, every one of the six
# bindings calls a method that raises. Textual's own action dispatcher catches
# `SkipAction`; a message handler does not, which is why the screen's handler
# catches it explicitly and why the test below is worth its 0.6 s.
_SHORT_GAME_BODY = (
    "<transcript>\n"
    + "\n".join(f"Alice: line {i}" for i in range(4))
    + "\n</transcript>"
)

# The four keys functional-spec §2.4 says are typed into the query rather than
# acted on. `q` and `/` are the interesting ones: `q` is the screen's
# (unadvertised) leave-the-game key and `/` is the key that opened this bar, so
# both would be a collision if the focused chain resolved them the other way.
# `n`/`N` are Slice 4's navigation keys, included now so the slice that binds
# them inherits a witness that they still type here.
_TYPED_NOT_ACTED_KEYS = "qnN/"


# HOW LONG A TEST WAITS BEFORE READING THE HIGHLIGHT (spec 044, Slice 2). The
# screen coalesces `Input.Changed` through a RESTARTING single-shot timer, so
# the spans a test asserts on do not exist at the moment the last key is
# pressed: read straight after `pilot.press(...)` and every assertion below
# measures the PREVIOUS query — which fails as a flake rather than as a bug,
# and passes on a fast machine often enough to be believed.
#
# Derived from the viewer's own `_SEARCH_DEBOUNCE_SECONDS` rather than written
# as a literal, following this file's idiom for reaching a module private
# (`importlib.import_module`, as B14's tokenizer patches do). Re-tuning the
# debounce — the constant's own docstring invites it, within the tech spec's
# 50-120 ms band — then re-tunes this wait with it, where a hardcoded 0.1 s
# would quietly start reading early the day someone widened the window.
#
# DOUBLED, because one window is the minimum and not the margin: the window
# opens on the LAST keystroke of a burst and the repaint itself still has to
# run on the event loop afterwards.
_SEARCH_SETTLE_SECONDS = (
    importlib.import_module("graphia.ui.ledger_viewer")._SEARCH_DEBOUNCE_SECONDS * 2
)

# THE GAME B15.2 SEARCHES: `_LONG_GAME_BODY`'s shape with words in it worth
# looking for. Deliberately the same 80 prose lines between the same two
# `<transcript>` tags, none of them long enough to wrap at 96 columns, so every
# geometry constant above still describes it exactly — 82 virtual rows,
# `max_scroll_y` 60 closed and 61 open, `body.content_size.width` 96 — and "the
# game did not move" can be asserted as an ABSOLUTE row rather than as a
# before/after difference (spec 037's mutation finding, the same reason B15.1
# pins the closed bar absolutely).
#
# WHY NOT `_LONG_GAME_BODY` ITSELF. Every one of its lines is `Alice: line N`,
# which offers nothing to narrow: functional-spec §2.2 AC2/AC3 are about a
# query growing by a character and the highlighting getting SMALLER, then
# widening again on Backspace, so the fixture has to contain a word that a
# longer query matches less often. The four lines below cycle so that a quarter
# of the game says `vote` and another quarter says `voting` — `vot` therefore
# matches exactly twice as often as `vote`, and the narrowing is arithmetic on
# the line count rather than two hand-counted numbers that rot if the fixture
# grows. The two quiet lines are not padding: a highlight that painted whole
# LINES rather than the matched characters would light them up too.
_SEARCH_MATCH_LINES = (
    "Alice: I vote for Cleo.",  # one `vote`, and therefore one `vot`
    "Bo: Cleo keeps voting late.",  # one `vot`, and deliberately NOT a `vote`
    "Cleo: nothing to add here.",  # neither
    "Alice: quiet line.",  # neither
)
_SEARCH_MATCH_BODY = (
    "<transcript>\n"
    + "\n".join(
        _SEARCH_MATCH_LINES[line % len(_SEARCH_MATCH_LINES)]
        for line in range(_LONG_GAME_PROSE_LINES)
    )
    + "\n</transcript>"
)

# 80 lines cycling through 4 gives 20 of each. `vote` matches once per vote
# line; `vot` matches once per vote line AND once per voting line.
_SEARCH_VOTE_MATCHES = _LONG_GAME_PROSE_LINES // len(_SEARCH_MATCH_LINES)
_SEARCH_VOT_MATCHES = 2 * _SEARCH_VOTE_MATCHES

# A query the fixture above contains nowhere, for functional-spec §2.8 AC2.
# Lowercase, so smart case leaves it case-INSENSITIVE and the empty result is a
# statement about the game rather than about the casing.
_SEARCH_NO_MATCH_QUERY = "zzz"

# WHY THE §2.3 TESTS SEARCH `_HIGHLIGHT_BODY` INSTEAD, and it is not
# convenience: §2.3 is a claim about what a match does to a line that ALREADY
# has a colour, so the fixture has to be the one whose every kind is pinned
# run-by-run above. `Bo` is the query because that one name lands in SIX
# different kinds of this fixture at once — a cast-list attribute, a Mafia
# speaker, a Law-abiding line's content, a private thought, the moderator's
# recap and a diary owner — so a single terminal session exercises §2.3 AC1 (a
# Mafia line still reads as Mafia), AC2 (the kinds still tell apart) and the
# recap's measured inversion together.
#
# Capitalised, so smart case makes it exact — which is what keeps the count at
# six rather than at "six plus whatever the tokenizer happens to lowercase".
_HIGHLIGHT_SEARCH_QUERY = "Bo"
_HIGHLIGHT_SEARCH_MATCHES = 6

# The two lines of `_HIGHLIGHT_BODY` the §2.3 tests read a character out of.
# Named rather than sliced at a literal offset, so the assertions survive the
# fixture growing again the way it has in every slice of spec 038.
_HIGHLIGHT_MAFIA_LINE = "Bo: Nor did I."
_HIGHLIGHT_RECAP_TEXT = "Alive: Alice, Bo."

# ===========================================================================
# B15.4's FIXTURES — three games, each shaped by the one thing its criterion
# cannot be proven without.
#
# WHY NOT `_SEARCH_MATCH_BODY`, which B15.2 already searches and which already
# says `vote` twenty times. Because its matches run all the way to row 77, and
# THE LAST SCREENFUL OF A GAME CANNOT BE BROUGHT TO THE TOP LINE: with the bar
# open `max_scroll_y` is 61, so the matches on rows 65, 69, 73 and 77 all come
# to rest at 61 whatever the jump computes. A test written on that fixture
# would be asserting the CLAMP while believing it was asserting the JUMP, and
# would keep passing with the row arithmetic deleted outright. Task 3.8 says
# this in its own words — "keep fixtures for (a)-(d) away from the tail so the
# clamp does not mask them" — and these games are that instruction.
#
# The first two keep `_LONG_GAME_BODY`'s shape exactly — 80 short prose lines
# between the two `<transcript>` tags — so B14.4's absolute geometry still
# describes them (82 rows, a 22-row viewport, `max_scroll_y` 60 closed and 61
# open) and, because no line is long enough to wrap at 96 columns, ROW AND LINE
# ARE THE SAME THING. That is what lets a match be placed ON a named row and a
# landing be read as an absolute integer rather than as a difference from
# wherever the reviewer happened to be. The third fixture is the one where row
# and line part company, and it exists precisely to make them part company.

# The word all of them are searched for, and TWO things about it are load-
# bearing rather than cosmetic.
#
# It is ABSENT from every filler line and from the `<transcript>` tags, so the
# match count IS the number of match rows and a landing row that does not
# contain it is a mis-jump rather than an ambiguity.
#
# And it is THE FIRST THING ON ITS LINE — which is the only shape in which
# `test_enter_stops_on_a_match_that_is_already_on_the_top_row` can tell an
# inclusive Enter from an exclusive one, and it was found the hard way: with
# the query mid-line (`Alice: I vote for Cleo.`) BOTH resolutions give the
# same answer and the test passes against either. `next_match` resolves from
# `offset_for_row(top row)`, the offset of the row's FIRST character, and
# `inclusive` decides only what happens to a match starting exactly THERE — a
# match nine characters into the row is strictly after it and is found by
# `bisect_left` and `bisect_right` alike. A speaker name is the realistic
# instance of this (searching for a player finds the start of every line they
# spoke) as well as the discriminating one, so the fixture searches for one.
#
# Capitalised, so smart case makes it exact — which keeps the count at exactly
# the match rows rather than at those plus whatever else the game happens to
# say in another casing. Casing itself is §2.7's question, and B15.2 asks it.
_JUMP_QUERY = "Cleo"
_JUMP_MATCH_LINE = "Cleo: I say we vote for Bo."


def _jump_game(match_rows: tuple[int, ...]) -> str:
    """`_LONG_GAME_BODY`'s exact shape, carrying `_JUMP_QUERY` on named ROWS.

    ``match_rows`` are RENDERED ROW indices, not source-line indices, and the
    ``+ 1`` is the ``<transcript>`` tag that occupies row 0. Rows are the unit
    Enter scrolls in and the unit every assertion in this sub-section is
    written in, so the fixture is addressed in the same unit — rather than
    leaving each test to do the conversion and one of them to get it wrong.
    """
    lines = [
        _JUMP_MATCH_LINE if line + 1 in match_rows else f"Alice: line {line}"
        for line in range(_LONG_GAME_PROSE_LINES)
    ]
    return "<transcript>\n" + "\n".join(lines) + "\n</transcript>"


# THE (a)-(d) GAME. Three matches, and every row is the assertion some test has
# to survive:
#
#   * row 10 sits ABOVE where (a) and (b) start reading, so an implementation
#     that jumped to the first match in the GAME rather than the first match
#     below the reviewer lands here and fails;
#   * row 30 is what Enter must find from BOTH starts — from row 22 because it
#     is the next one down, and from row 30 itself because functional-spec
#     §2.5 AC2 says a match already on the top line is where Enter STOPS;
#   * row 45 is where an EXCLUSIVE Enter would land from row 30. That is the
#     single distinction between Enter and Slice 4's `n` (offset-keyed and
#     inclusive vs row-keyed and exclusive), and this row is what makes
#     flipping the flag a red test instead of a silent behaviour change.
#
# All three are clear of `_SEARCH_OPEN_MAX_SCROLL_Y` (61), so every landing
# below is the row arithmetic's answer and never the clamp's.
_JUMP_MATCH_ROWS = (10, 30, 45)
_JUMP_BODY = _jump_game(_JUMP_MATCH_ROWS)
_JUMP_MATCHES = len(_JUMP_MATCH_ROWS)

# Where (a) reads from before it searches: exactly one `pagedown` out of the
# top of the game, pressed with the bar still closed so the setup owes nothing
# to the feature under test. Below the first match and above the second, which
# is what makes "the first match BELOW the reviewer" falsifiable at all.
_JUMP_START_ROW = _LONG_GAME_VIEWPORT_ROWS

# THE (e) GAME. One match, inside the final screenful: row 75 is past
# `_LONG_GAME_MAX_SCROLL_Y` (60) and past `_SEARCH_OPEN_MAX_SCROLL_Y` (61), so
# the game runs out of scroll before the match can reach the top line. That is
# the whole of functional-spec §2.5's fourth bullet, and this is the ONLY
# fixture in B15.4 deliberately placed in the tail.
_JUMP_TAIL_MATCH_ROW = 75
_JUMP_TAIL_BODY = _jump_game((_JUMP_TAIL_MATCH_ROW,))

# THE (width) GAME, and the one place in B15.4 where a row is not a line.
#
# Every other fixture here is short-lined on purpose, which is what makes the
# assertions readable — and which also makes the wrap width IRRELEVANT to them:
# a map built at 96, at 98 or at 76 puts `Alice: line 29` on row 30 either way.
# So one game has to wrap, because the width the map is built at is a LIVE
# reading (`body.content_size.width`) and hardcoding it is the hazard the tech
# spec ranks highest: 96 when a game overflows, 98 when it fits, and neither of
# those at a terminal that is not 100 columns wide.
#
# READ AT 80 COLUMNS RATHER THAN 100, and that is what turns a hardcoded width
# from unjustified into wrong. Here the body wraps at 76 while every line below
# is 176-188 characters — three rows at 76, two rows at 96 — so the single
# match sits on row 16 as the game is really rendered and on row 11 as a map
# built at 96 would have it, and row 11 holds ordinary filler. The test asserts
# that disagreement BEFORE it asserts the landing, so a fixture edit that made
# the two widths agree reports itself instead of passing vacuously.
#
# 80 columns is also why this cannot be tested on a game that FITS, which is
# the other half of the 96/98 split: a game that fits has `max_scroll_y == 0`
# and Enter has nowhere to move it to, so the 98 regime is unreachable by any
# landing assertion and is pinned by B15.6 instead, which needs no scroll.
_JUMP_WRAP_TERMINAL_SIZE = (80, 24)
# 80 columns - 2 for `#transcript-body`'s `padding: 0 1` - 2 for the vertical
# scrollbar this (overflowing) game grows.
_JUMP_WRAP_BODY_WIDTH = 76
_JUMP_WRAP_LINE = (
    "Alice: I keep coming back to the same three names and I would rather "
    "say so now than sit on it, so here is the order I saw things in and "
    "where I think the reasoning goes wrong."
)
_JUMP_WRAP_MATCH_LINE = (
    "Cleo: I say we vote for Bo, and I will say why at some length so "
    "that this line folds exactly the way every other line in this game "
    "folds and nothing about it renders shorter than its neighbours."
)
_JUMP_WRAP_LINES = 20
_JUMP_WRAP_MATCH_LINE_INDEX = 5
_JUMP_WRAP_BODY = (
    "<transcript>\n"
    + "\n".join(
        _JUMP_WRAP_MATCH_LINE if line == _JUMP_WRAP_MATCH_LINE_INDEX
        else _JUMP_WRAP_LINE
        for line in range(_JUMP_WRAP_LINES)
    )
    + "\n</transcript>"
)

# ===========================================================================
# B15.6's FIXTURES — two games chosen to put the offset->row map in BOTH wrap
# regimes, plus the biggest real game in the corpus.
#
# THE WRAP WIDTH IS NOT A CONSTANT, and that is the whole reason there are two
# synthetic games here rather than one. At the 100-column terminal every test
# in this section uses, `body.content_size.width` is:
#
#     100 - 2 (`#transcript-body`'s `padding: 0 1`)                    = 98
#     100 - 2 (that padding) - 2 (the vertical scrollbar)              = 96
#
# and which of the two a game gets depends on whether it OVERFLOWS the 22-row
# viewport, because that is what grows the scrollbar. A pin measured only on an
# overflowing game would leave the 98 path — every short preserved game in the
# corpus — untested, and the two widths genuinely disagree: the fold line below
# breaks after 96 characters at one width and after 98 at the other, so a map
# built at the wrong one puts the jump on the wrong row and stays wrong.
_WRAP_PIN_WIDTH_OVERFLOWING = 96
_WRAP_PIN_WIDTH_FITTING = 98

# One block of game, written so that a single block exercises every branch of
# `divide_line` that a real transcript reaches:
#
#   * `_WRAP_PIN_SPEECH`   — ordinary prose, wrapped at SPACES. 208 characters,
#     which is 3 rows at either width (cuts at 94 and 187, identical at 96 and
#     98 because the word boundaries land in the same places).
#   * `_WRAP_PIN_FOLD`     — a short speaker tag followed by ONE UNBROKEN TOKEN
#     wider than either wrap width, which sends `divide_line` down its `fold`
#     branch (`chop_cells`) instead of its word-boundary branch. Also the line
#     that makes the two widths DISTINGUISHABLE: the token is chopped at 96 at
#     one width and at 98 at the other, so leg 2 fails if the map is built at
#     the wrong width rather than passing by coincidence.
#   * `_WRAP_PIN_SHORT`    — a line that fits, so "1 source line = 1 row" is in
#     the mix too.
#   * `""`                 — a BLANK line, which Textual's `split(allow_blank=
#     True)` keeps as a row of its own. A mirror that dropped blanks would be
#     off by one from here on, which is a mis-jump of exactly the silent kind.
#
# 3 + 3 + 1 + 1 = 8 rendered rows per block, at both widths.
_WRAP_PIN_SPEECH = (
    "Alice: I have been thinking about the vote all night and I keep coming "
    "back to the same three names, so let me lay out what I saw in the order "
    "that I saw it and you can tell me where the reasoning goes wrong."
)
_WRAP_PIN_FOLD_TOKEN_CELLS = 140
_WRAP_PIN_FOLD = "Bo: " + "x" * _WRAP_PIN_FOLD_TOKEN_CELLS
_WRAP_PIN_SHORT = "Cleo: agreed."
_WRAP_PIN_BLOCK = (_WRAP_PIN_SPEECH, _WRAP_PIN_FOLD, _WRAP_PIN_SHORT, "")
_WRAP_PIN_BLOCK_ROWS = 8


def _wrapping_game(blocks: int) -> str:
    """A game of ``blocks`` copies of :data:`_WRAP_PIN_BLOCK`, between the tags.

    Deliberately NOT `_LONG_GAME_BODY`, whose every line is `Alice: line N` and
    therefore never wraps — the constant that section relies on, and the reason
    "row" and "line" are the same thing there, is exactly what makes it useless
    for pinning a wrap.
    """
    lines: list[str] = []
    for _ in range(blocks):
        lines.extend(_WRAP_PIN_BLOCK)
    return "<transcript>\n" + "\n".join(lines) + "\n</transcript>"


# Two block counts either side of the 22-row viewport, so one game grows a
# scrollbar and the other does not. Stated as arithmetic on the constants
# above rather than as 18 and 26, so a fixture edit re-derives them instead of
# rotting into two magic numbers that no longer describe the fixture. The `+ 2`
# is the pair of `<transcript>` tags, one row each.
_WRAP_PIN_FITTING_BLOCKS = 2
_WRAP_PIN_OVERFLOWING_BLOCKS = 3
_WRAP_PIN_FITTING_ROWS = _WRAP_PIN_FITTING_BLOCKS * _WRAP_PIN_BLOCK_ROWS + 2
_WRAP_PIN_OVERFLOWING_ROWS = _WRAP_PIN_OVERFLOWING_BLOCKS * _WRAP_PIN_BLOCK_ROWS + 2
_WRAP_PIN_FITTING_BODY = _wrapping_game(_WRAP_PIN_FITTING_BLOCKS)
_WRAP_PIN_OVERFLOWING_BODY = _wrapping_game(_WRAP_PIN_OVERFLOWING_BLOCKS)


def _largest_committed_transcript() -> Path | None:
    """The biggest preserved game in the corpus, or ``None`` if there is none.

    DISCOVERED, never named, for the same reason `_one_real_transcript_per_era`
    is: the corpus grew 298 -> 420 files since spec 038 and gains a directory on
    every measured eval run, so a hard-coded path is a test that breaks when
    someone curates a run away. "The biggest" is deterministic (size, then path,
    so a tie cannot reorder) and is the file that wraps the hardest — today
    `evals/transcripts/2026-09-05T17-58-24/game-01.txt`, 88 KB of model prose in
    405 source lines, which Textual renders as 1,182 rows at width 96.

    Its row count is deliberately NOT asserted anywhere. What B15.6 pins is the
    AGREEMENT between the mirror and the render; a golden 1,182 would pin which
    file happened to be biggest on the day it was written.
    """
    if not _CORPUS_ROOT.is_dir():
        return None
    games = [path for path in _CORPUS_ROOT.rglob("*.txt") if path.is_file()]
    if not games:
        return None
    return max(games, key=lambda path: (path.stat().st_size, str(path)))


class _WrapPinGame(NamedTuple):
    """One game for B15.6, with the regime it is there to put the map in.

    ``rows`` is the hand-derived row count for the synthetic games, and ``None``
    for the discovered real one — see `_largest_committed_transcript` on why a
    golden count would be the wrong assertion there.
    """

    text: str
    width: int
    rows: int | None


_WRAP_PIN_REAL_GAME = _largest_committed_transcript()

# The three games, one item each. `fits` first because it is the regime the
# obvious assertion gets wrong (see the `virtual_size` test below), `overflow`
# second because it is the regime every long game is actually read in, and
# `real` last because it is the only one whose text nobody here wrote — 88 KB of
# em-dashes, quotation marks and whatever else a model produced, which is the
# case a synthetic fixture cannot stand in for.
_WRAP_PIN_PARAMS = [
    pytest.param(
        _WrapPinGame(
            _WRAP_PIN_FITTING_BODY, _WRAP_PIN_WIDTH_FITTING, _WRAP_PIN_FITTING_ROWS
        ),
        id="fits",
    ),
    pytest.param(
        _WrapPinGame(
            _WRAP_PIN_OVERFLOWING_BODY,
            _WRAP_PIN_WIDTH_OVERFLOWING,
            _WRAP_PIN_OVERFLOWING_ROWS,
        ),
        id="overflow",
    ),
    (
        pytest.param(
            _WrapPinGame(
                _WRAP_PIN_REAL_GAME.read_text(encoding="utf-8"),
                _WRAP_PIN_WIDTH_OVERFLOWING,
                None,
            ),
            id="real",
        )
        if _WRAP_PIN_REAL_GAME is not None
        else pytest.param(
            None,
            marks=pytest.mark.skip(
                reason=f"no committed transcripts under {_CORPUS_ROOT}"
            ),
            id="real",
        )
    ),
]



async def _type_transcript_search(pilot, query: str) -> TranscriptSearchInput:
    """Type ``query`` into the open transcript search bar, one real keystroke each.

    **Deliberately NOT ``_drive_search`` (B4), and the difference is the whole
    point of this helper existing.** That one is for
    :class:`LedgerTableScreen`'s runs-list filter — a different surface on a
    different screen, which functional-spec §3 explicitly puts out of this
    spec's scope — and it works by ASSIGNING ``Input.value``. Assignment
    bypasses the key dispatcher entirely, and the key dispatcher is exactly
    what spec 044's Slice 1 is a bet on: whether ``/`` reaches the screen,
    whether ``home`` reaches the game past a focused ``Input``, whether ``q``
    types a character instead of leaving. Driven by assignment, every test
    below would pass against a screen with no bindings at all. So the two
    helpers stay separate and differently named; consolidating them would
    silently delete the coverage.

    Asserts the bar holds focus before typing, because the failure mode it
    guards is quiet: with the scroller focused instead, ``q`` would pop the
    screen and the assertion that follows would be about a widget on a screen
    the reviewer has already left.

    Returns the input so a caller can read ``value`` back — the query as the
    key path actually built it, never as the test asked for it.
    """
    search_input = pilot.app.screen.query_one(TranscriptSearchInput)
    assert pilot.app.screen.focused is search_input, (
        "the search bar must hold focus before typing into it; focus is on "
        f"{type(pilot.app.screen.focused).__name__}"
    )
    await pilot.press(*query)
    await pilot.pause()
    return search_input


async def _settle_search(pilot) -> None:
    """Let the search debounce expire, so the highlight is the one just typed.

    Every B15.2 read goes through this first. The screen does not repaint on
    the keystroke — it restarts a single-shot timer and repaints once the
    typing stops (tech-spec §2.8: the worst measured query costs 336 ms per
    keystroke on the 88 KB game without the coalescing) — so a test that
    asserted straight after ``pilot.press(...)`` would be measuring the query
    the reviewer typed one character ago. Which passes, on a fast enough
    machine, often enough to be trusted.

    Two waits, and they do different jobs: the first is the debounce window
    itself (:data:`_SEARCH_SETTLE_SECONDS`, derived from the viewer's own
    constant), the second lets the repaint the expiring timer posts actually
    run on the event loop before anything reads the widget.

    Kept separate from :func:`_type_transcript_search` rather than folded into
    it, because the two are not always paired: Backspace is pressed with
    ``pilot.press`` directly and still has to settle, and B15.1's tests type
    without ever reading a highlight and should not pay the wait.
    """
    await pilot.pause(_SEARCH_SETTLE_SECONDS)
    await pilot.pause()


async def _settle_scroll(pilot) -> None:
    """Let a scroll ANIMATION land before an absolute row is read.

    **Every one of the six scroll keys animates**, which is easy to assume is
    true only of `home`/`end` and is not: each of the scroller's actions calls
    its ``scroll_*`` method at Textual's default ``animate=True``.
    ``scroll_home``/``scroll_end`` pin ``duration = 1.0`` whatever the distance;
    the other four fall through to ``speed=50`` rows a second, so a page key
    travels its 21 rows over 0.42 s and a line key its single row over 0.02 s.
    The row a test wants to assert on therefore does not exist at the moment the
    key is released — the scroller is somewhere between where it was and where
    it is going — and that is Textual behaving correctly, not a bug to fix in
    the bindings: the reviewer sees a smooth scroll and should.

    That the assertions pass at all today is an accident of two Textual
    internals lining up. ``App._press_keys`` awaits
    ``animator.wait_until_complete()`` after every key, and ``pilot.pause()``
    will sit inside ``wait_for_idle`` for up to a second while the process looks
    busy. But the animation is not registered with the animator by the keypress:
    ``scroll_to`` defers ``_scroll_to`` through ``call_after_refresh``, and that
    callback waits on the SCREEN — ``Screen._on_idle`` runs deferred callbacks
    inline only when nothing needs a repaint, and otherwise hands them to the
    screen update timer. Lose that race, which concurrent load is enough to do,
    and ``wait_until_complete`` samples an animator holding nothing, the pause
    finds an idle process and returns in ~20 ms, and the assertion reads a row
    the scroll has not reached yet. `home` fails first because a full second of
    travel out of the middle of the game is the widest gap there is between
    where the scroller is and where the test says it should be.

    So: ``pause()`` first — its trailing ``screen._on_timer_update()`` is what
    gets the deferred callback moving — and then
    ``pilot.wait_for_scheduled_animations()``, whose own ``_wait_for_screen``
    drives the screen through a dispatch (flushing that callback, which starts
    the animation and clears the animator's completion event) before it blocks
    on that event. **Event, not clock:** nothing here sleeps for a guessed
    duration, so a key whose animation is already over costs only the pause it
    was paying anyway, and a slower machine waits longer by itself.

    Applied to all six keys rather than to `home`/`end` alone, because all six
    animate; settling only the two slowest would be aimed at which one happened
    to be caught rather than at what is actually racing.
    """
    await pilot.pause()
    await pilot.wait_for_scheduled_animations()


def _search_runs(content: Content) -> list[tuple[int, int, str]]:
    """The SEARCH's spans alone — the tail `_rendered_runs` grows while one is live.

    Functional-spec §2.3 asks that a highlight sit "on top of, not instead of"
    the game's own colours, and the implementation makes that true by
    CONSTRUCTION: ``TranscriptBody.highlight`` copies the base ``Text`` — which
    already carries spec 038's kind spans — and ``Text.stylize`` APPENDS, so a
    highlighted body's spans are always the tokenizer's runs followed by one
    span per match, in that order.

    Which is why this helper can exist at all, and why it asserts rather than
    merely slicing: the split point is only meaningful if the leading slice
    really is the tokenizer's runs untouched, so every caller gets that
    ordering checked for free and a caller reading "the matches" can never be
    handed a kind run that drifted into the tail. The dedicated ordering test
    below states the same property in full and by hand, so the two are not
    circular — this one spreads the guarantee across the section.

    Returns ``(start, end, text)`` triples like :func:`_rendered_runs`, so a
    caller can assert on the matched text and on where it sits in one read.
    """
    base = _tokenizer_styled_runs(content.plain)
    runs = _rendered_runs(content)
    assert runs[: len(base)] == base, (
        "the rendered runs must OPEN with the tokenizer's runs, unchanged and "
        "at their exact offsets — functional-spec §2.3's 'on top of, not "
        "instead of' is exactly that ordering; a match span that landed among "
        "them, or a kind span the highlight replaced, shows up here"
    )
    return runs[len(base) :]


# ---------------------------------------------------------------------------
# B15.1 The bar opens and closes; the reading view underneath is untouched
# ---------------------------------------------------------------------------


def test_the_footer_advertises_search_beside_back() -> None:
    """Every key this feature DECLARES for the Footer (§2.1 AC1, §2.6's new AC).

    Terminal-free and about the DECLARATION: which bindings ask to be shown, and
    under what description. B15.8's `test_the_footer_names_the_stepping_keys`
    asks the other question — what the Footer actually renders — and the two are
    not the same question, which is the whole of why Slice 6 exists. This one
    costs none of the ~0.6 s a ``run_test()`` item costs, so it stays.

    Stated as the COMPLETE shown set rather than as "slash is in it", and the
    completeness now carries three things at once:

    - §2.1 AC1 — "Search" is declared beside the existing "Back";
    - §2.6's amended AC — `n` and `N` are declared too, each with a description
      rather than the blank Slice 4 left. **The description is the load-bearing
      half**: ``Binding.make_bindings`` normalises through
      ``show=bool(description and show)``, so a binding with an empty
      description is forced hidden no matter what ``show`` says — which is
      exactly how Slice 4 produced two keys the reviewer could not find;
    - and that Slice 6's `enter` did NOT ask for a cell. ``Footer.compose``
      groups by action and renders only the first binding of each group, so a
      shown `enter`/`next_match` entry would either be dropped as a duplicate of
      `n` or — declared above it — REPLACE `n`'s cell and silently undo the
      criterion above. Its absence here is the assertion that it cannot.

    **This test previously said the opposite**, and the sentence is kept as
    history rather than deleted: "`q` stays hidden as it always has been, and
    Slice 4's `n`/`N` must join it there — if a later slice advertises them,
    this fails rather than quietly widening a Footer that §2.10 asks to stay
    recognisable." A later slice did advertise them, on the author's direction,
    because the status line that was supposed to announce them instead was never
    built. The guard did its job: it failed, and this is the deliberate answer.
    `q` is still hidden.
    """
    shown = {
        binding.key: binding.description
        for binding in TranscriptScreen.BINDINGS
        if binding.show
    }
    assert shown == {
        "escape": "Back",
        "backspace": "Back",
        "slash": "Search",
        "n": "Next match",
        "N": "Prev match",
    }


async def test_the_closed_search_row_costs_the_reading_view_nothing(
    tmp_path: Path,
) -> None:
    """A reviewer who never presses `/` gets the same rows of game as before.

    Functional-spec §2.10 AC1/AC2. The bar is mounted in ``compose`` on every
    game, so "it costs nothing" is a claim about a widget that really is in the
    tree, and two assertions carry it that are NOT interchangeable: ``(0, 0)``
    says the bar is not on screen, while the SCROLLER's 22 rows say it is not
    costing the reviewer a line of game. Only the second one separates
    `display: none` from `visibility: hidden` — mutation-tested, swapping the
    rule keeps `#search-row` at ``(0, 0)`` (an invisible widget has no region
    at all) and takes a row off the scroller, so the bar's own size is blind to
    exactly the regression §2.10 AC2 is about.

    The reading view's geometry is re-pinned here rather than left to
    `test_scrolling_a_long_game_behaves_as_before`, because the two failures
    mean different things: that test would fail if the bar stole a row, this
    one says WHICH widget did it. Both use the same symbolic constants, so a
    fixture change moves them together.
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="s44-closed.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        search_row = screen.query_one("#search-row", Horizontal)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", Static)

        # The bar is there, and it occupies nothing.
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert "-open" not in search_row.classes

        # ...so the game keeps every row and every column it had.
        assert screen.focused is scroller
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.virtual_size.height == _LONG_GAME_LINES
        assert scroller.max_scroll_y == _LONG_GAME_MAX_SCROLL_Y
        assert body.content_size.width == _LONG_GAME_BODY_WIDTH


async def test_slash_opens_the_bar_focuses_it_and_leaves_the_game_where_it_was(
    tmp_path: Path,
) -> None:
    """One keypress opens the search, and the text does not move (§2.1 AC2/AC3/AC4).

    The reviewer is scrolled into the MIDDLE of the game first, because that is
    where §2.1 AC3's promise is falsifiable: opening a bar that re-laid out the
    scroller, or that focused a widget by re-running ``AUTO_FOCUS``, would jump
    the view to the top and pass any assertion written from row 0.

    ``body.content_size.width`` is the §2.1 AC4 assertion in disguise — "the
    lines they could read before are still readable". A bar that took columns
    rather than a row would re-fold every line of the game, so equality here is
    the statement that the passage the reviewer was mid-way through is still
    the same passage, laid out the same way.

    The one row the bar does cost comes out of the scroller's height, and
    `max_scroll_y` grows by exactly that row: the game did not get longer, the
    window onto it got shorter.
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="s44-open.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        search_row = screen.query_one("#search-row", Horizontal)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", Static)

        # Read into the middle of the game with the ordinary reading keys, so
        # the setup owes nothing to the feature under test.
        await pilot.press(
            "pagedown",
            *["down"] * (_SEARCH_SCROLL_START_Y - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
        assert body.content_size.width == _LONG_GAME_BODY_WIDTH

        await pilot.press("slash")
        await pilot.pause()

        # The bar is open, one row tall, and holds the cursor.
        assert "-open" in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_OPEN_SIZE
        assert screen.focused is screen.query_one(TranscriptSearchInput)

        # The game did not move, and it did not rewrap.
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
        assert body.content_size.width == _LONG_GAME_BODY_WIDTH

        # The row came out of the viewport, not out of the scroll position.
        assert scroller.size.height == _SEARCH_OPEN_VIEWPORT_ROWS
        assert scroller.max_scroll_y == _SEARCH_OPEN_MAX_SCROLL_Y


async def test_the_bars_own_keys_type_into_it_instead_of_leaving_the_game(
    tmp_path: Path,
) -> None:
    """`q`, `n`, `N` and `/` become characters; `backspace` deletes one (§2.4).

    Every one of these is a COLLISION the screen would lose if the focused
    chain resolved the other way, and two of them would lose it destructively:
    `q` and `backspace` are both bound on :class:`TranscriptScreen` to back out
    of the game, so a mid-search press of either would throw the reviewer out
    of the transcript and take the query with it. `/` is the key that opened
    this bar, and `n`/`N` are Slice 4's.

    The absolute stack depth is asserted, not just the screen type: a screen
    that was popped and re-pushed would still be a ``TranscriptScreen``.

    This is §2.4 WORKING, not a bug to be fixed later — the tech spec's §2.4
    key table lists exactly this outcome for all five keys, and the screen's
    ``action_close`` docstring records that with the bar open only `escape`
    ever reaches it.
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="s44-typed.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        await _open_the_only_game(pilot)
        await pilot.press("slash")
        await pilot.pause()

        search_input = await _type_transcript_search(pilot, _TYPED_NOT_ACTED_KEYS)

        assert search_input.value == _TYPED_NOT_ACTED_KEYS
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH

        # ...and backspace edits the query rather than backing out of the game.
        await pilot.press("backspace")
        await pilot.pause()
        assert search_input.value == _TYPED_NOT_ACTED_KEYS[:-1]
        assert isinstance(app.screen, TranscriptScreen)
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH


async def test_escape_with_no_search_leaves_the_game_in_one_press(
    tmp_path: Path,
) -> None:
    """The change costs a reviewer who never searches no extra keypress (§2.9 AC3).

    The regression this guards is specific and would be easy to ship: the Esc
    machine now has three states, and an ``action_close`` that cleared
    something unconditionally before popping — or that branched on the bar
    rather than on the search — would turn the first Esc into a no-op and make
    leaving a game take two presses for everyone.

    Deliberately pressed from a SCROLLED position with the scroll container
    focused, which is where a reviewer actually is when they finish reading.
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="s44-one-esc.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _LONG_GAME_VIEWPORT_ROWS
        assert screen.focused is scroller

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


async def test_escape_closes_the_search_first_and_leaves_the_game_next(
    tmp_path: Path,
) -> None:
    """"The first Esc closes a search; the next one leaves the game" (§2.9).

    The two-press sequence is the test, and asserting only the second press
    would miss the bug entirely: a screen that popped on the FIRST Esc would
    also end up on the :class:`DetailScreen`, just one keypress too early and
    with the reviewer's place in the game gone.

    `scroll_offset.y` is pinned across the close because §2.4 invited the
    reviewer to scroll while searching — so the place they abandon a search
    from is usually not the place they opened it from, and Esc abandons the
    search, not their reading position. Measured from the MIDDLE of the game
    rather than its last row, where Textual's own clamp legitimately moves the
    view by one as the closing bar hands its row back to the viewport.

    Focus returning to the scroller is what makes the game answer arrow keys
    again; without it they would go to a hidden box.
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="s44-two-esc.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        search_row = screen.query_one("#search-row", Horizontal)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press(
            "pagedown",
            *["down"] * (_SEARCH_SCROLL_START_Y - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)
        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, "vote")

        # The premise, stated rather than assumed: there really is an open bar
        # for the first Esc to close. Without this the whole test would still
        # pass against a bar that never opened — the closed-bar assertions
        # below would all be trivially true.
        assert "-open" in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_OPEN_SIZE
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y

        # First Esc: the search goes, the game stays.
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, TranscriptScreen)
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
        assert "-open" not in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert search_input.value == ""
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
        assert screen.focused is scroller
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.max_scroll_y == _LONG_GAME_MAX_SCROLL_Y

        # Second Esc: now they leave.
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


# ---------------------------------------------------------------------------
# B15.2 The matches light up as the reviewer types
# ---------------------------------------------------------------------------


async def test_typing_narrows_the_highlight_and_backspace_widens_it(
    tmp_path: Path,
) -> None:
    """`vot` → `vote` → Backspace, with the game never moving (§2.2 AC1-AC4).

    The three-step sequence is the test. Asserting a single query would prove
    only that something lights up; functional-spec §2.2 AC2/AC3 are about the
    highlighting FOLLOWING the query in both directions, and the two failure
    modes it guards are opposite and both silent. A repaint that stylized the
    base ``Text`` instead of a copy would ACCUMULATE spans — `vote`'s 20
    matches landing on top of `vot`'s 40 rather than replacing them, so the
    narrowing step would still show 40 lit and nothing would ever stop being
    highlighted. A repaint that never re-ran would show 40 throughout and pass
    any test that only ever typed once.

    The counts are arithmetic on the fixture (40 = 2 x 20 = half its 80 lines),
    so they re-derive rather than rot if it grows, and the widening step is the
    same number as the first — Backspace must land exactly back where `vot`
    was, not merely somewhere wider.

    `scroll_offset.y` is re-asserted after every step, from the MIDDLE of the
    game, which is where §2.2 AC4 is falsifiable: there are matches above and
    below the viewport at all three steps, so an implementation that scrolled
    to "show" the first match would move here and could not move in a test
    written from row 0. The row is absolute (`_SEARCH_SCROLL_START_Y`), not
    "the same as before", so a repaint that reset the view to the top and a
    fixture that never scrolled cannot both pass.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-narrow.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", Static)
        search_count = screen.query_one("#search-count", Static)

        def assert_lit(query: str, matches: int) -> None:
            """Exactly ``matches`` spans, each covering ``query``, and nothing moved.

            Reading the matched text back out of the rendered content (rather
            than counting spans) is what makes this an OFFSET assertion:
            `_search_runs` slices `content.plain` at each span's own
            boundaries, so a span one character wide of its match returns a
            different string and fails here.
            """
            content = _transcript_body_content(screen)
            assert [text for _, _, text in _search_runs(content)] == [
                query
            ] * matches
            assert plain_text(search_count) == f"{matches} matches"
            assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
            assert scroller.max_scroll_y == _SEARCH_OPEN_MAX_SCROLL_Y
            assert body.content_size.width == _LONG_GAME_BODY_WIDTH

        # Read into the middle of the game with the ordinary reading keys, so
        # the setup owes nothing to the feature under test.
        await pilot.press(
            "pagedown",
            *["down"] * (_SEARCH_SCROLL_START_Y - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)
        await pilot.press("slash")
        await pilot.pause()
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y

        # `vot` — every `vote` and every `voting` in the game.
        await _type_transcript_search(pilot, "vot")
        await _settle_search(pilot)
        assert_lit("vot", _SEARCH_VOT_MATCHES)

        # ...one more character, and the `voting` half goes dark.
        await _type_transcript_search(pilot, "e")
        await _settle_search(pilot)
        assert_lit("vote", _SEARCH_VOTE_MATCHES)

        # ...and Backspace brings it back, to exactly where it was.
        await pilot.press("backspace")
        await pilot.pause()
        await _settle_search(pilot)
        assert_lit("vot", _SEARCH_VOT_MATCHES)


async def test_the_match_spans_are_a_tail_on_the_tokenizers_own_runs(
    tmp_path: Path,
) -> None:
    """The rendered runs open with spec 038's and end with the search's (§2.3).

    **This is the checkable form of "on top of, not instead of"**, and the
    tech spec's §4 says so: the ordering is what makes the claim assertable at
    all, rather than a per-kind case analysis nobody could keep current as
    kinds keep arriving (there are twenty-four). Stated here in full and by
    hand — the leading slice against the pure tokenizer's runs, the tail
    against the matches — while `_search_runs` re-checks the leading half at
    every other read in this section. The two are not circular: this test is
    where the property is stated, that helper is where it is enforced.

    Three separate things are asserted and each fails differently:

    - the leading slice IS the tokenizer's runs, so no kind span was replaced,
      shifted or dropped to make room for a highlight;
    - the tail is exactly one span per match, in document order and
      non-overlapping, so the highlight is neither merged nor coalesced (which
      would destroy this assertion and, from Slice 4, the current-match step);
    - `content.plain` is unchanged, so painting a match added no character to
      the preserved game — the same guarantee B14.3 makes of the resting view,
      re-made while a search is live.

    `_HIGHLIGHT_BODY` is the fixture because its every run is already pinned
    above, and `Bo` the query because it lands in six different kinds of it at
    once.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="s44-tail.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        # The reading view as spec 038 leaves it, before a search exists.
        resting = _transcript_body_content(screen)
        kind_runs = _tokenizer_styled_runs(resting.plain)
        assert _rendered_runs(resting) == kind_runs

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _HIGHLIGHT_SEARCH_QUERY)
        await _settle_search(pilot)

        content = _transcript_body_content(screen)
        runs = _rendered_runs(content)

        # Not one character of the game changed...
        assert content.plain == resting.plain
        # ...spec 038's runs are all still there, first and untouched...
        assert runs[: len(kind_runs)] == kind_runs
        # ...and the search's are appended after them, one per match.
        matches = runs[len(kind_runs) :]
        assert len(runs) == len(kind_runs) + _HIGHLIGHT_SEARCH_MATCHES
        assert [text for _, _, text in matches] == [
            _HIGHLIGHT_SEARCH_QUERY
        ] * _HIGHLIGHT_SEARCH_MATCHES

        # In document order and never overlapping — `find_matches`' contract,
        # arriving intact at the widget.
        starts = [start for start, _, _ in matches]
        ends = [end for _, end, _ in matches]
        assert starts == sorted(starts)
        assert all(end <= start for end, start in zip(ends, starts[1:]))


async def test_a_match_takes_a_background_and_leaves_its_lines_style_alone(
    tmp_path: Path,
) -> None:
    """A highlighted Mafia line still reads as a Mafia line (§2.3 AC1/AC2).

    The span-level ordering test above says the kind runs SURVIVE; this one
    says what survives them is what a reviewer actually sees, by combining the
    spans at a single character the way the renderer does
    (`Content.get_style_at_offset` folds every span covering an offset in
    order) and comparing two characters of the SAME run — the `Bo` of
    `Bo: Nor did I.`, which the search matched, and the `:` beside it, which it
    did not.

    Deliberately a within-one-state comparison rather than a before/after one
    (spec 037's mutation finding: "X equals Y across two states" holds happily
    with both sides broken), and the foreground is asserted to be SET as well
    as equal, so a highlight that stripped the Mafia colour from the whole line
    cannot pass by making both characters colourless. No RGB value is pinned —
    B14's convention, and the palette is theme-dependent.

    The six-way sweep is functional-spec §2.3 AC2 ("the reviewer can still tell
    the three apart") stated as strongly as this fixture allows: all six
    matches share ONE background, and all six still render as six DIFFERENT
    combinations of colour, weight, slant and inversion — the cast-list
    attribute is still bold, the thought still italic, the Law-abiding line
    still blue, the recap still inverted. A highlight that replaced its line's
    style rather than sitting on top of it would collapse those six into one.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="s44-ontop.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _HIGHLIGHT_SEARCH_QUERY)
        await _settle_search(pilot)

        content = _transcript_body_content(screen)
        speaker = content.plain.index(_HIGHLIGHT_MAFIA_LINE)
        matched = content.get_style_at_offset(speaker)
        beside = content.get_style_at_offset(speaker + len(_HIGHLIGHT_SEARCH_QUERY))

        # The `:` one character along is the same speaker run, unmatched: the
        # side colour is there, and the ground is the body's own.
        assert beside.foreground is not None
        assert beside.background is None

        # The matched `Bo` keeps that colour exactly, and gains a ground.
        assert matched.foreground == beside.foreground
        assert (matched.bold, matched.italic, matched.reverse) == (
            beside.bold,
            beside.italic,
            beside.reverse,
        )
        assert matched.background is not None

        # ...and across all six kinds the name lands in: one shared highlight,
        # six still-distinguishable renderings.
        lit = [
            content.get_style_at_offset(start)
            for start, _, _ in _search_runs(content)
        ]
        assert len(lit) == _HIGHLIGHT_SEARCH_MATCHES
        assert len({style.background for style in lit}) == 1
        assert (
            len(
                {
                    (style.foreground, style.bold, style.italic, style.reverse)
                    for style in lit
                }
            )
            == _HIGHLIGHT_SEARCH_MATCHES
        )


async def test_a_match_inside_a_moderator_recap_inverts_and_only_there(
    tmp_path: Path,
) -> None:
    """The one kind where the highlight comes out inside-out, and it is expected.

    **A measured consequence of §2.3, written down as a test rather than
    discovered as a bug report.** `recap` is the only kind spec 038 paints with
    `reverse`, and `reverse` is what the terminal uses to SWAP a cell's
    foreground and background. So a match inside the moderator's status line
    arrives with the same amber ground every other match gets and the terminal
    then turns it inside out: the text goes amber on a light ground rather than
    the ground going amber. Measured at ~9.2:1 — clearly legible and clearly
    marked, just inverted relative to everywhere else.

    What is asserted is that the inversion belongs to the RECAP and not to the
    search: the unmatched characters of the same recap run already carry
    `reverse`, the match adds only the background, and that background is the
    very same one a Mafia line's match gets. And the sweep across all six
    matches says it happens in exactly one of them — "the only kind where this
    happens" is the claim, so a future kind that quietly took `reverse` (the
    flag ledger records it as spent, so it should not) fails here.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="s44-recap.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _HIGHLIGHT_SEARCH_QUERY)
        await _settle_search(pilot)

        content = _transcript_body_content(screen)
        recap = content.plain.index(_HIGHLIGHT_RECAP_TEXT)
        matched = content.get_style_at_offset(
            recap + _HIGHLIGHT_RECAP_TEXT.index(_HIGHLIGHT_SEARCH_QUERY)
        )
        beside = content.get_style_at_offset(recap)
        in_mafia_line = content.get_style_at_offset(
            content.plain.index(_HIGHLIGHT_MAFIA_LINE)
        )

        # The flag is the recap's, not the search's: it is already on the
        # characters the search did not match.
        assert beside.reverse is True
        assert beside.background is None
        assert matched.reverse is True

        # The match itself is marked identically to every other match — the
        # swap is the terminal's doing, downstream of anything this code says.
        # Asserted as a colour that EXISTS as well as one that is shared: two
        # matches with no background at all would be "identical" too, and that
        # is precisely what a highlight resolved through `_kind_styles` (which
        # strips backgrounds by design) would leave behind.
        assert matched.background is not None
        assert matched.background == in_mafia_line.background

        # ...and it is the only kind it happens to.
        lit = [
            content.get_style_at_offset(start)
            for start, _, _ in _search_runs(content)
        ]
        assert [bool(style.reverse) for style in lit].count(True) == 1


async def test_the_highlight_background_differs_from_the_body_background(
    tmp_path: Path,
) -> None:
    """A match is actually visible — the one colour assertion worth making.

    The tech spec's §4 rules out exact RGB values, theme sweeps and snapshots
    (a background-only span makes no per-theme contrast claim, and this file
    has asserted span STRUCTURE since B14 for reasons that have not changed),
    and then names this single exception: whatever the theme resolves the rule
    to, the highlight's ground must differ from the ground it sits on, or there
    is nothing to see.

    It guards a failure mode with no traceback and no visible symptom in any
    structural test: `Text.stylize` opens with `if style:`, and a Rich style
    with nothing set is falsy — so a highlight that resolved to no background
    at all would append NO span, silently, and the reviewer would type into a
    box that answers with a match count over a game where nothing lights up.
    The `#search-count` readout would still be right, which is what makes it
    worth an assertion of its own.

    Both halves are stated, because only the pair says "differs": the
    unmatched character carries no background at all — spec 038 strips it from
    every kind style, which is what leaves this axis free — so the ground it
    shows is the body's own, and the matched character's is a colour that is
    neither absent nor that.
    """
    ledger, _ = _ledger_with_body(tmp_path, _HIGHLIGHT_BODY, name="s44-visible.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        body = screen.query_one("#transcript-body", Static)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _HIGHLIGHT_SEARCH_QUERY)
        await _settle_search(pilot)

        content = _transcript_body_content(screen)
        speaker = content.plain.index(_HIGHLIGHT_MAFIA_LINE)
        matched = content.get_style_at_offset(speaker)
        beside = content.get_style_at_offset(speaker + len(_HIGHLIGHT_SEARCH_QUERY))

        # What the body paints behind every character that is not a match.
        ground = body.background_colors[1]

        assert beside.background is None
        assert matched.background is not None
        assert matched.background != ground


async def test_a_query_that_matches_nothing_says_so_and_moves_nothing(
    tmp_path: Path,
) -> None:
    """The no-match state: nothing lit, nothing moved, and it says so (§2.8 AC2).

    Three claims in one criterion, and the interesting one is the middle:
    "nothing is highlighted" is asserted as the rendered runs being EXACTLY the
    tokenizer's — equality, not "no extra spans for this query" — so a stale
    highlight left over from a previous query would fail here too. It is the
    same strict-equality shape the two span tests in B14.2/B14.3 use to catch a
    leaked search span in a freshly opened game, made while a search is
    deliberately live.

    The count is asserted by its literal copy rather than by importing
    `_NO_MATCHES_MESSAGE`, following this file's convention for user-visible
    strings (see `_EMPTY_LEDGER_MESSAGE`): what is pinned is what the reviewer
    reads, not the name the module gives it.

    Read from the middle of the game, because "the game has not moved" is only
    falsifiable somewhere it could have moved from.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-nomatch.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", Static)
        search_count = screen.query_one("#search-count", Static)
        resting = _transcript_body_content(screen).plain

        await pilot.press(
            "pagedown",
            *["down"] * (_SEARCH_SCROLL_START_Y - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)
        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(
            pilot, _SEARCH_NO_MATCH_QUERY
        )
        await _settle_search(pilot)

        content = _transcript_body_content(screen)

        # The reviewer really did ask something...
        assert search_input.value == _SEARCH_NO_MATCH_QUERY
        # ...the strip says plainly that it found nothing...
        assert plain_text(search_count) == "No matches"
        # ...nothing at all is lit, not even the previous query's matches...
        assert _rendered_runs(content) == _tokenizer_styled_runs(content.plain)
        assert _search_runs(content) == []
        # ...and the game is exactly where, and what, it was.
        assert content.plain == resting
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
        assert scroller.max_scroll_y == _SEARCH_OPEN_MAX_SCROLL_Y
        assert body.content_size.width == _LONG_GAME_BODY_WIDTH


async def test_the_game_is_tokenized_once_per_opening_not_once_per_keystroke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base `Text` is built at `compose` and reused — a structural guard.

    Tech-spec §4 rules out wall-clock assertions and asks for the structural
    guards instead; this is the one that pins the shape the whole debounce
    rests on. `TranscriptScreen._styled_body` re-reads the file and re-runs the
    pure tokenizer — ~2.8 ms on the 88 KB committed game, nothing at all once
    and an absurdity per character typed — so `TranscriptBody` is handed that
    base ONCE and copies it per update.

    A regression here has no visible symptom and no failing assertion anywhere
    else: the highlighting would be correct, the spans would be correct, and
    the viewer would simply get slower with every keystroke on exactly the
    biggest games a reviewer most wants to search. Counting the call is a
    statement with a stable failure mode, where timing it would be a flake.

    Patched at the UI module's own call site, the idiom B14 already uses for
    the tokenizer. `_tokenizer_styled_runs` calls the name this test module
    imported from `graphia.eval_ledger` instead, so the assertions below do not
    count themselves.
    """
    module = importlib.import_module("graphia.ui.ledger_viewer")
    tokenize = module.tokenize_transcript
    calls: list[str] = []

    def _counting_tokenize(text: str) -> list[tuple[str, str]]:
        calls.append(text)
        return tokenize(text)

    monkeypatch.setattr(module, "tokenize_transcript", _counting_tokenize)

    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-tokenize-once.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        # Once, at `compose`, for the game that was opened.
        assert len(calls) == 1

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, "vote")
        await _settle_search(pilot)
        await pilot.press("backspace")
        await pilot.pause()
        await _settle_search(pilot)

        # Five keystrokes and two repaints later: still once.
        assert len(calls) == 1
        # ...and the search really did repaint, so the count above is not the
        # count of a feature that quietly did nothing.
        assert len(_search_runs(_transcript_body_content(screen))) == (
            _SEARCH_VOT_MATCHES
        )


# ---------------------------------------------------------------------------
# B15.3 The game still scrolls while the reviewer is typing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "expected_y"), _SEARCH_SCROLL_KEY_CASES)
async def test_each_scroll_key_moves_the_game_and_leaves_the_query_alone(
    tmp_path: Path, key: str, expected_y: int
) -> None:
    """All six scroll keys reach the game past the focused search bar (§2.4).

    Swept one key per item rather than pressed in sequence, so a failure names
    the key. The distinction matters because the six are dead for two DIFFERENT
    reasons without :class:`TranscriptSearchInput`'s bindings: ``home`` and
    ``end`` are claimed by ``Input.BINDINGS`` and would be swallowed by the
    focused box, while ``up``/``down``/``pageup``/``pagedown`` are not bound by
    ``Input`` at all but have nothing up the ancestor chain to catch them,
    because the bar is the scroller's SIBLING rather than its child. A test
    that pressed all six together would pass on four and be silent about which.

    The landing row is absolute, not "moved by something": the page keys move
    by 21 rows and not 22, which is the open bar's row showing up in the
    arithmetic, and `end` lands on the open ``max_scroll_y`` — one row further
    than the reading view can reach.

    ``value`` unchanged is the other half of §2.4, and it is not a formality:
    each of these keys arrives at a focused text box, and an implementation
    that handled the scroll without stopping the key would also insert a
    character or move the cursor.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _LONG_GAME_BODY, name=f"s44-scroll-{key}.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        # Read into the middle of the game, then open the bar and type.
        await pilot.press(
            "pagedown",
            *["down"] * (_SEARCH_SCROLL_START_Y - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)
        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, "vote")

        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
        assert scroller.size.height == _SEARCH_OPEN_VIEWPORT_ROWS
        assert scroller.max_scroll_y == _SEARCH_OPEN_MAX_SCROLL_Y

        await pilot.press(key)
        await _settle_scroll(pilot)

        assert scroller.scroll_offset.y == expected_y
        assert search_input.value == "vote"
        assert screen.focused is search_input


async def test_typing_resumes_after_a_scroll_and_the_query_keeps_growing(
    tmp_path: Path,
) -> None:
    """Scrolling mid-search costs the reviewer nothing they have typed (§2.4 AC5).

    The sequence is the point: type part of a word, scroll a screenful, then
    type the rest. An implementation that restored focus to the scroller after
    handling the scroll — a plausible-looking way to make the keys "reach the
    game" — would pass every test above and fail here, because the next
    character would go to the game instead of the query. (It would also make
    `e` a no-op and `s` scroll nothing, so the failure would be silent in the
    app and loud only here.)
    """
    ledger, _ = _ledger_with_body(tmp_path, _LONG_GAME_BODY, name="s44-resume.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, "vot")
        assert search_input.value == "vot"
        assert scroller.scroll_offset.y == 0

        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _SEARCH_OPEN_VIEWPORT_ROWS
        assert search_input.value == "vot"

        await _type_transcript_search(pilot, "e")

        assert search_input.value == "vote"
        assert scroller.scroll_offset.y == _SEARCH_OPEN_VIEWPORT_ROWS


async def test_the_scroll_keys_in_a_game_that_fits_do_nothing_and_raise_nothing(
    tmp_path: Path,
) -> None:
    """A game with nothing to scroll must not crash the six keys (§2.4, §2.10 AC1).

    **This one guards a measured crash, not a hypothetical.** All six of the
    scroller's actions begin with ``if not self.allow_vertical_scroll: raise
    SkipAction()``, and ``allow_vertical_scroll`` is false for exactly this
    fixture — a game shorter than the viewport, which plenty of preserved games
    are. Textual's own action dispatcher catches ``SkipAction`` and lets the
    key bubble; the screen's ``ScrollGame`` handler calls the action DIRECTLY,
    from inside a message handler, where an uncaught raise reaches the app's
    unhandled-exception screen. Before the handler caught it, `down` in the
    search bar of a game like this one crashed the viewer.

    Pressed as one sequence rather than swept per key, because the claim is
    about all six together: none of them raises, none of them eats the query,
    and none of them moves a view that has nowhere to move.

    The premise is asserted first and asserted again with the bar OPEN, since
    opening it shortens the viewport by a row and a fixture that only just fit
    would start scrolling and quietly stop testing anything.
    """
    ledger, _ = _ledger_with_body(tmp_path, _SHORT_GAME_BODY, name="s44-short.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        assert scroller.max_scroll_y == 0
        assert scroller.allow_vertical_scroll is False

        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, "vote")
        assert scroller.allow_vertical_scroll is False

        await pilot.press(*(key for key, _ in _SEARCH_SCROLL_KEY_CASES))
        await pilot.pause()

        assert app.is_running
        assert isinstance(app.screen, TranscriptScreen)
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
        assert scroller.scroll_offset.y == 0
        assert search_input.value == "vote"
        assert screen.focused is search_input


# ---------------------------------------------------------------------------
# B15.4 Enter puts the first match at the top of the screen
# ---------------------------------------------------------------------------
#
# WHAT THIS SUB-SECTION ASKS, and it is a third question after B15.1's "where
# did the key land" and B15.2's "what lit up": WHERE DID THE GAME COME TO REST.
# Every assertion below is therefore an ABSOLUTE ROW — never "it moved", never
# "it is further down than it was" — because a jump that goes to the wrong
# place still moves, and a relative assertion holds happily with both sides
# wrong (spec 037's mutation finding, the same reason B15.1 pins the closed bar
# absolutely). The fixtures exist so those absolute rows can be written down:
# every game here has its matches on rows the test names.
#
# THE LANDING ROW IS READ BACK OFF THE RENDER, not only off the scroller.
# `scroll_offset.y == 30` says the view is where the arithmetic wanted it;
# `_JUMP_QUERY in body.render_line(30).text` says row 30 is the row that holds
# the match. The two fail for different reasons and only the second one can see
# an offset -> row map that is internally consistent and wrong — which is
# exactly the failure B15.6 exists to make loud, met here from the other side.
#
# ENTER IS OFFSET-KEYED AND INCLUSIVE. Slice 4's `n`/`N` will be row-keyed and
# EXCLUSIVE, and §2.5 AC2 ("a match already on the top line is where Enter
# lands") is the entire distinction between them. It is not a detail that can
# be smoothed away later without a test noticing, which is what
# `test_enter_stops_on_a_match_that_is_already_on_the_top_row` is for: its
# fixture carries a further match at row 45 purely so that an exclusive Enter
# has somewhere visibly wrong to go.
#
# WHAT IS NOT ASSERTED HERE, AND WHY IT IS NOT A HALF-TESTED CRITERION.
# Functional-spec §2.5 AC3 asks for two things when a jump wraps: that the view
# goes back to the first match, AND that the reviewer is TOLD it wrapped so a
# backwards jump is never silent. The telling has a channel — `Screen.sub_title`
# — which Slice 4 Task 4.5 built after this sub-section was written. So the wrap
# test below asserts the JUMP, and the MESSAGE is asserted one sub-section down
# by `test_a_wrapped_enter_announces_the_wrap_in_the_header` (B15.5), where the
# header is read live off the `Header` widget. Nothing here reads that channel:
# these tests are about where the game came to rest.


async def test_enter_lands_the_first_match_below_the_reviewer_on_the_top_row(
    tmp_path: Path,
) -> None:
    """The jump, from a position with matches on both sides of it (§2.5 AC1).

    **Read into the middle of the game first, and that is the test.** From row
    0 an implementation that scrolled to the first match in the GAME and one
    that scrolled to the first match BELOW THE REVIEWER give the same answer,
    so a jump written from the top of a game proves only that something moved.
    Started from row 22 with a match at row 10 behind it, the two answers are
    10 and 30 and only one of them passes.

    Row 45 is behind the assertion too, in the other direction: an off-by-one
    that resolved to the match *after* the right one lands there. So the single
    equality below is fenced on both sides rather than merely satisfied.

    The landing is then read back off the RENDER — the top row must actually
    contain the word — which is the assertion that survives an offset -> row
    map that is self-consistent and wrong. `scroll_offset.y` alone cannot see
    that; it would report exactly the row the map asked for.

    Typing is checked not to scroll on the way through (§2.2 AC4 again, from
    the one position where it is falsifiable — there are matches above and
    below the viewport), so the move that follows is Enter's and nothing
    else's.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-jump-below.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        # The premise the fixture is built on: a match above where the reviewer
        # starts, the answer below it, and every match clear of the tail so the
        # clamp cannot be what produces the landing.
        assert _JUMP_MATCH_ROWS[0] < _JUMP_START_ROW < _JUMP_MATCH_ROWS[1]
        assert _JUMP_MATCH_ROWS[-1] < _SEARCH_OPEN_MAX_SCROLL_Y

        # Read into the game with the ordinary reading keys, bar still closed.
        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _JUMP_START_ROW

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        # Typing found matches above and below without moving the game.
        assert len(_search_runs(_transcript_body_content(screen))) == _JUMP_MATCHES
        assert scroller.scroll_offset.y == _JUMP_START_ROW

        await pilot.press("enter")
        await pilot.pause()

        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[1], (
            "Enter must land the first match BELOW the reviewer on the top "
            f"row. Row {_JUMP_MATCH_ROWS[0]} means it jumped to the first "
            "match in the game and ignored where they were reading; row "
            f"{_JUMP_MATCH_ROWS[2]} means it skipped one"
        )
        assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text


async def test_enter_stops_on_a_match_that_is_already_on_the_top_row(
    tmp_path: Path,
) -> None:
    """Enter is INCLUSIVE, and this is the test that says so (§2.5 AC2).

    "A match already on the top line counts as *at* the current position and
    is where Enter lands — it does not skip ahead." Read as an assertion: from
    a top row that already holds a match, **the game does not move at all**.

    **This is the one criterion that separates Enter from Slice 4's `n`**, and
    the separation is deliberate on both sides: Enter resolves inclusive-forward
    from the current top row's offset, while `n` will be row-keyed and
    exclusive because §2.6 requires every press of it to move the view. The
    fixture carries a third match at row 45 for no other purpose than to give
    an exclusive Enter somewhere visibly wrong to go, so flipping the flag
    fails here loudly instead of changing behaviour nobody notices until the
    two keys have quietly become the same key.

    The reviewer is put on the match row by SCROLLING there with the ordinary
    reading keys, not by pressing Enter first. Arriving by the feature under
    test would make this test's premise depend on the test above it, and a
    single broken jump would then fail both while naming neither.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-jump-onit.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        # Scroll so the match itself is the top row, with the reading keys.
        await pilot.press(
            "pagedown",
            *["down"] * (_JUMP_MATCH_ROWS[1] - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)

        # The premise: the reviewer is looking AT a match, and there is another
        # one further down for a wrong answer to land on.
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[1]
        assert _JUMP_MATCH_ROWS[2] < _SEARCH_OPEN_MAX_SCROLL_Y

        # AND THE PREMISE THIS TEST IS BLIND WITHOUT: the match must begin at
        # the FIRST CHARACTER of the top row. `next_match` resolves from
        # `offset_for_row(top row)`, and `inclusive` decides only what happens
        # to a match starting exactly at that offset — a match a few characters
        # into the row is strictly after it and `bisect_left` and `bisect_right`
        # return it alike. With the query mid-line this test therefore passes
        # against an exclusive Enter, which is measured, not hypothetical. It is
        # asserted here rather than left to the fixture's comment because a
        # fixture edit is exactly how the coverage would go quietly.
        assert _JUMP_MATCH_LINE.startswith(_JUMP_QUERY)
        rendered_top = body.render_line(_JUMP_MATCH_ROWS[1]).text.strip()
        assert rendered_top.startswith(_JUMP_QUERY)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        await pilot.press("enter")
        await pilot.pause()

        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[1], (
            "Enter is inclusive (§2.5 AC2): a match already on the top row is "
            f"where it stops, so nothing may move. Landing on "
            f"{_JUMP_MATCH_ROWS[2]} instead means the resolution has become "
            "EXCLUSIVE — which is Slice 4's `n`, and not Enter"
        )
        assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text


async def test_enter_past_every_match_wraps_round_to_the_first(
    tmp_path: Path,
) -> None:
    """Scrolled below the last match, Enter goes back to the first (§2.5 AC3).

    **Half of AC3 is asserted here and half in B15.5, deliberately.** The
    criterion is that the view jumps back to the first match *and* that a
    message tells the reviewer it wrapped. The jump is Slice 3's and is what
    this test pins; the message needs a status channel that outlives the closed
    prompt, which is `Screen.sub_title` and which **Slice 4 Task 4.5 built**.
    `test_a_wrapped_enter_announces_the_wrap_in_the_header` (B15.5) is the
    other half, asserted off the live `Header`. Splitting the criterion this
    way keeps each test about one thing; writing nothing here would have left
    the wrap itself untested for a whole slice.

    Landing on row 10 rather than row 0 is what makes this a WRAP rather than a
    scroll-to-top: the first match is ten rows into the game, so an
    implementation that gave up and went home would fail here. The reviewer is
    taken past every match with `end` — pressed in the open search bar, which
    §2.4 promises still reaches the game — so "past every match" is the furthest
    the game can be scrolled and not merely far enough.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-jump-wrap.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        # All the way to the end of the game, from inside the search bar.
        await pilot.press("end")
        await _settle_scroll(pilot)

        # The premise: there is nothing left below the reviewer to find, and
        # the first match is well clear of the top of the game so a wrap and a
        # scroll-to-top are different answers.
        assert scroller.scroll_offset.y == _SEARCH_OPEN_MAX_SCROLL_Y
        assert _JUMP_MATCH_ROWS[-1] < scroller.scroll_offset.y
        assert _JUMP_MATCH_ROWS[0] > 0

        await pilot.press("enter")
        await pilot.pause()

        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[0], (
            "with every match behind them, Enter wraps to the FIRST match "
            f"(row {_JUMP_MATCH_ROWS[0]}). Row 0 would be a scroll to the top "
            "of the game rather than a wrap to a match"
        )
        assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text


async def test_after_the_jump_the_bar_is_gone_and_every_match_is_still_lit(
    tmp_path: Path,
) -> None:
    """The prompt closes, the highlighting stays, the game is readable (§2.5 AC4).

    Three things end the jump and each is a separate promise. The **bar is
    gone**, so the reviewer has their row of game back and is reading normally.
    The **highlights remain** — all of them, not only the one jumped to —
    because the reviewer jumped in order to read a match *in context*, and the
    others staying lit is how they see what is nearby. And **focus is back on
    the scroller**, which is what makes their arrow keys scroll again (and,
    from Slice 4, what lets `n`/`N` reach the screen at all instead of being
    typed into a focused box).

    **The highlight assertion is the load-bearing one**, because the plausible
    wrong implementation is a single line: routing Enter through
    `_clear_search`, which closes the bar correctly and throws the highlighting
    away with it. Every other assertion here passes against that version. This
    one is the difference between §2.5 AC4 and a bar that merely closes.

    The bar's absence is stated in both of the ways B15.1 established — the
    `-open` class gone and the row measuring `(0, 0)` — and the scroller's
    height is checked back at its full 22, which is the one reading that
    separates a bar that is hidden from a bar that is merely invisible.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-jump-after.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        search_row = screen.query_one("#search-row", Horizontal)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        # The premise: the bar is open, and every match in the game is lit.
        assert "-open" in search_row.classes
        assert [
            text for _, _, text in _search_runs(_transcript_body_content(screen))
        ] == [_JUMP_QUERY] * _JUMP_MATCHES

        await pilot.press("enter")
        await pilot.pause()

        # The prompt is gone, and the row it borrowed is back on the game.
        assert "-open" not in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.max_scroll_y == _LONG_GAME_MAX_SCROLL_Y

        # The reviewer is reading again: their keys move the game.
        assert screen.focused is scroller

        # ...and every match is still amber, not just the one they landed on.
        assert [
            text for _, _, text in _search_runs(_transcript_body_content(screen))
        ] == [_JUMP_QUERY] * _JUMP_MATCHES, (
            "§2.5 AC4: after the jump 'the search prompt is gone and the "
            "matches are still highlighted'. An empty list here is Enter "
            "having been routed through `_clear_search`, which closes the bar "
            "correctly and drops the highlighting the criterion promises"
        )


async def test_a_match_in_the_last_screenful_lands_as_close_to_the_top_as_it_can(
    tmp_path: Path,
) -> None:
    """The end-of-game limit, stated as the landing it produces (§2.5, 4th bullet).

    A game stops scrolling when its last line is on screen, so a match inside
    that final screenful **cannot** reach the top row — there is nothing past
    the end of the game to scroll into view, and `less` has exactly this limit
    for exactly this reason. The criterion asks for "as close to the top as the
    game can scroll", and this test reads that phrase as four assertions rather
    than one, because the single obvious one (`scroll_offset.y ==
    max_scroll_y`) is true of any implementation that over-scrolls by any
    amount at all, Textual clamping the result:

    * the match is genuinely in the tail (row 75 against a `max_scroll_y` of
      60), so the clamp is engaged rather than incidental — this is the premise
      the other three mean nothing without;
    * the game is scrolled as far as it goes, and the last row of the game is
      the last row on screen, which is what "as far as it goes" IS;
    * the match is nonetheless ON SCREEN, which is the whole point of having
      jumped — the limit costs the reviewer the top line, not the match;
    * and the top row does NOT hold the match, which is the honest statement of
      the limit and the assertion that would fail if the view had somehow
      scrolled further than the game allows.

    `max_scroll_y` is read back as `_LONG_GAME_MAX_SCROLL_Y` (60) rather than
    as the 61 the jump clamped against, and the difference is not a mistake:
    the jump scrolls while the bar is still open and hides it afterwards, so
    the scroller gets its row back and re-fits, pulling a reviewer parked on the
    very last row back by one with the same last line still on screen. That is
    the scroller re-fitting itself, documented in `_clear_search`, and asserting
    the settled number is asserting what the reviewer actually ends up looking
    at.

    **What this test can and cannot see, measured rather than assumed, because
    the answer is not the obvious one.** Mutating the jump's clamp two rows
    tight (`max_scroll_y - 2`) fails here, so the landing really is pinned.
    Mutating it LOOSE — `max_scroll_y + 5`, or deleting `min(...)` altogether
    and passing the raw `target_row` — passes, and **no test can be written
    that would fail**: `Widget.scroll_to(animate=False)` assigns through
    `scroll_y` and `scroll_target_y`, and Textual validates *both* with
    `clamp(value, 0, self.max_scroll_y)` (`validate_scroll_y` /
    `validate_scroll_target_y`, Textual 8.2.4). An over-scroll and a clamped
    scroll are therefore the same widget state, reachable by no public reading.
    The production code says this about itself — "`scroll_y` would clamp at
    `max_scroll_y` on its own; saying it here is the difference between a limit
    that is *stated* and one that is merely survived" — and that is an honest
    claim about readability, not about behaviour. So this test pins **the
    reviewer's resting position**, which is the criterion, and leaves the
    redundant expression to the code review that keeps it.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_TAIL_BODY, name="s44-jump-tail.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        # The premise, and without it every assertion below is vacuous: the
        # match is past the furthest the game can be scrolled, in both the
        # bar-open and the bar-closed geometry.
        assert _JUMP_TAIL_MATCH_ROW > _SEARCH_OPEN_MAX_SCROLL_Y
        assert _JUMP_TAIL_MATCH_ROW > _LONG_GAME_MAX_SCROLL_Y
        assert _JUMP_QUERY in body.render_line(_JUMP_TAIL_MATCH_ROW).text

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        assert scroller.scroll_offset.y == 0

        await pilot.press("enter")
        await pilot.pause()

        # As far as the game goes, and no further.
        assert scroller.scroll_offset.y == _LONG_GAME_MAX_SCROLL_Y
        assert scroller.scroll_offset.y == scroller.max_scroll_y
        # ...which is exactly "the last row of the game is the last row on
        # screen" — the reason there is nothing left to scroll.
        assert (
            scroller.scroll_offset.y + _LONG_GAME_VIEWPORT_ROWS == _LONG_GAME_LINES
        )

        # The match is on screen, which is what the jump was for...
        assert (
            scroller.scroll_offset.y
            <= _JUMP_TAIL_MATCH_ROW
            < scroller.scroll_offset.y + _LONG_GAME_VIEWPORT_ROWS
        )
        # ...but not on the top row, which is the limit being stated rather
        # than merely survived.
        assert _JUMP_TAIL_MATCH_ROW != scroller.scroll_offset.y
        assert _JUMP_QUERY not in body.render_line(scroller.scroll_offset.y).text


async def test_the_jump_wraps_at_the_width_the_body_is_really_rendered_at(
    tmp_path: Path,
) -> None:
    """A game that WRAPS, at a terminal that is not 100 columns (§2.5 AC1).

    **Every other test in B15.4 would pass against a hardcoded wrap width**,
    and that is not an oversight in them — their games are short-lined so that
    a row is a line, which is what makes their landings readable as integers.
    It does mean none of them can see the hazard the tech spec ranks highest:
    `body.content_size.width` is **96 when a game overflows the viewport and 98
    when it fits**, and neither at a terminal of another size, so a map built
    at a remembered or assumed width mis-folds every line of a real transcript
    and the jump lands somewhere plausible and wrong for the rest of the
    program's life.

    This game is read at 80 columns, where the body wraps at 76 and its
    176-188 character lines take three rows each instead of two. The single
    match therefore sits on row 16 as rendered and on row 11 under a
    width-96 map — and row 11 holds ordinary filler, so the render read at the
    end is what fails when the width is wrong, not merely an integer
    comparison.

    **The disagreement between the two widths is asserted first**, before the
    landing, because it is the test's premise: a fixture edit that made 76 and
    96 fold this game the same way would leave every assertion below true and
    proving nothing, and this is the line that says so.

    Only the overflowing regime can be reached this way, and the reason is
    worth writing down rather than rediscovering: a game that FITS the viewport
    has `max_scroll_y == 0`, so Enter has nowhere to move it and no landing
    assertion can exist. The width-98 path is pinned by B15.6 instead, which
    compares the map against the render and needs no scroll at all.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_WRAP_BODY, name="s44-jump-wrap-w.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_JUMP_WRAP_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        # The regime: a narrower terminal than the rest of this file uses, and
        # a game whose lines really do fold.
        assert body.content_size.width == _JUMP_WRAP_BODY_WIDTH
        rendered = wrap_row_starts(body.text, body.content_size.width)
        assert len(rendered) > body.text.count("\n") + 1

        # Where the match is, as rendered, and where a hardcoded 96 would put
        # it. The offset comes from the fixture's own text rather than from
        # `find_matches`, so the two legs of this test share no production code
        # beyond the map itself.
        offset = body.text.index(_JUMP_QUERY)
        landing = row_for_offset(rendered, offset)
        hardcoded = row_for_offset(
            wrap_row_starts(body.text, _LONG_GAME_BODY_WIDTH), offset
        )

        # THE PREMISE. Without this the landing assertion below is true of a
        # hardcoded width too, and the test proves nothing.
        assert landing != hardcoded, (
            f"this game must fold differently at {_JUMP_WRAP_BODY_WIDTH} and "
            f"at {_LONG_GAME_BODY_WIDTH} columns, or a jump built at the wrong "
            "width lands in the right place by luck and this test is vacuous"
        )
        # ...and the landing must be reachable, or the clamp is what produces
        # it rather than the row arithmetic.
        assert landing < scroller.max_scroll_y - 1

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        assert scroller.scroll_offset.y == 0

        await pilot.press("enter")
        await pilot.pause()

        assert scroller.scroll_offset.y == landing
        assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text, (
            f"row {scroller.scroll_offset.y} does not hold the match. The game "
            f"wraps at {body.content_size.width}; a map built at "
            f"{_LONG_GAME_BODY_WIDTH} puts this match on row {hardcoded} "
            "instead, which is what a hardcoded wrap width would produce"
        )


@pytest.mark.parametrize(
    "query",
    ["", _SEARCH_NO_MATCH_QUERY],
    ids=["nothing-typed", "no-match"],
)
async def test_enter_with_nothing_to_jump_to_moves_nothing_and_raises_nothing(
    tmp_path: Path, query: str
) -> None:
    """Enter on an empty query and on a query with no matches (§2.5 AC5, §2.8 AC4).

    Two criteria, one item each, because they are two criteria — but one
    behaviour, and that is the point worth pinning: `find_matches` returns
    ``[]`` for an empty query and `next_match` returns ``None`` for an empty
    list, so "nothing typed" and "typed something this game does not contain"
    are the *absence* of a scroll rather than two branches that could diverge.
    A version that special-cased one of them is what this parametrization would
    catch.

    The reviewer is reading from the middle of the game, so "nothing moves" is
    an absolute row with somewhere to move to in both directions — from row 0
    an implementation that scrolled home would pass.

    `app.is_running` and the screen-stack depth are the "no error appears" half:
    an exception inside a message handler reaches the app's unhandled-exception
    screen rather than the test's assertions, so the state of the app is what
    has to be read, not the absence of a raise at the call site.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-jump-nothing.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        search_row = screen.query_one("#search-row", Horizontal)

        await pilot.press(
            "pagedown",
            *["down"] * (_SEARCH_SCROLL_START_Y - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y

        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, query)
        await _settle_search(pilot)
        assert search_input.value == query

        # Nothing is lit, whichever of the two reasons applies.
        assert _search_runs(_transcript_body_content(screen)) == []

        await pilot.press("enter")
        await pilot.pause()

        # Nothing moved...
        assert scroller.scroll_offset.y == _SEARCH_SCROLL_START_Y
        # ...the prompt simply closed...
        assert "-open" not in search_row.classes
        assert screen.focused is scroller
        # ...and nothing blew up on the way.
        assert app.is_running
        assert isinstance(app.screen, TranscriptScreen)
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH


# ---------------------------------------------------------------------------
# B15.5 `n` and `N` walk the matches, and the header says where they landed
# ---------------------------------------------------------------------------
#
# THE FOURTH QUESTION IN THIS SECTION, after "where did the key land" (B15.1),
# "what lit up" (B15.2) and "where did the game come to rest" (B15.4): WHAT DOES
# THE REVIEWER KNOW NOW. Every test below reads at least one of the two channels
# that answer it — the live `Header`, and which of the highlighted matches
# carries the stronger marker — because for a whole class of presses those two
# are the ONLY answer there is.
#
# THAT CLASS IS NOT AN EDGE CASE, AND THIS IS THE SUB-SECTION'S CENTRAL FACT.
# A game stops scrolling when its last line is on screen, so `scroll_offset.y`
# CANNOT express any position inside the final screenful. Every match in there
# comes to rest at the same clamped row, and successive presses move the view by
# nothing whatever. A test that asserted only where the game came to rest would
# read "1160, 1160, 1160, 1160" and call four presses correct — which is exactly
# what happened: Task 4.4a found `n` returning the SAME match four times in a
# row, with nine matches unreachable and the wrap unfirable, after Slice 4's
# first round of probes had all passed. They passed because they all used the
# query `vote`, whose last match on the largest committed game sits on row 1030,
# comfortably below the clamp at 1160. **`vote` cannot witness the clamp or the
# wrap.** `the` on that same game has 702 matches with the last on row 1178, and
# is what the three tail tests below carry.
#
# SO EVERY TAIL TEST ASSERTS ITS PREMISE FIRST: the last match's row is past
# `max_scroll_y`. Not as documentation — as the line that fails when a curated
# corpus, a different terminal size or a fixture edit quietly moves the game out
# of the regime the test exists to cover. A test that can only pass by being
# blind is worse than no test, and this sub-section's history is why.
#
# GOLDEN COUNTS ARE DERIVED, NEVER WRITTEN DOWN (tech-spec §4). 702, 1178 and
# 1160 appear in these comments and in none of the assertions: the corpus gains
# a run directory on every measured eval run and loses one whenever a smoke run
# is curated away, so the largest committed game is DISCOVERED
# (`_largest_committed_transcript`, shared with B15.6) and every number is read
# back off the widget or computed from the match list. The header strings are
# built the same way — `_expected_status(query, index + 1, total)` against a
# `total` that came out of `find_matches` — so the FORMAT is pinned exactly
# while the count is free to change.
#
# THAT BUILDER IS ONE FUNCTION AS OF SLICE 6, and the reason is a measurement
# rather than tidiness: the status grew a field (the key hint, §2.8), and the
# twelve hand-built f-strings in this sub-section became twelve identical edits
# in ten tests that had nothing to say about that field. Twelve chances to
# weaken one into a `startswith` while getting the suite green again. Strict
# equality is unchanged at every call site; only the spelling moved.
#
# THE HEADER IS READ OFF THE WIDGET, NOT OFF THE ATTRIBUTE. `Screen.sub_title`
# is a `Reactive` and the `Header` watches it; asserting the reactive alone
# would pass against a header that had stopped rendering, stopped being
# composed, or been pushed off the row by a title change. `_rendered_sub_title`
# goes through `HeaderTitle.render_line(0)` and checks the two agree, so every
# status assertion below is a statement about what a reviewer can actually read.
#
# WHICH MATCH IS CURRENT IS READ STRUCTURALLY, never as an RGB value — B14's
# convention, restated by tech-spec §4 for this slice ("the current match's
# background differs from the others'", and no theme sweep). `_current_match`
# folds the spans at each match's first character the way the renderer does and
# asks which one is the odd one out.
#
# WHAT IS DELIBERATELY NOT HERE: the Esc state machine (Slice 5's Task 5.4,
# which parametrizes `(key, search_open)` over all three back-out keys). The one
# Esc pressed below is there for Task 4.5's other half — "restore `SUB_TITLE`
# when the search clears" — and is asserted as a header fact, not as a back-out
# fact.


# The resting sub-title, pinned BY VALUE as well as by attribute. Reading it off
# `TranscriptScreen.SUB_TITLE` alone would let a rewrite of the copy pass while
# claiming the header "goes back to the hint": the whole promise of §2.10 here
# is that a reviewer who has finished searching sees the screen they started
# with, and that is a string, not an attribute name.
_TRANSCRIPT_SUB_TITLE = "game transcript · Esc / Backspace to go back"

# What `App.format_title` puts between the viewer's name and the sub-title on the
# installed Textual 8.2.4. Split on rather than assumed away, so that a status
# string which happened to contain the separator would fail loudly here instead
# of silently truncating the position the reviewer is owed. (It is also why
# `_search_status_text` uses `·` internally rather than a second em dash.)
_HEADER_TITLE_SEPARATOR = " — "

# The two fields Slice 6 appended to the status, naming the keys that step
# between matches (functional-spec §2.8's AC added 2026-09-14).
#
# WRITTEN OUT AS A LITERAL, NOT IMPORTED from
# `graphia.ui.ledger_viewer._SEARCH_STATUS_KEY_HINT`. Importing it would make
# every status assertion in B15.5 agree with the production string BY
# CONSTRUCTION, so a rewrite of the copy to something that answers only half the
# criterion — `n/N`, which names the keys but not which way each goes — would
# sail through a dozen tests that read as though they pinned it. This module
# already pins user-visible copy this way (`_EMPTY_LEDGER_MESSAGE`,
# `_TRANSCRIPT_SUB_TITLE`), and for the same reason.
#
# Split into its two fields rather than kept as one string, so B15.8 can say
# what each one is: a key name and what that key does.
_SEARCH_STATUS_NEXT_HINT = "n next"
_SEARCH_STATUS_PREV_HINT = "N prev"
_SEARCH_STATUS_KEY_HINT = f"{_SEARCH_STATUS_NEXT_HINT} · {_SEARCH_STATUS_PREV_HINT}"

# THE ROW-MATE GAME (criterion (b)). `_JUMP_BODY`'s exact shape, with the match
# on row 30 replaced by a line carrying the query TWICE. Everything B15.4's
# fixture block establishes therefore still holds — 82 rows, a 22-row viewport,
# `max_scroll_y` 60 closed and 61 open, no line long enough to wrap at 96
# columns, so a row is a line and a landing is an integer — and exactly one
# thing is different: row 30 holds two matches instead of one.
#
# That difference is the whole criterion. §2.6: "Given two matches fall on the
# same line of the screen and the reviewer is on the first of them, when they
# press `n`, then the game moves on to the next line that has a match — the view
# always moves." With matches at rows 10, 30, 30 and 45, an `n` pressed from row
# 30 has a WRONG answer available that looks almost right (its row-mate, index
# 2, which would leave the view exactly where it is) and a right one (index 3 on
# row 45). A step that moved match-by-match passes every other test in this
# sub-section and fails here.
#
# It also produces the running count's accepted jump — 2/4 straight to 4/4 —
# which functional-spec §2.8 states explicitly and which is asserted below
# rather than tolerated, because the plausible "fix" for it (renumbering the
# status to count only reachable matches) would make the total disagree with the
# number of highlights on screen.
_STEP_ROWMATE_LINE = "Cleo: I say we vote for Bo, and Cleo agrees."
_STEP_ROWMATE_ROWS = (10, 30, 45)
_STEP_ROWMATE_CROWDED_ROW = _STEP_ROWMATE_ROWS[1]
_STEP_ROWMATE_BODY = "<transcript>\n" + "\n".join(
    _STEP_ROWMATE_LINE
    if line + 1 == _STEP_ROWMATE_CROWDED_ROW
    else _JUMP_MATCH_LINE
    if line + 1 in _STEP_ROWMATE_ROWS
    else f"Alice: line {line}"
    for line in range(_LONG_GAME_PROSE_LINES)
) + "\n</transcript>"
# Three rows, four matches: one on row 10, TWO on row 30, one on row 45.
_STEP_ROWMATE_MATCHES = len(_STEP_ROWMATE_ROWS) + 1

# THE QUERY THE TAIL TESTS CARRY, and `vote` will not do — see the banner. It is
# lowercase, so smart case leaves it case-INSENSITIVE, which is what makes it
# common enough to have matches inside the final screenful of any real game.
_TAIL_QUERY = "the"

# How many presses the clamped-tail walk takes. Four, because that is the number
# Task 4.4a measured returning the identical match four times over — and because
# four is the fewest that reaches past the first row-mate skip in that tail, so
# one walk witnesses both the advance and the skip.
_TAIL_WALK_PRESSES = 4

_requires_real_game = pytest.mark.skipif(
    _WRAP_PIN_REAL_GAME is None,
    reason=f"no committed transcripts under {_CORPUS_ROOT} — nothing to read",
)


def _rendered_sub_title(screen: TranscriptScreen) -> str:
    """The sub-title AS THE HEADER IS RENDERING IT, not as the reactive holds it.

    Functional-spec §2.8 AC3 and §2.6's two wrap criteria are promises about
    something the reviewer can *read*, and `Screen.sub_title` is one watcher
    away from that. The chain has real links in it: `Header._on_mount` calls
    `self.watch(self.screen, "sub_title", ...)`, the watcher rebuilds through
    `App.format_title`, and `HeaderTitle` is a one-row `Static` with
    `text-overflow: ellipsis` that will silently cut a status too long for the
    terminal. Asserting the reactive would pass against every one of those
    breaking.

    So this reads `HeaderTitle.render_line(0)`, splits the viewer's name off the
    front, and asserts the two agree before returning the half the tests care
    about — which means every caller gets the whole chain checked for free and
    none of them has to spell the title out.

    The title half is asserted too, and not as padding: it is what says the
    status was ADDED to the header rather than having replaced what was there.
    """
    header = screen.query_one(Header)
    rendered = header.query_one("HeaderTitle", Static).render_line(0).text.strip()
    title, separator, status = rendered.partition(_HEADER_TITLE_SEPARATOR)
    assert separator, (
        f"the header reads {rendered!r}, with no {_HEADER_TITLE_SEPARATOR!r} in "
        "it — the sub-title is not reaching the rendered row at all"
    )
    assert title == TranscriptScreen.TITLE, (
        f"the header's title half reads {title!r}; the status must be added "
        f"beside {TranscriptScreen.TITLE!r}, never instead of it"
    )
    assert status == screen.sub_title, (
        f"the header renders {status!r} while `Screen.sub_title` holds "
        f"{screen.sub_title!r} — the reactive is not reaching the widget, so "
        "nothing the reviewer can read is being asserted anywhere"
    )
    return status


def _expected_status(
    query: str, position: int, total: int, *, wrapped: bool = False
) -> str:
    """The WHOLE status a landing must put in the header, spelled out.

    ``position`` is what the reviewer reads — counted from one, as the header
    counts it — so a call site reads the same as the string it is pinning.

    **Centralised in Slice 6, and the reason is the one that just happened.**
    Twelve assertions across ten tests each built this string by hand as
    ``f"{query} · {i}/{total}"``. Appending one field to the status therefore
    broke ten tests that had nothing to say about that field, and the ten
    identical edits are ten chances to quietly weaken one of them into a
    ``startswith`` while "fixing the build". One function is one edit, and the
    next field the status grows costs one line here.

    **This is not a relaxation, and it must not become one.** Every caller still
    asserts STRICT EQUALITY against a fully spelled-out string; what moved is
    where the string is spelled. The key hint is a literal in this module
    (:data:`_SEARCH_STATUS_KEY_HINT`) rather than an import from the viewer, so
    the copy is genuinely pinned rather than agreed with — see that constant.

    **Field order is part of the assertion**, not presentation. ``HeaderTitle``
    truncates with an ellipsis at the END, so the last field is the first one a
    narrow terminal spends; the hint goes after ``(wrapped)`` precisely so that
    the position — a §2.8 acceptance criterion, and readable nowhere else once
    the prompt has closed — outlives it. A build that put the hint before the
    position would pass a substring test and fail here, which is the point.

    There is no ``current is None`` form to build: the header holds
    :data:`_TRANSCRIPT_SUB_TITLE` then, and the call sites say so by name.
    """
    status = f"{query} · {position}/{total}"
    if wrapped:
        status = f"{status} (wrapped)"
    return f"{status} · {_SEARCH_STATUS_KEY_HINT}"


def _current_match(content: Content) -> int | None:
    """Which of the search's matches carries the STRONGER marker, or ``None``.

    §2.6: "The match the reviewer last landed on is marked more strongly than
    the others, so they can see which one of the highlighted matches they are
    currently on." Read the way B15.2 reads §2.3 — by folding the spans at a
    single character the way the renderer does (`Content.get_style_at_offset`)
    — and answered STRUCTURALLY: the current match is the one whose background
    differs from every other match's. No RGB value is pinned, which is B14's
    convention and tech-spec §4's instruction for this slice.

    Returning the INDEX rather than asserting a caller-supplied one is what
    makes a failure readable: "the screen says match 692 is current, the test
    expected 693" says what went wrong, where `assert is_current(693)` says only
    that something did.

    ``None`` means no match is marked — every highlight shares one background,
    which is the state before any jump and after an edit drops the landing.

    Requires at least three matches to be unambiguous, and says so: with two,
    each background occurs once and "the odd one out" has no meaning.
    """
    runs = _search_runs(content)
    assert len(runs) >= 3, (
        f"{len(runs)} matches is too few to identify a current one — with two, "
        "both backgrounds occur once and the odd one out is undefined"
    )
    backgrounds = [
        content.get_style_at_offset(start).background for start, _, _ in runs
    ]
    assert all(background is not None for background in backgrounds), (
        "every match must carry a background; one without is a highlight that "
        "resolved to nothing (tech-spec §2.8: a falsy `RichStyle` appends no "
        "span at all) rather than a marker that moved"
    )
    distinct = Counter(backgrounds)
    if len(distinct) == 1:
        return None
    assert len(distinct) == 2, (
        f"the matches carry {len(distinct)} different backgrounds; there are "
        "exactly two styles — `transcript--match` and `transcript--match-current`"
    )
    odd = [background for background, count in distinct.items() if count == 1]
    assert len(odd) == 1, (
        "two backgrounds, but neither occurs exactly once — the stronger style "
        "is painted on more than one match, so 'which one am I on' has no answer"
    )
    return backgrounds.index(odd[0])


def _match_rows(body: TranscriptBody, query: str) -> tuple[
    list[tuple[int, int]], list[int], list[int]
]:
    """``(matches, row starts, the row each match sits on)`` for ``query``.

    Computed off the widget's own text and its own LIVE wrap width — never off
    the bytes on disk and never off a remembered width — for the reason
    `TranscriptScreen._row_starts` reads the width live: it is 96 when a game
    overflows the viewport and 98 when it fits, and neither at a terminal of
    another size.

    This is the tests' independent view of the fixture. It shares
    `wrap_row_starts` and `find_matches` with the screen, which B15.6 and
    `tests/test_transcript_search.py` pin from two other directions; what it
    does NOT share is any of the selection logic under test, so an expectation
    derived from it is a statement about §2.6 rather than a re-run of the code
    that implements it.
    """
    matches = find_matches(body.text, query)
    row_starts = wrap_row_starts(body.text, body.content_size.width)
    rows = [row_for_offset(row_starts, start) for start, _ in matches]
    return matches, row_starts, rows


def _first_match_on_each_row(rows: list[int]) -> dict[int, int]:
    """Row → the index of the FIRST match starting on it.

    §2.6's second accepted consequence, written out as data: "the second and
    later matches on a line are highlighted but are never themselves the current
    one". Every expectation below that names a match names it through here, so
    a test can never expect a row-mate.
    """
    first: dict[int, int] = {}
    for index, row in enumerate(rows):
        first.setdefault(row, index)
    return first


def _expected_walk(rows: list[int], origin_row: int, presses: int) -> list[int]:
    """The match indices ``presses`` forward `n`s must answer, from ``origin_row``.

    §2.6 restated as arithmetic over the fixture: each press goes to the lowest
    row ABOVE the one the reviewer is on that holds a match, and lands on that
    row's first match. Derived from the match list and the row map — never by
    calling `step_match_by_row`, which is what is under test — so a walk that
    matches this is a walk that matches the criterion.

    Used only where a hand-written expectation is impossible: the tail of a real
    88 KB game, whose rows nobody chose. The synthetic fixtures' expectations
    are written out as integers instead, because there a reader can check them
    against the game.
    """
    first_on_row = _first_match_on_each_row(rows)
    populated = sorted(first_on_row)
    walk: list[int] = []
    row = origin_row
    for _ in range(presses):
        above = [candidate for candidate in populated if candidate > row]
        assert above, (
            f"the fixture runs out of matches after row {row} — this walk needs "
            f"{presses} rows above the landing and the game does not have them"
        )
        row = above[0]
        walk.append(first_on_row[row])
    return walk


async def _open_the_real_game(pilot) -> tuple[
    TranscriptScreen, VerticalScroll, TranscriptBody
]:
    """Open the largest committed game and return its screen, scroller and body.

    Shared by the three tail tests so they cannot disagree about which game they
    are describing, and so the one assertion they all owe — that this game
    really does overflow far enough for a clamp to exist — is made once.
    """
    screen = await _open_the_only_game(pilot)
    scroller = screen.query_one("#transcript-scroll", VerticalScroll)
    body = screen.query_one("#transcript-body", TranscriptBody)
    assert scroller.max_scroll_y > _LONG_GAME_VIEWPORT_ROWS, (
        "the discovered game fits (or nearly fits) the viewport, so there is no "
        "final screenful to be clamped inside and these tests cannot ask their "
        "question"
    )
    return screen, scroller, body


async def test_n_advances_and_capital_n_goes_back_each_landing_on_the_top_row(
    tmp_path: Path,
) -> None:
    """The walk out and back, over matches sitting on named rows (§2.6 AC1/AC2).

    Four presses out of one Enter, and the route matters as much as the
    endpoints: `n` `n` walks 10 → 30 → 45 and `N` `N` walks it back 45 → 30 →
    10, so the two directions are asserted to be inverses over the same three
    rows rather than each tested from a fresh start where a single wrong answer
    could look right.

    **Every landing is asserted twice** — as an ABSOLUTE row on the scroller,
    and as the render at that row actually containing the query. The two fail
    for different reasons, and only the second can see an offset → row map that
    is internally consistent and wrong (B15.4 meets the same distinction from
    the other side; B15.6 is what makes it loud).

    The header is asserted at every step as well, because it is the other half
    of what a press produces: §2.8 AC3 says the reviewer "can tell which match
    of the total they are on", and after Enter the search box that was saying so
    is gone.

    Every row here is clear of the clamp (`_JUMP_MATCH_ROWS` are all below
    `_SEARCH_OPEN_MAX_SCROLL_Y`), which is deliberate: this test is about the
    step arithmetic, and a landing that came to rest against the end of the game
    would be the clamp's answer rather than the step's. The tail is three tests
    below, on a fixture built for it.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-step-walk.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        # The premise: three matches, none of them in the tail, so every landing
        # below is the step's answer and never the end of the game's.
        assert _JUMP_MATCH_ROWS[-1] < _SEARCH_OPEN_MAX_SCROLL_Y

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()

        # Enter puts them on the first match; the walk starts from there.
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[0]
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 1, _JUMP_MATCHES
        )

        for step, row in enumerate(_JUMP_MATCH_ROWS[1:], start=1):
            await pilot.press("n")
            await pilot.pause()
            assert scroller.scroll_offset.y == row, (
                f"press {step} of `n` should have landed row {row} on the top "
                f"line; it is showing row {scroller.scroll_offset.y}"
            )
            assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text
            assert _rendered_sub_title(screen) == _expected_status(
                _JUMP_QUERY, step + 1, _JUMP_MATCHES
            )

        # ...and `N` retraces it exactly, which is the criterion: "when they
        # press `N`, then the game moves back to the previous match with it on
        # the top line".
        for step, row in reversed(list(enumerate(_JUMP_MATCH_ROWS[:-1], start=1))):
            await pilot.press("N")
            await pilot.pause()
            assert scroller.scroll_offset.y == row, (
                f"`N` should have walked back to row {row}; it is showing row "
                f"{scroller.scroll_offset.y}"
            )
            assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text
            assert _rendered_sub_title(screen) == _expected_status(
                _JUMP_QUERY, step, _JUMP_MATCHES
            )


async def test_n_skips_the_matches_sharing_the_row_it_started_on(
    tmp_path: Path,
) -> None:
    """Two matches on one row: `n` moves to the next row that has one (§2.6 AC3).

    "Given two matches fall on the same line of the screen and the reviewer is
    on the first of them, when they press `n`, then the game moves on to the
    next line that has a match — **the view always moves**."

    The fixture puts the wrong answer within reach, which is what makes this a
    test rather than a demonstration: from row 30 a step that moved
    match-by-match would answer this row's second match, the view would not move
    at all, and every other assertion in this sub-section would still pass. The
    right answer is row 45.

    **The running count's jump is asserted, not tolerated** — 2/4 straight to
    4/4. Functional-spec §2.8 states it ("a position among all matches, so where
    a line of the screen holds several it can jump by more than one at a time")
    and the author accepted it explicitly; the plausible "fix", renumbering the
    status to count only reachable matches, would make the total disagree with
    the four highlights the reviewer can see on screen. A test that asserted
    3/4 here would be asking for that bug.

    **And the skipped row-mate stays lit but never becomes current**, which is
    the other half of the same decision: it is a match, it is highlighted, and
    the stronger marker passes straight over it.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _STEP_ROWMATE_BODY, name="s44-step-mate.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        # THE PREMISE, and this test is vacuous without it: row 30 really does
        # hold two matches, they really are the second and third of four, and
        # the answer is on a row that exists and is clear of the clamp.
        _, _, rows = _match_rows(body, _JUMP_QUERY)
        assert len(rows) == _STEP_ROWMATE_MATCHES
        assert rows == [
            _STEP_ROWMATE_ROWS[0],
            _STEP_ROWMATE_CROWDED_ROW,
            _STEP_ROWMATE_CROWDED_ROW,
            _STEP_ROWMATE_ROWS[2],
        ], (
            f"the fixture puts its matches on rows {rows}; criterion (b) needs "
            f"two of them together on row {_STEP_ROWMATE_CROWDED_ROW}"
        )
        assert _STEP_ROWMATE_ROWS[-1] < _SEARCH_OPEN_MAX_SCROLL_Y

        # Read down to just above the crowded row, so Enter lands ON it rather
        # than the test scrolling there by the feature under test.
        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        assert scroller.scroll_offset.y == _JUMP_START_ROW
        assert _JUMP_START_ROW < _STEP_ROWMATE_CROWDED_ROW

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()

        # The reviewer is on the FIRST of the two matches on row 30.
        assert scroller.scroll_offset.y == _STEP_ROWMATE_CROWDED_ROW
        assert _current_match(_transcript_body_content(screen)) == 1
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 2, _STEP_ROWMATE_MATCHES
        )

        await pilot.press("n")
        await pilot.pause()

        # THE VIEW MOVED, and it moved to the next row that has a match rather
        # than to the match sitting beside the one they were on.
        assert scroller.scroll_offset.y == _STEP_ROWMATE_ROWS[2], (
            "`n` from the first of two matches on one row must go to the next "
            f"ROW that has one (row {_STEP_ROWMATE_ROWS[2]}). Staying on row "
            f"{_STEP_ROWMATE_CROWDED_ROW} is the press §2.6 forbids — the "
            "reviewer can already see both of those matches"
        )
        assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text

        content = _transcript_body_content(screen)
        # The count jumped over the row-mate, by design.
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, _STEP_ROWMATE_MATCHES, _STEP_ROWMATE_MATCHES
        )
        assert _current_match(content) == _STEP_ROWMATE_MATCHES - 1
        # ...and the match it skipped is still lit, like every other.
        assert len(_search_runs(content)) == _STEP_ROWMATE_MATCHES


@_requires_real_game
async def test_both_wraps_move_the_view_and_announce_themselves_in_the_header(
    tmp_path: Path,
) -> None:
    """`n` off the end and `N` off the start, on a real game (§2.6 AC4/AC5).

    Two criteria, one session, two presses — and the game is the largest
    committed one because **the synthetic fixtures cannot ask this question
    honestly**. A wrap has to be seen from a position that the *scroll* cannot
    represent: the last match of any real query sits inside the final screenful,
    where the view clamps, and a test whose last match is comfortably above the
    clamp proves only that a wrap works where nothing was ever in the way. That
    is precisely how Slice 4 shipped a defect — its probes used `vote`, whose
    last match on this game is on row 1030 against a clamp at 1160 — so the
    premise below (**the last match's row is past `max_scroll_y`**) is asserted
    before any behaviour, and the query is `the`.

    `N` from the first match is the shortest route to the far end of the game,
    and it is also criterion AC5 in its own right: "Given the reviewer is on the
    first match in the game, when they press `N`, then the view moves to the
    last match and a message tells them the search wrapped around." The `n` that
    follows is AC4, from the position only that press can reach.

    **The header is the whole of the "message" half**, and inside the clamp it
    is very nearly the whole of the feedback: the `N` lands as far down as the
    game can scroll and the `n` comes back, so the reviewer's evidence is the
    position, the `(wrapped)` suffix, and the marker — all three asserted.

    No count is written down anywhere here: the total comes out of
    `find_matches` and the expected index out of the match list, so this test
    survives the corpus gaining or losing a run directory.
    """
    text = _WRAP_PIN_REAL_GAME.read_text(encoding="utf-8")
    ledger, _ = _ledger_with_body(tmp_path, text, name="s44-step-wraps.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen, scroller, body = await _open_the_real_game(pilot)
        matches, _, rows = _match_rows(body, _TAIL_QUERY)
        total = len(matches)

        # THE PREMISE THIS TEST IS BLIND WITHOUT: the game's last match lives
        # inside the final screenful, where the scroll position cannot follow.
        assert rows[-1] > scroller.max_scroll_y, (
            f"the last `{_TAIL_QUERY}` sits on row {rows[-1]} with "
            f"`max_scroll_y` {scroller.max_scroll_y} — it is REACHABLE by "
            "scrolling, so this game cannot witness the clamp and the wrap "
            "below would be tested where nothing was in the way"
        )
        assert total >= 3

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _TAIL_QUERY)
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()

        # Enter, from the top of the game, lands on its first match.
        assert scroller.scroll_offset.y == rows[0]
        assert _rendered_sub_title(screen) == _expected_status(_TAIL_QUERY, 1, total)
        assert _current_match(_transcript_body_content(screen)) == 0

        # §2.6 AC5 — `N` from the first match wraps to the last row that has
        # one, landing on ITS first match (the second consequence of row-keyed
        # stepping), and says so.
        last = _first_match_on_each_row(rows)[rows[-1]]
        await pilot.press("N")
        await pilot.pause()
        assert _rendered_sub_title(screen) == _expected_status(
            _TAIL_QUERY, last + 1, total, wrapped=True
        ), "a backwards wrap must be announced — §2.6, 'the reviewer is told'"
        assert _current_match(_transcript_body_content(screen)) == last
        # As far down as the game goes, which is not as far as the match.
        assert scroller.scroll_offset.y == scroller.max_scroll_y
        assert scroller.scroll_offset.y < rows[last]

        # §2.6 AC4 — and `n` from there comes round to the first match again,
        # which is a move of over a thousand rows and must not be silent.
        await pilot.press("n")
        await pilot.pause()
        assert scroller.scroll_offset.y == rows[0], (
            "`n` from the last match must wrap to the FIRST match in the game; "
            f"it is showing row {scroller.scroll_offset.y}"
        )
        assert _TAIL_QUERY in body.render_line(scroller.scroll_offset.y).text.lower()
        assert _rendered_sub_title(screen) == _expected_status(
            _TAIL_QUERY, 1, total, wrapped=True
        )
        assert _current_match(_transcript_body_content(screen)) == 0


async def test_n_follows_where_the_reviewer_scrolled_not_where_they_last_jumped(
    tmp_path: Path,
) -> None:
    """After scrolling by hand, `n` resolves from the viewport (§2.6 AC7).

    "Given the reviewer has scrolled by hand away from the match they were on,
    when they press `n`, then the view moves to the first match below where they
    have scrolled to — `n` follows where they are, not where they last jumped
    from."

    **This test is load-bearing twice over, and the second time is not obvious.**
    The first is the criterion above. The second is that it is the ONLY thing
    stopping `TranscriptScreen._step_origin_row` from being "simplified" into
    always preferring the current match's row — which is the shape that makes
    every other test in this sub-section pass, including all three tail tests,
    because in those the reviewer never scrolls between presses. Task 4.4a fixed
    a dead-end by teaching the step to prefer `current`'s row *when the reviewer
    has not moved since landing*; delete the second half of that sentence and
    only this test goes red. Its fixture is built so the wrong answer is visibly
    wrong: from row 32, preferring the landing at row 10 answers row 30, which
    is a step BACKWARDS out of a key the reviewer pressed to go forward.

    The hand scroll is one `pagedown` on the game itself with the bar already
    closed — the ordinary reading key, not a call into the widget — and it is
    settled through `_settle_scroll` before any absolute row is read, because
    every one of the scroller's six keys animates (see that helper).
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-step-scrolled.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[0]

        # ...and now they read on by hand, past the second match.
        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        landed = scroller.scroll_offset.y

        # THE PREMISE: they are now BELOW the second match and above the third,
        # so "where they are" and "where they last jumped from" give different
        # answers and only one of them is forwards.
        assert _JUMP_MATCH_ROWS[1] < landed < _JUMP_MATCH_ROWS[2], (
            f"one `pagedown` from row {_JUMP_MATCH_ROWS[0]} left the reviewer "
            f"on row {landed}; this test needs them between the second and "
            "third matches or the two candidate answers coincide"
        )

        await pilot.press("n")
        await pilot.pause()

        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[2], (
            "`n` must resolve from where the reviewer scrolled to (row "
            f"{landed}), giving row {_JUMP_MATCH_ROWS[2]}. Row "
            f"{_JUMP_MATCH_ROWS[1]} means it resolved from the LANDING instead "
            "— a step backwards out of a forward key, and the mutation "
            "`_step_origin_row` exists to be prevented from becoming"
        )
        assert _JUMP_QUERY in body.render_line(scroller.scroll_offset.y).text
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 3, _JUMP_MATCHES
        )


async def test_the_match_the_reviewer_is_on_is_marked_more_strongly_than_its_neighbours(
    tmp_path: Path,
) -> None:
    """Which of the highlighted matches am I on? (§2.6 AC6).

    "Given the reviewer is sitting on one match among several highlighted on
    screen, when they look at the screen, then they can tell which one they are
    currently on." Read structurally, as tech-spec §4 instructs: the current
    match's background differs from every other match's, and no RGB value is
    pinned anywhere.

    **Three things are asserted, and the third is the one with teeth.** That a
    marker exists after Enter; that it MOVES with `n`; and that the marker is
    the ONLY difference — the match that has just stopped being current goes
    back to looking exactly like its neighbours. A `highlight` that appended a
    second span rather than substituting the style would leave a trail of
    ex-current matches behind it, which is the failure a "does the current one
    look different" assertion alone would miss entirely.

    The span count is checked to stay exactly one per match across both presses,
    which is the mechanical form of the same statement and what keeps B15.2's
    §2.3 ordering assertion checkable at all.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-step-marker.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        # Before any landing there is nothing to be on, and nothing is marked.
        typing = _transcript_body_content(screen)
        assert len(_search_runs(typing)) == _JUMP_MATCHES
        assert _current_match(typing) is None, (
            "no jump has happened yet, so no match can be 'the one they are "
            "on' — a marker here would be pointing at a match the reviewer has "
            "not navigated to"
        )

        await pilot.press("enter")
        await pilot.pause()
        landed = _transcript_body_content(screen)
        assert _current_match(landed) == 0
        assert len(_search_runs(landed)) == _JUMP_MATCHES

        await pilot.press("n")
        await pilot.pause()
        stepped = _transcript_body_content(screen)
        assert _current_match(stepped) == 1, (
            "the stronger marker must move with the view; a marker still on "
            "match 0 is a reviewer being told they are somewhere they are not"
        )
        # ...and nothing accumulated: still one span per match, so the match
        # that WAS current is back to looking like its neighbours.
        assert len(_search_runs(stepped)) == _JUMP_MATCHES


@pytest.mark.parametrize("key", ["n", "N"], ids=["n", "N"])
async def test_stepping_with_no_search_in_progress_does_nothing_and_raises_nothing(
    tmp_path: Path, key: str
) -> None:
    """`n` and `N` on a game nobody has searched (§2.6 AC8).

    "Given no search has been made since the game was opened, when the reviewer
    presses `n`, then nothing happens and no error appears." These are *screen*
    bindings, so they arrive whenever the game has focus — which is most of the
    time a reviewer is in a transcript — and the guard that makes them no-ops is
    the first statement of `_step_match`.

    Pressed from a SCROLLED position, so "nothing happens" is an absolute row
    with somewhere to move in both directions: from row 0 a `N` that wrapped to
    the end of the game would be caught, but an `n` that scrolled home would not.

    "No error appears" is read off the app rather than off the absence of a
    raise at the call site: an exception inside a message handler reaches
    Textual's unhandled-exception screen, not this test's assertions, so what
    has to be asserted is that the app is still running, still on this screen,
    and still at the same stack depth.

    The header is asserted to be untouched too — a press that wrote "0/0" or an
    empty position into it would be a visible change out of a key that promises
    none.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _JUMP_BODY, name=f"s44-step-none-{key}.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        resting = scroller.scroll_offset.y
        assert resting == _JUMP_START_ROW

        # Twice, because a first press that established some state and a second
        # that acted on it would be a different bug from one press acting.
        await pilot.press(key, key)
        await pilot.pause()

        assert scroller.scroll_offset.y == resting
        assert _search_runs(_transcript_body_content(screen)) == []
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE
        assert app.is_running
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH


async def test_the_header_counts_the_position_and_restores_the_hint_at_the_end(
    tmp_path: Path,
) -> None:
    """The status channel over a whole search, start to finish (§2.8 AC3, Task 4.5).

    Four states of the header in one session, because the promise is about a
    channel rather than about a string: the back-out hint before anything is
    typed; **still the hint while typing**, since the count and the query are
    both under the reviewer's cursor in the search row and the header's one row
    is where the back-out keys are written down; the position once they have
    landed, and again after `n` moves it; and the hint back when the search
    ends.

    **`SUB_TITLE` is pinned by VALUE as well as by attribute** — a rewrite of
    the copy would otherwise pass while claiming the header "goes back", and
    what §2.10 promises a reviewer who has finished searching is the screen they
    started on, which is a string.

    The Esc pressed at the end is here for Task 4.5's own instruction ("restore
    `SUB_TITLE` when the search clears") and is asserted as a HEADER fact: the
    reviewer is still in the game afterwards. The Esc state machine itself is
    Slice 5's, parametrized over all three back-out keys and both bar states;
    nothing here anticipates it.
    """
    assert TranscriptScreen.SUB_TITLE == _TRANSCRIPT_SUB_TITLE

    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-step-header.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        # 1. Before anything is typed.
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        # 2. While typing: the search row is saying it better, so the header
        # keeps the hint rather than repeating the count beside it.
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE

        await pilot.press("enter")
        await pilot.pause()
        # 3. Landed — and this is the state the search row cannot report from,
        # because Enter has just hidden it.
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 1, _JUMP_MATCHES
        )

        await pilot.press("n")
        await pilot.pause()
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 2, _JUMP_MATCHES
        )

        # 4. The search ends and the header is the reading view's again.
        await pilot.press("escape")
        await pilot.pause()
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE, (
            "a header still counting off a position over a game with no "
            "highlighting on it is the §2.9 AC1 leak wearing a hat"
        )
        assert _search_runs(_transcript_body_content(screen)) == []
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH


async def test_a_wrapped_enter_announces_the_wrap_in_the_header(
    tmp_path: Path,
) -> None:
    """§2.5 AC3's other half, which B15.4 could not yet assert.

    "Given the reviewer has scrolled past every match in the game, when they
    press Enter, then the view jumps back to the first match **and a message
    tells them the search wrapped around**." B15.4's
    `test_enter_past_every_match_wraps_round_to_the_first` pins the jump and
    says in its own docstring that the message needed a channel Slice 4 had not
    built. This is that message, read off the live header.

    Deliberately a separate test rather than two more lines in B15.4's: the two
    fail for different reasons — one when the row arithmetic is wrong, one when
    `wrapped` is dropped between `next_match` and `_search_status_text` — and a
    single test failing for either would name neither.

    The same fixture and the same route as B15.4's, so the wrap itself is
    already proven and this test can be about the string: `end` pressed inside
    the open search bar takes the reviewer past every match (which is §2.4
    working), and Enter then has nowhere forward to go.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-step-wrapsaid.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        await pilot.press("end")
        await _settle_scroll(pilot)

        # The premise: nothing left below them to find, so Enter must wrap.
        assert _JUMP_MATCH_ROWS[-1] < scroller.scroll_offset.y

        await pilot.press("enter")
        await pilot.pause()

        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[0]
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 1, _JUMP_MATCHES, wrapped=True
        ), (
            "§2.5 AC3: a jump that went round the end of the game must say so. "
            "Without the suffix the reviewer sees the view leap a long way "
            "backwards with no explanation, which reads as a bug"
        )


@_requires_real_game
async def test_enter_into_the_clamped_tail_leaves_the_next_press_able_to_advance(
    tmp_path: Path,
) -> None:
    """The live re-clamp, which is worth exactly one press (Task 4.4a).

    **The narrowest test in this sub-section, and it exists because the bug it
    catches costs nothing visible.** Enter scrolls while the search bar is still
    open and hides the bar afterwards, which hands the scroller back a viewport
    row and lowers `max_scroll_y` by one — so a reviewer parked at the clamp is
    pulled back a row, and the landing position Enter *recorded* is now one
    ahead of where the view actually is. `_step_origin_row` re-clamps the
    recorded value against the live `max_scroll_y` before comparing; without
    that, it reads the difference as "the reviewer has scrolled", resolves from
    the viewport, and the first `n` after a tail Enter re-answers the match the
    reviewer is already on.

    One wasted press is invisible in a passing suite: the second `n` works, the
    view was never going to move anyway inside the clamp, and every other
    assertion about the walk still holds one press later. So the assertion here
    is precisely "**the FIRST press advances**", and the expected index is
    derived from the match list rather than written down.

    Measured on this game (`the`, width 96): the tail Enter records 1161 and the
    view settles at 1160.
    """
    text = _WRAP_PIN_REAL_GAME.read_text(encoding="utf-8")
    ledger, _ = _ledger_with_body(tmp_path, text, name="s44-step-reclamp.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen, scroller, body = await _open_the_real_game(pilot)
        matches, _, rows = _match_rows(body, _TAIL_QUERY)
        total = len(matches)

        # THE PREMISE: there are matches the scroll position cannot reach.
        assert rows[-1] > scroller.max_scroll_y

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _TAIL_QUERY)
        await _settle_search(pilot)
        await pilot.press("end")
        await _settle_scroll(pilot)
        await pilot.press("enter")
        await pilot.pause()

        # The reviewer has landed INSIDE the clamped tail: the view is as far
        # down as it goes, and the match they are on is further down still.
        assert scroller.scroll_offset.y == scroller.max_scroll_y
        landing = _current_match(_transcript_body_content(screen))
        assert landing is not None
        assert rows[landing] > scroller.scroll_offset.y, (
            "this Enter did not land inside the clamped tail, so the re-clamp "
            "it is here to exercise never runs"
        )
        assert landing < total - 1, "the landing must leave somewhere to advance to"

        expected = _expected_walk(rows, rows[landing], 1)[0]

        await pilot.press("n")
        await pilot.pause()

        assert _current_match(_transcript_body_content(screen)) == expected, (
            f"the first `n` after a tail Enter must advance to match "
            f"{expected}; answering {landing} again is the recorded landing "
            "being compared UNCLAMPED against a viewport that has since "
            "re-fitted — one press silently wasted"
        )
        assert _rendered_sub_title(screen) == _expected_status(
            _TAIL_QUERY, expected + 1, total
        )


@_requires_real_game
async def test_presses_inside_the_clamped_tail_advance_though_the_view_cannot_move(
    tmp_path: Path,
) -> None:
    """The defect Task 4.4a found, stated as the test that would have caught it.

    **Measured, not hypothetical.** Keyed on `scroll_offset.y` alone, `n` inside
    the final screenful of a game answered the SAME match four presses running:
    the scroll clamps at `max_scroll_y`, so the viewport cannot express any
    position in there, and every press resolved from the same row. Nine matches
    were unreachable, the wrap could not fire, the view did not move and the
    marker did not move — so there was nothing on screen to notice, which is why
    it survived a round of probes.

    **The pair of assertions is the point of this test**, and neither half means
    anything alone:

    * `scroll_offset.y` is **unchanged** at the clamp after every press — the
      view genuinely cannot move, and a test that demanded it did would be
      asking the game to grow rows it does not have; and
    * `current` and the marker **advance** on every press, through a row-mate
      skip, with the header counting up.

    That is the whole of what §2.6 can promise in the last screenful, and it is
    why the current-match marker is load-bearing rather than decorative there.

    The expected walk is derived from the match list and the row map by
    `_expected_walk` — §2.6's rule written as arithmetic over the fixture, never
    by calling the production step — so this test states the criterion rather
    than re-running the code under test. On today's corpus the four presses it
    makes include one row-mate skip, which is how a real query behaves in real
    prose and is exactly what a hand-written tail fixture would have left out.
    """
    text = _WRAP_PIN_REAL_GAME.read_text(encoding="utf-8")
    ledger, _ = _ledger_with_body(tmp_path, text, name="s44-step-tail.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen, scroller, body = await _open_the_real_game(pilot)
        matches, _, rows = _match_rows(body, _TAIL_QUERY)
        total = len(matches)

        # THE PREMISE, asserted before anything else happens: this query has
        # matches past the furthest the game can be scrolled. `vote` does not,
        # on this same game, and that is why Slice 4's probes were blind.
        assert rows[-1] > scroller.max_scroll_y, (
            f"the last `{_TAIL_QUERY}` is on row {rows[-1]}, within "
            f"`max_scroll_y` {scroller.max_scroll_y} — there is no clamped tail "
            "in this fixture and this test proves nothing"
        )

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _TAIL_QUERY)
        await _settle_search(pilot)
        await pilot.press("end")
        await _settle_scroll(pilot)
        await pilot.press("enter")
        await pilot.pause()

        parked = scroller.scroll_offset.y
        assert parked == scroller.max_scroll_y
        landing = _current_match(_transcript_body_content(screen))
        assert landing is not None
        assert rows[landing] > parked

        walk = _expected_walk(rows, rows[landing], _TAIL_WALK_PRESSES)
        seen = [landing]
        for press, expected in enumerate(walk, start=1):
            await pilot.press("n")
            await pilot.pause()
            current = _current_match(_transcript_body_content(screen))
            assert current == expected, (
                f"press {press} of `n` inside the clamped tail answered match "
                f"{current}; §2.6 says it must reach {expected}. Re-answering "
                f"{seen[-1]} is the Task 4.4a dead-end — the viewport used as "
                "the cursor, where the viewport cannot go"
            )
            assert scroller.scroll_offset.y == parked, (
                "the view must NOT move here: there is nothing past the end of "
                "the game to scroll into view, and padding it with rows that "
                "are not part of the game was rejected (tech-spec §2.5)"
            )
            assert _rendered_sub_title(screen) == _expected_status(
                _TAIL_QUERY, expected + 1, total
            )
            seen.append(current)

        # Every press reached somewhere new, which is the sentence the defect
        # made false: "four consecutive presses all answered match 692".
        assert len(set(seen)) == len(seen)
        assert seen == sorted(seen)


async def test_editing_the_query_drops_the_landing_it_no_longer_describes(
    tmp_path: Path,
) -> None:
    """A changed query has no 'match 3' — so the position and the marker go (Task 4.4a).

    `SearchState.current` indexes into the match list of the query it was
    resolved against: match 2 of `vot` is not match 2 of `vote`, and is usually
    not even the same part of the game. Keeping it across an edit would repaint
    the stronger marker on an unrelated match and leave the header counting a
    position into a list that no longer exists — a readout the reviewer would
    believe, with nothing on screen to contradict it.
    `SearchState.scroll_at_landing` is dropped in the same breath, both-or-
    neither, so the next step resolves from the viewport again — which is where
    a reviewer who has just changed what they are looking for actually is.

    **Why the viewport half is asserted through its consequence rather than
    directly.** There is no key path to "bar closed, search live, matches lit,
    nothing current": the bar must be open to edit a query, and the only ways
    out of it are Enter, which lands and sets both fields again, or Esc, which
    ends the search outright. So `n` can never be pressed in that state, and the
    honest assertion is the one this test makes — the landing is **gone**, which
    is what makes the viewport the origin the moment there is one. Reading
    `_search` directly would pin the mechanism and leave the promise untested,
    which is the rule this whole section is written to (see B15's banner).

    `_SEARCH_MATCH_BODY` is the fixture because the edit here must CHANGE the
    match list rather than merely re-run it: `vot` has twice as many matches as
    `vote`, so a surviving index would be pointing into a list half the length.

    One measured mechanic is relied on and is worth writing down, because it is
    surprising: re-opening the bar with `/` focuses the box and Textual's
    `Input` **selects its whole value on focus**, so the next character typed
    REPLACES the query rather than extending it. That is what makes `vote` the
    thing to type here, not `e`.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-step-edit.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, "vot")
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        # The landing this test is about: a position in the header and a marker
        # on the game.
        assert _current_match(_transcript_body_content(screen)) == 1
        assert _rendered_sub_title(screen) == _expected_status(
            "vot", 2, _SEARCH_VOT_MATCHES
        )
        settled = scroller.scroll_offset.y

        # Re-open on the live search and type a different query over it.
        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, "vote")
        await _settle_search(pilot)
        assert search_input.value == "vote"

        content = _transcript_body_content(screen)
        # The new query's matches are lit — half as many as before...
        assert len(_search_runs(content)) == _SEARCH_VOTE_MATCHES
        assert _SEARCH_VOTE_MATCHES != _SEARCH_VOT_MATCHES
        # ...nothing is marked as current...
        assert _current_match(content) is None, (
            "the marker must not survive an edit: index 1 of `vot` is not index "
            "1 of `vote`, so a surviving marker would be pointing at an "
            "unrelated match with nothing on screen to say so"
        )
        # ...the header has stopped counting a position that no longer exists...
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE, (
            "a header still reading `vot · 2/40` over `vote`'s twenty matches "
            "is the stale index the drop exists to prevent, in the one place "
            "the reviewer would read it"
        )
        # ...and typing moved nothing (§2.2 AC4), so the viewport really is
        # where the reviewer is when the next navigation resolves.
        assert scroller.scroll_offset.y == settled


# ---------------------------------------------------------------------------
# B15.6 The offset -> row map against the render — the three-legged pin
# ---------------------------------------------------------------------------
#
# WHAT THIS SUB-SECTION IS FOR, because it is not what the rest of B15 is for.
# `wrap_row_starts` (`graphia.eval_ledger`) answers the one question the whole
# feature turns on: which RENDERED ROW does character N of the game fall on.
# Enter, `n` and `N` all end in `scroll_to(y=<that row>)`, so if the answer is
# wrong the reviewer is simply put somewhere else — no traceback, no visible
# breakage, just a search that lands a few lines off and a reader who concludes
# the tool is unreliable.
#
# The mirror is right today BY CONSTRUCTION: `textual/content.py` wraps by
# calling `rich._wrap.divide_line`, and `wrap_row_starts` calls the same
# function with the same arguments. "By construction" is a statement about the
# CURRENT Textual and the CURRENT Rich, and it is the whole exposure — both are
# ordinary upgrades away from folding lines differently, `rich._wrap` is
# PRIVATE, and nothing in the type system or the test suite would notice.
#
# THE THREE LEGS, and each exists because the ones beside it cannot see what it
# sees:
#
#   LEG 1 — THE ROW COUNT, against two independent Textual paths:
#           `Content.get_height(body.styles, width)` (the measurement Textual
#           uses to lay the widget out) and `body.size.height` (the size it
#           ended up with). Catches a wrap that produces a different NUMBER of
#           rows. Blind to a wrap that produces the same number in different
#           places.
#
#           NOT `scroller.virtual_size.height`, which is the obvious third
#           reading and is WRONG: it is `max(content_height, viewport_height)`,
#           so for a game that fits the viewport it reports the VIEWPORT — 22 —
#           whatever the game's real row count is. A pin written against it
#           would pass while comparing 22 to 22 and would assert nothing about
#           the map at all. `test_the_scroller_virtual_size_is_not_the_row_
#           count_of_a_game_that_fits` states that trap as an assertion so the
#           next person to reach for the convenient attribute finds out here.
#
#   LEG 2 — THE ROW CONTENT, every row, both regimes. THIS IS THE VALUABLE ONE.
#           A wrap change that moves boundaries while preserving the count is
#           invisible to leg 1 and is a total mis-jump: every offset resolves to
#           a plausible row that holds the wrong text. Leg 2 is the only leg
#           that can see it, and its failure message is written for whoever
#           trips it — naming the row, and showing the map's answer against the
#           render's, because that reader is debugging an upgrade and has never
#           heard of spec 044.
#
#           `.strip()` on both sides is not slack, it is REQUIRED:
#           `_wrap_and_format` `rstrip()`s every divided row except the last of
#           a source line, so the rendered row is short by whatever whitespace
#           the wrap point ate, while the map's slice runs to the next row's
#           start and so includes it (and the newline, for a row that ends a
#           source line). This is the same fact that makes summing
#           `Content.wrap(...)[i].plain` lengths an invalid way to recover
#           offsets, which is why `divide_line` is called at all.
#
#   LEG 3 — `divide_line` ITSELF, with hand-written input and hand-written
#           offsets, and terminal-free. Legs 1 and 2 fail if the function is
#           REMOVED (the module-scope import in `eval_ledger` fails at
#           collection) or if its behaviour changes in a way this corpus
#           happens to reach. Leg 3 fails if the SEMANTICS change at all —
#           where a cut falls after a space, where the fold branch chops an
#           unbroken token — whether or not any fixture here would have noticed.
#
# WHY LEG 3 LIVES HERE RATHER THAN IN `tests/test_transcript_search.py`, which
# is where terminal-free assertions otherwise belong (tech-spec §4, the ~100x
# per-item cost ratio): the three legs are ONE argument, and the cost rule they
# would be split by does not apply to leg 3 — it opens no terminal and pays none
# of the 0.63 s an item in this file usually costs. Read alone in the pure file,
# a test asserting hand-written offsets of a private Rich function has no
# visible reason to exist; read here, under the banner above, it is the third
# leg of a stated argument. The argument's readability is worth more than the
# filing.
#
# BOTH WIDTH REGIMES ARE SWEPT, 96 and 98 — see `_WRAP_PIN_WIDTH_OVERFLOWING`
# for why they differ and why a pin measured at only one of them would miss half
# the corpus.
#
# NO SEARCH IS OPENED ANYWHERE IN THIS SUB-SECTION, deliberately. The map is a
# pure function of (text, width); pressing `/` first would add a debounce to
# wait out and a highlight to hold constant, and would make a failure here
# ambiguous between "the wrap moved" and "the search did something".


def _wrap_pin_row_slices(text: str, row_starts: list[int]) -> list[str]:
    """The text of every rendered row AS THE MAP SEES IT, row by row.

    Row ``i`` runs from ``row_starts[i]`` to the next start, with the last row
    running to the end of the game — which is the contract `wrap_row_starts`
    documents (the starts partition ``[0, len(text))`` contiguously) restated as
    the slices a reader can actually compare against a screen.
    """
    ends = [*row_starts[1:], len(text)]
    return [text[start:end] for start, end in zip(row_starts, ends)]


async def _wrap_pin_open(
    pilot, game: _WrapPinGame
) -> tuple[TranscriptScreen, TranscriptBody, VerticalScroll, list[int], int]:
    """Open the game and return ``(screen, body, scroller, row_starts, width)``.

    Shared by legs 1 and 2 so the two read the SAME live geometry — the width is
    asked of the widget rather than assumed, exactly as `TranscriptScreen.
    _row_starts` asks it at jump time, and the regime the case claims is
    asserted here rather than in each test.

    The regime assertion is a precondition, not a result: a fixture edit that
    tipped the `fits` game over 22 rows would silently move it into the other
    width and both legs would keep passing while testing one regime twice.
    """
    screen = await _open_the_only_game(pilot)
    body = screen.query_one("#transcript-body", TranscriptBody)
    scroller = screen.query_one("#transcript-scroll", VerticalScroll)

    width = body.content_size.width
    assert width == game.width, (
        f"this case is here to pin the width-{game.width} regime, but the body "
        f"wrapped at {width}. The two differ by the vertical scrollbar's two "
        f"cells, so the fixture has crossed the {_LONG_GAME_VIEWPORT_ROWS}-row "
        "viewport and is now testing the other regime twice"
    )
    # Which regime a game lands in IS whether it overflows: the scrollbar is
    # what takes the two cells. Asserted so the two facts cannot drift apart.
    assert (scroller.max_scroll_y > 0) == (width == _WRAP_PIN_WIDTH_OVERFLOWING)
    # The body's text is what a search would search — read from the widget, so
    # the map and the render are built from the same string.
    assert body.text == game.text
    return screen, body, scroller, wrap_row_starts(body.text, width), width


@pytest.mark.parametrize("game", _WRAP_PIN_PARAMS)
async def test_the_row_map_is_as_long_as_the_game_textual_rendered(
    tmp_path: Path, game: _WrapPinGame
) -> None:
    """Leg 1: `len(wrap_row_starts(...))` is the rendered row count.

    Checked against **two independent Textual readings**, not one:

    * ``Content.get_height(body.styles, width)`` — the measurement Textual makes
      to decide how tall the widget must be, taken straight off the rendered
      ``Content``; and
    * ``body.size.height`` — the height the widget actually ended up with after
      layout.

    Two, because they can only agree with each other through the wrap, and
    because either alone leaves a way for the assertion to be trivially true:
    ``get_height`` is the same code path the mirror imitates, while
    ``size.height`` is the end of a long chain of layout that a CSS rule could
    change without the wrap changing at all.

    The non-vacuity assertion is the third thing here and it earns its place:
    without it, a game that never wrapped would satisfy every equality above
    while proving only that a line is a row, which is the one case the mirror
    cannot get wrong.
    """
    ledger, _ = _ledger_with_body(tmp_path, game.text, name="s44-wrap-pin-count.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen, body, _, row_starts, width = await _wrap_pin_open(pilot, game)
        content = _transcript_body_content(screen)

        # The premise: this game really does wrap, so the count below is a
        # statement about wrapping rather than about counting newlines.
        source_lines = game.text.count("\n") + 1
        assert len(row_starts) > source_lines, (
            f"{len(row_starts)} rows for {source_lines} source lines — this "
            "fixture no longer wraps at all, so it pins nothing"
        )

        assert len(row_starts) == content.get_height(body.styles, width)
        assert len(row_starts) == body.size.height
        if game.rows is not None:
            assert len(row_starts) == game.rows


async def test_the_scroller_virtual_size_is_not_the_row_count_of_a_game_that_fits(
    tmp_path: Path,
) -> None:
    """The reading leg 1 deliberately does NOT use, stated as an assertion.

    ``scroller.virtual_size.height`` is the attribute anyone writing this pin
    reaches for first — it is right there on the scroller, and on an overflowing
    game it *is* the row count, which is exactly what makes it dangerous. It is
    ``max(content_height, viewport_height)``: for a game that fits the viewport
    it reports the **viewport**, so a pin written against it would compare 22 to
    22 and assert nothing whatever about the offset -> row map.

    Measured here rather than described: this fixture renders
    :data:`_WRAP_PIN_FITTING_ROWS` rows, the viewport is
    :data:`_LONG_GAME_VIEWPORT_ROWS`, and ``virtual_size.height`` reports the
    latter. The inequality between them is the assertion — if a future Textual
    made ``virtual_size`` honest, this test failing is the notice that leg 1
    could be simplified, which is a fine reason to fail.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _WRAP_PIN_FITTING_BODY, name="s44-wrap-pin-virtual.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        body = screen.query_one("#transcript-body", TranscriptBody)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        row_starts = wrap_row_starts(body.text, body.content_size.width)

        # The premise: a game that FITS, which is the only regime where the two
        # readings part company.
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert len(row_starts) == _WRAP_PIN_FITTING_ROWS
        assert len(row_starts) < _LONG_GAME_VIEWPORT_ROWS

        # `max(content, viewport)` — the viewport wins, and the true row count
        # is nowhere in this number.
        assert scroller.virtual_size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.virtual_size.height != len(row_starts)


@pytest.mark.parametrize("game", _WRAP_PIN_PARAMS)
async def test_every_rendered_row_holds_the_text_the_row_map_says_it_does(
    tmp_path: Path, game: _WrapPinGame
) -> None:
    """Leg 2: row by row, the render and the map agree on WHERE the cuts fell.

    The leg that catches a wrap change leg 1 cannot see. A fold rule that breaks
    two characters earlier can easily produce the same number of rows on a given
    game while moving every boundary inside it — and the consequence is not a
    smaller error than a wrong count, it is a larger one: every offset still
    resolves to a row that exists, holds plausible text, and is the wrong one.

    Compared ``.strip()``-ed on both sides because Textual ``rstrip()``s every
    divided row except the last of a source line, while the map's slice runs to
    the next row's start and therefore keeps the whitespace the wrap point ate
    (and the newline, where the row ends a source line). Stripping removes
    exactly that difference and nothing else: a boundary that moved by a word,
    or by a character of a folded token, changes the row's text and is caught.

    Every row is compared, not a sample, and the mismatches are COLLECTED rather
    than asserted one at a time — a wrap change breaks thousands of rows at
    once, and "row 0 differs" would say far less about what happened than "1,115
    of 1,182 rows differ, and here are the first five".
    """
    ledger, _ = _ledger_with_body(tmp_path, game.text, name="s44-wrap-pin-content.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        _, body, _, row_starts, width = await _wrap_pin_open(pilot, game)

        expected_rows = _wrap_pin_row_slices(body.text, row_starts)
        mismatches: list[str] = []
        for row, expected in enumerate(expected_rows):
            rendered = body.render_line(row).text.strip()
            if rendered != expected.strip():
                mismatches.append(
                    f"  row {row} (map offsets "
                    f"{row_starts[row]}:{row_starts[row] + len(expected)})\n"
                    f"    wrap_row_starts says: {expected.strip()!r}\n"
                    f"    Textual rendered:     {rendered!r}"
                )

        # The premise: a game that really does wrap, so agreement below is about
        # wrapping and not about a file of short lines.
        assert len(expected_rows) > game.text.count("\n") + 1

        # `len(...) == 0` rather than `not mismatches`, so pytest's own
        # rewritten line reads `assert 15 == 0` instead of dumping the whole
        # joined list back at a reader who already has it, formatted, above.
        assert len(mismatches) == 0, (
            f"{len(mismatches)} of {len(expected_rows)} rendered rows disagree "
            f"with `wrap_row_starts` at width {width}.\n\n"
            "This is the offset -> row map drifting away from the wrapping "
            "Textual actually performs. `graphia.eval_ledger.wrap_row_starts` "
            "mirrors `textual.content.Content._wrap_and_format` by calling the "
            "same `rich._wrap.divide_line`; if Textual or Rich has changed how "
            "lines are divided, or how `_wrap_and_format` calls it, the mirror "
            "has to be updated to match or every search jump in the transcript "
            "viewer lands on the wrong line.\n\n"
            "First mismatching rows:\n" + "\n".join(mismatches[:5])
        )


# The hand-written leg-3 cases: input, width, the offsets `divide_line` must
# return, and the rows those offsets cut the input into. The rows are redundant
# with the offsets on purpose — they are what makes the case readable, and a
# reader checking the expectation by eye checks the rows, not the integers.
#
# Every branch of `divide_line` that `wrap_row_starts` can reach is here:
# a word that fits the remaining space, a word that does not but fits the next
# row, a line that needs no cut at all, the FOLD branch for a token wider than
# the whole row (both at the very start of a line and after a word), the
# whitespace-ownership rule (`re_word` is `\s*\S+\s*`, so a word carries its
# trailing spaces and the cut falls AFTER them), and the empty line.
_DIVIDE_LINE_CASES = [
    pytest.param(
        "aaa bbb ccc ddd", 7, [8], ["aaa bbb ", "ccc ddd"], id="cuts-at-a-space"
    ),
    pytest.param(
        "Alice: I vote for Cleo today",
        10,
        [9, 18],
        ["Alice: I ", "vote for ", "Cleo today"],
        id="three-rows-of-speech",
    ),
    pytest.param(
        "aaa bbb ccc ddd", 15, [], ["aaa bbb ccc ddd"], id="no-cut-when-it-fits"
    ),
    pytest.param(
        "abcdefghij", 4, [4, 8], ["abcd", "efgh", "ij"], id="folds-a-long-token"
    ),
    pytest.param(
        "hi abcdefghijkl",
        6,
        [3, 9],
        ["hi ", "abcdef", "ghijkl"],
        id="folds-a-long-token-after-a-word",
    ),
    pytest.param(
        "one  two", 4, [5], ["one  ", "two"], id="the-cut-falls-after-the-spaces"
    ),
    pytest.param("", 5, [], [""], id="an-empty-line"),
]


@pytest.mark.parametrize(("line", "width", "cuts", "rows"), _DIVIDE_LINE_CASES)
def test_divide_line_still_cuts_where_the_row_map_assumes(
    line: str, width: int, cuts: list[int], rows: list[str]
) -> None:
    """Leg 3: the private function's SEMANTICS, by hand, with no terminal.

    Legs 1 and 2 go red if `divide_line` disappears (the module-scope import in
    `graphia.eval_ledger` fails at collection, which is the canary tech-spec
    §2.2 asks for) or if its behaviour changes somewhere the three fixtures
    above happen to reach. Neither is the same as pinning what the function
    MEANS, and a fold rule can change in a corner no fixture exercises and still
    move a real game's rows the day a transcript contains the right line.

    So: seven lines written by hand, seven sets of offsets written by hand, and
    the rows those offsets produce written out beside them so the expectation is
    checkable by eye rather than by re-deriving `rich/_wrap.py` in one's head.

    Two properties of `divide_line` that the cases encode and that are easy to
    get backwards when reading it:

    * **The offsets are CHARACTER indices, not cell offsets**, despite its own
      docstring saying "a list of cell offsets". `wrap_row_starts` adds them to a
      character offset, so this is load-bearing rather than pedantic.
    * **A word owns its trailing whitespace** (`re_word` is ``\\s*\\S+\\s*``), so
      the cut falls after the spaces and never before them — which is why the
      rendered row has to be `rstrip()`ed before leg 2 can compare it.

    ``fold=True`` throughout, because that is what `wrap_row_starts` passes and
    what `TranscriptScreen`'s CSS leaves in force (``text-overflow`` defaults to
    ``fold`` and the stylesheet does not set it).
    """
    assert divide_line(line, width, fold=True) == cuts

    # The same expectation, stated as the rows a reader can check by eye.
    divided = []
    previous = 0
    for cut in [*cuts, len(line)]:
        divided.append(line[previous:cut])
        previous = cut
    assert divided == rows


def test_the_row_map_folds_lines_with_rich_s_own_function() -> None:
    """The import canary, made explicit: `eval_ledger.divide_line` IS Rich's.

    Tech-spec §2.2 accepts a dependency on the **private** `rich._wrap` on one
    ground: `textual/content.py` imports it itself, so calling the same function
    makes the mirror agree with the render BY CONSTRUCTION. That argument holds
    only while the name in `graphia.eval_ledger` really is the object Textual
    uses — a local reimplementation, a vendored copy or a compatibility shim
    introduced to "remove the private import" would keep every signature, keep
    leg 3's hand-written offsets passing for as long as the copy stayed in step,
    and quietly end the guarantee.

    One `is` catches all of that, and costs nothing.
    """
    assert divide_line is rich_divide_line


# ---------------------------------------------------------------------------
# B15.7 Leaving the search, and leaving the game — the three-state Esc machine
# ---------------------------------------------------------------------------
#
# THE FIFTH QUESTION IN THIS SECTION, after "where did the key land" (B15.1),
# "what lit up" (B15.2), "where did the game come to rest" (B15.4) and "what
# does the screen SAY" (B15.5): WHAT DOES A BACK-OUT KEY DO, AND IN WHICH
# STATE. Functional-spec §2.9 is a three-state machine and the tech spec's own
# task list calls this "the single most likely bug in the feature", so the sweep
# below is two-dimensional on purpose — three keys against all three states.
#
# THE THREE STATES, named here because every test below reaches one of them:
#
#   (i)   BAR OPEN, SEARCH LIVE — `/` pressed, a query typed, `#search-row`
#         carrying `-open` and the `TranscriptSearchInput` holding focus.
#   (ii)  BAR HIDDEN, SEARCH LIVE — what Enter on a query that was actually
#         typed leaves behind: the matches stay lit, the scroller has focus.
#   (iii) NO SEARCH — `_search is None`, either never searched or cleared.
#
# THE MATRIX IS NOT UNIFORM, AND A SWEEP THAT ASSUMED IT WAS WOULD BE ASSERTING
# THE OPPOSITE OF THE DESIGN. In state (i) the focused `Input` is in the way and
# only `escape` ever reaches `action_close`: `backspace` DELETES A CHARACTER of
# the query and `q` TYPES A LITERAL `q` into it, both leaving the bar open. That
# is functional-spec §2.4 working (B15.1's
# `test_the_bars_own_keys_type_into_it_instead_of_leaving_the_game` is the same
# fact from the other side), and it is why the expectation below is a TABLE
# rather than a loop — and why the two typing rows assert THE QUERY TEXT. A row
# that asserted only "still on a `TranscriptScreen`" would pass just as happily
# against a key that did nothing at all, which is the one outcome none of the
# three is allowed to have.
#
# In states (ii) and (iii) the scroller holds focus and all three keys reach the
# same `action_close`, where the branch is on `_search` alone: in (ii) they
# clear the search and STAY, in (iii) they leave in one press.
#
# READING `TranscriptScreen._search` IS THE ONE DEPARTURE THIS SUB-SECTION MAKES
# FROM THE SECTION BANNER ABOVE, and it is forced rather than convenient. Task
# 5.2a's whole finding is that two of the states are IDENTICAL ON SCREEN: after
# Enter on an empty query and after Enter on a query that matched nothing,
# nothing is lit, the bar is hidden and the header is back at its resting hint —
# the only difference is whether a `SearchState` is still alive behind them, and
# the only thing a reviewer can observe about it is that the next Esc is spent
# clearing it. So every state assertion below is PAIRED with the observable
# consequence (how many Escs it takes to get back to the run), and neither is
# written without the other: the field says what the screen is, the Esc count
# says what that costs the reviewer, and a regression that broke one without the
# other is not a regression this feature can have.
#
# WHAT THE EXISTING SUITE ALREADY DISCHARGES, so it is not repeated here.
# Task 5.4's item (e) — "the runs list and a run's figures behave exactly as
# before" — is B1-B9's whole subject (the table, the runs-list filter in B4/B6,
# the detail pane in B9) plus B12/B13's pane-focus and round-trip walks, none of
# which this feature touches; and §2.10 AC1's reading view is pinned by
# `test_scrolling_a_long_game_behaves_as_before` (B14.4) and the two
# strict-equality span tests in B14.2/B14.3, which never press `/`. Duplicating
# any of them here would add items without adding coverage.

# The query the back-out sweep searches for, on B15.2's game. Four characters,
# so `backspace` leaves a THREE-character query that is still a live search
# (`vot`, which that fixture also matches) rather than an empty one — the
# deletion has to be visible as an edit, not as an accidental clear.
_BACK_OUT_QUERY = "vote"

# The three states of functional-spec §2.9, as the ids the sweep is written in.
_STATE_BAR_OPEN = "bar-open"
_STATE_SEARCH_LIVE = "search-live"
_STATE_NO_SEARCH = "no-search"


class _BackOutOutcome(NamedTuple):
    """What one back-out key does in one of §2.9's three states.

    ``query`` is what the search box holds AFTERWARDS, and it is the field that
    makes the two typing rows falsifiable: `backspace` and `q` are asserted to
    have changed the text in the way §2.4 says they do, not merely to have left
    the reviewer in the game.

    Unread when ``leaves`` is true — the screen is gone and there is no box.
    """

    leaves: bool
    search_alive: bool
    bar_open: bool
    query: str


# The search is gone and the reviewer is still in the game — `action_close`'s
# first branch, reached by `escape` in state (i) and by all three keys in (ii).
_BACK_OUT_CLEARED = _BackOutOutcome(
    leaves=False, search_alive=False, bar_open=False, query=""
)
# The reviewer is back on the run — `action_close`'s second branch.
_BACK_OUT_LEFT = _BackOutOutcome(
    leaves=True, search_alive=False, bar_open=False, query=""
)

# THE MATRIX, written out rather than derived, because five of its nine cells
# are the same answer and four are not — and the four that are not are the point
# of the sweep. `backspace` and `q` in state (i) never reach `action_close` at
# all; they edit the query under the reviewer's cursor.
_BACK_OUT_EXPECTATIONS: dict[tuple[str, str], _BackOutOutcome] = {
    (_STATE_BAR_OPEN, "escape"): _BACK_OUT_CLEARED,
    (_STATE_BAR_OPEN, "backspace"): _BackOutOutcome(
        leaves=False, search_alive=True, bar_open=True, query=_BACK_OUT_QUERY[:-1]
    ),
    (_STATE_BAR_OPEN, "q"): _BackOutOutcome(
        leaves=False, search_alive=True, bar_open=True, query=_BACK_OUT_QUERY + "q"
    ),
    (_STATE_SEARCH_LIVE, "escape"): _BACK_OUT_CLEARED,
    (_STATE_SEARCH_LIVE, "backspace"): _BACK_OUT_CLEARED,
    (_STATE_SEARCH_LIVE, "q"): _BACK_OUT_CLEARED,
    (_STATE_NO_SEARCH, "escape"): _BACK_OUT_LEFT,
    (_STATE_NO_SEARCH, "backspace"): _BACK_OUT_LEFT,
    (_STATE_NO_SEARCH, "q"): _BACK_OUT_LEFT,
}

# Every key crossed with every state, ids reading `<state>-<key>` so a failure
# names the cell. Derived from `_TRANSCRIPT_BACK_KEYS` rather than from a fresh
# tuple, so a fourth back-out key added to the screen arrives here — and in the
# rot guard beside that constant — instead of being swept by neither.
_BACK_OUT_PARAMS = [
    pytest.param(key, state, id=f"{state}-{key}")
    for state in (_STATE_BAR_OPEN, _STATE_SEARCH_LIVE, _STATE_NO_SEARCH)
    for key in _TRANSCRIPT_BACK_KEYS
]

# What `#search-count` says for a query the game does not contain. Mirrors the
# module's `_NO_MATCHES_MESSAGE`, asserted by value rather than imported — this
# file's convention for user-visible copy (see `_EMPTY_LEDGER_MESSAGE`), so a
# rewrite of the string is a decision someone makes here too.
_NO_MATCHES_READOUT = "No matches"

# A query absent from `_LONG_GAME_BODY`, for the third Enter case. Lowercase, so
# smart case leaves it case-INSENSITIVE and the empty result is a statement
# about the game rather than about the casing. The test asserts that absence
# before it asserts anything about it.
_ENTER_NO_MATCH_QUERY = "zqxjv7"


class _EnterCase(NamedTuple):
    """One thing a reviewer can have typed when they press Enter (§2.5 AC5, §2.8 AC4).

    Three cases, and the first two are INDISTINGUISHABLE ON SCREEN from each
    other in every respect but one — which is exactly why `survives` is asserted
    as a field of the screen and again as a number of Escs.

    ``count_readout`` is what ``#search-count`` must say afterwards, or ``None``
    for "the fixture's own match total", which the test computes rather than
    hard-coding a number that would rot with the fixture.
    """

    typed: str
    survives: bool
    survives_because: str
    lit: bool
    positioned: bool
    count_readout: str | None
    escapes: int
    builds_the_row_map: bool


_ENTER_CASES = [
    pytest.param(
        _EnterCase(
            typed="",
            survives=False,
            survives_because=(
                "an Enter on nothing typed must END the search (Task 5.2a): the "
                "screen it leaves is character-for-character the screen of a "
                "reviewer who never pressed `/`, so a `SearchState` behind it "
                "is a state they cannot see and the next Esc is silently spent "
                "clearing it — against §2.9 AC3"
            ),
            lit=False,
            positioned=False,
            count_readout="",
            escapes=1,
            builds_the_row_map=False,
        ),
        id="nothing-typed",
    ),
    pytest.param(
        _EnterCase(
            typed=" ",
            survives=True,
            survives_because=(
                "WHITESPACE IS A TYPED QUERY. `find_matches` is case-smart but "
                "otherwise literal, so a space is ordinary text to it — the "
                "most-matching query in the whole corpus (14,524 hits on the 88 "
                "KB committed game). The empty-Enter branch must therefore test "
                "`not search.query` and NEVER `not search.query.strip()`: "
                "stripping throws this reviewer's search away on the grounds "
                "that it 'looked empty'"
            ),
            lit=True,
            positioned=True,
            count_readout=None,
            escapes=2,
            builds_the_row_map=True,
        ),
        id="a-single-space",
    ),
    pytest.param(
        _EnterCase(
            typed=_ENTER_NO_MATCH_QUERY,
            survives=True,
            survives_because=(
                "a query that matched nothing is a search the reviewer is "
                "mid-way through and will edit or abandon (§2.8 AC2, §2.9 AC5) "
                "— 'I asked, and there was nothing' is not 'I did not ask'"
            ),
            lit=False,
            positioned=False,
            count_readout=_NO_MATCHES_READOUT,
            escapes=2,
            builds_the_row_map=True,
        ),
        id="typed-but-no-match",
    ),
]


async def _reach_back_out_state(
    pilot, screen: TranscriptScreen, state: str
) -> TranscriptSearchInput:
    """Put the screen in one of §2.9's three states, by real keystrokes only.

    Asserts the state it claims to have reached before handing back, so a sweep
    row can never press its key into a state the setup failed to produce — which
    would turn nine parametrized cases into three, silently and greenly.

    Returns the search box, so a caller can read the query back as the key path
    actually built it.
    """
    search_input = screen.query_one(TranscriptSearchInput)
    search_row = screen.query_one("#search-row", Horizontal)
    scroller = screen.query_one("#transcript-scroll", VerticalScroll)

    if state == _STATE_NO_SEARCH:
        assert screen._search is None
        assert "-open" not in search_row.classes
        assert screen.focused is scroller
        return search_input

    await pilot.press("slash")
    await pilot.pause()
    await _type_transcript_search(pilot, _BACK_OUT_QUERY)
    await _settle_search(pilot)

    # The premise both remaining states share: a query that is really typed and
    # really lit, so "the highlighting is gone" afterwards is a change.
    assert search_input.value == _BACK_OUT_QUERY
    assert screen._search is not None
    assert screen._search.query == _BACK_OUT_QUERY
    assert len(_search_runs(_transcript_body_content(screen))) == _SEARCH_VOTE_MATCHES

    if state == _STATE_BAR_OPEN:
        assert "-open" in search_row.classes
        assert screen.focused is search_input
        return search_input

    assert state == _STATE_SEARCH_LIVE, f"unknown state {state!r}"
    # Enter closes the prompt and keeps the search — §2.5 AC4, and the state
    # that only exists from Slice 3 onward.
    await pilot.press("enter")
    await pilot.pause()
    assert "-open" not in search_row.classes
    assert screen._search is not None
    assert screen.focused is scroller
    assert len(_search_runs(_transcript_body_content(screen))) == _SEARCH_VOTE_MATCHES
    return search_input


@pytest.mark.parametrize(("key", "state"), _BACK_OUT_PARAMS)
async def test_a_back_out_key_answers_the_state_it_was_pressed_in(
    tmp_path: Path, key: str, state: str
) -> None:
    """`escape` / `backspace` / `q` against all three of §2.9's states.

    The sweep the tech spec names as the feature's likeliest bug, and the second
    dimension is the half that matters: a one-dimensional "Esc clears then
    leaves" test passes against a screen that branches on the search BAR rather
    than on the search STATE, because bar visibility answers "closed" to state
    (ii) and state (iii) alike and the reviewer is popped out from under their
    own highlights.

    **The expectation is read from `_BACK_OUT_EXPECTATIONS`, which is NOT
    uniform.** In state (i) only `escape` reaches `action_close`; `backspace`
    deletes a character and `q` types one, and both rows assert the resulting
    QUERY TEXT — at the box and at `SearchState.query`, which are separately
    capable of being wrong. Without that, a key that was swallowed entirely
    would satisfy every other assertion in this test.

    **Where the press cleared the search and stayed, the SAME key is pressed
    again** and must leave. That is §2.9 AC5's second half — "a further Esc
    leaves it" — asserted for all three keys rather than for Esc alone, which is
    what `action_close`'s shared binding actually promises.

    `scroll_offset.y` is pinned across every press: Esc abandons the search, not
    the reviewer's place in the text (§2.9 AC1).
    """
    expected = _BACK_OUT_EXPECTATIONS[(state, key)]
    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-back-out.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        search_row = screen.query_one("#search-row", Horizontal)

        search_input = await _reach_back_out_state(pilot, screen, state)
        resting_y = scroller.scroll_offset.y

        await pilot.press(key)
        await pilot.pause()

        if expected.leaves:
            assert isinstance(app.screen, DetailScreen), (
                f"{key!r} in state {state!r} left the reviewer on a "
                f"{type(app.screen).__name__}; with no search live it must go "
                "straight back to the run (§2.9 AC3 — the change costs a "
                "reviewer who never searches no extra keypress)"
            )
            assert len(app.screen_stack) == _DETAIL_STACK_DEPTH
            return

        assert isinstance(app.screen, TranscriptScreen), (
            f"{key!r} in state {state!r} left the game; with a search live the "
            "first press owes the reviewer a cleared search and the game they "
            "were reading (§2.9 AC1/AC5)"
        )
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH

        assert (screen._search is not None) is expected.search_alive, (
            f"{key!r} in state {state!r}: the search is "
            f"{'alive' if screen._search is not None else 'gone'} and the "
            f"matrix says it should be "
            f"{'alive' if expected.search_alive else 'gone'}"
        )
        assert ("-open" in search_row.classes) is expected.bar_open
        assert tuple(search_row.size) == (
            _SEARCH_ROW_OPEN_SIZE if expected.bar_open else _SEARCH_ROW_CLOSED_SIZE
        )
        # THE QUERY, and for the two typing rows this is the whole assertion:
        # §2.4 says `backspace` deletes a character and `q` adds one, so a key
        # that was swallowed leaves `_BACK_OUT_QUERY` untouched and fails here
        # while passing everything above.
        assert search_input.value == expected.query, (
            f"{key!r} in state {state!r} left {search_input.value!r} in the "
            f"box; §2.4 says it should read {expected.query!r}"
        )
        assert scroller.scroll_offset.y == resting_y, (
            "the press moved the game; Esc abandons the search, not the "
            "reviewer's place in it (§2.9 AC1)"
        )

        if expected.search_alive:
            # Still searching: the state tracks the box character for
            # character, and the cursor is still in it.
            assert screen._search.query == expected.query
            assert screen.focused is search_input
            return

        # Cleared: the amber is gone, the header is back, the game answers the
        # arrow keys again...
        assert _search_runs(_transcript_body_content(screen)) == [], (
            f"{key!r} in state {state!r} left the matches lit; §2.9 AC1 asks "
            "for the highlighting to go with the search"
        )
        assert screen.sub_title == TranscriptScreen.SUB_TITLE
        assert screen.focused is scroller
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS

        # ...and the SAME key again leaves (§2.9 AC5's second half).
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen), (
            f"a second {key!r} did not leave the game; the first one cleared "
            "the search, so the next press has nothing left to clear"
        )
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


async def test_escape_keeps_the_place_the_reviewer_scrolled_to_while_typing(
    tmp_path: Path,
) -> None:
    """Esc hands back the place the search ENDED at, not the one it started from.

    Task 5.4 item (a), and the reason it is a separate test from the sweep: the
    sweep's state (i) never scrolls, so its `scroll_offset.y` assertion is true
    of an implementation that remembered the row `/` was pressed at and restored
    it on the way out. Functional-spec §2.4 invites the reviewer to scroll while
    the bar is open — that is the whole of the "the game still scrolls while you
    type" promise — so §2.9 AC1's "the game is still at the same place on
    screen" is a claim about where they ENDED, and it is only falsifiable from a
    row they scrolled to under the open bar.

    So this one opens the search at one row, scrolls a page under it, types
    another character there, and asserts Esc leaves the view at the second row.
    The two rows are asserted to differ before the Esc, so the claim cannot be
    satisfied by a scroll that never happened.

    The narrowing on that last character (`vot` → `vote`, 40 spans → 20) is not
    decoration either: it says the search was still live and still repainting
    after the scroll, which is what makes the Esc that follows an abandonment of
    something rather than of nothing.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-esc-place.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        search_row = screen.query_one("#search-row", Horizontal)

        # Read into the middle of the game with the ordinary reading keys, then
        # open the search there — this is the row a "restore where it opened"
        # bug would hand back.
        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        opened_at = scroller.scroll_offset.y
        assert opened_at == _LONG_GAME_VIEWPORT_ROWS

        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, "vot")
        await _settle_search(pilot)
        assert scroller.scroll_offset.y == opened_at
        assert (
            len(_search_runs(_transcript_body_content(screen))) == _SEARCH_VOT_MATCHES
        )

        # §2.4: the game scrolls under the open bar, and the query survives it.
        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        await pilot.press("e")
        await _settle_search(pilot)

        ended_at = scroller.scroll_offset.y
        assert ended_at == opened_at + _SEARCH_OPEN_VIEWPORT_ROWS
        assert ended_at != opened_at, (
            "the page key did not move the game under the open bar, so this "
            "test cannot tell 'where they ended' from 'where they opened'"
        )
        assert search_input.value == "vote"
        assert (
            len(_search_runs(_transcript_body_content(screen))) == _SEARCH_VOTE_MATCHES
        )

        await pilot.press("escape")
        await pilot.pause()

        # The search is gone and the bar with it...
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
        assert screen._search is None
        assert "-open" not in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert search_input.value == ""
        assert _search_runs(_transcript_body_content(screen)) == []
        assert screen.sub_title == TranscriptScreen.SUB_TITLE
        assert screen.focused is scroller

        # ...and the reviewer is where they scrolled to, not where they opened.
        assert scroller.scroll_offset.y == ended_at, (
            f"Esc left the game at row {scroller.scroll_offset.y}; the reviewer "
            f"had scrolled to {ended_at} under the open bar (they opened it at "
            f"{opened_at}). §2.9 AC1 owes them where the search ended"
        )
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.max_scroll_y == _LONG_GAME_MAX_SCROLL_Y


@pytest.mark.parametrize("case", _ENTER_CASES)
async def test_what_an_enter_leaves_behind_depends_on_what_was_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _EnterCase
) -> None:
    """Nothing typed ends the search; nothing FOUND does not (Task 5.2a).

    Three Enters that move the game not at all and look nearly identical
    afterwards, and the differences between them are the test:

    * `_search` — ``None`` for the empty one, alive for the other two. This is
      the one place in B15 that reads the private field, and it is forced: the
      empty case and the no-match case are the same screen (nothing lit, bar
      hidden, header at its resting hint) and the field is the only thing that
      separates them at the moment of the press.
    * **The Escs it costs to get back to the run** — 1 or 2 — which is that same
      difference stated as something the reviewer experiences. Asserted beside
      the field, never instead of it: the field says what the screen is, the
      count says what it costs.
    * `#search-count` — blank, a total, or "No matches". The ONE visible
      difference between the empty case and the no-match case, and it is on the
      row that is about to be hidden.
    * The header — a position only where there was something to land on.

    **The single-space row is the anti-`.strip()` guard.** It is the assertion
    that goes red for a future tidy-up that collapses the empty branch and the
    no-match branch into one, or that "normalises" the query before testing it:
    `find_matches` treats whitespace as ordinary literal text, so a space is a
    real question with more answers than any other query in the corpus.

    **And one assertion no state check can make: the ORDER.** A counting wrapper
    over `TranscriptScreen._row_starts` says whether the empty Enter built the
    offset→row map before discovering it had nothing to jump to. Every other
    assertion here passes against a `_clear_search()` moved to after the map is
    built and cached into a `SearchState` that ceases to exist on the next line;
    this one does not.

    `scroll_offset.y` is captured BEFORE `/` so the whole open-type-submit
    sequence is inside the "nothing moves" claim of §2.5 AC5 / §2.8 AC4 — not
    just the Enter.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _LONG_GAME_BODY, name="s44-enter-typed.yaml"
    )

    # THE ORDERING PROBE. A wrapper rather than a stub — the real map is still
    # built, so the two cases that legitimately need one still jump correctly
    # and the count is the only thing added.
    builds: list[int] = []
    unpatched_row_starts = TranscriptScreen._row_starts

    def counting_row_starts(
        self: TranscriptScreen, body: TranscriptBody
    ) -> list[int]:
        builds.append(body.content_size.width)
        return unpatched_row_starts(self, body)

    monkeypatch.setattr(TranscriptScreen, "_row_starts", counting_row_starts)

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)
        search_row = screen.query_one("#search-row", Horizontal)
        search_count = screen.query_one("#search-count", Static)

        # Read into the middle of the game before searching, with the ordinary
        # reading keys — so "nothing moved" is a statement about a row the
        # reviewer chose rather than about the top of the game, which an
        # implementation that reset the view would also produce.
        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        reading_at = scroller.scroll_offset.y
        assert reading_at == _LONG_GAME_VIEWPORT_ROWS

        # THE FIXTURE'S PREMISES, stated before anything is asserted about the
        # behaviour. Computed from the pure matchers against the widget's own
        # text and its own live wrap width, which is how the rest of B15 derives
        # an expectation without re-running the code under test.
        matches = find_matches(body.text, case.typed) if case.typed else []
        row_starts = wrap_row_starts(body.text, body.content_size.width)
        # `row_starts[row]` IS `offset_for_row(row_starts, row)` — the offset of
        # the first character of the reviewer's top rendered row, which is what
        # Enter resolves "at or below the current position" from.
        top_offset = row_starts[reading_at]
        landing = None
        if case.lit:
            assert matches, (
                f"{case.typed!r} matches nothing in this game, so this case "
                "cannot ask whether a typed query survives Enter"
            )
            landing = next(
                index
                for index, (start, _) in enumerate(matches)
                if start >= top_offset
            )
            # The jump has nowhere to go: the first match at or below the top
            # row is ON the top row, so §2.5 AC2 ("a match already on the top
            # line is where Enter lands") makes "nothing moved" a real claim
            # here rather than a lucky one.
            assert row_for_offset(row_starts, matches[landing][0]) == reading_at, (
                "the first match below the reviewer is not on their top row, "
                "so Enter will legitimately scroll and this case's "
                "`scroll_offset.y` assertion would be testing the wrong thing"
            )
        elif case.typed:
            assert matches == [], (
                f"{case.typed!r} was chosen because this game does not contain "
                f"it, and it has {len(matches)} matches"
            )

        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, case.typed)
        await _settle_search(pilot)
        assert search_input.value == case.typed
        assert scroller.scroll_offset.y == reading_at

        # Only the Enter's own map-building is being counted; the open and the
        # typing build none, and this makes that irrelevant either way.
        builds.clear()

        await pilot.press("enter")
        await pilot.pause()

        # WHAT EVERY CASE HAS IN COMMON: the prompt is gone, the game has not
        # moved, and the reviewer is back in the text.
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
        assert "-open" not in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert screen.focused is scroller
        assert scroller.scroll_offset.y == reading_at, (
            f"Enter on {case.typed!r} moved the game from row {reading_at} to "
            f"{scroller.scroll_offset.y}; §2.5 AC5 and §2.8 AC4 both say "
            "nothing moves"
        )

        # WHAT SEPARATES THEM, and the first of these is invisible on screen.
        if case.survives:
            assert screen._search is not None, (
                f"Enter on {case.typed!r} ended the search — {case.survives_because}"
            )
            assert screen._search.query == case.typed
        else:
            assert screen._search is None, (
                f"Enter on {case.typed!r} left a search alive — "
                f"{case.survives_because}"
            )

        content = _transcript_body_content(screen)
        lit = _search_runs(content)
        if case.lit:
            assert [text for _, _, text in lit] == [case.typed] * len(matches)
        else:
            assert lit == [], (
                f"Enter on {case.typed!r} lit {len(lit)} spans; there is "
                "nothing to light"
            )

        expected_readout = (
            f"{len(matches)} matches"
            if case.count_readout is None
            else case.count_readout
        )
        assert plain_text(search_count) == expected_readout, (
            f"`#search-count` reads {plain_text(search_count)!r} after Enter on "
            f"{case.typed!r}; it is the only VISIBLE difference between a query "
            "that found nothing and one that was never typed"
        )

        if case.positioned:
            assert landing is not None
            assert _rendered_sub_title(screen) == _expected_status(
                case.typed, landing + 1, len(matches)
            )
        else:
            assert screen.sub_title == TranscriptScreen.SUB_TITLE
            assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE

        # THE ORDERING — see the docstring. Nothing else here can see it.
        if case.builds_the_row_map:
            assert builds, (
                f"Enter on {case.typed!r} never built the offset→row map, so it "
                "cannot have resolved where to jump"
            )
        else:
            assert builds == [], (
                f"Enter on an empty query built the offset→row map {len(builds)} "
                "time(s) before ending the search. The clear has to come FIRST "
                "— before `find_matches`, before `_row_starts` — or the whole "
                "wrap map (1,182 rows and 7.6 ms on the 88 KB committed game) "
                "is built and cached into a `SearchState` that ceases to exist "
                "two lines later. Every other assertion in this test passes "
                "either way, which is why this one is here"
            )

        # AND THE SAME DIFFERENCE AS THE REVIEWER EXPERIENCES IT: how many Escs
        # it takes to get back to the run. The clearing presses (if any) come
        # first and are asserted to actually clear.
        for _ in range(case.escapes - 1):
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, TranscriptScreen), (
                f"the first Esc after Enter on {case.typed!r} left the game; a "
                "search the reviewer can see costs one Esc to clear and keeps "
                "them in the text (§2.9 AC5)"
            )
            assert app.screen is screen
            assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
            assert screen._search is None
            assert _search_runs(_transcript_body_content(screen)) == []
            assert screen.sub_title == TranscriptScreen.SUB_TITLE

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen), (
            f"it took more than {case.escapes} Esc(s) to leave a game after "
            f"Enter on {case.typed!r}"
        )
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


async def _reopen_the_only_game(pilot) -> TranscriptScreen:
    """Open the run's single game again, from the :class:`DetailScreen`.

    The second half of :func:`_open_the_only_game`, split out because a reviewer
    coming back from a game is already on the run — there is no table row to
    drill into a second time.
    """
    await _highlight_game(pilot, 0)
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(pilot.app.screen, TranscriptScreen)
    assert len(pilot.app.screen_stack) == _TRANSCRIPT_STACK_DEPTH
    return pilot.app.screen


async def test_reopening_a_game_starts_with_no_search_and_nothing_highlighted(
    tmp_path: Path,
) -> None:
    """A game opened again is a game nobody has searched (§2.9 AC4, §2.10 AC1).

    Task 5.4 item (d). **Clean by construction today** — `DetailScreen` pushes a
    NEW `TranscriptScreen` per selection and `pop_screen` discards the old one,
    so there is no search to carry across and nothing to reset on the way out —
    and asserted anyway, for the reason construction is worth pinning: the
    promise is §2.10 AC1's, the construction is one refactor (a cached screen, a
    pooled one, a `SearchState` lifted to the app) away from being a different
    construction, and the failure mode is a reviewer opening a fresh game onto
    someone else's amber.

    `second is not first` is asserted first, because it is the fact the rest of
    the test rests on; if a future viewer reuses the screen instead, every
    assertion below becomes a statement about a reset rather than about a
    rebuild, and the reader deserves to be told which.

    The body is compared to the pure tokenizer's runs with STRICT equality — the
    same shape B14.2/B14.3 use — rather than only counting the search's tail:
    the question here is whether the reading view is exactly what spec 038
    renders, and a leaked span of any kind is the thing that answers no.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _SEARCH_MATCH_BODY, name="s44-reopen.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        first = await _open_the_only_game(pilot)

        # Search it, jump, and leave with the search still live — the state that
        # has the most to leak.
        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _BACK_OUT_QUERY)
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert first._search is not None
        assert (
            len(_search_runs(_transcript_body_content(first))) == _SEARCH_VOTE_MATCHES
        )

        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH

        second = await _reopen_the_only_game(pilot)
        assert second is not first, (
            "the viewer handed back the same screen object; this test's "
            "assertions are then about a search being RESET rather than about "
            "there never having been one, which is a different guarantee"
        )

        # No search, and nothing of the last one anywhere on the screen.
        assert second._search is None
        search_row = second.query_one("#search-row", Horizontal)
        assert "-open" not in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert second.query_one(TranscriptSearchInput).value == ""
        assert plain_text(second.query_one("#search-count", Static)) == ""
        assert second.sub_title == TranscriptScreen.SUB_TITLE
        assert _rendered_sub_title(second) == _TRANSCRIPT_SUB_TITLE

        content = _transcript_body_content(second)
        assert _rendered_runs(content) == _tokenizer_styled_runs(content.plain), (
            "the freshly opened game carries spans the tokenizer did not put "
            "there — a search highlight survived into a game nobody has "
            "searched (§2.9 AC4)"
        )

        # ...and it is the reading view §2.10 AC1 promises, down to the row.
        scroller = second.query_one("#transcript-scroll", VerticalScroll)
        assert second.focused is scroller
        assert scroller.scroll_offset.y == 0
        assert scroller.size.height == _LONG_GAME_VIEWPORT_ROWS
        assert scroller.max_scroll_y == _LONG_GAME_MAX_SCROLL_Y


# ---------------------------------------------------------------------------
# B15.8 The way to the next match is one you can find (spec 044, Slice 6)
# ---------------------------------------------------------------------------
#
# ORIGIN: the by-eye check in Task 5.5, and everything below was measured on the
# 88 KB committed game before any of it was written. The author opened a game,
# searched, and could not reach the following match:
#
#   * repeated `/` + Enter was a NO-OP LOOP — four presses, all resting on row
#     7, header fixed at `the · 1/702`. That is functional-spec §2.5 AC2 working
#     exactly as specified: the jump is INCLUSIVE of where the reviewer already
#     is, and after a jump they are already standing on a match.
#   * `n` had been correct the whole time — 7 -> 10 -> 13 -> 14, `1/702` through
#     `4/702`.
#   * nothing advertised it. The Footer carried `esc Back`, `/ Search` and
#     `^p palette`; the status said `the · 1/702` — a POSITION, never a key.
#     Slice 4 hid `n`/`N` on the explicit written grounds that the status line
#     would announce them, and the status line never did.
#
# So this sub-section asks two questions the rest of B15 cannot, and they are
# different in kind:
#
# 1. DOES ENTER STEP? — behaviour, driven by keystroke, and stated as an
#    EQUIVALENCE rather than as rows. `n` is the key that was always right, so
#    the assertion that carries §2.6's new criterion is that Enter walks the
#    SAME rows, the same matches and the same header strings that a parallel `n`
#    walk produces IN THE SAME TEST. Writing 7, 10, 13, 14, 15 into two lists
#    would pass for as long as nobody edited one of them.
# 2. CAN THE REVIEWER FIND IT? — discoverability, and the trap is that the
#    obvious assertion cannot see it. `Binding.make_bindings` normalises through
#    `show=bool(binding.description and binding.show)`
#    (textual/binding.py:161, Textual 8.2.4), so a binding with `show=True` and
#    an EMPTY DESCRIPTION is forced hidden — which is precisely the state Slice
#    4 shipped. A test reading `TranscriptScreen.BINDINGS` is reading the INPUT
#    to that rule and would pass against a Footer with nothing in it. The Footer
#    test below therefore reads the rendered `FooterKey` widgets, and the status
#    test reads `_rendered_sub_title`.
#
# THE REGRESSION THIS SLICE MOST RISKS is §2.5, and it has its own test. Enter
# now means two things, and which one a press gets is decided by FOCUS rather
# than by a flag: `App._check_bindings` walks `focused.ancestors_with_self`, so
# while the bar is open the `TranscriptSearchInput` consumes the key and posts
# `Input.Submitted` (the inclusive jump), and the screen-level `enter` binding
# is never consulted. If that resolution order were ever to change, Enter in the
# prompt would become EXCLUSIVE and row-keyed — a silent behaviour change that
# every other test in B15.4 would survive, because they all press Enter from
# positions where inclusive and exclusive give the same answer.


# How many presses each walk takes past its landing. Four, matching the four
# the author pressed by hand — and four is what reaches past the first row-mate
# skip in this game, so one walk witnesses an ordinary advance and a skip.
_ENTER_WALK_PRESSES = 4


class _WalkStep(NamedTuple):
    """Everything a reviewer can see after one press of a stepping key.

    All three, because each is a different way for the two keys to agree while
    the feature is broken: two walks that never moved share a `row`, two that
    moved identically but marked nothing share a `match`, and two that moved and
    marked correctly could still put different strings in the header.
    """

    row: int
    match: int | None
    status: str


def _walk_step(screen: TranscriptScreen, scroller: VerticalScroll) -> _WalkStep:
    """Read the three visible facts off the live screen."""
    return _WalkStep(
        row=scroller.scroll_offset.y,
        match=_current_match(_transcript_body_content(screen)),
        status=_rendered_sub_title(screen),
    )


async def _walk_from_a_landing(
    pilot, screen: TranscriptScreen, query: str, key: str, presses: int
) -> list[_WalkStep]:
    """Search, land with Enter, then press ``key`` ``presses`` times and record.

    The landing is element 0, so a caller comparing two walks is comparing their
    STARTS as well as their steps — two walks that began in different places
    would otherwise be free to disagree about everything and still line up.

    Every walk begins with `home` and ends with `escape`, which is what lets two
    of them run back-to-back in one `run_test()` session: `home` puts the
    reviewer at the top of the game again and `escape` clears the search behind
    them (Slice 5), so the second walk starts from the state the first one did
    rather than from the first one's leftovers. Both are ordinary reading keys
    pressed at the game, not the feature under test.

    Driven entirely through `pilot.press(...)` — including the typing, via
    `_type_transcript_search` — for B15's standing reason: this whole slice is a
    question about where a keypress LANDS, and a helper that set `Input.value`
    would answer none of it.
    """
    await pilot.press("home")
    await _settle_scroll(pilot)
    await pilot.press("slash")
    await pilot.pause()
    await _type_transcript_search(pilot, query)
    await _settle_search(pilot)

    # The landing is the OPEN-PROMPT Enter — §2.5's inclusive jump — whichever
    # key the walk itself is made of. That is the state §2.6's new criterion
    # begins from: "given the reviewer has jumped to a match with Enter and the
    # prompt has closed".
    await pilot.press("enter")
    await pilot.pause()
    scroller = screen.query_one("#transcript-scroll", VerticalScroll)
    walk = [_walk_step(screen, scroller)]

    for _ in range(presses):
        await pilot.press(key)
        await pilot.pause()
        walk.append(_walk_step(screen, scroller))

    await pilot.press("escape")
    await pilot.pause()
    assert screen._search is None, (
        "the walk did not clean up after itself — the next walk in this session "
        "would start on this one's highlighting"
    )
    return walk


@_requires_real_game
async def test_repeated_enter_with_the_prompt_closed_walks_exactly_as_n_does(
    tmp_path: Path,
) -> None:
    """Enter steps like `n`, and the two can never drift (§2.6's new AC).

    "Given the reviewer has jumped to a match with Enter and the prompt has
    closed, when they press Enter again, then the view moves to the following
    match exactly as `n` would, and keeps moving on each further press."

    **The expectation is the OTHER WALK, produced in this same test**, not a row
    list. `n` is the key that was always right, so "exactly as `n` would" is
    literally the assertion: both walks are recorded here, one after the other,
    on the same game with the same query from the same starting row, and the two
    lists must be equal. Writing the measured rows (7, 10, 13, 14, 15) into two
    hand-copied lists would pass for exactly as long as nobody edited one of
    them — which is the failure mode the criterion is about.

    **Equality alone would not be enough, and this is the mutation that says
    so.** Two walks that both did nothing are equal; so are two that both ran
    backwards. Three further assertions close that:

    - the walk MOVED — more than one distinct row among the five;
    - it moved FORWARDS, one row-keyed step at a time, against `_expected_walk`
      derived from the match list and the row map rather than from the stepping
      code under test;
    - and the header counted along with it, through `_expected_status`.

    Pointing the `enter` binding at `previous_match` therefore fails on the
    third assertion rather than surviving as "the view moved", and removing the
    binding fails on the first.

    **The real game, because the synthetic fixtures cannot ask this honestly.**
    `_JUMP_BODY` has three matches on three rows; this game has 702 across 1,182
    rows with row-mates among them, which is where "exactly as `n` would" has
    something to be wrong about. No count is written down: the game is
    discovered, the total comes out of `find_matches`, and the walk is derived.
    """
    text = _WRAP_PIN_REAL_GAME.read_text(encoding="utf-8")
    ledger, _ = _ledger_with_body(tmp_path, text, name="s44-enter-walk.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen, scroller, body = await _open_the_real_game(pilot)
        matches, _, rows = _match_rows(body, _TAIL_QUERY)
        total = len(matches)

        # The premise: enough matches above the clamp for the walk to be the
        # STEP's answer at every press rather than the end of the game's.
        assert total > _ENTER_WALK_PRESSES
        assert rows[_ENTER_WALK_PRESSES] < scroller.max_scroll_y, (
            f"the first {_ENTER_WALK_PRESSES + 1} matches of "
            f"`{_TAIL_QUERY}` do not clear `max_scroll_y` "
            f"{scroller.max_scroll_y} — this walk would be measuring the clamp"
        )

        by_n = await _walk_from_a_landing(
            pilot, screen, _TAIL_QUERY, "n", _ENTER_WALK_PRESSES
        )
        by_enter = await _walk_from_a_landing(
            pilot, screen, _TAIL_QUERY, "enter", _ENTER_WALK_PRESSES
        )

        assert by_enter == by_n, (
            "Enter with the prompt closed must walk exactly as `n` does — same "
            "rows, same current match, same header. `n` walked "
            f"{[step.row for step in by_n]} and Enter walked "
            f"{[step.row for step in by_enter]}"
        )

        # NON-VACUITY, in three parts — see the docstring.
        assert len({step.row for step in by_n}) > 1, (
            f"every press rested on row {by_n[0].row}: two walks that both did "
            "nothing are equal, so the equality above proved nothing"
        )
        landing = by_n[0].match
        assert landing is not None
        expected = [landing] + _expected_walk(
            rows, rows[landing], _ENTER_WALK_PRESSES
        )
        assert [step.match for step in by_n] == expected, (
            "the walk is not the one §2.6 describes — each press must go to the "
            "next ROW that has a match and land on that row's first match"
        )
        assert [step.status for step in by_n] == [
            _expected_status(_TAIL_QUERY, index + 1, total) for index in expected
        ]
        # ...and every landing really is a row holding the query.
        for step in by_n:
            assert _TAIL_QUERY in body.render_line(step.row).text.lower()


async def test_enter_inside_the_open_prompt_still_confirms_and_jumps(
    tmp_path: Path,
) -> None:
    """The regression Slice 6 most risks, in the one shape that can see it.

    §2.6: "Enter *inside* the search prompt is unchanged — it confirms what has
    been typed and jumps, as §2.5 describes." Enter now has a second meaning as
    a screen binding onto `next_match`, and which meaning a press gets is
    decided by FOCUS, not by a flag: `App._check_bindings` walks
    `focused.ancestors_with_self`, so the focused `TranscriptSearchInput`
    consumes the key and posts `Input.Submitted` before the screen is consulted.
    That claim is the whole design, so it is driven rather than assumed — and
    the focus that makes it true is asserted immediately before the press, so a
    failure says which of the two facts broke.

    **The reviewer is put on a match row first, and that is what gives this test
    teeth.** §2.5's jump is INCLUSIVE and `n`'s step is row-keyed and EXCLUSIVE,
    and `_JUMP_BODY`'s third match at row 45 exists for no other purpose than to
    be where the exclusive answer lands. From row 30 — a row that already holds
    a match, beginning at its first character — the two meanings of Enter
    disagree visibly: inclusive stays, exclusive moves to 45. Pressed anywhere
    else in this game the two agree and the test proves nothing, which is why
    the premise is asserted rather than left to the fixture.

    **Both halves of the prompt's Enter are covered**, because Slice 6 put a
    screen binding under both: the typed-query jump above, and Task 5.2a's empty
    Enter, which must still CLEAR the search rather than step. `screen._search`
    is read for that one — B15's banner reserves that to the case where the
    distinction is invisible on screen, and this is exactly it: a surviving
    `SearchState(query="")` and no search at all look identical, and differ only
    in whether the next Esc leaves the game. So the Esc is pressed too.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-enter-inbar.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)
        body = screen.query_one("#transcript-body", TranscriptBody)
        search_row = screen.query_one("#search-row", Horizontal)

        # Scroll onto the match at row 30 with the ordinary reading keys, so the
        # premise owes nothing to the feature under test.
        await pilot.press(
            "pagedown",
            *["down"] * (_JUMP_MATCH_ROWS[1] - _LONG_GAME_VIEWPORT_ROWS),
        )
        await _settle_scroll(pilot)

        # THE PREMISES, all three of which this test is blind without: the
        # reviewer is looking AT a match; that match begins at the first
        # character of the top row (inclusive and exclusive differ only there);
        # and there is a further match below for an exclusive Enter to reach.
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[1]
        assert body.render_line(_JUMP_MATCH_ROWS[1]).text.startswith(_JUMP_QUERY)
        assert _JUMP_MATCH_ROWS[2] < _SEARCH_OPEN_MAX_SCROLL_Y

        await pilot.press("slash")
        await pilot.pause()
        search_input = await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)

        # The routing precondition, asserted at the moment it matters.
        assert "-open" in search_row.classes
        assert screen.focused is search_input, (
            "the bar is not focused, so the Enter below is not the one this "
            "test is about — it would reach the screen binding by default "
            "rather than by the resolution order under test"
        )

        await pilot.press("enter")
        await pilot.pause()

        # IT JUMPED, AND IT JUMPED INCLUSIVELY (§2.5 AC2).
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[1], (
            "Enter in the open prompt must still be §2.5's INCLUSIVE jump: a "
            "match already on the top row is where it stops. Landing on "
            f"{_JUMP_MATCH_ROWS[2]} means the screen's `enter` binding reached "
            "the focused bar and Enter has quietly become `n`"
        )
        # ...IT CONFIRMED (the prompt is gone and the reviewer is reading)...
        assert "-open" not in search_row.classes
        assert tuple(search_row.size) == _SEARCH_ROW_CLOSED_SIZE
        assert screen.focused is scroller
        # ...and the header says where they landed: the SECOND match, because
        # the one on row 10 is above them.
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 2, _JUMP_MATCHES
        )
        assert _current_match(_transcript_body_content(screen)) == 1

        # --- Task 5.2a's empty Enter, which must still CLEAR ----------------
        await pilot.press("escape")
        await pilot.pause()
        assert screen._search is None
        resting = scroller.scroll_offset.y

        await pilot.press("slash")
        await pilot.pause()
        assert "-open" in search_row.classes
        assert screen.focused is search_input
        assert search_input.value == ""

        await pilot.press("enter")
        await pilot.pause()

        assert screen._search is None, (
            "an Enter on nothing typed must END the search (Task 5.2a). A "
            "`SearchState(query='')` left alive behind a hidden bar is a state "
            "the reviewer cannot see, and the next Esc is silently spent "
            "clearing it — against §2.9 AC3"
        )
        assert "-open" not in search_row.classes
        assert scroller.scroll_offset.y == resting
        assert _search_runs(_transcript_body_content(screen)) == []
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE

        # ...and it really did cost them no extra keypress: ONE Esc leaves.
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert len(app.screen_stack) == _DETAIL_STACK_DEPTH


@pytest.mark.parametrize("state", ["no-search", "a-query-that-matches-nothing"])
async def test_enter_while_reading_does_nothing_when_there_is_nothing_to_step_to(
    tmp_path: Path, state: str
) -> None:
    """Enter with nowhere to go is a no-op, in both of its shapes (§2.6, §2.8 AC4).

    Two criteria, one body:

    - "Given no search has been made since the game was opened, when the
      reviewer presses Enter while reading, then nothing happens and no error
      appears."
    - "Given a search is live whose text matches nothing in the game, when the
      reviewer presses Enter while reading, then nothing happens and no error
      appears."

    They are separate cases because they fail separately: the first is guarded
    by `_step_match` returning on `self._search is None`, the second by the pure
    step answering `None` for an empty match list. A `next_match` that reached
    for `matches[0]` without the second guard passes the first case and raises
    on the second — and an `IndexError` inside a Textual message handler does
    not surface as a failed assertion here, it surfaces as the app swapping
    itself for the unhandled-exception screen. Which is why "no error appears"
    is read off the APP — still running, still this screen, still this stack
    depth — rather than off the absence of a raise at the call site.

    `TranscriptScreen` had no `enter` binding before Slice 6, so there is no
    prior behaviour being preserved here; these assertions describe the
    behaviour the binding must NOT introduce.

    Pressed twice from a SCROLLED position, both deliberate: from row 0 an Enter
    that scrolled home would be invisible, and a first press that established
    some state with a second that acted on it is a different bug from one press
    acting.
    """
    ledger, _ = _ledger_with_body(
        tmp_path, _JUMP_BODY, name=f"s44-enter-noop-{state}.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("pagedown")
        await _settle_scroll(pilot)
        resting = scroller.scroll_offset.y
        assert resting == _JUMP_START_ROW

        if state == "a-query-that-matches-nothing":
            await pilot.press("slash")
            await pilot.pause()
            await _type_transcript_search(pilot, _SEARCH_NO_MATCH_QUERY)
            await _settle_search(pilot)
            await pilot.press("enter")
            await pilot.pause()
            # The state this case is about: a search the reviewer really made,
            # which found nothing. "I asked and there was nothing" is not "I
            # did not ask" (§2.9 AC5), so it must still be alive.
            assert screen._search is not None
            assert scroller.scroll_offset.y == resting

        # Both cases arrive here reading the game, which is what puts the
        # screen's `enter` binding in the path of the next press.
        assert screen.focused is scroller
        assert _search_runs(_transcript_body_content(screen)) == []

        await pilot.press("enter", "enter")
        await pilot.pause()

        assert scroller.scroll_offset.y == resting, (
            f"Enter moved the game from row {resting} to "
            f"{scroller.scroll_offset.y} with nothing to step to"
        )
        assert _search_runs(_transcript_body_content(screen)) == []
        assert _rendered_sub_title(screen) == _TRANSCRIPT_SUB_TITLE, (
            "a press that wrote a position — or `0/0` — into the header is a "
            "visible change out of a key that promises none"
        )
        assert app.is_running
        assert app.screen is screen
        assert len(app.screen_stack) == _TRANSCRIPT_STACK_DEPTH


async def test_the_footer_names_the_stepping_keys_with_what_each_one_does(
    tmp_path: Path,
) -> None:
    """The keys say they exist, read off the RENDERED Footer (§2.6's new AC).

    "Given the reviewer is reading a game, when they look at the key hints at
    the foot of the screen, then `n` and `N` are named there together with what
    each does."

    **Asserted through the `FooterKey` widgets, and reading `BINDINGS` instead
    would be a tautology with a blind spot.** The binding is the INPUT to the
    thing under test: `Binding.make_bindings` normalises every declaration
    through `show=bool(binding.description and binding.show)`
    (textual/binding.py:161, Textual 8.2.4), so a binding declared `show=True`
    with an empty description is forced hidden and renders no cell at all. That
    is not a hypothetical — it is the exact state Slice 4 shipped and the reason
    this slice exists. Only the rendered side can see it.

    `key` AND `description` are both read, because the criterion is "named
    there TOGETHER WITH WHAT EACH DOES": a cell rendering a bare `n` with a
    blank description would satisfy "n is in the Footer" and answer none of the
    reviewer's question. `FooterKey.render` has a branch for exactly that case.

    **Stated as the COMPLETE rendered set**, which pins the other half of this
    slice's Footer decision: `enter` is a SYNONYM for `n` on the same action, and
    `Footer.compose` groups by action and renders only the first binding of each
    group. A shown `enter` entry would therefore either be dropped as a
    duplicate or — declared above `n` — REPLACE `n`'s cell with one named
    `enter`, silently undoing the criterion. Its absence here is what says it
    cannot. `escape` and `backspace` share a cell for the same grouping reason,
    and `q` is hidden as it always has been.

    The terminal is the tests' 100 columns, where all five cells fit: measured,
    they are 58 columns of it.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-footer-keys.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)

        footer = screen.query_one(Footer)
        rendered = {cell.key: cell.description for cell in footer.query(FooterKey)}

        assert rendered == {
            "escape": "Back",
            "slash": "Search",
            "n": "Next match",
            "N": "Prev match",
            "ctrl+p": "palette",
        }, (
            "the Footer a reviewer reading a game actually sees. `n`/`N` "
            "missing from it is Slice 4's blank-description trap — `show=True` "
            "with no description renders nothing; an `enter` cell in it has "
            "taken `n`'s place through `Footer.compose`'s action grouping"
        )

        # ...and every cell really is on screen, not merely composed.
        for cell in screen.query_one(Footer).query(FooterKey):
            assert cell.size.width > 0, (
                f"the `{cell.key}` cell renders zero columns wide — it is in "
                "the widget tree and not in the reviewer's Footer"
            )


async def test_the_running_status_names_the_keys_that_step_between_matches(
    tmp_path: Path,
) -> None:
    """The announcement Slice 4 promised and never built (§2.8's new AC).

    "Given the reviewer has jumped to a match, when they look at the running
    status, then it names the keys that step to the next and to the previous
    match."

    **Read through `_rendered_sub_title`, so the whole header chain is
    exercised** — `Screen.sub_title` is a reactive, `Header._on_mount` watches
    it, the watcher rebuilds through `App.format_title`, and `HeaderTitle` is a
    one-row `Static` with `text-overflow: ellipsis`. That last link is the one
    this slice put under pressure: the hint is new text on a row that was
    already carrying the viewer's name, the query and the position, and a hint
    that pushed the line into the ellipsis would be a hint nobody can read. The
    helper asserts the rendered row equals the reactive, so truncation fails
    here rather than passing as a string comparison against a value the widget
    never showed.

    **Field order is asserted, not just membership.** The ellipsis cuts the END,
    so the LAST field is the first one a narrow terminal spends. The position is
    a §2.8 acceptance criterion and is readable nowhere else once the prompt has
    closed; the hint is a convenience the Footer now also carries. A build that
    put the hint before the position would satisfy "the status names the keys"
    and would spend the position first on an 80-column terminal.

    **And the keys it names are pressed.** A status that said `n next · N prev`
    over a screen where `n` did nothing would be worse than saying nothing, and
    no string comparison can tell the difference — so the two key names are
    taken OUT of the status text and pressed, and the view is asserted to go
    forward and then back. That is what makes this a test of an announcement
    rather than of a spelling.
    """
    ledger, _ = _ledger_with_body(tmp_path, _JUMP_BODY, name="s44-status-keys.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test(size=_LONG_GAME_TERMINAL_SIZE) as pilot:
        await pilot.pause()
        screen = await _open_the_only_game(pilot)
        scroller = screen.query_one("#transcript-scroll", VerticalScroll)

        await pilot.press("slash")
        await pilot.pause()
        await _type_transcript_search(pilot, _JUMP_QUERY)
        await _settle_search(pilot)
        await pilot.press("enter")
        await pilot.pause()

        # The landing, and the whole status in one strict equality.
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[0]
        status = _rendered_sub_title(screen)
        assert status == _expected_status(_JUMP_QUERY, 1, _JUMP_MATCHES)

        # THE FIELDS, IN ORDER — the hint is last, after the position.
        query_field, position_field, *hint_fields = status.split(" · ")
        assert query_field == _JUMP_QUERY
        assert position_field == f"1/{_JUMP_MATCHES}"
        assert hint_fields == [
            _SEARCH_STATUS_NEXT_HINT,
            _SEARCH_STATUS_PREV_HINT,
        ], (
            f"the status reads {status!r}. §2.8 asks it to name the keys that "
            "step to the next AND to the previous match, and the two names must "
            "come after the position — `HeaderTitle` truncates from the end, so "
            "whatever is last is what a narrow terminal spends first"
        )

        # THE KEYS IT NAMES ARE THE KEYS THAT DO IT. Taken out of the announced
        # text rather than written down again, so a status naming some other key
        # fails here instead of passing a spelling check.
        named_next, next_does = _SEARCH_STATUS_NEXT_HINT.split()
        named_prev, prev_does = _SEARCH_STATUS_PREV_HINT.split()
        assert (next_does, prev_does) == ("next", "prev"), (
            "each field must say which way its key goes; naming the pair "
            "without the direction leaves the reviewer to guess, and the guess "
            "costs them a press backwards"
        )

        await pilot.press(named_next)
        await pilot.pause()
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[1], (
            f"the status advertises `{named_next}` as the key to the next "
            "match; pressing it did not go there"
        )
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 2, _JUMP_MATCHES
        )

        await pilot.press(named_prev)
        await pilot.pause()
        assert scroller.scroll_offset.y == _JUMP_MATCH_ROWS[0], (
            f"the status advertises `{named_prev}` as the key to the previous "
            "match; pressing it did not go back"
        )
        assert _rendered_sub_title(screen) == _expected_status(
            _JUMP_QUERY, 1, _JUMP_MATCHES
        )
