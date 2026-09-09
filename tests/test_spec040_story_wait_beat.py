"""Spec 040 (The Moderator's Closing Story), Task 7.2 — the waiting beat.

Slice 7 is *the wait is visible, and leaves nothing behind*. Task 7.1 shipped
the beat — ``The Moderator is writing the closing story…`` painted as
``#public-log.border_subtitle``, armed by
:meth:`~graphia.ui.app.GraphiaApp._track_story_wait` when ``end_screen``'s
marked facts message lands, taken back off the frame when the story lands, and
(if the story never comes) overwritten by
:meth:`~graphia.ui.app.GraphiaApp._enter_endgame`'s unconditional exit-hint
assignment — and reported it **entirely unwitnessed**: all nine mutations of it
were zero-red against the whole suite, with a canary run first (deleting
``log.focus()`` gave 10 failed / 178 passed over the same set) so a zero row
meant "uncovered", not "collected nothing". This file is the whole of that
coverage.

What the criterion is, and the false green it admits as worded
--------------------------------------------------------------

The task line asks for three things: the beat **adds no log lines**, nothing
shown during the wait **remains in the finished ending**, and the same holds on
the **no-story** path.

Taken literally, all three are satisfiable by a build with **no beat at all**.
The subtitle never enters ``RichLog.lines`` — the compositor paints it on the
widget's frame, and the assignment leaves ``len(lines)`` unchanged (measured
here as a precondition, not assumed) — so "the hint is absent from the rows"
and "the ending carries no hint" are both green when nothing ever arms. Every
absence assertion below therefore carries a **positive arm-side claim in the
same test**: the hint really was reachable through ``border_subtitle`` while
the story call was in flight, and it really was gone afterwards.

That is not a hypothetical: it reappeared at the integration level while this
file was being written, and was caught by mutation rather than by reasoning. An
earlier draft of the driven test read the caption only at the endgame, and "the
caption was already bare when the endgame was entered" is trivially true of a
build that never arms — so with the arm branch deleted (M1) the driven
``story`` parameter measured **green**, and so did it for M3 and M4. Adding
:func:`_spy_on_the_graph_messages` took M1 from 8 failed / 1 passed to **9
failed**, and M3 and M4 from 2 failed to **3 failed** each. Every number in the
table below is from after that change.

How the armed state is witnessed, and why not by sampling
---------------------------------------------------------

Task 7.1 measured that a fast mocked story cannot be caught mid-wait by
polling: the arm and the clear land inside one event-loop turn and **0 of 83**
samples saw the hint. Of the two approaches it found that do work — a
``time.sleep`` in the fake's ``Story`` branch (it runs on the driver's
``asyncio.to_thread`` worker, so Textual keeps pumping), or **wrapping** the
app's own methods and reading ``border_subtitle`` before delegating — this file
takes the wrappers, in two places, and for reasons beyond cheapness:

* :func:`_spy_on_the_graph_messages` wraps the ``on_message`` callback the
  driver is given, and records the caption **after each graph message has been
  handled**. That is a real user-visible moment rather than a sampled one: the
  driver awaits between messages, so frames render there. It captures the arm
  on the *story* path — the ordinary path, where sampling cannot see it — and
  it needs no sleep.
* :func:`_spy_on_the_endgame` wraps ``_enter_endgame`` and reads the caption
  before delegating. On the no-story path this witnesses the arm and the
  *nothing-left-behind* clause in one value, and it is a **stronger** claim
  than a sample: the facts message reaches the app from ``end_screen``'s
  super-step, which completes before ``moderator_story`` starts, so finding the
  hint still armed once ``drive_graph`` has returned establishes that the beat
  spanned the entire story call.

Neither wrapper suppresses production behaviour — both delegate — so the game
each drives is the real one.

Declared abstentions
--------------------

* **Arming *before* ``_write_public`` is not covered, and is not a gap.**
  Measured in Task 7.1: the arm sits in the same synchronous block as the
  write, so no frame renders between them and the two orders are
  indistinguishable to a person. A test pinning the order would pin a source
  line, not a behaviour.
* **Clearing to ``""`` instead of ``None`` is not covered, for the same
  reason** — Task 7.1 measured the two painting an *identical* bottom border
  row. Hence :func:`_subtitle` and the falsiness assertions: this file pins
  "no caption on the frame", never ``is None``.
* **"The hint is on a rendered frame during the wait" is not re-measured
  here.** Task 7.1 did it with a 1.5s blocking ``Story`` call (245 of 351
  samples carried it); reproducing that in the suite costs a wall-clock sleep
  on every run to re-establish what the two wrappers above already imply
  deterministically.
* **Only one no-story ending is driven, not three.** Task 7.1 witnessed the
  beat surviving to ``_enter_endgame`` on all three (a raising ``Story``
  queue, a runaway with no story, a crash mid-beat). ``end_screen`` marks
  unconditionally and ``_enter_endgame`` clears unconditionally, so the three
  share one code path; the raising-queue variant is driven because it is the
  only one that reaches the *facts* and then declines the story, which is the
  shape the criterion is about.

The traps that produce a FALSE result on correct code
-----------------------------------------------------

1. **A pilot test that starts observing after ``drive_until_public_log_contains``
   returns is observing the ending, not the game.** Task 7.1's first probe
   reported the hint had *never* appeared on any path and read as "the arm
   never fires"; it was the probe. This scenario (1 Mafioso, 2 Citizens, human
   pinned Law-abiding) ends on Night 1 with no human Day turn, so the whole
   mocked game completes inside ``pilot.press("enter")``'s awaits. Nothing here
   polls for the armed state, and both wrappers are installed **before**
   ``run_test()`` — the message wrapper has to be, because ``_drive`` hands
   ``on_message=self._handle_graph_message`` to ``drive_graph`` as a *bound
   method*, so a spy installed on the instance after mounting would race the
   worker that already read it.
2. **``RichLog`` defers every write until its first ``Resize``**, and
   ``len(lines)`` does not move for a deferred write. That would make the
   "adds no log lines" assertion green against the very mutation it exists to
   catch (a ``log.write`` of the hint instead of a subtitle), so
   :func:`_assert_a_write_reaches_lines` writes a canary row and checks it
   lands **before** anything measures a row count.
3. **``conftest.plain_text`` raises ``MarkupError`` on ``[/bold]``** against
   ``Content``-based widgets. Nothing here renders a widget through it; the
   panes are read as ``RichLog.lines`` and the frame is read as a plain
   ``str``.
4. **The live Night-pointing dispatcher the two sibling spec-040 UI files use
   kills the human on about one run in twenty, and the human's death is not
   quiet** — it flips the app into spectator mode, which writes extra rows into
   the pane every row assertion here reads. Measured at **8 failures in 150
   instrumented runs** before this file swapped it for a monkeypatched
   ``_ai_pick_target``, and **0 in 40** after. The mechanism — a ``get_state``
   read that has not caught up with ``assign_roles``, so every player still
   looks Law-abiding — is measured out in :func:`_pin_the_night_kill_to_an_ai`.

The seven mutations, measured
-----------------------------

Each was applied to ``src/``, this file was run, and the mutation reverted. The
batch was canaried first against a mutation known to be red and unrelated to
the beat's plumbing (the arm writing ``border_title`` instead of
``border_subtitle``): **9 failed**, so a low row below is a measurement and not
an empty collection. The named tests are the ones that actually fired, not the
ones predicted to — three rows differed from the prediction, and all three
differences are recorded here rather than reconciled away.

* **M1** — the arm branch of ``_track_story_wait`` deleted; no beat ever, and
  the wait reads as a hang. **9 failed** — every test in the file.
* **M2** — the clear-on-story branch deleted; the hint sits on the frame with
  the story already above it. **2 failed**:
  :func:`test_the_story_message_takes_the_beat_back_off_the_frame` and the
  driven ``story`` parameter. The ``no_story`` parameter abstains **by
  construction**: with no story arriving there is no clear to delete, and
  ``_enter_endgame`` is what takes the beat off that path's frame.
* **M3** — the ``_track_story_wait`` call deleted from
  ``_handle_graph_message``. **3 failed**:
  :func:`test_the_facts_message_arms_the_beat_through_the_message_handler` and
  **both** driven parameters.
* **M4** — ``ENDGAME_FACTS_KWARG`` dropped from ``end_screen``'s message, so
  nothing arms. **3 failed**:
  :func:`test_end_screens_facts_message_arms_the_beat` and **both** driven
  parameters.
* **M5** — ``log.write(Text(STORY_WAIT_HINT))`` instead of the subtitle: a
  **permanent row** between the reveal and the story, the design the task line
  forbids. **9 failed** — every test in the file.
* **M6** — an ``is None`` guard on ``_enter_endgame``'s subtitle assignment,
  stranding the hint on a finished game's frame. **1 failed**: the driven
  ``no_story`` parameter, and only it. Everything else abstains **by
  construction** — on any path where the story arrives the caption is already
  bare when ``_enter_endgame`` runs, so the guard's condition holds and the
  mutation is invisible. That is the measured reason M6 was zero-red against
  all 2743 tests: every one of the four endings in
  ``tests/test_spec040_exit_contract.py`` serves a scripted story, so no UI
  test in the suite drove a no-story ending before this file.
* **M7** — arming on ``isinstance(msg, SystemMessage)`` instead of the marker,
  which arms mid-Day on every Day-round recap. **4 failed**:
  :func:`test_only_the_flagged_facts_message_arms_the_beat` on its two
  ``SystemMessage`` parameters, plus
  :func:`test_the_story_message_takes_the_beat_back_off_the_frame` and the
  driven ``story`` parameter — because the closing story is *also* a public
  ``SystemMessage``, so a literal M7 **re-arms** the beat at the very point it
  should clear it. That second effect was not predicted and is the sharpest
  argument against the type test: it does not merely add false beats, it
  removes the true clear. The ``ai_speech`` parameter stays green by
  construction and is not a gap — an ``AIMessage`` is not what M7 selects on;
  it is there as an M1 and M5 witness.

Two mutations are deliberately absent, on Task 7.1's measurements rather than
on judgement: arming before ``_write_public`` (no frame renders between the two
statements) and clearing to ``""`` rather than ``None`` (an identical painted
border row). Both are argued in the abstentions above.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import pytest
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from rich.text import Text
from textual.widgets import Input, RichLog

import graphia.ui.app as app_module
from graphia.llm import Diary, Story
from graphia.nodes.day import render_day_round_recap
from graphia.nodes.endgame import end_screen
from graphia.prompts import ENDGAME_WINNER_MAFIA
from graphia.state import CLOSING_STORY_KWARG, ENDGAME_FACTS_KWARG, PlayerState
from graphia.ui.app import EXIT_HINT, STORY_WAIT_HINT, GraphiaApp

from conftest import drive_until_public_log_contains, endgame_facts_message

# Reused rather than re-derived, exactly as ``test_spec040_exit_contract.py``
# does: ``ENDGAME_WINNER_MAFIA`` wraps at this pane's 76 content columns, so a
# verbatim match finds zero rows.
from test_spec040_story_ui_order import _first_row_of

HUMAN_NAME = "Alice"
NAME_POOL = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav", "Noor", "Kai"]

# No square brackets: bracketed prose in a message body was a live crash on
# this path until Slice 2, and a crash would fail these tests for a reason
# that has nothing to do with the waiting beat.
STORY_SENTINEL = "Candlewright"
SCRIPTED_STORY = Story(
    text=(
        "Candlewright kept a tally that nobody asked to see.\n\n"
        "By the last night it was the only honest thing left at the table."
    )
)

# The no-story path, reached the way ``tests/conftest.py`` says to reach it: a
# scripted ``Exception`` in the ``Story`` queue still **books the call**, which
# is what keeps "the story call failed" distinct from "the story call was never
# made". Leaving the queue empty would instead serve the marker-bearing
# unscripted default, and the ending would carry a story after all.
STORY_FAILURE = RuntimeError("the closing story call failed for this game")

# Written into ``#public-log`` before any row count is taken — trap 2.
CANARY_ROW = "wait-beat-canary-row"

# Hand-built endgame state for the node-level facts assertion. Copied in shape
# from ``test_spec040_story_node.DIRECT_STATE``: a finished Mafia win with one
# night kill, which is all ``end_screen`` needs to render a full reveal.
DIRECT_PLAYERS: dict[str, PlayerState] = {
    "p-human": PlayerState(
        id="p-human", name=HUMAN_NAME, role="law_abiding", is_human=True
    ),
    "p-ai": PlayerState(
        id="p-ai", name="Ivy", role="law_abiding", is_human=False, is_alive=False
    ),
    "p-mafia": PlayerState(id="p-mafia", name="Marco", role="mafia", is_human=False),
}

DIRECT_STATE: dict[str, Any] = {
    "players": DIRECT_PLAYERS,
    "kill_log": [{"cycle": 1, "name": "Ivy", "cause": "night", "role": None}],
    "winner": "mafia",
    "messages": [],
}


# --------------------------------------------------------------------------
# Reading the pane and its frame
# --------------------------------------------------------------------------


def _subtitle(log: RichLog) -> str:
    """``log.border_subtitle`` as a string, never raising on a bare frame.

    ``border_subtitle`` is ``None`` before anything sets it and ``None`` again
    after the story clears it (measured on Textual 8.2.4; a ``str`` in
    between), so ``needle in log.border_subtitle`` would raise ``TypeError`` on
    exactly the states these tests are about. Collapsing the empty cases to
    ``""`` is also what keeps this file off the ``None``-versus-``""``
    distinction it declares an abstention on: both are falsy, and Task 7.1
    measured them painting an identical border row.
    """
    return log.border_subtitle or ""


def _public_rows(app: GraphiaApp) -> list[str]:
    """Every rendered row of ``#public-log``, in write order, plain and stripped.

    One entry per *rendered* row, so a wrapped line is several entries.
    Stripped because the pane carries ``padding: 0 1`` and no assertion here
    should depend on whether that padding reaches ``Strip.text``.
    """
    rows: list[str] = []
    for line in app.query_one("#public-log", RichLog).lines:
        text = getattr(line, "text", None)
        rows.append((text if text is not None else str(line)).strip())
    return rows


def _rows_carrying(rows: list[str], needle: str) -> list[int]:
    return [i for i, row in enumerate(rows) if needle in row]


def _assert_a_write_reaches_lines(log: RichLog) -> None:
    """Prove a ``write`` on this pane lands in ``lines`` *right now* — trap 2.

    ``RichLog`` defers every write until its first ``Resize``, and a deferred
    write does not move ``len(lines)``. Every row-count assertion below exists
    to catch a hint written as a **row** instead of a caption, and on an unsized
    pane that mutation would move no count at all — the assertion would be green
    against the one thing it is for. So a canary row is written and checked
    before any count is taken.
    """
    before = len(log.lines)
    log.write(Text(CANARY_ROW))
    assert len(log.lines) > before, (
        "#public-log swallowed a write without adding a row, so it has not "
        f"been sized yet (size={log.size!r}); every row-count assertion in "
        "this file would be vacuous. Pause the pilot before measuring."
    )


# --------------------------------------------------------------------------
# A mounted pane with no game behind it
# --------------------------------------------------------------------------


@asynccontextmanager
async def _mounted_pane(
    monkeypatch: pytest.MonkeyPatch,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
) -> AsyncIterator[tuple[GraphiaApp, Any, RichLog]]:
    """Mount ``GraphiaApp`` with the driver parked, and yield app/pilot/pane.

    ``_track_story_wait`` is a pure function of one message and the widget it
    queries, so the sharp instrument for it is a direct call on a real mounted
    pane — no game, no trajectory, no timing. ``drive_graph`` is replaced with a
    coroutine that parks on an ``asyncio.Event`` rather than one that returns,
    because a driver that returns runs :meth:`GraphiaApp._enter_endgame` and
    stamps the exit hint into the very slot under test. The event is set on the
    way out so the worker is not left blocking teardown.
    """
    fake_small(NAME_POOL)
    fake_large()
    parked = asyncio.Event()

    async def _park(**kwargs: Any) -> None:
        await parked.wait()

    monkeypatch.setattr("graphia.ui.app.drive_graph", _park)

    app = GraphiaApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#public-log", RichLog)
        _assert_a_write_reaches_lines(log)
        try:
            yield app, pilot, log
        finally:
            parked.set()
            app.exit()


def _facts_message() -> SystemMessage:
    """A minimal message carrying the marker the beat arms on.

    Hand-built on purpose for the tests whose subject is the *reader*: they must
    fail when ``_track_story_wait`` stops arming, not when ``end_screen`` stops
    marking. :func:`test_end_screens_facts_message_arms_the_beat` owns the other
    half and builds its message from the production node.
    """
    return SystemMessage(
        content="THE WINNER IS the Mafia.",
        additional_kwargs={ENDGAME_FACTS_KWARG: True},
    )


def _story_message() -> SystemMessage:
    return SystemMessage(
        content=SCRIPTED_STORY.text, additional_kwargs={CLOSING_STORY_KWARG: True}
    )


# --------------------------------------------------------------------------
# The beat costs no rows, and comes back off the frame
# --------------------------------------------------------------------------


async def test_arming_the_beat_paints_the_frame_and_adds_no_log_lines(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The facts arm a frame caption and cost ``#public-log`` zero rows.

    The direct, non-flaky witness for *adds no log lines*: ``len(lines)`` across
    one call, rather than a comparison of ending row counts (which Task 7.1
    measured at 37 / 39 / 40 across scenarios, so a cross-scenario count would
    be noise). The row count and the frame caption are asserted **together**,
    because either alone is green against a build with no beat: a caption costs
    no rows, so an app that never arms passes the count.
    """
    async with _mounted_pane(monkeypatch, fake_small, fake_large) as (app, _, log):
        assert _subtitle(log) == "", (
            "#public-log should carry no border subtitle before the beat is "
            f"armed; found {log.border_subtitle!r}"
        )
        rows_before = len(log.lines)

        app._track_story_wait(_facts_message())

        assert STORY_WAIT_HINT in _subtitle(log), (
            "the facts message must arm the waiting beat on "
            f"#public-log.border_subtitle; found {log.border_subtitle!r}"
        )
        assert len(log.lines) == rows_before, (
            "arming the waiting beat must add NO rows to #public-log — a row "
            "can never be taken back (RichLog has no per-line removal), which "
            "is the whole reason the hint is a border subtitle. Rows went "
            f"{rows_before} -> {len(log.lines)}:\n" + "\n".join(_public_rows(app))
        )
        assert _rows_carrying(_public_rows(app), STORY_WAIT_HINT) == [], (
            "the waiting hint must not be written into #public-log's rows"
        )


async def test_the_story_message_takes_the_beat_back_off_the_frame(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The story's arrival clears the caption, and costs no rows either.

    The earlier of the two clears, and the one that is *not* redundant with
    :meth:`GraphiaApp._enter_endgame`'s unconditional assignment: the story
    write is dispatched from the driver's ``await`` loop, so frames render
    between it and the endgame. Without this branch one of them would say "the
    Moderator is writing the closing story" with the story already on screen
    above it.

    Asserts **falsiness**, not ``is None`` — see the declared abstentions.
    """
    async with _mounted_pane(monkeypatch, fake_small, fake_large) as (app, _, log):
        app._track_story_wait(_facts_message())
        assert STORY_WAIT_HINT in _subtitle(log), (
            "precondition: the beat must be armed before the clear can be "
            f"measured; found {log.border_subtitle!r}"
        )
        rows_before = len(log.lines)

        app._track_story_wait(_story_message())

        assert not _subtitle(log), (
            "the closing story's arrival must take the waiting hint back off "
            f"#public-log's frame; found {log.border_subtitle!r}"
        )
        assert len(log.lines) == rows_before, (
            "clearing the waiting beat must add no rows to #public-log; rows "
            f"went {rows_before} -> {len(log.lines)}"
        )


# --------------------------------------------------------------------------
# Nothing else arms it
# --------------------------------------------------------------------------


def _non_arming_messages() -> list[tuple[str, BaseMessage]]:
    """Public messages that reach ``_track_story_wait`` and must NOT arm it.

    The Day-round recap is built by the **production** function rather than
    hand-typed, because it is the message that makes "any public
    ``SystemMessage``" actively harmful rather than merely imprecise: it is
    emitted once per Day round, so arming on the type would show "the Moderator
    is writing the closing story" several times a game, mid-Day, on a story that
    is not coming.

    Private messages are absent deliberately: ``_handle_graph_message`` returns
    before ``_track_story_wait`` for anything carrying ``private_to``
    (``src/graphia/ui/app.py``), so a private message is not an input this
    method can receive and a parameter for one would assert about an unreachable
    state.
    """
    return [
        ("day_round_recap", render_day_round_recap({}, day_round=1)),
        ("unflagged_moderator_line", SystemMessage(content="The Day begins.")),
        ("ai_speech", AIMessage(content="I am watching carefully.")),
    ]


@pytest.mark.parametrize(
    "message",
    [m for _, m in _non_arming_messages()],
    ids=[name for name, _ in _non_arming_messages()],
)
async def test_only_the_flagged_facts_message_arms_the_beat(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    message: BaseMessage,
) -> None:
    """Selection is on the marker kwarg, never on the message's type.

    Paired with its positive control in the same test — the marked facts message
    *does* arm the beat on the same pane, immediately afterwards — so "nothing
    armed" cannot pass because the arm is broken everywhere.
    """
    async with _mounted_pane(monkeypatch, fake_small, fake_large) as (app, _, log):
        app._track_story_wait(message)

        assert not _subtitle(log), (
            f"a {type(message).__name__} with no {ENDGAME_FACTS_KWARG!r} flag "
            "must not arm the waiting beat — the beat promises a story is "
            "coming, and for this message none is. Found "
            f"{log.border_subtitle!r} after:\n{message.content!r}"
        )

        # Positive control, forced to come second: the arm works on this pane
        # at this moment, so the absence above is about the message.
        app._track_story_wait(_facts_message())
        assert STORY_WAIT_HINT in _subtitle(log), (
            "control: the marked facts message must arm the beat on this same "
            f"pane; found {log.border_subtitle!r}"
        )


# --------------------------------------------------------------------------
# The producer and the wiring
# --------------------------------------------------------------------------


async def test_end_screens_facts_message_arms_the_beat(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``end_screen``'s own message — selected on content — arms the beat.

    The two ends of the marker joined without a hand-typed flag anywhere: the
    message comes from the production node, is selected by
    ``conftest.endgame_facts_message`` on the roster header (so this test means
    "the message a person reads the reveal in", not "the message at index N"),
    and is handed to the production reader.

    Both assertions matter and neither implies the other. The first is what
    turns red when ``end_screen`` stops marking — the mutation Task 7.1 measured
    as zero-red across all 2743 tests. The second is what turns red when the
    reader stops arming.
    """
    update = end_screen(DIRECT_STATE)
    facts = endgame_facts_message(update["messages"])

    assert (facts.additional_kwargs or {}).get(ENDGAME_FACTS_KWARG) is True, (
        "end_screen's facts message must carry "
        f"{ENDGAME_FACTS_KWARG!r} — it is the only thing that tells the UI a "
        "story call is now in flight, and without it the wait shows no sign of "
        f"activity at all. additional_kwargs={facts.additional_kwargs!r}"
    )

    async with _mounted_pane(monkeypatch, fake_small, fake_large) as (app, _, log):
        app._track_story_wait(facts)
        assert STORY_WAIT_HINT in _subtitle(log), (
            "the real facts message from end_screen must arm the waiting beat; "
            f"found {log.border_subtitle!r}"
        )


async def test_the_facts_message_arms_the_beat_through_the_message_handler(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_handle_graph_message`` is wired to the tracker, not just to the pane.

    The witness for the *call site*: every other unit test here calls
    ``_track_story_wait`` directly and stays green with the call deleted from
    the one method graph messages actually arrive through.

    The facts row landing on screen is the anti-vacuity control. With the
    tracker call removed the handler still writes the row, so "the row is there
    and the frame is bare" is a failure that names the missing call rather than
    a handler that did nothing.
    """
    facts = _facts_message()
    async with _mounted_pane(monkeypatch, fake_small, fake_large) as (app, _, log):
        rows_before = len(log.lines)

        await app._handle_graph_message(facts)

        rows = _public_rows(app)
        assert _rows_carrying(rows, "THE WINNER IS"), (
            "control: _handle_graph_message must have written the facts row, "
            "otherwise the frame assertion below is measuring a handler that "
            "did nothing. Rows were:\n" + "\n".join(rows)
        )
        assert STORY_WAIT_HINT in _subtitle(log), (
            "a facts message arriving through _handle_graph_message must arm "
            f"the waiting beat; found {log.border_subtitle!r}"
        )
        assert _rows_carrying(rows, STORY_WAIT_HINT) == [], (
            "the hint must reach the frame, not the rows; the facts write "
            f"itself accounts for rows {rows_before} -> {len(log.lines)}"
        )


# --------------------------------------------------------------------------
# Driving a whole game, with and without a story
# --------------------------------------------------------------------------


async def _boot_and_name(app: GraphiaApp, pilot: Any, name: str = HUMAN_NAME) -> None:
    """Wait for the drive worker to build the graph, then submit the name.

    The name is typed key by key, so it takes the path a person's keyboard
    takes.
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


def _pin_the_night_kill_to_an_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the Night take an AI Citizen, deterministically.

    **Not the live-dispatcher trick the two sibling spec-040 UI files use, and
    the difference is a measured defect rather than a preference.** Those files
    replace the fake's ``_invoke`` so a ``Pointing`` call resolves its target by
    reading ``graph.get_state(run_config)`` at invoke time — the only way to
    name a real player id, since the roster's UUIDs exist only once
    ``assign_roles`` has run. Two things go wrong with it, and the second was
    **diagnosed rather than reasoned**, because the first guess about it was
    wrong.

    * It has to be installed after the graph exists but before
      ``mafia_pointing`` runs, and ``pilot.press("enter")`` yields to the driver
      worker — on this Night-1 scenario the whole mocked game can finish inside
      that press's awaits (trap 1). Installing it before the name is submitted
      closes that half.
    * **Installed in time it still kills the human on roughly one run in
      twenty, because the state it reads is a roster in which nobody is Mafia
      yet.** Instrumented over 150 runs, 8 of which lost the human, and in
      every one of the 8 the roster logged at *both* ``Pointing`` attempts read
      ``[('Alice', 'law_abiding', ...), ('Ivy', 'law_abiding', ...), ('Marco',
      'law_abiding', ...)]`` while the finished game's roster read ``Ivy ->
      'mafia'``. ``PlayerState.role`` defaults to ``"law_abiding"``, so a
      ``get_state`` that has not yet caught up with ``assign_roles`` hands back
      a roster where **every** player looks Law-abiding — and the dispatcher's
      ``targets[0]`` is then whichever non-human comes first, which is the
      **Mafioso** about half the time. Its id is not in ``mafia_pointing``'s
      real ``valid_ids``, so ``_ai_pick_target`` rejects it, retries, gets the
      same answer, rejects it again and takes its ``random.choice`` over the
      genuinely alive Law-abiding — **which includes the human**. (My first
      guess, that ``get_state`` was raising into the node's broad ``except``,
      was measured false: it never raised and never returned an empty roster.)

    So the mechanical outcome is pinned the way the project pins mechanical
    outcomes (architecture section 6 / ADR 006): by monkeypatching the helper
    that uses the RNG, not by racing a checkpoint read. ``_ai_pick_target`` is
    handed ``mafia_pointing``'s OWN ``alive_law_abiding`` list, so returning the
    first non-human from it is a valid id by construction and the human always
    survives to read the ending — a dead human flips the app into spectator
    mode, which writes extra rows to the public pane. Measured **0 failures in
    40 runs** of the whole file afterwards.

    This is reported, not fixed, for ``tests/test_spec040_story_ui_order.py``
    and ``tests/test_spec040_exit_contract.py``: their assertions happen to
    survive a dead human (the ending is a Mafia parity win either way), so the
    defect costs them nothing today and the fix is not this task's.
    """

    def _pick_the_first_non_human(
        alive_law_abiding: list[Any],
        mafia: Any,
        prior_picks: str = "",
        **kwargs: Any,
    ) -> str:
        for player in alive_law_abiding:
            if not player.is_human:
                return player.id
        raise AssertionError(
            "this scenario must always leave an AI Law-abiding Citizen for the "
            f"Night to take; candidates were {alive_law_abiding!r}"
        )

    monkeypatch.setattr(
        "graphia.nodes.night._ai_pick_target", _pick_the_first_non_human
    )


def _spy_on_the_graph_messages(
    monkeypatch: pytest.MonkeyPatch, app: GraphiaApp
) -> list[tuple[bool, bool, str]]:
    """Record ``#public-log``'s caption after each graph message is handled.

    Returns a growing list of ``(carries_facts_marker, carries_story_marker,
    caption_after)``.

    **Wrapped at ``drive_graph``, not on the app instance, and that is trap 1
    rather than fastidiousness.** ``GraphiaApp._drive`` passes
    ``on_message=self._handle_graph_message`` — a *bound method*, read once —
    so replacing the attribute after ``run_test()`` has mounted the app races
    the worker that has already read it, and on this scenario the worker wins.
    Wrapping the module-level ``drive_graph`` that ``_drive`` looks up means the
    substitution is in place before the app is even started, and the real driver
    still runs the real game.

    What this captures is a **real user-visible moment**, not a sample: the
    driver awaits between messages, so frames render between the facts write and
    the story write. It is the arm-side witness on the ordinary story path,
    where Task 7.1 measured that polling catches the armed state 0 times in 83.
    """
    seen: list[tuple[bool, bool, str]] = []
    real_drive_graph = app_module.drive_graph

    async def _wrapped(*, on_message: Callable[..., Any], **kwargs: Any) -> Any:
        async def _spying(msg: BaseMessage) -> Any:
            result = await on_message(msg)
            extra = getattr(msg, "additional_kwargs", {}) or {}
            log = app.query_one("#public-log", RichLog)
            seen.append(
                (
                    bool(extra.get(ENDGAME_FACTS_KWARG)),
                    bool(extra.get(CLOSING_STORY_KWARG)),
                    _subtitle(log),
                )
            )
            return result

        return await real_drive_graph(on_message=_spying, **kwargs)

    monkeypatch.setattr("graphia.ui.app.drive_graph", _wrapped)
    return seen


def _spy_on_the_endgame(app: GraphiaApp) -> list[str]:
    """Record ``#public-log``'s caption as each ending is entered.

    ``_enter_endgame`` is invoked as ``self._enter_endgame(log)`` — a fresh
    attribute lookup every time — so an instance attribute is enough here and
    the ``drive_graph``-level indirection above is not needed. Reads the caption
    and delegates, so the production assignment still runs and the ending is the
    real one.
    """
    seen: list[str] = []
    original = app._enter_endgame

    def _spy(log: RichLog) -> None:
        seen.append(_subtitle(log))
        original(log)

    app._enter_endgame = _spy  # type: ignore[method-assign]
    return seen


def _sole_index(
    entries: list[tuple[bool, bool, str]], slot: int, what: str
) -> int:
    """Index of the ONE captured message whose flag at ``slot`` is set."""
    hits = [i for i, entry in enumerate(entries) if entry[slot]]
    assert len(hits) == 1, (
        f"expected exactly ONE {what} among the {len(entries)} graph messages "
        f"this game handled; found {len(hits)}. Captures were {entries!r}"
    )
    return hits[0]


@pytest.mark.parametrize("variant", ("story", "no_story"))
async def test_the_wait_beat_leaves_nothing_behind_on_either_ending(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    """The beat spans the story call and the finished ending keeps no trace.

    THE SHORTEST GAME THAT REACHES A REAL OUTCOME, driven twice: one Mafioso and
    two Citizens (the human one of them, pinned Law-abiding via
    ``GRAPHIA_ROLE`` so no ``point`` interrupt fires). The Night takes the AI
    Citizen, leaving parity, so ``check_win_night`` routes straight to
    ``end_screen`` and no Day ever runs.

    The two parameters differ only in the ``Story`` queue, and that difference
    is the point:

    * ``story`` — the story arrives, so ``_track_story_wait``'s own clear runs
      and the caption is bare again by the time the endgame is entered;
    * ``no_story`` — the ``Story`` call **raises**, so the beat is still armed
      when ``drive_graph`` returns and the only thing that takes it off the
      frame is ``_enter_endgame``'s unconditional assignment. This is the
      parameter the suite did not have: every one of the four endings in
      ``tests/test_spec040_exit_contract.py`` serves a scripted story, which is
      exactly why guarding that assignment on ``if border_subtitle is None`` was
      measured green against all 2743 tests.

    CLAIM 2 is the arm-side witness and the reason
    :func:`_spy_on_the_graph_messages` exists: measured, the endgame capture
    alone (CLAIM 3) leaves the ``story`` parameter green against a build that
    never arms at all, because "the caption was already bare" is trivially true
    when nothing ever set it.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
    fake_small(NAME_POOL)

    # ``diaries=`` is scripted defensively: the Day->Night hinge fans
    # ``day_diary`` out for any game that survives its first Day, and no
    # assertion here should depend on the scenario ending exactly when it means
    # to.
    fake = fake_large(
        stories=[SCRIPTED_STORY if variant == "story" else STORY_FAILURE],
        diaries=[Diary(entry="Day one is done and I trust no one yet.")],
    )

    _pin_the_night_kill_to_an_ai(monkeypatch)

    app = GraphiaApp()
    # Both spies are installed BEFORE ``run_test()`` — trap 1.
    handled = _spy_on_the_graph_messages(monkeypatch, app)
    entered_with = _spy_on_the_endgame(app)

    async with app.run_test() as pilot:
        await _boot_and_name(app, pilot)

        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_rows(app))
        )
        rows = _public_rows(app)
        log = app.query_one("#public-log", RichLog)

        # CLAIM 0 — the story call really happened on BOTH paths, so
        # ``no_story`` is a story that was attempted and failed rather than a
        # fake that was never reached (which would be the same absence on screen
        # and would mean nothing about the beat).
        assert fake.calls_by_schema[Story] == 1, (
            "expected exactly one Story call through the fake on the "
            f"{variant!r} path, got {fake.calls_by_schema[Story]}"
        )
        assert app._latest_state.get("winner") == "mafia", (
            "the shortest-win scenario should end in a Mafia parity win; "
            f"winner={app._latest_state.get('winner')!r}"
        )
        # The ending really is on screen, so the row-absence assertion in
        # CLAIM 4 is measured against a populated pane (trap 2).
        assert _rows_carrying(rows, _first_row_of(ENDGAME_WINNER_MAFIA)), (
            "the winner line never reached #public-log, so nothing here is "
            "measuring an ending. Rows were:\n" + "\n".join(rows)
        )

        # CLAIM 1 — the human survived, so no spectator rows muddy the pane.
        assert app._spectator is False, (
            "the human should survive this scenario; spectator mode writes "
            "extra rows the row assertions below do not expect"
        )

        # CLAIM 2 — the beat was armed on the frame from the moment the facts
        # were handled, which is the whole of the story call's window.
        facts_at = _sole_index(handled, 0, "facts message")
        assert STORY_WAIT_HINT in handled[facts_at][2], (
            "the facts message must leave the waiting beat on #public-log's "
            "frame — the story call is dispatched next and this caption is the "
            "only sign of activity the person gets. Caption after the facts "
            f"was {handled[facts_at][2]!r}"
        )
        if variant == "story":
            story_at = _sole_index(handled, 1, "closing story message")
            assert story_at > facts_at, (
                "the facts must be handled before the story; captures were "
                f"{handled!r}"
            )
            assert not handled[story_at][2], (
                "the story's arrival must take the waiting hint off the frame "
                "there and then — frames render between this write and the "
                "endgame, and one of them would otherwise say the story is "
                f"being written with the story on screen above it. Caption "
                f"after the story was {handled[story_at][2]!r}"
            )
        else:
            assert [i for i, e in enumerate(handled) if e[1]] == [], (
                "no closing story should have been handled when the Story call "
                f"raised; captures were {handled!r}"
            )

        # CLAIM 3 — what the beat looked like at the instant the game became
        # over, captured before ``_enter_endgame`` overwrote the slot.
        assert len(entered_with) == 1, (
            "_enter_endgame should run exactly once on this path; it ran "
            f"{len(entered_with)} times, captions {entered_with!r}"
        )
        if variant == "story":
            assert not entered_with[0], (
                "the story arrived, so the beat should already have been "
                f"cleared before the endgame was entered. Caption was "
                f"{entered_with[0]!r}"
            )
            assert _rows_carrying(rows, STORY_SENTINEL), (
                "control: the scripted story should be on screen on this path"
            )
        else:
            assert STORY_WAIT_HINT in entered_with[0], (
                "the story never came, so the beat should still have been armed "
                "when the endgame was entered — that is the state "
                "_enter_endgame's unconditional subtitle assignment exists to "
                f"clear. Caption was {entered_with[0]!r}"
            )
            assert not _rows_carrying(rows, STORY_SENTINEL), (
                "no story should be on screen when the Story call raised; rows "
                "were:\n" + "\n".join(rows)
            )

        # CLAIM 4 — nothing shown during the wait remains in the finished
        # ending: not on the frame, and not in the rows.
        assert STORY_WAIT_HINT not in _subtitle(log), (
            "the finished ending must carry no trace of the waiting beat on "
            f"#public-log's frame; found {log.border_subtitle!r}"
        )
        assert EXIT_HINT in _subtitle(log), (
            "the finished ending must carry the exit hint on the frame; found "
            f"{log.border_subtitle!r}"
        )
        assert _rows_carrying(rows, STORY_WAIT_HINT) == [], (
            "the waiting hint must never have been written as a row — a row "
            "cannot be taken back, so it would sit between the reveal and the "
            "story in the finished ending forever. Rows were:\n"
            + "\n".join(rows)
        )
