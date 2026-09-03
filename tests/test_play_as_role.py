"""Slice 1 tests: ``GRAPHIA_ROLE`` pins the human's role at deal time.

These tests exercise :func:`graphia.nodes.setup.assign_roles` directly: it is
pure (no LLM call), reads ``GRAPHIA_ROLE`` via :func:`graphia.config.load_config`,
and operates on a ``GameState`` whose ``players`` mapping is insertion-ordered
with the human at slot 0. We construct that state by hand rather than driving
the full graph so the assertions stay focused on the role-assignment branch.

**The table size is read from config, never spelled out here (spec 042, Task
3.6).** ``assign_roles`` sizes its deck from ``config.num_citizens +
config.num_mafia`` and maps it onto the ``players`` map **by index**, so a map
with fewer seats than the deck has cards silently DROPS the surplus roles — and
a dropped card only breaks an assertion when it happens to be a Mafioso, which
made the previous hard-coded seven-seat map fail intermittently (~1 run in 3)
rather than deterministically once the default lineup grew. Everything in this
module that used to be a bare count — the seat count and the two per-side
expectations — is therefore a **config echo**, derived below.
"""

from __future__ import annotations

import pytest

from graphia.config import GraphiaConfig, load_config
from graphia.nodes.setup import ai_name_count, assign_roles
from graphia.state import GameState, PlayerState


def _make_state() -> GameState:
    """Build a post-``generate_roster`` ``GameState`` with stable ids.

    Human is the first inserted player (id ``"human"``), followed by one
    ``"ai-N"`` seat per AI player at the **resolved** lineup — exactly what
    ``collect_name`` + ``generate_roster`` produce in the real graph, where the
    AI seat count is :func:`graphia.nodes.setup.ai_name_count` (the whole-table
    counts minus the one human seat). Initial roles are all ``"law_abiding"`` —
    :func:`assign_roles` overwrites them from the dealt deck.

    The config is read **at call time**, not at import: sibling modules in this
    suite override ``GRAPHIA_NUM_CITIZENS`` / ``GRAPHIA_NUM_MAFIA`` per test, and
    an import-time constant would not see those overrides. ``ai_name_count`` is
    called rather than re-derived so this map can never disagree with the deck
    ``assign_roles`` deals against it.
    """
    ids = ["human", *(f"ai-{i}" for i in range(1, ai_name_count(load_config()) + 1))]
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


def _assert_no_seat_dropped(result: dict, config: GraphiaConfig) -> None:
    """Every configured seat came back with a dealt role.

    The deterministic guard on the failure mode Task 3.6 fixed: ``assign_roles``
    zips its config-sized deck onto the ``players`` map by index, so a
    short map loses the surplus cards without complaint. Asserting the returned
    map still holds the whole configured table turns that mismatch into an
    immediate, self-explanatory failure on *every* run, instead of a one-in-three
    argument about how many Mafiosos the AI side ended up with.
    """
    assert len(result["players"]) == config.num_citizens + config.num_mafia


def test_pin_mafia_seats_human_as_mafia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRAPHIA_ROLE=mafia`` puts the human in the Mafia seat; deck balance preserved."""
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")

    state = _make_state()
    result = assign_roles(state)
    config = load_config()

    assert result["players"]["human"].role == "mafia"
    _assert_no_seat_dropped(result, config)

    ai_roles = [p.role for pid, p in result["players"].items() if pid != "human"]
    # The human took one of the configured Mafia seats, so the AI side holds the
    # remaining Mafiosos and every configured Citizen seat.
    assert ai_roles.count("mafia") == config.num_mafia - 1
    assert ai_roles.count("law_abiding") == config.num_citizens


def test_pin_law_abiding_seats_human_as_law_abiding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRAPHIA_ROLE=law-abiding`` puts the human in a Citizen seat; deck balance preserved."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    state = _make_state()
    result = assign_roles(state)
    config = load_config()

    assert result["players"]["human"].role == "law_abiding"
    _assert_no_seat_dropped(result, config)

    ai_roles = [p.role for pid, p in result["players"].items() if pid != "human"]
    # The human took one of the configured Citizen seats, so the AI side holds
    # every Mafioso and the remaining Citizens.
    assert ai_roles.count("mafia") == config.num_mafia
    assert ai_roles.count("law_abiding") == config.num_citizens - 1


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
