"""Spec 006 / ADR 008 — ``AgentCoreCareerEventStore`` unit tests.

The store is the read-only remote-mode view over AgentCore long-term memory:

* ``load()`` lists the single namespace-scoped record and parses it via
  ``_career_from_json``; a missing record or a malformed payload yields a
  zeroed :class:`CareerStats` (the namespace genuinely has no record yet),
  but **AgentCore / boto3 errors propagate** so a broken remote setup fails
  loud instead of silently rendering panels from a zeroed aggregate.
* ``record(summary)`` returns ``fold(self.load(), summary)`` and does NOT
  write anything to AgentCore. Persistence is the emitter + Lambda's job
  (ADR 008); the store only folds locally so the post-game panel renders
  the right deltas while the async write catches up.

All AWS access is mocked at the lazy ``_client`` seam — we inject a fake
client into ``store._client`` BEFORE any call, so ``_get_client`` never
reaches ``boto3.client('bedrock-agentcore')``. No real Bedrock from tests.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from graphia.stats_store import (
    AgentCoreCareerEventStore,
    CareerStats,
    GameSummary,
    _career_to_json,
    fold,
)


class _FakeAgentCoreClient:
    """Minimal stand-in for the ``bedrock-agentcore`` data-plane client.

    Records every call as a ``(operation, kwargs)`` tuple in
    :attr:`calls`. ``list_memory_records`` returns the pre-set response;
    raising ``_raise_on_list`` exposes the "boto3 errors → zeroed" path.
    Every other operation (``create_event``, ``batch_create_memory_records``,
    ``batch_update_memory_records``) raises :class:`AssertionError` — the
    store contract is read-and-fold-only, so any write attempt is a bug.
    """

    def __init__(
        self,
        *,
        list_response: dict[str, Any] | None = None,
        raise_on_list: Exception | None = None,
    ) -> None:
        self._list_response = list_response or {"memoryRecordSummaries": []}
        self._raise_on_list = raise_on_list
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_memory_records(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_memory_records", kwargs))
        if self._raise_on_list is not None:
            raise self._raise_on_list
        return self._list_response

    def create_event(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        self.calls.append(("create_event", kwargs))
        raise AssertionError(
            "AgentCoreCareerEventStore must never call create_event"
        )

    def batch_create_memory_records(
        self, **kwargs: Any
    ) -> dict[str, Any]:  # pragma: no cover
        self.calls.append(("batch_create_memory_records", kwargs))
        raise AssertionError(
            "AgentCoreCareerEventStore must never call batch_create_memory_records"
        )

    def batch_update_memory_records(
        self, **kwargs: Any
    ) -> dict[str, Any]:  # pragma: no cover
        self.calls.append(("batch_update_memory_records", kwargs))
        raise AssertionError(
            "AgentCoreCareerEventStore must never call batch_update_memory_records"
        )


def _make_store(client: _FakeAgentCoreClient) -> AgentCoreCareerEventStore:
    """Build a store with the fake client pre-injected (no boto3 touch)."""
    store = AgentCoreCareerEventStore(
        career_memory_id="mem-test",
        namespace="/career/human-career/",
    )
    store._client = client
    return store


def test_load_lists_memory_records_by_namespace() -> None:
    """``load()`` calls ``list_memory_records`` with memoryId + namespace."""
    persisted = CareerStats(
        games_total=2,
        games_by_role={"mafia": 1, "law_abiding": 1},
        wins_by_role={"mafia": 1},
        outcome_split={"mafia_win": 1, "law_abiding_win": 1},
        completed_games=2,
        sum_rounds_completed=8,
    )
    client = _FakeAgentCoreClient(
        list_response={
            "memoryRecordSummaries": [
                {
                    "memoryRecordId": "rec-1",
                    "content": {"text": json.dumps(_career_to_json(persisted))},
                }
            ]
        }
    )
    store = _make_store(client)

    stats = store.load()

    assert client.calls and client.calls[0][0] == "list_memory_records"
    kwargs = client.calls[0][1]
    assert kwargs["memoryId"] == "mem-test"
    assert kwargs["namespace"] == "/career/human-career/"
    # And the content is parsed back into the same aggregate.
    assert stats == persisted


@pytest.mark.parametrize(
    "list_response",
    [
        {"memoryRecordSummaries": []},
        {},
        {
            "memoryRecordSummaries": [
                {"memoryRecordId": "rec-1", "content": {"text": "{bad json"}}
            ]
        },
        {
            "memoryRecordSummaries": [
                {"memoryRecordId": "rec-1", "content": {}}
            ]
        },
    ],
    ids=["empty-list", "no-key", "unparseable-json", "no-text-field"],
)
def test_load_returns_zeroed_on_empty_or_malformed(
    list_response: dict[str, Any],
) -> None:
    """Missing / malformed records yield a zeroed aggregate, no raise."""
    client = _FakeAgentCoreClient(list_response=list_response)
    store = _make_store(client)

    stats = store.load()

    assert stats == CareerStats()
    assert stats.games_total == 0


def test_load_raises_on_boto_error() -> None:
    """A boto3 ``list_memory_records`` failure propagates — remote-mode
    failures must be loud, never silently rendered as a zeroed aggregate
    (which would hide a broken IAM / network / config from the user)."""
    client = _FakeAgentCoreClient(
        raise_on_list=RuntimeError("simulated boto3 client error")
    )
    store = _make_store(client)

    with pytest.raises(RuntimeError, match="simulated boto3 client error"):
        store.load()


def test_record_returns_local_fold_without_writing() -> None:
    """``record(summary)`` returns ``fold(load(), summary)`` and writes nothing.

    The store is read-and-fold-only — persistence flows through the
    emitter + Lambda, not this method. The fake records ZERO calls to
    ``create_event`` / ``batch_create_memory_records`` /
    ``batch_update_memory_records``; only ``list_memory_records`` fires
    (from the embedded ``load`` call).
    """
    prior = CareerStats(
        games_total=1,
        games_by_role={"mafia": 1},
        wins_by_role={"mafia": 1},
        outcome_split={"mafia_win": 1},
        completed_games=1,
        sum_rounds_completed=3,
    )
    client = _FakeAgentCoreClient(
        list_response={
            "memoryRecordSummaries": [
                {
                    "memoryRecordId": "rec-1",
                    "content": {"text": json.dumps(_career_to_json(prior))},
                }
            ]
        }
    )
    store = _make_store(client)
    summary = GameSummary(
        human_role="law_abiding",
        outcome="law_abiding_win",
        human_won=True,
        rounds=4,
        votes_called=2,
        ballots_cast=3,
        night_attempts=0,
        night_successes=0,
        night_victims=1,
        day_executions=1,
    )

    result = store.record(summary)

    # Returned aggregate equals the pure local fold.
    assert result == fold(prior, summary)
    # No writes — only the list call from load().
    ops = [op for op, _ in client.calls]
    assert ops == ["list_memory_records"], (
        f"unexpected client calls: {client.calls!r}"
    )
