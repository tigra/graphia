"""Privacy + label correctness for the AI day-speech/vote context builder.

``_render_context`` (``src/graphia/nodes/day.py``) renders the recent
discussion shown to an AI speaker (``_ai_day_action``) and AI voter
(``_ai_ballot``). Two gameplay-integrity properties are locked here:

Bug 1 — relabel: a public ``SystemMessage`` (the Moderator's voice, e.g. the
victim/role reveal) is labelled ``Moderator:`` in the AI context, matching the
human UI (``ui/app.py`` ``_write_public``), not ``SystemMessage:``.

Bug 2 — private filter: a whisper carrying
``additional_kwargs["private_to"] == other_id`` (the Mafia teammate intro from
``first_night_mafia_intros``; the human's role reveal from ``reveal_role``) must
NOT enter a *different* speaker's context — otherwise a Law-abiding AI could
read the Mafia roster. The speaker's OWN whisper is kept (a mafioso's only
record of its team), labelled ``Moderator (private):`` to match the UI.

Pure tests over hand-built message lists built from the REAL templates, so a
reword of a template breaks the test rather than letting a stale literal pass.
No graph, no LLM, no AWS.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, SystemMessage

from graphia.nodes.day import _render_context
from graphia.prompts import (
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    MAFIA_TEAMMATE_INTRO_TEMPLATE,
)

MAFIA_ID = "mafia-1"
OTHER_MAFIA_ID = "mafia-2"
LAW_ABIDING_ID = "citizen-1"
HUMAN_ID = "human-1"

# Real Moderator victim/role reveal — the public line every speaker must see.
VICTIM_REVEAL = DAY_OPEN_VICTIM_REVEAL_TEMPLATE.format(
    name="Daria", role_label="Law-abiding Citizen"
)
# Real Mafia teammate-intro whisper content (private to a mafioso).
MAFIA_INTRO = MAFIA_TEAMMATE_INTRO_TEMPLATE.format(names="Mallory, Victor")
# Real-shaped human role-reveal whisper content (see setup.reveal_role).
HUMAN_ROLE_REVEAL = "You are Alice. Your role is Mafia."


def _mafia_intro_whisper(to_id: str) -> SystemMessage:
    return SystemMessage(
        content=MAFIA_INTRO,
        additional_kwargs={"private_to": to_id},
    )


def _public_reveal() -> SystemMessage:
    return SystemMessage(content=VICTIM_REVEAL)


def test_law_abiding_speaker_does_not_see_mafia_intro() -> None:
    """A Law-abiding speaker's context omits a mafia teammate-intro whisper."""
    messages = [
        _mafia_intro_whisper(MAFIA_ID),
        _public_reveal(),
        AIMessage(content="I think Daria was too quiet.", name="Bianca"),
    ]

    rendered = _render_context(messages, LAW_ABIDING_ID)

    # The teammate roster must not leak. Match on distinctive intro content.
    assert "Mallory, Victor" not in rendered
    assert MAFIA_INTRO not in rendered
    assert "Mafia teammates" not in rendered
    # Public discussion is still present.
    assert "Bianca: I think Daria was too quiet." in rendered


def test_mafioso_speaker_sees_own_intro_whisper() -> None:
    """A mafioso's OWN teammate-intro whisper stays in its context.

    This is load-bearing: the Day/vote prompts never re-inject the role, so the
    mafioso's only record of its team is this whisper. It is labelled to match
    the UI's ``Moderator (private):``.
    """
    messages = [
        _mafia_intro_whisper(MAFIA_ID),
        _public_reveal(),
        AIMessage(content="Let's stay calm.", name="Mallory"),
    ]

    rendered = _render_context(messages, MAFIA_ID)

    assert "Mallory, Victor" in rendered
    assert f"Moderator (private): {MAFIA_INTRO}" in rendered


def test_mafioso_does_not_see_other_mafioso_intro() -> None:
    """One mafioso must not see a teammate's separately-addressed intro.

    Each mafioso gets its OWN intro whisper (``private_to`` = that mafioso).
    A whisper addressed to a *different* mafioso is still private-to-other and
    must be dropped — the filter keys on the recipient id, not the role.
    """
    distinct_other = MAFIA_TEAMMATE_INTRO_TEMPLATE.format(names="Mallory")
    messages = [
        SystemMessage(
            content=distinct_other,
            additional_kwargs={"private_to": OTHER_MAFIA_ID},
        ),
        _public_reveal(),
    ]

    rendered = _render_context(messages, MAFIA_ID)

    assert distinct_other not in rendered
    # The public reveal still survives.
    assert VICTIM_REVEAL in rendered


def test_human_role_reveal_not_visible_to_ai_speaker() -> None:
    """The human's private role-reveal whisper is hidden from an AI speaker."""
    messages = [
        SystemMessage(
            content=HUMAN_ROLE_REVEAL,
            additional_kwargs={"private_to": HUMAN_ID},
        ),
        _public_reveal(),
    ]

    rendered = _render_context(messages, LAW_ABIDING_ID)

    assert HUMAN_ROLE_REVEAL not in rendered
    assert "Your role is" not in rendered
    assert VICTIM_REVEAL in rendered


def test_public_moderator_reveal_visible_and_labelled_for_every_speaker() -> None:
    """The public victim/role reveal is visible to all and labelled ``Moderator:``.

    Bug 1: a public ``SystemMessage`` must render as ``Moderator:`` (matching
    ``ui/app.py``), never ``SystemMessage:``.
    """
    messages = [
        _mafia_intro_whisper(MAFIA_ID),
        _public_reveal(),
    ]

    for speaker_id in (LAW_ABIDING_ID, MAFIA_ID, HUMAN_ID):
        rendered = _render_context(messages, speaker_id)
        assert f"Moderator: {VICTIM_REVEAL}" in rendered, speaker_id
        # The old (buggy) label must never appear.
        assert "SystemMessage:" not in rendered, speaker_id


def test_private_to_others_does_not_consume_window_budget() -> None:
    """Other players' whispers are filtered BEFORE the 30-message window.

    Pad the front with >30 whispers addressed to a different player, then a
    public line. If filtering ran after windowing, the public line would be
    pushed out; filtering-first keeps it visible (spec 008 intent).
    """
    messages: list = [
        _mafia_intro_whisper(OTHER_MAFIA_ID) for _ in range(40)
    ]
    messages.append(
        AIMessage(content="A late but visible public line.", name="Finn")
    )

    rendered = _render_context(messages, LAW_ABIDING_ID)

    assert "Finn: A late but visible public line." in rendered
    assert "Mallory, Victor" not in rendered


# ---------------------------------------------------------------------------
# Spec 025: the privacy filter is unchanged under the new window/budget params.
# ---------------------------------------------------------------------------


def test_privacy_holds_at_the_fuller_window_and_under_the_cap() -> None:
    """Other players' whispers never leak — even at the fuller window + budget cap.

    Spec 025 added ``window`` / ``token_budget`` keyword params to
    ``_render_context``. The privacy filter runs BEFORE both, so a wide window
    and an active token-budget cap must not change the invariant: a whisper
    addressed to another player is still dropped, the speaker's own whisper is
    still kept and labelled, and the public reveal is still visible.
    """
    messages = [
        _mafia_intro_whisper(OTHER_MAFIA_ID),  # addressed to a DIFFERENT mafioso
        _mafia_intro_whisper(MAFIA_ID),  # the speaker's OWN whisper
        _public_reveal(),
        AIMessage(content="Let's stay calm.", name="Mallory"),
    ]

    rendered = _render_context(
        messages, MAFIA_ID, window=150, token_budget=20_000
    )

    # The speaker's own whisper is kept and labelled; the public reveal shows.
    assert f"Moderator (private): {MAFIA_INTRO}" in rendered
    assert f"Moderator: {VICTIM_REVEAL}" in rendered
    assert "Mallory: Let's stay calm." in rendered
    # A whisper addressed to another player is still dropped — the same content
    # appears once (the speaker's own), never twice.
    assert rendered.count(MAFIA_INTRO) == 1
