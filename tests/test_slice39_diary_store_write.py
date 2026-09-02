"""Spec 039 (Per-AI Private Diaries) — Slice 3: the dual write reaches the store.

Slice 3 gave ``nodes/day.py:day_diary`` a second destination. Each accepted
entry now goes to TWO places: the ``private_diaries`` state channel (Slices 1–2,
covered in ``tests/test_slice39_diary_before_night.py`` and
``tests/test_slice39_diary_interleave.py``) and the ``DiaryStore`` — via the new
``_persist_diary`` helper, at ``night_index = record["day"] + 1``. It also
DELETED ``night_close``'s synthetic placeholder write, which had stood since
spec 002, while keeping that node's read-back loop as the liveness probe of the
Gateway-fronted read path.

WHY THIS FILE EXISTS — the coverage hole Slice 3 opened
-------------------------------------------------------

Deleting ``night_close``'s write loop took its per-player guard test with it.
``test_night_close_swallows_failing_store_write`` pinned exactly that loop's
``try/except`` and was rewritten in place, in
``tests/test_diary_store.py``, as ``test_night_close_no_longer_writes_to_the_store``
— a test of the RETIREMENT, which by construction proves nothing about the new
helper. ``_persist_diary``'s failure-containment guard therefore arrived
UNCOVERED, and closing that hole is this file's first responsibility. A store
failure must:

1. not prevent the ``private_diaries`` state delta from being returned,
2. not skip the remaining players in the fan-out,
3. be LOGGED rather than silently swallowed.

Section "Slice 3.4" below covers all three in one parametrised sweep over which
writer fails (first / middle / last / all), because failing only the LAST writer
cannot tell "the loop continued" apart from "there was nothing left to skip".

THE HONEST ASYMMETRY (tech-spec §2.7), recorded here as the task requires
------------------------------------------------------------------------

With ``GRAPHIA_PRIVATE_DIARIES=0`` NO DIARY REACHES THE STORE AT ALL. The
retired spec-002 synthetic entry does NOT come back in its place — that write
was deleted outright rather than made conditional — so the off arm's store is
simply empty. Flag-off is therefore identical in everything ADR 011 is about
(the prompts, the public message stream, the eval transcript, all pinned
elsewhere in this spec's tests) but it is **not** byte-identical in the store.
That is the intended reading of the ablation — the retired entry was never
gameplay, only a persistence smoke — and it is the one respect in which
flag-off does not reproduce prior behaviour. See
:func:`test_flag_off_writes_no_diary_and_the_placeholder_does_not_return`.

Anti-vacuity discipline followed here
-------------------------------------

* **"No writes" is vacuously true of a node that never received a store.** Every
  such assertion is paired with positive evidence that the same store, in the
  same shape, does receive writes (or, for ``night_close``, that its ``read`` was
  reached). :func:`test_flag_off_writes_no_diary_and_the_placeholder_does_not_return`
  runs the on arm and the off arm against the SAME store instance.
* **A delegation assertion alone is self-agreeing.** Where the stored text is
  specified as "whatever the clamp produced", the assertion pins a written-out
  literal as well as the contract, so mutating ``_clamp_diary_entry`` cannot move
  both sides together.
* **Every "wrote something" assertion pins the scripted sentinel by value**, never
  mere non-emptiness — ``_DIARY_FALLBACK`` is non-empty too, so a swallowed-model-
  failure world would otherwise pass.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import HumanMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.diary_store import DiaryEntry, InProcessDiaryStore
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Diary, Pointing, Reflection
from graphia.nodes.day import (
    DIARY_MAX_CHARS,
    _DIARY_FALLBACK,
    _persist_diary,
    day_diary,
)
from graphia.state import DiaryRecord, GameState, PlayerState

# The logger ``_persist_diary`` reports a swallowed failure through. Written out
# rather than derived from ``day_nodes.logger.name`` so the test pins the channel
# an operator would actually grep, instead of agreeing with whatever the module
# happens to be called.
DAY_LOGGER = "graphia.nodes.day"
NIGHT_LOGGER = "graphia.nodes.night"

# A sentinel no production string could produce, so an exact match proves the
# model's text reached the STORE untouched — and that what reached it is not
# ``_DIARY_FALLBACK``.
SCRIPTED_ENTRY = "SCRIPTED-DIARY-039: the miller would not meet my eye today."

# The retired spec-002 placeholder was written as
# ``f"Night {cycle} diary placeholder for {player.id}"``. This is its invariant
# substring — the part no interpolation can change — and it is what a grep for
# the dead write has to come back empty on.
RETIRED_PLACEHOLDER_FRAGMENT = "diary placeholder for "

# A deliberately multi-line, over-spaced entry, plus the single-line form Slice
# 2's clamp folds it into. FOLDED_ENTRY is a WRITTEN-OUT LITERAL on purpose: the
# claim is "the clamp's single placement reaches the store", and asserting the
# stored text equals ``_clamp_diary_entry(MULTILINE_ENTRY)`` would let a mutated
# clamp move both sides of the equation together.
MULTILINE_ENTRY = (
    "Day one is done.\n\n"
    "   The miller would not meet my eye.\t\tI will watch him tonight.\n"
)
FOLDED_ENTRY = (
    "Day one is done. The miller would not meet my eye. I will watch him tonight."
)


# ==========================================================================
# Shared hand-built helpers
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


def _mixed_table() -> dict[str, PlayerState]:
    """2 AI Citizens, 1 AI Mafioso, 1 human — insertion-ordered.

    The fan-out order is therefore Ava, Ben, Mara, and the human is skipped, so
    "which writer failed" can be expressed as a position in that list.
    """
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-ben": _player("p-ben", "Ben", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
        "p-human": _player("p-human", "Hugo", "law_abiding", is_human=True),
    }


WRITERS = ["p-ava", "p-ben", "p-mara"]


def _day_close_state(**overrides: Any) -> GameState:
    """A state positioned at the Day→Night hinge, where ``day_diary`` runs.

    ``cycle`` is 3, so the Night the entries precede is 4 — a number distinct
    from the cycle, from the cycle minus one, and from any small constant. An
    off-by-one in ``night_index`` is invisible against ``cycle == 1``.
    """
    state: GameState = {
        "cycle": 3,
        "phase": "day",
        "players": _mixed_table(),
        "day_turn_index": 0,
        "day_rounds": 6,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": {},
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _record(day: int, text: str = SCRIPTED_ENTRY, cursor: int = 0) -> DiaryRecord:
    return {"day": day, "thoughts_before": cursor, "text": text}


class _RecordingDiaryStore:
    """A ``DiaryStore`` that records every call and can be told whom to fail on.

    Failure is keyed on ``player_id``, never on call order, so a test that pins
    "the loop did not stop at the failure" does not silently depend on a fan-out
    order it is not asserting.

    ``read`` is a working read-back over what was successfully stored, so the
    same fake can stand in for ``night_close``'s liveness probe and so a test
    can show the store was genuinely reachable — the pairing that keeps a
    "nothing was written" assertion from being vacuously true of a node that
    never received a store at all.
    """

    class WriteFailure(RuntimeError):
        """Sentinel raised by ``write`` — narrow enough to assert on by type."""

    def __init__(self, *, fail_for: frozenset[str] = frozenset()) -> None:
        self._fail_for = fail_for
        self._entries: dict[tuple[str, str], list[DiaryEntry]] = {}
        self.write_calls: list[dict[str, Any]] = []
        self.read_calls: list[tuple[str, str]] = []
        self.raised = 0

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        self.write_calls.append(
            {
                "game_id": game_id,
                "player_id": player_id,
                "night_index": night_index,
                "content": content,
            }
        )
        if player_id in self._fail_for:
            self.raised += 1
            raise self.WriteFailure(
                f"simulated persistence failure for {player_id} "
                f"before night {night_index}"
            )
        self._entries.setdefault((game_id, player_id), []).append(
            DiaryEntry(night_index=night_index, content=content)
        )

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        self.read_calls.append((game_id, player_id))
        return sorted(
            self._entries.get((game_id, player_id), []), key=lambda e: e.night_index
        )

    # -- test-side conveniences ------------------------------------------

    @property
    def written_player_ids(self) -> list[str]:
        """Ids in the order ``write`` was ATTEMPTED (failures included)."""
        return [call["player_id"] for call in self.write_calls]

    def stored(self, game_id: str, player_id: str) -> list[tuple[int, str]]:
        return [(e.night_index, e.content) for e in self.read(game_id, player_id)]


class _ScriptedDiaryFake:
    """A ``get_large()`` stand-in serving one scripted ``Diary`` per call."""

    def __init__(self, entry: str = SCRIPTED_ENTRY) -> None:
        self._entry = entry
        self.messages_log: list[Any] = []

    def with_structured_output(self, schema: type) -> "_ScriptedDiaryFake":
        assert schema is Diary, f"unexpected schema bound: {schema!r}"
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        return Diary(entry=self._entry)


def _install_diary_fake(
    monkeypatch: pytest.MonkeyPatch, entry: str = SCRIPTED_ENTRY
) -> _ScriptedDiaryFake:
    fake = _ScriptedDiaryFake(entry)
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)
    return fake


def _day_errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """``ERROR`` records emitted by ``graphia.nodes.day`` — the guard's evidence."""
    return [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and r.name == DAY_LOGGER
    ]


def _entries_by_player(delta: dict) -> dict[str, list[str]]:
    return {
        pid: [record["text"] for record in records]
        for pid, records in delta["private_diaries"].items()
    }


# ==========================================================================
# Slice 3.1 — night_index is the Night the entry PRECEDES (day + 1)
# ==========================================================================


@pytest.mark.parametrize(
    ("day", "expected_night_index"),
    [
        (1, 2),   # The earliest hinge there is: the lowest index is 2, never 1.
        (2, 3),
        (3, 4),
        (7, 8),
        (11, 12),  # The last Day of a default-cap game.
    ],
)
def test_persist_diary_writes_the_night_the_entry_precedes(
    day: int, expected_night_index: int
) -> None:
    """``night_index == day + 1``, pinned against absolute literals.

    The expected values are WRITTEN OUT rather than computed as ``day + 1``: a
    test that recomputes the production expression agrees with any expression
    the production code happens to hold, including the two wrong ones this pins
    against — ``night_index = record["day"]`` (numbering by the Day summarised,
    which silently redefines the field and shifts what ``inspect_diary`` prints)
    and ``record["day"] + 2``.

    The ``day == 1`` row carries the extra contract: the lowest ``night_index``
    the store can ever see is **2**. The Day→Night hinge is only reached from
    Day 1 onwards, so there is NO night-1 entry — which is also why
    ``night_close``'s read-back gate (``cycle >= 2``) never misses one.
    """
    store = _RecordingDiaryStore()

    _persist_diary(
        _record(day),
        player_id="p-ava",
        diary_store=store,
        game_id="game-1",
    )

    assert [call["night_index"] for call in store.write_calls] == [
        expected_night_index
    ]


def test_the_fan_out_writes_every_entry_at_one_night_ahead_of_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving the whole node: Day 3's entries all reach the store at night 4.

    The unit test above pins the helper's arithmetic; this pins that the node
    hands it the Day it recorded, for every writer, in one super-step. Passing
    ``cycle`` straight through, or reading the (already bumped) Night from
    somewhere else, breaks the literal 4.
    """
    _install_diary_fake(monkeypatch)
    store = _RecordingDiaryStore()

    day_diary(_day_close_state(), diary_store=store, game_id="game-1")

    assert store.written_player_ids == WRITERS
    assert {call["night_index"] for call in store.write_calls} == {4}


def test_night_index_stays_in_step_with_the_records_own_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across two successive Days, the store's index tracks the record's ``day``.

    One Day in isolation cannot distinguish ``day + 1`` from a constant. Running
    the hinge at cycle 3 and again at cycle 4 pins the RELATION as well as the
    values, so a hard-coded ``night_index=4`` fails on the second Day.
    """
    _install_diary_fake(monkeypatch)
    store = _RecordingDiaryStore()

    first = day_diary(_day_close_state(cycle=3), diary_store=store, game_id="g")
    second = day_diary(_day_close_state(cycle=4), diary_store=store, game_id="g")

    assert {r["day"] for rs in first["private_diaries"].values() for r in rs} == {3}
    assert {r["day"] for rs in second["private_diaries"].values() for r in rs} == {4}
    assert store.stored("g", "p-ava") == [
        (4, SCRIPTED_ENTRY),
        (5, SCRIPTED_ENTRY),
    ]


# ==========================================================================
# Slice 3.2 — the four fields the store is handed
# ==========================================================================


def test_persist_diary_maps_game_id_player_id_and_content_exactly() -> None:
    """All four ``DiaryStore.write`` arguments, pinned against literals.

    ``game_id`` and ``player_id`` are two strings of the same type in adjacent
    parameters — the classic silent swap, and one that survives every
    round-trip test in ``tests/test_diary_store.py`` because a consistently
    wrong pair still reads back consistently. It only shows up as
    per-player-diaries-keyed-by-game in production, or here.
    """
    store = _RecordingDiaryStore()

    _persist_diary(
        _record(6, text="an entry with its own text"),
        player_id="p-mara",
        diary_store=store,
        game_id="game-42",
    )

    assert store.write_calls == [
        {
            "game_id": "game-42",
            "player_id": "p-mara",
            "night_index": 7,
            "content": "an entry with its own text",
        }
    ]


def test_the_store_receives_the_clamped_single_line_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 2's clamp is placed once, and that one placement reaches the store.

    The model is scripted with a deliberately multi-line, over-spaced entry. The
    stored ``content`` must be the folded single-line form — asserted against a
    WRITTEN-OUT LITERAL, not against ``_clamp_diary_entry(MULTILINE_ENTRY)``,
    because a delegation assertion moves with the renderer it delegates to and
    would stay green if the clamp itself were mutated.

    The state record and the stored content must also be byte-equal to each
    other: the whole point of clamping at the single acceptance point is that
    the state channel, the store and the transcript can never disagree.
    Re-normalising (or failing to normalise) at the store boundary breaks that.
    """
    _install_diary_fake(monkeypatch, entry=MULTILINE_ENTRY)
    store = _RecordingDiaryStore()

    delta = day_diary(_day_close_state(), diary_store=store, game_id="g")

    stored = {call["content"] for call in store.write_calls}
    assert stored == {FOLDED_ENTRY}
    assert all("\n" not in content for content in stored)
    # …and it is the same bytes state got, which is the invariant the single
    # clamp placement exists to guarantee.
    assert _entries_by_player(delta) == {pid: [FOLDED_ENTRY] for pid in WRITERS}
    # Non-vacuity: the fake really did serve the awkward text, so the fold is
    # doing work rather than the literal happening to be what the model said.
    assert MULTILINE_ENTRY != FOLDED_ENTRY


def test_an_over_long_entry_reaches_the_store_already_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``DIARY_MAX_CHARS`` cap is applied before the store, not after it."""
    _install_diary_fake(monkeypatch, entry="x" * (DIARY_MAX_CHARS + 500))
    store = _RecordingDiaryStore()

    day_diary(_day_close_state(), diary_store=store, game_id="g")

    assert {len(call["content"]) for call in store.write_calls} == {900}
    assert DIARY_MAX_CHARS == 900, (
        "the literal above is the bare-value pin; if the cap moves deliberately "
        "both numbers change together, and if it moves accidentally this fails"
    )


def test_the_human_seat_never_reaches_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No write is attempted for the person playing, or for a dead player.

    The writer predicate lives in ``day_diary``; this pins its CONSEQUENCE at
    the persistence boundary, which is the one that would put a human's private
    notes into AgentCore Memory. Non-vacuity: two writers do reach the store in
    the same call, so "the human is absent" is not absence-of-everything.
    """
    _install_diary_fake(monkeypatch)
    store = _RecordingDiaryStore()
    players = _mixed_table()
    players["p-ben"] = _player("p-ben", "Ben", "law_abiding", is_alive=False)

    day_diary(_day_close_state(players=players), diary_store=store, game_id="g")

    assert store.written_player_ids == ["p-ava", "p-mara"]


# ==========================================================================
# Slice 3.3 — the injection guard: neither service, no write, no noise
# ==========================================================================


@pytest.mark.parametrize(
    ("with_store", "with_game_id"),
    [
        pytest.param(False, True, id="no-store"),
        pytest.param(True, False, id="no-game-id"),
        pytest.param(False, False, id="neither"),
    ],
)
def test_persist_diary_is_a_no_op_without_both_services(
    with_store: bool,
    with_game_id: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A graph assembled without the injection writes nothing AND logs nothing.

    The "logs nothing" half is what makes this non-vacuous, and it pins the
    early return's ``or``. Flip it to ``and`` and the ``no-store`` case falls
    through to ``None.write(...)``: the ``AttributeError`` is caught by the same
    broad ``except`` that exists for real persistence failures, so a
    write-count assertion alone would still pass while every hinge of every
    unwired game quietly logged an exception traceback.
    """
    caplog.set_level(logging.DEBUG, logger=DAY_LOGGER)
    store = _RecordingDiaryStore()

    _persist_diary(
        _record(3),
        player_id="p-ava",
        diary_store=store if with_store else None,
        game_id="g" if with_game_id else None,
    )

    assert store.write_calls == []
    assert _day_errors(caplog) == []


def test_the_same_call_with_both_services_does_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Positive control for the three no-op cases above.

    Without this, "no write happened" is compatible with a helper that never
    writes at all.
    """
    caplog.set_level(logging.DEBUG, logger=DAY_LOGGER)
    store = _RecordingDiaryStore()

    _persist_diary(_record(3), player_id="p-ava", diary_store=store, game_id="g")

    assert store.written_player_ids == ["p-ava"]
    assert _day_errors(caplog) == []


def test_day_diary_still_returns_its_delta_with_no_store_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state channel does not depend on the store being injected at all.

    Direct-call tests and a graph assembled without the services must keep
    working — the store is the side-channel, state is the source of truth.
    """
    _install_diary_fake(monkeypatch)

    delta = day_diary(_day_close_state())

    assert _entries_by_player(delta) == {pid: [SCRIPTED_ENTRY] for pid in WRITERS}


# ==========================================================================
# Slice 3.4 — THE FAILURE-CONTAINMENT GUARD (the coverage hole)
#
# ``test_night_close_swallows_failing_store_write`` pinned the DELETED loop's
# guard and now pins its retirement instead, so nothing covered the equivalent
# behaviour on ``_persist_diary`` until this section. All three obligations are
# asserted together, per failing position, because they fail independently:
# a guard hoisted around the whole fan-out keeps (1) and loses (2); a bare
# ``except Exception: pass`` keeps (1) and (2) and loses (3); a deleted
# ``try/except`` loses all three.
# ==========================================================================


@pytest.mark.parametrize(
    ("failing", "surviving"),
    [
        pytest.param(["p-ava"], ["p-ben", "p-mara"], id="first-writer-fails"),
        pytest.param(["p-ben"], ["p-ava", "p-mara"], id="middle-writer-fails"),
        pytest.param(["p-mara"], ["p-ava", "p-ben"], id="last-writer-fails"),
        pytest.param(["p-ava", "p-mara"], ["p-ben"], id="first-and-last-fail"),
        pytest.param(WRITERS, [], id="every-writer-fails"),
    ],
)
def test_a_store_failure_keeps_the_delta_whole_and_the_fan_out_going(
    failing: list[str],
    surviving: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing ``DiaryStore.write`` costs nothing but the store row.

    THE TEST THE SLICE OPENED A HOLE FOR. Three obligations, one sweep:

    1. **The state delta is returned, whole.** Every writer — the failing ones
       included — still has its entry in ``private_diaries``, with the scripted
       text rather than ``_DIARY_FALLBACK`` (the model succeeded; only
       persistence failed, and the two failure axes must not be confused).
       Deleting ``_persist_diary``'s ``try/except`` lets the exception escape
       ``day_diary`` entirely and this call raises.
    2. **The remaining players are not skipped.** ``write`` was ATTEMPTED for all
       three writers, in fan-out order, whichever one failed. Hoisting the guard
       out of the helper and around the whole loop — the natural "tidy-up" —
       aborts at the first failure: the ``first-writer-fails`` row then sees one
       attempt instead of three, and the surviving players lose their store rows
       AND (with the guard around the loop) their state entries.
    3. **The failure is logged, not silently swallowed.** One ``ERROR`` per
       failure on ``graphia.nodes.day``, carrying the traceback (it is
       ``logger.exception``, so ``exc_info`` is populated with the sentinel
       type) and naming the player and the Night. ``except Exception: pass``
       leaves 1 and 2 green and loses only this.

    Why the failing player is varied rather than fixed: failing only the LAST
    writer cannot distinguish "the loop continued" from "there was nothing left
    to skip", and failing only the FIRST cannot catch a guard that stops
    tolerating failures once one has succeeded.
    """
    _install_diary_fake(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=DAY_LOGGER)
    store = _RecordingDiaryStore(fail_for=frozenset(failing))

    delta = day_diary(_day_close_state(), diary_store=store, game_id="game-1")

    # (1) The state delta came back whole, with real entries for everyone.
    assert _entries_by_player(delta) == {pid: [SCRIPTED_ENTRY] for pid in WRITERS}
    assert _DIARY_FALLBACK not in [
        text for texts in _entries_by_player(delta).values() for text in texts
    ]

    # (2) Every writer's write was attempted, in order — nobody was skipped.
    assert store.written_player_ids == WRITERS
    # …and the ones that did not fail are genuinely in the store.
    assert sorted(
        pid for pid in WRITERS if store.stored("game-1", pid)
    ) == sorted(surviving)

    # Non-vacuity for (1): the store really did raise, exactly as often as
    # scripted. Without this a passing delta assertion proves nothing.
    assert store.raised == len(failing)

    # (3) One logged exception per failure, naming the player and the Night.
    errors = _day_errors(caplog)
    assert sorted(record.args for record in errors) == sorted(  # type: ignore[type-var]
        (pid, 4) for pid in failing
    )
    for record in errors:
        assert record.exc_info is not None, (
            "logger.exception must attach the traceback — a bare logger.error "
            "would leave an operator with no idea why the write failed"
        )
        assert record.exc_info[0] is _RecordingDiaryStore.WriteFailure
        assert str(record.args[0]) in record.getMessage()  # type: ignore[index]


def test_a_successful_fan_out_logs_no_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Positive control for obligation (3): the ``ERROR`` channel is quiet on success.

    Without this, the sweep above could be reading pre-existing noise on the
    ``graphia.nodes.day`` logger rather than the guard's own report.
    """
    _install_diary_fake(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=DAY_LOGGER)
    store = _RecordingDiaryStore()

    day_diary(_day_close_state(), diary_store=store, game_id="game-1")

    assert store.raised == 0
    assert _day_errors(caplog) == []


def test_persist_diary_swallows_a_failure_and_returns_none() -> None:
    """The helper itself never raises, at its own boundary.

    The node-level sweep proves the fan-out survives; this proves the containment
    is in ``_persist_diary`` rather than somewhere in ``day_diary`` that a future
    caller of the helper would not inherit.
    """
    store = _RecordingDiaryStore(fail_for=frozenset({"p-ava"}))

    assert (
        _persist_diary(
            _record(3), player_id="p-ava", diary_store=store, game_id="g"
        )
        is None
    )
    assert store.raised == 1


def test_a_store_failure_is_not_confused_with_a_model_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The two per-player failure axes are independent and stay distinguishable.

    Ben's MODEL call fails (so his state entry is ``_DIARY_FALLBACK``) while
    Ava's STORE write fails (so her state entry is the real scripted text and
    only her store row is missing). Collapsing the two guards into one — the
    tempting simplification, since both are "per-player, broad, logged" — would
    make a persistence failure blank a player's channel to the fallback, which
    is exactly the outcome the split exists to prevent.
    """

    class _ModelFailsForBen:
        def with_structured_output(self, schema: type) -> "_ModelFailsForBen":
            return self

        def invoke(self, messages: Any) -> Any:
            human = messages[1]
            assert isinstance(human, HumanMessage)
            if "You are Ben —" in human.content:
                raise RuntimeError("model down for this player only")
            return Diary(entry=SCRIPTED_ENTRY)

    monkeypatch.setattr(day_nodes, "get_large", lambda: _ModelFailsForBen())
    caplog.set_level(logging.DEBUG, logger=DAY_LOGGER)
    store = _RecordingDiaryStore(fail_for=frozenset({"p-ava"}))

    delta = day_diary(_day_close_state(), diary_store=store, game_id="g")

    assert _entries_by_player(delta) == {
        "p-ava": [SCRIPTED_ENTRY],
        "p-ben": [_DIARY_FALLBACK],
        "p-mara": [SCRIPTED_ENTRY],
    }
    # Ben's fallback still went to the store; Ava's real entry did not.
    assert store.stored("g", "p-ben") == [(4, _DIARY_FALLBACK)]
    assert store.stored("g", "p-ava") == []
    assert store.stored("g", "p-mara") == [(4, SCRIPTED_ENTRY)]
    # Only the persistence failure is reported on the day logger's ERROR channel.
    assert [record.args for record in _day_errors(caplog)] == [("p-ava", 4)]


# ==========================================================================
# Slice 3.5 — the retired placeholder appears nowhere
# ==========================================================================


def _package_python_files() -> list[Path]:
    root = Path(__file__).resolve().parent.parent / "src" / "graphia"
    files = sorted(root.rglob("*.py"))
    assert len(files) > 20, (
        f"expected to sweep the whole package; found only {len(files)} files "
        f"under {root} — the path is probably wrong and the grep below vacuous"
    )
    return files


def test_the_retired_placeholder_string_is_absent_from_the_package() -> None:
    """A grep for the dead synthetic write comes back empty across ``src/graphia``.

    ``night_close`` wrote ``f"Night {cycle} diary placeholder for {player.id}"``
    from spec 002 until this slice. Deleting the loop is the POINT of Slice 3,
    not a side effect: leaving it would interleave synthetic entries with real
    prose in the same ``(game_id, player_id)`` namespace, and every reader of
    the store — ``inspect_diary``, the read-back, a future Memory consumer —
    would have to tell them apart.

    The fragment asserted on is the interpolation-invariant middle of that
    f-string, so re-introducing the write in any form (a different Night, a
    different id, a different node) is caught. The *word* "placeholder" is
    deliberately NOT the needle: the retirement comment in ``nodes/night.py``
    uses it, and pinning that would make the test fail the moment someone
    tidied the prose.
    """
    offenders = [
        str(path)
        for path in _package_python_files()
        if RETIRED_PLACEHOLDER_FRAGMENT in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], (
        "the retired spec-002 synthetic diary write is back in the package: "
        f"{offenders}"
    )


def test_nothing_placeholder_shaped_reaches_the_store_in_a_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behavioural half: what the store actually receives is the entry.

    The grep above is a source-level assertion and would miss a placeholder
    reconstructed at runtime. This pins the observable: every stored ``content``
    is exactly the text the node accepted, and nothing else was written
    alongside it.
    """
    _install_diary_fake(monkeypatch)
    store = _RecordingDiaryStore()

    day_diary(_day_close_state(), diary_store=store, game_id="g")

    assert [call["content"] for call in store.write_calls] == [SCRIPTED_ENTRY] * 3
    assert all(
        RETIRED_PLACEHOLDER_FRAGMENT not in call["content"]
        for call in store.write_calls
    )


# ==========================================================================
# Slice 3.6 — the flag-off asymmetry, stated honestly
# ==========================================================================


def test_flag_off_writes_no_diary_and_the_placeholder_does_not_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE HONEST ASYMMETRY: with the flag off the store is simply EMPTY.

    ADR 011 says a default-on gameplay flag must have a flag-off parity test.
    On every axis ADR 011 is actually about, spec 039's off arm IS byte-identical
    to the pre-039 world, and that is pinned elsewhere in this spec's tests —
    the prompts (``test_day_turn_flag_off_removes_the_diary_from_the_speech_prompt``
    and its two siblings, plus the flags-off cross-product cells), the public
    message stream (the node returns no ``messages`` key at all), and the eval
    transcript (``test_flag_off_day_renders_byte_identically``).

    **The store is the one axis where it is not**, and the asymmetry is
    deliberate rather than an oversight. Before this slice the off arm's store
    would still have held one trivial synthetic entry per surviving AI per
    Night, written by ``night_close``. That write was DELETED outright rather
    than made conditional on the flag, so the placeholder does not come back to
    fill the gap: an off run's store is empty, where a pre-039 run's was not.
    The retired entry was never gameplay — only a persistence smoke — so nothing
    a player, a prompt or a transcript can observe differs; but a reviewer
    diffing the two arms' AgentCore Memory contents would see it, and would be
    right to. It is stated here so nobody has to discover it.

    Non-vacuity, and it is the point of running both arms against the SAME store
    instance: "no writes happened" is trivially true of a node that never
    received a store. The on arm's three writes prove this store was wired in,
    reachable, and willing.
    """
    _install_diary_fake(monkeypatch)
    store = _RecordingDiaryStore()
    state = _day_close_state()

    off_delta = day_diary(state, private_diaries_enabled=False, diary_store=store, game_id="g")

    # The off arm: no state delta, no store write, and — crucially — no
    # placeholder written in the real entry's place.
    assert off_delta == {}
    assert store.write_calls == []
    assert all(store.stored("g", pid) == [] for pid in WRITERS)

    # The on arm, same store, same state: three writes. The off assertions above
    # are therefore about the flag, not about an unwired store.
    on_delta = day_diary(state, private_diaries_enabled=True, diary_store=store, game_id="g")

    assert set(on_delta["private_diaries"]) == set(WRITERS)
    assert store.written_player_ids == WRITERS


# ==========================================================================
# Slice 3.7 — a driven game: the two channels agree, and the read-back sees it
#
# The trajectory is the Mafia-parity scenario ``tests/test_slice8_endgame.py``
# and this spec's cursor test already use: the human is pinned Mafia via
# ``GRAPHIA_ROLE`` (never a magic seed — ADR 006, architecture §6), AI Mafia kill
# one Law-abiding each Night, the AIs only ever speak so no Day vote opens, and
# each Day runs to the 6-round cap. Exactly one player dies per Night, so the
# game reaches 2-vs-2 parity on Night 3 — which is what makes the absolute entry
# counts below stable without a seed.
# ==========================================================================

HUMAN_NAME = "Alice"
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]


def _pin_default_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every env knob the driven trajectory's absolute numbers rest on."""
    monkeypatch.delenv("GRAPHIA_NUM_MAFIA", raising=False)
    monkeypatch.delenv("GRAPHIA_NUM_CITIZENS", raising=False)
    monkeypatch.delenv("GRAPHIA_MAX_DAYS", raising=False)
    monkeypatch.setenv("GRAPHIA_PRIVATE_THOUGHTS", "1")
    monkeypatch.setenv("GRAPHIA_PRIVATE_DIARIES", "1")
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    for task in graph.get_state(run_config).tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _drive(graph, run_config, payload) -> None:
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 300)
    for _ in graph.stream(payload, bounded, stream_mode="updates"):
        pass


def _advance_until(
    graph,
    run_config,
    *,
    stop: Callable[[], bool],
    responder: Callable[[dict[str, Any]], Any],
    budget: int = 300,
) -> None:
    for _ in range(budget):
        if stop():
            return
        if not graph.get_state(run_config).next:
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            _drive(graph, run_config, None)
            continue
        _drive(graph, run_config, Command(resume=responder(interrupt_value)))


def _alive_ai_ids(graph, run_config, role: str) -> list[str]:
    state = graph.get_state(run_config).values
    return [
        p.id
        for p in state.get("players", {}).values()
        if p.is_alive and p.role == role and not p.is_human
    ]


def test_driven_game_store_and_state_hold_exactly_the_same_entries(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The dual write, end to end, against a real ``InProcessDiaryStore``.

    Six facts are pinned here, all of them absolute rather than derived from the
    game that just ran (a count re-derived from the same state it is checking
    agrees with any state):

    1. **Nine entries, across nights {2, 3}.** Seven seats, human pinned Mafia,
       so one AI Mafioso and five AI Law-abiding. One kill per Night, three
       Nights to 2-vs-2 parity: five AI survive to write Day 1's entries (stored
       at night 2) and four survive to write Day 2's (night 3). There is no
       night-1 entry — Night 1 is reached from ``first_night_mafia_intros``, so
       the hinge never runs before it.
    2. **The human seat holds nothing.** ``is_alive and not is_human`` at the
       persistence boundary, over a whole real game.
    3. **``state(day + 1) == store(night_index)``, exactly**, compared as a set of
       ``(name, night_index, content)``. This is the dual write's actual
       contract, and it is the assertion that fails for an off-by-one in
       ``night_index``, a dropped player, a duplicated write, or a store row
       whose text drifted from the state record's.
    4. **A player who dies mid-game stops writing.** Exactly one AI holds a
       night-2 entry and no night-3 one — the Night-2 victim — and exactly one
       holds nothing at all, the Night-1 victim. Both are dead at the end. A
       writer predicate that forgot ``is_alive`` would give all six AI both
       entries.
    5. **The clamp's single placement reaches the store.** The scripted entry is
       multi-line and over-spaced; every stored ``content`` comes back as the
       single-line literal.
    6. **The read-back path still runs, and now reads real prose.** ``night_close``
       keeps its loop as the liveness probe of the Gateway-fronted read path —
       remotely the whole Gateway → Lambda → Memory round-trip. On Night 2 each
       of the four surviving AI reads back exactly the one entry ``day_diary``
       wrote for it at the Day-1 hinge. That closes the write→read circuit
       through one store, which no unit test in this file does.
    """
    _pin_default_table(monkeypatch)
    fake_small(AI_NAMES)
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        reflections=[Reflection(thought="A passing thought.")],
        diaries=[Diary(entry=MULTILINE_ENTRY)],
    )

    store = InProcessDiaryStore()
    graph, thread_id = build_graph(load_config(), diary_store=store)
    run_config = make_run_config(thread_id)

    caplog.set_level(logging.INFO, logger=NIGHT_LOGGER)

    _drive(graph, run_config, {"messages": []})
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            law = _alive_ai_ids(graph, run_config, "law_abiding")
            return Pointing(target_id=law[0] if law else "missing")
        if schema is DayAction:
            return DayAction(kind="speak", text="Nothing suspicious here.")
        if schema is Ballot:
            return Ballot(yes=False)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    def _respond(interrupt_value: dict[str, Any]) -> Any:
        kind = interrupt_value.get("kind")
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "no"
        if kind == "point":
            options = interrupt_value.get("options") or []
            return options[0]["id"] if options else ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    _advance_until(
        graph,
        run_config,
        stop=lambda: graph.get_state(run_config).values.get("winner") is not None,
        responder=_respond,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") == "mafia", (
        "the scripted trajectory did not reach the expected Mafia win; every "
        "absolute count below would be measuring a different game"
    )
    assert state.get("cycle") == 3
    # The scripted queue really served the diary call site — not the swallowed-
    # failure world, where the counts below would be counting fallbacks.
    assert fake.calls_by_schema[Diary] == 9

    players = state["players"]

    # (1) + (2) — the shape of what the store holds.
    stored = {
        pid: store.read(thread_id, pid) for pid in players
    }
    total = sum(len(entries) for entries in stored.values())
    assert total == 9, (
        "expected 9 store entries (five Day-1 writers at night 2, four Day-2 "
        f"writers at night 3); got {total} — {[(pid, len(e)) for pid, e in stored.items()]}"
    )
    assert {
        entry.night_index for entries in stored.values() for entry in entries
    } == {2, 3}, "entries must occupy nights 2..N; there is no night-1 entry"
    human_id = next(pid for pid, p in players.items() if p.is_human)
    assert stored[human_id] == [], "the human seat must never reach the store"

    # (3) — the two channels agree, exactly.
    from_store = {
        (players[pid].name, entry.night_index, entry.content)
        for pid, entries in stored.items()
        for entry in entries
    }
    from_state = {
        (players[pid].name, record["day"] + 1, record["text"])
        for pid, records in (state.get("private_diaries") or {}).items()
        for record in records
    }
    assert from_store == from_state, (
        "the dual write's two destinations disagree: "
        f"only in store {sorted(from_store - from_state)!r}; "
        f"only in state {sorted(from_state - from_store)!r}"
    )
    assert len(from_state) == 9

    # (4) — death stops the writing.
    nights_by_player = {
        pid: sorted(entry.night_index for entry in entries)
        for pid, entries in stored.items()
        if not players[pid].is_human
    }
    silent = [pid for pid, nights in nights_by_player.items() if nights == []]
    only_night_two = [pid for pid, nights in nights_by_player.items() if nights == [2]]
    both = [pid for pid, nights in nights_by_player.items() if nights == [2, 3]]
    assert len(silent) == 1, (
        f"exactly one AI (Night 1's victim) should have written nothing: {nights_by_player!r}"
    )
    assert len(only_night_two) == 1, (
        "exactly one AI (Night 2's victim) should hold a night-2 entry and no "
        f"night-3 one: {nights_by_player!r}"
    )
    assert len(both) == 4, nights_by_player
    assert not players[silent[0]].is_alive
    assert not players[only_night_two[0]].is_alive

    # (5) — the clamp reached the store; the literal is written out, not
    # recomputed from the clamp under test.
    contents = {
        entry.content for entries in stored.values() for entry in entries
    }
    assert contents == {FOLDED_ENTRY}
    assert all("\n" not in content for content in contents)

    # (6) — the surviving read-back saw exactly what the hinge had written.
    read_records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.name == NIGHT_LOGGER
        and r.getMessage().startswith("Read ")
    ]
    # ``night_close`` runs on Night 2 only: Night 1 is below the ``cycle >= 2``
    # gate, and Night 3's kill wins the game at ``check_win_night``, which routes
    # to ``end_screen`` without reaching ``night_close``.
    assert {r.args[2] for r in read_records} == {2}, (  # type: ignore[index]
        f"read-back fired on unexpected nights: {[r.getMessage() for r in read_records]}"
    )
    assert len(read_records) == 4, (
        "four AI survive into Night 2's close, and each must be read back once: "
        f"{[r.getMessage() for r in read_records]}"
    )
    assert {r.args[0] for r in read_records} == {1}, (  # type: ignore[index]
        "each survivor had written exactly one entry (Day 1's) by Night 2, so "
        "the read-back must report 1 — a 0 here means the write and the read "
        "are not talking to the same store: "
        f"{[r.getMessage() for r in read_records]}"
    )
    assert human_id not in {r.args[1] for r in read_records}  # type: ignore[index]
