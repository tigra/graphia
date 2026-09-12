"""Pure, terminal-free unit tests for the transcript-search matchers (spec 044).

Covers the two functions Slice 2 task 2.1 added to :mod:`graphia.eval_ledger`
beside ``tokenize_transcript`` — :func:`search_is_case_sensitive` and
:func:`find_matches` — and nothing else. No Textual, no ``run_test()``, no
``App``, no widget: this module imports one production module and the stdlib.

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

--- What is deliberately NOT here ---

* **The corpus sweep.** Task 3.7 owns it, and it needs the row arithmetic
  (``wrap_row_starts`` / ``row_for_offset``) that does not exist yet. It will
  land in this file, properties only and never golden counts — the corpus grew
  298 -> 420 since spec 038 and gains a directory on every eval run.
* **Row, offset and next/previous-match arithmetic** — Slices 3 and 4.
* **Colour, themes, snapshots and timings** (tech-spec §4, *"what is deliberately
  not tested"*). A background-only span makes no per-theme contrast claim;
  ``snap_compare`` appears in zero test files in this repo; and there are no
  wall-clock assertions anywhere in the suite.

--- Offline by construction ---

``graphia.eval_ledger`` is the pure, Textual-free, I/O-free layer, so the autouse
``safe_llm`` net in ``conftest.py`` is simply irrelevant here, exactly as it is
for ``tests/test_ledger_model.py`` and ``tests/test_transcript_highlight.py``.
Nothing below reads or writes a file: the one "transcript" is a module-level
string. The committed corpus and ``evals/blunder-ledger.yaml`` are never touched.

The real production symbols are imported directly (never re-implemented locally),
so a rename breaks these tests — which is the point.
"""

from __future__ import annotations

import pytest

from graphia import eval_ledger
from graphia.eval_ledger import find_matches, search_is_case_sensitive

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
