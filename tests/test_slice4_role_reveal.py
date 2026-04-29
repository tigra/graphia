"""Slice 4 tests: the human sees their private role reveal, publicly silent.

Asserts:
  1. `GRAPHIA_SEED` determines the human's role deterministically. Seeds are
     chosen so one produces `mafia` and the other `law_abiding` for the
     human's insertion-order slot (index 0).
  2. The role-reveal string lands in `#private-log` only — never `#public-log`
     (no leak of private state to other observers).
  3. End of run, the `players` map on graph state has exactly 2 mafia and 5
     law-abiding roles (the canonical 7-player split).

The whole suite stubs `get_haiku` via the `fake_haiku` fixture so nothing
touches real Bedrock. The LLM boundary is the only non-determinism at this
slice and the seeded `random.Random(config.seed)` in `assign_roles` makes the
role deal reproducible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.widgets import Input, RichLog

from graphia.llm import DayAction, Pointing
from graphia.ui.app import GraphiaApp

# The six fake AI names used when stubbing the Haiku call. Single letters keep
# the test focused on the role-reveal mechanics (they still satisfy Roster's
# min_length=6/distinct/non-empty validators).
AI_NAMES = ["A", "B", "C", "D", "E", "F"]
HUMAN_NAME = "Alice"


# Seeds enumerated empirically (0..60) against the shared role deck
# [mafia, mafia, law_abiding, law_abiding, law_abiding, law_abiding,
# law_abiding]: the human occupies insertion-order slot 0, so deck[0] after
# the seeded shuffle IS the human's role. Smallest seed per role below.
SEED_LAW_ABIDING = 0
SEED_MAFIA = 3


async def _wait_for(
    pilot,
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until truthy, awaiting ``pilot.pause`` each tick."""
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
    """Flatten a RichLog's lines to a single plain string (no Rich markup)."""
    parts: list[str] = []
    for line in widget.lines:
        text = getattr(line, "text", None)
        if text is None:
            text = str(line)
        parts.append(text)
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("seed", "expected_role_label", "other_role_label"),
    [
        (SEED_LAW_ABIDING, "Law-abiding Citizen", "Mafia"),
        (SEED_MAFIA, "Mafia", "Law-abiding Citizen"),
    ],
    ids=["seed-law-abiding", "seed-mafia"],
)
async def test_private_role_reveal_by_seed(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
    expected_role_label: str,
    other_role_label: str,
) -> None:
    """Seed pins the human's role; reveal lands private, never public."""
    monkeypatch.setenv("GRAPHIA_SEED", str(seed))
    fake_haiku(AI_NAMES)
    # Defensive stub: after role reveal the graph immediately enters
    # Night 1 and calls ``get_sonnet()`` for Mafia pointing. Without this
    # the worker would drive through real Bedrock (dummy creds → boto3
    # retries) and keep an executor thread alive past ``app.exit()``.
    # A placeholder ``Pointing`` triggers ``_ai_pick_target``'s seeded
    # fallback — no race, no hang. FakeSonnetUnified replays the last
    # popped value across subsequent invocations.
    fake_sonnet(
        pointings=[Pointing(target_id="placeholder")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Wait for the Input to be enabled (graph has reached `collect_name`).
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

        private_log = app.query_one("#private-log", RichLog)
        public_log = app.query_one("#public-log", RichLog)

        expected_reveal = (
            f"You are {HUMAN_NAME}. Your role is {expected_role_label}."
        )

        def _reveal_rendered() -> bool:
            return expected_reveal in _rich_log_text(private_log)

        await _wait_for(pilot, _reveal_rendered, timeout=5.0)

        # Allow any trailing super-steps to flush before state checks.
        await pilot.pause()
        await pilot.pause()

        private_rendered = _rich_log_text(private_log)
        public_rendered = _rich_log_text(public_log)

        # 1. Private panel has exactly this run's role-reveal string.
        assert expected_reveal in private_rendered
        # The *other* role label must NOT also leak into the private panel —
        # guards against accidental double-reveal or message duplication.
        wrong_reveal = (
            f"You are {HUMAN_NAME}. Your role is {other_role_label}."
        )
        assert wrong_reveal not in private_rendered

        # 2. Role reveal never leaks to the public log (neither role label,
        #    nor the "You are …" sentence prefix).
        assert "Your role is" not in public_rendered, (
            "private role information leaked into the public log"
        )
        assert expected_reveal not in public_rendered
        assert f"You are {HUMAN_NAME}" not in public_rendered

        # 3. Underlying graph state has the canonical 2/4 role split.
        graph = getattr(app, "_graph", None)
        run_config = getattr(app, "_run_config", None)
        # Both attributes are set by GraphiaApp._drive(); the Slice-2 test
        # already relies on their presence, so failing to find them here is
        # a real regression — assert rather than skip.
        assert graph is not None, "GraphiaApp._graph was not set"
        assert run_config is not None, "GraphiaApp._run_config was not set"

        players = graph.get_state(run_config).values["players"]
        assert len(players) == 7
        mafia_count = sum(1 for p in players.values() if p.role == "mafia")
        law_count = sum(1 for p in players.values() if p.role == "law_abiding")
        assert mafia_count == 2, (
            f"expected 2 mafia, got {mafia_count} "
            f"({[p.role for p in players.values()]})"
        )
        assert law_count == 5, (
            f"expected 5 law_abiding, got {law_count} "
            f"({[p.role for p in players.values()]})"
        )

        # And the human's role on state matches what the private panel showed.
        human_id = graph.get_state(run_config).values["human_id"]
        human = players[human_id]
        expected_state_role = (
            "mafia" if expected_role_label == "Mafia" else "law_abiding"
        )
        assert human.role == expected_state_role

        await pilot.press("q")
    assert app.is_running is False
