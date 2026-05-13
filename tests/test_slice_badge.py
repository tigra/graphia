"""Slice 5 sub-task 3: corner mode-badge renders the right label per mode.

The badge is a small decorative widget in ``src/graphia/ui/badge.py`` that
sits over the main ``Vertical`` container and shows ``[local]`` or
``[remote]`` so a glance at the screen tells the player which mode the
session is using.

The label is captured at ``GraphiaApp.__init__`` time (eager
``load_config()`` call), then bound into ``CornerBadge`` inside
``compose()``. The two parametrised cases below pin both branches of that
``"[remote]" if self.config.remote_mode else "[local]"`` ternary. The
``can_focus is False`` assertion pins that the badge does not steal
keyboard navigation from the input/modals.

Mocking
-------

The autouse ``safe_llm`` fixture from ``conftest.py`` is enough: the badge
is constructed during ``compose()`` and we never drive the graph past the
opening ``compose`` + ``pause`` pair, so no LLM call site is reached.
``env`` provides the dummy bearer token and tmp log/checkpoint paths.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from graphia.ui.app import GraphiaApp
from graphia.ui.badge import CornerBadge

# Synthetic ARN — never resolved against AWS, only used to satisfy the
# "remote_mode requires runtime_invocation_url" guard inside load_config().
FAKE_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test"
)


@pytest.mark.parametrize(
    ("mode", "expected_label"),
    [
        ("local", "[local]"),
        ("remote", "[remote]"),
    ],
)
async def test_corner_badge_renders_correct_label_for_mode(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_label: str,
) -> None:
    """Pin the badge label per mode.

    The env vars must be set BEFORE ``GraphiaApp()`` is constructed because
    the badge's label is captured in ``__init__`` (eager ``load_config()``)
    and bound into ``CornerBadge`` inside ``compose()``. Setting env vars
    after instantiation would have no effect — the label is already frozen.

    Asserts:
      * ``query_one("#mode-badge", CornerBadge)`` finds the widget,
      * ``badge._label`` (the bound label string) equals the expected
        bracketed mode marker — also confirmed via ``badge.render()`` so
        a future change to the storage attribute name doesn't silently
        break the assertion,
      * ``badge.can_focus is False`` — the badge is decorative and must
        not capture keyboard focus from the input/modals.
    """
    # Set env vars FIRST — load_config() runs inside GraphiaApp.__init__,
    # so anything set after construction would be too late.
    if mode == "remote":
        monkeypatch.setenv("GRAPHIA_REMOTE", "1")
        monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
        # Slice 6: ``make_diary_store`` (called inside ``build_graph`` once
        # the worker boots) raises SystemExit in remote mode without an id.
        # The badge test never drives the graph far enough to exercise the
        # store, so a sentinel value is enough to clear the factory guard.
        monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-tests")
    else:
        # Defensive: clear in case the parent shell or a prior test left
        # them set. ``env`` does not touch these two specifically.
        monkeypatch.delenv("GRAPHIA_REMOTE", raising=False)
        monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
        monkeypatch.delenv("GRAPHIA_MEMORY_ID", raising=False)

    app = GraphiaApp()

    # Sanity: the config object the app loaded matches the mode under test.
    # This pins the test against an accidental refactor that decouples
    # ``config.remote_mode`` from the env vars — without it, a regression
    # that always set the label to "[local]" could be masked by a bug that
    # also always left ``remote_mode=False``.
    assert app.config.remote_mode is (mode == "remote")

    async with app.run_test() as pilot:
        await pilot.pause()

        badge = app.query_one("#mode-badge", CornerBadge)

        # Bound label captured at __init__-time matches the mode.
        assert badge._label == expected_label, (
            f"[{mode}] expected badge._label={expected_label!r}, "
            f"got {badge._label!r}"
        )

        # render() is the public surface Textual uses to display the badge;
        # asserting on its plain-text content means the test still pins the
        # user-visible invariant if the storage attribute is ever renamed.
        # We compare on plain text (not type-equality with str) because the
        # widget intentionally returns a Rich ``Text`` instance — that
        # bypasses Rich's markup parser, which would otherwise eat the
        # square brackets in ``[local]`` / ``[remote]`` and render an
        # invisible label (see 2026-05-13 visibility regression below).
        rendered = badge.render()
        rendered_plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert rendered_plain == expected_label, (
            f"[{mode}] expected badge.render() plain text=={expected_label!r}, "
            f"got {rendered_plain!r} (raw: {rendered!r})"
        )

        # Decorative widget — must not steal keyboard focus.
        assert badge.can_focus is False, (
            f"[{mode}] badge.can_focus should be False so the input/modals "
            f"keep keyboard focus; got {badge.can_focus!r}"
        )


@pytest.mark.parametrize(
    ("mode", "expected_label"),
    [
        ("local", "[local]"),
        ("remote", "[remote]"),
    ],
)
async def test_corner_badge_label_is_visible_in_rendered_screen(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_label: str,
) -> None:
    """Regression: the badge LABEL must be visible in the rendered terminal.

    The earlier ``test_corner_badge_renders_correct_label_for_mode`` only
    pins widget data (``badge._label``, ``badge.render()`` return value). It
    does NOT check that those characters survive Textual's compositor and
    actually paint on screen — under the project theme's accent palette
    ``color: $text`` collides with ``background: $accent`` and the label
    becomes invisible against its own background (user-reported 2026-05-13
    smoke: "I see orange top-right background, but no local/remote").

    To pin the user-facing invariant we capture the rendered screen via
    ``app.export_screenshot()`` (Textual's stable SVG-renderer surface),
    concatenate every ``<text>`` element's body, and assert the literal
    bracketed label appears in the result. Because Textual paints one
    character per ``<text>`` element with a width attribute, the raw
    concatenation reproduces the visible reading order, so a plain
    substring check is enough — no SVG-DOM walking required.

    A wide terminal width (120 cells) keeps the 9-cell badge clear of the
    right edge so the assertion can't accidentally pass on partial labels
    after clipping (or fail purely because of clipping).
    """
    # Set env vars BEFORE constructing the app — load_config() runs in
    # __init__ and the label is captured there.
    if mode == "remote":
        monkeypatch.setenv("GRAPHIA_REMOTE", "1")
        monkeypatch.setenv("GRAPHIA_RUNTIME_URL", FAKE_RUNTIME_ARN)
        # Slice 6: clear ``make_diary_store``'s remote-mode guard. See the
        # sibling test above for context.
        monkeypatch.setenv("GRAPHIA_MEMORY_ID", "fake-memory-id-for-tests")
    else:
        monkeypatch.delenv("GRAPHIA_REMOTE", raising=False)
        monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
        monkeypatch.delenv("GRAPHIA_MEMORY_ID", raising=False)

    app = GraphiaApp()

    # Wide enough that the 9-cell badge isn't clipped at the right edge.
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()

        svg = app.export_screenshot()

        # Concatenate every <text>...</text> body. Textual emits one
        # <text> per painted cell (or per styled run), so concatenation
        # reproduces the visible reading order.
        text_chunks = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
        rendered_text = "".join(text_chunks).replace("&#160;", " ")

        assert expected_label in rendered_text, (
            f"[{mode}] expected the literal {expected_label!r} to appear in "
            f"the rendered screen output, but it was not visible. This means "
            f"the badge background paints but the label text is invisible "
            f"(colour collision with the theme accent). Inspect "
            f"CornerBadge.DEFAULT_CSS — `color: $text` on `background: $accent` "
            f"collides under the active theme."
        )
