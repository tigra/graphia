"""Shared fixtures and helpers for Graphia tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Input

from graphia.config import GraphiaConfig, load_config
from graphia.llm import (
    Ballot,
    DayAction,
    Diary,
    Persona,
    Pointing,
    Reflection,
    Roster,
)
from graphia.nodes.day import DAY_MAX_ROUNDS, DAY_MAX_VOTES
from graphia.nodes.setup import ai_name_count


def _fake_embed_documents(texts: list[str]) -> list[list[float]]:
    """Deterministic stand-in for ``BedrockEmbeddings.embed_documents`` (spec 033).

    Maps each text to a fixed-length (26-dim) lowercase-letter frequency vector —
    a stable bag-of-characters, no model, no network. Identical texts yield
    identical vectors (so the semantic scorer's cosine of a duplicated persona is
    ≈ 1.0), differently-worded texts yield different vectors, and the value is
    fully reproducible across runs. This is what the autouse ``safe_llm`` fixture
    installs at the ``graphia.tools.blunder_eval.get_embeddings`` call site, so a
    flag-on ``run_eval`` integration test gets a deterministic ``persona_sem_mean``
    and NEVER reaches real Bedrock embeddings.
    """
    vectors: list[list[float]] = []
    for text in texts:
        counts = [0.0] * 26
        for ch in text.lower():
            index = ord(ch) - ord("a")
            if 0 <= index < 26:
                counts[index] += 1.0
        vectors.append(counts)
    return vectors


class _FakeEmbeddings:
    """Minimal stand-in for ``langchain_aws.BedrockEmbeddings`` (spec 033).

    ``run_eval`` resolves the embed callable via
    ``get_embeddings().embed_documents``, so the fake ``get_embeddings`` the
    ``safe_llm`` fixture installs returns this object, whose ``embed_documents``
    is the deterministic :func:`_fake_embed_documents`. No constructor args are
    read — the real factory takes none from the caller either.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _fake_embed_documents(texts)


class _LoudFailureLLM:
    """Default LLM stand-in installed by the ``safe_llm`` autouse fixture.

    Any attempt to call through an unstubbed LLM raises ``RuntimeError`` with
    a pointer to the right fixture. Without this safety net a test that forgets
    to stub the large model would silently fall through to the real ``ChatBedrockConverse``
    binding, which triggers boto3 retry loops against dummy AWS credentials —
    those retries keep an ``asyncio.to_thread`` worker alive long after
    ``app.exit()`` and block pytest teardown until the 300s executor-join
    timeout fires (the "executor did not finishing joining its threads"
    warning). Failing loudly here surfaces the missing stub immediately.
    """

    def __init__(self, which: str) -> None:
        self._which = which

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "_LoudFailureLLM":
        return self

    def invoke(self, messages: Any) -> Any:
        raise RuntimeError(
            f"Unstubbed LLM call through {self._which}. Add the matching "
            "fixture to this test: `fake_small(...)` for roster generation, "
            "`fake_large(...)` (unified) for Day/Night. Real Bedrock must "
            "never be reached from the test suite."
        )


@pytest.fixture(autouse=True)
def drain_driver_producers() -> Iterator[None]:
    """Autouse teardown: join any in-flight driver producer thread before next test.

    The Textual driver (``graphia.driver``) runs each graph super-step in a
    worker thread via ``asyncio.to_thread`` (``_producer``). On a USER-cancelled
    exit (``app.exit()`` / Esc / Ctrl+C) the driver deliberately cancels the
    producer's asyncio-task wrapper WITHOUT awaiting the underlying thread — the
    real-app concern being a thread parked in a slow Bedrock call. The thread
    cannot be killed from Python, so it keeps running in the background.

    In the mocked test suite that thread finishes within milliseconds, but if it
    finishes AFTER the next test has set up its own RNG-dependent trajectory
    (the role deal, day-speech order, tie-breaks all draw from the module-global
    ``random``), the leaked producer corrupts that trajectory — the cross-test
    flakiness behind ``test_multi_round_consensus``. This fixture closes the
    leak generically (protecting every off-convention RNG-pinning test, e.g.
    ``test_configurable_lineup`` and ``test_dual_mode_smoke``) by joining each
    producer's real completion signal at teardown — a point with no pending
    asyncio cancellation, so the join genuinely blocks (unlike a drain attempted
    inside the driver's own cancellation ``finally``). The wait is purely
    threading-based and never touches an event loop, so it is safe from sync
    fixture teardown regardless of the test loop's state. Bounded so a genuinely
    stuck thread cannot hang the suite.
    """
    import graphia.driver as _driver

    yield
    _driver.wait_for_producers_quiescent(timeout=10.0)


@pytest.fixture(autouse=True)
def safe_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse safety net: any unstubbed LLM call raises immediately.

    Patches the call-site bindings (``get_small`` and ``get_large`` in
    ``nodes.setup``, and ``get_large`` in both ``nodes.night`` and
    ``nodes.day``) with a loud-failure fake. Explicit per-test fixtures
    (``fake_small``,
    ``fake_large``, ``fake_large_pointing``, ``fake_large_day``) run after
    this one and replace these bindings via the same ``monkeypatch`` surface,
    so tests that *do* stub keep working while tests that forgot now fail
    loudly instead of hanging on boto3 retries.
    """
    monkeypatch.setattr(
        "graphia.nodes.setup.get_small",
        lambda: _LoudFailureLLM("graphia.nodes.setup.get_small"),
    )
    # Spec 016: ``generate_personas`` adds a ``get_large`` call site in
    # ``nodes.setup``. Net it too — the node's broad-exception fallback turns
    # this loud failure into a deterministic fallback persona, so tests that
    # don't install a persona fake stay green (and never reach real Bedrock).
    monkeypatch.setattr(
        "graphia.nodes.setup.get_large",
        lambda: _LoudFailureLLM("graphia.nodes.setup.get_large"),
    )
    # Spec 034: diversified persona generation builds a higher-temperature persona
    # model via ``get_persona_model(temperature)`` in ``nodes.setup`` (default-on
    # flag). Net it too — the persona node's broad-exception fallback turns this
    # loud failure into a deterministic fallback persona, so tests that don't
    # install a persona fake stay green (and never reach real Bedrock). The
    # ``temperature`` arg is accepted and ignored by the loud-failure default.
    monkeypatch.setattr(
        "graphia.nodes.setup.get_persona_model",
        lambda temperature: _LoudFailureLLM("graphia.nodes.setup.get_persona_model"),
    )
    monkeypatch.setattr(
        "graphia.nodes.night.get_large",
        lambda: _LoudFailureLLM("graphia.nodes.night.get_large"),
    )
    monkeypatch.setattr(
        "graphia.nodes.day.get_large",
        lambda: _LoudFailureLLM("graphia.nodes.day.get_large"),
    )
    # Spec 033: ``blunder_eval.run_eval`` resolves the semantic-persona embeddings
    # client via the module-level ``get_embeddings`` binding. Patch it here with a
    # deterministic fake (a stable char-frequency embedder) so the offline suite
    # never reaches real Bedrock embeddings and a flag-on eval gets a reproducible
    # ``persona_sem_mean``. A per-test fixture may re-patch this same binding to
    # drive a specific cosine outcome (it runs after this autouse fixture).
    monkeypatch.setattr(
        "graphia.tools.blunder_eval.get_embeddings",
        lambda: _FakeEmbeddings(),
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


class _LoudFailureStreamableHTTPClient:
    """Default stand-in for ``mcp.client.streamable_http.streamablehttp_client``.

    Mirrors the ``safe_llm`` / ``safe_memory_client`` pattern at the
    third-party import boundary. Without this safety net, a test that
    accidentally exercises :class:`graphia.diary_store.GatewayMCPDiaryStore`
    would fall through to the real ``streamablehttp_client``, which would
    try to open a real httpx connection to whatever the Gateway URL points
    at — racing against ``boto3.Session().get_credentials()`` and almost
    certainly hanging on connect timeouts. Raising loudly at the boundary
    surfaces the missing stub immediately.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "Unstubbed Gateway MCP call. Tests that exercise "
            "GatewayMCPDiaryStore must install a fake streamablehttp_client "
            "via `monkeypatch.setattr("
            "'mcp.client.streamable_http.streamablehttp_client', "
            "fake_factory)`. Real MCP / Gateway must never be reached "
            "from the suite. "
            f"(called with args={args!r}, kwargs={list(kwargs)})"
        )


@pytest.fixture(autouse=True)
def safe_gateway_mcp_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse safety net: unstubbed MCP-over-HTTP calls raise immediately.

    Mirrors :func:`safe_memory_client`'s import-boundary pattern.
    :class:`GatewayMCPDiaryStore._call_tool` does a local
    ``from mcp.client.streamable_http import streamablehttp_client`` on
    every call, so the canonical patchable seam is the source attribute
    on the ``mcp.client.streamable_http`` module — patching that covers
    every future call site too.

    Tests that *do* want a working fake override this by replacing the
    same attribute with their own factory (see Slice 7 sub-task 4's
    ``test_gateway_mcp_smoke.py`` for the pattern).
    """
    import mcp.client.streamable_http as _streamable_http

    monkeypatch.setattr(
        _streamable_http,
        "streamablehttp_client",
        _LoudFailureStreamableHTTPClient,
    )


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


class FakeSmall:
    """STRICT stand-in for the small model — installed by ``fake_small(outputs=[...])``.

    The real code path is ``get_small().with_structured_output(Roster).invoke(msgs)``.
    This fake collapses that to a scripted-outputs queue. Each entry is either a
    ``Roster`` to return or an ``Exception`` to raise (e.g. ``ValidationError``
    to exercise the retry path).

    **One of the fixture's two call forms** (spec 042 §2.2): this strict queue
    backs ``outputs=`` ONLY, and drains one entry per ``.invoke``, raising
    ``AssertionError`` once empty. That guard is the point of the form — it is
    how a test pins "the corrective retry ran exactly once and then stopped",
    so retry- and coercion-semantics tests get a fake that cannot quietly
    answer a call they did not script. The other form (a plain list of names)
    routes to the permissive :class:`_PooledSmall` instead and must NEVER be
    routed here; see :func:`fake_small` for why blurring the two would hide a
    real defect.

    Attributes:
        call_count: How many times ``.invoke`` was called. Useful for asserting
            "retried exactly once" (i.e. ``call_count == 2``).
    """

    def __init__(self, outputs: Sequence[Roster | Exception]) -> None:
        self._outputs: list[Roster | Exception] = list(outputs)
        self.call_count = 0
        self._bound_schema: type | None = None

    def with_structured_output(self, schema: type, **kwargs: Any) -> "FakeSmall":
        # Real LangChain returns a new runnable bound to the schema; for the
        # test we just record the schema and return self so subsequent
        # ``.invoke`` calls go through the scripted queue.
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> Roster:
        self.call_count += 1
        if not self._outputs:
            raise AssertionError(
                "FakeSmall.invoke called more times than scripted outputs"
            )
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


# Names :func:`_pooled_names` draws on when the supplied pool is shorter than
# the count the game asks for (spec 042, Task 3.3). Genuinely distinct names,
# NOT recycled ones — a suffix on a name already in the roster violates the
# prefix invariant by construction, which is the regression this reserve exists
# to remove. Three properties of the contents are load-bearing:
#
# * **Plainly synthetic.** The ``-Extra`` marker (a film *extra* — an additional
#   body at the table) means nobody reading a roster line or a captured log can
#   mistake one of these for a name a test scripted itself.
# * **Not the production placeholder.** ``graphia.nodes.setup._coerce_to_count``
#   pads with ``Player-{k}``; none of these match that shape, so the
#   "no placeholder reached the table" assertions keep discriminating.
# * **Mutually distinct in their first three characters**, and distinct in those
#   three from every pool the suite supplies. That is a stronger property than
#   the prefix invariant itself, and it is deliberate: several tests resolve a
#   vote target by ``name[:3]`` through the production SUBSTRING matcher
#   ``graphia.nodes.day._fuzzy_match_alive`` and assert the match is unique, so
#   a reserve name sharing a three-character opening with a pool name would
#   reintroduce the same ambiguity in a slightly different disguise. ``Chi-`` is
#   deliberately absent: it opens ``Chiko``, which sits in most of the suite's
#   pools.
#
# Twelve entries against a worst case of ten (``graphia.llm._MAX_AI_NAMES`` is
# 11, reached from a one-name pool), so the numbered fallback below is
# unreachable at the table cap.
_EXTENSION_RESERVE: tuple[str, ...] = (
    "Kappa-Extra",
    "Lambda-Extra",
    "Omega-Extra",
    "Theta-Extra",
    "Sigma-Extra",
    "Upsilon-Extra",
    "Zeta-Extra",
    "Delta-Extra",
    "Gamma-Extra",
    "Iota-Extra",
    "Psi-Extra",
    "Tau-Extra",
)

# Bound on the numbered last-resort family, so a caller asking for an absurd
# count gets a named failure instead of an infinite loop.
_MAX_FALLBACK_UNDERSTUDIES = 999


def _prefix_free(candidate: str, accepted: Sequence[str]) -> bool:
    """True when ``candidate`` neither opens nor is opened by any accepted name.

    The single acceptance gate every name in :func:`_pooled_names`'s result
    passes through — pool names and extension names alike — so the prefix
    invariant holds **by construction** rather than by the extension scheme
    happening to be well chosen.

    Case-insensitive, matching :class:`graphia.llm.Roster`'s own distinctness
    rule. Note that it *subsumes* that rule: two names equal under ``lower()``
    each open the other, so this returns ``False`` for them and no separate
    ``seen`` set is needed.
    """
    key = candidate.lower()
    return not any(
        key.startswith(other.lower()) or other.lower().startswith(key)
        for other in accepted
    )


def _pooled_names(pool: Sequence[str], count: int) -> list[str]:
    """Exactly ``count`` distinct names drawn from ``pool``, extended when short.

    The pure name-supply behind :class:`_PooledSmall` (spec 042 §2.2). The
    supplied list is a **pool**, not a scripted answer: it is consumed in order,
    stripped and filtered through :func:`_prefix_free`, then truncated to
    ``count``. When the pool is shorter than ``count`` it is extended from
    :data:`_EXTENSION_RESERVE` — ``["Ivy", "Marco"]`` at count 5 yields
    ``["Ivy", "Marco", "Kappa-Extra", "Lambda-Extra", "Omega-Extra"]``.

    Three properties of the result are load-bearing:

    - **Prefix-safe (spec 042, Task 3.3).** No name in the returned list is a
      prefix of another, case-insensitively. This is a real contract, not
      tidiness: the production vote-target resolver
      ``graphia.nodes.day._fuzzy_match_alive`` matches a needle as a
      case-insensitive **substring** of an alive player's name and refuses to
      act when two players match, and several tests point at a target by its
      first three characters. The scheme this function used before Task 3.3
      extended a short pool by recycling it with a generation suffix
      (``Aarav`` → ``Aarav-2``), which violates the invariant *by construction*
      — the base name opens the suffixed one. At seven AI seats that put
      ``Aarav`` and ``Aarav-2`` at the same table and
      ``tests/test_slice7_vote.py::test_human_vote_bumps_human_votes_called``
      failed with ``prefix 'Aar' is ambiguous``, intermittently, because which
      AI is dealt Mafia is an unseeded RNG decision. Drawing from a reserve of
      genuinely distinct names is what removes the collision at the source.
    - **Deterministic.** Same ``(pool, count)`` in, same list out, always — no
      RNG, no clock, no counter. ``tests/test_dual_mode_smoke.py``'s byte-equal
      cross-mode comparison only means something if both modes derive an
      identical roster from an identical pool.
    - **Not the production placeholder.** Neither the reserve's ``-Extra``
      names nor the numbered last resort match the ``Player-{k}`` shape
      ``graphia.nodes.setup._coerce_to_count`` pads with, so a fixture-extended
      roster can never be mistaken for a coerced one by a test asserting that
      no placeholder reached the table.

    Because the invariant is stated over the **returned roster**, the gate runs
    over the supplied pool too: a pool holding ``["Ann", "Anna"]`` yields only
    ``Ann``, and the reserve makes up the difference. That is the same class of
    cleaning the function already did for blanks and case-duplicates — it keeps
    the invariant unconditional instead of contingent on the caller's pool. No
    pool in the suite collides today, so nothing observable changes.
    """
    names: list[str] = []
    for raw in pool:
        name = raw.strip()
        if not name or not _prefix_free(name, names):
            continue
        names.append(name)
        if len(names) == count:
            return names
    if not names:
        raise AssertionError(
            "fake_small was given an empty name pool; it needs at least one "
            "non-blank name to extend from."
        )
    for candidate in _EXTENSION_RESERVE:
        if len(names) == count:
            return names
        if _prefix_free(candidate, names):
            names.append(candidate)
    # Last resort, unreachable at the 12-seat table cap (see the reserve's
    # margin above): a numbered family, zero-padded to a fixed width so no
    # member opens another. The gate above still applies, so even a width
    # overflow cannot break the invariant — it would only skip a candidate.
    k = 1
    while len(names) < count:
        if k > _MAX_FALLBACK_UNDERSTUDIES:
            raise AssertionError(
                f"_pooled_names could not reach {count} prefix-free names from "
                f"pool {list(pool)!r}; the reserve and the numbered fallback "
                "are both exhausted."
            )
        candidate = f"Understudy-{k:03d}"
        if _prefix_free(candidate, names):
            names.append(candidate)
        k += 1
    return names


class _PooledSmall:
    """PERMISSIVE stand-in for the small model — installed by ``fake_small([...])``.

    Production call shape is
    ``get_small().with_structured_output(Roster).invoke(msgs)``, and
    ``graphia.nodes.setup._generate_names`` decides how many names it wants from
    the *resolved config*, at runtime. A pre-scripted queue therefore cannot
    know the right answer: at the wrong count the production guard
    ``len(roster.names) == count`` fails, ``_generate_names`` issues its
    corrective retry, the queue starves, and the resulting ``AssertionError``
    escapes ``generate_roster`` — swallowed in UI-driven tests into a multi-second
    poll timeout that never names the cause (spec 042 §2.2).

    So this fake answers the question instead of guessing it: every ``.invoke``
    asks :func:`graphia.nodes.setup.ai_name_count` how many names the lineup
    needs and returns exactly that many distinct names from the supplied pool
    (:func:`_pooled_names`). Every call is independent and idempotent — no
    queue, no replay, no exhaustion — exactly the reasoning behind
    :class:`_DynamicNightPointing` one phase later in the game.

    ``call_count`` still increments, so a test wanting "generated exactly once"
    keeps asserting it explicitly; what is gone is only the *implicit*,
    unwritten "…and never more than I scripted" that 82 scaffolding call sites
    were asserting by accident and could not read when it fired.

    Attributes:
        call_count: How many times ``.invoke`` was called.
    """

    def __init__(self, pool: Sequence[str]) -> None:
        self._pool: list[str] = list(pool)
        self.call_count = 0
        self._bound_schema: type | None = None

    def with_structured_output(self, schema: type, **kwargs: Any) -> "_PooledSmall":
        # Mirrors ``FakeSmall``: record the bound schema and return self.
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> Roster:
        self.call_count += 1
        # Derived HERE, at invoke time — not in ``__init__``, not at import.
        # ``tests/test_configurable_lineup.py`` and its siblings vary the lineup
        # per test through ``monkeypatch.setenv``, so a count bound any earlier
        # would be pinned to the default and disagree with the game actually
        # being played. ``ai_name_count`` is called rather than re-derived
        # because a second copy of ``num_citizens + num_mafia - 1`` living here
        # could silently drift from the production one.
        count = ai_name_count(load_config())
        # The rendered prompt is deliberately ignored: parsing the count out of
        # it would couple this fake to prompt wording that other specs change
        # freely.
        return Roster(names=_pooled_names(self._pool, count))


@pytest.fixture
def fake_small(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeSmall | _PooledSmall]:
    """Factory fixture: patch ``graphia.nodes.setup.get_small`` with a fake.

    **Two call forms, two different meanings** (spec 042 §2.2). Do not blur
    them — the separation is the whole reason the permissive form is safe:

    1. **The list form — a permissive POOL.** ``fake_small([...])`` installs
       :class:`_PooledSmall`: stateless, count-derived, no queue, no drain, no
       replay. Every ``.invoke`` asks the production helper
       :func:`graphia.nodes.setup.ai_name_count` how many names the *resolved
       config* wants and answers with exactly that many distinct names, drawing
       the supplied list as a pool and extending it deterministically when the
       pool is short (:func:`_pooled_names`). The list is therefore a supply of
       recognisable names, **not** a lineup-sized script — it never needs
       resizing when the default table changes. This is the right form for the
       overwhelming majority of tests, which only need the roster step to
       produce *some* names so the game can proceed. ``call_count`` still
       increments, so::

           fake = fake_small(["Ivy", "Marco", "Priya", "Silas", "Yuki"])
           # ...run the app / node under test...
           assert fake.call_count == 1  # generated exactly once

       still says exactly what it used to say.

    2. **The ``outputs=`` form — a STRICT one-shot queue.** ``fake_small(
       outputs=[...])`` installs :class:`FakeSmall`: a sequence of ``Roster``
       values and ``Exception`` instances, one consumed per ``.invoke``, raising
       ``AssertionError`` once drained. **For retry and coercion tests only** —
       those are the tests whose subject *is* how many times the model was
       called and what it returned each time, and the drain guard is what makes
       them able to catch a real defect in ``_generate_names``.

    The safety constraint, stated so it is not re-litigated: **the ``outputs=``
    form must never be routed to the permissive fake.** A permissive fake could
    in principle mask a defect in the retry/coercion path; keeping the three
    call sites that test that path on the strict queue is what contains the
    risk by construction.

    Patches the ``get_small`` binding **inside** ``graphia.nodes.setup`` (the
    call site) so the already-imported reference is replaced cleanly — patching
    the canonical ``graphia.llm.get_small`` would not help, because setup.py
    already bound the original at import time.
    """

    def _install(
        names: Sequence[str] | None = None,
        *,
        outputs: Sequence[Roster | Exception] | None = None,
    ) -> FakeSmall | _PooledSmall:
        if outputs is not None and names is not None:
            raise TypeError(
                "fake_small takes `names` (permissive pool) OR `outputs` "
                "(strict queue), never both — the two forms mean different "
                "things; see the fixture docstring."
            )
        if outputs is not None:
            return _patched(FakeSmall(outputs))
        if names is None:
            raise TypeError("fake_small requires either `names` or `outputs`")
        return _patched(_PooledSmall(names))

    def _patched(fake: FakeSmall | _PooledSmall) -> FakeSmall | _PooledSmall:
        monkeypatch.setattr("graphia.nodes.setup.get_small", lambda: fake)
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
    - Points the career stats file at ``tmp_path / career.json`` so tests never
      read or write the developer's real career history (and each test starts
      from a zeroed, first-run aggregate).

    Yields the log-file path for tests that want to read emitted events.
    """
    log_file = tmp_path / "graphia.log"
    checkpoint_dir = tmp_path / "checkpoints"
    stats_file = tmp_path / "career.json"
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    monkeypatch.setenv("GRAPHIA_LOG_FILE", str(log_file))
    monkeypatch.setenv("GRAPHIA_CHECKPOINT_DIR", str(checkpoint_dir))
    monkeypatch.setenv("GRAPHIA_STATS_FILE", str(stats_file))
    yield log_file


class FakeLarge:
    """Stand-in for ``ChatBedrockConverse`` used inside Mafia pointing.

    The production call is
    ``get_large().with_structured_output(Pointing).invoke(msgs)``. This fake
    collapses that into a queue of scripted ``Pointing`` outputs (or
    exceptions to exercise the retry / random-fallback path).

    Attributes:
        call_count: Number of times ``.invoke`` was called — useful for
            asserting the retry branch ran exactly once.
        last_messages: The message list handed to the most recent ``.invoke``
            call (the LangChain ``[SystemMessage, HumanMessage, ...]`` prompt).
            Lets prompt-threading tests inspect the actual rendered prompt
            text the model would have received (Spec 015 §2.4 — the by-name
            "teammates' picks so far" block). Purely additive: existing tests
            only read ``call_count``.
        messages_log: Every ``.invoke``'s message list, in call order — for
            tests that need the prompt from a *specific* (e.g. 2nd-pointer)
            invocation rather than just the last.
    """

    def __init__(self, outputs: Sequence[Pointing | Exception]) -> None:
        self._outputs: list[Pointing | Exception] = list(outputs)
        self.call_count = 0
        self._bound_schema: type | None = None
        self.last_messages: Any = None
        self.messages_log: list[Any] = []

    def with_structured_output(self, schema: type, **kwargs: Any) -> "FakeLarge":
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> Pointing:
        self.call_count += 1
        self.last_messages = messages
        self.messages_log.append(messages)
        if not self._outputs:
            raise AssertionError(
                "FakeLarge.invoke called more times than scripted outputs"
            )
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def fake_large_pointing(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeLarge]:
    """Factory fixture: patch ``graphia.nodes.night.get_large`` with a fake.

    Usage::

        fake = fake_large_pointing(["victim-id", "victim-id"])
        # ... run the app ...
        assert fake.call_count == 2

    Accepts either a list of target ids (one per AI-mafia ``.invoke`` call,
    in call order — each id is wrapped in a ``Pointing``) or an explicit
    ``outputs=`` sequence mixing ``Pointing`` values and ``Exception``
    instances for retry-path tests.

    Patches the ``get_large`` binding **inside** ``graphia.nodes.night`` so
    the already-imported reference is replaced at the call site.
    """

    def _install(
        target_ids: Sequence[str] | None = None,
        *,
        outputs: Sequence[Pointing | Exception] | None = None,
    ) -> FakeLarge:
        if outputs is None:
            if target_ids is None:
                raise TypeError(
                    "fake_large_pointing requires either `target_ids` or `outputs`"
                )
            outputs = [Pointing(target_id=t) for t in target_ids]
        fake = FakeLarge(outputs)
        monkeypatch.setattr("graphia.nodes.night.get_large", lambda: fake)
        return fake

    return _install


class FakeLargeDay:
    """Stand-in for ``ChatBedrockConverse`` used inside Day-phase speaking.

    Production call site is
    ``get_large().with_structured_output(DayAction).invoke(msgs)`` inside
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

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "FakeLargeDay":
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
                    "FakeLargeDay.invoke called but no scripted outputs "
                    "remain and no prior output to repeat"
                )
            return self._last
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        self._last = out
        return out


@pytest.fixture
def fake_large_day(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeLargeDay]:
    """Factory fixture: patch ``graphia.nodes.day.get_large`` with a fake.

    Usage::

        fake = fake_large_day([
            DayAction(kind="speak", text="msg-from-AI-1"),
            DayAction(kind="speak", text="msg-from-AI-2"),
        ])
        # ... drive the app ...
        assert fake.call_count >= 1

    Accepts either an explicit sequence of ``DayAction`` / ``Exception`` values
    via ``outputs=``, or a shortcut ``texts=`` parameter that wraps each
    string as ``DayAction(kind="speak", text=...)``.

    Patches the ``get_large`` binding **inside** ``graphia.nodes.day`` so the
    already-imported reference is replaced at the call site.
    """

    def _install(
        outputs: Sequence[DayAction | Exception] | None = None,
        *,
        texts: Sequence[str] | None = None,
    ) -> FakeLargeDay:
        if outputs is None:
            if texts is None:
                raise TypeError(
                    "fake_large_day requires either `outputs` or `texts`"
                )
            outputs = [DayAction(kind="speak", text=t) for t in texts]
        fake = FakeLargeDay(outputs)
        monkeypatch.setattr("graphia.nodes.day.get_large", lambda: fake)
        return fake

    return _install


def plain_text(widget: Widget) -> str:
    """Return the rendered plain-text form of a widget (Rich markup stripped)."""
    rendered = widget.render()
    if isinstance(rendered, Text):
        return rendered.plain
    return Text.from_markup(str(rendered)).plain


# --------------------------------------------------------------------------
# Lineup-derived test budgets (spec 042, Task 4.1)
#
# Two kinds of budget in this suite were sized against the seven-player table
# and did not say so: a hard-coded LangGraph ``recursion_limit=50`` in
# ``tests/test_vote_validation.py``'s two drive helpers, and a hard-coded
# ``range(80)`` poll loop in the three UI game-drivers. The first FAILED the
# moment the default lineup grew by one seat —
# ``GraphRecursionError: Recursion limit of 50 reached`` — because the drive
# that runs after the human's self-execution has no human interrupt left to
# pause on and therefore free-runs the rest of the game in one ``stream``.
#
# **The cost is not "one more speaker per round."** Dead players do not speak,
# so the real quantity is the *sum of alive players across the Days the game
# lasts* — and the number of Days is itself lineup-derived. With nobody ever
# executed (the shape every fake-driven game takes, since the scripted
# ``DayAction`` queue only ever speaks), the Mafia reach parity after
# ``total - 2 * num_mafia`` nights: three at five-and-two, **four** at
# six-and-two. That is why the post-execution drive costs 17 super-steps at
# seven players and 63 at eight — a quadratic step, not a linear one.
#
# Every budget below is therefore derived from the resolved config, so a larger
# table cannot silently re-tighten it. Measured need vs. the derived
# :func:`whole_game_recursion_limit` across the whole legal range (the numbers
# on the left were measured by streaming the post-execution drive with the
# limit lifted):
#
#     players   6    7    8    9   10   11   12
#     measured  8   17   63  115  179  240  313
#     derived 212  277  348  425  508  597  692
#
# The headroom is deliberate and cheap: a recursion limit is an anti-hang
# guard, so being generous costs nothing on a healthy trajectory while still
# terminating a genuine loop in well under a second.
# --------------------------------------------------------------------------


def game_cycles(config: GraphiaConfig) -> int:
    """Upper bound on the Day/Night cycles a fake-driven game runs at ``config``.

    Starting law-abiding count is ``total - num_mafia``; each Night removes one
    of them and the Mafia win at parity, so with nobody ever executed the game
    ends after ``total - 2 * num_mafia`` Nights. Two extra cycles are allowed
    for the partial Day a drive may start inside and the Day the win-check
    finally fires on, and the whole thing is clamped by production's own
    ``max_days`` runaway cap so this can never claim more Days than the graph
    will actually run.
    """
    total = config.num_citizens + config.num_mafia
    nights_to_parity = max(1, total - 2 * config.num_mafia)
    return min(nights_to_parity + 2, config.max_days)


def alive_speaker_sum(config: GraphiaConfig) -> int:
    """Sum of alive players across :func:`game_cycles` Days.

    The quantity the tech spec's risk row names. One player dies per Night, so
    the speaking roster shrinks by one each cycle — the reason a budget shaped
    as "players x rounds" understates a bigger table while "one more speaker
    per round" overstates it. Floored at 2 because a Day with fewer than two
    players alive cannot happen before the win-check ends the game.
    """
    total = config.num_citizens + config.num_mafia
    return sum(max(2, total - offset) for offset in range(game_cycles(config)))


def whole_game_recursion_limit(config: GraphiaConfig) -> int:
    """A LangGraph ``recursion_limit`` spanning a whole fake-driven game.

    Replaces the hard-coded ``50`` in ``tests/test_vote_validation.py``. Sized
    from production's own caps so it tracks the lineup instead of a remembered
    table:

    * **speech** — ``DAY_MAX_ROUNDS`` rounds per Day, each costing one
      ``day_turn`` super-step per alive speaker (:func:`alive_speaker_sum`)
      plus one round-wrap/reflect super-step;
    * **ballots** — a full poll of the table for each of the ``DAY_MAX_VOTES``
      votes a Day allows, plus that vote's open and resolve steps;
    * **phase** — the fixed per-cycle nodes (``day_open``, ``day_close``,
      ``day_diary``, both win-checks, ``night_open``, ``night_resolve``, the
      end-screen) plus one ``mafia_pointing`` super-step per Mafioso.

    Pass it wherever a test streams the graph without a guaranteed human
    interrupt to pause on — after the human dies there is none, and the drive
    then runs the entire remaining game in a single ``stream`` call.
    """
    total = config.num_citizens + config.num_mafia
    cycles = game_cycles(config)
    speech = DAY_MAX_ROUNDS * (alive_speaker_sum(config) + cycles)
    ballots = DAY_MAX_VOTES * (total + 2)
    phase = cycles * (12 + config.num_mafia)
    return speech + ballots + phase


def expected_human_day_prompts(config: GraphiaConfig) -> int:
    """Upper bound on the Day-turn prompts a fake-driven game shows the human.

    One prompt per Day round the human survives to see, over
    :func:`game_cycles` Days. Used to size the UI game-driver's wall-clock
    deadline, which is the quantity that actually moves with the lineup: a
    bigger table means more Days before parity, hence more prompts, hence more
    press/settle round-trips.
    """
    return DAY_MAX_ROUNDS * game_cycles(config)


# How long a single "input still disabled" poll waits. Unchanged from the three
# hand-rolled loops this helper replaces, so their pacing is preserved exactly.
_PUBLIC_LOG_POLL_INTERVAL = 0.2

# Slack for the final ``end_screen`` + banner super-step batch, which lands
# with no further human prompt to drive it. This is the separate "longer-
# grained final poll" each of the three call sites used to run AFTER its
# exhausted loop — folded into the one deadline here, because a second phase
# sitting behind an exhausted loop converts "the budget was too small" into a
# confusing failure about the wrong thing.
_PUBLIC_LOG_SETTLE_SECONDS = 10.0


def public_log_deadline_seconds(config: GraphiaConfig) -> float:
    """Wall-clock budget for driving one whole game through the Textual UI.

    Sized from :func:`expected_human_day_prompts` at three poll intervals per
    prompt — one to press, and two of slack for the worker's super-step batch
    to land before the next prompt enables — plus
    :data:`_PUBLIC_LOG_SETTLE_SECONDS` for the final end-screen batch. At
    five-and-two this comes out at ~28s, at six-and-two ~32s, at the twelve-seat
    table cap ~46s, so it is never tighter than the ``range(80)``-and-a-10s-tail
    budget it replaces and it grows with the table.
    """
    prompts = expected_human_day_prompts(config)
    return _PUBLIC_LOG_SETTLE_SECONDS + 3.0 * _PUBLIC_LOG_POLL_INTERVAL * prompts


def public_log_iteration_cap(config: GraphiaConfig) -> int:
    """Secondary stop for :func:`drive_until_public_log_contains`.

    Derived FROM the deadline rather than alongside it, so the relationship
    between the two stops holds by construction: the cap is more iterations
    than the deadline could ever allow if every one of them paused, plus slack
    for the cheap press-and-enter iterations that do not pause at all. The
    consequence is the intended division of labour — on a healthy run the
    deadline is what stops the loop, and the cap only bites when iterations
    stop costing time, which is exactly the pathological
    always-enabled-input shape (a bug that re-enables the prompt without the
    graph ever advancing) that would otherwise spin for the full deadline
    without telling anyone why.
    """
    deadline_iterations = int(
        public_log_deadline_seconds(config) / _PUBLIC_LOG_POLL_INTERVAL
    )
    return deadline_iterations + 2 * expected_human_day_prompts(config) + 20


async def drive_until_public_log_contains(
    pilot: Any,
    *,
    log_text: Callable[[], str],
    sentinel: str = "Game over.",
    reply: str = ".",
    input_selector: str = "#player-input",
) -> str:
    """Answer every Day-turn prompt until ``sentinel`` reaches the public log.

    The one shared game-driver for the three UI smoke tests that used to
    hand-roll it (``test_dual_mode_smoke.py``, ``test_remote_mode_smoke.py``,
    ``test_slice8_endgame.py``), each with its own ``range(80)`` loop, its own
    10-second tail poll, and its own near-identical "never appeared" block.
    Returns the rendered log once ``sentinel`` appears; raises
    ``AssertionError`` carrying that rendered log if neither stop is reached
    first.

    ``log_text`` renders the public pane; the three call sites disagree on how
    (a module helper in two of them, a closure over the ``RichLog`` widget in
    the third), so it stays the caller's business.

    **The named trade-off, recorded because it was a deliberate choice.** This
    helper is keyed on a **wall-clock deadline** (:func:`public_log_deadline_seconds`)
    with an iteration count only as a secondary stop
    (:func:`public_log_iteration_cap`). A deadline is *flakier on a loaded
    machine* than an iteration count is reproducible — a busy CI box can blow
    a wall-clock budget that a quiet laptop clears easily, and no amount of
    care in this function changes that. It is accepted because the design this
    replaces **already depended on wall-clock behaviour**: its per-iteration
    ``pilot.pause(0.2)`` meant its ``range(80)`` was a time budget wearing an
    iteration count's clothes, and a *badly sized* one — it could not say what
    it was worth without the reader knowing how many of the 80 iterations
    would pause. Naming the seconds makes the budget legible and lets it be
    derived from the lineup; the mitigation is generous sizing.
    """
    config = load_config()
    deadline_seconds = public_log_deadline_seconds(config)
    iteration_cap = public_log_iteration_cap(config)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_seconds
    app = pilot.app
    iterations = 0

    while True:
        rendered = log_text()
        if sentinel in rendered:
            return rendered

        exhausted = (
            f"iteration cap ({iteration_cap})"
            if iterations >= iteration_cap
            else f"deadline ({deadline_seconds:.1f}s)"
            if loop.time() >= deadline
            else None
        )
        if exhausted is not None:
            app.exit()
            raise AssertionError(
                f"{sentinel!r} never appeared in the public log before the "
                f"{exhausted} was reached "
                f"({iterations} iterations, "
                f"lineup {config.num_citizens}+{config.num_mafia}, "
                f"{expected_human_day_prompts(config)} human prompts "
                f"expected). If the game legitimately needs longer at this "
                f"table, the budget is derived in "
                f"tests/conftest.py::public_log_deadline_seconds — do not "
                f"re-hard-code it. Log was:\n" + rendered
            )

        iterations += 1
        try:
            prompt = app.query_one(input_selector, Input)
        except Exception:  # noqa: BLE001
            prompt = None
        if prompt is not None and prompt.disabled is False:
            await pilot.press(*reply)
            await pilot.press("enter")
        else:
            await pilot.pause(_PUBLIC_LOG_POLL_INTERVAL)


# --------------------------------------------------------------------------
# Unified large-model fake — dispatches on the schema passed to
# ``with_structured_output`` so a single fake can serve DayAction, Ballot,
# and Pointing calls simultaneously. Needed for Slice 7 where ``collect_votes``
# binds ``Ballot`` while ``day_turn`` binds ``DayAction`` on the same
# ``get_large()`` reference.
# --------------------------------------------------------------------------


class _LargeQueue:
    """Bound view over one of the unified fake's scripted queues.

    Each ``invoke`` call pops the next scripted item for the bound schema.
    When the queue is empty the last popped value is replayed — the exact
    same "keep serving the last output" behaviour the per-schema fakes use
    so long-running tests don't need to pre-script exactly the right number
    of invocations.
    """

    def __init__(
        self, owner: "FakeLargeUnified", schema: type, *, include_raw: bool = False
    ) -> None:
        self._owner = owner
        self._schema = schema
        self._include_raw = include_raw

    def invoke(self, messages: Any) -> Any:
        out = self._owner._invoke(self._schema, messages)
        if not self._include_raw:
            return out
        # ``include_raw=True`` (spec 039 defect fix; ``_ai_diary``'s call shape)
        # hands back a MAPPING, not the parsed object. A scripted
        # ``BaseMessage`` expresses the real ollama failure this fix exists
        # for: the model answered in prose and emitted no tool call, so
        # ``parsed`` is None and the entry survives only in ``raw``.
        if isinstance(out, BaseMessage):
            return {"raw": out, "parsed": None, "parsing_error": None}
        return {
            "raw": AIMessage(content="", response_metadata={"stop_reason": "tool_use"}),
            "parsed": out,
            "parsing_error": None,
        }


class FakeLargeUnified:
    """Unified large-model fake dispatching on the schema bound at call time.

    Production call shape::

        get_large().with_structured_output(SchemaClass).invoke(msgs)

    This fake keeps a separate scripted queue per schema class so one fixture
    can satisfy ``DayAction`` (speak/vote), ``Ballot`` (yes/no), ``Pointing``
    (night target), ``Persona`` (setup-time character generation),
    ``Reflection`` (spec 028 — the per-AI end-of-Day-round private thought),
    and ``Diary`` (spec 039 — the per-AI before-Night private diary entry)
    bindings without interference.

    **A missing queue is a SILENT failure, not a loud one.** Both
    ``day_round_reflect`` (028) and ``day_diary`` (039) wrap their model call
    in ``try/except Exception`` and substitute a deterministic fallback note.
    The ``AssertionError`` this class raises for an unknown schema is therefore
    *swallowed by the node under test*, and the test goes on to measure the
    fallback while passing — proving nothing about the real path. Every new
    structured-output call site must get its queue here in the same change that
    introduces it (tech-spec 039 §3).

    Attributes:
        call_count: Total invocations across all schemas.
        calls_by_schema: Per-schema invocation counts, keyed by schema class.
            The counter is what distinguishes "the scripted queue served this
            call" from "the queue was never reached": a swallowed
            unknown-schema ``AssertionError`` leaves the schema's count at 0.
    """

    def __init__(
        self,
        *,
        day_actions: Sequence[DayAction | Exception] | None = None,
        ballots: Sequence[Ballot | Exception] | None = None,
        pointings: Sequence[Pointing | Exception] | None = None,
        personas: Sequence[Persona | Exception] | None = None,
        reflections: Sequence[Reflection | Exception] | None = None,
        diaries: Sequence[Diary | BaseMessage | Exception] | None = None,
    ) -> None:
        self._queues: dict[type, list[Any]] = {
            DayAction: list(day_actions) if day_actions else [],
            Ballot: list(ballots) if ballots else [],
            Pointing: list(pointings) if pointings else [],
            # Spec 016: ``generate_personas`` binds ``Persona`` on this same
            # ``get_large()`` reference at setup time. A persona queue replays
            # its last value once drained, like the others — so a test can
            # supply one persona and have it serve every AI player.
            Persona: list(personas) if personas else [],
            # Spec 028: ``day_round_reflect`` binds ``Reflection`` on this same
            # ``get_large()`` reference, once per surviving AI per completed Day
            # round. Replays its last value once drained, like the others — so a
            # test can supply one reflection and have it serve every reflection
            # call. REQUIRED so a flag-on full-Day run never falls through to the
            # loud-failure default (the reflection node's try/except would turn
            # that into a fallback note, but the explicit queue keeps the
            # captured-prompt tests deterministic).
            Reflection: list(reflections) if reflections else [],
            # Spec 039: ``day_diary`` binds ``Diary`` on this same
            # ``get_large()`` reference, once per surviving AI per Day, at the
            # Day→Night hinge. Replays its last value once drained, like the
            # others — so a test can supply one entry and have it serve every
            # player on every Day. REQUIRED, and for a sharper reason than the
            # others: the node's ``try/except`` swallows the unknown-schema
            # ``AssertionError`` into ``_DIARY_FALLBACK``, so WITHOUT this queue
            # a flag-on run measures the fallback and passes green while
            # proving nothing (tech-spec 039 §3; regression test in
            # ``tests/test_slice39_diary_fake_coverage.py``).
            Diary: list(diaries) if diaries else [],
        }
        self._last: dict[type, Any] = {}
        self.call_count = 0
        self.calls_by_schema: dict[type, int] = {
            DayAction: 0,
            Ballot: 0,
            Pointing: 0,
            Persona: 0,
            Reflection: 0,
            Diary: 0,
        }

    def with_structured_output(
        self, schema: type, *, include_raw: bool = False
    ) -> _LargeQueue:
        if schema not in self._queues:
            raise AssertionError(
                f"FakeLarge has no scripted queue for schema {schema!r}. "
                "Supported: DayAction, Ballot, Pointing, Persona, Reflection, "
                "Diary."
            )
        return _LargeQueue(self, schema, include_raw=include_raw)

    def _invoke(self, schema: type, messages: Any) -> Any:
        self.call_count += 1
        self.calls_by_schema[schema] = self.calls_by_schema.get(schema, 0) + 1
        queue = self._queues[schema]
        if not queue:
            last = self._last.get(schema)
            if last is None:
                raise AssertionError(
                    f"FakeLarge.invoke called for {schema.__name__} but no "
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
    ``get_large().with_structured_output(Pointing).invoke(msgs)``. Between
    a test's ``fake_large(...)`` call and the worker actually reaching the
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

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "_DynamicNightPointing":
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
    """Factory: patch ``graphia.nodes.night.get_large`` with a race-safe fake.

    Usage (after an earlier ``fake_large(...)`` call — this fixture
    overrides the night-side binding installed there)::

        dynamic_night_pointing(lambda: app._graph.get_state(app._run_config).values)

    The returned ``_DynamicNightPointing`` instance exposes ``call_count``
    for tests that want to assert the number of AI-Mafia invocations.
    """

    def _install(
        state_provider: Callable[[], dict],
    ) -> _DynamicNightPointing:
        fake = _DynamicNightPointing(state_provider)
        monkeypatch.setattr("graphia.nodes.night.get_large", lambda: fake)
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

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "_TargetHumanPointing":
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
    """Factory: patch ``graphia.nodes.night.get_large`` to always target the human.

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
        monkeypatch.setattr("graphia.nodes.night.get_large", lambda: fake)
        return fake

    return _install


@pytest.fixture
def fake_large(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FakeLargeUnified]:
    """Factory fixture: unified large-model fake patched into BOTH day and night.

    Usage::

        fake = fake_large(
            day_actions=[DayAction(kind="speak", text="hello")],
            ballots=[Ballot(yes=True), Ballot(yes=False)],
            pointings=[Pointing(target_id="p-2")],
            personas=[Persona(personality="bold", manner="terse",
                              public_backstory="the baker")],
            reflections=[Reflection(thought="I should watch the baker.")],
            diaries=[Diary(entry="Day one is done. The baker talks too much.")],
        )

    Patches ``graphia.nodes.day.get_large``, ``graphia.nodes.night.get_large``
    AND ``graphia.nodes.setup.get_large`` with the same instance so calls
    routed through any call site go through one queue-set. This is required
    for Slice 7/8 tests where a single run touches ``DayAction`` (speaking),
    ``Ballot`` (voting), ``Pointing`` (next night), (Spec 016) ``Persona``
    (setup-time generation), (Spec 028) ``Reflection`` (per-AI end-of-round
    private thought), and (Spec 039) ``Diary`` (per-AI before-Night private
    diary entry) on the same large-model binding.

    Script ``diaries=`` whenever the assertion depends on WHAT the diary node
    wrote. Leaving it unscripted is not an error — the queue exists, so the
    node's own empty-queue path decides the outcome — but a test that wants the
    real path exercised must pass an entry AND check
    ``calls_by_schema[Diary]``; see ``tests/test_slice39_diary_fake_coverage.py``.
    """

    def _install(
        *,
        day_actions: Sequence[DayAction | Exception] | None = None,
        ballots: Sequence[Ballot | Exception] | None = None,
        pointings: Sequence[Pointing | Exception] | None = None,
        personas: Sequence[Persona | Exception] | None = None,
        reflections: Sequence[Reflection | Exception] | None = None,
        diaries: Sequence[Diary | BaseMessage | Exception] | None = None,
    ) -> FakeLargeUnified:
        fake = FakeLargeUnified(
            day_actions=day_actions,
            ballots=ballots,
            pointings=pointings,
            personas=personas,
            reflections=reflections,
            diaries=diaries,
        )
        monkeypatch.setattr("graphia.nodes.day.get_large", lambda: fake)
        monkeypatch.setattr("graphia.nodes.night.get_large", lambda: fake)
        # Spec 016: ``generate_personas`` is the first heavyweight call site in
        # ``setup.py``. Patch it here too so a single ``fake_large(...)`` covers
        # persona generation in addition to Day/Night — keeping the one-fake
        # contract whole-game.
        monkeypatch.setattr("graphia.nodes.setup.get_large", lambda: fake)
        # Spec 034: with the diversity flag ON (default), ``generate_personas``
        # builds the persona model via ``get_persona_model(temperature)`` instead
        # of ``get_large()``. Route it to the SAME unified fake (its ``Persona``
        # queue), so one ``fake_large(personas=[...])`` covers the diversified
        # path too and a flag-on full-setup test never reaches real Bedrock.
        monkeypatch.setattr(
            "graphia.nodes.setup.get_persona_model", lambda temperature: fake
        )
        return fake

    return _install
