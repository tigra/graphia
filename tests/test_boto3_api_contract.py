"""boto3 service-model contract tests for the bedrock-agentcore APIs we call.

Catches the class of bug where our source-code constants — operation names,
input parameter names — drift from the real boto3 service model. The local
mocks stub ``client.list_events(**kwargs)`` and never inspect kwargs, so a
parameter typo passes unit tests and only surfaces when real boto3 rejects
the call. Two live-production traces showed this:

* ``includePayload`` (singular) — see commit f3c59a0. CloudWatch:
  ``ParamValidationError: Unknown parameter in input: 'includePayload',
  must be one of: ..., includePayloads, ...``.
* ``batch_create_memory_records`` missing on the Lambda runtime's bundled
  boto3 — see commit a7a5d38. The contract test below catches it against
  the LOCAL boto3; the Lambda-zip contract test
  (:mod:`tests.test_lambda_zip_contents`) catches it against the vendored
  copy that the Lambda actually runs with.

These tests require no AWS credentials and no network — they only walk
``boto3.client(...).meta.service_model``.
"""

from __future__ import annotations

import boto3
import pytest


@pytest.fixture(scope="module")
def service_model():
    """The ``bedrock-agentcore`` data-plane service model.

    Built from the boto3 install present in the test runner. The Lambda
    runtime ships its own boto3 snapshot — :mod:`tests.test_lambda_zip_contents`
    asserts the vendored one in ``career_consumer.zip`` separately.
    """
    return boto3.client(
        "bedrock-agentcore", region_name="us-east-1"
    ).meta.service_model


def _operation_inputs(service_model, op_name: str) -> set[str]:
    return set(service_model.operation_model(op_name).input_shape.members.keys())


def test_list_events_kwarg_is_includepayloads_plural(service_model):
    """The kwarg is ``includePayloads``, NOT the singular ``includePayload``.

    A live Lambda invocation crashed with ``ParamValidationError`` on the
    singular form (commit f3c59a0). Locking the plural in keeps a future
    typo regression out of the deployed pipeline.
    """
    members = _operation_inputs(service_model, "ListEvents")
    assert "includePayloads" in members, (
        f"ListEvents.includePayloads missing; got members {sorted(members)!r}"
    )
    assert "includePayload" not in members, (
        f"ListEvents must NOT accept the singular includePayload; "
        f"got members {sorted(members)!r}"
    )


def test_list_events_required_inputs_are_what_the_lambda_passes(service_model):
    """The Lambda's ``_list_session_events`` passes memoryId, actorId,
    sessionId, includePayloads. All four must be in the input shape."""
    members = _operation_inputs(service_model, "ListEvents")
    for name in ("memoryId", "actorId", "sessionId", "includePayloads"):
        assert name in members, f"ListEvents.{name} missing in {sorted(members)!r}"


def test_list_memory_records_supports_namespace_query(service_model):
    """``AgentCoreCareerEventStore.load`` lists records by ``memoryId`` +
    ``namespace`` — both must be in the operation's input shape."""
    members = _operation_inputs(service_model, "ListMemoryRecords")
    for name in ("memoryId", "namespace"):
        assert name in members, (
            f"ListMemoryRecords.{name} missing in {sorted(members)!r}"
        )


def test_batch_create_memory_records_operation_exists(service_model):
    """Catches commit a7a5d38's class of bug against the LOCAL boto3:
    a stale wheel that no longer exposes ``BatchCreateMemoryRecords``
    fails this test loudly instead of letting unit tests pass and the
    deployed Lambda crash."""
    op = service_model.operation_model("BatchCreateMemoryRecords")
    members = set(op.input_shape.members.keys())
    assert "memoryId" in members
    assert "records" in members


def test_batch_update_memory_records_operation_exists(service_model):
    """Same shape as Create — the Lambda picks one or the other based on
    whether a record already exists in the namespace."""
    op = service_model.operation_model("BatchUpdateMemoryRecords")
    members = set(op.input_shape.members.keys())
    assert "memoryId" in members
    assert "records" in members


def test_create_event_required_inputs_are_what_the_emitter_passes(service_model):
    """``AgentCoreCareerEventEmitter.emit`` passes memoryId, actorId,
    sessionId, payload, eventTimestamp. Locking the names so a renaming
    in a future boto3 release fails this test at update time."""
    members = _operation_inputs(service_model, "CreateEvent")
    for name in ("memoryId", "actorId", "sessionId", "payload", "eventTimestamp"):
        assert name in members, f"CreateEvent.{name} missing in {sorted(members)!r}"
