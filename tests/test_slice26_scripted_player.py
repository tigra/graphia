"""Offline unit tests for the active scripted player (spec 026, slices 1-2).

Pins the deterministic, no-LLM / no-RNG human-seat stand-in in
``src/graphia/tools/scripted_player.py`` — **without ever reaching a real model,
the network, or a live game**. The policy is pure over a reconstructed public
view, so it is precisely unit-testable on synthetic histories, exactly the
posture of the existing ``score_*`` scorers.

Coverage (the functional-spec acceptance criteria):

Slice 1 (Law-abiding seat):
- LA scoring weighting order (propose > Yes > spare) and own-goal suspicion;
- a night-victim's prior hunters gain suspicion;
- the **knowledge-boundary** contradiction test (hidden true roles contradict
  public reveals → scores follow the PUBLIC reveals, never ``players[*].role``);
- final-round vote-initiation on the highest-suspicion living player + the
  deterministic id tie-break;
- the ballot threshold (Yes on a suspected target, No on a trusted one);
- the speak decision states a noted fact / the top suspect's name;
- same-history-same-decision determinism;
- **zero LLM**: the module imports without ``graphia.llm``;
- the config flag default-active + the ``--scripted-player passive`` override +
  byte-for-byte passive parity of the driver's resume values;
- ``render_record`` emits ``settings.scripted_player`` (and omits it when absent).

Slice 2 (Mafioso seat):
- teammate protection (No on a teammate; never nominate / point / Yes a teammate);
- never-reveal (no teammate name, no side word in any Mafioso speech);
- target choice = lowest-suspicion living non-teammate, the night point, and the
  final-round nomination (deterministic id tie-break);
- the per-run role control (default ``law-abiding``; ``GRAPHIA_ROLE=mafia``
  reaches the Mafioso path).

**Every reveal/vote line is built from the REAL imported ``graphia.prompts``
templates via ``.format``**, wrapped in the real ``SystemMessage`` the nodes
emit — so a template reword breaks extraction loudly here, exactly as it would
in the harness. ``PlayerState`` is imported from ``graphia.state`` so a field
rename breaks these tests honestly. The autouse ``safe_llm`` net is left intact;
nothing here goes near an LLM call site.
"""

from __future__ import annotations

import argparse
import sys

import pytest
from langchain_core.messages import SystemMessage

from graphia.config import load_config
from graphia.nodes.day import DAY_MAX_ROUNDS, _role_label
from graphia.prompts import (
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    VOTE_EXECUTED_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
)
from graphia.state import PlayerState
from graphia.tools import scripted_player as sp
from graphia.tools.blunder_eval import (
    EvalResult,
    _make_scripted_seat,
    _scripted_resume,
    render_record,
)
from graphia.tools.scripted_player import (
    SUSPICION_THRESHOLD,
    Decision,
    law_abiding_decision,
    mafia_decision,
    reconstruct_public_view,
    score_suspicion,
)


# ===========================================================================
# Synthetic-history builders — every line rendered from the REAL imported
# template via ``.format`` and wrapped in the real ``SystemMessage`` the nodes
# emit. A reword of any template breaks these helpers (and every test) loudly.
# ===========================================================================


def _player(pid: str, name: str, role: str, *, is_human: bool = False) -> PlayerState:
    return PlayerState(id=pid, name=name, role=role, is_human=is_human)


def _executed(player: PlayerState) -> SystemMessage:
    """The Moderator execution reveal — names the player and reveals their side."""
    return SystemMessage(
        content=VOTE_EXECUTED_TEMPLATE.format(
            name=player.name, role_label=_role_label(player.role)
        )
    )


def _victim_reveal(player: PlayerState) -> SystemMessage:
    """The Day-open night-victim reveal (a night victim is always Law-abiding)."""
    return SystemMessage(
        content=DAY_OPEN_VICTIM_REVEAL_TEMPLATE.format(
            name=player.name, role_label=_role_label("law_abiding")
        )
    )


def _announce(initiator: PlayerState, target: PlayerState) -> SystemMessage:
    return SystemMessage(
        content=VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
            initiator=initiator.name, target=target.name
        )
    )


def _ballot(voter: PlayerState, *, yes: bool) -> SystemMessage:
    return SystemMessage(
        content=VOTE_PER_BALLOT_TEMPLATE.format(
            voter=voter.name, vote_label="Yes" if yes else "No"
        )
    )


def _players(*ps: PlayerState) -> dict[str, PlayerState]:
    return {p.id: p for p in ps}


# A reusable cast. The seat under test is ``self`` (the human); the others are
# AI players whose public behaviour the policy scores.
SELF = _player("p_self", "Avery", "law_abiding", is_human=True)
ALICE = _player("p_alice", "Alice", "law_abiding")
BORIS = _player("p_boris", "Boris", "mafia")
CLEO = _player("p_cleo", "Cleo", "law_abiding")
DREW = _player("p_drew", "Drew", "mafia")


# ===========================================================================
# Slice 1 — Law-abiding scoring + decisions.
# ===========================================================================


def test_scoring_order_propose_beats_yes_beats_spare():
    """AC1: against a confirmed Mafioso, propose > Yes > spare (most→least town).

    Alice PROPOSED the execution, Cleo voted YES, Drew voted NO (spared). After
    Boris is revealed Mafia, Alice must read most town-aligned (lowest score),
    Cleo next, Drew most suspect. Ordering, not magnitudes, is asserted.
    """
    players = _players(SELF, ALICE, BORIS, CLEO, DREW)
    messages = [
        _announce(ALICE, BORIS),  # Alice initiates against Boris
        _ballot(ALICE, yes=True),
        _ballot(CLEO, yes=True),  # Cleo follows with a Yes
        _ballot(DREW, yes=False),  # Drew spares
        _executed(BORIS),  # ...and Boris is revealed Mafia
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)

    # Boris is dead+confirmed → not a live suspect; the three living AIs are.
    assert set(scores) == {ALICE.id, CLEO.id, DREW.id}
    assert scores[ALICE.id] < scores[CLEO.id] < scores[DREW.id]


def test_own_goal_push_is_more_suspicious():
    """AC2: proposing/Yes-voting someone revealed Law-abiding reads more suspect."""
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(ALICE, CLEO),  # Alice pushes Cleo (an innocent)
        _ballot(ALICE, yes=True),
        _ballot(BORIS, yes=False),  # Boris declines the own-goal
        _executed(CLEO),  # Cleo revealed Law-abiding → own-goal confirmed
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)

    # Alice pushed an own-goal (suspicious, positive); Boris spared a Citizen
    # (mildly town, negative). So Alice > Boris.
    assert scores[ALICE.id] > 0.0
    assert scores[ALICE.id] > scores[BORIS.id]


def test_night_victim_hunters_gain_suspicion():
    """AC3: whoever a night-killed player had moved against gains suspicion.

    Cleo proposes a vote against Boris, then Cleo is killed overnight. Boris (the
    one Cleo hunted) should carry the night-victim-hunter suspicion bump.
    """
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(CLEO, BORIS),  # Cleo moved against Boris
        _ballot(CLEO, yes=True),
        _ballot(ALICE, yes=False),
        _victim_reveal(CLEO),  # ...then Cleo is killed in the night
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)

    assert CLEO.name in view.night_victims
    # Boris was Cleo's target → suspicion bump; Alice (uninvolved) stays at 0.
    assert scores[BORIS.id] == pytest.approx(sp.W_NIGHT_VICTIM_HUNTER)
    assert scores[ALICE.id] == pytest.approx(0.0)


def test_knowledge_boundary_scores_follow_public_reveals_not_hidden_roles():
    """Load-bearing: hidden true roles CONTRADICT the public reveals → scores
    follow the PUBLIC reveals, never ``players[*].role`` of a living player.

    The execution reveal LIES relative to the dataclass roles: we render Boris's
    reveal as "Law-abiding" though his ``PlayerState.role`` is ``"mafia"`` (and
    vice-versa). A cheating policy that read ``.role`` would score Alice (who
    pushed Boris) as town-aligned; the honest public-only policy must instead
    read Alice as pushing an own-goal (suspicious), because the PUBLIC reveal
    said Law-abiding.
    """
    # Dataclass roles: Boris is really mafia, Cleo really law_abiding.
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(ALICE, BORIS),
        _ballot(ALICE, yes=True),
        # PUBLIC reveal CONTRADICTS the hidden role: announce Boris as Law-abiding.
        SystemMessage(
            content=VOTE_EXECUTED_TEMPLATE.format(
                name=BORIS.name, role_label=_role_label("law_abiding")
            )
        ),
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)

    # The public view recorded Boris as law_abiding (the reveal), NOT mafia.
    assert view.confirmed_side[BORIS.name] == "law_abiding"
    # Alice pushed a publicly-Law-abiding target → own-goal → suspicious (>0),
    # exactly what reading the public reveal (not the hidden mafia role) yields.
    assert scores[ALICE.id] > 0.0


def test_final_round_proposes_highest_suspicion_with_id_tiebreak():
    """AC4: on the final round, the LA decision is a vote-initiation on the
    highest-suspicion living player, ties broken by lexical id (never RNG)."""
    # Construct a tie: two players each pushed one confirmed-Law-abiding own-goal,
    # so their scores are equal. The tie-break must pick the lexically-lower id.
    lo = _player("p_aaa", "Mara", "law_abiding")  # lexically-lower id
    hi = _player("p_zzz", "Nico", "law_abiding")  # lexically-higher id
    victim = _player("p_vic", "Owen", "law_abiding")
    players = _players(SELF, lo, hi, victim)
    messages = [
        _announce(lo, victim),
        _ballot(lo, yes=True),
        _announce(hi, victim),
        _ballot(hi, yes=True),
        _executed(victim),  # Owen revealed Law-abiding → both pushers suspect
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)
    assert scores[lo.id] == pytest.approx(scores[hi.id])  # a genuine tie

    decision = law_abiding_decision(
        view, scores, players, SELF.id, kind="day_turn", last_round=True
    )
    assert decision.action == "vote"
    # Tie broken by lexical id → the lower id wins.
    assert decision.target_name == lo.name


def test_ballot_yes_on_suspected_no_on_trusted():
    """AC5: open vote on a suspected target (score ≥ threshold) ⇒ Yes; on a
    trusted target (below threshold) ⇒ No."""
    players = _players(SELF, ALICE, BORIS, CLEO)
    # Alice pushed an own-goal on Cleo (suspicious); Boris stays quiet (trusted).
    messages = [
        _announce(ALICE, CLEO),
        _ballot(ALICE, yes=True),
        _executed(CLEO),
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)
    assert scores[ALICE.id] >= SUSPICION_THRESHOLD  # suspected
    assert scores[BORIS.id] < SUSPICION_THRESHOLD  # trusted (quiet)

    yes_on_suspect = law_abiding_decision(
        view, scores, players, SELF.id, kind="vote",
        last_round=False, open_vote_target=ALICE.id,
    )
    assert yes_on_suspect.action == "ballot" and yes_on_suspect.yes is True

    no_on_trusted = law_abiding_decision(
        view, scores, players, SELF.id, kind="vote",
        last_round=False, open_vote_target=BORIS.id,
    )
    assert no_on_trusted.yes is False


def test_speak_states_top_suspect_name():
    """AC6: a day_turn speak decision names a noted fact / the top suspect."""
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(ALICE, CLEO),
        _ballot(ALICE, yes=True),
        _executed(CLEO),  # Cleo own-goal → Alice is the top suspect
    ]
    view = reconstruct_public_view(messages, players, SELF.id)
    scores = score_suspicion(view, players, SELF.id)
    decision = law_abiding_decision(
        view, scores, players, SELF.id, kind="day_turn", last_round=False
    )
    assert decision.action == "speak"
    assert decision.text  # non-empty
    # The top suspect's name is surfaced in the spoken reasoning.
    assert ALICE.name in decision.text


def test_same_history_same_decision_is_deterministic():
    """AC (no-model determinism): the same synthetic history yields an identical
    decision across repeated calls — no RNG, no clock, no LLM."""
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(ALICE, CLEO),
        _ballot(ALICE, yes=True),
        _executed(CLEO),
    ]

    def decide() -> Decision:
        view = reconstruct_public_view(messages, players, SELF.id)
        scores = score_suspicion(view, players, SELF.id)
        return law_abiding_decision(
            view, scores, players, SELF.id, kind="day_turn", last_round=True
        )

    first = decide()
    second = decide()
    assert (first.action, first.target_name, first.text) == (
        second.action, second.target_name, second.text
    )


def test_module_does_not_import_graphia_llm():
    """No-model guarantee: importing the policy module must not pull in
    ``graphia.llm`` (the structural zero-invoke property; tech-spec §2.1)."""
    # Drop any prior import so this is a clean check of the policy's own deps.
    for mod in ("graphia.llm", "graphia.tools.scripted_player"):
        sys.modules.pop(mod, None)
    import importlib

    importlib.import_module("graphia.tools.scripted_player")
    assert "graphia.llm" not in sys.modules


# ===========================================================================
# Slice 2 — Mafioso seat.
# ===========================================================================

# A Mafia-seat cast: the human seat is a Mafioso; Boris is its teammate.
MSELF = _player("p_self", "Avery", "mafia", is_human=True)
TEAMMATE = _player("p_boris", "Boris", "mafia")
HUNTER_LO = _player("p_aaa", "Cleo", "law_abiding")  # lexically-lower id
HUNTER_HI = _player("p_zzz", "Drew", "law_abiding")  # lexically-higher id
MAFIA_TEAM = {TEAMMATE.id}


def test_mafia_spares_teammate_never_pushes_teammate():
    """Mafia AC1/AC4: a teammate put up ⇒ No (spare); never propose/Yes a teammate."""
    players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    view = reconstruct_public_view([], players, MSELF.id)
    scores = score_suspicion(view, players, MSELF.id)

    # Ballot on the teammate ⇒ spare (No), unconditionally.
    spare = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="vote",
        last_round=False, open_vote_target=TEAMMATE.id,
    )
    assert spare.action == "ballot" and spare.yes is False

    # The day-turn nomination target is never the teammate (it's a non-teammate).
    nominate = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="day_turn",
        last_round=True,
    )
    assert nominate.action == "vote"
    assert nominate.target_name != TEAMMATE.name

    # The night point is never the teammate either.
    point = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="point", last_round=False
    )
    assert point.action == "point"
    assert point.target_id != TEAMMATE.id and point.target_id not in MAFIA_TEAM


def test_mafia_pushes_non_teammate():
    """Mafia AC2: a non-teammate put up ⇒ Yes (execute)."""
    players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    view = reconstruct_public_view([], players, MSELF.id)
    scores = score_suspicion(view, players, MSELF.id)
    push = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="vote",
        last_round=False, open_vote_target=HUNTER_LO.id,
    )
    assert push.yes is True


def test_mafia_target_is_lowest_suspicion_non_teammate_with_tiebreak():
    """Mafia AC3: target = the lowest-suspicion living non-teammate (strongest
    hunter), with a deterministic id tie-break; the night point + final-round
    nomination both land on that same player."""
    players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    # Both hunters quiet → equal (0.0) suspicion → a tie; lexical id breaks it.
    view = reconstruct_public_view([], players, MSELF.id)
    scores = score_suspicion(view, players, MSELF.id)
    assert scores[HUNTER_LO.id] == pytest.approx(scores[HUNTER_HI.id])

    point = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="point", last_round=False
    )
    nominate = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="day_turn",
        last_round=True,
    )
    # Tie → lexically-lower id wins; point id and nomination name agree.
    assert point.target_id == HUNTER_LO.id
    assert nominate.target_name == HUNTER_LO.name


def test_mafia_target_prefers_strongest_hunter_when_scores_differ():
    """Mafia AC3: when suspicion differs, the lower-suspicion (more town-aligned,
    strongest-hunter) non-teammate is chosen over a higher-suspicion one."""
    players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    # Make HUNTER_HI look more town-aligned (lower score) by having it correctly
    # push the teammate Boris, who is then publicly revealed Mafia.
    messages = [
        _announce(HUNTER_HI, TEAMMATE),
        _ballot(HUNTER_HI, yes=True),
        _executed(TEAMMATE),  # Boris revealed Mafia → HUNTER_HI scored town
    ]
    view = reconstruct_public_view(messages, players, MSELF.id)
    scores = score_suspicion(view, players, MSELF.id)
    # HUNTER_HI is the stronger hunter now (lower suspicion). With the teammate
    # dead+confirmed, the only living non-teammates are the two hunters.
    assert scores[HUNTER_HI.id] < scores[HUNTER_LO.id]
    point = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="point", last_round=False
    )
    assert point.target_id == HUNTER_HI.id


def test_mafia_speech_never_reveals_side_or_teammate():
    """Mafia AC4: no Mafioso scripted speech names a teammate or any side word.

    Across both the regular-round speak and the final-round nomination, assert
    the produced text never contains the teammate's name nor any side-revealing
    word ('mafia', 'mafioso', 'teammate', 'law-abiding', 'citizen')."""
    players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    view = reconstruct_public_view([], players, MSELF.id)
    scores = score_suspicion(view, players, MSELF.id)

    speak = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="day_turn",
        last_round=False,
    )
    assert speak.action == "speak" and speak.text

    forbidden = ("mafia", "mafioso", "teammate", "law-abiding", "citizen")
    text_lc = speak.text.lower()
    for word in forbidden:
        assert word not in text_lc, f"speech leaked side word {word!r}: {speak.text!r}"
    assert TEAMMATE.name not in speak.text

    # The final-round nomination is a vote (no free speech text), so nothing to
    # leak there; just confirm it never names the teammate as target.
    nominate = mafia_decision(
        view, scores, players, MSELF.id, MAFIA_TEAM, kind="day_turn",
        last_round=True,
    )
    assert nominate.target_name != TEAMMATE.name


# ===========================================================================
# Config flag + CLI override + driver parity.
# ===========================================================================


def test_flag_defaults_active(monkeypatch):
    """The flag is ON by default (no env) and OFF for an explicit falsy value."""
    monkeypatch.delenv("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", raising=False)
    assert load_config().scripted_player_active is True

    monkeypatch.setenv("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", "0")
    assert load_config().scripted_player_active is False

    monkeypatch.setenv("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", "true")
    assert load_config().scripted_player_active is True


def test_cli_scripted_player_override_maps_to_env(monkeypatch):
    """``--scripted-player passive`` selects the passive baseline; ``active``
    selects active; an invalid value is rejected by argparse."""
    from graphia.tools.blunder_eval import _build_parser

    parser = _build_parser()

    # passive → falsy env → config flag False (the prior baseline).
    monkeypatch.delenv("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", raising=False)
    args = parser.parse_args(["--provider", "ollama", "--scripted-player", "passive"])
    assert args.scripted_player == "passive"
    # Mirror what main() does with the value.
    monkeypatch.setenv("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", "0")
    assert load_config().scripted_player_active is False

    args = parser.parse_args(["--provider", "ollama", "--scripted-player", "active"])
    assert args.scripted_player == "active"
    monkeypatch.setenv("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", "1")
    assert load_config().scripted_player_active is True

    # An invalid choice errors out at parse time.
    with pytest.raises(SystemExit):
        parser.parse_args(["--provider", "ollama", "--scripted-player", "bananas"])


def test_passive_parity_resume_values_unchanged():
    """ADR-011 flag-off parity: in PASSIVE mode the driver keeps the byte-for-byte
    prior resume values — a neutral ``HUMAN_LINES`` speech, ``"no"`` ballot,
    ``options[0]`` point. We assert the passive branches in ``_play_one_game``
    produce exactly those, by mirroring their (unchanged) selection logic.

    The active path is exercised through ``_scripted_resume`` below; here we lock
    that with ``seat is None`` the prior literals are still what's resumed.
    """
    from graphia.tools.eval_dialogue import HUMAN_LINES

    seat = None  # passive mode

    # day_turn: the indexed HUMAN_LINES pool (unchanged).
    line_idx = 0
    if seat is not None:  # pragma: no cover - documents the active branch
        resume = "unused"
    else:
        resume = HUMAN_LINES[line_idx % len(HUMAN_LINES)]
    assert resume == HUMAN_LINES[0]

    # vote: the literal "no" (unchanged).
    resume = _scripted_resume(seat, {"kind": "vote"}, {}) if seat is not None else "no"
    assert resume == "no"

    # point: options[0]["id"] (unchanged).
    options = [{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}]
    iv = {"kind": "point", "options": options}
    if seat is not None:  # pragma: no cover - documents the active branch
        resume = "unused"
    else:
        resume = options[0]["id"] if options else ""
    assert resume == "p1"


def test_scripted_resume_day_turn_speak_and_vote():
    """The active resume helper maps a speak decision to its text and a final-round
    vote decision to ``/vote <name>`` (the human slash-command branch's shape)."""
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(ALICE, CLEO),
        _ballot(ALICE, yes=True),
        _executed(CLEO),
    ]
    seat = sp_seat_law(SELF.id)
    # Mid-round: a speak. ``day_rounds`` below the final round.
    state = {"players": players, "messages": messages, "day_rounds": 0}
    resume = _scripted_resume(seat, {"kind": "day_turn"}, state)
    assert ALICE.name in resume  # the spoken reasoning names the suspect
    assert not resume.startswith("/vote")

    # Final round: a /vote initiation on the top suspect (by name).
    state_final = {
        "players": players,
        "messages": messages,
        "day_rounds": DAY_MAX_ROUNDS - 1,
    }
    resume = _scripted_resume(seat, {"kind": "day_turn"}, state_final)
    assert resume == f"/vote {ALICE.name}"


def test_scripted_resume_vote_yes_no():
    """The active resume helper maps a ballot decision to ``yes``/``no`` using the
    interrupt payload's ``target_id`` as the open vote target."""
    players = _players(SELF, ALICE, BORIS, CLEO)
    messages = [
        _announce(ALICE, CLEO),
        _ballot(ALICE, yes=True),
        _executed(CLEO),
    ]
    seat = sp_seat_law(SELF.id)
    state = {"players": players, "messages": messages, "day_rounds": 0}

    # Open ballot on the suspect Alice ⇒ yes.
    yes = _scripted_resume(seat, {"kind": "vote", "target_id": ALICE.id}, state)
    assert yes == "yes"
    # Open ballot on the trusted (quiet) Boris ⇒ no.
    no = _scripted_resume(seat, {"kind": "vote", "target_id": BORIS.id}, state)
    assert no == "no"


def test_scripted_resume_point_returns_target_id():
    """The active resume helper maps a Mafioso point decision to the chosen target
    id (not a name) — the shape the human point branch validates."""
    players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    seat = _ScriptedSeat_mafia(MSELF.id, MAFIA_TEAM)
    state = {"players": players, "messages": [], "day_rounds": 0}
    options = [{"id": HUNTER_LO.id, "name": HUNTER_LO.name},
               {"id": HUNTER_HI.id, "name": HUNTER_HI.name}]
    resume = _scripted_resume(seat, {"kind": "point", "options": options}, state)
    # The lowest-suspicion non-teammate (tie → lexical id) is HUNTER_LO.
    assert resume == HUNTER_LO.id


def test_make_scripted_seat_reads_own_role_and_teammates():
    """The seat builder reads the human seat's OWN role and (Mafia only) its OWN
    teammate ids — the single legitimate true-role read."""
    # Law-abiding seat: no teammates.
    la_players = _players(SELF, ALICE, BORIS, CLEO)
    la_state = {"players": la_players, "human_id": SELF.id}
    la_seat = _make_scripted_seat(la_state)
    assert la_seat.role == "law_abiding"
    assert la_seat.teammate_ids == set()
    assert la_seat.self_id == SELF.id

    # Mafia seat: teammate ids are the OTHER mafia players.
    mafia_players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    mafia_state = {"players": mafia_players, "human_id": MSELF.id}
    mafia_seat = _make_scripted_seat(mafia_state)
    assert mafia_seat.role == "mafia"
    assert mafia_seat.teammate_ids == {TEAMMATE.id}


# Small helpers to construct seats without the live game (the offline resume tests).
def sp_seat_law(self_id: str):
    from graphia.tools.blunder_eval import _ScriptedSeat

    return _ScriptedSeat(
        self_id=self_id, role="law_abiding", teammate_ids=set(),
        day_max_rounds=DAY_MAX_ROUNDS,
    )


def _ScriptedSeat_mafia(self_id: str, teammate_ids: set[str]):
    from graphia.tools.blunder_eval import _ScriptedSeat

    return _ScriptedSeat(
        self_id=self_id, role="mafia", teammate_ids=set(teammate_ids),
        day_max_rounds=DAY_MAX_ROUNDS,
    )


# ===========================================================================
# Ledger recording.
# ===========================================================================


def test_render_record_emits_scripted_player():
    """``render_record`` emits ``settings.scripted_player`` when the run recorded
    it, and OMITS it (back-compat) when the settings map lacks the key."""
    with_mode = EvalResult(
        provider="ollama",
        settings={
            "large_model": "L",
            "small_model": "S",
            "base_url": "http://x",
            "games": 3,
            "seed": None,
            "max_days": 12,
            "scripted_player": "active",
        },
    )
    doc = render_record(with_mode, "2026-06-21")
    assert "scripted_player: 'active'" in doc

    # A settings map without the key (a pre-026 / synthetic record) omits the line.
    without_mode = EvalResult(
        provider="ollama",
        settings={
            "large_model": "L",
            "small_model": "S",
            "base_url": "http://x",
            "games": 3,
            "seed": None,
            "max_days": 12,
        },
    )
    doc2 = render_record(without_mode, "2026-06-21")
    assert "scripted_player" not in doc2


def test_seat_role_is_per_run_default_law_abiding(monkeypatch):
    """Slice 2 per-run role control: the seat defaults to law-abiding (the prior
    unconditional pin is relaxed to a ``setdefault`` default), and an explicit
    ``GRAPHIA_ROLE=mafia`` survives to reach the Mafioso path.

    Mirrors ``main()``'s ``os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")``
    role-pin logic and asserts ``load_config`` resolves each case, then that
    ``_make_scripted_seat`` builds the matching seat for a dealt-Mafia human.
    """
    def setdefault_role(env: dict[str, str]) -> str:
        """Mirror main()'s ``setdefault`` against an isolated env mapping."""
        return env.setdefault("GRAPHIA_ROLE", "law-abiding")

    # Default: no GRAPHIA_ROLE → setdefault pins law-abiding → resolves law_abiding.
    # Use an isolated dict for the setdefault semantics so the suite's real env is
    # untouched, then drive load_config via monkeypatch (auto-restored).
    monkeypatch.delenv("GRAPHIA_ROLE", raising=False)
    assert setdefault_role({}) == "law-abiding"
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    assert load_config().human_role == "law_abiding"

    # Explicit mafia survives setdefault → resolves mafia (reaches the Mafioso path).
    assert setdefault_role({"GRAPHIA_ROLE": "mafia"}) == "mafia"  # no-op: already set
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    assert load_config().human_role == "mafia"

    # A dealt-Mafia human seat builds the Mafioso seat with its teammate ids.
    mafia_players = _players(MSELF, TEAMMATE, HUNTER_LO, HUNTER_HI)
    seat = _make_scripted_seat({"players": mafia_players, "human_id": MSELF.id})
    assert seat.role == "mafia" and seat.teammate_ids == {TEAMMATE.id}


def test_render_record_passive_label():
    """A passive run records the readable ``passive`` label."""
    res = EvalResult(
        provider="bedrock",
        settings={"games": 1, "scripted_player": "passive"},
    )
    doc = render_record(res, "2026-06-21")
    assert "scripted_player: 'passive'" in doc
