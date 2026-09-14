"""Offline tests for the finalise-cost instrument (spec 043, Slice 7, Task 7.1).

Pins the measurement technical-considerations §2.8 refuses to leave as an
estimate. Since Task 3.4 the scoring pass (``_finalise_result``) runs after
**every** completed game instead of once after the loop, and one term inside it
— ``score_repetition``, pairwise near-duplicate detection over *all* pooled AI
lines — is quadratic in the pool. Calling it once per game therefore makes a
run's total scoring work grow as the **sum of squares** rather than as one
square. §2.8 predicts that this is seconds against a 40-minute run and demands
the real number; ``_FinaliseCost`` is the instrument that produces it, and this
module is what keeps the instrument honest.

WHAT IS BEING PROTECTED, AND WHY IT NEEDS TESTS AT ALL. The instrument's whole
output is six numbers printed to stderr, so nothing downstream fails if it
drifts — a wrong figure simply gets written into §2.8 and believed. Three
properties carry the weight:

* **Cadence.** One line per pass, and a pass happens after every *completed*
  game plus once after the loop — so an *N*-game run prints *N+1* lines. A
  missing line silently understates the total; a duplicated one overstates it.
* **The pool size beside the cost.** Cost alone shows growth; cost beside the
  size of the input that drives the quadratic term shows *why*, and lets the
  sum-of-squares model be checked rather than asserted. So ``pool_lines`` must
  be the length of the list actually handed to ``score_repetition``, not a
  plausible neighbour of it.
* **Nothing reaches the record.** Task 7.1 says explicitly: not a new ledger
  field. Section 6 asserts that on **bytes**, by running the same games twice —
  once instrumented, once with the instrument replaced by a silent double — and
  comparing the documents.

THE FORMAT IS PINNED BY REGEX, deliberately. The figures are read off a
captured 40-minute run's log with ``grep``, by a human and by whatever one-liner
they write at the time, so the field names, their order and the number of
decimal places are part of the deliverable rather than an implementation
detail. ``_PASS_RE`` / ``_TOTAL_RE`` below are that contract: a reworded field
or a dropped decimal turns them red instead of quietly breaking a parse someone
runs once, three weeks from now, against a run they cannot repeat.

ASSERTED ON STDERR, NOT ON A SPY. The instrument exists to reach a human
reading a log; a spy on ``record_pass`` would keep passing if the ``print`` were
deleted, which is the one regression that matters here. This follows
``test_progressive_ledger_write.py``'s own reasoning for its read-back warning.

NO TEST REACHES A MODEL OR THE REAL LEDGER. ``_stub_run_eval_env`` (imported,
never re-declared) redirects ``LEDGER_PATH`` and ``LEDGER_BACKUP_DIR`` into
``tmp_path`` and stubs both provenance collectors; every ``run_eval`` call below
also injects ``transcripts_root=tmp_path/...`` so nothing is created under
``evals/transcripts/``. ``_play_one_game`` is stubbed in every case, so no graph
runs and no provider is constructed.

WHAT IS DELIBERATELY NOT HERE. *When* a record is written and what a write must
not disturb are ``test_progressive_ledger_write.py``'s (Slice 3);
``quality.ended_early`` is ``test_early_stop_marker.py``'s (Slice 4); the write
primitives are ``test_ledger_write.py``'s (Slice 1). This module owns exactly
one topic: what the cost instrument reports, and that it reports it nowhere but
stderr. The *measured figures themselves* are not here either — they come from
the real ten-game run Task 7.1's second half makes, and land in §2.8.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import time
from pathlib import Path
from typing import Any, NamedTuple

import pytest

# The ``run_eval`` scaffold, its hand-built completed-game capture and the
# every-write tape live with the test modules that own them (see the module
# docstring): re-declaring them here would let the copies drift, and a stale
# copy of a stub list is how a test starts reaching a real client. These blocks
# sort above ``graphia`` because a sibling test module is not first-party to the
# package.
from test_blunder_eval import (
    _capture_for_storage,
    _storage_args,
    _stub_run_eval_env,
)
from test_progressive_ledger_write import _record_every_write

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import _FinaliseCost, _percent_of_run, run_eval

_RUN_ID = "2026-09-14T08-00-00-4c1e77"

# How long the deliberately-slow scorer in section 3 blocks for. Comfortably
# above the reported figure's 1 ms resolution and above ordinary scheduler
# jitter, and small enough that three passes cost the suite 0.15 s.
_SLOW_SCORER_SECONDS = 0.05


# ===========================================================================
# The output contract, as regexes.
#
# Two prefixes, and neither is a prefix of the other — ``finalise-cost:`` picks
# out the per-pass lines alone, ``finalise-cost TOTAL:`` the summary block
# alone. Both are anchored end-to-end so an EXTRA field is a failure too: a
# silently-added column is exactly what breaks a positional parse written
# against a run nobody can repeat.
# ===========================================================================

_PASS_RE = re.compile(
    r"^  finalise-cost: pass=(?P<pass_no>\d+) game=(?P<game>\d+)"
    r" final=(?P<final>yes|no) finalise_s=(?P<finalise_s>\d+\.\d{3})"
    r" pool_lines=(?P<pool_lines>\d+)"
    r" finalise_total_s=(?P<total_s>\d+\.\d{3})$"
)

_TOTAL_RE = re.compile(
    r"^  finalise-cost TOTAL: passes=(?P<passes>\d+) writes=(?P<writes>\d+)"
    r" finalise_s=(?P<finalise_s>\d+\.\d{3}) write_s=(?P<write_s>\d+\.\d{3})"
    r" run_s=(?P<run_s>n/a|\d+\.\d{3})"
    r" finalise_pct=(?P<finalise_pct>n/a|\d+\.\d{3})"
    r" write_pct=(?P<write_pct>n/a|\d+\.\d{3})"
    r" pool_lines=(?P<pool_lines>\d+)$"
)


class _Pass(NamedTuple):
    """One parsed per-pass line."""

    pass_no: int
    game: int
    final: bool
    finalise_s: float
    pool_lines: int
    total_s: float


class _Total(NamedTuple):
    """The parsed summary line (the block's first line — the one with figures)."""

    passes: int
    writes: int
    finalise_s: float
    write_s: float
    run_s: str
    finalise_pct: str
    write_pct: str
    pool_lines: int


def _passes(err: str) -> list[_Pass]:
    """Every per-pass line in ``err``, in the order printed.

    Every line carrying the per-pass prefix must MATCH — a line that carries the
    prefix and fails the regex is a format break, and skipping it would let this
    helper report "no passes" where the truth is "the format moved".
    """
    parsed: list[_Pass] = []
    for line in err.splitlines():
        if "finalise-cost:" not in line:
            continue
        match = _PASS_RE.match(line)
        assert match is not None, f"per-pass line does not match the contract: {line!r}"
        parsed.append(
            _Pass(
                pass_no=int(match["pass_no"]),
                game=int(match["game"]),
                final=match["final"] == "yes",
                finalise_s=float(match["finalise_s"]),
                pool_lines=int(match["pool_lines"]),
                total_s=float(match["total_s"]),
            )
        )
    return parsed


def _totals(err: str) -> list[_Total]:
    """Every summary FIGURE line in ``err`` (the block's prose line is skipped)."""
    parsed: list[_Total] = []
    for line in err.splitlines():
        if "finalise-cost TOTAL:" not in line or " passes=" not in line:
            continue
        match = _TOTAL_RE.match(line)
        assert match is not None, f"summary line does not match the contract: {line!r}"
        parsed.append(
            _Total(
                passes=int(match["passes"]),
                writes=int(match["writes"]),
                finalise_s=float(match["finalise_s"]),
                write_s=float(match["write_s"]),
                run_s=match["run_s"],
                finalise_pct=match["finalise_pct"],
                write_pct=match["write_pct"],
                pool_lines=int(match["pool_lines"]),
            )
        )
    return parsed


def _one_total(err: str) -> _Total:
    """The run's single summary line — asserting there is exactly one."""
    totals = _totals(err)
    assert len(totals) == 1, f"expected exactly one summary line, got {len(totals)}"
    return totals[0]


# ===========================================================================
# Game-capture builders.
# ===========================================================================


def _completed_game(
    lines_per_game: int = 0,
) -> Any:
    """A ``_play_one_game`` stub that always completes, with ``n`` AI lines.

    ``lines_per_game`` grows the pooled speech list the quadratic term is paid
    over, which is what section 3 needs. The lines are unique per game *and* per
    index so none of them clusters with another — the pool's SIZE is what is
    under test here, not the repetition rate it produces.

    ``ai_names`` is left as the base capture's, so ``score_repetition``'s
    name-masking has real names to mask; no line contains one, so the
    third-person measure stays at zero and cannot become a reason the pool
    length differs from what is reported.
    """

    def _stub(args: argparse.Namespace, game_index: int) -> Any:
        base = _capture_for_storage("Cara")
        if lines_per_game == 0:
            return base
        lines = [
            f"a unique remark number {index} from batch {game_index}"
            for index in range(lines_per_game)
        ]
        return dataclasses.replace(
            base,
            ai_lines=lines,
            ai_lines_with_speakers=[("Don", line) for line in lines],
        )

    return _stub


def _failing_game(*, exc: BaseException) -> Any:
    """A ``_play_one_game`` stub that always raises ``exc``."""

    def _stub(args: argparse.Namespace, game_index: int) -> Any:
        raise exc

    return _stub


class _FrozenClock:
    """A stand-in for ``blunder_eval``'s ``time`` module that never advances.

    ``blunder_eval`` reads exactly one name off ``time`` (``monotonic`` — there
    is no ``time.sleep`` and no ``time.time`` in the module), so replacing the
    whole binding is complete rather than partial, and it is scoped to this one
    module instead of patching the stdlib for the whole process.

    Freezing the clock makes ``run.duration_seconds`` exactly ``0.0``, which is
    the only way to reach ``_percent_of_run``'s zero-denominator guard from a
    real ``run_eval`` call deterministically.
    """

    @staticmethod
    def monotonic() -> float:
        return 1_000.0


@pytest.fixture
def transcripts_root(tmp_path: Path) -> Path:
    """The injected transcripts root — never ``evals/transcripts/``."""
    return tmp_path / "transcripts"


# ===========================================================================
# 0. The rendering, on exact numbers.
#
# The only deterministic place to pin the FORMAT and the ARITHMETIC together:
# a mocked run's timings are whatever the machine did, so an integration test
# can assert that a figure is present and self-consistent but never that it
# equals a particular string. These three call the instrument directly with
# the numbers a real ten-game Nova run is expected to produce, so the sample
# in :class:`_FinaliseCost`'s banner — the one a human will grep against — is
# executable rather than aspirational.
# ===========================================================================


def test_the_per_pass_line_renders_the_pinned_fields_in_the_pinned_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two passes, booked by hand: every field, to three decimals, in order.

    Three decimals on ``finalise_s`` is a deliberate floor, not a default: the
    per-pass figure on the FIRST game of a run is the smallest number in the
    whole measurement (the pool is at its smallest), and truncating it to two
    would report the start of the growth curve as ``0.4`` twice and lose the
    slope §2.8 is asking about.

    ``finalise_total_s`` is the running sum, so the second line carries both
    passes: a log truncated anywhere still yields a usable total from its last
    surviving line, which matters most for the runs that get killed.
    """
    cost = _FinaliseCost()

    cost.record_pass(game=3, final=False, elapsed=0.4123, pool_lines=271)
    cost.record_pass(game=3, final=True, elapsed=0.4951, pool_lines=271)

    assert _passes(capsys.readouterr().err) == [
        _Pass(pass_no=1, game=3, final=False, finalise_s=0.412, pool_lines=271,
              total_s=0.412),
        _Pass(pass_no=2, game=3, final=True, finalise_s=0.495, pool_lines=271,
              total_s=0.907),
    ]


def test_the_summary_line_renders_the_pinned_fields_in_the_pinned_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole summary, on the figures a real ten-game Nova run should give.

    ``finalise_s`` and ``write_s`` are reported SEPARATELY and never as a sum,
    and this is the test that holds them apart. The first §2.8 datum measured
    "one finalise pass + one ledger write" as a single 4.102 s blob, which
    cannot answer the only question Task 7.2 has to decide — whether the
    per-game overhead is the scoring or the writing. Here the two figures
    differ by two orders of magnitude, which is the answer if the numbers come
    out as predicted, and a single field could not express it.

    The percentages are the derived figures, so they are pinned against a
    denominator with more precision than they print: ``100 * 4.8123 /
    2410.1173`` is ``0.19967…``, and a reader who re-derives ``0.200`` from the
    printed ``4.812`` and ``2410.117`` must land on the same string.
    """
    cost = _FinaliseCost(
        passes=11,
        writes=11,
        finalise_seconds=4.8123,
        write_seconds=0.0412,
        pool_lines=902,
    )

    cost.report(2410.1173)

    assert _one_total(capsys.readouterr().err) == _Total(
        passes=11,
        writes=11,
        finalise_s=4.812,
        write_s=0.041,
        run_s="2410.117",
        finalise_pct="0.200",
        write_pct="0.002",
        pool_lines=902,
    )


def test_an_account_that_booked_no_pass_reports_nothing_at_all(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``passes == 0`` prints NOTHING — silence, not a block of zeroes.

    A ``passes=0 finalise_s=0.000 finalise_pct=0.000`` block would read as a
    measurement showing re-scoring is free, when what happened is that no
    re-scoring was observed. Absent, never a misleading zero — the same rule
    the ledger record follows for a metric with no denominator.
    """
    _FinaliseCost().report(2410.117)

    assert capsys.readouterr().err == ""


# ===========================================================================
# 1. Cadence: one line per pass, and a pass per completed game plus one.
# ===========================================================================


def test_a_four_game_run_prints_one_pass_line_per_game_plus_one_final_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Five lines for four games: four progressive passes, then the final one.

    The cadence *is* the measurement. A run's total re-scoring cost is the sum
    over passes, so a line lost or doubled misstates the one number §2.8 is
    waiting for — and because the instrument's only consumer is a human reading
    a log, nothing else would notice.

    ``pass`` counts 1→5 with no gap, ``game`` tracks the game each pass
    followed, and ``final`` is ``no`` for every in-loop pass and ``yes`` exactly
    once. The last pair — game 4 twice — is not a duplicate: the post-loop pass
    re-scores the same four games, which is precisely why its cost belongs in
    the total.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    lines = _passes(capsys.readouterr().err)
    assert len(lines) == 5, "one pass per completed game, plus the post-loop one"
    assert [row.pass_no for row in lines] == [1, 2, 3, 4, 5]
    assert [row.game for row in lines] == [1, 2, 3, 4, 4]
    assert [row.final for row in lines] == [False, False, False, False, True]


def test_a_game_that_fails_early_makes_no_pass_so_pass_and_game_diverge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Game 1 fails: the passes are for games 2 and 3, then the final one.

    The failed-early branch ``continue``s before the progressive write, so it
    makes no pass and pays no scoring cost — and from that point the pass number
    and the game number disagree permanently. Both fields exist for exactly this
    reason: with only one of them, a reader correlating a slow pass with its
    game (or with its ``game-NN.txt`` transcript) would be off by the number of
    failures, silently.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)

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

    lines = _passes(capsys.readouterr().err)
    assert [(row.pass_no, row.game, row.final) for row in lines] == [
        (1, 2, False),
        (2, 3, False),
        (3, 3, True),
    ]


# ===========================================================================
# 2. The summary block: once, at the end, and agreeing with the lines above it.
# ===========================================================================


def test_the_summary_block_is_printed_exactly_once_and_after_every_pass_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One summary, last — a totals line printed per game would read as a total.

    Both assertions have teeth. *Exactly once* is what stops the block drifting
    into the loop, where each copy would be a true statement about a prefix and
    a false one about the run. *After every per-pass line* is what makes the
    block a summary rather than a prediction: on a stopped run the per-pass
    lines are all a log has, and their position relative to the totals is how a
    reader knows whether the totals cover them.

    The block is two lines and both carry the ``finalise-cost TOTAL:`` prefix,
    so ``grep`` on either prefix returns a complete set — the figure line and
    the line explaining what the figures mean.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    err = capsys.readouterr().err
    total = _one_total(err)
    assert total.passes == 4

    prefixed = [line for line in err.splitlines() if "finalise-cost TOTAL:" in line]
    assert len(prefixed) == 2, "the block is a figure line plus its legend"

    lines = err.splitlines()
    last_pass = max(i for i, line in enumerate(lines) if "finalise-cost:" in line)
    first_total = min(
        i for i, line in enumerate(lines) if "finalise-cost TOTAL:" in line
    )
    assert last_pass < first_total, "the summary must follow every pass it sums"


def test_the_summary_totals_agree_with_the_per_pass_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``passes``, ``finalise_s`` and ``pool_lines`` are the lines' own numbers.

    The two halves of the output must not be able to disagree — a summary
    derived from a second set of accumulators is a second chance to be wrong,
    and the wrongness would be invisible because nobody adds up eleven lines by
    hand to check. So the summary's ``finalise_s`` is asserted to be the last
    line's running ``finalise_total_s`` (same Σ, printed twice), its ``passes``
    the number of lines, and its ``pool_lines`` the final pool.

    ``writes == passes`` on a clean run is the *arithmetic* of the design rather
    than a coincidence: each completed game finalises then writes, and the
    post-loop pair does the same. Section 5 shows the two parting company when
    games fail.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval, "_play_one_game", _completed_game(lines_per_game=2)
    )

    run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    err = capsys.readouterr().err
    lines = _passes(err)
    total = _one_total(err)

    assert total.passes == len(lines) == 4
    assert total.writes == 4
    assert total.finalise_s == lines[-1].total_s, (
        "the summary's Σ must be the running Σ the last line already printed"
    )
    assert total.pool_lines == lines[-1].pool_lines == 6


def test_every_ledger_write_the_run_makes_is_booked_in_the_write_total(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``writes`` counts the real writes — both ``upsert_record`` sites, not one.

    The write is timed at its two call sites rather than inside the closure, so
    unlike the pass it *can* be forgotten: a run makes progressive writes in the
    loop and one final write after it, and instrumenting only the loop would
    report a write total short by one while every other figure stayed plausible.

    Counted against ``_atomic_write_text`` — the single funnel every ledger write
    goes through — rather than against the number of games, so the claim is
    "every write that happened was booked" and not "the number I expected was
    booked".
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    snapshots = _record_every_write(monkeypatch)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    total = _one_total(capsys.readouterr().err)
    assert len(snapshots) == 5, "four progressive writes plus the final one"
    assert total.writes == len(snapshots), (
        "a write site left un-timed understates the write total silently"
    )


# ===========================================================================
# 3. The pool size beside the cost — the input whose square is being paid.
# ===========================================================================


def test_the_reported_pool_size_grows_with_the_accumulated_speech_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Three lines per game: the reported pool climbs 3 → 6 → 9 → 12, then holds.

    This is the field that makes the cost figure *explicable*. §2.8's claim is
    about a sum of squares, and a sum of squares cannot be checked from a column
    of timings alone — the pool size is the ``n``. Record 47 pooled 902 lines
    over ten games, so the shape a real run must show is this one, scaled.

    Monotonic growth is also the tell for a whole class of bug: every
    accumulator in the finalise pass is a Σ-over-games fold or a running ``max``,
    so a pool that shrank, reset, or double-counted would mean the closure had
    stopped being safe to call repeatedly — which is the property Slice 3 rests
    on.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval, "_play_one_game", _completed_game(lines_per_game=3)
    )

    run_eval(
        config,
        _storage_args(games=4),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    lines = _passes(capsys.readouterr().err)
    assert [row.pool_lines for row in lines] == [3, 6, 9, 12, 12]


def test_the_reported_pool_size_is_the_list_score_repetition_was_handed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``pool_lines`` equals ``len`` of the scorer's actual argument, per pass.

    A plausible neighbour of the right number would explain the cost curve
    wrongly and nobody could tell: the third-person measure's denominator, the
    per-game line count, or the repetition metric's denominator all look like
    pool sizes. The quadratic term is paid by ``score_repetition`` over the
    POOLED list, so the reported size is asserted against that call's own
    argument.

    The spy doubles as the "no per-line timing" check Task 7.1 asks for: the
    scorer is called exactly once per pass, so the instrument is two
    ``monotonic()`` calls around a whole pass and not a stopwatch inside the
    clustering loop.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    real = blunder_eval.score_repetition
    seen: list[int] = []

    def _spy(ai_lines: list[str], ai_names: set[str]) -> dict[str, float | int]:
        seen.append(len(ai_lines))
        return real(ai_lines, ai_names)

    monkeypatch.setattr(blunder_eval, "score_repetition", _spy)
    monkeypatch.setattr(
        blunder_eval, "_play_one_game", _completed_game(lines_per_game=5)
    )

    run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    lines = _passes(capsys.readouterr().err)
    assert seen == [5, 10, 15, 15], "one scoring call per pass, over the pooled list"
    assert [row.pool_lines for row in lines] == seen


def test_the_reported_cost_spans_the_scoring_and_not_merely_its_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A deliberately slow scorer must show up in every pass's figure.

    THE ONE FAILURE NO OTHER TEST HERE CAN SEE. Cadence, pool sizes, totals and
    percentages are all satisfied by an instrument whose clock starts in the
    wrong place — a ``pass_started`` hoisted below the scoring, or dropped into
    the last few statements of the closure, would report ``0.000`` for every
    pass and every other assertion in this module would still pass. The number
    would then be written into §2.8 as "re-scoring is free", which is the one
    conclusion this slice must not reach by accident.

    So the pass being priced is made *measurably* expensive at the one place
    §2.8 says the cost lives — ``score_repetition`` — and the reported figure has
    to contain it. The sleep is a lower bound, never an equality: the assertion
    is that the clock spans the scoring, not that it spans only the scoring.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    real = blunder_eval.score_repetition

    def _slow(ai_lines: list[str], ai_names: set[str]) -> dict[str, float | int]:
        time.sleep(_SLOW_SCORER_SECONDS)
        return real(ai_lines, ai_names)

    monkeypatch.setattr(blunder_eval, "score_repetition", _slow)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    lines = _passes(capsys.readouterr().err)
    assert len(lines) == 3
    assert all(row.finalise_s >= _SLOW_SCORER_SECONDS for row in lines), (
        f"every pass must price the scoring it performed: {lines}"
    )


# ===========================================================================
# 4. The fraction, and every denominator that would break it.
# ===========================================================================


@pytest.mark.parametrize(
    "run_seconds",
    [None, 0.0, -1.0],
    ids=["never-stamped", "rounded-to-zero", "negative"],
)
def test_percent_of_run_reports_absence_rather_than_dividing_by_zero(
    run_seconds: float | None,
) -> None:
    """Every unusable denominator yields ``'n/a'`` — no exception, ever.

    ``duration_seconds`` is a ``round(…, 3)`` monotonic delta, so ``0.0`` is a
    value a real run can produce (``--games 0``; every mocked run in this
    suite), and ``None`` is the field's value before the first pass stamps it.
    A ``ZeroDivisionError`` here would be an instrument killing the run it
    exists to observe — the graceful-degradation rule the eval layer applies to
    every optional measurement.

    The negative case is not reachable from a monotonic clock and is guarded
    anyway: the alternative is a *negative percentage* printed as if it were a
    measurement.
    """
    assert _percent_of_run(1.5, run_seconds) == "n/a"


def test_percent_of_run_renders_a_usable_denominator_as_a_percentage() -> None:
    """The control: a real denominator gives a percentage, to three decimals.

    Three decimals because the expected magnitude is a fraction of one percent —
    §2.8 predicts seconds against a 40-minute run — and a figure rounded to
    ``0.0`` would answer Task 7.2's question with a rounding artefact.
    """
    assert _percent_of_run(4.812, 2410.117) == "0.200"
    assert _percent_of_run(1.0, 100.0) == "1.000"


def test_the_summary_fraction_is_computed_against_the_runs_own_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``run_s`` is ``result.duration_seconds`` — the record's own clock, reused.

    Task 7.1 is explicit: reuse the clock the run already keeps, do not start a
    second one. It matters because the figure a reader compares the cost against
    is the one on the record (``run.duration_seconds``); a privately-held second
    clock would produce a percentage that could not be re-derived from the
    ledger, and a mismatch would be invisible.

    Asserted as the same rendering, not as "close enough": both come from one
    float, so anything but equality means a second clock appeared.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    total = _one_total(capsys.readouterr().err)
    assert result.duration_seconds is not None
    assert total.run_s == f"{result.duration_seconds:.3f}"
    # The percentage is present and derived, not "n/a" — its exact arithmetic
    # is pinned on exact inputs in section 0, because a mocked run's timings
    # are whatever the machine did and the printed 3-dp seconds cannot
    # reproduce a 3-dp percentage of a sub-second denominator.
    assert total.finalise_pct != "n/a"
    assert total.write_pct != "n/a"


def test_a_run_whose_clock_never_advanced_reports_n_a_and_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A frozen clock gives ``run_s=0.000`` and ``n/a`` percentages, not a crash.

    The zero-denominator guard, reached through a real ``run_eval`` call rather
    than only through the unit test above — because the guard's value is that it
    holds *in the harness*, and a unit test on the helper would keep passing if
    ``report`` grew its own division.

    The frozen clock is the deterministic way there: it makes every duration in
    the run exactly ``0.0``, which is what a ``--games 0`` run or a fast enough
    mocked one can produce by luck.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "time", _FrozenClock)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    result = run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    total = _one_total(capsys.readouterr().err)
    assert result.duration_seconds == 0.0
    assert total.run_s == "0.000"
    assert total.finalise_pct == "n/a"
    assert total.write_pct == "n/a"
    assert total.passes == 3, "the run still completed and still made its passes"


def test_a_zero_game_run_reports_its_single_post_loop_pass_over_an_empty_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--games 0``: one pass, ``game=0``, ``pool_lines=0``, and it parses.

    ``range(0)`` runs no iterations, so the only pass is the post-loop one — and
    its ``game`` field is ``0`` because ``games_attempted`` is ``0``, which is
    the reason the field reads ``games_attempted`` rather than the loop's
    ``game_number``: that local is *unbound* on this path, and an instrument
    that raised ``NameError`` on an edge the rest of the harness handles fine
    would be worse than no instrument.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(blunder_eval, "_play_one_game", _completed_game())

    run_eval(
        config,
        _storage_args(games=0),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    err = capsys.readouterr().err
    lines = _passes(err)
    assert [(row.pass_no, row.game, row.final, row.pool_lines) for row in lines] == [
        (1, 0, True, 0)
    ]
    assert _one_total(err).passes == 1


# ===========================================================================
# 5. Runs that end with nothing finished — this spec's whole subject.
# ===========================================================================


def test_a_run_stopped_before_its_first_game_says_nothing_about_cost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Killed during game 1: no pass line, no summary, no record, no crash.

    "Stopped before game 1" is a case the functional spec handles explicitly —
    nothing finished, so nothing is written — and the instrument must agree
    rather than print a summary of a run that produced no measurement. A
    ``passes=0 finalise_s=0.000`` block would read as *"re-scoring is free"*,
    which is the opposite of not knowing.

    ``KeyboardInterrupt`` is a ``BaseException``, so the loop's ``except
    Exception`` does not catch it and everything after the loop is skipped —
    the same control flow a real ``SIGINT`` produces, which is why no test here
    sends a signal.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        _failing_game(exc=KeyboardInterrupt("operator stopped the run")),
    )

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            config,
            _storage_args(games=10),
            transcripts_root=transcripts_root,
            run_id=_RUN_ID,
        )

    err = capsys.readouterr().err
    assert "finalise-cost" not in err, (
        "a run that finished no game produced no measurement to report"
    )
    assert not ledger.exists(), "and it wrote no record either"


def test_a_run_whose_every_game_failed_early_reports_exactly_one_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Three games, all raising: one post-loop pass, one write, an empty pool.

    The other zero-completed-games shape, and the one that is NOT an early stop:
    this run attempted everything it was asked for, so it runs to the end, makes
    its post-loop pass and writes its record. The instrument has to report that
    single pass — ``passes=1``, ``writes=1``, ``pool_lines=0`` — rather than
    either staying silent (as the stopped run above does) or reporting four
    passes as a clean run would.

    ``writes=1`` is the figure with teeth: the failed-early branch ``continue``s
    before the progressive write, so a run like this pays the scoring cost once
    and not once per game. It is also where ``writes`` and ``passes`` part
    company from the game count.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval, "_play_one_game", _failing_game(exc=RuntimeError("boom"))
    )

    result = run_eval(
        config,
        _storage_args(games=3),
        transcripts_root=transcripts_root,
        run_id=_RUN_ID,
    )

    err = capsys.readouterr().err
    lines = _passes(err)
    total = _one_total(err)
    assert [(row.pass_no, row.game, row.final, row.pool_lines) for row in lines] == [
        (1, 3, True, 0)
    ]
    assert (total.passes, total.writes, total.pool_lines) == (1, 1, 0)
    assert result.games_completed == 0
    assert ledger.exists(), "it attempted every game it was asked for, so it wrote"


# ===========================================================================
# 6. The record shape is untouched — "not a new ledger field", on bytes.
# ===========================================================================


class _SilentCost:
    """A drop-in ``_FinaliseCost`` that measures nothing and prints nothing.

    The control arm for the byte-identity comparison below: same call surface,
    no output, no state. Substituting it removes the instrument entirely from a
    run, which is the only way to compare "with" against "without" inside one
    test process.
    """

    def record_pass(self, **kwargs: object) -> None:
        return None

    def record_write(self, elapsed: float) -> None:
        return None

    def report(self, run_seconds: float | None) -> None:
        return None


# ``duration_seconds`` is wall clock and is the one field two runs of the same
# games legitimately differ in. Blanked before comparing so the assertion is
# about SHAPE AND CONTENT rather than about how fast the machine was; it appears
# twice per record (``run`` and ``quality``), and ``re.sub`` replaces both.
_DURATION_RE = re.compile(r"duration_seconds: \S+")


def _document_of_a_run(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    *,
    instrumented: bool,
) -> str:
    """Play a fixed two-game run in ``workspace`` and return its ledger document.

    Each call gets its own ledger, backup dir and transcripts root under
    ``workspace``, so the two arms cannot see each other's files — while keeping
    the SAME ``run_id``, because ``run.id`` is part of the bytes being compared.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, workspace)
    if not instrumented:
        monkeypatch.setattr(blunder_eval, "_FinaliseCost", _SilentCost)
    monkeypatch.setattr(
        blunder_eval, "_play_one_game", _completed_game(lines_per_game=2)
    )

    run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=workspace / "transcripts",
        run_id=_RUN_ID,
    )
    return ledger.read_bytes().decode("utf-8")


def test_the_instrument_changes_not_one_byte_of_the_rendered_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Instrumented and un-instrumented runs write byte-identical documents.

    Task 7.1's hard constraint, asserted the way Slice 1 established and Task
    3.1's verification repeated: on bytes. Timings, pass counts and pool sizes
    are diagnostics about a run, not properties of it — a record that carried
    them would be a record-shape change this spec did not ask for, would not be
    comparable with the fifty already committed, and would have to argue about
    ``METRICS_VERSION``.

    Bytes rather than parsed values because a parse-and-re-render round trip
    would hide exactly the failure being guarded: a key appended in the right
    block, with the right type, in the wrong record shape.

    ``duration_seconds`` is normalised out — it is wall clock, and the two arms
    run at different speeds by construction. Everything else must match,
    ``run.id``, ``run.seq`` and the metric floats included.
    """
    with monkeypatch.context() as instrumented_ctx:
        instrumented = _document_of_a_run(
            instrumented_ctx, tmp_path / "with", instrumented=True
        )
    with monkeypatch.context() as silent_ctx:
        silent = _document_of_a_run(
            silent_ctx, tmp_path / "without", instrumented=False
        )

    assert _DURATION_RE.sub("duration_seconds: <t>", instrumented) == _DURATION_RE.sub(
        "duration_seconds: <t>", silent
    ), "the cost instrument must contribute nothing to the record"


def test_the_records_own_keys_never_mention_the_instrument(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """None of the instrument's field names appears anywhere in the document.

    The complement of the byte-identity test above, and worth having separately
    because it fails with a *readable* message: if somebody adds
    ``quality.finalise_seconds`` one day, the comparison test says "these two
    blobs differ" while this one names the string that leaked.
    """
    document = _document_of_a_run(monkeypatch, tmp_path / "solo", instrumented=True)

    for token in (
        "finalise",
        "pool_lines",
        "finalise_s",
        "write_s",
        "passes",
        "cost",
    ):
        assert token not in document, f"the instrument leaked {token!r} into the record"


def test_a_silent_instrument_is_a_faithful_double_of_the_real_one() -> None:
    """``_SilentCost`` answers every call ``run_eval`` makes on ``_FinaliseCost``.

    The control arm above is only a control if it cannot diverge: a double
    missing a method would make the un-instrumented run fail for a reason that
    has nothing to do with the record, and a double with *extra* methods would
    hide a call site the real class had grown. Asserted as a set equality over
    the public surface so both directions are covered.
    """
    def _methods(cls: type) -> set[str]:
        return {
            name
            for name, value in vars(cls).items()
            if callable(value) and not name.startswith("_")
        }

    assert _methods(_SilentCost) == _methods(_FinaliseCost)
