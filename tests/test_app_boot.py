"""Boot tests: drive GraphiaApp via Textual's async pilot API.

Slice 2 replaced the original ``#welcome`` greeting with a graph-driven
roster intro rendered into ``#public-log``. The "name appears in the
public log" behavior is already covered by ``tests/test_slice2_roster.py``
so this module focuses on ctrl+c exit and boot logging.

Both tests defensively stub ``fake_small`` *and* ``fake_large`` so the
graph can advance past the roster intro and Night-1 without ever reaching
real Bedrock — even if the shutdown keystroke lands after the driver has
already kicked off the large-model-backed Mafia-pointing super-step. Without
the large-model stub, that call would hit ``ChatBedrockConverse`` with dummy
credentials and keep a boto3 retry thread alive past ``app.exit()``,
causing pytest to hang on the 300s executor-join timeout.
"""

from __future__ import annotations

import json
from pathlib import Path

from graphia.llm import DayAction, Pointing
from graphia.ui.app import GraphiaApp


async def test_ctrl_c_exits_cleanly(
    env: Path, fake_small, fake_large, monkeypatch
) -> None:
    """Submitting a name then pressing ctrl+c should end the app cleanly."""
    # Pin the human as Law-abiding so the ``mafia_pointing`` super-step never
    # raises the human-Mafia modal interrupt — that modal would leave the
    # producer thread blocked awaiting a resume the test never sends and
    # drag out pytest teardown.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"])
    fake_large(
        # A placeholder ``Pointing`` triggers ``_ai_pick_target``'s random
        # fallback — a single scripted entry is enough because
        # ``FakeLargeUnified`` replays the last popped value for all
        # subsequent invocations.
        pointings=[Pointing(target_id="placeholder")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )
    app = GraphiaApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"Alice")
        await pilot.press("enter")
        await pilot.press("ctrl+c")
    assert app.is_running is False


async def test_log_file_contains_app_start_event(
    env: Path, fake_small, fake_large, monkeypatch
) -> None:
    # Pin the human as Law-abiding to avoid the human-Mafia modal interrupt —
    # see ``test_ctrl_c_exits_cleanly`` for the teardown-hang rationale.
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"])
    fake_large(
        pointings=[Pointing(target_id="p-1")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )
    app = GraphiaApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
    lines = [line for line in env.read_text(encoding="utf-8").splitlines() if line]
    events = [json.loads(line) for line in lines]
    assert any(e.get("event") == "app_start" for e in events)
