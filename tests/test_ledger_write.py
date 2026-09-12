"""Offline tests for the quality ledger's **write primitives** (spec 043, Slice 1).

Pins the three pieces Slice 1 added to ``src/graphia/tools/blunder_eval.py`` —
:func:`~graphia.tools.blunder_eval._split_ledger`,
:func:`~graphia.tools.blunder_eval._join_ledger` and
:func:`~graphia.tools.blunder_eval._atomic_write_text` — plus the rewritten
:func:`~graphia.tools.blunder_eval.append_record` that composes them. No model,
no network, no game: every test here is a string or a file in ``tmp_path``.

SLICE 3 GAVE ONE OF THOSE THREE A SECOND MODE, AND IT IS PINNED HERE TOO.
Spec 043 Task 3.8 added ``_atomic_write_text``'s ``verify`` hook — a caller's
predicate asked between the temp file's ``fsync`` and the ``os.replace``, whose
``False`` aborts the write and returns ``False`` rather than raising — plus the
:func:`~graphia.tools.blunder_eval._unlink_quietly` cleanup both of its
non-landing routes share. Section 5b is theirs. It stays in this module rather
than moving up to ``test_progressive_ledger_write.py`` for the same reason the
rest of section 5 is here: the hook is a predicate the primitive knows nothing
about, and what is asserted below is *when* it is asked and what a ``False``
leaves behind — not what a ledger means. The OCC cycle that supplies the
predicate is pinned one level up.

WHY A NEW MODULE RATHER THAN ``tests/test_blunder_eval.py``. That file is 2200
lines across six numbered concern sections its own docstring enumerates, all of
them about the *harness's* surface (CLI forcing, the repetition scorer, the
renderer's key order, streaming capture, transcript storage, cleanup). What is
tested here is a level below that: a text splitter, its inverse, and a durable
file replace, none of which know what a record is. The repo's precedent is
topic-scoped ledger files — ``test_ledger_model.py``, ``test_ledger_viewer.py``,
``test_substitution_ledger.py``, ``test_blunder_eval_provenance.py`` — and this
is one more of them.

ASSERTIONS ARE ON **BYTES**, NEVER ON PARSED VALUES. That is the whole point of
the design being tested: the write reads the ledger as opaque text and never
parses a record, precisely so that hand-written ``notes``, float renderings and
key order in the committed history cannot be reformatted by a later write. A
test that compared ``yaml.safe_load`` output would pass a writer that rewrote
all 50 committed records into canonical YAML — which is exactly the failure
being guarded. PyYAML appears here in only one role: as the **second opinion**
on how many documents a text holds, so the splitter can be shown to agree with
the parser every reader uses (section 2).

THE COMMITTED LEDGER IS READ, NEVER WRITTEN. ``evals/blunder-ledger.yaml`` is
the project's only long-term measurement history and an early-bound signature
default once leaked ~25 synthetic records into it. So the real file is opened
read-only, its bytes are copied into ``tmp_path``, and every write in this
module lands on the copy. There is no ``monkeypatch.setattr(LEDGER_PATH)`` here
because there is no code path here that resolves the global: every call passes
``ledger_path=`` explicitly, and the one test that reads the real file makes no
call that writes.

INVARIANTS OVER THE COMMITTED LEDGER, PLUS ONE LOWER-BOUND CANARY — and this is
a deliberate choice worth arguing, because Slice 1's task text asks for "all 50
pre-existing documents byte-identical and ``yaml.safe_load_all`` yields 51".
Those numbers were true when they were measured (2026-09-11: 50 records,
171,686 bytes). They are also **guaranteed to stop being true**: spec 043's own
remaining slices each perform real measured runs that append records, and every
future baseline does too. A test asserting ``== 50`` would go red on a correct
ledger for the rest of the project's life, and the reflex fix for a red test
that names a committed data file is to edit the number — which quietly retires
the coverage. So the assertions are the invariants those figures were an
instance of: the split round-trips byte-exactly, the chunk count agrees with
PyYAML's document count, every document chunk begins with the separator, and an
append leaves the *n* pre-existing documents byte-identical while
``safe_load_all`` yields *n+1*. On the ledger as committed today that test
asserts exactly 50 and 51, without being written in terms of them. The one
figure pinned as a literal is a **lower bound** (``>= 50`` records), which
catches a catastrophic truncation — the failure a canary is actually for —
while a ledger that grows stays green.

ONE TEST HERE DOCUMENTS A LANDMINE RATHER THAN A DESIRED OUTCOME, and says so in
its name: ``test_KNOWN_BAD_appending_to_a_ledger_with_no_trailing_newline_...``.
A ledger whose text does not end in a newline gets the next ``---`` written
mid-line, so the splitter sees one document where two were intended and
``yaml.safe_load_all`` raises. This is **pre-existing** — the append-mode writer
Slice 1 replaced produced byte-identical output for that input, which is why
Slice 1 was right not to repair it — and spec 043's Task 3.2 is where it gets
decided. The test exists so that fixing it shows up as a deliberate change to a
named test rather than as a surprise red.

WHAT IS DELIBERATELY NOT HERE. ``append_record``'s parent-directory creation is
already covered by ``tests/test_blunder_eval.py::
test_append_record_creates_parent_directory``, and the ``mkdir`` line's real job
— being the first attribute access, so a ``str`` argument dies naming
``parent`` — is pinned by ``tests/test_substitution_ledger.py::
test_the_injected_ledger_path_must_be_a_Path_not_a_str``. Neither is duplicated.
Nothing here tests the ``ended_early`` marker: that is Slice 4's, and it is
pinned in ``tests/test_early_stop_marker.py`` — landed 2026-09-12, after this
module was written, which is why the sentence above used to say it did not exist
yet. ``upsert_record``, the progressive per-game write and the pre-run backup
arrived the same way (Slice 3, 2026-09-12) and were listed among the not-yet-built
beside it. They are pinned in ``tests/test_progressive_ledger_write.py``, which sits
a level above these primitives for the same reason ``run.id`` does: it is about
*when* a record is written and what a write must not disturb, not about how text
is cut and replaced. ``run.id`` likewise arrived later — Slice 2, 2026-09-11 —
and is pinned in ``tests/test_run_identity.py`` rather than here: run identity
sits a level above the write primitives and spans three source modules
(``blunder_eval``, ``persona_bench``, ``eval_ledger``).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    _atomic_write_text,
    _join_ledger,
    _split_ledger,
    _unlink_quietly,
    append_record,
    render_record,
)

# ``tests/`` sits directly under the repo root — the locator
# ``tests/test_slice39_diary_measurement.py`` already uses for the same file.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMITTED_LEDGER = _REPO_ROOT / "evals" / "blunder-ledger.yaml"

# The record count the ledger had when spec 043 Slice 1 was written
# (2026-09-11). Asserted as a FLOOR, never as an equality — see the module
# docstring's argument for why.
_COMMITTED_RECORDS_FLOOR = 50


def _result() -> EvalResult:
    """A minimal ``EvalResult`` — just enough for ``render_record`` to emit a document.

    Deliberately thin. Nothing in this module cares what a record *says*; the
    renderer's content is ``tests/test_blunder_eval.py``'s subject. What matters
    here is that a real document (a few hundred bytes of multi-line YAML with
    quoted scalars) is what gets spliced, so the splice is exercised against the
    shape it meets in production rather than against ``"x\\n"``.
    """
    return EvalResult(
        provider="ollama",
        large_model="qwen2.5:3b",
        small_model="qwen2.5:3b",
        games_attempted=2,
        games_completed=2,
        metrics={"repetition": {"rate": 0.5, "count": 1, "denominator": 2}},
    )


# ===========================================================================
# The awkward-input table.
#
# One dict, shared by the round-trip test (section 1) and the byte-identity
# test (section 3), because both are TOTAL claims — "for every input" — and a
# shared table is what stops the two drifting into testing different inputs.
# Validity as YAML is NOT a precondition of either: the splitter is total over
# every string and the writer never parses, which is precisely why each is
# asserted over malformed inputs too.
# ===========================================================================

_AWKWARD_LEDGERS: dict[str, str] = {
    # A missing file reads as ``''`` in ``append_record``, so this is also the
    # first-use path.
    "empty": "",
    # Task 1.4's named case, and the landmine section 4 documents.
    "no_trailing_newline": "---\nrun:\n  date: '2026-09-11'",
    # No separator anywhere: the whole text is the preamble.
    "preamble_only": "# hand-written header\n# no records in this file yet\n",
    # The prospective hazard the separator rule is spelled for: an INDENTED
    # ``---`` inside a ``notes: |`` block scalar is content, not a boundary.
    "notes_block_scalar_with_indented_dashes": (
        "---\nrun:\n  date: '2026-09-11'\nnotes: |\n"
        "  first line of the note\n  ---\n  third line of the note\n"
    ),
    # The rejected "bare sequence anywhere" rule would cut this note in half.
    "notes_dashes_midline": (
        "---\nrun:\n  date: '2026-09-11'\nnotes: 'rate fell 0.4 --- 0.1'\n"
    ),
    # A separator that is NOT byte-equal to what this module writes. This is the
    # input that makes ``_join_ledger``'s "preserve, never synthesise" contract
    # observable: on a ledger whose separators are all plain ``---\n``, a join
    # that re-emitted ``"---\n"`` would be indistinguishable from the real one.
    "hand_edited_separator": "--- # record 51, kept by hand\nrun:\n  date: 'x'\n",
    # A CRLF-edited ledger. Both the splitter's trailing character class and
    # ``append_record``'s read-bytes-and-decode exist for this file.
    "crlf": "---\r\nrun:\r\n  date: 'x'\r\n---\r\nrun:\r\n  date: 'y'\r\n",
    # Four dashes at a line start: a plain scalar in YAML, not a marker. Not
    # valid YAML in this position, and it does not need to be — nothing in the
    # write path parses.
    "four_dashes_line": "---\nrun:\n  date: 'x'\n----\n",
    # A separator as the final bytes, with no newline after it.
    "separator_at_end_of_text": "---\nrun:\n  date: 'x'\n---",
    # The ordinary two-record shape — the control, without which a green table
    # could be green on nothing but edge cases.
    "two_documents": "---\nrun:\n  date: 'x'\n---\nrun:\n  date: 'y'\n",
}

_AWKWARD_CASES = pytest.mark.parametrize(
    "text",
    [pytest.param(text, id=name) for name, text in _AWKWARD_LEDGERS.items()],
)


# ===========================================================================
# 1. The round-trip identity: ``_join_ledger(_split_ledger(t)) == t``
# ===========================================================================


@_AWKWARD_CASES
def test_the_split_join_round_trip_is_byte_exact(text: str) -> None:
    """``_join_ledger(_split_ledger(t)) == t`` — for every input, including the awkward ones.

    The identity the whole write path rests on: if it fails, an ``os.replace``
    lands mangled bytes with full atomicity and a clean read-back. Asserted on
    the string rather than on a parse, because the failure being guarded is a
    reformat that a parse would agree with.
    """
    assert _join_ledger(_split_ledger(text)) == text


def test_the_committed_ledger_round_trips_byte_exactly() -> None:
    """The identity holds on the real 50-record history — the input that matters.

    Read-only: the committed ledger is the one file in this module that is not
    a copy, and nothing here writes to it.
    """
    text = _COMMITTED_LEDGER.read_bytes().decode("utf-8")

    assert _join_ledger(_split_ledger(text)) == text


@_AWKWARD_CASES
def test_every_chunk_after_the_preamble_begins_with_a_separator(text: str) -> None:
    """Element 0 is the preamble; elements 1.. each carry their own ``---``.

    The structural half of the contract, and what makes the splice an index
    operation with no separator bookkeeping: a caller can replace
    ``chunks[i]`` wholesale.
    """
    chunks = _split_ledger(text)

    assert all(chunk.startswith("---") for chunk in chunks[1:])
    # And the preamble holds no boundary — otherwise "the documents are
    # ``chunks[1:]``" would be false.
    assert not chunks[0].startswith("---")


def test_the_join_preserves_a_hand_edited_separator_rather_than_synthesising_one() -> (
    None
):
    """A ``--- # comment`` separator survives the round trip verbatim.

    The direct form of what the round-trip table proves indirectly, kept as its
    own test because it names the mechanism: ``_split_ledger`` leaves each
    separator attached to the chunk it introduces, so ``_join_ledger`` has
    nothing to synthesise. A join that emitted ``"---\\n"`` per chunk would pass
    every assertion made against the committed ledger — whose 50 separators are
    all byte-equal to ``"---\\n"`` — and silently normalise a hand-edited one.
    """
    text = _AWKWARD_LEDGERS["hand_edited_separator"]

    chunks = _split_ledger(text)

    assert len(chunks) == 2
    assert chunks[1].startswith("--- # record 51, kept by hand\n")
    assert _join_ledger(chunks) == text


# ===========================================================================
# 2. The separator rule, and its agreement with PyYAML
#
# ``_LEDGER_SEPARATOR_RE`` matches ``---`` at the start of a line followed by
# end-of-text or a whitespace character. Two properties of that spelling are
# deliberate, argued in the module's section comment, and invisible to the
# round-trip identity above (which holds for ANY cut points, because the cuts
# partition the text). They are tested here because they are exactly what a
# later "simplification" to the bare sequence would remove.
# ===========================================================================


@pytest.mark.parametrize(
    ("text", "documents", "why"),
    [
        pytest.param(
            "---\nrun: {}\n----\n",
            1,
            "four dashes is a plain YAML scalar, not a document marker",
            id="four_dashes_is_not_a_boundary",
        ),
        pytest.param(
            "---\nnotes: 'rate fell 0.4 --- 0.1'\n",
            1,
            "the sequence mid-line is note text, and cutting there would "
            "produce two chunks neither of which is a document",
            id="mid_line_is_not_a_boundary",
        ),
        pytest.param(
            "---\nrun: {}\n---not-a-marker\n",
            1,
            "the trailing lookahead requires a separation character",
            id="dashes_glued_to_content_is_not_a_boundary",
        ),
        pytest.param(
            "---\r\nrun: {}\r\n---\r\nrun: {}\r\n",
            2,
            "a CRLF-edited ledger must still split, which is why the trailing "
            "lookahead is an explicit class including \\r and not ``$``",
            id="crlf_separators_do_split",
        ),
        pytest.param(
            "---\nrun: {}\n---",
            2,
            "a separator as the final bytes is a boundary — the ``\\Z`` arm",
            id="separator_at_end_of_text_does_split",
        ),
        pytest.param(
            "--- # record 51\nrun: {}\n",
            1,
            "a trailing comment is a separation character away, so it splits",
            id="separator_with_a_trailing_comment_does_split",
        ),
    ],
)
def test_the_separator_rule_finds_exactly_the_boundaries_it_should(
    text: str, documents: int, why: str
) -> None:
    """``len(_split_ledger(t)) - 1`` is the document count, with no special cases."""
    assert len(_split_ledger(text)) - 1 == documents, why


@pytest.mark.parametrize(
    ("text", "documents"),
    [
        # An INDENTED ``---`` inside a block scalar is content: one document.
        pytest.param(
            "---\nnotes: |\n  first line\n  ---\n  third line\n",
            1,
            id="indented_dashes_in_a_block_scalar_are_content",
        ),
        # An UNINDENTED one terminates the block scalar and IS a document
        # break — the ambiguity belongs to the YAML format, not to the
        # splitter, and the splitter takes the parser's side of it.
        pytest.param(
            "---\nnotes: |\n  first line\n---\nnotes: 'second record'\n",
            2,
            id="unindented_dashes_after_a_block_scalar_are_a_break",
        ),
        pytest.param(
            "---\nnotes: 'rate fell 0.4 --- 0.1'\n",
            1,
            id="mid_line_dashes_in_a_quoted_scalar_are_content",
        ),
        pytest.param(
            "---\r\nrun:\r\n  date: 'x'\r\n---\r\nrun:\r\n  date: 'y'\r\n",
            2,
            id="crlf_documents",
        ),
        pytest.param(
            "--- # record 51\nrun:\n  date: 'x'\n",
            1,
            id="separator_with_a_trailing_comment",
        ),
        pytest.param(
            "---\nrun:\n  date: 'x'\n---",
            2,
            id="separator_at_end_of_text",
        ),
    ],
)
def test_the_splitter_and_pyyaml_agree_on_the_document_count(
    text: str, documents: int
) -> None:
    """The splitter's chunk count equals ``yaml.safe_load_all``'s document count.

    This agreement is the splitter's whole justification. The splice swaps a
    *document*; every reader of the ledger — ``load_ledger``, the viewer, the
    ad-hoc scripts — finds its documents through PyYAML. If the two disagreed
    about where a record begins, the splice could replace a fragment of one
    document and the write would still be atomic and still read back clean.

    Both counts are asserted in the same test on purpose: asserting only the
    splitter would pin the implementation, and asserting only PyYAML would test
    PyYAML.

    SCOPE, stated because it is narrower than it looks: the claim is made over
    text in the ledger's canonical shape — every document introduced by an
    explicit ``---``. It is NOT true in general, and does not need to be: a
    bare ``a: 1\\n`` is one document to PyYAML and zero to the splitter, because
    the splitter counts *separated* documents. Every record this project has
    ever written is separator-led (verified on all 50 committed records, and
    ``render_record`` cannot emit a leading separator — see
    ``test_render_record_has_no_leading_document_separator``).
    """
    assert text.startswith("---"), "this test's scope is separator-led text"

    assert len(_split_ledger(text)) - 1 == documents
    assert len(list(yaml.safe_load_all(text))) == documents


def test_the_committed_ledgers_chunk_count_matches_pyyamls_document_count() -> None:
    """Splitter and parser agree on the real history: *n* chunks, *n* documents.

    The canary for a separator-rule change: any loosening or tightening that
    disagreed with PyYAML about the committed file would land here. Read-only.
    """
    text = _COMMITTED_LEDGER.read_bytes().decode("utf-8")

    chunks = _split_ledger(text)

    assert len(chunks) - 1 == len(list(yaml.safe_load_all(text)))
    # The committed ledger begins immediately with ``---\n``, so its preamble
    # is empty — the property that makes ``chunks[1:]`` the whole file.
    assert chunks[0] == ""
    assert all(chunk.startswith("---\n") for chunk in chunks[1:])


def test_the_committed_ledger_has_not_shrunk() -> None:
    """A lower-bound canary: at least the 50 records that were there on 2026-09-11.

    Deliberately a floor and not an equality. The ledger is appended to by every
    real measured run, so ``== 50`` would go red on a correct ledger and invite
    the number to be edited — retiring the coverage. A floor catches the failure
    a canary is for (a truncation, a splice bug eating documents) and stays
    green as the history grows.
    """
    text = _COMMITTED_LEDGER.read_bytes().decode("utf-8")

    assert len(_split_ledger(text)) - 1 >= _COMMITTED_RECORDS_FLOOR


# ===========================================================================
# 3. ``append_record``: the bytes it writes
# ===========================================================================


@_AWKWARD_CASES
def test_append_record_is_plain_concatenation_for_every_input(
    text: str, tmp_path: Path
) -> None:
    """The written bytes are ``existing + b"---\\n" + document``, always.

    Slice 1's headline claim — "no change to what it writes" — in executable
    form. The replaced implementation opened the file in append mode and wrote
    those two pieces, so matching this expression for every input IS matching
    the old writer byte for byte, including for the inputs nobody would choose
    (no trailing newline, CRLF, a hand-edited separator, malformed YAML).

    It is also the strongest single assertion available against a splice bug:
    it fixes every byte of the output rather than sampling it.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    before = text.encode("utf-8")
    ledger.write_bytes(before)
    result = _result()
    document = render_record(result, "2026-09-11")

    append_record(result, "2026-09-11", ledger_path=ledger)

    assert ledger.read_bytes() == before + b"---\n" + document.encode("utf-8")


def test_append_record_onto_a_missing_file_creates_a_separator_led_ledger(
    tmp_path: Path,
) -> None:
    """First use: the file does not exist, and the record written is ``---``-led."""
    ledger = tmp_path / "blunder-ledger.yaml"
    assert not ledger.exists()
    result = _result()
    document = render_record(result, "2026-09-11")

    written = append_record(result, "2026-09-11", ledger_path=ledger)

    assert written == ledger
    assert ledger.read_bytes() == b"---\n" + document.encode("utf-8")
    assert len(list(yaml.safe_load_all(ledger.read_text(encoding="utf-8")))) == 1


def test_append_record_onto_an_empty_file_matches_the_missing_file_case(
    tmp_path: Path,
) -> None:
    """An existing but empty ledger and a missing one produce identical bytes.

    Both read as ``''``. Pinned because the two are separate branches in
    ``append_record`` (``exists()`` decides), and a divergence between them
    would show up as a stray leading newline nobody would look for.
    """
    missing = tmp_path / "missing.yaml"
    empty = tmp_path / "empty.yaml"
    empty.write_bytes(b"")
    result = _result()

    append_record(result, "2026-09-11", ledger_path=missing)
    append_record(result, "2026-09-11", ledger_path=empty)

    assert empty.read_bytes() == missing.read_bytes()


def test_append_record_onto_a_copy_of_the_committed_ledger_leaves_every_document_intact(
    tmp_path: Path,
) -> None:
    """The regression that protects the project's only measurement history.

    A copy of the real ``evals/blunder-ledger.yaml`` is appended to, and every
    pre-existing document must come out **byte-identical** — asserted chunk by
    chunk, so a reformat of record 17 alone is caught and named, not just a
    change in total length. On the ledger as committed today this asserts 50
    documents byte-identical and ``yaml.safe_load_all`` yielding 51; both
    figures are derived from the file rather than written as literals, for the
    reason the module docstring argues.

    The real file is READ here and written nowhere: the append lands on the
    ``tmp_path`` copy.
    """
    before = _COMMITTED_LEDGER.read_bytes()
    before_chunks = _split_ledger(before.decode("utf-8"))
    records = len(before_chunks) - 1
    ledger = tmp_path / "blunder-ledger.yaml"
    ledger.write_bytes(before)

    append_record(_result(), "2026-09-11", ledger_path=ledger)

    after = ledger.read_bytes()
    after_chunks = _split_ledger(after.decode("utf-8"))
    # Every pre-existing chunk, byte for byte, in place.
    assert after_chunks[: records + 1] == before_chunks
    # Exactly one more document, and it is at the end.
    assert len(after_chunks) == records + 2
    assert after.startswith(before)
    # And the result is still a well-formed multi-document YAML file with one
    # more record in it — the parser's agreement, on the real history.
    assert len(list(yaml.safe_load_all(after.decode("utf-8")))) == records + 1


def test_append_record_does_not_translate_a_crlf_ledgers_line_endings(
    tmp_path: Path,
) -> None:
    """A CRLF ledger keeps its CRLFs — the read-bytes-and-decode guard.

    ``append_record`` reads ``read_bytes().decode("utf-8")``, not
    ``read_text()``, and ``_atomic_write_text`` writes with ``newline=""``.
    Swap either for the text-mode default and every ``\\r\\n`` in the file
    becomes ``\\n`` on the first append — a whole-file reformat, invisible in a
    diff viewer that normalises line endings, on the one file the project cannot
    reconstruct.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    before = b"---\r\nrun:\r\n  date: 'x'\r\n"
    ledger.write_bytes(before)
    assert before.count(b"\r\n") == 3

    append_record(_result(), "2026-09-11", ledger_path=ledger)

    after = ledger.read_bytes()
    assert after.startswith(before)
    # The three that were there are still there. ``read_text()`` would give 0:
    # the existing endings are translated on the way in and never restored.
    assert after.count(b"\r\n") == 3


# ===========================================================================
# 4. The known-bad input, documented as such
# ===========================================================================


def test_KNOWN_BAD_appending_to_a_ledger_with_no_trailing_newline_corrupts_it(
    tmp_path: Path,
) -> None:
    """TODAY'S BEHAVIOUR ON A KNOWN-BAD INPUT — not a desired outcome.

    A ledger whose text does not end in a newline gets the next ``---`` written
    onto the end of its last line, so the file holds ``date: 'x'---`` and the
    boundary is not at a line start any more. Two consequences, both asserted:
    the splitter sees ONE document where two were intended, and
    ``yaml.safe_load_all`` refuses the file.

    **This is pre-existing, not a Slice 1 regression.** The append-mode writer
    that ``append_record`` replaced produced byte-identical output for this
    input — it also just concatenated — which is why Slice 1 was right not to
    repair it, and why
    ``test_append_record_is_plain_concatenation_for_every_input`` covers this
    case as a byte-identity claim rather than as a defect.

    **Spec 043 Task 3.2 decided it, on 2026-09-12, and decided it for the OTHER
    writer only.** The OCC cycle's read-back step (*exactly one chunk carries
    this run's id*) can never succeed on such a file, so a progressive write
    would have burned its whole retry budget on its first attempt and diverted a
    good record to the fallback file, for a ledger one byte would have repaired.
    Of the two exits — normalising the missing newline before splicing, or
    teaching the read-back to tell *malformed* from *conflicted* — Task 3.2 took
    the repair, and put it in ``_splice_own_chunk``: that path is already a
    whole-file rewrite, so the byte is free, and the fix is convergent. It was
    deliberately NOT put in ``append_record``, whose byte-identity with the
    pre-ADR-014 append mode is the measured property Slice 1 Task 1.5 asserted
    over a 38-case matrix that includes precisely this input. So the two writers
    differ here on purpose — one is frozen against history, the other repairs a
    file it is about to rewrite anyway — and this test keeps standing as the
    frozen half's behaviour. The repaired half is pinned by
    ``tests/test_progressive_ledger_write.py::
    test_a_ledger_with_no_trailing_newline_is_repaired_rather_than_retried``.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    ledger.write_bytes(b"---\nrun:\n  date: 'x'")  # no trailing newline

    append_record(_result(), "2026-09-11", ledger_path=ledger)

    text = ledger.read_text(encoding="utf-8")
    assert "date: 'x'---\n" in text, "the separator landed mid-line, as documented"
    # Two records were intended; the splitter can only see one boundary.
    assert len(_split_ledger(text)) - 1 == 1
    # And no reader can parse the result at all.
    with pytest.raises(yaml.YAMLError):
        list(yaml.safe_load_all(text))


# ===========================================================================
# 5. ``_atomic_write_text``: what it leaves behind, and what it never leaves
# ===========================================================================


def test_a_successful_write_leaves_no_temp_file_beside_the_ledger(
    tmp_path: Path,
) -> None:
    """After two appends the directory holds the ledger and nothing else.

    Litter beside a committed data file is indistinguishable, to a human and in
    ``git status``, from a ledger that has gone wrong.
    """
    ledger_dir = tmp_path / "evals"
    ledger = ledger_dir / "blunder-ledger.yaml"

    append_record(_result(), "2026-09-11", ledger_path=ledger)
    append_record(_result(), "2026-09-12", ledger_path=ledger)

    assert [path.name for path in ledger_dir.iterdir()] == [ledger.name]


def test_a_failed_replace_leaves_no_temp_file_and_no_damage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` raising leaves the ledger exactly as it was, and no ``.tmp``.

    The failure is injected at the last possible moment — after the temp file
    has been written and ``fsync``-ed — which is the only point at which a
    naive implementation has litter to leave.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"
    before = b"---\nrun:\n  date: 'x'\n"
    ledger.write_bytes(before)

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("injected: replace failed")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(OSError, match="injected: replace failed"):
        append_record(_result(), "2026-09-11", ledger_path=ledger)

    assert ledger.read_bytes() == before
    assert [path.name for path in ledger_dir.iterdir()] == [ledger.name]


def test_a_failed_fsync_raises_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing ``fsync`` is reported, not swallowed — and still leaves no litter.

    Durability is the one thing ``_atomic_write_text`` exists to promise, so a
    failure to achieve it must be loud. Pinned separately from the replace case
    because it fires *inside* the ``with`` block, on a path where the file
    handle is still open — the arrangement most likely to be got wrong by a
    later edit.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("injected: fsync failed")

    monkeypatch.setattr(os, "fsync", _boom)

    with pytest.raises(OSError, match="injected: fsync failed"):
        _atomic_write_text(ledger, "---\nrun: {}\n")

    assert not ledger.exists()
    assert list(ledger_dir.iterdir()) == []


def test_an_existing_targets_permission_bits_are_carried_across(
    tmp_path: Path,
) -> None:
    """A ``0640`` ledger is still ``0640`` after a write — not ``mkstemp``'s ``0600``.

    ``tempfile.mkstemp`` creates at ``0600`` and ``os.replace`` moves that inode
    into place, so without the explicit carry the committed ledger would be
    silently tightened from ``0644`` on its first write — invisibly to ``git``,
    which records only the executable bit.

    ``0640`` rather than ``0644`` on purpose: ``0644`` is also what a plain
    ``open(path, "w")`` produces under the usual ``022`` umask, so a green test
    on ``0644`` could be a coincidence of the umask rather than evidence of the
    carry. ``0640`` is nobody's default.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    ledger.write_bytes(b"---\nrun:\n  date: 'x'\n")
    ledger.chmod(0o640)

    append_record(_result(), "2026-09-11", ledger_path=ledger)

    assert stat.S_IMODE(ledger.stat().st_mode) == 0o640


def test_a_ledger_that_did_not_exist_is_created_private_rather_than_umask_default(
    tmp_path: Path,
) -> None:
    """No target, no mode to carry: the file keeps ``mkstemp``'s ``0600``.

    The documented other half of the carry, and the documented direction of the
    trade — stricter rather than looser, and only for a ledger nobody has
    created yet. Pinned so that "stricter" is a decision on record rather than
    an accident somebody later relaxes to the umask default without noticing.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    assert not ledger.exists()

    append_record(_result(), "2026-09-11", ledger_path=ledger)

    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_the_temp_file_is_named_after_the_target_and_hidden_from_a_yaml_glob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file sits in the target's directory, prefixed with its name, suffixed ``.tmp``.

    Two properties, both load-bearing and neither visible after a successful
    write — which is why the replace is blocked to observe them. The directory
    is what makes ``os.replace`` atomic at all (across a filesystem it degrades
    to an interruptible copy). The ``.tmp`` suffix keeps a mid-write temp file
    out of the ``*.yaml`` globs the ad-hoc ``yaml.safe_load_all`` scripts run
    against this directory — a temp file those picked up would read as a
    duplicate half-written record.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"
    seen: list[str] = []

    def _capture(src: object, dst: object) -> None:
        seen.append(str(src))
        raise OSError("injected: stop before the replace")

    monkeypatch.setattr(os, "replace", _capture)

    with pytest.raises(OSError, match="injected"):
        _atomic_write_text(ledger, "---\nrun: {}\n")

    assert len(seen) == 1
    temp = Path(seen[0])
    assert temp.parent == ledger_dir
    assert temp.name.startswith("blunder-ledger.yaml.")
    assert temp.suffix == ".tmp"
    assert not list(ledger_dir.glob("*.yaml"))


def test_atomic_write_text_creates_a_missing_parent_directory(tmp_path: Path) -> None:
    """The primitive creates ``evals/`` itself, so it is usable without a caller's ``mkdir``.

    ``append_record`` keeps its own ``mkdir`` for an unrelated reason (it is the
    first attribute access, so a ``str`` argument dies naming ``parent`` —
    pinned in ``tests/test_substitution_ledger.py``). This asserts the
    primitive does not depend on that caller doing it.
    """
    target = tmp_path / "deep" / "nested" / "ledger.yaml"

    _atomic_write_text(target, "---\nrun: {}\n")

    assert target.read_bytes() == b"---\nrun: {}\n"


def test_atomic_write_text_writes_the_string_byte_for_byte(tmp_path: Path) -> None:
    """No line-ending translation, no trailing newline added, no encoding surprise.

    ``newline=""`` is what makes the caller's bytes the file's bytes, and the
    byte-identity claim for a CRLF ledger rests on it. Asserted directly on the
    primitive as well as through ``append_record`` so a regression names the
    right function.
    """
    target = tmp_path / "ledger.yaml"
    text = "---\r\nrun:\r\n  date: 'x'\r\nnotes: 'ends without a newline'"

    _atomic_write_text(target, text)

    assert target.read_bytes() == text.encode("utf-8")


# ===========================================================================
# 5b. ``verify``: the last-instant abort hook (spec 043 Task 3.8a).
#
# The hook exists to move ONE question — the caller's optimistic version
# check — from before ``_atomic_write_text`` is entered to the instant before
# its ``os.replace``. Everything it buys is in that relocation: with the check
# outside, the span between the last look at the file and the rename contained
# a whole temp-file write plus an ``fsync`` (measured at 466 us median over the
# committed ledger); asked here it is the check plus the rename (215 us).
#
# SO THE ORDERING TEST BELOW IS THE ONE THAT CARRIES THE CHANGE. A test that
# only asserted "verify was called" would pass against an implementation that
# called it before the temp file was written — which restores the whole window
# while every other assertion in this section stays green.
# ===========================================================================


def test_a_verify_that_returns_false_aborts_the_write_and_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    """``False`` means: do not land these bytes. The target and the directory prove it.

    Three separable claims, and each has its own way of regressing. The return
    is ``False`` — the caller distinguishes an abort from a landed write only by
    that value, and an abort reported as success would let the OCC cycle move on
    to a read-back it has no business passing. The target is byte-identical —
    an abort that had already replaced would be no abort at all. And the temp
    file is gone — the abort route is the *second* way out of this function that
    leaves a temp file behind, and it does not go through the ``except`` block
    that cleans up the first.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"
    before = b"---\nrun:\n  date: 'x'\n"
    ledger.write_bytes(before)

    landed = _atomic_write_text(ledger, "---\nrun:\n  date: 'y'\n", verify=lambda: False)

    assert landed is False, "an aborted write must report itself as one"
    assert ledger.read_bytes() == before, "an aborted write must not have landed"
    assert [path.name for path in ledger_dir.iterdir()] == [ledger.name], (
        "the abort route must clean its temp file up, exactly as the raise route does"
    )


def test_a_verify_that_returns_true_lets_the_replace_happen(tmp_path: Path) -> None:
    """``True`` is the ordinary path: the new bytes land and ``True`` comes back.

    The control for the abort test. Without it, an implementation that aborted
    unconditionally — never calling ``os.replace`` at all — would satisfy every
    assertion about what an abort leaves behind.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"
    ledger.write_bytes(b"---\nrun:\n  date: 'x'\n")
    new = "---\nrun:\n  date: 'y'\n"

    landed = _atomic_write_text(ledger, new, verify=lambda: True)

    assert landed is True
    assert ledger.read_bytes() == new.encode("utf-8")
    assert [path.name for path in ledger_dir.iterdir()] == [ledger.name]


def test_a_write_with_no_verify_returns_true_so_ignoring_the_return_is_correct(
    tmp_path: Path,
) -> None:
    """With no hook there is nothing that can abort, so the return is always ``True``.

    This is what keeps ``append_record`` — a one-shot write with no version
    token and nothing to re-check — unchanged by Task 3.8, and it is why every
    other call site in the module may discard the return value. If this ever
    returned ``None`` or ``False`` on the default path, a caller written against
    the documented contract (*"a caller that ignores it is correct"*) would start
    silently treating a successful write as a failed one.
    """
    target = tmp_path / "ledger.yaml"

    assert _atomic_write_text(target, "---\nrun: {}\n") is True
    assert target.read_bytes() == b"---\nrun: {}\n"


def test_verify_runs_once_after_the_temp_files_fsync_and_before_the_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ORDERING. ``fsync`` → ``verify`` → ``replace``, once, with the bytes already durable.

    This is the whole substance of Task 3.8a and the only test in this section
    that can fail on it. The hook's entire value is *where* it is asked: move
    the call one statement earlier — before the temp file is written — and the
    function still calls ``verify``, still honours its ``False``, still cleans
    up and still returns the right value, while the window it was added to close
    is back to its full width. Nothing but the order says so.

    Four observations, taken from inside the hook so they describe the moment it
    runs rather than the outcome afterwards:

    - the temp file's ``fsync`` has already happened (``events[:3]``);
    - the temp file exists and holds **the new bytes in full** — so the work the
      hook is supposed to be standing after is genuinely done, which an earlier
      call site would show as a missing or empty temp file;
    - the target still holds the **old** bytes, so the replace has not happened;
    - it is asked exactly **once** per call, not once per retry-ish internal loop.

    ``os.fsync`` fires twice per successful write — the temp file's, then the
    parent directory's after the rename — which is why the assertion is on the
    first three events rather than on the whole list.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"
    before = b"---\nrun:\n  date: 'x'\n"
    ledger.write_bytes(before)
    new = "---\nrun:\n  date: 'y'\n"

    events: list[str] = []
    temps_at_verify: list[list[bytes]] = []
    target_at_verify: list[bytes] = []
    real_fsync, real_replace = os.fsync, os.replace

    def _traced_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def _traced_replace(src: object, dst: object) -> None:
        events.append("replace")
        real_replace(src, dst)  # type: ignore[arg-type]

    def _verify() -> bool:
        events.append("verify")
        temps_at_verify.append(sorted(p.read_bytes() for p in ledger_dir.glob("*.tmp")))
        target_at_verify.append(ledger.read_bytes())
        return True

    monkeypatch.setattr(os, "fsync", _traced_fsync)
    monkeypatch.setattr(os, "replace", _traced_replace)

    assert _atomic_write_text(ledger, new, verify=_verify) is True

    assert events[:3] == ["fsync", "verify", "replace"], (
        "verify must be asked AFTER the temp file is durable and BEFORE the "
        f"rename — the window it exists to close is the rename alone; saw {events}"
    )
    assert events.count("verify") == 1, "the hook is a question, not a poll"
    assert temps_at_verify == [[new.encode("utf-8")]], (
        "at the instant verify is asked there must be exactly one temp file and "
        "it must already hold the complete new bytes"
    )
    assert target_at_verify == [before], "the replace must not have happened yet"
    assert ledger.read_bytes() == new.encode("utf-8")


def test_a_verify_that_raises_propagates_with_the_temp_file_cleaned_up(
    tmp_path: Path,
) -> None:
    """An exception from the hook is not a conflict, and is not swallowed.

    The distinction the docstring draws and the one a later edit is most likely
    to erase by wrapping the call in a ``try``: ``False`` means *the caller's
    precondition stopped holding*, which under ADR 014 is an expected outcome
    and therefore a return value; an ``OSError`` means *the ledger could not be
    read at all*, which no retry repairs and which must reach the caller as the
    ``OSError`` it is. Caught-and-converted-to-``False`` would send an
    unreadable ledger around the retry loop until the budget was spent.

    The temp file is still cleaned up: the raise route out of this function was
    always litter-free and the hook must not make it otherwise.
    """
    ledger_dir = tmp_path / "evals"
    ledger_dir.mkdir()
    ledger = ledger_dir / "blunder-ledger.yaml"
    before = b"---\nrun:\n  date: 'x'\n"
    ledger.write_bytes(before)

    def _boom() -> bool:
        raise OSError("injected: the ledger could not be re-read")

    with pytest.raises(OSError, match="injected: the ledger could not be re-read"):
        _atomic_write_text(ledger, "---\nrun:\n  date: 'y'\n", verify=_boom)

    assert ledger.read_bytes() == before
    assert [path.name for path in ledger_dir.iterdir()] == [ledger.name]


def test_unlink_quietly_removes_what_it_can_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared cleanup: it removes the file, and its own failure is never news.

    Three arrangements, one contract. It removes an existing file — without
    which both non-landing routes out of ``_atomic_write_text`` would leave a
    ``.tmp`` beside the ledger, indistinguishable in ``git status`` from a
    ledger that has gone wrong. A file that is already gone is not an error
    (``FileNotFoundError`` is an ``OSError``). And a removal that genuinely
    fails is swallowed, which is the property with a real argument behind it and
    the one a later edit would remove as defensive clutter: on the raise route a
    raise here would **mask** the exception that brought us to it, and on the
    abort route it would escalate a routine lost race — which the caller repairs
    by retrying — into an ``OSError`` that goes straight to the fallback file.
    """
    doomed = tmp_path / "blunder-ledger.yaml.xyz.tmp"
    doomed.write_bytes(b"half a record")

    _unlink_quietly(doomed)
    assert not doomed.exists(), "the ordinary job: the temp file goes away"

    _unlink_quietly(doomed)  # already gone — must not raise

    def _boom(path: object) -> None:
        raise PermissionError("injected: the directory is read-only")

    survivor = tmp_path / "survivor.tmp"
    survivor.write_bytes(b"x")
    monkeypatch.setattr(os, "unlink", _boom)

    _unlink_quietly(survivor)  # must not raise

    assert survivor.exists(), "the arrangement proves nothing if the unlink worked"


def test_append_record_still_resolves_the_module_global_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rewrite kept the call-time ``LEDGER_PATH`` resolution the suite depends on.

    Slice 1 replaced ``append_record``'s body wholesale, and this is the seam
    that an early-bound signature default once broke by leaking ~25 synthetic
    records into the committed ledger. The standing guard lives in
    ``tests/test_substitution_ledger.py::
    test_append_record_resolves_the_module_global_at_call_time``; this is the
    same property asserted against the *rewritten* writer, so a future change
    to the write path meets it here rather than in an unrelated module.
    """
    redirected = tmp_path / "redirected" / "blunder-ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", redirected)

    written = append_record(_result(), "2026-09-11")

    assert written == redirected
    assert redirected.read_bytes().startswith(b"---\n")
