"""Spec 015 Slice 1 — multi-round Mafia consensus by pointing.

The Night kill is no longer a single pass. ``night_open`` seeds the loop and
``route_after_night_open`` enters it at ``mafia_round_start``; from there the
topology is::

    mafia_round_start → mafia_point → route_after_mafia_point
        → {mafia_point | mafia_round_start | resolve_night_kill}

``mafia_round_start`` shuffles the living-Mafioso order (its own super-step, no
``interrupt()`` — the one place per-round randomness lives, via
``_shuffle_mafia_order``) and, on re-entry, archives the just-completed round
into ``night_rounds_log`` + bumps ``night_round``. ``mafia_point`` handles
exactly ONE pointer per visit, committing to ``night_round_picks`` and advancing
``night_pointer_index``. ``route_after_mafia_point`` loops within the round,
starts another round when the round is split and ``night_round < 3``, or resolves
on unanimity / the 3-round cap. ``resolve_night_kill`` reads the **deciding**
round's picks from ``night_round_picks``.

These tests are entirely offline (architecture §6 — Determinism Posture):

* The LLM boundary is the ``fake_large`` pointing queue (scripted ``Pointing``
  outputs through ``graphia.nodes.night.get_large``).
* The per-round order is pinned by monkeypatching
  ``graphia.nodes.night._shuffle_mafia_order`` (the single shuffle surface).
* The final-round tie-break is pinned by monkeypatching
  ``graphia.nodes.night.random.choice`` (the only RNG inside
  ``resolve_night_kill``).

A small node-level driver (:func:`_drive_night`) walks the four functions in the
same order the compiled graph's edges do, so the multi-round mechanic is tested
by calling the nodes directly with a hand-built state — no Textual harness, no
checkpointer. The replay-safety case additionally drives the **real** graph
through the Textual app (mirroring ``test_slice5_night.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, RichLog, Static

from graphia.llm import DayAction, Pointing
from graphia.nodes import night as night_mod
from graphia.nodes import setup as setup_mod
from graphia.nodes.night import (
    NIGHT_ROUND_CAP,
    _ai_pick_target,
    _render_prior_picks,
    mafia_point,
    mafia_round_start,
    night_open,
    resolve_night_kill,
    route_after_mafia_point,
    route_after_night_open,
)
from graphia.prompts import MAFIA_POINT_USER_TEMPLATE
from graphia.state import PlayerState
from graphia.ui.app import GraphiaApp
from graphia.ui.widgets import PointingModal


# --------------------------------------------------------------------------
# Hand-built-state helpers for the node-isolation tests.
# --------------------------------------------------------------------------


def _player(
    pid: str,
    name: str,
    role: str,
    *,
    is_human: bool = False,
    is_alive: bool = True,
) -> PlayerState:
    return PlayerState(
        id=pid, name=name, role=role, is_human=is_human, is_alive=is_alive
    )


def _seed_state(players: dict[str, PlayerState], *, human_id: str | None = None) -> dict:
    """A fresh post-``night_open`` Night state for the node-level driver.

    Mirrors what ``night_open`` seeds on its normal (non-cap) path: round 1,
    empty order/picks/log, cursor at 0. ``cycle`` stays at 1.
    """
    state: dict = {
        "cycle": 1,
        "phase": "night",
        "players": players,
        "night_round": 1,
        "night_mafia_order": [],
        "night_pointer_index": 0,
        "night_round_picks": {},
        "night_rounds_log": [],
    }
    if human_id is not None:
        state["human_id"] = human_id
    return state


def _apply(state: dict, delta: dict) -> None:
    """Apply a node delta to ``state`` with plain-replace semantics.

    Every Spec-015 Night channel is a plain-replace reducer (state.py §2.2),
    so a node delta overwrites the matching keys. ``messages`` / ``kill_log``
    accumulate in the real graph, but no Night routing decision reads them, so
    the node-level driver ignores them.
    """
    for key, value in delta.items():
        if key in ("messages", "kill_log"):
            continue
        state[key] = value


def _drive_night(state: dict, **resolve_kwargs) -> dict:
    """Walk the Night pointing loop over the real node functions.

    Reproduces the compiled graph's night edges by hand so the multi-round
    mechanic can be tested by calling the nodes directly:

        night_open → route_after_night_open
                   → mafia_round_start
                   → mafia_point (loop)
                   → route_after_mafia_point
                   → {mafia_point | mafia_round_start | resolve_night_kill}

    ``state`` must already be a post-``night_open`` seed (see ``_seed_state``).
    Returns ``resolve_night_kill``'s delta. Guards against runaway loops with a
    generous step ceiling (well above 3 rounds × a handful of pointers).
    """
    _apply(state, mafia_round_start(state))
    steps = 0
    while True:
        steps += 1
        assert steps < 100, "night pointing loop did not terminate"
        route = route_after_mafia_point(state)
        if route == "resolve_night_kill":
            return resolve_night_kill(state, **resolve_kwargs)
        if route == "mafia_round_start":
            _apply(state, mafia_round_start(state))
            continue
        # route == "mafia_point"
        _apply(state, mafia_point(state))


# --------------------------------------------------------------------------
# Early agreement — a unanimous round 1 resolves in exactly one round.
# --------------------------------------------------------------------------


def test_early_agreement_resolves_in_one_round(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two AI Mafia, scripted unanimous round 1 → one round, agreed victim.

    Both AI point at ``victim`` on their single visit; the round is unanimous
    (``set`` size 1) so ``route_after_mafia_point`` resolves immediately. The
    pointing fake is called exactly once per Mafioso (no extra rounds), the
    rounds-log is empty, ``night_round`` stays at 1, and the agreed target dies.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "victim": _player("victim", "Priya", "law_abiding"),
        "bystander": _player("bystander", "Silas", "law_abiding"),
    }
    # Pin the round order so the script lines up with the pointers.
    monkeypatch.setattr(night_mod, "_shuffle_mafia_order", lambda ids: ["m1", "m2"])
    fake = fake_large_pointing(["victim", "victim"])

    state = _seed_state(players)
    delta = _drive_night(state)

    assert delta["players"]["victim"].is_alive is False
    assert delta["night_victim_count"] == 1
    # Exactly one AI invocation per Mafioso — no second round.
    assert fake.call_count == 2
    assert state["night_round"] == 1
    assert state["night_rounds_log"] == []
    assert state["night_round_picks"] == {"m1": "victim", "m2": "victim"}


# --------------------------------------------------------------------------
# Cap fallback — split across all 3 rounds → plurality of the FINAL round,
# with a forced final-round tie broken by the pinned random selector.
# --------------------------------------------------------------------------


def test_cap_fallback_resolves_majority_of_final_round_with_tiebreak(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No consensus across 3 rounds → majority of the final round, tie broken.

    Three AI Mafia never agree; the loop hits the 3-round cap. The **final**
    round is engineered as a tie (each of three Mafia points at a distinct
    target), and the tie-break ``random.choice`` is pinned to a known winner.
    Exactly three rounds run: two archived in ``night_rounds_log`` plus the
    deciding round in ``night_round_picks``.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "m3": _player("m3", "Dana", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
        "c": _player("c", "Ivy", "law_abiding"),
    }
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: ["m1", "m2", "m3"]
    )
    # Three rounds × three pointers, all split (no round unanimous). The final
    # (3rd) round is a 1-1-1 three-way tie over a, b, c.
    fake = fake_large_pointing(
        [
            "a", "b", "b",   # round 1: b plurality (not unanimous)
            "a", "a", "c",   # round 2: a plurality (not unanimous)
            "a", "b", "c",   # round 3 (final): three-way tie a/b/c
        ]
    )
    # Pin the tie-break: of the tied finalists, pick "b" deterministically.
    monkeypatch.setattr(night_mod.random, "choice", lambda seq: "b")

    state = _seed_state(players)
    delta = _drive_night(state)

    # Victim is the pinned tie-break winner from the FINAL round.
    assert delta["players"]["b"].is_alive is False
    assert delta["night_victim_count"] == 1
    # Exactly three rounds ran: two archived + the deciding round still live.
    assert state["night_round"] == 3
    assert len(state["night_rounds_log"]) == 2
    assert state["night_rounds_log"][0] == {"m1": "a", "m2": "b", "m3": "b"}
    assert state["night_rounds_log"][1] == {"m1": "a", "m2": "a", "m3": "c"}
    assert state["night_round_picks"] == {"m1": "a", "m2": "b", "m3": "c"}
    # Nine pointer picks total over three full rounds.
    assert fake.call_count == 9


# --------------------------------------------------------------------------
# Slice 2 — the running picks reach the AI prompt so the AI can converge.
#
# Slice 1 above proved the *mechanic* (loop, rounds, resolution) with an AI
# that points blind. These tests prove the Slice-2 prompt-threading contract
# (tech-spec §2.4): the by-name "teammates' picks so far" block is rendered
# from ``night_rounds_log`` + ``night_round_picks`` and handed to the model,
# so a follow-the-leader AI converges past a split.
# --------------------------------------------------------------------------


def _human_message_text(messages) -> str:
    """Concatenate the text of every HumanMessage in a captured prompt.

    ``_ai_pick_target`` builds ``[SystemMessage(...), HumanMessage(...)]`` and,
    on the retry path, appends a second ``HumanMessage``. The picks-so-far block
    lives in the first HumanMessage (the rendered ``MAFIA_POINT_USER_TEMPLATE``).
    Joining all HumanMessage content keeps the assertion robust if the retry
    path ever fires.
    """
    from langchain_core.messages import HumanMessage

    return "\n".join(
        str(m.content) for m in messages if isinstance(m, HumanMessage)
    )


def test_round2_convergence_resolves_at_round_two_not_three(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Split round 1, unanimous round 2 → the Night resolves at round 2.

    Two AI Mafia disagree in round 1 (``m1→a``, ``m2→b``) so the round is not
    unanimous and the loop starts round 2; in round 2 both point at ``b`` (the
    AI "moved toward a shared target"), so the round is unanimous and
    ``route_after_mafia_point`` resolves WITHOUT starting a third round. This
    proves the loop continues past a split and stops on agreement: at resolution
    ``night_round == 2`` and exactly one round (round 1) is archived.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
    }
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: ["m1", "m2"]
    )
    # Round 1: m1→a, m2→b (split). Round 2: m1→b, m2→b (unanimous on b).
    fake = fake_large_pointing(["a", "b", "b", "b"])

    state = _seed_state(players)
    delta = _drive_night(state)

    # Resolved at round 2, not round 3.
    assert state["night_round"] == 2
    assert len(state["night_rounds_log"]) == 1  # round 1 archived
    assert state["night_rounds_log"][0] == {"m1": "a", "m2": "b"}
    assert state["night_round_picks"] == {"m1": "b", "m2": "b"}  # deciding round
    # The agreed target dies; no third round ran (4 picks = 2 rounds × 2).
    assert delta["players"]["b"].is_alive is False
    assert delta["night_victim_count"] == 1
    assert fake.call_count == 4


def test_round2_prompt_contains_round1_picks_by_name(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round-2 AI prompt carries the round-1 picks rendered BY NAME.

    Drives the same split-then-converge trajectory and captures the actual
    prompt text handed to the model on the round-2 invocations (through the
    real ``_ai_pick_target`` → ``MAFIA_POINT_USER_TEMPLATE.format(...)`` path,
    so the template wiring is covered). The round-2 prompts must contain the
    round-1 picks by display NAME ("Marco → Priya", "Yuki → Silas") and must
    NOT leak the raw uuid-style ids ("m1", "m2", "a", "b") into the prose.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
    }
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: ["m1", "m2"]
    )
    fake = fake_large_pointing(["a", "b", "b", "b"])

    state = _seed_state(players)
    _drive_night(state)

    # Calls 0,1 are round 1 (empty block); calls 2,3 are round 2 (carry round 1).
    assert fake.call_count == 4
    round2_first = _human_message_text(fake.messages_log[2])

    # Round-1 picks rendered by NAME (the roster's display names), grouped under
    # "Round 1". The first round-2 pointer (m1) sees both round-1 picks.
    assert "Round 1" in round2_first
    assert "Marco → Priya" in round2_first  # m1→a in round 1
    assert "Yuki → Silas" in round2_first  # m2→b in round 1

    # And NOT the raw ids — only names appear in the picks prose. Guard against a
    # whole-id leak (these are this test's deliberately id-shaped names so a
    # naive id render would surface them). The roster block lists "name: id", so
    # bare-id substrings can legitimately appear there; restrict the negative
    # assertion to the picks block (everything after the picks header).
    picks_block = round2_first.split("picks so far this Night:", 1)[-1]
    picks_block = picks_block.split("\n\n", 1)[0]
    for raw_id in ("m1", "m2"):
        assert raw_id not in picks_block, (
            f"raw id {raw_id!r} leaked into the picks block: {picks_block!r}"
        )


def test_in_turn_same_round_picks_visible_to_later_pointer() -> None:
    """The Nth pointer's rendered context includes earlier SAME-round picks.

    Asserts the pure ``_render_prior_picks`` helper directly (the cleanest unit):
    with no completed rounds and a current-round picks dict holding two earlier
    pointers' choices, the third pointer (``exclude_pointer_id="m3"``) sees both
    earlier picks rendered by NAME under "Round 1 so far", and its OWN line is
    excluded (it hasn't picked yet).
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "m3": _player("m3", "Dana", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
    }
    # Earlier same-round pickers m1, m2 have committed; m3 is about to point.
    current_round_picks = {"m1": "a", "m2": "b", "m3": "a"}

    rendered = _render_prior_picks(
        players=players,
        rounds_log=[],
        current_round_picks=current_round_picks,
        exclude_pointer_id="m3",
    )

    # Earlier same-round picks visible, by name, tagged as the live round.
    assert "Round 1 so far" in rendered
    assert "Marco → Priya" in rendered  # m1→a
    assert "Yuki → Silas" in rendered  # m2→b
    # The excluded current pointer (m3) does not appear — it hasn't picked yet.
    assert "Dana" not in rendered
    # No completed round was synthesised.
    assert "Round 1 —" not in rendered


def test_first_pointer_gets_neutral_empty_block() -> None:
    """The very first pointer of round 1 gets the neutral "no picks" block.

    With an empty rounds-log and empty current-round picks, ``_render_prior_picks``
    returns the neutral sentinel (never a stale pick), and that exact text flows
    into the rendered ``MAFIA_POINT_USER_TEMPLATE`` so the first-pointer prompt
    shows it rather than a leftover from a prior Night.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
    }
    neutral = "No teammate has pointed yet this Night."

    rendered = _render_prior_picks(
        players=players,
        rounds_log=[],
        current_round_picks={},
        exclude_pointer_id="m1",
    )
    assert rendered == neutral

    # The neutral text is embedded by the template wiring used by _ai_pick_target.
    prompt = MAFIA_POINT_USER_TEMPLATE.format(
        roster="Priya: a", mafia_persona="", prior_picks=neutral
    )
    assert neutral in prompt
    # And not a stale "Round 1" pick line.
    assert "Round 1" not in prompt


def test_ai_pick_target_first_pointer_prompt_has_neutral_block(
    fake_large_pointing,
) -> None:
    """End-to-end through ``_ai_pick_target``: empty ``prior_picks`` → neutral.

    Calling the real ``_ai_pick_target`` with the default empty ``prior_picks``
    (the first pointer of round 1) renders the neutral sentinel into the prompt
    the model receives — the template default branch, exercised through the
    production code path rather than asserted only on the helper.
    """
    targets = [_player("a", "Priya", "law_abiding")]
    mafia = _player("m1", "Marco", "mafia")
    fake = fake_large_pointing(["a"])

    chosen = _ai_pick_target(alive_law_abiding=targets, mafia=mafia)

    assert chosen == "a"
    assert fake.call_count == 1
    prompt = _human_message_text(fake.last_messages)
    assert "No teammate has pointed yet this Night." in prompt


# --------------------------------------------------------------------------
# Lone Mafioso — a single pointer is trivially unanimous → one round.
# --------------------------------------------------------------------------


def test_lone_mafioso_resolves_in_one_round(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One living Mafioso → one pick → trivially unanimous → immediate resolve."""
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "victim": _player("victim", "Priya", "law_abiding"),
        "bystander": _player("bystander", "Silas", "law_abiding"),
    }
    monkeypatch.setattr(night_mod, "_shuffle_mafia_order", lambda ids: ["m1"])
    fake = fake_large_pointing(["victim"])

    state = _seed_state(players)
    delta = _drive_night(state)

    assert delta["players"]["victim"].is_alive is False
    assert delta["night_victim_count"] == 1
    assert fake.call_count == 1
    assert state["night_round"] == 1
    assert state["night_rounds_log"] == []
    assert state["night_round_picks"] == {"m1": "victim"}


# --------------------------------------------------------------------------
# Per-round reshuffle — the shuffle helper is called once per round.
# --------------------------------------------------------------------------


def test_shuffle_called_once_per_round(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_shuffle_mafia_order`` fires exactly once per round (re-randomized).

    Reuses the cap-fallback script (three split rounds) so the loop runs all
    three rounds, then asserts the recording shuffle stub was invoked three
    times — once per round, proving the order is re-rolled each round rather
    than fixed for the Night.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "m3": _player("m3", "Dana", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
        "c": _player("c", "Ivy", "law_abiding"),
    }

    shuffle_calls: list[list[str]] = []

    def _recording_shuffle(ids: list[str]) -> list[str]:
        shuffle_calls.append(list(ids))
        return ["m1", "m2", "m3"]

    monkeypatch.setattr(night_mod, "_shuffle_mafia_order", _recording_shuffle)
    monkeypatch.setattr(night_mod.random, "choice", lambda seq: "b")
    fake_large_pointing(
        [
            "a", "b", "b",
            "a", "a", "c",
            "a", "b", "c",
        ]
    )

    state = _seed_state(players)
    _drive_night(state)

    assert len(shuffle_calls) == 3, (
        f"expected the shuffle helper once per round (3 rounds); "
        f"got {len(shuffle_calls)} calls: {shuffle_calls!r}"
    )
    # Each call gets the same living-Mafioso id set (order-insensitive).
    for call in shuffle_calls:
        assert set(call) == {"m1", "m2", "m3"}


# --------------------------------------------------------------------------
# Structural replay-safety — shuffle and pick live in separate nodes.
# --------------------------------------------------------------------------


def test_shuffle_is_a_separate_node_from_the_interrupt_pick(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-deterministic shuffle is committed by a node with no interrupt.

    The replay-safety spine (tech-spec §3): per-round randomness lives in
    ``mafia_round_start`` (no ``interrupt()``), while the human pointer's
    ``interrupt()`` lives in ``mafia_point``. Because the shuffle is its own
    committed super-step, a human-pointer resume re-executes only ``mafia_point``
    and never re-rolls the order.

    This asserts the structural property directly: ``mafia_round_start`` calls
    the shuffle helper and commits the order WITHOUT touching ``night_round_picks``
    (no pick, hence no interrupt), and ``mafia_point`` produces a pick that
    leaves ``night_mafia_order`` untouched (it only reads the committed order).
    The full real-driver resume trajectory is covered by
    ``test_human_mafioso_multi_round_replay_does_not_recompute_ai_picks``.
    """
    players = {
        "m1": _player("m1", "Marco", "mafia"),
        "m2": _player("m2", "Yuki", "mafia"),
        "victim": _player("victim", "Priya", "law_abiding"),
    }

    shuffle_calls: list[list[str]] = []

    def _recording_shuffle(ids: list[str]) -> list[str]:
        shuffle_calls.append(list(ids))
        return ["m1", "m2"]

    monkeypatch.setattr(night_mod, "_shuffle_mafia_order", _recording_shuffle)
    fake = fake_large_pointing(["victim", "victim"])

    state = _seed_state(players)

    # mafia_round_start commits the order and rolls the round bookkeeping —
    # but makes no pick (so it carries no interrupt).
    rs_delta = mafia_round_start(state)
    assert shuffle_calls == [["m1", "m2"]]
    assert rs_delta["night_mafia_order"] == ["m1", "m2"]
    assert rs_delta["night_round_picks"] == {}
    assert "messages" not in rs_delta  # nothing human-facing emitted here
    _apply(state, rs_delta)

    # mafia_point commits ONE pick and does NOT re-emit the order — proving it
    # only reads the committed order, never re-rolls it.
    mp_delta = mafia_point(state)
    assert "night_mafia_order" not in mp_delta
    assert mp_delta["night_round_picks"] == {"m1": "victim"}
    assert mp_delta["night_pointer_index"] == 1
    # The shuffle helper was not invoked again by the pick step.
    assert shuffle_calls == [["m1", "m2"]]
    assert fake.call_count == 1


# --------------------------------------------------------------------------
# Career stats — one attempt per Night; success iff deciding-round pick wins.
# --------------------------------------------------------------------------


def test_human_counted_once_and_success_on_deciding_round_pick(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human Mafioso over multiple rounds: one attempt; success on the win.

    The human is a Mafioso who points across two rounds (round 1 split, round 2
    unanimous on the human's target). At resolution the human is credited with
    exactly ONE attempt for the Night (not per round) and ONE success iff the
    human's **deciding-round** pick equals the victim.

    The human pick is supplied via the ``interrupt()`` resume value, which the
    node-level driver injects with ``Command(resume=...)`` semantics by
    monkeypatching ``graphia.nodes.night.interrupt`` to a scripted queue — the
    same boundary the Textual driver pumps in production.
    """
    human_id = "h"
    players = {
        human_id: _player(human_id, "Alice", "mafia", is_human=True),
        "m2": _player("m2", "Yuki", "mafia"),
        "victim": _player("victim", "Priya", "law_abiding"),
        "other": _player("other", "Silas", "law_abiding"),
    }
    # Human points first, AI second, every round (pin the order).
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: [human_id, "m2"]
    )

    # Round 1: human→other, AI→victim (split). Round 2: human→victim, AI→victim
    # (unanimous → deciding round). Human's deciding-round pick == victim.
    human_resumes = iter(["other", "victim"])

    def _fake_interrupt(payload: dict) -> str:
        assert payload["kind"] == "point"
        return next(human_resumes)

    monkeypatch.setattr(night_mod, "interrupt", _fake_interrupt)
    fake = fake_large_pointing(["victim", "victim"])

    state = _seed_state(players, human_id=human_id)
    delta = _drive_night(state)

    assert delta["players"]["victim"].is_alive is False
    assert delta["night_victim_count"] == 1
    # Human counted once for the Night regardless of the two rounds.
    assert delta["human_night_attempts"] == 1
    # Deciding (round 2) pick == victim → success.
    assert delta["human_night_successes"] == 1
    # Two rounds: round 1 archived, round 2 is the deciding live round.
    assert state["night_round"] == 2
    assert len(state["night_rounds_log"]) == 1
    assert state["night_rounds_log"][0] == {human_id: "other", "m2": "victim"}
    assert state["night_round_picks"] == {human_id: "victim", "m2": "victim"}
    # AI invoked once per round (no recompute of round 1 in round 2).
    assert fake.call_count == 2


def test_human_attempt_without_success_when_deciding_pick_misses(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human Mafioso whose deciding-round pick loses → attempt, no success.

    Cap-fallback Night where the human points at a target that does NOT win the
    final round's plurality. The human is still credited one attempt for the
    Night, but ``human_night_successes`` is never bumped.
    """
    human_id = "h"
    players = {
        human_id: _player(human_id, "Alice", "mafia", is_human=True),
        "m2": _player("m2", "Yuki", "mafia"),
        "m3": _player("m3", "Dana", "mafia"),
        "a": _player("a", "Priya", "law_abiding"),
        "b": _player("b", "Silas", "law_abiding"),
    }
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: [human_id, "m2", "m3"]
    )
    # Three split rounds → cap fallback. In the final round the human points at
    # "a" while the AI pair points at "b" (b is the strict plurality, 2-1),
    # so no tie-break RNG is needed and the human's pick loses.
    human_resumes = iter(["a", "b", "a"])

    def _fake_interrupt(payload: dict) -> str:
        return next(human_resumes)

    monkeypatch.setattr(night_mod, "interrupt", _fake_interrupt)
    fake = fake_large_pointing(
        [
            "b", "b",   # round 1 AI picks (human→a) → round 1: a,b,b
            "a", "a",   # round 2 AI picks (human→b) → round 2: b,a,a
            "b", "b",   # round 3 AI picks (human→a) → round 3: a,b,b (b wins 2-1)
        ]
    )

    state = _seed_state(players, human_id=human_id)
    delta = _drive_night(state)

    assert delta["players"]["b"].is_alive is False
    assert delta["night_victim_count"] == 1
    assert delta["human_night_attempts"] == 1
    # Human's deciding (round 3) pick was "a", victim was "b" → no success.
    assert "human_night_successes" not in delta
    assert state["night_round"] == 3
    assert state["night_round_picks"] == {human_id: "a", "m2": "b", "m3": "b"}
    assert fake.call_count == 6


# --------------------------------------------------------------------------
# Replay-safety — full real-driver multi-round human-Mafioso trajectory.
# --------------------------------------------------------------------------

AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"


async def _wait_for(
    pilot,
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until truthy, yielding to pilot each tick."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(
                f"Predicate {predicate!r} did not become truthy within {timeout}s"
            )
        await pilot.pause(interval)


def _rich_log_text(widget: RichLog) -> str:
    parts: list[str] = []
    for line in widget.lines:
        text = getattr(line, "text", None)
        if text is None:
            text = str(line)
        parts.append(str(text))
    return "\n".join(parts)


async def _wait_for_input(app: GraphiaApp, pilot) -> Input:
    async def _input_enabled() -> bool:
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001
            return False
        return prompt.disabled is False

    await _wait_for(pilot, _input_enabled, timeout=5.0)
    return app.query_one("#player-input", Input)


async def _submit_name(app: GraphiaApp, pilot) -> None:
    prompt = await _wait_for_input(app, pilot)
    prompt.focus()
    await pilot.press(*HUMAN_NAME)
    await pilot.press("enter")


def _players_snapshot(app: GraphiaApp) -> dict:
    return app._graph.get_state(app._run_config).values["players"]


async def _wait_for_players(app: GraphiaApp, pilot) -> dict:
    def _ready() -> bool:
        try:
            players = _players_snapshot(app)
        except Exception:  # noqa: BLE001
            return False
        if len(players) != 7:
            return False
        return all(p.role in ("mafia", "law_abiding") for p in players.values())

    await _wait_for(pilot, _ready, timeout=5.0)
    return _players_snapshot(app)


async def _wait_for_pointing_modal(app: GraphiaApp, pilot) -> PointingModal:
    """Wait for a ``PointingModal`` to be pushed and return it (not dismissed)."""

    def _modal_open() -> bool:
        return isinstance(app.screen, PointingModal) or len(app.screen_stack) > 1

    await _wait_for(pilot, _modal_open, timeout=5.0)
    for screen in app.screen_stack:
        if isinstance(screen, PointingModal):
            return screen
    raise AssertionError("PointingModal not found on the screen stack")


async def _dismiss_next_pointing_modal(app: GraphiaApp, pilot, target_id: str) -> None:
    """Wait for a ``PointingModal`` to appear, then dismiss it with ``target_id``."""
    modal = await _wait_for_pointing_modal(app, pilot)
    modal.dismiss(target_id)


def _static_text(modal: PointingModal, widget_id: str) -> str | None:
    """Return a ``Static``'s rendered plain text by id, or ``None`` if absent.

    The ``PointingModal`` composes its round header (``#pointing-round``) and the
    teammates-so-far line (``#pointing-prior-picks``) *conditionally*, so a
    ``query_one`` would raise when the widget is suppressed (first pointer / no
    context). ``query`` returns an empty result-set instead, which we map to
    ``None`` — letting a test assert "this block is absent" cleanly. The
    matched ``Static``'s renderable is flattened to plain text (markup stripped)
    so substring assertions ignore Rich styling.
    """
    matches = modal.query(f"#{widget_id}")
    if not matches:
        return None
    widget = matches.first(Static)
    rendered = widget.render()
    # ``Static.render`` returns a Rich ``Text`` or a Textual ``Content`` — both
    # expose ``.plain`` for the markup-stripped text; fall back to ``str``.
    plain = getattr(rendered, "plain", None)
    return plain if isinstance(plain, str) else str(rendered)


class _CountingRoundPointing:
    """Night-pointing fake whose AI pick depends on the live round.

    Resolves targets at invoke time from the live graph state (dodging the
    assign-roles race, like ``_DynamicNightPointing``) and varies its target by
    ``night_round`` so the human can be steered into a multi-round trajectory:

    * Round 1: point at ``law_abiding_ids[1]`` (disagreeing with the human, who
      picks ``law_abiding_ids[0]`` via the modal) → round 1 is split.
    * Round 2+: point at ``law_abiding_ids[0]`` (the human's target) → unanimous
      → resolve at round 2.

    ``call_count`` lets the replay test assert the AI pick is NOT recomputed
    across the human's interrupt/resume.
    """

    def __init__(self, state_provider: Callable[[], dict]) -> None:
        self._state_provider = state_provider
        self.call_count = 0

    def with_structured_output(self, schema: type) -> "_CountingRoundPointing":
        return self

    def invoke(self, messages) -> Pointing:
        self.call_count += 1
        state = self._state_provider()
        players = state.get("players", {})
        law_abiding = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        round_no = state.get("night_round", 1)
        if round_no <= 1 and len(law_abiding) >= 2:
            return Pointing(target_id=law_abiding[1])
        return Pointing(target_id=law_abiding[0])


async def test_human_mafioso_multi_round_replay_does_not_recompute_ai_picks(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-driver multi-round human-Mafioso Night: AI picks aren't recomputed.

    The human is a Mafioso. Round 1 is forced split (the human picks
    ``law_abiding_ids[0]`` via the modal, the AI picks ``law_abiding_ids[1]``),
    so the loop starts round 2; in round 2 the AI follows the human's target and
    the Night resolves. Across BOTH human modal interrupts the AI pointing fake's
    ``call_count`` advances by exactly one per AI ``mafia_point`` super-step —
    proving a resume after the human's ``interrupt()`` re-reads committed picks
    and recomputes no earlier AI pick (tech-spec §3 replay-safety). The deciding
    round's ``night_round_picks`` is unanimous on the human's target.
    """
    # Pin the role deal via the RNG-using helper, NOT a global `random.seed(...)`
    # (architecture §6: "pin it via targeted monkeypatching of the RNG-using
    # helper"). Replacing the deck-shuffle seam with an identity no-op makes the
    # deal fully deterministic from the deck's constructed order, so this real-
    # driver trajectory no longer depends on cumulative suite-wide RNG state
    # (which a leaked prior-test driver thread could corrupt after `seed(0)`).
    # With GRAPHIA_ROLE=mafia and the default 5+2 lineup, the un-shuffled deck is
    # ["mafia"] + ["law_abiding"]*5; assign_roles prepends the pinned human role,
    # yielding ["mafia"(human), "mafia"(AI), "law_abiding"*5] in insertion order:
    # the human + exactly one AI Mafioso (the two-pointer trajectory this test
    # drives) plus five Law-abiding targets.
    monkeypatch.setattr(setup_mod, "_shuffle_deck", lambda deck: None)
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_small(AI_NAMES)
    fake_large(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"day-talk-{i}") for i in range(8)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Pin the round order so the HUMAN points FIRST and the AI second each
        # round — the trajectory this test depends on. ``_CountingRoundPointing``
        # reads ``night_round`` from the *committed* checkpoint to decide
        # disagree-vs-agree; when the human points first the AI's pick lands
        # after the round's bookkeeping has committed, so round detection is
        # reliable. A plain ``sorted(ids)`` pin would NOT guarantee this — the
        # player ids are random uuid4 strings, so sorting them puts the human
        # first only ~half the time, which silently flips round 1 unanimous (or
        # mis-detects round 2) and breaks the two-round expectation. So resolve
        # the live ``human_id`` and pin it to the front (the single Night shuffle
        # surface; architecture §6), mirroring the sibling modal test.
        def _human_first(ids: list[str]) -> list[str]:
            state = app._graph.get_state(app._run_config).values
            human = state.get("human_id")
            rest = sorted(i for i in ids if i != human)
            return ([human] + rest) if human in ids else sorted(ids)

        monkeypatch.setattr(night_mod, "_shuffle_mafia_order", _human_first)

        ai_fake = _CountingRoundPointing(
            lambda: app._graph.get_state(app._run_config).values
        )
        monkeypatch.setattr(
            "graphia.nodes.night.get_large", lambda: ai_fake
        )

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        human_id = app._graph.get_state(app._run_config).values["human_id"]
        assert players[human_id].role == "mafia"

        law_abiding_ids = [
            pid for pid, p in players.items() if p.role == "law_abiding"
        ]
        # The human always points at the same target both rounds; the AI
        # disagrees in round 1 and agrees in round 2.
        human_target = law_abiding_ids[0]
        human_target_name = players[human_target].name

        # Round 1 modal: human picks human_target (AI will pick a different one).
        await _dismiss_next_pointing_modal(app, pilot, human_target)
        # Sample the AI call count after round 1 has been driven far enough to
        # have started round 2's modal — the AI made one round-1 pick by then.
        # Round 2 modal: human picks the same target → unanimous → resolve.
        await _dismiss_next_pointing_modal(app, pilot, human_target)

        kill_line = f"During the night, {human_target_name} was killed."
        public_log = app.query_one("#public-log", RichLog)

        def _kill_resolved() -> bool:
            if kill_line not in _rich_log_text(public_log):
                return False
            victim = _players_snapshot(app).get(human_target)
            return victim is not None and victim.is_alive is False

        await _wait_for(pilot, _kill_resolved, timeout=10.0)

        state = app._graph.get_state(app._run_config).values
        # Deciding round resolved on the human's target.
        assert state["players"][human_target].is_alive is False
        # Two rounds ran: round 1 archived, round 2 deciding.
        assert state["night_round"] == 2
        assert len(state["night_rounds_log"]) == 1
        # The deciding round is unanimous on the human's target.
        assert set(state["night_round_picks"].values()) == {human_target}
        assert state["night_round_picks"][human_id] == human_target
        # The AI made exactly one pick per round (one per AI mafia_point
        # super-step): two AI picks total across the two rounds. A resume that
        # recomputed earlier AI picks would inflate this count.
        assert ai_fake.call_count == 2, (
            f"expected exactly 2 AI picks (one per round); the human's "
            f"interrupt/resume must not recompute committed AI picks; "
            f"got {ai_fake.call_count}"
        )

        app.exit()
    assert app.is_running is False


# --------------------------------------------------------------------------
# Slice 3 — the human sees the convergence: round header + teammates' picks
# in the point modal, across rounds, and the non-Mafia no-op.
#
# Three tests per tech-spec §2.5 / §4:
#   1. Real-app Pilot drive of a human-Mafioso two-round Night: the modal shows
#      "round 1 of 3" then "round 2 of 3", and round 2's modal carries a
#      teammate's round-1 pick BY NAME. Backed by a node-level payload assertion
#      that ``mafia_point``'s human ``interrupt()`` carries ``round`` /
#      ``round_cap`` / by-name ``prior_picks``.
#   2. First pointer of round 1 → header present, NO teammates-so-far block
#      (the neutral block is suppressed). Asserted by mounting ``PointingModal``
#      directly via ``App.run_test()``.
#   3. A Law-abiding human never sees a ``PointingModal`` during the Night.
# --------------------------------------------------------------------------


def test_mafia_point_human_payload_carries_round_and_by_name_picks(
    fake_large_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Node-level: the human ``interrupt()`` payload carries the modal context.

    Drives the node-level loop with a human Mafioso pointing first each round.
    ``graphia.nodes.night.interrupt`` is monkeypatched to *capture* the payload
    dict the modal would receive — the same boundary the Textual driver pumps —
    and to script the human's resume value. In round 2 the captured payload must
    carry ``round == 2``, ``round_cap == NIGHT_ROUND_CAP``, and a ``prior_picks``
    summary that names the teammate's round-1 pick BY NAME (not a raw id),
    proving the by-name context reaches the modal across rounds.
    """
    human_id = "h"
    players = {
        human_id: _player(human_id, "Alice", "mafia", is_human=True),
        "m2": _player("m2", "Yuki", "mafia"),
        "victim": _player("victim", "Priya", "law_abiding"),
        "other": _player("other", "Silas", "law_abiding"),
    }
    # Human points first, AI second, each round (pin the order).
    monkeypatch.setattr(
        night_mod, "_shuffle_mafia_order", lambda ids: [human_id, "m2"]
    )

    captured_payloads: list[dict] = []
    # Round 1: human→other, AI→victim (split → round 2). Round 2: human→victim,
    # AI→victim (unanimous → resolve). So by round 2 the AI's round-1 pick of
    # "victim" (Priya) is archived and must surface by name in the payload.
    human_resumes = iter(["other", "victim"])

    def _capturing_interrupt(payload: dict) -> str:
        captured_payloads.append(payload)
        return next(human_resumes)

    monkeypatch.setattr(night_mod, "interrupt", _capturing_interrupt)
    fake_large_pointing(["victim", "victim"])

    state = _seed_state(players, human_id=human_id)
    _drive_night(state)

    # The human was prompted once per round → two payloads captured.
    assert len(captured_payloads) == 2
    round1, round2 = captured_payloads

    # Round 1 payload: first pointer, neutral "no picks yet" block.
    assert round1["kind"] == "point"
    assert round1["round"] == 1
    assert round1["round_cap"] == NIGHT_ROUND_CAP
    assert round1["prior_picks"] == "No teammate has pointed yet this Night."

    # Round 2 payload: carries the round + cap + the teammate's round-1 pick.
    assert round2["round"] == 2
    assert round2["round_cap"] == NIGHT_ROUND_CAP
    prior = round2["prior_picks"]
    # The AI teammate's round-1 pick rendered BY NAME (Yuki → Priya), not ids.
    assert "Yuki → Priya" in prior
    # The human's own round-1 pick is included too (Alice → Silas).
    assert "Alice → Silas" in prior
    # No raw ids leak into the by-name summary.
    for raw_id in ("m2", "victim", "other"):
        assert raw_id not in prior, (
            f"raw id {raw_id!r} leaked into the human payload prior_picks: "
            f"{prior!r}"
        )


async def test_human_mafioso_modal_shows_round_and_prior_picks_across_rounds(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-app Pilot: the modal shows the round header + prior picks each round.

    A human Mafioso plays a two-round Night through the REAL app. The per-round
    order is pinned so the human points FIRST each round (so round 1's modal has
    no teammates-so-far block, and round 2's modal carries the AI teammate's
    round-1 pick by name). Round 1 is forced split (the AI disagrees with the
    human); round 2 the AI follows the human and the Night resolves.

    Assertions, on the live ``PointingModal`` pushed onto the screen stack:

    * Round 1: the ``#pointing-round`` header reads "round 1 of 3"; the
      ``#pointing-prior-picks`` block is ABSENT (first pointer, neutral block
      suppressed).
    * Round 2: the header reads "round 2 of 3"; the teammates-so-far line names
      the AI teammate's round-1 pick BY NAME (a display name, never a uuid).
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_small(AI_NAMES)
    fake_large(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"day-talk-{i}") for i in range(8)
        ],
    )

    app = GraphiaApp()
    async with app.run_test() as pilot:
        # Resolve the human at invoke time and pin the order so the HUMAN points
        # first each round (the AI's round-1 pick is then archived by the time
        # the human's round-2 modal opens). The single shuffle surface;
        # architecture §6.
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

        ai_mafia_id = next(
            pid
            for pid, p in players.items()
            if p.role == "mafia" and not p.is_human
        )
        ai_mafia_name = players[ai_mafia_id].name
        law_abiding_ids = [
            pid for pid, p in players.items() if p.role == "law_abiding"
        ]
        human_target = law_abiding_ids[0]
        human_target_name = players[human_target].name
        # ``_CountingRoundPointing`` picks law_abiding[1] in round 1 (disagree)
        # and law_abiding[0] in round 2 (agree). The AI's round-1 pick name is
        # what must show up in the human's round-2 prior-picks line.
        ai_round1_target_name = players[law_abiding_ids[1]].name

        # --- Round 1 modal -------------------------------------------------
        modal1 = await _wait_for_pointing_modal(app, pilot)
        round_header_1 = _static_text(modal1, "pointing-round")
        assert round_header_1 is not None
        assert "round 1 of 3" in round_header_1
        # First pointer of round 1 → the neutral block is suppressed entirely.
        assert _static_text(modal1, "pointing-prior-picks") is None
        modal1.dismiss(human_target)

        # --- Round 2 modal -------------------------------------------------
        modal2 = await _wait_for_pointing_modal(app, pilot)
        round_header_2 = _static_text(modal2, "pointing-round")
        assert round_header_2 is not None
        assert "round 2 of 3" in round_header_2
        prior_picks_2 = _static_text(modal2, "pointing-prior-picks")
        assert prior_picks_2 is not None, (
            "round 2 modal should show a teammates-so-far block"
        )
        # The teammate's round-1 pick appears BY NAME (both ends are names).
        assert "Teammates so far:" in prior_picks_2
        assert ai_mafia_name in prior_picks_2
        assert ai_round1_target_name in prior_picks_2
        assert f"{ai_mafia_name} → {ai_round1_target_name}" in prior_picks_2
        # No raw uuid-style id leaks into the human-visible picks line.
        assert ai_mafia_id not in prior_picks_2
        for pid in law_abiding_ids:
            assert pid not in prior_picks_2, (
                f"raw id {pid!r} leaked into the modal prior-picks line: "
                f"{prior_picks_2!r}"
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
        assert set(state["night_round_picks"].values()) == {human_target}

        app.exit()
    assert app.is_running is False


class _ModalHostApp(App[None]):
    """Minimal host app that pushes a ``PointingModal`` for isolated UI tests.

    ``ModalScreen`` can't be the top-level screen of ``App.run_test()``, so this
    bare host mounts and immediately pushes a pre-built ``PointingModal``. Lets a
    test assert the modal's composed widgets (header, prior-picks line, option
    list) deterministically without driving a whole Night through the graph.
    """

    def __init__(self, modal: PointingModal) -> None:
        super().__init__()
        self._modal = modal

    def compose(self) -> ComposeResult:
        yield Static("host", id="host-root")

    def on_mount(self) -> None:
        self.push_screen(self._modal)


async def test_pointing_modal_first_pointer_has_header_but_no_picks_block() -> None:
    """First pointer of round 1: header shown, teammates-so-far block absent.

    Mounts a ``PointingModal`` directly (round 1 of 3, the neutral "no picks
    yet" sentinel for ``prior_picks``) and asserts the ``#pointing-round`` header
    renders "round 1 of 3" while the ``#pointing-prior-picks`` ``Static`` is
    absent — the modal suppresses the neutral block so the first pointer sees no
    teammates line (Spec 015 §2.5).
    """
    modal = PointingModal(
        options=[
            {"id": "a", "name": "Priya"},
            {"id": "b", "name": "Silas"},
        ],
        round_number=1,
        round_cap=NIGHT_ROUND_CAP,
        # The neutral sentinel the graph renders for the very first pointer.
        prior_picks="No teammate has pointed yet this Night.",
    )
    app = _ModalHostApp(modal)
    async with app.run_test() as pilot:
        await _wait_for(
            pilot, lambda: isinstance(app.screen, PointingModal), timeout=5.0
        )
        header = _static_text(modal, "pointing-round")
        assert header is not None
        assert "round 1 of 3" in header
        # The neutral block is suppressed → the picks Static is not composed.
        assert _static_text(modal, "pointing-prior-picks") is None
        assert len(modal.query("#pointing-prior-picks")) == 0
        app.exit()
    assert app.is_running is False


async def test_pointing_modal_renders_prior_picks_line_when_present() -> None:
    """A non-first pointer's modal shows the by-name teammates-so-far line.

    Mounts a ``PointingModal`` for round 2 of 3 with a by-name ``prior_picks``
    summary and asserts both the round header and the "Teammates so far:" line
    render the supplied names — the display path the real round-2 drive exercises
    (kept as a fast, graph-free check of the widget wiring).
    """
    modal = PointingModal(
        options=[{"id": "c", "name": "Carol"}],
        round_number=2,
        round_cap=NIGHT_ROUND_CAP,
        prior_picks="Round 1 — Alice → Carol",
    )
    app = _ModalHostApp(modal)
    async with app.run_test() as pilot:
        await _wait_for(
            pilot, lambda: isinstance(app.screen, PointingModal), timeout=5.0
        )
        header = _static_text(modal, "pointing-round")
        assert header is not None
        assert "round 2 of 3" in header
        picks = _static_text(modal, "pointing-prior-picks")
        assert picks is not None
        assert "Teammates so far:" in picks
        assert "Round 1 — Alice → Carol" in picks
        app.exit()
    assert app.is_running is False


async def test_pointing_modal_shows_all_targets_without_scroll_on_short_terminal() -> None:
    """All targets are visible (no scroll) on a SHORT terminal, with chrome.

    Regression for the small-terminal squeeze: with the Spec-015 round header
    (``#pointing-round``) + teammates-so-far line (``#pointing-prior-picks``)
    eating dialog rows, the old fixed ``height: 40%`` dialog with a ``1fr``
    OptionList collapsed the list to ~1 visible row and forced scrolling. The
    fix sizes the dialog to its content (``height: auto`` + ``max-height: 90%``)
    and the list to one row per option (``height: auto``), so on any normal
    terminal the full roster shows at once.

    Mounts a 7-target ``PointingModal`` (round 2 of 3, with a prior-picks line)
    at a deliberately short 18-row terminal and asserts the OptionList renders
    EVERY option with no overflow: its visible content height equals its option
    count (so ``virtual_size`` does not exceed the visible region — nothing is
    scrolled off). The round header and teammates-so-far line are still present,
    proving the chrome no longer squeezes the list.
    """
    options = [
        {"id": "a", "name": "Priya"},
        {"id": "b", "name": "Silas"},
        {"id": "c", "name": "Ivy"},
        {"id": "d", "name": "Marco"},
        {"id": "e", "name": "Yuki"},
        {"id": "f", "name": "Dana"},
        {"id": "g", "name": "Aarav"},
    ]
    modal = PointingModal(
        options=options,
        round_number=2,
        round_cap=NIGHT_ROUND_CAP,
        prior_picks="Round 1 — Alice → Carol",
    )
    app = _ModalHostApp(modal)
    # A short terminal: the old 40%-of-18 ≈ 7-row dialog minus thick border +
    # padding + 3 chrome lines left ~1 row for the list. 80 cols keeps names on
    # one line so the height assertion is about vertical sizing only.
    async with app.run_test(size=(80, 18)) as pilot:
        await _wait_for(
            pilot, lambda: isinstance(app.screen, PointingModal), timeout=5.0
        )
        await pilot.pause()

        option_list = modal.query_one("#pointing-options", OptionList)
        # Every option is rendered into the visible content region — the list's
        # drawn height equals the number of options, so none are scrolled off.
        assert option_list.option_count == len(options)
        assert option_list.size.height == len(options), (
            f"expected all {len(options)} options visible without scroll on a "
            f"short terminal; OptionList visible height was "
            f"{option_list.size.height}"
        )
        # The content (virtual) height does not exceed the visible height →
        # there is genuinely nothing to scroll.
        assert option_list.virtual_size.height <= option_list.size.height

        # The Spec-015 chrome is still composed above the list (the fix didn't
        # drop it to make room — the dialog grew instead).
        assert _static_text(modal, "pointing-round") is not None
        assert "round 2 of 3" in _static_text(modal, "pointing-round")
        assert _static_text(modal, "pointing-prior-picks") is not None

        app.exit()
    assert app.is_running is False


async def test_human_law_abiding_never_sees_pointing_modal(
    env: Path,
    fake_small,
    fake_large,
    dynamic_night_pointing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Law-abiding human is never prompted to point during the Night.

    Mirrors the existing law-abiding Night test, but asserts the Slice-3
    no-op: across the full Night (both AI Mafia point and the kill resolves) NO
    ``PointingModal`` is ever pushed onto the screen stack — the human sees only
    the "Night falls."/kill messages. Pointing is Mafia-only (Spec 013
    knowledge-boundary): a non-Mafia human is absent from ``night_mafia_order``,
    so ``mafia_point`` never interrupts them.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)
    fake_large(
        pointings=[],
        day_actions=[
            DayAction(kind="speak", text=f"day-talk-{i}") for i in range(8)
        ],
    )

    # A latch that flips if a PointingModal ever reaches the screen stack.
    modal_ever_seen = {"value": False}

    app = GraphiaApp()
    async with app.run_test() as pilot:
        dynamic_night_pointing(
            lambda: app._graph.get_state(app._run_config).values
        )

        await _submit_name(app, pilot)
        players = await _wait_for_players(app, pilot)

        law_abiding_ids = [
            pid
            for pid, p in players.items()
            if p.role == "law_abiding" and not p.is_human
        ]
        assert law_abiding_ids, "no AI law-abiding player to victimise"
        target_id = law_abiding_ids[0]
        target_name = players[target_id].name

        public_log = app.query_one("#public-log", RichLog)
        kill_line = f"During the night, {target_name} was killed."

        def _modal_present() -> bool:
            return isinstance(app.screen, PointingModal) or any(
                isinstance(s, PointingModal) for s in app.screen_stack
            )

        def _kill_resolved() -> bool:
            # Poll for a modal on every tick while waiting for the kill — if one
            # ever appears for the Law-abiding human it must be caught.
            if _modal_present():
                modal_ever_seen["value"] = True
            if kill_line not in _rich_log_text(public_log):
                return False
            victim = _players_snapshot(app).get(target_id)
            return victim is not None and victim.is_alive is False

        await _wait_for(pilot, _kill_resolved, timeout=10.0)

        # No PointingModal ever surfaced for the Law-abiding human.
        assert modal_ever_seen["value"] is False
        assert not _modal_present()
        # The human saw the ordinary Night messages.
        public_rendered = _rich_log_text(public_log)
        assert "Night falls." in public_rendered
        assert kill_line in public_rendered

        app.exit()
    assert app.is_running is False
