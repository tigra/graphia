"""Spec 014 (Configurable Role Counts) — offline tests for the configurable
deck + roster.

All tests here are pure / node-level and reach **no** model or network:

- **deck** : :func:`graphia.nodes.setup.assign_roles` deals a deck sized to the
  configured lineup, honours a pinned human role, and never desyncs from the
  ``players`` map.
- **coerce** : :func:`graphia.nodes.setup._coerce_to_count` is the pure
  last-resort guarantee — exactly ``count`` distinct names every time.
- **generate-names** : :func:`graphia.nodes.setup._generate_names` retries once
  on a wrong-count response, retries once on a :class:`ValidationError` and
  returns the recovered roster verbatim, and otherwise coerces to exactly
  ``count`` (driven by the ``fake_small`` scripted-queue fixture). Every test in
  this block passes ``count`` **explicitly**, which is why it is the right home
  for the retry contract: nothing here moves when the default lineup does.
- **default-regression** : with the lineup env unset, roster generation and role
  assignment agree with the resolved config and with each other end to end —
  ``generate_roster`` mints exactly :func:`graphia.nodes.setup.ai_name_count`
  AI seats and ``assign_roles`` deals exactly ``num_mafia`` Mafiosos and
  ``num_citizens`` Law-abiding across them. **No lineup number is written down
  here** (spec 042, Task 5.1): tech-spec 042 §2.4 leaves
  ``tests/test_lineup_config.py``'s defaults test as the *single* owner of the
  default's value, because a second copy is exactly how a lineup sweep misses a
  site and leaves a self-contradictory suite.

``assign_roles`` shuffles the deck via the module-global ``random``; tests that
make order-sensitive assertions seed it first so the deal is reproducible.
"""

from __future__ import annotations

import random
import uuid

import pytest
from pydantic import ValidationError

from graphia.config import load_config
from graphia.llm import Roster
from graphia.nodes.setup import (
    _coerce_to_count,
    _generate_names,
    ai_name_count,
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


# Names the validation-recovery test slices its scripted roster from. Longer
# than any lineup this suite plays, so the slice is always exact and the list
# never has to be resized. Prefix-free of one another, matching the roster
# fake's own reserve discipline (nothing here votes, but the habit is cheap).
_RECOVERY_NAMES = [
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


def _roster_validation_error() -> ValidationError:
    """A genuine :class:`ValidationError` from the real ``Roster`` schema.

    Built by provoking the schema rather than hand-rolling an exception, so the
    retry path is entered by the same class the production ``except`` clause
    names. Spec 014 relaxed the schema to ``min_length=1`` for variable
    lineups, so a one-element list is now valid; an **empty** list still trips
    ``min_length=1``.
    """
    try:
        Roster(names=[])
    except ValidationError as exc:
        return exc
    raise AssertionError("expected Roster to reject an empty list")


@pytest.mark.parametrize("count", [4, 7])
def test_generate_names_returns_the_retried_roster_verbatim(
    env, fake_small, count: int
) -> None:
    """A ``ValidationError`` on the first call recovers, and the retry's names
    are returned **untouched**.

    This is the suite's only coverage of a validation failure that *recovers*
    — the sibling tests above trigger their retries by wrong count, never by
    exception — and spec 042, Task 3.5 moved it here from
    ``tests/test_slice3_names.py::test_retry_on_validation_failure``. It had to
    move because at UI level the count comes from the resolved config, while
    the scripted roster was a hand-written list of six: the moment the table
    wanted a different number, the retry answered with the wrong count,
    :func:`graphia.nodes.setup._coerce_to_count` padded the difference,
    ``call_count == 2`` still held and every scripted name still appeared. The
    test stayed green while exercising **coercion** instead of the recovery it
    documented.

    Two choices here are what close that hole, and neither is cosmetic:

    - ``count`` is **explicit**, so this can never again drift with the default
      lineup.
    - The assertion is **full equality**, not membership. Coercion preserves
      the names it was given and appends placeholders, so
      ``all(n in roster.names for n in scripted)`` passes over a coerced
      roster; ``roster.names == scripted`` cannot, because the padded roster is
      longer. Equality is the only form that distinguishes "the retry
      succeeded" from "the retry failed and was papered over".
    """
    scripted = _RECOVERY_NAMES[:count]
    fake = fake_small(outputs=[_roster_validation_error(), Roster(names=scripted)])

    roster = _generate_names(count)

    # Exactly the scripted list, in order and nothing appended — see the
    # docstring: a membership check would also pass over a coerced roster.
    assert roster.names == scripted
    # The failing first call plus the one corrective retry, and no third call:
    # the strict queue would raise on a fourth, and coercion would need no
    # further call at all.
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# Default-lineup regression
# ---------------------------------------------------------------------------


def test_default_lineup_roster_and_deal_agree_with_config(
    env, monkeypatch: pytest.MonkeyPatch, fake_small
) -> None:
    """At the default lineup, roster generation and the deal agree — with the
    config and with each other.

    **This test deliberately owns no lineup number** (spec 042, Task 5.1).
    It used to be called ``test_default_lineup_unset_env_yields_seven`` and did
    two jobs: it asserted the default's *values* (``(5, 2)``, ``ai_count == 6``,
    ``len(players) == 7``) and it asserted the *deal mechanics* at the default.
    The first job now belongs solely to
    ``tests/test_lineup_config.py::test_defaults_when_lineup_env_unset``, which
    keeps its literal and its triple-equality idiom — tech-spec 042 §2.4 wants
    exactly one owner, because three copies of the same number is precisely how
    a 5→6 sweep misses a site and leaves a suite that contradicts itself. The
    old name is gone for the same reason: "seven" becomes a lie the moment the
    lineup moves, and a name cannot be caught by a failing assertion.

    What is left is the invariant that actually matters and that nothing else
    covers end to end: the two setup nodes and the resolved config all describe
    **the same table**. Every expectation below is derived —
    :func:`graphia.nodes.setup.ai_name_count` for the AI seat count (the named
    seam the roster fake also calls, so a drift between the two statements of
    ``num_citizens + num_mafia - 1`` would surface here), and
    ``config.num_mafia`` / ``config.num_citizens`` for the deck composition. So
    it holds at whatever the default happens to be, and it would go red if
    ``generate_roster`` minted the wrong number of seats or ``assign_roles``
    dealt a deck that disagreed with them.

    The count env vars are explicitly cleared rather than assumed unset, so
    "the default lineup" is a fact this test establishes instead of one it
    inherits from the developer's environment.
    """
    monkeypatch.delenv("GRAPHIA_NUM_CITIZENS", raising=False)
    monkeypatch.delenv("GRAPHIA_NUM_MAFIA", raising=False)

    config = load_config()
    expected_total = config.num_citizens + config.num_mafia
    expected_ai = ai_name_count(config)
    # The whole-table counts include the human's seat; every other seat is an
    # AI player needing a generated name. Stated as a relation, not a literal.
    assert expected_ai == expected_total - 1

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
    # A name *pool*, not a lineup-sized script (spec 042 §2.2): the permissive
    # ``fake_small`` list form answers with as many names as ``ai_name_count``
    # asks for, extending the pool deterministically if it is short. So this
    # list never needs resizing when the table does.
    fake = fake_small(["Bianca", "Chiko", "Daria", "Elias", "Farah", "Gus"])
    roster_delta = generate_roster(base)
    assert fake.call_count == 1
    base["players"] = roster_delta["players"]
    assert len(base["players"]) == expected_total  # the human plus the AI seats
    ai_players = [p for p in base["players"].values() if not p.is_human]
    assert len(ai_players) == expected_ai

    random.seed(99)
    result = assign_roles(base)
    players = result["players"]
    # The deck is dealt 1:1 over the map the roster node built — no drops, no
    # IndexError — and its composition is the configured one.
    assert len(players) == expected_total
    roles = [p.role for p in players.values()]
    assert roles.count("mafia") == config.num_mafia
    assert roles.count("law_abiding") == config.num_citizens
