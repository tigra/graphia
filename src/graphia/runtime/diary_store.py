"""In-Runtime placeholder diary store.

Slice 4 placeholder; Slice 6 replaces with ``AgentCoreMemoryDiaryStore``
behind a ``DiaryStore`` Protocol that also covers an
``InProcessDiaryStore`` for local mode. For Slice 4 we only need a
Runtime-internal, thread-safe, dict-backed store that holds diary
entries keyed by ``(game_id, player_id)`` so the Runtime container has
a real (if disposable) diary surface ready to be swapped out.

No graph node calls into this yet — Slice 6 introduces the call sites
and the abstraction.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiaryEntry:
    """One private night-phase diary entry for a single player."""

    night_index: int
    content: str


class InRuntimeDiaryStore:
    """Dict-backed, thread-safe diary store living inside the Runtime process.

    Keyed by ``(game_id, player_id)``. Sessions are ephemeral — the store
    has the lifetime of the Runtime process, which matches an AgentCore
    Runtime microVM session.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], list[DiaryEntry]] = {}
        self._lock = threading.Lock()

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        """Append a new diary entry for ``(game_id, player_id)``."""
        entry = DiaryEntry(night_index=night_index, content=content)
        with self._lock:
            self._entries.setdefault((game_id, player_id), []).append(entry)

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        """Return a copy of all entries for ``(game_id, player_id)``.

        Sorted by ``night_index`` ascending. Returns an empty list if the
        key was never written. The returned list is a fresh copy — callers
        cannot mutate the store via the result.
        """
        with self._lock:
            stored = self._entries.get((game_id, player_id), [])
            return sorted(stored, key=lambda e: e.night_index)

    def clear(self, game_id: str | None = None) -> None:
        """Drop entries — for ``game_id`` if given, otherwise everything.

        Convenience for tests and session-end cleanup; the real Slice 6
        store will likely surface its own scoped-clear semantics.
        """
        with self._lock:
            if game_id is None:
                self._entries.clear()
            else:
                stale = [k for k in self._entries if k[0] == game_id]
                for key in stale:
                    del self._entries[key]
