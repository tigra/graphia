"""Spec 040 (The Moderator's Closing Story), Task 2.2 — the markup chain.

WHAT THIS FILE OWNS. Task 2.1 stopped ``ui/app.py`` handing text nobody
authored to Rich's tag parser: four call sites that interpolate model or
player content moved from ``Text.from_markup`` to ``Text.assemble``, which
attaches styles to character *ranges* instead of parsing them out of the
string. This file is the regression that keeps them there. It is the only
place in the suite where a bracket ever reaches ``#public-log``.

THE FAULT HAS TWO HALVES AND THEY NEED TWO TESTS. Measured on the installed
Rich, whose tag pattern is ``((\\*)\\[([a-z#/@][^[]*?)])``:

- ``'Candlewright called it [gibberish] and moved on.'`` renders as
  ``'Candlewright called it  and moved on.'`` — **silently deleted**. No
  exception, no trace, nothing on screen to tell the player a word went
  missing. This is the half with no symptom.
- ``'a stray [/] close'`` and ``'a stray [/bold] close'`` raise
  ``MarkupError``, which propagates ``_write_public`` ->
  ``_handle_graph_message`` -> ``driver._consume_stream`` ->
  ``driver.drive_graph`` -> ``_drive``'s ``except``, so ``_record_career`` is
  never reached and a red banner replaces the ending.

**They cannot share one probe string.** Against unfixed code the crash fires
first, so a combined story would never reach the deletion assertion and the
insidious half would be un-mutation-verified while appearing covered. Hence
one named test per half, each failing independently — which is the only shape
that makes Task 10.1's mutation entry ("the markup path restored — the silent
deletion **and** the ``MarkupError``") mean anything.

**AND EVERY DELETION PROBE HERE IS LOWERCASE, DELIBERATELY.** Rich only treats
a bracket as a tag when its body opens with a lowercase letter, ``#``, ``/``
or ``@``: ``[gibberish]`` and ``[sic]`` are deleted, while ``[Gibberish]`` and
``[Sic]`` **survive untouched on unfixed code**. A capitalised probe would
pass against the fault it is meant to catch. That is not a hypothetical —
Task 1.9 watched the fake's own ``[UNSCRIPTED-FAKE-DEFAULT: …]`` persona text
render with its brackets intact on unfixed code, for exactly this reason, and
it is why no assertion in this file uses a capitalised token.

TWO REACHABLE PATHS, NOT ONE. The spec argues the fault from the story — the
largest string the game has ever put on that path. But Task 2.1 found the
worse one: ``_request_resume``'s ``"It's your turn, {speaker_name}."`` hint
interpolates the name the person typed at the name prompt, taken as
``value.strip()`` with no sanitising. A player who calls themselves ``[/]``
used to crash **their own first turn**, and one who calls themselves
``[sic]`` had their name deleted from the line announcing it — on **every
human turn of every game**, a path incomparably more travelled than the
ending. Both paths are covered here, one test per half on each.

WHICH SITE EACH TEST GUARDS, measured by mutating one site at a time and
re-running this file (Task 2.2's "do it per-site if you can"):

======================================  ==========================  =========
mutation (production, then reverted)    tests that turn red         half
======================================  ==========================  =========
``_write_public`` story branch ->       the deletion story test     deletion
``from_markup``, narrow guard KEPT      only
``_write_public`` restored to its       all four                    both
pre-2.1 body (one ``from_markup``,
no story branch, no guard)
``_write_public`` general write ->      both name tests             both
``from_markup``, story branch kept      (the human's own speech
                                        row is labelled with the
                                        name they typed)
turn hint -> ``from_markup``            both name tests             both
``_write_private`` -> ``from_markup``   both name tests             both
                                        (the private role reveal
                                        greets the player by the
                                        name they typed)
``day_turn_error`` -> ``from_markup``   NONE — see below            neither
======================================  ==========================  =========

THE ASYMMETRY IN ROW 1 IS THE POINT, and it is why the deletion half could
never have been left to the crash test. Task 2.1 wrapped the story write in a
narrow ``try/except`` -> plain-``Text`` fallback, so a ``from_markup``
restored *inside* that guard raises, is caught, and the story still lands
literally with the career panel intact — the crash story test stays green,
correctly, because the ending really is whole. The guard is blind to the
deletion half, because deletion does not raise. Defence in depth covers one
half and only one; the other half has nothing but its own test.

THE WHISPERS PANE IS ON THE CHAIN TOO, which the per-site mutations found
rather than the plan. ``setup.reveal_role`` writes ``f"You are {human.name}.
Your role is {role_label}."`` privately, so ``_write_private`` takes the
player's own unsanitised name as surely as the turn hint does — reverting it
to ``from_markup`` turns both name tests red. Both name tests therefore assert
the reveal row as well, and the table above lists that site.

THE ONE SITE NOTHING HERE COVERS, stated rather than left to be discovered:
``_request_resume``'s ``day_turn_error`` line. Reverting it to ``from_markup``
leaves all four tests green, because its strings are the graph's own literals
today ("Usage: /vote <name>", "No such player. Try again.") and no test here
issues a malformed vote command. Task 2.1 converted it anyway, on the grounds
that the call site cannot see where the string came from — recorded as
uncovered rather than asserted vacuously.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from textual.widgets import RichLog

from graphia.llm import Ballot, DayAction, Diary, Story
from graphia.ui.app import GraphiaApp

from conftest import drive_until_public_log_contains
from test_spec040_story_ui_order import (
    BANNER_MARKER,
    CAREER_PANEL_MARKER,
    NAME_POOL,
    _boot_and_name,
    _public_lines,
    _sole_row,
)

# --------------------------------------------------------------------------
# The probes
# --------------------------------------------------------------------------

# Nonce first word, so the row selectors below cannot latch onto anything else
# on screen; two paragraphs, because the story really is one multi-line
# ``write`` and a one-line body would hide whether a token on a continuation
# row survives too. Every line is short enough to occupy ONE rendered row:
# ``#public-log`` under ``run_test()``'s default 80x24 has only ~76 columns of
# content (round border plus ``padding: 0 1``), and the story's first row
# additionally carries ``_write_public``'s 11-character ``"Moderator: "``
# label. Task 1.9 lost a whole draft to that wrap.
DELETION_STORY = Story(
    text=(
        "Candlewright called it [gibberish] and moved on.\n\n"
        "By the last night the tally was [sic] and nobody minded."
    )
)

# NO lowercase word token anywhere in this one — only close tags. See the
# module docstring: the two halves cannot share a probe string, because
# against unfixed code the raise happens first and the deletion assertion is
# never reached.
CRASH_STORY = Story(
    text=(
        "Candlewright left a stray [/] where a bell should hang.\n\n"
        "By dawn the [/bold] tags had stopped pretending."
    )
)

# Bracket-free, for the two name tests: there the NAME is the probe, and a
# bracketed story would make a failure ambiguous between two call sites.
PLAIN_STORY = Story(text="Candlewright kept a tally that nobody asked to see.")

# Every scripted line an AI says or writes is bracket-free for the same
# reason. The queues replay their last value once drained, so one of each
# serves a whole game.
AI_DAY_LINE = DayAction(kind="speak", text="I am watching the table closely.")
AI_BALLOT = Ballot(yes=False)
SCRIPTED_DIARY = Diary(entry="Day one is done and I trust no one yet.")

# ``_request_resume``'s day-turn hint, verbatim up to the name. Selecting rows
# by this prefix is what makes the deletion assertion sharp: under the fault
# the row is still there and still starts with this, it has just lost the name
# out of the middle of it.
TURN_HINT_PREFIX = "It's your turn,"

# ``_drive``'s local-mode failure banner. Only its first words are matched:
# the ``GRAPHIA_LOG_FILE`` path that follows them is a long ``tmp_path`` and
# wraps across rows.
ERROR_BANNER_MARKER = "Error — see"


# --------------------------------------------------------------------------
# Driving one whole game with the human alive
# --------------------------------------------------------------------------


def _assert_the_ending_is_whole(rows: list[str]) -> None:
    """The career panel is there and no error banner replaced the ending.

    THE CHAIN CLAIM, and the one that ties this file back to the functional
    spec's criteria rather than to Rich's parser. §2.11 records the
    propagation path in full: a ``MarkupError`` out of ``_write_public``
    unwinds through ``_handle_graph_message``, ``_consume_stream`` and
    ``drive_graph`` into ``_drive``'s ``except``, which means
    ``_record_career`` is never called. So "the career panel appears" and "no
    error banner is shown" are not two tidy extra assertions — they are two
    visible halves of the same survival, and the panel's presence is the
    load-bearing one: a banner could in principle be suppressed while the
    panel was still skipped.
    """
    assert [row for row in rows if CAREER_PANEL_MARKER in row], (
        "the career summary is missing from the ending — the markup path "
        "raised through _drive's except before _record_career could run. "
        "Public log was:\n" + "\n".join(rows)
    )
    assert not [row for row in rows if ERROR_BANNER_MARKER in row], (
        "an error banner replaced part of the ending; public log was:\n"
        + "\n".join(rows)
    )
    assert [row for row in rows if BANNER_MARKER in row], (
        "the finished-game banner is missing, so the ending did not complete "
        "normally. Public log was:\n" + "\n".join(rows)
    )


def _private_lines(app: GraphiaApp) -> list[str]:
    """Every rendered row of ``#private-log``, the mirror of ``_public_lines``.

    The whispers pane needs its own reader because it is a different widget on
    a different write path (``_write_private``), and the per-site mutations
    showed it carries the same player-typed name the public pane does.
    """
    rows: list[str] = []
    for line in app.query_one("#private-log", RichLog).lines:
        text = getattr(line, "text", None)
        rows.append((text if text is not None else str(line)).strip())
    return rows


def _assert_the_reveal_greets_the_player(app: GraphiaApp, name: str) -> None:
    """The private role reveal still says the player's name.

    ``setup.reveal_role``'s body is ``f"You are {human.name}. Your role is
    …"``, so the whispers pane takes the unsanitised name straight from the
    name interrupt — the same exposure as the turn hint, on the pane that
    carries the role reveal and the Mafia teammate intros. Asserted on the
    ROW rather than on the message so it measures what ``_write_private``
    actually rendered.
    """
    rows = _private_lines(app)
    reveal = [row for row in rows if "You are " in row]
    assert reveal, (
        "the private role reveal never reached the whispers pane. Private "
        "log was:\n" + "\n".join(rows)
    )
    assert all(name in row for row in reveal), (
        f"the private role reveal must greet the player by name ({name!r}); "
        f"got {reveal!r}"
    )


def _assert_the_human_survived(rows: list[str]) -> None:
    """No spectator notice, i.e. the Night did not take the human.

    NOT DEFENSIVENESS — this was a real 30%-of-runs failure while this file
    was being written, and the assertion is what makes the scenario's
    precondition visible instead of leaving a dead human to surface as some
    unrelated-looking absence three assertions later. ``fake_large`` cannot
    pre-script a Night target (the ids are UUIDs minted by ``assign_roles``),
    so an unprimed ``Pointing`` queue serves a deliberately INVALID id, the
    node retries, and its random fallback picks any alive player — including
    the human, who then never gets a Day turn at all. The fix is the
    ``dynamic_night_pointing`` fixture, installed **before the name is
    submitted** so the worker cannot reach the Night before it lands; this
    assertion is what proves the fixture did its job.
    """
    assert not [row for row in rows if "spectating" in row], (
        "the Night took the human, so this game never asked them to speak — "
        "the night-pointing fake did not steer the kill. Public log was:\n"
        + "\n".join(rows)
    )


# --------------------------------------------------------------------------
# The story: one test per half
# --------------------------------------------------------------------------


async def test_a_lowercase_bracket_word_in_the_story_is_not_silently_deleted(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    dynamic_night_pointing: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[gibberish]`` and ``[sic]`` reach the screen instead of vanishing.

    THE HALF WITH NO SYMPTOM, so it gets the first and most careful test.
    Both probes are lowercase — the fault is case-sensitive and a capitalised
    token would sail through unfixed code — and neither is a close tag, so
    nothing in this story can raise and the assertion is always reached.

    The scenario is the shortest game that reaches a real outcome, borrowed
    from Task 1.9: one Mafioso and two Citizens (the human pinned Law-abiding
    via ``GRAPHIA_ROLE``, so no ``point`` interrupt ever fires), the Night
    takes the AI Citizen, parity ends the game before a single Day runs. The
    human is named "Alice" here on purpose: in this test the STORY is the
    probe, and a bracketed name would make a failure ambiguous between two
    call sites.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    fake_small(NAME_POOL)
    # ``diaries=`` is scripted defensively even though this game should end on
    # Night 1: the Day->Night hinge fans ``day_diary`` out for any game that
    # survives its first Day, and no assertion here should depend on the
    # scenario ending when it is meant to.
    fake = fake_large(stories=[DELETION_STORY], diaries=[SCRIPTED_DIARY])

    app = GraphiaApp()
    async with app.run_test() as pilot:
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )
        await _boot_and_name(app, pilot)

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_lines(app))
        )
        rows = _public_lines(app)

        # The guard the whole spec-040 suite carries: ``moderator_story``'s
        # broad ``except`` turns a missed re-patch of the endgame call site
        # into a legitimate no-story ending, so a missing token could
        # otherwise mean "no story was ever produced" rather than "the story
        # lost a word".
        assert fake.calls_by_schema[Story] == 1, (
            "expected exactly one Story call through the fake, got "
            f"{fake.calls_by_schema[Story]}"
        )

        # Exactly one row per token, so a match cannot come from somewhere
        # else on screen and cannot be a duplicate. ``[gibberish]`` is on the
        # story's FIRST row, behind the styled ``"Moderator: "`` label — the
        # composition the fault lived in — and ``[sic]`` is on a continuation
        # row, which has no label. Both must survive.
        _sole_row(rows, "[gibberish]", "the story's first bracketed word")
        _sole_row(rows, "[sic]", "the story's second bracketed word")

        # And the chain: a story that keeps its words must also keep the rest
        # of the ending. No mutation of the deletion half can turn these red
        # (deletion never raises), which is said here rather than left for a
        # reader to wonder about — they are the regression's other half, and
        # the crash test is where they carry the weight.
        _assert_the_ending_is_whole(rows)

        await pilot.press("q")


async def test_a_close_tag_in_the_story_does_not_crash_the_ending(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    dynamic_night_pointing: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[/]`` and ``[/bold]`` render literally and the ending stays whole.

    THE CRASH HALF, on its own, with no deletion token in the probe. Against
    unfixed code this is the failure that costs more than it looks: the raise
    unwinds past ``_record_career``, so the person gets a red banner where the
    career panel should be — three of this spec's acceptance criteria broken
    by one stray close tag in a model-written paragraph.

    THE MUTATION THIS TEST IS AND IS NOT SENSITIVE TO, measured rather than
    assumed. Restoring ``from_markup`` *inside* Task 2.1's narrow story guard
    leaves this test GREEN, and rightly: the guard catches the ``MarkupError``
    and writes the story as plain ``Text``, so it lands literally and the
    ending is whole — which is exactly what the belt-and-braces was for. What
    turns this test red is the pre-2.1 write: one ``from_markup`` for every
    message, no story branch, no guard. The deletion test above is the one
    that pins the story branch itself, because the guard cannot see a fault
    that does not raise.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    fake_small(NAME_POOL)
    fake = fake_large(stories=[CRASH_STORY], diaries=[SCRIPTED_DIARY])

    app = GraphiaApp()
    async with app.run_test() as pilot:
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )
        await _boot_and_name(app, pilot)

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_lines(app))
        )
        rows = _public_lines(app)

        assert fake.calls_by_schema[Story] == 1, (
            "expected exactly one Story call through the fake, got "
            f"{fake.calls_by_schema[Story]}"
        )

        # Rendered literally — both shapes, because they fail differently
        # inside Rich ("has nothing to close" vs "doesn't match any open
        # tag") and only one of them would be caught by a fix that merely
        # balanced the label's own tags.
        _sole_row(rows, "[/]", "the story's bare close tag")
        _sole_row(rows, "[/bold]", "the story's named close tag")

        # The chain, and here it is the load-bearing claim: under the fault
        # these are the rows that disappear.
        _assert_the_ending_is_whole(rows)
        assert app._game_over is True

        await pilot.press("q")


# --------------------------------------------------------------------------
# The player's own name: the same fault on the path every human turn takes
# --------------------------------------------------------------------------


async def test_a_lowercase_bracket_name_survives_the_players_own_turn_hint(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    dynamic_night_pointing: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A player called ``[sic]`` is still named in their own turn hint.

    THE PATH THE PLAN MISSED. §2.11 argues the whole markup fault from the
    closing story; Task 2.1 found the name prompt feeding the same parser on
    every human turn of every game. Measured on the pre-fix string:
    ``from_markup("[dim]It's your turn, [sic].[/dim]")`` renders ``"It's your
    turn, ."`` — the player's name deleted from the one line that exists to
    address them.

    A FOUR-SEAT TABLE, and the seat count is load-bearing. One Mafioso and
    three Citizens (the human among them) is the smallest lineup that reaches
    a Day at all: the first Night takes one AI Citizen and leaves 1 vs 2, so
    the win check does not fire and Day 1 runs. The story tests' three-seat
    scenario ends at parity on Night 1 and never prompts the human, which is
    why it cannot carry these two assertions.

    The human's own speech row is asserted alongside the hint, and it is not
    decoration: it is labelled ``f"{name}: "`` through ``_write_public``'s
    GENERAL write, the one path no other test in this file touches (the story
    goes through the guarded branch). Pre-fix that label was
    ``f"[bold]{name}:[/] {body}"``, which renders as ``": ."`` — the name gone
    from the line attributing their own words to them.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "3")
    fake_small(NAME_POOL)
    # ``ballots`` are scripted even though a speak-only Day never calls for
    # one: an execution would remove a player this scenario's arithmetic
    # depends on, and "No" is the answer that keeps the table as dealt.
    fake = fake_large(
        day_actions=[AI_DAY_LINE],
        ballots=[AI_BALLOT],
        stories=[PLAIN_STORY],
        diaries=[SCRIPTED_DIARY],
    )

    name = "[sic]"
    app = GraphiaApp()
    async with app.run_test() as pilot:
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )
        # Typed key by key, brackets included: Textual maps ``[`` to
        # ``left_square_bracket`` and back to the character, so this is the
        # same path a person's keyboard takes to the name interrupt.
        await _boot_and_name(app, pilot, name)

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_lines(app))
        )
        rows = _public_lines(app)

        _assert_the_human_survived(rows)
        _assert_the_reveal_greets_the_player(app, name)

        hints = [row for row in rows if row.startswith(TURN_HINT_PREFIX)]
        assert hints, (
            "the human was never prompted for a Day turn, so this test "
            "measured nothing. Public log was:\n" + "\n".join(rows)
        )
        assert all(name in row for row in hints), (
            f"every turn hint must name the player ({name!r}); got {hints!r}"
        )

        speech = [row for row in rows if row.startswith(f"{name}: ")]
        assert speech, (
            f"no public row attributes a speech to {name!r}; the general "
            "_write_public label dropped the name. Public log was:\n"
            + "\n".join(rows)
        )

        _assert_the_ending_is_whole(rows)

        await pilot.press("q")


async def test_a_close_tag_name_does_not_crash_the_players_own_first_turn(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    dynamic_night_pointing: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A player called ``[/]`` gets a turn, and an ending, instead of a banner.

    THE CRASH HALF ON THE REACHABLE PATH, and the worst version of the fault
    in the game: pre-fix, ``from_markup("[dim]It's your turn, [/].[/dim]")``
    raises ``MarkupError`` — the bare close tag consumes the ``dim`` the hint
    opened, so the trailing ``[/dim]`` matches nothing — and the raise happens
    inside ``_request_resume`` on the driver worker. That unwinds through
    ``drive_graph`` into ``_drive``'s ``except``, so this player never takes a
    single turn, never sees a story and never sees a career panel: the game
    ends in a red banner at their FIRST Day turn.

    Same lineup and same reasoning as the test above; the name is the only
    difference, and this probe carries no deletion token, so the only failure
    it can catch is the raise.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "3")
    fake_small(NAME_POOL)
    fake = fake_large(
        day_actions=[AI_DAY_LINE],
        ballots=[AI_BALLOT],
        stories=[PLAIN_STORY],
        diaries=[SCRIPTED_DIARY],
    )

    name = "[/]"
    app = GraphiaApp()
    async with app.run_test() as pilot:
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )
        await _boot_and_name(app, pilot, name)

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_lines(app))
        )
        rows = _public_lines(app)

        _assert_the_human_survived(rows)
        _assert_the_reveal_greets_the_player(app, name)

        hints = [row for row in rows if row.startswith(TURN_HINT_PREFIX)]
        assert hints, (
            "the human was never prompted for a Day turn — pre-fix, the hint "
            "write itself raised MarkupError on this name and the game ended "
            "in a banner. Public log was:\n" + "\n".join(rows)
        )
        assert all(name in row for row in hints), (
            f"every turn hint must name the player ({name!r}); got {hints!r}"
        )

        speech = [row for row in rows if row.startswith(f"{name}: ")]
        assert speech, (
            f"no public row attributes a speech to {name!r}. Public log was:\n"
            + "\n".join(rows)
        )

        _assert_the_ending_is_whole(rows)
        assert app._game_over is True

        await pilot.press("q")
