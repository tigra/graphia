"""Slice 8 sub-task 4: the remote-mode crash modal renders CloudWatch coordinates.

The thing under test (built by the previous Slice-8 task):

* ``src/graphia/ui/failure_modal.py`` — :class:`FailureModal`, a
  :class:`ModalScreen` that surfaces the two CloudWatch Logs coordinates a
  player needs to investigate a *remote* crash: the Runtime's log group
  name and a copy-pasteable per-session filter ``{ $.thread_id = "<id>" }``.
* ``src/graphia/ui/app.py`` — ``GraphiaApp._drive`` wraps ``drive_graph`` in
  ``try/except``; on an unhandled exception it sets ``_game_over`` and, in
  **remote mode only**, calls ``_show_failure_modal`` which does
  ``push_screen(FailureModal(...))``. Local mode keeps the old banner with
  no modal.

Strategy
--------

We reuse the smoke-test harness's seam: ``graphia.driver.AgentCoreClient``
is monkeypatched with a tiny fake. Instead of yielding a normal stream,
this :class:`CrashingAgentCoreClient`'s ``stream`` *raises* — an unhandled
exception surfaces from inside ``drive_graph``'s ``_consume_stream`` loop,
which is exactly the real ``try/except`` seam ``_drive`` guards. This
faithfully exercises the production crash path: the exception is not
caught anywhere between ``client.stream`` and ``_drive``.

Raising lazily (during iteration of the generator, after the first
``stream`` call has begun) — rather than from ``__init__`` — mirrors a
real boto3 failure mid-game: the session booted, ``build_graph`` returned
a ``thread_id``, then the deployed Runtime threw. That ordering matters:
it means ``GraphiaApp._thread_id`` is populated before the crash, so the
modal renders the *real* session thread id in its filter expression
rather than the ``<unknown>`` fallback.

What's mocked
-------------

* ``ChatBedrockConverse`` — never reached (the crash happens before any
  node runs); the autouse ``safe_llm`` fixture guards it regardless.
* ``AgentCoreClient`` — replaced with :class:`CrashingAgentCoreClient` at
  the ``graphia.driver`` import binding (the call site). No
  ``boto3.client('bedrock-agentcore')`` is ever constructed.
* ``build_graph`` is left untouched — it runs for real (local, in-process)
  so ``_thread_id`` is set from its return value, but the graph is never
  actually executed because the crashing client owns the stream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from graphia.ui.app import GraphiaApp
from graphia.ui.failure_modal import FailureModal

# Synthetic runtime ARN — only satisfies the ``AgentCoreClient`` constructor /
# ``load_config`` remote-mode guard. Never resolved against AWS because the
# client is replaced by ``CrashingAgentCoreClient`` before any network call.
FAKE_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test"
)

# The CloudWatch log group name the test wires via GRAPHIA_LOG_GROUP. The
# modal must echo this verbatim so a player can paste it into the console.
FAKE_LOG_GROUP = "/aws/bedrock-agentcore/runtime/graphia-test"

# Message carried by the injected exception — surfaces in the modal's
# (optional) error-summary line and lets the test pin the exact crash.
CRASH_MESSAGE = "simulated remote runtime failure"


# --------------------------------------------------------------------------
# Crashing AgentCore client — raises mid-stream instead of yielding chunks
# --------------------------------------------------------------------------


class CrashingAgentCoreClient:
    """Drop-in ``AgentCoreClient`` replacement whose ``stream`` raises.

    Mirrors the real constructor surface (``runtime_arn`` / ``region`` /
    ``boto3_client``) so the driver's ``client = AgentCoreClient(...)`` call
    site lands here cleanly. ``stream`` is a generator that raises a
    :class:`RuntimeError` on first iteration — simulating the deployed
    Runtime throwing partway through a game. The exception is uncaught
    between here and ``GraphiaApp._drive``'s ``try/except``.
    """

    def __init__(
        self,
        *,
        runtime_arn: str,
        region: str,
        boto3_client: Any | None = None,
    ) -> None:
        if boto3_client is not None:
            raise AssertionError(
                "CrashingAgentCoreClient must never receive a boto3 client"
            )
        if not runtime_arn:
            raise ValueError("runtime_arn is required for AgentCoreClient")
        self.runtime_arn = runtime_arn
        self.region = region
        self.call_count = 0

    def stream(
        self,
        payload: Any,
        run_config: dict,
        stream_mode: str = "updates",
    ) -> Iterator[dict]:
        self.call_count += 1
        # Raise *during iteration* (after the generator is entered), not
        # from __init__ — this models a Runtime that crashes mid-game, so
        # build_graph has already returned and _thread_id is populated.
        raise RuntimeError(CRASH_MESSAGE)
        # Unreachable, but keeps this a generator so callers iterate it.
        yield {}  # pragma: no cover


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def remote_env_with_log_group(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Remote-mode env vars plus a known ``GRAPHIA_LOG_GROUP``.

    ``load_config()`` (run inside ``GraphiaApp.__init__``) reads
    ``GRAPHIA_REMOTE`` / ``GRAPHIA_RUNTIME_URL`` to flip remote mode, and
    ``GRAPHIA_LOG_GROUP`` to populate ``config.cloudwatch_log_group`` — the
    value the failure modal renders. ``GRAPHIA_MEMORY_ID`` clears
    ``build_graph``'s remote-mode diary-store guard (``build_graph`` runs
    for real here, even though the graph is never executed).
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
    monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-tests")
    monkeypatch.setenv("GRAPHIA_LOG_GROUP", FAKE_LOG_GROUP)
    return env


@pytest.fixture
def remote_env_no_log_group(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Remote-mode env vars with ``GRAPHIA_LOG_GROUP`` explicitly unset.

    Exercises the modal's graceful-degradation branch: when
    ``config.cloudwatch_log_group`` is ``None`` the modal must render the
    ``(unknown — ...)`` note instead of the literal ``"None"``, while the
    filter expression stays valid.
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
    monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-tests")
    monkeypatch.delenv("GRAPHIA_LOG_GROUP", raising=False)
    return env


@pytest.fixture
def local_env_with_log_group(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Local-mode env: remote vars cleared, but ``GRAPHIA_LOG_GROUP`` set.

    The log group is set on purpose — it proves the *mode* branch (not the
    mere presence of a log group) is what gates the modal. A local-mode
    crash must NOT push :class:`FailureModal` even when a log group name
    is available.
    """
    monkeypatch.delenv("GRAPHIA_REMOTE", raising=False)
    monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
    monkeypatch.delenv("GRAPHIA_MEMORY_ID", raising=False)
    monkeypatch.setenv("GRAPHIA_LOG_GROUP", FAKE_LOG_GROUP)
    return env


@pytest.fixture
def crashing_agentcore_client(
    monkeypatch: pytest.MonkeyPatch,
) -> type[CrashingAgentCoreClient]:
    """Replace ``graphia.driver.AgentCoreClient`` with the crashing fake.

    Patches the import binding at the *call site* (``graphia.driver``),
    matching the seam ``test_remote_mode_smoke.py`` already uses. Any
    remote-mode ``drive_graph`` run then raises mid-stream.
    """
    monkeypatch.setattr(
        "graphia.driver.AgentCoreClient", CrashingAgentCoreClient
    )
    return CrashingAgentCoreClient


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _failure_modal_text(modal: FailureModal) -> str:
    """Flatten every Label/Static body inside the modal into one string.

    The modal composes plain ``Label`` / ``Static`` widgets (``Label`` is a
    ``Static`` subclass, so one ``query(Static)`` covers both). Each one's
    ``render()`` output is the user-visible renderable — concatenating the
    plain text gives the modal's content without depending on byte-for-byte
    terminal rendering, matching the repo's pilot-API-over-screenshot
    convention for content assertions. Reuses ``conftest.plain_text``,
    which normalises a ``Static``'s renderable to a plain string.
    """
    from textual.widgets import Static

    from conftest import plain_text

    return "\n".join(plain_text(widget) for widget in modal.query(Static))


async def _drive_until_game_over(app: GraphiaApp, pilot: Any) -> None:
    """Pump the pilot until the crash handler has set ``_game_over``."""
    for _ in range(200):
        if app._game_over:
            return
        await pilot.pause(0.05)
    raise AssertionError(
        "crash handler never set _game_over — the injected exception did "
        "not propagate to GraphiaApp._drive's try/except as expected"
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


async def test_remote_crash_shows_failure_modal_with_log_group_and_filter(
    remote_env_with_log_group: Path,
    crashing_agentcore_client: type[CrashingAgentCoreClient],
) -> None:
    """Remote-mode crash pushes :class:`FailureModal` with both coordinates.

    Injects an exception during stream consumption (the
    :class:`CrashingAgentCoreClient` raises on first ``stream`` iteration).
    Asserts:

    * the screen on top of the stack is a :class:`FailureModal`,
    * its rendered content contains the configured CloudWatch log group
      name verbatim, and
    * it contains the per-session filter expression
      ``{ $.thread_id = "<thread>" }`` carrying the *actual* failed
      session's thread id (the one ``build_graph`` returned, which the app
      stores on ``_thread_id``).
    """
    app = GraphiaApp()
    # Sanity: the env fixture really put the app in remote mode with the
    # log group wired — otherwise the modal branch would never be reached.
    assert app.config.remote_mode is True
    assert app.config.cloudwatch_log_group == FAKE_LOG_GROUP

    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_until_game_over(app, pilot)

        # The crash handler stamped _thread_id from build_graph's return
        # before the crash; it must be a real id, not the <unknown> fallback.
        thread_id = app._thread_id
        assert thread_id, "_thread_id should be set before the crash"
        assert thread_id != "<unknown>"

        # The top-of-stack screen is the FailureModal.
        top = app.screen
        assert isinstance(top, FailureModal), (
            f"expected FailureModal on top of the screen stack after a "
            f"remote-mode crash; got {type(top).__name__}"
        )

        text = _failure_modal_text(top)

        # (a) the CloudWatch log group identifier is rendered verbatim.
        assert FAKE_LOG_GROUP in text, (
            f"FailureModal must render the configured CloudWatch log group "
            f"{FAKE_LOG_GROUP!r}; rendered content was:\n{text}"
        )

        # (b) the thread-id filter expression carries the real session id.
        expected_filter = f'{{ $.thread_id = "{thread_id}" }}'
        assert expected_filter in text, (
            f"FailureModal must render the per-session filter "
            f"{expected_filter!r}; rendered content was:\n{text}"
        )

        # The crash summary surfaces the injected exception too.
        assert CRASH_MESSAGE in text, (
            f"expected the crash message {CRASH_MESSAGE!r} in the modal "
            f"summary; rendered content was:\n{text}"
        )


async def test_remote_crash_modal_degrades_when_log_group_unset(
    remote_env_no_log_group: Path,
    crashing_agentcore_client: type[CrashingAgentCoreClient],
) -> None:
    """With ``GRAPHIA_LOG_GROUP`` unset the modal degrades gracefully.

    The modal must NOT render the literal ``"None"`` for the log group;
    instead it shows the ``(unknown — ...)`` recovery note pointing at the
    ``terraform output cloudwatch_log_group`` command. The per-session
    filter expression stays valid and present regardless — that is the one
    coordinate that does not depend on the env var.
    """
    app = GraphiaApp()
    assert app.config.remote_mode is True
    assert app.config.cloudwatch_log_group is None

    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_until_game_over(app, pilot)

        top = app.screen
        assert isinstance(top, FailureModal), (
            f"expected FailureModal after a remote-mode crash; "
            f"got {type(top).__name__}"
        )

        text = _failure_modal_text(top)

        # Never render the bare "None" — show the recovery note instead.
        assert "(unknown" in text, (
            f"FailureModal should show the '(unknown — ...)' note when no "
            f"log group is configured; rendered content was:\n{text}"
        )
        assert "terraform output cloudwatch_log_group" in text, (
            f"the recovery note should point at the Terraform output; "
            f"rendered content was:\n{text}"
        )

        # The filter expression is still valid and carries the session id.
        thread_id = app._thread_id
        assert thread_id and thread_id != "<unknown>"
        expected_filter = f'{{ $.thread_id = "{thread_id}" }}'
        assert expected_filter in text, (
            f"the filter expression must still render even without a log "
            f"group; expected {expected_filter!r} in:\n{text}"
        )


async def test_local_crash_does_not_show_failure_modal(
    local_env_with_log_group: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: a *local*-mode crash never pushes the modal.

    The crash path's mode branch in ``GraphiaApp._drive`` must gate the
    modal on ``config.remote_mode`` — not on the mere availability of a log
    group. This run sets ``GRAPHIA_LOG_GROUP`` but leaves remote mode off:
    the crash must still set ``_game_over`` (so the game ends) but the
    screen stack must remain free of any :class:`FailureModal`.

    In local mode ``drive_graph`` runs the in-process graph and pauses at
    the very first ``interrupt()`` (the name prompt), so it never raises
    on its own. To exercise the crash path deterministically we replace
    ``graphia.ui.app.drive_graph`` with a coroutine that raises — the
    exception surfaces from inside ``_drive`` exactly where a real
    ``drive_graph`` failure would, so the ``try/except`` mode branch is
    hit faithfully without depending on any graph internals.
    """

    async def _crashing_drive_graph(**kwargs: Any) -> None:
        raise RuntimeError(CRASH_MESSAGE)

    monkeypatch.setattr(
        "graphia.ui.app.drive_graph", _crashing_drive_graph
    )

    app = GraphiaApp()
    # Local mode, but a log group IS available — proving the branch keys
    # on the mode, not on the presence of the log group.
    assert app.config.remote_mode is False
    assert app.config.cloudwatch_log_group == FAKE_LOG_GROUP

    async with app.run_test() as pilot:
        await pilot.pause()
        await _drive_until_game_over(app, pilot)

        # Game ended on the crash...
        assert app._game_over is True

        # ...but no FailureModal anywhere on the screen stack.
        assert not isinstance(app.screen, FailureModal), (
            "local-mode crash must NOT push FailureModal; the modal is "
            "remote-mode only"
        )
        for screen in app.screen_stack:
            assert not isinstance(screen, FailureModal), (
                f"found a FailureModal in the local-mode screen stack: "
                f"{screen!r} — the modal must be gated on remote_mode"
            )
