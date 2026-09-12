"""Offline tests for the **early-stop marker** — ``quality.ended_early`` (spec 043, Slice 4).

Slice 3 made a stopped run leave a record. This slice is what stops that record
being *misread* as a planned run of that size, and it is one gated boolean plus
one comparison, so almost everything worth asserting is about what the marker
does **not** disturb:

* a **partial** record carries ``ended_early: true`` as the **first** ``quality``
  key, ahead of the three counts it changes the reading of;
* a **complete** record omits the key **entirely** — never ``false``, because
  absence is the value (and is two-valued: absent ⇒ the run attempted everything
  it was asked for, which every one of the 50 committed records provably did);
* the **all-failed full run** keeps behaving exactly as it does today — ten
  games, every one raising, one record, ``games_completed: 0`` and **no**
  marker, because it attempted everything it was asked for. That is
  functional-spec requirement five, and it is the case a
  ``games_completed == 0`` guard would silently break;
* ``settings.games`` keeps carrying the number **requested**, never the completed
  count.

THE SHARPEST TEST HERE IS THE PAIR, NOT THE MARKER. Section 4 drives a 4-of-10
run and a planned-4 run side by side and asserts their ``quality`` blocks are
**equal** once the marker line and the wall clock are removed. That is the
functional spec's distinguishability criterion in its strongest form, and it
states outright the thing the marker exists for: without it those two records
are indistinguishable in ``quality`` **entirely**, and a reader comparing a
stopped run against a planned baseline would have nothing to go on. A test that
only checked "the marker is present on a partial record" would stay green under
a change that made the marker redundant — this one says why it is not.

NO TEST SENDS A REAL SIGNAL, and that is the design rather than a shortcut.
There is no signal handler, no ``atexit`` hook and no graceful shutdown anywhere
in spec 043 (technical-considerations §4 says so plainly); durability comes from
having already written. So "the run was stopped" is expressed by making the
stubbed ``_play_one_game`` raise a ``KeyboardInterrupt`` — a ``BaseException``,
so the loop's ``except Exception`` does not catch it and the post-loop write is
never reached, which is exactly the control flow a ``SIGINT`` produces.
``tests/test_progressive_ledger_write.py`` established the idiom. The one
verification this suite cannot express — a real ``SIGKILL`` — is Task 4.4's
single manual kill, and this module does not pretend otherwise.

WHY A NEW MODULE. The repo files ledger tests by topic, one module per spec-043
slice: ``test_ledger_write.py`` (Slice 1 — the text splitter, its inverse, the
durable replace), ``test_run_identity.py`` (Slice 2 — who wrote a record),
``test_progressive_ledger_write.py`` (Slice 3 — *when* a record is written and
what a write must not disturb). This is Slice 4's topic: what a record **says
about its own completeness**. All three of those modules' docstrings already name
``quality.ended_early`` as deliberately not theirs and point here.

THE SCAFFOLDS ARE IMPORTED, NOT RE-DECLARED. ``_stub_run_eval_env`` (in
``test_blunder_eval.py``) owns the list of seams ``run_eval`` needs stubbed —
both provenance collectors, the model-name resolver, ``LEDGER_PATH`` and
``LEDGER_BACKUP_DIR`` — and ``test_progressive_ledger_write.py`` owns the
ledger-reading helpers built on top of it. Re-typing either here would let the
copies drift, and a stale copy of a stub list is how a test starts reaching a
real client.

THE COMMITTED LEDGER, THE REAL TRANSCRIPT STORE AND THE REAL ``.graphia/`` ARE
NEVER WRITTEN. ``evals/blunder-ledger.yaml`` is the project's only long-term
measurement history and an early-bound signature default once leaked ~25
synthetic records into it. Every ``run_eval`` call below redirects both the
ledger and the pre-run backup directory into ``tmp_path`` (the scaffold does it),
and injects ``transcripts_root=tmp_path/...`` so nothing is created under
``evals/transcripts/``. Section 6's autouse guard notices if any of that ever
stops working.

ASSERTIONS ON KEY ORDER ARE ON THE RENDERED **TEXT**, never on a parsed dict.
``_yaml_block`` renders in insertion order, so insertion order *is* key order and
the marker's position is a property of the bytes a human reads. ``yaml.safe_load``
would hand back a dict that happens to preserve order today and would agree just
as happily with a writer that appended the marker at the bottom — which is
mutation (d) in this task's own mutation table, and the reason the helpers below
read lines rather than parse.

WHAT IS DELIBERATELY NOT HERE. The viewer's ``4/10`` ``Games`` cell and the
drill-down's marker line landed in **Slice 5** (Tasks 5.1-5.2) after this module
was written — which is why the sentence here used to say they did not exist yet
— and they are pinned in ``tests/test_partial_run_viewer.py``, including the one
decision that spans both modules: the *writer* emits the key only when true, so
the hand-edited ``ended_early: false`` that the table and the drill-down read
through two different gates is a state no test here can produce. The write
cadence, the OCC cycle, the pre-run backup and the near-twin match are
``test_progressive_ledger_write.py``'s; the write primitives are
``test_ledger_write.py``'s; the id's shape is ``test_run_identity.py``'s.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

# The ``run_eval`` scaffold, the renderer's synthetic fixture and the
# ledger-reading helpers live with the test modules that own them (see the module
# docstring). This block sorts above ``graphia`` because a sibling test module is
# not first-party to the package, not because it matters more.
from test_blunder_eval import (
    _capture_for_storage,
    _storage_args,
    _stub_run_eval_env,
    _synthetic_result,
)
from test_progressive_ledger_write import (
    _completed_game,
    _count_splices,
    _documents,
    _foreign_result,
    _record_every_write,
    _seed_ledger,
    _text,
)

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    LEDGER_WRITE_ATTEMPTS_FINAL,
    render_record,
    run_eval,
    upsert_record,
)

# The run under test, pinned so a document can be found by eye in a failure
# message. Shape and uniqueness are ``test_run_identity.py``'s subject; here the
# id only has to be stable.
_RUN_ID = "2026-09-12T10-00-00-9f3a1c"

# The marker exactly as ``_yaml_block`` emits it: two-space indented (``quality``
# is a top-level block), and the bare YAML ``true`` — ``_yaml_scalar`` checks
# ``bool`` before ``int``, so it is never ``True`` or ``1``. Written as a literal
# rather than built from the field name so a rename of the attribute cannot
# quietly keep this file green.
_MARKER_LINE = "  ended_early: true"

# The wall clock, which is the ONE key inside ``quality`` that two otherwise
# identical runs cannot agree on. ``duration_seconds`` appears in BOTH ``run``
# and ``quality``; this prefix matches the ``quality`` one because the helper
# below has already narrowed to that block.
_WALL_CLOCK_PREFIX = "  duration_seconds:"


# ===========================================================================
# Helpers — all of them read the rendered TEXT, for the reason in the docstring.
# ===========================================================================


def _quality_lines(document: str) -> list[str]:
    """Every line of a rendered record's ``quality:`` block, in document order.

    Collects the indented lines that follow the top-level ``quality:`` header and
    stops at the next unindented line, so the leading ``---`` separator and every
    other block are ignored. Deeper indentation is kept rather than skipped —
    spec 041's ``quality.substitutions`` is a nested sub-map, and a helper that
    dropped its rows would silently narrow what "the two blocks are equal" means.
    """
    lines: list[str] = []
    inside = False
    for line in document.splitlines():
        if line and not line.startswith(" "):
            inside = line.startswith("quality:")
            continue
        if inside:
            lines.append(line)
    return lines


def _quality_keys(document: str) -> list[str]:
    """The ``quality`` block's own keys (two-space indent only), in order.

    Sub-map rows are excluded: the claim under test is where ``ended_early`` sits
    among ``quality``'s OWN keys, and a nested ``substitutions`` row is not one of
    them.
    """
    return [
        line.partition(":")[0].strip()
        for line in _quality_lines(document)
        if line.startswith("  ") and not line.startswith("    ")
    ]


def _comparable_quality(document: str) -> list[str]:
    """The ``quality`` block with the marker and the wall clock removed.

    The two lines two otherwise-identical runs are *allowed* to differ in. What
    remains must match line for line, which is what makes section 4's pair test
    the distinguishability criterion rather than a weaker restatement of it.
    """
    return [
        line
        for line in _quality_lines(document)
        if line != _MARKER_LINE and not line.startswith(_WALL_CLOCK_PREFIX)
    ]


def _block_lines(document: str, header: str) -> list[str]:
    """Every indented line under the top-level ``header:``, in document order."""
    lines: list[str] = []
    inside = False
    for line in document.splitlines():
        if line and not line.startswith(" "):
            inside = line.startswith(f"{header}:")
            continue
        if inside:
            lines.append(line)
    return lines


def _sole_document(ledger: Path) -> str:
    """The ledger's one and only document, asserted to be exactly one."""
    documents = _documents(_text(ledger))
    assert len(documents) == 1, f"expected one document, got {len(documents)}"
    return documents[0]


def _stub_games(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_at: tuple[int, ...] = (),
    stop_at: int | None = None,
) -> None:
    """Stub ``_play_one_game`` with a pinned per-game trajectory.

    ``fail_at`` names the 0-based game indices that raise a plain ``Exception``
    — the *failed-early* path, which the loop catches, counts and ``continue``s
    past without writing. ``stop_at`` names the index at which the run is
    STOPPED: a ``KeyboardInterrupt``, which is a ``BaseException``, so the loop's
    ``except Exception`` does not see it, it propagates out of ``run_eval`` and
    the post-loop write never happens — the control flow a real ``SIGINT``
    produces, with none of a signal's flakiness. Every other index completes.
    """

    def _stub(args: argparse.Namespace, game_index: int) -> Any:
        if stop_at is not None and game_index >= stop_at:
            raise KeyboardInterrupt
        if game_index in fail_at:
            raise RuntimeError("game exploded")
        return _capture_for_storage("Cara")

    monkeypatch.setattr(blunder_eval, "_play_one_game", _stub)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    games: int,
    fail_at: tuple[int, ...] = (),
    stop_at: int | None = None,
) -> Path:
    """Drive one stubbed ``run_eval`` into a ledger under ``tmp_path``; return it.

    ``tmp_path`` is the directory the scaffold hangs the ledger, the backup dir
    and the transcripts root off, so passing a *sub*-directory is what lets one
    test drive two runs into two separate ledgers (section 4's pair).
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    _stub_games(monkeypatch, fail_at=fail_at, stop_at=stop_at)

    def _call() -> None:
        run_eval(
            config,
            _storage_args(games=games),
            transcripts_root=tmp_path / "transcripts",
            run_id=_RUN_ID,
        )

    if stop_at is None:
        _call()
    else:
        # A stop is never observed: the ``KeyboardInterrupt`` propagates out of
        # ``run_eval``, so what is on disk is whatever the last completed game
        # wrote. That is the whole mechanism, expressed as a ``pytest.raises``.
        with pytest.raises(KeyboardInterrupt):
            _call()
    return ledger


@pytest.fixture
def transcripts_root(tmp_path: Path) -> Path:
    """The injected transcripts root — never ``evals/transcripts/``."""
    return tmp_path / "transcripts"


# ===========================================================================
# 1. The rendered marker, over hand-built results.
#
# ``render_record`` is a pure function of an ``EvalResult``, so the gate and the
# position are testable with no run at all. These are the two assertions the
# whole slice reduces to, and they are the ones mutations (a) and (d) break.
# ===========================================================================


def test_the_marker_is_the_very_first_quality_key_ahead_of_the_three_counts() -> None:
    """``ended_early`` opens the block, before ``games_attempted``.

    POSITION IS THE POINT, and it is not decoration. ``_yaml_block`` renders in
    insertion order, so insertion order *is* key order, and this field's whole
    job is to stop a reader parsing the three counts beneath it as a PLANNED run
    of that size. A qualifier that decides how everything below it reads belongs
    above it — the ``run.kind`` precedent, which sits immediately after ``date``
    for the same reason. Appended at the bottom of the block it would sit below
    the very numbers it qualifies, and past the wall clock, where a reader
    scanning the counts would never reach it.

    Asserted on the rendered TEXT rather than on ``yaml.safe_load``: a parsed
    dict preserves insertion order today and would agree just as happily with a
    writer that appended the marker last.
    """
    result = _synthetic_result()
    result.ended_early = True

    doc = render_record(result, "2026-06-13")

    assert _quality_keys(doc)[0] == "ended_early"
    assert _quality_keys(doc)[:4] == [
        "ended_early",
        "games_attempted",
        "games_completed",
        "games_failed_early",
    ]
    assert _quality_lines(doc)[0] == _MARKER_LINE
    assert doc.index(_MARKER_LINE) < doc.index("  games_attempted:")


def test_a_complete_result_renders_no_marker_line_at_all_rather_than_false() -> None:
    """Absence carries the value — ``ended_early: false`` is never written.

    The gate, and the reason it is a gate. Absence here is TWO-valued, not three:
    before this spec there was exactly one route to the ledger (falling off the
    end of the games loop), so every one of the 50 committed records is provably
    a run that attempted everything it was asked for, and a reader of an older
    record should conclude exactly that. Emitting ``false`` would add a third
    reading that does not exist, and — the mechanical half — would change the
    bytes of every complete run's record, breaking the byte-identity that
    protects every existing reader and every committed comparison.

    The ``false`` check is deliberately made against the whole document, not just
    the block: an unconditional emission that also moved the key would still be
    caught.
    """
    result = _synthetic_result()
    assert result.ended_early is False, "the dataclass default must be the absent value"

    doc = render_record(result, "2026-06-13")

    assert "ended_early" not in doc
    assert "ended_early" not in _quality_keys(doc)
    assert _quality_keys(doc)[0] == "games_attempted"


def test_the_marker_changes_nothing_else_in_the_quality_block() -> None:
    """One result, rendered twice: the blocks differ in the marker line ALONE.

    The pure-function form of section 4's pair, and the cheapest possible
    statement of what the marker costs — one line, inserted at the top, with
    every other key and every other value untouched. A ``settings.games`` rewrite
    (mutation (c)) is outside this block and is caught in section 4; what this
    pins is that the marker itself is purely additive.
    """
    result = _synthetic_result()
    unmarked = render_record(result, "2026-06-13")
    result.ended_early = True
    marked = render_record(result, "2026-06-13")

    assert _quality_lines(marked) == [_MARKER_LINE, *_quality_lines(unmarked)]
    # And nothing outside ``quality`` moved either.
    assert marked.replace(f"{_MARKER_LINE}\n", "") == unmarked


# ===========================================================================
# 2. The marker through a real (stubbed) run — the rule ``_finalise_result``
#    applies, on real bytes in a real ledger.
#
# ``result.ended_early = result.games_attempted < args.games``, evaluated at
# every write. No special-casing, and none is needed: a progressive write happens
# mid-loop where ``games_attempted`` is necessarily short of what was asked for,
# and the post-loop write happens after the loop has attempted everything.
# ===========================================================================


def test_a_run_stopped_after_four_of_ten_games_lands_one_marked_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The spec's headline case, end to end: 4 of 10, marked, and both numbers present.

    Ten games asked for, four played to a natural finish, then stopped. What must
    be on disk afterwards is ONE record whose scores cover exactly those four
    games, carrying the marker first and ``settings.games: 10`` — the pair a
    reader judges the record's weight from. Neither number works without the
    other: the marker alone does not say how far short it fell, and the counts
    alone are indistinguishable from a planned four-game run.

    ``settings.games`` reading 10 is asserted here rather than only in the pair
    test because it is the single most tempting "fix" — stamping the completed
    count there would make the record self-consistent and destroy exactly the
    distinction this slice exists to draw. ``settings`` is documented as the
    values that let a run be repeated LIKE-FOR-LIKE, and the value that
    reproduces this run's intent is the number requested.
    """
    ledger = _run(monkeypatch, tmp_path, games=10, stop_at=4)

    document = _sole_document(ledger)
    assert _quality_keys(document)[0] == "ended_early"
    assert _MARKER_LINE in document

    quality = yaml.safe_load(document)["quality"]
    assert (quality["games_attempted"], quality["games_completed"]) == (4, 4)
    assert "  games: 10" in _block_lines(document, "settings")
    assert yaml.safe_load(document)["settings"]["games"] == 10


def test_a_run_that_plays_every_game_it_asked_for_lands_an_unmarked_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nothing about a complete run changes — the regression the whole slice owes.

    Functional-spec requirement one's third criterion: *"given a run that plays
    every game it was asked for, its entry reads exactly as such entries read
    today"*. The post-loop write sees ``4 < 4``, which is false, and the key is
    omitted — so the word does not appear in the document at all.
    """
    ledger = _run(monkeypatch, tmp_path, games=4)

    document = _sole_document(ledger)
    assert "ended_early" not in document
    assert _quality_keys(document)[0] == "games_attempted"
    assert yaml.safe_load(document)["quality"]["games_completed"] == 4


def test_the_all_failed_full_run_is_undisturbed_and_carries_no_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ten games, every one raising: one record, ``games_completed: 0``, NO marker.

    Functional-spec requirement five, and the case a ``games_completed == 0``
    guard would silently break. This run did **not** end early — it attempted
    every one of the ten games it was asked for and every one of them failed,
    which is a different fact about a different thing and is already recorded, in
    ``games_failed_early``. Marking it would tell a reader the run was cut short
    when what actually happened is that the measurement failed ten times out of
    ten.

    THE NAMING HAZARD THIS TEST STANDS ON: ``ended_early`` describes the **run**,
    ``games_failed_early`` describes a **game**. They are the pair a reader — and
    an implementer — trips over, and this record is where the difference is
    visible, because it is the one shape where the two would disagree.

    The rule that gets it right is ``games_attempted < args.games`` (10 < 10 ⇒
    false). ``games_completed < args.games`` (0 < 10 ⇒ true) would mark this
    record and break the requirement, while leaving every other test in this
    module green — which is why this case is asserted rather than assumed.
    """
    ledger = _run(monkeypatch, tmp_path, games=10, fail_at=tuple(range(10)))

    document = _sole_document(ledger)
    assert "ended_early" not in document

    quality = yaml.safe_load(document)["quality"]
    assert quality["games_attempted"] == 10
    assert quality["games_completed"] == 0
    assert quality["games_failed_early"] == 10
    assert yaml.safe_load(document)["settings"]["games"] == 10


def test_the_marker_is_true_on_every_progressive_write_and_clears_on_the_last_game(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """A live record says "ended early" until the run's last game — correctly.

    The rule applied at every write, not merely at the end, and the shape it
    produces is worth stating because it looks surprising: a three-game run's
    first two records carry the marker. They are RIGHT to. At the instant write
    one lands, the run has attempted one game of the three it was asked for — so
    if the power went at that instant, the record standing on disk would be a
    1-of-3 record, and it says so. The marker clears on the write after game
    three, where ``3 < 3`` is false, and the post-loop write then re-renders the
    same unmarked record.

    No special-casing, and none is needed: ``final`` is deliberately NOT consulted
    when deciding the marker. It would give the same answer today only because
    the loop always attempts every game, and reading it would encode that
    coincidence as a rule.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    writes = _record_every_write(monkeypatch)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    texts = [snapshot.decode("utf-8") for snapshot in writes]
    assert len(texts) == 4, "three completed games write three times, plus the final one"
    assert [_MARKER_LINE in text for text in texts] == [True, True, False, False]
    assert [yaml.safe_load(_documents(t)[0])["quality"]["games_attempted"] for t in texts] == [
        1,
        2,
        3,
        3,
    ]


def test_a_run_that_was_asked_for_no_games_at_all_is_not_marked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The ``--games 0`` edge, checked rather than assumed: ``0 < 0`` is false.

    ``range(0)`` runs no iterations, so the only write is the post-loop one, with
    zero attempted against zero requested. A run that was asked for nothing and
    attempted nothing did not stop short of anything, so the key is absent —
    which is also the boundary that catches a comparison loosened to ``<=``.
    """
    ledger = _run(monkeypatch, tmp_path, games=0)

    document = _sole_document(ledger)
    assert "ended_early" not in document
    assert yaml.safe_load(document)["quality"]["games_attempted"] == 0
    assert yaml.safe_load(document)["settings"]["games"] == 0


# ===========================================================================
# 3. The balance invariant (technical-considerations §2.4).
#
# ``games_attempted == games_completed + games_failed_early`` is a cross-kind
# property of the ledger — ``persona_bench`` preserves it by construction too —
# and the second by-eye check a reader makes on a record. §2.4 asks for it to be
# ASSERTED on a partial record rather than left to inspection, and it holds with
# no code spent on it only *because* every write happens after a game has reached
# a terminal outcome.
# ===========================================================================


@pytest.mark.parametrize(
    ("games", "fail_at", "stop_at", "expected", "marked"),
    [
        # A partial run with a failure mixed in — the only shape where the three
        # counts can genuinely disagree, and therefore the only one where the
        # invariant is not trivially true. Game 1 fails, games 2-4 complete,
        # game 5 is interrupted: the last write that landed covers 4 attempted,
        # 3 completed, 1 failed.
        pytest.param(10, (0,), 4, (4, 3, 1), True, id="partial-with-a-failed-game"),
        # A partial run with nothing but completions.
        pytest.param(10, (), 4, (4, 4, 0), True, id="partial-all-completed"),
        # The complete run — the record that must read exactly as it does today.
        pytest.param(4, (), None, (4, 4, 0), False, id="complete-run"),
        # The all-failed FULL run: attempted everything, completed nothing, and
        # correctly unmarked (functional-spec requirement five).
        pytest.param(
            10, tuple(range(10)), None, (10, 0, 10), False, id="all-failed-full-run"
        ),
    ],
)
def test_the_three_counts_balance_and_the_marker_agrees_with_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    games: int,
    fail_at: tuple[int, ...],
    stop_at: int | None,
    expected: tuple[int, int, int],
    marked: bool,
) -> None:
    """Four run shapes, one invariant, and the marker read off the same record.

    The two claims are parametrized together on purpose. The invariant alone
    would pass under a marker that was always absent; the marker alone would pass
    under counts that did not add up. Asserted side by side, the sweep says the
    record is internally consistent AND labelled consistently with itself — which
    is the whole of what a reader checks by eye before trusting a row.

    The marker column is also where a loosened or inverted comparison shows: an
    inverted ``games_attempted > args.games`` never marks anything and fails the
    two partial rows; a ``<=`` marks everything and fails the two complete ones.
    """
    ledger = _run(monkeypatch, tmp_path, games=games, fail_at=fail_at, stop_at=stop_at)

    document = _sole_document(ledger)
    quality = yaml.safe_load(document)["quality"]

    counts = (
        quality["games_attempted"],
        quality["games_completed"],
        quality["games_failed_early"],
    )
    assert counts == expected
    assert counts[0] == counts[1] + counts[2], (
        f"games_attempted must equal games_completed + games_failed_early:\n{document}"
    )
    assert (_MARKER_LINE in document) is marked
    assert ("ended_early" in document) is marked, "the key must be absent, never false"


# ===========================================================================
# 4. The distinguishability pair — the criterion in its strongest form.
#
# Functional-spec requirement two's third criterion: *"given an entry from a run
# stopped after four of ten games, and an entry from a run that asked for four
# games and played all four, the two are clearly distinguishable as an incomplete
# run and a complete one."*
# ===========================================================================


def test_a_four_of_ten_record_and_a_planned_four_record_differ_only_in_the_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The two records' ``quality`` blocks are EQUAL once the marker is removed.

    Driven side by side rather than reasoned about: two runs over the same
    stubbed game, one asked for ten and stopped after four, the other asked for
    four and given all four. They measured the same four games, so every scored
    block agrees — and that is precisely the problem the marker solves. Strip the
    marker line and the wall clock and the two ``quality`` blocks are
    **identical**, line for line: the counts, the outcomes they were folded from,
    everything. **Without the marker those two records are indistinguishable in
    ``quality`` entirely.**

    So the pair asserts three things, and all three are needed:

    1. the two blocks are equal modulo the marker and ``duration_seconds`` — the
       statement of what would be lost;
    2. the marker is present on one and absent from the other — the thing that is
       therefore load-bearing rather than decorative;
    3. ``settings.games`` reads ``10`` against ``4`` — the second half of the
       pair a reader needs, since the marker says *that* it fell short and this
       says *how far*. Stamping the completed count into ``settings.games``
       collapses the 4-of-10 record onto the planned-4 one and fails the
       criterion directly even with the marker in place, which is why this is
       asserted here and not left to the record's own internal consistency.

    The ``outcomes`` blocks are compared as supporting evidence: they are the
    scored measurement itself, and their byte-equality is what makes "the same
    four games, two records" a fact rather than an assumption about the stub.
    """
    partial = _run(monkeypatch, tmp_path / "partial", games=10, stop_at=4)
    planned = _run(monkeypatch, tmp_path / "planned", games=4)

    partial_doc = _sole_document(partial)
    planned_doc = _sole_document(planned)

    # 1. Identical apart from the marker and the wall clock.
    assert _comparable_quality(partial_doc) == _comparable_quality(planned_doc)
    assert _block_lines(partial_doc, "outcomes") == _block_lines(planned_doc, "outcomes")

    # 2. And the marker is the one thing that tells them apart in ``quality``.
    assert _MARKER_LINE in partial_doc
    assert "ended_early" not in planned_doc
    assert _quality_keys(partial_doc) == ["ended_early", *_quality_keys(planned_doc)]

    # 3. The number each run ASKED FOR, which is what makes the marker actionable.
    assert yaml.safe_load(partial_doc)["settings"]["games"] == 10
    assert yaml.safe_load(planned_doc)["settings"]["games"] == 4
    assert "  games: 10" in _block_lines(partial_doc, "settings")
    assert "  games: 4" in _block_lines(planned_doc, "settings")


# ===========================================================================
# 5. The malformed ledger: more than one document already carrying this run's id.
#
# Carry-forward from Task 3.6 — a condition with code and no test until now.
# ``_splice_own_chunk`` replaces only the FIRST match, so a second document with
# the same id would survive every attempt and step 6's read-back (*exactly one
# chunk carries this run's id*) could never pass. That is MALFORMED, not
# conflicted, and a retry cannot clear it, so ``upsert_record`` routes straight to
# the fallback rather than burning its budget on a condition that cannot change.
#
# It is the exact state a Slice-2 id collision would leave behind — the failure
# the six-hex suffix makes negligible rather than impossible — and it is cheap to
# assert here because this module is already building ledgers by hand.
# ===========================================================================


def test_a_ledger_already_holding_two_documents_for_this_run_is_malformed_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One attempt, no write, and the record parked in the fallback file.

    Three assertions, each for a different failure this guards:

    * **Exactly one splice attempt**, with the FINAL write's ten-attempt budget
      handed in. A duplicate is not a race: the file will look the same on every
      re-read, so nine more attempts are nine more whole-file reads and hashes
      that cannot possibly succeed. Recognising it as malformed is what turns a
      burnt budget into an immediate, named diagnosis.
    * **The ledger is byte-identical afterwards.** With two candidate documents
      carrying this id there is no way to know which one is this run's, so
      writing over either could destroy another run's record — the silent loss
      the collision-proof id exists to prevent. Refusing to write is the only
      safe move.
    * **The record is in the fallback file, not lost.** Step 7's rule holds here
      as everywhere: a ledger update that cannot land must never cost the run the
      games it already played.
    """
    ledger = tmp_path / "ledger.yaml"
    # Two documents, same ``run.id``, differing elsewhere so neither is a
    # de-duplicable copy of the other — exactly what an id collision leaves.
    first = f"---\n{render_record(_foreign_result(_RUN_ID), '2026-09-11')}"
    second = f"---\n{render_record(_foreign_result(_RUN_ID), '2026-09-12')}"
    before = _seed_ledger(ledger, first, second)
    splices = _count_splices(monkeypatch)

    landed = upsert_record(
        _foreign_result(_RUN_ID),
        "2026-09-12",
        ledger_path=ledger,
        attempts=LEDGER_WRITE_ATTEMPTS_FINAL,
    )

    assert len(splices) == 1, (
        "a duplicate cannot be resolved by retrying — the budget must not be burnt"
    )
    assert ledger.read_bytes() == before, "an ambiguous ledger must not be written"

    assert landed != ledger, "the write must report the fallback, not the ledger"
    assert landed == ledger.with_name(f"{ledger.name}.UNWRITTEN-{_RUN_ID}.txt")
    parked = landed.read_text(encoding="utf-8")
    assert parked.startswith("---\n"), "the parked document must carry its separator"
    assert f"  id: '{_RUN_ID}'" in parked


# ===========================================================================
# 6. The real ledger is never reached — the standing guard this file inherits.
# ===========================================================================


@pytest.fixture(autouse=True)
def _the_committed_ledger_is_never_written() -> Iterator[None]:
    """Fail this module's tests loudly if the committed ledger's bytes move.

    Cheap insurance on the one file that cannot be regenerated: an early-bound
    signature default once let ~25 synthetic records into
    ``evals/blunder-ledger.yaml``, and this module drives ``run_eval`` — the
    function whose no-arg write resolves ``LEDGER_PATH``. The scaffold's redirect
    is the real protection; this notices if it ever stops working.
    """
    real = blunder_eval._REPO_ROOT / "evals" / "blunder-ledger.yaml"
    before = real.read_bytes() if real.exists() else None
    yield
    after = real.read_bytes() if real.exists() else None
    assert after == before, "the COMMITTED ledger was modified by a test"
