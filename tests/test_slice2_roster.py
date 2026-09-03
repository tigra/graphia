"""Slice 2 roster tests: drive GraphiaApp through the setup graph with a stub name.

Asserts that once the human submits a name, the Moderator's roster-intro
message seats **exactly** the table the resolved config describes — the human
plus one AI per remaining seat, no more — and that the underlying graph state
agrees.

Spec 042, Task 3.4: this used to loop over the six names it had scripted,
asserting each appeared. That form cannot see an **extra** member, so it passed
over an eight-seat table whose eighth name (a fixture-extended ``Kappa-Extra``,
or in production a coerced ``Player-1`` placeholder) it had never heard of. The
cardinality assertion below is what turns that silent padding into a failure.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable

from textual.widgets import Input, RichLog

from graphia.config import load_config
from graphia.llm import DayAction, Pointing
from graphia.nodes.setup import ai_name_count
from graphia.prompts import ROSTER_INTRO_TEMPLATE
from graphia.ui.app import GraphiaApp

# A *pool* the roster fake draws from, not a lineup-sized script (spec 042
# §2.2): the fake answers every call with as many names as
# ``ai_name_count(load_config())`` asks for, consuming this list in order and
# topping up from its own reserve when the table wants more. So the names that
# must appear are the first ``ai_name_count(config)`` of these — derived below,
# never counted by hand.
HARDCODED_AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]
HUMAN_NAME = "Alice"

# Fixed markers from ROSTER_INTRO_TEMPLATE, either side of the templated
# `{names}` slot. Using the literal prefix/suffix lets us locate *and delimit*
# the single message the Moderator emits without duplicating the template text.
ROSTER_INTRO_PREFIX, ROSTER_INTRO_SUFFIX = ROSTER_INTRO_TEMPLATE.split("{names}")

# ``graphia.nodes.setup._coerce_to_count`` pads a short roster up to the
# required count with ``Player-{k}`` placeholders — the one way an unnoticed
# extra seat actually reaches a real table. Nothing else at the table is named
# this way: the roster fake's extension names (``Kappa-Extra`` from its
# reserve, ``Understudy-NNN`` as a last resort) are deliberately *not*
# ``Player-``-shaped, so this prefix discriminates a production-coerced roster
# from a fixture-extended one.
PLACEHOLDER_PREFIX = "Player-"


def _roster_intro_names(rendered: str) -> list[str]:
    """Every name the Moderator's roster-intro message actually seats.

    Reads the *logical* message rather than a physical row. ``#public-log`` is
    a ``RichLog`` with ``wrap=True`` on an 80-column test terminal, and the
    name list spills onto a second row as soon as the table grows — at eight
    players the eighth name lands there, which is exactly how the old
    "every name I scripted is on the same line" loop stayed green while
    ignoring the extra seat. Slicing between the template's fixed prefix and
    suffix and collapsing whitespace rejoins the wrapped rows; Rich folds on
    whitespace, so a name is never split across the boundary.

    The suffix search runs against the whitespace-collapsed text on purpose:
    at seven players the wrap falls *between* the last name and the suffix
    (``"…Aarav.\nLet the game begin."``), so the literal ``". Let"`` only
    matches after normalisation. Finding the suffix at all is the (now
    explicit) claim that the whole roster is one contiguous Moderator
    message.
    """
    start = rendered.find(ROSTER_INTRO_PREFIX)
    assert start != -1, (
        f"roster-intro line not found in public log:\n{rendered}"
    )
    flat = re.sub(r"\s+", " ", rendered[start + len(ROSTER_INTRO_PREFIX) :])
    end = flat.find(ROSTER_INTRO_SUFFIX)
    assert end != -1, (
        f"roster-intro message never closed with {ROSTER_INTRO_SUFFIX!r}; "
        f"the roster may not be a single Moderator message. Log was:\n"
        f"{rendered}"
    )
    return [name.strip() for name in flat[:end].split(",")]


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


async def test_roster_intro_seats_exactly_the_configured_table(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch,
) -> None:
    # Pin the human as Law-abiding so the ``mafia_pointing`` super-step never
    # blocks on the human-Mafia modal interrupt. Without this pin, a random
    # Mafia draw makes the worker wait on a modal the test never drives,
    # which strands the producer thread and bloats teardown by up to 300s.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Slice 3 replaced the hardcoded list with a live small-model call. Pin the
    # fake to the original Slice-2 names so the rest of this test's
    # assertions still hold.
    fake_small(HARDCODED_AI_NAMES)
    # After the roster intro the graph chains into Night-1 Mafia pointing,
    # which binds ``get_large()``. Without this stub the worker would
    # reach real Bedrock with dummy creds, triggering boto3 retries that
    # keep an executor thread alive past ``app.exit()`` and blocking
    # pytest teardown on the 300s executor-join timeout.
    # A placeholder ``Pointing`` triggers the production random fallback
    # in ``_ai_pick_target`` — safer than racing to script real target
    # ids before the worker invokes the large model. FakeLargeUnified replays the
    # last popped value on subsequent invocations.
    fake_large(
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

        # Everything below is sized from the *resolved* config, never from a
        # literal: ``num_citizens + num_mafia`` are whole-table counts with the
        # human included, so ``ai_name_count`` is that total minus the human
        # seat. Read here rather than at import time because sibling modules
        # override the lineup per test via ``monkeypatch.setenv``.
        config = load_config()
        expected_total = config.num_citizens + config.num_mafia
        # The roster fake consumes the pool in order, so the names that must
        # appear are its first ``ai_name_count(config)`` entries; a table
        # bigger than the pool is topped up from the fixture's own reserve.
        expected_pool_names = HARDCODED_AI_NAMES[: ai_name_count(config)]

        # 1. Human and every AI name the pool was asked for must appear
        #    somewhere in the log.
        assert HUMAN_NAME in rendered, f"{HUMAN_NAME!r} missing from public log"
        for ai_name in expected_pool_names:
            assert ai_name in rendered, f"AI name {ai_name!r} missing from public log"

        # 2. The roster-intro template prefix must appear.
        assert ROSTER_INTRO_PREFIX in rendered

        # 3. The roster-intro message names **exactly** the configured table.
        #    The cardinality assertion is the load-bearing one (spec 042, Task
        #    3.4): the membership loop that follows it only looks for names
        #    this test already knows, so on its own it cannot see an extra
        #    seat — and one extra seat is precisely what a coerced roster
        #    quietly adds.
        intro_names = _roster_intro_names(rendered)
        assert len(intro_names) == expected_total, (
            f"roster-intro line should name exactly {expected_total} players "
            f"({config.num_citizens} citizens + {config.num_mafia} mafia, "
            f"human included), but named {len(intro_names)}: {intro_names!r}"
        )
        for name in [HUMAN_NAME, *expected_pool_names]:
            assert name in intro_names, (
                f"{name!r} is not one of the names on the roster-intro line "
                f"(line named: {intro_names!r})"
            )
        #    No production placeholder may reach the table. ``_coerce_to_count``
        #    pads a short roster with ``Player-{k}``, which is the specific
        #    degradation the cardinality check above is here to catch; naming it
        #    explicitly says so to the next reader. The fake's own extension
        #    names are not ``Player-``-shaped, so this stays discriminating.
        assert not [n for n in intro_names if n.startswith(PLACEHOLDER_PREFIX)], (
            f"a coerced {PLACEHOLDER_PREFIX}N placeholder reached the roster: "
            f"{intro_names!r}"
        )
        assert PLACEHOLDER_PREFIX not in rendered, (
            f"a coerced {PLACEHOLDER_PREFIX}N placeholder appears in the "
            f"public log:\n{rendered}"
        )

        # 4. Underlying graph state seats the same configured table, with
        #    exactly one human.
        #    The app exposes neither `_graph` nor `_run_config` publicly, but
        #    we can reach through the attributes that `_drive()` creates on
        #    `GraphiaApp`. If they aren't present we skip this assertion
        #    (production code must not be modified to add test hooks).
        graph = getattr(app, "_graph", None)
        run_config = getattr(app, "_run_config", None)
        if graph is not None and run_config is not None:
            state_values = graph.get_state(run_config).values
            players = state_values.get("players", {})
            assert len(players) == expected_total, (
                f"expected {expected_total} players in state, "
                f"got {len(players)}"
            )
            humans = [p for p in players.values() if p.is_human]
            assert len(humans) == 1, (
                f"expected exactly 1 human player, got {len(humans)}"
            )
            assert humans[0].name == HUMAN_NAME

        await pilot.press("q")
    assert app.is_running is False
