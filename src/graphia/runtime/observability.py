"""Runtime-side observability wiring — stamps the LangGraph ``thread_id``
onto every log/trace record the AgentCore Runtime emits.

Why this module exists
----------------------

Spec-002 §2.5 requires that, after a remote game, the traces in the
CloudWatch log group are *filterable down to a single game*. The
follow-on failure modal (``src/graphia/ui/failure_modal.py``) shows the
player a copy-pasteable CloudWatch Logs Insights / metric-filter
expression of the form::

    { $.thread_id = "<thread>" }

That filter only selects anything if every record the Runtime emits is a
**structured JSON line carrying a top-level ``thread_id`` field**.

How the Runtime's telemetry reaches CloudWatch
----------------------------------------------

The container baked from this repo runs ``python -m graphia.runtime``
directly (see ``Dockerfile``). AgentCore Runtime captures the
container's **stdout/stderr** as the ``APPLICATION_LOGS`` telemetry
stream; the previous Slice-8 task wired a CloudWatch vended-log-delivery
pipeline that ships ``APPLICATION_LOGS`` (and ``TRACES``) to a 30-day
log group. So the cleanest, dependency-free way to land a ``thread_id``
field on every CloudWatch record is: **emit every Runtime log line as a
single-line JSON object on stdout**, with ``thread_id`` as a top-level
key.

Mechanism
---------

* A module-level :class:`contextvars.ContextVar` holds the current
  invocation's ``thread_id``. ``contextvars`` is the right tool because
  the AgentCore SDK iterates the ``@app.entrypoint`` async generator in
  its own task/loop — a ``ContextVar`` set at the top of the handler is
  visible to every ``logging`` call made for the rest of that
  invocation, and AgentCore microVMs are single-session so there is no
  cross-talk.
* A :class:`logging.Filter` (:class:`ThreadIdLogFilter`) reads that
  ContextVar and attaches ``thread_id`` to every :class:`logging.LogRecord`.
* A :class:`logging.Formatter` (:class:`JsonLogFormatter`) renders each
  record as a one-line JSON object — the shape the CloudWatch
  ``{ $.thread_id = "..." }`` filter matches.

The trace half (OTEL spans) is now live on the deployed Runtime image
(CR 003): the container starts under the ADOT ``opentelemetry-instrument``
auto-instrumentation wrapper, so an OpenTelemetry SDK *is* on the path
there. :func:`stamp_trace_thread_id` puts the game's ``thread_id`` into
OTEL baggage under the key AWS GenAI Observability groups a session's
spans by (``session.id``) and also stamps it as an attribute on the
current span. That makes the per-session trace tree both navigable
(grouped as one session) and filterable by game. The import is still
guarded so local mode and the test environment — neither of which runs
the ADOT wrapper — see a silent no-op and pull in no OTEL runtime
behaviour. The JSON-log path above is independent of all this and keeps
working with zero extra deps.

Local mode is untouched
-----------------------

This module is imported only by ``graphia.runtime.__main__`` (the remote
Runtime entry-point). Local mode keeps emitting its JSONL trace via
:class:`graphia.logging.StreamTraceLogger` to ``GRAPHIA_LOG_FILE`` and
never imports anything here.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

# The correlation id for the in-flight invocation. Set once at the top of
# the ``@app.entrypoint`` handler via :func:`bind_thread_id`; read by
# :class:`ThreadIdLogFilter` on every log record emitted thereafter.
_THREAD_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "graphia_runtime_thread_id", default=None
)

# Top-level JSON key the CloudWatch filter ``{ $.thread_id = "..." }``
# matches against. Kept as a named constant so the failure modal (a later
# Slice-8 sub-task) and any tests reference one source of truth.
THREAD_ID_FIELD = "thread_id"

# OTEL baggage key AWS GenAI Observability groups a trace's spans into a
# single navigable *session* by. ADOT's AWS configurator reads ``session.id``
# from baggage and promotes it onto the spans / X-Ray segments, which is what
# makes the GenAI Observability "Sessions" view collapse one game's spans
# into one tree. Setting it to the LangGraph ``thread_id`` means one game ==
# one session in the console.
SESSION_ID_BAGGAGE_KEY = "session.id"


def bind_thread_id(thread_id: str) -> contextvars.Token:
    """Establish ``thread_id`` as the correlation id for this invocation.

    Call this as the **first** thing once a payload's ``thread_id`` is
    known. Every :mod:`logging` call made afterwards in the same context
    — including from inside the LangGraph nodes — has ``thread_id``
    stamped onto its JSON record by :class:`ThreadIdLogFilter`.

    Returns the :class:`contextvars.Token` so the caller can
    :func:`contextvars.ContextVar.reset` it; in practice the AgentCore
    microVM is single-session and torn down after the invocation, so a
    reset is defensive hygiene rather than a correctness requirement.
    """
    return _THREAD_ID.set(thread_id)


def current_thread_id() -> str | None:
    """Return the ``thread_id`` bound for the current invocation, if any."""
    return _THREAD_ID.get()


class ThreadIdLogFilter(logging.Filter):
    """Inject the bound ``thread_id`` onto every :class:`logging.LogRecord`.

    A :class:`logging.Filter` (not a :class:`logging.Handler`) is the
    right seam: filters run for *every* record on the handler regardless
    of which logger created it, so a node deep in the graph that does
    ``logging.getLogger(__name__).info(...)`` still gets the field
    without that node knowing anything about observability.

    When no ``thread_id`` is bound (records emitted before the first
    invocation, or from a stray import-time log) the field is set to
    ``None`` so the JSON shape stays stable — the CloudWatch filter
    simply will not match those records, which is the desired behaviour.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.thread_id = current_thread_id()
        return True


class JsonLogFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single-line JSON object.

    The output shape is::

        {"ts": "...", "level": "INFO", "logger": "graphia.runtime",
         "thread_id": "<thread>", "message": "...", ...}

    ``thread_id`` is promoted to a top-level key so the CloudWatch
    metric-filter / Logs-Insights expression ``{ $.thread_id = "..." }``
    selects exactly the records for one game. Exceptions are flattened
    into a ``traceback`` string field so a crashed remote game leaves a
    full, filterable traceback in CloudWatch (spec-002 §2.5).
    """

    # Standard LogRecord attributes we never want echoed into the JSON
    # body — everything else attached to the record (e.g. ``thread_id``
    # from the filter, plus any ``extra=`` fields) is included.
    _RESERVED = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "message",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # ``thread_id`` is attached by ThreadIdLogFilter; default to
            # None so the key is always present even if the filter is
            # somehow bypassed.
            THREAD_ID_FIELD: getattr(record, THREAD_ID_FIELD, None),
            "message": record.getMessage(),
        }
        # Carry any structured ``extra={...}`` fields through verbatim.
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key == THREAD_ID_FIELD:
                continue
            if key not in payload:
                payload[key] = value
        if record.exc_info:
            # A crashed remote game must leave a full traceback in
            # CloudWatch, still carrying thread_id for filtering.
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_runtime_observability() -> None:
    """Install JSON-structured, thread-id-stamped logging on the root logger.

    Idempotent: a second call is a no-op (guarded by a marker attribute
    on the installed handler) so re-imports during test collection do not
    stack handlers.

    After this runs, every :mod:`logging` call anywhere in the process —
    ``graphia.runtime``, the LangGraph nodes, third-party libraries —
    lands on stdout as a single-line JSON object carrying ``thread_id``.
    AgentCore Runtime captures that stdout as ``APPLICATION_LOGS`` and the
    Slice-8 vended-log-delivery pipeline ships it to CloudWatch, where
    ``{ $.thread_id = "<thread>" }`` selects exactly one game's events.
    """
    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, "_graphia_json_handler", False):
            return  # already configured

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(ThreadIdLogFilter())
    handler._graphia_json_handler = True  # type: ignore[attr-defined]

    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def stamp_trace_thread_id(thread_id: str) -> None:
    """Attach the game's ``thread_id`` to this invocation's OTEL spans.

    The deployed Runtime image starts under the ADOT
    ``opentelemetry-instrument`` wrapper (see ``Dockerfile``), so an
    OpenTelemetry SDK is on the path there and AgentCore captures the
    emitted spans as the ``TRACES`` telemetry stream. This function does
    two things, both best-effort:

    * **Session grouping.** It puts ``thread_id`` into OTEL *baggage*
      under :data:`SESSION_ID_BAGGAGE_KEY` (``"session.id"``) and attaches
      that context. ADOT's AWS configurator reads ``session.id`` from
      baggage and promotes it onto every span produced for the rest of
      the invocation — including the child spans the auto-instrumented
      LangGraph / Bedrock / Gateway-MCP calls create. That is what makes
      AgentCore GenAI Observability collapse one game's spans into a
      single navigable per-session **trace tree** (CR 003).
    * **Per-game filtering.** It also sets ``thread_id`` as an attribute
      on the current span so the trace half is filterable by the same
      game identifier the structured logs already carry.

    The ``context.attach`` token is intentionally not reset: AgentCore
    microVMs are single-session, the handler runs once per microVM, and
    the baggage must stay live for the whole invocation so every
    downstream child span inherits it.

    OTEL is **not** a hard dependency of Graphia at *runtime* in local
    mode — local mode never imports this module and never runs the ADOT
    wrapper — so the import is still guarded. When the OTEL SDK is absent
    (local mode, the all-mocked test suite) this is a silent no-op and
    the JSON-log path above is unaffected. Telemetry must never break a
    game, so every step is also wrapped defensively.
    """
    try:
        from opentelemetry import baggage, context, trace
    except ImportError:
        return  # No OTEL SDK on the path — nothing to stamp.

    # Group this invocation's spans into one navigable session. AWS GenAI
    # Observability keys the Sessions/Trace view off the ``session.id``
    # baggage entry; setting it to the LangGraph thread_id means one game
    # renders as exactly one session tree.
    try:
        context.attach(
            baggage.set_baggage(SESSION_ID_BAGGAGE_KEY, thread_id)
        )
    except Exception:  # noqa: BLE001 - telemetry must never break the game
        pass
    # Also place thread_id itself in baggage so any custom span that wants
    # the raw game id (not the session.id alias) can read it.
    try:
        context.attach(baggage.set_baggage(THREAD_ID_FIELD, thread_id))
    except Exception:  # noqa: BLE001
        pass
    # Stamp the current span so the trace half is filterable by thread_id,
    # mirroring the field the structured JSON logs carry.
    try:
        trace.get_current_span().set_attribute(THREAD_ID_FIELD, thread_id)
    except Exception:  # noqa: BLE001
        pass
