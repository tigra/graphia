"""A substituted body is visibly marked — spec 041, Slices 8, 9 and 10.

Functional-spec 041's fourth requirement, delivered end-to-end: *a reviewer
reading a preserved game can see that a body was not the player's writing.*
Three layers had to agree for that to be true, and this module is where they
are held to it.

    ``nodes/day.day_diary``            writes ``DiaryRecord["substituted"]``,
                                       and ONLY on the substitution branch
    ``tools/eval_transcript``          emits ``substituted="true"``,
                                       and ONLY for the literal ``True``
    ``eval_ledger.tokenize_transcript``reads it back, and ONLY off ``"true"``

ALL THREE SUBSTITUTION CHANNELS LIVE HERE NOW. Sections 1-4 are Slice 8's
diary (Task 8.5); section 5 is Slice 9's THOUGHT (Task 9.4), the same three
layers one channel over with a fourth joint in front of them
(``_ai_reflect``'s ``_SubstitutionMark``, stamped inside a fan-out); section 6
is the claim neither of those can make alone — that the two markings stay
tellable apart from ONE ANOTHER, not merely each from its own honest twin;
section 7 is Slice 10's DAY SPEECH (Task 10.3), the last channel and the only
PUBLIC one; and section 8 is section 6's claim at full width, over all three.
One module rather than one per channel because the layers, the literal and the
reader are SHARED: the writer's value test is literally one function for all
three channels, so a per-channel module would assert it three times and would
still not hold the cross-channel claim, which needs all three in a single
document.

**WHAT MAKES SECTION 7 DIFFERENT IN KIND, AND NOT MERELY THIRD.** The two
private channels are marked by adding an attribute to a tag that already
existed. Day speech is written as a bare ``Speaker: text`` line with no tag at
all, so a substituted line is **promoted to an element** and an authored one is
left byte-for-byte as it was. Three consequences run through section 7 and
belong here rather than being rediscovered in each test:

* the marking is **conditional on the flag**, so this channel's byte-identity
  claim is about a LINE SHAPE and not about an attribute — and the shape it
  protects is the dominant line of the whole format;
* the two halves of the pair are therefore **different shapes**, which is why
  "both directions" cannot mean here what it means for the private channels.
  ``("speech", False)`` is deliberately unregistered — the writer never emits an
  unflagged ``<speech>`` tag — so a hand-edited unflagged tag reads
  :data:`~graphia.eval_ledger.KIND_PLAIN`, and the honest twin to compare
  against is the bare line;
* and a substituted line **can never be a human-seat kind**. The flag is
  written only by ``_ai_day_action``, which the human branch never calls, so
  that combination is unreachable by CONSTRUCTION rather than by omission —
  asserted in section 7 rather than worked around silently, because an
  unreachable combination that nobody states looks exactly like an untested one.

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

* **The record's shape** — one guard per channel, both one layer UPSTREAM of
  everything here, before any transcript is rendered:
  ``test_slice39_diary_before_night.test_record_carries_exactly_day_cursor_and_text``
  for the diary and
  ``test_slice28_private_thoughts.test_an_authored_thought_record_carries_exactly_text``
  for the thought, each asserting that an authored record carries exactly its
  pre-041 keys with a message naming the unconditional-flag error. This
  module's :func:`test_an_authored_document_carries_no_marking_anywhere` and
  :func:`test_an_authored_thought_document_carries_no_marking_whatever_the_shape`
  catch the same class of error at the transcript layer, and the two altitudes
  fail with different messages. Both are worth keeping, and Slice 8 proved it
  by mutation rather than by argument: a renderer mutation turned 24
  transcript tests red with the record guard silent, and a NODE mutation
  turned the record guard red with this whole module green. Sharper still on
  the thought channel — an unconditional ``substituted: False`` leaves every
  rendered document byte-identical, because the writer emits the attribute for
  the literal ``True`` alone, so the record guard is the ONLY thing that sees
  it.
* **The attribute's span kind** — ``test_transcript_highlight``'s
  ``_ATTR_NAME_CASES`` sweep pins that ``substituted``'s value is lifted into
  its own achromatic ``attr`` span, and ``_ACHROMATIC_ATTR_KEYS`` pins that it
  never takes a side. Not repeated here.
* **The SIDE on a marked speech line's speaker name** (spec 041 Slice 10) —
  ``test_transcript_highlight``'s
  ``test_a_substituted_speech_lines_speaker_in_the_cast_list_takes_their_side``
  and the comparison beside it, where the ``<setup>`` cast list that grants a
  side lives. It is the assertion that protects Task 10.2's decision to move
  the side off the marked BODY and onto the wrapping tag's ``speaker="…"``
  name, and it belongs there rather than here for the reason the whole side
  axis does: a side is a fact this module's fixtures happen to carry and that
  module's fixtures are BUILT around. Section 7 below asserts the other half —
  that the marked body carries no side of its own.
* **The body's colour and text style** —
  ``test_ledger_viewer``'s three ``…_style_is_the_…_rule_plus_strike`` tests,
  next to that file's ``App.run_test()`` harness. This module is terminal-free,
  which is why sections 6 and 8's distinguishability claims are made at the
  KIND level: distinct kinds, plus that file's own completeness equality
  (``_TRANSCRIPT_KIND_COMPONENTS`` == ``TRANSCRIPT_KINDS`` less ``plain``),
  is what guarantees a separate component class per kind and therefore a rule
  per kind.
* **The reshaped thought ELEMENT** — ``str | ThoughtRecord``, the seam
  ``graphia.state.thought_text``, the mixed accumulated list and the two
  prompt renderers' shape-agnosticism are spec 028's and spec 039's, in
  ``test_slice28_private_thoughts`` and ``test_slice39_diary_interleave``.
  Nothing about the marking depends on them, and the marking is deliberately
  NOT rendered into either prompt (a prompt-visible mark would change what the
  player reasons from, which is a game-design change and not this spec's).
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
  written backwards — see :func:`test_the_flag_splits_the_tag_head_and_nothing_else`;
* and on the speech channel (section 7), where the absence assertion is about a
  LINE SHAPE rather than an attribute, the bare-shape test asserts the spoken
  line is really in the document before asserting no ``<speech>`` tag is, the
  "changes nothing else" test asserts the two documents DIFFER and then
  reconciles them by an exact line-for-line substitution, and the
  unflagged-tag test carries the marked element as its own inline positive
  control. Each was confirmed by mutation rather than by argument: an
  unconditional wrap in ``_append_messages`` turns 30 tests in this module red,
  including every one of those.

Strictly offline: no model, no network, no terminal. THREE tests drive a real
node with a failing tier stand-in — ``day_diary`` in section 4,
``day_round_reflect`` in section 5 and ``day_turn`` in section 7 — which is why
``monkeypatch``ing ``get_large`` appears at all; the autouse ``safe_llm`` net
covers every other call site, and the autouse ``reset_substitution_sink``
fixture in ``conftest`` owns the sink those nodes' substitutions book.
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
    KIND_PLAIN,
    KIND_RECAP,
    KIND_SPEAKER_LAW_ABIDING,
    KIND_SPEECH,
    KIND_SPEECH_LAW_ABIDING,
    KIND_SPEECH_SUBSTITUTED,
    KIND_THOUGHT,
    KIND_THOUGHT_SUBSTITUTED,
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


# SLICE 10'S SPEECH BODIES. Ava's is the line the pre-Slice-10 builder has
# always emitted and is left spelled exactly as it was; Ben's is the second
# speaker section 7 needs, because the marked speaker has to be able to be the
# `role="Mafia"` one (his name inside the tag is then `attr-mafia`, the more
# decorated shape, exactly as his diary and thought tags are).
#
# Distinct sentences from every diary and thought body in this module, for the
# reason `_AVA_THOUGHT`'s comment records in full: sections 6 and 8 look each
# body's kind up BY ITS TEXT, so two bodies sharing a sentence would collapse
# into one entry of that map and the distinguishability assertion would compare
# a channel against itself.
_AVA_LINE = "I heard nothing."
_BEN_LINE = "Nor did I, and I was listening for it."


def _speech(name: str, text: str, *, substituted: Any = None) -> AIMessage:
    """One Day-speech ``AIMessage`` in the exact shape ``day_turn`` builds.

    Both of production's keys, and the asymmetry between them is the point:
    ``day_turn``'s AI branch puts the speaker's display name in ``name=`` AND
    in ``additional_kwargs["speaker"]`` (always in agreement), and the flag in
    the kwargs **alone** — where ``_append_messages`` reads it, taking the name
    off ``msg.name``.

    Tri-state for the same reason :func:`_record` and :func:`_thought` are:
    ``substituted=None`` means the key is ABSENT, which is what an authored
    line looks like — never ``False``. Any other value is a hand-edit, a
    reader-boundary shape the node cannot produce, and is available as such.
    """
    kwargs: dict[str, Any] = {"speaker": name}
    if substituted is not None:
        kwargs["substituted"] = substituted
    return AIMessage(content=text, name=name, additional_kwargs=kwargs)


def _day_events(
    diaries: dict[str, list[dict[str, Any]]] | None,
    *,
    thoughts: dict[str, list[Any]] | None = None,
    speeches: list[AIMessage] | None = None,
) -> list[dict[str, Any]]:
    """A one-round Day whose trailer carries ``diaries`` (or no diary delta).

    Shaped from the real engine's stream: ``day_open`` → a ``day_turn`` that
    wraps the round → (Slice 9) ``day_round_reflect`` → ``day_close`` →
    ``day_diary``. The diary delta arrives while the ``<day>`` section is still
    open, which is what puts the elements in the day-level trailer rather than
    inside the last ``<round>``; a thought delta lands INSIDE the round, which
    is the structural difference between the two channels and the reason the
    same builder can carry both.

    ``thoughts`` is keyword-only and defaults to no delta at all, so every
    Slice-8 caller passing ``diaries`` positionally is untouched — the shared
    builder is what lets Slice 9's distinguishability tests put a marked
    thought and a marked entry in ONE document rendered by the same code path.

    ``speeches`` (Slice 10) replaces the ``day_turn`` delta's messages, and it
    defaults to the ONE bare Ava line this builder has emitted since Slice 8 —
    spelled without ``additional_kwargs`` at all, exactly as it was, so no
    existing caller's document moves by a byte. Passing a list is what lets
    section 7 mark a line, hand-edit its flag, or put a marked line and an
    authored one in the same round; the default's *rendered* output is
    identical to ``[_speech("Ava", _AVA_LINE)]``'s, which is itself an
    assertion in
    :func:`test_an_authored_line_keeps_the_bare_shape_byte_for_byte` — the
    writer takes the name off ``msg.name`` and never off the kwargs.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 2 breaks.")]}},
        {
            "day_turn": {
                "messages": (
                    [AIMessage(content=_AVA_LINE, name="Ava")]
                    if speeches is None
                    else list(speeches)
                ),
                "day_rounds": 1,
            }
        },
    ]
    if thoughts is not None:
        events.append({"day_round_reflect": {"private_thoughts": thoughts}})
    events.append(
        {"day_close": {"messages": [SystemMessage(content="The Day ends.")]}}
    )
    if diaries is not None:
        events.append({"day_diary": {"private_diaries": diaries}})
    return events


def _render(
    diaries: dict[str, list[dict[str, Any]]] | None,
    *,
    thoughts: dict[str, list[Any]] | None = None,
    speeches: list[AIMessage] | None = None,
) -> str:
    """Render the fixture Day through the real production renderer."""
    return render_transcript(
        _day_events(diaries, thoughts=thoughts, speeches=speeches),
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
        "`eval_transcript._substituted_attr`."
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
    assert eval_transcript._substituted_attr(value) == expected


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
    written = eval_transcript._substituted_attr(True)

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

# SLICE 9'S PAIR, in the same hand-written form and beside the diary's on
# purpose: ``_INLINE_CONTENT_KINDS`` is keyed by ``(tag, flag)``, so "which
# tags have a marked body kind registered" is a table with one row per channel
# and the coverage below is that table read back. The bodies differ from the
# diary's so a row that came back with the wrong kind also comes back with the
# wrong text.
_THOUGHT_BODY = "I'll keep watching and weigh what I've heard so far."
_AUTHORED_THOUGHT_ELEMENT = f'<thought player="Alice">{_THOUGHT_BODY}</thought>'
_MARKED_THOUGHT_ELEMENT = (
    f'<thought player="Alice" {_SUBSTITUTED_ATTRIBUTE}>{_THOUGHT_BODY}</thought>'
)

# SLICE 10'S — AND IT IS A SINGLETON RATHER THAN A PAIR, WHICH IS THE ONE
# STRUCTURAL DIFFERENCE BETWEEN THIS CHANNEL AND THE TWO ABOVE.
#
# ``_INLINE_CONTENT_KINDS`` registers ``("speech", True)`` and deliberately does
# NOT register ``("speech", False)``, because the writer never emits an
# unflagged ``<speech>`` tag: an authored Day-speech line stays the bare
# ``Speaker: text`` shape and never becomes an element at all. So there is no
# ``_AUTHORED_SPEECH_ELEMENT`` to write, and inventing one would be inventing a
# document the writer cannot produce.
#
# The two honest directions this channel DOES have are each pinned in section
# 7, where they belong:
#
#   * the bare line — :func:`test_an_authored_line_keeps_the_bare_shape_byte_for_byte`
#     for the writer and
#     :func:`test_the_authored_twin_is_the_bare_line_and_carries_its_side` for
#     the reader. That is the real "other direction" for this channel;
#   * a hand-written unflagged ``<speech>`` tag —
#     :func:`test_a_hand_edited_unflagged_speech_tag_reads_plain_not_speech`,
#     which pins ``KIND_PLAIN`` rather than :data:`KIND_SPEECH`. Written out
#     because a test authored by analogy with the two channels above would
#     expect the honest kind, get ``plain``, and read as a bug: registering
#     :data:`KIND_SPEECH` there would claim the *side-unknown* kind for a
#     hand-edited line whose bare twin reads TINTED, which is a wrong answer
#     where plain is honest.
#
# The body is the day-action fallback text and the speaker is Alice, matching
# the two elements above so the three hand-written shapes differ only where the
# channel does.
_SPEECH_BODY = "I'm not sure who to trust yet."
_MARKED_SPEECH_ELEMENT = (
    f'<speech speaker="Alice" {_SUBSTITUTED_ATTRIBUTE}>{_SPEECH_BODY}</speech>'
)
_UNFLAGGED_SPEECH_ELEMENT = f'<speech speaker="Alice">{_SPEECH_BODY}</speech>'


@pytest.mark.parametrize(
    ("source", "expected_kind", "forbidden_kind"),
    [
        pytest.param(
            _MARKED_ELEMENT, KIND_DIARY_SUBSTITUTED, KIND_DIARY, id="marked-diary"
        ),
        pytest.param(
            _AUTHORED_ELEMENT,
            KIND_DIARY,
            KIND_DIARY_SUBSTITUTED,
            id="authored-diary",
        ),
        # SLICE 9'S ROWS — the `("thought", True)` coverage the registration
        # assertion two tests down explicitly demands rather than a widened
        # set. Task 9.3 registered the pair and styled it; these are the rows
        # that make the registration mean something.
        pytest.param(
            _MARKED_THOUGHT_ELEMENT,
            KIND_THOUGHT_SUBSTITUTED,
            KIND_THOUGHT,
            id="marked-thought",
        ),
        pytest.param(
            _AUTHORED_THOUGHT_ELEMENT,
            KIND_THOUGHT,
            KIND_THOUGHT_SUBSTITUTED,
            id="authored-thought",
        ),
        # SLICE 10'S ROW — the `("speech", True)` coverage the registration
        # assertion two tests down demands by name, landing one task after Task
        # 10.2 widened that set, exactly as `("thought", True)`'s row landed
        # one task after Task 9.3 widened it. ONE row, not two: see
        # `_MARKED_SPEECH_ELEMENT` for why there is no authored twin to pair it
        # with, and section 7 for where this channel's two honest directions
        # are pinned instead.
        pytest.param(
            _MARKED_SPEECH_ELEMENT,
            KIND_SPEECH_SUBSTITUTED,
            KIND_SPEECH,
            id="marked-speech",
        ),
    ],
)
def test_the_body_kind_follows_the_flag_in_both_directions(
    source: str, expected_kind: str, forbidden_kind: str
) -> None:
    """The body axis of the marking, on every marked channel.

    Spec 041 re-keyed ``_INLINE_CONTENT_KINDS`` from ``tag`` to
    ``(tag, flag)``, so a ``<diary>``, ``<thought>`` or ``<speech>`` body is no
    longer a function of its tag NAME alone. Both directions are needed on the
    two PRIVATE channels because either alone is passable by a constant: a
    reader hard-wired to ``diary-substituted`` passes the marked row, and one
    that never learned the flag passes the authored row.

    **AND ON SPEECH THERE IS ONE ROW, WHICH IS A FACT ABOUT THE FORMAT AND NOT
    A GAP.** ``("speech", False)`` is deliberately unregistered: the writer
    never emits an unflagged ``<speech>`` tag, because an authored Day-speech
    line stays the bare ``Speaker: text`` shape and is never promoted to an
    element at all. So this channel has no same-shape twin to compare against,
    and its two honest directions are pinned in section 7 instead — the bare
    line (:func:`test_the_authored_twin_is_the_bare_line_and_carries_its_side`)
    and a hand-written unflagged tag, which reads ``plain``
    (:func:`test_a_hand_edited_unflagged_speech_tag_reads_plain_not_speech`).
    Between them those two close the same hole a second row would: a reader
    hard-wired to ``speech-substituted`` fails the second of them.

    THE CHANNELS ARE SEPARATE ROWS AND NOT A SAMPLE OF ONE RULE. The lookup is
    a table, so ``thought`` being registered is not implied by ``diary`` being
    registered — and the marked kinds are DIFFERENT kinds by design
    (functional-spec 041's fourth requirement wants a reviewer to see which
    channel a stand-in landed in), which is what makes each row's
    forbidden-kind assertion a statement about its own channel. The
    cross-channel half of that claim — that the marks stay tellable apart
    inside ONE document — is sections 6 and 8's.

    The forbidden-kind assertion is what makes each row exclusive rather than
    merely satisfied. On the speech row it is the NEUTRAL ``speech`` kind, which
    is the wrong answer that was actually on the table: registering it for the
    unflagged pair was considered and rejected, and a reader that gave a marked
    body its honest kind would be that mistake one step further on.
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

    **Slice 9 registered the second pair, and this test's subject is unchanged
    by that** — which is the point of having picked ``<recap>`` as the probe
    rather than ``<thought>``. The moderator has no model call to substitute
    for, so no slice will ever register ``recap``, and the fallback therefore
    stays exercised on a genuinely unregistered tag for the life of the spec.
    The registration set below widened to two; the assertion it protects did
    not move.
    """
    source = f'<recap substituted="true">{_BODY}</recap>'
    kinds = {text: kind for text, kind in tokenize_transcript(source)}

    assert kinds[_BODY] == KIND_RECAP
    assert eval_ledger.KIND_PLAIN not in set(kinds.values())
    # The registration this rests on, pinned in one place: the pair exists for
    # `diary` (Slice 8) and `thought` (Slice 9), and for nothing else. Slice 10
    # adds its own row, and the assertion says which shape it takes.
    #
    # WIDENED, NOT LOOSENED, and the distinction is the one the failure message
    # below insists on: the probe above is still an UNregistered tag, so the
    # fallback guarantee is still under test at full strength. What this line
    # tracks is a different thing — which tags have *claimed* a marked kind — so
    # a slice that registers one without covering it is still caught, by the
    # `(tag, True)` coverage rows this module owns.
    #
    # ALL THREE ROWS ARE NOW COVERED, AND THE TWO-STEP IS CLOSED FOR THE LAST
    # TIME. `("diary", True)` has had its pair of directions since Slice 8;
    # `("thought", True)` got its own two rows in
    # `test_the_body_kind_follows_the_flag_in_both_directions` above (Task
    # 9.4), one task after Task 9.3 widened this set; and `("speech", True)`
    # got its row there too (Task 10.3), one task after Task 10.2 widened it.
    # Three registrations, three one-task lags, three coverage rows landing in
    # the very next task — which is the pattern this failure message asks for
    # rather than an exception to it. `speech` is the LAST channel the spec
    # marks, so there is no fourth lag to expect.
    #
    # The probe above is STILL an unregistered tag, so the fallback guarantee
    # is untouched at full strength — `<recap>` remains the right probe and no
    # slice will ever register it.
    #
    # ONE ASYMMETRY WORTH KNOWING, because it is the first: `speech` is
    # registered for the FLAGGED form only — there is no `("speech", False)`
    # entry, unlike `diary` and `thought`, which have both. The writer never
    # emits an unflagged `<speech>` tag (an authored line stays the bare
    # `Speaker: text` shape), so a hand-written `<speech speaker="Ava">…` body
    # lands on `KIND_PLAIN` rather than on a speech kind. That is deliberate:
    # `KIND_SPEECH` is the *side-unknown* form and this lookup cannot consult
    # the cast list, so claiming it would read achromatic where the bare line
    # it imitates reads tinted. It does not weaken the rule this test protects
    # — nothing is demoted, because there was never an unflagged kind for the
    # flag to take away. That `plain` landing is asserted rather than merely
    # described, in
    # `test_a_hand_edited_unflagged_speech_tag_reads_plain_not_speech`
    # (section 7): this is the first WHITELISTED tag for which the chained
    # fallback's final hop is reachable at all, so it is worth a test and not
    # only a paragraph.
    assert {
        tag for tag, flag in eval_ledger._INLINE_CONTENT_KINDS if flag
    } == {"diary", "thought", "speech"}, (
        "a tag other than `diary` / `thought` / `speech` has a marked body "
        "kind registered — so add the tag here AND give it a `(tag, True)` row "
        "of coverage above; do not simply widen this set, because the fallback "
        "assertion in this test is what keeps an UNregistered flag from "
        "demoting a body to plain. Note that `speech` was the LAST channel the "
        "spec marks: `_ai_day_action`, `_ai_reflect` and `day_diary` are the "
        "three substitution sites that produce reader-visible prose."
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


# ===========================================================================
# 5. The SECOND marked channel: a substituted thought (Slice 9)
# ===========================================================================
#
# The same three layers, one channel over:
#
#     ``nodes/day._ai_reflect``          stamps the ``_SubstitutionMark``, and
#                                        ONLY on its substitution branch
#     ``nodes/day.day_round_reflect``    writes ``ThoughtRecord["substituted"]``,
#                                        and ONLY when the mark is set
#     ``tools/eval_transcript``          emits ``substituted="true"`` on
#                                        ``<thought>``, ONLY for the literal
#                                        ``True``
#     ``eval_ledger.tokenize_transcript``re-kinds the body, ONLY off ``"true"``
#
# WHAT IS SHARED WITH THE DIARY AND THEREFORE NOT RETESTED HERE. The writer's
# value test is ONE function — ``eval_transcript._substituted_attr``, which as
# of Task 10.3 is named for what it does rather than for its first caller (the
# ``_diary_`` prefix Slice 9 wrote around is gone, on the condition Slice 9 set:
# a third caller and a task that edits this file anyway) — so section 1's
# eleven-row identity sweep and section 2's writer/reader literal agreement
# cover all three channels by construction. Duplicating them per channel would
# assert the same function three times.
#
# WHAT IS GENUINELY NEW AND HAS TO BE TESTED HERE:
#
#   * ``_append_thoughts`` reads the flag off the RAW entry behind an
#     ``isinstance(note, dict)`` hop, because ``ThoughtEntry`` is the union
#     ``str | ThoughtRecord`` — a shape the diary channel does not have. A bare
#     ``str`` has nowhere to carry a flag and is authored by construction;
#   * the byte-identity stake is an order of magnitude larger. The preserved
#     corpus holds 10,706 thought bodies against 376 diary ones, so an
#     unconditional attribute here would mark almost every private line in
#     every committed transcript;
#   * a ``<thought>`` tag has ONE attribute before the flag where a ``<diary>``
#     has two, so the flag lands at a different position in the head and the
#     tag-line axis is a different span shape;
#   * the WRITER is a different node reached by a different route
#     (``_SubstitutionMark`` through a fan-out, one mark per reflector).

# DISTINCT FROM `_AVA_ENTRY` / `_BEN_ENTRY` BY DESIGN, and the reason is not
# tidiness. Section 6 looks each body's kind up BY ITS TEXT — the only form in
# which a failure can name which body took which kind — so two bodies sharing a
# sentence would silently collapse into one entry of that map and the
# distinguishability assertion would compare a channel against itself. (Written
# down because the first draft of this fixture reused the diary's sentence for
# the thought and that is exactly what happened.)
_AVA_THOUGHT = "Ben has not met my eye all round."
_BEN_THOUGHT = "Ava keeps circling back to the same question."


def _thought(text: str, *, substituted: bool | None = None) -> dict[str, Any]:
    """One ``ThoughtRecord``, in the exact shape ``day_round_reflect`` builds.

    Tri-state for the same reason :func:`_record` is: ``substituted=None``
    means the key is ABSENT, which is what an authored thought looks like —
    never ``False``. Passing ``False`` explicitly is a hand-edit, a
    reader-boundary shape the node cannot produce, and is available as such.
    """
    record: dict[str, Any] = {"text": text}
    if substituted is not None:
        record["substituted"] = substituted
    return record


def _thought_lines(doc: str) -> list[str]:
    """Every ``<thought …>`` line of a rendered document, in order."""
    return [line for line in doc.splitlines() if line.startswith("<thought ")]


def _authored_thoughts() -> dict[str, list[Any]]:
    """Both thoughts the players' own — the pre-041 words in the new shape."""
    return {
        "p-ava": [_thought(_AVA_THOUGHT)],
        "p-ben": [_thought(_BEN_THOUGHT)],
    }


def _marked_thoughts() -> dict[str, list[Any]]:
    """Both thoughts substituted — the same records plus the carried flag."""
    return {
        "p-ava": [_thought(_AVA_THOUGHT, substituted=True)],
        "p-ben": [_thought(_BEN_THOUGHT, substituted=True)],
    }


def _one_marked_thoughts() -> dict[str, list[Any]]:
    """Ben's thought substituted, Ava's authored — one marked line, one not."""
    return {
        "p-ava": [_thought(_AVA_THOUGHT)],
        "p-ben": [_thought(_BEN_THOUGHT, substituted=True)],
    }


def test_the_marked_thought_carries_exactly_the_substituted_true_attribute() -> None:
    """The emitted element, pinned as the whole line — the thought's twin of §1.

    The same bytes as the diary's attribute (one shared writer guarantees that,
    and section 2 pins it), but at a DIFFERENT POSITION in the head: a
    ``<thought>`` tag carries only ``player`` before the flag, so this is where
    ``player="Ava" substituted="true"`` — the actual adjacency a committed
    transcript holds — is stated. The near-misses are re-asserted because they
    are a claim about the rendered document and not about the helper.
    """
    doc = _render(None, thoughts=_marked_thoughts())
    lines = _thought_lines(doc)

    assert lines == [
        f'<thought player="Ava" {_SUBSTITUTED_ATTRIBUTE}>{_AVA_THOUGHT}</thought>',
        f'<thought player="Ben" {_SUBSTITUTED_ATTRIBUTE}>{_BEN_THOUGHT}</thought>',
    ]
    assert doc.count(_SUBSTITUTED_ATTRIBUTE) == 2
    assert "substituted='true'" not in doc
    assert 'substituted="True"' not in doc
    assert 'Substituted="' not in doc


@pytest.mark.parametrize(
    ("label", "entries"),
    [
        pytest.param(
            "records",
            {"p-ava": [_thought(_AVA_THOUGHT)], "p-ben": [_thought(_BEN_THOUGHT)]},
            id="records",
        ),
        pytest.param(
            "bare-strings",
            {"p-ava": [_AVA_THOUGHT], "p-ben": [_BEN_THOUGHT]},
            id="bare-strings",
        ),
        pytest.param(
            "mixed",
            {"p-ava": [_AVA_THOUGHT], "p-ben": [_thought(_BEN_THOUGHT)]},
            id="mixed",
        ),
    ],
)
def test_an_authored_thought_document_carries_no_marking_whatever_the_shape(
    label: str, entries: dict[str, list[Any]]
) -> None:
    """No marking on the authored path — and the SAME BYTES for all three shapes.

    Two claims in one, and the second is the one the diary channel cannot make.
    :data:`graphia.state.ThoughtEntry` is the PERMANENT union ``str |
    ThoughtRecord``, so a preserved-corpus game, a resumed pre-reshape
    checkpoint and this session all reach this renderer with different element
    shapes. A bare ``str`` has nowhere to carry a flag, which is exactly why
    ``_append_thoughts`` needs no branch for it: it is authored by
    construction. The three rows therefore assert one document, not three.

    The stake here is an order of magnitude above the diary's: 10,706 thought
    bodies across the 372 committed transcripts against 376 diary ones. An
    unconditional attribute — or an ``if "substituted" in note`` presence test
    — would mark essentially every private line ever preserved.

    Bracketed by a presence assertion, so "no marking" is a statement about
    marked-ness and not about an empty round.
    """
    doc = _render(None, thoughts=entries)
    lines = _thought_lines(doc)

    # The premise: two real thoughts went in, whatever shape they arrived as.
    assert len(lines) == 2
    assert lines == [
        f'<thought player="Ava">{_AVA_THOUGHT}</thought>',
        f'<thought player="Ben">{_BEN_THOUGHT}</thought>',
    ]
    assert "substituted" not in doc, (
        f"an authored thought document ({label}) carries the substitution "
        "marking, so nearly every private line in every preserved game is now "
        "marked. The attribute must be written ONLY where the record's own "
        "`substituted` is the literal `True` — see the `isinstance(note, "
        "dict)` hop in `eval_transcript._append_thoughts`."
    )
    # ...and no dict repr leaked in either, which is the OTHER way this
    # renderer can be wrong about the new element shape: well-formed output.
    assert "{" not in doc and "'text'" not in doc


def test_marking_a_thought_adds_the_attribute_and_changes_nothing_else() -> None:
    """Byte identity as a DIFFERENCE, the thought channel's copy of §1's form.

    Rendering the same round twice — once authored, once with the same records
    plus the flag — and asserting that deleting the attribute from the second
    reproduces the first EXACTLY. That covers what a substring assertion
    cannot: no line re-flows, no element re-orders, and the elements keep their
    place inside the ``<round>`` rather than drifting into the Day trailer where
    the diaries live.

    Non-vacuous by construction: the two documents are asserted to differ
    first, and the number of deletions is pinned.
    """
    authored = _render(None, thoughts=_authored_thoughts())
    marked = _render(None, thoughts=_marked_thoughts())

    assert marked != authored, "the flag changed nothing at all"
    assert marked.count(f" {_SUBSTITUTED_ATTRIBUTE}") == 2
    assert marked.replace(f" {_SUBSTITUTED_ATTRIBUTE}", "") == authored, (
        "the substitution marking changed something other than adding its own "
        "attribute to the `<thought>` tags that carry it"
    )
    assert len(marked.splitlines()) == len(authored.splitlines())


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
def test_a_hand_edited_thought_record_value_renders_as_authored(value: Any) -> None:
    """The writer's defence through ``_append_thoughts``' own caller path.

    Section 1 pins the shared helper and section 1's diary twin pins
    ``_append_diaries``' use of it. This pins the THIRD thing, which neither
    covers: the branch in ``_append_thoughts`` that decides whether to consult
    the helper at all. A caller that appended the helper's result
    unconditionally, or that tested ``if "substituted" in note``, would pass
    both of those and mark a hand-edited record here.

    ``False`` is the row that bites hardest under a presence test: a record
    explicitly DENYING substitution must not be marked because it mentioned the
    subject. ``1`` is the row that makes it an identity check — ``1 == True``
    in Python, so an equality would honour it and the reader would then read
    the resulting marking back as authored, the two halves disagreeing about
    what the file says.
    """
    doc = _render(None, thoughts={"p-ava": [_thought(_AVA_THOUGHT, substituted=value)]})

    assert _thought_lines(doc) == [
        f'<thought player="Ava">{_AVA_THOUGHT}</thought>'
    ]
    assert "substituted" not in doc


def test_the_substituted_thought_body_is_single_line_and_needs_no_clamp() -> None:
    """The bound Slice 9's own forward note claims, checked rather than cited.

    Task 8.4 worried that a marked body at CORPUS length (46-626 characters,
    p90 316) would wrap over many rows and turn ``italic underline strike``
    into a hatch pattern. It cannot, and the reason is structural rather than
    lucky: the mark is set only on the substitution branch, where the returned
    body IS ``nodes.day._REFLECTION_FALLBACK`` verbatim. So the marked body is
    a fixed short string — and this channel's is SHORTER than the diary's (52
    characters against 110), which is the direction that matters.

    Pinned here, in the slice that depends on it, so the argument cannot rot
    into a task note nobody rechecks. The corresponding diary claim is
    :func:`test_the_substituted_body_is_single_line_and_needs_no_clamp`.
    """
    fallback = day_nodes._REFLECTION_FALLBACK

    assert "\n" not in fallback and "\r" not in fallback
    assert fallback == fallback.strip(), "a stripped body would not round-trip"
    assert len(fallback) < len(day_nodes._DIARY_FALLBACK), (
        "the reflection fallback grew past the diary's, so the bound Slice 9 "
        "relies on ('shorter than the diary's, which already renders on one "
        "row') no longer holds"
    )
    # And it really does render as one line through the writer.
    doc = _render(None, thoughts={"p-ava": [_thought(fallback, substituted=True)]})
    assert _thought_lines(doc) == [
        f'<thought player="Ava" {_SUBSTITUTED_ATTRIBUTE}>{fallback}</thought>'
    ]


def test_the_flag_splits_the_thought_tag_head_and_nothing_else() -> None:
    """The tag-line axis on a tag with ONE attribute, not two.

    The diary's version of this test is over a head that already holds
    ``player`` and ``day``; a ``<thought>`` head holds only ``player``, so the
    flag lands in a different position and the span arithmetic is its own:
    FIVE spans become SEVEN. Written in the direction it actually goes — the
    flag ADDS to the head, by design, because a terminal without SGR 9 still
    shows the marking in bold on the tag even though it cannot strike the body.

    What must NOT split is the BODY: one span in both shapes, changing only its
    kind, so a reviewer's eye lands on one struck sentence rather than on a
    body diced at the flag's boundary.
    """
    authored = tokenize_transcript(_AUTHORED_THOUGHT_ELEMENT)
    marked = tokenize_transcript(_MARKED_THOUGHT_ELEMENT)

    assert authored == [
        ('<thought player="', KIND_MARKER),
        ("Alice", KIND_ATTR),
        ('">', KIND_MARKER),
        (_THOUGHT_BODY, KIND_THOUGHT),
        ("</thought>", KIND_MARKER),
    ]
    assert marked == [
        ('<thought player="', KIND_MARKER),
        ("Alice", KIND_ATTR),
        ('" substituted="', KIND_MARKER),
        (_SUBSTITUTED_VALUE, KIND_ATTR),
        ('">', KIND_MARKER),
        (_THOUGHT_BODY, KIND_THOUGHT_SUBSTITUTED),
        ("</thought>", KIND_MARKER),
    ]

    # Restated as the relation, so a regression reports WHICH rule broke.
    assert len(authored) == 5
    assert len(marked) == 7, "the tag-line axis of the thought's marking is gone"
    assert [text for text, kind in marked if kind == KIND_ATTR] == [
        "Alice",
        _SUBSTITUTED_VALUE,
    ]
    assert [text for text, _ in marked if text == _THOUGHT_BODY] == [_THOUGHT_BODY]
    assert marked[-1] == authored[-1] == ("</thought>", KIND_MARKER)
    assert "".join(text for text, _ in marked) == _MARKED_THOUGHT_ELEMENT
    assert "".join(text for text, _ in authored) == _AUTHORED_THOUGHT_ELEMENT


def test_no_line_but_the_marked_thoughts_own_tokenizes_differently() -> None:
    """The flag is read PER LINE on this channel too, and it matters more here.

    ``tokenize_transcript`` makes two WHOLE-FILE passes before the per-line
    chain runs, so a flag read at the wrong level would re-kind every
    ``<thought>`` in the file — and on this channel that is thousands of bodies
    per preserved run rather than one per Day.

    Measured by regrouping both documents' spans into lines and asserting that
    exactly ONE index differs. Ava's authored thought sits on the line directly
    above Ben's marked one, so the neighbour case is covered as well as the
    distant one.
    """
    authored = _spans_by_line(_render(None, thoughts=_authored_thoughts()))
    one_marked = _spans_by_line(_render(None, thoughts=_one_marked_thoughts()))

    assert len(one_marked) == len(authored)
    differing = [
        index
        for index, (before, after) in enumerate(zip(authored, one_marked, strict=True))
        if before != after
    ]

    assert len(differing) == 1, (
        f"marking one thought changed {len(differing)} lines; the flag must be "
        "read per line, off the tag whose body it re-kinds"
    )
    moved = "".join(text for text, _ in one_marked[differing[0]])
    assert moved == (
        f'<thought player="Ben" {_SUBSTITUTED_ATTRIBUTE}>{_BEN_THOUGHT}</thought>'
    )

    # ...and Ava's thought, one line away, is untouched in both kind and shape.
    ava_line = next(
        line
        for line in one_marked
        if "".join(text for text, _ in line).startswith('<thought player="Ava"')
    )
    assert [kind for _, kind in ava_line][-2:] == [KIND_THOUGHT, KIND_MARKER]
    assert KIND_THOUGHT_SUBSTITUTED not in {kind for _, kind in ava_line}


class _FailingReflectionTier:
    """A tier stand-in whose every ``invoke`` raises — forces the substitution.

    The reflection twin of :class:`_FailingTier`, and separate from it only
    because ``day_round_reflect`` fans out: raising on EVERY invocation rather
    than from a queue is what makes it independent of how many reflectors the
    fixture state holds.
    """

    def __init__(self) -> None:
        self.invocations = 0

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "_FailingReflectionTier":
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        raise RuntimeError("reflection model down")


def test_the_node_marks_the_thought_it_substituted_and_the_reader_strikes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole chain on real data, one channel over from §4's diary version.

    Drives the REAL ``day_round_reflect`` against a tier that cannot answer,
    takes the delta it returns, renders it through the real
    ``render_transcript`` and tokenizes the result — so the marking travels
    from ``_ai_reflect``'s substitution branch, through the
    ``_SubstitutionMark`` carrier and the ``ThoughtRecord``, to the span a
    reviewer's terminal strikes, with no literal shared between the ends but
    the body text the node itself chose.

    It is the test that would catch a chain broken at any single joint while
    every layer's own tests stayed green: a helper that stopped stamping the
    mark, a node that stopped reading it, a renderer that omitted the
    attribute, a reader that ignored it. The mark-stamping joint in particular
    has no other end-to-end cover.

    ``_hinge_state`` is reused verbatim: its ``day_turn_index == 0`` and
    ``day_rounds == 1`` are exactly the reflection node's own self-guard for a
    fresh-round start, and its one surviving AI player keeps the delta to a
    single record.

    The premise is asserted first — the model really did fail and the node
    really did substitute — because a chain test whose first link never fired
    would be measuring an authored thought and would pass the moment the
    marking was deleted from all four layers at once.
    """
    tier = _FailingReflectionTier()
    monkeypatch.setattr(day_nodes, "get_large", lambda: tier)

    delta = day_nodes.day_round_reflect(_hinge_state())  # type: ignore[arg-type]
    record = delta["private_thoughts"]["p-ava"][0]

    # The premise: the model was reached, it failed, and the node substituted.
    assert tier.invocations >= 1, "the failing tier was never invoked"
    assert record["text"] == day_nodes._REFLECTION_FALLBACK
    # ``.get`` rather than a subscript, deliberately, and unlike section 4's
    # diary twin: the fault this joint actually has — `_ai_reflect` no longer
    # stamping the mark — leaves the key ABSENT, and a subscript would report it
    # as a bare ``KeyError`` from a test whose message is the only place the
    # mark-stamping joint is named at all.
    assert record.get("substituted") is True, (
        "the node substituted its deterministic stand-in and did not carry the "
        "fact out on the record, so nothing downstream can mark it — check "
        "that `_ai_reflect` stamps its `substitution_mark` on the fallback "
        "branch and that `day_round_reflect` reads it"
    )

    doc = _render(None, thoughts={"p-ava": [record]})

    # The writer marked it...
    assert _thought_lines(doc) == [
        f'<thought player="Ava" {_SUBSTITUTED_ATTRIBUTE}>'
        f"{day_nodes._REFLECTION_FALLBACK}</thought>"
    ]
    # ...and the reader strikes exactly that body and nothing else.
    spans = tokenize_transcript(doc)
    struck = [text for text, kind in spans if kind == KIND_THOUGHT_SUBSTITUTED]
    assert struck == [day_nodes._REFLECTION_FALLBACK]
    assert KIND_THOUGHT not in {kind for _, kind in spans}, (
        "an honest `thought` span survives in a document whose only thought "
        "was substituted"
    )
    assert "".join(text for text, _ in spans) == doc


# ===========================================================================
# 6. DISTINGUISHABILITY: the two PRIVATE marked channels stay tellable apart
# ===========================================================================
#
# The claim Task 9.4 owes beyond "each channel's flag works": a substituted
# diary entry and a substituted thought must remain distinguishable FROM ONE
# ANOTHER, not merely each from its own honest twin.
#
# KEPT AT TWO CHANNELS BY TASK 10.3, WHICH ADDED SECTION 8 RATHER THAN WIDENING
# THIS ONE. The two private bodies are siblings — both muted, both italic, both
# invisible to the table — and this section is the only place their collision
# with each OTHER is under test at full strength. Section 8's six-way count
# would still pass if these two merged and the public kind stayed distinct from
# the merged pair; this section's four-way count refuses that. Two sections, two
# strengths, and the narrower one is not implied by the wider.
#
# WHY IT IS A CLAIM AT ALL, rather than something the two sections above imply.
# ``_INLINE_CONTENT_KINDS`` is one table with a shared value space, and the
# natural cheap implementation of "mark a substituted body" is a single
# ``KIND_SUBSTITUTED`` for every channel — which would satisfy every
# marked/authored pair above (each row asserts a kind is present and its own
# honest twin absent; a shared kind passes all four) while collapsing what a
# reviewer sees: a struck line in a preserved game would no longer say WHICH
# private channel the model failed to answer on. That is the diagnosis the
# marking exists to give, since the per-run counts (Slices 6 and 7) already
# report how many failed and cannot report which entry.
#
# THE FIXTURE IS ONE DOCUMENT HOLDING ALL FOUR BODIES — marked and authored, on
# both channels — because the failure is a COLLISION and a collision is
# invisible from inside either channel's own document. It is also the shape a
# real run produces: ``day_round_reflect`` writes inside the round and
# ``day_diary`` into the Day trailer of the same ``<day>``, so a run in which
# the local model was unreliable holds exactly this.


def _all_four_bodies() -> str:
    """One rendered Day carrying a marked and an authored body on BOTH channels.

    Ava's are the authored pair and Ben's the marked pair, and Ben being
    ``role="Mafia"`` is deliberate: his owner name is ``attr-mafia`` on both
    tags, so the two marked elements are the more decorated of the two shapes
    and the distinguishability claim is made against the harder case.
    """
    return _render(
        {
            "p-ava": [_record(_AVA_ENTRY)],
            "p-ben": [_record(_BEN_ENTRY, substituted=True)],
        },
        thoughts=_one_marked_thoughts(),
    )


def test_the_four_marked_and_authored_body_kinds_are_four_distinct_kinds() -> None:
    """One document, four bodies, four different kinds — the headline claim.

    Every body is looked up by its own TEXT rather than by position, so a
    failure names which body took which kind. The set assertion is what catches
    a collapse to a shared ``substituted`` kind: four texts mapping to fewer
    than four kinds fails here and nowhere else in this module.
    """
    doc = _all_four_bodies()
    kinds = {text: kind for text, kind in tokenize_transcript(doc)}

    # The premise: all four bodies survived to the tokenizer as spans of their
    # own, so the mapping below is about kinds and not about absence.
    for body in (_AVA_THOUGHT, _BEN_THOUGHT, _AVA_ENTRY, _BEN_ENTRY):
        assert body in kinds, f"body did not survive to the tokenizer: {body!r}"

    assert kinds[_AVA_THOUGHT] == KIND_THOUGHT
    assert kinds[_BEN_THOUGHT] == KIND_THOUGHT_SUBSTITUTED
    assert kinds[_AVA_ENTRY] == KIND_DIARY
    assert kinds[_BEN_ENTRY] == KIND_DIARY_SUBSTITUTED

    # ...and the four really are four, which is the claim a shared
    # `KIND_SUBSTITUTED` would break while every pairwise test above passed.
    assert len(
        {
            kinds[_AVA_THOUGHT],
            kinds[_BEN_THOUGHT],
            kinds[_AVA_ENTRY],
            kinds[_BEN_ENTRY],
        }
    ) == 4, (
        "two of the four private-body kinds collapsed into one, so a struck "
        "line in a preserved game no longer says which channel the model "
        "failed to answer on"
    )
    # Stated once more at the constants, so a failure can be read without the
    # document: these are what the UI's style map is keyed on, and the viewer's
    # own completeness equality (`test_ledger_viewer`, `_TRANSCRIPT_KIND_COMPONENTS`
    # == `TRANSCRIPT_KINDS` less `plain`) then guarantees each gets its own
    # component class and therefore its own rule.
    assert KIND_THOUGHT_SUBSTITUTED != KIND_DIARY_SUBSTITUTED
    assert len(
        {KIND_THOUGHT, KIND_THOUGHT_SUBSTITUTED, KIND_DIARY, KIND_DIARY_SUBSTITUTED}
    ) == 4
    assert {
        KIND_THOUGHT,
        KIND_THOUGHT_SUBSTITUTED,
        KIND_DIARY,
        KIND_DIARY_SUBSTITUTED,
    } <= set(eval_ledger.TRANSCRIPT_KINDS)


@pytest.mark.parametrize(
    ("label", "diaries", "thoughts", "expected_marked"),
    [
        pytest.param(
            "diary-only",
            {"p-ava": [_record(_AVA_ENTRY, substituted=True)]},
            {"p-ava": [_thought(_AVA_THOUGHT)]},
            {KIND_DIARY_SUBSTITUTED},
            id="diary-only",
        ),
        pytest.param(
            "thought-only",
            {"p-ava": [_record(_AVA_ENTRY)]},
            {"p-ava": [_thought(_AVA_THOUGHT, substituted=True)]},
            {KIND_THOUGHT_SUBSTITUTED},
            id="thought-only",
        ),
        pytest.param(
            "both",
            {"p-ava": [_record(_AVA_ENTRY, substituted=True)]},
            {"p-ava": [_thought(_AVA_THOUGHT, substituted=True)]},
            {KIND_DIARY_SUBSTITUTED, KIND_THOUGHT_SUBSTITUTED},
            id="both",
        ),
        pytest.param(
            "neither",
            {"p-ava": [_record(_AVA_ENTRY)]},
            {"p-ava": [_thought(_AVA_THOUGHT)]},
            set(),
            id="neither",
        ),
    ],
)
def test_a_flag_on_one_channel_never_reaches_the_other(
    label: str,
    diaries: dict[str, list[dict[str, Any]]],
    thoughts: dict[str, list[Any]],
    expected_marked: set[str],
) -> None:
    """The full cross-product of the two channels' flags, in one document each.

    The sharpest form of distinguishability: a reader that keyed the body kind
    on the FLAG and forgot the tag, or that hoisted "this file has a
    substitution in it" to a document-level fact, would mark both bodies in the
    two single-channel rows. Both of those pass every test in sections 3 and 5,
    because each of those feeds a document in which one channel is the only one
    present.

    Four rows rather than two, because the ``neither`` row is the one that keeps
    the other three honest — it asserts that the marked kinds are absent from a
    clean game, so a reader that emitted them unconditionally cannot pass the
    sweep — and the ``both`` row is the only one that shows the two flags do not
    interfere when they arrive together.
    """
    doc = _render(diaries, thoughts=thoughts)
    kinds = {text: kind for text, kind in tokenize_transcript(doc)}

    # The premise: both bodies are in the document in every row.
    assert _AVA_THOUGHT in kinds and _AVA_ENTRY in kinds

    marked = {KIND_THOUGHT_SUBSTITUTED, KIND_DIARY_SUBSTITUTED} & set(kinds.values())
    assert marked == expected_marked, (
        f"the {label} row produced the wrong marked kinds: a flag on one "
        "channel reached the other, or was read as a document-level fact"
    )
    # ...and every body that is NOT marked keeps its own honest kind rather
    # than being demoted, which is the other half of "a flag may only re-kind".
    if KIND_THOUGHT_SUBSTITUTED not in expected_marked:
        assert kinds[_AVA_THOUGHT] == KIND_THOUGHT
    if KIND_DIARY_SUBSTITUTED not in expected_marked:
        assert kinds[_AVA_ENTRY] == KIND_DIARY


# ===========================================================================
# 7. The THIRD marked channel: a substituted line of Day speech (Slice 10)
# ===========================================================================
#
# The same layers, one channel over and one layer shorter — there is no record
# in between, because a Day-speech line is a message rather than a stored entry:
#
#     ``nodes/day._ai_day_action``       stamps the ``_SubstitutionMark``, and
#                                        ONLY on its substitution branch
#     ``nodes/day.day_turn``             writes
#                                        ``additional_kwargs["substituted"]``,
#                                        and ONLY when the mark is set — and
#                                        ONLY in its AI branch
#     ``tools/eval_transcript``          WRAPS the line as
#                                        ``<speech speaker="…"
#                                        substituted="true">…</speech>``, and
#                                        ONLY for the literal ``True``
#     ``eval_ledger.tokenize_transcript``re-kinds the body, ONLY off ``"true"``
#
# WHAT IS SHARED WITH THE TWO PRIVATE CHANNELS AND THEREFORE NOT RETESTED HERE.
# The writer's value test is ONE function — the same one both sections above
# use — so section 1's eleven-row identity sweep and section 2's writer/reader
# literal agreement cover this channel by construction. Its ``diary_`` prefix
# is gone as of Task 10.3, which is the rename Slice 9 deferred to "a THIRD
# caller and a task that edits this file anyway": both halves are now true.
#
# WHAT IS GENUINELY NEW AND HAS TO BE TESTED HERE, and none of it is a copy of
# the sections above with a name swapped:
#
#   * **the WRITER changes the line's SHAPE rather than a tag's attributes.**
#     This is the only conditional line shape in the whole renderer, and the
#     conditionality is what made covering this channel affordable: the bare
#     ``Speaker: text`` form is the dominant line of the format — 22,508 of
#     them across the committed transcripts — and not one moves. So this
#     channel's byte-identity claim is about a SHAPE, and it is the largest
#     stake in the spec;
#   * **the flag is read off an ``AIMessage``'s ``additional_kwargs`` while the
#     name is read off ``msg.name``** — two sources for one line, where the
#     private channels read one dict. A writer that took the name from the
#     kwargs would be a second source of one truth and would break on any
#     message that carries only ``name``;
#   * **there is no authored ELEMENT**, so "both directions" is two different
#     tests rather than two rows: the bare line, and a hand-written unflagged
#     tag that reads ``plain``;
#   * **the honest twin is SIDE-BEARING**, which no other marked channel's is.
#     Its ``Name:`` prefix and its words both carry the speaker's side, and the
#     marked body carries none — the side moves onto the tag's ``speaker="…"``
#     name (pinned in ``test_transcript_highlight``, where the cast fixtures
#     live);
#   * **and a substituted line can never be a HUMAN-seat line.** Unreachable by
#     construction, and asserted rather than assumed — see the last test in
#     this section.

# The deterministic stand-in ``_ai_day_action`` files when the model's answer
# cannot be decoded. WRITTEN OUT AS A LITERAL HERE, unlike ``_DIARY_FALLBACK``
# and ``_REFLECTION_FALLBACK`` which are module constants: this one is an
# inline literal in ``nodes/day._ai_day_action``'s return, so there is nothing
# to import. Spelled the same way ``test_substitution_counts`` spells it, and
# these are the bytes a committed transcript must hold.
_DAY_ACTION_FALLBACK = "I'm not sure who to trust yet."


def _speech_lines(doc: str) -> list[str]:
    """Every ``<speech …>`` line of a rendered document, in order."""
    return [line for line in doc.splitlines() if line.startswith("<speech ")]


def _bare_line(name: str, text: str) -> str:
    """The bare ``Speaker: text`` shape, spelled once for the tests below."""
    return f"{name}: {text}"


def _marked_line(name: str, text: str) -> str:
    """The wrapped element the writer emits for a substituted line."""
    return f'<speech speaker="{name}" {_SUBSTITUTED_ATTRIBUTE}>{text}</speech>'


def test_the_marked_line_is_wrapped_and_carries_exactly_the_element() -> None:
    """The emitted line, pinned whole — the speech channel's twin of §1.

    Pinned as the complete line rather than by substring, and here that is
    load-bearing twice over: the attribute literal is the interface between two
    modules that never import each other, AND the whole LINE SHAPE is new, so a
    substring assertion would hold on a wrapped line whose tag name, attribute
    order or closing tag had drifted.

    The exact adjacency a committed transcript will hold is
    ``speaker="Ava" substituted="true"`` — the flag LAST, after the name, in the
    order ``_append_messages`` appends them — and the element is SINGLE-LINE,
    which the reader depends on absolutely: it decides a body's kind off the
    attributes on the tag that opens the same line.

    Every near-miss a reviewer would never notice fails here: ``player=`` for
    ``speaker=``, ``substituted='true'``, ``substituted="True"``, a bare
    ``<speech>`` with the name left in the body.
    """
    doc = _render(None, speeches=[_speech("Ava", _AVA_LINE, substituted=True)])

    assert _speech_lines(doc) == [_marked_line("Ava", _AVA_LINE)]
    # Restated as the attribute's own spelling, so a failure says WHICH half
    # drifted rather than showing two walls of tag.
    assert doc.count(_SUBSTITUTED_ATTRIBUTE) == 1
    assert 'speaker="Ava"' in doc
    assert 'player="Ava"' not in doc, (
        "the marked line names its speaker with the diary's/thought's OWNER "
        "attribute; a public utterance has a speaker, and `_attr_kind` is keyed "
        "on the `(tag, key)` PAIR, so `player` on a `<speech>` tag takes no "
        "side at all"
    )
    assert "substituted='true'" not in doc
    assert 'substituted="True"' not in doc
    assert 'Substituted="' not in doc
    # ...and the bare shape is GONE for this line, which is the half a
    # substring assertion cannot state.
    assert _bare_line("Ava", _AVA_LINE) not in doc


def test_an_authored_line_keeps_the_bare_shape_byte_for_byte() -> None:
    """An authored Day renders byte-identically to a pre-041 capture.

    **THE LARGEST BYTE-IDENTITY STAKE IN THE SPEC, by an order of magnitude
    over the thought channel's.** The bare ``Speaker: text`` line is the
    dominant line of the transcript format — 22,508 of them across the
    committed corpus, under 498 distinct names — and it is the shape the
    tokenizer's ``_SPEAKER_RE`` reads. An unconditional wrap would rewrite
    every one of them: every committed transcript would tokenize differently,
    every ``speaker`` / ``speech`` span in the corpus sweep would move, and the
    side tints that make a preserved game readable would relocate onto tag
    attributes en masse.

    So the conditionality is not an optimisation, it is the feature: the marked
    line is promoted to an element and the authored line is left exactly as it
    was. This test is where "exactly" is a byte count rather than an intention.

    THE TWO-SOURCE CLAIM IS HERE TOO, and it is why the first assertion pairs
    two builders. ``_append_messages`` takes the NAME off ``msg.name`` and the
    FLAG off ``additional_kwargs``; production writes the name in both places.
    So a message carrying only ``name=`` and one carrying ``name=`` plus
    ``additional_kwargs["speaker"]`` must render identically — and a writer that
    had started reading the name out of the kwargs would fail on the first,
    which is the shape every pre-Slice-10 test in this module builds.

    The absence assertion is bracketed by a presence one: the document really
    does hold the spoken line, so "no marking" is a statement about
    marked-ness and not about a silent round.
    """
    legacy = _render(None)
    with_speaker_kwarg = _render(None, speeches=[_speech("Ava", _AVA_LINE)])

    # The two-source claim: the flag lives in the kwargs, the name does not.
    assert with_speaker_kwarg == legacy, (
        "a message carrying `additional_kwargs['speaker']` renders differently "
        "from one carrying only `name=`, so the writer has started reading the "
        "speaker out of the kwargs — a second source for one truth"
    )

    # The premise: a real spoken line went in, in the bare shape.
    assert _bare_line("Ava", _AVA_LINE) in legacy.splitlines()
    assert _speech_lines(legacy) == []
    # ...and neither the tag nor the attribute appears ANYWHERE in the whole
    # document — not as `substituted="false"`, not as an unflagged `<speech>`.
    assert "substituted" not in legacy, (
        "an authored line's document carries the substitution marking, so every "
        "honest utterance in every preserved game is now marked"
    )
    assert "<speech" not in legacy and "</speech>" not in legacy, (
        "an authored Day-speech line was promoted to an element, so all 22,508 "
        "bare `Speaker: text` lines in the committed corpus have changed shape "
        "and every span test over them moves. The wrap must be CONDITIONAL on "
        "the flag — see the branch in `eval_transcript._append_messages`"
    )
    assert 'speaker="' not in legacy


def test_marking_one_line_wraps_it_and_leaves_every_other_line_alone() -> None:
    """Byte identity stated as a DIFFERENCE, and the flag read PER MESSAGE.

    Two speakers in one round, one line apart: Ava's authored and Ben's
    substituted. Rendering the round twice — once with both authored, once with
    Ben's marked — and asserting that the ONLY change is Ben's line changing
    shape. That covers what a substring assertion cannot: no other line
    re-flows, the round keeps its bounds, the neighbouring authored line keeps
    the bare shape, and the document keeps its line count.

    THE NEIGHBOUR IS THE POINT. A writer that read the flag off the DELTA
    rather than off each message — or that latched it once and kept it — would
    wrap both lines here and pass every single-message test in this section.

    Non-vacuous by construction: the two documents are asserted to differ
    first, and the replacement that reconciles them is pinned as the exact pair
    of lines rather than as a count.
    """
    authored = _render(
        None, speeches=[_speech("Ava", _AVA_LINE), _speech("Ben", _BEN_LINE)]
    )
    one_marked = _render(
        None,
        speeches=[
            _speech("Ava", _AVA_LINE),
            _speech("Ben", _BEN_LINE, substituted=True),
        ],
    )

    assert one_marked != authored, "the flag changed nothing at all"
    # Exactly one line changed, and unwrapping it reproduces the authored
    # document EXACTLY — no re-flow, no re-order, no line-count change.
    assert one_marked.replace(
        _marked_line("Ben", _BEN_LINE), _bare_line("Ben", _BEN_LINE)
    ) == authored, (
        "marking one line changed something other than that line's own shape"
    )
    assert len(one_marked.splitlines()) == len(authored.splitlines())
    # ...and the neighbour is untouched in shape, so the flag is per message.
    assert _bare_line("Ava", _AVA_LINE) in one_marked.splitlines()
    assert _speech_lines(one_marked) == [_marked_line("Ben", _BEN_LINE)]

    # The same statement at the READER, per line, which is where a flag
    # remembered across lines would show up as a re-kinded neighbour.
    before = _spans_by_line(authored)
    after = _spans_by_line(one_marked)
    assert len(after) == len(before)
    differing = [
        index
        for index, (a, b) in enumerate(zip(before, after, strict=True))
        if a != b
    ]
    assert len(differing) == 1, (
        f"marking one line changed {len(differing)} lines' spans; the flag must "
        "be read per message, off the one whose body it re-kinds"
    )
    assert "".join(text for text, _ in after[differing[0]]) == _marked_line(
        "Ben", _BEN_LINE
    )
    # Ava's line, one line away, still tokenizes as a bare spoken line in her
    # own side's colour — never re-kinded, never demoted.
    ava_line = next(
        line
        for line in after
        if "".join(text for text, _ in line).startswith("Ava:")
    )
    assert [kind for _, kind in ava_line] == [
        KIND_SPEAKER_LAW_ABIDING,
        KIND_SPEECH_LAW_ABIDING,
    ]
    assert KIND_SPEECH_SUBSTITUTED not in {kind for _, kind in ava_line}


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
def test_a_hand_edited_message_flag_value_renders_as_the_bare_line(
    value: Any,
) -> None:
    """The writer's defence through ``_append_messages``' own caller path.

    Section 1 pins the shared helper and sections 1 and 5 pin the two private
    channels' use of it. This pins the FOURTH thing, which none of those
    covers: the branch in ``_append_messages`` that decides whether to wrap at
    all. A caller that appended the helper's result unconditionally, or that
    tested ``if "substituted" in msg.additional_kwargs``, would pass all three
    and wrap a hand-edited message here.

    And on this channel the consequence of guessing is a step worse than
    elsewhere: the private channels would gain a spurious attribute, this one
    would gain a spurious LINE SHAPE — a bare line silently promoted to an
    element, which the reader then kinds by an entirely different rule.

    ``False`` is the row that bites hardest under a presence test: a message
    explicitly DENYING substitution must not be wrapped because it mentioned
    the subject. ``1`` is the row that makes it an identity check — ``1 == True``
    in Python, so an equality would honour it and the reader would then read the
    resulting marking back as authored, the two halves disagreeing about what
    the file says.
    """
    doc = _render(None, speeches=[_speech("Ava", _AVA_LINE, substituted=value)])

    assert _speech_lines(doc) == []
    assert _bare_line("Ava", _AVA_LINE) in doc.splitlines()
    assert "substituted" not in doc


def test_the_substituted_speech_body_is_single_line_and_needs_no_clamp() -> None:
    """The bound Slice 10's own header note claims, checked rather than cited.

    Task 8.4 worried that a marked body at CORPUS length (46-626 characters,
    p90 316) would wrap over many terminal rows and turn the marking's text
    style into a hatch pattern. On this channel that worry looks strongest —
    Day speech IS the corpus's long prose — and it is still structurally
    impossible: the mark is set only on the substitution branch, where the
    returned body IS the deterministic stand-in verbatim. So the marked body is
    a fixed short string, and this channel's is the SHORTEST of the three.

    Pinned here, in the slice that depends on it, so the argument cannot rot
    into a task note nobody rechecks — and pinned against the OTHER TWO
    fallbacks rather than against a number, because "shortest of the three" is
    the claim the UI's flag ledger makes and a bare ``< 40`` would not notice
    the ordering changing.

    The rendered-document half of the same claim is
    ``test_eval_transcript.test_one_line_sections_are_written_inline``, which
    sweeps this element too as of Slice 10.
    """
    assert "\n" not in _DAY_ACTION_FALLBACK and "\r" not in _DAY_ACTION_FALLBACK
    assert _DAY_ACTION_FALLBACK == _DAY_ACTION_FALLBACK.strip(), (
        "a stripped body would not round-trip"
    )
    assert len(_DAY_ACTION_FALLBACK) < len(day_nodes._REFLECTION_FALLBACK) < len(
        day_nodes._DIARY_FALLBACK
    ), (
        "the three fallbacks are no longer in the order the UI's flag ledger "
        "records (speech shortest, then reflection, then diary), so the "
        "'the last marked kind paints one row' claim needs remeasuring"
    )
    # And it really does render as one line through the writer.
    doc = _render(
        None, speeches=[_speech("Ava", _DAY_ACTION_FALLBACK, substituted=True)]
    )
    assert _speech_lines(doc) == [_marked_line("Ava", _DAY_ACTION_FALLBACK)]


def test_the_authored_twin_is_the_bare_line_and_carries_its_side() -> None:
    """THE HONEST DIRECTION FOR THIS CHANNEL, which is not an unflagged tag.

    Written out as its own test because the analogy with the two private
    channels breaks here and breaking it silently would leave a hole. There,
    "flag off" means the same tag without the attribute. Here the writer never
    emits an unflagged ``<speech>`` at all, so the authored twin is a DIFFERENT
    SHAPE — the bare ``Ava: I heard nothing.`` line — and this is where it is
    read back.

    Two claims, and the second is what makes the side-relocation decision
    legible from this module:

    * the bare line splits into ``speaker`` + ``speech`` and NEVER into the
      marked kind, whatever else is in the file;
    * and both of its spans carry Ava's SIDE. That is what the marked line
      gives up on the body and recovers on the tag's ``speaker="…"`` name —
      pinned where the cast fixtures live
      (``test_transcript_highlight.test_a_substituted_speech_lines_speaker_in_the_cast_list_takes_their_side``).
      Asserted here so this module's own document shows the pair a reviewer
      actually compares.

    The tokenizer's ratified convention is visible in the expectation and is
    part of it: the colon belongs to ``speaker``, the separating space to
    ``speech``.
    """
    doc = _render(None, speeches=[_speech("Ava", _AVA_LINE)])
    lines = _spans_by_line(doc)

    spoken = next(
        line
        for line in lines
        if "".join(text for text, _ in line).startswith("Ava:")
    )

    assert spoken == [
        ("Ava:", KIND_SPEAKER_LAW_ABIDING),
        (f" {_AVA_LINE}", KIND_SPEECH_LAW_ABIDING),
    ]
    # ...and the marked kind is nowhere in the document at all.
    assert KIND_SPEECH_SUBSTITUTED not in {
        kind for _, kind in tokenize_transcript(doc)
    }


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
def test_a_hand_edited_unflagged_speech_tag_reads_plain_not_speech(
    value: str,
) -> None:
    """The READER's defence, and the one answer that is deliberately ``plain``.

    ``_INLINE_CONTENT_KINDS`` registers ``("speech", True)`` and deliberately
    NOT ``("speech", False)``, so a ``<speech>`` tag whose flag is missing or
    unrecognised falls all the way through the chained lookup to
    :data:`KIND_PLAIN`. **This is the first WHITELISTED tag for which that final
    hop is reachable at all**, which is why it is worth a test rather than only
    a paragraph — and why a reader who arrives here by analogy needs telling: on
    ``<diary>`` and ``<thought>`` an unrecognised flag falls back to the tag's
    own honest kind, and on ``<speech>`` there is no honest kind to fall back
    to.

    IT IS A DECISION, NOT A GAP, and the alternative was concretely wrong.
    Registering :data:`KIND_SPEECH` for the unflagged pair would claim the
    *side-unknown* kind for the body, and this lookup cannot consult the cast
    list — so a hand-written ``<speech speaker="Ava">…</speech>`` would render
    ACHROMATIC where the bare ``Ava: …`` line it imitates renders in Ava's side
    colour. ``plain`` — "this body is not a kind I know" — is the honest answer
    for a shape nothing produces; a wrong colour is not.

    It also does not weaken the "a flag may only RE-KIND a body, never demote
    one" rule that
    :func:`test_the_flag_re_kinds_only_a_body_that_has_a_marked_kind_registered`
    protects: nothing is demoted here, because there was never an unflagged
    kind for the flag to take away.

    The positive control is inline rather than implied — the literal ``"true"``
    really does re-kind the body — so a tokenizer that had stopped reading the
    flag altogether fails this test instead of passing every row of it.
    """
    source = f'<speech speaker="Alice" substituted="{value}">{_SPEECH_BODY}</speech>'
    kinds = {text: kind for text, kind in tokenize_transcript(source)}

    assert kinds[_SPEECH_BODY] == KIND_PLAIN, (
        f'substituted="{value}" was honoured on a `<speech>` tag, or the '
        "unflagged pair has been registered — only the literal "
        f'"{_SUBSTITUTED_VALUE}" may re-kind this body, and the unflagged form '
        "must read `plain` rather than claiming the side-unknown `speech` kind"
    )
    assert kinds[_SPEECH_BODY] != KIND_SPEECH
    assert eval_ledger._is_substituted(f' substituted="{value}"') is False
    # The positive control, in the same test, on the same shape.
    positive = tokenize_transcript(_MARKED_SPEECH_ELEMENT)
    assert (_SPEECH_BODY, KIND_SPEECH_SUBSTITUTED) in positive
    # ...and the wholly unflagged tag reads plain too, which is the shape a
    # hand-editor is likeliest to type.
    unflagged = {text: kind for text, kind in tokenize_transcript(
        _UNFLAGGED_SPEECH_ELEMENT
    )}
    assert unflagged[_SPEECH_BODY] == KIND_PLAIN
    # The name is still lifted whatever the flag says, so a hand-edit is
    # visible rather than hidden inside the marker.
    assert unflagged["Alice"] == KIND_ATTR


class _FailingDayActionTier:
    """A tier stand-in whose every ``invoke`` raises — forces the substitution.

    The third of this module's failing tiers, and separate from the other two
    for the reason they are separate from each other: raising on EVERY
    invocation rather than from a queue makes it independent of how many times
    ``_ai_day_action`` retries (measured: two invocations for one turn).
    """

    def __init__(self) -> None:
        self.invocations = 0

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "_FailingDayActionTier":
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        raise RuntimeError("day action model down")


def _speaking_state() -> dict[str, Any]:
    """A mid-Day state whose ``day_order`` puts the AI player's turn first.

    ``_hinge_state``'s shape plus the one channel ``day_turn`` reads that the
    hinge nodes do not: ``day_order``. Two entries with the AI first, so the
    turn does NOT wrap the round — a wrap would fold the speech into
    ``_round_complete_update``'s delta beside a recap and reshuffle the order,
    none of which this test is about.

    Hugo is the human seat and is deliberately SECOND: his turn is never
    reached, so this node call never interrupts.
    """
    state = _hinge_state()
    state["day_order"] = ["p-ava", "p-hugo"]
    return state


def test_the_node_marks_the_line_it_substituted_and_the_reader_strikes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole chain on real data, one channel over from §4's and §5's.

    Drives the REAL ``day_turn`` against a tier that cannot answer, takes the
    delta it returns, renders it through the real ``render_transcript`` and
    tokenizes the result — so the marking travels from ``_ai_day_action``'s
    substitution branch, through the ``_SubstitutionMark`` carrier and the
    ``AIMessage``'s ``additional_kwargs``, to the span a reviewer's terminal
    strikes, with no literal shared between the ends but the body text the node
    itself chose.

    It is the test that would catch a chain broken at any single joint while
    every layer's own tests stayed green: a helper that stopped stamping the
    mark, a node that stopped reading it, a renderer that stopped wrapping, a
    reader that ignored the flag.

    THE MESSAGE THIS CHANNEL PUTS THE FACT ON IS THE ARTEFACT ITSELF, which is
    the joint that has no other end-to-end cover. The two private channels stamp
    a stored record that the writer reads later; here the node builds the very
    ``AIMessage`` the transcript renders, so the flag and the words travel
    together on one object and a mismatch between them is invisible upstream.

    Both premises are asserted first — the model really did fail, and the node
    really did substitute — because a chain test whose first link never fired
    would be measuring an authored line and would pass the moment the marking
    was deleted from all four layers at once.
    """
    tier = _FailingDayActionTier()
    monkeypatch.setattr(day_nodes, "get_large", lambda: tier)

    delta = day_nodes.day_turn(_speaking_state())  # type: ignore[arg-type]
    message = delta["messages"][0]

    # The premise: the model was reached, it failed, and the node substituted.
    assert tier.invocations >= 1, "the failing tier was never invoked"
    assert message.content == _DAY_ACTION_FALLBACK
    # ``.get`` rather than a subscript, exactly as §5's twin does and for the
    # same reason: the fault this joint actually has — `_ai_day_action` no
    # longer stamping the mark — leaves the key ABSENT, and a subscript would
    # report it as a bare `KeyError` from a test whose message is the only
    # place the mark-stamping joint is named.
    assert message.additional_kwargs.get("substituted") is True, (
        "the node substituted its deterministic stand-in and did not carry the "
        "fact out on the message, so nothing downstream can mark it — check "
        "that `_ai_day_action` stamps its `substitution_mark` on the fallback "
        "branch and that `day_turn`'s AI branch reads it"
    )
    # ...and the name is on the message twice, in agreement, which is what the
    # writer's two-source split relies on.
    assert message.name == "Ava"
    assert message.additional_kwargs["speaker"] == "Ava"

    doc = render_transcript(
        _day_events(None, speeches=[message]),
        _transcript_players(),
        game_index=1,
        run_meta=None,
    )

    # The writer wrapped it...
    assert _speech_lines(doc) == [_marked_line("Ava", _DAY_ACTION_FALLBACK)]
    # ...and the reader strikes exactly that body and nothing else.
    spans = tokenize_transcript(doc)
    struck = [text for text, kind in spans if kind == KIND_SPEECH_SUBSTITUTED]
    assert struck == [_DAY_ACTION_FALLBACK]
    # ...and no honest speech span survives for a line that was substituted:
    # the bare shape is gone, so both of its spans are too.
    assert not {KIND_SPEAKER_LAW_ABIDING, KIND_SPEECH_LAW_ABIDING} & {
        kind for _, kind in spans
    }, (
        "an honest speech span survives in a document whose only utterance was "
        "substituted"
    )
    # The round trip still holds over the whole document, which is the promise
    # every kind added to the tokenizer must leave intact.
    assert "".join(text for text, _ in spans) == doc


def test_a_substituted_human_seat_line_is_unreachable_and_stays_unmarked() -> None:
    """THE IMPOSSIBILITY, asserted rather than worked around silently.

    A substituted line of speech **can never be a human-seat line**, and the
    reason is structural: the flag is written by ``day_turn``'s AI branch alone,
    off a ``_SubstitutionMark`` that only ``_ai_day_action`` stamps, and the
    human branch calls neither. So ``substituted="true"`` and the reviewer's own
    seat cannot co-occur in any document the engine produces — **by
    construction rather than by omission**, which is exactly the distinction
    this test exists to record. An unreachable combination that nobody states
    reads identically to an untested one.

    Task 10.1 also left the human construction site deliberately unmarked and
    said why: its ``if not text: text = "(stays silent.)"`` stands in for the
    human's own DECISION to type nothing, not for an answer that failed to
    decode, so marking it would file honest human silence in the same visual
    class as a failed structured decode.

    THREE CLAIMS, at three different layers, because "cannot happen" is only
    worth as much as the layer that refuses it:

    * **the vocabulary has no kind for it.** No declared kind names both the
      seat and the marking, so even a writer that produced such a line could
      not be rendered as one. This is the cheapest and most permanent half;
    * **the seat's honest line really is seat-kinded in this very fixture**, so
      the claim is not vacuous through a fixture that has no seat at all;
    * **and a HAND-BUILT marked line for the seat still reads as the plain
      marked kind** — no seat, no side, on the body. This is the second layer
      Task 10.2 added without needing to: the tag path never consults the seat
      at all, so the combination is refused by the reader as well as by the
      writer. It is also the honest answer to "what if somebody hand-edits a
      transcript", which is the only way this document can exist.

    Note what the third claim gives up and why that is acceptable: a marked line
    loses the BOLD that marks the reviewer's own seat, not merely the side. That
    would be a real loss if the combination could occur — and it cannot, so the
    only lines it applies to are hand-edits.
    """
    # 1. THE VOCABULARY. No kind names both axes.
    seat_and_marking = [
        kind
        for kind in eval_ledger.TRANSCRIPT_KINDS
        if "substituted" in kind and "human" in kind
    ]
    assert seat_and_marking == [], (
        "a kind names both the reviewer's seat and the substitution marking, so "
        "the vocabulary now describes a line the engine cannot produce: the flag "
        "is written only by `day_turn`'s AI branch and the human branch never "
        f"calls `_ai_day_action` — {seat_and_marking}"
    )

    # 2. THE PREMISE. Hugo is the human seat, and his AUTHORED line is
    # seat-kinded — so this fixture really does have a seat to lose.
    players = {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-hugo": PlayerState(
            id="p-hugo",
            name="Hugo",
            role="mafia",  # type: ignore[arg-type]
            is_human=True,
            is_alive=True,
            persona=None,
        ),
    }
    human_line = "I was asleep, and I have said so twice."

    def _render_for(message: AIMessage) -> str:
        return render_transcript(
            _day_events(None, speeches=[message]),
            players,
            game_index=1,
            run_meta=None,
        )

    authored = _render_for(_speech("Hugo", human_line))
    authored_kinds = {text: kind for text, kind in tokenize_transcript(authored)}

    assert authored_kinds["Hugo:"] == eval_ledger.KIND_SPEAKER_MAFIA_HUMAN
    assert authored_kinds[f" {human_line}"] == eval_ledger.KIND_SPEECH_MAFIA_HUMAN

    # 3. THE HAND-BUILT IMPOSSIBLE LINE. Only a hand-edit can produce it, and
    # the reader gives it the plain marked kind — never a seat kind, never a
    # side, on the body.
    forced = _render_for(_speech("Hugo", human_line, substituted=True))
    forced_kinds = {text: kind for text, kind in tokenize_transcript(forced)}

    assert forced_kinds[human_line] == KIND_SPEECH_SUBSTITUTED
    assert not [
        kind for kind in forced_kinds.values() if "human" in kind
    ], (
        "the tag path consulted the reviewer's seat, so a hand-edited marked "
        "line can now wear a seat kind — the marked kinds are about AUTHORSHIP "
        "and name neither a side nor a seat"
    )
    # ...and his side is still readable, on the one token that can carry it.
    assert forced_kinds["Hugo"] == eval_ledger.KIND_ATTR_MAFIA


# ===========================================================================
# 8. DISTINGUISHABILITY at full width: all THREE marked channels
# ===========================================================================
#
# Section 6's claim with the last channel in it, and it is a strictly stronger
# statement rather than a third row: a shared marked kind for two of the three
# would satisfy every per-channel test in sections 3, 5 and 7 and every
# assertion in section 6 that does not name the third.
#
# WHY THE PUBLIC CHANNEL MAKES IT A NEW CLAIM RATHER THAN A LONGER ONE. The two
# private bodies are siblings — both muted, both italic, both invisible to the
# table — and Slice 9's worry was that they would collide with each other. A
# public utterance is a different thing entirely: it is a turn the whole table
# HEARD, so a reviewer reading a struck line needs to know whether the model
# failed to give a player their voice or failed to give them a private thought,
# which are different diagnoses about the same run. The per-run counts (Slices
# 6 and 7) report how many failed on each and can never report which line.
#
# THE FIXTURE IS ONE DOCUMENT HOLDING ALL SIX BODIES — marked and authored on
# all three channels — because the failure is a COLLISION and a collision is
# invisible from inside any one channel's own document. It is also the shape a
# real run produces: ``day_turn`` writes into the round, ``day_round_reflect``
# into the same round and ``day_diary`` into the Day's trailer, so a run in
# which the local model was unreliable holds exactly this.
#
# The reader-side twin of this section, on hand-written bytes small enough to
# check by eye, is ``test_transcript_highlight``'s sixth synthetic game
# (``_THREE_MARKED_CHANNELS_GAME``). This one renders through the real writer.


def _all_six_bodies() -> str:
    """One rendered Day carrying a marked and an authored body on ALL THREE.

    Ava's are the authored three and Ben's the marked three, and Ben being
    ``role="Mafia"`` is deliberate for the same reason section 6 gives: his
    name is ``attr-mafia`` on every tag that carries it, so the marked elements
    are the more decorated shapes and the claim is made against the harder case.

    Note the speech pair's asymmetry, which is the whole character of the
    channel: Ava's authored line is the bare ``Ava: …`` shape and Ben's marked
    one is an element, so this document is the only place in the module where
    the two halves of a pair are different SHAPES.
    """
    return _render(
        {
            "p-ava": [_record(_AVA_ENTRY)],
            "p-ben": [_record(_BEN_ENTRY, substituted=True)],
        },
        thoughts=_one_marked_thoughts(),
        speeches=[
            _speech("Ava", _AVA_LINE),
            _speech("Ben", _BEN_LINE, substituted=True),
        ],
    )


def test_the_six_marked_and_authored_body_kinds_are_six_distinct_kinds() -> None:
    """One document, six bodies, six different kinds — the family's headline.

    Every body is looked up by its own TEXT rather than by position, so a
    failure names which body took which kind. The set assertion is what catches
    a collapse to a shared marked kind: six texts mapping to fewer than six
    kinds fails here and nowhere else in this module.

    The authored speech body is keyed with its LEADING SPACE, which is not a
    blemish in the fixture but the tokenizer's ratified convention showing
    through: the bare shape gives the colon to ``speaker`` and the separating
    space to ``speech``. A marked body has no such prefix at all, which is the
    line-shape difference this channel's marking makes, visible in the span
    text itself.
    """
    doc = _all_six_bodies()
    kinds = {text: kind for text, kind in tokenize_transcript(doc)}

    expected = {
        f" {_AVA_LINE}": KIND_SPEECH_LAW_ABIDING,
        _BEN_LINE: KIND_SPEECH_SUBSTITUTED,
        _AVA_THOUGHT: KIND_THOUGHT,
        _BEN_THOUGHT: KIND_THOUGHT_SUBSTITUTED,
        _AVA_ENTRY: KIND_DIARY,
        _BEN_ENTRY: KIND_DIARY_SUBSTITUTED,
    }

    # The premise: all six bodies survived to the tokenizer as spans of their
    # own, so the mapping below is about kinds and not about absence.
    for body in expected:
        assert body in kinds, f"body did not survive to the tokenizer: {body!r}"

    assert {body: kinds[body] for body in expected} == expected

    # ...and the six really are six, which is the claim a shared marked kind
    # would break while every pairwise test above passed.
    assert len(set(expected.values())) == 6, (
        "two of the six body kinds collapsed into one, so a struck line in a "
        "preserved game no longer says which channel the model failed to "
        "answer on"
    )
    # Stated once more at the constants, so a failure can be read without the
    # document: these are what the UI's style map is keyed on, and the viewer's
    # own completeness equality (`test_ledger_viewer`,
    # `_TRANSCRIPT_KIND_COMPONENTS` == `TRANSCRIPT_KINDS` less `plain`) then
    # guarantees each gets its own component class and therefore its own rule.
    marked = {
        KIND_SPEECH_SUBSTITUTED,
        KIND_THOUGHT_SUBSTITUTED,
        KIND_DIARY_SUBSTITUTED,
    }
    assert len(marked) == 3
    assert marked <= set(eval_ledger.TRANSCRIPT_KINDS)
    # ...and the marked SPEECH body took no side, on the one channel where a
    # side was available to take: Ben is `role="Mafia"` and his authored twin
    # would have read `speech-mafia`. Read off the document, and stated as "no
    # side-bearing speech kind" rather than as one inequality, because the
    # rejected alternative was three tinted body kinds and not one.
    assert not [
        kind
        for kind in (kinds[_BEN_LINE],)
        if kind.startswith("speech-") and kind != KIND_SPEECH_SUBSTITUTED
    ], (
        "the marked speech body took a side-qualified kind, so the side is on "
        "the words rather than on the speaker's name — the words of a "
        "substituted line have no author and therefore no allegiance"
    )
    # ...while his NAME inside the tag does carry it, which is where Task 10.2
    # moved it. Read off the marked line's OWN spans rather than through the
    # by-text map, and that is a correction rather than a flourish: Ben's name
    # appears on four tags in this document (his cast entry and all three
    # marked ones), so a `kinds["Ben"]` lookup reports whichever came LAST and
    # would be satisfied by the `<diary player=…>` branch with the
    # `<speech speaker=…>` one deleted. (The full three-row sweep of the side
    # lives in `test_transcript_highlight`, where the cast fixtures are.)
    spans = tokenize_transcript(doc)
    head = spans.index(('<speech speaker="', KIND_MARKER))
    assert spans[head : head + 7] == [
        ('<speech speaker="', KIND_MARKER),
        ("Ben", eval_ledger.KIND_ATTR_MAFIA),
        ('" substituted="', KIND_MARKER),
        (_SUBSTITUTED_VALUE, KIND_ATTR),
        ('">', KIND_MARKER),
        (_BEN_LINE, KIND_SPEECH_SUBSTITUTED),
        ("</speech>", KIND_MARKER),
    ]


@pytest.mark.parametrize(
    ("label", "speech_marked", "private_marked", "expected_marked"),
    [
        pytest.param("neither", False, False, set(), id="neither"),
        pytest.param(
            "speech-only",
            True,
            False,
            {KIND_SPEECH_SUBSTITUTED},
            id="speech-only",
        ),
        pytest.param(
            "privates-only",
            False,
            True,
            {KIND_THOUGHT_SUBSTITUTED, KIND_DIARY_SUBSTITUTED},
            id="privates-only",
        ),
        pytest.param(
            "all-three",
            True,
            True,
            {
                KIND_SPEECH_SUBSTITUTED,
                KIND_THOUGHT_SUBSTITUTED,
                KIND_DIARY_SUBSTITUTED,
            },
            id="all-three",
        ),
    ],
)
def test_a_flag_on_the_public_channel_never_reaches_the_private_ones(
    label: str,
    speech_marked: bool,
    private_marked: bool,
    expected_marked: set[str],
) -> None:
    """The public/private cross-product, in one document each.

    Section 6 sweeps the two PRIVATE channels' flags against each other; this
    sweeps the public one against both of them, which is a different failure.
    The two private channels are written by two nodes into two state channels
    and rendered by two functions; the public one is written by a THIRD node
    onto a message and rendered by a third function that changes the line's
    shape. So "this file has a substitution in it" hoisted to a document-level
    fact, or a flag keyed on the flag alone with the tag forgotten, would show
    up in the two single-sided rows and nowhere else — and both of those pass
    every test in sections 3, 5 and 7, because each of those feeds a document in
    which one channel is the only marked one.

    The ``neither`` row is what keeps the other three honest: it asserts the
    marked kinds are absent from a clean game, so a reader that emitted them
    unconditionally cannot pass the sweep. The ``all-three`` row is the only one
    that shows three flags do not interfere when they arrive together.

    The authored SPEECH body is checked by its bare-shape key in the rows where
    it is not marked, which is the assertion that the un-marked public line was
    not merely left un-struck but left in the bare SHAPE — the thing this
    channel can lose that the private ones cannot.
    """
    doc = _render(
        {"p-ava": [_record(_AVA_ENTRY, substituted=private_marked or None)]},
        thoughts={"p-ava": [_thought(_AVA_THOUGHT, substituted=private_marked or None)]},
        speeches=[_speech("Ava", _AVA_LINE, substituted=speech_marked or None)],
    )
    kinds = {text: kind for text, kind in tokenize_transcript(doc)}

    # The premise: all three bodies are in the document in every row, whichever
    # shape the speech arrived in.
    assert _AVA_THOUGHT in kinds and _AVA_ENTRY in kinds
    speech_body = _AVA_LINE if speech_marked else f" {_AVA_LINE}"
    assert speech_body in kinds

    all_marked = {
        KIND_SPEECH_SUBSTITUTED,
        KIND_THOUGHT_SUBSTITUTED,
        KIND_DIARY_SUBSTITUTED,
    }
    assert all_marked & set(kinds.values()) == expected_marked, (
        f"the {label} row produced the wrong marked kinds: a flag on one "
        "channel reached another, or was read as a document-level fact"
    )
    # ...and every body that is NOT marked keeps its own honest kind — and, for
    # the public channel, its own honest SHAPE.
    if not private_marked:
        assert kinds[_AVA_THOUGHT] == KIND_THOUGHT
        assert kinds[_AVA_ENTRY] == KIND_DIARY
    if not speech_marked:
        assert kinds[speech_body] == KIND_SPEECH_LAW_ABIDING
        assert _speech_lines(doc) == [], (
            "an unmarked line was promoted to an element, so the flag changed "
            "the shape of a line it was not set on"
        )
