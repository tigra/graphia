"""Setup-phase nodes: collect human name, build roster, introduce it."""

from __future__ import annotations

import random
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import ValidationError

from graphia.config import load_config
from graphia.llm import Roster, get_haiku
from graphia.prompts import NAME_GEN_SYSTEM, NAME_GEN_USER, ROSTER_INTRO_TEMPLATE
from graphia.state import GameState, PlayerState

_ROLE_LABELS: dict[str, str] = {
    "mafia": "Mafia",
    "law_abiding": "Law-abiding Citizen",
}


def collect_name(state: GameState) -> dict:
    value = interrupt({"kind": "name"})
    name = value.strip() if isinstance(value, str) else ""
    if not name:
        name = "Player"
    player_id = str(uuid.uuid4())
    human = PlayerState(
        id=player_id,
        name=name,
        role="law_abiding",
        is_human=True,
        is_alive=True,
    )
    return {
        "human_id": player_id,
        "players": {player_id: human},
        "phase": "setup",
        "cycle": 1,
        "human_votes_called": 0,
        "human_ballots_cast": 0,
        "human_night_attempts": 0,
        "human_night_successes": 0,
        "night_victim_count": 0,
        "execution_count": 0,
        "messages": [SystemMessage(content=f"A new game begins. Welcome, {name}.")],
    }


def _generate_names() -> Roster:
    llm = get_haiku().with_structured_output(Roster)
    messages: list = [
        SystemMessage(content=NAME_GEN_SYSTEM),
        HumanMessage(content=NAME_GEN_USER),
    ]
    try:
        return llm.invoke(messages)
    except ValidationError:
        retry_messages = [
            SystemMessage(content=NAME_GEN_SYSTEM),
            HumanMessage(content=NAME_GEN_USER),
            HumanMessage(
                content="Those names were invalid: must be 6 distinct non-empty "
                "strings. Try again."
            ),
        ]
        return llm.invoke(retry_messages)


def generate_roster(state: GameState) -> dict:
    roster = _generate_names()
    new_players: dict[str, PlayerState] = {}
    for ai_name in roster.names:
        pid = str(uuid.uuid4())
        new_players[pid] = PlayerState(
            id=pid,
            name=ai_name,
            role="law_abiding",
            is_human=False,
            is_alive=True,
        )
    existing = state.get("players", {})
    return {"players": {**existing, **new_players}}


def assign_roles(state: GameState) -> dict:
    config = load_config()
    deck: list[str] = [
        "mafia",
        "mafia",
        "law_abiding",
        "law_abiding",
        "law_abiding",
        "law_abiding",
        "law_abiding",
    ]
    if config.human_role is None:
        random.shuffle(deck)
        roles = deck
    else:
        # Human is always the first inserted player; surface mis-seating loudly.
        assert state["human_id"] == next(iter(state["players"]))
        pinned_role = config.human_role
        deck.remove(pinned_role)
        random.shuffle(deck)
        roles = [pinned_role, *deck]
    existing = state.get("players", {})
    human_id = state["human_id"]
    updated: dict[str, PlayerState] = {}
    human_role = "law_abiding"
    for index, (pid, player) in enumerate(existing.items()):
        role = roles[index]
        if pid == human_id:
            human_role = role
        updated[pid] = PlayerState(
            id=player.id,
            name=player.name,
            role=role,  # type: ignore[arg-type]
            is_human=player.is_human,
            is_alive=player.is_alive,
        )
    return {"players": updated, "human_role": human_role}


def introduce_roster(state: GameState) -> dict:
    players = state.get("players", {})
    names = ", ".join(p.name for p in players.values())
    line = ROSTER_INTRO_TEMPLATE.format(names=names)
    return {"messages": [SystemMessage(content=line)]}


def reveal_role(state: GameState) -> dict:
    human_id = state["human_id"]
    human = state["players"][human_id]
    role_label = _ROLE_LABELS[human.role]
    message = SystemMessage(
        content=f"You are {human.name}. Your role is {role_label}.",
        additional_kwargs={"private_to": human_id},
    )
    return {"messages": [message]}
