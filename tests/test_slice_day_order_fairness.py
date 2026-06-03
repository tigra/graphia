"""Spec 007 (Fair Day Speaking Order): fairness of ``_shuffle_order``.

``graphia.nodes.day._shuffle_order`` is the single producer of the Day
speaking order. It returns the alive players' ids ``random.shuffle``-d,
reading only ``PlayerState.id`` and ``PlayerState.is_alive`` — never
``role`` or ``is_human``. This module locks that fairness down on three
axes:

(a) **Role/type independence (exact, deterministic).** The order produced
    for a given alive-id set under a given seed must NOT depend on which
    players are mafia / law-abiding / human. We prove this by holding the
    ids + alive flags fixed, reassigning ``role`` and ``is_human`` to
    *different* ids, and asserting the full sequence of orders over many
    seeds is byte-identical. This is exact equality, not sampling.

(b) **Uniformity (statistical, within tolerance).** Over a large seeded
    sample, every player must land in each list position about ``N/7`` of
    the time, with no role or player-type advantage.

(c) **Survivors.** The same uniformity holds among the still-living as
    players are eliminated (5 alive, then 3 alive).

Determinism posture (architecture §6, and the precedent in
``test_dual_mode_smoke.py``): ALL randomness flows from one stable
``BASE_SEED`` declared in this module, driven in-test via ``random.Random``
/ ``random.seed``. There is NO env var, NO ``GRAPHIA_SEED``, NO production
seed — the production code uses the module-global ``random`` API directly,
so seeding it in-test before each ``_shuffle_order`` call fully pins the
trajectory and makes the statistical assertions reproducible run-to-run.

No Bedrock / AWS is touched: this exercises a pure stdlib helper only, so
the autouse ``safe_llm`` net is trivially satisfied (no LLM is called).
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from graphia.nodes.day import _shuffle_order
from graphia.state import PlayerState

# --------------------------------------------------------------------------
# Determinism knobs.
#
# BASE_SEED is the single stable root. The multi-sample harness derives N
# child seeds from a ``random.Random(BASE_SEED)`` master, then for each child
# calls ``random.seed(child)`` immediately before invoking ``_shuffle_order``
# — production reads the module-global ``random`` state, so this fixes every
# shuffle.
#
# N / tolerance choice for the uniformity tests:
#   N = 7000 samples over 7 positions => expected count per (player, position)
#   is N/7 = 1000. The shuffle is a permutation, so for a fixed position the
#   per-player count is Binomial(N, 1/p) where p = number of alive players;
#   for p == 7 the standard deviation is sqrt(N * (1/7) * (6/7)) ≈ 29.3, so a
#   tolerance of 0.12 * (N/p) = 120 is ~4 sigma — comfortably wider than the
#   natural spread of the (seeded, fixed) sample yet far tighter than the
#   ~143-count gap a 1-in-8 skew would create. The sample is fully
#   deterministic given BASE_SEED, so this passes identically on every run;
#   the sigma framing only documents why the band is neither flaky-tight nor
#   meaninglessly-loose. The smaller-population survivor checks reuse the same
#   0.12 relative tolerance against their own N/p baseline.
# --------------------------------------------------------------------------
BASE_SEED = 20250607
N_SAMPLES = 7000
RELATIVE_TOLERANCE = 0.12


# --------------------------------------------------------------------------
# Player-set builders.
# --------------------------------------------------------------------------


def _make_players(
    *,
    mafia_ids: set[str],
    human_id: str,
    alive_ids: set[str],
    all_ids: list[str],
) -> dict[str, PlayerState]:
    """Build an insertion-ordered ``players`` dict over ``all_ids``.

    ``role`` is ``"mafia"`` for ids in ``mafia_ids`` else ``"law_abiding"``;
    ``is_human`` is True only for ``human_id``; ``is_alive`` is True only for
    ids in ``alive_ids``. Names are derived from the id so they are stable
    and unique but never read by ``_shuffle_order``.
    """
    players: dict[str, PlayerState] = {}
    for pid in all_ids:
        players[pid] = PlayerState(
            id=pid,
            name=f"Player-{pid}",
            role="mafia" if pid in mafia_ids else "law_abiding",
            is_human=(pid == human_id),
            is_alive=(pid in alive_ids),
        )
    return players


SEVEN_IDS = [f"p{i}" for i in range(7)]


# --------------------------------------------------------------------------
# The seeded multi-sample harness — pure and reusable.
# --------------------------------------------------------------------------


def _child_seeds(base_seed: int, n: int) -> list[int]:
    """Derive ``n`` child seeds deterministically from ``base_seed``."""
    master = random.Random(base_seed)
    return [master.randrange(2**31) for _ in range(n)]


def _orders_over_seeds(
    players: dict[str, PlayerState], child_seeds: list[int]
) -> list[list[str]]:
    """Return the order ``_shuffle_order`` yields under each child seed.

    For each child seed: ``random.seed(child)`` then call ``_shuffle_order``.
    Production mutates the global ``random`` state, so seeding immediately
    before each call makes the result a pure function of (seed, alive ids).
    """
    orders: list[list[str]] = []
    for seed in child_seeds:
        random.seed(seed)
        orders.append(_shuffle_order(players))
    return orders


def _position_counts(
    players: dict[str, PlayerState], child_seeds: list[int]
) -> dict[str, Counter[int]]:
    """Accumulate ``counts[player_id][position]`` over the seeded sample."""
    counts: dict[str, Counter[int]] = {
        pid: Counter() for pid, p in players.items() if p.is_alive
    }
    for order in _orders_over_seeds(players, child_seeds):
        for position, pid in enumerate(order):
            counts[pid][position] += 1
    return counts


# --------------------------------------------------------------------------
# (a) Role/type independence — exact equality, no sampling.
# --------------------------------------------------------------------------


def test_order_ignores_role_and_human_flag_exactly() -> None:
    """The order is a pure function of ids + alive-ness, not role/is_human.

    Two 7-player sets share the SAME ids and alive flags but assign
    ``role`` and ``is_human`` to DIFFERENT ids. Driven under the SAME child
    seeds, their full order sequences must be byte-identical — proving
    ``_shuffle_order`` never reads ``role`` / ``is_human``.
    """
    all_ids = list(SEVEN_IDS)
    alive = set(all_ids)
    seeds = _child_seeds(BASE_SEED, 500)

    # Set A: mafia = {p0, p1}, human = p0.
    players_a = _make_players(
        mafia_ids={"p0", "p1"},
        human_id="p0",
        alive_ids=alive,
        all_ids=all_ids,
    )
    # Set B: same ids/alive, but mafia = {p5, p6}, human = p3.
    players_b = _make_players(
        mafia_ids={"p5", "p6"},
        human_id="p3",
        alive_ids=alive,
        all_ids=all_ids,
    )

    # Sanity: the two sets genuinely differ on the ignored attributes.
    assert {pid for pid, p in players_a.items() if p.role == "mafia"} != {
        pid for pid, p in players_b.items() if p.role == "mafia"
    }
    assert next(pid for pid, p in players_a.items() if p.is_human) != next(
        pid for pid, p in players_b.items() if p.is_human
    )

    orders_a = _orders_over_seeds(players_a, seeds)
    orders_b = _orders_over_seeds(players_b, seeds)

    assert orders_a == orders_b, (
        "Day speaking order diverged when only role/is_human changed — "
        "_shuffle_order must depend solely on ids + alive-ness."
    )
    # Guard against a degenerate match on a constant order: the shuffle must
    # actually produce variety across seeds.
    assert len({tuple(o) for o in orders_a}) > 1, (
        "expected the shuffle to produce more than one distinct order"
    )


# --------------------------------------------------------------------------
# (b) Uniformity — per-player, per-role, per-type, within tolerance.
# --------------------------------------------------------------------------


def _assert_uniform_per_player(
    counts: dict[str, Counter[int]], n_alive: int, n_samples: int
) -> None:
    """Every alive player lands in every position within tolerance of N/p."""
    expected = n_samples / n_alive
    band = RELATIVE_TOLERANCE * expected
    assert len(counts) == n_alive
    for pid, position_counter in counts.items():
        for position in range(n_alive):
            observed = position_counter[position]
            assert abs(observed - expected) <= band, (
                f"player {pid} at position {position}: observed {observed}, "
                f"expected ~{expected:.0f} (±{band:.0f})"
            )


def test_uniformity_seven_alive_per_player() -> None:
    """With 7 alive, each player occupies each of the 7 slots ~N/7 times."""
    players = _make_players(
        mafia_ids={"p0", "p1"},
        human_id="p2",
        alive_ids=set(SEVEN_IDS),
        all_ids=list(SEVEN_IDS),
    )
    seeds = _child_seeds(BASE_SEED, N_SAMPLES)
    counts = _position_counts(players, seeds)
    _assert_uniform_per_player(counts, n_alive=7, n_samples=N_SAMPLES)


def test_uniformity_aggregated_by_role_and_type() -> None:
    """Per-position share is proportional to a group's alive population.

    Aggregating the per-player position counts by role and by player-type
    must yield, for every position, a group share proportional to how many
    alive members the group has: each of the 7 positions should receive
    ``N`` total placements, split among the group in proportion to its size.
    The human (one player) should see the same per-position rate as any
    single AI.
    """
    players = _make_players(
        mafia_ids={"p0", "p1"},
        human_id="p2",
        alive_ids=set(SEVEN_IDS),
        all_ids=list(SEVEN_IDS),
    )
    seeds = _child_seeds(BASE_SEED, N_SAMPLES)
    counts = _position_counts(players, seeds)

    per_player_expected = N_SAMPLES / 7
    band = RELATIVE_TOLERANCE * per_player_expected

    mafia_ids = {pid for pid, p in players.items() if p.role == "mafia"}
    law_ids = {pid for pid, p in players.items() if p.role == "law_abiding"}
    human_id = next(pid for pid, p in players.items() if p.is_human)
    # A comparable single AI to contrast against the single human.
    comparison_ai_id = next(
        pid for pid, p in players.items() if not p.is_human
    )

    for position in range(7):
        mafia_share = sum(counts[pid][position] for pid in mafia_ids)
        law_share = sum(counts[pid][position] for pid in law_ids)
        # Group total at a position == group size * per-player expectation.
        assert abs(mafia_share - len(mafia_ids) * per_player_expected) <= (
            len(mafia_ids) * band
        ), f"mafia share skewed at position {position}: {mafia_share}"
        assert abs(law_share - len(law_ids) * per_player_expected) <= (
            len(law_ids) * band
        ), f"law-abiding share skewed at position {position}: {law_share}"
        # Total placements at any position equals the sample size.
        assert mafia_share + law_share == N_SAMPLES

        # Human's per-position rate matches a comparable single AI's.
        human_rate = counts[human_id][position]
        ai_rate = counts[comparison_ai_id][position]
        assert abs(human_rate - ai_rate) <= 2 * band, (
            f"human vs AI per-position rate diverged at position {position}: "
            f"human={human_rate}, ai={ai_rate}"
        )


# --------------------------------------------------------------------------
# (c) Survivors — fairness holds among the still-living as players die.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alive_ids", "n_alive"),
    [
        ({"p0", "p1", "p2", "p3", "p4"}, 5),
        ({"p0", "p3", "p6"}, 3),
    ],
)
def test_uniformity_holds_among_survivors(
    alive_ids: set[str], n_alive: int
) -> None:
    """As players are eliminated, the survivors still rotate fairly.

    The dead are kept in the ``players`` dict (only their ``is_alive`` flag
    is cleared) to mirror how the game eliminates players in place; only the
    alive subset should ever appear in the order, each landing in each of the
    ``n_alive`` positions ~N/n_alive times.
    """
    players = _make_players(
        mafia_ids={"p0", "p1"},
        human_id="p2",
        alive_ids=alive_ids,
        all_ids=list(SEVEN_IDS),
    )
    seeds = _child_seeds(BASE_SEED, N_SAMPLES)

    # The order must contain exactly the alive ids, every time.
    for order in _orders_over_seeds(players, seeds[:50]):
        assert set(order) == alive_ids
        assert len(order) == n_alive

    counts = _position_counts(players, seeds)
    _assert_uniform_per_player(counts, n_alive=n_alive, n_samples=N_SAMPLES)
