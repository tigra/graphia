"""Slice 2 roster tests: drive GraphiaApp through the setup graph with a stub name.

Asserts that once the human submits a name, the Moderator's roster-intro line
contains the human + all 6 hardcoded AI names in a single line, and that the
underlying graph state agrees.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from textual.widgets import Input, RichLog

from graphia.llm import DayAction, Pointing
from graphia.prompts import ROSTER_INTRO_TEMPLATE
from graphia.ui.app import GraphiaApp

HARDCODED_AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]
HUMAN_NAME = "Alice"

# Fixed marker from ROSTER_INTRO_TEMPLATE, without the templated `{names}` slot.
# Using the literal prefix lets us locate the single line the Moderator emits
# without duplicating the template text in the test.
ROSTER_INTRO_PREFIX = ROSTER_INTRO_TEMPLATE.split("{names}")[0]


def _rich_log_text(widget: RichLog) -> str:
    """Flatten a RichLog's accumulated lines into a single plain string.

    RichLog stores each `write(renderable)` as a `Strip`-ready entry in `lines`.
    We want the plain text the user sees, with one newline per written line so
    we can later slice by line and assert "all names appeared in the same line".
    """
    parts: list[str] = []
    for line in widget.lines:
        # `Strip` exposes a `.text` property with the plain text of that row.
        text = getattr(line, "text", None)
        if text is None:
            text = str(line)
        parts.append(text)
    return "\n".join(parts)


async def _wait_for(
    pilot,
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until truthy, awaiting ``pilot.pause(interval)`` each tick.

    Raises ``TimeoutError`` if the predicate never becomes truthy within
    ``timeout`` seconds. Supports both sync and async predicates.
    """
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


async def test_roster_intro_contains_all_seven_names_in_one_line(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch,
) -> None:
    # Pin GRAPHIA_SEED=0 so the human is Law-abiding (roster slot 0) and
    # the ``mafia_pointing`` super-step never blocks on the human-Mafia
    # modal interrupt. Without this, the default seed is
    # ``time.time_ns()``; a random Mafia draw makes the worker wait on a
    # modal the test never drives, which strands the producer thread and
    # bloats teardown by up to 300s.
    monkeypatch.setenv("GRAPHIA_SEED", "0")
    # Slice 3 replaced the hardcoded list with a live Haiku call. Pin the
    # fake to the original Slice-2 names so the rest of this test's
    # assertions still hold.
    fake_haiku(HARDCODED_AI_NAMES)
    # After the roster intro the graph chains into Night-1 Mafia pointing,
    # which binds ``get_sonnet()``. Without this stub the worker would
    # reach real Bedrock with dummy creds, triggering boto3 retries that
    # keep an executor thread alive past ``app.exit()`` and blocking
    # pytest teardown on the 300s executor-join timeout.
    # A placeholder ``Pointing`` triggers the production seeded fallback
    # in ``_ai_pick_target`` — safer than racing to script real target
    # ids before the worker invokes Sonnet. FakeSonnetUnified replays the
    # last popped value on subsequent invocations.
    fake_sonnet(
        pointings=[Pointing(target_id="placeholder")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )
    app = GraphiaApp()
    async with app.run_test() as pilot:
        # The app starts a worker that drives the graph. The first super-step
        # hits the `collect_name` interrupt, which enables the Input and makes
        # it focusable. Wait for that to happen before typing.
        async def _input_enabled() -> bool:
            try:
                prompt = app.query_one("#player-input", Input)
            except Exception:  # noqa: BLE001 — widget not mounted yet
                return False
            return prompt.disabled is False

        await _wait_for(pilot, _input_enabled, timeout=5.0)

        prompt = app.query_one("#player-input", Input)
        prompt.focus()
        await pilot.press(*HUMAN_NAME)
        await pilot.press("enter")

        log = app.query_one("#public-log", RichLog)

        # Wait until the roster-intro line is visible in the public log.
        def _intro_rendered() -> bool:
            return ROSTER_INTRO_PREFIX in _rich_log_text(log)

        await _wait_for(pilot, _intro_rendered, timeout=5.0)

        # Give the graph a couple more ticks to finish any trailing super-steps.
        await pilot.pause()
        await pilot.pause()

        rendered = _rich_log_text(log)

        # 1. Human and every hardcoded AI name must appear somewhere in the log.
        assert HUMAN_NAME in rendered, f"{HUMAN_NAME!r} missing from public log"
        for ai_name in HARDCODED_AI_NAMES:
            assert ai_name in rendered, f"AI name {ai_name!r} missing from public log"

        # 2. The roster-intro template prefix must appear.
        assert ROSTER_INTRO_PREFIX in rendered

        # 3. All 7 names must appear inside the *same* roster-intro line.
        intro_line = next(
            (line for line in rendered.splitlines() if ROSTER_INTRO_PREFIX in line),
            None,
        )
        assert intro_line is not None, "roster-intro line not found in public log"
        for name in [HUMAN_NAME, *HARDCODED_AI_NAMES]:
            assert name in intro_line, (
                f"{name!r} not on the same line as the roster-intro "
                f"(line was: {intro_line!r})"
            )

        # 4. Underlying graph state has 6 players, exactly one human.
        #    The app exposes neither `_graph` nor `_run_config` publicly, but
        #    we can reach through the attributes that `_drive()` creates on
        #    `GraphiaApp`. If they aren't present we skip this assertion
        #    (production code must not be modified to add test hooks).
        graph = getattr(app, "_graph", None)
        run_config = getattr(app, "_run_config", None)
        if graph is not None and run_config is not None:
            state_values = graph.get_state(run_config).values
            players = state_values.get("players", {})
            assert len(players) == 7, (
                f"expected 7 players in state, got {len(players)}"
            )
            humans = [p for p in players.values() if p.is_human]
            assert len(humans) == 1, (
                f"expected exactly 1 human player, got {len(humans)}"
            )
            assert humans[0].name == HUMAN_NAME

        await pilot.press("q")
    assert app.is_running is False
