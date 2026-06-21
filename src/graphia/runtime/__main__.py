"""AgentCore Runtime entry-point for the Graphia game engine.

The container image baked from this repo runs ``python -m graphia.runtime``,
which:

1. Stands up a :class:`bedrock_agentcore.BedrockAgentCoreApp` on
   ``0.0.0.0:8080`` (explicit host — the SDK's ``/.dockerenv`` autodetect
   doesn't fire under Podman; see spec-002 §2.5).
2. Exposes a single ``@app.entrypoint`` async-generator handler that
   compiles the Graphia ``StateGraph`` lazily per ``thread_id`` and
   streams ``graph.stream(..., stream_mode="updates")`` chunks back to
   the caller as SSE.

The Runtime is HTTP-only (per ADR 005): the diary tool surface lives in
Lambda functions behind the Gateway, not in this container. The agent's
diary writes route through ``make_diary_store(load_config())`` — when
``GRAPHIA_GATEWAY_ID`` is set (deployed Runtime), that resolves to a
``GatewayMCPDiaryStore`` invoking the Gateway-fronted Lambda tools.

Payload contract (the wire-level agent invocation API for this Runtime):

    Start a new game
    ----------------
        {
          "action": "start",
          "thread_id": "<caller-supplied session id, used by SqliteSaver>",
          "initial_state": {...}        # GameState seed, may be empty
        }

    Resume a paused (interrupted) game
    ----------------------------------
        {
          "action": "resume",
          "thread_id": "<same id used at start>",
          "resume_value": <whatever the human typed>
        }

The handler streams one JSON object per graph super-step:

    {"node": "<node_name>", "update": {...}}

When the graph pauses on ``interrupt()``, a trailing event surfaces the
interrupt payload so the client can prompt the human:

    {"event": "interrupt", "value": {...}, "thread_id": "..."}

When the graph reaches END, a trailing event signals clean completion:

    {"event": "done", "thread_id": "..."}

On a malformed payload the handler yields a single error event and
returns:

    {"event": "error", "error": "<message>"}

The Runtime's IAM execution role supplies AWS credentials via the
standard boto3 chain; no auth wiring is needed here. LLM-call failures
inside the graph surface as a ``producer_error`` event.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

from bedrock_agentcore import BedrockAgentCoreApp
from langchain_core.load import dumpd
from langgraph.types import Command

from graphia.career_events import make_career_emitter
from graphia.config import load_config
from graphia.diary_store import make_diary_store
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.runtime.observability import (
    bind_thread_id,
    configure_runtime_observability,
    runtime_invocation_span,
    stamp_trace_thread_id,
)

# Install JSON-structured, thread-id-stamped logging before anything else
# logs. After this, every ``logging`` call in the process — this module,
# the LangGraph nodes, third-party libs — lands on stdout as a one-line
# JSON object; AgentCore Runtime ships that stdout to CloudWatch as
# ``APPLICATION_LOGS``. Local mode never imports this module, so its
# JSONL-to-``GRAPHIA_LOG_FILE`` behaviour is untouched.
configure_runtime_observability()

_config = load_config()
_diary_store = make_diary_store(_config)
# ADR 008: per-action career events go directly to the dedicated career
# AgentCore Memory (no Gateway hop). NoOp until GRAPHIA_CAREER_MEMORY_ID is
# set in the Runtime container's env (which Terraform does in remote mode).
_career_emitter = make_career_emitter(_config)

# Runtime sessions are ephemeral (up to 8h microVMs per spec-002 §2.5).
# tmpfs is the right home for per-session SQLite checkpoints — they
# vanish with the session, which is what we want.
_CHECKPOINT_DIR = Path(
    os.environ.get("GRAPHIA_RUNTIME_CHECKPOINT_DIR", "/tmp/graphia/checkpoints")
)
_CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("graphia.runtime")

app = BedrockAgentCoreApp()


def _make_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _validate(payload: Any) -> tuple[str, str, Any] | dict:
    """Return ``(action, thread_id, body)`` or an error event dict."""
    if not isinstance(payload, dict):
        return {"event": "error", "error": "payload must be a JSON object"}
    action = payload.get("action")
    thread_id = payload.get("thread_id")
    if action not in ("start", "resume"):
        return {
            "event": "error",
            "error": f"action must be 'start' or 'resume', got {action!r}",
        }
    if not isinstance(thread_id, str) or not thread_id:
        return {
            "event": "error",
            "error": "thread_id is required and must be a non-empty string",
        }
    if action == "start":
        body = payload.get("initial_state", {})
        if not isinstance(body, dict):
            return {"event": "error", "error": "initial_state must be an object"}
    else:  # resume
        if "resume_value" not in payload:
            return {
                "event": "error",
                "error": "resume action requires a resume_value field",
            }
        body = payload["resume_value"]
    return action, thread_id, body


def _serialise_chunk(chunk: dict) -> list[dict]:
    """Flatten LangGraph's ``{node: update}`` super-step into stream events.

    ``graph.stream(stream_mode='updates')`` emits one dict per super-step
    that maps node name → that node's state delta. We yield one event
    per (node, update) pair so the client doesn't need to know the
    super-step shape.

    The update is serialised via ``langchain_core.load.dumpd`` so
    ``BaseMessage`` instances (and any other LangChain ``Serializable``)
    land on the wire as tagged ``{lc, type: constructor, id, kwargs}``
    dicts that the client reconstructs back into real ``BaseMessage``
    objects. Plain dataclasses / Pydantic models (e.g. ``PlayerState``)
    are emitted as ``not_implemented`` markers carrying their ``repr``;
    those are not consumed by the UI and need no reconstruction.

    The bare ``__interrupt__`` super-step marker that LangGraph emits when
    a node calls ``interrupt()`` is dropped here — the trailing
    ``{"event": "interrupt", "value": V, ...}`` envelope below is the
    canonical interrupt signal.
    """
    events: list[dict] = []
    for node_name, update in chunk.items():
        if node_name == "__interrupt__":
            continue
        try:
            safe_update = dumpd(update)
        except Exception as exc:  # noqa: BLE001
            safe_update = {"_serialisation_error": repr(exc)}
        events.append({"node": node_name, "update": safe_update})
    return events


async def _run_invocation(
    action: str, thread_id: str, body: Any
) -> AsyncIterator[dict]:
    """Drive one validated invocation, yielding stream events.

    Split out of :func:`handler` so the whole graph run — compilation,
    ``graph.stream``, and the post-stream interrupt/done snapshot — sits
    inside a single ``with runtime_invocation_span(...)`` block in the
    handler. Keeping that block thin (it just iterates this generator)
    avoids re-indenting the streaming logic while still guaranteeing the
    invocation root span is the active OTEL context for every super-step:
    the LangChain instrumentor only nests child spans under the active
    span, so the graph-node and model-call spans land in one tree (CR 003).
    """
    try:
        graph = build_runtime_graph(
            thread_id,
            _CHECKPOINT_DIR,
            _diary_store,
            career_emitter=_career_emitter,
            day_round_recap_enabled=_config.day_round_recap_enabled,
            recap_aware_reasoning_enabled=_config.recap_aware_reasoning_enabled,
            role_guidance_enabled=_config.role_guidance_enabled,
            max_days=_config.max_days,
            context_window=_config.context_window,
            context_token_budget=_config.context_token_budget,
            private_thoughts_enabled=_config.private_thoughts_enabled,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "graph compilation failed",
            extra={"event": "graph_compile_error"},
        )
        yield {"event": "error", "error": f"graph compile failed: {exc!r}"}
        return

    run_config = _make_config(thread_id)
    graph_payload: Any = body if action == "start" else Command(resume=body)

    try:
        # ``graph.stream`` is synchronous; the SDK's worker-loop wrapper
        # iterates this async generator in a dedicated event loop, so a
        # blocking ``for`` here will not stall ``/ping``. (See
        # ``BedrockAgentCoreApp._async_gen_to_sync_gen``.)
        for chunk in graph.stream(
            graph_payload, run_config, stream_mode="updates"
        ):
            for event in _serialise_chunk(chunk):
                # One structured trace record per graph super-step. The
                # ThreadIdLogFilter stamps thread_id, so these lines are
                # the per-node ("Runtime invocation ... model-invocation
                # roundtrips ... win-condition outcome", spec-002 §2.5)
                # events a CloudWatch thread_id filter picks up.
                logger.info(
                    "graph super-step",
                    extra={"event": "graph_step", "node": event["node"]},
                )
                yield event
    except Exception as exc:  # noqa: BLE001
        # A crashed remote game must leave a full traceback in CloudWatch,
        # still carrying thread_id so the failure modal's filter selects it.
        logger.exception(
            "graph.stream failed", extra={"event": "graph_stream_error"}
        )
        yield {"event": "error", "error": repr(exc)}
        return

    # Post-stream snapshot mirrors the local driver's pattern in
    # ``graphia.driver.drive_graph`` — surface interrupts so the client
    # can prompt the human and re-invoke with ``action=resume``.
    snapshot = graph.get_state(run_config)
    interrupts = [i for t in snapshot.tasks for i in (t.interrupts or ())]
    if interrupts:
        # Round-trip through ``json.dumps(default=str)`` to keep complex
        # payload types (TypedDicts, dataclasses) on the wire.
        for interrupt in interrupts:
            try:
                safe_value = json.loads(json.dumps(interrupt.value, default=str))
            except Exception as exc:  # noqa: BLE001
                safe_value = {"_serialisation_error": repr(exc)}
            yield {
                "event": "interrupt",
                "value": safe_value,
                "thread_id": thread_id,
            }
        logger.info(
            "runtime invocation paused on interrupt",
            extra={
                "event": "invocation_interrupt",
                "interrupt_count": len(interrupts),
            },
        )
    else:
        logger.info(
            "runtime invocation done",
            extra={"event": "invocation_done"},
        )
        yield {"event": "done", "thread_id": thread_id}


@app.entrypoint
async def handler(payload: dict) -> AsyncIterator[dict]:
    """Async-generator entry-point that streams graph super-steps.

    The SDK detects async generators and wraps their output in a
    ``StreamingResponse`` (``text/event-stream``). Each ``yield`` lands
    on the wire as one SSE ``data:`` frame.
    """
    parsed = _validate(payload)
    if isinstance(parsed, dict):
        # Validation failed — emit the error event and stop.
        yield parsed
        return

    action, thread_id, body = parsed

    # Establish the LangGraph thread_id as this invocation's correlation
    # id *before* any further work logs. ``bind_thread_id`` sets a
    # ContextVar that ``ThreadIdLogFilter`` reads on every subsequent log
    # record, so every JSON line emitted for the rest of this invocation
    # — including from inside the graph's nodes — carries
    # ``"thread_id": "<thread>"``. A CloudWatch ``{ $.thread_id = "..." }``
    # filter then selects exactly this game's events.
    bind_thread_id(thread_id)

    # Open the per-invocation OTEL **root span** (CR 003). Keeping the whole
    # graph run inside this ``with`` block means the root span is the active
    # OTEL context while ``graph.stream`` executes, so every graph-node and
    # ``ChatBedrockConverse`` model-call span the LangChain instrumentor
    # emits nests under it — producing one navigable per-session trace tree
    # instead of a flat list. ``stamp_trace_thread_id`` then mirrors the game
    # id into OTEL baggage (``session.id``) and onto the root span so the
    # GenAI Observability session view can select the tree by game. Both are
    # transparent no-ops when no OTEL SDK is present (local mode).
    with runtime_invocation_span(thread_id):
        stamp_trace_thread_id(thread_id)
        logger.info(
            "runtime invocation start",
            extra={"event": "invocation_start", "action": action},
        )
        async for event in _run_invocation(action, thread_id, body):
            yield event


if __name__ == "__main__":
    # Explicit host — the SDK's `/.dockerenv` heuristic does not fire
    # under Podman (per Slice 3 finding). Binding 0.0.0.0 works in any
    # container runtime.
    app.run(host="0.0.0.0")
