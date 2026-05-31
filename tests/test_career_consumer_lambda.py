"""Spec 006 / ADR 008 — career-consumer Lambda handler unit tests.

The Lambda lives at ``infra/lambda/career_consumer/lambda_function.py`` and
imports ``career_events`` / ``stats_store`` as top-level modules (the deploy
package vendors them next to ``lambda_function.py``). The CI tree does NOT
vendor them: we satisfy those imports by aliasing them in ``sys.modules`` to
the ``graphia.career_events`` / ``graphia.stats_store`` source modules
BEFORE importing the Lambda — that keeps the test free of any deploy-time
build step while still exercising the production code path byte-for-byte.

A file-local autouse fixture patches ``boto3.client`` so any AWS service
unaccompanied by a pre-injected fake raises immediately. Real boto3 retry
loops against dummy credentials are the documented failure mode for this
file (a prior testing-agent attempt timed out exactly on that). Every test
in this module pre-injects fakes for the cached ``_s3_client`` /
``_agentcore_client`` module-level slots before invoking the handler.

Tested scenarios:

* First delivery (``game_ended``) writes a new long-term record via
  ``batch_create_memory_records``; zero ``batch_update_memory_records``.
* Replay of the same delivery (session id already in ``games_folded``) is
  idempotent: zero ``batch_create`` / ``batch_update`` calls.
* Non-finalizer events (e.g. ``vote_initiated``) are dropped before any
  ``list_events`` / ``list_memory_records`` / ``batch_*`` call.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from graphia.career_events import (
    KIND_GAME_ENDED,
    KIND_NIGHT_RESOLVED,
    KIND_VOTE_INITIATED,
    KIND_VOTE_RESOLVED,
    CareerEvent,
    to_json,
)
from graphia.stats_store import (
    CareerStats,
    _career_from_json,
    _career_to_json,
)


# --------------------------------------------------------------------------
# Module-level setup: alias the Lambda's top-level imports to the in-repo
# ``graphia.*`` source modules, then import the handler. Done at import time
# so the test module itself only has to monkeypatch the cached AWS clients.
# --------------------------------------------------------------------------

# The Lambda does ``from career_events import ...`` and ``from stats_store
# import ...``. Map those names to the source modules so the import succeeds
# without vendoring. (The Lambda deploy package does its own vendoring.)
import graphia.career_events as _career_events_src  # noqa: E402
import graphia.stats_store as _stats_store_src  # noqa: E402

sys.modules.setdefault("career_events", _career_events_src)
sys.modules.setdefault("stats_store", _stats_store_src)

# Adding the lambda dir to sys.path lets `importlib.import_module` find it.
_LAMBDA_DIR = (
    Path(__file__).resolve().parent.parent
    / "infra"
    / "lambda"
    / "career_consumer"
)
if str(_LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(_LAMBDA_DIR))

lambda_function = importlib.import_module("lambda_function")


# --------------------------------------------------------------------------
# Test-local autouse boto3 leak guard
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _boto3_leak_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block any real ``boto3.client('bedrock-agentcore'|'s3')`` call.

    Tests in this file pre-inject the cached module-level clients
    (``_s3_client``, ``_agentcore_client``) so production ``_s3()`` /
    ``_agentcore()`` never reach ``boto3.client``. This guard is the
    seatbelt: if any test forgets to inject, ``boto3.client`` raises with
    a clear message rather than entering boto3's retry loop against
    dummy credentials and stalling pytest teardown.
    """
    import boto3

    real_client = boto3.client

    def _guarded_client(service_name: str, *args: Any, **kwargs: Any) -> Any:
        if service_name in {"bedrock-agentcore", "s3"}:
            raise RuntimeError(
                f"Unstubbed boto3.client({service_name!r}) call from a Lambda "
                "test. Pre-inject lambda_function._s3_client / "
                "lambda_function._agentcore_client before invoking the handler."
            )
        return real_client(service_name, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", _guarded_client)


@pytest.fixture(autouse=True)
def _lambda_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env vars the Lambda's ``_career_memory_id`` etc. require."""
    monkeypatch.setenv("CAREER_MEMORY_ID", "mem-career-test")
    monkeypatch.setenv("STATS_NAMESPACE", "/career/human-career/")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def _reset_cached_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the Lambda's cached module-level boto3 client slots per test."""
    monkeypatch.setattr(lambda_function, "_s3_client", None)
    monkeypatch.setattr(lambda_function, "_agentcore_client", None)


# --------------------------------------------------------------------------
# Fake boto3 clients
# --------------------------------------------------------------------------


class _FakeS3Client:
    """Minimal S3 client: hands out the pre-set envelope on ``get_object``."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"Body": io.BytesIO(json.dumps(self._body).encode("utf-8"))}


class _FakeAgentCoreClient:
    """AgentCore data-plane fake with per-operation scripted responses.

    Records every call as ``(op, kwargs)`` on :attr:`calls` so tests can
    assert which writes fired (or didn't). ``list_events`` and
    ``list_memory_records`` return the pre-set responses; the two
    ``batch_*_memory_records`` operations record their input and return
    an empty success dict.
    """

    def __init__(
        self,
        *,
        list_events_response: dict[str, Any] | None = None,
        list_memory_records_response: dict[str, Any] | None = None,
    ) -> None:
        self._list_events_response = list_events_response or {"events": []}
        self._list_memory_records_response = (
            list_memory_records_response or {"memoryRecordSummaries": []}
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_events", kwargs))
        return self._list_events_response

    def list_memory_records(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_memory_records", kwargs))
        return self._list_memory_records_response

    def batch_create_memory_records(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("batch_create_memory_records", kwargs))
        return {}

    def batch_update_memory_records(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("batch_update_memory_records", kwargs))
        return {}


# --------------------------------------------------------------------------
# Envelope / SNS shaping helpers
# --------------------------------------------------------------------------


def _event_text(event: CareerEvent) -> str:
    """Serialise a ``CareerEvent`` exactly as the emitter wrote it."""
    return json.dumps(to_json(event))


def _make_envelope(events: list[CareerEvent]) -> dict[str, Any]:
    """Build the S3 payload envelope the AgentCore strategy delivers."""
    return {
        "memoryId": "mem-career-test",
        "actorId": "human-career",
        "sessionId": events[0].session_id if events else "sess-x",
        "currentContext": [
            {
                "role": "ASSISTANT",
                "content": {"text": _event_text(e)},
            }
            for e in events
        ],
        "historicalContext": [],
    }


def _make_sns_event(s3_uri: str) -> dict[str, Any]:
    """Wrap an ``s3PayloadLocation`` in the SNS Records envelope."""
    return {
        "Records": [
            {
                "Sns": {
                    "Message": json.dumps(
                        {
                            "jobId": "job-1",
                            "s3PayloadLocation": s3_uri,
                            "memoryId": "mem-career-test",
                            "strategyId": "strat-1",
                        }
                    )
                }
            }
        ]
    }


def _agentcore_event_payload(event: CareerEvent) -> dict[str, Any]:
    """One AgentCore short-term event payload entry."""
    return {
        "payload": [
            {
                "conversational": {
                    "content": {"text": _event_text(event)},
                    "role": "ASSISTANT",
                }
            }
        ]
    }


def _inject_clients(
    monkeypatch: pytest.MonkeyPatch,
    s3: _FakeS3Client,
    agentcore: _FakeAgentCoreClient,
) -> None:
    """Pre-fill the Lambda's cached client slots so ``_s3`` / ``_agentcore`` return them."""
    monkeypatch.setattr(lambda_function, "_s3_client", s3)
    monkeypatch.setattr(lambda_function, "_agentcore_client", agentcore)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_first_delivery_game_ended_writes_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end fold: finalizer payload → one ``batch_create_memory_records``.

    The session has a ``night_resolved`` and the ``game_ended`` finalizer
    in short-term Memory; the long-term record is empty (first delivery).
    The handler must:

    * list the session events (to rebuild the full summary),
    * load the long-term record (sees none),
    * fold the summary on top of zeroed stats,
    * write exactly one new record via ``batch_create_memory_records`` —
      and zero ``batch_update_memory_records`` calls — carrying the
      folded CareerStats JSON with the session id in ``games_folded``.
    """
    session_id = "sess-A"
    night_event = CareerEvent(
        kind=KIND_NIGHT_RESOLVED,
        session_id=session_id,
        victim_died=True,
        human_was_mafia_picker=True,
        human_picked_victim=True,
    )
    end_event = CareerEvent(
        kind=KIND_GAME_ENDED,
        session_id=session_id,
        outcome="mafia_win",
        human_role="mafia",
        rounds=3,
    )

    envelope = _make_envelope([night_event, end_event])
    s3 = _FakeS3Client(body=envelope)
    agentcore = _FakeAgentCoreClient(
        list_events_response={
            "events": [
                _agentcore_event_payload(night_event),
                _agentcore_event_payload(end_event),
            ]
        },
        list_memory_records_response={"memoryRecordSummaries": []},
    )
    _inject_clients(monkeypatch, s3, agentcore)

    result = lambda_function.lambda_handler(
        _make_sns_event("s3://b/payload.json"), context=None
    )

    assert result == {"ok": True}
    # Exactly one batch_create and zero batch_update.
    ops = [op for op, _ in agentcore.calls]
    assert ops.count("batch_create_memory_records") == 1
    assert ops.count("batch_update_memory_records") == 0

    # Verify the persisted payload: folded CareerStats with session id in
    # games_folded.
    create_kwargs = next(
        kwargs for op, kwargs in agentcore.calls if op == "batch_create_memory_records"
    )
    records = create_kwargs["records"]
    assert len(records) == 1
    payload_text = records[0]["content"]["text"]
    persisted = _career_from_json(json.loads(payload_text))
    assert persisted.games_total == 1
    assert persisted.role_games("mafia") == 1
    assert persisted.role_wins("mafia") == 1
    assert session_id in persisted.games_folded


def test_replay_with_session_already_folded_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same session re-delivered → no ``batch_create`` / ``batch_update``.

    The long-term record already carries the session id in
    ``games_folded``, so the handler short-circuits the write and the
    re-fold never happens. This is the spec 006 §2.5 / ADR 008 idempotency
    contract: each session id is folded exactly once.
    """
    session_id = "sess-A"
    end_event = CareerEvent(
        kind=KIND_GAME_ENDED,
        session_id=session_id,
        outcome="mafia_win",
        human_role="mafia",
        rounds=3,
    )

    envelope = _make_envelope([end_event])

    # Pre-existing long-term record already lists this session.
    already_folded = CareerStats(
        games_total=1,
        games_by_role={"mafia": 1},
        wins_by_role={"mafia": 1},
        outcome_split={"mafia_win": 1},
        completed_games=1,
        sum_rounds_completed=3,
        games_folded=[session_id],
    )

    s3 = _FakeS3Client(body=envelope)
    agentcore = _FakeAgentCoreClient(
        list_events_response={
            "events": [_agentcore_event_payload(end_event)]
        },
        list_memory_records_response={
            "memoryRecordSummaries": [
                {
                    "memoryRecordId": "rec-1",
                    "content": {"text": json.dumps(_career_to_json(already_folded))},
                }
            ]
        },
    )
    _inject_clients(monkeypatch, s3, agentcore)

    result = lambda_function.lambda_handler(
        _make_sns_event("s3://b/payload.json"), context=None
    )

    assert result == {"ok": True}
    ops = [op for op, _ in agentcore.calls]
    assert "batch_create_memory_records" not in ops
    assert "batch_update_memory_records" not in ops


def test_non_finalizer_payload_skips_all_listing_and_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-finalizer (e.g. ``vote_initiated``) payload is dropped early.

    No ``list_events`` (session walk), no ``list_memory_records`` (long-term
    load), and no ``batch_*`` calls — the handler returns after recognising
    that no finalizer is present. Verifies the ADR 008 invariant that only
    ``game_ended`` / ``game_abandoned`` cause consolidation.
    """
    session_id = "sess-A"
    non_final = CareerEvent(
        kind=KIND_VOTE_INITIATED,
        session_id=session_id,
        initiator_is_human=True,
    )
    envelope = _make_envelope([non_final])

    s3 = _FakeS3Client(body=envelope)
    agentcore = _FakeAgentCoreClient()
    _inject_clients(monkeypatch, s3, agentcore)

    result = lambda_function.lambda_handler(
        _make_sns_event("s3://b/payload.json"), context=None
    )

    assert result == {"ok": True}
    # The S3 download is the only AWS call expected.
    assert agentcore.calls == [], (
        f"unexpected AgentCore calls on non-finalizer payload: {agentcore.calls!r}"
    )


def test_finalizer_with_no_session_events_skips_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finalizer with an empty ``list_events`` session → no fold, no write.

    Defensive: protects against a transient AgentCore read returning an
    empty session list right after a finalizer was written. The handler
    logs and skips rather than persisting an empty-summary fold.
    """
    session_id = "sess-A"
    end_event = CareerEvent(
        kind=KIND_GAME_ENDED,
        session_id=session_id,
        outcome="mafia_win",
        human_role="mafia",
        rounds=3,
    )
    envelope = _make_envelope([end_event])

    s3 = _FakeS3Client(body=envelope)
    agentcore = _FakeAgentCoreClient(
        list_events_response={"events": []},
        list_memory_records_response={"memoryRecordSummaries": []},
    )
    _inject_clients(monkeypatch, s3, agentcore)

    result = lambda_function.lambda_handler(
        _make_sns_event("s3://b/payload.json"), context=None
    )

    assert result == {"ok": True}
    ops = [op for op, _ in agentcore.calls]
    # list_events fires once; nothing else.
    assert ops == ["list_events"], f"unexpected ops: {ops!r}"


def test_list_events_uses_include_payloads_plural(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the boto3 ``list_events`` kwarg is ``includePayloads``
    (plural), not ``includePayload`` (singular).

    A live-production crash traced to this exact typo: the Lambda was
    invoked, ``ParamValidationError: Unknown parameter in input:
    'includePayload', must be one of: ..., includePayloads, ...`` aborted
    the handler before any ``batch_create_memory_records`` write, and the
    long-term career record stayed empty across games. Earlier tests stubbed
    ``list_events(**kwargs)`` without inspecting kwargs, so the typo passed
    unit tests and only surfaced against real boto3.
    """
    session_id = "sess-validate"
    end_event = CareerEvent(
        kind=KIND_GAME_ENDED,
        session_id=session_id,
        outcome="mafia_win",
        human_role="mafia",
        rounds=3,
    )
    envelope = _make_envelope([end_event])

    s3 = _FakeS3Client(body=envelope)
    agentcore = _FakeAgentCoreClient(
        list_events_response={"events": [_agentcore_event_payload(end_event)]},
        list_memory_records_response={"memoryRecordSummaries": []},
    )
    _inject_clients(monkeypatch, s3, agentcore)

    lambda_function.lambda_handler(
        _make_sns_event("s3://b/payload.json"), context=None
    )

    list_events_calls = [
        kwargs for op, kwargs in agentcore.calls if op == "list_events"
    ]
    assert list_events_calls, "expected the handler to call list_events"
    first = list_events_calls[0]
    assert first.get("includePayloads") is True, (
        f"list_events kwargs must carry includePayloads=True (plural); got {first!r}"
    )
    assert "includePayload" not in first, (
        f"list_events kwargs must not carry the singular includePayload typo; got {first!r}"
    )
