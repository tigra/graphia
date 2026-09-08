"""Spec 040 (The Moderator's Closing Story), Task 1.8 — the NODE's contract.

WHAT THIS FILE OWNS, AND WHAT IT DELIBERATELY DOES NOT.

``tests/test_spec040_story_topology.py`` (Task 1.6) owns ROUTING: that the
``end_screen -> moderator_story -> END`` edge is reached from all three arrival
paths, that the story runs directly after the facts, that the graph then halts,
and the career-event ORDERING that justifies the two-node topology. None of
that is re-asserted here.

This file owns the node's own contract, which spec 040 §2.8 states in five
clauses, all of them small and every one of them load-bearing:

1. Exactly ONE story message per game, and it is the last thing the player
   reads.
2. It is a ``SystemMessage`` with **no** ``name`` — a ledger-corruption guard,
   not a style choice (§2.6).
3. It carries ``additional_kwargs={CLOSING_STORY_KWARG: True}``, which is the
   only way any downstream reader identifies it. Task 5.3 added a SECOND flag,
   ``CLOSING_STORY_TRUNCATED_KWARG``, present only when the clamp actually
   dropped a paragraph — so the exact-equality assertion below still holds for
   every story in this file (all of them fit the bound) and going red under an
   always-set flag is the point of writing it as equality. Task 5.4 owns the
   clipped case; nothing here exercises it.
4. It does **not** return ``phase``; ``end_screen`` set that a super-step ago.
5. Failure means NO story — no placeholder, no error, nothing half-rendered —
   and the node never raises whatever comes back at it.

WHY THE FAILURE ASSERTIONS ALL CARRY ``calls_by_schema[Story]``
--------------------------------------------------------------

Spec 040 is the one feature in the game whose *absence* is a correct outcome,
so "no story message" is ambiguous between three causes and only one of them is
the property under test:

* the model failed — the designed outcome, what these tests mean;
* the queue raised for some unrelated reason — a real failure;
* **the fake was never reached at all** — a stubbing mistake, which would make
  every absence assertion here pass while measuring nothing.

The third is not hypothetical. ``conftest``'s ``_LoudFailureLLM`` raises
``RuntimeError``, an ordinary ``Exception``, so a test that forgot
``fake_large``'s re-patch of ``graphia.nodes.endgame.get_large`` is swallowed by
``moderator_story``'s broad ``except`` into a perfectly legitimate no-story
ending. ``calls_by_schema[Story]`` is the only thing that tells the three
apart, and no inspection of the ending can. Every test below that asserts on
absence therefore also asserts the counter — mutation-verified: deleting
``fake_large``'s endgame re-patch leaves every other assertion in
:func:`test_the_failure_path_leaves_the_facts_alone_and_still_books_the_call`
green and fails only the counter.

EVERY ASSERTION HERE WAS MUTATION-VERIFIED BEFORE THIS FILE WAS COMMITTED
-------------------------------------------------------------------------

A test that passes against the opposite behaviour is worthless, and this
project has been bitten twice: Task 1.7 found an inverted assertion that had
silently gone vacuous, and spec 041 found three green tests that stubbed an
exception type instead of exercising the real parser. So each production change
below was applied for real, from 11 green in this file, and reverted
(2026-09-08):

* ``fake_large``'s endgame re-patch deleted — **10 failed**; the failure-path
  test on the counter ALONE (green again with just that assertion removed).
* ``"runaway": "runaway"`` added to ``_OUTCOME_BY_WINNER`` — **1 failed**, the
  runaway param, on the career-event ABSENCE.
* ``end_screen``'s career emit disabled — **3 failed**: the two
  outcome-producing params and the failure path. The runaway param stayed
  green, as it must.
* the story emitted as an ``AIMessage`` named after a player — **5 failed**,
  including the ledger guard on "must not enter the repetition pool".
* the story left a ``SystemMessage`` but given a ``name`` — **4 failed** on the
  ``name`` assertion; the ledger guard stayed green, which is the point: the
  guard is doubly redundant on purpose and each half needed its own test.
* ``and result.text.strip()`` dropped from the answer check — **3 failed**, the
  three whitespace params.
* ``phase`` re-asserted / overwritten in the node's update — **1 / 4 failed**.
* the ``CLOSING_STORY_KWARG`` flag replaced with another key — **4 failed**.
* 039's ``" ".join(text.split())`` clamp used in place of ``.strip()`` —
  **4 failed** on the verbatim-content assertion.
* a placeholder story added on the failure path (§2.8's predicted "reader adds
  one to match the pattern") — **5 failed**.
* the outer ``try/except`` removed — **1 failed**, and it failed by RAISING
  ``ValidationError`` out of the node, which is the node docstring's measured
  claim reproduced.
* a second, distinct flagged message emitted — **4 failed**; and a trailing
  unflagged message — **4 failed** on "the story must be the last message".

ONE LIMIT WORTH RECORDING, found by that last mutation. Emitting the *same
message object* twice does **not** fail the driven "exactly one" assertion,
because ``add_messages`` de-duplicates by message id and collapses them. So on
the driven paths that assertion is jointly enforced by the reducer; the
direct-call test is what pins the node's own update, and a genuine double emit
(two distinct messages) is caught in both places.

WHY THE CAREER EVENT IS NOT ASSERTED THE SAME WAY ON ALL THREE ARRIVALS
----------------------------------------------------------------------

A Day-cap runaway folds **no** career outcome, and that is pre-existing rather
than anything this node does: ``endgame._OUTCOME_BY_WINNER`` deliberately omits
``"runaway"`` (spec 023 — Game Length Runaway Safeguard, on the grounds that a
cap-hit is not a legitimate result), so ``end_screen`` computes ``outcome=None``
and skips the emit entirely. The parametrisation below therefore carries the
expected outcome per arrival and asserts PRESENCE on the two that produce one
and ABSENCE on the runaway. Asserting presence on all three would fail for a
reason that has nothing to do with spec 040.

WHAT IS DELIBERATELY LEFT UNTESTED
----------------------------------

Task 1.4 probed the node's totality through 33 direct checks in a throwaway
that was deleted on purpose, and reproducing all of it here would be a
maintenance cost with no regression value — ``_ai_story`` is total by
construction and §2.8's contract is genuinely small. Three things from that
probe are NOT committed, each for a stated reason:

* **All five ``winner`` values.** They only reach ``_winner_line``, whose
  totality (including the runaway and draw lines and the defensive
  no-winner one) is already pinned by the endgame's own tests. The story path
  adds no branch of its own.
* **``get_large`` itself raising, as distinct from ``invoke`` raising.** Both
  sit inside the same single ``try`` and produce the same ``None``, so a second
  test would exercise the same line twice. The scripted raising queue below is
  also the realistic one.
* **A wrong-TYPE answer from the model** (the ``isinstance(result, Story)``
  guard). A test for it is **vacuous by construction, and that was measured
  rather than argued**: such a test was written, run against the guard and
  then against a build with the guard removed, passed **both** times, and was
  deleted. Without the guard, ``result.text`` on a non-``Story`` raises
  ``AttributeError`` inside the same ``try`` and returns the same ``None``, so
  there is no mutation that can make the assertion fail. An unmutatable test
  is worse than no test — it reads as coverage it does not provide.

All model calls go through ``fake_large`` / ``fake_small``; nothing here
touches real Bedrock, and nothing here touches the quality ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from conftest import endgame_facts_message
from graphia.career_events import KIND_GAME_ENDED
from graphia.config import (
    _DEFAULT_STORY_CONTEXT_WINDOW,
    _DEFAULT_STORY_TOKEN_BUDGET,
    load_config,
)
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Diary, Pointing, Story
from graphia.nodes.endgame import moderator_story
from graphia.state import CLOSING_STORY_KWARG, PlayerState
from graphia.tools.blunder_eval import (
    _ai_lines_with_names,
    _ai_lines_with_speakers,
)

# The graph-driving harness is IMPORTED from the Task 1.6 file rather than
# copied. Three of the tests below need a real game driven to a real ending,
# and that driver (stream one stretch, answer the pending interrupt, repeat)
# is the same driver in both files. Duplicating it would put two copies of the
# interrupt pump in one slice, where a fix to one silently misses the other.
# The alternative — lifting it into ``conftest.py``, where this suite's other
# shared helpers live — is the tidier long-term home and would mean editing
# Task 1.6's file to remove what it defines; left alone deliberately, since
# both files belong to the same slice and neither is the obvious owner.
from test_spec040_story_topology import (
    HUMAN_NAME,
    NAME_POOL,
    _CapturingEmitter,
    _passive_human,
    _run_to_end,
)

# THREE properties of this text are load-bearing and must survive any reword.
#
# * It is MULTI-PARAGRAPH, separated by a blank line. Slice 1's ``_ai_story``
#   ends in a plain ``.strip()`` precisely so the internal blank lines that ARE
#   the story's structure survive, which is what §2.5 says 039's
#   ``_clamp_diary_entry`` (``" ".join(text.split())``) would fold away and why
#   that clamp must not be reused here. Asserted directly below.
# * It carries a token no production string could emit, so an exact match
#   proves the text travelled from the scripted queue into the message
#   untouched, and no substring search can be satisfied by coincidence.
# * It names no player, so the ledger-guard assertions cannot be tripped by a
#   roster name and the repetition-pool control cannot pass by accident.
STORY_SENTINEL_TOKEN = "CLOSING-STORY-SENTINEL"
SCRIPTED_STORY = Story(
    text=(
        f"{STORY_SENTINEL_TOKEN}: it began, as these things do, with someone "
        "counting the wrong things in the wrong order and telling nobody.\n\n"
        "By the end the counting had stopped mattering, and the table went "
        "quiet in the particular way a table goes quiet when everyone has "
        "already decided and nobody wants to say so first."
    )
)

# Scripted defensively, exactly as the Task 1.6 file does: the Day->Night
# hinge fans ``day_diary`` out whenever a game survives its first Day. These
# scenarios are not meant to get that far, but no assertion here should be the
# thing that depends on that.
SCRIPTED_DIARY = Diary(entry="Day one is done and I trust no one yet.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _story_messages(messages: Any) -> list[Any]:
    """Every message flagged as the closing story, in order.

    Selects on the PUBLIC ``CLOSING_STORY_KWARG`` flag, never on type or
    position — spec 040 §2.9's rule for every downstream reader, applied here
    so these tests break the same way a real reader would if the flag ever
    stopped being set. The constant is imported rather than copied, so a
    reword breaks this loudly in the offline suite instead of drifting it.
    """
    return [
        m
        for m in messages
        if getattr(m, "additional_kwargs", None)
        and m.additional_kwargs.get(CLOSING_STORY_KWARG)
    ]


def _assert_story_message_shape(story: Any) -> None:
    """The message's own contract: a ``SystemMessage``, unnamed, verbatim.

    THE TYPE AND THE MISSING ``name`` ARE THE LEDGER-CORRUPTION GUARD (§2.6),
    which :func:`test_the_story_is_excluded_from_the_repetition_pool` then
    exercises against the real extractors. Both halves are asserted because
    the guard is deliberately doubly redundant — a ``SystemMessage`` fails
    ``isinstance(m, AIMessage)`` and no ``name`` fails ``name in ai_names`` —
    and a change that broke only one of them would still be a change worth
    catching here.
    """
    assert isinstance(story, SystemMessage), (
        f"the story must be a SystemMessage in the Moderator's voice; got "
        f"{type(story).__name__}"
    )
    assert not isinstance(story, AIMessage), (
        "the story must never be an AIMessage — blunder_eval's repetition "
        "extractors admit AIMessages by name and would pool it (§2.6)"
    )
    assert getattr(story, "name", None) is None, (
        "the story message must carry no name; a name matching a player's "
        f"would enter the repetition pool. Got {story.name!r}"
    )
    assert story.content == SCRIPTED_STORY.text, (
        "the scripted story text must reach the message verbatim"
    )


def _install_live_dispatcher(fake: Any, graph: Any, run_config: Any) -> None:
    """Answer the schemas whose payload is a live player id, at invoke time.

    ``Pointing``, ``DayAction`` and ``Ballot`` cannot be pre-scripted: the real
    roster ids only exist once ``assign_roles`` has run on graph state. Same
    live-dispatcher trick the Task 1.6 file and ``test_slice8_endgame`` use.

    One consequence is deliberate and worth knowing: these three branches
    return WITHOUT delegating to the fake's queue, so they book no
    ``calls_by_schema`` entry. ``Story`` falls through to the real queue, so
    ``calls_by_schema[Story]`` counts story calls and nothing else.

    A Night target prefers a non-human Citizen and falls back to any living
    Citizen, which is what makes ONE dispatcher serve two different scenarios:
    at the 1-Mafioso/2-Citizen table the AI Citizen dies and the table is left
    at parity (a night-side win); at 1-and-3 an AI Citizen dies without reaching
    parity, the Day runs, and every AI votes the sole Mafioso out (a day-side
    win).
    """
    original_invoke = fake._invoke

    def _invoke_live(schema: type, messages: Any) -> Any:
        players = graph.get_state(run_config).values.get("players", {})
        alive = [p for p in players.values() if p.is_alive]
        if schema is Pointing:
            targets = [
                p.id for p in alive if p.role == "law_abiding" and not p.is_human
            ] or [p.id for p in alive if p.role == "law_abiding"]
            return Pointing(target_id=targets[0] if targets else "missing")
        if schema is DayAction:
            mafia = [p.id for p in alive if p.role == "mafia" and not p.is_human]
            if mafia:
                return DayAction(kind="vote", target_id=mafia[0])
            return DayAction(kind="speak", text="(nothing to add.)")
        if schema is Ballot:
            return Ballot(yes=True)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]


# --------------------------------------------------------------------------
# Hand-built state for the direct-call tests
#
# The node's failure vocabulary and its no-``phase`` contract are properties of
# ONE super-step, and a direct call is the sharper instrument for them: a
# driven game cannot see whether ``phase`` was returned (``end_screen`` set it
# to the same value a super-step earlier), and cannot see the difference
# between an empty update and one carrying a placeholder that happens to be
# blank. Task 1.6 already pins that the node is reachable from every ending,
# so nothing is lost by not driving a game for these.
# --------------------------------------------------------------------------

DIRECT_PLAYERS: dict[str, PlayerState] = {
    "p-human": PlayerState(
        id="p-human", name=HUMAN_NAME, role="law_abiding", is_human=True
    ),
    "p-ai": PlayerState(
        id="p-ai", name="Ivy", role="law_abiding", is_human=False, is_alive=False
    ),
    "p-mafia": PlayerState(
        id="p-mafia", name="Marco", role="mafia", is_human=False
    ),
}

DIRECT_STATE: dict[str, Any] = {
    "players": DIRECT_PLAYERS,
    "kill_log": [{"cycle": 1, "name": "Ivy", "cause": "night", "role": None}],
    "winner": "mafia",
    "messages": [],
}


# --------------------------------------------------------------------------
# One story message per game, on every arrival
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Arrival:
    """One of the three ways a game reaches ``end_screen``.

    ``career_outcome`` is ``None`` for the runaway on purpose — see the module
    docstring: ``_OUTCOME_BY_WINNER`` omits ``"runaway"`` (spec 023 — Game
    Length Runaway Safeguard), so that ending folds no career event at all.
    """

    id: str
    env: dict[str, str]
    winner: str
    career_outcome: str | None


_ARRIVALS = [
    # One Mafioso, two Citizens (the human is one). Whoever the Mafia takes on
    # Night 1 leaves the table at 1 vs 1, which is parity, so
    # ``check_win_night`` fires before a single Day runs.
    _Arrival(
        id="night_win",
        env={
            "GRAPHIA_ROLE": "law-abiding",
            "GRAPHIA_NUM_MAFIA": "1",
            "GRAPHIA_NUM_CITIZENS": "2",
        },
        winner="mafia",
        career_outcome="mafia_win",
    ),
    # One Mafioso, three Citizens. Night 1 takes a Citizen without reaching
    # parity; Day 1 executes the sole Mafioso and ``check_win_day`` sees no
    # Mafia alive.
    _Arrival(
        id="day_win",
        env={
            "GRAPHIA_ROLE": "law-abiding",
            "GRAPHIA_NUM_MAFIA": "1",
            "GRAPHIA_NUM_CITIZENS": "3",
        },
        winner="law_abiding",
        career_outcome="law_abiding_win",
    ),
    # ``GRAPHIA_MAX_DAYS=1``, so the very first ``night_open`` sees
    # ``cycle >= max_days`` and declares ``winner="runaway"`` before the
    # pointing loop is entered. No Day, no Night kill, no vote — and no career
    # outcome.
    _Arrival(
        id="runaway",
        env={"GRAPHIA_ROLE": "law-abiding", "GRAPHIA_MAX_DAYS": "1"},
        winner="runaway",
        career_outcome=None,
    ),
]


@pytest.mark.parametrize("arrival", _ARRIVALS, ids=lambda a: a.id)
def test_the_ending_carries_exactly_one_story_message_on_every_arrival(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    arrival: _Arrival,
) -> None:
    """Every finished game ends with one flagged, unnamed Moderator story.

    Driven end-to-end on each of the three arrival paths, because the node's
    contract is a claim about the ENDING rather than about a function: the
    three routers reach ``end_screen`` with wildly different state (the runaway
    has no Night kill, no Day and no vote at all), and "one story message per
    game" has to hold for each of them.

    Six claims, and each fails a different way of breaking the node:

    * exactly one message carries ``CLOSING_STORY_KWARG`` — not zero (the call
      was made and its answer thrown away) and not two;
    * it is a ``SystemMessage`` with no ``name`` (the ledger guard);
    * its content is the scripted text, verbatim;
    * it is the LAST message in the game — the story is the last thing the
      player reads;
    * ``phase`` is still ``"end"``, untouched by a node that has no opinion
      about it;
    * ``calls_by_schema[Story] == 1`` — the story was told by the model on this
      path, exactly once, rather than by anything else.

    Plus the career event per arrival: present with its outcome on the two
    paths that produce one, absent on the runaway.
    """
    for key, value in arrival.env.items():
        monkeypatch.setenv(key, value)
    fake_small(NAME_POOL)

    emitter = _CapturingEmitter()
    config = load_config()
    graph, thread_id = build_graph(config, career_emitter=emitter)
    run_config = make_run_config(thread_id)

    fake = fake_large(stories=[SCRIPTED_STORY], diaries=[SCRIPTED_DIARY])
    _install_live_dispatcher(fake, graph, run_config)

    seen = _run_to_end(graph, run_config, responder=_passive_human)

    state = graph.get_state(run_config).values
    assert state.get("winner") == arrival.winner, (
        f"scenario did not reach the intended {arrival.id} ending; "
        f"trajectory: {seen}"
    )

    messages = state.get("messages", [])
    stories = _story_messages(messages)
    assert len(stories) == 1, (
        "expected exactly ONE message flagged with CLOSING_STORY_KWARG; found "
        f"{len(stories)} among {len(messages)} messages"
    )
    _assert_story_message_shape(stories[0])

    assert messages[-1] is stories[0], (
        "the story must be the last message of the game; the ending closed "
        f"with {type(messages[-1]).__name__} instead"
    )
    assert state.get("phase") == "end", (
        "moderator_story must leave end_screen's phase alone; phase was "
        f"{state.get('phase')!r}"
    )
    assert fake.calls_by_schema[Story] == 1, (
        "the story must come from exactly one model call on this path; "
        f"calls_by_schema[Story] was {fake.calls_by_schema[Story]}"
    )
    assert Story not in fake.unscripted_defaults_served, (
        "the story served the fake's unscripted default, so this test "
        "measured the default rather than the scripted answer"
    )

    ended = [
        event for _, event in emitter.emissions if event.kind == KIND_GAME_ENDED
    ]
    if arrival.career_outcome is None:
        # A Day-cap runaway folds NO career outcome (spec 023 — Game Length
        # Runaway Safeguard): ``_OUTCOME_BY_WINNER`` omits ``"runaway"``, so
        # ``end_screen`` computes ``outcome=None`` and skips the emit. Adding
        # the story node did not change that.
        assert not ended, (
            "a Day-cap runaway must fold no career outcome; emitter held "
            f"{[e.kind for _, e in emitter.emissions]}"
        )
    else:
        assert len(ended) == 1, (
            f"expected exactly one {KIND_GAME_ENDED} event, got "
            f"{[e.kind for _, e in emitter.emissions]}"
        )
        assert ended[0].outcome == arrival.career_outcome, (
            f"career event carried outcome {ended[0].outcome!r}, expected "
            f"{arrival.career_outcome!r}"
        )


# --------------------------------------------------------------------------
# The failure path — and the distinction that makes it worth asserting
# --------------------------------------------------------------------------


def test_the_failure_path_leaves_the_facts_alone_and_still_books_the_call(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A story call that raises ends the game on the facts, with nothing added.

    §2.8 makes absence a LEGITIMATE outcome, so four of the five assertions
    below would pass on a broken test that never reached the model at all.
    ``calls_by_schema[Story] == 1`` is the one that closes that hole, and it is
    the single most important assertion in Task 1.8: the counter is incremented
    before the queue is popped, so a raising queue books the call while an
    unreached fake books none.

    Everything else about the ending must be exactly as it is today: the facts
    message stands alone and last, ``phase`` is ``"end"``, the career event was
    emitted (it is committed a super-step earlier, so a failing story cannot
    cost it), and the graph halted rather than parking on a pending task.

    NO FALLBACK TEXT is the point of the first assertion, and Task 1.5 records
    the argument at the failure site: a placeholder story IS the half-rendered
    ending the functional spec forbids, and Slice 9's measurement depends on
    absence, because with no placeholder **presence is the signal**.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    fake_small(NAME_POOL)

    emitter = _CapturingEmitter()
    config = load_config()
    graph, thread_id = build_graph(config, career_emitter=emitter)
    run_config = make_run_config(thread_id)

    fake = fake_large(
        stories=[RuntimeError("the provider fell over")],
        diaries=[SCRIPTED_DIARY],
    )
    _install_live_dispatcher(fake, graph, run_config)

    seen = _run_to_end(graph, run_config, responder=_passive_human)

    state = graph.get_state(run_config).values
    assert state.get("winner") == "mafia", (
        f"scenario did not reach the night-side win; trajectory: {seen}"
    )

    messages = state.get("messages", [])
    assert _story_messages(messages) == [], (
        "a failed story must leave NO flagged message — no placeholder, no "
        "apology, nothing half-rendered (§2.8)"
    )

    # Exactly the facts message, and it is last: the ending reads as it does
    # today. Selected on the roster header rather than by index, per §3.
    facts = endgame_facts_message(messages)
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    assert system_msgs[-1] is facts, (
        "with no story, the facts message must be the last SystemMessage of "
        "the game"
    )

    assert state.get("phase") == "end", (
        f"phase must still be 'end'; got {state.get('phase')!r}"
    )
    ended = [
        event for _, event in emitter.emissions if event.kind == KIND_GAME_ENDED
    ]
    assert len(ended) == 1 and ended[0].outcome == "mafia_win", (
        "the career event is committed a super-step before the story call, so "
        "a failing story cannot cost it; emitter held "
        f"{[(e.kind, e.outcome) for _, e in emitter.emissions]}"
    )
    assert graph.get_state(run_config).next == (), (
        "the graph must have reached END; it still had "
        f"{graph.get_state(run_config).next!r} pending"
    )

    # THE ASSERTION THE OTHER FOUR DEPEND ON. Without it this test passes just
    # as green when the fake was never reached and ``safe_llm``'s loud double
    # was swallowed into a legitimate-looking no-story ending.
    assert fake.calls_by_schema[Story] == 1, (
        "the story call must have been MADE and failed, not skipped; "
        f"calls_by_schema[Story] was {fake.calls_by_schema[Story]}. A zero "
        "here means the fake was never reached — check that fake_large still "
        "re-points graphia.nodes.endgame.get_large."
    )
    assert Story not in fake.unscripted_defaults_served, (
        "the absence must come from the scripted exception, not from an "
        "unprimed queue serving its default"
    )


# --------------------------------------------------------------------------
# The node's own return shape
# --------------------------------------------------------------------------


def test_the_node_returns_a_message_and_nothing_else(
    env: Path,
    fake_large: Callable[..., Any],
) -> None:
    """The success update is ``{"messages": [...]}`` — no ``phase``, no extras.

    ``phase`` is asserted by its ABSENCE FROM THE UPDATE, which is the only
    place the claim is visible at all: ``end_screen`` set ``phase="end"`` one
    super-step earlier, so a node that re-asserted the same value would be
    invisible in every driven game. §2.8 lists "``phase`` — **not returned**"
    as a clause of the contract, and this is what tests it.

    The paragraph assertion is the other half of a §2.5 decision: Slice 1's
    ``.strip()`` is a forward-compatible placeholder for Slice 5's clamp
    precisely because it LEAVES the internal blank lines that are the story's
    structure. Reusing 039's ``_clamp_diary_entry``
    (``" ".join(text.split())``) here would fold them away, which is the
    mistake §2.5 predicts and this assertion catches.
    """
    fake = fake_large(stories=[SCRIPTED_STORY])

    result = moderator_story(DIRECT_STATE)

    assert set(result) == {"messages"}, (
        "the node must return exactly a messages update; its keys were "
        f"{sorted(result)}. An EMPTY update here means no story was told at "
        "all — check that fake_large still re-points "
        "graphia.nodes.endgame.get_large."
    )
    assert "phase" not in result, (
        "moderator_story must not return phase — end_screen set it a "
        "super-step ago and this node has no opinion about it (§2.8)"
    )
    assert len(result["messages"]) == 1
    story = result["messages"][0]
    _assert_story_message_shape(story)
    assert story.additional_kwargs == {CLOSING_STORY_KWARG: True}, (
        "the flag is how every downstream reader identifies the story "
        f"(§2.9); kwargs were {story.additional_kwargs!r}"
    )
    assert "\n\n" in story.content, (
        "the story's paragraph breaks must survive; a whitespace-folding "
        "clamp (039's _clamp_diary_entry) would collapse them (§2.5)"
    )
    assert fake.calls_by_schema[Story] == 1


@pytest.mark.parametrize(
    "scripted",
    [
        pytest.param(RuntimeError("the provider fell over"), id="raises"),
        pytest.param(Story(text=""), id="empty"),
        pytest.param(Story(text="   "), id="spaces"),
        pytest.param(Story(text="\n\n\t "), id="whitespace"),
    ],
)
def test_an_unusable_answer_yields_no_story_at_all(
    env: Path,
    fake_large: Callable[..., Any],
    scripted: Any,
) -> None:
    """Every unusable answer produces the same empty update, and books the call.

    The whole failure vocabulary in one place: a call that raises and a call
    that returns a valid ``Story`` carrying nothing usable are the SAME ending,
    which is what §2.8 means by "empty or whitespace-only counts as failure".

    The whitespace cases are the ones worth committing rather than proving
    once, because Slice 5 is scheduled to REPLACE the ``.strip()`` that
    implements them with the paragraph-preserving clamp. A clamp that returns
    ``""`` for a whitespace-only answer, with the truthiness guard dropped,
    would emit an empty story message — a blank section in the ending, which is
    exactly the half-rendered outcome the functional spec forbids.

    ``== {}`` rather than "no story message" is deliberate: it also forbids a
    placeholder (Task 1.5) and a stray ``phase``, in one assertion.
    """
    fake = fake_large(stories=[scripted])

    assert moderator_story(DIRECT_STATE) == {}, (
        "an unusable answer must produce an EMPTY update — no message, no "
        "placeholder, no phase (§2.8)"
    )
    assert fake.calls_by_schema[Story] == 1, (
        "the call must have been made and rejected, not skipped; "
        f"calls_by_schema[Story] was {fake.calls_by_schema[Story]}"
    )


def test_a_helper_that_breaks_its_contract_yields_no_story_rather_than_a_raise(
    env: Path,
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OUTER guard layer, exercised on the failure only it can catch.

    This is the test that stops the outer ``try/except`` being deleted as
    redundant — and a maintainer reading §2.8 would reasonably conclude that it
    is, because §2.8's stated reason for it ("belt-and-braces for a malformed
    ``players`` map or a persona shape surprise") names two things that are read
    INSIDE ``_ai_story``'s own ``try``. The node's docstring records the real,
    measured reason: a helper return that breaks the helper's contract raises
    OUTSIDE the inner ``try`` entirely, and only this catch is between that and
    the graph.

    **THE MECHANISM CHANGED UNDER THIS TEST AND THE DOCSTRING IS UPDATED
    RATHER THAN LEFT DESCRIBING A PATH THAT NO LONGER EXISTS (Task 5.3,
    2026-09-09).** The contract was ``str | None`` and the raise was Pydantic
    validation at the ``SystemMessage`` construction. Task 5.3 widened
    ``_ai_story`` to ``tuple[str, bool] | None`` so the clipped flag could
    cross out of the clamp, so ``17`` now raises ``TypeError`` at the
    ``text, clipped = told`` unpack ONE LINE EARLIER. Same catch, same empty
    update, a different raise — and the property this test asserts is
    unchanged, which is why the assertion below did not move.

    **WHAT IT NO LONGER COVERS, measured rather than reasoned — the first
    reasoned answer was wrong.** Under the old contract every non-``str``
    return raised. Under the tuple contract two layers still catch: a return
    that will not unpack into two raises at the unpack (``17``, ``object()``,
    any string but a two-character one), and a two-element return whose first
    element is not valid ``content`` still raises Pydantic validation below
    (``[1, 2]`` yields ``content=1`` and is caught). What slips through is
    exactly a two-element return whose FIRST element is a valid ``content``:
    ``{"a": 1, "b": 2}`` and the old contract's own ``"ab"`` both produce a
    story message with ``content="a"``. ``17`` is a non-iterable and so still
    exercises the guard; do not "strengthen" this test by swapping it for a
    dict or a two-char string without reading ``moderator_story``'s unpack
    comment first, which records why no guard was added for that case.

    "The node never raises" is a property the graph and the UI both depend on:
    ``GraphiaApp._record_career`` sits inside ``_drive``'s ``try`` after
    ``await drive_graph(...)``, so a raising story node would drop the UI-side
    career fold and show an error banner where the ending should be. Slice 5's
    flag PLUMBING (the unpack and the conditional ``additional_kwargs`` entry)
    landed beside that line, so the lines this catch covers did grow — though
    the clamp that COMPUTES the flag went inside ``_ai_story``, under the inner
    layer, which is the half the original prediction got wrong.

    ``fake_large`` is installed even though the model is never reached, so a
    regression that stops honouring the monkeypatched helper cannot fall
    through to ``safe_llm``'s loud double and pass for the wrong reason.

    **THE STUB RECORDS ITS KWARGS, AND THAT IS AN ANTI-VACUITY GUARD ADDED BY
    TASK 3.5.** This stub was ``lambda state: 17`` until Task 3.5 gave
    ``moderator_story`` two size-governor keyword arguments that it FORWARDS to
    ``_ai_story``. A one-parameter stub then raises ``TypeError`` on arity, the
    outer catch absorbs that exactly as happily as the Pydantic validation this
    test is about, and the assertion below stays green while measuring nothing —
    the precise trap this file's own docstring warns about for the model queue.
    So the stub accepts the governors and the test asserts they arrived, which
    fails loudly if the forwarding is renamed or dropped instead of degrading
    into a false pass.
    """
    fake_large(stories=[SCRIPTED_STORY])
    forwarded: dict[str, object] = {}

    def _contract_breaker(state: object, **kwargs: object) -> int:
        forwarded.update(kwargs)
        return 17

    monkeypatch.setattr("graphia.nodes.endgame._ai_story", _contract_breaker)

    assert moderator_story(DIRECT_STATE) == {}, (
        "a helper that breaks its tuple[str, bool] | None contract must be "
        "absorbed into an empty update; the node must never raise"
    )
    assert forwarded == {
        "story_context_window": _DEFAULT_STORY_CONTEXT_WINDOW,
        "story_token_budget": _DEFAULT_STORY_TOKEN_BUDGET,
    }, (
        "the stub was not reached with the two governors, so the empty update "
        f"above may be an arity TypeError rather than the Pydantic validation "
        f"this test exists for; got {forwarded!r}"
    )


# --------------------------------------------------------------------------
# The ledger-corruption guard
# --------------------------------------------------------------------------


def test_the_story_is_excluded_from_the_repetition_pool(
    env: Path,
    fake_large: Callable[..., Any],
) -> None:
    """``blunder_eval``'s AI-speech extractors must not see the story.

    THE FAILURE THIS GUARDS IS A CORRUPTED, APPEND-ONLY QUALITY LEDGER, which
    is why it is a committed test rather than a note. ``_ai_lines_with_names``
    and ``_ai_lines_with_speakers`` both admit a message only when
    ``isinstance(m, AIMessage)`` **and** its ``name`` is a non-human player's,
    and they feed ``score_repetition`` / ``score_third_person_self_talk``. A
    story emitted as an ``AIMessage`` named after a player would drop several
    hundred words of Moderator prose into a pool of one-line Day speeches,
    move a measured rate, and be written to ``evals/blunder-ledger.yaml`` —
    where nothing can be taken back out.

    The message is built by the real ``moderator_story`` rather than
    hand-authored, so the shape under test is the shape production emits.

    THE CONTROL IS WHAT MAKES THIS TEST NON-VACUOUS, and it is the assertion to
    read first. An exclusion assertion passes for any number of uninteresting
    reasons — a typo in the extractor's name, an empty message list, a
    ``players`` map with no AI in it. So the SAME TEXT is re-offered as an
    ``AIMessage`` named after an AI player and must be pooled: that proves the
    extractors were reachable, the roster was right, and the exclusion is
    caused by the story's TYPE and missing ``name`` and by nothing else.
    """
    fake_large(stories=[SCRIPTED_STORY])
    result = moderator_story(DIRECT_STATE)
    assert "messages" in result, (
        "no story was told, so there is nothing to guard the pool against — "
        "check that fake_large still re-points "
        "graphia.nodes.endgame.get_large"
    )
    story = result["messages"][0]

    excluded_state = {"players": DIRECT_PLAYERS, "messages": [story]}
    lines, ai_names = _ai_lines_with_names(excluded_state)
    assert lines == [], (
        "the closing story must not enter the repetition pool; "
        f"_ai_lines_with_names returned {lines!r}"
    )
    assert _ai_lines_with_speakers(excluded_state) == [], (
        "the closing story must not enter the third-person self-talk pool "
        "either — same predicate, same corruption"
    )
    assert "Ivy" in ai_names and HUMAN_NAME not in ai_names, (
        "sanity: the roster this test offered really does hold an AI player, "
        f"so an admissible message would have been found. Names: {ai_names!r}"
    )

    # THE CONTROL. Same bytes, wrong type and wrong name — and it IS pooled.
    named_ai_story = AIMessage(content=story.content, name="Ivy")
    mutant_state = {"players": DIRECT_PLAYERS, "messages": [named_ai_story]}
    mutant_lines, _ = _ai_lines_with_names(mutant_state)
    assert mutant_lines == [story.content], (
        "control failed: an AIMessage named after a player carrying the very "
        "same text must be pooled, or the exclusion above proves nothing"
    )
    assert _ai_lines_with_speakers(mutant_state) == [("Ivy", story.content)], (
        "control failed for the speaker-paired extractor"
    )
