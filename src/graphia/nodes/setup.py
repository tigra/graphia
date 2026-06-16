"""Setup-phase nodes: collect human name, build roster, introduce it."""

from __future__ import annotations

import random
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import ValidationError

from graphia.career_events import (
    KIND_GAME_STARTED,
    CareerEvent,
    CareerEventEmitter,
)
from graphia.config import load_config
from graphia.llm import Roster, get_small
from graphia.prompts import (
    NAME_GEN_SYSTEM,
    NAME_GEN_USER_TEMPLATE,
    ROSTER_INTRO_TEMPLATE,
)
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


def _coerce_to_count(roster: Roster | None, count: int) -> Roster:
    """Force a roster to exactly ``count`` distinct names.

    The pure, last-resort guarantee behind the deck/roster invariant: whatever
    the model returned (or failed to return), this yields exactly ``count``
    distinct names so the role-mapping loop in :func:`assign_roles` can never
    ``IndexError``. Trims to the first ``count`` distinct names when given too
    many; pads with deterministically-distinct ``Player-{k}`` placeholders —
    skipping any that would collide (case-insensitively) with a name already
    present — when given too few or ``None``.
    """
    names = list(roster.names) if roster is not None else []
    # De-dup defensively (case-insensitive) while preserving order; the schema
    # validator already enforces this on parsed rosters, but a coerced result
    # must hold the invariant unconditionally.
    seen: set[str] = set()
    distinct: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            distinct.append(name.strip())
    if len(distinct) >= count:
        return Roster(names=distinct[:count])
    k = 1
    while len(distinct) < count:
        placeholder = f"Player-{k}"
        if placeholder.lower() not in seen:
            seen.add(placeholder.lower())
            distinct.append(placeholder)
        k += 1
    return Roster(names=distinct)


def _generate_names(count: int) -> Roster:
    """Return exactly ``count`` distinct AI names.

    Validation-retry-then-coerce: invoke the small model for ``count`` names;
    if it parses to exactly ``count``, return it. On a :class:`ValidationError`
    or a wrong count, do one corrective retry naming the exact count. If that
    still fails or is the wrong count, :func:`_coerce_to_count` trims/pads to a
    guaranteed ``count`` distinct names — the result is *always* exactly
    ``count`` (never an ``IndexError`` in :func:`assign_roles`).
    """
    llm = get_small().with_structured_output(Roster)
    user_prompt = NAME_GEN_USER_TEMPLATE.format(count=count)
    messages: list = [
        SystemMessage(content=NAME_GEN_SYSTEM),
        HumanMessage(content=user_prompt),
    ]
    try:
        roster = llm.invoke(messages)
        if len(roster.names) == count:
            return roster
    except ValidationError:
        roster = None

    retry_messages = [
        SystemMessage(content=NAME_GEN_SYSTEM),
        HumanMessage(content=user_prompt),
        HumanMessage(
            content=f"That was invalid: return exactly {count} distinct, "
            "non-empty first names via the Roster schema. Try again."
        ),
    ]
    try:
        retried = llm.invoke(retry_messages)
        if len(retried.names) == count:
            return retried
        roster = retried
    except ValidationError:
        pass
    return _coerce_to_count(roster, count)


def generate_roster(state: GameState) -> dict:
    config = load_config()
    ai_count = config.num_citizens + config.num_mafia - 1
    roster = _generate_names(ai_count)
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


def assign_roles(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
    config = load_config()
    deck: list[str] = (
        ["mafia"] * config.num_mafia + ["law_abiding"] * config.num_citizens
    )
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
    if career_emitter is not None and game_id is not None:
        career_emitter.emit(
            game_id,
            CareerEvent(
                kind=KIND_GAME_STARTED,
                session_id=game_id,
                human_role=human_role,
            ),
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
