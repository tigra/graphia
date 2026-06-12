"""Spec 007 (Fair Day Speaking Order): end-to-end fairness on the REAL graph.

The chokepoint module ``test_slice_day_order_fairness.py`` already locks down
``graphia.nodes.day._shuffle_order`` in isolation with high-N statistical
weight. This module is the complementary *wiring-level* sanity check: it drives
the actual compiled graph (``build_graph``, local mode) through Day rounds and
asserts that the speaking order *as it emerges from a running game* is fair —
uniform across list positions and independent of role and human/AI type — on
the ``day_order`` channel the game itself produces.

Why this is distinct from the chokepoint test
----------------------------------------------
The chokepoint test calls ``_shuffle_order(players)`` directly. Here we never
touch that helper by hand: ``day_open`` seeds the Day-1 order and ``day_turn``
reshuffles at every round boundary, both via the real node path. If a future
refactor stopped routing the Day order through ``_shuffle_order`` (or started
biasing it by role / human flag), the chokepoint test could still pass while
this one would catch the regression in the assembled game.

Sampling strategy (documented per the task)
--------------------------------------------
We play each game's **Day 1 to its natural close** (6 speaking rounds with no
execution) rather than a fixed first-order snapshot, because the Day loop hands
us a *fresh* ``day_order`` at ``day_open`` AND at every round-boundary reshuffle
inside ``day_turn`` — so one cheap Day yields ~``DAY_MAX_ROUNDS`` order samples
straight from the real node path, with no extra Nights/Days to drive.

- ``M = 120`` games. After Night 1 (one Law-abiding AI is killed by the
  dynamic-pointing fake) exactly 6 players are alive (2 Mafia + 4 Law-abiding),
  so every captured order is over the same population of 6 — letting us
  aggregate a clean 6x6 position-count matrix. 120 games x ~7 distinct orders
  (1 at day_open + up to 6 reshuffles) gives ~800+ placements over 6 positions,
  i.e. ~130-140 expected per (player, position) — plenty for a wiring-level
  uniformity check. The whole sweep runs in well under a second (the LLM is a
  trivial in-process fake), so it stays save-on-run fast.
- ``RELATIVE_TOLERANCE = 0.45`` — deliberately loose. This is a wiring check on
  a modest sample, not the high-N statistical test (that lives in the
  chokepoint module). The band only needs to be tight enough to catch a gross
  positional or role bias (e.g. one role always speaking first) while never
  flaking on the natural spread of a ~130-count cell. The whole run is fully
  deterministic given ``BASE_SEED``, so it passes identically every run.

Determinism (architecture section 6, and the chokepoint module's precedent):
ALL randomness flows from one stable ``BASE_SEED`` declared here. We derive M
child seeds from a ``random.Random(BASE_SEED)`` master and call
``random.seed(child)`` immediately before building+driving each game — the
production role deal, Night pointing fallbacks, and every ``_shuffle_order``
read the module-global ``random`` state, so this pins the whole trajectory.
There is NO env var, NO ``GRAPHIA_SEED``.

Bedrock is never reached: ``fake_small`` scripts the roster, an unbounded
"always speak" large-model fake serves every AI Day turn, and the dynamic Night
pointing fake resolves a live target — so the autouse ``safe_llm`` net is
satisfied at every call site.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.types import Command

from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import DayAction, Pointing
from graphia.nodes import DAY_MAX_ROUNDS

AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"

DAY_CLOSE_NO_EXEC_LINE = "The Day ends with no one executed."

# --------------------------------------------------------------------------
# Determinism + sample-size knobs (see module docstring for the rationale).
# --------------------------------------------------------------------------
BASE_SEED = 20250607
M_GAMES = 120
# After Night 1 exactly 6 players survive every game (one Law-abiding AI dies).
N_ALIVE = 6
RELATIVE_TOLERANCE = 0.45


# --------------------------------------------------------------------------
# An unbounded "always speak" large-model fake.
#
# The unified ``fake_large`` fixture serves a *finite* DayAction queue (then
# replays the last item). That works, but to make the intent unmistakable and
# robust across an unknown number of AI turns over M games we use a tiny
# stateless fake that yields a generic speak action for ANY number of calls and
# resolves Night pointing against live graph state (so no real target id needs
# to be known ahead of time). It satisfies BOTH the day and night get_large
# call sites with one object.
# --------------------------------------------------------------------------


class _AlwaysSpeakLarge:
    """Stateless large-model stand-in: AIs always speak; Night points at a live target.

    Dispatches on the schema bound via ``with_structured_output`` (mirrors the
    production call shape ``get_large().with_structured_output(S).invoke(m)``):

    - ``DayAction`` -> ``DayAction(kind="speak", ...)`` every time, so no AI
      ever initiates a vote and the Day runs cleanly to its round cap.
    - ``Pointing`` -> the first alive Law-abiding non-human, resolved from live
      graph state at invoke time (no pre-known UUIDs, no queue to exhaust).
    """

    def __init__(self, state_provider: Callable[[], dict]) -> None:
        self._state_provider = state_provider
        self._bound_schema: type | None = None
        self.call_count = 0

    def with_structured_output(self, schema: type) -> "_AlwaysSpeakLarge":
        self._bound_schema = schema
        return self

    def invoke(self, messages: Any) -> Any:
        self.call_count += 1
        if self._bound_schema is DayAction:
            return DayAction(kind="speak", text="I am still weighing things.")
        if self._bound_schema is Pointing:
            players = self._state_provider().get("players", {})
            candidates = [
                p.id
                for p in players.values()
                if p.is_alive and p.role == "law_abiding" and not p.is_human
            ]
            if not candidates:
                candidates = [p.id for p in players.values() if p.is_alive]
            return Pointing(target_id=candidates[0])
        raise AssertionError(
            f"_AlwaysSpeakLarge got an unexpected schema: {self._bound_schema!r}"
        )


# --------------------------------------------------------------------------
# Driving helpers (mirrors the conventions in test_slice6_day / test_slice7).
# --------------------------------------------------------------------------


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _drive(graph, run_config, payload) -> None:
    """Stream with ``payload`` until the next pause/interrupt.

    Cap ``recursion_limit`` so a single ``stream`` call can never spin forever
    on the post-Slice-8 Day<->Night loop. A single Day with no human in the
    first speaker slots can take ~all-6-AI-turns x 6 rounds plus the setup/vote
    plumbing, so the cap is generous (200). If it is ever hit, the last
    completed super-step is still checkpointed and readable, so we swallow the
    error and let the caller inspect state — the capture loop's own stop
    conditions (Day closed / no ``.next``) are the real exit.
    """
    from langgraph.errors import GraphRecursionError

    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 200)
    try:
        for _ in graph.stream(payload, bounded, stream_mode="updates"):
            pass
    except GraphRecursionError:
        return


def _day_order(graph, run_config) -> list[str]:
    return list(graph.get_state(run_config).values.get("day_order", []))


def _day_closed(graph, run_config) -> bool:
    state = graph.get_state(run_config).values
    return any(
        DAY_CLOSE_NO_EXEC_LINE in getattr(m, "content", "")
        for m in state.get("messages", [])
    )


def _capture_day1_orders(graph, run_config) -> list[list[str]]:
    """Drive a single game through Day 1; return every distinct ``day_order``.

    The graph is paused on the ``name`` interrupt when this is called. We resume
    with the human name (roles assigned, Night 1 runs via the dynamic pointing
    fake), then repeatedly answer the human's ``day_turn`` interrupt with a
    generic speech. AIs always speak (no votes), so the Day advances round by
    round; at each round boundary ``day_turn`` reshuffles ``day_order``. We
    record the order at ``day_open`` and after every reshuffle, stopping when
    the Day closes (6 rounds) or the budget is spent.
    """
    # Resume the name interrupt -> roles assigned -> Night 1 -> day_open.
    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    orders: list[list[str]] = []
    last: list[str] | None = None

    def _record() -> None:
        nonlocal last
        current = _day_order(graph, run_config)
        if current and current != last:
            orders.append(current)
            last = current

    _record()  # the Day-1 day_open order

    # One human day_turn interrupt per round; budget covers 6 rounds + slack.
    for _ in range(DAY_MAX_ROUNDS + 4):
        if _day_closed(graph, run_config):
            break
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            break
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            _drive(graph, run_config, None)
            _record()
            continue
        kind = iv.get("kind")
        if kind == "day_turn":
            resume: str = "I have nothing pointed to say yet."
        elif kind == "point":
            # Human is pinned Law-abiding, so this should not fire; guard.
            options = iv.get("options") or []
            resume = options[0]["id"] if options else ""
        else:  # pragma: no cover - defensive
            raise AssertionError(f"Unexpected interrupt kind: {kind!r}")
        _drive(graph, run_config, Command(resume=resume))
        _record()

    return orders


# --------------------------------------------------------------------------
# The end-to-end fairness test.
# --------------------------------------------------------------------------


def test_emerging_day_order_is_fair_across_games(
    env: Path,
    tmp_path: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across M seeded games, the live Day order is uniform + role/type-blind.

    Aggregates a ``counts[player_id][position]`` matrix over every ``day_order``
    the running graph produces during each game's Day 1, then asserts:

    (a) Per-position uniformity: every alive player lands in each of the
        ``N_ALIVE`` positions within ``RELATIVE_TOLERANCE`` of the per-cell
        expectation.
    (b) Role parity: aggregating by Mafia vs Law-abiding, each role's share at
        every position is proportional to its alive headcount.
    (c) Human/AI parity: the single human's per-position rate matches a single
        comparable AI's within tolerance.

    Player identities (UUIDs) differ per game, so we key the matrix on a STABLE
    label — the player's display name (the roster ``AI_NAMES`` + ``HUMAN_NAME``
    are identical every game) — and track each name's role per game to bucket by
    role correctly even though the role deal varies run to run.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")

    master = random.Random(BASE_SEED)
    child_seeds = [master.randrange(2**31) for _ in range(M_GAMES)]

    # counts[name][position]: per-seat placement matrix across all games. Seats
    # are keyed by their stable display name (the roster is identical every
    # game); UUIDs differ per game so name is the only stable join key.
    position_counts: dict[str, Counter[int]] = {}
    # Per-position role membership, aggregated across games so role parity holds
    # even though the role deal is reshuffled every game.
    mafia_placements: Counter[int] = Counter()
    law_placements: Counter[int] = Counter()
    human_placements: Counter[int] = Counter()
    # The set of seats that are AI in at least one game (every name except the
    # human's). Used to pick a well-sampled comparison AI after aggregation.
    ai_names: set[str] = set()

    total_orders = 0

    for game_index, seed in enumerate(child_seeds):
        # Pin the global RNG: production role deal, Night fallbacks, and every
        # _shuffle_order read module-global ``random``. Seeding here makes the
        # whole game (and thus the fairness sample) reproducible.
        random.seed(seed)

        # ``build_graph`` derives its ``thread_id`` (and SQLite checkpoint file
        # name) from ``datetime.now()`` at *second* precision. Driving 100+
        # games in well under a second would otherwise collide thread ids and
        # make a later game resume an earlier game's checkpoint — non-
        # determinism that has nothing to do with the order under test. Give
        # each game its own checkpoint directory so the per-game DB is unique
        # regardless of wall-clock timing. (Test-harness isolation only; the
        # production thread_id scheme is untouched.)
        monkeypatch.setenv(
            "GRAPHIA_CHECKPOINT_DIR", str(tmp_path / f"ckpt-{game_index}")
        )

        fake_small(AI_NAMES)
        config = load_config()
        graph, thread_id = build_graph(config)
        run_config = make_run_config(thread_id)

        # One stateless large-model fake serves BOTH the Day (speak) and Night
        # (pointing) call sites. Resolves live state lazily so it needs no ids.
        large_fake = _AlwaysSpeakLarge(
            lambda: graph.get_state(run_config).values
        )
        monkeypatch.setattr(
            "graphia.nodes.day.get_large", lambda: large_fake
        )
        monkeypatch.setattr(
            "graphia.nodes.night.get_large", lambda: large_fake
        )

        # Stream to the first (name) interrupt.
        _drive(graph, run_config, {"messages": []})
        first = _collect_interrupt(graph, run_config)
        assert first is not None and first.get("kind") == "name", (
            f"expected a name interrupt first; got {first!r}"
        )

        orders = _capture_day1_orders(graph, run_config)
        assert orders, "no day_order was captured for this game"

        # Roster (id -> name/role/human) is stable for the whole Day-1 window
        # (no one dies during a vote-free Day), so read it once here. (At the
        # name interrupt above ``players`` is not yet in state — the roster is
        # generated only after the name is resumed inside the helper.)
        post_players = graph.get_state(run_config).values["players"]
        assert len(post_players) == 7
        id_to_name = {pid: p.name for pid, p in post_players.items()}
        id_to_role = {pid: p.role for pid, p in post_players.items()}
        id_is_human = {pid: p.is_human for pid, p in post_players.items()}

        for order in orders:
            # Only aggregate the steady-state 6-alive orders so every cell in
            # the matrix is over an identical population. (day_open and every
            # reshuffle here are all 6-alive; this guard just documents/guards
            # the invariant.)
            if len(order) != N_ALIVE:
                continue
            total_orders += 1
            for position, pid in enumerate(order):
                name = id_to_name[pid]
                position_counts.setdefault(name, Counter())[position] += 1
                if id_to_role[pid] == "mafia":
                    mafia_placements[position] += 1
                else:
                    law_placements[position] += 1
                if id_is_human[pid]:
                    human_placements[position] += 1
                else:
                    ai_names.add(name)

    # ----------------------------------------------------------------------
    # (a) Per-position uniformity for every seat.
    # ----------------------------------------------------------------------
    # Every captured order is a permutation of the 6 alive seats, so each
    # position receives exactly one placement per order: total placements at a
    # position == total_orders, expected per (seat, position) == orders / 6.
    assert total_orders > 0

    # The 7 roster seats are stable by name across games; after Night 1 one
    # Law-abiding AI is dead, so a given name is absent from a game's orders in
    # the (few) games where it was that Night's victim. The deterministic Night
    # pointing fake targets the first alive Law-abiding non-human, so ONE seat
    # ends up the victim disproportionately often and accumulates too few
    # placements to anchor a tight per-cell band. We therefore assert per-seat
    # uniformity only for seats that survived into most games' orders
    # (``seat_total`` near the full order count); the under-sampled victim seat
    # still feeds the robust aggregate role/position totals below. At least the
    # human plus several AI seats clear this bar, so the check is not vacuous.
    well_sampled_floor = 0.5 * total_orders
    asserted_seats = 0
    for name, counter in position_counts.items():
        seat_total = sum(counter.values())
        if seat_total < well_sampled_floor:
            continue
        asserted_seats += 1
        seat_expected = seat_total / N_ALIVE
        seat_band = RELATIVE_TOLERANCE * seat_expected
        for position in range(N_ALIVE):
            observed = counter[position]
            assert abs(observed - seat_expected) <= seat_band, (
                f"seat {name!r} at position {position}: observed {observed}, "
                f"expected ~{seat_expected:.1f} (+/-{seat_band:.1f}) over "
                f"{seat_total} placements"
            )

    # The per-seat band must have actually run on a non-trivial set of seats
    # (the human + the rarely-eliminated AI seats), or the loop above is vacuous.
    assert asserted_seats >= 4, (
        f"expected at least 4 well-sampled seats to assert uniformity on, "
        f"only {asserted_seats} cleared the {well_sampled_floor:.0f}-placement floor"
    )

    # ----------------------------------------------------------------------
    # (b) Role parity: each position gets one placement per order, split
    #     between Mafia and Law-abiding in proportion to their alive headcount.
    # ----------------------------------------------------------------------
    for position in range(N_ALIVE):
        assert (
            mafia_placements[position] + law_placements[position]
            == total_orders
        ), (
            f"placements at position {position} don't sum to the order count: "
            f"mafia={mafia_placements[position]}, "
            f"law={law_placements[position]}, total={total_orders}"
        )
        # 2 Mafia of 6 alive -> Mafia should hold ~1/3 of each position's
        # placements, Law-abiding ~2/3. Use the same relative tolerance.
        expected_mafia = total_orders * (2 / N_ALIVE)
        expected_law = total_orders * (4 / N_ALIVE)
        assert abs(mafia_placements[position] - expected_mafia) <= (
            RELATIVE_TOLERANCE * expected_mafia
        ), (
            f"Mafia share skewed at position {position}: "
            f"{mafia_placements[position]} vs ~{expected_mafia:.1f}"
        )
        assert abs(law_placements[position] - expected_law) <= (
            RELATIVE_TOLERANCE * expected_law
        ), (
            f"Law-abiding share skewed at position {position}: "
            f"{law_placements[position]} vs ~{expected_law:.1f}"
        )

    # ----------------------------------------------------------------------
    # (c) Human/AI parity: the single human and a single comparable AI seat
    #     should each appear at every position at roughly the per-seat rate.
    # ----------------------------------------------------------------------
    # Pick the best-sampled AI seat as the comparison baseline — never the
    # disproportionately-killed victim seat — so the parity check contrasts two
    # seats that were both alive in (almost) every game. The human is pinned
    # Law-abiding and the pointing fake only targets AIs, so the human is never
    # the Night victim and appears in every game's orders.
    comparison_ai_name = max(
        ai_names, key=lambda n: sum(position_counts.get(n, Counter()).values())
    )
    comparison_ai_placements = position_counts[comparison_ai_name]

    human_total = sum(human_placements.values())
    ai_total = sum(comparison_ai_placements.values())
    assert human_total > 0 and ai_total > 0
    # Compare per-seat *rates* (placements per position relative to each seat's
    # own total) rather than raw counts, so any residual difference in the two
    # seats' game-participation denominators doesn't bias the comparison.
    human_expected = human_total / N_ALIVE
    ai_expected = ai_total / N_ALIVE
    for position in range(N_ALIVE):
        human_rate = human_placements[position] / human_expected
        ai_rate = comparison_ai_placements[position] / ai_expected
        assert abs(human_rate - ai_rate) <= 2 * RELATIVE_TOLERANCE, (
            f"human vs AI ({comparison_ai_name}) per-position rate diverged at "
            f"position {position}: human_rate={human_rate:.2f}, "
            f"ai_rate={ai_rate:.2f}"
        )
