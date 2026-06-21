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
    SEARCH_FIELDS,
    SEARCH_SCOPE_ALL,
    TableModel,
    TranscriptEntry,
    _NOTES_CELL_MAXLEN,
    build_table_model,
    list_transcripts,
    load_ledger,
    read_transcript,
    render_detail,
    row_matches_field,
    transcript_dir_for,
)

# The fixed leading column count (⚠ / Date / Provider / Large / Small / Games /
# Notes), derived the same way the production code splits fixed-vs-metric columns
# (``len(columns) - len(METRIC_ORDER)``), so an added/removed fixed column is
# tracked rather than hard-coded.
_FIXED_COLUMN_COUNT = len(build_table_model([]).columns) - len(METRIC_ORDER)

# The Games column index, resolved by name so tests that check the games-count
# fallback don't assume Games is the last fixed column (Notes now follows it).
_GAMES_COLUMN = build_table_model([]).columns.index("Games")


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


# A *bedrock* record whose NOTE deliberately mentions "ollama" — the
# scoped-search disambiguation anchor (Slice 5). ``provider:ollama`` must NOT
# keep this row (its provider field is 'bedrock'), but a bare free-text
# ``ollama`` MUST (the substring lives in the notes-derived blob). Its note also
# carries the distinctive word "tuesday" so a ``provider:bedrock <word>`` AND
# sweep has a present/absent term to hinge on.
_BEDROCK_NOTE_OLLAMA_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-09'
      duration_seconds: 500.0
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
    notes: 'tuesday rerun comparing against the ollama baseline'
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
    assert row[_GAMES_COLUMN] == "3"
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
        "Wins (LA/M)",
        # Spec 029: the three curated game-dynamics columns, before ``Notes``.
        "Scripted (side)",
        "Unres (R/N)",
        "Votes (LA/M)",
        "Stand-in",
        "Lineup",
        "Notes",
    ]
    assert model.columns[_FIXED_COLUMN_COUNT:] == [label for _, label in METRIC_ORDER]


def test_notes_column_shows_short_note_verbatim_and_truncates_long_one(
    tmp_path: Path,
) -> None:
    """The Notes cell previews the run's note so a notes-match isn't a phantom hit.

    A short note appears verbatim; a long one is collapsed to a single line and
    truncated to a bounded width with a trailing ellipsis (the full, verbatim
    note lives in the drill-down); an absent note is the empty string.
    """
    records = load_ledger(
        _write_ledger(tmp_path, _PRE_PROVENANCE_DOC, _FULL_NO_CI_DOC, _FULL_WITH_CI_DOC)
    )
    model = build_table_model(records)
    notes_col = model.columns.index("Notes")
    pre_provenance, full_no_ci, full_with_ci = (row[notes_col] for row in model.rows)

    # Absent note → empty cell (the pre-provenance fixture carries no notes).
    assert pre_provenance == ""
    # Short note → verbatim (under the width cap).
    assert full_with_ci == "reliable baseline n=20 plus Wilson CI"
    # Long note → single line, truncated with an ellipsis, a prefix of the source.
    assert full_no_ci.endswith("…")
    assert len(full_no_ci) <= _NOTES_CELL_MAXLEN
    assert full_no_ci[:-1].startswith("self-run by Claude (Slice 5 acceptance)")


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

    assert row[_GAMES_COLUMN] == "7"  # the Games column == settings.games


def test_empty_records_yields_headers_only(tmp_path: Path) -> None:
    """An empty record list still produces the column headers and zero rows."""
    model = build_table_model([])

    assert model.columns  # headers always present
    assert model.rows == []
    assert model.search_blobs == []
    assert model.records == []


# ===========================================================================
# A3b. Spec-029 curated columns — Scripted (side) / Stand-in / Unres (R/N)
# ===========================================================================
#
# Three display-only fixed columns added before ``Notes`` (tech-spec 029 §2.1):
#   - ``Scripted (side)`` (_scripted_side_cell, spec 027) — present → ``LA .55``;
#     absent block → blank.
#   - ``Stand-in`` (_stand_in_cell, spec 026) — present → ``active``/``passive``;
#     absent field DEFAULTS to ``passive`` (the README contract), NOT blank.
#   - ``Unres (R/N)`` (_resolution_cell, spec 013/023) — present → ``R 1 N 2``;
#     present-zero → ``R 0 N 0`` (distinct from absent block's blank).
# These drive through ``build_table_model`` over hand-built records (the spec
# 013/014/027 column-test pattern). The full-with-CI fixture carries spec-027/026
# fields below so the present-value paths have a target.

# A *new-shape* record carrying every spec-029-surfaced field: spec-027
# ``outcomes.scripted_side`` (law_abiding @ .55), spec-026
# ``settings.scripted_player: active``, and spec-013/023 ``outcomes.runaway`` /
# ``outcomes.no_winner`` counts (1 / 2). The present-value anchor for all three
# new cells.
_FULL_SPEC029_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-19'
      duration_seconds: 900.0
      metrics_version: 1
    code:
      commit: 'ffff0000ffff0000ffff0000ffff0000ffff0000'
      branch: 'main'
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
      scripted_player: 'active'
    quality:
      games_attempted: 20
      games_completed: 20
      games_failed_early: 0
    outcomes:
      games: 20
      law_abiding:
        wins: 11
        rate: 0.55
      mafia:
        wins: 6
        rate: 0.3
      scripted_side:
        side: 'law_abiding'
        wins: 11
        rate: 0.55
      runaway: 1
      no_winner: 2
      draw: 0
    metrics:
      repetition:
        rate: 0.5
        count: 10
        denominator: 20
    notes: 'spec 029 anchor — active stand-in, scripted LA side'
    """
)

# A *present-outcomes-all-resolved* record: an ``outcomes`` block whose games all
# resolved to a side (no runaway / no_winner keys) → the resolution cell must read
# the present-zero ``R 0 N 0``, staying distinct from an absent block's blank.
_RESOLVED_OUTCOMES_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-17'
      metrics_version: 1
    provider:
      name: 'ollama'
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
    settings:
      games: 4
      scripted_player: 'passive'
    quality:
      games_attempted: 4
      games_completed: 4
      games_failed_early: 0
    outcomes:
      games: 4
      law_abiding:
        wins: 3
        rate: 0.75
      mafia:
        wins: 1
        rate: 0.25
    metrics:
      repetition:
        rate: 0.5
        count: 10
        denominator: 20
    """
)


def _col(model: TableModel, header: str) -> int:
    """The index of ``header`` in the model's columns (resolved by name)."""
    return model.columns.index(header)


def test_scripted_side_cell_present_shows_side_and_rate(tmp_path: Path) -> None:
    """A run with ``outcomes.scripted_side`` shows ``LA .55`` (abbr + leading-dot rate)."""
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _FULL_SPEC029_DOC)))
    (row,) = model.rows

    assert row[_col(model, "Scripted (side)")] == "LA .55"


def test_scripted_side_cell_absent_is_blank(tmp_path: Path) -> None:
    """A pre-027 record (no ``outcomes.scripted_side``) → blank scripted cell.

    The full-with-CI fixture carries an ``outcomes``-less shape (no outcomes block
    at all), and the pre-provenance fixture also predates the metric — both render
    the empty string, not a phantom value.
    """
    model = build_table_model(
        load_ledger(_write_ledger(tmp_path, _FULL_WITH_CI_DOC, _PRE_PROVENANCE_DOC))
    )
    col = _col(model, "Scripted (side)")

    assert model.rows[0][col] == ""  # full-with-CI: no scripted_side
    assert model.rows[1][col] == ""  # pre-provenance: no outcomes block


def test_stand_in_cell_present_shows_mode(tmp_path: Path) -> None:
    """A run recorded with ``settings.scripted_player: active`` shows ``active``."""
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _FULL_SPEC029_DOC)))
    (row,) = model.rows

    assert row[_col(model, "Stand-in")] == "active"


def test_stand_in_cell_absent_defaults_to_passive_not_blank(tmp_path: Path) -> None:
    """A pre-026 record (no ``settings.scripted_player``) reads ``passive``, NOT blank.

    The deliberate exception to the blank-for-absent contract: per the
    ``evals/README.md`` record contract the field is omitted on pre-026 records and
    read as the prior default ``passive``. Asserted on the full-no-CI fixture (a
    ``settings`` block WITHOUT ``scripted_player``) and the pre-provenance fixture
    (NO ``settings`` block at all) — both default to ``passive``.
    """
    model = build_table_model(
        load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC, _PRE_PROVENANCE_DOC))
    )
    col = _col(model, "Stand-in")

    # settings present but no scripted_player → default passive (not blank).
    assert model.rows[0][col] == "passive"
    # no settings block at all → still the passive default (not blank).
    assert model.rows[1][col] == "passive"


def test_resolution_cell_present_shows_runaway_and_no_winner(tmp_path: Path) -> None:
    """A run with ``runaway`` / ``no_winner`` counts shows them as ``R 1 N 2``."""
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _FULL_SPEC029_DOC)))
    (row,) = model.rows

    assert row[_col(model, "Unres (R/N)")] == "R 1 N 2"


def test_resolution_cell_present_zero_is_distinct_from_absent_blank(
    tmp_path: Path,
) -> None:
    """Present-zero ``R 0 N 0`` (all games resolved) stays distinct from absent-blank.

    Mirrors the ``_vote_activity_cell`` present-zero-vs-absent contract: a present
    ``outcomes`` block whose games all resolved to a side (no ``runaway`` /
    ``no_winner`` keys) reads ``R 0 N 0``; a record with NO ``outcomes`` block (the
    pre-provenance fixture) renders the empty string. The two must differ.
    """
    model = build_table_model(
        load_ledger(_write_ledger(tmp_path, _RESOLVED_OUTCOMES_DOC, _PRE_PROVENANCE_DOC))
    )
    col = _col(model, "Unres (R/N)")

    # Present block, zero unresolved buckets → explicit present-zero.
    assert model.rows[0][col] == "R 0 N 0"
    # Absent outcomes block (pre-013) → blank.
    assert model.rows[1][col] == ""
    # The whole point: present-zero and absent are NOT the same cell.
    assert model.rows[0][col] != model.rows[1][col]


def test_spec029_columns_heterogeneous_mix_stays_index_parallel(
    tmp_path: Path,
) -> None:
    """A pre-013/pre-026/pre-027 + full-new mix flattens with no KeyError, all rows aligned.

    The headline back-compat risk for a column addition: every ``len(row) ==
    len(columns)`` even across the heterogeneous shapes (pre-provenance with no
    outcomes/settings, full-no-CI with settings-but-no-scripted_player, and the
    new-shape record carrying all spec-029 fields). The new columns never raise.
    """
    model = build_table_model(
        load_ledger(
            _write_ledger(
                tmp_path,
                _PRE_PROVENANCE_DOC,  # pre-013/026/027: no outcomes, no settings
                _FULL_NO_CI_DOC,  # settings, but no scripted_player / outcomes
                _FULL_SPEC029_DOC,  # all spec-029 fields present
            )
        )
    )

    assert len(model.rows) == 3
    assert all(len(row) == len(model.columns) for row in model.rows)
    # Spot-check the new columns flatten to their expected per-shape values.
    scripted = _col(model, "Scripted (side)")
    stand_in = _col(model, "Stand-in")
    unres = _col(model, "Unres (R/N)")
    # pre-provenance: scripted blank, stand-in passive default, unres blank.
    assert (model.rows[0][scripted], model.rows[0][stand_in], model.rows[0][unres]) == (
        "",
        "passive",
        "",
    )
    # full-no-CI: scripted blank (no outcomes), stand-in passive default, unres blank.
    assert (model.rows[1][scripted], model.rows[1][stand_in], model.rows[1][unres]) == (
        "",
        "passive",
        "",
    )
    # full spec-029: all three present.
    assert (model.rows[2][scripted], model.rows[2][stand_in], model.rows[2][unres]) == (
        "LA .55",
        "active",
        "R 1 N 2",
    )


# ===========================================================================
# A4. render_detail — full-precision metric counts, verbatim multi-line notes,
#     and graceful degradation on a pre-provenance record (spec 012, Slice 3)
# ===========================================================================
#
# render_detail is the pure string the DetailScreen wraps: a sectioned plain
# render (run → code → provider → settings → quality → metrics → notes) where
# each PRESENT metric shows full-precision ``rate`` + optional ``[lo–hi]`` band
# + ``count/denominator``, an absent metric/field shows the ``—`` placeholder,
# and the note is rendered VERBATIM (newlines preserved). These tests pin the
# exact rendered substrings so a format drift breaks them.


def test_render_detail_shows_full_precision_metric_with_ci_band(tmp_path: Path) -> None:
    """A present CI-banded metric renders full-precision rate + band + exact counts.

    The table cell truncates the rate to two places; the detail view shows what
    the ledger recorded (``repr(float(rate))``) and the full ``count/denominator``
    — neither rounded nor abbreviated.
    """
    records = load_ledger(_write_ledger(tmp_path, _FULL_WITH_CI_DOC))
    (record,) = records

    text = render_detail(record)

    # The exact full-precision metric line (full rate, en-dash CI band, counts).
    assert (
        "repetition: 0.5541740674955595 "
        "[0.525005635243927–0.5829741028007689] 624/1126"
    ) in text
    # The exact count/denominator substring is present verbatim.
    assert "624/1126" in text
    # An absent metric for this record degrades to the em-dash placeholder.
    assert "self-talk: —" in text


def test_render_detail_shows_clean_zero_metric_without_ci(tmp_path: Path) -> None:
    """A present metric WITHOUT a CI band renders rate + counts, no bracketed band.

    The full-no-CI fixture's ``self_vote.initiation`` is a clean 0.0 (count 0/1):
    it must render full-precision with its exact ``0/1`` count and no ``[`` band,
    staying distinct from an absent metric's ``—``.
    """
    records = load_ledger(_write_ledger(tmp_path, _FULL_NO_CI_DOC))
    (record,) = records

    text = render_detail(record)

    # Full-precision repetition rate + exact counts, no CI band.
    assert "repetition: 0.4537037037037037 49/108" in text
    # The clean zero vote metric: full precision, exact 0/1 count, no band.
    assert "self-vote init: 0.0 0/1" in text
    # The non-CI lines carry no bracketed band.
    repetition_line = next(
        line for line in text.splitlines() if "repetition:" in line
    )
    assert "[" not in repetition_line


def test_render_detail_renders_multiline_note_verbatim() -> None:
    """A multi-line note appears VERBATIM under the notes section — newlines kept.

    A YAML literal-block note keeps its line breaks; the second/third lines must
    survive the render exactly (newlines preserved, never collapsed).
    """
    note = "first line of the note\nsecond line with detail\nthird and final line"
    record = {
        "run": {"date": "2026-06-14", "metrics_version": 1},
        "notes": note,
    }

    text = render_detail(record)

    # The whole multi-line note appears verbatim, including the embedded newlines.
    assert note in text
    # Each individual line survives (the newline really was preserved, not joined).
    assert "second line with detail" in text
    assert "third and final line" in text
    # The note sits under its section header.
    assert text.endswith("notes\n" + note)


def test_render_detail_degrades_gracefully_on_pre_provenance_record(
    tmp_path: Path,
) -> None:
    """A pre-provenance record (no code/settings/CI) renders without raising.

    The absent ``code`` and ``settings`` blocks each collapse to a single ``—``
    line; the present metric still shows its full-precision rate + exact counts;
    and an empty/absent note shows ``—``. No ``KeyError`` escapes.
    """
    records = load_ledger(_write_ledger(tmp_path, _PRE_PROVENANCE_DOC))
    (record,) = records

    text = render_detail(record)  # must not raise

    # The absent sub-blocks each collapse to a single em-dash line.
    assert "code\n  —" in text
    assert "settings\n  —" in text
    # The present pre-provenance metric still shows full precision + exact counts.
    assert "repetition: 0.45384615384615384 59/130" in text
    # The pre-provenance ``run.games`` fallback surfaces in the run section.
    assert "games: 3" in text
    # No note on this record → the notes section shows the em-dash placeholder.
    assert text.endswith("notes\n—")


# ===========================================================================
# A5. row_matches_field + search_fields — selector-scoped search (spec 012, Slice 6)
# ===========================================================================
#
# The typed ``field:value`` syntax of Slice 5 is GONE. Scoping is now the field
# selector's job: ``row_matches_field(field, value, blob, fields)`` searches the
# whole free-text ``blob`` when ``field == SEARCH_SCOPE_ALL`` ("All"), or the one
# named ``fields[field]`` for any SEARCH_FIELDS name. ``value`` is lowercased +
# whitespace-split into ANDed terms (empty → keep all); a colon in ``value`` is
# LITERAL (no parsing). These tests drive through ``build_table_model`` so the
# parallel ``search_fields`` construction is covered too. The fixtures give
# deterministic targets:
#   _FULL_WITH_CI_DOC        → provider 'bedrock', note 'reliable baseline …';
#   _BEDROCK_NOTE_OLLAMA_DOC → provider 'bedrock', note mentions 'ollama'
#                              + 'tuesday' (the disambiguation anchor);
#   _PRE_PROVENANCE_DOC      → provider 'ollama', model 'qwen3-coder:30b'.


def _model_for(tmp_path: Path, *docs: str) -> TableModel:
    """build_table_model over a freshly written ledger of ``docs`` (in order)."""
    return build_table_model(load_ledger(_write_ledger(tmp_path, *docs)))


def test_all_scope_value_matches_the_free_text_blob(tmp_path: Path) -> None:
    """Under SEARCH_SCOPE_ALL, the value matches the whole free-text blob.

    A word from the notes (only in the blob) keeps the row; a word in neither the
    blob nor any field drops it — the free-text behaviour the "All" scope owns.
    """
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert row_matches_field(SEARCH_SCOPE_ALL, "baseline", blob, fields) is True
    assert (
        row_matches_field(SEARCH_SCOPE_ALL, "nonexistent-token", blob, fields)
        is False
    )


def test_provider_scope_targets_only_the_provider_field(
    tmp_path: Path,
) -> None:
    """A ``provider``-scoped value keys off the provider field, not the blob.

    The bedrock fixture matches ``provider`` / "bedrock"; the ollama fixture does
    not (its provider field is 'ollama').
    """
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC, _PRE_PROVENANCE_DOC)
    bedrock_fields = model.search_fields[0]
    ollama_fields = model.search_fields[1]
    bedrock_blob = model.search_blobs[0]
    ollama_blob = model.search_blobs[1]

    assert (
        row_matches_field("provider", "bedrock", bedrock_blob, bedrock_fields)
        is True
    )
    assert (
        row_matches_field("provider", "bedrock", ollama_blob, ollama_fields)
        is False
    )


def test_provider_scope_ignores_note_mention_but_all_scope_matches_it(
    tmp_path: Path,
) -> None:
    """The disambiguation: a note mention must not satisfy a provider-scoped value.

    The bedrock fixture's NOTE mentions 'ollama'. Scoped ``provider`` / "ollama"
    must NOT keep it (its provider field is 'bedrock'), proving the scoped match
    checks the field and not the blob; the All-scoped ``"ollama"`` MUST keep it
    (the word is in the notes-derived blob).
    """
    model = _model_for(tmp_path, _BEDROCK_NOTE_OLLAMA_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    # Sanity: the note really did seed 'ollama' into the blob, while the
    # provider field stays 'bedrock'.
    assert "ollama" in blob
    assert fields["provider"] == "bedrock"

    # Scoped provider / "ollama" is dropped (field is bedrock)...
    assert row_matches_field("provider", "ollama", blob, fields) is False
    # ...but the All-scoped value hits the blob.
    assert row_matches_field(SEARCH_SCOPE_ALL, "ollama", blob, fields) is True


def test_note_scope_targets_only_the_notes_field(
    tmp_path: Path,
) -> None:
    """A ``note``-scoped value targets the notes field; a non-notes word misses.

    'baseline' is in the bedrock fixture's note → kept; 'bedrock' is the provider
    name (not in its note text) → dropped by a ``note``-scoped value.
    """
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert row_matches_field("note", "baseline", blob, fields) is True
    # 'bedrock' is the provider, absent from the notes field → scoped miss.
    assert row_matches_field("note", "bedrock", blob, fields) is False


def test_multi_term_value_is_anded(tmp_path: Path) -> None:
    """A multi-word value ANDs: every term must hit the chosen haystack.

    Under ``provider`` scope, "bedrock" alone holds, but "bedrock <note-word>"
    fails — the second term is a notes word, absent from the provider field.
    Under "All", "bedrock" plus a word from its note both hit the blob → keep.
    """
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    # All scope: provider word + a note word both live in the blob → AND holds.
    assert row_matches_field(SEARCH_SCOPE_ALL, "bedrock baseline", blob, fields) is True
    # ...swapping in a word absent from the row drops it.
    assert (
        row_matches_field(SEARCH_SCOPE_ALL, "bedrock absent-word", blob, fields)
        is False
    )
    # Provider scope: "baseline" is a notes word, not in the provider field → AND fails.
    assert row_matches_field("provider", "bedrock baseline", blob, fields) is False


def test_colon_bearing_value_is_matched_literally(
    tmp_path: Path,
) -> None:
    """A value with a colon is matched literally — NO field:value parsing.

    The model id itself contains a colon. ``qwen3-coder:30b`` is searched as
    written: it matches under "All" (the model id is in the blob) and under the
    ``model`` scope (the model field holds it), never split into field/value.
    """
    model = _model_for(tmp_path, _PRE_PROVENANCE_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert "qwen3-coder" not in SEARCH_FIELDS  # not a field name — would-be prefix
    assert "qwen3-coder:30b" in blob  # the model id is in the blob
    assert (
        row_matches_field(SEARCH_SCOPE_ALL, "qwen3-coder:30b", blob, fields) is True
    )
    assert row_matches_field("model", "qwen3-coder:30b", blob, fields) is True


@pytest.mark.parametrize("value", ["", "   ", "\t\n  "])
def test_empty_or_whitespace_value_matches_every_row(
    tmp_path: Path, value: str
) -> None:
    """An empty / whitespace-only value keeps the row (no terms → match).

    True under both the All scope and a named field scope — the value, not the
    field, drives the no-op.
    """
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert row_matches_field(SEARCH_SCOPE_ALL, value, blob, fields) is True
    assert row_matches_field("provider", value, blob, fields) is True


@pytest.mark.parametrize("value", ["BEDROCK", "Bedrock", "bEdRoCk"])
def test_scoped_match_is_case_insensitive(tmp_path: Path, value: str) -> None:
    """A value in any case matches the lowercased field value."""
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert row_matches_field("provider", value, blob, fields) is True


def test_search_fields_is_index_parallel_with_rows_and_records(
    tmp_path: Path,
) -> None:
    """``search_fields`` is one dict per record, aligned to rows/records."""
    model = _model_for(
        tmp_path, _PRE_PROVENANCE_DOC, _FULL_NO_CI_DOC, _FULL_WITH_CI_DOC
    )

    assert len(model.search_fields) == len(model.rows) == len(model.records) == 3
    assert all(isinstance(f, dict) for f in model.search_fields)


def test_search_fields_has_note_key_not_notes_alias(tmp_path: Path) -> None:
    """SEARCH_FIELDS dropped the ``notes`` alias; the per-row map keys on ``note``.

    Slice 6 removed the ``notes`` alias: SEARCH_FIELDS no longer contains
    ``"notes"`` and each ``search_fields[i]`` dict has a ``"note"`` key (not
    ``"notes"``), holding the lowercased full notes text.
    """
    assert "notes" not in SEARCH_FIELDS
    assert "note" in SEARCH_FIELDS

    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (fields,) = model.search_fields

    assert "note" in fields
    assert "notes" not in fields
    assert fields["note"] == "reliable baseline n=20 plus wilson ci"


def test_search_fields_carry_expected_lowercased_field_values(
    tmp_path: Path,
) -> None:
    """Known field keys hold the expected lowercased text.

    ``provider`` is the lowercased provider name; ``model`` carries BOTH resolved
    model ids; ``note`` is the full notes text; ``state`` is the dirty/clean
    keyword. The key set is exactly SEARCH_FIELDS.
    """
    model = _model_for(tmp_path, _FULL_WITH_CI_DOC)
    (fields,) = model.search_fields

    # The per-row map keys are exactly the recognised field names.
    assert set(fields) == set(SEARCH_FIELDS)
    # provider → lowercased provider name.
    assert fields["provider"] == "bedrock"
    # model → both resolved ids, lowercased.
    assert "amazon.nova-pro-v1:0" in fields["model"]
    assert "amazon.nova-lite-v1:0" in fields["model"]
    # note → the full notes text, lowercased.
    assert fields["note"] == "reliable baseline n=20 plus wilson ci"
    # state is the clean keyword for this clean-tree record; everything lowercased.
    assert fields["state"] == "clean"
    assert all(v == v.lower() for v in fields.values())


def test_search_fields_state_is_dirty_for_a_dirty_record(tmp_path: Path) -> None:
    """A ``code.dirty: true`` record carries ``state == 'dirty'``."""
    model = _model_for(tmp_path, _FULL_NO_CI_DOC)
    (fields,) = model.search_fields

    assert fields["state"] == "dirty"
    assert row_matches_field("state", "dirty", model.search_blobs[0], fields) is True
    assert row_matches_field("state", "clean", model.search_blobs[0], fields) is False


# ===========================================================================
# A6. transcript locating / listing / reading (spec 017, Slice 2 — pure layer)
# ===========================================================================
#
# The pure data layer the viewer's TranscriptListScreen / TranscriptScreen
# consume: ``transcript_dir_for`` resolves a record's ``run.transcript_dir``
# against the ledger's SIBLING ``transcripts/`` dir; ``list_transcripts`` lists
# its ``game-*.txt`` as SORTED ``TranscriptEntry`` items; ``read_transcript``
# reads one file's text. All three are read-only and DEFENSIVE (mirroring
# ``_dig``): a missing field / missing dir / empty dir / unreadable file all
# resolve to an EMPTY result, never raise — the contract that drives the
# viewer's "No transcripts for this run." state. Everything lives in
# ``tmp_path``; the committed ``evals/transcripts/`` is never touched.
#
# A record naming a run-id transcript dir under ``run.transcript_dir`` — the
# field ``blunder_eval.render_record`` writes (the dir NAME, never an absolute
# path). The id matches the sibling dir built by ``_write_transcripts``.
_RUN_WITH_TRANSCRIPTS_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-18'
      transcript_dir: '2026-06-18T14-32-05'
      metrics_version: 1
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
    """
)


def _write_transcripts(
    ledger_path: Path, run_id: str, files: dict[str, str]
) -> Path:
    """Create the ledger's sibling ``transcripts/<run-id>/`` dir + game files.

    Mirrors ``blunder_eval``'s on-disk layout: a ``transcripts/`` dir SIBLING to
    the ledger file, one ``<run-id>`` dir under it, each ``files`` entry written
    as a ``game-NN.txt``. Returns the run dir. Everything sits inside the
    caller's ``tmp_path`` — never the committed ``evals/transcripts/``.
    """
    run_dir = ledger_path.parent / "transcripts" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (run_dir / name).write_text(body, encoding="utf-8")
    return run_dir


def test_list_transcripts_returns_sorted_entries_with_labels_and_paths(
    tmp_path: Path,
) -> None:
    """A run's two games list as sorted ``TranscriptEntry`` items, read back verbatim.

    ``game-01.txt`` before ``game-02.txt`` (sorted by filename), each entry's
    ``.label`` is the file stem (``game-01``) and ``.path`` the resolved file;
    ``read_transcript(entry.path)`` returns the file's text.
    """
    ledger = _write_ledger(tmp_path, _RUN_WITH_TRANSCRIPTS_DOC)
    (record,) = load_ledger(ledger)
    # Write game-02 FIRST so the sort (not insertion order) is what orders them.
    _write_transcripts(
        ledger,
        "2026-06-18T14-32-05",
        {
            "game-02.txt": "second game body",
            "game-01.txt": "first game body",
        },
    )

    entries = list_transcripts(record, ledger)

    assert [e.label for e in entries] == ["game-01", "game-02"]
    assert all(isinstance(e, TranscriptEntry) for e in entries)
    # The paths resolve under the ledger's sibling transcripts/<run-id>/ dir.
    assert entries[0].path == ledger.parent / "transcripts" / "2026-06-18T14-32-05" / "game-01.txt"
    # The reader returns each file's text verbatim.
    assert read_transcript(entries[0].path) == "first game body"
    assert read_transcript(entries[1].path) == "second game body"


def test_transcript_dir_for_resolves_against_ledger_sibling(tmp_path: Path) -> None:
    """``transcript_dir_for`` returns ``<ledger>/../transcripts/<run-id>`` (no I/O).

    Locating does NOT check existence — it returns the Path the run-id resolves
    to under the ledger's sibling ``transcripts/`` dir, even before the dir is
    created (existence is ``list_transcripts``'s concern).
    """
    ledger = _write_ledger(tmp_path, _RUN_WITH_TRANSCRIPTS_DOC)
    (record,) = load_ledger(ledger)

    located = transcript_dir_for(record, ledger)

    assert located == ledger.parent / "transcripts" / "2026-06-18T14-32-05"


def test_transcript_dir_for_missing_field_is_none(tmp_path: Path) -> None:
    """A record with NO ``run.transcript_dir`` field resolves to ``None``.

    An older pre-017 record (the ``_FULL_WITH_CI_DOC`` fixture carries no
    transcript_dir) has nowhere to point — the locate half of the "no
    transcripts" state.
    """
    ledger = _write_ledger(tmp_path, _FULL_WITH_CI_DOC)
    (record,) = load_ledger(ledger)

    assert transcript_dir_for(record, ledger) is None


def test_list_transcripts_missing_field_is_empty(tmp_path: Path) -> None:
    """A record with NO ``run.transcript_dir`` lists as ``[]`` (never raises)."""
    ledger = _write_ledger(tmp_path, _FULL_WITH_CI_DOC)
    (record,) = load_ledger(ledger)

    assert list_transcripts(record, ledger) == []


def test_list_transcripts_nonexistent_dir_is_empty(tmp_path: Path) -> None:
    """A ``transcript_dir`` naming a dir that doesn't exist locally → ``[]``.

    The record names a run-id, but the sibling ``transcripts/<run-id>/`` dir was
    never created (a run not shared/pulled). Defensive: ``[]``, not an error.
    """
    ledger = _write_ledger(tmp_path, _RUN_WITH_TRANSCRIPTS_DOC)
    (record,) = load_ledger(ledger)

    # The dir is located but absent on disk.
    located = transcript_dir_for(record, ledger)
    assert located is not None and not located.exists()

    assert list_transcripts(record, ledger) == []


def test_list_transcripts_empty_dir_is_empty(tmp_path: Path) -> None:
    """A present-but-EMPTY run dir (no ``game-*.txt``) lists as ``[]``."""
    ledger = _write_ledger(tmp_path, _RUN_WITH_TRANSCRIPTS_DOC)
    (record,) = load_ledger(ledger)
    # Create the run dir but write no game files into it.
    _write_transcripts(ledger, "2026-06-18T14-32-05", {})

    located = transcript_dir_for(record, ledger)
    assert located is not None and located.is_dir()

    assert list_transcripts(record, ledger) == []


def test_read_transcript_missing_file_is_empty_string(tmp_path: Path) -> None:
    """``read_transcript`` on a missing file returns ``""`` (never raises).

    A transcript that vanished between listing and opening degrades to a blank
    view, not a traceback — the loader's defensive empty-string contract.
    """
    missing = tmp_path / "transcripts" / "2026-06-18T14-32-05" / "game-01.txt"
    assert not missing.exists()

    assert read_transcript(missing) == ""
