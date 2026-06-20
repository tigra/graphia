"""Spec 008 chokepoint test: a full Day round fits inside the speaker context.

``_render_context`` (``src/graphia/nodes/day.py``) is the single function that
builds the "recent discussion" shown to a speaker for both the AI day-speak
prompt and the AI vote prompt. It renders ``messages[-_CONTEXT_WINDOW:]``, one
line per message as ``f"{speaker}: {content}"`` where ``speaker`` is the
message's ``name`` (a public ``SystemMessage`` — the Moderator's voice — is
labelled ``Moderator`` to match the UI).

``_render_context`` now takes a ``speaker_id`` so it can drop whispers
addressed to *other* players; these tests use all-public message lists, so any
non-empty speaker id renders them identically. Privacy/label behaviour proper
is covered in ``tests/test_day_context_privacy.py``.

These are pure tests over a hand-built message list: no graph, no LLM, no AWS.
They lock in same-round message visibility (functional spec §2.1 "Same-Round
Message Visibility") so a future shrink of ``_CONTEXT_WINDOW`` that would drop a
round's earliest speaker fails loudly.

Spec 025 (Fuller Multi-Day Discussion Window) extends this file: the live window
is now ``GraphiaConfig.context_window`` (default ~150 ≈ 3+ days), threaded into
``_render_context`` via the ``window`` keyword; ``_CONTEXT_WINDOW`` (30) stays as
the documented ablation baseline that the original guards above still pin. The
spec-025 additions cover: the window parameter (default + override), the
ablation-parity invariant (window=30 reproduces the pre-025 rendered context
byte-identically), a fuller window spanning ≥3 days, the R3 role-survival
invariant (the actor's role/grounding is never trimmed no matter how long the
history), the defensive token-budget cap (trims the OLDEST), the determinism of
the token estimate, and the ``build_runtime_graph`` anti-drift threading.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import graphia.nodes.day as day_nodes
from graphia.config import _DEFAULT_CONTEXT_WINDOW, _MAX_TABLE_SIZE
from graphia.llm import Ballot, DayAction
from graphia.nodes.day import (
    _CHARS_PER_TOKEN,
    _CONTEXT_WINDOW,
    _ai_ballot,
    _ai_day_action,
    _estimate_tokens,
    _render_context,
)
from graphia.state import GameState, PlayerPersona, PlayerState

# Standard lineup: 7 players. A full round is up to 7 speeches plus the
# day-open announcement.
PLAYER_COUNT = 7
FULL_ROUND_MESSAGES = PLAYER_COUNT + 1  # 7 speeches + 1 day-open announcement

# Any non-empty speaker id renders an all-public message list identically;
# these tests carry no whispers, so the value is irrelevant to the assertions.
SPEAKER_ID = "speaker-1"


def _build_full_round() -> list:
    """One day-open ``SystemMessage`` plus seven named ``AIMessage`` speeches."""
    messages: list = [SystemMessage(content="The Day begins. Discuss.")]
    for i in range(PLAYER_COUNT):
        messages.append(
            AIMessage(content=f"speech from player {i}", name=f"P{i}")
        )
    return messages


def test_full_round_every_speaker_line_appears() -> None:
    """Every speaker in a full round — including the earliest — is rendered."""
    messages = _build_full_round()

    rendered = _render_context(messages, SPEAKER_ID)

    # The day-open announcement and all seven speeches must survive the window.
    # The Moderator's public SystemMessage is labelled "Moderator" (matching
    # the UI), not "SystemMessage".
    assert "Moderator: The Day begins. Discuss." in rendered
    for i in range(PLAYER_COUNT):
        assert f"P{i}: speech from player {i}" in rendered
    # The earliest AIMessage specifically must not be trimmed.
    assert "P0: speech from player 0" in rendered


def test_context_window_holds_a_full_round() -> None:
    """Guard: the window must fit a full round for the 7-player lineup.

    A full round = up to 7 speeches + the day-open announcement => at least 8.
    ``_CONTEXT_WINDOW`` is currently 30, which gives comfortable headroom; this
    assertion makes a future shrink below a round's worth fail loudly.
    """
    assert _CONTEXT_WINDOW >= FULL_ROUND_MESSAGES  # i.e. >= 8


def test_context_window_holds_a_full_round_at_max_lineup() -> None:
    """Guard: the window fits a full round at the largest configurable lineup.

    Spec 014 lets the table grow to ``_MAX_TABLE_SIZE`` players. A full round
    at that ceiling is ``_MAX_TABLE_SIZE`` speeches + the day-open
    announcement, so the window must hold at least ``_MAX_TABLE_SIZE + 1``
    messages for the earliest speaker at the biggest table to survive the
    trim. Importing the real constants makes a future shrink of either
    ``_CONTEXT_WINDOW`` *or* a raise of ``_MAX_TABLE_SIZE`` past the window
    fail loudly here.
    """
    assert _CONTEXT_WINDOW >= _MAX_TABLE_SIZE + 1


def test_earlier_speaker_visible_to_later_speaker() -> None:
    """An earlier speaker's line is still present when a later one is up.

    Sequence: P0..P5 have spoken; P6 is conceptually about to speak. Since
    ``_render_context`` takes the last N messages, P0's content must remain in
    the context that P6 would be shown.
    """
    messages: list = [SystemMessage(content="The Day begins. Discuss.")]
    for i in range(6):  # P0 spoke first, through P5
        messages.append(
            AIMessage(content=f"speech from player {i}", name=f"P{i}")
        )

    rendered = _render_context(messages, SPEAKER_ID)

    assert "P0: speech from player 0" in rendered
    assert "P5: speech from player 5" in rendered


# ==========================================================================
# Spec 025: the window is a parameter (default + override + ablation parity)
# ==========================================================================


def _multi_day_messages(days: int, speeches_per_day: int) -> list:
    """Build a synthetic multi-day public history.

    Each day opens with a Moderator ``SystemMessage`` carrying a distinctive,
    day-tagged marker (``Day-N-open``) and then ``speeches_per_day`` named
    ``AIMessage`` speeches each tagged with their day and index, so a test can
    assert exactly which day's events survived the window.
    """
    messages: list = []
    for day in range(1, days + 1):
        messages.append(SystemMessage(content=f"Moderator note: Day-{day}-open."))
        for i in range(speeches_per_day):
            messages.append(
                AIMessage(content=f"Day{day} remark {i}", name=f"P{i}")
            )
    return messages


def test_render_context_defaults_to_the_prior_baseline_window() -> None:
    """With no ``window`` passed, ``_render_context`` keeps the 30-msg baseline.

    Build 40 public speeches (> 30) and assert exactly the last 30 survive — the
    default-parameter value is ``_CONTEXT_WINDOW`` (30), so direct callers that
    don't pass a window get the unchanged pre-025 behaviour.
    """
    messages = [
        AIMessage(content=f"line {i}", name=f"P{i}") for i in range(40)
    ]

    rendered = _render_context(messages, SPEAKER_ID)

    lines = rendered.splitlines()
    assert len(lines) == _CONTEXT_WINDOW == 30
    # The oldest 10 (0..9) are trimmed; the newest 30 (10..39) survive.
    assert "P9: line 9" not in rendered
    assert "P10: line 10" in rendered
    assert "P39: line 39" in rendered


def test_render_context_window_override_widens_the_view() -> None:
    """A larger ``window`` keeps more history than the 30 baseline would."""
    messages = [
        AIMessage(content=f"line {i}", name=f"P{i}") for i in range(120)
    ]

    rendered = _render_context(messages, SPEAKER_ID, window=100)

    lines = rendered.splitlines()
    assert len(lines) == 100
    # window=100 over 120 messages keeps the last 100 (indices 20..119): line 20
    # survives where the 30-message baseline would have dropped it; line 19 is
    # the oldest casualty.
    assert "P19: line 19" not in rendered
    assert "P20: line 20" in rendered
    assert "P119: line 119" in rendered


def test_window_set_to_30_reproduces_prior_render_byte_identically() -> None:
    """Ablation parity (R-ablation): ``window=30`` == the pre-025 default render.

    The pre-025 ``_render_context(messages, speaker_id)`` always sliced
    ``[-30:]``. Passing ``window=_CONTEXT_WINDOW`` (30) explicitly must produce
    the byte-for-byte same string as the bare-default call — proving the knob
    set back to 30 reproduces the old short window exactly. Uses a mixed
    Moderator + speech history longer than 30 so the slice actually bites.
    """
    messages: list = [SystemMessage(content="Day breaks. Discuss.")]
    for i in range(50):
        messages.append(AIMessage(content=f"remark {i}", name=f"P{i}"))

    baseline = _render_context(messages, SPEAKER_ID)
    explicit_30 = _render_context(messages, SPEAKER_ID, window=_CONTEXT_WINDOW)

    assert explicit_30 == baseline


def test_fuller_window_spans_at_least_three_days() -> None:
    """R1: the default fuller window reaches back across ≥3 days of events.

    Build 4 days × 10 speeches (= 44 messages with the day-open notes) and
    render at the production default (~150). Events from the EARLIEST day
    survive — where the prior 30-message window would have dropped them — so an
    AI player reasons from multiple days, not a fraction of the current Day.
    """
    days, per_day = 4, 10
    messages = _multi_day_messages(days, per_day)
    # Sanity: this history exceeds the prior baseline window, so day-1 events
    # would have been trimmed at window=30.
    assert len(messages) > _CONTEXT_WINDOW

    rendered = _render_context(
        messages, SPEAKER_ID, window=_DEFAULT_CONTEXT_WINDOW
    )

    # Day-1 (the earliest) survives at the fuller default ...
    assert "Day-1-open" in rendered
    assert "Day1 remark 0" in rendered
    # ... while the prior 30-message window would have dropped the earliest day.
    narrow = _render_context(messages, SPEAKER_ID, window=_CONTEXT_WINDOW)
    assert "Day-1-open" not in narrow
    # The most recent day is present in both.
    assert "Day4 remark 9" in rendered
    assert "Day4 remark 9" in narrow


# ==========================================================================
# Spec 025: the token estimate is a pure, deterministic heuristic
# ==========================================================================


def test_estimate_tokens_is_deterministic_and_pure() -> None:
    """Same text ⇒ same number; the estimate is ``len // _CHARS_PER_TOKEN``."""
    text = "Moderator: Day breaks.\nP0: I suspect Bianca.\nP1: Agreed."
    first = _estimate_tokens(text)
    second = _estimate_tokens(text)

    assert first == second
    assert first == len(text) // _CHARS_PER_TOKEN


def test_estimate_tokens_over_estimates_for_short_english() -> None:
    """The 3-chars/token ratio over-estimates vs the ~4-chars/token real rate.

    Over-estimation is the SAFE direction for a guardrail (it trims earlier,
    never later). For a representative English line the estimate must be at
    least as large as a ~4-chars/token reckoning.
    """
    text = "P0: I think we should look harder at who stayed silent yesterday."
    assert _estimate_tokens(text) >= len(text) // 4


# ==========================================================================
# Spec 025: the defensive token-budget cap trims the OLDEST, never the role
# ==========================================================================


def test_token_budget_cap_trims_oldest_keeps_newest() -> None:
    """An oversized rendered history is trimmed from the OLDEST until it fits.

    Feed many lines with a budget far below their total estimated size, and a
    ``window`` large enough that the COUNT slice would keep them all — so the
    cap (not the count) is what trims. The newest lines survive; the oldest are
    dropped; the result fits the budget by the heuristic.
    """
    messages = [
        AIMessage(content=f"a fairly wordy remark number {i}", name=f"P{i}")
        for i in range(60)
    ]
    # window large enough to keep all 60 ⇒ only the budget can trim.
    budget = 80

    rendered = _render_context(
        messages, SPEAKER_ID, window=1000, token_budget=budget
    )

    # The result fits the budget (by the same heuristic the cap uses).
    assert _estimate_tokens(rendered) <= budget
    # The NEWEST line is retained; an OLD line is dropped.
    assert "P59: a fairly wordy remark number 59" in rendered
    assert "P0: a fairly wordy remark number 0" not in rendered
    # And it really was the cap, not the count window (which kept all 60).
    uncapped = _render_context(messages, SPEAKER_ID, window=1000)
    assert len(uncapped.splitlines()) == 60
    assert len(rendered.splitlines()) < 60


def test_token_budget_cap_no_op_when_history_fits() -> None:
    """A history already under budget is returned untrimmed (cap is inert)."""
    messages = [AIMessage(content=f"line {i}", name=f"P{i}") for i in range(5)]

    capped = _render_context(messages, SPEAKER_ID, window=1000, token_budget=10_000)
    uncapped = _render_context(messages, SPEAKER_ID, window=1000)

    assert capped == uncapped


def test_token_budget_cap_keeps_at_least_the_newest_line() -> None:
    """Even a single line larger than the whole budget keeps the newest line.

    An empty history would be strictly worse than one slightly-over line; the
    cap floors at one line rather than emptying the context.
    """
    messages = [
        AIMessage(content="x" * 500, name="P0"),
        AIMessage(content="y" * 500, name="P1"),
    ]

    rendered = _render_context(messages, SPEAKER_ID, window=1000, token_budget=1)

    lines = rendered.splitlines()
    assert len(lines) == 1
    # The newest (P1) is the one kept.
    assert lines[0].startswith("P1: ")


# ==========================================================================
# Spec 025: R3 — the actor's role/grounding survives an over-long history
# ==========================================================================


class _CapturingDayFake:
    """Content-recording ``get_large()`` stand-in (mirrors test_recap_aware...).

    Returns a scripted ``DayAction``/``Ballot`` and records every prompt it was
    handed so a test can drive the REAL ``_ai_day_action`` / ``_ai_ballot`` and
    inspect the actual rendered prompt the model would have received.
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
    human = messages[1]
    assert isinstance(human, HumanMessage)
    return human.content


def _over_long_state(speaker_role: str = "mafia") -> tuple[GameState, PlayerState]:
    """A Day state whose discussion history dwarfs any window/budget.

    A small living roster (so role grounding is well-defined) plus a synthetic
    history of hundreds of long public speeches — far larger than the fuller
    window AND the token budget — so the trim is guaranteed to bite the history.
    Returns the state and the non-human speaker to drive.
    """
    speaker = PlayerState(
        id="maf-0",
        name="Mallory",
        role=speaker_role,  # type: ignore[arg-type]
        is_human=False,
        is_alive=True,
        persona=PlayerPersona(
            personality="wry and observant",
            manner="speaks in short clipped sentences",
            public_persona="a mild-mannered florist",
            true_self="a ruthless fixer who has done this before",
        ),
    )
    other_maf = PlayerState(
        id="maf-1", name="Victor", role="mafia", is_human=False, is_alive=True
    )
    citizen = PlayerState(
        id="la-0", name="Cleo", role="law_abiding", is_human=False, is_alive=True
    )
    players = {p.id: p for p in (speaker, other_maf, citizen)}
    history: list = []
    for i in range(400):
        history.append(
            AIMessage(
                content=(
                    f"This is a fairly long and substantive Day remark number "
                    f"{i} about who has been acting suspiciously today."
                ),
                name="Cleo",
            )
        )
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": history,
    }
    return state, speaker


def test_role_grounding_survives_over_long_history_in_speak_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 (load-bearing): the speaker's role/win-condition/persona/teammate line
    are never trimmed, no matter how long the discussion.

    Drives the REAL ``_ai_day_action`` through a content-recording fake with a
    synthetic history far larger than the fuller window and the budget, at the
    production default window AND the strict cap, and asserts the essentials
    (assembled OUTSIDE ``_render_context``) are all present in the prompt.
    """
    state, speaker = _over_long_state(speaker_role="mafia")
    fake = _CapturingDayFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(
        speaker,
        state,
        context_window=_DEFAULT_CONTEXT_WINDOW,
        context_token_budget=200,  # deliberately tiny ⇒ aggressive history trim
    )

    prompt = _human_prompt(fake.messages_log[0])
    # The Mafioso's own role + win condition are present (spec 013 grounding).
    assert "your secret role is Mafia" in prompt
    assert "the Mafia, wins when the Mafia count" in prompt
    # The Mafia-only teammate line names the living teammate.
    assert "Victor" in prompt
    assert "fellow Mafiosi" in prompt
    # The cover/stay-in-character instruction (persona) survives too.
    assert "never reveal" in prompt.lower()
    # And the history WAS trimmed (the cap bit) — the oldest remark is gone.
    assert "Day remark number 0 " not in prompt


def test_role_grounding_survives_over_long_history_in_ballot_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 for the vote path: the voter's role/win-condition survive the trim."""
    state, voter = _over_long_state(speaker_role="law_abiding")
    target = state["players"]["maf-1"]
    fake = _CapturingDayFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(
        voter,
        target,
        state,
        context_window=_DEFAULT_CONTEXT_WINDOW,
        context_token_budget=200,
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert "your secret role is Law-abiding Citizen" in prompt
    assert "the Law-abiding Citizens, wins when no Mafia remain" in prompt
    # A Law-abiding voter gets NO teammate list (knowledge-boundary invariant).
    # The history was trimmed by the cap, but the role line was not.
    assert "remark number 0 " not in prompt


def test_role_grounding_survives_at_window_30_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-suspenders: the essentials survive at the ablation window too."""
    state, speaker = _over_long_state(speaker_role="mafia")
    fake = _CapturingDayFake(DayAction(kind="speak", text="A remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(speaker, state, context_window=_CONTEXT_WINDOW)

    prompt = _human_prompt(fake.messages_log[0])
    assert "your secret role is Mafia" in prompt
    assert "Victor" in prompt


# ==========================================================================
# Spec 025: anti-drift — build_runtime_graph threads the window
# ==========================================================================


def test_build_runtime_graph_threads_context_window(tmp_path) -> None:
    """``build_runtime_graph`` accepts + forwards ``context_window`` /
    ``context_token_budget`` to ``_assemble_graph`` (the named anti-drift seam).

    Mirrors the spec-018/019/023 anti-drift coverage: assert the runtime builder
    forwards the spec-025 params into ``_assemble_graph`` rather than dropping
    them, so the deployed Runtime shows AI players the same fuller window as
    local mode.
    """
    from unittest.mock import patch

    from graphia.runtime.graph_builder import build_runtime_graph

    with patch("graphia.runtime.graph_builder._assemble_graph") as mock_assemble:
        build_runtime_graph(
            "thread-xyz",
            tmp_path,
            context_window=137,
            context_token_budget=9999,
        )

    assert mock_assemble.call_count == 1
    kwargs = mock_assemble.call_args.kwargs
    assert kwargs["context_window"] == 137
    assert kwargs["context_token_budget"] == 9999


def test_build_runtime_graph_defaults_match_config_defaults(tmp_path) -> None:
    """The runtime builder's defaults match the config defaults (no drift)."""
    from unittest.mock import patch

    from graphia.runtime.graph_builder import build_runtime_graph

    with patch("graphia.runtime.graph_builder._assemble_graph") as mock_assemble:
        build_runtime_graph("thread-default", tmp_path)

    kwargs = mock_assemble.call_args.kwargs
    assert kwargs["context_window"] == _DEFAULT_CONTEXT_WINDOW == 150
