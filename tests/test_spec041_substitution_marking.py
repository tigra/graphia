"""A substituted diary entry is visibly marked — spec 041, Slice 8 (Task 8.5).

Functional-spec 041's fourth requirement, delivered end-to-end for the first
of the three substitution channels: *a reviewer reading a preserved game can
see that an entry was not the player's writing.* Three layers had to agree for
that to be true, and this module is where they are held to it.

    ``nodes/day.day_diary``            writes ``DiaryRecord["substituted"]``,
                                       and ONLY on the substitution branch
    ``tools/eval_transcript``          emits ``substituted="true"``,
                                       and ONLY for the literal ``True``
    ``eval_ledger.tokenize_transcript``reads it back, and ONLY off ``"true"``

Each of those "only"s is what the marking is worth. A flag written on every
record would mark every honest entry in every preserved game; an attribute
emitted as ``substituted="false"`` would become a field readers learn to
ignore; a reader that coerced its value would disagree with the writer about
what the file says. So the contract asserted here is mostly a contract about
what does NOT happen.

Why a module of its own rather than rows in the three modules it spans
----------------------------------------------------------------------

Because the failure this feature can have is a DISAGREEMENT, and a
disagreement is invisible from inside either half. ``eval_transcript`` and
``eval_ledger`` share no import (the pure reader must never import the writer —
that separation is deliberate and correct), so a drift between the literal one
writes and the literal the other looks for would leave every existing test in
both modules green while no preserved game was marked at all. The same shape,
and the same reason, as ``test_transcript_highlight``'s
``test_the_writer_and_the_reader_agree_on_the_marker_literal``, which ties the
``human="true"`` seat marker's two halves together; this module's
:func:`test_the_writer_and_the_reader_agree_on_the_substitution_literal` is its
direct sibling.

What lives elsewhere, deliberately
----------------------------------

* **The record's shape** —
  ``test_slice39_diary_before_night.test_record_carries_exactly_day_cursor_and_text``
  asserts an authored record carries exactly its three pre-041 keys, with a
  message naming the unconditional-flag error. That fires one layer UPSTREAM of
  everything here, before any transcript is rendered. This module's
  :func:`test_an_authored_document_carries_no_marking_anywhere` catches the same
  class of error at the transcript layer, and the two fail with different
  messages. Both are worth keeping: the upstream one names the node, this one
  names the artifact a reviewer actually reads.
* **The attribute's span kind** — ``test_transcript_highlight``'s
  ``_ATTR_NAME_CASES`` sweep pins that ``substituted``'s value is lifted into
  its own achromatic ``attr`` span, and ``_ACHROMATIC_ATTR_KEYS`` pins that it
  never takes a side. Not repeated here.
* **The body's colour and text style** —
  ``test_ledger_viewer.test_the_substituted_diary_style_is_the_diarys_rule_plus_strike``,
  next to that file's ``App.run_test()`` harness. This module is terminal-free.
* **The per-run substitution COUNTS** — ``test_substitution_counts`` and
  ``test_substitution_ledger`` (Slices 6 and 7). A count is a per-run aggregate
  with no addressee; it cannot say *which* entry substituted, which is the
  whole reason this marking exists beside it.

Anti-vacuity discipline
-----------------------

Marking is a feature whose tests all pass on arrival, so every absence
assertion here is paired with a presence assertion that would fail if the
fixture stopped exercising anything:

* the byte-identity test asserts the authored document really does contain
  diaries (it counts them) before asserting it contains no marking;
* the "changes nothing else" test asserts the two documents DIFFER before
  asserting the difference is exactly the one attribute;
* the reader's defensive sweep is bracketed by the positive case (``"true"``
  really does re-kind the body), so a tokenizer that had stopped reading the
  flag at all could not pass the negatives;
* the span-shape test asserts the tag head GROWS, which is the direction easily
  written backwards — see :func:`test_the_flag_splits_the_tag_head_and_nothing_else`.

Strictly offline: no model, no network, no terminal. One test drives the real
``day_diary`` node with a failing tier stand-in, which is why
``monkeypatch``ing ``get_large`` appears at all; the autouse ``safe_llm`` net
covers every other call site, and the autouse ``reset_substitution_sink``
fixture in ``conftest`` owns the sink that node's substitution books.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from graphia import eval_ledger
from graphia.eval_ledger import (
    KIND_ATTR,
    KIND_DIARY,
    KIND_DIARY_SUBSTITUTED,
    KIND_MARKER,
    KIND_RECAP,
    tokenize_transcript,
)
from graphia.nodes import day as day_nodes
from graphia.state import PlayerState
from graphia.tools import eval_transcript
from graphia.tools.eval_transcript import render_transcript

# The two literals the whole slice turns on, written out HERE rather than
# imported from either side. That is the point of the agreement test below: a
# constant imported from the writer and compared to the writer is a tautology,
# and one imported from the reader and compared to the reader is the same
# tautology mirrored. These are the bytes a committed transcript must hold.
_SUBSTITUTED_ATTRIBUTE = 'substituted="true"'
_SUBSTITUTED_VALUE = "true"


# ===========================================================================
# The fixture: one Day whose trailer holds two diaries
# ===========================================================================
#
# Two owners, on OPPOSITE SIDES, and that is load-bearing twice over. Ben is
# `role="Mafia"`, so the tokenizer kinds his diary's owner name `attr-mafia`
# while the Day value beside it stays achromatic — which means a test that
# marks Ben's entry is marking the more decorated of the two tags, and the
# "nothing else moved" assertions are made against the harder case. And with
# two diaries in one trailer, marking ONE of them leaves an unmarked sibling
# on the very next line: the strongest available statement that the flag is
# read per line and not per file.


def _player(pid: str, name: str, role: str) -> PlayerState:
    """A surviving AI player, from the real dataclass the harness passes in."""
    return PlayerState(
        id=pid,
        name=name,
        role=role,  # type: ignore[arg-type]
        is_human=False,
        is_alive=True,
        persona=None,
    )


def _transcript_players() -> dict[str, PlayerState]:
    """The final ``players`` map — ids deliberately unlike the display names."""
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-ben": _player("p-ben", "Ben", "mafia"),
    }


_AVA_ENTRY = "I watched Ben all evening and he never once looked up."
_BEN_ENTRY = "Ava is closer to the truth than she knows."


def _record(text: str, *, substituted: bool | None = None) -> dict[str, Any]:
    """One ``DiaryRecord``, in the exact shape ``day_diary`` builds.

    ``substituted=None`` means the key is ABSENT, which is what an authored
    entry looks like — never ``False``. Passing ``False`` explicitly is
    therefore a hand-edit and is available as such (a reader-boundary shape the
    node cannot produce), which is why the parameter is tri-state rather than a
    plain ``bool``.
    """
    record: dict[str, Any] = {"day": 2, "thoughts_before": 1, "text": text}
    if substituted is not None:
        record["substituted"] = substituted
    return record


def _day_events(diaries: dict[str, list[dict[str, Any]]] | None) -> list[dict[str, Any]]:
    """A one-round Day whose trailer carries ``diaries`` (or no diary delta).

    Shaped from the real engine's stream: ``day_open`` → a ``day_turn`` that
    wraps the round → ``day_close`` → ``day_diary``. The diary delta arrives
    while the ``<day>`` section is still open, which is what puts the elements
    in the day-level trailer rather than inside the last ``<round>``.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 2 breaks.")]}},
        {
            "day_turn": {
                "messages": [AIMessage(content="I heard nothing.", name="Ava")],
                "day_rounds": 1,
            }
        },
        {"day_close": {"messages": [SystemMessage(content="The Day ends.")]}},
    ]
    if diaries is not None:
        events.append({"day_diary": {"private_diaries": diaries}})
    return events


def _render(diaries: dict[str, list[dict[str, Any]]] | None) -> str:
    """Render the fixture Day through the real production renderer."""
    return render_transcript(
        _day_events(diaries),
        _transcript_players(),
        game_index=1,
        run_meta=None,
    )


def _authored_diaries() -> dict[str, list[dict[str, Any]]]:
    """Both entries the players' own writing — the pre-041 shape, exactly."""
    return {
        "p-ava": [_record(_AVA_ENTRY)],
        "p-ben": [_record(_BEN_ENTRY)],
    }


def _marked_diaries() -> dict[str, list[dict[str, Any]]]:
    """Both entries substituted — the same records plus the carried flag."""
    return {
        "p-ava": [_record(_AVA_ENTRY, substituted=True)],
        "p-ben": [_record(_BEN_ENTRY, substituted=True)],
    }


def _one_marked_diaries() -> dict[str, list[dict[str, Any]]]:
    """Ben's entry substituted, Ava's authored — one marked line, one not."""
    return {
        "p-ava": [_record(_AVA_ENTRY)],
        "p-ben": [_record(_BEN_ENTRY, substituted=True)],
    }


def _diary_lines(doc: str) -> list[str]:
    """Every ``<diary …>`` line of a rendered document, in order."""
    return [line for line in doc.splitlines() if line.startswith("<diary ")]


def _spans_by_line(doc: str) -> list[list[tuple[str, str]]]:
    """``tokenize_transcript(doc)`` regrouped into one span list per line.

    The tokenizer is a function of the whole FILE (two whole-file passes for
    the cast map and the seat), so its output cannot be compared line by line
    without first re-cutting it at the newlines. Only ``plain`` spans may carry
    one — the separator rule — so a span is split on ``"\\n"`` and its pieces
    handed to consecutive lines; empty pieces contribute nothing, preserving
    the tokenizer's own no-empty-span guarantee.

    :func:`test_the_line_regrouping_helper_really_cuts_the_document` is this
    helper's positive control, because a regrouping that quietly dropped
    everything would make every per-line comparison below trivially equal.
    """
    lines: list[list[tuple[str, str]]] = [[]]
    for text, kind in tokenize_transcript(doc):
        pieces = text.split("\n")
        for index, piece in enumerate(pieces):
            if index:
                lines.append([])
            if piece:
                lines[-1].append((piece, kind))
    return lines


def test_the_line_regrouping_helper_really_cuts_the_document() -> None:
    """The positive control for :func:`_spans_by_line`, on the marked fixture.

    Three claims, each of which a broken regrouping would fail while leaving
    the comparisons below vacuously green: the line count is the document's own,
    the text joins back to the document exactly, and no piece carries a newline.
    """
    doc = _render(_one_marked_diaries())
    lines = _spans_by_line(doc)

    assert len(lines) == doc.count("\n") + 1
    assert "\n".join("".join(text for text, _ in line) for line in lines) == doc
    assert not [
        piece for line in lines for piece in line if "\n" in piece[0]
    ]
    # And the premise: the marked line really is in there, so the per-line
    # comparisons have something to find.
    assert any(
        _SUBSTITUTED_ATTRIBUTE in "".join(text for text, _ in line)
        for line in lines
    )


# ===========================================================================
# 1. The writer: the exact literal, and the absence that matters more
# ===========================================================================


def test_the_marked_element_carries_exactly_the_substituted_true_attribute() -> None:
    """The emitted element, pinned as the whole line rather than by substring.

    The attribute literal is the interface between two modules that never
    import each other, so it is pinned as bytes: ``substituted="true"``, lower
    case, double-quoted, one space before it, and LAST in the tag — after
    ``day``, in the order ``_append_diaries`` appends them. Every near-miss a
    reviewer would never notice fails here: ``substituted='true'``,
    ``substituted=true``, ``Substituted="true"``, ``substituted="True"``.

    Asserted on the full line, because the element being SINGLE-LINE is part of
    the same claim (tech-spec 041; Task 8.2 keeps the element inline) and a
    substring assertion would hold on a tag broken across two lines.
    """
    doc = _render(_marked_diaries())
    lines = _diary_lines(doc)

    assert lines == [
        f'<diary player="Ava" day="2" {_SUBSTITUTED_ATTRIBUTE}>{_AVA_ENTRY}</diary>',
        f'<diary player="Ben" day="2" {_SUBSTITUTED_ATTRIBUTE}>{_BEN_ENTRY}</diary>',
    ]
    # Restated as the attribute's own spelling, so a failure says WHICH half
    # drifted rather than showing two walls of tag.
    assert doc.count(_SUBSTITUTED_ATTRIBUTE) == 2
    assert "substituted='true'" not in doc
    assert 'substituted="True"' not in doc
    assert 'Substituted="' not in doc


def test_an_authored_document_carries_no_marking_anywhere() -> None:
    """An authored Day renders byte-identically to a pre-041 capture.

    THE SECOND CATCHER, not the first. ``test_slice39_diary_before_night``'s
    ``test_record_carries_exactly_day_cursor_and_text`` fires one layer
    upstream, at the record, before any transcript exists; this one fires at
    the artifact a reviewer actually reads, and the two report the same class of
    error with different messages. An unconditional flag — ``"substituted":
    False`` on every record, or an attribute emitted unconditionally as
    ``substituted="false"`` — fails here.

    The absence assertion is bracketed by a presence one: the document really
    does hold two diaries, so "no marking" is a statement about marked-ness and
    not about an empty trailer.
    """
    doc = _render(_authored_diaries())
    lines = _diary_lines(doc)

    # The premise: two real diaries went in.
    assert len(lines) == 2
    assert lines == [
        f'<diary player="Ava" day="2">{_AVA_ENTRY}</diary>',
        f'<diary player="Ben" day="2">{_BEN_ENTRY}</diary>',
    ]
    # ...and the word appears NOWHERE in the whole document — not as an
    # attribute, not as `substituted="false"`, not in any other spelling.
    assert "substituted" not in doc, (
        "an authored diary's document carries the substitution marking, so "
        "every honest entry in every preserved game is now marked. The "
        "attribute must be written ONLY on the substitution branch and never "
        "as `substituted=\"false\"` — see "
        "`eval_transcript._diary_substituted_attr`."
    )


def test_the_marking_adds_the_attribute_and_changes_nothing_else() -> None:
    """Byte identity stated as a DIFFERENCE, which is the strongest form of it.

    Rendering the same Day twice — once authored, once with the same records
    plus the flag — and asserting that deleting the attribute from the second
    reproduces the first EXACTLY. That covers what a substring assertion cannot:
    no line is re-flowed, no element is re-ordered, no text is re-clamped, and
    the trailer keeps its place between the last ``</round>`` and ``</day>``.

    Non-vacuous by construction: the two documents are asserted to differ
    first, and the number of deletions is pinned, so a renderer that had
    stopped emitting the attribute would fail rather than pass twice.
    """
    authored = _render(_authored_diaries())
    marked = _render(_marked_diaries())

    assert marked != authored, "the flag changed nothing at all"
    assert marked.count(f" {_SUBSTITUTED_ATTRIBUTE}") == 2
    assert marked.replace(f" {_SUBSTITUTED_ATTRIBUTE}", "") == authored, (
        "the substitution marking changed something other than adding its own "
        "attribute to the tags that carry it"
    )
    # The same statement per line, so a failure names the line that moved.
    assert len(marked.splitlines()) == len(authored.splitlines())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(True, _SUBSTITUTED_ATTRIBUTE, id="the-literal-True"),
        pytest.param("true", "", id="the-string-true"),
        pytest.param("True", "", id="the-string-True"),
        pytest.param(1, "", id="the-int-1"),
        pytest.param(1.0, "", id="the-float-1"),
        pytest.param(False, "", id="False"),
        pytest.param(0, "", id="0"),
        pytest.param(None, "", id="None"),
        pytest.param("", "", id="empty-string"),
        pytest.param("yes", "", id="yes"),
        pytest.param([True], "", id="a-list-holding-True"),
    ],
)
def test_only_the_literal_true_yields_the_attribute(value: Any, expected: str) -> None:
    """The WRITER's defence: identity against ``True``, never a truthiness test.

    ``day_diary`` writes the key only on the substitution branch and only as
    ``True``, so anything else arriving at this renderer came from a hand-built
    record or a hand-edited checkpoint — defensiveness at a read boundary, not
    coverage of a reachable node path (``_SubstitutionMark.substituted`` is a
    ``bool`` and the node cannot produce anything else).

    ``1`` is the row that makes this an identity check rather than an equality
    one: ``1 == True`` is ``True`` in Python, so ``substituted == True`` would
    honour it. And honouring a plausible hand-edit is the failure that matters,
    because it would let this writer disagree with ``eval_ledger`` about what a
    file says — the reader's own defence (its half of this sweep lives in
    :func:`test_a_hand_edited_flag_value_reads_as_authored`) rejects every
    non-``"true"`` string, so a writer that guessed would emit a marking the
    reader would then read as authored.

    The same posture, and the same table shape, as ``_diary_day_attr``'s.
    """
    assert eval_transcript._diary_substituted_attr(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("true", id="the-string-true"),
        pytest.param("True", id="the-string-True"),
        pytest.param(1, id="the-int-1"),
        pytest.param(1.0, id="the-float-1"),
        pytest.param(False, id="False"),
    ],
)
def test_a_hand_edited_record_value_renders_as_authored(value: Any) -> None:
    """The same defence through the real renderer, on a whole document.

    The helper-level sweep above pins the function; this pins the ARTIFACT, and
    the two are not the same claim — a caller that appended the helper's result
    unconditionally, or that tested the record's key with ``if "substituted" in
    record``, would pass the sweep above and mark a hand-edited record here.

    ``False`` is in the table on purpose and is the case that would bite
    hardest under a key-presence test: a record explicitly denying substitution
    must not be marked because it mentioned the subject.
    """
    doc = _render({"p-ava": [_record(_AVA_ENTRY, substituted=value)]})

    assert _diary_lines(doc) == [f'<diary player="Ava" day="2">{_AVA_ENTRY}</diary>']
    assert "substituted" not in doc


def test_the_substituted_body_is_single_line_and_needs_no_clamp() -> None:
    """Task 8.2's premise, checked rather than cited.

    "Keep the element single-line" is free on the marked path only because the
    substituted text is ``nodes.day._DIARY_FALLBACK`` VERBATIM — the node files
    it without passing it through ``_clamp_diary_entry`` at all. So the
    constant itself must be single-line and comfortably under the clamp, or the
    marked element could break across lines and the reader (which decides a
    body's kind per line, off the flag on its own tag) would see a tag with no
    body and a body with no tag.

    Pinned here, in the module that depends on it, rather than left as an
    assumption in a task note. The rendered-document half of the same claim is
    ``test_eval_transcript.test_one_line_sections_are_written_inline``.
    """
    fallback = day_nodes._DIARY_FALLBACK

    assert "\n" not in fallback and "\r" not in fallback
    assert fallback == fallback.strip(), "a stripped body would not round-trip"
    assert len(fallback) < day_nodes.DIARY_MAX_CHARS
    # And it really does render as one line through the writer.
    doc = _render({"p-ava": [_record(fallback, substituted=True)]})
    assert _diary_lines(doc) == [
        f'<diary player="Ava" day="2" {_SUBSTITUTED_ATTRIBUTE}>{fallback}</diary>'
    ]


# ===========================================================================
# 2. The writer and the reader agree
# ===========================================================================


def test_the_writer_and_the_reader_agree_on_the_substitution_literal() -> None:
    """Two modules, two independent literals, one attribute — tied together here.

    The direct sibling of ``test_transcript_highlight``'s
    ``test_the_writer_and_the_reader_agree_on_the_marker_literal``, and it earns
    its place for exactly the reason that one does: ``eval_transcript`` writes
    the attribute, ``eval_ledger`` reads it, and NOTHING in either module refers
    to the other. That separation is correct — the pure reader must not import
    the writer — and it is precisely why a drift between them would be SILENT:
    transcripts would carry a marking nothing looks for, every test in both
    modules would pass, and every substituted entry would go on rendering as an
    honest one forever.

    Both halves are resolved from the production symbols and compared against
    the bytes written at the top of this module, so neither side is compared
    only to itself.

    The last assertion is the lookbehind ``_ATTR_VALUE_RE`` already carries: a
    longer attribute name ENDING in ``substituted`` is not the flag. Without it
    a hypothetical ``notsubstituted="true"`` would re-kind a body as void.
    """
    written = eval_transcript._diary_substituted_attr(True)

    # The writer's side.
    assert written == _SUBSTITUTED_ATTRIBUTE
    # The reader's side, named by its own constants.
    assert eval_ledger._SUBSTITUTED_ATTR == "substituted"
    assert eval_ledger._SUBSTITUTED_ATTR_TRUE == _SUBSTITUTED_VALUE

    # ...and the reader's scan really finds the writer's own output, key and
    # value both, through the same regex that lifts the value into an `attr`
    # span. Space-prefixed exactly as `_append_diaries` appends it.
    match = eval_ledger._ATTR_VALUE_RE.search(f" {written}")
    assert match is not None, (
        f"the reader's attribute scan does not match the writer's {written!r}"
    )
    assert match.group("key") == eval_ledger._SUBSTITUTED_ATTR
    assert match.group("value") == eval_ledger._SUBSTITUTED_ATTR_TRUE
    assert eval_ledger._is_substituted(f" {written}") is True

    # The lookbehind: an attribute merely ENDING in the flag's name is not it.
    assert eval_ledger._is_substituted(' notsubstituted="true"') is False


@pytest.mark.parametrize(
    ("label", "record", "expected_kind"),
    [
        pytest.param(
            "authored", _record(_AVA_ENTRY), KIND_DIARY, id="authored"
        ),
        pytest.param(
            "substituted",
            _record(day_nodes._DIARY_FALLBACK, substituted=True),
            KIND_DIARY_SUBSTITUTED,
            id="substituted",
        ),
    ],
)
def test_a_diary_record_round_trips_from_the_writer_to_the_body_kind(
    label: str, record: dict[str, Any], expected_kind: str
) -> None:
    """End to end, writer to reader, with no literal shared between the halves.

    Everything else in section 3 feeds the tokenizer a hand-written tag. This
    feeds it a document ``render_transcript`` actually produced from a
    ``DiaryRecord`` in the shape ``day_diary`` builds — so the attribute the
    reader reads is one the writer wrote, and a drift in the literal shows up as
    a body that reads as the wrong kind rather than as two tests disagreeing
    about a string.

    Both rows matter and they are not symmetric: the authored row is what keeps
    the marking from creeping onto honest entries, and the substituted row is
    the requirement itself.
    """
    doc = _render({"p-ava": [record]})
    body = record["text"]

    kinds = {text: kind for text, kind in tokenize_transcript(doc)}
    assert body in kinds, (
        f"the {label} body did not survive to the tokenizer as a span of its "
        f"own: {_diary_lines(doc)!r}"
    )
    assert kinds[body] == expected_kind
    # ...and never as the other kind, so the two rows cannot both pass on a
    # reader that returned one kind for every diary.
    assert kinds[body] != (
        KIND_DIARY if expected_kind == KIND_DIARY_SUBSTITUTED else KIND_DIARY_SUBSTITUTED
    )


# ===========================================================================
# 3. The reader: the body kind, both directions
# ===========================================================================
#
# Hand-written tags from here down, small enough to check by eye, following the
# idiom of `test_transcript_highlight`'s synthetic half. `_MARKED_ELEMENT` and
# `_AUTHORED_ELEMENT` differ by the flag and by nothing else, which is what
# makes the span comparisons between them mean something.

_BODY = "Nothing much more to set down tonight."
_AUTHORED_ELEMENT = f'<diary player="Alice" day="2">{_BODY}</diary>'
_MARKED_ELEMENT = (
    f'<diary player="Alice" day="2" {_SUBSTITUTED_ATTRIBUTE}>{_BODY}</diary>'
)


@pytest.mark.parametrize(
    ("source", "expected_kind", "forbidden_kind"),
    [
        pytest.param(
            _MARKED_ELEMENT, KIND_DIARY_SUBSTITUTED, KIND_DIARY, id="marked"
        ),
        pytest.param(
            _AUTHORED_ELEMENT, KIND_DIARY, KIND_DIARY_SUBSTITUTED, id="authored"
        ),
    ],
)
def test_the_body_kind_follows_the_flag_in_both_directions(
    source: str, expected_kind: str, forbidden_kind: str
) -> None:
    """The body axis of the marking, and BOTH directions of it.

    Spec 041 re-keyed ``_INLINE_CONTENT_KINDS`` from ``tag`` to
    ``(tag, flag)``, so a ``<diary>`` body is no longer a function of its tag
    NAME alone. Both rows are needed because either alone is passable by a
    constant: a reader hard-wired to ``diary-substituted`` passes the marked
    row, and one that never learned the flag passes the authored row.

    The forbidden-kind assertion is what makes each row exclusive rather than
    merely satisfied.
    """
    kinds = {kind for _, kind in tokenize_transcript(source)}

    assert expected_kind in kinds
    assert forbidden_kind not in kinds


def test_the_flag_splits_the_tag_head_and_nothing_else() -> None:
    """What the flag DOES split, stated in the direction it actually goes.

    Easy to write backwards, so it is written out: the flag ADDS TWO SPANS TO
    THE TAG HEAD, by design. Seven spans become nine — the marker gains
    ``" substituted="`` and the value ``true`` lifts out as an ordinary bold
    ``attr`` span. That is the TAG-LINE AXIS of the marking, and it is half of
    the belt-and-braces the design asks for: a terminal without SGR 9 still
    shows the flag in bold on the tag even though it cannot strike the body. A
    test asserting an unchanged tag head would assert the opposite of the
    intent.

    What must NOT split is the BODY. It stays exactly ONE span in both shapes,
    changing only its kind — so a reviewer's eye lands on one struck paragraph
    rather than on a body diced at the flag's boundary — and the closing tag
    stays ``marker``.

    Pinned as full span lists in both shapes, because that is the one form in
    which "two more spans, in the head, and the body is still one" is a single
    readable statement.
    """
    authored = tokenize_transcript(_AUTHORED_ELEMENT)
    marked = tokenize_transcript(_MARKED_ELEMENT)

    assert authored == [
        ('<diary player="', KIND_MARKER),
        ("Alice", KIND_ATTR),
        ('" day="', KIND_MARKER),
        ("2", KIND_ATTR),
        ('">', KIND_MARKER),
        (_BODY, KIND_DIARY),
        ("</diary>", KIND_MARKER),
    ]
    assert marked == [
        ('<diary player="', KIND_MARKER),
        ("Alice", KIND_ATTR),
        ('" day="', KIND_MARKER),
        ("2", KIND_ATTR),
        ('" substituted="', KIND_MARKER),
        (_SUBSTITUTED_VALUE, KIND_ATTR),
        ('">', KIND_MARKER),
        (_BODY, KIND_DIARY_SUBSTITUTED),
        ("</diary>", KIND_MARKER),
    ]

    # Restated as the relation, so a regression reports WHICH rule broke.
    assert len(authored) == 7
    assert len(marked) == 9, "the tag-line axis of the marking is gone"
    assert [text for text, kind in marked if kind == KIND_ATTR] == [
        "Alice",
        "2",
        _SUBSTITUTED_VALUE,
    ]
    # The body: one span in each, same text, different kind — never diced.
    assert [text for text, _ in marked if text == _BODY] == [_BODY]
    assert [text for text, _ in authored if text == _BODY] == [_BODY]
    assert marked[-1] == authored[-1] == ("</diary>", KIND_MARKER)
    # And the round trip both ways, which is the guarantee the whole tokenizer
    # owes and the one a re-split could break silently.
    assert "".join(text for text, _ in marked) == _MARKED_ELEMENT
    assert "".join(text for text, _ in authored) == _AUTHORED_ELEMENT


def test_no_line_but_the_marked_diarys_own_tokenizes_differently() -> None:
    """The flag is read PER LINE: one marked diary moves one line and no other.

    The element-level test above cannot see this. ``tokenize_transcript`` makes
    two WHOLE-FILE passes before the per-line chain runs (the cast side map and
    the human seat), so a flag read at the wrong level — remembered across
    lines, hoisted to a file-wide fact, keyed on the tag name instead of the
    pair — would re-kind every diary in the file, or every ``<thought>`` after
    the marked line, and the element-level pin would still pass.

    Measured by regrouping both documents' spans into lines and asserting that
    exactly ONE index differs. Ava's authored diary sits on the very next line
    after Ben's marked one, so the assertion covers the neighbour case as well
    as the distant one.
    """
    authored = _spans_by_line(_render(_authored_diaries()))
    one_marked = _spans_by_line(_render(_one_marked_diaries()))

    assert len(one_marked) == len(authored)
    differing = [
        index
        for index, (before, after) in enumerate(zip(authored, one_marked, strict=True))
        if before != after
    ]

    # Exactly one line moved, and it is Ben's diary.
    assert len(differing) == 1, (
        "marking one diary changed "
        f"{len(differing)} lines; the flag must be read per line, off the tag "
        "whose body it re-kinds"
    )
    moved = "".join(text for text, _ in one_marked[differing[0]])
    assert moved == (
        f'<diary player="Ben" day="2" {_SUBSTITUTED_ATTRIBUTE}>{_BEN_ENTRY}</diary>'
    )

    # ...and Ava's diary, one line away, is untouched in both kind and shape.
    ava_line = next(
        line
        for line in one_marked
        if "".join(text for text, _ in line).startswith('<diary player="Ava"')
    )
    assert [kind for _, kind in ava_line][-2:] == [KIND_DIARY, KIND_MARKER]
    assert KIND_DIARY_SUBSTITUTED not in {kind for _, kind in ava_line}


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("false", id="false"),
        pytest.param("", id="empty"),
        pytest.param("1", id="one"),
        pytest.param("True", id="capitalised"),
        pytest.param("TRUE", id="uppercase"),
        pytest.param("yes", id="yes"),
    ],
)
def test_a_hand_edited_flag_value_reads_as_authored(value: str) -> None:
    """The READER's defence — and at this boundary there is no bool to be non-.

    A committed transcript is a text file: it holds strings and nothing else, so
    the reader's defence is not "reject a non-bool" but ``value != "true"``. The
    cases that matter are therefore the plausible hand-EDITS, and the sharpest
    of them is ``"True"`` with a capital T — the spelling a Python programmer
    types by reflex, which no equality against ``"true"`` will honour and which
    a ``.lower()`` anywhere in the reader would.

    ``"false"`` is the case that would bite hardest under a truthiness test: a
    non-empty string is truthy, so a coercing reader would strike the body of an
    entry the file has just declared authored.

    Reading an unrecognised value as AUTHORED is the honest answer, not a
    conservative one: the file has not told us the entry was substituted, and
    claiming it was would put words in a reviewer's mouth about a player's own
    writing. The mirror of the writer emitting nothing for anything but ``True``
    (:func:`test_only_the_literal_true_yields_the_attribute`).

    The positive control is inline rather than implied — ``"true"`` really does
    re-kind the body — so a tokenizer that had stopped reading the flag
    altogether fails this test instead of passing every row of it.
    """
    source = f'<diary player="Alice" day="2" substituted="{value}">{_BODY}</diary>'
    kinds = {text: kind for text, kind in tokenize_transcript(source)}

    assert kinds[_BODY] == KIND_DIARY, (
        f'substituted="{value}" struck the body — only the literal '
        f'"{_SUBSTITUTED_VALUE}" may'
    )
    assert eval_ledger._is_substituted(f' substituted="{value}"') is False
    # The positive control, in the same test, on the same shape.
    positive = source.replace(f'substituted="{value}"', _SUBSTITUTED_ATTRIBUTE)
    positive_kinds = {text: kind for text, kind in tokenize_transcript(positive)}
    assert positive_kinds[_BODY] == KIND_DIARY_SUBSTITUTED
    # ...and the value is still lifted into its own span whatever it says, so a
    # reviewer sees the hand-edit rather than having it hidden inside the
    # marker. (An empty value contributes no span at all — the tokenizer's
    # no-empty-span guarantee — so there is nothing to lift there.)
    if value:
        assert kinds[value] == KIND_ATTR


def test_the_flag_re_kinds_only_a_body_that_has_a_marked_kind_registered() -> None:
    """A flag on a tag with no marked kind leaves that body's own kind alone.

    ``_INLINE_CONTENT_KINDS`` is keyed by ``(tag, flag)`` and the lookup falls
    back to ``(tag, False)`` — "a flag may only RE-KIND a body, never demote
    one". Slice 8 registers the pair for ``diary`` only, so a flag typed onto a
    ``<recap>`` (a moderator's posted fact, which no slice will ever mark: the
    moderator has no model call to substitute for) must leave its body
    ``recap`` rather than dropping it to ``plain``.

    Without the fallback the same lookup would return ``None`` and the body
    would land on ``KIND_PLAIN``, so a stray attribute in a hand-edited file
    would silently un-style a whole element — degradation by attribute, which
    is the failure mode the chained ``.get`` exists to prevent.
    """
    source = f'<recap substituted="true">{_BODY}</recap>'
    kinds = {text: kind for text, kind in tokenize_transcript(source)}

    assert kinds[_BODY] == KIND_RECAP
    assert eval_ledger.KIND_PLAIN not in set(kinds.values())
    # The registration this rests on, pinned in one place: the pair exists for
    # `diary` and for nothing else in Slice 8. Slices 9 and 10 add their own
    # rows here, and the assertion says which shape they take.
    assert {
        tag for tag, flag in eval_ledger._INLINE_CONTENT_KINDS if flag
    } == {"diary"}, (
        "a tag other than `diary` has a marked body kind registered. That is "
        "what Slices 9 and 10 legitimately do (`thought`, then speech) — so "
        "add the tag here AND give it a `(tag, True)` row of coverage above; "
        "do not simply widen this set, because the fallback assertion in this "
        "test is what keeps an UNregistered flag from demoting a body to plain."
    )


# ===========================================================================
# 4. The whole chain: the node writes what the reader strikes
# ===========================================================================


class _FailingTier:
    """A tier stand-in whose every ``invoke`` raises — forces the substitution.

    The same shape ``tests/test_substitution_counts.py`` uses, and raising on
    EVERY invocation rather than from a queue is what makes it independent of
    how many times ``_ai_diary`` retries.
    """

    def __init__(self) -> None:
        self.invocations = 0

    def with_structured_output(self, schema: type, **kwargs: Any) -> "_FailingTier":
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        raise RuntimeError("diary model down")


def _hinge_state() -> dict[str, Any]:
    """A Day→Night hinge state with one surviving AI player."""
    return {
        "cycle": 2,
        "phase": "day",
        "players": {
            "p-ava": _player("p-ava", "Ava", "law_abiding"),
            "p-hugo": PlayerState(
                id="p-hugo",
                name="Hugo",
                role="mafia",  # type: ignore[arg-type]
                is_human=True,
                is_alive=True,
                persona=None,
            ),
        },
        "day_turn_index": 0,
        "day_rounds": 1,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": {},
    }


def test_the_node_marks_what_it_substituted_and_the_reader_strikes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only test in the module where the whole chain runs on real data.

    Every other test hands the writer a hand-built ``DiaryRecord`` or hands the
    reader a hand-written tag. This drives the REAL ``day_diary`` against a tier
    that cannot answer, takes the delta it returns, renders it through the real
    ``render_transcript`` and tokenizes the result — so the marking travels the
    whole distance from the code that knows it substituted to the span a
    reviewer's terminal strikes, with no literal shared between the ends but the
    body text the node itself chose.

    It is the test that would catch a chain broken at any single joint while
    every layer's own tests stayed green: a node that stopped setting the key, a
    ``_SubstitutionMark`` never read, a renderer that omitted the attribute, a
    reader that ignored it.

    The premise is asserted first — the model really did fail and the node
    really did substitute — because a chain test whose first link never fired
    would be measuring an authored entry and would pass the moment the marking
    was deleted from all three layers at once.
    """
    tier = _FailingTier()
    monkeypatch.setattr(day_nodes, "get_large", lambda: tier)

    delta = day_nodes.day_diary(_hinge_state())  # type: ignore[arg-type]
    record = delta["private_diaries"]["p-ava"][0]

    # The premise: the model was reached, it failed, and the node substituted.
    assert tier.invocations >= 1, "the failing tier was never invoked"
    assert record["text"] == day_nodes._DIARY_FALLBACK
    assert record["substituted"] is True, (
        "the node substituted its deterministic stand-in and did not carry the "
        "fact out on the record, so nothing downstream can mark it"
    )

    doc = render_transcript(
        _day_events({"p-ava": [record]}),
        _transcript_players(),
        game_index=1,
        run_meta=None,
    )

    # The writer marked it...
    assert _diary_lines(doc) == [
        f'<diary player="Ava" day="2" {_SUBSTITUTED_ATTRIBUTE}>'
        f"{day_nodes._DIARY_FALLBACK}</diary>"
    ]
    # ...and the reader strikes exactly that body and nothing else.
    spans = tokenize_transcript(doc)
    struck = [text for text, kind in spans if kind == KIND_DIARY_SUBSTITUTED]
    assert struck == [day_nodes._DIARY_FALLBACK]
    assert KIND_DIARY not in {kind for _, kind in spans}, (
        "an honest `diary` span survives in a document whose only entry was "
        "substituted"
    )
    # The round trip still holds over the whole document, which is the promise
    # every kind added to the tokenizer must leave intact.
    assert "".join(text for text, _ in spans) == doc
