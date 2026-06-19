"""Async bridge between the Textual UI and the synchronous LangGraph stream."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any, Awaitable, Callable, Iterator

from langchain_core.messages import BaseMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt

from graphia.agentcore_client import AgentCoreClient
from graphia.config import GraphiaConfig
from graphia.logging import StreamTraceLogger

_SENTINEL = object()

# Real-completion signal for in-flight :func:`_producer` worker threads.
#
# ``asyncio.to_thread`` runs each producer on a persistent ThreadPoolExecutor,
# so the pool threads stay alive idle between super-steps — a naive
# ``threading.active_count()`` / thread-join check cannot tell whether a
# *producer body* is still running. Instead each producer owns a
# ``threading.Event`` that it sets in its ``finally`` (the body's true exit,
# whether it completed, errored, or was cancelled-from-asyncio but kept running
# in the background because the underlying thread cannot be killed). The driver
# registers the event for the run's lifetime; :func:`wait_for_producers_quiescent`
# blocks (bounded) on all outstanding events.
#
# This closes a cross-test isolation leak: on a user-cancelled exit the
# ``consumer_cancelled`` branch deliberately does NOT await the producer thread
# (it may be parked in a slow Bedrock call in the real app), so a still-running
# producer can keep consuming the module-global ``random`` after the test that
# launched it has exited — corrupting the next test's RNG-dependent trajectory.
# The autouse pytest fixture calls :func:`wait_for_producers_quiescent` at
# teardown (where no asyncio cancellation is pending, unlike the driver's
# ``finally``), so a mocked-and-therefore-fast leaked producer drains BEFORE the
# next test runs. In the real app this is never on the hot path — it is only
# called from the test fixture; production exit behaviour is unchanged.
_inflight_producers: set[threading.Event] = set()
_inflight_lock = threading.Lock()


def wait_for_producers_quiescent(timeout: float = 5.0) -> bool:
    """Block (bounded) until every in-flight :func:`_producer` body has finished.

    Returns ``True`` if all producers quiesced within ``timeout`` seconds,
    ``False`` if the deadline was hit with one still running. Safe to call with
    no producers outstanding (returns ``True`` immediately). Intended for test
    teardown — it joins the producer bodies via their completion events so a
    leaked background producer can no longer race the next test's RNG state.
    """
    deadline = time.monotonic() + timeout
    while True:
        with _inflight_lock:
            outstanding = [e for e in _inflight_producers if not e.is_set()]
        if not outstanding:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        # Wait on the first outstanding event; loop re-snapshots the rest.
        outstanding[0].wait(timeout=remaining)


def _swallow_task_result(task: asyncio.Task) -> None:
    """Done-callback that consumes a task's result/exception silently.

    Attached to the producer task on user-cancelled exit so asyncio does
    not emit ``Task exception was never retrieved`` warnings when the
    producer eventually finishes (or raises) after we detach from it.
    """
    with contextlib.suppress(BaseException):
        task.exception()


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
    done_event: threading.Event,
) -> None:
    """Iterate the sync graph stream in a worker thread, pushing chunks live.

    ``done_event`` is set in the ``finally`` — the body's true exit point — so
    a watcher (test teardown via :func:`wait_for_producers_quiescent`) can tell
    when this producer body has actually stopped touching shared module state
    (the global ``random`` RNG), even on the cancelled-but-kept-running path
    where the asyncio task wrapper is cancelled but the thread runs on.
    """
    try:
        for chunk in _make_stream_iterator(graph, client, payload, run_config):
            loop.call_soon_threadsafe(queue.put_nowait, chunk)
    except BaseException as exc:  # noqa: BLE001 - forwarded to consumer
        loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
    finally:
        done_event.set()
        loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)


async def _consume_stream(
    graph: CompiledStateGraph,
    client: AgentCoreClient | None,
    payload: Any,
    run_config: dict,
    logger: StreamTraceLogger,
    on_message: Callable[[BaseMessage], Awaitable[None]],
    seen_message_ids: set[str],
    on_state: Callable[[dict], Awaitable[None]] | None = None,
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

    # Completion signal for this super-step's producer body (set in its
    # ``finally``). Registered for the run so test teardown can join it via
    # :func:`wait_for_producers_quiescent` even on the cancelled-but-still-
    # running path; pruned once set to bound the registry over a long session.
    done_event = threading.Event()
    with _inflight_lock:
        _inflight_producers.add(done_event)
        # Opportunistically drop any already-finished producers so the set
        # does not grow across a full game's many super-steps.
        for finished in [e for e in _inflight_producers if e.is_set()]:
            _inflight_producers.discard(finished)

    producer_task = asyncio.create_task(
        asyncio.to_thread(
            _producer, graph, client, payload, run_config, loop, queue, done_event
        )
    )

    captured_interrupts: list[Interrupt] = []
    consumer_cancelled = False
    try:
        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                # Quit was requested (Esc → QuitModal → exit, or Ctrl+C).
                # We must not block on the producer thread — it may be
                # mid-Bedrock-call (1–10s) and awaiting it would keep the
                # Python process alive after Textual has already torn the
                # UI down. Flag the cancellation so the finally block
                # cancels the task without awaiting it, and re-raise.
                consumer_cancelled = True
                raise
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
                # Surface chunk state to the UI mirror *before* dispatching the
                # chunk's messages: a collect_name chunk carries both human_id
                # and the welcome message, and _handle_graph_message must see
                # the id when it routes that message's private_to addressing.
                if on_state is not None and isinstance(update, dict):
                    await on_state(update)
                if isinstance(update, dict) and "messages" in update:
                    for msg in update["messages"]:
                        msg_id = getattr(msg, "id", None) or str(id(msg))
                        if msg_id in seen_message_ids:
                            continue
                        seen_message_ids.add(msg_id)
                        await on_message(msg)
    finally:
        if consumer_cancelled:
            # User-requested exit: cancel the producer task but do NOT
            # await it. The asyncio.to_thread worker is wrapping a
            # synchronous graph.stream() iteration that may be parked
            # inside a Bedrock call; awaiting would block the event-loop
            # teardown until that call returns. The underlying thread
            # cannot be cancelled from Python; it will run to completion
            # of the current super-step in the background. The daemon-
            # Timer fallback in GraphiaApp._on_quit_decision guarantees
            # process exit even if the thread is still alive. Mark the
            # task's result as consumed so asyncio doesn't warn about an
            # un-retrieved exception on a cancelled-but-still-pending task.
            #
            # ``done_event`` is deliberately LEFT registered: the thread is
            # still running, so it will set the event from its ``finally``
            # when it eventually finishes. Test teardown's
            # :func:`wait_for_producers_quiescent` joins that event so the
            # leaked producer drains before the next test — closing the
            # cross-test global-RNG contention. Production exit is unaffected
            # (the waiter is only invoked from the test fixture).
            if not producer_task.done():
                producer_task.cancel()
            producer_task.add_done_callback(_swallow_task_result)
        else:
            # Normal completion: the producer body has finished and set its
            # event. Drop it from the registry so the set stays bounded.
            await producer_task
            with _inflight_lock:
                _inflight_producers.discard(done_event)
    return captured_interrupts


async def drive_graph(
    graph: CompiledStateGraph,
    run_config: dict,
    initial: dict,
    logger: StreamTraceLogger,
    on_message: Callable[[BaseMessage], Awaitable[None]],
    request_resume: Callable[[dict], Awaitable[Any]],
    config: GraphiaConfig | None = None,
    on_state: Callable[[dict], Awaitable[None]] | None = None,
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
            graph, client, payload, run_config, logger, on_message, seen, on_state
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
