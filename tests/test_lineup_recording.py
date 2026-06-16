"""Offline tests for recording the configured lineup (spec 014, Slice 3 Task 3).

Locks in the final increment of spec 014 — *recording* the whole-table lineup in
an eval run's ``settings`` block and *surfacing* it in the read-only ledger
viewer — across the two pure layers it touches, **without ever reaching a real
model, the network, or a live game**:

1. **render_record** (``graphia.tools.blunder_eval``) — a synthetic
   ``EvalResult`` whose ``settings.lineup`` carries ``{num_citizens, num_mafia}``
   renders a ``lineup:`` sub-map nested under ``settings:`` (both keys, bare
   ints), and that sub-map ``yaml.safe_load``-round-trips to the exact dict. A
   bare ``EvalResult`` with no lineup omits the ``lineup:`` line entirely
   (pre-014 back-compat — no migration).

2. **The lineup env overrides + the Slice-1 fail-fast guard**
   (``_apply_lineup_overrides`` + ``graphia.config.load_config``) — the helper
   routes ``--citizens`` / ``--mafia`` onto ``GRAPHIA_NUM_CITIZENS`` /
   ``GRAPHIA_NUM_MAFIA`` *before* ``load_config`` reads them, so a valid pair
   flows through to the resolved config and an invalid one (e.g. ``citizens=0``)
   is rejected by the same single config choke point with a ``SystemExit`` — no
   separate CLI validation. ``None`` overrides leave the env untouched so the
   per-var default (today's 5 + 2) stands.

3. **The eval-ledger flatten + viewer** (``graphia.eval_ledger`` +
   ``graphia.ui.ledger_viewer``) — a record carrying ``settings.lineup`` flattens
   to a ``Lineup`` cell of ``"5/2"``, a ``citizens``/``mafia`` pair in the detail
   ``settings`` section, and a ``"5c2m"`` token in its search blob; a **pre-014**
   record (no ``settings.lineup``) renders the blank cell / ``—`` lines / no
   token — and the present-vs-absent states render **differently** (the whole
   point of the absent-blank distinction). A Pilot (``App.run_test()``) test
   asserts the ``Lineup`` column header is in the DataTable and the drill-down
   detail carries the counts — always over an injected ``tmp_path`` ledger, never
   the committed one.

The synthetic ``EvalResult`` is built from the real dataclass and every real
helper/symbol is imported, so a field/key rename breaks these tests honestly.
Everything is stubbed and offline: no provider client is built, no socket is
touched, and the autouse ``safe_llm`` net is left intact.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
from textual.widgets import DataTable, Static

from graphia.config import load_config
from graphia.eval_ledger import (
    METRIC_ORDER,
    build_table_model,
    load_ledger,
    render_detail,
    _lineup_cell,
    _lineup_keyword,
    _render_settings_section,
    _search_blob,
)
from graphia.tools.blunder_eval import (
    EvalResult,
    _apply_lineup_overrides,
    render_record,
)
from graphia.ui.ledger_viewer import DetailScreen, LedgerViewerApp

from conftest import plain_text

# The two lineup env vars ``_apply_lineup_overrides`` writes and ``load_config``
# reads — kept as module constants so the env-isolation and assertions read off
# one source of truth.
_CITIZENS_ENV = "GRAPHIA_NUM_CITIZENS"
_MAFIA_ENV = "GRAPHIA_NUM_MAFIA"


# ===========================================================================
# 1. render_record — the settings.lineup sub-map (present + absent)
# ===========================================================================


def _result_with_lineup(num_citizens: int, num_mafia: int) -> EvalResult:
    """A populated ``EvalResult`` whose ``settings`` carries a ``lineup`` sub-map.

    Mirrors the real ``run_eval`` settings shape (flat scalar keys plus the
    nested ``lineup`` map), so the renderer is exercised against the exact
    structure a real run produces.
    """
    return EvalResult(
        provider="bedrock",
        large_model="us.amazon.nova-pro-v1:0",
        small_model="us.amazon.nova-lite-v1:0",
        games_attempted=5,
        games_completed=5,
        games_failed_early=0,
        settings={
            "large_model": "us.amazon.nova-pro-v1:0",
            "small_model": "us.amazon.nova-lite-v1:0",
            "base_url": None,
            "games": 5,
            "seed": None,
            "max_rounds": 3,
            "lineup": {"num_citizens": num_citizens, "num_mafia": num_mafia},
        },
        metrics={"repetition": {"rate": 0.4, "count": 2, "denominator": 5}},
    )


def _settings_block_lines(doc: str) -> list[str]:
    """The lines of the ``settings:`` block (header through the next top-level key).

    A top-level key is an unindented ``key:`` line; the ``settings`` block runs
    from its header up to (not including) the next unindented line, so the nested
    ``lineup`` sub-map can be located unambiguously inside it.
    """
    lines = doc.splitlines()
    start = lines.index("settings:")
    end = start + 1
    while end < len(lines) and (lines[end] == "" or lines[end].startswith(" ")):
        end += 1
    return lines[start:end]


def test_render_record_emits_lineup_submap_under_settings() -> None:
    """A ``settings.lineup`` renders a ``lineup:`` sub-map (both keys) under settings."""
    doc = render_record(_result_with_lineup(5, 2), "2026-06-16")
    block = _settings_block_lines(doc)

    # The sub-map header is nested one level under ``settings:`` (two spaces) and
    # its two keys are nested one level deeper (four spaces) as bare ints.
    assert "  lineup:" in block
    header_i = block.index("  lineup:")
    assert block[header_i + 1] == "    num_citizens: 5"
    assert block[header_i + 2] == "    num_mafia: 2"


def test_render_record_lineup_round_trips_through_yaml() -> None:
    """The rendered ``settings.lineup`` ``yaml.safe_load``s back to the exact dict.

    Stronger than a substring match — proves the hand-rendered sub-map is genuine
    well-formed YAML nested at the right depth, not merely text that string-matches.
    """
    yaml = pytest.importorskip("yaml")

    doc = render_record(_result_with_lineup(5, 2), "2026-06-16")
    parsed = yaml.safe_load(doc)

    assert parsed["settings"]["lineup"] == {"num_citizens": 5, "num_mafia": 2}


def test_render_record_without_lineup_omits_the_submap() -> None:
    """A bare ``EvalResult`` (no ``settings.lineup``) renders no ``lineup:`` line.

    Back-compat: a pre-014 run carried no lineup, so the renderer must omit the
    sub-map entirely rather than emitting a half-populated / null one.
    """
    result = EvalResult(
        provider="ollama",
        metrics={"repetition": {"rate": 0.0, "count": 0, "denominator": 0}},
    )

    doc = render_record(result, "2026-06-16")

    assert "lineup:" not in doc
    assert "num_citizens" not in doc
    assert "num_mafia" not in doc


@pytest.mark.parametrize(
    "citizens, mafia", [(5, 2), (7, 3), (4, 1)], ids=["5-2", "7-3", "4-1"]
)
def test_render_record_lineup_round_trips_for_various_pairs(
    citizens: int, mafia: int
) -> None:
    """Several valid lineups each round-trip through the renderer to their dict."""
    yaml = pytest.importorskip("yaml")

    doc = render_record(_result_with_lineup(citizens, mafia), "2026-06-16")
    parsed = yaml.safe_load(doc)

    assert parsed["settings"]["lineup"] == {
        "num_citizens": citizens,
        "num_mafia": mafia,
    }


# ===========================================================================
# 2. _apply_lineup_overrides + the Slice-1 fail-fast guard in load_config
# ===========================================================================
#
# The overrides set GRAPHIA_NUM_CITIZENS / GRAPHIA_NUM_MAFIA env *before*
# load_config reads them; there is deliberately no separate CLI validation, so a
# bad lineup is caught by the same single config choke point with a SystemExit.
# monkeypatch.setenv/delenv isolates the env so no developer .env leaks in and
# nothing leaks out.


@pytest.fixture(autouse=True)
def _isolate_lineup_env():
    """Clear the lineup env vars before each test and FULLY restore os.environ after.

    ``_apply_lineup_overrides`` mutates ``os.environ`` **directly**
    (``os.environ[KEY] = ...``), not through ``monkeypatch`` — that direct
    mutation is the behaviour under test. A ``monkeypatch.delenv`` of a var that
    started unset records nothing to undo, so a value the helper then sets
    directly would **leak** into later tests (e.g. the vote-validation suite,
    which builds its roster from ``load_config`` and would see a changed lineup).

    So this mirrors ``test_blunder_eval.py``'s ``blunder_env_clean``: a full
    snapshot-and-restore of ``os.environ`` around each test, on top of a targeted
    wipe of the two lineup vars for a clean starting slate that no developer
    ``.env`` leakage can taint.
    """
    saved = dict(os.environ)
    os.environ.pop(_CITIZENS_ENV, None)
    os.environ.pop(_MAFIA_ENV, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_apply_lineup_overrides_sets_env_and_config_resolves_them() -> None:
    """A valid pair flows env → ``load_config`` → resolved ``num_citizens/num_mafia``."""
    _apply_lineup_overrides(4, 1)

    # The helper wrote the two env vars the config choke point reads.
    assert os.environ[_CITIZENS_ENV] == "4"
    assert os.environ[_MAFIA_ENV] == "1"

    config = load_config()

    assert (config.num_citizens, config.num_mafia) == (4, 1)


def test_apply_lineup_overrides_invalid_lineup_fails_fast_in_load_config() -> None:
    """``citizens=0`` is rejected by the Slice-1 guard — ``load_config`` ``SystemExit``s.

    There is NO separate CLI check: the override validates through the same
    fail-fast guard a bad ``.env`` would hit (``num_citizens`` must be >= 1).
    """
    _apply_lineup_overrides(0, 5)

    with pytest.raises(SystemExit):
        load_config()


def test_apply_lineup_overrides_mafia_not_fewer_than_citizens_fails_fast() -> None:
    """``mafia >= citizens`` is also caught by the same guard (no CLI pre-check)."""
    _apply_lineup_overrides(3, 3)

    with pytest.raises(SystemExit):
        load_config()


def test_apply_lineup_overrides_none_leaves_env_untouched_defaults_stand() -> None:
    """``None`` overrides write neither env var, so the per-var default wins."""
    _apply_lineup_overrides(None, None)

    assert _CITIZENS_ENV not in os.environ
    assert _MAFIA_ENV not in os.environ

    # With nothing set, the config resolves to the documented defaults (5 + 2).
    config = load_config()
    assert (config.num_citizens, config.num_mafia) == (5, 2)


def test_apply_lineup_overrides_single_flag_only_sets_that_var() -> None:
    """Setting only ``citizens`` leaves the ``mafia`` env var untouched (default)."""
    _apply_lineup_overrides(6, None)

    assert os.environ[_CITIZENS_ENV] == "6"
    assert _MAFIA_ENV not in os.environ


# ===========================================================================
# 3. eval_ledger flatten + viewer — Lineup cell, detail section, search keyword
# ===========================================================================
#
# Real on-disk shapes (mirroring tests/test_ledger_model.py): a *with-lineup*
# record carries settings.lineup; a *pre-014* record has a settings block but no
# lineup sub-map (no migration). Every fixture is written to tmp_path; the
# committed ledger is never read or written.

_WITH_LINEUP_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-16'
      duration_seconds: 100.0
      metrics_version: 1
    code:
      commit: '0123456789abcdef0123456789abcdef01234567'
      branch: 'main'
      dirty: false
    provider:
      name: 'bedrock'
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
    settings:
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
      games: 5
      seed: null
      lineup:
        num_citizens: 5
        num_mafia: 2
    quality:
      games_attempted: 5
      games_completed: 5
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.5
        count: 10
        denominator: 20
    notes: 'with-lineup record'
    """
)

# A *pre-014* record: a present ``settings`` block but NO ``lineup`` sub-map —
# the never-recorded-lineup state that must stay distinct from any present value.
_PRE_014_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-15'
      duration_seconds: 90.0
      metrics_version: 1
    code:
      commit: 'aaaa1111bbbb2222cccc3333dddd4444eeee5555'
      branch: 'main'
      dirty: false
    provider:
      name: 'bedrock'
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
    settings:
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
      games: 5
      seed: null
    quality:
      games_attempted: 5
      games_completed: 5
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.5
        count: 10
        denominator: 20
    notes: 'pre-014 record without a lineup'
    """
)


def _write_ledger(tmp_path: Path, *docs: str, name: str = "ledger.yaml") -> Path:
    """Write a ``---``-separated multi-document ledger to a temp file."""
    text = "".join(f"---\n{doc}" for doc in docs)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _record(tmp_path: Path, doc: str) -> dict:
    """Parse one document into its raw record via the real ``load_ledger``."""
    (record,) = load_ledger(_write_ledger(tmp_path, doc))
    return record


def test_lineup_cell_present_renders_citizens_slash_mafia(tmp_path: Path) -> None:
    """A record with ``settings.lineup`` → the compact ``"5/2"`` Lineup cell."""
    assert _lineup_cell(_record(tmp_path, _WITH_LINEUP_DOC)) == "5/2"


def test_lineup_cell_absent_is_blank(tmp_path: Path) -> None:
    """A pre-014 record (no ``settings.lineup``) → the empty-string Lineup cell."""
    assert _lineup_cell(_record(tmp_path, _PRE_014_DOC)) == ""


def test_lineup_cell_present_and_absent_render_differently(tmp_path: Path) -> None:
    """The whole point: a recorded lineup and a never-recorded one must differ."""
    present = _lineup_cell(_record(tmp_path, _WITH_LINEUP_DOC))
    absent = _lineup_cell(_record(tmp_path, _PRE_014_DOC))

    assert present != absent
    assert present == "5/2"
    assert absent == ""


def test_settings_section_shows_citizens_and_mafia_when_present(
    tmp_path: Path,
) -> None:
    """The detail ``settings`` section lists ``citizens: 5`` / ``mafia: 2``."""
    section = _render_settings_section(_record(tmp_path, _WITH_LINEUP_DOC))

    assert "  citizens: 5" in section
    assert "  mafia: 2" in section


def test_settings_section_shows_dash_for_absent_lineup(tmp_path: Path) -> None:
    """A pre-014 record shows the ``—`` em-dash for both lineup fields (no migration)."""
    section = _render_settings_section(_record(tmp_path, _PRE_014_DOC))

    assert "  citizens: —" in section
    assert "  mafia: —" in section


def test_settings_section_present_and_absent_render_differently(
    tmp_path: Path,
) -> None:
    """The detail section distinguishes a recorded lineup from a never-recorded one."""
    present = _render_settings_section(_record(tmp_path, _WITH_LINEUP_DOC))
    absent = _render_settings_section(_record(tmp_path, _PRE_014_DOC))

    assert present != absent


def test_lineup_keyword_present_is_the_search_token(tmp_path: Path) -> None:
    """``_lineup_keyword`` yields the ``"5c2m"`` search-friendly token when present."""
    assert _lineup_keyword(_record(tmp_path, _WITH_LINEUP_DOC)) == "5c2m"


def test_lineup_keyword_absent_is_blank(tmp_path: Path) -> None:
    """A pre-014 record yields no lineup keyword (so it neither matches nor pollutes)."""
    assert _lineup_keyword(_record(tmp_path, _PRE_014_DOC)) == ""


def test_search_blob_carries_the_lineup_keyword_when_present(tmp_path: Path) -> None:
    """The with-lineup record's search blob contains ``5c2m``; the pre-014 one does not."""
    with_lineup_blob = _search_blob(_record(tmp_path, _WITH_LINEUP_DOC))
    pre_014_blob = _search_blob(_record(tmp_path, _PRE_014_DOC))

    assert "5c2m" in with_lineup_blob
    assert "5c2m" not in pre_014_blob


def test_build_table_model_lineup_column_carries_the_cell(tmp_path: Path) -> None:
    """End-to-end through ``build_table_model``: the ``Lineup`` column row cells.

    Confirms the flattener places the lineup cell in the ``Lineup`` column for a
    with-lineup row and blanks it for a pre-014 row (the two render differently),
    so the per-row cell agrees with the standalone ``_lineup_cell``.
    """
    records = load_ledger(_write_ledger(tmp_path, _WITH_LINEUP_DOC, _PRE_014_DOC))
    model = build_table_model(records)
    lineup_col = model.columns.index("Lineup")

    assert model.rows[0][lineup_col] == "5/2"
    assert model.rows[1][lineup_col] == ""
    # The metric columns still trail the fixed columns (Lineup is a fixed column).
    assert model.columns[lineup_col:] != [label for _, label in METRIC_ORDER]


# ---------------------------------------------------------------------------
# Pilot (App.run_test) — the Lineup column header and the drill-down counts.
# Mirrors tests/test_ledger_viewer.py; the ledger path is always tmp_path.
# ---------------------------------------------------------------------------


async def test_viewer_shows_lineup_column_header(tmp_path: Path) -> None:
    """The DataTable's column headers include ``Lineup`` (the fixed column)."""
    ledger = _write_ledger(tmp_path, _WITH_LINEUP_DOC, _PRE_014_DOC)
    expected = build_table_model(load_ledger(ledger))

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)

        header_labels = [str(col.label) for col in table.columns.values()]
        assert "Lineup" in header_labels
        # The viewer's headers match the data layer model exactly.
        assert header_labels == expected.columns

        # The with-lineup row carries the "5/2" cell in the Lineup column.
        lineup_col = expected.columns.index("Lineup")
        cell = table.get_cell_at((0, lineup_col))
        plain = cell.plain if hasattr(cell, "plain") else str(cell)
        assert plain == "5/2"


async def test_viewer_drilldown_shows_lineup_counts(tmp_path: Path) -> None:
    """Selecting the with-lineup row opens a DetailScreen showing the lineup counts.

    Drives the real RowSelected key path (the table holds focus on mount), then
    reads the detail body — which must carry the ``citizens: 5`` / ``mafia: 2``
    lines from the record's ``settings`` section.
    """
    ledger = _write_ledger(tmp_path, _WITH_LINEUP_DOC, name="drill.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        table.focus()
        await pilot.pause()
        assert table.row_count == 1

        # Open the detail screen via the real RowSelected key path.
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        body_text = plain_text(app.screen.query_one("#detail-body", Static))
        assert "citizens: 5" in body_text
        assert "mafia: 2" in body_text
