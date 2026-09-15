"""Pure, terminal-free unit tests for the transcript-search matchers (spec 044).

Covers the pure search layer of tech-spec §2.1 — the two matchers Slice 2
task 2.1 added to :mod:`graphia.eval_ledger` beside ``tokenize_transcript``
(:func:`search_is_case_sensitive` and :func:`find_matches`) and, from Slice 3
task 3.7, the row arithmetic they feed (:func:`wrap_row_starts` and
:func:`row_for_offset`) as the committed corpus exercises it. No Textual, no
``run_test()``, no ``App``, no widget: this module imports one production module
and the stdlib.

**Why the split, in one measured number.** ``tests/test_ledger_viewer.py`` runs
90 items in ~57 s (≈0.63 s per ``run_test()`` item); ``tests/test_transcript_
highlight.py`` runs 305 items in ~2 s. That is ~100x per item, and it is the
whole reason this file exists rather than these criteria living in section B15
of the viewer's suite. This module plays for the *matcher* exactly the role
``test_transcript_highlight.py`` plays for the *tokenizer* (tech-spec §4): every
criterion that is stateable without a terminal is stated here, and only the ones
that would become *unstateable* without one — "does ``n`` reach the screen rather
than get typed into the box" — go through a Pilot.

The rule that decides placement, restated so a later reader does not have to
re-derive it: **"which text matches" is not a Textual fact.** Functional-spec
§2.7 (smart case) and the matching half of §2.2 are statements about a string and
a query. Colour, scrolling and focus are the widget's, and they are asserted
there.

--- What is here ---

1. **Smart case, both halves** (functional-spec §2.7's three criteria). The
   verdict function is tested directly *and* through ``find_matches``, because
   the two can drift: a correct verdict paired with a flag the matcher ignores
   passes a verdict-only suite. Includes the **``miRa`` boundary the functional
   spec leaves unstated** — tech-spec §2.1 pins it as case-*sensitive*, so it
   matches nothing in a text holding only *Mira* and *mira*, and that decision is
   asserted here rather than left to whatever the implementation happened to do.

2. **The length-changing-lowercase trap**, which is the reason the contract says
   ``re.finditer`` and *"never ``text.lower().find(...)``"*. ``'İ'`` (U+0130)
   lowercases to **two** characters, so every offset after it shifts by one in
   lowercase space. This is pinned as a regression test with a **non-vacuity
   precondition** beside it (the naive answer really is wrong on this input and
   on this Python), so a future "simplification" back to ``.lower().find()`` —
   which reads harmless and passes every ASCII test anyone would think to write —
   fails loudly instead of quietly mis-highlighting model-generated proper nouns.
   The assertion is deliberately made by **slicing the original text** with the
   returned offsets, because that is the property the widget actually consumes.

3. **Narrowing and widening** (§2.2 AC2/AC3) as a pure property. A keystroke is
   a longer query and a Backspace is a shorter one; "the highlighting narrows"
   means the longer query's matches sit inside the shorter one's, and "it widens
   again" means the shorter query's answer is restored **byte for byte**. Neither
   needs a terminal to state.

4. **Everything the game shows is searched** (§2.2 AC5/AC6 and the sentence above
   them): private thoughts, the opening cast list, the moderator's lines, the
   section markers and the recap. Asserted by locating the match *inside the
   enclosing element* of a small synthetic transcript written in the real
   structured format — the same shape ``test_transcript_highlight.py``'s
   ``_RICH_SYNTHETIC_TRANSCRIPT`` uses, so what a match means is checkable by eye.

5. **The contract edges**: an empty query, non-overlap, regex metacharacters
   matched literally, and a whitespace-only query honoured as a real search.

6. **The committed corpus, swept as PROPERTIES and never as counts** (task
   3.7, tech-spec §4). One item per run directory, each with a coverage counter,
   over all 420 committed games: every reported match round-trips through its
   own offsets, the row starts partition ``[0, len(text))`` contiguously, every
   match resolves to a row holding its head — straddling a fold into the
   immediate successor row when it must — and the two preconditions the wrap
   mirror rests on still hold (zero tabs, zero Rich-stripped control codes).
   Golden per-file counts are deliberately absent: the corpus grew 298 → 420
   since spec 038 and gains a directory on every eval run, so a pinned number is
   a test that fails on a green tree.

7. **Row-keyed stepping** (functional-spec §2.6, Slice 4 task 4.1). ``n`` and
   ``N`` skip past every match sharing the row the reviewer is on and go to the
   next row that has one — and tasks.md says where that belongs in as many
   words: *"cover the row-keyed skip in the pure tests too — it is a statement
   about ``next_match``, not about Textual."* So the truth table for
   :func:`step_match_by_row` is here, in both directions, with both wraps, with
   the two consequences the author accepted explicitly (the running count jumps
   4 → 7; the first match on a row is the one that becomes current, whichever
   way the reviewer arrived at it), and with the **Enter/``n`` asymmetry** stated
   as one assertion on the two functions from a single row. The corpus then says
   the skip is not a fixture's invention: on the largest committed game the walk
   forward from the end visits **every** row that holds a match, exactly once,
   in order, and wraps.

--- What is deliberately NOT here ---

* **Golden match counts per game.** See item 6 for why they would rot.
* **Sweeping more than one width over the corpus.** Rich's wrapping is Rich's
  contract, not ours: ``tests/test_ledger_viewer.py``'s B15.6 three-legged pin
  covers agreement with the live render, and the synthetic fixtures beside it
  cover width variety. The corpus sweep fixes one width, **96** — the measured
  live ``body.content_size.width`` of a game that overflows a 100-column
  terminal.
* **Any corpus sweep through ``run_test()``** — 420 Textual sessions is ~4.5
  minutes (tech-spec §4). Exactly one real committed game goes through a
  terminal, and it does so in the viewer's own suite, not here.
* **Where the game SCROLLS to.** Section 7 states which match ``n`` answers;
  turning that index into a row, clamping it at the end of the game, painting
  the marker and writing the header are the widget's, and they are asserted in
  ``tests/test_ledger_viewer.py``'s B15.5.
* **Colour, themes, snapshots and timings** (tech-spec §4, *"what is deliberately
  not tested"*). A background-only span makes no per-theme contrast claim;
  ``snap_compare`` appears in zero test files in this repo; and there are no
  wall-clock assertions anywhere in the suite.

--- Offline by construction ---

``graphia.eval_ledger`` is the pure, Textual-free, I/O-free layer, so the autouse
``safe_llm`` net in ``conftest.py`` is simply irrelevant here, exactly as it is
for ``tests/test_ledger_model.py`` and ``tests/test_transcript_highlight.py``.
Everything above section 6 works on module-level strings. Section 6 **reads**
the committed corpus under ``evals/transcripts/`` — read-only, through
``Path.read_text``, never through the viewer and never through a terminal — and
writes nothing anywhere. ``evals/blunder-ledger.yaml`` is never touched, by
either half.

The real production symbols are imported directly (never re-implemented locally),
so a rename breaks these tests — which is the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphia import eval_ledger
from graphia.eval_ledger import (
    find_matches,
    next_match,
    offset_for_row,
    prev_match,
    row_for_offset,
    search_is_case_sensitive,
    step_match_by_row,
    wrap_row_starts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slices(text: str, query: str) -> list[str]:
    """The matched runs, read back out of the ORIGINAL text by their offsets.

    ``find_matches`` returns offsets, not substrings, and the contract is that
    ``text[start:end]`` *is* the matched run — the widget highlights by slicing
    and scrolls by offset, so an offset that points at the right text in some
    other string is worth nothing. Every readability-minded assertion below goes
    through here rather than comparing offset pairs, so a failure message shows
    what was actually found instead of a list of integers.
    """
    return [text[start:end] for start, end in find_matches(text, query)]


def _starts(text: str, query: str) -> list[int]:
    """Just the start offsets, for the narrowing/widening set relations."""
    return [start for start, _ in find_matches(text, query)]


def _element_span(text: str, opening: str, closing: str) -> tuple[int, int]:
    """The half-open offset range of one element of the synthetic transcript.

    ``opening`` may be a tag *prefix* (``'<thought'``) so a tag carrying
    attributes is still addressable without spelling the attributes out twice.
    Both ends are located with ``str.index``, so a fixture edit that removes the
    element fails here with a ``ValueError`` naming it rather than silently
    making the containment assertion vacuous.
    """
    start = text.index(opening)
    end = text.index(closing, start) + len(closing)
    return start, end


# ===========================================================================
# 1. Smart case (functional-spec §2.7, tech-spec §2.1)
# ===========================================================================
#
# The rule: a query with NO uppercase character matches any casing; ANY uppercase
# character ANYWHERE makes the whole query exact. "Anywhere" is the half the
# functional spec never states, and it is the half a plausible implementation
# gets wrong — `query[0].isupper()` reads like the same rule and silently ignores
# a mid-word capital, so `miRa` and `McKay` would quietly match anything.

_CASE_VERDICTS = [
    # (query, is_case_sensitive, why)
    ("mira", False, "all lowercase -- matches any casing"),
    ("vote", False, "all lowercase"),
    ("i vote", False, "lowercase with a literal space"),
    ("Mira", True, "a leading capital"),
    ("MIRA", True, "all caps"),
    ("miRa", True, "THE UNSTATED BOUNDARY: a mid-word capital still counts"),
    ("mIRA", True, "a capital in position 1 is no different from one in 0"),
    ("voTE", True, "trailing capitals count too"),
    ("İnigo", True, "a non-ASCII capital is a capital"),
    ("", False, "no characters, so no uppercase one -- see the docstring"),
    ("1.5", False, "digits and punctuation have no case"),
    ("vote!", False, "punctuation does not make a query exact"),
    ("   ", False, "whitespace has no case"),
]


@pytest.mark.parametrize(
    ("query", "expected", "why"),
    _CASE_VERDICTS,
    ids=[query or "<empty>" for query, _, _ in _CASE_VERDICTS],
)
def test_any_uppercase_character_anywhere_makes_a_query_case_sensitive(
    query: str, expected: bool, why: str
) -> None:
    """:func:`search_is_case_sensitive` is a property of the WHOLE query.

    Tested directly as well as through :func:`find_matches` because the two can
    drift apart: a correct verdict wired to a matcher that ignores it passes any
    suite that only ever asserts on matches.
    """
    assert search_is_case_sensitive(query) is expected, (
        f"search_is_case_sensitive({query!r}) should be {expected} -- {why}"
    )


# One text, three casings of one name, and NO `miRa` anywhere in it -- which is
# what makes the `miRa` row below an assertion about smart case rather than an
# assertion that the text happens not to contain a string.
_MIRA_CASINGS = "Mira spoke; mira echoed; MIRA shouted."


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # functional-spec §2.7 AC1: lowercase finds every casing.
        ("mira", ["Mira", "mira", "MIRA"]),
        # §2.7 AC2: a capital narrows to exactly that casing.
        ("Mira", ["Mira"]),
        # §2.7 AC3: so does all-caps.
        ("MIRA", ["MIRA"]),
        # THE UNSTATED BOUNDARY (tech-spec §2.1). `miRa` is case-sensitive, and
        # this text holds no `miRa`, so it finds NOTHING. An implementation that
        # only looked at the first character would return all three above.
        ("miRa", []),
        ("mIRA", []),
        ("MirA", []),
    ],
    ids=["mira", "Mira", "MIRA", "miRa", "mIRA", "MirA"],
)
def test_smart_case_decides_which_casings_of_a_name_are_found(
    query: str, expected: list[str]
) -> None:
    """The three §2.7 criteria plus the boundary, read back off the text."""
    assert _slices(_MIRA_CASINGS, query) == expected


def test_a_case_insensitive_query_reports_offsets_into_the_original_casing() -> None:
    """Offsets are half-open pairs into the text as written, not as lowercased.

    Pinned absolutely rather than derived, so the pair semantics themselves are
    checkable by eye against ``_MIRA_CASINGS``: *Mira* at 0, *mira* at 12, *MIRA*
    at 25, each four characters long.
    """
    assert find_matches(_MIRA_CASINGS, "mira") == [(0, 4), (12, 16), (25, 29)]
    # ...and the end offset is EXCLUSIVE: the character at `end` is the one after
    # the match, never part of it.
    assert _MIRA_CASINGS[0:4] == "Mira"
    assert _MIRA_CASINGS[4] == " "


# ===========================================================================
# 2. The length-changing-lowercase trap (tech-spec §2.1, risk table)
# ===========================================================================
#
# This is the single reason the contract says `re.finditer(re.escape(q), text,
# flags)` and "never text.lower().find(...)". `'İ'` (U+0130, LATIN CAPITAL LETTER
# I WITH DOT ABOVE) lowercases to TWO characters -- 'i' + U+0307 COMBINING DOT
# ABOVE -- so a lowercased copy of the text is LONGER than the text, and every
# offset found in the copy is shifted for everything that follows.
#
# Measured (Python 3.14.3): it is the ONLY character in the entire codepoint
# range whose `.lower()` changes length. That is exactly what makes the bug so
# quiet: it cannot be found by trying "some accented text", only by knowing.
#
# These transcripts are full of model-generated proper nouns, which is where such
# a character would actually turn up.

_TURKISH_I_LINE = "İnigo says: vote now"

# Where `vote` really is, and where a lowercased search would have said it was.
_TRUE_VOTE_OFFSET = 12
_NAIVE_VOTE_OFFSET = 13


def test_the_naive_lowercase_search_really_is_wrong_on_this_input() -> None:
    """NON-VACUITY: prove the trap is live before asserting that it is avoided.

    Without this, the regression test below would keep passing on a future Python
    that changed ``'İ'.lower()`` -- while quietly testing nothing at all. Here the
    precondition fails loudly instead, and the reader is told exactly which
    assumption moved.

    Everything asserted here is about :mod:`str`, not about Graphia. It is the
    *specification of the bug* that :func:`find_matches` must not have.
    """
    assert len(_TURKISH_I_LINE.lower()) == len(_TURKISH_I_LINE) + 1, (
        "'İ'.lower() is expected to be two characters, which is what shifts "
        "every later offset in lowercase space"
    )
    assert _TURKISH_I_LINE.lower().find("vote") == _NAIVE_VOTE_OFFSET
    # ...and slicing the ORIGINAL text at that answer returns the wrong run --
    # off by one, mid-word, and highlighted a character into the next word.
    naive = _TURKISH_I_LINE[_NAIVE_VOTE_OFFSET : _NAIVE_VOTE_OFFSET + 4]
    assert naive == "ote ", f"expected the shifted slice 'ote ', got {naive!r}"


@pytest.mark.parametrize(
    ("query", "expected_slices"),
    [
        # The measured case: the word AFTER the length-changing character.
        ("vote", ["vote"]),
        # A word before it is unaffected either way, so it is the control that
        # says the fixture is a normal string apart from one character.
        ("İnigo", ["İnigo"]),
        # Case-insensitively, `re.IGNORECASE` still reaches the capital form --
        # so avoiding `.lower()` costs nothing in what is findable.
        ("inigo", ["İnigo"]),
        # Two words past it: the shift is cumulative in lowercase space, not
        # local to the character.
        ("now", ["now"]),
    ],
    ids=["vote", "İnigo", "inigo", "now"],
)
def test_a_length_changing_lowercase_character_does_not_shift_later_offsets(
    query: str, expected_slices: list[str]
) -> None:
    """Slicing the ORIGINAL text with the returned offsets yields the query.

    The assertion is made by slicing rather than by comparing offset numbers
    because slicing is the property the widget consumes -- it highlights
    ``text[start:end]`` and scrolls to ``start``. An offset that is right about
    some lowercased copy of the text is simply wrong here.
    """
    assert _slices(_TURKISH_I_LINE, query) == expected_slices


def test_the_trap_word_is_found_at_its_true_offset_not_the_shifted_one() -> None:
    """The one absolute pin of the trap, stated as the pair it must return.

    A ``text.lower().find(...)``-style implementation returns
    ``[(13, 17)]`` here -- it is off by exactly the one character ``'İ'`` gained
    -- so this assertion is what a "simplification" back to lowercasing has to
    get past.
    """
    assert find_matches(_TURKISH_I_LINE, "vote") == [
        (_TRUE_VOTE_OFFSET, _TRUE_VOTE_OFFSET + len("vote"))
    ]


# ===========================================================================
# 3. Narrowing on a keystroke, widening on Backspace (§2.2 AC2/AC3)
# ===========================================================================
#
# A keystroke is a longer query; a Backspace is a shorter one. The widget's job
# is to re-highlight; the matcher's job -- all of it -- is that the longer query's
# matches sit INSIDE the shorter query's, and that going back restores the wider
# answer exactly. Both are stateable with no terminal at all.
#
# Every case below is chosen so the narrowing is PROPER (the prefix has strictly
# more matches), because a case where the two answers coincide would pass a
# `find_matches` that ignored its query's last character.

_TYPING_CASES = [
    # (text, prefix, one-more-character, why the prefix is strictly wider)
    (
        "I vote now; my vote is a pivot, not a verdict. Bo votes early.",
        "vot",
        "vote",
        "`pivot` holds `vot` but not `vote`",
    ),
    (
        "Mira accused Miranda, and Miro said nothing at all.",
        "Mir",
        "Mira",
        "`Miro` holds `Mir` but not `Mira`",
    ),
    (
        "the lighthouse light was lit",
        "li",
        "lig",
        "`lit` holds `li` but not `lig`",
    ),
]


@pytest.mark.parametrize(
    ("text", "prefix", "longer", "why"),
    _TYPING_CASES,
    ids=[f"{prefix}->{longer}" for _, prefix, longer, _ in _TYPING_CASES],
)
def test_typing_one_more_character_narrows_the_matches(
    text: str, prefix: str, longer: str, why: str
) -> None:
    """§2.2 AC2: typing a further character narrows the highlighting.

    Three things, in the order they matter: the narrowing is real (strictly
    fewer), it is a *narrowing* rather than a different answer (every longer
    match starts where a prefix match started), and neither side is empty -- a
    vacuous "0 is a subset of 0" would satisfy the first two.
    """
    wide = _starts(text, prefix)
    narrow = _starts(text, longer)

    assert wide, f"the fixture must match {prefix!r} at all"
    assert narrow, f"the fixture must match {longer!r} at all"
    assert len(narrow) < len(wide), (
        f"typing the extra character must drop at least one match -- {why}"
    )
    assert set(narrow) <= set(wide), (
        "every match of the longer query must start where a match of its prefix "
        "started; anything else is a different search, not a narrowing"
    )


@pytest.mark.parametrize(
    ("text", "prefix", "longer", "why"),
    _TYPING_CASES,
    ids=[f"{longer}->{prefix}" for _, prefix, longer, _ in _TYPING_CASES],
)
def test_backspace_widens_the_matches_back_to_exactly_what_they_were(
    text: str, prefix: str, longer: str, why: str
) -> None:
    """§2.2 AC3: Backspace restores the wider answer BYTE FOR BYTE.

    Written as the reviewer's actual sequence -- type the prefix, type one more,
    delete it -- so what is asserted is the round trip rather than two unrelated
    calls that happen to agree. The matcher is pure, so "restored" must mean
    *identical*, not merely "a superset again".
    """
    before = find_matches(text, prefix)
    _ = find_matches(text, longer)  # the keystroke
    after = find_matches(text, prefix)  # the Backspace

    assert after == before
    assert len(after) > len(find_matches(text, longer)), why


def test_a_whole_typed_word_narrows_monotonically_keystroke_by_keystroke() -> None:
    """Every prefix of a query matches at least as much as the query itself.

    The generalisation of AC2 across a whole typing sequence: a reviewer typing
    ``v``, ``o``, ``t``, ``e`` never sees the highlighting *grow*. Stated as a
    property so it holds for any word, not only for the one pair above.
    """
    text = _TYPING_CASES[0][0]
    counts = [len(find_matches(text, "vote"[: n + 1])) for n in range(len("vote"))]

    assert counts == sorted(counts, reverse=True), (
        f"match counts must not grow as characters are typed, got {counts}"
    )
    assert counts[-1] > 0, "the fixture must still match the whole word"
    assert counts[0] > counts[-1], "the sweep must be non-vacuous"


# ===========================================================================
# 4. The search reaches everything the game shows (§2.2 AC5/AC6)
# ===========================================================================
#
# Functional-spec §2.2: "The search looks at everything the game shows: what
# players said, their private thoughts, the moderator's lines, the section
# markers, and the opening cast list." Two of those are acceptance criteria in
# their own right (AC5 private thoughts, AC6 the cast list); the sweep below
# covers the sentence.
#
# The fixture is written in the REAL structured transcript format -- the shape
# `test_transcript_highlight.py`'s `_RICH_SYNTHETIC_TRANSCRIPT` uses, itself
# copied from the committed corpus -- rather than an invented one, so "inside the
# private thought" means what it means in a real game. It is deliberately small
# enough to read: four elements, two players, one Day.
#
# The matcher has no notion of an element, which is exactly the point: nothing in
# `find_matches` could exclude a thought or a cast entry even if it wanted to.
# What these tests actually pin is that the transcript's OWN text -- the text the
# widget hands over -- carries those regions, so the criteria are discharged
# structurally rather than by a comment.

_SEARCHABLE_GAME = (
    "Game 1 | provider=ollama | large_model=qwen3-coder:30b | games=1\n"
    "<transcript>\n"
    "<setup>\n"
    '<player name="Mira" role="Mafia">\n'
    "Personality: watchful and slow to speak\n"
    "</player>\n"
    '<player name="Bo" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    "</setup>\n"
    "<preamble>\n"
    "Moderator: A new game begins. Welcome, Bo.\n"
    "</preamble>\n"
    "<day>\n"
    "  <round>\n"
    "  Round 1.\n"
    "Mira: I vote for nobody yet.\n"
    "Bo: The lighthouse keeper saw you, Mira.\n"
    '<thought player="Mira">The lighthouse was empty and Bo knows it.</thought>\n'
    '<vote initiator="Bo" target="Mira">\n'
    "Mira: Yes\n"
    "</vote>\n"
    "<recap>Alive: Mira, Bo.</recap>\n"
    "  </round>\n"
    "</day>\n"
    "<endgame>\n"
    "Mafia win.\n"
    "</endgame>\n"
    "</transcript>"
)

_REGION_CASES = [
    # (query, opening, closing, label)
    (
        "watchful",
        "<setup>",
        "</setup>",
        "the opening cast list (§2.2 AC6)",
    ),
    (
        "empty",
        "<thought",
        "</thought>",
        "a private thought (§2.2 AC5)",
    ),
    (
        "A new game begins",
        "<preamble>",
        "</preamble>",
        "the moderator's lines",
    ),
    (
        "<endgame>",
        "<endgame>",
        "</endgame>",
        "a section marker",
    ),
    (
        "Alive",
        "<recap>",
        "</recap>",
        "the moderator's end-of-round recap",
    ),
]


@pytest.mark.parametrize(
    ("query", "opening", "closing", "label"),
    _REGION_CASES,
    ids=[label.split(" (")[0].replace(" ", "-") for *_, label in _REGION_CASES],
)
def test_every_part_of_a_game_is_searched(
    query: str, opening: str, closing: str, label: str
) -> None:
    """A match inside any element of the transcript is found like any other."""
    matches = find_matches(_SEARCHABLE_GAME, query)
    start, end = _element_span(_SEARCHABLE_GAME, opening, closing)

    assert matches, f"{query!r} must be found at all, in {label}"
    assert any(start <= s and e <= end for s, e in matches), (
        f"{query!r} must be found inside {label} -- the element spans "
        f"[{start}, {end}) and the matches are {matches}"
    )
    for s, e in matches:
        found = _SEARCHABLE_GAME[s:e]
        assert found.lower() == query.lower(), (
            f"the offsets must slice back to {query!r}, got {found!r}"
        )


def test_a_name_in_the_cast_list_is_found_there_as_well_as_in_the_game() -> None:
    """§2.2 AC6, stated as *"like any other"* rather than merely *"found"*.

    ``Mira`` appears in the cast entry, in her spoken lines, in another player's
    speech, as the owner of a private thought, as a vote target, in a ballot and
    in the recap. The criterion is that the cast-list occurrence is *one of them*
    -- neither skipped nor treated specially -- so the assertion is both that it
    is present and that it is not alone.
    """
    matches = find_matches(_SEARCHABLE_GAME, "mira")
    cast_start, cast_end = _element_span(_SEARCHABLE_GAME, "<setup>", "</setup>")

    in_cast = [(s, e) for s, e in matches if cast_start <= s and e <= cast_end]
    assert len(in_cast) == 1, (
        f"the cast list holds exactly one `Mira`, found {in_cast}"
    )
    assert len(matches) > len(in_cast), (
        "the rest of the game must match too, or 'like any other' is untested"
    )
    assert all(_SEARCHABLE_GAME[s:e] == "Mira" for s, e in matches)


def test_a_word_in_a_private_thought_is_found_beside_the_spoken_one() -> None:
    """§2.2 AC5, with the spoken occurrence beside it as the comparison.

    ``lighthouse`` is said out loud by Bo and again privately by Mira. Both are
    found, in document order, and the private one is not marked out in any way --
    the matcher returns the same shape of answer for both, which is exactly what
    *"highlighted like any other"* means at this layer.
    """
    matches = find_matches(_SEARCHABLE_GAME, "lighthouse")
    thought_start, thought_end = _element_span(
        _SEARCHABLE_GAME, "<thought", "</thought>"
    )

    assert len(matches) == 2, f"expected the spoken and the private one, got {matches}"
    spoken, private = matches
    assert spoken[0] < thought_start, "the spoken occurrence comes first"
    assert thought_start <= private[0] and private[1] <= thought_end, (
        "the second occurrence sits inside the private thought"
    )
    assert [_SEARCHABLE_GAME[s:e] for s, e in matches] == ["lighthouse"] * 2


def test_a_literal_phrase_crosses_a_space_rather_than_splitting_into_terms() -> None:
    """Why ``row_matches_field`` is not reused (tech-spec §2.1).

    The runs-list filter splits its value on whitespace and **ANDs** the terms;
    that would make ``I vote`` match any line holding ``i`` and ``vote``
    anywhere. Here the space is literal, so the phrase matches as a phrase --
    once, where it is actually written -- and ``vote I`` (both terms present in
    the game, in the wrong order) matches nothing.
    """
    assert _slices(_SEARCHABLE_GAME, "I vote") == ["I vote"]
    assert find_matches(_SEARCHABLE_GAME, "vote I") == []


# ===========================================================================
# 5. The contract's edges
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    ["", "Mira spoke", _SEARCHABLE_GAME],
    ids=["empty-text", "short-text", "whole-game"],
)
def test_an_empty_query_matches_nothing(text: str) -> None:
    """Empty query => ``[]``, whatever the text.

    Not merely a convenience: an empty regex pattern matches zero-width at every
    position, so without the short-circuit this would report ``len(text) + 1``
    matches of nothing -- 2,001 highlighted nothings on a 2 KB game, and a first
    keystroke that flashes the whole file.
    """
    assert find_matches(text, "") == []


@pytest.mark.parametrize(
    ("text", "query", "expected"),
    [
        ("aaaa", "aa", [(0, 2), (2, 4)]),
        ("aaaaa", "aa", [(0, 2), (2, 4)]),
        ("abababa", "aba", [(0, 3), (4, 7)]),
        ("...", "..", [(0, 2)]),
    ],
    ids=["aa-in-aaaa", "aa-in-aaaaa", "aba-in-abababa", "dots"],
)
def test_matches_never_overlap(
    text: str, query: str, expected: list[tuple[int, int]]
) -> None:
    """Non-overlapping, which is what ``less`` does (tech-spec §2.1).

    ``"aa"`` in ``"aaaa"`` is offsets 0 and 2, not 0, 1 and 2. Overlapping
    matches would also make the highlight spans overlap, which the widget has no
    sensible way to paint.
    """
    assert find_matches(text, query) == expected
    # ...and the invariant behind the table: each match starts at or after the
    # previous one's end.
    matches = find_matches(text, query)
    assert all(a[1] <= b[0] for a, b in zip(matches, matches[1:]))


_METACHARACTER_CASES = [
    # (text, query, expected matched runs) -- each query is plain text a reviewer
    # could type, and each would mean something else entirely as a regex.
    ("a.c and abc", ".", ["."]),
    ("a.c and abc", "a.c", ["a.c"]),
    ("say [bold] now", "[bold]", ["[bold]"]),
    ("a*b and ab", "a*", ["a*"]),
    ("one+two", "+", ["+"]),
    ("is it? yes", "it?", ["it?"]),
    ("a|b", "|", ["|"]),
    ("2 ^ 3", "^", ["^"]),
    ("end$ here", "$", ["$"]),
    # An UNBALANCED metacharacter: without `re.escape` this is not merely a wrong
    # answer, it is `re.error: missing ), unterminated subpattern` -- the loudest
    # of the family, and the one a reviewer types by accident most often.
    ("f(x) = y", "(", ["("]),
    ("f(x) = y", "(x)", ["(x)"]),
    ("back\\slash", "\\", ["\\"]),
    # `{2}` is a repetition count as a regex and a literal pair of braces here.
    ("a{2}b", "{2}", ["{2}"]),
]


@pytest.mark.parametrize(
    ("text", "query", "expected"),
    _METACHARACTER_CASES,
    ids=[query for _, query, _ in _METACHARACTER_CASES],
)
def test_a_regex_metacharacter_is_matched_literally(
    text: str, query: str, expected: list[str]
) -> None:
    """The reviewer types plain text and it matches plain text.

    Functional-spec §3 puts pattern searches explicitly out of scope, so every
    one of these is a literal. ``re.escape`` is what makes that true; without it
    ``.`` matches every character, ``[bold]`` becomes a character class matching
    four single letters, and ``(`` raises.
    """
    assert _slices(text, query) == expected


@pytest.mark.parametrize(
    ("query", "expected_count"),
    # `"a b  c a"` holds four spaces -- at 1, 3, 4 and 6 -- one pair of adjacent
    # spaces (one match for `"  "`, non-overlapping) and one space-then-`a`.
    [(" ", 4), ("  ", 1), (" a", 1)],
    ids=["one-space", "two-spaces", "space-then-letter"],
)
def test_a_whitespace_only_query_is_a_real_search(
    query: str, expected_count: int
) -> None:
    """A space is a legitimate, literal query -- nothing strips or rejects it.

    Tech-spec §2.1 measures a single space at 14,524 matches on the 88 KB game,
    so this is not a theoretical case: it is the worst-case keystroke the
    debounce exists for. Only the genuinely empty string short-circuits.
    """
    text = "a b  c a"
    assert len(find_matches(text, query)) == expected_count
    assert all(text[s:e] == query for s, e in find_matches(text, query))


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("Mira spoke", "Bo"),
        ("Mira spoke", "Mira spoke and then some"),
        ("", "Mira"),
        (_SEARCHABLE_GAME, "zzzz"),
    ],
    ids=["absent", "query-longer-than-text", "empty-text", "absent-from-game"],
)
def test_a_query_that_is_not_there_matches_nothing(text: str, query: str) -> None:
    """The no-match state (§2.8 AC2) begins here: an empty list, not an error."""
    assert find_matches(text, query) == []


def test_matches_come_back_in_document_order() -> None:
    """Document order, which is what the jump and the running count both assume.

    ``re.finditer`` scans left to right, so this is true by construction -- and
    that is precisely why it is worth one assertion: a future implementation that
    gathered matches some other way (per line, per element, from a set) would
    break Enter, ``n``/``N`` and the ``3/18`` counter all at once, and nothing
    else in this file would notice.
    """
    matches = find_matches(_SEARCHABLE_GAME, "mira")
    assert matches == sorted(matches)
    assert len(matches) > 1, "the fixture must have enough matches to order"


def test_both_matchers_are_exported_from_the_module() -> None:
    """Rot guard on the public surface (spec 044 Slice 2, task 2.1).

    The widget imports these by name from :mod:`graphia.eval_ledger`; the
    ``__all__`` entry is what says they are that module's contract rather than an
    internal helper free to move.
    """
    assert "search_is_case_sensitive" in eval_ledger.__all__
    assert "find_matches" in eval_ledger.__all__
    assert eval_ledger.find_matches is find_matches
    assert eval_ledger.search_is_case_sensitive is search_is_case_sensitive


# ===========================================================================
# 6. The committed corpus (spec 044 Slice 3, task 3.7) — properties, not counts
# ===========================================================================
#
# Sections 1–5 answer "is the matcher right about a string I can read?". This one
# answers the question those cannot: **is the matcher right about the row map, on
# 15 MB of real model prose that nobody wrote by hand?**
#
# Why the corpus is the only place these four properties can be stated. The
# synthetic fixtures above are, by construction, the inputs someone thought of.
# The committed games are the inputs nobody thought of: 420 of them, 230,451
# rendered rows at width 96, the longest single source line **4,269 characters**
# (that is characters — 4,297 is its length in bytes), and one whitespace-
# delimited token 166 characters long where a model forgot its spaces entirely.
# That last one is the shape that folds *mid-word*, and no hand-written fixture
# was ever going to contain it.
#
# **Properties, never golden counts.** `tasks.md` records this decision and the
# reason is arithmetic: the corpus grew 298 → 420 files since spec 038 and gains
# a run directory on every measured eval run, so `assert len(matches) == 11238`
# is a test that goes red on a green tree the first time someone commits a
# transcript. Everything below is a relation that holds for *any* transcript.

# ---------------------------------------------------------------------------
# Corpus discovery — DUPLICATED from tests/test_transcript_highlight.py
# ---------------------------------------------------------------------------
#
# **Deliberately copied rather than imported**, and the reason is worth one
# paragraph because "don't repeat yourself" argues the other way. Importing
# `_TRANSCRIPTS` from a sibling test module makes that module import at *this*
# module's collection time, couples the two files' collection order, and turns a
# failure in one into a mysterious error in the other — a `pytest -k` selecting
# nothing in `test_transcript_highlight.py` would still pay for its module body,
# and a syntax error there would report as a collection failure *here*. Test
# modules are leaves; they may share via `conftest.py` or via production code,
# never via each other. The block is ~20 lines and its drift risk is nil: if the
# corpus ever moves, both files fail loudly on the same day.
#
# Resolved from THIS FILE, never from the CWD: `pytest` may be invoked from
# anywhere (repo root, `tests/`, an IDE runner) and this sweep must always find
# the same corpus. `tests/` sits directly under the repo root, so `parents[1]` is
# that root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_ROOT = _REPO_ROOT / "evals" / "transcripts"


def _discover_transcripts() -> list[Path]:
    """Every committed transcript, discovered — deliberately never hard-coded.

    Matches ``*.txt`` at any depth under the corpus root: that is the artifact
    form spec 017 writes (``<run-id>/game-NN.txt``) and it keeps a stray
    ``.DS_Store`` — a live hazard on macOS — from failing the suite on an
    undecodable read.
    """
    if not _CORPUS_ROOT.is_dir():
        return []
    return sorted(path for path in _CORPUS_ROOT.rglob("*.txt") if path.is_file())


_TRANSCRIPTS = _discover_transcripts()

# One test item per run directory, not per file: 36 items rather than 420. A run
# dir is the unit the corpus actually grows and shrinks in (spec 017 writes one
# per measured run, `make clean-transcripts` deletes whole ones), so "which runs
# broke" is immediately meaningful, and growth is ~1 item per eval run rather
# than ~20. The per-item coverage counter is what keeps that grouping honest —
# it proves the item swept every file in its dir rather than silently none.
_RUN_DIRS = sorted({path.parent for path in _TRANSCRIPTS})

# A fresh clone can legitimately carry no transcripts: the dir is NOT gitignored
# but its contents are curated commit-or-delete, and a shallow or partial
# checkout may have none. Skipping keeps `pytest -q` green there instead of
# failing on absent input data.
#
# Gated **per test**, never at module level: sections 1–5 above need no corpus at
# all, and a module-level `pytest.skip(allow_module_level=True)` would take all
# 70 of them down with the sweep on a corpus-less checkout, reporting a green run
# that had not exercised the matcher once.
_NO_CORPUS_REASON = (
    f"no committed transcripts found under {_CORPUS_ROOT} — "
    "nothing to sweep in this checkout"
)

_requires_corpus = pytest.mark.skipif(not _TRANSCRIPTS, reason=_NO_CORPUS_REASON)

# The sweep's parameters: one per run dir, or a single explicitly-skipped
# placeholder when there is no corpus. The placeholder is deliberate — an empty
# parameter list is reported according to pytest's `empty_parameter_set_mark`
# setting, which this repo does not pin, so the skip is spelled out here instead
# of inherited from configuration.
_RUN_DIR_PARAMS = [pytest.param(run_dir, id=run_dir.name) for run_dir in _RUN_DIRS] or [
    pytest.param(None, marks=pytest.mark.skip(reason=_NO_CORPUS_REASON), id="no-corpus")
]


def _rel(path: Path) -> str:
    """Path relative to the repo root, for readable failure messages."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:  # pragma: no cover - corpus always lives under the root
        return str(path)


# ---------------------------------------------------------------------------
# What the sweep checks, and with which queries
# ---------------------------------------------------------------------------

# The one width the corpus is swept at. Tech-spec §2.2 measures it: at a
# 100-column terminal a game that overflows renders at
# `body.content_size.width == 96` (100 − 2 scrollbar − 2 padding). Sweeping more
# widths would be testing Rich's wrapping, which is Rich's contract and not ours
# — see this module's docstring.
_CORPUS_WRAP_WIDTH = 96

# The two queries, and they are two for a reason that is itself a finding.
#
# `vote` is tech-spec §4's own example and the domain's most-searched word:
# 11,238 matches across the corpus, present in every game. It exercises the
# round trip and the row resolution at scale.
#
# `" the "` is here because **a bare word can never straddle a fold.**
# `divide_line` cuts at *word starts*, so a word too short to be folded moves to
# the next row whole — measured, `vote` straddles a row boundary **zero** times
# in all 420 games. A match can only span a fold if it contains the whitespace
# the cut fell on, which is exactly what a space-bearing phrase does: `" the "`
# straddles 2,637 times, in 407 of the 420 files and in all 36 run dirs. Without
# it, property 3's straddle branch below would be dead code that never executed
# and the sweep would look thorough while testing half of itself. That is the
# whole point of `test_width_96_really_does_fold_a_match_across_a_row_boundary`.
#
# Both are lowercase, so both run case-INSENSITIVELY (§2.7) — which is why the
# round-trip assertion compares `.lower()` on both sides rather than `==`.
_SWEEP_QUERIES = ("vote", " the ")

# Precondition one. `wrap_row_starts` calls `expandtabs(8)` before wrapping,
# exactly as `Content._wrap_and_format` does, so for a line containing a tab the
# offsets `divide_line` reports are indices into the *expanded* line and every
# offset after the tab sits further right than the same character does in the
# file. That is not worked around — it is pinned here, so a transcript that ever
# grew a tab fails loudly instead of mis-jumping quietly.
_TAB = "\t"

# Precondition two. Rich's `strip_control_codes` silently removes these five
# (BEL, BS, VT, FF, CR) and `rich.text.Text.append` runs it, so a transcript
# containing one renders a character short of its file — and every offset after
# it points at the wrong character on screen. `\n` and `\t` are NOT in Rich's
# list; the tab is excluded here for the separate reason above.
#
# `tests/test_transcript_highlight.py` guards the same fact for the *tokenizer*.
# Restated here because this sweep depends on it for a different reason: there,
# a stripped code costs a character of rendered text; here it costs the
# alignment between an offset and the row it is supposed to land on.
_RICH_STRIPPED_CONTROL_CODES = "\x07\x08\x0b\x0c\x0d"

# How many problems one failing file is allowed to print before its report is
# truncated. A broken row map would otherwise emit tens of thousands of
# complaints and bury the useful first one.
_MAX_REPORTED_PROBLEMS = 10

# How many failing files one run dir prints in full. A row-map bug is systematic,
# so all ~20 games in a run break at once; the first few reports say everything
# and the count line says how wide the damage is.
_MAX_REPORTED_FILES = 5


def _row_end(row_starts: list[int], row: int, length: int) -> int:
    """Where rendered row ``row`` ends: the next row's start, or the text's end.

    The half-open upper bound that makes row ``i`` exactly
    ``text[row_starts[i] : _row_end(...)]``. Written once because the last row is
    the only special case and getting it wrong in one of three call sites is
    precisely the kind of drift this sweep exists to catch elsewhere.
    """
    return row_starts[row + 1] if row + 1 < len(row_starts) else length


def _sweep_one_game(text: str) -> list[str]:
    """All four properties against one game, as a list of readable problems.

    Collecting rather than raising is the corpus idiom: a systematic bug breaks
    every game in a run at once, and one file aborting the item would hide the
    other nineteen. The list is capped at :data:`_MAX_REPORTED_PROBLEMS` — a
    broken row map has one cause and tens of thousands of symptoms, and the
    first ten say everything the eleven-thousandth would.

    Whether the straddle branch of property 3 ever *fires* is deliberately NOT
    answered here: ``test_width_96_really_does_fold_a_match_across_a_row_boundary``
    runs its own census for it, so that verdict never depends on which items
    pytest happened to select or in what order.
    """
    problems: list[str] = []
    length = len(text)

    # --- Property 4: the two preconditions the wrap mirror rests on ----------
    # Checked FIRST and reported first, because both of them invalidate
    # everything below: a tab makes the offsets indices into a different string,
    # and a stripped control code makes the render shorter than the file. A
    # reader who sees a hundred row-resolution failures should see the reason
    # above them, not have to guess it.
    if _TAB in text:
        problems.append(
            f"contains {text.count(_TAB)} tab character(s) — `expandtabs(8)` runs "
            "before the wrap, so every offset after a tab indexes the EXPANDED "
            "line and no longer points where `find_matches` says it does"
        )
    stripped = sorted(
        hex(ord(char)) for char in set(_RICH_STRIPPED_CONTROL_CODES) if char in text
    )
    if stripped:
        problems.append(
            f"contains control code(s) {stripped} that `rich.text.Text.append` "
            "silently strips — the rendered game is shorter than the file, so "
            "every later offset lands on the wrong character"
        )

    # --- Property 2: the row starts partition [0, len(text)) contiguously ----
    row_starts = wrap_row_starts(text, _CORPUS_WRAP_WIDTH)
    if not row_starts:
        # `wrap_row_starts` documents that even the empty string is one row, so
        # an empty list is not a degenerate input — it is a broken mirror, and
        # nothing below it can be meaningfully checked.
        problems.append(
            f"`wrap_row_starts` returned NO rows for {length} characters — the "
            "list's length IS the rendered row count, so this game would be "
            "unreachable by any jump"
        )
        return problems

    if row_starts[0] != 0:
        problems.append(
            f"row 0 starts at offset {row_starts[0]}, not 0 — the first "
            f"{row_starts[0]} character(s) of the game belong to no row"
        )
    for row, (this, following) in enumerate(zip(row_starts, row_starts[1:])):
        if this >= following:
            problems.append(
                f"row {row} starts at {this} and row {row + 1} at {following} — "
                "starts must STRICTLY increase, or two rows overlap (or one is "
                "empty and every later row index is off by one)"
            )
            break
    if row_starts[-1] >= length:
        # Measured over all 420: no committed transcript ends with a newline, so
        # the documented trailing-blank-row branch of `wrap_row_starts` (whose
        # last start legitimately IS `len(text)`) never fires here. A start at or
        # past the end therefore means a real arithmetic fault, not that branch.
        problems.append(
            f"the last row starts at {row_starts[-1]} but the game is only "
            f"{length} characters — no committed transcript ends in a newline, "
            "so every row start must sit strictly inside the text"
        )

    # "No gaps and no overlaps" is now established — but note what does NOT
    # establish it, because the obvious check is a trap: concatenating
    # `text[starts[i] : starts[i + 1]]` down the list rebuilds the text for ANY
    # increasing starts whatever, since each row ends exactly where the next
    # begins by construction. It would read as the strongest assertion here and
    # assert nothing at all. Contiguity IS the three checks above; what is left
    # to pin is that the partition falls in the RIGHT PLACES.
    #
    # The text's own newlines say where some of those places are. Textual splits
    # on newlines before it wraps anything, so a newline always ends a rendered
    # row: every `\n` must be the last character of a row, which means the offset
    # after it is either a row start or the end of the game. This is the property
    # a one-character drift in the offset arithmetic breaks on the very first
    # line, while leaving the starts perfectly increasing and row 0 at 0.
    starts_index = set(row_starts)
    newline = text.find("\n")
    while newline != -1:
        if newline + 1 != length and newline + 1 not in starts_index:
            row = row_for_offset(row_starts, newline)
            problems.append(
                f"the newline at offset {newline} does not end a rendered row: "
                f"offset {newline + 1} is not a row start, so row {row} "
                f"([{row_starts[row]}, {_row_end(row_starts, row, length)})) "
                "runs on past the end of its source line — the row starts have "
                "drifted off the text they are supposed to partition"
            )
            break
        newline = text.find("\n", newline + 1)

    # --- Properties 1 and 3: round trip, and match → row ---------------------
    for query in _SWEEP_QUERIES:
        if len(problems) >= _MAX_REPORTED_PROBLEMS:
            break
        for start, end in find_matches(text, query):
            if len(problems) >= _MAX_REPORTED_PROBLEMS:
                break

            # Property 1. The offsets must slice the ORIGINAL text back to the
            # query — `.lower()` on both sides because both sweep queries are
            # all-lowercase and so match case-insensitively (§2.7).
            found = text[start:end]
            if found.lower() != query.lower():
                problems.append(
                    f"{query!r} reported a match at ({start}, {end}) but the "
                    f"text there is {found!r} — the offsets must slice back to "
                    "the query, which is the property the widget consumes"
                )
                continue

            # Property 3, first half: the resolved row must HOLD the match's
            # start. This is the assertion a jump depends on — `scroll_to(y=row)`
            # is worth nothing if `row` is not where the match is.
            row = row_for_offset(row_starts, start)
            head_end = _row_end(row_starts, row, length)
            if not row_starts[row] <= start < head_end:
                problems.append(
                    f"{query!r} at offset {start} resolved to row {row}, which "
                    f"spans [{row_starts[row]}, {head_end}) — `row_for_offset` "
                    "must return a row that CONTAINS the offset"
                )
                continue

            if end <= head_end:
                continue

            # Property 3, second half: the match straddles a fold. Its head is on
            # `row` (established above) and its tail must continue into the
            # IMMEDIATE successor — asserted through `row_for_offset` again,
            # rather than by re-deriving it from the starts, so it is the
            # resolver being checked and not arithmetic restating itself.
            #
            # "Immediate" rather than merely "later" is safe here because neither
            # sweep query contains a newline, and inside one source line every
            # row is a full wrap width wide (`divide_line` cuts at word starts,
            # and folds an over-long word into exact `width`-cell chunks). Only a
            # blank source line produces a one-character row, and a query with no
            # newline cannot span one.
            tail_row = row_for_offset(row_starts, end - 1)
            if tail_row != row + 1:
                problems.append(
                    f"{query!r} at ({start}, {end}) straddles the fold at "
                    f"{head_end}: its head resolves to row {row} but its tail "
                    f"resolves to row {tail_row}, not {row + 1} — a straddling "
                    "match must continue into the immediate successor row"
                )

    return problems


@pytest.mark.parametrize("run_dir", _RUN_DIR_PARAMS)
def test_the_row_map_and_the_matcher_agree_on_every_committed_game(
    run_dir: Path,
) -> None:
    """The four sweep properties, over one run directory of real games.

    Round trip, partition, resolution and the two preconditions — stated in
    :func:`_sweep_one_game` and reported here per run dir, with the coverage
    counter that proves the item swept every file it was given rather than
    quietly none of them.

    No count of anything is asserted. What is asserted is that whatever this run
    directory happens to contain, and however many games join it later, the
    offsets `find_matches` reports and the rows `wrap_row_starts` lays out
    describe the same text.
    """
    files = [path for path in _TRANSCRIPTS if path.parent == run_dir]
    assert files, f"no transcripts discovered under {run_dir} — the sweep is vacuous"

    failures: list[str] = []
    checked = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        problems = _sweep_one_game(text)
        checked += 1
        if not problems:
            continue
        reported = problems[:_MAX_REPORTED_PROBLEMS]
        if len(problems) > len(reported):
            reported.append(
                f"… and {len(problems) - len(reported)} further problem(s), "
                "not shown"
            )
        failures.append(
            "\n  ".join([f"{_rel(path)} ({len(text)} characters):", *reported])
        )

    # The coverage counter. Cheap, and it is the whole justification for
    # grouping 420 files into 36 items: without it a `files` list that had
    # silently narrowed — a glob change, a `continue` added to the loop — would
    # leave this item green while sweeping nothing.
    assert checked == len(files), (
        f"{run_dir.name}: swept {checked} of {len(files)} discovered transcripts"
    )

    shown = failures[:_MAX_REPORTED_FILES]
    if len(failures) > len(shown):
        shown.append(
            f"… and {len(failures) - len(shown)} further failing transcript(s) "
            f"in {run_dir.name}, not shown"
        )
    assert not failures, "\n\n".join(
        [
            f"{len(failures)} of {len(files)} transcripts in {run_dir.name} broke "
            f"the offset/row contract at width {_CORPUS_WRAP_WIDTH}:",
            *shown,
        ]
    )


@_requires_corpus
def test_the_run_dir_items_between_them_sweep_every_discovered_transcript() -> None:
    """The coverage counters add up, and no file falls between two items.

    Each item above proves it swept every file in *its own* directory. This
    proves the directories themselves partition the corpus — that ``_RUN_DIRS``
    loses no file and double-counts none, so the green items together mean
    "every discovered transcript was swept" rather than "every transcript in
    some subset of the directories was". (36 items over 420 files as this is
    written; both grow with every committed eval run, which is why neither
    number is asserted.)

    Order-independent by construction: it recomputes the grouping from
    ``_TRANSCRIPTS`` rather than reading a counter the other items filled in, so
    it says the same thing under ``-k``, under ``-p no:randomly`` and under
    ``xdist``. Reads no files, costs nothing.
    """
    assert _TRANSCRIPTS, f"no transcripts discovered under {_CORPUS_ROOT}"
    assert _RUN_DIRS, "transcripts discovered but no run directories derived"

    grouped = sum(
        len([path for path in _TRANSCRIPTS if path.parent == run_dir])
        for run_dir in _RUN_DIRS
    )
    assert grouped == len(_TRANSCRIPTS), (
        f"the {len(_RUN_DIRS)} run-dir items cover {grouped} transcripts but "
        f"{len(_TRANSCRIPTS)} were discovered — a file is being swept twice or "
        "not at all"
    )
    # The other direction: no transcript sits outside every run dir, which a
    # `grouped == len(...)` alone would tolerate if one file were counted twice
    # while another was counted not at all.
    swept = set(_RUN_DIRS)
    assert all(path.parent in swept for path in _TRANSCRIPTS)


@_requires_corpus
def test_width_96_really_does_fold_a_match_across_a_row_boundary() -> None:
    """NON-VACUITY: property 3's straddle branch actually fires on real games.

    Without this, the sweep above could pass with its straddle half never
    executing once, and nobody would know — which is not a hypothetical here.
    **Measured: the tech spec's own example query ``vote`` straddles a fold zero
    times across all 420 committed games.** `divide_line` cuts at word starts, so
    a word short enough not to be folded moves to the next row whole and a
    single-word query can only ever sit inside one row. Had the sweep carried
    ``vote`` alone, half of it would have been dead code reading as coverage.

    So this test says the corpus genuinely produces the case, counts how much of
    it there is, and — the part a counter cannot give — takes one real example
    apart to show it is a straddle and not an off-by-one: a non-empty head ending
    exactly at the fold, a non-empty tail starting exactly there, and the two
    concatenating back to the matched run.

    Deliberately its OWN sweep rather than a total accumulated by the items
    above. Reading a counter they filled in would make this test's verdict depend
    on collection order, on ``-k`` and on ``xdist`` sharding; recomputing costs
    about a second and means the same thing however the suite is invoked.
    """
    straddles = 0
    files_with_one = 0
    run_dirs_with_one: set[str] = set()
    example: tuple[str, str, str] | None = None

    for path in _TRANSCRIPTS:
        text = path.read_text(encoding="utf-8")
        row_starts = wrap_row_starts(text, _CORPUS_WRAP_WIDTH)
        here = 0
        for query in _SWEEP_QUERIES:
            for start, end in find_matches(text, query):
                row = row_for_offset(row_starts, start)
                fold = _row_end(row_starts, row, len(text))
                if end <= fold:
                    continue
                here += 1
                if example is None:
                    example = (_rel(path), text[start:fold], text[fold:end])
        if here:
            straddles += here
            files_with_one += 1
            run_dirs_with_one.add(path.parent.name)

    assert straddles, (
        f"not one match of {list(_SWEEP_QUERIES)} straddles a row boundary at "
        f"width {_CORPUS_WRAP_WIDTH} across {len(_TRANSCRIPTS)} committed games "
        "— the straddle half of the sweep above is passing by never firing. "
        "Either the wrap width no longer folds these games, or a sweep query was "
        "narrowed to something that cannot span a fold (a query with no "
        "whitespace in it never can)."
    )
    # Not one freak game — which does NOT follow from the count above, and is
    # the half worth having: if every straddle in the corpus came from a single
    # transcript, deleting or regenerating that one file would silently take
    # property 3's straddle branch dark again. Measured at the time of writing:
    # 2,637 straddles across 407 of the 420 files and all 36 run dirs. Asserted
    # as "more than one run dir", never as those numbers — they grow with the
    # corpus.
    assert len(run_dirs_with_one) > 1, (
        f"every fold-straddling match in the corpus comes from {files_with_one} "
        f"file(s) in run dir(s) {sorted(run_dirs_with_one)} — too narrow a base "
        "for the sweep's straddle branch to rest on"
    )

    assert example is not None
    where, head, tail = example
    assert head, f"{where}: a straddling match must have a non-empty head"
    assert tail, f"{where}: a straddling match must have a non-empty tail"
    assert (head + tail).lower() in {query.lower() for query in _SWEEP_QUERIES}, (
        f"{where}: the head {head!r} and tail {tail!r} of a straddling match "
        "must concatenate back to the query that found it"
    )


# ===========================================================================
# 7. Row-keyed stepping (spec 044 Slice 4, task 4.1) — what `n` and `N` answer
# ===========================================================================
#
# WHY THIS IS A PURE SECTION AT ALL, since `n` and `N` are keys on a screen.
# tasks.md task 4.6 says it outright — *"cover the row-keyed skip in the pure
# tests too — it is a statement about `next_match`, not about Textual"* — and
# tech-spec §4 gives the rule behind it: a criterion belongs in the widget layer
# only if removing Textual would make it UNSTATEABLE. "Which match does `n`
# answer from row 30?" is a question about three lists of integers. "Does `n`
# reach the screen rather than get typed into the search box?" is not, and that
# one lives in `tests/test_ledger_viewer.py`'s B15.5.
#
# The split is worth about 100x per item (this file: ~0.006 s an item; a
# `run_test()` item: ~0.63 s), and it is what lets the truth table below be
# swept exhaustively — every row of the fixture, in both directions — rather
# than sampled at the two or three positions a terminal test could afford.
#
# WHAT THE FUNCTION PROMISES, restated as the four things asserted below:
#
#   1. A PRESS NEVER STOPS ON THE ROW IT STARTED FROM. Several matches routinely
#      share one rendered row, and a jump scrolls by ROW — so stepping
#      match-by-match would spend whole presses moving between two matches the
#      reviewer can already see side by side, with the view not moving at all.
#      `step_match_by_row` skips every match sharing the origin row.
#
#   2. THE RUNNING COUNT THEREFORE JUMPS, and that is accepted rather than
#      regretted (functional-spec §2.6/§2.8): the index is a position among ALL
#      matches, because that is what the reviewer is counting off against the
#      highlights they can see. The fixture below produces the spec's own
#      example — match 4 straight to match 7.
#
#   3. THE FIRST MATCH ON A ROW IS THE ONE THAT BECOMES CURRENT, in BOTH
#      directions. Forwards that falls out for free; backwards it is the one
#      piece of real work in the function, because `prev_match` answers with the
#      LAST match above the row, which on a row holding three is the third.
#      Asserted as a property over every row, not only where it happens to bite.
#
#   4. BOTH WRAPS. Forward off the last row answers the game's first match;
#      backward off the first row answers the first match on whichever row holds
#      the game's last one — consequence 3 again — each with `wrapped=True`.
#
# AND THE ASYMMETRY WITH ENTER, which is the reason `next_match` carries an
# `inclusive` flag at all: from one and the same row, Enter (offset-keyed,
# inclusive) stops ON a match sitting there while `n` (row-keyed) moves past it.
# Same inputs, opposite answers, both correct — functional-spec §2.5 AC2 against
# §2.6's "a press never stops on the line it started from". A single assertion
# below states the two side by side so that "tidying" one into the other is a
# red test rather than a silent collapse of two keys into one.

# ---------------------------------------------------------------------------
# The fixture: six rendered rows carrying 0, 2, 0, 1, 3 and 1 matches
# ---------------------------------------------------------------------------
#
# SHORT LINES AND A WIDE WIDTH, so no line folds and A ROW IS A LINE. That is
# not a simplification that weakens the tests — wrapping is `wrap_row_starts`'
# contract and is swept over 420 real games in section 6 — it is what lets the
# truth table below be read by eye against the six strings, and what makes a
# failure say "row 4 answered the wrong match" rather than "some offset resolved
# somewhere".
#
# THE THREE ROW POPULATIONS ARE ALL PRESENT ON PURPOSE: a row with ONE match is
# the case where skipping row-mates is a no-op, a row with TWO is where the
# forward skip first bites, and a row with THREE is where the BACKWARD walk-back
# to the row's first match has two matches to walk past rather than one — an
# off-by-one that stopped at the second would pass a two-match fixture.
#
# Row 5 carries the game's last match and row 1 its first, so both wraps have a
# row of their own to answer with, and neither wrap can be confused with "the
# step ran off the end and clamped".
_STEP_LINES = (
    "quiet line",  # row 0 — no match: a step must pass straight over it
    "x and x",  # row 1 — matches 0 and 1
    "nothing here",  # row 2 — no match
    "x alone",  # row 3 — match 2
    "x x x",  # row 4 — matches 3, 4 and 5
    "x last",  # row 5 — match 6
)

# One character, so a match can never straddle a fold and every match sits
# wholly inside the row this fixture places it on. Absent from every other word
# above (`quiet`, `line`, `nothing`, `here`, `and`, `alone`, `last`), so the
# match count is exactly the number of `x`s written out.
_STEP_QUERY = "x"

# Wider than the longest line (12 characters), so `wrap_row_starts` returns one
# start per line and the row indices in the comments above are the row indices
# the assertions use.
_STEP_WIDTH = 40

_STEP_TEXT = "\n".join(_STEP_LINES)
_STEP_ROW_STARTS = wrap_row_starts(_STEP_TEXT, _STEP_WIDTH)
_STEP_MATCHES = find_matches(_STEP_TEXT, _STEP_QUERY)

# The rendered row each match sits on, in match order — the fixture's shape
# stated once as data so the tables below can be read against it, and asserted
# in `test_the_fixture_really_has_rows_holding_one_two_and_three_matches` so a
# fixture edit reports itself instead of quietly rewriting the expectations.
_STEP_MATCH_ROWS = (1, 1, 3, 4, 4, 4, 5)

# FORWARD: (origin row, the match index `n` answers, whether it wrapped).
#
# Every row of the fixture, because the interesting rows are not the ones a
# sampler would choose: row 2 holds no match at all (a step from an empty row
# must find the next populated one, not stall), and row 0 is the one place where
# "skip the row I am on" and "find the first match in the game" give the same
# answer — which is exactly why it cannot be the only case.
_STEP_FORWARD_CASES = [
    pytest.param(0, 0, False, id="empty-row-finds-the-first-match"),
    pytest.param(1, 2, False, id="two-row-mates-are-both-skipped"),
    pytest.param(2, 2, False, id="empty-row-finds-the-next-match"),
    pytest.param(3, 3, False, id="one-match-row-moves-on"),
    pytest.param(4, 6, False, id="three-row-mates-are-all-skipped"),
    pytest.param(5, 0, True, id="off-the-last-row-wraps-to-the-first"),
]

# BACKWARD: the mirror, and the direction where consequence 3 is real work.
#
# From row 2 the last match above is index 1 — the SECOND of row 1's two — and
# the answer must nonetheless be index 0, the row's first. From row 5 the last
# match above is index 5, the THIRD of row 4's three, and the answer must be
# index 3. Those two rows are the whole reason the backward branch has code in
# it, and an implementation that returned `prev_match`'s raw answer passes every
# other case here.
_STEP_BACKWARD_CASES = [
    pytest.param(0, 6, True, id="off-the-first-row-wraps-to-the-last"),
    pytest.param(1, 6, True, id="own-row-mates-do-not-stop-the-wrap"),
    pytest.param(2, 0, False, id="backs-up-to-the-first-of-two-row-mates"),
    pytest.param(3, 0, False, id="one-match-row-below-two-row-mates"),
    pytest.param(4, 2, False, id="one-match-row-is-answered-as-itself"),
    pytest.param(5, 3, False, id="backs-up-to-the-first-of-three-row-mates"),
]


def test_the_fixture_really_has_rows_holding_one_two_and_three_matches() -> None:
    """THE PREMISE every table below is read against (§2.6).

    Stated as an assertion rather than left in a comment, because a fixture edit
    is exactly how this section would go quietly vacuous: lengthen a line past
    :data:`_STEP_WIDTH` and rows stop being lines; drop one `x` from row 4 and
    the backward walk-back has only one row-mate to step over, which an
    off-by-one survives. Both would leave every parametrized case below passing
    against a fixture that no longer poses the question.

    Three populations, all required: a row with one match (skipping is a no-op),
    a row with two (the forward skip bites), and a row with three (the backward
    walk-back has more than one match to walk past).
    """
    assert len(_STEP_ROW_STARTS) == len(_STEP_LINES), (
        "a line of this fixture has folded — `_STEP_WIDTH` must stay wider than "
        "the longest line, or a row is no longer a line and the tables below "
        "are addressing rows that do not exist"
    )
    rows = tuple(row_for_offset(_STEP_ROW_STARTS, start) for start, _ in _STEP_MATCHES)
    assert rows == _STEP_MATCH_ROWS

    per_row = {row: rows.count(row) for row in set(rows)}
    assert sorted(per_row.values()) == [1, 1, 2, 3], (
        f"the fixture's rows hold {sorted(per_row.values())} matches; §2.6's "
        "skip needs a one-match row, a two-match row and a three-match row"
    )
    # ...and two rows with no match at all, which is what makes "the step passes
    # over an empty row" a case rather than an accident of the first row.
    assert len(set(rows)) == len(_STEP_LINES) - 2


@pytest.mark.parametrize(("row", "index", "wrapped"), _STEP_FORWARD_CASES)
def test_n_answers_the_first_match_on_the_next_row_that_has_one(
    row: int, index: int, wrapped: bool
) -> None:
    """`n`'s whole truth table, every row of the fixture (§2.6).

    "A press never stops on the line it started from … `n` and `N` skip past any
    that share the line the reviewer is already on and go to the next line that
    has a match." Read as a function: from row ``r``, the answer is the first
    match starting on the lowest row **above** ``r`` that holds one.

    The two-row-mate and three-row-mate cases are where that costs something: an
    implementation stepping ``current ± 1`` — which is what tech-spec §2.6 said
    before it was corrected during Slice 4 — answers a match on the SAME row and
    the view does not move, which is the press §2.6 exists to eliminate.
    """
    assert step_match_by_row(
        _STEP_MATCHES, _STEP_ROW_STARTS, row, forward=True
    ) == (index, wrapped)


@pytest.mark.parametrize(("row", "index", "wrapped"), _STEP_BACKWARD_CASES)
def test_capital_n_answers_the_first_match_on_the_previous_row_that_has_one(
    row: int, index: int, wrapped: bool
) -> None:
    """`N`'s truth table — the mirror, and the direction with real work in it.

    :func:`prev_match` answers with the **last** match above the row, which on a
    row holding three is the third. §2.6's second accepted consequence says the
    reviewer must land on the row's **first** match whichever way they arrived,
    so the backward branch walks that answer back. The two multi-match rows here
    are what make an implementation that skipped the walk-back fail; every
    single-match case passes against it.
    """
    assert step_match_by_row(
        _STEP_MATCHES, _STEP_ROW_STARTS, row, forward=False
    ) == (index, wrapped)


@pytest.mark.parametrize("forward", [True, False], ids=["n", "N"])
def test_a_press_never_answers_a_match_on_the_row_it_started_from(
    forward: bool,
) -> None:
    """§2.6's headline, as a property over every row rather than as a table.

    The tables above say *which* match each row answers; this says the one thing
    that must be true of **all** of them, in both directions, and would keep
    being true if the fixture changed shape: the answer never sits on the origin
    row.

    The wrap is the one case where the criterion's companion clause applies —
    "the one place a press still produces no movement is the last screenful of
    the game" is about the *view*, not about the match — so a wrap that answers a
    match on the origin row is still a genuine violation and is not excused here.
    A single-match game would be the exception, which is why this fixture has
    seven.
    """
    for row in range(len(_STEP_ROW_STARTS)):
        found = step_match_by_row(_STEP_MATCHES, _STEP_ROW_STARTS, row, forward=forward)
        assert found is not None
        index, _ = found
        landed = row_for_offset(_STEP_ROW_STARTS, _STEP_MATCHES[index][0])
        assert landed != row, (
            f"a {'forward' if forward else 'backward'} step from row {row} "
            f"answered match {index}, which is on row {row} as well — that is "
            "the press that moves nothing, and §2.6 forbids it"
        )


@pytest.mark.parametrize("forward", [True, False], ids=["n", "N"])
def test_the_match_answered_is_always_the_first_one_on_its_row(
    forward: bool,
) -> None:
    """§2.6's second accepted consequence, in both directions (tech-spec §2.6).

    "The second and later matches on a line are highlighted but are never
    themselves the current one." Stated over every row of the fixture, because
    the direction that can break it breaks it only on rows with more than one
    match and a table that happened to avoid those would prove nothing.
    """
    first_on_row: dict[int, int] = {}
    for index, (start, _) in enumerate(_STEP_MATCHES):
        first_on_row.setdefault(row_for_offset(_STEP_ROW_STARTS, start), index)

    for row in range(len(_STEP_ROW_STARTS)):
        found = step_match_by_row(_STEP_MATCHES, _STEP_ROW_STARTS, row, forward=forward)
        assert found is not None
        index, _ = found
        landed = row_for_offset(_STEP_ROW_STARTS, _STEP_MATCHES[index][0])
        assert index == first_on_row[landed], (
            f"the step answered match {index} on row {landed}, but the first "
            f"match on that row is {first_on_row[landed]} — a reviewer arriving "
            "at a row must land on its first match whichever way they came"
        )


def test_the_running_count_jumps_over_the_matches_sharing_a_row() -> None:
    """The consequence the author accepted explicitly: 4 → 7 (§2.8).

    "The running count is a position among **all** matches, so where a line of
    the screen holds several it can jump by more than one at a time." The
    fixture produces the functional spec's own example exactly: row 3 holds match
    3 (counted from one), row 4 holds matches 4, 5 and 6, and row 5 holds match
    7 — so two presses from row 3 count 3 → 4 → 7.

    This is asserted rather than merely documented because the plausible "fix"
    is renumbering the status to count only reachable matches, which would make
    the total disagree with the number of highlights on screen — the worse lie,
    and the one this test makes expensive to ship.
    """
    from_row_three = step_match_by_row(
        _STEP_MATCHES, _STEP_ROW_STARTS, 3, forward=True
    )
    assert from_row_three is not None
    first, _ = from_row_three

    next_row = row_for_offset(_STEP_ROW_STARTS, _STEP_MATCHES[first][0])
    onwards = step_match_by_row(_STEP_MATCHES, _STEP_ROW_STARTS, next_row, forward=True)
    assert onwards is not None
    second, _ = onwards

    # Counted from one, as the reviewer is told them (`3/7`, `4/7`, `7/7`).
    assert (first + 1, second + 1) == (4, 7), (
        "two presses out of row 3 must count 4 then 7, skipping the two "
        "matches that share row 4 with match 4 — they are highlighted and the "
        "reviewer can already see them"
    )
    # The skipped two are real, which is what makes the jump a jump.
    assert second - first == 3


def test_enter_and_n_answer_differently_from_the_same_row() -> None:
    """The asymmetry that keeps Enter and `n` two keys (§2.5 AC2 vs §2.6).

    From one row, with one match list: Enter stops on a match already sitting
    there ("it does not skip ahead"), and `n` moves past it ("a press is never
    spent moving between two matches the reviewer can already see together").
    Both are correct, and the difference is the entire reason
    :func:`next_match` carries an ``inclusive`` flag instead of a fixed policy.

    Asserted here, on the two functions side by side, because the failure it
    guards is a tidy-up rather than a bug: routing Enter through
    :func:`step_match_by_row` "for consistency" reads like a simplification and
    silently costs §2.5 AC2 — and the widget test that would catch it needs a
    terminal, a fixture and half a second, where this needs three lines.

    Row 1 is the discriminating one precisely because it holds a match; from row
    2, which holds none, the two agree and the assertion would be vacuous — so
    that agreement is asserted too, as the other half of the statement.
    """
    row_with_a_match = 1
    enter = next_match(
        _STEP_MATCHES,
        offset_for_row(_STEP_ROW_STARTS, row_with_a_match),
        inclusive=True,
    )
    n_key = step_match_by_row(
        _STEP_MATCHES, _STEP_ROW_STARTS, row_with_a_match, forward=True
    )
    assert enter == (0, False), "Enter stops on the match already on this row"
    assert n_key == (2, False), "`n` moves past it to the next row that has one"
    assert enter != n_key

    # ...and where the row holds nothing, the two agree — so the difference
    # above is about the row-mate rule and not about two unrelated functions.
    empty_row = 2
    assert next_match(
        _STEP_MATCHES, offset_for_row(_STEP_ROW_STARTS, empty_row), inclusive=True
    ) == step_match_by_row(
        _STEP_MATCHES, _STEP_ROW_STARTS, empty_row, forward=True
    )


@pytest.mark.parametrize("forward", [True, False], ids=["n", "N"])
def test_stepping_with_no_matches_answers_nothing_rather_than_raising(
    forward: bool,
) -> None:
    """An empty match list is ``None``, in both directions (§2.6's last criterion).

    "Given no search has been made since the game was opened, when the reviewer
    presses `n`, then nothing happens and no error appears." The screen's guard
    on ``_search`` catches the no-search case; this is the other one — a search
    whose query matches nothing — and ``None`` is what makes "do nothing" the
    caller's easy branch rather than a sentinel index it could accidentally
    scroll to.
    """
    assert step_match_by_row([], _STEP_ROW_STARTS, 2, forward=forward) is None


@pytest.mark.parametrize("row", [-5, -1, 999], ids=["far-above", "above", "far-below"])
@pytest.mark.parametrize("forward", [True, False], ids=["n", "N"])
def test_a_row_outside_the_map_is_clamped_rather_than_raising(
    row: int, forward: bool
) -> None:
    """Bounds are clamped, never raised — the same rule the row arithmetic uses.

    ``row`` arrives from a live scroll position, and the map it is resolved
    against may have been built a moment earlier at a different wrap width (the
    reviewer resized the terminal between two presses). A traceback out of a
    keypress is strictly worse than answering the first or the last row, so the
    contract is to clamp — and this pins that it is a contract rather than an
    accident of `bisect` happening not to raise.
    """
    found = step_match_by_row(_STEP_MATCHES, _STEP_ROW_STARTS, row, forward=forward)
    assert found is not None
    index, _ = found
    assert 0 <= index < len(_STEP_MATCHES)


# ---------------------------------------------------------------------------
# ...and the same rules over a real game, where nobody chose the rows
# ---------------------------------------------------------------------------
#
# The fixture above is, by construction, a shape someone thought of: six lines
# with the row populations the criteria need. These two say the rules hold on
# 88 KB of model prose whose row boundaries fall wherever Rich's wrapping puts
# them — and the first of them is a NON-VACUITY check on the whole section, in
# exactly the sense tech-spec §4 means it: if no real game ever put two matches
# on one rendered row, the row-mate skip would be a rule written for a case that
# does not occur, and every test above it would be about a fixture.
#
# THE LARGEST COMMITTED GAME, discovered rather than named, for the reason
# `tests/test_ledger_viewer.py`'s `_largest_committed_transcript` gives: the
# corpus gains a run directory on every measured eval run and loses one whenever
# a smoke run is curated away, so a hard-coded path is a test that breaks on a
# green tree. Width 96 is the measured live wrap width of a game that overflows
# a 100-column terminal (tech-spec §2.2).
#
# THE QUERY IS `the`, AND `vote` WOULD NOT DO — measured on that game: `vote`'s
# 18 matches sit one to a row and produce **no** row-mate anywhere, so a sweep
# carrying it alone would find the skip never firing and report that as
# coverage. This is the same trap, in the same corpus, that
# `test_width_96_really_does_fold_a_match_across_a_row_boundary` records for the
# straddle branch, and the answer is the same: carry a query common enough to
# collide.


def _largest_committed_game() -> tuple[str, list[int], list[tuple[int, int]]]:
    """``(text, row starts at width 96, the matches for ``the``)`` for that game.

    One read and one row map, shared by the two tests below rather than built
    twice — the map is ~7.6 ms on this file and the read is 88 KB, which is
    cheap but not free, and building it once means the two tests cannot
    disagree about which game they are describing.
    """
    game = max(_TRANSCRIPTS, key=lambda path: (path.stat().st_size, str(path)))
    text = game.read_text(encoding="utf-8")
    row_starts = wrap_row_starts(text, _CORPUS_WRAP_WIDTH)
    return text, row_starts, find_matches(text, "the")


@_requires_corpus
def test_the_row_mate_skip_fires_on_a_real_committed_game() -> None:
    """NON-VACUITY: real prose really does put two matches on one rendered row.

    Without this the whole section could be a rule about a fixture. Measured on
    the largest committed game at width 96: `the` has 702 matches spread over
    522 rows, and **154 of those rows hold more than one** — so the skip is not
    an edge case the row-keyed design was invented for, it is what happens on
    every other page of a real game.

    The behaviour is then taken apart on one of those rows rather than left to
    the counter: the forward step from a row holding several answers a match on
    a **strictly later** row, and the row-mates it passed over are genuinely
    matches the reviewer can already see — which is the justification §2.6 gives
    for skipping them at all.
    """
    _, row_starts, matches = _largest_committed_game()
    per_row: dict[int, list[int]] = {}
    for index, (start, _) in enumerate(matches):
        per_row.setdefault(row_for_offset(row_starts, start), []).append(index)

    crowded = sorted(row for row, on_it in per_row.items() if len(on_it) > 1)
    assert crowded, (
        "no rendered row of the largest committed game holds two matches of "
        "`the` at width 96 — the row-mate skip has no real case, and every "
        "test above it is about a hand-written fixture"
    )

    row = crowded[0]
    row_mates = per_row[row]
    found = step_match_by_row(matches, row_starts, row, forward=True)
    assert found is not None
    index, wrapped = found
    assert not wrapped
    assert index not in row_mates, (
        f"row {row} holds matches {row_mates}; the step answered {index}, "
        "which is one of them — the press would not move the view"
    )
    assert row_for_offset(row_starts, matches[index][0]) > row


@_requires_corpus
def test_walking_forward_through_a_real_game_visits_every_row_with_a_match() -> None:
    """The walk a reviewer makes, over a game nobody wrote by hand (§2.6).

    Press `n` from the end of the game and keep pressing. The properties the
    individual cases above state one at a time have to hold **together** across
    hundreds of presses, and this is the only place that composition is checked:
    the rows visited come out strictly increasing, every row that holds a match
    is visited **exactly once** (so no press is wasted and no match's row is
    unreachable), each landing is its row's first match, and the walk ends by
    wrapping round to the game's very first match rather than stalling.

    **This is the shape of the Task 4.4a defect, seen from the pure side.** The
    bug that shipped into Slice 4 was the screen keying the step on
    ``scroll_offset.y``, which clamps at the end of the game and so cannot
    express a position inside the last screenful — every press re-answered the
    same match and the wrap was unreachable. The pure step was never at fault
    and this test says so: handed honest rows it walks the tail to the end and
    round. The screen's half is `tests/test_ledger_viewer.py`'s B15.5, which is
    where the clamp actually lives.

    The walk starts from the LAST row so that the first step is the wrap itself —
    the press that had no way of firing at all before Task 4.4a — and so that the
    visit set is complete rather than missing whichever rows sit above an
    arbitrary starting point.
    """
    _, row_starts, matches = _largest_committed_game()
    rows_with_a_match = sorted(
        {row_for_offset(row_starts, start) for start, _ in matches}
    )
    first_on_row: dict[int, int] = {}
    for index, (start, _) in enumerate(matches):
        first_on_row.setdefault(row_for_offset(row_starts, start), index)

    row = len(row_starts) - 1
    visited: list[int] = []
    wraps = 0
    # One press per populated row, plus the two wraps that bracket the walk, plus
    # slack — a bound rather than a `while True`, so a step that stopped moving
    # fails as an assertion instead of hanging the suite.
    for _ in range(len(rows_with_a_match) + 4):
        found = step_match_by_row(matches, row_starts, row, forward=True)
        assert found is not None
        index, wrapped = found
        if wrapped:
            wraps += 1
            if wraps > 1:
                break
            assert index == 0, (
                "the wrap must answer the game's FIRST match; anything else is "
                "a step that ran off the end rather than round it"
            )
        visited.append(index)
        row = row_for_offset(row_starts, matches[index][0])

    assert wraps == 2, (
        f"the walk wrapped {wraps} times — it should wrap once at the start "
        "(off the last row) and once again on returning to it"
    )
    landed_rows = [row_for_offset(row_starts, matches[index][0]) for index in visited]
    assert landed_rows == rows_with_a_match, (
        f"{len(visited)} presses visited {len(set(landed_rows))} distinct rows "
        f"of the {len(rows_with_a_match)} that hold a match — a row visited "
        "twice is a wasted press and a row never visited is a match the "
        "reviewer cannot reach"
    )
    assert all(
        index == first_on_row[landed]
        for index, landed in zip(visited, landed_rows)
    )
