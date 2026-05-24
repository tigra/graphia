"""Slice 1 tests: ``GRAPHIA_ROLE`` pins the human's role at deal time.

These tests exercise :func:`graphia.nodes.setup.assign_roles` directly: it is
pure (no LLM call), reads ``GRAPHIA_ROLE`` via :func:`graphia.config.load_config`,
and operates on a ``GameState`` whose ``players`` mapping is insertion-ordered
with the human at slot 0. We construct that state by hand rather than driving
the full graph so the assertions stay focused on the role-assignment branch.
"""

from __future__ import annotations

import pytest

from graphia.config import load_config
from graphia.nodes.setup import assign_roles
from graphia.state import GameState, PlayerState


def _make_state() -> GameState:
    """Build a post-``generate_roster`` ``GameState`` with stable ids.

    Human is the first inserted player (id ``"human"``), followed by six AIs
    (``"ai-1"`` .. ``"ai-6"``). Initial roles are all ``"law_abiding"`` —
    :func:`assign_roles` overwrites them.
    """
    ids = ["human", "ai-1", "ai-2", "ai-3", "ai-4", "ai-5", "ai-6"]
    players: dict[str, PlayerState] = {}
    for pid in ids:
        players[pid] = PlayerState(
            id=pid,
            name=pid,
            role="law_abiding",
            is_human=(pid == "human"),
            is_alive=True,
        )
    return {"human_id": "human", "players": players}


def test_pin_mafia_seats_human_as_mafia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRAPHIA_ROLE=mafia`` puts the human in the Mafia seat; deck balance preserved."""
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")

    state = _make_state()
    result = assign_roles(state)

    assert result["players"]["human"].role == "mafia"

    ai_roles = [p.role for pid, p in result["players"].items() if pid != "human"]
    assert ai_roles.count("mafia") == 1
    assert ai_roles.count("law_abiding") == 5


def test_pin_law_abiding_seats_human_as_law_abiding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRAPHIA_ROLE=law-abiding`` puts the human in a Citizen seat; deck balance preserved."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    state = _make_state()
    result = assign_roles(state)

    assert result["players"]["human"].role == "law_abiding"

    ai_roles = [p.role for pid, p in result["players"].items() if pid != "human"]
    assert ai_roles.count("mafia") == 2
    assert ai_roles.count("law_abiding") == 4


@pytest.mark.parametrize("role_value", ["MAFIA", "Mafia", "mafia"])
def test_role_value_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
    role_value: str,
) -> None:
    """Case variants of ``mafia`` all parse to ``human_role == "mafia"``.

    Tests the concern at the parsing layer (``load_config``) rather than
    routing through ``assign_roles`` — the case-insensitivity guarantee lives
    in :func:`graphia.config.load_config`'s ``role_raw.strip().lower()`` match,
    and asserting against that directly keeps the test independent of any
    downstream RNG state.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", role_value)

    config = load_config()

    assert config.human_role == "mafia"


def test_invalid_role_value_exits_with_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown ``GRAPHIA_ROLE`` value fails fast naming both accepted choices."""
    monkeypatch.setenv("GRAPHIA_ROLE", "villain")

    with pytest.raises(SystemExit) as excinfo:
        load_config()

    message = str(excinfo.value)
    assert "mafia" in message
    assert "law-abiding" in message
