"""Spec 040 (The Moderator's Closing Story), Task 9.4 — the ``quality.story_*`` trio.

WHAT THIS FILE PINS. Slice 9's whole claim is that **a run cannot silently
produce no stories at all**: a 20-game batch whose story call failed 18 times
must not append a record indistinguishable from a clean one. That claim is
carried by three flat keys under ``quality`` —
``story_missing_rate`` / ``story_missing_games`` / ``story_ending_games`` —
written by :func:`~graphia.tools.blunder_eval.render_record` from the
``{count, denominator}`` pair :func:`~graphia.tools.blunder_eval.score_story_missing`
derives per game and ``run_eval`` sums over the batch.

FOUR SEAMS, IN FOUR SECTIONS, and each one can break independently:

1. **The pure scorer**, over synthetic ``{node: delta}`` logs — is a game that
   never reached the ending excluded from *both* facets, is presence read off
   the message FLAG (never off "the delta had messages"), and is the flag name
   resolved through the shared constant?
2. **The record writer**, over dedicated hand-built ``EvalResult``s — is the
   gate the DENOMINATOR (not truthiness, not the numerator), are all three keys
   written together, and does a clean run's ``0.0`` actually reach the record?
3. **The fold-in**, through a real ``run_eval`` with ``_play_one_game`` stubbed
   to serve hand-built captures — does the batch total come from the capture,
   and does the trio stay OUT of the versioned ``metrics`` family?
4. **The real failure path**, driving the actual graph offline with
   ``stories=[RuntimeError(...)]`` — is the delta the scorer's guard has to
   survive really ``None``, and does the real ``end_screen`` delta really carry
   unflagged messages?

WHY SECTION 4 EXISTS AT ALL, i.e. why the synthetic logs are not enough. Two of
the scorer's assertions are claims about what LangGraph *actually streams*, not
about the scorer's own arithmetic: that a failed story super-step arrives as
``{"moderator_story": None}`` (a bare ``{}`` return streams as ``None``, not as
``{}``), and that ``end_screen``'s delta carries a non-empty ``messages`` list
with no story flag on it. A synthetic log asserting either one is only as good
as the hand that typed it, so the driven arm measures both against a genuine
capture and the synthetic fixtures inherit their realism from it. The driven arm
also happens to be the only place the ``__interrupt__`` tuple appears for free —
``eval_dialogue._drive`` pushes every stream payload to its sink unfiltered, so
every real event log holds one, and ``tuple.get`` raises exactly like
``None.get``.

WHAT THIS FILE DELIBERATELY DOES NOT TEST, recorded so the gaps read as choices:

* **The denominator's node choice.** ``_STORY_ENDING_NODE = "end_screen"`` could
  equally be ``"moderator_story"``: the two are consecutive super-steps of ONE
  ``_drive`` call joined by an unconditional edge, so in a real log they are
  always both present or both absent and no test SHOULD tell them apart. That
  is not free, though — a synthetic log CAN hold one without the other, so an
  early draft of this file distinguished them by accident (four red on a
  re-definition that is not a defect). :func:`_endgame_delta` therefore derives
  its key from the constant, and what remains pinned is the thing that matters:
  section 4 scores a REAL log, so a marker matching nothing a real game streams
  collapses the denominator to zero and turns both driven arms red. The rename
  guard proper lives in
  ``tests/test_spec040_story_topology.py::_assert_story_closes_the_game``, which
  pins the trajectory tail ``["end_screen", "moderator_story"]``.
* **``METRICS_VERSION`` staying at 1.** Already pinned by name in
  ``tests/test_substitution_ledger.py::test_METRICS_VERSION_is_still_one``,
  which is the ledger-wide guard every additive block leans on. Duplicating it
  here would add a second place to update and no coverage.
* **"The trio is never backfilled onto an older record."** Not source-mutatable
  at all: :func:`~graphia.tools.blunder_eval.append_record` only ever **adds** a
  document, so no code path can rewrite a committed record — the rule guards
  a future hand edit or tidy-up script, not today's code, and a test can only
  assert the append-only PROPERTY. (Until spec 043's Slice 1 — Task 1.3,
  2026-09-11 — that was true because the function opened the ledger ``"a"``-only.
  It now renders the document, reads the ledger as opaque text and replaces the
  whole file atomically, so the *mechanism* is gone; the property survives it,
  upheld by the function's own semantics instead. Earlier documents are carried
  across as the exact bytes that were read — never parsed, never re-rendered —
  and the function has no path that replaces or removes a chunk. Spec 043's
  in-place update of a *live* run's own record is a different function, which is
  the point of the distinction.) That property already has its witness in
  ``tests/test_blunder_eval_provenance.py``'s
  ``test_append_record_with_provenance_accumulates_two_docs`` (two appends, two
  ``---``-separated docs, the first one intact), and since spec 043 also in
  ``tests/test_ledger_write.py``'s
  ``test_append_record_onto_a_copy_of_the_committed_ledger_leaves_every_document_intact``
  (every pre-existing document byte-identical after an append). This file
  asserts the one
  adjacent thing it can: a result carrying NO pair renders no story keys at all,
  which is the shape every pre-040 record has — so nothing this writer does can
  put a trio onto a record that lacks a denominator.

NO WRITE EVER LEAVES ``tmp_path``. Section 3 calls the real ``run_eval``, which
really appends a record — so the ``ledger`` fixture redirects
``blunder_eval.LEDGER_PATH`` *and* ``blunder_eval.TRANSCRIPTS_ROOT`` at
``tmp_path`` as belt-and-braces on top of the ``args`` paths, exactly as
``tests/test_substitution_ledger.py`` does. The committed
``evals/blunder-ledger.yaml`` and ``evals/transcripts/`` are unreachable from
here even from a code path that ignored its arguments.

THE POSITIONAL TRAP, AVOIDED BY CONSTRUCTION.
``tests/test_blunder_eval_provenance.py``'s
``test_render_record_quality_block_carries_duration_and_counts`` reads
``lines[q_i + 4]`` positionally off ``_result_with_provenance``, so adding
story data to that shared helper would turn a correct-code test red. Every
result in this file is built HERE, for one test, and no shared fixture gains a
story pair.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from graphia import state as state_module
from graphia.llm import Persona, Story
from graphia.state import CLOSING_STORY_KWARG
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    _GameCapture,
    render_record,
    run_eval,
    score_story_missing,
)
from graphia.tools.eval_dialogue import _collect_interrupt, _drive

_HUMAN_NAME = "Alice"
_NAME_POOL = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav", "Noor", "Kai"]

_SCRIPTED_STORY = Story(
    text="The baker had been counting the wrong things all along."
)

# The three keys, as ONE tuple used by every assertion below, so a reword has a
# single place to land instead of drifting between tests.
_STORY_KEYS = ("story_missing_rate", "story_missing_games", "story_ending_games")


# ==========================================================================
# Shared scaffolding
# ==========================================================================


def _story_message(flag: str = CLOSING_STORY_KWARG) -> SystemMessage:
    """The Moderator's story message, shaped as ``moderator_story`` emits it.

    A ``SystemMessage`` with no ``name`` and ``additional_kwargs={flag: True}``
    — see ``nodes/endgame.moderator_story``'s docstring for why the type and the
    missing name are a ledger-corruption guard rather than a style choice. The
    ``flag`` parameter exists for the one test that reworks the constant.
    """
    return SystemMessage(
        content="Once the baker started counting…", additional_kwargs={flag: True}
    )


def _endgame_delta() -> dict[str, Any]:
    """The delta that makes a synthetic log count as "this game reached the ending".

    Two things are deliberate about it.

    **The KEY is read off the production marker, never typed.**
    ``blunder_eval._STORY_ENDING_NODE`` is ``"end_screen"`` today and could
    equally be ``"moderator_story"``: the two are consecutive super-steps of one
    ``_drive`` call joined by an unconditional edge, so in a REAL log they are
    always both present or both absent and the choice between them is an
    EQUIVALENCE. A synthetic log can hold one without the other, so hard-coding
    ``"end_screen"`` here would let these fixtures "detect" a change that is not
    a defect — measured: four tests in this file went red on that re-definition
    before the key was derived. Reading the constant keeps the equivalence an
    equivalence, and the guard against a marker that matches NOTHING a real game
    streams is section 4, where the log is real.

    **The messages are real and UNFLAGGED**, which matters for exactly one
    mutation: a scorer that read presence as "this delta's ``messages`` list is
    non-empty" instead of "a message carries the flag" would call this an
    arrived story. Section 4 measures that the real ending delta is indeed
    non-empty and indeed unflagged, so the shape is not a guess.
    """
    return {
        blunder_eval._STORY_ENDING_NODE: {
            "phase": "endgame",
            "messages": [AIMessage(content="The Mafia have taken the town.")],
        }
    }


def _result(**fields: Any) -> EvalResult:
    """A minimal synthetic ``EvalResult`` carrying only what is under test.

    DEDICATED PER TEST, never shared and never a fixture — see the module
    docstring's positional-trap note.
    """
    return EvalResult(provider="ollama", **fields)


def _quality_of(result: EvalResult) -> dict[str, Any]:
    """Render ``result`` and read its ``quality`` block back as the reader sees it.

    Round-tripping through YAML rather than asserting on rendered text is what
    makes the writer/reader seam testable: the record on disk is the contract.
    ``dict`` preserves insertion order and PyYAML constructs mappings in
    document order, so ``list(...)`` of the returned mapping IS the render
    order — which the key-order test relies on.
    """
    return yaml.safe_load(render_record(result, "2026-09-09"))["quality"]


def _story_keys_in(quality: dict[str, Any]) -> list[str]:
    """Every ``story_``-prefixed key the writer emitted, in render order."""
    return [key for key in quality if key.startswith("story")]


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The redirected ledger path, with both module globals pointed at ``tmp_path``.

    THE ONLY WRITE TARGET IN THIS FILE. The returned path is what section 3
    passes as ``args.ledger_path`` (a ``Path``, never a ``str`` — ``append_record``
    reads ``.parent`` off it); the two ``monkeypatch.setattr`` calls make the
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
# 1. The pure scorer: one game in, ``{count, denominator}`` out
# ==========================================================================


def test_a_game_that_never_reached_the_ending_is_in_neither_facet() -> None:
    """No ``end_screen`` delta ⇒ ``{"count": 0, "denominator": 0}``.

    A game that exhausted ``_play_one_game``'s anti-hang super-step backstop
    returns a perfectly good capture and counts in ``games_completed``, yet it
    never offered the story node a chance. Counting it as a MISSING story would
    inflate both facets from a game that never had the opportunity — the rate
    would then report a plumbing timeout as a story failure.
    """
    events = [
        {"night_open": {"night_round_picks": {}}},
        {"mafia_point": {"night_round_picks": {"p-1": "p-2"}}},
        {"resolve_night_kill": {"kill_log": [{"cycle": 1, "name": "Cara"}]}},
    ]

    assert score_story_missing(events) == {"count": 0, "denominator": 0}


def test_an_ending_with_no_story_is_one_missing_out_of_one() -> None:
    """The failure path: ``end_screen`` ran, no message carries the flag.

    The ``moderator_story`` delta is ``None`` here because that is what the real
    graph streams when the story call fails (measured in section 4, not assumed),
    and the ``end_screen`` delta carries unflagged messages — so this single
    fixture kills two different simplifications: dropping the ``isinstance(delta,
    dict)`` guard, and reading presence as "some delta had messages".
    """
    events = [_endgame_delta(), {"moderator_story": None}]

    assert score_story_missing(events) == {"count": 1, "denominator": 1}


@pytest.mark.parametrize(
    "node",
    ["moderator_story", "end_screen", "some_future_narration_node"],
    ids=["from_the_story_node", "folded_into_the_ending", "from_a_renamed_node"],
)
def test_a_flagged_message_counts_wherever_it_arrived(node: str) -> None:
    """Presence discriminates on the MESSAGE, never on the node (§2.9).

    ``score_story_missing`` answers "did this run produce a story?", and a story
    that arrived from a renamed or re-parented node still did. Pinning all three
    node placements is what stops the scorer being narrowed to
    ``delta["moderator_story"]`` — a change that would read as a tightening and
    would silently start counting a re-parented story as missing.
    """
    events = [_endgame_delta(), {node: {"messages": [_story_message()]}}]

    assert score_story_missing(events) == {"count": 0, "denominator": 1}


def test_a_none_story_delta_is_skipped_rather_than_crashing_the_batch() -> None:
    """The ``None`` half of the non-dict guard, on its own.

    ``nodes.endgame.moderator_story`` returns a bare ``{}`` when the story call
    fails and LangGraph streams that super-step as ``{"moderator_story": None}``.
    ``None.get("messages")`` is an ``AttributeError`` raised INSIDE a measured
    batch that has to finish, so the guard is load-bearing rather than
    defensive. The existing suite catches its removal only on the
    ``__interrupt__`` TUPLE (five driven tests in
    ``tests/test_substitution_ledger.py``); this is the ``None`` half.
    """
    events = [_endgame_delta(), {"moderator_story": None}]

    # The assertion is that this call RETURNS: an unguarded ``delta.get`` raises.
    assert score_story_missing(events)["denominator"] == 1


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(None, id="a_non_dict_event"),
        pytest.param(
            {"__interrupt__": ("an-interrupt-object",)},
            id="langgraphs_own_interrupt_tuple",
        ),
        pytest.param({"moderator_story": None}, id="a_none_delta"),
        pytest.param({"moderator_story": {"messages": None}}, id="messages_not_a_list"),
        pytest.param(
            {"moderator_story": {"messages": ["a bare string"]}},
            id="a_message_with_no_kwargs",
        ),
        pytest.param(
            {"moderator_story": {"messages": [SystemMessage(content="x")]}},
            id="a_message_with_empty_kwargs",
        ),
    ],
)
def test_a_malformed_or_dunder_event_is_skipped_not_raised(event: Any) -> None:
    """Every non-conforming log shape is skipped, and the ending still counts.

    ``eval_dialogue._drive`` pushes EVERY ``stream_mode="updates"`` payload to
    its sink unfiltered, so LangGraph's own ``__interrupt__`` channel — whose
    value is a tuple — is in every real event log this scorer is handed, story
    or no story. ``tuple.get`` raises just as ``None.get`` does. The remaining
    shapes are ordinary defensiveness: this runs inside a measured batch, and a
    scorer that raises loses the whole run's record.
    """
    events = [_endgame_delta(), event]

    assert score_story_missing(events) == {"count": 1, "denominator": 1}


def test_an_empty_log_scores_nothing_at_all() -> None:
    """No events ⇒ no opportunity ⇒ neither facet. The degenerate boundary."""
    assert score_story_missing([]) == {"count": 0, "denominator": 0}


def test_the_scorer_reads_the_story_flag_through_the_shared_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag name is IMPORTED, not copied — Task 9.2's rule, made loud.

    039's discipline: *import the thing, never copy it, so a reword breaks
    loudly in the offline suite instead of drifting a measurement.* A copied
    literal in the scorer would keep passing on the day ``graphia.state``
    rewords the kwarg, and the drift is the worst possible direction — every
    story would score as MISSING, so a healthy run would read as a total
    failure.

    Tested by redirecting ``blunder_eval``'s module global (which is what the
    ``from graphia.state import CLOSING_STORY_KWARG`` binding creates) and
    checking BOTH directions: the reworded flag is honoured, and the old
    spelling stops counting. A baked-in literal fails both.
    """
    reworded = "closing_story_v2"
    assert reworded != CLOSING_STORY_KWARG
    monkeypatch.setattr(blunder_eval, "CLOSING_STORY_KWARG", reworded)

    reworded_log = [
        _endgame_delta(),
        {"moderator_story": {"messages": [_story_message(reworded)]}},
    ]
    stale_log = [
        _endgame_delta(),
        {"moderator_story": {"messages": [_story_message(CLOSING_STORY_KWARG)]}},
    ]

    assert score_story_missing(reworded_log) == {"count": 0, "denominator": 1}
    assert score_story_missing(stale_log) == {"count": 1, "denominator": 1}


def test_the_harness_and_the_state_module_name_one_constant() -> None:
    """``blunder_eval`` exposes the shared constant itself, not a same-valued copy.

    A weak guard on purpose (``str`` literals intern, so an identical copy would
    satisfy ``is`` too), and it is here for the one thing it does catch that the
    redirect test above cannot: an inlined literal that deletes the import
    outright, which turns this into an ``AttributeError`` at collection.
    """
    assert blunder_eval.CLOSING_STORY_KWARG is state_module.CLOSING_STORY_KWARG


# ==========================================================================
# 2. The record writer: the gate is the denominator, and the rate is never alone
# ==========================================================================


@pytest.mark.parametrize(
    "pair",
    [
        pytest.param({}, id="no_pair_at_all"),
        pytest.param({"count": 0, "denominator": 0}, id="a_pair_with_a_zero_denominator"),
        pytest.param({"count": 3, "denominator": 0}, id="a_zero_denominator_with_a_count"),
        pytest.param({"count": 0}, id="a_pair_missing_its_denominator_key"),
    ],
)
def test_no_ending_in_the_whole_run_writes_no_story_keys(pair: dict[str, int]) -> None:
    """Absence is a third case, not a falsy one — on the WRITE side.

    ``run_eval`` leaves ``story_missing`` empty when no game reached the ending,
    so the empty case is the shape every pre-040 record has and the writer must
    omit the keys for it. The other three rows are the ones no existing test
    covers: a pair that EXISTS with a zero denominator. A writer gated on
    truthiness of the pair (``if result.story_missing:``) sails past the first
    row and then divides by zero on the rest; one gated on the data cannot.

    ``story_missing_rate: 0.0`` here would assert a clean measurement of a node
    that never ran, which is the single most misleading thing this record could
    say.
    """
    quality = _quality_of(_result(story_missing=pair, games_attempted=2, games_completed=2))

    assert _story_keys_in(quality) == []


def test_a_clean_run_records_a_zero_rate_over_its_real_denominator() -> None:
    """The trio on a CLEAN run: ``0.0`` over a real denominator, written out.

    THE ANTI-VACUITY CASE, and the one this whole signal exists to report. A
    writer gated on the NUMERATOR (``if stories_missing > 0:``) still passes any
    test that merely checks "the trio is present when something went wrong" —
    what it silently drops is the good news, and a reader then cannot tell a
    clean 20-game run from a pre-040 build with no story path at all. So the
    ``0.0`` is asserted by value, and the two integers beside it are asserted
    too: the rate is never written alone.
    """
    quality = _quality_of(
        _result(story_missing={"count": 0, "denominator": 3}, games_attempted=3, games_completed=3)
    )

    assert quality["story_missing_rate"] == 0.0
    assert quality["story_missing_games"] == 0
    assert quality["story_ending_games"] == 3


@pytest.mark.parametrize(
    "count, denominator, rate",
    [
        (0, 1, 0.0),
        (0, 20, 0.0),
        (1, 2, 0.5),
        (18, 20, 0.9),
        (2, 2, 1.0),
    ],
    ids=["clean_single", "clean_batch", "half", "the_039_smoke_shape", "total_failure"],
)
def test_the_rate_is_the_pairs_own_quotient(count: int, denominator: int, rate: float) -> None:
    """The rate is derived from the pair at write time — one definition of it.

    The 18-of-20 row is the scenario Slice 9 was written for: the record has to
    be visibly different from a clean one. The 0-of-20 row is its control.
    """
    quality = _quality_of(
        _result(story_missing={"count": count, "denominator": denominator})
    )

    assert quality["story_missing_rate"] == pytest.approx(rate)
    assert quality["story_missing_games"] == count
    assert quality["story_ending_games"] == denominator


def test_the_denominator_is_the_captures_pair_not_games_completed() -> None:
    """``games_completed`` is NOT the denominator, and the divergence is the point.

    ``games_completed`` counts games ``_play_one_game`` returned a capture for;
    the denominator counts games whose event log actually holds an ``end_screen``
    delta. A game that exhausted the anti-hang backstop is completed and yet had
    no ending, so it must not sit in this denominator — and any divergence
    between the two numbers is itself a finding about the batch, which is why
    both are on the record and neither is derived from the other.

    Asserted deliberately rather than left to be caught by accident: a writer
    that read ``result.games_completed`` here would report 1-of-5 (0.2) for a
    batch whose measurable endings were 1-of-2 (0.5).
    """
    quality = _quality_of(
        _result(
            story_missing={"count": 1, "denominator": 2},
            games_attempted=5,
            games_completed=5,
        )
    )

    assert quality["games_completed"] == 5
    assert quality["story_ending_games"] == 2
    assert quality["story_missing_rate"] == pytest.approx(0.5)


def test_the_trio_is_exactly_three_flat_keys_with_no_confidence_band() -> None:
    """Three keys, no more: no Wilson band, and none of them droppable.

    ``quality`` is a CENSUS of one run, not a sample of a population: 2-of-18
    missing stories is not an ESTIMATE of an underlying missing rate, it is the
    complete and exact composition of this run's endings, so there is no
    sampling uncertainty for an interval to express. (The mechanical reason
    reinforces it: ``_attach_ci`` walks ``result.metrics`` and keys off
    ``count``, so banding a ``quality`` field would mean leaking it into the
    metric map — the very thing section 3 forbids.)

    The exact-set assertion is what makes this row bite in both directions: it
    fails if a ``story_missing_ci_low`` / ``ci_high`` pair is bolted on, and it
    fails if ``story_ending_games`` is dropped so the rate is written alone.
    """
    quality = _quality_of(
        _result(story_missing={"count": 2, "denominator": 18})
    )

    assert _story_keys_in(quality) == list(_STORY_KEYS)


def test_the_trio_sits_after_the_diary_trio_and_before_the_wall_clock() -> None:
    """Position: beside ``games_failed_early``, not trailing after the clock.

    ``duration_seconds`` stays the last key of ``quality`` — the invariant every
    committed record holds and spec 041's own render comment pins — and the story
    trio joins the other "did this run measure anything?" counts rather than
    following the wall clock. Order is 039's diary trio, then this one, then
    041's ``substitutions`` sub-map. §2.10's sample block omits several of these
    keys, so the position was DECIDED rather than copied; this is where the
    decision is written down as behaviour.
    """
    result = _result(
        games_attempted=2,
        games_completed=2,
        diary_fallback={"count": 1, "denominator": 4},
        story_missing={"count": 1, "denominator": 2},
        substitutions={"diary": {"count": 1, "attempted": 4}},
        duration_seconds=42.5,
    )

    text = render_record(result, "2026-09-09")

    # RELATIVE positions, never a frozen key list — the shape spec 041's own
    # ``test_the_census_sits_between_the_diary_trio_and_the_wall_clock`` uses, and
    # for its reason: a full-list equality would turn red the day any later spec
    # adds a ``quality`` key, with no defect to find. ``rindex`` for the wall
    # clock because ``duration_seconds`` appears twice in a record (once in
    # ``run``, once mirrored here) and it is the ``quality`` one the trio must
    # precede.
    assert (
        text.index("  diary_entries_attempted:")
        < text.index("  story_missing_rate:")
        < text.index("  story_ending_games:")
        < text.index("  substitutions:")
        < text.rindex("  duration_seconds:")
    )


def test_a_pre_040_shaped_record_renders_byte_identically_without_the_trio() -> None:
    """A result with no pair renders exactly as it did before Slice 9 existed.

    The additive claim, stated as a whole-document equality rather than as a
    key-absence check: the ONLY difference between a story-bearing render and a
    storyless one is the three inserted lines, so every one of the committed
    records — none of which can satisfy the gate — is untouched by Slice 9.
    """
    storyless = _result(games_attempted=1, games_completed=1, duration_seconds=1.5)
    bearing = _result(
        games_attempted=1,
        games_completed=1,
        duration_seconds=1.5,
        story_missing={"count": 0, "denominator": 1},
    )

    without = render_record(storyless, "2026-09-09")
    with_trio = render_record(bearing, "2026-09-09")

    assert "story" not in without
    inserted = [line for line in with_trio.splitlines() if line not in without.splitlines()]
    assert inserted == [
        "  story_missing_rate: 0.0",
        "  story_missing_games: 0",
        "  story_ending_games: 1",
    ]


# ==========================================================================
# 3. The fold-in: a real ``run_eval`` over hand-built captures
# ==========================================================================


_AI_SPEAKER = "Ivy"
# Two unremarkable, mutually dissimilar Day lines. They are here for ONE reason:
# without any AI speech the ``repetition`` metric has a zero denominator, so
# ``_attach_ci`` skips every metric and the "no story key carries a Wilson band"
# assertion could not tell a correct record from a dead band machine. With them,
# the record carries at least one real band and that control has teeth.
_AI_LINES = [
    "The baker kept looking at the door all evening.",
    "Nobody has explained where the tanner went.",
]


def _capture(events: list[dict[str, Any]]) -> _GameCapture:
    """A ``_GameCapture`` with nothing in it but the event log under test.

    No graph, no provider, no model — ``score_story_missing`` is driver-
    independent by design (a plain list of ``{node: delta}`` dicts and nothing
    else), so the fold-in can be exercised without wiring a game.
    """
    return _GameCapture(
        ai_lines=list(_AI_LINES),
        ai_names={_AI_SPEAKER},
        ai_lines_with_speakers=[(_AI_SPEAKER, line) for line in _AI_LINES],
        players={},
        messages=[],
        captures=[],
        winner="mafia",
        events=events,
    )


@pytest.fixture
def folded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ledger: Path, env: Path
) -> Callable[..., tuple[EvalResult, dict[str, Any]]]:
    """Run the REAL ``run_eval`` over a list of hand-built per-game event logs.

    Real: ``load_config``, the settings assembly, every scorer, the fold-in, the
    ``quality`` render and the ledger append (into ``tmp_path``). Stubbed only
    where a stub cannot affect the story trio — ``_play_one_game`` (no graph, no
    model) and the two provenance collectors (a ``git status`` subprocess and, on
    the ollama path, an HTTP call to a server that is not running).

    Returns ``(result, record)``: the populated ``EvalResult`` and the record as
    parsed back off the redirected ledger, so both the in-memory pair and the
    written document are assertable from one call.
    """

    def _run(logs: list[list[dict[str, Any]]]) -> tuple[EvalResult, dict[str, Any]]:
        monkeypatch.setattr(
            blunder_eval,
            "collect_code_provenance",
            lambda root: {"commit": None, "branch": None, "dirty": False},
        )
        monkeypatch.setattr(
            blunder_eval,
            "collect_provider_provenance",
            lambda provider, large, small, base: {
                "name": provider,
                "large_model": large,
                "small_model": small,
            },
        )
        monkeypatch.setattr(
            blunder_eval,
            "_play_one_game",
            lambda args, game_index: _capture(logs[game_index]),
        )

        from graphia.config import load_config

        args = argparse.Namespace(
            provider="ollama",
            games=len(logs),
            seed=None,
            note="spec 040 task 9.4 offline fold-in",
            ledger_path=ledger,
            transcripts_root=tmp_path / "folded-transcripts",
        )
        result = run_eval(
            load_config(),
            args,
            transcripts_root=tmp_path / "folded-transcripts",
            run_id="20260909T000000",
        )
        record = yaml.safe_load(ledger.read_text(encoding="utf-8"))
        return result, record

    return _run


def test_a_batch_whose_games_never_ended_records_no_pair_and_no_keys(
    folded: Callable[..., tuple[EvalResult, dict[str, Any]]],
) -> None:
    """Two completed games, neither reached ``end_screen`` ⇒ nothing recorded.

    The run-level half of "absent when no game reached the ending": the fold-in
    leaves ``result.story_missing`` empty, so the writer never sees a pair at
    all. Both games still count in ``games_completed`` — they produced speech
    and votes and every other metric scored them — which is precisely why the
    story denominator cannot be taken from that number.
    """
    log = [{"night_open": {"night_round_picks": {}}}]

    result, record = folded([log, log])

    assert result.story_missing == {}
    assert result.games_completed == 2
    assert _story_keys_in(record["quality"]) == []


def test_a_batch_where_every_ending_told_its_story_records_a_zero_rate(
    folded: Callable[..., tuple[EvalResult, dict[str, Any]]],
) -> None:
    """The clean batch, end to end: ``0.0`` over 2 endings, in the written record."""
    log = [
        _endgame_delta(),
        {"moderator_story": {"messages": [_story_message()]}},
    ]

    result, record = folded([log, log])

    assert result.story_missing == {"count": 0, "denominator": 2}
    assert record["quality"]["story_missing_rate"] == 0.0
    assert record["quality"]["story_missing_games"] == 0
    assert record["quality"]["story_ending_games"] == 2


def test_a_batch_with_one_failed_story_records_the_missing_share(
    folded: Callable[..., tuple[EvalResult, dict[str, Any]]],
) -> None:
    """One story arrived, one did not ⇒ 1-of-2, and the record says so.

    This is Slice 9's claim in miniature: the two batches differ in nothing an
    operator could see except these three keys, and here they differ.
    """
    told = [_endgame_delta(), {"moderator_story": {"messages": [_story_message()]}}]
    lost = [_endgame_delta(), {"moderator_story": None}]

    result, record = folded([told, lost])

    assert result.story_missing == {"count": 1, "denominator": 2}
    assert record["quality"]["story_missing_rate"] == pytest.approx(0.5)
    assert record["quality"]["story_missing_games"] == 1
    assert record["quality"]["story_ending_games"] == 2


def test_the_trio_never_enters_the_versioned_metric_family(
    folded: Callable[..., tuple[EvalResult, dict[str, Any]]],
) -> None:
    """The trio stays in ``quality``; ``metrics`` gains nothing story-shaped.

    Leaking the pair into ``result.metrics`` would do three things at once, none
    of them wanted: ``_attach_ci`` would give a census a Wilson band (it walks
    ``result.metrics`` and keys off ``count``); ``render_record`` iterates ALL of
    ``result.metrics``, so the key would appear in the versioned metric tail; and
    ``eval_ledger.METRIC_ORDER`` would then disagree with the record's own metric
    map. Run health is not a scored behaviour of the AI — no model is judged well
    or badly by a story that failed to arrive.

    ANTI-VACUITY: the second assertion proves ``_attach_ci`` really ran on this
    record, so "no story key carries a band" is a statement about the trio's
    placement rather than about a dead code path.
    """
    log = [_endgame_delta(), {"moderator_story": None}]

    result, record = folded([log])

    assert result.story_missing == {"count": 1, "denominator": 1}
    assert [key for key in result.metrics if "story" in key] == []
    assert [key for key in record["metrics"] if "story" in key] == []
    # The band machinery is live on this very record — so the absence above is
    # the trio's placement, not a disabled ``_attach_ci``.
    banded = [name for name, facets in record["metrics"].items() if "ci_low" in facets]
    assert banded, f"no metric on this record carried a Wilson band: {record['metrics']}"


# ==========================================================================
# 4. The real failure path: what the graph actually streams when the story dies
# ==========================================================================


@pytest.mark.parametrize(
    "stories, expected, story_in_log",
    [
        pytest.param(
            [_SCRIPTED_STORY],
            {"count": 0, "denominator": 1},
            True,
            id="the_story_arrived",
        ),
        pytest.param(
            [RuntimeError("the story model is down")],
            {"count": 1, "denominator": 1},
            False,
            id="the_story_call_failed",
        ),
    ],
)
def test_a_real_games_event_log_scores_its_own_story_health(
    stories: list[Any],
    expected: dict[str, int],
    story_in_log: bool,
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both arms of the scorer, measured on a genuine offline capture.

    THE GAME IS THE CHEAPEST ONE THAT REACHES AN ENDING: ``GRAPHIA_MAX_DAYS=1``
    makes the very first ``night_open`` trip the spec-023 runaway cap, so
    ``route_after_night_open`` short-circuits straight to ``end_screen`` with no
    Night kill, no Day and no vote. That matters for determinism as much as for
    speed — no pointing target is ever chosen, so no RNG-using helper needs
    pinning and no live state read from inside a running node can flake (the
    failure mode ``tests/test_substitution_ledger.py`` records at 1-run-in-8).

    THE FAILURE ARM IS SCRIPTED EXPLICITLY, and it has to be:
    ``conftest.UNSCRIPTED_DEFAULTS[Story]`` ANSWERS an unprimed story queue, so
    every driven test in the suite records a story that arrived and the
    failure path is unreachable by omission. ``stories=[RuntimeError(...)]``
    books the call (``calls_by_schema[Story] == 1``) and raises inside it, which
    ``moderator_story``'s broad ``except`` turns into an ending with no story —
    spec 040 §2.8's correct outcome, and the one the trio exists to count.

    WHAT THIS ARM MEASURES THAT NO SYNTHETIC LOG CAN, and why the synthetic
    fixtures above are entitled to their shapes:

    * the failed super-step really streams as ``{"moderator_story": None}`` — a
      bare ``{}`` return, not an empty ``messages`` list — so the scorer's
      ``isinstance(delta, dict)`` guard is on the live path;
    * the real ``end_screen`` delta really carries a non-empty ``messages`` list
      with NO story flag on it, which is what makes "presence == the delta had
      messages" a wrong answer rather than an equivalent one;
    * ``eval_dialogue._drive`` really pushes ``{"__interrupt__": (...)}`` into
      the same sink, so the guard is load-bearing twice over.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_MAX_DAYS", "1")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    # Personas are scripted (and diversity is off) so setup cannot spend the run
    # regenerating identical unscripted defaults — irrelevant to the story, and
    # the flag-off arm keeps the game short.
    monkeypatch.setenv("GRAPHIA_PERSONA_DIVERSITY", "0")

    from graphia.config import load_config
    from graphia.graph import build_graph, make_run_config

    fake_small(_NAME_POOL)
    fake = fake_large(
        personas=[
            Persona(personality=f"trait-{i}", manner=f"manner-{i}", public_backstory=f"trade {i}")
            for i in range(2)
        ],
        stories=stories,
    )

    config = load_config()
    assert config.max_days == 1, "GRAPHIA_MAX_DAYS did not reach the config"
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # ``_play_one_game``'s own capture seam: every ``stream_mode="updates"``
    # payload, unfiltered, in order — the exact list the scorer is handed live.
    events: list[dict[str, Any]] = []
    _drive(graph, run_config, {"messages": []}, on_update=events.append)
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}
    _drive(graph, run_config, Command(resume=_HUMAN_NAME), on_update=events.append)

    assert graph.get_state(run_config).next == (), (
        "the runaway game did not reach END in one resume; "
        f"pending: {graph.get_state(run_config).next!r}"
    )
    assert graph.get_state(run_config).values.get("winner") == "runaway"
    assert fake.calls_by_schema[Story] == 1, (
        "the story call was never made, so this arm measures nothing"
    )

    assert score_story_missing(events) == expected

    # --- what the log actually held, so the synthetic fixtures are not guesses ---
    story_deltas = [
        delta
        for event in events
        for node, delta in event.items()
        if node == "moderator_story"
    ]
    assert len(story_deltas) == 1
    if story_in_log:
        assert isinstance(story_deltas[0], dict)
    else:
        assert story_deltas[0] is None, (
            "the failure path is expected to stream a None delta (a bare {} return); "
            f"got {story_deltas[0]!r}"
        )

    endgame_deltas = [
        delta
        for event in events
        for node, delta in event.items()
        if node == "end_screen"
    ]
    assert len(endgame_deltas) == 1
    endgame_messages = endgame_deltas[0]["messages"]
    assert endgame_messages, "end_screen streamed no messages, so the row-8 fixture is unrealistic"
    assert not [
        m for m in endgame_messages
        if (getattr(m, "additional_kwargs", None) or {}).get(CLOSING_STORY_KWARG)
    ], "the ending's own messages must not carry the story flag"

    assert [
        event for event in events if any(isinstance(delta, tuple) for delta in event.values())
    ], "no __interrupt__ tuple reached the sink, so the guard's second half is untested here"
