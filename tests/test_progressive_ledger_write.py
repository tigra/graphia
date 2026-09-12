"""Offline tests for the **progressive** ledger write (spec 043, Slice 3).

Pins the mechanism the whole spec exists for: ``run_eval`` writes its record
after **every completed game** and once more after the loop, each write an
``upsert_record`` OCC cycle that *replaces* this run's own document rather than
adding another — so a run stopped after four of ten games leaves one record
covering exactly those four, because the record was already on disk, not because
anything noticed the stop. Also pinned here: the pre-run backup that must exist
before the first write, and the retry/fallback behaviour that must never kill a
measured run.

NO TEST SENDS A REAL SIGNAL, and that is not a shortcut — it is the design.
There is no signal handler, no ``atexit`` hook and no graceful shutdown anywhere
in spec 043; durability comes entirely from having already written. So "the run
was stopped" is expressed by making the stubbed ``_play_one_game`` raise a
``KeyboardInterrupt`` — a ``BaseException``, so the loop's ``except Exception``
does not catch it and the post-loop write is never reached, which is exactly the
control flow a ``SIGINT`` produces. Technical-considerations §4 states plainly
that surviving a real ``SIGKILL`` or a power cut is **not** covered by the mocked
suite; that is Task 4.4's one manual kill, and this module does not pretend
otherwise.

CONCURRENCY IS TESTED WITHOUT CONCURRENCY. Section 6 injects a mutation between
the OCC cycle's read (step 2) and its re-read (step 4) by wrapping
``_read_ledger_text`` — a second run's document appears in the file at exactly
that instant. No threads, no subprocesses, no timing, and fully deterministic.
Technical-considerations §4 calls this "the test that would have caught the
truncate-to-a-remembered-offset design the first draft proposed", and the reason
it works is that the injection point is a function call rather than a moment.

ASSERTIONS ARE ON **BYTES** WHEREVER THE CLAIM IS "UNCHANGED".
``tests/test_ledger_write.py``'s docstring argues this at length for Slice 1 and
the argument is the same one level up: the write reads the ledger as opaque text
and parses no record, precisely so that hand-written ``notes``, float renderings
and key order in the committed history survive every rewrite. A test comparing
``yaml.safe_load`` output would pass a writer that reformatted all fifty
committed records into canonical YAML — which IS the failure being guarded.
PyYAML appears below only where the claim is genuinely about a *value* ("this
document covers four games"), never where it is about preservation.

WHY A NEW MODULE. The repo files ledger tests by topic —
``test_ledger_write.py`` (Slice 1: the text splitter, its inverse, the durable
replace), ``test_run_identity.py`` (Slice 2: who wrote a record),
``test_ledger_model.py``, ``test_ledger_viewer.py``,
``test_substitution_ledger.py``, ``test_blunder_eval_provenance.py``. This is
Slice 3's topic: *when* a record is written and what a write must not disturb.
``test_blunder_eval.py`` is 2200 lines across six numbered concern sections its
own docstring enumerates, all about the harness's surface; a seventh about the
write cadence would be filed under the wrong heading.

THE SCAFFOLD IS IMPORTED, NOT RE-DECLARED. ``_stub_run_eval_env`` (in
``test_blunder_eval.py``) owns the list of seams ``run_eval`` needs stubbed —
both provenance collectors, the model-name resolver, ``LEDGER_PATH`` and, since
this slice, ``LEDGER_BACKUP_DIR``. Re-typing that list here would let the copies
drift, and a stale copy of a stub list is how a test starts reaching a real
client. ``test_run_identity.py`` imports it for the same reason.

THE COMMITTED LEDGER, THE REAL TRANSCRIPT STORE AND THE REAL ``.graphia/`` ARE
NEVER WRITTEN. ``evals/blunder-ledger.yaml`` is the project's only long-term
measurement history and an early-bound signature default once leaked ~25
synthetic records into it. Every ``run_eval`` call below redirects the ledger and
the backup directory into ``tmp_path`` (via the scaffold) and injects
``transcripts_root=tmp_path/...`` so nothing is created under
``evals/transcripts/``. The backup redirect is the new one and it is easy to
miss: the scaffold's ledger does not exist, so ``_backup_ledger`` takes its
nothing-to-lose skip and the non-skip path was never exercised — until section 3
below pre-creates documents, at which point an unredirected copy lands in the
developer's ``.graphia/ledger-backups``. Section 8 asserts the redirect is still
in place, so it cannot quietly disappear from the shared scaffold.

WHAT IS DELIBERATELY NOT HERE. ``quality.ended_early`` and the
``settings.games``-versus-completed distinguishability pair are **Slice 4**'s and
are pinned in ``tests/test_early_stop_marker.py`` — landed 2026-09-12, after this
module was written, which is why the sentence above used to say they did not
exist yet. That module imports this one's ``_completed_game``,
``_record_every_write``, ``_seed_ledger``, ``_count_splices`` and the two
ledger-reading helpers rather than re-declaring them, so a change to how a write
is observed lands in one place. The write *primitives* — ``_split_ledger``
/ ``_join_ledger`` / ``_atomic_write_text`` and the frozen ``append_record`` —
are ``test_ledger_write.py``'s. ``run.id``'s shape and its single draw per run
are ``test_run_identity.py``'s; only its use as the upsert's match key is
exercised here (section 7). The viewer is Slice 5's.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

# The ``run_eval`` scaffold and its hand-built completed-game capture live with
# the test module that owns them (see the module docstring): re-declaring them
# here would let the copies drift, and a stale copy of a stub list is how a test
# starts reaching a real client. This block sorts above ``graphia`` because a
# sibling test module is not first-party to the package.
from test_blunder_eval import (
    _capture_for_storage,
    _storage_args,
    _stub_run_eval_env,
)

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    _passthrough_chunks,
    _run_id_line_re,
    _split_ledger,
    render_record,
    run_eval,
    upsert_record,
)

# ---------------------------------------------------------------------------
# Pinned ids. The run under test and its two NEAR-TWINS, which exist to prove
# the upsert matches on the whole rendered ``run.id`` scalar and not on a
# prefix: ``_TWIN_SUFFIX`` shares the entire timestamp and differs only in the
# six random characters (the collision ``make_run_id``'s suffix was widened to
# close), and ``_TWIN_LEGACY`` is a suffix-free pre-043 id of which OURS is a
# literal string prefix — the case a marker without the closing quote would
# match.
# ---------------------------------------------------------------------------
_RUN_ID = "2026-09-12T10-00-00-9f3a1c"
_TWIN_SUFFIX = "2026-09-12T10-00-00-aaaaaa"
_TWIN_LEGACY = "2026-09-12T10-00-00"

# The ``run.date`` line inside a rendered ``run`` block — two-space indented and
# single-quoted, as ``_yaml_str`` emits it. Read back off the document rather
# than recomputed with ``date.today()``: Task 3.4 hoisted the sample to once per
# run precisely so ``run.date`` cannot move while a run is alive, and a test that
# re-derived it could differ in a SECOND line when run across midnight.
_RUN_DATE_RE = re.compile(r"^  date: '([^']+)'$", re.MULTILINE)


# ===========================================================================
# Helpers.
# ===========================================================================


def _text(path: Path) -> str:
    """The ledger's text, with a missing file reading as ``''``.

    Bytes-then-decode, never ``read_text``, for the reason
    ``_read_ledger_text`` documents: text mode translates ``\\r\\n``, so a test
    comparing text would silently agree with a writer that rewrote every line of
    a CRLF ledger.
    """
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def _documents(text: str) -> list[str]:
    """The ``---``-separated documents of ``text``, each with its own separator.

    ``_split_ledger``'s element 0 is the preamble and is never a document, so
    this is ``[1:]`` — the same slice ``upsert_record`` takes at step 6.
    """
    return _split_ledger(text)[1:]


def _preamble(text: str) -> str:
    """Everything before the first document separator."""
    return _split_ledger(text)[0]


def _games_completed(document: str) -> int:
    """``quality.games_completed`` of one rendered document.

    PyYAML here and nowhere else in this module: the claim *"this document covers
    four games"* is genuinely about a value, so parsing is the honest way to read
    it. Every "unchanged" claim below is asserted on bytes instead.
    """
    return yaml.safe_load(document)["quality"]["games_completed"]


def _run_date_of(document: str) -> str:
    """The ``run.date`` the run itself stamped, read back off its own document."""
    match = _RUN_DATE_RE.search(document)
    assert match is not None, f"no run.date line in:\n{document}"
    return match.group(1)


def _completed_game(
    on_call: Callable[[int], None] | None = None,
) -> Callable[[argparse.Namespace, int], Any]:
    """A ``_play_one_game`` stub that always COMPLETES, with an optional probe.

    ``on_call`` receives the 0-based ``game_index`` *before* the capture is
    returned, which is how the tests below observe the ledger at a real moment in
    the run — the call for game *n* happens after game *n-1*'s progressive write
    and before game *n*'s.
    """

    def _stub(args: argparse.Namespace, game_index: int) -> Any:
        if on_call is not None:
            on_call(game_index)
        return _capture_for_storage("Cara")

    return _stub


def _record_every_write(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """Tape the ledger's bytes after **every** ledger write the run makes.

    Wraps ``_atomic_write_text`` — the single funnel every ledger write goes
    through (``upsert_record``'s step 5, and ``append_record``'s whole body) —
    calls the real function, then reads the file back. So the returned list has
    one entry per write, in order, holding the file exactly as that write left
    it.

    Deliberately NOT a wrapper around ``upsert_record``: a test that observed the
    writer would go blind the moment somebody changed WHICH writer the loop
    calls, which is precisely the regression these tests exist to catch. The
    backup copy is invisible here on purpose — ``_backup_ledger`` does its own
    ``mkstemp``/``os.replace`` rather than routing through this function, because
    a backup must be byte-exact and this function takes ``str``.
    """
    real = blunder_eval._atomic_write_text
    snapshots: list[bytes] = []

    def _wrapper(path: Path, text: str, **kwargs: object) -> bool:
        # ``**kwargs`` forwards spec 043 Task 3.8's ``verify`` hook rather than
        # swallowing it: a double that dropped it would silently disable the
        # last-instant conflict check for every test in this module.
        landed = real(path, text, **kwargs)
        snapshots.append(path.read_bytes())
        return landed

    monkeypatch.setattr(blunder_eval, "_atomic_write_text", _wrapper)
    return snapshots


def _foreign_result(run_id: str) -> EvalResult:
    """Another run's ``EvalResult`` — something to pre-seed a ledger with."""
    return EvalResult(
        provider="ollama",
        large_model="qwen2.5:3b",
        small_model="qwen2.5:3b",
        games_attempted=3,
        games_completed=3,
        run_id=run_id,
        metrics={"repetition": {"rate": 0.25, "count": 1, "denominator": 4}},
    )


# A hand-written pre-existing document, deliberately NOT canonical output. A
# parse-and-re-render round trip would normalise every one of these: ``0.40`` →
# ``0.4``, the aligned ``count:`` padding, the key order, and the block scalar's
# interior. It also carries a bare ``---`` INSIDE its ``notes`` block (indented,
# so not a separator) and a ``---`` mid-line — the two inputs the splitter's rule
# exists for. If a write ever starts parsing chunks, this is the document that
# says so.
_HAND_WRITTEN_DOCUMENT = f"""---
run:
  date: '2026-08-01'
  id: '{_TWIN_SUFFIX}'
  duration_seconds: 1234.5
  metrics_version: 1
code:
  commit: 'abc1234'
  branch: 'main'
  dirty: false
provider:
  name: 'ollama'
settings:
  games: 10
quality:
  games_attempted: 10
  games_completed: 10
  games_failed_early: 0
metrics:
  repetition:
    rate: 0.40
    count:       4
    denominator: 10
notes: |
  Hand-edited by a maintainer, and every byte of it must survive.
  The rate fell 0.6 --- 0.4 after the prompt change.
  ---
  (that bare separator is INSIDE the note, not a document boundary)
"""

# A leading comment block before the first record. The committed ledger has no
# preamble today, which is exactly why one is exercised here: element 0 of the
# split is the easiest thing for a splice to lose.
_PREAMBLE = (
    "# blunder-ledger.yaml — hand-maintained header\n"
    "# append-only by convention; a run rewrites ONLY its own document\n"
)


def _seed_ledger(ledger: Path, *documents: str) -> bytes:
    """Write a pre-existing ledger (preamble + documents) and return its bytes."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes((_PREAMBLE + "".join(documents)).encode("utf-8"))
    return ledger.read_bytes()


@pytest.fixture
def transcripts_root(tmp_path: Path) -> Path:
    """The injected transcripts root — never ``evals/transcripts/``."""
    return tmp_path / "transcripts"


# ===========================================================================
# 1. The progressive shape (functional-spec §2: "the entry is brought up to
#    date as the run goes").
#
# A ten-game run leaves ONE record, never one per game. This is the criterion a
# naive per-game append satisfies in spirit and fails in fact, so it is asserted
# on the file after EVERY write rather than only at the end.
# ===========================================================================


def test_the_ledger_holds_one_growing_document_after_every_completed_game(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """After game 1: one document, one game. After game 4: the SAME one, four games.

    The whole of functional-spec §2's third requirement in one assertion pair.
    A four-game run makes five writes (one per completed game, plus the post-loop
    one), and the ledger must hold exactly **one** document after every one of
    them, its ``games_completed`` climbing 1 → 2 → 3 → 4 and then standing still
    on the final write, which re-renders the same four games.

    The document counts are the load-bearing half: an implementation that
    appended per game would produce 1, 2, 3, 4 — ten records for a ten-game run,
    and the functional spec's "one entry for that run" broken while every
    per-game number still looked right.

    THE EMPTY PREAMBLE IS ASSERTED HERE TOO (added by spec 043 Task 3.9). This
    is the only test in the module whose ledger does not exist when the run
    starts, which makes it the only one that can see the ``not raw`` half of
    ``upsert_record``'s trailing-newline normalisation. Drop that half and an
    empty ledger — which does not end with a newline, because it has no bytes at
    all — gets one prepended, so the very first write to a fresh ledger begins
    with a blank line. The file still parses and every count above still holds,
    so nothing else notices; the byte simply stays there for the life of the
    file, in the one file the project cannot regenerate.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    writes = _record_every_write(monkeypatch)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    texts = [b.decode("utf-8") for b in writes]
    assert len(texts) == 5, "expected one write per completed game plus the final one"
    assert [len(_documents(t)) for t in texts] == [1, 1, 1, 1, 1], (
        "a run must contribute exactly ONE document, however many times it writes"
    )
    assert [_games_completed(_documents(t)[0]) for t in texts] == [1, 2, 3, 4, 4]
    assert [_preamble(t) for t in texts] == [""] * 5, (
        "a ledger created from nothing must start at its first separator — no "
        "blank line prepended by the trailing-newline normalisation"
    )

    # And the file left on disk is that same single document.
    final = _documents(_text(ledger))
    assert len(final) == 1
    assert _games_completed(final[0]) == 4
    assert f"  id: '{_RUN_ID}'" in final[0]
    assert result.games_completed == 4


def test_every_progressive_record_balances_attempted_against_its_two_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """``games_attempted == games_completed + games_failed_early`` at every write.

    Technical-considerations §2.4's cross-kind invariant, and the second by-eye
    check a reader makes on a record. It holds with no code spent on it *because*
    the write is the last statement of the loop body, after a game has reached a
    terminal outcome — so a write hoisted above the failure branch, or above the
    ``games_completed`` increment, breaks it. §2.4 asks for it to be asserted on
    a partial record rather than left to inspection, so it is asserted on all of
    them: this run's first game fails and its next two complete, which is the
    only shape where the three counts can disagree.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    writes = _record_every_write(monkeypatch)

    def _first_game_fails(args: argparse.Namespace, game_index: int) -> Any:
        if game_index == 0:
            raise RuntimeError("game exploded")
        return _capture_for_storage("Cara")

    monkeypatch.setattr(blunder_eval, "_play_one_game", _first_game_fails)

    run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    for text in (b.decode("utf-8") for b in writes):
        quality = yaml.safe_load(_documents(text)[0])["quality"]
        assert quality["games_attempted"] == (
            quality["games_completed"] + quality["games_failed_early"]
        ), f"the three counts disagree in:\n{text}"


def test_no_progressive_record_disagrees_with_itself_about_how_many_games_it_covers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """``outcomes.games`` equals ``quality.games_completed`` at every single write.

    The other half of the balance check, and the one that pins the write's
    POSITION rather than its guard. The progressive write is the **last**
    statement of the loop body, after every per-game fold — the winners and vote
    marginals, the action metrics, the persona pairs, the run-health counts — and
    a write moved even one statement earlier lands a record whose
    ``games_completed`` counts the game in flight while its scored blocks do not.
    Technical-considerations §2.1 treats exactly that kind of self-disagreement as
    the one visible symptom of a bad write, and ``outcomes`` is where it shows:
    the block is folded from the per-game ``winners`` list, so a hoisted write
    leaves it one game behind for the whole run.

    The partition invariant is asserted alongside it — the ``outcomes`` buckets
    are mutually exclusive over the completed games, which is the check
    ``evals/README.md`` already tells a reader to make by eye.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    writes = _record_every_write(monkeypatch)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    for index, snapshot in enumerate(writes):
        record = yaml.safe_load(_documents(snapshot.decode("utf-8"))[0])
        completed = record["quality"]["games_completed"]
        outcomes = record["outcomes"]
        assert outcomes["games"] == completed, (
            f"write {index}: the record says it covers {completed} game(s) but its "
            f"outcomes block was folded over {outcomes['games']}"
        )
        partition = (
            outcomes["law_abiding"]["wins"]
            + outcomes["mafia"]["wins"]
            + outcomes["runaway"]
            + outcomes["draw"]
            + outcomes["no_winner"]
        )
        assert partition == outcomes["games"], f"write {index}: outcomes do not partition"


def test_a_game_that_failed_early_writes_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """The failed-early branch ``continue``s BEFORE the write, and must keep doing so.

    This is the protection that makes "a run killed before its first game
    finished leaves no record" true with no guard to write and no guard that
    could later be wrong (there is deliberately no ``games_completed == 0``
    check anywhere — such a guard would silently break the all-failed *full* run,
    which must still produce its record).

    Three games, the first of which fails: two completed games means two
    progressive writes plus the final one. A write on the failed-early path would
    make it four, and ``run.seq`` — which counts writes — would say so in the
    record.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    writes = _record_every_write(monkeypatch)

    def _first_game_fails(args: argparse.Namespace, game_index: int) -> Any:
        if game_index == 0:
            raise RuntimeError("game exploded")
        return _capture_for_storage("Cara")

    monkeypatch.setattr(blunder_eval, "_play_one_game", _first_game_fails)

    result = run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    assert result.games_failed_early == 1
    assert result.games_completed == 2
    assert len(writes) == 3, "two completed games write twice, plus the final write"
    assert "  seq: 3" in _documents(_text(ledger))[0]


def test_run_seq_counts_one_write_per_completed_game_plus_the_final_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """``run.seq`` on the landed record is ``games + 1`` for a clean run.

    ``seq`` exists to make a silently rolled-back OCC update nameable after the
    fact, so its arithmetic is worth pinning: it is incremented once per *call*
    to ``upsert_record`` (at step 1, before the single render), never per retry.
    A four-game run therefore lands on 5 — and a record whose ``seq`` is 1 is a
    run that wrote only at the end, which is the defect this slice removes.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    assert result.seq == 5
    document = _documents(_text(ledger))[0]
    assert "  seq: 5" in document
    # Immediately after ``id``, because the two are one thought.
    assert document.index(f"  id: '{_RUN_ID}'") < document.index("  seq: 5")


# ===========================================================================
# 2. The complete run's record, against a one-shot render of the same result.
#
# Task 3.6 case (b), restated 2026-09-12: "byte-identical to the pre-change
# code's" stopped being satisfiable the moment ``run.seq`` landed (a four-game
# run's final record carries ``seq: 5``), and Slice 2's ``run.id`` had already
# made "identical to today's" mean "modulo this spec's additive fields". The
# honest form is stronger because it is checkable: the progressive path's final
# record differs from a ONE-SHOT render of the same result in exactly the
# ``run.seq`` line — same metrics, same counts, same key order, one added line.
# ===========================================================================


def test_the_progressive_final_record_differs_from_a_one_shot_render_in_only_the_seq_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """Progressive and one-shot agree line for line, but for ``run.seq``.

    The regression that protects every existing reader and every committed
    comparison. It fails loudly if the progressive path ever scores *differently*
    from the one-shot path — a re-scoring fold that double-counted, a Wilson band
    attached twice, a duration measured from the wrong origin, a metric that
    vanished on the second pass — because all of those change some other line.

    THE COMPARISON RENDERS WITH THE RUN'S **OWN** ``run_date``, read back off the
    landed document rather than recomputed. Task 3.4 hoisted that sample to once
    per run exactly so ``run.date`` cannot move while a run is alive; a test that
    called ``date.today()`` here would differ in a second line whenever it ran
    across midnight, and would fail for a reason that has nothing to do with what
    it is testing.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    landed = _documents(_text(ledger))[0].removeprefix("---\n")
    run_date = _run_date_of(landed)

    # A one-shot writer (``append_record``, ``persona_bench``) never touches
    # ``seq``, so it stays 0 and ``render_record`` omits the key entirely. Zeroing
    # it on the finished result is what makes this the SAME result rendered two
    # ways rather than two different results.
    assert result.seq == 5
    result.seq = 0
    one_shot = render_record(result, run_date)

    seq_line = "  seq: 5"
    progressive_lines = landed.splitlines()
    assert seq_line in progressive_lines
    assert [ln for ln in progressive_lines if ln != seq_line] == one_shot.splitlines()


# ===========================================================================
# 3. Non-destructive rewrite: every OTHER document is byte-identical, after
#    every write of a whole run.
#
# Technical-considerations §3 names a splicing bug as the one risk atomicity
# cannot cover and re-running cannot repair — an atomic write of wrong content is
# still wrong, and the ledger is the only long-term history the project has.
# ===========================================================================


def test_pre_existing_documents_are_byte_identical_after_every_write_of_a_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """Two pre-existing records survive a four-game run's five rewrites, byte for byte.

    Asserted on **bytes**, and that is the whole point: a parse-and-re-render
    round trip would agree with a writer that reformatted all fifty committed
    records into canonical YAML, so a ``yaml.safe_load`` comparison would pass
    exactly the implementation this guards against. One of the two seeded
    documents is therefore deliberately non-canonical — ``0.40`` rather than
    ``0.4``, padded alignment, a ``notes`` block scalar containing both an
    indented ``---`` and a mid-line one — so a round trip could not possibly come
    back identical.

    The preamble is checked alongside them: it is element 0 of the split, never a
    document, and the easiest thing for a splice to drop.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    rendered = f"---\n{render_record(_foreign_result(_TWIN_LEGACY), '2026-08-02')}"
    before = _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT, rendered)
    original_documents = _documents(before.decode("utf-8"))
    assert len(original_documents) == 2, "the seed must hold two documents"

    writes = _record_every_write(monkeypatch)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    assert len(writes) == 5
    for index, snapshot in enumerate(writes):
        text = snapshot.decode("utf-8")
        assert _preamble(text) == _PREAMBLE, f"the preamble changed at write {index}"
        assert _documents(text)[:2] == original_documents, (
            f"a pre-existing document was rewritten at write {index}"
        )
        assert len(_documents(text)) == 3, (
            f"expected the two seeds plus this run's one document at write {index}"
        )

    # And the run's own document really is the third one, not a fourth seed.
    assert f"  id: '{_RUN_ID}'" in _documents(_text(ledger))[2]


# ===========================================================================
# 4. Provenance is sampled once, before game 1 (functional-spec §2's fourth
#    requirement: "keeping results as the run goes must not cost a clean run its
#    clean record").
# ===========================================================================


def test_collect_code_provenance_is_called_exactly_once_across_a_four_game_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """A COUNTING stub, because asserting the flag's value cannot catch this.

    The first progressive write is itself what dirties the tracked tree — it
    rewrites ``evals/blunder-ledger.yaml``, and the transcript directory under
    ``evals/transcripts/`` is not gitignored either — so a run that re-read git
    would stamp ``code.dirty: true`` from its second game onward on the strength
    of nothing but its own output. The record would accuse its own build of being
    modified and a reader would have no way to tell that libel from a genuinely
    dirty checkout.

    Nothing in the code prevents it except the call's POSITION, above the loop:
    there is no memo, no once-flag and nothing that raises. A value assertion
    cannot protect a position — the suite's mocked provenance reports a clean
    tree, so ``dirty: false`` would hold however many times it was sampled. Only
    the count goes red when the call drifts into the loop.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    calls: list[Path] = []

    def _counting(root: Path) -> dict[str, object]:
        calls.append(root)
        return {"commit": "abc1234", "branch": "main", "dirty": False}

    monkeypatch.setattr(blunder_eval, "collect_code_provenance", _counting)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    assert len(calls) == 1, (
        f"provenance must be sampled once, before game 1; got {len(calls)} samples"
    )
    # Belt-and-braces on the consequence, weak on its own and stated as such.
    assert "  dirty: false" in _documents(_text(ledger))[0]


# ===========================================================================
# 5. Nothing finished, nothing written.
#
# Functional-spec §2: "Given a run stopped before any game has finished, then no
# entry for it has been added — there was nothing finished to record." Expressed
# by STOPPING THE STUB, never by signalling: ``KeyboardInterrupt`` is a
# ``BaseException``, so the loop's ``except Exception`` does not catch it and the
# post-loop write is never reached — the same control flow a ``SIGINT``
# produces, with none of a real signal's flakiness.
# ===========================================================================


def test_a_run_stopped_after_a_failed_first_game_leaves_the_ledger_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """Game 1 raises, game 2 is interrupted: the ledger is untouched, to the byte.

    The two ways a run can produce nothing, composed: a game that fails early
    ``continue``s past the write, and a stop is never observed at all. Together
    they are the criterion — an entry exists only when something *finished*.

    Byte-identity over a pre-existing, non-canonical ledger rather than "the file
    is absent", because the interesting failure is not a stray record but a
    rewrite: a write on the failed-early path would land a ``games_completed: 0``
    record AND re-render the file around it.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    before = _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)

    def _fail_then_stop(args: argparse.Namespace, game_index: int) -> Any:
        if game_index == 0:
            raise RuntimeError("game exploded")
        raise KeyboardInterrupt

    monkeypatch.setattr(blunder_eval, "_play_one_game", _fail_then_stop)

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            config,
            _storage_args(games=10),
            transcripts_root=transcripts_root,
            run_id=_RUN_ID,
        )

    assert ledger.read_bytes() == before
    assert list(tmp_path.glob("*UNWRITTEN*")) == [], "no record should exist anywhere"


def test_a_run_stopped_before_its_first_game_finished_creates_no_ledger_at_all(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """With no ledger to start from, a stopped run leaves none behind.

    The companion to the byte-identity case, over the other starting state: the
    scaffold's ledger does not exist, so "unchanged" means "still absent". This
    is the state a scratch ``--ledger-path`` run begins in, and the one where a
    stray write is most visible.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    assert not ledger.exists()

    def _stop(args: argparse.Namespace, game_index: int) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(blunder_eval, "_play_one_game", _stop)

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            config,
            _storage_args(games=10),
            transcripts_root=transcripts_root,
            run_id=_RUN_ID,
        )

    assert not ledger.exists()
    assert list(tmp_path.glob("*UNWRITTEN*")) == []


# ===========================================================================
# 6. The OCC cycle: concurrency tested without concurrency, plus the one
#    unretryable input it repairs on the way past.
#
# The cycle's interesting behaviour is what happens when the file changes under
# it. There are THREE such windows, not two — the third arrived with spec 043
# Task 3.8 and is section 6b's: between the read (step 2) and the re-read (step
# 4); between step 4's re-read and the rename, which only the ``verify`` hook
# can see; and between the replace (step 5) and the read-back (step 6). Those
# are moments, but each is reachable as a FUNCTION CALL, so a wrapper puts a
# hook exactly there. No threads, no subprocesses, no timing, and the same
# trajectory every run.
# ===========================================================================


# The number of splice attempts an UNCONTESTED write makes. Named rather than
# written as a bare ``1`` at four call sites, because every retry assertion below
# is really "one more than this".
_UNCONTESTED_ATTEMPTS = 1


def _count_splices(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count calls to ``_splice_own_chunk`` — one per OCC attempt, by construction."""
    real = blunder_eval._splice_own_chunk
    calls: list[int] = []

    def _wrapper(text: str, chunk: str, marker: re.Pattern[str]) -> str | None:
        calls.append(len(text))
        return real(text, chunk, marker)

    monkeypatch.setattr(blunder_eval, "_splice_own_chunk", _wrapper)
    return calls


def _interpose(
    monkeypatch: pytest.MonkeyPatch, ledger: Path, intruder: str, *, once: bool
) -> None:
    """Write ``intruder`` into ``ledger`` between the OCC read and the re-read.

    Wraps ``_read_ledger_text``: the hook reads the file, appends another run's
    document to it, and returns the text **as it was** — so the caller splices
    against text that is already stale by the time step 4 re-hashes the file.
    That is a conflict, precisely as a second process replacing the file between
    those two instants would be, and with none of the flakiness.

    ``once=True`` conflicts the first attempt only, so the write is expected to
    retry and succeed; ``once=False`` conflicts every attempt, which is how the
    retry budget is driven to exhaustion.
    """
    real = blunder_eval._read_ledger_text
    fired: list[int] = []

    def _wrapper(path: Path) -> str:
        text = real(path)
        if not once or not fired:
            fired.append(len(text))
            path.write_bytes((text + intruder).encode("utf-8"))
        return text

    monkeypatch.setattr(blunder_eval, "_read_ledger_text", _wrapper)


def test_an_uncontested_write_makes_exactly_one_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for the conflict test: with nothing racing, there is one splice.

    Without this, a retry assertion proves nothing — a writer that always looped
    twice would satisfy it.
    """
    ledger = tmp_path / "ledger.yaml"
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    splices = _count_splices(monkeypatch)

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    assert landed == ledger
    assert len(splices) == _UNCONTESTED_ATTEMPTS


def test_a_ledger_with_no_trailing_newline_is_repaired_rather_than_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The landmine Task 3.2 had to decide, and the decision it made, asserted.

    A ledger whose text does not end with a newline puts the next ``---``
    mid-line, so the splitter — correctly — sees one document where two were
    intended and ``yaml.safe_load_all`` refuses the file. It is **pre-existing**,
    and ``append_record`` deliberately still produces it
    (``tests/test_ledger_write.py::
    test_KNOWN_BAD_appending_to_a_ledger_with_no_trailing_newline_corrupts_it``
    pins that, because byte-identity with the pre-ADR-014 append mode is a
    measured property there).

    For the upsert it could not be left alone: step 6's read-back can never
    succeed on such a file, so a run would burn its **whole** retry budget on its
    FIRST write and divert a perfectly good record to the fallback, for a ledger
    one byte would have repaired. Task 3.2 chose to repair rather than to teach
    the read-back to tell *malformed* from *conflicted*. Hence the two assertions
    that matter: exactly ONE attempt (the condition was repaired, not retried),
    and the repair is CONVERGENT — the second write finds a file that already
    ends with a newline and adds nothing.
    """
    ledger = tmp_path / "ledger.yaml"
    ledger.write_bytes(b"---\nrun:\n  date: 'x'")  # no trailing newline
    splices = _count_splices(monkeypatch)
    result = _foreign_result(_RUN_ID)

    landed = upsert_record(result, "2026-09-12", ledger_path=ledger)

    assert landed == ledger, "the repairable case must not divert to the fallback"
    assert len(splices) == _UNCONTESTED_ATTEMPTS, "a repaired ledger needs no retry"
    text = _text(ledger)
    assert text.startswith("---\nrun:\n  date: 'x'\n---\n"), "one byte, in one place"
    assert len(_documents(text)) == 2
    assert len(list(yaml.safe_load_all(text))) == 2, "the file must parse again"

    upsert_record(result, "2026-09-12", ledger_path=ledger)
    assert len(_documents(_text(ledger))) == 2, "the repair must be convergent"


def test_a_write_conflicted_between_the_read_and_the_replace_retries_and_keeps_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second run's document appears mid-cycle: the write detects it, retries, both survive.

    Technical-considerations §4 calls this "the test that would have caught the
    truncate-to-a-remembered-offset design the first draft proposed" — a writer
    that remembered where its own chunk started and truncated back to it would
    silently delete the intruder, and every other assertion in this module would
    still be green.

    Three things are asserted and all three matter: the cycle made a **second**
    attempt (so the conflict was detected rather than ignored); the intruder's
    document is **byte-identical** to what was written into the file behind the
    writer's back (so the retry re-read rather than re-using its stale text); and
    this run's document appears **exactly once** (so the retry replaced rather
    than appended).
    """
    ledger = tmp_path / "ledger.yaml"
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    intruder = f"---\n{render_record(_foreign_result('2026-09-12T09-59-59-b7c2d1'), '2026-09-12')}"

    splices = _count_splices(monkeypatch)
    _interpose(monkeypatch, ledger, intruder, once=True)

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    assert landed == ledger, "a conflict that resolves must not divert to the fallback"
    assert len(splices) == _UNCONTESTED_ATTEMPTS + 1, "the conflict must cause exactly one retry"

    documents = _documents(_text(ledger))
    assert len(documents) == 3, "the seed, the intruder and this run — all three"
    assert documents[0] == _HAND_WRITTEN_DOCUMENT
    assert documents[1] == intruder, "the intruder's document was not preserved intact"
    assert f"  id: '{_RUN_ID}'" in documents[2]
    assert sum(f"  id: '{_RUN_ID}'" in d for d in documents) == 1


def _clobber_after_the_replace(
    monkeypatch: pytest.MonkeyPatch, replacement: str, *, once: bool
) -> list[int]:
    """Replace the ledger's whole content immediately after a write lands.

    Wraps ``_atomic_write_text``: the real write happens, and then — before the
    caller can read it back — another writer's ``os.replace`` drops our document
    on the floor. This is the LAST of the OCC cycle's three interesting moments
    (section 6's header comment lists them) and the only one step 6 exists for:
    neither step 4 nor Task 3.8's ``verify`` hook can see it, because both have
    already answered by the time it happens. The step-6 comment names it exactly —
    "somebody replaced the file between our replace and our read-back, dropping
    or regressing our chunk".
    """
    real = blunder_eval._atomic_write_text
    fired: list[int] = []

    def _wrapper(path: Path, text: str, **kwargs: object) -> bool:
        # ``**kwargs`` forwards Task 3.8's ``verify`` hook, which runs INSIDE
        # ``real`` and before its rename — so this clobber still lands after the
        # write, which is the moment this double exists to simulate.
        landed = real(path, text, **kwargs)
        if not once or not fired:
            fired.append(len(text))
            path.write_bytes(replacement.encode("utf-8"))
        return landed

    monkeypatch.setattr(blunder_eval, "_atomic_write_text", _wrapper)
    return fired


def test_a_write_clobbered_after_the_replace_is_caught_by_the_read_back_and_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 6 is load-bearing: without it the record is silently lost on a lost race.

    This is the conflict the hash check **cannot** catch, and therefore the only
    thing that makes "with retries" converge rather than merely repeat: the
    competing write lands after step 4 has already re-hashed and after step 5 has
    already replaced, so from the writer's point of view everything succeeded.
    Only reading the file back afterwards and finding this run's document missing
    turns that into a retry.

    Drop step 6 and every other assertion in this module stays green while a
    measured run reports success and leaves no record — the worst shape a failure
    in this design can take, because nothing anywhere says so.
    """
    ledger = tmp_path / "ledger.yaml"
    seed = _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT).decode("utf-8")
    splices = _count_splices(monkeypatch)
    clobbered = _clobber_after_the_replace(monkeypatch, seed, once=True)

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    assert clobbered, "the injection never fired — the test proves nothing"
    assert len(splices) == _UNCONTESTED_ATTEMPTS + 1, "the lost write must be noticed and retried"
    assert landed == ledger

    documents = _documents(_text(ledger))
    assert documents[0] == _HAND_WRITTEN_DOCUMENT
    assert sum(f"  id: '{_RUN_ID}'" in d for d in documents) == 1, (
        "this run's record is missing or duplicated after the retry"
    )


def test_retry_exhaustion_writes_the_fallback_file_and_never_raises_out_of_run_eval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """Step 7: a write that can never land parks the record and lets the run finish.

    A ledger update that killed a measured run would invert the spec's whole
    purpose — the run would lose the games it had already played *because* the
    code meant to preserve them failed. So every attempt is conflicted here, the
    budget is exhausted on both the progressive and the final write, and what
    must happen is: ``run_eval`` returns its scored result normally, and the
    rendered document is on disk in the ``UNWRITTEN`` file beside the ledger,
    complete with its separator so recovery is a ``cat``.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    intruder = f"---\n{render_record(_foreign_result('2026-09-12T09-59-59-b7c2d1'), '2026-09-12')}"
    _interpose(monkeypatch, ledger, intruder, once=False)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    assert result.games_completed == 1, "the measurement itself must be unaffected"

    fallback = ledger.with_name(f"{ledger.name}.UNWRITTEN-{_RUN_ID}.txt")
    assert fallback.exists(), "the record must be parked, not lost"
    parked = fallback.read_text(encoding="utf-8")
    assert parked.startswith("---\n"), "the parked document must carry its separator"
    assert f"  id: '{_RUN_ID}'" in parked
    assert _games_completed(parked) == 1


# ===========================================================================
# 6b. The last-instant check, and the two pass-through checks (spec 043 Task
#     3.8), which are three separate mechanisms with three separate answers.
#
# (a) The CONFLICT window. Step 4's hash check used to be the last look at the
#     file, and it sits OUTSIDE ``_atomic_write_text`` — so the span from it to
#     the rename contained a whole temp-file write plus an ``fsync``. The same
#     check is now also handed in as a ``verify`` hook and re-asked between that
#     ``fsync`` and the rename. A conflict landing in that span was previously
#     invisible until step 6 and cost a wasted replace; it is now caught before
#     the rename and retried. Section 6's ``_interpose`` cannot reach this
#     window — it fires at the read — so the injection below fires at the
#     temp file's ``fsync`` instead, which is the one call inside the window.
#
# (b) Step 3b: the splice must not have moved anybody else's bytes. Both
#     operands come from one read and nothing has been written yet, so the
#     answer is unambiguous — a difference is a BUG in this module, and the
#     write is REFUSED outright rather than retried.
#
# (c) Step 6: the same comparison after the rename, where it is ambiguous — a
#     concurrent run legitimately updating its own record in the microseconds
#     between our rename and our read-back produces it too. So it WARNS and
#     does nothing else. Both alternatives would lie: this run's own record did
#     land, so a fallback file (whose name means *your measurement was not
#     recorded*) would say the opposite of what happened, and a retry would
#     either rewrite identical bytes or end in exactly that fallback.
#
# The trailing-newline test is filed here rather than beside its sibling in
# section 6 because what it guards is (b)'s FALSE POSITIVE, not the repair.
# ===========================================================================


def _count_replaces(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the destination of every ``os.replace`` — i.e. every write that LANDED.

    The observation that separates an abort from a wasted write. A conflict
    caught at step 4 and one caught by the ``verify`` hook are indistinguishable
    from the ledger's final bytes: both retry, both converge. What tells them
    apart is whether the losing attempt renamed anything, and this is the only
    place that can see it.
    """
    real = os.replace
    destinations: list[str] = []

    def _wrapper(src: object, dst: object) -> None:
        destinations.append(str(dst))
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", _wrapper)
    return destinations


def _conflict_during_the_temp_files_fsync(
    monkeypatch: pytest.MonkeyPatch, ledger: Path, intruder: str, *, once: bool
) -> list[int]:
    """Append ``intruder`` to ``ledger`` from INSIDE the temp file's ``fsync``.

    The injection point for window (a), chosen because it is the only function
    call the OCC cycle makes between step 4's hash check and the rename. A
    second run's document appears in the file at exactly the instant this write
    is making its own bytes durable — which is precisely the race the ``verify``
    hook was added to catch, and precisely the one step 4 has already passed.

    ``once=True`` conflicts the first attempt only, so the write retries and
    succeeds; ``once=False`` conflicts every attempt, driving the budget to
    exhaustion. With ``once=False`` the hook keeps firing during the *fallback*
    file's own write too — harmless, because it only ever appends to the
    ledger, which by then is a file this run has given up on.
    """
    real = os.fsync
    fired: list[int] = []

    def _wrapper(fd: int) -> None:
        real(fd)
        if not once or not fired:
            fired.append(fd)
            ledger.write_bytes(ledger.read_bytes() + intruder.encode("utf-8"))

    monkeypatch.setattr(os, "fsync", _wrapper)
    return fired


def _corrupt_a_foreign_chunk(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make ``_splice_own_chunk`` damage a document this run does NOT own.

    Stands in for the class of defect step 3b exists to catch and that nothing
    else in this design can: a wrong boundary, the wrong chunk swapped, a chunk
    dropped. Simulated rather than induced, because the real splice is correct —
    what is under test is the *check*, and a check is only worth having if it
    fires on content it was not the one to produce.

    The edit lands in ``_HAND_WRITTEN_DOCUMENT``'s ``rate: 0.40``, which no
    rendered record in this module carries, so it cannot touch this run's own
    chunk by accident.
    """
    real = blunder_eval._splice_own_chunk
    calls: list[int] = []

    def _wrapper(text: str, chunk: str, marker: re.Pattern[str]) -> str | None:
        calls.append(len(text))
        spliced = real(text, chunk, marker)
        if spliced is None:
            return None
        return spliced.replace("rate: 0.40", "rate: 0.99")

    monkeypatch.setattr(blunder_eval, "_splice_own_chunk", _wrapper)
    return calls


def _append_after_the_replace(
    monkeypatch: pytest.MonkeyPatch, ledger: Path, intruder: str
) -> list[int]:
    """Append ``intruder`` to ``ledger`` once, immediately after a write lands.

    ``_clobber_after_the_replace``'s gentler sibling, and the difference is the
    whole point: that one REPLACES the file, so this run's own chunk disappears
    and step 6's *first* question fails. This one only appends, so the run's own
    record is still there, byte-for-byte — step 6's first question passes and
    its second one (the pass-through comparison) is the only thing that notices
    anything at all. That is the exact shape of a concurrent run updating its
    own record between our rename and our read-back.
    """
    real = blunder_eval._atomic_write_text
    fired: list[int] = []

    def _wrapper(path: Path, text: str, **kwargs: object) -> bool:
        # ``**kwargs`` forwards Task 3.8's ``verify`` hook and the return value
        # is passed through, for the reason ``_record_every_write`` gives: a
        # double that swallowed either would silently disable the last-instant
        # check for every test that installs it.
        landed = real(path, text, **kwargs)
        if landed and path == ledger and not fired:
            fired.append(len(text))
            ledger.write_bytes(ledger.read_bytes() + intruder.encode("utf-8"))
        return landed

    monkeypatch.setattr(blunder_eval, "_atomic_write_text", _wrapper)
    return fired


def test_a_conflict_during_the_temp_files_fsync_aborts_before_the_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Window (a): a writer lands while we are making our temp file durable.

    Step 4 has already said yes and cannot be asked again from where it sits;
    only the ``verify`` hook, asked between the ``fsync`` and the rename, can
    still stop this write. Two assertions carry it. The cycle made a **second**
    attempt, so the conflict was detected at all. And ``os.replace`` ran exactly
    **once** — the losing attempt never renamed anything, which is the
    difference between catching this at the hook and catching it at step 6, and
    the only way to tell from outside that the check moved.

    What the intruder's survival proves is the consequence: hand the write no
    ``verify`` and attempt 1 renames its stale splice over the top, silently
    deleting a record this run never read. Nothing raises, nothing warns, and
    the loss is visible only as a record that used to be in the ledger.
    """
    ledger = tmp_path / "ledger.yaml"
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    intruder = (
        f"---\n{render_record(_foreign_result('2026-09-12T09-59-59-b7c2d1'), '2026-09-12')}"
    )

    splices = _count_splices(monkeypatch)
    replaces = _count_replaces(monkeypatch)
    fired = _conflict_during_the_temp_files_fsync(
        monkeypatch, ledger, intruder, once=True
    )

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    assert fired, "the injection never fired — the test proves nothing"
    assert landed == ledger, "a conflict that resolves must not divert to the fallback"
    assert len(splices) == _UNCONTESTED_ATTEMPTS + 1, (
        "the late conflict must be detected and cause exactly one retry"
    )
    assert len(replaces) == 1, (
        "the aborted attempt must never have reached os.replace — that span is "
        f"the window the verify hook closes; saw renames onto {replaces}"
    )

    documents = _documents(_text(ledger))
    assert len(documents) == 3, "the seed, the intruder and this run — all three"
    assert documents[0] == _HAND_WRITTEN_DOCUMENT
    assert documents[1] == intruder, "the intruder's document was not preserved intact"
    assert sum(f"  id: '{_RUN_ID}'" in d for d in documents) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == [ledger.name], (
        "the aborted attempt must leave no .tmp beside the ledger"
    )


def test_a_late_conflict_on_every_attempt_exhausts_the_budget_into_the_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An abort is a conflict, so it spends the budget and then parks the record.

    The abort route must be wired into the retry loop the same way step 4's
    ``continue`` is — an abort treated as a hard failure would raise out of a
    measured run, and an abort treated as success would return a ledger path
    for a write that never landed. Neither happens: every attempt is conflicted
    at the last instant, the budget runs out, and step 7 does what it does for
    any exhausted budget.

    ``attempts=3`` is passed explicitly rather than left to the progressive
    default so the assertion reads as *the budget*, not as a number that would
    quietly stop meaning anything if the constant changed.
    """
    ledger = tmp_path / "ledger.yaml"
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    intruder = (
        f"---\n{render_record(_foreign_result('2026-09-12T09-59-59-b7c2d1'), '2026-09-12')}"
    )

    splices = _count_splices(monkeypatch)
    replaces = _count_replaces(monkeypatch)
    _conflict_during_the_temp_files_fsync(monkeypatch, ledger, intruder, once=False)

    landed = upsert_record(
        _foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger, attempts=3
    )

    fallback = ledger.with_name(f"{ledger.name}.UNWRITTEN-{_RUN_ID}.txt")
    assert landed == fallback, "an unwinnable race must park the record, not raise"
    assert len(splices) == 3, "every attempt in the budget must have been spent"
    assert [Path(dst).name for dst in replaces] == [fallback.name], (
        "the ledger must never have been renamed onto — only the fallback file"
    )
    assert f"  id: '{_RUN_ID}'" not in _text(ledger)

    parked = fallback.read_text(encoding="utf-8")
    assert parked.startswith("---\n"), "the parked document must carry its separator"
    assert f"  id: '{_RUN_ID}'" in parked
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [ledger.name, fallback.name]
    ), "three aborted attempts must leave no .tmp behind"


def test_a_splice_that_damages_another_runs_document_refuses_to_write_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Step 3b (b): not one byte is written when the splice moved somebody else's.

    ADR 014 §5 names a splicing bug as the one risk atomicity cannot cover —
    wrong content lands just as atomically as right content — and until Task
    3.8 the pre-run backup was the *only* thing standing between such a bug and
    fifty committed records. Step 3b turns it into something the writer refuses
    to commit.

    Refusal, not a retry, and that is the assertion with teeth: the splice runs
    exactly **once**. The comparison's two operands both come from the single
    read at step 2 and nothing has been written yet, so a difference cannot be a
    race — it is deterministic, it will reproduce on every attempt, and spending
    the budget on it would only delay the same fallback. The measurement is not
    lost either way: it is in the ``UNWRITTEN`` file, and the ledger is
    byte-identical to what it was before the call.
    """
    ledger = tmp_path / "ledger.yaml"
    seed = _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    splices = _corrupt_a_foreign_chunk(monkeypatch)

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    fallback = ledger.with_name(f"{ledger.name}.UNWRITTEN-{_RUN_ID}.txt")
    assert landed == fallback
    assert ledger.read_bytes() == seed, (
        "a damaged splice must not reach the ledger at all — not one byte"
    )
    assert len(splices) == _UNCONTESTED_ATTEMPTS, (
        "a splice bug is deterministic; retrying it only delays the same outcome"
    )
    assert f"  id: '{_RUN_ID}'" in fallback.read_text(encoding="utf-8")
    assert "BUG in the splice" in capsys.readouterr().err, (
        "the stderr block is the only thing that tells a human this was code, "
        "not contention"
    )


def test_a_ledger_with_no_trailing_newline_is_not_read_as_a_damaged_splice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 3b's FALSE POSITIVE, and the normalisation that removes it.

    The least obvious interaction in Task 3.8, and the one with the worst
    failure mode. ``_splice_own_chunk`` repairs a ledger that does not end with
    a newline by appending one byte — and that byte lands inside the **last
    chunk**, which on a ledger like this one belongs to somebody else. So a
    pass-through baseline taken from the *unrepaired* text reports that chunk as
    damaged on the very write that repairs it: step 3b refuses, and every first
    write to such a ledger routes to the fallback file. Two correct mechanisms,
    one false accusation. The fix is that ``upsert_record`` normalises the read
    text before both the splice and the baseline, so the two agree about what
    they are comparing.

    Nothing about this is visible from the ledger's final bytes when it works,
    which is why it is asserted on the *absence* of the fallback file and on the
    single splice attempt. Its sibling in section 6 pins the repair itself
    (one attempt, convergent); this pins that the repair is not mistaken for a
    bug — a distinction that only exists because of Task 3.8.
    """
    ledger = tmp_path / "ledger.yaml"
    # The repaired byte must land in a chunk this run does NOT own, which is
    # what makes the false positive reachable: strip the document's final
    # newline and it becomes the file's last chunk, unterminated.
    ledger.write_bytes((_PREAMBLE + _HAND_WRITTEN_DOCUMENT[:-1]).encode("utf-8"))
    splices = _count_splices(monkeypatch)

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    fallback = ledger.with_name(f"{ledger.name}.UNWRITTEN-{_RUN_ID}.txt")
    assert not fallback.exists(), (
        "the one-byte repair was accused of being a splice bug — every first "
        "write to a ledger with no trailing newline now goes to the fallback"
    )
    assert landed == ledger
    assert len(splices) == _UNCONTESTED_ATTEMPTS

    text = _text(ledger)
    documents = _documents(text)
    assert len(documents) == 2
    assert documents[0] == _HAND_WRITTEN_DOCUMENT, (
        "the repaired newline must be the ONLY change to the foreign document"
    )
    assert _preamble(text) == _PREAMBLE
    assert f"  id: '{_RUN_ID}'" in documents[1]
    assert len(list(yaml.safe_load_all(text))) == 2, "the file must parse again"


def test_a_document_appended_after_the_rename_is_warned_about_and_nothing_else(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Step 6 (c): it says so on stderr, and it does nothing — deliberately.

    The write SUCCEEDED. This run's own record is in the ledger, byte-for-byte
    what was rendered, and step 6's first question passes. What changed is a
    chunk this run does not own, appearing between the rename and the read-back,
    and the writer cannot tell a splice bug from a concurrent run updating its
    own record — so both possible *actions* would lie about what happened. A
    fallback file means *your measurement was not recorded*, which is false. A
    retry would rewrite identical bytes and, on the last attempt, produce
    exactly that fallback.

    So the assertions are mostly about what must NOT have happened: one splice
    attempt, no ``UNWRITTEN`` file, no litter, the ledger path returned as
    normal — and the warning on stderr naming the backup a human would recover
    from, which is the only output this condition produces.
    """
    ledger = tmp_path / "ledger.yaml"
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    intruder = (
        f"---\n{render_record(_foreign_result('2026-09-12T09-59-59-b7c2d1'), '2026-09-12')}"
    )

    splices = _count_splices(monkeypatch)
    fired = _append_after_the_replace(monkeypatch, ledger, intruder)

    landed = upsert_record(_foreign_result(_RUN_ID), "2026-09-12", ledger_path=ledger)

    assert fired, "the injection never fired — the test proves nothing"
    assert landed == ledger, (
        "the write succeeded, so the return must say so — a fallback path here "
        "would tell the run its measurement was lost when it was not"
    )
    assert len(splices) == _UNCONTESTED_ATTEMPTS, "a warning is not a retry"
    fallback = ledger.with_name(f"{ledger.name}.UNWRITTEN-{_RUN_ID}.txt")
    assert not fallback.exists(), "warning must not escalate into step 7"
    assert sorted(p.name for p in tmp_path.iterdir()) == [ledger.name]

    err = capsys.readouterr().err
    assert "LEDGER READ-BACK WARNING" in err, "silence here is the failure"
    assert "ledger-backups" in err, "the message must name where the recovery is"

    documents = _documents(_text(ledger))
    assert len(documents) == 3
    assert documents[0] == _HAND_WRITTEN_DOCUMENT
    assert f"  id: '{_RUN_ID}'" in documents[1]
    assert documents[2] == intruder


def test_a_clean_four_game_run_never_warns_about_a_document_it_does_not_own(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control for both pass-through checks: on an ordinary run they are silent.

    A check that fires when nothing is wrong is worse than no check, because the
    message it prints tells a developer to go looking in a pre-run backup for
    damage that is not there — and after the second false alarm nobody reads it
    again. So an ordinary run is driven across the shapes most likely to trip a
    boundary comparison: a preamble (element 0 of every split, and the easiest
    thing for a splice to lose), a foreign document that must pass through, and
    **five** writes — four progressive plus the final one — each of which
    re-takes the baseline and re-compares it twice.

    Asserted on stderr rather than on a spy, because the warning's whole purpose
    is to reach a human, and a spy would keep passing if the print were removed.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    err = capsys.readouterr().err
    assert "LEDGER READ-BACK WARNING" not in err, (
        "step 6's pass-through check fired on a run where nothing moved"
    )
    assert "LEDGER WRITE FAILED" not in err, (
        "step 3b refused a correct splice, or a write was diverted to the fallback"
    )

    documents = _documents(_text(ledger))
    assert len(documents) == 2, "one pre-existing document and this run's one record"
    assert documents[0] == _HAND_WRITTEN_DOCUMENT
    assert _games_completed(documents[1]) == 4


# ===========================================================================
# 6c. ``_passthrough_chunks`` as a pure function.
#
# The predicate both checks above are built from, tested directly because the
# way it fails is by being too PERMISSIVE — and a too-permissive comparison
# makes both of its callers into no-ops that stay green forever. A version that
# dropped the preamble, or that excluded one chunk too many, would let exactly
# the corruption step 3b exists to refuse straight through.
#
# It sits in this module rather than with the other text helpers in
# ``test_ledger_write.py`` because its contract is stated in terms of a RUN's
# own marker — the near-twin case below is the same distinction section 7
# exists for, and the ids it turns on are pinned at the top of this file.
# ===========================================================================


def test_passthrough_chunks_keeps_the_preamble_and_every_document_but_this_runs(
    tmp_path: Path,
) -> None:
    """Everything a splice must carry through untouched, in file order, preamble first.

    The four claims that make it usable as a corruption check, and the degenerate
    shapes that must not raise:

    - **the preamble is element 0, always.** A splice has no business touching
      it either, and index 0 is where a boundary error that swallowed the first
      separator would show up. Dropping it is the most natural-looking
      simplification available to a later edit, and it would silently stop
      protecting the one part of the file no run owns.
    - **this run's own chunk is excluded** — it is the one thing the splice is
      *supposed* to rewrite, so including it would make the comparison fail on
      every correct write.
    - **a near-twin id is kept.** ``_TWIN_SUFFIX`` shares the whole timestamp
      and differs only in the six random characters; it is somebody else's
      record and must be protected as one. This is the marker's exactness seen
      from the other side of section 7.
    - **empty, preamble-only and only-our-own-document** all return a list
      rather than raising: the first write of a run meets the first two, and the
      third is the second write of a run into a ledger it is alone in.
    """
    marker = _run_id_line_re(_RUN_ID)
    ours = f"---\n{render_record(_foreign_result(_RUN_ID), '2026-09-12')}"
    twin = f"---\n{render_record(_foreign_result(_TWIN_SUFFIX), '2026-09-12')}"

    assert _passthrough_chunks(_PREAMBLE + twin + ours, marker) == [_PREAMBLE, twin], (
        "the preamble leads the list and the near-twin is somebody else's record"
    )
    assert _passthrough_chunks(_PREAMBLE + ours + twin, marker) == [_PREAMBLE, twin], (
        "file order is preserved regardless of where this run's chunk sits"
    )
    assert _passthrough_chunks("", marker) == [""], "an absent ledger reads as empty"
    assert _passthrough_chunks(_PREAMBLE, marker) == [_PREAMBLE], (
        "a ledger with a header and no records yet is all preamble"
    )
    assert _passthrough_chunks(ours, marker) == [""], (
        "a ledger holding only this run's own document has nothing to protect"
    )

    # And the claim the other five rest on: what comes back is the ledger's own
    # bytes, never a re-rendering of them. Asserted against the hand-written
    # document, because that is the one whose float renderings, key order and
    # block scalar a parse-and-re-render round trip would silently normalise.
    seeded = _seed_ledger(tmp_path / "ledger.yaml", _HAND_WRITTEN_DOCUMENT, ours)
    assert _passthrough_chunks(seeded.decode("utf-8"), marker) == [
        _PREAMBLE,
        _HAND_WRITTEN_DOCUMENT,
    ]


# ===========================================================================
# 7. The run finds its own document among near-twins.
#
# The test that earns the collision-proof id (technical-considerations §2.2). The
# upsert matches on the rendered ``run.id`` SCALAR — quotes included — so an id
# that shares the whole timestamp, and a legacy suffix-free id of which ours is a
# literal prefix, are both somebody else's document.
# ===========================================================================


@pytest.mark.parametrize(
    "twin_id",
    [
        pytest.param(_TWIN_SUFFIX, id="same-second-different-suffix"),
        pytest.param(_TWIN_LEGACY, id="legacy-suffix-free-prefix-of-ours"),
    ],
)
def test_a_run_updates_its_own_document_and_leaves_a_near_twin_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    twin_id: str,
) -> None:
    """The twin's record is byte-identical afterwards; ours is the only one rewritten.

    The consequence being guarded is silent, which is why it is worth a test of
    its own: the splice replaces *the chunk carrying this run's id*, and step 6's
    read-back then finds the run's own chunk and passes. A run that matched a
    twin would overwrite that twin's record with no exception, no fallback file
    and nothing in either run's output mentioning it — the loss visible only as a
    record that used to be in the ledger and no longer is.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    twin = f"---\n{render_record(_foreign_result(twin_id), '2026-09-12')}"
    _seed_ledger(ledger, twin)

    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    documents = _documents(_text(ledger))
    assert len(documents) == 2, "the run must add one document, not replace the twin"
    assert documents[0] == twin, f"the near-twin {twin_id} was rewritten"
    assert f"  id: '{_RUN_ID}'" in documents[1]
    assert _games_completed(documents[1]) == 2


# ===========================================================================
# 8. The pre-run backup (technical-considerations §2.2b).
#
# Its job is to survive a LOGIC bug in the chunk splice, which atomicity cannot
# protect against — an atomic write of wrong content is still wrong. So it must
# exist before the first write, and a location that cannot take it must stop the
# run before a single token is spent.
# ===========================================================================


def test_the_shared_scaffold_redirects_the_pre_run_backup_away_from_dot_graphia(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_stub_run_eval_env`` must point ``LEDGER_BACKUP_DIR`` inside ``tmp_path``.

    A guard on the suite itself, not on the harness. Every other test in this
    module pre-creates a ledger, which makes ``_backup_ledger`` take its real
    copy path; without the redirect that copy lands in the developer's
    ``.graphia/ledger-backups`` and accumulates one file per test run, silently,
    outside the repo's own cleanup. The redirect lives in the shared scaffold so
    it cannot be forgotten by a future test — and this asserts it is still there,
    because a defence nothing checks is a defence that gets refactored away.
    """
    _config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)

    assert blunder_eval.LEDGER_BACKUP_DIR.is_relative_to(tmp_path)
    assert ledger.is_relative_to(tmp_path)


def test_the_backup_exists_before_the_first_game_and_holds_the_pre_run_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """The copy is taken before game 1, hence before the first write, by construction.

    Checked at the earliest observable moment inside the run — the first
    ``_play_one_game`` call — because "before the first write" is not the
    requirement ``_backup_ledger``'s call site actually meets: it is above the
    games loop, so the copy predates the first game rather than merely the first
    write. That matters at the scale this spec is about, where the first write is
    thirty-three minutes into a five-and-a-half-hour run and discovering an
    unwritable backup then would waste exactly the work the spec exists to save.

    Three things are asserted at that moment: a backup exists, its bytes are the
    ledger's PRE-RUN bytes, and the ledger itself is still untouched. The third
    is what makes the first two mean "before the first write" rather than "at
    some point".
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    before = _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)
    observed: list[tuple[list[bytes], bytes]] = []

    def _observe(game_index: int) -> None:
        backups = sorted(blunder_eval.LEDGER_BACKUP_DIR.glob("*"))
        observed.append(([p.read_bytes() for p in backups], ledger.read_bytes()))

    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game(_observe))

    run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    backups_at_game_1, ledger_at_game_1 = observed[0]
    assert backups_at_game_1 == [before], "no pre-run copy of the ledger existed"
    assert ledger_at_game_1 == before, "the ledger was written before it was copied"

    # The copy is named after the run that took it, and is never rewritten.
    copies = sorted(blunder_eval.LEDGER_BACKUP_DIR.glob("*"))
    assert [p.name for p in copies] == [f"ledger.{_RUN_ID}.pre-run.yaml"]
    assert copies[0].read_bytes() == before
    assert ledger.read_bytes() != before, "the run must have written by now"


def test_an_unwritable_backup_location_fails_the_run_before_any_game_is_played(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """The run refuses to start, plays nothing, and leaves the ledger byte-identical.

    A precondition whose absence makes the run's output unrecoverable stops the
    run before a single token is spent — the same posture, and the same
    ``SystemExit`` mechanism, as the diaries-arm check. ``SystemExit`` rather than
    a module exception specifically so that neither the per-game
    ``except Exception`` inside the loop nor a caller's broad handler can swallow
    it, and the deliberate contrast with ``upsert_record``, which never raises at
    all: that is a write of a measurement already made, where failing the run
    would destroy the very games the spec preserves; this is a check made before
    anything has been measured.

    The unwritable location is a directory path whose PARENT is a regular file,
    so ``mkdir(parents=True)`` raises ``NotADirectoryError`` — an ``OSError``,
    which is the class ``_backup_ledger`` converts into the refusal. Anything
    that is not an ``OSError`` is re-raised unchanged, deliberately.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    before = _seed_ledger(ledger, _HAND_WRITTEN_DOCUMENT)

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file, so nothing can be created under it\n", "utf-8")
    monkeypatch.setattr(blunder_eval, "LEDGER_BACKUP_DIR", blocker / "backups")

    played: list[int] = []
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game(played.append))

    with pytest.raises(SystemExit) as excinfo:
        run_eval(
            config,
            _storage_args(games=3),
            transcripts_root=transcripts_root,
            run_id=_RUN_ID,
        )

    assert played == [], "the run must refuse BEFORE it plays anything"
    assert ledger.read_bytes() == before, "the ledger must not have been touched"
    assert not transcripts_root.exists(), "no transcript should have been written"
    assert "backup" in str(excinfo.value), "the refusal must say what failed"


def test_a_ledger_that_does_not_exist_yet_is_not_backed_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
) -> None:
    """The one skip rule: nothing to lose, nothing to copy — and the run proceeds.

    The state every scratch ``--ledger-path`` run and (until this slice) every
    test in the suite begins in. It is the skip that needs no knowledge of the
    repo at all: a ledger that does not exist cannot be damaged by a splice,
    because the run's first write is what creates it. The alternative §2.2b
    offered — skipping when the path is "outside the repo" — was declined for
    having an inverted failure mode inside a git worktree, and this test is the
    positive half of that decision.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    assert not ledger.exists()
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    assert result.games_completed == 1
    assert not blunder_eval.LEDGER_BACKUP_DIR.exists(), (
        "a run with nothing to lose must not create a backup directory"
    )
    assert len(_documents(_text(ledger))) == 1


# ===========================================================================
# 9. The real ledger is never reached — the standing guard this file inherits.
# ===========================================================================


@pytest.fixture(autouse=True)
def _the_committed_ledger_is_never_written() -> Iterator[None]:
    """Fail this module's tests loudly if the committed ledger's bytes move.

    Cheap insurance on the one file that cannot be regenerated: an early-bound
    signature default once let ~25 synthetic records into
    ``evals/blunder-ledger.yaml``, and this module drives ``run_eval`` — the
    function whose no-arg write resolves ``LEDGER_PATH`` — more than any other.
    The scaffold's redirect is the real protection; this notices if it ever stops
    working.
    """
    real = blunder_eval._REPO_ROOT / "evals" / "blunder-ledger.yaml"
    before = real.read_bytes() if real.exists() else None
    yield
    after = real.read_bytes() if real.exists() else None
    assert after == before, "the COMMITTED ledger was modified by a test"
