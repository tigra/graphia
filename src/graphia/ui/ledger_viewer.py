"""Standalone Textual viewer for the eval quality ledger (spec 012, Slice 1).

A *second, separate* Textual app — not the game's :class:`~graphia.ui.app.GraphiaApp`
— that reads ``evals/blunder-ledger.yaml`` (the provenance-stamped quality
ledger ``blunder_eval`` appends to) and presents it as a scrollable table. The
heavy lifting — parsing the heterogeneous multi-document YAML and flattening it
into a stable, index-parallel column model — lives in the pure, Textual-free
:mod:`graphia.eval_ledger` data layer; this module is the thin presentation on
top (a :class:`~textual.widgets.DataTable` rendering that model).

**This viewer needs only a file**, so — unlike the game app — it never calls
:func:`graphia.config.load_config`: no AWS / checkpoint / model env is required
to read a ledger. The ledger path is injected via the app constructor (the same
DI seam the game app uses for its stores), defaulting to
:data:`~graphia.tools.blunder_eval.LEDGER_PATH` (the single source of truth for
where the harness writes), so tests can point it at a temp file.

Search (Slice 2) and the full-record drill-down (Slice 3) are later increments;
the structure here leaves clean seams for both (a top dock region for the search
input, ``cursor_type="row"`` for the eventual row-select) without implementing
them yet.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.style import Style as RichStyle
from rich.text import Text
from textual import events
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.message import Message
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
)

from graphia.eval_ledger import (
    KIND_ATTR,
    KIND_ATTR_LAW_ABIDING,
    KIND_ATTR_MAFIA,
    KIND_DIARY,
    KIND_DIARY_SUBSTITUTED,
    KIND_FIELD_LABEL,
    KIND_MARKER,
    KIND_RECAP,
    KIND_SPEAKER,
    KIND_SPEAKER_HUMAN,
    KIND_SPEAKER_LAW_ABIDING,
    KIND_SPEAKER_LAW_ABIDING_HUMAN,
    KIND_SPEAKER_MAFIA,
    KIND_SPEAKER_MAFIA_HUMAN,
    KIND_SPEECH,
    KIND_SPEECH_HUMAN,
    KIND_SPEECH_LAW_ABIDING,
    KIND_SPEECH_LAW_ABIDING_HUMAN,
    KIND_SPEECH_MAFIA,
    KIND_SPEECH_MAFIA_HUMAN,
    KIND_SPEECH_SUBSTITUTED,
    KIND_THOUGHT,
    KIND_THOUGHT_SUBSTITUTED,
    LedgerParseError,
    METRIC_ORDER,
    RawRecord,
    SEARCH_FIELDS,
    SEARCH_SCOPE_ALL,
    TableModel,
    TranscriptEntry,
    build_table_model,
    find_matches,
    list_transcripts,
    load_ledger,
    next_match,
    offset_for_row,
    read_transcript,
    render_detail,
    row_for_offset,
    row_matches_field,
    step_match_by_row,
    tokenize_transcript,
    wrap_row_starts,
)
from graphia.tools.blunder_eval import LEDGER_PATH

# The empty-ledger copy shown in #empty-state when the ledger is missing or has
# no records. (Slice 2's distinct no-matches message — built by
# :func:`_no_match_message` — handles a non-empty query that filters everything
# out — kept separate on purpose.)
_EMPTY_LEDGER_MESSAGE = "No runs recorded yet."


def _no_match_message(query: str) -> str:
    """The #empty-state copy when a non-empty query survives zero rows.

    Distinct from :data:`_EMPTY_LEDGER_MESSAGE` (the empty/missing ledger): this
    is shown when the ledger *has* runs but none match the live filter, and it
    echoes the query so the user sees exactly what filtered everything out.
    """
    return f"No runs match '{query}'."

# The friendly copy shown when the ledger file exists but is malformed YAML
# (``load_ledger`` raised ``LedgerParseError``) — a readable error state in the
# same #empty-state slot instead of a traceback escaping to the terminal.
_PARSE_ERROR_PREFIX = "Could not read the ledger:"


class SearchInput(Input):
    """The value :class:`~textual.widgets.Input`, with a boundary-jump left edge.

    A plain ``Input`` consumes ``left`` via its own ``cursor_left`` binding, so a
    screen-level ``on_key`` never sees a ``left`` press to act on. This subclass
    overrides :meth:`action_cursor_left` so that when the caret is already at the
    **start** of the text (``cursor_position == 0``) the keystroke jumps focus
    *out* to the field selector on its left (the boundary-jump nav, requirement
    C) instead of being a no-op; anywhere else inside the text it falls through
    to the normal caret move. The jump is announced via the
    :class:`FocusFieldSelect` message the screen handles, keeping the widget
    decoupled from the selector's id.
    """

    class FocusFieldSelect(Message):
        """Posted when ``left`` is pressed with the caret at the input's start.

        The screen handles it by moving focus to the ``#field-select`` selector —
        the left half of the boundary-jump (the selector's ``right`` jumps back).
        """

    def action_cursor_left(self, select: bool = False) -> None:
        """Move the caret left, or jump to the field selector at the start edge.

        Mirrors ``Input.action_cursor_left(select=False)`` (Textual 8.2.4). At
        ``cursor_position == 0`` there is nothing to the left, so instead of the
        normal no-op we post :class:`FocusFieldSelect` to hand focus to the
        selector; otherwise we defer to the base caret move (preserving the
        optional ``select`` extend-selection behaviour).
        """
        if self.cursor_position == 0:
            self.post_message(self.FocusFieldSelect())
            return
        super().action_cursor_left(select=select)


class DetailScreen(Screen):
    """The full-record drill-down: one ledger record rendered in a scroller.

    Wraps the pure data layer's :func:`~graphia.eval_ledger.render_detail` output
    (a long, sectioned plain string — ``run`` → ``code`` → … → ``notes``) in a
    :class:`~textual.containers.VerticalScroll` so the whole record is reachable
    even when it overruns the viewport. **No formatting lives here** — the string
    comes verbatim from the data layer, mirroring the table model's plain-string
    contract. ``escape``/``q``/``backspace`` pop back to the table (its own
    bindings, so they take precedence over the app-level quit while this screen
    is active).

    A :class:`~textual.widgets.Header` (the viewer name + a "run detail · Esc /
    Backspace to go back" subtitle) and a :class:`~textual.widgets.Footer` (the
    ``Back`` key hints) frame the record, so it stays obvious that this is the
    same ledger viewer and how to return — a full-window record can otherwise
    read like a different program with no visible way out.

    **The body is a horizontal split** (spec 037): the ``#detail-split``
    :class:`~textual.containers.Horizontal` holds the record scroller at ``1fr``
    beside a fixed-width ``#transcript-panel`` listing this run's preserved
    games, so the numbers and the games that produced them are on screen
    together. ``Header``/``Footer`` stay *outside* the split, so the framing
    above is unchanged. The panel's width is a fixed column count rather than
    ``auto`` precisely so the details pane keeps the same width whether a run has
    transcripts or not; a run with none keeps the panel in place and shows the
    plain :data:`_NO_TRANSCRIPTS_MESSAGE` with its list hidden — the same
    hide-list/show-message posture the removed spec-017 list screen used.

    **The two panes are one keypress apart** (spec 037): ``right`` focuses the
    panel's list, ``left`` focuses the record scroller, and each focuses one
    *named* widget rather than cycling — so pressing a direction while already
    there is a no-op and focus never wraps or leaves the screen. ``up``/``down``
    are deliberately **unbound** here: Textual delivers them to whichever pane
    holds focus, so the same two keys move the highlighted game or scroll the
    figures according to where the reviewer is.

    **``t`` is a shortcut *into* the panel, not a screen of its own** (spec 037).
    Under spec 017 it ran ``action_open_transcripts`` and pushed a
    ``TranscriptListScreen``; it now runs the *same*
    :meth:`action_focus_panel` as ``right``, so the key that used to open the
    intermediate games list moves focus into the panel and pushes nothing — the
    run's figures stay visible beside it (functional-spec §2.4).

    **Picking a game opens it full-screen.** The panel's ``ListView.Selected``
    bubbles up to :meth:`on_list_view_selected` here, which resolves the
    selection through the index-parallel ``entries`` list and pushes a
    :class:`TranscriptScreen`. That screen's ``action_close`` pops back to
    *this* screen, so the reviewer returns to the figures-plus-panel view with
    the same game still highlighted — the panel's ``ListView`` is the same live
    widget across the push/pop and keeps its index. A run with no transcripts
    has no rows to pick, and its hidden list simply has nothing to select.
    """

    # Drives the Header band: the app name (so the detail view is unmistakably
    # still the ledger viewer) plus a subtitle spelling out the back keys.
    TITLE = "Graphia eval ledger"
    SUB_TITLE = "run detail · Esc / Backspace to go back · t / → for the games"

    DEFAULT_CSS = """
    DetailScreen {
        layout: vertical;
    }

    /* The horizontal split holding both panes. Sits between the Header and the
       Footer, which remain direct children of the screen. */
    #detail-split {
        height: 1fr;
        width: 1fr;
    }

    /* 1fr in a horizontal layout = "whatever the fixed-width panel leaves", so
       the details pane absorbs every terminal width change on its own. */
    #detail-scroll {
        height: 1fr;
        width: 1fr;
        border: round $panel;
    }

    /* FOCUS INDICATION (spec 037, tech-spec §2 D) — both panes carry a border
       in EVERY state and only its *colour* changes with focus. That is the whole
       point: adding a border on focus would consume two columns and two rows of
       the pane, reflowing its contents the instant focus moved, and the
       functional spec requires the layout not to jump. So the geometry is fixed
       once and only the paint changes.

       `:focus-within`, not `:focus`, and deliberately so: `#transcript-panel` is
       a `Vertical`, whose `can_focus` is False — it can never itself be the
       focused widget, so `#transcript-panel:focus` would match nothing and the
       panel would look permanently unfocused. Focus lands on the `ListView`
       *inside* it. Textual's `has_focus_within` walks up from the focused node
       and returns True when it reaches the widget itself, so `:focus-within`
       also covers `#detail-scroll` (a focusable `VerticalScroll` that holds
       focus directly) — one pseudo-class serves both panes.

       Colours come from theme variables, never literals, so the indication
       tracks the terminal's light/dark theme: `$panel` unfocused, `$accent`
       focused — exactly the pair `ui/app.py` already uses to distinguish its
       active pane's border from its inactive one, so the two Textual apps in
       this repo read the same way.

       NOT `$text-muted`, despite it being this module's precedent elsewhere:
       that precedent is for `color:`, and the two properties do not accept the
       same values. `$text-muted` resolves to `auto 60%` — an *auto* colour,
       which means "compute black or white for contrast against the background"
       and is only meaningful for text. Textual's parser rejects it as a border
       colour outright (`StylesheetParseError: Invalid value for border
       property`), and because a screen whose CSS fails to parse never mounts,
       the symptom is not a mis-drawn border but the whole DetailScreen going
       missing. `$panel` and `$accent` are concrete colours, so both parse. */
    #detail-scroll:focus-within,
    #transcript-panel:focus-within {
        border: round $accent;
    }

    #detail-body {
        padding: 0 1;
        width: 1fr;
    }

    /* THE panel width — the one place the column count lives (spec 037). A
       FIXED count, deliberately, not `auto`: the details pane must keep an
       identical width across runs that have transcripts and runs that do not,
       and a width never derived from content guarantees that by construction.
       22 columns fits the `game-01` … `game-50` label shape with room for a
       border. (A future matchup-titled label shape would own this number.)

       The 22 is INCLUSIVE of the border below (Textual sizes border-box), so the
       usable label area inside is ~20 columns. */
    #transcript-panel {
        width: 22;
        height: 1fr;
        border: round $panel;
    }

    #transcript-panel-list {
        height: 1fr;
        width: 1fr;
    }

    #transcript-panel-empty {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
        color: $text-muted;
    }
    """

    # Esc / Backspace / q pop back to the table screen — NOT quit the app.
    # Because these are screen-level bindings, they are consulted before the
    # app's escape→quit while the DetailScreen is the active screen, so the first
    # escape returns to the table and the next escape (handled by the app
    # binding) quits. Esc and Backspace are shown in the Footer ("Back"); q is the
    # quiet third option.
    BINDINGS = [
        Binding("escape", "close", "Back", show=True),
        Binding("backspace", "close", "Back", show=True),
        Binding("q", "close", "Back", show=False),
        # `t` — spec 017's transcripts key — is KEPT but RETARGETED (spec 037,
        # functional-spec §2.4): it now runs the very same `focus_panel` action
        # as `right`, so the key that used to push a `TranscriptListScreen`
        # moves focus into the panel and pushes NOTHING; the figures stay
        # visible beside the list. Still `show=True` — it remains the
        # discoverable route to a run's games — with the description naming the
        # new destination instead of the screen that no longer opens.
        Binding("t", "focus_panel", "Games", show=True),
        # PANE NAVIGATION (spec 037, tech-spec §2 C). Each key focuses ONE named
        # widget, so non-wrapping is free: `right` with the panel already focused
        # simply focuses it again, and no cycle logic exists to accidentally
        # introduce. `up`/`down` are deliberately absent — Textual routes them to
        # whichever pane holds focus, which IS the "same two keys serve both"
        # requirement; a handler here would be the bug, not the feature.
        #
        # `priority=True`, and NOT decoration — MEASURED as load-bearing. Both
        # panes are scrollable containers, and `ScrollableContainer.BINDINGS`
        # (inherited by `VerticalScroll`, and by `ListView` through it) ALREADY
        # binds `left`→`scroll_left` and `right`→`scroll_right`. Textual checks
        # the focused widget's bindings before the screen's
        # (`App._check_bindings` walks `screen._modal_binding_chain`, which is
        # `focused.ancestors_with_self`), so a plain screen binding is second in
        # line behind the pane's own.
        #
        # It appears to work anyway, because `Widget.action_scroll_right` raises
        # `SkipAction` when `allow_horizontal_scroll` is False — and that property
        # is `is_scrollable and show_horizontal_scrollbar`, i.e. it is False
        # exactly while no pane has a horizontal scrollbar, which is the case for
        # today's content. `SkipAction` makes `run_action` return False, so
        # `_check_bindings` continues down the chain and reaches us.
        #
        # That fall-through is content-dependent, and it does break. Measured with
        # the plain (non-priority) form and a horizontal scrollbar present on both
        # panes: `right` with the details focused left focus on `#detail-scroll`,
        # and `left` with the panel focused left focus on
        # `#transcript-panel-list` — the pane swallowed the key as a scroll that
        # had nothing to scroll, and focus simply stopped moving. A `priority`
        # binding is checked App-down BEFORE the focused widget, so left/right are
        # unconditionally pane navigation on this screen whatever a record's text
        # or a transcript label does to the panes' overflow. `up`/`down` carry no
        # priority and therefore stay with the focused pane, as required.
        #
        # show=False, following the `q` precedent above: the functional spec does
        # not ask for the arrows to be advertised, the Footer already carries
        # Back / Back / Transcripts / Quit, and the focused pane's accent border
        # already shows where focus is.
        Binding("right", "focus_panel", "Games", show=False, priority=True),
        Binding("left", "focus_details", "Figures", show=False, priority=True),
    ]

    def __init__(self, record: RawRecord, entries: list[TranscriptEntry]) -> None:
        """Build the screen from a record plus its **pre-resolved** transcripts.

        ``entries`` arrives already listed by the caller — the single
        ``push_screen(DetailScreen(...))`` site in :class:`LedgerTableScreen`,
        which runs the pure :func:`~graphia.eval_ledger.list_transcripts` against
        the app's ledger ``Path``. Taking them pre-resolved (the same contract
        the removed spec-017 list screen used) is what keeps this screen's
        documented "no path arithmetic of its own" invariant: it never reaches
        for ``self.app._path``.

        The list is held **index-parallel** to the panel's ``ListView`` rows, so
        a later selection resolves straight back to the right entry. An empty
        list is a normal state, not an error (an older pre-017 record, a
        missing/un-pulled dir): the panel then hides its list and shows
        :data:`_NO_TRANSCRIPTS_MESSAGE`.
        """
        super().__init__()
        self._record = record
        self._entries = entries

    def compose(self) -> ComposeResult:
        yield Header()
        # The two panes side by side. Header/Footer stay outside the split.
        with Horizontal(id="detail-split"):
            with VerticalScroll(id="detail-scroll"):
                yield Static(render_detail(self._record), id="detail-body")
            with Vertical(id="transcript-panel"):
                # One row per transcript, in the order the pure layer sorted
                # them (game-01 … game-NN). Empty when the run has none —
                # on_mount then hides this and shows the message instead.
                yield ListView(
                    *(ListItem(Label(entry.label)) for entry in self._entries),
                    id="transcript-panel-list",
                )
                yield Static(_NO_TRANSCRIPTS_MESSAGE, id="transcript-panel-empty")
        yield Footer()

    def on_mount(self) -> None:
        """Show the panel's list (or the "no transcripts" note), then focus details.

        Two jobs. First the hide-list/show-message posture reused verbatim from
        ``TranscriptListScreen`` — with the one deliberate difference that
        this does **not** focus the list. The deleted screen's ``on_mount`` ended
        in ``listing.focus()``; that must **not** be carried over, because here
        the list is a *pane*, not the whole screen.

        Second, **focus starts on the details pane** (functional-spec §2.2), so
        the reviewer's first ``up``/``down`` scrolls the figures they came to
        read. Textual's auto-focus happens to land there already — ``#detail-scroll``
        is the first focusable widget in ``compose`` order — but that is
        *incidental*: inserting any focusable widget ahead of it, or reordering
        the split, would silently move initial focus to the panel with no test
        failing at the point of the change. Naming the target makes the guarantee
        explicit and independent of composition order.
        """
        listing = self.query_one("#transcript-panel-list", ListView)
        empty = self.query_one("#transcript-panel-empty", Static)
        if not self._entries:
            listing.display = False
            empty.display = True
        else:
            empty.display = False
        # Explicit, and after the display switching so it is unconditional: the
        # details pane holds focus for a run with transcripts and one without
        # alike.
        self.query_one("#detail-scroll", VerticalScroll).focus()

    def action_close(self) -> None:
        """Pop this screen, returning to the table (which restores its cursor)."""
        self.app.pop_screen()

    def action_focus_panel(self) -> None:
        """Move focus into the transcript panel (the ``right`` binding, spec 037).

        Focuses the panel's ``ListView`` **by id**, not via ``focus_next()``, for
        two independent reasons:

        1. It is the only focusable widget in the panel — ``#transcript-panel`` is
           a :class:`~textual.containers.Vertical`, whose ``can_focus`` is False,
           so the container itself can never hold focus.
        2. On a run with **no** transcripts the list is ``display = False``, and a
           hidden widget is excluded from the screen's ``focus_chain`` (measured:
           the chain is ``[VerticalScroll(id='detail-scroll')]`` alone). A
           ``focus_next()``-style implementation would therefore skip the panel
           entirely on exactly the runs the functional spec says must still
           accept focus. Focusing by name works regardless, because
           :meth:`~textual.screen.Screen.set_focus` gates on ``Widget.focusable``
           — ``can_focus and visible`` — and ``display = False`` leaves
           ``visibility`` untouched.

        Focusing the hidden list on an empty run is the deliberate choice: the
        panel's ``:focus-within`` border still lights up (Textual walks up from
        the focused node), so the pane reads as active, and the ``ListView``'s own
        ``up``/``down`` are no-ops over zero children — "nothing happens and no
        error appears", exactly as specified.

        A no-op when the list already has focus (``set_focus`` returns early on
        the already-focused widget), which is what makes ``right`` non-wrapping
        without any guard here.
        """
        self.query_one("#transcript-panel-list", ListView).focus()

    def action_focus_details(self) -> None:
        """Move focus back to the record scroller (the ``left`` binding, spec 037).

        The mirror of :meth:`action_focus_panel`: one named widget, so pressing
        ``left`` while the details pane already holds focus focuses it again — no
        wrap, no screen change.
        """
        self.query_one("#detail-scroll", VerticalScroll).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open the picked game's transcript full-screen (spec 037).

        Moved here from the removed ``TranscriptListScreen``, which already proved the
        resolution: ``ListView.Selected`` carries the picked row's ``index``, and
        ``_entries`` is held **index-parallel** to the panel's rows, so the index
        resolves straight back to the right
        :class:`~graphia.eval_ledger.TranscriptEntry`. The message *bubbles* from
        the panel's list up through ``#transcript-panel`` / ``#detail-split`` to
        this screen (Textual delivers widget messages to their DOM ancestors), so
        the extra nesting the split introduced needs no wiring — and
        ``#transcript-panel-list`` is this screen's only ``ListView``, so there
        is nothing to disambiguate against.

        **Pushing** (rather than switching) is what makes the return trip free:
        :class:`TranscriptScreen`'s ``action_close`` pops back to this screen,
        whose ``ListView`` is the same live widget and therefore still holds its
        index — the reviewer lands back on the figures with the same game
        highlighted, and no intermediate list screen anywhere in the round trip.

        A no-op for an out-of-range index, carried over from the moved handler
        and still the right guard: a run with **no** transcripts keeps a hidden,
        empty list that can still take focus, so a selection there must do
        nothing rather than raise.
        """
        index = event.index
        if not 0 <= index < len(self._entries):
            return
        self.app.push_screen(TranscriptScreen(self._entries[index]))


# The copy shown in the transcript panel's #transcript-panel-empty when a run has
# no preserved transcripts — an older pre-017 record, a missing/un-pulled run
# dir, or an empty dir all land here (``list_transcripts`` returned ``[]``). A
# plain message, not an error (functional-spec §2.2), mirroring the table
# screen's #empty-state posture.
_NO_TRANSCRIPTS_MESSAGE = "No transcripts for this run."

# ===========================================================================
# Transcript syntax highlighting (spec 038) — THE kind → style map.
#
# :func:`~graphia.eval_ledger.tokenize_transcript` splits a game into
# ``(text, kind)`` spans where each ``kind`` names a *semantic* role and never a
# colour (``marker`` / ``attr`` / ``field-label`` / ``speaker`` / ``speech`` /
# ``thought`` / ``recap`` now; the side-bearing kinds in Slice 4 — see
# :data:`~graphia.eval_ledger.TRANSCRIPT_KINDS` for the canonical vocabulary).
# **This dict is where those semantics become an appearance**, and it is the one
# place a later slice (or a reviewer adjusting the palette) has to look: one
# entry per styled kind, one CSS rule per entry.
#
# The value is a Textual **component-class** name rather than a Rich style
# literal, because the appearance has to come from the *theme*:
#
# - functional-spec §2 requires legibility on both dark and light terminals, and
#   a hard-coded colour cannot satisfy that. A component class is styled in
#   ``TranscriptScreen.DEFAULT_CSS`` with the same ``$…`` theme variables the
#   rest of this module uses, and Textual resolves it against the *live* theme.
# - it is the only route that can resolve ``$text-muted`` at all. That variable
#   is ``auto 60%`` — "compute a contrasting colour against the background, at
#   60% alpha" — which only Textual's CSS engine can turn into a real colour
#   (``#a0a0a0`` on ``textual-dark``, ``#595959`` on ``textual-light``).
#   ``rich.style.Style.parse("auto 60%")`` cannot. This is the *other* half of
#   spec 037's lesson: ``$text-muted`` is rejected for ``border:`` and correct
#   for ``color:``, which is all we set here.
#
# A kind with **no entry here renders unstyled** (see ``_kind_styles``) rather
# than raising, so a tokenizer that learns a new kind before this map does
# degrades to plain text — which is also why ``plain`` is deliberately absent:
# "everything else" needs no style, and its absence is the fallback working.
#
# THE PALETTE IS NOW FULLY SPENT (see the CSS below for each rule's own
# reasoning). Measured on the installed Textual 8.2.4 against both builtin
# themes' real backgrounds, only five theme variables clear the 4.5:1 WCAG AA
# floor on BOTH: ``$text`` (14.19 / 12.77), ``$text-primary`` (7.03 / 10.69),
# ``$text-muted`` (7.16 / 5.31), ``$text-error`` (6.34 / 7.29) and
# ``$text-secondary`` (4.67 / 6.39). ``$text-accent`` (3.27 light) and
# ``$text-success`` (3.77 light) fail, which is why the obvious red/green side
# pairing is unavailable. Three went to structure in Slices 1-2 (``$text`` on the
# body and ``attr``, ``$text-muted`` on ``marker``, ``$text-secondary`` on
# ``field-label``); Slice 3's four kinds were therefore separated on axes OTHER
# than hue — ``text-style``, which survives ``_kind_styles`` (verified) and,
# unlike a colour, survives the ``textual-ansi`` theme too. **Slice 4 spends the
# last two on the two sides**: ``$text-error`` = Mafia, ``$text-primary`` =
# Law-abiding. Nothing is left over, so a later kind needing to be *seen* rather
# than *coloured* must reach for ``text-style`` as Slice 3 did (Slice 5's bold
# already does). **Spec 039 is the first kind added from outside spec 038, and
# it took exactly that route:** its ``diary`` body reuses ``thought``'s
# ``$text-muted`` unchanged and separates itself on ``underline``.
#
# ...AND SPEC 041 SPENDS THE LAST COMFORTABLE ``text-style`` SLOT, which is the
# same accounting one axis over and is recorded here for the same reason: the
# fallback route the paragraph above hands to "a later kind" is now nearly gone
# too, and the next kind will need to hear that from this comment rather than
# rediscover it. Textual's ``VALID_STYLE_FLAGS`` offers eight usable flags;
# ``bold`` (Slice 5's seat), ``italic`` (``thought``), ``underline`` (``diary``),
# ``reverse`` (``recap``) and now ``strike`` (``diary-substituted``) are spent,
# and the three survivors are all compromised — ``uu`` (SGR 4:2, far thinner
# terminal support than SGR 4's), ``dim`` (a second intensity reduction on a
# variable already ``auto 60%``, unmeasurable from inside the app and the wrong
# thing to bet a 4.5:1 floor on) and ``blink``. A ninth flag only *looks*
# available: ``overline`` is in ``VALID_STYLE_FLAGS`` and parses in the
# stylesheet, but it is MEASURED DEAD at both downstream hops on the installed
# Textual 8.2.4 — ``rich.style.Style.parse("overline")`` returns an empty
# ``Style()``, and ``textual.style.Style`` has no ``overline`` field for
# ``from_rich_style`` to carry — so the declaration would read correctly in this
# stylesheet and paint nothing on screen. So a kind arriving after this one
# cannot assume either axis is free: it must either compose two spent flags
# (which is what ``diary-substituted`` itself does — see its rule) or separate
# itself on something this stylesheet has not used yet at all.
#
# ...AND SLICE 9 IS THE FIRST KIND TO TAKE THAT FIRST ROUTE RATHER THAN SPEND
# ANYTHING, which is the accounting one step further and the reason this note is
# extended rather than repeated. ``thought-substituted`` REUSES ``strike`` on a
# second kind; it does not spend a new flag, and the ledger above is therefore
# unchanged by it. What makes the reuse honest is that ``strike`` means the same
# thing on both kinds — *this body is not the player's writing* — so the flag
# has one meaning across the stylesheet rather than two, and the two marked
# kinds are told apart by the flags their HONEST forms already differ by
# (``diary`` carries ``underline``, ``thought`` does not). A flag reused for a
# second, unrelated meaning would be the thing to refuse; a marking family
# sharing one flag is the thing the family is for. Slice 10's speech marking
# takes the same route again, so the eight-flag budget above holds for all
# three channels.
#
# ...AND SLICE 10 CLOSES THE LEDGER WITH A THIRD REUSE AND NO NEW SPEND, which
# is the accounting complete rather than a fourth instalment. ``speech-
# substituted`` REUSES ``strike`` on a third kind; ``bold`` (the seat),
# ``italic`` (``thought``), ``underline`` (``diary``), ``reverse`` (``recap``)
# and ``strike`` (the marking family) remain the five spent flags, and the
# three compromised survivors (``uu``, ``dim``, ``blink``) plus the
# measured-dead ``overline`` are still the whole of what a later kind can
# reach for. **So the entire marking family cost the budget exactly one flag**
# — which is the strongest form of the one-meaning-per-flag rule this ledger
# exists to keep: three channels, one flag, one meaning ("this body is not the
# player's writing"), and the three marked kinds separated by the flags their
# HONEST forms already differ by.
#
# THE THREE-WAY SEPARATION, written out because it is what a reader will want
# to check: ``diary-substituted`` is ``italic underline strike``,
# ``thought-substituted`` is ``italic strike``, ``speech-substituted`` is
# ``strike`` alone. Each is its own honest rule plus the one flag, and the
# stack shrinks as the honest forms do — so the LAST marked kind is the
# PLAINEST of the three, and it is the only one outside the muted family
# (``$text``, not ``$text-muted``). That makes speech the easiest of the three
# to keep distinguishable, exactly the opposite of what the channel's
# prominence would suggest: under ``textual-ansi`` the two private kinds
# collapse to the terminal default and rely on their flags alone, and this one
# collapses there too — but it is separated from BOTH of them by the absence of
# ``italic``, which no theme can take away.
#
# SIX SIDE-BEARING KINDS, NOT TWO — two hues, but ``speaker``, ``speech`` and
# ``attr`` each split two ways, so six entries and six rules. The neutral
# ``speaker`` / ``speech`` / ``attr`` above are UNTOUCHED by that: Slice 4 adds,
# it does not edit. They survive as the appearance of a line whose side is
# *unknown* (a pre-022 game, or a name absent from ``<setup>``), which is how
# "never guess a side" and "the neutral is the body colour" become one rule.
_TRANSCRIPT_KIND_COMPONENTS: dict[str, str] = {
    KIND_MARKER: "transcript--marker",
    KIND_ATTR: "transcript--attr",
    KIND_FIELD_LABEL: "transcript--field-label",
    KIND_SPEAKER: "transcript--speaker",
    KIND_SPEECH: "transcript--speech",
    KIND_THOUGHT: "transcript--thought",
    KIND_RECAP: "transcript--recap",
    # The side-bearing kinds (Slice 4). Each is the qualified form of the neutral
    # kind directly above it, and it is the KIND that names the side — the
    # component class only carries that name through to a CSS rule, where the
    # hue is finally chosen. So the semantics-never-colours rule the tokenizer
    # keeps (`speaker-mafia` is a kind, `speaker-red` never is) survives all the
    # way to the last hop: `transcript--speaker-mafia`, not `transcript--red`.
    KIND_SPEAKER_MAFIA: "transcript--speaker-mafia",
    KIND_SPEAKER_LAW_ABIDING: "transcript--speaker-law-abiding",
    KIND_SPEECH_MAFIA: "transcript--speech-mafia",
    KIND_SPEECH_LAW_ABIDING: "transcript--speech-law-abiding",
    KIND_ATTR_MAFIA: "transcript--attr-mafia",
    KIND_ATTR_LAW_ABIDING: "transcript--attr-law-abiding",
    # The reviewer's OWN SEAT (Slice 5) — the last kinds spec 038 adds. Each is
    # the qualified form of the dialogue kind it sits under, and each rule below
    # is that kind's rule plus `text-style: bold` and nothing else: the seat is
    # marked *within* its side's colour, never with a hue of its own.
    #
    # SIX, NOT TWO, and the neutral pair is not padding. Side and seat are
    # INDEPENDENT facts a file can state separately — a side comes from the
    # `<setup>` cast list, a seat from the `human="true"` marker or the
    # preamble's welcome line — so `speaker-human` / `speech-human` is "the seat
    # whose side is unknown", exactly as `speaker` / `speech` is "a line whose
    # side is unknown". That is the whole of the pre-spec-022 story: all 30 of
    # those files name their seat in the preamble AND yield an empty cast map,
    # and 183 + 183 real corpus spans live in the neutral pair as a result.
    # Folding the axes together would have to discard a fact the file states.
    #
    # `attr` gains no human form, deliberately: the seat's cast entry and its
    # `<thought player="…">` owner name are already `attr-mafia` /
    # `attr-law-abiding`, both of which KEEP `attr`'s bold, so a
    # bold-within-the-side would be invisible there. Functional-spec §2 scopes
    # the requirement to that seat's *lines* in any case.
    KIND_SPEAKER_HUMAN: "transcript--speaker-human",
    KIND_SPEECH_HUMAN: "transcript--speech-human",
    KIND_SPEAKER_MAFIA_HUMAN: "transcript--speaker-mafia-human",
    KIND_SPEECH_MAFIA_HUMAN: "transcript--speech-mafia-human",
    KIND_SPEAKER_LAW_ABIDING_HUMAN: "transcript--speaker-law-abiding-human",
    KIND_SPEECH_LAW_ABIDING_HUMAN: "transcript--speech-law-abiding-human",
    # The day's diary (spec 039) — the twenty-first kind, and the first added by
    # a spec other than 038. APPENDED, exactly as `TRANSCRIPT_KINDS` appends it:
    # one constant there, one entry here, one rule below, and not a single
    # existing kind, class or rule moved. That the house pattern held for a kind
    # arriving from another spec is the pattern's first real test.
    KIND_DIARY: "transcript--diary",
    # A SUBSTITUTED diary body (spec 041) — the twenty-second kind, and the
    # first that says nothing about WHO wrote a line and everything about
    # WHETHER anybody did. Appended by the same route spec 039 proved: one
    # constant in `eval_ledger`, one entry here, one rule below, nothing moved.
    #
    # It is a second BODY kind rather than a second treatment of the tag: the
    # `substituted="true"` flag's own value is already lifted out as an ordinary
    # bold `attr` span by `_ATTR_NAMES`, so the tag-line axis needs no entry
    # here at all. This entry is the body axis — the second of the two the
    # `thought` rule's "TWO AXES, NOT ONE" precedent asks for, and one axis is
    # one thing to lose.
    KIND_DIARY_SUBSTITUTED: "transcript--diary-substituted",
    # A SUBSTITUTED THOUGHT body (spec 041 Slice 9) — the twenty-third kind, and
    # the second of the marking family. Same route again: one constant in
    # `eval_ledger`, one entry here, one rule below, nothing moved.
    #
    # ITS OWN CLASS RATHER THAN A REUSE OF `transcript--diary-substituted`, and
    # that is the point rather than the paperwork: the two marked kinds must stay
    # tellable apart, because a reviewer asking "was this stood in for?" is
    # usually also asking "which channel?" — a round's reaction or a whole Day's
    # settled read. Each derives from ITS OWN honest rule (`thought` here,
    # `diary` there), so the pair differs by exactly the flag their honest forms
    # differ by, and no third look is invented.
    #
    # This is the channel that matters most, measured: 10,706 thought bodies in
    # the committed corpus against 376 diary ones.
    KIND_THOUGHT_SUBSTITUTED: "transcript--thought-substituted",
    # A SUBSTITUTED DAY-SPEECH body (spec 041 Slice 10) — the twenty-fourth
    # kind, the third and last of the marking family, and the only one whose
    # LINE is a different shape from its honest twin's: an authored line is the
    # bare `Speaker: text` the tokenizer splits into `speaker` + `speech`, a
    # substituted one is a `<speech speaker="…" substituted="true">…</speech>`
    # element. Same route as the other two: one constant in `eval_ledger`, one
    # entry here, one rule below, nothing moved.
    #
    # ONE CLASS, NOT THREE, AND THE SIDE IS NOT LOST — it moves. Speech is the
    # only channel in the family whose honest forms are side-bearing
    # (`speech-mafia` / `speech-law-abiding` / the neutral), so this is the
    # first marked kind where "does it keep its side tint?" had a real answer
    # either way. The body does not: colour has meant *side* since Slice 4, a
    # side is a fact about the SPEAKER, and the words of a substituted line have
    # no author to have a side. The speaker's NAME carries it instead, tinted
    # `attr-mafia` / `attr-law-abiding` on the wrapping tag exactly as a
    # `<thought player=…>` owner's is — so a reviewer still places the line at a
    # glance while the prose stays outside the side palette.
    #
    # Three classes would also have broken a structural invariant rather than
    # merely tripling: `speaker` and `speech` are INDEX-ALIGNED families ("a
    # speaker and their words carry the same side and the same seat"), and a
    # wrapped line has no speaker span at all — so a `speech-mafia-substituted`
    # with no `speaker-mafia-substituted` twin would leave that pairing
    # unpairable.
    #
    # NO HUMAN FORM, and by construction rather than by omission:
    # `nodes.day._ai_day_action` is the only writer of the flag and the human
    # branch never calls it, so `substituted="true"` cannot co-occur with the
    # reviewer's seat. The human path's own `"(stays silent.)"` is deliberately
    # unmarked — it stands in for a person's decision to type nothing, not for
    # an answer that failed to decode.
    KIND_SPEECH_SUBSTITUTED: "transcript--speech-substituted",
}


# ===========================================================================
# THE SEARCH HIGHLIGHT (spec 044) — two component classes that are NOT kinds,
# and the one style axis the ledger above leaves unspent.
#
# They sit BESIDE `_TRANSCRIPT_KIND_COMPONENTS` rather than inside it, because
# they answer a different question about the same character. That map says what
# a span *is* — a Mafioso's speech, a private thought, a section marker — and it
# is keyed on the tokenizer's own vocabulary, which is what lets the suite
# assert `set(mapping) == TRANSCRIPT_KINDS - {plain}` and catch a kind that
# never got a rule. A MATCH IS NOT A KIND: any of the twenty-four can carry one,
# the tokenizer knows nothing about it, and it appears and disappears as the
# reviewer types. An entry in that dict would both break the completeness
# equality and claim a semantics `tokenize_transcript` never emits.
#
# TWO CLASSES BECAUSE THERE ARE TWO STATES, and the second is load-bearing
# rather than decorative: `transcript--match` is every match the reviewer can
# see, `transcript--match-current` is the one `n`/`N` have landed on (Slice 4).
# Inside the last screenful of a game successive jumps cannot scroll — there is
# nothing past the end of the file to scroll into view — so the stronger mark
# and the status count are the reviewer's only evidence that a press did
# anything at all.
#
# The rules live in `TranscriptScreen.DEFAULT_CSS` (see them for why the hue is
# forced and what it costs), and `TranscriptScreen._match_styles` — **not**
# `_kind_styles`, which strips backgrounds by design — is what turns them into
# Rich styles.
_MATCH_COMPONENT = "transcript--match"
_MATCH_CURRENT_COMPONENT = "transcript--match-current"
_TRANSCRIPT_MATCH_COMPONENTS: tuple[str, ...] = (
    _MATCH_COMPONENT,
    _MATCH_CURRENT_COMPONENT,
)

# HOW LONG TYPING MUST SETTLE BEFORE THE GAME IS REPAINTED (spec 044, tech-spec
# §2.8). Seconds, because that is the unit `MessagePump.set_timer` takes.
#
# The tech spec asks for 50-120 ms and leaves the choice here. 80 ms, for two
# reasons that bound it from opposite sides:
#
# - FROM ABOVE, PERCEPTION. Functional-spec §2.2 says matches light up
#   "immediately", and ~100 ms is the long-standing threshold under which a
#   response reads as instantaneous rather than as lag. 80 ms leaves the repaint
#   itself a little room inside that budget instead of spending all of it
#   waiting.
# - FROM BELOW, THE COST THIS EXISTS TO COALESCE. A repaint of the 88 KB
#   committed game measures ~59 ms for `vote` and ~146 ms for a single `e`
#   (`TranscriptBody`'s docstring re-measures tech-spec §2.8 against this
#   implementation), and it runs ON THE EVENT LOOP — so every key the reviewer
#   presses during one queues behind it and they all arrive together when the
#   loop frees. Each restarts this timer and only the last survives, which is
#   the whole trick; but a window shorter than that burst takes to drain would
#   fire mid-drain and repaint a query the reviewer has already typed past.
#   80 ms clears the drain with room to spare.
#
# The alternative is no debounce at all, and it is not viable: tech-spec §2.8
# measures 336 ms PER KEYSTROKE for the worst query (a single space, 14,524
# matches) because `Widget._render_content` re-renders all 1,182 rendered rows
# rather than the 22-row viewport. Windowing the highlight to the viewport was
# the other candidate and was rejected on measurement — it saves only the
# `stylize` component (<=6 ms) and would move the 32-113 ms re-render onto every
# scroll step, where functional-spec §2.4 promises the game stays cheap to
# scroll while a search is live.
#
# A MODULE CONSTANT RATHER THAN A LITERAL so a test can wait on this window by
# name instead of on a magic number that drifts away from the code the day
# someone re-tunes it.
_SEARCH_DEBOUNCE_SECONDS = 0.08

# THE NO-MATCH READOUT for #search-count (spec 044, functional-spec §2.8 AC2 —
# a query that appears nowhere in the game "says there are no matches").
#
# Deliberately does NOT echo the query back, where the runs list's
# :func:`_no_match_message` does. That one is the whole of a screen-filling
# empty state and has to say what filtered the table away; this one sits one
# character to the right of the box still holding the query, so repeating it
# would spend the row's width telling the reviewer what they are already
# looking at. Same reason it carries no full stop: this is a readout beside an
# input, in #match-count's "Showing X of N" register, not a sentence in an
# empty pane.
_NO_MATCHES_MESSAGE = "No matches"


def _search_count_text(query: str, matches: int) -> str:
    """The #search-count copy for a settled query and its match total.

    Three states, and the empty one is why this is a function rather than an
    f-string at the call site:

    - **Nothing typed, nothing said.** An empty query is not a search that found
      zero things, it is a reviewer who has not asked yet, so the strip stays
      blank rather than reading ``0 matches`` at them.
      :func:`~graphia.eval_ledger.find_matches` returns ``[]`` for an empty
      query, so the count alone cannot tell those two apart — the query is what
      separates them, which is why it is a parameter at all.
    - **No matches**, plainly (:data:`_NO_MATCHES_MESSAGE`, functional-spec
      §2.8 AC2).
    - **Otherwise the total**, singular at one so the readout reads as English
      rather than as ``1 matches``.

    Pure and module-level, beside this module's other copy, so what the strip
    says is checkable without a terminal.

    This is the WHOLE of the readout, on purpose. Functional-spec §2.8
    AC3 — which match of the total the reviewer is currently on — does not
    belong here: it has to outlive the prompt, so its channel is
    :attr:`Screen.sub_title` (tech-spec §2.7) and its copy is
    :func:`_search_status_text`, not a Static that is hidden with the bar.
    """
    if not query:
        return ""
    if matches == 0:
        return _NO_MATCHES_MESSAGE
    return f"{matches} match" if matches == 1 else f"{matches} matches"


# HOW MUCH OF THE QUERY THE HEADER WILL SHOW (spec 044, Task 4.5).
#
# The header is ONE row, `text-wrap: nowrap` with `text-overflow: ellipsis`
# (`HeaderTitle.DEFAULT_CSS` on the installed Textual 8.2.4), and the viewer
# name plus the " — " that `App.format_title` puts between title and sub-title
# already spend 22 of its columns. Ellipsis cuts the END — so an unbounded query
# would push the POSITION off the right edge, and the position is the one thing
# functional-spec §2.8 AC3 actually owes the reviewer and the one thing they
# cannot read anywhere else once the prompt has closed.
#
# So the query is the half that gives way, and it is the right half to lose:
# `/` re-opens the prompt on the search the reviewer already has (see
# `action_open_search`), so the full text they typed is one keypress away, where
# the position exists nowhere but here.
#
# 32 is past any real search phrase and still fits the query, the position and
# the wrap notice together: 22 for the title and its separator, 32 for the
# query, and 20 for the widest plausible " · 999/999 (wrapped)" — 74 columns.
#
# AND THE ROW IS 82 COLUMNS WIDE, NOT 100 — corrected in Task 6.2, which
# MEASURED the row rather than deriving it from the terminal width as this note
# originally did ("74 of 100"). `Header` docks a `HeaderIcon` (`width: 8`) on
# the left and a `HeaderClockSpace` (`width: 10`) on the right, so `HeaderTitle`
# gets `terminal - 18`: 82 at the tests' 100 columns, 62 at 80. 74 of 82 still
# fits, so the cap itself was never wrong — only the headroom it was believed to
# have, which is 8 columns, and which is why `_SEARCH_STATUS_KEY_HINT` below is
# placed where the ellipsis reaches it first.
_SEARCH_STATUS_QUERY_MAX = 32


# WHAT THE STATUS CALLS THE KEYS THAT MOVE BETWEEN MATCHES (spec 044, Task 6.2).
#
# Functional-spec §2.8, amended 2026-09-14: "The running status names the keys
# that move between matches, so the reviewer learns them at the moment they are
# useful rather than having to know them in advance." Slice 4 assumed this was
# already happening and hid `n`/`N` from the Footer on the strength of it; it
# was not, and the author could not find the keys at all (see the note on
# `TranscriptScreen.BINDINGS`).
#
# BOTH KEYS AND BOTH DIRECTIONS, not `n/N`. Naming the pair without saying which
# way each goes leaves the reviewer to guess, and the guess costs them a press
# in the wrong direction — the shorter form saves 2 columns and answers half the
# criterion.
#
# `·` is the module's established separator (both `SUB_TITLE` constants use it,
# and `_search_status_text` already puts one between query and position), so the
# hint reads as one more field rather than as a different kind of thing.
#
# IT GOES LAST, AFTER `(wrapped)`, AND THAT IS THE WIDTH ANSWER. `HeaderTitle`
# is `text-overflow: ellipsis`, which cuts the END — so whatever sits last is
# what a narrow terminal spends first. The position is a §2.8 acceptance
# criterion and exists nowhere else once the prompt has closed; the hint is a
# convenience the Footer now also carries. Ordering the string this way is what
# makes "drop the hint before dropping the position" a property of the layout
# rather than a rule someone has to remember.
#
# MEASURED at the tests' 100-column terminal, by rendering `HeaderTitle` and
# sweeping the query length rather than by counting characters:
#
# - The realistic status `the · 1/702 · n next · N prev` is 29 columns; the
#   wrapped form is 39. The budget is 60 (82 for `HeaderTitle`, less 22 for the
#   viewer name and `App.format_title`'s " — ").
# - WITHOUT `(wrapped)` nothing truncates, even at the 32-character cap: the
#   longest such status is exactly 60.
# - WITH `(wrapped)` the tail is first cut at a 23-character query (against a
#   three-digit position such as `702/702`; 26 against `3/18`) — and what is cut
#   is the HINT, with the whole `… · 702/702 (wrapped) · n ne…` position intact,
#   because the hint is last. That is the outcome this ordering was chosen for.
_SEARCH_STATUS_KEY_HINT = "n next · N prev"


def _search_status_text(
    query: str, total: int, current: int | None, *, wrapped: bool
) -> str | None:
    """The header sub-title for a search the reviewer has LANDED in — or ``None``.

    Functional-spec §2.8 AC3 ("they can tell which match of the total they are
    on") and §2.6's two wrap criteria ("the reviewer is told when a wrap
    happens") both have to be answered **after the prompt is gone**: Enter hides
    the search row, and ``n``/``N`` exist only in the state it leaves behind. So
    neither can be served by ``#search-count``, which is visible only while the
    bar is open. :attr:`Screen.sub_title` is the channel (tech-spec §2.7) — a
    ``Reactive`` that the already-composed :class:`~textual.widgets.Header`
    watches (``Header._on_mount`` calls ``self.watch(self.screen, "sub_title",
    …)`` on the installed Textual 8.2.4), so writing it re-renders the header
    live and costs **zero extra rows**, which is what §2.10's promise about the
    reading area requires.

    ``None`` means "say nothing of your own" — the caller puts
    :attr:`TranscriptScreen.SUB_TITLE` back.

    **It renders :attr:`SearchState.current`, and nothing else — which is the
    deliberate answer to "what does the header show before any jump?"** While
    the reviewer is typing there is no landing, so this returns ``None`` and the
    header keeps the back-out hint it has always had. That is a choice, not an
    omission, and it rests on three things:

    - **The typing half of §2.8 is already discharged, in a better place.** The
      query is in the box under the reviewer's cursor and the count sits one
      character to its right (:func:`_search_count_text`, §2.8 AC1/AC2).
      Repeating both in the header would spend the row saying what is already in
      front of them.
    - **The header row is not free.** It is the only place the back-out keys are
      written down, and while a search is live Esc does *not* leave the game
      (§2.9's three states) — so overwriting the hint would buy a duplicate and
      sell a hint at the moment it starts to matter.
    - **A per-keystroke status would have to race the highlight.** The repaint is
      debounced (tech-spec §2.8), so a header written from ``Input.Changed``
      would announce a query the game is not yet showing — the drift
      :meth:`TranscriptScreen._update_search_count` is deliberately routed
      through one path to avoid. Rendering ``current`` keeps this on that same
      settled path.

    **The format is ``vote · 3/18 · n next · N prev``**, with ``(wrapped)``
    between the position and the hint when the jump came round the end. Four
    details in it are decisions:

    - **``·`` rather than an em dash**, though tech-spec §2.7 sketches one:
      ``App.format_title`` already inserts ``" — "`` between the viewer name and
      this string, so a second em dash inside it would read as a second title
      break. ``·`` is what both of this module's ``SUB_TITLE`` constants already
      use as their internal separator.
    - **``3/18`` is a POSITION, never a press count**, and the slash notation is
      what makes that legible. §2.6 has ``n`` skip every match sharing the
      reviewer's rendered row, so this number legitimately jumps — 4 straight to
      7 where one row holds three matches — and the author accepted that
      explicitly. "Which of" survives a jump; anything that read as "how many
      presses" (a step counter, ``next``/``previous`` wording) would look broken
      the first time one happened.
    - **``(wrapped)`` verbatim from tech-spec §2.7**, and it is sometimes the
      *entire* feedback a press produces: inside the last screenful of a game
      the scroll clamps at ``max_scroll_y``, so successive landings move nothing
      at all and only this string and the stronger match marker say the key did
      anything (functional-spec §2.5's end-of-game bullet).
    - **The key hint is LAST** (:data:`_SEARCH_STATUS_KEY_HINT`, spec 044 Task
      6.2), which is both what §2.8's amended criterion asks for — "the running
      status names the keys that step to the next and to the previous match" —
      and the width answer: ``HeaderTitle`` truncates with an ellipsis at the
      END, so the last field is the first one a narrow terminal spends, and the
      position that sits before it survives. It appears only with a landing,
      because the ``current is None`` return above is taken first.

    Pure and module-level, beside this module's other copy, so what the header
    says is checkable without a terminal.

    **Square brackets in the query are safe**, for a different reason than
    :class:`TranscriptBody`'s ``markup=False``: ``App.format_title`` builds
    ``Content(sub_title)``, and a ``Content`` constructed from a plain ``str`` is
    literal text — markup is parsed only by ``Content.from_markup``. So a
    reviewer searching for ``[`` gets a header, not a traceback.

    Args:
        query: What the reviewer typed. Truncated at
            :data:`_SEARCH_STATUS_QUERY_MAX` — see the constant for why the
            query is the half that gives way.
        total: How many matches that query has in the whole game.
        current: Which of them the reviewer is on, or ``None`` when they are on
            none — typing, nothing typed, nothing matched, or no search at all.
        wrapped: Whether the jump that landed on ``current`` wrapped round the
            end (or the start) of the game. Meaningless without a landing, and
            unread when ``current`` is ``None``.

    Returns:
        The sub-title to show, or ``None`` when the search has nothing to say
        that the search row is not already saying better.
    """
    # NO LANDING, NOTHING TO SAY. `total == 0` cannot happen with a `current`
    # set — an edit that empties the match list drops the index in the same
    # breath (`TranscriptScreen.on_input_changed`) — but "1/0" is a bad enough
    # readout to be worth one `or`.
    if current is None or total == 0:
        return None
    shown = (
        query
        if len(query) <= _SEARCH_STATUS_QUERY_MAX
        else query[: _SEARCH_STATUS_QUERY_MAX - 1] + "…"
    )
    # `current` is a zero-based index and the reviewer is being told a position,
    # so it is counted from one: the first match in the game reads "1/18".
    status = f"{shown} · {current + 1}/{total}"
    if wrapped:
        status = f"{status} (wrapped)"
    # THE KEY HINT IS LAST, AND ONLY EVER REACHED WITH A LANDING IN HAND — the
    # early return above means this line runs only when `current` is set, which
    # is exactly §2.8's "given the reviewer has jumped to a match". See
    # `_SEARCH_STATUS_KEY_HINT` for the copy and for why the tail is the right
    # place for it.
    return f"{status} · {_SEARCH_STATUS_KEY_HINT}"


class TranscriptSearchInput(Input):
    """The transcript search bar's input, with the six scroll keys passed through.

    Functional-spec §2.4 asks that the game keep scrolling while the reviewer is
    typing: ``up``, ``down``, ``pageup``, ``pagedown``, ``home`` and ``end`` must
    move :class:`TranscriptScreen`'s ``#transcript-scroll`` and leave the typed
    query untouched. This subclass is the whole mechanism.

    **Why the bindings live HERE and not on the screen.** Putting them on
    :class:`TranscriptScreen` is the obvious simplification, and it is MEASURED
    DEAD for half of them. Textual resolves a key against
    ``screen._modal_binding_chain`` — ``focused.ancestors_with_self`` — so the
    focused widget's own bindings are consulted BEFORE the screen's, and
    ``Input.BINDINGS`` on the installed Textual 8.2.4 claims ``home,ctrl+a`` →
    ``action_home`` and ``end,ctrl+e`` → ``action_end``. A screen-level
    ``home``/``end`` binding therefore never runs while this box has focus:
    measured ``scroll_offset.y`` ``67 → 67`` on both, no movement at all.

    Subclass ``BINDINGS`` *do* beat the base class's, which is what makes this
    placement work: ``DOMNode._merge_bindings`` walks the reversed MRO and merges
    per KEY (``keys[key] = key_bindings``), and by then ``"home,ctrl+a"`` has
    already been split into two separate entries — so the ``home`` declared here
    replaces ``Input``'s while ``ctrl+a`` still reaches ``action_home`` and
    start-of-line editing of the query survives.

    **All six are declared even though ``Input`` claims only two**, and that is
    not redundancy — the other four are dead for a *different* reason. ``Input``
    does not bind ``up``/``down``/``pageup``/``pagedown`` at all, so they bubble
    freely; but the scroller is this widget's SIBLING, not its ancestor (inside
    the scroller the bar would scroll away with the text), so there is nothing up
    the chain to catch them either. One uniform declaration here is both halves
    of the fix. Measured with it in place: ``down 44→45``, ``pagedown 45→66``,
    ``up 66→65``, ``pageup 65→44``, ``home 44→0``, ``end 0→1161`` — with
    ``Input.value`` unchanged throughout.

    Two consequences worth writing down, because both are things a future reader
    would otherwise add back:

    - **No ``priority=True`` anywhere.** A priority binding is checked App-down,
      BEFORE the focused widget — which is exactly what :class:`DetailScreen`'s
      pane arrows need, and exactly wrong here: it would hijack all six keys even
      while the scroller itself has focus and this bar is closed, breaking plain
      reading for someone who never presses ``/`` (functional-spec §2.10).
    - **No ``action_home``/``action_end`` overrides.** Shadowing the *binding* is
      enough, and it is the narrower cut: overriding the actions would also
      strand ``ctrl+a``/``ctrl+e``, which legitimately belong to the query.

    Every other key keeps its ``Input`` meaning, which is both what §2.4 asks for
    and what keeps the screen's back-out keys from firing mid-search: ``q``,
    ``n``, ``N`` and ``/`` are typed into the query, ``backspace`` deletes a
    character, ``enter`` submits, and ``escape`` is claimed by neither ``Input``
    nor this class, so it still reaches the screen.

    Like :class:`SearchInput` above, this widget **announces rather than acts**:
    it posts :class:`ScrollGame` instead of reaching for ``#transcript-scroll``
    itself, keeping it decoupled from the scroller's id the same way
    ``SearchInput`` is decoupled from the field selector's.
    """

    class ScrollGame(Message):
        """Posted when one of the six scroll keys is pressed in the search bar.

        The screen handles it by running :attr:`scroll_action` on
        ``#transcript-scroll`` — it owns the id, this widget does not.
        """

        def __init__(self, scroll_action: str) -> None:
            """Name the action to run on the game's scroller.

            Args:
                scroll_action: The action's name on the scroller itself —
                    ``scroll_up`` / ``scroll_down`` / ``page_up`` / ``page_down``
                    / ``scroll_home`` / ``scroll_end``, copied verbatim from
                    ``ScrollableContainer.BINDINGS`` (so the screen's handler is
                    a one-liner and no translation table exists to drift).
            """
            super().__init__()
            self.scroll_action = scroll_action

    # Each binding re-posts the scroller's OWN action for that key, so "what does
    # pageup do in the search bar?" has the same answer as "what does pageup do
    # in the game?" by construction rather than by a mapping someone maintains.
    #
    # `show=False` throughout: these are not Footer material — they restore keys
    # the reviewer already expects to work, and the Footer's single new entry is
    # the `/` that opens this bar in the first place.
    BINDINGS = [
        Binding("up", "scroll_game('scroll_up')", show=False),
        Binding("down", "scroll_game('scroll_down')", show=False),
        Binding("pageup", "scroll_game('page_up')", show=False),
        Binding("pagedown", "scroll_game('page_down')", show=False),
        Binding("home", "scroll_game('scroll_home')", show=False),
        Binding("end", "scroll_game('scroll_end')", show=False),
    ]

    def action_scroll_game(self, scroll_action: str) -> None:
        """Announce a scroll of the game, without touching the typed query.

        Does no scrolling and no querying of its own — see the class docstring
        for why the id stays on the screen. Leaving ``value`` alone is the other
        half of functional-spec §2.4: scrolling mid-search must cost the reviewer
        nothing they have already typed.
        """
        self.post_message(self.ScrollGame(scroll_action))


class TranscriptBody(Static):
    """The game's text, with custody of the unhighlighted base and a layout-free swap.

    A plain :class:`~textual.widgets.Static` would do everything this widget does
    except the two things spec 044's live search needs, and both are stated here
    rather than left to the call site because both are the kind of thing a later
    "simplification" removes without noticing anything break.

    **1. THE BASE ``Text`` LIVES HERE, AND NOTHING OUTSIDE CAN REACH IT.**
    :attr:`_base` is the styled renderable
    :meth:`TranscriptScreen._styled_body` builds **once**, at ``compose`` — the
    tokenized, kind-coloured game with no search highlight on it. Every update
    works on ``self._base.copy()`` and the base itself is **never** stylized.

    That is not fastidiousness, it is the difference between a search that works
    and one that gets worse with every keystroke. ``Text.stylize`` **appends** to
    ``_spans`` (read from the installed Rich 15.0.0) and never removes, so
    stylizing the base would leave last keystroke's highlights on it forever:
    typing ``v``-``o``-``t``-``e`` would paint the matches for ``v``, then those
    *plus* the matches for ``vo``, and nothing the reviewer typed would ever stop
    being highlighted. The copy is what makes each update a fresh statement
    rather than an accumulation, and it is sound for the same reason — Rich's
    ``Text.copy`` does ``copy_self._spans[:] = self._spans``, a **fresh list**
    built by slice-assignment, so appending to the copy cannot reach the
    original. (A shallow ``copy.copy`` would share the list and would be exactly
    the bug this paragraph rules out.)

    Making the base **private to this widget** is what turns "never stylize the
    base" from a rule someone has to remember into one there is no handle to
    break: the screen hands the base in at construction and never holds a way to
    mutate what the widget kept.

    **2. THE CONTENT SWAP IS LAYOUT-FREE, AND IT IS PROVABLY SAFE.**
    ``Static.update`` defaults to ``layout=True``, which re-measures the widget.
    On the 88 KB committed game that measurement is over **1,182 rendered rows**,
    and it is pure waste here: a highlight update changes only which character
    ranges carry a span — ``copy()`` copies the plain text verbatim and
    ``stylize`` cannot touch it — so the widget's text, and therefore its height,
    is *identical* before and after. There is nothing for a layout pass to
    discover.

    Measured per keystroke on that game (median of 8, whole path: copy +
    stylize + swap + pump to settle), with the layout-free swap against the
    default: ``vote`` (18 matches) **64.5 ms** vs 77.9; ``e`` (7,941 matches)
    **207.4** vs 233.8; a single space (14,524 matches) **306.0** vs 336.6 —
    about 15%. Re-measured against *this* implementation rather than the
    prototype the tech spec timed, the direction and the magnitude both hold:
    ``vote`` 58.8 ms vs 69.6, ``e`` 146.1 vs 164.9.

    The second half of the win is not speed at all: it **guarantees**
    ``virtual_size.height`` stays at 1,182 across an update, which is what Slice
    3's offset → row map is entitled to assume when it jumps to a match.

    (The remaining cost is structural and is recorded rather than fixed:
    ``Widget._render_content`` calls ``Visual.to_strips`` with the full 1,182-row
    height rather than the 22-row viewport, so any repaint re-renders the whole
    body. ``refresh(region)`` buys nothing — ``_dirty_regions.clear()``. Only a
    body with its own ``render_line(y)`` would close it; Slice 2's debounce is
    the answer instead.)

    **``markup=False`` is forced in the constructor, not passed by the caller.**
    Spec 038's guarantee is that this widget shows the preserved game character
    for character, and console markup is ``[…]`` — one ``[`` in model-generated
    persona prose would, with markup on, either vanish into a style tag or raise
    on an unclosed one. Baking the flag in means the guarantee cannot be lost by
    a future call site that forgets the keyword.
    """

    def __init__(self, base: Text, *, id: str | None = None) -> None:
        """Mount the game's styled text and keep it as the unhighlighted base.

        Args:
            base: The styled renderable from
                :meth:`TranscriptScreen._styled_body` — the game with its kind
                colours and **no** search highlight. Kept, not copied: this
                widget is its only owner, and every highlight update copies it
                rather than touching it.
            id: The widget id, so the screen's ``#transcript-body`` CSS and the
                tests' ``query_one`` keep working unchanged.
        """
        super().__init__(base, id=id, markup=False)
        self._base = base

    @property
    def text(self) -> str:
        """The game exactly as this widget renders it — what a search searches.

        Read-only, and it hands out a ``str`` rather than the base
        :class:`~rich.text.Text`, so the custody rule in the class docstring
        survives: a caller can ask what the game *says* without acquiring a
        handle it could ``stylize``.

        **It is the RENDERED text, not the file's bytes, and that is the point
        rather than an accident.** :func:`~graphia.eval_ledger.find_matches`
        returns offsets, and :meth:`highlight` turns those offsets into spans on
        this exact string — so the two have to be the same string or a match
        lands on the wrong characters. They can differ: ``Text.append`` runs
        Rich's ``strip_control_codes``, which drops BEL, BS, VT, FF and CR, so a
        transcript carrying one of those five would be one character shorter
        here than on disk and every offset after it would be shifted by a
        re-read of the file. No committed game contains one today (pinned by
        ``test_the_rich_text_path_drops_the_five_control_codes``), so the two
        agree — but searching what is on screen is correct by construction
        rather than by that corpus fact holding forever.

        Free to call per update: ``Text.plain`` collapses ``_text`` to a single
        joined string the first time and returns that same object afterwards,
        and :meth:`highlight`'s own ``copy()`` already forces that collapse on
        the first keystroke.
        """
        return self._base.plain

    def highlight(
        self,
        matches: Sequence[tuple[int, int]],
        style: RichStyle,
        *,
        current: tuple[int, RichStyle] | None = None,
    ) -> None:
        """Repaint the game with ``style`` over each of ``matches``.

        The whole per-update path, and the one place a search highlight is ever
        applied: copy the base, append one span per match, swap the content
        without a layout pass. See the class docstring for why each of those
        three is what it is.

        **The match spans land AFTER the kind spans, and that ordering is the
        feature.** ``copy()`` preserves spec 038's kind spans in their original
        order and ``stylize`` *appends*, so a match's background arrives on top
        of whatever colour, weight or slant its line already had;
        ``Content.from_rich_text`` then preserves both and combines them.
        Functional-spec §2.3 — "on top of, not instead of" — is therefore true
        **by construction** rather than by a per-kind case analysis, and it is
        directly assertable: the rendered runs' leading slice is exactly the
        tokenizer's runs, and the tail is exactly these match spans. **Do not
        sort, merge or coalesce them** — reordering would put a match underneath
        its own line's colour, and merging would destroy the assertion.

        An empty ``matches`` restores precisely the base: a copy with nothing
        appended has the base's span list and no more, which is what makes
        clearing a search (or deleting the last typed character) leave the
        reading view byte-for-byte and span-for-span as spec 038 renders it.

        **ONE SPAN PER MATCH, INCLUDING THE CURRENT ONE** (spec 044, Slice 4).
        The match the reviewer has navigated to is painted by *substituting* its
        style, never by appending a second span on top of it — so the tail of
        the span list stays exactly ``len(matches)`` long however many times
        ``n`` is pressed. That is what keeps the §2.3 ordering assertion
        checkable (``test_the_match_spans_are_a_tail_on_the_tokenizers_own_runs``
        counts the tail against the match count) and it is also the cheaper
        arithmetic: one ``int`` comparison per match against a second stylize
        pass and a span list that grows with navigation.

        Args:
            matches: Half-open ``(start, end)`` offset pairs in document order,
                as :func:`~graphia.eval_ledger.find_matches` returns them.
            style: The resolved highlight, **background-only** — see
                :meth:`TranscriptScreen._match_styles`. A style that set a
                foreground would replace the line's own colour instead of
                sitting on top of it.
            current: The match the reviewer is on and the stronger style to
                paint it with — ``(index, style)`` — or ``None`` when they have
                not landed on one (nothing typed yet, or the query just edited).
                **The index and the style travel as one argument on purpose**:
                an index with no style would paint the current match like every
                other and silently cost §2.6 its only feedback in the last
                screenful, where successive jumps do not scroll. An index
                outside ``matches`` simply matches nothing and paints no current
                match, which is the harmless reading of a stale index.
        """
        highlighted = self._base.copy()
        # `-1` can never equal an `enumerate` index, so "no current match" needs
        # no branch inside the loop — the substitution below is simply never
        # taken. The fallback style is irrelevant for the same reason.
        current_index, current_style = current if current is not None else (-1, style)
        for index, (start, end) in enumerate(matches):
            # Appends a `Span(start, end, style)`. Note that Rich's `stylize`
            # opens with `if style:` — a `RichStyle` with nothing set is falsy
            # and would silently append nothing at all. That is the failure mode
            # to look for if a theme ever resolves the highlight to no
            # background: not a traceback, just a search that highlights
            # nothing.
            highlighted.stylize(
                current_style if index == current_index else style, start, end
            )
        # `layout=False`, deliberately and provably — the plain text is
        # unchanged, so there is no size to re-measure. This is the ONE call
        # that may swap this widget's content; anything reaching for
        # `Static.update` directly would bring the layout pass back.
        self.update(highlighted, layout=False)


@dataclass(slots=True)
class SearchState:
    """One live transcript search (spec 044) — the thing whose *existence* is asked about.

    :attr:`TranscriptScreen._search` is ``None`` when no search is in progress
    and an instance of this when one is. That presence/absence — **never**
    "is the search bar showing" — is what :meth:`TranscriptScreen.action_close`
    branches on, and the difference is the whole reason this class exists in
    Slice 1 rather than in the slice that first needs a second field.

    **Functional-spec §2.9 describes a THREE-state machine, and bar visibility
    can only tell two of the states apart:**

    1. **Bar open, query typed.** Esc closes the bar, drops the search, and
       leaves the reviewer exactly where they had scrolled to (§2.9 AC1).
    2. **Bar hidden, search still live.** The state Enter leaves behind from
       Slice 3 onward: the prompt closes and the highlights stay. Esc here
       clears the highlighting and the reviewer **stays in the game**; "a
       further Esc leaves it" (§2.9 AC5).
    3. **No search at all.** Esc leaves the game immediately, exactly as it did
       before this feature existed (§2.9 AC3).

    Branching on whether the bar is open collapses (2) into (3): the screen
    would pop out from under a reviewer who is still looking at highlighted
    matches, losing both the highlights and the game in one keypress. Branching
    on ``_search is not None`` keeps all three distinct.

    **Four fields now, and the dataclass is what made each one after the first
    cheap.** Slice 3 added :attr:`row_map` in place, Slice 4 added
    :attr:`current` the same way, and Task 4.4a added
    :attr:`scroll_at_landing` beside it — each an added field rather than a
    reshape of every use site, which is what a bare ``str | None`` would have
    cost. The last of them is the clearest case for the shape: the fix it
    belongs to is *one more thing a search remembers*, and it cost one field
    plus the three lines that keep it in step with :attr:`current`. (The
    match offsets are deliberately *not* a field:
    :func:`~graphia.eval_ledger.find_matches` is ~1 ms and is recomputed from
    :attr:`TranscriptBody.text` at each use, so there is no second copy of the
    answer to keep in step with the query.)
    Mutable (no ``frozen=True``): a search is edited keystroke by keystroke and
    re-navigated by ``n``/``N``, so replacing the whole object per character
    would be ceremony, not safety.

    **``query`` is kept in step with the box by
    :meth:`TranscriptScreen.on_input_changed`**, which writes it on every
    keystroke — while the repaint it triggers happens later, once the typing has
    settled. So this field is deliberately the *live* query and the highlight on
    screen is the *settled* one, and the two differ for up to
    :data:`_SEARCH_DEBOUNCE_SECONDS`. That is not a lag to design away: the
    debounce's callback reads this field back rather than capturing a value when
    the window opened, which is precisely what makes a burst of keystrokes
    collapse into one repaint of what the reviewer ended up typing instead of
    one repaint of what they typed first.
    """

    query: str

    # THE OFFSET → RENDERED-ROW MAP, CACHED WITH THE WIDTH IT WAS BUILT AT
    # (spec 044, Slice 3) — ``None`` until the first jump asks for one.
    #
    # `wrap_row_starts` is what turns a match's character offset into the screen
    # row Enter scrolls to, and it depends on the wrap width, so the width is
    # half the cache key rather than an afterthought. **The width is not a
    # constant**: measured at a 100-column terminal, a game that OVERFLOWS the
    # viewport wraps at 96 (100 − 2 scrollbar − 2 padding) while one that FITS
    # wraps at 98, and opening the search row can flip a borderline short game
    # between the two by costing it the row that gave it a scrollbar. A map
    # built at the wrong width mis-wraps every line of a real game, so
    # `TranscriptScreen._row_starts` reads `body.content_size.width` live on
    # every jump and rebuilds when it differs from what is cached here.
    #
    # Lazy rebuild rather than a `Resize` handler, because the rebuild is cheap
    # enough not to need one: the whole map for the worst committed game (88 KB,
    # 1,182 rows) costs **7.6 ms**, paid once per width the reviewer actually
    # jumps at. A handler would pay it on every intermediate width of a drag.
    #
    # The TEXT is not part of the key, and cannot be: `TranscriptBody`'s base is
    # built once at `compose` and never changes for the life of the screen, so
    # width is the only thing a map can go stale against.
    row_map: tuple[int, list[int]] | None = None

    # THE MATCH THE REVIEWER IS ON — an index into the CURRENT match list, or
    # ``None`` when they have not landed on one yet (spec 044, Slice 4).
    #
    # **It is the CURSOR — the row it sits on is where the next `n`/`N` steps
    # from, unless the reviewer has scrolled away since (Task 4.4a).** This
    # comment used to say the opposite ("an OUTPUT, never an input to a
    # decision"), with the step resolved from the live `scroll_offset.y` alone,
    # and that is what dead-ended `n` at the end of every game whose query has
    # matches inside the last screenful: the scroll clamps at `max_scroll_y`, so
    # the viewport CANNOT express a position in that final screenful and every
    # press re-answered the same match. `TranscriptScreen._step_origin_row`
    # carries the measurement and owns the two-origin rule; `scroll_at_landing`
    # below is what lets it tell the two apart.
    #
    # **What did NOT change, and must not be re-litigated by anyone reading the
    # word "cursor" here:** the step stays ROW-keyed. `current ± 1` — tech-spec
    # §2.6's first phrasing, corrected during Slice 4 — can land on a MATCH
    # SHARING THE SAME RENDERED ROW, re-admitting exactly the press that moves
    # nothing, which is the thing `step_match_by_row` exists to eliminate. The
    # fix hands that function a better ROW; it does not step by match.
    #
    # What it IS for, both halves of §2.6's "the reviewer can tell which one
    # they are on": `transcript--match-current` paints this one match more
    # strongly than its neighbours, and Task 4.5's `Screen.sub_title` counts it
    # off as "3/18". Neither is decoration — inside the last screenful of a game
    # the clamp to `max_scroll_y` leaves nothing to scroll into view, so
    # successive presses produce NO movement and the marker plus the count are
    # the reviewer's only evidence that the keypress did anything at all.
    #
    # **Dropped whenever the match list it indexes into changes.** Editing the
    # query re-runs `find_matches` and index 3 of the old list is nothing in the
    # new one, so `on_input_changed` clears it; clearing the search drops the
    # whole `SearchState` and takes it along. An index kept across an edit would
    # paint the marker on an unrelated match — a silent lie rather than a
    # traceback.
    current: int | None = None

    # WHERE THE VIEW WAS WHEN `current` WAS LANDED ON — the recorded fact that
    # makes "has the reviewer scrolled since?" answerable (spec 044, Task 4.4a).
    #
    # Written together with `current` by both landings (:meth:`Enter
    # <TranscriptScreen.on_input_submitted>` and
    # :meth:`n/N <TranscriptScreen._step_match>`), read by
    # :meth:`TranscriptScreen._step_origin_row`, and dropped together with
    # `current` by `on_input_changed`. Both-or-neither: a landing index with no
    # landing position is not a state the reader below knows how to answer for,
    # and keeping the two in step is one line at each of the three sites.
    #
    # **Why a RECORDED position rather than a comparison against `current`'s
    # row.** The obvious test for "they have not scrolled" — does `current`'s
    # rendered row equal `scroll_offset.y`? — is wrong exactly where it is
    # needed. Inside the last screenful the scroll CLAMPS at `max_scroll_y`, so
    # a match on row 1178 of the 1,182-row committed game rests at y 1160 and
    # the two legitimately differ. Reading that difference as "the reviewer
    # scrolled" is what dead-ended `n` in the first place; see
    # `TranscriptScreen._step_origin_row` for the measurement. The answer cannot
    # be inferred from the match — it has to be remembered from the landing.
    scroll_at_landing: int | None = None


class TranscriptScreen(Screen):
    """One game's full transcript in a scrollable, read-only view (spec 017).

    Pushed from :class:`DetailScreen` when a game is selected in its transcript
    panel (spec 037). Under spec 017 an intermediate list screen pushed it; that
    screen is now removed. Reads the
    transcript text through the pure
    :func:`~graphia.eval_ledger.read_transcript` (handed the entry's ``.path`` —
    **no** file reads or path arithmetic of its own) and wraps it verbatim in a
    :class:`~textual.containers.VerticalScroll` so a long game is fully reachable.
    The body is a :class:`~textual.widgets.Static` (a read-only text widget, **not**
    an :class:`~textual.widgets.Input`) — there is nothing to edit; the viewer is
    read-only throughout.

    **Colour-coded since spec 038.** The text is still the file's text, character
    for character — the screen only paints it. The split into semantic kinds is
    the pure :func:`~graphia.eval_ledger.tokenize_transcript`'s job; this screen
    owns only the kind → appearance mapping (:data:`_TRANSCRIPT_KIND_COMPONENTS`
    plus one CSS rule per entry) and the assembly of the styled renderable. The
    spans concatenate back to the file exactly, so where colour is unavailable
    the view degrades to precisely the plain text it showed before, with no stray
    formatting characters (functional-spec §2).

    ``escape``/``backspace``/``q`` pop back to the :class:`DetailScreen` that pushed this screen
    — the identical back-out idiom as :class:`DetailScreen` / the list screen,
    reusing spec 012's push/pop pattern, so the maintainer steps back out
    transcript → list → run record exactly the way they came in. A
    missing/unreadable file degrades to a blank view (``read_transcript`` returns
    ``""``), never a traceback.
    """

    TITLE = "Graphia eval ledger"
    SUB_TITLE = "game transcript · Esc / Backspace to go back"

    # PINNED, NOT INHERITED — and it has to be, before this screen grows a second
    # focusable widget. `App.AUTO_FOCUS` is `"*"`, and `Screen._update_auto_focus`
    # filters that query on `Widget.focusable`, which gates on `self.visible` —
    # the `visibility` rule — and NOT on `display` (read from the installed
    # Textual 8.2.4: `app.py`'s `AUTO_FOCUS = "*"`, `screen.py`'s
    # `_update_auto_focus`, `widget.py`'s `focusable`). So a hidden `Input` in
    # `compose()` would still be the first `"*"` match and would take the
    # screen's opening focus while `display: none`, silently swallowing every
    # keystroke into an invisible box. Naming the scroller also writes down
    # today's behaviour, which otherwise holds only by accident of tree order;
    # `test_scrolling_a_long_game_behaves_as_before` is its witness.
    AUTO_FOCUS: ClassVar[str] = "#transcript-scroll"

    # Derived from THE map, so adding a highlighted kind stays a one-line change
    # there: a new entry brings its component class along, and only the matching
    # CSS rule below has to be written.
    #
    # ...PLUS the search highlight's two (spec 044), which are unioned in rather
    # than folded into the map for the reason `_TRANSCRIPT_MATCH_COMPONENTS`
    # gives: a match is not a kind, and the map's keys are the tokenizer's
    # vocabulary. Declaring them HERE is not paperwork —
    # `get_component_rich_style` hands back a bare default for a class the
    # screen has not declared, so an undeclared `transcript--match` would
    # resolve to the body's own background and highlight nothing, silently.
    COMPONENT_CLASSES: ClassVar[frozenset[str]] = frozenset(
        _TRANSCRIPT_KIND_COMPONENTS.values()
    ) | frozenset(_TRANSCRIPT_MATCH_COMPONENTS)

    DEFAULT_CSS = """
    TranscriptScreen {
        layout: vertical;
    }

    #transcript-scroll {
        height: 1fr;
        width: 1fr;
    }

    #transcript-body {
        padding: 0 1;
        width: 1fr;
    }

    /* ------------------------------------------------------------------
       THE SEARCH BAR (spec 044) — a one-row prompt above the game, whose
       RESTING STATE IS CLOSED. Three decisions here are measured requirements
       rather than taste.

       A SIBLING OF THE SCROLLER, NOT A CHILD. Inside `#transcript-scroll` the
       bar would be part of the scrolled *content*: it would ride up off the top
       of the screen the moment the reviewer scrolled, which is the one thing a
       search prompt must never do. Being a sibling is also what makes
       `TranscriptSearchInput`'s six scroll bindings necessary rather than
       redundant — with the bar outside the scroller there is nothing up the
       focused widget's ancestor chain to catch `up`/`down`/`pageup`/`pagedown`
       either, so the subclass declares all six itself (see its docstring). The
       layout fact and the routing fact are the same fact.

       `display: none`, NOT `visibility: hidden`, and the difference *is*
       functional-spec §2.10 AC2 ("nothing about searching is taking up space
       they were using to read"). `visibility: hidden` RESERVES the cell — the
       row would still be laid out and still a row tall, so a reviewer who never
       presses `/` would silently pay one row of game, forever, for a box they
       cannot see. `display: none` gives the row no region at all. Measured with
       the bar closed: `#search-row` size `(0, 0)`, and the reading view's
       geometry byte-identical to before this feature existed — 22 viewport
       rows, 82 virtual, `max_scroll_y` 60, which is exactly what
       `test_scrolling_a_long_game_behaves_as_before` pins.

       A CLASS TOGGLE RATHER THAN `widget.display = …`, so both states are
       readable *here*, beside the kind rules, instead of one of them living in
       a Python assignment the stylesheet cannot see. `#search-row.-open` has
       the same id as `#search-row` plus one class, so it wins on specificity
       and the closed state stays the default without an `!important`.

       No collision with `LedgerTableScreen`'s own `#search-row` (the runs-list
       filter, a different thing on a different screen). `Screen.SCOPED_CSS` is
       `True` on the installed Textual 8.2.4, so each screen's `DEFAULT_CSS` is
       rewritten into its own scope — verified by measuring two screens whose
       `DEFAULT_CSS` sets a different height on the same id, and each got its
       own. */
    #search-row {
        display: none;
        height: 1;
    }

    #search-row.-open {
        display: block;
    }

    /* A DEFAULT `Input` IS 3 ROWS, and that is the whole reason this rule
       exists: `Input.DEFAULT_CSS` on the installed Textual 8.2.4 sets
       `height: 3` with `border: tall $border-blurred`. Three rows of chrome to
       hold one row of query would cost a seventh of a 22-row reading viewport,
       so the border goes and the height comes down to the row the text actually
       needs. `padding: 0 1` narrows the default `0 2` to match
       `#transcript-body`'s own padding, so the query sits on the same left
       margin as the game beneath it.

       THE `#search-row` PREFIX IS LOAD-BEARING — a bare `TranscriptSearchInput`
       selector would LOSE. `Input.DEFAULT_CSS` carries a nested `&:focus` rule
       restoring `border: tall $border`, and a pseudo-class outranks a type: a
       scoped `TranscriptScreen TranscriptSearchInput` is (id 0, class 0, type 2)
       against `Input:focus`'s (0, 1, 1), so the border — and with it the third
       row — would come back the instant the bar took focus, moving the game
       under a reviewer at exactly the moment functional-spec §2.1 promises it
       will not. Naming the row buys the id, and the id beats the pseudo-class.
       Measured: `#search-row` stays `1` row tall and `scroll_offset.y` is
       unchanged across focusing the box.

       (`Input` also ships a `-textual-compact` class doing much the same job.
       Deliberately not used: its `border: none !important` is an `!important`
       this stylesheet would then have to fight to say anything else about the
       border, and the sizing is the kind of thing that belongs where the rest
       of this screen's appearance is written down.) */
    #search-row TranscriptSearchInput {
        height: 1;
        border: none;
        padding: 0 1;
        width: 1fr;
    }

    /* The live match count, written by `TranscriptScreen._update_search_count`
       (its copy is `_search_count_text`'s). Mounted EMPTY, so that the row's
       geometry is settled and measured before anything writes to it, rather
       than shifting the first time a count appears — and empty is also the
       resting state it returns to, since a reviewer who has typed nothing has
       not asked anything yet.
       `width: auto` takes exactly the room its text needs and leaves
       the rest of the row to the query box's `1fr`, so the count sits hard
       against the right of what is typed rather than at a fixed column.
       `$text-muted` is this module's own precedent for a secondary readout
       (`#match-count`, `#empty-state`, `#transcript-panel-empty`). */
    #search-count {
        height: 1;
        width: auto;
        color: $text-muted;
    }

    /* SYNTAX HIGHLIGHTING (spec 038) — one rule per entry in
       `_TRANSCRIPT_KIND_COMPONENTS`. Component classes are Textual's own way to
       style *parts* of a widget's content from CSS: the rule cannot be a
       `#transcript-body` rule because the whole point is that different runs of
       text inside that one Static look different, and CSS selects widgets, not
       substrings. `Widget.get_component_rich_style` resolves each rule to a
       concrete Rich style, which is then attached to the matching spans.

       Colours come from theme variables, never literals, so the view tracks the
       reviewer's light/dark theme — functional-spec §2 requires every kind to be
       legible on both.

       `marker` — the game's skeleton (section tags, the top metadata line, the
       bare `Round N.` label). `$text-muted` is exactly the "recedes behind the
       content" requirement of functional-spec §2, and is this module's own
       precedent for `color:` (`#match-count`, `#empty-state`,
       `#transcript-panel-empty`). Note that `color:` is the ONLY property this
       block sets: `$text-muted` resolves to `auto 60%`, which the parser rejects
       for `border:` — the spec-037 mistake that made a whole screen fail to
       mount. Setting only text colour is what keeps it safe here. */
    TranscriptScreen > .transcript--marker {
        color: $text-muted;
    }

    /* `attr` — the SPECIFIC a marker carries: which player a cast entry
       describes and their role, whose private thought follows, who called a vote
       and against whom. The marker around it is deliberately quiet, so this rule
       is the inverse of the one above: the value has to be MORE legible than the
       punctuation holding it, or the reviewer is back to reading
       `<vote initiator="…" target="…">` character by character to find two names
       (functional-spec §2, "Markers that carry details show those details
       distinguishably").

       ACHROMATIC ON PURPOSE, and this is the palette decision the later slices
       depend on: from Slice 4 onward **colour means side**. A `name=` value
       carries no side of its own — Slice 4 re-kinds the ones that do (a `role=`
       value, a thought's owner) to their side kinds and leaves plain names here
       — so tinting it would let a Mafioso's name read in the Law-abiding hue.
       Structure is therefore marked with brightness and weight, colour is left
       to mean allegiance, and the two axes never contradict each other.

       Full body brightness (`$text`, i.e. `auto 87%`) against the marker's
       `auto 60%`, PLUS `text-style: bold`, lifts the value out on two axes at
       once. The `color:` is written even though the span would inherit the same
       colour unstyled: it says out loud that the neutral is a choice, not an
       omission, and it is the weight that makes this rule more than a no-op. */
    TranscriptScreen > .transcript--attr {
        color: $text;
        text-style: bold;
    }

    /* `field-label` — the cast list's five labels (`Personality:`, `Manner:`,
       `Public legend:`, `True self (hidden):`, `Persona:`), colon included. The
       job is skimming: an entry should be readable by jumping label to label
       instead of as a paragraph (functional-spec §2). Its neighbour is the
       OPPOSITE of `attr`'s — persona prose at full brightness, not quiet
       punctuation — so lifting it further would be shouting, and weight alone
       would be a weak separator inside a wall of equally bright text. A
       restrained hue plus weight is what makes the left margin scannable.

       `$text-secondary` is the dimmest of the three theme hues that clear the
       4.5:1 AA floor on BOTH builtin themes (measured: 4.67:1 on textual-dark,
       6.39:1 on textual-light; `$text-accent` and `$text-success` fail the light
       theme at 3.27 and 3.77 and are unusable here). Spending the dim one on
       6,200 short bolded labels leaves `$text-primary` and `$text-error` — the
       two brightest — for Slice 4's two sides, which colour the bulk of a game's
       text. A label is never a person, so it cannot be misread as an allegiance
       even if a side later shares its hue.

       `text-style` here is also the mechanism proof for Slice 5: `_kind_styles`
       keeps a rule's text style through `without_color`, so weight composes with
       colour inside one component class and the bold-within-the-side-colour
       human seat stays a one-rule change. No collision with it either — the
       human's cast entry is the one that reads `(no persona recorded)`, so it
       carries no field label at all. */
    TranscriptScreen > .transcript--field-label {
        color: $text-secondary;
        text-style: bold;
    }

    /* `speaker` / `speech` — the `Name:` prefix of a spoken line and the words
       after it. DELIBERATE NEUTRAL PLACEHOLDERS, and the two rules are identical
       on purpose: speech is the NORM a reading view is calibrated against, and
       functional-spec §2 asks for it to be coloured *by side* — which is Slice
       4's job, off the `<setup>` cast list this slice's tokenizer does not read
       yet. Painting speech some interim colour now would spend one of the two
       AA-on-both variables Slice 4 needs (see the note on the map above) and
       would have to be un-picked a slice later.

       So this is the honest minimum: `color: $text` is the body's own colour, so
       a spoken line looks exactly as it did before this slice and the three
       kinds of *content* the slice does own read as "speech (the baseline),
       thought (a departure), recap (a departure)". Measured, so the "no-op"
       claim is not a guess: the resolved span colour is byte-identical to the
       unstyled body on `textual-dark` (#e0e0e0) and on `textual-ansi` (the
       terminal default), and differs by 2/255 on `textual-light` (#1d1d1d vs
       #1f1f1f) — `$text` is `auto 87%` where the widget default is `$foreground`.
       Writing the `color:` rather than leaving the rule empty says out loud that
       the neutral is chosen, and gives Slice 4 a rule to look at.

       NO `text-style` HERE, and that is the load-bearing half. Two later slices
       depend on dialogue carrying no weight of its own:

       - Slice 5 marks the reviewer's own seat **bold within its side colour**,
         and functional-spec §2 states the contrast explicitly — "other
         Law-abiding players' lines are the same colour but not bold". A bold
         `speaker` here would make every seat's name bold and leave that
         criterion unsatisfiable. Bold is spent elsewhere in this stylesheet
         (`attr`, `field-label`) but nowhere on *a player's line*, so inside
         dialogue bold stays unambiguously "this seat was the reviewer's".
       - Slice 4 then overrides by ADDING, never editing: it appends its
         side-bearing kinds (`speaker-mafia` / `speech-mafia` / … ) to the map
         with their own component classes and their own rules, and these two
         rules survive untouched as the correct appearance for a line whose side
         is unknown — a pre-022 game with no `<player>` tag, or a name absent
         from the cast list. "Never guess a side" and "the neutral is the body
         colour" are then the same rule. */
    TranscriptScreen > .transcript--speaker {
        color: $text;
    }

    TranscriptScreen > .transcript--speech {
        color: $text;
    }

    /* `thought` — the BODY of a `<thought player="X">…</thought>`, something
       nobody at the table could see. functional-spec §2 is absolutist about it:
       set apart from spoken dialogue "at a glance, so it can never be mistaken
       for something said aloud", and distinguishable "without reading either".

       Italic is the reason this needs no hue. Slanted type is the printed
       convention for interior monologue, so it reads as unspoken before a word
       of it is parsed, and a 120-200 character thought (the corpus range) is a
       long enough run for the slant to register as texture rather than as a
       typo. It also composes: from Slice 4 the owner's name inside the
       surrounding tag takes its side colour as an `attr` span, and this rule
       touches only the body, so "whose thought" and "a thought" are read off two
       different spans without either fighting the other.

       TWO AXES, NOT ONE — the same belt-and-braces `attr` uses a few rules up,
       and here it is what makes "never" credible rather than aspirational.
       Neither axis is available everywhere: `text-style` is dropped by a
       terminal without italic support, and colour collapses under the
       `textual-ansi` theme (measured: `$text` and `$text-muted` both resolve to
       the terminal default there). Each axis therefore covers the other's
       failure — a no-italic terminal still separates a thought by brightness, and
       `textual-ansi` still separates it by slant. On one axis alone the
       requirement would quietly fail on real setups.

       `$text-muted` for the brightness axis, and it is the right *semantics* and
       not merely the variable that was left over: a thought is an aside, quieter
       than what the table actually heard. No risk of reading as skeleton despite
       sharing `marker`'s variable — a marker is a short bracketed token and a
       thought is a sentence in italics, and the two meet on the same line
       (`<thought player="…">` upright, the body slanted), so the boundary
       between them is visible rather than inferred. It clears AA on both themes
       (7.16 dark / 5.31 light) as a paragraph of read-in-full prose must.

       Unlike `speaker`/`speech`, this rule is PERMANENT: Slice 4 gives the
       thought's owner a side, not the thought's body — a body tinted by side
       would say the private reflection is itself an act of allegiance, and would
       make thought and speech share a colour, undoing exactly this rule. */
    TranscriptScreen > .transcript--thought {
        color: $text-muted;
        text-style: italic;
    }

    /* `recap` — the BODY of a `<recap>…</recap>`, the moderator's end-of-round
       status line ("Day 1, 9 AM status: 4 Law-abiding Citizens and 2 Mafiosos
       remain…"). Two requirements from functional-spec §2, and the second is the
       one that picks the treatment: it must read as fact rather than opinion,
       and the recaps must be findable "when the reviewer scrolls the game" — a
       LANDMARK, not merely a distinguishable kind.

       `reverse` is the only achromatic treatment that satisfies a landmark.
       Scanning a scrolling wall of text, what the eye catches is mass and shape,
       not texture: bold prose still reads as prose at speed, an underline under
       wrapped prose is a thin rule easily lost, but inverted video turns the
       recap into a filled block two or three wrapped rows tall. A corpus recap
       is 130-195 characters and a game carries up to 18 of them, so those blocks
       land at round boundaries and give the reviewer a ruler down the game.
       Inverted video is also what a terminal has always used for a status line,
       which is precisely the register the moderator is speaking in — posted fact,
       not a player's argument.

       It spends NO hue, which is the whole reason it is available: `$text-error`
       and `$text-primary` are held for Slice 4's two sides, and a recap must not
       carry a side at all — the moderator has none, and tinting it would make the
       one impartial voice in the game read as a player's.

       Legibility is preserved by construction rather than by luck: `reverse`
       swaps foreground and background, so the recap's contrast is the same
       14.19 / 12.77 `$text` already measures on the two builtin themes, only
       inverted. And like `thought`'s italic it is an SGR attribute, so it is the
       one part of this stylesheet that survives `textual-ansi` intact — where
       `marker`'s `$text-muted` degrades to the terminal default, a recap is
       still unmistakable.

       Verified rather than assumed, because it is the first rule here to rely on
       something other than colour + weight: `text-style: reverse` parses (it is
       in Textual's `VALID_STYLE_FLAGS`), survives `_kind_styles`'
       `without_color + Style.from_color(...)`, and survives the Rich `Text` →
       `Content` conversion (`textual.style.Style.from_rich_style` carries
       `reverse` through). Permanent, like `thought`: no later slice re-kinds a
       recap. */
    TranscriptScreen > .transcript--recap {
        color: $text;
        text-style: reverse;
    }

    /* ------------------------------------------------------------------
       SIDES (spec 038, Slice 4) — the headline of the whole spec:
       "Each side has its own colour, applied to a speaker's name and to what
       they say" (functional-spec §2). Six rules, two hues: `speaker`, `speech`
       and `attr` each split Mafia / Law-abiding.

       THE PAIRING IS FORCED, NOT PREFERRED. Only five theme variables clear
       4.5:1 WCAG AA on BOTH builtin themes (see the map above); three are spent
       on structure, leaving exactly `$text-error` and `$text-primary`. The
       obvious Mafia-red / Law-abiding-green is unavailable because
       `$text-success` measures 3.77:1 on `textual-light` and functional-spec §2
       requires legibility on both terminals. The forced pair is the better one
       anyway: red/green is the classic deuteranopia failure, and red/blue
       separates on the one axis every common colour-vision deficiency keeps.

         Mafia        `$text-error`    6.34 dark / 7.29 light
         Law-abiding  `$text-primary`  7.03 dark / 10.69 light

       `color:` ONLY, NO `text-style`, on the four dialogue rules, and that
       restraint is load-bearing rather than minimal. functional-spec §2 states
       the Slice 5 contrast literally — the reviewer's own seat is bold while
       "other Law-abiding players' lines are the same colour but not bold" — so
       any weight here would make that criterion unsatisfiable. Bold is spent
       elsewhere in this stylesheet but nowhere on *a player's line*, so inside
       dialogue it stays unambiguously "this seat was the reviewer's".

       DEGRADATION, stated once for all six and measured, not assumed: under
       `textual-ansi` both variables resolve to CONCRETE colours (`#ab5656` and
       `#5656ab`) rather than collapsing to the terminal default the way
       `marker`'s and `thought`'s `$text-muted` does — the same behaviour Slice 2
       found for `field-label`'s `$text-secondary`. So the sides stay
       distinguishable from each other and from the body under all three builtin
       themes. No AA figure is quoted for `textual-ansi` because the background
       there is the terminal's own and unknowable from inside the app. And where
       colour cannot be shown at all the spans still concatenate to the file, so
       the view falls back to exactly the plain text it showed before Slice 1. */

    /* A spoken line: the `Name:` prefix and the words after it, in the
       speaker's side colour. The pair of rules per side is deliberately
       identical — functional-spec §2 asks for the name and the words to *share*
       the colour ("the speaker's name and the words they spoke share that
       speaker's side colour"), so the two kinds exist to be coloured together,
       not differently. They are separate kinds because the tokenizer draws the
       boundary (the colon belongs to `speaker`, the separating space to
       `speech`) and because Slice 5 may want the prefix and the line to diverge;
       today they must not. */
    TranscriptScreen > .transcript--speaker-mafia {
        color: $text-error;
    }

    TranscriptScreen > .transcript--speech-mafia {
        color: $text-error;
    }

    TranscriptScreen > .transcript--speaker-law-abiding {
        color: $text-primary;
    }

    TranscriptScreen > .transcript--speech-law-abiding {
        color: $text-primary;
    }

    /* The side-bearing `attr` values — a cast entry's `role="…"` and a
       `<thought player="…">` owner's name. The tokenizer gives a side to exactly
       these two of the five whitelisted attributes, because they are the two
       that *tell the reviewer a side*: a role states one outright, and a thought
       tag names a person and nothing else. A cast entry's `name=` and a vote's
       `initiator`/`target` stay achromatic `attr` above.

       BOLD IS KEPT, and this is the one open choice in the slice, so here is the
       reasoning. The "no `text-style` on a side rule" ratification was argued
       for dialogue specifically — to leave bold unspent on *a player's line* for
       Slice 5. An `attr` span is not a player's line: it lives inside `<…>`
       punctuation, never on a line of speech, so the two never share a context
       and Slice 5's bold stays unambiguous. Dropping the weight would also
       invert Slice 2's own requirement on the line where it matters most: in
       `<player name="Avery" role="Mafioso">` the name is bold `attr` and the
       role sits right beside it, so an unweighted role would read *lighter* than
       the name it qualifies — the specific carrying the most information
       receding behind the one carrying least. Keeping bold means the two
       treatments differ on hue alone, which is exactly the claim being made:
       same job, now with a side.

       So this rule is `attr`'s rule with the colour swapped — same weight, side
       hue instead of `$text`. */
    TranscriptScreen > .transcript--attr-mafia {
        color: $text-error;
        text-style: bold;
    }

    TranscriptScreen > .transcript--attr-law-abiding {
        color: $text-primary;
        text-style: bold;
    }

    /* ------------------------------------------------------------------
       THE REVIEWER'S OWN SEAT (spec 038, Slice 5) — the last appearance the
       spec adds. functional-spec §2: the seat the person plays "is shown in its
       side's colour, but **bold**, so it stands out from the other players on
       the same side", while other same-side players' lines are "the same colour
       but not bold". Six rules, mirroring the six kinds: `speaker` and `speech`
       each split three ways (side unknown / Mafia / Law-abiding).

       EACH RULE IS ITS NON-HUMAN TWIN'S `color:` PLUS `text-style: bold`, AND
       NOTHING ELSE. That is the requirement read literally: the seat must stay
       inside its side, so reaching for a new hue would say "a third side" where
       the spec says "this one of them is yours", and a second `text-style`
       would separate the seat from its side-mates on an axis the spec did not
       ask for. There is no new hue to reach for in any case — the palette is
       fully spent (see the map above), which is why Slice 3 and Slice 4 both
       declined `text-style` on a player's line and left this one weight free.

       BOLD IS UNAMBIGUOUS HERE BECAUSE IT IS UNSPENT *ON A LINE*. The
       stylesheet uses bold three other places — `attr`, `field-label`, and the
       two `attr` side rules — and not one of them can appear on a player's
       line: an `attr` span lives inside `<…>` marker punctuation (a cast
       entry's `role=`, a `<thought player=…>` owner) and a field label occurs
       only inside `<setup>`. So within dialogue, bold means exactly one thing.

       WHY THE NEUTRAL PAIR EXISTS, since it looks like padding and is not: side
       and seat are independent facts. A pre-spec-022 game names its seat in the
       preamble and has no cast list at all, so its seat is known while its side
       is not — 183 speaker and 183 speech spans across those 30 files. Bolding
       the body colour is the honest rendering of that: "this seat was yours" is
       said, "and they were Mafia" is not.

       Contrast is inherited unchanged from the twin, since only the weight
       differs (measured against both builtin themes' real backgrounds):

         seat, side unknown  `$text`          14.19 dark / 12.77 light
         seat, Mafia         `$text-error`     6.34 dark /  7.29 light
         seat, Law-abiding   `$text-primary`   7.03 dark / 10.69 light

       And bold, being an SGR attribute rather than a colour, is the half that
       survives `textual-ansi` — where `$text` collapses to the terminal default
       the seat is still marked, exactly as `thought`'s italic and `recap`'s
       reverse survive there. Verified, not assumed: `text-style: bold` composes
       with `color:` through `_kind_styles`' `without_color + from_color(...)`
       (the same mechanism `field-label` has proved since Slice 2), so the
       resolved span style carries both the hue and the weight. */
    TranscriptScreen > .transcript--speaker-human {
        color: $text;
        text-style: bold;
    }

    TranscriptScreen > .transcript--speech-human {
        color: $text;
        text-style: bold;
    }

    TranscriptScreen > .transcript--speaker-mafia-human {
        color: $text-error;
        text-style: bold;
    }

    TranscriptScreen > .transcript--speech-mafia-human {
        color: $text-error;
        text-style: bold;
    }

    TranscriptScreen > .transcript--speaker-law-abiding-human {
        color: $text-primary;
        text-style: bold;
    }

    TranscriptScreen > .transcript--speech-law-abiding-human {
        color: $text-primary;
        text-style: bold;
    }

    /* ------------------------------------------------------------------
       THE DAY'S DIARY (spec 039) — the body of a
       `<diary player="X" day="N">…</diary>`: the private note an AI files at the
       Day->Night hinge, summing up the Day it has just lived through. It renders
       in the day trailer, between the last `</round>` and `</day>` (tech-spec 039
       §2.8).

       THIS RULE IS `thought`'S RULE PLUS `underline`, AND NOTHING ELSE — the same
       shape Slice 5 used for the reviewer's seat ("its side's colour plus bold,
       and nothing else"). A diary is a SIBLING of a thought, not a stranger: both
       are private, both are the same player's own writing, and neither is an act
       of allegiance. So the family trait is *rendered* rather than merely
       asserted — `color: $text-muted` and `italic` are inherited from the
       `thought` rule verbatim — and exactly one axis carries the difference.

       NO HUE, AND THAT IS A DECISION AND NOT A SHORTAGE. It is also a shortage:
       the palette is fully spent (see the map above), and the two survivors are
       `$text-error` and `$text-primary`, which mean *side*. But even with a hue
       going spare this rule would decline it, for the reason the `thought` rule
       gives and tech-spec 039 §2.8 restates in those words: a private reflection
       is not an act of allegiance, and tinting the prose would say it was. Whose
       diary it is still reads at a glance, off the owner's name in the
       surrounding tag, which the tokenizer kinds `attr-mafia` /
       `attr-law-abiding` and the rules above already colour. The `day` value
       beside it stays achromatic `attr`.

       WHY `underline` IS THE AXIS — by meaning first, and then by elimination.
       A thought is a passing reaction inside one speaking round; a diary is
       written down and filed. An underline is the ruled line of the page it is
       written on, which is the one SGR attribute that reads as "on the record"
       rather than as emphasis, alarm or deletion. Every other flag in Textual's
       `VALID_STYLE_FLAGS` is unavailable, and one of them only looks available:

       - `bold` is Slice 5's, and its whole argument is that bold is unspent *on
         a player's line*. A diary body is a line of a player's prose, so bolding
         it would put a diary and the reviewer's own seat in the same register.
       - `reverse` is `recap`'s, where it buys a scroll landmark. A second
         inverted block would file the moderator's posted fact and a player's
         private note under one appearance.
       - `dim` stacks a second intensity reduction on a variable that is
         ALREADY `auto 60%`, and how far is the terminal's own business — it is
         SGR 2, not a colour, so the resulting contrast cannot be measured from
         inside the app at all. The wrong thing to bet a 4.5:1 floor on for a
         paragraph meant to be read in full.
       - `strike` reads as retracted, `blink` is unusable.
       - `overline` IS MEASURED DEAD, and this is the one worth recording. It
         parses (`o` is in `VALID_STYLE_FLAGS`) and it reaches the resolved Rich
         style intact — but `textual.style.Style.from_rich_style` on the installed
         Textual 8.2.4 has **no `overline` parameter**, so `Content.from_rich_text`
         drops it and the span renders undecorated. A rule that looks right in the
         stylesheet and paints nothing on screen. (`uu`, double underline, does
         survive both hops — but SGR 4:2 support is far thinner than SGR 4's, and
         the plain underline is the better-supported half of the same idea.)
       - a filled `background:` — the obvious "make it a block" move — cannot
         work here at all: `_kind_styles` strips the resolved background with
         `without_color` and adds back only the foreground, so a `background:`
         would be silently discarded. Recorded so nobody re-derives it.

       WHAT THE REVIEWER ACTUALLY SEES, which is the real design problem, and the
       measured reason the RULE rather than the LENGTH is load-bearing. The
       tempting argument is mass: a diary is bounded by
       `prompts.DIARY_SENTENCE_BOUND` (6 sentences) and clamped at
       `nodes.day.DIARY_MAX_CHARS` (900), and the same clamp folds it to a SINGLE
       line, so it wraps into a block of rows. But measured over the 298
       committed transcripts a `<thought>` body runs 46-626 characters (n=8183,
       median 188, p90 316) — so at ~100 columns a thought is usually two rows
       and occasionally six, and the two distributions OVERLAP. (The `thought`
       rule above says "120-200 characters (the corpus range)"; that is the
       middle of the distribution, not its range.) Mass alone would therefore be
       a coin-flip on the tail. What does not overlap is the rule under every
       wrapped row: at ANY length a diary is a block of ruled rows and a thought
       is unruled. Position is the second, independent cue — a thought sits
       inside a `<round>`, a diary only ever after the last `</round>`.

       DEGRADATION, measured on the installed Textual 8.2.4 against both builtin
       themes' real backgrounds, and identical to `thought`'s because it is the
       same variable: `$text-muted` clears AA on both (7.16:1 on `textual-dark`,
       5.31:1 on `textual-light`). Under `textual-ansi` it collapses to the
       terminal default, exactly as `marker`'s and `thought`'s do — so there the
       italic and the underline together are the whole of what separates a diary
       from body text, and the UNDERLINE ALONE is what separates it from a
       thought. That is the case the differentia had to survive, and the reason
       it had to be an SGR attribute rather than a hue. Verified rather than
       assumed, the same way `recap`'s `reverse` was: `text-style: italic
       underline` survives `_kind_styles`' `without_color + Style.from_color(...)`
       and the Rich `Text` -> `Content` conversion, arriving at the span as
       `italic=True, underline=True`.

       Permanent, like `thought` and `recap`: no later slice re-kinds a diary. */
    TranscriptScreen > .transcript--diary {
        color: $text-muted;
        text-style: italic underline;
    }

    /* ------------------------------------------------------------------
       A SUBSTITUTED DIARY (spec 041) — the body of a
       `<diary player="X" day="N" substituted="true">…</diary>`: the
       deterministic stand-in `graphia.nodes.day._DIARY_FALLBACK` the node files
       when the model's answer could not be decoded, rather than the player's own
       writing. Functional-spec 041 asks that such an entry be *visibly marked*,
       so a reviewer reading a preserved game can see that the words are not the
       player's.

       THIS RULE IS THE `diary` RULE PLUS `strike`, AND NOTHING ELSE — the third
       use of the shape Slice 5 introduced and spec 039 reused ("its side's
       colour plus bold, and nothing else"; "`thought`'s rule plus `underline`").
       The inheritance is the point and not economy: a substituted entry is still
       a diary — still private, still filed at the Day->Night hinge, still not an
       act of allegiance — so it keeps the whole of the family's appearance
       (`$text-muted`, `italic`, `underline`, all three verbatim from the rule
       above) and adds exactly one flag saying the one thing that is different.
       Its measured contrast is therefore `diary`'s, untouched: 7.16:1 on
       `textual-dark`, 5.31:1 on `textual-light`, the same variable, still clear
       of AA on both.

       WHY `strike` — by meaning, and then because the alternatives are gone.
       The meaning is exact and it is the reason this flag was held back rather
       than spent earlier: strike-through is the typographic convention for text
       that is *void* — struck from the record, not to be read as the author's.
       That is precisely a substituted entry's status, and it is the one register
       none of the other kinds wants. The `diary` rule above rejected `strike`
       for itself in one clause ("`strike` reads as retracted") — and this rule
       is the reason that reading was correct rather than dismissive: the flag
       was never wrong, it was describing a fact no kind had yet needed to state.
       The elimination half is short because the map above now records it in
       full: `bold`, `italic`, `underline` and `reverse` are spent on the seat,
       the thought, the diary and the recap; `uu`, `dim` and `blink` are the
       three compromised survivors; and `overline` is measured dead at both
       downstream hops. `strike` was the last comfortable slot, and this is what
       it went on.

       WHAT THE REVIEWER ACTUALLY SEES, stated as the open question it is rather
       than as a settled claim. This is the first rule in this stylesheet to
       stack THREE `text-style` flags on `$text-muted`: `italic underline strike`
       on the quietest colour in the palette. The design is sound on the axis
       that matters — the mark is *derived*, so a substituted entry can only ever
       read as "a diary, and struck", never as some fourth unrelated kind, and
       the strike is a horizontal rule through the glyphs where the underline is
       a rule beneath them, which is a different place on the row rather than a
       second rule in the same place. But three stacked decorations on slanted
       muted prose is a real legibility risk and it cannot be settled from
       inside the source: `underline` and `strike` are both thin horizontal
       rules, italic shears the glyphs they cross, and how the two lines and the
       slant compose is the terminal's font's business, not this stylesheet's.

       WHAT BOUNDS THAT RISK, and it is a measured fact rather than a hope: the
       marked body is not a 900-character diary. It is `_DIARY_FALLBACK`
       VERBATIM — one fixed 110-character sentence, which `nodes.day` files
       without passing it through `_clamp_diary_entry` at all — so at ~100
       columns this rule paints one or two rows, every time, for every
       substituted entry in the corpus. The failure mode a long body would have
       (nine wrapped rows each carrying two horizontal rules, reading as a hatch
       pattern rather than as prose) is therefore unreachable here.

       THAT FORECAST FOR SLICES 9 AND 10 WAS WRONG, AND SLICE 9 MEASURED IT.
       This paragraph used to end "where it becomes reachable is Slices 9 and 10,
       whose bodies are a real thought and a real line of speech at their real
       lengths — so those slices inherit this rule's shape but NOT this bound".
       They do inherit the bound, and for a stronger reason than the diary has
       it: the mark is set ONLY on the substitution branch, where the body *is*
       the channel's fallback constant. Those constants are SHORTER than this
       one — `_REFLECTION_FALLBACK` is 52 characters and the day-speech fallback
       30, against `_DIARY_FALLBACK`'s 110 — so every marked body in every
       channel is one row at ~100 columns. The corpus's 46-626 character range is
       the range of AUTHORED bodies, which never carry the marking. The bound is
       structural for the whole family; no later rule owes a length check, and
       none should be weakened on this basis.

       **THAT QUESTION IS ANSWERED AND CLOSED — do not reopen it by dropping
       `underline`.** Slice 8's Task 8.6 looked (`make view-ledger` against a
       transcript carrying a marked entry beside an honest one, on all three
       themes, judged from the real SGR wire rather than a rasterised SVG — the
       exporter emits one `text-decoration:` per flag and the later declaration
       wins, so an SVG shows you the *retreat* while you believe you are judging
       this rule) and did NOT apply the retreat. It reads: the underline sits
       below the baseline and the strike crosses at mid x-height, so the two
       rules never merge; the marked body is bounded to one or two rows; and
       under `textual-ansi` the inherited `underline` is the only thing left
       saying "this is a *diary*" at all, so giving it up would move a marked
       entry into a *thought*'s register.

       **And since Slice 9 the retreat is not merely declined, it is blocked by
       test.** Dropping `underline` here leaves `italic strike` on `$text-muted`
       — which is EXACTLY `thought-substituted` (see the next rule): same
       colour, same flags, and byte-identical in style under `textual-ansi`,
       where the colour collapses for both. `underline` is the whole of what
       separates the two marked kinds, and two committed tests say so —
       `test_the_substituted_diary_style_is_the_diarys_rule_plus_strike` and
       `test_the_substituted_thought_style_is_the_thoughts_rule_plus_strike`,
       each asserting `diary_substituted.underline is True` and each
       parametrised over all three themes. So the retreat costs **6 red cases
       across 2 test functions**, not a nuance about the "on the record" half.
       A future rendering complaint has to find a different answer than this
       one; that is what this paragraph is here to say.

       TWO AXES, and this rule is only ONE of them — the same belt-and-braces
       `attr` and `thought` use, and here it is what makes the marking survive a
       terminal that cannot draw it. The flag's own value is already an ordinary
       bold `attr` span on the tag line (`_ATTR_NAMES` lifts it; no rule here
       does that work), so a terminal without SGR 9 still shows
       `substituted="true"` in bold on the tag, and a reviewer skimming bodies
       rather than tags still sees the struck prose. Under `textual-ansi`, where
       `$text-muted` collapses to the terminal default exactly as `marker`'s,
       `thought`'s and `diary`'s do, the three SGR flags are the whole of what
       separates a substituted diary from body text — and the STRIKE ALONE is
       what separates it from an honest one, which is the case the differentia
       had to survive and the reason it had to be an SGR attribute and not a hue.

       NO HUE, and unlike `diary`'s that is not even a shortage argument: the two
       surviving variables mean *side*, and "nobody wrote this" is not a side.
       Tinting a substituted entry would say a fact about allegiance where the
       fact is about authorship.

       Verified rather than assumed, at the two hops that killed `overline`:
       `rich.style.Style.parse("strike")` is `Style(strike=True)` (so it reaches
       the resolved Rich style and survives `_kind_styles`' `without_color +
       Style.from_color(...)`), and `textual.style.Style.from_rich_style` carries
       `strike=rich_style.strike` explicitly into the `Content` conversion — the
       parameter `overline` does not have. Measured on the installed Textual
       8.2.4.

       Permanent, like `diary`: no later slice re-kinds a substituted entry.
       Slices 9 and 10 add the same treatment to the thought and speech channels
       as their OWN kinds, deriving each from its own honest rule the way this
       one derives from `diary` — they do not reuse this class. */
    TranscriptScreen > .transcript--diary-substituted {
        color: $text-muted;
        text-style: italic underline strike;
    }

    /* ------------------------------------------------------------------
       A SUBSTITUTED THOUGHT (spec 041 Slice 9) — the body of a
       `<thought player="X" substituted="true">…</thought>`: the deterministic
       stand-in `graphia.nodes.day._REFLECTION_FALLBACK` that `_ai_reflect` files
       when the model's answer could not be decoded, rather than the player's own
       reflection. The same requirement as the rule above, on the channel a
       reviewer actually reads: the committed corpus holds 10,706 thought bodies
       against 376 diary ones.

       THIS RULE IS THE `thought` RULE PLUS `strike`, AND NOTHING ELSE — the
       fourth use of the shape ("its side's colour plus bold, and nothing else";
       "`thought`'s rule plus `underline`"; "the `diary` rule plus `strike`").
       It derives from `thought`, NOT from `diary-substituted`, and that choice
       is the whole design of the marking family: each marked kind is its own
       honest kind plus the one flag, so the two marked kinds differ by exactly
       the flag their honest forms differ by (`underline`) and a reviewer who has
       learnt "struck = nobody wrote this" reads both without learning a second
       thing. Deriving this one from `diary-substituted` instead would have had
       to *subtract* `underline` — an operation the family has never needed and
       one that would leave a substituted thought looking like a retreat from a
       diary rather than a thought that was stood in for.

       TWO `text-style` FLAGS, NOT THREE, and this rule is therefore LESS stacked
       than the `diary-substituted` rule above it — the one place in this
       stylesheet where the later kind is the quieter one. `italic` is inherited
       from `thought`; `strike` is the mark. The legibility question the rule
       above states as open does not reach here: there is no `underline` for the
       strike to compose with, so what a reviewer sees is one horizontal rule
       through slanted glyphs, which is the plainest form the marking takes
       anywhere.

       `strike` IS REUSED, NOT SPENT, and that is why this rule needs no new axis
       (see the flag ledger above `_TRANSCRIPT_KIND_COMPONENTS`). The flag carries
       ONE meaning across this stylesheet — *this body is not the player's
       writing* — so a second kind wearing it says the same thing about a second
       channel rather than overloading it. Reusing a flag for an unrelated
       meaning is what this ledger exists to prevent; a marking family sharing
       one flag is what the family is.

       THE BLOCK-TEXTURE CONCERN THE RULE ABOVE FORWARDED TO THIS SLICE IS
       REFUTED, MEASURED RATHER THAN ARGUED. That rule worried that a marked
       thought would be "a real thought at corpus length" (46-626 characters,
       n=8183, p90 316) and so could wrap to many rows, each carrying horizontal
       rules, until the block read as a hatch pattern rather than as prose. It
       cannot: the mark is set ONLY on the substitution branch, where the body
       *is* the fallback constant — `_REFLECTION_FALLBACK` at **52 characters**,
       less than half `_DIARY_FALLBACK`'s 110. So this rule paints ONE row at
       ~100 columns, every time; the corpus range is the range of AUTHORED
       thoughts, which never carry the marking. The bound is structural for every
       channel in this family rather than incidental to the diary, which is also
       why nothing here is weakened on that basis.

       TWO AXES, and this rule is only ONE of them — the belt-and-braces `attr`,
       `thought` and `diary-substituted` all use. The flag's own value is already
       an ordinary bold `attr` span on the tag line (`_ATTR_NAMES` lifts it; no
       rule here does that work), so a terminal without SGR 9 still shows
       `substituted="true"` on the tag and a reviewer skimming bodies still sees
       the struck prose. Under `textual-ansi`, where `$text-muted` collapses to
       the terminal default exactly as `marker`'s, `thought`'s and `diary`'s do,
       the two SGR flags are the whole of what separates a substituted thought
       from body text — and the STRIKE ALONE is what separates it from an honest
       one, the case the differentia had to survive and the reason it is an SGR
       attribute and not a hue.

       NO HUE, for the reason `diary-substituted` gives and one more of its own:
       the two surviving variables mean *side*, "nobody wrote this" is not a
       side, and a thought's body has been un-tinted since spec 038's Slice 3
       precisely so a private reflection never reads as an act of allegiance. A
       substituted one is still a private reflection.

       Verified at the two hops that killed `overline`, on the installed Textual
       8.2.4: `rich.style.Style.parse("strike")` is `Style(strike=True)` and
       `textual.style.Style.from_rich_style` carries `strike=rich_style.strike`
       explicitly into the `Content` conversion.

       Permanent, like `thought`: no later slice re-kinds a substituted thought.
       Slice 10 adds the same treatment to Day speech as its OWN kind, derived
       from its own honest rule the way this one derives from `thought` — it does
       not reuse this class. */
    TranscriptScreen > .transcript--thought-substituted {
        color: $text-muted;
        text-style: italic strike;
    }

    /* ------------------------------------------------------------------
       A SUBSTITUTED PIECE OF DAY SPEECH (spec 041 Slice 10) — the body of a
       `<speech speaker="X" substituted="true">…</speech>`: the deterministic
       stand-in `"I'm not sure who to trust yet."` that `_ai_day_action` files
       when the model's answer could not be decoded, rather than the words the
       player would have said out loud. The third and last channel of the
       marking family, and the only PUBLIC one — the other two are things
       nobody at the table could see, this is a turn the whole table heard.

       THIS RULE IS THE `speech` RULE PLUS `strike`, AND NOTHING ELSE — the
       fifth use of the shape ("its side's colour plus bold, and nothing else";
       "`thought`'s rule plus `underline`"; "the `diary` rule plus `strike`";
       "the `thought` rule plus `strike`"). `color: $text` is inherited verbatim
       from the neutral `speech` rule; `strike` is the mark. THE PLAINEST FORM
       THE MARKING TAKES ANYWHERE: ONE flag, against `thought-substituted`'s two
       and `diary-substituted`'s three, because the honest form it derives from
       carries none. The stack shrinks as the honest forms do, so the last kind
       added is the quietest — which is the second time in this stylesheet that
       happens (see the rule above) and is worth stating rather than reading as
       an oversight.

       IT DERIVES FROM THE *NEUTRAL* `speech`, WHICH IS THE SIDE-TINTING
       DECISION MADE VISIBLE. Speech is the only channel in the family whose
       honest forms are side-bearing (`speech-mafia`, `speech-law-abiding`, and
       the neutral for a line whose side is unknown), so this rule had a real
       choice its two predecessors did not: inherit a side, or inherit the
       body colour. It inherits the body colour, for the reason both rules above
       give and which does not weaken for the body being public: colour has
       meant *side* since Slice 4, a side is a fact about the SPEAKER, and the
       words of a substituted line have no author to have a side. Tinting them
       would attribute an act of allegiance to a sentence nobody uttered.

       THE SIDE IS NOT LOST, IT MOVES — and this is the half that made the
       decision affordable rather than a sacrifice. The wrapped line carries a
       `speaker="X"` attribute, `speaker` is whitelisted in `_ATTR_NAMES`, and
       `_attr_kind` gives it the speaker's side — so the name reads
       `attr-mafia` / `attr-law-abiding` through the rules above, exactly as a
       `<thought player=…>` owner's does. A reviewer still places a marked line
       at a glance. Without that relocation the honest alternative would have
       been three tinted body kinds, which `_TRANSCRIPT_KIND_COMPONENTS`'
       entry records as breaking the `speaker`/`speech` families' index
       alignment.

       NO HUE OF ITS OWN, for the reason the two rules above give and one more
       that is specific to this channel: `$text` is the body's own colour, so a
       substituted line reads as *body text, struck* — which is the honest
       rendering of "these are not anybody's words". The two surviving
       variables mean *side*, and "nobody wrote this" is not a side.

       WHAT THE REVIEWER ACTUALLY SEES, and the block-texture worry does not
       reach here either: the marked body is the fallback constant
       `"I'm not sure who to trust yet."` — **30 characters**, the SHORTEST of
       the three (against `_REFLECTION_FALLBACK`'s 52 and `_DIARY_FALLBACK`'s
       110) — so this rule paints one row at ~100 columns, every time. The
       corpus's 46-626 character range is the range of AUTHORED speech, which
       never carries the marking, because the flag is set only on the
       substitution branch where the body IS the constant. Nothing here is
       weakened on that basis.

       DEGRADATION, and this is the one place the family gets EASIER rather
       than thinner. `$text` clears AA on both builtin themes (14.19:1 on
       `textual-dark`, 12.77:1 on `textual-light`) and collapses to the terminal
       default under `textual-ansi`, exactly as `speech`'s and `recap`'s do. So
       under `textual-ansi` all three marked kinds resolve to the terminal
       default and the SGR flags are the entire separation — but this kind is
       separated from BOTH of the others by the ABSENCE of `italic`, which no
       theme can take away, and from an honest line by `strike`. Being outside
       the muted family also means it never has to be told apart from `marker`,
       `thought` or `diary` by brightness at all.

       TWO AXES, and this rule is only ONE of them — the belt-and-braces every
       marked kind uses. The flag's own value is already an ordinary bold `attr`
       span on the tag line (`_ATTR_NAMES` lifts it; no rule here does that
       work), and here there is a THIRD signal the other two channels do not
       have: the line's whole SHAPE changes, from `Speaker: text` to a tag. A
       terminal with no SGR 9 at all still shows a marked turn as the one
       element in a Day round.

       Verified at the two hops that killed `overline`, on the installed Textual
       8.2.4: `rich.style.Style.parse("strike")` is `Style(strike=True)` and
       `textual.style.Style.from_rich_style` carries `strike=rich_style.strike`
       explicitly into the `Content` conversion.

       Permanent: no later slice re-kinds a substituted line of speech, and this
       is the last channel — `_ai_day_action`, `_ai_reflect` and `day_diary` are
       the three substitution sites that produce reader-visible prose. */
    TranscriptScreen > .transcript--speech-substituted {
        color: $text;
        text-style: strike;
    }

    /* ------------------------------------------------------------------
       THE SEARCH HIGHLIGHT (spec 044) — `background:` AND NOTHING ELSE, on the
       one axis the palette ledger above leaves unspent. Two rules, and they are
       the last two in this stylesheet for the same reason every kind rule was
       appended rather than inserted: nothing above moves.

       WHY BACKGROUND — which is the whole of the colour argument, and it is an
       exhaustion argument rather than a preference. The ledger above
       `_TRANSCRIPT_KIND_COMPONENTS` closes both axes spec 038 used: all five
       theme foregrounds clearing 4.5:1 on BOTH builtin themes are spent
       (`$text`, `$text-primary`, `$text-muted`, `$text-error`,
       `$text-secondary`), and five of the eight usable `text-style` flags are
       spent too — including `reverse`, which is `recap`'s and is precisely what
       `less` marks its own matches with; `overline` is measured dead at both
       downstream hops. So a highlight could not be a colour without meaning a
       SIDE and could not be a flag without meaning a KIND. Background is the
       axis nothing has claimed — and `_kind_styles` has been deliberately
       stripping it out of every kind style since spec 038 for exactly this
       case: its docstring names "a selection highlight" as the thing it was
       leaving room for.

       IT IS ALSO WHY THE HIGHLIGHT COMPOSES RATHER THAN REPLACES. Setting only
       the background leaves every kind's foreground and flags untouched
       underneath it, so functional-spec §2.3 ("on top of, not instead of")
       holds for all twenty-four kinds BY CONSTRUCTION, rather than by a case
       analysis nobody could keep current as kinds keep arriving. A Mafioso's
       line under a match is still `$text-error`, a thought is still italic, the
       reviewer's own seat is still bold. `_match_styles` is the half of that
       guarantee written in Python; these two rules are the half written here.

       AMBER IS FORCED, NOT CHOSEN. `$primary-muted` / `$secondary-muted` (blue)
       and `$error-muted` (red) are excluded on spec 038's own grounds: in this
       view blue and red MEAN SIDE, so a highlight in either would read as an
       allegiance claim about a line the reviewer has merely searched for.
       `$success-muted` measures worse (2.67 worst case on dark). That leaves
       amber — `$accent` and `$warning` are identical on both builtin themes and
       neither is spent — which is also the universal search-hit colour, so the
       one hue left over is the one a reviewer already expects.

       THE PERCENTAGE FORM RATHER THAN `$accent-muted`: `background: $accent N%`
       blends against the theme's OWN background, so it tracks light and dark
       automatically, and a percentage yields TWO STEPS OUT OF ONE HUE — which a
       named muted variable cannot, and which is what `transcript--match-current`
       needs in order to stand out from its neighbours without introducing a
       second meaning.

       THE COST — A REAL RELAXATION OF A STANDING PROJECT RULE, WRITTEN DOWN
       HERE RATHER THAN BURIED, because spec 038's whole design record is about
       AA and the next reader deserves to know this was CHOSEN rather than
       overlooked. On `textual-dark` the HIGHLIGHTED state holds a 3:1 floor,
       not the 4.5:1 every rule above keeps. Measured against the 20% blend
       (`#412f17`, re-measured at implementation and matching the tech spec's
       table to the decimal): `$text` 9.68, `$text-muted` 4.89, `$text-primary`
       4.80, `$text-error` 4.33, `$text-secondary` **3.19**. The arithmetic is
       CLOSED, so this is not a tuning failure waiting for a better number:
       `$text-secondary` already sits at 4.67:1 on dark with zero headroom, so
       ANY background light enough to be seen at all pushes it under AA. There
       is no value satisfying both, and a highlight nobody can see is not a
       highlight.

       Accepted on four grounds, all of them about the state being transient:
       the RESTING reading view keeps its AA guarantee completely untouched (not
       one kind rule changes); the highlight exists only while a reviewer has
       asked for it, and is one Esc away; `$text-secondary` is only the five
       cast-list field labels, not a channel anyone reads at length; and a match
       is redundantly located by the status count and by the jump putting it on
       the top row. The light theme is comfortable throughout — worst case 4.84
       (`$text-muted` on the 20% blend `#e6d4bb`).

       THE 45% STEP IS A SECOND, STEEPER RELAXATION, AND IT IS MEASURED HERE
       RATHER THAN INFERRED FROM THE FIRST — the tech spec quotes only the 20%
       column, and the second step is materially worse, so a reader must not
       take 3:1 as the floor for both. Against `#7c541d` on `textual-dark`:
       `$text` 5.06, `$text-muted` 2.56, `$text-primary` 2.51, `$text-error`
       2.26, `$text-secondary` **1.67** — i.e. below 3:1 for four of the five,
       so the CURRENT match is the one place in this view where legibility
       genuinely leans on something other than contrast. What makes it bearable
       is that it applies to exactly ONE span at a time, and to the one the
       reviewer deliberately jumped to: it is on the top row, named by the
       status count, and surrounded by its 20% neighbours for comparison. On
       `textual-light` the same step is comfortable (worst case 4.33). If the
       by-eye pass finds it illegible on dark, the lever is this rule's
       percentage and nothing else depends on it.

       VISIBILITY, the other end of the same trade — "can it be seen at all"
       measured as the blend against the body background: dark 1.47 (20%) and
       2.80 (45%) against `#121212`; light 1.10 and 1.23 against `#e0e0e0`.
       The light theme is the thin one, which is what the re-tuning note below
       is about.

       ONE MEASURED CONSEQUENCE, and `recap` is the only kind where it happens:
       a match inside a moderator recap combines to `reverse … on #412f17`, so
       the terminal SWAPS the pair — the text goes amber on a light ground
       instead of the ground going amber. ~9.2:1, clearly legible and clearly
       marked, just inverted relative to every other kind. That is `reverse`
       doing what `reverse` does, not a bug to code around.

       TO RE-TUNE: light-theme separation between the two steps measures 1.12
       here (the tech spec's 1.09 at the same two percentages), against 1.91 on
       dark — the one figure that wants a human eye rather than an assertion
       (Task 5.5's by-hand pass). If the current match is too hard to pick out
       on light, widen to 15% / 50%; nothing else in this stylesheet depends on
       either number. */
    TranscriptScreen > .transcript--match {
        background: $accent 20%;
    }

    TranscriptScreen > .transcript--match-current {
        background: $accent 45%;
    }
    """

    # Identical back-out idiom as DetailScreen: pop back to the transcript list.
    BINDINGS = [
        Binding("escape", "close", "Back", show=True),
        Binding("backspace", "close", "Back", show=True),
        Binding("q", "close", "Back", show=False),
        # `/` opens the search bar (spec 044).
        #
        # `show=True` IS A DELIBERATE, AUTHOR-CHOSEN ADDITION TO THE FOOTER —
        # DO NOT "restore parity" by hiding it later. Functional-spec §2.1 asks
        # for the key to be advertised in as many words ("the strip of key hints
        # already along the bottom of the screen gains a `Search` entry beside
        # the existing `Back`"), so a reviewer who does not know the feature
        # exists can discover it rather than having to be told. The author
        # resolved the trade-off explicitly (tech-spec §2.9, 2026-09-12):
        # discoverability over a pixel-identical Footer. §2.10's promise that
        # everything else about reading a game is unchanged is about the reading
        # AREA — the rows of game text, which this costs nothing — not about the
        # Footer, and the Footer is the only place a key can be advertised at
        # all.
        #
        # `slash`, not `"/"`: Textual names the key, and this is the spelling
        # `LedgerTableScreen` already uses for the runs-list filter, so the
        # module's two `/`s read the same way.
        Binding("slash", "open_search", "Search", show=True),
        # `n` / `N` walk the matches once the prompt has closed (spec 044,
        # §2.6), ADVERTISED — description plus `show=True` (Slice 6, Task 6.1).
        #
        # WHAT WAS HERE BEFORE, AND WHY IT IS GONE. Slice 4 bound both
        # `show=False` and with no description at all, on the written grounds
        # that they had "somewhere else to be announced — Task 4.5's status
        # line". The status line never announced them: it was built to render a
        # POSITION (`the · 1/702`) and never named a key. The author found this
        # by hand on the 88 KB committed game in Task 5.5 and could not reach
        # the following match — repeated `/`+Enter sat on row 7 four presses
        # running (§2.5's jump is inclusive, so after a landing it is already
        # satisfied), while `n` had been walking 7 → 10 → 13 → 14 correctly the
        # whole time with nothing on screen to say so. Functional-spec §2.6 and
        # §2.8 were amended on 2026-09-14 to require both halves; Task 6.2
        # built the status half.
        #
        # THE DESCRIPTION IS LOAD-BEARING, NOT DECORATIVE — and this is the
        # trap that would make a future "just flip `show`" a silent no-op.
        # `Binding.make_bindings` (textual/binding.py, installed 8.2.4)
        # normalises every declared binding through
        # `show=bool(binding.description and binding.show)`, so a binding with
        # an empty description is forced hidden NO MATTER what `show` says.
        # That is also why the key help panel — the command palette's "Keys"
        # entry, whose `BindingsTable` renders `binding.description` straight
        # into a column — showed these two blank: there was nothing to render.
        #
        # The copy is short because the Footer is ONE row and these two make it
        # five entries. Measured, not estimated: the five cells are `esc Back`
        # (10), `/ Search` (10), `n Next match` (14), `N Prev match` (14) and
        # `^p palette` (10) — 58 columns, which still fits a 60-column terminal
        # and leaves 42 spare at the 100 the tests use.
        #
        # `"N"`, NOT `"shift+n"`. Textual names a shifted printable key by the
        # character it produces: the terminal delivers `Key(key="N",
        # character="N")` and `_normalize_key_list` leaves a single alphanumeric
        # character alone, so `"N"` is what the binding map is keyed on. A
        # `"shift+n"` binding would be dead — the key event never carries that
        # spelling — and dead silently, with no error anywhere to find.
        #
        # Neither is reachable while the bar is open, and that is §2.4 working
        # rather than a gap: the focused `TranscriptSearchInput` takes both as
        # literal characters of the query (measured in Task 1.2). They are for
        # the reading state the prompt leaves behind.
        Binding("n", "next_match", "Next match", show=True),
        Binding("N", "previous_match", "Prev match", show=True),
        # Enter steps forward exactly as `n` does, once the prompt has closed
        # (spec 044, §2.6, Task 6.3). SAME ACTION, deliberately: Enter is not a
        # second implementation of the step, it is a second key on the one
        # `action_next_match` above, so the two can never drift.
        #
        # THE OPEN BAR IS NOT SPECIAL-CASED, AND DOES NOT NEED TO BE — verified
        # by keystroke rather than assumed, because §2.5 rests on it.
        # `App._check_bindings` walks `focused.ancestors_with_self`, and while
        # the bar is open the focused node is the `TranscriptSearchInput`, whose
        # own `Input` key handling posts `Input.Submitted` and consumes the key
        # before this screen-level binding is ever consulted. So Enter inside
        # the prompt still performs §2.5's INCLUSIVE jump (measured: from row
        # 500 it lands on row 502 and a second Enter on that match moves
        # nothing), and Task 5.2a's empty-Enter clear still clears. It is the
        # same resolution order that already makes `n`/`N` type literal
        # characters into an open bar.
        #
        # NO GUARD OF ITS OWN for "no search" or "query matches nothing":
        # `_step_match` already returns on `self._search is None` (§2.6 AC8 —
        # `n` on a fresh game is a documented no-op) and again when the pure
        # step answers `None` for an empty match list. Enter inherits both for
        # free, and adding a second copy here would be a second place for the
        # rule to be got wrong.
        #
        # `show=False`, and that is not the trap above — it is a choice about a
        # SYNONYM. `Footer.compose` groups its keys by `binding.action` and
        # renders only the first binding of each group (which is why `escape`
        # and `backspace` show as one `esc Back` cell today), so an
        # `enter`/`next_match` entry could never earn a sixth column anyway: it
        # would either be dropped as a duplicate of `n`, or — if it were
        # declared ABOVE `n` — REPLACE `n`'s cell and quietly undo Task 6.1.
        # Both halves measured on a throwaway app rather than reasoned from the
        # source: two shown bindings on one action render ONE cell, and the cell
        # takes the name of whichever was declared first. `show=False` makes
        # that independent of declaration order instead of an accident of it,
        # and the key this one duplicates is named on the row above.
        Binding("enter", "next_match", show=False),
    ]

    def __init__(self, entry: TranscriptEntry) -> None:
        super().__init__()
        self._entry = entry
        # THE SEARCH, OR ITS ABSENCE (spec 044). `None` until the reviewer
        # presses `/`; see `SearchState` for why this — and not the search bar's
        # visibility — is what `action_close` branches on.
        #
        # Per-instance, and that is functional-spec §2.9 AC4 ("open that game
        # again … no search is in progress and nothing is highlighted") for
        # free: `DetailScreen` pushes a NEW `TranscriptScreen` per game and
        # `pop_screen` discards it, so there is no search to carry between
        # games and nothing to reset on the way out.
        self._search: SearchState | None = None
        # THE PENDING DEBOUNCED REPAINT, or `None` when none is scheduled (spec
        # 044). There is never more than one: `_schedule_repaint` stops the open
        # window before opening the next, which is what makes the debounce
        # *restarting* rather than a queue of repaints that every one of them
        # still runs. Per-instance for the same reason `_search` is — a game is
        # one screen, so popping the screen takes the timer with it.
        self._repaint_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        # The search bar (spec 044), mounted CLOSED and OUTSIDE the scroller.
        #
        # Both of those are load-bearing and both are argued at the `#search-row`
        # rule above: `display: none` costs the reading view nothing (a reviewer
        # who never presses `/` sees the same 22 rows of game they saw before
        # this feature existed), and a sibling of `#transcript-scroll` — rather
        # than a child of it — is what keeps the prompt on screen when the game
        # scrolls under it.
        #
        # Mounting it now rather than on first `/` is the cheaper correctness:
        # a widget that exists from `compose` has its geometry settled and
        # measured once, where a mount mid-read would resize the scroller at the
        # moment functional-spec §2.1 promises the text will not move. It costs
        # nothing to carry — a `display: none` row has no region — and it is
        # also why `AUTO_FOCUS` had to be pinned before this line could be
        # written at all (see the note on the class attribute).
        #
        # The input takes no id: the screen reaches it by type, and the bar
        # reaches the scroller by message (`ScrollGame`), so neither widget has
        # to know the other's name.
        with Horizontal(id="search-row"):
            yield TranscriptSearchInput()
            yield Static(id="search-count")
        with VerticalScroll(id="transcript-scroll"):
            # The transcript's own text, verbatim from the pure reader, with
            # per-span styles attached (spec 038) — and **markup parsing still
            # off**.
            #
            # `markup=False` is not negotiable and is not made redundant by
            # passing a renderable. The hazard is square brackets, not the
            # transcript's `<day>` / `<round>` tags: Textual/Rich console markup
            # is `[…]`, so a persona description or a line of speech containing
            # `[` would, with markup on, either swallow the text as a style tag
            # or raise on an unclosed one. Every word here is model-generated, so
            # that is one eval run away even though no committed transcript
            # contains a `[` today. Spans sidestep markup entirely — they are
            # attached to character ranges, never parsed out of the text — and the
            # flag keeps the guarantee in place for the string path a future
            # refactor might take. Since spec 044 the flag is forced by
            # `TranscriptBody.__init__` rather than passed here, so it cannot be
            # dropped by a future call site — the reasoning above is why.
            #
            # THE BASE IS BUILT EXACTLY ONCE, HERE (spec 044). `_styled_body`
            # re-reads the file and re-runs `tokenize_transcript`, which is
            # ~2.8 ms on the 88 KB committed game — nothing at all once, and an
            # absurdity per keystroke of a live search. `TranscriptBody` keeps
            # what it is handed and copies it per update, so the tokenizer runs
            # once per *game opened* and never once per character typed.
            #
            # Per `compose` is also exactly the right lifetime for
            # functional-spec §2.9 AC4 ("open that game again … nothing is
            # highlighted"): `DetailScreen` pushes a fresh `TranscriptScreen`
            # per game, so a new base is built with no highlight on it and there
            # is nothing to reset on the way out.
            yield TranscriptBody(self._styled_body(), id="transcript-body")
        yield Footer()

    def _styled_body(self) -> Text:
        """The transcript as a styled renderable whose text is the file's text.

        Tokenizes with the pure :func:`~graphia.eval_ledger.tokenize_transcript`
        and paints each span with its kind's style, appending **every** span —
        styled or not — in order. So the renderable's plain text is
        ``"".join(text for text, _ in spans)``, which the tokenizer guarantees is
        the file byte for byte: the highlighting can change how the game looks
        but not what it says.

        **Called exactly ONCE per :meth:`compose`, and that is a contract rather
        than an observation** (spec 044). It re-reads the file and re-runs the
        tokenizer — ~2.8 ms on the 88 KB committed game — which is free once and
        indefensible per keystroke of a live search. The result becomes
        :class:`TranscriptBody`'s private base, which every search update
        *copies*; nothing outside ``compose`` calls this method, and a future
        caller that re-built the body per update would silently reintroduce the
        tokenizer into the typing path (Task 2.7's structural guard is what
        would catch it).

        A Rich :class:`~rich.text.Text` rather than Textual's native
        :class:`~textual.content.Content`: ``Static`` converts it through
        ``Content.from_rich_text``, preserving the spans, and it is the shape
        verified working on the installed Textual (8.2). Spec 044 leans on that
        conversion for more than fidelity — it is what preserves and *combines*
        the kind spans below with the search highlight's spans appended after
        them (see :meth:`TranscriptBody.highlight`).

        **Neither renderable is lossless by default, and the choice between them
        is stylistic.** ``Text.append`` runs Rich's ``strip_control_codes``,
        which silently drops BEL, BS, VT, FF and CR (7, 8, 11, 12, 13) — so a
        transcript containing one of those five would render one character short
        of its file, which is precisely the guarantee this screen exists to
        keep. An earlier version of this docstring said switching to ``Content``
        would close that hole; **it would not.** Measured on the installed
        Textual 8.2.4: ``textual/content.py`` carries its own
        ``_STRIP_CONTROL_CODES = [7, 8, 11, 12, 13]`` — the identical five — and
        strips them by default, so ``Content("a\\x07b").plain == "ab"`` exactly
        as the Rich ``Text`` path does. Only an explicit
        ``strip_control_codes=False`` closes the hole, whichever renderable is
        used; ``Content`` on its own buys nothing here.

        Line feed and tab are **not** among the five, and no committed
        transcript contains any of the five (checked over all 298), so the round
        trip holds today. It is pinned rather than merely believed:
        ``tests/test_ledger_viewer.py``'s
        ``test_the_rich_text_path_drops_the_five_control_codes`` asserts this
        boundary, so the day a transcript carries one of them is a test failure
        rather than a silently short render.
        """
        styles = self._kind_styles()
        body = Text()
        for text, kind in tokenize_transcript(read_transcript(self._entry.path)):
            # `style=None` appends the text with no span at all, which is both the
            # `plain` case and the forward-compatible fallback for a kind this
            # screen has not learned yet.
            body.append(text, style=styles.get(kind))
        return body

    def _kind_styles(self) -> dict[str, RichStyle]:
        """Resolve :data:`_TRANSCRIPT_KIND_COMPONENTS` to concrete Rich styles.

        Resolved once per :meth:`compose` (not once per span) against the live
        theme, via the component classes declared in
        :attr:`COMPONENT_CLASSES` — so the CSS above, and the theme variables in
        it, are the only place the palette is written down.

        Two details that are easy to get wrong, both checked against the
        installed Textual rather than assumed:

        - **the full style, not ``partial=True``.** A partial style reports only
          the properties the rule sets, which sounds like exactly what a per-span
          style wants — but it reports them *unresolved*, and ``$text-muted``'s
          ``auto 60%`` comes back as ``#ffffff`` (the un-blended auto colour)
          instead of the ``#a0a0a0`` the same rule renders. Only the full style
          runs the blend against the background.
        - **so the background is stripped afterwards.** The full style carries the
          background it blended against, and baking that into a span would paint
          a block behind every marker — wrong the moment anything else (a future
          rule on ``#transcript-body``, a selection highlight) sits underneath.
          ``without_color`` drops both colours; adding the foreground back keeps
          the colour and any ``text-style`` the rule sets.
        """
        styles: dict[str, RichStyle] = {}
        for kind, component in _TRANSCRIPT_KIND_COMPONENTS.items():
            resolved = self.get_component_rich_style(component)
            styles[kind] = resolved.without_color + RichStyle.from_color(resolved.color)
        return styles

    def _match_styles(self) -> dict[str, RichStyle]:
        """Resolve the two search-highlight classes to BACKGROUND-ONLY styles.

        Keyed by component class (:data:`_TRANSCRIPT_MATCH_COMPONENTS`), because
        unlike :meth:`_kind_styles` there is no kind to key on: a match is a
        thing that happens *to* a span of any kind, not a kind of its own.

        **The mirror image of :meth:`_kind_styles`, and deliberately not a
        branch of it.** That method keeps the foreground and throws the
        background away (``without_color + from_color(resolved.color)``);
        running the match classes through it would discard the one property they
        exist to set, and the highlight would paint nothing at all. This one
        keeps the background and throws the foreground away — which is what
        makes functional-spec §2.3 ("on top of, not instead of") true **by
        construction**: a style setting only ``bgcolor`` composes with whatever
        kind style is already on the span, for all twenty-four of them, with no
        case analysis to keep current as kinds keep arriving. The CSS rules say
        the same thing from the stylesheet's side.

        **The full style, not ``partial=True``** — the same hazard
        :meth:`_kind_styles`' docstring documents, and the reason it bites here
        is the percentage. A partial style reports only the properties the rule
        sets, which sounds like exactly what a per-span style wants, but it
        reports them *unresolved*: measured on the installed Textual 8.2.4,
        ``partial=True`` gives ``transcript--match`` a ``bgcolor`` of
        ``#fea62b`` — the raw, un-blended ``$accent`` — where the full style
        resolves the ``20%`` blend against the live theme background to
        ``#412f17``. Partial would paint a solid amber block behind every match
        instead of the tint the rule renders, on both themes.

        Resolved on demand rather than cached, exactly as :meth:`_kind_styles`
        is: a theme change re-resolves for free and there is nothing to
        invalidate.
        """
        # Line for line the same shape as `_kind_styles` above, so the "mirror
        # image" is visible in the code and not only in this docstring: same
        # full-style resolution, opposite half kept.
        styles: dict[str, RichStyle] = {}
        for component in _TRANSCRIPT_MATCH_COMPONENTS:
            resolved = self.get_component_rich_style(component)
            styles[component] = RichStyle(bgcolor=resolved.bgcolor)
        return styles

    def _repaint_matches(
        self,
        matches: Sequence[tuple[int, int]],
        current: int | None = None,
        *,
        wrapped: bool = False,
    ) -> None:
        """Light up ``matches`` in the game — the screen's half of one update.

        The seam between "what colour is a match" (this screen's, because
        :attr:`COMPONENT_CLASSES` and the CSS rules are declared here) and "how
        the game is repainted without re-tokenizing or re-laying-out it"
        (:class:`TranscriptBody`'s). Everything expensive or easy to get wrong
        is on the other side of that seam; this method resolves one style and
        hands the offsets over.

        **Four callers, and none of them is a keystroke of the query.**
        :meth:`_repaint_settled_query` when the debounce window expires on the
        query the reviewer has settled on; :meth:`_clear_search` with no matches
        at all, to put the unhighlighted reading view back when a search ends;
        and the two navigations — :meth:`on_input_submitted` (Enter) and
        :meth:`_step_match` (``n``/``N``) — which repaint not because the
        *matches* changed but because ``current`` did. All four also get the
        count in ``#search-count`` **and the header status on
        :attr:`Screen.sub_title`** from the same arguments — see
        :meth:`_update_search_count` and :meth:`_update_search_subtitle` for why
        both are written here and not per keystroke.
        **Do not call this from ``Input.Changed`` directly** — the debounce is
        not optional at this cost: 336 ms per keystroke on the 88 KB game for
        the worst measured query (tech-spec §2.8), because
        ``Widget._render_content`` re-renders all 1,182 rendered rows rather
        than the 22-row viewport.

        **The style is resolved once per update, not once per match.**
        ``get_component_rich_style`` walks the stylesheet, and there can be
        14,524 matches (tech-spec §2.8's worst measured query, a single space);
        resolving inside the loop would turn a 6 ms styling pass into a
        stylesheet walk per character of the game. Resolving per *update* rather
        than caching on the screen is the same trade
        :meth:`_kind_styles` makes: a theme change re-resolves for free and
        there is nothing to invalidate.

        Args:
            matches: Half-open ``(start, end)`` offset pairs in document order,
                as :func:`~graphia.eval_ledger.find_matches` returns them. Empty
                restores the unhighlighted reading view.
            current: Which of ``matches`` the reviewer is on, or ``None`` when
                they are on none of them — typing, or a search just cleared.
                Defaults to ``None`` so the two callers that are *not* about
                navigation say nothing about it rather than having to pass it.
                Task 2.3 refused this parameter while it would have been unused;
                Slice 4 gives it the use it was waiting for.
            wrapped: Whether the jump that produced ``current`` wrapped round the
                end (or the start) of the game — functional-spec §2.5 AC3 and
                §2.6's two wrap criteria. **Only the two navigations can know
                it**, so it defaults to ``False`` for the two that cannot; with
                ``current`` ``None`` on both of those paths it is unread anyway.
                Keyword-only, because ``_repaint_matches(matches, index, True)``
                would read as a third thing about the *matches* rather than as a
                fact about the jump that selected one of them.
        """
        # BOTH CLASSES NOW. `transcript--match-current` was resolved by this
        # same call from Slice 2 onward and deliberately left unused until
        # `n`/`N` existed to land on something — painting a "current" match
        # before anything could *be* current would have marked a match the
        # reviewer had not navigated to.
        styles = self._match_styles()
        self.query_one("#transcript-body", TranscriptBody).highlight(
            matches,
            styles[_MATCH_COMPONENT],
            # The index and its style travel together (see `highlight`): the
            # pair is built here, where both halves are in hand, so the widget
            # cannot be handed one without the other.
            current=(
                None
                if current is None
                else (current, styles[_MATCH_CURRENT_COMPONENT])
            ),
        )
        # THE COUNT TRAVELS WITH THE HIGHLIGHT — functional-spec §2.8 AC1 ("it
        # tells them how many matches there are"), counted off the same list the
        # body was just handed.
        #
        # It could as easily be counted per keystroke in `on_input_changed`:
        # `find_matches` is ~1 ms and is not what the debounce exists to
        # coalesce (the 336 ms is the re-render). It is not, because the number
        # and the highlighting are one statement to the reviewer. Counting early
        # would put "18 matches" on screen while 19 are still lit from the
        # previous query, for up to `_SEARCH_DEBOUNCE_SECONDS` of every burst —
        # and a readout that contradicts what the reviewer can see is a worse
        # failure than one that is briefly behind, because it is the readout
        # they would believe. Sharing this path makes the two unable to drift:
        # there is no second code path that can update one without the other.
        self._update_search_count(len(matches))
        # AND THE HEADER STATUS RIDES THE SAME PATH, for the same reason and one
        # more (spec 044, Task 4.5). The same reason: what is lit, what is
        # counted and what the header claims are one statement to the reviewer,
        # and a shared path is the only way to make them unable to drift. The
        # one more: this is where BOTH of its inputs already are — the match
        # total and the index — so the alternative is a second call at each of
        # the four sites, two of which would have to remember to say "and
        # nothing is current now" to clear a stale position.
        self._update_search_subtitle(len(matches), current, wrapped=wrapped)

    def _update_search_count(self, matches: int) -> None:
        """Write the match total — or the no-match state — into ``#search-count``.

        The screen's half of :func:`_search_count_text`: that function owns the
        copy, this one owns where the query comes from. It is read back off
        :attr:`_search` rather than passed in, and that is what makes the
        clearing half of the job fall out for free. :meth:`_clear_search` drops
        the state **before** it repaints (its docstring says why that order is
        load-bearing), so the empty repaint that puts the unhighlighted reading
        view back arrives here with no search left to describe and blanks the
        strip in the same breath. Functional-spec §2.9 AC1 asks for the
        highlighting to go when the search does; a count left behind reading
        "18 matches" over an unhighlighted game would be that same leak wearing
        different clothes.

        **Nothing here moves the game.** ``Static.update`` refreshes with
        layout — ``width: auto`` means the strip really does resize as the
        number's digits change — but the row it sits in is a fixed ``height: 1``
        sibling of the scroller, so re-arranging it cannot change the scroller's
        height, its ``max_scroll_y``, or the clamp that reads them.
        Functional-spec §2.8 AC2 asks for exactly that of the no-match case, and
        gets it from the layout rather than from care taken here.

        Args:
            matches: How many matches are currently lit — the length of the very
                list :meth:`_repaint_matches` just handed the body.
        """
        search = self._search
        self.query_one("#search-count", Static).update(
            _search_count_text("" if search is None else search.query, matches)
        )

    def _update_search_subtitle(
        self, total: int, current: int | None, *, wrapped: bool
    ) -> None:
        """Write the search's position into the header — or put :attr:`SUB_TITLE` back.

        The screen's half of :func:`_search_status_text`, exactly as
        :meth:`_update_search_count` is :func:`_search_count_text`'s: the
        function owns the copy and the reasoning behind every character of it,
        this method owns where the query comes from and what "nothing to say"
        means on a screen.

        **This is the channel that outlives the prompt** (tech-spec §2.7). After
        Enter the search row is hidden, so ``#search-count`` is gone — yet
        functional-spec §2.8 AC3 still owes the reviewer which match of the total
        they are on, and §2.6 still owes them a wrap notice on every ``n``/``N``
        that comes round the end of the game. :attr:`Screen.sub_title` is a
        ``Reactive`` the existing :class:`~textual.widgets.Header` is already
        watching, so this costs **zero extra rows** — which is why it is the
        channel rather than a second ``Static``: §2.10 promises the reading area
        is unchanged, and a widget that only exists while the bar is open would
        answer neither criterion.

        **``SUB_TITLE`` is restored from the class constant, not from a
        remembered previous value.** The resting sub-title is a constant on this
        screen, so there is nothing to save and nothing that can go stale;
        ``Screen.__init__`` seeds ``sub_title`` from it the same way. This is
        also where functional-spec §2.9 AC1's "the search is gone" reaches the
        header: :meth:`_clear_search` drops :attr:`_search` **before** it
        repaints (its docstring says why that order is load-bearing), so the
        empty repaint that puts the unhighlighted reading view back arrives here
        with no search left to describe, and the header goes back to the
        back-out hint in the same breath as the amber leaves the game. A header
        still counting off "3/18" over an unhighlighted game would be that same
        leak wearing a hat.

        **Nothing here can move the game.** The ``Header`` is docked, ``height:
        1``, and outside ``#transcript-scroll`` — so however long this string
        gets, it cannot change the scroller's height, its ``max_scroll_y``, or
        the clamp both jumps read them for. That is what makes it safe to write
        *inside* the jump's own repaint.

        Args:
            total: How many matches the live query has — the length of the very
                list :meth:`_repaint_matches` just handed the body.
            current: Which of them the reviewer is on, or ``None``.
            wrapped: Whether the jump that landed on ``current`` wrapped.
        """
        search = self._search
        status = (
            None
            if search is None
            else _search_status_text(search.query, total, current, wrapped=wrapped)
        )
        self.sub_title = self.SUB_TITLE if status is None else status

    def on_input_changed(self, event: Input.Changed) -> None:
        """Keep the query in step with the box, and light up the matches — shortly.

        The whole of functional-spec §2.2 — "every time the reviewer adds or
        removes a character, the highlighting updates" — with the repaint itself
        deferred by :data:`_SEARCH_DEBOUNCE_SECONDS` (see
        :meth:`_schedule_repaint` for the shape and the constant for the
        number). The *query* is updated here and now, so whatever runs next
        reads what is actually in the box.

        **Nothing here reads or writes ``scroll_offset``, and §2.2 asks for
        exactly that**: "the view does not move to hunt for matches elsewhere,
        so what lights up is what was already in front of the reviewer". The
        repaint styles the whole document rather than the viewport, so matches
        below the fold are already lit when the reviewer scrolls down to them
        (§2.4 AC2) without this handler ever having to go looking for one.
        Moving the view to a match is Enter's job (§2.5), from Slice 3.
        """
        # SCOPED BY TYPE, not by id, because this screen's box deliberately has
        # no id (see `compose` — the screen reaches it by type and it reaches
        # the screen by message). `LedgerTableScreen` runs a `#search` `Input`
        # of its own for the runs-list filter — a different surface on a
        # different screen, which functional-spec §3 puts out of this spec's
        # scope — and guards its own handler the mirror-image way. The two
        # screens cannot exchange an `Input.Changed` today, so this guard is not
        # load-bearing against a bug that exists; it is here so that "which box
        # is this handler about" is written down rather than inferred from a
        # screen-tree fact a later refactor could quietly change.
        if not isinstance(event.input, TranscriptSearchInput):
            return
        # THE RE-ENTRANCY GUARD (tech-spec §3's `input.value = ""` risk row),
        # and it closes a loop rather than being defensive padding.
        # `_clear_search` empties the box on the way out; `value` is a plain
        # reactive, so that assignment POSTS an `Input.Changed` which arrives
        # back here after the search has already ended. That method drops
        # `_search` BEFORE the clear — its docstring says to keep the order —
        # precisely so this line is unconditionally true on the echo and the
        # clear cannot reach back into the code unwinding it. Neither half works
        # alone: without the guard the echo repaints a search that is gone,
        # without the ordering the guard has nothing to read. Keep both.
        #
        # It answers a stray `Changed` at any other search-less moment too, and
        # the answer is the same: there is no query to update and nothing to
        # paint.
        if self._search is None:
            return
        self._search.query = event.value
        # THE CURRENT MATCH DOES NOT SURVIVE AN EDIT (spec 044, Slice 4).
        # `current` indexes into the match list of the query it was resolved
        # against, and this keystroke has just made a different query: match 3
        # of `vote` is not match 3 of `votes`, and is usually not even the same
        # part of the game. Keeping it would repaint the stronger marker on an
        # unrelated match — a readout the reviewer would believe, with nothing
        # on screen to contradict it. Dropped here, at the one place the query
        # changes, rather than by every reader remembering to re-derive it.
        #
        # `scroll_at_landing` goes with it, both-or-neither (spec 044, Task
        # 4.4a — see `SearchState.scroll_at_landing`): it means nothing except
        # "where the view was when `current` was landed on", so a landing
        # position outliving the landing it describes is dead state. The
        # behavioural consequence is the intended one — after an edit the next
        # `n` has no landing to step from and resolves from the viewport again,
        # which is where a reviewer who has just changed what they are looking
        # for actually is.
        self._search.current = None
        self._search.scroll_at_landing = None
        self._schedule_repaint()

    def _schedule_repaint(self) -> None:
        """(Re)open the debounce window that ends in one repaint of the game.

        **A RESTARTING SINGLE-SHOT timer** — not a periodic one, and not a
        "repaint at most every N ms" throttle. Each keystroke stops the pending
        window and opens a fresh one, so the repaint happens once, after the
        typing stops, on the query the reviewer settled on. A throttle would
        instead repaint the FIRST query of a burst, which is the one keystroke
        whose result the reviewer is least interested in, and would still owe a
        trailing repaint for the last.

        ``set_timer`` builds a ``Timer`` with ``repeat=0`` — it fires once and
        stops — and returns it. Keeping the handle is what makes the restart
        possible at all, and it is also what :meth:`_cancel_pending_repaint`
        needs so a window can be closed *without* a repaint when the search
        ends under it.
        """
        self._cancel_pending_repaint()
        self._repaint_timer = self.set_timer(
            _SEARCH_DEBOUNCE_SECONDS, self._repaint_settled_query
        )

    def _cancel_pending_repaint(self) -> None:
        """Close an open debounce window without letting it repaint.

        Called when a window is being replaced by a newer one, and when the
        search ends. Textual would stop the timer on its own *eventually* —
        ``MessagePump._close_messages`` stops everything in ``self._timers`` —
        but eventually is after the screen has gone, and the case that matters
        is a live screen whose search has ended. So it is stopped here,
        synchronously, at the moment the thing it serves stops existing.

        ``Timer.stop`` is safe on a timer that has already fired: it cancels a
        task that is already done, which does nothing. Dropping the handle
        afterwards keeps "is a repaint pending?" answerable by reading the
        field.
        """
        if self._repaint_timer is None:
            return
        self._repaint_timer.stop()
        self._repaint_timer = None

    def _repaint_settled_query(self) -> None:
        """The debounce window expired — light up whatever is typed now.

        **Reads the query back out of :attr:`_search` instead of being handed it
        when the window opened**, which is what makes a coalesced burst paint
        the right thing: every keystroke in the burst wrote ``query`` and
        restarted the window, so by the time this runs the field holds what the
        reviewer ended up with rather than what they started with.

        **The state check here is a second guard doing a different job** from
        :meth:`on_input_changed`'s. That one stops a repaint being *scheduled*
        after a search has ended; this one stops a scheduled repaint *landing*
        if the search ends between the last keystroke and the window expiring —
        Esc within :data:`_SEARCH_DEBOUNCE_SECONDS` of a keystroke, which is an
        ordinary thing for a reviewer to do. :meth:`_cancel_pending_repaint`
        already makes it unreachable; it is kept because of what the failure
        would look like if either lock were ever removed — amber text under no
        search, with nothing on screen to explain it and no traceback to find.

        Matches are found in :attr:`TranscriptBody.text` — the string the widget
        renders — so the offsets are offsets into the very text the spans are
        applied to. See that property for why a fresh read of the file is not
        guaranteed to be the same string.
        """
        # The window has fired; the handle is spent whatever happens next.
        self._repaint_timer = None
        search = self._search
        if search is None:
            return
        body = self.query_one("#transcript-body", TranscriptBody)
        # `search.current` is `None` on every path that reaches here —
        # `on_input_changed` is the only scheduler of this window and it drops
        # the index in the same breath as it writes the query. Passed rather
        # than hardcoded so that this method states "paint whatever the search
        # says is current" and cannot go stale if a later slice schedules a
        # repaint from somewhere the index does survive.
        self._repaint_matches(find_matches(body.text, search.query), search.current)

    def _row_starts(self, body: TranscriptBody) -> list[int]:
        """The offset → rendered-row map for the game, at the width it wraps to *now*.

        :func:`~graphia.eval_ledger.find_matches` answers "where in the text";
        scrolling is measured in rendered rows, and this is the only bridge
        between the two. It is a pure function of (text, width) —
        :func:`~graphia.eval_ledger.wrap_row_starts` mirrors Textual's wrapping
        by calling the very function Textual calls — so all this method owns is
        *which width*, and *when to rebuild*.

        **The width is read live, every time, and hardcoding it would mis-wrap
        every real game.** ``body.content_size.width`` is the widget's region
        minus its gutter, and it is **96 for a game that overflows the viewport
        and 98 for one that fits** at a 100-column terminal: the vertical
        scrollbar an overflowing game grows takes two cells, and
        ``#transcript-body``'s ``padding: 0 1`` takes two more. 96 and 98 wrap
        differently on essentially every line of an 88 KB game, so a map built
        at the wrong one puts the jump on the wrong row and stays wrong.
        Opening the search row can even flip a *borderline* short game between
        the two — it costs the scroller a viewport row (measured 22 → 21), and a
        game that fitted 22 rows but not 21 acquires a scrollbar and loses two
        columns — which is precisely why the answer is asked of the widget at
        jump time rather than remembered from when the search opened.

        **Cached on the width it was built at, and rebuilt only when that
        differs** (:attr:`SearchState.row_map`). A full rebuild of the worst
        committed game — 88 KB, 405 source lines, 1,182 rendered rows — is
        **7.6 ms**, which is cheap enough that a lazy rebuild needs no
        ``Resize`` handler behind it: the cost is paid once per width the
        reviewer actually presses Enter at, instead of once per intermediate
        width of a terminal drag. Worth knowing while reading this: Textual does
        **not** remap ``scroll_y`` across a resize, so the reading view already
        loses the reviewer's place on a resize today — this method inherits that
        rather than introducing it, and rebuilding here restores a *correct* map
        for wherever the view has ended up.

        With no search in progress the map is simply not cached (there is
        nowhere to put it); that path costs one rebuild and is not on any
        keystroke's way.

        Args:
            body: The transcript body — asked for both its text and its live
                width, so the two can never come from different moments.

        Returns:
            The offset each rendered row starts at; its length is the row count.
        """
        width = body.content_size.width
        search = self._search
        if search is not None and search.row_map is not None:
            cached_width, cached_starts = search.row_map
            if cached_width == width:
                return cached_starts
        row_starts = wrap_row_starts(body.text, width)
        if search is not None:
            search.row_map = (width, row_starts)
        return row_starts

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter jumps to the first match at or below where the reviewer is (§2.5).

        The whole of functional-spec §2.5, and the keypress that makes this
        feature a navigation tool rather than a colouring one: the first match
        **at or below** the current top row comes to rest **on** the top row, the
        prompt closes, and the highlights stay.

        **Enter is INCLUSIVE, and that is §2.5 AC2 rather than a preference.**
        "A match already on the top line counts as *at* the current position and
        is where Enter lands — it does not skip ahead." So the reviewer's
        position is converted to an offset and handed to
        :func:`~graphia.eval_ledger.next_match` with ``inclusive=True``, and
        pressing Enter on a match you are already looking at moves nothing.

        **Note the asymmetry with Slice 4's ``n``/``N``, which is deliberate and
        must not be smoothed away.** Enter is *offset-keyed and inclusive*;
        ``n``/``N`` are *row-keyed and exclusive* — they skip past every match
        sharing the current row, because §2.6 requires a press to always move the
        view. Making Enter row-keyed too would break §2.5 AC2 directly.

        **This is the OPEN-PROMPT Enter only, and spec 044's Slice 6 gave the
        key a second meaning that does not pass through here.** With the prompt
        closed, `enter` is a screen binding onto ``action_next_match`` and steps
        like `n` (§2.6, amended 2026-09-14) — because the inclusive jump above,
        repeated, is a no-op loop: after a landing the reviewer is already
        standing on a match, so Enter answers with the match they are on. The
        two never collide, and the reason is focus rather than a flag: while the
        bar is open the focused :class:`TranscriptSearchInput` consumes the key
        and posts ``Input.Submitted``, so ``App._check_bindings`` never reaches
        the screen's binding at all. Measured, not assumed — see the `enter`
        entry in ``BINDINGS``.

        **Nothing typed and nothing found LOOK alike and are NOT the same case**
        (spec 044, Task 5.2a — the author's decision of 2026-09-14). Neither one
        moves the game: :func:`~graphia.eval_ledger.find_matches` returns ``[]``
        for an empty query and ``next_match`` returns ``None`` for an empty list,
        so §2.5 AC5 ("Enter with nothing typed … nothing moves and the search
        simply closes") and §2.8 AC4 ("a word with no matches … nothing moves and
        no error appears") are both the *absence* of a scroll. What differs is
        what the reviewer is left holding, and it is invisible from the outside —
        which is exactly why it has to be got right here:

        - **Empty — "I did not ask anything."** Nothing is lit, ``#search-count``
          says nothing, the header is back at :attr:`SUB_TITLE`. That is
          character-for-character the screen of a reviewer who never pressed
          ``/``, so a live :class:`SearchState` behind it is a state they cannot
          see — and the next Esc would be silently spent clearing it, against
          §2.9 AC3's promise that leaving "costs them no extra keypress". So an
          empty query is routed through :meth:`_clear_search` and the search
          genuinely ends, matching what the screen already shows.
        - **Typed but matching nothing — "I asked, and there was nothing."** The
          query is real and the count says so in as many words (§2.8 AC2); the
          reviewer is mid-search and will edit it or abandon it. That is state 2
          of :class:`SearchState`'s three and it stays alive exactly as before:
          the first Esc clears it, the next leaves the game (§2.9 AC5).

        Collapsing the two in either direction breaks one criterion to satisfy
        the other. And the test is ``not search.query``, **not**
        ``not search.query.strip()``: :func:`~graphia.eval_ledger.find_matches`
        treats whitespace as ordinary literal text — a single space has 14,524
        matches on the 88 KB committed game — so a space is *typed*, and
        stripping would send the most-matching query in the corpus down the "you
        asked nothing" path.

        **The search survives, and only the prompt closes.** ``_search`` stays
        non-``None`` — that is state 2 of the three :class:`SearchState`
        documents, the one that only exists from this slice onward, and
        :meth:`action_close` already branches on it: an Esc after this jump
        clears the highlighting and keeps the reviewer *in the game*, and a
        further Esc leaves (§2.9 AC5). Routing Enter through
        :meth:`_clear_search` would dump the highlights §2.5 AC4 promises will
        still be there — the reviewer jumped in order to read the match in
        context, and the other matches staying lit is how they see what is
        nearby.

        That survival is for every query the reviewer actually typed, **including
        one with no matches** — see the two bullets above. The single exception
        is the empty one, which ends the search instead, and it is an exception
        precisely *because* it is not unpredictable: the rule a reviewer can
        apply without looking is "Esc clears what you can see". A search you can
        see (amber, or a count, or a no-match message) survives Enter and costs
        an Esc; a search that shows nothing because you typed nothing does not
        exist to be cleared.
        """
        # SCOPED BY TYPE for the same reason `on_input_changed` is: this
        # screen's box has no id, and `LedgerTableScreen` runs an `Input` of its
        # own whose `Submitted` means something entirely different (commit the
        # runs-list filter and focus the table).
        if not isinstance(event.input, TranscriptSearchInput):
            return
        # No search, nothing to jump to. Unreachable in practice — the box is
        # only focusable while the bar is open, and `action_open_search`
        # establishes the state *before* it opens the bar precisely so that
        # invariant holds — but this method would otherwise be the one place
        # that dereferences it without asking.
        search = self._search
        if search is None:
            return

        # NOTHING TYPED ENDS THE SEARCH; NOTHING FOUND DOES NOT (spec 044, Task
        # 5.2a). The docstring above argues the distinction; the two things this
        # branch has to get right are WHICH TEST and WHERE.
        #
        # WHICH TEST: `not search.query`, never `not search.query.strip()`.
        # `find_matches` is case-smart but otherwise literal, and whitespace is
        # literal text to it — a single space matches 14,524 times on the 88 KB
        # committed game. A reviewer who typed a space asked a question with more
        # answers than any other query in the corpus, so stripping here would
        # throw away their search on the grounds that it "looked empty".
        #
        # WHERE: before `find_matches`, before `_row_starts`, before the body and
        # the scroller are even looked up. `_row_starts` is the one that matters
        # — it builds AND CACHES the whole wrap map, 1,182 rows and 7.6 ms on
        # that same game, to answer "where is the first match at or below here?"
        # for a query that has no matches anywhere and a state that is about to
        # be dropped. Clearing first is not a micro-optimisation bolted onto a
        # correctness fix; it is the same fix, since the map would be built into
        # a `SearchState` that ceases to exist two lines later.
        #
        # `_clear_search` is the whole undo, and is already load-bearing
        # elsewhere (Esc reaches it too): it drops the state first, cancels any
        # debounce window still open, repaints the game with no matches, blanks
        # the count, puts `SUB_TITLE` back, hides the row and returns focus to
        # the scroller. So Enter-on-empty and Esc leave the screen in *identical*
        # states, which is the property §2.9 AC3 actually needs — and nothing on
        # that path assigns `scroll_offset.y`, so §2.5 AC5's "nothing moves"
        # holds as it did before.
        if not search.query:
            self._clear_search()
            return

        body = self.query_one("#transcript-body", TranscriptBody)
        scroller = self.query_one("#transcript-scroll", VerticalScroll)

        # ONE MATCH LIST, USED FOR BOTH THE JUMP AND (IF IT IS OWED) THE PAINT.
        # Offsets into `body.text` — the string the widget actually renders,
        # which is what makes them safe to hand to both the highlighter and the
        # row map.
        matches = find_matches(body.text, search.query)
        # SETTLE A DEBOUNCE WINDOW THAT IS STILL OPEN, so the jump and the
        # highlighting cannot describe two different queries. Pressing Enter
        # within `_SEARCH_DEBOUNCE_SECONDS` of the last keystroke is the
        # ordinary way to use this feature — type, Enter — and it is exactly the
        # window in which the screen is still showing the *previous* query's
        # matches. Cancelling here is what stops that timer firing afterwards
        # and repeating the repaint this method ends with.
        #
        # Since Slice 4 the repaint itself is unconditional and lives at the end
        # of this method rather than inside this branch: Enter now RECORDS which
        # match it landed on, and `transcript--match-current` has to be painted
        # for the reviewer to see it. So there is no longer a case where Enter
        # has nothing to repaint — even an already-settled query needs the
        # marker moved. (What was saved by skipping it is one re-render, up to
        # 300 ms on tech-spec §2.8's worst measured query; what it now buys is
        # the whole of §2.6's "they can tell which one they are on", so the
        # trade is not close.)
        self._cancel_pending_repaint()

        row_starts = self._row_starts(body)
        # `scroll_offset.y` IS A ROW; `next_match` WANTS AN OFFSET. Converting
        # through `offset_for_row` is the whole of the bridge, and it is what
        # makes "at or below the reviewer's current position" mean the first
        # character of the top rendered row — so a match that *begins* above the
        # fold and merely runs onto the top row is behind the reviewer and is
        # skipped, while one that begins on the top row is landed on.
        found = next_match(
            matches,
            offset_for_row(row_starts, scroller.scroll_offset.y),
            inclusive=True,
        )
        # FALSE UNTIL A JUMP SAYS OTHERWISE — the no-match and nothing-typed
        # exits fall through to the repaint below with this still unset, and
        # there `current` is `None` so it is unread. Declared before the branch
        # rather than inside it because the repaint is unconditional (see the
        # comment on it), so the flag has to survive the branch not being taken.
        wrapped = False
        if found is not None:
            # `wrapped` IS CONSUMED NOW (spec 044, Task 4.5) — it used to be
            # dropped here with a note pointing at this task. The jump itself is
            # identical either way, which is exactly why it needed a channel of
            # its own: §2.5 AC3 asks that a backwards jump never be silent, and
            # movement alone cannot say "this went round the end of the game"
            # because a wrap to the first match looks like any other scroll. It
            # travels to `Screen.sub_title` through the repaint at the foot of
            # this method — the one status channel that outlives the prompt this
            # method is about to hide.
            index, wrapped = found
            # ENTER IS A LANDING, SO IT SETS `current` (spec 044, Slice 4).
            # §2.6's "the match the reviewer last landed on is marked more
            # strongly than the others" is about the last landing, whichever key
            # made it — otherwise the marker would appear only on the first `n`
            # after an Enter, and a reviewer who typed, jumped, and read would
            # never see it at all.
            #
            # AND `n`/`N` DO READ IT BACK — corrected in Task 4.4a, because the
            # separation this comment used to claim ("nothing later reads this
            # back to decide where to go next") is exactly what dead-ended `n`
            # at the end of a game. A landing is where the next step starts
            # from unless the reviewer has moved since; `_step_origin_row` owns
            # that question and `scroll_at_landing`, written on the next line,
            # is what lets it be answered as a fact.
            search.current = index
            target_row = row_for_offset(row_starts, matches[index][0])
            # THE END-OF-GAME CLAMP (functional-spec §2.5, fourth bullet). A
            # match inside the final screenful CANNOT reach the top line,
            # because there is nothing past the end of the game to scroll into
            # view — it comes to rest as close to the top as the game allows.
            # `less` has exactly this limit for exactly this reason, and the
            # author chose it over padding the view with blank rows that are not
            # part of the game (tech-spec §2.5).
            #
            # `scroll_y` would clamp at `max_scroll_y` on its own; saying it
            # here is the difference between a limit that is *stated* and one
            # that is merely survived — and it is what makes the clamp testable
            # as the intended landing rather than as a coincidence of Textual's.
            #
            # `animate=False, immediate=True`: `Widget.scroll_to` otherwise
            # defers the actual move through `call_after_refresh`, so the
            # landing would not be observable until a further refresh cycle.
            # Together the two make the jump synchronous and deterministic,
            # which every test of this behaviour depends on.
            scroller.scroll_to(
                y=min(target_row, scroller.max_scroll_y),
                animate=False,
                immediate=True,
            )
            # ...AND THE POSITION IT CAME TO REST AT, READ BACK OUT OF THE
            # SCROLLER (spec 044, Task 4.4a). Read back rather than assumed
            # equal to the `min(...)` above, because they are two different
            # claims — one is what this method asked for, the other is what the
            # widget accepted — and only the second is the "where were they
            # when they landed" `_step_origin_row` needs.
            #
            # `immediate=True` is what makes this line legal at all: without
            # it `scroll_to` defers the move and this would record the position
            # the reviewer was leaving rather than the one they arrived at.
            #
            # It is still one row optimistic in the clamped case, and
            # `_step_origin_row` re-clamps it live rather than this line trying
            # to predict the future — measured, with the numbers, there.
            search.scroll_at_landing = scroller.scroll_offset.y

        # ONE REPAINT, AFTER THE JUMP, SAYING THREE THINGS AT ONCE: these are the
        # query's matches, this is the one you are on, and (since Task 4.5) this
        # is how you got here. It replaces the conditional settle that used to
        # sit above the jump (see there for why the condition is gone), and it
        # runs on every Enter that reaches it, including the no-match one — where
        # `matches` is empty and `current` is `None`, so it repaints the
        # unhighlighted reading view, writes the no-match message into the count
        # and puts `SUB_TITLE` back, which is exactly what §2.8 AC4 asks for.
        # That last part is what keeps an edit-then-Enter from leaving the
        # previous query's position standing in the header over a game that no
        # longer has it.
        #
        # (Task 5.2a took the *nothing-typed* Enter out from under this line: it
        # leaves through `_clear_search` above, which does the same repaint plus
        # the state drop. So "empty" no longer arrives here, and the empty
        # `matches` this comment describes now means "typed, and found nothing".)
        #
        # After the scroll rather than before it for the same reason the bar is
        # hidden after: `_update_search_count` re-lays-out the count strip, and
        # keeping every number in the jump's arithmetic from one moment is
        # cheaper to reason about than proving a `height: 1` sibling cannot
        # change `max_scroll_y`. (The header status written on the same call has
        # no such question to answer: `Header` is docked outside the scroller.)
        self._repaint_matches(matches, search.current, wrapped=wrapped)

        # THE PROMPT CLOSES, THE SEARCH DOES NOT (§2.5 AC4). Two lines, and
        # neither is `_clear_search`: `_search` survives, the box keeps what was
        # typed (so `/` re-opens the search the reviewer already has rather than
        # a blank one), and the amber stays on every match.
        #
        # AFTER the scroll, deliberately. Hiding the row hands a viewport row
        # back to the scroller, which re-lays-out on the next refresh and lowers
        # `max_scroll_y` by one; doing it first would mean clamping against a
        # `max_scroll_y` that no longer matches the geometry the row map was
        # built at. Scrolling first keeps every number in the calculation above
        # from the same moment, and the worst the re-layout can then do is pull
        # a reviewer parked on the very last row back by one — the same
        # re-fitting `_clear_search` already documents.
        self.query_one("#search-row", Horizontal).remove_class("-open")
        # Focus goes back to the game: the reviewer is reading again, so their
        # arrow keys must scroll — and Slice 4's `n`/`N` are *screen* bindings,
        # which a focused `Input` would take as literal characters.
        scroller.focus()

    def action_next_match(self) -> None:
        """`n` — move to the next rendered row that holds a match (§2.6).

        **Two keys, one action** (spec 044, Task 6.3): `enter` is bound here
        too, so pressing Enter with the prompt closed steps exactly as `n` does
        and the two can never drift into separate implementations. Enter
        *inside* the prompt never reaches this — the focused
        :class:`TranscriptSearchInput` consumes the key and posts
        ``Input.Submitted``, which is §2.5's inclusive jump
        (:meth:`on_input_submitted`). See the `enter` binding for the
        measurement behind that claim.

        Both directions are :meth:`_step_match`; this and
        :meth:`action_previous_match` exist so the two BINDINGS read as the two
        things they are rather than as one action with a boolean spelled out in
        a binding string. Textual can pass arguments through a binding
        (`"step_match(True)"`), and the reason not to here is that the Footer,
        the key map and every traceback would then show the caller a flag
        instead of a direction.
        """
        self._step_match(forward=True)

    def action_previous_match(self) -> None:
        """`N` — move back to the previous rendered row that holds a match (§2.6)."""
        self._step_match(forward=False)

    def _step_match(self, *, forward: bool) -> None:
        """Walk one row-with-a-match in ``forward``'s direction, and land on it.

        The whole of functional-spec §2.6's movement, and the mirror of
        :meth:`on_input_submitted`'s jump with **one deliberate difference**:
        where Enter is offset-keyed and inclusive, this is **row-keyed**.

        **Why row-keyed, restated here because this is where it is spent.**
        Several matches routinely fall on one rendered row, and a jump scrolls
        by *row* — so stepping match-by-match would spend whole presses moving
        between two matches the reviewer can already see side by side, with the
        view not moving at all. :func:`~graphia.eval_ledger.step_match_by_row`
        therefore skips past every match sharing the current row and answers with
        the **first** match on the next row that has one, in both directions.
        §2.6 says it in as many words: *"a press is never spent moving between
        two matches the reviewer can already see together"*.

        **The step is row-keyed; what changed in Task 4.4a is WHICH row.** The
        origin comes from :meth:`_step_origin_row` — the current match's row
        when the reviewer has not scrolled since landing on it, and
        ``scroll_offset.y`` when they have. Keying it on ``scroll_offset.y``
        unconditionally, which is how this method was first built, dead-ends
        every query with matches in the last screenful of a game; that method's
        docstring carries the measurement and the reason.

        Note what did **not** change, because both were the author's decisions
        and neither is in question here: the step stays row-keyed, so row-mates
        are still skipped (``current ± 1`` would re-admit the press that does
        not move, which is the trap tech-spec §2.6 was corrected out of); and
        the end of a game stays clamped rather than padded with rows that are
        not part of the game.

        **No search, no movement, no error** (§2.6's final criterion). The guard
        is the first statement below, so `n` on a game nobody has searched is a
        key that does nothing — which is what it was before this feature existed.
        An empty or unmatched query takes the same exit one step later, because
        :func:`~graphia.eval_ledger.step_match_by_row` answers ``None`` for an
        empty match list.

        Args:
            forward: ``True`` for `n`, ``False`` for `N`. Passed straight through
                to the pure step, which owns both wraps.
        """
        # §2.6: "Given no search has been made since the game was opened, when
        # the reviewer presses `n`, then nothing happens and no error appears."
        # Note this is the ONLY state that can reach here with no search: the
        # keys are screen bindings, so they arrive whenever the game has focus —
        # which is most of the time.
        search = self._search
        if search is None:
            return

        body = self.query_one("#transcript-body", TranscriptBody)
        scroller = self.query_one("#transcript-scroll", VerticalScroll)

        # Re-found rather than remembered, exactly as Enter re-finds them: the
        # offsets are into `body.text`, ~1 ms to compute, and a stored copy would
        # be a second answer to keep in step with the query (see `SearchState`).
        matches = find_matches(body.text, search.query)
        row_starts = self._row_starts(body)
        found = step_match_by_row(
            matches,
            row_starts,
            # NOT `scroller.scroll_offset.y` — that is the Task 4.4a defect, and
            # `_step_origin_row` is where it is written down.
            self._step_origin_row(search, matches, row_starts, scroller),
            forward=forward,
        )
        if found is None:
            # Nothing typed, or typed and matching nothing. Not an error, and
            # not a state worth announcing: the count strip already says "no
            # matches", so a press that does nothing is the honest response.
            #
            # NOTHING IN THE HEADER GOES STALE ON THIS EXIT either, and it does
            # not need a line to make sure: `found is None` means the match list
            # is empty, and an edit that empties it has already dropped
            # `current` (`on_input_changed`) — which is the only thing
            # `_search_status_text` renders. So there is no position left in the
            # header for this press to contradict.
            return

        # `wrapped` IS CONSUMED NOW (spec 044, Task 4.5) — it used to be dropped
        # here with a note pointing at this task. It matters MORE here than on
        # Enter: §2.6 promises both wraps are announced, and inside the last
        # screenful of a game the clamp at `max_scroll_y` means an `n` that
        # wraps round to the first match may not move the view at all, so this
        # flag and the current-match marker are the whole of what tells the
        # reviewer the key did anything.
        index, wrapped = found
        search.current = index
        target_row = row_for_offset(row_starts, matches[index][0])
        # The same clamp, and the same reasoning, as the Enter jump above: a
        # match inside the final screenful cannot reach the top line because
        # there is nothing past the end of the game to scroll into view. Here it
        # is what makes the current-match marker load-bearing rather than
        # decorative — inside that last screenful successive presses produce no
        # scrolling at all, so the marker moving (and Task 4.5's count) is the
        # reviewer's only evidence that the key did anything.
        scroller.scroll_to(
            y=min(target_row, scroller.max_scroll_y), animate=False, immediate=True
        )
        # THE LANDING IS RECORDED, NOT JUST MADE (spec 044, Task 4.4a) — read
        # back off the scroller for `on_input_submitted`'s reason, and the thing
        # that makes the NEXT press able to start from this match rather than
        # from a viewport that may be pinned at the clamp. Nothing re-lays-out
        # the scroller on this path (the search row is already hidden, and the
        # count strip lives inside it), so unlike Enter's there is no re-fit
        # between this read and the next press.
        search.scroll_at_landing = scroller.scroll_offset.y
        # The marker moves with the view — one repaint, from the same match list
        # the step resolved against, so what is lit, what is counted and what the
        # header says cannot describe two different queries. The header's share
        # of it is §2.8 AC3's position and §2.6's wrap notice, both of which have
        # to reach a reviewer whose search prompt closed several keypresses ago.
        self._repaint_matches(matches, index, wrapped=wrapped)

    def _step_origin_row(
        self,
        search: SearchState,
        matches: Sequence[tuple[int, int]],
        row_starts: list[int],
        scroller: VerticalScroll,
    ) -> int:
        """Which rendered row ``n``/``N`` step FROM (spec 044, Task 4.4a).

        Two origins, and functional-spec §2.6 needs both:

        * **The current match's row**, when the reviewer has not scrolled since
          landing on it. This is what makes the last screenful of a game
          navigable at all — see the defect below.
        * **``scroll_offset.y``**, when they have. §2.6: *"the reviewer has
          scrolled by hand away from the match they were on… `n` follows where
          they are, not where they last jumped from."*

        **THE DEFECT THIS EXISTS TO FIX — measured, so that nobody simplifies
        the cursor back into the scroll position.** ``n``/``N`` were keyed on
        ``scroll_offset.y`` alone. But the scroll **clamps** at ``max_scroll_y``,
        so the scroll position cannot represent any position inside the final
        screenful — and whenever a query has matches there, every press
        re-answers the same one. On the committed 88 KB game
        (``evals/transcripts/2026-09-05T17-58-24/game-01.txt``, 1,182 rendered
        rows at width 96, ``max_scroll_y`` 1160) the query ``the`` has 702
        matches with the last on row **1178**, and from the bottom of the game
        four consecutive ``n`` presses all answered match 692 on row 1163:
        matches 693-701 unreachable, the wrap never firing, the view not moving,
        and the marker frozen — so even "the marker is your feedback inside the
        clamp" was absent. Two §2.6 criteria were unsatisfiable by construction:
        the ``n`` wrap, and *"a press never stops on the line it started from"*.
        Query ``vote`` (last match row 1030, below the clamp) behaved correctly
        throughout, which is why it took a query reaching past 1160 to see it.

        **Why the question is answered from a RECORDED position.** Comparing
        ``current``'s row against ``scroll_offset.y`` looks like the same test
        and is not: at the clamp the two legitimately differ (row 1178, scroll
        1160), which is the defect restated. So the landings record where they
        came to rest (:attr:`SearchState.scroll_at_landing`) and this asks
        whether the view is still there.

        **The one wrinkle, also measured: the recorded landing can be a row
        optimistic, and only Enter's can.** Enter scrolls while the search bar
        is still open and hides it afterwards, handing the scroller back a
        viewport row; the re-fit lowers ``max_scroll_y`` by one and pulls a
        reviewer parked at the clamp back with it. On that same game a tail
        Enter records **1161** and settles at **1160**. So the recorded position
        is re-clamped against the scroller **as it stands now** before being
        compared — the same ``min(…, max_scroll_y)`` both landings already apply
        to their targets, applied to the number they wrote down. Predicting the
        post-re-fit position at landing time, or deferring the record through
        ``call_after_refresh``, would both buy the same answer for more moving
        parts.

        With no landing (no Enter yet, or the query edited since — see
        :meth:`on_input_changed`) the answer is simply the viewport, which is
        both the honest one and what ``n`` did before this feature grew a
        ``current`` at all.

        Args:
            search: The live search; its ``current`` and ``scroll_at_landing``
                are the landing, both-or-neither.
            matches: The current query's matches, as ``current`` indexes them.
            row_starts: The row map at the body's live wrap width.
            scroller: Asked for both ``scroll_offset.y`` and ``max_scroll_y``,
                so the comparison and the clamp come from one moment.

        Returns:
            The rendered row to hand :func:`~graphia.eval_ledger.step_match_by_row`.
        """
        viewport_row = scroller.scroll_offset.y
        if search.current is None or search.scroll_at_landing is None:
            return viewport_row
        # Unreachable today — `on_input_changed` drops `current` on every edit
        # and the match list is a pure function of (body text, query), so the
        # index cannot outlive the list it points into. Kept because the
        # alternative to an unreachable branch here is an `IndexError` out of a
        # keypress, and the pure layer clamps rather than raises for exactly
        # that reason (`row_for_offset`, `offset_for_row`).
        if not 0 <= search.current < len(matches):
            return viewport_row
        if min(search.scroll_at_landing, scroller.max_scroll_y) != viewport_row:
            # They have scrolled. §2.6: `n` follows where they are.
            return viewport_row
        return row_for_offset(row_starts, matches[search.current][0])

    def on_transcript_search_input_scroll_game(
        self, message: TranscriptSearchInput.ScrollGame
    ) -> None:
        """Scroll the game by the action the search bar announced.

        The screen half of :class:`TranscriptSearchInput`'s decoupling: the bar
        names an action, this screen owns the id. **Without this handler the
        subclass is inert** — its six bindings post a message nobody listens to,
        so `up`/`down`/`pageup`/`pagedown`/`home`/`end` in the search bar all
        silently do nothing, and functional-spec §2.4 ("the game still scrolls
        while the reviewer is typing") quietly fails with no error to find. The
        :class:`~TranscriptSearchInput.ScrollGame` docstring says so out loud for
        the same reason.

        ``getattr`` rather than a dispatch table, deliberately: the message
        carries the scroller's OWN action name, copied verbatim from
        ``ScrollableContainer.BINDINGS``, so "what does ``pagedown`` do in the
        search bar?" resolves to the same method as "what does ``pagedown`` do
        in the game?" by construction. A mapping here would be a second place to
        maintain and a place to drift.

        **``SkipAction`` is caught, and it is the normal path rather than
        defensive padding.** All six actions on the installed Textual 8.2.4 open
        with ``if not self.allow_vertical_scroll: raise SkipAction()`` (``home``
        and ``end`` with the two-axis ``_allow_scroll``), and
        ``allow_vertical_scroll`` is ``is_scrollable and
        show_vertical_scrollbar`` — **false for a game that fits the viewport**,
        which plenty of preserved games do. Textual's own action dispatcher
        catches ``SkipAction`` and lets the key bubble; calling the method
        directly puts the raise inside a message handler instead, where it would
        reach the app's unhandled-exception screen. Measured: uncaught, ``down``
        in the search bar of a short game raises. Swallowed, the key does
        nothing — which is exactly what it already does on that same game when
        the scroller itself holds focus.

        ``value`` is never touched here, which is the other half of §2.4:
        scrolling mid-search must cost the reviewer nothing they have typed.
        """
        scroller = self.query_one("#transcript-scroll", VerticalScroll)
        try:
            getattr(scroller, f"action_{message.scroll_action}")()
        except SkipAction:
            # Nothing to scroll — a game shorter than the viewport. Not an error.
            pass
        message.stop()

    def action_open_search(self) -> None:
        """Reveal the search bar and put the cursor in it — without moving the game.

        The `/` binding. Functional-spec §2.1 is mostly a list of things this
        method must NOT do, so they are worth stating as guarantees rather than
        leaving as the absence of code.

        **It never touches ``scroll_y``, and it cannot.** Two separate mechanisms
        would have to move the game, and neither is reachable from here:

        - *Revealing the row* costs the scroller one row of viewport (measured
          ``scroll.size.height`` 22 → 21, ``max_scroll_y`` 1160 → 1161). A
          shorter viewport only ever RAISES the scroll ceiling, so the clamp that
          runs on resize can never pull ``scroll_y`` down — it stayed at 44
          across the toggle. ``body.content_size.width`` is unchanged at 96 too,
          because the row is a sibling above the scroller rather than anything
          that narrows it, so the text does not rewrap either: the reviewer's
          first visible line is still their first visible line, one row lower on
          the screen.
        - *Focusing the box* schedules a scroll-into-view
          (``Screen.set_focus(scroll_visible=True)`` → ``scroll_to_center``),
          which walks the focused widget's ANCESTOR chain. ``#transcript-scroll``
          is not on that chain — the bar is its SIBLING, not its child (see the
          ``#search-row`` rule for why), so the one scrollable container in this
          screen is not something focusing the bar can reach. This is the same
          layout fact that makes :class:`TranscriptSearchInput`'s six scroll
          bindings necessary, read from the other side.

        **It mounts nothing.** The row has existed since :meth:`compose`; opening
        is a class toggle, so there is no mid-read mount to resize the scroller
        at the moment §2.1 promises the text will not move. The stylesheet, not
        this method, owns what "open" looks like.

        **Opening while already open is harmless.** In practice a second `/`
        never arrives here at all — the focused ``Input`` takes it as a literal
        `/` typed into the query (measured in Task 1.2), which is the behaviour
        §2.4 asks for. Should it arrive anyway (the scroller holding focus with
        the bar already shown), ``add_class`` and ``focus`` are both no-ops on a
        widget that already has the class / the focus, so there is nothing to
        guard.

        **It establishes :attr:`_search`**, which is the one thing here that is
        not about leaving the game alone — see the body for why that pairing is
        an invariant rather than a convenience.
        """
        # ESTABLISH THE STATE BEFORE SHOWING THE BAR, so the invariant
        # `action_close` reads — AN OPEN BAR ALWAYS HAS A `SearchState` BEHIND
        # IT — holds from the instant the row becomes visible rather than from
        # whenever the first character arrives. Without this line, `/` followed
        # immediately by Esc would find `_search is None` and LEAVE THE GAME
        # instead of closing the bar the reviewer had just opened.
        #
        # CONDITIONAL, because a search can already exist with the bar hidden:
        # from Slice 3 onward Enter closes the prompt and keeps the search alive
        # (state 2 in `SearchState`'s docstring), and `/` there must re-open the
        # prompt on the search the reviewer already has rather than silently
        # throwing it away. So the fresh seed is the `None` branch only.
        if self._search is None:
            self._search = SearchState(query="")
        self.query_one("#search-row", Horizontal).add_class("-open")
        self.query_one(TranscriptSearchInput).focus()

    def action_close(self) -> None:
        """Clear a live search if there is one; otherwise leave the game.

        **THE BRANCH IS ON SEARCH STATE, NOT ON WHETHER THE BAR IS SHOWING, and
        "simplifying" it back to two states is the bug this docstring exists to
        prevent.** Functional-spec §2.9 is a three-state machine and
        :class:`SearchState` spells the three out; the short version is that
        from Slice 3 onward Enter leaves a search *alive with the bar hidden*,
        and an Esc there owes the reviewer a cleared highlight and a game they
        are still in — not a pop. ``_search is not None`` is the only question
        that separates that state from "no search at all". Asking the bar would
        answer "closed" to both.

        **Deliberate consequence, ranked and accepted rather than discovered:
        ``backspace`` and ``q`` share this action, so they too clear a live
        search before they leave.** Where that bites is precise, because it is
        not everywhere:

        - With the bar OPEN it never fires at all — the focused
          :class:`TranscriptSearchInput` takes ``q`` as a character and
          ``backspace`` as a deletion (measured in Task 1.2), so Esc is the only
          one of the three that reaches this method.
        - With a search live and the bar hidden, the scroller has focus and all
          three land here, clear the search, and stay. The reviewer then presses
          the same key again to leave.

        That costs a second keypress to exit a game the reviewer has searched
        in. Functional-spec §2.10 only promises an unchanged reading view to
        someone who never presses ``/``, so this is compliant — but it is a
        choice, and the alternative (routing ``q``/``backspace`` straight out
        past a live search) was not taken because it would make the same key
        mean "clear" or "leave" depending on invisible state.

        Leaving is unchanged for everyone else: one Esc, straight back to the
        :class:`DetailScreen` that pushed this screen (§2.9 AC3).
        """
        if self._search is None:
            self.app.pop_screen()
            return
        self._clear_search()

    def _clear_search(self) -> None:
        """Drop the search and put the screen back the way the reviewer had it.

        **The ordering is load-bearing: the state goes FIRST.** Assigning
        ``input.value = ""`` below fires :class:`Input.Changed` — ``value`` is a
        plain ``reactive("", init=False)`` on the installed Textual 8.2.4, so a
        *change* to it posts, and clearing a non-empty query is exactly such a
        change. Nothing consumes that message yet; Slice 2 adds the live
        re-highlight handler, and that handler will be re-entered by this very
        line unless something stops it. Dropping ``_search`` before the clear is
        what stops it, **by construction** rather than by the handler
        remembering to be careful: by the time the echo arrives the state is
        already gone, so the Slice-2 guard (``if self._search is None: return``)
        is unconditionally true and the clear cannot reach back into the code
        that is unwinding it. *That guard now exists — keep this order.*
        (Clearing an already
        empty box posts nothing at all — a non-``always_update`` reactive does
        not fire on an unchanged assignment — so the echo only exists when the
        reviewer had actually typed something.)

        **``scroll_offset.y`` is never touched, and that is the requirement, not
        an omission.** Functional-spec §2.9 AC1 asks that the game be "still at
        the same place on screen" after Esc, which matters precisely because
        §2.4 invited the reviewer to scroll while typing — the place they are
        abandoning the search from is usually not the place they opened it from,
        and Esc abandons the search, not their reading position. The one thing
        that can still move it is Textual's own clamp: closing the row hands a
        viewport row back to the scroller, which lowers ``max_scroll_y`` by one,
        so a reviewer parked on the very last row comes back one row with the
        same last line still on screen. That is the scroller re-fitting itself,
        not this method moving the text, and there is no scroll assignment here
        to make it otherwise.

        Focus returns to ``#transcript-scroll`` explicitly rather than being
        left to whatever Textual does with the focus of a widget that has just
        been hidden: the reviewer pressed a *back-out* key, so the game — the
        only thing left to interact with — should be what their arrow keys move.

        **Two callers now, and the second is not a back-out key** (spec 044, Task
        5.2a): :meth:`action_close` (Esc/``backspace``/``q``, above), and
        :meth:`on_input_submitted` when Enter arrives on an **empty** query.
        Nothing here needed changing to take it — an Enter that ends a search the
        reviewer never typed owes them exactly the screen an Esc would have
        left, which is the point of routing it here rather than writing a second
        half-undo beside it. Focus landing on the scroller is right for both:
        the bar is being hidden either way, and the game is what is left. (An
        Enter on a query that was *typed* does **not** come here — it keeps the
        search alive; :meth:`on_input_submitted`'s docstring argues why the two
        diverge.)
        """
        # STATE FIRST — see the docstring. Everything below is undoing the
        # visible half of a search that, as far as the rest of the screen is
        # concerned, has already ended.
        self._search = None
        # ...AND THE PENDING REPAINT WITH IT. Esc within
        # `_SEARCH_DEBOUNCE_SECONDS` of the last keystroke is an ordinary thing
        # to do, and a window left open across this point would fire and paint
        # matches for a search the reviewer has just abandoned. Its callback
        # guards on `_search` as well, so this is the second of two locks — it
        # is here because "stop the timer when the thing it serves ends" is the
        # honest statement of the lifetime, not "rely on the callback noticing".
        self._cancel_pending_repaint()
        # THE HIGHLIGHTING GOES WITH THE SEARCH — functional-spec §2.9 AC1, "the
        # prompt closes, the highlighting is gone". An empty match list is
        # exactly the unhighlighted reading view: `TranscriptBody.highlight`
        # copies its base and appends nothing, so spec 038's kind colours come
        # back at their exact offsets and no amber survives a search that has
        # ended. (That is also what keeps the two strict-equality span tests
        # honest, since a leaked span is the thing they fail on.)
        #
        # Unconditional rather than conditional on anything having been lit:
        # the repaint costs one keypress's worth of work, where tracking
        # whether the body is currently clean would cost a second piece of state
        # to keep true.
        #
        # SLICE 5 (Tasks 5.1/5.2) FOUND THIS STATE MACHINE ALREADY COMPLETE AND
        # MEASURED IT RATHER THAN ADDING TO IT. This paragraph used to end with a
        # promise that Slice 5 would finish "the bar-hidden-search-live state
        # Enter leaves behind", and there was nothing left to finish: Slice 1
        # branched `action_close` on `_search` for exactly that reason and Task
        # 4.5 added the `SUB_TITLE` restore. Walked on the 88 KB committed game
        # at 100x24 through real keystrokes, all three of `SearchState`'s states:
        # state (i) — Esc from an open bar dropped 18 match spans back to the
        # base 998 and HELD `scroll_offset.y` at 45, where the reviewer had
        # scrolled to UNDER the open bar rather than the 22 they opened it at
        # (§2.4 invited that scroll, so §2.9 AC1 owes them the place it ended,
        # not the place it started); state (ii) — Esc after Enter and an `n`
        # dropped the same 18 spans, held y at 204 and STAYED on the screen, with
        # the next Esc leaving; state (iii) — Esc with no search left in one
        # press. `backspace` and `q` answered identically wherever they reach
        # here, and the header went back to `SUB_TITLE` on every one of those
        # clears.
        #
        # WHAT MAKES THE CLEAR WHOLE-STATE RATHER THAN QUERY-ONLY, which is the
        # half a reader is most likely to doubt: `self._search = None` above
        # drops the `SearchState` OBJECT, so `current`, `scroll_at_landing` and
        # the width-keyed `row_map` go with the query instead of being reset
        # field by field. Measured across one clear: `query='vote' current=1
        # scroll_at_landing=204 row_map=(96, 1182 rows)` → `None`, and the next
        # `/` seeded a fresh `SearchState` with `row_map=None`. So a map keyed on
        # a stale width, or an index into a match list that no longer exists, has
        # no route into a later search — and that is a property of dropping the
        # object, which is why a future "just reset the query" would be a
        # regression rather than a tidy-up.
        #
        # AND THE COUNT AND THE HEADER GO WITH IT, without a line here to say so:
        # `_repaint_matches` writes `#search-count` AND `Screen.sub_title` on
        # this same path, and both read the search off `_search` — which the line
        # above has already set to `None`. So the strip blanks and the header
        # goes back to `SUB_TITLE` (spec 044, Task 4.5: "restore `SUB_TITLE` when
        # the search clears") instead of being left claiming "18 matches" and
        # "vote · 3/18" over a game with no amber on it. The restore is a
        # consequence of the state-first ordering above rather than a fourth
        # thing to remember here — which is the point of that ordering.
        self._repaint_matches(())
        self.query_one("#search-row", Horizontal).remove_class("-open")
        self.query_one(TranscriptSearchInput).value = ""
        self.query_one("#transcript-scroll", VerticalScroll).focus()


class LedgerTableScreen(Screen):
    """The table screen: a scrollable :class:`DataTable` of the flattened ledger.

    Composes the ``#ledger-table`` :class:`DataTable` at ``height: 1fr`` plus a
    hidden ``#empty-state`` :class:`Static` shown in its place when there is
    nothing to render (empty/missing ledger, or a parse error). The numeric
    metric columns are wrapped in a right-justified Rich :class:`Text` at
    ``add_row`` time; the fixed identity columns stay plain strings.

    A docked search ``Input`` + match-count (Slice 2) filters the rows, and
    selecting the highlighted cell opens that row's :class:`DetailScreen`
    drill-down (Slice 3). The cursor is a single **cell** the ``DataTable``
    auto-scrolls fully into view as it moves, so a wide table pans by moving the
    highlight rather than nudging the viewport character by character.

    **Focus belongs to the table by default** so the arrow keys / Enter drive
    row navigation and drill-down the moment the viewer opens — the search box
    is opt-in (``/`` or Tab to reach it). While the search box has focus,
    ``escape`` returns focus to the table (rather than quitting), and ``Enter``
    commits the filter and jumps to the results; a printable ``q``/``/`` typed
    there is captured by the Input, not the app/screen bindings.
    """

    DEFAULT_CSS = """
    LedgerTableScreen {
        layout: vertical;
    }

    /* Top dock region for the search Input + match-count (Slice 2). Docked top
       at auto height so it pins above the table without disturbing the
       table's 1fr fill. */
    #search-region {
        dock: top;
        height: auto;
    }

    /* The selector + value Input sit side by side on one row; #match-count
       sits below them (still inside #search-region). */
    #search-row {
        height: auto;
        width: 1fr;
    }

    #field-select {
        width: 22;
    }

    #search {
        width: 1fr;
    }

    #match-count {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #ledger-table {
        height: 1fr;
        width: 1fr;
    }

    #empty-state {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    # ``/`` jumps focus to the search box (the table holds focus by default, so
    # this is how you start filtering without reaching for the mouse). Shown in
    # the Footer; while the search Input itself is focused the keystroke is
    # captured as text, so it only fires from the table.
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
    ]

    def __init__(self, model: TableModel | None, error: str | None = None) -> None:
        """Build the screen from a flattened model (or an error / empty state).

        ``model`` is the index-parallel :class:`TableModel` from the data layer;
        ``None`` together with a non-``None`` ``error`` represents the parse-error
        state (the ledger existed but was malformed). An empty model (no rows)
        and a ``None`` model both resolve to the ``#empty-state`` path — the
        difference is only the message shown.
        """
        super().__init__()
        self._model = model
        self._error = error
        # Maps each currently-displayed table row → its index in the model's
        # index-parallel lists (rows/search_blobs/records). Rebuilt on every
        # filter so a later row-select (Slice 3) resolves to the right raw
        # record even when the visible set is a filtered subset.
        self._visible_indices: list[int] = []
        # The cursor cell stashed when a cell is selected into the DetailScreen, so
        # it can be restored when that screen pops and this one resumes — the
        # maintainer returns to exactly the cell they drilled into. ``None`` until
        # the first drill-down.
        self._stashed_cursor: Coordinate | None = None

    def compose(self) -> ComposeResult:
        # The top dock region carrying the field selector + value Input on one
        # row, with the match-count below. The selector (defaulting to "All" /
        # SEARCH_SCOPE_ALL) picks the field to scope on, so the maintainer never
        # types a field name into the value box.
        with Vertical(id="search-region"):
            with Horizontal(id="search-row"):
                yield Select(
                    [("All", SEARCH_SCOPE_ALL)] + [(f, f) for f in SEARCH_FIELDS],
                    value=SEARCH_SCOPE_ALL,
                    allow_blank=False,
                    id="field-select",
                )
                yield SearchInput(id="search", placeholder="Filter runs…")
            yield Static(id="match-count")
        # cursor_type="cell": a single highlighted cell that the DataTable
        # auto-scrolls **fully into view** on every move (both axes), so panning a
        # wide table is "move the highlight, the cell comes into view" rather than
        # nudging the viewport a character at a time. fixed_columns=0 is
        # deliberate (everything scrolls together); fixed_columns=1 would pin the
        # date column.
        yield DataTable(id="ledger-table", cursor_type="cell")
        # Hidden by default; shown (and the table hidden) for the empty/error
        # states in on_mount.
        yield Static(self._empty_message(), id="empty-state")
        # Key hints (Search / Quit) so the table-first focus model is discoverable.
        yield Footer()

    def on_mount(self) -> None:
        """Populate the table, or switch to the #empty-state for no-data states."""
        table = self.query_one("#ledger-table", DataTable)
        empty = self.query_one("#empty-state", Static)

        if self._model is None or not self._model.rows:
            # Empty/missing ledger or a parse error: hide the grid, show the
            # message. (The Static already carries the right copy from compose.)
            table.display = False
            empty.display = True
            # Nothing to search; leave the visible map empty (filter is a no-op).
            return

        empty.display = False
        # Initial render shows every row, so the visible map is the full range.
        table.add_columns(*self._model.columns)
        self._set_visible(table, list(range(len(self._model.rows))))
        # Focus the table (not the search Input) so the arrow keys / Enter drive
        # navigation immediately — otherwise the docked Input grabs initial focus
        # and swallows every navigation keystroke as text.
        table.focus()

    def action_focus_search(self) -> None:
        """Move focus to the search box (the ``/`` binding). No-op without rows."""
        if self._model is None or not self._model.rows:
            return
        self.query_one("#search", Input).focus()

    def on_key(self, event: events.Key) -> None:
        """Boundary-jump nav + ``escape`` back-out for the search controls.

        Three cases handled here (all from the search region; the table's own
        keys are untouched):

        - **``right`` on the collapsed field selector** → focus the value
          ``Input`` (the right half of the boundary-jump; the Input's left edge
          jumps back, handled in :class:`SearchInput`). Guarded by
          ``not expanded`` so arrows are NOT stolen while the dropdown overlay is
          open.
        - **``escape`` inside the value ``Input``** → back out to the table
          rather than letting the app's ``escape``→quit fire.
        - **``escape`` on the collapsed field selector** → also back out to the
          table; while the selector is *expanded* we let its own ``escape`` close
          the dropdown instead.
        """
        select = self.query_one("#field-select", Select)

        # right on the collapsed selector → jump into the value Input.
        if (
            event.key == "right"
            and select.has_focus
            and not select.expanded
        ):
            self.query_one("#search", Input).focus()
            event.stop()
            event.prevent_default()
            return

        if event.key != "escape":
            return

        search = self.query_one("#search", Input)
        # escape inside the value box, or on the collapsed selector, returns to
        # the table; an expanded selector keeps escape for closing its overlay.
        if search.has_focus or (select.has_focus and not select.expanded):
            self.query_one("#ledger-table", DataTable).focus()
            event.stop()
            event.prevent_default()

    def on_search_input_focus_field_select(
        self, event: SearchInput.FocusFieldSelect
    ) -> None:
        """``left`` at the value Input's start edge → focus the field selector.

        The left half of the boundary-jump: :class:`SearchInput` posts this when
        ``left`` is pressed with the caret at position 0, and we hand focus to
        the ``#field-select`` selector on its left.
        """
        self.query_one("#field-select", Select).focus()
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the search box commits the filter and jumps to the results."""
        if event.input.id != "search":
            return
        table = self.query_one("#ledger-table", DataTable)
        if table.display:
            table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-run the filter whenever the value box changes (live filtering)."""
        if event.input.id != "search":
            return
        self._run_filter()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Re-run the filter whenever the scope field selector changes."""
        if event.select.id != "field-select":
            return
        self._run_filter()

    def _run_filter(self) -> None:
        """Live-filter the table to rows matching the selector field + value.

        The single filter path both widgets drive: it reads the scope ``field``
        from the ``#field-select`` :class:`~textual.widgets.Select` and the typed
        ``value`` from the ``#search`` ``Input``, then keeps each model index
        ``i`` where the pure matcher
        :func:`~graphia.eval_ledger.row_matches_field` returns True for that row's
        ``search_blobs[i]`` / per-field ``search_fields[i]``. With the selector on
        :data:`~graphia.eval_ledger.SEARCH_SCOPE_ALL` (the default) the value hits
        the whole free-text blob; on a named field it scopes to that field only.
        The matcher lowercases/splits/ANDs the value, so an empty/whitespace value
        matches every row (restoring all rows). A non-empty value that survives
        zero rows hides the table and shows the distinct "No runs match" copy
        echoing the original-case **value**; clearing it (or a value/field that
        matches again) restores the grid. The empty/missing-ledger case has no
        rows, so this is a no-op there.
        """
        if self._model is None or not self._model.rows:
            return

        table = self.query_one("#ledger-table", DataTable)
        empty = self.query_one("#empty-state", Static)
        field = self.query_one("#field-select", Select).value
        value = self.query_one("#search", Input).value

        indices = [
            i
            for i in range(len(self._model.rows))
            if row_matches_field(
                field,
                value,
                self._model.search_blobs[i],
                self._model.search_fields[i],
            )
        ]

        if not indices:
            # Non-empty value that matched nothing: hide the grid, show the
            # distinct no-match copy echoing the (original-case) value.
            self._set_visible(table, indices)
            table.display = False
            empty.update(_no_match_message(value))
            empty.display = True
            return

        empty.display = False
        table.display = True
        self._set_visible(table, indices)

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Drill into the highlighted cell's row → full record via the DetailScreen.

        With the cell cursor, selecting *any* cell opens that row's record.
        Stashes the selected cell coordinate (restored on resume so the maintainer
        returns to the exact cell), resolves the raw record through the
        ``_visible_indices`` map — so a filtered subset still points at the right
        record — and pushes the :class:`DetailScreen`. A no-op when there is no
        model or the row is out of range (the empty/error states have no rows).

        **This is where the run's transcripts are resolved** (spec 037): the pure
        :func:`~graphia.eval_ledger.list_transcripts` needs the ledger ``Path``,
        and doing the lookup on *this* side of the push — where ``self.app`` is
        already in hand — lets :class:`DetailScreen` take its entries
        pre-resolved and keep its "no path arithmetic of its own" invariant. An
        empty list is normal (older record, missing/un-pulled dir) and drives the
        panel's "no transcripts" state.
        """
        if self._model is None or not self._model.records:
            return
        row = event.coordinate.row
        if not 0 <= row < len(self._visible_indices):
            return
        self._stashed_cursor = event.coordinate
        record = self._model.records[self._visible_indices[row]]
        entries = list_transcripts(record, self.app._path)
        self.app.push_screen(DetailScreen(record, entries))

    def on_screen_resume(self) -> None:
        """Restore the cursor when the DetailScreen pops back to this screen.

        Fires (via Textual's ``ScreenResume`` event) when this screen becomes
        active again after the pushed :class:`DetailScreen` is popped. Moves the
        cursor back to the stashed cell — scrolling it into view — so the
        drill-down round-trip lands the maintainer exactly where they were. Only
        acts when a cell was actually stashed and a populated table exists.
        """
        if self._stashed_cursor is None:
            return
        if self._model is None or not self._model.rows:
            return
        table = self.query_one("#ledger-table", DataTable)
        row, column = self._stashed_cursor.row, self._stashed_cursor.column
        if 0 <= row < table.row_count:
            table.move_cursor(row=row, column=column, scroll=True)

    def _set_visible(self, table: DataTable, indices: list[int]) -> None:
        """Rebuild the table body to show exactly ``indices`` and record the map.

        Clears the rows (keeping the already-added columns) and re-adds one row
        per model index in ``indices``, then stores ``indices`` as
        ``_visible_indices`` so a displayed row → model index lookup stays
        correct. Reuses :meth:`_render_row` for the metric right-justification.
        """
        assert self._model is not None  # callers guard the no-model state
        table.clear(columns=False)
        for i in indices:
            table.add_row(*self._render_row(self._model.rows[i]))
        self._visible_indices = list(indices)
        self._update_match_count(len(indices))

    def _render_row(self, cells: list[str]) -> list[str | Text]:
        """Render one model row's cells, right-justifying the metric columns.

        The fixed leading columns (``⚠``, Date, Provider, models, Games, Notes)
        stay plain strings; the trailing metric columns are wrapped in a
        right-justified Rich :class:`Text` so the numeric rates line up under
        their headers. The split point is the fixed-column count derived from the
        model shape, so it tracks an added/removed fixed column upstream. Shared
        by the initial populate and every filter rebuild.
        """
        assert self._model is not None
        fixed = len(self._model.columns) - len(METRIC_ORDER)
        return [
            cell if i < fixed else Text(cell, justify="right")
            for i, cell in enumerate(cells)
        ]

    def _update_match_count(self, visible: int) -> None:
        """Update #match-count to ``Showing X of N`` (N = total model rows)."""
        total = len(self._model.rows) if self._model is not None else 0
        self.query_one("#match-count", Static).update(f"Showing {visible} of {total}")

    def _empty_message(self) -> str:
        """The copy for #empty-state — a friendly parse error, or the empty hint."""
        if self._error is not None:
            return f"{_PARSE_ERROR_PREFIX} {self._error}"
        return _EMPTY_LEDGER_MESSAGE


class LedgerViewerApp(App[None]):
    """The standalone ledger-viewer app — reads a ledger file, shows the table.

    Constructed with the ledger :class:`~pathlib.Path` (default
    :data:`~graphia.tools.blunder_eval.LEDGER_PATH`); the path is the only input
    it needs, so it **never calls** :func:`graphia.config.load_config`. On mount
    it loads the ledger via :func:`~graphia.eval_ledger.load_ledger` (catching
    :class:`~graphia.eval_ledger.LedgerParseError` to show a friendly error
    state, not a traceback), flattens it with
    :func:`~graphia.eval_ledger.build_table_model`, and pushes the
    :class:`LedgerTableScreen`.
    """

    TITLE = "Graphia eval ledger"

    # Esc (and q) quit the viewer — it's a read-only browser with nothing to
    # save, so a single keystroke exit is the expected affordance.
    BINDINGS = [
        Binding("escape", "quit", "Quit", show=True),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        super().__init__()
        self._path = Path(path)

    def on_mount(self) -> None:
        """Load + flatten the ledger and push the table screen.

        A missing or empty ledger yields ``[]`` (a normal empty state, not an
        error); malformed YAML raises :class:`LedgerParseError`, which we catch
        and turn into a readable error screen rather than letting the traceback
        escape.
        """
        try:
            records = load_ledger(self._path)
        except LedgerParseError as exc:
            self.push_screen(LedgerTableScreen(model=None, error=str(exc)))
            return
        model = build_table_model(records)
        self.push_screen(LedgerTableScreen(model=model))


def main(argv: list[str] | None = None) -> None:
    """CLI entry: ``python -m graphia.ui.ledger_viewer [--path FILE]``.

    ``--path`` points the viewer at an alternate ledger (default
    :data:`~graphia.tools.blunder_eval.LEDGER_PATH`), so a maintainer can inspect
    a ledger somewhere other than the repo-committed one. Mirrors the ``tools/``
    argparse idiom.
    """
    parser = argparse.ArgumentParser(
        prog="graphia.ui.ledger_viewer",
        description="Browse the eval quality ledger (evals/blunder-ledger.yaml) "
        "as a scrollable table.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=LEDGER_PATH,
        help="Path to the ledger YAML to view "
        "(default: the repo-committed evals/blunder-ledger.yaml).",
    )
    args = parser.parse_args(argv)
    LedgerViewerApp(path=args.path).run()


if __name__ == "__main__":
    main()
