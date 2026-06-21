"""Compiled LangGraph assembly and per-thread checkpoint wiring."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from functools import partial

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from graphia.career_events import (
    CareerEventEmitter,
    make_career_emitter,
)
from graphia.config import GraphiaConfig
from graphia.diary_store import DiaryStore, make_diary_store
from graphia.nodes import (
    assign_roles,
    check_win_condition,
    collect_name,
    collect_votes,
    day_close,
    day_open,
    day_round_reflect,
    day_turn,
    end_screen,
    first_night_mafia_intros,
    generate_personas,
    generate_roster,
    introduce_roster,
    mafia_point,
    mafia_round_start,
    night_close,
    night_open,
    resolve_night_kill,
    resolve_vote,
    reveal_role,
    route_after_mafia_point,
    route_after_night_open,
    route_after_win_day,
    route_after_win_night,
    route_collect_votes,
    route_day_turn_or_vote,
    vote_prompt,
)
from graphia.state import GameState, PlayerPersona, PlayerState


def make_checkpoint_serde() -> JsonPlusSerializer:
    """Checkpoint serializer with Graphia's custom state types allowlisted.

    ``PlayerState`` is a dataclass, so msgpack round-trips it as a typed
    extension; without an explicit allowlist langgraph warns on every
    deserialization ("Deserializing unregistered type graphia.state.PlayerState
    ...") and a future release will hard-block it. Registering the class here
    *extends* langgraph's built-in ``SAFE_MSGPACK_TYPES`` (langchain messages,
    stdlib types) — it does not replace them — but it does switch off the
    permissive warn-and-allow default: any *new* custom class stored in
    ``GameState`` must be added to this list or its deserialization will be
    blocked. Every ``SqliteSaver`` construction site (local ``build_graph``
    and the Runtime's ``build_runtime_graph``) must pass this serde so the
    allowlist can't drift between modes.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=[PlayerState, PlayerPersona])


def _with_career(
    node, *, career_emitter: CareerEventEmitter, game_id: str
):
    """Bind ``career_emitter`` + ``game_id`` into a node's kwargs.

    Returns a partial so each emitting node call-site stays free of module-
    level singletons — same shape the diary store uses for ``night_close``.
    """
    return partial(node, career_emitter=career_emitter, game_id=game_id)


def _assemble_graph(
    *,
    diary_store: DiaryStore,
    career_emitter: CareerEventEmitter,
    game_id: str,
    saver: SqliteSaver,
    recap_enabled: bool,
    recap_aware_reasoning_enabled: bool,
    role_guidance_enabled: bool = True,
    max_days: int = 12,
    context_window: int = 150,
    context_token_budget: int = 20000,
    private_thoughts_enabled: bool = True,
    night_roster_shuffle_enabled: bool = True,
) -> CompiledStateGraph:
    """Build the Graphia StateGraph topology and compile it with ``saver``.

    Shared by local-mode :func:`build_graph` and the AgentCore Runtime's
    :func:`graphia.runtime.graph_builder.build_runtime_graph`. The two
    builders differ only in how they obtain ``game_id`` (and the matching
    SQLite checkpoint location); the node graph itself — what's wrapped
    in service-injection ``partial``s, which conditional edges exist —
    lives here so the two modes can't drift apart.
    """
    # Service-injection pattern (mirrors the diary store): every node that
    # emits a per-action career event closes over the ``career_emitter`` +
    # ``game_id`` so node implementations stay free of module-level singletons.
    emit = partial(_with_career, career_emitter=career_emitter, game_id=game_id)

    builder: StateGraph = StateGraph(GameState)
    builder.add_node("collect_name", collect_name)
    builder.add_node("generate_roster", generate_roster)
    builder.add_node("assign_roles", emit(assign_roles))
    # Spec 016: persona generation runs AFTER role assignment (it needs roles to
    # tailor a Mafioso's cover-legend-plus-true-self vs. a Citizen's single
    # honest persona) and BEFORE the roster intro. Plain node — no career event.
    builder.add_node("generate_personas", generate_personas)
    builder.add_node("introduce_roster", introduce_roster)
    builder.add_node("reveal_role", reveal_role)
    builder.add_node("first_night_mafia_intros", first_night_mafia_intros)
    # Spec 023: the runaway Day cap is bound into ``night_open`` the same way the
    # recap flags are bound into the Day nodes — via ``partial`` over a value both
    # builders thread, so local and remote share one limit and can't drift.
    builder.add_node("night_open", partial(night_open, max_days=max_days))
    # Spec 015: the single-pass ``mafia_pointing`` is replaced by a bounded
    # multi-round pointing loop. ``mafia_round_start`` shuffles the round's
    # order (its own super-step, no interrupt) and ``mafia_point`` handles one
    # pointer per super-step (interrupt-safe for a human pointer).
    # Spec 030 (ADR 011): the Night-roster-shuffle flag is bound into
    # ``mafia_round_start`` — the same interrupt-free super-step that hosts the
    # candidate-roster draw it gates — via ``partial``, exactly as ``max_days``
    # rides ``night_open``. Both builders thread the flag so local and remote
    # can't drift.
    builder.add_node(
        "mafia_round_start",
        partial(
            mafia_round_start,
            night_roster_shuffle_enabled=night_roster_shuffle_enabled,
        ),
    )
    # Spec 028 (ADR 011): the private-thoughts flag is bound into ``mafia_point``
    # so an AI Mafioso's Night pick is grounded in its own accumulated Day
    # reflections (the third AI-decision prompt this family of flags reaches).
    # ``mafia_point`` also only READS the frozen spec-030 ``night_law_order``.
    builder.add_node(
        "mafia_point",
        partial(mafia_point, private_thoughts_enabled=private_thoughts_enabled),
    )
    builder.add_node("resolve_night_kill", emit(resolve_night_kill))
    # ``night_close`` closes over the diary store + game id so the per-Night
    # placeholder writes don't need to reach into module-level singletons.
    builder.add_node(
        "night_close",
        partial(night_close, diary_store=diary_store, game_id=game_id),
    )
    builder.add_node("day_open", day_open)
    # Spec 018: the end-of-round recap flag is bound into both Day nodes that
    # post it. ``day_turn`` composes it onto its existing career-emitter
    # binding; ``day_close`` (a plain node) is wrapped in its own ``partial``.
    # Both builders thread ``recap_enabled`` so local and remote can't drift.
    # Spec 019 (ADR 011): the recap-aware-reasoning flag is bound into the two
    # AI-decision nodes — ``day_turn`` (which calls ``_ai_day_action``) and
    # ``collect_votes`` (which calls ``_ai_ballot``) — alongside their existing
    # bindings, so it gates the ``{standings}`` prompt block in both modes.
    # Spec 024 (ADR 011): the role-guidance flag is bound into those SAME two
    # AI-decision nodes alongside the recap-aware flag, so it gates the tail
    # ``{role_guidance}`` prompt block in both modes.
    # Spec 025 (ADR 011): the fuller-discussion-window count + its defensive
    # token-budget cap are bound into the same two AI-decision nodes alongside
    # the recap-aware-reasoning flag, so ``_ai_day_action`` / ``_ai_ballot``
    # render the configured window in both modes.
    # Spec 028 (ADR 011): the private-thoughts flag is bound into the same two
    # AI-decision Day nodes (``day_turn`` / ``collect_votes``) alongside the
    # other flags, so it gates the ``{private_thoughts}`` block in both modes.
    builder.add_node(
        "day_turn",
        partial(
            emit(day_turn),
            recap_enabled=recap_enabled,
            recap_aware_reasoning_enabled=recap_aware_reasoning_enabled,
            role_guidance_enabled=role_guidance_enabled,
            context_window=context_window,
            context_token_budget=context_token_budget,
            private_thoughts_enabled=private_thoughts_enabled,
        ),
    )
    # Spec 028: the end-of-round reflection node. Its own super-step (so the
    # fan-out of N non-deterministic reflection calls is checkpointed once and
    # never replayed on a later ``day_turn`` interrupt). The flag + the window /
    # token-budget are bound here so the reflection prompt honours the same
    # discussion window the Day prompts use; flag-off makes the node a no-op.
    builder.add_node(
        "day_round_reflect",
        partial(
            day_round_reflect,
            private_thoughts_enabled=private_thoughts_enabled,
            context_window=context_window,
            context_token_budget=context_token_budget,
        ),
    )
    builder.add_node("vote_prompt", vote_prompt)
    builder.add_node(
        "collect_votes",
        partial(
            emit(collect_votes),
            recap_aware_reasoning_enabled=recap_aware_reasoning_enabled,
            role_guidance_enabled=role_guidance_enabled,
            context_window=context_window,
            context_token_budget=context_token_budget,
            private_thoughts_enabled=private_thoughts_enabled,
        ),
    )
    builder.add_node("resolve_vote", emit(resolve_vote))
    builder.add_node("day_close", partial(day_close, recap_enabled=recap_enabled))
    # Slice 8: win-condition detection + end screen. The same pure-read
    # function is registered under two node names so each check site can
    # own a dedicated conditional fan-out (night → night_close fallthrough,
    # day → day_turn / day_close fallthrough).
    builder.add_node("check_win_night", check_win_condition)
    builder.add_node("check_win_day", check_win_condition)
    builder.add_node("end_screen", emit(end_screen))

    builder.add_edge(START, "collect_name")
    builder.add_edge("collect_name", "generate_roster")
    builder.add_edge("generate_roster", "assign_roles")
    builder.add_edge("assign_roles", "generate_personas")
    builder.add_edge("generate_personas", "introduce_roster")
    builder.add_edge("introduce_roster", "reveal_role")
    builder.add_edge("reveal_role", "first_night_mafia_intros")
    builder.add_edge("first_night_mafia_intros", "night_open")
    # Spec 023: the runaway safeguard short-circuits Night setup to end_screen
    # when night_open detects cycle >= max_days (default 12 Days). Otherwise,
    # enter the pointing loop.
    builder.add_conditional_edges(
        "night_open",
        route_after_night_open,
        {
            "end_screen": "end_screen",
            "mafia_round_start": "mafia_round_start",
        },
    )
    # Spec 015 multi-round pointing loop. A round starts (shuffle the order),
    # then ``mafia_point`` runs one pointer per super-step. ``route_after_
    # mafia_point`` loops within the round, starts another round when split and
    # under the cap, or resolves on consensus / cap — mirroring the Day phase's
    # ``day_turn`` self-loop.
    builder.add_edge("mafia_round_start", "mafia_point")
    builder.add_conditional_edges(
        "mafia_point",
        route_after_mafia_point,
        {
            "mafia_point": "mafia_point",
            "mafia_round_start": "mafia_round_start",
            "resolve_night_kill": "resolve_night_kill",
        },
    )
    # After the Night kill, check the win condition before closing the Night.
    builder.add_edge("resolve_night_kill", "check_win_night")
    builder.add_conditional_edges(
        "check_win_night",
        route_after_win_night,
        {
            "end_screen": "end_screen",
            "night_close": "night_close",
        },
    )
    builder.add_edge("night_close", "day_open")
    builder.add_edge("day_open", "day_turn")

    # day_turn branches: vote-initiated → vote_prompt; round-cap → day_close;
    # a completed-round loop-back → day_round_reflect (spec 028) → day_turn; a
    # mid-round turn → day_turn directly.
    builder.add_conditional_edges(
        "day_turn",
        route_day_turn_or_vote,
        {
            "vote_prompt": "vote_prompt",
            "day_turn": "day_turn",
            "day_round_reflect": "day_round_reflect",
            "day_close": "day_close",
        },
    )
    # Spec 028: after reflecting, start the next speaking round.
    builder.add_edge("day_round_reflect", "day_turn")

    # Vote sub-graph: announce → poll one voter at a time → tally.
    builder.add_edge("vote_prompt", "collect_votes")
    builder.add_conditional_edges(
        "collect_votes",
        route_collect_votes,
        {
            "collect_votes": "collect_votes",
            "resolve_vote": "resolve_vote",
        },
    )
    # After the vote resolves, check win condition before deciding the
    # Day's next step. route_after_win_day delegates to the Slice 7 logic
    # (execution-this-cycle / caps / loop) when no winner is set.
    builder.add_edge("resolve_vote", "check_win_day")
    builder.add_conditional_edges(
        "check_win_day",
        route_after_win_day,
        {
            "end_screen": "end_screen",
            "day_turn": "day_turn",
            "day_close": "day_close",
        },
    )

    # Terminal edge: the recap message is posted and the graph halts.
    builder.add_edge("end_screen", END)

    # Day → Night cycle. night_open bumps cycle on re-entry.
    builder.add_edge("day_close", "night_open")

    return builder.compile(checkpointer=saver)


def build_graph(
    config: GraphiaConfig,
    *,
    diary_store: DiaryStore | None = None,
    career_emitter: CareerEventEmitter | None = None,
) -> tuple[CompiledStateGraph, str]:
    # Slice 6 sub-task 3: bind a ``DiaryStore`` into the Night-close write
    # site. Tests that don't care can leave ``diary_store=None`` and the
    # factory picks the right impl per :attr:`GraphiaConfig.remote_mode`.
    if diary_store is None:
        diary_store = make_diary_store(config)
    # Slice 8.4: per-action career-stats emitter. Mirrors the diary store —
    # the factory picks the right impl per ``GraphiaConfig.career_memory_id``
    # (NoOp locally, AgentCore Memory remotely). Tests that don't care can
    # leave ``career_emitter=None`` and inherit the NoOp local default.
    if career_emitter is None:
        career_emitter = make_career_emitter(config)

    config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    thread_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    db_path = config.checkpoint_dir / f"{thread_id}.sqlite"

    # Open the connection directly rather than via from_conn_string's context
    # manager, which would close the DB as soon as this frame goes out of scope.
    # The graph process owns the lifetime for the duration of the game.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn, serde=make_checkpoint_serde())

    graph = _assemble_graph(
        diary_store=diary_store,
        career_emitter=career_emitter,
        game_id=thread_id,
        saver=saver,
        recap_enabled=config.day_round_recap_enabled,
        recap_aware_reasoning_enabled=config.recap_aware_reasoning_enabled,
        role_guidance_enabled=config.role_guidance_enabled,
        max_days=config.max_days,
        context_window=config.context_window,
        context_token_budget=config.context_token_budget,
        private_thoughts_enabled=config.private_thoughts_enabled,
        night_roster_shuffle_enabled=config.night_roster_shuffle_enabled,
    )
    return graph, thread_id


def make_run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
