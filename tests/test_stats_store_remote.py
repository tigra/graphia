"""Spec 006 tests: the AgentCore long-term career stats store (remote mode).

These tests exercise :class:`graphia.stats_store.AgentCoreLongTermStatsStore`
WITHOUT ever touching real AWS. The store builds its boto3
``bedrock-agentcore`` client lazily in ``_get_client`` and caches it on
``self._client``; setting ``store._client`` to a fake BEFORE any call means
``import boto3`` is never reached. Every test here injects a fake immediately
after construction, so the suite stays fast and offline.

The fake (:class:`_FakeAgentCoreClient`) is a tiny in-memory stand-in for the
three data-plane operations the store calls:

* ``list_memory_records`` — returns the single stored career record (or none),
  recording the kwargs it was invoked with so tests can assert the namespace
  filter.
* ``batch_create_memory_records`` — first write; asserts no record existed.
* ``batch_update_memory_records`` — subsequent write; asserts a record existed.

Coverage:

* ``load`` lists by namespace and zeroes an empty career.
* First ``record`` creates with the exact payload (content / namespaces /
  strategy / stable ``requestIdentifier``).
* Second ``record`` updates the existing record and accumulates.
* A ``list_memory_records`` failure is tolerated (zeroed, no raise).
* The remote store and :class:`LocalFileStatsStore` agree on the final
  aggregate for an identical sequence of games (equivalence).
* ``_get_client`` never reaches boto3 once ``_client`` is pre-set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from graphia.stats_store import (
    AgentCoreLongTermStatsStore,
    CareerStats,
    GameSummary,
    LocalFileStatsStore,
    _career_from_json,
    fold,
)

# Re-use the established flat-GameSummary builder + style from the local-mode
# career-stats suite.
from test_career_stats import _summary

MEMORY_ID = "mem-career-123"
STRATEGY_ID = "strat-career-456"
NAMESPACE = "/career/human-career/"
ACTOR_ID = "human-career"


class _FakeAgentCoreClient:
    """In-memory stand-in for the boto3 ``bedrock-agentcore`` data-plane client.

    Holds at most one career record, shaped like the slice the store reads:
    ``{"memoryRecordId", "content": {"text": ...}, "namespaces": [...]}``.
    Every call is logged so tests can assert how the store invoked the API.
    No network, no boto3 — injected via ``store._client = fake`` before any
    store method runs.
    """

    def __init__(self) -> None:
        self._record: dict[str, Any] | None = None
        self._next_id = 1
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.list_should_raise: Exception | None = None

    def list_memory_records(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        if self.list_should_raise is not None:
            raise self.list_should_raise
        summaries = [self._record] if self._record is not None else []
        return {"memoryRecordSummaries": summaries}

    def batch_create_memory_records(
        self, memoryId: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:  # noqa: N803 — boto3 kwarg name
        assert self._record is None, "create called when a record already exists"
        assert len(records) == 1
        self.create_calls.append({"memoryId": memoryId, "records": records})
        item = records[0]
        record_id = f"rec-{self._next_id}"
        self._next_id += 1
        self._record = {
            "memoryRecordId": record_id,
            "content": dict(item["content"]),
            "namespaces": list(item["namespaces"]),
        }
        return {"successful": [{"memoryRecordId": record_id}]}

    def batch_update_memory_records(
        self, memoryId: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:  # noqa: N803 — boto3 kwarg name
        assert self._record is not None, "update called with no existing record"
        assert len(records) == 1
        self.update_calls.append({"memoryId": memoryId, "records": records})
        item = records[0]
        # The store must target the id we handed back from create.
        assert item["memoryRecordId"] == self._record["memoryRecordId"]
        self._record["content"] = dict(item["content"])
        return {"successful": [{"memoryRecordId": item["memoryRecordId"]}]}


def _make_store(fake: _FakeAgentCoreClient) -> AgentCoreLongTermStatsStore:
    """Construct the store and IMMEDIATELY inject the fake (no boto3 ever)."""
    store = AgentCoreLongTermStatsStore(
        memory_id=MEMORY_ID,
        strategy_id=STRATEGY_ID,
        namespace=NAMESPACE,
        actor_id=ACTOR_ID,
        region="us-east-1",
    )
    store._client = fake  # pre-set: _get_client returns this, never imports boto3
    return store


def _stored_career(fake: _FakeAgentCoreClient) -> CareerStats:
    """Parse the fake's single stored record back into a ``CareerStats``."""
    assert fake._record is not None
    return _career_from_json(json.loads(fake._record["content"]["text"]))


# --------------------------------------------------------------------------
# 1. load lists by namespace, zeroed when empty
# --------------------------------------------------------------------------


def test_load_empty_returns_zeroed_and_lists_by_namespace() -> None:
    """A fresh (record-less) store loads a zeroed career, filtered by namespace."""
    fake = _FakeAgentCoreClient()
    store = _make_store(fake)

    stats = store.load()

    assert stats == CareerStats()
    assert len(fake.list_calls) == 1
    call = fake.list_calls[0]
    assert call["memoryId"] == MEMORY_ID
    assert call["namespace"] == NAMESPACE


# --------------------------------------------------------------------------
# 2. first record() -> create with exact payload
# --------------------------------------------------------------------------


def test_first_record_creates_with_exact_payload() -> None:
    """First ``record`` issues a create whose content is the folded aggregate."""
    fake = _FakeAgentCoreClient()
    store = _make_store(fake)
    summary = _summary(
        human_role="mafia",
        outcome="mafia_win",
        human_won=True,
        rounds=4,
        votes_called=2,
        night_attempts=3,
        night_successes=1,
    )

    returned = store.record(summary)

    expected = fold(CareerStats(), summary)
    # Exactly one create, no update.
    assert len(fake.create_calls) == 1
    assert fake.update_calls == []

    item = fake.create_calls[0]["records"][0]
    # Content parses back to the folded aggregate.
    parsed = _career_from_json(json.loads(item["content"]["text"]))
    assert parsed == expected
    # Payload metadata: namespace, strategy, and the STABLE career identity.
    assert item["namespaces"] == [NAMESPACE]
    assert item["memoryStrategyId"] == STRATEGY_ID
    assert item["requestIdentifier"] == ACTOR_ID
    assert item["requestIdentifier"] == "human-career"  # not a per-game id
    # Returned aggregate equals the fold.
    assert returned == expected


# --------------------------------------------------------------------------
# 3. second record() -> update + accumulation
# --------------------------------------------------------------------------


def test_second_record_updates_existing_and_accumulates() -> None:
    """A follow-up ``record`` updates by id; a later ``load`` reads the total."""
    fake = _FakeAgentCoreClient()
    store = _make_store(fake)
    first = _summary(
        human_role="mafia", outcome="mafia_win", human_won=True, rounds=3
    )
    second = _summary(
        human_role="law_abiding",
        outcome="law_abiding_win",
        human_won=True,
        rounds=5,
    )

    store.record(first)
    created_id = fake._record["memoryRecordId"]

    returned = store.record(second)

    # The second write is an update against the id created by the first write.
    assert len(fake.create_calls) == 1
    assert len(fake.update_calls) == 1
    assert fake.update_calls[0]["records"][0]["memoryRecordId"] == created_id

    expected = fold(fold(CareerStats(), first), second)
    assert returned == expected
    # A fresh load reads the accumulated totals back.
    loaded = store.load()
    assert loaded == expected
    assert loaded.games_total == 2
    assert loaded.role_wins("mafia") == 1
    assert loaded.role_wins("law_abiding") == 1
    assert loaded.sum_rounds_completed == 8


# --------------------------------------------------------------------------
# 4. error tolerance — list failure yields a zeroed aggregate, no raise
# --------------------------------------------------------------------------


def test_load_tolerates_list_error_returns_zeroed() -> None:
    """A ``list_memory_records`` failure during ``load`` is swallowed (zeroed)."""
    fake = _FakeAgentCoreClient()
    fake.list_should_raise = RuntimeError("AccessDenied / throttle / etc.")
    store = _make_store(fake)

    stats = store.load()  # must not raise

    assert stats == CareerStats()


# --------------------------------------------------------------------------
# 5. EQUIVALENCE — local file vs. remote AgentCore over the same game sequence
# --------------------------------------------------------------------------


def test_local_and_remote_stores_agree_on_final_aggregate(tmp_path: Path) -> None:
    """Identical game sequences fold to identical aggregates in both stores."""
    summaries = [
        _summary(
            human_role="mafia",
            outcome="mafia_win",
            human_won=True,
            rounds=3,
            votes_called=1,
            ballots_cast=2,
            night_attempts=2,
            night_successes=2,
            night_victims=1,
            day_executions=0,
        ),
        _summary(
            human_role="law_abiding",
            outcome="law_abiding_win",
            human_won=True,
            rounds=5,
            votes_called=3,
            ballots_cast=1,
            night_attempts=0,
            night_successes=0,
            night_victims=2,
            day_executions=3,
        ),
        _summary(
            human_role="mafia",
            outcome="law_abiding_win",
            human_won=False,
            rounds=4,
            votes_called=0,
            ballots_cast=4,
            night_attempts=1,
            night_successes=0,
            night_victims=1,
            day_executions=1,
        ),
        _summary(
            human_role="law_abiding",
            outcome="draw",
            human_won=False,
            rounds=6,
            votes_called=2,
            ballots_cast=2,
            night_victims=0,
            day_executions=2,
        ),
        _summary(
            human_role="mafia",
            outcome="abandoned",
            human_won=False,
            rounds=9,
            votes_called=1,
            ballots_cast=0,
            night_attempts=4,
            night_successes=1,
            night_victims=3,
            day_executions=1,
        ),
    ]

    local = LocalFileStatsStore(tmp_path / "c.json")
    remote = _make_store(_FakeAgentCoreClient())

    local_final = CareerStats()
    remote_final = CareerStats()
    for summary in summaries:
        local_final = local.record(summary)
        remote_final = remote.record(summary)

    assert local_final == remote_final
    # And both agree with a load from their backing store.
    assert local.load() == remote.load() == local_final


# --------------------------------------------------------------------------
# 6. no real boto3 — _get_client never imports/calls boto3 once _client is set
# --------------------------------------------------------------------------


def test_no_real_boto3_when_client_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``_client`` pre-set, exercising the store never touches boto3."""
    import boto3

    def _explode(service_name: str, *args: Any, **kwargs: Any) -> Any:
        if service_name == "bedrock-agentcore":
            raise AssertionError(
                "boto3.client('bedrock-agentcore') reached — real-AWS leak; "
                "store._client should have short-circuited _get_client."
            )
        raise AssertionError(f"unexpected boto3.client({service_name!r}) call")

    monkeypatch.setattr(boto3, "client", _explode)

    fake = _FakeAgentCoreClient()
    store = _make_store(fake)

    # Exercise both read and both write paths; none may reach boto3.
    assert store.load() == CareerStats()
    store.record(_summary(human_role="mafia", outcome="mafia_win", human_won=True))
    store.record(
        _summary(human_role="law_abiding", outcome="law_abiding_win", human_won=True)
    )

    assert len(fake.create_calls) == 1
    assert len(fake.update_calls) == 1
