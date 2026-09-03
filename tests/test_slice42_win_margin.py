"""Spec 042 (A Starter Table With Room For One Mistake) — Slice 1: the margin.

WHAT THIS MODULE PINS
---------------------

Spec 042 changes the default table from 5 Law-abiding Citizens + 2 Mafiosos to
6 + 2, and its entire justification is a claim about **how many mistaken
executions the law-abiding side can afford**:

* At **5 + 2** the town can afford **zero**. Night 1 takes a citizen; if the
  town then executes a citizen rather than a Mafioso, Night 2's kill brings the
  sides level, ``check_win_condition``'s parity rule fires, and the game ends —
  **the town never reaches a second Day to recover.**
* At **6 + 2** the town can afford **exactly one**. The same wrong first-day
  vote leaves it 3 citizens against 2 Mafiosos after Night 2, the game
  continues, and a town that is right thereafter still wins.

Those are the functional spec's two win-margin acceptance criteria
(§2, "Given the new table…" / "Given the old table of five citizens…"), and
they are the source of the arithmetic in
`CR 007 <../context/change-requests/007-starter-lineup-balance-claim-and-default.md>`__
(Accepted 2026-09-03), which withdrew the earlier claim that 5 + 2 left the
game "reasonably balanced toward the Law-abiding side". **They had no test
anywhere in the suite** before this module: the two claims that state what the
change buys existed only in prose.

WHY IT MUST BE ABLE TO FAIL
---------------------------

A margin test that cannot go red proves nothing about the margin. The mechanism
the whole claim rests on is one comparison —
``check_win_condition``'s ``alive_mafia >= alive_law`` — so the module is
written so that **weakening that comparison to a strict ``>`` turns it red**,
not merely less precise:

* :func:`test_one_mistaken_execution_decides_the_game_at_the_old_table_only`'s
  ``5+2`` arm flips outcome outright (mafia → law_abiding).
* :func:`test_a_second_mistaken_execution_is_fatal_at_the_new_table` moves its
  ending event (Day 2 → Night 3), which is why every arm asserts **which
  event ended the game**, not only who won.
* :func:`test_level_counts_are_a_mafia_win` pins the boundary itself.

Anti-vacuity discipline followed here
-------------------------------------

* **No config is read.** The player maps are built by hand from the
  parametrised counts. The point of this module is to state the arithmetic
  *independently* of whatever the default happens to be, so it passes before
  Slice 5 moves the default and after — and so it cannot be made vacuous by the
  very change it is the yardstick for.
* **Winners are asserted by value** (``"law_abiding"`` / ``"mafia"``), never as
  "somebody won" — the two arms disagree about *who*, which is the whole claim.
* **Every arm additionally asserts the structural shape of the ending** — which
  event ended the game and how many Days were completed — because "the town
  never reaches a second Day" is half of the 5 + 2 claim and a winner-only
  assertion would pass over a version where 5 + 2 merely lost more slowly.
* **The mistake is shown to be what decides it, not the lineup.**
  :func:`test_a_town_that_never_errs_wins_at_either_table` runs the same walker
  with a flawless town and gets a law-abiding win at *both* tables — without it,
  the ``5+2`` arm's mafia win would be equally consistent with "5 + 2 is simply
  unwinnable", which is not the claim being made.
* **The scripted sequence is checked as it runs.** :func:`_execute` raises when
  asked to eliminate a side that has nobody left alive, so a script that has
  quietly drifted out of step with the board fails loudly instead of asserting
  something about a different game.

The sequence walked is the game's real order — **Night first, then Day** — with
a win check after each elimination, exactly as the graph registers
``check_win_night`` and ``check_win_day`` around its two fan-out sites. Nothing
here touches the graph, a model, or the RNG: ``check_win_condition`` is a pure
read over a ``players`` mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import pytest

from graphia.nodes.endgame import check_win_condition
from graphia.state import PlayerState

Side = Literal["law_abiding", "mafia"]

# The two tables under comparison, as (law_abiding, mafia) whole-side counts.
# NOT read from config, deliberately (see the module docstring): this module is
# the yardstick Slice 5's default change is measured against, so it must state
# both tables regardless of which one is the default on any given day.
NEW_TABLE = (6, 2)
OLD_TABLE = (5, 2)


# --------------------------------------------------------------------------
# Hand-built boards and a walker over the Night → Day sequence
# --------------------------------------------------------------------------


def _table(num_law_abiding: int, num_mafia: int) -> dict[str, PlayerState]:
    """Build a players map of ``num_law_abiding`` citizens and ``num_mafia`` mafia.

    Names and ids are positional and carry no meaning beyond making a failure
    message readable. Everyone starts alive; nobody is flagged human, because
    ``check_win_condition`` reads only ``is_alive`` and ``role`` — the human's
    seat is on one of the two sides and is counted with it.
    """
    players: dict[str, PlayerState] = {}
    for index in range(1, num_law_abiding + 1):
        player_id = f"la-{index}"
        players[player_id] = PlayerState(
            id=player_id,
            name=f"Citizen{index}",
            role="law_abiding",
            is_human=False,
        )
    for index in range(1, num_mafia + 1):
        player_id = f"maf-{index}"
        players[player_id] = PlayerState(
            id=player_id,
            name=f"Mafioso{index}",
            role="mafia",
            is_human=False,
        )
    return players


def _alive(players: dict[str, PlayerState], side: Side) -> list[PlayerState]:
    return [p for p in players.values() if p.is_alive and p.role == side]


def _execute(players: dict[str, PlayerState], side: Side, what: str) -> str:
    """Eliminate the first living member of ``side``; return their name.

    Raises rather than no-opping when that side has nobody left alive: a script
    that has drifted out of step with the board must fail loudly, not silently
    assert something about a different game than the one it describes.
    """
    candidates = _alive(players, side)
    assert candidates, (
        f"{what} cannot remove a {side} player: none are alive. "
        f"The scripted sequence has drifted out of step with the board."
    )
    victim = candidates[0]
    victim.is_alive = False
    return victim.name


@dataclass(frozen=True)
class _Ending:
    """How a walked game finished — who won, and on which event.

    ``ended_on`` is the event that produced the winner (``"night 2"``,
    ``"day 3"``), and ``days_completed`` counts Days that reached an execution.
    Both are asserted, not just ``winner``: "the town never reaches a second
    Day" is a statement about the shape of the ending, and a winner-only
    assertion cannot see it.
    """

    winner: str | None
    ended_on: str
    days_completed: int
    nights_completed: int
    log: tuple[str, ...]


def _play(
    players: dict[str, PlayerState], day_targets: Sequence[Side]
) -> _Ending:
    """Walk Night-kill → win-check → Day-execution → win-check until someone wins.

    The Night kill always takes a Law-abiding Citizen — the mafia never kill
    their own. ``day_targets[i]`` says which side the town executes on Day
    ``i + 1``, so ``["law_abiding", "mafia", ...]`` is "one mistaken execution
    on the first day, and right thereafter".

    The script bounds the walk, so a rule change that let the game run forever
    surfaces as a ``winner=None`` ending rather than a hang.
    """
    log: list[str] = []
    nights = 0
    days = 0

    for cycle, day_target in enumerate(day_targets, start=1):
        # --- Night: the mafia take a citizen, then the night-side win check.
        victim = _execute(players, "law_abiding", f"Night {cycle}")
        nights += 1
        log.append(
            f"Night {cycle}: {victim} killed "
            f"({len(_alive(players, 'law_abiding'))} law-abiding vs "
            f"{len(_alive(players, 'mafia'))} mafia)"
        )
        result = check_win_condition({"players": players})
        if result:
            return _Ending(
                winner=result["winner"],
                ended_on=f"night {cycle}",
                days_completed=days,
                nights_completed=nights,
                log=tuple(log),
            )

        # --- Day: the town executes someone, then the day-side win check.
        executed = _execute(players, day_target, f"Day {cycle}")
        days += 1
        log.append(
            f"Day {cycle}: {executed} ({day_target}) executed "
            f"({len(_alive(players, 'law_abiding'))} law-abiding vs "
            f"{len(_alive(players, 'mafia'))} mafia)"
        )
        result = check_win_condition({"players": players})
        if result:
            return _Ending(
                winner=result["winner"],
                ended_on=f"day {cycle}",
                days_completed=days,
                nights_completed=nights,
                log=tuple(log),
            )

    return _Ending(
        winner=None,
        ended_on="script exhausted",
        days_completed=days,
        nights_completed=nights,
        log=tuple(log),
    )


def _mistakes_then_perfect(num_mistakes: int, total_days: int) -> list[Side]:
    """A Day script: ``num_mistakes`` citizens executed first, Mafiosos after."""
    return ["law_abiding"] * num_mistakes + ["mafia"] * (
        total_days - num_mistakes
    )


# ==========================================================================
# The headline criteria — one body, both tables
# ==========================================================================


@pytest.mark.parametrize(
    (
        "num_law_abiding",
        "num_mafia",
        "expected_winner",
        "expected_ended_on",
        "expected_days_completed",
    ),
    [
        pytest.param(*NEW_TABLE, "law_abiding", "day 3", 3, id="6+2"),
        pytest.param(*OLD_TABLE, "mafia", "night 2", 1, id="5+2"),
    ],
)
def test_one_mistaken_execution_decides_the_game_at_the_old_table_only(
    num_law_abiding: int,
    num_mafia: int,
    expected_winner: str,
    expected_ended_on: str,
    expected_days_completed: int,
) -> None:
    """One wrong first-day vote: survivable at six-and-two, fatal at five-and-two.

    Both arms play the *same* town — one mistaken execution on Day 1, only
    Mafiosos thereafter — so the only thing that differs between them is the
    starting table. That is what makes a single body a proof of the difference
    rather than two unrelated assertions.

    ``6+2`` (functional spec §2, "Given the new table, when the town votes out
    one citizen by mistake on the first day and then votes out only Mafiosos,
    then the law-abiding side still wins"):

        N1 → 5 v 2 · D1 mistake → 4 v 2 · N2 → 3 v 2 (continues)
        · D2 → 3 v 1 · N3 → 2 v 1 · D3 → 2 v 0 — **law-abiding win on Day 3.**

    ``5+2`` (functional spec §2, "Given the old table of five citizens and two
    Mafiosos … then the mafia win" — which doubles as the criterion that the old
    behaviour is still reachable for anyone who configures it):

        N1 → 4 v 2 · D1 mistake → 3 v 2 · N2 → 2 v 2 — **mafia win on Night 2,
        with Day 2 never reached.**
    """
    players = _table(num_law_abiding, num_mafia)
    ending = _play(players, _mistakes_then_perfect(num_mistakes=1, total_days=6))

    assert ending.winner == expected_winner, (
        f"at {num_law_abiding}+{num_mafia}, a town that errs once and is right "
        f"thereafter should end with winner={expected_winner!r}; got "
        f"{ending.winner!r} on {ending.ended_on}. Sequence:\n  "
        + "\n  ".join(ending.log)
    )
    # The structural half of the claim, and what distinguishes the two arms
    # from each other: WHERE the game ended, not merely who won. A version in
    # which five-and-two lost more slowly — reaching a second Day first — would
    # pass the winner assertion above and fail here.
    assert ending.ended_on == expected_ended_on, (
        f"at {num_law_abiding}+{num_mafia}, the game should end on "
        f"{expected_ended_on!r}; got {ending.ended_on!r}. Sequence:\n  "
        + "\n  ".join(ending.log)
    )
    assert ending.days_completed == expected_days_completed, (
        f"at {num_law_abiding}+{num_mafia}, {expected_days_completed} Day(s) "
        f"should complete before the game ends; got {ending.days_completed}. "
        f"Sequence:\n  " + "\n  ".join(ending.log)
    )


def test_the_old_table_never_reaches_a_second_day_but_the_new_one_does() -> None:
    """The two tables' endings differ *structurally*, not only in who won.

    Stated as its own assertion because it is the sentence the functional spec
    and CR 007 both lead with — "the town never reaches a second day to
    recover" — and because a bug that made the two tables behave alike would
    satisfy neither arm's expectations while this comparison names the
    difference directly.
    """
    new_table = _play(
        _table(*NEW_TABLE), _mistakes_then_perfect(num_mistakes=1, total_days=6)
    )
    old_table = _play(
        _table(*OLD_TABLE), _mistakes_then_perfect(num_mistakes=1, total_days=6)
    )

    assert old_table.days_completed == 1
    assert new_table.days_completed > old_table.days_completed, (
        "the new table must afford the town Days the old one does not: "
        f"new={new_table.days_completed} old={old_table.days_completed}"
    )
    assert new_table.winner != old_table.winner, (
        "the same town, erring once, must reach opposite outcomes at the two "
        f"tables; both gave {new_table.winner!r}"
    )
    assert (new_table.winner, old_table.winner) == ("law_abiding", "mafia")


# ==========================================================================
# The controls that keep the headline non-vacuous
# ==========================================================================


@pytest.mark.parametrize(
    ("num_law_abiding", "num_mafia"),
    [pytest.param(*NEW_TABLE, id="6+2"), pytest.param(*OLD_TABLE, id="5+2")],
)
def test_a_town_that_never_errs_wins_at_either_table(
    num_law_abiding: int, num_mafia: int
) -> None:
    """A flawless town wins at both tables — so the *mistake* is what decides it.

    Without this control, five-and-two's mafia win in the headline test would be
    equally consistent with "five-and-two is simply unwinnable", which is not
    the claim CR 007 makes. The claim is specifically about the **room for
    error**: at five-and-two the town must be right every single time, and here
    it is, and it wins.
    """
    ending = _play(
        _table(num_law_abiding, num_mafia),
        _mistakes_then_perfect(num_mistakes=0, total_days=6),
    )

    assert ending.winner == "law_abiding", (
        f"at {num_law_abiding}+{num_mafia}, a town that executes only Mafiosos "
        f"should win; got {ending.winner!r} on {ending.ended_on}. Sequence:\n  "
        + "\n  ".join(ending.log)
    )
    assert ending.ended_on == "day 2", (
        "with two Mafiosos and no wasted execution the game ends on Day 2 at "
        f"either table; got {ending.ended_on!r}. Sequence:\n  "
        + "\n  ".join(ending.log)
    )


def test_a_second_mistaken_execution_is_fatal_at_the_new_table() -> None:
    """The new table's margin is *one* mistake, not "more" — the spec's exact claim.

    Functional spec §1 ("The desired outcome"): the law-abiding side can be
    "wrong once and still win". Pinning only the survivable first mistake would
    leave the size of the margin unstated, and would pass equally over a table
    far more generous than the one chosen (§1, "Why one more citizen, and not
    two").

        N1 → 5 v 2 · D1 mistake → 4 v 2 · N2 → 3 v 2
        · D2 mistake → 2 v 2 — **mafia win on Day 2.**
    """
    ending = _play(
        _table(*NEW_TABLE), _mistakes_then_perfect(num_mistakes=2, total_days=6)
    )

    assert ending.winner == "mafia", (
        "two mistaken executions must still lose at six-and-two, or the "
        f"margin is not the one mistake the spec claims; got {ending.winner!r} "
        f"on {ending.ended_on}. Sequence:\n  " + "\n  ".join(ending.log)
    )
    assert ending.ended_on == "day 2", (
        "the second mistake itself levels the sides, so the game ends on the "
        f"Day it is made; got {ending.ended_on!r}. Sequence:\n  "
        + "\n  ".join(ending.log)
    )


# ==========================================================================
# The boundary the whole margin rests on
# ==========================================================================


@pytest.mark.parametrize(
    ("num_law_abiding", "num_mafia"),
    [
        pytest.param(1, 1, id="1v1"),
        pytest.param(2, 2, id="2v2"),
        pytest.param(3, 3, id="3v3"),
    ],
)
def test_level_counts_are_a_mafia_win(
    num_law_abiding: int, num_mafia: int
) -> None:
    """Level sides are a mafia win — the single comparison the margin is made of.

    Every "room for error" figure above is downstream of this one rule
    (``alive_mafia >= alive_law``). Pinning it directly is what makes the
    arithmetic above legible as arithmetic rather than as a set of remembered
    outcomes: a mistaken execution costs the town two of its lead — its own
    member now, the Night's victim next — and parity ends the game.
    """
    players = _table(num_law_abiding, num_mafia)
    assert check_win_condition({"players": players}) == {"winner": "mafia"}


@pytest.mark.parametrize(
    ("num_law_abiding", "num_mafia"),
    [
        pytest.param(2, 1, id="2v1"),
        pytest.param(3, 2, id="3v2"),
        pytest.param(6, 2, id="6v2"),
    ],
)
def test_one_citizen_ahead_is_not_yet_a_win_for_anyone(
    num_law_abiding: int, num_mafia: int
) -> None:
    """A living lead of one leaves the game running — the other side of parity.

    The complement of :func:`test_level_counts_are_a_mafia_win`: three-versus-two
    after Night 2 is exactly the board six-and-two buys and five-and-two does
    not, and it must be an *ongoing* game (an empty update, so the graph falls
    through to its normal continuation) rather than a result for either side.
    """
    players = _table(num_law_abiding, num_mafia)
    assert check_win_condition({"players": players}) == {}


def test_no_mafia_left_is_a_law_abiding_win_regardless_of_survivors() -> None:
    """Zero living mafia is a law-abiding win — the other terminal rule.

    Asserted at a single surviving citizen as well as several, because the
    law-abiding rule is checked *before* parity: one citizen against zero mafia
    is level in neither direction and must not be read as anything but a win.
    """
    for num_law_abiding in (1, 2, 4):
        players = _table(num_law_abiding, num_mafia=1)
        _execute(players, "mafia", "Day 1")
        assert check_win_condition({"players": players}) == {
            "winner": "law_abiding"
        }, f"{num_law_abiding} citizens vs 0 mafia should be a law-abiding win"
