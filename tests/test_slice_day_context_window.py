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
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, SystemMessage

from graphia.config import _MAX_TABLE_SIZE
from graphia.nodes.day import _CONTEXT_WINDOW, _render_context

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
