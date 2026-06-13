"""Pure, Textual-free unit tests for the eval-ledger data layer (spec 012, Slice 1).

Locks in :mod:`graphia.eval_ledger` — ``load_ledger`` (multi-document YAML →
records, with missing/empty/malformed/``None``-document behaviour) and
``build_table_model`` (the heterogeneous-record flattener: column order, the
blank-vs-zero cell contract, CI band omission, the per-row search blobs, and
the index-parallelism the UI relies on) — **without ever importing Textual**.
No LLM / AWS / network is touched: the viewer (and this data layer) never call
``load_config``, so the autouse ``safe_llm`` net is simply irrelevant here.

Fixtures use the **REAL on-disk shape** copied from ``evals/blunder-ledger.yaml``:

- *pre-provenance* records carry ``run.games`` (not ``settings.games``), no
  ``code`` block, and no CI band on their metrics;
- *full* records carry ``code`` / ``settings`` / a Wilson CI band and put the
  game count under ``settings.games``;
- vote metrics are stored as **flat dotted keys** —
  ``metrics['self_vote.initiation']`` is one literal key, NOT a nested map.

The committed ledger is never read or written; every fixture is an in-process
YAML string written to ``tmp_path`` (for ``load_ledger``) or a hand-built dict
(for ``build_table_model``). The real ``TableModel`` / ``METRIC_ORDER`` /
``LedgerParseError`` symbols are imported, so a rename breaks these tests.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from graphia.eval_ledger import (
    LedgerParseError,
    METRIC_ORDER,
    TableModel,
    build_table_model,
    load_ledger,
)

# The fixed leading column count (⚠ / Date / Provider / Large / Small / Games),
# derived the same way the production code splits fixed-vs-metric columns
# (``len(columns) - len(METRIC_ORDER)``), so an added/removed fixed column is
# tracked rather than hard-coded.
_FIXED_COLUMN_COUNT = 6


# ===========================================================================
# Real-shaped fixtures (copied from evals/blunder-ledger.yaml on-disk shape)
# ===========================================================================

# A *pre-provenance* record: NO ``code``, NO ``settings``, NO CI band, and the
# game count under ``run.games`` (the early shape, docs 1-2 of the real ledger).
_PRE_PROVENANCE_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-13'
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

# A *full* record: ``code`` (dirty) + ``settings`` (games here, not run.games),
# the flat dotted vote-metric key ``self_vote.initiation`` carrying a clean
# 0.0 rate (the blank-vs-zero anchor), and NO CI band on its metrics. Notes
# carry distinctive substrings the search-blob assertions anchor on.
_FULL_NO_CI_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-13'
      duration_seconds: 343.364
      metrics_version: 1
    code:
      commit: '6d3926885201de4239a689936c0e1a02c248679b'
      branch: 'main'
      dirty: true
    provider:
      name: 'ollama'
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
    settings:
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
      games: 7
      seed: null
    quality:
      games_attempted: 7
      games_completed: 7
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.4537037037037037
        count: 49
        denominator: 108
      third_person_self_talk:
        rate: 0.037037037037037035
        count: 4
        denominator: 108
      self_vote.initiation:
        rate: 0.0
        count: 0
        denominator: 1
      self_vote.yes:
        rate: 0.5
        count: 1
        denominator: 2
    notes: 'self-run by Claude (Slice 5 acceptance); tree dirty: parked AWOS renames'
    """
)

# A *full + CI* record: every present metric carries a Wilson ci_low/ci_high
# band, settings.games is the game count, and the full vote family is present.
_FULL_WITH_CI_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-12'
      duration_seconds: 1072.842
      metrics_version: 1
    code:
      commit: 'e7dd42c90d1ea581f3836103addf50842037a592'
      branch: 'feature-branch'
      dirty: false
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
    notes: 'reliable baseline n=20 plus Wilson CI'
    """
)


def _write_ledger(tmp_path: Path, *docs: str) -> Path:
    """Write a ``---``-separated multi-document ledger from raw doc bodies.

    Mirrors the real on-disk layout: each record document is preceded by its
    own ``---`` separator line (the appender writes the first ``---`` too).
    """
    text = "".join(f"---\n{doc}" for doc in docs)
    path = tmp_path / "blunder-ledger.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ===========================================================================
# A1. load_ledger — multi-document parse, missing/empty/None-doc, malformed
# ===========================================================================


def test_load_ledger_parses_every_document(tmp_path: Path) -> None:
    """A 3-document fixture yields exactly 3 records in document order."""
    path = _write_ledger(
        tmp_path, _PRE_PROVENANCE_DOC, _FULL_NO_CI_DOC, _FULL_WITH_CI_DOC
    )

    records = load_ledger(path)

    assert len(records) == 3
    # Order preserved: the dates appear in the order written.
    assert [r["run"]["date"] for r in records] == [
        "2026-06-13",
        "2026-06-13",
        "2026-06-12",
    ]


def test_load_ledger_missing_file_is_empty_list(tmp_path: Path) -> None:
    """A path that does not exist yields ``[]`` (not an error)."""
    missing = tmp_path / "does-not-exist.yaml"
    assert not missing.exists()

    assert load_ledger(missing) == []


def test_load_ledger_empty_file_is_empty_list(tmp_path: Path) -> None:
    """An empty / whitespace-only file yields ``[]`` (a normal empty ledger)."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("   \n\n  ", encoding="utf-8")

    assert load_ledger(empty) == []


def test_load_ledger_skips_trailing_none_document(tmp_path: Path) -> None:
    """A trailing ``---`` (a ``None`` document) is skipped, not counted."""
    text = f"---\n{_PRE_PROVENANCE_DOC}---\n"  # trailing separator → None doc
    path = tmp_path / "trailing.yaml"
    path.write_text(text, encoding="utf-8")

    records = load_ledger(path)

    # Only the one real record survives; the trailing None document is dropped.
    assert len(records) == 1
    assert records[0]["run"]["date"] == "2026-06-13"


def test_load_ledger_malformed_yaml_raises_parse_error(tmp_path: Path) -> None:
    """Genuinely malformed YAML raises ``LedgerParseError`` (chained)."""
    bad = tmp_path / "bad.yaml"
    # Unbalanced flow-mapping brace — a hard ``yaml.YAMLError``.
    bad.write_text("---\nrun: {date: '2026-06-13'\n", encoding="utf-8")

    with pytest.raises(LedgerParseError):
        load_ledger(bad)


# ===========================================================================
# A2. Pre-provenance heterogeneity flattens without raising
# ===========================================================================


def test_pre_provenance_record_flattens_without_raising(tmp_path: Path) -> None:
    """A pre-provenance record (no code/settings/CI) flattens cleanly.

    Blank ``⚠`` cell (no ``code.dirty``), the ``run.games`` games-count
    fallback (no ``settings.games``), and a metric formatted WITHOUT a CI band
    (the record carries no ci_low/ci_high) — none of which raises.
    """
    records = load_ledger(_write_ledger(tmp_path, _PRE_PROVENANCE_DOC))

    model = build_table_model(records)  # must not raise

    (row,) = model.rows
    # ⚠ blank: no code block means not-dirty.
    assert row[0] == ""
    # Games-count fallback: run.games (3), since there is no settings block.
    assert row[_FIXED_COLUMN_COUNT - 1] == "3"
    # The repetition cell is present-without-CI: rate + count/denom, no band.
    repetition_cell = row[_FIXED_COLUMN_COUNT]
    assert repetition_cell == "0.45 59/130"
    assert "[" not in repetition_cell  # band omitted when CI absent


# ===========================================================================
# A3. build_table_model — columns, blank-vs-zero, CI band, search blobs, parallelism
# ===========================================================================


def test_columns_are_fixed_columns_then_metric_labels() -> None:
    """``columns`` == fixed leading columns + the METRIC_ORDER labels, in order."""
    model = build_table_model([])

    assert model.columns[:_FIXED_COLUMN_COUNT] == [
        "⚠",
        "Date",
        "Provider",
        "Large model",
        "Small model",
        "Games",
    ]
    assert model.columns[_FIXED_COLUMN_COUNT:] == [label for _, label in METRIC_ORDER]


def test_absent_metric_is_blank_while_clean_zero_is_non_empty(tmp_path: Path) -> None:
    """The blank-vs-zero contract: absent metric → ``""``, a genuine 0.0 → non-empty.

    The full-no-CI fixture has ``self_vote.initiation`` at a clean 0.0 (count 0)
    but no ``peer_vote.*`` family at all. The zero metric must render a visible
    ``"0.00 …"`` cell; the never-exercised peer metric must render the empty
    string — the two states must stay distinct.
    """
    records = load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC))
    model = build_table_model(records)
    (row,) = model.rows

    labels = [label for _, label in METRIC_ORDER]
    col = {label: _FIXED_COLUMN_COUNT + i for i, label in enumerate(labels)}

    # A genuine zero (self_vote.initiation: rate 0.0, count 0/1) renders non-empty.
    self_vote_init = row[col["self-vote init"]]
    assert self_vote_init == "0.00 0/1"
    assert self_vote_init != ""

    # An absent family (no peer_vote.* in this record) renders the empty string.
    assert row[col["peer-vote init"]] == ""
    assert row[col["peer-vote yes"]] == ""


def test_metric_cell_omits_ci_band_when_absent(tmp_path: Path) -> None:
    """A present metric WITHOUT ci_low/ci_high renders ``rate count/denom`` only."""
    records = load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC))
    (row,) = build_table_model(records).rows

    repetition_cell = row[_FIXED_COLUMN_COUNT]  # first metric column
    assert repetition_cell == "0.45 49/108"
    assert "[" not in repetition_cell and "]" not in repetition_cell


def test_metric_cell_includes_ci_band_when_present(tmp_path: Path) -> None:
    """A present metric WITH a Wilson band renders ``rate [lo–hi] count/denom``.

    The en-dash separator (``–``, not a hyphen) and 2-decimal bounds are part
    of the documented cell format.
    """
    records = load_ledger(_write_ledger(tmp_path, _FULL_WITH_CI_DOC))
    (row,) = build_table_model(records).rows

    repetition_cell = row[_FIXED_COLUMN_COUNT]
    assert repetition_cell == "0.55 [0.53–0.58] 624/1126"
    assert "–" in repetition_cell  # en-dash, the typographic CI separator


def test_search_blob_contains_identity_facets_and_full_notes(tmp_path: Path) -> None:
    """The blob carries date, provider, both models, commit, branch, full notes.

    All lowercased so the UI's ``query in blob`` substring filter is
    case-insensitive. The *full* notes text (every word) must be present so a
    search on any note phrase hits.
    """
    records = load_ledger(_write_ledger(tmp_path, _FULL_WITH_CI_DOC))
    (blob,) = build_table_model(records).search_blobs

    assert "2026-06-12" in blob
    assert "bedrock" in blob
    assert "amazon.nova-pro-v1:0" in blob  # large model id
    assert "amazon.nova-lite-v1:0" in blob  # small model id
    assert "e7dd42c90d1ea581f3836103addf50842037a592" in blob  # commit
    assert "feature-branch" in blob  # branch
    # The FULL notes text, lowercased, appears verbatim.
    assert "reliable baseline n=20 plus wilson ci" in blob
    # Everything is lowercased.
    assert blob == blob.lower()


def test_search_blob_marks_dirty_vs_clean_tree(tmp_path: Path) -> None:
    """A dirty tree contributes ``dirty`` to the blob; a clean tree ``clean``."""
    dirty_records = load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC))
    (dirty_blob,) = build_table_model(dirty_records).search_blobs
    assert "dirty" in dirty_blob.split()

    clean_path = tmp_path / "clean.yaml"
    clean_path.write_text(f"---\n{_FULL_WITH_CI_DOC}", encoding="utf-8")
    clean_records = load_ledger(clean_path)
    (clean_blob,) = build_table_model(clean_records).search_blobs
    assert "clean" in clean_blob.split()


def test_dirty_record_marks_the_warning_column(tmp_path: Path) -> None:
    """A ``code.dirty: true`` record fills the ⚠ column; a clean record blanks it."""
    dirty_records = load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC))
    (dirty_row,) = build_table_model(dirty_records).rows
    assert dirty_row[0] == "⚠"

    clean_path = tmp_path / "clean-warning.yaml"
    clean_path.write_text(f"---\n{_FULL_WITH_CI_DOC}", encoding="utf-8")
    (clean_row,) = build_table_model(load_ledger(clean_path)).rows
    assert clean_row[0] == ""


def test_table_model_lists_are_index_parallel(tmp_path: Path) -> None:
    """``rows`` / ``search_blobs`` / ``records`` are the same length, in order."""
    records = load_ledger(
        _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_NO_CI_DOC, _FULL_WITH_CI_DOC)
    )

    model = build_table_model(records)

    assert isinstance(model, TableModel)
    assert len(model.rows) == len(model.records) == len(model.search_blobs) == 3
    # records[i] is the same backing record the row was built from.
    assert [r["run"]["date"] for r in model.records] == [
        "2026-06-13",
        "2026-06-13",
        "2026-06-12",
    ]
    # Every row is aligned to the shared column header list.
    assert all(len(row) == len(model.columns) for row in model.rows)


def test_settings_games_overrides_run_games_for_full_record(tmp_path: Path) -> None:
    """A full record reports ``settings.games`` (7), not the absent run.games."""
    records = load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC))
    (row,) = build_table_model(records).rows

    assert row[_FIXED_COLUMN_COUNT - 1] == "7"  # the Games column == settings.games


def test_empty_records_yields_headers_only(tmp_path: Path) -> None:
    """An empty record list still produces the column headers and zero rows."""
    model = build_table_model([])

    assert model.columns  # headers always present
    assert model.rows == []
    assert model.search_blobs == []
    assert model.records == []
