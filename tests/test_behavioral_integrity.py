"""Spec 013, Slice 3 — deterministic offline tests for the behaviour-fix prompts.

The behaviour fixes themselves (the self-vote / teammate-vote reductions, the
Day-passivity nudge) are **non-deterministic prompt nudges** measured live in
Slice 4 (``make blunder-eval`` before/after). What IS deterministically testable
— and what this file pins — are the four things that survive into code:

1. **Knowledge boundary (functional-spec §2.5, the load-bearing one):** the role
   grounding discloses only what an actor's role legitimately knows. A Mafioso's
   day-speak prompt names its fellow Mafiosi; a Law-abiding Citizen's prompt
   carries NO teammate list and NO other-player allegiance. The ballot
   ``{relationship}`` flag emits only "is YOU" / "is your fellow Mafioso" / "" —
   never "fellow Citizen", so a law-abiding voter's ballot leaks nothing.
2. **Win-condition congruence (§2.5):** ``_win_condition_line`` wording matches
   the rule ``check_win_condition`` actually enforces.
3. **Template-field formatting (KeyError guard, §4):** every new ``.format``
   placeholder is supplied for every actor kind, on both the first and retry
   message blocks — drive the real ``_ai_day_action`` / ``_ai_ballot`` through
   the fakes for a mafioso AND a citizen AND self/peer/other ballot targets.
4. **``self_vote.initiation`` regression pin (§2.6):** ``_accept`` (inside
   ``_ai_day_action``) still rejects a self-targeted vote initiation and falls
   back, unchanged by the prompt rework.
5. **Human self-vote path unchanged:** the spec-004 self-vote tests stay green
   (asserted by re-running them; the human ballot path got no AI-branch guard).
6. **Speaker-resolver integrity (the bug Task 1 fixed):** ``blunder_eval``'s
   ``_DAY_SPEAKER_RE`` still matches a real day-speak prompt and does NOT
   spuriously match ``DAY_SPEAK_SYSTEM`` — the anchor-coupling regression guard.

These import the REAL helpers/templates, so a future reword breaks the test
(the template-coupling discipline). No production code is modified; no test here
reaches a real model (``safe_llm`` is untouched — the AI-path tests install the
unified ``fake_large``).
"""

from __future__ import annotations

import pytest

import graphia.nodes.day as day_nodes
from graphia.llm import Ballot, DayAction
from graphia.nodes.day import (
    _ai_ballot,
    _ai_day_action,
    _ballot_relationship,
    _team_line,
    _teammates_str,
    _win_condition_line,
)
from graphia.nodes.endgame import check_win_condition
from graphia.prompts import (
    AI_VOTE_USER_TEMPLATE,
    DAY_SPEAK_SYSTEM,
    DAY_SPEAK_USER_TEMPLATE,
)
from graphia.state import PlayerState
from graphia.tools.blunder_eval import _DAY_SPEAKER_RE


# --------------------------------------------------------------------------
# Roster builders — a small fixed table used across the boundary tests.
# One human Law-abiding ("Alice"), two Mafia AIs ("Mara", "Max"), and two
# Law-abiding AIs ("Cleo", "Cody"). Mafia names share no substring with the
# citizen names so a rendered teammate list is unambiguous to assert against.
# --------------------------------------------------------------------------


def _players() -> dict[str, PlayerState]:
    roster = [
        PlayerState(id="p-human", name="Alice", role="law_abiding", is_human=True),
        PlayerState(id="p-mara", name="Mara", role="mafia", is_human=False),
        PlayerState(id="p-max", name="Max", role="mafia", is_human=False),
        PlayerState(id="p-cleo", name="Cleo", role="law_abiding", is_human=False),
        PlayerState(id="p-cody", name="Cody", role="law_abiding", is_human=False),
    ]
    return {p.id: p for p in roster}


def _state(players: dict[str, PlayerState]) -> dict:
    """Minimal GameState dict good enough for ``_ai_day_action`` / ``_ai_ballot``.

    Both read only ``players`` and ``messages`` off state, so a bare dict with
    those two keys is a faithful hand-built state delta input.
    """
    return {"players": players, "messages": []}


def _render_day_prompt(actor: PlayerState, players: dict[str, PlayerState]) -> str:
    """Render the REAL ``DAY_SPEAK_USER_TEMPLATE`` for ``actor`` via the real helpers.

    Mirrors exactly the field computation in ``_ai_day_action`` (role_label,
    win_condition, team_line, roster, context) so a reword of any of those
    helpers or of the template breaks this render.
    """
    return DAY_SPEAK_USER_TEMPLATE.format(
        speaker=actor.name,
        role_label=day_nodes._role_label(actor.role),
        win_condition=_win_condition_line(actor.role),
        team_line=_team_line(actor, players),
        roster=day_nodes._render_alive_roster(players),
        context="(no prior discussion)",
    )


# ==========================================================================
# Concern 1 — Knowledge boundary (functional-spec §2.5)
# ==========================================================================


def test_mafioso_day_prompt_names_fellow_mafiosi() -> None:
    """A Mafioso's day-speak prompt discloses its (and only its) fellow Mafiosi."""
    players = _players()
    mara = players["p-mara"]

    prompt = _render_day_prompt(mara, players)

    # The team line is non-empty and lists the OTHER mafioso by name.
    team_line = _team_line(mara, players)
    assert team_line != ""
    assert team_line in prompt
    assert "Max" in prompt  # the fellow Mafioso
    # The teammates helper enumerates mafia peers excluding self.
    assert _teammates_str(mara, players) == "Max"
    assert "Mara" not in _teammates_str(mara, players)


def test_citizen_day_prompt_has_no_team_line_and_no_allegiance_labels() -> None:
    """A Law-abiding Citizen's prompt leaks no teammate list and no allegiance.

    The only player names that may appear are in the neutral ``name: id`` roster
    — never labelled by side. ``_team_line`` for a citizen is ``""`` (the
    knowledge-boundary invariant), and the rendered prompt must contain no
    "fellow Mafiosi"/"fellow Citizen" disclosure.
    """
    players = _players()
    cleo = players["p-cleo"]

    # Invariant: a citizen's team line is empty — no teammate list, ever.
    assert _team_line(cleo, players) == ""

    prompt = _render_day_prompt(cleo, players)

    # No teammate-DISCLOSURE phrasing: the template's static "never reveal your
    # teammates" instruction is fine (it appears for every actor and discloses
    # nothing); what must be absent is a rendered teammate LIST or any
    # allegiance label naming another player. The mafia-only "Your fellow
    # Mafiosi (keep this secret): …" line and any "fellow Citizen" label must
    # not appear.
    assert "Your fellow Mafiosi" not in prompt
    assert "fellow Mafioso" not in prompt
    assert "fellow Citizen" not in prompt

    # The citizen's own role/win-condition IS grounded (legitimate self-knowledge).
    assert "Law-abiding Citizen" in prompt
    assert _win_condition_line("law_abiding") in prompt

    # Every other player's name that appears does so ONLY in the neutral
    # "Name: id" roster line — never adjacent to an allegiance label. The
    # roster is the only place other names appear, so each other-name line must
    # match the "Name: id" shape produced by ``_render_alive_roster``.
    roster_block = day_nodes._render_alive_roster(players)
    for other in players.values():
        if other.id == cleo.id:
            continue
        # The other player's name appears in the roster block exactly as "Name: id".
        assert f"{other.name}: {other.id}" in roster_block
        # And outside the roster block, the prompt does not pair that name with
        # a side label (no "Mara is your fellow ...", etc.).
        prompt_without_roster = prompt.replace(roster_block, "")
        assert other.name not in prompt_without_roster


def test_ballot_relationship_self_is_you() -> None:
    """Self-targeted ballot → the 'is YOU' self-sacrifice nudge."""
    players = _players()
    mara = players["p-mara"]
    rel = _ballot_relationship(mara, mara)
    assert "is YOU" in rel
    assert "Mara" in rel
    # It is a nudge, not an imperative ban.
    assert "self-sacrifice" in rel


def test_ballot_relationship_mafia_on_mafia_is_fellow_mafioso() -> None:
    """A Mafia voter on a Mafia target → the 'fellow Mafioso' bussing nudge."""
    players = _players()
    mara = players["p-mara"]
    max_ = players["p-max"]
    rel = _ballot_relationship(mara, max_)
    assert "fellow Mafioso" in rel
    assert "Max" in rel
    assert "bus" in rel.lower()


def test_ballot_relationship_law_abiding_voter_on_any_target_is_empty() -> None:
    """A Law-abiding voter never gets an allegiance label on any other target.

    The knowledge boundary: a citizen's ballot relationship is only ever
    "is YOU" (self) or "" — NEVER "fellow Citizen". This sweeps the citizen
    voter across a mafia target, another citizen, and the human.
    """
    players = _players()
    cleo = players["p-cleo"]  # law-abiding AI voter
    for target_id in ("p-mara", "p-cody", "p-human"):
        target = players[target_id]
        rel = _ballot_relationship(cleo, target)
        assert rel == "", (
            f"law-abiding voter on {target.name} should yield no relationship "
            f"label; got {rel!r}"
        )
        assert "fellow Citizen" not in rel


def test_ballot_relationship_mafia_voter_on_law_abiding_target_is_empty() -> None:
    """A Mafia voter on a Law-abiding target → "" (no disclosure of the target's side)."""
    players = _players()
    mara = players["p-mara"]  # mafia voter
    cleo = players["p-cleo"]  # law-abiding target
    assert _ballot_relationship(mara, cleo) == ""


def test_ballot_relationship_self_takes_precedence_for_law_abiding() -> None:
    """A law-abiding voter on THEMSELF still gets the 'is YOU' nudge (self wins)."""
    players = _players()
    cleo = players["p-cleo"]
    rel = _ballot_relationship(cleo, cleo)
    assert "is YOU" in rel


# ==========================================================================
# Concern 2 — Win-condition congruence with check_win_condition
# ==========================================================================


def test_win_condition_line_mafia_matches_parity_rule() -> None:
    """The mafia win-condition wording matches ``check_win_condition``'s parity rule.

    ``check_win_condition`` declares Mafia the winner when
    ``alive_mafia >= alive_law`` — i.e. "greater than or equal to". The prompt
    objective the model optimises must use that exact relation.
    """
    line = _win_condition_line("mafia")
    assert "Mafia" in line
    assert "greater than or equal to" in line
    assert "Law-abiding" in line

    # Behavioural anchor: a parity board (mafia == law) is a Mafia win per the
    # mechanical rule, which is exactly what "greater than or equal to" encodes.
    players = {
        "m1": PlayerState(id="m1", name="M1", role="mafia", is_human=False),
        "l1": PlayerState(id="l1", name="L1", role="law_abiding", is_human=False),
    }
    assert check_win_condition({"players": players}) == {"winner": "mafia"}


def test_win_condition_line_law_matches_no_mafia_remain_rule() -> None:
    """The law-abiding win-condition wording matches the 'no Mafia remain' rule."""
    line = _win_condition_line("law_abiding")
    assert "Law-abiding" in line
    assert "no Mafia remain" in line

    # Behavioural anchor: zero alive mafia is a Law-abiding win per the rule.
    players = {
        "l1": PlayerState(id="l1", name="L1", role="law_abiding", is_human=False),
        "l2": PlayerState(id="l2", name="L2", role="law_abiding", is_human=False),
    }
    assert check_win_condition({"players": players}) == {"winner": "law_abiding"}


# ==========================================================================
# Concern 3 — Template-field formatting (KeyError guard) through the real nodes
# ==========================================================================


class _FakeLargeDayAction:
    """Minimal ``get_large()`` stand-in returning a scripted ``DayAction``.

    The fake ignores prompt CONTENT entirely — so if ``_ai_day_action`` reaches
    ``.invoke`` and returns the scripted action, every ``.format`` placeholder
    in the (real) template was supplied. A ``KeyError`` from a missing kwarg
    would be raised by ``.format`` BEFORE ``.invoke`` is reached, failing the
    test loudly.
    """

    def __init__(self, action: DayAction) -> None:
        self._action = action
        self.invoke_count = 0

    def with_structured_output(self, schema: type) -> "_FakeLargeDayAction":
        return self

    def invoke(self, messages: object) -> DayAction:
        self.invoke_count += 1
        return self._action


class _FakeLargeBallot:
    """``get_large()`` stand-in returning a scripted ``Ballot`` (content-blind)."""

    def __init__(self, ballot: Ballot) -> None:
        self._ballot = ballot
        self.invoke_count = 0

    def with_structured_output(self, schema: type) -> "_FakeLargeBallot":
        return self

    def invoke(self, messages: object) -> Ballot:
        self.invoke_count += 1
        return self._ballot


@pytest.mark.parametrize("actor_id", ["p-mara", "p-cleo"], ids=["mafioso", "citizen"])
def test_ai_day_action_formats_all_fields_for_every_actor_kind(
    actor_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_ai_day_action`` renders the real template without ``KeyError`` for both kinds.

    Drives the REAL node (which computes role_label/win_condition/team_line and
    calls ``.format``) for a mafioso and a citizen. The fake returns a scripted
    speak action, so a clean return proves every new placeholder was supplied.
    """
    players = _players()
    actor = players[actor_id]
    scripted = DayAction(kind="speak", text="A measured remark.")
    fake = _FakeLargeDayAction(scripted)
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    result = _ai_day_action(actor, _state(players))

    assert result is scripted
    assert fake.invoke_count == 1


def test_ai_day_action_retry_block_also_formats_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The RETRY message block is also assembled from the rendered template.

    The first ``.invoke`` returns an unacceptable action (empty speak text), so
    ``_ai_day_action`` builds the retry block (``[*base_messages, reminder]``)
    and invokes again. Both invokes succeeding proves the base render (reused in
    the retry block) formatted cleanly — no missing kwarg on the retry path.
    """
    players = _players()
    mara = players["p-mara"]

    class _RejectThenAccept:
        def __init__(self) -> None:
            self.invoke_count = 0

        def with_structured_output(self, schema: type) -> "_RejectThenAccept":
            return self

        def invoke(self, messages: object) -> DayAction:
            self.invoke_count += 1
            if self.invoke_count == 1:
                # Unacceptable: empty speak text → triggers the retry block.
                return DayAction(kind="speak", text="   ")
            return DayAction(kind="speak", text="Second time's the charm.")

    fake = _RejectThenAccept()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    result = _ai_day_action(mara, _state(players))

    assert fake.invoke_count == 2  # first rejected, retry accepted
    assert result.kind == "speak"
    assert result.text == "Second time's the charm."


@pytest.mark.parametrize(
    "voter_id,target_id",
    [
        ("p-mara", "p-mara"),  # self → "is YOU" relationship
        ("p-mara", "p-max"),   # mafia→mafia → "fellow Mafioso" relationship
        ("p-cleo", "p-mara"),  # law-abiding voter on mafia → "" relationship
        ("p-cleo", "p-human"), # law-abiding voter on human → "" relationship
    ],
    ids=["self", "mafia_peer", "law_on_mafia", "law_on_human"],
)
def test_ai_ballot_formats_all_fields_for_every_relationship(
    voter_id: str,
    target_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_ai_ballot`` renders ``AI_VOTE_USER_TEMPLATE`` cleanly for every relationship.

    Sweeps the four ``{relationship}`` cases (empty AND non-empty) plus both
    actor kinds. A clean return proves ``voter``/``role_label``/``win_condition``/
    ``team_line``/``target``/``relationship``/``context`` were all supplied.
    """
    players = _players()
    voter = players[voter_id]
    target = players[target_id]
    scripted = Ballot(yes=False)
    fake = _FakeLargeBallot(scripted)
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    result = _ai_ballot(voter, target, _state(players))

    assert result is scripted
    assert fake.invoke_count == 1


def test_ai_vote_template_supplies_relationship_placeholder() -> None:
    """Belt-and-braces: the real template's ``{relationship}`` slot accepts ''.

    A direct ``.format`` with an empty relationship (the law-abiding-voter case)
    must not raise — guarding against a stray brace or renamed field.
    """
    rendered = AI_VOTE_USER_TEMPLATE.format(
        voter="Cleo",
        role_label="Law-abiding Citizen",
        win_condition=_win_condition_line("law_abiding"),
        team_line="",
        target="Mara",
        relationship="",
        context="(no prior discussion)",
    )
    assert "Cleo" in rendered
    assert "Mara" in rendered


# ==========================================================================
# Concern 4 — self_vote.initiation regression pin (_accept rejects self-target)
# ==========================================================================


def test_ai_day_action_rejects_self_targeted_vote_initiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_accept`` still rejects a self-targeted vote and falls back (§2.6).

    ``self_vote.initiation`` is structurally prevented: ``_ai_day_action``'s
    ``_accept`` requires ``target_id != speaker.id``. We script BOTH the first
    invoke and the retry to return a self-targeted vote; both are rejected, so
    the node lands on its deterministic speak fallback rather than emitting a
    self-vote. The prompt rework did not touch this guard.
    """
    players = _players()
    mara = players["p-mara"]

    class _AlwaysSelfVote:
        def __init__(self) -> None:
            self.invoke_count = 0

        def with_structured_output(self, schema: type) -> "_AlwaysSelfVote":
            return self

        def invoke(self, messages: object) -> DayAction:
            self.invoke_count += 1
            # Mara votes against Mara — a degenerate self-initiation.
            return DayAction(kind="vote", target_id="p-mara")

    fake = _AlwaysSelfVote()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    result = _ai_day_action(mara, _state(players))

    # Both the first call and the retry returned the self-vote; both rejected.
    assert fake.invoke_count == 2
    # Fell back to a speak (never a self-targeted vote).
    assert result.kind == "speak"
    assert result.target_id is None
    # The deterministic fallback line from the production node.
    assert result.text == "I'm not sure who to trust yet."


def test_ai_day_action_accepts_valid_other_target_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: a vote against a DIFFERENT alive player IS accepted.

    Confirms the self-rejection above is specific to self-targeting, not a
    blanket vote rejection — guards the test from a false-positive where votes
    are simply never accepted.
    """
    players = _players()
    mara = players["p-mara"]
    scripted = DayAction(kind="vote", target_id="p-cleo")
    fake = _FakeLargeDayAction(scripted)
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    result = _ai_day_action(mara, _state(players))

    assert result.kind == "vote"
    assert result.target_id == "p-cleo"
    assert fake.invoke_count == 1


# ==========================================================================
# Concern 6 — blunder_eval day-speaker resolver still couples to the template
# ==========================================================================


def test_day_speaker_regex_matches_real_day_speak_prompt() -> None:
    """``_DAY_SPEAKER_RE`` extracts the speaker NAME from a rendered day prompt.

    The resolver anchor is derived FROM ``DAY_SPEAK_USER_TEMPLATE`` (literal
    spans around ``{speaker}``). After Task 1 reworded the template (adding the
    role_label/win_condition/team_line fields right after ``{speaker}``), the
    anchor must still bound the captured name to real template prose — this is
    the regression guard for the bug Task 1 fixed.
    """
    players = _players()
    prompt = _render_day_prompt(players["p-mara"], players)

    match = _DAY_SPEAKER_RE.search(prompt)
    assert match is not None, (
        "day-speaker anchor failed to match the rendered DAY_SPEAK_USER_TEMPLATE"
    )
    assert match.group("speaker").strip() == "Mara"


def test_day_speaker_regex_does_not_match_day_speak_system() -> None:
    """The anchor must NOT spuriously match ``DAY_SPEAK_SYSTEM``.

    ``DAY_SPEAK_SYSTEM`` is the system prompt that accompanies every day-speak
    invoke; if the anchor's leading literal were too loose it could match there
    and mis-attribute the capture. Pins the anchor coupling so a future reword
    that accidentally re-introduces the "You are a player" overlap is caught.
    """
    match = _DAY_SPEAKER_RE.search(DAY_SPEAK_SYSTEM)
    assert match is None, (
        "day-speaker anchor spuriously matched DAY_SPEAK_SYSTEM — anchor "
        "coupling regressed"
    )


def test_day_speaker_resolver_attributes_each_actor_kind() -> None:
    """The full ``make_day_speaker_resolver`` maps a rendered prompt to the right id.

    End-to-end through the public resolver factory (not just the raw regex):
    for both a mafioso and a citizen, the resolver bound to this game's players
    resolves the rendered day-speak prompt to the speaking player's id, and
    returns ``None`` for the ``DAY_SPEAK_SYSTEM`` message alone.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from graphia.tools.blunder_eval import make_day_speaker_resolver

    players = _players()
    resolve = make_day_speaker_resolver(players)

    for actor_id in ("p-mara", "p-cleo"):
        actor = players[actor_id]
        messages = [
            SystemMessage(content=DAY_SPEAK_SYSTEM),
            HumanMessage(content=_render_day_prompt(actor, players)),
        ]
        assert resolve(messages) == actor_id

    # System prompt alone (no day-speak user prompt) → unattributed.
    assert resolve([SystemMessage(content=DAY_SPEAK_SYSTEM)]) is None
