"""Spec 024 (Role-Specific Day Guidance for AI Players), Slice 1 tests.

The production change appends a role-matched closing directive — the concrete
plays for the actor's own side — at the TAIL (recency position) of both AI Day
prompts (the new ``{role_guidance}`` slot in ``DAY_SPEAK_USER_TEMPLATE`` /
``AI_VOTE_USER_TEMPLATE``), so the directive reaches an AI player at BOTH its
speak turn (``_ai_day_action``) and its vote turn (``_ai_ballot``), matched to
its true secret role and never carrying the other side's text.

This file is the structural-invariant test of that change — the BEHAVIOURAL
effect (a more decisive, better-coordinated AI town) is eval-measured OUT of the
suite, per the effort-not-results acceptance principle (CR 005); nothing here
asserts decisiveness. It mirrors ``tests/test_recap_aware_reasoning.py``:

1. **Pure ``_role_guidance_block``** — role-match (Law-abiding menu vs Mafia
   menu, each carrying only its own distinctive marker) and ``enabled=False`` ⇒
   ``""`` for both roles (the ADR-011 ablation seam).
2. **Per-role guidance reaches BOTH prompts** — drive the REAL
   ``_ai_day_action`` / ``_ai_ballot`` through a content-recording ``get_large``
   fake, once for a Citizen actor and once for a Mafioso actor, asserting the
   captured HumanMessage carries that actor's side menu (the "all four cases").
3. **Never the other side's guidance / never reveal** — a Citizen prompt never
   carries a Mafia-menu marker and vice-versa; the Mafioso menu reinforces (never
   contradicts) the never-reveal rule and instructs no disclosure.
4. **Tail placement (recency)** — the guidance appears AFTER ``Recent public
   discussion:`` (the contrast to spec-019's standings, which sit before it).
5. **Flag-off parity** — with the flag OFF both prompts revert to the pre-024
   form (no label, no menu body) for both sides; the default keeps the block.
6. **``load_config()`` default-on semantics** for ``GRAPHIA_ROLE_GUIDANCE``.
7. **Template-slot guard** — each template ``.format(...)`` with ``role_guidance=``
   renders cleanly with no leftover placeholder.
8. **Threading anti-drift** — ``build_runtime_graph`` carries / forwards the flag.

Per the project's determinism posture (architecture §6) every assertion is
structural — substring presence/absence, ordering, purity — never verbatim LLM
text. No RNG is touched; the LLM boundary is the content-recording fake. Reuses
the spec-018 ``_roster`` / ``_player`` helpers.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.llm import Ballot, DayAction
from graphia.nodes.day import _ai_ballot, _ai_day_action, _role_guidance_block
from graphia.prompts import (
    AI_VOTE_USER_TEMPLATE,
    DAY_SPEAK_USER_TEMPLATE,
    ROLE_GUIDANCE_LABEL,
)
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.state import GameState

# Reuse the spec-018 hand-built-state helpers verbatim.
from test_slice_day_round_recap import _roster

# Distinctive marker phrases for each side's menu. A Law-abiding prompt must
# carry the Law-abiding marker and NEVER the Mafia marker, and vice-versa.
_LAW_MARKER = "The town wins ONLY by executing Mafiosos"
_LAW_CAUTION = "fellow Law-abiding Citizens"
_MAFIA_MARKER = "You win by deception"
_MAFIA_COVER = "Hold your public cover persona"
# The never-reveal rule the Mafioso menu must reinforce (and never contradict).
_MAFIA_NEVER_REVEAL = "NEVER reveal that you are Mafia"


# ==========================================================================
# 1. Pure ``_role_guidance_block`` — role-match + ablation seam
# ==========================================================================


def test_role_guidance_block_law_abiding_returns_only_the_law_menu() -> None:
    """A Law-abiding actor gets the Law-abiding menu and no Mafia text."""
    block = _role_guidance_block("law_abiding", enabled=True)

    assert ROLE_GUIDANCE_LABEL in block
    assert _LAW_MARKER in block
    assert _LAW_CAUTION in block
    # Never the other side's menu.
    assert _MAFIA_MARKER not in block
    assert _MAFIA_COVER not in block


def test_role_guidance_block_mafia_returns_only_the_mafia_menu() -> None:
    """A Mafioso actor gets the Mafia menu and no Law-abiding text."""
    block = _role_guidance_block("mafia", enabled=True)

    assert ROLE_GUIDANCE_LABEL in block
    assert _MAFIA_MARKER in block
    assert _MAFIA_COVER in block
    # The never-reveal rule is reinforced, not contradicted.
    assert _MAFIA_NEVER_REVEAL in block
    # Never the other side's menu.
    assert _LAW_MARKER not in block


def test_mafia_guidance_never_instructs_disclosure() -> None:
    """The Mafioso menu never instructs revealing side, teammates, or cover.

    Every reveal-shaped phrase in the Mafia menu is under an explicit negation
    (``NEVER reveal …``); there is no bare disclosure imperative. We assert the
    standing never-reveal sentence is present and that no positive
    "reveal/admit you are Mafia"-style instruction appears.
    """
    block = _role_guidance_block("mafia", enabled=True)

    assert _MAFIA_NEVER_REVEAL in block
    # No positive disclosure imperative (these would contradict the cover).
    assert "Reveal that you are Mafia" not in block
    assert "Tell the table you are Mafia" not in block
    assert "Name your teammates publicly" not in block


@pytest.mark.parametrize("role", ["law_abiding", "mafia"])
def test_role_guidance_block_disabled_returns_empty(role: str) -> None:
    """``enabled=False`` ⇒ ``""`` for either role (the ADR-011 ablation seam)."""
    assert _role_guidance_block(role, enabled=False) == ""


# ==========================================================================
# 2/3/4. Per-role guidance reaches BOTH prompts; never the other side; tail
# ==========================================================================


class _CapturingDayFake:
    """A content-recording ``get_large()`` stand-in for the real Day path.

    Returns a scripted output (a ``DayAction`` for the speak path or a ``Ballot``
    for the vote path — both share the ``with_structured_output(...).invoke(...)``
    call shape) and records every prompt it was handed, so a test can drive the
    REAL ``_ai_day_action`` / ``_ai_ballot`` and inspect the actual rendered
    prompt the model would have received. Mirrors the same helper in
    ``tests/test_recap_aware_reasoning.py``.
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
    ``[SystemMessage(...SYSTEM), HumanMessage(...USER_TEMPLATE)]``; the
    role-guidance block lives in the HumanMessage (index 1).
    """
    human = messages[1]
    assert isinstance(human, HumanMessage)
    return human.content


def _mixed_state() -> GameState:
    """A hand-built Day state with both a living Citizen and a living Mafioso."""
    return {
        "cycle": 1,
        "players": _roster(law_alive=3, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
    }


def _living(state: GameState, role: str) -> Any:
    return next(
        p
        for p in state["players"].values()
        if p.is_alive and not p.is_human and p.role == role
    )


def test_law_abiding_guidance_reaches_speak_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Citizen's speak prompt carries the Law menu and NOT the Mafia menu."""
    state = _mixed_state()
    speaker = _living(state, "law_abiding")
    fake = _CapturingDayFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(speaker, state)

    prompt = _human_prompt(fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL in prompt
    assert _LAW_MARKER in prompt
    assert _LAW_CAUTION in prompt
    assert _MAFIA_MARKER not in prompt
    assert _MAFIA_COVER not in prompt
    # Tail placement (recency): the guidance follows the scrolling discussion.
    assert prompt.index(ROLE_GUIDANCE_LABEL) > prompt.index(
        "Recent public discussion:"
    )


def test_mafia_guidance_reaches_speak_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mafioso's speak prompt carries the Mafia menu and NOT the Law menu."""
    state = _mixed_state()
    speaker = _living(state, "mafia")
    fake = _CapturingDayFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(speaker, state)

    prompt = _human_prompt(fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL in prompt
    assert _MAFIA_MARKER in prompt
    assert _MAFIA_NEVER_REVEAL in prompt
    assert _LAW_MARKER not in prompt
    assert prompt.index(ROLE_GUIDANCE_LABEL) > prompt.index(
        "Recent public discussion:"
    )


def test_law_abiding_guidance_reaches_vote_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Citizen's vote prompt carries the Law menu and NOT the Mafia menu."""
    state = _mixed_state()
    voter = _living(state, "law_abiding")
    target = next(
        p for p in state["players"].values() if p.is_alive and p.id != voter.id
    )
    fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(voter, target, state)

    prompt = _human_prompt(fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL in prompt
    assert _LAW_MARKER in prompt
    assert _MAFIA_MARKER not in prompt
    assert prompt.index(ROLE_GUIDANCE_LABEL) > prompt.index(
        "Recent public discussion:"
    )


def test_mafia_guidance_reaches_vote_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mafioso's vote prompt carries the Mafia menu and NOT the Law menu."""
    state = _mixed_state()
    voter = _living(state, "mafia")
    target = next(
        p for p in state["players"].values() if p.is_alive and p.id != voter.id
    )
    fake = _CapturingDayFake(Ballot(yes=True))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(voter, target, state)

    prompt = _human_prompt(fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL in prompt
    assert _MAFIA_MARKER in prompt
    assert _MAFIA_NEVER_REVEAL in prompt
    assert _LAW_MARKER not in prompt
    assert prompt.index(ROLE_GUIDANCE_LABEL) > prompt.index(
        "Recent public discussion:"
    )


# ==========================================================================
# 5. Flag-off parity — GRAPHIA_ROLE_GUIDANCE (ADR 011 ablation)
# ==========================================================================
#
# With the flag OFF the AI Day-speech and vote prompts revert to their PRE-024
# form (no label, no menu body); with the flag ON (the default) the block is
# present. Both directions are driven through the REAL nodes.


def test_flag_off_removes_guidance_from_speak_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the Day-speak prompt carries NO label and NO menu body."""
    state = _mixed_state()
    speaker = _living(state, "law_abiding")
    fake = _CapturingDayFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(speaker, state, role_guidance_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL not in prompt
    assert _LAW_MARKER not in prompt


def test_flag_off_removes_guidance_from_vote_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the AI vote prompt carries NO label and NO menu body."""
    state = _mixed_state()
    voter = _living(state, "mafia")
    target = next(
        p for p in state["players"].values() if p.is_alive and p.id != voter.id
    )
    fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(voter, target, state, role_guidance_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL not in prompt
    assert _MAFIA_MARKER not in prompt


def test_flag_on_default_keeps_guidance_in_both_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON (the implicit default) ⇒ BOTH prompts carry the block.

    The contrast partner to the two flag-off tests: the same call sites WITHOUT
    passing the flag (so the ``True`` default applies) inject the label + menu,
    proving the off-result above is attributable to the flag.
    """
    state = _mixed_state()
    speaker = _living(state, "law_abiding")
    target = next(
        p for p in state["players"].values() if p.is_alive and p.id != speaker.id
    )

    speak_fake = _CapturingDayFake(DayAction(kind="speak", text="A remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: speak_fake)
    _ai_day_action(speaker, state)  # default role_guidance_enabled=True
    speak_prompt = _human_prompt(speak_fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL in speak_prompt
    assert _LAW_MARKER in speak_prompt

    ballot_fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: ballot_fake)
    _ai_ballot(speaker, target, state)  # default flag on
    ballot_prompt = _human_prompt(ballot_fake.messages_log[0])
    assert ROLE_GUIDANCE_LABEL in ballot_prompt
    assert _LAW_MARKER in ballot_prompt


# ==========================================================================
# 6. ``load_config()`` default-on semantics for GRAPHIA_ROLE_GUIDANCE
# ==========================================================================
#
# Mirrors the spec-019 ``GRAPHIA_RECAP_AWARE_REASONING`` config unit: unset/blank
# ⇒ on (the documented default), truthy ⇒ on, explicit falsy ⇒ off.


def test_load_config_role_guidance_default_on_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ⇒ role guidance on (the documented default)."""
    monkeypatch.delenv("GRAPHIA_ROLE_GUIDANCE", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().role_guidance_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_load_config_role_guidance_blank_is_default_on(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank/whitespace value is treated as unset ⇒ on (``_env_flag``)."""
    monkeypatch.setenv("GRAPHIA_ROLE_GUIDANCE", blank)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().role_guidance_enabled is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "On"])
def test_load_config_role_guidance_truthy_value_enables(
    truthy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any truthy value keeps role guidance on."""
    monkeypatch.setenv("GRAPHIA_ROLE_GUIDANCE", truthy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().role_guidance_enabled is True


@pytest.mark.parametrize(
    "falsy", ["0", "false", "FALSE", "no", "off", "Off", "anything-else"]
)
def test_load_config_role_guidance_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit falsy value (or any non-truthy token) disables the flag."""
    monkeypatch.setenv("GRAPHIA_ROLE_GUIDANCE", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().role_guidance_enabled is False


# ==========================================================================
# 7. Template-slot guard
# ==========================================================================


def test_day_speak_template_slot_guard_renders_with_role_guidance() -> None:
    """``DAY_SPEAK_USER_TEMPLATE.format(...)`` with ``role_guidance=`` is clean."""
    guidance = _role_guidance_block("law_abiding", enabled=True)
    rendered = DAY_SPEAK_USER_TEMPLATE.format(
        speaker="Cleo",
        role_label="Law-abiding Citizen",
        win_condition="Your side, the Law-abiding Citizens, wins when no Mafia remain.",
        team_line="",
        persona="",
        standings="",
        roster="Cleo: p-cleo",
        context="(no prior discussion)",
        role_guidance=guidance,
    )

    assert "{role_guidance}" not in rendered
    assert ROLE_GUIDANCE_LABEL in rendered
    assert _LAW_MARKER in rendered
    assert "Cleo" in rendered


def test_ai_vote_template_slot_guard_renders_with_role_guidance() -> None:
    """``AI_VOTE_USER_TEMPLATE.format(...)`` with ``role_guidance=`` is clean."""
    guidance = _role_guidance_block("mafia", enabled=True)
    rendered = AI_VOTE_USER_TEMPLATE.format(
        voter="Cleo",
        role_label="Mafia",
        win_condition="Your side, the Mafia, wins when the Mafia count is greater "
        "than or equal to the Law-abiding count.",
        team_line="",
        standings="",
        target="Mara",
        relationship="",
        context="(no prior discussion)",
        role_guidance=guidance,
    )

    assert "{role_guidance}" not in rendered
    assert ROLE_GUIDANCE_LABEL in rendered
    assert _MAFIA_MARKER in rendered
    assert "Cleo" in rendered
    assert "Mara" in rendered


def test_template_slot_guard_empty_guidance_leaves_pre024_form() -> None:
    """An empty ``role_guidance=""`` renders with no label or menu body.

    The flag-off rendered form: the slot collapses and neither the label nor any
    menu marker survives.
    """
    rendered = DAY_SPEAK_USER_TEMPLATE.format(
        speaker="Cleo",
        role_label="Law-abiding Citizen",
        win_condition="Your side, the Law-abiding Citizens, wins when no Mafia remain.",
        team_line="",
        persona="",
        standings="",
        roster="Cleo: p-cleo",
        context="(no prior discussion)",
        role_guidance="",
    )

    assert "{role_guidance}" not in rendered
    assert ROLE_GUIDANCE_LABEL not in rendered
    assert _LAW_MARKER not in rendered


# ==========================================================================
# 8. Threading anti-drift — build_runtime_graph carries/forwards the flag
# ==========================================================================
#
# The named anti-drift requirement: both graph builders must thread the flag or
# local and remote diverge. Mirrors the spec-019 anti-drift coverage — assert
# the Runtime builder's signature carries ``role_guidance_enabled`` and that a
# graph compiles with the flag toggled (no AWS / no LLM is reached at compile).


def test_build_runtime_graph_signature_carries_role_guidance_flag() -> None:
    """``build_runtime_graph`` exposes a ``role_guidance_enabled`` parameter."""
    sig = inspect.signature(build_runtime_graph)
    assert "role_guidance_enabled" in sig.parameters
    # Defaulted to True (matching the config default) so callers compiling the
    # graph directly need not supply it.
    assert sig.parameters["role_guidance_enabled"].default is True


def test_build_runtime_graph_compiles_with_flag_off(tmp_path) -> None:
    """The Runtime builder compiles a graph with the role-guidance flag off.

    A lightweight anti-drift smoke: compiling does not reach AWS/LLM, so this
    proves the flag is an accepted, forwarded kwarg in the Runtime path too.
    """
    graph = build_runtime_graph(
        "thread-roleguid-off",
        tmp_path / "checkpoints",
        role_guidance_enabled=False,
    )
    assert graph is not None
    # Sanity: the same pure builder honours the off flag regardless of role.
    assert _role_guidance_block("law_abiding", enabled=False) == ""
    assert _role_guidance_block("mafia", enabled=False) == ""
