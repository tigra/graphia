"""Spec 040 (The Moderator's Closing Story) — Task 3.1: the shared cursor walk.

Spec 039 (Per-AI Private Diaries) put TWO private channels into one prompt slot
and made ``nodes/day.py:_private_record_block`` merge them on the
``thoughts_before`` cursor. Spec 040 needs that same merge for a THIRD-PARTY
reader — the Moderator, writing a closing story over every AI player's private
material — so §2.3 pulls the walk out into
``nodes/day.py:_walk_private_record``, a pure helper returning an ordered
``list[tuple[kind, day, text]]`` that BOTH renderers consume.

WHY THIS FILE EXISTS AT ALL, WHICH IS ALSO WHY IT IS MOSTLY ABOUT ORDER
-----------------------------------------------------------------------

The spec chose extraction over duplication here — the opposite call from 039's
own ``_merge_private_*`` reducers, which are deliberately duplicated — for one
reason, stated in §2.3 in these words: the walk is *"the load-bearing
correctness rule of the two-channel design and its failure mode is 'the story
tells events in the wrong order and nothing raises.'"*

Nothing raises. A broken walk emits the same bullets, the same count of them,
the same words, under the same label — in the wrong sequence. There is no
exception, no schema violation, no log line, and no eval metric that would
notice. So a direct assertion on the ORDER is the only thing standing between
this design and a story that confidently narrates Day 3 before Day 1, and the
sections below are weighted accordingly.

The five sections
-----------------

1. **The order**, asserted as whole ordered lists rather than membership — the
   two degenerate shapes a broken walk actually produces (all thoughts then all
   diaries, or all diaries then all thoughts) both hold exactly the right
   entries, so ``in`` assertions cannot tell them from the truth.
2. **The safety constraint: the walk NEVER windows.** 039 applies
   ``DIARY_WINDOW`` *before* the merge deliberately (cursors partition the
   thought list, so dropping a diary removes only its own line), so the walk
   takes the diary list already windowed — or the full list, which is what the
   Moderator wants and what §2.3 forbids reusing ``DIARY_WINDOW`` for.
3. **The attribution slots** Task 3.2's renderer will read: a diary carries its
   Day, a thought carries ``None`` because ``ThoughtRecord`` has no Day at all.
4. **Purity**, including that neither argument is mutated — a caller may hand
   over a live state list.
5. **The byte-identity of what a PLAYER sees**, against a frozen copy of the
   pre-extraction renderer. This is the regression the refactor risks, and it
   is the one property no amount of walk testing can imply.

Anti-vacuity discipline, inherited from spec 039's suite
--------------------------------------------------------

Every numeric or string constant this file leans on gets a BARE-LITERAL pin
beside any relational check (``DIARY_WINDOW == 3``, ``DIARY_DAY_UNKNOWN ==
"?"``, the two kind constants asserted distinct), because an f-string-derived
expectation is true at any value. And §5's parity oracle is a FROZEN COPY, not
a call into the code under test, so it cannot agree with a mutation by
construction.

These are all unit-level tests over pure functions: no driver, no
``App.run_test()``, no model. Slice 3's preamble warns against copying
``tests/test_spec040_story_ui_order.py``'s live-dispatcher pattern (a ~30%
flake in which the human is killed on Night 1); nothing here drives a game, so
that trap is out of reach rather than avoided.
"""

from __future__ import annotations

import inspect
import itertools

import pytest

import graphia.nodes.day as day_nodes
from graphia.nodes.day import (
    DIARY_DAY_UNKNOWN,
    DIARY_LINE_MARKER,
    DIARY_WINDOW,
    PRIVATE_RECORD_KIND_DIARY,
    PRIVATE_RECORD_KIND_THOUGHT,
    PRIVATE_THOUGHTS_LABEL,
    _private_record_block,
    _private_thoughts_block,
    _walk_private_record,
)
from graphia.state import DiaryRecord, ThoughtEntry, thought_text

# ==========================================================================
# Shared helpers
# ==========================================================================


def _rec(day: int, cursor: int, text: str) -> DiaryRecord:
    """One ``DiaryRecord``. ``cursor`` is ``thoughts_before``.

    Deliberately the same shape and name as
    ``tests/test_slice39_diary_interleave.py:_rec`` — the two files assert
    different properties of the same records, and a reader moving between them
    should not have to re-learn the fixture.
    """
    return {"day": day, "thoughts_before": cursor, "text": text}


def _thought(text: str) -> tuple[str, None, str]:
    """The walk entry a thought must produce, built from the kind constant."""
    return (PRIVATE_RECORD_KIND_THOUGHT, None, text)


def _diary(day: int | str, text: str) -> tuple[str, int | str, str]:
    """The walk entry a diary must produce, built from the kind constant."""
    return (PRIVATE_RECORD_KIND_DIARY, day, text)


_FOUR_THOUGHTS: list[ThoughtEntry] = ["t0", "t1", "t2", "t3"]


# ==========================================================================
# 1 — THE ORDER: the silent failure mode, asserted as whole ordered lists
# ==========================================================================


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4])
def test_a_diary_written_after_k_thoughts_walks_after_exactly_those_k(
    k: int,
) -> None:
    """For every k, the entry sits after thought k-1 and before thought k.

    Swept across the whole range including both ends, because the ends are
    where an off-by-one hides: ``k == 0`` (the entry leads, nothing before it)
    and ``k == len(thoughts)`` (the entry closes, nothing after it) are exactly
    the cases a ``<=``-for-``<`` slip renders identically at the middle values.

    The expectation is the WHOLE list, spliced at ``k``, so a walk that put the
    entry anywhere else fails on the sequence rather than on a count.
    """
    walked = _walk_private_record(_FOUR_THOUGHTS, [_rec(1, k, "ENTRY")])

    expected = [
        *[_thought(t) for t in _FOUR_THOUGHTS[:k]],
        _diary(1, "ENTRY"),
        *[_thought(t) for t in _FOUR_THOUGHTS[k:]],
    ]
    assert walked == expected


def test_several_days_interleave_exactly_as_one_ordered_sequence() -> None:
    """The property a story's chronology actually rests on, as one whole list.

    Three Days' entries against eight thoughts, with every entry's cursor at a
    different cut, so the correct answer is a strict interleave and no simpler
    rule (all-thoughts-first, all-diaries-first, one-diary-per-two-thoughts)
    reproduces it.
    """
    thoughts: list[ThoughtEntry] = [f"t{i}" for i in range(8)]
    diaries = [_rec(1, 2, "E1"), _rec(2, 5, "E2"), _rec(3, 7, "E3")]

    assert _walk_private_record(thoughts, diaries) == [
        _thought("t0"),
        _thought("t1"),
        _diary(1, "E1"),
        _thought("t2"),
        _thought("t3"),
        _thought("t4"),
        _diary(2, "E2"),
        _thought("t5"),
        _thought("t6"),
        _diary(3, "E3"),
        _thought("t7"),
    ]


def test_the_walk_is_neither_thoughts_then_diaries_nor_diaries_then_thoughts()\
        -> None:
    """THE TWO DEGENERATE ORDERS, ruled out by name.

    Both hold exactly the right entries with exactly the right words, so every
    membership assertion in this file would pass against either. They are also
    the two shapes the likeliest mistakes produce — appending each diary after
    the loop instead of inside it, or hoisting the trailing
    ``notes[emitted:]`` extend above the diary loop. Asserted as *inequalities*
    against the real answer so the test says which wrong order it caught.
    """
    thoughts: list[ThoughtEntry] = ["t0", "t1"]
    diaries = [_rec(1, 1, "E1"), _rec(2, 2, "E2")]

    walked = _walk_private_record(thoughts, diaries)

    thoughts_then_diaries = [
        _thought("t0"), _thought("t1"), _diary(1, "E1"), _diary(2, "E2")
    ]
    diaries_then_thoughts = [
        _diary(1, "E1"), _diary(2, "E2"), _thought("t0"), _thought("t1")
    ]

    # The premise: all three orders are permutations of one another, so the
    # inequalities below are about ORDER and nothing else.
    assert sorted(walked) == sorted(thoughts_then_diaries)
    assert sorted(walked) == sorted(diaries_then_thoughts)

    assert walked != thoughts_then_diaries, (
        "the walk emitted every thought before every diary — the cursors were "
        "not consulted"
    )
    assert walked != diaries_then_thoughts, (
        "the walk emitted every diary before every thought — the trailing "
        "remainder was hoisted above the loop"
    )
    assert walked == [
        _thought("t0"), _diary(1, "E1"), _thought("t1"), _diary(2, "E2")
    ]


@pytest.mark.parametrize(
    "cursors",
    [
        (0, 0, 0),
        (1, 2, 3),
        (3, 3, 3),
        (3, 1, 2),          # a cursor that went BACKWARDS
        (9, 9, 9),          # cursors past the notes in hand
        (-3, 2, 99),        # a negative and an out-of-range one together
        (True, "2", None),  # nonsensical shapes: bool, str, missing-ish
    ],
)
def test_every_thought_appears_exactly_once_in_order_whatever_the_cursors_say(
    cursors: tuple[object, ...],
) -> None:
    """THE CLAMP INVARIANT, as the observable property it implies.

    Deliberately NOT "the cursor equals k": tech-spec §2.4 records that a
    cursor can legitimately point at notes that are not there (an entry written
    with thoughts ON, read back with thoughts OFF; a resumed checkpoint from
    the other setting), and a malformed record can carry any shape at all. What
    the walk must guarantee under all of that is that it stays a MERGE: every
    thought appears exactly once, in its original order, and every diary
    appears exactly once, in its original order.

    A walk that dropped, doubled or reordered thoughts on a surprising cursor
    would still produce a well-formed block, which is why this is asserted
    directly rather than inferred from the happy path.
    """
    thoughts: list[ThoughtEntry] = ["t0", "t1", "t2", "t3"]
    diaries: list[DiaryRecord] = [
        {"day": i, "thoughts_before": c, "text": f"E{i}"}  # type: ignore[typeddict-item]
        for i, c in enumerate(cursors, 1)
    ]

    walked = _walk_private_record(thoughts, diaries)

    assert [
        text for kind, _, text in walked if kind == PRIVATE_RECORD_KIND_THOUGHT
    ] == ["t0", "t1", "t2", "t3"]
    assert [
        text for kind, _, text in walked if kind == PRIVATE_RECORD_KIND_DIARY
    ] == [f"E{i}" for i in range(1, len(cursors) + 1)]
    assert len(walked) == len(thoughts) + len(diaries)


# ==========================================================================
# 2 — THE SAFETY CONSTRAINT: the walk never windows
# ==========================================================================


def test_the_walk_never_windows_the_diaries() -> None:
    """§2.3's safety constraint: the list it is handed is the list it walks.

    The Moderator is the one reader for whom the FULL diary is the point, so
    ``DIARY_WINDOW`` must not be reachable from inside the walk. Six entries,
    twice the window, all six present and in order.

    Non-vacuous by construction: the count is asserted against a bare literal
    ``DIARY_WINDOW == 3`` as well, so a future window of six could not make
    this row pass by coincidence.
    """
    assert DIARY_WINDOW == 3
    diaries = [_rec(day, 0, f"E{day}") for day in range(1, 7)]

    walked = _walk_private_record([], diaries)

    assert walked == [_diary(day, f"E{day}") for day in range(1, 7)]
    assert len(walked) == 6 > DIARY_WINDOW


def test_the_block_windows_and_the_walk_does_not() -> None:
    """The division of labour, pinned from both sides at once.

    The same six entries: the player's block shows the last ``DIARY_WINDOW``,
    and the shared walk — handed the full list — shows all six. This is the
    assertion that would catch a "tidy-up" moving the window down into the walk
    (which would silently cap the Moderator at three entries per player) or up
    out of the block (which would hand a player its whole diary).
    """
    diaries = [_rec(day, 0, f"E{day}") for day in range(1, 7)]

    block = _private_record_block(
        [], diaries, thoughts_enabled=True, diaries_enabled=True
    )
    block_days = [
        line for line in block.splitlines() if DIARY_LINE_MARKER in line
    ]

    assert len(block_days) == DIARY_WINDOW == 3
    assert "E4" in block and "E5" in block and "E6" in block
    assert "E1" not in block and "E2" not in block and "E3" not in block

    walked = _walk_private_record([], diaries)
    assert [text for _, _, text in walked] == [f"E{d}" for d in range(1, 7)]


@pytest.mark.parametrize("diaries", [None, []])
def test_no_diaries_walks_the_thoughts_alone_in_order(
    diaries: list[DiaryRecord] | None,
) -> None:
    """``None`` and ``[]`` are the same "nothing to merge" case.

    ``None`` is accepted so a caller with no diary channel at all — a game
    resumed from a pre-039 checkpoint, or a player who never survived to write
    one — needs no sentinel of its own.
    """
    assert _walk_private_record(_FOUR_THOUGHTS, diaries) == [
        _thought(t) for t in _FOUR_THOUGHTS
    ]


def test_no_thoughts_walks_the_diaries_alone_in_order() -> None:
    """The mirror case, which is real rather than theoretical.

    A player killed on the very first Night wrote nothing at all, and the
    thoughts channel is empty for every player until the first Day round
    closes — so a diary-only walk is the shape the Moderator will meet.
    """
    diaries = [_rec(1, 3, "E1"), _rec(2, 7, "E2")]

    assert _walk_private_record([], diaries) == [
        _diary(1, "E1"), _diary(2, "E2")
    ]


# ==========================================================================
# 3 — THE ATTRIBUTION SLOTS Task 3.2's third-party renderer reads
# ==========================================================================


def test_a_diary_carries_its_day_and_a_thought_carries_none() -> None:
    """The ``day`` slot's contract, and why ``None`` is honest.

    ``ThoughtRecord`` deliberately carries no ``day`` field (a thought is
    written per Day *round*, and the transcript renders it inside its
    ``<round>``, so the round is positional there). So ``None`` here is "this
    channel has no Day", not a lost value — and a renderer attributing a
    thought must do it without one.
    """
    walked = _walk_private_record(["t0"], [_rec(2, 1, "E")])

    kinds = [kind for kind, _, _ in walked]
    days = [day for _, day, _ in walked]

    assert kinds == [PRIVATE_RECORD_KIND_THOUGHT, PRIVATE_RECORD_KIND_DIARY]
    assert days == [None, 2]
    assert isinstance(days[1], int)


def test_a_record_with_no_usable_day_still_walks_carrying_the_placeholder()\
        -> None:
    """A record with no ``day`` still holds an entry worth showing.

    The degrade-rather-drop posture the whole private-record path takes: the
    Day falls back to ``DIARY_DAY_UNKNOWN`` and the entry survives, matching
    how ``eval_transcript._diary_day_attr`` omits the attribute and still
    renders the element. Pinned against the bare literal too, since a
    placeholder derived from the constant would be true at any value.
    """
    assert DIARY_DAY_UNKNOWN == "?"

    walked = _walk_private_record([], [{"thoughts_before": 0, "text": "E"}])

    assert walked == [_diary(DIARY_DAY_UNKNOWN, "E")]
    assert walked == [_diary("?", "E")]


def test_the_two_kinds_are_distinct_bare_string_constants() -> None:
    """ANTI-VACUITY over the discriminator every consumer branches on.

    Two constants that happened to be equal — or that carried a format
    placeholder, the vacuity shape spec 039's suite guards against — would make
    every ``kind ==`` assertion in this file and every branch in both renderers
    meaningless while staying green.
    """
    assert PRIVATE_RECORD_KIND_THOUGHT == "thought"
    assert PRIVATE_RECORD_KIND_DIARY == "diary"
    assert PRIVATE_RECORD_KIND_THOUGHT != PRIVATE_RECORD_KIND_DIARY
    for kind in (PRIVATE_RECORD_KIND_THOUGHT, PRIVATE_RECORD_KIND_DIARY):
        assert "{" not in kind and "}" not in kind


_MIXED_NOTES: list[ThoughtEntry] = [
    "from a resumed checkpoint",
    {"text": "from this session"},
    {"text": "a deterministic stand-in", "substituted": True},
    {"text": "authored again"},
]
_MIXED_WORDS: list[ThoughtEntry] = [
    "from a resumed checkpoint",
    "from this session",
    "a deterministic stand-in",
    "authored again",
]


@pytest.mark.parametrize("cursor", [0, 1, 2, 3, 4])
def test_a_mixed_shape_thought_list_walks_identically_to_an_all_string_one(
    cursor: int,
) -> None:
    """Spec 041's element reshape is invisible to the walk, at every cut.

    Swept over the cursor rather than sampled, because the sweep is what puts
    the mixed elements on BOTH SIDES of the diary entry: the walk reads the
    thoughts in TWO places (``notes[emitted:stop]`` inside the loop and
    ``notes[emitted:]`` after it), so a conversion applied to one and not the
    other passes some rows and fails others.

    The equality is against the all-``str`` list — what a pre-reshape
    checkpoint holds and what most of the offline suite hand-builds — so what
    is asserted is that the shape does not reach a consumer.
    """
    mixed = _walk_private_record(_MIXED_NOTES, [_rec(1, cursor, "ENTRY")])
    all_strings = _walk_private_record(_MIXED_WORDS, [_rec(1, cursor, "ENTRY")])

    assert mixed == all_strings
    assert len(mixed) == 5
    # ...and no element reached a consumer as a dict repr, said in its own
    # words so a failure names the defect rather than showing a diff.
    assert all(isinstance(text, str) for _, _, text in mixed)
    assert not any("'text'" in text for _, _, text in mixed), (
        "a thought element came out of the walk as its dict repr — a slice "
        "bound in `_walk_private_record` is not reading through `thought_text`"
    )


def test_a_malformed_thought_element_costs_one_empty_text_not_a_raise() -> None:
    """The seam's degrade-rather-than-raise posture, at the walk.

    ``""`` means "nothing usable here" and the CALLER owns what to do with it —
    ``thought_text``'s documented contract, because its two families of caller
    want opposite things (the transcript renderers drop a blank; the prompt
    block renders one bare ``"- "`` bullet). The walk therefore keeps the
    entry.
    """
    walked = _walk_private_record(
        ["real", {}, {"text": None}, "also real"], [_rec(1, 2, "ENTRY")]
    )

    assert walked == [
        _thought("real"),
        _thought(""),
        _diary(1, "ENTRY"),
        _thought(""),
        _thought("also real"),
    ]


def test_the_walk_surfaces_no_substitution_marking() -> None:
    """SETTLED DESIGN, pinned so a later slice cannot drift into it.

    A substituted thought and a substituted entry walk as their words alone.
    The marking belongs to the eval transcript and the ledger viewer; surfacing
    it here would put it into a prompt, which changes what a player reasons
    from — a game-design change, and not this task's business.

    Non-vacuous by construction: both marked bodies are asserted PRESENT first,
    so a walk that had stopped emitting them would fail rather than satisfy the
    absence trivially.
    """
    marked_entry: DiaryRecord = {
        "day": 1,
        "thoughts_before": 1,
        "text": "an entry that is a stand-in",
        "substituted": True,
    }
    walked = _walk_private_record(
        [{"text": "a thought that is a stand-in", "substituted": True}],
        [marked_entry],
    )

    assert walked == [
        _thought("a thought that is a stand-in"),
        _diary(1, "an entry that is a stand-in"),
    ]
    assert all(len(entry) == 3 for entry in walked)


# ==========================================================================
# 4 — PURITY, and the flag-free signature
# ==========================================================================


def test_the_walk_mutates_neither_argument() -> None:
    """A caller may hand over a live state list, so the walk must not touch it.

    ``moderator_story`` will pass ``state["private_diaries"][pid]`` and
    ``state["private_thoughts"][pid]`` straight in. Mutating either would
    corrupt the channel a checkpoint replays from, and it would do so silently.
    """
    thoughts: list[ThoughtEntry] = ["t0", "t1"]
    diaries = [_rec(1, 1, "E1"), _rec(2, 2, "E2")]
    thoughts_before = list(thoughts)
    diaries_before = [dict(record) for record in diaries]

    _walk_private_record(thoughts, diaries)

    assert thoughts == thoughts_before
    assert [dict(record) for record in diaries] == diaries_before


def test_the_walk_takes_no_ablation_flags() -> None:
    """FLAG-FREE ON PURPOSE — the ADR-011 seams stay with the prompt block.

    ``_private_record_block`` owns ``thoughts_enabled`` / ``diaries_enabled``
    because it owns the prompt slot those flags collapse. The walk suppresses a
    channel the only way a pure ordering function should: the caller passes an
    empty list. A flag here would put an ablation seam under a consumer
    (spec 040's endgame renderer) that has no ablation arm at all (§2.12).
    """
    sig = inspect.signature(_walk_private_record)

    assert list(sig.parameters) == ["thoughts", "diaries"]
    for param in sig.parameters.values():
        assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert "enabled" not in str(sig)


# ==========================================================================
# 5 — BYTE-IDENTITY OF WHAT A PLAYER SEES
# ==========================================================================
#
# THE REGRESSION THIS REFACTOR RISKS, and the one property no amount of walk
# testing can imply: a player's rendered block must be unchanged, to the byte,
# including for the malformed records the channel is defensive about.
#
# The oracle below is a FROZEN VERBATIM COPY of the pre-extraction body of
# ``_private_record_block`` (git ``5f97bdc``). It is deliberately NOT factored,
# NOT shared with the code under test, and must NEVER be edited to agree with a
# change in behaviour: if a later spec deliberately changes what a player sees,
# this oracle and its test are DELETED with that change, not updated. Editing
# it to match would convert the only parity proof into a tautology.
#
# It does call ``_private_thoughts_block`` and read ``DIARY_WINDOW`` /
# ``DIARY_LINE_MARKER`` / ``PRIVATE_THOUGHTS_LABEL`` from the module, and that
# is correct rather than a leak: the delegation branch, the window value and
# the marker text are spec 039's, pinned by its own suite. What is frozen here
# is the WALK and the two line shapes it feeds — exactly what Task 3.1 moved.


def _frozen_pre_extraction_block(
    thoughts: list[ThoughtEntry],
    diaries: list[DiaryRecord] | None = None,
    *,
    thoughts_enabled: bool,
    diaries_enabled: bool,
) -> str:
    """``_private_record_block`` as it stood before the walk was extracted."""
    window = diaries[-DIARY_WINDOW:] if diaries_enabled and diaries else []
    if not window:
        return _private_thoughts_block(thoughts, enabled=thoughts_enabled)

    notes = list(thoughts) if thoughts_enabled else []
    lines: list[str] = []
    emitted = 0
    for record in window:
        cursor = record.get("thoughts_before", 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            cursor = 0
        stop = max(emitted, min(cursor, len(notes)))
        lines.extend(f"- {thought_text(note)}" for note in notes[emitted:stop])
        emitted = stop
        day = record.get("day", "?")
        text = record.get("text", "")
        lines.append(f"- {DIARY_LINE_MARKER} {day}] {text}".rstrip())
    lines.extend(f"- {thought_text(note)}" for note in notes[emitted:])

    bullets = "\n".join(lines)
    return f"\n{PRIVATE_THOUGHTS_LABEL}\n{bullets}\n"


_PARITY_THOUGHTS: dict[str, list[ThoughtEntry]] = {
    "none": [],
    "one": ["t0"],
    "four": list(_FOUR_THOUGHTS),
    "mixed": list(_MIXED_NOTES),
    "malformed": ["real", {}, {"text": None}, {"text": 5}, "also real"],
}

# Every diary shape the channel can present, well-formed and not. The malformed
# rows are the point of the matrix: they are where an "obvious" tidy-up during
# extraction (normalising a non-``int`` Day to the placeholder, coercing a
# non-``str`` body, rstripping the thought bullets too) changes a byte.
_PARITY_DIARIES: dict[str, list[DiaryRecord] | None] = {
    "null": None,
    "empty": [],
    "one_k0": [_rec(1, 0, "E1")],
    "one_k2": [_rec(1, 2, "E1")],
    "one_k4": [_rec(1, 4, "E1")],
    "one_k9": [_rec(1, 9, "E1")],
    "three": [_rec(1, 1, "E1"), _rec(2, 2, "E2"), _rec(3, 4, "E3")],
    "six_over_the_window": [
        _rec(day, day - 1, f"E{day}") for day in range(1, 7)
    ],
    "cursor_backwards": [_rec(1, 3, "E1"), _rec(2, 1, "E2")],
    "cursor_negative": [_rec(1, -3, "E1")],
    "cursor_bool": [{"day": 1, "thoughts_before": True, "text": "E1"}],
    "cursor_str": [{"day": 1, "thoughts_before": "2", "text": "E1"}],
    "no_day": [{"thoughts_before": 1, "text": "NO-DAY"}],
    "day_none": [{"day": None, "thoughts_before": 1, "text": "DAY-NONE"}],
    "day_str": [{"day": "2", "thoughts_before": 1, "text": "DAY-STR"}],
    "day_bool": [{"day": True, "thoughts_before": 1, "text": "DAY-BOOL"}],
    "day_float": [{"day": 2.5, "thoughts_before": 1, "text": "DAY-FLOAT"}],
    "no_text": [{"day": 1, "thoughts_before": 1}],
    "text_int": [{"day": 1, "thoughts_before": 1, "text": 7}],
    "text_none": [{"day": 1, "thoughts_before": 1, "text": None}],
    "empty_record": [{}],
    "trailing_space": [_rec(1, 1, "TRAILING   ")],
}


@pytest.mark.parametrize("diary_shape", sorted(_PARITY_DIARIES))
def test_the_block_is_byte_identical_to_the_frozen_pre_extraction_renderer(
    diary_shape: str,
) -> None:
    """What a player sees is unchanged, to the byte, over every shape and cell.

    One row per diary shape; inside each row, every thought shape crossed with
    all four cells of the ADR-011 flag cross-product — twenty comparisons a
    row, so the row's id names the shape that broke.

    Whole-string equality against the frozen oracle, not a property check: the
    label, the bullets, the diary tag, the framing newlines, the diary line's
    ``.rstrip()`` and the thought bullet's PRESERVED trailing space are all in
    scope at once. That last asymmetry is the one a tidy-up reaches for first.
    """
    diaries = _PARITY_DIARIES[diary_shape]

    for thought_shape, (t_on, d_on) in itertools.product(
        sorted(_PARITY_THOUGHTS), itertools.product((True, False), repeat=2)
    ):
        thoughts = list(_PARITY_THOUGHTS[thought_shape])
        cell = f"{thought_shape}/{diary_shape}/t={t_on}/d={d_on}"

        live = _private_record_block(
            thoughts,
            None if diaries is None else list(diaries),
            thoughts_enabled=t_on,
            diaries_enabled=d_on,
        )
        frozen = _frozen_pre_extraction_block(
            thoughts,
            None if diaries is None else list(diaries),
            thoughts_enabled=t_on,
            diaries_enabled=d_on,
        )

        assert live == frozen, cell


def test_the_block_reads_its_order_from_the_shared_walk() -> None:
    """THE ANTI-DUPLICATION GUARD: the block does not keep its own copy.

    §2.3's whole decision is that ONE walk serves both renderers. Every other
    test in this file would stay green if someone re-inlined the walk into
    ``_private_record_block`` and left ``_walk_private_record`` unused beside
    it — the two copies would agree on the day they were written, and drift
    later, silently. This test fails the moment the block stops asking.

    Substituting a sentinel walk rather than spying on a call, so what is
    pinned is that the block's ORDER and CONTENT come from the return value,
    not merely that the name was touched.
    """
    sentinel = [
        (PRIVATE_RECORD_KIND_DIARY, 9, "SENTINEL-DIARY"),
        (PRIVATE_RECORD_KIND_THOUGHT, None, "SENTINEL-THOUGHT"),
    ]

    def _fake_walk(
        thoughts: list[ThoughtEntry], diaries: list[DiaryRecord] | None
    ) -> list[tuple[str, int | str | None, str]]:
        return list(sentinel)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(day_nodes, "_walk_private_record", _fake_walk)
        block = _private_record_block(
            ["a real thought"],
            [_rec(1, 0, "a real entry")],
            thoughts_enabled=True,
            diaries_enabled=True,
        )

    assert block == (
        f"\n{PRIVATE_THOUGHTS_LABEL}\n"
        f"- {DIARY_LINE_MARKER} 9] SENTINEL-DIARY\n"
        f"- SENTINEL-THOUGHT\n"
    )
    assert "a real thought" not in block
    assert "a real entry" not in block
