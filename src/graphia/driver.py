"""Async bridge between the Textual UI and the synchronous LangGraph stream."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from langchain_core.messages import BaseMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from graphia.logging import StreamTraceLogger

_SENTINEL = object()


def _producer(
    graph: CompiledStateGraph,
    payload: Any,
    run_config: dict,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
) -> None:
    """Iterate the sync graph stream in a worker thread, pushing chunks live."""
    try:
        for chunk in graph.stream(payload, run_config, stream_mode="updates"):
            loop.call_soon_threadsafe(queue.put_nowait, chunk)
    except BaseException as exc:  # noqa: BLE001 - forwarded to consumer
        loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)


async def _consume_stream(
    graph: CompiledStateGraph,
    payload: Any,
    run_config: dict,
    logger: StreamTraceLogger,
    on_message: Callable[[BaseMessage], Awaitable[None]],
    seen_message_ids: set[str],
) -> None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    loop = asyncio.get_running_loop()

    payload_kind = "Command" if isinstance(payload, Command) else type(payload).__name__
    logger.record({"driver": "consume_start", "payload_kind": payload_kind})

    producer_task = asyncio.create_task(
        asyncio.to_thread(_producer, graph, payload, run_config, loop, queue)
    )

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


async def drive_graph(
    graph: CompiledStateGraph,
    run_config: dict,
    initial: dict,
    logger: StreamTraceLogger,
    on_message: Callable[[BaseMessage], Awaitable[None]],
    request_resume: Callable[[dict], Awaitable[Any]],
) -> None:
    """Drive the compiled graph, flushing each super-step to the UI as it arrives."""
    seen: set[str] = set()
    payload: Any = initial

    while True:
        await _consume_stream(graph, payload, run_config, logger, on_message, seen)

        snapshot = graph.get_state(run_config)
        next_nodes = list(snapshot.next or ())
        interrupts = [i for t in snapshot.tasks for i in (t.interrupts or ())]
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
