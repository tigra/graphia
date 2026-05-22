"""Slice 9 tests: spectator mode, Ctrl-C abort banner, 20-cycle draw cap.

Four scenarios cover the polish layer that landed in Slice 9:

1. ``test_spectator_view_when_human_dies_midgame`` — Textual pilot. The
   human is Law-abiding (seed 0). Night-1 pointing is hijacked to target
   the human; after the kill resolves the app flips into spectator mode,
   writes the yellow "You have been killed." whisper, dims a
   "(You are now spectating.)" line to the public log, disables the
   input for the rest of play, and the graph continues to produce AI
   speeches that land in the public log.

2. ``test_ctrl_c_shows_aborted_banner`` — Textual pilot. After submitting
   a name, ``pilot.press("ctrl+c")`` fires the ``action_abort`` binding
   which writes the red "Game aborted." banner and exits the app.

3. ``test_cycle_20_triggers_draw_end`` — Direct unit call to
   ``night_open`` with ``cycle=19`` + ``phase="day"`` (so ``night_open``
   computes ``cycle=20``), then ``route_after_night_open`` — verifies
   the draw short-circuit path without having to replay 20 rounds of
   real gameplay. The full-graph ``update_state`` approach is
   deliberately avoided: the Slice 9 task spec permits this fallback
   and it is the least flaky option given LangGraph's checkpointer
   semantics.

4. ``test_draw_end_screen_contains_kill_log_and_roster`` — Direct unit
   call to ``end_screen`` with ``winner="draw"``, a synthetic kill-log
   record, and a full 7-player roster. Asserts the Draw winner line,
   the kill-log header + entry, and every player name + role label
   appear in the composed Moderator message.

No test touches real Bedrock. The autouse ``safe_llm`` fixture in
``conftest.py`` raises on any unstubbed LLM call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from langchain_core.messages import SystemMessage
from rich.text import Text
from textual.widgets import Input, RichLog

from graphia.llm import DayAction
from graphia.nodes.endgame import end_screen
from graphia.nodes.night import night_open, route_after_night_open
from graphia.prompts import (
    ENDGAME_HEADER_KILLS,
    ENDGAME_HEADER_ROSTER,
    ENDGAME_WINNER_DRAW,
)
from graphia.state import KillRecord, PlayerState
from graphia.ui.app import GraphiaApp

# Seed 0 places the human in insertion-order slot 0 as Law-abiding. The
# Slice 4/5/6/7/8 suites all rely on this — consistent across Slice 9 too.
SEED_LAW_ABIDING = 0

AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"


# --------------------------------------------------------------------------
# Textual-pilot helpers (mirrored from Slice 5 / 8 to keep this file
# self-contained — no cross-file test imports).
# --------------------------------------------------------------------------


async def _wait_for(
    pilot,
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 5.0,
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
        if isinstance(text, Text):
            parts.append(text.plain)
        else:
            parts.append(str(text))
    return "\n".join(parts)


async def _wait_for_input_enabled(app: GraphiaApp, pilot) -> Input:
    async def _ready() -> bool:
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001
            return False
        return prompt.disabled is False

    await _wait_for(pilot, _ready, timeout=5.0)
    return app.query_one("#player-input", Input)


# --------------------------------------------------------------------------
# Test 1: spectator view activates when the human dies mid-game.
# --------------------------------------------------------------------------


async def test_spectator_view_when_human_dies_midgame(
    env: Path,
    fake_haiku,
    fake_sonnet,
    target_human_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human dies Night 1 → app flips into spectator mode; game continues."""
    monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))
    fake_haiku(AI_NAMES)

    # Script Day speeches so AIs keep the game going after the human dies.
    # Using `kind="speak"` only (never `vote`) keeps the Day loop simple —
    # it will cap out at DAY_MAX_ROUNDS rather than ever triggering a vote.
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"post-death-talk-{i}")
            for i in range(12)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Install the night-pointing fake that resolves the human's id at
        # invoke time. Both AI Mafia point at the human, producing a 2-0
        # consensus → the human dies Night 1.
        target_human_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        # Submit the name to seed roles.
        prompt = await _wait_for_input_enabled(app, pilot)
        prompt.focus()
        await pilot.press(*HUMAN_NAME)
        await pilot.press("enter")

        # Wait for the spectator flip. The night kill is the only thing
        # that can flip _spectator at seed 0 (human is Law-abiding, so no
        # human-Mafia pointing interrupt blocks the flow). Sample the
        # graph's message count the very first tick where _spectator is
        # True — needed so the "more messages land afterwards" assertion
        # isn't captured against a graph that's already finished.
        spectator_snapshot_count = {"n": -1}

        def _just_flipped() -> bool:
            if app._spectator and spectator_snapshot_count["n"] == -1:
                try:
                    msgs = app._graph.get_state(
                        app._run_config
                    ).values.get("messages", [])
                    spectator_snapshot_count["n"] = len(msgs)
                except Exception:  # noqa: BLE001
                    pass
            return app._spectator

        await _wait_for(pilot, _just_flipped, timeout=10.0)

        assert app._spectator is True

        private_log = app.query_one("#private-log", RichLog)
        public_log = app.query_one("#public-log", RichLog)

        private_text = _rich_log_text(private_log)
        public_text = _rich_log_text(public_log)

        assert "You have been killed." in private_text, (
            f"expected 'You have been killed.' in #private-log; got:\n"
            f"{private_text!r}"
        )
        assert "Watching as a spectator." in private_text, (
            f"expected 'Watching as a spectator.' in #private-log; got:\n"
            f"{private_text!r}"
        )
        # Spec 003 / Sub 3.1 hint line — points the spectator at the
        # canonical exit keys so they aren't stuck watching forever.
        assert "(Press Esc to exit.)" in private_text, (
            f"expected '(Press Esc to exit.)' hint in #private-log; got:\n"
            f"{private_text!r}"
        )
        assert "(You are now spectating.)" in public_text, (
            f"expected '(You are now spectating.)' in #public-log; got:\n"
            f"{public_text!r}"
        )

        # Human is dead in graph state.
        state = app._graph.get_state(app._run_config).values
        human_id = state["human_id"]
        assert state["players"][human_id].is_alive is False

        # Input stays disabled for the rest of play: sample it several
        # times while the graph advances past Day 1.
        input_widget = app.query_one("#player-input", Input)

        # Count system+AI messages in graph state at the moment of the
        # spectator flip, then wait for the graph to produce more.
        def _message_count() -> int:
            try:
                msgs = (
                    app._graph.get_state(app._run_config).values.get(
                        "messages", []
                    )
                )
            except Exception:  # noqa: BLE001
                return 0
            return len(msgs)

        # The graph continued past the human's death — assert by locating
        # the human-kill announcement in the message log and confirming
        # there are additional public/AI messages following it. Seed 0
        # kills the human on Night 1 so the line appears early; by the
        # time the test resumes the graph typically has 50+ more messages
        # (further Night kills, Day speeches, endgame reveal).
        state = app._graph.get_state(app._run_config).values
        human_id = state["human_id"]
        human_name = state["players"][human_id].name
        kill_line = f"During the night, {human_name} was killed."

        messages = state.get("messages", [])
        kill_index = -1
        for idx, msg in enumerate(messages):
            content = getattr(msg, "content", "")
            if isinstance(content, str) and kill_line in content:
                kill_index = idx
                break
        assert kill_index >= 0, (
            f"expected kill announcement for human ({human_name!r}) in "
            f"state.messages; got\n"
            f"{[getattr(m, 'content', repr(m))[:60] for m in messages]!r}"
        )

        # At least one further message must land after the kill — proving
        # the graph kept running while the human was a spectator.
        assert len(messages) > kill_index + 1, (
            f"expected graph to produce more messages after the human's "
            f"death; kill_index={kill_index}, total_messages={len(messages)}"
        )

        # Across the observation, the player-input must never re-enable
        # while the human is a spectator.
        for _ in range(20):
            assert input_widget.disabled is True, (
                "player-input must remain disabled while in spectator mode"
            )
            await pilot.pause(0.05)

        # Clean up — the graph would otherwise loop Night↔Day indefinitely
        # against a dead human who still sits in the roster.
        app.exit()
    assert app.is_running is False


# --------------------------------------------------------------------------
# Test 2: Ctrl+C during play shows the red "Game aborted." banner.
# --------------------------------------------------------------------------


async def test_ctrl_c_shows_aborted_banner(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing ctrl+c mid-game writes 'Game aborted.' to #public-log and exits."""
    monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))
    fake_haiku(AI_NAMES)
    fake_sonnet(
        # Placeholder pointing — the fake unified queue replays its last
        # output when empty, which is fine because we abort before any
        # second Night runs. Day actions cover the entire pre-abort window.
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"early-talk-{i}") for i in range(6)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Submit the human name so we're past the first interrupt.
        prompt = await _wait_for_input_enabled(app, pilot)
        prompt.focus()
        await pilot.press(*HUMAN_NAME)
        await pilot.press("enter")

        # Let the graph advance at least one super-step past the name
        # interrupt so ``_game_over`` is still False (the on_key handler
        # is a no-op; we rely on the ``ctrl+c`` binding instead).
        await pilot.pause(0.2)

        assert app._game_over is False, (
            "game should still be running before ctrl+c is pressed"
        )

        await pilot.press("ctrl+c")

        # Give Textual a tick to process the binding before the context
        # manager tears down the pilot.
        await pilot.pause(0.1)

        public_log = app.query_one("#public-log", RichLog)
        public_text = _rich_log_text(public_log)

        assert "Game aborted." in public_text, (
            f"expected 'Game aborted.' in #public-log after ctrl+c; got:\n"
            f"{public_text!r}"
        )

    assert app.is_running is False


# --------------------------------------------------------------------------
# Test 3: reaching cycle 20 forces a draw ending.
# --------------------------------------------------------------------------


def test_cycle_20_triggers_draw_end() -> None:
    """Unit test on ``night_open`` + ``route_after_night_open``.

    Approach: call the two cycle-cap-relevant functions directly rather
    than replaying 20 full Day/Night rounds through the compiled graph.
    LangGraph's checkpointer makes ``update_state`` awkward for large
    skips (reducer semantics on ``messages`` / ``kill_log``), and the
    draw-cap logic is pure Python that lives entirely in these two
    functions — so a focused unit test is the cleanest verification
    and the Slice 9 task spec explicitly permits this fallback.
    """
    # Entering night_open from the Day loop at cycle=19 → cycle should
    # bump to 20 and trigger the draw branch.
    update = night_open({"cycle": 19, "phase": "day", "players": {}})

    assert update["winner"] == "draw"
    assert update["cycle"] == 20
    assert update["phase"] == "night"

    # Message announcing the draw is emitted as a SystemMessage.
    messages = update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], SystemMessage)
    assert "20 cycles" in messages[0].content

    # Router short-circuits to end_screen when the winner is "draw".
    assert route_after_night_open({"winner": "draw"}) == "end_screen"

    # Sanity: below the cap, night_open behaves normally and the router
    # takes the mafia_pointing path.
    normal = night_open({"cycle": 1, "phase": "setup", "players": {}})
    assert normal.get("winner") is None
    assert normal["cycle"] == 1
    assert normal["phase"] == "night"
    assert route_after_night_open(normal) == "mafia_pointing"

    # And night_open bumps the cycle when re-entering from Day below the cap.
    rolled = night_open({"cycle": 5, "phase": "day", "players": {}})
    assert rolled["cycle"] == 6
    assert rolled.get("winner") is None


# --------------------------------------------------------------------------
# Test 4: the draw end screen still includes the kill log + full roster.
# --------------------------------------------------------------------------


def test_draw_end_screen_contains_kill_log_and_roster() -> None:
    """``end_screen`` with winner='draw' emits the Draw winner line AND
    unconditionally includes the kill-log header and the full roster
    reveal — matching the behaviour exercised for law/mafia wins in
    ``test_slice8_endgame.py``.
    """
    # Build a minimal but complete roster: 2 Mafia, 5 Law-abiding — the
    # regulation 7-player game. One victim was killed on Night 3 (before
    # the draw cap fired).
    players: dict[str, PlayerState] = {
        "p-1": PlayerState(id="p-1", name="Alice", role="law_abiding", is_human=True),
        "p-2": PlayerState(id="p-2", name="Ivy", role="law_abiding", is_human=False),
        "p-3": PlayerState(id="p-3", name="Marco", role="mafia", is_human=False),
        "p-4": PlayerState(id="p-4", name="Priya", role="mafia", is_human=False),
        "p-5": PlayerState(
            id="p-5", name="Silas", role="law_abiding", is_human=False, is_alive=False
        ),
        "p-6": PlayerState(id="p-6", name="Yuki", role="law_abiding", is_human=False),
        "p-7": PlayerState(id="p-7", name="Aarav", role="law_abiding", is_human=False),
    }
    kill_log: list[KillRecord] = [
        {"cycle": 3, "name": "Silas", "cause": "night", "role": None},
    ]

    state = {
        "winner": "draw",
        "cycle": 20,
        "phase": "night",
        "players": players,
        "kill_log": kill_log,
    }

    update = end_screen(state)
    assert update["phase"] == "end"

    messages = update["messages"]
    assert len(messages) == 1
    final = messages[0]
    assert isinstance(final, SystemMessage)
    content = final.content

    # Draw winner line.
    assert ENDGAME_WINNER_DRAW in content

    # Kill-log section present with the lone Night-3 entry.
    assert ENDGAME_HEADER_KILLS in content
    assert "Silas" in content
    # Role is resolved by name lookup (KillRecord.role was None).
    assert "Law-abiding Citizen" in content

    # Full roster reveal: every player + role label.
    assert ENDGAME_HEADER_ROSTER in content
    roster_section = content.split(ENDGAME_HEADER_ROSTER, 1)[1]
    for player in players.values():
        assert player.name in roster_section, (
            f"{player.name!r} missing from roster section:\n{roster_section!r}"
        )
        role_label = (
            "Mafia" if player.role == "mafia" else "Law-abiding Citizen"
        )
        expected_fragment = f"{player.name} ({role_label})"
        assert expected_fragment in roster_section, (
            f"missing {expected_fragment!r} in roster section:\n"
            f"{roster_section!r}"
        )
