"""Live observability verification — drives the *deployed* AgentCore Runtime
and inspects the *real* telemetry recorded in CloudWatch.

WHY THIS FILE EXISTS
--------------------

We are trying to get a nested OpenTelemetry **trace tree** to appear in
AgentCore GenAI Observability for a remote game. Three fixes have failed.
The blind spot: the in-process trace test (``tests/test_slice8_trace_tree.py``)
installs its *own* ``TracerProvider``, so it passes even when the deployed
Runtime records nothing. This test instead hits the **real deployed Runtime**
over the wire and reads back the **real recorded telemetry** from CloudWatch.

It is a debugging / iteration tool, NOT a fix. The trace-tree assertions at
the end are *expected to FAIL today* — the printed CloudWatch dump above
them is the payload that makes the test useful for iterating on the fix.

OPT-IN ONLY — NEVER RUNS IN THE NORMAL SUITE
--------------------------------------------

The whole module is skipped unless ``GRAPHIA_LIVE_OBSERVABILITY_TEST=1``.
``uv run pytest -q`` (97 passing, fully mocked/offline) is completely
unaffected: the ``pytest.skip(..., allow_module_level=True)`` below fires at
collection time before any AWS-touching code runs.

The autouse ``safe_*`` fixtures in ``conftest.py`` mock the LLM / Memory /
Gateway *import seams*. This test deliberately makes **real** calls, so it
does not route through any of those seams: it builds a real
:class:`graphia.agentcore_client.AgentCoreClient` and a real ``boto3``
``logs`` client directly. The autouse fixtures still run (they are autouse)
but they patch surfaces this module never touches, so they are inert here.

HOW TO RUN
----------

    make verify-observability

which expands to::

    GRAPHIA_LIVE_OBSERVABILITY_TEST=1 \
        uv run pytest tests/test_remote_observability_live.py -s -v

Requires: a deployed Runtime (``GRAPHIA_RUNTIME_URL`` in ``.env``), a live
AWS SSO session (``AWS_PROFILE`` set in the environment), and the AWS region
``us-east-1``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import pytest

# --------------------------------------------------------------------------
# Module-level opt-in guard. This MUST run before any AWS import / call so the
# default `uv run pytest -q` run never touches AWS and stays at 97 passing.
# --------------------------------------------------------------------------

if os.environ.get("GRAPHIA_LIVE_OBSERVABILITY_TEST") != "1":
    pytest.skip(
        "Live observability test is opt-in. Set GRAPHIA_LIVE_OBSERVABILITY_TEST=1 "
        "(or run `make verify-observability`) to drive the deployed Runtime and "
        "query CloudWatch. Skipped by default so the normal suite stays offline.",
        allow_module_level=True,
    )

# Imports below are deferred past the skip guard so a plain `pytest -q`
# collection never even imports boto3-touching code from this module.
import boto3  # noqa: E402

from graphia.agentcore_client import (  # noqa: E402
    AgentCoreClient,
    AgentCoreRuntimeError,
)
from graphia.config import load_config  # noqa: E402
from langgraph.types import Command  # noqa: E402

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

# How many Runtime invocations (start + resumes) to attempt. ~5-8 gets the
# game past name-collection / setup and into real gameplay so the telemetry
# includes AI-action slices, not just the name prompt.
MAX_INVOCATIONS = 8

# CloudWatch propagation: vended-log-delivery + Transaction Search indexing
# lag a few minutes. Poll up to ~4 minutes at ~25s intervals.
TELEMETRY_POLL_TIMEOUT_S = 240
TELEMETRY_POLL_INTERVAL_S = 25

# CloudWatch log groups carrying the deployed Runtime's telemetry (us-east-1).
SPANS_LOG_GROUP = "aws/spans"
RUNTIME_LOG_GROUP = "/aws/bedrock-agentcore/graphia-demo-runtime"

# The platform-only span name — these have `scope: null` / `parentSpanId: null`
# and are NOT what we want. In-app (instrumented) spans have a populated scope.
PLATFORM_SPAN_NAME = "AgentCore.Runtime.Invoke"


# --------------------------------------------------------------------------
# Scripted resume values, keyed by interrupt `kind`
# --------------------------------------------------------------------------


def _scripted_resume_value(interrupt_value: Any) -> Any:
    """Return a plausible valid resume value for one interrupt payload.

    The Graphia graph emits four interrupt kinds (see ``graphia.nodes``):

    * ``name``     — ``collect_name`` wants a name string.
    * ``day_turn`` — the human's Day turn; any plain string is a speech. We
      deliberately do NOT send ``/vote ...`` (that opens a vote sub-flow and
      a vote interrupt) — a plain ``"."`` keeps the partial game simple.
    * ``point``    — the human is Mafia picking a Night victim; the payload
      carries ``options: [{"id", "name"}, ...]`` and the resume must be one
      of those ids.
    * ``vote``     — a yes/no ballot; resume is ``"yes"`` or ``"no"``.

    Anything unrecognised falls back to an empty string, which every node
    tolerates (each interrupt handler defaults a non-string / empty value to
    a safe choice rather than crashing).
    """
    kind = None
    if isinstance(interrupt_value, dict):
        kind = interrupt_value.get("kind")

    if kind == "name":
        return "Observa"
    if kind == "day_turn":
        return "I have nothing to add right now."
    if kind == "point":
        options = []
        if isinstance(interrupt_value, dict):
            options = interrupt_value.get("options") or []
        if options and isinstance(options[0], dict) and options[0].get("id"):
            return options[0]["id"]
        return ""
    if kind == "vote":
        return "no"
    # Unknown interrupt kind — empty string is the universally-tolerated
    # fallback across every node's interrupt handler.
    return ""


# --------------------------------------------------------------------------
# Drive the deployed Runtime — headless, no Textual UI
# --------------------------------------------------------------------------


def _drive_partial_remote_game(client: AgentCoreClient, thread_id: str) -> int:
    """Drive a partial game against the deployed Runtime; return invocations done.

    Mirrors the start/resume loop in :func:`graphia.driver.drive_graph` but
    headless — no Textual UI, no local graph. We talk to ``AgentCoreClient``
    directly: send the ``start`` payload, consume streamed chunks until an
    ``{"__interrupt__": (...)}`` chunk, script a resume for that interrupt's
    ``kind``, repeat. The game is LLM-driven and non-deterministic, so we
    assert nothing about game state — we only count completed invocations and
    stop cleanly if the game ends, the script is rejected, or we hit the cap.
    """
    run_config = {"configurable": {"thread_id": thread_id}}
    payload: Any = {"messages": []}  # initial state for the `start` action
    invocations = 0

    for attempt in range(MAX_INVOCATIONS):
        pending_interrupt: Any | None = None
        chunk_count = 0
        try:
            for chunk in client.stream(payload, run_config, stream_mode="updates"):
                chunk_count += 1
                interrupt_tuple = chunk.get("__interrupt__")
                if isinstance(interrupt_tuple, tuple) and interrupt_tuple:
                    # AgentCoreClient synthesises this shape from the server's
                    # trailing `{"event": "interrupt", ...}` SSE envelope.
                    pending_interrupt = interrupt_tuple[0]
                    # The interrupt chunk is the last meaningful one for this
                    # super-step; keep draining so the SSE body closes cleanly.
        except AgentCoreRuntimeError as exc:
            print(f"  [invocation {attempt + 1}] Runtime error — stopping: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 — debugging tool, report & stop
            print(
                f"  [invocation {attempt + 1}] unexpected error driving the "
                f"Runtime — stopping: {type(exc).__name__}: {exc}"
            )
            break

        invocations += 1
        print(
            f"  [invocation {attempt + 1}] {chunk_count} chunk(s); "
            f"{'interrupt' if pending_interrupt is not None else 'no interrupt'}"
        )

        if pending_interrupt is None:
            # No interrupt: graph either reached END or paused at a super-step
            # boundary. For a partial game we treat this as a clean stop.
            print("  Runtime stream ended without an interrupt — stopping.")
            break

        interrupt_value = getattr(pending_interrupt, "value", None)
        resume_value = _scripted_resume_value(interrupt_value)
        kind = (
            interrupt_value.get("kind")
            if isinstance(interrupt_value, dict)
            else "<unknown>"
        )
        print(f"  -> resuming interrupt kind={kind!r} with {resume_value!r}")
        payload = Command(resume=resume_value)

    return invocations


# --------------------------------------------------------------------------
# CloudWatch querying
# --------------------------------------------------------------------------


def _fetch_log_records(
    logs_client: Any, log_group: str, filter_term: str, start_ms: int
) -> list[dict]:
    """Fetch all log records in ``log_group`` matching ``filter_term``.

    Uses ``filter_log_events`` with a plain substring filter on the
    ``thread_id`` (the AgentCore session id embeds the thread id, so a
    substring match catches span records keyed by session id too). Each
    returned record's ``message`` is parsed as JSON when possible; the parsed
    object is attached as ``_parsed`` for the caller, falling back to the raw
    string when the line is not JSON.
    """
    records: list[dict] = []
    paginator = logs_client.get_paginator("filter_log_events")
    page_iter = paginator.paginate(
        logGroupName=log_group,
        startTime=start_ms,
        filterPattern=f'"{filter_term}"',
    )
    try:
        for page in page_iter:
            for event in page.get("events", []):
                raw = event.get("message", "")
                try:
                    event["_parsed"] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    event["_parsed"] = raw
                records.append(event)
    except logs_client.exceptions.ResourceNotFoundException:
        print(
            f"  WARNING: log group {log_group!r} does not exist in this region. "
            "Telemetry may not be configured / delivered yet."
        )
    return records


def _poll_for_telemetry(
    logs_client: Any, thread_id: str, start_ms: int
) -> tuple[list[dict], list[dict]]:
    """Poll both CloudWatch log groups until span data appears (or timeout).

    Returns ``(span_records, runtime_records)``. Polls up to
    ``TELEMETRY_POLL_TIMEOUT_S`` because vended-log delivery and Transaction
    Search indexing lag a few minutes; stops early as soon as the ``aws/spans``
    group yields anything for this thread.
    """
    deadline = time.monotonic() + TELEMETRY_POLL_TIMEOUT_S
    span_records: list[dict] = []
    runtime_records: list[dict] = []
    attempt = 0

    while True:
        attempt += 1
        span_records = _fetch_log_records(
            logs_client, SPANS_LOG_GROUP, thread_id, start_ms
        )
        runtime_records = _fetch_log_records(
            logs_client, RUNTIME_LOG_GROUP, thread_id, start_ms
        )
        print(
            f"  poll #{attempt}: aws/spans={len(span_records)} record(s), "
            f"runtime log group={len(runtime_records)} record(s)"
        )
        if span_records:
            break
        if time.monotonic() >= deadline:
            print("  telemetry poll timed out — proceeding with what we have.")
            break
        time.sleep(TELEMETRY_POLL_INTERVAL_S)

    return span_records, runtime_records


# --------------------------------------------------------------------------
# Reporting — print everything found, always, pass or fail
# --------------------------------------------------------------------------


def _span_field(parsed: Any, *names: str) -> Any:
    """Pull the first present field from a parsed span record (tolerant of
    casing / nesting variants emitted by the OTel CloudWatch exporter)."""
    if not isinstance(parsed, dict):
        return None
    for name in names:
        if name in parsed:
            return parsed[name]
    return None


def _print_span_report(span_records: list[dict]) -> dict[str, Any]:
    """Print every span and a summary; return the parsed summary dict."""
    print("\n--- aws/spans (OpenTelemetry spans) ---")
    print(f"total span records: {len(span_records)}")

    names: set[str] = set()
    scopes: set[str] = set()
    with_parent = 0
    in_app_spans: list[dict] = []

    for i, rec in enumerate(span_records):
        parsed = rec.get("_parsed")
        name = _span_field(parsed, "name")
        kind = _span_field(parsed, "kind")
        scope = _span_field(parsed, "scope")
        parent = _span_field(parsed, "parentSpanId", "parent_span_id")
        trace_id = _span_field(parsed, "traceId", "trace_id")
        attributes = _span_field(parsed, "attributes")

        # `scope` may itself be a dict ({"name": "...", "version": "..."}).
        scope_name = scope.get("name") if isinstance(scope, dict) else scope

        if name is not None:
            names.add(str(name))
        if scope_name is not None:
            scopes.add(str(scope_name))
        if parent:
            with_parent += 1
        is_in_app = bool(scope_name) and name != PLATFORM_SPAN_NAME
        if is_in_app:
            in_app_spans.append(rec)

        print(
            f"  span[{i}] name={name!r} kind={kind!r} scope={scope_name!r} "
            f"parentSpanId={parent!r} traceId={trace_id!r}"
        )
        if isinstance(attributes, dict) and attributes:
            keys = sorted(attributes)[:8]
            print(f"            attributes keys: {keys}")

    print("\n  span summary:")
    print(f"    total spans          : {len(span_records)}")
    print(f"    distinct names       : {sorted(names)}")
    print(f"    distinct scopes      : {sorted(scopes)}")
    print(f"    spans with a parent  : {with_parent}")
    print(f"    in-app (scoped) spans: {len(in_app_spans)}")

    return {
        "total": len(span_records),
        "names": names,
        "scopes": scopes,
        "with_parent": with_parent,
        "in_app_spans": in_app_spans,
    }


def _print_runtime_log_report(runtime_records: list[dict]) -> dict[str, Any]:
    """Print runtime-log-group findings; return a summary dict.

    Distinguishes Graphia *app logs* (a JSON line with a top-level
    ``thread_id`` field — emitted by the container's structured logger) from
    platform ``InvokeAgentRuntime`` records.
    """
    print(f"\n--- {RUNTIME_LOG_GROUP} (Runtime log group) ---")
    print(f"total records: {len(runtime_records)}")

    app_logs: list[dict] = []
    platform_logs: list[dict] = []

    for rec in runtime_records:
        parsed = rec.get("_parsed")
        if isinstance(parsed, dict) and "thread_id" in parsed:
            app_logs.append(rec)
        else:
            platform_logs.append(rec)

    print(f"  Graphia app logs (top-level thread_id) : {len(app_logs)}")
    print(f"  other / platform records              : {len(platform_logs)}")

    for i, rec in enumerate(app_logs[:5]):
        parsed = rec["_parsed"]
        print(f"  app_log[{i}] keys={sorted(parsed)}")

    return {
        "total": len(runtime_records),
        "app_logs": app_logs,
        "platform_logs": platform_logs,
    }


# --------------------------------------------------------------------------
# The live test
# --------------------------------------------------------------------------


def test_remote_runtime_records_nested_trace_tree() -> None:
    """Drive the deployed Runtime, then assert the live CloudWatch telemetry
    shows a nested OpenTelemetry trace tree.

    The three assertions at the end ARE EXPECTED TO FAIL today (no in-app
    spans, no nesting, no app logs reaching the runtime log group). That is
    correct: the printed CloudWatch dump above the assertions is the debugging
    payload that drives the observability fix. Once the fix lands, this test
    flips to passing against the real deployed Runtime.
    """
    # --- Config: real .env, no mocks ---------------------------------
    config = load_config()
    if not config.runtime_invocation_url:
        pytest.fail(
            "GRAPHIA_RUNTIME_URL is not set. The live observability test needs "
            "a deployed Runtime — run `make wire-env` (or set GRAPHIA_RUNTIME_URL "
            "in .env) and retry."
        )
    region = config.aws_region  # us-east-1

    # Fresh, unique, timestamp-based thread id — same format the app uses
    # (graphia.graph build_graph: datetime.utcnow().strftime("%Y%m%dT%H%M%S")).
    thread_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # Window start for CloudWatch queries: a minute before we begin, in ms.
    start_ms = int((time.time() - 60) * 1000)

    print("\n" + "=" * 72)
    print("LIVE OBSERVABILITY VERIFICATION")
    print("=" * 72)
    print(f"region              : {region}")
    print(f"runtime ARN         : {config.runtime_invocation_url}")
    print(f"thread_id           : {thread_id}")
    print(f"aws profile (env)   : {os.environ.get('AWS_PROFILE', '<default chain>')}")

    # --- Drive a partial remote game ---------------------------------
    print("\n--- driving partial remote game ---")
    client = AgentCoreClient(
        runtime_arn=config.runtime_invocation_url, region=region
    )
    invocations = _drive_partial_remote_game(client, thread_id)
    print(f"\ncompleted {invocations} Runtime invocation(s) for thread {thread_id}")

    if invocations == 0:
        pytest.fail(
            f"No Runtime invocations completed for thread {thread_id} — the "
            "deployed Runtime could not be driven at all (check SSO session, "
            "GRAPHIA_RUNTIME_URL, and that the Runtime is deployed)."
        )

    # --- Wait for telemetry to propagate, then query CloudWatch ------
    print("\n--- polling CloudWatch for telemetry ---")
    logs_client = boto3.client("logs", region_name=region)
    span_records, runtime_records = _poll_for_telemetry(
        logs_client, thread_id, start_ms
    )

    # --- Print everything found — ALWAYS, pass or fail ---------------
    print("\n" + "=" * 72)
    print(f"TELEMETRY REPORT — thread_id={thread_id}, invocations={invocations}")
    print("=" * 72)
    span_summary = _print_span_report(span_records)
    runtime_summary = _print_runtime_log_report(runtime_records)
    print("=" * 72 + "\n")

    # --- Assert the trace-tree contract on the LIVE data -------------
    # These currently FAIL (expected). The dump above is the iteration payload.
    assert span_summary["total"] > 0, (
        f"No span records found in '{SPANS_LOG_GROUP}' for thread {thread_id}. "
        "The deployed Runtime emitted no OpenTelemetry spans at all — check "
        "that Transaction Search is enabled (`make enable-transaction-search`) "
        "and that the Runtime container exports spans."
    )
    assert span_summary["in_app_spans"], (
        f"Only platform spans (name='{PLATFORM_SPAN_NAME}', scope=null) were "
        f"recorded — no in-app instrumented spans. Expected spans with a "
        "populated 'scope' (e.g. 'openinference.instrumentation.langchain'). "
        "The Runtime is not exporting application-level spans."
    )
    assert span_summary["with_parent"] >= 1, (
        "No span has a non-null 'parentSpanId' — the recorded telemetry is a "
        "flat list, not a nested trace tree. A trace tree requires at least "
        "one child span pointing at a parent."
    )
    assert runtime_summary["app_logs"], (
        f"No Graphia application logs (JSON lines with a top-level 'thread_id') "
        f"reached '{RUNTIME_LOG_GROUP}' for thread {thread_id}. Only platform "
        "records were found — the container's structured app logs are not "
        "being delivered to the runtime log group."
    )
