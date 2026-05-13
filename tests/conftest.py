"""Shared fixtures and helpers for Graphia tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import pytest
from rich.text import Text
from textual.widget import Widget

from graphia.llm import Ballot, DayAction, Pointing, Roster


class _LoudFailureLLM:
    """Default LLM stand-in installed by the ``safe_llm`` autouse fixture.

    Any attempt to call through an unstubbed LLM raises ``RuntimeError`` with
    a pointer to the right fixture. Without this safety net a test that forgets
    to stub Sonnet would silently fall through to the real ``ChatBedrockConverse``
    binding, which triggers boto3 retry loops against dummy AWS credentials —
    those retries keep an ``asyncio.to_thread`` worker alive long after
    ``app.exit()`` and block pytest teardown until the 300s executor-join
    timeout fires (the "executor did not finishing joining its threads"
    warning). Failing loudly here surfaces the missing stub immediately.
    """

    def __init__(self, which: str) -> None:
        self._which = which

    def with_structured_output(self, schema: type) -> "_LoudFailureLLM":
        return self

    def invoke(self, messages: Any) -> Any:
        raise RuntimeError(
            f"Unstubbed LLM call through {self._which}. Add the matching "
            "fixture to this test: `fake_haiku(...)` for roster generation, "
            "`fake_sonnet(...)` (unified) for Day/Night. Real Bedrock must "
            "never be reached from the test suite."
        )


@pytest.fixture(autouse=True)
def safe_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse safety net: any unstubbed LLM call raises immediately.

    Patches the three call-site bindings (``get_haiku`` in ``nodes.setup`` and
    ``get_sonnet`` in both ``nodes.night`` and ``nodes.day``) with a
    loud-failure fake. Explicit per-test fixtures (``fake_haiku``,
    ``fake_sonnet``, ``fake_sonnet_pointing``, ``fake_sonnet_day``) run after
    this one and replace these bindings via the same ``monkeypatch`` surface,
    so tests that *do* stub keep working while tests that forgot now fail
    loudly instead of hanging on boto3 retries.
    """
    monkeypatch.setattr(
        "graphia.nodes.setup.get_haiku",
        lambda: _LoudFailureLLM("graphia.nodes.setup.get_haiku"),
    )
    monkeypatch.setattr(
        "graphia.nodes.night.get_sonnet",
        lambda: _LoudFailureLLM("graphia.nodes.night.get_sonnet"),
    )
    monkeypatch.setattr(
        "graphia.nodes.day.get_sonnet",
        lambda: _LoudFailureLLM("graphia.nodes.day.get_sonnet"),
    )


class _LoudFailureMemoryClient:
    """Default AgentCore Memory client stand-in installed by ``safe_memory_client``.

    Any attempt to call through an unstubbed ``MemoryClient`` raises
    ``RuntimeError`` with a pointer to the right test pattern. The
    ``AgentCoreMemoryDiaryStore`` lazily imports ``MemoryClient`` from
    ``graphia.diary_store`` at first ``write``/``read`` — patching the
    import binding at module scope and substituting a loud-failure default
    ensures a test that forgets to install a working fake fails immediately
    instead of falling through to ``boto3.client('bedrock-agentcore')`` and
    triggering an SDK retry loop against dummy credentials.
    """

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401, ANN002, ANN003
        self._args = args
        self._kwargs = kwargs

    def _explode(self, op: str) -> None:
        raise RuntimeError(
            f"Unstubbed AgentCore MemoryClient.{op} call. Tests that exercise "
            "AgentCoreMemoryDiaryStore must install a FakeMemoryClient via "
            "`monkeypatch.setattr('graphia.diary_store.MemoryClient', "
            "FakeMemoryClient)`. Real Bedrock AgentCore Memory must never "
            "be reached from the suite."
        )

    def create_event(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self._explode("create_event")

    def list_events(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self._explode("list_events")


@pytest.fixture(autouse=True)
def safe_memory_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse safety net: unstubbed AgentCore Memory calls raise immediately.

    Mirrors ``safe_llm``'s import-boundary pattern. ``AgentCoreMemoryDiaryStore``
    instantiates ``MemoryClient`` via the import binding at
    ``graphia.diary_store.MemoryClient``; patching that binding to a loud-
    failure default means a test that forgets to install ``FakeMemoryClient``
    fails immediately rather than hanging in boto3 retry loops.

    Tests that *do* want a working fake override this via
    ``monkeypatch.setattr('graphia.diary_store.MemoryClient', FakeMemoryClient)``
    after ``safe_memory_client`` has run.
    """
    # ``AgentCoreMemoryDiaryStore._get_client`` performs a local
    # ``from bedrock_agentcore.memory import MemoryClient`` so the canonical
    # patchable seam is the source attribute itself, not a copy on the
    # diary_store module. Patching ``bedrock_agentcore.memory.MemoryClient``
    # covers every future call site too.
    import bedrock_agentcore.memory as _agentcore_memory

    monkeypatch.setattr(
        _agentcore_memory, "MemoryClient", _LoudFailureMemoryClient
    )


class FakeHaiku:
    """Stand-in for ``ChatBedrockConverse`` used inside ``generate_roster``.

    The real code path is ``get_haiku().with_structured_output(Roster).invoke(msgs)``.
    This fake collapses that to a scripted-outputs queue. Each entry is either a
    ``Roster`` to return or an ``Exception`` to raise (e.g. ``ValidationError``
    to exercise the retry path).

    Attributes:
        call_count: How many times ``.invoke`` was called. Useful for asserting
            "retried exactly once" (i.e. ``call_count == 2``).
    """

    def __init__(self, outputs: Sequence[Roster | Exception]) -> None:
        self._outputs: list[Roster | Exception] = list(outputs)
        self.call_count = 0
        self._bound_schema: type | None = None

    def with_structured_output(self, schema: type) -> "FakeHaiku":
        # Real LangChain returns a new runnable bound to the schema; for the
        # test we just record the schema and return self so subsequent
        # ``.invoke`` calls go through the scripted queue.
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> Roster:
        self.call_count += 1
        if not self._outputs:
            raise AssertionError(
                "FakeHaiku.invoke called more times than scripted outputs"
            )
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def fake_haiku(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeHaiku]:
    """Factory fixture: patch ``graphia.nodes.setup.get_haiku`` with a fake.

    Usage::

        fake = fake_haiku(["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"])
        # ...run the app / node under test...
        assert fake.call_count == 1

    Accepts either a single list of 6 names (converted to a one-shot
    ``Roster``) or an explicit ``outputs=`` sequence mixing ``Roster`` values
    and ``Exception`` instances for retry-path tests.

    Patches the ``get_haiku`` binding **inside** ``graphia.nodes.setup`` (the
    call site) so the already-imported reference is replaced cleanly — patching
    the canonical ``graphia.llm.get_haiku`` would not help, because setup.py
    already bound the original at import time.
    """

    def _install(
        names: Sequence[str] | None = None,
        *,
        outputs: Sequence[Roster | Exception] | None = None,
    ) -> FakeHaiku:
        if outputs is None:
            if names is None:
                raise TypeError("fake_haiku requires either `names` or `outputs`")
            outputs = [Roster(names=list(names))]
        fake = FakeHaiku(outputs)
        monkeypatch.setattr("graphia.nodes.setup.get_haiku", lambda: fake)
        return fake

    return _install


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Provide a clean env.

    - Sets a dummy Bedrock bearer token (avoids SystemExit in load_config).
    - Points the JSONL log at ``tmp_path / graphia.log`` so tests don't touch
      the developer's real log.
    - Points the checkpoint dir at ``tmp_path / checkpoints`` so each test gets
      a fresh SqliteSaver backing store.
    - Ensures GRAPHIA_SEED is unset unless a test opts in.

    Yields the log-file path for tests that want to read emitted events.
    """
    log_file = tmp_path / "graphia.log"
    checkpoint_dir = tmp_path / "checkpoints"
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    monkeypatch.setenv("GRAPHIA_LOG_FILE", str(log_file))
    monkeypatch.setenv("GRAPHIA_CHECKPOINT_DIR", str(checkpoint_dir))
    monkeypatch.delenv("GRAPHIA_SEED", raising=False)
    yield log_file


class FakeSonnet:
    """Stand-in for ``ChatBedrockConverse`` used inside Mafia pointing.

    The production call is
    ``get_sonnet().with_structured_output(Pointing).invoke(msgs)``. This fake
    collapses that into a queue of scripted ``Pointing`` outputs (or
    exceptions to exercise the retry / seeded-fallback path).

    Attributes:
        call_count: Number of times ``.invoke`` was called — useful for
            asserting the retry branch ran exactly once.
    """

    def __init__(self, outputs: Sequence[Pointing | Exception]) -> None:
        self._outputs: list[Pointing | Exception] = list(outputs)
        self.call_count = 0
        self._bound_schema: type | None = None

    def with_structured_output(self, schema: type) -> "FakeSonnet":
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> Pointing:
        self.call_count += 1
        if not self._outputs:
            raise AssertionError(
                "FakeSonnet.invoke called more times than scripted outputs"
            )
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def fake_sonnet_pointing(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeSonnet]:
    """Factory fixture: patch ``graphia.nodes.night.get_sonnet`` with a fake.

    Usage::

        fake = fake_sonnet_pointing(["victim-id", "victim-id"])
        # ... run the app ...
        assert fake.call_count == 2

    Accepts either a list of target ids (one per AI-mafia ``.invoke`` call,
    in call order — each id is wrapped in a ``Pointing``) or an explicit
    ``outputs=`` sequence mixing ``Pointing`` values and ``Exception``
    instances for retry-path tests.

    Patches the ``get_sonnet`` binding **inside** ``graphia.nodes.night`` so
    the already-imported reference is replaced at the call site.
    """

    def _install(
        target_ids: Sequence[str] | None = None,
        *,
        outputs: Sequence[Pointing | Exception] | None = None,
    ) -> FakeSonnet:
        if outputs is None:
            if target_ids is None:
                raise TypeError(
                    "fake_sonnet_pointing requires either `target_ids` or `outputs`"
                )
            outputs = [Pointing(target_id=t) for t in target_ids]
        fake = FakeSonnet(outputs)
        monkeypatch.setattr("graphia.nodes.night.get_sonnet", lambda: fake)
        return fake

    return _install


class FakeSonnetDay:
    """Stand-in for ``ChatBedrockConverse`` used inside Day-phase speaking.

    Production call site is
    ``get_sonnet().with_structured_output(DayAction).invoke(msgs)`` inside
    ``graphia.nodes.day._ai_speak``. This fake collapses that to a scripted
    FIFO queue of ``DayAction`` outputs (or exceptions to exercise the retry
    / deterministic-fallback path).

    Each call to ``.invoke`` pops one output. When the queue empties the fake
    keeps returning the final scripted action (so long-running tests don't
    need to pre-script exactly the right number of turns).
    """

    def __init__(self, outputs: Sequence[DayAction | Exception]) -> None:
        self._outputs: list[DayAction | Exception] = list(outputs)
        self.call_count = 0
        self._bound_schema: type | None = None
        self._last: DayAction | None = None

    def with_structured_output(self, schema: type) -> "FakeSonnetDay":
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> DayAction:
        self.call_count += 1
        if not self._outputs:
            # Gracefully keep serving the last scripted action once the queue
            # is drained — the Day loop will otherwise run far longer than a
            # hand-authored script.
            if self._last is None:
                raise AssertionError(
                    "FakeSonnetDay.invoke called but no scripted outputs "
                    "remain and no prior output to repeat"
                )
            return self._last
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        self._last = out
        return out


@pytest.fixture
def fake_sonnet_day(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeSonnetDay]:
    """Factory fixture: patch ``graphia.nodes.day.get_sonnet`` with a fake.

    Usage::

        fake = fake_sonnet_day([
            DayAction(kind="speak", text="msg-from-AI-1"),
            DayAction(kind="speak", text="msg-from-AI-2"),
        ])
        # ... drive the app ...
        assert fake.call_count >= 1

    Accepts either an explicit sequence of ``DayAction`` / ``Exception`` values
    via ``outputs=``, or a shortcut ``texts=`` parameter that wraps each
    string as ``DayAction(kind="speak", text=...)``.

    Patches the ``get_sonnet`` binding **inside** ``graphia.nodes.day`` so the
    already-imported reference is replaced at the call site.
    """

    def _install(
        outputs: Sequence[DayAction | Exception] | None = None,
        *,
        texts: Sequence[str] | None = None,
    ) -> FakeSonnetDay:
        if outputs is None:
            if texts is None:
                raise TypeError(
                    "fake_sonnet_day requires either `outputs` or `texts`"
                )
            outputs = [DayAction(kind="speak", text=t) for t in texts]
        fake = FakeSonnetDay(outputs)
        monkeypatch.setattr("graphia.nodes.day.get_sonnet", lambda: fake)
        return fake

    return _install


def plain_text(widget: Widget) -> str:
    """Return the rendered plain-text form of a widget (Rich markup stripped)."""
    rendered = widget.render()
    if isinstance(rendered, Text):
        return rendered.plain
    return Text.from_markup(str(rendered)).plain


# --------------------------------------------------------------------------
# Unified Sonnet fake — dispatches on the schema passed to
# ``with_structured_output`` so a single fake can serve DayAction, Ballot,
# and Pointing calls simultaneously. Needed for Slice 7 where ``collect_votes``
# binds ``Ballot`` while ``day_turn`` binds ``DayAction`` on the same
# ``get_sonnet()`` reference.
# --------------------------------------------------------------------------


class _SonnetQueue:
    """Bound view over one of the unified fake's scripted queues.

    Each ``invoke`` call pops the next scripted item for the bound schema.
    When the queue is empty the last popped value is replayed — the exact
    same "keep serving the last output" behaviour the per-schema fakes use
    so long-running tests don't need to pre-script exactly the right number
    of invocations.
    """

    def __init__(self, owner: "FakeSonnetUnified", schema: type) -> None:
        self._owner = owner
        self._schema = schema

    def invoke(self, messages: Any) -> Any:
        return self._owner._invoke(self._schema, messages)


class FakeSonnetUnified:
    """Unified Sonnet fake dispatching on the schema bound at call time.

    Production call shape::

        get_sonnet().with_structured_output(SchemaClass).invoke(msgs)

    This fake keeps a separate scripted queue per schema class so one fixture
    can satisfy ``DayAction`` (speak/vote), ``Ballot`` (yes/no), and
    ``Pointing`` (night target) bindings without interference.

    Attributes:
        call_count: Total invocations across all schemas.
        calls_by_schema: Per-schema invocation counts, keyed by schema class.
    """

    def __init__(
        self,
        *,
        day_actions: Sequence[DayAction | Exception] | None = None,
        ballots: Sequence[Ballot | Exception] | None = None,
        pointings: Sequence[Pointing | Exception] | None = None,
    ) -> None:
        self._queues: dict[type, list[Any]] = {
            DayAction: list(day_actions) if day_actions else [],
            Ballot: list(ballots) if ballots else [],
            Pointing: list(pointings) if pointings else [],
        }
        self._last: dict[type, Any] = {}
        self.call_count = 0
        self.calls_by_schema: dict[type, int] = {
            DayAction: 0,
            Ballot: 0,
            Pointing: 0,
        }

    def with_structured_output(self, schema: type) -> _SonnetQueue:
        if schema not in self._queues:
            raise AssertionError(
                f"FakeSonnet has no scripted queue for schema {schema!r}. "
                "Supported: DayAction, Ballot, Pointing."
            )
        return _SonnetQueue(self, schema)

    def _invoke(self, schema: type, messages: Any) -> Any:
        self.call_count += 1
        self.calls_by_schema[schema] = self.calls_by_schema.get(schema, 0) + 1
        queue = self._queues[schema]
        if not queue:
            last = self._last.get(schema)
            if last is None:
                raise AssertionError(
                    f"FakeSonnet.invoke called for {schema.__name__} but no "
                    "scripted outputs remain and no prior output to repeat."
                )
            return last
        out = queue.pop(0)
        if isinstance(out, Exception):
            raise out
        self._last[schema] = out
        return out


class _DynamicNightPointing:
    """Stateless Night-pointing fake that picks an alive target at call time.

    Production call shape is
    ``get_sonnet().with_structured_output(Pointing).invoke(msgs)``. Between
    a test's ``fake_sonnet(...)`` call and the worker actually reaching the
    ``mafia_pointing`` super-step there is an unavoidable race: the real
    target UUIDs are only known once ``assign_roles`` has run on graph
    state, so tests can't pre-script a specific ``Pointing(target_id=...)``
    without racing the worker.

    This fake dodges the race by deferring target selection to *invoke*
    time — it reads live graph state through the caller-supplied
    ``state_provider`` callable and always returns a ``Pointing`` at the
    first alive Law-abiding non-human player (matching
    ``law_abiding_ids[0]`` in the tests). Every call is independent and
    idempotent: no queue, no replay, no exhaustion.
    """

    def __init__(self, state_provider: Callable[[], dict]) -> None:
        self._state_provider = state_provider
        self.call_count = 0

    def with_structured_output(self, schema: type) -> "_DynamicNightPointing":
        return self

    def invoke(self, messages: Any) -> Pointing:
        self.call_count += 1
        state = self._state_provider()
        players = state.get("players", {})
        candidates = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        if not candidates:
            # Fall back to any alive player — better than raising.
            candidates = [p.id for p in players.values() if p.is_alive]
        return Pointing(target_id=candidates[0])


@pytest.fixture
def dynamic_night_pointing(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., _DynamicNightPointing]:
    """Factory: patch ``graphia.nodes.night.get_sonnet`` with a race-safe fake.

    Usage (after an earlier ``fake_sonnet(...)`` call — this fixture
    overrides the night-side binding installed there)::

        dynamic_night_pointing(lambda: app._graph.get_state(app._run_config).values)

    The returned ``_DynamicNightPointing`` instance exposes ``call_count``
    for tests that want to assert the number of AI-Mafia invocations.
    """

    def _install(
        state_provider: Callable[[], dict],
    ) -> _DynamicNightPointing:
        fake = _DynamicNightPointing(state_provider)
        monkeypatch.setattr("graphia.nodes.night.get_sonnet", lambda: fake)
        return fake

    return _install


class _TargetHumanPointing:
    """Night-pointing fake that always targets the human player.

    Resolves the human's id at invoke time by reading live graph state via
    the caller-supplied ``state_provider`` callable. Used by the Slice 9
    spectator test to script an unambiguous Night-1 kill against the human.
    If the human is already dead (or ``human_id`` is not yet set) the fake
    falls back to the first alive Law-abiding non-human — matching
    ``_DynamicNightPointing`` — so downstream Night super-steps don't crash.
    """

    def __init__(self, state_provider: Callable[[], dict]) -> None:
        self._state_provider = state_provider
        self.call_count = 0

    def with_structured_output(self, schema: type) -> "_TargetHumanPointing":
        return self

    def invoke(self, messages: Any) -> Pointing:
        self.call_count += 1
        state = self._state_provider()
        players = state.get("players", {})
        human_id = state.get("human_id")
        if isinstance(human_id, str) and human_id in players:
            human = players[human_id]
            if getattr(human, "is_alive", False):
                return Pointing(target_id=human_id)
        # Fallback: target the first alive Law-abiding non-human.
        candidates = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        if not candidates:
            candidates = [p.id for p in players.values() if p.is_alive]
        return Pointing(target_id=candidates[0])


@pytest.fixture
def target_human_pointing(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., _TargetHumanPointing]:
    """Factory: patch ``graphia.nodes.night.get_sonnet`` to always target the human.

    Usage::

        target_human_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

    The returned ``_TargetHumanPointing`` instance exposes ``call_count`` for
    tests that want to assert the number of AI-Mafia invocations.
    """

    def _install(
        state_provider: Callable[[], dict],
    ) -> _TargetHumanPointing:
        fake = _TargetHumanPointing(state_provider)
        monkeypatch.setattr("graphia.nodes.night.get_sonnet", lambda: fake)
        return fake

    return _install


@pytest.fixture
def fake_sonnet(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeSonnetUnified]:
    """Factory fixture: unified Sonnet fake patched into BOTH day and night.

    Usage::

        fake = fake_sonnet(
            day_actions=[DayAction(kind="speak", text="hello")],
            ballots=[Ballot(yes=True), Ballot(yes=False)],
            pointings=[Pointing(target_id="p-2")],
        )

    Patches ``graphia.nodes.day.get_sonnet`` AND
    ``graphia.nodes.night.get_sonnet`` with the same instance so calls
    routed through either call site go through one queue-set. This is
    required for Slice 7 tests where a single run touches ``DayAction``
    (speaking), ``Ballot`` (voting), and ``Pointing`` (next night) on the
    same Sonnet binding.
    """

    def _install(
        *,
        day_actions: Sequence[DayAction | Exception] | None = None,
        ballots: Sequence[Ballot | Exception] | None = None,
        pointings: Sequence[Pointing | Exception] | None = None,
    ) -> FakeSonnetUnified:
        fake = FakeSonnetUnified(
            day_actions=day_actions,
            ballots=ballots,
            pointings=pointings,
        )
        monkeypatch.setattr("graphia.nodes.day.get_sonnet", lambda: fake)
        monkeypatch.setattr("graphia.nodes.night.get_sonnet", lambda: fake)
        return fake

    return _install
