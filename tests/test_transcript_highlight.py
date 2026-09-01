"""Tests for the pure transcript tokenizer (spec 038, Slices 1-5).

Two halves, in the order the slice was built:

1. **The corpus round-trip property test** (Slice 1, task 1 — written before the
   tokenizer existed), which sweeps every committed game;
2. **The tokenizer's synthetic unit tests** (Slice 1, task 4; extended by
   Slice 2, task 3 and Slice 3, task 3), which pin the kind each recognised
   shape gets, on inputs small enough to read.

The matching **widget-level** half of task 4 — that the painted body's spans
survive, that its plain text is the file's text exactly, that ``[bold]`` renders
literally, and that scrolling and the back-out keys still behave as before —
lives in ``tests/test_ledger_viewer.py`` section **B14**, next to that file's
``App.run_test()`` harness and the ``TranscriptScreen`` helpers it already owns.
This module stays terminal-free.

--- 1. The corpus round-trip property test ---

**Written before the tokenizer exists.** This file *is* the specification that
``graphia.eval_ledger.tokenize_transcript`` must satisfy: it asserts, over
**every** preserved game committed under ``evals/transcripts/``, that the
tokenizer's spans concatenate back to the file's exact text ::

    "".join(text for text, _ in spans) == original

That single property is what makes functional-spec §2's *"the stored game is
never altered"* structurally true rather than merely intended. Colour is applied
to the **display** only, so a dropped, duplicated or reordered character would
change what a reviewer reads while being invisible against a 30 KB wall of text.
Every later slice of spec 038 adds kinds (``attr``, ``field-label``, ``speaker``,
``speech``, ``thought``, ``recap``, the side-bearing kinds, the human-seat bold);
**every one of them must leave this test passing**, which is why it is the first
task of the spec and why its failure message — not its pass — is the deliverable.

**Why the whole corpus and not a fixture.** The corpus is 298 real files in two
formats (268 spec-022 ``<player name=… role=…>``, 30 pre-022 indented
``Name — Role``) covering element-presence combinations no hand-written fixture
would think to include: games with no ``<vote>``, games with no ``<thought>``,
games with no ``<player>`` tag at all. Sweeping it costs a few hundred
milliseconds and needs no fixture authoring. A tokenizer that assumes presence
passes its synthetic unit tests and breaks on real data — that is exactly the
gap this test closes.

**Read-only, and offline.** Transcripts are curated, committed artifacts
(spec 017's commit-or-delete contract); nothing here writes, moves or deletes
anything, and ``evals/blunder-ledger.yaml`` is never touched. No model, no
network, no AWS: ``eval_ledger`` is the pure Textual-free layer, so the autouse
``safe_llm`` net in ``conftest.py`` is simply irrelevant here — as it is for
``tests/test_ledger_model.py``. The real production symbol is used (never a local
re-implementation), so a rename breaks these tests; see :func:`_tokenize` for why
it is resolved at call time rather than imported at module level.

**Which kind a shape gets is mostly the unit tests' job.** The per-run-dir sweep
pins only what is structural and permanent — total coverage of the text, no empty
spans, a non-empty semantic name on every span — because those hold whatever
kinds a later slice adds. Pinning *which* kind a given shape gets happens on
synthetic inputs a reader can check by eye (half 2).

**Slice 2 added one narrow kind-shaped corpus assertion** and no more. ``attr``
and ``field-label`` are the first kinds that split a *line* rather than claim a
whole one, so their boundaries — the value without its quotes, the label with its
colon, neither ever straddling a newline — are the first thing 9.4 MB of real
model-generated prose can contradict that a hand-written fixture never would.
See :func:`test_the_line_splitting_kinds_hold_their_shape_across_the_corpus`.

**Slice 3 widened that one assertion to its four new kinds** — ``speaker`` /
``speech`` split a line the same way ``field-label`` does, and ``thought`` /
``recap`` claim an inline body between two tags — and added nothing else to the
sweep. The reason is the finding recorded in ``tasks.md``: *the corpus sweep is
necessary but not sufficient, proven rather than theorised.* Slice 2's mutation
pass broke the round-trip invariant in principle (``.match`` → ``.search`` on the
field-label branch) and all 298 files passed anyway, because no committed
transcript contains a mid-line field label. Slice 3's ``Name:`` rule collides
with the cast-list labels, with ``Moderator:``, with a ballot and with
``Pointing round N:``, so the **synthetic near-misses below are load-bearing**,
not decorative — several of them (``Mary Ann: hello``, ``Avery:no space``, a
degenerate ``<recap></recap>``) are shapes the corpus does not contain at all.

**Slice 4 changed what a test input IS, which is the one thing to internalise
before reading or adding anything below.** Until this slice the tokenizer was a
function of a *line*; it is now a function of a **file**. ``tokenize_transcript``
makes two passes — ``_cast_side_map(lines)`` reads ``<setup>``'s
``<player name=… role=…>`` entries into ``name → side``, then the per-line chain
runs with that map threaded through — so **the same line tokenizes differently in
two different files**. ``Avery: hi`` is ``speaker``/``speech`` on its own,
``speaker-mafia``/``speech-mafia`` under a ``<setup>`` that says so, and
``speaker``/``speech`` again under one that contradicts itself. A synthetic test
that wants a side kind **must carry the ``<setup>`` block that grants it**; a
synthetic test that wants the neutral kinds gets them for free.

Everything else about the layer is unchanged: pure, no I/O, no Rich/Textual, and
the round trip still holds byte for byte.

**The unknown side is the NEUTRAL ``speaker``/``speech``, never the ``plain``
kind** (ratified in Slice 4; see
:func:`test_a_speaker_absent_from_the_cast_list_is_neutral_speech_not_plain`,
which is this slice's single most important assertion). Slice 4's task text and
tech-spec §2 A both say an unknown name "yields ``plain``" — wording that
predates ``speech`` existing as a kind and that contradicts tech-spec §2 B's
promise that a pre-022 file keeps ``marker``, **``speaker``**, **``speech``**,
``field-label`` and ``plain`` spans. What "never a guess" forbids is assigning a
**side**, not styling the line as speech.

**Slice 4's own near-misses are where its value is**, for the reason Slices 2-3
each re-proved: the 298-file sweep only covers shapes the corpus happens to
contain, and the corpus contains none of these — a speaker absent from
``<setup>``, a role string the writer never emits, an empty / missing / unclosed
``<setup>``, a name given two conflicting sides, a ``<player …>`` tag typed
*outside* ``<setup>``, a cast name colliding with ``Moderator:`` or with
``Personality:``, a ``<thought>`` owner the cast list does not know, and
``name=""``. Each is one production line away from being wrong and no committed
game would notice.

**Slice 5 adds a SECOND whole-file fact beside the cast map: the reviewer's own
seat.** ``tokenize_transcript`` now reads ``_cast_side_map`` *and* ``_human_seat``
before the per-line chain runs. The two answer independent questions — *which
side is this name on* and *which seat was the person's* — and the tokenizer keeps
them independent, which is why ``speaker``/``speech`` go to **six** members each
(side unknown / Mafia / Law-abiding, each with and without the seat) while
``attr`` stays at three. Everything a Slice-4 test knew is still true; there is
simply one more argument threaded through, and one more reason the same line
tokenizes differently in two different files.

**The corpus's coverage of Slice 5 is the exact inverse of Slice 4's, measured.**
The *inference* route has total coverage — the moderator's ``Welcome, <name>.``
greeting appears exactly once in all 298 files, always inside ``<preamble>``, and
resolves a seat in every one of them. The *marker* route has **none**: ``human=``
appears in **0 of the 298**, so the writer's explicit ``human="true"``, the
both-routes-present precedence, and the no-seat fallback exist **only** in the
synthetic tests of section 4. Two further real-data facts shape what may be
asserted there: only **224** of the 298 files emit a seat kind at all (in the
other 74 the welcomed seat was killed on Night 1 and never spoke, so *"every file
bolds something"* is a FALSE assertion), and the seat kinds whose side is unknown
occur only in the 30 pre-spec-022 games.

**Spec 039 appends the twenty-first kind, and it is the first one this file has
absorbed from a spec other than 038.** ``diary`` is the body of a
``<diary player="X" day="N">…</diary>`` — the private note an AI files at the
Day->Night hinge, which the writer puts in the Day's TRAILER so it renders
between the last ``</round>`` and ``</day>``. Structurally the tag is
``<thought>``'s shape with a second attribute, so remarkably little moved: one
row in ``_STRUCTURAL_TAGS``, one in ``_ATTR_NAME_CASES``, ``diary`` in the family
test's ``unfamilied`` set, ``day`` in ``_ACHROMATIC_ATTR_KEYS``, and the
vocabulary count from twenty to twenty-one. That the port is that small **is**
spec 038's extension point collecting on its promise.

**Three of its consequences are worth knowing before reading anything below.**

* ``day`` joins ``_ATTR_NAMES``, which is a **global** whitelist — any tag
  carrying ``day="…"`` splits now, not only a ``<diary>`` — and the only thing
  stopping a ``birthday="…"`` splitting at its tail is ``_ATTR_VALUE_RE``'s
  lookbehind. Both are pinned.
* ``day`` has **zero** occurrences across the 298 committed transcripts, because
  no committed game was played by a build that had diaries. So the corpus
  sweep's "every whitelisted attribute really occurs" guard could not survive as
  an equality and is now three containments (see
  ``_CORPUS_REQUIRED_ATTR_NAMES``) — which also survives the opposite state, when
  spec 039's own measured runs are committed and ``day`` appears thousands of
  times.
* the ``plain`` fallback's "an unrecognised tag" case was literally
  ``<diary>secret</diary>``. Spec 038 picked that tag to stand for the next
  format change; the next format change arrived. The name now lives in
  ``_UNRECOGNISED_TAG`` with a rot guard against ``_MARKER_TAGS``, so the next
  collision is a sentence rather than a puzzle.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from graphia import eval_ledger

# ---------------------------------------------------------------------------
# The three kind FAMILIES (spec 038, Slices 4-5)
# ---------------------------------------------------------------------------
#
# Three of the eight Slice-1-to-3 kinds acquired side-bearing forms in Slice 4:
# `speaker`, `speech` and `attr` each split two ways, so a sweep or a helper that
# says "is this span a speaker?" must ask about the FAMILY, not the one neutral
# literal. Getting this wrong is silent rather than loud — the corpus sweep's
# `elif` chain simply stops seeing 20,655 of its 22,508 speaker spans and goes on
# passing — which is why the families are named once here and used everywhere
# below.
#
# **SLICE 5 SPLITS TWO OF THEM AGAIN, and this block is where that is absorbed.**
# `speaker` and `speech` each gain a *seat* axis on top of their side axis — the
# reviewer's own seat, bold within its side colour — so both families go from
# three members to SIX. `attr` deliberately gains nothing: the seat's cast entry
# and its `<thought player=…>` owner name are already `attr-mafia` /
# `attr-law-abiding`, which carry bold of their own, and functional-spec §2
# scopes the seat requirement to that seat's *lines*.
#
# Written as tuples of the constants rather than as string literals because the
# literals themselves are pinned, once, in
# `test_the_kind_constants_hold_the_literal_names_the_tables_use`. The neutral
# member is FIRST in each, and it is not a leftover: it is the appearance of a
# line whose side is unknown, which is the whole of Slice 4's degradation story.
#
# **`_SPEAKER_KINDS` and `_SPEECH_KINDS` are INDEX-ALIGNED**, and four call sites
# below pair them positionally via `.index(kind)` — "this speaker's speech must
# claim the same side and the same seat". Reordering one without the other turns
# every one of those into a silent lie, so the alignment is pinned as its own
# assertion in `test_the_kind_family_tuples_are_index_aligned_and_complete`.
_SPEAKER_KINDS = (
    eval_ledger.KIND_SPEAKER,
    eval_ledger.KIND_SPEAKER_MAFIA,
    eval_ledger.KIND_SPEAKER_LAW_ABIDING,
    eval_ledger.KIND_SPEAKER_HUMAN,
    eval_ledger.KIND_SPEAKER_MAFIA_HUMAN,
    eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN,
)
_SPEECH_KINDS = (
    eval_ledger.KIND_SPEECH,
    eval_ledger.KIND_SPEECH_MAFIA,
    eval_ledger.KIND_SPEECH_LAW_ABIDING,
    eval_ledger.KIND_SPEECH_HUMAN,
    eval_ledger.KIND_SPEECH_MAFIA_HUMAN,
    eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN,
)
_ATTR_KINDS = (
    eval_ledger.KIND_ATTR,
    eval_ledger.KIND_ATTR_MAFIA,
    eval_ledger.KIND_ATTR_LAW_ABIDING,
)

# Every side-bearing kind, as the set a "no sides here at all" assertion tests
# against — the pre-022 degradation case, and every synthetic near-miss whose
# point is that a side was NOT invented.
#
# **ENUMERATED, NEVER SLICED — and that is a correction, not a style choice.**
# Until Slice 5 this read `_SPEAKER_KINDS[1:] + _SPEECH_KINDS[1:] + _ATTR_KINDS[1:]`,
# which was exactly right while every non-neutral member named a side. It stopped
# being right the moment `speaker-human` / `speech-human` joined those tuples:
# those two name a SEAT and no side at all (a pre-spec-022 game names its seat in
# the preamble and has no cast list to read a side from — 183 + 183 real spans).
# Left as a slice, this set would have quietly absorbed them, and two assertions
# would then have been wrong in opposite directions at once: "a pre-022 file
# gains no side kind" would FAIL on 20 real files that legitimately emit
# `speaker-human`, and the conservation test's `set(moved) == _SIDE_KINDS` would
# demand a kind the cast map cannot move. TEN kinds name a side — four dialogue,
# four dialogue-for-the-seat, two attr — and they are written out.
_SIDE_KINDS = frozenset(
    {
        eval_ledger.KIND_SPEAKER_MAFIA,
        eval_ledger.KIND_SPEAKER_LAW_ABIDING,
        eval_ledger.KIND_SPEECH_MAFIA,
        eval_ledger.KIND_SPEECH_LAW_ABIDING,
        eval_ledger.KIND_SPEAKER_MAFIA_HUMAN,
        eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN,
        eval_ledger.KIND_SPEECH_MAFIA_HUMAN,
        eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN,
        eval_ledger.KIND_ATTR_MAFIA,
        eval_ledger.KIND_ATTR_LAW_ABIDING,
    }
)

# The six kinds that name the reviewer's own SEAT, whatever their side. The seat
# axis's counterpart to `_SIDE_KINDS`, and the set a "nobody is bolded here"
# assertion tests against — which is most of Slice 5's near-misses, since every
# one of them is a route to the seat that must NOT fire.
_HUMAN_KINDS = frozenset(_SPEAKER_KINDS[3:] + _SPEECH_KINDS[3:])

# Side-bearing kind → its neutral base. The inverse of the SIDE split, used by
# the side-axis conservation test to state "only kinds moved, no boundary did" as
# an equality rather than as a pair of totals that the next committed eval run
# would move.
#
# Slice 5 adds the four side-qualified seat kinds. Note where each lands: with an
# empty cast map a Mafia seat is still a SEAT, so `speaker-mafia-human` degrades
# to `speaker-human` and not to `speaker` — the two axes are independent, and a
# table that collapsed both at once would let a tokenizer lose the seat entirely
# while this test went on passing.
_NEUTRAL_BASE_KIND = {
    eval_ledger.KIND_SPEAKER_MAFIA: eval_ledger.KIND_SPEAKER,
    eval_ledger.KIND_SPEAKER_LAW_ABIDING: eval_ledger.KIND_SPEAKER,
    eval_ledger.KIND_SPEECH_MAFIA: eval_ledger.KIND_SPEECH,
    eval_ledger.KIND_SPEECH_LAW_ABIDING: eval_ledger.KIND_SPEECH,
    eval_ledger.KIND_ATTR_MAFIA: eval_ledger.KIND_ATTR,
    eval_ledger.KIND_ATTR_LAW_ABIDING: eval_ledger.KIND_ATTR,
    eval_ledger.KIND_SPEAKER_MAFIA_HUMAN: eval_ledger.KIND_SPEAKER_HUMAN,
    eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN: eval_ledger.KIND_SPEAKER_HUMAN,
    eval_ledger.KIND_SPEECH_MAFIA_HUMAN: eval_ledger.KIND_SPEECH_HUMAN,
    eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN: eval_ledger.KIND_SPEECH_HUMAN,
}

# Dialogue kind → the same kind for the reviewer's own SEAT: `_NEUTRAL_BASE_KIND`'s
# opposite number on the other axis, and the table the seat-axis conservation test
# is written against. Six entries, because the seat multiplies every dialogue kind
# rather than replacing any of them — including the neutral pair, which is where
# all 30 pre-spec-022 games put their seat.
#
# Deliberately a table rather than `kind + "-human"`, mirroring the production
# module's own reason for spelling `_HUMAN_KINDS` out: a kind with no seated form
# (`marker`, `attr`, `thought`, `recap`) must be absent, not manufactured.
_SEATED_KIND = {
    eval_ledger.KIND_SPEAKER: eval_ledger.KIND_SPEAKER_HUMAN,
    eval_ledger.KIND_SPEECH: eval_ledger.KIND_SPEECH_HUMAN,
    eval_ledger.KIND_SPEAKER_MAFIA: eval_ledger.KIND_SPEAKER_MAFIA_HUMAN,
    eval_ledger.KIND_SPEAKER_LAW_ABIDING: eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN,
    eval_ledger.KIND_SPEECH_MAFIA: eval_ledger.KIND_SPEECH_MAFIA_HUMAN,
    eval_ledger.KIND_SPEECH_LAW_ABIDING: eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN,
}

# `_SEATED_KIND` read backwards: a seat kind → the kind that seat's SIDE-MATES
# take. Named separately from `_NEUTRAL_BASE_KIND` because the two undo different
# axes and confusing them is easy — de-seating `speaker-mafia-human` gives
# `speaker-mafia` (the side-mate), de-siding it gives `speaker-human` (the same
# seat in a game with no cast list). Both tables are needed and neither
# substitutes for the other.
_UNSEATED_KIND = {seated: base for base, seated in _SEATED_KIND.items()}


# ---------------------------------------------------------------------------
# The tokenizer under test
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> object:
    """Call ``graphia.eval_ledger.tokenize_transcript``, resolved at call time.

    Deliberately **not** a module-level ``from graphia.eval_ledger import
    tokenize_transcript``, which is otherwise this repo's idiom (see
    ``tests/test_ledger_model.py``). This test is written *before* the tokenizer
    exists, and a module-level ``ImportError`` is a **collection** error: pytest
    aborts the whole session on one, so a red-by-design file would take the other
    ~1290 tests down with it. Resolving the attribute here keeps the missing
    tokenizer a loud failure of exactly these tests. A later rename still breaks
    them, so the rename-detection property of the direct-import idiom is kept.
    """
    tokenize = getattr(eval_ledger, "tokenize_transcript", None)
    if tokenize is None:
        pytest.fail(
            "graphia.eval_ledger.tokenize_transcript does not exist. Spec 038 "
            "Slice 1 must provide the pure tokenizer "
            "`tokenize_transcript(text: str) -> list[tuple[str, str]]` returning "
            "ordered (text, kind) spans."
        )
    return tokenize(text)


# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------

# Resolved from THIS FILE, never from the CWD: ``pytest`` may be invoked from
# anywhere (repo root, ``tests/``, an IDE runner) and this sweep must always find
# the same corpus. ``tests/`` sits directly under the repo root, so ``parents[1]``
# is that root — the idiom ``tests/test_lambda_zip_contents.py`` already uses.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_ROOT = _REPO_ROOT / "evals" / "transcripts"

# How many problems one failing file is allowed to print before the report is
# truncated. A malformed tokenizer could otherwise emit tens of thousands of
# span complaints and bury the useful first one.
_MAX_REPORTED_PROBLEMS = 10

# How many failing files one run dir prints in full. A tokenizer bug is usually
# systematic, so all ~20 games in a run break at once; the first few reports say
# everything, and the count line says how wide the damage is.
_MAX_REPORTED_FILES = 5

# Characters of context shown either side of a round-trip divergence.
_DIVERGENCE_WINDOW = 60


def _discover_transcripts() -> list[Path]:
    """Every committed transcript, discovered — deliberately never hard-coded.

    The corpus grows with every measured eval run (298 files in 15 run dirs
    today), so a hard-coded list would rot within a day. Matches ``*.txt`` at any
    depth under the corpus root: that is the artifact form spec 017 writes
    (``<run-id>/game-NN.txt``) and it keeps a stray ``.DS_Store`` — a live hazard
    on macOS — from failing the suite on an undecodable read.
    """
    if not _CORPUS_ROOT.is_dir():
        return []
    return sorted(path for path in _CORPUS_ROOT.rglob("*.txt") if path.is_file())


_TRANSCRIPTS = _discover_transcripts()

# One test item per run directory (see the parametrize note on the sweep below).
_RUN_DIRS = sorted({path.parent for path in _TRANSCRIPTS})

# A fresh clone can legitimately carry no transcripts: the dir is NOT gitignored
# but its contents are curated commit-or-delete, and a shallow or partial checkout
# may have none. Skipping keeps ``pytest -q`` green there instead of failing on
# absent input data.
#
# Gated **per test** rather than at module level (where Slice 1's first task put
# it, when the corpus sweep was this file's only content). Slice 1's fourth task
# added the tokenizer's **synthetic** unit tests below, which need no corpus at
# all — under a module-level ``pytest.skip(allow_module_level=True)`` a
# corpus-less checkout would have silently taken every one of them down with the
# sweep, reporting a green run that had not exercised the tokenizer once. Where
# the corpus IS present (every full checkout: ``evals/transcripts/`` is committed
# and not gitignored) this gate changes nothing — the same test items run with the
# same results.
_NO_CORPUS_REASON = (
    f"no committed transcripts found under {_CORPUS_ROOT} — "
    "nothing to sweep in this checkout"
)

_requires_corpus = pytest.mark.skipif(not _TRANSCRIPTS, reason=_NO_CORPUS_REASON)

# The sweep's parameters: one per run dir, or a single explicitly-skipped
# placeholder when there is no corpus. The placeholder is deliberate — an empty
# parameter list is reported according to pytest's ``empty_parameter_set_mark``
# setting, which this repo does not pin, so the skip is spelled out here instead
# of inherited from configuration.
_RUN_DIR_PARAMS = [
    pytest.param(run_dir, id=run_dir.name) for run_dir in _RUN_DIRS
] or [
    pytest.param(
        None, marks=pytest.mark.skip(reason=_NO_CORPUS_REASON), id="no-corpus"
    )
]


def _rel(path: Path) -> str:
    """Path relative to the repo root, for readable failure messages."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:  # pragma: no cover - corpus always lives under the root
        return str(path)


# ---------------------------------------------------------------------------
# Diagnostics — this test is re-run after every slice, so its failure message
# is the product. A bare ``assert`` over 298 files that says only "False" would
# tell the tokenizer's author nothing.
# ---------------------------------------------------------------------------


def _span_problems(spans: object) -> list[str]:
    """Structural complaints about a tokenizer result, in reporting order.

    Checks what the concatenation invariant *cannot* see:

    * the result is a list of ``(text, kind)`` 2-tuples at all;
    * **no span has empty text** — an empty span concatenates to nothing, so a
      tokenizer that emits one (the classic symptom of splitting a marker into
      ``marker``/``attr`` runs at a boundary) round-trips perfectly while
      handing the UI junk to style;
    * **every kind is a non-empty string** — the UI maps kind → style, and a
      ``None`` or ``""`` kind is an unstyleable hole rather than ``plain``.
    """
    if not isinstance(spans, list):
        return [
            f"tokenize_transcript returned {type(spans).__name__}, expected list"
        ]
    problems: list[str] = []
    for index, span in enumerate(spans):
        if not isinstance(span, tuple) or len(span) != 2:
            problems.append(f"span {index} is not a (text, kind) 2-tuple: {span!r}")
            continue
        text, kind = span
        if not isinstance(text, str):
            problems.append(
                f"span {index} text is {type(text).__name__}, expected str: {text!r}"
            )
        elif not text:
            problems.append(
                f"span {index} has empty text (kind={kind!r}) — an empty span is "
                "invisible to the concatenation check but is still a bug"
            )
        if not isinstance(kind, str):
            problems.append(
                f"span {index} kind is {type(kind).__name__}, expected str: {kind!r}"
            )
        elif not kind:
            problems.append(f"span {index} has an empty kind (text={text!r})")
    return problems


def _divergence_report(original: str, spans: list[tuple[str, str]]) -> str:
    """Where the rebuilt text first parts company with the file, and in which span.

    Names the offset, the line/column, both lengths, a window of expected-vs-
    actual text, and the span the divergence lands inside — the four things
    needed to find the offending branch of the tokenizer without bisecting it.
    """
    rebuilt = "".join(text for text, _ in spans)
    limit = min(len(original), len(rebuilt))
    offset = next(
        (i for i in range(limit) if original[i] != rebuilt[i]),
        limit,  # one is a prefix of the other: the divergence is the length
    )
    line = original.count("\n", 0, offset) + 1
    column = offset - original.rfind("\n", 0, offset)
    start = max(0, offset - _DIVERGENCE_WINDOW)
    end = offset + _DIVERGENCE_WINDOW

    # Which span the divergence falls in, by walking the cumulative lengths.
    consumed = 0
    culprit = "past the last span (the spans stop short of the file)"
    for index, (text, kind) in enumerate(spans):
        if consumed + len(text) > offset:
            culprit = f"span {index} kind={kind!r} text={text!r}"
            break
        consumed += len(text)

    return (
        f"    first divergence at offset {offset} (line {line}, column {column})\n"
        f"    lengths: file={len(original)} rebuilt={len(rebuilt)} "
        f"(spans={len(spans)})\n"
        f"    in {culprit}\n"
        f"    file    : {original[start:end]!r}\n"
        f"    rebuilt : {rebuilt[start:end]!r}"
    )


def _file_failure(path: Path) -> str | None:
    """Tokenize one transcript; return a failure report, or ``None`` if clean.

    Tokenizes each file exactly **once** and checks every facet in that single
    pass — the corpus is 9.4 MB, so re-tokenizing it per assertion would put a
    real cost on a suite meant to run on every save.
    """
    original = path.read_text(encoding="utf-8")
    spans = _tokenize(original)

    problems = _span_problems(spans)
    if problems:
        shown = problems[:_MAX_REPORTED_PROBLEMS]
        if len(problems) > len(shown):
            shown.append(f"… and {len(problems) - len(shown)} more")
        return f"{_rel(path)}: malformed spans\n" + "\n".join(
            f"    {problem}" for problem in shown
        )

    # THE invariant. Written exactly as the tech spec states it (§2 Component A).
    if "".join(text for text, _ in spans) != original:
        return (
            f"{_rel(path)}: spans do not concatenate to the file's text\n"
            + _divergence_report(original, spans)
        )
    return None


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
#
# Parametrized by **run directory** (15 items today), not by file (298) and not
# as one monolithic test. The reasoning, since the choice is deliberate:
#
# * Per-file would add 298 items to a ~1290-item suite — a 23% inflation for a
#   single invariant — and grow unbounded, ~20 more items with every measured
#   eval run committed. Its only real gain over the run-dir split is ``--lf``
#   granularity, which the failure report below already supplies by naming the
#   exact file and offset.
# * One monolithic test would make a single bad transcript hide the shape of the
#   damage (is it one file or one whole format era?).
# * The run directory is the corpus's own unit of curation (spec 017 commits or
#   deletes a whole run dir) and its format eras fall along the same lines — the
#   three pre-022 dirs are exactly three items — so "which runs broke" is
#   immediately meaningful. Growth is ~1 item per eval run, not ~20.
#
# Within an item the sweep **collects** every failure rather than aborting on the
# first, so one malformed file never masks the other 19 in its run.


@pytest.mark.parametrize("run_dir", _RUN_DIR_PARAMS)
def test_spans_reconstruct_every_transcript_exactly(run_dir: Path) -> None:
    """``"".join(text for text, _ in spans) == original`` for every game in a run.

    Plus the two things concatenation cannot see: no span has empty text, and
    every kind is a non-empty string (see :func:`_span_problems`).
    """
    files = [path for path in _TRANSCRIPTS if path.parent == run_dir]
    assert files, f"no transcripts discovered under {run_dir} — the sweep is vacuous"

    failures = [report for path in files if (report := _file_failure(path))]
    reported = failures[:_MAX_REPORTED_FILES]
    if len(failures) > len(reported):
        reported.append(
            f"… and {len(failures) - len(reported)} further failing "
            f"transcript(s) in {run_dir.name}, not shown"
        )

    assert not failures, "\n\n".join(
        [
            f"{len(failures)} of {len(files)} transcripts in {run_dir.name} "
            "broke the round-trip invariant "
            '("".join(text for text, _ in spans) == original):',
            *reported,
        ]
    )


@_requires_corpus
def test_corpus_sweep_is_not_vacuous() -> None:
    """The sweep really has real input — a guard against a silently narrowed glob.

    Cheap (no tokenizing) and deliberately loose: it pins that transcripts were
    discovered, that they are grouped into run dirs, and that none of them is
    empty, so a green round-trip test can never mean "tokenized 0 bytes, 298
    times". Counts are asserted as lower bounds, not as today's 298/15, because
    the corpus grows with every committed eval run.
    """
    assert _TRANSCRIPTS, f"no transcripts discovered under {_CORPUS_ROOT}"
    assert _RUN_DIRS, "transcripts discovered but no run directories derived"

    empty = [_rel(path) for path in _TRANSCRIPTS if not path.read_text(encoding="utf-8")]
    assert not empty, f"empty transcript files would make the sweep vacuous: {empty}"


# The tail a marker must end with immediately before an `attr` span: the
# attribute's key, its equals sign and its OPENING quote. `attr` is the value
# only, so this is exactly what the tokenizer must have left behind.
_ATTR_OPENS_WITH = '="'

# Spec 038, Slice 4 — the kind a `role="…"` value must take, by the LITERAL role
# label written in the tag. Deliberately spelled out here rather than derived
# from `eval_ledger._ROLE_SIDES`: a guard that reads the same table the
# production code reads goes vacuous the moment that table is what breaks. These
# two strings are `eval_transcript._ROLE_LABELS`' own values — the format's
# authority — and all 1,960 role values in the corpus are one of them (1,424
# Law-abiding Citizen, 536 Mafia). Anything else is a role the writer never
# emits and must stay achromatic.
_EXPECTED_ROLE_KINDS = {
    "Mafia": eval_ledger.KIND_ATTR_MAFIA,
    "Law-abiding Citizen": eval_ledger.KIND_ATTR_LAW_ABIDING,
}

# The whitelisted attributes that carry NO side of their own and must stay
# achromatic `attr` forever (the ratified narrow reading: only `<player role=…>`,
# `<thought player=…>` and — from spec 039 — `<diary player=…>` go side-bearing).
# 3,970 corpus spans ride on this — tinting every name would leave `attr` with
# zero occupants in any real game and retire Slice 2's achromatic treatment by
# accident.
#
# SPEC 039 ADDS `day`, and it is the first member that is not a name at all. A
# `<diary player="Ava" day="2">` names a person and a Day, and only the person
# can carry a side; `_attr_kind`'s default is what keeps the Day achromatic, so
# the plausible "colour the whole diary tag" edit fails here as well as in the
# synthetic diary tests below. It has **zero** occurrences across the 298
# committed transcripts (measured: `day="` appears nowhere), so today this row is
# carried entirely by those synthetic tests and it starts biting on real data the
# first time a diaries-on eval run is committed.
_ACHROMATIC_ATTR_KEYS = frozenset({"name", "initiator", "target", "day"})

# The attribute names the corpus must ALWAYS carry — spec 038's five, written out
# as literals rather than read from `_ATTR_NAMES`, because this is the sweep's
# non-vacuity guard and a guard derived from the table it guards is no guard.
#
# Spec 039's `day` is deliberately absent: it is whitelisted and has zero
# occurrences today, and it will have thousands the first time a diaries-on eval
# run is committed. Neither state may fail, so the sweep asserts a containment in
# each direction rather than the equality it used to (see the end of
# :func:`test_the_line_splitting_kinds_hold_their_shape_across_the_corpus`).
_CORPUS_REQUIRED_ATTR_NAMES = frozenset(
    {"name", "role", "player", "initiator", "target"}
)


@_requires_corpus
def test_the_line_splitting_kinds_hold_their_shape_across_the_corpus() -> None:
    """The six sub-line kinds keep their boundaries on 9.4 MB of real prose.

    The one kind-shaped assertion the corpus sweep makes, and it is narrow on
    purpose (see this module's header). ``attr`` and ``field-label`` are the first
    kinds that split a *line* rather than claim a whole one, so they are the first
    whose boundaries real data can contradict:

    * a value must sit between a marker ending ``="`` and a marker starting with
      the closing quote, and must contain neither a quote nor an equals sign —
      the "value ONLY, never the key or the quotes" rule of tech-spec §2 A. The
      synthetic tests pin it on names a human chose; the corpus pins it on 14,113
      model-generated ones, including names with spaces, hyphens and accents.
    * a label must be one of the five the writer emits — anything else means the
      label branch matched prose.
    * **no styled span may carry a newline.** A marker style that ran to the end
      of a terminal row would be visible on every line of a game; splitting spans
      mid-line is what makes that newly possible, so it is checked here rather
      than only on the synthetic game.

    **Slice 3 widened this to its own four kinds**, on the same
    boundaries-real-data-can-contradict reasoning:

    * a ``speaker`` span must end at its colon, must be immediately followed by a
      ``speech`` span, and that speech must begin with the separating space —
      the ratified "the colon belongs to ``speaker``, the space to ``speech``"
      convention, checked against 22,508 real utterances under 498 distinct
      model-generated names rather than the handful a fixture can invent;
    * a ``speaker`` name must never be one of the writer's own
      ``_NON_SPEAKER_PREFIXES`` — the exclusion set doing its job on real data;
    * a ``thought`` or ``recap`` body must sit **between two markers**, the
      opening tag ending ``>`` and the closing one starting ``</``. That is
      Slice 3's "the surrounding tag stays ``marker``" made structural over the
      8,183 thoughts and 2,736 recaps the corpus actually holds.

    **Slice 4 widened it to the three FAMILIES and added one rule of its own.**
    ``speaker``, ``speech`` and ``attr`` each split two ways, so every branch
    below now asks about the family (:data:`_SPEAKER_KINDS` and friends). That
    was not optional bookkeeping: left as three ``==`` comparisons against the
    neutral literals, the chain would have stopped seeing **20,655 of the 22,508**
    speaker spans and **10,143 of the 14,113** attribute values and gone on
    passing — a silent loss of most of the coverage this test exists for.

    The rule Slice 4 adds is the ratified **narrow reading** of where a side may
    land, checked on every attribute value in the corpus:

    * a ``role="…"`` value's kind is decided by the label *written right there* —
      ``Mafia`` → ``attr-mafia``, ``Law-abiding Citizen`` → ``attr-law-abiding``,
      anything else → achromatic ``attr``, never a guess;
    * a ``<thought player="…">`` owner may take either side or neither (its side
      is a map lookup, and the map may not know the name);
    * a cast entry's ``name`` and a vote's ``initiator``/``target`` are
      **always** achromatic ``attr`` — 3,970 spans that a one-line "tint every
      name" change would recolour, retiring Slice 2's achromatic treatment by
      accident;
    * and a ``speaker`` span's side must equal its ``speech`` span's side, so a
      line can never be half red and half blue.

    Plus non-vacuity guards that cannot rot as the corpus grows: every one of
    the five attribute names and all five labels must actually occur, and the
    pairs the writer always emits together must be equinumerous — every
    ``<player>`` carries both a ``name`` and a ``role``, every ``<vote>`` both an
    ``initiator`` and a ``target``. Absolute equalities rather than today's
    counts, so a new eval run cannot break them and a tokenizer that dropped one
    attribute of a pair cannot pass them. Slice 3 adds three more of the same
    shape: every utterance is one ``speaker`` and one ``speech`` so the two
    counts are equal and non-zero, both inline bodies occur, and **at least one
    speaker name begins with a lowercase letter** — the guard on the measured
    decision that casing is not a speaker test (39 corpus cast names are
    lowercase and a casing rule would lose 426 real speaker lines).

    **Two of those Slice-3 guards still pass but no longer measure what they
    did, and saying so is the point of this paragraph.** ``kind_counts[speaker]``
    now counts only the **neutral** spans — 1,853 of them, and measurement puts
    every single one inside the 30 pre-spec-022 files, because every spec-022
    speaker is in their own file's cast list. So the neutral speaker/speech
    equality and the lowercase guard are **pre-022-era measurements** from
    Slice 4 onward, and they are kept in that reading (the era is 10% of the
    corpus and its degradation is a promise tech-spec §2 B makes explicitly).
    The whole-corpus versions of both are asserted alongside them, over the
    families, so neither property is quietly lost to the era split.
    """
    labels = set(eval_ledger._FIELD_LABELS)
    non_speakers = set(eval_ledger._NON_SPEAKER_PREFIXES)
    problems: list[str] = []
    attr_names: Counter[str] = Counter()
    label_texts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    lowercase_speakers: set[str] = set()
    lowercase_speakers_any_side: set[str] = set()

    for path in _TRANSCRIPTS:
        spans = _tokenize(path.read_text(encoding="utf-8"))
        assert isinstance(spans, list)
        for index, (text, kind) in enumerate(spans):
            if kind == eval_ledger.KIND_PLAIN:
                continue
            kind_counts[kind] += 1
            if "\n" in text:
                problems.append(
                    f"{_rel(path)} span {index}: styled {kind!r} span carries a "
                    f"newline: {text!r}"
                )
            if kind in _SPEAKER_KINDS:
                after = spans[index + 1] if index + 1 < len(spans) else None
                # The side the speaker prefix claims, as the index into the
                # family tuples — so the speech span after it can be required to
                # claim the SAME one. A line half red and half blue is the exact
                # shape of a side split applied to only one of the pair.
                side_index = _SPEAKER_KINDS.index(kind)
                if not text.endswith(":"):
                    problems.append(
                        f"{_rel(path)} span {index}: speaker {text!r} does not "
                        "end at its colon"
                    )
                elif text[:-1] in non_speakers:
                    problems.append(
                        f"{_rel(path)} span {index}: {text!r} is one of the "
                        f"writer's own prefixes {sorted(non_speakers)}, not a "
                        "player speaking"
                    )
                else:
                    if text[0].islower():
                        lowercase_speakers_any_side.add(text)
                        if kind == eval_ledger.KIND_SPEAKER:
                            lowercase_speakers.add(text)
                if after is None or after[1] != _SPEECH_KINDS[side_index]:
                    problems.append(
                        f"{_rel(path)} span {index}: speaker {text!r} ({kind}) is "
                        f"followed by {after!r}, expected a "
                        f"{_SPEECH_KINDS[side_index]} span — a speaker and their "
                        "words must carry the SAME side"
                    )
            elif kind in _SPEECH_KINDS:
                before = spans[index - 1] if index else None
                expected_speaker = _SPEAKER_KINDS[_SPEECH_KINDS.index(kind)]
                if before is None or before[1] != expected_speaker:
                    problems.append(
                        f"{_rel(path)} span {index}: speech {text[:40]!r} ({kind}) "
                        f"is preceded by {before!r}, expected a "
                        f"{expected_speaker} span"
                    )
                if not text.startswith(" "):
                    problems.append(
                        f"{_rel(path)} span {index}: speech {text[:40]!r} does "
                        "not begin with the separating space"
                    )
            elif kind in (eval_ledger.KIND_THOUGHT, eval_ledger.KIND_RECAP):
                before = spans[index - 1] if index else None
                after = spans[index + 1] if index + 1 < len(spans) else None
                if (
                    before is None
                    or before[1] != eval_ledger.KIND_MARKER
                    or not before[0].endswith(">")
                ):
                    problems.append(
                        f"{_rel(path)} span {index}: {kind} body {text[:40]!r} is "
                        f"preceded by {before!r}, expected its opening marker"
                    )
                if (
                    after is None
                    or after[1] != eval_ledger.KIND_MARKER
                    or not after[0].startswith("</")
                ):
                    problems.append(
                        f"{_rel(path)} span {index}: {kind} body {text[:40]!r} is "
                        f"followed by {after!r}, expected its closing marker"
                    )
            elif kind == eval_ledger.KIND_FIELD_LABEL:
                label_texts[text] += 1
                if text not in labels:
                    problems.append(
                        f"{_rel(path)} span {index}: {text!r} is kinded "
                        f"field-label but is not one of {sorted(labels)}"
                    )
            elif kind in _ATTR_KINDS:
                before = spans[index - 1] if index else None
                after = spans[index + 1] if index + 1 < len(spans) else None
                if '"' in text or "=" in text:
                    problems.append(
                        f"{_rel(path)} span {index}: attr {text!r} contains its "
                        "own punctuation — the value must exclude the quotes"
                    )
                if (
                    before is None
                    or before[1] != eval_ledger.KIND_MARKER
                    or not before[0].endswith(_ATTR_OPENS_WITH)
                ):
                    problems.append(
                        f"{_rel(path)} span {index}: attr {text!r} is preceded by "
                        f"{before!r}, expected a marker ending {_ATTR_OPENS_WITH!r}"
                    )
                else:
                    key = before[0].rsplit(" ", 1)[-1][: -len(_ATTR_OPENS_WITH)]
                    attr_names[key] += 1
                    # Slice 4's narrow reading, checked per attribute value on
                    # every one of the corpus's 14,113 of them. `_EXPECTED_ROLE_KINDS`
                    # is spelled out as LITERALS rather than read from
                    # `_ROLE_SIDES`, so a mutation of that table is caught here
                    # instead of being mirrored into the expectation.
                    if key == "role":
                        wanted = _EXPECTED_ROLE_KINDS.get(text, eval_ledger.KIND_ATTR)
                        if kind != wanted:
                            problems.append(
                                f"{_rel(path)} span {index}: role {text!r} is "
                                f"{kind}, expected {wanted} — a role's side comes "
                                "from the label written right there"
                            )
                    elif key in _ACHROMATIC_ATTR_KEYS and kind != eval_ledger.KIND_ATTR:
                        problems.append(
                            f"{_rel(path)} span {index}: {key}={text!r} is {kind}; "
                            f"{sorted(_ACHROMATIC_ATTR_KEYS)} carry no side of "
                            "their own and must stay achromatic `attr`"
                        )
                if (
                    after is None
                    or after[1] != eval_ledger.KIND_MARKER
                    or not after[0].startswith('"')
                ):
                    problems.append(
                        f"{_rel(path)} span {index}: attr {text!r} is followed by "
                        f"{after!r}, expected a marker starting with a quote"
                    )
        if len(problems) > _MAX_REPORTED_PROBLEMS:
            problems.append("… truncated; fix these first")
            break

    assert not problems, "\n".join(problems)

    # Non-vacuity: the corpus really exercises both kinds, spec 038's five
    # attributes and all five labels.
    #
    # SPEC 039 SPLIT THE ATTRIBUTE HALF INTO THREE CLAIMS, and the reason is worth
    # reading before "simplifying" it back. `day` is whitelisted and occurs zero
    # times in the corpus today; it will occur thousands of times the first time a
    # diaries-on eval run is committed. A bare `set(attr_names) == set(_ATTR_NAMES)`
    # is therefore wrong now in one direction and would be wrong later in the
    # other. What holds in both states, and keeps every ounce of the guard:
    #
    #   * the five spec-038 names ALWAYS occur — the non-vacuity claim itself,
    #     undiminished, and the thing that fails if the tokenizer stops lifting
    #     one of a pair;
    #   * nothing OUTSIDE the whitelist is ever lifted — the whitelist really is
    #     a whitelist, on 9.4 MB of model-generated prose;
    #   * and `day` is the ONLY whitelisted name the corpus is allowed to be
    #     missing. That last one is the rot guard the equality used to be: a
    #     seventh attribute added to `_ATTR_NAMES` fails here until somebody comes
    #     and says which of the three claims it belongs to.
    assert _CORPUS_REQUIRED_ATTR_NAMES <= set(attr_names), (
        f"attribute values found for {sorted(attr_names)}, expected at least "
        f"{sorted(_CORPUS_REQUIRED_ATTR_NAMES)}"
    )
    assert set(attr_names) <= set(eval_ledger._ATTR_NAMES), (
        "the corpus lifted "
        f"{sorted(set(attr_names) - set(eval_ledger._ATTR_NAMES))}, which is not "
        "on the tokenizer's whitelist at all"
    )
    assert set(eval_ledger._ATTR_NAMES) - _CORPUS_REQUIRED_ATTR_NAMES == {"day"}, (
        "the whitelist grew a name the corpus is not required to carry: "
        f"{sorted(set(eval_ledger._ATTR_NAMES) - _CORPUS_REQUIRED_ATTR_NAMES)} — "
        "`day` is the one spec-039 exception, and a second one needs its own "
        "reason here"
    )
    assert set(label_texts) == labels, (
        f"field labels found: {sorted(label_texts)}, expected all of "
        f"{sorted(labels)}"
    )
    # The writer emits each pair together, always — so a tokenizer that lifted
    # one of a pair and not the other is caught without pinning a count that the
    # next committed eval run would move.
    assert attr_names["name"] == attr_names["role"] > 0
    assert attr_names["initiator"] == attr_names["target"] > 0
    assert attr_names["player"] > 0

    # Slice 3's three, in the same rot-proof form. Every utterance is exactly one
    # speaker span and one speech span, so a tokenizer that emitted a speaker and
    # swallowed its speech (or the reverse) fails here without a count being
    # pinned that the next committed eval run would move.
    #
    # SINCE SLICE 4 THIS PAIR IS A PRE-022-ERA MEASUREMENT, not a whole-corpus
    # one: `KIND_SPEAKER` is now only the NEUTRAL kind, and measurement puts all
    # 1,853 of its spans inside the 30 pre-spec-022 files (every spec-022 speaker
    # is in their own file's cast list, so every one of them takes a side). It is
    # kept in that narrowed reading deliberately — the pre-022 degradation is a
    # promise tech-spec §2 B makes in those words, and this is the only assertion
    # in the sweep that measures it — with the whole-corpus version restated over
    # the families immediately below.
    assert (
        kind_counts[eval_ledger.KIND_SPEAKER]
        == kind_counts[eval_ledger.KIND_SPEECH]
        > 0
    ), (
        "the NEUTRAL speaker/speech counts diverged, or the pre-022 era stopped "
        f"producing them at all: {kind_counts}"
    )
    # ...and the same property over all three side families, which is the
    # whole-corpus statement. Also the non-vacuity guard for Slice 4 itself: both
    # sides must actually occur, or every side assertion above is checking a kind
    # no real game emits.
    for family_speaker, family_speech in zip(
        _SPEAKER_KINDS, _SPEECH_KINDS, strict=True
    ):
        assert kind_counts[family_speaker] == kind_counts[family_speech] > 0, (
            f"{family_speaker}/{family_speech} diverged or never occurred: "
            f"{kind_counts}"
        )
    assert kind_counts[eval_ledger.KIND_ATTR_MAFIA] > 0
    assert kind_counts[eval_ledger.KIND_ATTR_LAW_ABIDING] > 0
    assert kind_counts[eval_ledger.KIND_THOUGHT] > 0
    assert kind_counts[eval_ledger.KIND_RECAP] > 0
    # The measured reason `_SPEAKER_RE` is not gated on an initial capital: the
    # corpus really does contain lowercase-named players, so a casing rule would
    # drop real speaker lines rather than only the three writer literals.
    #
    # Also narrowed by Slice 4 in exactly the same way — 11 distinct lowercase
    # NEUTRAL names, all pre-022 — so the whole-corpus version is asserted
    # alongside it (49 more, spread across both sides).
    assert lowercase_speakers, (
        "no lowercase-initial NEUTRAL speaker name found — either the pre-022 "
        "corpus changed or the speaker rule has quietly acquired a casing guard, "
        "which measurement rejected (39 lowercase cast names, 426 speaker lines "
        "at stake)"
    )
    assert lowercase_speakers <= lowercase_speakers_any_side
    assert len(lowercase_speakers_any_side) > len(lowercase_speakers), (
        "lowercase speaker names occur ONLY in the neutral family — the "
        "side-bearing branches have lost them, which is what a casing guard "
        "added to the side path alone would look like"
    )


# ===========================================================================
# 2. The tokenizer's synthetic unit tests (spec 038, Slice 1, task 4)
# ===========================================================================
#
# The sweep above proves the tokenizer never LOSES anything. It says nothing
# about whether the right things are marked, because it deliberately ignores
# kinds: a tokenizer that returned one giant ``plain`` span for each file would
# pass every assertion in half 1. These tests close exactly that gap, on inputs
# small enough to read in the failure message.
#
# Everything below is **absolute**: each case pins the complete expected span
# list, not "the same as some other input" or "at least one marker somewhere".
# That is spec 037's mutation finding applied here — a relative assertion can
# hold while both sides are broken identically, and "contains a marker" holds for
# a tokenizer that marks the whole line.
#
# Inputs are synthetic and hand-written (never lifted from a real game), so the
# expected spans are checkable by eye; the real corpus is half 1's job.


def _spans(text: str) -> list[tuple[str, str]]:
    """:func:`_tokenize` with the result narrowed to the documented span type."""
    spans = _tokenize(text)
    assert isinstance(spans, list), (
        f"tokenize_transcript returned {type(spans).__name__}, expected list"
    )
    return spans


# ---------------------------------------------------------------------------
# The marker vocabulary
# ---------------------------------------------------------------------------

def test_the_kind_constants_hold_the_literal_names_the_tables_use() -> None:
    """The twenty-one kind constants really are these twenty-one strings.

    The expectation tables below are written with literal ``"marker"`` /
    ``"attr"`` strings rather than ``eval_ledger.KIND_*`` references, because a
    five-span expectation reads as a span list only when it is spelled like one.
    That is safe exactly as far as this test: it ties the literals to the
    constants in one place, so renaming a constant fails **here**, with a message
    naming the pair, instead of leaving every table below silently asserting
    against a string the production code no longer emits.

    Pinned to the literals, not to ``TRANSCRIPT_KINDS`` — the tuple is allowed to
    grow (later slices, and now later SPECS, append), these twenty-one names are
    not allowed to change.

    **The six Slice-4 literals are the ones with a rule to break.** Each is the
    neutral kind's name plus a hyphen plus the side, and the side token is
    ``mafia`` / ``law-abiding`` — **semantic, never a colour**, which is the one
    naming law this spec states outright (tech-spec §2 A: "``speaker-mafia`` is
    acceptable as a kind; ``speaker-red`` is not"). The literals are also what
    the UI's ``_TRANSCRIPT_KIND_COMPONENTS`` keys and its six CSS rules are
    written against, so a rename that this test did not catch would leave the
    reading view silently unstyled rather than raising — the ratified
    forward-compatibility fallback working against us.

    The literal-vs-constant split matters here too: ``_ATTR_KINDS`` and the other
    family tuples at the top of this module are built from the CONSTANTS, so they
    would follow a rename without complaint. This test is the one place the
    strings themselves are nailed down.
    """
    assert eval_ledger.KIND_MARKER == "marker"
    assert eval_ledger.KIND_PLAIN == "plain"
    assert eval_ledger.KIND_ATTR == "attr"
    assert eval_ledger.KIND_FIELD_LABEL == "field-label"
    assert eval_ledger.KIND_SPEAKER == "speaker"
    assert eval_ledger.KIND_SPEECH == "speech"
    assert eval_ledger.KIND_THOUGHT == "thought"
    assert eval_ledger.KIND_RECAP == "recap"
    # Slice 4's six.
    assert eval_ledger.KIND_SPEAKER_MAFIA == "speaker-mafia"
    assert eval_ledger.KIND_SPEAKER_LAW_ABIDING == "speaker-law-abiding"
    assert eval_ledger.KIND_SPEECH_MAFIA == "speech-mafia"
    assert eval_ledger.KIND_SPEECH_LAW_ABIDING == "speech-law-abiding"
    assert eval_ledger.KIND_ATTR_MAFIA == "attr-mafia"
    assert eval_ledger.KIND_ATTR_LAW_ABIDING == "attr-law-abiding"
    # Slice 5's six — the reviewer's own seat, on both axes. Each is its Slice-4
    # (or Slice-3) kind plus the `-human` qualifier, and the qualifier names WHOSE
    # SEAT IT IS, never the weight it is drawn in: `speaker-mafia-human` is a
    # kind, `speaker-mafia-bold` never is. The UI's six extra CSS rules key off
    # exactly these strings, and the ratified fallback (a kind the style map has
    # never heard of renders unstyled and never raises) means a rename this test
    # did not catch would leave the reviewer's seat silently unbolded rather than
    # failing anywhere.
    assert eval_ledger.KIND_SPEAKER_HUMAN == "speaker-human"
    assert eval_ledger.KIND_SPEECH_HUMAN == "speech-human"
    assert eval_ledger.KIND_SPEAKER_MAFIA_HUMAN == "speaker-mafia-human"
    assert eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN == "speaker-law-abiding-human"
    assert eval_ledger.KIND_SPEECH_MAFIA_HUMAN == "speech-mafia-human"
    assert eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN == "speech-law-abiding-human"
    # Spec 039's one — the Day's diary, and the twenty-first. A BARE LITERAL for
    # the same reason as the twenty above and then some: the string `"diary"` is
    # ALSO a key of `_INLINE_CONTENT_KINDS` and a member of `_MARKER_TAGS` (where
    # it is the TAG name, not the kind), and the UI's `transcript--diary`
    # component class spells it out a third time. A rename that only checked
    # `KIND_DIARY in TRANSCRIPT_KINDS` would leave those three agreeing with each
    # other and disagreeing with the constant.
    assert eval_ledger.KIND_DIARY == "diary"


def test_the_kind_family_tuples_are_index_aligned_and_complete() -> None:
    """This module's own family tables, guarded against the two ways they rot.

    They are test-local data, not production data, which is exactly why they need
    a test: nothing else in the suite fails when they go stale, and `tasks.md`
    records what that costs. When Slice 4 split `speaker`/`speech`/`attr`, the
    corpus sweep's `elif` chain kept comparing against the neutral literals and
    **silently stopped seeing 20,655 of 22,508 speaker spans** while only three
    assertions went red. Slice 5 splits two of the same families again.

    Four claims, each covering a distinct failure:

    * **`_SPEAKER_KINDS` and `_SPEECH_KINDS` are index-aligned.** Four call sites
      pair them positionally (`_SPEECH_KINDS[_SPEAKER_KINDS.index(kind)]`) to say
      "a speaker and their words carry the same side AND the same seat". Reorder
      one tuple and every one of those becomes a silent lie — it still runs, it
      just asserts the wrong pairing. Checked by rebuilding each speech kind from
      its speaker kind's own name.
    * **The families are complete**, i.e. every declared kind belongs to exactly
      one family or to the handful that have none (`marker`, `plain`,
      `field-label`, `thought`, `recap` and, from spec 039, `diary`). A kind added
      to the vocabulary and left out of a family is invisible to every sweep that
      classifies by family — which is the Slice-4 failure restated as a guard, and
      which is exactly the assertion that went red when the tokenizer learned
      `<diary>`.
    * **`_SIDE_KINDS` is exactly the ten kinds that name a side**, and contains
      neither of the two seat kinds that name none. This is the trap this slice
      set: written as `_SPEAKER_KINDS[1:] + …` it would absorb `speaker-human`
      and `speech-human`, breaking "a pre-022 file gains no side" on 20 real
      files and the conservation test's `set(moved)` in both directions.
    * **`_SEATED_KIND` and `_NEUTRAL_BASE_KIND` are the two axes' inverses**, and
      they compose: seating a kind then de-siding it is the same as de-siding it
      then seating it. That is "side and seat are independent facts" written as
      an equation, and it is what makes the two conservation sweeps below
      separable at all.
    """
    # (1) Index alignment, derived rather than restated — a second hand-written
    # list would rot in the same commit as the first.
    assert len(_SPEAKER_KINDS) == len(_SPEECH_KINDS) == 6
    for speaker_kind, speech_kind in zip(_SPEAKER_KINDS, _SPEECH_KINDS, strict=True):
        assert speaker_kind.replace("speaker", "speech", 1) == speech_kind, (
            f"{speaker_kind} is paired with {speech_kind}; the two families must "
            "be index-aligned because four call sites pair them by position"
        )
    assert len(set(_SPEAKER_KINDS)) == len(_SPEAKER_KINDS)
    assert len(set(_SPEECH_KINDS)) == len(_SPEECH_KINDS)

    # (2) Completeness: every declared kind is accounted for.
    familied = set(_SPEAKER_KINDS) | set(_SPEECH_KINDS) | set(_ATTR_KINDS)
    unfamilied = {
        eval_ledger.KIND_MARKER,
        eval_ledger.KIND_PLAIN,
        eval_ledger.KIND_FIELD_LABEL,
        eval_ledger.KIND_THOUGHT,
        eval_ledger.KIND_RECAP,
        # Spec 039. `diary` joins `thought` and `recap` as a BODY kind: it names
        # neither a side nor a seat, so it belongs to no family and has to be
        # named here or the completeness assertion below reports it as a kind
        # every family-classifying sweep is blind to. Which is what it did.
        eval_ledger.KIND_DIARY,
    }
    assert familied | unfamilied == set(eval_ledger.TRANSCRIPT_KINDS), (
        "a declared kind belongs to no family and to no named exception, so every "
        "sweep that classifies by family is now blind to it: "
        f"{sorted(set(eval_ledger.TRANSCRIPT_KINDS) - familied - unfamilied)}"
    )
    assert not familied & unfamilied

    # (3) `_SIDE_KINDS` names sides and only sides.
    assert len(_SIDE_KINDS) == 10
    assert _SIDE_KINDS == set(_SIDE_OF_KIND)
    assert eval_ledger.KIND_SPEAKER_HUMAN not in _SIDE_KINDS, (
        "`speaker-human` is a SEAT whose side is unknown — the pre-022 case, 183 "
        "real spans — and it names no side. Absorbing it into `_SIDE_KINDS` is "
        "the exact slicing bug this test exists to stop."
    )
    assert eval_ledger.KIND_SPEECH_HUMAN not in _SIDE_KINDS
    assert _SIDE_KINDS < familied
    assert _HUMAN_KINDS == set(_SEATED_KIND.values())
    assert len(_HUMAN_KINDS) == 6

    # (4) The two axes are inverses of each other and they commute.
    assert set(_SEATED_KIND) == set(_SPEAKER_KINDS[:3]) | set(_SPEECH_KINDS[:3])
    assert set(_NEUTRAL_BASE_KIND) == _SIDE_KINDS
    for base, seated in _SEATED_KIND.items():
        # De-siding a seated kind lands on the seated form of the base's own
        # neutral — never on the base itself, which would lose the seat.
        assert _NEUTRAL_BASE_KIND.get(seated, seated) == _SEATED_KIND.get(
            _NEUTRAL_BASE_KIND.get(base, base)
        ), f"the seat and side axes do not commute at {base}/{seated}"


def test_every_kind_constant_is_exported_and_declared_exactly_once() -> None:
    """Slice 4's housekeeping task, pinned so it cannot half-rot again.

    ``eval_ledger.__all__`` used to export ``KIND_MARKER``, ``KIND_PLAIN`` and
    ``TRANSCRIPT_KINDS`` but none of the other six kinds — while
    ``ui/ledger_viewer.py`` imported them all regardless. That is a
    half-populated export nobody chose: it works (Python does not enforce
    ``__all__`` on a direct import) right up until something does a star-import
    or a doc tool reads the public surface, and it teaches the next slice that
    adding the constant is optional. Slice 4 completed it; this is the guard.

    Three separate things, because each fails differently:

    * every constant named in :data:`TRANSCRIPT_KINDS` is reachable as a module
      attribute AND listed in ``__all__``;
    * the vocabulary is exactly twenty-one entries with no duplicate — a
      copy-paste that declared ``speech-mafia`` twice would leave a kind silently
      missing while the length still looked plausible;
    * ``TRANSCRIPT_KINDS`` and the ``KIND_*`` constants describe the same set, so
      a constant can neither be added without being declared nor declared
      without existing.

    **Slice 5 closed spec 038's vocabulary at twenty**, and the count is written
    out rather than derived deliberately: this is the one assertion in the file
    whose job is to make a slice that adds a kind come and look here, which is
    where the ``__all__`` obligation is stated. Slice 4 raised it from eight to
    fourteen; the six that took it to twenty are the reviewer's own seat on both
    axes (side unknown / Mafia / Law-abiding, times speaker and speech).

    **And it did its job.** Spec 039's ``diary`` — the first kind added by a spec
    other than 038 — takes it to twenty-one, and this assertion is where that
    slice's author was sent to read the ``__all__`` obligation and the one-route
    house pattern: one constant, one ``TRANSCRIPT_KINDS`` entry, one ``__all__``
    line. Nothing else about this test changed, which is the route working — a
    kind is added by appending, never by moving anything.
    """
    kind_constants = {
        name: value
        for name, value in vars(eval_ledger).items()
        if name.startswith("KIND_") and isinstance(value, str)
    }

    assert len(eval_ledger.TRANSCRIPT_KINDS) == 21, (
        "spec 038 closed the vocabulary at twenty kinds and spec 039 appended "
        "the twenty-first, `diary`; "
        f"found {eval_ledger.TRANSCRIPT_KINDS}"
    )
    assert len(set(eval_ledger.TRANSCRIPT_KINDS)) == len(
        eval_ledger.TRANSCRIPT_KINDS
    ), f"a kind is declared twice: {eval_ledger.TRANSCRIPT_KINDS}"
    assert set(kind_constants.values()) == set(eval_ledger.TRANSCRIPT_KINDS), (
        f"the KIND_* constants {sorted(kind_constants.values())} and "
        f"TRANSCRIPT_KINDS {sorted(eval_ledger.TRANSCRIPT_KINDS)} disagree"
    )

    exported = set(eval_ledger.__all__)
    missing = sorted(name for name in kind_constants if name not in exported)
    assert not missing, (
        f"kind constants missing from eval_ledger.__all__: {missing} — every "
        "kind constant is part of the module's public surface (the UI imports "
        "them), so a new one is added to `__all__` as well as to "
        "`TRANSCRIPT_KINDS`"
    )
    assert "TRANSCRIPT_KINDS" in exported
    assert "tokenize_transcript" in exported
    # ...and `__all__` names nothing that does not exist, which is the failure
    # mode a bare "is it listed?" check cannot see.
    assert not [name for name in exported if not hasattr(eval_ledger, name)]


# The THIRTEEN structural tags — spec 038's twelve plus spec 039's ``<diary>`` —
# each with a representative opening form and the
# COMPLETE span list that form must produce. Ratified during Slice 1 (see the
# header note in ``tasks.md``): the eight attribute-free, content-free section
# delimiters are only a *subset* — ``<player …>``, ``<vote …>``, ``<recap>`` and
# ``<thought …>`` are markers too, from Slice 1 onward, because Slices 2-3
# reclassify their *innards* and would otherwise have to promote whole lines out
# of ``plain``. A game whose ``<setup>`` dimmed while each ``<player …>`` inside
# it read as content would be the visible symptom.
#
# **Changed in Slice 2, widened by spec 039**: the FOUR tags that carry one of the
# six detail attributes no longer produce a single span. ``attr`` is the VALUE
# only, so the tag alternates marker / attr / marker — the key, the quotes and the
# angle brackets all stay marker. The other nine are unchanged and must STAY one
# span:
# that is the "the attribute split must not leave stray empty spans" half of
# Slice 2's test task, and it has its own test below as well.
_STRUCTURAL_TAGS: tuple[tuple[str, str, list[tuple[str, str]]], ...] = (
    ("transcript", "<transcript>", [("<transcript>", "marker")]),
    ("setup", "<setup>", [("<setup>", "marker")]),
    ("preamble", "<preamble>", [("<preamble>", "marker")]),
    ("night", "<night>", [("<night>", "marker")]),
    ("day", "<day>", [("<day>", "marker")]),
    ("round", "<round>", [("<round>", "marker")]),
    ("endgame", "<endgame>", [("<endgame>", "marker")]),
    ("kill", "<kill>", [("<kill>", "marker")]),
    (
        "player",
        '<player name="Alice" role="Mafioso">',
        [
            ('<player name="', "marker"),
            ("Alice", "attr"),
            ('" role="', "marker"),
            ("Mafioso", "attr"),
            ('">', "marker"),
        ],
    ),
    (
        "vote",
        '<vote initiator="Alice" target="Bo">',
        [
            ('<vote initiator="', "marker"),
            ("Alice", "attr"),
            ('" target="', "marker"),
            ("Bo", "attr"),
            ('">', "marker"),
        ],
    ),
    ("recap", "<recap>", [("<recap>", "marker")]),
    (
        "thought",
        '<thought player="Alice">',
        [
            ('<thought player="', "marker"),
            ("Alice", "attr"),
            ('">', "marker"),
        ],
    ),
    # Spec 039's thirteenth, and the only row here not written by spec 038.
    # Structurally ``<thought>``'s shape with a second attribute: the ``player``
    # is side-bearing (achromatic in THIS input, which carries no ``<setup>`` to
    # look Alice up in) and the ``day`` never is.
    (
        "diary",
        '<diary player="Alice" day="2">',
        [
            ('<diary player="', "marker"),
            ("Alice", "attr"),
            ('" day="', "marker"),
            ("2", "attr"),
            ('">', "marker"),
        ],
    ),
)

# The tag name the ``plain`` fallback's "an unrecognised tag" case is written
# against (see :func:`test_unrecognised_text_falls_back_to_a_single_plain_span`),
# named up here rather than inline down there for one reason: the rot guard in
# :func:`test_the_swept_tag_vocabulary_matches_the_tokenizers_whitelist` can then
# check that it is STILL unrecognised.
#
# **It was ``diary`` until spec 039, which is the whole point of naming it.**
# Spec 038 chose ``<diary>`` for that case precisely because it stood for "the
# next format change"; the next format change then arrived and recognised the
# tag, turning the case from a test of the fallback into a test of its opposite.
# It failed loudly only because the expectation was absolute. ``whisper`` is the
# replacement — a plausible next element that ``graphia.tools.eval_transcript``
# does not emit — and the guard is what makes the NEXT such collision a message
# instead of a puzzle.
_UNRECOGNISED_TAG = "whisper"

_TAG_CASES = [
    pytest.param(opening, expected, id=name)
    for name, opening, expected in _STRUCTURAL_TAGS
]
_CLOSING_TAG_CASES = [
    pytest.param(f"</{name}>", id=name) for name, _, _ in _STRUCTURAL_TAGS
]

# The nine tags the writer emits with no attributes at all. Derived from the
# table by shape rather than re-listed, so a tag that GAINS an attribute in a
# later slice moves between the two groups automatically instead of being
# asserted as attribute-free while carrying one.
_ATTRIBUTE_FREE_TAG_CASES = [
    pytest.param(opening, id=name)
    for name, opening, _ in _STRUCTURAL_TAGS
    if '="' not in opening
]


def test_the_swept_tag_vocabulary_matches_the_tokenizers_whitelist() -> None:
    """The thirteen tags below are exactly the ones the tokenizer recognises.

    A ROT GUARD, in the same spirit as ``test_ledger_viewer``'s
    ``_DETAIL_KEYS_SWEPT`` check against ``DetailScreen.BINDINGS``: without it a
    fourteenth tag could be added to the tokenizer and never swept by the case
    tables below, and the "each recognised marker" coverage this task owes would
    quietly become "each marker somebody remembered". It has fired once for real
    now — spec 039's ``<diary>`` is the thirteenth, and this is where that slice's
    author was sent.

    Reaches for the module-private ``_MARKER_TAGS`` deliberately — it is the
    whitelist itself, and there is no public projection of it. The count is
    pinned absolutely as well, because a set comparison against a table derived
    from the same source could not catch both sides shrinking together.

    The 9 / 4 split is pinned too (Slice 2, widened by spec 039): the derived
    ``_ATTRIBUTE_FREE_TAG_CASES`` is what the "still a single marker span" test
    sweeps, so a table edit that quietly emptied it would make that test vacuous
    rather than failing. Note which half ``<diary>`` joined — the attribute
    carriers — so the attribute-free count is unmoved and a table edit that put
    it in the wrong half fails on the count rather than on the spans.
    """
    whitelist = getattr(eval_ledger, "_MARKER_TAGS", None)
    assert whitelist is not None, (
        "graphia.eval_ledger._MARKER_TAGS is gone — the tag whitelist moved or "
        "was renamed; update _STRUCTURAL_TAGS and this guard together"
    )
    swept = {name for name, _, _ in _STRUCTURAL_TAGS}
    assert swept == set(whitelist)
    assert len(_STRUCTURAL_TAGS) == 13, (
        "spec 038 ratified twelve tags (tasks.md, Slice 1 header note) and spec "
        "039 added `diary`, the thirteenth"
    )
    assert len(_ATTRIBUTE_FREE_TAG_CASES) == 9, (
        "nine of the thirteen tags carry no attribute; four do "
        "(<player>, <vote>, <thought>, <diary>)"
    )
    # ...and the tag the `plain` fallback's unrecognised-tag case is written
    # against is still unrecognised. See `_UNRECOGNISED_TAG`: spec 038 wrote that
    # case as `<diary>secret</diary>`, spec 039 recognised the tag, and the case
    # silently became a test of the opposite of what it claims. This is the guard
    # that turns the next such collision into a sentence.
    assert _UNRECOGNISED_TAG not in whitelist, (
        f"`{_UNRECOGNISED_TAG}` is a recognised tag now, so the `plain` "
        "fallback's unrecognised-tag case no longer tests the fallback — pick a "
        "tag name the writer does not emit and update `_UNRECOGNISED_TAG`"
    )


@pytest.mark.parametrize(("source", "expected"), _TAG_CASES)
def test_each_structural_tag_line_yields_its_exact_spans(
    source: str, expected: list[tuple[str, str]]
) -> None:
    """A recognised opening tag alone on a line, pinned span for span.

    Covers all twelve — including the four (``player``, ``vote``, ``recap``,
    ``thought``) that carry attributes or content and that a reader of the
    tech-spec's eight-tag prose might expect to be ``plain``. They are not, and
    this is the test that says so.

    **Superseded Slice 1's "…is a single marker span" for three of them.** Since
    Slice 2 a detail-carrying tag alternates marker / attr / marker: the value is
    lifted out so a reviewer's eye lands on *who*, while the key, the quotes and
    the angle brackets stay part of the quiet skeleton (functional-spec §2,
    "the specifics are readable at a glance rather than buried in punctuation").
    The other nine are unchanged — see the next test, which says so on purpose.
    """
    assert _spans(source) == expected


@pytest.mark.parametrize("source", _ATTRIBUTE_FREE_TAG_CASES)
def test_an_attribute_free_tag_line_is_still_a_single_marker_span(
    source: str,
) -> None:
    """A tag with no attributes is ONE span — the attribute split left no debris.

    Named explicitly by Slice 2's test task, and worth its own test rather than
    being left implicit in the table above: the split in ``_tag_head_spans``
    emits its pieces without checking for emptiness and relies on
    ``_coalesce_spans`` to drop the empties, so the failure mode it guards
    against is not "the wrong kind" but "an extra span". A ``('', 'marker')``
    wedged into ``<day>`` would round-trip perfectly, satisfy every
    kind-containment check, and hand the UI a zero-width run to paint.

    Both halves are asserted: the exact span list, and the count on its own so
    the failure message says *how many* spans came back.
    """
    spans = _spans(source)
    assert len(spans) == 1, f"{source!r} split into {spans}"
    assert spans == [(source, eval_ledger.KIND_MARKER)]


@pytest.mark.parametrize("source", _CLOSING_TAG_CASES)
def test_each_structural_closing_tag_line_is_a_single_marker_span(
    source: str,
) -> None:
    """``</tag>`` alone on a line is one ``marker`` span, for all twelve tags.

    A closing form never carries an attribute, so Slice 2 changed nothing here —
    and that is worth keeping asserted: an attribute splitter that fired on
    ``</player>`` would be splitting punctuation that holds no specific at all.
    """
    assert _spans(source) == [(source, eval_ledger.KIND_MARKER)]


# ---------------------------------------------------------------------------
# `attr` — the VALUE inside a marker, never the key, the quotes or the tag
# (spec 038, Slice 2)
# ---------------------------------------------------------------------------
#
# functional-spec §2: "Markers that carry details show those details
# distinguishably… the name and the role are distinguishable from the surrounding
# marker text… both names are picked out from the marker." The tech spec's §2 A
# table fixes the boundary precisely — "the **values** of `name=` / `role=` /
# `player=` / `initiator=` / `target=` inside a marker — the value ONLY, never the
# `name=` key or the quotes" — and that boundary is what these tests pin, because
# it is the one thing a reviewer sees and the one thing Slice 4 builds on (it
# re-kinds the `role=` value and the thought owner's name to their side kinds and
# must not have to re-derive where a value starts).


def test_a_cast_entry_tag_picks_out_the_name_and_the_role() -> None:
    """``<player name="…" role="…">`` → two ``attr`` spans, one per value.

    The five-span pin is the whole requirement in one line. The follow-up
    assertions say the same thing from the other side — which is what stops a
    tokenizer that marked ``name="Avery"`` (key and quotes included) from
    looking correct: it would still produce five spans in the right order.
    """
    spans = _spans('<player name="Avery" role="Mafioso">')

    assert spans == [
        ('<player name="', "marker"),
        ("Avery", "attr"),
        ('" role="', "marker"),
        ("Mafioso", "attr"),
        ('">', "marker"),
    ]
    # Two values, in the tag's own order, and nothing but the values.
    assert [text for text, kind in spans if kind == "attr"] == ["Avery", "Mafioso"]


def test_a_vote_tag_picks_out_the_initiator_and_the_target() -> None:
    """``<vote initiator="…" target="…">`` → both names, as separate spans.

    functional-spec §2's second acceptance criterion for this requirement, and
    the reason it is a separate test from the cast entry: a reviewer scanning for
    "who moved against whom" reads this tag far more often than a cast entry, and
    the two attributes are a different pair of names from ``name``/``role``.
    """
    spans = _spans('<vote initiator="Alice" target="Bo">')

    assert spans == [
        ('<vote initiator="', "marker"),
        ("Alice", "attr"),
        ('" target="', "marker"),
        ("Bo", "attr"),
        ('">', "marker"),
    ]
    assert [text for text, kind in spans if kind == "attr"] == ["Alice", "Bo"]


def test_a_thought_tag_picks_out_the_owners_name() -> None:
    """``<thought player="X">`` → the owner's name, alone, as one ``attr`` span.

    The single-attribute shape, and by far the most common one in the corpus
    (8,183 of the 14,113 attribute values are a thought's ``player``). Slice 4
    re-kinds exactly this span to the owner's side kind, so where it begins and
    ends is that slice's starting point.
    """
    spans = _spans('<thought player="Alice">')

    assert spans == [
        ('<thought player="', "marker"),
        ("Alice", "attr"),
        ('">', "marker"),
    ]
    assert [text for text, kind in spans if kind == "attr"] == ["Alice"]


# ---------------------------------------------------------------------------
# `<diary player="X" day="N">` — spec 039's element
# ---------------------------------------------------------------------------
#
# The thirteenth tag, the sixth attribute and the twenty-first kind, all landing
# together. Structurally the tag is `<thought>`'s shape with a second attribute,
# which is what let the production change be four lines; these tests are the
# per-shape pins that a four-line change still owes.
#
# THE TWO SHAPES ARE BOTH REAL, and only one of them looks like the format.
# `eval_transcript._diary_day_attr` returns `""` for a record whose `day` is
# absent, `None`, a `bool`, a float or a container, and `_append_diaries` then
# OMITS the attribute and renders the entry anyway — "a missing or nonsensical
# `day` still leaves an entry worth showing" (tech-spec 039 §2.8, on the same
# reasoning §2.10 gives for rendering an absent ledger arm blank rather than
# `false`). So `<diary player="Ava" day="2">` and `<diary player="Ava">` both
# reach the reading view and both are pinned below.


def test_a_diary_tag_picks_out_the_owner_and_the_day() -> None:
    """``<diary player="X" day="N">`` → two ``attr`` spans, the owner and the Day.

    Spec 039's tag, and the first two-attribute shape whose values are different
    KINDS of thing: a ``<player>`` pairs a name with a role and a ``<vote>`` pairs
    two names, but a ``<diary>`` names a person and a Day. Both are lifted — the
    Day is what tells one of a player's diaries from the next — and everything
    between and around them stays ``marker``, exactly as for the other two.

    Bare input with no ``<setup>``, so ``Alice`` has no known side and keeps the
    achromatic ``attr``. The side-bearing form is
    :func:`test_a_diary_owner_in_the_cast_list_takes_their_side` in section 3.
    """
    spans = _spans('<diary player="Alice" day="2">')

    assert spans == [
        ('<diary player="', "marker"),
        ("Alice", "attr"),
        ('" day="', "marker"),
        ("2", "attr"),
        ('">', "marker"),
    ]
    # Two values, in the tag's own order, and nothing but the values — the same
    # from-the-other-side statement the cast-entry test makes, and what stops a
    # tokenizer that lifted `day="2"` whole from looking correct on the count.
    assert [text for text, kind in spans if kind == "attr"] == ["Alice", "2"]


def test_a_diary_element_claims_its_body_and_keeps_both_tags_marker() -> None:
    """The whole element, pinned span for span (spec 039, tech-spec §2.8).

    Three structural claims in one expectation: the body is exactly one span of
    its own, both tags stay ``marker``, and the two attribute values are lifted
    out of the punctuation holding them.

    **The body's kind is the point — ``diary`` and not ``thought``.** The two are
    neighbours (both private, both muted, both never side-tinted) and reusing
    ``thought`` would have been one character less work. It is refused because a
    thought is one player's reaction inside a single speaking round and a diary
    is that player's settled read of a whole Day: collapsing them would make the
    Day's trailer indistinguishable from the round bodies above it, which is the
    one thing the trailer's placement exists to say.
    """
    source = '<diary player="Alice" day="2">Bo folded under pressure.</diary>'
    spans = _spans(source)

    assert spans == [
        ('<diary player="', "marker"),
        ("Alice", "attr"),
        ('" day="', "marker"),
        ("2", "attr"),
        ('">', "marker"),
        ("Bo folded under pressure.", "diary"),
        ("</diary>", "marker"),
    ]
    # Restated structurally, so a later re-split of the opening tag cannot
    # quietly take the body with it, and named by constant so a regression
    # reports WHICH rule broke rather than only that a list differs.
    assert spans[-2] == ("Bo folded under pressure.", eval_ledger.KIND_DIARY)
    assert spans[-1] == ("</diary>", eval_ledger.KIND_MARKER)
    assert eval_ledger.KIND_THOUGHT not in {kind for _, kind in spans}
    assert "".join(text for text, _ in spans) == source


def test_a_diary_carrying_only_its_owner_still_tokenizes_cleanly() -> None:
    """The ``player``-only shape, which the writer really does emit.

    Its own test rather than a second parameter on the case above, because the
    failure it guards against is a splitter that ASSUMES the pair: a ``day``-less
    tag must come back as five spans, not six with an empty one wedged in where
    the missing value would have gone. The no-empty-span assertion is what says
    so, and it is the half a span-list equality alone would not report clearly.

    Both halves of the element are still claimed — the owner is lifted and the
    body is still ``diary`` — so a reviewer reading a Day whose record lost its
    number sees an entry with an unknown Day, not an unstyled paragraph.
    """
    source = '<diary player="Alice">Bo folded under pressure.</diary>'
    spans = _spans(source)

    assert spans == [
        ('<diary player="', "marker"),
        ("Alice", "attr"),
        ('">', "marker"),
        ("Bo folded under pressure.", "diary"),
        ("</diary>", "marker"),
    ]
    assert all(text for text, _ in spans), f"a span has empty text: {spans}"
    assert "".join(text for text, _ in spans) == source


def test_a_diarys_body_is_not_re_split_as_speech() -> None:
    """A diary quoting a name and a colon stays ONE ``diary`` span.

    The control ``test_a_thoughts_body_is_not_re_split_as_speech`` is for spec
    028's thoughts, restated here because a diary is far likelier to trip it: it
    is invited to run to ``prompts.DIARY_SENTENCE_BOUND`` (six) sentences of a
    player's own prose about what other players said, where a thought is one or
    two. ``Bo: I saw nothing.`` is shaped exactly like a spoken line and would
    split into ``speaker`` + ``speech`` if the body were ever re-tokenized.

    It is not, and the reason is the branch order rather than anything about
    diaries: :func:`_tag_element_spans` claims the whole line before the speaker
    rule is reached, and an inline body is emitted as one span without a second
    pass. Pinned so a "helpful" recursive tokenizer fails here.
    """
    spans = _spans('<diary player="Alice" day="2">Bo: I saw nothing.</diary>')

    assert spans[-2] == ("Bo: I saw nothing.", eval_ledger.KIND_DIARY)
    assert not {kind for _, kind in spans} & (
        set(_SPEAKER_KINDS) | set(_SPEECH_KINDS)
    ), f"the diary body was re-split as speech: {spans}"


def test_the_day_attribute_is_lifted_on_any_tag_that_carries_it() -> None:
    """``_ATTR_NAMES`` is a GLOBAL whitelist, not a diary-scoped rule.

    Tech-spec 039 §2.8 says so in as many words — "``day`` joins ``_ATTR_NAMES``,
    which is a **GLOBAL** whitelist, not a diary-scoped rule … any tag carrying
    ``day="…"`` will now split" — and records that the blast radius is nil today
    (``day="`` occurs zero times across the 298 committed transcripts). It is
    still a global change rather than a local one, and pinning it is what keeps a
    later "let us scope this to ``<diary>``" edit a decision somebody takes
    rather than one that happens quietly to a whitelist.

    The value stays **achromatic** on the foreign tag too, which is the second
    half: :func:`eval_ledger._attr_kind` decides per ``(tag, key)`` PAIR and only
    three pairs carry a side, so a ``day`` on a ``<vote>`` gets exactly as much
    colour as a ``day`` on a ``<diary>`` — none.
    """
    spans = _spans('<vote initiator="Vera" target="Iris" day="2">')

    assert spans == [
        ('<vote initiator="', "marker"),
        ("Vera", "attr"),
        ('" target="', "marker"),
        ("Iris", "attr"),
        ('" day="', "marker"),
        ("2", "attr"),
        ('">', "marker"),
    ]


# Every one of the six whitelisted attributes, in a tag the writer really emits
# it on, with the value it must lift out. Swept so no attribute is covered only
# by accident of appearing beside another.
#
# `day` is spec 039's, and it is the only value in the table that is not a name:
# a `<diary>` says whose the entry is and which Day it sums up, and the Day is
# the thing that tells one of a player's diaries from the next. It is also the
# only one with zero occurrences in the committed corpus, so this row is the
# whole of its "the value really is lifted" coverage.
_ATTR_NAME_CASES: tuple[tuple[str, str, str], ...] = (
    ("name", '<player name="Avery" role="Mafioso">', "Avery"),
    ("role", '<player name="Avery" role="Mafioso">', "Mafioso"),
    ("player", '<thought player="Avery">', "Avery"),
    ("initiator", '<vote initiator="Avery" target="Bo">', "Avery"),
    ("target", '<vote initiator="Avery" target="Bo">', "Bo"),
    ("day", '<diary player="Avery" day="2">', "2"),
)


def test_the_swept_attribute_names_match_the_tokenizers_whitelist() -> None:
    """The six attributes swept below are exactly the ones the tokenizer lifts.

    The same rot guard the tag vocabulary gets, for the same reason: a seventh
    attribute added to ``_ATTR_NAMES`` and never swept would ship untested, and
    the "distinct ``attr`` spans for both values" coverage this task owes would
    become "the two values somebody remembered".

    It fired for real on spec 039's ``day``, which is the sixth — and the one the
    corpus cannot cover at all, since ``day="`` occurs nowhere in the 298
    committed transcripts. For that attribute this sweep and its row in
    ``_ATTR_NAME_CASES`` are the only coverage there is.
    """
    whitelist = getattr(eval_ledger, "_ATTR_NAMES", None)
    assert whitelist is not None, (
        "graphia.eval_ledger._ATTR_NAMES is gone — the attribute whitelist moved "
        "or was renamed; update _ATTR_NAME_CASES and this guard together"
    )
    assert {name for name, _, _ in _ATTR_NAME_CASES} == set(whitelist)
    assert len(_ATTR_NAME_CASES) == 6


@pytest.mark.parametrize(
    ("source", "value"),
    [
        pytest.param(source, value, id=name)
        for name, source, value in _ATTR_NAME_CASES
    ],
)
def test_each_whitelisted_attributes_value_becomes_an_attr_span(
    source: str, value: str
) -> None:
    """Every one of the five lifts its value out, and lifts *only* the value.

    Three assertions, each catching something the others do not:

    * the value really is one of the spans, kinded ``attr`` — the requirement;
    * the marker before it ends with ``="`` and the marker after it starts with
      ``"`` — the boundary, which is what fails if the quotes are swallowed into
      the value or left dangling in a span of their own;
    * no ``attr`` span contains a quote or an equals sign — the same boundary
      stated so a *partial* over-capture (``Avery"`` say) cannot slip through.
    """
    spans = _spans(source)
    attrs = [(index, text) for index, (text, kind) in enumerate(spans) if kind == "attr"]

    assert value in [text for _, text in attrs], (
        f"{value!r} is not an attr span of {source!r}: {spans}"
    )
    for index, text in attrs:
        assert '"' not in text and "=" not in text, (
            f"attr span {text!r} swallowed part of its own punctuation"
        )
        # A value always sits between the two halves of its own punctuation, so
        # neither neighbour can be missing.
        assert 0 < index < len(spans) - 1, (
            f"attr span {text!r} is at the edge of {spans} — it has no "
            "surrounding marker"
        )
        before, after = spans[index - 1], spans[index + 1]
        assert before[1] == "marker" and before[0].endswith('="'), (
            f"the span before {text!r} is {before!r}, expected a marker ending "
            'with `="`'
        )
        assert after[1] == "marker" and after[0].startswith('"'), (
            f"the span after {text!r} is {after!r}, expected a marker starting "
            "with the closing quote"
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<player name="Alice" role="Mafioso" human="true">',
            [
                ('<player name="', "marker"),
                ("Alice", "attr"),
                ('" role="', "marker"),
                ("Mafioso", "attr"),
                ('" human="true">', "marker"),
            ],
            id="slice-5-human-flag",
        ),
        pytest.param(
            '<player nickname="Ace">',
            [('<player nickname="Ace">', "marker")],
            id="lookbehind-nickname",
        ),
        pytest.param(
            '<diary player="Ava" birthday="2">',
            [
                ('<diary player="', "marker"),
                ("Ava", "attr"),
                ('" birthday="2">', "marker"),
            ],
            id="lookbehind-birthday",
        ),
    ],
)
def test_an_attribute_outside_the_whitelist_stays_inside_the_marker(
    source: str, expected: list[tuple[str, str]]
) -> None:
    """Only the five whitelisted attributes are lifted; anything else is skeleton.

    Two cases, both deliberate rather than incidental:

    * ``human="true"`` is the writer marker Slice 5 adds to the person's seat,
      and ``tasks.md`` ratifies that it is **deliberately not** in ``_ATTR_NAMES``
      — a machine flag is not a specific a reviewer reads. Pinning it now means
      Slice 5 finds the decision already asserted instead of rediscovering it.
    * ``nickname="Ace"`` is the lookbehind: a naive ``name="`` search would split
      this tag at the tail of ``nickname``, marking ``Ace`` as a player's name.
    * ``birthday="2"`` is spec 039's stake in that same lookbehind, and the
      reason it is pinned here rather than left implied. ``day`` is a **global**
      whitelist entry from spec 039 on — any tag carrying ``day="…"`` splits —
      and ``birthday`` is the shortest real word that ends in it. Nothing but
      :data:`eval_ledger._ATTR_VALUE_RE`'s ``(?<![A-Za-z0-9_-])`` stops that
      ``"2"`` becoming an ``attr`` span, and 49 lines of the committed corpus say
      "birthday" in prose already. The owner beside it still splits, so this is
      also the control: the tag is being parsed, and only the wrong half of it is
      being left alone.
    """
    assert _spans(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<player name="" role="Mafioso">',
            [
                ('<player name="" role="', "marker"),
                ("Mafioso", "attr"),
                ('">', "marker"),
            ],
            id="one-empty",
        ),
        pytest.param(
            '<player name="" role="">',
            [('<player name="" role="">', "marker")],
            id="both-empty",
        ),
        pytest.param(
            '<thought player="">',
            [('<thought player="">', "marker")],
            id="only-attribute-empty",
        ),
    ],
)
def test_an_empty_attribute_value_leaves_no_stray_span(
    source: str, expected: list[tuple[str, str]]
) -> None:
    """``name=""`` contributes nothing, and the markers around it merge back.

    The other half of Slice 2's "the attribute split must not leave stray empty
    spans". ``_tag_head_spans`` emits its pieces unconditionally and lets
    ``_coalesce_spans`` drop the empties at the single exit point — so an empty
    value is the input most likely to produce a zero-width span, and a tokenizer
    that stopped coalescing would leak one here while every other test still
    passed (an empty span is invisible to the round trip by construction).

    A name is model-generated, so an empty one is not merely theoretical.
    """
    spans = _spans(source)
    assert all(text for text, _ in spans), f"a span has empty text: {spans}"
    assert spans == expected
    # ...and coalescing really did merge, rather than leaving two markers.
    adjacent = [a for (_, a), (_, b) in zip(spans, spans[1:], strict=False) if a == b]
    assert not adjacent, f"adjacent spans share a kind (uncoalesced): {spans}"


# ---------------------------------------------------------------------------
# The metadata header and the bare `Round N.` label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(
            "Game 1 | provider=ollama | large_model=qwen3-coder:30b | "
            "small_model=qwen2.5:3b | games=50",
            id="full",
        ),
        pytest.param("Game 12 | provider=bedrock", id="short"),
        # `_header` joins only the parts the run metadata actually has, and
        # `Game N` is the only one always present.
        pytest.param("Game 7", id="bare"),
    ],
)
def test_the_top_metadata_line_is_a_marker(header: str) -> None:
    """The single information line at the very top of a game is skeleton.

    Functional-spec §2 treats it as skeleton alongside the section markers, so
    it must not read as content.
    """
    assert _spans(header) == [(header, eval_ledger.KIND_MARKER)]


def test_a_metadata_shaped_line_below_the_first_is_plain() -> None:
    """The header is a **positional** fact, not a shape any line can have.

    A player saying "Game 4 | provider=ollama" mid-transcript is speech, and
    dimming it would misrepresent the game. Pinned as the complete span list of a
    three-line document so the metadata-shaped middle line is visibly folded into
    its neighbouring separators as plain text.
    """
    source = "<day>\nGame 4 | provider=ollama\n</day>"
    assert _spans(source) == [
        ("<day>", eval_ledger.KIND_MARKER),
        ("\nGame 4 | provider=ollama\n", eval_ledger.KIND_PLAIN),
        ("</day>", eval_ledger.KIND_MARKER),
    ]


def test_the_bare_round_label_is_a_marker() -> None:
    """``Round 3.`` inside a Day is skeleton (functional-spec §2)."""
    assert _spans("Round 3.") == [("Round 3.", eval_ledger.KIND_MARKER)]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("Round 3. I accuse Bo.", id="sentence-that-starts-that-way"),
        pytest.param("Round 3", id="no-full-stop"),
        pytest.param("round 3.", id="lowercase"),
        pytest.param("Round three.", id="spelled-out"),
        pytest.param("Round 3. ", id="trailing-space"),
    ],
)
def test_a_line_merely_resembling_the_round_label_is_plain(source: str) -> None:
    """The label is anchored end to end, so near-misses stay content.

    The dangerous one is the first: a Day speech beginning "Round 3. I accuse
    Bo." would, under a prefix match, have the reviewer's eye told that a whole
    accusation is scaffolding.
    """
    assert _spans(source) == [(source, eval_ledger.KIND_PLAIN)]


# ---------------------------------------------------------------------------
# Inline elements: three spans, and the one exception
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "content", "content_kind", "expected"),
    [
        pytest.param(
            '<player name="Bo" role="Law-abiding Citizen">'
            "(no persona recorded)</player>",
            "(no persona recorded)",
            "plain",
            [
                ('<player name="', "marker"),
                ("Bo", "attr"),
                ('" role="', "marker"),
                # Slice 4: the ROLE takes the side, the NAME stays achromatic —
                # and it does so with no `<setup>` in this input at all, because
                # a role's side is read from the label written right there
                # rather than looked up in the cast map.
                ("Law-abiding Citizen", "attr-law-abiding"),
                ('">', "marker"),
                ("(no persona recorded)", "plain"),
                ("</player>", "marker"),
            ],
            id="player",
        ),
        pytest.param(
            "<recap>Alive: Alice, Bo. Mafia 1, Law-abiding 1.</recap>",
            "Alive: Alice, Bo. Mafia 1, Law-abiding 1.",
            "recap",
            [
                ("<recap>", "marker"),
                ("Alive: Alice, Bo. Mafia 1, Law-abiding 1.", "recap"),
                ("</recap>", "marker"),
            ],
            id="recap",
        ),
        pytest.param(
            '<thought player="Alice">Bo suspects me.</thought>',
            "Bo suspects me.",
            "thought",
            [
                ('<thought player="', "marker"),
                ("Alice", "attr"),
                ('">', "marker"),
                ("Bo suspects me.", "thought"),
                ("</thought>", "marker"),
            ],
            id="thought",
        ),
    ],
)
def test_an_inline_element_keeps_its_content_a_span_of_its_own(
    source: str,
    content: str,
    content_kind: str,
    expected: list[tuple[str, str]],
) -> None:
    """``<tag …>content</tag>`` splits the content off from both of its tags.

    Ratified during Slice 1, when this read "yields THREE spans". Slice 2
    superseded the count — a detail-carrying opening tag is now marker / attr /
    marker, so ``<player …>…</player>`` is seven spans and ``<thought …>…`` five
    — but not the property the count stood for, which is restated below in a form
    no later slice's split can invalidate: **the content is exactly one span, the
    second from last, with the closing tag behind it.**

    **Slice 3 moved the content KIND on two of the three cases and left the
    structure untouched** — which is the whole point of having written the
    property this way. ``<recap>``'s body is now ``recap`` and a thought's is now
    ``thought``; ``<player>``'s ``(no persona recorded)`` stays ``plain``, because
    it is prose rather than a kind of its own. The tags around all three stay
    ``marker`` (tasks.md, Slice 3: "a thought's content is ``thought`` and its
    surrounding tag stays ``marker``").

    **Slice 4 moved one kind INSIDE an opening tag and left every boundary where
    it was** — the third time this test has survived a re-kinding untouched in
    its structural half, which is what it was written for. The ``player`` row's
    ``Law-abiding Citizen`` is now ``attr-law-abiding``: a role's side is read
    from the label written *in the tag*, so it needs no ``<setup>`` and this
    bare one-line input gets it.

    The ``thought`` row's owner name stays plain ``attr``, and **the reason is no
    longer "Slice 4 has not happened yet"** — that was true when this docstring
    was written and is stale prose now. A ``<thought player="X">`` owner's side
    *is* a map lookup, and this input carries no ``<setup>`` at all, so the map is
    empty and the name has no known side. Never a guess: that is the correct
    answer, not a missing feature. The side-bearing form of the same tag, and its
    unknown-owner control, are pinned in
    :func:`test_a_thought_owner_in_the_cast_list_takes_their_side` below.

    ``content_kind`` is a parameter rather than a constant precisely so the
    ``player`` row keeps proving that claiming a body is per-tag opt-in and not a
    blanket "every inline body gets its tag's kind".
    """
    spans = _spans(source)

    assert spans == expected
    # Restated structurally, so this survives a later slice's re-split of the
    # opening tag and fails loudly on the one thing that must not change.
    assert spans[-2] == (content, content_kind)
    assert spans[-1][1] == eval_ledger.KIND_MARKER
    assert spans[-1][0].startswith("</")
    assert spans[0][1] == eval_ledger.KIND_MARKER
    assert spans[0][0].startswith("<") and not spans[0][0].startswith("</")
    assert [text for text, _ in spans].count(content) == 1, (
        "the content must be ONE span, not several"
    )


def test_a_kill_element_coalesces_to_a_single_marker_span() -> None:
    """``<kill>`` is the exception: its content is marker too, so it merges.

    The tech-spec §2 A table lists ``<kill>`` plainly among the markers and no
    later slice reclassifies a night kill's ``Name — Side`` payload, so the whole
    line reads as one piece of skeleton.
    """
    source = "<kill>Avery — Law-abiding Citizen</kill>"
    assert _spans(source) == [(source, eval_ledger.KIND_MARKER)]


def test_the_kill_coalescing_is_specific_to_kill_not_general_to_inline_tags() -> None:
    """The non-vacuity control for the test above.

    The identical payload inside any other inline tag must still be three spans.
    Without this, "kill is one span" would also pass on a tokenizer that had
    silently stopped splitting inline elements at all — and Slices 2-3 depend on
    that split existing.

    Slice 3 moved the control's middle span from ``plain`` to ``recap`` (the
    control tag is a ``<recap>``); the property it controls for is unchanged and
    is if anything sharper now, because ``kill`` and ``recap`` differ by kind as
    well as by span count.
    """
    source = "<recap>Avery — Law-abiding Citizen</recap>"
    assert _spans(source) == [
        ("<recap>", eval_ledger.KIND_MARKER),
        ("Avery — Law-abiding Citizen", eval_ledger.KIND_RECAP),
        ("</recap>", eval_ledger.KIND_MARKER),
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<night></night>", id="section"),
        # Slice 3's two content-claiming tags, degenerate. `<recap></recap>` and
        # an empty `<thought>` are the shapes where a body-claiming branch is
        # most likely to emit a zero-length `recap` / `thought` span: the corpus
        # contains neither, so nothing but this test can see it.
        pytest.param("<recap></recap>", id="empty-recap"),
        pytest.param('<thought player="X"></thought>', id="empty-thought"),
        # Spec 039's third content-claiming tag, degenerate the same way.
        # `_append_diaries` skips a record whose `text` is blank, so the writer
        # never emits this and the corpus can never contain it — which is exactly
        # why nothing but this row can see a zero-length `diary` span.
        pytest.param('<diary player="X" day="1"></diary>', id="empty-diary"),
    ],
)
def test_an_empty_element_contributes_no_inner_span(source: str) -> None:
    """An element with nothing between its tags leaves no empty span behind.

    ``<night></night>`` is what the writer emits for a section that captured
    nothing: two adjacent markers, and therefore ONE span after coalescing, with
    no empty span wedged between them.

    Asserted as "no span has empty text" plus the round trip rather than as a
    literal expectation, because the three cases coalesce differently — the
    attribute-free two merge into a single marker, while the thought's attr span
    keeps its tags apart. What must hold for all three is the guarantee.
    """
    spans = _spans(source)

    assert "".join(text for text, _ in spans) == source
    assert all(text for text, _ in spans), f"an empty span survived: {spans}"
    assert eval_ledger.KIND_THOUGHT not in {kind for _, kind in spans}
    assert eval_ledger.KIND_RECAP not in {kind for _, kind in spans}
    assert eval_ledger.KIND_DIARY not in {kind for _, kind in spans}


def test_an_empty_section_element_is_one_coalesced_marker_span() -> None:
    """...and the attribute-free case really does coalesce to a single span.

    The absolute half of the guarantee above: without it, "no empty span" would
    also pass on a tokenizer that emitted ``<night>`` and ``</night>`` as two
    separate marker spans, which would violate the no-adjacent-same-kind rule.
    """
    assert _spans("<night></night>") == [
        ("<night></night>", eval_ledger.KIND_MARKER)
    ]


@pytest.mark.parametrize(
    ("source", "tag", "remainder"),
    [
        pytest.param(
            '<thought player="Alice">it runs on',
            [
                ('<thought player="', "marker"),
                ("Alice", "attr"),
                ('">', "marker"),
            ],
            "it runs on",
            id="thought",
        ),
        pytest.param(
            "<recap>it runs on",
            [("<recap>", "marker")],
            "it runs on",
            id="recap",
        ),
        # Spec 039's, and the one shape here that is not merely hypothetical.
        # Tech-spec 039 §2.8 names this degradation by name: a diary is invited
        # to run to `DIARY_SENTENCE_BOUND` sentences, so if the clamp's
        # whitespace fold ever stopped folding, a model's blank line would
        # produce the format's first multi-line inline element. What that must
        # cost is the BODY's kind, never the round trip — "an unmatched open tag
        # reads as plain text and the round-trip invariant holds", which is why
        # the fold is robustness rather than a fix for a defect.
        pytest.param(
            '<diary player="Alice" day="2">it runs on',
            [
                ('<diary player="', "marker"),
                ("Alice", "attr"),
                ('" day="', "marker"),
                ("2", "attr"),
                ('">', "marker"),
            ],
            "it runs on",
            id="diary",
        ),
    ],
)
def test_an_unclosed_inline_element_degrades_to_its_tag_plus_plain(
    source: str, tag: list[tuple[str, str]], remainder: str
) -> None:
    """A tag whose content does not close on the same line still never raises.

    The corpus contains no such line today, but a multi-line model-generated
    thought would produce one, and the reviewer must still be able to read it.

    Slice 2 note: the degraded path still splits the attribute out. That is the
    point of doing the split in ``_tag_head_spans`` rather than in the
    closed-element branch — the owner's name is picked out whether or not the
    thought closes on its line, so the fallback is a *degradation of the
    content*, never of the marker.

    **Slice 3's stake in this is the remainder's kind.** Now that ``<thought>``
    and ``<recap>`` bodies are claimed, the tempting implementation is to give
    the unclosed remainder the tag's content kind too. It must not: the extent is
    unknown, so styling it as a private thought would run an italic (and, from
    Slice 4, a side) over however much of the game follows. ``plain`` is the
    documented answer, and the second assertion says so by name rather than only
    by span equality, so a regression reports *which* rule broke.
    """
    spans = _spans(source)

    assert spans == [*tag, (remainder, eval_ledger.KIND_PLAIN)]
    assert eval_ledger.KIND_THOUGHT not in {kind for _, kind in spans}
    assert eval_ledger.KIND_RECAP not in {kind for _, kind in spans}
    assert eval_ledger.KIND_DIARY not in {kind for _, kind in spans}


# ---------------------------------------------------------------------------
# The `plain` fallback — the thing that makes degradation total
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # `Personality: …` moved OUT of this table in Slice 2 — it is now a
        # `field-label` line and has its own section below. What stays here is
        # the near-miss: the same words with anything at all in front of them.
        pytest.param("He said Personality: brisk", id="field-label-mid-line"),
        pytest.param("(no persona recorded)", id="no-persona"),
        pytest.param("(persona has no recorded detail)", id="no-detail"),
        # A Night's pick list, deliberately NOT one of the five cast-list field
        # labels even though it is shaped like one — and, since Slice 3, not a
        # speaker either: `Pointing round 1` carries spaces, so `_SPEAKER_RE`'s
        # whitespace-free name never matches it.
        pytest.param("Pointing round 1: Alice → Bo", id="pointing-round"),
        # A tag the writer does not emit: styled as skeleton it is not would be
        # a guess, so the whitelist declines and the line stays readable.
        #
        # THE TAG NAME IS `_UNRECOGNISED_TAG`, NOT A LITERAL, and the reason is
        # this very case's history. Spec 038 wrote it as `<diary>secret</diary>`
        # — a tag picked to stand for "the next format change" — and spec 039
        # arrived, taught the tokenizer `<diary>`, and turned the case into an
        # assertion that a RECOGNISED tag degrades to plain. It failed loudly
        # only because the expectation is absolute. The name now lives beside the
        # tag table with a guard that checks it is still absent from
        # `_MARKER_TAGS`, so the spec that finally emits a `<whisper>` is told to
        # pick a new placeholder rather than discovering it from a span list.
        pytest.param(
            f"<{_UNRECOGNISED_TAG}>secret</{_UNRECOGNISED_TAG}>",
            id="unrecognised-tag",
        ),
        pytest.param("<Day>", id="wrong-case-tag"),
        pytest.param("<night/>", id="self-closing"),
        # SLICE 3 REMOVED three rows from this table — `Alice: I saw nothing
        # last night.`, `Alice: 3 < 4 and 5 > 2` and `Alice: says [bold] a lot`
        # were all spoken lines pinned as `plain` before the speaker rule
        # existed. They are not deleted: each is now a case in the
        # speaker/speech section below, where the same prose is asserted to
        # split into exactly two spans with the angle brackets and the square
        # brackets still inside the speech. `Moderator: …` left too, for the
        # exclusion-set section that owns it.
    ],
)
def test_unrecognised_text_falls_back_to_a_single_plain_span(source: str) -> None:
    """Everything the chain does not recognise comes back as one ``plain`` span.

    This fallback is what makes the degradation total: an unrecognised tag, a
    future format change, or model-generated prose containing an angle bracket
    all render as ordinary readable text instead of raising or being mis-styled.
    """
    assert _spans(source) == [(source, eval_ledger.KIND_PLAIN)]


# ---------------------------------------------------------------------------
# `field-label` — the cast list's five labels, colon included (spec 038, Slice 2)
# ---------------------------------------------------------------------------
#
# functional-spec §2: "the field labels are distinguishable from the descriptions
# that follow them", so an entry "can be skimmed by field rather than read as a
# paragraph". The label carries its colon and stops there — the space and the
# prose after it are the description, and styling those would defeat the purpose
# (the whole line would be a label). 6,200 of them across the corpus.
#
# The five are verbatim from the writer's `_persona_lines`
# (`graphia.tools.eval_transcript`), which is the format's authority:
# `Personality:` and `Manner:` for everyone, `Public legend:` + `True self
# (hidden):` for a Mafioso's two layers, `Persona:` for a Citizen's single one.

# One case per label, each with a description that could not be mistaken for
# part of it. Written as (label, prose) so the expectation below is built from
# the same two pieces the tokenizer has to separate.
_FIELD_LABEL_CASES: tuple[tuple[str, str, str], ...] = (
    ("personality", "Personality:", "brisk and sly, and too fond of a pause"),
    ("manner", "Manner:", "clipped"),
    ("public-legend", "Public legend:", "the village baker, up before dawn"),
    ("true-self", "True self (hidden):", "the one who opens the door at night"),
    ("persona", "Persona:", "an honest baker with flour on his sleeves"),
)


def test_the_swept_field_labels_match_the_tokenizers_list() -> None:
    """The five labels swept below are exactly the ones the tokenizer knows.

    The third rot guard in this file, for the third whitelist. A sixth label
    added to the writer and to ``_FIELD_LABELS`` but not here would be styled in
    the viewer and untested in the suite.
    """
    labels = getattr(eval_ledger, "_FIELD_LABELS", None)
    assert labels is not None, (
        "graphia.eval_ledger._FIELD_LABELS is gone — the cast-list label list "
        "moved or was renamed; update _FIELD_LABEL_CASES and this guard together"
    )
    assert {label for _, label, _ in _FIELD_LABEL_CASES} == set(labels)
    assert len(_FIELD_LABEL_CASES) == 5
    # Every label carries its colon — the boundary the spans below depend on.
    assert all(label.endswith(":") for label in labels)


@pytest.mark.parametrize(
    ("label", "prose"),
    [
        pytest.param(label, prose, id=case_id)
        for case_id, label, prose in _FIELD_LABEL_CASES
    ],
)
def test_each_cast_list_field_label_is_its_own_span_and_its_prose_is_not(
    label: str, prose: str
) -> None:
    """The label (colon included) is ``field-label``; everything after it is not.

    Both halves are the requirement. A tokenizer that kinded the whole line
    ``field-label`` would satisfy "the label is a field-label span" and destroy
    the reason the kind exists — the eye needs somewhere to *stop*.

    The separating space belongs to the prose deliberately: it is not part of the
    label the writer emits (``f"{Field}: {value}"`` puts the colon in the label
    and the space in front of the value), and a trailing styled space is a
    visible artefact at the left margin of a bolded, tinted run.
    """
    spans = _spans(f"{label} {prose}")

    assert spans == [(label, "field-label"), (f" {prose}", "plain")]
    # Said the other way round, so a change that grew the label into the prose
    # fails with a message about the prose rather than about a span list.
    assert [text for text, kind in spans if kind == "field-label"] == [label]
    assert prose not in "".join(
        text for text, kind in spans if kind == "field-label"
    )


def test_a_bare_field_label_with_no_prose_is_one_span_with_nothing_trailing() -> None:
    """``Personality:`` alone leaves no empty ``plain`` span behind it.

    The label branch slices the body in two and hands both halves on; when the
    second half is empty it must vanish rather than arrive as a zero-width span
    (the same no-stray-span contract the attribute split has).
    """
    spans = _spans("Personality:")
    assert spans == [("Personality:", "field-label")]
    assert all(text for text, _ in spans)


def test_an_indented_field_label_is_recognised_the_same_way() -> None:
    """The 400 pre-spec-022 labels are indented four spaces and still labels.

    Spec 022 writes the cast list flush-left; the three older run dirs indent it.
    Because the indent is split off as its own plain span *before* the label
    branch runs, one rule serves both eras with no format branch — which is the
    same mechanism that makes the old indented ``  <round>`` tags work, restated
    for the kind Slice 2 added.
    """
    assert _spans("    Personality: brisk") == [
        ("    ", "plain"),
        ("Personality:", "field-label"),
        (" brisk", "plain"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("He said Personality: brisk", id="prefixed"),
        pytest.param("Personality brisk", id="no-colon"),
        pytest.param("Personality : brisk", id="space-before-colon"),
    ],
)
def test_a_line_merely_containing_a_label_is_not_a_field_label(source: str) -> None:
    """The label is anchored to the start of the (unindented) line.

    The label branch matches at position 0 of the lstripped body and nowhere
    else, so a word that merely *contains* a label is prose.

    **Slice 3 moved two rows out of this table without dropping what either
    proved.** ``Alice: my Manner: is clipped`` and ``personality: brisk`` are no
    longer whole-line ``plain`` — the first is somebody speaking, the second is
    the accepted casing trade — so each got its own test below, and both still
    assert the thing this table exists for: **no ``field-label`` span is
    produced.** The outer classification moved; the label rule did not.
    """
    spans = _spans(source)
    assert spans == [(source, eval_ledger.KIND_PLAIN)]
    assert eval_ledger.KIND_FIELD_LABEL not in {kind for _, kind in spans}


def test_a_label_said_mid_sentence_is_not_lifted_out_of_the_speech() -> None:
    """``Alice: my Manner: is clipped`` — the dangerous case, re-expressed.

    Ported from ``test_a_line_merely_containing_a_label_is_not_a_field_label``'s
    ``mid-speech`` row, which Slice 3's speaker rule superseded on the OUTER
    classification only: the line is now an utterance rather than anonymous
    prose. **The assertion's own point is untouched and is restated below** —
    a player who says the word "Manner:" mid-sentence must not have half their
    line painted as cast-list scaffolding in the middle of a Day round where no
    cast list exists.

    Two ways, deliberately: the exact spans (so the boundary is pinned at the
    FIRST colon and not the second), and the negative (so the failure names the
    field-label rule if that is what regressed).
    """
    spans = _spans("Alice: my Manner: is clipped")

    assert spans == [
        ("Alice:", eval_ledger.KIND_SPEAKER),
        (" my Manner: is clipped", eval_ledger.KIND_SPEECH),
    ]
    assert eval_ledger.KIND_FIELD_LABEL not in {kind for _, kind in spans}


def test_a_lowercase_field_label_reads_as_speech_and_the_real_one_still_does_not(
) -> None:
    """``personality: brisk`` is speech; ``Personality: brisk`` is still a label.

    Ported from the ``lowercase`` row of the table above, which pinned
    ``personality: brisk`` as one ``plain`` span. Slice 3's speaker rule
    supersedes that, and the change is **deliberate and measured**, not
    collateral: an initial-capital guard on ``_SPEAKER_RE`` was drafted and
    rejected because 39 of the corpus's cast names are lowercase (``mina``,
    ``arthur``, ``kai``, ``zara``, …) and casing would lose **426 real speaker
    lines** to exclude three writer literals. The accepted price is exactly this
    line.

    The second half is what keeps the trade a trade rather than a regression:
    the writer's real cast-list labels are **always capitalised**, so the 6,200
    ``field-label`` spans in the corpus are untouched (verified: the count is
    identical across Slices 2 and 3). Both halves live in one test so neither can
    be edited without seeing the other.
    """
    assert _spans("personality: brisk") == [
        ("personality:", eval_ledger.KIND_SPEAKER),
        (" brisk", eval_ledger.KIND_SPEECH),
    ]
    assert _spans("Personality: brisk") == [
        ("Personality:", eval_ledger.KIND_FIELD_LABEL),
        (" brisk", eval_ledger.KIND_PLAIN),
    ]


def test_the_longest_matching_label_wins() -> None:
    """``Personality:`` is never read as the shorter ``Persona`` plus prose.

    ``_FIELD_LABEL_RE`` alternates longest-first for exactly this reason. The
    two labels genuinely collide only if the colon is ignored, so this pins the
    ordering rather than trusting that no future label pair overlaps.
    """
    assert _spans("Personality: brisk")[0] == ("Personality:", "field-label")
    assert _spans("Persona: brisk")[0] == ("Persona:", "field-label")


def test_a_field_labels_prose_may_contain_square_brackets() -> None:
    """Console-markup-shaped persona prose is prose, label or no label.

    Persona descriptions are model-generated, so ``[`` is one eval run away. The
    tokenizer has no opinion on it — the label is still lifted, the brackets stay
    in the plain remainder, and the widget-level guarantee that they *render*
    literally is B14's job in ``tests/test_ledger_viewer.py``.
    """
    assert _spans("Personality: brisk [bold] and sly") == [
        ("Personality:", "field-label"),
        (" brisk [bold] and sly", "plain"),
    ]


# ---------------------------------------------------------------------------
# `speaker` / `speech` — a spoken line, split at its colon (spec 038, Slice 3)
# ---------------------------------------------------------------------------
#
# functional-spec §2: "a speaker's name and the words they speak both carry
# their side's colour". Slice 3 makes the two halves their own kinds; Slice 4
# gives them a side. The split point is ratified in `tasks.md`: **the colon
# belongs to `speaker`, the separating space to `speech`** — the same convention
# `field-label` already uses, so the file holds one rule rather than two.
#
# THE RULE IS SHAPE-DRIVEN, NOT CAST-LIST-DRIVEN (tech-spec §2 B): the 30 pre-022
# transcripts have no `<player>` tag at all, and their speaker prefixes must
# still be lifted. "Somebody is speaking" is a shape; *which side* they are on is
# a judgement, and that is Slice 4's problem, gated on the cast list, never
# guessed.
#
# WHAT THE CORPUS SWEEP CANNOT SEE. Nearly every case below is a shape 298 real
# games cannot fail on: some (`tally:`, `outcome:`, `Moderator (private to X):`)
# occur thousands of times but are indistinguishable from a pass unless the kind
# is asserted, and others (`Mary Ann: hello`, `Avery:no space`, a bare `Avery:`)
# do not occur at all. Slice 2 proved that gap is real rather than theoretical —
# a mutation that broke the round-trip invariant in principle swept 298 files
# clean. These are the near-misses that close it.

# A spoken line and the two spans it must become. Written as (name-with-colon,
# rest-of-line) so the expectation below is assembled from the same two pieces
# the tokenizer has to separate — a test that spelled the answer out twice could
# agree with itself while both halves were wrong.
_SPOKEN_LINE_CASES: tuple[tuple[str, str, str], ...] = (
    # The ordinary case: what `_append_messages` writes for every Day utterance.
    ("plain-speech", "Alice:", " I saw nothing last night."),
    # THE BALLOT. Spec 022 strips the `Moderator:` voice off a vote precisely so
    # this reads as the player's own word; 5,656 of them in the corpus.
    ("ballot-yes", "Bo:", " Yes"),
    ("ballot-no", "Bo:", " No"),
    # A lowercase-named player: 39 real cast names look like this, which is the
    # measured reason `_SPEAKER_RE` carries no initial-capital guard.
    ("lowercase-name", "zara:", " I saw nothing."),
    # A non-ASCII initial. `[^\W\d_]` is Unicode-aware, so `Sofía` and `Inés`
    # match where a bare `[A-Za-z]` would have dropped them.
    ("non-ascii-name", "Sofía:", " bonjour"),
    ("non-ascii-initial", "Émile:", " bonsoir"),
    # A hyphen is not a word break — the deliberate miss is the SPACE in
    # `Mary Ann`, not the two-part name (see `_NOT_A_SPEAKER_CASES`).
    ("hyphenated-name", "Mary-Ann:", " hello"),
    # Speech that merely contains angle brackets: ported from the plain-fallback
    # table, where Slice 1 pinned the whole line as one `plain` span. The point
    # it made — a `<` in model-generated prose is content, never skeleton — is
    # restated here, now inside the speech span.
    ("angle-brackets-in-speech", "Alice:", " 3 < 4 and 5 > 2"),
    # Console-markup-shaped speech, ported from the same table. The tokenizer has
    # no opinion on `[`; the widget-level guarantee that it RENDERS literally is
    # B14's job in tests/test_ledger_viewer.py.
    ("square-brackets", "Alice:", " says [bold] a lot, and [/bold]"),
    # The boundary of the never-empty guarantee: `_SPEAKER_RE`'s lookahead
    # requires the separating space, so a line that ends immediately after it is
    # still two spans and the second one is that single character. A bare
    # `Avery:` with nothing at all after the colon is the miss (see
    # `_NOT_A_SPEAKER_CASES`) — the space is exactly what separates the two.
    ("nothing-but-the-space", "Avery:", " "),
)


@pytest.mark.parametrize(
    ("speaker", "speech"),
    [
        pytest.param(speaker, speech, id=case_id)
        for case_id, speaker, speech in _SPOKEN_LINE_CASES
    ],
)
def test_a_spoken_line_splits_into_speaker_and_speech(
    speaker: str, speech: str
) -> None:
    """``Avery: hi`` → ``('Avery:', speaker)``, ``(' hi', speech)``.

    The complete span list, so the **colon and the space are both accounted
    for**: the colon is the last character of the speaker span and the separating
    space is the first character of the speech span. A tokenizer that dropped
    either, or that put the space on the speaker's side, fails here rather than
    losing a character invisibly.
    """
    spans = _spans(speaker + speech)

    assert spans == [
        (speaker, eval_ledger.KIND_SPEAKER),
        (speech, eval_ledger.KIND_SPEECH),
    ]
    # Spelled out, so a failure names WHICH end of the boundary moved.
    assert spans[0][0].endswith(":")
    assert spans[1][0].startswith(" ")
    assert "".join(text for text, _ in spans) == speaker + speech


def test_a_ballot_is_speaker_and_speech_not_a_kind_of_its_own() -> None:
    """THE BALLOT DECISION, pinned (tasks.md, Slice 3; and the tokenizer docstring).

    Spec 022 strips the ``Moderator:`` voice off each vote, so a ``<vote>``
    block's ``Bo: Yes`` is shaped exactly like ordinary speech. It is kinded
    exactly like ordinary speech too — deliberately, and the reason is Slice 4:
    colour means side from there on, so a vote block will show at a glance that
    both Mafiosos voted No. A separate achromatic ``ballot`` kind would throw
    that away, and telling one apart would need to know the line sits inside a
    ``<vote>`` element, which this per-line stateless tokenizer does not.

    Asserted three ways, because the decision is what is being pinned and not
    just today's output: the ballot's spans, the identity of those spans with an
    ordinary utterance's (so a future ``ballot`` kind fails here rather than
    quietly appearing), and the absence of any such kind from the vocabulary.
    """
    ballot = _spans("Bo: Yes")
    speech = _spans("Bo: I agree")

    assert ballot == [
        ("Bo:", eval_ledger.KIND_SPEAKER),
        (" Yes", eval_ledger.KIND_SPEECH),
    ]
    assert [kind for _, kind in ballot] == [kind for _, kind in speech]
    assert "ballot" not in eval_ledger.TRANSCRIPT_KINDS


# The writer's own line prefixes that are shaped exactly like a speaker, each
# with a realistic line. `Moderator:` is the public moderator voice (2,655 lines);
# `tally:` and `outcome:` are the two fields every `<vote>` block ends with
# (1,005 of each). All three are frequent in the corpus AND invisible to the
# round-trip sweep, which never looks at a kind.
_NON_SPEAKER_PREFIX_LINES: tuple[tuple[str, str, str], ...] = (
    ("moderator", "Moderator", "Moderator: A new game begins. Welcome, Avery."),
    ("tally", "tally", "tally: 3 Yes, 3 No"),
    ("outcome", "outcome", "outcome: failed — The vote fails."),
)


def test_the_swept_non_speaker_prefixes_match_the_tokenizers_exclusion_set() -> None:
    """The three prefixes swept below are exactly the ones the tokenizer excludes.

    The fourth rot guard in this file, for the fourth whitelist (after
    ``_MARKER_TAGS``, ``_ATTR_NAMES`` and ``_FIELD_LABELS``). A fourth exclusion
    added to the tokenizer and not here would be untested; one REMOVED from the
    tokenizer would turn thousands of writer lines into player speech and this
    guard names which one went.

    The exclusion set exists instead of a casing rule, and the choice was
    measured: see
    :func:`test_a_lowercase_field_label_reads_as_speech_and_the_real_one_still_does_not`.
    """
    excluded = getattr(eval_ledger, "_NON_SPEAKER_PREFIXES", None)
    assert excluded is not None, (
        "graphia.eval_ledger._NON_SPEAKER_PREFIXES is gone — the speaker "
        "exclusion set moved or was renamed; update _NON_SPEAKER_PREFIX_LINES "
        "and this guard together"
    )
    assert {prefix for _, prefix, _ in _NON_SPEAKER_PREFIX_LINES} == set(excluded)
    assert len(_NON_SPEAKER_PREFIX_LINES) == 3
    # Each swept line really does begin with the prefix it claims to test.
    assert all(
        line.startswith(prefix + ":")
        for _, prefix, line in _NON_SPEAKER_PREFIX_LINES
    )


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(line, id=case_id)
        for case_id, _, line in _NON_SPEAKER_PREFIX_LINES
    ],
)
def test_a_writer_vocabulary_prefix_is_not_a_player_speaking(line: str) -> None:
    """``Moderator:``, ``tally:`` and ``outcome:`` stay one ``plain`` span.

    **This is the whole justification for an exclusion set rather than a casing
    rule, and nothing pinned ``tally:`` or ``outcome:`` before Slice 3.** They
    are lowercase, so a capitalisation guard would have excluded them for free —
    and would have cost 426 real speaker lines under lowercase player names.
    With the set instead, these three are the only lines the writer emits that
    are shaped like speech and are not, so each one gets an assertion.

    ``Moderator:`` is deliberately neither speech nor a recap. He is the writer's
    narration; ``<recap>`` is the moderator content that DOES get a kind, and it
    is marked by its tag rather than by his name.
    """
    spans = _spans(line)

    assert spans == [(line, eval_ledger.KIND_PLAIN)]
    assert eval_ledger.KIND_SPEAKER not in {kind for _, kind in spans}


def test_the_moderators_private_form_is_not_a_speaker_either() -> None:
    """``Moderator (private to Avery): You are Avery.`` — one ``plain`` span.

    **Covered nowhere before Slice 3**, and it is the case most likely to be got
    wrong by accident, because it passes for a reason that is *not* the exclusion
    set: ``_SPEAKER_RE``'s name is one whitespace-free token, and this prefix has
    two spaces in it, so the regex never matches and the literal
    ``"Moderator (private to Avery)"`` never reaches the exclusion lookup.

    That is asserted explicitly below — the string is NOT in the exclusion set —
    so this test cannot be "fixed" later by adding an entry there without the
    reader noticing that a per-player prefix can never be enumerated anyway
    (``private to <name>`` varies with the cast).
    """
    line = "Moderator (private to Avery): You are Avery."
    spans = _spans(line)

    assert spans == [(line, eval_ledger.KIND_PLAIN)]
    assert "Moderator (private to Avery)" not in eval_ledger._NON_SPEAKER_PREFIXES
    # ...and it is the SPACE that disqualifies it, not the parentheses: the same
    # words without the space would be caught by the `Moderator` entry instead.
    assert _spans("Moderator: hi")[0][1] == eval_ledger.KIND_PLAIN


# Lines that look enough like a speaker to be worth a test and are deliberately
# NOT one. The misses are all in the same direction — an unrecognised line falls
# back to `plain`, which the spec's degradation posture prefers to a wrong
# capture.
_NOT_A_SPEAKER_CASES: tuple[tuple[str, str], ...] = (
    # A name carrying an internal space: the documented deliberate miss. Absent
    # from the corpus entirely, so only this test can see it.
    ("internal-space", "Mary Ann: hello"),
    # A colon with nothing after it — `_SPEAKER_RE`'s lookahead requires the
    # separating space, which is also what guarantees the speech span is never
    # empty.
    ("bare-colon", "Avery:"),
    # A colon with no space after it: `http://x` is the shape this protects.
    ("no-space-after-colon", "Avery:no space"),
    ("url-in-prose", "See http://example.com/x for details"),
    # The endgame's own summary headings, which end in a colon and open a line.
    ("full-roster", "Full roster: Alice, Bo, Cy"),
    ("events-heading", "Events this game:"),
    ("who-they-were", "Who they really were:"),
    # A bullet: `•` is not a letter, so the name never starts.
    ("bulleted-event", "• Night 1: Zoe was killed"),
    # A digit initial is excluded by `[^\W\d_]`.
    ("digit-initial", "1984: a year"),
)


@pytest.mark.parametrize(
    "line",
    [pytest.param(line, id=case_id) for case_id, line in _NOT_A_SPEAKER_CASES],
)
def test_a_line_that_only_resembles_a_speaker_stays_plain(line: str) -> None:
    """Each deliberate miss comes back as exactly one ``plain`` span.

    Every one of these is a shape the 298-file sweep cannot fail on: the endgame
    headings and the bulleted events occur in real games but are indistinguishable
    from a pass unless the kind is asserted, and ``Mary Ann: hello``,
    ``Avery:no space`` and a bare ``Avery:`` do not occur at all.

    The negative assertion is spelled out beside the span equality so a
    regression reports *the speaker rule* by name rather than only "spans
    differ".
    """
    spans = _spans(line)

    assert spans == [(line, eval_ledger.KIND_PLAIN)]
    assert not {kind for _, kind in spans} & {
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_SPEECH,
    }


def test_the_speaker_rule_sits_below_the_field_label_rule() -> None:
    """Priority ordering in ``_line_spans``, pinned as behaviour.

    ``Personality: brisk`` is shaped *exactly* like a player named "Personality"
    speaking. Put the speaker branch above the field-label branch and it swallows
    all **6,200** cast-list labels in the corpus, turning the opening cast list
    into a transcript of six people called Personality, Manner, Persona, Public
    legend and True self (hidden).

    ``tasks.md`` records this as load-bearing for Slice 3 and the tokenizer notes
    it in the code; this is the executable form. All five labels are swept, since
    a reordering breaks every one of them and a test that checked only
    ``Personality:`` would leave the other four to a comment.
    """
    for label in eval_ledger._FIELD_LABELS:
        spans = _spans(f"{label} some prose")
        assert spans[0] == (label, eval_ledger.KIND_FIELD_LABEL), (
            f"{label!r} was not read as a cast-list label — the speaker rule has "
            "moved above the field-label rule in _line_spans"
        )
    # The non-vacuity half: the speaker rule really is there to be stolen from.
    assert _spans("Personalityx: some prose")[0][1] == eval_ledger.KIND_SPEAKER


# ---------------------------------------------------------------------------
# `thought` / `recap` — the two inline content kinds (spec 038, Slice 3)
# ---------------------------------------------------------------------------
#
# functional-spec §2: a private thought "can never be mistaken for something said
# aloud", and a recap "reads as fact rather than opinion" and doubles as a scroll
# landmark. Both are claimed by their TAG, not by their prose — which is what
# makes them safe: the tokenizer never has to guess whether a sentence is private.
#
# The surrounding tags stay `marker` on both, and the owner's name inside a
# `<thought player="X">` stays `attr` (Slice 4 gives that name its side).


def test_a_private_thoughts_body_is_thought_and_its_tag_stays_marker() -> None:
    """The complete span list for a real ``<thought>`` line.

    Three properties in one expectation, each of which a later slice could break
    independently: the body is ``thought``; both tags are still ``marker``; and
    the owner's name is still the ``attr`` span Slice 2 made it, sitting inside
    the opening tag rather than being pulled out of it.
    """
    spans = _spans('<thought player="Alice">Bo suspects me tonight.</thought>')

    assert spans == [
        ('<thought player="', eval_ledger.KIND_MARKER),
        ("Alice", eval_ledger.KIND_ATTR),
        ('">', eval_ledger.KIND_MARKER),
        ("Bo suspects me tonight.", eval_ledger.KIND_THOUGHT),
        ("</thought>", eval_ledger.KIND_MARKER),
    ]


def test_a_thoughts_body_is_not_re_split_as_speech() -> None:
    """A thought whose body is shaped like a spoken line stays one ``thought``.

    Private reflections are model-generated first person, so ``I keep thinking:
    Bo is lying`` is a shape one will eventually take. The inline-content branch
    runs before the per-line chain, so the body is claimed whole — which is what
    "can never be mistaken for something said aloud" requires. Without this, a
    thought would render half italic and half speech-coloured.
    """
    spans = _spans('<thought player="Alice">Bo: he is lying</thought>')

    assert spans[-2] == ("Bo: he is lying", eval_ledger.KIND_THOUGHT)
    assert not {kind for _, kind in spans} & {
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_SPEECH,
    }


def test_a_moderator_recaps_body_is_recap_and_its_tags_stay_marker() -> None:
    """The complete span list for a real ``<recap>`` line."""
    assert _spans("<recap>Alive: Alice, Bo. Mafia 1, Law-abiding 1.</recap>") == [
        ("<recap>", eval_ledger.KIND_MARKER),
        ("Alive: Alice, Bo. Mafia 1, Law-abiding 1.", eval_ledger.KIND_RECAP),
        ("</recap>", eval_ledger.KIND_MARKER),
    ]


def test_a_recaps_inner_colons_are_not_lifted_out_of_it() -> None:
    """A recap's body is ONE span, whatever colons it contains.

    The real shape: a status line carries a clock time and an ``Alive:`` list, so
    it holds three colons and opens with a word that is shaped like a speaker.
    The recap wins because the tag claims the body before the per-line chain runs
    — and it must, or a landmark a reviewer scrolls to would arrive in three
    pieces of three different kinds.
    """
    source = "<recap>Day 3, 4:15 PM status: Alive: Alice, Bo.</recap>"
    spans = _spans(source)

    assert spans == [
        ("<recap>", eval_ledger.KIND_MARKER),
        ("Day 3, 4:15 PM status: Alive: Alice, Bo.", eval_ledger.KIND_RECAP),
        ("</recap>", eval_ledger.KIND_MARKER),
    ]
    assert [kind for _, kind in spans].count(eval_ledger.KIND_RECAP) == 1
    assert not {kind for _, kind in spans} & {
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_FIELD_LABEL,
    }


@_requires_corpus
@pytest.mark.parametrize(
    ("missing", "token"),
    [
        pytest.param("thought", "<thought", id="no-thought"),
        pytest.param("vote", "<vote", id="no-vote"),
    ],
)
def test_a_real_game_missing_an_element_tokenizes_without_raising(
    missing: str, token: str
) -> None:
    """**Absence is normal**: 82 committed games have no ``<thought>`` and 34 no
    ``<vote>``, and both tokenize cleanly.

    Named explicitly by Slice 3's test task because Slice 3 is the first slice
    whose kinds depend on those two elements — ``thought`` comes only from a
    ``<thought>`` tag and a ballot only from inside a ``<vote>`` block — so a
    reader implementing them can easily write code that assumes presence. A
    tokenizer that does passes every synthetic fixture in this file and breaks on
    a quarter of the corpus.

    The file is discovered rather than hard-coded (the corpus grows with every
    committed eval run) and the count is asserted as a lower bound for the same
    reason. Read-only.
    """
    games = [
        path
        for path in _TRANSCRIPTS
        if token not in path.read_text(encoding="utf-8")
    ]
    assert games, f"the corpus has no game without a {missing!r} element to test"

    for path in games:
        text = path.read_text(encoding="utf-8")
        spans = _spans(text)
        assert "".join(span_text for span_text, _ in spans) == text, _rel(path)
        assert all(span_text for span_text, _ in spans), _rel(path)
        kinds = {kind for _, kind in spans}
        assert kinds <= set(eval_ledger.TRANSCRIPT_KINDS), _rel(path)
        if missing == "thought":
            assert eval_ledger.KIND_THOUGHT not in kinds, _rel(path)


# ---------------------------------------------------------------------------
# Structural guarantees: indentation, separators, coalescing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "indent",
    [pytest.param("  ", id="two-spaces"), pytest.param("\t", id="tab")],
)
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("<round>", [("<round>", "marker")], id="tag"),
        pytest.param("Round 1.", [("Round 1.", "marker")], id="round-label"),
        pytest.param(
            '<player name="Alice" role="Mafioso">',
            [
                ('<player name="', "marker"),
                ("Alice", "attr"),
                ('" role="', "marker"),
                ("Mafioso", "attr"),
                ('">', "marker"),
            ],
            id="player-tag",
        ),
        pytest.param(
            "Personality: brisk",
            [("Personality:", "field-label"), (" brisk", "plain")],
            id="field-label",
        ),
        # Slice 3. A pre-022 game indents its Day lines, so an indented spoken
        # line is a REAL shape and not a hypothetical — and the non-ASCII name
        # keeps the Unicode half of `_SPEAKER_RE` covered here too.
        pytest.param(
            "Sofía: bonjour",
            [("Sofía:", "speaker"), (" bonjour", "speech")],
            id="spoken-line",
        ),
    ],
)
def test_leading_indentation_is_its_own_plain_span(
    indent: str, body: str, expected: list[tuple[str, str]]
) -> None:
    """Indentation is layout, never part of what follows it.

    This is the whole reason the three pre-spec-022 run dirs need no era branch:
    they indent ``  <round>``, ``  Round N.`` and their four-space cast-list
    fields where the spec-022 form does not, and splitting the indent off makes
    both tokenize to the same spans. Pinned as the exact span list, so an
    implementation that swallowed the indent into the first styled span (and so
    styled leading whitespace) would fail.

    Slice 2 widened this from "…part of the marker": the body's spans are now
    passed in whole, because a ``<player …>`` line is five of them and a
    ``Personality:`` line is two. The indent's own span is what stays constant.
    """
    spans = _spans(indent + body)
    assert spans[0] == (indent, eval_ledger.KIND_PLAIN)
    assert spans == [(indent, eval_ledger.KIND_PLAIN), *expected]


def test_an_indented_pre_022_cast_entry_is_one_plain_span() -> None:
    """The old ``Name — Role`` cast form: indent and text coalesce into one span.

    The counterpart to the test above — indentation is only split off when what
    follows it is marked; before plain text it simply merges back.
    """
    assert _spans("  Alice — Mafioso") == [
        ("  Alice — Mafioso", eval_ledger.KIND_PLAIN)
    ]


def test_line_separators_are_plain_spans_of_their_own() -> None:
    """No styled span ever carries a ``\\n``, so a marker cannot bleed to the
    end of a terminal row.

    Pinned as the complete span list of a five-line document: the separators are
    visible in the expectation, folded into the plain runs around them.

    **Slice 3 lengthened the expectation without changing the property.** The
    document's middle line is ``Alice: hi``, which Slice 1 folded into the plain
    run ``"\\nAlice: hi\\n"`` between two markers; the speaker rule now splits it
    into ``speaker`` + ``speech``, so the two newlines around it become plain
    spans of their own. That is the rule working harder, not differently — and it
    is the shape that makes the rule matter, because a speech style running to
    the end of a terminal row is exactly what the separator split prevents.
    """
    source = "<day>\n<round>\nAlice: hi\n</round>\n</day>"
    spans = _spans(source)

    assert spans == [
        ("<day>", eval_ledger.KIND_MARKER),
        ("\n", eval_ledger.KIND_PLAIN),
        ("<round>", eval_ledger.KIND_MARKER),
        ("\n", eval_ledger.KIND_PLAIN),
        ("Alice:", eval_ledger.KIND_SPEAKER),
        (" hi", eval_ledger.KIND_SPEECH),
        ("\n", eval_ledger.KIND_PLAIN),
        ("</round>", eval_ledger.KIND_MARKER),
        ("\n", eval_ledger.KIND_PLAIN),
        ("</day>", eval_ledger.KIND_MARKER),
    ]
    # The property the expectation encodes, restated so a future re-split fails
    # with the rule's own name rather than only with a longer diff.
    assert not [
        span
        for span in spans
        if "\n" in span[0] and span[1] != eval_ledger.KIND_PLAIN
    ]


# A synthetic game exercising every shape this slice recognises at once: the
# metadata header, both cast-list eras, all twelve tags, inline elements, the
# round label, indentation, blank lines, prose, and NO trailing newline (all 298
# committed transcripts end on `>`).
#
# SLICE 4 ADDED A THIRD CAST ENTRY AND A THIRD SPEAKER, and the choice of who is
# in the cast is the whole design of this fixture now that the tokenizer reads
# `<setup>`. All three sides a name can have are present at once:
#
#   Alice — `role="Mafioso"`, a role string the WRITER NEVER EMITS. Kept exactly
#           as Slices 1-3 wrote it, and now doing a second job: an unrecognised
#           label leaves her out of the map, so `Alice:` is the NEUTRAL
#           `speaker`/`speech`, her `Mafioso` is achromatic `attr`, and her
#           `<thought player="Alice">` owner name is achromatic `attr` too. The
#           whole never-guess posture, exercised in the full-shape game rather
#           than only in a near-miss fixture.
#   Bo    — `role="Law-abiding Citizen"`, so his BALLOT inside the `<vote>` block
#           is `speaker-law-abiding`/`speech-law-abiding`. That a vote reads at a
#           glance as "which sides voted which way" is the single most useful
#           thing colour can say about a vote (tasks.md, Slice 3's ballot
#           ratification).
#   Cass  — `role="Mafia"`, the entry Slice 4 added. Without a real Mafia in the
#           cast this fixture reached 11 of the 14 kinds and
#           `test_every_kind_emitted_is_declared_in_the_vocabulary`'s
#           no-dead-entries property was unprovable.
#
# SLICE 5 ADDED A FOURTH CAST ENTRY AND MOVED THE WELCOME LINE, for one reason:
# a transcript has exactly ONE seat, so one file can emit at most TWO of the six
# seat kinds and no single fixture can reach the whole twenty-kind vocabulary any
# more (see `test_every_kind_emitted_is_declared_in_the_vocabulary`, which is now
# the union of three games). This one owns the case where the seat's SIDE IS
# UNKNOWN — the pre-spec-022 story, and the half most easily lost, since it is
# the one that proves side and seat are independent facts rather than one fact
# with two names:
#
#   Dex   — `role="Mafioso"`, the same role string the writer never emits, AND
#           the welcomed seat. So `Dex:` is `speaker-human`/`speech-human`: bold
#           because the game says the seat was his, uncoloured because nothing in
#           the file says which side he was on. Alice keeps the identical
#           unrecognised role and is NOT the seat, so the NEUTRAL `speaker` /
#           `speech` survive beside him — which is the contrast that makes the
#           seat axis visible here rather than merely present.
#
# The welcome moved from Bo to Dex for the same reason: with Bo welcomed, his
# ballot was the seat and the plain `speaker-law-abiding` pair vanished from the
# fixture entirely.
_RICH_SYNTHETIC_TRANSCRIPT = (
    "Game 2 | provider=ollama | large_model=qwen3-coder:30b | games=3\n"
    "<transcript>\n"
    "<setup>\n"
    '<player name="Alice" role="Mafioso">\n'
    "Personality: brisk and sly\n"
    "Manner: clipped\n"
    "</player>\n"
    '<player name="Bo" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    '<player name="Cass" role="Mafia">(no persona recorded)</player>\n'
    '<player name="Dex" role="Mafioso">(no persona recorded)</player>\n'
    "</setup>\n"
    "<preamble>\n"
    "Moderator: A new game begins. Welcome, Dex.\n"
    "\n"
    "</preamble>\n"
    "<night>\n"
    "<kill>Avery — Law-abiding Citizen</kill>\n"
    "</night>\n"
    "<day>\n"
    "  <round>\n"
    "  Round 1.\n"
    "Alice: I saw nothing last night.\n"
    "Cass: Bo is lying.\n"
    "Dex: I was asleep.\n"
    '<thought player="Alice">Bo suspects me.</thought>\n'
    '<vote initiator="Alice" target="Bo">\n'
    "Bo: Yes\n"
    "</vote>\n"
    "<recap>Alive: Alice, Bo.</recap>\n"
    "  </round>\n"
    # SPEC 039 ADDED THIS LINE, and where it sits is half of what it says. A
    # diary is the Day's TRAILER — `_render_phases` appends `day_trailer` after
    # the rounds loop — so it renders between the last `</round>` and `</day>`,
    # "between the day it was written about and the Night that followed". Cass
    # is the owner because she is the fixture's only real `role="Mafia"`, so her
    # name in the tag is `attr-mafia` and the fixture states the side-bearing
    # owner rule as well as the body kind.
    '<diary player="Cass" day="1">Bo folded under pressure.</diary>\n'
    "</day>\n"
    "<endgame>\n"
    "Mafia win.\n"
    "</endgame>\n"
    "</transcript>"
)

# The other TWO of the three games the twenty-kind vocabulary now takes to cover
# (see `test_every_kind_emitted_is_declared_in_the_vocabulary`). A transcript has
# exactly one seat, so no single file can emit more than two of the six seat
# kinds, and the fixture above owns the pair whose side is unknown. These own the
# four side-qualified ones.
#
# Each carries a NON-SEAT player on the SEAT'S OWN SIDE, which is the whole design
# of both and not padding: it is what keeps the plain side kinds alive in the same
# file, so "bold" is provably the seat rather than the side, and it is
# functional-spec §2's contrast written into the fixture — "that seat's lines are
# in the side colour and bold, while other players on the same side are the same
# colour but not bold".
#
# They also split the two ROUTES to a seat between them, so the union exercises
# both without a third game: the Mafia seat is named by the writer's
# `human="true"` marker and carries no welcome line at all (the shape every
# transcript written from Slice 5 onward has, and which **0 of the 298 committed
# files** contain), while the Law-abiding seat is named only by the moderator's
# greeting (the shape all 298 committed files have). Both are reused by the
# Slice-5 section below.
_MAFIA_SEAT_GAME = (
    "<transcript>\n"
    "<setup>\n"
    '<player name="Vera" role="Mafia" human="true">(no persona recorded)</player>\n'
    '<player name="Dot" role="Mafia">(no persona recorded)</player>\n'
    '<player name="Iris" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    "</setup>\n"
    "<day>\n"
    "Vera: I was asleep.\n"
    "Dot: So was I.\n"
    "Iris: Neither of you was.\n"
    "</day>\n"
    "</transcript>"
)

_LAW_ABIDING_SEAT_GAME = (
    "<transcript>\n"
    "<setup>\n"
    '<player name="Iris" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    '<player name="Wren" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    '<player name="Vera" role="Mafia">(no persona recorded)</player>\n'
    "</setup>\n"
    "<preamble>\n"
    "Moderator: A new game begins. Welcome, Iris.\n"
    "</preamble>\n"
    "<day>\n"
    "Iris: I was asleep.\n"
    "Wren: So was I.\n"
    "Vera: Neither of you was.\n"
    "</day>\n"
    "</transcript>"
)


def test_no_styled_span_carries_a_newline_across_a_whole_synthetic_game() -> None:
    """The separator rule, checked over every span of a full-shape game.

    The premise assertion matters: without it a tokenizer that returned nothing
    but ``plain`` would satisfy "no styled span has a newline" trivially.
    """
    spans = _spans(_RICH_SYNTHETIC_TRANSCRIPT)
    styled = [
        (text, kind) for text, kind in spans if kind != eval_ledger.KIND_PLAIN
    ]
    # THE PREMISE, pinned by SHAPE and not only by total. Counted by hand off
    # `_RICH_SYNTHETIC_TRANSCRIPT`:
    #
    # SPEC 039 ADDS SEVEN SPANS, all from the one `<diary>` line: four marker
    # pieces (`<diary player="`, `" day="`, `">`, `</diary>`), Cass's owner name
    # as `attr-mafia`, the `1` day value as achromatic `attr`, and the body as
    # the new `diary` kind. 65 -> 72, and the three kinds that move are the three
    # the line touches — which is what the breakdown is for.
    #
    #   marker 46 — the header (1), `<transcript>` (2), `<setup>` (3), the FOUR
    #               cast entries' opening heads at 3 marker pieces each (4-6,
    #               8-10, 12-14, 16-18) with their four `</player>` (7, 11, 15,
    #               19), `</setup>` (20), `<preamble>`/`</preamble>` (21-22),
    #               `<night>` (23), the coalesced `<kill>…</kill>` line as ONE
    #               span (24), `</night>` (25), `<day>` (26), `<round>` (27),
    #               `Round 1.` (28), the thought's 2 head pieces (29-30) and
    #               `</thought>` (31), the vote's 3 head pieces (32-34) and
    #               `</vote>` (35), `<recap>`/`</recap>` (36-37), `</round>`
    #               (38), the diary's 3 head pieces (39-41) and `</diary>` (42),
    #               `</day>` (43), `<endgame>`/`</endgame>` (44-45) and
    #               `</transcript>` (46);
    #   attr   10 — the four cast NAMES (Alice, Bo, Cass, Dex — achromatic by the
    #               ratified narrow reading, and NOTE that Dex's stays achromatic
    #               too: `attr` gains no seated form, because the seat's own cast
    #               entry already carries `attr`'s bold), Alice's and Dex's
    #               unrecognised `Mafioso` roles, the thought's owner `Alice`
    #               (unrecognised role, so absent from the map), the vote's
    #               `Alice`/`Bo`, and the diary's `day="1"` value — spec 039's,
    #               and achromatic because it names a Day and not a person;
    #   attr-mafia 2 — Cass's `Mafia` role text, and (spec 039) her name as the
    #               diary's OWNER, which is the second place a name-shaped
    #               attribute is side-bearing;
    #   attr-law-abiding 1 — Bo's `Law-abiding Citizen` role text;
    #   field-label 2 — `Personality:` and `Manner:` on Alice's entry;
    #   speaker 1 / speech 1 — `Alice:` and her words: her role label is one the
    #               writer never emits, so she has no known side, and she is not
    #               the welcomed seat, so she takes the NEUTRAL pair. This is the
    #               fixture's own copy of Slice 4's headline degradation rule —
    #               and, beside Dex, the proof that Slice 5's bold is the SEAT
    #               and not merely "an unknown side drawn differently";
    #   speaker-mafia 1 / speech-mafia 1 — `Cass:` and her words;
    #   speaker-law-abiding 1 / speech-law-abiding 1 — `Bo:` on the ballot inside
    #               the `<vote>` block (a ballot is speech, tasks.md Slice 3);
    #   speaker-human 1 / speech-human 1 — `Dex:` and his words. Same
    #               unrecognised role as Alice, so no side; welcomed by the
    #               moderator, so the seat. Side and seat, independent.
    #   thought 1 — `Bo suspects me.`, the body of the `<thought>` element,
    #               NEVER side-tinted (tasks.md: a private reflection is not an
    #               act of allegiance);
    #   recap   1 — `Alive: Alice, Bo.`, the body of the `<recap>` element, never
    #               side-tinted either (the moderator has no side);
    #   diary   1 — `Bo folded under pressure.`, the body of the `<diary>`
    #               element in the Day's trailer (spec 039). Never side-tinted
    #               although its owner IS — the owner's name carries the side and
    #               the prose does not, which is `thought`'s precedent one tag
    #               over.
    #
    # The `Moderator: A new game begins.` preamble line is deliberately NOT among
    # them: he is in the exclusion set, so his line stays plain and coalesces with
    # the blank line after it. That absence is the count's own guard against the
    # speaker rule over-reaching — and since Slice 5 it guards one thing more, as
    # that same line is the one the seat is read from: the tokenizer reads the
    # name out of it without ever styling it.
    #
    # Slice 1 pinned only `len(styled) == 27`, Slice 2 raised it to 43, Slice 3 to
    # 49, Slice 4 to 57 and Slice 5 to 65. Spec 039 supersedes all five — and the
    # reason the breakdown exists rather than a bare total is visible in this very
    # edit: the failure names WHICH kinds moved, so the growth (65 → 72, entirely
    # from one `<diary>` line) is instantly distinguishable from a boundary bug.
    assert len(styled) == 72, (
        "the premise: this game really does produce styled spans "
        f"(got {len(styled)})"
    )
    assert Counter(kind for _, kind in styled) == {
        eval_ledger.KIND_MARKER: 46,
        eval_ledger.KIND_ATTR: 10,
        eval_ledger.KIND_ATTR_MAFIA: 2,
        eval_ledger.KIND_ATTR_LAW_ABIDING: 1,
        eval_ledger.KIND_FIELD_LABEL: 2,
        eval_ledger.KIND_SPEAKER: 1,
        eval_ledger.KIND_SPEECH: 1,
        eval_ledger.KIND_SPEAKER_MAFIA: 1,
        eval_ledger.KIND_SPEECH_MAFIA: 1,
        eval_ledger.KIND_SPEAKER_LAW_ABIDING: 1,
        eval_ledger.KIND_SPEECH_LAW_ABIDING: 1,
        eval_ledger.KIND_SPEAKER_HUMAN: 1,
        eval_ledger.KIND_SPEECH_HUMAN: 1,
        eval_ledger.KIND_THOUGHT: 1,
        eval_ledger.KIND_RECAP: 1,
        eval_ledger.KIND_DIARY: 1,
    }
    offenders = [(text, kind) for text, kind in styled if "\n" in text]
    assert not offenders, f"styled spans carrying a newline: {offenders}"


def test_the_synthetic_game_round_trips_and_has_no_empty_or_adjacent_spans() -> None:
    """The sweep's structural guarantees, restated on an input small enough to read.

    Half 1 asserts these over 298 real files; repeating them here means a
    developer editing the tokenizer sees the failure against 30 readable lines
    rather than a 30 KB game.
    """
    spans = _spans(_RICH_SYNTHETIC_TRANSCRIPT)

    assert "".join(text for text, _ in spans) == _RICH_SYNTHETIC_TRANSCRIPT
    assert all(text for text, _ in spans), "a span has empty text"
    adjacent = [
        (a, b)
        for (_, a), (_, b) in zip(spans, spans[1:], strict=False)
        if a == b
    ]
    assert not adjacent, f"adjacent spans share a kind (uncoalesced): {adjacent}"


def test_a_wall_of_prose_arrives_as_one_plain_span() -> None:
    """Coalescing, pinned absolutely: five prose lines are one span, not five.

    **Slice 3 replaced the fixture, not the point.** The five lines used to be
    ``Alice: line 0`` … — which the speaker rule now splits into fifteen spans,
    so that input no longer exercises coalescing at all. Five ``Moderator:``
    lines are the same wall of unremarkable text that still tokenizes to a single
    ``plain`` span (he is in the exclusion set), so the assertion goes on proving
    what it was written to prove: adjacent same-kind runs merge, and the
    separators between them merge with them.

    The premise line is what keeps that non-vacuous — five lines really did go in,
    so "one span" is coalescing and not an empty result.
    """
    source = "\n".join(f"Moderator: line {index}" for index in range(5))

    assert source.count("\n") == 4
    assert _spans(source) == [(source, eval_ledger.KIND_PLAIN)]


def test_the_coalescing_stops_at_a_kind_boundary() -> None:
    """The control for the wall above: a run of SPEECH does not merge into one.

    Without it, "five lines are one span" would also pass on a tokenizer that had
    stopped splitting anything at all. Five spoken lines are fifteen spans —
    speaker, speech, separator, five times over, less the trailing separator —
    and no two neighbours share a kind, which is the guarantee coalescing owes.
    """
    source = "\n".join(f"Alice: line {index}" for index in range(5))
    spans = _spans(source)

    assert len(spans) == 14
    assert [kind for _, kind in spans[:3]] == [
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_SPEECH,
        eval_ledger.KIND_PLAIN,
    ]
    assert not [
        pair
        for pair in zip(spans, spans[1:], strict=False)
        if pair[0][1] == pair[1][1]
    ]
    assert "".join(text for text, _ in spans) == source


def test_every_kind_emitted_is_declared_in_the_vocabulary() -> None:
    """``TRANSCRIPT_KINDS`` really is the canonical list the UI builds from.

    Deliberately NOT ``TRANSCRIPT_KINDS == ("marker", "plain", "attr",
    "field-label")``: later slices append to it, and a test that has to be edited
    by every slice teaches the editor to edit it without reading. What must hold
    forever is the containment — a kind the tokenizer emits but never declares is
    a kind the UI's style map can never learn about.

    The second assertion is the non-vacuity half and it *is* slice-scoped: this
    full-shape game exercises every shape the tokenizer recognises, so the set of
    kinds it emits is the set of kinds that exist. Slice 1 pinned two; Slice 2
    superseded it with four and Slice 3 with all eight, and each later slice
    moves it again — which is exactly the point of writing it out rather than
    deriving it from ``TRANSCRIPT_KINDS``, since a kind declared but never
    emitted by any real shape is a kind nobody will ever see.

    Slice 3 is the first slice where the two assertions have the same content
    (every declared kind is emitted), so the third one below says that in its own
    words: after this slice the vocabulary has no dead entries.

    **Slice 4 moved all three together, and keeping the no-dead-entries property
    is what shaped the fixture.** The six side-bearing kinds took the vocabulary
    from eight to fourteen; the fixture as Slice 3 left it reached only eleven,
    because it had a Law-abiding cast member (Bo) but no player whose role was
    the literal ``Mafia`` — ``Mafioso`` is a role string the writer never emits.
    Rather than change Alice's role and lose that unrecognised-label case, a
    third cast entry was added (Cass, ``role="Mafia"``) so the fixture covers
    **both**: a real Mafia and a role label nothing in the codebase produces.

    A kind declared but never emitted by any real shape is a kind nobody will
    ever see — that is why this is written out rather than derived from
    ``TRANSCRIPT_KINDS``, and it is exactly the check that would have caught a
    Slice 4 that added ``speaker-mafia`` to the vocabulary and forgot to emit it.

    **Slice 5 made the no-dead-entries half unsatisfiable by any single fixture,
    and that is a structural fact about transcripts rather than a limitation of
    this one.** A game has exactly ONE seat, so a file can emit at most two of the
    six seat kinds — the pair belonging to whichever side that seat turned out to
    be on. Three games are therefore needed and three are used: a seat whose side
    is unknown (``_RICH_SYNTHETIC_TRANSCRIPT``), a Mafia seat, and a Law-abiding
    seat. Each of the two extra games keeps a NON-SEAT player on the seat's own
    side, so the plain side kinds survive in the same file and the union does not
    quietly trade a Slice-4 kind for a Slice-5 one.

    The union is asserted per game as well as in total, because a union is the one
    shape where a fixture can go dead without anything failing: if the Mafia-seat
    game stopped emitting its two kinds and the rich game started emitting them,
    the total would be unchanged and this test would go on passing.
    """
    per_game = {
        "rich": {kind for _, kind in _spans(_RICH_SYNTHETIC_TRANSCRIPT)},
        "mafia-seat": {kind for _, kind in _spans(_MAFIA_SEAT_GAME)},
        "law-abiding-seat": {kind for _, kind in _spans(_LAW_ABIDING_SEAT_GAME)},
    }
    for name, kinds in per_game.items():
        assert kinds <= set(eval_ledger.TRANSCRIPT_KINDS), (
            f"the {name} game emits kinds the vocabulary does not declare: "
            f"{sorted(kinds - set(eval_ledger.TRANSCRIPT_KINDS))}"
        )

    # The rich game, pinned absolutely: every Slice-1-to-4 kind plus the seat
    # pair whose SIDE IS UNKNOWN.
    assert per_game["rich"] == {
        eval_ledger.KIND_MARKER,
        eval_ledger.KIND_PLAIN,
        eval_ledger.KIND_ATTR,
        eval_ledger.KIND_FIELD_LABEL,
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_SPEECH,
        eval_ledger.KIND_THOUGHT,
        eval_ledger.KIND_RECAP,
        eval_ledger.KIND_SPEAKER_MAFIA,
        eval_ledger.KIND_SPEAKER_LAW_ABIDING,
        eval_ledger.KIND_SPEECH_MAFIA,
        eval_ledger.KIND_SPEECH_LAW_ABIDING,
        eval_ledger.KIND_ATTR_MAFIA,
        eval_ledger.KIND_ATTR_LAW_ABIDING,
        eval_ledger.KIND_SPEAKER_HUMAN,
        eval_ledger.KIND_SPEECH_HUMAN,
        # Spec 039's, from the `<diary>` in the Day's trailer. The rich game is
        # where it goes because it is the full-shape game and a diary is part of
        # a full-shape game now — and because the two seated games below own one
        # narrow claim each and adding a second to either would blur it.
        eval_ledger.KIND_DIARY,
    }
    # ...and each seated game contributes exactly its own two, pinned per game so
    # a fixture cannot go dead behind the union.
    assert per_game["mafia-seat"] & _HUMAN_KINDS == {
        eval_ledger.KIND_SPEAKER_MAFIA_HUMAN,
        eval_ledger.KIND_SPEECH_MAFIA_HUMAN,
    }
    assert per_game["law-abiding-seat"] & _HUMAN_KINDS == {
        eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN,
        eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN,
    }
    # ...and each keeps a non-seat player on the seat's own side, so the plain
    # side kinds are still reachable in the very file that bolds one of them.
    assert eval_ledger.KIND_SPEAKER_MAFIA in per_game["mafia-seat"]
    assert eval_ledger.KIND_SPEAKER_LAW_ABIDING in per_game["law-abiding-seat"]

    # As of Slice 5 the vocabulary still has no declared-but-unreachable entry.
    assert set().union(*per_game.values()) == set(eval_ledger.TRANSCRIPT_KINDS)
    assert len(eval_ledger.TRANSCRIPT_KINDS) == len(set(eval_ledger.TRANSCRIPT_KINDS))


# ===========================================================================
# 3. Sides, read from the cast list (spec 038, Slice 4)
# ===========================================================================
#
# THE ONE THING THAT CHANGED, restated where the tests that depend on it live:
# `tokenize_transcript` is no longer a function of a line. It makes two passes —
# `_cast_side_map(lines)` reads `<setup>`'s `<player name=… role=…>` entries into
# `name → side`, then the per-line chain runs with that map threaded through — so
# a synthetic input that wants a side kind MUST carry the `<setup>` block that
# grants it, and one that carries no `<setup>` gets the neutral kinds by
# definition rather than by accident.
#
# WHY ALMOST EVERYTHING BELOW IS SYNTHETIC. Slices 2 and 3 each proved the same
# thing about the 298-file sweep: it is necessary and not sufficient, because it
# only covers shapes the corpus happens to contain. Measured against the corpus,
# Slice 4's rules are almost entirely unexercised by real data —
#
#   * every one of the 1,960 `role="…"` values is one of the two labels the
#     writer emits, so a role it does NOT emit never occurs;
#   * every one of the 8,183 `<thought player="…">` owners is in their own file's
#     cast list, so the unknown-owner fallback never occurs;
#   * every one of the 22,508 speaker names is in their own file's cast list, so
#     an unknown SPEAKER never occurs outside the 30 pre-022 files;
#   * `<player …>` occurs nowhere outside `<setup>` in any of the 298 files, so
#     the scoping guard is never exercised;
#   * no cast list names anybody twice, and none is empty, missing or unclosed.
#
# Each of those is one production line away from being wrong and no committed
# game would notice. They are the whole point of this section.


# A two-sided cast, the base fixture for most of what follows: one Mafia, one
# Law-abiding, both written in the writer's own `_ROLE_LABELS` spelling. Short
# enough that a test can pin the complete span list of everything after it.
_TWO_SIDED_CAST = (
    "<setup>\n"
    '<player name="Vera" role="Mafia">(no persona recorded)</player>\n'
    '<player name="Iris" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    "</setup>\n"
)

# The complete span list `_TWO_SIDED_CAST` itself produces, pinned once here so
# the tests below can pin only the dialogue after it without any of them losing
# sight of what the cast list is doing. This IS functional-spec §2's "the side
# colours used in the cast list match the ones used for dialogue", stated at the
# cast-list end: each entry's ROLE carries the side and each entry's NAME stays
# achromatic.
_TWO_SIDED_CAST_SPANS = [
    ("<setup>", "marker"),
    ("\n", "plain"),
    ('<player name="', "marker"),
    ("Vera", "attr"),
    ('" role="', "marker"),
    ("Mafia", "attr-mafia"),
    ('">', "marker"),
    ("(no persona recorded)", "plain"),
    ("</player>", "marker"),
    ("\n", "plain"),
    ('<player name="', "marker"),
    ("Iris", "attr"),
    ('" role="', "marker"),
    ("Law-abiding Citizen", "attr-law-abiding"),
    ('">', "marker"),
    ("(no persona recorded)", "plain"),
    ("</player>", "marker"),
    ("\n", "plain"),
    ("</setup>", "marker"),
]

# Every side-bearing kind → the side it names. Used where a test's claim is
# "these two spans agree about a side" rather than "this span has this kind" —
# the cast-list-matches-dialogue requirement, and the per-line speaker/speech
# agreement.
#
# Slice 5 adds the four side-qualified SEAT kinds, and adding them is the point:
# the seat's side is still a side, so "the cast list and the dialogue agree"
# must go on holding for the one player the reviewer cares most about. The two
# seat kinds that name no side (`speaker-human`, `speech-human`) are deliberately
# absent — this table's keys are exactly `_SIDE_KINDS`, which
# `test_the_kind_family_tuples_are_index_aligned_and_complete` pins.
_SIDE_OF_KIND = {
    eval_ledger.KIND_SPEAKER_MAFIA: "mafia",
    eval_ledger.KIND_SPEECH_MAFIA: "mafia",
    eval_ledger.KIND_ATTR_MAFIA: "mafia",
    eval_ledger.KIND_SPEAKER_LAW_ABIDING: "law-abiding",
    eval_ledger.KIND_SPEECH_LAW_ABIDING: "law-abiding",
    eval_ledger.KIND_ATTR_LAW_ABIDING: "law-abiding",
    eval_ledger.KIND_SPEAKER_MAFIA_HUMAN: "mafia",
    eval_ledger.KIND_SPEECH_MAFIA_HUMAN: "mafia",
    eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN: "law-abiding",
    eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN: "law-abiding",
}


def _dialogue_spans(source: str) -> list[tuple[str, str]]:
    """Every span after the cast list, with the whole source's round trip checked.

    The tests in this section are about what a name's side does to the lines
    BELOW the cast list, and re-pinning the cast list's own nineteen spans in
    each of them would bury the one line that is actually under test. So this
    returns the tail after `</setup>` and its separator — still an absolute,
    complete span list, just of the half the test is making a claim about
    (`_TWO_SIDED_CAST_SPANS` pins the other half, once).

    The round trip is asserted over the WHOLE source, not the tail, because that
    is the invariant's actual scope and this is a free place to check it on
    twenty-odd synthetic games the corpus sweep will never see.
    """
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source, (
        "the round trip fails on this synthetic input"
    )
    for index, span in enumerate(spans):
        if span == ("</setup>", eval_ledger.KIND_MARKER):
            tail = spans[index + 1 :]
            break
    else:
        pytest.fail(
            f"no `</setup>` marker span in {spans!r} — this helper is for inputs "
            "that carry a closed cast list"
        )
    assert tail and tail[0] == ("\n", eval_ledger.KIND_PLAIN), (
        f"expected a separator after `</setup>`, got {tail[:1]!r}"
    )
    return tail[1:]


# ---------------------------------------------------------------------------
# The two sides, and the neutral that is NOT `plain`
# ---------------------------------------------------------------------------


def test_the_cast_list_itself_colours_the_role_and_not_the_name() -> None:
    """The cast list's own spans, pinned absolutely (functional-spec §2).

    "Given the reviewer looks at the opening cast list, when they read the roles
    there, then the side colours used in the cast list match the ones used for
    dialogue." The cast-list half of that is here; the dialogue half is the next
    test; the two are tied together in
    :func:`test_the_cast_lists_role_text_matches_the_dialogue_kinds`.

    The ratified narrow reading is what makes this worth pinning absolutely: the
    side lands on the ROLE, and the NAME beside it stays achromatic `attr`.
    Colouring the name too was considered and rejected — it would leave `attr`
    with no occupants in any real transcript and retire Slice 2's treatment by
    accident — so "Vera is `attr`, `Mafia` is `attr-mafia`" is a decision, not an
    implementation detail.
    """
    assert _spans(_TWO_SIDED_CAST.rstrip("\n")) == _TWO_SIDED_CAST_SPANS


def test_a_mafia_speaker_and_a_law_abiding_speaker_from_one_cast_get_different_kinds() -> None:
    """The spec's headline requirement, on the smallest input that can show it.

    "Given a Day round in which both sides speak, when the reviewer looks at the
    round, then Mafia lines and Law-abiding lines are visibly different colours"
    (functional-spec §2). Once colour is a CSS concern that reduces to: the two
    speakers get different KINDS, and each speaker's name and words share one.
    """
    source = _TWO_SIDED_CAST + "Vera: It was quiet last night.\nIris: Too quiet."

    assert _dialogue_spans(source) == [
        ("Vera:", "speaker-mafia"),
        (" It was quiet last night.", "speech-mafia"),
        ("\n", "plain"),
        ("Iris:", "speaker-law-abiding"),
        (" Too quiet.", "speech-law-abiding"),
    ]


def test_the_same_two_lines_are_neutral_without_the_cast_list_that_names_them() -> None:
    """The control for the test above, and the property the whole slice turns on.

    Absolute equality with a hard-coded expectation can hold on a tokenizer that
    ignores the cast list entirely and hard-codes "the first speaker is Mafia" —
    spec 037's mutation finding, in its sharpest local form. The same two lines
    with the `<setup>` block removed must come back NEUTRAL, which no
    line-shaped rule could produce.

    It is also the plainest statement of what changed in Slice 4: the tokenizer
    is a function of the FILE now, so these two inputs differ in a part of the
    text that is nowhere near the lines being asserted about.
    """
    dialogue = "Vera: It was quiet last night.\nIris: Too quiet."

    assert _spans(dialogue) == [
        ("Vera:", "speaker"),
        (" It was quiet last night.", "speech"),
        ("\n", "plain"),
        ("Iris:", "speaker"),
        (" Too quiet.", "speech"),
    ]


def test_a_speaker_absent_from_the_cast_list_is_neutral_speech_not_plain() -> None:
    """**The single most important assertion of Slice 4.**

    A name the cast list does not carry gets the NEUTRAL `speaker` / `speech`
    kinds — *not* the `plain` kind. Slice 4's own task text and tech-spec §2 A
    both say "yields `plain`", and both are loose wording from before `speech`
    existed as a kind: tech-spec §2 B promises in the same document that a
    degraded file keeps `marker`, **`speaker`**, **`speech`**, `field-label` and
    `plain` spans, and the task's own sentence — "its speech falls back to
    `plain` **while speaker prefixes still work**" — is self-refuting under the
    literal reading, since a `plain` line has no speaker prefix left to work.

    What "never a guess" forbids is assigning a **SIDE**. That somebody is
    speaking is a shape the line proves on its own (`_SPEAKER_RE`, zero false
    positives over 22,508 real utterances); which side they are on is a
    judgement only the cast list can settle. Reading it as the `plain` kind would
    undo Slice 3 for every unknown name and for all 30 pre-spec-022 games.

    Four statements of the same fact, because each fails differently: the exact
    spans, no side kind anywhere, no `plain` kind on the line, and — in the same
    file, so the map is provably non-empty — a known name still taking a side.
    """
    source = _TWO_SIDED_CAST + "Zed: I only just arrived.\nVera: Nobody knows him."
    spans = _dialogue_spans(source)

    # (1) Exactly this, with the same speaker/speech boundary a known name gets.
    assert spans == [
        ("Zed:", "speaker"),
        (" I only just arrived.", "speech"),
        ("\n", "plain"),
        ("Vera:", "speaker-mafia"),
        (" Nobody knows him.", "speech-mafia"),
    ]

    unknown = spans[:2]
    # (2) No side was invented for him...
    assert not {kind for _, kind in unknown} & _SIDE_KINDS, (
        "a name absent from `<setup>` was given a side — a wrong side is "
        "actively misleading, not merely ugly (tech-spec §3)"
    )
    # (3) ...and his line was NOT demoted to the `plain` kind either, which is
    # the half of this the spec documents wrongly.
    assert eval_ledger.KIND_PLAIN not in {kind for _, kind in unknown}, (
        "the unknown-side fallback is the NEUTRAL speaker/speech, never `plain` "
        "— demoting the line would drop the speaker prefix tech-spec §2 B "
        "promises a degraded file keeps"
    )
    # (4) The premise: the cast map really was populated, so (2) is the guard
    # doing its job rather than a tokenizer that never colours anything.
    assert spans[3][1] == eval_ledger.KIND_SPEAKER_MAFIA


def test_the_same_player_takes_the_same_kind_in_every_round_they_speak() -> None:
    """"That player's colour is the same every time" (functional-spec §2).

    The cast map is built once for the file, so a player's side cannot drift
    between rounds — but "cannot drift" is exactly the sort of claim that is only
    true until someone rebuilds the map per section. Two rounds, both speakers in
    each, and the second round's kinds must equal the first's.

    Pinned absolutely as well as relatively: "round 2 matches round 1" holds if
    both rounds are neutral, which is the vacuous pass spec 037 warned about.
    """
    source = _TWO_SIDED_CAST + (
        "<day>\n"
        "<round>\n"
        "Round 1.\n"
        "Vera: I was asleep.\n"
        "Iris: So you say.\n"
        "</round>\n"
        "<round>\n"
        "Round 2.\n"
        "Iris: I still say it.\n"
        "Vera: And I still was.\n"
        "</round>\n"
        "</day>"
    )
    spans = _dialogue_spans(source)
    by_speaker: dict[str, list[str]] = {}
    for index, (text, kind) in enumerate(spans):
        if kind in _SPEAKER_KINDS:
            by_speaker.setdefault(text, []).append(kind)
            # The speech beside it carries the same side, every time.
            assert spans[index + 1][1] == _SPEECH_KINDS[_SPEAKER_KINDS.index(kind)]

    # Relative: each name is one kind, whatever it is.
    assert {name: set(kinds) for name, kinds in by_speaker.items()} == {
        "Vera:": {eval_ledger.KIND_SPEAKER_MAFIA},
        "Iris:": {eval_ledger.KIND_SPEAKER_LAW_ABIDING},
    }
    # ...and the premise: each really did speak twice, in different rounds.
    assert [len(kinds) for kinds in by_speaker.values()] == [2, 2]


def test_the_cast_lists_role_text_matches_the_dialogue_kinds() -> None:
    """The cast list and the dialogue agree about every player's side.

    functional-spec §2's fourth side criterion, asserted as the agreement it
    actually is rather than as two independent expectations that happen to line
    up. Two DIFFERENT code paths produce these: a role's side is read from the
    label written in the tag (`_attr_kind`, no lookup at all), while a speaker's
    is a lookup in the map that `_cast_side_map` built from that same tag. They
    can disagree — a whitelist edit on one side only would do it — and if they
    did, the reviewer would see a Mafioso's cast entry in one colour and every
    line he speaks in the other.
    """
    source = _TWO_SIDED_CAST + "Vera: I was asleep.\nIris: So you say."
    spans = _spans(source)

    role_sides = {
        text: _SIDE_OF_KIND[kind]
        for text, kind in spans
        if kind in (eval_ledger.KIND_ATTR_MAFIA, eval_ledger.KIND_ATTR_LAW_ABIDING)
    }
    speaker_sides = {
        # `kind in _SIDE_KINDS`, not `_SPEAKER_KINDS[1:]`, which is what this read
        # until Slice 5. The slice was "every speaker kind that names a side" and
        # it stopped meaning that when `speaker-human` — a seat with NO side —
        # joined the family: the comprehension would then have looked
        # `_SIDE_OF_KIND` up on a kind that has no entry and died with a KeyError
        # the day a fixture here grew a seat.
        text.rstrip(":"): _SIDE_OF_KIND[kind]
        for text, kind in spans
        if kind in _SPEAKER_KINDS and kind in _SIDE_KINDS
    }

    # The premise: both halves were actually found.
    assert role_sides == {"Mafia": "mafia", "Law-abiding Citizen": "law-abiding"}
    assert speaker_sides == {"Vera": "mafia", "Iris": "law-abiding"}
    # ...and they agree, player by player, through the cast list that names both.
    assert speaker_sides["Vera"] == role_sides["Mafia"]
    assert speaker_sides["Iris"] == role_sides["Law-abiding Citizen"]


# ---------------------------------------------------------------------------
# Near-miss: a role label the writer never emits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [
        pytest.param("Sheriff", id="sheriff"),
        pytest.param("Mafioso", id="mafioso"),
        pytest.param("mafia", id="lowercase-mafia"),
        pytest.param("Law-abiding", id="truncated-law-abiding"),
        pytest.param("", id="empty-role"),
    ],
)
def test_a_role_label_the_writer_never_emits_yields_no_side_at_all(role: str) -> None:
    """An unrecognised role leaves BOTH the role text and the player neutral.

    `_ROLE_SIDES` is a whitelist mirroring `eval_transcript._ROLE_LABELS`, and
    the reason it is a whitelist rather than a substring test is in the writer:
    its own fallback is `_ROLE_LABELS.get(role, role or "unknown role")`, so an
    unmapped role reaches the transcript as a raw string. Guessing a side from an
    unrecognised label is exactly the "actively misleading" failure tech-spec §3
    names.

    The parameters are the near-misses a substring or casefold rule would get
    wrong: `Mafioso` and `mafia` both CONTAIN the whitelisted label,
    `Law-abiding` is its prefix, and `""` is what a `role=""` attribute carries.
    All five must leave the player unknown.

    Note `Mafioso` is not hypothetical — it is the role string
    `_RICH_SYNTHETIC_TRANSCRIPT` and this repo's other fixtures have used since
    Slice 1, and it is not a label any Graphia code emits.
    """
    source = f'<setup>\n<player name="Vera" role="{role}">\n</setup>\nVera: I was asleep.'
    spans = _dialogue_spans(source)

    assert spans == [("Vera:", "speaker"), (" I was asleep.", "speech")]
    # ...and the role text itself stays achromatic too, in the cast entry.
    assert not {kind for _, kind in _spans(source)} & _SIDE_KINDS
    assert eval_ledger._cast_side_map(source.split("\n")) == {}


# ---------------------------------------------------------------------------
# Near-miss: a cast list that is empty, missing, or never closed
# ---------------------------------------------------------------------------


def test_an_empty_cast_list_yields_no_sides_and_does_not_raise() -> None:
    """`<setup></setup>` with nothing between: an empty map, not a failure."""
    source = "<setup>\n</setup>\nVera: I was asleep."

    assert eval_ledger._cast_side_map(source.split("\n")) == {}
    assert _dialogue_spans(source) == [
        ("Vera:", "speaker"),
        (" I was asleep.", "speech"),
    ]


def test_a_missing_cast_list_yields_no_sides_and_does_not_raise() -> None:
    """No `<setup>` at all — the shape of a future format, and of a truncated file.

    Note the `<player …>` line here is NOT inside a cast list, so it contributes
    nothing to the map even though it is perfectly well formed. That is the
    scoping guard, seen from the degenerate end.
    """
    source = '<player name="Vera" role="Mafia">\nVera: I was asleep.'

    assert eval_ledger._cast_side_map(source.split("\n")) == {}
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source
    assert spans[-2:] == [("Vera:", "speaker"), (" I was asleep.", "speech")]
    # The role text still takes its side — that one is read from the label in the
    # tag, not from the map, so it needs no `<setup>` and gets none here either.
    assert ("Mafia", "attr-mafia") in spans


def test_an_unclosed_cast_list_is_read_to_the_end_of_the_file() -> None:
    """A `<setup>` with no `</setup>`: parsed to EOF, deliberately, and no raise.

    This is a DECISION being pinned rather than an accident being tolerated. The
    scan sets a flag on `<setup>` and clears it on `</setup>`; with no closing tag
    the flag never clears, so every `<player …>` line in the rest of the file
    joins the map — including one typed into a Day speech, the very thing the
    scoping guard exists to prevent.

    It is the right trade anyway: an unclosed `<setup>` is a file the writer
    cannot produce (`_wrap` emits both tags or neither), so the choice is between
    "a corrupt file colours nothing" and "a corrupt file colours what it says".
    Both are defensible; what is NOT acceptable is raising, and that is the half
    this test guards for real. If a later slice decides to close the scan at the
    first section boundary instead, this test is the record of what it is
    changing.
    """
    source = '<setup>\n<player name="Vera" role="Mafia">\nVera: I was asleep.'

    assert eval_ledger._cast_side_map(source.split("\n")) == {"Vera": "mafia"}
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source
    assert spans[-2:] == [
        ("Vera:", "speaker-mafia"),
        (" I was asleep.", "speech-mafia"),
    ]


# ---------------------------------------------------------------------------
# Near-miss: one name, two entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "expected_map", "expected_kinds"),
    [
        pytest.param(
            "Mafia",
            "Law-abiding Citizen",
            {},
            ("speaker", "speech"),
            id="two-different-sides-dropped",
        ),
        pytest.param(
            "Mafia",
            "Mafia",
            {"Vera": "mafia"},
            ("speaker-mafia", "speech-mafia"),
            id="same-side-twice-kept",
        ),
        pytest.param(
            "Mafia",
            "Sheriff",
            {},
            ("speaker", "speech"),
            id="one-known-one-unrecognised-dropped",
        ),
    ],
)
def test_a_name_the_cast_list_gives_two_sides_is_dropped_entirely(
    first: str, second: str, expected_map: dict[str, str], expected_kinds: tuple[str, str]
) -> None:
    """An ambiguous name has NO known side — never first-wins, never a coin flip.

    A coin flip here paints a whole game's dialogue in the wrong colour, which is
    the one failure mode functional-spec §2's author accepted the trade for
    ("a wrong side is actively misleading"). So a name whose entries disagree, or
    whose only recognised entry is contradicted by an unrecognised one, drops out
    of the map and reads neutral.

    **The middle parameter is the control and it is what makes the other two
    mean anything.** "Two entries → dropped" also passes on a tokenizer that
    drops every DUPLICATED name regardless of whether the entries agree — which
    would be a different rule with a different failure. The same-side-twice row
    is kept, so the assertion is about the disagreement and not about the
    repetition.
    """
    source = (
        "<setup>\n"
        f'<player name="Vera" role="{first}">\n'
        f'<player name="Vera" role="{second}">\n'
        "</setup>\n"
        "Vera: I was asleep."
    )

    assert eval_ledger._cast_side_map(source.split("\n")) == expected_map
    assert _dialogue_spans(source) == [
        ("Vera:", expected_kinds[0]),
        (" I was asleep.", expected_kinds[1]),
    ]


# ---------------------------------------------------------------------------
# Near-miss: a `<player …>` tag OUTSIDE the cast list
# ---------------------------------------------------------------------------


def test_a_player_tag_typed_into_a_speech_cannot_poison_the_cast_map() -> None:
    """One line of model output must not recolour a whole game.

    The highest-value guard in the slice after the neutral fallback, and the one
    the corpus provably cannot check: `<player …>` occurs **nowhere outside
    `<setup>`** in any of the 298 committed files, so scoping and not scoping are
    indistinguishable on today's data. But every word of a transcript outside the
    cast list is model-generated — the players' speech, their personas, the
    moderator's prose — and an unscoped scan would let a player who typed
    `<player name="Iris" role="Mafia">` into a Day speech flip the real Iris to
    the Mafia colour for the entire game.

    **Both shapes an impostor can take are here, and only one of them tests the
    guard.** A tag quoted *inside* a sentence never reaches the scan at all —
    `_TAG_HEAD_RE.match` is anchored, so a line beginning `Vera: look at this —`
    fails it whether the scan is scoped or not. Only a tag ALONE ON ITS LINE gets
    that far, which is also the realistic shape (a model asked to write dialogue
    emitting a bare structural line). A first draft of this test used the
    quoted-inside-a-sentence form only, and a mutation that deleted the `<setup>`
    scoping altogether passed it — so both are pinned, with the standalone lines
    doing the actual work.

    Two impostor lines, because unscoped scanning fails two different ways:

    * one **re-roles a real player** (Iris, Law-abiding in the cast list, claimed
      as Mafia). Unscoped, her two entries disagree and the never-guess rule
      drops her from the map entirely — so the attack does not even need to be
      believed to work: contradicting a cast entry is enough to strip the colour
      off every line that player speaks all game;
    * one **invents a player** (Zed). Unscoped, a name the cast list never named
      acquires a side out of a Day speech.

    Three halves, then: the map is exactly the cast list's, the two victims keep
    the kinds their real entries earn, and the impostor lines themselves still
    read as the text they are.
    """
    quoted = '<player name="Iris" role="Mafia">'
    source = _TWO_SIDED_CAST + (
        "<day>\n"
        '<player name="Iris" role="Mafia">\n'
        '<player name="Zed" role="Mafia">\n'
        f"Vera: look at this — {quoted}\n"
        "Iris: that proves nothing.\n"
        "Zed: nor does he."
    )

    # The map is exactly the cast list's, with no trace of either impostor.
    assert eval_ledger._cast_side_map(source.split("\n")) == {
        "Vera": "mafia",
        "Iris": "law-abiding",
    }
    assert _dialogue_spans(source) == [
        ("<day>", "marker"),
        ("\n", "plain"),
        # The impostor lines tokenize as the tags they look like — their ROLE
        # text is coloured, because that word says "Mafia" on the reviewer's
        # screen. What must not happen is that word reaching the map.
        ('<player name="', "marker"),
        ("Iris", "attr"),
        ('" role="', "marker"),
        ("Mafia", "attr-mafia"),
        ('">', "marker"),
        ("\n", "plain"),
        ('<player name="', "marker"),
        ("Zed", "attr"),
        ('" role="', "marker"),
        ("Mafia", "attr-mafia"),
        ('">', "marker"),
        ("\n", "plain"),
        # A tag quoted inside a sentence stays inside the speech span.
        ("Vera:", "speaker-mafia"),
        (f" look at this — {quoted}", "speech-mafia"),
        ("\n", "plain"),
        # THE ASSERTION: still Law-abiding, as her real cast entry says — not
        # Mafia, and not dropped to neutral by a contradiction she never made.
        ("Iris:", "speaker-law-abiding"),
        (" that proves nothing.", "speech-law-abiding"),
        ("\n", "plain"),
        # ...and the invented player got no side at all.
        ("Zed:", "speaker"),
        (" nor does he.", "speech"),
    ]


def test_a_cast_entry_for_a_name_the_setup_does_not_carry_still_colours_its_own_role() -> None:
    """The impostor's own role text is coloured — and that is correct, not a leak.

    The complement of the test above, spelled out so the two are not confused. A
    `role="Mafia"` attribute says "Mafia" on the screen wherever it appears; the
    tokenizer colours the token the reviewer is reading, which is the literal
    word in front of them. What must not happen is that word changing what the
    PLAYER's lines look like elsewhere in the game — and it does not.

    Written on a standalone tag, outside any `<setup>`, so nothing else can be
    supplying the side.
    """
    spans = _spans('<player name="Iris" role="Mafia">')

    assert spans == [
        ('<player name="', "marker"),
        ("Iris", "attr"),
        ('" role="', "marker"),
        ("Mafia", "attr-mafia"),
        ('">', "marker"),
    ]


# ---------------------------------------------------------------------------
# Near-miss: a cast name colliding with the writer's own vocabulary
# ---------------------------------------------------------------------------


def test_a_player_named_moderator_does_not_make_the_moderators_lines_speech() -> None:
    """The exclusion set still wins after Slice 4, even against a real cast entry.

    `Moderator` is the writer's own public voice, excluded from the speaker rule
    by `_NON_SPEAKER_PREFIXES`. Slice 4 gives names sides — so a roster that
    generated a player literally called "Moderator" would, if the side lookup
    were consulted BEFORE the exclusion set, turn all 2,655 of the moderator's
    corpus lines into that player's coloured speech and hand the reviewer a
    narrator with an allegiance.

    The map may well contain the name (it is a legitimate cast entry, and this
    test pins that it does); what must not happen is the line being read as
    speech at all.
    """
    source = (
        "<setup>\n"
        '<player name="Moderator" role="Mafia">\n'
        "</setup>\n"
        "Moderator: A new game begins. Welcome, Vera."
    )

    # The cast entry is honoured as a cast entry...
    assert eval_ledger._cast_side_map(source.split("\n")) == {"Moderator": "mafia"}
    # ...and the moderator's line is still not somebody speaking. Pinned as the
    # whole span list rather than through `_dialogue_spans`, because the line is
    # so thoroughly unpainted that it COALESCES WITH ITS OWN SEPARATOR into a
    # single plain span — the strongest form the claim can take, and one the
    # helper's "a separator follows `</setup>`" precondition cannot express.
    assert _spans(source) == [
        ("<setup>", "marker"),
        ("\n", "plain"),
        ('<player name="', "marker"),
        ("Moderator", "attr"),
        ('" role="', "marker"),
        ("Mafia", "attr-mafia"),
        ('">', "marker"),
        ("\n", "plain"),
        ("</setup>", "marker"),
        ("\nModerator: A new game begins. Welcome, Vera.", "plain"),
    ]


def test_a_player_named_personality_does_not_turn_a_field_label_into_speech() -> None:
    """The field-label branch still outranks the speaker branch after Slice 4.

    `Personality: brisk` is shaped exactly like a player named "Personality"
    speaking, and the ordering that resolves it — field label above speaker — is
    the load-bearing decision of the whole chain: inverted, it swallows all 6,200
    cast-list labels in the corpus and renders the opening cast list as a
    transcript of six people called Personality, Manner, Persona, Public legend
    and True self (hidden).

    Slice 4 threads a cast map through that chain, which is a new way to get the
    ordering wrong: consulting the map first, or promoting a line because its
    prefix is a known name, would put the speaker reading back on top for exactly
    the names the cast list carries. So the collision is pinned with the name
    actually in `<setup>` and given a side.
    """
    source = (
        "<setup>\n"
        '<player name="Personality" role="Mafia">\n'
        "</setup>\n"
        "Personality: brisk and sly"
    )

    assert eval_ledger._cast_side_map(source.split("\n")) == {"Personality": "mafia"}
    assert _dialogue_spans(source) == [
        ("Personality:", "field-label"),
        (" brisk and sly", "plain"),
    ]


# ---------------------------------------------------------------------------
# Near-miss: the `<thought player="X">` owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "expected_kind"),
    [
        pytest.param("Vera", "attr-mafia", id="mafia-owner"),
        pytest.param("Iris", "attr-law-abiding", id="law-abiding-owner"),
        pytest.param("Zed", "attr", id="owner-absent-from-the-cast-list"),
    ],
)
def test_a_thought_owner_in_the_cast_list_takes_their_side(
    owner: str, expected_kind: str
) -> None:
    """"The owner's name carries that player's side colour" (functional-spec §2).

    The ONE place a `name`-shaped attribute is side-bearing, and it is stated in
    the functional spec in those words. The underlying rule: inside a marker the
    side lands on whichever token tells the reviewer the side — the ROLE where a
    role is written (a cast entry), the NAME where none is (a `<thought>` tag,
    which names a person and nothing else).

    Unlike a role, this one IS a map lookup, so the third parameter is the
    fallback: an owner the cast list does not know keeps the achromatic `attr`
    Slice 2 gave them. The corpus cannot check that — all 8,183 of its thought
    owners are in their own file's cast list — so this row is the only thing
    standing between a `sides[value]` and a `KeyError` on the first game whose
    thought outlives its speaker's cast entry.

    The BODY is deliberately not in the expectation's side: a private reflection
    is never side-tinted (tasks.md, Slice 3's permanent ruling — tinting it would
    claim the thought is an act of allegiance and would make thought and speech
    share a colour), and the tags around it stay `marker`.
    """
    source = _TWO_SIDED_CAST + f'<thought player="{owner}">Nobody suspects me.</thought>'

    assert _dialogue_spans(source) == [
        ('<thought player="', "marker"),
        (owner, expected_kind),
        ('">', "marker"),
        ("Nobody suspects me.", "thought"),
        ("</thought>", "marker"),
    ]


# ---------------------------------------------------------------------------
# Near-miss: the `<diary player="X" day="N">` owner (spec 039)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "expected_kind"),
    [
        pytest.param("Vera", "attr-mafia", id="mafia-owner"),
        pytest.param("Iris", "attr-law-abiding", id="law-abiding-owner"),
        pytest.param("Zed", "attr", id="owner-absent-from-the-cast-list"),
    ],
)
def test_a_diary_owner_in_the_cast_list_takes_their_side(
    owner: str, expected_kind: str
) -> None:
    """The SECOND place a name-shaped attribute is side-bearing (spec 039).

    Ratified during spec 039's Slice 4, and it follows :func:`_attr_kind`'s own
    rule rather than being a new one: inside a marker the side lands on whichever
    token actually TELLS the reviewer the side — the role where a role is
    written, the name where none is. A ``<diary>`` names a person and a Day and
    no role, exactly as a ``<thought>`` names a person and nothing else, so the
    owner carries it. Leaving it achromatic was the alternative and was rejected
    because it would show one player's name in two different treatments a few
    lines apart in the same file, inviting a reviewer to read a distinction that
    is not there.

    **A copy of the thought's test rather than a parametrization of it, on
    purpose.** The decision is per ``(tag, key)`` PAIR: ``<thought player=…>``
    having a branch is not what gives ``<diary player=…>`` one, the production
    code needed a second ``elif``, and a tag nobody has ruled on still gets no
    side. Folding the two tests together would assert exactly the thing the
    tag-scoped rule exists to deny.

    Three claims ride on every row, and only the first is the headline:

    * **the owner takes their side**, or the achromatic ``attr`` when the cast
      list does not know them (the third row). Never a guessed side — and the
      corpus cannot check that row either, since ``<diary`` occurs nowhere in it;
    * **the ``day`` value beside it stays achromatic in all three**, including
      the two rows where a side was sitting right there to spend. It names a Day,
      not a person, so there is no side it could carry;
    * **the BODY stays ``diary`` and is never side-tinted in any row** — a
      private reflection is not an act of allegiance (tech-spec 039 §2.8, which
      puts the diary body under ``<thought>``'s precedent in those words). The
      owner coloured and the prose not, on one line, is the whole rule.
    """
    source = _TWO_SIDED_CAST + (
        f'<diary player="{owner}" day="2">Nobody suspects me.</diary>'
    )

    assert _dialogue_spans(source) == [
        ('<diary player="', "marker"),
        (owner, expected_kind),
        ('" day="', "marker"),
        ("2", "attr"),
        ('">', "marker"),
        ("Nobody suspects me.", "diary"),
        ("</diary>", "marker"),
    ]


# ---------------------------------------------------------------------------
# Near-miss: the three attributes that must stay achromatic
# ---------------------------------------------------------------------------


def test_a_votes_initiator_and_target_stay_achromatic_even_when_both_are_known() -> None:
    """A vote marker names two players the map knows, and neither takes a side.

    This pins the ratified narrow reading at the point it is easiest to get
    wrong: the two names ARE in the cast map, a lookup would succeed, and
    colouring them would look like an improvement. It is not. From Slice 4 colour
    means side, and `attr` lifts a *specific* out of the punctuation by weight
    and brightness — a vote marker's job is to say who called it and against
    whom, which the coloured ballots below it already answer. Tinting every name
    would also leave `attr` with zero occupants in any real transcript, retiring
    Slice 2's treatment by accident.

    The ballot inside the block is included precisely to show the contrast: the
    same name, one line apart, achromatic in the marker and side-coloured as
    speech.
    """
    source = _TWO_SIDED_CAST + (
        '<vote initiator="Vera" target="Iris">\n'
        "Iris: No\n"
        "</vote>"
    )

    assert _dialogue_spans(source) == [
        ('<vote initiator="', "marker"),
        ("Vera", "attr"),
        ('" target="', "marker"),
        ("Iris", "attr"),
        ('">', "marker"),
        ("\n", "plain"),
        # ...and one line later, the same name IS coloured, because a ballot is
        # the player's own word (tasks.md, Slice 3).
        ("Iris:", "speaker-law-abiding"),
        (" No", "speech-law-abiding"),
        ("\n", "plain"),
        ("</vote>", "marker"),
    ]


def test_a_kill_line_and_a_recap_body_naming_a_side_stay_untinted() -> None:
    """Two bodies that MENTION a side in their text and must not be coloured by it.

    A `<kill>Iris — Law-abiding Citizen</kill>` line contains a whitelisted role
    label verbatim, and a `<recap>` body counts both sides by name. Neither is an
    attribute, so neither goes near `_attr_kind` — but a "colour any recognised
    role label" shortcut would tint both, and the recap would then read as an
    opinion with an allegiance rather than as the moderator's fact.

    `<kill>` keeps its Slice-1 shape too: its content is `marker`, so the whole
    element coalesces to one span.
    """
    source = _TWO_SIDED_CAST + (
        "<kill>Iris — Law-abiding Citizen</kill>\n"
        "<recap>Alive: Vera. Mafia 1, Law-abiding 0.</recap>"
    )

    assert _dialogue_spans(source) == [
        ("<kill>Iris — Law-abiding Citizen</kill>", "marker"),
        ("\n", "plain"),
        ("<recap>", "marker"),
        ("Alive: Vera. Mafia 1, Law-abiding 0.", "recap"),
        ("</recap>", "marker"),
    ]


# ---------------------------------------------------------------------------
# Near-miss: the shapes the two eras share
# ---------------------------------------------------------------------------


def test_an_indented_pre_022_style_setup_still_builds_the_cast_map() -> None:
    """`  <setup>` with a spec-022 `<player>` inside it: the literals match lstripped.

    A hybrid the corpus does not contain — the pre-022 era's indentation with the
    spec-022 era's cast tag — and it is worth pinning because the map's two
    section literals are matched against the STRIPPED line while the tag itself
    is matched against the stripped body. Comparing either against the raw line
    would silently return an empty map for any indented file, and the only
    symptom would be a game that quietly lost its colours.
    """
    source = (
        "  <setup>\n"
        '  <player name="Vera" role="Mafia">\n'
        "  </setup>\n"
        "Vera: I was asleep."
    )

    assert eval_ledger._cast_side_map(source.split("\n")) == {"Vera": "mafia"}
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source
    assert spans[-2:] == [
        ("Vera:", "speaker-mafia"),
        (" I was asleep.", "speech-mafia"),
    ]
    # The indent is still layout, never skeleton — unchanged by Slice 4.
    assert spans[0] == ("  ", eval_ledger.KIND_PLAIN)


def test_an_empty_cast_name_contributes_nothing_and_leaves_no_empty_span() -> None:
    """`name=""`: no map entry, no zero-length span, and the role still coloured.

    Two guarantees meeting on one line. The map skips a falsy name (an entry for
    `""` could only ever match a speaker prefix of `":"`, which `_SPEAKER_RE`
    cannot produce), and `_coalesce_spans` drops the zero-length value span at
    the single exit point, merging the markers on either side of it back into
    one — which is why the head below is `<player name="" role="` rather than
    three spans with an empty one in the middle.
    """
    source = '<setup>\n<player name="" role="Mafia">\n</setup>\nVera: I was asleep.'

    assert eval_ledger._cast_side_map(source.split("\n")) == {}
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source
    assert all(text for text, _ in spans), "a span has empty text"
    assert spans[2:5] == [
        ('<player name="" role="', "marker"),
        ("Mafia", "attr-mafia"),
        ('">', "marker"),
    ]
    assert spans[-2:] == [("Vera:", "speaker"), (" I was asleep.", "speech")]


# ---------------------------------------------------------------------------
# Pre-spec-022 degradation, on the real committed files
# ---------------------------------------------------------------------------

# The three run dirs written before spec 022 gave the transcript its structured
# form. They write the cast list as an indented `Name — Role` and carry no
# `<player>` tag at all, so `_cast_side_map` returns `{}` for every file in them
# — which is the only place in the whole corpus where the neutral `speaker` /
# `speech` kinds actually occur (all 1,853 of them).
#
# Hard-coded rather than discovered, because they are a closed historical set: no
# future eval run can add a pre-022 transcript. Their absence is handled the same
# way the sweep handles a missing corpus — skip, never fail.
_PRE_022_RUN_DIRS = (
    "2026-06-19T18-33-37",
    "2026-06-20T14-17-09",
    "2026-06-20T18-18-52",
)

_PRE_022_TRANSCRIPTS = [
    path for path in _TRANSCRIPTS if path.parent.name in _PRE_022_RUN_DIRS
]

# One representative file per pre-022 run dir — the first in sorted order, so a
# failure is reproducible and names a specific game.
_PRE_022_FILE_PARAMS = [
    pytest.param(
        min(
            (path for path in _PRE_022_TRANSCRIPTS if path.parent.name == run_dir),
            default=None,
        ),
        id=run_dir,
        marks=(
            []
            if any(path.parent.name == run_dir for path in _PRE_022_TRANSCRIPTS)
            else [pytest.mark.skip(reason=f"pre-022 run dir {run_dir} not committed")]
        ),
    )
    for run_dir in _PRE_022_RUN_DIRS
]


@pytest.mark.parametrize("path", _PRE_022_FILE_PARAMS)
def test_a_real_pre_022_game_keeps_its_markers_and_speech_and_gains_no_sides(
    path: Path,
) -> None:
    """Tech-spec §2 B's degradation promise, on a real game from each old run.

    "A pre-022 file still gets `marker`, `speaker`, `speech`, `field-label` and
    `plain` spans — it simply has no side map. **Nothing errors.**" Best-effort by
    the author's explicit decision: the tokenizer parses the spec-022 cast list
    only, and **a second parser for the old form was considered and declined**,
    so this is the forward-compatibility posture for the next format change as
    much as it is backwards compatibility with this one.

    Every clause of that sentence is asserted, and the positive half matters as
    much as the negative one. "No side kinds" alone is satisfied by a tokenizer
    that crashed into returning one `plain` span for the file — which is why the
    four surviving kinds are required to be present, not merely permitted.
    """
    text = path.read_text(encoding="utf-8")

    # The premise: a real game, and one whose era really has no cast tag.
    assert len(text) > 5000, f"{_rel(path)} is too small to be a real game"
    assert "<player" not in text, (
        f"{_rel(path)} carries a `<player>` tag — it is not a pre-022 file, so "
        "this test is measuring the wrong era"
    )
    assert eval_ledger._cast_side_map(text.split("\n")) == {}

    spans = _spans(text)
    kinds = Counter(kind for _, kind in spans)

    # Nothing errored, and nothing was lost.
    assert "".join(span_text for span_text, _ in spans) == text
    # NO side kind anywhere — the whole point.
    assert not set(kinds) & _SIDE_KINDS, (
        f"{_rel(path)} produced side kinds "
        f"{sorted(set(kinds) & _SIDE_KINDS)} with an empty cast map"
    )
    # ...and the five kinds tech-spec §2 B promises survive really do.
    for kind in (
        eval_ledger.KIND_MARKER,
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_SPEECH,
        eval_ledger.KIND_FIELD_LABEL,
        eval_ledger.KIND_PLAIN,
    ):
        assert kinds[kind] > 0, (
            f"{_rel(path)} lost its {kind} spans in the degraded path: {kinds}"
        )
    assert kinds[eval_ledger.KIND_SPEAKER] == kinds[eval_ledger.KIND_SPEECH]


@_requires_corpus
def test_the_pre_022_era_is_exactly_where_the_neutral_kinds_live() -> None:
    """The era split, measured over the whole corpus rather than asserted per file.

    Two directions, and together they are what makes every side assertion in this
    module non-vacuous on real data:

    * **no pre-022 file produces a side kind** — the degradation, swept over all
      30 rather than the 3 sampled above;
    * **no spec-022 file produces a neutral `speaker`** — every one of the 22,508
      real utterances outside the old era is under a name its own cast list
      carries, so a spec-022 game that suddenly went neutral means the cast
      parser stopped finding its `<setup>`, which is a silent total loss of
      colour that no other test in this file would notice.

    The second direction is the load-bearing one and it cannot be written as a
    count: the corpus grows with every committed eval run. Written as a partition
    instead — which era a file belongs to decides which kinds it may emit — it
    survives the corpus doubling.

    **Slice 5 widened the second direction to the SEAT kind whose side is
    unknown.** `speaker-human` is "the seat, in a game with no cast list to read a
    side from", and measurement puts every one of its 183 spans inside the same 30
    pre-022 files for exactly the reason the neutral `speaker` is: those are the
    only games whose cast map is empty. So a spec-022 game emitting either of them
    means the same single failure — the cast parser stopped finding `<setup>` —
    and leaving `speaker-human` out of this check would let that failure hide in
    whichever files happen to be the seat's.
    """
    era_only_kinds = (eval_ledger.KIND_SPEAKER, eval_ledger.KIND_SPEAKER_HUMAN)
    pre_022_with_sides: list[str] = []
    spec_022_with_neutral_speech: list[str] = []
    pre_022_seated = 0

    for path in _TRANSCRIPTS:
        kinds = {kind for _, kind in _spans(path.read_text(encoding="utf-8"))}
        if path.parent.name in _PRE_022_RUN_DIRS:
            if kinds & _SIDE_KINDS:
                pre_022_with_sides.append(_rel(path))
            if eval_ledger.KIND_SPEAKER_HUMAN in kinds:
                pre_022_seated += 1
        elif kinds & set(era_only_kinds):
            spec_022_with_neutral_speech.append(_rel(path))

    assert not pre_022_with_sides, (
        "pre-022 games have no cast list to read, so they can have no sides: "
        f"{pre_022_with_sides[:_MAX_REPORTED_FILES]}"
    )
    assert not spec_022_with_neutral_speech, (
        "a spec-022 game produced a NEUTRAL speaker span (seated or not) — every "
        "speaker in that era is in their own file's cast list, so this means the "
        "cast parser stopped finding `<setup>`: "
        f"{spec_022_with_neutral_speech[:_MAX_REPORTED_FILES]}"
    )
    # The premise for both: each era really is represented...
    assert _PRE_022_TRANSCRIPTS, "no pre-022 transcripts found to degrade"
    assert len(_TRANSCRIPTS) > len(_PRE_022_TRANSCRIPTS), "no spec-022 transcripts"
    # ...and the old era really is where the sideless SEAT lives, so the widened
    # half of the partition is checking a kind real data actually produces rather
    # than an empty set on both sides.
    assert pre_022_seated > 0, (
        "no pre-022 game bolds a seat — `speaker-human` (seat known, side "
        "unknown) has no real-data occupant left and this partition is vacuous"
    )


# ---------------------------------------------------------------------------
# Conservation: the side map moves kinds, never a boundary
# ---------------------------------------------------------------------------


@_requires_corpus
def test_the_side_map_moves_kinds_and_never_a_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 4 re-kinded 12,000 spans and re-split none — asserted, not counted.

    `tasks.md` records the measurement as three absolute totals ("193,740,
    identical to Slice 3; `attr` 3,970 + `attr-mafia` 3,695 + `attr-law-abiding`
    6,448 = 14,113"). Those are the right *finding* and the wrong *assertion*:
    the corpus grows with every committed eval run, so a pinned total is a test
    that fails on a green tree.

    The relation behind the totals is what is pinned instead. Tokenize every
    committed game twice — once normally, once with the cast scan stubbed to
    return an empty map — and require the two runs to produce **the same spans of
    text in the same order**, differing only in which kind each carries, and
    differing only by the side split (`speaker-mafia` where the neutral run says
    `speaker`, and never anything else). That is exactly "only kinds moved, no
    boundary did", it holds however large the corpus grows, and it is stronger
    than the totals were: a boundary that moved in a way that preserved the
    counts would still fail.

    It doubles as the pre-022 degradation path swept over all 298 files rather
    than the 30 that exercise it naturally — an empty cast map is precisely what
    those 30 produce.
    """
    real_cast_side_map = eval_ledger._cast_side_map
    moved = Counter()
    checked = 0

    for path in _TRANSCRIPTS:
        text = path.read_text(encoding="utf-8")
        with_sides = _spans(text)
        monkeypatch.setattr(eval_ledger, "_cast_side_map", lambda lines: {})
        neutral = _spans(text)
        monkeypatch.setattr(eval_ledger, "_cast_side_map", real_cast_side_map)
        checked += 1

        assert [span_text for span_text, _ in with_sides] == [
            span_text for span_text, _ in neutral
        ], (
            f"{_rel(path)}: the cast map moved a span BOUNDARY, not just a kind — "
            "Slice 4 re-kinds spans and must never re-split a line"
        )
        for (span_text, kind), (_, neutral_kind) in zip(
            with_sides, neutral, strict=True
        ):
            if kind == neutral_kind:
                continue
            assert _NEUTRAL_BASE_KIND.get(kind) == neutral_kind, (
                f"{_rel(path)}: {span_text[:40]!r} is {kind} with a cast map and "
                f"{neutral_kind} without — the only difference a cast map may "
                "make is the side split"
            )
            moved[kind] += 1

    # The premise, as a relation rather than a total: every side-bearing kind
    # really did get exercised, so a stub that returned the real map (or a
    # tokenizer that ignored it) could not pass by moving nothing.
    assert checked == len(_TRANSCRIPTS)
    assert set(moved) == _SIDE_KINDS, (
        f"kinds actually moved by the cast map: {sorted(moved)}; expected all of "
        f"{sorted(_SIDE_KINDS)}"
    )


# ===========================================================================
# 4. The reviewer's own seat (spec 038, Slice 5)
# ===========================================================================
#
# `tokenize_transcript` now makes TWO whole-file passes, not one. `_cast_side_map`
# answers "which side is this name on"; `_human_seat` answers "which seat was the
# person's" — and the two are INDEPENDENT, which is the fact every test in this
# section is built to hold onto. A game can state either without the other, and
# all 30 pre-spec-022 games are exactly that case: they name their seat in the
# preamble greeting and yield no cast map at all.
#
# TWO ROUTES TO A SEAT, AND THE MARKER WINS:
#
#   1. the writer's `<player … human="true">` entry inside `<setup>` — explicit,
#      carried by every transcript written from Slice 5 onward;
#   2. failing that, the preamble's `Moderator: A new game begins. Welcome, <name>.`
#      line — the inference that covers the 298 already-committed transcripts.
#
# WHY ALMOST EVERYTHING BELOW IS SYNTHETIC, MEASURED RATHER THAN ASSUMED. The
# corpus's coverage of this slice is lopsided in a way that is the exact inverse
# of what the earlier slices met:
#
#   * the INFERENCE has total coverage — the greeting appears exactly once in all
#     298 files, always inside `<preamble>`, and the seat it names is `Avery` in
#     every one of them;
#   * the MARKER path, the BOTH-PRESENT path and the NO-SEAT path have **ZERO**
#     coverage: `human=` appears in **0 of the 298** committed files. Every
#     assertion about them is synthetic or it does not exist.
#
# So the near-misses here are not decoration, they are the whole of the marker
# route's test suite: a marker with no greeting, a marker and a greeting that
# disagree, a marker whose value is not the one literal, a marker outside
# `<setup>`, two contradicting markers, a marker with no usable name, a greeting
# outside `<preamble>`, and a game that states nothing at all.
#
# ONE MORE STRUCTURAL FACT, because it shapes several tests below: a transcript
# has exactly ONE seat, so a file can emit at most TWO of the six seat kinds.
# Anything wanting all six needs more than one game.


# The writer's own marker text (`eval_transcript._HUMAN_ATTR`), spelled out here
# as a literal rather than imported, for the reason `_EXPECTED_ROLE_KINDS` is:
# a fixture built from the production constant cannot notice the production
# constant changing. The two are tied together, once, in
# `test_the_writer_and_the_reader_agree_on_the_marker_literal`.
_HUMAN_MARKER = 'human="true"'

# The moderator's greeting, verbatim from `graphia.nodes.setup.collect_name`'s
# `f"A new game begins. Welcome, {name}."` with the writer's `Moderator: ` voice
# in front of it. Same reasoning: a literal, tied to the production regex once.
_WELCOME_TEMPLATE = "Moderator: A new game begins. Welcome, {name}."


def _cast_entry(name: str, role: str, *, marker: str | None = None) -> str:
    """One `<setup>` cast entry line, optionally carrying a `human=` attribute.

    `marker` is the raw attribute VALUE (`"true"`, `"false"`, `"TRUE"`, `""`) so a
    near-miss can put something other than the one blessed literal in the file;
    `None` emits the entry exactly as the writer emitted it before Slice 5.

    The attribute order — `name`, then `role`, then `human` — is the writer's own
    (`eval_transcript._render_setup` appends the marker after `role=`), and it
    matters: it is what makes the marker land in the tag's TRAILING punctuation
    span rather than between two `attr` values.
    """
    flag = "" if marker is None else f' human="{marker}"'
    return f'<player name="{name}" role="{role}"{flag}>(no persona recorded)</player>\n'


def _cast(*entries: str) -> str:
    """A `<setup>` block wrapping the given cast entry lines."""
    return "<setup>\n" + "".join(entries) + "</setup>\n"


def _preamble(*names: str) -> str:
    """A `<preamble>` block whose moderator greeting welcomes each of ``names``.

    More than one greeting is a contradiction the file cannot resolve, and there
    is a test for exactly that; one is the shape all 298 committed games have.
    """
    lines = "".join(_WELCOME_TEMPLATE.format(name=name) + "\n" for name in names)
    return "<preamble>\n" + lines + "</preamble>\n"


# The two section closers a synthetic game's dialogue can begin after. `<setup>`
# alone was enough for Slice 4; from Slice 5 a fixture usually carries a
# `<preamble>` too, because that is where the greeting the seat is read from
# lives.
_HEADER_CLOSERS = ("</setup>", "</preamble>")


def _spoken_spans(source: str) -> list[tuple[str, str]]:
    """Every span after the last `</setup>` / `</preamble>`, separator dropped.

    `_dialogue_spans` (Slice 4) cuts after `</setup>`, which was the whole header
    then. A Slice-5 fixture usually has a `<preamble>` after it, so cutting there
    would leave the greeting block in every expected span list and bury the one
    line under test — and the greeting is deliberately unremarkable text, which
    is the point of it.

    The leading separator is stripped rather than asserted away, because it does
    not always survive as a span of its own: `_coalesce_spans` merges it into the
    next line whenever that line is also `plain`, which is exactly what happens
    when the dialogue opens on a moderator line. Handling it here keeps every
    expectation below a clean list of the spans a reader actually cares about.

    The round trip is asserted over the WHOLE source, as `_dialogue_spans` does,
    since these fixtures are games the corpus sweep will never see.
    """
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source, (
        "the round trip fails on this synthetic input"
    )
    closers = [
        index
        for index, (text, kind) in enumerate(spans)
        if kind == eval_ledger.KIND_MARKER and text in _HEADER_CLOSERS
    ]
    assert closers, (
        f"no closed `<setup>`/`<preamble>` in {spans!r} — this helper is for "
        "inputs that carry one"
    )
    tail = spans[closers[-1] + 1 :]
    assert tail, "nothing follows the header blocks"
    first_text, first_kind = tail[0]
    assert first_kind == eval_ledger.KIND_PLAIN and first_text.startswith("\n"), (
        f"expected a separator after the header blocks, got {tail[:1]!r}"
    )
    remainder = first_text[1:]
    return ([(remainder, first_kind)] if remainder else []) + tail[1:]


def _seat_kinds(spans: list[tuple[str, str]]) -> set[str]:
    """Every seat-bearing kind in ``spans`` — the "who is bolded" summary."""
    return {kind for _, kind in spans} & _HUMAN_KINDS


def _bolded_names(spans: list[tuple[str, str]]) -> set[str]:
    """The speaker names carrying a seat kind, without their colons.

    "Which seats are bolded" as a set of *people*, so a test's failure names the
    player who was wrongly marked rather than a kind string.
    """
    return {
        text.rstrip(":")
        for text, kind in spans
        if kind in _HUMAN_KINDS and kind in _SPEAKER_KINDS
    }


# ---------------------------------------------------------------------------
# The two routes, both sides
# ---------------------------------------------------------------------------


def test_the_writers_marker_bolds_a_mafia_seat_within_its_side() -> None:
    """Near-miss 1: the marker route, with **no greeting in the file at all**.

    The shape every transcript written from Slice 5 onward has, and a shape **no
    committed transcript has** — `human=` appears in 0 of the 298 — so this test
    is the entirety of that route's coverage together with its siblings below.

    Pinned absolutely, and the fixture's third player is what makes the pin worth
    reading: Dot is a Mafia who is NOT the seat. functional-spec §2 states the
    requirement as a contrast — the seat's lines are "in its side's colour, but
    **bold**", while other players on the same side are "the same colour but
    **not bold**" — so a test that only looked at Vera would pass on a tokenizer
    that bolded the whole Mafia side. Iris is here for the other half: the seat
    does not leak across sides either.
    """
    spans = _spoken_spans(_MAFIA_SEAT_GAME)

    assert spans == [
        ("<day>", "marker"),
        ("\n", "plain"),
        # The seat: its side's kind, in its seated form.
        ("Vera:", "speaker-mafia-human"),
        (" I was asleep.", "speech-mafia-human"),
        ("\n", "plain"),
        # A side-mate: the SAME side, NOT seated. This pair is the requirement.
        ("Dot:", "speaker-mafia"),
        (" So was I.", "speech-mafia"),
        ("\n", "plain"),
        ("Iris:", "speaker-law-abiding"),
        (" Neither of you was.", "speech-law-abiding"),
        ("\n", "plain"),
        ("</day>", "marker"),
        ("\n", "plain"),
        ("</transcript>", "marker"),
    ]
    # Restated as the two properties the span list encodes, so a future re-kind
    # fails with the rule's own name and not only with a longer diff.
    assert _bolded_names(spans) == {"Vera"}
    assert _MAFIA_SEAT_GAME.count("Welcome,") == 0, (
        "the premise: this game names its seat by MARKER only — a greeting here "
        "would make the marker route untested"
    )


def test_the_greeting_bolds_a_law_abiding_seat_within_its_side() -> None:
    """The inference route, with no marker — the shape all 298 committed games have.

    The mirror of the test above on every axis: the other route, the other side,
    and a seat read from the preamble's `Moderator: A new game begins. Welcome,
    <name>.` line rather than from an attribute. Wren is the non-seat side-mate
    that makes "bold means the seat, not the side" the thing being asserted.

    The greeting line itself must stay unstyled while being read, which the span
    list pins on its way past: the moderator is in the writer-vocabulary exclusion
    set, so his line is `plain` and coalesces with the separators around it.
    """
    spans = _spans(_LAW_ABIDING_SEAT_GAME)

    assert _bolded_names(spans) == {"Iris"}
    assert _seat_kinds(spans) == {
        eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN,
        eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN,
    }
    # The dialogue, pinned absolutely.
    assert _spoken_spans(_LAW_ABIDING_SEAT_GAME) == [
        ("<day>", "marker"),
        ("\n", "plain"),
        ("Iris:", "speaker-law-abiding-human"),
        (" I was asleep.", "speech-law-abiding-human"),
        ("\n", "plain"),
        ("Wren:", "speaker-law-abiding"),
        (" So was I.", "speech-law-abiding"),
        ("\n", "plain"),
        ("Vera:", "speaker-mafia"),
        (" Neither of you was.", "speech-mafia"),
        ("\n", "plain"),
        ("</day>", "marker"),
        ("\n", "plain"),
        ("</transcript>", "marker"),
    ]
    # ...and the line the seat was READ from is not itself painted.
    greeting = _WELCOME_TEMPLATE.format(name="Iris")
    assert greeting in _LAW_ABIDING_SEAT_GAME
    assert not [
        span
        for span in spans
        if greeting in span[0] and span[1] != eval_ledger.KIND_PLAIN
    ], "the moderator's greeting was styled — he is not a player speaking"


@pytest.mark.parametrize(
    ("route", "seat", "role", "speaker_kind", "speech_kind"),
    [
        pytest.param(
            "marker",
            "Iris",
            "Law-abiding Citizen",
            eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN,
            eval_ledger.KIND_SPEECH_LAW_ABIDING_HUMAN,
            id="marker-law-abiding",
        ),
        pytest.param(
            "greeting",
            "Vera",
            "Mafia",
            eval_ledger.KIND_SPEAKER_MAFIA_HUMAN,
            eval_ledger.KIND_SPEECH_MAFIA_HUMAN,
            id="greeting-mafia",
        ),
        pytest.param(
            "marker",
            "Vera",
            "Mafioso",
            eval_ledger.KIND_SPEAKER_HUMAN,
            eval_ledger.KIND_SPEECH_HUMAN,
            id="marker-side-unknown",
        ),
        pytest.param(
            "greeting",
            "Vera",
            "Mafioso",
            eval_ledger.KIND_SPEAKER_HUMAN,
            eval_ledger.KIND_SPEECH_HUMAN,
            id="greeting-side-unknown",
        ),
    ],
)
def test_both_routes_reach_every_side_a_seat_can_have(
    route: str, seat: str, role: str, speaker_kind: str, speech_kind: str
) -> None:
    """The full 2x3 grid: each route x each side a seat can be on.

    The two tests above pin one cell of this grid each, absolutely and with their
    non-seat side-mates. This sweeps the remaining four so no cell is reachable
    only through the other route — which is precisely how a precedence bug hides,
    since the marker route and the greeting route land on the *same* six kinds and
    a tokenizer that only ever consulted the greeting would still colour four of
    the six correctly.

    Near-miss 9 lives in the last two rows: `role="Mafioso"` is a label the writer
    never emits, so the seat is **marked but sideless**. Side and seat are
    independent facts and this is the shape that proves it — a tokenizer that
    folded the two axes together would have to invent a side here or drop the
    seat.
    """
    entry = _cast_entry(seat, role, marker="true" if route == "marker" else None)
    source = (
        _cast(entry, _cast_entry("Dot", "Mafia"))
        + (_preamble(seat) if route == "greeting" else "")
        + f"{seat}: I was asleep."
    )
    spans = _spoken_spans(source)

    assert spans[-2:] == [(f"{seat}:", speaker_kind), (" I was asleep.", speech_kind)]
    assert _bolded_names(spans) == {seat}


# ---------------------------------------------------------------------------
# Precedence: the marker wins
# ---------------------------------------------------------------------------


def test_the_marker_wins_when_both_routes_are_present_and_disagree() -> None:
    """Near-miss 2b, and the assertion tech-spec §3 asks for by name.

    "Assert both paths, and assert that the marker wins when both are present."
    The greeting is a **heuristic behind an explicit marker** — it depends on the
    moderator's wording and it is the fallback for files written before the
    marker existed — so a file that states its seat outright and also carries the
    old greeting must be read as it states, not as it implies.

    This is the shape with **zero real-data coverage of any kind**: no committed
    transcript carries a marker, so no committed transcript can carry both. It is
    also the one a mutation test can flip invisibly — swapping the precedence
    leaves every one of the 298 files tokenizing identically, because their
    marker route sees nothing at all.

    Pinned in both directions: Vera IS bolded and Iris is NOT, because "the
    marker wins" and "both are bolded" produce the same answer for Vera alone.
    """
    source = (
        _cast(
            _cast_entry("Vera", "Mafia", marker="true"),
            _cast_entry("Iris", "Law-abiding Citizen"),
        )
        + _preamble("Iris")
        + "Vera: I was asleep.\nIris: So you say."
    )
    spans = _spoken_spans(source)

    assert spans == [
        ("Vera:", "speaker-mafia-human"),
        (" I was asleep.", "speech-mafia-human"),
        ("\n", "plain"),
        # The GREETING's candidate, left in her side's plain colour.
        ("Iris:", "speaker-law-abiding"),
        (" So you say.", "speech-law-abiding"),
    ]
    assert _bolded_names(spans) == {"Vera"}
    # The premise: the greeting really is in the file and really does name the
    # other player, so this is precedence and not an absent fallback.
    assert _WELCOME_TEMPLATE.format(name="Iris") in source


def test_the_marker_and_the_greeting_agreeing_seat_the_same_player() -> None:
    """Near-miss 2a: both routes present and in agreement — one seat, not two.

    The complement of the disagreement case, and not redundant with it: a
    tokenizer that ran both routes and UNIONED their answers would pass the
    disagreement test's "Vera is bolded" half and fail only its "Iris is not"
    half. Here the union and the precedence give the same seat, so what is being
    checked is that agreement does not double-count — the file names one seat and
    the reader finds one.
    """
    source = (
        _cast(
            _cast_entry("Vera", "Mafia", marker="true"),
            _cast_entry("Iris", "Law-abiding Citizen"),
        )
        + _preamble("Vera")
        + "Vera: I was asleep.\nIris: So you say."
    )
    spans = _spoken_spans(source)

    assert spans == [
        ("Vera:", "speaker-mafia-human"),
        (" I was asleep.", "speech-mafia-human"),
        ("\n", "plain"),
        ("Iris:", "speaker-law-abiding"),
        (" So you say.", "speech-law-abiding"),
    ]
    assert _bolded_names(spans) == {"Vera"}


# ---------------------------------------------------------------------------
# Near-misses: every way a seat must NOT be found
# ---------------------------------------------------------------------------


def test_a_game_that_names_no_seat_bolds_nobody() -> None:
    """Near-miss 3: neither route available — not bolded, never guessed.

    functional-spec §2 and tech-spec §2 D both end on this: "a seat that cannot
    be identified (neither marker nor inferable name) simply is not bolded —
    never guessed." The positive tests above cannot see a tokenizer that bolds
    the first speaker, or the first cast entry, or everyone; this one can.

    Deliberately NOT keyed off `(no persona recorded)`, which is present in this
    fixture on both entries exactly as it is in all 298 committed files. That
    string identifies the same seat reliably and is still not used, because it is
    a **side effect** — only AI players get personas — and it encodes "no persona"
    rather than "the person". A tokenizer that reached for it would bold two
    players here.
    """
    source = _TWO_SIDED_CAST + "Vera: I was asleep.\nIris: So you say."
    spans = _spoken_spans(source)

    assert spans == [
        ("Vera:", "speaker-mafia"),
        (" I was asleep.", "speech-mafia"),
        ("\n", "plain"),
        ("Iris:", "speaker-law-abiding"),
        (" So you say.", "speech-law-abiding"),
    ]
    assert _seat_kinds(spans) == set()
    # The premise, and the trap: the persona side effect IS present and IS
    # ignored.
    assert source.count("(no persona recorded)") == 2
    assert eval_ledger._human_seat(source.split("\n")) is None


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("false", id="false"),
        pytest.param("", id="empty"),
        pytest.param("TRUE", id="uppercase"),
        pytest.param("True", id="capitalised"),
        pytest.param("1", id="one"),
        pytest.param("yes", id="yes"),
    ],
)
def test_only_the_literal_true_marks_the_seat(value: str) -> None:
    """Near-miss 4: a whitelist of ONE literal, never a boolean coercion.

    The same never-guess posture as `_ROLE_SIDES` and `_MARKER_TAGS`, applied to
    the one attribute whose whole job is to be read as a flag. The writer emits
    the marker only on the human's entry and only as `human="true"`, so anything
    else in that slot is a shape the writer did not produce — a hand edit, a
    future tri-state, a different tool's output — and the honest reading of it is
    "this file has not told me". `human="false"` is the case that would bite
    hardest under coercion: a truthiness test on a non-empty string bolds the
    seat the file has just denied.

    Both halves of the behaviour are pinned, because they differ. With nothing
    else in the file the seat is simply unknown; with a greeting also present the
    greeting decides — an unrecognised value is not a contradictory *statement*,
    it is silence, so it does not block the documented fallback the way two
    contradicting real markers do (see the test below).
    """
    entry = _cast_entry("Vera", "Mafia", marker=value)
    dialogue = "Vera: I was asleep.\nIris: So you say."

    alone = _cast(entry, _cast_entry("Iris", "Law-abiding Citizen")) + dialogue
    assert _seat_kinds(_spoken_spans(alone)) == set(), (
        f'human="{value}" marked a seat — only the literal "true" may'
    )

    with_greeting = (
        _cast(entry, _cast_entry("Iris", "Law-abiding Citizen"))
        + _preamble("Iris")
        + dialogue
    )
    assert _bolded_names(_spoken_spans(with_greeting)) == {"Iris"}, (
        f'human="{value}" is not a statement, so the greeting still decides'
    )


@pytest.mark.parametrize(
    "placement",
    [
        pytest.param("day", id="inside-a-day-round"),
        pytest.param("endgame", id="inside-the-endgame-reveal"),
        pytest.param("top-level", id="between-the-sections"),
    ],
)
def test_a_marked_cast_entry_outside_the_setup_cannot_seat_anybody(
    placement: str,
) -> None:
    """Near-miss 5: the marker scan is scoped to `<setup>`, exactly as the cast scan is.

    Every word of a transcript outside the cast list and the preamble is
    model-generated. A player who types a `<player … human="true">` tag into a Day
    speech, or a persona reveal that echoes one back inside `<endgame>`, must not
    be able to move the reviewer's seat — the same guard `_cast_side_map` carries
    for the same reason, and one with **zero real-data coverage** (no committed
    file contains a `<player>` tag outside `<setup>` at all).

    Dot is the intruder in every placement and the real seat is Vera, named by the
    greeting, so the failure is legible in both directions: an unscoped scan would
    see two markers, resolve to no seat at all, and silently un-bold Vera.
    """
    intruder = _cast_entry("Dot", "Mafia", marker="true").rstrip("\n")
    blocks = {
        "day": f"<day>\n{intruder}\nVera: I was asleep.\n</day>",
        "endgame": f"<endgame>\nVera: I was asleep.\n{intruder}\n</endgame>",
        "top-level": f"{intruder}\nVera: I was asleep.",
    }
    source = (
        _cast(_cast_entry("Vera", "Mafia"), _cast_entry("Dot", "Mafia"))
        + _preamble("Vera")
        + blocks[placement]
    )
    spans = _spans(source)

    assert _bolded_names(spans) == {"Vera"}, (
        "a `<player … human=\"true\">` tag outside `<setup>` reached the seat scan"
    )
    assert eval_ledger._human_seat(source.split("\n")) == "Vera"


@pytest.mark.parametrize(
    "placement",
    [
        pytest.param("day", id="inside-a-day-round"),
        pytest.param("top-level", id="between-the-sections"),
    ],
)
def test_a_greeting_outside_the_preamble_does_not_seat_anybody(
    placement: str,
) -> None:
    """Near-miss 6: the greeting is read inside `<preamble>` and nowhere else.

    The inference's counterpart to the scoping test above, and the more exposed of
    the two: the greeting is a *sentence*, so a player who repeats the moderator's
    opening words back at the table produces the exact line the reader keys on.
    Scoping is what stops a Day speech from re-seating the reviewer, and there is
    no real-data coverage of it either — all 298 greetings are inside
    `<preamble>`, which is precisely why an unscoped rule and a scoped one look
    identical on the corpus.
    """
    echo = _WELCOME_TEMPLATE.format(name="Dot")
    blocks = {
        "day": f"<day>\n{echo}\nVera: I was asleep.\n</day>",
        "top-level": f"{echo}\nVera: I was asleep.",
    }
    source = (
        _cast(_cast_entry("Vera", "Mafia"), _cast_entry("Dot", "Mafia"))
        + blocks[placement]
    )
    spans = _spans(source)

    assert eval_ledger._human_seat(source.split("\n")) is None
    assert _seat_kinds(spans) == set(), (
        "a greeting typed outside `<preamble>` seated somebody"
    )
    # ...and the echoed line is still ordinary unstyled text, not skeleton. The
    # span it lands in may be larger than the line itself (coalescing merges it
    # with the separators around it), so this asks which span CONTAINS it.
    assert [kind for text, kind in spans if echo in text] == [
        eval_ledger.KIND_PLAIN
    ], f"the echoed greeting is not a single plain span: {spans!r}"


def test_two_contradicting_markers_yield_no_seat_and_no_fall_back_to_the_greeting() -> None:
    """Near-miss 7: a file that states its seat incoherently has still STATED it.

    The precedence rule is "did the marker route see anything", not "did it
    produce an answer", and the difference is only visible here. Two
    `human="true"` entries are a contradiction the file cannot resolve; falling
    through to the greeting would be **this module resolving a contradiction the
    file could not**, which is the one thing it never does anywhere else — a name
    the cast list gives two sides is dropped rather than resolved first-wins, and
    this is the same rule on the other axis.

    Two assertions, and the second is the whole point: no seat AND the greeting's
    candidate is not bolded either. A tokenizer that fell through would satisfy
    "Vera and Dot are not bolded" perfectly.
    """
    source = (
        _cast(
            _cast_entry("Vera", "Mafia", marker="true"),
            _cast_entry("Dot", "Mafia", marker="true"),
            _cast_entry("Iris", "Law-abiding Citizen"),
        )
        + _preamble("Iris")
        + "Vera: I was asleep.\nDot: So was I.\nIris: Neither of you was."
    )
    spans = _spoken_spans(source)

    assert eval_ledger._human_seat(source.split("\n")) is None
    assert _seat_kinds(spans) == set()
    assert spans == [
        ("Vera:", "speaker-mafia"),
        (" I was asleep.", "speech-mafia"),
        ("\n", "plain"),
        ("Dot:", "speaker-mafia"),
        (" So was I.", "speech-mafia"),
        ("\n", "plain"),
        # The greeting named her, and the contradiction upstream silences it.
        ("Iris:", "speaker-law-abiding"),
        (" Neither of you was.", "speech-law-abiding"),
    ]


def test_two_contradicting_greetings_yield_no_seat_either() -> None:
    """The same rule on the inference route: two greetings name nobody.

    Not reachable in any committed game — the moderator greets once, in all 298 —
    but the rule is one `len(candidates) == 1` shared by both routes, and a
    first-wins mutation of it would be invisible on the corpus while quietly
    seating whoever a future format happened to greet first.
    """
    source = (
        _cast(
            _cast_entry("Vera", "Mafia"),
            _cast_entry("Iris", "Law-abiding Citizen"),
        )
        + _preamble("Vera", "Iris")
        + "Vera: I was asleep.\nIris: So you say."
    )

    assert eval_ledger._human_seat(source.split("\n")) is None
    assert _seat_kinds(_spoken_spans(source)) == set()


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(
            '<player name="" role="Mafia" human="true">(no persona recorded)</player>\n',
            id="empty-name",
        ),
        pytest.param(
            '<player role="Mafia" human="true">(no persona recorded)</player>\n',
            id="no-name-attribute",
        ),
        pytest.param(
            '<vote initiator="Vera" target="Iris" human="true">\n</vote>\n',
            id="marker-on-another-tag",
        ),
    ],
)
def test_a_marked_entry_with_no_usable_name_marks_nothing(entry: str) -> None:
    """Near-miss 8: a flag with nobody attached is not a seat.

    A seat is a NAME — the reader matches it against the `Name:` prefix of every
    spoken line — so an entry carrying the flag and no usable name has marked
    nothing there is anything to do with. The third case is the same hole from
    the other side: the flag is meaningful on a cast entry and meaningless
    anywhere else, so a `<vote>` that happens to carry it is not a cast entry
    growing a seat.

    Distinguished from the contradiction case above deliberately, and the two
    tests are the pair that pins the distinction: an unusable entry is **silence**
    (the greeting still decides), two usable-but-conflicting entries are an
    incoherent **statement** (nothing decides). Both readings are documented in
    `_human_seat`; only asserting them keeps them apart.
    """
    dialogue = "Vera: I was asleep.\nIris: So you say."
    body = _cast(entry, _cast_entry("Iris", "Law-abiding Citizen"))

    assert eval_ledger._human_seat((body + dialogue).split("\n")) is None
    assert _seat_kinds(_spoken_spans(body + dialogue)) == set()

    # Silence, not a statement: the greeting is still consulted.
    with_greeting = body + _preamble("Iris") + dialogue
    assert _bolded_names(_spoken_spans(with_greeting)) == {"Iris"}


def test_a_welcomed_seat_that_never_speaks_bolds_nothing_and_does_not_raise() -> None:
    """The commonest real shape this section covers: a seat killed on Night 1.

    Measured over the corpus, **74 of the 298** committed games are exactly this —
    the greeting names a seat that then has no `Name: ` line anywhere, because it
    died before it ever spoke. So "every transcript emits a seat kind" is a FALSE
    statement about the corpus and a tempting one to write; the true form is
    "every transcript whose welcomed seat speaks emits one", which the corpus
    sweep below asserts over all 298.

    Here it is on an input small enough to read: the seat is identified, nothing
    is bolded, and nothing raises.
    """
    source = (
        _cast(
            _cast_entry("Vera", "Mafia"),
            _cast_entry("Iris", "Law-abiding Citizen"),
        )
        + _preamble("Dot")
        + "<night>\n<kill>Dot — Law-abiding Citizen</kill>\n</night>\n"
        + "Vera: I was asleep.\nIris: So you say."
    )
    spans = _spoken_spans(source)

    assert eval_ledger._human_seat(source.split("\n")) == "Dot"
    assert _seat_kinds(spans) == set()
    assert spans[-2:] == [("Iris:", "speaker-law-abiding"), (" So you say.", "speech-law-abiding")]


def test_the_writers_vocabulary_still_outranks_the_seat() -> None:
    """A seat named `Moderator` does not turn the moderator's lines into speech.

    Slice 3's exclusion set sits in the speaker branch, ABOVE everything the seat
    axis does — the seat can only re-kind a line the tokenizer already decided is
    somebody speaking. A file whose greeting welcomes a player called `Moderator`
    is a pathological input rather than a realistic one, and the point is exactly
    that: two whitelists that both key on a line's leading name must compose in a
    fixed order, and only a test says which.

    The mirror of `test_a_player_named_moderator_does_not_make_the_moderators_lines_speech`,
    which pins the same precedence for the side axis.
    """
    source = (
        _cast(_cast_entry("Moderator", "Mafia"), _cast_entry("Iris", "Law-abiding Citizen"))
        + _preamble("Moderator")
        + "Moderator: Night falls.\nIris: So you say."
    )
    spans = _spoken_spans(source)

    assert eval_ledger._human_seat(source.split("\n")) == "Moderator"
    assert spans == [
        # His line stays `plain` and takes the trailing separator with it, since
        # coalescing merges adjacent runs of one kind.
        ("Moderator: Night falls.\n", "plain"),
        ("Iris:", "speaker-law-abiding"),
        (" So you say.", "speech-law-abiding"),
    ]
    assert _seat_kinds(spans) == set()


# ---------------------------------------------------------------------------
# The marker is punctuation, not a detail
# ---------------------------------------------------------------------------


def test_the_seat_marker_is_not_an_attr_span_of_its_own() -> None:
    """Near-miss 10: ratified in Slice 2, and pinned here so a later slice cannot "fix" it.

    `human` is deliberately absent from `_ATTR_NAMES`. The five whitelisted
    attributes are lifted out of the punctuation because a reviewer reads them as
    **specifics** — a person's name, a role — and a machine flag is not one: it
    says nothing a reviewer wants picked out, and the thing it does say is
    already visible as the bold on that seat's every line.

    So the cast entry splits five ways with the marker riding along inside the
    tag's TRAILING marker span, exactly as the closing `">` did before it. That is
    a boundary a future slice could plausibly "correct" by adding `human` to the
    whitelist, at which point the achromatic `attr` treatment would start
    painting `true` as though it were a name.
    """
    source = _cast(_cast_entry("Vera", "Mafia", marker="true")) + "Vera: I was asleep."
    spans = _spans(source)

    assert spans[2:7] == [
        ('<player name="', "marker"),
        ("Vera", "attr"),
        ('" role="', "marker"),
        ("Mafia", "attr-mafia"),
        # The marker rides inside the tag's punctuation, not beside it.
        (f'" {_HUMAN_MARKER}>', "marker"),
    ]
    assert "human" not in eval_ledger._ATTR_NAMES
    # ...and no span anywhere is the flag's value on its own.
    assert "true" not in [text for text, kind in spans if kind in _ATTR_KINDS]
    # The premise: the seat really was read out of that tag.
    assert _bolded_names(spans) == {"Vera"}


def test_the_seats_own_attributes_take_a_side_but_never_a_seat() -> None:
    """Ratified in Slice 5: `attr` gains **no** seated form, and here is why.

    The seat's cast entry and its `<thought player="…">` owner name are already
    `attr-mafia` / `attr-law-abiding`, and Slice 4's styling task kept `attr`'s
    bold on both — so a "bold within the side" treatment there would be
    invisible, adding a kind that renders identically to the one it replaced.
    functional-spec §2 scopes the requirement to that seat's **lines** in any
    case, and bold on a player's line is unspent precisely so it can mean this
    one thing.

    Four spans, four different rules meeting on one player:

    * the seat's `name=` is achromatic `attr` — a name carries no side;
    * the seat's `role=` is `attr-mafia` — the side is written right there;
    * the seat's thought OWNER is `attr-mafia` — a map lookup, side only;
    * the seat's thought BODY is `thought` — never side-tinted and never seated,
      because a private reflection is not an act of allegiance.
    """
    source = (
        _cast(_cast_entry("Vera", "Mafia", marker="true"))
        + '<thought player="Vera">They suspect me.</thought>\n'
        + "Vera: I was asleep."
    )
    spans = _spans(source)
    by_text = dict(spans)

    assert by_text["Vera"] == eval_ledger.KIND_ATTR_MAFIA, (
        "the `<thought player=…>` owner name takes the side and only the side"
    )
    assert by_text["Mafia"] == eval_ledger.KIND_ATTR_MAFIA
    assert by_text["They suspect me."] == eval_ledger.KIND_THOUGHT
    # The cast entry's own `name=` value, which coalescing keeps separate from
    # the thought owner only by their differing kinds — so read it positionally.
    assert spans[3] == ("Vera", eval_ledger.KIND_ATTR)
    # No attr span anywhere carries a seat kind — there is no such kind.
    assert not _HUMAN_KINDS & set(_ATTR_KINDS)
    assert _seat_kinds(spans) == {
        eval_ledger.KIND_SPEAKER_MAFIA_HUMAN,
        eval_ledger.KIND_SPEECH_MAFIA_HUMAN,
    }


# ---------------------------------------------------------------------------
# The seat inside the rest of the format
# ---------------------------------------------------------------------------


def test_a_ballot_cast_by_the_seat_takes_the_seats_kind() -> None:
    """Near-miss 11: inside `<vote>`, a ballot is speech — and the seat's is bold.

    Spec 022 strips the `Moderator:` voice off ballots precisely so `Vera: Yes`
    reads as the player's own word, and Slice 3 ratified that a ballot is
    `speaker` + `speech` rather than a kind of its own. The seat axis composes
    with that decision rather than being scoped around it: the vote block then
    shows at a glance both which sides voted which way AND which of those votes
    was the reviewer's own, which is the most useful thing this view can say
    about a vote.

    The stateless per-line tokenizer cannot tell it is inside `<vote>` at all,
    which is why this holds — but "it holds because the tokenizer cannot see the
    difference" is an implementation accident until a test makes it a promise.
    """
    source = (
        _cast(
            _cast_entry("Vera", "Mafia", marker="true"),
            _cast_entry("Iris", "Law-abiding Citizen"),
        )
        + '<vote initiator="Iris" target="Vera">\n'
        + "Vera: No\n"
        + "Iris: Yes\n"
        + "tally: 1 Yes, 1 No\n"
        + "</vote>"
    )
    spans = _spoken_spans(source)

    assert spans == [
        # The vote marker's own names stay achromatic: a vote says who called it
        # and against whom, and the ballots below it already answer the side
        # question — including, now, whose ballot was the reviewer's own.
        ('<vote initiator="', "marker"),
        ("Iris", "attr"),
        ('" target="', "marker"),
        ("Vera", "attr"),
        ('">', "marker"),
        ("\n", "plain"),
        ("Vera:", "speaker-mafia-human"),
        (" No", "speech-mafia-human"),
        ("\n", "plain"),
        ("Iris:", "speaker-law-abiding"),
        (" Yes", "speech-law-abiding"),
        # `tally:` is writer vocabulary, not a player — the exclusion set holds
        # inside a vote block as much as outside it.
        ("\ntally: 1 Yes, 1 No\n", "plain"),
        ("</vote>", "marker"),
    ]


def test_the_seat_takes_the_same_kind_in_every_round_they_speak() -> None:
    """"That player's colour is the same every time" (functional-spec §2), for the seat.

    The seat is read once per file, so it cannot drift between rounds — but so was
    the cast map, and that claim is only true until somebody rebuilds one of them
    per section. The side axis has this test already; the seat axis needs its own,
    because a rebuild would break them independently.

    Pinned absolutely as well as relatively, for spec 037's mutation finding: "the
    second round matches the first" holds when both are wrong in the same way.
    """
    source = _cast(
        _cast_entry("Vera", "Mafia", marker="true"),
        _cast_entry("Iris", "Law-abiding Citizen"),
    ) + (
        "<day>\n"
        "<round>\n"
        "Round 1.\n"
        "Vera: I was asleep.\n"
        "Iris: So you say.\n"
        "</round>\n"
        "<round>\n"
        "Round 2.\n"
        "Iris: I still say it.\n"
        "Vera: And I still was.\n"
        "</round>\n"
        "</day>"
    )
    spans = _spoken_spans(source)
    by_speaker: dict[str, list[str]] = {}
    for index, (text, kind) in enumerate(spans):
        if kind in _SPEAKER_KINDS:
            by_speaker.setdefault(text, []).append(kind)
            assert spans[index + 1][1] == _SPEECH_KINDS[_SPEAKER_KINDS.index(kind)]

    assert {name: set(kinds) for name, kinds in by_speaker.items()} == {
        "Vera:": {eval_ledger.KIND_SPEAKER_MAFIA_HUMAN},
        "Iris:": {eval_ledger.KIND_SPEAKER_LAW_ABIDING},
    }
    assert [len(kinds) for kinds in by_speaker.values()] == [2, 2]


def test_an_indented_pre_022_style_preamble_still_names_the_seat() -> None:
    """The 30 pre-spec-022 games indent their preamble's content, and 20 seat a player.

    Their `<preamble>` tag is flush-left but every line inside it carries two
    spaces, exactly as their cast list does — which is why `_human_seat` compares
    the *stripped* line and why that is not an incidental `.strip()`. Those same
    30 files yield an empty cast map, so they are also the corpus's only source of
    the `speaker-human` / `speech-human` pair: seat known, side unknown, both read
    from the same file.

    Written synthetically as well as swept, because the sweep would report a
    dropped indent as "20 files lost their seat" rather than as this rule.
    """
    source = (
        "<setup>\n"
        "  Vera — Mafia\n"
        "    (no persona recorded)\n"
        "</setup>\n"
        "<preamble>\n"
        f"  {_WELCOME_TEMPLATE.format(name='Vera')}\n"
        "</preamble>\n"
        "Vera: I was asleep."
    )
    spans = _spans(source)

    assert eval_ledger._human_seat(source.split("\n")) == "Vera"
    # No cast map — the old form is not parsed, by the author's explicit decision
    # — so the seat is known and the side is not. Both facts, from one file.
    assert eval_ledger._cast_side_map(source.split("\n")) == {}
    assert spans[-2:] == [
        ("Vera:", eval_ledger.KIND_SPEAKER_HUMAN),
        (" I was asleep.", eval_ledger.KIND_SPEECH_HUMAN),
    ]
    assert not set(spans) & _SIDE_KINDS


def test_a_seat_name_carrying_its_own_full_stop_survives_the_greeting() -> None:
    """`Welcome, Dr. Aziz.` seats `Dr. Aziz`, not `Dr`.

    The greeting's name group is greedy up to the line's final full stop, which is
    the only reading that survives a name containing one. Names are
    model-generated, so this is a shape the next eval run could produce and no
    committed one has — and a non-greedy `.+?` would look identical on all 298
    files while seating a person who never speaks.
    """
    greedy = _cast(_cast_entry("Dr. Aziz", "Mafia")) + _preamble("Dr. Aziz")
    assert eval_ledger._human_seat(greedy.split("\n")) == "Dr. Aziz", (
        "the greeting's name group must be greedy to the line's FINAL full stop; "
        "a non-greedy one seats `Dr` and looks identical on all 298 committed files"
    )

    # ...and the bold that follows from it, on a name a `Name:` prefix can carry.
    # `Dr. Aziz` cannot be one — `_SPEAKER_RE` allows no internal whitespace, a
    # deliberate Slice-3 miss — so the seat that speaks here is spelled without
    # the space while keeping the internal full stop that makes the point.
    source = (
        _cast(_cast_entry("St.Clair", "Mafia"))
        + _preamble("St.Clair")
        + "St.Clair: I was asleep."
    )
    assert eval_ledger._human_seat(source.split("\n")) == "St.Clair"
    assert _spoken_spans(source) == [
        ("St.Clair:", eval_ledger.KIND_SPEAKER_MAFIA_HUMAN),
        (" I was asleep.", eval_ledger.KIND_SPEECH_MAFIA_HUMAN),
    ]


# ---------------------------------------------------------------------------
# The writer and the reader agree
# ---------------------------------------------------------------------------


def test_the_writer_and_the_reader_agree_on_the_marker_literal() -> None:
    """Two modules, two independent literals, one attribute — tied together here.

    `eval_transcript._HUMAN_ATTR` writes the marker; `eval_ledger._HUMAN_ATTR_RE`
    and `_HUMAN_ATTR_TRUE` read it back. Nothing in either module refers to the
    other, which is correct (the pure reader must not import the writer) and is
    exactly why a drift between them would be silent: transcripts would carry a
    marker nothing looks for, every existing test would pass, and the seat would
    quietly fall back to the greeting forever.

    The same shape as `test_the_cast_lists_role_text_matches_the_dialogue_kinds`,
    which ties the two independent side paths together for the same reason.
    """
    from graphia.tools import eval_transcript

    assert eval_transcript._HUMAN_ATTR == _HUMAN_MARKER
    match = eval_ledger._HUMAN_ATTR_RE.search(f" {eval_transcript._HUMAN_ATTR}")
    assert match is not None, (
        f"the reader's pattern does not match the writer's "
        f"{eval_transcript._HUMAN_ATTR!r}"
    )
    assert match.group("value") == eval_ledger._HUMAN_ATTR_TRUE
    # The lookbehind really does its job: a longer attribute ending in `human`
    # is not the flag.
    assert eval_ledger._HUMAN_ATTR_RE.search(' nonhuman="true"') is None


def test_a_transcript_the_writer_produced_bolds_the_seat_it_marked() -> None:
    """End to end, writer to reader, with no literal shared between the halves.

    Everything else in this section feeds the tokenizer a hand-written tag. This
    feeds it a document `render_transcript` actually produced from a roster whose
    seat carries `PlayerState.is_human=True` — the only test in the suite where
    the marker's whole round trip runs, and the closest thing to real data the
    marker route can have, since `human=` appears in **0 of the 298** committed
    files and will not appear until the next measured eval run is committed.

    Both sides are covered, because the seat's kind depends on its role and the
    two roles take different paths through `_ROLE_SIDES`.
    """
    from graphia.state import PlayerState
    from graphia.tools.eval_transcript import render_transcript

    for role, speaker_kind in (
        ("mafia", eval_ledger.KIND_SPEAKER_MAFIA_HUMAN),
        ("law_abiding", eval_ledger.KIND_SPEAKER_LAW_ABIDING_HUMAN),
    ):
        players = {
            "p-seat": PlayerState(
                id="p-seat",
                name="Avery",
                role=role,
                is_human=True,
                is_alive=True,
                persona=None,
            ),
            "p-other": PlayerState(
                id="p-other",
                name="Bo",
                role=role,
                is_human=False,
                is_alive=True,
                persona=None,
            ),
        }
        doc = render_transcript([], players, game_index=1, run_meta=None)
        # The writer really emitted the marker, on one entry only.
        assert doc.count(_HUMAN_MARKER) == 1, doc

        spans = _spans(doc + "\n<day>\nAvery: I was asleep.\nBo: So was I.\n</day>")
        assert _bolded_names(spans) == {"Avery"}, (
            f"the writer's own {role} transcript did not bold the seat it marked"
        )
        by_text = dict(spans)
        assert by_text["Avery:"] == speaker_kind
        # ...and the side-mate the writer did NOT mark is not bolded.
        assert by_text["Bo:"] == _UNSEATED_KIND[speaker_kind]


# ---------------------------------------------------------------------------
# The seat across the whole committed corpus
# ---------------------------------------------------------------------------


@_requires_corpus
def test_every_committed_game_names_a_seat_and_bolds_it_only_where_it_speaks() -> None:
    """The seat's real-data story, stated as the two relations that are actually true.

    Measured over all 298 committed games, and written as relations rather than
    counts because the corpus grows with every eval run:

    * **every committed game names its seat.** All 298 carry the moderator's
      greeting exactly once inside `<preamble>`, so the inference resolves a seat
      for every one of them — which is what lets tech-spec §2 D call the reader's
      heuristic "a documented fallback" rather than a partial one. A file that
      stopped resolving would mean the greeting's wording had drifted.
    * **a game emits seat kinds exactly when its seat speaks.** The tempting
      assertion, "every game bolds something", is FALSE: in **74** of the 298 the
      welcomed seat has no `Name: ` line anywhere, because it was killed on
      Night 1 and never spoke. Asserted as an ``iff`` per file, so neither
      direction can rot — a tokenizer that bolded nothing and one that bolded a
      seat with nothing to bold both fail.
    * **and the bold never lands on anybody else.** Every seat-kinded speaker span
      in the corpus is the seat's own name, checked span by span across all
      22,508 utterances. This is the assertion that fails if the seat is matched
      loosely — by prefix, case-insensitively, or against the wrong name.

    The premise counts are lower bounds and non-emptiness, never today's figures.
    """
    seatless: list[str] = []
    mismatched: list[str] = []
    misattributed: list[str] = []
    emitting = 0
    silent = 0
    seat_kinds_seen: Counter[str] = Counter()

    for path in _TRANSCRIPTS:
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        seat = eval_ledger._human_seat(lines)
        if seat is None:
            seatless.append(_rel(path))
            continue
        spans = _spans(text)
        kinds = _seat_kinds(spans)
        speaks = any(line.lstrip().startswith(f"{seat}: ") for line in lines)
        if bool(kinds) is not speaks:
            mismatched.append(
                f"{_rel(path)}: seat {seat!r} speaks={speaks} but seat kinds={sorted(kinds)}"
            )
        if kinds:
            emitting += 1
            seat_kinds_seen.update(kinds)
        else:
            silent += 1
        misattributed.extend(
            f"{_rel(path)}: {text_!r} is {kind} but the seat is {seat!r}"
            for text_, kind in spans
            if kind in _HUMAN_KINDS
            and kind in _SPEAKER_KINDS
            and text_ != f"{seat}:"
        )

    assert not seatless, (
        "every committed transcript carries the moderator's greeting exactly once "
        f"inside `<preamble>`, so every one resolves a seat: {seatless[:_MAX_REPORTED_FILES]}"
    )
    assert not mismatched, (
        "a game emits seat kinds exactly when its welcomed seat has a `Name: ` "
        f"line: {mismatched[:_MAX_REPORTED_FILES]}"
    )
    assert not misattributed, (
        "a seat kind landed on somebody other than the seat — the bold means "
        f"'this was yours' and it is now saying it to the wrong player: "
        f"{misattributed[:_MAX_REPORTED_FILES]}"
    )
    # The premises: both populations really exist, so neither direction of the
    # `iff` is vacuous, and all six seat kinds are reachable on real data.
    assert emitting > 0, "no committed game bolds a seat — the iff is vacuous"
    assert silent > 0, (
        "no committed game has a seat that never spoke — the 74 Night-1 deaths "
        "are what makes 'every game bolds something' the wrong assertion"
    )
    assert emitting + silent == len(_TRANSCRIPTS)
    assert set(seat_kinds_seen) == _HUMAN_KINDS, (
        f"seat kinds reached on real data: {sorted(seat_kinds_seen)}; expected all "
        f"of {sorted(_HUMAN_KINDS)}"
    )


@_requires_corpus
def test_the_seat_axis_moves_kinds_and_never_a_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The side-axis conservation sweep's twin, on the axis Slice 5 added.

    `tasks.md` records this slice's measurement as absolute totals again — "193,740,
    identical to Slices 3 and 4; `speaker` 1,670 + `speaker-human` 183 = 1,853" —
    and they are again the right finding and the wrong assertion: the corpus grows
    with every committed eval run, so a pinned total is a test that fails on a
    green tree. The relation behind them is pinned instead.

    Tokenize every committed game twice — once normally, once with `_human_seat`
    stubbed to find nothing — and require **the same spans of text in the same
    order**, differing only in which kind each carries and only by the seat split.
    That is "only kinds moved, no boundary did" stated so it survives the corpus
    doubling, and it is stronger than the totals were: a boundary that moved while
    preserving the counts still fails.

    It doubles as the no-seat degradation path swept over all 298 files rather
    than the zero that exercise it naturally — every committed game names a seat,
    so *nothing* in the corpus produces the unseated reading on its own. Stubbing
    is the only way to sweep it, exactly as it is for the empty cast map.

    Deliberately a SECOND sweep rather than a widening of the side-axis one: run
    together, a tokenizer that had collapsed the two axes into one would pass both
    at once. Run separately, each pins its own axis with the other held fixed,
    which is what "side and seat are independent facts" actually claims.
    """
    real_human_seat = eval_ledger._human_seat
    moved: Counter[str] = Counter()
    checked = 0

    for path in _TRANSCRIPTS:
        text = path.read_text(encoding="utf-8")
        seated = _spans(text)
        monkeypatch.setattr(eval_ledger, "_human_seat", lambda lines: None)
        unseated = _spans(text)
        monkeypatch.setattr(eval_ledger, "_human_seat", real_human_seat)
        checked += 1

        assert [span_text for span_text, _ in seated] == [
            span_text for span_text, _ in unseated
        ], (
            f"{_rel(path)}: the seat moved a span BOUNDARY, not just a kind — "
            "Slice 5 re-kinds spans and must never re-split a line"
        )
        for (span_text, kind), (_, unseated_kind) in zip(
            seated, unseated, strict=True
        ):
            if kind == unseated_kind:
                continue
            assert _SEATED_KIND.get(unseated_kind) == kind, (
                f"{_rel(path)}: {span_text[:40]!r} is {kind} with a seat and "
                f"{unseated_kind} without — the only difference a seat may make "
                "is the seat split"
            )
            moved[kind] += 1

    assert checked == len(_TRANSCRIPTS)
    # Every kind the seat moves, and no other. "The seat never touches a kind
    # with no seated form" needs no separate assertion: the loop above already
    # rejects any difference that is not a `_SEATED_KIND` step, so a moved
    # `attr`, `marker`, `thought` or `recap` span fails there, and a seated form
    # invented for one of them fails this equality.
    assert set(moved) == _HUMAN_KINDS, (
        f"kinds actually moved by the seat: {sorted(moved)}; expected all of "
        f"{sorted(_HUMAN_KINDS)}"
    )


# ---------------------------------------------------------------------------
# Hostile input: the tokenizer never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("", id="empty"),
        pytest.param("\n", id="one-newline"),
        pytest.param("\n\n\n", id="only-newlines"),
        pytest.param("   ", id="only-spaces"),
        pytest.param("<", id="lone-open-bracket"),
        pytest.param(">", id="lone-close-bracket"),
        pytest.param("<>", id="empty-tag"),
        pytest.param("</>", id="empty-closing-tag"),
        pytest.param("<player", id="unterminated-tag"),
        pytest.param('<player name="a>b">', id="angle-inside-attribute"),
        pytest.param("</night", id="unterminated-closing-tag"),
        pytest.param("[bold]not markup[/bold]", id="console-markup-shaped"),
        pytest.param("Alice: café — naïve \U0001f600", id="unicode"),
        pytest.param("bell\x07 back\x08 vt\x0b ff\x0c cr\x0d", id="control-codes"),
        pytest.param("x" * 5000, id="very-long-line"),
        pytest.param("<day>" * 500, id="many-tags-one-line"),
    ],
)
def test_the_tokenizer_never_raises_and_always_round_trips(source: str) -> None:
    """Any input at all: no exception, and the spans rebuild it exactly.

    ``tokenize_transcript`` is on the read path of a viewer whose whole job is to
    show a file; a traceback there loses the game the reviewer opened. The round
    trip is re-asserted per case because "did not raise" alone would pass on a
    function that returned ``[]``.
    """
    spans = _spans(source)
    assert "".join(text for text, _ in spans) == source
    assert all(text for text, _ in spans), "a span has empty text"


# ---------------------------------------------------------------------------
# The corpus guard behind the widget-level control-code tripwire (B14)
# ---------------------------------------------------------------------------


# Rich's ``strip_control_codes`` (``rich.control.STRIP_CONTROL_CODES``) silently
# removes these five, and ``rich.text.Text.append`` runs it — so a transcript
# containing one would render one character short of its file. ``\n`` and ``\t``
# are NOT in the list and survive.
_RICH_STRIPPED_CONTROL_CODES = "\x07\x08\x0b\x0c\x0d"


@_requires_corpus
def test_no_committed_transcript_contains_a_rich_stripped_control_code() -> None:
    """The guard that keeps the widget-level rendering hole theoretical.

    ``TranscriptScreen`` builds a ``rich.text.Text``, whose ``append`` drops BEL,
    BS, VT, FF and CR. The tokenizer round-trips them perfectly, so half 1 cannot
    see the loss — only a widget-level assertion can, and
    ``tests/test_ledger_viewer.py``'s
    ``test_the_rich_text_path_drops_the_five_control_codes`` pins that behaviour
    as measured.

    This test is the other half of that pair: it says the hole is not currently
    open on any real game. If it ever fires, a committed transcript renders
    incomplete in the viewer and the renderable has to be made lossless.

    Note (measured, and correcting tech-spec §2 Component C): switching to
    Textual's native ``Content`` does **not** by itself fix that — ``Content``
    strips the same five codes by default. It takes an explicit
    ``strip_control_codes=False``. See
    ``tests/test_ledger_viewer.py::test_the_rich_text_path_drops_the_five_control_codes``.
    """
    offenders = {
        _rel(path): sorted(
            hex(ord(char))
            for char in set(_RICH_STRIPPED_CONTROL_CODES)
            if char in text
        )
        for path in _TRANSCRIPTS
        if any(
            char in (text := path.read_text(encoding="utf-8"))
            for char in _RICH_STRIPPED_CONTROL_CODES
        )
    }
    assert not offenders, (
        "committed transcripts contain control codes that rich.text.Text.append "
        f"silently drops, so the viewer renders them short: {offenders}"
    )
