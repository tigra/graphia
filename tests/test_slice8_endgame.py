"""Slice 8 tests: win-condition detection and end-of-game screen.

Three graph-level scenarios driving the compiled graph directly (no
Textual) mirroring the pattern used in ``test_slice7_vote.py``:

1. ``test_law_abiding_wins_when_all_mafia_executed`` — Day votes succeed
   in sequence against each Mafia. Once the last Mafia is executed, the
   win check (at ``check_win_day``) routes to ``end_screen`` and the game
   terminates cleanly.

2. ``test_mafia_wins_when_parity_reached`` — Mafia kills Law-abiding on
   successive Nights while Day votes fail (or target Law-abiding). When
   ``alive_mafia >= alive_law_abiding`` the night-side win check routes
   to ``end_screen``.

3. ``test_endgame_message_contains_kill_log_and_roster`` — piggybacks on
   the Law-abiding win to assert the final Moderator message includes the
   kill-log (chronological) AND the full roster reveal with roles.

Plus a single Textual-pilot smoke test asserting the "Game over." marker
renders in ``#public-log`` and any keypress exits the app.

All LLM calls go through the unified ``fake_large`` fixture (DayAction,
Ballot, Pointing served from one fake keyed on schema), plus
``fake_small`` for name generation. No test touches real Bedrock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import SystemMessage
from langgraph.types import Command

from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Pointing
from graphia.prompts import (
    ENDGAME_HEADER_KILLS,
    ENDGAME_HEADER_ROSTER,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
)
from graphia.ui.app import GraphiaApp

HUMAN_NAME = "Alice"
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]


# --------------------------------------------------------------------------
# Helpers (mirrored from test_slice7_vote.py)
# --------------------------------------------------------------------------


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _system_contents(graph, run_config) -> list[str]:
    state = graph.get_state(run_config).values
    return [
        m.content
        for m in state.get("messages", [])
        if isinstance(m, SystemMessage)
    ]


def _players(graph, run_config) -> dict:
    return graph.get_state(run_config).values.get("players", {})


def _alive_ai_ids_by_role(graph, run_config, role: str) -> list[str]:
    players = _players(graph, run_config)
    return [
        p.id
        for p in players.values()
        if p.is_alive and p.role == role and not p.is_human
    ]


def _drive(graph, run_config, payload) -> None:
    """Stream the graph with ``payload`` until the next pause.

    We cap ``recursion_limit`` at 200 — generous enough for the entire
    Slice-8 end-to-end scenarios (several Days + Nights + vote rounds +
    end_screen) but low enough that a runaway loop fails quickly rather
    than burning minutes.
    """
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 200)
    for _ in graph.stream(payload, bounded, stream_mode="updates"):
        pass


def _advance_until(
    graph,
    run_config,
    *,
    stop: Callable[[], bool],
    interrupt_responder: Callable[[dict[str, Any]], Any],
    budget: int = 200,
) -> None:
    """Drive the graph one super-step at a time until ``stop()`` is True.

    Hard cap on iterations so a failing test surfaces as a budget-exhaustion
    assertion rather than an infinite loop. When the graph reaches END
    (``snapshot.next == ()``) the driver exits normally.
    """
    for _ in range(budget):
        if stop():
            return
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            # Graph reached END (or is idle with nothing pending).
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            # No pending interrupt but the graph still has .next — drive
            # once with None to let it settle and re-poll on the next loop.
            _drive(graph, run_config, None)
            continue
        resume = interrupt_responder(interrupt_value)
        _drive(graph, run_config, Command(resume=resume))


# --------------------------------------------------------------------------
# Test 1: Law-abiding Citizens win when every Mafia has been executed.
# --------------------------------------------------------------------------


def test_law_abiding_wins_when_all_mafia_executed(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequential Day votes remove both Mafia; end_screen announces Law win.

    Strategy:
    - The ``FakeLargeUnified`` fake's DayAction queue is scripted to
      always return ``kind="vote"`` targeting the *first alive Mafia AI*.
      Because we override ``._invoke`` with a live-state reader, each vote
      action is resolved at call-time — no need to pre-compute UUIDs.
    - Ballots are all Yes, so every vote succeeds and executes its target.
    - Night pointings target the first alive Law-abiding AI.
    - The human (Law-abiding) answers ``"..."`` for day_turn interrupts
      and ``"yes"`` for vote interrupts.

    Expected trajectory: Night 1 kills one Law-abiding → Day 1 executes
    first Mafia → Night 2 kills another Law-abiding → Day 2 executes
    second Mafia → ``check_win_day`` sees no Mafia alive → end_screen.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # Resolve roles via the name interrupt first so we can read the roster.
    _drive(graph, run_config, {"messages": []})
    first_iv = _collect_interrupt(graph, run_config)
    assert first_iv == {"kind": "name"}, (
        f"expected kind='name' first, got {first_iv!r}"
    )

    # Install a live dispatcher: Pointing targets a fresh Law-abiding each
    # Night; DayAction targets a fresh Mafia each Day (so the scripted
    # DayAction queue doesn't need ids baked in); Ballots are always Yes.
    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            law_ids = _alive_ai_ids_by_role(graph, run_config, "law_abiding")
            if not law_ids:
                # Shouldn't happen in this scenario, but guard against hang.
                return Pointing(target_id="missing")
            return Pointing(target_id=law_ids[0])
        if schema is DayAction:
            mafia_ids = _alive_ai_ids_by_role(graph, run_config, "mafia")
            if mafia_ids:
                return DayAction(kind="vote", target_id=mafia_ids[0])
            # Safety net: nothing to vote on (win already reached).
            return DayAction(kind="speak", text="(nothing to add.)")
        if schema is Ballot:
            return Ballot(yes=True)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            # Let AIs drive the vote; human just passes.
            return "..."
        if kind == "vote":
            return "yes"
        if kind == "point":
            # Human is Law-abiding (pinned via GRAPHIA_ROLE) — this should never fire.
            options = iv.get("options") or []
            return options[0]["id"] if options else ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    def _ended() -> bool:
        return graph.get_state(run_config).values.get("winner") == "law_abiding"

    _advance_until(
        graph,
        run_config,
        stop=_ended,
        interrupt_responder=_respond,
        budget=200,
    )

    # --- Assertions -----------------------------------------------------

    state = graph.get_state(run_config).values
    assert state.get("winner") == "law_abiding", (
        f"expected winner='law_abiding', got {state.get('winner')!r}"
    )

    # Graph terminated — no pending next node.
    assert graph.get_state(run_config).next == (), (
        f"graph should be at END; next was "
        f"{graph.get_state(run_config).next!r}"
    )

    # Final Moderator message is a single SystemMessage containing all
    # end-screen content.
    system_msgs = [
        m for m in state.get("messages", []) if isinstance(m, SystemMessage)
    ]
    final = system_msgs[-1].content
    assert ENDGAME_WINNER_LAW in final, (
        f"final message missing winner line; got:\n{final!r}"
    )
    assert ENDGAME_HEADER_KILLS in final
    assert ENDGAME_HEADER_ROSTER in final

    # The kill log should hold every kill that happened and they should
    # all be referenced in the end-screen message in chronological order.
    kill_log = state.get("kill_log", [])
    assert len(kill_log) >= 3, (
        f"expected at least 3 kills (2 Night + 2 execution); got {kill_log!r}"
    )
    # At least two Night kills and two executions landed across the game.
    night_kills = [r for r in kill_log if r.get("cause") == "night"]
    exec_kills = [r for r in kill_log if r.get("cause") == "execution"]
    assert len(exec_kills) >= 2, (
        f"expected 2 executions (one per Mafia), got {exec_kills!r}"
    )
    assert len(night_kills) >= 1, (
        f"expected at least one Night kill, got {night_kills!r}"
    )

    # Every kill record's victim name appears in the final message.
    for record in kill_log:
        assert record["name"] in final, (
            f"kill-log entry {record!r} missing from end-screen message:\n"
            f"{final!r}"
        )

    # Chronological ordering: kill names appear in the same order in the
    # final message as in kill_log.
    indices = [final.find(rec["name"]) for rec in kill_log]
    assert all(i >= 0 for i in indices), (
        "all kill names must be present in end-screen"
    )
    assert indices == sorted(indices), (
        f"kill names must appear in chronological order; got indices "
        f"{indices!r}"
    )

    # Full roster reveal: every player (alive OR dead) listed with role.
    players = state.get("players", {})
    assert len(players) == 7
    for player in players.values():
        assert player.name in final, (
            f"roster reveal missing {player.name!r}"
        )
    # Role labels appear (at least one Mafia, at least one Law-abiding).
    assert "Mafia" in final
    assert "Law-abiding Citizen" in final

    # Phase flipped to "end".
    assert state.get("phase") == "end"


# --------------------------------------------------------------------------
# Test 2: Mafia wins when parity is reached.
# --------------------------------------------------------------------------


def test_mafia_wins_when_parity_reached(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Night kills bring Mafia to parity with Law-abiding → Mafia wins.

    With the human pinned as Mafia there are 2 Mafia + 5 Law-abiding
    (the deck always holds exactly 2 mafia cards). Three Night kills
    (each removing one Law-abiding) drop the count to 2 vs 2, triggering
    the Mafia-win branch at ``check_win_night``.

    Day votes are scripted to always target Law-abiding (and fail — all
    No ballots from AIs, human votes No) so no Mafia ever dies.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_small(AI_NAMES)

    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})
    first_iv = _collect_interrupt(graph, run_config)
    assert first_iv == {"kind": "name"}

    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            # AI Mafia targets a Law-abiding; same rule as human — see
            # `_respond` below.
            law_ids = _alive_ai_ids_by_role(graph, run_config, "law_abiding")
            if not law_ids:
                return Pointing(target_id="missing")
            return Pointing(target_id=law_ids[0])
        if schema is DayAction:
            # AI speaks (or votes against a Law-abiding so nobody removes
            # a Mafia). Speaking is simpler — no vote flow triggered at
            # all → day_votes_called stays at 0 → 6 rounds cap the Day.
            return DayAction(kind="speak", text="Nothing suspicious here.")
        if schema is Ballot:
            # No ballots all round — votes never pass (if one ever starts).
            return Ballot(yes=False)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "no"
        if kind == "point":
            # Human-Mafia interrupt: target the first alive Law-abiding.
            options = iv.get("options") or []
            if options:
                return options[0]["id"]
            return ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    def _ended() -> bool:
        return graph.get_state(run_config).values.get("winner") == "mafia"

    _advance_until(
        graph,
        run_config,
        stop=_ended,
        interrupt_responder=_respond,
        budget=300,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") == "mafia", (
        f"expected winner='mafia', got {state.get('winner')!r}"
    )
    assert graph.get_state(run_config).next == ()

    system_msgs = [
        m for m in state.get("messages", []) if isinstance(m, SystemMessage)
    ]
    final = system_msgs[-1].content
    assert ENDGAME_WINNER_MAFIA in final
    assert ENDGAME_HEADER_KILLS in final
    assert ENDGAME_HEADER_ROSTER in final

    # Some kills happened (at least the Night kills that led to parity).
    kill_log = state.get("kill_log", [])
    assert len(kill_log) >= 3, (
        f"expected at least 3 Night kills leading to parity; got {kill_log!r}"
    )
    for record in kill_log:
        assert record["name"] in final, (
            f"kill-log entry {record!r} missing from end-screen"
        )

    # Roster reveal: every player listed.
    players = state.get("players", {})
    assert len(players) == 7
    for player in players.values():
        assert player.name in final

    # Phase flipped to "end".
    assert state.get("phase") == "end"


# --------------------------------------------------------------------------
# Test 3: End-screen message contains the full kill log and roster.
# --------------------------------------------------------------------------


def test_endgame_message_contains_kill_log_and_roster(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dedicated assertion pass on the final message structure.

    Reuses the Law-abiding-win setup from Test 1 but focuses assertions
    exclusively on the end-screen payload: it must contain every
    ``KillRecord`` entry in chronological order AND the full roster with
    role labels for every player (alive and dead).
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            law_ids = _alive_ai_ids_by_role(graph, run_config, "law_abiding")
            if not law_ids:
                return Pointing(target_id="missing")
            return Pointing(target_id=law_ids[0])
        if schema is DayAction:
            mafia_ids = _alive_ai_ids_by_role(graph, run_config, "mafia")
            if mafia_ids:
                return DayAction(kind="vote", target_id=mafia_ids[0])
            return DayAction(kind="speak", text="(nothing to add.)")
        if schema is Ballot:
            return Ballot(yes=True)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    def _respond(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "yes"
        if kind == "point":
            options = iv.get("options") or []
            return options[0]["id"] if options else ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    _advance_until(
        graph,
        run_config,
        stop=lambda: graph.get_state(run_config).values.get("winner") is not None,
        interrupt_responder=_respond,
        budget=200,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") == "law_abiding"

    system_msgs = [
        m for m in state.get("messages", []) if isinstance(m, SystemMessage)
    ]
    final = system_msgs[-1].content

    # Kill-log section header + every record's name AND cycle reference.
    assert ENDGAME_HEADER_KILLS in final
    kill_log = state.get("kill_log", [])
    assert kill_log, "kill log should be non-empty for this scenario"

    # Chronological ordering: build indices in the final message and
    # confirm monotonic non-decreasing order.
    positions = []
    for rec in kill_log:
        idx = final.find(rec["name"])
        assert idx != -1, (
            f"kill-log name {rec['name']!r} missing from end-screen:\n"
            f"{final!r}"
        )
        positions.append(idx)
    assert positions == sorted(positions), (
        f"kill names appear out of order. Order in log: "
        f"{[r['name'] for r in kill_log]!r}; "
        f"first positions in message: {positions!r}. Message:\n{final!r}"
    )

    # Roster section contains EVERY player and their role label.
    assert ENDGAME_HEADER_ROSTER in final
    players = state.get("players", {})
    assert len(players) == 7
    roster_section = final.split(ENDGAME_HEADER_ROSTER, 1)[1]
    for player in players.values():
        assert player.name in roster_section, (
            f"{player.name!r} missing from roster section:\n"
            f"{roster_section!r}"
        )
        role_label = "Mafia" if player.role == "mafia" else "Law-abiding Citizen"
        # Role label appears near the player's name (parenthesised).
        expected_fragment = f"{player.name} ({role_label})"
        assert expected_fragment in roster_section, (
            f"expected fragment {expected_fragment!r} missing from roster "
            f"section:\n{roster_section!r}"
        )


# --------------------------------------------------------------------------
# Test 4: Textual pilot — end screen renders and any keypress exits.
# --------------------------------------------------------------------------


async def test_end_screen_visible_in_ui(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pilot smoke test: end screen lands in #public-log; any key exits.

    Pins the human as Law-abiding via ``GRAPHIA_ROLE`` so the test never
    has to respond to a ``kind="point"`` modal interrupt. AIs always
    *speak* (never vote), so no ``VoteModal`` ever pops up and the pilot
    never has to disambiguate modal vs Input focus. Each Day ends after 6
    no-vote rounds via the ``DAY_MAX_ROUNDS`` cap; each Night kills one
    Law-abiding AI.

    Expected trajectory (with 2 Mafia AI, 4 Law-abiding AI, 1
    Law-abiding human — 5 Law-abiding total):
      - Night 1: AI Mafia kills Law-abiding #1 → 2M, 4L (no win).
      - Day 1: 6 rounds, no vote → day_close.
      - Night 2: AI Mafia kills Law-abiding #2 → 2M, 3L (no win).
      - Day 2: 6 rounds, no vote → day_close.
      - Night 3: AI Mafia kills Law-abiding #3 → 2M, 2L (Mafia parity!).
      - ``check_win_night`` routes to ``end_screen`` → Mafia win.

    After ``end_screen`` runs the driver posts ``"Game over."`` and any
    keypress exits the app.
    """
    import asyncio

    from rich.text import Text
    from textual.widgets import Input, RichLog

    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    app = GraphiaApp()

    async def _wait_for(predicate, timeout=30.0, interval=0.1) -> bool:
        """Poll ``predicate`` until True or timeout. Returns True on success."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await pilot.pause(interval)
        return False

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

        original_invoke = fake._invoke

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
                # Always speak — no vote modals are triggered. The Day
                # terminates via the 6-round cap.
                return DayAction(kind="speak", text="I'm watching carefully.")
            if schema is Ballot:
                # Shouldn't fire (no votes), but defensive.
                return Ballot(yes=False)
            return original_invoke(schema, messages)

        fake._invoke = _invoke_live  # type: ignore[method-assign]

        # Enter name. Wait for the input to become enabled first.
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

        public_log = app.query_one("#public-log", RichLog)

        def _log_contents() -> str:
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

        # Interleave human day_turn responses with polling for "Game over.".
        # Each human day_turn interrupt enables the input; we submit "..." to
        # pass the turn. After ~12 human turns across Day 1 + Day 2, the
        # second Night's parity check ends the game.
        got_it = False
        for _ in range(80):
            text = _log_contents()
            if "Game over." in text:
                got_it = True
                break
            try:
                prompt = app.query_one("#player-input", Input)
            except Exception:  # noqa: BLE001
                prompt = None  # type: ignore[assignment]
            if prompt is not None and prompt.disabled is False:
                # The input is live — submit an empty-ish speech.
                await pilot.press(".")
                await pilot.press("enter")
            else:
                await pilot.pause(0.2)

        if not got_it:
            got_it = await _wait_for(
                lambda: "Game over." in _log_contents(), timeout=10.0
            )

        if not got_it:
            rendered = _log_contents()
            app.exit()
            raise AssertionError(
                "'Game over.' never appeared in #public-log. Log was:\n"
                + rendered
            )

        rendered = _log_contents()
        assert (
            ENDGAME_WINNER_LAW in rendered or ENDGAME_WINNER_MAFIA in rendered
        ), f"no winner line in public log; got:\n{rendered}"
        assert "Game over." in rendered

        assert app._game_over is True
        # Any keypress exits the app.
        await pilot.press("x")

    assert app.is_running is False
