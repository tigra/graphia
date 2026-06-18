"""Spec 016 Slice 1 tests: AI character personas — generation, persistence,
serde round-trip, and the end-of-game reveal.

All offline and model-free. The persona LLM call is mocked at the
``ChatBedrockConverse`` boundary via the extended unified large-model fake
(``fake_large(personas=[...])``), which patches ``graphia.nodes.setup.get_large``
alongside the Day/Night call sites. Per architecture §6 we never assert persona
prose verbatim beyond what the fake supplies — only structural presence (fields
populated, instance types, the reveal contains the supplied strings).

Layout:

- **Generation** drives the pure setup nodes (``collect_name`` is skipped — we
  hand-build the post-name state — then ``generate_roster`` → ``assign_roles`` →
  ``generate_personas``), mirroring ``test_play_as_role.py``'s direct-node style.
- **Fallback** proves the never-block guarantee with NO persona fake installed
  (the autouse ``safe_llm`` loud-failure stands in and the broad-except path
  yields fallback personas).
- **Serde round-trip** exercises ``make_checkpoint_serde`` directly (the
  ``allowed_msgpack_modules`` registration that unblocks Slice 2).
- **Persistence** calls ``resolve_night_kill`` / ``resolve_vote`` directly and
  asserts the persona survives the ``dataclasses.replace`` rebuild.
- **Reveal** and **regression** drive the compiled graph to a Law-abiding win
  (mirroring ``test_slice8_endgame.py``) and assert the end-screen reveal plus
  the end-only invariant.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import SystemMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_checkpoint_serde, make_run_config
from graphia.llm import Ballot, DayAction, Persona, Pointing
from graphia.nodes.day import (
    _ai_day_action,
    _persona_block,
    _render_alive_roster,
    _role_label,
    _team_line,
    _win_condition_line,
    resolve_vote,
)
from graphia.nodes.night import _ai_pick_target, resolve_night_kill
from graphia.nodes.setup import assign_roles, generate_personas, generate_roster
from graphia.prompts import (
    DAY_SPEAK_USER_TEMPLATE,
    ENDGAME_PERSONA_HEADER,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
    MAFIA_POINT_USER_TEMPLATE,
)
from graphia.state import ActiveVote, GameState, PlayerPersona, PlayerState

HUMAN_NAME = "Alice"
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]


# --------------------------------------------------------------------------
# Persona fakes scripted for the tests. A distinct Citizen vs. Mafioso pair so
# the role-tailoring branch is exercised; the queue replays its last value once
# drained, so a two-entry queue serves every AI on the table.
# --------------------------------------------------------------------------

_CITIZEN_PERSONA = Persona(
    personality="warm and chatty",
    manner="speaks in long, friendly sentences",
    public_backstory="the village baker who knows everyone",
    secret_backstory="",
)
_MAFIA_PERSONA = Persona(
    personality="calm and reassuring",
    manner="measured, never raises their voice",
    public_backstory="a retired schoolteacher, beloved and above suspicion",
    secret_backstory="the quiet enforcer who runs the family's books",
)


def _post_name_state() -> GameState:
    """A ``GameState`` as it stands right after ``collect_name`` returned.

    Only the human is present; ``generate_roster`` adds the AI seats. The human
    is the first inserted player (``assign_roles`` asserts this when a role is
    pinned).
    """
    human_id = "human"
    human = PlayerState(
        id=human_id,
        name=HUMAN_NAME,
        role="law_abiding",
        is_human=True,
        is_alive=True,
    )
    return {"human_id": human_id, "players": {human_id: human}}


def _run_setup(
    state: GameState,
    fake_small,
    fake_large,
    *,
    personas: list[Persona] | None,
) -> dict[str, PlayerState]:
    """Drive ``generate_roster`` → ``assign_roles`` → ``generate_personas``.

    Returns the final players map. ``fake_small`` scripts the roster names;
    ``fake_large(personas=...)`` scripts the persona generation (or, when
    ``personas`` is None, no persona fake is installed at all — proving the
    fallback path under the autouse loud-failure).
    """
    fake_small(AI_NAMES)
    if personas is not None:
        fake_large(personas=personas)

    # generate_roster reads the existing (human-only) players and appends AIs.
    state = {**state, **generate_roster(state)}
    # assign_roles deals roles (human pinned via GRAPHIA_ROLE in the test).
    state = {**state, **assign_roles(state)}
    # generate_personas attaches a persona to every AI seat.
    result = generate_personas(state)
    return result["players"]


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def test_every_ai_gets_a_persona_human_has_none(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After setup, every AI player carries a persona; the human carries none."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    players = _run_setup(
        _post_name_state(),
        fake_small,
        fake_large,
        personas=[_CITIZEN_PERSONA, _MAFIA_PERSONA],
    )

    # 7-seat default table: 1 human + 6 AIs.
    assert len(players) == 7
    human = players["human"]
    assert human.is_human is True
    assert human.persona is None, "the human must never be given a persona"

    ai_players = [p for p in players.values() if not p.is_human]
    assert len(ai_players) == 6
    for ai in ai_players:
        assert ai.persona is not None, f"AI {ai.name!r} has no persona"
        assert isinstance(ai.persona, PlayerPersona)


def test_mafioso_persona_has_public_and_true_self(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mafioso's stored persona has a non-empty public_persona AND true_self;
    a Citizen's has an honest public_persona and an empty true_self."""
    # Pin the human Law-abiding so all Mafia seats are AIs (cleaner to inspect).
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    # The persona queue dispatches by call order, not by role; the node tailors
    # the conversion (true_self kept only for Mafia). Supplying the Mafia-shaped
    # persona (with a secret) to ALL calls lets us assert that a Citizen's
    # true_self is dropped while a Mafioso's is kept.
    players = _run_setup(
        _post_name_state(),
        fake_small,
        fake_large,
        personas=[_MAFIA_PERSONA],
    )

    ai_players = [p for p in players.values() if not p.is_human]
    mafiosi = [p for p in ai_players if p.role == "mafia"]
    citizens = [p for p in ai_players if p.role == "law_abiding"]
    # Default lineup: 2 Mafia total, human is Law-abiding → 2 AI Mafiosi.
    assert len(mafiosi) == 2
    assert len(citizens) == 4

    for maf in mafiosi:
        assert maf.persona is not None
        assert maf.persona.public_persona.strip(), (
            f"Mafioso {maf.name!r} has an empty public_persona (legend)"
        )
        assert maf.persona.true_self.strip(), (
            f"Mafioso {maf.name!r} has an empty true_self"
        )

    for cit in citizens:
        assert cit.persona is not None
        assert cit.persona.public_persona.strip(), (
            f"Citizen {cit.name!r} has an empty public_persona"
        )
        assert cit.persona.true_self == "", (
            f"Citizen {cit.name!r} leaked a true_self: "
            f"{cit.persona.true_self!r}"
        )


# --------------------------------------------------------------------------
# Fallback — never-block behaviour
# --------------------------------------------------------------------------


def test_generate_personas_never_blocks_without_a_fake(
    env: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With NO persona fake installed, ``generate_personas`` does not raise and
    every AI still gets a (fallback) persona — setup completes.

    The autouse ``safe_llm`` loud-failure stands in for ``setup.get_large``;
    ``generate_personas``' broad-except retry-then-fallback turns each loud
    failure into a deterministic name-anchored fallback persona.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    # Note: fake_large is intentionally NOT requested — no persona queue exists,
    # so every ``setup.get_large().invoke(...)`` hits the loud-failure fake.
    players = _run_setup(
        _post_name_state(),
        fake_small,
        fake_large=None,  # type: ignore[arg-type]
        personas=None,
    )

    ai_players = [p for p in players.values() if not p.is_human]
    assert len(ai_players) == 6
    for ai in ai_players:
        assert ai.persona is not None, (
            f"AI {ai.name!r} got no fallback persona — setup blocked"
        )
        assert isinstance(ai.persona, PlayerPersona)
        # Fallback personas are name-anchored and non-empty.
        assert ai.persona.personality.strip()
        assert ai.persona.public_persona.strip()
    # The human still carries no persona on the fallback path.
    assert players["human"].persona is None


# --------------------------------------------------------------------------
# Typed serde round-trip (the allow-list fix that unblocks Slice 2)
# --------------------------------------------------------------------------


def test_checkpoint_serde_roundtrips_persona_to_typed_instance() -> None:
    """A ``PlayerState`` carrying a ``PlayerPersona`` round-trips through
    ``make_checkpoint_serde`` back to a typed ``PlayerPersona`` — not a dict.

    Guards the ``allowed_msgpack_modules`` registration in ``graph.py``: without
    ``PlayerPersona`` on the allow-list the serde returns the persona as a plain
    dict after a checkpoint boundary, which would break the typed Day-speech
    injection in Slice 2.
    """
    serde = make_checkpoint_serde()
    persona = PlayerPersona(
        personality="bold and brash",
        manner="clipped, declarative",
        public_persona="the harbour master",
        true_self="runs contraband through the docks",
    )
    player = PlayerState(
        id="p-1",
        name="Marco",
        role="mafia",
        is_human=False,
        is_alive=True,
        persona=persona,
    )

    blob = serde.dumps_typed({"players": {"p-1": player}})
    restored = serde.loads_typed(blob)

    got_player = restored["players"]["p-1"]
    assert isinstance(got_player, PlayerState)
    got_persona = got_player.persona
    assert isinstance(got_persona, PlayerPersona), (
        f"persona came back as {type(got_persona).__name__}, not PlayerPersona"
    )
    assert not isinstance(got_persona, dict)
    assert got_persona == persona


# --------------------------------------------------------------------------
# Persistence through a dataclasses.replace rebuild (role change + kill)
# --------------------------------------------------------------------------


def test_persona_survives_role_assignment(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persona attached before ``assign_roles`` carries through the role deal.

    ``assign_roles`` rebuilds every ``PlayerState`` via ``dataclasses.replace``;
    only the dealt role changes, so a pre-existing persona must carry over.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    state = _post_name_state()
    # Hand-attach a persona to a single AI seat before roles are dealt.
    ai_persona = PlayerPersona(
        personality="dry and analytical",
        manner="asks pointed questions",
        public_persona="the town clerk",
        true_self="",
    )
    ai = PlayerState(
        id="ai-1",
        name="Ivy",
        role="law_abiding",
        is_human=False,
        is_alive=True,
        persona=ai_persona,
    )
    state["players"]["ai-1"] = ai

    result = assign_roles(state)
    rebuilt = result["players"]["ai-1"]
    assert rebuilt.persona is ai_persona, (
        "persona reference must carry through assign_roles' replace"
    )
    assert rebuilt.persona == ai_persona


def test_persona_survives_night_kill_and_vote_execution(
    env: Path,
) -> None:
    """A killed/executed player keeps its persona through the rebuild.

    Both ``resolve_night_kill`` and ``resolve_vote`` flip ``is_alive`` via
    ``dataclasses.replace``; the persona must carry over unchanged so the
    end-of-game reveal can still surface it for an eliminated player.
    """
    victim_persona = PlayerPersona(
        personality="nervous and earnest",
        manner="over-explains everything",
        public_persona="the apprentice blacksmith",
        true_self="",
    )
    victim = PlayerState(
        id="victim",
        name="Yuki",
        role="law_abiding",
        is_human=False,
        is_alive=True,
        persona=victim_persona,
    )
    bystander = PlayerState(
        id="other",
        name="Silas",
        role="mafia",
        is_human=False,
        is_alive=True,
        persona=PlayerPersona(
            personality="aloof",
            manner="terse",
            public_persona="the ferryman",
            true_self="smuggles for the family",
        ),
    )

    # --- Night kill ---
    night_state: GameState = {
        "cycle": 2,
        "players": {"victim": victim, "other": bystander},
        "night_round_picks": {"other": "victim"},
        "night_victim_count": 0,
    }
    night_result = resolve_night_kill(night_state)
    killed = night_result["players"]["victim"]
    assert killed.is_alive is False
    assert killed.persona == victim_persona, (
        "persona must survive the night-kill replace"
    )

    # --- Vote execution ---
    active: ActiveVote = {
        "initiator": "other",
        "target": "victim",
        "ballots": {"other": "yes", "victim": "yes"},
        "pending": [],
    }
    vote_state: GameState = {
        "cycle": 1,
        "players": {"victim": victim, "other": bystander},
        "active_vote": active,
        "execution_count": 0,
    }
    vote_result = resolve_vote(vote_state)
    executed = vote_result["players"]["victim"]
    assert executed.is_alive is False
    assert executed.persona == victim_persona, (
        "persona must survive the vote-execution replace"
    )


# --------------------------------------------------------------------------
# Reveal + regression — drive the compiled graph to a Law-abiding win and
# inspect the end-screen persona section (mirrors test_slice8_endgame.py).
# --------------------------------------------------------------------------


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _alive_ai_ids_by_role(graph, run_config, role: str) -> list[str]:
    players = graph.get_state(run_config).values.get("players", {})
    return [
        p.id
        for p in players.values()
        if p.is_alive and p.role == role and not p.is_human
    ]


def _drive(graph, run_config, payload) -> None:
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
    for _ in range(budget):
        if stop():
            return
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            _drive(graph, run_config, None)
            continue
        resume = interrupt_responder(interrupt_value)
        _drive(graph, run_config, Command(resume=resume))


def _drive_to_law_win(graph, run_config, fake):
    """Drive a full game to a Law-abiding win via the live-dispatch pattern.

    Pointing targets a fresh Law-abiding each Night; DayAction votes a fresh
    Mafia each Day; Ballots are all Yes. Returns the final state values.
    """
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
        stop=lambda: graph.get_state(run_config).values.get("winner")
        == "law_abiding",
        interrupt_responder=_respond,
        budget=200,
    )
    return graph.get_state(run_config).values


def test_end_screen_reveals_personas_and_is_end_only(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end-screen public message reveals every AI's persona — contrasting a
    Mafioso's legend with its true self, covering eliminated players too — and
    no persona text appears in any message emitted before ``end_screen``."""
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        personas=[_CITIZEN_PERSONA, _MAFIA_PERSONA],
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    state = _drive_to_law_win(graph, run_config, fake)
    assert state.get("winner") == "law_abiding"
    assert graph.get_state(run_config).next == ()

    players = state.get("players", {})
    messages = state.get("messages", [])
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    final = system_msgs[-1].content

    # The reveal section header is present, end-screen carries the winner line.
    assert ENDGAME_WINNER_LAW in final
    assert ENDGAME_PERSONA_HEADER in final

    # Every AI player's persona surfaces in the reveal — survivors AND
    # eliminated (the kill log proves at least one AI was killed/executed).
    ai_players = [p for p in players.values() if not p.is_human]
    assert ai_players
    for ai in ai_players:
        assert ai.persona is not None
        assert ai.persona.public_persona in final, (
            f"AI {ai.name!r} persona missing from end-screen reveal"
        )

    # At least one revealed AI was eliminated — the reveal covers the dead.
    eliminated_ai = [p for p in ai_players if not p.is_alive]
    assert eliminated_ai, "scenario should have eliminated at least one AI"
    for dead in eliminated_ai:
        assert dead.persona.public_persona in final

    # A Mafioso's reveal contrasts its legend (public) with its true self.
    mafiosi = [p for p in ai_players if p.role == "mafia"]
    assert mafiosi
    for maf in mafiosi:
        assert maf.persona.true_self, "AI Mafioso should carry a true_self"
        assert maf.persona.true_self in final, (
            f"Mafioso {maf.name!r} true_self missing from reveal"
        )
        assert maf.persona.public_persona in final, (
            f"Mafioso {maf.name!r} legend missing from reveal"
        )

    # End-only invariant: the reveal header appears ONLY in the final message,
    # and no persona prose leaks into any earlier in-play message.
    earlier_contents = [m.content for m in system_msgs[:-1]]
    for content in earlier_contents:
        assert ENDGAME_PERSONA_HEADER not in content, (
            "persona reveal header leaked into a pre-end message:\n"
            f"{content!r}"
        )
    # The Mafioso true_self (owner-private during play) must not appear in any
    # message before the end.
    for maf in mafiosi:
        for content in earlier_contents:
            assert maf.persona.true_self not in content, (
                f"Mafioso true_self leaked into a pre-end message:\n{content!r}"
            )


def test_default_game_runs_to_completion_with_persona_node(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a default full game still reaches a clean win with the new
    ``generate_personas`` node in the graph — turn/vote/pointing behaviour and
    win-condition outcome unchanged.

    This run installs NO persona fake (only Day/Night queues), so persona
    generation falls through to the loud-failure → fallback path — proving the
    new node never destabilises a normal game even when the model is absent.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)
    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    state = _drive_to_law_win(graph, run_config, fake)

    # Win-condition outcome unchanged: Law-abiding win, graph at END.
    assert state.get("winner") == "law_abiding"
    assert graph.get_state(run_config).next == ()
    assert state.get("phase") == "end"

    # Mechanics untouched: every Mafia was executed (no Mafia alive), kills
    # were recorded, and the full roster (7 players) is intact.
    players = state.get("players", {})
    assert len(players) == 7
    alive_mafia = [p for p in players.values() if p.is_alive and p.role == "mafia"]
    assert alive_mafia == [], "Law win requires no Mafia alive"
    kill_log = state.get("kill_log", [])
    exec_kills = [r for r in kill_log if r.get("cause") == "execution"]
    assert len(exec_kills) >= 2, (
        f"expected both Mafia executed; got {exec_kills!r}"
    )

    # Even on the fallback path, the end-screen reveal still lists personas
    # (fallback personas are valid PlayerPersonas).
    final = [
        m for m in state.get("messages", []) if isinstance(m, SystemMessage)
    ][-1].content
    assert ENDGAME_PERSONA_HEADER in final


# ==========================================================================
# Slice 2 — Day-speech prompt injection (persona reaches the speak prompt;
# Mafioso cover + true_self; no-leak / knowledge boundary; absent-when-None).
#
# All OFFLINE. We assert STRUCTURAL presence of the supplied persona strings in
# the rendered Day-speech prompt — never persona prose the model would invent
# (architecture §6). Two seams are used, both anchored on the REAL production
# code so a reword breaks the test:
#
#   * ``_render_day_prompt`` re-renders ``DAY_SPEAK_USER_TEMPLATE`` with the
#     REAL ``_persona_block`` (+ the spec-013 grounding helpers), exactly as
#     ``_ai_day_action`` computes its fields — the same render seam the
#     spec-013/015 prompt tests use.
#   * ``_capture_day_prompt`` drives the REAL ``_ai_day_action`` through a
#     content-recording fake, so the template WIRING (the ``{persona}`` slot
#     being filled from ``speaker.persona``) is covered end-to-end, not just the
#     helper in isolation.
# --------------------------------------------------------------------------

# A clean Citizen cover — an honest townsperson, no allegiance tell.
_DAY_CITIZEN_PERSONA = PlayerPersona(
    personality="warm and chatty",
    manner="speaks in long, friendly sentences",
    public_persona="the village baker who knows everyone",
    true_self="",
)
# A Mafioso whose public legend is a clean cover (no Mafia signal) while the
# true self IS the Mafia truth — the exact shape the no-leak test needs.
_DAY_MAFIA_PERSONA = PlayerPersona(
    personality="calm and reassuring",
    manner="measured, never raises their voice",
    public_persona="a retired schoolteacher, beloved and above suspicion",
    true_self="the quiet enforcer who runs the family's books for the mafia",
)


def _day_players() -> dict[str, PlayerState]:
    """A small fixed table for the Day-speak prompt tests.

    One human Law-abiding, two Mafia AIs (Mara carries a persona), and two
    Law-abiding AIs (Cleo carries a persona). Names share no substring so a
    rendered persona / true_self string is unambiguous to assert against.
    """
    roster = [
        PlayerState(id="p-human", name="Alice", role="law_abiding", is_human=True),
        PlayerState(
            id="p-mara",
            name="Mara",
            role="mafia",
            is_human=False,
            persona=_DAY_MAFIA_PERSONA,
        ),
        PlayerState(id="p-max", name="Max", role="mafia", is_human=False),
        PlayerState(
            id="p-cleo",
            name="Cleo",
            role="law_abiding",
            is_human=False,
            persona=_DAY_CITIZEN_PERSONA,
        ),
        PlayerState(id="p-cody", name="Cody", role="law_abiding", is_human=False),
    ]
    return {p.id: p for p in roster}


def _render_day_prompt(
    speaker: PlayerState, players: dict[str, PlayerState]
) -> str:
    """Render the REAL ``DAY_SPEAK_USER_TEMPLATE`` for ``speaker``.

    Mirrors the field computation in ``_ai_day_action`` (role_label,
    win_condition, team_line, persona via the REAL ``_persona_block``, roster,
    context) so a reword of any of those helpers or of the template breaks this
    render — the same seam ``test_behavioral_integrity`` uses.
    """
    return DAY_SPEAK_USER_TEMPLATE.format(
        speaker=speaker.name,
        role_label=_role_label(speaker.role),
        win_condition=_win_condition_line(speaker.role),
        team_line=_team_line(speaker, players),
        persona=_persona_block(speaker),
        roster=_render_alive_roster(players),
        context="(no prior discussion)",
    )


class _CapturingDayFake:
    """A content-recording ``get_large()`` stand-in for the real Day path.

    Returns a scripted ``DayAction`` and records every prompt it was handed, so
    a test can drive the REAL ``_ai_day_action`` and then inspect the actual
    rendered Day-speech prompt the model would have received — proving the
    ``{persona}`` slot is wired from ``speaker.persona`` end-to-end (not just in
    ``_persona_block`` in isolation).
    """

    def __init__(self, action: DayAction) -> None:
        self._action = action
        self.messages_log: list[Any] = []

    def with_structured_output(self, schema: type) -> "_CapturingDayFake":
        return self

    def invoke(self, messages: Any) -> DayAction:
        self.messages_log.append(messages)
        return self._action


def _captured_human_prompt(fake: _CapturingDayFake) -> str:
    """Return the rendered Day-speech HumanMessage text from the first capture."""
    messages = fake.messages_log[0]
    # The Day prompt is [SystemMessage(DAY_SPEAK_SYSTEM), HumanMessage(user)].
    human = messages[1]
    return human.content


# --------------------------------------------------------------------------
# 1. Persona reaches the speak prompt (all AIs) — through the REAL node.
# --------------------------------------------------------------------------


def test_persona_reaches_day_speak_prompt_via_real_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AI speaker's persona (personality/manner/public_persona) reaches the
    Day-speech prompt that ``_ai_day_action`` actually builds.

    Drives the REAL node through a content-recording fake so the template
    WIRING — ``DAY_SPEAK_USER_TEMPLATE`` filled from ``speaker.persona`` via
    ``_persona_block`` — is exercised, not just the helper in isolation.
    """
    players = _day_players()
    cleo = players["p-cleo"]  # a Law-abiding AI carrying a persona
    fake = _CapturingDayFake(DayAction(kind="speak", text="A friendly remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    action = _ai_day_action(cleo, {"players": players, "messages": []})

    assert action.kind == "speak"
    prompt = _captured_human_prompt(fake)
    persona = cleo.persona
    assert persona is not None
    assert persona.personality in prompt
    assert persona.manner in prompt
    assert persona.public_persona in prompt


def test_persona_reaches_day_speak_prompt_via_render(
    env: Path,
) -> None:
    """A re-render of ``DAY_SPEAK_USER_TEMPLATE`` (REAL ``_persona_block``)
    carries the speaker's personality/manner/public_persona — for both a
    Citizen and a Mafioso speaker."""
    players = _day_players()

    for speaker_id in ("p-cleo", "p-mara"):
        speaker = players[speaker_id]
        prompt = _render_day_prompt(speaker, players)
        persona = speaker.persona
        assert persona is not None
        assert persona.personality in prompt, (
            f"{speaker.name!r} personality missing from its own Day prompt"
        )
        assert persona.manner in prompt
        assert persona.public_persona in prompt


# --------------------------------------------------------------------------
# 2. Mafioso gets true_self + cover instruction; Citizen does not.
# --------------------------------------------------------------------------

# Distinctive spans of the production cover instruction in ``_persona_block``
# (a Mafioso-only line). Kept short so a benign reword of surrounding prose
# doesn't break the test, while a removal of the secrecy directive does.
_COVER_NEVER_REVEAL = "NEVER reveal that you are Mafia"
_COVER_IS_A_COVER = "This public face is a cover"


def test_mafioso_prompt_carries_true_self_and_cover_instruction() -> None:
    """A Mafioso speaker's Day prompt contains its ``true_self`` AND the
    stay-in-cover / never-reveal-you-are-Mafia instruction."""
    players = _day_players()
    mara = players["p-mara"]  # a Mafioso carrying a two-layer persona

    prompt = _render_day_prompt(mara, players)
    persona = mara.persona
    assert persona is not None

    # The true self is surfaced (only into the Mafioso's OWN prompt).
    assert persona.true_self in prompt, (
        "Mafioso true_self missing from its own Day prompt"
    )
    # And the explicit stay-in-cover, never-reveal-allegiance directive.
    assert _COVER_NEVER_REVEAL in prompt
    assert _COVER_IS_A_COVER in prompt


def test_citizen_prompt_has_persona_but_no_true_self_or_cover() -> None:
    """A Law-abiding speaker's Day prompt carries its persona but NO
    ``true_self``-style secret line and NO cover instruction."""
    players = _day_players()
    cleo = players["p-cleo"]  # a Law-abiding AI

    prompt = _render_day_prompt(cleo, players)
    persona = cleo.persona
    assert persona is not None

    # Its honest persona IS present.
    assert persona.public_persona in prompt
    assert persona.personality in prompt

    # But no secret-truth line and no cover instruction (Citizen has no cover).
    assert "YOUR SECRET TRUTH" not in prompt
    assert _COVER_NEVER_REVEAL not in prompt
    assert _COVER_IS_A_COVER not in prompt


def test_persona_block_mafioso_vs_citizen_directly() -> None:
    """Unit-level: ``_persona_block`` adds the secret-truth line for a Mafioso
    only, and the Citizen block carries no cover wording.

    Pins the role-tailoring branch on the helper itself (independent of the
    template render), so a regression in either layer is localised.
    """
    players = _day_players()
    maf_block = _persona_block(players["p-mara"])
    cit_block = _persona_block(players["p-cleo"])

    assert _DAY_MAFIA_PERSONA.true_self in maf_block
    assert _COVER_NEVER_REVEAL in maf_block

    assert _DAY_CITIZEN_PERSONA.public_persona in cit_block
    assert "YOUR SECRET TRUTH" not in cit_block
    assert _COVER_NEVER_REVEAL not in cit_block


# --------------------------------------------------------------------------
# 3. No-leak / knowledge boundary.
# --------------------------------------------------------------------------


def test_mafioso_public_persona_carries_no_allegiance_tell() -> None:
    """A Mafioso's ``public_persona`` (the legend the table sees) does NOT itself
    contain the ``true_self`` text nor the word 'mafia'.

    The legend is the clean cover string shown to everyone; it must read as an
    innocent townsperson. (The true self lives only in the owner's own prompt.)
    """
    legend = _DAY_MAFIA_PERSONA.public_persona
    assert _DAY_MAFIA_PERSONA.true_self not in legend, (
        "the Mafioso legend leaked its true self"
    )
    assert "mafia" not in legend.lower(), (
        "the Mafioso legend names 'mafia' — that is an allegiance tell"
    )


def test_mafioso_true_self_absent_from_another_speakers_prompt() -> None:
    """The Mafioso's ``true_self`` / cover instruction appears ONLY in that
    Mafioso's own prompt.

    Rendering the Day prompt for a DIFFERENT AI speaker (a fellow Mafioso AND a
    Citizen) must not surface Mara's secret self or the cover directive — the
    persona is private to its owner (spec 016 §2.3 privacy invariant).
    """
    players = _day_players()
    mara = players["p-mara"]
    secret = mara.persona.true_self

    # A different Mafioso (Max — no persona of its own here).
    other_mafia_prompt = _render_day_prompt(players["p-max"], players)
    assert secret not in other_mafia_prompt, (
        "Mara's true_self leaked into a fellow Mafioso's Day prompt"
    )
    assert _COVER_NEVER_REVEAL not in other_mafia_prompt, (
        "Mara's cover instruction leaked into another speaker's prompt"
    )

    # A Law-abiding speaker.
    citizen_prompt = _render_day_prompt(players["p-cleo"], players)
    assert secret not in citizen_prompt, (
        "Mara's true_self leaked into a Citizen's Day prompt"
    )
    assert _COVER_NEVER_REVEAL not in citizen_prompt


# --------------------------------------------------------------------------
# 4. Persona absent when None.
# --------------------------------------------------------------------------


def test_persona_block_none_returns_empty_and_prompt_still_renders() -> None:
    """``_persona_block`` on a ``PlayerState`` with ``persona=None`` returns ``""``
    and the Day-speech prompt still renders (no crash, no leftover ``{persona}``)."""
    players = _day_players()
    max_ = players["p-max"]  # an AI with NO persona attached
    assert max_.persona is None

    assert _persona_block(max_) == ""

    # The template still renders cleanly with the empty persona block.
    prompt = _render_day_prompt(max_, players)
    assert "{persona}" not in prompt
    assert max_.name in prompt  # "You are Max ..." — the prompt is intact


# ==========================================================================
# Slice 3 — Night-pointing prompt injection (secondary, tech-spec §2.4).
#
# The pointing Mafioso's persona (personality + manner) is threaded into
# ``MAFIA_POINT_USER_TEMPLATE`` via the ``{mafia_persona}`` slot, built inside
# ``_ai_pick_target`` from ``mafia.persona``. Night pointing is silent and
# Mafia-only, so the block surfaces the TRUE character (personality + manner),
# never a public cover.
#
# All OFFLINE. We assert STRUCTURAL presence of the supplied persona strings in
# the rendered pointing prompt — never persona prose the model would invent
# (architecture §6). The primary seam drives the REAL ``_ai_pick_target`` through
# the conftest ``fake_large_pointing`` content-recording fake (it records every
# ``.invoke`` message list), so the ``{mafia_persona}`` template WIRING is
# covered end-to-end; a direct ``.format(...)`` render is the second test.
# --------------------------------------------------------------------------

# A Mafioso whose pointing persona has a distinctive personality + manner. The
# strings share no substring with the roster names below so a rendered persona
# string is unambiguous to assert against.
_POINT_MAFIA_PERSONA = PlayerPersona(
    personality="ruthless and calculating",
    manner="speaks in cold, clipped imperatives",
    public_persona="the affable harbour clerk",
    true_self="the family's quiet enforcer who never misses",
)


def _pointing_human_text(messages: Any) -> str:
    """Concatenate every ``HumanMessage`` text in a captured pointing prompt.

    ``_ai_pick_target`` builds ``[SystemMessage(...), HumanMessage(...)]`` and,
    on the retry path, appends a second ``HumanMessage``. The rendered
    ``MAFIA_POINT_USER_TEMPLATE`` (carrying the persona block) lives in the first
    HumanMessage; joining all HumanMessage content keeps the assertion robust if
    the retry path ever fires.
    """
    from langchain_core.messages import HumanMessage

    return "\n".join(
        str(m.content) for m in messages if isinstance(m, HumanMessage)
    )


def test_mafioso_night_pointing_prompt_carries_persona_manner(
    fake_large_pointing,
) -> None:
    """A Mafioso's Night-pointing prompt carries its persona personality+manner.

    Drives the REAL ``_ai_pick_target`` through the content-recording pointing
    fake so the ``{mafia_persona}`` slot being filled from ``mafia.persona`` is
    exercised end-to-end (template wiring), then inspects the actual prompt text
    the model received for the personality and manner strings.
    """
    targets = [
        PlayerState(id="t1", name="Priya", role="law_abiding", is_human=False),
        PlayerState(id="t2", name="Silas", role="law_abiding", is_human=False),
    ]
    mafia = PlayerState(
        id="m1",
        name="Marco",
        role="mafia",
        is_human=False,
        persona=_POINT_MAFIA_PERSONA,
    )
    fake = fake_large_pointing(["t1"])

    chosen = _ai_pick_target(alive_law_abiding=targets, mafia=mafia)

    # The scripted pick is honoured (mechanics unaffected by the persona slot).
    assert chosen == "t1"
    assert fake.call_count == 1

    prompt = _pointing_human_text(fake.last_messages)
    assert _POINT_MAFIA_PERSONA.personality in prompt, (
        "Mafioso pointing personality missing from its own pointing prompt"
    )
    assert _POINT_MAFIA_PERSONA.manner in prompt, (
        "Mafioso pointing manner missing from its own pointing prompt"
    )


def test_mafia_point_template_renders_persona_block_directly() -> None:
    """A direct ``MAFIA_POINT_USER_TEMPLATE.format(...)`` carries the persona.

    Mirrors the real persona-block construction in ``_ai_pick_target`` (the
    ``"You are {personality} {manner}"`` line) so a reword of that block or the
    template breaks this render — the second, helper-free seam.
    """
    persona = _POINT_MAFIA_PERSONA
    mafia_persona_block = f"\nYou are {persona.personality} {persona.manner}\n"
    prompt = MAFIA_POINT_USER_TEMPLATE.format(
        roster="Priya: t1\nSilas: t2",
        mafia_persona=mafia_persona_block,
        prior_picks="No teammate has pointed yet this Night.",
    )

    assert "{mafia_persona}" not in prompt
    assert persona.personality in prompt
    assert persona.manner in prompt


def test_ai_pick_target_none_persona_is_safe(
    fake_large_pointing,
) -> None:
    """A Mafioso with ``persona=None`` still produces a valid pick and the prompt
    renders cleanly (no crash, no leftover ``{mafia_persona}`` placeholder)."""
    targets = [
        PlayerState(id="t1", name="Priya", role="law_abiding", is_human=False),
        PlayerState(id="t2", name="Silas", role="law_abiding", is_human=False),
    ]
    mafia = PlayerState(
        id="m1", name="Marco", role="mafia", is_human=False
    )  # no persona
    assert mafia.persona is None
    fake = fake_large_pointing(["t2"])

    chosen = _ai_pick_target(alive_law_abiding=targets, mafia=mafia)

    # A valid pick is still produced.
    assert chosen == "t2"
    assert chosen in {t.id for t in targets}
    assert fake.call_count == 1

    # The prompt rendered with no leftover placeholder and no persona "You are"
    # line (the None branch yields an empty block).
    prompt = _pointing_human_text(fake.last_messages)
    assert "{mafia_persona}" not in prompt
    assert "You are " not in prompt


def test_persona_slot_does_not_change_scripted_pick(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: adding a persona doesn't change which target a scripted
    pointing fake selects — the multi-round loop mechanics are undisturbed.

    Mirrors ``test_multi_round_consensus.py``'s early-agreement scenario (two AI
    Mafia, unanimous round 1), but gives one pointer a persona. The scripted
    unanimous pick still resolves to the same victim in one round — proving the
    Slice-3 ``{mafia_persona}`` slot is inert to pointing resolution. The spec-015
    suite already covers the loop in depth; this only confirms the persona slot
    didn't disturb it.
    """
    from graphia.nodes import night as night_mod
    from graphia.nodes.night import (
        mafia_point,
        mafia_round_start,
        resolve_night_kill,
        route_after_mafia_point,
    )

    players = {
        "m1": PlayerState(
            id="m1",
            name="Marco",
            role="mafia",
            is_human=False,
            persona=_POINT_MAFIA_PERSONA,
        ),
        "m2": PlayerState(id="m2", name="Yuki", role="mafia", is_human=False),
        "victim": PlayerState(
            id="victim", name="Priya", role="law_abiding", is_human=False
        ),
        "bystander": PlayerState(
            id="bystander", name="Silas", role="law_abiding", is_human=False
        ),
    }
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: ["m1", "m2"]
    )
    fake = fake_large_pointing(["victim", "victim"])

    state: dict = {
        "cycle": 1,
        "phase": "night",
        "players": players,
        "night_round": 1,
        "night_mafia_order": [],
        "night_pointer_index": 0,
        "night_round_picks": {},
        "night_rounds_log": [],
    }

    # Walk the Night pointing loop over the real node functions (the same edge
    # order the compiled graph uses), mirroring the spec-015 node-level driver.
    def _apply(delta: dict) -> None:
        for key, value in delta.items():
            if key in ("messages", "kill_log"):
                continue
            state[key] = value

    _apply(mafia_round_start(state))
    delta: dict = {}
    for _ in range(100):
        route = route_after_mafia_point(state)
        if route == "resolve_night_kill":
            delta = resolve_night_kill(state)
            break
        if route == "mafia_round_start":
            _apply(mafia_round_start(state))
            continue
        _apply(mafia_point(state))
    else:  # pragma: no cover — loop guard
        raise AssertionError("night pointing loop did not terminate")

    # Same outcome as the persona-free early-agreement case: one unanimous round.
    assert delta["players"]["victim"].is_alive is False
    assert delta["night_victim_count"] == 1
    assert fake.call_count == 2
    assert state["night_round"] == 1
    assert state["night_rounds_log"] == []
    assert state["night_round_picks"] == {"m1": "victim", "m2": "victim"}
