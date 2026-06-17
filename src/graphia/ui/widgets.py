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
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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
            if self._prior_picks is not None:
                yield Static(
                    f"Teammates so far: {self._prior_picks}",
                    id="pointing-prior-picks",
                )
            yield OptionList(
                *[Option(opt["name"], id=opt["id"]) for opt in self._options],
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
            if self._error is not None:
                yield Label(self._error, id="vote-error")
            yield Label(
                f"Execute {self._target_name}?",
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
