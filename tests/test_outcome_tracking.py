"""Offline tests for the spec-013 outcome + vote-activity measurement (Slice 1).

Locks in the two orthogonal game-dynamics measurements added in spec 013 —
``outcomes`` (win-rate by side, with a Wilson CI on the two sides) and
``vote_activity`` (AI vote-initiation counts by side and by game-day) — across
the producer (:mod:`graphia.tools.blunder_eval`) and the consumer
(:mod:`graphia.eval_ledger` flatten + the Textual viewer). **No LLM, no network,
no AWS** — the pure scorers/renderers are unit-tested on synthetic inputs, and
the viewer is driven through Textual's ``run_test`` Pilot against a ``tmp_path``
ledger. The committed ``evals/blunder-ledger.yaml`` is NEVER read or written.

Test groups (the spec's A/B/C/D plan):

A. ``tally_outcomes`` — the four-bucket partition, per-side Wilson rates over the
   completed denominator, the partition invariant, the ``games == 0`` and
   ``None``-only edge cases.
B. ``score_vote_activity`` — by_side/by_day counts on a multi-day multi-vote
   history (built from the REAL imported day-open / vote-announce templates), the
   explicit-zero headline, the day-open PREFIX TRAP, human-initiator exclusion, a
   two-game fold, and the marginal-sums invariant.
C. ``render_record`` — both blocks in the fixed key order between ``quality`` and
   ``metrics`` (``notes`` last), the immutable caveat, the literal ``by_day: {}``,
   a ``yaml.safe_load`` round-trip, and a bare ``EvalResult`` that omits both.
D. ``eval_ledger`` flatten + viewer — the ``Wins (LA/M)`` / ``Votes (LA/M)``
   cells, the present-zero-vs-absent distinction (rendered DIFFERENTLY), the
   ``render_detail`` sections, the ``winner`` scoped search, and a Pilot session
   over a mixed new/old ledger.

Every vote/day fixture is rendered from the REAL imported templates via
``.format`` (a template reword breaks the test, not the metric — the same
discipline ``test_blunder_eval_detectors.py`` uses); ``PlayerState`` and
``EvalResult`` are imported from the real modules so a field rename breaks these
tests honestly.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from textual.widgets import DataTable, Static

from graphia.eval_ledger import (
    METRIC_ORDER,
    build_table_model,
    load_ledger,
    render_detail,
    row_matches_field,
)
from graphia.prompts import (
    DAY_OPEN_NO_VICTIM_TEMPLATE,
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
)
from graphia.state import PlayerState
from graphia.tools.blunder_eval import (
    _OUTCOMES_HUMAN_CAVEAT,
    EvalResult,
    render_record,
    score_vote_activity,
    tally_outcomes,
    wilson_ci,
)
from graphia.ui.ledger_viewer import (
    DetailScreen,
    LedgerViewerApp,
)

from conftest import plain_text

# A fixed run date threaded into every rendered record (``render_record`` takes
# it as an argument; the value is not under test, only its placement).
_RUN_DATE = "2026-06-15"


# ===========================================================================
# Synthetic-history builders — every day-open / vote-announce line is rendered
# from the REAL imported template via ``.format`` and wrapped in the real
# ``SystemMessage`` the node emits. A reword of either template breaks these
# helpers (and every test that uses them) immediately.
# ===========================================================================


def _player(
    pid: str,
    name: str,
    role: str = "law_abiding",
    is_human: bool = False,
) -> PlayerState:
    """A ``PlayerState`` built from the real dataclass (mirrors existing tests)."""
    return PlayerState(id=pid, name=name, role=role, is_human=is_human)


def _roster(*players: PlayerState) -> dict[str, PlayerState]:
    """The ``players`` map keyed by id, as the harness surfaces from a final state."""
    return {p.id: p for p in players}


def _announce(initiator: PlayerState, target: PlayerState) -> SystemMessage:
    """A vote-initiation announce ``SystemMessage`` from the real template."""
    return SystemMessage(
        content=VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
            initiator=initiator.name,
            target=target.name,
        )
    )


def _day_open_victim(victim: PlayerState, role_label: str = "law-abiding") -> SystemMessage:
    """A victim-reveal day-open ``SystemMessage`` from the real template.

    The full ("Day breaks. {name} was killed last night. …") form — which the
    victim regex must match in full, exercising the prefix-trap's first branch.
    """
    return SystemMessage(
        content=DAY_OPEN_VICTIM_REVEAL_TEMPLATE.format(
            name=victim.name,
            role_label=role_label,
        )
    )


def _day_open_no_victim() -> SystemMessage:
    """A bare no-victim day-open ``SystemMessage`` ("Day breaks.").

    This is a strict PREFIX of the victim-reveal line, so it must be matched by
    exact-equality (after the victim regex fails) — the prefix-trap's second
    branch — and never double-count a victim line.
    """
    return SystemMessage(content=DAY_OPEN_NO_VICTIM_TEMPLATE)


# ===========================================================================
# A — tally_outcomes (pure)
# ===========================================================================


def test_tally_outcomes_four_buckets_populated() -> None:
    """All four buckets populated → counts, per-side rates, and the partition holds.

    Six completed games: 3 law-abiding wins, 1 mafia win, 1 draw, 1 unresolved
    (``None`` → ``no_winner``). The two sides carry ``wins`` + ``rate`` over the
    SAME ``games`` denominator (6), and the four buckets PARTITION the run.
    """
    winners: list[str | None] = [
        "law_abiding",
        "law_abiding",
        "law_abiding",
        "mafia",
        "draw",
        None,
    ]

    block = tally_outcomes(winners)

    assert block["games"] == 6
    assert block["law_abiding"]["wins"] == 3
    assert block["law_abiding"]["rate"] == pytest.approx(3 / 6)
    assert block["mafia"]["wins"] == 1
    assert block["mafia"]["rate"] == pytest.approx(1 / 6)
    assert block["draw"] == 1
    assert block["no_winner"] == 1
    # The immutable passive-human caveat rides along, by value.
    assert block["note"] == _OUTCOMES_HUMAN_CAVEAT


def test_tally_outcomes_partition_invariant_holds() -> None:
    """THE PARTITION INVARIANT: la + mafia + runaway + draw + no_winner == games.

    The buckets are mutually exclusive and exhaustive over the completed games,
    so their counts must sum to the denominator — the README-stated invariant the
    whole block leans on (spec 023 added the ``runaway`` bucket).
    """
    winners: list[str | None] = [
        "law_abiding",
        "mafia",
        "mafia",
        "runaway",
        "draw",
        None,
        None,
        "law_abiding",
    ]

    block = tally_outcomes(winners)

    total = (
        block["law_abiding"]["wins"]
        + block["mafia"]["wins"]
        + block["runaway"]
        + block["draw"]
        + block["no_winner"]
    )
    assert total == block["games"] == len(winners)


def test_tally_outcomes_runaway_bucket_distinct_from_wins_and_draw() -> None:
    """Spec 023: a ``"runaway"`` (Day-cap hit) lands in its OWN bucket.

    A runaway game is a stuck/looping game, not a legitimate result — it must be
    visibly distinct from a real side win and from the legacy ``draw``/the
    ``None`` → ``no_winner`` bucket. Here three games hit the cap; none inflate a
    side's ``wins`` and none are counted as a draw or as ``no_winner``.
    """
    winners: list[str | None] = [
        "law_abiding",
        "runaway",
        "runaway",
        "mafia",
        "runaway",
        "draw",
        None,
    ]

    block = tally_outcomes(winners)

    assert block["runaway"] == 3
    # The cap-hits did NOT inflate either side's wins, the draw, or no_winner.
    assert block["law_abiding"]["wins"] == 1
    assert block["mafia"]["wins"] == 1
    assert block["draw"] == 1
    assert block["no_winner"] == 1
    # Side win-rates are over the full denominator, NOT shrunk by runaways.
    assert block["games"] == 7
    assert block["law_abiding"]["rate"] == pytest.approx(1 / 7)
    # The buckets still partition the run with runaway in the sum.
    total = (
        block["law_abiding"]["wins"]
        + block["mafia"]["wins"]
        + block["runaway"]
        + block["draw"]
        + block["no_winner"]
    )
    assert total == block["games"]


def test_tally_outcomes_wilson_ci_present_on_both_sides() -> None:
    """Each side win-rate carries a Wilson 95% CI equal to ``wilson_ci(wins, games)``."""
    winners: list[str | None] = ["law_abiding", "law_abiding", "mafia", "draw"]

    block = tally_outcomes(winners)

    la_low, la_high = wilson_ci(2, 4)
    m_low, m_high = wilson_ci(1, 4)
    assert block["law_abiding"]["ci_low"] == la_low
    assert block["law_abiding"]["ci_high"] == la_high
    assert block["mafia"]["ci_low"] == m_low
    assert block["mafia"]["ci_high"] == m_high
    # The CI is a proper band inside the unit interval.
    assert 0.0 <= la_low <= la_high <= 1.0
    assert 0.0 <= m_low <= m_high <= 1.0


def test_tally_outcomes_all_law_abiding_gives_mafia_zero_rate_with_ci() -> None:
    """All-``law_abiding`` → ``mafia.rate == 0.0`` with a (positive-width) CI present.

    A genuine 0.0 mafia win-rate (not absent) — distinct from the ``games == 0``
    path that omits the rate entirely. ``count == 0`` pins ``ci_low`` to exactly
    0.0 while ``ci_high`` stays above it (0/n is not certainty of zero).
    """
    block = tally_outcomes(["law_abiding"] * 5)

    assert block["law_abiding"]["wins"] == 5
    assert block["law_abiding"]["rate"] == 1.0
    assert block["mafia"]["wins"] == 0
    assert block["mafia"]["rate"] == 0.0
    # The 0.0 rate still carries a CI (it is present, not omitted).
    assert block["mafia"]["ci_low"] == 0.0
    assert block["mafia"]["ci_high"] > 0.0


def test_tally_outcomes_empty_list_omits_rates_and_ci_no_zero_division() -> None:
    """``games == 0`` (empty list) → zero counts, rates/CI OMITTED, no ZeroDivisionError.

    The block is still emitted with ``games: 0`` and a bare ``wins: 0`` on each
    side — but the ``rate`` / ``ci_low`` / ``ci_high`` keys are absent so a 0/0
    never raises and never reads as a real 0.0 rate.
    """
    block = tally_outcomes([])

    assert block["games"] == 0
    assert block["law_abiding"] == {"wins": 0}
    assert block["mafia"] == {"wins": 0}
    assert "rate" not in block["law_abiding"]
    assert "ci_low" not in block["law_abiding"]
    assert "ci_high" not in block["mafia"]
    assert block["draw"] == 0
    assert block["no_winner"] == 0
    assert block["note"] == _OUTCOMES_HUMAN_CAVEAT


def test_tally_outcomes_none_only_maps_all_to_no_winner() -> None:
    """A ``None``-only list → ``no_winner == games``, both side rates a real 0.0.

    Unresolved games (round cap, the common eval outcome against a passive human)
    all land in ``no_winner``; neither side won any, so both side rates are 0.0
    (present, over the full denominator) and ``no_winner`` equals the game count.
    """
    block = tally_outcomes([None, None, None])

    assert block["games"] == 3
    assert block["no_winner"] == 3
    assert block["draw"] == 0
    assert block["law_abiding"]["wins"] == 0
    assert block["law_abiding"]["rate"] == 0.0
    assert block["mafia"]["wins"] == 0
    assert block["mafia"]["rate"] == 0.0


# ===========================================================================
# B — score_vote_activity (pure, from real templates)
# ===========================================================================


def test_score_vote_activity_counts_by_side_and_by_day() -> None:
    """by_side / by_day counts over a multi-day, multi-vote, multi-side history.

    Day 1 (after a victim-reveal open): a law-abiding AI initiation and a mafia AI
    initiation. Day 2 (after a no-victim open): a second mafia AI initiation. So
    by_side == {law_abiding: 1, mafia: 2}; by_day == {day_1: 2, day_2: 1}.
    """
    cara = _player("p-1", "Cara", role="law_abiding")
    don = _player("p-2", "Don", role="mafia")
    eve = _player("p-3", "Eve", role="law_abiding")
    vito = _player("p-4", "Vito", role="mafia")
    players = _roster(cara, don, eve, vito)

    messages = [
        _day_open_victim(eve),       # → day 1
        _announce(cara, don),        # day 1: law-abiding initiates
        _announce(don, eve),         # day 1: mafia initiates
        _day_open_no_victim(),       # → day 2
        _announce(vito, cara),       # day 2: mafia initiates
    ]

    activity = score_vote_activity(messages, players)

    assert activity["by_side"] == {"law_abiding": 1, "mafia": 2}
    assert activity["by_day"] == {"day_1": 2, "day_2": 1}


def test_score_vote_activity_explicit_zero_headline() -> None:
    """EXPLICIT-ZERO: day-opens present, ZERO initiations → by_side present, by_day {}.

    The deliberate INVERSE of ``metrics``' absent-omission: the absence of vote
    activity is ITSELF the signal (the Nova-silent-Day pathology), so ``by_side``
    ALWAYS carries BOTH side keys at a committed integer ``0``, while ``by_day``
    is the empty map (no day saw an initiation).
    """
    cara = _player("p-1", "Cara", role="law_abiding")
    don = _player("p-2", "Don", role="mafia")
    players = _roster(cara, don)

    messages = [
        _day_open_victim(cara),   # → day 1, but nobody ever calls a vote
        _day_open_no_victim(),    # → day 2, still silent
    ]

    activity = score_vote_activity(messages, players)

    # by_side PRESENT with both keys at an explicit 0 — never an omitted block.
    assert activity["by_side"] == {"law_abiding": 0, "mafia": 0}
    # by_day is the empty map (sparse, no day had activity).
    assert activity["by_day"] == {}


def test_score_vote_activity_prefix_trap_increments_each_day_once() -> None:
    """PREFIX TRAP: a victim-reveal open and a bare "Day breaks." each bump day once.

    ``DAY_OPEN_NO_VICTIM_TEMPLATE`` ("Day breaks.") is a strict prefix of the
    victim-reveal line ("Day breaks. {name} was…"). The scorer tests the victim
    regex (full-anchored) FIRST and falls back to exact-equality for the no-victim
    line, so the victim line increments the day counter exactly once (it must NOT
    also match the no-victim branch) and the bare line increments it exactly once.
    A single mafia initiation on each day proves the counter advanced to day 2.
    """
    cara = _player("p-1", "Cara", role="law_abiding")
    don = _player("p-2", "Don", role="mafia")
    players = _roster(cara, don)

    messages = [
        _day_open_victim(cara),   # victim line → day 1 (matched by victim regex)
        _announce(don, cara),     # day 1 mafia initiation
        _day_open_no_victim(),    # bare line → day 2 (matched by exact-equality)
        _announce(don, cara),     # day 2 mafia initiation
    ]

    activity = score_vote_activity(messages, players)

    # If the victim line had double-counted, the first initiation would land on
    # day 2 and there would be no day_1 / a day_3. Exactly one per day proves the
    # counter incremented once each.
    assert activity["by_day"] == {"day_1": 1, "day_2": 1}
    assert activity["by_side"] == {"law_abiding": 0, "mafia": 2}


def test_score_vote_activity_excludes_human_initiator() -> None:
    """A human initiator is excluded; only AI initiations are counted.

    The human (a mafioso here, so role is not the reason they drop out) initiates
    a vote — it must not enter any side count. The lone AI mafioso initiation is
    the only one counted.
    """
    human = _player("p-1", "You", role="mafia", is_human=True)
    don = _player("p-2", "Don", role="mafia")
    cara = _player("p-3", "Cara", role="law_abiding")
    players = _roster(human, don, cara)

    messages = [
        _day_open_no_victim(),     # → day 1
        _announce(human, cara),    # human initiation — excluded
        _announce(don, cara),      # AI mafioso initiation — counted
    ]

    activity = score_vote_activity(messages, players)

    assert activity["by_side"] == {"law_abiding": 0, "mafia": 1}
    assert activity["by_day"] == {"day_1": 1}


def test_score_vote_activity_two_game_fold_sums_correctly() -> None:
    """A two-game fold sums the per-game by_side counts (the run_eval batch fold).

    ``run_eval`` folds per-game ``score_vote_activity`` results into one block by
    summing the side / day counts. We reproduce that fold over two independently
    scored games and assert the side totals add up, mirroring how the harness
    aggregates a batch.
    """
    # Game A: one law-abiding initiation on day 1, one mafia on day 2.
    cara_a = _player("p-1", "Cara", role="law_abiding")
    don_a = _player("p-2", "Don", role="mafia")
    game_a = [
        _day_open_no_victim(),
        _announce(cara_a, don_a),     # day 1: law-abiding
        _day_open_no_victim(),
        _announce(don_a, cara_a),     # day 2: mafia
    ]
    activity_a = score_vote_activity(game_a, _roster(cara_a, don_a))

    # Game B: two mafia initiations on day 1.
    eve_b = _player("p-1", "Eve", role="law_abiding")
    vito_b = _player("p-2", "Vito", role="mafia")
    game_b = [
        _day_open_victim(eve_b),
        _announce(vito_b, eve_b),     # day 1: mafia
        _announce(vito_b, eve_b),     # day 1: mafia again
    ]
    activity_b = score_vote_activity(game_b, _roster(eve_b, vito_b))

    # Fold the two by_side maps (the run_eval summation).
    folded = {
        side: activity_a["by_side"][side] + activity_b["by_side"][side]
        for side in ("law_abiding", "mafia")
    }

    assert activity_a["by_side"] == {"law_abiding": 1, "mafia": 1}
    assert activity_b["by_side"] == {"law_abiding": 0, "mafia": 2}
    assert folded == {"law_abiding": 1, "mafia": 3}


def test_score_vote_activity_marginal_sums_are_equal() -> None:
    """by_side and by_day are two marginals of one grand total → their sums are equal.

    ``sum(by_side.values()) == sum(by_day.values())`` — both partition the same
    set of counted initiations, just along different axes.
    """
    cara = _player("p-1", "Cara", role="law_abiding")
    don = _player("p-2", "Don", role="mafia")
    eve = _player("p-3", "Eve", role="law_abiding")
    players = _roster(cara, don, eve)

    messages = [
        _day_open_no_victim(),     # → day 1
        _announce(cara, don),      # day 1: law-abiding
        _announce(don, cara),      # day 1: mafia
        _day_open_victim(eve),     # → day 2
        _announce(eve, don),       # day 2: law-abiding
        _announce(don, eve),       # day 2: mafia
        _announce(don, cara),      # day 2: mafia again
    ]

    activity = score_vote_activity(messages, players)

    assert sum(activity["by_side"].values()) == sum(activity["by_day"].values())
    # Sanity: a non-trivial total (so the equality is not vacuous on 0).
    assert sum(activity["by_side"].values()) == 5


def test_score_vote_activity_ignores_non_system_and_unmatched_lines() -> None:
    """Non-System / unmatched lines never advance the day or count as an initiation.

    An ``AIMessage`` Day speech and a ``HumanMessage`` that happen to mention
    names, plus a ``SystemMessage`` matching no anchor, are all ignored — only the
    real day-open + announce pair around them scores.
    """
    cara = _player("p-1", "Cara", role="law_abiding")
    don = _player("p-2", "Don", role="mafia")
    players = _roster(cara, don)

    messages = [
        AIMessage(content="Day breaks. I think Don is mafia.", name="Cara"),  # AI, not a marker
        HumanMessage(content="Don has called for a vote? unsure"),  # not an announce
        SystemMessage(content="Night falls over the town."),  # matches no anchor
        _day_open_no_victim(),    # the real day-open → day 1
        _announce(don, cara),     # the real mafia initiation
    ]

    activity = score_vote_activity(messages, players)

    assert activity["by_side"] == {"law_abiding": 0, "mafia": 1}
    assert activity["by_day"] == {"day_1": 1}


# ===========================================================================
# C — render_record (both blocks, fixed key order, caveat, by_day: {}, round-trip)
# ===========================================================================


def _populated_result() -> EvalResult:
    """An ``EvalResult`` with populated outcomes + vote_activity + one metric."""
    return EvalResult(
        provider="ollama",
        outcomes=tally_outcomes(["law_abiding", "law_abiding", "mafia", "draw"]),
        vote_activity={
            "by_side": {"law_abiding": 1, "mafia": 3},
            "by_day": {"day_1": 2, "day_2": 2},
        },
        metrics={"repetition": {"rate": 0.5, "count": 1, "denominator": 2}},
    )


def test_render_record_both_blocks_in_fixed_key_order() -> None:
    """Both blocks render BETWEEN ``quality`` and ``metrics``; ``notes`` is LAST.

    The fixed top-level order is ``… quality → outcomes → vote_activity →
    metrics → notes``. We assert the top-level section headers appear in exactly
    that relative order.
    """
    doc = render_record(_populated_result(), _RUN_DATE)
    lines = doc.splitlines()

    quality_i = lines.index("quality:")
    outcomes_i = lines.index("outcomes:")
    vote_activity_i = lines.index("vote_activity:")
    metrics_i = lines.index("metrics:")
    notes_i = next(i for i, ln in enumerate(lines) if ln.startswith("notes:"))

    assert quality_i < outcomes_i < vote_activity_i < metrics_i < notes_i


def test_render_record_includes_immutable_outcomes_caveat() -> None:
    """The fixed passive-human caveat is rendered verbatim under ``outcomes.note``."""
    doc = render_record(_populated_result(), _RUN_DATE)

    # The caveat text appears verbatim in the document...
    assert _OUTCOMES_HUMAN_CAVEAT in doc
    # ...and round-trips as the outcomes block's ``note`` value (a single-quoted
    # scalar under the ``outcomes`` mapping), not the top-level ``notes`` field.
    parsed = yaml.safe_load(doc)
    assert parsed["outcomes"]["note"] == _OUTCOMES_HUMAN_CAVEAT


def test_render_record_explicit_zero_renders_literal_empty_by_day_map() -> None:
    """The explicit-zero case renders the literal ``by_day: {}`` — not an omitted key.

    A silent run (zero initiations) must still emit ``by_side`` with both keys at
    0 and ``by_day`` as the present-but-empty flow map ``{}``, so the explicit-zero
    guarantee survives into the rendered record.
    """
    result = EvalResult(
        provider="ollama",
        outcomes=tally_outcomes([None, None]),
        vote_activity={"by_side": {"law_abiding": 0, "mafia": 0}, "by_day": {}},
    )

    doc = render_record(result, _RUN_DATE)

    assert "  by_day: {}" in doc
    # by_side still carries both explicit-zero keys.
    assert "    law_abiding: 0" in doc
    assert "    mafia: 0" in doc


def test_render_record_round_trips_through_yaml_safe_load() -> None:
    """The rendered document round-trips through ``yaml.safe_load`` faithfully.

    Including the empty ``by_day`` map and the numerically-sorted day keys: the
    parsed mapping must carry the exact outcomes / vote_activity shapes the
    scorers produced, proving the hand-rendered YAML is valid data-only YAML.
    """
    result = EvalResult(
        provider="ollama",
        outcomes=tally_outcomes(["law_abiding", "mafia", "draw", None]),
        vote_activity={
            "by_side": {"law_abiding": 0, "mafia": 0},
            "by_day": {},
        },
        metrics={"repetition": {"rate": 0.25, "count": 1, "denominator": 4}},
    )

    doc = render_record(result, _RUN_DATE)
    parsed = yaml.safe_load(doc)

    # The empty by_day map round-trips as an empty dict, not None or absent.
    assert parsed["vote_activity"]["by_day"] == {}
    assert parsed["vote_activity"]["by_side"] == {"law_abiding": 0, "mafia": 0}
    # Outcomes survived intact (counts + the caveat note).
    assert parsed["outcomes"]["games"] == 4
    assert parsed["outcomes"]["law_abiding"]["wins"] == 1
    assert parsed["outcomes"]["mafia"]["wins"] == 1
    assert parsed["outcomes"]["draw"] == 1
    assert parsed["outcomes"]["no_winner"] == 1
    assert parsed["outcomes"]["note"] == _OUTCOMES_HUMAN_CAVEAT


def test_render_record_round_trips_numerically_sorted_day_keys() -> None:
    """A non-empty by_day round-trips with ``day_N`` keys sorted by integer suffix.

    The ``day_10`` key must follow ``day_2`` (numeric sort), not precede it
    (lexical sort) — the rendered lines are in numeric order and re-parse to the
    same mapping.
    """
    result = EvalResult(
        provider="ollama",
        outcomes=tally_outcomes(["mafia"]),
        vote_activity={
            "by_side": {"law_abiding": 0, "mafia": 3},
            "by_day": {"day_10": 1, "day_2": 2},
        },
    )

    doc = render_record(result, _RUN_DATE)
    lines = doc.splitlines()

    # day_2 is rendered before day_10 (numeric, not lexical, order).
    day2_i = next(i for i, ln in enumerate(lines) if ln.strip() == "day_2: 2")
    day10_i = next(i for i, ln in enumerate(lines) if ln.strip() == "day_10: 1")
    assert day2_i < day10_i

    parsed = yaml.safe_load(doc)
    assert parsed["vote_activity"]["by_day"] == {"day_2": 2, "day_10": 1}


def test_render_record_bare_result_omits_both_blocks() -> None:
    """A bare ``EvalResult`` (no outcomes / vote_activity) still renders, omitting both.

    The two blocks are only emitted when the run actually produced them; a
    synthetic result without them simply skips the sections (no crash), while the
    surrounding ``quality`` / ``metrics`` / ``notes`` still render.
    """
    result = EvalResult(provider="ollama")

    doc = render_record(result, _RUN_DATE)

    assert "outcomes:" not in doc
    assert "vote_activity:" not in doc
    # The record is still a valid, parseable document with the core sections.
    assert "quality:" in doc
    assert "metrics:" in doc
    assert doc.rstrip().endswith("notes: ''")
    parsed = yaml.safe_load(doc)
    assert "outcomes" not in parsed
    assert "vote_activity" not in parsed


# ===========================================================================
# D — eval_ledger flatten + viewer (zero-vs-absent, detail sections, search, Pilot)
# ===========================================================================
#
# Fixtures use the REAL on-disk record shape. The spec-013 blocks are rendered by
# ``render_record`` into the new-shape docs; pre-013 records simply omit them.

# A new-shape (spec-013) record: both game-dynamics blocks present, with non-zero
# outcome rates and vote counts. Built so ``_winner_keyword`` derives 'law_abiding'
# (3 of 5 completed games → strict majority).
_NEW_OUTCOMES_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-15'
      duration_seconds: 120.0
      metrics_version: 1
    code:
      commit: 'cafe0000babe1111cafe2222babe3333cafe4444'
      branch: 'main'
      dirty: false
    provider:
      name: 'ollama'
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
    settings:
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
      games: 5
      seed: null
    quality:
      games_attempted: 5
      games_completed: 5
      games_failed_early: 0
    outcomes:
      games: 5
      law_abiding:
        wins: 3
        rate: 0.6
        ci_low: 0.23659352256015563
        ci_high: 0.8795518147273414
      mafia:
        wins: 1
        rate: 0.2
        ci_low: 0.03623386708831742
        ci_high: 0.6244233735782268
      draw: 0
      no_winner: 1
      note: 'win-rate is measured against a passive scripted human.'
    vote_activity:
      by_side:
        law_abiding: 2
        mafia: 4
      by_day:
        day_1: 3
        day_2: 3
    metrics:
      repetition:
        rate: 0.5
        count: 10
        denominator: 20
    notes: 'spec-013 outcome tracking baseline'
    """
)

# A new-shape record whose vote_activity is PRESENT-but-ZERO (the Nova-silent-Day
# pathology). by_day is the literal empty map; outcomes is all no_winner (games==4).
_NEW_ZERO_ACTIVITY_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-14'
      duration_seconds: 90.0
      metrics_version: 1
    code:
      commit: '1111aaaa2222bbbb3333cccc4444dddd5555eeee'
      branch: 'main'
      dirty: false
    provider:
      name: 'bedrock'
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
    settings:
      large_model: 'amazon.nova-pro-v1:0'
      small_model: 'amazon.nova-lite-v1:0'
      games: 4
      seed: null
    quality:
      games_attempted: 4
      games_completed: 4
      games_failed_early: 0
    outcomes:
      games: 4
      law_abiding:
        wins: 0
        rate: 0.0
        ci_low: 0.0
        ci_high: 0.49003708518808635
      mafia:
        wins: 0
        rate: 0.0
        ci_low: 0.0
        ci_high: 0.49003708518808635
      draw: 0
      no_winner: 4
      note: 'win-rate is measured against a passive scripted human.'
    vote_activity:
      by_side:
        law_abiding: 0
        mafia: 0
      by_day: {}
    metrics:
      repetition:
        rate: 0.5
        count: 5
        denominator: 10
    notes: 'silent-day run, no initiations'
    """
)

# A PRE-013 record: NO outcomes block, NO vote_activity block (the early shape).
_PRE_013_DOC = textwrap.dedent(
    """\
    run:
      date: '2026-06-13'
      games: 3
      metrics_version: 1
    provider:
      name: 'ollama'
      large_model: 'qwen3-coder:30b'
      small_model: 'qwen2.5:3b'
    quality:
      games_attempted: 3
      games_completed: 3
      games_failed_early: 0
    metrics:
      repetition:
        rate: 0.45384615384615384
        count: 59
        denominator: 130
    notes: 'pre-013 record, no game-dynamics blocks'
    """
)


def _write_ledger(tmp_path: Path, *docs: str, name: str = "ledger.yaml") -> Path:
    """Write a ``---``-separated multi-document ledger from raw doc bodies."""
    text = "".join(f"---\n{doc}" for doc in docs)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _col(model, label: str) -> int:
    """Index of the column with header ``label`` (resolved by name, not position)."""
    return model.columns.index(label)


def test_ledger_flatten_formats_both_game_dynamics_cells(tmp_path: Path) -> None:
    """A new-shape record fills the ``Wins (LA/M)`` and ``Votes (LA/M)`` cells.

    Wins shows the compact leading-dot pair (``LA .60 / M .20``); Votes shows the
    explicit-zero-capable ``LA 2 / M 4`` pair.
    """
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _NEW_OUTCOMES_DOC)))
    (row,) = model.rows

    assert row[_col(model, "Wins (LA/M)")] == "LA .60 / M .20"
    assert row[_col(model, "Votes (LA/M)")] == "LA 2 / M 4"


def test_ledger_present_zero_vote_cell_is_la_0_m_0(tmp_path: Path) -> None:
    """A vote_activity-PRESENT-but-zero record renders the ``Votes`` cell ``LA 0 / M 0``.

    The explicit-zero guarantee carried into the viewport: a present block with
    both counts at 0 must render the visible ``LA 0 / M 0``, never a blank.
    """
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _NEW_ZERO_ACTIVITY_DOC)))
    (row,) = model.rows

    assert row[_col(model, "Votes (LA/M)")] == "LA 0 / M 0"


def test_ledger_pre_013_record_blanks_both_cells(tmp_path: Path) -> None:
    """A pre-013 record (no blocks) → both game-dynamics cells BLANK, no raise."""
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _PRE_013_DOC)))
    (row,) = model.rows

    assert row[_col(model, "Wins (LA/M)")] == ""
    assert row[_col(model, "Votes (LA/M)")] == ""


def test_ledger_zero_vs_absent_vote_cells_render_differently(tmp_path: Path) -> None:
    """THE DISTINCTION: present-zero (``LA 0 / M 0``) ≠ absent (``""``) for Votes.

    The whole point of the explicit-zero guarantee — a silent-but-present run and
    a pre-013 run that never measured votes must render the ``Votes`` cell
    DIFFERENTLY, so the viewport never conflates "AI was silent" with "not
    measured".
    """
    model = build_table_model(
        load_ledger(_write_ledger(tmp_path, _NEW_ZERO_ACTIVITY_DOC, _PRE_013_DOC))
    )
    votes_col = _col(model, "Votes (LA/M)")
    present_zero_cell = model.rows[0][votes_col]
    absent_cell = model.rows[1][votes_col]

    assert present_zero_cell == "LA 0 / M 0"
    assert absent_cell == ""
    assert present_zero_cell != absent_cell


def test_ledger_pre_013_wins_vs_present_outcomes_render_differently(
    tmp_path: Path,
) -> None:
    """The Wins cell likewise distinguishes a present outcomes block from an absent one."""
    model = build_table_model(
        load_ledger(_write_ledger(tmp_path, _NEW_OUTCOMES_DOC, _PRE_013_DOC))
    )
    wins_col = _col(model, "Wins (LA/M)")
    present_cell = model.rows[0][wins_col]
    absent_cell = model.rows[1][wins_col]

    assert present_cell == "LA .60 / M .20"
    assert absent_cell == ""
    assert present_cell != absent_cell


def test_render_detail_shows_both_game_dynamics_sections(tmp_path: Path) -> None:
    """``render_detail`` lays out both blocks: outcomes (rates + CI) and vote_activity.

    Outcomes shows full-precision per-side ``wins`` + ``rate`` + a ``[lo–hi]``
    band, the bare draw/no_winner counts and the caveat note; vote_activity shows
    the explicit both-side by_side and the sorted by_day lines. Both sit between
    ``quality`` and ``metrics``.
    """
    (record,) = load_ledger(_write_ledger(tmp_path, _NEW_OUTCOMES_DOC))

    text = render_detail(record)
    lines = text.splitlines()

    # Both section headers are present and in the canonical order.
    assert "outcomes" in lines
    assert "vote_activity" in lines
    assert lines.index("quality") < lines.index("outcomes")
    assert lines.index("outcomes") < lines.index("vote_activity")
    assert lines.index("vote_activity") < lines.index("metrics")

    # Outcomes: per-side wins + full-precision rate + CI band, the counts, caveat.
    # The rate line carries the full-precision rate then a ``[lo–hi]`` band; the
    # CI digits re-render through ``repr(float(...))`` so we anchor on the rate +
    # the band structure (the en-dash separator) rather than the last float digit.
    assert "  games: 5" in text
    assert "    wins: 3" in text
    law_abiding_rate_line = next(
        ln for ln in lines if ln.strip().startswith("rate: 0.6")
    )
    assert law_abiding_rate_line.strip().startswith("rate: 0.6 [")
    assert "–" in law_abiding_rate_line  # the en-dash CI band separator
    assert law_abiding_rate_line.strip().endswith("]")
    assert "  draw: 0" in text
    assert "  no_winner: 1" in text
    assert "passive scripted human" in text

    # vote_activity: both sides always; by_day sorted.
    assert "    law_abiding: 2" in text
    assert "    mafia: 4" in text
    assert "    day_1: 3" in text
    assert "    day_2: 3" in text


def test_render_detail_present_zero_vote_activity_shows_explicit_sides(
    tmp_path: Path,
) -> None:
    """A present-but-zero vote_activity shows both sides at 0 and a ``(none)`` by_day.

    The explicit-zero guarantee in the drill-down: both sides print ``0`` and the
    empty by_day shows ``(none)`` so "present but no per-day activity" stays
    distinct from an absent block.
    """
    (record,) = load_ledger(_write_ledger(tmp_path, _NEW_ZERO_ACTIVITY_DOC))

    text = render_detail(record)

    assert "    law_abiding: 0" in text
    assert "    mafia: 0" in text
    assert "    (none)" in text


def test_render_detail_pre_013_collapses_both_blocks_to_dash(tmp_path: Path) -> None:
    """A pre-013 record drills down with both blocks collapsed to a single ``—`` line.

    The absent-block pattern (mirroring ``code``/``settings`` on an old record):
    each whole-absent game-dynamics block renders one ``—`` line, and no
    ``KeyError`` escapes.
    """
    (record,) = load_ledger(_write_ledger(tmp_path, _PRE_013_DOC))

    text = render_detail(record)  # must not raise

    assert "outcomes\n  —" in text
    assert "vote_activity\n  —" in text


def test_winner_scoped_search_matches_derived_majority_keyword(
    tmp_path: Path,
) -> None:
    """The ``winner`` scoped search matches the record's derived majority-side keyword.

    The new-outcomes fixture has 3 law-abiding wins of 5 completed games — a
    strict majority — so its derived ``winner`` keyword is 'law_abiding' and a
    ``winner``-scoped 'law_abiding' value keeps it while 'mafia' drops it.
    """
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _NEW_OUTCOMES_DOC)))
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert fields["winner"] == "law_abiding"
    assert row_matches_field("winner", "law_abiding", blob, fields) is True
    assert row_matches_field("winner", "mafia", blob, fields) is False


def test_winner_scoped_search_is_empty_for_pre_013_record(tmp_path: Path) -> None:
    """A pre-013 record (no outcomes) has an empty ``winner`` field — neither matches."""
    model = build_table_model(load_ledger(_write_ledger(tmp_path, _PRE_013_DOC)))
    (blob,) = model.search_blobs
    (fields,) = model.search_fields

    assert fields["winner"] == ""
    # An empty value field matches nothing concrete...
    assert row_matches_field("winner", "law_abiding", blob, fields) is False
    assert row_matches_field("winner", "mafia", blob, fields) is False


# ---------------------------------------------------------------------------
# D (Pilot) — the Textual viewer over a mixed new/old ledger.
# ---------------------------------------------------------------------------


async def test_viewer_shows_both_new_columns_over_mixed_ledger(tmp_path: Path) -> None:
    """A mixed new/old ledger → the two new column headers appear; rows populate.

    Drives the viewer through ``run_test`` (Pilot) and asserts via the widget API
    — the DataTable carries the ``Wins (LA/M)`` and ``Votes (LA/M)`` headers and a
    row per record — never against rendered bytes. The ledger path is injected via
    the app constructor → ``tmp_path``.
    """
    ledger = _write_ledger(
        tmp_path, _NEW_OUTCOMES_DOC, _PRE_013_DOC, _NEW_ZERO_ACTIVITY_DOC
    )
    expected = build_table_model(load_ledger(ledger))

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)

        assert table.row_count == 3
        header_labels = [str(col.label) for col in table.columns.values()]
        assert "Wins (LA/M)" in header_labels
        assert "Votes (LA/M)" in header_labels
        assert header_labels == expected.columns

        # The new row's Votes cell carries the formatted pair (model→cell fidelity).
        votes_col = header_labels.index("Votes (LA/M)")
        first_votes = table.get_cell_at((0, votes_col))
        plain = first_votes.plain if hasattr(first_votes, "plain") else str(first_votes)
        assert plain == "LA 2 / M 4"

    assert app.is_running is False


async def test_viewer_drilldown_new_row_shows_both_sections(tmp_path: Path) -> None:
    """Drill-down on a NEW row → the DetailScreen shows both game-dynamics sections.

    The new-outcomes record is the first row; opening its detail via the real
    RowSelected path must show the outcomes (with its caveat) and vote_activity
    (with both sides) sections in the body.
    """
    ledger = _write_ledger(tmp_path, _NEW_OUTCOMES_DOC, _PRE_013_DOC, name="drill.yaml")

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        table.focus()
        await pilot.pause()
        assert table.cursor_row == 0  # the new-outcomes record

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        body = plain_text(app.screen.query_one("#detail-body", Static))
        # The two game-dynamics sections are present in the drill-down body.
        assert "outcomes" in body
        assert "vote_activity" in body
        assert "passive scripted human" in body
        assert "law_abiding: 2" in body
        assert "mafia: 4" in body

    assert app.is_running is False


async def test_viewer_drilldown_pre_013_row_shows_dash_no_crash(tmp_path: Path) -> None:
    """Drill-down on a PRE-013 row → both blocks show ``—``; the viewer does not crash.

    The pre-013 record is the second row; opening its detail must show the absent
    game-dynamics blocks collapsed to ``—`` and leave the app running cleanly.
    """
    ledger = _write_ledger(
        tmp_path, _NEW_OUTCOMES_DOC, _PRE_013_DOC, name="drill-old.yaml"
    )

    app = LedgerViewerApp(path=ledger)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#ledger-table", DataTable)
        table.focus()
        await pilot.pause()

        # Move to the pre-013 row (index 1) and drill in.
        await pilot.press("down")
        assert table.cursor_row == 1
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        body = plain_text(app.screen.query_one("#detail-body", Static))
        # Both absent blocks collapse to the em-dash placeholder.
        assert "outcomes\n  —" in body
        assert "vote_activity\n  —" in body

    # The app shut down cleanly — no crash on the pre-013 drill-down.
    assert app.is_running is False
