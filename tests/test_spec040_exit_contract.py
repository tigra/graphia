"""Spec 040, Slice 6: the person can read back through the ending before leaving.

Task 6.6. The five production tasks of this slice (6.1-6.4 and 6.7) landed with
a **fourteen-row mutation table of which twelve were zero-red** — deleting
``log.focus()``, deleting ``log.auto_scroll = False``, capping ``max_lines``,
restoring the exit-on-any-key ``on_key``, dropping either ``SkipAction`` guard,
deleting ``border_subtitle``, reverting all three markup fixes and rewording
the hint all turned **nothing** in the suite red. This file is the coverage
that makes the slice mean something; Task 6.5 repointed the five hollow
end-of-game presses that had been standing in for it — plus two more of the
same shape that no count had included.

What is under test
------------------

``GraphiaApp._enter_endgame`` (``src/graphia/ui/app.py``) — the one place a
game becomes over and the one place the reading affordances are set — plus
``action_leave``, ``action_request_quit`` and the two markup fixes on the
crash paths.

The contract, in the words of the functional spec: at game-over ``#public-log``
is focused and frozen, the winner line is still reachable, the hint naming the
leaving key is pinned to the frame, ``q`` and ``escape`` leave, and ``x``,
``space``, ``up`` and ``pageup`` do **not**.

Three measured traps, all of which produce a FALSE result on correct code
--------------------------------------------------------------------------

1. **The runaway winner line never occupies a single row.**
   ``ENDGAME_WINNER_RUNAWAY`` is 84 characters and ``#public-log`` under
   ``run_test()``'s default 80x24 measures **76** content columns (round
   border plus ``padding: 0 1``), so a verbatim single-row match finds zero
   hits. Every winner-line anchor here goes through
   :func:`test_spec040_story_ui_order._first_row_of`, which derives the
   fragment from the imported production constant. (Tech spec section 2.11
   ("The UI — the exit contract, the focus fix, and two faults") says "120
   lines in a 14-row viewport, ``max_scroll_y=106``"; that does not describe this
   pane: it settles at 76 columns and a **10-11** row viewport. Measured here:
   a runaway ending is ~60 rows with ``max_scroll_y`` 50.)

2. **``RichLog`` defers every write until its first ``Resize``**, and a
   deferred write replays through ``write()`` re-reading ``auto_scroll`` at
   *replay* time. A game driven entirely before the pane was ever sized
   replays every row with auto-scroll already off and sits at row 0 with the
   ending below the fold — which a "``home`` reaches the winner line"
   assertion would pass for the wrong reason. Real play cannot hit it; a pilot
   test that never pauses can. Every scrolling test here therefore asserts
   ``scroll_offset.y == max_scroll_y > 0`` as a **precondition**: the pane was
   sized, it really did auto-scroll to the true bottom, and there is somewhere
   to scroll back from.

3. **``conftest.plain_text`` makes a ``FailureModal`` escaping test worse than
   vacuous.** ``Static(Content(...)).render()`` returns a
   :class:`textual.content.Content`, not a rich ``Text``, so ``plain_text``
   falls through to ``Text.from_markup(str(rendered)).plain`` — which *raises*
   ``MarkupError`` on ``[/bold]`` and silently *deletes* ``[sic]``. A test
   built on it would fail from the helper, on correct production code, and
   read as the fault still being present. :func:`_static_plain` below reads
   ``Content.plain`` instead.

Anti-vacuity
------------

This spec has been bitten repeatedly by an assertion that passed because the
thing it looked for was never in the fixture. Every absence assertion in this
file carries its positive control in the same test:

* "``x`` does not leave" is paired with "``q`` does" — proving a keypress can
  exit this app at that moment. The control is forced to come second: it ends
  the app.
* "the bracketed path/message survived" is paired with a local
  ``Text.from_markup`` call on the *same* string showing the segment really is
  markup-hostile — so the fixture cannot be a string markup would have left
  alone anyway.
* "the winner line is on screen after ``home``" is paired with "it was off
  screen before ``home``".
* "the pinned hint is still on screen after ``home``" is paired with "the
  banner row that also carried it is not".

Declared abstentions
--------------------

* **The mouse wheel cannot fail.** Scroll events route to the widget under the
  pointer, so ``#public-log`` scrolls whether or not it is focused and a wheel
  event never reached the old ``on_key``. The wheel was the one way to read
  back through an ending even *before* this slice, so
  :func:`test_the_mouse_wheel_scrolls_the_ending_pane` is a documented
  regression floor, not coverage of anything Slice 6 added. (``Pilot`` on
  Textual 8.2.4 has no wheel helper — no ``scroll_up`` / ``scroll_down`` — so
  it posts :class:`textual.events.MouseScrollUp` directly.)
* **Making the ``q`` binding ``priority=True`` is not a behaviour change on
  this version, so nothing here can catch it on its own.** Measured: with
  ``priority=True`` the App binding fires first, ``action_leave`` raises
  ``SkipAction`` because the game is not over, the keystroke is handed back to
  the binding chain and the focused ``Input`` inserts the character anyway
  (``'quq'``). The two guards make the flag unobservable, so the fear it was
  listed for — that ``q`` would stop being typeable, breaking every name and
  speech containing one, which is why spec 003 unbound it — is already
  answered by ``SkipAction``.
  :func:`test_q_is_a_character_mid_game_and_the_leaving_key_at_the_end` pins
  that behaviour regardless, and it *does* fail once the flag is combined with
  a dropped ``_game_over`` guard, or if a guard ever becomes a bare ``return``
  instead of ``SkipAction`` (the dispatcher reads a ``return`` as "handled").
* **``action_leave``'s ``ModalScreen`` -> ``SkipAction`` guard is currently
  unreachable, so removing it alone changes nothing.** Measured: ``q`` is a
  *non-priority* App binding and a :class:`~textual.screen.ModalScreen` is
  ``is_modal``, which stops bindings propagating past it — so with
  :class:`~graphia.ui.failure_modal.FailureModal` on screen the App binding
  never fires and the guard never runs. It is not dead weight, though: it is
  what makes the ``priority=True`` change *safe*. With the flag set and the
  guard removed, ``q`` under the modal exits and the player loses the only
  pointer to the server-side log of a remote crash — measured, and
  :func:`test_q_under_the_failure_modal_is_inert_and_the_modal_survives`
  turns red on exactly that pair. The production docstring's "without it ``q``
  fires from under FailureModal" is therefore true of the *combination*, not
  of the guard's removal by itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import pytest
from rich.errors import MarkupError
from rich.text import Text
from textual import events
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

from graphia.llm import Ballot, DayAction, Diary, Pointing, Story
from graphia.prompts import ENDGAME_WINNER_MAFIA, ENDGAME_WINNER_RUNAWAY
from graphia.ui.app import EXIT_HINT, GraphiaApp
from graphia.ui.failure_modal import FailureModal
from graphia.ui.quit_modal import QuitModal

from conftest import drive_until_public_log_contains

# Reused rather than re-derived — see trap 1 in the module docstring. This is
# the helper Task 1.9 shipped precisely so no test in this spec hand-types a
# fragment of a production string that wraps.
from test_spec040_story_ui_order import _first_row_of

HUMAN_NAME = "Alice"
NAME_POOL = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav", "Noor", "Kai"]

# No square brackets: bracketed prose in a *message body* is a separate fault
# (Slice 2), and a crash there would fail these tests for the wrong reason.
SCRIPTED_STORY = Story(
    text=(
        "Candlewright kept a tally that nobody asked to see.\n\n"
        "By the last night it was the only honest thing left at the table."
    )
)

BANNER_MARKER = "Game over."

# Synthetic remote-mode coordinates. Never resolved against AWS — the
# AgentCore client is replaced before any network call.
FAKE_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test"
)

# Both halves of the markup fault in one string, on purpose: ``[sic]`` is the
# silent-deletion half (Rich drops an unknown tag with no error and no trace)
# and ``[/bold]`` is the raising half. A fix that only escaped one would be
# caught.
MARKUP_HOSTILE = "boom [sic] and a stray [/bold] close"


# --------------------------------------------------------------------------
# Reading the panes and the widgets
# --------------------------------------------------------------------------


def _public_rows(app: GraphiaApp) -> list[str]:
    """Every rendered row of ``#public-log``, in write order, plain and stripped.

    ``RichLog`` keeps one entry per *rendered* row, so a wrapped long line is
    several entries — which is the granularity the scroll assertions need,
    because ``scroll_y`` counts these rows. Stripped because the pane carries
    ``padding: 0 1`` and no assertion here should depend on whether that
    padding reaches ``Strip.text``.
    """
    rows: list[str] = []
    for line in app.query_one("#public-log", RichLog).lines:
        text = getattr(line, "text", None)
        rows.append((text if text is not None else str(line)).strip())
    return rows


def _squashed(text: str) -> str:
    """``text`` with every whitespace character removed.

    Used on both sides of the crash-banner assertions, because a long
    filesystem path is one unbroken "word": ``RichLog``'s wrap hard-breaks it
    at the content width, possibly mid-segment (``.../[ru`` + ``n]/...``), and
    joining the rows back needs no separator. Measured while writing this
    file: the break also landed inside ``"Press q to leave."`` and rejoining
    with no separator produced ``"Pressq to leave."`` — hence squashing both
    the haystack *and* the needle rather than only the haystack. Safe here
    because neither a path nor the hint depends on which whitespace it
    contains; not used anywhere a row's internal spacing matters.
    """
    return re.sub(r"\s+", "", text)


def _sole_row(rows: list[str], needle: str, what: str) -> int:
    """Index of the ONE row containing ``needle``; fail loudly on 0 or 2+.

    Exactly-one is an anti-vacuity guard, not defensiveness: a lookup that
    tolerated "no match" would let every row comparison below run against a
    sentinel index, and one that tolerated several would compare the wrong row.
    """
    hits = [i for i, row in enumerate(rows) if needle in row]
    assert len(hits) == 1, (
        f"expected exactly ONE row of #public-log to carry {what} "
        f"({needle!r}); found {len(hits)} among {len(rows)} rows. "
        "Public log was:\n" + "\n".join(rows)
    )
    return hits[0]


def _visible_row_range(app: GraphiaApp) -> range:
    """The rows of ``#public-log`` currently inside the viewport.

    ``scroll_offset.y`` is the integer row at the top of the window;
    ``size.height`` is how many rows the window shows. ``scroll_y`` is a float
    (a wheel event lands on a fraction), which is why the offset is used for
    row arithmetic.
    """
    log = app.query_one("#public-log", RichLog)
    top = int(log.scroll_offset.y)
    return range(top, top + log.size.height)


def _static_plain(widget: Static) -> str:
    """Plain text of a ``Static``, tolerating a ``Content`` renderable.

    Trap 3 in the module docstring: ``conftest.plain_text`` handles ``Text``
    and then falls back to ``Text.from_markup(str(rendered))``, which is the
    exact parse the production fix exists to avoid — it raises on ``[/bold]``
    and deletes ``[sic]``. Both ``Content`` and ``Text`` expose ``.plain``, so
    reading that is correct for either and parses nothing.
    """
    rendered = widget.render()
    if isinstance(rendered, (Content, Text)):
        return rendered.plain
    return str(rendered)


def _failure_modal_plain(modal: FailureModal) -> str:
    """Every ``Label``/``Static`` body in the modal, flattened, unparsed."""
    return "\n".join(_static_plain(w) for w in modal.query(Static))


def _leaving_key() -> str:
    """The key ``GraphiaApp`` binds to ``action_leave``, read off ``BINDINGS``.

    Derived rather than typed so the hint-names-the-key assertion below cannot
    drift from the binding: rebinding the leaving key without rewording
    :data:`~graphia.ui.app.EXIT_HINT` fails here rather than shipping a hint
    that advertises a key nothing is bound to.
    """
    keys = [
        binding.key
        for binding in GraphiaApp.BINDINGS
        if getattr(binding, "action", None) == "leave"
    ]
    assert len(keys) == 1, (
        f"expected exactly one App binding for the `leave` action, got {keys!r}"
    )
    return keys[0]


# --------------------------------------------------------------------------
# Reaching each of the ways a game can end
# --------------------------------------------------------------------------
#
# THREE ENDING VARIANTS, THREE ``_enter_endgame`` CALL STATEMENTS, AND FOUR
# PARAMETERS HERE — the three counts differ, and the mismatch decides what
# this parametrisation has to cover.
#
# ``win`` and ``runaway`` are two variants sharing ONE statement:
# ``_OUTCOME_BY_WINNER`` deliberately omits ``"runaway"``, so ``_record_career``
# computes no outcome and returns having written nothing, which is why the
# runaway story is the last content before the banner. Both are still driven,
# because the shared statement is not the same as a shared trajectory.
# ``_drive``'s ``except`` is the third variant and splits on
# ``config.remote_mode`` into the other two statements, which is where the
# fourth parameter comes from. Deleting any one of the three statements must
# turn this file red — Task 6.7's runaway-parity claim, expressed as a test.

_ENDING_VARIANTS = ("win", "runaway", "local_crash", "remote_crash")

# Only the two real endings write the facts message, so only they have a
# winner line to scroll back to.
_WINNER_LINE = {
    "win": ENDGAME_WINNER_MAFIA,
    "runaway": ENDGAME_WINNER_RUNAWAY,
}


class _CrashingAgentCoreClient:
    """Drop-in ``AgentCoreClient`` whose ``stream`` raises mid-iteration.

    Mirrors ``tests/test_slice_failure_modal.py``'s fake. Raising during
    iteration rather than from ``__init__`` models a Runtime that crashed
    mid-game, so ``build_graph`` has already returned and ``_thread_id`` is a
    real session id by the time the modal renders.
    """

    def __init__(
        self, *, runtime_arn: str, region: str, boto3_client: Any | None = None
    ) -> None:
        if boto3_client is not None:
            raise AssertionError("the scripted client must never get a boto3 client")
        self.runtime_arn = runtime_arn
        self.region = region

    def stream(self, payload: Any, run_config: dict, stream_mode: str = "updates"):
        raise RuntimeError(MARKUP_HOSTILE)
        yield {}  # pragma: no cover - keeps this a generator


def _arrange_ending(
    variant: str,
    *,
    monkeypatch: pytest.MonkeyPatch,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
) -> tuple[GraphiaApp, Any]:
    """Configure ``variant`` and return ``(app, fake_large_handle)``, not running.

    Every variant pins the human Law-abiding via ``GRAPHIA_ROLE`` so no
    ``point`` interrupt fires and the pilot never has to disambiguate a modal
    from the docked ``Input``.

    * ``win`` — one Mafioso and two Citizens (the human one of them): the Night
      takes the AI Citizen, which is parity, so ``check_win_night`` routes
      straight to ``end_screen`` and no Day ever runs. THE SHORTEST GAME THAT
      REACHES A REAL OUTCOME, and the only one needing a live Pointing
      dispatcher (the target must be resolved against freshly-assigned uuid
      ids so the *human* survives — a dead human flips the app into spectator
      mode and writes extra rows).
    * ``runaway`` — ``GRAPHIA_MAX_DAYS=1`` trips the spec-023 short-circuit at
      the first ``night_open``: no Night kill, no Day, no vote, and no career
      panel. The ending with the least state behind it.
    * ``local_crash`` / ``remote_crash`` — ``_drive``'s ``except``. Local
      replaces ``graphia.ui.app.drive_graph`` with a coroutine that raises
      (the in-process graph would otherwise just park at the name prompt);
      remote replaces ``graphia.driver.AgentCoreClient`` so the failure comes
      from where a real deployed-Runtime failure comes from.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(NAME_POOL)

    if variant == "win":
        monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
        monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "2")
        fake = fake_large(
            stories=[SCRIPTED_STORY],
            # Scripted defensively: the Day->Night hinge fans ``day_diary`` out
            # for any game that survives its first Day, and no assertion here
            # should depend on the scenario ending exactly when it means to.
            diaries=[Diary(entry="Day one is done and I trust no one yet.")],
        )
        return GraphiaApp(), fake

    if variant == "runaway":
        monkeypatch.setenv("GRAPHIA_MAX_DAYS", "1")
        return GraphiaApp(), fake_large(stories=[SCRIPTED_STORY])

    if variant == "local_crash":

        async def _crashing_drive_graph(**kwargs: Any) -> None:
            raise RuntimeError(MARKUP_HOSTILE)

        monkeypatch.setattr("graphia.ui.app.drive_graph", _crashing_drive_graph)
        return GraphiaApp(), fake_large()

    if variant == "remote_crash":
        monkeypatch.setenv("GRAPHIA_REMOTE", "1")
        monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
        monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-tests")
        monkeypatch.setattr(
            "graphia.driver.AgentCoreClient", _CrashingAgentCoreClient
        )
        return GraphiaApp(), fake_large()

    raise AssertionError(f"unknown ending variant {variant!r}")


async def _boot_and_name(app: GraphiaApp, pilot: Any, name: str = HUMAN_NAME) -> Any:
    """Wait for the drive worker to build the graph, then submit the name."""
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


def _install_live_dispatcher(fake: Any, graph: Any, run_config: Any) -> None:
    """Resolve Pointing/DayAction/Ballot at invoke time against live state.

    The Night target cannot be pre-scripted: the real player ids only exist
    once ``assign_roles`` has run. Always picks a Law-abiding **non-human**, so
    the human survives to the ending.
    """
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


async def _reach_the_ending(
    app: GraphiaApp, pilot: Any, variant: str, fake: Any
) -> None:
    """Drive ``app`` to ``variant``'s ending and leave it there.

    Returns with ``_game_over`` True and the final banner written. Asserts the
    scenario really reached its own ending rather than some other one, so a
    contract assertion can never be measuring a game that ended differently
    from the way the test says it did.
    """
    if variant in ("local_crash", "remote_crash"):
        await pilot.pause()
        for _ in range(400):
            if app._game_over:
                break
            await pilot.pause(0.05)
        assert app._game_over, (
            f"the injected {variant} exception never reached _drive's except"
        )
        # One more tick so the pane is sized and any deferred write has
        # replayed before anything is read off it (trap 2).
        await pilot.pause()
        return

    graph = await _boot_and_name(app, pilot)
    if variant == "win":
        _install_live_dispatcher(fake, graph, app._run_config)

    await drive_until_public_log_contains(
        pilot, log_text=lambda: "\n".join(_public_rows(app))
    )
    assert app._game_over is True, "the banner landed but _game_over did not flip"
    winner = app._latest_state.get("winner")
    if variant == "runaway":
        assert winner == "runaway", (
            f"scenario did not reach the Day-cap runaway; winner={winner!r}"
        )
    else:
        assert winner == "mafia", (
            f"the shortest-win scenario should end in a Mafia parity win; "
            f"winner={winner!r}"
        )


async def _dismiss_any_modal(app: GraphiaApp, pilot: Any) -> bool:
    """Close a modal sitting over the ending, and say whether there was one.

    Only the ``remote_crash`` variant has one: ``_drive`` calls
    ``_enter_endgame`` and *then* pushes :class:`FailureModal`, and
    ``push_screen`` moves focus into the modal — so on that path the reading
    contract is only observable once the modal is gone. That is the design
    (the production comment says so: the ending pane is focused **before** the
    modal goes up precisely so dismissing it hands focus back to
    ``#public-log`` rather than to the whispers pane), which makes the focus
    assertion after this call a test of that claim too. Dismissed with the
    modal's own ``escape`` binding rather than by calling ``dismiss()``, so
    the App's priority ``escape`` binding yielding to it is exercised as well.
    """
    if not isinstance(app.screen, ModalScreen):
        return False
    await pilot.press("escape")
    for _ in range(40):
        if not isinstance(app.screen, ModalScreen):
            break
        await pilot.pause(0.05)
    assert not isinstance(app.screen, ModalScreen), (
        f"{type(app.screen).__name__} did not dismiss on `escape`"
    )
    return True


def _assert_ready_to_scroll(app: GraphiaApp) -> RichLog:
    """Precondition for every scrolling assertion — see trap 2.

    ``RichLog`` defers writes until its first ``Resize`` and a deferred write
    replays re-reading ``auto_scroll``, so a pane that was never sized sits at
    row 0 with the ending below the fold. At row 0, "``home`` reaches the
    winner line" and "``pageup`` moved the view" both pass or fail for reasons
    that have nothing to do with the code under test. Requiring the pane to be
    parked at a non-zero true bottom rules that out.
    """
    log = app.query_one("#public-log", RichLog)
    assert log.max_scroll_y > 0, (
        f"#public-log has nothing to scroll (max_scroll_y="
        f"{log.max_scroll_y}, {len(log.lines)} rows, size {log.size!r}) — "
        "the pane was probably never sized, so its writes replayed with "
        "auto-scroll already off"
    )
    assert int(log.scroll_offset.y) == int(log.max_scroll_y), (
        f"#public-log should be parked at its true bottom when the ending "
        f"lands; scroll_offset.y={log.scroll_offset.y} of "
        f"max_scroll_y={log.max_scroll_y}"
    )
    return log


# --------------------------------------------------------------------------
# The reading state: focused, frozen, uncapped, and captioned
# --------------------------------------------------------------------------


@pytest.mark.parametrize("variant", _ENDING_VARIANTS)
async def test_every_ending_lands_the_same_reading_contract(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    """Every path out of a game puts ``#public-log`` into the same state.

    The runaway-parity witness (Task 6.7) and the mutation witness for all
    four lines of ``_enter_endgame`` at once: measured, deleting
    ``log.focus()``, deleting ``log.auto_scroll = False``, deleting
    ``log.border_subtitle`` or adding a ``max_lines`` cap turns **every**
    parameter of this test red, and deleting the ``_enter_endgame(log)`` call
    from any one of the three call statements turns exactly the parameters
    that reach that statement red.

    Anti-vacuity on the focus assertion, which is the one that would otherwise
    pass by accident: ``#private-log`` is FIRST in the game screen's focus
    chain, so "focus is on #public-log" is a decision rather than the default.
    The chain is asserted, not assumed — if a later change reorders
    ``compose()`` so the public pane comes first, this assertion stops being
    worth anything and says so instead of quietly going green.

    ``remote_crash`` needs its :class:`FailureModal` dismissed first; see
    :func:`_dismiss_any_modal` for why that is the contract rather than a
    workaround.
    """
    app, fake = _arrange_ending(
        variant,
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, variant, fake)
        had_modal = await _dismiss_any_modal(app, pilot)
        assert had_modal is (variant == "remote_crash"), (
            f"only the remote crash should raise a modal over the ending; "
            f"{variant} had_modal={had_modal}"
        )
        log = app.query_one("#public-log", RichLog)

        # Positive half of the focus claim: the pane the ending is in is NOT
        # the one Textual would land on by itself.
        chain = [w.id for w in app.screen.focus_chain]
        assert chain and chain[0] == "private-log", (
            "this assertion only means something while #private-log is first "
            f"in the focus chain (it is what focus falls to when the docked "
            f"Input is disabled); chain is {chain!r}"
        )
        assert app.focused is log, (
            f"the ending pane must hold focus, or PgUp scrolls the six-row "
            f"whispers pane while the ending stays pinned and nothing visibly "
            f"happens; focus was on {getattr(app.focused, 'id', None)!r}"
        )

        # Frozen: a late write must not yank a reader out of the roster.
        assert log.auto_scroll is False, (
            "#public-log must stop auto-scrolling once the game is over"
        )
        assert RichLog().auto_scroll is True, (
            "control: RichLog defaults auto_scroll True, so False above is a "
            "deliberate setting and not the framework default"
        )

        # Uncapped: the oldest rows of an ending are the winner line and the
        # roster reveal, i.e. exactly what a cap discards first.
        #
        # This is asserted on the ATTRIBUTE as well as behaviourally (in
        # ``test_home_brings_the_winner_line_back_into_view``) because the two
        # forms of the cap fail differently, measured. A cap set at
        # compose time trims while the game plays, so the winner line is gone
        # from ``lines`` by the ending and the behavioural test goes red. A cap
        # set inside ``_enter_endgame`` — the mutation as it was written down —
        # trims on the *next* write, and nothing writes after it, so the rows
        # survive and only this line catches it. Neither form is caught by
        # both, so both assertions stay.
        assert log.max_lines is None, (
            f"#public-log must keep every row of the ending; max_lines was "
            f"{log.max_lines!r}"
        )

        # Captioned, on the frame rather than in the log.
        assert log.border_subtitle is not None, (
            "#public-log must carry the exit hint on its border, where it "
            "survives scrolling — the banner row does not"
        )
        assert EXIT_HINT in log.border_subtitle, (
            f"the border subtitle {log.border_subtitle!r} must carry the hint "
            f"{EXIT_HINT!r} (imported, never copied)"
        )


@pytest.mark.parametrize("variant", _ENDING_VARIANTS)
async def test_every_ending_is_left_by_q(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    """A stray ``x`` leaves every ending on screen; ``q`` leaves every ending.

    The exit contract must not differ between ending variants — a second
    contract for one of them makes "it is clear which key leaves" conditional
    on how the game happened to finish. Both halves per variant, absence first
    and its control second, because the control ends the app.

    On ``remote_crash`` the :class:`FailureModal` is on screen, so the
    keystrokes reach it first: ``q`` is inert there for a reason of its own
    (the modal is ``is_modal``, which stops a non-priority App binding
    propagating past it — see the module docstring's abstention) and the modal
    has to be dismissed before ``q`` can leave. Asserted here rather than
    skipped: it is the same contract seen from under the modal, and
    :func:`test_q_under_the_failure_modal_is_inert_and_the_modal_survives`
    isolates it.
    """
    app, fake = _arrange_ending(
        variant,
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, variant, fake)

        await pilot.press("x")
        assert app.is_running is True, (
            "`x` must not exit a finished game — the ending has to survive a "
            "stray keypress to be readable"
        )

        if isinstance(app.screen, ModalScreen):
            await pilot.press("q")
            assert app.is_running is True, (
                "`q` must be inert while a modal is up — it would throw away "
                "the ending and the modal's contents in one keystroke"
            )
            assert isinstance(app.screen, ModalScreen), (
                "`q` must not dismiss the modal either"
            )
            await pilot.press("escape")
            for _ in range(40):
                if not isinstance(app.screen, ModalScreen):
                    break
                await pilot.pause(0.05)
            assert not isinstance(app.screen, ModalScreen), (
                "the failure modal should dismiss on its own `escape` binding"
            )

        await pilot.press("q")
        assert app.is_running is False, (
            "`q` must leave a finished game — it is the key the pinned hint "
            "advertises"
        )


# --------------------------------------------------------------------------
# The keys a reader presses, and the two that leave
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "must_scroll"),
    [
        # `space` is named explicitly because it exited before this slice and
        # is what people press to page through text; Textual binds no
        # page-down to it, so it must be inert rather than scrolling.
        pytest.param("x", False, id="x"),
        pytest.param("space", False, id="space"),
        pytest.param("up", True, id="up"),
        pytest.param("pageup", True, id="pageup"),
    ],
)
async def test_reading_keys_do_not_leave_the_finished_game(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    must_scroll: bool,
) -> None:
    """``x``, ``space``, ``up`` and ``pageup`` all leave the app running.

    Re-measured here rather than taken on trust: restoring the deleted
    exit-on-any-key ``on_key`` turns all four parameters red, so at game-over
    a ``pageup`` really did scroll *and* kill the app in the same keystroke —
    the ending was unreadable in the one gesture a person reaches for. The
    scroll keys are asserted to have actually moved the view, not merely to
    have been survived; ``q`` at the end is the positive control that a
    keypress can still exit here.
    """
    app, fake = _arrange_ending(
        "runaway",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "runaway", fake)
        log = _assert_ready_to_scroll(app)
        before = int(log.scroll_offset.y)

        await pilot.press(key)
        await pilot.pause()

        assert app.is_running is True, (
            f"{key!r} must not exit a finished game — it is a reading key, "
            "not a leaving key"
        )
        after = int(log.scroll_offset.y)
        if must_scroll:
            assert after < before, (
                f"{key!r} should have scrolled #public-log back; "
                f"scroll_offset.y stayed at {after} (max {log.max_scroll_y})"
            )
        else:
            assert after == before, (
                f"{key!r} is bound to nothing and should not move the view; "
                f"scroll_offset.y went {before} -> {after}"
            )

        await pilot.press("q")
        assert app.is_running is False, (
            "control: `q` must still leave, so the assertion above is a guard "
            "firing and not a dead keyboard"
        )


async def test_escape_leaves_the_finished_game_in_two_keystrokes(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``escape`` leaves — via the confirmation modal, so it takes two keys.

    Tech spec section 4 ("Testing Strategy") and Task 6.6's own line both say
    "``q`` and ``escape`` leave" without saying that ``escape`` alone does
    not: it pushes
    :class:`~graphia.ui.quit_modal.QuitModal` and ``y`` then exits. Pinned
    here so the criterion cannot be read as a one-keystroke claim, and so the
    first keystroke's *non*-exit is asserted rather than assumed.
    """
    app, fake = _arrange_ending(
        "runaway",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "runaway", fake)

        await pilot.press("escape")
        for _ in range(40):
            if isinstance(app.screen, QuitModal):
                break
            await pilot.pause(0.05)
        assert isinstance(app.screen, QuitModal), (
            f"`escape` at game-over should push the QuitModal; the screen was "
            f"{type(app.screen).__name__}"
        )
        assert app.is_running is True, (
            "`escape` on its own must not exit — the confirmation is the "
            "second keystroke"
        )

        await pilot.press("y")
        assert app.is_running is False, (
            "confirming the quit modal must leave the game"
        )


async def test_q_is_a_character_mid_game_and_the_leaving_key_at_the_end(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``q`` types mid-game, is inert mid-game off the input, and leaves at the end.

    Three claims on one game, because the third is the positive control for
    the first two and it ends the app.

    1. **Typed into the focused ``Input``, ``q`` inserts the character.** Spec
       003 unbound ``q`` from quitting for exactly this reason: every name and
       every speech containing a "q" would break. Nothing in the suite covered
       it before this file.
    2. **With focus off the input, ``q`` still does not leave.** This is
       ``action_leave``'s ``not self._game_over`` -> ``SkipAction`` guard, and
       it needs focus off the input to be *reached* at all: with the ``Input``
       focused and enabled the widget consumes the ``q`` and the App binding
       never runs, which is why the two mid-game presses in
       ``tests/test_career_stats.py`` are not a witness for it. Two ``tab``
       presses walk the focus chain (``#private-log`` then ``#public-log``) —
       a gesture a player makes to read the panes while the AI is talking.
    3. **At game-over the same key leaves.** Without this the first two claims
       would also hold on an app where ``q`` is bound to nothing at all.
    """
    app, fake = _arrange_ending(
        "runaway",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(200):
            if app._graph is not None and app._run_config is not None:
                break
            await pilot.pause(0.05)
        prompt = app.query_one("#player-input", Input)
        for _ in range(200):
            if prompt.disabled is False:
                break
            await pilot.pause(0.05)

        assert app._game_over is False, "precondition: the game has not ended"
        assert app.focused is prompt, (
            "precondition: the name prompt should hold focus, or claim 1 is "
            "not about a focused Input at all"
        )

        # (1) a character, not a command.
        await pilot.press("q", "u", "i", "q")
        await pilot.pause()
        assert prompt.value == "quiq", (
            f"`q` must reach the Input as a character mid-game; the box holds "
            f"{prompt.value!r}"
        )
        assert app.is_running is True, "typing must not end the game"

        # (2) off the input, the guard is reached and holds.
        await pilot.press("tab", "tab")
        await pilot.pause()
        assert app.focused is app.query_one("#public-log", RichLog), (
            f"two tabs should walk focus to #public-log; focus is on "
            f"{getattr(app.focused, 'id', None)!r}"
        )
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running is True, (
            "`q` must not leave a game in progress even when the App binding "
            "is reachable — action_leave is gated on _game_over"
        )
        assert app._game_over is False, "the game must still be in progress"

        # (3) the control: finish the game and the same key leaves.
        prompt.focus()
        prompt.value = ""
        await pilot.press(*HUMAN_NAME)
        await pilot.press("enter")
        await drive_until_public_log_contains(
            pilot, log_text=lambda: "\n".join(_public_rows(app))
        )
        assert app._game_over is True
        await pilot.press("q")
        assert app.is_running is False, (
            "control: `q` leaves once the game is over, so the two assertions "
            "above are guards firing and not a dead binding"
        )


async def test_q_under_the_failure_modal_is_inert_and_the_modal_survives(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``q`` must not fire from under :class:`FailureModal`.

    The modal's whole job is to hand the player the CloudWatch log group and
    the per-session filter expression — the only pointer they have to the
    server-side log of a remote crash. ``_game_over`` is True underneath it,
    so without ``action_leave``'s ``ModalScreen`` -> ``SkipAction`` guard the
    coordinates are thrown away by the first ``q``.

    Both halves, control second: the modal survives ``q``, then dismissing it
    with its own ``escape`` binding leaves a game that ``q`` does exit.
    """
    app, fake = _arrange_ending(
        "remote_crash",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "remote_crash", fake)

        assert isinstance(app.screen, FailureModal), (
            f"a remote-mode crash should push the FailureModal; the screen "
            f"was {type(app.screen).__name__}"
        )
        assert app._game_over is True, (
            "precondition: _game_over is set under the modal, so only the "
            "ModalScreen guard can be holding `q` back"
        )

        await pilot.press("q")
        await pilot.pause()
        assert app.is_running is True, (
            "`q` must be inert under the FailureModal — it would exit before "
            "the player could copy the CloudWatch coordinates"
        )
        assert isinstance(app.screen, FailureModal), (
            "`q` must not dismiss the FailureModal either"
        )

        await pilot.press("escape")
        for _ in range(40):
            if not isinstance(app.screen, FailureModal):
                break
            await pilot.pause(0.05)
        assert not isinstance(app.screen, FailureModal), (
            "the modal dismisses on its own `escape` binding"
        )
        await pilot.press("q")
        assert app.is_running is False, (
            "control: with the modal gone `q` leaves, so the assertions above "
            "are the guard firing"
        )


# --------------------------------------------------------------------------
# Reading back: the winner line stays reachable and the hint stays on screen
# --------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["win", "runaway"])
async def test_home_brings_the_winner_line_back_into_view(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    """``home`` reaches the top of the ending, on both real ending paths.

    This is what an uncapped ``max_lines`` buys: the oldest rows of an ending
    are the winner line and the roster reveal, so a cap discards exactly what
    a person scrolls back for. A cap makes the anchor unfindable and
    :func:`_sole_row` fails loudly rather than letting the row comparison run
    against a sentinel — measured against a ``max_lines=20`` on the
    ``RichLog`` in ``compose()``, which is the form of the cap that trims
    while the game is being played.

    Both halves, positive first: the winner line is **off screen** at rest
    (the pane is parked at the bottom of a ~60-row ending in an 11-row
    viewport), and **on screen** after ``home``. Without the first half this
    would pass on a short ending that never needed scrolling at all.

    The anchor comes from :func:`_first_row_of` over the imported constant —
    see trap 1: ``ENDGAME_WINNER_RUNAWAY`` is 84 characters against 76 content
    columns, so it never occupies one row and a verbatim match finds nothing.
    """
    app, fake = _arrange_ending(
        variant,
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, variant, fake)
        _assert_ready_to_scroll(app)

        rows = _public_rows(app)
        winner_row = _sole_row(
            rows, _first_row_of(_WINNER_LINE[variant]), "the winner line"
        )

        assert winner_row not in _visible_row_range(app), (
            f"the winner line (row {winner_row}) is already on screen at rest "
            f"(visible {_visible_row_range(app)!r}) — this ending is too short "
            "for `home` to have anything to do, so the assertion below would "
            "pass without scrolling"
        )

        await pilot.press("home")
        await pilot.pause()

        assert winner_row in _visible_row_range(app), (
            f"`home` must bring the winner line (row {winner_row}) back into "
            f"view; the viewport shows {_visible_row_range(app)!r} of "
            f"{len(rows)} rows"
        )


async def test_the_pinned_hint_names_the_leaving_key_and_survives_scrolling(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hint stays on the frame while the banner that carried it scrolls away.

    This is the whole reason the affordance is a ``border_subtitle`` and not a
    log line: the banner row leaves the viewport the moment the person scrolls
    up, and ``RichLog`` has no per-line removal so a "one moment" line could
    never be taken back either. The subtitle costs zero rows and is pinned to
    the frame for as long as they read.

    Both halves, positive first: the banner row **is** on screen at rest and
    **is not** after ``home``, while the subtitle is on screen throughout.
    Without the banner half, "the subtitle is still there" would hold even if
    nothing had scrolled.

    The hint text is asserted against the imported
    :data:`~graphia.ui.app.EXIT_HINT` — never a copied literal, so a reword
    moves the assertion with it. What a reword must *not* do is stop naming
    the bound key, and that coupling is asserted against
    :func:`_leaving_key`, read off ``GraphiaApp.BINDINGS``.
    """
    app, fake = _arrange_ending(
        "runaway",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "runaway", fake)
        log = _assert_ready_to_scroll(app)

        key = _leaving_key()
        assert re.search(rf"\b{re.escape(key)}\b", EXIT_HINT), (
            f"the hint {EXIT_HINT!r} must name the key actually bound to "
            f"`leave` ({key!r}) — otherwise it advertises the wrong key"
        )

        rows = _public_rows(app)
        banner_row = _sole_row(rows, BANNER_MARKER, "the game-over banner")
        assert EXIT_HINT in rows[banner_row], (
            f"the banner should carry the hint too, at rest; row "
            f"{banner_row} was {rows[banner_row]!r}"
        )
        assert banner_row in _visible_row_range(app), (
            f"the banner (row {banner_row}) must be on screen at rest, or "
            f"'it scrolled away' below is vacuous; viewport shows "
            f"{_visible_row_range(app)!r}"
        )
        assert EXIT_HINT in (log.border_subtitle or ""), (
            f"the border subtitle {log.border_subtitle!r} must carry the hint"
        )

        await pilot.press("home")
        await pilot.pause()

        assert banner_row not in _visible_row_range(app), (
            f"`home` should have scrolled the banner (row {banner_row}) out "
            f"of view; viewport shows {_visible_row_range(app)!r}"
        )
        assert EXIT_HINT in (log.border_subtitle or ""), (
            "the hint must survive scrolling — it is on the frame, not in the "
            f"log; subtitle was {log.border_subtitle!r}"
        )


async def test_a_write_after_the_ending_leaves_the_readers_position_alone(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the game is over, a late write must not yank the view to the bottom.

    ``RichLog.auto_scroll`` defaults True, which is right for the whole game
    and wrong the instant the person starts reading back: any later write
    jumps the view to the bottom, taking the reader out of the roster
    mid-sentence. Task 6.2 measured that on this pane as ``scroll_y`` 91 ->
    107; the positive control below re-measures it here rather than citing it,
    which is the point of having the control at all.

    Both halves, positive first: with ``auto_scroll`` forced back **on**, a
    write from row 0 really does jump this exact widget to the bottom — so the
    negative half cannot be passing because writes are inert here. Then with
    the production setting restored, the same write from row 0 leaves the view
    at row 0.
    """
    app, fake = _arrange_ending(
        "runaway",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "runaway", fake)
        log = _assert_ready_to_scroll(app)

        assert log.auto_scroll is False, (
            "production must switch auto-scroll off when the game ends"
        )

        # Positive control — writes CAN move this widget.
        log.auto_scroll = True
        await pilot.press("home")
        await pilot.pause()
        assert int(log.scroll_offset.y) == 0
        log.write(Text("a late line arriving while someone reads"))
        await pilot.pause()
        await pilot.pause()
        assert int(log.scroll_offset.y) > 0, (
            "control: with auto_scroll on, a write must move the view — "
            "otherwise the assertion below proves nothing"
        )

        # The production setting, restored, and the same write.
        log.auto_scroll = False
        await pilot.press("home")
        await pilot.pause()
        assert int(log.scroll_offset.y) == 0
        log.write(Text("a second late line"))
        await pilot.pause()
        await pilot.pause()
        assert int(log.scroll_offset.y) == 0, (
            "with auto-scroll off, a write must leave the reader where they "
            f"were; the view moved to row {log.scroll_offset.y}"
        )


async def test_the_mouse_wheel_scrolls_the_ending_pane(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wheel scrolls the ending and the game stays open.

    **A DECLARED ABSTENTION, recorded as a fact rather than counted as
    coverage.** Nothing about the wheel changed in this slice: scroll events
    route to the widget under the pointer, so ``#public-log`` scrolls whether
    or not it is focused, and a wheel event never reached the old
    exit-on-any-key ``on_key`` either — the wheel was the one way to read back
    through an ending *before* this slice named a leaving key. Measured, the
    only mutation in this slice's table that turns this test red is deleting
    ``_enter_endgame`` from the win/runaway call site, and it does so through
    the "did the ending happen at all" precondition rather than through
    anything about the wheel. Pinned as a regression floor for a future change
    that docks something over the pane or takes its ``overflow-y: scroll``
    away.

    ``Pilot`` on Textual 8.2.4 has no wheel helper — there is no ``scroll_up``
    or ``scroll_down`` — so the event is posted directly.
    """
    app, fake = _arrange_ending(
        "runaway",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "runaway", fake)
        log = _assert_ready_to_scroll(app)
        assert not hasattr(pilot, "scroll_up"), (
            "Pilot grew a wheel helper — use it instead of posting the event"
        )

        before = log.scroll_y
        log.post_message(
            events.MouseScrollUp(
                widget=log,
                x=1,
                y=1,
                delta_x=0,
                delta_y=-1,
                screen_x=1,
                screen_y=1,
                button=0,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        await pilot.pause()

        assert log.scroll_y < before, (
            f"a wheel-up must scroll the ending pane back; scroll_y stayed at "
            f"{log.scroll_y} (was {before}, max {log.max_scroll_y})"
        )
        assert app.is_running is True, "the wheel must not end the game"


# --------------------------------------------------------------------------
# The two markup exposures on the crash paths
# --------------------------------------------------------------------------


@pytest.fixture
def bracketed_log_file(env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``GRAPHIA_LOG_FILE`` at a path with BOTH markup faults in it.

    ``[run]`` is the silent-deletion half and ``[/bold]`` is the raising half;
    a directory name carrying both means one fixture covers a fault that
    ``rich.markup`` treats in two entirely different ways. A path is not model
    or player content, which is why Task 2.1 scoped it out — but it is still a
    value the banner's call site does not author, and the banner's only job is
    to tell the player where the log is.
    """
    log_file = tmp_path / "[run]" / "[/bold]" / "graphia.log"
    monkeypatch.setenv("GRAPHIA_LOG_FILE", str(log_file))
    return log_file


async def test_the_local_crash_banner_renders_the_log_path_intact(
    bracketed_log_file: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bracketed ``GRAPHIA_LOG_FILE`` reaches the screen character for character.

    The banner interpolating the path into a markup string had two failure
    modes, and this fixture carries both: ``[run]`` was **silently deleted**
    (``/tmp/[run]/graphia.log`` rendered as ``/tmp//graphia.log``, with no
    error and no trace) and ``[/bold]`` **raised** ``MarkupError`` — from
    inside the handler whose entire job is to say where the log is, so the
    exception escaped ``_drive``'s ``except`` and the player got neither the
    path nor an ending.

    Positive control, first: the same string put through
    ``Text.from_markup`` here loses the ``[run]`` segment, so the fixture
    really is markup-hostile and "the segment survived" cannot be passing on a
    path markup would have left alone.

    Read through :func:`_squashed` — every whitespace character stripped from
    both the needle and the rows. A filesystem path is one unbroken word, so
    ``RichLog`` hard-breaks it at the content width and a bracketed segment
    can straddle two rows; measured while writing this, the break also landed
    inside the hint, so the needles are squashed too rather than only the
    haystack.
    """
    app, fake = _arrange_ending(
        "local_crash",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    path = str(app.config.log_file)
    assert "[run]" in path and "[/bold]" in path, (
        f"the fixture must reach config.log_file; it is {path!r}"
    )

    # Control: this is what the reverted implementation would have produced.
    # The raising half first, then the silent-deletion half on the prefix of
    # the same path that stops short of the `[/bold]` (markup gives up at the
    # first unmatched close tag, so the two halves cannot be shown together).
    with pytest.raises(MarkupError):
        Text.from_markup(f"Error — see {path}")
    up_to_the_close_tag = path.split("[/bold]")[0]
    assert "[run]" in up_to_the_close_tag
    assert "[run]" not in Text.from_markup(up_to_the_close_tag).plain, (
        "control: rich.markup must really eat the `[run]` segment, or this "
        "test is asserting against a path markup would not have touched"
    )

    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "local_crash", fake)

        rows = _public_rows(app)
        squashed = _squashed("".join(rows))
        assert _squashed("Error — see") in squashed, (
            "the local-mode crash banner must be on screen; public log was:\n"
            + "\n".join(rows)
        )
        assert _squashed(path) in squashed, (
            f"the configured log path {path!r} must render character for "
            "character; the pane held:\n" + "\n".join(rows)
        )
        assert _squashed(EXIT_HINT) in squashed, (
            "the crash banner must advertise the leaving key too — a crashed "
            "ending is also something a person may want to scroll"
        )


async def test_the_failure_modal_renders_brackets_in_the_crash_summary(
    env: Path,
    fake_small: Callable[..., Any],
    fake_large: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bracketed exception message and log group reach the modal literally.

    The two values :class:`FailureModal` does not author — the crash summary
    (``f"{type(exc).__name__}: {exc}"``, whose text comes from wherever the
    exception came from) and ``GRAPHIA_LOG_GROUP``. A plain ``Static(str)``
    parses both as markup: ``[sic]`` vanishes and ``[/bold]`` raises, taking
    the whole modal — and with it the only pointer to the server-side log of a
    remote crash — down with it.

    Positive control, first: ``Text.from_markup`` on these same two strings
    deletes the ``[sic]``, so the fixture is genuinely markup-hostile.

    NOT read through ``conftest.plain_text`` — see trap 3. That helper hands a
    ``Content`` to ``Text.from_markup``, which raises on the very ``[/bold]``
    the production fix exists to survive, so a test built on it fails on
    correct code and reads as the fault still being present.
    """
    log_group = "/aws/bedrock-agentcore/runtime/graphia [sic] [/bold] test"
    monkeypatch.setenv("GRAPHIA_LOG_GROUP", log_group)

    # Control: markup really does mangle both of these. The `[/bold]` is
    # dropped for the deletion half because markup gives up at the first
    # unmatched close tag and would never reach the `[sic]` behind it.
    assert "[sic]" not in Text.from_markup(MARKUP_HOSTILE.replace("[/bold]", "")).plain
    assert "[sic]" not in Text.from_markup(log_group.replace("[/bold]", "")).plain
    with pytest.raises(MarkupError):
        Text.from_markup(MARKUP_HOSTILE)
    with pytest.raises(MarkupError):
        Text.from_markup(log_group)

    app, fake = _arrange_ending(
        "remote_crash",
        monkeypatch=monkeypatch,
        fake_small=fake_small,
        fake_large=fake_large,
    )
    assert app.config.cloudwatch_log_group == log_group

    async with app.run_test() as pilot:
        await _reach_the_ending(app, pilot, "remote_crash", fake)

        modal = app.screen
        assert isinstance(modal, FailureModal), (
            f"a remote crash should push the FailureModal; got "
            f"{type(modal).__name__}"
        )
        text = _failure_modal_plain(modal)

        assert MARKUP_HOSTILE in text, (
            f"the crash summary must render literally, brackets and all; the "
            f"modal held:\n{text}"
        )
        assert log_group in text, (
            f"the CloudWatch log group must render literally so it can be "
            f"pasted into the console; the modal held:\n{text}"
        )
        assert app._thread_id and app._thread_id != "<unknown>"
        assert f'{{ $.thread_id = "{app._thread_id}" }}' in text, (
            "the per-session filter must still be there — the modal's other "
            "coordinate"
        )
