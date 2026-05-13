"""Per-game diary store abstraction with parallel local + remote impls.

Spec 002 §2.4. The diary store is the per-game, per-player private journal
surface — each AI player writes one entry per Night with hidden reasoning,
and only the owning player (plus the Moderator, conceptually) ever reads
it back. In Slice 6 the entries are placeholder text; Phase 6 will replace
them with the AI's real diary content.

Two implementations live behind one Protocol so the rest of the engine
stays mode-agnostic:

- ``InProcessDiaryStore`` — dict-backed, thread-safe. Used in local mode
  and inside the Runtime container as the Slice 4 placeholder. Lifted
  here from ``graphia.runtime.diary_store`` (which is now removed) so the
  schema and the local impl live in one place.

- ``AgentCoreMemoryDiaryStore`` — wraps :class:`bedrock_agentcore.memory.MemoryClient`
  (verified against bedrock-agentcore==1.9.0; the skill's reference names
  ``AgentCoreMemory``, which does not exist in this SDK version). One
  Memory *event* per diary entry; ``(memory_id, actor_id=player_id,
  session_id=game_id)`` is the natural mapping onto the SDK's session
  model. ``night_index`` is mirrored into the event metadata as a
  zero-padded ``StringValue`` so list-time filtering (which the SDK only
  exposes for strings via ``EventMetadataFilter``) stays exact-match.
  Reads use ``list_events`` with a server-side ``(kind == diary_entry)``
  filter and then sort client-side by ``night_index``; the per-pair
  scoping already happens via the actor/session keys.

Note on design deviation from spec 002 §2.4's hint that
``InProcessDiaryStore`` is "backed by ``PlayerState.diary_entries`` in
``GameState``": for Slice 6 we keep ``InProcessDiaryStore`` self-contained
(dict-backed, same as the Slice 4 placeholder). Coupling the diary
surface to the LangGraph reducer is out of scope here and lands later
if/when Phase 6's AI diary writes actually need to participate in the
graph's checkpoint.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from graphia.config import GraphiaConfig


@dataclass(frozen=True, slots=True)
class DiaryEntry:
    """One private night-phase diary entry for a single player."""

    night_index: int
    content: str


class DiaryStore(Protocol):
    """Per-game diary surface; see module docstring."""

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        """Append a new diary entry for ``(game_id, player_id)``."""
        ...

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        """Return all entries for ``(game_id, player_id)``, sorted by night_index.

        Unknown pairs return ``[]``. The returned list is a fresh copy —
        callers cannot mutate the store via the result.
        """
        ...


class InProcessDiaryStore:
    """Dict-backed, thread-safe diary store. Local mode + Runtime placeholder.

    Keyed by ``(game_id, player_id)``. Sessions are ephemeral — the store
    has the lifetime of the host process (local: the Textual app; remote
    Slice 4-style placeholder: the Runtime microVM session).
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], list[DiaryEntry]] = {}
        self._lock = threading.Lock()

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        entry = DiaryEntry(night_index=night_index, content=content)
        with self._lock:
            self._entries.setdefault((game_id, player_id), []).append(entry)

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        with self._lock:
            stored = self._entries.get((game_id, player_id), [])
            return sorted(stored, key=lambda e: e.night_index)

    def clear(self, game_id: str | None = None) -> None:
        """Drop entries — for ``game_id`` if given, otherwise everything.

        Convenience for tests and session-end cleanup.
        """
        with self._lock:
            if game_id is None:
                self._entries.clear()
            else:
                stale = [k for k in self._entries if k[0] == game_id]
                for key in stale:
                    del self._entries[key]


_DIARY_ENTRY_KIND = "diary_entry"
_METADATA_KIND_KEY = "kind"
_METADATA_NIGHT_INDEX_KEY = "night_index"
# Zero-pad night_index so the StringValue-only metadata filter still gives
# a deterministic key shape; the actual ordering happens client-side
# against the parsed int from the message body.
_NIGHT_INDEX_WIDTH = 4


class AgentCoreMemoryDiaryStore:
    """AgentCore Memory-backed diary store for remote mode.

    ``memory_id`` identifies the provisioned Memory resource (one per
    Runtime workload, per spec 002 §2.6). The SDK client is constructed
    lazily on first call so importing this module does not require AWS
    credentials.
    """

    def __init__(self, memory_id: str, region_name: str | None = None) -> None:
        if not memory_id:
            raise ValueError("memory_id is required for AgentCoreMemoryDiaryStore")
        self._memory_id = memory_id
        self._region_name = region_name
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            from bedrock_agentcore.memory import MemoryClient

            self._client = MemoryClient(region_name=self._region_name)
        return self._client

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        body = {
            "kind": _DIARY_ENTRY_KIND,
            "game_id": game_id,
            "player_id": player_id,
            "night_index": night_index,
            "content": content,
        }
        client = self._get_client()
        client.create_event(
            memory_id=self._memory_id,
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

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        from bedrock_agentcore.memory.models.filters import (
            EventMetadataFilter,
            LeftExpression,
            OperatorType,
            RightExpression,
        )

        client = self._get_client()
        # Server-side filter on kind keeps non-diary events (if any ever
        # share the same actor/session pair) out of the result; exact
        # (game_id, player_id) scoping already happens via the actor and
        # session identifiers.
        kind_filter: EventMetadataFilter = {
            "left": LeftExpression.build(_METADATA_KIND_KEY),
            "operator": OperatorType.EQUALS_TO.value,
            "right": RightExpression.build(_DIARY_ENTRY_KIND),
        }
        events = client.list_events(
            memory_id=self._memory_id,
            actor_id=player_id,
            session_id=game_id,
            event_metadata=[kind_filter],
            include_payload=True,
        )

        entries: list[DiaryEntry] = []
        for event in events:
            entry = _entry_from_event(event)
            if entry is not None:
                entries.append(entry)
        return sorted(entries, key=lambda e: e.night_index)


def _entry_from_event(event: dict) -> DiaryEntry | None:
    """Parse a Memory event back into a ``DiaryEntry``.

    Returns ``None`` if the event isn't a diary entry (shape we don't
    recognise) — defensive against shared-session storage future-proofing.
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
        return DiaryEntry(night_index=night_index, content=content)
    return None


def make_diary_store(config: "GraphiaConfig") -> DiaryStore:
    """Select the diary store implementation based on whether a Memory is configured.

    Gates on ``config.memory_id`` (the actual signal — "does this process
    have access to an AgentCore Memory resource?") rather than on
    ``config.remote_mode`` (which means "is the local UI invoking a remote
    Runtime?"). The two concerns are orthogonal: a Runtime container has
    ``memory_id`` set but never knows nor cares about ``remote_mode``; a
    local-mode developer might point at a real Memory for ad-hoc inspection
    by setting ``GRAPHIA_MEMORY_ID`` without flipping ``--remote``.
    """
    if config.memory_id:
        return AgentCoreMemoryDiaryStore(
            memory_id=config.memory_id, region_name=config.aws_region
        )
    return InProcessDiaryStore()
