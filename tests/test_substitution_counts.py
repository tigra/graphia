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

``ROSTER`` cannot be forced with a generic exception. ``_generate_names`` is
the one helper of the seven that does not catch broadly: it catches
``setup._UNUSABLE_ANSWER_ERRORS`` — the two types a structured-output *parser*
raises, :class:`~pydantic.ValidationError` on Bedrock's tool-use binding and
:class:`~langchain_core.exceptions.OutputParserException` on the local
grammar binding. A ``RuntimeError`` therefore still propagates out of the
helper, booking an attempt, no substitution and no return value at all, and
that is pinned deliberately in
:func:`test_a_roster_transport_failure_propagates_and_books_no_substitution`;
the sweep forces ``ROSTER`` with a real ``ValidationError`` harvested from
``Roster``'s own distinctness validator instead.

**Spec 041, Task 12.2a — why that paragraph used to read "catches only
``ValidationError``", and what it cost.** ADR 013 swapped the local transport
from tool use to grammar-constrained decoding, which swapped the parser and
therefore the exception: ``PydanticToolsParser`` raises the model's own
``ValidationError``, whereas ``PydanticOutputParser`` catches that and
re-raises its own ``OutputParserException``, of which
``issubclass(OutputParserException, ValidationError)`` is **False**. So on the
shipped local provider the narrow clauses caught nothing, the substitution
branch became unreachable, and a grammar-perfect roster with a repeated name
ended the game — measured at 1 of 20 trials on ``qwen2.5:3b``. **This module
was green throughout, because every test here stubbed the exception TYPE
rather than driving the real parser.** That is corrected below:
:class:`_GrammarConstrainedFake` now hands its payload to the parser of the
*real* bound runnable of the *real* provider client, and the two negative
controls run once per :data:`_PARSE_BOUNDARIES` entry, so neither type can be
dropped from the production tuple without a red test.

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

Task 6.4's negative controls, added below the sweep
---------------------------------------------------

The schema-valid/model-invalid ``DayAction`` and its ``Roster`` twin now live
here too, in their own section: ADR 013's grammar makes a malformed *document*
unrepresentable, and these two show that every rule stated in a Pydantic
validator instead of in JSON Schema stays outside that guarantee, so the
substitution path is still reachable on the enforcing provider. Neither is
constructible as an instance, so both arrive as raw payload through a fake that
parses — see that section's own header comment for the delivery choice and for
where each one actually lands, which is not where a reader would guess.

What is deliberately NOT here
-----------------------------

No assertion is made about the INNER keys of a ``DiaryRecord``. Slice 8 adds
``substituted`` to that record on purpose, and pre-breaking it here would
misread Task 6.3's requirement, which is about the DELTA carrying no
substitution channel — Slice 6 changes no state shape.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema
import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ValidationError

import graphia.nodes.day as day_nodes
import graphia.nodes.night as night_nodes
import graphia.nodes.setup as setup_nodes
from graphia.llm import (
    GAMEPLAY_SCHEMAS,
    Ballot,
    BedrockProvider,
    DayAction,
    Diary,
    OllamaProvider,
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
    default_sink,
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
    broadly: its two ``except`` clauses share ``setup._UNUSABLE_ANSWER_ERRORS``,
    which names the two parse-boundary types and nothing else. A hand-rolled
    ``RuntimeError`` therefore propagates straight out of it (pinned in
    :func:`test_a_roster_transport_failure_propagates_and_books_no_substitution`),
    so the sweep needs a genuine member of that tuple. Harvested from a
    construction that the validator rejects rather than assembled through
    ``ValidationError.from_exception_data``, which would let the test keep
    passing after the validator itself was removed.

    This is the **cloud** boundary's exception, and using it here is faithful:
    Bedrock's tool-use parser lets the model's own ``ValidationError`` escape
    from ``invoke``, which is exactly where :class:`_FailingModel` raises it.
    The local grammar boundary's ``OutputParserException`` is not exercised by
    the sweep — it has the two parametrized negative controls below instead,
    which reach it through the real parser rather than by naming it.
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
    """A broken TRANSPORT propagates — pinned, and now paired with its twin.

    The other six helpers catch broadly, so a ``RuntimeError`` is a fine way to
    force them. Here it propagates: an attempt is booked, no substitution is,
    and there is no return value at all. That asymmetry is deliberate in the
    production docstring ("a broken model or transport propagates and surfaces
    as an error banner"), and pinning it is what stops a future reader from
    forcing this call site with a generic exception and concluding the
    notification is missing.

    It is also the sharpest illustration of why ``note_attempt`` sits BEFORE the
    ``try``: the attempt survives an exception that the helper never catches.

    **Spec 041, Task 12.2a — this test used to be half a contract, and the
    missing half was the defect.** It pinned "an exception the clauses do not
    name propagates" and stopped there, so it was equally satisfied by a
    version of ``_generate_names`` that named the *wrong* content type and
    propagated every failure the local transport could produce — which is what
    shipped, and what ended a measured game. The half that closes it is
    :func:`test_a_grammar_valid_roster_with_a_case_variant_books_exactly_one`,
    now run once per real parse boundary: content recovers, transport
    propagates, and neither half can pass alone.

    The ``assert not isinstance(...)`` line states the *reason* rather than
    re-observing the outcome. Without it this test says only "a RuntimeError
    escapes"; with it, it says "a RuntimeError escapes BECAUSE it is not a
    parse-boundary failure" — and it turns red if somebody widens the
    production tuple to ``Exception``, which would fix the symptom this task
    fixed by destroying the distinction instead.
    """
    broken_transport = RuntimeError("bedrock throttled")
    monkeypatch.setattr(
        "graphia.nodes.setup.get_small",
        lambda: _FailingModel(broken_transport),
    )

    # The reason, stated at the boundary that decides: this is not a parser's
    # exception, so it is not the model answering badly.
    assert not isinstance(broken_transport, setup_nodes._UNUSABLE_ANSWER_ERRORS)

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
# Task 6.4: the negative controls — the grammar guarantees SHAPE, not meaning
# ==========================================================================
#
# ADR 013 moved the local provider to grammar-constrained decoding, where
# non-conforming output is unrepresentable at the token level rather than
# merely requested. That is true, and it is spec 041's main result. These two
# controls exist so that nobody reads it as a reason to stop looking: what a
# grammar makes unrepresentable is a malformed DOCUMENT, and every rule the
# game states in a Pydantic validator rather than in JSON Schema sits outside
# the grammar's reach. ADR 013 §5 already records this ("the grammar
# constrains SHAPE, not CONTENT"); these tests are what stop that bullet from
# being a claim nobody checks.
#
# The gap is DEMONSTRATED, not asserted in a comment.
# :func:`test_the_grammar_accepts_the_payload_the_validator_rejects` validates
# each payload against the very ``model_json_schema()`` a provider is handed as
# its grammar, and THEN shows construction raising. Two lines, and they are the
# whole argument — a comment reading "the grammar would allow this" is exactly
# the claim under test, so a comment cannot be the evidence for it.
#
# **Delivery: raw payload through a fake that hands it to the REAL PARSER.**
# ``DayAction(kind="vote")`` and ``Roster(names=["Ava", "ava"])`` each raise
# ``ValidationError`` at CONSTRUCTION, so neither control can be handed in as
# an object at all — which is the point rather than an obstacle.
# :class:`_GrammarConstrainedFake` therefore holds the raw dict and, inside
# ``invoke``, feeds it to the parser of the *real* bound runnable of the *real*
# provider tier client (``.last`` of what ``with_structured_output`` returns —
# the same accessor ``tests/test_structured_output_method.py`` reads). So the
# exception escapes ``invoke`` at exactly the boundary a provider raises it
# from, and it is the exception that provider genuinely produces rather than
# one the test chose. ``model_construct`` — the other route Task 6.4 allows —
# bypasses validation and hands the helper an object no provider could have
# produced, so it lands somewhere ELSE. That difference is worth pinning rather
# than picking one and forgetting the other, so it has its own test below.
#
# **Spec 041, Task 12.2a: the fake used to call ``schema.model_validate``
# itself, and that one shortcut is why this module could not see the defect.**
# ``model_validate`` raises ``ValidationError``, which is what the RETIRED
# tool-use transport raised. The shipped local transport binds
# ``PydanticOutputParser``, which catches that and re-raises
# ``OutputParserException`` — a type ``_generate_names`` did not name, so the
# roster control's "this RETURNS, therefore the narrow clause caught it"
# reasoning was true of a transport the project no longer runs. The fake now
# takes a :class:`_ParseBoundary`, and both controls run once per boundary.
# Slice 3's Task 3.5 deliberate-break check could not have caught this either:
# it broke the *method kwarg*, not the *exception type at the parse boundary*.
#
# **Where each control lands, because it is not where a reader would guess:**
#
# - ``DayAction``'s lands in ``_ai_day_action``'s ``except Exception``, NOT in
#   ``_accept``. ``_accept`` never sees it — parsing failed before any
#   acceptance check could run, so no ``DayAction`` object ever existed. Pinned
#   by ``fake.parsed == []``. Being broad, that clause catches either boundary's
#   exception, which is exactly why ``DayAction`` never broke.
# - ``Roster``'s is caught by ``_generate_names``' NARROWER
#   ``except setup._UNUSABLE_ANSWER_ERRORS`` — the one clause among the seven
#   helpers that does not catch broadly. That this control RETURNS at all, where
#   :func:`test_a_roster_transport_failure_propagates_and_books_no_substitution`
#   propagates, is itself the proof of which clause caught it — and running it
#   on BOTH boundaries is what makes that proof cover the transport in use.
#
# Both still book exactly one substitution, through the fallback ``return``
# where Task 6.2 put the notification — and both assert the count AND the
# returned value, for Task 6.3's reason: either alone is vacuous.


@dataclass(frozen=True)
class _GapCase:
    """One payload the JSON-Schema grammar accepts and the Pydantic model refuses.

    ``expected_required`` is carried as data rather than left implicit because
    it is the mechanism, not a detail: ``DayAction``'s ``["kind"]`` is precisely
    why ``{"kind": "vote"}`` is grammar-legal, and ``Roster``'s ``["names"]``
    with no ``uniqueItems`` anywhere is why a case-variant pair is. If somebody
    later expresses either rule IN the schema — a ``uniqueItems``, a
    ``oneOf`` — this field is where that shows up as a readable diff, and the
    ``jsonschema.validate`` line below is where it turns into a failure that
    says "the gap closed" rather than a silent change of meaning.
    """

    label: str
    schema: type[BaseModel]
    payload: dict[str, Any]
    expected_required: tuple[str, ...]
    validator_message: str
    tier: str
    """Which capability tier the production call site binds this schema on.

    Carried so :class:`_GrammarConstrainedFake` builds its parser from the same
    tier client the game would — ``get_small`` for ``Roster``, ``get_large`` for
    ``DayAction``. The parser is in fact tier-independent (the two tiers differ
    only in model id and temperature), so this buys faithfulness rather than
    behaviour; naming it is cheaper than a comment explaining why a mismatch
    would not matter, and it stops mattering silently if a provider ever varies
    its binding by tier.
    """


_GAP_CASES = (
    _GapCase(
        label="day_action",
        schema=DayAction,
        # Grammar-legal: only ``kind`` is required, so a vote with no target is
        # a schema-valid document. Model-invalid: the mutual-exclusion rule
        # lives in ``_check_kind``, a ``@model_validator``.
        payload={"kind": "vote"},
        expected_required=("kind",),
        validator_message="vote requires non-empty target_id",
        tier="large",
    ),
    _GapCase(
        label="roster",
        schema=Roster,
        # Grammar-legal: ``names`` is an array of strings with a length range
        # and no distinctness constraint of any kind. Model-invalid: the
        # case-insensitive rule lives in ``_distinct_nonempty``, a
        # ``@field_validator``.
        payload={"names": ["Ava", "ava"]},
        expected_required=("names",),
        validator_message="names must be distinct (case-insensitive)",
        tier="small",
    ),
)


def _local_grammar_reply(
    schema: type[BaseModel], payload: dict[str, Any]
) -> AIMessage:
    """What ADR 013's grammar-constrained decoder hands back: a JSON document.

    ``PydanticOutputParser`` reads the message *content*, so the payload is
    serialised into it. This is the only faithful shape for that boundary — a
    tool call would be parsed by nothing on this path.
    """
    return AIMessage(content=json.dumps(payload))


def _cloud_tool_use_reply(
    schema: type[BaseModel], payload: dict[str, Any]
) -> AIMessage:
    """What Bedrock Converse's tool-use binding hands back: a tool call.

    ``PydanticToolsParser`` reads ``AIMessage.tool_calls`` and matches on the
    tool NAME, which LangChain derives from the schema class name — so the name
    is taken from ``schema`` rather than written down, and a schema rename
    cannot leave this fake matching a tool nobody declared.
    """
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": schema.__name__,
                "args": payload,
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )


@dataclass(frozen=True)
class _ParseBoundary:
    """One provider's REAL structured-output parse boundary.

    Spec 041, Task 12.2a. The parse boundary is a fact about the TRANSPORT, and
    Graphia ships two: the local Ollama provider pins ``method="json_schema"``
    (ADR 013) and binds ``PydanticOutputParser``; the Bedrock providers request
    no method and get LangChain's tool-use default, ``PydanticToolsParser``.
    Those two parsers raise **different, unrelated** exception types, and that
    is the entire content of the defect this dataclass exists to keep visible.

    ``provider`` is the real production provider class, not a stand-in:
    :class:`_GrammarConstrainedFake` asks it for a tier client and takes the
    parser off the runnable ``with_structured_output`` actually returns, so the
    exception under test is produced by shipped code rather than named by the
    test. Both constructions are strictly offline — ``ChatOllama`` has
    ``validate_model_on_init=False`` and ``ChatBedrockConverse.__init__``
    performs no call and needs no credentials (both pinned in
    ``tests/test_llm_provider_construction.py``).

    ``raises`` is the CLAIM, and it is the one thing here that is written down
    rather than derived — deliberately, because "whatever the parser raises is
    what we expect" would be unfalsifiable. It is checked against the real
    parser's real exception in every case below, and
    :func:`test_the_two_parse_boundaries_raise_unrelated_types_and_both_are_caught`
    checks the pair against the production tuple.
    """

    label: str
    provider: Callable[[], Any]
    reply: Callable[[type[BaseModel], dict[str, Any]], AIMessage]
    raises: type[Exception]


_PARSE_BOUNDARIES = (
    _ParseBoundary(
        label="local_grammar",
        provider=OllamaProvider,
        reply=_local_grammar_reply,
        raises=OutputParserException,
    ),
    _ParseBoundary(
        label="cloud_tool_use",
        provider=BedrockProvider,
        reply=_cloud_tool_use_reply,
        raises=ValidationError,
    ),
)

_boundary_sweep = pytest.mark.parametrize(
    "boundary", _PARSE_BOUNDARIES, ids=lambda b: b.label
)


def _gap_case(label: str) -> _GapCase:
    """The :data:`_GAP_CASES` entry named ``label`` — one declaration, three readers.

    The two controls each drive ONE case, while
    :func:`test_the_grammar_accepts_the_payload_the_validator_rejects` sweeps
    both. Looking the case up rather than re-typing its payload is what keeps
    the grammar proof and the substitution proof talking about the same
    document: a payload edited in ``_GAP_CASES`` alone would otherwise leave a
    control quietly exercising a different one.
    """
    for case in _GAP_CASES:
        if case.label == label:
            return case
    raise AssertionError(  # pragma: no cover - a test-suite defect
        f"no _GapCase labelled {label!r}; _GAP_CASES has "
        f"{[c.label for c in _GAP_CASES]}."
    )


class _GrammarConstrainedFake:
    """A tier stand-in that returns a raw grammar-valid payload to the REAL parser.

    The delivery mechanism these controls need, and the reason they cannot be
    written with an instance. It models the real path one step at a time:
    grammar-constrained decoding emits a JSON document that satisfies
    ``model_json_schema()``; the structured-output runnable then hands that
    document to the parser the provider bound; and the model's validators —
    which the grammar could not express — reject it. So the real parser is
    invoked INSIDE :meth:`invoke`, and its exception escapes from exactly the
    boundary a provider raises it from.

    **The parser is obtained, never chosen** (spec 041, Task 12.2a). The fake
    asks ``boundary.provider()`` for the case's tier client, calls the
    production ``with_structured_output`` on it — so the provider merges in its
    own defaults, ``method="json_schema"`` locally and nothing on Bedrock — and
    keeps ``.last``, the parser step of the returned sequence. Before this, the
    fake called ``schema.model_validate`` itself, which raises the exception the
    *retired* tool-use transport raised, and that is precisely how a fatal
    regression on the shipped transport left this module green.

    Every invocation replays the same payload, for :class:`_FailingModel`'s
    reason: ``_ai_day_action`` and ``_generate_names`` each invoke twice for one
    answer, and a fake that succeeded on the second would never substitute.

    ``parsed`` and ``raised`` are the route evidence. ``parsed == []`` says no
    model object ever existed, which is how the control proves ``_accept`` was
    never consulted; ``raised`` says what kind of exception the helper's
    ``except`` clause actually had to catch, which is what separates
    ``_generate_names``' narrow clause from a broad one.
    """

    def __init__(
        self, payload: dict[str, Any], boundary: _ParseBoundary, tier: str
    ) -> None:
        self._payload = payload
        self._boundary = boundary
        self._tier = tier
        self._schema: type[BaseModel] | None = None
        self._parser: Any = None
        self.invocations = 0
        self.bound_schemas: list[type] = []
        self.parsed: list[Any] = []
        self.raised: list[Exception] = []

    def with_structured_output(
        self, schema: type, **kwargs: Any
    ) -> "_GrammarConstrainedFake":
        self.bound_schemas.append(schema)
        self._schema = schema
        # The production binding, on the production client, with the caller's
        # own kwargs passed through — so the provider's structured-output
        # defaults are merged exactly as they are in a game. ``.last`` is the
        # parser step (``.first`` is the client that would have made the call).
        tier_client = getattr(self._boundary.provider(), self._tier)()
        self._parser = tier_client.with_structured_output(schema, **kwargs).last
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        if self._schema is None or self._parser is None:  # pragma: no cover
            pytest.fail(
                "_GrammarConstrainedFake.invoke was reached without a bound "
                "schema, so there is no real parser to hand the payload to.",
                pytrace=False,
            )
        reply = self._boundary.reply(self._schema, self._payload)
        try:
            value = self._parser.invoke(reply)
        except Exception as exc:
            self.raised.append(exc)
            raise
        self.parsed.append(value)
        return value


class _ChainingSink(_RecordingSink):
    """Records ``(event, kind)`` AND delegates to ``default_sink``.

    Task 6.1 kept ``default_sink`` public for exactly this: a counting sink can
    keep the log line instead of reproducing its message. The two controls use
    it so that "``note_substitution`` owns the log line, so a count and a log
    can never disagree" is checked at the site where a substitution is hardest
    to see from outside — nothing in the returned fallback, the log text or the
    count distinguishes a validator rejection from a transport failure, so all
    three are pinned together here rather than in three separate tests.

    The plain :class:`_RecordingSink` cannot serve: it displaces
    ``default_sink`` entirely, so a ``caplog`` assertion under it would be
    asserting the absence of a record the fixture itself suppressed.
    """

    def __call__(self, event: SubstitutionEvent, kind: SubstitutionKind) -> None:
        super().__call__(event, kind)
        default_sink(event, kind)


@pytest.fixture
def chaining_sink() -> _ChainingSink:
    """Install a :class:`_ChainingSink`; teardown is :func:`sink_reset`'s job."""
    recorder = _ChainingSink()
    set_sink(recorder)
    return recorder


def _substitution_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Only ``graphia.substitution``'s records — trap (d), applied uniformly.

    Task 6.3's warning: ``_ai_day_action``'s sibling helpers emit their own
    records on these same paths (``_ai_diary``'s ``logger.warning`` /
    ``logger.exception`` on ``graphia.nodes.day``), and third-party libraries
    on the parse path may emit more. A bare ``len(caplog.records) == 1`` would
    therefore be true today only by accident and would fail confusingly the
    first time anything else logged. Filtering by name states which logger the
    assertion is about.
    """
    return [r for r in caplog.records if r.name == "graphia.substitution"]


@pytest.mark.parametrize("case", _GAP_CASES, ids=lambda c: c.label)
def test_the_grammar_accepts_the_payload_the_validator_rejects(
    case: _GapCase,
) -> None:
    """The whole argument, in two steps: the grammar allows it, Pydantic does not.

    ``model_json_schema()`` is not a stand-in for the grammar here — it IS the
    grammar. ADR 013's route hands that exact document to Ollama as ``format``,
    and ``tests/test_structured_output_method.py`` pins that wiring
    (``format == Schema.model_json_schema()``, no ``tools``). So
    ``jsonschema.validate`` passing on this payload says the constrained decoder
    could emit it, and ``pytest.raises(ValidationError)`` on the next line says
    the application would then throw it away. Between those two lines sits the
    substitution path that the two controls below walk.

    This test is what makes those controls evidence rather than decoration. It
    also fails in the RIGHT direction if the gap is ever closed: expressing
    either rule in JSON Schema turns ``jsonschema.validate`` red here, at a
    test whose name says what changed, instead of leaving two controls quietly
    exercising an unreachable branch.
    """
    schema = case.schema.model_json_schema()

    # The grammar's own view of the rule: it is not there.
    assert tuple(schema.get("required", ())) == case.expected_required
    jsonschema.validate(case.payload, schema)  # raises if the grammar refuses

    # The application's view: it is there, and it is fatal.
    with pytest.raises(ValidationError) as excinfo:
        case.schema(**case.payload)
    assert case.validator_message in str(excinfo.value)


@_boundary_sweep
def test_a_grammar_valid_day_action_with_no_target_books_exactly_one(
    boundary: _ParseBoundary,
    chaining_sink: _ChainingSink,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control one: ``{"kind": "vote"}`` substitutes once, and says so once.

    The route is the part worth reading. ``_accept`` — the function that exists
    to reject an unusable vote — is never consulted, because the parser raised
    before any object existed for it to inspect (``fake.parsed == []``,
    ``fake.raised`` two of the boundary's exception). The payload lands in
    ``_ai_day_action``'s broad ``except Exception`` instead, on both
    invocations, and converges on the single fallback ``return``.

    So a reader auditing this class of failure by reading ``_accept`` would
    find nothing, and would conclude the path is unreachable under a grammar.
    It is reachable; this is the test that says so.

    Run once per :data:`_PARSE_BOUNDARIES` entry, and here that is anti-vacuity
    rather than caution: ``_ai_day_action`` catches broadly, so it survives
    either exception, and the value of the second case is that it proves the
    two boundaries genuinely differ under the *same* payload. That is the
    control for ``ROSTER``'s twin below, where the difference was fatal.

    Four assertions, Task 6.3's pairing: exactly one substitution, exactly one
    attempt, the known fallback value, and the model genuinely reached twice.
    Plus the log line, because the chaining sink keeps it.
    """
    case = _gap_case("day_action")
    fake = _GrammarConstrainedFake(case.payload, boundary, case.tier)
    monkeypatch.setattr("graphia.nodes.day.get_large", lambda: fake)
    state = _state()
    speaker = state["players"]["p-ava"]

    with caplog.at_level(logging.WARNING, logger="graphia.substitution"):
        result = day_nodes._ai_day_action(speaker, state)

    assert chaining_sink.substitutions() == [SubstitutionKind.DAY_ACTION]
    assert chaining_sink.attempts() == [SubstitutionKind.DAY_ACTION]
    assert result == DayAction(kind="speak", text=_DAY_ACTION_FALLBACK_TEXT)
    assert fake.invocations == 2
    assert fake.bound_schemas == [DayAction]

    # The route: parsing failed, so ``_accept`` had nothing to accept — and the
    # exception is the one THIS transport's real parser produced.
    assert fake.parsed == []
    assert [type(exc) for exc in fake.raised] == [boundary.raises] * 2

    records = _substitution_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "day_action" in records[0].getMessage()


@_boundary_sweep
def test_a_grammar_valid_roster_with_a_case_variant_books_exactly_one(
    boundary: _ParseBoundary,
    chaining_sink: _ChainingSink,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control two: ``["Ava", "ava"]`` substitutes once, and says so once.

    ``Roster``'s twin of the ``DayAction`` gap, and the reason the pair is worth
    having rather than one example: the missing rule is a ``@field_validator``
    here and a ``@model_validator`` there, so between them they cover both
    places a Pydantic rule can hide from JSON Schema.

    The route is the narrow one. ``_generate_names`` is the single helper of the
    seven whose ``except`` clauses name specific types rather than ``Exception``,
    so this control returning AT ALL — where
    :func:`test_a_roster_transport_failure_propagates_and_books_no_substitution`
    propagates a ``RuntimeError`` straight out — is itself the evidence that the
    narrow clause is what caught it.

    **This is the test the defect was hiding from** (spec 041, Task 12.2a). It
    exists in one shape per parse boundary now, and the ``local_grammar`` case
    is the one that was red before the production fix: on the shipped local
    transport the real parser raises ``OutputParserException``, the narrow
    clauses named only ``ValidationError``, and the exception left the node —
    no substitution booked, no roster returned, the game over. Measured at 1 of
    20 trials on ``qwen2.5:3b``. The ``cloud_tool_use`` case is what the old
    single-shape test was really asserting, and keeping it is what stops a
    future edit from dropping ``ValidationError`` while fixing the other half.

    The value is the clean all-placeholder end of the two-shape fallback: both
    invocations raised, so the first ``except`` cleared ``roster`` to ``None``
    and the second had nothing to put back, leaving ``_coerce_to_count(None, 3)``
    to fill every seat. The mixed shape has its own test above; naming which of
    the two this is keeps the expectation honest.
    """
    case = _gap_case("roster")
    fake = _GrammarConstrainedFake(case.payload, boundary, case.tier)
    monkeypatch.setattr("graphia.nodes.setup.get_small", lambda: fake)

    with caplog.at_level(logging.WARNING, logger="graphia.substitution"):
        result = setup_nodes._generate_names(_ROSTER_COUNT)

    assert chaining_sink.substitutions() == [SubstitutionKind.ROSTER]
    assert chaining_sink.attempts() == [SubstitutionKind.ROSTER]
    assert result == Roster(names=["Player-1", "Player-2", "Player-3"])
    assert fake.invocations == 2
    assert fake.bound_schemas == [Roster]

    # The route: the real parser's own exception on each invocation, caught by
    # the one narrow ``except`` in the seven — which is why this returns rather
    # than propagating. The second assertion is the anti-drift one: whatever
    # this transport's parser raises must be a member of the production tuple,
    # so a transport swap that changes the type cannot pass here again.
    assert fake.parsed == []
    assert [type(exc) for exc in fake.raised] == [boundary.raises] * 2
    assert all(
        isinstance(exc, setup_nodes._UNUSABLE_ANSWER_ERRORS) for exc in fake.raised
    )

    records = _substitution_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "roster" in records[0].getMessage()


def test_the_two_parse_boundaries_raise_unrelated_types_and_both_are_caught() -> None:
    """The one fact whose absence ended a measured game, asserted directly.

    Spec 041, Task 12.2a. Three claims, in the order they matter:

    1. **The two shipped transports raise DIFFERENT exception types** from their
       parse boundaries, produced here by the real provider clients' real
       parsers rather than named. If a future LangChain release collapses them,
       this goes red at a test whose name says what changed.
    2. **Neither type is a subclass of the other.** This is the load-bearing
       one: ``OutputParserException``'s MRO is ``OutputParserException ->
       ValueError -> LangChainException -> Exception``, so
       ``except ValidationError`` catches nothing on the local transport. The
       whole defect is contained in that sentence, and it was true and unstated
       for the entire life of ADR 013's swap.
    3. **``setup._UNUSABLE_ANSWER_ERRORS`` names both**, so ``_generate_names``
       recovers on either. Asserted against the exceptions the parsers actually
       raised, not against the tuple's contents — a tuple-equality assertion
       would keep passing after a transport swap changed what gets raised, which
       is exactly the failure mode being closed.

    Strictly offline: two vendor clients are constructed and neither is called.
    """
    raised: dict[str, Exception] = {}
    for boundary in _PARSE_BOUNDARIES:
        tier_client = boundary.provider().small()
        parser = tier_client.with_structured_output(Roster).last
        # Deliberately broad: the exception TYPE is the subject of this test,
        # so naming it in the ``raises`` would assert it before measuring it.
        with pytest.raises(Exception) as excinfo:  # noqa: BLE001
            parser.invoke(boundary.reply(Roster, {"names": ["Ava", "ava"]}))
        raised[boundary.label] = excinfo.value

    local = raised["local_grammar"]
    cloud = raised["cloud_tool_use"]

    # 1. Different types, from the real parsers.
    assert type(local) is not type(cloud)
    assert type(local) is OutputParserException
    assert type(cloud) is ValidationError

    # 2. Unrelated: neither clause would have caught the other's exception.
    assert not isinstance(local, ValidationError)
    assert not isinstance(cloud, OutputParserException)

    # 3. Both are in the production tuple, so both recover.
    assert isinstance(local, setup_nodes._UNUSABLE_ANSWER_ERRORS)
    assert isinstance(cloud, setup_nodes._UNUSABLE_ANSWER_ERRORS)


def test_a_validation_bypassed_day_action_lands_in_accept_and_still_books_one(
    sink: _RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterfactual delivery: ``model_construct`` takes the OTHER route.

    Task 6.4 allows two deliveries for an unconstructible payload — a parsing
    fake or ``model_construct`` — and they are not interchangeable, which is
    why this is a test rather than a sentence in a comment. ``model_construct``
    skips the validator, so a real ``DayAction`` instance carrying
    ``kind="vote"`` and ``target_id=None`` reaches the helper: ``_accept``'s
    ``isinstance`` check passes, and ``_accept`` itself does the rejecting,
    silently, with nothing raised anywhere. The control above walks the raising
    route; this one walks the silent one.

    Both converge on the same single ``note_substitution``, which is the
    property Task 6.2 chose the fallback ``return`` to get: four routes into one
    branch, counted once each. A notification placed in the two ``except``
    blocks would double-book the route above and MISS this one entirely — and
    "missed entirely" is the worse failure, because it reads in the ledger as a
    clean game.

    The parsing fake stays the primary delivery for the control because
    ``model_construct`` produces an object no grammar-constrained provider could
    hand over: the wire carries a document, not an instance.
    """
    bypassed = DayAction.model_construct(kind="vote")
    # Nothing was validated, so the instance holds a state its own validator
    # forbids — this is what makes it the other route rather than a duplicate.
    assert isinstance(bypassed, DayAction)
    assert bypassed.target_id is None

    model = _ScriptedModel({DayAction: bypassed})
    monkeypatch.setattr("graphia.nodes.day.get_large", lambda: model)
    state = _state()
    speaker = state["players"]["p-ava"]

    result = day_nodes._ai_day_action(speaker, state)

    assert sink.substitutions() == [SubstitutionKind.DAY_ACTION]
    assert sink.attempts() == [SubstitutionKind.DAY_ACTION]
    assert result == DayAction(kind="speak", text=_DAY_ACTION_FALLBACK_TEXT)
    assert result is not bypassed
    # Both invocations returned normally: the rejection was ``_accept``'s, not
    # an exception's.
    assert model.invocations == 2
    assert model.bound_schemas == [DayAction]


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
