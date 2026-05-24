"""Slice 5 tests: Night 1 end-to-end via the Textual app.

Covers two deterministic scenarios driven through the compiled graph:

1. Human is Law-abiding — both AI Mafia point at the same Law-abiding
   target, no tie-break needed, and the public log announces the kill with
   no role reveal. The human sees their private role reveal but no Mafia
   teammate intro (because they're not Mafia).

2. Human is Mafia — the ``PointingModal`` is pushed, the human selects a
   target via the modal, the lone AI Mafia points at the same target, and
   the kill resolves. The human sees both their role reveal and the private
   Mafia-teammate intro.

Both tests stub the Bedrock boundary with the unified ``fake_sonnet``
fixture (day + night bindings in one shot) plus ``fake_haiku`` for the
roster generator, so nothing touches real Bedrock. Using the unified
fake also covers the Day-open Sonnet call that fires once the night
kill resolves — previous revisions stubbed only night, letting Day-1
speaking calls leak to real Bedrock and strand a boto3 retry thread
past ``app.exit()``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.widgets import Input, RichLog

from graphia.llm import DayAction
from graphia.ui.app import GraphiaApp
from graphia.ui.widgets import PointingModal


AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"


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
        parts.append(text)
    return "\n".join(parts)


async def _wait_for_input(app: GraphiaApp, pilot) -> Input:
    """Wait until ``#player-input`` is enabled (i.e. ``collect_name`` ready)."""

    async def _input_enabled() -> bool:
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001 — widget not mounted yet
            return False
        return prompt.disabled is False

    await _wait_for(pilot, _input_enabled, timeout=5.0)
    return app.query_one("#player-input", Input)


async def _submit_name(app: GraphiaApp, pilot) -> None:
    """Type ``HUMAN_NAME`` into the prompt and press Enter."""
    prompt = await _wait_for_input(app, pilot)
    prompt.focus()
    await pilot.press(*HUMAN_NAME)
    await pilot.press("enter")


def _players_snapshot(app: GraphiaApp) -> dict:
    """Return the ``players`` dict from the current graph state snapshot."""
    state = app._graph.get_state(app._run_config)
    return state.values["players"]


async def _wait_for_players(app: GraphiaApp, pilot) -> dict:
    """Poll until the graph state has a fully-assigned 7-player roster."""

    def _ready() -> bool:
        try:
            players = _players_snapshot(app)
        except Exception:  # noqa: BLE001 — state not there yet
            return False
        if len(players) != 7:
            return False
        # Roles are only populated after `assign_roles` runs.
        return all(p.role in ("mafia", "law_abiding") for p in players.values())

    await _wait_for(pilot, _ready, timeout=5.0)
    return _players_snapshot(app)


async def test_night1_human_law_abiding_kill_announced(
    env: Path,
    fake_haiku,
    fake_sonnet,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human is Law-abiding; both AI Mafia point at the same target.

    No human pointing interrupt is raised (the human isn't Mafia), so the
    flow goes straight from name submission to kill resolution. Assertions:

    - Public log shows the Night-falls line and the kill announcement,
      with no role-reveal leak about the victim.
    - Exactly 6 of 7 players are alive; the victim's ``is_alive`` flipped.
    - ``kill_log`` has the expected cycle-1 Night record.
    - The human's private panel shows their role reveal but no Mafia intro.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)

    # Unified Sonnet fake handles Day speaking / Ballot. Night pointing
    # is patched immediately afterwards with the dynamic fake that picks
    # the first alive Law-abiding non-human at invoke time — required so
    # the assertions below line up with the actual victim regardless of
    # worker timing.
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"day-talk-{i}") for i in range(8)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        # Drive through name entry so roles get assigned on graph state.
        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        # Pick the first alive Law-abiding non-human as the agreed victim —
        # matches the dynamic night-pointing fake's selection rule.
        mafia_ids = [
            pid for pid, p in players.items() if p.role == "mafia"
        ]
        law_abiding_ids = [
            pid
            for pid, p in players.items()
            if p.role == "law_abiding" and not p.is_human
        ]
        assert len(mafia_ids) == 2, (
            f"expected 2 AI mafia when GRAPHIA_ROLE=law-abiding, got {mafia_ids}"
        )
        assert law_abiding_ids, "no AI law-abiding player to victimise"

        target_id = law_abiding_ids[0]
        target_name = players[target_id].name

        public_log = app.query_one("#public-log", RichLog)
        private_log = app.query_one("#private-log", RichLog)

        kill_line = f"During the night, {target_name} was killed."

        def _kill_resolved() -> bool:
            if kill_line not in _rich_log_text(public_log):
                return False
            try:
                players_now = _players_snapshot(app)
            except Exception:  # noqa: BLE001
                return False
            victim = players_now.get(target_id)
            return victim is not None and victim.is_alive is False

        await _wait_for(pilot, _kill_resolved, timeout=10.0)

        public_rendered = _rich_log_text(public_log)
        private_rendered = _rich_log_text(private_log)

        # Public log assertions.
        assert "Night falls." in public_rendered
        assert kill_line in public_rendered
        # No role reveal of the victim should leak from the night-kill line.
        # (Day 1 may add a role-reveal line; we only guard the night-kill line.)
        assert (
            f"During the night, {target_name} was killed. {target_name} was"
            not in public_rendered
        )
        assert "Your role is" not in public_rendered

        # Graph-state assertions.
        state = app._graph.get_state(app._run_config).values
        final_players = state["players"]
        assert final_players[target_id].is_alive is False
        alive = [p for p in final_players.values() if p.is_alive]
        assert len(alive) == 6

        kill_log = state.get("kill_log", [])
        assert len(kill_log) >= 1
        record = kill_log[0]
        assert record["cycle"] == 1
        assert record["cause"] == "night"
        assert record["role"] is None
        assert record["name"] == target_name

        # Private-panel assertions.
        # Role reveal from Slice 4 should be there (human is Law-abiding).
        assert (
            f"You are {HUMAN_NAME}. Your role is Law-abiding Citizen."
            in private_rendered
        )
        # Mafia teammate intro is private-to each Mafia; the human is NOT
        # Mafia here, so that line must not reach the human's private panel.
        assert "Your Mafia teammates are" not in private_rendered

        # The graph now loops Night -> Day -> Night; force the app to exit
        # rather than waiting for an END that will never come.
        app.exit()
    assert app.is_running is False


async def test_night1_human_mafia_picks_target_via_modal(
    env: Path,
    fake_haiku,
    fake_sonnet,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human is Mafia; the pointing modal opens and the human picks a target.

    With 2 total Mafia and the human being one of them, only one AI Mafia
    is asked. Both Mafia (human + AI) agree on the same target, producing
    a clean majority of 2-0.

    Assertions cover: the Mafia-teammate private intro reaching the human,
    the public kill announcement, the recorded ``night_picks``, and the
    victim's ``is_alive`` flipping to ``False``.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_haiku(AI_NAMES)

    # Install LLM stubs BEFORE ``run_test`` — once the worker starts it can
    # reach ``mafia_pointing`` almost immediately, so we need the Sonnet
    # bindings in place before the modal interrupt even fires.
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"day-talk-{i}") for i in range(8)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Dynamic night-pointing fake: the one AI Mafia picks the first
        # alive Law-abiding AI at invoke time. This matches the
        # ``law_abiding_ids[0]`` target the human selects via the modal,
        # producing a clean 2-0 consensus.
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        human_id = app._graph.get_state(app._run_config).values["human_id"]
        assert players[human_id].role == "mafia", (
            f"expected GRAPHIA_ROLE=mafia to make the human Mafia; "
            f"got {players[human_id].role}"
        )

        ai_mafia_ids = [
            pid
            for pid, p in players.items()
            if p.role == "mafia" and not p.is_human
        ]
        law_abiding_ids = [
            pid for pid, p in players.items() if p.role == "law_abiding"
        ]
        assert len(ai_mafia_ids) == 1
        ai_mafia_id = ai_mafia_ids[0]
        assert len(law_abiding_ids) == 5

        target_id = law_abiding_ids[0]
        target_name = players[target_id].name

        # Wait for the PointingModal to be pushed onto the screen stack.
        def _modal_open() -> bool:
            screen = app.screen
            return isinstance(screen, PointingModal) or len(app.screen_stack) > 1

        await _wait_for(pilot, _modal_open, timeout=5.0)

        # Grab the PointingModal from the stack and dismiss it with our
        # chosen target — a legitimate test-time shortcut that avoids the
        # fragility of sending keystrokes through a modal during the graph
        # driver's worker super-step.
        modal: PointingModal | None = None
        for screen in app.screen_stack:
            if isinstance(screen, PointingModal):
                modal = screen
                break
        assert modal is not None, "PointingModal not found on the screen stack"
        modal.dismiss(target_id)

        public_log = app.query_one("#public-log", RichLog)
        private_log = app.query_one("#private-log", RichLog)

        kill_line = f"During the night, {target_name} was killed."

        def _kill_resolved() -> bool:
            if kill_line not in _rich_log_text(public_log):
                return False
            try:
                players_now = _players_snapshot(app)
            except Exception:  # noqa: BLE001
                return False
            victim = players_now.get(target_id)
            return victim is not None and victim.is_alive is False

        await _wait_for(pilot, _kill_resolved, timeout=10.0)

        public_rendered = _rich_log_text(public_log)
        private_rendered = _rich_log_text(private_log)

        # Private panel: role reveal + Mafia teammate intro.
        assert (
            f"You are {HUMAN_NAME}. Your role is Mafia." in private_rendered
        )
        assert "Your Mafia teammates are" in private_rendered
        ai_mafia_name = players[ai_mafia_id].name
        assert ai_mafia_name in private_rendered

        # Public: the kill announcement lands.
        assert kill_line in public_rendered

        # Graph-state assertions.
        state = app._graph.get_state(app._run_config).values
        final_players = state["players"]
        night_picks = state.get("night_picks", {})
        assert night_picks.get(human_id) == target_id
        assert night_picks.get(ai_mafia_id) == target_id
        assert final_players[target_id].is_alive is False

        # Graph now loops Night -> Day -> Night; force-exit instead of
        # pressing 'q', which wouldn't gracefully terminate the worker
        # while the Day loop is running.
        app.exit()
    assert app.is_running is False
