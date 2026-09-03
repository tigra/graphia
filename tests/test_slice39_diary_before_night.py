"""Spec 039 (Per-AI Private Diaries) — Slice 1 behavioural tests.

The production change adds a dedicated ``day_diary`` node on the Day→Night
hinge (``day_close -> day_diary -> night_open``). Every surviving AI player
writes ONE diary entry summing up the Day just ended; each entry lands in the
new ``GameState.private_diaries`` channel as a :class:`DiaryRecord`
(``day`` / ``thoughts_before`` / ``text``) accumulated by
``_merge_private_diaries``; the eval transcript renders it as
``<diary player="…" day="…">`` in a new day-level trailer. The whole feature is
gated by the default-on ablation flag ``GRAPHIA_PRIVATE_DIARIES`` (ADR 011).

Spec 028's ``tests/test_slice28_private_thoughts.py`` is this file's structural
twin and its section layout is followed deliberately. As there, every assertion
is STRUCTURAL (architecture §6): an entry was produced, it carries the right
cursor, it never leaks, the node is a no-op when guarded. Nothing here asserts
diary *quality* — that is eval-measured out of the suite (effort-not-results,
CR 005).

What this file does NOT cover, on purpose
-----------------------------------------

``tests/test_slice39_diary_fake_coverage.py`` owns the ``Diary``-queue
prerequisite: that the fake serves the schema at all, and that a flag-on full
game records the SCRIPTED entry rather than ``_DIARY_FALLBACK`` (measured with
``calls_by_schema[Diary]``, because "an entry exists" is true of the fallback
world too). That file is not duplicated here; the driven games below script
``diaries=`` and assert on the scripted sentinel for the same reason.

Slice 2 superseded exactly one test here: ``_clamp_diary_entry`` now folds
internal whitespace so an accepted entry is always single-line, and
``test_clamp_preserves_internal_newlines_today`` — which pinned the previous
non-normalising behaviour on purpose — was rewritten in place as
:func:`test_clamp_folds_internal_whitespace_into_a_single_line`.

Anti-vacuity notes are attached to the assertions that need them. The two
recurring hazards in this repo are (a) a test that reads the same constant the
production code reads, which goes vacuous the moment that constant is mutated —
so the bounds are pinned to absolute literals as well as to relations; and
(b) a test that asserts "entries were written" while silently measuring the
deterministic fallback — so every writer test distinguishes the scripted text
from ``_DIARY_FALLBACK`` by value.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Diary, Pointing, Reflection
from graphia.nodes.day import (
    DIARY_MAX_CHARS,
    PRIVATE_THOUGHTS_LABEL,
    _clamp_diary_entry,
    _DIARY_FALLBACK,
    day_diary,
)
from graphia.nodes.endgame import route_after_win_day
from graphia.nodes.night import night_open
from graphia.prompts import (
    DIARY_SENTENCE_BOUND,
    DIARY_SYSTEM,
    REFLECTION_SYSTEM,
)
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.state import GameState, PlayerState, _merge_private_diaries
from graphia.tools.eval_transcript import render_transcript

# The standings label ``_standings_prompt_block`` emits when spec 019's
# recap-aware-reasoning flag is on. Read as a literal (not imported) so this
# file pins the rendered prompt rather than agreeing with the renderer.
STANDINGS_LABEL = "Current standings (act on these):"

# A sentinel that no production string could produce, so an exact match proves
# the model's text reached state untouched — and, crucially, that what reached
# state is NOT ``_DIARY_FALLBACK``.
SCRIPTED_ENTRY = "SCRIPTED-DIARY-039: the miller would not meet my eye today."


# ==========================================================================
# Shared hand-built helpers (mirroring tests/test_slice28_private_thoughts.py)
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
    """2 AI Citizens, 1 AI Mafioso, 1 human — distinct ids and names.

    Insertion-ordered, so the fan-out order is Ava, Ben, Mara, (human) and a
    per-player assertion can be indexed against ``messages_log``.
    """
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-ben": _player("p-ben", "Ben", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
        "p-human": _player("p-human", "Hugo", "law_abiding", is_human=True),
    }


def _day_close_state(**overrides: Any) -> GameState:
    """A state positioned at the Day→Night hinge, where ``day_diary`` runs.

    ``cycle`` is deliberately 3 (not 1) so ``DiaryRecord["day"]`` can be pinned
    to an absolute literal: an off-by-one such as ``"day": cycle + 1`` is
    invisible against a state whose cycle is 1 and whose expectation is
    "whatever the cycle is".
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


class _CapturingDiaryFake:
    """A content-recording ``get_large()`` stand-in that counts every reach.

    Three counters, because the guards in ``day_diary`` must fire BEFORE any
    model work — an empty delta alone does not prove that:

    - ``bindings``  — ``get_large()`` was called and a schema bound.
    - ``messages_log`` — ``invoke`` was reached (one entry per prompt).
    - ``schemas``   — which schema classes were bound.

    Mirrors ``tests/test_slice28_private_thoughts.py:_CapturingFake``, plus the
    binding counter.
    """

    def __init__(self, entry: str = SCRIPTED_ENTRY) -> None:
        self._entry = entry
        self.messages_log: list[Any] = []
        self.bindings = 0
        self.schemas: list[type] = []

    def with_structured_output(
        self, schema: type, **kwargs: object
    ) -> "_CapturingDiaryFake":
        self.bindings += 1
        self.schemas.append(schema)
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        return Diary(entry=self._entry)


class _PerPlayerFailingFake:
    """Raises for the prompt naming ``fail_for``; serves a real entry otherwise.

    The failure is keyed on the rendered prompt text rather than on call order,
    so the test does not silently depend on the fan-out order it is not
    asserting.
    """

    def __init__(self, fail_for: str) -> None:
        self._fail_for = fail_for
        self.messages_log: list[Any] = []

    def with_structured_output(
        self, schema: type, **kwargs: object
    ) -> "_PerPlayerFailingFake":
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        if f"You are {self._fail_for} —" in _human_prompt(messages):
            raise RuntimeError("model down for this player only")
        return Diary(entry=SCRIPTED_ENTRY)


def _human_prompt(messages: Any) -> str:
    """The rendered ``HumanMessage`` text from a captured ``[System, Human]``."""
    human = messages[1]
    assert isinstance(human, HumanMessage)
    return human.content


def _entries_by_player(delta: dict) -> dict[str, list[str]]:
    """``{player id: [entry text, …]}`` from a ``day_diary`` delta."""
    return {
        pid: [record["text"] for record in records]
        for pid, records in delta["private_diaries"].items()
    }


# ==========================================================================
# Slice 1.1 — the accumulating reducer (_merge_private_diaries)
# ==========================================================================


def _record(day: int, cursor: int, text: str) -> dict:
    return {"day": day, "thoughts_before": cursor, "text": text}


def test_reducer_accumulates_per_player_in_write_order() -> None:
    """Two successive Days' deltas concatenate for the same player, in order.

    A plain ``dict`` merge (``{**prior, **incoming}``) would clobber Day 1's
    entry with Day 2's — the exact bug this reducer exists to prevent, and the
    one-line change that breaks this assertion.
    """
    day1 = _merge_private_diaries({}, {"p-ava": [_record(1, 5, "d1")]})
    day2 = _merge_private_diaries(day1, {"p-ava": [_record(2, 10, "d2")]})

    assert day2 == {
        "p-ava": [_record(1, 5, "d1"), _record(2, 10, "d2")]
    }
    # Order is the write order, not sorted, not reversed.
    assert [r["day"] for r in day2["p-ava"]] == [1, 2]


def test_reducer_adds_new_key_without_disturbing_others() -> None:
    """A delta for a newly-writing player leaves the existing keys intact."""
    prior = {"p-ava": [_record(1, 5, "a1"), _record(2, 10, "a2")]}
    out = _merge_private_diaries(prior, {"p-ben": [_record(2, 10, "b1")]})

    assert out == {
        "p-ava": [_record(1, 5, "a1"), _record(2, 10, "a2")],
        "p-ben": [_record(2, 10, "b1")],
    }


def test_reducer_is_pure_inputs_not_mutated_or_aliased() -> None:
    """Copy-not-mutate: neither input changes, and no output list is an alias.

    ``p-ben`` is present in ``prior`` and ABSENT from ``incoming`` on purpose.
    That is the case a ``dict(prior or {})`` shallow copy — a plausible
    "simplification" of the comprehension — would leave aliased: only the keys
    the incoming delta touches get rebuilt, so an untouched key's list would
    still be the caller's object and a later in-place append would corrupt the
    checkpointed prior map.
    """
    prior = {"p-ava": [_record(1, 5, "a1")], "p-ben": [_record(1, 5, "b1")]}
    incoming = {"p-ava": [_record(2, 10, "a2")], "p-mara": [_record(2, 10, "m1")]}

    out = _merge_private_diaries(prior, incoming)

    # Inputs unchanged.
    assert prior == {"p-ava": [_record(1, 5, "a1")], "p-ben": [_record(1, 5, "b1")]}
    assert incoming == {
        "p-ava": [_record(2, 10, "a2")],
        "p-mara": [_record(2, 10, "m1")],
    }
    # Every output list is a fresh object — including the untouched key.
    assert out["p-ava"] is not prior["p-ava"]
    assert out["p-ava"] is not incoming["p-ava"]
    assert out["p-ben"] is not prior["p-ben"]
    assert out["p-mara"] is not incoming["p-mara"]
    # And the output map is not either input map.
    assert out is not prior
    assert out is not incoming


@pytest.mark.parametrize(
    ("prior", "incoming", "expected"),
    [
        (None, {"p-ava": [_record(1, 0, "x")]}, {"p-ava": [_record(1, 0, "x")]}),
        ({"p-ava": [_record(1, 0, "x")]}, None, {"p-ava": [_record(1, 0, "x")]}),
        (None, None, {}),
        ({}, {}, {}),
    ],
)
def test_reducer_tolerates_none_and_empty_operands(
    prior: dict | None, incoming: dict | None, expected: dict
) -> None:
    """``None`` on either side is the empty map — initial-state safe."""
    assert _merge_private_diaries(prior, incoming) == expected


# ==========================================================================
# Slice 1.2 — the clamp: the ENFORCEMENT half of the length bound
# ==========================================================================


def test_diary_max_chars_is_the_documented_cap() -> None:
    """``DIARY_MAX_CHARS`` is 900 — pinned as an ABSOLUTE literal.

    Deliberately not ``DIARY_MAX_CHARS > 0`` or a comparison against itself:
    every other clamp assertion in this file necessarily reads the constant,
    so a mutation of the constant would slide past all of them together. This
    is the single test that notices the constant itself moving.
    """
    assert DIARY_MAX_CHARS == 900


def test_clamp_truncates_an_over_long_entry_to_the_cap() -> None:
    """A runaway entry is cut to exactly the cap — the functional spec's third
    length criterion, which a prompt sentence cannot satisfy.

    Absolute literals on both sides (2000 in, 900 out) so the assertion does
    not move with the constant.
    """
    runaway = "z" * 2000
    clamped = _clamp_diary_entry(runaway)

    assert len(clamped) == 900
    assert clamped == "z" * 900


def test_clamp_leaves_a_compliant_entry_byte_identical() -> None:
    """An entry within the cap passes through unchanged (no reflow, no ellipsis)."""
    entry = "The baker contradicted himself twice. I will watch him tomorrow."
    assert _clamp_diary_entry(entry) == entry


def test_clamp_strips_surrounding_whitespace() -> None:
    """Leading/trailing whitespace is stripped before the cap is applied."""
    assert _clamp_diary_entry("  \n  A quiet day.  \t ") == "A quiet day."


def test_clamp_right_strips_a_cut_that_lands_on_whitespace() -> None:
    """Truncation never leaves a trailing blank — the cut is right-stripped.

    The input is built so character 900 falls inside a run of spaces: a plain
    ``[:DIARY_MAX_CHARS]`` with no ``rstrip`` would return an 900-char string
    ending in a space. Pinned to the absolute 899.
    """
    text = "a" * 899 + "    " + "b" * 200
    clamped = _clamp_diary_entry(text)

    assert clamped == "a" * 899
    assert len(clamped) == 899
    assert not clamped.endswith(" ")


def test_clamp_folds_internal_whitespace_into_a_single_line() -> None:
    """The clamp normalises internal whitespace: an accepted entry is single-line.

    THE REWRITE of ``test_clamp_preserves_internal_newlines_today``, which
    pinned the pre-fold-in behaviour deliberately so this change would be a
    visible one. Slice 2's first task folded the normalisation in, so the
    assertion flips from "preserved" to "folded" and the coverage stays.

    Why the clamp normalises at all: ``eval_transcript._inline_attr`` promises a
    SINGLE-LINE element, and ``DIARY_SENTENCE_BOUND`` (6) invites six sentences
    where spec 028 invited one or two — so a model putting a blank line between
    two thoughts would produce the transcript format's first multi-line inline
    element. The fold lives at the clamp rather than in the renderer because the
    clamp is the one point where an entry is accepted, so it covers the state
    channel, the store write and the transcript at once.

    The exact-text assertion is what makes this non-vacuous: ``"\\n" not in``
    alone would pass for a clamp that simply DELETED the newlines and ran the
    surrounding words together.
    """
    multiline = "Two things today.\n\nFirst, the miller lied.\nSecond, I said nothing."
    clamped = _clamp_diary_entry(multiline)

    assert "\n" not in clamped
    assert clamped == (
        "Two things today. First, the miller lied. Second, I said nothing."
    )
    # Tabs, carriage returns and runs of spaces fold to one space as well — the
    # normalisation is over whitespace generally, not newlines specifically.
    assert _clamp_diary_entry("a\tb\r\nc   d") == "a b c d"


def test_clamp_is_pure_and_repeatable() -> None:
    """No state, no RNG, no clock: the same input clamps to the same output."""
    text = "q" * 1500
    assert _clamp_diary_entry(text) == _clamp_diary_entry(text)


def test_diary_fallback_is_within_the_cap() -> None:
    """``_DIARY_FALLBACK`` needs no clamping — the clamp is for model text only."""
    assert len(_DIARY_FALLBACK) <= DIARY_MAX_CHARS
    assert _clamp_diary_entry(_DIARY_FALLBACK) == _DIARY_FALLBACK


# ==========================================================================
# Slice 1.3 — the node's self-guards: NO write AND no model call
# ==========================================================================


def test_flag_off_writes_nothing_and_calls_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 011 flag-off parity: the node is a no-op with the feature ablated.

    The delta assertion catches a deleted guard; the counters catch a guard
    moved BELOW the fan-out (which would spend the tokens and then throw the
    result away — the cost the guard exists to avoid).
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    assert day_diary(_day_close_state(), private_diaries_enabled=False) == {}
    assert fake.bindings == 0
    assert fake.messages_log == []


def test_winner_already_set_writes_nothing_and_calls_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished game has nothing to sum up — and spends no tokens saying so."""
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    assert day_diary(_day_close_state(winner="mafia")) == {}
    assert fake.bindings == 0
    assert fake.messages_log == []


@pytest.mark.parametrize(
    ("cycle", "max_days", "writes"),
    [
        (1, 4, True),   # Night 2 is coming — well below the cap.
        (2, 4, True),   # Night 3 is coming — the last Night that happens.
        (3, 4, False),  # Night 4 would hit the cap: no entry.
        (4, 4, False),  # Past the cap.
        (10, 12, True),  # Default cap, Night 11 coming.
        (11, 12, False),  # Default cap, Night 12 would hit it.
    ],
)
def test_runaway_cap_guard_skips_the_final_day_of_a_capped_game(
    cycle: int,
    max_days: int,
    writes: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec-023 runaway cap suppresses the fan-out BEFORE any model call.

    ``day_diary`` runs one super-step before ``night_open`` detects the cap, so
    without this guard the final Day of a capped game would fire a full fan-out
    of model calls for a Night that never happens.

    The sweep spans both sides of the boundary at two different ``max_days``, so
    an off-by-one in either direction is caught: ``cycle >= max_days`` writes at
    (11, 12) where it must not, and ``cycle + 2 >= max_days`` skips at (10, 12)
    where it must write.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(_day_close_state(cycle=cycle), max_days=max_days)

    if writes:
        assert delta["private_diaries"], "expected a fan-out below the cap"
        assert fake.messages_log, "expected one model call per surviving AI"
    else:
        assert delta == {}
        assert fake.bindings == 0
        assert fake.messages_log == []


@pytest.mark.parametrize(
    ("cycle", "max_days"),
    [(1, 4), (2, 4), (3, 4), (4, 4), (5, 6), (10, 12), (11, 12), (12, 12)],
)
def test_runaway_guard_agrees_with_night_open_exactly(
    cycle: int, max_days: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard skips a Day iff ``night_open`` would call the Night a runaway.

    The named anti-drift risk in tech-spec §2.1: binding ``max_days`` from the
    same ``_assemble_graph`` value removes CONSTANT drift, but the PREDICATE is
    duplicated — ``day_diary`` hard-codes the knowledge that ``night_open``
    bumps the cycle because the prior phase is ``"day"``. This test compares the
    two implementations against each other instead of restating either, so a
    change to ``night_open``'s bump condition that leaves ``day_diary`` behind
    fails here rather than silently costing a fan-out (or silently skipping one)
    in a live game.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    night = night_open(
        {"cycle": cycle, "phase": "day", "players": {}}, max_days=max_days
    )
    night_is_runaway = night.get("winner") == "runaway"

    delta = day_diary(_day_close_state(cycle=cycle), max_days=max_days)
    diary_skipped = delta == {}

    assert diary_skipped == night_is_runaway, (
        f"cycle={cycle} max_days={max_days}: night_open "
        f"{'DID' if night_is_runaway else 'did NOT'} declare a runaway, but "
        f"day_diary {'DID' if diary_skipped else 'did NOT'} skip. The two "
        "predicates have drifted."
    )


@pytest.mark.parametrize(
    ("label", "players"),
    [
        ("empty roster", {}),
        (
            "only the human left",
            {"p-human": _player("p-human", "Hugo", "law_abiding", is_human=True)},
        ),
        (
            "every AI dead",
            {
                "p-ava": _player("p-ava", "Ava", "law_abiding", is_alive=False),
                "p-mara": _player("p-mara", "Mara", "mafia", is_alive=False),
                "p-human": _player(
                    "p-human", "Hugo", "law_abiding", is_human=True
                ),
            },
        ),
    ],
)
def test_no_surviving_writer_writes_nothing_and_calls_no_model(
    label: str,
    players: dict[str, PlayerState],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no surviving non-human player the node is a no-op (``{}``, no calls)."""
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    assert day_diary(_day_close_state(players=players)) == {}, label
    assert fake.bindings == 0, label
    assert fake.messages_log == [], label


# ==========================================================================
# Slice 1.4 — the writers: one entry per surviving AI, and nobody else
# ==========================================================================


def test_one_entry_per_surviving_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly one entry each for the three AI players; none for the human.

    The entry TEXT is asserted against the scripted sentinel, not merely
    "non-empty": ``_DIARY_FALLBACK`` is non-empty too, so a "wrote something"
    assertion would pass in a world where every model call is failing and being
    swallowed.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(_day_close_state())
    entries = _entries_by_player(delta)

    assert set(entries) == {"p-ava", "p-ben", "p-mara"}
    assert entries == {
        "p-ava": [SCRIPTED_ENTRY],
        "p-ben": [SCRIPTED_ENTRY],
        "p-mara": [SCRIPTED_ENTRY],
    }
    assert _DIARY_FALLBACK not in [t for texts in entries.values() for t in texts]
    # One model call per writer, no more.
    assert len(fake.messages_log) == 3
    # And it is the diary schema that was bound, not a neighbour's.
    assert fake.schemas == [Diary, Diary, Diary]


def test_human_seat_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The person playing writes no diary — and no prompt is built for them."""
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(_day_close_state())

    assert "p-human" not in delta["private_diaries"]
    for messages in fake.messages_log:
        assert "You are Hugo —" not in _human_prompt(messages)


def test_eval_scripted_stand_in_never_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec-026 measured-game stand-in writes nothing — no special case.

    The scripted stand-in occupies the HUMAN seat (``is_human=True``); it is a
    pure, deterministic, no-LLM policy that makes no such decisions. So the
    ``is_alive and not is_human`` writer predicate excludes it for free, and
    there is no stand-in-specific branch to keep in step. This test states that
    explicitly so a future "let the stand-in keep a diary too" change is a
    deliberate one: the table below is a measured game's shape — the seat the
    harness drives is the only human, and every other seat is an AI.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    stand_in_table = {
        "p-standin": _player(
            "p-standin", "Alice", "law_abiding", is_human=True
        ),
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
    }
    delta = day_diary(_day_close_state(players=stand_in_table))

    assert set(delta["private_diaries"]) == {"p-ava", "p-mara"}
    assert "p-standin" not in delta["private_diaries"]


def test_dead_players_never_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """A killed or executed AI writes no entry for the Day it did not survive."""
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    players = dict(_mixed_table())
    players["p-ben"] = _player(
        "p-ben", "Ben", "law_abiding", is_alive=False
    )
    delta = day_diary(_day_close_state(players=players))

    assert set(delta["private_diaries"]) == {"p-ava", "p-mara"}
    # And no prompt was built for the dead player either.
    assert len(fake.messages_log) == 2
    for messages in fake.messages_log:
        assert "You are Ben —" not in _human_prompt(messages)


def test_record_carries_exactly_day_cursor_and_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``DiaryRecord`` holds ``day`` / ``thoughts_before`` / ``text``, no more.

    ``day`` is pinned to the literal 3 — the state's cycle BEFORE
    ``night_open`` bumps it. ``"day": cycle + 1`` (the store's ``night_index``
    arithmetic, which Slice 3 applies at the store boundary and NOT here) would
    read as 4 and is caught.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(_day_close_state(cycle=3))
    record = delta["private_diaries"]["p-ava"][0]

    assert set(record) == {"day", "thoughts_before", "text"}
    assert record["day"] == 3
    assert record["text"] == SCRIPTED_ENTRY
    assert isinstance(record["thoughts_before"], int)


# ==========================================================================
# Slice 1.5 — privacy: the highest-stakes invariant
# ==========================================================================


def test_delta_contains_no_messages_key_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The diary delta is ``{"private_diaries": …}`` and nothing else.

    Privacy here is STRUCTURAL rather than filtered: an entry never enters the
    message stream, so it can never reach the UI, the public log or another
    player's rendered context. Adding a single ``"messages": [...]`` key to the
    return — the one-line change a future "announce that everyone wrote in
    their diary" ask would make — breaks this.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(_day_close_state())

    assert set(delta.keys()) == {"private_diaries"}
    assert "messages" not in delta


def test_no_entry_carries_a_private_to_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No part of the delta carries the ``private_to`` whisper route.

    ``private_to`` is the convention the UI uses to decide what the human may
    see. A diary must never be routed at all — not even to its own author — so
    the marker must be absent from the whole delta, not merely filtered by the
    UI.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_diary(_day_close_state())

    assert "private_to" not in repr(delta)
    assert "additional_kwargs" not in repr(delta)


def test_prompt_carries_only_the_writers_own_private_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ava's diary prompt holds Ava's notes and nobody else's."""
    state = _day_close_state(
        private_thoughts={
            "p-ava": ["AVA-SECRET-1", "AVA-SECRET-2"],
            "p-ben": ["BEN-SECRET-1"],
            "p-mara": ["MARA-SECRET-1"],
        }
    )
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_diary(state)

    ava_prompt = _human_prompt(fake.messages_log[0])
    assert "AVA-SECRET-1" in ava_prompt
    assert "AVA-SECRET-2" in ava_prompt
    assert "BEN-SECRET-1" not in ava_prompt
    assert "MARA-SECRET-1" not in ava_prompt

    ben_prompt = _human_prompt(fake.messages_log[1])
    assert "BEN-SECRET-1" in ben_prompt
    assert "AVA-SECRET-1" not in ben_prompt
    assert "MARA-SECRET-1" not in ben_prompt


# ==========================================================================
# Slice 1.6 — THE CROSS-CHANNEL CURSOR (the novel design in this spec)
# ==========================================================================
#
# ``DiaryRecord.thoughts_before`` is the length of the writer's OWN
# ``private_thoughts`` list as observed at write time. Slice 2's merge walks the
# diary list in order and emits every not-yet-emitted thought at index
# < thoughts_before ahead of its entry. Captured at the wrong moment — or read
# from the wrong list — a player's own history renders in the wrong order in its
# own prompt, which is invisible without a direct assertion on the number.


def test_cursor_is_each_writers_own_thought_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each entry records THAT writer's own thought count — three distinct values.

    Deliberately three DIFFERENT counts (3 / 1 / 0, one of them a player absent
    from the channel entirely). The single most likely slip is reading the wrong
    thing and getting a plausible number anyway:

    - ``len(all_thoughts)`` — the number of PLAYERS with notes (3 here) — would
      give every writer 3 and is caught by Ben's 1 and Mara's 0.
    - ``state.get("private_diaries")`` instead of ``private_thoughts`` (a
      copy-paste slip between two adjacent channel names) gives 0 everywhere
      and is caught by Ava's 3.
    - A shared cursor computed once before the loop is caught by any two writers
      differing.
    """
    state = _day_close_state(
        private_thoughts={
            "p-ava": ["a1", "a2", "a3"],
            "p-ben": ["b1"],
            # p-mara has never reflected: absent from the channel, cursor 0.
        }
    )
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    diaries = day_diary(state)["private_diaries"]

    assert diaries["p-ava"][0]["thoughts_before"] == 3
    assert diaries["p-ben"][0]["thoughts_before"] == 1
    assert diaries["p-mara"][0]["thoughts_before"] == 0


def test_cursor_matches_the_notes_actually_shown_in_that_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The number recorded equals the number of notes the writer was just shown.

    ``day_diary`` reads the writer's thought list ONCE and passes the same list
    both to the prompt builder and to the cursor, so the two can never
    disagree. This test is what makes that a contract rather than a comment: it
    counts the ``- `` bullets in the rendered block and compares them with the
    recorded cursor, per writer.
    """
    state = _day_close_state(
        private_thoughts={
            "p-ava": ["a1", "a2", "a3"],
            "p-ben": ["b1"],
        }
    )
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    diaries = day_diary(state)["private_diaries"]

    for index, pid in enumerate(("p-ava", "p-ben", "p-mara")):
        prompt = _human_prompt(fake.messages_log[index])
        cursor = diaries[pid][0]["thoughts_before"]
        if cursor == 0:
            assert PRIVATE_THOUGHTS_LABEL not in prompt, pid
        else:
            block = prompt.split(PRIVATE_THOUGHTS_LABEL, 1)[1]
            bullets = [
                line
                for line in block.splitlines()
                if line.startswith("- ")
            ]
            assert len(bullets) == cursor, (
                f"{pid}: recorded thoughts_before={cursor} but the prompt "
                f"showed {len(bullets)} note(s): {bullets!r}"
            )


def test_cursor_is_non_decreasing_across_successive_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As thoughts accumulate, successive Days record larger cursors.

    Monotonicity is what makes Slice 2's merge a stable two-way merge rather
    than a sort. Simulated across two Days by feeding the node the state each
    Day would actually see.
    """
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day1 = day_diary(
        _day_close_state(cycle=1, private_thoughts={"p-ava": ["t1", "t2"]})
    )
    day2 = day_diary(
        _day_close_state(
            cycle=2, private_thoughts={"p-ava": ["t1", "t2", "t3", "t4", "t5"]}
        )
    )

    assert day1["private_diaries"]["p-ava"][0] == {
        "day": 1,
        "thoughts_before": 2,
        "text": SCRIPTED_ENTRY,
    }
    assert day2["private_diaries"]["p-ava"][0] == {
        "day": 2,
        "thoughts_before": 5,
        "text": SCRIPTED_ENTRY,
    }


def test_cursor_is_recorded_even_when_the_thoughts_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thoughts OFF hides the block from the prompt but still records the cursor.

    Two separate things, and the test pins both because they look like one: the
    ``private_thoughts_enabled`` flag governs what the writer is SHOWN (spec
    028's ablation seam, threaded properly here rather than hard-coded), while
    the cursor is a property of committed state and stays truthful either way.
    Hard-coding ``enabled=True`` in the diary's block call — the latent
    ``_ai_reflect`` defect this node deliberately does not inherit — is what
    breaks the first assertion.
    """
    state = _day_close_state(private_thoughts={"p-ava": ["a1", "a2"]})
    fake = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    diaries = day_diary(state, private_thoughts_enabled=False)["private_diaries"]

    ava_prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL not in ava_prompt
    assert "a1" not in ava_prompt
    assert diaries["p-ava"][0]["thoughts_before"] == 2


def test_recap_aware_flag_is_threaded_into_the_diary_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 019's standings ablation reaches the diary prompt (not hard-coded).

    The 028 defect this node was told not to inherit: ``_ai_reflect`` calls
    ``_standings_prompt_block(state, enabled=True)`` with the flag hard-coded,
    so spec 019's ablation is incomplete on that path. Here the flag must
    actually govern the slot.
    """
    fake_on = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake_on)
    day_diary(_day_close_state(), recap_aware_reasoning_enabled=True)
    assert STANDINGS_LABEL in _human_prompt(fake_on.messages_log[0])

    fake_off = _CapturingDiaryFake()
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake_off)
    day_diary(_day_close_state(), recap_aware_reasoning_enabled=False)
    assert STANDINGS_LABEL not in _human_prompt(fake_off.messages_log[0])


# ==========================================================================
# Slice 1.7 — per-player failure isolation
# ==========================================================================


def test_one_players_model_failure_does_not_skip_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure for Ben yields Ben the fallback and leaves Ava and Mara real.

    The ``try/except`` lives INSIDE the per-player helper. Hoisting it around
    the whole fan-out — the natural "tidy-up" — would abort the loop at Ben and
    lose Mara's entry entirely; asserting all three keys AND the exact texts is
    what distinguishes the two worlds. Note that Ben's entry is the FALLBACK,
    not a blank: one model hiccup must never leave a player's channel empty.
    """
    fake = _PerPlayerFailingFake(fail_for="Ben")
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    entries = _entries_by_player(day_diary(_day_close_state()))

    assert entries == {
        "p-ava": [SCRIPTED_ENTRY],
        "p-ben": [_DIARY_FALLBACK],
        "p-mara": [SCRIPTED_ENTRY],
    }
    # All three prompts were built — the loop did not abort at the failure.
    assert len(fake.messages_log) == 3


@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
def test_an_empty_model_entry_becomes_the_fallback(
    empty: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank ``Diary.entry`` is refused in favour of the deterministic note."""
    fake = _CapturingDiaryFake(entry=empty)
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    entries = _entries_by_player(day_diary(_day_close_state()))

    assert entries["p-ava"] == [_DIARY_FALLBACK]
    assert entries["p-mara"] == [_DIARY_FALLBACK]


def test_safe_llm_nets_the_diary_call_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """With NO fake installed, the node completes on the ``safe_llm`` net.

    ``day_diary`` lives in ``nodes/day.py`` precisely so the autouse
    ``safe_llm`` patch of ``graphia.nodes.day.get_large`` already covers it — no
    new patch target, and therefore no chance of the forgotten-stub failure that
    falls through to real boto3 and hangs pytest teardown on retry loops. The
    loud-failure LLM raises, the node's guard converts that into
    ``_DIARY_FALLBACK``, and every surviving AI still has an entry.

    Deliberately no ``monkeypatch.setattr(day_nodes, "get_large", …)`` here —
    the autouse binding is the thing under test.
    """
    entries = _entries_by_player(day_diary(_day_close_state()))

    assert set(entries) == {"p-ava", "p-ben", "p-mara"}
    for pid, texts in entries.items():
        assert texts == [_DIARY_FALLBACK], pid


# ==========================================================================
# Slice 1.8 — the stated bound is larger than spec 028's (no 028 edits)
# ==========================================================================


def test_diary_sentence_bound_is_six() -> None:
    """``DIARY_SENTENCE_BOUND`` is 6 — pinned as an ABSOLUTE literal.

    The relation test below necessarily reads the constant, so it would slide
    past a mutation of the constant itself. This is the test that notices.
    """
    assert DIARY_SENTENCE_BOUND == 6


def test_diary_bound_exceeds_028s_unchanged_prose_bound() -> None:
    """The diary's stated bound is the LARGER of the two — checked, not edited.

    The functional spec's "larger of the two" criterion is satisfiable without
    touching spec 028: ``REFLECTION_SYSTEM`` still states "one or two
    sentences" (so its bound is 2), and ``DIARY_SENTENCE_BOUND`` is greater.
    Rewording 028's prompt to state a number instead would break the first
    assertion — which is the point: this test is also the guard that 028's
    prompt bytes were left alone.
    """
    assert "one or two sentences" in REFLECTION_SYSTEM
    assert DIARY_SENTENCE_BOUND > 2
    # And the diary does not silently inherit 028's prose bound.
    assert "one or two sentences" not in DIARY_SYSTEM


def test_the_stated_bound_is_the_constant_interpolated() -> None:
    """``DIARY_SYSTEM`` states the bound by interpolation — no prose/constant drift.

    Hard-coding a number in the prompt text (``"at most 3 sentences"``) while
    leaving the constant at 6 is exactly the drift the interpolation exists to
    make impossible, and it is what this assertion catches.
    """
    assert f"at most {DIARY_SENTENCE_BOUND} sentences" in DIARY_SYSTEM
    # The number is written down once: the user template refers back to it
    # rather than repeating it.
    assert DIARY_SYSTEM.count(f"{DIARY_SENTENCE_BOUND} sentences") == 1


# ==========================================================================
# Slice 1.9 — eval transcript: the day-level trailer
# ==========================================================================


def _transcript_players() -> dict[str, PlayerState]:
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
    }


def _two_round_day(diary_delta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A synthetic two-round Day, optionally closed by a ``day_diary`` delta.

    The Day ends on a ROUND WRAP (the final ``day_turn`` carries
    ``day_rounds``), so ``pending_round_break`` is still set when ``day_close``
    and ``day_diary`` arrive. That is the state in which a renderer that
    consumed the break on any delta rather than only on a ``day_turn`` would
    open a spurious third round — see
    :func:`test_a_diary_delta_does_not_disturb_the_round_bookkeeping`.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 2 breaks.")]}},
        {
            "day_turn": {
                "messages": [AIMessage(content="I suspect the miller.", name="Ava")]
            }
        },
        # A genuine round wrap: ``day_turn`` returns ``day_rounds``.
        {
            "day_turn": {
                "messages": [AIMessage(content="Not me.", name="Mara")],
                "day_rounds": 1,
            }
        },
        # The next ``day_turn`` consumes the break and opens round 2.
        {
            "day_turn": {
                "messages": [AIMessage(content="Second round talk.", name="Ava")]
            }
        },
        # Round 2 wraps too — the Day ends with a break still pending.
        {
            "day_turn": {
                "messages": [AIMessage(content="Nothing more.", name="Mara")],
                "day_rounds": 2,
            }
        },
        {"day_close": {"messages": [SystemMessage(content="The Day ends.")]}},
    ]
    if diary_delta is not None:
        events.append({"day_diary": diary_delta})
    return events


def test_transcript_renders_a_diary_in_the_day_trailer() -> None:
    """A ``private_diaries`` delta renders ``<diary player=… day=…>`` per entry.

    The placement assertion is the load-bearing one: the element must sit AFTER
    the last ``</round>`` and BEFORE ``</day>``. Appending to
    ``current_day_body()`` instead of the trailer — a one-line "simplification"
    the renderer's own docstring warns about — files the diary inside the last
    ``<round>``, which still renders and still contains the text, so only the
    ordering assertion notices.
    """
    out = render_transcript(
        _two_round_day(
            {
                "private_diaries": {
                    "p-ava": [{"day": 2, "thoughts_before": 5, "text": "AVA_DIARY"}],
                    "p-mara": [
                        {"day": 2, "thoughts_before": 5, "text": "MARA_DIARY"}
                    ],
                }
            }
        ),
        _transcript_players(),
        game_index=1,
        run_meta=None,
    )

    assert '<diary player="Ava" day="2">AVA_DIARY</diary>' in out
    assert '<diary player="Mara" day="2">MARA_DIARY</diary>' in out

    # Between the last </round> and </day> — the trailer, not a round body.
    last_round_close = out.rindex("</round>")
    day_close = out.rindex("</day>")
    trailer = out[last_round_close + len("</round>") : day_close]
    assert "AVA_DIARY" in trailer, (
        "the diary must render in the day-level trailer, between the last "
        f"</round> and </day>; the trailer held:\n{trailer!r}"
    )
    assert "MARA_DIARY" in trailer

    # Still inside the day, and never as a public utterance.
    assert out.index("Day 2 breaks.") < out.index("AVA_DIARY")
    assert "Ava: AVA_DIARY" not in out


def test_a_diary_delta_does_not_disturb_the_round_bookkeeping() -> None:
    """The trailer takes no part in the round structure: still exactly 2 rounds.

    ``pending_round_break`` is set and consumed only by ``day_turn`` deltas, and
    the trailer is a list of its own. A diary arriving after the final wrap must
    not open a spurious third round.
    """
    with_diary = render_transcript(
        _two_round_day(
            {
                "private_diaries": {
                    "p-ava": [{"day": 2, "thoughts_before": 5, "text": "AVA_DIARY"}]
                }
            }
        ),
        _transcript_players(),
        game_index=1,
        run_meta=None,
    )

    assert with_diary.count("Round 1.") == 1
    assert with_diary.count("Round 2.") == 1
    assert "Round 3." not in with_diary


def test_flag_off_day_renders_byte_identically() -> None:
    """A diaries-off Day is byte-identical to a pre-039 capture.

    Flag-off makes ``day_diary`` return ``{}``, so the two comparisons that
    matter are: an empty delta from the node, and no such delta at all (which
    is what every pre-039 capture looks like). Both must render exactly the
    same bytes as the Day without the feature.
    """
    baseline = render_transcript(
        _two_round_day(None), _transcript_players(), game_index=1, run_meta=None
    )
    flag_off = render_transcript(
        _two_round_day({}), _transcript_players(), game_index=1, run_meta=None
    )
    empty_channel = render_transcript(
        _two_round_day({"private_diaries": {}}),
        _transcript_players(),
        game_index=1,
        run_meta=None,
    )

    assert flag_off == baseline
    assert empty_channel == baseline
    assert "<diary" not in baseline


@pytest.mark.parametrize(
    ("label", "channel", "expect_present", "expect_absent"),
    [
        ("missing entirely", None, None, "<diary"),
        ("wrong type", "not-a-dict", None, "<diary"),
        ("empty channel", {}, None, "<diary"),
        ("empty per-player list", {"p-ava": []}, None, "<diary"),
        (
            "per-player value not a list",
            {"p-ava": "not-a-list"},
            None,
            "<diary",
        ),
        (
            "records are not dicts",
            {"p-ava": [None, 123, "bare string"]},
            None,
            "<diary",
        ),
        (
            "record has no text",
            {"p-ava": [{"day": 2, "thoughts_before": 1}]},
            None,
            "<diary",
        ),
        (
            "text is not a string",
            {"p-ava": [{"day": 2, "text": 42}]},
            None,
            "<diary",
        ),
        (
            "text is blank",
            {"p-ava": [{"day": 2, "text": "   \n "}]},
            None,
            "<diary",
        ),
        (
            "unknown player id resolves to itself",
            {"p-ghost": [{"day": 2, "text": "ORPHAN"}]},
            '<diary player="p-ghost" day="2">ORPHAN</diary>',
            None,
        ),
        (
            "missing day omits the attribute, keeps the entry",
            {"p-ava": [{"text": "NO_DAY"}]},
            '<diary player="Ava">NO_DAY</diary>',
            None,
        ),
        (
            "bool day omits the attribute",
            {"p-ava": [{"day": True, "text": "BOOL_DAY"}]},
            '<diary player="Ava">BOOL_DAY</diary>',
            'day="True"',
        ),
        (
            "float day omits the attribute",
            {"p-ava": [{"day": 2.5, "text": "FLOAT_DAY"}]},
            '<diary player="Ava">FLOAT_DAY</diary>',
            'day="2.5"',
        ),
        (
            "None day omits the attribute",
            {"p-ava": [{"day": None, "text": "NONE_DAY"}]},
            '<diary player="Ava">NONE_DAY</diary>',
            'day="None"',
        ),
        (
            "a good record beside a bad one still renders",
            {
                "p-ava": [
                    {"day": 2, "text": None},
                    {"day": 2, "text": "SURVIVOR"},
                ]
            },
            '<diary player="Ava" day="2">SURVIVOR</diary>',
            None,
        ),
    ],
)
def test_transcript_diary_rendering_is_defensive(
    label: str,
    channel: Any,
    expect_present: str | None,
    expect_absent: str | None,
) -> None:
    """Malformed diary input never raises — and never invents an attribute.

    Every row asserts something beyond "did not raise": either the exact
    element that must appear, or the fragment that must not. A pure
    smoke-test-shaped version of this sweep would pass against a renderer that
    silently dropped every well-formed entry too.

    ``day`` is the one field where "defensive" carries a design choice: a
    missing or nonsensical ``day`` still leaves an entry worth showing, so the
    ATTRIBUTE is omitted and the element is still rendered — the same reasoning
    the ledger uses for rendering an absent arm blank rather than ``false``.
    """
    delta: dict[str, Any] = {} if channel is None else {"private_diaries": channel}
    out = render_transcript(
        _two_round_day(delta), _transcript_players(), game_index=1, run_meta=None
    )

    assert "<transcript>" in out, label
    if expect_present is not None:
        assert expect_present in out, label
    if expect_absent is not None:
        assert expect_absent not in out, label


# ==========================================================================
# Slice 1.10 — topology: the edge rewire, and why Night 1 has no entry
# ==========================================================================


def _graph_edges() -> set[tuple[str, str]]:
    """The compiled graph's edge set as ``{(source, target)}``."""
    graph, _thread_id = build_graph(load_config())
    drawn = graph.get_graph()
    return {(edge.source, edge.target) for edge in drawn.edges}


def test_hinge_is_rewired_through_day_diary(env: Path) -> None:
    """``day_close -> day_diary -> night_open`` replaced ``day_close -> night_open``.

    Both halves matter. Adding the node while leaving the old direct edge in
    place would run the Night twice; adding the edges without the node would
    fail to compile. Asserting the OLD edge is gone is what catches the first.
    """
    edges = _graph_edges()

    assert ("day_close", "day_diary") in edges
    assert ("day_diary", "night_open") in edges
    assert ("day_close", "night_open") not in edges


def test_day_diary_sits_on_exactly_one_path(env: Path) -> None:
    """``day_diary`` has one predecessor (``day_close``) and one successor.

    This is the structural reason NIGHT 1 HAS NO ENTRY: the only way into the
    node is from ``day_close``, and Night 1 is entered from
    ``first_night_mafia_intros`` instead. A second inbound edge — from
    ``night_open``'s head, say, which tech-spec §2.1 rules out on three
    independent grounds — would show up here.
    """
    edges = _graph_edges()

    assert {s for s, t in edges if t == "day_diary"} == {"day_close"}
    assert {t for s, t in edges if s == "day_diary"} == {"night_open"}
    # Night 1's entry path is unchanged and does not pass through the node.
    assert ("first_night_mafia_intros", "night_open") in edges


def test_winning_day_bypasses_day_close_and_therefore_the_diary() -> None:
    """``check_win_day`` routes a winner to ``end_screen``, skipping ``day_close``.

    The functional spec's boundary condition "no diary entries were written for
    the Day on which the game is won" is a TOPOLOGY property, not a guard: the
    node is never reached. Pinned at the router, and again end-to-end below in
    :func:`test_driven_day_win_writes_no_diary_for_the_winning_day`.
    """
    winning = {
        "winner": "law_abiding",
        "cycle": 2,
        "kill_log": [{"cycle": 2, "cause": "execution", "name": "Mara"}],
        "day_rounds": 1,
    }
    assert route_after_win_day(winning) == "end_screen"

    # Same state without a winner: the execution ends the Day the normal way,
    # through ``day_close`` — so the routing difference really is the winner.
    ongoing = dict(winning)
    del ongoing["winner"]
    assert route_after_win_day(ongoing) == "day_close"


# ==========================================================================
# Slice 1.11 — flag plumbing (ADR 011 anti-drift)
# ==========================================================================


def test_load_config_private_diaries_default_on_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ⇒ diaries on (the documented default)."""
    monkeypatch.delenv("GRAPHIA_PRIVATE_DIARIES", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().private_diaries_enabled is True


@pytest.mark.parametrize("falsy", ["0", "false", "FALSE", "no", "off", "Off"])
def test_load_config_private_diaries_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit falsy value disables the flag — the ablation arm's entry point."""
    monkeypatch.setenv("GRAPHIA_PRIVATE_DIARIES", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().private_diaries_enabled is False


def test_build_runtime_graph_signature_carries_the_diaries_flag() -> None:
    """``build_runtime_graph`` exposes ``private_diaries_enabled``, defaulted True.

    The named anti-drift requirement (ADR 011): both graph builders must thread
    the flag or local and remote diverge.
    """
    sig = inspect.signature(build_runtime_graph)
    assert "private_diaries_enabled" in sig.parameters
    assert sig.parameters["private_diaries_enabled"].default is True


# ==========================================================================
# Slice 1.12 — driven games: the cursor and the boundaries, end to end
# ==========================================================================
#
# Two full driven games, each ~0.2s with the fake LLM. They exist because the
# properties below are emergent — they depend on the graph's real edge set, the
# real round cap and the real win routing, none of which a hand-built state
# exercises.

HUMAN_NAME = "Alice"
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]


def _pin_default_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the five-and-two table the two driven trajectories' numbers rest on.

    These tests' subject is the CROSS-CHANNEL CURSOR arithmetic — how many of a
    writer's own committed private thoughts precede each diary entry — and the
    pins are absolute (5 / 10 / 0), as is the number of Days each trajectory
    takes. Both are only hand-checkable against a KNOWN table, so this helper
    *sets* the lineup it claims to pin: five Law-abiding and two Mafia, seven
    seats, which is the six ``AI_NAMES`` above plus the human.

    Setting rather than clearing is the load-bearing part. An earlier version
    called ``delenv`` on the two count knobs, which pins to whatever the
    default happens to be — so when the default lineup moved (spec 042) the
    Mafia needed one extra Day to reach parity and the Day pins below broke,
    even though the per-Day cursor arithmetic these tests exist to protect was
    untouched. The seven-seat table is this scenario's own choice, not the
    product's default; deriving the expected Day counts from the resolved
    config instead would only make the expectation restate the implementation.

    What each knob does here:

    * ``GRAPHIA_NUM_CITIZENS`` / ``GRAPHIA_NUM_MAFIA`` — the seven-seat table.
      With the human pinned Mafia and exactly one kill per Night, the game
      reaches 2-vs-2 parity on Night 3, so the capped trajectory closes exactly
      two Days (hence ``{1: {5}, 2: {10}}`` and ``cycle == 3``).
    * ``GRAPHIA_MAX_DAYS`` — cleared rather than set: both games end long
      before any Day cap binds, so nothing below depends on its value; clearing
      it only keeps a developer's stray shell override out of the way.
    * ``GRAPHIA_PRIVATE_THOUGHTS`` — spec 028's reflections are what the cursor
      counts. With them off every cursor would be 0 and the 5 / 10 pin would be
      measuring nothing.

    The Day's own 6-round cap is ``graphia.nodes.day.DAY_MAX_ROUNDS``, a code
    constant rather than an env knob, so five of the six rounds wrap into a
    reflection whatever the environment says.
    """
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "5")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "2")
    monkeypatch.delenv("GRAPHIA_MAX_DAYS", raising=False)
    monkeypatch.setenv("GRAPHIA_PRIVATE_THOUGHTS", "1")


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    for task in graph.get_state(run_config).tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _drive(graph, run_config, payload, sink: list[dict[str, Any]]) -> None:
    """Stream one leg of the game, appending every ``{node: delta}`` to ``sink``."""
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 300)
    for event in graph.stream(payload, bounded, stream_mode="updates"):
        sink.append(event)


def _advance_until(
    graph,
    run_config,
    *,
    stop: Callable[[], bool],
    responder: Callable[[dict[str, Any]], Any],
    sink: list[dict[str, Any]],
    budget: int = 300,
) -> None:
    for _ in range(budget):
        if stop():
            return
        if not graph.get_state(run_config).next:
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            _drive(graph, run_config, None, sink)
            continue
        _drive(graph, run_config, Command(resume=responder(interrupt_value)), sink)


def _alive_ai_ids(graph, run_config, role: str) -> list[str]:
    state = graph.get_state(run_config).values
    return [
        p.id
        for p in state.get("players", {}).values()
        if p.is_alive and p.role == role and not p.is_human
    ]


def _node_sequence(events: list[dict[str, Any]], *names: str) -> list[str]:
    return [node for event in events for node in event if node in names]


def test_driven_game_pins_the_cross_channel_cursor(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Day-1 entry records cursor 5 and every Day-2 entry records 10.

    THE CURSOR TEST. The trajectory is the Mafia-parity scenario from
    ``tests/test_slice8_endgame.py``: the human is pinned Mafia via
    ``GRAPHIA_ROLE`` (never a magic seed — ADR 006), AI Mafia kill one
    Law-abiding each Night, the AIs only ever speak so no Day vote opens, and
    each Day therefore runs to the 6-round cap.

    Why 5 and 10 are the right absolute numbers, and why they are worth
    pinning: ``route_day_turn_or_vote`` sends a completed round to
    ``day_round_reflect`` only while the round cap is unmet, so round 6 routes
    straight to ``day_close``. Five of the six rounds therefore wrap into a
    reflection, and every writer sees exactly 5 committed thoughts when the
    Day-1 diary is written and exactly 10 when the Day-2 one is.

    That is precisely the property Slice 2's merge relies on, and it is
    invisible without this assertion: capture the cursor one super-step early
    (before the last round's reflection commits) and it reads 4 and 9; capture
    it from the wrong channel and it reads 0; capture it after
    ``night_open``'s bump and ``day`` shifts too. Each renders a player's own
    thoughts and diaries in the WRONG ORDER inside its OWN prompt — a defect
    that produces perfectly well-formed output and would never surface as a
    crash.
    """
    _pin_default_table(monkeypatch)
    monkeypatch.setenv("GRAPHIA_PRIVATE_DIARIES", "1")
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_small(AI_NAMES)
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        reflections=[Reflection(thought="A passing thought.")],
        diaries=[Diary(entry=SCRIPTED_ENTRY)],
    )

    graph, thread_id = build_graph(load_config())
    run_config = make_run_config(thread_id)
    events: list[dict[str, Any]] = []

    _drive(graph, run_config, {"messages": []}, events)
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
        sink=events,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") == "mafia", (
        "the scripted trajectory did not reach the expected Mafia win; the "
        "cursor assertions below would be measuring a different game"
    )

    diaries = state.get("private_diaries") or {}
    assert diaries, "a flag-on driven game wrote no diaries at all"

    records = [record for entries in diaries.values() for record in entries]
    # The scripted queue really served these — not the swallowed-failure world.
    assert fake.calls_by_schema[Diary] >= 1
    assert {record["text"] for record in records} == {SCRIPTED_ENTRY}

    # THE PIN: one cursor value per Day, absolute.
    by_day: dict[int, set[int]] = {}
    for record in records:
        by_day.setdefault(record["day"], set()).add(record["thoughts_before"])

    assert by_day == {1: {5}, 2: {10}}, (
        "expected every Day-1 entry to record thoughts_before=5 and every "
        f"Day-2 entry 10 (the 6-round cap's five completed-round wraps per "
        f"Day); got {by_day!r}"
    )

    # NONE BEFORE NIGHT 1: three Nights happened, two Days closed into one.
    assert state.get("cycle") == 3
    assert sorted(by_day) == [1, 2]

    # Every writer of each Day was alive and not the human.
    for pid in diaries:
        assert not state["players"][pid].is_human

    # END-TO-END PRIVACY. The unit test above asserts the node's delta carries
    # no ``messages`` key; this asserts the consequence over a whole real game:
    # the entry text is nowhere in the accumulated message stream, which is the
    # only channel that reaches the UI, the public log, and the rendered
    # context every other player is given. Adding a single ``"messages": [...]``
    # key to the node's return breaks this.
    for message in state.get("messages", []):
        assert SCRIPTED_ENTRY not in str(getattr(message, "content", "")), (
            "a diary entry reached the public message stream"
        )

    # Hinge order in the real stream: each diary super-step sits strictly
    # between a ``day_close`` and the next ``night_open``.
    hinge = _node_sequence(
        events, "night_open", "day_open", "day_close", "day_diary", "end_screen"
    )
    assert hinge[0] == "night_open", (
        f"the first phase super-step must be Night 1's night_open, with no "
        f"diary before it; sequence was {hinge!r}"
    )
    assert "day_diary" not in hinge[: hinge.index("day_open")], (
        f"a diary was written before the first Day even began: {hinge!r}"
    )
    for index, node in enumerate(hinge):
        if node != "day_diary":
            continue
        assert hinge[index - 1] == "day_close", (
            f"day_diary at position {index} was not preceded by day_close: "
            f"{hinge!r}"
        )
        assert hinge[index + 1] == "night_open", (
            f"day_diary at position {index} was not followed by night_open: "
            f"{hinge!r}"
        )

    # And the preserved record places each entry in its own Day's trailer.
    transcript = render_transcript(
        events, state["players"], game_index=1, run_meta=None
    )
    first_night = transcript.split("<day>", 1)[0]
    assert "<diary" not in first_night, (
        "Night 1 has no preceding Day to sum up, so no diary may appear "
        f"before the first <day>:\n{first_night}"
    )
    assert transcript.count("<diary ") == len(records)


def test_driven_day_win_writes_no_diary_for_the_winning_day(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Day the game is won produces no entries — end to end.

    The Law-abiding-win trajectory from ``tests/test_slice8_endgame.py``: the
    AIs vote out a Mafioso each Day and every ballot is Yes, so Day 2's
    execution empties the Mafia and ``check_win_day`` routes to ``end_screen``
    without ever reaching ``day_close``.

    The complementary cursor case to the test above, and worth having for its
    own sake: Day 1 here closes on an EXECUTION rather than the round cap, so
    no round ever wrapped and every Day-1 entry records ``thoughts_before=0``.
    A cursor computed from anything other than the writer's committed thought
    list — a round counter, the Day number, a constant — would read non-zero
    here while still reading 5/10 in the capped game.
    """
    _pin_default_table(monkeypatch)
    monkeypatch.setenv("GRAPHIA_PRIVATE_DIARIES", "1")
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_small(AI_NAMES)
    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        reflections=[Reflection(thought="A passing thought.")],
        diaries=[Diary(entry=SCRIPTED_ENTRY)],
    )

    graph, thread_id = build_graph(load_config())
    run_config = make_run_config(thread_id)
    events: list[dict[str, Any]] = []

    _drive(graph, run_config, {"messages": []}, events)
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            law = _alive_ai_ids(graph, run_config, "law_abiding")
            return Pointing(target_id=law[0] if law else "missing")
        if schema is DayAction:
            mafia = _alive_ai_ids(graph, run_config, "mafia")
            if mafia:
                return DayAction(kind="vote", target_id=mafia[0])
            return DayAction(kind="speak", text="(nothing to add.)")
        if schema is Ballot:
            return Ballot(yes=True)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    def _respond(interrupt_value: dict[str, Any]) -> Any:
        kind = interrupt_value.get("kind")
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "yes"
        if kind == "point":
            options = interrupt_value.get("options") or []
            return options[0]["id"] if options else ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    _advance_until(
        graph,
        run_config,
        stop=lambda: graph.get_state(run_config).values.get("winner")
        == "law_abiding",
        responder=_respond,
        sink=events,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") == "law_abiding", (
        "the scripted trajectory did not reach the expected Day win; the "
        "assertions below would be measuring a different game"
    )
    assert state.get("cycle") == 2, (
        f"expected the win on Day 2; cycle was {state.get('cycle')!r}"
    )

    records = [
        record
        for entries in (state.get("private_diaries") or {}).values()
        for record in entries
    ]
    assert records, "Day 1 closed into Night 2, so it must have written entries"

    days = sorted({record["day"] for record in records})
    assert days == [1], (
        "only Day 1 closed into a Night; Day 2 is the winning Day and "
        f"bypasses day_close entirely, so it must write nothing. Got {days!r}"
    )
    assert {record["thoughts_before"] for record in records} == {0}, (
        "Day 1 ended on an execution before any speaking round wrapped, so no "
        "reflection had been written and every cursor must be 0; got "
        f"{sorted({r['thoughts_before'] for r in records})!r}"
    )

    # The node ran exactly once — after Day 1's close, never after Day 2's win.
    hinge = _node_sequence(
        events, "day_open", "day_close", "day_diary", "check_win_day", "end_screen"
    )
    assert hinge.count("day_diary") == 1, hinge
    assert hinge[-1] == "end_screen", hinge
    assert "day_diary" not in hinge[hinge.index("end_screen") - 1 :], (
        f"a diary was written on the winning Day: {hinge!r}"
    )

    # And the preserved record shows the last <day> carrying no diary.
    transcript = render_transcript(
        events, state["players"], game_index=1, run_meta=None
    )
    last_day = transcript.rsplit("<day>", 1)[1]
    assert "<diary" not in last_day, (
        f"the winning Day's section must hold no diary:\n{last_day}"
    )
