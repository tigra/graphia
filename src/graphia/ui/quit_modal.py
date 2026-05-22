"""Quit-confirmation modal: guards Esc/q against accidental exits.

Graphia is a single-player console game with no save/resume — once the
process dies, the current game is gone. To keep an accidental Esc or q
from throwing the session away, the App pushes :class:`QuitModal` on
those keys and only honours the exit when the player explicitly answers
``y`` (or clicks **Yes**). Answering ``n``, hitting Esc, or clicking
**No** dismisses the modal and drops the player back where they were.

The modal returns a ``bool`` via :py:meth:`textual.screen.Screen.dismiss`
— ``True`` means "quit", ``False`` means "stay". The caller (wired in
the next sub-task) decides what to do with that value.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class QuitModal(ModalScreen[bool]):
    """Modal that asks the player to confirm quitting the game.

    Dismisses with ``True`` on confirm (``y`` / Enter / **Yes** button)
    and ``False`` on cancel (``n`` / Esc / **No** button). The **No**
    button is focused on mount so a stray Enter cancels rather than
    quits.
    """

    DEFAULT_CSS = """
    QuitModal {
        align: center middle;
        background: $background 70%;
    }

    QuitModal > Vertical {
        width: 50%;
        height: auto;
        min-width: 36;
        max-width: 70;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }

    QuitModal #quit-title {
        height: auto;
        padding: 0 0 1 0;
        text-style: bold;
        color: $warning;
    }

    QuitModal #quit-help {
        height: auto;
        padding: 0 0 1 0;
        color: $text-muted;
    }

    QuitModal #quit-buttons {
        height: auto;
        align-horizontal: center;
        padding: 1 0 0 0;
    }

    QuitModal #quit-buttons Button {
        min-width: 10;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=True),
        Binding("enter", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Quit?", id="quit-title")
            yield Static(
                "Press y to quit, n to cancel.",
                id="quit-help",
            )
            with Horizontal(id="quit-buttons"):
                yield Button("Yes", id="quit-yes", variant="error")
                yield Button("No", id="quit-no", variant="primary")

    def on_mount(self) -> None:
        try:
            self.query_one("#quit-no", Button).focus()
        except Exception:  # noqa: BLE001
            # Defensive: keybindings still resolve the modal if focus fails.
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit-yes":
            self.dismiss(True)
        elif event.button.id == "quit-no":
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)