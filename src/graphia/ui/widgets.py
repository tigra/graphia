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
from textual.widgets import Button, Label, OptionList
from textual.widgets.option_list import Option


class PointingModal(ModalScreen[str]):
    """Modal prompting the human Mafia to point at a target.

    Parameters:
        options: List of mappings, each with ``"id"`` (string — used as the
            dismissal value) and ``"name"`` (string — the user-visible label).
            Typically sourced from the ``kind="point"`` interrupt payload,
            restricted to alive law-abiding players.

    The modal dismisses with the selected option's ``id``. The caller is
    expected to ``await app.push_screen_wait(PointingModal(...))`` from a
    worker and forward that string back into ``Command(resume=...)``.
    """

    DEFAULT_CSS = """
    PointingModal {
        align: center middle;
        background: $background 60%;
    }

    PointingModal > Vertical {
        width: 40%;
        height: 40%;
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

    PointingModal OptionList {
        height: 1fr;
        border: none;
    }
    """

    def __init__(self, options: list[dict[str, Any]]) -> None:
        super().__init__()
        # Defensive copy + normalization so we don't carry arbitrary payload
        # fields into the widget tree.
        self._options: list[dict[str, str]] = [
            {"id": str(o["id"]), "name": str(o["name"])} for o in options
        ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                "Choose your target (Night Mafia vote)",
                id="pointing-title",
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
