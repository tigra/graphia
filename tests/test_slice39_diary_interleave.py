"""Spec 039 (Per-AI Private Diaries) — Slice 2: the merged private record.

Slice 1 proved a diary gets *written*. This file proves it gets *read back*
correctly: ``nodes/day.py:_private_record_block`` merges one player's own
before-Night diary entries into that same player's own Day-round thoughts
(spec 028) as ONE train of thought in event order, tags only the diary lines,
windows the diaries to ``DIARY_WINDOW`` before merging, and delegates verbatim
to ``_private_thoughts_block`` whenever there is no diary to show.

Why a separate file from ``tests/test_slice39_diary_before_night.py``
--------------------------------------------------------------------

That file is Slice 1's, and it is about the WRITE path — the node, its guards,
the record it emits, the transcript it produces. It already runs 48 test
functions over 1.7k lines. Slice 2 is the READ-BACK path: a pure merge
function, the ADR-011 flag cross-product over a shared prompt slot, and the
privacy invariant at four call sites. Different surface, different failure
modes, so it gets its own file rather than a fifth section bolted onto a file
that is already the longest in the suite. Nothing here duplicates Slice 1: the
cursor's *capture* (``thoughts_before`` is the writer's own thought count at
write time) is pinned there; the cursor's *use* is pinned here.

The five sections, and why each earns its place
-----------------------------------------------

1. **The merge rule.** A diary written after *k* thoughts renders after exactly
   those *k*, for every *k* from 0 to ``len(thoughts)``.
2. **Interleaving across several Days**, exact, as a whole-string comparison —
   the property a player's own reasoning actually depends on.
3. **The cursor-clamp invariant.** Explicitly NOT ``k == 0``: tech-spec §2.4
   records that ``k == 0`` in the thoughts-OFF cell is a coincidence of a
   whole-run flag, not an invariant. Entries written while thoughts were ON
   carry ``k > 0``, and flipping the flag mid-campaign or resuming a checkpoint
   written under the other setting yields cursors pointing at notes that are
   not there. What the code actually needs is that **the cursor is clamped to
   the notes in hand** — pinned here as the observable property it implies:
   *every thought appears exactly once, in its original order, whatever the
   cursors say.*
4. **The window.** Three or fewer shows all; a fourth drops the oldest **from
   the block but not from the transcript**; and dropping it merges the two
   thought runs it separated without losing a single thought.
5. **The flag cross-product and privacy.** One test per cell of tech-spec
   §2.4's table, including the two byte-identity-by-delegation cells, plus the
   composed contract; and no cross-player leak at any of the four call sites.

Anti-vacuity discipline
-----------------------

Two shapes are already proven vacuous in this spec and are guarded against
throughout:

- **An f-string bound test is true at any value.** Every numeric constant gets
  a BARE-LITERAL pin alongside any relational check — ``DIARY_WINDOW == 3`` is
  the one in scope here.
- **A marker constant that is a template makes every flag-off
  ``DIARY_LINE_MARKER not in prompt`` assertion vacuously true**, because a
  literal ``{day}`` never appears in a rendered prompt. So the marker is pinned
  as a brace-free invariant PREFIX that is separately asserted to be *present*
  in a rendered flag-on block.

And ``test_dual_mode_smoke`` does NOT cover the privacy invariant — it scripts
no ``diaries=``, so a mutation leaking every entry into the public message
stream leaves it green. Section 5's assertions are the only cover.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import graphia.nodes.day as day_nodes
import graphia.nodes.night as night_nodes
from graphia.llm import Ballot, DayAction, Diary, Pointing, Reflection
from graphia.nodes.day import (
    DIARY_LINE_MARKER,
    DIARY_WINDOW,
    PRIVATE_THOUGHTS_LABEL,
    _ai_ballot,
    _ai_day_action,
    _private_record_block,
    _private_thoughts_block,
    collect_votes,
    day_diary,
    day_round_reflect,
    day_turn,
)
from graphia.nodes.night import _ai_pick_target, mafia_point
from graphia.state import DiaryRecord, GameState, PlayerState
from graphia.tools.eval_transcript import render_transcript

# ==========================================================================
# Shared hand-built helpers
# ==========================================================================


def _player(
    pid: str,
    name: str,
    role: str,
    *,
    is_human: bool = False,
    is_alive: bool = True,
) -> PlayerState:
    return PlayerState(
        id=pid,
        name=name,
        role=role,  # type: ignore[arg-type]
        is_human=is_human,
        is_alive=is_alive,
    )


def _rec(day: int, cursor: int, text: str) -> DiaryRecord:
    """One ``DiaryRecord``. ``cursor`` is ``thoughts_before``."""
    return {"day": day, "thoughts_before": cursor, "text": text}


def _diary_line(day: object, text: str) -> str:
    """The rendered bullet for one diary entry, built from the marker constant."""
    return f"- {DIARY_LINE_MARKER} {day}] {text}"


def _block(*lines: str) -> str:
    """The full expected block: framing newlines, the 028 label, then bullets."""
    body = "\n".join(lines)
    return f"\n{PRIVATE_THOUGHTS_LABEL}\n{body}\n"


def _bullets(block: str) -> list[str]:
    """Every ``- `` bullet line of a rendered block, in order."""
    return [line for line in block.splitlines() if line.startswith("- ")]


def _thought_bullets(block: str) -> list[str]:
    """The BARE (untagged) bullets — the thought lines only."""
    return [line for line in _bullets(block) if DIARY_LINE_MARKER not in line]


def _diary_bullets(block: str) -> list[str]:
    """The TAGGED bullets — the diary lines only."""
    return [line for line in _bullets(block) if DIARY_LINE_MARKER in line]


class _CapturingFake:
    """A ``get_large()`` stand-in that records every prompt it is handed.

    Mirrors ``tests/test_slice28_private_thoughts.py:_CapturingFake``. Returns
    one scripted structured output regardless of the bound schema, which is all
    the four call sites under test need.
    """

    def __init__(self, output: Any) -> None:
        self._output = output
        self.messages_log: list[Any] = []

    def with_structured_output(self, schema: type, **kwargs: object) -> "_CapturingFake":
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        return self._output


def _human_prompt(messages: Any) -> str:
    """The rendered ``HumanMessage`` text from a captured ``[System, Human]``."""
    human = messages[1]
    assert isinstance(human, HumanMessage)
    return human.content


# ==========================================================================
# Slice 2.1 — THE MERGE RULE: a diary lands after exactly its own k thoughts
# ==========================================================================
#
# The single highest-value property in the spec. ``thoughts_before`` is the ONLY
# ordering metadata that exists — spec 028's channel is bare strings and spec
# 039 must not touch it — so if the merge misreads the cursor a player's own
# history renders in the wrong order inside its own prompt. That produces
# perfectly well-formed output and never crashes, so nothing but a direct
# assertion catches it.

_FOUR_THOUGHTS = ["t0", "t1", "t2", "t3"]


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4])
def test_a_diary_written_after_k_thoughts_renders_after_exactly_those_k(
    k: int,
) -> None:
    """For every k, the diary line sits after thought k-1 and before thought k.

    Swept across the whole range including both ends, because the two ends are
    where an off-by-one hides: ``k == 0`` (the entry leads the block, no thought
    before it) and ``k == len(thoughts)`` (the entry closes the block, no
    thought after it) are exactly the cases a ``<=``-for-``<`` slip renders
    identically at the middle values.
    """
    block = _private_record_block(
        _FOUR_THOUGHTS,
        [_rec(1, k, "ENTRY")],
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    expected = _block(
        *[f"- {note}" for note in _FOUR_THOUGHTS[:k]],
        _diary_line(1, "ENTRY"),
        *[f"- {note}" for note in _FOUR_THOUGHTS[k:]],
    )
    assert block == expected


def test_the_merged_block_keeps_028s_label_and_framing_newlines() -> None:
    """The merged block reuses spec 028's slot verbatim: same label, same frame.

    The tech spec forbids a fourth ``{diaries}`` slot (it would ``KeyError`` at
    16 ``.format()`` call sites and two slots cannot express event order at
    all), so diaries ride in the EXISTING one under the UNCHANGED heading. A
    renamed label would also break ``blunder_eval:_speaker_anchor``, which
    derives its speaker resolver from ``DAY_SPEAK_USER_TEMPLATE``'s literal
    prefix.
    """
    block = _private_record_block(
        ["t0"], [_rec(1, 1, "E")], thoughts_enabled=True, diaries_enabled=True
    )

    assert block.startswith(f"\n{PRIVATE_THOUGHTS_LABEL}\n")
    assert block.endswith("\n")
    assert block.count(PRIVATE_THOUGHTS_LABEL) == 1


def test_only_the_diary_lines_are_tagged() -> None:
    """Thought lines stay bare ``- ``; the diaries-on delta is purely insertion.

    The design property this pins: turning diaries ON adds lines to an otherwise
    byte-unchanged 028 block. Tagging the thoughts too — or tagging nothing —
    would still read fine to a human and still merge in the right order.
    """
    block = _private_record_block(
        ["t0", "t1"],
        [_rec(1, 1, "E1"), _rec(2, 2, "E2")],
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert _thought_bullets(block) == ["- t0", "- t1"]
    assert len(_diary_bullets(block)) == 2
    assert block.count(DIARY_LINE_MARKER) == 2


def test_the_tag_carries_the_entrys_own_day_number() -> None:
    """Each tag names the Day that entry sums up — not the position in the list.

    A renderer that numbered by enumeration index would produce ``Day 1`` /
    ``Day 2`` here and look correct; the entries are deliberately Days 4 and 7.
    """
    block = _private_record_block(
        [],
        [_rec(4, 0, "FOUR"), _rec(7, 0, "SEVEN")],
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert _diary_bullets(block) == [
        _diary_line(4, "FOUR"),
        _diary_line(7, "SEVEN"),
    ]


def test_the_builder_mutates_neither_input() -> None:
    """PURE: the caller's thought list and diary list come back untouched.

    The four call sites hand in lists read straight out of ``state``; a merge
    that popped from them would corrupt the channel for the next reader.

    Deliberately FOUR entries, one over the window. An in-place window — ``del
    diaries[:-DIARY_WINDOW]`` instead of a slice — is a no-op on any shorter
    list, so a three-entry fixture would leave this test's own name unearned
    and the defect to be caught incidentally somewhere else.
    """
    thoughts = ["t0", "t1"]
    original = [_rec(1, 1, "E1"), _rec(2, 2, "E2"), _rec(3, 2, "E3"), _rec(4, 2, "E4")]
    diaries = list(original)

    _private_record_block(
        thoughts, diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert thoughts == ["t0", "t1"]
    assert diaries == original


# ==========================================================================
# Slice 2.2 — interleaving stays exact across several Days
# ==========================================================================


def test_interleaving_is_exact_across_several_days() -> None:
    """Three Days of entries against seven thoughts, pinned as one whole string.

    A whole-string comparison rather than a bag of ``in`` checks: the failure
    this guards against is an ORDERING defect, and every substring assertion
    that could be written here would pass under a wrong order.

    The cursors (2, 5, 7) are deliberately unequal and the last one equals
    ``len(thoughts)``, so the final entry closes the block with no trailing
    thought — the case where a "flush the remainder" step that runs
    unconditionally would emit a stray blank bullet.
    """
    thoughts = [f"t{i}" for i in range(7)]
    diaries = [
        _rec(1, 2, "DAY-ONE"),
        _rec(2, 5, "DAY-TWO"),
        _rec(3, 7, "DAY-THREE"),
    ]

    block = _private_record_block(
        thoughts, diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert block == _block(
        "- t0",
        "- t1",
        _diary_line(1, "DAY-ONE"),
        "- t2",
        "- t3",
        "- t4",
        _diary_line(2, "DAY-TWO"),
        "- t5",
        "- t6",
        _diary_line(3, "DAY-THREE"),
    )


def test_trailing_thoughts_after_the_last_diary_are_kept() -> None:
    """Thoughts written after the newest entry close the block, in order.

    The mirror of the case above: a merge that stopped walking at the last
    diary would silently drop the current Day's reflections — the freshest and
    most decision-relevant notes a player has.
    """
    block = _private_record_block(
        ["t0", "t1", "t2"],
        [_rec(1, 1, "E")],
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert block == _block(
        "- t0", _diary_line(1, "E"), "- t1", "- t2"
    )


def test_two_entries_sharing_a_cursor_stay_adjacent_in_write_order() -> None:
    """Equal cursors ⇒ adjacent diary lines, in write order, no thought between.

    Happens whenever a Day produced no reflections at all (an execution or the
    vote cap closes a Day before any round wraps). A merge implemented as a SORT
    rather than a stable two-way walk can reorder ties; this pins the walk.
    """
    block = _private_record_block(
        ["t0", "t1"],
        [_rec(1, 1, "FIRST"), _rec(2, 1, "SECOND")],
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert block == _block(
        "- t0",
        _diary_line(1, "FIRST"),
        _diary_line(2, "SECOND"),
        "- t1",
    )


# ==========================================================================
# Slice 2.3 — THE CURSOR-CLAMP INVARIANT (not the ``k == 0`` coincidence)
# ==========================================================================
#
# Tech-spec §2.4, corrected: ``k == 0`` in the thoughts-OFF cell is a
# coincidence of a WHOLE-RUN flag, not an invariant. An entry written while
# spec 028's thoughts were ON carries ``k > 0`` for ever, and that entry can be
# read back with thoughts OFF — by flipping the flag mid-campaign, or by
# resuming a checkpoint written under the other setting. The cursor then points
# at notes that are not there.
#
# So the invariant pinned here is NOT "k is 0 when thoughts are off". It is the
# observable consequence of clamping the cursor to the notes in hand:
#
#     every thought appears exactly once, in its original order,
#     whatever the cursors say.
#
# That holds for a cursor past the end of the list, for a cursor that goes
# backwards, for a negative cursor, and for a garbage cursor — and it is what
# the four call sites actually depend on.

_PATHOLOGICAL_CURSORS = [
    pytest.param([0], id="cursor-0-with-notes-present"),
    pytest.param([99], id="cursor-far-past-the-end"),
    pytest.param([3, 1], id="cursor-goes-backwards"),
    pytest.param([-4], id="negative-cursor"),
    pytest.param([2, 2], id="repeated-cursor"),
    pytest.param([0, 99, 1], id="mixed-out-of-order-and-past-end"),
    pytest.param([5, 5, 5], id="all-at-the-end"),
]


@pytest.mark.parametrize("cursors", _PATHOLOGICAL_CURSORS)
def test_every_thought_appears_exactly_once_in_order_whatever_the_cursors(
    cursors: list[int],
) -> None:
    """THE CLAMP INVARIANT. No duplication, no loss, no reordering, no raise.

    Deliberately stated as a property over pathological cursors rather than as
    ``k == 0``, which tech-spec §2.4 records as a coincidence of a whole-run
    flag. Any of these cursor lists is reachable in production: an entry
    written under one flag setting and read back under another, a checkpoint
    resumed after a flag flip, or a hand-edited state.

    Dropping the ``max(emitted, …)`` guard makes ``cursor-goes-backwards``
    re-emit thoughts already emitted — the block then shows a player the same
    note twice and reads as if it had reflected more than it did.
    """
    thoughts = [f"t{i}" for i in range(5)]
    diaries = [_rec(day, cursor, f"E{day}") for day, cursor in enumerate(cursors, 1)]

    block = _private_record_block(
        thoughts, diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert _thought_bullets(block) == [f"- {note}" for note in thoughts], (
        "every thought must appear exactly once and in its original order, "
        f"whatever the cursors say; cursors were {cursors!r}"
    )
    assert len(_diary_bullets(block)) == len(diaries)


def test_a_cursor_pointing_past_the_notes_in_hand_renders_the_notes_it_has() -> None:
    """An entry written with 9 thoughts, read back with 2, shows those 2 first.

    The concrete mid-campaign case §2.4 names. The clamp keeps the entry AFTER
    the notes that do exist, which is the truthful placement: those two notes
    were written before the entry.
    """
    block = _private_record_block(
        ["t0", "t1"],
        [_rec(3, 9, "WRITTEN-WHEN-NINE-EXISTED")],
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert block == _block(
        "- t0", "- t1", _diary_line(3, "WRITTEN-WHEN-NINE-EXISTED")
    )


def test_the_merge_is_stable_with_thoughts_off_even_for_nonzero_cursors() -> None:
    """Thoughts OFF ⇒ diary lines only, in write order — cursors notwithstanding.

    The tech spec's OFF/ON cell. Written with cursors 5 and 11 precisely
    BECAUSE ``k == 0`` is not the invariant: these are the numbers a real entry
    carries when it was written while spec 028's flag was on. The rendered block
    must be the same as if they had been zero.
    """
    with_real_cursors = _private_record_block(
        ["hidden-1", "hidden-2"],
        [_rec(1, 5, "E1"), _rec(2, 11, "E2")],
        thoughts_enabled=False,
        diaries_enabled=True,
    )
    with_zero_cursors = _private_record_block(
        ["hidden-1", "hidden-2"],
        [_rec(1, 0, "E1"), _rec(2, 0, "E2")],
        thoughts_enabled=False,
        diaries_enabled=True,
    )

    assert with_real_cursors == with_zero_cursors
    assert with_real_cursors == _block(
        _diary_line(1, "E1"), _diary_line(2, "E2")
    )
    # The suppressed notes never leak into the block through the cursor path.
    assert "hidden-1" not in with_real_cursors
    assert "hidden-2" not in with_real_cursors


@pytest.mark.parametrize(
    ("label", "record"),
    [
        ("no cursor key at all", {"day": 2, "text": "E"}),
        ("cursor is a string", {"day": 2, "thoughts_before": "3", "text": "E"}),
        ("cursor is None", {"day": 2, "thoughts_before": None, "text": "E"}),
        ("cursor is a bool", {"day": 2, "thoughts_before": True, "text": "E"}),
        ("cursor is a float", {"day": 2, "thoughts_before": 1.9, "text": "E"}),
        ("no day key", {"thoughts_before": 1, "text": "E"}),
        ("no text key", {"day": 2, "thoughts_before": 1}),
        ("empty record", {}),
    ],
)
def test_a_malformed_record_never_raises_inside_a_prompt_build(
    label: str, record: dict
) -> None:
    """``DiaryRecord`` is ``total=False``: every field is read through ``.get``.

    A raise here would take down a Day-speech, a vote or a Night pick — the
    defensive house style the transcript renderer already follows. The entry is
    still shown (an entry with a nonsensical ``day`` is still worth reading),
    and the thoughts are still all there exactly once.
    """
    block = _private_record_block(
        ["t0", "t1"],
        [record],  # type: ignore[list-item]
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert _thought_bullets(block) == ["- t0", "- t1"], label
    assert len(_diary_bullets(block)) == 1, label


# ==========================================================================
# Slice 2.4 — THE WINDOW: three most recent, and the transcript keeps the rest
# ==========================================================================


def test_diary_window_is_three() -> None:
    """BARE-LITERAL pin. ``DIARY_WINDOW == 3``, the functional spec's number.

    Deliberately an absolute literal and not ``len(something)``: every other
    assertion in this section reads the constant, so all of them stay true if
    the constant is mutated. This one is the anchor that does not.
    """
    assert DIARY_WINDOW == 3


@pytest.mark.parametrize("count", [1, 2, 3])
def test_three_or_fewer_entries_are_all_shown(count: int) -> None:
    """At or under the window, every entry a player has written is in the block."""
    diaries = [_rec(day, 0, f"ENTRY-{day}") for day in range(1, count + 1)]

    block = _private_record_block(
        [], diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert len(_diary_bullets(block)) == count
    for day in range(1, count + 1):
        assert f"ENTRY-{day}" in block


def test_a_fourth_entry_drops_the_oldest_from_the_block() -> None:
    """Four written ⇒ the three most recent are shown and the oldest is not."""
    diaries = [_rec(day, 0, f"ENTRY-{day}") for day in range(1, 5)]

    block = _private_record_block(
        [], diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert "ENTRY-1" not in block
    assert _diary_bullets(block) == [
        _diary_line(2, "ENTRY-2"),
        _diary_line(3, "ENTRY-3"),
        _diary_line(4, "ENTRY-4"),
    ]


def test_the_window_keeps_the_newest_not_the_oldest() -> None:
    """Six written ⇒ Days 4, 5, 6 — not 1, 2, 3.

    ``diaries[:DIARY_WINDOW]`` instead of ``diaries[-DIARY_WINDOW:]`` is a
    one-character slip that still shows exactly three entries, still interleaves
    correctly against their own cursors, and still passes any test that only
    counts lines. It freezes a player on its oldest reads for the rest of the
    game.
    """
    diaries = [_rec(day, 0, f"ENTRY-{day}") for day in range(1, 7)]

    block = _private_record_block(
        [], diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert [line.split("] ", 1)[1] for line in _diary_bullets(block)] == [
        "ENTRY-4",
        "ENTRY-5",
        "ENTRY-6",
    ]


def test_windowing_out_the_oldest_does_not_disturb_the_remaining_cursors() -> None:
    """The windowed 4-entry block is byte-identical to the surviving 3 alone.

    Tech-spec §2.5: the window is applied BEFORE the merge, and that ordering is
    correct rather than incidental — thoughts are indexed independently, so
    dropping a diary removes only its own line and the survivors' cursors still
    partition the thought list exactly as they did.

    Merging first and then deleting the oldest diary LINE would leave the
    thought runs partitioned by a cursor that is no longer represented; this
    equality is what tells the two implementations apart.
    """
    thoughts = [f"t{i}" for i in range(9)]
    all_four = [
        _rec(1, 1, "E1"),
        _rec(2, 3, "E2"),
        _rec(3, 6, "E3"),
        _rec(4, 8, "E4"),
    ]

    windowed = _private_record_block(
        thoughts, all_four, thoughts_enabled=True, diaries_enabled=True
    )
    survivors_only = _private_record_block(
        thoughts, all_four[1:], thoughts_enabled=True, diaries_enabled=True
    )

    assert windowed == survivors_only
    assert windowed == _block(
        "- t0",
        "- t1",
        "- t2",
        _diary_line(2, "E2"),
        "- t3",
        "- t4",
        "- t5",
        _diary_line(3, "E3"),
        "- t6",
        "- t7",
        _diary_line(4, "E4"),
        "- t8",
    )


def test_no_thought_is_lost_when_a_diary_is_windowed_out() -> None:
    """Dropping the oldest diary MERGES the two thought runs it separated.

    Worth stating explicitly because the result looks like a defect: ``t0``,
    ``t1`` and ``t2`` were split by ``E1``'s cursor while ``E1`` was in the
    window, and after it drops out they form one contiguous run. Nothing is
    lost and the order is preserved — a contiguous run must not be read as a
    missing thought, and a "fix" that reinserted a gap would be the actual bug.
    """
    thoughts = [f"t{i}" for i in range(9)]
    all_four = [
        _rec(1, 1, "E1"),
        _rec(2, 3, "E2"),
        _rec(3, 6, "E3"),
        _rec(4, 8, "E4"),
    ]

    inside_window = _private_record_block(
        thoughts, all_four[:3], thoughts_enabled=True, diaries_enabled=True
    )
    windowed_out = _private_record_block(
        thoughts, all_four, thoughts_enabled=True, diaries_enabled=True
    )

    # E1 split t0 from t1/t2 while it was shown...
    assert _diary_line(1, "E1") in inside_window
    assert inside_window.index("- t0") < inside_window.index(_diary_line(1, "E1"))
    assert inside_window.index(_diary_line(1, "E1")) < inside_window.index("- t1")

    # ...and after it drops out the two runs are contiguous, with NOTHING lost.
    assert _diary_line(1, "E1") not in windowed_out
    assert _thought_bullets(windowed_out) == [f"- {note}" for note in thoughts]
    head = windowed_out.split(_diary_line(2, "E2"), 1)[0]
    assert _bullets(head) == ["- t0", "- t1", "- t2"], (
        "the run the dropped entry used to split must be contiguous, in order, "
        f"and complete; got {_bullets(head)!r}"
    )


def _transcript_players() -> dict[str, PlayerState]:
    return {"p-ava": _player("p-ava", "Ava", "law_abiding")}


def _four_day_game_events() -> list[dict[str, Any]]:
    """Four Days, each closing with its own ``day_diary`` delta."""
    events: list[dict[str, Any]] = []
    for day in range(1, 5):
        events.append(
            {"day_open": {"messages": [SystemMessage(content=f"Day {day} breaks.")]}}
        )
        events.append(
            {
                "day_turn": {
                    "messages": [AIMessage(content="A remark.", name="Ava")],
                    "day_rounds": 1,
                }
            }
        )
        events.append(
            {"day_close": {"messages": [SystemMessage(content="The Day ends.")]}}
        )
        events.append(
            {
                "day_diary": {
                    "private_diaries": {
                        "p-ava": [_rec(day, 0, f"ENTRY-{day}")],
                    }
                }
            }
        )
    return events


def test_the_dropped_entry_is_gone_from_the_block_but_kept_in_the_transcript() -> None:
    """The functional spec's exact promise, both halves pinned in one test.

    "Every entry ever written is still kept in the record of the game —
    dropping out affects only what the player is reasoning from, not what is
    preserved." Applying the window at WRITE time would satisfy the block half
    and quietly destroy the preservation half, and nothing that looks only at
    prompts would notice.
    """
    accumulated = [_rec(day, 0, f"ENTRY-{day}") for day in range(1, 5)]

    block = _private_record_block(
        [], accumulated, thoughts_enabled=True, diaries_enabled=True
    )
    transcript = render_transcript(
        _four_day_game_events(), _transcript_players(), game_index=1, run_meta=None
    )

    # Dropped from what the player reasons from...
    assert "ENTRY-1" not in block
    # ...and still, in full, in the preserved record.
    assert transcript.count("<diary ") == 4
    for day in range(1, 5):
        assert f'<diary player="Ava" day="{day}">ENTRY-{day}</diary>' in transcript


# ==========================================================================
# Slice 2.5 — THE FLAG CROSS-PRODUCT (tech-spec §2.4's table, one test per cell)
# ==========================================================================
#
# ``GRAPHIA_PRIVATE_THOUGHTS`` × ``GRAPHIA_PRIVATE_DIARIES`` over ONE shared
# prompt slot is genuinely new contract surface. Five cells; the first and third
# are the byte-identity-by-delegation cases, where ``_private_record_block``
# does not reproduce spec 028's rendering, it CALLS it.

_CELL_THOUGHTS = ["a", "b"]
_CELL_DIARIES = [_rec(1, 1, "E1")]

# The exact bytes spec 028 renders for ``_CELL_THOUGHTS``, written out as a
# literal rather than derived. Both delegation cells below assert equality with
# ``_private_thoughts_block(...)`` — which is the contract — AND with this
# literal, which is what keeps the contract from being vacuously self-agreeing
# if the 028 renderer itself is mutated.
_CELL_028_BYTES = (
    "\nYour private notes so far (yours alone):\n- a\n- b\n"
)


def test_cell_thoughts_on_diaries_off_is_028s_block_byte_for_byte() -> None:
    """Cell 1 (ON / OFF): byte-identical to spec 028, BY DELEGATION.

    Entries exist and are handed in; the flag alone suppresses them. This is
    ADR 011's ablation promise for the diary feature: switching it off restores
    the prompt bytes the previous spec produced.
    """
    block = _private_record_block(
        _CELL_THOUGHTS,
        _CELL_DIARIES,
        thoughts_enabled=True,
        diaries_enabled=False,
    )

    assert block == _private_thoughts_block(_CELL_THOUGHTS, enabled=True)
    assert block == _CELL_028_BYTES
    assert DIARY_LINE_MARKER not in block
    assert "E1" not in block


def test_cell_thoughts_off_diaries_off_is_the_empty_slot() -> None:
    """Cell 2 (OFF / OFF): ``""`` — byte-identical to the PRE-028 prompt.

    The only combination that reproduces pre-028 bytes from spec 039 onward.
    ``""`` and not a neutral line: the slot must collapse entirely, leaving no
    label, no body and no stray blank line.
    """
    block = _private_record_block(
        _CELL_THOUGHTS,
        _CELL_DIARIES,
        thoughts_enabled=False,
        diaries_enabled=False,
    )

    assert block == ""


@pytest.mark.parametrize(
    ("label", "diaries"),
    [("empty list", []), ("None", None)],
)
def test_cell_thoughts_on_diaries_on_but_none_written_is_028s_block(
    label: str, diaries: list[DiaryRecord] | None
) -> None:
    """Cell 3 (ON / ON, no entries yet): 028's block, BY DELEGATION.

    This is all of Day 1 in every game — the change is invisible until the first
    entry exists. Both spellings of "nothing written" are swept because the four
    call sites default the parameter to ``None`` and pass ``[]`` when the
    channel has no key for the actor.
    """
    block = _private_record_block(
        _CELL_THOUGHTS,
        diaries,
        thoughts_enabled=True,
        diaries_enabled=True,
    )

    assert block == _private_thoughts_block(_CELL_THOUGHTS, enabled=True)
    assert block == _CELL_028_BYTES, label
    assert DIARY_LINE_MARKER not in block


def test_cell_thoughts_on_diaries_on_with_entries_is_merged_and_windowed() -> None:
    """Cell 4 (ON / ON, entries exist): merged, interleaved, tagged, windowed.

    All four properties of the cell asserted in one rendering: the oldest of
    four entries is gone (windowed), the survivors sit at their own cursors
    (merged and interleaved) and only they carry the tag.
    """
    thoughts = ["a", "b", "c"]
    diaries = [
        _rec(1, 0, "OLDEST"),
        _rec(2, 1, "E2"),
        _rec(3, 2, "E3"),
        _rec(4, 3, "E4"),
    ]

    block = _private_record_block(
        thoughts, diaries, thoughts_enabled=True, diaries_enabled=True
    )

    assert block == _block(
        "- a",
        _diary_line(2, "E2"),
        "- b",
        _diary_line(3, "E3"),
        "- c",
        _diary_line(4, "E4"),
    )
    assert "OLDEST" not in block


def test_cell_thoughts_off_diaries_on_is_heading_plus_tagged_lines_only() -> None:
    """Cell 5 (OFF / ON): the heading and the tagged diary lines, nothing else.

    Note what is NOT asserted here: that the cursors are zero. Tech-spec §2.4
    records ``k == 0`` as a coincidence of a whole-run flag, so the entries below
    carry ``k = 2`` and ``k = 3`` — the values a real entry written under
    thoughts-ON keeps for ever. What the cell requires is that the notes are
    suppressed and the entries still render in order.
    """
    block = _private_record_block(
        ["suppressed-1", "suppressed-2", "suppressed-3"],
        [_rec(1, 2, "E1"), _rec(2, 3, "E2")],
        thoughts_enabled=False,
        diaries_enabled=True,
    )

    assert block == _block(_diary_line(1, "E1"), _diary_line(2, "E2"))
    assert "suppressed-1" not in block
    assert _thought_bullets(block) == []


def test_composed_contract_pre_028_bytes_need_both_flags_off() -> None:
    """THE COMPOSED CONTRACT, recorded: two features now share one slot.

    Spec 028's own flag-off parity tests still pass unchanged and are not
    weakened — they assert the label and the note text are absent from prompts
    built with ``private_thoughts_enabled=False``, and they call the helpers
    directly where ``diaries`` defaults to ``[]``, so they remain exactly true.

    But from spec 039 on, the standing claim "``GRAPHIA_PRIVATE_THOUGHTS=0``
    reproduces the pre-028 prompt" is only true when ``GRAPHIA_PRIVATE_DIARIES``
    is off as well. This test is that sentence in executable form, so nobody
    reconstructs the old claim from the 028 file alone.
    """
    thoughts = ["a", "b"]
    diaries = [_rec(1, 2, "E1")]

    thoughts_off_only = _private_record_block(
        thoughts, diaries, thoughts_enabled=False, diaries_enabled=True
    )
    both_off = _private_record_block(
        thoughts, diaries, thoughts_enabled=False, diaries_enabled=False
    )

    assert thoughts_off_only != ""
    assert PRIVATE_THOUGHTS_LABEL in thoughts_off_only
    assert both_off == ""


def test_the_flag_parameters_are_keyword_only_and_undefaulted() -> None:
    """The builder never guesses a flag (tech-spec §2.11).

    ``thoughts_enabled`` / ``diaries_enabled`` are keyword-only with NO
    defaults, deliberately: the helpers above them keep ``diaries_enabled=True``
    for the 019/024/025/028 direct-call convention, and the node's own
    ``private_diaries_enabled`` is the real guard. Giving the builder a default
    would put a silent guess at the bottom of the stack, where every call site
    inherits it.
    """
    sig = inspect.signature(_private_record_block)

    for name in ("thoughts_enabled", "diaries_enabled"):
        param = sig.parameters[name]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert param.default is inspect.Parameter.empty, name


def test_the_marker_is_an_invariant_prefix_not_a_template() -> None:
    """ANTI-VACUITY. A ``"…{day}]"`` constant would void every flag-off check.

    Every ``DIARY_LINE_MARKER not in prompt`` assertion in this spec — and there
    are many — becomes vacuously true the moment the constant contains a format
    placeholder, because a literal ``{day}`` never appears in a rendered prompt.
    So: no braces, and the same constant is asserted PRESENT in a flag-on
    rendering, which is what makes the absence checks mean something.
    """
    assert "{" not in DIARY_LINE_MARKER
    assert "}" not in DIARY_LINE_MARKER
    assert DIARY_LINE_MARKER == "[Diary — end of Day"

    flag_on = _private_record_block(
        [], [_rec(2, 0, "E")], thoughts_enabled=True, diaries_enabled=True
    )
    assert DIARY_LINE_MARKER in flag_on


# ==========================================================================
# Slice 2.6 — PRIVACY at all four call sites (the highest-stakes invariant)
# ==========================================================================
#
# ``test_dual_mode_smoke`` does NOT cover this: it scripts no ``diaries=``, so a
# mutation that handed every player the whole channel would leave it green.
# These are the only assertions that catch it, and each drives the REAL node so
# the KEYING (``…get(actor.id, [])`` rather than the whole map) is what is under
# test, not just the builder.

AVA_DIARY = "AVA-DIARY-039: the miller would not meet my eye."
BEN_DIARY = "BEN-DIARY-039: I still think the baker is lying."
MARA_DIARY = "MARA-DIARY-039: the smith is the safest kill tonight."


def _leaky_table() -> dict[str, PlayerState]:
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-ben": _player("p-ben", "Ben", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
        "p-human": _player("p-human", "Hugo", "law_abiding", is_human=True),
    }


def _leaky_diaries() -> dict[str, list[DiaryRecord]]:
    return {
        "p-ava": [_rec(1, 0, AVA_DIARY)],
        "p-ben": [_rec(1, 0, BEN_DIARY)],
        "p-mara": [_rec(1, 0, MARA_DIARY)],
    }


def _day_state() -> GameState:
    return {
        "cycle": 2,
        "phase": "day",
        "players": _leaky_table(),
        "day_order": ["p-ava", "p-ben", "p-mara"],
        "day_turn_index": 0,
        "day_rounds": 1,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": _leaky_diaries(),
    }


def test_day_turn_gives_the_speaker_only_its_own_diary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live Day-speech call site keys on the ACTING player's id."""
    state = _day_state()
    fake = _CapturingFake(DayAction(kind="speak", text="Ava speaks."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_turn(state)

    prompt = _human_prompt(fake.messages_log[0])
    assert AVA_DIARY in prompt
    assert BEN_DIARY not in prompt
    assert MARA_DIARY not in prompt


def test_collect_votes_gives_the_voter_only_its_own_diary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live vote call site keys on the VOTER's id."""
    state = _day_state()
    state["active_vote"] = {  # type: ignore[typeddict-unknown-key]
        "initiator": "p-mara",
        "target": "p-ben",
        "ballots": {},
        "pending": ["p-ava"],
    }
    fake = _CapturingFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    collect_votes(state)

    prompt = _human_prompt(fake.messages_log[0])
    assert AVA_DIARY in prompt
    assert BEN_DIARY not in prompt
    assert MARA_DIARY not in prompt


def test_mafia_point_gives_the_pointer_only_its_own_diary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live Night-pointing call site keys on the POINTER's id."""
    state = _day_state()
    state["phase"] = "night"  # type: ignore[typeddict-item]
    state["night_mafia_order"] = ["p-mara"]  # type: ignore[typeddict-unknown-key]
    state["night_pointer_index"] = 0  # type: ignore[typeddict-unknown-key]
    state["night_law_order"] = ["p-ava", "p-ben"]  # type: ignore[typeddict-unknown-key]
    state["night_round"] = 1  # type: ignore[typeddict-unknown-key]
    state["night_round_picks"] = {}  # type: ignore[typeddict-unknown-key]
    state["night_rounds_log"] = []  # type: ignore[typeddict-unknown-key]
    fake = _CapturingFake(Pointing(target_id="p-ava"))
    monkeypatch.setattr(night_nodes, "get_large", lambda: fake)

    mafia_point(state)

    prompt = _human_prompt(fake.messages_log[0])
    assert MARA_DIARY in prompt
    assert AVA_DIARY not in prompt
    assert BEN_DIARY not in prompt


def test_day_diary_gives_the_writer_only_its_own_prior_diaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FOURTH call site — the diary prompt itself — keys the same way.

    Tech-spec §2.4's most easily missed site: a player writing Day 3's entry
    must have read its own Day 1 and Day 2 entries, and nobody else's.
    """
    state = _day_state()
    fake = _CapturingFake(Diary(entry="A new entry."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_diary(state)

    ava_prompt = _human_prompt(fake.messages_log[0])
    assert AVA_DIARY in ava_prompt
    assert BEN_DIARY not in ava_prompt
    assert MARA_DIARY not in ava_prompt

    ben_prompt = _human_prompt(fake.messages_log[1])
    assert BEN_DIARY in ben_prompt
    assert AVA_DIARY not in ben_prompt


def test_a_writer_never_sees_its_own_not_yet_committed_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``day_diary`` returns ONE delta after the loop, so nothing is self-visible.

    The prompt shows Day 1's entry (committed state) and never the Day-2 text
    the same fan-out is in the middle of producing.
    """
    state = _day_state()
    fake = _CapturingFake(Diary(entry="TODAYS-BRAND-NEW-ENTRY"))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(state)

    assert delta["private_diaries"]["p-ava"][0]["text"] == "TODAYS-BRAND-NEW-ENTRY"
    for messages in fake.messages_log:
        assert "TODAYS-BRAND-NEW-ENTRY" not in _human_prompt(messages)


def test_no_diary_reaches_the_public_message_stream_from_any_call_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural privacy: the merged block is prompt-only, never a message.

    ``day_turn`` DOES emit public ``messages`` (the speech), so this is the node
    where a "helpfully" surfaced private record would actually reach the UI and
    every other player's rendered context.
    """
    state = _day_state()
    fake = _CapturingFake(DayAction(kind="speak", text="Ava speaks."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_turn(state)

    for message in delta.get("messages", []):
        content = str(getattr(message, "content", ""))
        assert AVA_DIARY not in content
        assert DIARY_LINE_MARKER not in content


# ==========================================================================
# Slice 2.7 — the flag reaches each live call site (ADR 011 ablation)
# ==========================================================================


def test_day_turn_flag_off_removes_the_diary_from_the_speech_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``private_diaries_enabled=False`` at the node suppresses the diary lines.

    Spec 028's block is untouched by this flag — a note is present and stays
    present — which is what makes the two features independently ablatable
    despite sharing one slot.
    """
    state = _day_state()
    state["private_thoughts"] = {"p-ava": ["ava-thought"]}
    fake = _CapturingFake(DayAction(kind="speak", text="Ava speaks."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_turn(state, private_diaries_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert DIARY_LINE_MARKER not in prompt
    assert AVA_DIARY not in prompt
    assert "ava-thought" in prompt


def test_collect_votes_flag_off_removes_the_diary_from_the_vote_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same ablation on the vote path."""
    state = _day_state()
    state["private_thoughts"] = {"p-ava": ["ava-thought"]}
    state["active_vote"] = {  # type: ignore[typeddict-unknown-key]
        "initiator": "p-mara",
        "target": "p-ben",
        "ballots": {},
        "pending": ["p-ava"],
    }
    fake = _CapturingFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    collect_votes(state, private_diaries_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert DIARY_LINE_MARKER not in prompt
    assert AVA_DIARY not in prompt
    assert "ava-thought" in prompt


def test_mafia_point_flag_off_removes_the_diary_from_the_pointing_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same ablation on the Night-pointing path."""
    state = _day_state()
    state["private_thoughts"] = {"p-mara": ["mara-thought"]}
    state["night_mafia_order"] = ["p-mara"]  # type: ignore[typeddict-unknown-key]
    state["night_pointer_index"] = 0  # type: ignore[typeddict-unknown-key]
    state["night_law_order"] = ["p-ava", "p-ben"]  # type: ignore[typeddict-unknown-key]
    state["night_round"] = 1  # type: ignore[typeddict-unknown-key]
    state["night_round_picks"] = {}  # type: ignore[typeddict-unknown-key]
    state["night_rounds_log"] = []  # type: ignore[typeddict-unknown-key]
    fake = _CapturingFake(Pointing(target_id="p-ava"))
    monkeypatch.setattr(night_nodes, "get_large", lambda: fake)

    mafia_point(state, private_diaries_enabled=False)

    prompt = _human_prompt(fake.messages_log[0])
    assert DIARY_LINE_MARKER not in prompt
    assert MARA_DIARY not in prompt
    assert "mara-thought" in prompt


@pytest.mark.parametrize(
    "helper", [_ai_day_action, _ai_ballot, _ai_pick_target, day_nodes._ai_diary]
)
def test_every_helper_defaults_diaries_off_free_for_direct_test_calls(
    helper: Any,
) -> None:
    """All four helpers keep the 019/024/025/028 direct-call convention.

    ``diaries=None`` / ``diaries_enabled=True`` so a pre-039 direct call renders
    byte-identically (no entries handed in ⇒ delegation to 028's block). The
    node-level ``private_diaries_enabled`` is the real ablation guard; these
    defaults are a test convenience, and pinning them keeps a future signature
    change from silently invalidating dozens of existing direct calls.
    """
    sig = inspect.signature(helper)

    assert sig.parameters["diaries"].default is None
    assert sig.parameters["diaries_enabled"].default is True


# ==========================================================================
# Slice 2.8 — ``_ai_reflect`` is deliberately NOT the fifth call site
# ==========================================================================


def test_ai_reflect_takes_no_diaries_parameter() -> None:
    """Spec 028's reflection keeps its own place in the player's reasoning.

    Functional spec §3 Out-of-Scope forbids changing the Day-round reflections,
    so ``_private_thoughts_block`` keeps this one remaining caller and the
    signature carries no diary seam at all. Pinned structurally, because "we
    just didn't pass it" is one keyword argument away from being wrong.
    """
    sig = inspect.signature(day_nodes._ai_reflect)

    assert "diaries" not in sig.parameters
    assert "diaries_enabled" not in sig.parameters


def test_the_reflection_prompt_carries_no_diary_even_when_entries_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driven through the real ``day_round_reflect`` node with diaries in state.

    The consequence of the signature above, asserted where it is observable: the
    reflection prompt sees the reflector's own THOUGHTS and nothing from the
    diary channel, even though the channel is populated for that same player.
    """
    state = _day_state()
    state["private_thoughts"] = {"p-ava": ["ava-thought"]}
    fake = _CapturingFake(Reflection(thought="A passing thought."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_round_reflect(state)

    assert fake.messages_log, "day_round_reflect made no model call"
    for messages in fake.messages_log:
        prompt = _human_prompt(messages)
        assert DIARY_LINE_MARKER not in prompt
        assert AVA_DIARY not in prompt
        assert BEN_DIARY not in prompt
        assert MARA_DIARY not in prompt
    assert "ava-thought" in _human_prompt(fake.messages_log[0])
