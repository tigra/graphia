"""Slice 1 validation tests for spec 004 (robust /vote input validation).

Pins the strict ``/vote`` parser implemented in Sub 1.1 of
``context/spec/004-robust-vote-input-validation/tasks.md``. The cases here
cover functional-spec sections:

- §2.4 "Empty / bare ``/vote`` — distinct 'Usage' error" — bare ``/vote``,
  ``/vote `` (whitespace only), and ``/vote\\t\\t`` all surface the
  re-issued day_turn interrupt with ``error == "Usage: /vote <name>"`` and
  do not consume the human's turn.
- §2.6 "The slash command is strictly ``/vote`` followed by whitespace or
  end-of-line" — ``/voted yesterday`` and ``/votefor Alice`` are captured
  as ordinary spoken lines (no ``active_vote``, turn advances).

Self-vote pass-branch (§2.2, Sub 2.1) is also pinned here:

- ``test_vote_against_self_passes_executes_human`` drives the human's
  ``/vote <their-own-name>`` through to resolution with every AI voting
  Yes. Asserts that the human dies, ``kill_log`` gains exactly one
  execution record, ``active_vote`` is cleared, and the win-check node
  (``check_win_day``) fires exactly once on the resolution path.

Self-vote fail-branch (§2.2, Sub 2.2) is the symmetric pinning:

- ``test_vote_against_self_fails_human_survives`` mirrors the pass-branch
  but every AI ballot is ``Ballot(yes=False)`` and the human also votes
  No on themself. Asserts the ``kill_log`` is unchanged, the human is
  still alive, ``active_vote`` is cleared, ``day_votes_called`` ticked
  to 1, and the graph routed back to ``day_turn`` via ``check_win_day``
  (which still fires exactly once on the resolution path).

Nonexistent / dead-target cases (§2.3 + §2.5) are pinned in later
slices (Sub 3.1 / 3.2) and are intentionally out of scope for this file.

Bedrock is stubbed at the ``ChatBedrockConverse`` boundary via the unified
``fake_sonnet`` fixture; no test touches real AWS.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Pointing

# Role assignment is pinned via ``GRAPHIA_ROLE`` per ADR-006. The human is
# always Law-abiding here so the test never has to answer a ``kind="point"``
# Mafia-night interrupt before reaching the human's first Day turn.
AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"

# Architecture §6 "Determinism Posture & Testing Conventions": production
# uses an UNSEEDED module-global ``random`` for the role deal and the
# Night-kill tie-break/fallback ``random.choice``. ``_advance_until_human_day_turn``
# drives the REAL graph through Night 1 and the Day-1 speaker loop, and the
# dead-target test additionally resumes a full re-prompt cycle, so the number
# of super-steps a single ``graph.stream`` consumes before pausing is
# RNG-trajectory dependent. On some trajectories that count exceeds the test's
# ``recursion_limit`` of 50 and the drive raises ``GraphRecursionError``.
# Because the global RNG state at test entry shifts with collection order and
# with how much RNG earlier tests consumed, the test passes in isolation but
# can flake intermittently in the full suite. Per the §6-sanctioned mechanism
# (the same one ``tests/test_dual_mode_smoke.py`` uses), the at-risk test seeds
# the module-global RNG once, locally and explicitly, with this stable constant
# to pin a trajectory that stays within the recursion limit. This is NOT a
# ``GRAPHIA_SEED`` env protocol and does not weaken any assertion: the
# dead-target re-prompt behaviour (§2.5) holds identically under the seed.
SEED_VOTE_LOOP_WITHIN_RECURSION_LIMIT = 2024


# --------------------------------------------------------------------------
# Day-1 speaker-order stub.
#
# Each test in this file drives the graph until the human's first Day-1
# ``day_turn`` interrupt surfaces via ``_advance_until_human_day_turn``.
# That helper walks the graph forward one super-step at a time, capped by
# its own 20-iteration budget and LangGraph's ``recursion_limit=50``. With
# production's ``_shuffle_order`` running against the module-global RNG, the
# human can land anywhere in the speaker order; if late, a single
# ``_drive(Command(resume=...))`` after the human's turn may need to
# traverse many AI speakers + a fresh round shuffle before pausing again,
# occasionally exceeding the recursion budget and raising
# ``GraphRecursionError`` mid-test.
#
# Per architecture §6 "Determinism Posture & Testing Conventions" bullet 3,
# we monkeypatch ``_shuffle_order`` with a deterministic stub that pins the
# human at a known position (index 1). We deliberately avoid index 0 here:
# tests that observe post-human state changes (e.g. ``day_turn_index``
# advancing past ``pre_turn_index``, or a ``day_turn`` chunk firing after
# the human's failed vote reshuffles back to index 0) rely on at least one
# AI super-step running before the human's interrupt surfaces. With the
# human at index 1, exactly one AI speaker fires before the first human
# pause and at least one AI ``day_turn`` chunk also appears on the
# resolution path — keeping the recursion budget bounded while preserving
# every assertion about post-turn graph activity.
# --------------------------------------------------------------------------


def _human_first_factory():
    """Build a stateful ``_shuffle_order`` replacement that varies per call.

    The stub places the human at index 1 on the FIRST call and at index 2
    on every subsequent call. This shape satisfies three constraints
    simultaneously:

    1. **Recursion budget.** On the Day-1 entry shuffle (the first call),
       the human sits at index 1 — exactly one AI super-step runs before
       the helper hits the human's interrupt. ``_advance_until_human_day_turn``
       therefore pauses in two ``_drive`` cycles, well below the 50
       super-step ``recursion_limit`` even on the slowest test.
    2. **Post-resolution ``day_turn`` chunk.** The self-vote-fails test
       asserts that ``day_turn`` re-fires after the failed vote
       reshuffles back to round-start. With the human at index 2 on the
       second call (the reshuffle inside ``resolve_vote``'s failure
       branch), two AI ``day_turn`` chunks emit before the human's
       interrupt — preserving the assertion's behaviour.
    3. **"Turn was consumed" assertions.** The speech tests assert
       ``day_turn_index != pre_turn_index`` after the human's speech is
       captured. ``pre_turn_index`` is observed as 1 (the human's index
       on call #1). After speech, the round wraps, ``day_turn_index``
       resets to 0, a fresh ``_shuffle_order`` call returns the human at
       index 2, and the helper pauses with ``day_turn_index = 2`` — so
       the post-snapshot is 2, never equal to pre-snapshot 1.

    The factory pattern keeps the per-test counter isolated: each test
    installs its own instance via ``monkeypatch.setattr``, so call counts
    never leak between tests.
    """

    call_count = {"n": 0}

    def _shuffle(players):
        call_count["n"] += 1
        n = call_count["n"]
        alive = [pid for pid, p in players.items() if p.is_alive]
        human_id = next(
            (pid for pid, p in players.items() if p.is_human),
            None,
        )
        if human_id is None or human_id not in alive:
            # Defensive: human dead or not yet rostered; fall back to
            # roster order so the stub never raises.
            return alive
        others = [pid for pid in alive if pid != human_id]
        if not others:
            return [human_id]
        # Place the human at index 1 on call #1, at index 2 on calls #2+.
        # See the docstring above for why these positions are chosen.
        if n == 1:
            return [others[0], human_id, *others[1:]]
        if len(others) >= 2:
            return [others[0], others[1], human_id, *others[2:]]
        # Only one other player alive — fall back to "human second".
        return [others[0], human_id]

    return _shuffle


# --------------------------------------------------------------------------
# Helpers — mirrored from tests/test_slice7_vote.py so this file stays
# self-contained without coupling the two test modules.
# --------------------------------------------------------------------------


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    """Return the first pending interrupt value on the graph, or None."""
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        interrupts = task.interrupts or ()
        for interrupt_obj in interrupts:
            return interrupt_obj.value
    return None


def _drive(graph, run_config, payload) -> None:
    """Exhaust ``graph.stream`` super-steps until the next pause.

    A low ``recursion_limit`` caps runaway day/night loops at 50 super-steps
    so a single drive never hangs.
    """
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 50)
    for _ in graph.stream(payload, bounded, stream_mode="updates"):
        pass


def _players(graph, run_config) -> dict:
    return graph.get_state(run_config).values.get("players", {})


def _alive_law_abiding_ai_id(graph, run_config) -> str:
    players = _players(graph, run_config)
    ids = [
        p.id
        for p in players.values()
        if p.is_alive and p.role == "law_abiding" and not p.is_human
    ]
    assert ids, "expected at least one alive AI Law-abiding"
    return ids[0]


def _advance_until_human_day_turn(graph, run_config, fake) -> None:
    """Drive the graph through Night 1 and AI Day-1 turns to the human's slot.

    AI speakers fall through to the unified Sonnet fake's ``DayAction``
    queue (scripted as ``speak`` lines below); Night 1 ``Pointing`` calls
    are resolved at invoke time against live graph state via the override
    installed on the fake's ``_invoke``.
    """
    # First stream: up to the name interrupt.
    _drive(graph, run_config, {"messages": []})
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    # Patch the unified Sonnet fake so Night-1 Pointing resolves against
    # live state — we don't know the role-assigned UUIDs until after the
    # name resume runs.
    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            la_id = _alive_law_abiding_ai_id(graph, run_config)
            return Pointing(target_id=la_id)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    # Resume the name interrupt — graph runs through Night 1 and pauses on
    # the first Day-1 day_turn interrupt (either an AI or the human).
    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # Pre-stock a large queue of generic AI speech actions so any AI turn
    # before (or after) the human cleanly advances without hitting the
    # empty-queue assertion in the unified fake. The Ballot queue is unused
    # by Slice 1 tests (no vote ever starts) but is pre-seeded defensively.
    fake._queues[DayAction] = [
        DayAction(kind="speak", text=f"AI speaks ({i}).") for i in range(40)
    ]
    fake._last.pop(DayAction, None)

    # Step through AI day_turn super-steps until the human's turn surfaces.
    # No human day_turn pauses the graph; AI turns return without
    # interrupting, so each ``_drive(None)`` advances zero-or-more AI
    # turns before pausing on the next interrupt (which is either another
    # AI iteration or — eventually — the human's day_turn).
    for _ in range(20):
        iv = _collect_interrupt(graph, run_config)
        if (
            iv is not None
            and iv.get("kind") == "day_turn"
            and iv.get("speaker_name") == HUMAN_NAME
        ):
            return
        # No human interrupt yet — drive once with no resume value to
        # advance to the next super-step.
        _drive(graph, run_config, None)
    raise AssertionError("human day_turn never surfaced within budget")


# --------------------------------------------------------------------------
# Test 1: bare/whitespace-only /vote → usage hint re-interrupt, turn intact.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed_input",
    ["/vote", "/vote ", "/vote\t\t"],
    ids=["bare", "trailing_space", "trailing_tabs"],
)
def test_vote_empty_name_shows_usage_hint(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
    malformed_input: str,
) -> None:
    """Bare / whitespace-only ``/vote`` re-prompts with the usage hint.

    The human's turn must NOT be consumed: ``day_turn_index``,
    ``day_rounds``, and ``active_vote`` are all unchanged from before the
    rejected input. The re-issued day_turn interrupt payload carries
    ``error == "Usage: /vote <name>"`` so the UI can surface the hint.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _advance_until_human_day_turn(graph, run_config, fake)

    # Sanity: the first prompt should be the plain base payload (no error).
    iv_pre = _collect_interrupt(graph, run_config)
    assert iv_pre is not None and iv_pre.get("kind") == "day_turn"
    assert iv_pre.get("speaker_name") == HUMAN_NAME
    assert "error" not in iv_pre, (
        f"first human prompt must not carry an error field; got {iv_pre!r}"
    )

    # Snapshot state before the rejected input.
    pre_state = graph.get_state(run_config).values
    pre_turn_index = pre_state.get("day_turn_index")
    pre_rounds = pre_state.get("day_rounds")
    assert pre_state.get("active_vote") is None

    # Resume with the malformed input → day_turn re-issues the interrupt
    # via the inner while-loop and the graph pauses on the new payload.
    _drive(graph, run_config, Command(resume=malformed_input))

    iv_post = _collect_interrupt(graph, run_config)
    assert iv_post is not None, (
        "graph should still be paused on a re-issued day_turn interrupt"
    )
    assert iv_post.get("kind") == "day_turn"
    assert iv_post.get("speaker_name") == HUMAN_NAME
    assert iv_post.get("error") == "Usage: /vote <name>", (
        f"expected 'Usage: /vote <name>' on re-issued interrupt; "
        f"got {iv_post!r}"
    )

    # Turn was NOT consumed.
    post_state = graph.get_state(run_config).values
    assert post_state.get("day_turn_index") == pre_turn_index
    assert post_state.get("day_rounds") == pre_rounds
    assert post_state.get("active_vote") is None

    # Cleanly exit the re-prompt loop so the test teardown isn't left with
    # a paused worker waiting on the human.
    _drive(graph, run_config, Command(resume="(stays silent.)"))


# --------------------------------------------------------------------------
# Test 2: "/voted yesterday" is speech, not a /vote command.
# --------------------------------------------------------------------------


def test_voted_yesterday_is_speech(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/voted yesterday`` is captured as the human's spoken line.

    The strict parser only fires on the literal ``/vote`` token; ``/voted``
    is a different token and falls through to the speech path. No
    ``active_vote`` is set and the human's turn advances (consumed by
    speech, not by a vote ritual).
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _advance_until_human_day_turn(graph, run_config, fake)

    pre_state = graph.get_state(run_config).values
    pre_turn_index = pre_state.get("day_turn_index")
    assert pre_state.get("active_vote") is None

    _drive(graph, run_config, Command(resume="/voted yesterday"))

    # The human's speech must appear verbatim in the message log as an
    # AIMessage with the human's name as speaker — that is the production
    # contract for a spoken Day line.
    messages = graph.get_state(run_config).values.get("messages", [])
    human_lines = [
        m
        for m in messages
        if isinstance(m, AIMessage)
        and getattr(m, "name", None) == HUMAN_NAME
        and m.content == "/voted yesterday"
    ]
    assert human_lines, (
        f"expected human AIMessage with content '/voted yesterday'; "
        f"messages: {[(getattr(m, 'name', None), getattr(m, 'content', None)) for m in messages]!r}"
    )

    post_state = graph.get_state(run_config).values
    # Speech path never sets active_vote.
    assert post_state.get("active_vote") is None
    # Turn was consumed: the index advanced past the human's slot (further
    # AI turns may also have run, so we only assert "moved" not "==pre+1").
    assert post_state.get("day_turn_index") != pre_turn_index, (
        "human turn should have been consumed by the speech path"
    )


# --------------------------------------------------------------------------
# Test 3: "/votefor Alice" is speech, not a /vote command.
# --------------------------------------------------------------------------


def test_votefor_alice_is_speech(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/votefor Alice`` is captured as the human's spoken line.

    Like ``/voted``, ``/votefor`` is a distinct token from ``/vote`` and
    falls through to the speech path — even though the substring ``/vote``
    is a prefix of it. No vote is initiated.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _advance_until_human_day_turn(graph, run_config, fake)

    pre_state = graph.get_state(run_config).values
    pre_turn_index = pre_state.get("day_turn_index")
    assert pre_state.get("active_vote") is None

    _drive(graph, run_config, Command(resume="/votefor Alice"))

    messages = graph.get_state(run_config).values.get("messages", [])
    human_lines = [
        m
        for m in messages
        if isinstance(m, AIMessage)
        and getattr(m, "name", None) == HUMAN_NAME
        and m.content == "/votefor Alice"
    ]
    assert human_lines, (
        f"expected human AIMessage with content '/votefor Alice'; "
        f"messages: {[(getattr(m, 'name', None), getattr(m, 'content', None)) for m in messages]!r}"
    )

    post_state = graph.get_state(run_config).values
    assert post_state.get("active_vote") is None
    assert post_state.get("day_turn_index") != pre_turn_index, (
        "human turn should have been consumed by the speech path"
    )


# --------------------------------------------------------------------------
# Test 4 (Sub 2.1): self-vote with all-Yes ballots executes the human.
# --------------------------------------------------------------------------


def _drive_capture(graph, run_config, payload) -> list[dict]:
    """Stream the graph and return every super-step update chunk.

    Mirrors ``_drive`` but collects the per-node update dicts emitted by
    ``stream_mode="updates"`` so the caller can audit which graph nodes
    actually fired during the drive. Used by the self-vote test to verify
    that ``check_win_day`` ran exactly once on the resolution path (and
    not, say, twice via a duplicate fan-out wiring bug).
    """
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 50)
    chunks: list[dict] = []
    for chunk in graph.stream(payload, bounded, stream_mode="updates"):
        chunks.append(chunk)
    return chunks


def test_vote_against_self_passes_executes_human(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human votes against themselves with unanimous Yes → human is executed.

    Pins §2.2's pass branch end-to-end:

    1. Day 1 has reached the human's ``day_turn`` interrupt (after
       Night 1 removed one Law-abiding AI).
    2. The human resumes with ``/vote ALICE`` — uppercase to exercise the
       case-insensitive fuzzy match on their own name.
    3. The first ballot polled is the human's own (insertion order: the
       human was added by ``collect_name`` before any AI). The human
       answers Yes to executing themself.
    4. All AI voters return ``Ballot(yes=True)`` from the unified Sonnet
       fake's pre-seeded queue, so the tally is unanimous Yes.
    5. ``resolve_vote`` flips the human's ``is_alive`` to False, appends a
       single ``KillRecord`` with ``cause='execution'`` and the human's
       name, and clears ``active_vote``.
    6. ``check_win_day`` fires exactly once on the resolution path. The
       post-execution parity depends on RNG-driven AI role placement;
       both routes — game continues via ``day_close`` (no winner yet)
       and immediate END (win check finds parity) — are well-formed,
       and the test accepts either outcome.

    No production code is modified by this test. If the assertions fail,
    the failure pinpoints a real self-vote bug — fixing it is Sub 2.4's
    job.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # Drive to the human's first Day-1 turn. The helper pre-seeds 40
    # generic ``DayAction(kind='speak')`` outputs so any AI turn before
    # the human's slot clears without hitting the empty-queue assertion.
    _advance_until_human_day_turn(graph, run_config, fake)

    # Snapshot pre-vote state so we can compare the kill_log delta.
    pre_state = graph.get_state(run_config).values
    pre_kill_log = list(pre_state.get("kill_log", []))
    assert pre_state.get("active_vote") is None
    human_id = pre_state["human_id"]
    human = pre_state["players"][human_id]
    assert human.is_alive is True
    assert human.name == HUMAN_NAME

    # Force every AI ballot to Yes. The unified fake's Ballot queue is
    # consumed in FIFO order by ``collect_votes``; the human's ballot is
    # served via the interrupt path and never touches this queue.
    fake._queues[Ballot] = [Ballot(yes=True)] * 20
    fake._last.pop(Ballot, None)

    # Resume the human's day_turn with /vote against themself. Uppercase
    # exercises the case-insensitive fuzzy match in ``_fuzzy_match_alive``.
    _drive(graph, run_config, Command(resume=f"/vote {HUMAN_NAME.upper()}"))

    # The vote sub-graph now polls voters in roster insertion order; the
    # human was inserted first by ``collect_name`` so they vote first.
    iv_vote = _collect_interrupt(graph, run_config)
    assert iv_vote is not None, "expected a vote interrupt for the human"
    assert iv_vote.get("kind") == "vote", (
        f"expected the first poll to interrupt for the human's own ballot; "
        f"got {iv_vote!r}"
    )
    assert iv_vote.get("voter_id") == human_id, (
        f"expected human as the first voter (roster order); got "
        f"voter_id={iv_vote.get('voter_id')!r}, human_id={human_id!r}"
    )
    assert iv_vote.get("target_id") == human_id, (
        "vote target should be the human themself"
    )

    # Resume with "y" → 5 AI ballots flow from the Yes-only queue, then
    # resolve_vote → check_win_day. We CAPTURE every super-step update so
    # we can assert check_win_day fired exactly once on this drive.
    chunks = _drive_capture(graph, run_config, Command(resume="y"))

    # --- Assertions ------------------------------------------------------

    state = graph.get_state(run_config).values

    # (a) kill_log gained exactly one execution record naming the human.
    post_kill_log = list(state.get("kill_log", []))
    new_records = post_kill_log[len(pre_kill_log):]
    exec_records = [
        r for r in new_records if r.get("cause") == "execution"
    ]
    assert len(exec_records) == 1, (
        f"expected exactly one new execution record; got {exec_records!r} "
        f"(full delta: {new_records!r})"
    )
    assert exec_records[0]["name"] == HUMAN_NAME, (
        f"execution record should name the human; got {exec_records[0]!r}"
    )

    # (b) The human is now dead.
    players = state.get("players", {})
    assert players[human_id].is_alive is False, (
        "human's is_alive flag should be False after self-execution"
    )

    # (c) active_vote was cleared by resolve_vote.
    assert state.get("active_vote") is None, (
        f"active_vote should be None after resolution; got "
        f"{state.get('active_vote')!r}"
    )

    # (d) check_win_day fired exactly once on the resolution path. Each
    # stream chunk in ``stream_mode='updates'`` is keyed by node name —
    # we count the chunks naming the day-side win-check node.
    win_day_fires = sum(
        1 for chunk in chunks if "check_win_day" in chunk
    )
    assert win_day_fires == 1, (
        f"check_win_day should fire exactly once on the resolution path; "
        f"fired {win_day_fires} times. Chunk node-keys (in order): "
        f"{[list(c.keys()) for c in chunks]!r}"
    )

    # (e) The post-check routing produced a well-formed state. Either the
    # game ended (winner set, graph at END) or it continued via day_close.
    # The same chunk stream tells us which path was taken.
    winner = state.get("winner")
    snapshot_next = graph.get_state(run_config).next
    day_close_fired = any("day_close" in chunk for chunk in chunks)
    end_screen_fired = any("end_screen" in chunk for chunk in chunks)

    if winner is not None:
        # Game ended — the win-check found a winning side.
        assert winner in ("law_abiding", "mafia"), (
            f"winner must be a side name; got {winner!r}"
        )
        assert end_screen_fired, (
            "winner is set but end_screen never fired in the chunk stream"
        )
        assert snapshot_next == (), (
            f"graph should be at END once a winner is set; next was "
            f"{snapshot_next!r}"
        )
    else:
        # No winner — game continues; day_close should have routed off the
        # win-check, closing the Day. (The graph may have proceeded into
        # Night 2 before pausing again; that's fine.)
        assert day_close_fired, (
            "no winner was set but day_close did not fire after the "
            "win-check — the graph state is inconsistent. Chunks: "
            f"{[list(c.keys()) for c in chunks]!r}"
        )


# --------------------------------------------------------------------------
# Test 5 (Sub 2.2): self-vote with all-No ballots fails; human survives.
# --------------------------------------------------------------------------


def test_vote_against_self_fails_human_survives(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human votes against themself; all No ballots → vote fails, human lives.

    Symmetric fail-branch counterpart to Sub 2.1. Pins §2.2's failure path
    end-to-end:

    1. Day 1 has reached the human's ``day_turn`` interrupt (after Night 1
       removed one Law-abiding AI). Same setup as the pass-branch test.
    2. The human resumes with ``/vote ALICE`` — uppercase exercises the
       case-insensitive fuzzy match.
    3. The first ballot polled is the human's own (roster insertion order
       — the human was added by ``collect_name`` before any AI). The
       human answers ``"n"``.
    4. Every AI ballot polled afterwards returns ``Ballot(yes=False)`` from
       the unified Sonnet fake's pre-seeded queue, so the tally is
       unanimous No.
    5. ``resolve_vote`` posts the "vote fails" line, clears ``active_vote``,
       bumps ``day_votes_called`` from 0 → 1, resets ``day_turn_index``,
       reshuffles ``day_order`` — and crucially does NOT append any
       ``KillRecord``.
    6. ``check_win_day`` fires exactly once on the resolution path. No
       winner can emerge (nobody died), so the graph routes back to
       ``day_turn`` (per ``route_after_resolve_vote``: no execution this
       cycle, ``day_votes_called=1 < 3``).

    No production code is modified by this test. If the assertions fail,
    the failure pinpoints a real self-vote-fail bug — fixing it is
    Sub 2.4's job.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # Drive to the human's first Day-1 turn. The helper pre-seeds 40
    # generic ``DayAction(kind='speak')`` outputs so any AI turn before
    # the human's slot — and any AI turn AFTER the failed vote routes
    # back to ``day_turn`` — cleanly advances without hitting the
    # empty-queue assertion.
    _advance_until_human_day_turn(graph, run_config, fake)

    # Snapshot pre-vote state so we can compare the kill_log delta and
    # the day_votes_called delta.
    pre_state = graph.get_state(run_config).values
    pre_kill_log = list(pre_state.get("kill_log", []))
    pre_votes_called = pre_state.get("day_votes_called", 0)
    assert pre_state.get("active_vote") is None
    human_id = pre_state["human_id"]
    human = pre_state["players"][human_id]
    assert human.is_alive is True
    assert human.name == HUMAN_NAME

    # Force every AI ballot to No. The unified fake's Ballot queue is
    # consumed in FIFO order by ``collect_votes``; the human's ballot is
    # served via the interrupt path and never touches this queue.
    fake._queues[Ballot] = [Ballot(yes=False)] * 20
    fake._last.pop(Ballot, None)

    # Resume the human's day_turn with /vote against themself. Uppercase
    # exercises the case-insensitive fuzzy match in ``_fuzzy_match_alive``.
    _drive(graph, run_config, Command(resume=f"/vote {HUMAN_NAME.upper()}"))

    # The vote sub-graph polls voters in roster insertion order; the
    # human was inserted first by ``collect_name`` so they vote first.
    iv_vote = _collect_interrupt(graph, run_config)
    assert iv_vote is not None, "expected a vote interrupt for the human"
    assert iv_vote.get("kind") == "vote", (
        f"expected the first poll to interrupt for the human's own ballot; "
        f"got {iv_vote!r}"
    )
    assert iv_vote.get("voter_id") == human_id, (
        f"expected human as the first voter (roster order); got "
        f"voter_id={iv_vote.get('voter_id')!r}, human_id={human_id!r}"
    )
    assert iv_vote.get("target_id") == human_id, (
        "vote target should be the human themself"
    )

    # Resume with "n" → 5 AI ballots flow from the No-only queue, then
    # resolve_vote (failure path) → check_win_day → route back to
    # day_turn. CAPTURE every super-step update so we can assert
    # check_win_day fired exactly once on this drive and that the
    # post-resolution routing landed on day_turn (not day_close, not END).
    chunks = _drive_capture(graph, run_config, Command(resume="n"))

    # --- Assertions ------------------------------------------------------

    state = graph.get_state(run_config).values

    # (a) kill_log unchanged — no execution, no death of any kind.
    post_kill_log = list(state.get("kill_log", []))
    assert post_kill_log == pre_kill_log, (
        f"kill_log should be unchanged on a failed vote; "
        f"pre={pre_kill_log!r}, post={post_kill_log!r}"
    )

    # (b) The human is still alive.
    players = state.get("players", {})
    assert players[human_id].is_alive is True, (
        "human's is_alive flag should remain True after a failed self-vote"
    )

    # (c) active_vote was cleared by resolve_vote.
    assert state.get("active_vote") is None, (
        f"active_vote should be None after resolution; got "
        f"{state.get('active_vote')!r}"
    )

    # (d) day_votes_called incremented by exactly 1 (this is the first
    # failed vote of Day 1 against a starting value of 0).
    post_votes_called = state.get("day_votes_called", 0)
    assert post_votes_called == pre_votes_called + 1, (
        f"day_votes_called should bump by exactly 1 on a failed vote; "
        f"pre={pre_votes_called}, post={post_votes_called}"
    )

    # (e) check_win_day fired exactly once on the resolution path. Each
    # stream chunk in ``stream_mode='updates'`` is keyed by node name —
    # we count the chunks naming the day-side win-check node.
    win_day_fires = sum(
        1 for chunk in chunks if "check_win_day" in chunk
    )
    assert win_day_fires == 1, (
        f"check_win_day should fire exactly once on the resolution path; "
        f"fired {win_day_fires} times. Chunk node-keys (in order): "
        f"{[list(c.keys()) for c in chunks]!r}"
    )

    # (f) Graph routed back to day_turn (not day_close, not END). A failed
    # vote keeps the Day going: no execution this cycle, votes_called=1<3,
    # rounds<cap. ``day_turn`` MUST have fired at least once after the
    # win-check; ``day_close`` MUST NOT have fired; ``end_screen`` MUST
    # NOT have fired; no winner was set.
    day_turn_fired = any("day_turn" in chunk for chunk in chunks)
    day_close_fired = any("day_close" in chunk for chunk in chunks)
    end_screen_fired = any("end_screen" in chunk for chunk in chunks)
    assert day_turn_fired, (
        "graph should route back to day_turn after a failed vote; "
        f"chunk node-keys: {[list(c.keys()) for c in chunks]!r}"
    )
    assert not day_close_fired, (
        "day_close must NOT fire after a failed vote that is below the "
        "votes-called cap; chunk node-keys: "
        f"{[list(c.keys()) for c in chunks]!r}"
    )
    assert not end_screen_fired, (
        "end_screen must NOT fire after a failed self-vote — nobody died, "
        "so no win condition can have triggered; chunk node-keys: "
        f"{[list(c.keys()) for c in chunks]!r}"
    )
    assert state.get("winner") is None, (
        f"no winner should be set after a failed vote; got "
        f"{state.get('winner')!r}"
    )


# --------------------------------------------------------------------------
# Test 6 (Sub 3.1): /vote against a nonexistent name re-prompts; turn intact.
# --------------------------------------------------------------------------


def test_vote_nonexistent_name_reprompts(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/vote zzz`` re-prompts with "No such player. Try again." (§2.3).

    The strict parser accepts the slash-command form (``/vote`` + whitespace
    + non-empty remainder), so ``zzz`` is passed to ``_fuzzy_match_alive``.
    No alive player's name contains ``zzz`` as a substring, so the helper
    returns ``None`` and ``day_turn`` re-issues its interrupt with the
    "No such player." error.

    Critically, the human's turn must NOT be consumed: ``day_turn_index``,
    ``day_rounds``, and ``active_vote`` are all unchanged from before the
    rejected input.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _advance_until_human_day_turn(graph, run_config, fake)

    # Sanity: the first prompt should be the plain base payload (no error).
    iv_pre = _collect_interrupt(graph, run_config)
    assert iv_pre is not None and iv_pre.get("kind") == "day_turn"
    assert iv_pre.get("speaker_name") == HUMAN_NAME
    assert "error" not in iv_pre

    # Snapshot state before the rejected input.
    pre_state = graph.get_state(run_config).values
    pre_turn_index = pre_state.get("day_turn_index")
    pre_rounds = pre_state.get("day_rounds")
    assert pre_state.get("active_vote") is None

    # Belt-and-braces: confirm no alive player's name matches "zzz" so the
    # assertion below is testing what we think it is.
    alive_names = [
        p.name for p in pre_state["players"].values() if p.is_alive
    ]
    assert not any("zzz" in n.lower() for n in alive_names), (
        f"sanity: 'zzz' must not be a substring of any alive name; "
        f"got alive_names={alive_names!r}"
    )

    # Resume with a nonexistent target → day_turn re-issues its interrupt
    # via the inner while-loop and the graph pauses on the new payload.
    _drive(graph, run_config, Command(resume="/vote zzz"))

    iv_post = _collect_interrupt(graph, run_config)
    assert iv_post is not None, (
        "graph should still be paused on a re-issued day_turn interrupt"
    )
    assert iv_post.get("kind") == "day_turn"
    assert iv_post.get("speaker_name") == HUMAN_NAME
    assert iv_post.get("error") == "No such player. Try again.", (
        f"expected 'No such player. Try again.' on re-issued interrupt; "
        f"got {iv_post!r}"
    )

    # Turn was NOT consumed.
    post_state = graph.get_state(run_config).values
    assert post_state.get("day_turn_index") == pre_turn_index
    assert post_state.get("day_rounds") == pre_rounds
    assert post_state.get("active_vote") is None

    # Cleanly exit the re-prompt loop so test teardown isn't left with a
    # paused worker waiting on the human.
    _drive(graph, run_config, Command(resume="(stays silent.)"))


# --------------------------------------------------------------------------
# Test 7 (Sub 3.2): /vote against a dead player re-prompts; turn intact.
# --------------------------------------------------------------------------


def test_vote_dead_player_reprompts(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/vote <dead-name>`` re-prompts with the same "No such player" error.

    Pins §2.5: dead players are filtered out by ``_fuzzy_match_alive``'s
    ``is_alive`` predicate, so they look identical to nonexistent names
    from the parser's point of view — the error string is intentionally
    the same as Test 6's so the UI never leaks which names are dead vs.
    never-existed.

    Setup: after reaching the human's first ``day_turn`` interrupt, pick an
    alive AI player who is NOT the human and NOT the Night-1 victim, mark
    them ``is_alive=False`` via ``graph.update_state`` (mutating only the
    chosen player's dataclass field — the other state channels stay
    untouched), then resume the interrupt with ``/vote <that-dead-name>``.

    Asserts: the next interrupt carries ``error == "No such player. Try
    again."``, ``day_turn_index`` / ``day_rounds`` / ``active_vote`` are
    all unchanged from before the rejected input (turn not consumed).
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Pin the Day-1 speaker order so the human's day_turn surfaces on the
    # first super-step after the name resume.
    monkeypatch.setattr(day_nodes, "_shuffle_order", _human_first_factory())
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    # Pin the mechanical-RNG trajectory the graph-drive below will consume
    # (role deal + Night-1 kill tie-break/fallback) so ``_advance_until_human_day_turn``
    # and the subsequent re-prompt drive stay within ``recursion_limit=50``.
    # See the module-level constant's docstring (architecture §6). Seeded here,
    # after env/role setup, right before the graph is built and driven —
    # production reads the module-global ``random`` directly, so this fixes the
    # whole downstream trajectory without touching production code.
    random.seed(SEED_VOTE_LOOP_WITHIN_RECURSION_LIMIT)

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # Before advancing into Day 1, install a wrapper around ``_fuzzy_match_alive``
    # that mirrors the production helper but pretends one chosen AI is dead.
    # This is the cleanest equivalent to a state patch: production already
    # filters on ``p.is_alive`` inside the real helper, so injecting an extra
    # "treat this name as dead" predicate is behaviour-equivalent to flipping
    # the dataclass field — without fighting LangGraph's checkpointer over
    # how to update a dict-reducer channel mid-interrupt.
    #
    # We pick the dead-target NAME from the fixed ``AI_NAMES`` roster handed
    # to ``fake_haiku`` above — "Finn" is guaranteed to be present regardless
    # of how RNG assigns roles. The ``_fuzzy_match_alive`` wrapper below
    # shadows Finn as dead in a local copy of the players dict; the live
    # state's actual is_alive flag for Finn doesn't matter (the shadow path
    # always wins inside the helper).
    dead_target_name = "Finn"

    import graphia.nodes.day as day_module

    real_fuzzy = day_module._fuzzy_match_alive

    def _fuzzy_match_alive_with_dead_finn(
        players: dict, needle: str
    ) -> str | None:
        # Identify Finn's id from the live roster and pretend he's dead.
        finn_id = next(
            (pid for pid, p in players.items() if p.name == dead_target_name),
            None,
        )
        if finn_id is None:
            return real_fuzzy(players, needle)
        # Build a shadow dict where Finn's ``is_alive`` is False, then defer
        # to the real helper so the substring-matching / uniqueness logic is
        # exercised exactly as production runs it.
        from dataclasses import replace

        shadow = dict(players)
        shadow[finn_id] = replace(shadow[finn_id], is_alive=False)
        return real_fuzzy(shadow, needle)

    monkeypatch.setattr(
        day_module, "_fuzzy_match_alive", _fuzzy_match_alive_with_dead_finn
    )

    _advance_until_human_day_turn(graph, run_config, fake)

    # Sanity: the first prompt should be the plain base payload (no error).
    iv_pre = _collect_interrupt(graph, run_config)
    assert iv_pre is not None and iv_pre.get("kind") == "day_turn"
    assert iv_pre.get("speaker_name") == HUMAN_NAME
    assert "error" not in iv_pre

    # Snapshot state before the rejected input.
    pre_state = graph.get_state(run_config).values
    pre_turn_index = pre_state.get("day_turn_index")
    pre_rounds = pre_state.get("day_rounds")
    assert pre_state.get("active_vote") is None

    # Sanity: ``dead_target_name`` is a real roster name (so a /vote against
    # it would resolve to a valid id if it were still alive — that is what
    # makes this a "dead player" rejection rather than a "nonexistent name"
    # rejection).
    roster_names = [p.name for p in pre_state["players"].values()]
    assert dead_target_name in roster_names, (
        f"sanity: '{dead_target_name}' should be in the roster; "
        f"got {roster_names!r}"
    )

    # Resume the human's turn with /vote against the (shadow-)dead player.
    _drive(graph, run_config, Command(resume=f"/vote {dead_target_name}"))

    iv_post = _collect_interrupt(graph, run_config)
    assert iv_post is not None, (
        "graph should still be paused on a re-issued day_turn interrupt"
    )
    assert iv_post.get("kind") == "day_turn"
    assert iv_post.get("speaker_name") == HUMAN_NAME
    assert iv_post.get("error") == "No such player. Try again.", (
        f"expected 'No such player. Try again.' on the re-issued interrupt "
        f"(dead-target should look identical to nonexistent); got "
        f"{iv_post!r}"
    )

    # Turn was NOT consumed.
    post_state = graph.get_state(run_config).values
    assert post_state.get("day_turn_index") == pre_turn_index
    assert post_state.get("day_rounds") == pre_rounds
    assert post_state.get("active_vote") is None

    # Cleanly exit the re-prompt loop so test teardown isn't left with a
    # paused worker waiting on the human.
    _drive(graph, run_config, Command(resume="(stays silent.)"))
