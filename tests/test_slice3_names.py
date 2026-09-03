"""Slice 3 tests: AI names come from the small model (stubbed), not a hardcoded list.

Every test in this module monkeypatches ``graphia.nodes.setup.get_small`` via
the ``fake_small`` fixture so nothing reaches real Bedrock. We drive the app
through the same pilot flow used in Slice 2, capture the public log, and make
assertions about which names appeared.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from pydantic import ValidationError
from textual.widgets import Input, RichLog

from graphia.config import load_config
from graphia.llm import DayAction, Pointing, Roster
from graphia.nodes.setup import ai_name_count
from graphia.prompts import ROSTER_INTRO_TEMPLATE
from graphia.ui.app import GraphiaApp

# Re-use Task 3.4's roster-intro parser and its placeholder marker rather than
# growing a third copy: the helper reads the *logical* Moderator message across
# RichLog's wrapped rows, which is precisely the subtlety a re-implementation
# here would get wrong (at eight seats the last name lands on the second row).
# Importing a sibling test module is the established pattern in this suite —
# ``tests/test_career_stats.py`` already pulls ``_rich_log_text`` / ``_wait_for``
# from this same module, and ``tests/test_dual_mode_smoke.py`` imports from
# ``tests/test_remote_mode_smoke.py``.
from test_slice2_roster import PLACEHOLDER_PREFIX, _roster_intro_names


def _install_large_defaults(fake_large) -> None:
    """Defensive large-model stub for Slice-3 tests.

    Slice 3 only asserts roster behaviour, but once the human submits their
    name the graph chains into Night-1, which calls ``get_large()``. Without
    this stub the worker hits real Bedrock (via boto3 with dummy creds),
    keeping an executor thread alive past ``app.exit()`` and blocking
    pytest teardown on the 300s executor-join timeout.

    A placeholder ``Pointing`` (invalid UUID) is enough: the production
    ``_ai_pick_target`` treats an unrecognised target id as invalid and
    falls back to a random pick from the real roster, so the
    graph advances. ``FakeLargeUnified`` replays the last popped value
    once its queue drains, which covers both AI-Mafia invocations on
    Night 1 plus any subsequent nights.
    """
    fake_large(
        pointings=[Pointing(target_id="placeholder")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )

HUMAN_NAME = "Alice"
SLICE2_HARDCODED = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]
ROSTER_INTRO_PREFIX = ROSTER_INTRO_TEMPLATE.split("{names}")[0]

# Names ``test_retry_on_validation_failure`` slices its scripted retry roster
# from. It scripts the STRICT ``outputs=`` queue (that form is the whole point
# of a retry test and must stay), so the roster it hands back has to be the
# size the game will actually ask for — sliced to ``ai_name_count(config)`` at
# test time, never counted by hand. Longer than any lineup this suite plays
# (``graphia.llm._MAX_AI_NAMES`` is 11), so the slice is always exact.
RETRY_NAME_POOL = [
    "Noor",
    "Oleg",
    "Pema",
    "Quinn",
    "Rafa",
    "Sage",
    "Tomas",
    "Udo",
    "Vera",
    "Wren",
    "Yara",
]


def _rich_log_text(widget: RichLog) -> str:
    """Flatten a RichLog's accumulated lines into a single plain string."""
    parts: list[str] = []
    for line in widget.lines:
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
    """Poll ``predicate`` until truthy, awaiting pilot.pause each tick."""
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


async def _drive_until_roster(app: GraphiaApp, pilot) -> RichLog:
    """Submit the human name, wait for the roster-intro to render, return log."""

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

    def _intro_rendered() -> bool:
        return ROSTER_INTRO_PREFIX in _rich_log_text(log)

    await _wait_for(pilot, _intro_rendered, timeout=5.0)
    # Let any trailing super-steps flush.
    await pilot.pause()
    await pilot.pause()
    return log


async def test_roster_uses_small_model_generated_names(
    env: Path, fake_small, fake_large, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names actually come from the (stubbed) small-model call, not the Slice 2 list."""
    # Pin the human as Law-abiding so the ``mafia_pointing`` super-step never
    # raises the human-Mafia modal interrupt. Without this pin, a random
    # Mafia draw makes the worker stall on the modal awaiting a resume the
    # test never sends, which stretches teardown by up to 300s as the pilot
    # cleanup waits for the worker.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    ai_names = ["Bianca", "Chiko", "Daria", "Elias", "Farah", "Gus"]
    fake = fake_small(ai_names)
    _install_large_defaults(fake_large)

    app = GraphiaApp()
    async with app.run_test() as pilot:
        log = await _drive_until_roster(app, pilot)
        rendered = _rich_log_text(log)

        # All 6 stubbed AI names must appear.
        for name in ai_names:
            assert name in rendered, f"AI name {name!r} missing from public log"
        # Human name must appear too.
        assert HUMAN_NAME in rendered

        # None of the Slice 2 hardcoded names should leak through — that would
        # mean the code path still uses the old list instead of the LLM.
        for stale in SLICE2_HARDCODED:
            assert stale not in rendered, (
                f"Slice 2 hardcoded name {stale!r} leaked into the public log"
            )

        # The small model was invoked exactly once (no retry on a valid first response).
        assert fake.call_count == 1

        await pilot.press("q")
    assert app.is_running is False


@pytest.mark.parametrize(
    "ai_names",
    [
        ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"],
        ["Gus", "Hana", "Iker", "Juno", "Kato", "Lila"],
    ],
)
async def test_different_names_across_runs(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    ai_names: list[str],
) -> None:
    """Parametrizing over two rosters proves injection works run-to-run.

    The real "two runs produce different names" guarantee comes from the small model at
    runtime; here we simply demonstrate the stub's per-run names reach the UI.
    """
    # Pin the human as Law-abiding to avoid the Mafia-modal stall; see sibling
    # test for the teardown-hang rationale.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(ai_names)
    _install_large_defaults(fake_large)

    app = GraphiaApp()
    async with app.run_test() as pilot:
        log = await _drive_until_roster(app, pilot)
        rendered = _rich_log_text(log)

        for name in ai_names:
            assert name in rendered

        # Cross-parametrization sanity: the *other* parametrization's names
        # must NOT appear in this run, proving the rosters really differ.
        other_rosters = [
            ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"],
            ["Gus", "Hana", "Iker", "Juno", "Kato", "Lila"],
        ]
        other = next(r for r in other_rosters if r != ai_names)
        assert set(other).isdisjoint(set(ai_names))
        for stray in other:
            assert stray not in rendered

        await pilot.press("q")


async def test_retry_on_validation_failure(
    env: Path, fake_small, fake_large, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app survives a first-response validation failure and shows the retried roster.

    A **recovery** test, and deliberately only that (spec 042, Task 3.5). The
    exact-names half of the old claim — "the roster the game uses is verbatim
    the one the retry returned" — now lives in
    ``tests/test_configurable_lineup.py::test_generate_names_returns_the_retried_roster_verbatim``,
    a unit test on ``_generate_names`` with an **explicit** count.

    It had to move. Here the count comes from the *resolved config* at runtime,
    while the scripted retry was a hand-written list of six names. The moment
    the table wanted a different number, the retry answered with the wrong
    count, ``_coerce_to_count`` padded the difference with ``Player-1``,
    ``call_count == 2`` still held and all six scripted names still appeared —
    so this test stayed green while exercising **coercion** instead of the
    recovery its name promised, and the suite lost its only coverage of a
    validation failure that recovers without anything going red.

    What this test is still the only place to say is that the *app* comes back:
    the graph advances past the failed call, the roster-intro renders, and the
    table it renders is the real one — exactly as many seats as the config
    asks for, with not one production placeholder among them. Those two
    assertions are also what stop the vacuity returning: the scripted roster is
    now sized from the config, and if that ever drifts again the placeholder
    check fires instead of the test quietly changing subject.
    """
    # Pin the human as Law-abiding to avoid the Mafia-modal stall; see sibling
    # test for the teardown-hang rationale.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # A synthetic ValidationError for the Roster schema — we cannot easily
    # build one by hand, so force validation by feeding Roster an invalid
    # list and capture the exception. Spec 014 relaxed the schema to
    # ``min_length=1`` (variable lineups), so a 1-element list is now valid;
    # an *empty* list still trips ``min_length=1`` and gives us the
    # first-call validation failure this test needs.
    try:
        Roster(names=[])
    except ValidationError as exc:
        validation_error = exc
    else:  # pragma: no cover
        raise AssertionError("expected Roster to reject an empty list")

    # Read the lineup here, not at import time: sibling modules override the
    # counts per test via ``monkeypatch.setenv``, and the whole point is that
    # the scripted retry matches whatever table the graph is about to deal.
    config = load_config()
    expected_total = config.num_citizens + config.num_mafia
    good_names = RETRY_NAME_POOL[: ai_name_count(config)]
    # The STRICT one-shot queue, on purpose: this is a retry test, so the fake
    # must not be able to quietly answer a call the test did not script.
    fake = fake_small(
        outputs=[validation_error, Roster(names=good_names)],
    )
    _install_large_defaults(fake_large)

    app = GraphiaApp()
    async with app.run_test() as pilot:
        log = await _drive_until_roster(app, pilot)
        rendered = _rich_log_text(log)

        for name in good_names:
            assert name in rendered, (
                f"post-retry AI name {name!r} missing from public log"
            )

        # Exactly two small-model invocations: the failing first + the successful retry.
        assert fake.call_count == 2

        # The retry actually recovered — it was not papered over by coercion.
        # ``_coerce_to_count`` pads a short roster with ``Player-{k}``, and that
        # is the one way a seat nobody scripted reaches a real table, so its
        # absence is the load-bearing claim: ``call_count == 2`` and the
        # membership loop above are both satisfied by a coerced roster too.
        intro_names = _roster_intro_names(rendered)
        assert len(intro_names) == expected_total, (
            f"roster-intro line should name exactly {expected_total} players "
            f"({config.num_citizens} citizens + {config.num_mafia} mafia, "
            f"human included), but named {len(intro_names)}: {intro_names!r}"
        )
        assert not [n for n in intro_names if n.startswith(PLACEHOLDER_PREFIX)], (
            f"a coerced {PLACEHOLDER_PREFIX}N placeholder reached the roster, "
            f"so the retry did not recover: {intro_names!r}"
        )
        assert PLACEHOLDER_PREFIX not in rendered, (
            f"a coerced {PLACEHOLDER_PREFIX}N placeholder appears in the "
            f"public log:\n{rendered}"
        )

        await pilot.press("q")
