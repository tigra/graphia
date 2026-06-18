"""Offline unit tests for the shared structured-output instrumentation proxy
(spec 011, Slice 3, Task 3) — **without ever reaching a real model, the network,
or a live game**.

These round out the coverage Task 2 began (the three inline attribution tests in
``tests/test_blunder_eval_detectors.py``) by pinning the three things Slice 3
Task 3 requires of ``src/graphia/tools/instrument.py`` and the
``self_vote.initiation`` scorer:

A. **The shared proxy records raw payloads + speaker id.** A fake inner runnable
   returning scripted ``DayAction``s is wrapped in :class:`InstrumentedModel` with
   a ``captures`` list and a ``speaker_resolver``; each ``CaptureRecord`` carries
   the right ``schema`` / ``raw_result`` / resolved ``speaker_id``. Capture is
   shown to accrue ONLY when a ``captures`` list is supplied, and a non-``DayAction``
   schema (and a count-only proxy with no list) does not break.

B. **The detector counts safety-net-rejected attempts.** ``score_self_vote_initiation``
   over synthetic ``CaptureRecord``s built directly: a self-vote
   (``DayAction(kind="vote", target_id == speaker_id)`` — the case the GAME's
   ``_accept`` rejects before it reaches state) IS in the numerator, a vote on
   another is denominator-only, a ``kind="speak"`` is excluded entirely, and the
   all-absent case returns ``{rate: None, ...}``. This is the heart of the slice:
   the metric exists precisely to see attempts the game absorbs.

C. **``ollama_smoke``'s counting behaviour is unchanged** after the Task-1
   refactor. A regression pin on the count-only path: drive :class:`InstrumentedModel`
   in count-only mode (``stats``, no ``captures``, no resolver) over a scripted
   sequence of inner outcomes — successes, an exception, a non-instance result,
   and consecutive failures that must trip exactly one fallback — and assert the
   resulting :class:`SchemaStats` (attempts / failures / fallbacks / failure_rate)
   match the documented semantics ``ollama_smoke``'s verdict is built on.

Everything is built on the REAL classes/constants (imported), so a rename breaks
these tests; day-speak prompts use the REAL ``DAY_SPEAK_USER_TEMPLATE`` so a
template reword breaks attribution loudly. No provider client is ever
constructed and the autouse ``safe_llm`` net is left intact — these tests never
go near an LLM call site (the proxy is driven over a hand-built fake inner
runnable, not ``graphia.llm``).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from graphia.llm import DayAction, Ballot
from graphia.nodes.day import (
    _persona_block,
    _role_label,
    _team_line,
    _win_condition_line,
)
from graphia.prompts import DAY_SPEAK_SYSTEM, DAY_SPEAK_USER_TEMPLATE
from graphia.state import PlayerState
from graphia.tools.blunder_eval import (
    make_day_speaker_resolver,
    score_self_vote_initiation,
)
from graphia.tools.instrument import (
    CaptureRecord,
    InstrumentedModel,
    SchemaStats,
)


# ===========================================================================
# Shared offline scaffolding — no model, no network.
# ===========================================================================


def _player(
    pid: str,
    name: str,
    role: str = "law_abiding",
    is_human: bool = False,
) -> PlayerState:
    """A ``PlayerState`` built from the real dataclass (mirrors existing tests)."""
    return PlayerState(id=pid, name=name, role=role, is_human=is_human)


def _roster(*players: PlayerState) -> dict[str, PlayerState]:
    """The ``players`` map keyed by id, as the harness surfaces from a final state."""
    return {p.id: p for p in players}


def _day_prompt(speaker: PlayerState) -> list:
    """The messages ``day._ai_day_action`` builds for ``speaker``'s turn.

    Rendered from the REAL imported ``DAY_SPEAK_USER_TEMPLATE`` (opens
    "You are {speaker}."), so the resolver parses the same line production emits
    — a reword of that template breaks this attribution test loudly.
    """
    return [
        SystemMessage(content=DAY_SPEAK_SYSTEM),
        HumanMessage(
            content=DAY_SPEAK_USER_TEMPLATE.format(
                speaker=speaker.name,
                role_label=_role_label(speaker.role),
                win_condition=_win_condition_line(speaker.role),
                team_line=_team_line(speaker, {speaker.id: speaker}),
                persona=_persona_block(speaker),
                roster="(roster)",
                context="(ctx)",
            )
        ),
    ]


class _ScriptedStructured:
    """A ``with_structured_output(...)`` runnable: invoke yields the next outcome.

    Each scripted outcome is either a plain value (returned) or an ``Exception``
    INSTANCE (raised) — so one fake inner runnable can drive a full sequence of
    successes, parse failures (non-instance results), and raised invokes, exactly
    the mix ``ollama_smoke``'s counting path must handle.
    """

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0

    def invoke(self, *args: object, **kwargs: object) -> object:
        outcome = self._outcomes[self._i]
        self._i += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _ScriptedInner:
    """A fake tier client: each ``with_structured_output`` yields the next runnable.

    The inner the proxy wraps. Per-schema invoke outcomes are drained from a flat
    list in call order, so a sequence of Day turns (or one schema's run of
    successes/failures) is driven with NO model. Records the schema each
    ``with_structured_output`` was asked for so a test can confirm passthrough.
    """

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0
        self.requested_schemas: list[object] = []

    def with_structured_output(
        self, schema: object, **kwargs: object
    ) -> _ScriptedStructured:
        self.requested_schemas.append(schema)
        outcome = self._outcomes[self._i]
        self._i += 1
        return _ScriptedStructured([outcome])


# ===========================================================================
# A — the instrument captures raw payloads + speaker id (requirement 1).
# ===========================================================================


def test_capture_records_schema_raw_result_and_resolved_speaker() -> None:
    """Each ``CaptureRecord`` carries the schema, the raw inner result, and the id.

    Drive ``with_structured_output(DayAction).invoke(prompt)`` over a few turns
    with the REAL ``make_day_speaker_resolver``; every record's ``schema`` is the
    class asked for, its ``raw_result`` is the exact object the inner returned
    (identity, not equality — the payload is captured unmodified BEFORE any
    ``_accept`` validation), and its ``speaker_id`` is the speaker read off THAT
    invoke's prompt. Ids differ from names so a name-vs-id confusion would fail.
    """
    mira = _player("p-1", "Mira")
    bo = _player("p-2", "Bo", role="mafia")
    players = _roster(mira, bo)

    a0 = DayAction(kind="vote", target_id="p-1")
    a1 = DayAction(kind="speak", text="hmm.")
    inner = _ScriptedInner([a0, a1])
    captures: list[CaptureRecord] = []
    proxy = InstrumentedModel(
        inner,
        captures=captures,
        speaker_resolver=make_day_speaker_resolver(players),
    )

    r0 = proxy.with_structured_output(DayAction).invoke(_day_prompt(mira))
    r1 = proxy.with_structured_output(DayAction).invoke(_day_prompt(bo))

    # The proxy passes the inner's payload through unchanged (identity).
    assert r0 is a0
    assert r1 is a1
    # The schema asked for reaches the inner untouched.
    assert inner.requested_schemas == [DayAction, DayAction]

    assert len(captures) == 2
    # schema is the class asked for; raw_result is the EXACT inner object
    # (captured before any validation); speaker_id is read off this prompt.
    assert captures[0].schema is DayAction
    assert captures[0].raw_result is a0
    assert captures[0].speaker_id == "p-1"
    assert captures[1].schema is DayAction
    assert captures[1].raw_result is a1
    assert captures[1].speaker_id == "p-2"


def test_capture_speaker_id_is_none_for_a_non_day_speak_schema() -> None:
    """A non-Day-speak invoke (a ``Ballot`` prompt) captures with ``speaker_id=None``.

    The resolver matches only the ``"You are {speaker}."`` Day-speak line, so a
    ``Ballot`` invoke whose prompt carries no such line is captured (the raw
    payload is still recorded) but UNATTRIBUTED — the record exists, its
    ``speaker_id`` is ``None``, and the scorer later skips it. This proves a
    non-``DayAction`` schema does not break the capture path.
    """
    mira = _player("p-1", "Mira")
    ballot = Ballot(yes=True)
    inner = _ScriptedInner([ballot])
    captures: list[CaptureRecord] = []
    proxy = InstrumentedModel(
        inner,
        captures=captures,
        speaker_resolver=make_day_speaker_resolver(_roster(mira)),
    )

    proxy.with_structured_output(Ballot).invoke(
        [HumanMessage(content="Vote yes or no on executing Bo.")]
    )

    assert len(captures) == 1
    assert captures[0].schema is Ballot
    assert captures[0].raw_result is ballot
    assert captures[0].speaker_id is None


def test_capture_with_a_fixed_id_resolver_attributes_every_record() -> None:
    """A resolver that returns a fixed id attributes every capture to that id.

    The proxy is resolver-agnostic: it hands the resolver this invoke's messages
    and records whatever id comes back. A trivial fixed-id resolver therefore
    stamps every record with that id — confirming the proxy uses the resolver's
    return verbatim and does not itself parse the prompt.
    """
    inner = _ScriptedInner(
        [DayAction(kind="speak", text="one"), DayAction(kind="speak", text="two")]
    )
    captures: list[CaptureRecord] = []
    proxy = InstrumentedModel(
        inner,
        captures=captures,
        speaker_resolver=lambda _messages: "fixed-id",
    )

    proxy.with_structured_output(DayAction).invoke([HumanMessage(content="x")])
    proxy.with_structured_output(DayAction).invoke([HumanMessage(content="y")])

    assert [c.speaker_id for c in captures] == ["fixed-id", "fixed-id"]


def test_no_captures_list_means_no_capture_and_invoke_passes_through() -> None:
    """A count-only proxy (no ``captures`` list) records nothing and still returns.

    Capture accrues ONLY when a ``captures`` list is supplied — the orthogonality
    ``ollama_smoke`` relies on. With ``captures=None`` (the default) and a resolver
    that would raise if ever called, the invoke still passes the inner payload
    through and ``proxy.captures`` is ``None``: capture is wholly inert, and the
    speaker resolver is never consulted on the count-only path.
    """

    def _boom(_messages: object) -> str:
        raise AssertionError("resolver must not run when captures is None")

    inner = _ScriptedInner([DayAction(kind="vote", target_id="p-1")])
    proxy = InstrumentedModel(inner, speaker_resolver=_boom)  # no captures list

    result = proxy.with_structured_output(DayAction).invoke([HumanMessage(content="x")])

    assert isinstance(result, DayAction)
    assert proxy.captures is None


def test_captures_property_exposes_the_accumulating_list() -> None:
    """``InstrumentedModel.captures`` exposes the very list the proxy appends to."""
    captures: list[CaptureRecord] = []
    proxy = InstrumentedModel(_ScriptedInner([]), captures=captures)

    assert proxy.captures is captures


# ===========================================================================
# B — detector counts safety-net-rejected attempts (requirement 2).
#
# Build synthetic ``CaptureRecord``s DIRECTLY for the realistic case: a
# ``DayAction(kind="vote", target_id == speaker_id)`` that the GAME would reject
# via ``day._ai_day_action._accept`` (``target_id != speaker.id``) but which the
# proxy captures RAW. The metric exists precisely to see this attempt the game
# absorbs — so the scorer must count it in the numerator from the raw payload.
# ===========================================================================


def _capture(action: object, speaker_id: str | None) -> CaptureRecord:
    """A synthetic ``DayAction`` capture as the proxy would have recorded it.

    Built directly (not via the proxy) so the test pins the SCORER's reading of a
    raw payload — including the self-targeted vote the game's ``_accept`` would
    have rejected before it could reach any post-game state.
    """
    return CaptureRecord(schema=DayAction, raw_result=action, speaker_id=speaker_id)


def test_scorer_counts_a_safety_net_rejected_self_vote_from_the_raw_payload() -> None:
    """A self-targeted vote ``_accept`` rejects is STILL counted by the scorer.

    The load-bearing claim of the whole slice: a ``DayAction(kind="vote",
    target_id == speaker_id)`` never reaches game state (the turn-handler's
    ``_accept`` requires ``target_id != speaker.id``), so the ONLY place it can be
    counted is the raw structured-output payload the proxy intercepts. A single
    such capture, attributed to its speaker, lands in BOTH numerator and
    denominator → rate 1.0. If the scorer read post-game state instead of the raw
    capture, this would be 0/0 — so this test proves the metric sees what the game
    absorbs.
    """
    captures = [_capture(DayAction(kind="vote", target_id="p-1"), speaker_id="p-1")]

    facets = score_self_vote_initiation(captures)

    assert facets == {"rate": 1.0, "count": 1, "denominator": 1}


def test_scorer_mixed_captures_count_self_votes_over_all_vote_attempts() -> None:
    """A mixed batch: self-vote (num), vote-on-another (denom only), speak (excluded).

    The full classification in one batch:
    - a self-vote (``target_id`` == speaker) → numerator AND denominator,
    - a vote on a DIFFERENT player → denominator only (a real attempt, not
      self-targeted),
    - a ``kind="speak"`` → excluded entirely (not a vote-initiation attempt),
    - an unattributed capture (``speaker_id is None``) → skipped (no AI speaker).
    Numerator 1 over denominator 2 (the two votes) → rate 0.5.
    """
    captures = [
        _capture(DayAction(kind="vote", target_id="p-1"), speaker_id="p-1"),  # self
        _capture(DayAction(kind="vote", target_id="p-1"), speaker_id="p-2"),  # other
        _capture(DayAction(kind="speak", text="hmm."), speaker_id="p-1"),  # excluded
        _capture(DayAction(kind="vote", target_id="p-9"), speaker_id=None),  # skipped
    ]

    facets = score_self_vote_initiation(captures)

    assert facets == {"rate": 0.5, "count": 1, "denominator": 2}


def test_scorer_vote_on_another_is_denominator_only() -> None:
    """A vote on a DIFFERENT player is an attempt (denominator) but not self (0/1)."""
    captures = [_capture(DayAction(kind="vote", target_id="p-2"), speaker_id="p-1")]

    facets = score_self_vote_initiation(captures)

    assert facets == {"rate": 0.0, "count": 0, "denominator": 1}


def test_scorer_speak_captures_are_excluded_from_the_denominator() -> None:
    """``kind="speak"`` captures are not vote-initiation attempts → absent (0/0).

    A batch of only speaks (each attributed to a real speaker) offered no vote
    attempt at all, so the denominator is 0 and the metric is reported ABSENT —
    ``{rate: None, count: 0, denominator: 0}`` — not a misleading ``rate: 0.0``.
    """
    captures = [
        _capture(DayAction(kind="speak", text="one"), speaker_id="p-1"),
        _capture(DayAction(kind="speak", text="two"), speaker_id="p-2"),
    ]

    facets = score_self_vote_initiation(captures)

    assert facets == {"rate": None, "count": 0, "denominator": 0}


def test_scorer_empty_captures_is_absent_no_zero_division() -> None:
    """An empty capture list is absent (rate None, 0/0) and never raises."""
    assert score_self_vote_initiation([]) == {
        "rate": None,
        "count": 0,
        "denominator": 0,
    }


# ===========================================================================
# C — ollama_smoke counting unchanged after the Task-1 refactor (requirement 3).
#
# A regression pin on the count-only path: drive the proxy in count-only mode
# (stats, no captures, no resolver) over a scripted sequence of inner outcomes
# and assert the resulting ``SchemaStats`` match the documented semantics —
# i.e. ollama_smoke's RELIABLE/UNRELIABLE verdict inputs (attempts / failures /
# fallbacks / failure_rate) are intact.
# ===========================================================================


def _drive_count_only(
    schema: object,
    outcomes: list[object],
) -> SchemaStats:
    """Drive ONE schema through a count-only proxy over ``outcomes``; return its stats.

    Count-only mode is exactly ``ollama_smoke``'s install: a ``stats`` map, NO
    ``captures`` list, NO ``speaker_resolver``. Each outcome is a value the inner
    returns or an ``Exception`` instance it raises; a raised invoke is re-raised
    by the proxy (the game's own exception handling is preserved), so the helper
    swallows it AFTER the proxy has booked the failure — mirroring how the game's
    retry/fallback would catch it. Returns the per-schema ``SchemaStats`` the
    proxy accumulated, keyed by the schema's ``__name__``.
    """
    stats: dict[str, SchemaStats] = {}
    inner = _ScriptedInner(outcomes)
    proxy = InstrumentedModel(inner, stats=stats)
    for _ in outcomes:
        try:
            proxy.with_structured_output(schema).invoke([HumanMessage(content="x")])
        except Exception:
            # The proxy already recorded the failure; the game's retry/fallback
            # would absorb the raise here. The counting is what we assert.
            pass
    return stats[schema.__name__]


def test_counting_all_successes_record_attempts_only() -> None:
    """Clean instance results: attempts climb, no failures, no fallbacks, 0.0 rate.

    A run of valid ``DayAction`` instances are each a success — ``record_success``
    bumps only ``attempts``. This is the all-green column ``ollama_smoke`` reports
    as a RELIABLE schema.
    """
    stats = _drive_count_only(
        DayAction,
        [
            DayAction(kind="speak", text="a"),
            DayAction(kind="speak", text="b"),
            DayAction(kind="speak", text="c"),
        ],
    )

    assert stats.attempts == 3
    assert stats.failures == 0
    assert stats.fallbacks == 0
    assert stats.failure_rate == 0.0


def test_counting_non_instance_result_is_a_failure() -> None:
    """A non-instance result (no exception) still counts as a parse failure.

    When the inner returns something that is NOT an instance of the requested
    schema (e.g. langchain handed back ``None`` / a raw message because the model
    produced no tool call), the proxy books a failure even though nothing was
    raised — the masked parse failure ``ollama_smoke`` was built to surface. One
    isolated non-instance among successes is a single failure, not yet a fallback.
    """
    stats = _drive_count_only(
        DayAction,
        [
            DayAction(kind="speak", text="ok"),
            None,  # non-instance result → a parse failure, no exception
            DayAction(kind="speak", text="ok again"),
        ],
    )

    assert stats.attempts == 3
    assert stats.failures == 1
    assert stats.fallbacks == 0
    assert stats.failure_rate == pytest.approx(1 / 3)


def test_counting_raised_invoke_is_a_failure_and_records_last_error() -> None:
    """A raised invoke is booked as a failure (and the error message is captured).

    An exception from the inner ``invoke`` is recorded as a failure and re-raised
    (so the game's own handling is preserved); ``last_error`` carries the
    ``"<Type>: <msg>"`` string the report prints. One raise among successes is one
    failure, no fallback.
    """
    stats = _drive_count_only(
        DayAction,
        [
            DayAction(kind="speak", text="ok"),
            ValueError("boom"),  # raised by the inner invoke
            DayAction(kind="speak", text="ok again"),
        ],
    )

    assert stats.attempts == 3
    assert stats.failures == 1
    assert stats.fallbacks == 0
    assert stats.last_error == "ValueError: boom"


def test_counting_two_consecutive_failures_trip_exactly_one_fallback() -> None:
    """Two consecutive failures = ONE fallback (the masked retry-then-fallback path).

    The signature ``ollama_smoke`` reads as "the game's retry-then-deterministic-
    fallback fired": two consecutive raw failures on the same schema increment
    ``fallbacks`` once and reset the consecutive counter. A success between
    failures breaks the run, so isolated failures never trip a fallback.
    Sequence: success, fail, fail (→ 1 fallback), success — 4 attempts,
    2 failures, exactly 1 fallback.
    """
    stats = _drive_count_only(
        DayAction,
        [
            DayAction(kind="speak", text="ok"),
            ValueError("first"),  # consecutive-failure run begins
            None,  # second consecutive failure → ONE fallback
            DayAction(kind="speak", text="ok again"),
        ],
    )

    assert stats.attempts == 4
    assert stats.failures == 2
    assert stats.fallbacks == 1
    assert stats.failure_rate == pytest.approx(0.5)


def test_counting_a_success_between_failures_resets_the_fallback_run() -> None:
    """A success between two failures resets the consecutive run → NO fallback.

    fail, success, fail: the success in the middle resets ``_consecutive_failures``
    to 0, so neither failure ever reaches the two-in-a-row that trips a fallback —
    2 failures over 3 attempts but ``fallbacks == 0``. This pins the adjacency
    rule ``ollama_smoke``'s fallback column depends on.
    """
    stats = _drive_count_only(
        DayAction,
        [
            ValueError("first"),  # failure
            DayAction(kind="speak", text="recovered"),  # success → resets the run
            None,  # an isolated second failure (non-instance)
        ],
    )

    assert stats.attempts == 3
    assert stats.failures == 2
    assert stats.fallbacks == 0


def test_counting_four_consecutive_failures_trip_two_fallbacks() -> None:
    """Four consecutive failures = TWO fallbacks (one per pair).

    The fallback counter ticks once per *two consecutive* failures and resets
    after each tick, so a straight run of four raw failures on one schema books
    two fallbacks — the game's retry-then-fallback firing twice across two turns.
    Mixes a raise and non-instance failures to show both failure kinds feed the
    same consecutive run.
    """
    stats = _drive_count_only(
        DayAction,
        [
            ValueError("1"),  # fail 1 ─┐ pair → fallback #1
            None,             # fail 2 ─┘
            None,             # fail 3 ─┐ pair → fallback #2
            ValueError("4"),  # fail 4 ─┘
        ],
    )

    assert stats.attempts == 4
    assert stats.failures == 4
    assert stats.fallbacks == 2
    assert stats.failure_rate == 1.0


def test_counting_failure_rate_zero_when_no_attempts() -> None:
    """A never-exercised schema has ``failure_rate == 0.0`` (no ZeroDivisionError).

    ``ollama_smoke`` reports unexercised schemas as ``(not exercised)`` and its
    ``_judge`` guards on ``s.attempts`` before reading ``failure_rate`` — but the
    property must still be safe to read on a fresh ``SchemaStats`` (0/0 → 0.0).
    """
    assert SchemaStats().failure_rate == 0.0
