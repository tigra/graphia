"""Spec 040 (The Moderator's Closing Story) — Task 3.2: the third-party renderer.

Task 3.1 extracted the ``thoughts_before`` cursor walk out of
``nodes/day.py:_private_record_block`` into ``nodes/day.py:_walk_private_record``
so that TWO renderers could share it. This file covers the second one:
``nodes/endgame.py:_story_private_block``, which turns the walk's ordered
``(kind, day, text)`` tuples into text a THIRD PARTY — the Moderator, writing a
closing story — can read, as against the second-person block a player reads.

WHAT THIS FILE IS WEIGHTED AGAINST: MISATTRIBUTION
---------------------------------------------------

The silent failure mode here is not absence and not a crash. It is a line that
arrives under the wrong label:

- **a diary rendered as a Day round**, or a reflection rendered as a whole-Day
  diary — which is precisely what a *copied* ``"diary"`` literal in
  ``endgame.py`` would eventually produce, with nothing raising. Spec 039
  (Per-AI Private Diaries)'s "import the thing, never copy it" rule is what
  the two kind constants exist for, and §2 below is what makes the rule
  checkable rather than merely stated.
- **one author's material appearing under another's name** — a well-formed
  block, the right entries, the right count, attributed to the wrong player.
  The Moderator then tells the table that the Citizen who was executed
  suspected nobody, in the words of the Mafioso who framed them.

Neither raises, neither trips a schema, neither shows up in a metric. So the
assertions below are positive AND negative per author and per kind: it is not
enough that the right text is present somewhere.

The seven sections
------------------

1. **Per-author grouping and attribution** — each author's entries under their
   own heading, and NOT under anyone else's.
2. **Kind attribution** — a diary carries the diary marker and its Day; a
   reflection carries the reflection marker and NO diary marker.
3. **Order within an author** is the shared walk's, asserted as a whole ordered
   list (the two degenerate shapes a broken walk produces both hold exactly the
   right lines, so membership checks cannot tell them from the truth), and
   the walk is CONSUMED rather than re-implemented.
4. **The full diary, never windowed** — the §2.3 constraint that separates this
   reader from the player, plus the two forbidden reuses asserted as absences.
5. **The Day-attribution decision** — kind alone, no inference, and the
   ``DIARY_DAY_UNKNOWN`` normalisation, including the pin that what a PLAYER
   sees is deliberately unchanged.
6. **The degenerate shapes** — nothing written, blank entries, absent channels,
   and the human, whose absence is structural.
7. **Purity** — neither argument mutated; a live state list may be handed over.

Anti-vacuity discipline, inherited from spec 039's suite
--------------------------------------------------------

Every constant leaned on gets a BARE-LITERAL pin beside any relational check
(``DIARY_WINDOW == 3``, ``DIARY_DAY_UNKNOWN == "?"``, the marker texts), because
an expectation derived from the constant under test is true at any value.

These are all unit-level tests over pure functions: no driver, no
``App.run_test()``, no model, no game. Slice 3's preamble warns against copying
``tests/test_spec040_story_ui_order.py``'s live-dispatcher pattern (a
~30%-per-test flake in which the human is killed on Night 1, measured by Task
2.2) and points at ``dynamic_night_pointing`` installed before the name submit
as the race-free alternative. Nothing here drives a game, so that trap is out of
reach rather than avoided — the game-level material assertions are Task 3.6's.
"""

from __future__ import annotations

import pytest

import graphia.nodes.endgame as endgame
from graphia.nodes.day import (
    DIARY_DAY_UNKNOWN,
    DIARY_LINE_MARKER,
    DIARY_WINDOW,
    PRIVATE_RECORD_KIND_DIARY,
    PRIVATE_RECORD_KIND_THOUGHT,
    PRIVATE_THOUGHTS_LABEL,
    _private_record_block,
)
from graphia.nodes.endgame import (
    STORY_PRIVATE_AUTHOR_TEMPLATE,
    STORY_PRIVATE_DIARY_MARKER,
    STORY_PRIVATE_EMPTY,
    STORY_PRIVATE_LEGEND,
    STORY_PRIVATE_THOUGHT_MARKER,
    _story_private_block,
    _story_private_line,
)
from graphia.state import DiaryRecord, PlayerState

# ==========================================================================
# Shared helpers
# ==========================================================================


def _rec(day: int, cursor: int, text: str) -> DiaryRecord:
    """One ``DiaryRecord``. ``cursor`` is ``thoughts_before``.

    Deliberately the same shape and name as
    ``tests/test_spec040_private_record_walk.py:_rec`` and
    ``tests/test_slice39_diary_interleave.py:_rec`` — three files assert
    different properties of the same records, and a reader moving between them
    should not have to re-learn the fixture.
    """
    return {"day": day, "thoughts_before": cursor, "text": text}


def _p(
    pid: str,
    name: str,
    *,
    role: str = "law_abiding",
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


def _roster(*players: PlayerState) -> dict[str, PlayerState]:
    return {p.id: p for p in players}


def _heading(name: str) -> str:
    return STORY_PRIVATE_AUTHOR_TEMPLATE.format(name=name)


def _section(block: str, name: str) -> str:
    """The one author section of ``block`` belonging to ``name``.

    Sections are joined by a blank line, so an author's section runs from their
    heading to the next blank line. Fails loudly rather than returning ``""`` —
    a helper that silently yields nothing would make every negative assertion
    below vacuous.
    """
    heading = _heading(name)
    chunks = [c for c in block.split("\n\n") if c.startswith(heading)]
    assert len(chunks) == 1, f"expected exactly one {name!r} section in:\n{block}"
    return chunks[0]


# ==========================================================================
# 1 — PER-AUTHOR GROUPING: the misattribution the Moderator would repeat aloud
# ==========================================================================


def test_each_authors_material_appears_under_their_own_heading_only() -> None:
    """Two authors, two channels each; nothing crosses.

    THE CENTRAL MISATTRIBUTION PIN. Both directions are asserted per author,
    because "the right text is present somewhere in the block" is true of a
    renderer that flattens every player's entries under the first heading, of
    one that looks the channels up by the wrong key, and of the truth.
    """
    players = _roster(_p("p1", "Kira"), _p("p2", "Bram"))
    block = _story_private_block(
        players,
        {"p1": ["KIRA-THOUGHT"], "p2": ["BRAM-THOUGHT"]},
        {"p1": [_rec(1, 1, "KIRA-DIARY")], "p2": [_rec(1, 1, "BRAM-DIARY")]},
    )

    kira = _section(block, "Kira")
    bram = _section(block, "Bram")

    assert "KIRA-THOUGHT" in kira
    assert "KIRA-DIARY" in kira
    assert "BRAM-THOUGHT" not in kira
    assert "BRAM-DIARY" not in kira

    assert "BRAM-THOUGHT" in bram
    assert "BRAM-DIARY" in bram
    assert "KIRA-THOUGHT" not in bram
    assert "KIRA-DIARY" not in bram


def test_the_channels_are_keyed_by_player_id_not_by_display_name() -> None:
    """A name-keyed lookup finds nothing, and that must not pass silently.

    Both channels are keyed on ``player.id`` (``day_round_reflect`` and
    ``day_diary`` both write ``new[player.id]``), while the heading shows
    ``player.name``. The two are different strings, so a renderer that reached
    into the channels with the name would render an empty block for a game full
    of private writing — and ``STORY_PRIVATE_EMPTY`` reads like a legitimately
    short game rather than like a bug.
    """
    players = _roster(_p("p1", "Kira"))
    by_id = _story_private_block(players, {"p1": ["FOUND-BY-ID"]}, None)
    by_name = _story_private_block(players, {"Kira": ["FOUND-BY-NAME"]}, None)

    assert "FOUND-BY-ID" in by_id
    assert by_name == STORY_PRIVATE_EMPTY


def test_authors_appear_in_roster_order() -> None:
    """``players`` insertion order, the same order ``_story_roster_block`` uses.

    Deterministic by construction — ``dict.values()``, never a ``set`` — so the
    dual-mode byte-equal smoke is unaffected. Asserted on the reverse of the
    alphabetical order too, so a renderer that sorted by name would fail one of
    the two calls.
    """
    forward = _story_private_block(
        _roster(_p("p1", "Ada"), _p("p2", "Zoe")),
        {"p1": ["A"], "p2": ["Z"]},
        None,
    )
    reverse = _story_private_block(
        _roster(_p("p2", "Zoe"), _p("p1", "Ada")),
        {"p1": ["A"], "p2": ["Z"]},
        None,
    )
    assert forward.index(_heading("Ada")) < forward.index(_heading("Zoe"))
    assert reverse.index(_heading("Zoe")) < reverse.index(_heading("Ada"))


def test_a_dead_player_is_an_author_like_any_other() -> None:
    """NOTHING FILTERS BY SURVIVAL — a dead player's writing is the point.

    What the Moderator holds and the living never saw. Spec 040 Task 3.6 owns
    the game-level version of this; here it is the renderer contract.
    """
    players = _roster(_p("p1", "Kira", is_alive=False))
    block = _story_private_block(players, {"p1": ["SPOKEN-FROM-THE-GRAVE"]}, None)
    assert "SPOKEN-FROM-THE-GRAVE" in _section(block, "Kira")


# ==========================================================================
# 2 — KIND ATTRIBUTION: a whole-Day diary must never read as a Day round
# ==========================================================================


def test_a_diary_line_carries_the_diary_marker_and_its_day() -> None:
    """BARE-LITERAL pin on the marker beside the rendered line.

    The marker is an invariant PREFIX rather than a ``"…{day}]"`` template, on
    ``DIARY_LINE_MARKER``'s recorded lesson: a template constant would make the
    negative assertions in this section vacuously true, because a literal
    ``{day}`` never appears in a rendered prompt.
    """
    assert STORY_PRIVATE_DIARY_MARKER == "[diary of Day"

    line = _story_private_line((PRIVATE_RECORD_KIND_DIARY, 2, "THE ENTRY"))
    assert line == "  • [diary of Day 2] THE ENTRY"
    assert STORY_PRIVATE_THOUGHT_MARKER not in line


def test_a_reflection_line_carries_the_reflection_marker_and_no_diary_marker() -> None:
    """BARE-LITERAL pin, and the negative that catches the copied literal.

    A reflection tagged as a whole-Day diary is the misattribution a drifted
    ``"diary"`` literal in this module produces, and it is well-formed output.
    """
    assert STORY_PRIVATE_THOUGHT_MARKER == "[reflection]"

    line = _story_private_line((PRIVATE_RECORD_KIND_THOUGHT, None, "THE THOUGHT"))
    assert line == "  • [reflection] THE THOUGHT"
    assert STORY_PRIVATE_DIARY_MARKER not in line


def test_the_two_kinds_are_tagged_differently_in_one_block() -> None:
    """Both markers present, each on exactly its own line — the end-to-end pin.

    Section 2's two unit tests can both pass while the block wires the wrong
    branch to the wrong entry, so the same claim is made once over rendered
    output.
    """
    players = _roster(_p("p1", "Kira"))
    block = _story_private_block(
        players, {"p1": ["A THOUGHT"]}, {"p1": [_rec(1, 1, "A DIARY")]}
    )
    diary_lines = [ln for ln in block.splitlines() if "A DIARY" in ln]
    thought_lines = [ln for ln in block.splitlines() if "A THOUGHT" in ln]
    assert len(diary_lines) == 1 and len(thought_lines) == 1
    assert STORY_PRIVATE_DIARY_MARKER in diary_lines[0]
    assert STORY_PRIVATE_THOUGHT_MARKER not in diary_lines[0]
    assert STORY_PRIVATE_THOUGHT_MARKER in thought_lines[0]
    assert STORY_PRIVATE_DIARY_MARKER not in thought_lines[0]


def test_an_unrecognised_kind_is_neither_a_diary_nor_a_reflection() -> None:
    """THE THREE-WAY BRANCH IS THE GUARD, not defensive noise.

    An ``if diary … else thought`` two-way branch is exactly how a third
    channel added to the walk one day becomes silently attributed as a
    reflection. An unknown kind renders under its own name instead, so it
    arrives in the prompt visibly unhandled rather than quietly wrong.
    """
    line = _story_private_line(("whisper", None, "SOMETHING NEW"))
    assert "SOMETHING NEW" in line
    assert STORY_PRIVATE_DIARY_MARKER not in line
    assert STORY_PRIVATE_THOUGHT_MARKER not in line
    assert "[whisper]" in line


# ==========================================================================
# 3 — ORDER: the shared walk's, and consumed rather than re-implemented
# ==========================================================================


def test_one_authors_lines_are_in_the_walks_order() -> None:
    """The WHOLE ordered list of lines, not membership.

    The two degenerate shapes a broken merge produces — all reflections then
    all diaries, or all diaries then all reflections — hold exactly the right
    lines, in the right number, under the right heading. Only an ordered
    comparison separates them from the truth.

    ``thoughts_before`` cursors of 1 and 3 over four thoughts put diary Day 1
    after the first thought and diary Day 2 after the third.
    """
    players = _roster(_p("p1", "Kira"))
    block = _story_private_block(
        players,
        {"p1": ["t0", "t1", "t2", "t3"]},
        {"p1": [_rec(1, 1, "d1"), _rec(2, 3, "d2")]},
    )
    assert _section(block, "Kira").splitlines() == [
        _heading("Kira"),
        "  • [reflection] t0",
        "  • [diary of Day 1] d1",
        "  • [reflection] t1",
        "  • [reflection] t2",
        "  • [diary of Day 2] d2",
        "  • [reflection] t3",
    ]


def test_the_block_consumes_the_shared_walk() -> None:
    """A sentinel walk reaches the rendered block, so the walk is not re-done.

    The regression this guards is a renderer that re-implements the merge — the
    thing §2.3 chose extraction to prevent, whose failure mode is well-formed
    output in the wrong order and nothing raising. Patched on THIS module's
    binding, because ``endgame`` imports the name into its own namespace.
    """
    sentinels = [
        (PRIVATE_RECORD_KIND_DIARY, 9, "SENTINEL-DIARY"),
        (PRIVATE_RECORD_KIND_THOUGHT, None, "SENTINEL-THOUGHT"),
    ]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(endgame, "_walk_private_record", lambda *_a, **_k: sentinels)
        block = _story_private_block(_roster(_p("p1", "Kira")), {"p1": ["x"]}, None)
    assert block.splitlines()[-2:] == [
        "  • [diary of Day 9] SENTINEL-DIARY",
        "  • [reflection] SENTINEL-THOUGHT",
    ]


# ==========================================================================
# 4 — THE FULL DIARY, AND THE TWO FORBIDDEN REUSES
# ==========================================================================


def test_the_whole_diary_is_rendered_with_no_window() -> None:
    """Every entry ever written, which is what separates this reader from a player.

    BARE-LITERAL pin ``DIARY_WINDOW == 3`` beside the relational check, and six
    entries rather than four so a window of six could not pass either.
    """
    assert DIARY_WINDOW == 3

    diaries = [_rec(day, 0, f"D{day}") for day in range(1, 7)]
    players = _roster(_p("p1", "Kira"))
    block = _story_private_block(players, {}, {"p1": diaries})

    rendered = [ln for ln in block.splitlines() if STORY_PRIVATE_DIARY_MARKER in ln]
    assert len(rendered) == 6 > DIARY_WINDOW
    for day in range(1, 7):
        assert f"  • [diary of Day {day}] D{day}" in rendered


def test_the_player_block_still_windows_the_same_diaries() -> None:
    """The contrast, asserted rather than assumed — two readers, two contracts.

    Spec 040 §2.3 forbids reusing ``DIARY_WINDOW`` here because it exists so a
    *player* reasons from three entries. This test is what makes the previous
    one a deliberate divergence: the same six records still reach a player as
    three.
    """
    diaries = [_rec(day, 0, f"D{day}") for day in range(1, 7)]
    player_block = _private_record_block(
        [], diaries, thoughts_enabled=True, diaries_enabled=True
    )
    assert player_block.count(DIARY_LINE_MARKER) == DIARY_WINDOW == 3


def test_the_block_does_not_delegate_to_the_players_renderer() -> None:
    """Neither by call nor by label — the second §2.3 forbidden reuse.

    ``_private_record_block`` windows AND renders under a second-person label
    addressed to the writer, so a third-party reader cannot borrow it. A
    delegating implementation would trip the raising patch; one that merely
    copied its label would trip the ``PRIVATE_THOUGHTS_LABEL`` assertion.
    """

    def _explode(*_a: object, **_k: object) -> str:
        raise AssertionError("_story_private_block must not call _private_record_block")

    players = _roster(_p("p1", "Kira"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("graphia.nodes.day._private_record_block", _explode)
        block = _story_private_block(
            players, {"p1": ["t"]}, {"p1": [_rec(1, 1, "d")]}
        )
    assert PRIVATE_THOUGHTS_LABEL == "Your private notes so far (yours alone):"
    assert PRIVATE_THOUGHTS_LABEL not in block
    assert "Your" not in block


def test_the_legend_states_the_per_author_limit_and_the_missing_day() -> None:
    """The legend is a fabrication guard, so its two load-bearing facts are pinned.

    Reason 1: no cross-player order exists to render (the cursor orders ONE
    player's two channels against each other; ``day_round_reflect`` reflects
    only survivors, so thought indices cannot substitute across players).
    Reason 2: a reflection carries no Day at all, said in words, because the
    model is about to read dated diaries beside undated reflections.
    """
    block = _story_private_block(_roster(_p("p1", "Kira")), {"p1": ["t"]}, None)
    assert block.startswith(STORY_PRIVATE_LEGEND)
    assert "not ordered against each other" in STORY_PRIVATE_LEGEND
    assert "does not record which Day" in STORY_PRIVATE_LEGEND


# ==========================================================================
# 5 — THE DAY-ATTRIBUTION DECISION: kind alone, and no inference
# ==========================================================================


def test_a_reflection_is_never_given_an_inferred_day() -> None:
    """THE RECORDED DECISION, asserted (Task 3.2).

    Task 3.2 offered two options: attribute a reflection by kind alone, or infer
    a Day from the flanking diary Days (before the first → Day 1; between
    ``day=d`` and ``day=d+1`` → ``d+1``; after the last → ``d+1``). Kind alone
    was chosen because an inferred Day is indistinguishable from a recorded one
    inside a prompt — the model reads both as ground truth — so a wrong
    inference comes back as a confidently misdated event, which is a
    fabrication rather than a loss of precision.

    Every limb of the rejected inference is exercised here: a leading
    reflection (would be Day 1), reflections between Day 1 and Day 3 (would be
    Day 2 or 3), and a trailing one (would be Day 4). None may carry a number.
    """
    players = _roster(_p("p1", "Kira"))
    block = _story_private_block(
        players,
        {"p1": ["LEADING", "MIDDLE", "TRAILING"]},
        {"p1": [_rec(1, 1, "d1"), _rec(3, 2, "d3")]},
    )
    for line in block.splitlines():
        if STORY_PRIVATE_THOUGHT_MARKER not in line:
            continue
        assert line.count("Day") == 0, line
        for day in (1, 2, 3, 4):
            assert f"Day {day}" not in line


def test_a_record_with_no_diary_at_all_dates_nothing() -> None:
    """The case where the rejected inference had NO FLANK, and misdated wholesale.

    Slice 3's preamble: a player who died during Day 1 has reflections and no
    diary. So does every player in a run with ``GRAPHIA_PRIVATE_DIARIES=0``
    (ADR 011) — ``day_diary`` returns ``{}`` before writing anything, so the
    channel is empty by construction while ``private_thoughts`` fills up as
    usual. The rejected "before the first diary → Day 1" limb would have dated a
    whole five-Day record to Day 1, produced by a config flag, with nothing
    raising.
    """
    players = _roster(_p("p1", "Kira"))
    block = _story_private_block(players, {"p1": ["a", "b", "c", "d", "e"]}, {})
    assert block.count(STORY_PRIVATE_THOUGHT_MARKER) == 5
    assert "Day" not in "\n".join(
        ln for ln in block.splitlines() if ln.startswith("  • ")
    )


@pytest.mark.parametrize(
    "record",
    [
        pytest.param({"thoughts_before": 0, "text": "E"}, id="day-key-missing"),
        pytest.param({"day": None, "thoughts_before": 0, "text": "E"}, id="day-none"),
        pytest.param({"day": True, "thoughts_before": 0, "text": "E"}, id="day-bool"),
        pytest.param({"day": [2], "thoughts_before": 0, "text": "E"}, id="day-list"),
    ],
)
def test_an_unusable_day_renders_as_the_placeholder(record: DiaryRecord) -> None:
    """Every unusable shape reads as unknown, and none of them invents a Day.

    BARE-LITERAL pin ``DIARY_DAY_UNKNOWN == "?"``. ``bool`` is in the sweep
    because ``True`` is an ``int`` in Python and ``Day True`` would be nonsense
    — the same guard ``eval_transcript._diary_day_attr`` carries.
    """
    assert DIARY_DAY_UNKNOWN == "?"

    block = _story_private_block(_roster(_p("p1", "Kira")), {}, {"p1": [record]})
    assert "  • [diary of Day ?] E" in block
    for junk in ("Day None", "Day True", "Day [2]", "Day 1", "Day 2"):
        assert junk not in block


def test_the_placeholder_normalisation_does_not_change_what_a_player_sees() -> None:
    """THE PINNED INHERITED ODDITY, and the divergence made deliberate.

    ``_walk_private_record`` records that the ``"?"`` placeholder fires only on
    a MISSING ``day`` key, so a record carrying an explicit ``"day": None``
    renders ``[Diary — end of Day None]`` in the PLAYER's block. Task 3.1 pinned
    that as-is rather than move a player-visible byte. Task 3.2 normalises it
    for the STORY only, where ``Day None`` would be a meaningless date handed to
    a model. Both halves are asserted here so neither can be "tidied" into the
    other without a red test.
    """
    record: DiaryRecord = {"day": None, "thoughts_before": 0, "text": "E"}

    player_block = _private_record_block(
        [], [record], thoughts_enabled=True, diaries_enabled=True
    )
    assert f"- {DIARY_LINE_MARKER} None] E" in player_block

    story_block = _story_private_block(_roster(_p("p1", "Kira")), {}, {"p1": [record]})
    assert f"  • {STORY_PRIVATE_DIARY_MARKER} {DIARY_DAY_UNKNOWN}] E" in story_block
    assert "None" not in story_block


# ==========================================================================
# 6 — THE DEGENERATE SHAPES
# ==========================================================================


@pytest.mark.parametrize(
    ("thoughts", "diaries"),
    [
        pytest.param(None, None, id="both-channels-absent"),
        pytest.param({}, {}, id="both-channels-empty"),
        pytest.param({"p1": []}, {"p1": []}, id="empty-lists-for-the-player"),
        pytest.param({"p2": ["OTHER"]}, None, id="only-a-stranger-wrote"),
    ],
)
def test_nothing_written_says_so_in_words(
    thoughts: dict[str, list[str]] | None, diaries: dict[str, list[DiaryRecord]] | None
) -> None:
    """A blank section invites a model to read an omission it should fill.

    ``_story_events_block``'s reasoning, and NOT a hypothetical case: a game won
    on the first Night ends before any reflection or diary is written, so this
    is the real ending of a short game. Task 3.6 owns the game-level version.
    """
    block = _story_private_block(_roster(_p("p1", "Kira")), thoughts, diaries)
    assert block == STORY_PRIVATE_EMPTY == "• (nothing was written privately this game)"
    assert _heading("Kira") not in block


def test_the_human_gets_no_section_because_neither_channel_holds_them() -> None:
    """STRUCTURAL, NOT FILTERED — and the renderer makes no ``is_human`` test.

    ``day_round_reflect`` and ``day_diary`` both skip ``is_human``, so neither
    channel ever holds a key for the human seat; §2.2 calls that "the strongest
    guarantee in the spec" for the criterion that the story "never claims to
    know what the person was thinking". This test asserts the consequence — a
    player with nothing gets no heading, which is what leaves the human out —
    and deliberately does NOT assert the absence of a filter, which would pin
    behaviour nobody wants. Task 3.6 owns the game-level assertion that the
    channels really are empty for the human.
    """
    players = _roster(_p("h", "You", is_human=True), _p("p1", "Kira"))
    block = _story_private_block(players, {"p1": ["KIRA-THOUGHT"]}, None)
    assert _heading("Kira") in block
    assert _heading("You") not in block


def test_a_blank_or_malformed_entry_is_dropped_not_placeheld() -> None:
    """A tag with nothing after it is an omission a model can read as a gap to fill.

    ``thought_text`` yields ``""`` for a malformed element, and a
    ``total=False`` ``DiaryRecord`` may carry no ``text``. Both are dropped
    here, which is a considered divergence from the player's block — that one
    renders a malformed element as a bare ``"- "`` bullet whose trailing space
    spec 039's suite pins.
    """
    players = _roster(_p("p1", "Kira"), _p("p2", "Bram"))
    block = _story_private_block(
        players,
        {"p1": ["  ", {"nope": 1}, "KEPT"], "p2": ["", "   "]},
        {"p1": [{"thoughts_before": 3}]},
    )
    kira = _section(block, "Kira")
    assert kira.splitlines() == [_heading("Kira"), "  • [reflection] KEPT"]
    # An author whose every entry was blank gets no heading at all.
    assert _heading("Bram") not in block


def test_a_line_never_carries_a_trailing_space() -> None:
    """Uniform ``rstrip`` on both kinds.

    The player block strips only the diary line, and that asymmetry is a
    byte-identity obligation to spec 028 (AI Private Thoughts) which this
    reader does not carry.
    """
    for line in (
        _story_private_line((PRIVATE_RECORD_KIND_DIARY, 1, "d  ")),
        _story_private_line((PRIVATE_RECORD_KIND_THOUGHT, None, "t  ")),
    ):
        assert line == line.rstrip()
        assert line.endswith(("d", "t"))


# ==========================================================================
# 7 — PURITY
# ==========================================================================


def test_neither_argument_is_mutated() -> None:
    """A caller may hand over a live state list (the walk copies before slicing).

    Compared against independently-built expectations rather than against
    ``copy.deepcopy`` of the inputs, so the check cannot agree with an
    in-place edit by construction.
    """
    thoughts: dict[str, list[str]] = {"p1": ["t0", "t1"]}
    diaries: dict[str, list[DiaryRecord]] = {"p1": [_rec(1, 1, "d1")]}

    _story_private_block(_roster(_p("p1", "Kira")), thoughts, diaries)

    assert thoughts == {"p1": ["t0", "t1"]}
    assert diaries == {"p1": [{"day": 1, "thoughts_before": 1, "text": "d1"}]}
