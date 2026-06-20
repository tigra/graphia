"""Spec 018 (End-of-Round Day-Dynamics Recap), Slice 1 + Slice 2 tests.

Slice 1 covers the day-round recap (``recap_enabled`` defaults to ``True`` on
``day_turn`` / ``day_close``):

1. The pure ``render_day_round_recap`` renderer over hand-built state dicts —
   no-execution vs with-execution clause, singular/plural for both role counts
   and the votes line, public-SystemMessage shape, and purity.
2. ``day_turn`` round-wrap (node isolation): exactly one recap on a continuing
   wrap, none mid-round; the round-cap boundary is gated out (with ``day_close``
   posting exactly one closing recap end-to-end — the no-double-post invariant).
3. ``day_close`` closing recap: execution and no-execution cases, plus the
   ``recap_enabled=False`` clean no-op.
4. ``day_votes_initiated`` counter: bumps on a human ``/vote`` and on an AI
   ``DayAction(kind="vote")`` in ``day_turn``; resets to 0 in ``day_open``.
5. Eval-isolation regression: the recap text is never picked up by the three
   AI-speech extractors, so repeated recaps can never inflate the
   repetition/blunder metrics.

Slice 2 adds the ablation off-switch (``GRAPHIA_DAY_ROUND_RECAP``):

6. ``load_config()`` default-on semantics for the env flag — unset/blank ⇒ on;
   an explicit truthy value ⇒ on; an explicit falsy value (``0``/``false``/
   ``no``/``off``) ⇒ off.
7. Off-switch end-to-end through the COMPILED graph: with the env flag falsy,
   a full Day produces ZERO recap markers at every round end and at Day close,
   while the rest of Day behaviour is unchanged (the Day still ends, six rounds,
   the "no one executed" close line still present) — contrasted with the
   default-on case which DOES post recaps, proving the flag actually gates.
   Because ``build_graph`` reads ``load_config()`` for the flag, setting the env
   var before building the graph exercises the real ``_assemble_graph``/
   ``partial`` wiring.
8. Anti-drift: ``runtime/graph_builder.build_runtime_graph`` honours the same
   ``day_round_recap_enabled`` parameter — driving a full Day through a runtime
   graph built with the flag off yields zero recaps, with the flag on yields
   recaps, so local and remote can't diverge.

Per the project's determinism posture (architecture §6) the assertions are
structural — marker-substring presence, counts, counter values — never verbatim
LLM text. Mechanical RNG (the round reshuffle) is pinned by monkeypatching
``graphia.nodes.day._shuffle_order`` (never ``random.seed``). The LLM boundary
is stubbed via ``fake_small`` / ``fake_large`` / ``fake_large_day``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
import graphia.nodes.night as night_nodes
from graphia.config import GraphiaConfig, load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Pointing
from graphia.nodes.day import (
    DAY_MAX_ROUNDS,
    day_close,
    day_open,
    day_turn,
    render_day_round_recap,
)
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.state import GameState, KillRecord, PlayerState
from graphia.tools import eval_dialogue as eval_dialogue_mod
from graphia.tools.blunder_eval import _ai_lines_with_names
from graphia.tools.repetition_experiment import _ai_speeches

AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"

# A stable marker substring drawn from ``DAY_ROUND_RECAP_TEMPLATE`` — robust
# against rewording of the dynamic clauses (it lives in the fixed scaffold).
RECAP_MARKER = " status:"


# ==========================================================================
# Helpers
# ==========================================================================


def _player(
    pid: str,
    name: str,
    role: str,
    *,
    is_human: bool = False,
    is_alive: bool = True,
) -> PlayerState:
    return PlayerState(
        id=pid,
        name=name,
        role=role,  # type: ignore[arg-type]
        is_human=is_human,
        is_alive=is_alive,
    )


def _roster(
    *,
    law_alive: int,
    mafia_alive: int,
    law_dead: int = 0,
    mafia_dead: int = 0,
) -> dict[str, PlayerState]:
    """Build an insertion-ordered ``players`` dict with the requested counts."""
    players: dict[str, PlayerState] = {}
    idx = 0
    for _ in range(law_alive):
        pid = f"la-{idx}"
        players[pid] = _player(pid, f"Citizen{idx}", "law_abiding")
        idx += 1
    for _ in range(law_dead):
        pid = f"la-{idx}"
        players[pid] = _player(
            pid, f"Citizen{idx}", "law_abiding", is_alive=False
        )
        idx += 1
    for _ in range(mafia_alive):
        pid = f"maf-{idx}"
        players[pid] = _player(pid, f"Mobster{idx}", "mafia")
        idx += 1
    for _ in range(mafia_dead):
        pid = f"maf-{idx}"
        players[pid] = _player(pid, f"Mobster{idx}", "mafia", is_alive=False)
        idx += 1
    return players


def _is_recap(msg: object) -> bool:
    return (
        isinstance(msg, SystemMessage)
        and isinstance(msg.content, str)
        and RECAP_MARKER in msg.content
    )


def _recap_messages(messages: list) -> list[SystemMessage]:
    return [m for m in messages if _is_recap(m)]


# ==========================================================================
# 1. Pure ``render_day_round_recap`` over hand-built state
# ==========================================================================


def test_render_recap_no_execution_states_day_counts_votes_and_no_exec() -> None:
    """No execution this cycle: day number, both side counts, votes, no-exec."""
    state: GameState = {
        "cycle": 2,
        "players": _roster(law_alive=4, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    msg = render_day_round_recap(state, day_round=1)
    text = msg.content

    # Spec 020 inserts the in-world clock between the day number and the marker,
    # so assert the two fixed scaffold parts separately rather than the old
    # contiguous "Day 2 status:" literal.
    assert "Day 2" in text
    assert " status:" in text
    assert "4 Law-abiding Citizens" in text
    assert "2 Mafiosos" in text
    assert "No execution votes called yet today." in text
    assert "No one has been executed today." in text


def test_render_recap_with_execution_names_player_and_revealed_side() -> None:
    """An execution this cycle names the player and the revealed side (Mafia)."""
    players = _roster(law_alive=4, mafia_alive=1, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 2,
        "name": "Mobster5",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": [executed],
    }
    text = render_day_round_recap(state, day_round=1).content

    assert "Mobster5 was executed today" in text
    assert "revealed to be Mafia" in text
    # The "no one executed" clause must NOT appear on the execution path.
    assert "No one has been executed today." not in text


def test_render_recap_executed_law_abiding_reveals_law_abiding_side() -> None:
    """An executed Law-abiding Citizen is revealed as a Law-abiding Citizen."""
    players = _roster(law_alive=3, mafia_alive=2, law_dead=1)
    executed: KillRecord = {
        "cycle": 1,
        "name": "Citizen3",
        "cause": "execution",
        "role": "law_abiding",
    }
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 2,
        "kill_log": [executed],
    }
    text = render_day_round_recap(state, day_round=1).content

    assert "Citizen3 was executed today" in text
    assert "revealed to be Law-abiding Citizen" in text


def test_render_recap_only_counts_execution_for_the_current_cycle() -> None:
    """A prior-cycle execution must not surface in this cycle's recap."""
    players = _roster(law_alive=4, mafia_alive=2, mafia_dead=1)
    stale: KillRecord = {
        "cycle": 1,
        "name": "Mobster6",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 0,
        "kill_log": [stale],
    }
    text = render_day_round_recap(state, day_round=1).content
    assert "No one has been executed today." in text
    assert "Mobster6" not in text


@pytest.mark.parametrize(
    ("law_alive", "mafia_alive", "expected_law", "expected_mafia"),
    [
        (1, 1, "1 Law-abiding Citizen ", "1 Mafioso "),
        (1, 2, "1 Law-abiding Citizen ", "2 Mafiosos "),
        (4, 1, "4 Law-abiding Citizens ", "1 Mafioso "),
        (5, 2, "5 Law-abiding Citizens ", "2 Mafiosos "),
    ],
)
def test_render_recap_role_count_singular_vs_plural(
    law_alive: int,
    mafia_alive: int,
    expected_law: str,
    expected_mafia: str,
) -> None:
    """Both role counts pluralize correctly (1 -> singular, N -> plural).

    The trailing space pins the boundary so "1 Mafioso" never matches inside
    "1 Mafiosos" and "1 Law-abiding Citizen" never matches inside the plural.
    """
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=law_alive, mafia_alive=mafia_alive),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    # Recap reads "... {law} and {mafia} remain." — assert the exact clauses.
    text = render_day_round_recap(state, day_round=1).content
    assert f"{expected_law}and {expected_mafia}remain." in text


@pytest.mark.parametrize(
    ("votes", "expected_clause"),
    [
        (0, "No execution votes called yet today."),
        (1, "1 execution vote called today."),
        (3, "3 execution votes called today."),
    ],
)
def test_render_recap_votes_clause_singular_vs_plural(
    votes: int, expected_clause: str
) -> None:
    """The votes line uses 0/1/N phrasing for ``day_votes_initiated``."""
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": votes,
        "kill_log": [],
    }
    assert expected_clause in render_day_round_recap(state, day_round=1).content


def test_render_recap_returns_public_system_message() -> None:
    """The recap is a PUBLIC SystemMessage — no ``private_to`` whisper key."""
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    msg = render_day_round_recap(state, day_round=1)
    assert isinstance(msg, SystemMessage)
    extra = getattr(msg, "additional_kwargs", {}) or {}
    assert "private_to" not in extra


def test_render_recap_does_not_mutate_input_state() -> None:
    """The renderer is pure — it never mutates the state it reads."""
    players = _roster(law_alive=4, mafia_alive=2)
    kill_log: list[KillRecord] = []
    state: GameState = {
        "cycle": 2,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": kill_log,
    }
    before_keys = set(state.keys())
    before_players = dict(players)
    before_alive = {pid: p.is_alive for pid, p in players.items()}

    render_day_round_recap(state, day_round=1)

    assert set(state.keys()) == before_keys
    assert state["kill_log"] == []
    assert state["day_votes_initiated"] == 1
    assert dict(state["players"]) == before_players
    assert {pid: p.is_alive for pid, p in players.items()} == before_alive


# ==========================================================================
# 2. ``day_turn`` round-wrap (node isolation)
# ==========================================================================


def _day_turn_state(
    *,
    order: list[str],
    turn_index: int,
    players: dict[str, PlayerState],
    rounds: int = 0,
    votes_initiated: int = 0,
) -> GameState:
    return {
        "cycle": 1,
        "players": players,
        "day_order": order,
        "day_turn_index": turn_index,
        "day_rounds": rounds,
        "day_votes_called": 0,
        "day_votes_initiated": votes_initiated,
        "active_vote": None,
        "messages": [],
        "kill_log": [],
    }


def test_day_turn_round_wrap_appends_exactly_one_recap(
    fake_large_day,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AI speak on the final slot of a continuing round appends one recap.

    The wrapping turn is an AI speaker at the last index of a 2-player order
    (rounds 0 -> 1, well under the cap) so ``_round_complete_update`` fires the
    recap. The reshuffle is pinned so the test never touches the global RNG.
    """
    monkeypatch.setattr(
        day_nodes, "_shuffle_order", lambda players: list(players.keys())
    )
    fake_large_day(texts=["I have a hunch about someone."])

    players = {
        "la-0": _player("la-0", "Citizen0", "law_abiding"),
        "maf-0": _player("maf-0", "Mobster0", "mafia"),
    }
    # The AI at index 1 speaks; new_turn_index (2) == len(order) -> round wrap.
    state = _day_turn_state(
        order=["la-0", "maf-0"], turn_index=1, players=players, rounds=0
    )

    update = day_turn(state)

    assert update["day_rounds"] == 1
    assert update["day_turn_index"] == 0
    recaps = _recap_messages(update.get("messages", []))
    assert len(recaps) == 1, f"expected exactly one recap, got {recaps!r}"
    # The speech precedes the recap (recap is last).
    msgs = update["messages"]
    assert isinstance(msgs[0], AIMessage)
    assert _is_recap(msgs[-1])


def test_day_turn_mid_round_appends_no_recap(
    fake_large_day,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-wrapping (mid-round) AI turn posts no recap."""
    monkeypatch.setattr(
        day_nodes, "_shuffle_order", lambda players: list(players.keys())
    )
    fake_large_day(texts=["Still gathering my thoughts."])

    players = {
        "la-0": _player("la-0", "Citizen0", "law_abiding"),
        "maf-0": _player("maf-0", "Mobster0", "mafia"),
        "la-1": _player("la-1", "Citizen1", "law_abiding"),
    }
    # AI at index 0 of a 3-player order: new_turn_index (1) < len(order) — no wrap.
    state = _day_turn_state(
        order=["la-0", "maf-0", "la-1"], turn_index=0, players=players, rounds=0
    )

    update = day_turn(state)

    assert update["day_turn_index"] == 1
    assert "day_rounds" not in update  # mid-round: no wrap bookkeeping
    assert _recap_messages(update.get("messages", [])) == []


def test_day_turn_round_cap_wrap_is_gated_out(
    fake_large_day,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the round-cap boundary, ``day_turn`` stays silent (no recap).

    Wrapping from ``DAY_MAX_ROUNDS - 1`` pushes ``day_rounds`` to the cap, where
    the ``new_rounds < DAY_MAX_ROUNDS`` gate suppresses the recap so ``day_close``
    can own that boundary's single recap.
    """
    monkeypatch.setattr(
        day_nodes, "_shuffle_order", lambda players: list(players.keys())
    )
    fake_large_day(texts=["Final word for the round."])

    players = {
        "la-0": _player("la-0", "Citizen0", "law_abiding"),
        "maf-0": _player("maf-0", "Mobster0", "mafia"),
    }
    state = _day_turn_state(
        order=["la-0", "maf-0"],
        turn_index=1,
        players=players,
        rounds=DAY_MAX_ROUNDS - 1,
    )

    update = day_turn(state)

    assert update["day_rounds"] == DAY_MAX_ROUNDS
    assert _recap_messages(update.get("messages", [])) == [], (
        "round-cap wrap must be gated out; day_close owns that recap"
    )


def test_day_turn_round_wrap_respects_recap_disabled(
    fake_large_day,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``recap_enabled=False`` suppresses the round-wrap recap (off path)."""
    monkeypatch.setattr(
        day_nodes, "_shuffle_order", lambda players: list(players.keys())
    )
    fake_large_day(texts=["A continuing-round remark."])

    players = {
        "la-0": _player("la-0", "Citizen0", "law_abiding"),
        "maf-0": _player("maf-0", "Mobster0", "mafia"),
    }
    state = _day_turn_state(
        order=["la-0", "maf-0"], turn_index=1, players=players, rounds=0
    )

    update = day_turn(state, recap_enabled=False)

    assert update["day_rounds"] == 1
    assert _recap_messages(update.get("messages", [])) == []
    # The speech still lands — only the recap is suppressed.
    assert any(isinstance(m, AIMessage) for m in update.get("messages", []))


# ==========================================================================
# 3. ``day_close`` closing recap
# ==========================================================================


def test_day_close_no_execution_posts_recap_with_no_exec_clause() -> None:
    """No-execution close: close line + a recap with the no-one-executed clause."""
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    update = day_close(state)
    contents = [
        m.content for m in update["messages"] if isinstance(m, SystemMessage)
    ]

    assert any("The Day ends with no one executed." in c for c in contents)
    recaps = _recap_messages(update["messages"])
    assert len(recaps) == 1
    assert "No one has been executed today." in recaps[0].content


def test_day_close_execution_posts_recap_naming_player_and_side() -> None:
    """Execution close: the closing recap names the executed player + side.

    On the execution path ``resolve_vote`` already posted the reveal, so
    ``day_close`` emits no extra close line — only the recap, which carries the
    post-execution standings and the executed-today clause.
    """
    players = _roster(law_alive=4, mafia_alive=1, mafia_dead=1)
    executed: KillRecord = {
        "cycle": 1,
        "name": "Mobster5",
        "cause": "execution",
        "role": "mafia",
    }
    state: GameState = {
        "cycle": 1,
        "players": players,
        "day_votes_initiated": 1,
        "kill_log": [executed],
    }
    update = day_close(state)

    recaps = _recap_messages(update["messages"])
    assert len(recaps) == 1
    text = recaps[0].content
    assert "Mobster5 was executed today" in text
    assert "revealed to be Mafia" in text
    # The generic no-execution close line is NOT emitted on the execution path.
    contents = [
        m.content for m in update["messages"] if isinstance(m, SystemMessage)
    ]
    assert not any("no one executed" in c for c in contents)


def test_day_close_recap_disabled_is_clean_no_op() -> None:
    """``recap_enabled=False`` posts no recap; the prior close behavior holds.

    No-execution + recap off must collapse to exactly the legacy single close
    line (no recap). An execution + recap off returns the pre-spec ``{}`` no-op
    (``resolve_vote`` already posted everything).
    """
    no_exec_state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
    }
    update = day_close(no_exec_state, recap_enabled=False)
    assert _recap_messages(update.get("messages", [])) == []
    contents = [m.content for m in update["messages"]]
    assert contents == ["The Day ends with no one executed."]

    executed: KillRecord = {
        "cycle": 1,
        "name": "Mobster5",
        "cause": "execution",
        "role": "mafia",
    }
    exec_state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=4, mafia_alive=1, mafia_dead=1),
        "day_votes_initiated": 1,
        "kill_log": [executed],
    }
    exec_update = day_close(exec_state, recap_enabled=False)
    # Execution path with recap off: no close line, no recap -> empty no-op.
    assert exec_update == {}


# ==========================================================================
# 4. ``day_votes_initiated`` counter
# ==========================================================================


def test_day_open_resets_day_votes_initiated_to_zero() -> None:
    """``day_open`` zeroes ``day_votes_initiated`` (alongside the other counters)."""
    state: GameState = {
        "cycle": 1,
        "players": _roster(law_alive=5, mafia_alive=2),
        "kill_log": [],
        "day_votes_initiated": 3,
    }
    update = day_open(state)
    assert update["day_votes_initiated"] == 0


def test_day_turn_ai_vote_increments_day_votes_initiated(
    fake_large_day,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AI ``DayAction(kind="vote")`` bumps ``day_votes_initiated`` by one."""
    monkeypatch.setattr(
        day_nodes, "_shuffle_order", lambda players: list(players.keys())
    )
    players = {
        "la-0": _player("la-0", "Citizen0", "law_abiding"),
        "maf-0": _player("maf-0", "Mobster0", "mafia"),
        "la-1": _player("la-1", "Citizen1", "law_abiding"),
    }
    # The AI at index 0 votes against a valid, alive, non-self target.
    fake_large_day(
        outputs=[DayAction(kind="vote", target_id="la-1")]
    )
    state = _day_turn_state(
        order=["la-0", "maf-0", "la-1"],
        turn_index=0,
        players=players,
        rounds=0,
        votes_initiated=0,
    )

    update = day_turn(state)

    assert update.get("active_vote") is not None
    assert update["day_votes_initiated"] == 1


def test_day_turn_human_slash_vote_increments_day_votes_initiated(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human ``/vote <name>`` bumps ``day_votes_initiated`` end-to-end.

    Reuses the test_slice7_vote.py drive-to-human-day_turn harness: advance the
    compiled graph to the human's own Day turn (counter still 0), resume with a
    valid ``/vote <prefix>`` against a unique alive AI, and assert the counter
    ticked to exactly 1 in the resulting graph state.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)
    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive_to_human_day_turn(graph, run_config, fake)

    pre = graph.get_state(run_config).values
    assert pre.get("day_votes_initiated", 0) == 0

    mafia_id = _alive_mafia_ai_id(graph, run_config)
    mafia_name = _players(graph, run_config)[mafia_id].name
    prefix = mafia_name[:3]
    alive_names = [
        p.name for p in _players(graph, run_config).values() if p.is_alive
    ]
    matching = [n for n in alive_names if prefix.lower() in n.lower()]
    assert matching == [mafia_name], (
        f"prefix {prefix!r} is ambiguous across alive roster {alive_names!r}"
    )

    _drive(graph, run_config, Command(resume=f"/vote {prefix}"))

    state = graph.get_state(run_config).values
    assert state.get("day_votes_initiated") == 1, (
        f"expected day_votes_initiated==1 after the human's /vote, got "
        f"{state.get('day_votes_initiated')!r}"
    )


# ==========================================================================
# 5. No double-post at the round-cap boundary (end-to-end)
# ==========================================================================


def test_six_rounds_boundary_posts_exactly_one_recap(
    env: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the six-round cap, exactly ONE recap marks that boundary.

    Mirrors ``test_slice6_day.py::test_six_rounds_without_vote_ends_day``: drive
    the compiled graph directly (no Textual), AIs only ever speak (never vote),
    so the Day runs to the round cap. ``day_turn`` is gated out at the cap and
    ``day_close`` posts the single closing recap. We assert recaps over rounds
    1..5 (one each) plus the close recap == DAY_MAX_ROUNDS total, and crucially
    that the cap boundary itself contributes exactly one (no double-post).
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    class _InfLargeDay:
        call_count = 0

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.call_count += 1
            return DayAction(kind="speak", text=f"msg-ai-{self.call_count}")

    class _InfLargePointing:
        def __init__(self, pick: Callable[[], str]) -> None:
            self._pick = pick

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            return Pointing(target_id=self._pick())

    graph_ref: list = []
    run_config_ref: list = []

    def _live_victim() -> str:
        g = graph_ref[0]
        rc = run_config_ref[0]
        players = g.get_state(rc).values.get("players", {})
        candidates = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        if not candidates:
            candidates = [p.id for p in players.values() if p.is_alive]
        return candidates[0]

    monkeypatch.setattr(
        "graphia.nodes.day.get_large", lambda: _InfLargeDay()
    )
    monkeypatch.setattr(
        "graphia.nodes.night.get_large",
        lambda: _InfLargePointing(_live_victim),
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    graph_ref.append(graph)
    run_config_ref.append(run_config)

    def _day_closed() -> bool:
        state = graph.get_state(run_config).values
        for msg in state.get("messages", []):
            if (
                isinstance(msg, SystemMessage)
                and "The Day ends with no one executed." in msg.content
            ):
                return True
        return False

    def _run_until_close(payload) -> None:
        for _ in graph.stream(payload, run_config, stream_mode="updates"):
            if _day_closed():
                return

    _run_until_close({"messages": []})
    first = _collect_interrupt(graph, run_config)
    assert first is not None and first.get("kind") == "name"

    for _ in range(120):
        if _day_closed():
            break
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            break
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            _run_until_close(None)
            continue
        kind = iv.get("kind")
        if kind == "name":
            resume_value: str = HUMAN_NAME
        elif kind == "day_turn":
            resume_value = "I speak briefly."
        elif kind == "point":
            options = iv.get("options") or []
            resume_value = options[0]["id"] if options else _live_victim()
        else:
            raise AssertionError(f"Unexpected interrupt kind: {kind!r}")
        _run_until_close(Command(resume=resume_value))

    assert _day_closed(), "the Day never closed within the budget"

    state = graph.get_state(run_config).values
    assert state.get("day_rounds") == DAY_MAX_ROUNDS

    recaps = _recap_messages(state.get("messages", []))
    # Rounds 1..(DAY_MAX_ROUNDS-1) each emit a day_turn recap; the cap boundary
    # is gated out of day_turn and the single closing recap is posted by
    # day_close — DAY_MAX_ROUNDS recaps total, with the cap boundary owning
    # exactly one (not two).
    assert len(recaps) == DAY_MAX_ROUNDS, (
        f"expected {DAY_MAX_ROUNDS} recaps (no double-post at the cap), "
        f"got {len(recaps)}"
    )


# ==========================================================================
# 6. Eval-isolation regression
# ==========================================================================


class _FakeGraphState:
    """Minimal ``graph.get_state(run_config)`` stand-in for ``_ai_speeches``.

    ``repetition_experiment._ai_speeches`` reads ``graph.get_state(rc).values``,
    so a stub exposing a ``.values`` mapping is all the extractor needs.
    """

    def __init__(self, values: dict) -> None:
        self.values = values


class _FakeGraph:
    def __init__(self, values: dict) -> None:
        self._values = values

    def get_state(self, _run_config) -> _FakeGraphState:
        return _FakeGraphState(self._values)


def test_recap_excluded_from_ai_speech_extraction() -> None:
    """Recap text is invisible to all three AI-speech extractors.

    Build a final-state messages list mixing AI ``AIMessage``s (with ``name`` in
    the ai-names set) and recap ``SystemMessage``s, then assert none of the three
    extractors returns the recap text — so repeated recaps can never inflate the
    repetition / blunder metrics.
    """
    players = {
        "la-0": _player("la-0", "Aarav", "law_abiding"),
        "maf-0": _player("maf-0", "Bianca", "mafia"),
        "human": _player("human", HUMAN_NAME, "law_abiding", is_human=True),
    }

    recap_one = render_day_round_recap(
        {
            "cycle": 1,
            "players": players,
            "day_votes_initiated": 0,
            "kill_log": [],
        },
        day_round=1,
    )
    recap_two = render_day_round_recap(
        {
            "cycle": 2,
            "players": players,
            "day_votes_initiated": 1,
            "kill_log": [],
        },
        day_round=2,
    )

    ai_line_a = "I think Bianca has been too quiet."
    ai_line_b = "Let's keep watching the votes carefully."
    human_line = "I'm not sure who to trust yet."

    messages = [
        recap_one,
        AIMessage(content=ai_line_a, name="Aarav"),
        AIMessage(content=ai_line_b, name="Bianca"),
        # Scripted human line: excluded by the name filter (human not in ai_names).
        AIMessage(content=human_line, name=HUMAN_NAME),
        recap_two,
    ]
    state = {"players": players, "messages": messages}

    # --- blunder_eval._ai_lines_with_names (takes a final-state dict) ---
    lines, ai_names = _ai_lines_with_names(state)
    assert ai_line_a in lines and ai_line_b in lines
    assert human_line not in lines  # human excluded by ai-names filter
    assert RECAP_MARKER not in " ".join(lines)
    assert recap_one.content not in lines and recap_two.content not in lines
    assert ai_names == {"Aarav", "Bianca"}

    # --- repetition_experiment._ai_speeches (takes graph, run_config, names) ---
    rep_lines = _ai_speeches(_FakeGraph(state), {}, ai_names)
    assert ai_line_a in rep_lines and ai_line_b in rep_lines
    assert RECAP_MARKER not in " ".join(rep_lines)
    assert recap_one.content not in rep_lines

    # --- eval_dialogue's inline extraction (replicate its exact predicate) ---
    # ``eval_dialogue`` has no standalone extractor; its extraction is the same
    # AIMessage-name-content predicate inlined in ``_play_one_game``. Apply that
    # predicate verbatim against the same names so the regression pins it too.
    ai_names_ed = {
        p.name for p in state["players"].values() if not p.is_human
    }
    ed_lines = [
        m.content.strip()
        for m in state["messages"]
        if isinstance(m, AIMessage)
        and getattr(m, "name", None) in ai_names_ed
        and isinstance(m.content, str)
        and m.content.strip()
    ]
    assert ai_line_a in ed_lines and ai_line_b in ed_lines
    assert RECAP_MARKER not in " ".join(ed_lines)
    assert recap_one.content not in ed_lines
    # Sanity: the eval_dialogue module exposes the constants the extraction
    # references, confirming the import path is the real harness module.
    assert hasattr(eval_dialogue_mod, "_play_one_game")


# ==========================================================================
# Shared harness for the end-to-end human-/vote test (mirrors test_slice7_vote)
# ==========================================================================


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _drive(graph, run_config, payload) -> None:
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 50)
    for _ in graph.stream(payload, bounded, stream_mode="updates"):
        pass


def _players(graph, run_config) -> dict:
    return graph.get_state(run_config).values.get("players", {})


def _alive_mafia_ai_id(graph, run_config) -> str:
    players = _players(graph, run_config)
    ids = [
        p.id
        for p in players.values()
        if p.is_alive and p.role == "mafia" and not p.is_human
    ]
    assert ids, "expected at least one alive AI Mafia"
    return ids[0]


def _ai_point_target_from_prompt(messages) -> str:
    """Resolve a Night-pointing target from the mafia-point PROMPT roster.

    Parses the rendered ``"name: id"`` roster lines in the prompt (never reading
    mid-stream graph state, which would be a stale pre-``assign_roles`` snapshot)
    and returns the first AI's id — a live, valid, non-human target.
    """
    text = "\n".join(
        c if isinstance(c := getattr(m, "content", ""), str) else str(c)
        for m in messages
    )
    for line in text.splitlines():
        name, sep, ident = line.partition(":")
        name = name.strip()
        if not sep or name not in AI_NAMES:
            continue
        return ident.strip()
    raise AssertionError(
        "no matching alive Law-abiding AI in the mafia-point roster:\n" + text
    )


def _advance_until(
    graph,
    run_config,
    *,
    stop: Callable[[], bool],
    interrupt_responder: Callable[[dict[str, Any]], Any],
    budget: int = 200,
) -> None:
    for _ in range(budget):
        if stop():
            return
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            _drive(graph, run_config, None)
            continue
        resume = interrupt_responder(interrupt_value)
        _drive(graph, run_config, Command(resume=resume))


def _drive_to_human_day_turn(graph, run_config, fake) -> None:
    """Advance from the name interrupt to the first HUMAN day_turn interrupt."""
    _drive(graph, run_config, {"messages": []})

    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            return Pointing(target_id=_ai_point_target_from_prompt(messages))
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    _drive(graph, run_config, Command(resume=HUMAN_NAME))

    # AIs that speak before the human just chatter; ballots are irrelevant here.
    fake._queues[DayAction] = [
        DayAction(kind="speak", text=f"AI speaks ({i}).") for i in range(40)
    ]
    fake._last.pop(DayAction, None)
    fake._queues[Ballot] = [Ballot(yes=True)] * 20
    fake._last.pop(Ballot, None)

    def _is_human_day_turn() -> bool:
        iv = _collect_interrupt(graph, run_config)
        return bool(
            iv
            and iv.get("kind") == "day_turn"
            and iv.get("speaker_name") == HUMAN_NAME
        )

    def _respond_speak(iv: dict[str, Any]) -> str:
        kind = iv.get("kind")
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "yes"
        return ""

    _advance_until(
        graph,
        run_config,
        stop=_is_human_day_turn,
        interrupt_responder=_respond_speak,
        budget=100,
    )

    iv = _collect_interrupt(graph, run_config)
    assert iv is not None and iv.get("speaker_name") == HUMAN_NAME, (
        f"expected human day_turn interrupt, got {iv!r}"
    )


# ==========================================================================
# Slice 2: ablation off-switch (``GRAPHIA_DAY_ROUND_RECAP``)
# ==========================================================================

# --------------------------------------------------------------------------
# 7. ``load_config()`` default-on semantics for ``GRAPHIA_DAY_ROUND_RECAP``
# --------------------------------------------------------------------------


def test_load_config_recap_default_on_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ⇒ recap on (the documented default)."""
    monkeypatch.delenv("GRAPHIA_DAY_ROUND_RECAP", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().day_round_recap_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_load_config_recap_blank_is_default_on(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank/whitespace value is treated as unset ⇒ on (``_env_flag``)."""
    monkeypatch.setenv("GRAPHIA_DAY_ROUND_RECAP", blank)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().day_round_recap_enabled is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "On"])
def test_load_config_recap_truthy_value_enables(
    truthy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any truthy value keeps the recap on."""
    monkeypatch.setenv("GRAPHIA_DAY_ROUND_RECAP", truthy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().day_round_recap_enabled is True


@pytest.mark.parametrize(
    "falsy", ["0", "false", "FALSE", "no", "off", "Off", "anything-else"]
)
def test_load_config_recap_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit falsy value (or any non-truthy token) disables the recap.

    ``_env_flag`` returns membership in the truthy set for a non-blank value, so
    every value that is not in ``{1,true,yes,on}`` reads as off — the off-switch
    is the documented ``0``/``false``/``no``/``off`` family, asserted here.
    """
    monkeypatch.setenv("GRAPHIA_DAY_ROUND_RECAP", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")

    assert load_config().day_round_recap_enabled is False


# --------------------------------------------------------------------------
# Shared full-Day drive harness (mirrors
# ``test_slice6_day.py::test_six_rounds_without_vote_ends_day``)
# --------------------------------------------------------------------------

DAY_CLOSE_LINE = "The Day ends with no one executed."


class _InfLargeDay:
    """AIs that always SPEAK (never vote) — the Day runs to the round cap."""

    def __init__(self) -> None:
        self.call_count = 0

    def with_structured_output(self, schema):  # noqa: ANN001, ANN201
        return self

    def invoke(self, messages):  # noqa: ANN001, ANN201
        self.call_count += 1
        return DayAction(kind="speak", text=f"msg-ai-{self.call_count}")


class _InfLargePointing:
    """Stateless Night-pointing fake: picks a live victim at invoke time."""

    def __init__(self, pick: Callable[[], str]) -> None:
        self._pick = pick

    def with_structured_output(self, schema):  # noqa: ANN001, ANN201
        return self

    def invoke(self, messages):  # noqa: ANN001, ANN201
        return Pointing(target_id=self._pick())


def _install_full_day_llm_fakes(
    graph,
    run_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch the Day/Night large-model bindings so a Day runs to the cap.

    The human is pinned Law-abiding (caller sets ``GRAPHIA_ROLE``) so the
    ``point`` interrupt never fires; AIs only ever speak, so no vote is ever
    called and the Day runs the full six rounds to ``day_close``.
    """

    def _live_victim() -> str:
        players = graph.get_state(run_config).values.get("players", {})
        candidates = [
            p.id
            for p in players.values()
            if p.is_alive and p.role == "law_abiding" and not p.is_human
        ]
        if not candidates:
            candidates = [p.id for p in players.values() if p.is_alive]
        return candidates[0]

    monkeypatch.setattr(day_nodes, "get_large", lambda: _InfLargeDay())
    monkeypatch.setattr(
        night_nodes, "get_large", lambda: _InfLargePointing(_live_victim)
    )


def _drive_full_day_to_close(graph, run_config) -> dict:
    """Drive the compiled graph to the first Day's close; return final state.

    Resumes the ``name`` interrupt and each ``day_turn`` interrupt with scripted
    strings, stopping the moment the Day-close line is emitted. Mirrors the
    ``test_slice6_day`` drive loop, factored so the off-switch and runtime-graph
    tests share one harness.
    """

    def _day_closed() -> bool:
        for msg in graph.get_state(run_config).values.get("messages", []):
            if isinstance(msg, SystemMessage) and DAY_CLOSE_LINE in msg.content:
                return True
        return False

    def _run_until_close(payload) -> None:
        for _ in graph.stream(payload, run_config, stream_mode="updates"):
            if _day_closed():
                return

    _run_until_close({"messages": []})
    first = _collect_interrupt(graph, run_config)
    assert first is not None and first.get("kind") == "name", (
        f"expected the name interrupt first; got {first!r}"
    )

    for _ in range(120):
        if _day_closed():
            break
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            break
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            _run_until_close(None)
            continue
        kind = iv.get("kind")
        if kind == "name":
            resume_value: str = HUMAN_NAME
        elif kind == "day_turn":
            resume_value = "I speak briefly."
        elif kind == "point":
            options = iv.get("options") or []
            resume_value = (
                options[0]["id"]
                if options
                else next(
                    p.id
                    for p in graph.get_state(run_config)
                    .values["players"]
                    .values()
                    if p.is_alive
                )
            )
        else:
            raise AssertionError(f"unexpected interrupt kind: {kind!r}")
        _run_until_close(Command(resume=resume_value))

    assert _day_closed(), "the Day never closed within the drive budget"
    return graph.get_state(run_config).values


# --------------------------------------------------------------------------
# 8. Off-switch end-to-end through the COMPILED graph (the key criterion)
# --------------------------------------------------------------------------


def test_off_switch_disables_recap_end_to_end(
    env: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRAPHIA_DAY_ROUND_RECAP=0`` ⇒ ZERO recaps anywhere in a full Day.

    Drives the COMPILED graph (built AFTER setting the env var, so ``build_graph``
    reads the flag from ``load_config()`` and threads it through
    ``_assemble_graph``'s ``partial`` binding). The Day still ends after the full
    six rounds with the "no one executed" close line — only the recap is gone.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_DAY_ROUND_RECAP", "0")
    fake_small(AI_NAMES)

    config = load_config()
    assert config.day_round_recap_enabled is False  # the env var was honoured
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    _install_full_day_llm_fakes(graph, run_config, monkeypatch)

    state = _drive_full_day_to_close(graph, run_config)

    # The key acceptance criterion: not a single recap marker anywhere.
    assert _recap_messages(state.get("messages", [])) == [], (
        "recap markers leaked through with GRAPHIA_DAY_ROUND_RECAP off"
    )
    # ...and the rest of Day behaviour is unchanged.
    assert state.get("day_rounds") == DAY_MAX_ROUNDS
    contents = [
        m.content
        for m in state.get("messages", [])
        if isinstance(m, SystemMessage)
    ]
    assert any(DAY_CLOSE_LINE in c for c in contents), (
        "the no-one-executed close line must still be posted with recap off"
    )


def test_default_on_posts_recaps_end_to_end_for_contrast(
    env: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var ⇒ recaps DO appear — the contrast that proves the switch gates.

    Identical drive to the off-switch test but with ``GRAPHIA_DAY_ROUND_RECAP``
    left unset (default on). Recaps appear at every round boundary, so the
    off-switch test's empty result is attributable to the flag, not the harness.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.delenv("GRAPHIA_DAY_ROUND_RECAP", raising=False)
    fake_small(AI_NAMES)

    config = load_config()
    assert config.day_round_recap_enabled is True
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    _install_full_day_llm_fakes(graph, run_config, monkeypatch)

    state = _drive_full_day_to_close(graph, run_config)

    assert state.get("day_rounds") == DAY_MAX_ROUNDS
    recaps = _recap_messages(state.get("messages", []))
    # Rounds 1..(cap-1) each post one day_turn recap + the day_close recap:
    # exactly DAY_MAX_ROUNDS, the same invariant as the Slice-1 no-double-post
    # test — and crucially MORE than zero (the off-switch test's contrast).
    assert len(recaps) == DAY_MAX_ROUNDS, (
        f"expected {DAY_MAX_ROUNDS} recaps with the flag on (contrast vs off), "
        f"got {len(recaps)}"
    )


# --------------------------------------------------------------------------
# 9. Both builders honour the flag — runtime graph anti-drift
# --------------------------------------------------------------------------


def _build_runtime_graph_for_day(
    env_path: Path, thread_id: str, *, day_round_recap_enabled: bool
):
    """Compile a runtime graph onto a tmp checkpoint dir under ``env_path``.

    ``build_runtime_graph`` shares ``_assemble_graph`` with ``build_graph``, so a
    runtime-built graph drives a Day identically — letting us prove the
    ``day_round_recap_enabled`` parameter threads through to the real Day nodes.
    """
    checkpoint_dir = env_path.parent / f"rt-checkpoints-{thread_id}"
    return build_runtime_graph(
        thread_id,
        checkpoint_dir,
        day_round_recap_enabled=day_round_recap_enabled,
    )


def test_build_runtime_graph_honors_recap_disabled(
    env: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime graph built with the flag OFF posts zero recaps in a full Day.

    The strongest practical check: ``build_runtime_graph`` accepts and threads
    ``day_round_recap_enabled`` all the way into ``day_turn`` / ``day_close``, so
    the deployed Runtime honours the same ablation switch as local mode.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    thread_id = "20260619T120000"
    graph = _build_runtime_graph_for_day(
        env, thread_id, day_round_recap_enabled=False
    )
    run_config = make_run_config(thread_id)
    _install_full_day_llm_fakes(graph, run_config, monkeypatch)

    state = _drive_full_day_to_close(graph, run_config)

    assert _recap_messages(state.get("messages", [])) == [], (
        "build_runtime_graph did not thread day_round_recap_enabled=False "
        "through to the Day nodes — recaps leaked on the runtime path"
    )
    assert state.get("day_rounds") == DAY_MAX_ROUNDS


def test_build_runtime_graph_honors_recap_enabled(
    env: Path,
    fake_small,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime graph built with the flag ON posts recaps — the gate contrast.

    Defaulting/forcing ``day_round_recap_enabled=True`` on the runtime path posts
    the same DAY_MAX_ROUNDS recaps as local mode, so the disabled-path test's
    empty result is attributable to the flag and the two builders can't drift.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)

    thread_id = "20260619T120100"
    graph = _build_runtime_graph_for_day(
        env, thread_id, day_round_recap_enabled=True
    )
    run_config = make_run_config(thread_id)
    _install_full_day_llm_fakes(graph, run_config, monkeypatch)

    state = _drive_full_day_to_close(graph, run_config)

    recaps = _recap_messages(state.get("messages", []))
    assert len(recaps) == DAY_MAX_ROUNDS, (
        f"expected {DAY_MAX_ROUNDS} recaps on the runtime path with the flag "
        f"on (contrast vs the disabled runtime test), got {len(recaps)}"
    )


def test_build_runtime_graph_recap_flag_is_keyword_threaded() -> None:
    """Narrow signature guard: the parameter exists with the documented default.

    Complements the end-to-end runtime drives above — it pins the public
    contract (``day_round_recap_enabled`` keyword, default ``True``) so a future
    refactor that drops or renames the parameter fails fast even if the drive
    tests were skipped.
    """
    import inspect

    params = inspect.signature(build_runtime_graph).parameters
    assert "day_round_recap_enabled" in params
    flag = params["day_round_recap_enabled"]
    assert flag.kind is inspect.Parameter.KEYWORD_ONLY
    assert flag.default is True

    # And the dataclass field the production entrypoint reads is on by default.
    cfg = GraphiaConfig(
        bearer_token=None,
        aws_region="us-east-1",
        log_file=Path("x"),
        checkpoint_dir=Path("x"),
        stats_file=Path("x"),
        human_role=None,
        remote_mode=False,
        runtime_invocation_url=None,
        memory_id=None,
        career_memory_id=None,
        gateway_id=None,
        gateway_url=None,
        cloudwatch_log_group=None,
        stats_strategy_id=None,
        stats_namespace=None,
    )
    assert cfg.day_round_recap_enabled is True
