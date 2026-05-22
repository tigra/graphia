"""End-to-end pilot tests for the QuitModal.

The thing under test (built by Slice 1 Subs 1.1 and 1.2; Slice 2's
``q``-as-priority-binding was reversed — see the Sub 2.3 cleanup):

* ``src/graphia/ui/quit_modal.py`` — :class:`QuitModal`, a
  :class:`ModalScreen` that dismisses with ``True`` on confirm
  (``y`` / Enter / **Yes**) and ``False`` on cancel (``n`` / Esc / **No**).
* ``src/graphia/ui/app.py`` — ``GraphiaApp`` binds priority
  ``escape -> action_request_quit``, which pushes :class:`QuitModal` via
  ``push_screen(QuitModal(), self._on_quit_decision)``. The action is
  guarded by ``isinstance(self.screen, ModalScreen): return`` so a quit
  modal is never stacked on top of an existing modal. ``q`` is NOT bound
  — it is treated as a plain printable character per functional spec
  section 2.1a.

These tests drive the real ``GraphiaApp`` through Textual's ``App.run_test``
pilot. To keep them fast and deterministic the LangGraph startup path is
short-circuited: ``GRAPHIA_SEED=0`` pins role assignment, ``fake_haiku``
covers the roster generation, and ``fake_sonnet`` pre-loads scripted
day/night outputs so the driver never reaches real Bedrock. No test
submits the human's name — pausing the worker on the very first
``interrupt()`` (the name prompt) is enough for the modal to be opened
against a live, idle game.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.screen import ModalScreen
from textual.widgets import Input

from graphia.ui.app import GraphiaApp
from graphia.ui.failure_modal import FailureModal
from graphia.ui.quit_modal import QuitModal

# Seed 0 pins the human as Law-abiding (matches the rest of the suite).
# We never advance past the name prompt in these tests, but the seed is
# still set so role assignment is fully deterministic if any future code
# path samples it during boot.
SEED_LAW_ABIDING = 0

# Roster names handed to the Haiku fake. Six AIs + the human round out
# the seven-player table; the human's name is never submitted in any of
# these tests, so role labels here are inert.
AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]


# --------------------------------------------------------------------------
# Pilot helpers — mirror the lightweight patterns from
# ``tests/test_slice9_polish.py`` so this file stays self-contained (no
# cross-file imports between test modules).
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
    """Wait until ``QuitModal`` is the top-of-stack screen and return it.

    ``push_screen`` is asynchronous from the perspective of the pilot —
    one ``await pilot.pause()`` after the keystroke is normally enough,
    but on slower CI runs the screen swap can lag a tick or two. Polling
    keeps the test deterministic without resorting to fixed sleeps.
    """

    async def _ready() -> bool:
        return isinstance(app.screen, QuitModal)

    await _wait_for(pilot, _ready, timeout=2.0)
    return app.screen  # type: ignore[return-value]


async def _wait_until_idle(app: GraphiaApp, pilot) -> None:
    """Pump the pilot until the worker is parked at the first interrupt.

    The ``_drive`` worker reaches the name prompt very quickly, then
    blocks on ``_pending_resume`` waiting for an ``Input.Submitted``
    that never comes (no test submits the name). Once that future is
    set, the worker is idle and any keystroke driven by the pilot
    reaches the App's bindings cleanly.
    """

    async def _ready() -> bool:
        return app._pending_resume is not None and not app._pending_resume.done()

    await _wait_for(pilot, _ready, timeout=5.0)


@pytest.fixture
def booted_app(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> GraphiaApp:
    """Build a ``GraphiaApp`` with the LLM surface pre-stubbed.

    Returned ready-to-go but NOT yet running — the caller wraps it in
    ``app.run_test()`` to start the pilot. Pinning the seed and stubbing
    both LLMs is defensive: a Mafia-pointing super-step may begin before
    the test's modal interactions land, and an unstubbed Sonnet call
    would fail loudly via the autouse ``safe_llm`` net.
    """
    monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))
    fake_haiku(AI_NAMES)
    # Empty scripted queues are fine: ``FakeSonnetUnified`` will only be
    # invoked if the worker gets past the name prompt, which it never
    # does here (no name is ever submitted). Installing the fake still
    # matters — it overrides the loud-failure default from
    # ``safe_llm``'s autouse so a defensive Sonnet call from any future
    # boot-time code path does not crash teardown.
    fake_sonnet(pointings=[], day_actions=[], ballots=[])
    return GraphiaApp()


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


async def test_esc_opens_quit_modal(booted_app: GraphiaApp) -> None:
    """Pressing Esc on the idle main screen pushes :class:`QuitModal`."""
    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)
        await pilot.press("escape")
        modal = await _wait_for_quit_modal(app, pilot)
        assert isinstance(modal, QuitModal)


async def test_n_dismisses_quit_modal(booted_app: GraphiaApp) -> None:
    """``n`` on an open QuitModal dismisses it and the app keeps running."""
    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)

        await pilot.press("n")
        # Modal is dismissed: the active screen is no longer a QuitModal
        # (and no other modal was substituted in its place).
        async def _modal_gone() -> bool:
            return not isinstance(app.screen, QuitModal)

        await _wait_for(pilot, _modal_gone, timeout=2.0)

        assert not isinstance(app.screen, QuitModal)
        # The game is still alive: the Textual app loop has not exited,
        # and the crash/end-of-game flag was never flipped.
        assert app.is_running is True
        assert app._game_over is False


async def test_y_exits_via_quit_modal(booted_app: GraphiaApp) -> None:
    """``y`` on an open QuitModal exits the app cleanly."""
    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)

        await pilot.press("y")
        # Let Textual process the dismiss callback (``_on_quit_decision``
        # calls ``app.exit()``) before the context manager tears down.
        await pilot.pause(0.1)

    # After ``run_test`` exits the app must be fully stopped. This is the
    # same end-state assertion the other shutdown tests in this suite use
    # (``tests/test_app_boot.py`` and ``tests/test_slice9_polish.py``).
    assert app.is_running is False


async def test_esc_on_modal_dismisses(booted_app: GraphiaApp) -> None:
    """A second Esc on the open QuitModal cancels it (modal owns its Esc)."""
    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)

        # The modal's own ``escape -> action_cancel`` binding fires here.
        # The App's priority Esc binding is correctly NOT re-triggered
        # because the guard in ``action_request_quit`` short-circuits when
        # a ``ModalScreen`` is already on top.
        await pilot.press("escape")

        async def _modal_gone() -> bool:
            return not isinstance(app.screen, QuitModal)

        await _wait_for(pilot, _modal_gone, timeout=2.0)

        assert not isinstance(app.screen, QuitModal)
        # Crucially, the app is still running — the second Esc cancelled
        # the modal, it did NOT propagate up to a fresh quit confirmation.
        assert app.is_running is True
        assert app._game_over is False


async def test_ctrl_c_bypasses_modal(booted_app: GraphiaApp) -> None:
    """Ctrl+C while QuitModal is open exits immediately, ignoring the modal."""
    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)

        await pilot.press("escape")
        await _wait_for_quit_modal(app, pilot)

        # ``Binding("ctrl+c", "abort", show=False, priority=True)`` on
        # the App fires regardless of which modal is on the screen stack
        # — that is the whole point of ``priority=True`` for an abort
        # key. The modal does not get a chance to swallow it.
        await pilot.press("ctrl+c")
        await pilot.pause(0.1)

    assert app.is_running is False


async def test_quit_modal_not_stacked_over_existing_modal(
    booted_app: GraphiaApp,
) -> None:
    """``action_request_quit`` is a no-op while another modal is on screen.

    Verifies the ``isinstance(self.screen, ModalScreen)`` guard: pushing
    :class:`FailureModal` directly (the simplest pre-existing modal in
    the codebase) and then triggering the Esc action must NOT cause a
    second :class:`QuitModal` to land on top of it.

    The production guard yields the keystroke back to Textual's binding
    chain by raising :class:`SkipAction` (so the underlying modal's own
    ``escape`` binding gets a chance). When we call the action method
    directly — rather than press a key — that exception surfaces here,
    so the test catches it and asserts the post-condition (no QuitModal
    on the stack) from the post-call state.
    """
    from textual.actions import SkipAction

    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)

        # Push a FailureModal directly — equivalent to the real remote-mode
        # crash path, but without having to inject an exception. The
        # constructor's defensive ``isinstance`` guards on its kwargs accept
        # plain strings here.
        app.push_screen(
            FailureModal(
                thread_id="test-thread-id",
                log_group="/aws/test/log-group",
                error_summary="injected for guard test",
            )
        )

        async def _failure_modal_active() -> bool:
            return isinstance(app.screen, FailureModal)

        await _wait_for(pilot, _failure_modal_active, timeout=2.0)

        # Snapshot the screen-stack depth with FailureModal on top.
        depth_before = len(app.screen_stack)

        # Trigger the Esc action directly. Pressing Esc here would route
        # through both the App's priority binding AND (because the guard
        # yields with SkipAction) the FailureModal's own ``close``
        # binding — dismissing the FailureModal and obscuring the guard
        # we care about. Calling the action method is the precise way to
        # exercise the ``isinstance(self.screen, ModalScreen)`` guard
        # without the modal's own bindings interfering.
        with pytest.raises(SkipAction):
            app.action_request_quit()
        await pilot.pause(0.1)

        # The screen stack must not have grown — no QuitModal landed on
        # top of the FailureModal.
        assert len(app.screen_stack) == depth_before, (
            f"action_request_quit must not push a QuitModal while another "
            f"modal is active; stack depth went {depth_before} -> "
            f"{len(app.screen_stack)}"
        )
        # And the top screen is still the FailureModal we pushed, not a
        # newly-pushed QuitModal.
        assert isinstance(app.screen, FailureModal)
        # Explicit negative check — no QuitModal anywhere on the stack.
        for screen in app.screen_stack:
            assert not isinstance(screen, QuitModal), (
                f"found a QuitModal in the screen stack while a "
                f"FailureModal was already active: {screen!r}"
            )

        # Clean up: dismiss the FailureModal so the worker can wind down.
        app.exit()


# --------------------------------------------------------------------------
# Spec 003 Slice 2 rollback — ``q`` is no longer a priority binding.
#
# Slice 2 (Sub 2.1) briefly bound ``q`` to ``action_request_quit`` with
# ``priority=True``, with a custom ``GraphiaInput`` workaround so the
# focused input would not capture the keystroke. That whole approach was
# reversed (per functional spec section 2.1a): ``q`` is now a plain
# printable character. The binding was removed from ``GraphiaApp.BINDINGS``
# and the ``GraphiaInput`` workaround in ``widgets.py`` is gone — the app
# uses Textual's stock :class:`Input`.
#
# This single test pins the new contract: pressing ``q`` while the
# ``#player-input`` widget has focus must NOT open :class:`QuitModal`,
# and the keystroke must land in the input as a literal ``q`` character.
# --------------------------------------------------------------------------


async def test_q_does_not_open_quit_modal(booted_app: GraphiaApp) -> None:
    """``q`` is a normal printable character — it must NOT open QuitModal.

    The first interrupt is the name prompt, which enables and focuses
    the ``#player-input`` widget. Pressing ``q`` there must be treated
    as a literal character typed into the field; the quit modal stays
    closed. This locks in the Slice 2 rollback — Esc remains the sole
    way to open the quit confirmation.
    """
    app = booted_app
    async with app.run_test() as pilot:
        await _wait_until_idle(app, pilot)

        # Pre-condition: the input is focused at the first interrupt.
        # Without focus the "q is captured as text" half of the contract
        # is meaningless.
        prompt = app.query_one("#player-input", Input)
        assert prompt.has_focus, (
            "Expected #player-input to be focused at the first interrupt."
        )

        await pilot.press("q")
        # Give Textual a couple of ticks in case a (regressed) priority
        # binding tried to push the modal asynchronously.
        await pilot.pause(0.1)

        # The modal MUST NOT have opened — ``q`` is just text now.
        assert not isinstance(app.screen, QuitModal), (
            "Pressing q must NOT open QuitModal after the Slice 2 rollback; "
            "q is a plain printable character per functional spec 2.1a."
        )

        # And the keystroke must have landed in the input as a literal
        # ``q``. Asserting ``endswith`` rather than equality keeps the
        # test robust to any boot-time pre-fill of the input.
        assert prompt.value.endswith("q"), (
            f"Expected the 'q' keystroke to be typed into #player-input as "
            f"a literal character; got value={prompt.value!r}."
        )

        # Game still running — no quit confirmation was triggered.
        assert app.is_running is True
        assert app._game_over is False
