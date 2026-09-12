"""Spec 043, Task 5.3 — what the VIEWER says about a run that was stopped part-way.

Slice 4 put ``quality.ended_early`` on the record. This module is the reader's
half: the ``Games`` table cell that turns ``10`` into ``1/3`` when a run did not
finish what it asked for (:func:`~graphia.eval_ledger._games_cell`), and the
drill-down line that shows the marker at all
(:func:`~graphia.eval_ledger._render_quality_section`). Both are **pure
functions over hand-built records** — no Textual, no LLM, no network — which is
why they live beside ``tests/test_ledger_model.py``'s data-layer tests rather
than in the pilot-driven ``tests/test_ledger_viewer.py``.

WHY THIS REPAIR IS IN SCOPE AT ALL, since the functional spec defers presenting
partial runs. ``settings.games`` is the number **requested** and must stay that
way — it is what repeats a run like-for-like, and rewriting it to the completed
count would collapse a 4-of-10 record onto a planned 4-game run. So from spec
043 onward the number this column has always shown is no longer necessarily the
number the row's metric cells were measured over. That cell is correct on all 50
records committed before this spec; **this spec is what makes it wrong**, and
the spec that introduces an ambiguity into an existing display owes the fix
(technical-considerations 043 §2.6). Section 5 asserts the "nothing else moved"
half over the real committed corpus.

THE TWO THINGS THIS FILE EXISTS TO PIN, neither of which §2.6 states:

**1. A record carrying the marker but not a usable PAIR falls back to today's
cell.** ``games_completed`` missing, ``null``, or no requested count all render
the plain cell rather than a half-pair, because ``1/`` and ``/3`` read worse than
the unmarked number. The invariant, asserted directly in section 2, is that the
cell is **today's cell, or today's cell with a ``"<completed>/"`` prefix** —
nothing this function does can remove or alter information already on screen; it
can only add the numerator. A genuine ``games_completed: 0`` survives as
``0/10``, because the emptiness tests are on the rendered **text**, where ``"0"``
is truthy. That is a decision, not an accident.

**2. One key, two gates, deliberately.** The table cell is gated on the marker's
**truthiness**; the drill-down on its **presence**. A summary cell loses nothing
by treating a hand-edited ``ended_early: false`` as absent — both mean "show the
requested count". A field dump is the one view whose job is to say which bytes it
found, so a truthiness gate there would hide a hand-edited ``false`` in the only
place a reader looks instead of opening raw YAML — reintroducing through the gate
the very whitelist gap Task 5.2 closed. The visible divergence is asserted in
section 4, **both halves in one test**: the cell shows the plain requested count
while the drill-down shows ``ended_early: False``. No *written* record can
produce that state — the writer emits the key only when true — so the test is
what puts the pair on record as intended rather than accidental.

THE ``Games`` CELL AND THE SCOPED ``games`` SEARCH ARE TWO FUNCTIONS ON PURPOSE,
and section 3 is the test that defends it. :func:`~graphia.eval_ledger._resolved_games`
has two callers — the cell and the scoped-search ``games`` field — and they want
different things: a search for ``games`` ``10`` must keep finding a run that
*asked for* ten whether or not it finished them, which is exactly the run a
reviewer hunting the ten-game baselines needs to see. Teaching the resolver about
the marker would render an identical-looking table while silently changing what
that search matches, so the assertion there is on the field's text **exactly**,
not on a substring match — ``"10"`` is a substring of ``"4/10"`` and a match-only
assertion would pass under the mutation it exists to catch.

DEVELOPED AGAINST A REAL KILLED RUN. Task 4.4's genuine ``kill -9`` of a
three-game Nova run left a one-game record carrying ``ended_early: true`` with
``settings.games: 3``; run through this module's code it renders ``1/3`` in the
``Games`` cell and ``ended_early: True`` as the first ``quality`` line, which is
what the hand-built documents below reproduce. That file lives outside the repo
and is deliberately **not** depended on here — section 6 gets the same guarantee
from the writer itself, in-process, by handing ``render_record`` an
``ended_early`` result and asserting the reader finds the pair in the bytes the
writer actually produced.

WHAT IS DELIBERATELY NOT HERE. ``run.id`` / ``run.seq`` / ``run.transcript_dir``
are **not rendered anywhere in the viewer** and this slice does not add them:
that is a missing nicety rather than a contradiction (nothing shown becomes
false), it is logged as CONSIDERED AND EXCLUDED above Slice 5 and routed to the
backlog by Task 6.3, and asserting it here would be testing a decision the slice
did not make. The ``⚠`` indicator stays untouched — it means ``code.dirty``, and
overloading one indicator with two unrelated caveats would leave neither
readable. The writer's side of the marker (gating, key order, the
``games_attempted == games_completed + games_failed_early`` balance) is
``tests/test_early_stop_marker.py``'s; the write cadence is
``tests/test_progressive_ledger_write.py``'s.

THE COMMITTED LEDGER IS NEVER WRITTEN. Every fixture is a hand-built document
under ``tmp_path``; the one round-trip test redirects ``blunder_eval.LEDGER_PATH``
and ``blunder_eval.TRANSCRIPTS_ROOT`` into ``tmp_path`` **and** passes an explicit
path; section 5 opens ``evals/blunder-ledger.yaml`` read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphia.eval_ledger import (
    _EARLY_STOP_QUALITY_GATE,
    SEARCH_FIELDS,
    _games_cell,
    _resolved_games,
    _text,
    build_table_model,
    load_ledger,
    render_detail,
    row_matches_field,
)
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import EvalResult, append_record

# ==========================================================================
# Fixtures: documents in the shape a real run writes
#
# Modelled on Task 4.4's killed run (one game of a requested three, the marker
# first in ``quality``), with the requested count and the marker parameterised so
# a partial and a planned record can be driven side by side.
# ==========================================================================


def _doc(*, requested: int | str = 10, quality: str = "") -> str:
    """A spec-043-era game record; ``quality`` supplies the block's own lines.

    With ``quality=""`` this is the shape of every record committed before this
    spec — the three counts and then straight to ``duration_seconds`` — which is
    what makes "an unmarked record renders exactly what it renders today" a claim
    about a realistic document rather than about an empty block.
    """
    return (
        "run:\n"
        "  date: '2026-09-12'\n"
        "  id: '2026-09-12T14-35-53-b23bb1'\n"
        "  seq: 1\n"
        "  duration_seconds: 172.575\n"
        "  metrics_version: 1\n"
        "code:\n"
        "  commit: '82b65615a4ff030fbb618ef1f6780c91cd0a1044'\n"
        "  branch: 'main'\n"
        "  dirty: false\n"
        "provider:\n"
        "  name: 'bedrock'\n"
        "  large_model: 'amazon.nova-pro-v1:0'\n"
        "  small_model: 'amazon.nova-lite-v1:0'\n"
        "settings:\n"
        "  large_model: 'amazon.nova-pro-v1:0'\n"
        "  small_model: 'amazon.nova-lite-v1:0'\n"
        f"  games: {requested}\n"
        "  seed: null\n"
        "  lineup:\n"
        "    num_citizens: 6\n"
        "    num_mafia: 2\n"
        "quality:\n"
        f"{quality}"
        "  duration_seconds: 172.575\n"
        "metrics:\n"
        "  repetition:\n"
        "    rate: 0.5\n"
        "    count: 10\n"
        "    denominator: 20\n"
        "notes: ''\n"
    )


def _counts(*, attempted: int, completed: int, failed: int = 0) -> str:
    """The three unconditional ``quality`` counts, in the writer's order."""
    return (
        f"  games_attempted: {attempted}\n"
        f"  games_completed: {completed}\n"
        f"  games_failed_early: {failed}\n"
    )


# A 4-of-10 record: the marker FIRST, exactly where ``render_record`` puts it.
_PARTIAL_QUALITY = "  ended_early: true\n" + _counts(attempted=4, completed=4)

# The same run had it finished: no marker at all, never a ``false``.
_COMPLETE_QUALITY = _counts(attempted=10, completed=10)


def _one(tmp_path: Path, doc: str) -> dict[str, Any]:
    """Load the single record from a one-document temp ledger.

    The temp-ledger convention every ledger-viewer test module uses: never the
    committed file.
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


def _games_column(record: dict[str, Any]) -> str:
    """The ``Games`` cell for one record, through the real table model.

    Resolved by column NAME, never by index — the fixed head has gained five
    columns since this one was added and the next spec may add another.
    """
    model = build_table_model([record])
    (row,) = model.rows
    return row[model.columns.index("Games")]


# ==========================================================================
# 1. The pair, and the bare count — the two cells §2.6 names
# ==========================================================================


def test_a_marked_record_renders_completed_over_requested(tmp_path: Path) -> None:
    """A run stopped after four of ten reads ``4/10`` — finished, then asked for.

    The order is the whole cell: ``10/4`` would read as a ten-game measurement
    qualified by something, which is the misreading the pair exists to prevent.
    Asserted through :func:`build_table_model` as well as the cell function, so
    the row a reviewer actually sees is what is pinned.
    """
    record = _one(tmp_path, _doc(requested=10, quality=_PARTIAL_QUALITY))

    assert _games_cell(record) == "4/10"
    assert _games_column(record) == "4/10"


def test_an_unmarked_record_renders_the_bare_requested_count(tmp_path: Path) -> None:
    """A run that finished what it asked for reads ``10`` — today's cell, unchanged.

    Every record committed before this spec is this case, and so is every run
    that completes: the marker is written only when a run stopped short, so the
    pair must never appear on a record that did not claim it.
    """
    record = _one(tmp_path, _doc(requested=10, quality=_COMPLETE_QUALITY))

    assert _games_cell(record) == "10"
    assert _games_column(record) == "10"
    assert "/" not in _games_column(record)


def test_the_killed_runs_shape_reads_one_of_three(tmp_path: Path) -> None:
    """Task 4.4's real artifact, reproduced: one game of a requested three.

    The genuine ``kill -9`` record renders ``1/3``. Kept as its own case rather
    than folded into the 4-of-10 one because it is the shape that was actually
    witnessed on disk, and because ``1`` and ``3`` share no digits with ``10``
    or ``4`` — a cell built from the wrong operands cannot coincide here.
    """
    record = _one(
        tmp_path,
        _doc(
            requested=3,
            quality="  ended_early: true\n" + _counts(attempted=1, completed=1),
        ),
    )

    assert _games_cell(record) == "1/3"


# ==========================================================================
# 2. Half a pair is not a cell — the fallback §2.6 is silent about
# ==========================================================================


@pytest.mark.parametrize(
    ("record", "shape"),
    [
        pytest.param(
            {"settings": {"games": 10}, "quality": {"ended_early": True}},
            "marked, but no games_completed key",
            id="completed-missing",
        ),
        pytest.param(
            {
                "settings": {"games": 10},
                "quality": {"ended_early": True, "games_completed": None},
            },
            "marked, games_completed is a YAML null",
            id="completed-null",
        ),
        pytest.param(
            {"settings": {}, "quality": {"ended_early": True, "games_completed": 4}},
            "marked and counted, but nothing was requested",
            id="requested-missing",
        ),
        pytest.param(
            {"quality": {"ended_early": True, "games_completed": 4}},
            "marked and counted, no settings block at all",
            id="settings-missing",
        ),
    ],
)
def test_an_unusable_pair_falls_back_to_todays_cell(
    record: dict[str, Any], shape: str
) -> None:
    """A marker without both numbers renders the plain cell, never a half-pair.

    ``1/`` and ``/3`` read worse than the unmarked number: the pair is a claim
    about **two** values, and half a claim is not a smaller claim but a confusing
    one. §2.6 does not cover this — it says ``4/10`` when the marker is present
    and ``10`` otherwise — so the resolution is recorded here as behaviour.

    The expectation is derived from :func:`_resolved_games` rather than typed as
    a literal, because the claim IS "what this column shows today", and typing
    ``"10"`` would restate the fixture instead of the rule. The separator check
    beside it is what keeps that from being self-referential: it holds whatever
    the resolver returns, so the pair can never appear here even if the two ever
    get collapsed into one function.
    """
    cell = _games_cell(record)

    assert "/" not in cell, shape
    assert cell == _text(_resolved_games(record)), shape


def test_a_genuine_zero_completed_count_survives_as_a_pair() -> None:
    """``games_completed: 0`` renders ``0/10`` — a real reading, not an absence.

    The emptiness tests that drive the fallback above are on the rendered
    **text**, where ``"0"`` is truthy, precisely so a run stopped before its
    first game finished (or one whose every completed game is yet to land) is
    reported as the zero it is. Testing the raw value for truthiness instead
    would silently reclassify this record as the "no usable pair" case and show
    ``10``, asserting a ten-game measurement that does not exist.
    """
    record = {
        "settings": {"games": 10},
        "quality": {"ended_early": True, "games_completed": 0},
    }

    assert _games_cell(record) == "0/10"


def test_both_halves_render_verbatim_with_no_numeric_coercion() -> None:
    """A non-integer count renders as itself rather than raising or vanishing.

    The requested half has always rendered verbatim (a hand-written
    ``settings.games: 'ten'`` has always read ``ten``), so coercing the completed
    half while the other stays verbatim would be the inconsistency. Neither is
    parsed as a number, which is also why nothing here can raise on a malformed
    record.
    """
    record = {
        "settings": {"games": "ten"},
        "quality": {"ended_early": True, "games_completed": "four"},
    }

    assert _games_cell(record) == "four/ten"


@pytest.mark.parametrize(
    ("record", "shape"),
    [
        pytest.param({}, "an empty record", id="empty"),
        pytest.param({"quality": {"ended_early": True}}, "marker only", id="marker-only"),
        pytest.param(
            {"quality": None, "settings": {"games": 10}},
            "a null quality block",
            id="quality-null",
        ),
        pytest.param(
            {"quality": "stopped", "settings": {"games": 10}},
            "a quality block that is not a mapping",
            id="quality-scalar",
        ),
        pytest.param(
            {"run": {"games": 3}, "quality": {"ended_early": True, "games_completed": 1}},
            "a pre-provenance record, count under run.games",
            id="pre-provenance",
        ),
        pytest.param(
            {
                "settings": {"games": 10},
                "quality": {"ended_early": "yes", "games_completed": 4},
            },
            "a hand-written truthy marker that is not a bool",
            id="marker-string",
        ),
        pytest.param(
            {
                "settings": {"games": 10},
                "quality": {"ended_early": True, "games_completed": []},
            },
            "a structured value where a count belongs",
            id="completed-list",
        ),
    ],
)
def test_the_cell_is_todays_cell_or_todays_cell_with_a_prefix(
    record: dict[str, Any], shape: str
) -> None:
    """THE INVARIANT: this function can only ADD a numerator, never alter the cell.

    Stated as a property over awkward shapes rather than as a table of expected
    strings, because the property is what makes the change safe to ship over a
    heterogeneous fifty-record corpus: whatever the record, the cell is either
    exactly what this column renders today or that same text behind a
    ``"<completed>/"`` prefix. Nothing is removed, reordered or reformatted.

    The prefix arm demands a **non-empty** numerator and a **non-empty** requested
    count, which is what makes this one assertion also reject both half-pairs and
    a reversed ``requested/completed`` rendering — ``"10/4"`` does not end with
    ``"/10"``.
    """
    today = _text(_resolved_games(record))
    cell = _games_cell(record)

    prefixed = (
        today != ""
        and cell.endswith(f"/{today}")
        and cell[: -(len(today) + 1)] != ""
    )
    assert cell == today or prefixed, f"{shape}: {cell!r} against today's {today!r}"


# ==========================================================================
# 3. The scoped ``games`` search is NOT the cell — the two-function design
# ==========================================================================


def test_the_scoped_games_search_still_finds_a_marked_run_by_what_it_asked_for(
    tmp_path: Path,
) -> None:
    """A 4-of-10 run is still found by ``games`` ``10``, and is NOT found by ``4``.

    THE TEST THE WHOLE TWO-FUNCTION DESIGN EXISTS FOR. Teaching
    :func:`_resolved_games` about the marker — rather than adding a second
    function beside it — renders an identical table while silently changing what
    the scoped search matches, because that resolver is also the search field.
    A reviewer hunting the ten-game baselines needs the partial run in the
    results; it is arguably the one they most need to be shown.

    Asserted on the field's text **exactly**, and with the negative beside it,
    because a match-only assertion would survive the mutation: ``"10"`` is a
    substring of ``"4/10"``, so ``row_matches_field("games", "10", ...)`` stays
    ``True`` under it. ``"4"`` is the term that tells the two apart.
    """
    record = _one(tmp_path, _doc(requested=10, quality=_PARTIAL_QUALITY))
    model = build_table_model([record])
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert "games" in SEARCH_FIELDS
    # The field carries the number REQUESTED, on its own — not the cell's pair.
    assert fields["games"] == "10"
    assert _resolved_games(record) == 10
    assert row_matches_field("games", "10", blob, fields) is True
    # The completed half never entered the haystack.
    assert row_matches_field("games", "4", blob, fields) is False


def test_the_completed_count_is_not_searchable_as_free_text(tmp_path: Path) -> None:
    """The pair is display vocabulary, not a fact the blob carries.

    No game count of any kind is in the free-text blob (only the scoped field
    has one), so the cell's new numerator must not leak in through a row-cell
    join either. Cheap, and it pins the boundary the moment someone builds the
    blob from the rendered row.
    """
    record = _one(tmp_path, _doc(requested=10, quality=_PARTIAL_QUALITY))
    (blob,) = build_table_model([record]).search_blobs

    assert "4/10" not in blob


# ==========================================================================
# 4. The drill-down line, and the second gate
# ==========================================================================


def test_the_marker_renders_first_in_the_quality_block(tmp_path: Path) -> None:
    """``ended_early: True`` leads the block, ahead of the three counts.

    Read first because it changes how everything beneath it reads: the counts
    below are what the run FINISHED and ``settings.games`` is what it ASKED FOR.
    Appending it after the counts would render the same characters in an order
    that defeats its only purpose, so the position is asserted, not just the
    presence.
    """
    record = _one(tmp_path, _doc(requested=10, quality=_PARTIAL_QUALITY))

    detail = render_detail(record)

    assert "  ended_early: True" in detail
    labels = _quality_labels(detail)
    assert labels[0] == "ended_early"
    assert (
        detail.index("  ended_early:")
        < detail.index("  games_attempted:")
        < detail.index("  games_completed:")
    )


def test_an_absent_marker_renders_no_line_at_all(tmp_path: Path) -> None:
    """A complete run's drill-down gains not one line — never ``ended_early: False``.

    Absence is the value and it is two-valued, not three: every record written
    before this spec came from the one code path that ran the loop to its end, so
    an absent marker means "attempted everything it asked for". A rendered
    ``False`` would assert a claim no writer ever made.

    Stated as "the marked render minus exactly the one marker line IS the
    unmarked render", which is stronger than a substring absence: it also catches
    the line landing in the wrong place, and it catches a gate keyed on a field
    every record carries (which would paint an ``ended_early: —`` line onto all
    fifty committed records).

    The two fixtures therefore differ in the marker line and **nothing else** —
    the counts are held constant so the diff is attributable. That makes the
    marked one a document the harness would never write (a run that attempted all
    ten claiming it stopped short); this test is about what one key adds to a
    render, and holding every other variable fixed is what makes the equality
    mean that.
    """
    complete = _one(tmp_path, _doc(requested=10, quality=_COMPLETE_QUALITY))
    partial = _one(
        tmp_path, _doc(requested=10, quality="  ended_early: true\n" + _COMPLETE_QUALITY)
    )

    complete_detail = render_detail(complete)
    partial_detail = render_detail(partial)

    assert "ended_early" not in complete_detail
    inserted = [
        line
        for line in partial_detail.splitlines()
        if line not in complete_detail.splitlines()
    ]
    assert inserted == ["  ended_early: True"]


def test_a_null_marker_renders_present_but_no_value(tmp_path: Path) -> None:
    """``ended_early: null`` reads ``ended_early: —`` — a key that IS there.

    The gate is spelled with the ``_MISSING`` sentinel rather than ``is not
    None`` for exactly this: a hand-edited null is a key the record carries, and
    the drill-down's job is to say what it found. Rewriting the gate as ``is not
    None`` makes this record indistinguishable from a complete run's, which is
    the presence-versus-truthiness distinction one step further along.
    """
    record = _one(
        tmp_path,
        _doc(requested=10, quality="  ended_early: null\n" + _counts(attempted=4, completed=4)),
    )

    detail = render_detail(record)

    assert "  ended_early: —" in detail
    assert _quality_labels(detail)[0] == "ended_early"


def test_the_gate_names_the_marker_key() -> None:
    """The drill-down gate is the writer's own key, spelled once.

    A constant no well-formed record can prove, asserted directly: the gate reads
    ``quality.ended_early``, the same dotted path the cell reads, so the two gates
    below differ in their TEST and not in what they look at.
    """
    assert _EARLY_STOP_QUALITY_GATE == "quality.ended_early"


def test_a_hand_edited_false_shows_the_plain_cell_and_the_false_line(
    tmp_path: Path,
) -> None:
    """ONE KEY, TWO GATES — the divergence asserted in a single test, on purpose.

    The cell is truthiness-gated and the drill-down presence-gated, so a
    hand-edited ``ended_early: false`` reads ``10`` in the table and
    ``ended_early: False`` in the drill-down. **No written record can produce
    that state** — the writer emits the key only when true — so nothing else in
    the suite can witness it, and without this test the divergence would read as
    a bug the next time somebody "harmonised" the two gates.

    Both halves are right for their own view. A summary cell loses nothing by
    collapsing absent and false: both mean "show the requested count". A field
    dump exists to say which bytes it found, and a truthiness gate there would
    make a hand-edited ``false`` invisible in the only place a reader looks
    instead of opening raw YAML — reintroducing the whitelist gap through the
    gate.
    """
    record = _one(
        tmp_path,
        _doc(
            requested=10,
            quality="  ended_early: false\n" + _counts(attempted=10, completed=10),
        ),
    )

    # The table: false means "this run did not end early" — the plain count.
    assert _games_cell(record) == "10"
    assert _games_column(record) == "10"
    # The drill-down: the key IS on the record, so it is shown to be.
    assert "  ended_early: False" in render_detail(record)


# ==========================================================================
# 5. The committed ledger, read-only — nothing else moved
# ==========================================================================

_COMMITTED_LEDGER = (
    Path(__file__).resolve().parents[1] / "evals" / "blunder-ledger.yaml"
)
_requires_ledger = pytest.mark.skipif(
    not _COMMITTED_LEDGER.is_file(),
    reason=f"no committed ledger at {_COMMITTED_LEDGER}",
)


@_requires_ledger
def test_every_committed_record_renders_the_pair_iff_it_carries_the_marker() -> None:
    """The conditional cell and line, proven over the real curated corpus.

    Task 5.4 opens ``make view-ledger`` over the committed ledger by hand to
    confirm all fifty existing rows render exactly as before; this is the
    executable half of that check, and it is the one that keeps holding after the
    slice is closed. Written as a biconditional rather than as today's census
    (zero marked records of fifty) so it stays true the first time a genuinely
    partial run is committed.

    The ledger is opened **read-only**. Nothing in this module writes to it.
    """
    records = load_ledger(_COMMITTED_LEDGER)

    assert records, "the committed ledger parsed to zero records"
    for index, record in enumerate(records):
        quality = record.get("quality")
        marked = bool(isinstance(quality, dict) and quality.get("ended_early"))
        cell = _games_cell(record)
        detail_line = "  ended_early:" in render_detail(record)
        assert ("/" in cell) is marked, f"record {index}: cell {cell!r}"
        if not marked:
            # Byte-for-byte what this column rendered before spec 043.
            assert cell == _text(_resolved_games(record)), f"record {index}"
        carries = isinstance(quality, dict) and "ended_early" in quality
        assert detail_line is carries, f"record {index}"


# ==========================================================================
# 6. The seam: the writer's own record, read back through the viewer
# ==========================================================================


def test_the_writers_own_partial_record_renders_the_pair_and_the_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Harness → file → ``load_ledger`` → cell + drill-down, with no hand-typed key.

    THE ANTI-VACUITY CONTROL FOR EVERY FIXTURE ABOVE. Those documents are typed
    by hand, so they agree with the reader by construction: a *coordinated*
    rename on both sides would leave them green while the real harness wrote a
    key the real viewer could not see. This test never spells ``ended_early`` in
    a document — it sets the flag on an ``EvalResult``, lets ``render_record``
    write whatever it writes, and asserts the reader found the pair and the line
    in those bytes. It is also the in-repo stand-in for Task 4.4's real killed
    run, which lives outside the tree and cannot be a fixture.

    Both the module-global ``LEDGER_PATH`` and ``TRANSCRIPTS_ROOT`` are
    redirected into ``tmp_path`` **as well as** an explicit ``ledger_path`` being
    passed: an early-bound signature default once leaked ~25 synthetic records
    into the committed ledger, and belt-and-braces is cheap.
    """
    monkeypatch.setattr(
        blunder_eval, "LEDGER_PATH", tmp_path / "fallback" / "ledger.yaml"
    )
    monkeypatch.setattr(
        blunder_eval, "TRANSCRIPTS_ROOT", tmp_path / "fallback-transcripts"
    )
    result = EvalResult(
        provider="bedrock",
        ended_early=True,
        games_attempted=1,
        games_completed=1,
        games_failed_early=0,
        settings={"games": 3},
        duration_seconds=172.575,
    )

    path = append_record(result, "2026-09-12", ledger_path=tmp_path / "run" / "l.yaml")
    (record,) = load_ledger(path)

    assert _games_cell(record) == "1/3"
    assert _games_column(record) == "1/3"
    assert _quality_labels(render_detail(record))[0] == "ended_early"
    assert "  ended_early: True" in render_detail(record)


def test_the_writers_complete_record_renders_neither(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same seam, unmarked: a finished run reads ``3`` and gains no line.

    The control half. Without it the test above would pass just as happily under
    a writer that stamped the marker on every record, and under a reader that
    rendered the pair unconditionally.
    """
    monkeypatch.setattr(
        blunder_eval, "LEDGER_PATH", tmp_path / "fallback" / "ledger.yaml"
    )
    monkeypatch.setattr(
        blunder_eval, "TRANSCRIPTS_ROOT", tmp_path / "fallback-transcripts"
    )
    result = EvalResult(
        provider="bedrock",
        games_attempted=3,
        games_completed=3,
        games_failed_early=0,
        settings={"games": 3},
        duration_seconds=500.0,
    )

    path = append_record(result, "2026-09-12", ledger_path=tmp_path / "run" / "l.yaml")
    (record,) = load_ledger(path)

    assert _games_cell(record) == "3"
    assert "ended_early" not in render_detail(record)
