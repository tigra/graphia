"""Night-phase nodes: Mafia intros, pointing, and kill resolution."""

from __future__ import annotations

import logging
import random
from collections import Counter

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from graphia.career_events import (
    KIND_NIGHT_RESOLVED,
    CareerEvent,
    CareerEventEmitter,
)
from graphia.diary_store import DiaryStore
from graphia.llm import Pointing, get_sonnet
from graphia.prompts import (
    MAFIA_POINT_SYSTEM,
    MAFIA_POINT_USER_TEMPLATE,
    MAFIA_TEAMMATE_INTRO_TEMPLATE,
)
from graphia.state import GameState, KillRecord, PlayerState

logger = logging.getLogger(__name__)


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

    # Fall back to a random choice so the game doesn't stall.
    return random.choice(alive_law_abiding).id


def mafia_pointing(state: GameState) -> dict:
    players = state.get("players", {})

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
    for mafia in alive_mafia:
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
                target_id = random.choice(alive_law_abiding).id
            picks[mafia.id] = target_id
        else:
            picks[mafia.id] = _ai_pick_target(
                alive_law_abiding=alive_law_abiding,
                mafia=mafia,
            )

    return {"night_picks": picks}


def resolve_night_kill(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
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
    victim_id = random.choice(tied) if len(tied) > 1 else tied[0]

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

    delta: dict = {
        "players": updated,
        "kill_log": [record],
        "messages": [announcement],
        "night_victim_count": state.get("night_victim_count", 0) + 1,
    }

    human_id = state.get("human_id")
    human = players.get(human_id) if human_id else None
    human_was_picker = False
    human_picked_victim = False
    if (
        human is not None
        and human.is_alive
        and human.role == "mafia"
        and human_id in night_picks
    ):
        human_was_picker = True
        delta["human_night_attempts"] = (
            state.get("human_night_attempts", 0) + 1
        )
        if night_picks[human_id] == victim_id:
            human_picked_victim = True
            delta["human_night_successes"] = (
                state.get("human_night_successes", 0) + 1
            )

    if career_emitter is not None and game_id is not None:
        career_emitter.emit(
            game_id,
            CareerEvent(
                kind=KIND_NIGHT_RESOLVED,
                session_id=game_id,
                victim_died=True,
                human_was_mafia_picker=human_was_picker,
                human_picked_victim=human_picked_victim,
            ),
        )

    return delta


def night_close(
    state: GameState,
    *,
    diary_store: DiaryStore | None = None,
    game_id: str | None = None,
) -> dict:
    cycle = state.get("cycle", 1)

    # Slice 11 read-back (spec 002 §2.4.2): on Night 2+ each surviving AI
    # player reads back its own prior diary entries before this Night's write.
    # Running the read before the write means on Night N it reads cycles
    # 1..N-1 and then writes cycle N — exercising the genuine write/read
    # round-trip in both modes (local ``InProcessDiaryStore``; remote
    # ``AgentCoreMemoryDiaryStore`` via the Gateway-fronted Lambda). The
    # Phase-2 use of the result is a placeholder log of the entry count —
    # Phase 6 will feed the entries into the AI's reasoning. Like the write,
    # each read is guarded so a persistence failure never crashes gameplay.
    if diary_store is not None and game_id is not None and cycle >= 2:
        players = state.get("players", {})
        for player in players.values():
            if player.is_alive and not player.is_human:
                try:
                    entries = diary_store.read(
                        game_id=game_id,
                        player_id=player.id,
                    )
                    logger.info(
                        "Read %s prior diary entries for player %s on night %s.",
                        len(entries),
                        player.id,
                        cycle,
                    )
                except Exception:
                    logger.exception(
                        "Diary read failed for player %s on night %s; "
                        "continuing without those entries.",
                        player.id,
                        cycle,
                    )

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
                # A diary write can hit AgentCore Memory / the Gateway in
                # remote mode and so can raise (e.g. Gateway unreachable).
                # Persistence must never crash gameplay: catch broadly, log,
                # and continue so one player's failure doesn't skip the rest
                # (Functional §2.4.5).
                try:
                    diary_store.write(
                        game_id=game_id,
                        player_id=player.id,
                        night_index=cycle,
                        content=f"Night {cycle} diary placeholder for {player.id}",
                    )
                except Exception:
                    logger.exception(
                        "Diary write failed for player %s on night %s; "
                        "continuing without that entry.",
                        player.id,
                        cycle,
                    )

    return {
        "messages": [SystemMessage(content=f"Night {cycle} ends.")],
        "phase": "day",
    }
