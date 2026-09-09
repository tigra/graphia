"""Spec 040 (The Moderator's Closing Story), Task 9.6 — the trio in the VIEWER.

WHAT THIS FILE PINS, AND WHY IT IS A SEPARATE FILE FROM TASK 9.4's. Tasks
9.1–9.5 shipped the ``quality.story_*`` trio into the ledger and stopped there:
:func:`~graphia.eval_ledger._render_quality_section` renders an **explicit field
list**, so a key written by the harness is invisible in ``make view-ledger``
until the reader is taught its name. Before this task ``story_missing`` and
``story_ending`` appeared **zero** times in ``src/graphia/eval_ledger.py``, and
an operator wanting the one signal that says *this run produced no stories at
all* had to open the raw YAML. Task 9.4 tests the WRITER
(``blunder_eval.render_record``); this file tests the READER
(``eval_ledger.render_detail``) — a different module, a different failure mode,
and one that no assertion in 9.4's four sections can reach.

THE ONE RULE EVERY TEST HERE CIRCLES. **Absence is a third case, not a falsy
one** (Task 9.3). Three states must stay mutually distinguishable in the
rendered pane:

* **absent** — a pre-040 record, played by a build with no story path, carrying
  no denominator at all ⇒ **no lines**, and not one byte of difference from the
  render it had before this task existed;
* **a clean ``0.0``** — every ending told its story ⇒ **shown**, because it is
  the single most informative result the trio can carry and the whole point of
  measuring;
* **a bad rate** — endings that told no story ⇒ shown, and visibly bad.

Collapsing the first two is the failure this file exists to prevent, and it is
the same trap Task 9.4 names on the writer's side: gate on the numerator, or on
truthiness of anything, and the clean reading disappears into "no stories ran".

WHAT IS DELIBERATELY *NOT* TESTED HERE, recorded so the gaps read as choices:

* **A table column.** Task 9.3 forbids one and its reasoning stands — nothing is
  paired, and a story-quality *rate* would invent the scorer spec 040 disowns.
  This file asserts the negative once
  (:func:`test_no_story_column_entered_the_table_or_the_metric_tail`) precisely
  because the column machinery lives in the same module as the change.
* **``METRICS_VERSION``.** Untouched, and already pinned by name in
  ``tests/test_substitution_ledger.py::test_METRICS_VERSION_is_still_one``.
* **Search scoping.** No ``quality`` field of any spec is in ``_search_blob`` /
  ``SEARCH_FIELDS`` — 039's diary trio included — so staying out of them mirrors
  the precedent exactly rather than being an omission.
* **Widget composition.** ``LedgerViewerApp`` wraps this string in a single
  ``Static`` (``src/graphia/ui/ledger_viewer.py``, ``DetailScreen.compose``), so
  the change is string rendering end to end and there is no widget to snapshot.

NO WRITE REACHES THE COMMITTED LEDGER. Every fixture record is a hand-built
document under ``tmp_path``; the one round-trip test redirects
``blunder_eval.LEDGER_PATH`` and ``blunder_eval.TRANSCRIPTS_ROOT`` at ``tmp_path``
as well as passing an explicit path. Section 4 opens
``evals/blunder-ledger.yaml`` **read-only**.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphia.eval_ledger import (
    METRIC_ORDER,
    _DIARY_QUALITY_FIELDS,
    _STORY_QUALITY_FIELDS,
    _STORY_QUALITY_GATE,
    build_table_model,
    load_ledger,
    render_detail,
)
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import EvalResult, append_record

# ==========================================================================
# Fixtures: hand-built record documents, one conditional band at a time
# ==========================================================================

_DIARY_LINES = (
    "  diary_fallback_rate: 0.25\n"
    "  diary_fallback_entries: 1\n"
    "  diary_entries_attempted: 4\n"
)

_STORY_LINES = (
    "  story_missing_rate: {rate}\n"
    "  story_missing_games: {missing}\n"
    "  story_ending_games: {endings}\n"
)

_SUBSTITUTION_LINES = (
    "  substitutions:\n"
    "    diary:\n"
    "      rate: 0.25\n"
    "      count: 1\n"
    "      attempted: 4\n"
)


def _doc(*, quality: str = "") -> str:
    """A spec-040-era record document; the ``quality`` extras are optional.

    With ``quality=""`` this is the shape of every record committed before spec
    040 — ``games_attempted`` / ``games_completed`` / ``games_failed_early`` and
    then straight to ``duration_seconds``. It carries those three counts on
    purpose: they are the fields a mis-keyed gate would latch onto, so "absent
    renders nothing" is a claim about the *story* keys and not about an empty
    block.
    """
    return (
        "run:\n"
        "  date: '2026-09-09'\n"
        "  metrics_version: 1\n"
        "code:\n"
        "  commit: '1111222233334444555566667777888899990000'\n"
        "  branch: 'spec-040-moderator-creative-recap'\n"
        "  dirty: false\n"
        "provider:\n"
        "  name: 'ollama'\n"
        "  large_model: 'qwen3-coder:30b'\n"
        "  small_model: 'qwen2.5:3b'\n"
        "settings:\n"
        "  games: 2\n"
        "  lineup:\n"
        "    num_citizens: 5\n"
        "    num_mafia: 2\n"
        "quality:\n"
        "  games_attempted: 2\n"
        "  games_completed: 2\n"
        "  games_failed_early: 0\n"
        f"{quality}"
        "  duration_seconds: 400.0\n"
        "metrics:\n"
        "  repetition:\n"
        "    rate: 0.5\n"
        "    count: 10\n"
        "    denominator: 20\n"
        "notes: ''\n"
    )


def _one(tmp_path: Path, doc: str) -> dict[str, Any]:
    """Load the single record from a one-document temp ledger.

    The temp-ledger convention ``tests/test_ledger_viewer.py`` established and
    spec 039's viewer tests reuse: never the committed file.
    """
    path = tmp_path / "blunder-ledger.yaml"
    path.write_text(f"---\n{doc}", encoding="utf-8")
    (record,) = load_ledger(path)
    return record


def _quality_labels(detail: str) -> list[str]:
    """The ``quality`` section's field labels, in render order."""
    lines = detail.splitlines()
    start = lines.index("quality")
    end = start + 1
    while end < len(lines) and lines[end].strip():
        end += 1
    return [line.split(":", 1)[0].strip() for line in lines[start + 1 : end]]


# ==========================================================================
# 1. The trio renders — in the writer's own position
# ==========================================================================


def test_the_trio_renders_in_writer_order_when_the_denominator_is_present(
    tmp_path: Path,
) -> None:
    """Rate → count → denominator, after 039's diary trio, before 041's census.

    RELATIVE positions, never a frozen ``quality`` key list. A full-list equality
    is a trap in both directions: it turns red the day any later spec adds a
    ``quality`` key (no defect to find), and Task 9.4 measured it going red under
    an unrelated canary — the shape used here is the one spec 041's own census
    test and Task 9.4's writer-order test both settled on.

    ``rindex`` for the wall clock because ``duration_seconds`` appears twice in a
    record (once under ``run``, once mirrored under ``quality``) and it is the
    ``quality`` one the trio must precede.
    """
    record = _one(
        tmp_path,
        _doc(
            quality=_DIARY_LINES
            + _STORY_LINES.format(rate="0.5", missing="1", endings="2")
            + _SUBSTITUTION_LINES
        ),
    )

    detail = render_detail(record)

    assert "  story_missing_rate: 0.5" in detail
    assert "  story_missing_games: 1" in detail
    assert "  story_ending_games: 2" in detail
    assert (
        detail.index("  diary_entries_attempted:")
        < detail.index("  story_missing_rate:")
        < detail.index("  story_missing_games:")
        < detail.index("  story_ending_games:")
        < detail.index("  substitutions:")
        < detail.rindex("  duration_seconds:")
    )
    # The rendered band IS the tuple — a field dropped from
    # ``_STORY_QUALITY_FIELDS`` fails here, and so does a reordering.
    labels = _quality_labels(detail)
    head = labels.index("story_missing_rate")
    assert labels[head : head + 3] == list(_STORY_QUALITY_FIELDS)


def test_the_rendered_rate_is_read_from_the_record_never_recomputed(
    tmp_path: Path,
) -> None:
    """A record whose stored rate disagrees with its operands renders the STORED rate.

    ``render_record`` derives the rate once, from the count/denominator pair,
    precisely so there is one definition of it. A second derivation in the reader
    would render a self-contradicting record as though it agreed — hiding the
    contradiction rather than showing it. (039's identical guard, restated for
    this trio because the tuple is a separate call site.)
    """
    record = _one(
        tmp_path, _doc(quality=_STORY_LINES.format(rate="0.9", missing="1", endings="10"))
    )

    detail = render_detail(record)

    assert "  story_missing_rate: 0.9" in detail
    assert "  story_missing_rate: 0.1" not in detail


# ==========================================================================
# 2. Absence, and the clean zero — the two states that must not collapse
# ==========================================================================


def test_an_absent_denominator_renders_nothing_at_all(tmp_path: Path) -> None:
    """A pre-040 record gains not one line — the additive claim, as an equality.

    Stated as "the story-bearing render minus exactly the three story lines IS
    the storyless render" rather than as a substring absence, because the
    stronger form is what catches a gate keyed on a field *every* record carries
    (``quality.games_attempted``, say): the trio would then render as three
    ``—`` lines onto every one of the committed records, and a bare
    ``"story" not in detail`` check would catch that too — but only this form
    also catches the trio landing in the wrong band.
    """
    absent = _one(tmp_path, _doc())
    bearing = _one(
        tmp_path, _doc(quality=_STORY_LINES.format(rate="0.5", missing="1", endings="2"))
    )

    absent_detail = render_detail(absent)
    bearing_detail = render_detail(bearing)

    assert "story" not in absent_detail
    for name in _STORY_QUALITY_FIELDS:
        assert name not in absent_detail
    inserted = [
        line
        for line in bearing_detail.splitlines()
        if line not in absent_detail.splitlines()
    ]
    assert inserted == [
        "  story_missing_rate: 0.5",
        "  story_missing_games: 1",
        "  story_ending_games: 2",
    ]


def test_a_clean_runs_zero_rate_is_shown_rather_than_hidden(tmp_path: Path) -> None:
    """THE ANTI-VACUITY CONTROL: ``story_missing_rate: 0.0`` renders, in full.

    Every ending told its story — the best result this trio can report, and the
    one a numerator-shaped gate deletes. The one-line change this kills is
    ``_STORY_QUALITY_GATE`` pointed at ``quality.story_missing_games`` (or the
    guard rewritten as a truthiness test on it): a clean run then renders exactly
    like a build that never had a story path, which is the *opposite* of what
    Slice 9 was written to make visible.

    Measured on the writer's side by Task 9.4 and re-measured here because the
    reader is a second, independent gate — the writer emitting the trio does not
    make the viewer show it.
    """
    clean = _one(
        tmp_path, _doc(quality=_STORY_LINES.format(rate="0.0", missing="0", endings="20"))
    )

    detail = render_detail(clean)

    assert "  story_missing_rate: 0.0" in detail
    assert "  story_missing_games: 0" in detail
    assert "  story_ending_games: 20" in detail


# ==========================================================================
# 3. WHICH key is the gate, and PRESENCE rather than truthiness
# ==========================================================================


def test_the_gate_names_the_denominator_and_the_denominator_ends_the_tuple() -> None:
    """The gate is the last element of the tuple, spelled once.

    Two facts a well-formed record cannot distinguish, so they are asserted
    directly: the gate names ``story_ending_games`` (the DENOMINATOR, mirroring
    the writer's own ``story_endings > 0``), and the tuple ends with the same key
    — the rate-then-count-then-denominator order the record itself is written in.
    """
    assert _STORY_QUALITY_GATE == "quality.story_ending_games"
    assert _STORY_QUALITY_FIELDS[-1] == _STORY_QUALITY_GATE.split(".", 1)[1]
    # Distinct families: no key is shared with 039's trio.
    assert not set(_STORY_QUALITY_FIELDS) & set(_DIARY_QUALITY_FIELDS)


def test_a_rate_written_without_its_denominator_renders_nothing(
    tmp_path: Path,
) -> None:
    """A rate is by contract never written alone, so a lone rate is not a measurement.

    Pins WHICH key the gate reads, which no well-formed record can: gate on
    ``story_missing_rate`` instead and this malformed record renders a rate with
    nothing to read it against.

    The negative is asserted over the ``story_`` PREFIX rather than as a full
    ``quality`` key-list equality — the frozen-list form turns red the first time
    any later spec adds an unconditional ``quality`` field, with no defect to
    find, and Task 9.4 measured exactly that going red under an unrelated canary.
    The positive half (the block still renders its own counts) is what keeps the
    prefix check from passing on a wrecked section.
    """
    record = _one(tmp_path, _doc(quality="  story_missing_rate: 0.75\n"))

    detail = render_detail(record)

    labels = _quality_labels(detail)
    assert not [label for label in labels if label.startswith("story_")]
    assert {"games_attempted", "games_failed_early", "duration_seconds"} <= set(labels)


def test_a_present_zero_denominator_still_renders_the_trio(tmp_path: Path) -> None:
    """PRESENCE, not truthiness — the reader shows what the record says.

    ``render_record``'s gate is ``story_endings > 0``, so the writer cannot
    produce this document; a hand edit, a tidy-up script or a future writer
    change can. The reader's job then is to render it, not to silently re-classify
    a malformed record as a **pre-040** one — the two are different findings and
    an operator must be able to tell them apart. (039's ``_DIARY_QUALITY_GATE``
    has the same ``is not _MISSING`` semantics; this is the first place the
    property is written down as behaviour.)

    This is also the only witness that kills a gate rewritten as truthiness over
    the whole field group (``any(...)``): on a clean run the denominator is a
    truthy 20, so that mutation survives every other test in this file.
    """
    record = _one(
        tmp_path, _doc(quality=_STORY_LINES.format(rate="0.0", missing="0", endings="0"))
    )

    detail = render_detail(record)

    assert "  story_missing_rate: 0.0" in detail
    assert "  story_missing_games: 0" in detail
    assert "  story_ending_games: 0" in detail


def test_no_story_column_entered_the_table_or_the_metric_tail() -> None:
    """Task 9.3's "no table column" holds — asserted where the risk lives.

    The detail pane and the table model are the same module, so a scope creep
    from one into the other is a one-line change. Nothing about the trio is
    paired, and a story-quality *rate* column would need the scorer spec 040
    explicitly disowns.
    """
    columns = build_table_model([]).columns

    assert not [name for name in columns if "story" in name.lower()]
    assert not [name for name in METRIC_ORDER if "story" in name]


# ==========================================================================
# 4. The seam: the writer's keys and the reader's tuple are ONE contract
# ==========================================================================


def test_the_writers_own_record_renders_its_trio_through_the_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Harness → file → ``load_ledger`` → ``render_detail``, with no hand-typed keys.

    THE ANTI-VACUITY CONTROL FOR EVERY FIXTURE ABOVE. Those documents are typed
    by hand, so they agree with :data:`_STORY_QUALITY_FIELDS` by construction and
    a *coordinated* rename on both sides would leave them green while the real
    harness wrote keys the real viewer could not see. This test never spells a
    story key: it hands the writer a ``{count, denominator}`` pair and asserts
    the reader found three story lines in the record the writer actually
    produced.
    """
    monkeypatch.setattr(
        blunder_eval, "LEDGER_PATH", tmp_path / "fallback" / "ledger.yaml"
    )
    monkeypatch.setattr(
        blunder_eval, "TRANSCRIPTS_ROOT", tmp_path / "fallback-transcripts"
    )
    result = EvalResult(
        provider="ollama",
        games_attempted=4,
        games_completed=4,
        story_missing={"count": 1, "denominator": 4},
        duration_seconds=42.5,
    )

    path = append_record(result, "2026-09-09", ledger_path=tmp_path / "run" / "l.yaml")
    (record,) = load_ledger(path)
    detail = render_detail(record)

    rendered = [
        label for label in _quality_labels(detail) if label.startswith("story_")
    ]
    assert rendered == list(_STORY_QUALITY_FIELDS)
    assert "  story_missing_rate: 0.25" in detail
    assert "  story_ending_games: 4" in detail


# ==========================================================================
# 5. A read-only sweep over the COMMITTED ledger
#
# Never a write target. Written as an *iff* rather than as today's census (0 of
# N on both counts) so it stays true once spec 040's own measured campaign
# appends records that DO carry the trio.
# ==========================================================================

_COMMITTED_LEDGER = (
    Path(__file__).resolve().parents[1] / "evals" / "blunder-ledger.yaml"
)
_requires_ledger = pytest.mark.skipif(
    not _COMMITTED_LEDGER.is_file(),
    reason=f"no committed ledger at {_COMMITTED_LEDGER}",
)


@_requires_ledger
def test_every_committed_record_renders_the_trio_iff_it_carries_the_denominator() -> None:
    """The conditional render, proven over the real curated corpus.

    A fixture that agrees with the reader is weak evidence for "no committed
    record gains a line". This opens the repo-committed, append-only ledger
    **read-only** and checks the biconditional record by record, so a gate keyed
    on a field every record carries fails here over 20-plus real documents.
    """
    records = load_ledger(_COMMITTED_LEDGER)

    assert records, "the committed ledger parsed to zero records"
    for index, record in enumerate(records):
        quality = record.get("quality")
        carries = isinstance(quality, dict) and "story_ending_games" in quality
        detail = render_detail(record)
        shown = [name for name in _STORY_QUALITY_FIELDS if f"  {name}:" in detail]
        assert bool(shown) is carries, (
            f"record {index}: carries={carries} but rendered {shown}"
        )
        if carries:
            assert shown == list(_STORY_QUALITY_FIELDS)
