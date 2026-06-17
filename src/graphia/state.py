"""Typed state containers and reducers for the Graphia game graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


@dataclass
class PlayerState:
    id: str
    name: str
    role: Literal["mafia", "law_abiding"]
    is_human: bool
    is_alive: bool = True


class KillRecord(TypedDict, total=False):
    cycle: int
    name: str
    cause: Literal["night", "execution"]
    role: str | None


class ActiveVote(TypedDict, total=False):
    initiator: str
    target: str
    ballots: dict[str, Literal["yes", "no"]]
    pending: list[str]


class GameState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    players: dict[str, PlayerState]
    human_id: str
    # The human's dealt faction ("mafia"/"law_abiding") lifted to a top-level
    # plain string so it survives remote-mode serialization: PlayerState is not
    # a LangChain Serializable, so client-side it crosses the wire as its repr
    # string and ``players[human_id].role`` is unavailable. summarize() reads
    # this field instead. Set server-side in assign_roles where roles are dealt.
    human_role: str
    phase: Literal["setup", "night", "day", "end"]
    cycle: int
    night_picks: dict[str, str]
    # Multi-round Mafia consensus (Spec 015 §2.2) — all plain-replace, reset in
    # night_open beside night_picks. night_round is the current round (1–3);
    # night_mafia_order is the round's shuffled living-Mafioso ids (empty ⇒
    # reshuffle on next mafia_point); night_pointer_index is the cursor within
    # it; night_round_picks is mafioso_id → target_id for the current (deciding)
    # round; night_rounds_log holds completed rounds' pick dicts.
    night_round: int
    night_mafia_order: list[str]
    night_pointer_index: int
    night_round_picks: dict[str, str]
    night_rounds_log: list[dict[str, str]]
    day_order: list[str]
    day_turn_index: int
    day_rounds: int
    day_votes_called: int
    human_votes_called: int
    human_ballots_cast: int
    human_night_attempts: int
    human_night_successes: int
    night_victim_count: int
    execution_count: int
    active_vote: ActiveVote | None
    # Carries a validation error (e.g. a bad ``/vote`` target) forward to the
    # NEXT ``day_turn`` execution so the human can be re-prompted with the hint
    # via a single ``interrupt()`` per node execution. A graph loop — not a
    # second in-node ``interrupt()`` — drives the re-prompt; this keeps
    # ``snapshot.next`` reliable for the driver (a second in-node interrupt
    # empties ``snapshot.next`` while the interrupt is still pending, which the
    # driver misreads as game-over). Cleared (set to None) on any accepted
    # human turn.
    day_turn_error: str | None
    kill_log: Annotated[list[KillRecord], operator.add]
    winner: Literal["law_abiding", "mafia", "draw"] | None
