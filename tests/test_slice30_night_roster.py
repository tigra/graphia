"""Spec 030 (Randomized Night-Pointing Roster Order), Slice 1 tests.

The production change shuffles the Mafia's living-Law-abiding Night candidate
roster into a fresh per-round order via the single module-level seam
``graphia.nodes.night._shuffle_night_roster`` (over the module-global ``random``
RNG, mirroring ``_shuffle_mafia_order`` / ``graphia.nodes.day._shuffle_order``).
The draw lives in the interrupt-free ``mafia_round_start`` super-step and is
frozen into a new ``night_law_order`` state channel; ``mafia_point`` reads that
frozen id-order back at the SINGLE candidate-assembly point so the AI roster
render and the human ``"point"`` interrupt ``options`` see one consistent,
replay-safe order. It is gated by a default-on ADR-011 ablation flag
(``GRAPHIA_NIGHT_ROSTER_SHUFFLE`` / ``night_roster_shuffle_enabled``).

Every assertion is structural / order-based (architecture §6); no Bedrock is
reached — the seam is pure stdlib RNG, and the few node/flow tests use the
LLM-boundary fakes (``fake_large_pointing``) the autouse ``safe_llm`` net allows.
The fairness harness (``_child_seeds`` + per-call ``random.seed``) is borrowed
from ``test_slice_day_order_fairness.py``; the flag/threading shape mirrors
``test_role_guidance.py``.

Test map:

1.  **Set preserved** — the shuffled roster is a reordering, never an
    add/drop (the functional "contains exactly those players" AC).
2.  **Order is actually shuffled** — over many seeds the seam yields ``> 1``
    distinct order for a fixed candidate set (and is the single monkeypatch seam).
3.  **Role/type independence** — the produced order is a pure function of the
    candidate identities, never ``role`` / ``is_human`` (the fairness invariant).
4.  **Flag-OFF = insertion order AND no RNG draw** — the load-bearing §3.1
    contract: OFF returns the input order and ``random.getstate()`` is unchanged
    across the call (no draw → prior seeded trajectory preserved byte-for-byte).
5.  **Seed-reproducible (ON)** — fixed seed ⇒ identical order; differing seeds
    ⇒ (statistically) varying orders.
6.  **``mafia_round_start`` freezes ``night_law_order``** — the per-round draw
    is committed once in the interrupt-free super-step; OFF freezes insertion
    order with no draw; the candidate set is preserved in the channel.
7.  **``mafia_point`` presents the frozen order to BOTH paths** — the AI roster
    render and the human ``options`` payload reflect ``night_law_order``.
8.  **Replay-safety on the human-Mafioso ``point`` path** — driving the real
    graph through a human Mafioso's modal interrupt + resume, the order shown is
    the frozen one and is NOT re-drawn on resume (the path the seed-0 dual-mode
    smoke does not exercise).
9.  **``load_config()`` default-on semantics** for ``GRAPHIA_NIGHT_ROSTER_SHUFFLE``.
10. **Threading anti-drift** — ``build_runtime_graph`` carries / forwards the flag.
"""

from __future__ import annotations

import asyncio
import inspect
import random
from pathlib import Path
from typing import Callable

import pytest
from langchain_core.messages import HumanMessage
from textual.widgets import RichLog

from graphia.config import load_config
from graphia.llm import DayAction, Pointing
from graphia.nodes import night as night_mod
from graphia.nodes import setup as setup_mod
from graphia.nodes.night import (
    _ai_pick_target,
    _roster_lines,
    _shuffle_night_roster,
    mafia_point,
    mafia_round_start,
)
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.state import PlayerState
from graphia.ui.app import GraphiaApp
from graphia.ui.widgets import PointingModal

# Borrow the proven multi-round human-Mafioso real-app harness pieces rather
# than re-deriving them (``tests/`` is not a package; sibling modules import by
# bare name).
from test_multi_round_consensus import (
    AI_NAMES,
    HUMAN_NAME,
    _CountingRoundPointing,
    _dismiss_next_pointing_modal,
    _player,
    _players_snapshot,
    _rich_log_text,
    _seed_state,
    _submit_name,
    _wait_for,
    _wait_for_players,
    _wait_for_pointing_modal,
)

# Single stable root for the seeded fairness/reproducibility harness, mirroring
# ``test_slice_day_order_fairness.BASE_SEED``: production reads the module-global
# ``random`` state, so ``random.seed(child)`` immediately before each call fully
# pins the shuffle.
BASE_SEED = 20250619


# --------------------------------------------------------------------------
# Candidate-list builders + seeded harness.
# --------------------------------------------------------------------------


def _candidates(
    ids: list[str],
    *,
    mafia_ids: set[str] | None = None,
    human_id: str | None = None,
) -> list[PlayerState]:
    """Build a living Law-abiding candidate list (insertion-ordered by ``ids``).

    ``role`` / ``is_human`` are assignable so the role/type-independence test can
    reassign them to different ids without changing the candidate identities the
    seam reorders. ``_shuffle_night_roster`` must never read either.
    """
    mafia_ids = mafia_ids or set()
    return [
        PlayerState(
            id=pid,
            name=f"Player-{pid}",
            role="mafia" if pid in mafia_ids else "law_abiding",
            is_human=(pid == human_id),
            is_alive=True,
        )
        for pid in ids
    ]


SIX_IDS = [f"c{i}" for i in range(6)]


def _child_seeds(base_seed: int, n: int) -> list[int]:
    """Derive ``n`` child seeds deterministically from ``base_seed``."""
    master = random.Random(base_seed)
    return [master.randrange(2**31) for _ in range(n)]


def _orders_over_seeds(
    candidates: list[PlayerState], child_seeds: list[int]
) -> list[list[str]]:
    """Return the id-order ``_shuffle_night_roster`` yields under each seed (ON)."""
    orders: list[list[str]] = []
    for seed in child_seeds:
        random.seed(seed)
        shuffled = _shuffle_night_roster(candidates, enabled=True)
        orders.append([p.id for p in shuffled])
    return orders


# ==========================================================================
# 1. Set preserved — a reordering, never add/drop.
# ==========================================================================


def test_shuffle_preserves_the_candidate_set() -> None:
    """ON: the shuffled roster holds exactly the input players (set + len)."""
    candidates = _candidates(SIX_IDS)
    input_ids = [p.id for p in candidates]

    random.seed(BASE_SEED)
    shuffled = _shuffle_night_roster(candidates, enabled=True)

    assert [p.id for p in shuffled] != input_ids or len(SIX_IDS) == 1, (
        "expected the seeded shuffle to reorder (guard against a no-op match)"
    )
    assert {p.id for p in shuffled} == set(input_ids)
    assert len(shuffled) == len(candidates)
    # The same PlayerState objects, only reordered — never copied/mutated.
    assert set(id(p) for p in shuffled) == set(id(p) for p in candidates)


def test_shuffle_does_not_mutate_the_input_list() -> None:
    """The input list's order is untouched; a fresh copy is returned."""
    candidates = _candidates(SIX_IDS)
    before = list(candidates)

    random.seed(BASE_SEED)
    _shuffle_night_roster(candidates, enabled=True)

    assert candidates == before, "the input list must not be mutated in place"


# ==========================================================================
# 2. Order is actually shuffled (the single monkeypatch seam).
# ==========================================================================


def test_shuffle_produces_more_than_one_order_over_seeds() -> None:
    """Over many seeds the seam yields > 1 distinct order — it really reorders."""
    candidates = _candidates(SIX_IDS)
    seeds = _child_seeds(BASE_SEED, 500)

    orders = _orders_over_seeds(candidates, seeds)

    assert len({tuple(o) for o in orders}) > 1, (
        "expected the candidate-roster shuffle to produce more than one order"
    )
    # Not always the same player first — the positional-bias the spec removes.
    firsts = {o[0] for o in orders}
    assert len(firsts) > 1, "expected the first-listed candidate to vary"


# ==========================================================================
# 3. Role / type independence — pure function of identities.
# ==========================================================================


def test_order_ignores_role_and_human_flag_exactly() -> None:
    """The order is a pure function of candidate ids, not role/is_human.

    Two candidate lists share the SAME ids (same insertion order) but assign
    ``role`` / ``is_human`` to DIFFERENT ids. Driven under the SAME seeds their
    full order sequences must be byte-identical — proving ``_shuffle_night_roster``
    never reads ``role`` / ``is_human`` (the fairness invariant ``_shuffle_order``
    already holds).
    """
    seeds = _child_seeds(BASE_SEED, 300)

    cand_a = _candidates(SIX_IDS, mafia_ids={"c0", "c1"}, human_id="c0")
    cand_b = _candidates(SIX_IDS, mafia_ids={"c4", "c5"}, human_id="c3")

    orders_a = _orders_over_seeds(cand_a, seeds)
    orders_b = _orders_over_seeds(cand_b, seeds)

    assert orders_a == orders_b, (
        "Night roster order diverged when only role/is_human changed — "
        "_shuffle_night_roster must depend solely on candidate identity."
    )
    assert len({tuple(o) for o in orders_a}) > 1, (
        "expected the shuffle to produce more than one distinct order"
    )


# ==========================================================================
# 4. Flag-OFF = insertion order AND no RNG draw (the load-bearing test).
# ==========================================================================


def test_flag_off_returns_insertion_order() -> None:
    """OFF: the seam returns the exact input order (prior fixed presentation)."""
    candidates = _candidates(SIX_IDS)

    result = _shuffle_night_roster(candidates, enabled=False)

    assert [p.id for p in result] == [p.id for p in candidates]


def test_flag_off_takes_no_rng_draw() -> None:
    """OFF consumes ZERO module-global RNG state (the §3.1 contract).

    The whole point of the ablation flag is that OFF reproduces the prior seeded
    trajectory byte-for-byte. That only holds if the disabled call draws nothing
    from the shared ``random`` state. Record ``random.getstate()`` before/after
    the disabled call and assert it is unchanged.
    """
    candidates = _candidates(SIX_IDS)
    random.seed(BASE_SEED)
    state_before = random.getstate()

    _shuffle_night_roster(candidates, enabled=False)

    assert random.getstate() == state_before, (
        "flag-OFF _shuffle_night_roster drew from the module-global RNG — it "
        "must return before any random.* call so the OFF trajectory matches the "
        "pre-030 behaviour byte-for-byte."
    )


def test_flag_off_never_calls_random_shuffle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFF never reaches ``random.shuffle`` (the guard is ahead of the draw).

    A complementary, intent-readable check of the same contract: monkeypatch
    ``random.shuffle`` to explode and assert the disabled call never trips it,
    while the enabled call DOES.
    """
    candidates = _candidates(SIX_IDS)

    def _boom(_seq: list) -> None:
        raise AssertionError("random.shuffle must not be called when disabled")

    monkeypatch.setattr(night_mod.random, "shuffle", _boom)

    # OFF: must not call shuffle.
    _shuffle_night_roster(candidates, enabled=False)

    # ON: proves the explode-stub is actually wired (would fire if reached).
    with pytest.raises(AssertionError, match="must not be called when disabled"):
        _shuffle_night_roster(candidates, enabled=True)


# ==========================================================================
# 5. Seed-reproducible (ON).
# ==========================================================================


def test_same_seed_yields_identical_order() -> None:
    """ON: a fixed seed reproduces the order exactly (seeded comparisons stable)."""
    candidates = _candidates(SIX_IDS)

    random.seed(BASE_SEED)
    first = [p.id for p in _shuffle_night_roster(candidates, enabled=True)]
    random.seed(BASE_SEED)
    second = [p.id for p in _shuffle_night_roster(candidates, enabled=True)]

    assert first == second


def test_different_seeds_vary_the_order() -> None:
    """ON: distinct seeds (statistically) produce distinct orders."""
    candidates = _candidates(SIX_IDS)
    seeds = _child_seeds(BASE_SEED, 50)

    orders = {tuple(o) for o in _orders_over_seeds(candidates, seeds)}

    assert len(orders) > 1, "expected differing seeds to vary the order"


# ==========================================================================
# 6. mafia_round_start freezes night_law_order (the replay-safe channel).
# ==========================================================================


def _night_seed(players: dict[str, PlayerState]) -> dict:
    """A fresh post-``night_open`` Night state (reuses the sibling helper)."""
    return _seed_state(players)


def test_mafia_round_start_freezes_shuffled_law_order_when_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ON: the candidate order is frozen ONCE in the interrupt-free super-step.

    Pin the seam to a known reordering and assert ``mafia_round_start`` commits
    that exact id-order into ``night_law_order`` — the strict mirror of the
    replay-safe ``night_mafia_order`` pattern, computed where the draw lives.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
        "c": _player("c", "Ivy", "law_abiding"),
    }
    monkeypatch.setattr(night_mod, "_shuffle_mafia_order", lambda ids: ["m1"])
    # Pin the candidate-roster seam to a deterministic reordering (reverse of
    # insertion order) so the assertion is exact, not statistical.
    monkeypatch.setattr(
        night_mod,
        "_shuffle_night_roster",
        lambda candidates, *, enabled: list(reversed(candidates)),
    )

    delta = mafia_round_start(_night_seed(players))

    # Frozen in insertion-reverse (the pinned reordering): c, b, a.
    assert delta["night_law_order"] == ["c", "b", "a"]
    # The set is exactly the living Law-abiding candidates — none added/dropped.
    assert set(delta["night_law_order"]) == {"a", "b", "c"}


def test_mafia_round_start_freezes_insertion_order_and_no_roster_draw_when_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFF: ``night_law_order`` is plain insertion order, with no roster draw.

    Drives the REAL ``mafia_round_start`` with the flag off (the production
    threading path) and asserts both halves of the OFF contract end-to-end: the
    frozen order is insertion order, and the candidate-roster seam took NO RNG
    draw. (The unrelated ``_shuffle_mafia_order`` for the living Mafiosos still
    draws — it is not gated by this flag — so the no-draw guarantee is scoped to
    the roster seam: pin ``random.shuffle`` to explode and prove the OFF roster
    path never reaches it.)
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
        "c": _player("c", "Ivy", "law_abiding"),
    }
    # Pin the Mafioso order to identity (no draw) so the only RNG a draw could
    # come from in this node is the gated roster seam.
    monkeypatch.setattr(night_mod, "_shuffle_mafia_order", lambda ids: list(ids))

    def _boom(_seq: list) -> None:
        raise AssertionError(
            "random.shuffle reached with the roster-shuffle flag OFF"
        )

    monkeypatch.setattr(night_mod.random, "shuffle", _boom)

    delta = mafia_round_start(
        _night_seed(players), night_roster_shuffle_enabled=False
    )

    # Insertion order of the living Law-abiding players, and no shuffle drawn.
    assert delta["night_law_order"] == ["a", "b", "c"]


def test_mafia_round_start_law_order_empty_on_no_target_guard() -> None:
    """The no-target defensive guard yields an empty ``night_law_order``."""
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        # No living Law-abiding target.
        "dead": _player("dead", "Priya", "law_abiding", is_alive=False),
    }
    delta = mafia_round_start(_night_seed(players))
    assert delta["night_law_order"] == []
    assert delta["night_mafia_order"] == []


# ==========================================================================
# 7. mafia_point presents the frozen order to BOTH paths.
# ==========================================================================


def _human_message_text(messages) -> str:
    return "\n".join(
        str(m.content) for m in messages if isinstance(m, HumanMessage)
    )


def test_mafia_point_ai_roster_reflects_frozen_law_order(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI path: the rendered roster lines are in ``night_law_order`` order.

    Build a committed Night state whose ``night_law_order`` is a NON-insertion
    order, then run the real AI ``mafia_point`` and inspect the prompt handed to
    the model: the ``{name}: {id}`` roster lines must follow the frozen order,
    proving ``mafia_point`` applies the frozen list at the single assembly point.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
        "c": _player("c", "Ivy", "law_abiding"),
    }
    state = _night_seed(players)
    # Committed (frozen) round state: one AI pointer, candidates in c, a, b order.
    state["night_mafia_order"] = ["m1"]
    state["night_pointer_index"] = 0
    state["night_law_order"] = ["c", "a", "b"]
    fake = fake_large_pointing(["a"])

    mafia_point(state)

    prompt = _human_message_text(fake.last_messages)
    # The roster block follows the frozen order: Ivy(c) then Priya(a) then Silas(b).
    expected = _roster_lines(
        [players["c"], players["a"], players["b"]]
    )
    assert expected in prompt, (
        f"AI roster lines not in frozen night_law_order. Prompt was:\n{prompt}"
    )


def test_mafia_point_human_options_reflect_frozen_law_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human path: the ``"point"`` interrupt ``options`` follow ``night_law_order``.

    A human Mafioso pointer reaches the ``interrupt()``; capture its payload and
    assert the ``options`` id-sequence equals the frozen ``night_law_order`` —
    the SAME ordered list the AI path sees, applied at one assembly point.
    """
    human_id = "h"
    players = {
        human_id: _player(human_id, "Alice", "mafia", is_human=True),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
        "c": _player("c", "Ivy", "law_abiding"),
    }
    state = _night_seed(players)
    state["human_id"] = human_id
    state["night_mafia_order"] = [human_id]
    state["night_pointer_index"] = 0
    state["night_law_order"] = ["b", "c", "a"]

    captured: list[dict] = []

    def _capturing_interrupt(payload: dict) -> str:
        captured.append(payload)
        return "b"  # a valid target so the node completes

    monkeypatch.setattr(night_mod, "interrupt", _capturing_interrupt)

    mafia_point(state)

    assert len(captured) == 1
    options = captured[0]["options"]
    assert [o["id"] for o in options] == ["b", "c", "a"], (
        "human point options must follow the frozen night_law_order"
    )
    # The set is exactly the candidates — none missing, none extra.
    assert {o["id"] for o in options} == {"a", "b", "c"}


# ==========================================================================
# 8. Replay-safety on the human-Mafioso point path (the smoke doesn't cover it).
# ==========================================================================


async def test_human_mafioso_replay_does_not_redraw_night_law_order(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-driver human-Mafioso Night: ``night_law_order`` is not re-drawn on resume.

    The path the seed-0 dual-mode smoke does NOT exercise (its human is a
    Law-abiding target, never a Mafioso pointer). A human Mafioso plays a
    two-round Night with the roster shuffle ON (default). Across the human's
    modal interrupts we assert the candidate-roster seam (``_shuffle_night_roster``)
    is called exactly ONCE per round — proving the order shown to the human is
    the frozen one and is never re-rolled on a resume — and that the modal's
    option set always equals the round's frozen ``night_law_order``.

    The deck shuffle is pinned to identity so the deal is fully deterministic
    from the constructed deck (architecture §6 — pin via the RNG-using helper,
    not a global seed): with GRAPHIA_ROLE=mafia and the default 5+2 lineup the
    deal yields the human + one AI Mafioso + five Law-abiding targets.
    """
    monkeypatch.setattr(setup_mod, "_shuffle_deck", lambda deck: None)
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_small(AI_NAMES)
    fake_large(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"day-talk-{i}") for i in range(8)
        ],
    )

    # Count + pass-through the candidate-roster seam so we can assert it fires
    # once per round and never on a human resume. Delegates to the real shuffle
    # (the order still varies / is replay-frozen via state).
    real_shuffle = night_mod._shuffle_night_roster
    roster_shuffle_calls: list[int] = []

    def _counting_roster_shuffle(candidates, *, enabled):
        roster_shuffle_calls.append(len(candidates))
        return real_shuffle(candidates, enabled=enabled)

    monkeypatch.setattr(
        night_mod, "_shuffle_night_roster", _counting_roster_shuffle
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Pin the per-round Mafioso order so the HUMAN points FIRST each round —
        # the trajectory ``_CountingRoundPointing`` depends on (its disagree-vs-
        # agree decision reads the committed ``night_round``).
        def _human_first(ids: list[str]) -> list[str]:
            state = app._graph.get_state(app._run_config).values
            human = state.get("human_id")
            rest = sorted(i for i in ids if i != human)
            return ([human] + rest) if human in ids else sorted(ids)

        monkeypatch.setattr(night_mod, "_shuffle_mafia_order", _human_first)

        ai_fake = _CountingRoundPointing(
            lambda: app._graph.get_state(app._run_config).values
        )
        monkeypatch.setattr("graphia.nodes.night.get_large", lambda: ai_fake)

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        human_id = app._graph.get_state(app._run_config).values["human_id"]
        assert players[human_id].role == "mafia"

        law_abiding_ids = [
            pid for pid, p in players.items() if p.role == "law_abiding"
        ]
        human_target = law_abiding_ids[0]
        human_target_name = players[human_target].name

        # --- Round 1 modal: capture the frozen order shown, then resume. ------
        modal1 = await _wait_for_pointing_modal(app, pilot)
        frozen_round1 = list(
            app._graph.get_state(app._run_config).values["night_law_order"]
        )
        modal1_option_ids = [opt["id"] for opt in modal1._options]
        # The modal presents exactly the frozen order.
        assert modal1_option_ids == frozen_round1
        calls_after_round1_modal = len(roster_shuffle_calls)
        modal1.dismiss(human_target)

        # --- Round 2 modal: a fresh round → the seam fired exactly once more.
        modal2 = await _wait_for_pointing_modal(app, pilot)
        frozen_round2 = list(
            app._graph.get_state(app._run_config).values["night_law_order"]
        )
        modal2_option_ids = [opt["id"] for opt in modal2._options]
        assert modal2_option_ids == frozen_round2
        # The roster seam advanced by exactly ONE between the two rounds' modals
        # — the human's round-1 interrupt/resume did NOT re-draw the order.
        assert len(roster_shuffle_calls) == calls_after_round1_modal + 1, (
            "the candidate-roster shuffle must fire once per round and never on "
            f"a human resume; calls={roster_shuffle_calls!r}"
        )
        modal2.dismiss(human_target)

        kill_line = f"During the night, {human_target_name} was killed."
        public_log = app.query_one("#public-log", RichLog)

        def _kill_resolved() -> bool:
            if kill_line not in _rich_log_text(public_log):
                return False
            victim = _players_snapshot(app).get(human_target)
            return victim is not None and victim.is_alive is False

        await _wait_for(pilot, _kill_resolved, timeout=10.0)

        state = app._graph.get_state(app._run_config).values
        assert state["night_round"] == 2
        # Exactly two roster draws across the two rounds (one per round) — never
        # inflated by the human's resumes.
        assert len(roster_shuffle_calls) == 2, (
            f"expected exactly 2 roster shuffles (one per round); "
            f"got {len(roster_shuffle_calls)}: {roster_shuffle_calls!r}"
        )

        app.exit()
    assert app.is_running is False


# ==========================================================================
# 9. load_config() default-on semantics for GRAPHIA_NIGHT_ROSTER_SHUFFLE.
# ==========================================================================


def test_load_config_night_roster_shuffle_default_on_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ⇒ the roster shuffle is on (the documented default)."""
    monkeypatch.delenv("GRAPHIA_NIGHT_ROSTER_SHUFFLE", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().night_roster_shuffle_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_load_config_night_roster_shuffle_blank_is_default_on(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank/whitespace value is treated as unset ⇒ on (``_env_flag``)."""
    monkeypatch.setenv("GRAPHIA_NIGHT_ROSTER_SHUFFLE", blank)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().night_roster_shuffle_enabled is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "On"])
def test_load_config_night_roster_shuffle_truthy_value_enables(
    truthy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any truthy value keeps the roster shuffle on."""
    monkeypatch.setenv("GRAPHIA_NIGHT_ROSTER_SHUFFLE", truthy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().night_roster_shuffle_enabled is True


@pytest.mark.parametrize(
    "falsy", ["0", "false", "FALSE", "no", "off", "Off", "anything-else"]
)
def test_load_config_night_roster_shuffle_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit falsy value (or any non-truthy token) disables the flag."""
    monkeypatch.setenv("GRAPHIA_NIGHT_ROSTER_SHUFFLE", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().night_roster_shuffle_enabled is False


# ==========================================================================
# 10. Threading anti-drift — build_runtime_graph carries / forwards the flag.
# ==========================================================================


def test_build_runtime_graph_signature_carries_night_roster_flag() -> None:
    """``build_runtime_graph`` exposes a ``night_roster_shuffle_enabled`` param."""
    sig = inspect.signature(build_runtime_graph)
    assert "night_roster_shuffle_enabled" in sig.parameters
    # Defaulted to True (matching the config default) so callers compiling the
    # graph directly need not supply it.
    assert sig.parameters["night_roster_shuffle_enabled"].default is True


def test_build_runtime_graph_compiles_with_flag_off(tmp_path: Path) -> None:
    """The Runtime builder compiles a graph with the roster-shuffle flag off.

    A lightweight anti-drift smoke: compiling reaches no AWS/LLM, so this proves
    the flag is an accepted, forwarded kwarg in the Runtime path too — local and
    remote can't drift on the gate.
    """
    graph = build_runtime_graph(
        "thread-roster-off",
        tmp_path / "checkpoints",
        night_roster_shuffle_enabled=False,
    )
    assert graph is not None
