"""Async bridge between the Textual UI and the synchronous LangGraph stream."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterator

from langchain_core.messages import BaseMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt

from graphia.agentcore_client import AgentCoreClient
from graphia.config import GraphiaConfig
from graphia.logging import StreamTraceLogger

_SENTINEL = object()


def _make_stream_iterator(
    graph: CompiledStateGraph,
    client: AgentCoreClient | None,
    payload: Any,
    run_config: dict,
) -> Iterator[dict]:
    """Return the chunk iterator for this super-step.

    Local mode (``client is None``): iterate the in-process compiled graph.
    Remote mode (``client`` set): iterate the deployed Runtime over the wire
    via :class:`AgentCoreClient`, which yields chunks in the **same**
    ``{node_name: update}`` shape — including ``{"__interrupt__": (Interrupt(...),)}``
    on a paused graph — so the consumer side is mode-agnostic.

    ``payload`` is either an initial-state dict / ``None`` (start) or a
    ``Command(resume=...)`` (resume); both branches accept it identically,
    so the driver's resume hand-off needs no remote-specific path.
    """
    if client is not None:
        return client.stream(payload, run_config, stream_mode="updates")
    return graph.stream(payload, run_config, stream_mode="updates")


def _producer(
    graph: CompiledStateGraph,
    client: AgentCoreClient | None,
    payload: Any,
    run_config: dict,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
) -> None:
    """Iterate the sync graph stream in a worker thread, pushing chunks live."""
    try:
        for chunk in _make_stream_iterator(graph, client, payload, run_config):
            loop.call_soon_threadsafe(queue.put_nowait, chunk)
    except BaseException as exc:  # noqa: BLE001 - forwarded to consumer
        loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)


async def _consume_stream(
    graph: CompiledStateGraph,
    client: AgentCoreClient | None,
    payload: Any,
    run_config: dict,
    logger: StreamTraceLogger,
    on_message: Callable[[BaseMessage], Awaitable[None]],
    seen_message_ids: set[str],
) -> list[Interrupt]:
    """Consume one super-step stream, capturing any interrupts inline.

    Returns the list of real :class:`Interrupt` objects that flowed through
    the stream as ``{"__interrupt__": (Interrupt(...),)}`` chunks. The driver
    uses this in remote mode where introspecting the local ``graph``'s state
    would miss interrupts that fired on the server's compiled graph.

    Only ``tuple`` payloads on the ``__interrupt__`` key are treated as real
    interrupts (LangGraph's native shape, plus the same shape synthesised by
    :meth:`AgentCoreClient._translate_event` from the server's trailing
    ``{"event": "interrupt", ...}`` envelope). Other shapes — notably the
    string-list LangGraph's bare super-step marker degrades to when
    ``_serialise_chunk`` runs ``json.dumps(default=str)`` over the
    ``Interrupt`` tuple — are skipped, not rendered as messages.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    loop = asyncio.get_running_loop()

    payload_kind = "Command" if isinstance(payload, Command) else type(payload).__name__
    logger.record({"driver": "consume_start", "payload_kind": payload_kind})

    producer_task = asyncio.create_task(
        asyncio.to_thread(_producer, graph, client, payload, run_config, loop, queue)
    )

    captured_interrupts: list[Interrupt] = []
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                logger.record({"driver": "sentinel"})
                break
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "error":
                logger.record({"driver": "producer_error", "error": repr(item[1])})
                raise item[1]
            for node_name, update in item.items():
                if node_name == "__interrupt__":
                    # Real interrupts arrive as a tuple of ``Interrupt`` objects
                    # (LangGraph native + the shape the AgentCore client
                    # synthesises). Stringified/serialised duplicates from the
                    # server's bare marker pass-through arrive as list[str] and
                    # are intentionally dropped.
                    if isinstance(update, tuple):
                        captured_interrupts.extend(update)
                    logger.record(
                        {
                            "node": node_name,
                            "keys": [],
                            "cycle": None,
                            "phase": None,
                        }
                    )
                    continue
                snapshot = graph.get_state(run_config)
                logger.record(
                    {
                        "node": node_name,
                        "keys": list(update.keys()) if isinstance(update, dict) else [],
                        "cycle": snapshot.values.get("cycle"),
                        "phase": snapshot.values.get("phase"),
                    }
                )
                if isinstance(update, dict) and "messages" in update:
                    for msg in update["messages"]:
                        msg_id = getattr(msg, "id", None) or str(id(msg))
                        if msg_id in seen_message_ids:
                            continue
                        seen_message_ids.add(msg_id)
                        await on_message(msg)
    finally:
        await producer_task
    return captured_interrupts


async def drive_graph(
    graph: CompiledStateGraph,
    run_config: dict,
    initial: dict,
    logger: StreamTraceLogger,
    on_message: Callable[[BaseMessage], Awaitable[None]],
    request_resume: Callable[[dict], Awaitable[Any]],
    config: GraphiaConfig | None = None,
) -> None:
    """Drive the compiled graph, flushing each super-step to the UI as it arrives.

    When ``config.remote_mode`` is True, the chunk source is the deployed
    AgentCore Runtime (via :class:`AgentCoreClient`) instead of the
    in-process compiled ``graph``; the chunk shape is identical so the
    consumer loop, resume hand-off, and UI plumbing are unchanged.

    Interrupt detection is mode-aware. In **local mode** we introspect
    the in-process graph's state via :meth:`get_state` — that path also
    covers the "paused without interrupt" continuation branch below. In
    **remote mode** the work runs in a different process whose
    ``SqliteSaver`` is unreachable from here, so the local ``graph``'s
    state would be empty; we instead capture interrupts from the stream
    chunks themselves (the ``{"__interrupt__": (Interrupt(...),)}`` shape
    emitted by the AgentCore client) inside :func:`_consume_stream`.
    The ``graph`` argument is still required so the local-mode path can
    use it and so :func:`_consume_stream` has a stable handle for the
    per-message snapshot lookups during normal chunks.
    """
    seen: set[str] = set()
    payload: Any = initial

    client: AgentCoreClient | None = None
    if config is not None and config.remote_mode:
        # Construct once per ``drive_graph`` call so the underlying boto3
        # client (and any pooled HTTPS connection) is reused across every
        # super-step / resume in the session.
        client = AgentCoreClient(
            runtime_arn=config.runtime_invocation_url or "",
            region=config.aws_region,
        )
        logger.record(
            {"driver": "remote_mode_client_ready", "region": config.aws_region}
        )

    while True:
        captured_interrupts = await _consume_stream(
            graph, client, payload, run_config, logger, on_message, seen
        )

        if client is not None:
            # Remote mode: the local ``graph`` instance never ran the
            # super-step (the work happened on the server's compiled graph
            # in a different process with its own SqliteSaver). Trust the
            # interrupts the stream itself carried; ``next_nodes`` becomes a
            # non-empty sentinel iff there is an interrupt so the existing
            # "no next nodes -> return" early-exit below still works.
            interrupts = captured_interrupts
            next_nodes = ["<remote>"] if interrupts else []
        else:
            # Local mode: the in-process graph is the source of truth.
            # Preserve the existing behaviour exactly — including the
            # "paused without interrupt" branch below, whose correctness
            # depends on local-graph state introspection.
            snapshot = graph.get_state(run_config)
            next_nodes = list(snapshot.next or ())
            interrupts = [
                i for t in snapshot.tasks for i in (t.interrupts or ())
            ]
        logger.record(
            {
                "driver": "post_stream_snapshot",
                "next": next_nodes,
                "interrupt_count": len(interrupts),
            }
        )
        if not next_nodes:
            return
        if not interrupts:
            # Graph paused at a super-step boundary without an interrupt.
            # Resume by streaming with ``None`` to flush the next super-step.
            logger.record({"driver": "resume_without_interrupt"})
            payload = None
            continue

        logger.record(
            {"driver": "request_resume", "interrupt_value": repr(interrupts[0].value)}
        )
        resume_value = await request_resume(interrupts[0].value)
        logger.record({"driver": "resume_returned", "value": repr(resume_value)})
        payload = Command(resume=resume_value)
