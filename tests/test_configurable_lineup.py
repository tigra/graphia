"""Spec 014 (Configurable Role Counts) — offline tests for the configurable
deck + roster.

All tests here are pure / node-level and reach **no** model or network:

- **deck** : :func:`graphia.nodes.setup.assign_roles` deals a deck sized to the
  configured lineup, honours a pinned human role, and never desyncs from the
  ``players`` map.
- **coerce** : :func:`graphia.nodes.setup._coerce_to_count` is the pure
  last-resort guarantee — exactly ``count`` distinct names every time.
- **generate-names** : :func:`graphia.nodes.setup._generate_names` retries once
  on a wrong-count response and otherwise coerces to exactly ``count`` (driven
  by the ``fake_small`` scripted-queue fixture).
- **default-regression** : with the lineup env unset, the default 5+2 table is
  intact end to end (``generate_roster`` asks for 6, ``assign_roles`` deals
  2 mafia + 5 law-abiding over 7 players).

``assign_roles`` shuffles the deck via the module-global ``random``; tests that
make order-sensitive assertions seed it first so the deal is reproducible.
"""

from __future__ import annotations

import random
import uuid

import pytest

from graphia.config import load_config
from graphia.llm import Roster
from graphia.nodes.setup import (
    _coerce_to_count,
    _generate_names,
    assign_roles,
    generate_roster,
)
from graphia.state import PlayerState


def _make_state(total: int) -> dict:
    """Build a synthetic graph state for ``assign_roles``.

    The human is inserted first (index 0 / ``human_id``) followed by
    ``total - 1`` AI players, mirroring the real ``collect_name`` +
    ``generate_roster`` insertion order. Every player starts Law-abiding;
    ``assign_roles`` overwrites the role from the dealt deck.
    """
    human_id = str(uuid.uuid4())
    players: dict[str, PlayerState] = {
        human_id: PlayerState(
            id=human_id,
            name="Human",
            role="law_abiding",
            is_human=True,
            is_alive=True,
        )
    }
    for i in range(total - 1):
        pid = str(uuid.uuid4())
        players[pid] = PlayerState(
            id=pid,
            name=f"AI-{i}",
            role="law_abiding",
            is_human=False,
            is_alive=True,
        )
    return {"human_id": human_id, "players": players}


# ---------------------------------------------------------------------------
# Deck composition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "num_citizens,num_mafia",
    [
        pytest.param(4, 1, id="4citizens_1mafia"),
        pytest.param(4, 2, id="4citizens_2mafia"),
    ],
)
def test_deck_composition_matches_lineup(
    env,
    monkeypatch: pytest.MonkeyPatch,
    num_citizens: int,
    num_mafia: int,
) -> None:
    """The dealt roles hold exactly the configured Mafia/Citizen counts."""
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", str(num_citizens))
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", str(num_mafia))
    random.seed(1234)  # deterministic shuffle for a stable deal

    total = num_citizens + num_mafia
    state = _make_state(total)
    human_id = state["human_id"]

    result = assign_roles(state)
    players = result["players"]

    # The player map is preserved 1:1 with the deck — no IndexError, no drops.
    assert len(players) == total == num_citizens + num_mafia

    roles = [p.role for p in players.values()]
    assert roles.count("mafia") == num_mafia
    assert roles.count("law_abiding") == num_citizens

    # The human (index 0 / human_id) is present and got *some* dealt role.
    assert human_id in players
    assert players[human_id].role in {"mafia", "law_abiding"}
    assert result["human_role"] == players[human_id].role


def test_deck_pins_human_mafia_with_non_default_lineup(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GRAPHIA_ROLE=mafia`` pins the human; the rest fit the counts."""
    num_citizens, num_mafia = 5, 3
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", str(num_citizens))
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", str(num_mafia))
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    random.seed(7)

    total = num_citizens + num_mafia
    state = _make_state(total)
    human_id = state["human_id"]

    result = assign_roles(state)
    players = result["players"]

    assert players[human_id].role == "mafia"
    assert result["human_role"] == "mafia"
    # Whole-table counts still hold once the human is pinned.
    roles = [p.role for p in players.values()]
    assert roles.count("mafia") == num_mafia
    assert roles.count("law_abiding") == num_citizens
    assert len(players) == total


# ---------------------------------------------------------------------------
# _coerce_to_count (pure)
# ---------------------------------------------------------------------------


def test_coerce_trims_too_many() -> None:
    """A roster with more than ``count`` names is trimmed to N distinct."""
    roster = Roster(names=["A", "B", "C", "D", "E"])
    coerced = _coerce_to_count(roster, 3)
    assert len(coerced.names) == 3
    assert coerced.names == ["A", "B", "C"]
    assert len(set(n.lower() for n in coerced.names)) == 3


def test_coerce_pads_too_few() -> None:
    """A roster with fewer than ``count`` names is padded to N distinct."""
    roster = Roster(names=["A", "B"])
    coerced = _coerce_to_count(roster, 5)
    assert len(coerced.names) == 5
    assert coerced.names[:2] == ["A", "B"]
    assert len(set(n.lower() for n in coerced.names)) == 5


def test_coerce_none_yields_placeholders() -> None:
    """``None`` yields exactly N distinct placeholder names."""
    coerced = _coerce_to_count(None, 4)
    assert len(coerced.names) == 4
    assert len(set(n.lower() for n in coerced.names)) == 4


def test_coerce_dedups_case_insensitively_then_pads() -> None:
    """Case-insensitive dups are collapsed, then padded back up to N."""
    roster = Roster(names=["Ann", "BOB"])
    # Inject a case-dup post-construction (the schema would reject it on parse)
    # to prove the coercer dedups defensively, not just the validator.
    roster.names = ["Ann", "ann", "Bob", "BOB"]
    coerced = _coerce_to_count(roster, 5)
    assert len(coerced.names) == 5
    lowered = [n.lower() for n in coerced.names]
    assert len(set(lowered)) == 5
    # The two distinct survivors lead; placeholders fill the rest.
    assert lowered[:2] == ["ann", "bob"]


@pytest.mark.parametrize("count", [1, 2, 6, 11])
def test_coerce_always_exact_distinct_count(count: int) -> None:
    """Whatever the input, the result is exactly N distinct names."""
    for roster in (None, Roster(names=["X"]), Roster(names=["X", "Y", "Z"])):
        coerced = _coerce_to_count(roster, count)
        assert len(coerced.names) == count
        assert len(set(n.lower() for n in coerced.names)) == count


# ---------------------------------------------------------------------------
# _generate_names retry / fallback (via fake_small)
# ---------------------------------------------------------------------------


def test_generate_names_retries_then_succeeds(env, fake_small) -> None:
    """A wrong-count first response triggers one retry that succeeds."""
    count = 4
    wrong = Roster(names=["A", "B"])  # only 2 — wrong count, no exception
    right = Roster(names=["W", "X", "Y", "Z"])
    fake = fake_small(outputs=[wrong, right])

    roster = _generate_names(count)

    assert len(roster.names) == count
    assert roster.names == ["W", "X", "Y", "Z"]
    assert fake.call_count == 2  # initial wrong + corrective retry


def test_generate_names_coerces_after_two_wrong(env, fake_small) -> None:
    """Two wrong-count responses fall back to a coerced exact-count roster."""
    count = 5
    first = Roster(names=["A", "B"])  # wrong count
    second = Roster(names=["A", "B", "C"])  # still wrong count
    fake = fake_small(outputs=[first, second])

    roster = _generate_names(count)

    assert len(roster.names) == count
    assert len(set(n.lower() for n in roster.names)) == count
    # The last (wrong) response seeds the coercion, so its names survive.
    assert roster.names[:3] == ["A", "B", "C"]
    assert fake.call_count == 2  # initial + retry, then coerce (no 3rd call)


# ---------------------------------------------------------------------------
# Default-lineup regression
# ---------------------------------------------------------------------------


def test_default_lineup_unset_env_yields_seven(env, fake_small) -> None:
    """With the lineup env unset, the default 5+2 = 7-player deal is intact.

    ``generate_roster`` asks the small model for 6 AI names (``5 + 2 - 1``);
    ``assign_roles`` then deals 2 mafia + 5 law-abiding across all 7 seats.
    """
    config = load_config()
    assert (config.num_citizens, config.num_mafia) == (5, 2)
    ai_count = config.num_citizens + config.num_mafia - 1
    assert ai_count == 6

    # collect_name seeds the human first; generate_roster appends the AI seats.
    human_id = str(uuid.uuid4())
    base = {
        "human_id": human_id,
        "players": {
            human_id: PlayerState(
                id=human_id,
                name="Human",
                role="law_abiding",
                is_human=True,
                is_alive=True,
            )
        },
    }
    fake = fake_small(["Bianca", "Chiko", "Daria", "Elias", "Farah", "Gus"])
    roster_delta = generate_roster(base)
    assert fake.call_count == 1
    base["players"] = roster_delta["players"]
    assert len(base["players"]) == 7  # human + 6 AI

    random.seed(99)
    result = assign_roles(base)
    players = result["players"]
    assert len(players) == 7
    roles = [p.role for p in players.values()]
    assert roles.count("mafia") == 2
    assert roles.count("law_abiding") == 5
