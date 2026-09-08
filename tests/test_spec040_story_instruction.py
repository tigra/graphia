"""Spec 040 (The Moderator's Closing Story), Task 4.3 — THE INSTRUCTION.

WHAT THIS FILE OWNS. ``prompts:STORY_SYSTEM`` — the seven moves §2.6 specifies
and Task 4.1 wrote — plus the outcome half of the prompt that Task 4.2 added.
§2.7 states outright that "the instruction itself is directly testable" and
that there is deliberately no automated grounding check, so the instruction is
the whole of this feature's guarantee. Until this file existed, none of it was
under test.

**THE GAP WAS MEASURED, NOT ARGUED.** Task 4.1 reduced ``STORY_SYSTEM`` to a
stub — all seven moves deleted at once — and the suite stayed byte-for-byte at
2674 passed / 1 skipped. Re-verified here before writing a line (see the
mutation table below, row 0). Since a deletion can only break a *presence*
assertion, that one result proves every individual move was unwitnessed.

-------------------------------------------------------------------------------
THE MOVE-BY-MOVE DECISION, WHICH IS THE SUBSTANCE OF THIS TASK
-------------------------------------------------------------------------------

A test that greps one sentence of prompt text pins the **wording**, not the
behaviour, and the maintenance is not free: this spec has already retired one
permanently-red assertion (Task 3.6's "author, Day and kind") and corrected
four stale in-source instructions, three of them wrong precisely because they
described text that had since changed. So each move got a decision rather than
a reflex.

**THE SPINE, AND IT IS WHAT CLOSES THE MEASURED GAP FOR ALL SEVEN.**
:func:`test_the_instruction_is_one_paragraph_per_move` asserts eight
paragraphs — one opening address plus the seven moves, the structure Task 4.1's
in-source note promises ("Each is one paragraph, so … Task 4.3 can assert them
separately"). It pins **no wording at all** and it goes red on the deletion of
**any** move, including the two whose content is deliberately unwitnessed. It
is a deletion witness, not a content witness, and that distinction is stated
here so no later reader over-reads it.

* **Move 1** (name the reader and what they already know) — **NO PIN ON ITS
  ANTI-RECAP FUNCTION; one short pin on its OUTCOME clause.** §2.6 calls it the
  strongest anti-recap move, so the reflex is to grep "they do not need any of
  that again". Two reasons not to. (a) It is the one move with a live open
  question against it: Task 4.2 measured that ``end_screen``'s facts message
  re-enters the prompt through ``{context}``, so move 1 tells the model the
  reader "does not need any of that again" while the material hands it exactly
  those facts — and **Task 4.4 owns the decision** on that. A wording pin on
  the anti-recap sentence is a predictable red on somebody else's decision.
  (b) Its deletion is caught by the spine.

  What it got instead is the half of it that is an **outcome** claim, where the
  defect is concrete rather than stylistic. Move 1 enumerates what the reader
  already knows, and §2.6's canonical quote for that list is *wrong*: it reads
  "they have just read **who won**", which is false on a runaway. Task 4.1
  flagged it and wrote "how the game ended, who died, and who every player
  really was" instead. So two tests hold that correction —
  :func:`test_the_instruction_never_tells_the_reader_they_know_who_won`
  (the phrase must not come back) and
  :func:`test_a_runaway_gets_no_winning_side_from_the_instruction_either`
  (the corrected clause must be there), which is reference 1 of Task 4.2's
  five-reference outcome audit. **That second test does pin eight words of move
  1's wording, and the honest justification is the audit, not §2.6's
  anti-recap argument:** a reader tidying move 1 back to match the tech spec
  would reintroduce §2.6's own "most conspicuous fabrication available".

  One consequence, stated because it is a real cost and it is deliberate: the
  who-won test doctors move 1's text to prove its matcher can fire, so
  **deleting or rewording move 1 breaks that positive control** and the test
  goes red even though the property still holds. That is the control refusing
  to certify an absence over text it can no longer find — the correct
  behaviour for a positive control, and it makes the test a second deletion
  witness for move 1 (measured: mutation 1 turns it red). The cost is that a
  legitimate reword of move 1 costs one line here.

* **Move 2** (name what they have never seen, and that it is the substance) —
  **DELIBERATE ABSTENTION on content.** No functional-spec criterion reads on
  it. The enforceable half of its claim — that the private material really is
  in the prompt — is already witnessed behaviourally by
  ``test_spec040_story_assembly.py`` and ``test_spec040_story_material_from_a_game.py``;
  a grep here would add only the wording. And its wording is *specifically*
  unstable: Task 4.1 finding (v) records that whether the prompt should say
  more about the **empty** private-material case is Task 4.4's to re-check, and
  the "whatever of it there is" hedge is the clause that would change. It keeps
  the spine plus one deliberate-omission witness
  (:func:`test_the_instruction_does_not_re_explain_the_private_channels`),
  which pins move 2's own in-source design note — it must *not* restate the
  diary-vs-reflection mechanics, because ``endgame:STORY_PRIVATE_LEGEND``
  states them inside the block and duplicating them is how the two drift.

* **Move 3** (grant the choice of thread) — **REAL WITNESS.** It carries
  ``functional-spec.md:55`` ("it is free to choose which thread to follow and
  how to tell it"), which no task line claimed before this one. Two short
  anchors, and they are chosen to be the *design decision* rather than the
  prose: ``"Which thread is yours to choose"`` (the grant itself) and
  ``"None of those is asked of you"`` (the disclaimer, lifted from
  ``DIARY_SYSTEM``, that is the whole difference between 039's *choice* and the
  *checklist* the DELIBERATELY ABSENT note forbids). Delete the disclaimer and
  the menu of four threads becomes the checklist this slice exists to fight —
  a real behavioural regression, not a reword.

* **Move 4** (forbid the fixed forms by name) — **REAL WITNESS, on the two
  names only.** §2.6's move is "forbid the fixed forms **by name**", so what is
  load-bearing is that *chronology* and *roll-call* are named: the anchors are
  ``"do not walk through the game day by day"`` and ``"do not go down the
  roster player by player"``, plus ``"There is no set form"``, which is the
  half of ``functional-spec.md:55`` that reads "and how to tell it".
  **Abstained on the headings/bullets/numbering clause** — it is inherited 039
  boilerplate, it is the most reword-prone sentence in the prompt, and Slice
  5's paragraph-preserving clamp is the real enforcement of the story's shape.

* **Move 5** (the record constraint as a boundary on nouns) — **REAL WITNESS,
  as three literal sentences, and here is why the string is worth its cost.**
  §2.6 does not merely describe this move, it *designates* it: "**This is the
  sentence the acceptance criterion tests for.**" Two functional-spec criteria
  read on it — ``:53`` (stay to what happened; "not to invent events, players
  or motives", which is the second sentence's *own* wording, quoted from the
  spec) and ``:55``'s "within that, the telling is yours" tail, which Task
  4.1's note says is "part of it, not decoration". A test whose expected string
  is quoted from the spec fails only when the spec's own words stop being in
  the prompt, which is the failure the criterion is about. Task 4.2's third
  sentence is pinned the same way, at its request.

* **Move 6** (the human clause) — **REAL WITNESS, and PAIRED, which is the
  point.** ``STORY_ROSTER_HUMAN_MARKER`` cannot be interpolated into
  ``STORY_SYSTEM`` (``prompts.py`` is a leaf; ``nodes/endgame.py:109`` imports
  from it, so reading the marker back is an import cycle), so the prompt
  *describes* the mark rather than quoting it. The clause alone is therefore
  the weakest possible witness — it can be present while pointing at nothing,
  which is exactly what Slice 1 shipped and what made a live run narrate the
  reader in the third person as "Alexey, another innocent citizen". So the
  clause and the marked roster line are asserted **in one test**: either half
  alone is misleading.

* **Move 7** (the length bound) — **REAL WITNESS, the cheapest and strongest of
  the five, and it needs no literal at all.** Three assertions: the constant's
  absolute value (039's ``DIARY_SENTENCE_BOUND`` precedent — the relation tests
  read the constant, so they slide past a mutation *of* the constant); the
  bound stated exactly once in ``STORY_SYSTEM``; and — the part 039 does not
  have — an **AST check that the number reaches the prompt by interpolating
  ``STORY_PARAGRAPH_BOUND``**, not as a retyped digit. It carries
  ``functional-spec.md:62`` ("it states an upper limit on the length of the
  story"), the other criterion no task line claimed.

  *No English-numeral absence check, and that is a decision.* "Assert 'four'
  is absent" is the shape this spec has been bitten by three times, and it is
  also unnecessary: a retyped ``"four"`` removes the digit, so
  ``count(str(BOUND)) == 1`` already goes red, and a retyped ``"4"`` is caught
  by the AST check. The hazard is fully covered with no absence assertion.

**ANTI-VACUITY.** Every absence assertion in this file is preceded by its
positive control, in the same test:

* the outcome sweep asserts the winner's *own* line is present **before**
  asserting the two side-naming constants are absent — and it does so under one
  parametrization that includes ``"law_abiding"`` and ``"mafia"``, so the
  control and the absence are literally the same assertion body;
* the facts-message witness asserts the three facts headers are **in** the
  ``{context}`` slot before asserting the same slot does not carry them without
  the message;
* each deliberate-omission test runs its own matcher against a **doctored**
  copy of ``STORY_SYSTEM`` first, so a matcher that could never fire fails
  loudly instead of passing.

**HARD-WRAPPING IS WHY NOTHING HERE MATCHES A RAW LITERAL, and it is a
correction to Task 4.2's handover instruction.** ``STORY_SYSTEM`` is hard
wrapped at ~78 columns, so *every* sentence worth asserting spans a newline:
``must appear\\nbelow``, ``players or\\nmotives``, ``is not\\nyours to
settle``, ``None\\nof those is asked of you``, ``at most 4 short\\nparagraphs``.
Task 4.2 asked for its sentence "retyped as a literal … present exactly once"
— retyped flat, it is **not** a substring of the prompt. Every match here
therefore goes through :func:`_flat`, which collapses whitespace runs. That is
*more* durable, not less: a rewrap — which happens automatically whenever an
earlier word changes length — cannot break these tests, while a genuine reword
still does.

Task 4.2 also asked for the sentence's boundaries checked as "preceded by
``recorded. ``, followed by ``\\n\\nOne seat``". Both are literals about
*neighbouring* text, so a reword of move 6 would break a runaway assertion.
The same property is pinned structurally instead:
:func:`test_move_5s_outcome_sentence_is_the_last_of_its_paragraph` asserts move
5's paragraph is exactly three sentences, in order, and that move 6's paragraph
is the next one.

**NOTHING HERE DRIVES A GAME.** Every property is a property of one pure
assembly, so each is asserted by composing the prompt directly — Task 3.4's
``_full_prompt`` (the whole request, ``STORY_SYSTEM`` included) and Task 3.3's
``_slot_text``, imported rather than re-derived. Slice 3's preamble
warns against copying the live-dispatcher pattern, and the headless drive
helpers in ``test_spec040_story_topology.py`` are not needed for a prompt.

-------------------------------------------------------------------------------
MUTATION TABLE — every mutation applied to production code for real from green,
measured over the whole suite, and reverted (2026-09-08)
-------------------------------------------------------------------------------

Full suite is 2699 tests with this file (2674 + 25); every row's
failed + passed reconciles to it. Rows marked *file* were additionally re-run
file-scoped, and **every file-scoped count equalled its full-suite count** — so
no test outside this file reacts to any prompt-text mutation, and the "which"
column is complete rather than truncated.

===================================  ======  ====================================
mutation                             failed  which
===================================  ======  ====================================
0a. ``STORY_SYSTEM`` stubbed to one   **0**  **2674 passed / 1 skipped** — Task
    sentence (all 7 moves deleted),          4.1's measurement reproduced
    this file ignored                        byte-for-byte
0b. the same stub, this file in        11    every test below except the 10
                                             outcome-sweep cases, move 5's
                                             ``:53`` noun-list half, and the two
                                             deliberate-omission tests that do
                                             not depend on deleted text
1. move 1's paragraph deleted          3     the spine; the who-won omission
                                             witness (its control doctors move
                                             1); the runaway-instruction test
2. move 2's paragraph deleted        **1**   the spine, and only the spine — the
                                             documented abstention, measured
3. move 3's paragraph deleted          2     the spine; move 3
4. move 4's paragraph deleted          3     the spine; move 4; move 3 (it
                                             asserts ``:55``'s "no set form")
5. move 5's paragraph deleted          7     the spine; all three move-5 tests;
                                             move 3 (``:55``'s "telling is
                                             yours" tail); the who-won control;
                                             the runaway-instruction test
6. move 6's paragraph deleted          3     the spine; move 6; move 5's
                                             paragraph-order boundary check
7. move 7's paragraph deleted          3     the spine; both move-7 tests
8. move 3 loses "None of those is    **1**   move 3 — the disclaimer that keeps
   asked of you"                              the menu a choice, alone
9. move 4 stops naming the two       **1**   move 4, alone
   moulds ("do not use one")
10. move 5's noun boundary reverted    3     move 5 (the designated sentence);
    to Slice 1's "Keep to the game            move 5's sentence order; move 3's
    recorded below."                          ``:55`` tail
11. Task 4.2's sentence swapped for    3     move 5's sentence order; the
    a near-miss paraphrase                   runaway-instruction test; the
    ("set out"/"for you to                   who-won control
    decide"/"no victor")
12. ``{STORY_PARAGRAPH_BOUND}``        2     both move-7 tests
    retyped as the word "four"
13. ``{STORY_PARAGRAPH_BOUND}``      **1**   the AST check, **alone** — the row
    retyped as the digit "4"                 039's discipline does not cover
14. ``STORY_ROSTER_HUMAN_MARKER`` no   3     move 6 here, plus 2 pre-existing in
    longer applied to the human seat         ``test_spec040_story_assembly.py``
15. move 1 reworded to §2.6's          2     the who-won omission witness; the
    "who won, who died"                      runaway-instruction test
16. ``_winner_line`` returns           5     the outcome sweep's two ``runaway``
    ``ENDGAME_WINNER_LAW`` for                cases and the facts-slot witness
    ``"runaway"``                             here, plus 2 pre-existing
                                              (``test_slice9_polish.py``,
                                              ``test_spec040_story_ui_order.py``)
17. the facts message given            8     the facts-slot witness here, plus 7
    ``private_to`` so                        pre-existing across
    ``_render_context`` drops it             ``test_remote_mode_smoke.py``,
                                             ``test_slice8_endgame.py``,
                                             ``test_spec040_story_ui_order.py``,
                                             ``test_spec040_story_whisper_privacy.py``
===================================  ======  ====================================

**Rows 12 and 13 are the pair that justifies the AST check.** 039's
``DIARY_SYSTEM`` discipline catches 12 (a retyped word removes the digit, so
the count assertion fires) and would let 13 through — a retyped digit agrees
with the constant, so every runtime assertion stays green and the drift
surfaces only the next time somebody changes the number. Row 13 is a single
red, and it is the AST check.

**Rows 2, 8, 9 and 13 are the sharpest results**, because each is exactly one
red and it is the test that claims that property. Row 2 is the one deliberate
abstention, measured rather than asserted: move 2 has the spine and nothing
else, by decision.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from graphia.nodes.endgame import (
    STORY_ROSTER_HUMAN_MARKER,
    _persona_reveal_line,
    _winner_line,
    end_screen,
)
from graphia.prompts import (
    ENDGAME_HEADER_ROSTER,
    ENDGAME_PERSONA_HEADER,
    ENDGAME_WINNER_DRAW,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
    ENDGAME_WINNER_RUNAWAY,
    STORY_PARAGRAPH_BOUND,
    STORY_SYSTEM,
)

# Task 3.4's composer and Task 3.3's slot reader, imported rather than
# re-derived — nine files in this suite already assemble this prompt and a
# tenth copy is how they drift. ``_full_prompt`` returns the WHOLE request with
# ``STORY_SYSTEM`` in it, which is what an instruction test needs (Task 3.3's
# ``_compose`` returns the user prompt alone, so it is not used here).
# ``_full_prompt`` asserts the large tier was reached with the ``Story`` schema
# before returning, so a fixture mistake cannot masquerade as ``_ai_story``'s
# legitimate no-story ending and leave every assertion below vacuous.
# ``_slot_text`` derives each slot's delimiters from ``STORY_USER_TEMPLATE``
# itself, so a reword of the slot LABELS breaks nothing here.
from test_spec040_story_assembly import (  # type: ignore[import-not-found]
    _slot_text,
)
from test_spec040_story_whisper_privacy import (  # type: ignore[import-not-found]
    _full_prompt,
    _game_state,
)

_PROMPTS_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "graphia" / "prompts.py"
)

# §2.6's seven moves, plus the opening address that names the Moderator and the
# reader. Written once, here, because the spine test and the reader both need
# it and a bare literal ``8`` in an assertion says nothing.
_MOVE_COUNT = 7
_OPENING_ADDRESS = 1


# ==========================================================================
# Shared helpers
# ==========================================================================


def _flat(text: str) -> str:
    """Collapse every run of whitespace to one space.

    THE REASON THIS FILE ASSERTS NOTHING AGAINST A RAW LITERAL. See the module
    docstring: ``STORY_SYSTEM`` is hard wrapped, so every sentence worth
    asserting spans a newline and a retyped flat sentence is not a substring of
    the prompt. Matching flattened text survives a rewrap — which is automatic
    whenever an earlier word changes length — and still fails on a reword.
    """
    return re.sub(r"\s+", " ", text).strip()


def _paragraphs(text: str) -> list[str]:
    """The blank-line-separated paragraphs of a prompt, non-empty ones only."""
    return [p for p in text.strip().split("\n\n") if p.strip()]


def _sentences(paragraph: str) -> list[str]:
    """One paragraph's sentences, flattened.

    Split on a full stop followed by whitespace. Safe over this prompt: no
    abbreviation, decimal or ellipsis appears in any of the eight paragraphs,
    and the only non-alphanumeric token is move 7's backticked ``text`` field.
    Used on move 5's and move 6's paragraphs only.
    """
    return [s for s in re.split(r"(?<=\.)\s+", _flat(paragraph)) if s]


def _move(anchor: str) -> str:
    """The one ``STORY_SYSTEM`` paragraph whose flattened text carries ``anchor``.

    Locating a move by its own minimal anchor rather than by index means the
    seven may be REORDERED without touching this file, while a move that went
    missing — or one whose anchor turned up twice — fails loudly here instead
    of silently matching the wrong paragraph.
    """
    flat_anchor = _flat(anchor)
    hits = [p for p in _paragraphs(STORY_SYSTEM) if flat_anchor in _flat(p)]
    assert len(hits) == 1, (
        f"expected exactly one STORY_SYSTEM paragraph carrying {flat_anchor!r}, "
        f"found {len(hits)}"
    )
    return hits[0]


def _phrase_matcher(*phrases: str) -> Callable[[str], list[str]]:
    """A matcher over flattened text, returned so it can be positive-controlled.

    Every deliberate-omission test below runs the matcher against a DOCTORED
    copy of ``STORY_SYSTEM`` before asserting it finds nothing in the real one.
    An absence assertion whose matcher could never fire proves nothing, and
    this spec has been bitten by that exact shape three times (Tasks 1.7, 1.8,
    3.6).
    """

    def matcher(text: str) -> list[str]:
        flat = _flat(text).lower()
        return [p for p in phrases if p.lower() in flat]

    return matcher


def _human_seat(state: dict) -> Any:
    """The one seat flagged ``is_human`` in a state's roster."""
    seats = [p for p in state["players"].values() if p.is_human]
    assert len(seats) == 1, f"expected exactly one human seat, got {len(seats)}"
    return seats[0]


def _with_facts_message(state: dict) -> dict:
    """``state`` with the REAL facts message ``end_screen`` writes appended.

    Task 4.2 measured that this message re-enters the story prompt through
    ``{context}`` in every game. Built by calling ``end_screen`` rather than by
    hand-typing its text, so the tests below cannot pass against a fact sheet
    the game does not actually emit.
    """
    facts = end_screen(state)["messages"][0]  # type: ignore[arg-type]
    return {**state, "messages": [*state.get("messages", []), facts]}


# ==========================================================================
# 0 — THE SPINE: one paragraph per move
#
# The only witness moves 1 and 2 have, and the witness that closes Task 4.1's
# measured all-seven-deletion hole for every move at once. It pins no wording.
# ==========================================================================


def test_the_instruction_is_one_paragraph_per_move() -> None:
    """``STORY_SYSTEM`` is the opening address plus §2.6's seven moves, one each.

    Task 4.1's in-source note states this structure as a promise to this task:
    "Each is one paragraph, so a maintainer can see which sentence is which
    move and Slice 4 Task 4.3 can assert them separately." This is that promise
    held.

    WHAT IT IS AND IS NOT. It is a DELETION witness: remove any move and the
    count drops. It is not a CONTENT witness — a move replaced by a different
    paragraph passes here, which is why the moves that matter also have their
    own tests below. Read it as the floor under all seven, not as coverage of
    any one.

    A legitimate eighth move would fail here, on purpose: §2.6 fixes the count
    at seven, so adding one is a spec change and should cost a line in a test.
    """
    paragraphs = _paragraphs(STORY_SYSTEM)
    assert len(paragraphs) == _OPENING_ADDRESS + _MOVE_COUNT, (
        f"expected {_OPENING_ADDRESS + _MOVE_COUNT} paragraphs (the opening "
        f"address plus §2.6's {_MOVE_COUNT} moves), got {len(paragraphs)}: "
        f"{[_flat(p)[:60] for p in paragraphs]}"
    )
    # No paragraph is a stub: each move is a real instruction, and a move
    # emptied down to a fragment would otherwise keep the count.
    for index, paragraph in enumerate(paragraphs):
        assert _flat(paragraph).endswith("."), (
            f"paragraph {index} is not a complete sentence: {_flat(paragraph)!r}"
        )


# ==========================================================================
# 1 — MOVE 5: the record constraint, the sentence §2.6 designates
#
# functional-spec.md:53 and the "within that, the telling is yours" half of :55
# ==========================================================================

# Quoted from §2.6 and from the functional spec, NOT copied off the prompt —
# the point of a literal here is that the criterion's own words are the
# expected value, so the test fails exactly when the prompt stops saying what
# the spec says it says.
_MOVE_5_NOUN_BOUNDARY = (
    "Every player you name, every death and every vote you mention must appear "
    "below; within that, the telling is yours."
)
_MOVE_5_NO_INVENTION = (
    "Do not invent events, players or motives that no one recorded."
)
_MOVE_5_OUTCOME_IS_NOT_YOURS = (
    "How the game ended is stated below and is not yours to settle: where the "
    "record names no winning side, neither do you."
)


def test_move_5_states_the_record_constraint_as_a_boundary_on_nouns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§2.6's designated sentence is in the composed prompt, exactly once.

    "**This is the sentence the acceptance criterion tests for**" — §2.6, of
    this sentence specifically. That designation is why a literal is worth its
    maintenance here and nowhere else in the instruction: the criterion is
    about these words, so the expected value is quoted from the spec rather
    than from the prompt.

    The "; within that, the telling is yours" tail is asserted as PART of the
    sentence, not as decoration — Task 4.1's note calls it out by name, and it
    is the half that carries ``functional-spec.md:55``'s "and how to tell it".
    A noun boundary with the freedom clause stripped off is precisely the bare
    "keep to the record" §2.6 says CREATES the flat summary.

    Asserted over the whole composed request, not over the constant, because
    the criterion is about the prompt the model is handed.
    """
    flat = _flat(_full_prompt(monkeypatch, _game_state()))
    assert flat.count(_flat(_MOVE_5_NOUN_BOUNDARY)) == 1, (
        "§2.6's record-constraint sentence is not in the composed prompt "
        "exactly once"
    )


def test_move_5_forbids_invention_in_the_functional_specs_own_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``functional-spec.md:53``, whose wording move 5's second sentence carries.

    The criterion reads "then it tells the story to stay to what happened and
    **not to invent events, players or motives**" — and Task 4.1's in-source
    note records that the second sentence was written to carry exactly that
    phrase. Both are asserted: the sentence, and the criterion's bare noun list
    on its own, so a reword that keeps the spec's words in a different sentence
    still satisfies the criterion half.

    NOTE ON SCOPE, because Task 4.3's own line understates it: the line asks
    for "the record-constraint sentence of move 5", which is §2.6's canonical
    quote and the FIRST sentence only. ``:53`` reads on the first two together.
    """
    flat = _flat(_full_prompt(monkeypatch, _game_state()))
    assert _flat(_MOVE_5_NO_INVENTION) in flat
    assert "events, players or motives" in flat


def test_move_5s_outcome_sentence_is_the_last_of_its_paragraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 4.2's sentence: present once, third of three, and move 6 follows.

    Task 4.2 asked for the sentence "retyped as a literal … present exactly
    once in the composed prompt, with its sentence boundaries checked
    (preceded by ``recorded. ``, followed by ``\\n\\nOne seat``)". Both halves
    of that instruction are honoured, and both are adjusted — see the module
    docstring:

    * the literal is matched flattened, because the sentence spans a hard line
      break (``is not\\nyours to settle``) and retyped flat it is not a
      substring of the prompt at all;
    * the boundaries are pinned STRUCTURALLY rather than by the neighbouring
      literals, because ``\\n\\nOne seat`` is move 6's text and a reword of
      move 6 would then break a runaway assertion for no reason. Move 5's
      paragraph is three sentences in a fixed order and move 6's paragraph is
      the next one, which is the same claim without the coupling.

    WHY THE SENTENCE LIVES IN MOVE 5's PARAGRAPH AT ALL, since a reader will
    ask: the gap it closes is a gap in move 5. Move 5's first sentence bounds
    three noun classes (players, deaths, votes) and its second bounds three
    more (events, players, motives) — **a winning side is none of those six**,
    so a story writing "and so the Mafia's grip closed on the town" onto a
    runaway would satisfy every word of the first two while asserting an
    outcome the record denies.
    """
    flat = _flat(_full_prompt(monkeypatch, _game_state()))
    assert flat.count(_flat(_MOVE_5_OUTCOME_IS_NOT_YOURS)) == 1

    paragraphs = _paragraphs(STORY_SYSTEM)
    move_5 = _move("Every player you name")
    assert _sentences(move_5) == [
        _flat(_MOVE_5_NOUN_BOUNDARY),
        _flat(_MOVE_5_NO_INVENTION),
        _flat(_MOVE_5_OUTCOME_IS_NOT_YOURS),
    ]
    move_6 = _move("One seat on the roster below is marked")
    assert paragraphs[paragraphs.index(move_5) + 1] == move_6, (
        "the human clause no longer follows the record constraint"
    )


# ==========================================================================
# 2 — MOVE 6: the human clause, PAIRED with the marked roster line
#
# functional-spec.md:69
# ==========================================================================


def test_move_6_is_its_own_sentence_and_the_roster_marks_the_seat_it_points_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clause and the mark, in one test, because either alone is misleading.

    §2.6 asks for the human clause "as its own sentence, so it is separately
    assertable", and Task 4.3's line asks for the clause and the marker
    "together". Task 4.1 recorded why that pairing has to happen at test level:
    ``STORY_ROSTER_HUMAN_MARKER`` cannot be interpolated into ``STORY_SYSTEM``
    because ``prompts.py`` is a leaf (only ``__future__``) and
    ``nodes/endgame.py:109`` imports from it, so reading the marker back would
    be an import cycle. The prompt therefore DESCRIBES the mark ("One seat on
    the roster below is marked as …") instead of quoting it.

    **WHICH IS EXACTLY WHY THE CLAUSE ALONE IS NOT A WITNESS.** It can be
    present and point at nothing — that is what Slice 1 shipped, and the
    measured consequence (Task 2.3) was a live run narrating the reader in the
    third person as "Alexey, another innocent citizen". So the marker in the
    composed roster is asserted here as well, and yes, that half is also
    covered by ``test_spec040_story_assembly.py::test_the_human_seat_is_marked_and_no_other_seat_is``.
    The duplication is the property: what this test owns is that the two cannot
    drift apart, which neither file can hold on its own.

    ``functional-spec.md:69`` ("it speaks only of what that seat did in the
    open — never of private thoughts or plans it attributes to them") is the
    criterion, and §2.7 is explicit that for an INVENTED private thought "only
    the prompt clause stops it" — there is no structural guard. So this clause
    is the whole of that criterion's enforcement.
    """
    state = _game_state()
    prompt = _full_prompt(monkeypatch, state)

    # (a) the clause is one sentence, and its own paragraph.
    clause = _move("One seat on the roster below is marked")
    assert len(_sentences(clause)) == 1, (
        f"the human clause is no longer one sentence: {_sentences(clause)}"
    )
    assert "never claim to know what the person was thinking" in _flat(clause)
    assert _flat(clause) in _flat(prompt)

    # (b) the roster the clause points at really marks one seat, and it is the
    #     human's. Read out of the {roster} slot of the SAME composed prompt,
    #     so the clause and the mark are witnessed against one another.
    roster = _slot_text(prompt, "roster")
    lines = [ln for ln in roster.splitlines() if ln.strip()]
    assert len(lines) == len(state["players"]), (
        "the roster slot does not carry every seat, so asserting which one is "
        "marked would measure nothing"
    )
    marked = [ln for ln in lines if STORY_ROSTER_HUMAN_MARKER in ln]
    assert len(marked) == 1, (
        f"expected exactly one marked seat, got {len(marked)}: {marked}"
    )
    assert _human_seat(state).name in marked[0]


# ==========================================================================
# 3 — MOVE 7: the length bound
#
# functional-spec.md:62 — previously unowned by any task line
# ==========================================================================


def test_story_paragraph_bound_is_four() -> None:
    """``STORY_PARAGRAPH_BOUND`` is 4 — pinned as an ABSOLUTE literal.

    039's ``test_diary_sentence_bound_is_six`` and its reason, which transfers
    unchanged: the relation tests below necessarily READ the constant, so they
    would slide past a mutation of the constant itself. This is the test that
    notices.

    It also holds one end of the drift risk Task 4.1 handed to Task 5.2:
    ``STORY_MAX_PARAGRAPHS`` will be the same number in the same dimension in
    another module, so whoever writes the clamp either derives it from this
    constant or pins their equality — and this assertion is what makes a
    silent divergence visible from the prompt side.
    """
    assert STORY_PARAGRAPH_BOUND == 4


def test_move_7_states_the_length_bound_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``functional-spec.md:62``: the request states an upper limit, once.

    §2.6 move 7 is "the length bound, **stated once**", and 039's
    ``test_the_stated_bound_is_the_constant_interpolated`` is the precedent for
    both halves — the bound appears in the prompt as the constant's value, and
    the number is written down a single time so the prose and the constant
    cannot drift.

    The count is taken over ``STORY_SYSTEM`` rather than over the composed
    prompt on purpose: the composed prompt legitimately contains digits from
    the material (Day numbers in a diary heading, for one), so counting there
    would be counting the fixture.
    """
    assert STORY_SYSTEM.count(str(STORY_PARAGRAPH_BOUND)) == 1, (
        "the paragraph bound is not written down exactly once in STORY_SYSTEM"
    )
    stated = (
        f"as at most {STORY_PARAGRAPH_BOUND} short paragraphs separated by "
        "blank lines"
    )
    assert _flat(stated) in _flat(STORY_SYSTEM)
    # And it reaches the model, not just the constant.
    assert _flat(stated) in _flat(_full_prompt(monkeypatch, _game_state()))


def test_move_7s_bound_is_the_constant_interpolated_not_a_retyped_number() -> None:
    """The number reaches the prompt by interpolation — checked in the SOURCE.

    THE ROW 039's DISCIPLINE DOES NOT COVER, and Task 4.3's line asks for it by
    name ("assert the bound is stated once and is the interpolation of
    ``STORY_PARAGRAPH_BOUND``, not a retyped 'four'"). A retyped ``"four"`` is
    already caught by the count test above, because it removes the digit. A
    retyped ``"4"`` is **not**: the value still agrees, so every runtime
    assertion stays green and the drift only surfaces the next time somebody
    changes the constant — which is the failure mode 039's
    ``DIARY_SENTENCE_BOUND`` discipline exists to prevent, one release too
    late.

    So this reads ``prompts.py`` and asserts the shape of the assignment: an
    f-string, whose interpolations are exactly one reference to
    ``STORY_PARAGRAPH_BOUND``. Precedent for an AST check over ``src/`` in this
    suite: ``test_spec040_story_whisper_privacy.py``'s ``_private_to_sites``
    and Task 3.5's ``runtime/__main__.py`` check.

    Deliberately NOT an absence assertion on the word "four". The bound's own
    English numeral is a word that some values ("one", "two") occur naturally
    all over this prompt, so the check would be sound for 4 and vacuous or
    falsely red for other values. The pair above covers the hazard completely
    without one.
    """
    tree = ast.parse(_PROMPTS_SOURCE.read_text(encoding="utf-8"))
    targets = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "STORY_SYSTEM"
    ]
    assert len(targets) == 1, (
        f"expected one module-level STORY_SYSTEM assignment, found {len(targets)}"
    )
    value = targets[0]
    assert isinstance(value, ast.JoinedStr), (
        "STORY_SYSTEM is no longer an f-string, so the paragraph bound cannot "
        "be reaching it by interpolation"
    )
    interpolated = [
        part.value.id
        for part in value.values
        if isinstance(part, ast.FormattedValue)
        and isinstance(part.value, ast.Name)
    ]
    assert interpolated == ["STORY_PARAGRAPH_BOUND"], (
        "STORY_SYSTEM's interpolations are "
        f"{interpolated!r}; the stated bound must be STORY_PARAGRAPH_BOUND "
        "interpolated exactly once, never a retyped number"
    )


# ==========================================================================
# 4 — MOVE 3: the choice of thread
#
# functional-spec.md:55 — previously unowned by any task line
# ==========================================================================


def test_move_3_grants_the_choice_of_thread_rather_than_naming_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``functional-spec.md:55``: free to choose which thread to follow.

    039's lesson, and §2.6 states it as the reason this move exists: naming ONE
    direction steers every story the same way, whereas naming the CHOICE makes
    variation legitimate. Two anchors, and both are the design rather than the
    prose:

    * ``"Which thread is yours to choose"`` — the grant itself, and the clause
      the criterion reads on.
    * ``"None of those is asked of you"`` — the disclaimer, lifted from
      ``DIARY_SYSTEM``. **This is the load-bearing half.** Move 3 offers four
      example threads; without the disclaimer that list is a menu the model
      will read as a specification, which is the checklist the DELIBERATELY
      ABSENT note forbids and the collapse this whole slice exists to fight.
      Its deletion is a behavioural regression, not a reword — which is what
      makes the string worth pinning.

    The criterion's second half ("and how to tell it") is carried by move 4's
    "There is no set form" and by move 5's "within that, the telling is yours";
    both are asserted here too, so the criterion is covered by one test rather
    than spread across three where a reader would have to reassemble it.
    """
    thread = _move("Which thread is yours to choose")
    assert "None of those is asked of you" in _flat(thread), (
        "move 3's menu of threads has lost the disclaimer that keeps it a "
        "choice rather than a checklist"
    )

    flat = _flat(_full_prompt(monkeypatch, _game_state()))
    assert "Which thread is yours to choose" in flat
    assert "None of those is asked of you" in flat
    # The "and how to tell it" half of the same criterion.
    assert "There is no set form" in flat
    assert "within that, the telling is yours" in flat


# ==========================================================================
# 5 — MOVE 4: the fixed forms, forbidden BY NAME
# ==========================================================================


def test_move_4_forbids_the_two_moulds_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chronology and roll-call are named, which IS the move.

    §2.6: "forbid the fixed forms **by name** … naming *chronology* and
    *roll-call* is the analogue of 039 naming 'summing-up-followed-by-a-plan':
    they are the two moulds a grounded storyteller falls into unprompted." A
    generic "do not use a fixed form" would satisfy a lenient reading of the
    move and lose the whole of its effect, so what is asserted is the two
    names — not the sentence around them.

    ABSTAINED: the "No headings, no bullet points, no numbering" clause. It is
    inherited 039 boilerplate rather than this spec's finding, it is the most
    reword-prone sentence in the prompt, and the story's actual shape is
    enforced by Slice 5's paragraph-preserving clamp rather than by this
    sentence. Pinning it would buy wording and cost maintenance.
    """
    form = _move("There is no set form")
    flat_form = _flat(form)
    assert "do not walk through the game day by day" in flat_form, (
        "move 4 no longer forbids the day-by-day chronology by name"
    )
    assert "do not go down the roster player by player" in flat_form, (
        "move 4 no longer forbids the per-player roll-call by name"
    )

    flat = _flat(_full_prompt(monkeypatch, _game_state()))
    assert "day by day" in flat
    assert "player by player" in flat


# ==========================================================================
# 6 — THE DELIBERATE OMISSIONS
#
# Not one of the seven moves: the "DELIBERATELY ABSENT — DO NOT TIDY THIS IN"
# note above STORY_SYSTEM, plus move 1's and move 2's own in-source omissions.
# A test is the only enforcement any of them has, and each failure is a real
# defect rather than a reword. Every one is positive-controlled on a doctored
# copy first.
# ==========================================================================


def test_the_instruction_never_invites_embellishment() -> None:
    """No "dramatic", "vivid" or "compelling" — the words §2.6 names.

    §2.7 records that embellishment of a real event is one of the three things
    grounding CANNOT be guaranteed against, and that there is deliberately no
    automated grounding check to catch it. So these three words are the risk
    this spec is most exposed to, and the DELIBERATELY ABSENT note is the only
    thing keeping them out. A note is not enforcement; this is.

    The matcher is proved able to fire, on a doctored copy, before its silence
    on the real prompt is treated as evidence.
    """
    matcher = _phrase_matcher("dramatic", "vivid", "compelling")
    doctored = STORY_SYSTEM + "\nMake it vivid, dramatic and compelling.\n"
    assert sorted(matcher(doctored)) == ["compelling", "dramatic", "vivid"], (
        "the matcher cannot detect the words it is asserting the absence of"
    )
    assert matcher(STORY_SYSTEM) == [], (
        "STORY_SYSTEM now invites embellishment — see the DELIBERATELY ABSENT "
        "note above it in prompts.py"
    )


def test_the_instruction_does_not_re_explain_the_private_channels() -> None:
    """Move 2 points at the material; it does not restate its mechanics.

    Move 2's own in-source note: it "deliberately does NOT re-explain
    diary-vs-reflection mechanics — ``endgame:STORY_PRIVATE_LEGEND`` already
    states those inside the block itself, and duplicating them here is how the
    two drift." Two statements of the same mechanics in one prompt, in two
    modules, is a drift this spec has already paid for once: Task 3.6 retired a
    permanently-red assertion built on the belief that every private entry
    carries a Day, which only the legend states correctly.

    THIS IS MOVE 2'S ONLY CONTENT-ADJACENT WITNESS, and it is a witness for the
    move's deliberate omission rather than for its presence. The abstention on
    move 2's own wording is argued in the module docstring: its "whatever of it
    there is" clause is under an open Task 4.4 re-check, so pinning it would be
    a scheduled red on somebody else's decision.
    """
    matcher = _phrase_matcher("diary", "diaries", "reflection")
    doctored = (
        STORY_SYSTEM
        + "\nThe diaries below are whole-day entries; a diary covers a day and "
        "a reflection covers one round.\n"
    )
    assert sorted(matcher(doctored)) == ["diaries", "diary", "reflection"], (
        "the matcher cannot detect the words it is asserting the absence of"
    )
    assert matcher(STORY_SYSTEM) == [], (
        "STORY_SYSTEM now restates the private-channel mechanics that "
        "endgame:STORY_PRIVATE_LEGEND owns"
    )


def test_the_instruction_never_tells_the_reader_they_know_who_won() -> None:
    """Move 1 enumerates what the reader knows — and a winner is not in it.

    **THIS IS THE WITNESS MOVE 1 GOT INSTEAD OF A WORDING PIN**, and it exists
    because §2.6's own canonical quote for move 1 is wrong. §2.6 writes it as
    "they have just read **who won**, who died, who everyone really was";
    Task 4.1 wrote "how the game ended, who died, and who every player really
    was" instead and flagged the discrepancy, because ``winner`` can be
    ``"runaway"`` and a story declaring a winner on an unresolved game is
    §2.6's own "most conspicuous fabrication available".

    So the live hazard is a reader tidying the prompt back to match the tech
    spec. That is reference 1 of Task 4.2's five-reference outcome audit, and
    this is its witness.

    ``"the winner"`` is included alongside ``"who won"`` because it is the
    vocabulary the functional spec itself uses in four places (lines 13, 36, 83
    and 90 — verified by Task 4.2 finding (iv)), which makes it the likeliest
    phrasing for exactly that tidy-in. ``"winning side"`` is deliberately NOT
    searched for: move 5's third sentence says "where the record names no
    winning side", and forbidding the phrase would fail correctly-written text.
    """
    matcher = _phrase_matcher("who won", "the winner")
    doctored = STORY_SYSTEM.replace(
        "how the game ended, who died", "who won, who died"
    ).replace("How the game ended is stated below", "The winner is stated below")
    assert sorted(matcher(doctored)) == ["the winner", "who won"], (
        "the matcher cannot detect the phrases it is asserting the absence of"
    )
    assert matcher(STORY_SYSTEM) == [], (
        "STORY_SYSTEM now tells the model the reader has read who won, which "
        "is false on a runaway — see the outcome audit in prompts.py"
    )


# ==========================================================================
# 7 — THE OUTCOME: no winning side the record does not name
#
# Task 4.3's own line ("assert the outcome reference does not assume a winner
# on a runaway") and Task 4.2's handover assertions (2), (3) and (5).
# ==========================================================================

# Imported, never retyped, per Task 4.2's handover (2). The two that NAME A
# SIDE are the pair whose absence is the property; the others are the positive
# controls, and they sit in the same parametrization so the control and the
# absence are one assertion body.
_SIDE_NAMING = (ENDGAME_WINNER_LAW, ENDGAME_WINNER_MAFIA)
_OUTCOME_LINES: dict[str | None, str] = {
    "law_abiding": ENDGAME_WINNER_LAW,
    "mafia": ENDGAME_WINNER_MAFIA,
    "runaway": ENDGAME_WINNER_RUNAWAY,
    "draw": ENDGAME_WINNER_DRAW,
    # ``_winner_line``'s defensive fall-through — unreachable through the graph
    # (every edge into ``end_screen`` gates on ``winner is not None``) but the
    # only branch where the material names NOTHING about the outcome, so it is
    # the one case where a model with no outcome content could invent one.
    None: _winner_line(None),
}


@pytest.mark.parametrize("with_facts_message", [False, True])
@pytest.mark.parametrize(
    "winner", ["law_abiding", "mafia", "runaway", "draw", None]
)
def test_the_prompt_names_a_winning_side_only_when_the_record_does(
    monkeypatch: pytest.MonkeyPatch,
    winner: str | None,
    with_facts_message: bool,
) -> None:
    """Every outcome branch: its own line present, no other side's claimed.

    THE ANTI-VACUITY SHAPE TASK 4.2 REQUIRED, and it is structural rather than
    a separate control test: the ``"law_abiding"`` and ``"mafia"`` cases assert
    that ``ENDGAME_WINNER_LAW`` / ``ENDGAME_WINNER_MAFIA`` **are** in the
    composed prompt, through the same three lines of assertion that the
    ``"runaway"``, ``"draw"`` and ``None`` cases use to assert they are not. An
    absence over a prompt that never carries outcome text proves nothing, and
    this spec has been bitten by that exact shape three times (Tasks 1.7, 1.8,
    3.6). Here the control cannot rot away from the absence, because they are
    the same code.

    ``with_facts_message`` is Task 4.2's fifth-reference finding as a
    parameter, and it is why every assertion here is PRESENCE, never
    ``count == 1``: with the facts message in ``messages``,
    ``ENDGAME_WINNER_RUNAWAY`` reaches the model **twice** — once as
    ``{outcome}`` and once through ``{context}``. The ``True`` half is the
    realistic shape of a real game (``graph.py:433`` puts ``end_screen``
    immediately before ``moderator_story``), and it is where a naive count
    assertion would have gone red.

    Expected values are the imported constants and ``_winner_line`` itself, so
    a reword of any outcome line cannot leave a stale literal passing.
    """
    state = _game_state(winner=winner)
    if with_facts_message:
        state = _with_facts_message(state)
    prompt = _full_prompt(monkeypatch, state)

    expected = _OUTCOME_LINES[winner]
    # POSITIVE CONTROL FIRST — the prompt does carry this game's outcome text.
    assert expected in prompt, (
        f"winner={winner!r}: the prompt does not carry {expected!r}, so the "
        "absence assertions below would prove nothing"
    )
    for side_line in _SIDE_NAMING:
        if side_line == expected:
            continue
        assert side_line not in prompt, (
            f"winner={winner!r}: the prompt claims {side_line!r}, a winning "
            "side the record does not name"
        )


def test_a_runaway_gets_no_winning_side_from_the_instruction_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The instruction's own outcome references, on the case that has no winner.

    Task 4.3's line asks that "the outcome reference does not assume a winner
    on a runaway". The sweep above covers the MATERIAL's outcome references;
    this covers the INSTRUCTION's, which are references 1, 2 and 3 of Task
    4.2's audit and are the same bytes whoever won.

    **AND NOT BY ASSERTING THE WORD "runaway" IS IN ``STORY_SYSTEM``** — Task
    4.2's handover (4) forbids that in so many words, and it is right: the
    sentence conditions on THE RECORD rather than on a case, deliberately, so
    that it stays one rule instead of a per-outcome switch (which would be the
    checklist shape the DELIBERATELY ABSENT note forbids). A test for the word
    would fail correctly-written prompt text.
    """
    flat = _flat(_full_prompt(monkeypatch, _game_state(winner="runaway")))
    # POSITIVE CONTROLS FIRST — the three references are really in the prompt,
    # so the absence below is a statement about a prompt that does discuss the
    # outcome, not about one that says nothing.
    assert _flat(_MOVE_5_OUTCOME_IS_NOT_YOURS) in flat  # reference 2, the clause
    assert "how the game ended, who died" in flat  # move 1, reference 1
    assert "How the game ended:" in flat  # the {outcome} slot label, reference 3
    # AND NONE OF THEM NAMES THE CASE.
    assert "runaway" not in STORY_SYSTEM.lower(), (
        "STORY_SYSTEM now names the runaway case, which turns one rule into a "
        "per-outcome switch — see Task 4.2's handover (4)"
    )


# ==========================================================================
# 8 — THE STANDING WITNESS FOR TASK 4.2's FIFTH-REFERENCE FINDING
#
# SEPARATELY LABELLED, AND IT GOES RED ON PURPOSE IF TASK 4.4 DECIDES TO
# EXCLUDE THE FACTS MESSAGE FROM {context}. Read the failure as the decision
# landing, not as a regression: delete this test, or invert it, and say which
# task did it.
# ==========================================================================


def test_the_facts_message_reaches_the_public_history_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``end_screen``'s fact sheet re-enters the story prompt through ``{context}``.

    Task 4.2 measured this and reported rather than fixed it, because the slot
    is Task 3.3's and the fabrication measurement is Task 4.4's. **This test is
    the witness that keeps the finding true rather than remembered**, and Task
    4.4's line carries a recommendation to EXCLUDE the message. If that lands,
    this test goes red by design.

    The chain, each link independently verifiable in source: ``end_screen``
    returns a **public** ``SystemMessage`` with no ``private_to``
    (``endgame.py:310``); ``graph.py:433`` is
    ``add_edge("end_screen", "moderator_story")``; ``_render_context`` keeps
    public ``SystemMessage``s and labels them ``Moderator``
    (``day.py:908-912``); and as the NEWEST message it survives both
    ``visible[-window:]`` and a token trim that pops the OLDEST line.

    WHY IT MATTERS, in one line, since the value of a witness is the defect it
    describes: move 1 tells the model the reader "does not need any of that
    again" while this slot hands it exactly those facts as the most recent
    thing anyone said — under the heading "What was said out loud, oldest
    first", text that was never said out loud during play. §2.6's own account
    of the flat summary is "handed a fact sheet … a model produces a
    chronological recap", and the fact sheet is in this prompt twice.

    The message is produced by calling ``end_screen`` for real, so this cannot
    pass against a fact sheet the game does not emit. Anti-vacuity: the same
    composition WITHOUT the message is checked not to carry the three facts
    headers on its own — asserted after the positive half, in that order.
    """
    base = _game_state(winner="runaway")
    with_facts = _slot_text(
        _full_prompt(monkeypatch, _with_facts_message(base)), "context"
    )

    # POSITIVE HALF: the fact sheet is in the public-history slot, labelled as
    # something the Moderator said out loud.
    assert f"Moderator: {ENDGAME_WINNER_RUNAWAY}" in with_facts
    assert ENDGAME_HEADER_ROSTER in with_facts
    assert ENDGAME_PERSONA_HEADER in with_facts
    # Including the cover-vs-true-self reveal, which is the part of the fact
    # sheet furthest from anything anyone said during play. Derived from
    # ``_persona_reveal_line`` rather than retyped, so a reword of
    # ``ENDGAME_PERSONA_MAFIA_TEMPLATE`` cannot leave a stale literal passing.
    mafioso = next(p for p in base["players"].values() if p.role == "mafia")
    reveal = _persona_reveal_line(mafioso)
    assert reveal is not None and "but was really" in reveal, (
        "the fixture's Mafioso has no cover-vs-true-self reveal to look for"
    )
    assert reveal in with_facts

    # AND THE SAME OUTCOME LINE IS IN TWO SLOTS AT ONCE — the fifth reference,
    # stated as the relation rather than as a count.
    full = _full_prompt(monkeypatch, _with_facts_message(base))
    assert ENDGAME_WINNER_RUNAWAY in _slot_text(full, "outcome")
    assert ENDGAME_WINNER_RUNAWAY in _slot_text(full, "context")

    # CONTROL: the slot says none of this on its own.
    without = _slot_text(_full_prompt(monkeypatch, base), "context")
    assert ENDGAME_WINNER_RUNAWAY not in without
    assert ENDGAME_HEADER_ROSTER not in without
    assert ENDGAME_PERSONA_HEADER not in without
