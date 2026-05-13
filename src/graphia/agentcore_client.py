"""Client wrapper for invoking the deployed Graphia Runtime over the wire.

This module is the *remote-mode* mirror of the local ``graph.stream(...)``
call site in :mod:`graphia.driver`. Its single job is to translate the
SSE stream produced by the Runtime entrypoint
(:mod:`graphia.runtime.__main__`) back into the same
``{node_name: update_dict}`` chunk shape that LangGraph's
``stream_mode='updates'`` emits in local mode, so the existing
``_consume_stream`` consumer code is mode-agnostic.

SDK choice
----------

Spec-002 §2.8 mentions a ``bedrock_agentcore.client.RuntimeClient`` -- that
class does not exist in the published ``bedrock-agentcore`` 1.9.0 SDK
(which ships only server-side primitives: ``BedrockAgentCoreApp``,
memory/identity/gateway helpers, etc.). The supported client-side path
for invoking a deployed Runtime from outside the container is the
AWS data-plane API ``InvokeAgentRuntime``, exposed by boto3 as
``boto3.client('bedrock-agentcore').invoke_agent_runtime``. We use that.

Credentials come from the standard boto3 chain
(``AWS_PROFILE`` / SSO / instance role) per Slice 1's auth posture.

Wire-translation table
----------------------

Server SSE event                               -> Yielded chunk
---------------------------------------------- ----------------------------------------
``{"node": "N", "update": {...}}``             ``{"N": {...}}``
``{"event": "interrupt", "value": V, ...}``    ``{"__interrupt__": (Interrupt(value=V, id=...),)}``
``{"event": "done", "thread_id": ...}``        (none; iterator exits cleanly)
``{"event": "error", "error": M}``             raises :class:`AgentCoreRuntimeError`

The ``__interrupt__`` chunk shape mirrors what LangGraph itself emits in
local mode at the super-step where ``interrupt()`` fires (verified
against ``langgraph`` 1.x: ``{'__interrupt__': (Interrupt(value=...,
id='...'),)}``). Emitting the same shape lets the driver detect a paused
graph by the same key in both modes.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

import boto3
from langchain_core.load import load
from langgraph.types import Command, Interrupt


class AgentCoreRuntimeError(RuntimeError):
    """Raised when the Runtime reports an error event or returns a non-2xx
    response, or when the SSE stream is malformed beyond recovery."""


class AgentCoreClient:
    """Thin wrapper around ``boto3.client('bedrock-agentcore')``.

    One instance per game session is fine; the underlying boto3 client is
    thread-safe for read-only configuration but each ``stream()`` call
    spins up an independent ``InvokeAgentRuntime`` request.

    The class surface is intentionally small (``__init__`` +
    ``stream``) so tests can monkey-patch ``AgentCoreClient.stream`` at
    the import boundary, mirroring the ``safe_llm`` autouse pattern.
    """

    def __init__(
        self,
        *,
        runtime_arn: str,
        region: str,
        boto3_client: Any | None = None,
    ) -> None:
        if not runtime_arn:
            raise ValueError("runtime_arn is required for AgentCoreClient")
        self._runtime_arn = runtime_arn
        self._region = region
        # ``boto3_client`` is a seam for tests; production code passes None
        # and we lazy-create the real client from the default chain.
        self._client = boto3_client or boto3.client(
            "bedrock-agentcore", region_name=region
        )
        # AgentCore routes invocations by ``runtimeSessionId`` — the same id
        # lands on the same microVM, where ``/tmp/graphia/checkpoints/`` lives.
        # A start + a resume MUST share the same session id or the resume hits
        # an empty checkpoint dir and the server starts a fresh graph (resulting
        # in an infinite "enter your name" loop). We cache the padded id per
        # ``thread_id`` so it's stable for the life of this client instance.
        self._session_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stream(
        self,
        payload: Any,
        run_config: dict,
        stream_mode: str = "updates",
    ) -> Iterator[dict]:
        """Invoke the Runtime and yield local-mode-shaped stream chunks.

        ``payload`` is either an initial state ``dict`` (start) or a
        ``langgraph.types.Command(resume=...)`` (resume); we inspect it
        and build the wire-level ``{"action": "start"|"resume", ...}``
        body the Runtime expects.

        ``run_config['configurable']['thread_id']`` is propagated as the
        Runtime's ``thread_id`` so the server-side ``SqliteSaver`` lands
        on the same checkpoint.

        Only ``stream_mode='updates'`` is supported in v1; the parameter
        exists for signature parity with ``graph.stream``.
        """
        if stream_mode != "updates":
            raise NotImplementedError(
                f"AgentCoreClient.stream only supports stream_mode='updates', "
                f"got {stream_mode!r}"
            )

        thread_id = (run_config.get("configurable") or {}).get("thread_id")
        if not thread_id:
            raise ValueError(
                "run_config['configurable']['thread_id'] is required"
            )

        body = self._build_server_payload(payload, thread_id)
        response = self._invoke(body, thread_id)
        yield from self._translate_sse(response)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_server_payload(payload: Any, thread_id: str) -> dict:
        """Map local-mode ``payload`` (state-dict or Command) to the wire body."""
        if isinstance(payload, Command):
            return {
                "action": "resume",
                "thread_id": thread_id,
                "resume_value": payload.resume,
            }
        if isinstance(payload, dict) or payload is None:
            return {
                "action": "start",
                "thread_id": thread_id,
                "initial_state": payload or {},
            }
        raise TypeError(
            f"AgentCoreClient.stream payload must be dict|Command|None, "
            f"got {type(payload).__name__}"
        )

    def _session_id_for(self, thread_id: str) -> str:
        """Return a stable AgentCore ``runtimeSessionId`` for this thread.

        Cached on the instance so a start + every subsequent resume share the
        same id — AgentCore uses it for sticky routing to the microVM that
        owns ``/tmp/graphia/checkpoints/<thread_id>.sqlite``. ``uuid5`` over
        the thread id gives a deterministic 32-hex pad without leaking the
        thread id into a non-stable form.
        """
        cached = self._session_id_cache.get(thread_id)
        if cached is not None:
            return cached
        if len(thread_id) >= 33:
            session_id = thread_id
        else:
            pad = uuid.uuid5(uuid.NAMESPACE_OID, thread_id).hex
            session_id = f"{thread_id}-{pad}"
        self._session_id_cache[thread_id] = session_id
        return session_id

    def _invoke(self, body: dict, thread_id: str) -> Any:
        """Call ``invoke_agent_runtime`` and return the raw boto3 response."""
        session_id = self._session_id_for(thread_id)
        return self._client.invoke_agent_runtime(
            agentRuntimeArn=self._runtime_arn,
            contentType="application/json",
            accept="text/event-stream",
            runtimeSessionId=session_id,
            payload=json.dumps(body).encode("utf-8"),
        )

    def _translate_sse(self, response: dict) -> Iterator[dict]:
        """Iterate the response body, parse SSE frames, yield local chunks.

        boto3 surfaces the body as a ``StreamingBody`` (``response['response']``);
        in tests we accept any object with ``.iter_lines()`` or any
        iterable of ``bytes``/``str`` chunks for cheap stubbing.
        """
        status = response.get("statusCode", 200)
        if status >= 400:
            raise AgentCoreRuntimeError(
                f"InvokeAgentRuntime returned status {status}"
            )

        body = response.get("response")
        if body is None:
            raise AgentCoreRuntimeError(
                "InvokeAgentRuntime response missing 'response' streaming body"
            )

        for event in _iter_sse_events(body):
            chunk = self._translate_event(event)
            if chunk is not None:
                yield chunk

    @staticmethod
    def _translate_event(event: dict) -> dict | None:
        """Translate one parsed SSE event into a local-mode chunk (or None).

        ``None`` means "swallow this event" (e.g. the terminal ``done``
        marker; the iterator simply exits afterwards).
        """
        if "node" in event and "update" in event:
            update = _load_update(event["update"])
            return {event["node"]: update}

        ev = event.get("event")
        if ev == "interrupt":
            return {
                "__interrupt__": (
                    Interrupt(value=event.get("value"), id=event.get("id", "")),
                )
            }
        if ev == "done":
            return None
        if ev == "error":
            raise AgentCoreRuntimeError(
                f"Runtime emitted error event: {event.get('error', '<no message>')}"
            )
        # Unknown event -- skip rather than crash; the runtime may grow
        # new event kinds (e.g. tracing) before this client does.
        return None


# ----------------------------------------------------------------------
# Reconstruction of LangChain ``Serializable`` objects from wire payload.
# ----------------------------------------------------------------------


def _strip_not_implemented(obj: Any) -> Any:
    """Walk ``obj`` and replace any ``not_implemented`` markers with a string.

    ``langchain_core.load.dumpd`` emits two kinds of tagged dicts:

    * ``{'lc': 1, 'type': 'constructor', 'id': [...], 'kwargs': {...}}`` for
      registered ``Serializable`` classes (incl. all ``BaseMessage``
      subclasses). ``load`` reconstructs the original object from these.
    * ``{'lc': 1, 'type': 'not_implemented', 'id': [...], 'repr': '...'}``
      for everything else (plain dataclasses, Pydantic models like
      ``PlayerState`` / ``Pointing`` / ``Ballot`` / ``DayAction``). ``load``
      raises ``NotImplementedError`` on these.

    The UI never reads those non-Serializable values from wire chunks (the
    canonical state lives on the server's SqliteSaver; only ``messages``
    needs to round-trip), so we collapse ``not_implemented`` markers to
    their ``repr`` string here — the same lossy fallback the previous
    ``json.dumps(default=str)`` produced. This lets ``load`` run cleanly on
    the cleaned tree and reconstruct genuine ``BaseMessage`` instances.
    """
    if isinstance(obj, dict):
        if obj.get("lc") == 1 and obj.get("type") == "not_implemented":
            return obj.get("repr", "<not_implemented>")
        return {k: _strip_not_implemented(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_not_implemented(v) for v in obj]
    return obj


def _load_update(update: Any) -> Any:
    """Reconstruct LangChain ``Serializable``s in a wire-side state update.

    Strips ``not_implemented`` markers (see :func:`_strip_not_implemented`)
    then runs ``langchain_core.load.load`` so ``BaseMessage`` subclasses
    arrive at the UI as real objects with a usable ``.content`` attribute.
    """
    if not isinstance(update, (dict, list)):
        return update
    cleaned = _strip_not_implemented(update)
    try:
        return load(cleaned)
    except Exception:  # noqa: BLE001 -- fall back to the cleaned tree
        return cleaned


# ----------------------------------------------------------------------
# SSE frame parsing -- stdlib-only, tight inline loop.
# ----------------------------------------------------------------------


def _iter_sse_events(body: Any) -> Iterator[dict]:
    """Yield one parsed JSON object per ``data:`` SSE frame.

    Handles boto3's ``StreamingBody`` (which exposes ``iter_lines``) and
    any plain iterable of ``bytes``/``str`` chunks (the test seam).
    Frames are delimited by a blank line per the SSE spec; we tolerate
    both ``\\n\\n`` (typical) and missing trailing newlines.
    """
    line_iter = _line_iter(body)
    data_buf: list[str] = []
    for raw_line in line_iter:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")
        if line == "":
            if data_buf:
                yield from _parse_data_frame(data_buf)
                data_buf = []
            continue
        if line.startswith(":"):
            # SSE comment / keep-alive
            continue
        if line.startswith("data:"):
            data_buf.append(line[5:].lstrip())
            continue
        # other SSE fields (event:, id:, retry:) are not used by our
        # Runtime handler; ignore them.
    if data_buf:
        yield from _parse_data_frame(data_buf)


def _parse_data_frame(data_lines: list[str]) -> Iterator[dict]:
    """Parse the accumulated ``data:`` payload of one SSE frame."""
    text = "\n".join(data_lines).strip()
    if not text:
        return
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentCoreRuntimeError(
            f"Runtime emitted non-JSON SSE frame: {text!r} ({exc})"
        ) from exc
    if not isinstance(obj, dict):
        raise AgentCoreRuntimeError(
            f"Runtime emitted non-object SSE frame: {obj!r}"
        )
    yield obj


def _line_iter(body: Any) -> Iterator[Any]:
    """Best-effort adapter: yield SSE lines from a variety of body shapes."""
    iter_lines = getattr(body, "iter_lines", None)
    if callable(iter_lines):
        yield from iter_lines()
        return
    # Plain iterable of bytes/str chunks -- split on newlines ourselves.
    buffer = b""
    for chunk in body:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buffer += chunk
        while b"\n" in buffer:
            line, _, buffer = buffer.partition(b"\n")
            yield line
    if buffer:
        yield buffer
