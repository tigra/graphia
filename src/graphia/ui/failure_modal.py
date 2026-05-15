"""Remote-mode crash modal: hands the player CloudWatch coordinates.

When a ``--remote`` game hits an unhandled exception, the LangGraph work
ran inside the deployed AgentCore Runtime — the local JSONL log only
captures the client-side stack, not the server-side cause. The Runtime
stamps every log/trace record with the game's ``thread_id`` (see
``graphia.runtime.observability``), so a CloudWatch Logs filter
``{ $.thread_id = "<thread>" }`` against the Runtime's log group selects
exactly this failed session's events.

:class:`FailureModal` surfaces those two coordinates — log group name and
filter expression — so the player can paste them straight into the
CloudWatch Logs console. It is deliberately self-contained: it does not
read any other game widget, so it stays readable even if the rest of the
UI was mid-stream when the crash hit.

Local-mode crashes do NOT use this modal — they keep the existing banner
+ JSONL log-file pointer, since there is no CloudWatch log group involved.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


def _filter_expression(thread_id: str) -> str:
    """Build the copy-pasteable CloudWatch Logs filter for one session."""
    return f'{{ $.thread_id = "{thread_id}" }}'


class FailureModal(ModalScreen[None]):
    """Modal shown on a remote-mode unhandled exception.

    Parameters:
        thread_id: The failed game's LangGraph ``thread_id``. Substituted
            into the ``{ $.thread_id = "<thread>" }`` filter expression.
        log_group: The CloudWatch log group name carrying the Runtime's
            logs/traces. ``None`` when ``GRAPHIA_LOG_GROUP`` is unset — the
            modal then shows the filter alone plus a note to run
            ``terraform output cloudwatch_log_group`` to recover the name.
        error_summary: Optional short one-line description of the error,
            rendered (dim) under the heading.

    The modal dismisses with ``None`` via the "Dismiss" button or the
    ``escape`` / ``enter`` keys. The caller (``GraphiaApp``) treats the
    game as over once this modal is shown, so dismissal just clears the
    overlay; the underlying screen already carries the exit-on-keypress
    behaviour.
    """

    DEFAULT_CSS = """
    FailureModal {
        align: center middle;
        background: $background 70%;
    }

    FailureModal > Vertical {
        width: 70%;
        height: auto;
        min-width: 50;
        max-width: 100;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }

    FailureModal #failure-title {
        height: auto;
        padding: 0 0 1 0;
        text-style: bold;
        color: $error;
    }

    FailureModal #failure-summary {
        height: auto;
        padding: 0 0 1 0;
        color: $text-muted;
    }

    FailureModal .failure-field-label {
        height: auto;
        text-style: bold;
        color: $text;
    }

    FailureModal .failure-field-value {
        height: auto;
        padding: 0 0 1 0;
        color: $accent;
    }

    FailureModal #failure-buttons {
        height: auto;
        align-horizontal: center;
        padding: 1 0 0 0;
    }

    FailureModal #failure-buttons Button {
        min-width: 12;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Dismiss", show=True),
        Binding("enter", "close", "Dismiss", show=False),
    ]

    def __init__(
        self,
        thread_id: str,
        log_group: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        super().__init__()
        self._thread_id: str = str(thread_id)
        self._log_group: str | None = (
            str(log_group) if isinstance(log_group, str) and log_group else None
        )
        self._error_summary: str | None = (
            str(error_summary)
            if isinstance(error_summary, str) and error_summary
            else None
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                "Remote game error",
                id="failure-title",
            )
            if self._error_summary is not None:
                yield Static(self._error_summary, id="failure-summary")
            yield Static(
                "This game ran in the deployed AgentCore Runtime. To "
                "investigate, open CloudWatch Logs and use the coordinates "
                "below — they select exactly this session's events.",
                id="failure-intro",
            )

            yield Label("CloudWatch log group:", classes="failure-field-label")
            if self._log_group is not None:
                yield Static(self._log_group, classes="failure-field-value")
            else:
                # Degrade gracefully: GRAPHIA_LOG_GROUP was unset. Never
                # render the literal "None" — point the player at the
                # Terraform output that carries the real name instead.
                yield Static(
                    "(unknown — run `terraform output cloudwatch_log_group` "
                    "from infra/terraform/ to get the log group name)",
                    classes="failure-field-value",
                )

            yield Label("Filter expression:", classes="failure-field-label")
            yield Static(
                _filter_expression(self._thread_id),
                classes="failure-field-value",
            )

            with Horizontal(id="failure-buttons"):
                yield Button("Dismiss", id="failure-dismiss", variant="error")

    def on_mount(self) -> None:
        try:
            self.query_one("#failure-dismiss", Button).focus()
        except Exception:  # noqa: BLE001
            # Defensive: keybindings still dismiss the modal if focus fails.
            pass

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "failure-dismiss":
            self.dismiss(None)
