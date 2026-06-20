"""Night-phase nodes: Mafia intros, pointing, and kill resolution."""

from __future__ import annotations

import dataclasses
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
from graphia.llm import Pointing, get_large
from graphia.prompts import (
    MAFIA_POINT_SYSTEM,
    MAFIA_POINT_USER_TEMPLATE,
    MAFIA_TEAMMATE_INTRO_TEMPLATE,
)
from graphia.state import GameState, KillRecord, PlayerState

logger = logging.getLogger(__name__)

# Spec 015 §2.3 / §2.5: the hard cap on private pointing rounds. Referenced by
# the loop router (route_after_mafia_point) and surfaced to the human pointer
# via the "point" interrupt payload's ``round_cap`` so the modal can show
# "round X of N" with a single source of truth.
NIGHT_ROUND_CAP = 3


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


def night_open(state: GameState, *, max_days: int = 12) -> dict:
    # First Night of the game arrives with no prior phase (or "setup");
    # subsequent Nights arrive from the Day loop with phase=="day". Only bump
    # the cycle counter on re-entry so Night 1 and Day 1 share cycle=1.
    prior_phase = state.get("phase")
    current_cycle = state.get("cycle", 1)
    if prior_phase == "day":
        cycle = current_cycle + 1
    else:
        cycle = current_cycle

    # Runaway safeguard (spec 023): a Mafia game thins out to a winner on its
    # own, so reaching the Day cap always signals a stuck/looping game — never a
    # legitimate result. ``max_days`` (default 12, ``GRAPHIA_MAX_DAYS``) is bound
    # in by ``_assemble_graph`` exactly like the recap flags, so local and remote
    # share one limit. When hit, record ``winner="runaway"`` (distinct from a
    # real win and from a ``"draw"``); a downstream conditional edge
    # (route_after_night_open) short-circuits Night setup to end_screen.
    if cycle >= max_days:
        return {
            "winner": "runaway",
            "cycle": cycle,
            "phase": "night",
            "messages": [
                SystemMessage(
                    content=(
                        f"The game has reached the {max_days}-Day cap without a "
                        "resolution (runaway game)."
                    )
                )
            ],
        }

    return {
        "messages": [SystemMessage(content="Night falls.")],
        "night_picks": {},
        "night_round": 1,
        "night_mafia_order": [],
        "night_pointer_index": 0,
        "night_round_picks": {},
        "night_rounds_log": [],
        "phase": "night",
        "cycle": cycle,
    }


def route_after_night_open(state: GameState) -> str:
    """Runaway safeguard router (spec 023).

    If ``night_open`` detected the Day cap and set ``winner="runaway"``, short-
    circuit to ``end_screen``; otherwise enter the multi-round pointing loop at
    ``mafia_round_start`` (Spec 015 — Multi-Round Mafia Consensus by Pointing).
    Only ``night_open`` sets a winner before this site, so checking for any
    non-None winner is equivalent to checking the cap and stays robust.
    """
    if state.get("winner") is not None:
        return "end_screen"
    return "mafia_round_start"


def _shuffle_mafia_order(mafia_ids: list[str]) -> list[str]:
    """Return a freshly shuffled copy of the living-Mafioso ids for one round.

    The single Night shuffle surface (Spec 015 §2.6): one module-level function
    over the module-global ``random`` RNG, mirroring the Day phase's
    ``graphia.nodes.day._shuffle_order``. Keeping it as the *only* place the
    per-round order is randomized gives tests one monkeypatch point to pin the
    pointing order. It is called from ``mafia_round_start`` — a node with no
    ``interrupt()`` — so the non-deterministic shuffle is committed as its own
    super-step and is never re-run on a human-pointer replay (§3 replay-safety).
    """
    ids = list(mafia_ids)
    random.shuffle(ids)
    return ids


def _roster_lines(alive_law_abiding: list[PlayerState]) -> str:
    return "\n".join(f"{p.name}: {p.id}" for p in alive_law_abiding)


def _render_prior_picks(
    players: dict[str, PlayerState],
    rounds_log: list[dict[str, str]],
    current_round_picks: dict[str, str],
    *,
    exclude_pointer_id: str,
) -> str:
    """Render the picks-so-far context for the AI pointing prompt, by NAME.

    Spec 015 §2.4: a Mafioso-only block summarizing every teammate pick already
    committed this Night — completed rounds from ``rounds_log`` (in order) plus
    the current round's picks collected before this pointer's turn
    (``exclude_pointer_id`` is the current pointer, who has not yet picked).
    Ids are resolved to names through ``players``; only names appear in the
    prose. Renders a neutral line when no teammate has pointed yet (the very
    first pointer of round 1).

    Knowledge-boundary (Spec 013): this is invoked only from the AI pointing
    path, which is only ever a Mafioso — never threaded into a Law-abiding
    player's prompt.
    """

    def name_of(player_id: str) -> str:
        player = players.get(player_id)
        return player.name if player is not None else player_id

    def render_round(picks: dict[str, str]) -> str:
        return ", ".join(
            f"{name_of(mafioso_id)} → {name_of(target_id)}"
            for mafioso_id, target_id in picks.items()
        )

    segments: list[str] = []
    for round_number, picks in enumerate(rounds_log, start=1):
        if picks:
            segments.append(f"Round {round_number} — {render_round(picks)}")

    current_so_far = {
        mafioso_id: target_id
        for mafioso_id, target_id in current_round_picks.items()
        if mafioso_id != exclude_pointer_id
    }
    if current_so_far:
        current_round_number = len(rounds_log) + 1
        segments.append(
            f"Round {current_round_number} so far — "
            f"{render_round(current_so_far)}"
        )

    if not segments:
        return "No teammate has pointed yet this Night."
    return "; ".join(segments)


def _ai_pick_target(
    alive_law_abiding: list[PlayerState],
    mafia: PlayerState,
    prior_picks: str = "",
) -> str:
    valid_ids = {p.id for p in alive_law_abiding}
    valid_ids_list = sorted(valid_ids)
    roster = _roster_lines(alive_law_abiding)
    prior_picks_block = prior_picks or "No teammate has pointed yet this Night."

    # Spec 016 §2.4 (secondary, light): colour this Mafioso's private pointing
    # reasoning with its persona. Night pointing is silent and Mafia-only, so it
    # reasons as its TRUE character — surface personality + manner, no public
    # cover. Defensive: render "" when no persona so .format never breaks.
    persona = mafia.persona
    mafia_persona_block = (
        f"\nYou are {persona.personality} {persona.manner}\n"
        if persona is not None
        else ""
    )

    llm = get_large().with_structured_output(Pointing)
    base_messages: list = [
        SystemMessage(content=MAFIA_POINT_SYSTEM),
        HumanMessage(
            content=MAFIA_POINT_USER_TEMPLATE.format(
                roster=roster,
                mafia_persona=mafia_persona_block,
                prior_picks=prior_picks_block,
            )
        ),
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


def _alive_mafia(state: GameState) -> list[PlayerState]:
    """Living Mafiosos in roster (players-dict insertion) order."""
    players = state.get("players", {})
    return [p for p in players.values() if p.is_alive and p.role == "mafia"]


def _alive_law_abiding(state: GameState) -> list[PlayerState]:
    """Living Law-abiding targets in roster (players-dict insertion) order."""
    players = state.get("players", {})
    return [p for p in players.values() if p.is_alive and p.role == "law_abiding"]


def mafia_round_start(state: GameState) -> dict:
    """Begin one pointing round: roll over the prior round, then shuffle order.

    This node does the round's only non-deterministic work — the per-round
    shuffle of the living-Mafioso order — and contains **no** ``interrupt()``.
    Because it is its own super-step, the shuffle is committed before any
    human pointer is prompted and is never recomputed on a resume (§3
    replay-safety): ``mafia_point`` only ever *reads* the committed order.

    Re-entry from ``route_after_mafia_point`` (a round ended without consensus
    and under the cap) carries a non-empty ``night_round_picks`` — that
    completed round is appended to ``night_rounds_log`` and the round counter is
    bumped here. The first entry from ``night_open`` carries empty picks, so the
    round stays at 1 and the log is untouched.

    Defensive guard: with no living Mafioso or no living target, return an empty
    order so ``route_after_mafia_point`` routes straight to ``resolve_night_kill``
    with no picks — a graceful no-kill no-op matching today's behaviour.
    """
    alive_mafia = _alive_mafia(state)
    alive_law_abiding = _alive_law_abiding(state)

    if not alive_mafia or not alive_law_abiding:
        # Graceful no-op: empty order → router → resolve_night_kill → no kill.
        return {
            "night_mafia_order": [],
            "night_pointer_index": 0,
            "night_round_picks": {},
        }

    delta: dict = {}

    prior_round_picks = state.get("night_round_picks", {})
    if prior_round_picks:
        # Re-entry for a later round: archive the just-completed round and bump.
        rounds_log = list(state.get("night_rounds_log", []))
        rounds_log.append(dict(prior_round_picks))
        delta["night_rounds_log"] = rounds_log
        delta["night_round"] = state.get("night_round", 1) + 1

    delta["night_mafia_order"] = _shuffle_mafia_order(
        [m.id for m in alive_mafia]
    )
    delta["night_pointer_index"] = 0
    delta["night_round_picks"] = {}
    return delta


def mafia_point(state: GameState) -> dict:
    """Handle exactly ONE pointer's pick this super-step (replay-safe).

    All work before the ``interrupt()`` is a pure read of committed state, so a
    human-pointer resume re-derives the same pointer and replays its resume
    value without recomputing any AI pick made earlier in the round (§3). The
    pick is committed into ``night_round_picks`` and the cursor advanced; the
    next pointer (or round) is selected by ``route_after_mafia_point``.
    """
    order: list[str] = list(state.get("night_mafia_order", []))
    index = state.get("night_pointer_index", 0)

    # Empty order ⇒ no-mafia / no-target guard from mafia_round_start. No-op;
    # the router sends us to resolution.
    if not order or index >= len(order):
        return {}

    players = state.get("players", {})
    alive_law_abiding = _alive_law_abiding(state)
    valid_ids = {p.id for p in alive_law_abiding}

    pointer_id = order[index]
    pointer = players.get(pointer_id)

    if pointer is not None and pointer.is_human:
        # Surface the human Mafioso exactly the AI's information (Spec 015
        # §2.4): the current round number, the cap, and the by-name picks-so-
        # far — completed rounds plus this round's picks made before the
        # human's turn (the human is excluded as the current pointer, who has
        # not yet picked). Built with the SAME helper the AI path uses so both
        # see an identical summary. All pure reads of committed state, so this
        # is replay-safe before the interrupt() below.
        prior_picks = _render_prior_picks(
            players=players,
            rounds_log=state.get("night_rounds_log", []),
            current_round_picks=state.get("night_round_picks", {}),
            exclude_pointer_id=pointer_id,
        )
        # interrupt() as the first effecting statement (only pure reads above).
        value = interrupt(
            {
                "kind": "point",
                "options": [
                    {"id": t.id, "name": t.name} for t in alive_law_abiding
                ],
                "round": state.get("night_round", 1),
                "round_cap": NIGHT_ROUND_CAP,
                "prior_picks": prior_picks,
            }
        )
        target_id = value if isinstance(value, str) else ""
        if target_id not in valid_ids:
            # UI is responsible for returning valid ids; fall back rather than
            # hang if something slips through.
            target_id = random.choice(alive_law_abiding).id
    else:
        # AI pointer. Thread the by-name picks-so-far context (Spec 015 §2.4):
        # completed rounds from night_rounds_log plus this round's picks made
        # before this pointer's turn, so the AI Mafioso can converge on a
        # shared target. Mafia-only context (Spec 013 knowledge-boundary): the
        # pointing prompt is only ever invoked for a Mafioso.
        prior_picks = _render_prior_picks(
            players=players,
            rounds_log=state.get("night_rounds_log", []),
            current_round_picks=state.get("night_round_picks", {}),
            exclude_pointer_id=pointer_id,
        )
        target_id = _ai_pick_target(
            alive_law_abiding=alive_law_abiding,
            mafia=pointer,
            prior_picks=prior_picks,
        )

    new_picks: dict[str, str] = dict(state.get("night_round_picks", {}))
    new_picks[pointer_id] = target_id
    return {
        "night_round_picks": new_picks,
        "night_pointer_index": index + 1,
    }


def route_after_mafia_point(state: GameState) -> str:
    """Pure router off ``mafia_point``.

    * Empty order (no-mafia / no-target guard) → ``resolve_night_kill``.
    * More pointers remain this round → ``mafia_point`` (next pointer).
    * Round complete: unanimous OR round cap (3) reached → ``resolve_night_kill``;
      otherwise → ``mafia_round_start`` (play another round).
    """
    order = state.get("night_mafia_order", [])
    if not order:
        return "resolve_night_kill"

    index = state.get("night_pointer_index", 0)
    if index < len(order):
        return "mafia_point"

    # Round complete.
    round_picks = state.get("night_round_picks", {})
    unanimous = len(set(round_picks.values())) == 1
    if unanimous or state.get("night_round", 1) >= NIGHT_ROUND_CAP:
        return "resolve_night_kill"
    return "mafia_round_start"


def resolve_night_kill(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
    cycle = state.get("cycle", 1)
    # Spec 015 §2.3: resolve from the *deciding* round's picks — the round that
    # ended the loop (unanimous target, or the final round on the cap). The
    # tally / plurality / random-tie-break body below is unchanged from the
    # single-round rule; it now operates on that deciding round.
    night_picks = state.get("night_round_picks", {})
    players = state.get("players", {})

    # If the deciding round produced no picks (e.g., nobody left to target),
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
            # Only the victim's alive flag changes; every other field (persona
            # included) carries forward via ``replace``.
            updated[pid] = dataclasses.replace(player, is_alive=False)
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
