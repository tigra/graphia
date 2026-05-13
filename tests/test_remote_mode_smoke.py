"""Slice 4 sub-task 5: end-to-end remote-mode smoke test.

Spec 002's central equivalence claim is that the deployed AgentCore Runtime
behaves *identically* to the in-process compiled graph from the consumer's
point of view — the same ``{node_name: update}`` chunks flow through the
same ``_consume_stream`` loop, the same Textual UI renders them, and the
human-in-the-loop resume hand-off lands the same ``Command(resume=...)``
back on the same checkpoint. Slice 9 covers the full equivalence matrix;
this file pins down the smoke case so an obvious regression is caught at
every save.

Strategy
--------

We construct **one** local ``StateGraph`` per test (the real, in-process
compiled graph) and capture it by wrapping ``graphia.ui.app.build_graph``.
We then monkeypatch ``graphia.driver.AgentCoreClient`` with a tiny
:class:`FakeAgentCoreClient` whose ``.stream(...)`` method *proxies* to
that same graph's ``stream(...)``. This guarantees byte-identical chunks
travel through the consumer path in both modes — the only difference is
that in remote mode the chunks pass through the ``client.stream`` seam,
exercising the boundary's call/parameter contract without ever
instantiating ``boto3``.

The parametrised pair ``[remote, local]`` runs the *same* scripted
scenario through both modes and asserts the public-pane messages and
final winner are identical. This is the equivalence claim spec 002
makes; the parametrisation form keeps the assertion list authoritative
in one place rather than duplicated across two sibling tests.

What's mocked
-------------

* ``ChatBedrockConverse`` — via the autouse ``safe_llm`` fixture and the
  per-test ``fake_haiku`` / ``fake_sonnet`` factories. No real Bedrock.
* ``AgentCoreClient`` — via ``monkeypatch.setattr`` against the
  ``graphia.driver`` import binding (the call site). The fake's
  ``stream`` method records every invocation and forwards to the local
  graph. No ``boto3.client('bedrock-agentcore')`` is ever constructed.
* ``build_graph`` — wrapped at the ``graphia.ui.app`` import binding to
  capture the freshly-built compiled graph so the fake client can borrow
  it. Production code is unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

import pytest
from rich.text import Text
from textual.widgets import Input, RichLog

from graphia.llm import Ballot, DayAction, Pointing
from graphia.prompts import ENDGAME_WINNER_LAW, ENDGAME_WINNER_MAFIA
from graphia.ui.app import GraphiaApp

# Seed 0 puts the human in slot 0 as Law-abiding — the test never has to
# answer a ``kind="point"`` interrupt (no PointingModal). Same seed used by
# the Slice 8 end-screen pilot test, so the trajectory through 2 Mafia AIs
# + 4 Law-abiding AIs + 1 Law-abiding human is well-trodden territory.
SEED_LAW_ABIDING = 0

AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]
HUMAN_NAME = "Alice"

# Synthetic runtime ARN used only to satisfy ``AgentCoreClient`` constructor
# validation; never resolved against a real AWS endpoint because the client
# is replaced by ``FakeAgentCoreClient`` before any network call could happen.
FAKE_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test"
)


# --------------------------------------------------------------------------
# Fake AgentCore client — proxies to the underlying local graph
# --------------------------------------------------------------------------


class FakeAgentCoreClient:
    """Drop-in replacement for ``AgentCoreClient`` in tests.

    Mirrors the real surface: ``__init__(*, runtime_arn, region,
    boto3_client=None)`` plus ``stream(payload, run_config,
    stream_mode='updates') -> Iterator[dict]``.

    Internally, ``stream`` delegates to the local in-process compiled
    graph captured by the test (one per test, via the
    ``_captured_graph`` holder set up by ``_wrap_build_graph``). This
    yields *exactly* the chunk shapes LangGraph would emit locally,
    which is the whole point of spec 002's equivalence claim — the
    consumer-side ``_consume_stream`` loop cannot tell the modes apart.

    Class-level state is reset by the ``_captured_graph`` fixture
    before each test so per-test invocation counters and recorded
    payloads stay clean.
    """

    # Class-level so the test can read counts/payloads without holding a
    # reference to the instance (which is created inside ``drive_graph``).
    instances: list["FakeAgentCoreClient"] = []
    captured_graph: Any = None  # set by the build_graph wrapper

    def __init__(
        self,
        *,
        runtime_arn: str,
        region: str,
        boto3_client: Any | None = None,
    ) -> None:
        if not runtime_arn:
            raise ValueError("runtime_arn is required for AgentCoreClient")
        self.runtime_arn = runtime_arn
        self.region = region
        self.call_count = 0
        self.recorded_payloads: list[Any] = []
        FakeAgentCoreClient.instances.append(self)

    def stream(
        self,
        payload: Any,
        run_config: dict,
        stream_mode: str = "updates",
    ) -> Iterator[dict]:
        if stream_mode != "updates":
            raise NotImplementedError(
                f"FakeAgentCoreClient only handles stream_mode='updates', "
                f"got {stream_mode!r}"
            )
        # Validate the run_config has the shape the real client requires.
        thread_id = (run_config.get("configurable") or {}).get("thread_id")
        if not thread_id:
            raise ValueError(
                "run_config['configurable']['thread_id'] is required"
            )

        self.call_count += 1
        self.recorded_payloads.append(payload)

        graph = FakeAgentCoreClient.captured_graph
        if graph is None:
            raise RuntimeError(
                "FakeAgentCoreClient.captured_graph is unset — the "
                "build_graph wrapper must run before the first stream() call"
            )

        # Bound recursion so a malformed scenario fails fast rather than
        # hanging the worker thread.
        bounded = dict(run_config)
        bounded.setdefault("recursion_limit", 200)
        # Delegate to the same compiled graph the consumer side already
        # holds a reference to. ``payload`` may be a dict, None, or a
        # ``Command(resume=...)`` — all three are accepted by graph.stream.
        yield from graph.stream(payload, bounded, stream_mode="updates")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _public_log_text(app: GraphiaApp) -> str:
    """Flatten the rendered #public-log RichLog into a plain string."""
    public_log = app.query_one("#public-log", RichLog)
    parts: list[str] = []
    for line in public_log.lines:
        text_obj = getattr(line, "text", None)
        if text_obj is None:
            text_obj = str(line)
        if isinstance(text_obj, Text):
            parts.append(text_obj.plain)
        else:
            parts.append(str(text_obj))
    return "\n".join(parts)


@pytest.fixture
def remote_env(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Extend the standard ``env`` fixture with remote-mode env vars.

    ``load_config()`` (called inside ``GraphiaApp.on_mount``) reads
    ``GRAPHIA_REMOTE`` and ``GRAPHIA_RUNTIME_URL`` to populate
    ``remote_mode`` and ``runtime_invocation_url``. Setting them here means
    the in-app config flips to remote without needing to patch
    ``load_config`` directly.
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
    # Slice 6: ``make_diary_store(config)`` (called inside ``build_graph``)
    # raises SystemExit in remote mode if ``GRAPHIA_MEMORY_ID`` is unset.
    # The actual store is replaced with ``InProcessDiaryStore`` by the
    # ``patched_agentcore_client`` fixture's ``_wrapped_build_graph``, so
    # this env var only has to be non-empty to satisfy the factory's guard.
    monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-tests")
    return env


@pytest.fixture
def local_env(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Companion of ``remote_env`` for the equivalence-pair sibling test.

    Explicitly clears the remote-mode env vars so the same scripted
    scenario runs through the in-process graph and we can compare the
    public-pane output against the remote-mode run.
    """
    monkeypatch.delenv("GRAPHIA_REMOTE", raising=False)
    monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
    monkeypatch.delenv("GRAPHIA_MEMORY_ID", raising=False)
    return env


@pytest.fixture
def patched_agentcore_client(
    monkeypatch: pytest.MonkeyPatch,
) -> type[FakeAgentCoreClient]:
    """Replace ``graphia.driver.AgentCoreClient`` with the proxying fake.

    Also wraps ``graphia.ui.app.build_graph`` so the freshly compiled
    graph is captured into ``FakeAgentCoreClient.captured_graph`` —
    this is the reference ``FakeAgentCoreClient.stream`` proxies into.

    Resets the fake's class-level state between tests so per-test
    instance counts / recorded payloads stay clean.
    """
    # Reset class-level state every test.
    FakeAgentCoreClient.instances = []
    FakeAgentCoreClient.captured_graph = None

    import graphia.ui.app as app_module
    from graphia.diary_store import InProcessDiaryStore

    real_build_graph = app_module.build_graph

    def _wrapped_build_graph(config):
        # Force the in-process diary store so the proxied local graph never
        # tries to reach AgentCore Memory via boto3 — even when the env
        # asks for remote mode. Slice 6 sub-task 4 adds the dedicated
        # diary-store equivalence tests; here we just keep this smoke run
        # boto3-free.
        graph, thread_id = real_build_graph(
            config, diary_store=InProcessDiaryStore()
        )
        FakeAgentCoreClient.captured_graph = graph
        return graph, thread_id

    monkeypatch.setattr(app_module, "build_graph", _wrapped_build_graph)
    monkeypatch.setattr("graphia.driver.AgentCoreClient", FakeAgentCoreClient)
    return FakeAgentCoreClient


# --------------------------------------------------------------------------
# The shared scenario runner — drives a full game from boot to "Game over."
# --------------------------------------------------------------------------


async def _run_full_game(
    app: GraphiaApp,
    fake_sonnet_handle: Any,
) -> str:
    """Drive ``app`` from boot through end-of-game, returning the final log.

    The scripted scenario at seed 0 produces a Mafia win via the
    DAY_MAX_ROUNDS no-vote cap on each Day: every Night the AI Mafia
    kills one Law-abiding citizen, every Day AIs speak (never call a
    vote), and after enough Nights the parity check at
    ``check_win_night`` routes to ``end_screen``. The same trajectory
    is used by the Slice 8 pilot test, so the assertions on
    ``Game over.`` and the winner line are well-trodden territory.
    """
    async with app.run_test() as pilot:
        await pilot.pause()

        # Wait for the worker to boot the graph.
        for _ in range(100):
            if app._graph is not None:
                break
            await pilot.pause(0.05)
        assert app._graph is not None, "graph never initialised"

        graph = app._graph
        rc = app._run_config
        assert rc is not None

        # Install a live-state dispatcher for the unified Sonnet fake so
        # Pointing/DayAction targets resolve at invoke time (uuid ids are
        # only known once roles are assigned).
        original_invoke = fake_sonnet_handle._invoke

        def _invoke_live(schema, messages):
            if schema is Pointing:
                state = graph.get_state(rc).values
                law_ids = [
                    p.id
                    for p in state.get("players", {}).values()
                    if p.is_alive and p.role == "law_abiding" and not p.is_human
                ]
                if not law_ids:
                    return Pointing(target_id="missing")
                return Pointing(target_id=law_ids[0])
            if schema is DayAction:
                return DayAction(kind="speak", text="I'm watching carefully.")
            if schema is Ballot:
                return Ballot(yes=False)
            return original_invoke(schema, messages)

        fake_sonnet_handle._invoke = _invoke_live  # type: ignore[method-assign]

        # Enter the human name. Wait for the input to enable first.
        for _ in range(100):
            try:
                prompt = app.query_one("#player-input", Input)
            except Exception:  # noqa: BLE001
                prompt = None  # type: ignore[assignment]
            if prompt is not None and prompt.disabled is False:
                break
            await pilot.pause(0.05)

        await pilot.press(*HUMAN_NAME)
        await pilot.press("enter")

        # Each Day asks the human to speak six times across two Days. Submit
        # ``.`` (becomes ``…`` in the graph) for every prompt and poll the
        # public log for "Game over." between presses.
        for _ in range(80):
            text = _public_log_text(app)
            if "Game over." in text:
                break
            try:
                prompt = app.query_one("#player-input", Input)
            except Exception:  # noqa: BLE001
                prompt = None  # type: ignore[assignment]
            if prompt is not None and prompt.disabled is False:
                await pilot.press(".")
                await pilot.press("enter")
            else:
                await pilot.pause(0.2)

        # Final poll with a longer-grained interval for the very last
        # super-step batch (end_screen + "Game over." banner).
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            if "Game over." in _public_log_text(app):
                break
            await pilot.pause(0.1)

        rendered = _public_log_text(app)
        if "Game over." not in rendered:
            app.exit()
            raise AssertionError(
                "'Game over.' never appeared in #public-log. Log was:\n"
                + rendered
            )

        # Press any key to exit the post-game screen.
        await pilot.press("x")

    assert app.is_running is False
    return rendered


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


async def test_remote_mode_drives_full_game_through_fake_agentcore_client(
    remote_env: Path,
    fake_haiku,
    fake_sonnet,
    patched_agentcore_client: type[FakeAgentCoreClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke test: a full game runs end-to-end in remote mode without boto3.

    Asserts that (a) the game reaches a decisive ending and renders the
    expected winner line in the public pane, (b) the fake AgentCore
    client was instantiated exactly once per session, (c) ``stream(...)``
    was invoked at least once for the start payload plus one resume per
    interrupt, and (d) the first recorded payload was the initial state
    dict (start) and a later payload was a ``Command`` (resume).
    """
    from langgraph.types import Command

    monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    app = GraphiaApp()
    rendered = await _run_full_game(app, fake)

    # --- Winner + post-game banner ---------------------------------
    assert (
        ENDGAME_WINNER_LAW in rendered or ENDGAME_WINNER_MAFIA in rendered
    ), f"no winner line in public log; got:\n{rendered}"
    assert "Game over." in rendered
    assert app._game_over is True

    # --- AgentCore client surface was exercised --------------------
    assert (
        len(patched_agentcore_client.instances) == 1
    ), "exactly one FakeAgentCoreClient should be constructed per drive_graph session"
    client = patched_agentcore_client.instances[0]
    assert client.runtime_arn == FAKE_RUNTIME_ARN
    assert client.region == "us-east-1"

    # At minimum: one start + one resume after the name interrupt. In
    # practice the seed-0 trajectory drives many more (one per human-
    # facing interrupt across two Days + multiple Nights), so we
    # assert a generous lower bound rather than an exact count.
    assert client.call_count >= 2, (
        f"expected at least 2 stream() calls (start + 1 resume); "
        f"got {client.call_count}"
    )

    # Start payload: initial state dict {"messages": []} from the app
    # worker (or None as the post-interrupt-less-pause continuation,
    # which the driver dispatches between super-step boundaries).
    first_payload = client.recorded_payloads[0]
    assert isinstance(first_payload, dict), (
        f"first stream() payload should be the initial state dict; "
        f"got {type(first_payload).__name__}: {first_payload!r}"
    )

    # At least one resume after a human-facing interrupt arrived as
    # a Command(resume=...).
    resume_payloads = [
        p for p in client.recorded_payloads if isinstance(p, Command)
    ]
    assert resume_payloads, (
        f"expected at least one Command(resume=...) payload; "
        f"got {client.recorded_payloads!r}"
    )
    # The first resume must carry the human's name (the very first
    # interrupt the graph emits is ``kind="name"``).
    first_resume_values = [p.resume for p in resume_payloads]
    assert HUMAN_NAME in first_resume_values, (
        f"expected {HUMAN_NAME!r} to appear among resume values; "
        f"got {first_resume_values!r}"
    )


@pytest.mark.parametrize("mode", ["local", "remote"])
async def test_local_and_remote_render_equivalent_winner(
    request: pytest.FixtureRequest,
    mode: str,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equivalence claim: same scenario, same winner line in both modes.

    Spec 002 §2 promises the deployed Runtime behaves identically to the
    in-process graph from the consumer's view. Slice 9 will expand this
    to a full equivalence matrix; here we just pin down the smoke pair
    so an obvious divergence (different winner, missing "Game over.",
    crash in one mode but not the other) fails fast.

    The parametrisation keeps the assertion list authoritative in one
    place — adding a new assertion below covers both modes automatically.
    """
    # Resolve the right ``env`` fixture variant for this parameter.
    if mode == "remote":
        request.getfixturevalue("remote_env")
        request.getfixturevalue("patched_agentcore_client")
    else:
        request.getfixturevalue("local_env")

    monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    app = GraphiaApp()
    rendered = await _run_full_game(app, fake)

    # Same winner line lands in both modes (seed-0 trajectory → Mafia win).
    assert ENDGAME_WINNER_MAFIA in rendered, (
        f"[{mode}] expected Mafia win at seed 0; got:\n{rendered}"
    )
    assert "Game over." in rendered, (
        f"[{mode}] missing 'Game over.' banner; got:\n{rendered}"
    )
    assert app._game_over is True

    # Final public-log mentions every player by name (full roster reveal).
    for name in AI_NAMES + [HUMAN_NAME]:
        assert name in rendered, (
            f"[{mode}] roster reveal missing {name!r} in log:\n{rendered}"
        )


# --------------------------------------------------------------------------
# Unit-level invariants on the real AgentCoreClient (boto3 contract)
# --------------------------------------------------------------------------


def test_agentcore_client_uses_stable_session_id_across_calls() -> None:
    """Start + every resume on the same thread share one ``runtimeSessionId``.

    AgentCore routes invocations by session id to the microVM that owns
    ``/tmp/graphia/checkpoints/<thread_id>.sqlite``. If consecutive calls
    receive different session ids, the resume lands on a fresh microVM with
    an empty checkpoint dir and the server starts a brand-new graph —
    producing an infinite "enter the name" loop because the human resume
    never reaches the original interrupt. This pins that the client caches
    the padded session id per ``thread_id``.
    """
    from graphia.agentcore_client import AgentCoreClient
    from langgraph.types import Command

    captured_args: list[dict] = []

    class _FakeBoto3:
        def invoke_agent_runtime(self, **kwargs):
            captured_args.append(kwargs)
            # Minimal response that ``_translate_sse`` can consume cleanly.
            return {"statusCode": 200, "response": iter([])}

    client = AgentCoreClient(
        runtime_arn=FAKE_RUNTIME_ARN,
        region="us-east-1",
        boto3_client=_FakeBoto3(),
    )
    thread_id = "20260513T100744"  # the real format Graphia generates: 15 chars
    rc = {"configurable": {"thread_id": thread_id}}

    # Drain three calls: a start, then two resumes. All must use the same id.
    list(client.stream({}, rc))
    list(client.stream(Command(resume="Alice"), rc))
    list(client.stream(Command(resume="."), rc))

    assert len(captured_args) == 3
    session_ids = {call["runtimeSessionId"] for call in captured_args}
    assert len(session_ids) == 1, (
        f"expected one stable runtimeSessionId across all calls; "
        f"got {session_ids!r}"
    )
    sole = session_ids.pop()
    assert sole.startswith(thread_id + "-"), (
        f"session id should preserve the thread_id prefix; got {sole!r}"
    )
    assert len(sole) >= 33, (
        f"AgentCore requires runtimeSessionId >= 33 chars; got len={len(sole)}"
    )


# --------------------------------------------------------------------------
# Regression: driver detects interrupts from the stream in remote mode
# --------------------------------------------------------------------------
#
# Background: the previous ``drive_graph`` implementation called
# ``graph.get_state(run_config)`` after every super-step to find pending
# interrupts. In remote mode the **local** ``graph`` instance never runs
# anything — the work happens on the server's compiled graph (different
# process, different ``SqliteSaver``). ``get_state`` on the local instance
# returns empty state, ``interrupts=[]``, the early-exit ``if not
# next_nodes: return`` trips, and the driver silently completes without
# ever calling ``request_resume``. Symptom in the smoke trace: the name
# modal never opens.
#
# The full-game smoke tests above don't catch this because their
# ``FakeAgentCoreClient.stream`` proxies into the real local graph, which
# DOES populate the local SqliteSaver — hiding the architectural
# divergence. This regression test instead emits hand-crafted chunks
# without touching any local graph state.
#
# What this pins down: "in remote mode, ``drive_graph`` does not depend on
# local graph state introspection to detect interrupts."


class ScriptedAgentCoreClient:
    """Hand-crafted chunk emitter for the regression test.

    Unlike ``FakeAgentCoreClient`` above (which proxies into a real local
    graph), this fake yields a scripted sequence with no reliance on
    LangGraph runtime state. ``stream`` is invoked once at session start
    and once per resume; each invocation pulls the next script and yields
    its chunks verbatim, exactly as ``AgentCoreClient.stream`` would.
    """

    def __init__(self, scripts: list[list[dict]]) -> None:
        # Each entry is a chunk sequence for one ``stream`` call.
        self._scripts: list[list[dict]] = list(scripts)
        self.call_count = 0
        self.recorded_payloads: list[Any] = []

    def __call__(
        self,
        *,
        runtime_arn: str,
        region: str,
        boto3_client: Any | None = None,
    ) -> "ScriptedAgentCoreClient":
        # Mimic ``AgentCoreClient`` constructor signature so the driver's
        # ``client = AgentCoreClient(...)`` call site lands here. Returns
        # self so all stream() calls share state with the test fixture.
        if boto3_client is not None:
            raise AssertionError(
                "ScriptedAgentCoreClient must never receive a boto3 client"
            )
        if not runtime_arn:
            raise ValueError("runtime_arn is required")
        self.runtime_arn = runtime_arn
        self.region = region
        return self

    def stream(
        self,
        payload: Any,
        run_config: dict,
        stream_mode: str = "updates",
    ) -> Iterator[dict]:
        if stream_mode != "updates":
            raise NotImplementedError(
                f"ScriptedAgentCoreClient only supports stream_mode='updates'"
            )
        self.call_count += 1
        self.recorded_payloads.append(payload)
        if not self._scripts:
            raise AssertionError(
                f"stream() called {self.call_count} time(s) but no script left"
            )
        yield from self._scripts.pop(0)


async def test_remote_mode_driver_detects_interrupt_from_stream_not_local_state(
    remote_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression: remote-mode interrupt detection comes from the stream chunk.

    Pins down "``drive_graph`` does not consult ``graph.get_state`` for
    interrupt detection when ``client is not None``". Constructs a minimal
    real ``CompiledStateGraph`` (required by the function signature) but
    never runs it — its local state stays empty for ``thread_id``. A
    :class:`ScriptedAgentCoreClient` then emits a single ``__interrupt__``
    chunk on the first call and an empty (end-of-stream) second call. The
    driver must:

    * see the interrupt without inspecting local graph state,
    * invoke ``request_resume`` with the interrupt's ``value``,
    * re-invoke ``client.stream`` with a ``Command(resume=...)``,
    * return cleanly after the second stream ends, and
    * never instantiate ``boto3``.

    Before the driver fix this test fails: the driver returns at the
    first early-exit ``if not next_nodes: return`` and ``request_resume``
    is never called.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, Interrupt

    from graphia.config import GraphiaConfig
    from graphia.driver import drive_graph
    from graphia.logging import StreamTraceLogger

    # --- Boto3 trip-wire: instantiating one fails the test ---------
    import boto3 as _boto3

    def _explode_boto3_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "boto3.client() must not be called in this test "
            f"(args={args!r}, kwargs={kwargs!r})"
        )

    monkeypatch.setattr(_boto3, "client", _explode_boto3_client)

    # --- Minimal real CompiledStateGraph (never executed) -----------
    # ``drive_graph`` requires a CompiledStateGraph instance for the
    # local-mode snapshot calls inside ``_consume_stream``. We give it
    # one but the scripted client never delegates to it.
    class _S(dict):
        pass

    def _noop(state: dict) -> dict:
        return {}

    builder: StateGraph = StateGraph(dict)
    builder.add_node("noop", _noop)
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    # ``_consume_stream`` calls ``graph.get_state(run_config)`` on every
    # non-interrupt chunk to log cycle/phase — that needs a checkpointer
    # even though the graph is never executed. The saver is purely a
    # bookkeeping requirement; its state stays empty for the duration of
    # this test.
    graph = builder.compile(checkpointer=InMemorySaver())

    thread_id = "regression-test-thread"
    run_config = {"configurable": {"thread_id": thread_id}}

    interrupt_value = {"kind": "name", "prompt": "Enter your name"}
    interrupt_obj = Interrupt(value=interrupt_value, id="i-1")

    # First stream: emit one state-update chunk, then one interrupt
    # chunk. Second stream (resume): one state-update chunk, no
    # interrupt -> graph reached END.
    scripted = ScriptedAgentCoreClient(
        scripts=[
            [
                {"setup_node": {"phase": "setup"}},
                {"__interrupt__": (interrupt_obj,)},
            ],
            [
                {"post_resume_node": {"phase": "after_resume"}},
            ],
        ]
    )
    monkeypatch.setattr("graphia.driver.AgentCoreClient", scripted)

    # --- HITL plumbing ---------------------------------------------
    resume_calls: list[dict] = []
    resume_value = "Alice"

    async def _request_resume(payload: dict) -> Any:
        resume_calls.append(payload)
        return resume_value

    messages_seen: list[Any] = []

    async def _on_message(msg: Any) -> None:
        messages_seen.append(msg)

    # --- Logger backed by tmp_path (env fixture already pointed it
    # here; we just need an instance) ------------------------------
    logger = StreamTraceLogger(tmp_path / "regression.log")

    # --- Config: remote mode on ------------------------------------
    config = GraphiaConfig(
        bearer_token=None,
        aws_region="us-east-1",
        log_file=tmp_path / "regression.log",
        seed=0,
        checkpoint_dir=tmp_path / "checkpoints",
        remote_mode=True,
        runtime_invocation_url=FAKE_RUNTIME_ARN,
        memory_id=None,
    )

    # --- Drive --------------------------------------------------------
    await drive_graph(
        graph=graph,
        run_config=run_config,
        initial={"messages": []},
        logger=logger,
        on_message=_on_message,
        request_resume=_request_resume,
        config=config,
    )

    # --- Assertions ---------------------------------------------------
    # The interrupt detector found the chunk-borne Interrupt and routed
    # to request_resume exactly once.
    assert len(resume_calls) == 1, (
        f"expected exactly one request_resume call; got {len(resume_calls)}: "
        f"{resume_calls!r}"
    )
    assert resume_calls[0] == interrupt_value, (
        f"request_resume should receive the interrupt's value verbatim; "
        f"got {resume_calls[0]!r}"
    )

    # Two stream() invocations: start + one resume.
    assert scripted.call_count == 2, (
        f"expected 2 stream() invocations (start + 1 resume); "
        f"got {scripted.call_count}"
    )
    assert isinstance(scripted.recorded_payloads[0], dict), (
        f"first payload should be initial state dict; "
        f"got {type(scripted.recorded_payloads[0]).__name__}"
    )
    assert isinstance(scripted.recorded_payloads[1], Command), (
        f"second payload should be Command(resume=...); "
        f"got {type(scripted.recorded_payloads[1]).__name__}"
    )
    assert scripted.recorded_payloads[1].resume == resume_value, (
        f"second payload should carry the test's resume value; "
        f"got {scripted.recorded_payloads[1].resume!r}"
    )


# --------------------------------------------------------------------------
# Wire-format: BaseMessage objects round-trip without becoming repr strings
# --------------------------------------------------------------------------
#
# Background: pre-fix, ``_serialise_chunk`` ran
# ``json.loads(json.dumps(update, default=str))``. The ``default=str`` hook
# stringified every ``BaseMessage`` into its ``repr`` ("HumanMessage(content=
# 'Alice', additional_kwargs={...}, ...)"), so the UI received plain strings
# and crashed on ``msg.content``:
#
#     AttributeError: 'str' object has no attribute 'content'
#
# Post-fix the server uses ``langchain_core.load.dumpd`` (tagged
# constructor markers) and the client reconstructs via ``load`` so the UI
# gets back genuine ``BaseMessage`` subclasses.
#
# These two tests pin that wire format. They fail on a pre-fix revert
# because both assert ``isinstance(msg, BaseMessage)`` and ``msg.content``
# equality — neither of which holds against a stringified repr.


def test_serialise_and_translate_round_trip_yields_real_basemessages() -> None:
    """Unit test: chunk with HumanMessage + AIMessage survives the wire.

    Drives ``_serialise_chunk`` (server side) followed by a JSON
    encode/decode (the SSE frame boundary) followed by
    ``AgentCoreClient._translate_event`` (client side). Asserts that the
    resulting ``messages`` list contains genuine ``BaseMessage`` instances
    whose ``.content`` matches the originals, and that scalar fields pass
    through unchanged.

    On a pre-fix revert (``json.dumps(default=str)``), messages arrive as
    ``str`` instances — ``isinstance(msg, HumanMessage)`` fails and the
    test crashes on the same ``AttributeError`` the user hit in the smoke.
    """
    import json as _json

    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

    from graphia.agentcore_client import AgentCoreClient
    from graphia.runtime.__main__ import _serialise_chunk

    chunk = {
        "collect_name": {
            "messages": [
                HumanMessage(content="Alice"),
                AIMessage(content="Welcome, Alice."),
            ],
            "human_id": "h-123",
            "cycle": 1,
        }
    }

    # 1) Server-side serialisation.
    events = _serialise_chunk(chunk)
    assert len(events) == 1, f"expected one event, got {events!r}"
    assert events[0]["node"] == "collect_name"

    # 2) JSON wire round-trip — this is exactly what the SSE frame does.
    wire_payload = _json.dumps(events[0])
    parsed = _json.loads(wire_payload)

    # 3) Client-side translation.
    translated = AgentCoreClient._translate_event(parsed)
    assert translated is not None
    assert "collect_name" in translated
    update = translated["collect_name"]

    # Scalars unchanged.
    assert update["human_id"] == "h-123"
    assert update["cycle"] == 1

    # Messages reconstructed as real BaseMessage instances — NOT strings.
    msgs = update["messages"]
    assert isinstance(msgs, list) and len(msgs) == 2
    for msg in msgs:
        assert isinstance(msg, BaseMessage), (
            f"wire-decoded message should be a BaseMessage, got "
            f"{type(msg).__name__}: {msg!r}"
        )
    assert isinstance(msgs[0], HumanMessage)
    assert msgs[0].content == "Alice"
    assert isinstance(msgs[1], AIMessage)
    assert msgs[1].content == "Welcome, Alice."


def test_serialise_chunk_skips_bare_interrupt_marker() -> None:
    """The bare ``__interrupt__`` super-step marker is noise — drop it.

    LangGraph emits a ``{'__interrupt__': (Interrupt(...),)}`` chunk on the
    super-step where a node calls ``interrupt()``. The Runtime entrypoint
    already emits a canonical trailing ``{"event": "interrupt", ...}``
    envelope after the stream loop, so the bare marker would only
    duplicate the signal. This pins the dup-fix in ``_serialise_chunk``.
    """
    from graphia.runtime.__main__ import _serialise_chunk

    # Pure __interrupt__ super-step: no events emitted.
    assert _serialise_chunk({"__interrupt__": ("opaque-marker",)}) == []

    # Mixed super-step: the real node still passes through.
    mixed = _serialise_chunk(
        {
            "noop": {"phase": "x"},
            "__interrupt__": ("opaque-marker",),
        }
    )
    assert len(mixed) == 1
    assert mixed[0] == {"node": "noop", "update": {"phase": "x"}}


async def test_remote_mode_consumer_receives_basemessage_from_scripted_sse(
    remote_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end (mocked SSE): driver hands BaseMessage to ``on_message``.

    Builds a scripted client that emits a chunk whose ``messages`` list is
    the **dumpd-encoded** form of a ``HumanMessage`` — exactly what the
    real Runtime now puts on the wire. Drives the full ``_consume_stream``
    loop through ``drive_graph`` and asserts the ``on_message`` callback
    received a genuine ``BaseMessage`` instance with the right
    ``.content``, not a ``repr`` string.

    On a pre-fix revert (server emits ``str(msg)`` via ``default=str``,
    client passes the string through unchanged), the assertion
    ``isinstance(received, BaseMessage)`` fails.
    """
    from langchain_core.load import dumpd
    from langchain_core.messages import BaseMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    from graphia.config import GraphiaConfig
    from graphia.driver import drive_graph
    from graphia.logging import StreamTraceLogger

    # Boto3 trip-wire — the scripted client must not touch real AWS.
    import boto3 as _boto3

    def _explode_boto3_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            f"boto3.client() must not be called (args={args!r}, kwargs={kwargs!r})"
        )

    monkeypatch.setattr(_boto3, "client", _explode_boto3_client)

    # Minimal compiled graph — never executed (the scripted client owns
    # the stream), but required by ``drive_graph``'s signature.
    def _noop(state: dict) -> dict:
        return {}

    builder: StateGraph = StateGraph(dict)
    builder.add_node("noop", _noop)
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    # Build the chunk the real Runtime would now emit: messages dumped
    # via ``dumpd`` (tagged constructor markers), JSON-clean.
    original = HumanMessage(content="Wire reconstruction works.")
    wire_update = dumpd(
        {
            "messages": [original],
            "phase": "post-name",
        }
    )

    scripted = ScriptedAgentCoreClient(
        scripts=[
            [
                # The chunk shape the client translates: same as a server
                # event minus the SSE envelope (``_translate_event`` runs
                # on the parsed event dict).
                {"collect_name": wire_update},
            ],
        ]
    )

    # The scripted client publishes through the same translation surface
    # the production client uses; the easiest way to exercise the *real*
    # ``_translate_event`` here is to route through ``AgentCoreClient``'s
    # static helper inside the script-yielding stream loop. Wire the
    # scripted client to run each yielded chunk through the production
    # translator, mimicking what the SSE-parsing layer does for boto3
    # responses.
    from graphia.agentcore_client import AgentCoreClient

    raw_stream = scripted.stream

    def _translating_stream(
        payload: Any, run_config: dict, stream_mode: str = "updates"
    ) -> Iterator[dict]:
        for chunk in raw_stream(payload, run_config, stream_mode=stream_mode):
            # Each scripted chunk is shaped like a server event
            # ``{node_name: update}``. ``_translate_event`` accepts the
            # canonical ``{"node": N, "update": U}`` envelope, so adapt.
            for node_name, update in chunk.items():
                event = {"node": node_name, "update": update}
                translated = AgentCoreClient._translate_event(event)
                if translated is not None:
                    yield translated

    scripted.stream = _translating_stream  # type: ignore[method-assign]
    monkeypatch.setattr("graphia.driver.AgentCoreClient", scripted)

    # HITL: the scripted stream yields no interrupt, so this should never
    # be called; we still install it so signature checks pass.
    async def _request_resume(payload: dict) -> Any:
        raise AssertionError(
            f"request_resume must not be invoked in this scripted run; "
            f"got payload={payload!r}"
        )

    messages_seen: list[Any] = []

    async def _on_message(msg: Any) -> None:
        messages_seen.append(msg)

    logger = StreamTraceLogger(tmp_path / "wire-format.log")
    config = GraphiaConfig(
        bearer_token=None,
        aws_region="us-east-1",
        log_file=tmp_path / "wire-format.log",
        seed=0,
        checkpoint_dir=tmp_path / "checkpoints",
        remote_mode=True,
        runtime_invocation_url=FAKE_RUNTIME_ARN,
        memory_id=None,
    )

    await drive_graph(
        graph=graph,
        run_config={"configurable": {"thread_id": "wire-format-test"}},
        initial={"messages": []},
        logger=logger,
        on_message=_on_message,
        request_resume=_request_resume,
        config=config,
    )

    # The driver must surface the wire-encoded HumanMessage as a real
    # BaseMessage to ``on_message`` — NOT as a ``repr`` string. This is
    # the assertion that fails before the fix and passes after it.
    assert messages_seen, "expected at least one message delivered to on_message"
    received = messages_seen[0]
    assert isinstance(received, BaseMessage), (
        f"on_message received {type(received).__name__}: {received!r} -- "
        f"expected a BaseMessage subclass. This is the exact regression "
        f"the wire-format fix prevents."
    )
    assert isinstance(received, HumanMessage)
    assert received.content == "Wire reconstruction works."
