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

D. **``include_raw`` classification** (spec 041, Slice 1, Task 1.2 — added after
   the defect). Until spec 041 success was the single test
   ``isinstance(result, schema)``, so the ``include_raw=True`` envelope — a
   ``dict`` — was booked ``"non-instance result: dict"`` on EVERY invoke, healthy
   or not: ``SchemaStats["Diary"].failure_rate`` was 1.0 by construction and the
   ADR-013 transport gate could not observe its own fix (six healthy invokes read
   ``attempts=6 failures=6 fallbacks=3``). These tests drive the same count-only
   path over envelope-shaped results and pin BOTH signs of the bug: a good
   ``parsed`` books zero failures **and zero fallbacks**, while a plain dict that
   is not the envelope is **still** a failure — because "any mapping passes" is
   the identical defect wearing the opposite sign.

E. **Kwarg forwarding.** ``method=`` and ``include_raw=`` must reach the inner
   runnable verbatim, through one proxy and through the two-deep nesting the eval
   stack actually builds. A proxy that ate ``method=`` would disable
   grammar-constrained decoding *only while the eval harness is watching*.

F. **The capture path's half of the envelope story.** ``CaptureRecord.raw_result``
   is documented as the *unmodified* inner return, so an ``include_raw`` invoke
   captures the envelope rather than the parsed object — and a scorer reading
   captures sees nothing. Pinned so the hazard lives somewhere red-able.

Everything is built on the REAL classes/constants (imported), so a rename breaks
these tests; day-speak prompts use the REAL ``DAY_SPEAK_USER_TEMPLATE`` so a
template reword breaks attribution loudly. No provider client is ever
constructed and the autouse ``safe_llm`` net is left intact — these tests never
go near an LLM call site (the proxy is driven over a hand-built fake inner
runnable, not ``graphia.llm``).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from graphia.llm import Ballot, DayAction, Diary
from graphia.nodes.day import (
    _persona_block,
    _render_standings,
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
                standings=_render_standings({"players": {speaker.id: speaker}}),
                roster="(roster)",
                context="(ctx)",
                private_thoughts="",
                role_guidance="",
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
    successes/failures) is driven with NO model. Records the schema AND the
    kwargs each ``with_structured_output`` was asked for, so a test can confirm
    both reach the inner untouched.
    """

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0
        self.requested_schemas: list[object] = []
        self.requested_kwargs: list[dict[str, object]] = []

    def with_structured_output(
        self, schema: object, **kwargs: object
    ) -> _ScriptedStructured:
        self.requested_schemas.append(schema)
        # Recorded so a test can assert the proxy forwards ``method=`` /
        # ``include_raw=`` verbatim and injects nothing of its own (section E).
        self.requested_kwargs.append(dict(kwargs))
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


# ===========================================================================
# D — ``include_raw`` classification (spec 041, Slice 1, Task 1.2).
#
# The defect: success used to be the single test ``isinstance(result, schema)``,
# which is the ``include_raw=True`` envelope's exact NEGATION — the envelope is a
# ``dict``, never a schema instance. So every diary invoke (production binds
# ``with_structured_output(Diary, include_raw=True)``, spec 039) was booked
# ``record_failure("non-instance result: dict")`` whether or not it carried a
# perfectly good ``parsed``. ``SchemaStats["Diary"].failure_rate`` was therefore
# 1.0 BY CONSTRUCTION, and the ADR-013 transport gate could not observe its own
# fix. Measured before the fix: six healthy invokes read
# ``attempts=6 failures=6 fallbacks=3``.
#
# Both signs are pinned below. "Any mapping passes" would be the identical defect
# with the sign flipped, so a plain dict that is not the envelope — and a mapping
# carrying only ONE of the discriminating pair — must still book a failure.
#
# The envelope's key names are written as LITERALS here on purpose: they are
# LangChain's ``with_structured_output(include_raw=True)`` contract, not ours, so
# these tests model the external shape rather than mirroring
# ``instrument._INCLUDE_RAW_KEYS`` back at itself.
# ===========================================================================


def _envelope(
    parsed: object,
    parsing_error: object = None,
    *,
    with_raw: bool = True,
) -> dict[str, object]:
    """The mapping ``with_structured_output(..., include_raw=True)`` returns.

    Built in LangChain's own key order (``raw`` / ``parsed`` / ``parsing_error``)
    with a realistic ``AIMessage`` in ``raw``. ``with_raw=False`` drops the
    ``raw`` key to model a provider that ignored the ``include_raw`` request —
    which is why the instrument requires only the ``parsed`` / ``parsing_error``
    pair to recognise the envelope.
    """
    env: dict[str, object] = {}
    if with_raw:
        env["raw"] = AIMessage(content='{"entry": "raw text"}')
    env["parsed"] = parsed
    env["parsing_error"] = parsing_error
    return env


def test_counting_an_include_raw_envelope_with_a_good_parsed_is_a_success() -> None:
    """A healthy ``include_raw`` envelope books ZERO failures — THE missing test.

    This is the test whose absence let the defect ship: the outer object is a
    ``dict``, so the pre-041 ``isinstance(result, schema)`` rule booked it as
    ``"non-instance result: dict"`` even though ``parsed`` holds a real
    ``Diary``. The answer is INSIDE the envelope, and that is where the
    classification must look.

    ``last_error`` is asserted ``None`` too, because on the old rule this very
    invoke wrote the string that told a reader nothing about what the model did.
    """
    stats = _drive_count_only(Diary, [_envelope(Diary(entry="Bo watched me."))])

    assert stats.attempts == 1
    assert stats.failures == 0
    assert stats.fallbacks == 0
    assert stats.failure_rate == 0.0
    assert stats.last_error is None


def test_counting_six_healthy_envelope_invokes_invent_no_fallbacks() -> None:
    """Six healthy envelopes: 6 attempts, 0 failures, and — the point — 0 fallbacks.

    ``fallbacks`` is **inferred**, not counted: two consecutive failures are read
    as "the game's retry-then-deterministic-fallback fired". So the old rule did
    not merely add N failures on a per-player diary fan-out — it manufactured
    ``N // 2`` fallbacks that never happened, the instrument inventing a claim
    about the GAME's behaviour out of its own mis-classification. Six healthy
    invokes read ``attempts=6 failures=6 fallbacks=3`` before the fix, and the
    ``3`` is the more misleading half: ``floor(6/2)`` happens to equal the
    disqualified campaign's genuine 0.50 substitution rate, so cross-checking the
    two figures could have shown spurious agreement. This test pins the derived
    number, not just the counted one.
    """
    stats = _drive_count_only(
        Diary,
        [_envelope(Diary(entry=f"day {n}")) for n in range(6)],
    )

    assert stats.attempts == 6
    assert stats.failures == 0
    assert stats.fallbacks == 0
    assert stats.failure_rate == 0.0


def test_counting_an_include_raw_envelope_with_parsed_none_is_one_failure() -> None:
    """``parsed: None`` is a real (masked) failure — and ``last_error`` names it.

    The common live shape: the model answered in prose and emitted no tool call,
    so LangChain hands back an envelope whose ``parsed`` is ``None`` and whose
    ``raw`` holds the text. That IS a parse failure and must be booked as one —
    the fix must not over-reach into "any envelope passes".

    ``last_error`` must **not** read ``"dict"``: that was the pre-041 message for
    every diary invoke ever booked, healthy or not, and it described the
    instrument's own confusion rather than the model's behaviour. The assertion
    is written both ways — the exact string, and the explicit absence of
    ``"dict"`` — because the second is the regression the task names.
    """
    stats = _drive_count_only(Diary, [_envelope(None)])

    assert stats.attempts == 1
    assert stats.failures == 1
    assert stats.fallbacks == 0
    assert stats.last_error == "include_raw parsed is NoneType, not Diary"
    assert "dict" not in (stats.last_error or "")


def test_counting_an_include_raw_envelope_with_a_parsing_error_is_a_failure() -> None:
    """A carried ``parsing_error`` is a failure, and the error text is reported.

    Under ``include_raw=True`` LangChain **catches** the parse error and hands it
    back in the envelope rather than raising, so this branch is the ONLY place
    the instrument can ever see it — an exception-based classification would
    score it a success. ``last_error`` carries the error's type and message so
    the report names the actual cause.
    """
    stats = _drive_count_only(
        Diary,
        [_envelope(None, ValueError("bad json"))],
    )

    assert stats.attempts == 1
    assert stats.failures == 1
    assert stats.last_error == "include_raw parsing_error: ValueError: bad json"


def test_counting_an_envelope_whose_parsed_is_a_different_schema_is_a_failure() -> None:
    """``parsed`` must be an instance of the schema THIS call asked for.

    An envelope carrying a well-formed ``Ballot`` where a ``Diary`` was requested
    is not a usable answer, and the failure names both classes. Without this, the
    envelope branch could degenerate into "``parsed`` is not None ⇒ success".
    """
    stats = _drive_count_only(Diary, [_envelope(Ballot(yes=True))])

    assert stats.attempts == 1
    assert stats.failures == 1
    assert stats.last_error == "include_raw parsed is Ballot, not Diary"


def test_counting_an_envelope_missing_only_raw_is_still_read_as_the_envelope() -> None:
    """``raw`` is deliberately NOT part of the discriminating pair → success.

    ``raw`` is the one key a provider that ignored the ``include_raw`` request
    may omit, and such a reply still carries a usable answer in ``parsed`` — so
    the envelope is recognised by ``parsed`` + ``parsing_error`` alone. A
    mapping carrying a good ``parsed`` and a ``None`` ``parsing_error`` is a
    success even with no ``raw`` at all.
    """
    stats = _drive_count_only(
        Diary,
        [_envelope(Diary(entry="no raw key here"), with_raw=False)],
    )

    assert stats.attempts == 1
    assert stats.failures == 0
    assert stats.last_error is None


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"entry": "x"}, id="the-game-payload-shape"),
        pytest.param({}, id="empty-dict"),
        pytest.param({"parsed": Diary(entry="x")}, id="parsed-without-parsing-error"),
        pytest.param({"parsing_error": None}, id="parsing-error-without-parsed"),
        pytest.param(
            {"raw": AIMessage(content="{}"), "parsed": Diary(entry="x")},
            id="raw-and-parsed-but-no-parsing-error",
        ),
    ],
)
def test_counting_a_plain_dict_that_is_not_the_envelope_is_still_a_failure(
    result: dict[str, object],
) -> None:
    """The inverse-bug guard: a mapping is NOT read as the envelope by default.

    "Any dict passes" is the same defect wearing the opposite sign, and without
    this sweep the fix could silently become it. ``{"entry": "x"}`` — the
    ``Diary`` payload's own field, the mapping most likely to appear here — is a
    genuine parse failure: the game asked for a ``Diary`` instance and got a raw
    mapping it cannot use. So is a mapping carrying only ONE of the
    discriminating pair, which is what keeps the envelope identified by its
    *shape* rather than by "is a mapping".

    ``last_error`` is asserted exactly, so a mapping that fell into the envelope
    branch and failed there for a different reason would not pass this test.
    """
    stats = _drive_count_only(Diary, [result])

    assert stats.attempts == 1
    assert stats.failures == 1
    assert stats.last_error == "non-instance result: dict"


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        pytest.param(None, "non-instance result: NoneType", id="none"),
        pytest.param(
            AIMessage(content="I would rather just talk."),
            "non-instance result: AIMessage",
            id="bare-ai-message",
        ),
        pytest.param("just a string", "non-instance result: str", id="bare-string"),
    ],
)
def test_counting_a_bare_non_instance_result_is_still_a_failure(
    result: object,
    expected_error: str,
) -> None:
    """The pre-041 rule's TRUE positives survive the fix, with their messages.

    A bare ``AIMessage`` (LangChain's shape when the model emitted no tool call
    at all), a ``None``, or a bare string are each a masked parse failure: nothing
    raised, but the game got nothing usable. The envelope correction must not
    swallow these — and ``last_error`` must still name the type it actually saw,
    which is what makes the message informative here and uninformative on the
    envelope.
    """
    stats = _drive_count_only(Diary, [result])

    assert stats.attempts == 1
    assert stats.failures == 1
    assert stats.last_error == expected_error


def test_counting_mixed_envelopes_book_only_the_empty_ones() -> None:
    """One run of envelopes, discriminated: healthy, empty, healthy → 1 failure.

    The single strongest anti-vacuity case in section D, because it goes red
    under BOTH mutations: the old ``isinstance`` rule books 3 failures (and one
    fallback from the two adjacent ones), an "any mapping passes" rule books 0.
    Only a rule that reads INSIDE each envelope gives 3 attempts, 1 failure,
    0 fallbacks — the success on either side breaking the consecutive run.
    """
    stats = _drive_count_only(
        Diary,
        [
            _envelope(Diary(entry="a real entry")),
            _envelope(None),  # prose answer, no tool call → masked failure
            _envelope(Diary(entry="another real entry")),
        ],
    )

    assert stats.attempts == 3
    assert stats.failures == 1
    assert stats.fallbacks == 0
    assert stats.failure_rate == pytest.approx(1 / 3)


# ===========================================================================
# E — every ``with_structured_output`` kwarg reaches the recording inner.
#
# The proxy must never eat a kwarg. ``include_raw=True`` (spec 039's diary call)
# changes the return SHAPE, so eating it would make the harness measure a
# different shape than production produces; ``method="json_schema"`` (spec 041's
# provider seam) selects grammar-constrained decoding, so eating it would disable
# reliable decoding ONLY while the eval harness is watching — the worst failure
# mode this project has.
# ===========================================================================


def test_with_structured_output_forwards_method_and_include_raw_to_the_inner() -> None:
    """``method=`` and ``include_raw=`` arrive at the inner client verbatim.

    Both kwargs on one call, recorded by the fake inner: the schema and the exact
    kwargs mapping reach the inner untouched, and the invoke's return still
    passes straight back through the proxy.
    """
    envelope = _envelope(Diary(entry="from the envelope"))
    inner = _ScriptedInner([envelope])
    stats: dict[str, SchemaStats] = {}
    proxy = InstrumentedModel(inner, stats=stats)

    bound = proxy.with_structured_output(Diary, method="json_schema", include_raw=True)
    result = bound.invoke([HumanMessage(content="write your diary")])

    assert inner.requested_schemas == [Diary]
    assert inner.requested_kwargs == [{"method": "json_schema", "include_raw": True}]
    assert result is envelope
    # And the forwarded shape is classified on its ``parsed``, not the envelope.
    assert stats["Diary"].failures == 0


def test_with_structured_output_injects_no_kwargs_of_its_own() -> None:
    """A bare bind forwards an EMPTY kwargs mapping — no allowlist, no defaults.

    The anti-vacuity twin of the forwarding test above: asserting that kwargs
    arrive proves nothing about a proxy that *adds* one. A proxy that injected
    ``include_raw`` or a ``method`` default would change the transport under the
    harness just as surely as one that dropped it.
    """
    inner = _ScriptedInner([Diary(entry="bare")])
    proxy = InstrumentedModel(inner, stats={})

    proxy.with_structured_output(Diary).invoke([HumanMessage(content="x")])

    assert inner.requested_kwargs == [{}]


def test_kwargs_survive_two_nested_instrumented_proxies() -> None:
    """Two proxies deep — the eval stack's real arrangement — and nothing is eaten.

    ``blunder_eval`` installs a capturing proxy over a client that may already be
    proxied, so on an eval run the stack is two deep. Each layer's
    ``with_structured_output`` must pass ``**kwargs`` straight out, or the
    transport changes only under the harness. Both layers are also shown to do
    their own job on the same invoke: the outer books the count (on the envelope's
    ``parsed``, not the envelope) and the inner records the capture.
    """
    envelope = _envelope(Diary(entry="two proxies deep"))
    client = _ScriptedInner([envelope])
    captures: list[CaptureRecord] = []
    stats: dict[str, SchemaStats] = {}
    capturing = InstrumentedModel(
        client, captures=captures, speaker_resolver=lambda _messages: "p-1"
    )
    counting = InstrumentedModel(capturing, stats=stats)

    result = counting.with_structured_output(
        Diary, method="json_schema", include_raw=True
    ).invoke([HumanMessage(content="write your diary")])

    # The kwargs reached the INNERMOST client, unchanged, through both layers.
    assert client.requested_schemas == [Diary]
    assert client.requested_kwargs == [{"method": "json_schema", "include_raw": True}]
    assert result is envelope
    # Both layers still did their own work on this one invoke.
    assert stats["Diary"].attempts == 1
    assert stats["Diary"].failures == 0
    assert [c.raw_result for c in captures] == [envelope]


# ===========================================================================
# F — the capture path's half of the envelope story.
# ===========================================================================


def test_capture_of_an_include_raw_envelope_holds_the_envelope_not_the_parsed() -> None:
    """``raw_result`` is the UNMODIFIED inner return — so a scorer sees nothing.

    ``CaptureRecord.raw_result`` is documented as the unmodified return of the
    inner runnable, captured before any validator runs, and this pins that
    contract for the envelope shape: the record holds the mapping, not the
    ``DayAction`` inside it.

    The consequence is recorded here rather than left as a comment, because it is
    the capture path's half of the same defect: ``score_self_vote_initiation``
    tests ``isinstance(action, DayAction)``, so an envelope capture is skipped
    entirely and the metric reads ABSENT. Had any ``DayAction`` call site ever
    bound ``include_raw=True``, self-vote detection would have silently measured
    nothing. The last two assertions are what make the ``rate: None`` non-vacuous:
    the payload INSIDE the envelope is a genuine self-vote, so the absent metric
    is caused by the envelope and by nothing else.
    """
    self_vote = DayAction(kind="vote", target_id="p-1")
    envelope = _envelope(self_vote)
    inner = _ScriptedInner([envelope])
    captures: list[CaptureRecord] = []
    proxy = InstrumentedModel(
        inner, captures=captures, speaker_resolver=lambda _messages: "p-1"
    )

    result = proxy.with_structured_output(DayAction, include_raw=True).invoke(
        [HumanMessage(content="x")]
    )

    assert result is envelope
    assert len(captures) == 1
    assert captures[0].raw_result is envelope
    assert captures[0].speaker_id == "p-1"
    # The scorer is envelope-blind: the attempt is invisible to it.
    assert score_self_vote_initiation(captures) == {
        "rate": None,
        "count": 0,
        "denominator": 0,
    }
    # Non-vacuity: the payload inside IS a self-vote by this very speaker, so the
    # absent metric above is the envelope's doing, not a benign payload's.
    assert envelope["parsed"] is self_vote
    assert self_vote.target_id == captures[0].speaker_id
