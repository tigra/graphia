"""Spec 040 (The Moderator's Closing Story), Task 1.6 — the three arrival paths.

WHAT THIS FILE PINS, AND WHY IT IS THREE TESTS FOR A ONE-LINE CHANGE.

``end_screen`` is reached from three places — ``route_after_win_night``,
``route_after_win_day``, and the spec-023 (Game Length Runaway Safeguard)
short-circuit in ``route_after_night_open`` — but it has exactly ONE outgoing
edge. Re-pointing that edge at ``moderator_story`` therefore covers all three
arrival paths *by construction*: spec 040 §2.1's "it looks like three changes
and is one".

So the risk this file guards is NOT that the edge was missed — one assertion
would catch that. It is that a router changes later: a fourth ending, or an
existing router re-pointed at some new terminal node, would reach END without
ever passing through the story and nothing in a single edge assertion would
notice. Each test below therefore drives a REAL game to one of the three
endings and asserts the recorded super-step sequence closes with
``end_screen`` immediately followed by ``moderator_story``, then END.

SCOPE — this file is deliberately about ROUTING, not about the node. It does
not assert the story message's type, its ``name``, its kwarg, its absence on
failure, or ``calls_by_schema``; those are the node's own contract and belong
to Task 1.8's suite. The one non-routing assertion here is the career-event
ORDERING in the night-win test, and it is here because it is the property that
justifies the topology: §2.1 reason 2 says a separate node commits and
checkpoints ``KIND_GAME_ENDED`` a super-step BEFORE the story's 5-15s
non-deterministic call, and a fold would stand that call between "the winner
is known" and "the career event is emitted". That ordering is a fact about the
edge, so it is tested beside the edge.

WHY THAT ASSERTION LIVES ON THE NIGHT-WIN PATH AND NOT THE RUNAWAY ONE — the
first draft of this file put it on the runaway test (the cheapest game) and it
failed, for a reason worth recording rather than working around. A Day-cap
runaway emits NO ``game_ended`` event at all: ``endgame._OUTCOME_BY_WINNER``
deliberately omits ``"runaway"`` (spec 023 — Game Length Runaway Safeguard, on
the grounds that a cap-hit is not a legitimate result), so ``end_screen``
computes ``outcome=None`` and skips the emit. That is pre-existing and correct,
and ``moderator_story`` does not change it — but "the career event still lands
after the story node is inserted" is only a meaningful claim on the two paths
that produce a real outcome. The runaway test asserts the absence instead, so
the next reader does not have to rediscover it.

EACH SCENARIO IS THE SHORTEST GAME THAT REACHES ITS ROUTER, tuned through the
lineup and the Day cap rather than through long scripted trajectories:

* ``route_after_win_night`` — one Mafioso, two Citizens (human included). Any
  Night-1 kill leaves 1 Mafia vs 1 Law-abiding, which is parity, so
  ``check_win_night`` fires before a single Day runs.
* ``route_after_win_day`` — one Mafioso, three Citizens. Night 1 takes a
  Citizen without reaching parity, then Day 1 executes the sole Mafioso and
  ``check_win_day`` sees no Mafia alive.
* ``route_after_night_open`` — ``GRAPHIA_MAX_DAYS=1``, so the very first
  ``night_open`` sees ``cycle >= max_days`` and declares ``winner="runaway"``
  before the pointing loop is ever entered. No Day, no Night kill, no vote.

All model calls go through ``fake_large`` / ``fake_small``; no test here
touches real Bedrock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.types import Command

from graphia.career_events import KIND_GAME_ENDED, CareerEvent
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Diary, Pointing, Story

HUMAN_NAME = "Alice"
NAME_POOL = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav", "Noor", "Kai"]

SCRIPTED_STORY = Story(
    text="The baker had been counting the wrong things all along."
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class _CapturingEmitter:
    """Career-event sink that keeps every emission in order.

    Satisfies the :class:`graphia.career_events.CareerEventEmitter` protocol.
    The night-win test uses it to pin the ORDER of the ``game_ended`` emit
    against the story call; the runaway test uses it to pin that no such
    event is folded there at all. See the module docstring for both.
    """

    def __init__(self) -> None:
        self.emissions: list[tuple[str, CareerEvent]] = []

    def emit(self, session_id: str, event: CareerEvent) -> None:
        self.emissions.append((session_id, event))


def _stream(graph, run_config, payload, seen: list[str]) -> None:
    """Stream one stretch of the graph, recording the node of every update.

    ``stream_mode="updates"`` keys each chunk by the node that produced it,
    which is what makes the arrival path directly observable. LangGraph's
    own dunder channels (``__interrupt__``) are dropped — they are not nodes.

    ``recursion_limit`` is capped so a mis-wired router surfaces as a fast
    failure rather than a long spin, matching ``test_slice8_endgame._drive``.
    """
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 200)
    for chunk in graph.stream(payload, bounded, stream_mode="updates"):
        seen.extend(key for key in chunk if not key.startswith("__"))


def _pending_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _run_to_end(
    graph,
    run_config,
    *,
    responder: Callable[[dict[str, Any]], Any],
    budget: int = 200,
) -> list[str]:
    """Drive the graph from START to END, answering interrupts as they arrive.

    Returns the ordered list of nodes that produced an update, which is the
    trajectory the arrival-path assertions read.
    """
    seen: list[str] = []
    _stream(graph, run_config, {"messages": []}, seen)
    for _ in range(budget):
        if not graph.get_state(run_config).next:
            return seen
        interrupt_value = _pending_interrupt(graph, run_config)
        if interrupt_value is None:
            _stream(graph, run_config, None, seen)
            continue
        _stream(graph, run_config, Command(resume=responder(interrupt_value)), seen)
    raise AssertionError(
        f"graph did not reach END within {budget} steps; trajectory so far: {seen}"
    )


def _passive_human(interrupt_value: dict[str, Any]) -> Any:
    """Answer every interrupt kind the way a human who never leads would.

    The human names themselves, passes on every speaking turn, votes yes on
    whatever the AIs propose, and (should they ever be dealt Mafia) points at
    the first offered option. None of these choices carries a routing meaning
    — the lineup and the Day cap are what steer each game to its ending.
    """
    kind = interrupt_value.get("kind")
    if kind == "name":
        return HUMAN_NAME
    if kind == "day_turn":
        return "..."
    if kind == "vote":
        return "yes"
    if kind == "point":
        options = interrupt_value.get("options") or []
        return options[0]["id"] if options else ""
    raise AssertionError(f"unexpected interrupt kind: {kind!r}")


def _assert_story_closes_the_game(graph, run_config, seen: list[str]) -> None:
    """The trajectory ends ``end_screen`` -> ``moderator_story`` -> END.

    Three claims, and each one fails a different way of breaking Task 1.6:
    the story node ran (the edge exists); it ran DIRECTLY after the facts
    (nothing was interposed, and the facts are on screen first); and the
    graph then halted (``moderator_story`` really is terminal, not a detour
    back into play).
    """
    assert "moderator_story" in seen, (
        "the arrival path reached END without running moderator_story; "
        f"trajectory: {seen}"
    )
    assert seen[-2:] == ["end_screen", "moderator_story"], (
        "expected the game to close with end_screen -> moderator_story; "
        f"trajectory tail: {seen[-6:]}"
    )
    assert graph.get_state(run_config).next == (), (
        "moderator_story must be terminal; graph still had "
        f"{graph.get_state(run_config).next!r} pending"
    )


# --------------------------------------------------------------------------
# Arrival 1 of 3: route_after_win_night
# --------------------------------------------------------------------------


def test_night_win_arrival_reaches_moderator_story(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mafia parity win at ``check_win_night`` still ends through the story.

    One Mafioso against two Citizens (the human is one of them, pinned via
    ``GRAPHIA_ROLE``): whoever the Mafia takes on Night 1, the table is left
    at 1 vs 1, which is parity. So ``check_win_night`` routes to
    ``end_screen`` and no Day ever runs — the shortest game that exercises
    this router.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    fake_small(NAME_POOL)

    emitter = _CapturingEmitter()
    config = load_config()
    graph, thread_id = build_graph(config, career_emitter=emitter)
    run_config = make_run_config(thread_id)

    fake = fake_large(stories=[SCRIPTED_STORY])

    # The Night target cannot be pre-scripted: the real ids only exist once
    # ``assign_roles`` has run on graph state. Resolve at invoke time, the
    # same live-dispatcher trick ``test_slice8_endgame`` uses.
    #
    # The ``Story`` branch snapshots the emitter as the model is asked, which
    # is what turns "the career event landed" into "the career event landed
    # FIRST" — see the module docstring.
    emissions_when_story_asked: list[list[str]] = []
    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            players = graph.get_state(run_config).values.get("players", {})
            targets = [
                p.id
                for p in players.values()
                if p.is_alive and p.role == "law_abiding"
            ]
            return Pointing(target_id=targets[0] if targets else "missing")
        if schema is Story:
            emissions_when_story_asked.append(
                [event.kind for _, event in emitter.emissions]
            )
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    seen = _run_to_end(graph, run_config, responder=_passive_human)

    assert graph.get_state(run_config).values.get("winner") == "mafia", (
        "scenario did not reach the intended night-side win; trajectory: "
        f"{seen}"
    )
    # The router under test really is the one that fired: the game never
    # reached a Day, so ``check_win_day`` cannot be what routed here.
    assert "check_win_night" in seen and "check_win_day" not in seen, (
        f"expected a night-side arrival only; trajectory: {seen}"
    )
    _assert_story_closes_the_game(graph, run_config, seen)

    # THE PROPERTY THAT JUSTIFIES TWO NODES (§2.1 reason 2). ``end_screen``
    # emits ``KIND_GAME_ENDED`` at its own tail, so by the time the story's
    # non-deterministic call is made the career event is already committed and
    # checkpointed. A fold would stand that call between the two.
    ended = [
        event for _, event in emitter.emissions if event.kind == KIND_GAME_ENDED
    ]
    assert len(ended) == 1, (
        f"expected exactly one {KIND_GAME_ENDED} event, got "
        f"{[e.kind for _, e in emitter.emissions]}"
    )
    assert ended[0].outcome == "mafia_win", (
        f"career event carried outcome {ended[0].outcome!r}"
    )
    assert len(emissions_when_story_asked) == 1, (
        f"expected exactly one Story call, got {len(emissions_when_story_asked)}"
    )
    assert KIND_GAME_ENDED in emissions_when_story_asked[0], (
        "the career event must be committed a super-step before the story "
        "call, per spec 040 §2.1; the emitter held "
        f"{emissions_when_story_asked[0]!r} when Story was requested"
    )


# --------------------------------------------------------------------------
# Arrival 2 of 3: route_after_win_day
# --------------------------------------------------------------------------


def test_day_win_arrival_reaches_moderator_story(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Law-abiding win at ``check_win_day`` still ends through the story.

    One Mafioso against three Citizens: Night 1 takes a Citizen and leaves
    1 vs 2, which is not parity, so the game reaches Day 1. Every AI Day
    action is a vote against the sole Mafioso and every ballot is a yes, so
    the execution lands, ``check_win_day`` finds no Mafia alive, and the
    day-side router routes to ``end_screen``.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "3")
    fake_small(NAME_POOL)

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # ``diaries=`` is scripted defensively: the Day->Night hinge fans
    # ``day_diary`` out whenever a game survives its first Day, and an
    # unscripted queue there would quietly serve the node's fallback. This
    # scenario is meant to end on Day 1, but the assertion should not be the
    # thing that depends on that.
    fake = fake_large(
        stories=[SCRIPTED_STORY],
        diaries=[Diary(entry="Day one is done and I trust no one yet.")],
    )

    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        players = graph.get_state(run_config).values.get("players", {})
        alive_ai = [p for p in players.values() if p.is_alive and not p.is_human]
        if schema is Pointing:
            targets = [p.id for p in alive_ai if p.role == "law_abiding"]
            return Pointing(target_id=targets[0] if targets else "missing")
        if schema is DayAction:
            mafia = [p.id for p in alive_ai if p.role == "mafia"]
            if mafia:
                return DayAction(kind="vote", target_id=mafia[0])
            return DayAction(kind="speak", text="(nothing to add.)")
        if schema is Ballot:
            return Ballot(yes=True)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    seen = _run_to_end(graph, run_config, responder=_passive_human)

    assert graph.get_state(run_config).values.get("winner") == "law_abiding", (
        "scenario did not reach the intended day-side win; trajectory: "
        f"{seen}"
    )
    assert "check_win_day" in seen, (
        f"expected a day-side arrival; trajectory: {seen}"
    )
    _assert_story_closes_the_game(graph, run_config, seen)


# --------------------------------------------------------------------------
# Arrival 3 of 3: route_after_night_open (spec 023 runaway short-circuit)
# --------------------------------------------------------------------------


def test_runaway_arrival_reaches_moderator_story_after_the_career_event(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec-023 runaway short-circuit still ends through the story.

    This is the least obvious of the three arrivals, because it is not a win
    check: ``night_open`` itself sets ``winner="runaway"`` when
    ``cycle >= max_days`` and ``route_after_night_open`` short-circuits Night
    setup. ``GRAPHIA_MAX_DAYS=1`` makes the FIRST ``night_open`` the one that
    trips it, so the game reaches ``end_screen`` with no Night kill, no Day
    and no vote — which is exactly why it is worth pinning separately: none
    of the state the other two routers depend on exists here.

    IT ALSO PINS THE ONE PATH ON WHICH NO CAREER EVENT IS EXPECTED, and that
    is a measured correction rather than a design choice made here. A Day-cap
    runaway folds NO career outcome: ``endgame._OUTCOME_BY_WINNER`` omits
    ``"runaway"`` on purpose (spec 023 — Game Length Runaway Safeguard), so
    ``end_screen`` computes ``outcome=None`` and skips the emit entirely. The
    career-event ORDERING that justifies the two-node topology is therefore
    pinned on the night-win path, where a real outcome exists; here the claim
    is the complementary one — inserting ``moderator_story`` did not somehow
    start emitting an event this path never emitted.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_MAX_DAYS", "1")
    fake_small(NAME_POOL)

    emitter = _CapturingEmitter()
    config = load_config()
    assert config.max_days == 1, "GRAPHIA_MAX_DAYS did not reach the config"
    graph, thread_id = build_graph(config, career_emitter=emitter)
    run_config = make_run_config(thread_id)

    fake_large(stories=[SCRIPTED_STORY])

    seen = _run_to_end(graph, run_config, responder=_passive_human)

    assert graph.get_state(run_config).values.get("winner") == "runaway", (
        f"scenario did not reach the runaway cap; trajectory: {seen}"
    )
    # The short-circuit really is what routed here: neither win check ran.
    assert "check_win_night" not in seen and "check_win_day" not in seen, (
        f"expected the night_open short-circuit, not a win check; got {seen}"
    )
    _assert_story_closes_the_game(graph, run_config, seen)

    # A runaway folds no career outcome, and adding the story node did not
    # change that. Asserted rather than assumed because the first draft of
    # this file asserted the opposite and was wrong; see the docstring.
    assert not [
        event for _, event in emitter.emissions if event.kind == KIND_GAME_ENDED
    ], (
        "a Day-cap runaway must fold no career outcome (spec 023); emitter "
        f"held {[e.kind for _, e in emitter.emissions]}"
    )
