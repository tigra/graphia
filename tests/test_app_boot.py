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

import pytest

from graphia.config import GraphiaConfig
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


# ===========================================================================
# Boot provider provenance — the app_start record names the model that served
# the run (spec 035 follow-up).
#
# Before this, the local JSONL trace carried only graph-stream deltas, so a
# finished local game left NO evidence of which provider played it: verifying
# spec 035's local Claude game required an out-of-band CloudWatch metrics
# query, while the deployed Runtime had been self-evidencing all along.
# ===========================================================================


def _boot_record(tmp_path: Path, **overrides: object) -> dict:
    """Run ``setup_logger`` against a config and return its ``app_start`` record."""
    from graphia.logging import setup_logger

    defaults: dict[str, object] = {
        "bearer_token": None,
        "aws_region": "us-east-1",
        "log_file": tmp_path / "boot.log",
        "checkpoint_dir": tmp_path / "checkpoints",
        "stats_file": tmp_path / "career.json",
        "human_role": None,
        "remote_mode": False,
        "runtime_invocation_url": None,
        "memory_id": None,
        "career_memory_id": None,
        "gateway_id": None,
        "gateway_url": None,
        "cloudwatch_log_group": None,
        "stats_strategy_id": None,
        "stats_namespace": None,
    }
    config = GraphiaConfig(**{**defaults, **overrides})  # type: ignore[arg-type]
    setup_logger(config)
    lines = config.log_file.read_text(encoding="utf-8").splitlines()
    # The trace is APPEND-mode and long-lived (real logs span months of runs),
    # so the boot record just written is the LAST line, not the first — which is
    # exactly why a per-run provenance stamp is what makes a window in it
    # attributable at all.
    return json.loads(lines[-1])


def test_boot_record_names_the_bedrock_nova_provider_and_models(tmp_path: Path) -> None:
    """The Nova arm stamps provider + both resolved tier ids + region."""
    rec = _boot_record(tmp_path, llm_provider="bedrock")

    assert rec["event"] == "app_start"
    assert rec["provider"] == "bedrock"
    assert rec["large_model"] == "amazon.nova-pro-v1:0"
    assert rec["small_model"] == "amazon.nova-lite-v1:0"
    assert rec["aws_region"]
    assert rec["ollama_base_url"] is None


def test_boot_record_names_claude_when_the_claude_provider_is_selected(
    tmp_path: Path,
) -> None:
    """The whole point: a local Claude game is now self-evidencing in the log."""
    claude = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    rec = _boot_record(
        tmp_path,
        llm_provider="bedrock-claude",
        large_model=claude,
        small_model=claude,
    )

    assert rec["provider"] == "bedrock-claude"
    assert rec["large_model"] == claude
    assert rec["small_model"] == claude


def test_boot_record_names_the_ollama_endpoint_not_a_region(tmp_path: Path) -> None:
    """Ollama stamps its tier models + base URL, and no irrelevant AWS region."""
    rec = _boot_record(
        tmp_path,
        llm_provider="ollama",
        ollama_large_model="qwen3-coder:30b",
        ollama_small_model="qwen2.5:3b",
    )

    assert rec["provider"] == "ollama"
    assert rec["large_model"] == "qwen3-coder:30b"
    assert rec["small_model"] == "qwen2.5:3b"
    assert rec["ollama_base_url"]
    assert rec["aws_region"] is None, "an Ollama run has no meaningful AWS region"


def test_boot_record_never_leaks_credentials_or_the_aws_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model provenance must not become a credential/identity disclosure."""
    monkeypatch.setenv("AWS_PROFILE", "some-private-profile-name")
    rec = _boot_record(tmp_path, llm_provider="bedrock", bearer_token="SUPER-SECRET")

    rendered = repr(rec)
    assert "SUPER-SECRET" not in rendered
    assert "some-private-profile-name" not in rendered
    assert "bearer" not in rendered.lower()


def test_boot_record_marks_remote_mode(tmp_path: Path) -> None:
    """Remote mode writes this local trace too — record which side ran the graph."""
    assert _boot_record(tmp_path, remote_mode=True)["remote_mode"] is True
    assert _boot_record(tmp_path, remote_mode=False)["remote_mode"] is False
