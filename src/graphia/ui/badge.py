"""Corner-docked mode badge widget for the Graphia TUI."""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.geometry import Size
from textual.widget import Widget


class CornerBadge(Widget):
    """A small `[local]` / `[remote]` indicator for the top-right corner.

    Decorative: does not intercept input or steal focus. The label is set
    once at construction and remains constant for the session.
    """

    DEFAULT_CSS = """
    CornerBadge {
        layer: overlay;
        dock: right;
        width: auto;
        height: 1;
        padding: 0 1;
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    can_focus = False

    def __init__(self, label: str, *, id: str = "mode-badge") -> None:
        super().__init__(id=id)
        self._label = label

    def render(self) -> RenderResult:
        # Return a Rich ``Text`` constructed via the plain-string ctor (not
        # ``Text.from_markup``) so the square brackets in ``[local]`` /
        # ``[remote]`` are treated as literal characters, not as opening
        # Rich-markup tags. Returning ``self._label`` as a bare ``str`` made
        # Textual run it through markup parsing, which silently swallowed
        # the bracketed body — the user saw the orange accent background
        # but no label text. ``Text(self._label)`` bypasses the parser.
        return Text(self._label)

    def get_content_width(self, container: Size, viewport: Size) -> int:
        # `Widget.render()` returning a plain string does not auto-measure
        # for `width: auto`; without this override the badge collapses to a
        # 2-cell strip and the label is clipped. Returning the literal label
        # length (label has no markup) gives `auto` the right answer at any
        # terminal width.
        return len(self._label)

    def get_content_height(
        self, container: Size, viewport: Size, width: int
    ) -> int:
        # Single-line badge; pin to 1 so `height: 1` stays exact even if
        # padding/measurement defaults change in future Textual versions.
        return 1
