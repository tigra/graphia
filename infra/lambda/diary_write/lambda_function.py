"""Gateway-fronted Lambda handler for the ``diary_write`` MCP tool.

Per ADR 005 the agent's diary write path is:

    Agent (Runtime) ── MCP/SigV4 ──▶ Gateway ── invoke ──▶ this Lambda ──▶ Memory

Gateway invokes the Lambda with a JSON event whose keys are the
``inputSchema.properties`` values (see ``gateway-add-target-lambda.md``):

    {
      "game_id":     "<thread_id>",
      "player_id":   "<player_id>",
      "night_index": 0,
      "content":     "diary text"
    }

The Lambda returns a JSON-serialisable dict; Gateway wraps it into an MCP
``CallToolResult`` for the agent.

Implementation note: the diary-write logic is **duplicated** from
``src/graphia/diary_store.py::AgentCoreMemoryDiaryStore.write`` (ADR 005
calls this out as acceptable for v1 — ~30 lines of duplication versus the
complexity of vendoring the whole package). When the Graphia ``DiaryStore``
schema changes, both files must move in lockstep.
"""

from __future__ import annotations

import json
import logging
import os

from bedrock_agentcore.memory import MemoryClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


_DIARY_ENTRY_KIND = "diary_entry"
_METADATA_KIND_KEY = "kind"
_METADATA_NIGHT_INDEX_KEY = "night_index"
# Zero-pad night_index for deterministic StringValue ordering — matches
# AgentCoreMemoryDiaryStore in src/graphia/diary_store.py.
_NIGHT_INDEX_WIDTH = 4


def _memory_id() -> str:
    value = os.environ.get("GRAPHIA_MEMORY_ID")
    if not value:
        raise RuntimeError("GRAPHIA_MEMORY_ID env var is not set")
    return value


def _region() -> str | None:
    # Lambda sets AWS_REGION automatically; MemoryClient picks it up from the
    # standard chain so passing None is fine. Kept here for symmetry with the
    # in-process store and for future explicit pinning.
    return os.environ.get("AWS_REGION")


def _write(game_id: str, player_id: str, night_index: int, content: str) -> None:
    """Append one diary entry to AgentCore Memory.

    Mirrors ``AgentCoreMemoryDiaryStore.write`` in
    ``src/graphia/diary_store.py``.
    """
    body = {
        "kind": _DIARY_ENTRY_KIND,
        "game_id": game_id,
        "player_id": player_id,
        "night_index": night_index,
        "content": content,
    }
    client = MemoryClient(region_name=_region())
    client.create_event(
        memory_id=_memory_id(),
        actor_id=player_id,
        session_id=game_id,
        messages=[(json.dumps(body), "ASSISTANT")],
        metadata={
            _METADATA_KIND_KEY: {"stringValue": _DIARY_ENTRY_KIND},
            _METADATA_NIGHT_INDEX_KEY: {
                "stringValue": f"{night_index:0{_NIGHT_INDEX_WIDTH}d}"
            },
        },
    )


def lambda_handler(event, context):
    """Gateway-invoked Lambda entry point.

    ``event`` is the inputSchema-shaped dict; ``context`` carries
    AgentCore-specific metadata under ``context.client_context.custom``
    (e.g. ``bedrockAgentCoreToolName``). We don't currently branch on tool
    name — this Lambda exposes a single tool — but the standard envelope
    is documented for future use.
    """
    logger.info("diary_write invoked, keys=%s", sorted((event or {}).keys()))
    game_id = event["game_id"]
    player_id = event["player_id"]
    night_index = int(event["night_index"])
    content = event["content"]

    _write(game_id, player_id, night_index, content)
    return {"ok": True}
