"""Tests for the pure transcript tokenizer (spec 038, Slices 1-3).

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
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from graphia import eval_ledger

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
    """
    labels = set(eval_ledger._FIELD_LABELS)
    non_speakers = set(eval_ledger._NON_SPEAKER_PREFIXES)
    problems: list[str] = []
    attr_names: Counter[str] = Counter()
    label_texts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    lowercase_speakers: set[str] = set()

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
            if kind == eval_ledger.KIND_SPEAKER:
                after = spans[index + 1] if index + 1 < len(spans) else None
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
                elif text[0].islower():
                    lowercase_speakers.add(text)
                if after is None or after[1] != eval_ledger.KIND_SPEECH:
                    problems.append(
                        f"{_rel(path)} span {index}: speaker {text!r} is followed "
                        f"by {after!r}, expected a speech span"
                    )
            elif kind == eval_ledger.KIND_SPEECH:
                before = spans[index - 1] if index else None
                if before is None or before[1] != eval_ledger.KIND_SPEAKER:
                    problems.append(
                        f"{_rel(path)} span {index}: speech {text[:40]!r} is "
                        f"preceded by {before!r}, expected a speaker span"
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
            elif kind == eval_ledger.KIND_ATTR:
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
                    attr_names[before[0].rsplit(" ", 1)[-1][: -len(_ATTR_OPENS_WITH)]] += 1
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

    # Non-vacuity: the corpus really exercises both kinds, all five attributes
    # and all five labels.
    assert set(attr_names) == set(eval_ledger._ATTR_NAMES), (
        f"attribute values found for {sorted(attr_names)}, expected all of "
        f"{sorted(eval_ledger._ATTR_NAMES)}"
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
    assert (
        kind_counts[eval_ledger.KIND_SPEAKER]
        == kind_counts[eval_ledger.KIND_SPEECH]
        > 0
    ), f"speaker/speech counts diverged: {kind_counts}"
    assert kind_counts[eval_ledger.KIND_THOUGHT] > 0
    assert kind_counts[eval_ledger.KIND_RECAP] > 0
    # The measured reason `_SPEAKER_RE` is not gated on an initial capital: the
    # corpus really does contain lowercase-named players, so a casing rule would
    # drop real speaker lines rather than only the three writer literals.
    assert lowercase_speakers, (
        "no lowercase-initial speaker name found — either the corpus changed or "
        "the speaker rule has quietly acquired a casing guard, which measurement "
        "rejected (39 lowercase cast names, 426 speaker lines at stake)"
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
    """The eight kind constants really are these eight strings.

    The expectation tables below are written with literal ``"marker"`` /
    ``"attr"`` strings rather than ``eval_ledger.KIND_*`` references, because a
    five-span expectation reads as a span list only when it is spelled like one.
    That is safe exactly as far as this test: it ties the literals to the
    constants in one place, so renaming a constant fails **here**, with a message
    naming the pair, instead of leaving every table below silently asserting
    against a string the production code no longer emits.

    Pinned to the literals, not to ``TRANSCRIPT_KINDS`` — the tuple is allowed to
    grow (later slices append), these eight names are not allowed to change.
    Slice 4 appends side-bearing kinds *derived* from ``speaker`` / ``speech``
    (``speaker-mafia`` and friends), so those two literals in particular are load
    bearing beyond this file.
    """
    assert eval_ledger.KIND_MARKER == "marker"
    assert eval_ledger.KIND_PLAIN == "plain"
    assert eval_ledger.KIND_ATTR == "attr"
    assert eval_ledger.KIND_FIELD_LABEL == "field-label"
    assert eval_ledger.KIND_SPEAKER == "speaker"
    assert eval_ledger.KIND_SPEECH == "speech"
    assert eval_ledger.KIND_THOUGHT == "thought"
    assert eval_ledger.KIND_RECAP == "recap"


# The TWELVE structural tags, each with a representative opening form and the
# COMPLETE span list that form must produce. Ratified during Slice 1 (see the
# header note in ``tasks.md``): the eight attribute-free, content-free section
# delimiters are only a *subset* — ``<player …>``, ``<vote …>``, ``<recap>`` and
# ``<thought …>`` are markers too, from Slice 1 onward, because Slices 2-3
# reclassify their *innards* and would otherwise have to promote whole lines out
# of ``plain``. A game whose ``<setup>`` dimmed while each ``<player …>`` inside
# it read as content would be the visible symptom.
#
# **Changed in Slice 2**: the three tags that carry one of the five detail
# attributes no longer produce a single span. ``attr`` is the VALUE only, so the
# tag alternates marker / attr / marker — the key, the quotes and the angle
# brackets all stay marker. The other nine are unchanged and must STAY one span:
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
)

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
    """The twelve tags below are exactly the ones the tokenizer recognises.

    A ROT GUARD, in the same spirit as ``test_ledger_viewer``'s
    ``_DETAIL_KEYS_SWEPT`` check against ``DetailScreen.BINDINGS``: without it a
    thirteenth tag could be added to the tokenizer and never swept by the case
    tables below, and the "each recognised marker" coverage this task owes would
    quietly become "each marker somebody remembered".

    Reaches for the module-private ``_MARKER_TAGS`` deliberately — it is the
    whitelist itself, and there is no public projection of it. The count is
    pinned absolutely as well, because a set comparison against a table derived
    from the same source could not catch both sides shrinking together.

    The 9 / 3 split is pinned too (Slice 2): the derived
    ``_ATTRIBUTE_FREE_TAG_CASES`` is what the "still a single marker span" test
    sweeps, so a table edit that quietly emptied it would make that test vacuous
    rather than failing.
    """
    whitelist = getattr(eval_ledger, "_MARKER_TAGS", None)
    assert whitelist is not None, (
        "graphia.eval_ledger._MARKER_TAGS is gone — the tag whitelist moved or "
        "was renamed; update _STRUCTURAL_TAGS and this guard together"
    )
    swept = {name for name, _, _ in _STRUCTURAL_TAGS}
    assert swept == set(whitelist)
    assert len(_STRUCTURAL_TAGS) == 12, (
        "the ratified vocabulary is twelve tags (tasks.md, Slice 1 header note)"
    )
    assert len(_ATTRIBUTE_FREE_TAG_CASES) == 9, (
        "nine of the twelve tags carry no attribute; three do "
        "(<player>, <vote>, <thought>)"
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


# Every one of the five whitelisted attributes, in a tag the writer really emits
# it on, with the value it must lift out. Swept so no attribute is covered only
# by accident of appearing beside another.
_ATTR_NAME_CASES: tuple[tuple[str, str, str], ...] = (
    ("name", '<player name="Avery" role="Mafioso">', "Avery"),
    ("role", '<player name="Avery" role="Mafioso">', "Mafioso"),
    ("player", '<thought player="Avery">', "Avery"),
    ("initiator", '<vote initiator="Avery" target="Bo">', "Avery"),
    ("target", '<vote initiator="Avery" target="Bo">', "Bo"),
)


def test_the_swept_attribute_names_match_the_tokenizers_whitelist() -> None:
    """The five attributes swept below are exactly the ones the tokenizer lifts.

    The same rot guard the tag vocabulary gets, for the same reason: a sixth
    attribute added to ``_ATTR_NAMES`` and never swept would ship untested, and
    the "distinct ``attr`` spans for both values" coverage this task owes would
    become "the two values somebody remembered".
    """
    whitelist = getattr(eval_ledger, "_ATTR_NAMES", None)
    assert whitelist is not None, (
        "graphia.eval_ledger._ATTR_NAMES is gone — the attribute whitelist moved "
        "or was renamed; update _ATTR_NAME_CASES and this guard together"
    )
    assert {name for name, _, _ in _ATTR_NAME_CASES} == set(whitelist)
    assert len(_ATTR_NAME_CASES) == 5


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
                ("Law-abiding Citizen", "attr"),
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
    surrounding tag stays ``marker``"), so the owner's name inside a thought's tag
    is still an ``attr`` span waiting for Slice 4 to give it a side.

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
        pytest.param("<diary>secret</diary>", id="unrecognised-tag"),
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
_RICH_SYNTHETIC_TRANSCRIPT = (
    "Game 2 | provider=ollama | large_model=qwen3-coder:30b | games=3\n"
    "<transcript>\n"
    "<setup>\n"
    '<player name="Alice" role="Mafioso">\n'
    "Personality: brisk and sly\n"
    "Manner: clipped\n"
    "</player>\n"
    '<player name="Bo" role="Law-abiding Citizen">(no persona recorded)</player>\n'
    "</setup>\n"
    "<preamble>\n"
    "Moderator: A new game begins. Welcome, Bo.\n"
    "\n"
    "</preamble>\n"
    "<night>\n"
    "<kill>Avery — Law-abiding Citizen</kill>\n"
    "</night>\n"
    "<day>\n"
    "  <round>\n"
    "  Round 1.\n"
    "Alice: I saw nothing last night.\n"
    '<thought player="Alice">Bo suspects me.</thought>\n'
    '<vote initiator="Alice" target="Bo">\n'
    "Bo: Yes\n"
    "</vote>\n"
    "<recap>Alive: Alice, Bo.</recap>\n"
    "  </round>\n"
    "</day>\n"
    "<endgame>\n"
    "Mafia win.\n"
    "</endgame>\n"
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
    #   marker 34 — the header, `Round 1.` and the coalesced `<kill>…</kill>`
    #               line as ONE span (3), the 20 attribute-free opening/closing
    #               tags, and the 11 marker pieces the four attributed tags split
    #               into (3 + 3 for the two cast entries, 2 for the thought,
    #               3 for the vote);
    #   attr    7 — Alice/Mafioso and Bo/Law-abiding Citizen from the two cast
    #               entries, Alice from the thought, Alice/Bo from the vote;
    #   field-label 2 — `Personality:` and `Manner:` on Alice's entry;
    #   speaker 2 — `Alice:` on the Day line and `Bo:` on the ballot inside the
    #               `<vote>` block (a ballot is speech, tasks.md Slice 3);
    #   speech  2 — the words after each of those two colons;
    #   thought 1 — `Bo suspects me.`, the body of the `<thought>` element;
    #   recap   1 — `Alive: Alice, Bo.`, the body of the `<recap>` element.
    #
    # The `Moderator: A new game begins.` preamble line is deliberately NOT among
    # them: he is in the exclusion set, so his line stays plain and coalesces with
    # the blank line after it. That absence is the count's own guard against the
    # speaker rule over-reaching.
    #
    # Slice 1 pinned only `len(styled) == 27` and Slice 2 raised it to 43 with a
    # three-kind breakdown. Slice 3 supersedes both — and the reason the breakdown
    # exists is visible in this very edit: the failure named `speaker`, `speech`,
    # `thought` and `recap` as the kinds that appeared, instead of saying only
    # that 43 had become 49.
    assert len(styled) == 49, (
        "the premise: this game really does produce styled spans "
        f"(got {len(styled)})"
    )
    assert Counter(kind for _, kind in styled) == {
        eval_ledger.KIND_MARKER: 34,
        eval_ledger.KIND_ATTR: 7,
        eval_ledger.KIND_FIELD_LABEL: 2,
        eval_ledger.KIND_SPEAKER: 2,
        eval_ledger.KIND_SPEECH: 2,
        eval_ledger.KIND_THOUGHT: 1,
        eval_ledger.KIND_RECAP: 1,
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
    words: after this slice the vocabulary has no dead entries. Slice 4 appends
    side-bearing kinds and will move all three together.
    """
    kinds = {kind for _, kind in _spans(_RICH_SYNTHETIC_TRANSCRIPT)}
    assert kinds <= set(eval_ledger.TRANSCRIPT_KINDS)
    # ...and this slice really does emit all eight of them.
    assert kinds == {
        eval_ledger.KIND_MARKER,
        eval_ledger.KIND_PLAIN,
        eval_ledger.KIND_ATTR,
        eval_ledger.KIND_FIELD_LABEL,
        eval_ledger.KIND_SPEAKER,
        eval_ledger.KIND_SPEECH,
        eval_ledger.KIND_THOUGHT,
        eval_ledger.KIND_RECAP,
    }
    # As of Slice 3 the vocabulary has no declared-but-unreachable entry.
    assert kinds == set(eval_ledger.TRANSCRIPT_KINDS)
    assert len(eval_ledger.TRANSCRIPT_KINDS) == len(set(eval_ledger.TRANSCRIPT_KINDS))


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
