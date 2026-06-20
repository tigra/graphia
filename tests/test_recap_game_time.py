"""Spec 020 (Game-Time in the Recap), Slice 1 tests.

Spec 020 adds a pure, display-only in-world **clock** to the day-round recap
that advances one step per Day round from morning toward midnight, so the recap
reads like the Day burning down toward Night. The clock is a pure function of the
round number; the only subtlety is *which* round each call site passes. These
tests cover that surface:

1. The pure ``_round_clock(day_round)`` map — rounds 1..6 to their tokens, plus
   the both-ends clamp (``< 1`` → ``9 AM``; ``> 6`` → the midnight literal). The
   exact midnight literal the code emits is pinned (``12 AM (midnight)``).
2. ``render_day_round_recap(state, *, day_round=...)`` shows the right clock for a
   given round while the standings clauses (counts/votes/executed) stay unchanged.
3. Caller-passes-correct-round (load-bearing): the two production call sites pass
   the round the spec table prescribes —
     * ``_round_complete_update`` round-wrap passes ``new_rounds`` (the just-
       completed round): wrapping round 1 → ``9 AM``; round 2 → ``12 PM`` (catches
       a ``rounds``/``new_rounds`` off-by-one);
     * ``day_close`` passes ``ended_on_round`` — an early end (execution / vote-cap
       mid-round, ``day_rounds`` one short of the round in progress) shows the
       round it stopped on (``day_rounds == 2`` → round 3 → ``3 PM``) and NEVER
       jumps to midnight, while a round-cap close (``day_rounds == DAY_MAX_ROUNDS``)
       shows the midnight token.
4. End-to-end progression over a full six-round Day: the recap clocks, in message
   order, read ``9 AM → 12 PM → 3 PM → 6 PM → 9 PM → 12 AM``.

Per the project's determinism posture (architecture §6) the assertions are
structural — they assert the in-world time *token*, never verbatim Moderator
prose. The mechanical round reshuffle is pinned by monkeypatching
``graphia.nodes.day._shuffle_order`` (never ``random.seed``). The LLM boundary is
stubbed via ``fake_small`` / ``fake_large_day`` and the in-test ``_InfLargeDay`` /
``_InfLargePointing`` fakes reused from the Slice-1 recap suite.
"""

from __future__ import annotations

from typing import Callable

import pytest
from langchain_core.messages import SystemMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.nodes.day import (
    DAY_MAX_ROUNDS,
    _round_clock,
    _round_complete_update,
    day_close,
    render_day_round_recap,
)
from graphia.state import GameState, KillRecord, PlayerState

# Reuse the Slice-1 recap suite's helpers/markers so the two files can't drift on
# the recap-detection scaffold or the roster builder. Imported by bare module name
# (``tests/`` is on the pytest path), matching the sibling-import convention the
# spec-019 file ``test_recap_aware_reasoning.py`` already uses.
from test_slice_day_round_recap import (
    AI_NAMES,
    DAY_CLOSE_LINE,
    HUMAN_NAME,
    RECAP_MARKER,
    _InfLargeDay,
    _InfLargePointing,
    _collect_interrupt,
    _is_recap,
    _player,
    _recap_messages,
    _roster,
)

# The exact midnight literal the production ``_ROUND_CLOCKS`` tuple emits at the
# round-6 (and clamped > 6) slot. Pinned so a reworded "(midnight)" parenthetical
# fails fast.
MIDNIGHT = "12 AM (midnight)"

# The full clock progression across a Day's up-to-six rounds, in round order.
CLOCKS_IN_ORDER = ["9 AM", "12 PM", "3 PM", "6 PM", "9 PM", MIDNIGHT]


# ==========================================================================
# 1. Pure ``_round_clock(day_round)`` — mapping + both-ends clamp
# ==========================================================================


@pytest.mark.parametrize(
    ("day_round", "expected"),
    [
        (1, "9 AM"),
        (2, "12 PM"),
        (3, "3 PM"),
        (4, "6 PM"),
        (5, "9 PM"),
        (6, MIDNIGHT),
    ],
)
def test_round_clock_maps_each_round_to_its_time(
    day_round: int, expected: str
) -> None:
    """Each 1-based round maps to its in-world time; round 6 is midnight."""
    assert _round_clock(day_round) == expected


@pytest.mark.parametrize("low", [0, -1, -5])
def test_round_clock_clamps_below_one_to_morning(low: int) -> None:
    """``< 1`` clamps to the morning slot (``9 AM``) — never before morning."""
    assert _round_clock(low) == "9 AM"


@pytest.mark.parametrize("high", [7, 8, 100])
def test_round_clock_clamps_above_six_to_midnight(high: int) -> None:
    """``> 6`` clamps to the midnight literal — never runs past midnight."""
    assert _round_clock(high) == MIDNIGHT


def test_round_clock_midnight_literal_is_exact() -> None:
    """Pin the exact midnight literal the code emits (parenthetical included)."""
    assert _round_clock(6) == "12 AM (midnight)"


# ==========================================================================
# 2. ``render_day_round_recap`` shows the right clock per round
# ==========================================================================


def _clock_state() -> GameState:
    """A stable hand-built state whose standings clauses don't vary by round.

    Counts (5 Law-abiding / 2 Mafia), votes (0), and the no-execution clause are
    fixed so any change across the parametrized rounds is attributable solely to
    the clock token, never the standings body.
    """
    return {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
    }


@pytest.mark.parametrize(
    ("day_round", "expected_clock"),
    list(enumerate(CLOCKS_IN_ORDER, start=1)),
)
def test_render_recap_shows_clock_for_round(
    day_round: int, expected_clock: str
) -> None:
    """The recap shows the round's clock between the day number and the marker.

    Asserts the clock TOKEN is present (never the whole Moderator line), and that
    every standings clause is byte-unchanged across the rounds — the clock is the
    only thing that varies (satisfies "changes nothing else").
    """
    text = render_day_round_recap(_clock_state(), day_round=day_round).content

    # The clock sits in the fixed scaffold "Day {day}, {clock} status: ...".
    assert f"Day 1, {expected_clock} status:" in text
    # ...and the standings clauses are unchanged regardless of the round.
    assert "5 Law-abiding Citizens and 2 Mafiosos remain." in text
    assert "No execution votes called yet today." in text
    assert "No one has been executed today." in text


def test_render_recap_round_six_shows_midnight() -> None:
    """Round 6 explicitly shows the midnight token (the latest the clock reaches)."""
    text = render_day_round_recap(_clock_state(), day_round=6).content
    assert MIDNIGHT in text
    assert f"Day 1, {MIDNIGHT} status:" in text


# ==========================================================================
# 3. Caller-passes-correct-round (load-bearing)
# ==========================================================================


def _round_wrap_state() -> GameState:
    """Minimal state for a ``_round_complete_update`` round-wrap recap render."""
    players = {
        "la-0": _player("la-0", "Citizen0", "law_abiding"),
        "maf-0": _player("maf-0", "Mobster0", "mafia"),
    }
    return {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 0,
        "kill_log": [],
    }


@pytest.mark.parametrize(
    ("rounds_completing", "expected_clock"),
    [
        # ``rounds`` is the count BEFORE the wrap; the recap covers
        # ``new_rounds == rounds + 1`` (the just-completed round).
        (0, "9 AM"),  # completing round 1
        (1, "12 PM"),  # completing round 2
    ],
)
def test_round_complete_update_passes_just_completed_round(
    rounds_completing: int,
    expected_clock: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_round_complete_update`` passes ``new_rounds`` — the just-completed round.

    Catches a ``rounds``/``new_rounds`` off-by-one: completing round 1 must read
    ``9 AM`` (not ``12 PM``), and completing round 2 must read ``12 PM``. The
    reshuffle is pinned so the test never touches the global RNG.
    """
    monkeypatch.setattr(
        day_nodes, "_shuffle_order", lambda players: list(players.keys())
    )
    update = _round_complete_update(
        _round_wrap_state(), rounds_completing, recap_enabled=True
    )

    # Sanity: the wrap bumped the completed-round counter by one.
    assert update["day_rounds"] == rounds_completing + 1

    recaps = _recap_messages(update.get("messages", []))
    assert len(recaps) == 1, f"expected exactly one recap, got {recaps!r}"
    assert f"Day 1, {expected_clock} status:" in recaps[0].content


def test_day_close_early_end_at_round_three_shows_three_pm_not_midnight() -> None:
    """An early Day end mid-round-3 shows ``3 PM`` and does NOT jump to midnight.

    ``day_rounds == 2`` (two rounds completed) with an execution this cycle is the
    spec's "ends early at round 3" case: ``ended_on_round = day_rounds + 1 == 3``.
    The closing recap must read ``3 PM`` and contain no midnight token — proving
    the early end shows the round it stopped on, never midnight.
    """
    players = _roster(law_alive=4, mafia_alive=1, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 1,
        "name": "Mobster5",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 1,
        "day_rounds": 2,
        "kill_log": [executed],
    }
    update = day_close(state)

    recaps = _recap_messages(update["messages"])
    assert len(recaps) == 1
    text = recaps[0].content
    assert "Day 1, 3 PM status:" in text
    assert MIDNIGHT not in text, (
        "an early Day end must show the round it stopped on, never midnight"
    )
    # The executed-today clause (a standings clause) is untouched by the clock.
    assert "Mobster5 was executed today" in text


def test_day_close_round_cap_shows_midnight() -> None:
    """A round-cap close (``day_rounds == DAY_MAX_ROUNDS``) shows the midnight token.

    ``ended_on_round = day_rounds`` here (round 6 completed), so the closing recap
    reaches midnight — the contrast to the early-end case above.
    """
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": 0,
        "day_rounds": DAY_MAX_ROUNDS,
        "kill_log": [],
    }
    update = day_close(state)

    recaps = _recap_messages(update["messages"])
    assert len(recaps) == 1
    assert f"Day 1, {MIDNIGHT} status:" in recaps[0].content


# ==========================================================================
# 4. End-to-end progression: 9 AM → 12 PM → 3 PM → 6 PM → 9 PM → 12 AM
# ==========================================================================


def _extract_clock(content: str) -> str | None:
    """Return the clock token from a recap line, or None if not a recap.

    Parses the fixed ``"Day {day}, {clock} status:"`` scaffold rather than
    matching verbatim prose — splits on ``", "`` after the day number and on the
    ``" status:"`` marker, so it reads the in-world time TOKEN structurally.
    """
    if RECAP_MARKER not in content or not content.startswith("Day "):
        return None
    after_day, _, rest = content.partition(", ")
    clock, sep, _ = rest.partition(RECAP_MARKER)
    if not sep:
        return None
    return clock.strip()


def test_recap_clocks_advance_across_a_full_six_round_day(
    env,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over a full six-round Day the recap clocks read 9 AM → … → 12 AM in order.

    Mirrors ``test_slice_day_round_recap.test_six_rounds_boundary_posts_exactly_
    one_recap``: drive the compiled graph directly (no Textual), AIs only ever
    speak (never vote) so the Day runs to the round cap, the human is pinned
    Law-abiding so the ``point`` interrupt never fires. Then read the recap clocks
    in message order and assert they advance one step per round to midnight.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    graph_ref: list = []
    run_config_ref: list = []

    def _live_victim() -> str:
        g = graph_ref[0]
        rc = run_config_ref[0]
        players = g.get_state(rc).values.get("players", {})
        candidates = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        if not candidates:
            candidates = [p.id for p in players.values() if p.is_alive]
        return candidates[0]

    monkeypatch.setattr(day_nodes, "get_large", lambda: _InfLargeDay())
    monkeypatch.setattr(
        "graphia.nodes.night.get_large",
        lambda: _InfLargePointing(_live_victim),
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    graph_ref.append(graph)
    run_config_ref.append(run_config)

    def _day_closed() -> bool:
        for msg in graph.get_state(run_config).values.get("messages", []):
            if isinstance(msg, SystemMessage) and DAY_CLOSE_LINE in msg.content:
                return True
        return False

    def _run_until_close(payload) -> None:
        for _ in graph.stream(payload, run_config, stream_mode="updates"):
            if _day_closed():
                return

    _run_until_close({"messages": []})
    first = _collect_interrupt(graph, run_config)
    assert first is not None and first.get("kind") == "name"

    for _ in range(120):
        if _day_closed():
            break
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            break
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            _run_until_close(None)
            continue
        kind = iv.get("kind")
        if kind == "name":
            resume_value: str = HUMAN_NAME
        elif kind == "day_turn":
            resume_value = "I speak briefly."
        elif kind == "point":
            options = iv.get("options") or []
            resume_value = options[0]["id"] if options else _live_victim()
        else:
            raise AssertionError(f"Unexpected interrupt kind: {kind!r}")
        _run_until_close(Command(resume=resume_value))

    assert _day_closed(), "the Day never closed within the budget"

    state = graph.get_state(run_config).values
    assert state.get("day_rounds") == DAY_MAX_ROUNDS

    recaps = _recap_messages(state.get("messages", []))
    # Rounds 1..5 each emit a day_turn recap; day_close owns the round-6 recap —
    # DAY_MAX_ROUNDS recaps total (the no-double-post invariant from Slice 1).
    assert len(recaps) == DAY_MAX_ROUNDS

    clocks = [_extract_clock(r.content) for r in recaps]
    assert clocks == CLOCKS_IN_ORDER, (
        f"recap clocks should advance 9 AM → … → midnight in message order, "
        f"got {clocks!r}"
    )
    # And the last recap is the midnight one (the Day ran its full course).
    assert _is_recap(recaps[-1]) and MIDNIGHT in recaps[-1].content
