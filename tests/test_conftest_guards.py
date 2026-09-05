"""Spec 041 Slice 5, Task 5.3 — the guards on ``tests/conftest.py``'s own fakes.

This module tests the *test suite*. Every other module here asserts something
about Graphia; this one asserts that the stand-ins the other 80 modules run
against cannot quietly substitute for the thing they stand in for. Slice 5's
value statement is "the offline suite stops being able to pass while silently
measuring the fallback", and the mechanisms Tasks 5.1 and 5.2 built to deliver
that — the recorded call, the kwarg allowlist, the violations list, the
teardown tripwire, and the unscripted defaults — had no coverage of their own
until this file.

Why the never-primed default is FIRST and not last
--------------------------------------------------

Task 5.1 measured the hole rather than guessing at it (2026-09-05): it reverted
its never-primed branch to the old ``AssertionError`` and ran all 80 modules —
**2109 passed, 1 skipped, nothing red**. The committed suite was therefore
completely indifferent to whether an unprimed queue serves a value or raises
into a node that swallows it. That is precisely the hazard Slice 5 exists to
close, and it was sitting unguarded inside the slice closing it.
:func:`test_a_never_primed_diary_queue_serves_the_recognisable_default` and its
two siblings are its only guard, so they lead the file.

The two axes, and why both
--------------------------

An unscripted default is recognisable by **text** (every text-carrying field
holds :data:`conftest.UNSCRIPTED_MARKER`) and by **identity** (the table's
values are module-level singletons). Both are asserted, because neither covers
the whole table on its own: ``Ballot``'s entire payload is one bool, so identity
is the only axis it has; and the text axis is the only one that survives the
node's ``_clamp_diary_entry``, which necessarily builds a new string on its way
into the delta.

Anti-vacuity
------------

Every guard here was observed failing under a deliberate mutation of
``tests/conftest.py`` before being committed (Task 5.3's requirement, since a
test written against already-correct code goes green on arrival and proves
nothing by doing so). Measured 2026-09-05, from 32 green:

* **Never-primed branch reverted to ``raise AssertionError``** — the mutation
  Task 5.1 measured the whole committed suite as green under: **7 red** here
  (the primary test plus all six identity cases), while
  ``test_slice39_diary_*`` — 138 tests, the modules closest to the hazard —
  stayed entirely green. That contrast is this file's reason to exist.
* ``FakeLargeDay`` given back a keyword-free signature and ``_PooledSmall``
  made to drop the kwargs from its recorded call: **4 red**, two cases of each
  sweep.
* The ``structured_output_violations.append`` removed from beside the
  unknown-schema ``pytest.fail``: **2 red**. Separately, the tripwire made to
  drain without failing: **1 red**, the teardown test alone — the two halves of
  Task 5.2 fail independently.
* ``include_raw`` and ``strict`` added to the allowlist: **10 red**.
* A ninth binder class added to ``conftest.py`` and left out of ``_BINDERS``:
  **1 red**, the exhaustiveness guard alone.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Iterator

import pytest

import conftest
from conftest import (
    STRUCTURED_OUTPUT_KWARG_ALLOWLIST,
    UNSCRIPTED_DEFAULTS,
    UNSCRIPTED_MARKER,
    FakeLarge,
    FakeLargeDay,
    FakeLargeUnified,
    FakeSmall,
    _DynamicNightPointing,
    _LoudFailureLLM,
    _PooledSmall,
    _TargetHumanPointing,
    drain_structured_output_violations,
)
from graphia.llm import Ballot, DayAction, Diary, Persona, Pointing, Reflection
from graphia.nodes.day import (
    _DIARY_FALLBACK,
    _REFLECTION_FALLBACK,
    day_diary,
)
from graphia.state import GameState, PlayerState

# ==========================================================================
# Shared hand-built helpers
# ==========================================================================

# The exact body a never-primed ``Diary`` queue serves, as a LITERAL rather than
# a call to ``conftest._unscripted_text``. Pinned this way on purpose: a test
# that renders its expectation with the same helper the code under test uses is
# vacuous the moment that helper changes, which is the recurring hazard
# ``tests/test_slice39_diary_before_night.py``'s docstring names. Cross-checked
# against the table in
# :func:`test_the_unscripted_default_is_recognisable_on_both_axes`, so a
# deliberate reword is one visible edit here and a silent drift is a red test.
EXPECTED_DIARY_DEFAULT_TEXT = (
    "[UNSCRIPTED-FAKE-DEFAULT: no Diary was scripted for this test, so "
    "FakeLargeUnified served its default]"
)


class _UnknownSchema:
    """A schema class no fake has a queue for — the "new call site" case.

    Deliberately not a Pydantic model and deliberately not in
    ``graphia.llm.GAMEPLAY_SCHEMAS``: the fake dispatches on the class object
    alone, so nothing about the shape matters, and keeping it local means this
    module cannot accidentally start passing because someone added a queue for
    a real schema.
    """


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


def _day_close_state(**overrides: Any) -> GameState:
    """A state positioned at the Day→Night hinge, where ``day_diary`` runs.

    Three surviving AI writers and one human, insertion-ordered, mirroring
    ``tests/test_slice39_diary_before_night.py::_mixed_table`` — so the fan-out
    below is exactly three entries and ``unscripted_defaults_served`` can be
    pinned to an exact list rather than a length.
    """
    state: GameState = {
        "cycle": 3,
        "phase": "day",
        "players": {
            "p-ava": _player("p-ava", "Ava", "law_abiding"),
            "p-ben": _player("p-ben", "Ben", "law_abiding"),
            "p-mara": _player("p-mara", "Mara", "mafia"),
            "p-human": _player("p-human", "Hugo", "law_abiding", is_human=True),
        },
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


def _entries_by_player(delta: dict) -> dict[str, list[str]]:
    """``{player id: [entry text, …]}`` from a ``day_diary`` delta."""
    return {
        pid: [record["text"] for record in records]
        for pid, records in delta["private_diaries"].items()
    }


@pytest.fixture
def clean_violations() -> Iterator[None]:
    """Drain :data:`conftest.structured_output_violations` at BOTH ends.

    The contract stated in ``drain_structured_output_violations``' docstring,
    packaged: a test that provokes a violation on purpose must leave the list
    empty or the autouse ``structured_output_tripwire`` fails it. Draining on
    the way in as well guarantees the assertions see only this test's own
    entries, and draining on the way out covers the tests below that assert on
    a violation *count* and would otherwise leak on an early failure.
    """
    drain_structured_output_violations()
    yield
    drain_structured_output_violations()


# ==========================================================================
# THE PRIMARY GUARD (Task 5.3, fourth bullet) — a never-primed queue serves
# the recognisable default, and NOT the node's substituted value.
#
# Measured hole: with the never-primed branch reverted to ``AssertionError``,
# all 80 committed modules stayed green (Task 5.1, 2026-09-05). These three
# tests are what make that mutation red.
# ==========================================================================


def test_a_never_primed_diary_queue_serves_the_recognisable_default(
    fake_large: Callable[..., FakeLargeUnified],
) -> None:
    """``day_diary`` with no ``diaries=`` writes the DEFAULT, not ``_DIARY_FALLBACK``.

    The whole hazard in one test. ``_ai_diary`` wraps its ``invoke`` in
    ``try/except Exception`` and substitutes ``_DIARY_FALLBACK`` on anything
    that escapes, so before Task 5.1 an unprimed queue's ``AssertionError`` was
    eaten by the node under test and every downstream assertion was made
    against a constant string the fake never produced — while the test passed.
    Both worlds produce a non-empty entry for every writer, so "entries exist"
    is true in each and cannot be the detector. The text is.

    ``day_actions=[]`` is passed and ``diaries=`` is not, which is the shape of
    the several dozen suite tests that drive a full Day without caring what any
    player wrote in its diary — the exact population that used to measure the
    substitution silently.

    Four independent facts, because no one of them alone is conclusive:

    * every writer's entry is the unscripted default TEXT (the substitution
      world fails here);
    * no entry is ``_DIARY_FALLBACK``, by value and by identity (the direct
      inverse — stated separately so a diff reads unambiguously);
    * ``unscripted_defaults_served == [Diary, Diary, Diary]`` — the fake's own
      record of having defaulted, once per writer, which is the axis that would
      still discriminate if the default text were ever changed to something
      plausible;
    * ``calls_by_schema[Diary] == 3`` — the queue was genuinely REACHED. An
      unknown-schema failure never gets as far as ``_invoke``, so this counter
      separates "defaulted" from "never arrived".
    """
    fake = fake_large(day_actions=[])

    entries = _entries_by_player(day_diary(_day_close_state()))

    assert set(entries) == {"p-ava", "p-ben", "p-mara"}
    assert entries == {
        "p-ava": [EXPECTED_DIARY_DEFAULT_TEXT],
        "p-ben": [EXPECTED_DIARY_DEFAULT_TEXT],
        "p-mara": [EXPECTED_DIARY_DEFAULT_TEXT],
    }

    written = [text for texts in entries.values() for text in texts]
    assert all(text != _DIARY_FALLBACK for text in written)
    assert all(text is not _DIARY_FALLBACK for text in written)
    assert all(UNSCRIPTED_MARKER in text for text in written)

    assert fake.unscripted_defaults_served == [Diary, Diary, Diary]
    assert fake.calls_by_schema[Diary] == 3


@pytest.mark.parametrize(
    "schema",
    [DayAction, Ballot, Pointing, Persona, Reflection, Diary],
    ids=lambda s: s.__name__,
)
def test_a_never_primed_queue_serves_the_singleton_itself(schema: type) -> None:
    """For every schema in the table, the served value IS the table's object.

    The IDENTITY axis, swept across the whole table. It is the only axis
    ``Ballot`` has — its entire payload is one bool, so ``yes=False`` is
    indistinguishable by value from a scripted "No" and from ``_ai_ballot``'s
    own conservative fallback — and it is the axis that keeps discriminating if
    a marker text is ever reworded.

    Driven against a bare ``FakeLargeUnified`` rather than through a node, so
    the assertion is on the object the fake handed back, before any node has
    clamped, coerced or copied it. ``unscripted_defaults_served`` is checked in
    the same breath: the singleton could in principle be returned by a path
    that forgot to record, and a silent recording gap would blind the primary
    test above.
    """
    fake = FakeLargeUnified()

    value = fake.with_structured_output(schema).invoke([])

    assert value is UNSCRIPTED_DEFAULTS[schema]
    assert fake.unscripted_defaults_served == [schema]
    assert fake.calls_by_schema[schema] == 1
    assert drain_structured_output_violations() == []


def test_the_unscripted_default_is_recognisable_on_both_axes() -> None:
    """The default cannot be confused with a scripted value or a node fallback.

    "Recognisable" is a claim about the *contents* of the table, and nothing
    else in the suite checks it. Three parts:

    * the ``Diary`` default carries the literal this module pins, so
      :data:`EXPECTED_DIARY_DEFAULT_TEXT` above cannot drift away from the
      table it mirrors;
    * every text-carrying default carries :data:`conftest.UNSCRIPTED_MARKER`,
      swept over the table rather than spot-checked, so a seventh entry added
      without the marker is red here;
    * no production fallback carries the marker. That is the half that makes
      the primary test's discriminator sound: if ``_DIARY_FALLBACK`` ever
      acquired the marker text, "the marker is present" would stop
      distinguishing the two worlds.
    """
    assert UNSCRIPTED_DEFAULTS[Diary].entry == EXPECTED_DIARY_DEFAULT_TEXT

    # Keyed over the WHOLE table, so a seventh entry added without a marker is
    # red here — but naming the fields explicitly rather than sweeping every
    # string on the model, because two strings legitimately carry no marker:
    # ``DayAction.kind`` is a discriminator ("speak"), and
    # ``Persona.secret_backstory`` defaults to "". A blanket sweep would have to
    # be weakened to tolerate those and would then tolerate a body field too.
    marker_bearing: dict[type, tuple[str, ...]] = {
        DayAction: ("text",),
        Ballot: (),
        Pointing: ("target_id",),
        Persona: ("personality", "manner", "public_backstory"),
        Reflection: ("thought",),
        Diary: ("entry",),
    }
    assert set(marker_bearing) == set(UNSCRIPTED_DEFAULTS)
    for schema, fields in marker_bearing.items():
        for field in fields:
            text = getattr(UNSCRIPTED_DEFAULTS[schema], field)
            assert UNSCRIPTED_MARKER in text, (schema.__name__, field, text)

    assert UNSCRIPTED_MARKER not in _DIARY_FALLBACK
    assert UNSCRIPTED_MARKER not in _REFLECTION_FALLBACK

    # ``Ballot``'s payload is one bool, so it carries no text at all — the
    # reason the identity sweep above exists. Pinned so "it has no marker" is a
    # recorded fact rather than an oversight.
    assert UNSCRIPTED_DEFAULTS[Ballot].yes is False


# ==========================================================================
# THE HEADLINE SWEEP (Task 5.3, first bullet) — every schema-binding class in
# conftest accepts the kwarg the provider seam injects, records it, and books
# no violation for it.
# ==========================================================================


# Every class in ``tests/conftest.py`` that binds a schema, with a
# minimally-constructed instance of each. Kept as (name, factory) pairs rather
# than as classes so the parametrize ids read as the class names and so each
# constructor's own arguments stay visible here.
#
# ``_LoudFailureLLM`` IS IN THE SWEEP, and the decision is deliberate. It is not
# a fake — it is the autouse net ``safe_llm`` installs on all five tier bindings
# — but it binds schemas through exactly the same
# ``_record_structured_output_call`` contract, and it is the binder that the
# largest number of tests in the suite actually reach (every test that installs
# no per-test fixture). A keyword-only signature there would be the widest
# breakage of the eight, not the narrowest, so excluding it would leave the
# sweep's own exhaustiveness guard needing an exemption list. What makes it
# different — that it is still loud at ``invoke`` — is pinned separately in
# :func:`test_the_loud_failure_net_is_still_loud_at_invoke`, so "does not raise
# at binding" cannot be misread as the net having softened.
_BINDERS: tuple[tuple[str, Callable[[], Any]], ...] = (
    ("FakeSmall", lambda: FakeSmall([])),
    ("_PooledSmall", lambda: _PooledSmall([])),
    ("FakeLarge", lambda: FakeLarge([])),
    ("FakeLargeDay", lambda: FakeLargeDay([])),
    ("FakeLargeUnified", lambda: FakeLargeUnified()),
    ("_DynamicNightPointing", lambda: _DynamicNightPointing(lambda: {})),
    ("_TargetHumanPointing", lambda: _TargetHumanPointing(lambda: {})),
    ("_LoudFailureLLM", lambda: _LoudFailureLLM("probe")),
)

_BINDER_IDS = [name for name, _ in _BINDERS]


@pytest.mark.parametrize("factory", [f for _, f in _BINDERS], ids=_BINDER_IDS)
def test_every_fake_accepts_and_records_the_provider_seams_method_kwarg(
    factory: Callable[[], Any],
) -> None:
    """``with_structured_output(Pointing, method="json_schema")`` is accepted everywhere.

    THE test this suite lacked. ``graphia.llm.StructuredMethodModel`` merges the
    active provider's ``structured_output_defaults()`` into every bind, and the
    local Ollama path's is ``{"method": "json_schema"}`` (ADR 013). The fakes
    stand in for a client that accepts that; until Task 5.1 they took a
    keyword-free signature, so the first caller kwarg to reach one would have
    been a ``TypeError`` from the stand-in rather than anything the real model
    would ever do — and nothing in the suite would have said so until a
    production change routed the seam through a fake.

    Three separate facts, none of which implies the others:

    * the call does not raise — the fake is at least as permissive as the model;
    * the kwarg is on the recorded call — the fake did not silently drop it, so
      a test asserting what the seam handed a fake is reading the truth;
    * no violation is booked — ``method`` is in the allowlist, so the tripwire
      stays quiet on the one kwarg production legitimately injects.

    ``Pointing`` rather than ``Diary`` because it is the one gameplay schema
    every binder here can serve: it is in ``FakeLargeUnified``'s queue map, and
    the six single-purpose fakes ignore the bound class entirely.
    """
    fake = factory()

    bound = fake.with_structured_output(Pointing, method="json_schema")

    assert bound is not None
    # The runnable contract every production call site relies on:
    # ``get_large().with_structured_output(X).invoke(msgs)``.
    assert hasattr(bound, "invoke")
    assert fake.structured_calls == [(Pointing, {"method": "json_schema"})]
    assert drain_structured_output_violations() == []


def test_the_headline_sweep_covers_every_binder_in_conftest() -> None:
    """No class in ``tests/conftest.py`` binds a schema without being swept.

    The sweep above is a hand-written list, so it can go stale in the one
    direction that matters: an EIGHTH (ninth, …) fake added later, with the
    keyword-only signature this slice exists to prevent, would simply not be
    swept and the sweep would stay green. Discovering the binders by
    introspection and comparing the two sets makes that a red test at the
    moment the class is added.

    ``__dict__`` rather than ``hasattr`` so an inherited method is not counted
    twice — each class must define its own binding to be a binder here.
    """
    discovered = {
        name
        for name, obj in vars(conftest).items()
        if inspect.isclass(obj) and "with_structured_output" in obj.__dict__
    }

    assert discovered == set(_BINDER_IDS)


def test_the_loud_failure_net_is_still_loud_at_invoke() -> None:
    """``_LoudFailureLLM`` binds quietly and then fails, which is the whole design.

    The companion to including it in the headline sweep. Binding must be silent
    (a legitimate test may construct a tier client it never calls), and the
    failure must arrive at ``invoke`` with the pointer to the right fixture.
    Without this, "the net does not raise on bind" would be indistinguishable
    from "the net stopped raising".
    """
    net = _LoudFailureLLM("graphia.nodes.day.get_large")

    bound = net.with_structured_output(Diary, method="json_schema")

    assert bound is net
    with pytest.raises(RuntimeError, match="Unstubbed LLM call"):
        bound.invoke([])
    assert net.structured_calls == [(Diary, {"method": "json_schema"})]
    assert drain_structured_output_violations() == []


# ==========================================================================
# THE UNKNOWN SCHEMA (Task 5.3, second bullet) — a swallow does not save it.
# ==========================================================================


def test_an_unknown_schema_fails_on_a_non_Exception_and_books_a_violation(
    clean_violations: None,
) -> None:
    """Binding a schema no fake knows raises ``Failed``, which is not an ``Exception``.

    Task 5.1's split, at its source. The unknown-schema case is a test-suite
    defect (a structured-output call site nobody taught the fakes about), so it
    stops the test where it went wrong — but through ``pytest.fail(...,
    pytrace=False)`` rather than the old ``AssertionError``, because ``Failed``
    derives straight from ``BaseException`` and therefore walks through the four
    gameplay nodes' ``except Exception``.

    ``not isinstance(excinfo.value, Exception)`` is the load-bearing assertion:
    without it this test passes just as well against the ``AssertionError`` it
    replaced, which is exactly the version that was swallowable. The violation
    is booked as well as raised, and the message text is preserved verbatim from
    before Task 5.1 (``tests/test_slice39_diary_fake_coverage.py`` asserts on
    it), so both are checked.
    """
    fake = FakeLargeUnified()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        fake.with_structured_output(_UnknownSchema)

    assert not isinstance(excinfo.value, Exception)
    assert "no scripted queue for schema" in str(excinfo.value)

    violations = drain_structured_output_violations()
    assert len(violations) == 1, violations
    assert "no scripted queue for schema" in violations[0]


def test_a_swallowed_unknown_schema_still_fails_at_teardown(
    clean_violations: None,
) -> None:
    """Even an ``except BaseException`` cannot hide it — the tripwire still fires.

    The reason Task 5.2's recorded fact is not redundant with Task 5.1's
    ``pytest.fail``. ``Failed`` beats every ``except Exception`` in the
    codebase, but it does not beat an ``except BaseException`` somebody adds
    tomorrow, and it does not beat ``graphia.driver._producer``, which runs each
    graph super-step on an ``asyncio.to_thread`` worker that reports rather than
    re-raises. A booked entry survives both, and this test simulates the first
    of them literally.

    The tripwire fixture's own generator is driven here — setup, provoke,
    teardown — because "fails at teardown" is a property of that function and
    cannot be observed from inside a test it is wrapping. The failure message is
    asserted to NAME the violation, which is the fixture's stated job (a bare
    "something was booked" would send the developer nowhere), and the list is
    asserted empty afterwards because the fixture drains before deciding, so a
    violation is charged once and never to the next test as well.
    """
    tripwire = inspect.unwrap(conftest.structured_output_tripwire)()
    next(tripwire)

    try:
        FakeLargeUnified().with_structured_output(_UnknownSchema)
    except BaseException:  # noqa: BLE001 — the hypothetical broad swallow
        pass

    with pytest.raises(pytest.fail.Exception) as excinfo:
        next(tripwire)

    message = str(excinfo.value)
    assert "1 structured-output violation(s) were booked" in message
    assert "no scripted queue for schema" in message
    assert "_UnknownSchema" in message
    assert conftest.structured_output_violations == []


def test_a_missing_default_walks_through_the_nodes_except_exception(
    fake_large: Callable[..., FakeLargeUnified],
    monkeypatch: pytest.MonkeyPatch,
    clean_violations: None,
) -> None:
    """A queue with no ``UNSCRIPTED_DEFAULTS`` entry fails THROUGH the real node.

    The drift case between the fake's two hand-typed schema listings: a seventh
    queue added without a default. Before Task 5.1 that would have been a bare
    ``KeyError`` raised from inside ``_invoke`` — which is inside ``_ai_diary``'s
    ``try`` — so the node would have turned it into ``_DIARY_FALLBACK`` and the
    test would have passed while measuring the substitution.

    Driven through the production ``day_diary`` rather than against the fake
    directly, because that ``except Exception`` is the thing being beaten. This
    is the only test in the file that proves the ``BaseException`` property
    against a real swallowing node rather than a simulated one: the
    unknown-schema failure above happens at BIND time, and ``_ai_diary`` binds
    outside its ``try``, so only the never-primed branch exercises the swallow
    for real.

    ``monkeypatch.delitem`` restores the table afterwards, so the mutation
    cannot leak into another test.
    """
    fake_large(day_actions=[])
    monkeypatch.delitem(UNSCRIPTED_DEFAULTS, Diary)

    with pytest.raises(pytest.fail.Exception) as excinfo:
        day_diary(_day_close_state())

    assert "no UNSCRIPTED_DEFAULTS entry" in str(excinfo.value)

    violations = drain_structured_output_violations()
    # ONE, not three: the failure stops the fan-out at the first writer rather
    # than booking one per player.
    assert len(violations) == 1, violations
    assert "UNSCRIPTED_DEFAULTS" in violations[0]


# ==========================================================================
# THE UNEXPECTED KWARG (Task 5.3, third bullet) — recorded, not rejected.
# ==========================================================================


@pytest.mark.parametrize("factory", [f for _, f in _BINDERS], ids=_BINDER_IDS)
def test_an_out_of_allowlist_kwarg_is_recorded_as_a_violation(
    factory: Callable[[], Any], clean_violations: None
) -> None:
    """Every binder accepts ``include_raw``, records it, and books a violation.

    The half of Task 5.1's design that keeps Task 4.2's guarantee alive after
    the mechanism changed. Slice 4 withdrew the interim diary prose recovery and
    made a re-introduction a ``TypeError`` at the binding; Task 5.1 replaced
    that typed signature with ``**kwargs``, because a stand-in that rejects what
    the real model accepts is less faithful rather than safer. The loudness moved
    to the allowlist and got wider in the move: ANY unexpected kwarg, on ALL
    eight binders, as a recorded fact that survives a swallow.

    Both halves are asserted together and neither is sufficient. "Does not
    raise" alone would also pass if the kwarg were silently tolerated — which is
    precisely the trap a kept-but-inert parameter sets. "Books a violation"
    alone would also pass if it raised as well.

    The message must name the fake, the kwarg and the allowlist, because the
    tripwire prints it verbatim and it is the only thing the developer sees.
    """
    fake = factory()

    bound = fake.with_structured_output(Pointing, include_raw=True)

    assert bound is not None
    assert fake.structured_calls == [(Pointing, {"include_raw": True})]

    violations = drain_structured_output_violations()
    assert len(violations) == 1, violations
    assert type(fake).__name__ in violations[0]
    assert "include_raw" in violations[0]
    assert "STRUCTURED_OUTPUT_KWARG_ALLOWLIST" in violations[0]


def test_each_out_of_allowlist_kwarg_books_its_own_violation(
    clean_violations: None,
) -> None:
    """Two unexpected kwargs on one call book two violations, and ``method`` books none.

    The per-kwarg granularity, and the mixed call that proves the allowlist is
    consulted per name rather than per call. A whole-call check would either
    book one entry for a two-kwarg mistake (losing the second name from the
    tripwire's report) or book one for a call that also carried the legitimate
    ``method``.
    """
    fake = FakeLargeUnified()

    fake.with_structured_output(
        Pointing, method="json_schema", include_raw=True, strict=True
    )

    assert fake.structured_calls == [
        (Pointing, {"method": "json_schema", "include_raw": True, "strict": True})
    ]
    violations = drain_structured_output_violations()
    assert len(violations) == 2, violations
    assert any("was passed 'include_raw'" in text for text in violations)
    assert any("was passed 'strict'" in text for text in violations)
    # ``'method'`` appears in every message as part of the rendered allowlist,
    # so the negative has to be on the "was passed …" clause specifically.
    assert not any("was passed 'method'" in text for text in violations)


def test_the_allowlist_holds_method_and_nothing_else() -> None:
    """``method`` only — and ``include_raw`` explicitly not.

    The allowlist is what the two sweeps above are asserted against, so its
    contents are the one thing they cannot check. Pinned to an exact set rather
    than a membership test, so widening it is a deliberate edit against a red
    test: after Slice 4 no production call site passes ANY caller kwarg, and the
    provider seam injects ``method`` and nothing else (both Bedrock providers
    contribute ``{}``).
    """
    assert STRUCTURED_OUTPUT_KWARG_ALLOWLIST == frozenset({"method"})
    assert "include_raw" not in STRUCTURED_OUTPUT_KWARG_ALLOWLIST


def test_the_recorded_kwargs_are_a_copy_the_caller_cannot_rewrite() -> None:
    """A caller mutating its own kwargs dict afterwards cannot rewrite history.

    ``_record_structured_output_call`` stores ``dict(kwargs)`` for this reason.
    A caller that builds one options dict and reuses it across bindings — which
    is what a provider seam merging defaults looks like — would otherwise leave
    every recorded call pointing at the same, last-written mapping, and the two
    sweeps above would be asserting on whatever the final caller happened to
    pass.
    """
    fake = FakeLargeUnified()
    options = {"method": "json_schema"}

    fake.with_structured_output(Pointing, **options)
    options["method"] = "function_calling"

    assert fake.structured_calls == [(Pointing, {"method": "json_schema"})]
    assert drain_structured_output_violations() == []
