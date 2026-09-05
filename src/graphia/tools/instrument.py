"""Shared structured-output instrumentation proxy (tech-spec 011 §2.2).

A small, provider-agnostic proxy pair that sits *above* the LLM provider branch
(installed through ``graphia.llm``'s documented in-process seams —
``_active_provider`` / ``_large`` / ``_small`` — so it works identically for
Bedrock and Ollama, ADR-009's dividend) and intercepts every
``with_structured_output(schema).invoke(...)`` call without touching production
code. It does two things, independently selectable per proxy instance:

- **counting** — per-schema raw-attempt outcomes (success / failure / masked
  fallback), the exact instrumentation ``ollama_smoke`` was built on. This is
  the ADR-010 reliability gate's measurement; its semantics were preserved
  byte-for-byte by the original extraction (``ollama_smoke`` is refactored
  *onto* this module with no behavior change). **Spec 041 corrected exactly one
  rule** — how an ``include_raw=True`` return is classified, see
  :func:`_classification_error` — and ``ollama_smoke``'s four schemas are
  untouched by that correction, because none of them binds ``include_raw``.
- **raw capture** — per invoke, a :class:`CaptureRecord` of
  ``(schema, raw_result, speaker_id)``. ``speaker_id`` is resolved **at invoke
  time** through an injected ``speaker_resolver`` callback (default ``None``),
  the same live-resolution shape ``conftest.dynamic_night_pointing`` uses for
  race-free target selection. This is what spec 011's ``self_vote.initiation``
  metric (Slice 3, Task 2) needs: a self-targeted AI vote is rejected by the
  game's turn-handler (``day._ai_day_action._accept``) before it ever reaches
  game state, so the *only* place to count it is the raw structured-output
  payload, with the speaker attributed.

Counting and capture are orthogonal: ``ollama_smoke`` constructs the proxy with
a ``stats`` map and no resolver (count only); Task 2's harness will construct it
with a ``captures`` list and a state-reading resolver (capture, optionally also
counting). Either, both, or neither may be wired on one proxy.

Speaker resolution — the design fork and why prompt-parse wins
--------------------------------------------------------------
The proxy needs to attribute each captured payload to "who is speaking right
now". Two sources are visible to a proxy that sees ``with_structured_output`` +
``invoke(messages)``:

1. **Parsing the speaker out of the invoke prompt** (chosen). The proxy hands
   the resolver the *invoke messages*; ``DAY_SPEAK_USER_TEMPLATE`` opens
   ``"You are {speaker}."``, so the resolver reads the speaker *name* straight
   off the ``HumanMessage`` it is given and maps it to the speaker's *id* via
   the game's ``players`` (a roster name→id lookup the consumer already holds).
   This needs **no graph state** and so cannot go stale or re-enter the running
   graph.

2. **A live-state resolver callback** — a ``Callable`` that reads live graph
   state (``graph.get_state(run_config)``) and returns the current speaker's id.

Prompt-parse is the robust choice, and a live-state read is a real trap here:

- **No stale snapshot, no re-entrancy.** ``self_vote.initiation`` attributes
  each ``DayAction`` to the AI that produced it; that attribution must be exact.
  A ``get_state()`` call from *inside* a running ``day_turn`` super-step returns
  the last *committed* checkpoint, not the in-flight turn — the same staleness
  class that bit ``tests/test_slice7_vote.py`` (a pre-``assign_roles`` snapshot).
  The invoke prompt, by contrast, is the literal input to *this* call: the
  ``{speaker}`` it names is exactly the AI whose ``DayAction`` we are capturing.
- **Schema-agnostic, by the resolver's own choice.** ``Ballot`` / ``Pointing`` /
  ``Roster`` invokes carry no ``"You are {speaker}"`` line; the resolver simply
  returns ``None`` for them (no match), so capture stays attributed only where a
  Day speaker is meaningful.

The cost is one template coupling: a reword of ``DAY_SPEAK_USER_TEMPLATE`` would
break the parse — but the consumer derives its anchor *from the imported
template* (not a hardcoded copy), so a reword fails loudly in the offline tests
rather than drifting silently, the same template-coupling discipline the
game-record detectors use. The alternative is documented here so a future
maintainer sees it was a considered fork, not an oversight.

The resolver receives the invoke ``messages`` (the first positional ``invoke``
argument, or ``None`` when the call shape is unusual) so it can read the prompt;
``ollama_smoke`` installs no resolver and is wholly unaffected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

# A resolver maps one invoke's ``messages`` (the first positional ``invoke``
# argument — the prompt this call was handed) to the current speaker's player
# *id*, or ``None`` when no speaker is meaningful for this invoke (a ``Roster`` /
# ``Ballot`` / ``Pointing`` call that carries no ``"You are {speaker}"`` line, or
# a prompt that does not resolve to a known player). It reads only the prompt it
# is given — never live graph state — so attribution cannot go stale or re-enter
# the running graph (the stale-``get_state`` trap). Called at *invoke* time.
SpeakerResolver = Callable[[Any], "str | None"]


# ---------------------------------------------------------------------------
# Counting — per-schema raw-attempt outcomes (ollama_smoke's measurement).
# ---------------------------------------------------------------------------


@dataclass
class SchemaStats:
    """Raw attempt outcomes for one structured-output schema.

    Counts raw ``with_structured_output(schema).invoke(...)`` outcomes
    *underneath* the game's own retry-then-deterministic-fallback handling, so a
    parse failure the game silently recovers from still shows up here. The
    counting semantics are exactly ``ollama_smoke``'s original ``SchemaStats``
    (this is the moved definition):

    - ``attempts`` increments on every raw invoke (success or failure).
    - ``failures`` increments on an exception OR a result carrying no usable
      schema instance, as decided by :func:`_classification_error` — which is
      what makes an ``include_raw=True`` envelope judged on its ``parsed``
      payload rather than on the envelope object itself.
    - ``fallbacks`` increments once per *two consecutive* failures on this
      schema — the signature of the game's retry-then-fallback path firing.
      Node helpers run to completion before the next starts (single-threaded
      graph), so per-schema adjacency is sound. Note that this figure is
      **inferred, not counted**: it is a claim about the *game's* behaviour
      derived from adjacency, so a mis-classified success does not merely add
      one failure — it manufactures fallbacks that never fired. That is what
      the pre-041 rule did to ``Diary``; again, see
      :func:`_classification_error`.
    """

    attempts: int = 0
    failures: int = 0  # exception OR non-instance result from a raw invoke
    fallbacks: int = 0  # two consecutive raw failures = the game's
    #                     retry-then-deterministic-fallback path fired
    _consecutive_failures: int = 0
    last_error: str | None = None

    def record_success(self) -> None:
        self.attempts += 1
        self._consecutive_failures = 0

    def record_failure(self, error: str) -> None:
        self.attempts += 1
        self.failures += 1
        self.last_error = error
        self._consecutive_failures += 1
        # Every node helper tries at most twice before falling back to a
        # deterministic value (or, for Roster, crashing the game) — so two
        # consecutive raw failures on the same schema mean the masked
        # fallback fired. Node helpers run to completion before the next one
        # starts (single-threaded graph), so per-schema adjacency is sound.
        if self._consecutive_failures >= 2:
            self.fallbacks += 1
            self._consecutive_failures = 0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.attempts if self.attempts else 0.0


# ---------------------------------------------------------------------------
# Outcome classification — what counts as a successful raw invoke.
# ---------------------------------------------------------------------------

# ``with_structured_output(..., include_raw=True)`` promises a mapping of ``raw``
# / ``parsed`` / ``parsing_error``. ``parsed`` and ``parsing_error`` are the
# *discriminating* pair, and a mapping must carry BOTH to be read as the
# envelope. ``raw`` is deliberately NOT required: it is the one key a provider
# that ignored the ``include_raw`` request may omit, and such a reply still
# carries a usable answer in ``parsed`` — booking it a failure would be the
# same mis-classification this rule exists to fix. Requiring the pair is what
# keeps a plain game payload — ``{"entry": "x"}`` — outside the envelope
# branch.
_INCLUDE_RAW_KEYS = ("parsed", "parsing_error")


def _classification_error(result: Any, schema: Any) -> str | None:
    """Classify ONE raw invoke's return value: ``None`` on success, else why not.

    Three shapes reach here, because ``with_structured_output`` has two
    documented return contracts and a failing model produces a third:

    1. **A bare schema instance** — the ``include_raw=False`` contract, and what
       every Graphia call site except the diary asks for. Success.
    2. **An ``include_raw=True`` envelope** — a mapping of ``raw`` / ``parsed``
       / ``parsing_error``. The envelope itself is *never* a schema instance;
       the answer is inside it. Judged on ``parsed`` being a schema instance
       with ``parsing_error is None``.
    3. **Anything else** — ``None``, a bare ``AIMessage`` (langchain's shape
       when the model emitted no tool call at all), or a plain dict that is not
       the envelope. A masked parse failure: no exception surfaced, but the game
       got nothing it can use.

    Why the old rule was wrong, and which measurement it corrupted
    --------------------------------------------------------------
    Until spec 041 the rule was the single test ``isinstance(result, schema)``,
    which is shape 2's *negation*: the envelope is a ``dict``, so **every**
    ``include_raw`` invoke was booked ``record_failure("non-instance result:
    dict")`` — including the ones carrying a perfectly good ``parsed``. The
    production diary call *was* ``with_structured_output(Diary,
    include_raw=True)`` (spec 039; spec 041 Slice 4 withdrew the envelope
    request with the prose recovery that consumed it), so while it stood — on
    every provider, in every run, healthy or not:

    - ``SchemaStats["Diary"].failure_rate`` was **1.0 by construction**, so the
      ADR-013 transport gate could not observe its own fix and any smoke output
      read as diary transport health was fabricated. The blast radius is exactly
      the *counting* consumers, which is narrower than it first looks:
      ``ollama_smoke`` installs this proxy with a ``stats`` map, whereas
      ``blunder_eval`` installs it in capture-only mode (``captures=``, no
      ``stats=``) and measures the diary through the independent sentinel scorer
      ``score_diary_fallback``. That is why the ledger's committed
      ``quality.diary_fallback_rate`` figures are NOT fabricated by this defect
      — and why the genuine 0.50 was visible at all.
    - ``fallbacks`` was corrupted too, and *more* misleadingly, because it is
      inferred rather than counted: two *consecutive* failures are read as "the
      game's retry-then-deterministic-fallback fired". The diary is a per-player
      fan-out, so N healthy diary invokes booked ``N // 2`` fallbacks that never
      happened — the instrument inventing a claim about the *game's* behaviour
      out of its own mis-classification. Measured before this fix: six healthy
      invokes gave ``attempts=6, failures=6, fallbacks=3``.

    Nobody saw it because ``ollama_smoke.SCHEMA_NAMES`` omits ``Diary`` and both
    the printed report and the verdict iterate that tuple; only the ``--json``
    payload iterates the full stats map. The one place a 100% ``Diary`` failure
    rate would have surfaced is a report nobody routinely reads. (Spec 041
    Slice 11 derives that tuple from ``llm.GAMEPLAY_SCHEMAS`` instead.)

    Why this ships even though ``include_raw`` is leaving
    ----------------------------------------------------
    Spec 041 Slice 4 withdraws the ``include_raw=True`` diary call, after which
    no Graphia call site produces shape 2. The fix ships anyway, and ships
    *first*: **this proxy is the measurement instrument.** It must not lie about
    a shape LangChain can legitimately return, whether or not this codebase
    currently asks for it — otherwise the next call site that wants the raw
    message silently re-poisons the ledger. Repairing the instrument *after*
    measuring the transport would also produce a number nobody could trust in
    either direction, which is why Slice 1 precedes the swap it exists to grade.

    The symmetric trap, stated so it stays shut
    -------------------------------------------
    "Any mapping passes" is the same bug wearing the opposite sign. A plain dict
    such as ``{"entry": "x"}`` is a genuine parse failure and must still be
    booked as one, so a mapping is read as the envelope only when it carries
    **both** keys in ``_INCLUDE_RAW_KEYS``: the envelope is identified by its
    *shape*, never by "is a mapping".

    The returned string is what lands in ``SchemaStats.last_error``, so each
    envelope failure names its own cause. ``last_error`` reading ``"dict"`` —
    the pre-041 message for every diary invoke ever booked, healthy or not —
    told a reader nothing about what the model actually did.

    PURE: no state, no RNG, no clock, no I/O, no model call.
    """
    if not isinstance(schema, type):
        # A dict JSON schema (or anything else non-class): there is nothing to
        # ``isinstance`` against, so the invoke is taken at its word. Unchanged
        # from the pre-041 rule.
        return None
    if isinstance(result, schema):
        return None  # shape 1 — the bare parsed instance
    if isinstance(result, Mapping) and all(k in result for k in _INCLUDE_RAW_KEYS):
        # shape 2 — the ``include_raw`` envelope. The answer is ``parsed``.
        parsing_error = result["parsing_error"]
        if parsing_error is not None:
            # langchain CATCHES the parse error under ``include_raw=True`` and
            # hands it back here rather than raising, so this branch is the only
            # place it is ever visible to the instrument.
            return (
                f"include_raw parsing_error: "
                f"{type(parsing_error).__name__}: {parsing_error}"
            )
        parsed = result["parsed"]
        if not isinstance(parsed, schema):
            # The common live shape: the model answered in prose and emitted no
            # tool call, so ``parsed`` is ``None`` and only ``raw`` holds text.
            return (
                f"include_raw parsed is {type(parsed).__name__}, "
                f"not {schema.__name__}"
            )
        return None
    return f"non-instance result: {type(result).__name__}"


# ---------------------------------------------------------------------------
# Raw capture — per-invoke (schema, raw_result, speaker_id) records.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CaptureRecord:
    """One raw structured-output invoke, with the speaker attributed.

    - ``schema`` — the schema class (or value) passed to
      ``with_structured_output``; the consumer filters on it (e.g.
      ``DayAction``) to find the invokes it cares about.
    - ``raw_result`` — the *unmodified* return of the inner runnable, captured
      BEFORE the game's ``_accept`` validators run. This is the whole point: a
      self-targeted ``DayAction(kind="vote", target_id == speaker_id)`` that the
      turn-handler rejects is visible here and nowhere else.
    - ``speaker_id`` — the speaker id resolved from this invoke's prompt, or
      ``None`` if no resolver was installed or the resolver returned/raised
      ``None`` (a non-speaker schema, or a name that mapped to no player).
    """

    schema: Any
    raw_result: Any
    speaker_id: str | None


class _InstrumentedStructured:
    """Wraps one ``with_structured_output(schema)`` runnable.

    On each ``invoke`` it (optionally) updates the per-schema :class:`SchemaStats`
    — classifying the outcome through :func:`_classification_error`, so an
    ``include_raw=True`` envelope is judged on its ``parsed`` payload rather
    than on the envelope object — and (optionally) appends a
    :class:`CaptureRecord` carrying the raw result and the speaker id the
    resolver reads off THIS invoke's prompt. The inner runnable's return value
    and exception behavior are passed through unchanged, so the game's own
    retry/fallback logic is untouched.

    ``with_structured_output`` kwargs (``include_raw=``, ``method=``, anything a
    provider adds) are forwarded verbatim to the inner runnable by
    :meth:`InstrumentedModel.with_structured_output` — the proxy must never eat
    a kwarg, because doing so would change the transport *only under the eval
    harness*, which is the worst failure mode this project has.
    """

    def __init__(
        self,
        inner: Any,
        schema: Any,
        stats: dict[str, SchemaStats] | None,
        captures: list[CaptureRecord] | None,
        speaker_resolver: SpeakerResolver | None,
    ) -> None:
        self._inner = inner
        self._schema = schema
        self._stats = stats
        self._captures = captures
        self._speaker_resolver = speaker_resolver

    def _rec(self) -> SchemaStats:
        name = (
            self._schema.__name__
            if isinstance(self._schema, type)
            else str(self._schema)
        )
        # ``self._stats`` is not None on this path (guarded by the caller).
        assert self._stats is not None
        return self._stats.setdefault(name, SchemaStats())

    def _resolve_speaker(self, messages: Any) -> str | None:
        """Resolve the speaker id from this invoke's prompt, errors → ``None``.

        Hands the resolver the invoke ``messages`` (the prompt this call was
        given) so it can read the ``"You are {speaker}."`` line and map the name
        to an id — no live graph state, so nothing can go stale. Defensive: any
        resolver failure (an unexpected prompt shape, a name that resolves to no
        player) degrades to an unattributed record; a capture must never break
        the game it is observing.
        """
        if self._speaker_resolver is None:
            return None
        try:
            return self._speaker_resolver(messages)
        except Exception:
            return None

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        # Resolve the speaker from THIS invoke's prompt (the first positional
        # arg, the ``messages`` list), so attribution reflects exactly the call
        # we are about to capture — no separate live-state read, no staleness.
        messages = args[0] if args else kwargs.get("input")
        speaker_id = (
            self._resolve_speaker(messages) if self._captures is not None else None
        )
        rec = self._rec() if self._stats is not None else None
        try:
            result = self._inner.invoke(*args, **kwargs)
        except Exception as exc:
            if rec is not None:
                rec.record_failure(f"{type(exc).__name__}: {exc}")
            # The capture records only successful raw payloads — a raised invoke
            # produced no structured value to attribute. (The counting path
            # already booked the failure above.)
            raise  # preserve the game's own exception handling exactly
        if self._captures is not None:
            self._captures.append(
                CaptureRecord(
                    schema=self._schema,
                    raw_result=result,
                    speaker_id=speaker_id,
                )
            )
        if rec is not None:
            error = _classification_error(result, self._schema)
            if error is not None:
                # A masked parse failure: nothing was raised, but the invoke
                # carries no usable schema instance — e.g. the model produced no
                # tool call and langchain returned ``None`` / a raw message, or
                # handed back an ``include_raw`` envelope whose ``parsed`` is
                # empty. Booked as a failure, and returned UNCHANGED either way
                # so the game's own validators decide.
                rec.record_failure(error)
            else:
                rec.record_success()
        return result

    def __getattr__(self, name: str) -> Any:  # defensive passthrough
        return getattr(self._inner, name)


class InstrumentedModel:
    """Thin proxy over a tier client: intercepts ``with_structured_output``
    and delegates everything else untouched.

    Construct with a ``stats`` map to count (``ollama_smoke``'s use), a
    ``captures`` list + ``speaker_resolver`` to capture raw payloads with the
    speaker attributed (Task 2's use — the resolver reads the speaker off each
    invoke's prompt), or both. All three are optional and independent. The proxy
    is provider-agnostic — install it through ``graphia.llm``'s ``_large`` /
    ``_small`` seams above the provider branch.
    """

    def __init__(
        self,
        inner: Any,
        *,
        stats: dict[str, SchemaStats] | None = None,
        captures: list[CaptureRecord] | None = None,
        speaker_resolver: SpeakerResolver | None = None,
    ) -> None:
        self._inner = inner
        self._stats = stats
        self._captures = captures
        self._speaker_resolver = speaker_resolver

    @property
    def captures(self) -> list[CaptureRecord] | None:
        """The capture list this proxy appends to (``None`` if not capturing).

        Exposed so a consumer that constructed its own list can read records, or
        one that did not can still reach whatever the proxy is accumulating.
        """
        return self._captures

    def with_structured_output(
        self, schema: Any, **kwargs: Any
    ) -> _InstrumentedStructured:
        """Bind ``schema`` on the inner client and wrap the resulting runnable.

        **Every kwarg is forwarded verbatim** — ``**kwargs`` in, ``**kwargs``
        straight out: no allowlist, no injected defaults, nothing popped. That
        matters twice over. ``include_raw=True`` (spec 039's diary call) must
        reach the inner runnable or the harness would measure a different return
        shape than production produces; and ``method="json_schema"`` (spec 041's
        provider seam) must reach it or grammar-constrained decoding would be
        disabled *only* while the eval harness is watching, which is the worst
        failure mode this project has.
        """
        return _InstrumentedStructured(
            self._inner.with_structured_output(schema, **kwargs),
            schema,
            self._stats,
            self._captures,
            self._speaker_resolver,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
