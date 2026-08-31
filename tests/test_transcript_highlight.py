"""Tests for the pure transcript tokenizer (spec 038, Slice 1).

Two halves, in the order the slice was built:

1. **The corpus round-trip property test** (Slice 1, task 1 — written before the
   tokenizer existed), which sweeps every committed game;
2. **The tokenizer's synthetic unit tests** (Slice 1, task 4), which pin the kind
   each recognised shape gets, on inputs small enough to read.

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

**Kinds are not asserted by the sweep.** Slice 1 recognises only ``marker`` and
``plain``, and later slices add more; pinning *which* kind a given shape gets is
the unit tests' job (half 2), on synthetic inputs the reader can check by eye.
What the sweep pins is structural and permanent: total coverage of the text, no
empty spans, and a non-empty semantic name on every span.
"""

from __future__ import annotations

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

# The TWELVE structural tags, each with a representative opening form. Ratified
# during Slice 1 (see the header note in ``tasks.md``): the eight attribute-free,
# content-free section delimiters are only a *subset* — ``<player …>``,
# ``<vote …>``, ``<recap>`` and ``<thought …>`` are markers too, from Slice 1
# onward, because Slices 2-3 reclassify their *innards* and would otherwise have
# to promote whole lines out of ``plain``. A game whose ``<setup>`` dimmed while
# each ``<player …>`` inside it read as content would be the visible symptom.
_STRUCTURAL_TAGS: tuple[tuple[str, str], ...] = (
    ("transcript", "<transcript>"),
    ("setup", "<setup>"),
    ("preamble", "<preamble>"),
    ("night", "<night>"),
    ("day", "<day>"),
    ("round", "<round>"),
    ("endgame", "<endgame>"),
    ("kill", "<kill>"),
    ("player", '<player name="Alice" role="Mafioso">'),
    ("vote", '<vote initiator="Alice" target="Bo">'),
    ("recap", "<recap>"),
    ("thought", '<thought player="Alice">'),
)

_TAG_CASES = [
    pytest.param(opening, id=name) for name, opening in _STRUCTURAL_TAGS
]
_CLOSING_TAG_CASES = [
    pytest.param(f"</{name}>", id=name) for name, _ in _STRUCTURAL_TAGS
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
    """
    whitelist = getattr(eval_ledger, "_MARKER_TAGS", None)
    assert whitelist is not None, (
        "graphia.eval_ledger._MARKER_TAGS is gone — the tag whitelist moved or "
        "was renamed; update _STRUCTURAL_TAGS and this guard together"
    )
    swept = {name for name, _ in _STRUCTURAL_TAGS}
    assert swept == set(whitelist)
    assert len(_STRUCTURAL_TAGS) == 12, (
        "the ratified vocabulary is twelve tags (tasks.md, Slice 1 header note)"
    )


@pytest.mark.parametrize("source", _TAG_CASES)
def test_each_structural_tag_line_is_a_single_marker_span(source: str) -> None:
    """A recognised opening tag alone on a line is one ``marker`` span, whole.

    Covers all twelve — including the four (``player``, ``vote``, ``recap``,
    ``thought``) that carry attributes or content and that a reader of the
    tech-spec's eight-tag prose might expect to be ``plain``. They are not, and
    this is the test that says so.
    """
    assert _spans(source) == [(source, eval_ledger.KIND_MARKER)]


@pytest.mark.parametrize("source", _CLOSING_TAG_CASES)
def test_each_structural_closing_tag_line_is_a_single_marker_span(
    source: str,
) -> None:
    """``</tag>`` alone on a line is one ``marker`` span, for all twelve tags."""
    assert _spans(source) == [(source, eval_ledger.KIND_MARKER)]


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
    ("source", "expected"),
    [
        pytest.param(
            '<player name="Bo" role="Law-abiding Citizen">'
            "(no persona recorded)</player>",
            [
                ('<player name="Bo" role="Law-abiding Citizen">', "marker"),
                ("(no persona recorded)", "plain"),
                ("</player>", "marker"),
            ],
            id="player",
        ),
        pytest.param(
            "<recap>Alive: Alice, Bo. Mafia 1, Law-abiding 1.</recap>",
            [
                ("<recap>", "marker"),
                ("Alive: Alice, Bo. Mafia 1, Law-abiding 1.", "plain"),
                ("</recap>", "marker"),
            ],
            id="recap",
        ),
        pytest.param(
            '<thought player="Alice">Bo suspects me.</thought>',
            [
                ('<thought player="Alice">', "marker"),
                ("Bo suspects me.", "plain"),
                ("</thought>", "marker"),
            ],
            id="thought",
        ),
    ],
)
def test_an_inline_element_yields_three_spans(
    source: str, expected: list[tuple[str, str]]
) -> None:
    """``<tag …>content</tag>`` splits into opening tag, content, closing tag.

    Ratified during Slice 1. The split is what lets Slices 2-3 change only the
    **middle** span's kind (``recap`` content becomes ``recap``, a thought's
    becomes ``thought``) instead of breaking one span into three — the rewrite
    the slicing exists to avoid. The content is ``plain`` for now; that is this
    slice's answer, and the slice that claims each one will move it.
    """
    assert _spans(source) == expected


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
    """
    source = "<recap>Avery — Law-abiding Citizen</recap>"
    assert _spans(source) == [
        ("<recap>", eval_ledger.KIND_MARKER),
        ("Avery — Law-abiding Citizen", eval_ledger.KIND_PLAIN),
        ("</recap>", eval_ledger.KIND_MARKER),
    ]


def test_an_empty_element_contributes_no_inner_span() -> None:
    """``<night></night>`` — what the writer emits for a section that captured
    nothing — is two adjacent markers and therefore one span, with no empty span
    wedged between them."""
    assert _spans("<night></night>") == [
        ("<night></night>", eval_ledger.KIND_MARKER)
    ]


def test_an_unclosed_inline_element_degrades_to_marker_plus_plain() -> None:
    """A tag whose content does not close on the same line still never raises.

    The corpus contains no such line today, but a multi-line model-generated
    thought would produce one, and the reviewer must still be able to read it.
    """
    assert _spans('<thought player="Alice">it runs on') == [
        ('<thought player="Alice">', eval_ledger.KIND_MARKER),
        ("it runs on", eval_ledger.KIND_PLAIN),
    ]


# ---------------------------------------------------------------------------
# The `plain` fallback — the thing that makes degradation total
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("Alice: I saw nothing last night.", id="speech"),
        pytest.param(
            "Moderator: A new game begins. Welcome, Alice.", id="moderator"
        ),
        pytest.param("Personality: brisk and sly", id="cast-field"),
        pytest.param("(no persona recorded)", id="no-persona"),
        # A tag the writer does not emit: styled as skeleton it is not would be
        # a guess, so the whitelist declines and the line stays readable.
        pytest.param("<diary>secret</diary>", id="unrecognised-tag"),
        pytest.param("<Day>", id="wrong-case-tag"),
        pytest.param("<night/>", id="self-closing"),
        # Speech that merely contains angle brackets.
        pytest.param("Alice: 3 < 4 and 5 > 2", id="angle-brackets-in-speech"),
        # Console-markup-shaped prose. The tokenizer has no opinion on `[`; the
        # widget-level guarantee that it renders literally is B14's job in
        # tests/test_ledger_viewer.py.
        pytest.param("Personality: brisk [bold] and sly", id="square-brackets"),
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
# Structural guarantees: indentation, separators, coalescing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "indent",
    [pytest.param("  ", id="two-spaces"), pytest.param("\t", id="tab")],
)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<round>", id="tag"),
        pytest.param("Round 1.", id="round-label"),
        pytest.param('<player name="Alice" role="Mafioso">', id="player-tag"),
    ],
)
def test_leading_indentation_is_its_own_plain_span(indent: str, body: str) -> None:
    """Indentation is layout, never part of the marker.

    This is the whole reason the three pre-spec-022 run dirs need no era branch:
    they indent ``  <round>`` and ``  Round N.`` where the spec-022 form does
    not, and splitting the indent off makes both tokenize to the same marker
    span. Pinned as the exact two-span list, so an implementation that swallowed
    the indent into the marker (and styled trailing whitespace) would fail.
    """
    assert _spans(indent + body) == [
        (indent, eval_ledger.KIND_PLAIN),
        (body, eval_ledger.KIND_MARKER),
    ]


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
    """
    source = "<day>\n<round>\nAlice: hi\n</round>\n</day>"
    assert _spans(source) == [
        ("<day>", eval_ledger.KIND_MARKER),
        ("\n", eval_ledger.KIND_PLAIN),
        ("<round>", eval_ledger.KIND_MARKER),
        ("\nAlice: hi\n", eval_ledger.KIND_PLAIN),
        ("</round>", eval_ledger.KIND_MARKER),
        ("\n", eval_ledger.KIND_PLAIN),
        ("</day>", eval_ledger.KIND_MARKER),
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
    # Absolute, counted by hand off `_RICH_SYNTHETIC_TRANSCRIPT`: the metadata
    # header, `Round 1.`, the coalesced `<kill>…</kill>` line as ONE span, and the
    # 24 remaining opening/closing tags. Pinned as a number rather than as "> 0"
    # so a tokenizer that stopped marking a whole *category* of tag — losing, say,
    # every closing form — would fail here even though some markers survived.
    assert len(styled) == 27, (
        "the premise: this game really does produce styled spans "
        f"(got {len(styled)})"
    )
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
    """Coalescing, pinned absolutely: five prose lines are one span, not five."""
    source = "\n".join(f"Alice: line {i}" for i in range(5))
    assert _spans(source) == [(source, eval_ledger.KIND_PLAIN)]


def test_every_kind_emitted_is_declared_in_the_vocabulary() -> None:
    """``TRANSCRIPT_KINDS`` really is the canonical list the UI builds from.

    Deliberately NOT ``TRANSCRIPT_KINDS == ("marker", "plain")``: later slices
    append to it, and a test that has to be edited by every slice teaches the
    editor to edit it without reading. What must hold forever is the containment
    — a kind the tokenizer emits but never declares is a kind the UI's style map
    can never learn about.
    """
    kinds = {kind for _, kind in _spans(_RICH_SYNTHETIC_TRANSCRIPT)}
    assert kinds <= set(eval_ledger.TRANSCRIPT_KINDS)
    # ...and this slice really does emit both of them.
    assert kinds == {eval_ledger.KIND_MARKER, eval_ledger.KIND_PLAIN}


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
