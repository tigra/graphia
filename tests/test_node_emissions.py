"""Spec 006 / ADR 008 — node-level ``CareerEvent`` emission smoke tests.

Each game-mechanics node that owns a statistical moment accepts a
``career_emitter`` (and ``game_id``) keyword and fires one ``CareerEvent``
when the moment lands. These tests call the *pure*, no-interrupt nodes
directly with a hand-built state dict and a capturing emitter so we lock
the wire-format contract independently of the full graph drive harness.

Covered (smoke level):

* ``resolve_vote`` execution branch → ``vote_resolved(was_executed=True)``
* ``resolve_vote`` failed branch → ``vote_resolved(was_executed=False)``
* ``resolve_night_kill`` with a human Mafia picker who hit the victim →
  ``night_resolved(victim_died=True, human_was_mafia_picker=True,
  human_picked_victim=True)``
* ``end_screen`` on a Mafia win → ``game_ended(outcome="mafia_win", ...)``
* All three nodes with the production no-op emitter → no captures

Deferred (interrupt-driven; would exceed the smoke time-box):

* ``day_turn`` ``vote_initiated`` emission (requires the human-vote
  interrupt path)
* ``collect_votes`` ``ballot_cast`` emission (requires the per-voter
  human-vote interrupt path)
"""

from __future__ import annotations

import pytest

from graphia.career_events import (
    KIND_GAME_ENDED,
    KIND_NIGHT_RESOLVED,
    KIND_VOTE_RESOLVED,
    CareerEvent,
    NoOpCareerEventEmitter,
)
from graphia.nodes.day import resolve_vote
from graphia.nodes.endgame import end_screen
from graphia.nodes.night import resolve_night_kill
from graphia.state import PlayerState


class _CapturingEmitter:
    """``CareerEventEmitter`` shape that records every emit call.

    Mirrors the production Protocol ``emit(session_id, event)`` and stores
    every call on :attr:`emissions` so tests can assert exactly which
    events fired and which session ids they carried.
    """

    def __init__(self) -> None:
        self.emissions: list[tuple[str, CareerEvent]] = []

    def emit(self, session_id: str, event: CareerEvent) -> None:
        self.emissions.append((session_id, event))


def _player(
    pid: str,
    name: str,
    role: str,
    *,
    is_human: bool = False,
    is_alive: bool = True,
) -> PlayerState:
    return PlayerState(
        id=pid, name=name, role=role, is_human=is_human, is_alive=is_alive
    )


# --------------------------------------------------------------------------
# resolve_vote — both branches
# --------------------------------------------------------------------------


def _vote_state(target_id: str, ballots: dict[str, str]) -> dict:
    """Minimal state dict with an ``active_vote`` for ``resolve_vote``."""
    return {
        "cycle": 1,
        "players": {
            "p-a": _player("p-a", "Alice", "law_abiding"),
            "p-b": _player("p-b", "Bob", "law_abiding"),
            target_id: _player(target_id, "Priya", "mafia"),
        },
        "active_vote": {
            "initiator": "p-a",
            "target": target_id,
            "ballots": ballots,
            "pending": [],
        },
    }


def test_resolve_vote_executed_emits_vote_resolved_true() -> None:
    """A majority-yes vote → ``vote_resolved(was_executed=True)``."""
    emitter = _CapturingEmitter()
    state = _vote_state(
        target_id="p-target",
        ballots={"p-a": "yes", "p-b": "yes", "p-target": "no"},
    )

    resolve_vote(state, career_emitter=emitter, game_id="game-1")

    assert len(emitter.emissions) == 1
    sid, event = emitter.emissions[0]
    assert sid == "game-1"
    assert event.kind == KIND_VOTE_RESOLVED
    assert event.session_id == "game-1"
    assert event.was_executed is True


def test_resolve_vote_failed_emits_vote_resolved_false() -> None:
    """A failed (no-majority) vote → ``vote_resolved(was_executed=False)``."""
    emitter = _CapturingEmitter()
    state = _vote_state(
        target_id="p-target",
        ballots={"p-a": "no", "p-b": "no", "p-target": "no"},
    )

    resolve_vote(state, career_emitter=emitter, game_id="game-1")

    assert len(emitter.emissions) == 1
    sid, event = emitter.emissions[0]
    assert sid == "game-1"
    assert event.kind == KIND_VOTE_RESOLVED
    assert event.was_executed is False


# --------------------------------------------------------------------------
# resolve_night_kill — human Mafia picker, hit the victim
# --------------------------------------------------------------------------


def test_resolve_night_kill_emits_night_resolved_with_human_mafia_picker() -> None:
    """Human Mafia picker who backs the killed target → all three flags True."""
    human_id = "p-human"
    victim_id = "p-victim"
    players = {
        human_id: _player(human_id, "Alice", "mafia", is_human=True),
        "p-ai-mafia": _player("p-ai-mafia", "Marco", "mafia"),
        victim_id: _player(victim_id, "Priya", "law_abiding"),
    }
    # Unanimous pick → no tie-break randomness, deterministic victim. Spec 015:
    # resolve_night_kill reads the deciding round's picks from night_round_picks.
    night_round_picks = {human_id: victim_id, "p-ai-mafia": victim_id}
    state = {
        "cycle": 1,
        "players": players,
        "night_round_picks": night_round_picks,
        "human_id": human_id,
    }
    emitter = _CapturingEmitter()

    resolve_night_kill(state, career_emitter=emitter, game_id="game-1")

    assert len(emitter.emissions) == 1
    sid, event = emitter.emissions[0]
    assert sid == "game-1"
    assert event.kind == KIND_NIGHT_RESOLVED
    assert event.victim_died is True
    assert event.human_was_mafia_picker is True
    assert event.human_picked_victim is True


# --------------------------------------------------------------------------
# end_screen — Mafia winner
# --------------------------------------------------------------------------


def test_end_screen_emits_game_ended_with_outcome_and_role() -> None:
    """``end_screen`` on a Mafia win → ``game_ended(outcome="mafia_win", ...)``."""
    state = {
        "players": {
            "p-h": _player("p-h", "Alice", "law_abiding", is_human=True),
            "p-m": _player("p-m", "Marco", "mafia"),
        },
        "winner": "mafia",
        "cycle": 3,
        "human_role": "law_abiding",
        "kill_log": [],
    }
    emitter = _CapturingEmitter()

    end_screen(state, career_emitter=emitter, game_id="game-1")

    assert len(emitter.emissions) == 1
    sid, event = emitter.emissions[0]
    assert sid == "game-1"
    assert event.kind == KIND_GAME_ENDED
    assert event.outcome == "mafia_win"
    assert event.human_role == "law_abiding"
    assert event.rounds == 3


# --------------------------------------------------------------------------
# NoOpCareerEventEmitter — drop everything, no captures
# --------------------------------------------------------------------------


def test_no_op_emitter_captures_nothing_across_nodes() -> None:
    """A ``NoOpCareerEventEmitter`` swallows every emission silently.

    Three nodes (``resolve_vote`` execution, ``resolve_night_kill``, and
    ``end_screen``) each fire one event normally; with the no-op injected
    in place of the capturing emitter, none of them surface anywhere. The
    no-op's contract is fire-and-forget: it must not raise, and it must
    not record state observable to the caller.
    """
    no_op = NoOpCareerEventEmitter()

    # Each call should be silent — no exception, no side effect we can
    # observe through the no-op (which has no recording surface). The
    # tests above already confirmed the capturing version sees one
    # emission each, so swapping the no-op in is the dual proof.
    resolve_vote(
        _vote_state(
            target_id="p-target",
            ballots={"p-a": "yes", "p-b": "yes", "p-target": "no"},
        ),
        career_emitter=no_op,
        game_id="game-1",
    )

    human_id = "p-human"
    victim_id = "p-victim"
    resolve_night_kill(
        {
            "cycle": 1,
            "players": {
                human_id: _player(
                    human_id, "Alice", "mafia", is_human=True
                ),
                "p-ai": _player("p-ai", "Marco", "mafia"),
                victim_id: _player(victim_id, "Priya", "law_abiding"),
            },
            "night_round_picks": {human_id: victim_id, "p-ai": victim_id},
            "human_id": human_id,
        },
        career_emitter=no_op,
        game_id="game-1",
    )

    end_screen(
        {
            "players": {
                "p-h": _player(
                    "p-h", "Alice", "law_abiding", is_human=True
                ),
                "p-m": _player("p-m", "Marco", "mafia"),
            },
            "winner": "mafia",
            "cycle": 3,
            "human_role": "law_abiding",
            "kill_log": [],
        },
        career_emitter=no_op,
        game_id="game-1",
    )

    # No assertion target — surviving without raising IS the contract.
    # Defensive: confirm the no-op exposes no `.emissions` attribute, so
    # this test never accidentally drifts to depend on one.
    assert not hasattr(no_op, "emissions")
