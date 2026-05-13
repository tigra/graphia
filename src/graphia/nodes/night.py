"""Night-phase nodes: Mafia intros, pointing, and kill resolution."""

from __future__ import annotations

import random
from collections import Counter

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from graphia.config import load_config
from graphia.diary_store import DiaryStore
from graphia.llm import Pointing, get_sonnet
from graphia.prompts import (
    MAFIA_POINT_SYSTEM,
    MAFIA_POINT_USER_TEMPLATE,
    MAFIA_TEAMMATE_INTRO_TEMPLATE,
)
from graphia.state import GameState, KillRecord, PlayerState


def first_night_mafia_intros(state: GameState) -> dict:
    if state.get("cycle", 1) != 1:
        return {}

    players = state.get("players", {})
    alive_mafia = [
        p for p in players.values() if p.is_alive and p.role == "mafia"
    ]

    messages: list = []
    for mafia in alive_mafia:
        teammates = [m.name for m in alive_mafia if m.id != mafia.id]
        names_str = ", ".join(teammates) if teammates else "(none)"
        content = MAFIA_TEAMMATE_INTRO_TEMPLATE.format(names=names_str)
        messages.append(
            SystemMessage(
                content=content,
                additional_kwargs={"private_to": mafia.id},
            )
        )
    return {"messages": messages}


def night_open(state: GameState) -> dict:
    # First Night of the game arrives with no prior phase (or "setup");
    # subsequent Nights arrive from the Day loop with phase=="day". Only bump
    # the cycle counter on re-entry so Night 1 and Day 1 share cycle=1.
    prior_phase = state.get("phase")
    current_cycle = state.get("cycle", 1)
    if prior_phase == "day":
        cycle = current_cycle + 1
    else:
        cycle = current_cycle

    # Slice 9 safety cap (Functional §2.9): if the game reaches cycle 20
    # without a winner, force a draw ending. A downstream conditional edge
    # (route_after_night_open) short-circuits the Night setup to end_screen.
    if cycle >= 20:
        return {
            "winner": "draw",
            "cycle": cycle,
            "phase": "night",
            "messages": [
                SystemMessage(
                    content="The game has reached 20 cycles without a resolution."
                )
            ],
        }

    return {
        "messages": [SystemMessage(content="Night falls.")],
        "night_picks": {},
        "phase": "night",
        "cycle": cycle,
    }


def route_after_night_open(state: GameState) -> str:
    """Slice 9 safety cap router.

    If ``night_open`` detected the cycle cap and set ``winner="draw"``, short-
    circuit to ``end_screen``; otherwise proceed with the normal Night flow.
    """
    if state.get("winner") == "draw":
        return "end_screen"
    return "mafia_pointing"


def _roster_lines(alive_law_abiding: list[PlayerState]) -> str:
    return "\n".join(f"{p.name}: {p.id}" for p in alive_law_abiding)


def _ai_pick_target(
    alive_law_abiding: list[PlayerState],
    mafia: PlayerState,
    cycle: int,
    mafia_index: int,
    seed: int,
) -> str:
    valid_ids = {p.id for p in alive_law_abiding}
    valid_ids_list = sorted(valid_ids)
    roster = _roster_lines(alive_law_abiding)

    llm = get_sonnet().with_structured_output(Pointing)
    base_messages: list = [
        SystemMessage(content=MAFIA_POINT_SYSTEM),
        HumanMessage(content=MAFIA_POINT_USER_TEMPLATE.format(roster=roster)),
    ]

    try:
        first: Pointing = llm.invoke(base_messages)
        if first.target_id in valid_ids:
            return first.target_id
    except Exception:
        pass

    # Retry once with a corrective reminder.
    retry_messages = [
        *base_messages,
        HumanMessage(
            content=(
                f"Invalid target_id. Must be one of: {valid_ids_list}. "
                "Try again."
            )
        ),
    ]
    try:
        second: Pointing = llm.invoke(retry_messages)
        if second.target_id in valid_ids:
            return second.target_id
    except Exception:
        pass

    # Fall back to a deterministic random choice so the game doesn't stall.
    rng = random.Random(seed + cycle + mafia_index)
    return rng.choice(alive_law_abiding).id


def mafia_pointing(state: GameState) -> dict:
    config = load_config()
    players = state.get("players", {})
    cycle = state.get("cycle", 1)

    # Preserve roster order from the players dict.
    alive_mafia = [
        p for p in players.values() if p.is_alive and p.role == "mafia"
    ]
    alive_law_abiding = [
        p for p in players.values() if p.is_alive and p.role == "law_abiding"
    ]
    valid_ids = {p.id for p in alive_law_abiding}

    # Defensive guard: Slice 8 will end the game when a side is wiped out.
    # Until then, make this node a graceful no-op so the graph doesn't crash
    # trying to pick from an empty pool.
    if not alive_mafia:
        return {"messages": [SystemMessage(content="No Mafia remain.")]}
    if not alive_law_abiding:
        return {"messages": [SystemMessage(content="No Mafia targets remain.")]}

    picks: dict[str, str] = {}
    for mafia_index, mafia in enumerate(alive_mafia):
        if mafia.is_human:
            value = interrupt(
                {
                    "kind": "point",
                    "options": [
                        {"id": t.id, "name": t.name} for t in alive_law_abiding
                    ],
                }
            )
            target_id = value if isinstance(value, str) else ""
            if target_id not in valid_ids:
                # UI is responsible for returning valid ids; fall back rather
                # than hang if something slips through.
                rng = random.Random(config.seed + cycle + mafia_index)
                target_id = rng.choice(alive_law_abiding).id
            picks[mafia.id] = target_id
        else:
            picks[mafia.id] = _ai_pick_target(
                alive_law_abiding=alive_law_abiding,
                mafia=mafia,
                cycle=cycle,
                mafia_index=mafia_index,
                seed=config.seed,
            )

    return {"night_picks": picks}


def resolve_night_kill(state: GameState) -> dict:
    config = load_config()
    cycle = state.get("cycle", 1)
    night_picks = state.get("night_picks", {})
    players = state.get("players", {})

    # If mafia_pointing produced no picks (e.g., nobody left to target),
    # nothing dies tonight. Keep kill_log and players untouched so the
    # downstream day_open can fall through to its "no victim" template.
    if not night_picks:
        return {
            "messages": [SystemMessage(content="No one was killed this Night.")]
        }

    counts = Counter(night_picks.values())
    top_count = max(counts.values())
    tied = [tid for tid, c in counts.items() if c == top_count]
    rng = random.Random(config.seed + cycle * 31)
    victim_id = rng.choice(tied) if len(tied) > 1 else tied[0]

    victim = players[victim_id]
    updated: dict[str, PlayerState] = {}
    for pid, player in players.items():
        if pid == victim_id:
            updated[pid] = PlayerState(
                id=player.id,
                name=player.name,
                role=player.role,
                is_human=player.is_human,
                is_alive=False,
            )
        else:
            updated[pid] = player

    record: KillRecord = {
        "cycle": cycle,
        "name": victim.name,
        "cause": "night",
        "role": None,
    }
    announcement = SystemMessage(
        content=f"During the night, {victim.name} was killed."
    )
    return {
        "players": updated,
        "kill_log": [record],
        "messages": [announcement],
    }


def night_close(
    state: GameState,
    *,
    diary_store: DiaryStore | None = None,
    game_id: str | None = None,
) -> dict:
    cycle = state.get("cycle", 1)

    # Slice 6 smoke-test placeholder (spec 002 §2.4): one diary entry per
    # surviving AI player per Night. The content is intentionally trivial —
    # Phase 6 will replace it with the AI's actual private reflection.
    # In remote mode this fires real ``bedrock-agentcore:CreateEvent`` calls
    # against AgentCore Memory; in local mode it appends to the dict-backed
    # ``InProcessDiaryStore``. Same call site, parallel implementations.
    if diary_store is not None and game_id is not None:
        players = state.get("players", {})
        for player in players.values():
            if player.is_alive and not player.is_human:
                diary_store.write(
                    game_id=game_id,
                    player_id=player.id,
                    night_index=cycle,
                    content=f"Night {cycle} diary placeholder for {player.id}",
                )

    return {
        "messages": [SystemMessage(content=f"Night {cycle} ends.")],
        "phase": "day",
    }
