"""Gateway-fronted Lambda handler for the ``diary_read`` MCP tool.

Per ADR 005 the agent's diary read path is:

    Agent (Runtime) ── MCP/SigV4 ──▶ Gateway ── invoke ──▶ this Lambda ──▶ Memory

Gateway invokes the Lambda with a JSON event whose keys are the
``inputSchema.properties`` values (see ``gateway-add-target-lambda.md``):

    {
      "game_id":   "<thread_id>",
      "player_id": "<player_id>"
    }

The Lambda returns a JSON-serialisable dict; Gateway wraps it into an MCP
``CallToolResult`` for the agent:

    {
      "entries": [
        {"night_index": 0, "content": "..."},
        ...
      ]
    }

Implementation note: the diary-read logic is **duplicated** from
``src/graphia/diary_store.py::AgentCoreMemoryDiaryStore.read`` (ADR 005
calls this out as acceptable for v1).
"""

from __future__ import annotations

import json
import logging
import os

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.models.filters import (
    EventMetadataFilter,
    LeftExpression,
    OperatorType,
    RightExpression,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


_DIARY_ENTRY_KIND = "diary_entry"
_METADATA_KIND_KEY = "kind"


def _memory_id() -> str:
    value = os.environ.get("GRAPHIA_MEMORY_ID")
    if not value:
        raise RuntimeError("GRAPHIA_MEMORY_ID env var is not set")
    return value


def _region() -> str | None:
    return os.environ.get("AWS_REGION")


def _entry_from_event(event: dict) -> dict | None:
    """Parse a Memory event back into a {night_index, content} dict.

    Mirrors ``_entry_from_event`` in ``src/graphia/diary_store.py``.
    Returns ``None`` for events that aren't diary entries.
    """
    payload = event.get("payload") or []
    for item in payload:
        conv = item.get("conversational")
        if conv is None:
            continue
        text = (conv.get("content") or {}).get("text")
        if not text:
            continue
        try:
            body = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(body, dict) or body.get("kind") != _DIARY_ENTRY_KIND:
            continue
        night_index = body.get("night_index")
        content = body.get("content")
        if not isinstance(night_index, int) or not isinstance(content, str):
            continue
        return {"night_index": night_index, "content": content}
    return None


def _read(game_id: str, player_id: str) -> list[dict]:
    """List diary entries for (game_id, player_id), sorted by night_index.

    Mirrors ``AgentCoreMemoryDiaryStore.read`` in
    ``src/graphia/diary_store.py``.
    """
    kind_filter: EventMetadataFilter = {
        "left": LeftExpression.build(_METADATA_KIND_KEY),
        "operator": OperatorType.EQUALS_TO.value,
        "right": RightExpression.build(_DIARY_ENTRY_KIND),
    }
    client = MemoryClient(region_name=_region())
    events = client.list_events(
        memory_id=_memory_id(),
        actor_id=player_id,
        session_id=game_id,
        event_metadata=[kind_filter],
        include_payload=True,
    )
    entries: list[dict] = []
    for event in events:
        parsed = _entry_from_event(event)
        if parsed is not None:
            entries.append(parsed)
    entries.sort(key=lambda e: e["night_index"])
    return entries


def lambda_handler(event, context):
    """Gateway-invoked Lambda entry point."""
    logger.info("diary_read invoked, keys=%s", sorted((event or {}).keys()))
    game_id = event["game_id"]
    player_id = event["player_id"]

    entries = _read(game_id, player_id)
    return {"entries": entries}
