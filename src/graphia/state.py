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
    phase: Literal["setup", "night", "day", "end"]
    cycle: int
    night_picks: dict[str, str]
    day_order: list[str]
    day_turn_index: int
    day_rounds: int
    day_votes_called: int
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
