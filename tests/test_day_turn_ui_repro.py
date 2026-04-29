"""Regression test: the UI unfreezes after the human's first Day speech.

The reported bug was that after submitting a day_turn, no AI players spoke
and no further messages appeared. This drives the full Textual UI through
the first human day_turn and asserts the public log continues to update
with at least one AI speech AND that another day_turn prompt eventually
appears (proving the driver kept pumping super-steps).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input, RichLog

from graphia.llm import DayAction
from graphia.ui.app import GraphiaApp

HUMAN_NAME = "Alice"
AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]


def _rich_log_text(widget: RichLog) -> str:
    parts: list[str] = []
    for line in widget.lines:
        t = getattr(line, "text", None) or str(line)
        parts.append(t)
    return "\n".join(parts)


async def _wait(pilot, pred, timeout: float = 15.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        result = pred()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(f"{pred!r} timed out after {timeout}s")
        await pilot.pause(interval)


async def test_ui_unfreezes_after_first_day_turn(
    env: Path,
    fake_haiku,
    fake_sonnet,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the human's first day_turn submission, AI turns must continue.

    Regression guard for a driver-side stall where ``drive_graph`` could
    return early after a ``Command(resume=...)`` super-step if the
    checkpoint state transiently exposed ``.next`` without a matching
    interrupt on ``.tasks[*].interrupts``.

    Uses the unified ``fake_sonnet`` fixture plus ``dynamic_night_pointing``
    so both Night pointing AND Day speaking calls route through fakes with
    no race — the separate per-phase fixtures previously left Day speaking
    unstubbed on the first Night's path, which could strand a boto3 retry
    thread past ``app.exit()``.
    """
    monkeypatch.setenv("GRAPHIA_SEED", "0")
    fake_haiku(AI_NAMES)
    # Install the unified Sonnet fake up front — DayAction queue carries
    # 80 scripted lines so the Day loop has plenty to consume before the
    # test stops driving.
    fake_sonnet(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"AI-speaks-{i}") for i in range(80)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Dynamic night-pointing fake resolves the real target id at
        # ``invoke`` time via live graph state — avoids racing the worker.
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        # Enter name at the first interrupt.
        await _wait(
            pilot,
            lambda: not app.query_one("#player-input", Input).disabled,
            timeout=5.0,
        )
        app.query_one("#player-input", Input).focus()
        await pilot.press(*HUMAN_NAME)
        await pilot.press("enter")

        # Wait for the roster so assertions below can reference player ids.
        await _wait(
            pilot,
            lambda: len(
                app._graph.get_state(app._run_config).values.get("players", {})
            )
            == 7,
            timeout=5.0,
        )

        # Wait for the first human day_turn prompt.
        await _wait(
            pilot,
            lambda: not app.query_one("#player-input", Input).disabled,
            timeout=15.0,
        )

        public_log = app.query_one("#public-log", RichLog)
        before_submit = _rich_log_text(public_log)

        # Submit the human's first Day speech.
        await pilot.press(*"I suspect Marco")
        await pilot.press("enter")

        # The log must grow with Alice's line plus at least one AI speech
        # *after* her line — proving the graph kept producing super-steps.
        def _progressed() -> bool:
            after = _rich_log_text(public_log)
            if after == before_submit:
                return False
            if f"{HUMAN_NAME}: I suspect Marco" not in after:
                return False
            return "AI-speaks-" in after.split("I suspect Marco", 1)[1]

        await _wait(pilot, _progressed, timeout=15.0)

        # A second human day_turn prompt (or the Day close) must eventually
        # arrive, proving the driver kept pumping past the first resume.
        await _wait(
            pilot,
            lambda: _rich_log_text(public_log).count("It's your turn, Alice.")
            >= 2
            or "The Day ends with no one executed." in _rich_log_text(public_log),
            timeout=15.0,
        )

        app.exit()
    assert app.is_running is False
