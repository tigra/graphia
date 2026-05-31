"""SNS-triggered Lambda that folds career-stats events into the long-term record.

Spec 006 / ADR 008 / Slice 8.7. The remote-mode career-stats pipeline is:

    Runtime nodes ──▶ CreateEvent (career Memory, short-term)
                       │
                       ▼ self-managed strategy (trigger fires per event)
                  SNS topic ──▶ this Lambda
                       │
                       ▼ on finalizer kinds only
                  list_events(session) + fold ──▶ batch_{create,update}_memory_records

Non-finalizer events sit durably in short-term Memory as the session log; only
``game_ended`` / ``game_abandoned`` cause consolidation, so a single game is
folded exactly once into the long-term :class:`CareerStats` record.

Envelope shape (AWS docs — Bedrock AgentCore Memory self-managed strategies,
"Understanding payload delivery"):
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-self-managed-strategies.html

    SNS message body (JSON string in `Records[*].Sns.Message`):
        {"jobId": "...", "s3PayloadLocation": "s3://bucket/key",
         "memoryId": "...", "strategyId": "..."}

    S3 object (the payload referenced by `s3PayloadLocation`):
        {"requestId": "...", "accountId": "...", "memoryId": "...",
         "actorId": "...", "sessionId": "...", "strategyId": "...",
         "startingTimestamp": ..., "endingTimestamp": ...,
         "currentContext": [
             {"role": "ASSISTANT",
              "content": {"text": "<JSON we passed to create_event>"}}
         ],
         "historicalContext": [...]}

The original ``CareerEvent`` JSON our Runtime emitted via ``create_event`` rides
in ``currentContext[*].content.text``. We parse defensively: any item without
that path is skipped, so an envelope shape drift surfaces as a logged warning
rather than a Lambda failure.

Verify-at-deploy caveat: the documented schema is treated as authoritative, but
the first real delivery from the deployed strategy is the canonical confirmation
— if the actual envelope differs (e.g. nests the event payload under a different
key), update :func:`_extract_event_texts` accordingly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3

from career_events import build_summary, from_json as event_from_json
from stats_store import (
    CareerStats,
    GameSummary,
    _career_from_json,
    _career_to_json,
    fold,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ACTOR_ID = "human-career"
_FINALIZER_KINDS = frozenset({"game_ended", "game_abandoned"})

_s3_client = None
_agentcore_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    return _s3_client


def _agentcore():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client(
            "bedrock-agentcore", region_name=os.environ.get("AWS_REGION")
        )
    return _agentcore_client


def _career_memory_id() -> str:
    value = os.environ.get("CAREER_MEMORY_ID")
    if not value:
        raise RuntimeError("CAREER_MEMORY_ID env var is not set")
    return value


def _stats_namespace() -> str:
    value = os.environ.get("STATS_NAMESPACE")
    if not value:
        raise RuntimeError("STATS_NAMESPACE env var is not set")
    return value


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/key/with/slashes`` → ``("bucket", "key/with/slashes")``."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"not an s3:// URI: {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def _extract_event_texts(envelope: dict) -> list[str]:
    """Pull the inner ``CareerEvent`` JSON strings out of one S3 payload.

    Walks ``currentContext`` then ``historicalContext``; each item with a
    ``content.text`` string contributes one candidate. Items missing that path
    are silently skipped — the caller decides what to do with non-decodable
    candidates.
    """
    texts: list[str] = []
    for section in ("currentContext", "historicalContext"):
        items = envelope.get(section) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                texts.append(text)
    return texts


def _decode_event(text: str):
    """Decode one ``CareerEvent`` from the inner JSON payload, or ``None``."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "kind" not in data or "session_id" not in data:
        return None
    try:
        return event_from_json(data)
    except (KeyError, TypeError):
        return None


def _download_envelope(s3_uri: str) -> dict:
    bucket, key = _parse_s3_uri(s3_uri)
    obj = _s3().get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    return json.loads(body)


def _list_session_events(session_id: str):
    """Page through every short-term event for one session, decoded.

    Walks ``list_events`` pagination by ``nextToken``; per item, pulls each
    ``payload[*].conversational.content.text`` and decodes it as a
    :class:`CareerEvent` (skipping anything that doesn't parse).
    """
    memory_id = _career_memory_id()
    client = _agentcore()
    events = []
    next_token: str | None = None
    while True:
        kwargs: dict = {
            "memoryId": memory_id,
            "actorId": _ACTOR_ID,
            "sessionId": session_id,
            "includePayload": True,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_events(**kwargs)
        for raw in response.get("events") or []:
            for item in raw.get("payload") or []:
                conv = item.get("conversational") if isinstance(item, dict) else None
                if not isinstance(conv, dict):
                    continue
                content = conv.get("content")
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if not isinstance(text, str):
                    continue
                decoded = _decode_event(text)
                if decoded is not None:
                    events.append(decoded)
        next_token = response.get("nextToken")
        if not next_token:
            break
    return events


def _read_career_stats() -> tuple[CareerStats, str | None]:
    """Load the single long-term career record + its id (None if absent).

    Mirrors ``AgentCoreCareerEventStore.load`` but also returns the
    ``memoryRecordId`` so the writer can pick between create vs update.
    """
    memory_id = _career_memory_id()
    namespace = _stats_namespace()
    try:
        response = _agentcore().list_memory_records(
            memoryId=memory_id, namespace=namespace
        )
    except Exception:
        logger.warning(
            "Could not list career memory records in %s; starting fresh.",
            namespace,
            exc_info=True,
        )
        return CareerStats(), None
    summaries = response.get("memoryRecordSummaries") or []
    if not summaries:
        return CareerStats(), None
    first = summaries[0]
    text = (first.get("content") or {}).get("text")
    record_id = first.get("memoryRecordId")
    if not isinstance(text, str):
        return CareerStats(), record_id
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("Career record in %s is unparseable; starting fresh.", namespace)
        return CareerStats(), record_id
    return _career_from_json(raw), record_id


def _write_career_stats(stats: CareerStats, existing_id: str | None) -> None:
    memory_id = _career_memory_id()
    namespace = _stats_namespace()
    text = json.dumps(_career_to_json(stats))
    now = datetime.now(timezone.utc)
    client = _agentcore()
    if existing_id is None:
        record_item: dict = {
            "requestIdentifier": _ACTOR_ID,
            "namespaces": [namespace],
            "content": {"text": text},
            "timestamp": now,
        }
        client.batch_create_memory_records(memoryId=memory_id, records=[record_item])
    else:
        record_item = {
            "memoryRecordId": existing_id,
            "timestamp": now,
            "content": {"text": text},
            "namespaces": [namespace],
        }
        client.batch_update_memory_records(memoryId=memory_id, records=[record_item])


def _handle_one_payload(envelope: dict) -> None:
    """Process a single S3-delivered AgentCore payload.

    Decodes inner events, returns early on non-finalizer payloads (the
    session log lives in short-term Memory; consolidation only happens at
    finalizers), and otherwise lists the full session, folds it, and
    persists — with session-id idempotency via ``games_folded``.
    """
    decoded = [
        event for text in _extract_event_texts(envelope)
        if (event := _decode_event(text)) is not None
    ]
    if not decoded:
        logger.info("Payload carried no decodable career events; nothing to do.")
        return

    finalizers = [e for e in decoded if e.kind in _FINALIZER_KINDS]
    if not finalizers:
        return

    finalizer = finalizers[-1]
    session_id = finalizer.session_id

    session_events = _list_session_events(session_id)
    if not session_events:
        logger.warning(
            "Finalizer %s seen but no session events listed for %s; skipping.",
            finalizer.kind,
            session_id,
        )
        return

    summary: GameSummary = build_summary(session_events)
    if not summary.outcome:
        logger.info(
            "Session %s has no closed outcome yet; skipping fold.", session_id
        )
        return

    current, existing_id = _read_career_stats()
    if session_id in current.games_folded:
        logger.info("Session %s already folded; skipping (idempotent).", session_id)
        return

    folded = fold(current, summary)
    new_stats = replace(
        folded, games_folded=[*current.games_folded, session_id]
    )
    _write_career_stats(new_stats, existing_id)
    logger.info(
        "Folded session %s into career: games_total=%d", session_id, new_stats.games_total
    )


def lambda_handler(event, context):
    """SNS-triggered entrypoint; one invocation may carry several records.

    Errors on individual records are logged and swallowed: SNS retry + DLQ
    handles real failures, and a single malformed envelope must not poison
    the rest of the batch.
    """
    records = (event or {}).get("Records") or []
    logger.info("career_consumer invoked with %d record(s)", len(records))
    for record in records:
        try:
            sns_message = ((record or {}).get("Sns") or {}).get("Message")
            if not isinstance(sns_message, str):
                logger.warning("Record missing Sns.Message string; skipping.")
                continue
            try:
                notification = json.loads(sns_message)
            except (TypeError, ValueError):
                logger.warning("SNS message is not JSON; skipping.")
                continue
            s3_uri = notification.get("s3PayloadLocation")
            if not isinstance(s3_uri, str):
                logger.warning("SNS message lacks s3PayloadLocation; skipping.")
                continue
            envelope = _download_envelope(s3_uri)
            _handle_one_payload(envelope)
        except Exception:
            logger.exception("Failed to process one SNS record; continuing.")
    return {"ok": True}
