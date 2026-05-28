"""Spec 006 Slice 5 UI tests: recording an *abandoned* game on quit.

When the player quits a game that is genuinely in progress, ``GraphiaApp``
folds an ``outcome="abandoned"`` :class:`GameSummary` into the career via the
injected :class:`StatsStore`. The guard in ``_on_quit_decision`` only records
when a game has started (``_human_id`` observed from the graph stream), is not
already at END (``_game_over``), and has not already been folded
(``_career_recorded``). A ``self._career_recorded`` flag set on both the
normal-end path and the abandoned path prevents a double record.

These tests drive the real ``GraphiaApp`` through Textual's ``App.run_test``
pilot, mirroring ``tests/test_quit_modal.py``: ``GRAPHIA_ROLE=law-abiding``
pins the human's role, ``fake_haiku`` covers roster generation, and
``fake_sonnet`` pre-loads scripted Day/Night queues so the driver never
reaches real Bedrock. The first ``interrupt()`` is the *name* prompt, which
fires before ``human_id`` exists — quitting there would record nothing. To
reach a genuinely in-progress, recordable game, ``_advance_to_in_progress``
submits the human's name and pumps the worker through setup until it parks at
the human's Day turn; by then ``collect_name`` / ``assign_roles`` have
streamed ``human_id`` and ``players`` into the state mirror, so the abandoned
summary has a real role to read.

The cases mirror the four guarded branches in ``_on_quit_decision`` /
``action_abort``:

* Esc-confirm during an in-progress game → exactly one ``"abandoned"`` record.
* Ctrl+C (``action_abort``) during a game → zero records.
* Esc on the end screen / after a normal end (``_game_over`` /
  ``_career_recorded`` set) → no second record.
* Esc but cancelled ("No") → zero records.

Out of scope: the AgentCore remote store (a later slice). No test touches real
Bedrock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.widgets import Input

from graphia.llm import DayAction, Pointing
from graphia.stats_store import CareerStats, GameSummary
from graphia.ui.app import GraphiaApp
from graphia.ui.quit_modal import QuitModal

# Six AIs + the human round out the seven-player table. The human's name is
# submitted by ``_advance_to_in_progress`` so the game genuinely starts
# (``human_id`` + roles streamed into the state mirror) before quit.
AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"


class _CapturingStatsStore:
    """In-memory ``StatsStore`` that records every ``record()`` call.

    ``load`` reports a zeroed (first-run) career so the launch greeting is the
    first-run form and no filesystem is touched. ``record`` folds nothing — it
    simply captures each :class:`GameSummary` so a test can assert how many
    games were recorded and with what outcome.
    """

    def __init__(self) -> None:
        self.recorded: list[GameSummary] = []

    def load(self) -> CareerStats:
        return CareerStats()

    def record(self, summary: GameSummary) -> CareerStats:
        self.recorded.append(summary)
        return CareerStats(games_total=len(self.recorded))


# --------------------------------------------------------------------------
# Pilot helpers — mirror the lightweight patterns from
# ``tests/test_quit_modal.py`` so this file stays self-contained.
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


async def _wait_for_quit_modal(app: GraphiaApp, pilot) -> QuitModal:
    """Wait until ``QuitModal`` is the top-of-stack screen and return it."""

    async def _ready() -> bool:
        return isinstance(app.screen, QuitModal)

    await _wait_for(pilot, _ready, timeout=2.0)
    return app.screen  # type: ignore[return-value]


async def _wait_until_idle(app: GraphiaApp, pilot) -> None:
    """Pump the pilot until the worker is parked at the first interrupt."""

    async def _ready() -> bool:
        return app._pending_resume is not None and not app._pending_resume.done()

    await _wait_for(pilot, _ready, timeout=5.0)


async def _input_enabled(app: GraphiaApp) -> bool:
    """True once the docked ``#player-input`` is mounted and accepting input."""
    try:
        prompt = app.query_one("#player-input", Input)
    except Exception:  # noqa: BLE001
        return False
    return prompt.disabled is False


async def _advance_to_in_progress(
    app: GraphiaApp, pilot, dynamic_night_pointing
) -> None:
    """Submit the human's name and pump until parked at the Day turn.

    The recordable in-progress state requires ``_human_id`` to be observed
    (set by ``collect_name``) *and* the human to have a role in ``players``
    (set by ``assign_roles``). Both are streamed into the state mirror once the
    name is submitted, after which the graph runs Night → Day and parks on the
    human's Day-turn interrupt. The human is pinned Law-abiding, so no
    PointingModal/VoteModal pops — but the AI Mafia's Night kill targets a
    Law-abiding player, which *could* be the human (then no Day turn ever
    fires). ``dynamic_night_pointing`` overrides the Night-side binding to
    always pick a Law-abiding *non-human*, keeping the human alive to the Day
    turn. Waiting for ``_human_id`` plus a re-enabled input proves we are at
    that second human prompt with a fully-determined game — exactly the state
    ``_on_quit_decision`` is meant to record.
    """
    await _wait_until_idle(app, pilot)
    # The graph is built by now; wire the race-safe Night-pointing fake so the
    # AI Mafia never kills the human before the Day turn.
    dynamic_night_pointing(lambda: app._graph.get_state(app._run_config).values)
    # First interrupt is the name prompt; submit the name.
    await _wait_for(pilot, lambda: _input_enabled(app), timeout=5.0)
    await pilot.press(*HUMAN_NAME)
    await pilot.press("enter")

    async def _ready() -> bool:
        return app._human_id is not None and await _input_enabled(app)

    await _wait_for(pilot, _ready, timeout=15.0)


@pytest.fixture
def capturing_store() -> _CapturingStatsStore:
    """A fresh in-memory capturing store per test."""
    return _CapturingStatsStore()


@pytest.fixture
def booted_app(
    env: Path,
    fake_haiku,
    fake_sonnet,
    capturing_store: _CapturingStatsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> GraphiaApp:
    """Build a ``GraphiaApp`` with the LLM surface stubbed and a capturing store.

    Returned ready-to-go but NOT yet running — the caller wraps it in
    ``app.run_test()``. Pinning the role and stubbing both LLMs is defensive:
    a pointing super-step may begin before the modal interactions land, and an
    unstubbed Sonnet call would fail loudly via the autouse ``safe_llm`` net.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(AI_NAMES)
    # AIs always speak (no VoteModal pops) and point at a placeholder target;
    # the unified fake replays the last scripted output once a queue drains, so
    # one entry each is enough to drive setup → Night → the human's Day turn.
    fake_sonnet(
        day_actions=[DayAction(kind="speak", text="Just watching for now.")],
        ballots=[],
        pointings=[Pointing(target_id="placeholder")],
    )
    return GraphiaApp(stats_store=capturing_store)


# --------------------------------------------------------------------------
# Case 3 — Esc-confirm during an in-progress game records exactly one
# "abandoned" summary.
# --------------------------------------------------------------------------


async def test_esc_confirm_in_progress_records_abandoned(
    booted_app: GraphiaApp,
    capturing_store: _CapturingStatsStore,
    dynamic_night_pointing,
) -> None:
    """Confirming the quit modal mid-game folds exactly one abandoned game."""
    app = booted_app
    async with app.run_test() as pilot:
        await _advance_to_in_progress(app, pilot, dynamic_night_pointing)
        assert app._game_over is False
        assert app._career_recorded is False

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)
        await pilot.press("y")
        await pilot.pause(0.1)

    assert app.is_running is False
    assert len(capturing_store.recorded) == 1, (
        f"expected exactly one recorded game, got {capturing_store.recorded!r}"
    )
    summary = capturing_store.recorded[0]
    assert summary.outcome == "abandoned"
    # Role pinned to law-abiding via GRAPHIA_ROLE.
    assert summary.human_role == "law_abiding"
    assert summary.human_won is False
    # The flag guards against a second record (e.g. a trailing Esc).
    assert app._career_recorded is True


# --------------------------------------------------------------------------
# Case 4 — Ctrl+C (action_abort) records nothing.
# --------------------------------------------------------------------------


async def test_ctrl_c_in_progress_records_nothing(
    booted_app: GraphiaApp,
    capturing_store: _CapturingStatsStore,
    dynamic_night_pointing,
) -> None:
    """Ctrl+C exits without folding any game into the career."""
    app = booted_app
    async with app.run_test() as pilot:
        await _advance_to_in_progress(app, pilot, dynamic_night_pointing)
        assert app._game_over is False

        await pilot.press("ctrl+c")
        await pilot.pause(0.1)

    assert app.is_running is False
    assert capturing_store.recorded == [], (
        f"Ctrl+C must record nothing; got {capturing_store.recorded!r}"
    )
    assert app._career_recorded is False


async def test_ctrl_c_while_quit_modal_open_records_nothing(
    booted_app: GraphiaApp,
    capturing_store: _CapturingStatsStore,
    dynamic_night_pointing,
) -> None:
    """Ctrl+C while the quit modal is open still records nothing.

    ``action_abort`` is a priority binding that fires regardless of which
    modal is on screen; it must bypass the abandoned-record path entirely.
    """
    app = booted_app
    async with app.run_test() as pilot:
        await _advance_to_in_progress(app, pilot, dynamic_night_pointing)

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)

        await pilot.press("ctrl+c")
        await pilot.pause(0.1)

    assert app.is_running is False
    assert capturing_store.recorded == [], (
        f"Ctrl+C must record nothing even over the modal; "
        f"got {capturing_store.recorded!r}"
    )


# --------------------------------------------------------------------------
# Case 6 — Esc but cancelled ("No") records nothing.
# --------------------------------------------------------------------------


async def test_esc_cancelled_records_nothing(
    booted_app: GraphiaApp,
    capturing_store: _CapturingStatsStore,
    dynamic_night_pointing,
) -> None:
    """Dismissing the quit modal with "No" leaves the career untouched."""
    app = booted_app
    async with app.run_test() as pilot:
        await _advance_to_in_progress(app, pilot, dynamic_night_pointing)

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)

        await pilot.press("n")

        async def _modal_gone() -> bool:
            return not isinstance(app.screen, QuitModal)

        await _wait_for(pilot, _modal_gone, timeout=2.0)

        # Still in the running game; nothing recorded.
        assert app.is_running is True
        assert app._game_over is False
        assert app._career_recorded is False
        assert capturing_store.recorded == [], (
            f"cancelling the quit must record nothing; "
            f"got {capturing_store.recorded!r}"
        )


# --------------------------------------------------------------------------
# Case 5 — Esc after the game has ended / been recorded: no second record.
#
# Driving a full game to its END inside the pilot is exercised elsewhere
# (test_career_stats.py::test_ui_panel_written_then_second_app_greets_cumulative).
# Here we exercise the guard precisely: with ``_game_over`` and/or
# ``_career_recorded`` set — the post-end / already-recorded state — confirming
# the quit modal must NOT record a second game. We set those flags directly on
# the live app (the values the END path would have set) rather than re-driving
# a whole game, keeping the test fast and focused on the guard branch.
# --------------------------------------------------------------------------


async def test_esc_confirm_after_game_over_records_nothing(
    booted_app: GraphiaApp,
    capturing_store: _CapturingStatsStore,
    dynamic_night_pointing,
) -> None:
    """With ``_game_over`` True, an Esc-confirm records no abandoned game.

    Note: ``on_key`` exits on *any* key once ``_game_over`` is True, so a real
    Esc keypress would exit before the modal even opens. We therefore invoke
    the dismiss callback directly with ``confirm=True`` — the precise way to
    exercise the ``not self._game_over`` guard in ``_on_quit_decision``.
    """
    app = booted_app
    async with app.run_test() as pilot:
        await _advance_to_in_progress(app, pilot, dynamic_night_pointing)

        # Simulate the post-END state the normal-end path would leave behind.
        app._game_over = True

        await app._on_quit_decision(True)
        await pilot.pause(0.1)

    assert capturing_store.recorded == [], (
        f"Esc-confirm after game over must record nothing; "
        f"got {capturing_store.recorded!r}"
    )


async def test_esc_confirm_after_already_recorded_records_nothing(
    booted_app: GraphiaApp,
    capturing_store: _CapturingStatsStore,
    dynamic_night_pointing,
) -> None:
    """With ``_career_recorded`` True, an Esc-confirm does not double-record.

    Mirrors the case where a normal end already folded the game (setting
    ``_career_recorded``) and the player then presses Esc on the end screen.
    The flag short-circuits the abandoned path, so no second game is folded.
    """
    app = booted_app
    async with app.run_test() as pilot:
        await _advance_to_in_progress(app, pilot, dynamic_night_pointing)

        # The normal-end path would have set this after folding the win/loss.
        app._career_recorded = True

        await app._on_quit_decision(True)
        await pilot.pause(0.1)

    assert capturing_store.recorded == [], (
        f"Esc-confirm after a game was already recorded must not double-record; "
        f"got {capturing_store.recorded!r}"
    )
