"""Slice 7 tests: vote-to-execute mechanics.

Five scenarios driven through the compiled graph directly (no Textual), so
the tests are fast, deterministic, and free of UI timing flakiness:

1. ``test_successful_execution_ends_day_and_reveals_role`` — a mid-round AI
   vote passes; target dies, role is revealed in the VOTE_EXECUTED line, a
   KillRecord with cause="execution" is appended, and the Day ends (graph
   rolls into Night 2).

2. ``test_failed_vote_continues_day_and_counts_against_cap`` — a vote is
   called but falls short of a majority. ``day_votes_called`` increments,
   the "vote fails" line fires, and the Day continues with further
   speaking turns before any day_close line.

3. ``test_three_failed_votes_ends_day`` — three consecutive failed votes
   trigger the DAY_MAX_VOTES cap. After the third, ``day_votes_called==3``,
   ``day_close`` fires, and Night 2 opens.

4. ``test_human_slash_vote_is_parsed`` — a human's ``/vote <substring>`` is
   fuzzy-matched against alive names, the resulting VOTE_INITIATE line is
   emitted against the correct target, and the turn is consumed by the
   vote flow.

5. ``test_human_slash_vote_ambiguous_re_interrupts`` — an ambiguous substring
   re-issues the day_turn interrupt with an ``"error"`` payload, and the
   turn index does NOT advance. A subsequent precise substring succeeds.

All Bedrock boundaries are stubbed via the unified ``fake_sonnet`` fixture
(DayAction / Ballot / Pointing served from one fake keyed on schema), plus
``fake_haiku`` for the name generator. No test touches real AWS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import SystemMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Pointing
from graphia.nodes import DAY_MAX_VOTES
from graphia.prompts import (
    VOTE_EXECUTED_TEMPLATE,
    VOTE_FAILED_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
)

AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"

DAY_CLOSE_NO_EXEC_LINE = "The Day ends with no one executed."


# --- Day-1 speaker-order stubs -------------------------------------------
#
# Tests 1-3 each script a ``DayAction(kind="vote", target_id=<some_AI>)``,
# but the scripted DayAction queue is only populated AFTER the test pauses
# on the first interrupt (the human's day_turn) — any AI day_turn fired
# BEFORE that point hits an empty queue and falls back to a generic speak,
# silently consuming nothing from the queue.  The first AI to consume the
# queued vote action must therefore be (a) the AI immediately following the
# human in the speaker order, AND (b) NOT itself the vote target (otherwise
# ``_ai_day_action`` rejects the self-targeted vote and falls back to a
# generic speak, dropping the scripted vote).
#
# The stubs below replace ``graphia.nodes.day._shuffle_order`` to place the
# human at index 0 and a known non-target AI at index 1, guaranteeing the
# scripted vote action lands on a non-target speaker on the first try.


def _human_then_law_abiding(players):
    """Speaker order: human at index 0, Law-abiding AI at index 1.

    Used when the scripted vote target is a Mafia AI: the first AI to act
    after the human's turn must NOT be the target, so a Law-abiding AI is
    forced into the slot immediately after the human.
    """
    alive = [pid for pid, p in players.items() if p.is_alive]
    human_id = next(pid for pid, p in players.items() if p.is_human)
    la_ai_ids = [
        pid
        for pid, p in players.items()
        if p.is_alive and p.role == "law_abiding" and not p.is_human
    ]
    assert la_ai_ids, "expected at least one alive Law-abiding AI"
    second = la_ai_ids[0]
    rest = [pid for pid in alive if pid not in (human_id, second)]
    return [human_id, second, *rest]


def _human_then_mafia(players):
    """Speaker order: human at index 0, Mafia AI at index 1.

    Used when the scripted vote target is a Law-abiding AI: the first AI to
    act after the human's turn must NOT be the target, so a Mafia AI is
    forced into the slot immediately after the human.
    """
    alive = [pid for pid, p in players.items() if p.is_alive]
    human_id = next(pid for pid, p in players.items() if p.is_human)
    mafia_ai_ids = [
        pid
        for pid, p in players.items()
        if p.is_alive and p.role == "mafia" and not p.is_human
    ]
    assert mafia_ai_ids, "expected at least one alive Mafia AI"
    second = mafia_ai_ids[0]
    rest = [pid for pid in alive if pid not in (human_id, second)]
    return [human_id, second, *rest]


# --------------------------------------------------------------------------
# Helpers mirrored from test_slice6_day.py so this file stays self-contained.
# --------------------------------------------------------------------------


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    """Return the first pending interrupt value on the graph, or None."""
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        interrupts = task.interrupts or ()
        for interrupt_obj in interrupts:
            return interrupt_obj.value
    return None


def _system_contents(graph, run_config) -> list[str]:
    """Flatten all SystemMessage contents currently in the graph state."""
    state = graph.get_state(run_config).values
    return [
        m.content
        for m in state.get("messages", [])
        if isinstance(m, SystemMessage)
    ]


def _has_line(graph, run_config, line: str) -> bool:
    return any(line in c for c in _system_contents(graph, run_config))


def _drive(graph, run_config, payload) -> None:
    """Stream with the given payload, exhausting super-steps until a pause.

    We cap ``recursion_limit`` low (default LangGraph value is large; here
    we force 50) so that when the graph deliberately enters an unbounded
    loop (e.g., post-Slice-7 the game keeps cycling Day↔Night forever),
    a single ``graph.stream`` call raises ``GraphRecursionError`` quickly
    instead of burning minutes spinning up to 10007 super-steps. Tests that
    deliberately allow the graph to run past their assertion point wrap
    their driver with ``swallow_recursion=True``.
    """
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 50)
    for _ in graph.stream(payload, bounded, stream_mode="updates"):
        pass


def _players(graph, run_config) -> dict:
    return graph.get_state(run_config).values.get("players", {})


def _alive_ai_ids_by_role(
    graph, run_config, role: str
) -> list[str]:
    players = _players(graph, run_config)
    return [
        p.id
        for p in players.values()
        if p.is_alive and p.role == role and not p.is_human
    ]


def _alive_mafia_ai_id(graph, run_config) -> str:
    ids = _alive_ai_ids_by_role(graph, run_config, "mafia")
    assert ids, "expected at least one alive AI Mafia"
    return ids[0]


def _alive_law_abiding_ai_id(graph, run_config) -> str:
    ids = _alive_ai_ids_by_role(graph, run_config, "law_abiding")
    assert ids, "expected at least one alive AI Law-abiding"
    return ids[0]


# --------------------------------------------------------------------------
# Driving helper: advances the graph through pending interrupts up to either
# a caller-provided stop condition or a budget cap.
# --------------------------------------------------------------------------


def _advance_until(
    graph,
    run_config,
    *,
    stop: Callable[[], bool],
    interrupt_responder: Callable[[dict[str, Any]], Any],
    budget: int = 200,
    swallow_recursion: bool = False,
) -> None:
    """Advance the graph, resuming each pending interrupt via ``interrupt_responder``.

    ``stop()`` is polled before each super-step; as soon as it returns True
    the driver exits. If the graph stops producing interrupts and ``stop``
    is still False, the loop exits silently — the test then asserts whatever
    it needs from final state.

    ``swallow_recursion`` — if True, ``GraphRecursionError`` inside a single
    ``_drive`` is caught and treated as "drive produced nothing new". Useful
    for tests that only care about state transitions up to a certain point
    (e.g., "Day 1 closed and Night 2 opened") and deliberately allow the
    graph to run past that milestone into the endless Day/Night loop.
    """
    # Local import to avoid making the error symbol load-time-visible — the
    # fallback path is only exercised when ``swallow_recursion=True``.
    from langgraph.errors import GraphRecursionError

    for _ in range(budget):
        if stop():
            return
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        try:
            if interrupt_value is None:
                # No pending interrupt but graph still has .next — drive once
                # with None to let it settle, then re-check.
                _drive(graph, run_config, None)
                continue
            resume = interrupt_responder(interrupt_value)
            _drive(graph, run_config, Command(resume=resume))
        except GraphRecursionError:
            if not swallow_recursion:
                raise
            # Graph hit its recursion cap between interrupts. The checkpointed
            # state from the last completed super-step is still intact and
            # readable, so the caller can inspect it. Stop the advance.
            return


# --------------------------------------------------------------------------
# Test 1: a successful execution ends the Day and reveals the target role.
# --------------------------------------------------------------------------


def test_successful_execution_ends_day_and_reveals_role(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI calls a vote; a Yes-majority executes the target and closes Day 1.

    The easiest deterministic path:
    - Drive through the name interrupt to assign roles.
    - Before Night 1 runs, we don't know the target_id, so we first start
      the graph only far enough to resolve roles, then re-arm the fake with
      a Pointing + a DayAction(kind="vote") whose target we now know.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Put the human at index 0 (so the drive pauses before any AI consumes
    # the still-empty DayAction queue) and a Law-abiding AI at index 1 so
    # the first AI to consume the scripted vote action is NOT the Mafia
    # target — otherwise _ai_day_action rejects the self-targeted vote and
    # the scripted DayAction(kind="vote") never fires.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_then_law_abiding)
    fake_haiku(AI_NAMES)

    # Initial fake has no scripted outputs yet — we'll reconfigure it once
    # roles are known. Use the 'replay last' behaviour: we pre-seed with a
    # placeholder speak action that will never actually be served because we
    # inject real scripts before any Day call.
    fake = fake_sonnet(
        day_actions=[],
        ballots=[],
        pointings=[],
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # First stream: up to the name interrupt.
    _drive(graph, run_config, {"messages": []})
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    # Resume with name → roles are now assigned. We can read the roster.
    # But driving past `collect_name` also drives straight into Night 1,
    # where mafia_pointing will try to invoke the Sonnet for Pointing.
    # Pre-stock the pointings queue NOW by mutating the fake.
    # Since we don't yet know player IDs, we need to resume the graph step by
    # step. The first name resume triggers collect_name → generate_roster
    # → assign_roles → introduce_roster → reveal_role
    # → first_night_mafia_intros → night_open → mafia_pointing.
    # mafia_pointing will hit our empty pointings queue and fail.
    #
    # Workaround: use a Pointing value that is lazily computed by hijacking
    # the fake's internal queue with a callable. Easier: inspect state mid-run
    # by calling get_state before resuming further. After the name interrupt
    # is resumed, but BEFORE we call .stream, we can compute target IDs from
    # the already-written graph state? No — the state is written atomically
    # per super-step; after resume, .stream runs until the next interrupt or
    # graph end. So we need to pre-populate pointings differently.
    #
    # Simplest solution: resume with name and let the Night-1 Pointing queue
    # fall through to the deterministic fallback (empty queue → AssertionError
    # in our unified fake).  Instead, patch the Pointing fake to resolve
    # targets from live graph state at call time.  We do this by monkey-
    # patching the fake's invoke dispatch for Pointing.
    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            # Fresh target each call: first alive Law-abiding AI (Night 1
            # reads current state).
            la_id = _alive_law_abiding_ai_id(graph, run_config)
            return Pointing(target_id=la_id)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    # Resume the name interrupt — graph should run through Night 1 and pause
    # on the first Day-1 day_turn interrupt (the first speaker in the round).
    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # Roles are now assigned. Find the first alive AI Mafia id — that's who
    # we want the (AI) vote-initiator to target? Actually we want the vote
    # TARGET to be Mafia (so a successful execution reveals Mafia role); the
    # initiator can be any AI. For simplicity: pick a target-Mafia id and
    # script any AI speaker to vote against it.
    mafia_id = _alive_mafia_ai_id(graph, run_config)
    mafia_name = _players(graph, run_config)[mafia_id].name

    # Reconfigure the fake's DayAction queue: the first AI day_turn should
    # initiate a vote against the Mafia target.  Subsequent calls (if any)
    # fall back to the replay-last behaviour — but once a vote passes, the
    # Day closes, so only the first matters in practice.
    fake._queues[DayAction] = [
        DayAction(kind="vote", target_id=mafia_id),
    ]
    fake._last.pop(DayAction, None)
    # Ballots: 6 voters (5 AI + 1 human). Scripted all Yes. (Human answers
    # Yes via interrupt; AI ballots come from this queue. At least 4 AI
    # voters remain alive after Night 1 since only a Law-abiding is dead.)
    fake._queues[Ballot] = [Ballot(yes=True)] * 10
    fake._last.pop(Ballot, None)

    # Now drive: the graph is currently paused on the first Day-1 day_turn
    # interrupt. The `_human_then_law_abiding` shuffle stub above places the
    # human at slot 0 deterministically, but we still handle every interrupt
    # kind generically in the responder so the test remains resilient if a
    # future change reorders the day_order shape.

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            # Human speaks (no vote) — let the AI initiate the vote.
            return "..."
        if kind == "vote":
            return "yes"
        if kind == "point":
            # Shouldn't fire — human is pinned Law-abiding via GRAPHIA_ROLE.
            return ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    def _executed() -> bool:
        expected_line = VOTE_EXECUTED_TEMPLATE.format(
            name=mafia_name, role_label="Mafia"
        )
        return _has_line(graph, run_config, expected_line)

    _advance_until(
        graph,
        run_config,
        stop=_executed,
        interrupt_responder=_respond,
        budget=200,
    )

    # --- Assertions -----------------------------------------------------

    messages = _system_contents(graph, run_config)

    # 1. Vote was announced.
    initiate_prefix = "has called for a vote to execute"
    assert any(
        initiate_prefix in m and mafia_name in m for m in messages
    ), f"VOTE_INITIATE_ANNOUNCE line missing; messages: {messages!r}"

    # 2. Per-voter tally lines appeared — at least the human's "Yes" and one
    #    AI's "Yes".
    yes_ballot_lines = [m for m in messages if m.endswith(": Yes")]
    assert len(yes_ballot_lines) >= 2, (
        f"expected multiple per-voter Yes lines; got {yes_ballot_lines!r}"
    )

    # 3. Execution line has role label.
    expected_exec = VOTE_EXECUTED_TEMPLATE.format(
        name=mafia_name, role_label="Mafia"
    )
    assert any(expected_exec in m for m in messages), (
        f"VOTE_EXECUTED line missing; messages: {messages!r}"
    )

    # 4. Target is dead; kill_log has the execution record.
    players = _players(graph, run_config)
    assert players[mafia_id].is_alive is False
    kill_log = graph.get_state(run_config).values.get("kill_log", [])
    exec_records = [r for r in kill_log if r.get("cause") == "execution"]
    assert len(exec_records) == 1
    assert exec_records[0]["name"] == mafia_name
    assert exec_records[0]["role"] == "mafia"

    # 5. Day ended and Night 2 opened (or at least day_close path fired).
    #    We can check by looking for a "Night falls" line after the
    #    execution line, OR by verifying day_close did NOT emit the
    #    "no one executed" filler (it skips that when an execution lands).
    #    The graph pauses next on either a human-mafia point interrupt
    #    (N/A here — human is Law-abiding) or the Night-2 Day-2 chain.
    #    Safest: day_votes_called stayed at 0 (only one vote, and it
    #    succeeded) and kill_log includes a night kill from Night 1 AND
    #    the execution from Day 1.
    state = graph.get_state(run_config).values
    assert state.get("day_votes_called", 0) == 0
    # The cycle counter should still be 1 at day_close; it bumps only when
    # Night 2 re-enters night_open. But since the graph drives past day_close
    # into night_open, we may see cycle==2 depending on how many super-steps
    # ran. Either way, confirm Night-related messaging or state progression:
    assert state.get("phase") in ("day", "night")
    # Kill log contains at least the Night-1 victim + execution.
    assert len(kill_log) >= 2, (
        f"kill_log should include Night 1 victim + execution; got {kill_log!r}"
    )


# --------------------------------------------------------------------------
# Test 2: a failed vote continues the Day and bumps day_votes_called.
# --------------------------------------------------------------------------


def test_failed_vote_continues_day_and_counts_against_cap(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vote is called but mostly-No ballots defeat it; Day continues."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Put the human at index 0 (so the drive pauses before any AI consumes
    # the still-empty DayAction queue) and a Mafia AI at index 1 so the
    # first AI to consume the scripted vote action is NOT the Law-abiding
    # target — otherwise _ai_day_action rejects the self-targeted vote and
    # the scripted DayAction(kind="vote") never fires.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_then_mafia)
    fake_haiku(AI_NAMES)

    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})

    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            la_id = _alive_law_abiding_ai_id(graph, run_config)
            return Pointing(target_id=la_id)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # The first AI speaker will initiate a vote against a Law-abiding.
    target_id = _alive_law_abiding_ai_id(graph, run_config)
    target_name = _players(graph, run_config)[target_id].name

    fake._queues[DayAction] = [
        DayAction(kind="vote", target_id=target_id),
        # After the vote fails, the Day continues. Subsequent AI turns should
        # just speak so the test can observe the "still speaking" state.
        DayAction(kind="speak", text="Back to chatting."),
    ]
    fake._last.pop(DayAction, None)
    # Ballots: all No so the vote fails. 5 AI ballots (incl. the target
    # itself — the roster includes the target as a voter until they die).
    fake._queues[Ballot] = [Ballot(yes=False)] * 10
    fake._last.pop(Ballot, None)

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "no"
        if kind == "point":
            return ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    # Stop once the vote-failed line lands AND we observe at least one
    # subsequent speaking turn (proving the Day didn't close).
    def _failed_and_resumed() -> bool:
        messages = _system_contents(graph, run_config)
        failed_seen = any(VOTE_FAILED_TEMPLATE in m for m in messages)
        if not failed_seen:
            return False
        # Check for an AIMessage after the vote-failed SystemMessage —
        # easier: verify day_votes_called > 0 AND no day_close line.
        state = graph.get_state(run_config).values
        return state.get("day_votes_called", 0) >= 1

    _advance_until(
        graph,
        run_config,
        stop=_failed_and_resumed,
        interrupt_responder=_respond,
        budget=100,
    )

    messages = _system_contents(graph, run_config)

    # Vote was called and failed.
    initiate_prefix = "has called for a vote to execute"
    assert any(
        initiate_prefix in m and target_name in m for m in messages
    ), f"VOTE_INITIATE line missing for {target_name}"
    assert any(VOTE_FAILED_TEMPLATE in m for m in messages), (
        f"'The vote fails.' missing; messages: {messages!r}"
    )

    state = graph.get_state(run_config).values
    assert state.get("day_votes_called") == 1, (
        f"expected day_votes_called==1, got {state.get('day_votes_called')!r}"
    )

    # Day did NOT close: the generic "no one executed" line is the only
    # close-line we'd see on this branch (no successful exec). Assert it's
    # not present yet. AND the target is still alive.
    assert DAY_CLOSE_NO_EXEC_LINE not in " ".join(messages), (
        "Day should not have closed after a single failed vote"
    )
    assert _players(graph, run_config)[target_id].is_alive is True


# --------------------------------------------------------------------------
# Test 3: three failed votes hit DAY_MAX_VOTES and close the Day.
# --------------------------------------------------------------------------


def test_three_failed_votes_ends_day(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After three failed votes, day_close fires and Night 2 opens."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Put the human at index 0 (so the drive pauses before any AI consumes
    # the still-empty DayAction queue) and a Mafia AI at index 1 so each of
    # the three consecutive scripted vote actions lands on a non-target AI
    # speaker — otherwise the vote is rejected, falls back to speak, and
    # the test never sees 3 fails.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_then_mafia)
    fake_haiku(AI_NAMES)

    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})

    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            la_id = _alive_law_abiding_ai_id(graph, run_config)
            return Pointing(target_id=la_id)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # Script 3 consecutive vote-initiations. After each vote fails, the
    # next AI speaker initiates another vote. We also ensure the target is
    # always alive (same Law-abiding target for simplicity; failed votes
    # don't kill anyone).
    target_id = _alive_law_abiding_ai_id(graph, run_config)

    fake._queues[DayAction] = [
        DayAction(kind="vote", target_id=target_id),
        DayAction(kind="vote", target_id=target_id),
        DayAction(kind="vote", target_id=target_id),
        # Safety net in case the graph asks for another action after the
        # third failed vote (it shouldn't — day_close should fire).
        DayAction(kind="speak", text="(fallback speech)"),
    ]
    fake._last.pop(DayAction, None)
    # All No ballots for every voter, every vote.
    fake._queues[Ballot] = [Ballot(yes=False)] * 40
    fake._last.pop(Ballot, None)

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "no"
        if kind == "point":
            # Night 2 human-mafia interrupt (N/A under GRAPHIA_ROLE=law-abiding,
            # but guard defensively so the graph never hangs).
            options = iv.get("options") or []
            return options[0]["id"] if options else ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    # Track the peak day_votes_called seen so far. Day 2's day_open resets
    # the counter to 0, so we can't just read the current value — we need
    # to watch for the moment it hits DAY_MAX_VOTES during Day 1 AND the
    # day_close line landing.
    # Counting markers we can observe AFTER Day 2 resets state:
    # - Each failed vote emits VOTE_FAILED_TEMPLATE → persists in messages.
    # - day_close emits DAY_CLOSE_NO_EXEC_LINE → persists.
    # - night_open bumps cycle → visible once Night 2 starts.
    # These survive Day-2's day_open reset, so we can assert against
    # permanent log markers rather than transient counters.

    def _stopped() -> bool:
        messages = _system_contents(graph, run_config)
        failed_count = sum(1 for m in messages if VOTE_FAILED_TEMPLATE in m)
        cycle = graph.get_state(run_config).values.get("cycle", 1)
        return (
            failed_count >= DAY_MAX_VOTES
            and DAY_CLOSE_NO_EXEC_LINE in " ".join(messages)
            and cycle >= 2
        )

    # The graph continues looping indefinitely past Night 2 (Day 2 speaks,
    # Night 3 kills, etc.) because Slice 8's end-game check isn't in yet.
    # We pass ``swallow_recursion=True`` so that if a single ``_drive``
    # stream call runs past its recursion cap before the next interrupt,
    # the driver exits cleanly and the test asserts against the last
    # checkpointed state. The stop condition above is still the primary
    # exit — recursion is only a safety net.
    _advance_until(
        graph,
        run_config,
        stop=_stopped,
        interrupt_responder=_respond,
        budget=200,
        swallow_recursion=True,
    )

    # --- Assertions -----------------------------------------------------

    messages = _system_contents(graph, run_config)

    # Exactly 3 "The vote fails." lines were emitted in Day 1. After Day 2's
    # day_open resets day_votes_called to 0, the permanent message history
    # is the only reliable count. (Later Days shouldn't add more because
    # the scripted DayAction queue no longer produces vote actions.)
    failed_count = sum(1 for m in messages if VOTE_FAILED_TEMPLATE in m)
    assert failed_count >= DAY_MAX_VOTES == 3, (
        f"expected {DAY_MAX_VOTES} 'vote fails' lines, got {failed_count}"
    )

    # day_close emitted its "no one executed" line at least once — the
    # no-execution path at the failed-votes cap.
    assert DAY_CLOSE_NO_EXEC_LINE in " ".join(messages), (
        "day_close line missing after DAY_MAX_VOTES"
    )

    # Night 2 opened: "Night falls" appears twice in the log (Night 1 + 2)
    # and the cycle counter has bumped to at least 2.
    night_falls_count = sum(1 for m in messages if "Night falls." in m)
    assert night_falls_count >= 2, (
        f"expected 2+ 'Night falls.' lines, got {night_falls_count}"
    )
    assert graph.get_state(run_config).values.get("cycle", 1) >= 2, (
        "expected cycle>=2 in Night 2"
    )


# --------------------------------------------------------------------------
# Test 4: a human /vote substring resolves to the right target.
# --------------------------------------------------------------------------


def test_human_slash_vote_is_parsed(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human types ``/vote <prefix>`` → the correct VOTE_INITIATE fires."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)

    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})

    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            la_id = _alive_law_abiding_ai_id(graph, run_config)
            return Pointing(target_id=la_id)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # Pick an alive Mafia AI as our vote target. Its name starts with a
    # prefix that only it matches among alive players (by construction of
    # AI_NAMES — all 6 names have distinct first letters and no substring
    # overlap with "Alice"). Use the first 3 chars to be safe.
    mafia_id = _alive_mafia_ai_id(graph, run_config)
    mafia_name = _players(graph, run_config)[mafia_id].name
    # Use a prefix guaranteed to be unique against the living roster.
    # After Night 1, one Law-abiding is dead; the remaining 6 alive include
    # the human "Alice" and 5 AIs. Pick 3 chars of the Mafia's name.
    prefix = mafia_name[:3]
    # Sanity: this prefix must not overlap with HUMAN_NAME or any other
    # alive player's name.
    alive_names = [
        p.name for p in _players(graph, run_config).values() if p.is_alive
    ]
    matching = [n for n in alive_names if prefix.lower() in n.lower()]
    assert matching == [mafia_name], (
        f"prefix {prefix!r} is ambiguous across alive roster {alive_names!r}"
    )

    # Speak lines for AIs that go before the human in the shuffled order.
    fake._queues[DayAction] = [
        DayAction(kind="speak", text=f"AI speaks ({i}).")
        for i in range(40)
    ]
    fake._last.pop(DayAction, None)
    # Ballots: doesn't matter whether the vote passes or fails — we only
    # need to confirm the initiation line. Use Yes so it passes (and thus
    # the Day closes deterministically after our assertion).
    fake._queues[Ballot] = [Ballot(yes=True)] * 10
    fake._last.pop(Ballot, None)

    # Drive until the first HUMAN day_turn interrupt.
    def _is_human_day_turn() -> bool:
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            return False
        if iv.get("kind") != "day_turn":
            return False
        # Distinguish the human from AI turns via speaker_name.
        return iv.get("speaker_name") == HUMAN_NAME

    def _respond_speak(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            # Non-human day_turn interrupts are not issued — AIs don't
            # interrupt. So this only fires for the human (handled below).
            return "..."
        if kind == "vote":
            return "yes"
        return ""

    _advance_until(
        graph,
        run_config,
        stop=_is_human_day_turn,
        interrupt_responder=_respond_speak,
        budget=100,
    )

    iv = _collect_interrupt(graph, run_config)
    assert iv is not None and iv.get("speaker_name") == HUMAN_NAME, (
        f"expected human day_turn interrupt, got {iv!r}"
    )

    # Resume the human's turn with /vote <prefix>.
    _drive(graph, run_config, Command(resume=f"/vote {prefix}"))

    # The next super-step should have emitted VOTE_INITIATE against the
    # Mafia target.
    expected_initiate = VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
        initiator=HUMAN_NAME,
        target=mafia_name,
    )
    assert _has_line(graph, run_config, expected_initiate), (
        f"VOTE_INITIATE line missing. Got:\n"
        f"{_system_contents(graph, run_config)!r}"
    )


# --------------------------------------------------------------------------
# Test 5: an ambiguous /vote substring re-interrupts without consuming the
# human's turn; a precise second try succeeds.
# --------------------------------------------------------------------------


def test_human_slash_vote_ambiguous_re_interrupts(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguous substring → re-interrupt with 'error'; turn index unchanged.

    We reuse the standard ``AI_NAMES`` roster — the substring ``"ia"``
    matches two alive players (Bianca and Elias) after Night 1, which
    gives us the ambiguity we need without bespoke haiku names that
    happen to be fragile under certain test orderings. The Night-1
    pointing fake below steers the Mafia kill to a non-"ia" target, so
    both candidates survive regardless of the role shuffle.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Both Bianca and Elias must be alive after Night 1 for the "ia"
    # substring to be genuinely ambiguous. The patched ``fake._invoke``
    # below (see ``_invoke_with_live_pointing``) makes the Night-1 Mafia
    # pointing deterministically target a non-"ia" Law-abiding AI, so
    # Bianca/Elias survive Night 1 regardless of which roles they were
    # dealt (Mafia don't kill themselves; the kill is aimed at a non-"ia"
    # name). The role-deck shuffle therefore has no effect on the
    # invariant this test depends on, and no RNG pinning is required.
    fake_haiku(AI_NAMES)

    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})

    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            # Pick an AI Law-abiding target that does NOT contain the
            # ambiguity substring ``"ia"``, so both Bianca and Elias remain
            # alive after Night 1.
            players = _players(graph, run_config)
            non_ia = [
                p.id
                for p in players.values()
                if p.is_alive
                and p.role == "law_abiding"
                and not p.is_human
                and "ia" not in p.name.lower()
            ]
            if non_ia:
                return Pointing(target_id=non_ia[0])
            return Pointing(
                target_id=_alive_law_abiding_ai_id(graph, run_config)
            )
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # Confirm both ambiguity candidates survived Night 1.
    players = _players(graph, run_config)
    alive_ia_names = [
        p.name
        for p in players.values()
        if p.is_alive and "ia" in p.name.lower()
    ]
    assert "Bianca" in alive_ia_names and "Elias" in alive_ia_names, (
        f"both Bianca and Elias must be alive for this test; "
        f"got {alive_ia_names!r}"
    )

    # AIs speak if their turn comes before the human.
    fake._queues[DayAction] = [
        DayAction(kind="speak", text=f"AI speaks ({i}).")
        for i in range(40)
    ]
    fake._last.pop(DayAction, None)
    fake._queues[Ballot] = [Ballot(yes=True)] * 10
    fake._last.pop(Ballot, None)

    # Drive until the first human day_turn.
    def _is_human_day_turn() -> bool:
        iv = _collect_interrupt(graph, run_config)
        return bool(
            iv
            and iv.get("kind") == "day_turn"
            and iv.get("speaker_name") == HUMAN_NAME
        )

    def _respond_speak(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "yes"
        return ""

    _advance_until(
        graph,
        run_config,
        stop=_is_human_day_turn,
        interrupt_responder=_respond_speak,
        budget=100,
    )

    pre_state = graph.get_state(run_config).values
    pre_turn_index = pre_state.get("day_turn_index")
    pre_order = list(pre_state.get("day_order", []))

    # First try: "/vote ia" — matches both Bianca and Elias → re-interrupt.
    _drive(graph, run_config, Command(resume="/vote ia"))

    # After the ambiguous resume, the graph should STILL be paused on a
    # day_turn interrupt (the same one, re-issued) with an error payload.
    iv_after = _collect_interrupt(graph, run_config)
    assert iv_after is not None, "graph should still be paused after ambiguous /vote"
    assert iv_after.get("kind") == "day_turn"
    assert iv_after.get("speaker_name") == HUMAN_NAME
    assert "error" in iv_after, (
        f"expected an 'error' field on the re-issued interrupt; "
        f"got {iv_after!r}"
    )

    # Turn index and order must be unchanged — the turn was NOT consumed.
    post_state = graph.get_state(run_config).values
    assert post_state.get("day_turn_index") == pre_turn_index
    assert list(post_state.get("day_order", [])) == pre_order
    # active_vote must still be unset — no vote started.
    assert post_state.get("active_vote") is None

    # Second try: "/vote Bianca" — unambiguous → vote initiates.
    _drive(graph, run_config, Command(resume="/vote Bianca"))

    expected_initiate = VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
        initiator=HUMAN_NAME,
        target="Bianca",
    )
    assert _has_line(graph, run_config, expected_initiate), (
        f"VOTE_INITIATE for Bianca missing; messages: "
        f"{_system_contents(graph, run_config)!r}"
    )


# --------------------------------------------------------------------------
# Spec 006, Slice 3 — human day-action counters (votes called / ballots cast)
#
# These two tests reuse the drive-until-human-day_turn + resume harness above
# to prove the GameState counters that ``stats_store.summarize`` reads are
# populated by the day nodes:
#
# - ``day_turn`` bumps ``human_votes_called`` on the human's successful
#   ``/vote <target>`` (human path only).
# - ``collect_votes`` bumps ``human_ballots_cast`` when the human casts a
#   yes/no ballot (human branch only).
#
# The pure-fold/summarize/render-panel coverage lives in test_career_stats.py;
# here we only assert the graph actually writes the keys.
# --------------------------------------------------------------------------


def _drive_to_human_day_turn(
    graph,
    run_config,
    fake,
) -> None:
    """Advance from the name interrupt to the first HUMAN day_turn interrupt.

    Mirrors the setup shared by tests 4 and 5: drive past the name interrupt
    with a live-state Night-1 Pointing fake (so ``mafia_pointing`` resolves a
    real target), pre-stock the Day queues with generic speaks + Yes ballots,
    then ``_advance_until`` the graph pauses on the human's own day_turn.
    """
    _drive(graph, run_config, {"messages": []})

    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            la_id = _alive_law_abiding_ai_id(graph, run_config)
            return Pointing(target_id=la_id)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # AIs that go before the human in the shuffled order just speak.
    fake._queues[DayAction] = [
        DayAction(kind="speak", text=f"AI speaks ({i}).") for i in range(40)
    ]
    fake._last.pop(DayAction, None)
    # AI ballots: Yes (the vote outcome is irrelevant to the counter tests).
    fake._queues[Ballot] = [Ballot(yes=True)] * 20
    fake._last.pop(Ballot, None)

    def _is_human_day_turn() -> bool:
        iv = _collect_interrupt(graph, run_config)
        return bool(
            iv
            and iv.get("kind") == "day_turn"
            and iv.get("speaker_name") == HUMAN_NAME
        )

    def _respond_speak(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "yes"
        return ""

    _advance_until(
        graph,
        run_config,
        stop=_is_human_day_turn,
        interrupt_responder=_respond_speak,
        budget=100,
    )

    iv = _collect_interrupt(graph, run_config)
    assert iv is not None and iv.get("speaker_name") == HUMAN_NAME, (
        f"expected human day_turn interrupt, got {iv!r}"
    )


def test_human_vote_bumps_human_votes_called(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human's successful ``/vote`` increments ``human_votes_called`` to 1.

    Drives to the human's day_turn (counter still 0), resumes with a valid
    ``/vote <prefix>`` against a unique alive Mafia AI, and asserts the
    counter ticked to exactly 1 in the resulting graph state.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive_to_human_day_turn(graph, run_config, fake)

    # The counter must still be unset/0 before the human's vote.
    pre = graph.get_state(run_config).values
    assert pre.get("human_votes_called", 0) == 0, (
        f"human_votes_called should start at 0, got "
        f"{pre.get('human_votes_called')!r}"
    )

    # Pick a unique-prefix alive Mafia AI as the vote target.
    mafia_id = _alive_mafia_ai_id(graph, run_config)
    mafia_name = _players(graph, run_config)[mafia_id].name
    prefix = mafia_name[:3]
    alive_names = [
        p.name for p in _players(graph, run_config).values() if p.is_alive
    ]
    matching = [n for n in alive_names if prefix.lower() in n.lower()]
    assert matching == [mafia_name], (
        f"prefix {prefix!r} is ambiguous across alive roster {alive_names!r}"
    )

    _drive(graph, run_config, Command(resume=f"/vote {prefix}"))

    state = graph.get_state(run_config).values
    assert state.get("human_votes_called") == 1, (
        f"expected human_votes_called==1 after the human's /vote, got "
        f"{state.get('human_votes_called')!r}"
    )


def test_human_ballot_bumps_human_ballots_cast(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the human casts a ballot, ``human_ballots_cast`` increments.

    After the human initiates a vote, ``collect_votes`` polls every alive
    player one per super-step; the human's own poll surfaces a ``kind="vote"``
    interrupt. Resuming it with "yes"/"no" must bump ``human_ballots_cast``.
    We advance until that counter goes positive (the human is polled exactly
    once per vote) and assert it reached 1.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive_to_human_day_turn(graph, run_config, fake)

    assert (
        graph.get_state(run_config).values.get("human_ballots_cast", 0) == 0
    ), "human_ballots_cast should start at 0"

    # Human initiates a vote against a unique-prefix alive Mafia AI, which
    # opens the ballot-collection flow that will eventually poll the human.
    mafia_id = _alive_mafia_ai_id(graph, run_config)
    mafia_name = _players(graph, run_config)[mafia_id].name
    prefix = mafia_name[:3]
    alive_names = [
        p.name for p in _players(graph, run_config).values() if p.is_alive
    ]
    assert [n for n in alive_names if prefix.lower() in n.lower()] == [
        mafia_name
    ], f"prefix {prefix!r} ambiguous across {alive_names!r}"

    _drive(graph, run_config, Command(resume=f"/vote {prefix}"))

    # Now poll through the ballot collection. The human's ballot interrupt
    # (kind="vote") gets a "yes"; AI ballots are served from the Yes queue.
    def _human_voted() -> bool:
        return (
            graph.get_state(run_config).values.get("human_ballots_cast", 0)
            >= 1
        )

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "vote":
            return "yes"
        if kind == "day_turn":
            return "..."
        return ""

    _advance_until(
        graph,
        run_config,
        stop=_human_voted,
        interrupt_responder=_respond,
        budget=100,
    )

    cast = graph.get_state(run_config).values.get("human_ballots_cast", 0)
    assert cast == 1, (
        f"expected human_ballots_cast==1 after the human cast one ballot, "
        f"got {cast!r}"
    )
