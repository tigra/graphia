"""Slice 8 (CR 003): the Runtime emits a navigable per-session *trace tree*.

CR 003 sharpens the Phase-2 observability deliverable: remote-mode play must
produce a navigable per-session **trace tree** in AgentCore's GenAI
Observability view — a parent/child span hierarchy of one game's agent
activity (Runtime invocation root -> per-node graph execution -> per-turn
model calls) — not only flat, session-correlated log events.

A trace tree is built from OpenTelemetry spans with correct parent linkage.
The tests in :mod:`test_slice8_observability` already pin the structured-log
half. *This* file pins the trace half: that driving the real
``@app.entrypoint`` handler produces a span **tree** (one root, child spans,
depth > 1, one shared trace id, the game's ``thread_id`` on the spans) — not
a flat list of unparented spans.

How this stays deterministic and offline
-----------------------------------------

* Spans are captured by an :class:`InMemorySpanExporter` wired to a real
  :class:`TracerProvider` via a :class:`SimpleSpanProcessor` — no collector,
  no AWS, no network.
* :func:`configure_runtime_observability` is the production code path that
  installs the LangChain/LangGraph GenAI instrumentor. The test calls it,
  then drives the handler; the same instrumentation runs in production and
  here — there is no test-only telemetry wiring.
* The "graph" the handler runs is a **real** LangGraph ``CompiledStateGraph``
  with two nodes, one of which invokes a real LangChain ``Runnable``
  (a model-call stand-in). LangGraph + LangChain are exactly what
  ``LangChainInstrumentor`` instruments, so the captured spans are produced
  by the genuine instrumentation path — only the Bedrock transport is absent.
  No real LLM is reached (the autouse ``safe_llm`` fixture still guards that).

Why the pre-fix code fails this test
-------------------------------------

Before the CR 003 fix, ``configure_runtime_observability`` installed only the
JSON log handler — no LangGraph instrumentor was ever loaded (the work assumed
ADOT auto-loads a non-existent ``aws_langchain`` instrumentor), and the
handler opened no root span. So driving the handler produces **zero in-app
spans**: the captured-span list is empty, there is no root, and the tree
assertions below cannot hold. That empirically reproduces the flat-trajectory
defect investigated on deployed image ``4f164f3``.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


# --------------------------------------------------------------------------
# Tracer-provider harness — captures spans in-process, no collector / no AWS.
# --------------------------------------------------------------------------


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Install a fresh in-memory TracerProvider and return its span exporter.

    The OpenTelemetry SDK only lets a *real* TracerProvider be set once per
    process; later ``set_tracer_provider`` calls are ignored with a warning.
    To keep this test isolated and re-runnable we bypass the global setter
    and patch ``opentelemetry.trace._TRACER_PROVIDER`` directly, restoring
    the prior value on teardown.

    The exporter is paired with a ``SimpleSpanProcessor`` so every span is
    exported synchronously on ``end()`` — by the time the handler returns,
    every span it produced is already in :meth:`get_finished_spans`.
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    prior = trace_api._TRACER_PROVIDER  # type: ignore[attr-defined]
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", provider, raising=False)
    yield exporter
    # Restore so other tests see the process default again.
    monkeypatch.setattr(
        trace_api, "_TRACER_PROVIDER", prior, raising=False
    )


def _build_instrumented_langgraph(tracer_provider: TracerProvider):
    """Compile a tiny real LangGraph graph whose node invokes a LangChain Runnable.

    The graph stands in for the Graphia game graph: it has two nodes wired
    in sequence, and the second node invokes a genuine LangChain
    ``RunnableLambda`` — the structural stand-in for a per-turn
    ``ChatBedrockConverse`` model call. LangGraph drives execution and
    LangChain runs the inner Runnable, so both the per-node spans and the
    nested model-call span are produced by the *real* instrumentation that
    ``configure_runtime_observability`` installs — there is no synthetic span
    minted by the test itself.
    """
    from langchain_core.runnables import RunnableLambda
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    # A genuine LangChain Runnable — OpenInference traces ``.invoke`` on it as
    # a nested child of whatever graph node calls it.
    model_stub = RunnableLambda(
        lambda _msg: "the mafia points at Marco", name="bedrock_model_call"
    )

    def night_open(state: dict) -> dict:
        return {"phase": "night"}

    def mafia_pointing(state: dict) -> dict:
        # A real LangChain Runnable invocation inside a graph node — this is
        # the per-turn "model call" the trace tree must show nested under
        # the node span.
        decision = model_stub.invoke("who do the mafia kill?")
        return {"phase": "night", "last_decision": decision}

    builder: StateGraph = StateGraph(dict)
    builder.add_node("night_open", night_open)
    builder.add_node("mafia_pointing", mafia_pointing)
    builder.add_edge(START, "night_open")
    builder.add_edge("night_open", "mafia_pointing")
    builder.add_edge("mafia_pointing", END)
    return builder.compile(checkpointer=InMemorySaver())


# --------------------------------------------------------------------------
# The test — driving the real handler must yield a span *tree*.
# --------------------------------------------------------------------------


async def test_runtime_invocation_produces_a_nested_span_tree(
    env,
    span_exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving ``handler`` yields a navigable span tree, not a flat list.

    Asserts the CR 003 trace-tree contract:

    * The instrumentation produced in-app spans at all (pre-fix: zero).
    * Exactly **one root** span (``parent is None``) for the invocation.
    * All spans share **one trace id** — it is one session's tree.
    * The tree has **depth > 1**: there is a span whose parent is the root
      *and which itself has children* (a non-flat hierarchy).
    * **Not every span is a root** — the explicit negation of a flat list.
    * The game's ``thread_id`` is present on the spans (attribute and/or the
      ``session.id`` the instrumentation derives from baggage).
    """
    import graphia.runtime.__main__ as runtime_main
    from graphia.runtime.observability import (
        SESSION_ID_BAGGAGE_KEY,
        THREAD_ID_FIELD,
        configure_runtime_observability,
    )

    # Production path: install the JSON logging + the LangGraph/LangChain
    # GenAI instrumentor. The CR 003 fix makes this method load the
    # instrumentor; pre-fix it installs only the log handler.
    configure_runtime_observability()

    # The handler compiles the game graph via ``build_runtime_graph``. Swap in
    # a small *real* LangGraph graph so the test stays deterministic and
    # offline while still exercising genuine LangGraph + LangChain execution.
    provider = trace_api.get_tracer_provider()
    monkeypatch.setattr(
        runtime_main,
        "build_runtime_graph",
        lambda *a, **k: _build_instrumented_langgraph(provider),
    )

    THREAD = "20260516T084500"
    events = [
        ev
        async for ev in runtime_main.handler(
            {
                "action": "start",
                "thread_id": THREAD,
                "initial_state": {"phase": "setup"},
            }
        )
    ]

    # The handler streamed the graph to completion.
    assert any(e.get("event") == "done" for e in events), (
        f"handler did not reach a clean 'done'; events were {events!r}"
    )

    spans = span_exporter.get_finished_spans()

    # --- The instrumentation produced in-app spans at all -----------------
    # Pre-fix this list is EMPTY: no instrumentor is loaded and the handler
    # opens no root span, so nothing in-app is ever recorded.
    assert spans, (
        "no OpenTelemetry spans were captured — the Runtime emitted zero "
        "in-app telemetry. This is the flat-trajectory defect: with no "
        "LangGraph/LangChain instrumentor loaded and no invocation root "
        "span, GenAI Observability has nothing to build a trace tree from."
    )

    # --- Exactly one root span for the invocation -------------------------
    roots = [s for s in spans if s.parent is None]
    assert len(roots) == 1, (
        f"expected exactly one root span per invocation; got {len(roots)}. "
        f"Span names: {[s.name for s in spans]}. More than one root (or "
        f"none) means the spans do not nest into a single navigable tree."
    )
    root = roots[0]

    # --- One shared trace id — it is a single session's tree --------------
    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1, (
        f"all spans for one invocation must share one trace id; got "
        f"{len(trace_ids)} distinct ids — the spans belong to disjoint "
        f"traces and cannot render as one session tree."
    )

    # --- Not a flat list: not every span is a root ------------------------
    non_root = [s for s in spans if s.parent is not None]
    assert non_root, (
        "every captured span is a root — that is a FLAT list, not a tree. "
        "A navigable trace tree requires child spans with parent linkage."
    )

    # --- Depth > 1: a child-of-root that itself has children --------------
    # Index spans by id and collect parent->children edges.
    by_id = {s.context.span_id: s for s in spans}
    children: dict[int, list] = {}
    for s in spans:
        if s.parent is not None:
            children.setdefault(s.parent.span_id, []).append(s)

    root_children = children.get(root.context.span_id, [])
    assert root_children, (
        f"the root span {root.name!r} has no children — the tree is one "
        f"level deep at most. Graph-node spans must nest under the "
        f"invocation root."
    )
    # At least one child-of-root must itself have children — that is the
    # encoding of "depth > 1" / a genuinely nested hierarchy (Runtime root
    # -> graph-node -> model call).
    deep = [
        c for c in root_children if children.get(c.context.span_id)
    ]
    assert deep, (
        "no span is both a child of the root AND a parent of further "
        "spans — the captured spans are at most two levels deep and do "
        "not form the Runtime->node->model-call hierarchy CR 003 requires. "
        f"Root children were: {[c.name for c in root_children]}."
    )

    # --- The game's thread_id is discoverable on the spans ----------------
    # The trace half must be filterable by the same game identifier the
    # structured logs carry — as a span attribute and/or the session.id the
    # instrumentation derives from OTEL baggage.
    def _span_carries_thread(s) -> bool:
        attrs = dict(s.attributes or {})
        if attrs.get(THREAD_ID_FIELD) == THREAD:
            return True
        if attrs.get(SESSION_ID_BAGGAGE_KEY) == THREAD:
            return True
        return False

    assert any(_span_carries_thread(s) for s in spans), (
        f"no captured span carries the game's thread_id ({THREAD!r}) as a "
        f"'{THREAD_ID_FIELD}' attribute or a '{SESSION_ID_BAGGAGE_KEY}' "
        f"attribute — the per-session trace tree is not filterable by game. "
        f"Span attributes were: "
        f"{[dict(s.attributes or {}) for s in spans]!r}"
    )

    # Be explicit that the thread_id sits on the *root* — the span the
    # GenAI Observability session view keys the whole tree off.
    assert _span_carries_thread(root), (
        f"the invocation root span {root.name!r} is not stamped with the "
        f"game's thread_id; the session tree cannot be selected by game id."
    )


# --------------------------------------------------------------------------
# Deployment guard — the image must run under the ADOT wrapper.
# --------------------------------------------------------------------------


def test_dockerfile_runs_runtime_under_adot_wrapper() -> None:
    """The Runtime image's CMD must start under ``opentelemetry-instrument``.

    The span-tree test above runs in-process and installs its *own*
    ``TracerProvider`` — so it cannot catch a deployment that has no provider
    at all. Production gets its provider from ADOT: ``bedrock-agentcore``'s
    ``runtime/app.py`` explicitly relies on "ADOT [setting] up the
    TracerProvider before __init__ runs", and a hand-built image supplies
    ADOT only by launching under the ``opentelemetry-instrument`` wrapper.

    Image ``89deed3`` dropped that wrapper; every span then landed on
    OpenTelemetry's no-op default provider and the deployed trajectory went
    flat. This test guards the wrapper so the regression cannot recur
    silently — it is the deployment-shape check the in-process test can't be.
    """
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    cmd_lines = [
        ln.strip() for ln in text.splitlines() if ln.strip().startswith("CMD")
    ]
    assert len(cmd_lines) == 1, (
        f"expected exactly one CMD instruction in the Dockerfile; "
        f"found {len(cmd_lines)}: {cmd_lines!r}"
    )
    assert "opentelemetry-instrument" in cmd_lines[0], (
        "the Runtime image CMD must run under the `opentelemetry-instrument` "
        "ADOT wrapper — the bedrock-agentcore SDK depends on ADOT to set up "
        "the OpenTelemetry TracerProvider; without it the deployed trace tree "
        f"is empty. CMD was: {cmd_lines[0]!r}"
    )