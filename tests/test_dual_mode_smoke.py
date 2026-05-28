"""Slice 9 sub-task 2: end-to-end dual-mode equivalence smoke test.

Spec 002 §2.3 makes a load-bearing promise: "no gameplay-visible behaviour
differs between local and remote modes." The deployed AgentCore Runtime is
meant to be a transport swap, not a behaviour change — the same graph, the
same nodes, the same RNG decisions. This test pins that promise down at the
sharpest possible granularity: it drives *one complete game* in each mode
with byte-identical scripted inputs and asserts the public-facing output
matches exactly.

Relationship to the sibling smoke tests
----------------------------------------

``test_remote_mode_smoke.py`` already has a parametrised
``test_local_and_remote_render_equivalent_winner`` pair — but that test only
asserts the *winner line* and *roster names* appear in both modes; it never
compares the two runs' full output to each other. ``test_diary_store.py``
(Slice 9 sub-task 1) compares the two ``DiaryStore`` impls observably, but
in isolation from a real game. This file is the missing piece: a full-game,
whole-public-transcript, exact ``==`` comparison of the two modes.

What makes the two runs comparable
----------------------------------

Determinism is engineered on three axes, all reset identically before each
run:

* **RNG** — ``random.seed(0)`` is called at the start of each mode run,
  fixing role assignment and every tie-break. Seed 0 puts the human in slot
  0 as Law-abiding, a well-trodden trajectory (see ``test_remote_mode_smoke.py``)
  that needs no ``kind="point"`` interrupt answer.
* **LLM** — the ``fake_haiku`` / ``fake_sonnet`` fixtures script every
  Bedrock call; the unified Sonnet fake additionally gets a live-state
  dispatcher (identical to the one in ``test_remote_mode_smoke._run_full_game``)
  so Pointing/DayAction targets resolve deterministically at invoke time.
* **Human input** — both runs feed the same name (``Alice``) and the same
  ``.`` for every Day-turn prompt.

How remote mode stays AWS-free
------------------------------

Two seams are mocked:

* ``graphia.driver.AgentCoreClient`` → :class:`FakeAgentCoreClient` (borrowed
  from ``test_remote_mode_smoke.py``), which proxies ``stream`` into the
  in-process compiled graph captured from ``build_graph``. No
  ``boto3.client('bedrock-agentcore')`` is ever constructed.
* ``bedrock_agentcore.memory.MemoryClient`` → :class:`FakeMemoryClient`
  (borrowed from ``test_diary_store.py``), so the remote run's graph is
  genuinely built with a real :class:`AgentCoreMemoryDiaryStore` —
  exercising the remote diary-store code path — while staying offline.

Normalisation
-------------

``build_graph`` derives ``thread_id`` from ``datetime.now()`` (a per-run
``%Y%m%dT%H%M%S`` timestamp). It is the one genuinely per-run value that
could appear in output, so each run's ``thread_id`` is substituted with the
fixed literal ``<thread-id>`` before the exact-match compare. Nothing else
needs normalising: at seed 0 the roster names, kill order and winner are all
RNG-derived and therefore identical between runs. Private (``private_to``)
messages and the ``[local]`` / ``[remote]`` mode badge are *expected* to
differ between modes, so the comparison is scoped to the genuinely public,
gameplay-visible ``#public-log`` transcript only.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

import pytest
from rich.text import Text
from textual.widgets import Input, RichLog

from graphia.llm import Ballot, DayAction, Pointing
from graphia.stats_store import LocalFileStatsStore
from graphia.ui.app import GraphiaApp

# Reuse the proven remote-mode harness pieces rather than re-deriving them.
# The ``tests/`` directory is not a package (no ``__init__.py``); pytest adds
# it to ``sys.path``, so sibling test modules import by their bare name.
from test_diary_store import FakeMemoryClient
from test_remote_mode_smoke import (
    AI_NAMES,
    FAKE_RUNTIME_ARN,
    FakeAgentCoreClient,
    HUMAN_NAME,
    _public_log_text,
)

# Load-bearing seed: this test asserts byte-identical public logs, kill_log,
# and winner between two independent runs (local vs remote). That equality
# only holds if the RNG is pinned across both runs — role assignment, mafia
# pointing target identity (law_ids[0] in the dispatcher depends on dict
# insertion order, which depends on role assignment), kill order, and any
# vote tie-break must all be reproducible. Per ADR-006 the seed is kept here
# because it pins mechanical RNG behaviour the asserts depend on, alongside
# GRAPHIA_ROLE which pins the human's role. Applied via ``random.seed(...)``
# at the start of each mode run — production code uses the module-global
# ``random`` API directly, so both modes draw from the same global RNG state.
SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY = 0


# --------------------------------------------------------------------------
# Per-run fixtures: local vs remote environment
# --------------------------------------------------------------------------


def _clear_remote_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every remote-mode env var so ``load_config`` reports local mode."""
    monkeypatch.delenv("GRAPHIA_REMOTE", raising=False)
    monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
    monkeypatch.delenv("GRAPHIA_MEMORY_ID", raising=False)


def _set_remote_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the remote-mode env vars ``load_config`` reads in ``on_mount``.

    ``GRAPHIA_MEMORY_ID`` being non-empty makes ``make_diary_store`` return a
    real :class:`AgentCoreMemoryDiaryStore` — that is the remote diary path
    this test deliberately exercises (offline, via :class:`FakeMemoryClient`).
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
    monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-dual-mode-test")


# --------------------------------------------------------------------------
# The shared full-game runner — drives one complete game and harvests output
# --------------------------------------------------------------------------


async def _run_full_game_collecting(
    app: GraphiaApp, fake_sonnet_handle: Any
) -> dict[str, Any]:
    """Drive ``app`` boot-to-"Game over." and return its observable outcome.

    Returns a dict with the public-log transcript plus the final
    ``kill_log`` and ``winner`` read from the compiled graph's terminal
    state. In both modes the local in-process graph reaches that terminal
    state: in local mode it runs the game directly; in remote mode
    :class:`FakeAgentCoreClient` proxies ``stream`` into that very graph, so
    ``graph.get_state`` is populated identically either way.

    The live-state Sonnet dispatcher is identical to the one in
    ``test_remote_mode_smoke._run_full_game`` — Pointing/DayAction targets
    can only resolve once role-assignment has minted the uuid player ids, so
    selection is deferred to invoke time. Both modes share this code, so the
    AI's choices are byte-identical across the two runs.
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

        # Install the live-state dispatcher so AI targets resolve at invoke
        # time against the freshly-assigned uuid player ids.
        fake = fake_sonnet_handle
        original_invoke = fake._invoke

        def _invoke_live(schema: type, messages: Any) -> Any:
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

        fake._invoke = _invoke_live  # type: ignore[method-assign]

        # Enter the human name once the input enables.
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

        # Answer every Day-turn prompt with "." and poll for game end.
        for _ in range(80):
            if "Game over." in _public_log_text(app):
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

        # Longer-grained final poll for the end_screen + banner super-step.
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

        # Harvest the terminal graph state while the app is still alive.
        final_state = graph.get_state(rc).values
        kill_log = final_state.get("kill_log", [])
        winner = final_state.get("winner")
        thread_id = (rc.get("configurable") or {}).get("thread_id")

        await pilot.press("x")

    assert app.is_running is False
    return {
        "public_log": rendered,
        "kill_log": kill_log,
        "winner": winner,
        "thread_id": thread_id,
    }


def _normalise(text: str, thread_id: str | None) -> str:
    """Replace the per-run ``thread_id`` with a stable literal.

    ``build_graph`` derives ``thread_id`` from ``datetime.now()`` — it is the
    single genuinely per-run value that could surface in public output (it is
    also the diary ``game_id``). Substituting it with ``<thread-id>`` lets the
    two transcripts compare exactly while still catching any *other*
    divergence. If ``thread_id`` is falsy the text is returned unchanged.
    """
    if not thread_id:
        return text
    return text.replace(thread_id, "<thread-id>")


# --------------------------------------------------------------------------
# The test
# --------------------------------------------------------------------------


async def test_local_and_remote_full_game_produce_identical_public_output(
    env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_haiku,
    fake_sonnet,
) -> None:
    """A full game in local vs remote mode yields identical public output.

    Drives one complete game in each mode with the same ``random.seed(...)``, the
    same scripted LLM responses and the same scripted human inputs, then
    asserts the three gameplay-visible outputs match **exactly**:

    * the ``#public-log`` transcript (per-run ``thread_id`` normalised out),
    * the ``kill_log``,
    * the final ``winner``.

    A divergence here would falsify spec 002 §2.3's claim that the deployed
    Runtime is gameplay-indistinguishable from the in-process graph.
    """
    # ----- Run 1: LOCAL mode (real InProcessDiaryStore) -------------------
    _clear_remote_env(monkeypatch)
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the full mechanical RNG trajectory (role assignment, mafia target
    # selection order, kill order, any tie-breaks) so run 1 and run 2 produce
    # byte-identical public logs — that equality is what this test asserts.
    # Production uses the module-global ``random`` API; resetting it here
    # before the first call into the graph fixes the trajectory.
    random.seed(SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY)
    fake_haiku(AI_NAMES)
    local_fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    # Each run is an independent game session; give each its own zeroed career
    # store so neither sees the other's recorded game. Without this, run 1's
    # post-game record() would bump run 2's greeting/panel counts and the
    # byte-identical public-log assertion below would fail on the career lines.
    local_app = GraphiaApp(
        stats_store=LocalFileStatsStore(tmp_path / "career-local.json")
    )
    local_result = await _run_full_game_collecting(local_app, local_fake)

    # ----- Run 2: REMOTE mode (FakeAgentCoreClient + AgentCoreMemoryDiaryStore)
    # Re-script every deterministic input from scratch so run 2 sees byte-
    # identical inputs to run 1 — the fixtures' queues were drained by run 1.
    _set_remote_env(monkeypatch)
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Same load-bearing seed as run 1 — without it the two runs' unseeded
    # RNGs would diverge on role assignment and the equality asserts below
    # would fail. Reset the module-global ``random`` state to the same seed
    # before the first call into the graph for this mode.
    random.seed(SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY)
    fake_haiku(AI_NAMES)
    remote_fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    # Reset the proxying AgentCore client's class-level state, then wrap
    # build_graph so the freshly compiled graph (built with a *real*
    # AgentCoreMemoryDiaryStore over the mocked Memory SDK) is captured for
    # FakeAgentCoreClient.stream to proxy into.
    FakeAgentCoreClient.instances = []
    FakeAgentCoreClient.captured_graph = None

    import bedrock_agentcore.memory as _agentcore_memory
    import graphia.ui.app as app_module

    # Override the autouse safe_memory_client loud-failure default: from here
    # the diary store's lazy MemoryClient import resolves to the in-memory
    # fake, so the remote run genuinely drives AgentCoreMemoryDiaryStore.
    monkeypatch.setattr(_agentcore_memory, "MemoryClient", FakeMemoryClient)

    real_build_graph = app_module.build_graph

    def _wrapped_build_graph(config):
        # diary_store=None -> build_graph calls make_diary_store(config),
        # which (GRAPHIA_MEMORY_ID set) returns a real AgentCoreMemoryDiaryStore.
        graph, thread_id = real_build_graph(config)
        FakeAgentCoreClient.captured_graph = graph
        return graph, thread_id

    monkeypatch.setattr(app_module, "build_graph", _wrapped_build_graph)
    monkeypatch.setattr("graphia.driver.AgentCoreClient", FakeAgentCoreClient)

    remote_app = GraphiaApp(
        stats_store=LocalFileStatsStore(tmp_path / "career-remote.json")
    )
    remote_result = await _run_full_game_collecting(remote_app, remote_fake)

    # ----- Sanity: the remote run actually used the remote seams ----------
    assert len(FakeAgentCoreClient.instances) == 1, (
        "remote run should construct exactly one FakeAgentCoreClient"
    )
    assert FakeAgentCoreClient.instances[0].call_count >= 2, (
        "remote run should drive stream() for start + >=1 resume"
    )

    # ----- (c) Final winner matches exactly -------------------------------
    assert local_result["winner"] == remote_result["winner"], (
        f"winner diverged between modes: local={local_result['winner']!r} "
        f"remote={remote_result['winner']!r}"
    )
    # Seed 0's trajectory is a decisive ending, not an unresolved game.
    assert local_result["winner"] in ("law_abiding", "mafia", "draw"), (
        f"expected a decisive winner; got {local_result['winner']!r}"
    )

    # ----- (b) Kill log matches exactly -----------------------------------
    assert local_result["kill_log"] == remote_result["kill_log"], (
        f"kill_log diverged between modes:\n"
        f"  local  = {local_result['kill_log']!r}\n"
        f"  remote = {remote_result['kill_log']!r}"
    )
    # The seed-0 trajectory kills at least one player before the game ends —
    # guards against both runs trivially matching on an empty list.
    assert local_result["kill_log"], (
        "expected a non-empty kill_log for the seed-0 trajectory"
    )

    # ----- (a) Public-log transcript matches exactly ----------------------
    # Normalise the per-run thread_id out of each transcript; everything else
    # at seed 0 is RNG-derived and therefore identical between runs.
    local_log = _normalise(local_result["public_log"], local_result["thread_id"])
    remote_log = _normalise(remote_result["public_log"], remote_result["thread_id"])
    assert local_log == remote_log, (
        "public-log transcript diverged between local and remote modes "
        "(thread_id already normalised). This falsifies spec 002 §2.3's "
        "'no gameplay-visible behaviour differs' claim.\n"
        f"--- local ---\n{local_log}\n"
        f"--- remote ---\n{remote_log}"
    )
