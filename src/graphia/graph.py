"""Compiled LangGraph assembly and per-thread checkpoint wiring."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from functools import partial

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from graphia.config import GraphiaConfig
from graphia.diary_store import DiaryStore, make_diary_store
from graphia.nodes import (
    assign_roles,
    check_win_condition,
    collect_name,
    collect_votes,
    day_close,
    day_open,
    day_turn,
    end_screen,
    first_night_mafia_intros,
    generate_roster,
    introduce_roster,
    mafia_pointing,
    night_close,
    night_open,
    resolve_night_kill,
    resolve_vote,
    reveal_role,
    route_after_night_open,
    route_after_win_day,
    route_after_win_night,
    route_collect_votes,
    route_day_turn_or_vote,
    vote_prompt,
)
from graphia.state import GameState


def build_graph(
    config: GraphiaConfig,
    *,
    diary_store: DiaryStore | None = None,
) -> tuple[CompiledStateGraph, str]:
    # Slice 6 sub-task 3: bind a ``DiaryStore`` into the Night-close write
    # site. Tests that don't care can leave ``diary_store=None`` and the
    # factory picks the right impl per :attr:`GraphiaConfig.remote_mode`.
    if diary_store is None:
        diary_store = make_diary_store(config)

    config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    thread_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    db_path = config.checkpoint_dir / f"{thread_id}.sqlite"

    # Open the connection directly rather than via from_conn_string's context
    # manager, which would close the DB as soon as this frame goes out of scope.
    # The graph process owns the lifetime for the duration of the game.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)

    builder: StateGraph = StateGraph(GameState)
    builder.add_node("collect_name", collect_name)
    builder.add_node("generate_roster", generate_roster)
    builder.add_node("assign_roles", assign_roles)
    builder.add_node("introduce_roster", introduce_roster)
    builder.add_node("reveal_role", reveal_role)
    builder.add_node("first_night_mafia_intros", first_night_mafia_intros)
    builder.add_node("night_open", night_open)
    builder.add_node("mafia_pointing", mafia_pointing)
    builder.add_node("resolve_night_kill", resolve_night_kill)
    # ``night_close`` closes over the diary store + game id so the per-Night
    # placeholder writes don't need to reach into module-level singletons.
    builder.add_node(
        "night_close",
        partial(night_close, diary_store=diary_store, game_id=thread_id),
    )
    builder.add_node("day_open", day_open)
    builder.add_node("day_turn", day_turn)
    builder.add_node("vote_prompt", vote_prompt)
    builder.add_node("collect_votes", collect_votes)
    builder.add_node("resolve_vote", resolve_vote)
    builder.add_node("day_close", day_close)
    # Slice 8: win-condition detection + end screen. The same pure-read
    # function is registered under two node names so each check site can
    # own a dedicated conditional fan-out (night → night_close fallthrough,
    # day → day_turn / day_close fallthrough).
    builder.add_node("check_win_night", check_win_condition)
    builder.add_node("check_win_day", check_win_condition)
    builder.add_node("end_screen", end_screen)

    builder.add_edge(START, "collect_name")
    builder.add_edge("collect_name", "generate_roster")
    builder.add_edge("generate_roster", "assign_roles")
    builder.add_edge("assign_roles", "introduce_roster")
    builder.add_edge("introduce_roster", "reveal_role")
    builder.add_edge("reveal_role", "first_night_mafia_intros")
    builder.add_edge("first_night_mafia_intros", "night_open")
    # Slice 9: the draw safety cap short-circuits Night setup to end_screen
    # when night_open detects cycle >= 20. Otherwise, proceed to pointing.
    builder.add_conditional_edges(
        "night_open",
        route_after_night_open,
        {
            "end_screen": "end_screen",
            "mafia_pointing": "mafia_pointing",
        },
    )
    builder.add_edge("mafia_pointing", "resolve_night_kill")
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

    # day_turn branches: vote-initiated → vote_prompt, else loop or close.
    builder.add_conditional_edges(
        "day_turn",
        route_day_turn_or_vote,
        {
            "vote_prompt": "vote_prompt",
            "day_turn": "day_turn",
            "day_close": "day_close",
        },
    )

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

    graph = builder.compile(checkpointer=saver)
    return graph, thread_id


def make_run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
