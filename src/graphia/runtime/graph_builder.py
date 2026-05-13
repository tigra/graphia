"""Runtime-side graph compilation.

The spec-001 ``build_graph`` in :mod:`graphia.graph` is local-mode-shaped:
it generates a thread_id from wall-clock time and writes the SQLite
checkpoint under :attr:`GraphiaConfig.checkpoint_dir`. The AgentCore Runtime
entry-point needs a different shape: the thread_id is supplied by the
caller (it identifies a game session across invocations) and the
checkpoint must live on the container's tmpfs.

Rather than branching local mode's ``build_graph`` (forbidden by the
slice-4-sub-task-1 brief), this helper assembles the same nodes + edges
against a caller-supplied ``thread_id`` and ``checkpoint_dir``.

Topology is duplicated verbatim from :func:`graphia.graph.build_graph`;
if the local-mode graph evolves, mirror the change here. Equivalence is
exercised by spec-002 §4 integration tests.
"""

from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from graphia.diary_store import DiaryStore, InProcessDiaryStore
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


def build_runtime_graph(
    thread_id: str,
    checkpoint_dir: Path,
    diary_store: DiaryStore | None = None,
) -> CompiledStateGraph:
    """Compile the Graphia StateGraph with a caller-supplied thread_id.

    The SqliteSaver writes to ``<checkpoint_dir>/<thread_id>.sqlite``.
    The connection's lifetime is bound to the returned graph; the
    Runtime process owns it until the session terminates.

    ``diary_store`` is bound into the ``night_close`` node so the per-Night
    placeholder writes route to the right impl (AgentCore Memory in remote
    mode, in-process dict in local mode). The Runtime entrypoint supplies
    one constructed via :func:`graphia.diary_store.make_diary_store`; tests
    that compile this graph directly can leave it ``None`` and an
    in-process fallback is used.
    """
    if diary_store is None:
        diary_store = InProcessDiaryStore()

    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    db_path = checkpoint_dir / f"{thread_id}.sqlite"

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
    builder.add_conditional_edges(
        "night_open",
        route_after_night_open,
        {"end_screen": "end_screen", "mafia_pointing": "mafia_pointing"},
    )
    builder.add_edge("mafia_pointing", "resolve_night_kill")
    builder.add_edge("resolve_night_kill", "check_win_night")
    builder.add_conditional_edges(
        "check_win_night",
        route_after_win_night,
        {"end_screen": "end_screen", "night_close": "night_close"},
    )
    builder.add_edge("night_close", "day_open")
    builder.add_edge("day_open", "day_turn")
    builder.add_conditional_edges(
        "day_turn",
        route_day_turn_or_vote,
        {
            "vote_prompt": "vote_prompt",
            "day_turn": "day_turn",
            "day_close": "day_close",
        },
    )
    builder.add_edge("vote_prompt", "collect_votes")
    builder.add_conditional_edges(
        "collect_votes",
        route_collect_votes,
        {"collect_votes": "collect_votes", "resolve_vote": "resolve_vote"},
    )
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
    builder.add_edge("end_screen", END)
    builder.add_edge("day_close", "night_open")

    return builder.compile(checkpointer=saver)