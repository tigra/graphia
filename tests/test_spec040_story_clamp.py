"""Spec 040 Slice 5 — the length bound, and the mark it leaves (Task 5.4).

WHY THIS FILE EXISTS, AND WHY IT IS THE WHOLE OF SLICE 5's WITNESS
------------------------------------------------------------------

Slice 5 shipped two things with **no test of their own**: the
paragraph-preserving clamp (Task 5.1) and the truncation flag (Task 5.3).
Both were measured to be unwitnessed before this file was written, and the
measurement is worth recording because it is the reason the file is as long
as it is:

* Restoring Slice 1's ``result.text.strip()`` at the clamp's call site —
  **deleting the clamp entirely** — left the suite at 2699 passed / 1 skipped,
  byte-for-byte identical.
* A census of the whole suite (a plugin wrapping ``_clamp_story`` and
  recording the running test's nodeid) found **94 test items across 22 files**
  reach the clamp, and **zero** of them reach it with an input that makes it
  truncate. So every one of those 94 is a pass-through witness: they prove the
  clamp does not corrupt an ordinary story, and none of them can see the bound.
  (Task 5.1's own census recorded 93 items across 22 files; it did not list
  them, so only the file count can be compared. The one extra item arrived
  with Task 5.3, which added no test of its own but did widen the helper.)
* ``CLOSING_STORY_TRUNCATED_KWARG`` had **one writer and no reader** anywhere
  in ``src/`` or ``tests/``.

EVERY FIXTURE HERE IS CONSTRUCTED INPUT, AND THAT IS NOT A SHORTCUT. Task 2.3
measured two live stories at 843 and 911 characters over three paragraphs
each, against the then-current ``STORY_PARAGRAPH_BOUND = 4`` and
``STORY_MAX_CHARS = 1800`` — the clamp would not have fired on either. Real
stories do not reach the cap, so no live run can exercise this and Task 10.4
should label this evidence **constructed** rather than implying a natural
instance.

**AND THEN ONE DID, WHICH IS WHY BOTH NUMBERS HAVE SINCE MOVED (2026-09-09).**
The first measured story after those two — record 45 — came in at three
paragraphs of 474 + 487 + 419 and *was* clamped, but by the CHARACTER backstop
rather than by the stated paragraph cap: a fourth ~460-character paragraph
would have crossed 1800 while four paragraphs had not been reached. The bound
is now 5 and the backstop 2500, so the paragraph cap governs again and the
backstop is a runaway guard once more. **Every assertion in this file is
derived from the two constants**, which is why the raise touched only this
prose and one absolute-literal pin in
``tests/test_spec040_story_instruction.py``.

THE FIXTURE TRAP THIS FILE'S OWN TASK LINE SETS. Task 5.4 says "a
four-paragraph story is untouched", which holds only if those four paragraphs
also fit inside ``STORY_MAX_CHARS``. That is a constraint on the fixture, not
on the clamp: a test built from four *long* paragraphs would fail correctly
while looking like a clamp bug. Every fixture below that intends the
paragraph cap to be the only thing in play asserts its own total length
against ``STORY_MAX_CHARS`` first, so a future reader who lengthens
``_paragraph``'s default fails on the fixture guard rather than on the claim.

TWO LEVELS, DELIBERATELY, BECAUSE THEY WITNESS DIFFERENT DEFECTS
----------------------------------------------------------------

The clamp tests call :func:`~graphia.nodes.endgame._clamp_story` directly —
it is pure (no state, no RNG, no clock), so a direct call is the sharpest
instrument for the bound itself.

The node tests call :func:`~graphia.nodes.endgame.moderator_story` with a
scripted ``Story`` and a hand-built state, because **no clamp test can see
the plumbing**. Three defects live strictly between the clamp and the
message: the clamp not being called at all, the flag being dropped on the way
out, and the flag being re-derived from the finished story instead of read
from the clamp. The last is the constraint §2.5 exists for — an
exactly-``STORY_MAX_PARAGRAPHS`` story and a longer one that lost its last
are **indistinguishable afterwards** — and it is why the
first-paragraph-exemption case is asserted at the *message* here and not only
at the clamp: only the message proves the exemption survived the plumbing.

ANTI-VACUITY IS STRUCTURAL HERE, NOT A HABIT. Spec 040 has been bitten four
times by an assertion that passed because the thing it tested for was never in
the fixture. Every absence assertion below carries its positive control **in
the same test, asserted first**: the sentinel of a dropped paragraph is
checked absent only after the kept sentinels are checked present; the
truncation key is checked absent only after it has been seen present on the
clipped case; ``"\\r"`` is checked absent from the output only after it is
confirmed present in the input; and the "a truthy answer never clamps to
nothing" property is preceded by a demonstration that an empty clamp is
observable at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from graphia.llm import Story
from graphia.nodes.endgame import (
    _STORY_PARAGRAPH_SEPARATOR,
    STORY_MAX_CHARS,
    STORY_MAX_PARAGRAPHS,
    _clamp_story,
    moderator_story,
)
from graphia.state import CLOSING_STORY_KWARG, CLOSING_STORY_TRUNCATED_KWARG

# The hand-built endgame state is IMPORTED from the Slice 1 node-test file
# rather than rebuilt, for the same reason that file imports its driver from
# the topology file: one copy per slice. Three spec-040 files already drive a
# game headlessly (``_story_topology``, ``_story_node``,
# ``_story_material_from_a_game``) and twenty files across the suite carry
# their own ``Command(resume=...)`` pump; none of the claims here needs a
# driven game at all, because ``moderator_story`` is directly callable with a
# hand-built state and one scripted ``Story``.
from test_spec040_story_node import DIRECT_STATE

# --------------------------------------------------------------------------
# Fixtures — constructed paragraphs that say which one they are
#
# Every paragraph carries an OPEN and a CLOSE sentinel naming its index, so an
# assertion can say WHICH paragraph survived and WHICH was dropped rather than
# only how many. The CLOSE sentinel is what makes "truncated at a paragraph
# boundary, never a character cut" assertable: §2.5 calls that a difference in
# kind rather than degree, and a length assertion alone cannot see it — a story
# cut mid-word is exactly as long as one that ends where it meant to.
#
# The filler is a single repeated character with no whitespace in it, so the
# clamp's whitespace fold is the identity on these paragraphs and the output
# can be compared to the input with ``==`` rather than with a shape assertion.
# --------------------------------------------------------------------------

#: Comfortably inside ``STORY_MAX_CHARS`` even at ``STORY_MAX_PARAGRAPHS + 1``
#: of them, so a test about the paragraph cap is never accidentally a test
#: about the character bound. Guarded per test, not trusted here.
_SHORT = 60


def _paragraph(n: int, chars: int = _SHORT) -> str:
    """One identifiable paragraph of exactly ``chars`` characters."""
    head = f"PARA-{n}-OPEN "
    tail = f" PARA-{n}-CLOSE"
    filler = "x" * (chars - len(head) - len(tail))
    assert filler, (
        f"fixture error: a {chars}-character paragraph cannot hold both "
        f"sentinels ({len(head) + len(tail)} characters)"
    )
    built = head + filler + tail
    assert len(built) == chars
    return built


def _story_of(count: int, chars: int = _SHORT) -> str:
    """``count`` identifiable paragraphs joined the way the model is asked to."""
    return _STORY_PARAGRAPH_SEPARATOR.join(
        _paragraph(n, chars) for n in range(1, count + 1)
    )


def _opened(n: int) -> str:
    return f"PARA-{n}-OPEN"


def _closed(n: int) -> str:
    return f"PARA-{n}-CLOSE"


def _paragraphs_of(story: str) -> list[str]:
    """The story's paragraphs, split on the separator the clamp rejoins with."""
    return story.split(_STORY_PARAGRAPH_SEPARATOR)


def _assert_fits_the_character_bound(text: str) -> None:
    """Guard the fixture, so a fixture fault never reads as a clamp fault.

    Task 5.4's own line says "a four-paragraph story is untouched", and that is
    true of the clamp only when the four paragraphs also fit inside
    ``STORY_MAX_CHARS``. Anything below that intends the paragraph cap to be
    the sole actor calls this first.
    """
    assert len(text) <= STORY_MAX_CHARS, (
        f"FIXTURE FAULT, not a clamp fault: this input is {len(text)} "
        f"characters against STORY_MAX_CHARS={STORY_MAX_CHARS}, so the "
        "character backstop will fire and the assertion below is no longer "
        "about the paragraph cap. Shorten the paragraphs."
    )


def _the_story_message(result: dict) -> Any:
    """The one message a successful ``moderator_story`` update carries."""
    assert set(result) == {"messages"}, (
        "expected exactly a messages update; got keys "
        f"{sorted(result)}. An EMPTY update means no story was told at all — "
        "conftest's _LoudFailureLLM raises RuntimeError, an ordinary "
        "Exception, which moderator_story's broad except swallows into a "
        "perfectly legitimate no-story ending. Check fake_large is installed."
    )
    assert len(result["messages"]) == 1
    return result["messages"][0]


# ==========================================================================
# The clamp itself
# ==========================================================================


def test_a_story_inside_both_bounds_is_returned_untouched_and_unflagged() -> None:
    """The pass-through case: exactly the cap's worth of paragraphs, unchanged.

    This is the control the other clamp tests are read against, and it is the
    claim the 94 census items already make incidentally — stated once here
    explicitly, and AT the boundary rather than below it. "At most
    ``STORY_MAX_PARAGRAPHS``" is an inclusive bound, and the *too strict*
    direction of an off-by-one in the cap is invisible everywhere else in this
    file. Verified under mutation, and the first answer was wrong: tightening
    the cap by one (``len(kept) >= STORY_MAX_PARAGRAPHS - 1``) turns **six**
    tests in this file red, not one — because a story built at the cap is the
    fixture several of them share. What is true, and is why this test earns
    its place beside them, is that it is the only one whose whole subject is
    the inclusive boundary: the others go red as collateral and would each
    still read as "the cap fired", while this one says in its name that a
    story inside the bound was rewritten.
    """
    text = _story_of(STORY_MAX_PARAGRAPHS)
    _assert_fits_the_character_bound(text)

    story, clipped = _clamp_story(text)

    assert story == text, (
        "a story already inside both bounds must come back byte-identical; "
        "the clamp normalises but must not rewrite"
    )
    assert len(_paragraphs_of(story)) == STORY_MAX_PARAGRAPHS
    assert clipped is False, (
        "nothing was dropped, so the flag must be False — it reports a DROP, "
        "not the fact that the story reached the cap"
    )


def test_one_paragraph_past_the_cap_loses_the_last_whole_paragraph() -> None:
    """``STORY_MAX_PARAGRAPHS + 1`` paragraphs lose the last one, whole.

    The two halves of §2.5's promise in one test: the cap is enforced, **and**
    what it removes is a whole paragraph rather than a character cut. The
    ``endswith`` assertion is the one that can tell those apart — a story
    truncated mid-sentence is exactly as long as one that ends where it meant
    to, so a length assertion cannot see the difference and §2.5 calls it a
    difference in kind rather than degree ("the story is the last thing the
    player reads").
    """
    text = _story_of(STORY_MAX_PARAGRAPHS + 1)
    _assert_fits_the_character_bound(text)

    story, clipped = _clamp_story(text)

    # Positive control FIRST: everything that should have survived did, so the
    # absence assertion below cannot pass on an empty or truncated-to-nothing
    # story.
    for n in range(1, STORY_MAX_PARAGRAPHS + 1):
        assert _opened(n) in story, (
            f"paragraph {n} is inside the cap and must survive; the clamp "
            f"returned {len(_paragraphs_of(story))} paragraph(s)"
        )
    assert _opened(STORY_MAX_PARAGRAPHS + 1) not in story, (
        f"paragraph {STORY_MAX_PARAGRAPHS + 1} is past the cap and must be "
        "dropped entirely"
    )
    assert len(_paragraphs_of(story)) == STORY_MAX_PARAGRAPHS
    assert story.endswith(_closed(STORY_MAX_PARAGRAPHS)), (
        "the story must end at a PARAGRAPH boundary, with the last kept "
        f"paragraph intact; it ended {story[-40:]!r}. A character cut applied "
        "after the join would land here (§2.5: a difference in kind, not "
        "degree)"
    )
    assert clipped is True, "a whole paragraph was dropped, so the cap fired"


def test_paragraphs_summing_past_the_character_bound_are_dropped_whole() -> None:
    """The second dimension, isolated: the char backstop with the cap idle.

    THE FIXTURE IS THE ARGUMENT. Exactly ``STORY_MAX_PARAGRAPHS`` paragraphs,
    so the paragraph cap **cannot** explain a single drop — only
    ``STORY_MAX_CHARS`` can. That is what makes this a witness for the char
    bound rather than a second witness for the cap.

    Two survive, for any value of ``STORY_MAX_CHARS``. Each paragraph is
    ``STORY_MAX_CHARS // 3`` characters, so two of them plus one separator is
    ``2 * (C // 3) + 2``, comfortably under ``C``; three plus two separators is
    ``3 * (C // 3) + 4``, which is ``C - (C % 3) + 4`` and therefore always
    **greater** than ``C``, since ``C % 3`` is at most 2. The expected count is
    stated rather than recomputed — recomputing the clamp's own arithmetic in
    the test would make the assertion tautological.
    """
    assert STORY_MAX_PARAGRAPHS >= 3, (
        "fixture: this construction needs room for a third paragraph that "
        f"the char bound can refuse; STORY_MAX_PARAGRAPHS is "
        f"{STORY_MAX_PARAGRAPHS}"
    )
    chunk = STORY_MAX_CHARS // 3
    text = _story_of(STORY_MAX_PARAGRAPHS, chunk)
    assert len(_paragraphs_of(text)) == STORY_MAX_PARAGRAPHS, (
        "fixture: the paragraph cap must not be able to fire here, or this "
        "stops being a test of the character bound"
    )
    assert len(text) > STORY_MAX_CHARS, (
        f"fixture: {len(text)} characters must exceed "
        f"STORY_MAX_CHARS={STORY_MAX_CHARS} or there is nothing to bound"
    )

    story, clipped = _clamp_story(text)

    # Positive control FIRST, again: the paragraphs that fit are present.
    for n in (1, 2):
        assert _opened(n) in story, (
            f"paragraph {n} fits inside STORY_MAX_CHARS and must survive; "
            f"got {len(_paragraphs_of(story))} paragraph(s)"
        )
    for n in range(3, STORY_MAX_PARAGRAPHS + 1):
        assert _opened(n) not in story, (
            f"paragraph {n} cannot be added without passing "
            f"STORY_MAX_CHARS={STORY_MAX_CHARS}, so it and everything after "
            "it must be dropped whole"
        )
    assert story == _story_of(2, chunk), (
        "the surviving paragraphs must be exactly the first two, rejoined "
        "with the separator and otherwise untouched"
    )
    assert len(story) <= STORY_MAX_CHARS
    assert story.endswith(_closed(2)), (
        "the char bound must also cut at a paragraph boundary — the only two "
        "honest answers for a paragraph that will not fit are keep it and "
        f"drop it. It ended {story[-40:]!r}"
    )
    assert clipped is True


def test_a_single_over_long_paragraph_survives_whole_and_is_not_marked() -> None:
    """The first-paragraph exemption — the asymmetry a reader will "tidy" away.

    A single paragraph longer than ``STORY_MAX_CHARS`` cannot be shortened at a
    paragraph boundary at all, so "accumulate while the length fits" would
    accumulate **nothing** and return ``""``. That empty would then travel as
    absence (``_ai_story`` returns ``None`` on an unusable answer), and §2.10's
    ``story_missing_rate`` reads absence as *failure* with no ablation arm to
    compare against — so a model that answered perfectly well in one long block
    would be recorded as a failed call. The char bound yields to the paragraph
    contract, not the reverse.

    And nothing was dropped, so ``clipped`` is ``False``. That is the case a
    naive test gets backwards: it is tempting to read "longer than the bound"
    as "the cap fired".

    THE POSITIVE CONTROL IS THE FIRST HALF OF THIS TEST, and it is doing real
    work rather than ceremony: it proves the exemption is confined to the
    *first* paragraph. Without it, an implementation that simply never enforced
    the char bound at all would pass the second half.
    """
    over_long = _paragraph(1, STORY_MAX_CHARS + 500)

    # POSITIVE CONTROL: the same over-long paragraph WITH a tail. The tail
    # cannot fit, so it is dropped and the flag fires. The exemption covers
    # the first paragraph only.
    with_tail, tail_clipped = _clamp_story(
        over_long + _STORY_PARAGRAPH_SEPARATOR + _paragraph(2)
    )
    assert tail_clipped is True, (
        "a tail after an over-long first paragraph cannot fit and must be "
        "dropped, which is a drop and must be flagged"
    )
    assert _opened(2) not in with_tail
    assert with_tail == over_long

    # THE EXEMPTION: alone, it survives entire and nothing is marked.
    story, clipped = _clamp_story(over_long)

    assert story == over_long, (
        "an over-long single paragraph must come back WHOLE; the char bound "
        "is a runaway guard that yields to the paragraph contract"
    )
    assert len(story) > STORY_MAX_CHARS, (
        f"the surviving story is {len(story)} characters, which is the point: "
        f"it is allowed past STORY_MAX_CHARS={STORY_MAX_CHARS}"
    )
    assert story.endswith(_closed(1)), (
        "asserted on the story's LAST CHARACTERS, not merely its length: a "
        "character cut applied after the join is exactly the mutation a "
        f"length assertion cannot see. It ended {story[-40:]!r}"
    )
    assert clipped is False, (
        "nothing was dropped, so the flag must be False — 'longer than the "
        "bound' is not 'the cap fired'"
    )


def test_an_internally_wrapped_paragraph_is_folded_but_blank_lines_survive() -> None:
    """Step 3 of the order, and the substitution §2.5 predicts, in one test.

    Within a paragraph a line wrap carries no meaning, so it folds to a single
    space — and folding here is also what lets the character bound measure the
    text as it will actually be rendered. But the blank line BETWEEN paragraphs
    is the story's structure, which is why 039's ``_clamp_diary_entry``
    (``" ".join(text.split())``) must not be reused: it would fold that away
    too and deliver one wall of text with nothing raising.

    The two assertions are the two directions of that one decision, and a
    clamp that got either half wrong would pass a test carrying only the other.
    """
    text = (
        "First paragraph opens here\nand wraps onto a second line\tand a tab.\n\n"
        "Second paragraph, one line."
    )

    story, clipped = _clamp_story(text)

    assert story == (
        "First paragraph opens here and wraps onto a second line and a tab."
        "\n\n"
        "Second paragraph, one line."
    ), (
        "every whitespace run INSIDE a paragraph folds to one space; the "
        f"blank line between paragraphs does not. Got {story!r}"
    )
    # Positive control before the absence: the paragraph break IS present.
    assert _STORY_PARAGRAPH_SEPARATOR in story, (
        "the blank line between paragraphs must survive (§2.5) — this is the "
        "standing witness against reusing 039's whitespace-folding clamp"
    )
    assert len(_paragraphs_of(story)) == 2
    assert "\n" not in story.replace(_STORY_PARAGRAPH_SEPARATOR, ""), (
        "no single newline may survive inside a paragraph once the fold has "
        f"run. Got {story!r}"
    )
    assert clipped is False, "folding a wrap drops no paragraph"


def test_leading_trailing_and_doubled_breaks_never_spend_a_paragraph_slot() -> None:
    """Step 4: empties are dropped, so they cost neither a slot nor a mark.

    The input carries a leading break, a doubled break in the middle and a
    trailing break around exactly ``STORY_MAX_PARAGRAPHS`` real paragraphs.
    THAT COUNT IS THE INSTRUMENT: if the empties were not dropped they would
    occupy slots in the cap, the last real paragraph would fall off the end,
    and ``clipped`` would come back ``True`` for a story that lost nothing the
    model wrote. So the same fixture witnesses the drop, the cap accounting and
    the flag together.
    """
    real = [_paragraph(n) for n in range(1, STORY_MAX_PARAGRAPHS + 1)]
    text = (
        "\n\n"
        + real[0]
        + "\n\n\n\n"
        + _STORY_PARAGRAPH_SEPARATOR.join(real[1:])
        + "\n\n\n"
    )
    _assert_fits_the_character_bound(text)

    story, clipped = _clamp_story(text)

    # Positive control FIRST: every real paragraph is here.
    for n in range(1, STORY_MAX_PARAGRAPHS + 1):
        assert _opened(n) in story, (
            f"paragraph {n} was lost. If the empty paragraphs produced by the "
            "leading, doubled and trailing breaks are not dropped, they spend "
            "slots in the cap and the LAST real paragraph falls off"
        )
    assert story == _story_of(STORY_MAX_PARAGRAPHS), (
        "the normalised story is exactly the real paragraphs rejoined with a "
        f"single separator. Got {story!r}"
    )
    assert len(_paragraphs_of(story)) == STORY_MAX_PARAGRAPHS
    assert "" not in _paragraphs_of(story), "no paragraph may be nothing"
    assert not story.startswith("\n") and not story.endswith("\n"), (
        "a leading or trailing break must not survive as an empty paragraph"
    )
    assert clipped is False, (
        "dropping an EMPTY paragraph is not dropping a paragraph the model "
        "wrote, so the cap did not fire and the story must not be marked"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param(
            "Alpha.\r\n\r\nBeta.",
            "Alpha.\n\nBeta.",
            id="crlf_blank_line_CONTROL",
        ),
        pytest.param(
            "Alpha.\r\rBeta.",
            "Alpha.\n\nBeta.",
            id="bare_cr_blank_line",
        ),
        pytest.param(
            "Alpha one\r\nalpha two.\r\n\r\nBeta.",
            "Alpha one alpha two.\n\nBeta.",
            id="crlf_wrap_inside_a_paragraph",
        ),
        pytest.param(
            "Alpha one\ralpha two.\r\rBeta.",
            "Alpha one alpha two.\n\nBeta.",
            id="bare_cr_wrap_inside_a_paragraph",
        ),
    ],
)
def test_line_endings_are_normalised_before_the_split(
    text: str, expected: str
) -> None:
    r"""Step 1, and WHICH HALF OF IT EACH ROW ACTUALLY WITNESSES — measured.

    The clamp normalises with ``.replace("\r\n", "\n").replace("\r", "\n")``,
    in that order, and the docstring's reason for it ("so a ``\r\n`` answer is
    not one paragraph whose interior happens to contain carriage returns") does
    **not** survive checking against the *widened* break pattern. Probed
    against a standalone replica of normalise → split → fold, with step 1
    deleted in each of its three possible ways:

    * ``crlf_blank_line`` is **insensitive to step 1 entirely** — all four
      variants (both replaces, either one alone, neither) return the same two
      paragraphs. ``\n\s*\n`` matches ``"\n\r\n"`` because ``\s`` includes
      ``\r``, and the per-paragraph fold then strips the stray ``\r`` anyway.
      **So a CRLF fixture is a control, not a witness**, and this row is
      labelled as one. That is worth knowing rather than quietly working
      around: the widening documented at ``_STORY_PARAGRAPH_BREAK`` absorbed
      most of step 1's stated job.
    * ``bare_cr_blank_line`` witnesses the ``.replace("\r", "\n")`` half, and
      step 1 as a whole. Old-Mac line endings carry no ``\n`` at all, so the
      break pattern cannot match and the two paragraphs fold into one.
    * ``crlf_wrap_inside_a_paragraph`` witnesses **the order of the two
      replaces**, which nothing else can see. With the ``\r\n`` replace
      deleted, ``\r\n`` becomes ``\n\n`` and every CRLF *line wrap* is
      promoted to a paragraph break: three paragraphs where the model wrote
      two, which then spends the cap on structure the model did not intend.
    """
    # Positive control before the absence assertion below: the input really
    # does carry the character whose disappearance is asserted.
    assert "\r" in text, "fixture: this row is vacuous without a carriage return"

    story, clipped = _clamp_story(text)

    assert story == expected, (
        f"{text!r} must normalise to {expected!r}; got {story!r}"
    )
    assert len(_paragraphs_of(story)) == 2, (
        "the answer holds two paragraphs however its lines end; the clamp "
        f"found {len(_paragraphs_of(story))}. Got {story!r}"
    )
    assert "\r" not in story, (
        f"no carriage return may reach the message. Got {story!r}"
    )
    assert clipped is False


@pytest.mark.parametrize(
    "break_text",
    [
        pytest.param("\n \n", id="space_between_newlines"),
        pytest.param("\n\t\n", id="tab_between_newlines"),
        pytest.param("\n   \n", id="spaces_between_newlines"),
        pytest.param("\n\n\n\n", id="four_newlines"),
    ],
)
def test_a_blank_line_carrying_whitespace_still_separates_paragraphs(
    break_text: str,
) -> None:
    """Step 2, deliberately WIDER than §2.5's "runs of two-or-more newlines".

    ``\\n\\s*\\n`` is a superset of ``\\n{2,}`` by exactly the whitespace a run
    can carry between its newlines, and the widening has a live reason: a
    trailing space before a newline is invisible in the model's own output and
    common in generated prose. Under the narrow pattern one of them anywhere in
    the answer silently merges two paragraphs into one — the cap then measures
    too few paragraphs AND the fold runs the two together into a wall of text.
    So the narrow form fails in the direction of not enforcing the bound while
    destroying the structure the clamp exists to keep.

    The ``four_newlines`` row is the CONTROL: it is the one blank line the
    narrow pattern handles too, so it stays green when the pattern is narrowed
    while the other three go red. That is what shows the sweep is measuring
    the widening specifically and not the split as a whole.
    """
    text = _paragraph(1) + break_text + _paragraph(2)
    _assert_fits_the_character_bound(text)

    story, clipped = _clamp_story(text)

    assert len(_paragraphs_of(story)) == 2, (
        f"{break_text!r} is a blank line and must separate paragraphs; the "
        f"clamp returned {len(_paragraphs_of(story))}. Got {story!r}"
    )
    assert story == _story_of(2), (
        "however the break was written, it is rejoined as exactly one "
        f"separator. Got {story!r}"
    )
    assert clipped is False


@pytest.mark.parametrize(
    "count,chars,expect_clipped",
    [
        pytest.param(STORY_MAX_PARAGRAPHS, _SHORT, False, id="at_the_cap"),
        pytest.param(STORY_MAX_PARAGRAPHS - 1, _SHORT, False, id="under_the_cap"),
        pytest.param(STORY_MAX_PARAGRAPHS + 1, _SHORT, True, id="past_the_cap"),
        pytest.param(1, STORY_MAX_CHARS + 500, False, id="over_long_single"),
    ],
)
def test_the_clamp_flag_reports_a_drop_and_only_a_drop(
    count: int, chars: int, expect_clipped: bool
) -> None:
    """``clipped`` is a COUNT comparison, and these four rows say why.

    It is ``len(kept) < len(paragraphs)`` — knowable only inside the clamp,
    between the split and the join. The ``over_long_single`` row is the one a
    naive implementation gets wrong in both available directions: measured off
    the finished story it looks clipped (it is longer than the bound), and
    measured off the paragraph count it looks clipped under any implementation
    that treats "reached the cap" as "fired".
    """
    story, clipped = _clamp_story(_story_of(count, chars))

    assert clipped is expect_clipped, (
        f"{count} paragraph(s) of {chars} characters: expected "
        f"clipped={expect_clipped}, got {clipped}"
    )
    # Controls, so no row above can pass while the story itself is mangled:
    # the clamp returned something, and it ends on a whole paragraph.
    assert story, "control: the row must be measured on a real story"
    assert story.endswith("-CLOSE"), (
        "control: the surviving story must end on a whole paragraph's closing "
        f"sentinel, so the flag is being read off an intact clamp. Ended "
        f"{story[-40:]!r}"
    )


def test_a_truthy_answer_never_clamps_to_nothing() -> None:
    """The invariant behind a guard Task 5.1 deliberately did NOT write.

    ``_ai_story`` gates on ``result.text.strip()`` and then does **not** re-test
    the clamp's output, because ``if not story: return None`` there would be
    unreachable: one non-whitespace character lands in some paragraph, survives
    the fold, and the first-paragraph exemption keeps it whatever its length.
    An unreachable branch is untestable, therefore unwitnessable, therefore
    deletable in silence — the vacuous-guard trap Task 10.1 recorded — so the
    invariant lives here as a property instead of there as a dead branch. If
    this test ever goes red, the guard becomes necessary rather than vacuous
    and ``_ai_story`` must grow it: the alternative is an EMPTY story message,
    a blank section in the ending, which the functional spec forbids.

    THE POSITIVE CONTROL COMES FIRST and is load-bearing: the property is an
    absence claim ("never empty"), so it is worthless unless an empty clamp is
    observable at all. It is — for a whitespace-only answer, which is precisely
    the input ``_ai_story``'s own truthiness gate rejects before the clamp
    sees it.

    WHY THE PROPERTY HOLDS RATHER THAN MERELY PASSING ON THESE ROWS, so a
    reader knows what a red here would mean: ``str.strip()`` and argument-less
    ``str.split()`` use the **same** definition of whitespace, so an answer
    with a truthy ``strip()`` contains at least one character that ``split()``
    cannot discard, and that character is therefore in some paragraph after
    the fold. Only the two bounds can then remove it, and neither can remove
    the first paragraph — which is exactly what the first-paragraph exemption
    buys. A red row here means one of those three facts stopped being true.
    """
    # POSITIVE CONTROL: emptiness IS reachable and IS observable.
    assert _clamp_story("\n\n\t ") == ("", False), (
        "the clamp does return an empty story for a whitespace-only answer — "
        "which is what makes the property below a real claim. That input is "
        "unreachable in production because _ai_story's truthiness gate "
        "rejects it first (test_an_unusable_answer_yields_no_story_at_all)"
    )

    for text in (
        "x",
        ".",
        "\t.\t",
        "\n\n\nx\n\n\n",
        "\r\n\r\nx\r\n",
        " \n \n x \n \n ",
        "\n \nonly a tail",
        "y" * (STORY_MAX_CHARS * 2),
        _story_of(STORY_MAX_PARAGRAPHS + 3),
    ):
        assert text.strip(), f"fixture: {text!r} must be truthy after strip()"
        story, _ = _clamp_story(text)
        assert story, (
            f"a truthy answer clamped to nothing: {text!r} -> {story!r}. "
            "_ai_story would return None and the ending would silently lose "
            "its story, counted by story_missing_rate as a FAILED call"
        )


# ==========================================================================
# The plumbing — what no clamp test can see
# ==========================================================================


def test_the_clamp_is_applied_to_the_story_that_reaches_the_message(
    env: Path,
    fake_large: Callable[..., Any],
) -> None:
    """THE STARKEST GAP: the clamp reaching the message at all.

    Nothing else in the suite notices if the call site reverts to Slice 1's
    ``result.text.strip()`` — measured twice before this file existed (Task
    5.1, and again by the dispatching author): the suite stayed byte-for-byte
    identical at 2699 passed / 1 skipped. Re-measured with this file in place:
    that mutation now turns exactly three tests red, all of them here, and
    every one of the 510 pre-existing items in the census-derived witness set
    still passes. The 94 census items that reach the clamp all feed it stories
    inside both bounds, so a pass-through and a deleted clamp look the same to
    every one of them.

    Only a node-level test with a scripted story PAST the bound can tell them
    apart, and it needs no driven game: ``moderator_story`` is directly
    callable with a hand-built state and one ``Story``.
    """
    scripted = _story_of(STORY_MAX_PARAGRAPHS + 1)
    _assert_fits_the_character_bound(scripted)
    fake = fake_large(stories=[Story(text=scripted)])

    story = _the_story_message(moderator_story(DIRECT_STATE))

    # Positive control FIRST: the scripted text really did travel, so the
    # absence assertion cannot pass on an empty or unrelated message.
    for n in range(1, STORY_MAX_PARAGRAPHS + 1):
        assert _opened(n) in story.content, (
            f"paragraph {n} of the scripted story must reach the message"
        )
    assert _opened(STORY_MAX_PARAGRAPHS + 1) not in story.content, (
        "the paragraph past the cap must not reach the message — this is the "
        "assertion that fails if the clamp is not called on the way out"
    )
    assert len(_paragraphs_of(story.content)) == STORY_MAX_PARAGRAPHS
    assert story.content.endswith(_closed(STORY_MAX_PARAGRAPHS)), (
        "and the message must end at a paragraph boundary, not mid-sentence"
    )
    assert fake.calls_by_schema[Story] == 1, (
        "the story call must have been MADE; a zero means fake_large no "
        "longer re-points graphia.nodes.endgame.get_large and the ending's "
        "no-story outcome is a swallowed RuntimeError"
    )


def test_the_truncation_flag_reaches_the_message_only_when_the_cap_fired(
    env: Path,
    fake_large: Callable[..., Any],
) -> None:
    """The flag's two states, at the message, in one test — and why one test.

    ``CLOSING_STORY_TRUNCATED_KWARG`` had exactly one writer and no reader
    before this test: its only other appearance anywhere in ``src/`` or
    ``tests/`` was a prose mention in another test module's docstring, which
    reads nothing. It is read from the clamp and never
    re-derived, because an exactly-``STORY_MAX_PARAGRAPHS`` story and a longer
    one that lost its last are indistinguishable afterwards — so the two halves
    below are only jointly a witness. Either alone can be satisfied by a
    re-derivation: ``>= STORY_MAX_PARAGRAPHS`` measured off the finished story
    passes the clipped half and fails the unclipped one; ``>`` passes the
    unclipped half and fails the clipped one.

    THE ABSENCE IS ASSERTED KEY-WISE, DELIBERATELY, and not by re-pinning the
    whole ``additional_kwargs``. ``test_spec040_story_node``'s
    ``test_the_node_returns_a_message_and_nothing_else`` already pins the
    unclipped dict exactly, and it is right to: an unclipped ending's kwargs
    must read exactly as they did before Task 5.3. A second whole-dict pin here
    would only mean two tests failing for one defect.

    The flag is ABSENT rather than ``False`` on an unclipped story: every
    reader is ``extra.get(KEY)``, so absent and ``False`` are one answer, and
    ``<story truncated="false">`` on all but a handful of stories would be
    noise in a corpus a person reads.
    """
    clipped_text = _story_of(STORY_MAX_PARAGRAPHS + 1)
    intact_text = _story_of(STORY_MAX_PARAGRAPHS)
    _assert_fits_the_character_bound(clipped_text)
    _assert_fits_the_character_bound(intact_text)
    fake = fake_large(
        stories=[Story(text=clipped_text), Story(text=intact_text)]
    )

    # POSITIVE HALF FIRST: the cap fired, so the mark is there.
    marked = _the_story_message(moderator_story(DIRECT_STATE))
    assert marked.additional_kwargs.get(CLOSING_STORY_TRUNCATED_KWARG) is True, (
        "a story that lost a whole paragraph must carry "
        f"{CLOSING_STORY_TRUNCATED_KWARG!r}; kwargs were "
        f"{marked.additional_kwargs!r}. Without it a reviewer judging the "
        "prose attributes an early stop to the storyteller rather than to the "
        "cap — exactly the wrong conclusion about the one thing the feature "
        "is meant to be judged on"
    )
    assert len(_paragraphs_of(marked.content)) == STORY_MAX_PARAGRAPHS, (
        "control: the mark must accompany a story that really was clipped"
    )

    # NEGATIVE HALF: nothing was dropped, so the key is not there at all.
    plain = _the_story_message(moderator_story(DIRECT_STATE))
    assert plain.additional_kwargs.get(CLOSING_STORY_KWARG) is True, (
        "control for the absence below: the message does carry kwargs, so an "
        "absent truncation key means 'not marked' and not 'no kwargs at all'"
    )
    assert CLOSING_STORY_TRUNCATED_KWARG not in plain.additional_kwargs, (
        "an unclipped story must not carry the truncation key at all — not "
        f"even set to False. kwargs were {plain.additional_kwargs!r}"
    )
    assert plain.content == intact_text, (
        "control: the unclipped story reached the message untouched"
    )
    assert fake.calls_by_schema[Story] == 2


def test_the_first_paragraph_exemption_survives_the_plumbing(
    env: Path,
    fake_large: Callable[..., Any],
) -> None:
    """The exemption asserted at the MESSAGE, which is the only proof it survived.

    The clamp-level version of this claim is
    ``test_a_single_over_long_paragraph_survives_whole_and_is_not_marked``.
    This one is not a duplicate: the exemption now has **two** assertable
    surfaces — the clamp's tuple and the message's ``additional_kwargs`` — and
    only the second shows that a story longer than ``STORY_MAX_CHARS`` did not
    acquire a truncation mark somewhere between the clamp and the
    ``SystemMessage``. A flag re-derived out here by measuring the story's
    length would mark it, and every clamp test in this file would stay green.

    It is also where a character cut applied after the join would show, since
    this is the only path on which the joined story exceeds the bound at all.
    """
    over_long = _paragraph(1, STORY_MAX_CHARS + 500)
    fake = fake_large(
        stories=[
            Story(text=over_long + _STORY_PARAGRAPH_SEPARATOR + _paragraph(2)),
            Story(text=over_long),
        ]
    )

    # POSITIVE CONTROL FIRST: the same over-long paragraph with a tail IS
    # marked, so the absence below is a statement about the exemption and not
    # about a flag that never fires on this shape.
    with_tail = _the_story_message(moderator_story(DIRECT_STATE))
    assert (
        with_tail.additional_kwargs.get(CLOSING_STORY_TRUNCATED_KWARG) is True
    ), (
        "a tail dropped after an over-long first paragraph IS a drop and must "
        f"be marked; kwargs were {with_tail.additional_kwargs!r}"
    )
    assert _opened(2) not in with_tail.content

    # THE EXEMPTION AT THE MESSAGE.
    alone = _the_story_message(moderator_story(DIRECT_STATE))
    assert alone.content == over_long, (
        f"an over-long single paragraph must reach the player WHOLE; the "
        f"message was {len(alone.content)} characters against an answer of "
        f"{len(over_long)}"
    )
    assert len(alone.content) > STORY_MAX_CHARS
    assert alone.content.endswith(_closed(1)), (
        "asserted on the last characters rather than the length: this is the "
        "one path where the joined story exceeds STORY_MAX_CHARS, so it is "
        "the only place a post-join character cut is visible at all"
    )
    assert CLOSING_STORY_TRUNCATED_KWARG not in alone.additional_kwargs, (
        "nothing was dropped, so the story must not be marked — a flag "
        "re-derived from the story's length or paragraph count out here would "
        f"mark it. kwargs were {alone.additional_kwargs!r}"
    )
    assert alone.additional_kwargs.get(CLOSING_STORY_KWARG) is True, (
        "control: the message does carry kwargs"
    )
    assert fake.calls_by_schema[Story] == 2
