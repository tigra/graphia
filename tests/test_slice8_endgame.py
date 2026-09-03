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

4. ``test_endgame_recap_omits_diaries_while_diaries_are_really_written``
   (spec 042 Slice 3, Task 3.1) — pins BOTH halves of the recap/diary
   boundary as it stands today: real scripted diary entries reach
   ``private_diaries``, and none of that text reaches the recap.

Plus a single Textual-pilot smoke test asserting the "Game over." marker
renders in ``#public-log`` and any keypress exits the app.

All LLM calls go through the unified ``fake_large`` fixture (DayAction,
Ballot, Pointing, Persona and Diary served from one fake keyed on schema),
plus ``fake_small`` for name generation. No test touches real Bedrock.

Why every ``fake_large(...)`` call here scripts ``diaries=``
------------------------------------------------------------

``GRAPHIA_PRIVATE_DIARIES`` defaults **on**, so every Day→Night hinge in
these driven games fans ``day_diary`` out across the surviving AI players.
Until spec 042 Slice 3 (Task 3.1) none of these tests scripted a ``Diary``
answer, so each of those calls drained an empty queue, raised inside
``_ai_diary``, and was swallowed by its broad ``except`` into
``graphia.nodes.day._DIARY_FALLBACK`` — 26 fallback entries across the four
tests in this file, every one of them green and none of them exercising the
diary model path. Scripting ``diaries=`` costs one line per call site,
changes no assertion in tests 1–3 or the pilot (diary text never enters the
message stream, and the recap reads no diaries), and puts the real path
under all of them. See ``tests/test_slice39_diary_fake_coverage.py`` for the
statement of why a missing ``Diary`` queue is a *silent* failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import SystemMessage
from langgraph.types import Command

from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Diary, Persona, Pointing
from graphia.nodes.day import _DIARY_FALLBACK
from graphia.prompts import (
    ENDGAME_HEADER_KILLS,
    ENDGAME_HEADER_ROSTER,
    ENDGAME_PERSONA_HEADER,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
)
from graphia.ui.app import GraphiaApp

HUMAN_NAME = "Alice"
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]

# The scripted diary entry every ``fake_large(diaries=...)`` call in this file
# serves. Three properties are load-bearing and must survive any reword:
#
# * It carries a token no production string could emit, so an exact match
#   proves the text travelled from the scripted queue into state untouched and
#   a substring search over the recap cannot be satisfied by coincidence.
# * It is SINGLE-LINE and far under ``DIARY_MAX_CHARS``, so
#   ``_clamp_diary_entry`` (whitespace-fold then truncate) is the identity on
#   it and exact equality is a fair assertion.
# * It names no player and contains no roster name as a substring, so the
#   "diary text is absent from the recap" assertion cannot be tripped by the
#   roster reveal, and vice versa.
DIARY_SENTINEL_TOKEN = "ENDGAME-DIARY-SENTINEL"
SCRIPTED_DIARY_ENTRY = (
    f"{DIARY_SENTINEL_TOKEN}: the whole day folded down to one thought, "
    "and I have set it here where no one else can read it."
)

# Scripted persona so the recap's persona reveal is pinned against text this
# test chose, not against ``setup._fallback_persona``'s name-anchored
# stand-in. Distinctive tokens for the same reason as above.
PERSONA_SENTINEL_TOKEN = "ENDGAME-PERSONA-SENTINEL"
SCRIPTED_PERSONA = Persona(
    personality=f"{PERSONA_SENTINEL_TOKEN}-personality.",
    manner=f"{PERSONA_SENTINEL_TOKEN}-manner.",
    public_backstory=f"{PERSONA_SENTINEL_TOKEN}-cover.",
    secret_backstory=f"{PERSONA_SENTINEL_TOKEN}-truth.",
)


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

    # ``diaries=`` is REQUIRED here, not decorative: without it the default-on
    # ``day_diary`` fan-out drains an empty queue and every entry silently
    # becomes ``_DIARY_FALLBACK`` (module docstring).
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        diaries=[Diary(entry=SCRIPTED_DIARY_ENTRY)],
    )

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

    # ``diaries=`` is REQUIRED here, not decorative: without it the default-on
    # ``day_diary`` fan-out drains an empty queue and every entry silently
    # becomes ``_DIARY_FALLBACK`` (module docstring).
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        diaries=[Diary(entry=SCRIPTED_DIARY_ENTRY)],
    )

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

    # ``diaries=`` is REQUIRED here, not decorative: without it the default-on
    # ``day_diary`` fan-out drains an empty queue and every entry silently
    # becomes ``_DIARY_FALLBACK`` (module docstring).
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        diaries=[Diary(entry=SCRIPTED_DIARY_ENTRY)],
    )

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
# Test 4: the recap/diary boundary — real diaries written, none of them read.
# --------------------------------------------------------------------------


def _diary_texts(state: dict[str, Any]) -> list[str]:
    """Flatten the ``private_diaries`` channel into a flat list of entry texts.

    ``DiaryRecord`` (spec 039) is a ``TypedDict`` carrying ``day``,
    ``thoughts_before`` and ``text``; a checkpoint round-trip hands it back as a
    plain ``dict``. Anything else is asserted against here rather than coerced,
    so a wobble in that shape surfaces as a shape failure instead of quietly
    emptying the list and making the caller's "the entries are real" assertions
    vacuous.
    """
    channel = state.get("private_diaries")
    assert isinstance(channel, dict), (
        "expected a `private_diaries` dict keyed by player id; got "
        f"{type(channel).__name__}: {channel!r}"
    )
    texts: list[str] = []
    for player_id, records in channel.items():
        assert isinstance(records, list), (
            f"`private_diaries[{player_id!r}]` must be a list of DiaryRecord; "
            f"got {type(records).__name__}: {records!r}"
        )
        for record in records:
            assert isinstance(record, dict) and isinstance(
                record.get("text"), str
            ), (
                f"`private_diaries[{player_id!r}]` holds {record!r}, which is "
                "not a DiaryRecord with a string `text` field"
            )
            texts.append(record["text"])
    return texts


def test_endgame_recap_omits_diaries_while_diaries_are_really_written(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real diaries get written; the recap contains none of their text.

    Spec 042 Slice 3, Task 3.1. Two claims, and **only the pair is worth
    anything** — which is the whole reason this test exists as one test rather
    than two.

    1. **The diaries are real.** With ``GRAPHIA_PRIVATE_DIARIES`` on, the
       Day→Night hinge fans ``day_diary`` out over the surviving AI players.
       Every recorded entry must equal :data:`SCRIPTED_DIARY_ENTRY` and none
       may be ``graphia.nodes.day._DIARY_FALLBACK`` — imported, never copied,
       so a reword of the fallback cannot disarm the check. The fallback is
       itself non-empty in-voice prose, so "an entry exists" distinguishes
       nothing; the discriminator is the text.

    2. **The recap reads no diaries.** ``end_screen`` composes the winner line,
       the chronological kill log, the roster-and-role reveal and the persona
       reveal — and nothing else. No diary text appears in it.

    Claim 2 alone passes trivially, including in the world this task was
    written to end: one where every diary call fell through into the
    deterministic fallback and the diary model path was never exercised at all.
    Claim 1 is what makes claim 2 load-bearing.

    **FOR WHOEVER IMPLEMENTS SPEC 040 (Moderator Creative Recap, Phase 6a,
    still Draft):** the "no diary text in the recap" assertion below is
    expected to **invert** when the recap learns to read the diaries. It is
    written here deliberately, as documentation of today's boundary, so that
    change *flips a stated expectation* instead of discovering an undeclared
    coupling. Invert it — do not delete it, and do not weaken claim 1, which
    stays true either way and is what keeps the inverted assertion honest.

    Trajectory (the Law-abiding win from Test 1, so the recap has real content
    to pin): Night 1 kills a Law-abiding AI → Day 1 executes the first Mafia →
    ``day_close`` → ``day_diary`` fans out → Night 2 kills another Law-abiding
    → Day 2 executes the second Mafia → ``check_win_day`` routes to
    ``end_screen``. The human is pinned Law-abiding with ``GRAPHIA_ROLE``
    (never a magic seed — ADR 006), so no ``kind="point"`` interrupt fires.

    Note that the winning Day writes NO diaries (``check_win_day`` bypasses
    ``day_close``) and Night 1 has none either, so the entries all come from
    Day 1's hinge — that is expected, and why the assertion is on the *content*
    of whatever was written rather than on a count of entries.
    """
    # Explicit rather than leaning on the default, so a future flag flip turns
    # this into a loud failure instead of a silent no-op.
    monkeypatch.setenv("GRAPHIA_PRIVATE_DIARIES", "1")
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    # ``personas=`` as well as ``diaries=``: the persona reveal is part of the
    # recap content this test pins, and without a scripted persona
    # ``generate_personas`` falls back to its name-anchored stand-in, leaving
    # nothing distinctive to assert against.
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        personas=[SCRIPTED_PERSONA],
        diaries=[Diary(entry=SCRIPTED_DIARY_ENTRY)],
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        # Pointing / DayAction resolve against LIVE state — the real player
        # uuids only exist once ``assign_roles`` has run, so they cannot be
        # pre-scripted. ``Persona`` and ``Diary`` deliberately fall through to
        # the scripted queues, which is what puts those two model paths under
        # test.
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
        stop=lambda: graph.get_state(run_config).values.get("winner")
        is not None,
        interrupt_responder=_respond,
        budget=200,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") == "law_abiding", (
        f"the scripted trajectory never reached the Law-abiding win; got "
        f"winner={state.get('winner')!r}. Every assertion below is "
        f"inconclusive until this holds."
    )
    assert state.get("phase") == "end"

    # ------------------------------------------------------------------
    # Claim 1: the diaries were written, and they are the player's own
    # words rather than the deterministic stand-in.
    # ------------------------------------------------------------------

    # The ``Diary`` binding was invoked at all. NOTE, so nobody mistakes this
    # for the discriminator: unlike the unknown-schema case in
    # ``tests/test_slice39_diary_fake_coverage.py``, the ``Diary`` queue exists
    # in ``FakeLargeUnified``, so this counter increments even when the queue
    # is empty and the call raises into the fallback. It pins that
    # ``day_diary`` ran through the model path at all — which is what stops the
    # whole test going vacuous if a future change skips the hinge — and nothing
    # more. The text assertions below are what separate real from fallback.
    assert fake.calls_by_schema[Diary] >= 1, (
        "`day_diary` never bound the Diary schema — the Day→Night hinge did "
        "not fan out, so neither claim in this test was exercised"
    )

    diary_texts = _diary_texts(state)
    assert diary_texts, (
        "a flag-on game that closed at least one Day wrote no diary entries "
        "at all; expected one per surviving AI player at Day 1's hinge"
    )
    assert _DIARY_FALLBACK not in diary_texts, (
        f"_DIARY_FALLBACK ({_DIARY_FALLBACK!r}) reached state even though the "
        f"Diary queue was scripted — the model call is failing and being "
        f"swallowed by `_ai_diary`'s broad except. Entries were: "
        f"{diary_texts!r}"
    )
    assert set(diary_texts) == {SCRIPTED_DIARY_ENTRY}, (
        "every diary entry should be the scripted text, unchanged by "
        f"`_clamp_diary_entry`; got {sorted(set(diary_texts))!r}"
    )

    # ------------------------------------------------------------------
    # Claim 2 (part a): the recap's actual content, pinned.
    # ------------------------------------------------------------------

    system_msgs = [
        m for m in state.get("messages", []) if isinstance(m, SystemMessage)
    ]
    final = system_msgs[-1].content

    # The winner line.
    assert ENDGAME_WINNER_LAW in final, (
        f"final message missing the Law-abiding winner line; got:\n{final!r}"
    )

    # The kill log, chronologically.
    assert ENDGAME_HEADER_KILLS in final
    kill_log = state.get("kill_log", [])
    assert kill_log, "kill log should be non-empty for this scenario"
    positions = []
    for record in kill_log:
        index = final.find(record["name"])
        assert index != -1, (
            f"kill-log name {record['name']!r} missing from the recap:\n"
            f"{final!r}"
        )
        positions.append(index)
    assert positions == sorted(positions), (
        f"kill names must appear in chronological order; order in log "
        f"{[r['name'] for r in kill_log]!r}, first positions {positions!r}"
    )

    # The roster-and-role reveal: every player, alive or dead, with its role.
    assert ENDGAME_HEADER_ROSTER in final
    players = state.get("players", {})
    roster_section = final.split(ENDGAME_HEADER_ROSTER, 1)[1]
    for player in players.values():
        role_label = (
            "Mafia" if player.role == "mafia" else "Law-abiding Citizen"
        )
        assert f"{player.name} ({role_label})" in roster_section, (
            f"roster reveal missing {player.name!r} ({role_label}):\n"
            f"{roster_section!r}"
        )

    # The persona reveal: one bullet per AI player, carrying the scripted
    # persona text. The human has no persona and gets no bullet.
    assert ENDGAME_PERSONA_HEADER in final
    persona_section = final.split(ENDGAME_PERSONA_HEADER, 1)[1]
    assert PERSONA_SENTINEL_TOKEN in persona_section, (
        "the persona reveal shows no scripted persona text — the persona "
        f"queue was not reached:\n{persona_section!r}"
    )
    for player in players.values():
        if player.is_human:
            continue
        role_label = (
            "Mafia" if player.role == "mafia" else "Law-abiding Citizen"
        )
        assert f"{player.name} ({role_label})" in persona_section, (
            f"persona reveal missing a bullet for {player.name!r}:\n"
            f"{persona_section!r}"
        )

    # ------------------------------------------------------------------
    # Claim 2 (part b): THE BOUNDARY. Today's recap reads no diaries.
    #
    # >>> SPEC 040 (Moderator Creative Recap) MUST INVERT THIS. <<<
    #
    # When the recap learns to read the private diaries, these two
    # assertions become the wrong way round — flip them to assert the
    # diary text IS present. Do not delete them, and do not touch Claim 1
    # above: without a positive check that real, scripted entries exist,
    # an absence assertion here passes in a world where the diaries were
    # never written or were the deterministic fallback, which is exactly
    # the vacuity this test was created to remove.
    # ------------------------------------------------------------------

    assert SCRIPTED_DIARY_ENTRY not in final, (
        "the endgame recap contains a private diary entry verbatim. If this "
        "is spec 040 landing, invert this assertion (and keep the entries-are-"
        f"real assertions above). Recap was:\n{final!r}"
    )
    assert DIARY_SENTINEL_TOKEN not in final, (
        "the endgame recap contains diary text (matched on the sentinel "
        "token, so a paraphrased or truncated quotation is caught too). If "
        "this is spec 040 landing, invert this assertion. Recap was:\n"
        f"{final!r}"
    )


# --------------------------------------------------------------------------
# Test 5: Textual pilot — end screen renders and any keypress exits.
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

    # ``diaries=`` is REQUIRED here, not decorative: without it the default-on
    # ``day_diary`` fan-out drains an empty queue and every entry silently
    # becomes ``_DIARY_FALLBACK`` (module docstring).
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        diaries=[Diary(entry=SCRIPTED_DIARY_ENTRY)],
    )

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
