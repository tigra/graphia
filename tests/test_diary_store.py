"""Slice 6 sub-task 4: equivalence tests for both DiaryStore implementations.

Spec 002 §3.2 risk row: "Two parallel DiaryStore impls drift out of sync
semantically." The defence is a single, parametrised scenario list that
runs through *both* :class:`InProcessDiaryStore` and
:class:`AgentCoreMemoryDiaryStore`. A behavioural drift between the two
(e.g. one sorts by night_index and the other doesn't, or one silently
deduplicates same-(night_index) appends while the other appends) fails
the same scenario for one store but not the other — surfacing the
divergence immediately.

How the AgentCore variant stays AWS-free
----------------------------------------

The autouse ``safe_memory_client`` fixture in ``conftest.py`` already
patches ``bedrock_agentcore.memory.MemoryClient`` with a loud-failure
default that raises on any call. This module's tests override that
binding with :class:`FakeMemoryClient` — a small in-memory class
mirroring the SDK's actor/session keyed event surface. The two relevant
methods (``create_event``, ``list_events``) accept the same kwargs the
real SDK does and return shapes the production
``AgentCoreMemoryDiaryStore.read`` already knows how to parse.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from graphia.diary_store import (
    AgentCoreMemoryDiaryStore,
    DiaryEntry,
    DiaryStore,
    InProcessDiaryStore,
)


# --------------------------------------------------------------------------
# In-memory fake of ``bedrock_agentcore.memory.MemoryClient``
# --------------------------------------------------------------------------


class FakeMemoryClient:
    """In-memory stand-in for ``bedrock_agentcore.memory.MemoryClient``.

    Keyed by ``(memory_id, actor_id, session_id)`` so the
    ``AgentCoreMemoryDiaryStore`` scoping pattern (``actor_id=player_id``,
    ``session_id=game_id``) maps onto isolated storage buckets — exactly
    as the real Memory resource does on the server side.

    Append order is preserved (``list_events`` returns events in
    chronological insertion order); the diary store sorts client-side
    by ``night_index``. Mirroring this is intentional — it forces the
    sort-by-night_index scenario to actually exercise the store's
    sort logic rather than being incidentally correct.

    The constructor accepts and ignores ``region_name`` so the real
    diary store's ``MemoryClient(region_name=self._region_name)`` call
    works unchanged.
    """

    def __init__(self, region_name: str | None = None) -> None:
        self.region_name = region_name
        # (memory_id, actor_id, session_id) -> list of event dicts
        self._events: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        # Recorded calls — useful for richer assertions if needed later.
        self.create_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def create_event(
        self,
        memory_id: str,
        actor_id: str,
        session_id: str,
        messages: list[tuple[str, str]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {
                "memory_id": memory_id,
                "actor_id": actor_id,
                "session_id": session_id,
                "messages": messages,
                "metadata": metadata,
            }
        )
        # Mirror the SDK's payload shape: one ``conversational`` item per
        # (text, role) tuple in ``messages``.
        payload = [
            {"conversational": {"content": {"text": text}, "role": role}}
            for text, role in messages
        ]
        event_id = f"evt-{len(self._events.get((memory_id, actor_id, session_id), [])) + 1}"
        event = {
            "eventId": event_id,
            "memoryId": memory_id,
            "actorId": actor_id,
            "sessionId": session_id,
            "payload": payload,
            "metadata": metadata or {},
        }
        self._events.setdefault((memory_id, actor_id, session_id), []).append(event)
        return event

    def list_events(
        self,
        memory_id: str,
        actor_id: str,
        session_id: str,
        event_metadata: list[dict[str, Any]] | None = None,
        include_payload: bool = True,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        self.list_calls.append(
            {
                "memory_id": memory_id,
                "actor_id": actor_id,
                "session_id": session_id,
                "event_metadata": event_metadata,
                "include_payload": include_payload,
                "max_results": max_results,
            }
        )
        events = self._events.get((memory_id, actor_id, session_id), [])

        # Honour the kind filter the diary store sends so a test that
        # exercises filter-mismatch behaviour fails loudly. The real
        # service applies these server-side; mirroring it here keeps the
        # fake's semantics consistent with production.
        if event_metadata:
            events = [
                e
                for e in events
                if _event_matches_filters(e, event_metadata)
            ]
        return events[:max_results]


def _event_matches_filters(
    event: dict[str, Any], filters: list[dict[str, Any]]
) -> bool:
    """Mimic AgentCore Memory's server-side ``EventMetadataFilter`` semantics.

    Only handles ``EQUALS_TO`` against a ``stringValue`` — that's the
    only operator the diary store currently sends, matching the SDK's
    supported metadata-value type.
    """
    metadata = event.get("metadata") or {}
    for flt in filters:
        left = flt.get("left") or {}
        operator = flt.get("operator")
        right = flt.get("right") or {}
        key = left.get("metadataKey")
        if key is None:
            return False
        if operator != "EQUALS_TO":
            return False
        # ``right`` shape per the SDK: {"metadataValue": {"stringValue": "..."}}
        right_value = (right.get("metadataValue") or {}).get("stringValue")
        actual = metadata.get(key, {})
        if isinstance(actual, dict):
            actual_value = actual.get("stringValue")
        else:
            actual_value = actual
        if actual_value != right_value:
            return False
    return True


# --------------------------------------------------------------------------
# Store factories — fed into the parametrised scenarios
# --------------------------------------------------------------------------


def _make_in_process(_monkeypatch: pytest.MonkeyPatch) -> DiaryStore:
    return InProcessDiaryStore()


def _make_agentcore_memory(monkeypatch: pytest.MonkeyPatch) -> DiaryStore:
    # Override the loud-failure default that ``safe_memory_client`` (autouse)
    # installed. From this point on the diary store's lazy
    # ``from bedrock_agentcore.memory import MemoryClient`` resolves to the
    # in-memory fake.
    import bedrock_agentcore.memory as _agentcore_memory

    monkeypatch.setattr(_agentcore_memory, "MemoryClient", FakeMemoryClient)
    return AgentCoreMemoryDiaryStore(
        memory_id="test-memory-id", region_name="us-east-1"
    )


StoreFactory = Callable[[pytest.MonkeyPatch], DiaryStore]


@pytest.fixture(
    params=[
        pytest.param(_make_in_process, id="in-process"),
        pytest.param(_make_agentcore_memory, id="agentcore-memory"),
    ]
)
def store(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> DiaryStore:
    """Parametrised fixture yielding a fresh store for each implementation.

    A failure under one parameter id but not the other is the signal the
    equivalence suite is built to detect.
    """
    factory: StoreFactory = request.param
    return factory(monkeypatch)


# --------------------------------------------------------------------------
# Equivalence scenarios
# --------------------------------------------------------------------------


def test_round_trip_write_then_read_returns_all_entries(store: DiaryStore) -> None:
    """Writing N entries for one (game, player) yields exactly those N on read."""
    store.write("g1", "p1", 0, "night 0 thoughts")
    store.write("g1", "p1", 1, "night 1 thoughts")
    store.write("g1", "p1", 2, "night 2 thoughts")

    entries = store.read("g1", "p1")

    assert entries == [
        DiaryEntry(night_index=0, content="night 0 thoughts"),
        DiaryEntry(night_index=1, content="night 1 thoughts"),
        DiaryEntry(night_index=2, content="night 2 thoughts"),
    ]


def test_read_returns_entries_sorted_by_night_index(store: DiaryStore) -> None:
    """Entries written out of order come back sorted ascending by night_index.

    The fake ``list_events`` preserves insertion order on purpose — if the
    diary store's client-side sort were missing, this test would surface
    the divergence immediately on the AgentCore variant while the
    in-process variant (which sorts on read) would still pass.
    """
    store.write("g1", "p1", 2, "third")
    store.write("g1", "p1", 0, "first")
    store.write("g1", "p1", 1, "second")

    night_indices = [e.night_index for e in store.read("g1", "p1")]

    assert night_indices == [0, 1, 2]


def test_read_unknown_pair_returns_empty_list(store: DiaryStore) -> None:
    """Reading a (game, player) that was never written returns ``[]``."""
    assert store.read("game-never-written", "player-never-written") == []


def test_isolation_between_game_and_player_pairs(store: DiaryStore) -> None:
    """Writes to one (game, player) pair never leak into another pair's reads.

    Three distinct pairs are seeded with disjoint content. Each pair's
    read returns only its own entries; cross-pair leak (e.g. a shared
    backend bucket keyed only by player_id without the game scope) would
    fail at least one of the assertions.
    """
    store.write("g1", "p1", 0, "g1-p1 entry")
    store.write("g1", "p2", 0, "g1-p2 entry")
    store.write("g2", "p1", 0, "g2-p1 entry")

    assert store.read("g1", "p1") == [
        DiaryEntry(night_index=0, content="g1-p1 entry")
    ]
    assert store.read("g1", "p2") == [
        DiaryEntry(night_index=0, content="g1-p2 entry")
    ]
    assert store.read("g2", "p1") == [
        DiaryEntry(night_index=0, content="g2-p1 entry")
    ]


def test_append_only_semantics_preserve_duplicates(store: DiaryStore) -> None:
    """Two writes at the same ``night_index`` produce two distinct entries.

    Spec 002 §2.6: diary entries are append-only. A defensive in-place
    update by night_index would silently drop one of these entries —
    failing this test under exactly one of the two store implementations
    is the kind of drift this suite catches.
    """
    store.write("g1", "p1", 0, "first")
    store.write("g1", "p1", 0, "second")

    entries = store.read("g1", "p1")

    assert len(entries) == 2
    contents = sorted(e.content for e in entries)
    assert contents == ["first", "second"]
    # Both entries carry the same night_index — the only differentiator
    # is content.
    assert {e.night_index for e in entries} == {0}


# --------------------------------------------------------------------------
# Extra coverage scenarios — these are additional drift sentinels.
# --------------------------------------------------------------------------


def test_returned_list_is_independent_of_store_state(store: DiaryStore) -> None:
    """Mutating a returned list must not corrupt subsequent reads.

    Both stores promise a fresh copy. If one ever started returning the
    internal list directly, a caller mutating it would surface as a drift
    here.
    """
    store.write("g1", "p1", 0, "alpha")
    first = store.read("g1", "p1")
    first.append(DiaryEntry(night_index=99, content="injected"))
    first.clear()

    second = store.read("g1", "p1")
    assert second == [DiaryEntry(night_index=0, content="alpha")]


def test_writes_after_read_show_up_in_next_read(store: DiaryStore) -> None:
    """Interleaved write/read/write/read exposes any read-side caching bug."""
    store.write("g1", "p1", 0, "first")
    assert store.read("g1", "p1") == [DiaryEntry(night_index=0, content="first")]

    store.write("g1", "p1", 1, "second")
    assert store.read("g1", "p1") == [
        DiaryEntry(night_index=0, content="first"),
        DiaryEntry(night_index=1, content="second"),
    ]


# --------------------------------------------------------------------------
# AgentCore-specific invariants — these only run under the agentcore-memory
# variant and pin down the SDK wire shape the production diary store relies
# on. (Putting them here keeps all diary-store behaviour in one file.)
# --------------------------------------------------------------------------


def test_agentcore_write_passes_correct_actor_and_session_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``write`` maps ``player_id -> actor_id`` and ``game_id -> session_id``.

    Pinning the mapping at the SDK boundary stops a future refactor from
    silently swapping the two — which would still pass the round-trip
    test (because both stores would consistently use the wrong key) but
    would isolate per-player diaries by game in production rather than
    per-game by player as spec 002 §2.6 specifies.
    """
    import bedrock_agentcore.memory as _agentcore_memory

    monkeypatch.setattr(_agentcore_memory, "MemoryClient", FakeMemoryClient)

    store = AgentCoreMemoryDiaryStore(memory_id="m-1", region_name="us-east-1")
    store.write("game-42", "player-abc", 3, "secret reasoning")

    fake: FakeMemoryClient = store._get_client()  # type: ignore[assignment]
    assert len(fake.create_calls) == 1
    call = fake.create_calls[0]
    assert call["memory_id"] == "m-1"
    assert call["actor_id"] == "player-abc"
    assert call["session_id"] == "game-42"

    # The diary entry is encoded as a single ASSISTANT-role message whose
    # text is JSON-decodable into the structured payload.
    assert len(call["messages"]) == 1
    text, role = call["messages"][0]
    assert role == "ASSISTANT"
    body = json.loads(text)
    assert body == {
        "kind": "diary_entry",
        "game_id": "game-42",
        "player_id": "player-abc",
        "night_index": 3,
        "content": "secret reasoning",
    }


def test_agentcore_read_sends_kind_filter_to_list_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read`` filters server-side on ``kind == diary_entry``.

    Pins the filter shape so a future refactor of the metadata schema
    (e.g. renaming the kind key) breaks this test rather than silently
    returning unrelated events that happen to share the actor/session.
    """
    import bedrock_agentcore.memory as _agentcore_memory

    monkeypatch.setattr(_agentcore_memory, "MemoryClient", FakeMemoryClient)

    store = AgentCoreMemoryDiaryStore(memory_id="m-1", region_name="us-east-1")
    store.read("g1", "p1")

    fake: FakeMemoryClient = store._get_client()  # type: ignore[assignment]
    assert len(fake.list_calls) == 1
    call = fake.list_calls[0]
    assert call["memory_id"] == "m-1"
    assert call["actor_id"] == "p1"
    assert call["session_id"] == "g1"
    filters = call["event_metadata"]
    assert filters is not None and len(filters) == 1
    flt = filters[0]
    # The filter is keyed on "kind" with EQUALS_TO "diary_entry".
    assert flt["left"]["metadataKey"] == "kind"
    assert flt["operator"] == "EQUALS_TO"
    assert flt["right"]["metadataValue"]["stringValue"] == "diary_entry"


def test_agentcore_read_ignores_non_diary_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events without the diary-entry kind are skipped client-side too.

    Defence-in-depth: the server-side filter should already exclude them,
    but ``_entry_from_event`` also guards against unexpected shapes. A
    plain text event (no JSON body, no kind) returned by ``list_events``
    must not crash ``read`` — it returns an empty list, leaving the
    diary entries alone.
    """
    import bedrock_agentcore.memory as _agentcore_memory

    monkeypatch.setattr(_agentcore_memory, "MemoryClient", FakeMemoryClient)

    store = AgentCoreMemoryDiaryStore(memory_id="m-1", region_name="us-east-1")
    fake: FakeMemoryClient = store._get_client()  # type: ignore[assignment]

    # Inject a foreign event (non-JSON text, no diary_entry kind metadata)
    # directly into the fake's storage. The server-side filter is what
    # would normally hide it, but exercising that here would mean
    # bypassing the filter or marking it diary_entry; instead we keep the
    # filter on but flip the metadata to a different kind so the filter
    # naturally excludes it.
    fake._events[("m-1", "p1", "g1")] = [
        {
            "eventId": "evt-foreign",
            "payload": [
                {"conversational": {"content": {"text": "raw chatter"}, "role": "USER"}}
            ],
            "metadata": {"kind": {"stringValue": "other_event"}},
        }
    ]

    assert store.read("g1", "p1") == []


# --------------------------------------------------------------------------
# Slice 9 sub-task 1: explicit impl-vs-impl equivalence.
#
# The Slice-6 tests above are parametrised across both stores: each one runs
# on each implementation and asserts *that* implementation is independently
# correct. They never compare the two implementations to *each other* — so a
# drift that happened to keep each store internally self-consistent (e.g.
# both stores agreeing on a wrong-but-consistent behaviour, or a scenario the
# Slice-6 assertions don't pin precisely) could slip through.
#
# Spec 002 §4 risk: "two parallel DiaryStore impls drift out of sync
# semantically." The defence below is the sharper check: one fixed scenario
# set is run against a fresh ``InProcessDiaryStore`` and a fresh
# ``AgentCoreMemoryDiaryStore`` (Memory SDK mocked), and the two stores'
# observable outputs are asserted equal **to each other**. Any semantic
# divergence — different ordering, different empty-read behaviour, different
# isolation — fails the equality directly, naming both sides in the diff.
#
# DiaryEntry comparison
# ---------------------
# ``DiaryEntry`` is a ``frozen=True`` dataclass carrying only ``night_index``
# and ``content`` — no store-specific field — so two entries are directly
# ``==``-comparable and could be compared as-is. We nonetheless project each
# read result to a list of ``(night_index, content)`` tuples via
# ``_observable`` before comparing. Rationale: the projection is the explicit
# *observable contract* of ``read`` (the tuple a caller can actually act on),
# it keeps the equivalence assertion independent of any future store-specific
# field added to the dataclass, and a tuple-list diff reads more clearly in
# the failure output than a dataclass-repr diff. ``game_id`` / ``player_id``
# are deliberately absent from the tuple: they are *inputs* to ``read``, not
# fields of an entry — scoping/isolation is exercised by the read *keys* used
# in the scenarios, not by per-entry fields.
# --------------------------------------------------------------------------


def _observable(entries: list[DiaryEntry]) -> list[tuple[int, str]]:
    """Project a ``read`` result to its store-agnostic observable form.

    See the section comment above for why this is ``(night_index, content)``
    and not the full dataclass.
    """
    return [(e.night_index, e.content) for e in entries]


def _run_equivalence_scenarios(store: DiaryStore) -> dict[str, list[tuple[int, str]]]:
    """Run the fixed scenario set against one store, returning observable results.

    The same three scenarios run identically against whichever store is
    passed; the caller compares two stores' returned dicts for equality.

    Scenarios:

    - ``written``: write three entries (deliberately out of night_index
      order) for one ``(game, player)`` pair, then read them back. Exercises
      round-trip, append fan-in, and client-side sort in one shot.
    - ``empty``: read a ``(game, player)`` pair that was never written.
      Exercises the unknown-pair empty-read contract.
    - ``other_player``: read a *different* ``player_id`` than the one written
      to (same ``game_id``). Exercises per-player isolation — a leak would
      surface here as a non-empty result.
    """
    # Scenario (a): three entries for one pair, written out of order.
    store.write("equiv-game", "equiv-player", 2, "night two")
    store.write("equiv-game", "equiv-player", 0, "night zero")
    store.write("equiv-game", "equiv-player", 1, "night one")
    written = _observable(store.read("equiv-game", "equiv-player"))

    # Scenario (b): a pair that was never written to.
    empty = _observable(store.read("equiv-game-unwritten", "equiv-player-unwritten"))

    # Scenario (c): a different player_id in the same game as scenario (a).
    other_player = _observable(store.read("equiv-game", "different-player"))

    return {"written": written, "empty": empty, "other_player": other_player}


def test_implementations_are_observably_equivalent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both DiaryStore impls produce identical observable output for one scenario set.

    This is the explicit impl-vs-impl comparison the Slice-6 parametrised
    tests do not make. The same three scenarios run against a fresh
    ``InProcessDiaryStore`` and a fresh ``AgentCoreMemoryDiaryStore`` (Memory
    SDK mocked via :class:`FakeMemoryClient`), and the two stores' results
    are asserted equal to each other — semantic drift between the parallel
    implementations fails here.
    """
    in_process = _make_in_process(monkeypatch)
    agentcore = _make_agentcore_memory(monkeypatch)

    in_process_results = _run_equivalence_scenarios(in_process)
    agentcore_results = _run_equivalence_scenarios(agentcore)

    # Single equality over all three scenarios at once: a diff names exactly
    # which scenario diverged and shows both sides.
    assert in_process_results == agentcore_results

    # Pin the observable contract itself, so a *consistent* drift — both
    # stores agreeing on a wrong behaviour — is also caught. (Equality above
    # only proves the two agree; these assert they agree on the *right*
    # answer.)
    assert in_process_results["written"] == [
        (0, "night zero"),
        (1, "night one"),
        (2, "night two"),
    ]
    assert in_process_results["empty"] == []
    assert in_process_results["other_player"] == []


@pytest.mark.parametrize(
    "scenario",
    ["written", "empty", "other_player"],
)
def test_implementations_equivalent_per_scenario(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Per-scenario impl-vs-impl equivalence — one parametrised case per scenario.

    Functionally a finer-grained slice of
    :func:`test_implementations_are_observably_equivalent`: it isolates each
    scenario into its own case so a failure's parametrise id (``written`` /
    ``empty`` / ``other_player``) names the diverging scenario directly,
    matching the per-scenario style of the Slice-6 tests in this file. Each
    case builds its own fresh pair of stores — no shared state across cases.
    """
    in_process = _make_in_process(monkeypatch)
    agentcore = _make_agentcore_memory(monkeypatch)

    in_process_results = _run_equivalence_scenarios(in_process)
    agentcore_results = _run_equivalence_scenarios(agentcore)

    assert in_process_results[scenario] == agentcore_results[scenario]
