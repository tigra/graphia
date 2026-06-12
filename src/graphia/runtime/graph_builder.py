"""Runtime-side graph compilation.

The spec-001 ``build_graph`` in :mod:`graphia.graph` is local-mode-shaped:
it generates a thread_id from wall-clock time and writes the SQLite
checkpoint under :attr:`GraphiaConfig.checkpoint_dir`. The AgentCore Runtime
entry-point needs a different shape: the thread_id is supplied by the
caller (it identifies a game session across invocations) and the
checkpoint must live on the container's tmpfs.

Both modes share node wiring + edges via :func:`graphia.graph._assemble_graph`,
so the only code that lives here is the bit that genuinely differs between
modes — turning the caller-supplied ``thread_id`` and ``checkpoint_dir``
into a SqliteSaver. Earlier versions of this module hand-mirrored the full
topology, and a Slice 8.4 plumbing change that landed in ``build_graph`` was
missed here, leaving the deployed Runtime with no career-event emitter; the
shared helper removes that whole class of drift.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph

from graphia.career_events import (
    CareerEventEmitter,
    NoOpCareerEventEmitter,
)
from graphia.diary_store import DiaryStore, InProcessDiaryStore
from graphia.graph import _assemble_graph, make_checkpoint_serde


def build_runtime_graph(
    thread_id: str,
    checkpoint_dir: Path,
    diary_store: DiaryStore | None = None,
    *,
    career_emitter: CareerEventEmitter | None = None,
) -> CompiledStateGraph:
    """Compile the Graphia StateGraph with a caller-supplied thread_id.

    Topology + node wiring live in :func:`graphia.graph._assemble_graph`,
    shared with local-mode :func:`graphia.graph.build_graph`. This wrapper
    only handles what's genuinely different between modes: the
    AgentCore-Runtime-supplied ``thread_id`` (instead of a wall-clock one)
    and a tmpfs ``checkpoint_dir`` for the per-session SQLite file at
    ``<checkpoint_dir>/<thread_id>.sqlite``.

    ``diary_store`` and ``career_emitter`` default to in-process / no-op
    so tests that compile this graph directly need no remote services;
    the production Runtime entrypoint supplies real instances built from
    :func:`graphia.diary_store.make_diary_store` /
    :func:`graphia.career_events.make_career_emitter`.
    """
    if diary_store is None:
        diary_store = InProcessDiaryStore()
    if career_emitter is None:
        career_emitter = NoOpCareerEventEmitter()

    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    db_path = checkpoint_dir / f"{thread_id}.sqlite"

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn, serde=make_checkpoint_serde())

    return _assemble_graph(
        diary_store=diary_store,
        career_emitter=career_emitter,
        game_id=thread_id,
        saver=saver,
    )
