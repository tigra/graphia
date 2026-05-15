"""Slice 8: Runtime observability — every emitted record carries ``thread_id``.

Spec-002 §2.5 requires that, after a remote game, the CloudWatch records
for one game are selectable as a set. The follow-on failure modal builds a
``{ $.thread_id = "<thread>" }`` CloudWatch filter; that filter only works
if every log/trace record the AgentCore Runtime emits is a structured JSON
line carrying a top-level ``thread_id`` field equal to the session's
LangGraph thread id.

These tests pin that correlation-id invariant at the unit level. They
exercise :mod:`graphia.runtime.observability` directly — no real AWS, no
LLM, no AgentCore SDK — so they run inside the all-mocked suite.
"""

from __future__ import annotations

import json
import logging

import pytest

from graphia.runtime.observability import (
    SESSION_ID_BAGGAGE_KEY,
    THREAD_ID_FIELD,
    JsonLogFormatter,
    ThreadIdLogFilter,
    bind_thread_id,
    configure_runtime_observability,
    current_thread_id,
    stamp_trace_thread_id,
)


def _format(record: logging.LogRecord) -> dict:
    """Run a record through the production filter + formatter, return JSON."""
    ThreadIdLogFilter().filter(record)
    line = JsonLogFormatter().format(record)
    # Must be a single line — CloudWatch metric filters match per line.
    assert "\n" not in line, f"JSON log record must be single-line; got {line!r}"
    return json.loads(line)


def _make_record(
    msg: str = "hello",
    *,
    level: int = logging.INFO,
    extra: dict | None = None,
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="graphia.runtime",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


@pytest.fixture(autouse=True)
def _reset_thread_id():
    """Clear the bound thread_id before and after each test."""
    token = bind_thread_id(None)  # type: ignore[arg-type]
    yield
    try:
        # Leave the ContextVar unset so tests don't bleed into each other.
        import graphia.runtime.observability as _obs

        _obs._THREAD_ID.set(None)
    except Exception:  # noqa: BLE001
        pass
    del token


def test_bound_thread_id_lands_as_top_level_json_field() -> None:
    """A record emitted after ``bind_thread_id`` carries that id as ``thread_id``.

    This is the exact invariant the failure modal's
    ``{ $.thread_id = "<thread>" }`` CloudWatch filter depends on.
    """
    bind_thread_id("20260515T101530")
    payload = _format(_make_record("runtime invocation start"))

    assert payload[THREAD_ID_FIELD] == "20260515T101530"
    assert payload["message"] == "runtime invocation start"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "graphia.runtime"
    # ``ts`` is always present and ISO-8601 — a stable record shape.
    assert "ts" in payload and "T" in payload["ts"]


def test_thread_id_field_is_always_present_even_when_unbound() -> None:
    """With no thread_id bound the field is still present, valued ``None``.

    Keeping the key present (rather than omitting it) means the JSON shape
    is stable; the CloudWatch filter simply does not match such records,
    which is the desired behaviour for pre-invocation noise.
    """
    payload = _format(_make_record("import-time noise"))
    assert THREAD_ID_FIELD in payload
    assert payload[THREAD_ID_FIELD] is None


def test_each_invocation_id_is_independent() -> None:
    """Re-binding swaps the correlation id — no bleed between sessions."""
    bind_thread_id("game-A")
    assert current_thread_id() == "game-A"
    first = _format(_make_record("step in game A"))

    bind_thread_id("game-B")
    second = _format(_make_record("step in game B"))

    assert first[THREAD_ID_FIELD] == "game-A"
    assert second[THREAD_ID_FIELD] == "game-B"


def test_extra_fields_pass_through_to_json() -> None:
    """``extra={...}`` structured fields (e.g. ``event``, ``node``) survive.

    The Runtime tags lifecycle records with ``event`` and per-super-step
    records with ``node``; those must reach CloudWatch alongside thread_id
    so a single game's events are not just filterable but also legible.
    """
    bind_thread_id("game-C")
    payload = _format(
        _make_record(
            "graph super-step",
            extra={"event": "graph_step", "node": "resolve_vote"},
        )
    )
    assert payload[THREAD_ID_FIELD] == "game-C"
    assert payload["event"] == "graph_step"
    assert payload["node"] == "resolve_vote"


def test_crash_record_carries_traceback_and_thread_id() -> None:
    """A crashed remote game leaves a full, thread-id-filterable traceback.

    Spec-002 §2.5: a crashed remote game leaves a full traceback in
    CloudWatch and the failure modal points at the log group + filter.
    The traceback record must still carry thread_id for that filter to
    select it.
    """
    bind_thread_id("game-crash")
    try:
        raise ValueError("night resolution blew up")
    except ValueError:
        import sys

        record = _make_record(
            "graph.stream failed",
            level=logging.ERROR,
            extra={"event": "graph_stream_error"},
            exc_info=sys.exc_info(),
        )
    payload = _format(record)

    assert payload[THREAD_ID_FIELD] == "game-crash"
    assert payload["level"] == "ERROR"
    assert payload["event"] == "graph_stream_error"
    assert "traceback" in payload
    assert "ValueError: night resolution blew up" in payload["traceback"]


def test_configure_runtime_observability_is_idempotent() -> None:
    """Repeated calls install exactly one JSON handler — no handler stacking."""
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_runtime_observability()
        configure_runtime_observability()
        json_handlers = [
            h
            for h in root.handlers
            if getattr(h, "_graphia_json_handler", False)
        ]
        assert len(json_handlers) == 1, (
            f"expected exactly one graphia JSON handler, got {len(json_handlers)}"
        )
        # The installed handler renders JSON carrying thread_id.
        handler = json_handlers[0]
        assert isinstance(handler.formatter, JsonLogFormatter)
    finally:
        # Restore the root logger so we don't leak a stdout handler into
        # the rest of the suite.
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)


def test_stamp_trace_thread_id_never_raises() -> None:
    """The trace stamp must degrade silently regardless of OTEL state.

    Telemetry must never break a game. Whether or not an OTEL SDK is on
    the path — and whether or not the ADOT ``opentelemetry-instrument``
    wrapper has configured a real exporter — calling the stamp must be a
    safe operation. (In the test environment ``aws-opentelemetry-distro``
    is installed but no exporter is configured, so the no-op
    ``ProxyTracerProvider`` is active and nothing is exported.)
    """
    # Must not raise regardless of OTEL availability / exporter config.
    stamp_trace_thread_id("game-no-otel")


def test_stamp_trace_thread_id_places_session_id_in_baggage() -> None:
    """CR 003: the game's thread_id lands in OTEL baggage as ``session.id``.

    AWS GenAI Observability groups a session's spans into one navigable
    trace tree by the ``session.id`` baggage entry. Stamping it to the
    LangGraph thread_id is what makes one game render as exactly one
    session in the console. The raw ``thread_id`` key is also placed so a
    custom span can read the unaliased game id.

    This test is meaningful only when an OTEL SDK is importable; when it
    is not (no ADOT on the path) the stamp is a no-op by design and the
    assertions are skipped.
    """
    try:
        from opentelemetry import baggage, context
    except ImportError:  # pragma: no cover - ADOT is a project dependency
        pytest.skip("no OpenTelemetry SDK on the path")

    token = context.attach(context.Context())  # isolate baggage for this test
    try:
        stamp_trace_thread_id("20260515T101530")
        assert baggage.get_baggage(SESSION_ID_BAGGAGE_KEY) == "20260515T101530"
        assert baggage.get_baggage(THREAD_ID_FIELD) == "20260515T101530"
    finally:
        context.detach(token)


# --------------------------------------------------------------------------
# Integration: the wired Runtime entry-point stamps thread_id on its records
# --------------------------------------------------------------------------
#
# The unit tests above pin the observability module in isolation. This test
# drives the real ``@app.entrypoint`` handler against a fake graph and
# asserts every JSON log line the handler emits during the invocation
# carries the payload's thread_id — the concrete, end-to-end form of the
# spec-002 §2.5 correlation-id requirement.


async def test_runtime_handler_stamps_thread_id_on_every_emitted_log(
    env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving ``handler`` with a payload yields log lines all carrying thread_id.

    A CloudWatch ``{ $.thread_id = "<thread>" }`` filter run over the
    captured lines would select every record this invocation produced —
    invocation-start, per-super-step, and invocation-done — which is
    exactly the failure-modal contract.
    """
    import logging as _logging

    import graphia.runtime.__main__ as runtime_main

    # --- Fake graph: a couple of super-steps, no interrupt -------------
    class _FakeGraph:
        def stream(self, payload, run_config, stream_mode="updates"):
            yield {"night_open": {"phase": "night"}}
            yield {"check_win_night": {"phase": "night"}}

        def get_state(self, run_config):
            class _Snapshot:
                tasks = ()

            return _Snapshot()

    monkeypatch.setattr(
        runtime_main, "build_runtime_graph", lambda *a, **k: _FakeGraph()
    )

    # --- Capture every JSON line the handler's logging emits ----------
    captured: list[str] = []

    class _CapturingHandler(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            captured.append(self.format(record))

    cap = _CapturingHandler()
    cap.setFormatter(JsonLogFormatter())
    cap.addFilter(ThreadIdLogFilter())
    root = _logging.getLogger()
    root.addHandler(cap)
    prior_level = root.level
    root.setLevel(_logging.INFO)

    THREAD = "20260515T120000"
    try:
        events = [
            ev
            async for ev in runtime_main.handler(
                {
                    "action": "start",
                    "thread_id": THREAD,
                    "initial_state": {"messages": []},
                }
            )
        ]
    finally:
        root.removeHandler(cap)
        root.setLevel(prior_level)

    # The handler streamed the two super-steps plus a trailing done event.
    nodes = [e["node"] for e in events if "node" in e]
    assert nodes == ["night_open", "check_win_night"]
    assert any(e.get("event") == "done" for e in events)

    # Every captured log line is JSON carrying the invocation's thread_id.
    assert captured, "handler emitted no log records"
    payloads = [json.loads(line) for line in captured]
    for payload in payloads:
        assert payload[THREAD_ID_FIELD] == THREAD, (
            f"a log record is not stamped with the thread_id: {payload!r}"
        )

    # The lifecycle + per-super-step trace events are all present and
    # filterable as one set.
    event_kinds = {p.get("event") for p in payloads}
    assert "invocation_start" in event_kinds
    assert "graph_step" in event_kinds
    assert "invocation_done" in event_kinds
