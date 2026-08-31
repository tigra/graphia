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
from pathlib import Path
from typing import ClassVar

from rich.style import Style as RichStyle
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.message import Message
from textual.screen import Screen
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
    KIND_MARKER,
    LedgerParseError,
    METRIC_ORDER,
    RawRecord,
    SEARCH_FIELDS,
    SEARCH_SCOPE_ALL,
    TableModel,
    TranscriptEntry,
    build_table_model,
    list_transcripts,
    load_ledger,
    read_transcript,
    render_detail,
    row_matches_field,
    tokenize_transcript,
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
# colour (``marker`` now; ``attr`` / ``field-label`` / ``speaker`` / ``speech`` /
# ``thought`` / ``recap`` and the side-bearing kinds in the later slices — see
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
_TRANSCRIPT_KIND_COMPONENTS: dict[str, str] = {
    KIND_MARKER: "transcript--marker",
}


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

    # Derived from THE map, so adding a highlighted kind stays a one-line change
    # there: a new entry brings its component class along, and only the matching
    # CSS rule below has to be written.
    COMPONENT_CLASSES: ClassVar[frozenset[str]] = frozenset(
        _TRANSCRIPT_KIND_COMPONENTS.values()
    )

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
    """

    # Identical back-out idiom as DetailScreen: pop back to the transcript list.
    BINDINGS = [
        Binding("escape", "close", "Back", show=True),
        Binding("backspace", "close", "Back", show=True),
        Binding("q", "close", "Back", show=False),
    ]

    def __init__(self, entry: TranscriptEntry) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        yield Header()
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
            # refactor might take.
            yield Static(
                self._styled_body(),
                id="transcript-body",
                markup=False,
            )
        yield Footer()

    def _styled_body(self) -> Text:
        """The transcript as a styled renderable whose text is the file's text.

        Tokenizes with the pure :func:`~graphia.eval_ledger.tokenize_transcript`
        and paints each span with its kind's style, appending **every** span —
        styled or not — in order. So the renderable's plain text is
        ``"".join(text for text, _ in spans)``, which the tokenizer guarantees is
        the file byte for byte: the highlighting can change how the game looks
        but not what it says.

        A Rich :class:`~rich.text.Text` rather than Textual's native
        :class:`~textual.content.Content`: ``Static`` converts it through
        ``Content.from_rich_text``, preserving the spans, and it is the shape
        verified working on the installed Textual (8.2).

        **The one reason a later refactor might prefer ``Content`` directly:**
        ``Text.append`` runs Rich's ``strip_control_codes``, which silently drops
        BEL, BS, VT, FF and CR (7, 8, 11, 12, 13) — so a transcript containing
        one of those five would render one character short of its file, which is
        precisely the guarantee this screen exists to keep. All 298 committed
        transcripts are clean of them (checked), and ``\n`` and ``\t`` are not
        stripped, so the round trip holds today; ``Content`` does no such
        rewriting and would close the hole for good.
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

    def action_close(self) -> None:
        """Pop back to the :class:`DetailScreen` that pushed this screen."""
        self.app.pop_screen()


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
