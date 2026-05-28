"""Spec 006, Slice 1 tests: cross-game career stats (Layer 1, local mode).

Covers only the scaffold that landed in Slice 1:

* ``render_greeting`` first-run welcome line for a zeroed aggregate.
* ``LocalFileStatsStore.load`` tolerance — a missing or corrupt JSON file
  yields a zeroed :class:`CareerStats` and never raises.
* The launch greeting reaching ``#public-log`` when an injected
  :class:`StatsStore` reports an empty career.

Out of scope (not yet implemented): ``record``, ``fold``, ``summarize``,
``render_panel``, and the AgentCore remote store. The injected fake store
keeps the UI test off the filesystem; no LLM or Bedrock call is made.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import RichLog

from graphia.llm import DayAction, Pointing
from graphia.stats_store import (
    CareerStats,
    GameSummary,
    LocalFileStatsStore,
    render_greeting,
)
from graphia.ui.app import GraphiaApp

# Re-use the established RichLog flattening + polling helpers so the UI test
# matches the Slice-2 pattern for reading pane text.
from test_slice2_roster import _rich_log_text, _wait_for

# Stable identifying substring of the first-run greeting. Matched loosely so a
# wording tweak that keeps the "first game" sense doesn't break the test.
FIRST_RUN_MARKER = "first game"


def test_render_greeting_first_run() -> None:
    """A zeroed career yields the first-run welcome line."""
    greeting = render_greeting(CareerStats())
    assert FIRST_RUN_MARKER in greeting


def test_load_missing_file_returns_zeroed(tmp_path: Path) -> None:
    """``load`` on a non-existent path returns a zeroed aggregate, no raise."""
    store = LocalFileStatsStore(tmp_path / "does-not-exist.json")

    stats = store.load()

    assert stats == CareerStats()
    assert stats.games_total == 0


def test_load_corrupt_file_returns_zeroed(tmp_path: Path) -> None:
    """``load`` on unparseable JSON returns a zeroed aggregate, no raise."""
    path = tmp_path / "career.json"
    path.write_text("{bad json", encoding="utf-8")
    store = LocalFileStatsStore(path)

    stats = store.load()

    assert stats == CareerStats()
    assert stats.games_total == 0


class _FakeStatsStore:
    """In-memory ``StatsStore`` reporting an empty (first-run) career.

    ``load`` returns a zeroed :class:`CareerStats`; ``record`` is part of the
    Protocol surface but unused in Slice 1, so it is a placeholder that fails
    loudly if ever reached from this test.
    """

    def load(self) -> CareerStats:
        return CareerStats()

    def record(self, summary: GameSummary) -> CareerStats:  # pragma: no cover
        raise AssertionError("record() must not be called in Slice 1 tests")


async def test_ui_greeting_appears_on_launch(
    env: Path, fake_haiku, fake_sonnet, monkeypatch
) -> None:
    """The first-run greeting is written to ``#public-log`` at launch.

    The injected fake store keeps this off the filesystem. ``fake_haiku`` /
    ``fake_sonnet`` and the Law-abiding role pin guard against teardown hangs:
    the greeting writes before ``build_graph``, but the driver then advances
    the graph, and an unstubbed LLM call would strand a boto3 retry thread
    past ``app.exit()``.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"])
    fake_sonnet(
        pointings=[Pointing(target_id="placeholder")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )
    app = GraphiaApp(stats_store=_FakeStatsStore())
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#public-log", RichLog)

        await _wait_for(
            pilot,
            lambda: FIRST_RUN_MARKER in _rich_log_text(log),
            timeout=5.0,
        )

        assert FIRST_RUN_MARKER in _rich_log_text(log)

        await pilot.press("q")
    assert app.is_running is False
