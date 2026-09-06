"""Spec 041 Slice 6, Task 6.3 — every substitution is counted where it happens.

``src/graphia/substitution.py`` (Task 6.1) declares seven answer kinds and two
notifications; Task 6.2 wired them into the seven substituting helpers across
``nodes/setup.py``, ``nodes/night.py`` and ``nodes/day.py``. This module is the
offline proof that the wiring is complete, exact, and closed.

The heart is a **seven-way sweep run twice**, and the pairing is the whole
design:

- :func:`test_a_forced_failure_books_exactly_one_substitution_and_the_fallback`
  forces a failure at each call site and asserts BOTH that exactly one
  substitution is booked AND that the helper returned its deterministic
  stand-in.
- :func:`test_a_scripted_success_books_no_substitution_and_returns_the_answer`
  is its anti-vacuity twin: a scripted success books NO substitution and
  returns the scripted value.

Either assertion alone is vacuous, in Task 6.3's own words. The count alone
could be double-booked somewhere and still read as one here; the value alone is
exactly what already shipped before any of this existed — the fallbacks are
years old and testing them proves nothing about counting. And the failure half
alone would pass a helper that booked a substitution unconditionally, which is
what the success half exists to catch. Only all four assertions together say a
substitution happened, was counted once, and is not counted when it did not
happen.

The traps this module was warned about, and what it does with each
--------------------------------------------------------------------

``POINTING``'s fallback is ``random.choice(alive_law_abiding).id``, so "the
known fallback value" is not knowable without pinning the RNG. Pinned per the
``_shuffle_order`` convention — ``monkeypatch.setattr(night_nodes.random,
"choice", …)`` — to the LAST candidate, while the scripted-success case answers
with the FIRST, so the two halves cannot be confused with one another. On the
success half ``random.choice`` is pinned to EXPLODE instead, which is the
``tests/test_slice30_night_roster.py`` convention and turns "no substitution was
booked" into "the substituting branch was never even entered".

``ROSTER`` cannot be forced with a generic exception: ``_generate_names``
catches only :class:`~pydantic.ValidationError`, so a ``RuntimeError`` from a
fake propagates out of the helper — booking an attempt, no substitution, and no
return value at all. That behaviour is pinned deliberately in
:func:`test_a_roster_transport_failure_propagates_and_books_no_substitution`,
and the sweep forces ``ROSTER`` with a real ``ValidationError`` harvested from
``Roster``'s own distinctness validator instead.

``ROSTER``'s known fallback is ``Player-1 … Player-N`` only when NOTHING usable
came back. A parsed-but-short reply yields a mixed roster, pinned separately in
:func:`test_a_short_roster_reply_substitutes_into_a_mixed_roster` so the sweep's
clean all-placeholder expectation stays honest about being one of two shapes.

``caplog`` must be filtered by logger name: every substitution now emits a
``graphia.substitution`` WARNING through ``default_sink``, and ``_ai_diary``
emits its own ``graphia.nodes.day`` records beside it, so an unfiltered
``caplog.records`` assertion mixes the two. Both log tests filter on
``record.name``.

``PERSONA``'s denominator is generations, not players. The sweep therefore
drives the HELPER ``_generate_one_persona``, where one call is one answer.
:func:`test_persona_books_one_event_per_generation_not_per_player` drives the
NODE with diversity on and asserts on generations (three per seat), and its
flag-off twin asserts one per seat — so the reading Slice 7 has to label is
pinned here rather than left to be rediscovered from the ledger.

What is deliberately NOT here
-----------------------------

Task 6.4's negative controls — the schema-valid/model-invalid ``DayAction`` and
its ``Roster`` twin, both of which are unconstructible as instances and must
arrive as raw payload through a parsing fake. That is the next task.

No assertion is made about the INNER keys of a ``DiaryRecord``. Slice 8 adds
``substituted`` to that record on purpose, and pre-breaking it here would
misread Task 6.3's requirement, which is about the DELTA carrying no
substitution channel — Slice 6 changes no state shape.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Callable

import pytest
from pydantic import ValidationError

import graphia.nodes.day as day_nodes
import graphia.nodes.night as night_nodes
import graphia.nodes.setup as setup_nodes
from graphia.llm import (
    GAMEPLAY_SCHEMAS,
    Ballot,
    DayAction,
    Diary,
    Persona,
    Pointing,
    Reflection,
    Roster,
)
from graphia.nodes.day import _DIARY_FALLBACK, _REFLECTION_FALLBACK
from graphia.state import GameState, PlayerPersona, PlayerState
from graphia.substitution import (
    SubstitutionEvent,
    SubstitutionKind,
    note_attempt,
    note_substitution,
    reset_sink,
    set_sink,
)

# The one production fallback with no named constant behind it: ``_ai_day_action``
# returns this literal inline. Written out here (rather than imported, because
# there is nothing to import) so a silent edit to the production line shows up
# as a diff of two readable sentences.
_DAY_ACTION_FALLBACK_TEXT = "I'm not sure who to trust yet."

# ``_fallback_persona``'s four name-anchored sentences for the player named
# "Ava", spelled out rather than obtained by calling ``_fallback_persona``
# itself: an assertion that reads the same function the code under test reads
# goes vacuous the moment that function changes, which is the hazard this
# repo's test docstrings call out repeatedly.
_AVA_FALLBACK_PERSONA = PlayerPersona(
    personality="Ava is an ordinary, even-tempered townsperson.",
    manner="Ava speaks plainly and to the point.",
    public_persona="Ava is a familiar face around town, trusted by neighbours.",
    true_self="",
)

_ROSTER_COUNT = 3


# ==========================================================================
# The sink-reset fixture, and the recording sink the tests read
# ==========================================================================


@pytest.fixture(autouse=True)
def sink_reset() -> Iterator[None]:
    """Restore ``default_sink`` around EVERY test in this module.

    ``graphia.substitution.set_sink`` is process-global by design (the module
    docstring's "Not promised, second: isolation"), so without this fixture a
    test that installs a recording sink leaves it installed for the next test —
    and for every later module in the run. Every test below installs a sink, so
    every test below would be order-dependent.

    The reset runs on BOTH sides. Before, so a leak from anywhere else cannot
    reach a test here; after, so a test here cannot leak outward. And it resets
    to ``default_sink`` rather than to a no-op, which is the fixed point
    ``reset_sink`` documents: a no-op would leave every test that installs no
    sink of its own running with the instrumentation dead, so
    :func:`test_a_forced_substitution_leaves_one_warning_from_the_default_sink`
    — the only test here that exercises production's actual sink — would be
    exercising a fixture artefact instead.

    **This fixture is itself the thing most likely to be silently wrong**,
    because a test that leaks counts into the next one usually still passes. So
    the leak it prevents is constructed on purpose by the pair
    ``test_leak_probe_a_…`` / ``test_leak_probe_b_…`` below; deleting the
    ``autouse=True`` here turns probe B red.
    """
    reset_sink()
    yield
    reset_sink()


class _RecordingSink:
    """An accumulating sink: records ``(event, kind)`` in call order.

    The shape Slice 7's harness sink will have, minus the chaining to
    ``default_sink`` and minus any locking (nothing here is concurrent). Kept
    assertion-free on purpose — a raising sink is deliberately unguarded in
    ``substitution.py``, so an ``assert`` in here would surface as a crash from
    inside a node rather than as a test failure with a useful diff.
    """

    def __init__(self) -> None:
        self.events: list[tuple[SubstitutionEvent, SubstitutionKind]] = []

    def __call__(self, event: SubstitutionEvent, kind: SubstitutionKind) -> None:
        self.events.append((event, kind))

    def attempts(self) -> list[SubstitutionKind]:
        return [k for e, k in self.events if e is SubstitutionEvent.ATTEMPT]

    def substitutions(self) -> list[SubstitutionKind]:
        return [k for e, k in self.events if e is SubstitutionEvent.SUBSTITUTION]


@pytest.fixture
def sink() -> _RecordingSink:
    """Install a fresh :class:`_RecordingSink` and hand it to the test.

    Deliberately WITHOUT teardown: uninstalling is :func:`sink_reset`'s job, and
    keeping it there is what makes the leak-probe pair below a real probe of a
    real mechanism rather than of a second, redundant reset.
    """
    recorder = _RecordingSink()
    set_sink(recorder)
    return recorder


# ==========================================================================
# Model stand-ins: one that always fails, one that answers from a table
# ==========================================================================


class _FailingModel:
    """A tier stand-in whose every ``invoke`` raises — the forced-failure half.

    Raising on EVERY invocation (rather than scripting a fixed-length queue)
    is what makes one fake serve all seven call sites: four of the seven invoke
    twice and three invoke once, and a queue sized for the wrong one would let
    a helper's second invocation succeed and never substitute at all.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.invocations = 0
        self.bound_schemas: list[type] = []

    def with_structured_output(self, schema: type, **kwargs: Any) -> "_FailingModel":
        self.bound_schemas.append(schema)
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        raise self._error


class _BoundScripted:
    """One schema's view of a :class:`_ScriptedModel` — the real binding shape."""

    def __init__(self, owner: "_ScriptedModel", schema: type) -> None:
        self._owner = owner
        self._schema = schema

    def invoke(self, messages: Any) -> Any:
        return self._owner._answer(self._schema)


class _ScriptedModel:
    """A tier stand-in that answers each bound schema from a fixed table.

    The scripted-success half. An unknown schema is a test-suite defect rather
    than a game outcome, so it goes through ``pytest.fail(..., pytrace=False)``:
    ``Failed`` derives from ``BaseException`` and therefore walks straight
    through the broad ``except Exception`` every one of these helpers wraps its
    call in — otherwise the helper would swallow it and substitute, and the
    success half of the sweep would silently become a second copy of the
    failure half.
    """

    def __init__(self, answers: dict[type, Any]) -> None:
        self._answers = answers
        self.invocations = 0
        self.bound_schemas: list[type] = []

    def with_structured_output(self, schema: type, **kwargs: Any) -> _BoundScripted:
        self.bound_schemas.append(schema)
        return _BoundScripted(self, schema)

    def _answer(self, schema: type) -> Any:
        self.invocations += 1
        if schema not in self._answers:
            pytest.fail(
                f"_ScriptedModel was bound to {schema.__name__}, which this "
                "case scripted no answer for. The scripted-success half of the "
                "sweep cannot pass by accident: add the answer to the case "
                "builder in tests/test_substitution_counts.py.",
                pytrace=False,
            )
        return self._answers[schema]


def _roster_validation_error() -> ValidationError:
    """A real ``ValidationError`` from ``Roster``'s own distinctness validator.

    ``_generate_names`` is the ONE of the seven helpers that does not catch
    broadly: its two ``except`` clauses name ``ValidationError`` only. A
    hand-rolled ``RuntimeError`` therefore propagates straight out of it (pinned
    in :func:`test_a_roster_transport_failure_propagates_and_books_no_substitution`),
    so the sweep needs the genuine article. Harvested from a construction that
    the validator rejects rather than assembled through
    ``ValidationError.from_exception_data``, which would let the test keep
    passing after the validator itself was removed.
    """
    try:
        Roster(names=["Duplicate", "duplicate"])
    except ValidationError as exc:
        return exc
    raise AssertionError(
        "Roster's case-insensitive distinctness validator no longer rejects "
        "['Duplicate', 'duplicate'], so this module can no longer force "
        "_generate_names down its ValidationError-only except clauses. Force "
        "it with whatever the validator does reject, or with a wrong-length "
        "reply (see test_a_short_roster_reply_substitutes_into_a_mixed_roster)."
    )


# ==========================================================================
# Table stubs
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


def _table() -> dict[str, PlayerState]:
    """Three AI players (two Citizens, one Mafioso) and one human."""
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-ben": _player("p-ben", "Ben", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
        "p-hugo": _player("p-hugo", "Hugo", "law_abiding", is_human=True),
    }


def _state(**overrides: Any) -> GameState:
    """A mid-Day state, complete enough for all four ``nodes/day.py`` helpers."""
    state: GameState = {
        "cycle": 3,
        "phase": "day",
        "players": _table(),
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


# ==========================================================================
# The seven-way case table
# ==========================================================================


@dataclass
class _Case:
    """One call site, wired for both halves of the sweep.

    ``install`` patches the tier binding(s) the call site actually reads;
    ``run`` drives the helper with no arguments left to decide. ``scripted``
    is the answer table for :class:`_ScriptedModel`, so the two halves differ
    only in which stand-in is installed.
    """

    kind: SubstitutionKind
    schema: type
    install: Callable[[Any], None]
    run: Callable[[], Any]
    failure: Exception
    scripted: dict[type, Any]
    expected_fallback: Any
    expected_scripted: Any
    invocations_when_failing: int = 2


def _installer(monkeypatch: pytest.MonkeyPatch, *targets: str) -> Callable[[Any], None]:
    """Patch each dotted ``targets`` binding with a zero-arg factory for ``model``."""

    def _install(model: Any) -> None:
        for target in targets:
            monkeypatch.setattr(target, lambda _model=model: _model)

    return _install


def _build_roster(monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool) -> _Case:
    return _Case(
        kind=SubstitutionKind.ROSTER,
        schema=Roster,
        install=_installer(monkeypatch, "graphia.nodes.setup.get_small"),
        run=lambda: setup_nodes._generate_names(_ROSTER_COUNT),
        failure=_roster_validation_error(),
        scripted={Roster: Roster(names=["Ava", "Ben", "Cyd"])},
        # Nothing usable came back on either invocation, so ``_coerce_to_count``
        # has nothing to keep and every seat is a placeholder. This is the clean
        # end of a two-shape fallback; the mixed shape has its own test.
        expected_fallback=Roster(names=["Player-1", "Player-2", "Player-3"]),
        expected_scripted=Roster(names=["Ava", "Ben", "Cyd"]),
    )


def _build_persona(monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool) -> _Case:
    player = _player("p-ava", "Ava", "law_abiding")
    return _Case(
        kind=SubstitutionKind.PERSONA,
        schema=Persona,
        # ``model=None`` below, so the helper reaches for the cached gameplay
        # tier — ``nodes.setup.get_large``, not ``get_persona_model``. The
        # hotter persona model only enters through the NODE, which is where the
        # generations-not-players denominator lives.
        install=_installer(monkeypatch, "graphia.nodes.setup.get_large"),
        run=lambda: setup_nodes._generate_one_persona(player, []),
        failure=RuntimeError("persona model down"),
        scripted={
            Persona: Persona(
                personality="bold",
                manner="terse",
                public_backstory="the baker",
            )
        },
        expected_fallback=_AVA_FALLBACK_PERSONA,
        expected_scripted=PlayerPersona(
            personality="bold",
            manner="terse",
            public_persona="the baker",
            true_self="",
        ),
    )


def _build_pointing(monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool) -> _Case:
    alive = [
        _player("p-ava", "Ava", "law_abiding"),
        _player("p-ben", "Ben", "law_abiding"),
    ]
    mafia = _player("p-mara", "Mara", "mafia")

    if expect_fallback:
        # Pin the RNG at the helper's own module, per the ``_shuffle_order``
        # convention (``tests/test_multi_round_consensus.py`` patches this exact
        # attribute). Pinned to the LAST candidate while the scripted half
        # answers with the FIRST, so the fallback value and the scripted value
        # can never be mistaken for one another.
        monkeypatch.setattr(night_nodes.random, "choice", lambda seq: seq[-1])
    else:

        def _boom(seq: Any) -> Any:
            raise AssertionError(
                "random.choice was reached on the scripted-success path: "
                "_ai_pick_target fell through to its substitution branch even "
                "though the model answered with a valid target."
            )

        monkeypatch.setattr(night_nodes.random, "choice", _boom)

    return _Case(
        kind=SubstitutionKind.POINTING,
        schema=Pointing,
        install=_installer(monkeypatch, "graphia.nodes.night.get_large"),
        run=lambda: night_nodes._ai_pick_target(alive, mafia),
        failure=RuntimeError("pointing model down"),
        scripted={Pointing: Pointing(target_id="p-ava")},
        expected_fallback="p-ben",
        expected_scripted="p-ava",
    )


def _build_ballot(monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool) -> _Case:
    state = _state()
    voter = state["players"]["p-ava"]
    target = state["players"]["p-ben"]
    return _Case(
        kind=SubstitutionKind.BALLOT,
        schema=Ballot,
        install=_installer(monkeypatch, "graphia.nodes.day.get_large"),
        run=lambda: day_nodes._ai_ballot(voter, target, state),
        failure=RuntimeError("ballot model down"),
        # ``yes=True`` deliberately: ``Ballot(yes=False)`` is both the fallback
        # AND the game's most common genuine answer, so a scripted ``No`` would
        # make the success half's value assertion indistinguishable from the
        # failure half's.
        scripted={Ballot: Ballot(yes=True)},
        expected_fallback=Ballot(yes=False),
        expected_scripted=Ballot(yes=True),
    )


def _build_day_action(
    monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool
) -> _Case:
    state = _state()
    speaker = state["players"]["p-ava"]
    return _Case(
        kind=SubstitutionKind.DAY_ACTION,
        schema=DayAction,
        install=_installer(monkeypatch, "graphia.nodes.day.get_large"),
        run=lambda: day_nodes._ai_day_action(speaker, state),
        failure=RuntimeError("day-action model down"),
        scripted={DayAction: DayAction(kind="speak", text="A scripted Day line.")},
        expected_fallback=DayAction(kind="speak", text=_DAY_ACTION_FALLBACK_TEXT),
        expected_scripted=DayAction(kind="speak", text="A scripted Day line."),
    )


def _build_reflection(
    monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool
) -> _Case:
    state = _state()
    speaker = state["players"]["p-ava"]
    return _Case(
        kind=SubstitutionKind.REFLECTION,
        schema=Reflection,
        install=_installer(monkeypatch, "graphia.nodes.day.get_large"),
        run=lambda: day_nodes._ai_reflect(speaker, state),
        failure=RuntimeError("reflection model down"),
        scripted={Reflection: Reflection(thought="A scripted reflection.")},
        expected_fallback=_REFLECTION_FALLBACK,
        expected_scripted="A scripted reflection.",
        # This helper invokes ONCE where the four above invoke twice, and the
        # event pair is identical either way — which is the property that makes
        # the seven kinds' rates readable against one another.
        invocations_when_failing=1,
    )


def _build_diary(monkeypatch: pytest.MonkeyPatch, *, expect_fallback: bool) -> _Case:
    state = _state()
    speaker = state["players"]["p-ava"]
    return _Case(
        kind=SubstitutionKind.DIARY,
        schema=Diary,
        install=_installer(monkeypatch, "graphia.nodes.day.get_large"),
        run=lambda: day_nodes._ai_diary(speaker, state),
        failure=RuntimeError("diary model down"),
        scripted={Diary: Diary(entry="A scripted diary entry.")},
        expected_fallback=_DIARY_FALLBACK,
        expected_scripted="A scripted diary entry.",
        invocations_when_failing=1,
    )


_BUILDERS: dict[SubstitutionKind, Callable[..., _Case]] = {
    SubstitutionKind.ROSTER: _build_roster,
    SubstitutionKind.PERSONA: _build_persona,
    SubstitutionKind.POINTING: _build_pointing,
    SubstitutionKind.BALLOT: _build_ballot,
    SubstitutionKind.DAY_ACTION: _build_day_action,
    SubstitutionKind.REFLECTION: _build_reflection,
    SubstitutionKind.DIARY: _build_diary,
}


# ==========================================================================
# The vocabulary: seven kinds, 1:1 with GAMEPLAY_SCHEMAS, and no eighth
# ==========================================================================


def _schema_key(cls: type) -> str:
    """``DayAction`` -> ``day_action``: the mapping Task 6.1 declared by hand.

    ``substitution.py`` deliberately does NOT derive its members from
    ``GAMEPLAY_SCHEMAS`` (importing ``graphia.llm`` would drag ``boto3`` and
    ``langchain_ollama`` behind a module whose whole job is to name seven
    strings), so the correspondence is asserted here instead — where the heavy
    import is affordable. Deriving the expectation mechanically rather than
    hand-typing seven pairs is what keeps this test from becoming a second copy
    of the vocabulary that can drift on its own.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()


def test_the_kind_vocabulary_covers_GAMEPLAY_SCHEMAS_exactly() -> None:
    """Same set AND same order as ``llm.GAMEPLAY_SCHEMAS``.

    Both directions are asserted separately because they fail differently and a
    reader needs to know which happened: a set mismatch is a kind added or
    dropped on one side only; an order mismatch with matching sets is a
    reordering, which matters because ``SubstitutionKind``'s declaration order
    is Slice 7's DISPLAY order (one ledger row per kind) and inserting rather
    than appending re-partitions a rendered record.
    """
    derived = [_schema_key(cls) for cls in GAMEPLAY_SCHEMAS]
    actual = [kind.value for kind in SubstitutionKind]

    assert set(actual) == set(derived), (
        "SubstitutionKind and llm.GAMEPLAY_SCHEMAS no longer name the same "
        "vocabulary. The two are held 1:1 by this test rather than by "
        "derivation (see substitution.py's module docstring for why), so a new "
        "schema needs a new enum member in the same change."
    )
    assert actual == derived, (
        "SubstitutionKind's declaration ORDER no longer mirrors "
        "GAMEPLAY_SCHEMAS. That order is Slice 7's ledger display order — "
        "append a new kind, never insert one."
    )


def test_the_vocabulary_holds_exactly_seven_kinds() -> None:
    """Seven, as an absolute literal.

    The set/order test above passes when both vocabularies grow together, which
    is correct — but "exactly seven" is a claim three docstrings and Slice 7's
    renderer make, so an eighth kind should be a deliberate change that lands
    with the renderer rather than a quiet one.
    """
    assert len(SubstitutionKind) == 7
    assert len(GAMEPLAY_SCHEMAS) == 7


def test_every_kind_has_a_case_in_the_sweep() -> None:
    """The sweep really is seven-way and cannot silently shrink.

    Without this, deleting a builder from ``_BUILDERS`` would remove a call
    site's coverage and turn the whole module green — the parametrization would
    simply run six cases and no test would notice. Chains onto the two tests
    above: schemas pin kinds, and kinds pin the sweep.
    """
    assert set(_BUILDERS) == set(SubstitutionKind)


@pytest.mark.parametrize("notify", [note_attempt, note_substitution])
def test_an_unknown_kind_fails_loudly_and_reaches_no_sink(
    notify: Callable[[Any], None], sink: _RecordingSink
) -> None:
    """A bogus kind raises rather than opening a silent eighth bucket.

    The annotation is not a runtime check, so an untyped caller — a test, the
    harness, anything assembling a kind out of data — could otherwise hand a
    free-form string to an accumulating sink and produce an eighth row in a
    ledger block that claims seven. Both halves matter: the raise, and the sink
    having recorded NOTHING, which is the half that says the bucket was never
    created.
    """
    with pytest.raises(ValueError, match="is not a SubstitutionKind"):
        notify("dairy")

    assert sink.events == []


def test_a_valid_string_spelling_reaches_the_sink_as_a_member(
    sink: _RecordingSink,
) -> None:
    """``note_attempt("diary")`` normalises to the member before the sink sees it.

    ``type(...) is SubstitutionKind`` rather than ``==``, deliberately: a
    ``StrEnum`` member compares equal to its own string, so an equality
    assertion here would pass on a bare ``str`` and prove nothing. The
    guarantee is that a dict keyed by what the sink receives cannot grow a
    second bucket for the same kind spelled two ways.
    """
    note_attempt("diary")
    note_substitution("diary")

    assert sink.events == [
        (SubstitutionEvent.ATTEMPT, SubstitutionKind.DIARY),
        (SubstitutionEvent.SUBSTITUTION, SubstitutionKind.DIARY),
    ]
    assert all(type(kind) is SubstitutionKind for _, kind in sink.events)


# ==========================================================================
# The seven-way sweep, run twice
# ==========================================================================


@pytest.mark.parametrize("kind", list(SubstitutionKind), ids=lambda k: k.value)
def test_a_forced_failure_books_exactly_one_substitution_and_the_fallback(
    kind: SubstitutionKind, sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each call site: one attempt, one substitution, and the known stand-in.

    Four assertions, none of which is sufficient alone:

    - ``substitutions() == [kind]`` — exactly one, and of the right kind. The
      two helpers with two ``except`` blocks each would book two here if the
      notification sat in the excepts rather than on the fallback ``return``.
    - ``attempts() == [kind]`` — exactly one, even for the five helpers that
      invoke the model twice. One event per ANSWER wanted, not per invocation;
      a per-invocation attempt would halve every rate Slice 7 reports.
    - the returned value IS the fallback — without which the count could be
      booked by a helper that did not actually substitute.
    - the model was really reached, ``invocations`` times — without which a
      helper that short-circuited before invoking at all would pass every
      assertion above.

    ``bound_schemas`` is one entry, not one per invocation: every one of the
    seven hoists its ``with_structured_output`` above the retry, so the two
    numbers differing is itself a fact worth pinning.
    """
    case = _BUILDERS[kind](monkeypatch, expect_fallback=True)
    model = _FailingModel(case.failure)
    case.install(model)

    result = case.run()

    assert sink.substitutions() == [kind]
    assert sink.attempts() == [kind]
    assert result == case.expected_fallback
    assert model.invocations == case.invocations_when_failing
    assert model.bound_schemas == [case.schema]


@pytest.mark.parametrize("kind", list(SubstitutionKind), ids=lambda k: k.value)
def test_a_scripted_success_books_no_substitution_and_returns_the_answer(
    kind: SubstitutionKind, sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anti-vacuity twin: a good answer books the attempt and nothing more.

    Without this half, a helper that called ``note_substitution``
    unconditionally — on entry, beside ``note_attempt`` — would pass the forced
    half of the sweep for all seven kinds and report a 100% substitution rate
    in the ledger forever. The denominator is still booked here, which is what
    makes a kind that was attempted and never substituted readable as ``0.0``
    rather than as absent.
    """
    case = _BUILDERS[kind](monkeypatch, expect_fallback=False)
    model = _ScriptedModel(case.scripted)
    case.install(model)

    result = case.run()

    assert sink.substitutions() == []
    assert sink.attempts() == [kind]
    assert result == case.expected_scripted
    assert model.invocations == 1


# ==========================================================================
# Items, not fan-outs: two players failing books two
# ==========================================================================


def _two_ai_table() -> dict[str, PlayerState]:
    """Exactly two surviving AI players, plus a human the fan-out skips."""
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
        "p-hugo": _player("p-hugo", "Hugo", "law_abiding", is_human=True),
    }


def test_two_players_failing_books_two_diary_substitutions(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``day_diary``'s fan-out books one event per ENTRY, not one per Day.

    The counter counts items. A Day on which two AI players both fail to write
    is two substituted answers, and a rate taken over "Days" would report one
    of them. The human is skipped, so the count is two and not three — which is
    also what proves the events are keyed to the fan-out rather than to the
    node running at all.
    """
    monkeypatch.setattr(
        "graphia.nodes.day.get_large",
        lambda: _FailingModel(RuntimeError("diary model down")),
    )

    delta = day_nodes.day_diary(_state(players=_two_ai_table()))

    assert sink.attempts() == [SubstitutionKind.DIARY, SubstitutionKind.DIARY]
    assert sink.substitutions() == [SubstitutionKind.DIARY, SubstitutionKind.DIARY]
    # And both entries really are the stand-in, so the pair of counts is not
    # two bookings over one substituted answer and one genuine one.
    written = {
        pid: [record["text"] for record in records]
        for pid, records in delta["private_diaries"].items()
    }
    assert written == {"p-ava": [_DIARY_FALLBACK], "p-mara": [_DIARY_FALLBACK]}


def test_the_day_diary_delta_carries_no_substitution_key(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delta is ``{"private_diaries": …}`` and nothing else, even when it substituted.

    The direct sibling of
    ``tests/test_slice39_diary_before_night.py::test_delta_contains_no_messages_key_at_all``,
    and it pins Slice 6's stated boundary: no state-shape change, no reducer, no
    caller threading. The count travels out through the injected sink, so a
    ``"substitutions"`` channel appearing on this delta would mean the
    measurement had started reshaping the thing measured — the exact trade
    ``substitution.py`` rejects a module-global counter for.

    Nothing is asserted about the INNER keys of a ``DiaryRecord``: Slice 8 adds
    ``substituted`` there on purpose, and it belongs there because a marking a
    reviewer can see has to be CARRIED. This test is about the channel set.
    """
    monkeypatch.setattr(
        "graphia.nodes.day.get_large",
        lambda: _FailingModel(RuntimeError("diary model down")),
    )

    delta = day_nodes.day_diary(_state(players=_two_ai_table()))

    assert set(delta.keys()) == {"private_diaries"}
    assert sink.substitutions() == [SubstitutionKind.DIARY, SubstitutionKind.DIARY]


# ==========================================================================
# PERSONA's denominator is generations, not players
# ==========================================================================


def _persona_state() -> GameState:
    return _state(players=_two_ai_table())


def test_persona_books_one_event_per_generation_not_per_player(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diversity ON: two failing seats book SIX persona events, not two.

    ``generate_personas`` -> ``_generate_with_regen`` calls
    ``_generate_one_persona`` up to ``persona_regen_attempts + 1`` = three times
    per seat, and a generation that FAILED is skipped rather than accepted (the
    loop ``continue``s past a fallback), so all three run. Three generations per
    seat, two seats, six events.

    This is asserted on GENERATIONS, per Task 6.1's instruction for any
    node-level ``PERSONA`` test — and the reason the instruction exists is
    visible right here: ``substitutions.persona.attempted`` will read 6 for a
    two-player table, so a reader who assumes it counts AI players will read the
    rate against the wrong denominator. Slice 7 has to label that column.
    """
    failing = _FailingModel(RuntimeError("persona model down"))
    monkeypatch.setattr("graphia.nodes.setup.get_persona_model", lambda temperature: failing)
    monkeypatch.setattr("graphia.nodes.setup.get_large", lambda: failing)

    setup_nodes.generate_personas(_persona_state(), persona_diversity_enabled=True)

    assert len(sink.attempts()) == 6
    assert len(sink.substitutions()) == 6
    assert set(sink.attempts()) == {SubstitutionKind.PERSONA}
    assert set(sink.substitutions()) == {SubstitutionKind.PERSONA}


def test_persona_flag_off_books_one_event_per_player(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diversity OFF (ADR 011 parity): two failing seats book TWO persona events.

    The flag-off parity twin, and the measurement of exactly what the default-on
    flag costs the denominator: the same table, the same failing model, one
    third of the events. Which is why the ledger column cannot be read as "AI
    players" — the number it reports depends on a flag.
    """
    failing = _FailingModel(RuntimeError("persona model down"))
    monkeypatch.setattr("graphia.nodes.setup.get_large", lambda: failing)

    def _unreachable(temperature: float) -> Any:
        raise AssertionError(
            "get_persona_model was reached with persona diversity OFF; the "
            "flag-off path must use the cached gameplay tier (spec 034)."
        )

    monkeypatch.setattr("graphia.nodes.setup.get_persona_model", _unreachable)

    setup_nodes.generate_personas(_persona_state(), persona_diversity_enabled=False)

    assert sink.attempts() == [SubstitutionKind.PERSONA] * 2
    assert sink.substitutions() == [SubstitutionKind.PERSONA] * 2


# ==========================================================================
# The two ROSTER traps, pinned rather than worked around
# ==========================================================================


def test_a_roster_transport_failure_propagates_and_books_no_substitution(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_generate_names`` catches only ``ValidationError`` — pinned, not lamented.

    The other six helpers catch broadly, so a ``RuntimeError`` is a fine way to
    force them. Here it propagates: an attempt is booked, no substitution is,
    and there is no return value at all. That asymmetry is deliberate in the
    production docstring ("a broken model or transport propagates and surfaces
    as an error banner"), and pinning it is what stops a future reader from
    forcing this call site with a generic exception and concluding the
    notification is missing.

    It is also the sharpest illustration of why ``note_attempt`` sits BEFORE the
    ``try``: the attempt survives an exception that the helper never catches.
    """
    monkeypatch.setattr(
        "graphia.nodes.setup.get_small",
        lambda: _FailingModel(RuntimeError("bedrock throttled")),
    )

    with pytest.raises(RuntimeError, match="bedrock throttled"):
        setup_nodes._generate_names(_ROSTER_COUNT)

    assert sink.attempts() == [SubstitutionKind.ROSTER]
    assert sink.substitutions() == []


def test_a_short_roster_reply_substitutes_into_a_mixed_roster(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second route into ``ROSTER``'s substitution branch, and its OTHER value.

    A parsed roster of the wrong length does not raise, so it falls through both
    ``if len(...) == count`` guards without touching an ``except``. That is why
    the notification sits on the fallback ``return``: an except-based one would
    miss this route entirely — and this is the route a decoding regression
    actually takes, since a grammar-constrained model returns a well-formed
    ``Roster`` or nothing.

    The value is the mixed shape Task 6.3 warns about: what came back is KEPT
    and only the shortfall is padded, so "the known fallback value" is
    ``Player-1 …`` only when nothing usable arrived at all.
    """
    monkeypatch.setattr(
        "graphia.nodes.setup.get_small",
        lambda: _ScriptedModel({Roster: Roster(names=["Ava"])}),
    )

    result = setup_nodes._generate_names(_ROSTER_COUNT)

    assert result.names == ["Ava", "Player-1", "Player-2"]
    assert sink.attempts() == [SubstitutionKind.ROSTER]
    assert sink.substitutions() == [SubstitutionKind.ROSTER]


# ==========================================================================
# The log line default_sink owns (filtered by logger name — trap (d))
# ==========================================================================


def test_a_forced_substitution_leaves_one_warning_from_the_default_sink(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No sink installed: production's ``default_sink`` logs once, at WARNING.

    This is the only test here that runs on the production sink, which is why
    ``reset_sink`` restores ``default_sink`` rather than a no-op — under a no-op
    reset this test would be exercising a fixture artefact.

    ``caplog`` is filtered by ``record.name``, and the filter is load-bearing
    rather than tidy: ``_ai_diary`` emits its own ``graphia.nodes.day``
    ``logger.exception`` on this same path, so an unfiltered assertion mixes two
    unrelated records and an ``assert caplog.records == []`` anywhere near a
    substituting helper fails for the wrong reason.
    """
    monkeypatch.setattr(
        "graphia.nodes.day.get_large",
        lambda: _FailingModel(RuntimeError("diary model down")),
    )
    state = _state()
    speaker = state["players"]["p-ava"]

    with caplog.at_level(logging.WARNING, logger="graphia.substitution"):
        result = day_nodes._ai_diary(speaker, state)

    assert result == _DIARY_FALLBACK
    records = [r for r in caplog.records if r.name == "graphia.substitution"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "diary" in records[0].getMessage()


def test_a_scripted_success_leaves_no_substitution_warning(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log line's anti-vacuity twin: an accepted answer logs nothing.

    ``note_attempt`` is silent by design — one record per model call across a
    whole game would bury the events this module exists to surface — so a
    successful diary write leaves the ``graphia.substitution`` logger empty.
    """
    monkeypatch.setattr(
        "graphia.nodes.day.get_large",
        lambda: _ScriptedModel({Diary: Diary(entry="A scripted diary entry.")}),
    )
    state = _state()
    speaker = state["players"]["p-ava"]

    with caplog.at_level(logging.WARNING, logger="graphia.substitution"):
        result = day_nodes._ai_diary(speaker, state)

    assert result == "A scripted diary entry."
    assert [r for r in caplog.records if r.name == "graphia.substitution"] == []


# ==========================================================================
# The reset fixture's own proof: the leak it prevents, constructed
# ==========================================================================

# A deliberately MODULE-LEVEL accumulator — the leak vehicle. Probe A installs a
# sink writing here and never uninstalls it; probe B substitutes without
# installing any sink at all. With ``sink_reset`` autouse, probe B's events
# reach ``default_sink`` and this list does not grow. Without it, probe A's sink
# is still installed and probe B appends to it — and probe B goes red.
_LEAKED_EVENTS: list[tuple[SubstitutionEvent, SubstitutionKind]] = []


def _leaky_sink(event: SubstitutionEvent, kind: SubstitutionKind) -> None:
    _LEAKED_EVENTS.append((event, kind))


def _force_one_diary_substitution(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(
        "graphia.nodes.day.get_large",
        lambda: _FailingModel(RuntimeError("diary model down")),
    )
    state = _state()
    return day_nodes._ai_diary(state["players"]["p-ava"], state)


def test_leak_probe_a_substitutes_under_a_sink_it_never_uninstalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half one of the fixture proof: install a leaky sink and substitute.

    This test is well-behaved in every respect except that it does NOT call
    ``reset_sink``, which is the realistic mistake — a harness or a test that
    installs a sink and returns early, or raises, or simply forgets. Correctness
    here is entirely the autouse fixture's responsibility, and probe B is what
    checks it discharged it.
    """
    _LEAKED_EVENTS.clear()
    set_sink(_leaky_sink)

    assert _force_one_diary_substitution(monkeypatch) == _DIARY_FALLBACK
    assert (SubstitutionEvent.SUBSTITUTION, SubstitutionKind.DIARY) in _LEAKED_EVENTS


def test_leak_probe_b_records_nothing_because_the_fixture_reset_the_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half two: substitute with NO sink installed and prove probe A's sink is gone.

    Measured by removing ``autouse=True`` from :func:`sink_reset`: this test
    fails, reporting probe A's sink still catching probe B's substitution, while
    probe A stays green — the signature of a leak, and the reason a missing
    reset fixture is so easy to ship. Reading ``before`` rather than asserting
    an empty list keeps this test correct when run alone (``-k probe_b``), where
    there is no probe A to leak from.
    """
    before = list(_LEAKED_EVENTS)

    assert _force_one_diary_substitution(monkeypatch) == _DIARY_FALLBACK

    assert _LEAKED_EVENTS == before, (
        "A substitution booked by this test reached the sink installed by "
        "test_leak_probe_a_…, which means the autouse sink_reset fixture did "
        "not restore default_sink between the two. Every test in this module "
        "is order-dependent while that is true."
    )
