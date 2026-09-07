"""Spec 041 (Reliable Structured Decoding) — Slice 7: the counts in the ledger.

The reading half of the substitution census. Slice 6 made every substitution
*countable* at its source; Tasks 7.1 and 7.2 carried those counts into the
record (``quality.substitutions``, plus ``provider.structured_output``) and back
out through the viewer. This module pins both ends **and the seam between
them**, because the seam is where this shape has failed before: a writer and a
reader that each hold their own copy of a key list drift silently, and the
drift is invisible until somebody compares two records by hand.

What is pinned, and why each one is not free
--------------------------------------------

1. **A rate AND a denominator for every attempted kind.** All seven, swept.
   ``rate`` is derived at write time from the pair, so the assertion is
   ``rate == count / attempted`` at full precision — not a re-derivation of the
   same division, which would agree with a writer that stored a rate its own
   operands contradict.

2. **A never-attempted kind is ABSENT, not zero — per kind.** Both directions
   in one sweep: the kind under test is omitted while its six siblings render,
   so a writer that emitted ``0.0`` for the missing one *and* a writer that
   dropped the whole block both fail. And ``count: 0`` over a real denominator
   still renders ``rate: 0.0``, because a clean measurement is a measurement —
   the distinction the block would lose if presence were inferred from
   truthiness.

3. **The natural absences are DRIVEN, not synthesised.** ``reflection`` and
   ``ballot`` are absent from real runs for structural reasons — no Day round
   completed, no vote ever opened — so two mocked games are driven through
   ``run_eval`` against the real graph: a three-seat game that ends on Night 1
   (no Day at all ⇒ no ``day_action``, ``reflection``, ``ballot`` or ``diary``)
   and a four-seat game that completes a Day (``day_action`` / ``reflection`` /
   ``diary`` present, ``ballot`` still absent because nobody calls a vote). A
   synthetic ``EvalResult`` can only prove the renderer omits a key it was not
   given; these prove the *harness* never books the attempt.

4. **One vocabulary.** ``blunder_eval`` and ``eval_ledger`` both iterate
   :class:`~graphia.substitution.SubstitutionKind` (Task 7.2 reversed the
   duplicate-the-names instruction on measured evidence — ``substitution.py``
   is a stdlib-only leaf). Task 7.3's original "the duplicated seven names
   agree with the enum" is therefore vacuous and is NOT written here. What is
   written instead is the pair of invariants that survive: **every enum member
   carrying a denominator renders, in enum order**, and **no key is dropped** —
   including a kind this reader has never heard of, which the renderer passes
   through on purpose (an eighth-schema build's kind must render rather than
   vanish; a measured kind shown as absent is this slice's failure with the
   sign flipped). A straight ``set(rendered) == set(SubstitutionKind)`` would
   forbid exactly that pass-through, so it is not asserted.

5. **The metric tail is untouched.** Task 7.2 added **two** fixed columns
   (``Transport``, ``Subst (worst)``), so "no substitution key entered
   ``METRIC_ORDER``" is too weak on its own — a regression that moved either
   column into the right-justified tail would pass it. The assertion is the
   split itself: ``columns[len(columns) - len(METRIC_ORDER):] == [label for _,
   label in METRIC_ORDER]``, with both new columns proven to be in the head.

6. **``METRICS_VERSION`` is unchanged by an additive block.** Pinned to the
   bare literal ``1`` *and* differentially: two records identical but for the
   census differ **only** by the census's own lines.

7. **The two diary instruments agree on a driven game — and the cross-check
   fires when they cannot.** ``score_diary_fallback`` matches placeholder TEXT
   in ``private_diaries``; the sink counts substitutions AT THE SOURCE. They
   read one channel by two routes, so a test that scripts the model to answer
   with ``_DIARY_FALLBACK``'s own sentence makes them disagree **with no bug
   present** (Task 7.1 demonstrated that deliberately: sentinel 18/18, sink
   0/18). So the agreement test scripts diary text that is emphatically NOT the
   fallback sentence, and a companion test scripts the sentence itself and
   asserts the harness's per-game warning fires with both figures recorded and
   neither reconciled.

The committed ledger is NEVER a write target
--------------------------------------------

Every write in this module lands in ``tmp_path``. The convention is
``tests/test_slice39_diary_measurement.py``'s and it is followed here to the
letter: ``args.ledger_path`` / ``transcripts_root`` are injected **and** the
module globals ``blunder_eval.LEDGER_PATH`` / ``blunder_eval.TRANSCRIPTS_ROOT``
are monkeypatched at ``tmp_path`` as belt-and-braces, so no code path — not
even one that ignored the arguments — can reach ``evals/blunder-ledger.yaml``
or ``evals/transcripts/``. This is not hygiene theatre: an early-bound
signature default once wrote ~25 synthetic records into the committed ledger,
and Task 6.5 found that ``--games 0`` appends a real (empty) record rather than
doing nothing. :func:`test_append_record_resolves_the_module_global_at_call_time`
is the standing guard on the seam that failed, and
:func:`test_the_committed_ledgers_substitution_blocks_are_well_formed` reads the
real file (read-only, the one place it is touched at all) and would go red on a
synthetic record that leaked into it.

**``ledger_path`` must be a ``Path``, not a ``str``** — a thin
``argparse.Namespace`` carrying a string dies inside ``append_record`` with
``AttributeError: 'str' object has no attribute 'parent'``. Task 7.1 hit it;
:func:`test_the_injected_ledger_path_must_be_a_Path_not_a_str` pins it so the
next author meets it as a test failure rather than as a mid-run crash.

There is deliberately NO module-local sink-reset fixture here
-------------------------------------------------------------

Every other module that installs an accumulating sink (``run_eval`` installs
one per game; several tests below install one directly) carries its own autouse
reset. This one does not, **on purpose**: Task 7.3 added a global
``reset_substitution_sink`` teardown to ``tests/conftest.py``, and the probe
pair at the bottom of this file is the proof that it works. A module-local
fixture here would make that pair a test of the local fixture instead, which is
precisely the coverage that already exists in
``tests/test_substitution_counts.py`` (whose module-local fixture stays — belt
and braces cost nothing there, where the probes are its own).

Anti-vacuity, measured 2026-09-07
---------------------------------

Each of these was observed failing under a deliberate, reverted mutation of
``src/`` (from 79 green):

* **The "absent, not zero" rule has TWO independent gates, and it takes both to
  break it.** ``run_eval`` drops a kind with no attempts before folding it into
  the result, and ``render_record`` drops one with a zero denominator before
  writing it. Breaking only the writer's gate (``if attempted <= 0: continue``
  replaced by a zero-safe division, so an unattempted kind renders ``rate:
  0.0``) turns the **7** synthetic sweep cases red and leaves the two driven
  tests green; breaking only the harness's fold turns **nothing** red. With
  **both** gates removed: **9 red** — the seven synthetic cases plus both driven
  natural-absence tests, the second reporting ``ballot`` where no vote was ever
  opened. That redundancy is worth knowing rather than assuming, and it is why
  the driven pair is not redundant with the sweep: they guard different gates.
* **``_substitution_lines`` iterating the RECORD's key order instead of the
  enum's** — 2 red (the enum-order test and the unknown-kind tail position).
* **``_substitution_lines`` iterating ONLY the enum, dropping unknown keys** —
  2 red (the pass-through test and the no-key-dropped test).
* **``Subst (worst)`` moved out of the fixed head and into the right-justified
  tail** (appended after the metric labels in ``build_table_model``) — 2 red
  here, the tail-split test and the column-position test, plus 3 red in
  ``tests/test_ledger_model.py``, which already pins the head list and the tail
  by index. ``METRIC_ORDER`` itself is untouched by that mutation, so the weaker
  "no substitution key entered ``METRIC_ORDER``" assertion stays green through
  it — which is why the split, and not that weaker form, is what this file
  asserts. What had NO coverage anywhere before this file is the two new cells'
  **content**: ``tests/test_ledger_model.py`` pins the headers and nothing else,
  so ``_worst_substitution_cell``'s tie-break (a tie was the common case in
  Task 7.1's first real run) and ``_transport_cell``'s absent-vs-verbatim
  behaviour were unguarded beyond Task 7.2's manual check until now.
* **``autouse=True`` removed from ``conftest.reset_substitution_sink``** — the
  cross-module leak, constructed and caught. With the global reset gone AND
  ``tests/test_substitution_counts.py``'s module-local reset disabled, running
  ``test_leak_probe_a_…`` from this file followed by that module's
  ``test_a_forced_substitution_leaves_one_warning_from_the_default_sink`` fails
  in the SECOND module (``assert 0 == 1`` — it counted no
  ``graphia.substitution`` warning, because this file's leaked sink had replaced
  production's ``default_sink``) while probe A stays green: the silent-failure
  signature. Restoring only the global reset makes that pair green again, with
  the other module's local fixture still disabled — which is the whole argument
  for putting the reset in ``conftest.py``. Across both whole modules the same
  double mutation is 4 red (this file's structural autouse check and probe B,
  plus that module's own pair — exactly the two tests Task 6.3 measured).

One flake was found and removed while writing this file, and it is recorded
because it is a trap for the next author: pointing the fake at a LIVE
``graph.get_state()`` read from inside the dispatcher (the shape
``tests/test_blunder_eval.py`` uses) booked a ``pointing`` substitution in 1 run
in 8 of a fully scripted game — a false positive manufactured by the test's own
apparatus. See :class:`_ScriptedTable` for what replaced it.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import re
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from langgraph.types import Command

import conftest as suite_conftest
import graphia.substitution as substitution
from graphia.eval_ledger import (
    METRIC_ORDER,
    _ABSENT,
    _FIXED_COLUMNS,
    _SUBSTITUTION_FACET_FIELDS,
    _SUBSTITUTION_GATE_FIELD,
    _substitution_lines,
    _transport_cell,
    _worst_substitution_cell,
    build_table_model,
    load_ledger,
    render_detail,
)
from graphia.llm import Ballot, DayAction, Diary, Pointing
# The node's deterministic placeholder, IMPORTED (never copied) — the same loud
# coupling ``tests/test_slice39_diary_measurement.py`` keeps: a reword breaks
# this file at import time instead of quietly turning an all-placeholder run
# into a clean-looking 0.0.
from graphia.nodes.day import _DIARY_FALLBACK
from graphia.substitution import (
    SubstitutionEvent,
    SubstitutionKind,
    default_sink,
    set_sink,
)
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    METRICS_VERSION,
    EvalResult,
    _GameCapture,
    _structured_output_transport,
    _SubstitutionCounter,
    _ai_lines_with_names,
    _ai_lines_with_speakers,
    append_record,
    render_record,
    run_eval,
)
from graphia.tools.eval_dialogue import _collect_interrupt, _drive

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMITTED_LEDGER = _REPO_ROOT / "evals" / "blunder-ledger.yaml"

_HUMAN_NAME = "Alice"
_AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]

# Diary text that is emphatically NOT ``_DIARY_FALLBACK``, for the agreement
# test. It has to differ from the placeholder for the two instruments to be
# comparable at all (see the module docstring, point 7).
_REAL_DIARY_TEXT = "The tavern keeper answered a question nobody asked. Watch him."

# The kind-header line the renderer emits inside the sub-block: ``    diary:``
# at four spaces of indent, one nesting level below ``  substitutions:``.
_KIND_HEADER = re.compile(r"^ {4}([A-Za-z0-9_]+):$")


# ==========================================================================
# Shared scaffolding
# ==========================================================================


def _result(
    substitutions: dict[str, dict[str, int]] | None = None,
    **fields: Any,
) -> EvalResult:
    """A minimal synthetic ``EvalResult`` carrying only what is under test."""
    result = EvalResult(provider="ollama", **fields)
    if substitutions is not None:
        result.substitutions = substitutions
    return result


def _full_census(count: int = 1, attempted: int = 2) -> dict[str, dict[str, int]]:
    """All seven kinds attempted — the every-kind-present shape."""
    return {
        kind.value: {"count": count, "attempted": attempted}
        for kind in SubstitutionKind
    }


def _record_of(result: EvalResult) -> dict[str, Any]:
    """Render ``result`` and parse it back as the viewer's reader would see it.

    Round-tripping through YAML rather than asserting on the rendered text is
    what makes the writer/reader seam testable at all: the record on disk is the
    contract, and both halves are pinned against the same parsed mapping.
    """
    return yaml.safe_load(render_record(result, "2026-09-07"))


def _raw_record(substitutions: Any, **quality: Any) -> dict[str, Any]:
    """A hand-built RAW record for the reader — only the keys the cells read."""
    block: dict[str, Any] = {"games_attempted": 1, "games_completed": 1, **quality}
    if substitutions is not None:
        block["substitutions"] = substitutions
    return {"quality": block}


def _rendered_kinds(record: dict[str, Any]) -> list[str]:
    """The kind headers ``_substitution_lines`` emitted, in render order."""
    return [
        match.group(1)
        for match in (_KIND_HEADER.match(line) for line in _substitution_lines(record))
        if match
    ]


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The redirected ledger path, with both module globals pointed at ``tmp_path``.

    THE ONLY WRITE TARGET IN THIS MODULE. The returned path is what tests pass
    as ``args.ledger_path`` (as a ``Path`` — see the module docstring); the two
    ``monkeypatch.setattr`` calls are the belt-and-braces layer that makes the
    committed ledger and the committed transcripts dir unreachable even from a
    code path that ignored the argument entirely.
    """
    monkeypatch.setattr(
        blunder_eval, "LEDGER_PATH", tmp_path / "fallback" / "ledger.yaml"
    )
    monkeypatch.setattr(
        blunder_eval, "TRANSCRIPTS_ROOT", tmp_path / "fallback-transcripts"
    )
    return tmp_path / "ledger" / "run-ledger.yaml"


# ==========================================================================
# 1. The writer: a rate and a denominator for every attempted kind
# ==========================================================================


@pytest.mark.parametrize("kind", list(SubstitutionKind), ids=lambda k: k.value)
@pytest.mark.parametrize(
    ("count", "attempted"),
    [
        pytest.param(0, 1, id="clean"),
        pytest.param(3, 108, id="partial"),
        pytest.param(18, 18, id="total"),
    ],
)
def test_every_attempted_kind_carries_a_rate_and_its_denominator(
    kind: SubstitutionKind, count: int, attempted: int
) -> None:
    """Each of the seven renders ``{rate, count, attempted}``, rate from the pair.

    The rate is asserted against ``count / attempted`` computed here, which is
    the one derivation the writer promises to have done exactly once. Full
    precision on purpose: ``3/108`` is ``0.027777777777777776`` in the record,
    and a writer that rounded for display would be storing a number that no
    longer reproduces its own operands.
    """
    record = _record_of(_result({kind.value: {"count": count, "attempted": attempted}}))

    facets = record["quality"]["substitutions"][kind.value]
    assert list(facets) == list(_SUBSTITUTION_FACET_FIELDS)
    assert facets["count"] == count
    assert facets["attempted"] == attempted
    assert facets["rate"] == count / attempted
    # The denominator is never absent from a written rate — the whole point of
    # the pair, and the rule the diary trio established.
    assert _SUBSTITUTION_GATE_FIELD in facets


def test_the_census_sits_between_the_diary_trio_and_the_wall_clock() -> None:
    """Key order inside ``quality``: the diary trio, the census, ``duration_seconds``.

    Position is contract, not cosmetics: the census belongs beside
    ``games_failed_early`` — the other *did this run measure anything?* count —
    rather than trailing after the wall clock, and ``duration_seconds`` stays
    the last key of ``quality`` exactly as in all 41 committed records.
    """
    result = _result(_full_census())
    result.diary_fallback = {"count": 2, "denominator": 4}

    text = render_record(result, "2026-09-07")

    # ``rindex`` for the wall clock: ``duration_seconds`` appears TWICE in a
    # record (once in ``run``, once mirrored in ``quality``), and it is the
    # ``quality`` one — the last key of that block in all 41 committed records
    # — that the census has to precede.
    assert (
        text.index("  diary_entries_attempted:")
        < text.index("  substitutions:")
        < text.rindex("  duration_seconds:")
    )


@pytest.mark.parametrize("absent", list(SubstitutionKind), ids=lambda k: k.value)
def test_a_never_attempted_kind_is_absent_not_zero(absent: SubstitutionKind) -> None:
    """Per kind: no denominator ⇒ no entry at all, while its six siblings render.

    Both halves in one assertion set, because either alone is vacuous. Omitting
    the whole block when one kind is missing would pass a "the absent kind has
    no line" check; emitting ``rate: 0.0`` for it would pass a "the six others
    render" check.

    Mutation-measured: making ``render_record`` emit the zero triple instead of
    ``continue``-ing turns all seven of these red (and nothing about the value
    ``0.0`` is wrong — a ``count: 0`` over a real denominator is the *clean*
    reading, pinned by the ``clean`` case in the sweep above).
    """
    census = _full_census()
    census[absent.value] = {"count": 0, "attempted": 0}

    record = _record_of(_result(census))

    written = record["quality"]["substitutions"]
    assert absent.value not in written, (
        f"{absent.value} had no opportunity (attempted: 0) and must be ABSENT "
        "from the record — a zero rate over a zero denominator is not a "
        "measurement, and reads as 'nothing substituted'"
    )
    assert set(written) == {k.value for k in SubstitutionKind} - {absent.value}


def test_a_run_that_attempted_nothing_writes_no_census_block_at_all() -> None:
    """An empty census adds not one line — what keeps pre-041 records identical."""
    text = render_record(_result({}), "2026-09-07")

    assert "substitutions:" not in text
    for kind in SubstitutionKind:
        assert f"    {kind.value}:" not in text


def test_the_writers_key_order_is_the_enum_order_not_the_results() -> None:
    """A result whose kinds were inserted backwards still renders canonically.

    The writer iterates the vocabulary, not the result dict, so a hand-built
    ``EvalResult`` (or a harness that folded its games in some other order)
    cannot re-order a committed record's keys.
    """
    reversed_census = {
        kind.value: {"count": 1, "attempted": 4}
        for kind in reversed(list(SubstitutionKind))
    }

    record = _record_of(_result(reversed_census))

    assert list(record["quality"]["substitutions"]) == [
        kind.value for kind in SubstitutionKind
    ]


def test_the_writer_and_the_reader_name_the_same_three_facets() -> None:
    """The writer's key list and the reader's field tuple are one contract.

    This is the drift test that is NOT vacuous. The kind *vocabulary* is now
    derived from one enum on both sides (Task 7.2), so asserting it agrees with
    itself proves nothing — but the three FACET names are still written as a
    dict literal in ``blunder_eval.render_record`` and read from
    ``eval_ledger._SUBSTITUTION_FACET_FIELDS``, two separate declarations that
    can genuinely disagree.
    """
    record = _record_of(_result(_full_census()))

    for kind, facets in record["quality"]["substitutions"].items():
        assert list(facets) == list(_SUBSTITUTION_FACET_FIELDS), (
            f"{kind}'s written facet order does not match the reader's field "
            "list — the two would render a record neither of them describes"
        )


# ==========================================================================
# 2. METRICS_VERSION: additive means additive
# ==========================================================================


def test_METRICS_VERSION_is_still_one() -> None:
    """The bare literal, not the constant read back through itself.

    Spec 041 changed the transport and added two record fields. Neither is what
    ``METRICS_VERSION`` versions: no detection rule changed, no denominator
    moved. A bump would have falsely flagged every committed rate as
    incomparable across the boundary.
    """
    assert METRICS_VERSION == 1


def test_the_census_changes_nothing_else_in_the_record() -> None:
    """Two records identical but for the census differ ONLY by its own lines.

    The differential form of "purely additive". Asserting ``metrics_version: 1``
    is present would pass even if the census had displaced or re-spelled a
    neighbouring key; this compares the whole document line by line.
    """
    without = render_record(_result({}), "2026-09-07").splitlines()
    with_census = render_record(_result(_full_census()), "2026-09-07").splitlines()

    added = [line for line in with_census if line not in without]
    removed = [line for line in without if line not in with_census]

    assert removed == [], f"the census removed lines from the record: {removed}"
    assert added[0] == "  substitutions:"
    assert all(
        line.startswith("    ") or line == "  substitutions:" for line in added
    ), f"the census added lines outside its own sub-block: {added}"
    assert "  metrics_version: 1" in without
    assert "  metrics_version: 1" in with_census


# ==========================================================================
# 3. The reader: one vocabulary, enum order, and no key dropped
# ==========================================================================


def test_every_enum_member_with_a_denominator_renders_in_enum_order() -> None:
    """A scrambled record still reads in canonical (schema) order, all seven.

    The reader iterates the enum, so the record's own dict order cannot leak
    into the drill-down — which is what makes two records comparable by eye.
    """
    scrambled = {
        kind.value: {"rate": 0.5, "count": 1, "attempted": 2}
        for kind in reversed(list(SubstitutionKind))
    }

    assert _rendered_kinds(_raw_record(scrambled)) == [
        kind.value for kind in SubstitutionKind
    ]


def test_an_unknown_kind_renders_after_the_seven_in_the_records_own_order() -> None:
    """An eighth-schema build's kind is rendered, never silently dropped.

    Deliberate pass-through, and the reason a ``set(rendered) ==
    set(SubstitutionKind)`` assertion is wrong here: a kind that WAS measured
    rendering as absent is exactly the misreading this block exists to prevent,
    wearing the opposite sign. The seven known ones lead, in schema order; the
    strangers follow in the record's own order, since this reader has no opinion
    about where they belong.
    """
    record = _raw_record(
        {
            "seance": {"rate": 1.0, "count": 2, "attempted": 2},
            "diary": {"rate": 0.0, "count": 0, "attempted": 3},
            "augury": {"rate": 0.5, "count": 1, "attempted": 2},
        }
    )

    assert _rendered_kinds(record) == ["diary", "seance", "augury"]


def test_no_key_carrying_a_denominator_is_ever_dropped() -> None:
    """Everything the record states with a denominator reaches the drill-down."""
    stated = {
        **{
            kind.value: {"rate": 0.0, "count": 0, "attempted": 1}
            for kind in SubstitutionKind
        },
        "seance": {"rate": 1.0, "count": 1, "attempted": 1},
    }

    assert set(_rendered_kinds(_raw_record(stated))) == set(stated)


@pytest.mark.parametrize(
    ("facets", "id_"),
    [
        pytest.param({"rate": 0.5, "count": 1}, "no-denominator", id="no-denominator"),
        pytest.param("not-a-map", "scalar", id="scalar"),
        pytest.param(None, "null", id="null"),
    ],
)
def test_a_kind_without_a_usable_denominator_is_gated_out(
    facets: Any, id_: str
) -> None:
    """The per-kind gate is the DENOMINATOR's presence, defensively applied.

    ``_DIARY_QUALITY_GATE``'s rule restated once per kind. A malformed kind is
    skipped rather than crashing the drill-down — the posture every other cell
    in the viewer takes — while its well-formed siblings still render.
    """
    record = _raw_record(
        {
            "diary": facets,
            "pointing": {"rate": 0.0, "count": 0, "attempted": 4},
        }
    )

    assert _rendered_kinds(record) == ["pointing"]


def test_a_zero_rate_over_a_real_denominator_renders() -> None:
    """``count: 0, attempted: 9`` is a measurement, and reads as one.

    The anti-vacuity twin of the absent-not-zero rule: gating on truthiness
    would hide precisely the finding a clean run reports.
    """
    lines = _substitution_lines(
        _raw_record({"diary": {"rate": 0.0, "count": 0, "attempted": 9}})
    )

    assert lines[0] == "  substitutions:"
    assert "    diary:" in lines
    assert any(line.strip() == "rate: 0.0" for line in lines)
    assert any(line.strip() == "attempted: 9" for line in lines)


def test_an_absent_or_empty_census_adds_not_one_line() -> None:
    """What keeps every pre-041 record's drill-down byte-identical."""
    assert _substitution_lines(_raw_record(None)) == []
    assert _substitution_lines(_raw_record({})) == []
    assert _substitution_lines(_raw_record("nonsense")) == []
    assert _substitution_lines({}) == []


def test_the_facet_lines_follow_the_readers_field_order() -> None:
    """``rate`` → ``count`` → ``attempted``, at the record's own nesting depth."""
    lines = _substitution_lines(
        _raw_record({"diary": {"rate": 0.25, "count": 1, "attempted": 4}})
    )

    assert lines == [
        "  substitutions:",
        "    diary:",
        "      rate: 0.25",
        "      count: 1",
        "      attempted: 4",
    ]


def test_the_round_trip_renders_the_written_census_unchanged(
    tmp_path: Path, ledger: Path
) -> None:
    """Writer → file → ``load_ledger`` → ``render_detail``, all seven kinds.

    The end-to-end seam test: a record written by the harness is read back by
    the model-free viewer and every kind, rate and denominator survives, in
    order, beside the untouched spec-039 diary trio.
    """
    result = _result(_full_census(count=1, attempted=4))
    result.diary_fallback = {"count": 1, "denominator": 4}

    path = append_record(result, "2026-09-07", ledger_path=ledger)
    records = load_ledger(path)

    assert len(records) == 1
    detail = render_detail(records[0])
    assert "  substitutions:" in detail
    for kind in SubstitutionKind:
        assert f"    {kind.value}:" in detail
    # The trio is untouched and still leads the census.
    assert (
        detail.index("diary_entries_attempted")
        < detail.index("  substitutions:")
        < detail.rindex("duration_seconds")
    )
    assert _rendered_kinds(records[0]) == [kind.value for kind in SubstitutionKind]


# ==========================================================================
# 4. The table: two new FIXED columns, and an untouched metric tail
# ==========================================================================


def test_the_metric_tail_is_exactly_METRIC_ORDER_after_the_two_new_columns() -> None:
    """The right-justify split still keys off the metric count.

    Task 7.2 added ``Transport`` and ``Subst (worst)`` as FIXED (head) columns.
    Asserting only that no substitution key entered ``METRIC_ORDER`` is too
    weak — measured: moving ``Subst (worst)`` into ``METRIC_ORDER`` leaves that
    weaker assertion green. This asserts the split the UI actually uses, so
    either column drifting into the tail fails here.
    """
    columns = build_table_model([]).columns
    tail_size = len(METRIC_ORDER)

    assert columns[len(columns) - tail_size :] == [
        label for _, label in METRIC_ORDER
    ]
    head = columns[: len(columns) - tail_size]
    assert head == list(_FIXED_COLUMNS)
    assert "Transport" in head
    assert "Subst (worst)" in head
    # And nothing substitution- or transport-shaped hides among the metric keys.
    metric_keys = {key for key, _ in METRIC_ORDER}
    assert not {key for key in metric_keys if "substitut" in key or "structured" in key}


def test_the_two_new_columns_sit_where_they_are_meant_to_read() -> None:
    """``Transport`` follows the model ids; ``Subst (worst)`` precedes ``Notes``.

    Position is the point of both cells: the transport mirrors the record's own
    ``provider`` key order, and the worst-substitution warning has to reach the
    reader BEFORE the metric block it qualifies — a run that was a third
    stand-in answers is not a measurement of the model, and that warning is
    worthless after the rates.
    """
    columns = build_table_model([]).columns

    assert columns[columns.index("Small model") + 1] == "Transport"
    assert columns[columns.index("Notes") - 1] == "Subst (worst)"
    assert columns.index("Subst (worst)") < columns.index(METRIC_ORDER[0][1])


def test_a_rows_cells_still_match_the_column_count() -> None:
    """One cell per column, on a record carrying both new keys and on one without."""
    model = build_table_model(
        [
            {
                "provider": {"structured_output": "json_schema"},
                "quality": {"substitutions": {"diary": {"rate": 0.5, "count": 1, "attempted": 2}}},
            },
            {"run": {"date": "2026-01-01"}},
        ]
    )

    assert all(len(row) == len(model.columns) for row in model.rows)


@pytest.mark.parametrize(
    ("census", "expected"),
    [
        pytest.param(
            {"diary": {"rate": 0.5, "count": 9, "attempted": 18}},
            "diary 0.50",
            id="single-kind",
        ),
        pytest.param(
            {
                "day_action": {"rate": 0.027777777777777776, "count": 3, "attempted": 108},
                "diary": {"rate": 0.5, "count": 9, "attempted": 18},
            },
            "diary 0.50",
            id="worst-of-two",
        ),
        pytest.param(
            {"pointing": {"rate": 0.0, "count": 0, "attempted": 20}},
            "pointing 0.00",
            id="a-clean-run-still-names-a-kind",
        ),
        pytest.param(None, _ABSENT, id="absent-block"),
        pytest.param({}, _ABSENT, id="empty-block"),
        pytest.param(
            {"diary": {"rate": 1.0, "count": 1, "attempted": 0}},
            _ABSENT,
            id="zero-denominator-is-not-a-reading",
        ),
        pytest.param(
            {"diary": {"count": 1, "attempted": 2}},
            _ABSENT,
            id="malformed-kind-is-not-a-candidate",
        ),
    ],
)
def test_the_worst_substitution_cell_names_the_kind_and_its_rate(
    census: Any, expected: str
) -> None:
    """``diary 0.50`` — the name is half the cell, and absence claims nothing.

    A bare ``0.50`` says a run was half stand-in answers and not which channel
    is unusable, which is the difference between "ignore the diary reading" and
    "ignore the record". Absence renders ``—`` rather than blank or ``0.00``:
    blank in a column of rates invites the one misreading this whole block
    exists to prevent.
    """
    assert _worst_substitution_cell(_raw_record(census)) == expected


def test_the_worst_cell_breaks_a_rate_tie_on_the_larger_denominator() -> None:
    """Equal rates ⇒ the better-evidenced kind wins.

    ``1.0`` over 18 attempts is a finding; ``1.0`` over 1 is a coin toss. Ties
    are the COMMON case here — Task 7.1's own first run had ``pointing``,
    ``reflection`` and ``diary`` all at ``1.0`` — so this rule is what makes the
    cell reproducible instead of incidental.
    """
    cell = _worst_substitution_cell(
        _raw_record(
            {
                "pointing": {"rate": 1.0, "count": 1, "attempted": 1},
                "diary": {"rate": 1.0, "count": 18, "attempted": 18},
            }
        )
    )

    assert cell == "diary 1.00"


def test_the_worst_cell_breaks_a_full_tie_on_schema_order_not_record_order() -> None:
    """Same rate, same denominator ⇒ ``SubstitutionKind`` declaration order.

    The record here lists ``diary`` FIRST, so a reader that fell back to dict
    order would answer ``diary``; the rule says the earlier schema wins, and
    ``pointing`` precedes ``diary`` in the enum. A total order is what keeps
    this cell a function of the data rather than of the writer's insertion
    sequence.
    """
    cell = _worst_substitution_cell(
        _raw_record(
            {
                "diary": {"rate": 1.0, "count": 4, "attempted": 4},
                "pointing": {"rate": 1.0, "count": 4, "attempted": 4},
            }
        )
    )

    assert cell == "pointing 1.00"


def test_an_unknown_kind_can_win_the_worst_cell() -> None:
    """A stranger at a higher rate is still the worst channel.

    A column reporting ``diary 0.50`` while the drill-down showed an unknown
    kind at ``1.00`` would understate the worst channel, which is the one thing
    this cell must not do. Strangers sort after the seven, but they compete.
    """
    cell = _worst_substitution_cell(
        _raw_record(
            {
                "diary": {"rate": 0.5, "count": 9, "attempted": 18},
                "seance": {"rate": 1.0, "count": 2, "attempted": 2},
            }
        )
    )

    assert cell == "seance 1.00"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        pytest.param(
            {"provider": {"structured_output": "json_schema"}},
            "json_schema",
            id="json_schema",
        ),
        pytest.param(
            {"provider": {"structured_output": "tool_use"}}, "tool_use", id="tool_use"
        ),
        pytest.param(
            {"provider": {"structured_output": "some_future_transport"}},
            "some_future_transport",
            id="verbatim-not-mapped",
        ),
        pytest.param({"provider": {"name": "ollama"}}, _ABSENT, id="pre-041-record"),
        pytest.param({}, _ABSENT, id="no-provider-block"),
    ],
)
def test_the_transport_cell_renders_verbatim_and_absence_as_the_em_dash(
    record: dict[str, Any], expected: str
) -> None:
    """Absence means *this record predates the label*, never a defaulted transport.

    Rendering ``tool_use`` for a pre-041 record would state a fact nobody
    recorded (however likely it is), which is the mistake ``_diaries_cell``
    refuses to make by rendering ``off`` for absence.
    """
    assert _transport_cell(record) == expected


def test_the_transport_reaches_the_provider_drill_down() -> None:
    """The label is READ, not merely written — the gap Task 7.1 caught.

    Slice 7's value statement is telling the three transport generations apart
    from the record itself, and ``_render_provider_section`` renders a fixed
    field list, so without this line the label would have been recorded and
    never displayed. Absence adds no line, which is what keeps the 41 committed
    records' detail views byte-identical.
    """
    labelled = render_detail(
        {"provider": {"name": "ollama", "structured_output": "json_schema"}}
    )
    unlabelled = render_detail({"provider": {"name": "ollama"}})

    assert "  structured_output: json_schema" in labelled
    assert "structured_output" not in unlabelled


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        pytest.param("ollama", "json_schema", id="ollama-grammar-constrained"),
        pytest.param("bedrock", "tool_use", id="bedrock-nova"),
        pytest.param("bedrock-claude", "tool_use", id="bedrock-claude"),
        pytest.param("no-such-provider", None, id="unknown-provider-gets-no-label"),
    ],
)
def test_the_transport_label_is_read_off_the_providers_own_declaration(
    provider: str, expected: str | None
) -> None:
    """The recorded label comes from ``structured_output_defaults``, not a table.

    Imported rather than copied, so removing ADR 013's ``method="json_schema"``
    pin shows up in the next record instead of nowhere. An unknown provider
    gets ``None`` and the caller omits the key — no plausible-looking wrong
    label.
    """
    assert _structured_output_transport(provider) == expected  # type: ignore[arg-type]


# ==========================================================================
# 5. Driven end to end: the natural absences, and the two diary instruments
# ==========================================================================


class _DiaryScript:
    """A deterministic diary policy for a driven game, and its own tally.

    Three modes, each answering the question a different test asks:

    - ``"alternate"`` — odd calls answer with :data:`_REAL_DIARY_TEXT`, even
      calls raise. The mix the agreement test needs, and it holds whatever size
      the fan-out turns out to be, so the assertion never depends on counting
      the game's writers by hand.
    - ``"placeholder"`` — every call answers with ``_DIARY_FALLBACK``'s own
      sentence. No substitution happens (the model answered), yet the sentinel
      matches every entry: the honest divergence that must fire the harness's
      cross-check warning.
    - ``"real"`` — every call answers with real text. No substitution, no
      placeholder, the two instruments both read zero.

    The tally is kept HERE, at the source of the decision, so the expected
    numbers in the assertions are the script's own record of what it did rather
    than a second guess at the game's shape.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.raised = 0

    def answer(self) -> Diary:
        self.calls += 1
        match self.mode:
            case "alternate" if self.calls % 2 == 0:
                self.raised += 1
                raise RuntimeError("diary model down")
            case "placeholder":
                return Diary(entry=_DIARY_FALLBACK)
            case _:
                return Diary(entry=f"{_REAL_DIARY_TEXT} (entry {self.calls})")


class _ScriptedTable:
    """The whole table's model answers for one driven game.

    ``DayAction`` is always a ``speak`` (so no AI ever CALLS a vote, which is
    what leaves ``ballot`` a *naturally* absent kind rather than a suppressed
    one), ``Ballot`` is always No (defensive — no vote is ever opened),
    ``Diary`` goes through the script, and ``Persona`` / ``Reflection`` fall
    through to the unified fake's own queues.

    ``Pointing`` targets a fresh **alive law-abiding AI**, so the law-abiding
    human is never the victim and each game ends by Night attrition reaching
    Mafia parity rather than by killing the seat that drives it.

    **Where the aliveness comes from, and why not from the graph.** The obvious
    shape is a live ``graph.get_state()`` read inside the dispatcher, which is
    what ``tests/test_blunder_eval.py`` does — and it is FLAKY here, measured at
    1 run in 8: a re-entrant state read from inside a running node can fail,
    ``_ai_pick_target`` swallows the exception, retries, fails again and returns
    its ``random.choice`` fallback. That books a ``pointing`` **substitution in
    a game where every answer was scripted** — a false positive manufactured by
    the test's own measuring apparatus, in a module whose whole subject is the
    honesty of that count. So the roster is read from the STREAMED event log
    instead: every ``{node: delta}`` super-step is already being taped for the
    transcript, several of those deltas carry ``players`` (``assign_roles``,
    ``generate_personas``, ``resolve_night_kill``, ``resolve_vote``), and
    merging them in order gives an up-to-date view — including aliveness —
    without touching the running graph at all.
    """

    def __init__(
        self,
        events: list[dict[str, Any]],
        diary: _DiaryScript,
        original_invoke: Callable[[type, Any], Any],
    ) -> None:
        self._events = events
        self._diary = diary
        self._original_invoke = original_invoke

    def _players(self) -> dict[str, Any]:
        """The table as the streamed deltas describe it, merged in order."""
        players: dict[str, Any] = {}
        for event in self._events:
            for delta in event.values():
                if isinstance(delta, dict) and isinstance(delta.get("players"), dict):
                    players.update(delta["players"])
        return players

    def _alive_law_abiding_ai(self) -> list[str]:
        return [
            p.id
            for p in self._players().values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]

    def __call__(self, schema: type, messages: Any) -> Any:
        if schema is Pointing:
            targets = self._alive_law_abiding_ai()
            if targets:
                return Pointing(target_id=targets[0])
            # Only reachable past the Mafia's win; a teammate id keeps the Night
            # resolving rather than hanging.
            mafia = [
                p.id
                for p in self._players().values()
                if p.is_alive and p.role == "mafia" and not p.is_human
            ]
            return Pointing(target_id=mafia[0] if mafia else "missing")
        if schema is DayAction:
            return DayAction(kind="speak", text="(nothing to add this round.)")
        if schema is Ballot:
            return Ballot(yes=False)
        if schema is Diary:
            return self._diary.answer()
        return self._original_invoke(schema, messages)


def _drive_to_the_natural_end(
    graph: Any, run_config: Any, *, events_sink: Callable[[dict], None], budget: int = 400
) -> None:
    """``_play_one_game``'s drive loop, model-free: name interrupt → … → END."""
    _drive(graph, run_config, {"messages": []}, on_update=events_sink)
    first = _collect_interrupt(graph, run_config)
    assert first == {"kind": "name"}, f"expected the name interrupt, got {first!r}"
    _drive(graph, run_config, Command(resume=_HUMAN_NAME), on_update=events_sink)

    for _ in range(budget):
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            return
        interrupt = _collect_interrupt(graph, run_config)
        if interrupt is None:
            _drive(graph, run_config, None, on_update=events_sink)
            continue
        kind = interrupt.get("kind")
        if kind == "day_turn":
            resume = "..."  # the human passes: never initiates a vote
        elif kind == "vote":
            resume = "no"
        elif kind == "point":
            options = interrupt.get("options") or []
            resume = options[0]["id"] if options else ""
        else:
            raise AssertionError(f"unexpected interrupt {kind!r}")
        _drive(graph, run_config, Command(resume=resume), on_update=events_sink)
    raise AssertionError("the driven game did not reach its natural end in budget")


def _make_play(
    fake: Any, diary: _DiaryScript
) -> Callable[[argparse.Namespace, int], _GameCapture]:
    """A ``_play_one_game`` replacement that drives the REAL graph, model-free.

    Substituted for ``blunder_eval._play_one_game`` so ``run_eval`` installs its
    accumulating sink around a genuine game: the counts below are booked by the
    real ``nodes/setup``, ``nodes/night`` and ``nodes/day`` helpers, not by a
    hand-built ``EvalResult``. That is the whole difference between proving the
    renderer omits a key it was not given and proving the harness never books
    the attempt.
    """

    def _play(args: argparse.Namespace, game_index: int) -> _GameCapture:
        from graphia.config import load_config
        from graphia.graph import build_graph, make_run_config

        config = load_config()
        graph, thread_id = build_graph(config)
        run_config = make_run_config(thread_id)

        events: list[dict[str, Any]] = []
        # Installed BEFORE any driving: persona generation and Night 1 both
        # happen inside the first ``_drive`` call, so a dispatcher installed
        # afterwards would let the fake's never-primed ``Pointing`` queue serve
        # its unscripted default into ``_ai_pick_target`` — an invalid id, and a
        # substitution booked by the test rather than by the game.
        fake._invoke = _ScriptedTable(  # type: ignore[method-assign]
            events, diary, fake._invoke
        )
        _drive_to_the_natural_end(graph, run_config, events_sink=events.append)

        state = graph.get_state(run_config).values
        lines, names = _ai_lines_with_names(state)
        return _GameCapture(
            ai_lines=lines,
            ai_names=names,
            ai_lines_with_speakers=_ai_lines_with_speakers(state),
            players=state.get("players", {}),
            messages=list(state.get("messages", [])),
            captures=[],
            winner=state.get("winner"),
            events=events,
            private_diaries=state.get("private_diaries", {}) or {},
            human_id=state.get("human_id", ""),
        )

    return _play


@pytest.fixture
def driven(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger: Path,
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
) -> Callable[..., tuple[EvalResult, dict[str, Any], _DiaryScript]]:
    """Drive one real mocked game through ``run_eval``; return result + record.

    Everything about the substitution census is REAL here: the sink install, the
    per-game isolation, the seven helpers' notifications, the fold into the
    record, the ledger write and the read-back. Stubbed only where a stub cannot
    affect the census — ``collect_code_provenance`` (a ``git status`` subprocess)
    — while ``collect_provider_provenance`` is left REAL so
    ``provider.structured_output`` lands end to end (the ``bedrock`` path takes
    no network).

    ``GRAPHIA_PERSONA_DIVERSITY`` is switched OFF for these runs, deliberately:
    with it on, ``persona.attempted`` counts GENERATIONS rather than players
    (one seat can book three), so the flag-off arm is what makes
    ``persona.attempted == AI seats`` a crisp assertion. The generations-not-
    players reading itself is pinned in ``tests/test_substitution_counts.py``.
    """

    def _run(
        *,
        citizens: int,
        mafia: int,
        diary_mode: str = "real",
        games: int = 1,
    ) -> tuple[EvalResult, dict[str, Any], _DiaryScript]:
        monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
        monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
        monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", str(citizens))
        monkeypatch.setenv("GRAPHIA_NUM_MAFIA", str(mafia))
        monkeypatch.setenv("GRAPHIA_PERSONA_DIVERSITY", "0")
        monkeypatch.setattr(
            blunder_eval,
            "collect_code_provenance",
            lambda root: {"commit": None, "branch": None, "dirty": False},
        )

        from graphia.config import load_config
        from graphia.llm import Persona, Reflection

        fake_small(_AI_NAMES)
        seats = citizens + mafia - 1
        fake = fake_large(
            personas=[
                Persona(
                    personality=f"trait-{i}",
                    manner=f"manner-{i}",
                    public_backstory=f"the {i}th trade of this town",
                )
                for i in range(seats)
            ],
            reflections=[Reflection(thought="Someone here is lying to me.")],
        )
        diary = _DiaryScript(diary_mode)
        monkeypatch.setattr(blunder_eval, "_play_one_game", _make_play(fake, diary))

        args = argparse.Namespace(
            provider="bedrock",
            games=games,
            seed=None,
            note="spec 041 slice 7 offline driven game",
            # A ``Path``, NOT a ``str``: ``append_record`` reads ``.parent`` off
            # it, so a string dies with ``AttributeError`` (Task 7.1 hit this).
            ledger_path=ledger,
            transcripts_root=tmp_path / "driven-transcripts",
        )
        result = run_eval(
            load_config(), args, transcripts_root=tmp_path / "driven-transcripts",
            run_id="20260907T000000",
        )
        record = load_ledger(ledger)[-1]
        return result, record, diary

    return _run


def test_a_driven_game_that_never_reaches_a_day_omits_four_kinds(
    driven: Callable[..., tuple[EvalResult, dict[str, Any], _DiaryScript]],
) -> None:
    """The natural absences, driven: no Day ⇒ no ballot, reflection, day_action, diary.

    A three-seat game (two Citizens, one Mafioso, the human pinned law-abiding)
    where the Mafioso kills the one law-abiding AI on Night 1: the Mafia reach
    parity and the game ends at ``check_win_night``, so no Day ever opens. The
    kinds with an opportunity — ``roster``, ``persona``, ``pointing`` — carry a
    denominator and a clean ``0.0``; the four with none are absent from the
    record entirely.

    Driven rather than synthesised on purpose (Task 7.1's note (c)): a hand-built
    ``EvalResult`` can only show that the renderer omits what it was not given.
    """
    result, record, _ = driven(citizens=2, mafia=1)

    assert result.games_completed == 1
    census = record["quality"]["substitutions"]

    assert set(census) == {"roster", "persona", "pointing"}
    for kind in ("ballot", "reflection", "day_action", "diary"):
        assert kind not in census, (
            f"{kind} had no opportunity in a game that never reached a Day and "
            "must be ABSENT, not a zero"
        )
    assert census["roster"] == {"rate": 0.0, "count": 0, "attempted": 1}
    # Two AI seats, diversity off ⇒ exactly one generation per seat.
    assert census["persona"]["attempted"] == 2
    # One Mafioso, one Night, one consensus round ⇒ one Pointing answer asked
    # for. Pinned exactly rather than ``>= 1``: the count is what tells a
    # single-round Night from a re-poll, and a fan-out counted per Night rather
    # than per pick would read 1 here whatever the game did.
    assert census["pointing"]["attempted"] == 1
    for kind, facets in census.items():
        assert facets["count"] == 0, f"{kind} substituted in a fully scripted game"
        assert facets["rate"] == 0.0

    # And the drill-down shows exactly the three, in schema order.
    assert _rendered_kinds(record) == ["roster", "persona", "pointing"]
    detail = render_detail(record)
    assert "    ballot:" not in detail
    assert "    reflection:" not in detail
    # The transport label travelled the whole way, unstubbed.
    assert record["provider"]["structured_output"] == "tool_use"
    assert "  structured_output: tool_use" in detail


def test_a_driven_game_that_completes_a_day_still_omits_ballot(
    driven: Callable[..., tuple[EvalResult, dict[str, Any], _DiaryScript]],
) -> None:
    """A Day's worth of speech and reflection, and no vote ever called.

    The complement of the test above, and the sharper half of "absent, not
    zero": here ``day_action``, ``reflection`` and ``diary`` DO carry
    denominators (so their absence above was structural, not a bug in the
    counting), while ``ballot`` stays absent because every AI only speaks and
    the human passes — nobody opens a vote, so no ballot is ever asked for.
    """
    _, record, _ = driven(citizens=3, mafia=1)

    census = record["quality"]["substitutions"]

    assert "ballot" not in census, (
        "no vote was ever opened, so no Ballot answer was asked of the model — "
        "ballot must be absent rather than reading 0.0"
    )
    for kind in ("roster", "persona", "pointing", "day_action", "reflection", "diary"):
        assert census[kind]["attempted"] >= 1, f"{kind} booked no attempt"
    # Structural, so pinned exactly: three AI seats (diversity off ⇒ one
    # generation each), two Nights of pointing, and one Day→Night hinge at which
    # the two SURVIVING AI players each write an entry.
    assert census["persona"]["attempted"] == 3
    assert census["pointing"]["attempted"] == 2
    assert census["diary"]["attempted"] == 2
    assert _rendered_kinds(record) == [
        "roster",
        "persona",
        "pointing",
        "day_action",
        "reflection",
        "diary",
    ]


def test_the_counter_and_the_sentinel_agree_on_a_driven_mixed_game(
    driven: Callable[..., tuple[EvalResult, dict[str, Any], _DiaryScript]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two instruments, one channel, one driven game of scripted mixed answers.

    The diary script answers with REAL text on odd calls and raises on even
    ones, so the game genuinely mixes model answers with substitutions. The sink
    counts the substitutions at the source; ``score_diary_fallback`` counts
    placeholder text in the final ``private_diaries``. Both must land on the
    script's own tally, and the harness must print NO disagreement warning.

    **The diary text is deliberately not the fallback sentence.** Scripting
    ``_DIARY_FALLBACK`` as the model's answer makes these two figures diverge
    with no bug present — the sentinel matches text the model itself wrote —
    which is exactly what the companion test below asserts instead.
    """
    result, record, diary = driven(citizens=3, mafia=1, diary_mode="alternate")

    assert diary.calls >= 2, "the driven game wrote too few entries to mix"
    assert diary.raised >= 1, "the script never forced a substitution"

    census_diary = record["quality"]["substitutions"]["diary"]
    assert census_diary["attempted"] == diary.calls
    assert census_diary["count"] == diary.raised
    assert census_diary["rate"] == diary.raised / diary.calls

    # The independent instrument, over the same run's channel.
    assert result.diary_fallback == {"count": diary.raised, "denominator": diary.calls}
    assert record["quality"]["diary_entries_attempted"] == diary.calls
    assert record["quality"]["diary_fallback_entries"] == diary.raised
    assert record["quality"]["diary_fallback_rate"] == census_diary["rate"]

    assert "DISAGREE" not in capsys.readouterr().err


def test_the_placeholder_answer_makes_the_two_instruments_diverge(
    driven: Callable[..., tuple[EvalResult, dict[str, Any], _DiaryScript]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cross-check WARNING fires, and both figures are recorded unreconciled.

    The honest divergence Task 7.1 demonstrated deliberately: the model answers
    with ``_DIARY_FALLBACK``'s exact sentence, so nothing substituted (sink 0/N)
    while the placeholder-text sentinel matches every entry (N/N). Neither
    instrument is broken and neither figure is adjusted — the harness names both
    routes on stderr and records both, because two instruments that CAN disagree
    is the point of having two.
    """
    result, record, diary = driven(citizens=3, mafia=1, diary_mode="placeholder")

    assert diary.calls >= 1
    assert diary.raised == 0, "the model answered every time; nothing substituted"

    census_diary = record["quality"]["substitutions"]["diary"]
    assert census_diary == {"rate": 0.0, "count": 0, "attempted": diary.calls}
    assert result.diary_fallback == {"count": diary.calls, "denominator": diary.calls}
    assert record["quality"]["diary_fallback_rate"] == 1.0

    err = capsys.readouterr().err
    assert "DISAGREE" in err
    assert "the substitution sink counted" in err
    assert "score_diary_fallback counted" in err


def test_the_sink_is_uninstalled_after_the_run(
    driven: Callable[..., tuple[EvalResult, dict[str, Any], _DiaryScript]],
) -> None:
    """``run_eval`` owns per-game isolation and hands the process back clean.

    The sink reference is process-global by design, so the harness installs one
    per game and resets it in a ``finally``. After the run, production's
    ``default_sink`` is back — otherwise a measured run would leave the game's
    instrumentation pointed at a dead counter.
    """
    driven(citizens=2, mafia=1)

    assert substitution._sink is default_sink


# ==========================================================================
# 6. The accumulating counter itself
# ==========================================================================


def test_the_accumulating_counter_keeps_productions_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The harness sink CHAINS to ``default_sink`` — counting does not silence.

    ``default_sink`` is public precisely so a counting sink can keep the log
    line; a sink that only counted would silently take production's
    substitution warning away for the whole measured run, which is the run that
    most wants the diagnostics.

    ``caplog`` is filtered by logger NAME, per Task 6.3's trap (d): every
    substitution now emits a ``graphia.substitution`` warning, so an unfiltered
    record list would be noisy in a confusing way.
    """
    counter = _SubstitutionCounter()

    with caplog.at_level(logging.WARNING, logger="graphia.substitution"):
        counter(SubstitutionEvent.ATTEMPT, SubstitutionKind.DIARY)
        counter(SubstitutionEvent.SUBSTITUTION, SubstitutionKind.DIARY)

    assert counter.attempted["diary"] == 1
    assert counter.substituted["diary"] == 1
    records = [r for r in caplog.records if r.name == "graphia.substitution"]
    assert len(records) == 1, "the attempt must be silent, the substitution logged"
    assert "diary" in records[0].getMessage()


# ==========================================================================
# 7. The committed ledger: read-only, and the write seam that once failed
# ==========================================================================


def test_the_committed_ledgers_substitution_blocks_are_well_formed() -> None:
    """A read-only sweep over ``evals/blunder-ledger.yaml`` — the one real read.

    Two jobs. It renders every committed record through the new reader code, so
    a drill-down that crashed or a cell that raised on a historical shape fails
    here. And it holds every ``quality.substitutions`` block that IS present to
    the writer's contract — all three facets, a positive denominator, and a rate
    that reproduces its own operands — which is what would catch a synthetic
    record leaking in from a test (the ~25-record accident, and Task 6.5's
    ``--games 0`` empty record, both reached this file by accident).

    Deliberately NOT an assertion that zero records carry the block: a real
    measured run will legitimately add one, and a test that goes red on the next
    honest eval is a test nobody keeps.
    """
    records = load_ledger(_COMMITTED_LEDGER)
    assert records, "the committed ledger should not be empty"

    for index, record in enumerate(records):
        detail = render_detail(record)
        assert detail
        # Every cell tolerates the historical shapes.
        assert _worst_substitution_cell(record)
        assert _transport_cell(record)

        census = (record.get("quality") or {}).get("substitutions")
        if census is None:
            assert _substitution_lines(record) == []
            continue
        assert isinstance(census, dict) and census, f"record {index}: empty census"
        for kind, facets in census.items():
            assert set(facets) == set(_SUBSTITUTION_FACET_FIELDS), (
                f"record {index}: {kind} does not carry the three facets"
            )
            assert facets["attempted"] > 0, (
                f"record {index}: {kind} has a zero denominator — an unattempted "
                "kind must be absent, not written"
            )
            assert facets["rate"] == facets["count"] / facets["attempted"], (
                f"record {index}: {kind}'s rate does not reproduce its operands"
            )


def test_append_record_resolves_the_module_global_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ledger: Path
) -> None:
    """The seam that once wrote ~25 synthetic records into the committed ledger.

    ``ledger_path`` defaults to ``None`` and is resolved to
    ``blunder_eval.LEDGER_PATH`` INSIDE the function. Bound as a signature
    default instead, the module global would be captured at import time and a
    test's ``monkeypatch.setattr`` would land on a name nothing reads — which is
    precisely how the accident happened. Both halves are asserted: the signature
    default is ``None``, and a monkeypatched global is honoured.
    """
    default = inspect.signature(append_record).parameters["ledger_path"].default
    assert default is None, (
        "ledger_path must stay a None default resolved at call time; an "
        "early-bound Path here makes every test's monkeypatch a no-op"
    )

    redirected = tmp_path / "redirected" / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", redirected)

    written = append_record(_result(_full_census()), "2026-09-07")

    assert written == redirected
    assert redirected.exists()
    assert "  substitutions:" in redirected.read_text(encoding="utf-8")


def test_the_injected_ledger_path_must_be_a_Path_not_a_str(
    tmp_path: Path, ledger: Path
) -> None:
    """A thin ``Namespace`` carrying a string ledger path dies in ``append_record``.

    Pinned because Task 7.1 hit it and the next author will too: ``append_record``
    reads ``.parent`` off the value to create the directory, so a ``str`` raises
    ``AttributeError: 'str' object has no attribute 'parent'`` — a crash AFTER
    the games have been played, at the worst possible moment in a real run. If
    the function is ever taught to accept a string, this test should be inverted
    rather than deleted.
    """
    with pytest.raises(AttributeError, match="parent"):
        append_record(_result({}), "2026-09-07", ledger_path=str(tmp_path / "s.yaml"))

    # And the Path form works — the positive control, without which the raise
    # above could be any unrelated failure.
    written = append_record(
        _result({}), "2026-09-07", ledger_path=tmp_path / "ok.yaml"
    )
    assert written.exists()


# ==========================================================================
# 8. The GLOBAL sink reset in tests/conftest.py — its own proof
#
# This module installs accumulating sinks (``run_eval`` does per game, and the
# tests below do directly) and deliberately has NO module-local reset fixture,
# so everything here rests on ``conftest.reset_substitution_sink``. Task 6.3
# measured that this failure is silent: removing its module-local equivalent
# left 28 tests green and turned exactly 2 red, one of which had been
# unknowingly exercising a previous test's sink instead of production's
# ``default_sink``. A global fixture is what closes the by-omission case a
# per-module fixture cannot — the same argument Slice 5 made for ``safe_llm``'s
# provider-level backstop behind its five call-site bindings.
# ==========================================================================

# The leak vehicle: a MODULE-LEVEL accumulator that outlives any one test.
_LEAKED_EVENTS: list[tuple[SubstitutionEvent, SubstitutionKind]] = []


def _leaky_sink(event: SubstitutionEvent, kind: SubstitutionKind) -> None:
    _LEAKED_EVENTS.append((event, kind))


def test_the_global_sink_reset_is_autouse_and_restores_the_default() -> None:
    """Structural + functional: it applies to EVERY test, and it resets to production.

    ``autouse=True`` is the whole mechanism — an opt-in fixture protects only
    the modules whose author remembered it, which is the failure mode this
    exists to remove — so it is asserted on the fixture object itself rather
    than inferred. Then the body is exercised directly with a leaked sink
    installed, on both sides of the ``yield``: the reset runs BEFORE a test (so
    a leak from elsewhere cannot reach it) and AFTER (so it cannot leak
    outward), and it restores ``default_sink`` rather than a silent no-op —
    which is what keeps production's own sink under test at all.
    """
    fixture = suite_conftest.reset_substitution_sink
    marker = fixture._fixture_function_marker  # type: ignore[attr-defined]
    assert marker.autouse is True, (
        "reset_substitution_sink must stay AUTOUSE — an opt-in reset protects "
        "only the modules whose author remembered to request it, which is the "
        "by-omission leak this fixture exists to close"
    )
    assert marker.scope == "function"

    set_sink(_leaky_sink)
    generator = fixture._get_wrapped_function()()  # type: ignore[attr-defined]
    next(generator)
    assert substitution._sink is default_sink, "the reset must run BEFORE the test"

    set_sink(_leaky_sink)
    with pytest.raises(StopIteration):
        next(generator)
    assert substitution._sink is default_sink, "the reset must run AFTER the test too"


def test_leak_probe_a_installs_a_sink_this_module_never_uninstalls() -> None:
    """Half one of the proof: install an accumulating sink and simply return.

    Well-behaved in every respect except that it never calls ``reset_sink`` —
    the realistic mistake, and the one a module with no local fixture cannot
    correct for itself. Correctness is entirely the conftest fixture's
    responsibility from here; probe B checks it discharged it.
    """
    _LEAKED_EVENTS.clear()
    set_sink(_leaky_sink)

    counter = _SubstitutionCounter()
    substitution.note_attempt(SubstitutionKind.DIARY)
    substitution.note_substitution(SubstitutionKind.DIARY)
    # The sink really is installed and really is catching events — otherwise
    # probe B's silence would prove nothing.
    assert _LEAKED_EVENTS == [
        (SubstitutionEvent.ATTEMPT, SubstitutionKind.DIARY),
        (SubstitutionEvent.SUBSTITUTION, SubstitutionKind.DIARY),
    ]
    assert counter.attempted == {}


def test_leak_probe_b_runs_on_the_default_sink_because_conftest_reset_it() -> None:
    """Half two: notify with NO sink installed and prove probe A's sink is gone.

    Reading ``before`` rather than asserting an empty list keeps this correct
    when run alone (``-k probe_b``), where there is no probe A to leak from.
    Measured by removing ``autouse=True`` from
    ``conftest.reset_substitution_sink``: this test fails, reporting probe A's
    sink still catching probe B's events, while probe A stays green — the
    signature of a leak, and the reason a missing reset is so easy to ship.
    """
    before = list(_LEAKED_EVENTS)

    assert substitution._sink is default_sink, (
        "a sink installed by an earlier test is still installed — the global "
        "conftest reset did not run between the two"
    )
    substitution.note_substitution(SubstitutionKind.DIARY)

    assert _LEAKED_EVENTS == before, (
        "an event booked by this test reached the sink test_leak_probe_a_… "
        "installed, so every test in this module is order-dependent"
    )
