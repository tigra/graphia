"""Textual widgets for Graphia.

This module hosts reusable widgets and modal screens pulled out of
``graphia.ui.app`` to keep the app module focused on orchestration.

Currently provides:

- :class:`PointingModal` — a blocking modal that asks the human Mafia to pick
  a target during the Night pointing phase. Renders as a centered dialog with
  an :class:`~textual.widgets.OptionList`; keyboard-first (arrow keys to
  navigate, Enter to select). The modal resolves with the chosen option's
  string ``id`` via ``Screen.dismiss``; Escape is intentionally not bound —
  the Mafia MUST point at someone.
- :class:`VoteModal` — a blocking yes/no modal used during the Day phase
  ``collect_votes`` step. Renders the target name, two buttons (Yes/No), and
  accepts ``y``/``n`` keybindings. Resolves with the literal string
  ``"yes"`` or ``"no"`` — exactly what the ``kind="vote"`` interrupt expects
  as its ``Command(resume=...)`` value. Escape is intentionally not bound —
  the voter MUST pick a side.

MARKUP: THIS MODULE HANDS THE PARSER NOTHING IT DID NOT WRITE ITSELF
--------------------------------------------------------------------

Spec 040 (The Moderator's Closing Story), Slice 2, Task 2.2a — a live sibling
of the fault Task 2.1 fixed in ``ui/app.py``, in a different parser. §2.11
records that there is *"no ``rich.markup.escape`` anywhere in ``app.py``"*,
which is true and invites the conclusion that ``app.py`` **is** the fault. It
was not the whole of it: ``Static``, ``Label`` and ``OptionList``'s ``Option``
all take ``markup=True`` by default, so **every bare ``str`` composed here was
parsed for tags too** — and four of them interpolate a value this module did
not author.

The rule is Task 2.1's, applied rather than re-derived: **does this call site
author the string it hands the parser?** A literal written here keeps markup;
anything interpolated from state, a model, the person or config does not. So
the four interpolating sites below are composed as :class:`textual.content.Content`,
whose constructor takes text as *text* — while the module's own literals (the
round header, the two dialog titles, the Yes/No button faces) stay plain
``str`` and keep their markup, exactly as in Task 2.1.

**Nothing is lost by the switch, and less than in ``app.py``:** every style on
these widgets comes from ``DEFAULT_CSS`` (``text-style: bold``, ``color:
$error``, ``color: $text-muted``), never from tags in the string, so there is
no label to re-assemble as spans — the plain ``Content`` renders identically.

**The parser here is NOT Rich's, and one measured difference matters for
anyone writing a probe.** Textual's ``Content.from_markup`` has its own
tokenizer, and unlike Rich's ``[a-z#/@]`` tag pattern it is
**case-INSENSITIVE**. Measured on the installed Textual 8.2.4:

- ``Content.from_markup("Execute [sic]?")`` -> ``"Execute ?"`` — silently
  deleted, and so are ``[Sic]`` and ``[SIC]``. Rich leaves those last two
  alone; Textual does not. A capitalised probe is *not* the free pass here
  that it is against ``app.py``, but every probe in the regression file is
  lowercase anyway, because lowercase is the shape both parsers eat.
- ``Content.from_markup("Execute [/]?")`` **raises** ``MarkupError``.

**Where that raise lands, measured rather than inferred, because it is not
where Task 2.2a's own task line predicts.** That line says a raise here
*"lands inside ``push_screen_wait`` on the driver worker, i.e. the same
``_drive`` ``except`` path"*. It does not. The parse is lazy — it happens in
``Static.visual`` during the app's own render cycle, not in ``compose`` and
not on the worker — so Textual treats it as a fatal app error, tears the app
down, and the worker parked in ``push_screen_wait`` is handed a bare
``CancelledError``. ``_drive`` catches ``Exception``, which
``CancelledError`` is not. **So the outcome is worse than the task line
describes, not equivalent:** no error banner, ``_record_career`` never
reached, and a raw Textual crash dump instead of an ending. The conclusion
the line drew from it — that this breaks the same acceptance criteria as the
``app.py`` fault — is right; only the mechanism was wrong.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option


class PointingModal(ModalScreen[str]):
    """Modal prompting the human Mafia to point at a target.

    Parameters:
        options: List of mappings, each with ``"id"`` (string — used as the
            dismissal value) and ``"name"`` (string — the user-visible label).
            Typically sourced from the ``kind="point"`` interrupt payload,
            restricted to alive law-abiding players.
        round_number: Optional current pointing round (1-based). When set
            alongside ``round_cap`` the modal shows a "Night kill — round X of
            N" header (Spec 015 — Multi-Round Mafia Consensus by Pointing).
        round_cap: Optional cap on pointing rounds, paired with
            ``round_number`` for the header.
        prior_picks: Optional by-name summary of the teammates' picks so far
            this Night. When non-empty (and not the neutral "no picks yet"
            line) it is rendered read-only above the target list so the human
            Mafioso sees exactly what the AI Mafiosos see.

    All three context params default to ``None`` so a round-agnostic call
    (e.g. tests, or any non-multi-round path) renders just the option list as
    before.

    The modal dismisses with the selected option's ``id``. The caller is
    expected to ``await app.push_screen_wait(PointingModal(...))`` from a
    worker and forward that string back into ``Command(resume=...)``.
    """

    # The neutral sentinel the graph's _render_prior_picks returns when no
    # teammate has pointed yet; treated as "nothing to show" so the very first
    # pointer of round 1 sees no picks block.
    _NEUTRAL_PRIOR_PICKS = "No teammate has pointed yet this Night."

    DEFAULT_CSS = """
    PointingModal {
        align: center middle;
        background: $background 60%;
    }

    PointingModal > Vertical {
        width: 40%;
        /* Size to content (title + optional round/prior-picks lines + every
           option) so the whole target list is visible without scrolling. The
           roster is at most ~11 targets, so on any normal terminal the dialog
           grows just tall enough to show them all. ``max-height`` caps it at
           90% of the screen so it can never overflow; when the content is
           genuinely taller than that, this Vertical's default ``overflow: auto``
           gives a single scrollbar (scroll, never clip-and-hide). ``min-height``
           keeps a 1-2 target list looking like a dialog rather than a sliver. */
        height: auto;
        max-height: 90%;
        min-width: 40;
        min-height: 10;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    PointingModal #pointing-title {
        height: auto;
        padding: 0 0 1 0;
        text-style: bold;
        color: $text;
    }

    PointingModal #pointing-round {
        height: auto;
        padding: 0 0 1 0;
        text-style: bold;
        color: $accent;
    }

    PointingModal #pointing-prior-picks {
        height: auto;
        padding: 0 0 1 0;
        color: $text-muted;
    }

    PointingModal OptionList {
        /* ``auto`` grows the list to one row per option (its optimal size with
           no scrolling) instead of ``1fr`` filling a fixed remainder — under a
           short dialog with the Spec-015 round + prior-picks chrome, ``1fr``
           collapsed the list to ~1 visible row. ``overflow-y: hidden`` stops the
           OptionList from drawing its OWN inner scrollbar; the parent Vertical's
           ``max-height`` + ``overflow: auto`` owns the single scroll when (and
           only when) the content can't fit the screen. */
        height: auto;
        overflow-y: hidden;
        border: none;
    }
    """

    def __init__(
        self,
        options: list[dict[str, Any]],
        round_number: int | None = None,
        round_cap: int | None = None,
        prior_picks: str | None = None,
    ) -> None:
        super().__init__()
        # Defensive copy + normalization so we don't carry arbitrary payload
        # fields into the widget tree.
        self._options: list[dict[str, str]] = [
            {"id": str(o["id"]), "name": str(o["name"])} for o in options
        ]
        self._round_number: int | None = (
            round_number if isinstance(round_number, int) else None
        )
        self._round_cap: int | None = (
            round_cap if isinstance(round_cap, int) else None
        )
        # Drop empty / neutral "no picks yet" so the first pointer sees nothing.
        prior = prior_picks if isinstance(prior_picks, str) else None
        if prior is not None:
            prior = prior.strip()
        self._prior_picks: str | None = (
            prior if prior and prior != self._NEUTRAL_PRIOR_PICKS else None
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            # Spec 015 §2.5 header: only shown when both round + cap are known,
            # so a round-agnostic call renders just the title + list as before.
            if self._round_number is not None and self._round_cap is not None:
                yield Static(
                    f"Night kill — round {self._round_number} "
                    f"of {self._round_cap}",
                    id="pointing-round",
                )
            yield Label(
                "Choose your target (Night Mafia vote)",
                id="pointing-title",
            )
            # Read-only teammates' picks-so-far (by name). Omitted entirely when
            # there is nothing to show (first pointer of round 1, or no context).
            #
            # ``Content``, not a bare f-string: ``night._render_prior_picks``
            # builds this out of ``player.name`` values, and one of those names
            # is the one the person typed at the name prompt (a human Mafioso
            # who pointed in a completed round appears in their own picks
            # summary the next round). See the module docstring — a player
            # called ``[sic]`` had their pick deleted from this line, and one
            # called ``[/]`` killed the app on the render.
            if self._prior_picks is not None:
                yield Static(
                    Content(f"Teammates so far: {self._prior_picks}"),
                    id="pointing-prior-picks",
                )
            # ``Content`` per option for the same reason, and this is the site
            # no plan document named: every target's name comes from the small
            # model's roster generation, so the option faces are model output.
            # ``Option`` has no ``markup=`` switch — a ``VisualType`` prompt is
            # the only way to keep a name out of the tokenizer.
            yield OptionList(
                *[
                    Option(Content(opt["name"]), id=opt["id"])
                    for opt in self._options
                ],
                id="pointing-options",
            )

    def on_mount(self) -> None:
        # Focus the list so arrow keys + Enter work immediately; also select
        # the first option as a convenience so a bare Enter resolves.
        option_list = self.query_one("#pointing-options", OptionList)
        option_list.focus()
        if self._options:
            option_list.highlighted = 0

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        option_id = event.option.id
        if isinstance(option_id, str) and option_id:
            self.dismiss(option_id)


class VoteModal(ModalScreen[str]):
    """Modal prompting a voter to approve or reject executing ``target_name``.

    Parameters:
        target_name: The user-visible name of the player on the chopping
            block. Appears in the modal body.
        error: Optional error string to render (red) above the prompt. The
            ``collect_votes`` node re-interrupts with this key when a prior
            resume value wasn't ``"yes"`` / ``"no"``; surfacing it here gives
            the voter an immediate cue without touching the public chat log.

    The modal dismisses with the exact string ``"yes"`` or ``"no"``, matching
    the values ``collect_votes`` accepts as a ``Command(resume=...)``. Escape
    is NOT bound — the voter must cast a ballot.
    """

    DEFAULT_CSS = """
    VoteModal {
        align: center middle;
        background: $background 60%;
    }

    VoteModal > Vertical {
        width: 50%;
        height: auto;
        min-width: 40;
        min-height: 9;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    VoteModal #vote-error {
        height: auto;
        padding: 0 0 1 0;
        color: $error;
        text-style: bold;
    }

    VoteModal #vote-title {
        height: auto;
        padding: 0 0 1 0;
        text-style: bold;
        color: $text;
    }

    VoteModal #vote-buttons {
        height: auto;
        align-horizontal: center;
    }

    VoteModal #vote-buttons Button {
        margin: 0 1;
        min-width: 10;
    }
    """

    BINDINGS = [
        Binding("y", "cast('yes')", "Yes", show=True),
        Binding("n", "cast('no')", "No", show=True),
    ]

    def __init__(
        self,
        target_name: str,
        error: str | None = None,
    ) -> None:
        super().__init__()
        self._target_name: str = str(target_name)
        self._error: str | None = (
            str(error) if isinstance(error, str) and error else None
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            # ``collect_votes``' re-interrupt error. Its strings are the
            # graph's own literals today, so this site is converted on the
            # same grounds Task 2.1 converted ``day_turn_error``: the call
            # site cannot see where the string came from, and the day the
            # message quotes back what the person typed there would be nothing
            # here to notice.
            if self._error is not None:
                yield Label(Content(self._error), id="vote-error")
            # The most reachable of the four: ``target_name`` is whoever is on
            # the block, and that includes the human, whose name is taken as
            # ``value.strip()`` at the name prompt with no bracket sanitising.
            # A player called ``[/]`` who gets nominated used to crash the vote
            # modal — for every voter, on the turn their own life was at stake.
            yield Label(
                Content(f"Execute {self._target_name}?"),
                id="vote-title",
            )
            with Horizontal(id="vote-buttons"):
                yield Button("Yes", id="yes", variant="success")
                yield Button("No", id="no", variant="error")

    def on_mount(self) -> None:
        # Focus "No" by default so an accidental Enter is the non-destructive
        # answer. The voter can Tab/click/press `y` to pick Yes.
        try:
            self.query_one("#no", Button).focus()
        except Exception:  # noqa: BLE001
            # Defensive: if focus fails for any reason, modal still works via
            # keybindings and mouse clicks.
            pass

    def action_cast(self, answer: Any) -> None:
        """Keybinding handler: dismiss with the bound literal answer."""
        if isinstance(answer, str) and answer in ("yes", "no"):
            self.dismiss(answer)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id in ("yes", "no"):
            self.dismiss(button_id)
