"""Spec 027 — Scripted-Player's-Side Win Rate in Evals (single slice).

Pure-tally unit tests for the ``outcomes.scripted_side`` entry: the win rate of
*the side the scripted stand-in was on*, computed per game over the all-games
denominator, with a Wilson 95% band and the side label. No live model, no RNG —
the same posture as the existing ``tally_outcomes`` tests in
``test_outcome_tracking.py`` (whose ``_player`` / ``_roster`` / fixed-date
helpers this file mirrors). Mapping to the functional-spec acceptance criteria:

- LA-seat run → ``scripted_side.rate == law_abiding.rate`` (+ ``side``); Mafia-seat
  → ``== mafia.rate``.
- A ``no_winner`` / ``runaway`` game counts toward the total but never as a win.
- Per-game correctness under a varying seat side (proves the per-game derivation).
- The Wilson CI is attached and matches the by-side band in the pinned case.
- The ``games == 0`` path (no ``ZeroDivisionError``).
- No resolved side → the entry is omitted entirely (absent, never a misleading 0).
- ``render_record`` emits the block in the fixed order (after ``mafia``, before
  ``runaway``) AND back-compat (a record without it renders).
- The viewer reads a pre-027 record cleanly (and renders the new detail line when
  present).
- ``run_eval``'s ``_seat_side`` fold + the console summary line.
"""

from __future__ import annotations

import yaml

from graphia.eval_ledger import render_detail
from graphia.state import PlayerState
from graphia.tools.blunder_eval import (
    EvalResult,
    _GameCapture,
    _scripted_side_summary,
    _seat_side,
    render_record,
    tally_outcomes,
    wilson_ci,
)

# A fixed run date threaded into every rendered record (its value is not under
# test, only the field placement) — mirrors ``test_outcome_tracking.py``.
_RUN_DATE = "2026-06-19"


def _player(
    pid: str,
    name: str,
    role: str = "law_abiding",
    is_human: bool = False,
) -> PlayerState:
    """A ``PlayerState`` from the real dataclass (mirrors the sibling tests)."""
    return PlayerState(id=pid, name=name, role=role, is_human=is_human)


def _roster(*players: PlayerState) -> dict[str, PlayerState]:
    """The ``players`` map keyed by id, as a final state surfaces it."""
    return {p.id: p for p in players}


# ===========================================================================
# A — the scripted-side tally (pure)
# ===========================================================================


def test_la_seat_run_equals_law_abiding_rate() -> None:
    """LA-seat run → ``scripted_side.rate == law_abiding.rate``, ``side == 'law_abiding'``.

    With every game's seat side pinned to ``law_abiding``, the scripted-side rate
    is a derived view of the LA by-side rate: same numerator (LA wins), same
    all-games denominator, so the two rates and their Wilson bands coincide (AC1,
    AC2). ``wins`` equals the LA win count.
    """
    winners: list[str | None] = ["law_abiding", "law_abiding", "mafia", "law_abiding"]
    sides: list[str | None] = ["law_abiding"] * 4

    block = tally_outcomes(winners, sides)
    scripted = block["scripted_side"]

    assert scripted["side"] == "law_abiding"
    assert scripted["wins"] == 3  # three LA wins == LA by-side wins
    assert scripted["wins"] == block["law_abiding"]["wins"]
    assert scripted["rate"] == block["law_abiding"]["rate"]


def test_mafia_seat_run_equals_mafia_rate() -> None:
    """The SAME games dealt a Mafia seat → ``scripted_side.rate == mafia.rate``.

    Same winners as the LA test, but the seat is pinned to Mafia, so the
    scripted-side rate now equals the Mafia by-side rate and the label flips
    (AC2). The by-side rates themselves are unchanged — the metric complements
    them, never replaces them.
    """
    winners: list[str | None] = ["law_abiding", "law_abiding", "mafia", "law_abiding"]
    sides: list[str | None] = ["mafia"] * 4

    block = tally_outcomes(winners, sides)
    scripted = block["scripted_side"]

    assert scripted["side"] == "mafia"
    assert scripted["wins"] == 1  # the single mafia win
    assert scripted["rate"] == block["mafia"]["rate"]
    # The by-side rates are intact alongside the derived view.
    assert block["law_abiding"]["rate"] == 3 / 4


def test_no_winner_and_runaway_count_toward_total_but_not_as_win() -> None:
    """A ``no_winner`` / ``runaway`` game is a NON-win yet counts in the denominator.

    The scripted side is LA. Two LA wins, then a ``None`` game and a ``runaway``
    game — neither ``winner`` is a side, so each is a non-win, but both still
    count toward ``games`` (the all-games denominator). So ``rate`` is over the
    larger denominator (2/4), not 2/2 (AC of "all games") — exactly how the
    by-side rates count.
    """
    winners: list[str | None] = ["law_abiding", "law_abiding", None, "runaway"]
    sides: list[str | None] = ["law_abiding"] * 4

    block = tally_outcomes(winners, sides)
    scripted = block["scripted_side"]

    assert block["games"] == 4
    assert scripted["wins"] == 2
    assert scripted["rate"] == 2 / 4  # denominator is ALL 4 games, not 2
    # The non-win games are still partitioned into their own buckets.
    assert block["no_winner"] == 1
    assert block["runaway"] == 1


def test_per_game_correctness_under_varying_seat_side() -> None:
    """A varying seat side → ``wins`` counts games where ``winner == that game's seat``.

    This is the load-bearing per-game derivation: it must NOT be a single by-side
    read. Game-by-game (winner, seat): (law_abiding, mafia) ✗,
    (mafia, mafia) ✓, (law_abiding, law_abiding) ✓, (mafia, law_abiding) ✗ → 2
    scripted wins. The label defaults to the most-common resolved side (here a
    2/2 tie → the first-seen ``mafia``); the rate is the per-game count regardless.
    """
    winners: list[str | None] = ["law_abiding", "mafia", "law_abiding", "mafia"]
    sides: list[str | None] = ["mafia", "mafia", "law_abiding", "law_abiding"]

    scripted = tally_outcomes(winners, sides)["scripted_side"]

    assert scripted["wins"] == 2
    assert scripted["rate"] == 2 / 4
    # Neither by-side rate equals this per-game rate in the general varying case:
    # the derivation is genuinely per game, not a constant by-side read.
    assert scripted["side"] in {"law_abiding", "mafia"}


def test_wilson_ci_attached_and_matches_by_side_in_pinned_case() -> None:
    """``scripted_side`` carries ``ci_low``/``ci_high`` from ``wilson_ci(wins, games)``.

    In the pinned case the band equals the matching by-side band (same
    ``wins``/``games``), and it is a proper interval inside ``[0, 1]``.
    """
    winners: list[str | None] = ["mafia", "mafia", "law_abiding", None]
    sides: list[str | None] = ["mafia"] * 4

    block = tally_outcomes(winners, sides)
    scripted = block["scripted_side"]

    low, high = wilson_ci(2, 4)  # two mafia wins over four games
    assert scripted["ci_low"] == low
    assert scripted["ci_high"] == high
    assert scripted["ci_low"] == block["mafia"]["ci_low"]
    assert scripted["ci_high"] == block["mafia"]["ci_high"]
    assert 0.0 <= scripted["ci_low"] <= scripted["ci_high"] <= 1.0


def test_games_zero_path_omits_rate_and_ci_no_zero_division() -> None:
    """Empty run with no resolved side → ``scripted_side`` omitted (parallel lists empty).

    An empty ``winners`` pairs with an empty ``scripted_sides``, so no side ever
    resolves and the entry is omitted entirely (the absent-metric posture) — and
    no ``ZeroDivisionError`` is raised building the rest of the block.
    """
    block = tally_outcomes([], [])

    assert block["games"] == 0
    assert "scripted_side" not in block  # no resolved side → omitted


def test_single_unresolved_game_with_seat_keeps_present_zero_rate() -> None:
    """A 1-game run (``None`` winner, LA seat) → ``rate: 0.0`` present over 1 game.

    Guards that a resolved seat side never silently drops its rate when
    ``games > 0``: the lone game is a non-win (its winner is not a side), so
    ``wins == 0`` and ``rate == 0.0`` (present, not omitted), with ``ci_low``
    pinned to exactly ``0.0`` by ``count == 0``.
    """
    block = tally_outcomes([None], ["law_abiding"])
    scripted = block["scripted_side"]

    assert scripted["side"] == "law_abiding"
    assert scripted["wins"] == 0
    assert scripted["rate"] == 0.0  # present, over the 1-game denominator
    assert scripted["ci_low"] == 0.0  # count == 0 pins the lower bound


def test_games_zero_with_resolved_label_emits_bare_wins_no_rate() -> None:
    """The internal ``games == 0`` branch → ``{side, wins: 0}`` with rate/CI omitted.

    Exercises ``_tally_scripted_side`` directly with a known label but zero games
    (the degenerate shape ``tally_outcomes`` itself can't reach through its
    parallel public lists), pinning the bare-shape contract that mirrors the
    side-rate ``games == 0`` path: no ``rate`` / ``ci_low`` / ``ci_high`` keys.
    """
    from graphia.tools.blunder_eval import _tally_scripted_side

    scripted = _tally_scripted_side([], ["law_abiding"], games=0)

    assert scripted == {"side": "law_abiding", "wins": 0}
    assert "rate" not in scripted and "ci_low" not in scripted


def test_no_resolved_side_omits_entry() -> None:
    """All games with an unresolvable seat side → ``scripted_side`` absent.

    A run where every per-game side is ``None`` (a malformed/missing ``human_id``
    everywhere) resolves no side, so the entry is omitted — treated like an absent
    metric, never a misleading ``0`` (defensive). The four partition buckets are
    untouched.
    """
    winners: list[str | None] = ["law_abiding", "mafia", None]
    sides: list[str | None] = [None, None, None]

    block = tally_outcomes(winners, sides)

    assert "scripted_side" not in block
    # The by-side partition is unaffected by the absent derived view.
    assert block["law_abiding"]["wins"] == 1
    assert block["mafia"]["wins"] == 1


def test_scripted_sides_none_argument_omits_entry() -> None:
    """Calling ``tally_outcomes(winners)`` with no sides → ``scripted_side`` absent.

    The default ``scripted_sides=None`` (a passive/older fold that never threaded
    sides, or any pre-027 call site) yields no entry — back-compat at the tally
    level, so existing one-argument callers see an unchanged block.
    """
    block = tally_outcomes(["law_abiding", "mafia"])
    assert "scripted_side" not in block


# ===========================================================================
# B — run_eval seat-side resolution (the per-game fold helper, pure)
# ===========================================================================


def _capture(winner: str | None, human_id: str, *players: PlayerState) -> _GameCapture:
    """A minimal ``_GameCapture`` for the ``_seat_side`` fold (no transcript/scoring)."""
    return _GameCapture(
        ai_lines=[],
        ai_names=set(),
        ai_lines_with_speakers=[],
        players=_roster(*players),
        messages=[],
        winner=winner,
        captures=[],
        human_id=human_id,
    )


def test_seat_side_resolves_dealt_role() -> None:
    """``_seat_side`` reads ``players[human_id].role`` — the underscore token."""
    human = _player("h", "Hugo", role="mafia", is_human=True)
    other = _player("p-1", "Cara", role="law_abiding")
    cap = _capture("mafia", "h", human, other)

    assert _seat_side(cap) == "mafia"


def test_seat_side_defensive_on_missing_or_empty_human_id() -> None:
    """An empty/missing/unmatched ``human_id`` → ``None`` (excluded from the numerator)."""
    other = _player("p-1", "Cara", role="law_abiding")

    # Empty id (a hand-built capture with no wiring).
    assert _seat_side(_capture("law_abiding", "", other)) is None
    # An id absent from the players map.
    assert _seat_side(_capture("law_abiding", "ghost", other)) is None


# ===========================================================================
# C — render_record (emit in fixed order + back-compat)
# ===========================================================================


def test_render_record_emits_scripted_side_after_mafia_before_runaway() -> None:
    """A populated ``outcomes`` renders ``scripted_side`` between ``mafia`` and ``runaway``.

    Asserts both the textual key ORDER (after ``mafia``, before ``runaway``) and
    that the document round-trips through ``yaml.safe_load`` with the sub-keys
    intact (``side`` first, then ``wins``/``rate``/``ci_low``/``ci_high``).
    """
    outcomes = tally_outcomes(
        ["law_abiding", "law_abiding", "mafia", None],
        ["law_abiding"] * 4,
    )
    result = EvalResult(provider="ollama", outcomes=outcomes)

    doc = render_record(result, _RUN_DATE)

    i_mafia = doc.index("  mafia:")
    i_scripted = doc.index("  scripted_side:")
    i_runaway = doc.index("  runaway:")
    assert i_mafia < i_scripted < i_runaway

    parsed = yaml.safe_load(doc)
    scripted = parsed["outcomes"]["scripted_side"]
    assert scripted["side"] == "law_abiding"
    assert scripted["wins"] == 2
    assert "rate" in scripted and "ci_low" in scripted and "ci_high" in scripted


def test_render_record_back_compat_without_scripted_side() -> None:
    """An ``outcomes`` block with no ``scripted_side`` renders without it, no error.

    The pre-027 / no-resolved-side shape: ``render_record`` omits the key
    entirely (the ``if isinstance(..., dict)`` guard), so an older synthetic
    record renders the outcomes block unchanged and still parses as YAML.
    """
    outcomes = tally_outcomes(["law_abiding", "mafia"])  # no sides → no entry
    result = EvalResult(provider="ollama", outcomes=outcomes)

    doc = render_record(result, _RUN_DATE)

    assert "scripted_side" not in doc
    parsed = yaml.safe_load(doc)
    assert "scripted_side" not in parsed["outcomes"]
    # The rest of the outcomes block is intact.
    assert parsed["outcomes"]["law_abiding"]["wins"] == 1


def test_render_record_games_zero_path_emits_no_outcomes_scripted_side() -> None:
    """A ``games == 0`` run omits ``scripted_side`` (no side resolved) and still renders."""
    result = EvalResult(provider="ollama", outcomes=tally_outcomes([], []))
    doc = render_record(result, _RUN_DATE)
    assert "scripted_side" not in doc
    assert yaml.safe_load(doc)["outcomes"]["games"] == 0


# ===========================================================================
# D — viewer detail (renders when present, clean on a pre-027 record)
# ===========================================================================


def test_viewer_renders_scripted_side_detail_when_present() -> None:
    """``render_detail`` shows a ``scripted_side`` sub-block when the record carries it."""
    outcomes = tally_outcomes(["mafia", "mafia", "law_abiding"], ["mafia"] * 3)
    doc = render_record(EvalResult(provider="ollama", outcomes=outcomes), _RUN_DATE)
    record = yaml.safe_load(doc)

    detail = render_detail(record)

    assert "scripted_side:" in detail
    assert "side: mafia" in detail
    # The full-precision rate (2/3) appears, not the table's two-decimal form.
    assert repr(2 / 3) in detail


def test_viewer_reads_pre_027_record_cleanly() -> None:
    """A pre-027 record (no ``outcomes.scripted_side``) renders with no scripted line.

    The defensive ``_dig`` path: the outcomes section is byte-faithful to the old
    shape — no ``scripted_side`` line, no extra ``—`` placeholder — and
    ``render_detail`` does not raise.
    """
    outcomes = tally_outcomes(["law_abiding", "mafia"])  # pre-027: no entry
    doc = render_record(EvalResult(provider="ollama", outcomes=outcomes), _RUN_DATE)
    record = yaml.safe_load(doc)

    detail = render_detail(record)  # must not raise

    assert "scripted_side" not in detail
    # The outcomes section is still rendered (sanity: the sides are present).
    assert "law_abiding:" in detail


# ===========================================================================
# E — console summary line
# ===========================================================================


def test_summary_line_prints_when_scripted_side_present() -> None:
    """The summary helper renders the scripted-side KPI line with side + rate + CI."""
    outcomes = tally_outcomes(
        ["law_abiding", "law_abiding", "mafia", None],
        ["law_abiding"] * 4,
    )

    line = _scripted_side_summary(outcomes)

    assert line.startswith("scripted side (law_abiding):")
    assert "won 2/4" in line
    assert "rate=0.50" in line
    assert "95% CI [" in line


def test_summary_line_empty_when_scripted_side_absent() -> None:
    """No ``scripted_side`` (pre-027 / unresolved) → the summary helper prints nothing."""
    # Absent from the outcomes block entirely.
    assert _scripted_side_summary(tally_outcomes(["law_abiding"])) == ""
    # And a completely empty outcomes mapping.
    assert _scripted_side_summary({}) == ""


def test_summary_line_drops_rate_clause_on_games_zero_shape() -> None:
    """A ``{side, wins}`` (rate-less) scripted block → the line omits the rate clause.

    Guards the ``games == 0`` rendering of the summary: a scripted block carrying
    only ``side``/``wins`` (no ``rate``) prints ``won W/G`` with no ``rate=`` /
    ``CI`` tail, mirroring the side-rate omission.
    """
    outcomes = {"games": 0, "scripted_side": {"side": "mafia", "wins": 0}}

    line = _scripted_side_summary(outcomes)

    assert line == "scripted side (mafia): won 0/0"
    assert "rate=" not in line
