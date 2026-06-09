"""Spec 008 (Same-Round Message Visibility) UI guarantee.

When it becomes the human's day turn, the speeches that *other* players
submitted earlier in the SAME round must already be visible on screen in
the ``#public-log`` pane before the human speaks (functional spec §2.1
"Same-Round Message Visibility" and §2.3).

This is a regression lock, not a production change: the on-screen public
log is unwindowed (every streamed AIMessage is written via
``GraphiaApp._write_public`` as it arrives), and the human's ``day_turn``
interrupt payload carries no discussion context. This test pins the
speaking order so AI players go first, scripts their speeches to known
lines, drives the Textual app to the human's ``day_turn`` interrupt, and
asserts those earlier lines are on screen *before* the human submits.

The Bedrock boundary is stubbed via ``fake_haiku`` (roster) and
``fake_sonnet`` (Day speaking) — no real AWS is reached, satisfying the
autouse ``safe_llm`` net.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.widgets import Input, RichLog

import graphia.nodes.day as day_nodes
from graphia.llm import DayAction
from graphia.ui.app import GraphiaApp

HUMAN_NAME = "Alice"
AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]


# --------------------------------------------------------------------------
# Polling / rendering helpers (mirrored from the Slice 6 UI tests to keep
# this file self-sufficient — no cross-test imports, matching project
# convention).
# --------------------------------------------------------------------------


async def _wait_for(
    pilot,
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 10.0,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until truthy, yielding to the pilot each tick."""
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
# The guarantee: earlier same-round speeches are on screen at the human's turn.
# --------------------------------------------------------------------------


async def test_same_round_speeches_visible_at_human_turn(
    env: Path,
    fake_haiku,
    fake_sonnet,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the human's day_turn, earlier same-round AI speeches are visible.

    Seat the human LAST in the round-1 speaking order so every alive AI
    speaks before the human's ``day_turn`` interrupt halts the worker. Each
    AI emits a distinctive scripted line; we read ``#public-log`` the moment
    the human is prompted (input enabled) and assert those lines are already
    rendered there — proving the on-screen log is not windowed away from the
    human at speak time.
    """
    # Pin the human as Law-abiding so a random Mafia draw cannot strand the
    # worker on the human-Mafia night branch during teardown.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    # Pin the Day-1 speaking order so the human is LAST: every alive AI
    # speaks ahead of the human's turn. ``_shuffle_order`` is the project's
    # sanctioned deterministic-ordering seam (see test_slice6_day.py).
    def _human_last(players):
        alive = [pid for pid, p in players.items() if p.is_alive]
        human_id = next(pid for pid, p in players.items() if p.is_human)
        ai_ids = [pid for pid in alive if pid != human_id]
        return [*ai_ids, human_id]

    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_last)
    fake_haiku(AI_NAMES)

    # Distinctive, assertable speeches — one per AI speaking slot. After the
    # night kill there are 5 alive AI players, so 5 distinct lines precede
    # the human's turn; extra lines pad the queue harmlessly.
    distinctive_texts = [f"distinctive-round1-line-{i}" for i in range(1, 41)]
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=t) for t in distinctive_texts
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Race-safe night-pointing fake: resolves the victim id at invoke
        # time from live graph state.
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        # Identify the 5 AI players who survive the night (all AIs except the
        # night victim — the first alive Law-abiding non-human, matching the
        # dynamic_night_pointing fake's selection).
        law_abiding_ai_ids = [
            pid
            for pid, p in players.items()
            if p.role == "law_abiding" and not p.is_human
        ]
        assert law_abiding_ai_ids, "no AI law-abiding player to victimise"
        victim_id = law_abiding_ai_ids[0]
        alive_ai_names_after_night = {
            p.name
            for pid, p in players.items()
            if not p.is_human and pid != victim_id
        }
        assert len(alive_ai_names_after_night) == 5

        public_log = app.query_one("#public-log", RichLog)

        # Drive until the human is prompted for their day_turn. The name
        # interrupt has already been answered, so the next time the input is
        # enabled it is the human's day_turn (the only other interrupt the
        # human can hit here — the /vote and vote-ballot paths are not
        # triggered). We additionally require that all 5 AI speeches have
        # landed in the log, which is the substantive same-round guarantee.
        def _human_turn_with_all_ai_spoken() -> bool:
            try:
                prompt = app.query_one("#player-input", Input)
            except Exception:  # noqa: BLE001
                return False
            if prompt.disabled:
                return False
            flat = _rich_log_text(public_log)
            return all(
                f"{name}:" in flat for name in alive_ai_names_after_night
            )

        try:
            await _wait_for(
                pilot, _human_turn_with_all_ai_spoken, timeout=15.0
            )
        except TimeoutError:
            raise AssertionError(
                "Human day_turn never reached with all AI having spoken. "
                "Public log was:\n" + _rich_log_text(public_log)
            )

        # BEFORE the human submits, the public log must already contain the
        # distinctive speeches the 5 earlier same-round AI speakers emitted.
        before_submit = _rich_log_text(public_log)

        # The 5 alive AI speakers consumed the first 5 distinctive lines, in
        # order. Every one of those lines must be on screen now.
        for line in distinctive_texts[:5]:
            assert line in before_submit, (
                f"earlier same-round speech {line!r} not visible in "
                f"#public-log at the human's turn. Log was:\n{before_submit}"
            )

        # And each alive AI speaker is attributed by name in the log.
        for name in alive_ai_names_after_night:
            assert f"{name}:" in before_submit, (
                f"AI speaker {name!r} not attributed in #public-log at the "
                f"human's turn. Log was:\n{before_submit}"
            )

        # The human has NOT spoken yet — their name must not lead any line.
        assert f"{HUMAN_NAME}:" not in before_submit

        app.exit()
    assert app.is_running is False
