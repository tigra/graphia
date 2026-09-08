"""Spec 040 (The Moderator's Closing Story), Task 1.9 — the ON-SCREEN order.

WHAT THIS FILE OWNS, AND WHY IT IS NOT COVERED BY THE TWO FILES BESIDE IT.

Task 1.6 (``tests/test_spec040_story_topology.py``) pinned the GRAPH
trajectory: ``end_screen -> moderator_story -> END`` from each of the three
arrival routers. Task 1.8 (``tests/test_spec040_story_node.py``) pinned the
MESSAGE: exactly one flagged, unnamed ``SystemMessage`` per finished game.
Neither of them looks at a screen, and the functional spec's first three
criteria are about ordering **as the person experiences it**:

1. a story appears **after** the roles-and-characters reveal and **before**
   the career summary;
2. the facts come first and the story second — the story never appears above
   the reveal;
3. a story appears for a game that ends because one side won **and** for a
   game that ends any other way it can end.

"After" and "before" are claims about ``#public-log``, and the three things
being ordered reach that widget by two DIFFERENT routes — which is the whole
reason a graph-level trajectory cannot stand in for this test. The facts and
the story arrive as graph messages, one super-step apart, through
``GraphiaApp._write_public``. The career panel is not a graph message at all:
``GraphiaApp._record_career`` writes it directly to the same pane **after**
``await drive_graph(...)`` returns, exactly like the launch greeting, so it
bypasses the ``private_to`` filter. Only a driven app puts all three in one
buffer where their order is observable, which is what ``App.run_test()`` is
for here.

THE FOUR MUTATIONS THESE ASSERTIONS WERE VERIFIED AGAINST, and which named
assertion each one turned red — recorded because an ordering assertion over
an order that cannot be violated proves nothing, and this spec has already
produced one test that had silently gone vacuous (Task 1.7) and one that was
vacuous by construction and was deleted (Task 1.8). Each mutation was applied
to production code, this file was run, and the mutation was reverted:

1. ``graph.py``'s edge reverted to ``end_screen -> END``, so the story node
   never runs — **both tests fail on CLAIM 0**, ``calls_by_schema[Story] ==
   1`` (got 0). Worth knowing that it is claim 0 and not the story-row
   selector that fires: the ``Story`` counter is checked first precisely so a
   story-shaped absence is never reported as a story-shaped ordering failure.
2. ``graph.py`` inverted to ``check_win_* -> moderator_story -> end_screen ->
   END``, so the story runs BEFORE the facts — **both tests fail on CLAIM 1**,
   the ``winner < roster < characters < story`` chain. This is the mutation
   that separates "the story is present" from "the story is present in the
   right place": the story is on screen throughout, and claim 0 stays green.
3. ``app.py``'s ``_handle_graph_message`` made to call ``_record_career`` the
   moment the facts message is written, so the panel lands BEFORE the story
   rather than after ``drive_graph`` returns — **the win test fails on CLAIM
   2**, ``story_idx < career_idx``. The runaway test stays green, and cannot
   do otherwise: see the next paragraph.
4. ``end_screen`` made to append ``_ai_story(state)`` to its own message with
   ``moderator_story`` returning ``{}`` — the FOLD that spec 040 §2.1
   rejected — **both tests fail on CLAIM 3**, the two-Moderator-beats count.
   Every other assertion stays green under it (the story text is on screen,
   after the reveal, before the panel, and the ``Story`` call still happens),
   so claim 3 is the only thing standing between this file and a fold.

THE RUNAWAY PATH'S "BEFORE THE CAREER SUMMARY" IS VACUOUS, AND IS LABELLED
AS SUCH RATHER THAN COUNTED. A Day-cap runaway shows **no career panel at
all**: ``GraphiaApp._OUTCOME_BY_WINNER`` deliberately omits ``"runaway"``
(spec 023 — Game Length Runaway Safeguard, on the grounds that a cap hit is
not a legitimate result), so ``_record_career`` computes ``outcome=None`` and
returns before writing anything. On that path the story is the last content
before the "Game over." banner, so "the story comes before the career
summary" is true of a comparison that does not exist and no mutation can
make it fail. ``test_the_runaway_ending_shows_the_story_and_no_career_panel``
therefore asserts the panel's ABSENCE — a claim that CAN fail — and leaves
the ordering claim to the win path, where both sides of it exist.

WHAT THIS FILE DOES NOT CLAIM. Slice 1 ships a deliberately minimal prompt
(outcome, roster, events — Task 1.4), so the assertions here are that **a**
story arrives in the right place, never that it is worth reading. The private
material is Slice 3, the anti-flat-summary prompt is Slice 4, and the length
clamp is Slice 5. Nothing here is evidence for those.

AND IT DOES NOT TOUCH THE MARKUP FAULT. ``_write_public`` interpolates message
bodies into a markup string and calls ``Text.from_markup``, so a story
containing ``[/bold]`` raises ``MarkupError`` up through ``_drive``'s
``except`` and replaces the whole ending with a red banner. That is Slice 2's
Task 2.1, and the scripted story text below is deliberately bracket-free so
this file measures ordering and not that fault. Task 2.2 owns the regression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from textual.widgets import Input, RichLog

from graphia.llm import Ballot, DayAction, Diary, Pointing, Story
from graphia.prompts import (
    ENDGAME_HEADER_ROSTER,
    ENDGAME_PERSONA_HEADER,
    ENDGAME_WINNER_MAFIA,
    ENDGAME_WINNER_RUNAWAY,
)
from graphia.ui.app import GraphiaApp

from conftest import drive_until_public_log_contains

HUMAN_NAME = "Alice"
NAME_POOL = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav", "Noor", "Kai"]

# The first word of the story is a nonce, so the sole-match selectors below
# cannot latch onto anything else on screen. Two paragraphs, because the story
# really is written as one multi-line ``write`` and a one-line body would hide
# whether the sentinel landed on the beat's FIRST rendered row.
#
# No square brackets anywhere in it — see the module docstring's last
# paragraph. This is not tidiness: bracketed prose is a live crash on this
# path until Slice 2's Task 2.1, and a crash here would replace the ending
# with a banner and fail these tests for the wrong reason.
STORY_SENTINEL = "Candlewright"
SCRIPTED_STORY = Story(
    text=(
        "Candlewright kept a tally that nobody asked to see.\n\n"
        "By the last night it was the only honest thing left at the table."
    )
)

# ``_record_career`` writes this panel directly to the public pane once
# ``drive_graph`` returns; ``stats_store.render_panel`` is its only writer.
CAREER_PANEL_MARKER = "Career update"
BANNER_MARKER = "Game over."

# ``_write_public``'s public-Moderator prefix, verbatim. Counting the rows that
# begin with it is how "the story is its OWN beat" is expressed: a story folded
# into the facts message (the design spec 040 §2.1 rejected) would show one
# such row in the ending instead of two.
MODERATOR_PREFIX = "Moderator:"


# --------------------------------------------------------------------------
# Reading the public pane
# --------------------------------------------------------------------------


def _public_lines(app: GraphiaApp) -> list[str]:
    """Every rendered row of ``#public-log``, in write order, plain and stripped.

    ``RichLog`` keeps one entry per rendered row — a multi-line ``write`` and
    a wrapped long line both become several rows — which is exactly the
    granularity these assertions want: row order IS reading order.

    Stripped because ``#public-log`` carries ``padding: 0 1``; whether that
    padding reaches ``Strip.text`` is a Textual implementation detail and no
    assertion here should depend on it.
    """
    rows: list[str] = []
    for line in app.query_one("#public-log", RichLog).lines:
        text = getattr(line, "text", None)
        rows.append((text if text is not None else str(line)).strip())
    return rows


def _sole_row(rows: list[str], needle: str, what: str) -> int:
    """Index of the ONE row containing ``needle``; fail loudly on 0 or 2+.

    Exactly-one is an anti-vacuity guard rather than defensiveness. An index
    lookup that tolerated "no match" would let every ordering comparison below
    run against a sentinel value, and one that tolerated several would order
    the wrong pair — both of which pass while measuring nothing. Failing the
    selection outright is the only outcome that cannot be mistaken for a pass.
    """
    hits = [i for i, row in enumerate(rows) if needle in row]
    assert len(hits) == 1, (
        f"expected exactly ONE row of #public-log to carry {what} "
        f"({needle!r}); found {len(hits)} among {len(rows)} rows. "
        f"Public log was:\n" + "\n".join(rows)
    )
    return hits[0]


def _moderator_beats_from(rows: list[str], start: int) -> list[int]:
    """Rows at or after ``start`` that OPEN a public Moderator beat."""
    return [
        i for i, row in enumerate(rows) if i >= start and row.startswith(MODERATOR_PREFIX)
    ]


def _first_row_of(line: str, words: int = 5) -> str:
    """The opening words of ``line``, which survive ``#public-log``'s wrapping.

    ``RichLog(wrap=True)`` word-wraps at the pane's content width (80 columns
    less the round border and ``padding: 0 1``), so a long production line
    does NOT occupy one row and cannot be matched whole. That is not
    hypothetical: ``ENDGAME_WINNER_RUNAWAY`` is 84 characters before
    ``_write_public`` prepends ``"Moderator: "``, and the first draft of this
    file searched for it verbatim and found zero rows.

    Taking the first few words of the imported constant keeps the anchor
    DERIVED rather than copied — a reword of the production string moves this
    anchor with it, which a hand-typed fragment would not — and five words of
    either winner line is far short of the wrap point, so the fragment is
    always on the beat's opening row.
    """
    return " ".join(line.split()[:words])


# --------------------------------------------------------------------------
# Driving one whole game through the UI
# --------------------------------------------------------------------------


async def _boot_and_name(app: GraphiaApp, pilot: Any, name: str = HUMAN_NAME) -> Any:
    """Wait for the drive worker to build the graph, then submit the name.

    Returns the compiled graph so the caller can install a live dispatcher —
    the Night target cannot be pre-scripted, because the real player ids only
    exist once ``assign_roles`` has run.

    ``name`` is a parameter rather than the module constant because Task 2.2's
    ``tests/test_spec040_markup_regression.py`` boots the same app as a player
    called ``[sic]`` / ``[/]`` — the name goes straight into the "It's your
    turn, …" hint with no bracket sanitising, so the name prompt is one of the
    two places this spec's markup fault is reachable from. Typed key by key
    (Textual maps ``[`` to ``left_square_bracket`` and back to the character),
    so it is the same path a person's keyboard takes.
    """
    await pilot.pause()
    for _ in range(200):
        if app._graph is not None and app._run_config is not None:
            break
        await pilot.pause(0.05)
    assert app._graph is not None, "graph never initialised"

    for _ in range(200):
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001
            prompt = None
        if prompt is not None and prompt.disabled is False:
            break
        await pilot.pause(0.05)
    await pilot.press(*name)
    await pilot.press("enter")
    return app._graph


# --------------------------------------------------------------------------
# Criterion 1 and 2: facts, then story, then the career summary
# --------------------------------------------------------------------------


async def test_the_story_lands_between_the_reveal_and_the_career_summary(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A side-win ending reads: facts, then story, then the career panel.

    THE SHORTEST GAME THAT REACHES A REAL OUTCOME, so this walk costs one
    Night rather than a whole match: one Mafioso and two Citizens (the human
    is one of them, pinned Law-abiding via ``GRAPHIA_ROLE`` so no ``point``
    interrupt ever fires). The Night takes the AI Citizen, which leaves 1 vs 1
    — parity — so ``check_win_night`` routes to ``end_screen`` before a single
    Day runs and the human is never asked for a Day turn. The Night target is
    resolved live rather than scripted so the HUMAN survives: a dead human
    flips the app into spectator mode, which writes extra rows to the public
    pane and would muddy the row ordering under test.

    CLAIM 0 pins that the story was really produced; CLAIMS 1-4 are the order
    the person reads the ending in. The docstring at the top of this file
    records which mutation turns each of them red.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    fake_small(NAME_POOL)

    # ``diaries=`` is scripted defensively even though this game should end on
    # Night 1: the Day->Night hinge fans ``day_diary`` out for any game that
    # survives its first Day, and no assertion here should depend on the
    # scenario ending when it is meant to.
    fake = fake_large(
        stories=[SCRIPTED_STORY],
        diaries=[Diary(entry="Day one is done and I trust no one yet.")],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        graph = await _boot_and_name(app, pilot)
        run_config = app._run_config
        original_invoke = fake._invoke

        def _invoke_live(schema: Any, messages: Any) -> Any:
            if schema is Pointing:
                players = graph.get_state(run_config).values.get("players", {})
                targets = [
                    p.id
                    for p in players.values()
                    if p.is_alive and p.role == "law_abiding" and not p.is_human
                ]
                return Pointing(target_id=targets[0] if targets else "missing")
            if schema is DayAction:
                return DayAction(kind="speak", text="I am watching carefully.")
            if schema is Ballot:
                return Ballot(yes=False)
            return original_invoke(schema, messages)

        fake._invoke = _invoke_live  # type: ignore[method-assign]

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_lines(app))
        )
        rows = _public_lines(app)

        # CLAIM 0 — the fake really served the story, so "the story is on
        # screen" below cannot be satisfied by anything else that happens to
        # say "Candlewright". This is the guard the whole spec-040 suite
        # carries:
        # ``moderator_story``'s broad ``except`` turns a missed re-patch of
        # ``graphia.nodes.endgame.get_large`` into a legitimate no-story
        # ending, so the ending alone cannot tell "the call failed" from "the
        # call was never made".
        assert fake.calls_by_schema[Story] == 1, (
            "expected exactly one Story call through the fake, got "
            f"{fake.calls_by_schema[Story]}"
        )

        winner_idx = _sole_row(
            rows, _first_row_of(ENDGAME_WINNER_MAFIA), "the winner line"
        )
        roster_idx = _sole_row(rows, ENDGAME_HEADER_ROSTER, "the roster reveal")
        persona_idx = _sole_row(rows, ENDGAME_PERSONA_HEADER, "the characters reveal")
        story_idx = _sole_row(rows, STORY_SENTINEL, "the closing story")
        career_idx = _sole_row(rows, CAREER_PANEL_MARKER, "the career summary")
        banner_idx = _sole_row(rows, BANNER_MARKER, "the game-over banner")

        # CLAIM 1 — criterion 2: the facts come first and the story second.
        # The three facts rows are one ``write`` so their internal order is
        # ``end_screen``'s business, not this spec's; what matters here is that
        # ALL of them precede the story. Asserted against the LAST of them
        # (the characters reveal) because that is the criterion's own
        # boundary: "after the roles-and-characters reveal".
        assert winner_idx < roster_idx < persona_idx < story_idx, (
            "the closing story must appear after the whole facts message "
            f"(winner {winner_idx}, roster {roster_idx}, characters "
            f"{persona_idx}, story {story_idx}). Public log was:\n"
            + "\n".join(rows)
        )

        # CLAIM 2 — criterion 1's second half: the career summary follows the
        # story. Ordered across the two DIFFERENT write routes described in
        # the module docstring, which is the part no graph-level test can see.
        assert story_idx < career_idx < banner_idx, (
            "the career summary must follow the story and precede the banner "
            f"(story {story_idx}, career {career_idx}, banner {banner_idx}). "
            "Public log was:\n" + "\n".join(rows)
        )

        # CLAIM 3 — the story is its OWN ``Moderator:`` beat, not prose folded
        # onto the end of the facts message. Spec 040 §2.1 chose a second
        # super-step over a fold, and the visible consequence is exactly this:
        # two public Moderator beats in the ending where there used to be one.
        beats = _moderator_beats_from(rows, winner_idx)
        assert beats == [winner_idx, story_idx], (
            "expected exactly two public Moderator beats in the ending — the "
            f"facts at row {winner_idx} and the story at row {story_idx} — "
            f"but rows {beats} open one. Public log was:\n" + "\n".join(rows)
        )

        # CLAIM 4 — the person reached a finished ending, not a banner. The
        # markup fault (Slice 2) is a live way for this to be false: a story
        # body containing a stray ``[/bold]`` raises through ``_drive``'s
        # ``except``, which skips ``_record_career`` entirely. Claim 2 would
        # already catch that, but this says so in one line.
        assert app._game_over is True
        assert not [row for row in rows if "Error — see" in row], (
            "an error banner replaced part of the ending; public log was:\n"
            + "\n".join(rows)
        )

        await pilot.press("q")


# --------------------------------------------------------------------------
# Criterion 3: the OTHER way a game can end
# --------------------------------------------------------------------------


async def test_the_runaway_ending_shows_the_story_and_no_career_panel(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Day-cap runaway also shows the story — and shows no career panel.

    ``GRAPHIA_MAX_DAYS=1`` makes the FIRST ``night_open`` trip the spec-023
    (Game Length Runaway Safeguard) short-circuit: it sets
    ``winner="runaway"`` and ``route_after_night_open`` skips Night setup
    entirely, so the game reaches ``end_screen`` with no Night kill, no Day
    and no vote. This is criterion 3's "any other way it can end", and it is
    the ending with the LEAST state behind it — nothing the win path's
    ordering depends on exists here.

    THE HALF OF CRITERION 1 THAT CANNOT BE TESTED HERE, stated rather than
    reported as met. ``GraphiaApp._OUTCOME_BY_WINNER`` omits ``"runaway"``,
    so ``_record_career`` returns without writing a panel and the story is the
    last content before the banner. "The story appears before the career
    summary" is therefore vacuously true on this path — there is no career
    summary to be before — so it is NOT asserted. The assertion that can fail
    is the panel's absence, and that is what is asserted.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_MAX_DAYS", "1")
    fake_small(NAME_POOL)

    fake = fake_large(stories=[SCRIPTED_STORY])

    app = GraphiaApp()
    async with app.run_test() as pilot:
        await _boot_and_name(app, pilot)

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_lines(app))
        )
        rows = _public_lines(app)

        assert app._latest_state.get("winner") == "runaway", (
            "scenario did not reach the Day-cap runaway; app mirrored "
            f"winner={app._latest_state.get('winner')!r}"
        )
        assert fake.calls_by_schema[Story] == 1, (
            "expected exactly one Story call through the fake, got "
            f"{fake.calls_by_schema[Story]}"
        )

        winner_idx = _sole_row(
            rows, _first_row_of(ENDGAME_WINNER_RUNAWAY), "the runaway line"
        )
        roster_idx = _sole_row(rows, ENDGAME_HEADER_ROSTER, "the roster reveal")
        persona_idx = _sole_row(rows, ENDGAME_PERSONA_HEADER, "the characters reveal")
        story_idx = _sole_row(rows, STORY_SENTINEL, "the closing story")
        banner_idx = _sole_row(rows, BANNER_MARKER, "the game-over banner")

        # Criteria 1 and 2, on the half of criterion 1 that exists here.
        assert winner_idx < roster_idx < persona_idx < story_idx < banner_idx, (
            "the closing story must appear after the whole facts message and "
            f"before the banner (winner {winner_idx}, roster {roster_idx}, "
            f"characters {persona_idx}, story {story_idx}, banner "
            f"{banner_idx}). Public log was:\n" + "\n".join(rows)
        )

        # The story is its own beat here too.
        beats = _moderator_beats_from(rows, winner_idx)
        assert beats == [winner_idx, story_idx], (
            "expected exactly two public Moderator beats in the ending — the "
            f"facts at row {winner_idx} and the story at row {story_idx} — "
            f"but rows {beats} open one. Public log was:\n" + "\n".join(rows)
        )

        # NO CAREER PANEL — the assertion that replaces the vacuous ordering
        # one. Pre-existing (spec 023) and unchanged by spec 040; pinned here
        # so the docstring's claim is a measured fact rather than a note, and
        # so a later change that starts folding a runaway into the career is
        # noticed by the test that depends on it not doing so.
        assert not [row for row in rows if CAREER_PANEL_MARKER in row], (
            "a Day-cap runaway must show no career panel (spec 023 — Game "
            "Length Runaway Safeguard); public log was:\n" + "\n".join(rows)
        )

        assert app._game_over is True

        await pilot.press("q")
