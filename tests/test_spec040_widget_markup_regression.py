"""Spec 040 (The Moderator's Closing Story), Task 2.2a — the OTHER parser.

WHAT THIS FILE OWNS, and why it is a second file rather than four more tests
in ``test_spec040_markup_regression.py``. That file guards ``ui/app.py``'s
writes into ``#public-log`` / ``#private-log``, where the parser is **Rich**'s
``Text.from_markup`` and the fix is ``Text.assemble``. This one guards
``ui/widgets.py``'s modal composition, where the parser is **Textual**'s
``Content.from_markup`` — reached through the ``markup=True`` default on
``Static``, ``Label`` and ``OptionList``'s ``Option`` — and the fix is a
pre-built :class:`textual.content.Content`. Same fault class, same slice, two
different tokenizers with two measurably different behaviours (below), so the
probes cannot be shared and neither can the file's reasoning.

FOUR SITES, AND ONLY TWO OF THEM WERE NAMED BY ANY PLAN DOCUMENT. Task 2.2a's
own line names ``f"Execute {self._target_name}?"`` and
``f"Teammates so far: {self._prior_picks}"``. Sweeping the whole file with
Task 2.1's rule — *does this call site author the string it hands the
parser?* — found two more, and one of them is the one no reader would guess:

======================  ==================================================
site (widget id)        where its text comes from
======================  ==================================================
``vote-title``          ``collect_votes``' interrupt payload
                        (``day.py``: ``target.name``) — includes the human,
                        whose name is ``value.strip()`` from the name
                        prompt with no bracket sanitising. The most
                        reachable of the four.
``vote-error``          ``collect_votes``' re-interrupt ``error``. The
                        graph's own literals TODAY; converted on the same
                        grounds Task 2.1 converted ``day_turn_error``.
``pointing-prior-``     ``night._render_prior_picks``, built from
``picks``               ``player.name`` values — the human's own typed name
                        appears here once they have pointed in a completed
                        round.
``pointing-options``    every target's name, i.e. the small model's roster
                        output. **The site no document names**, and the one
                        with no ``markup=`` switch to reach for: ``Option``
                        takes a ``VisualType`` prompt, so a ``Content`` is
                        the only way to keep the name out of the tokenizer.
======================  ==================================================

THE TWO HALVES STILL NEED TWO TESTS, for the reason Task 2.2 measured: under
a restored markup parse ``[/]`` **raises** and ``[sic]`` is **deleted**, so a
combined probe fails on the raise and never reaches the deletion assertion —
leaving the half with no symptom un-mutation-verified while appearing
covered. Hence one parametrized test per half, each site its own test id.

EVERY DELETION PROBE HERE IS LOWERCASE. Checked, and worth stating precisely
because **the case-sensitivity fact this suite carries elsewhere does NOT
hold for this parser** — measured on the installed Textual 8.2.4:

    Content.from_markup("Execute [sic]?")  -> 'Execute ?'      deleted
    Content.from_markup("Execute [Sic]?")  -> 'Execute ?'      deleted
    Content.from_markup("Execute [SIC]?")  -> 'Execute ?'      deleted
    Content.from_markup("Execute [/]?")    -> raises MarkupError

    Text.from_markup("Execute [sic]?")     -> 'Execute ?'      deleted
    Text.from_markup("Execute [Sic]?")     -> 'Execute [Sic]?'  SURVIVES
    Text.from_markup("Execute [SIC]?")     -> 'Execute [SIC]?'  SURVIVES

Rich's tag pattern is ``[a-z#/@]`` and is therefore case-sensitive; Textual's
tokenizer is **case-INSENSITIVE** and eats ``[Sic]`` as well. So a
capitalised probe would NOT have been vacuous here the way it is against
``app.py`` — but the probes are lowercase anyway, because lowercase is the
one shape both parsers eat and there is no reason for two conventions in one
suite. (The two parsers disagree the other way too: ``[@x]`` is Rich meta
syntax and is deleted by Rich, while Textual leaves it alone.)

WHERE THE RAISE ACTUALLY LANDS — measured, and NOT where Task 2.2a predicts.
That line says a raise here *"lands inside ``push_screen_wait`` on the driver
worker, i.e. the same ``_drive`` ``except`` path and the same lost ending"*.
Measured with a real worker awaiting ``push_screen_wait`` on a ``[/]`` name:

- the worker saw ``CancelledError``, not ``MarkupError``;
- the ``MarkupError`` came out of the ``run_test`` block, i.e. Textual
  handled it as a **fatal app error**.

The parse is lazy — it happens in ``Static.visual`` during the app's own
render cycle, not in ``compose`` and not on the worker — so Textual tears the
app down and cancels the workers. ``_drive`` catches ``Exception``, which
``CancelledError`` is not. **The outcome is therefore worse than the task
line describes rather than equivalent:** no red banner, ``_record_career``
never reached, and a raw Textual crash dump where the ending should be. The
conclusion the line drew — that this breaks the same acceptance criteria as
the ``app.py`` fault — is right; only the mechanism was wrong. It also means
the crash tests below fail *loudly* under mutation (the error surfaces from
the render, immediately) rather than as a missing row three assertions later.

WHY THESE ARE WIDGET-LEVEL AND NOT WHOLE-GAME TESTS. The fault lives entirely
inside ``compose()`` plus the render that follows it, and this repo already
has the deterministic mechanism for exercising exactly that: the
``_ModalHostApp`` pattern in ``test_multi_round_consensus.py``, reused here
rather than re-declared. Driving a real game to reach these two modals would
add the Night-pointing race that cost Task 2.2 a ~30%-per-test flake, and
would buy nothing — reachability is a property of the *payloads*, established
by reading ``day.collect_votes`` and ``night.point_at_target`` (table above),
not something a render test can assert.

WHICH SITE EACH TEST GUARDS, measured by restoring markup at one site at a
time and re-running this file:

=========================================  =============================
mutation (production, then reverted)       tests that turn red
=========================================  =============================
``vote-title`` -> bare f-string            both ``[vote-title]`` params
``vote-error`` -> bare ``self._error``     both ``[vote-error]`` params
``pointing-prior-picks`` -> f-string       both ``[prior-picks]`` params
``pointing-options`` -> ``opt["name"]``    both ``[option-name]`` params
=========================================  =============================

Each mutation turns red exactly its own two params and leaves the other six
green, which is what "mutation-verified per site" means here.

WHAT IS DELIBERATELY NOT CONVERTED, and so not asserted as escaped: the round
header ``f"Night kill — round {n} of {cap}"`` (both values pass an
``isinstance(int)`` guard in ``__init__``, so no string can reach it), the two
dialog titles, and the ``Yes`` / ``No`` button faces. Those are literals this
module writes, and Task 2.1's rule keeps literals on the markup path.
``test_the_modules_own_literals_still_render_intact`` pins that they still
render — it can pin no more than that, stated rather than dressed up: none of
those literals *contains* a tag, so "still parsed as markup" has no
observable consequence to assert.
"""

from __future__ import annotations

from typing import Callable

import pytest
from textual.screen import ModalScreen
from textual.widget import Widget

from graphia.nodes.night import NIGHT_ROUND_CAP
from graphia.ui.widgets import PointingModal, VoteModal

from test_multi_round_consensus import _ModalHostApp, _wait_for

# --------------------------------------------------------------------------
# The probes
# --------------------------------------------------------------------------

# Lowercase, per the module docstring: the one bracket shape BOTH parsers
# delete. Kept to five characters so no probe row can wrap inside the modal
# (``VoteModal`` is ``width: 50%`` and ``PointingModal`` ``width: 40%``, each
# ``min-width: 40``, less a thick border and ``padding: 1 2``).
DELETION_TOKEN = "[sic]"

# The bare auto-close tag, which has nothing to close and so raises. ``[/bold]``
# raises too, but for a different reason ("does not match any open tag") and
# these widgets open no tag of their own, so the bare form is the one that
# reaches the parser in production shape.
CRASH_TOKEN = "[/]"

# A bracket-free filler for the fields that are NOT the probe in a given case,
# so a failure can only be about the site under test.
FILLER_NAME = "Ann"


# --------------------------------------------------------------------------
# The four sites, as (test id, widget id, build a modal around the probe,
# the text that must survive)
# --------------------------------------------------------------------------


def _vote_title(token: str) -> VoteModal:
    return VoteModal(target_name=token)


def _vote_error(token: str) -> VoteModal:
    return VoteModal(target_name=FILLER_NAME, error=token)


def _pointing_prior_picks(token: str) -> PointingModal:
    return PointingModal(
        options=[{"id": "a", "name": FILLER_NAME}],
        round_number=2,
        round_cap=NIGHT_ROUND_CAP,
        prior_picks=token,
    )


def _pointing_option_name(token: str) -> PointingModal:
    return PointingModal(options=[{"id": "a", "name": token}])


_SITES = [
    pytest.param(
        "vote-title",
        _vote_title,
        lambda token: f"Execute {token}?",
        id="vote-title",
    ),
    pytest.param(
        "vote-error",
        _vote_error,
        lambda token: token,
        id="vote-error",
    ),
    pytest.param(
        "pointing-prior-picks",
        _pointing_prior_picks,
        lambda token: f"Teammates so far: {token}",
        id="prior-picks",
    ),
    pytest.param(
        "pointing-options",
        _pointing_option_name,
        lambda token: token,
        id="option-name",
    ),
]


# --------------------------------------------------------------------------
# Reading what was actually painted
# --------------------------------------------------------------------------


def _rendered_text(widget: Widget) -> str:
    """Every painted row of one widget, whitespace-collapsed into one string.

    ``Widget.render_line`` rather than ``Static.render`` on purpose, for two
    reasons. It is the one reader that works for all four sites — an
    ``OptionList``'s option faces are not reachable through ``render``, and
    ``Option.prompt`` still holds the raw ``str``, so reading the prompt would
    measure the *input* and pass under the fault. And it is the call that
    forces the parse: the fault is lazy, resolved in ``Static.visual`` at
    render time, which is why a mutation makes this helper itself raise.
    """
    rows = [
        "".join(segment.text for segment in widget.render_line(y))
        for y in range(widget.size.height)
    ]
    return " ".join(" ".join(rows).split())


async def _mount_and_read(
    modal: ModalScreen[str], widget_id: str
) -> tuple[str, bool]:
    """Push ``modal``, render it, and return (that widget's text, is_running).

    ``is_running`` is read and returned from INSIDE the ``run_test`` block —
    Task 6.5's finding is that an ``is_running`` assertion placed after the
    block measures nothing, because the pilot has already shut the app down by
    then. Here it is the "the app did not die on the render" half of the crash
    claim, and it has to be sampled while the app is still up.
    """
    app = _ModalHostApp(modal)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: app.screen is modal, timeout=5.0)
        await pilot.pause()
        text = _rendered_text(modal.query_one(f"#{widget_id}"))
        running = app.is_running
        app.exit()
    return text, running


# --------------------------------------------------------------------------
# Half one: the silent deletion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("widget_id, build, expected", _SITES)
async def test_a_lowercase_bracket_token_in_a_modal_is_not_silently_deleted(
    widget_id: str,
    build: Callable[[str], ModalScreen[str]],
    expected: Callable[[str], str],
) -> None:
    """``[sic]`` reaches the modal's screen instead of vanishing from it.

    THE HALF WITH NO SYMPTOM, and on these two modals it is the half that
    changes a decision rather than a sentence. Under the fault, a player
    called ``[sic]`` on the chopping block produced the prompt ``"Execute ?"``
    — every voter asked to approve an execution with the name removed from the
    question — and a Mafioso reading ``"Teammates so far: "`` saw an empty
    summary that looked exactly like "nobody has pointed yet".

    ``[sic]`` is lowercase deliberately; see the module docstring. Unlike the
    Rich path, capitalising it would NOT make this vacuous on this parser —
    but lowercase is the shape both parsers eat and the suite keeps one
    convention.
    """
    text, running = await _mount_and_read(build(DELETION_TOKEN), widget_id)

    assert expected(DELETION_TOKEN) in text, (
        f"#{widget_id} lost the bracketed token: expected "
        f"{expected(DELETION_TOKEN)!r} to survive, rendered {text!r}"
    )
    assert running is True


# --------------------------------------------------------------------------
# Half two: the crash
# --------------------------------------------------------------------------


@pytest.mark.parametrize("widget_id, build, expected", _SITES)
async def test_a_close_tag_in_a_modal_does_not_kill_the_app(
    widget_id: str,
    build: Callable[[str], ModalScreen[str]],
    expected: Callable[[str], str],
) -> None:
    """``[/]`` renders literally and the app is still standing afterwards.

    THE CRASH HALF, on its own, with no deletion token in the probe. Against
    unfixed code this does not fail as a missing row: the ``MarkupError``
    comes out of the render, Textual treats it as fatal, and the failure
    arrives as an exception out of ``_mount_and_read`` — which is why the
    ``running`` assertion below is a statement about the fix rather than the
    thing that catches the regression.

    What it costs in a real game is the whole rest of the game. The worker
    parked in ``push_screen_wait`` is handed a ``CancelledError``, which
    ``_drive``'s ``except Exception`` does not catch, so a player called
    ``[/]`` who got nominated took the app down with no banner, no career
    summary and no story — mid-game, on any Night they pointed and on any Day
    they were voted on.
    """
    text, running = await _mount_and_read(build(CRASH_TOKEN), widget_id)

    assert expected(CRASH_TOKEN) in text, (
        f"#{widget_id} did not render the close tag literally: expected "
        f"{expected(CRASH_TOKEN)!r}, rendered {text!r}"
    )
    assert running is True


# --------------------------------------------------------------------------
# The other side of the rule: what was left alone
# --------------------------------------------------------------------------


async def test_the_modules_own_literals_still_render_intact() -> None:
    """The round header and both dialog titles are unchanged by Task 2.2a.

    THE GUARD AGAINST THE MECHANICAL SWEEP. Task 2.1's rule cuts both ways:
    the four interpolating sites had to come off the markup path, and the
    module's own literals had to stay on it. This is the test that fails if a
    later reader "finishes the job" by converting the whole file and fumbles a
    label in the process.

    Stated rather than overclaimed: this pins that the literals still RENDER,
    not that they are still parsed as markup. None of them contains a tag, so
    markup-versus-plain has no observable difference for them — which is also
    why converting them would have been harmless and pointless in equal
    measure, and why the rule is about authorship rather than about risk.
    """
    modal = PointingModal(
        options=[{"id": "a", "name": FILLER_NAME}],
        round_number=1,
        round_cap=3,
    )
    header, running = await _mount_and_read(modal, "pointing-round")
    assert "Night kill — round 1 of 3" in header
    assert running is True

    modal = PointingModal(options=[{"id": "a", "name": FILLER_NAME}])
    title, running = await _mount_and_read(modal, "pointing-title")
    assert "Choose your target (Night Mafia vote)" in title
    assert running is True

    vote = VoteModal(target_name=FILLER_NAME)
    prompt, running = await _mount_and_read(vote, "vote-title")
    assert f"Execute {FILLER_NAME}?" in prompt
    assert running is True
