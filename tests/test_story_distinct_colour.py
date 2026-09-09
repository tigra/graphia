"""The closing story is coloured distinctively in the public pane.

Added 2026-09-09 at the author's request ("colour the creative recap in the UI
distinctively"), on top of spec 040 (The Moderator's Closing Story).

WHAT MAKES THIS NON-VACUOUS, measured rather than assumed. The story arrives as
a Moderator ``SystemMessage``, exactly like the winner line and the closing
facts, so **message type cannot be the discriminator** — before this change all
three rendered identically. The discriminator is the message's own
``CLOSING_STORY_KWARG`` flag, and the control below is a ``SystemMessage`` with
the *same content* and no flag: if the styling were keyed on type, position or
content, that control would come back styled and this file would fail.

WHY THE SPAN COUNT IS THE ASSERTION. ``Text.assemble(("body", ""))`` adds **no
span** for an empty style (measured on the installed Rich), so an ordinary
Moderator row carries exactly ONE span — its label — and the story row carries
TWO. That is a structural difference, not a colour-name comparison, and it
holds under any restyle that keeps the body styled at all.

WHY IT TESTS ``_write_public`` DIRECTLY RATHER THAN THROUGH A DRIVEN GAME. The
composition either side of this function is already pinned: the production
message carries ``CLOSING_STORY_KWARG`` (spec 040 Slice 5's clamp tests assert
it on the message), and ``_write_public`` is the public pane's only writer. What
had no witness was the styling decision itself, which is a pure function of the
message — so it is exercised with a duck-typed ``self`` rather than by paying
for an ``App.run_test()`` game, whose ``_install_live_dispatcher`` helper is
also a known ~5%-flaky path (see ``context/product/backlog.md``).

THE PLAIN TEXT IS PINNED TOO, and that is the half that protects other files: a
dozen tests across the spec-040 suite read rows as text and assert on the
``"Moderator: "`` prefix and on ordering. This change moved styles only, so the
last test here asserts a story row and an unflagged row of the same body render
**identical plain text**.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from rich.text import Text

from graphia.state import CLOSING_STORY_KWARG
from graphia.ui.app import (
    STORY_BODY_STYLE,
    STORY_LABEL_STYLE,
    GraphiaApp,
)

#: Two paragraphs, because the real thing is several and the separator is the
#: one character class a span-based writer could plausibly mishandle.
_STORY = (
    "Hana was the first to go, and nobody who spoke that morning knew why.\n\n"
    "What the record kept instead was quieter: a name written down twice."
)


class _Recorder:
    """Stands in for ``#public-log``, keeping every renderable written to it."""

    def __init__(self) -> None:
        self.written: list[Any] = []

    def write(self, renderable: Any) -> None:
        self.written.append(renderable)


class _Pane:
    """Duck-typed ``self`` for :meth:`GraphiaApp._write_public`."""

    def __init__(self, log: _Recorder) -> None:
        self._log = log

    def query_one(self, selector: str, kind: Any = None) -> _Recorder:
        assert selector == "#public-log", (
            f"_write_public asked for {selector!r}; this stub only serves the "
            "public pane, so the write path has changed"
        )
        return self._log


def _render(msg: Any) -> Text:
    """Run the real ``_write_public`` over one message; return the row."""
    log = _Recorder()
    GraphiaApp._write_public(_Pane(log), msg)  # type: ignore[arg-type]
    assert len(log.written) == 1, (
        f"expected exactly one row for one message, got {len(log.written)}"
    )
    row = log.written[0]
    assert isinstance(row, Text), f"row is {type(row)!r}, not a rich Text"
    return row


def _story_message() -> SystemMessage:
    return SystemMessage(
        content=_STORY, additional_kwargs={CLOSING_STORY_KWARG: True}
    )


def _styles_of(row: Text) -> list[str]:
    return [str(span.style) for span in row.spans]


def test_the_closing_story_gets_its_own_label_and_body_styles() -> None:
    """Both halves of the row are styled, and with the shipped constants."""
    row = _render(_story_message())

    assert _styles_of(row) == [STORY_LABEL_STYLE, STORY_BODY_STYLE], (
        "the story row's spans are not the two story styles; got "
        f"{_styles_of(row)!r}"
    )
    label_span, body_span = row.spans
    assert row.plain[label_span.start : label_span.end] == "Moderator: "
    assert row.plain[body_span.start : body_span.end] == _STORY, (
        "the body span does not cover the whole story, so part of it renders "
        "unstyled"
    )


def test_an_unflagged_moderator_message_is_not_story_coloured() -> None:
    """THE CONTROL: same type, same content, no flag — and no story styling.

    This is the assertion that makes the rest of the file mean something. If
    the styling were keyed on the message type, on its position in the pane or
    on its content, this row would come back with the story's styles.
    """
    plain_moderator = SystemMessage(content=_STORY)
    row = _render(plain_moderator)

    assert _styles_of(row) == ["bold cyan"], (
        "an ordinary Moderator message no longer renders as a single "
        f"bold-cyan label; got {_styles_of(row)!r}"
    )
    assert STORY_LABEL_STYLE not in _styles_of(row)
    assert STORY_BODY_STYLE not in _styles_of(row)


def test_a_player_line_is_not_story_coloured_either() -> None:
    """The other voice in this pane keeps its own plain-bold label."""
    row = _render(AIMessage(content=_STORY, additional_kwargs={"speaker": "Mika"}))

    assert _styles_of(row) == ["bold"], (
        f"a player line's styling changed; got {_styles_of(row)!r}"
    )
    assert row.plain.startswith("Mika: ")


def test_the_story_is_actually_distinguishable_from_every_other_row() -> None:
    """"Distinctively" as a measurement, not as an intention.

    Two claims, and the second is the one a future restyle would break
    silently: the story's styles differ from both other voices' (so it is not
    merely styled, it is styled *differently*), and its body is styled at all
    (so the difference is visible across the paragraphs rather than only in the
    eleven characters of the label).
    """
    story = _styles_of(_render(_story_message()))
    moderator = _styles_of(_render(SystemMessage(content=_STORY)))
    player = _styles_of(
        _render(AIMessage(content=_STORY, additional_kwargs={"speaker": "Mika"}))
    )

    assert story != moderator, "the story renders exactly like the facts do"
    assert story != player, "the story renders exactly like a player's line"
    assert len(story) == 2, (
        "the story row has one span, so only its label is styled and the "
        "paragraphs themselves are indistinguishable from any other body"
    )
    assert STORY_BODY_STYLE.strip(), "the body style is empty, so nothing shows"


def test_colouring_the_story_moved_no_text() -> None:
    """The row's plain text is byte-identical to the unflagged rendering.

    A dozen tests across the spec-040 suite read rows as TEXT — the ordering
    assertions, the beat assertions, the markup regressions. This is what says
    they still measure what they measured before the colour landed.
    """
    flagged = _render(_story_message()).plain
    unflagged = _render(SystemMessage(content=_STORY)).plain

    assert flagged == unflagged, (
        "colouring the story changed the text of the row, not just its "
        f"styling:\n{flagged!r}\n{unflagged!r}"
    )
    assert flagged == f"Moderator: {_STORY}"
