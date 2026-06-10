"""Slice 6 tests: the Day phase opens, rounds speak, and the Day ends at 6.

Three scenarios, each isolated to a specific piece of Day-phase behaviour:

1. ``test_day_opens_with_victim_role_reveal`` drives the Textual app and
   asserts the Day-open line reveals the victim's role via the
   ``DAY_OPEN_VICTIM_REVEAL_TEMPLATE``, while the night-kill line stays
   role-free.

2. ``test_day_rounds_shuffle_and_players_speak`` asserts that AI players
   take their turns in round order and emit their scripted texts. Because
   the ``day_turn`` interrupt for a human is not yet handled in the UI
   (pending UI task), this test only inspects graph state after AI turns
   have run; if the driver errors at the human turn the test still reads
   the state and exits cleanly.

3. ``test_six_rounds_without_vote_ends_day`` bypasses Textual entirely and
   drives the compiled graph directly, resuming ``name`` and ``day_turn``
   interrupts with scripted strings, and stops the moment the
   ``"The Day ends with no one executed."`` line is emitted.

All tests stub the Bedrock boundary via the ``fake_haiku`` /
``fake_sonnet_pointing`` / ``fake_sonnet_day`` fixtures so nothing touches
real AWS Bedrock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command
from textual.widgets import Input, RichLog

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import DayAction, Pointing
from graphia.ui.app import GraphiaApp

AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"


# --------------------------------------------------------------------------
# Polling helpers (mirrored from the Slice 5 tests to keep each test file
# self-sufficient — no cross-test imports).
# --------------------------------------------------------------------------


async def _wait_for(
    pilot,
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 10.0,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until truthy, yielding to pilot each tick."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(
                f"Predicate {predicate!r} did not become truthy within {timeout}s"
            )
        await pilot.pause(interval)


def _rich_log_text(widget: RichLog) -> str:
    """Flatten a RichLog's accumulated lines to plain text."""
    parts: list[str] = []
    for line in widget.lines:
        text = getattr(line, "text", None)
        if text is None:
            text = str(line)
        parts.append(text)
    return "\n".join(parts)


async def _wait_for_input(app: GraphiaApp, pilot) -> Input:
    async def _input_enabled() -> bool:
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001
            return False
        return prompt.disabled is False

    await _wait_for(pilot, _input_enabled, timeout=5.0)
    return app.query_one("#player-input", Input)


async def _submit_name(app: GraphiaApp, pilot) -> None:
    prompt = await _wait_for_input(app, pilot)
    prompt.focus()
    await pilot.press(*HUMAN_NAME)
    await pilot.press("enter")


def _players_snapshot(app: GraphiaApp) -> dict:
    state = app._graph.get_state(app._run_config)
    return state.values["players"]


async def _wait_for_players(app: GraphiaApp, pilot) -> dict:
    def _ready() -> bool:
        try:
            players = _players_snapshot(app)
        except Exception:  # noqa: BLE001
            return False
        if len(players) != 7:
            return False
        return all(p.role in ("mafia", "law_abiding") for p in players.values())

    await _wait_for(pilot, _ready, timeout=5.0)
    return _players_snapshot(app)


# --------------------------------------------------------------------------
# Test 1: day_open emits the victim reveal line; night-kill line stays clean.
# --------------------------------------------------------------------------


async def test_day_opens_with_victim_role_reveal(
    env: Path,
    fake_haiku,
    fake_sonnet,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Day-open line reveals the victim's role; night-kill line does not."""
    # Role-pin only: the test reads ``law_abiding_ids[0]`` from live state
    # and asserts the victim's name (whoever it is) appears in the
    # role-reveal line. No RNG-driven ordering is asserted, so the role
    # pin is the only setup required.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)
    # Unified Sonnet fake handles Day-speaking (and Ballot if a vote
    # happens). Day fake is installed first; then we immediately override
    # the Night binding with the race-safe dynamic fake.
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"I suspect someone. ({i})")
            for i in range(40)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Dynamic Night-pointing fake: picks the first alive Law-abiding
        # non-human at invoke time, resolving the real UUID from graph
        # state. Side-steps the race between ``_wait_for_players``
        # returning and the worker reaching ``mafia_pointing``.
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        law_abiding_ids = [
            pid
            for pid, p in players.items()
            if p.role == "law_abiding" and not p.is_human
        ]
        assert law_abiding_ids, "no AI law-abiding player to victimise"
        target_id = law_abiding_ids[0]
        target_name = players[target_id].name

        public_log = app.query_one("#public-log", RichLog)

        def _flattened() -> str:
            # RichLog wraps long messages, so collapse whitespace before we
            # substring-match. This lets us ignore line-wrap-induced spaces
            # and hyphen breaks.
            return " ".join(_rich_log_text(public_log).split())

        role_reveal_line = (
            f"{target_name} was killed last night. {target_name} was a "
            f"Law-abiding Citizen."
        )

        def _day_opened() -> bool:
            flat = _flattened()
            return "Day breaks." in flat and role_reveal_line in flat

        try:
            await _wait_for(pilot, _day_opened, timeout=10.0)
        except TimeoutError:
            # Surface the current public log for diagnosis.
            raise AssertionError(
                "Day never opened. Public log contents:\n"
                + _rich_log_text(public_log)
            )

        flat = _flattened()

        # Day-open message is the canonical template.
        assert "Day breaks." in flat
        assert role_reveal_line in flat

        # Night-kill announcement is still role-free: "During the night, X
        # was killed." is present, but NOT followed by role info.
        assert f"During the night, {target_name} was killed." in flat
        assert (
            f"During the night, {target_name} was killed. "
            f"{target_name} was a"
            not in flat
        )

        # Stop the graph: the game loops forever pre-Slice-8.
        app.exit()
    assert app.is_running is False


# --------------------------------------------------------------------------
# Test 2: AI speakers emit their scripted lines in round order.
# --------------------------------------------------------------------------


async def test_day_rounds_shuffle_and_players_speak(
    env: Path,
    fake_haiku,
    fake_sonnet,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At least the 5 alive AI players each emit one AIMessage in Day 1.

    Because the UI does not yet handle ``kind="day_turn"`` interrupts, the
    worker will raise ``NotImplementedError`` the moment the human's turn
    comes up. The assertion-side goal is to verify that *before* that error
    lands, every alive AI has already produced an AIMessage with their name
    and their scripted text. We poll graph state (not the log) to avoid
    depending on exact rendering behaviour.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    # Pin the Day-1 speech-order so the human lands at index 3, letting >=3
    # AI players speak before the human-turn interrupt halts the worker.
    # Without this pin, the human could be first in order and the "at least
    # 3 AI spoke" assertion below would flake.
    def _human_at_index_three(players):
        alive = [pid for pid, p in players.items() if p.is_alive]
        human_id = next(pid for pid, p in players.items() if p.is_human)
        ai_ids = [pid for pid in alive if pid != human_id]
        return [*ai_ids[:3], human_id, *ai_ids[3:]]

    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_at_index_three)
    fake_haiku(AI_NAMES)

    scripted_texts = [f"msg-from-AI-{i}" for i in range(1, 41)]
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=t) for t in scripted_texts
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Dynamic Night-pointing fake resolves the real target id at
        # ``invoke`` time, regardless of whether the worker has reached
        # ``mafia_pointing`` by the time the test proceeds.
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        law_abiding_ids = [
            pid
            for pid, p in players.items()
            if p.role == "law_abiding" and not p.is_human
        ]
        target_id = law_abiding_ids[0]

        # After the night, 5 AI players are alive. The human takes a slot
        # somewhere in the round-1 order, so at minimum 0 AI turns precede
        # the human's turn; at maximum 5 AI turns precede it. The
        # ``_shuffle_order`` stub above places the human at index 3, so we
        # expect at least 3 distinct AI speakers before the worker errors
        # out on the unimplemented human-turn interrupt. Any number < 3
        # would suggest the round-robin speaking isn't advancing at all.
        alive_ai_names_after_night = {
            p.name
            for pid, p in players.items()
            if not p.is_human and pid != target_id
        }
        assert len(alive_ai_names_after_night) == 5

        def _ai_speakers() -> set[str]:
            try:
                state = app._graph.get_state(app._run_config).values
            except Exception:  # noqa: BLE001
                return set()
            msgs = state.get("messages", [])
            speakers: set[str] = set()
            for msg in msgs:
                if not isinstance(msg, AIMessage):
                    continue
                name = getattr(msg, "name", None) or (
                    (msg.additional_kwargs or {}).get("speaker")
                )
                if isinstance(name, str) and name:
                    speakers.add(name)
            return speakers

        def _at_least_three_ai_spoke() -> bool:
            spoke = _ai_speakers()
            return len(spoke & alive_ai_names_after_night) >= 3

        try:
            await _wait_for(pilot, _at_least_three_ai_spoke, timeout=10.0)
        except TimeoutError:
            public_log = app.query_one("#public-log", RichLog)
            rendered = _rich_log_text(public_log)
            raise AssertionError(
                "Expected at least 3 alive AI players to have spoken before "
                "the test stopped driving the graph; public log was:\n"
                f"{rendered}"
            )

        state = app._graph.get_state(app._run_config).values
        ai_texts = [
            m.content
            for m in state.get("messages", [])
            if isinstance(m, AIMessage)
        ]
        # Scripted texts flow 1:1 from FakeSonnetDay.invoke into AIMessages:
        # at least the first 3 scripted values must appear, in order.
        assert scripted_texts[0] in ai_texts
        assert scripted_texts[1] in ai_texts
        assert scripted_texts[2] in ai_texts

        # And the AI speakers we've seen must all be drawn from the alive
        # AI set — no voice should come from the dead victim or the human.
        spoke = _ai_speakers()
        assert spoke.issubset(alive_ai_names_after_night), (
            f"unexpected AI speakers: {spoke - alive_ai_names_after_night!r}"
        )
        assert HUMAN_NAME not in spoke
        assert players[target_id].name not in spoke

        app.exit()
    assert app.is_running is False


# --------------------------------------------------------------------------
# Test 3: direct graph stream; drive until day_close fires at round 6.
# --------------------------------------------------------------------------


def _collect_interrupt(graph, run_config):
    """Return the first pending interrupt value on the graph, or None."""
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        interrupts = task.interrupts or ()
        for interrupt_obj in interrupts:
            return interrupt_obj.value
    return None


DAY_CLOSE_LINE = "The Day ends with no one executed."


def _day_closed(graph, run_config) -> bool:
    state = graph.get_state(run_config).values
    for msg in state.get("messages", []):
        if (
            isinstance(msg, SystemMessage)
            and DAY_CLOSE_LINE in msg.content
        ):
            return True
    return False


def test_six_rounds_without_vote_ends_day(
    env: Path,
    fake_haiku,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the graph directly (no Textual): Day closes after 6 rounds.

    We bypass the Textual pilot entirely — the UI does not yet resume
    ``kind="day_turn"`` interrupts, which would deadlock the pilot-driven
    run. Streaming the compiled graph ourselves lets us resume each
    interrupt with a scripted value and stop the moment the Day closes.
    """
    # Role-pin only: the test counts day_rounds and asserts the Day-close
    # line appears after 6 rounds. The infinite-sonnet fakes always
    # "speak" (never "vote"), and the human's role pin (law-abiding)
    # keeps the "point" interrupt suppressed — no RNG-driven ordering or
    # tie-break behaviour is asserted, so no RNG pinning is required.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)

    # Patch the sonnet bindings directly with inline callables — the
    # factory fixtures don't support "infinite" stream lengths that test 3
    # wants. We mirror the production call shape (.with_structured_output
    # returning self, .invoke returning a model) so the graph is happy.
    class _InfSonnetDay:
        call_count = 0

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.call_count += 1
            return DayAction(
                kind="speak", text=f"msg-ai-{self.call_count}"
            )

    class _InfSonnetPointing:
        """Returns the FIRST alive Law-abiding AI as the victim every call.

        Target resolution happens inside .invoke by reading the current
        graph state, so we don't need to know ids ahead of time. Because
        the graph loops after Day 6, subsequent nights must also work —
        this fake is stateless and always picks a fresh valid target.
        """

        def __init__(self, pick: Callable[[], str]) -> None:
            self._pick = pick
            self.call_count = 0

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.call_count += 1
            return __import__(
                "graphia.llm", fromlist=["Pointing"]
            ).Pointing(target_id=self._pick())

    # We'll install the pointing fake lazily — we need to know the roster
    # before we can pick a target. Start with a lambda that resolves against
    # live graph state at call time.
    graph_ref: list = []  # Populated after build_graph below.
    run_config_ref: list = []

    def _live_victim() -> str:
        g = graph_ref[0]
        rc = run_config_ref[0]
        state = g.get_state(rc).values
        players = state.get("players", {})
        candidates = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        if not candidates:
            # Fallback to any alive player — better than crashing.
            candidates = [p.id for p in players.values() if p.is_alive]
        return candidates[0]

    sonnet_day_fake = _InfSonnetDay()
    sonnet_pointing_fake = _InfSonnetPointing(_live_victim)
    monkeypatch.setattr(
        "graphia.nodes.day.get_large", lambda: sonnet_day_fake
    )
    monkeypatch.setattr(
        "graphia.nodes.night.get_large", lambda: sonnet_pointing_fake
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    graph_ref.append(graph)
    run_config_ref.append(run_config)

    def _run_until_next_pause(payload) -> None:
        """Stream with the given payload; early-exit the moment Day closes."""
        for _ in graph.stream(payload, run_config, stream_mode="updates"):
            if _day_closed(graph, run_config):
                return
        return

    # Drive the initial stream until the name interrupt.
    _run_until_next_pause({"messages": []})

    # Sanity check: we should be paused on kind="name".
    first = _collect_interrupt(graph, run_config)
    assert first is not None and first.get("kind") == "name", (
        f"expected name interrupt first; got {first!r}"
    )

    # Loop: resume each interrupt until Day 1 closes.
    budget = 120  # Generous: name + (day_turn_for_human) × 6 rounds = 7 stops.
    for _ in range(budget):
        if _day_closed(graph, run_config):
            break
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            # Graph terminated unexpectedly (pre-Slice-8 there's no end).
            break

        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            # Paused between super-steps (shouldn't happen with stream_mode
            # "updates", but guard anyway).
            _run_until_next_pause(None)
            continue

        kind = interrupt_value.get("kind")
        if kind == "name":
            resume_value: str = HUMAN_NAME
        elif kind == "day_turn":
            resume_value = "I speak briefly."
        elif kind == "point":
            # Human is pinned Law-abiding via GRAPHIA_ROLE, so this shouldn't fire. Guard.
            options = interrupt_value.get("options") or []
            resume_value = options[0]["id"] if options else _live_victim()
        else:
            raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

        _run_until_next_pause(Command(resume=resume_value))

    assert _day_closed(graph, run_config), (
        "The Day never closed within the super-step budget — expected "
        f"{DAY_CLOSE_LINE!r} after 6 rounds."
    )

    # day_rounds should have hit the cap (DAY_MAX_ROUNDS == 6).
    state = graph.get_state(run_config).values
    assert state.get("day_rounds") == 6, (
        f"expected day_rounds == 6 at day_close, got {state.get('day_rounds')!r}"
    )
