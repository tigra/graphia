"""Spec 019 (Recap-Aware AI Reasoning), Slice 1 tests.

The production change factors a pure ``_render_standings(state) -> str`` out of
``render_day_round_recap`` and injects it as a dedicated, front-and-center block
into each AI player's Day-speech and vote prompts (the new ``{standings}`` slot
in ``DAY_SPEAK_USER_TEMPLATE`` / ``AI_VOTE_USER_TEMPLATE``), so the current
standings reach an AI player at BOTH its speak turn (``_ai_day_action``) and its
vote turn (``_ai_ballot``).

This file is the structural-invariant test of that change (the behavioural
effect — a more decisive AI town — is eval-measured OUT of the suite, per the
effort-not-results acceptance principle; nothing here asserts decisiveness):

1. **Pure ``_render_standings`` over hand-built state** — living counts by side
   (singular/plural, space-pinned word boundary); votes-called-today sweep
   (0/1/N); executed-today (named + revealed side; the no-execution variant; a
   stale prior-cycle execution excluded); **no clock tokens** (the 019↔020
   boundary); **no ``"Day"`` / ``"status:"`` framing prefix** in the body; and
   purity (state, kill_log, day_votes_initiated, each ``is_alive`` unchanged).
2. **Non-leak invariant** — over a roster with BOTH a living Mafioso and a
   living Citizen, the standings carry only aggregate counts; no living player's
   name co-occurs with a side label (the only named-with-side disclosure is an
   executed, dead player).
3. **Prompt-injection capture** — drive the REAL ``_ai_day_action`` and
   ``_ai_ballot`` through a content-recording ``get_large()`` fake (patched at
   ``graphia.nodes.day.get_large`` after the autouse ``safe_llm``) and assert the
   captured HumanMessage contains ``_render_standings(state)`` — proving the
   standings reach BOTH prompts — plus a template-slot guard that each template
   ``.format(...)``s cleanly with ``standings=`` supplied.

Per the project's determinism posture (architecture §6) every assertion is
structural — substring presence, counts, purity — never verbatim LLM text. No
RNG is touched (``_render_standings`` is pure); the LLM boundary is the
content-recording fake. Reuses the spec-018 ``_roster`` / ``_player`` helpers.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.llm import Ballot, DayAction
from graphia.nodes.day import _ai_ballot, _ai_day_action, _render_standings
from graphia.prompts import AI_VOTE_USER_TEMPLATE, DAY_SPEAK_USER_TEMPLATE
from graphia.state import GameState, KillRecord

# The label that heads the injected standings block in both AI prompts. Its
# presence/absence is the structural marker the spec-019 ablation flag toggles:
# ON ⇒ the labelled block is injected; OFF ⇒ the block (label + body) is gone.
_STANDINGS_LABEL = "Current standings (act on these):"

# Reuse the spec-018 hand-built-state helpers verbatim — the standings body
# ``_render_standings`` returns is the same text the spec-018 recap composer
# wraps, so the same roster/player builders apply.
from test_slice_day_round_recap import _roster

# Clock tokens that MUST be absent from the standings body (the 019↔020
# boundary — the in-world clock belongs to the recap composer in spec 020,
# never to the prompt-fed standings).
_CLOCK_TOKENS = ("AM", "PM", "midnight")


# ==========================================================================
# 1. Pure ``_render_standings`` over hand-built state
# ==========================================================================


def test_render_standings_no_execution_states_counts_votes_and_no_exec() -> None:
    """No execution this cycle: both side counts, votes clause, no-exec clause."""
    state: GameState = {
        "cycle": 2,
        "players": _roster(law_alive=4, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    text = _render_standings(state)

    assert "4 Law-abiding Citizens" in text
    assert "2 Mafiosos" in text
    assert "No execution votes called yet today." in text
    assert "No one has been executed today." in text


def test_render_standings_with_execution_names_player_and_revealed_side() -> None:
    """An execution this cycle names the executed player and the revealed side."""
    players = _roster(law_alive=4, mafia_alive=1, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 2,
        "name": "Mobster5",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": [executed],
    }
    text = _render_standings(state)

    assert "Mobster5 was executed today" in text
    assert "revealed to be Mafia" in text
    # The "no one executed" clause must NOT appear on the execution path.
    assert "No one has been executed today." not in text


def test_render_standings_executed_law_abiding_reveals_law_abiding_side() -> None:
    """An executed Law-abiding Citizen is revealed as a Law-abiding Citizen."""
    players = _roster(law_alive=3, mafia_alive=2, law_dead=1)
    executed: KillRecord = {
        "cycle": 1,
        "name": "Citizen3",
        "cause": "execution",
        "role": "law_abiding",
    }
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 2,
        "kill_log": [executed],
    }
    text = _render_standings(state)

    assert "Citizen3 was executed today" in text
    assert "revealed to be Law-abiding Citizen" in text


def test_render_standings_only_counts_execution_for_the_current_cycle() -> None:
    """A stale prior-cycle execution must NOT surface in this cycle's standings."""
    players = _roster(law_alive=4, mafia_alive=2, mafia_dead=1)
    stale: KillRecord = {
        "cycle": 1,
        "name": "Mobster6",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 0,
        "kill_log": [stale],
    }
    text = _render_standings(state)

    assert "No one has been executed today." in text
    assert "Mobster6" not in text


@pytest.mark.parametrize(
    ("law_alive", "mafia_alive", "expected_law", "expected_mafia"),
    [
        (1, 1, "1 Law-abiding Citizen ", "1 Mafioso "),
        (1, 2, "1 Law-abiding Citizen ", "2 Mafiosos "),
        (4, 1, "4 Law-abiding Citizens ", "1 Mafioso "),
        (5, 2, "5 Law-abiding Citizens ", "2 Mafiosos "),
    ],
)
def test_render_standings_role_count_singular_vs_plural(
    law_alive: int,
    mafia_alive: int,
    expected_law: str,
    expected_mafia: str,
) -> None:
    """Both side counts pluralize correctly (1 -> singular, N -> plural).

    The trailing space pins the word boundary so ``"1 Mafioso "`` never matches
    inside ``"1 Mafiosos"`` and ``"1 Law-abiding Citizen "`` never matches inside
    the plural — the standings body reads ``"{law} and {mafia} remain."``.
    """
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=law_alive, mafia_alive=mafia_alive),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    text = _render_standings(state)
    assert f"{expected_law}and {expected_mafia}remain." in text


@pytest.mark.parametrize(
    ("votes", "expected_clause"),
    [
        (0, "No execution votes called yet today."),
        (1, "1 execution vote called today."),
        (3, "3 execution votes called today."),
    ],
)
def test_render_standings_votes_clause_singular_vs_plural(
    votes: int, expected_clause: str
) -> None:
    """The votes-called-today clause uses 0/1/N phrasing for ``day_votes_initiated``."""
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": votes,
        "kill_log": [],
    }
    assert expected_clause in _render_standings(state)


@pytest.mark.parametrize(
    ("law_alive", "mafia_alive", "votes", "kill_log"),
    [
        (4, 2, 0, []),
        (3, 1, 3, []),
        (
            2,
            1,
            1,
            [
                {
                    "cycle": 1,
                    "name": "Mobster4",
                    "cause": "execution",
                    "role": "mafia",
                }
            ],
        ),
    ],
)
def test_render_standings_carries_no_clock_tokens(
    law_alive: int,
    mafia_alive: int,
    votes: int,
    kill_log: list[KillRecord],
) -> None:
    """No clock tokens in the standings body (the 019↔020 boundary).

    The in-world clock is spec 020's, recap-only — it must NEVER appear in the
    prompt-fed standings. Sweeps the no-exec, vote-laden, and executed variants
    so a clock leaking into any clause is caught.
    """
    # ``kill_log`` for the executed variant is keyed on cycle 1 below.
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=law_alive, mafia_alive=mafia_alive),
        "day_votes_initiated": votes,
        "kill_log": kill_log,
    }
    text = _render_standings(state)
    for token in _CLOCK_TOKENS:
        assert token not in text, (
            f"clock token {token!r} leaked into the standings body — that "
            f"belongs to spec 020's recap, not the prompt-fed standings:\n{text!r}"
        )


def test_render_standings_carries_no_day_status_framing_prefix() -> None:
    """No ``"Day"`` / ``"status:"`` framing in the body (that stays in the recap).

    ``_render_standings`` returns the decision-facts BODY only; the
    ``"Day N status:"`` prefix is owned by ``render_day_round_recap``. Sweeping a
    cycle != 1 board makes a leaked ``"Day 3"`` prefix unambiguous.
    """
    state: GameState = {
        "cycle": 3,
        "players": _roster(law_alive=4, mafia_alive=2),
        "day_votes_initiated": 1,
        "kill_log": [],
    }
    text = _render_standings(state)

    assert "status:" not in text
    assert "Day" not in text
    # And it does not begin with the recap composer's day-number prefix.
    assert not text.startswith("Day 3")


def test_render_standings_does_not_mutate_input_state() -> None:
    """The helper is pure — state, kill_log, votes, and ``is_alive`` are unchanged."""
    players = _roster(law_alive=4, mafia_alive=2, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 2,
        "name": "Mobster6",
        "cause": "execution",
        "role": "mafia",
    }
    kill_log: list[KillRecord] = [executed]
    state: GameState = {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": kill_log,
    }
    before_keys = set(state.keys())
    before_players = dict(players)
    before_alive = {pid: p.is_alive for pid, p in players.items()}

    _render_standings(state)

    assert set(state.keys()) == before_keys
    assert state["kill_log"] == [executed]
    assert state["day_votes_initiated"] == 1
    assert dict(state["players"]) == before_players
    assert {pid: p.is_alive for pid, p in players.items()} == before_alive


# ==========================================================================
# 2. Non-leak invariant — only aggregate counts, no living player's side
# ==========================================================================


def test_render_standings_does_not_attribute_a_living_players_side() -> None:
    """No living player's name co-occurs with a side label in the standings.

    Over a roster with BOTH a living Mafioso and a living Citizen, the standings
    must carry only aggregate counts — never a living player's secret side. The
    ONLY named-with-side disclosure allowed is an executed (dead) player, so we
    assert no LIVING player's name appears anywhere in the body.
    """
    players = _roster(law_alive=2, mafia_alive=1)
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    text = _render_standings(state)

    living = [p for p in players.values() if p.is_alive]
    # Sanity: the scenario really does contain both a living Mafioso and Citizen.
    assert any(p.role == "mafia" for p in living)
    assert any(p.role == "law_abiding" for p in living)

    for p in living:
        assert p.name not in text, (
            f"living player {p.name!r} (a {p.role}) was named in the standings "
            f"— only aggregate counts may name a side:\n{text!r}"
        )
    # Aggregate counts ARE present (the legitimate side disclosure).
    assert "Mafioso" in text
    assert "Law-abiding Citizen" in text


def test_render_standings_names_only_the_executed_dead_player() -> None:
    """The sole named-with-side disclosure is the executed (dead) player.

    With a living Mafioso, a living Citizen, AND an executed (dead) Mafioso this
    cycle, only the dead player's name appears attributed to a side — the living
    players are still aggregate-only.
    """
    players = _roster(law_alive=2, mafia_alive=1, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 1,
        "name": "Mobster3",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": [executed],
    }
    text = _render_standings(state)

    # The executed (dead) player IS named with its revealed side — allowed.
    assert "Mobster3 was executed today" in text
    assert "revealed to be Mafia" in text

    # No LIVING player's name appears.
    for p in players.values():
        if not p.is_alive:
            continue
        assert p.name not in text, (
            f"living player {p.name!r} leaked into the standings alongside the "
            f"executed disclosure:\n{text!r}"
        )


# ==========================================================================
# 3. Prompt-injection capture — standings reach BOTH the speak and vote prompts
# ==========================================================================


class _CapturingDayFake:
    """A content-recording ``get_large()`` stand-in for the real Day path.

    Returns a scripted output (a ``DayAction`` for the speak path or a ``Ballot``
    for the vote path — both share the ``with_structured_output(...).invoke(...)``
    call shape) and records every prompt it was handed, so a test can drive the
    REAL ``_ai_day_action`` / ``_ai_ballot`` and inspect the actual rendered
    prompt the model would have received — proving the ``{standings}`` slot is
    wired from ``_render_standings(state)`` end-to-end, not just in isolation.

    Mirrors the ``_CapturingDayFake`` pattern in ``tests/test_personas.py``;
    generalised to serve either schema since both Day-turn call sites build the
    same ``[SystemMessage, HumanMessage]`` prompt shape.
    """

    def __init__(self, output: Any) -> None:
        self._output = output
        self.messages_log: list[Any] = []

    def with_structured_output(self, schema: type) -> "_CapturingDayFake":
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        return self._output


def _human_prompt(messages: Any) -> str:
    """Return the rendered HumanMessage text from a captured prompt.

    Both ``_ai_day_action`` and ``_ai_ballot`` build
    ``[SystemMessage(...SYSTEM), HumanMessage(...USER_TEMPLATE)]``; the standings
    block lives in the HumanMessage (index 1).
    """
    human = messages[1]
    assert isinstance(human, HumanMessage)
    return human.content


def _speak_state() -> GameState:
    """A hand-built Day state with a clearly non-trivial standings body.

    A mixed living roster (so the standings carry aggregate counts) plus an
    executed player this cycle (so the executed-today clause is non-default) —
    making the injected ``_render_standings`` string distinctive enough that its
    presence in the prompt is unambiguous.
    """
    players = _roster(law_alive=3, mafia_alive=2, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 2,
        "name": "Mobster5",
        "cause": "execution",
        "role": "mafia",
    }
    return {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": [executed],
        "messages": [],
    }


def test_standings_reach_the_day_speak_prompt_via_real_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standings block reaches the prompt ``_ai_day_action`` actually builds.

    Drives the REAL node through a content-recording fake (patched at
    ``graphia.nodes.day.get_large`` AFTER the autouse ``safe_llm``), then asserts
    the captured HumanMessage CONTAINS the exact ``_render_standings(state)``
    string — the template-wiring proof that the standings reach the speak prompt.
    """
    state = _speak_state()
    speaker = next(
        p for p in state["players"].values() if p.is_alive and not p.is_human
    )
    fake = _CapturingDayFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    action = _ai_day_action(speaker, state)

    assert action.kind == "speak"
    expected = _render_standings(state)
    prompt = _human_prompt(fake.messages_log[0])
    assert expected in prompt, (
        "the standings block did not reach the Day-speak prompt"
    )
    # Front-and-center: the standings appear before the scrolling discussion.
    assert prompt.index(expected) < prompt.index("Recent public discussion:")


def test_standings_reach_the_ai_ballot_prompt_via_real_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standings block reaches the prompt ``_ai_ballot`` actually builds.

    The higher-value injection (today the vote prompt would otherwise carry NO
    standings). Drives the REAL ``_ai_ballot`` through the same content-recording
    fake and asserts the captured HumanMessage CONTAINS ``_render_standings``.
    """
    state = _speak_state()
    living = [p for p in state["players"].values() if p.is_alive and not p.is_human]
    voter = living[0]
    target = next(p for p in living if p.id != voter.id)
    fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    ballot = _ai_ballot(voter, target, state)

    assert isinstance(ballot, Ballot)
    expected = _render_standings(state)
    prompt = _human_prompt(fake.messages_log[0])
    assert expected in prompt, (
        "the standings block did not reach the AI vote prompt"
    )
    # Front-and-center: the standings appear before the scrolling discussion.
    assert prompt.index(expected) < prompt.index("Recent public discussion:")


def test_day_speak_template_slot_guard_renders_with_standings() -> None:
    """``DAY_SPEAK_USER_TEMPLATE.format(...)`` with ``standings=`` renders cleanly.

    A template-slot guard: supplying every required kwarg (including the new
    ``standings=``) renders without ``KeyError`` and includes the standings text,
    with no leftover ``{standings}`` placeholder.
    """
    standings = (
        "3 Law-abiding Citizens and 2 Mafiosos remain. "
        "1 execution vote called today. No one has been executed today."
    )
    rendered = DAY_SPEAK_USER_TEMPLATE.format(
        speaker="Cleo",
        role_label="Law-abiding Citizen",
        win_condition="Your side, the Law-abiding Citizens, wins when no Mafia remain.",
        team_line="",
        persona="",
        standings=standings,
        roster="Cleo: p-cleo",
        context="(no prior discussion)",
    )

    assert "{standings}" not in rendered
    assert standings in rendered
    assert "Cleo" in rendered


def test_ai_vote_template_slot_guard_renders_with_standings() -> None:
    """``AI_VOTE_USER_TEMPLATE.format(...)`` with ``standings=`` renders cleanly."""
    standings = (
        "2 Law-abiding Citizens and 1 Mafioso remain. "
        "No execution votes called yet today. No one has been executed today."
    )
    rendered = AI_VOTE_USER_TEMPLATE.format(
        voter="Cleo",
        role_label="Law-abiding Citizen",
        win_condition="Your side, the Law-abiding Citizens, wins when no Mafia remain.",
        team_line="",
        standings=standings,
        target="Mara",
        relationship="",
        context="(no prior discussion)",
    )

    assert "{standings}" not in rendered
    assert standings in rendered
    assert "Cleo" in rendered
    assert "Mara" in rendered


# ==========================================================================
# 4. Ablation off-switch — GRAPHIA_RECAP_AWARE_REASONING (ADR 011 retrofit)
# ==========================================================================
#
# The ADR-011-required flag-off parity test. With the flag OFF the AI
# Day-speech and vote prompts must revert to their PRE-019 form — no standings
# label and no standings body — while ON (the default) keeps today's behaviour.
# Both directions are driven through the REAL ``_ai_day_action`` /
# ``_ai_ballot`` via the same content-recording ``_CapturingDayFake`` the ON
# tests above use, so the assertion is on the actual rendered prompt.


def test_flag_off_removes_standings_block_from_day_speak_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the Day-speak prompt carries NO standings label and NO body."""
    state = _speak_state()
    speaker = next(
        p for p in state["players"].values() if p.is_alive and not p.is_human
    )
    fake = _CapturingDayFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(speaker, state, recap_aware_reasoning_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert _STANDINGS_LABEL not in prompt, (
        "the standings label leaked into the Day-speak prompt with the flag off"
    )
    assert _render_standings(state) not in prompt, (
        "the standings body leaked into the Day-speak prompt with the flag off"
    )


def test_flag_off_removes_standings_block_from_ai_ballot_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the AI vote prompt carries NO standings label and NO body."""
    state = _speak_state()
    living = [
        p for p in state["players"].values() if p.is_alive and not p.is_human
    ]
    voter = living[0]
    target = next(p for p in living if p.id != voter.id)
    fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(voter, target, state, recap_aware_reasoning_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert _STANDINGS_LABEL not in prompt, (
        "the standings label leaked into the AI vote prompt with the flag off"
    )
    assert _render_standings(state) not in prompt, (
        "the standings body leaked into the AI vote prompt with the flag off"
    )


def test_flag_on_default_keeps_standings_block_in_both_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON (the implicit default) ⇒ BOTH prompts carry the labelled block.

    The contrast partner to the two flag-off tests: the same call sites WITHOUT
    passing the flag (so the ``True`` default applies) inject the standings
    label and body, proving the off-result above is attributable to the flag.
    """
    state = _speak_state()
    living = [
        p for p in state["players"].values() if p.is_alive and not p.is_human
    ]
    speaker = living[0]
    target = next(p for p in living if p.id != speaker.id)
    expected_body = _render_standings(state)

    speak_fake = _CapturingDayFake(DayAction(kind="speak", text="A remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: speak_fake)
    _ai_day_action(speaker, state)  # default recap_aware_reasoning_enabled=True
    speak_prompt = _human_prompt(speak_fake.messages_log[0])
    assert _STANDINGS_LABEL in speak_prompt
    assert expected_body in speak_prompt

    ballot_fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: ballot_fake)
    _ai_ballot(speaker, target, state)  # default flag on
    ballot_prompt = _human_prompt(ballot_fake.messages_log[0])
    assert _STANDINGS_LABEL in ballot_prompt
    assert expected_body in ballot_prompt


# ==========================================================================
# 5. ``load_config()`` default-on semantics for GRAPHIA_RECAP_AWARE_REASONING
# ==========================================================================
#
# Mirrors the spec-018 ``GRAPHIA_DAY_ROUND_RECAP`` config unit: unset/blank
# ⇒ on (the documented default), truthy ⇒ on, explicit falsy ⇒ off.


def test_load_config_recap_aware_default_on_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ⇒ recap-aware reasoning on (the documented default)."""
    monkeypatch.delenv("GRAPHIA_RECAP_AWARE_REASONING", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().recap_aware_reasoning_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_load_config_recap_aware_blank_is_default_on(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank/whitespace value is treated as unset ⇒ on (``_env_flag``)."""
    monkeypatch.setenv("GRAPHIA_RECAP_AWARE_REASONING", blank)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().recap_aware_reasoning_enabled is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "On"])
def test_load_config_recap_aware_truthy_value_enables(
    truthy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any truthy value keeps recap-aware reasoning on."""
    monkeypatch.setenv("GRAPHIA_RECAP_AWARE_REASONING", truthy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().recap_aware_reasoning_enabled is True


@pytest.mark.parametrize(
    "falsy", ["0", "false", "FALSE", "no", "off", "Off", "anything-else"]
)
def test_load_config_recap_aware_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit falsy value (or any non-truthy token) disables the flag.

    ``_env_flag`` returns truthy-set membership for a non-blank value, so every
    value not in ``{1,true,yes,on}`` reads as off — the off-switch is the
    documented ``0``/``false``/``no``/``off`` family, asserted here.
    """
    monkeypatch.setenv("GRAPHIA_RECAP_AWARE_REASONING", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().recap_aware_reasoning_enabled is False
