"""Setup-phase nodes: collect human name, build roster, introduce it."""

from __future__ import annotations

import dataclasses
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
from graphia.llm import Persona, Roster, get_large, get_small
from graphia.prompts import (
    NAME_GEN_SYSTEM,
    NAME_GEN_USER_TEMPLATE,
    PERSONA_CITIZEN_USER_TEMPLATE,
    PERSONA_MAFIA_USER_TEMPLATE,
    PERSONA_SYSTEM,
    ROSTER_INTRO_TEMPLATE,
)
from graphia.state import GameState, PlayerPersona, PlayerState

_ROLE_LABELS: dict[str, str] = {
    "mafia": "Mafia",
    "law_abiding": "Law-abiding Citizen",
}


def _shuffle_deck(deck: list[str]) -> None:
    """Shuffle the role deck in place via the module-global ``random`` RNG.

    The single role-deal shuffle surface (architecture §6), mirroring the Day
    phase's ``graphia.nodes.day._shuffle_order`` and the Night phase's
    ``graphia.nodes.night._shuffle_mafia_order``. Lifting the inline
    ``random.shuffle(deck)`` out of :func:`assign_roles` gives tests one
    monkeypatch point to pin the deal deterministically (substitute a no-op /
    identity so the deck keeps its constructed order) without seeding the
    module-global RNG — keeping a test's trajectory pinned by intent and immune
    to cross-test global-RNG state. Production behaviour is unchanged.
    """
    random.shuffle(deck)


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
        _shuffle_deck(deck)
        roles = deck
    else:
        # Human is always the first inserted player; surface mis-seating loudly.
        assert state["human_id"] == next(iter(state["players"]))
        pinned_role = config.human_role
        deck.remove(pinned_role)
        _shuffle_deck(deck)
        roles = [pinned_role, *deck]
    existing = state.get("players", {})
    human_id = state["human_id"]
    updated: dict[str, PlayerState] = {}
    human_role = "law_abiding"
    for index, (pid, player) in enumerate(existing.items()):
        role = roles[index]
        if pid == human_id:
            human_role = role
        # Only the dealt role changes; every other field (id, name, is_human,
        # is_alive, persona, …) carries forward via ``replace``.
        updated[pid] = dataclasses.replace(player, role=role)  # type: ignore[arg-type]
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


def _fallback_persona(player: PlayerState) -> PlayerPersona:
    """A deterministic minimal persona derived only from the player's name.

    The last-resort guarantee behind :func:`generate_personas`: when the model
    fails (or returns a clearly-empty result) twice, this yields a valid,
    name-anchored persona so setup never blocks. A Mafioso gets a generic
    "secretly a Mafioso" ``true_self``; a Citizen's ``true_self`` is empty.
    """
    name = player.name
    is_mafia = player.role == "mafia"
    return PlayerPersona(
        personality=f"{name} is an ordinary, even-tempered townsperson.",
        manner=f"{name} speaks plainly and to the point.",
        public_persona=f"{name} is a familiar face around town, trusted by neighbours.",
        true_self=(
            f"{name} is secretly a Mafioso, hiding behind an ordinary cover."
            if is_mafia
            else ""
        ),
    )


def _persona_is_empty(persona: Persona | None) -> bool:
    """True when a parsed persona is missing the fields we need for a voice.

    A clearly-empty result (no personality, manner, or public backstory) is
    treated like a failure and triggers the corrective retry / fallback —
    mirroring ``_generate_names``' wrong-shape handling for free-prose output.
    """
    if persona is None:
        return True
    return not (
        persona.personality.strip()
        and persona.manner.strip()
        and persona.public_backstory.strip()
    )


def _generate_one_persona(player: PlayerState) -> PlayerPersona:
    """Generate a single AI player's persona, role-tailored, never raising.

    Validation-retry-then-fallback (mirrors :func:`_generate_names`, but with
    BROAD exception catching since free-prose personas have no exact-shape
    invariant to validate beyond non-emptiness): invoke the large model with a
    role-tailored prompt anchored on the player's name; on any failure or a
    clearly-empty result, do one corrective retry; if that still fails, return
    a deterministic :func:`_fallback_persona`. The result is *always* a valid
    :class:`PlayerPersona`, so a flaky or missing model never blocks setup.
    """
    is_mafia = player.role == "mafia"
    template = (
        PERSONA_MAFIA_USER_TEMPLATE if is_mafia else PERSONA_CITIZEN_USER_TEMPLATE
    )
    user_prompt = template.format(name=player.name)
    llm = get_large().with_structured_output(Persona)
    messages: list = [
        SystemMessage(content=PERSONA_SYSTEM),
        HumanMessage(content=user_prompt),
    ]
    try:
        persona = llm.invoke(messages)
        if not _persona_is_empty(persona):
            return _to_player_persona(persona, is_mafia=is_mafia, player=player)
    except Exception:
        persona = None

    retry_messages = [
        SystemMessage(content=PERSONA_SYSTEM),
        HumanMessage(content=user_prompt),
        HumanMessage(
            content="That response was unusable: return a non-empty "
            "`personality`, `manner`, and `public_backstory` via the Persona "
            "schema. Try again."
        ),
    ]
    try:
        retried = llm.invoke(retry_messages)
        if not _persona_is_empty(retried):
            return _to_player_persona(retried, is_mafia=is_mafia, player=player)
    except Exception:
        pass
    return _fallback_persona(player)


def _to_player_persona(
    persona: Persona, *, is_mafia: bool, player: PlayerState
) -> PlayerPersona:
    """Convert a flat :class:`Persona` to the in-state :class:`PlayerPersona`.

    ``public_backstory`` becomes the table-facing ``public_persona``; a
    Mafioso's ``secret_backstory`` becomes ``true_self`` (falling back to a
    generic cover line if the model left it empty); a Citizen's ``true_self``
    is always empty.
    """
    secret = persona.secret_backstory.strip()
    if is_mafia and not secret:
        secret = f"{player.name} is secretly a Mafioso, hiding behind an ordinary cover."
    return PlayerPersona(
        personality=persona.personality.strip(),
        manner=persona.manner.strip(),
        public_persona=persona.public_backstory.strip(),
        true_self=secret if is_mafia else "",
    )


def generate_personas(state: GameState) -> dict:
    """Attach a fresh persona to every AI player (skipping the human).

    Runs after :func:`assign_roles` so each persona can be role-tailored — one
    honest persona for a Law-abiding Citizen, a two-layer cover-legend-plus-true
    -self persona for a Mafioso. Per-player heavyweight calls (N ≤ table cap,
    one-time at startup). Each call is wrapped in the validation-retry-then-
    fallback in :func:`_generate_one_persona`, so this node NEVER raises — a
    failing or missing model yields fallback personas and setup proceeds.
    """
    players = state.get("players", {})
    # ``players`` is a plain replace channel (no merge reducer), so the return
    # must carry the *whole* map — the human (skipped below) included — or it
    # would be dropped. Start from the existing players and overwrite only the
    # AI entries with their persona-bearing copies.
    updated: dict[str, PlayerState] = dict(players)
    for pid, player in players.items():
        if player.is_human:
            continue
        persona = _generate_one_persona(player)
        updated[pid] = dataclasses.replace(player, persona=persona)
    return {"players": updated}


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
