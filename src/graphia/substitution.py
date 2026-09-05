"""Counting substitutions at the source: the seven answer kinds and the sink.

Spec 041 §2.5, Slice 6. Seven helpers across ``nodes/setup.py``,
``nodes/night.py`` and ``nodes/day.py`` ask the gameplay model for a structured
answer and, when the answer never arrives in a usable shape, return a
deterministic stand-in instead. Until this module the stand-in was invisible:
five of the seven swallowed the failure with a bare ``except Exception: pass``,
and the two that logged wrote nothing anyone counted. A run could therefore
report a clean game while a third of its diary entries were the fallback
sentence, and no reader could tell.

Exact-equality scoring cannot close that gap — ``Ballot(yes=False)`` is the
game's most common GENUINE answer, ``Pointing``'s fallback is a
``random.choice`` over real player ids, and ``DayAction``'s stand-in is exactly
the bland line a small model emits unprompted. So the count is taken where the
substitution happens: **the code that substitutes is the code that says so**,
which is what makes the count free of false positives, and what makes it
survive a retry without modelling one (the substitution branch is reached only
after every retry has been spent).

Two notifications, deliberately separate:

- :func:`note_attempt` on entry to each helper — the DENOMINATOR. Without it a
  kind that had no opportunity at all (no Mafioso alive ⇒ no ``Pointing``;
  diaries switched off ⇒ no ``Diary``) is indistinguishable from a kind that
  had many opportunities and took none of them. Slice 7 turns that distinction
  into "absent, not zero" in the ledger, which is only expressible because the
  denominator is stated rather than inferred.
- :func:`note_substitution` on the substitution branch — the NUMERATOR, and the
  owner of the log line. One place emits both facts, so a count and a log can
  never disagree.

Why an injectable sink and not a module-global ``Counter``
----------------------------------------------------------

The measurement must not reshape the thing measured. A module-global counter
accumulates for the lifetime of the process whether or not anybody reads it,
which means the game carries an eval harness's data structure around in
production for no one's benefit, and means "which game did this count belong
to?" becomes a question about when someone last remembered to clear it.

So the state here is exactly one reference — the current sink — and nothing
else. Production runs on :func:`default_sink`, which emits a log line and
accumulates NOTHING. The eval harness installs an accumulating sink of its own
(Slice 7, ``tools/blunder_eval.py``) and owns per-game isolation itself, which
is the only place that knows where one game ends and the next begins.

It is also what survives the roadmap's **Asynchronous Day Chat**. A global
counter rests on a single-threaded premise: today the whole synchronous graph
runs inside one ``asyncio.to_thread`` worker, so the seven helpers never
overlap. Asynchronous Day Chat breaks that premise by design — several AI
players speak concurrently, so several substitutions can be in flight at once,
each belonging to a different logical actor. Under an injected sink that is a
``contextvars`` change in this one file (see "Thread safety" below); under a
module-global counter it is a debugging session about lost increments.

Why the seven members are declared here, not derived from ``GAMEPLAY_SCHEMAS``
------------------------------------------------------------------------------

:data:`graphia.llm.GAMEPLAY_SCHEMAS` is the project's single declaration of the
structured-output schema vocabulary, and consumers are told to derive from it
rather than retype it. This module does **not**, and the reason is not
laziness:

1. **Cost, measured.** ``import graphia.llm`` pulls ``langchain_aws``,
   ``langchain_ollama``, ``boto3`` and ``botocore`` — 130 modules, 0.45s —
   because ``llm.py`` constructs its vendor clients at module scope. A module
   whose entire job is to name seven strings and hold one function reference
   must not drag the LLM stack in behind it. Keeping this module a leaf with no
   ``graphia`` imports at all also makes it import-cycle-proof for Task 6.2,
   which wires it into three node modules that already import ``llm``.
2. **They are different vocabularies with different lifetimes.**
   ``GAMEPLAY_SCHEMAS`` is a tuple of *Pydantic classes*; :class:`SubstitutionKind`
   is a set of *stable string keys* that appear in log lines and, from Slice 7,
   in committed ledger records. A schema could be renamed — ``Reflection`` to
   ``EndOfRoundThought``, say — without the recorded key changing, and that is
   a feature rather than an oversight: a ledger key that shifts underneath
   historical records is a defect, because it silently re-partitions data
   nobody re-measured.
3. **The 1:1 correspondence is enforced by test, not by derivation.** Task 6.3
   asserts that this vocabulary covers ``GAMEPLAY_SCHEMAS`` exactly, and a test
   module can afford the heavy import that this one cannot. This is the same
   trade ``tests/conftest.py`` records for ``FakeLargeUnified``'s queue map,
   and the same one ``eval_ledger.py`` is told to make in Slice 7 (Task 7.2:
   duplicate the names, pin the agreement offline, keep the model-free viewer
   model-free).

The declaration ORDER mirrors ``GAMEPLAY_SCHEMAS`` and is load-bearing for the
same reason it is there: Slice 7 renders one row per kind, so this is display
order. Append rather than insert.

Thread safety: what is and is not promised
-------------------------------------------

**Promised.** Installing a sink is a single store to a module attribute and
each notification is a single load of it, so a notifier running concurrently
with :func:`set_sink` sees either the old sink or the new one — never a
half-installed one and never ``None``. And the two notifications are
independent by construction: this module never pairs an attempt with its own
substitution, never correlates them, and keeps no per-call state, so concurrent
substitutions interleave (``ATTEMPT, ATTEMPT, SUBSTITUTION, SUBSTITUTION``)
without any total going wrong. That is why Task 6.3's "two players failing
books two for one kind" holds regardless of how the two overlap — the counter
counts items, not fan-outs, and not pairs.

**Not promised, first: the sink's own safety.** ``counts[kind] += 1`` is a
read-modify-write and is not atomic; on a free-threaded build two concurrent
notifications can lose an increment. Whoever installs an accumulating sink owns
its locking. Today nothing exercises this — the whole synchronous graph runs
inside one ``asyncio.to_thread`` worker, so no two of the seven helpers ever
overlap — but that is a fact about the current caller, not a promise from here.

**Not promised, second: isolation.** :func:`set_sink` is process-global, not
per-thread, per-task or per-game. Two games running concurrently in one process
would share one sink and interleave their counts; the harness runs them
sequentially, and owns that boundary. When concurrency does arrive, the fix
stays inside this file: hold the sink in a :class:`contextvars.ContextVar`
instead of a module attribute. That this works across the existing seam is
checked rather than assumed — ``asyncio.to_thread`` copies the calling
context into the worker thread (documented, and exercised locally), so a sink
installed on the event loop is visible to the graph running in the worker.
Writes made inside the worker do not travel back out, which is irrelevant to a
reference that is only ever read there.

Where the log line actually goes
--------------------------------

:func:`note_substitution` logs through the stdlib :mod:`logging`, like every
other diagnostic in the engine. Be precise about the destination, because the
obvious reading is wrong: ``GRAPHIA_LOG_FILE`` is written by
:class:`graphia.logging.StreamTraceLogger` as JSONL graph-stream deltas, and
NOTHING in the local game path (or in ``blunder_eval``) installs a
:mod:`logging` handler — only ``runtime/observability.py`` does, on the
AgentCore side. So today these records reach ``logging.lastResort`` on stderr,
which Textual redirects while the TUI is running. Task 6.5 asks for the log
line to be confirmed in the game's log file; that will need a handler wired up
first, and the same caveat already applies to ``_ai_diary``'s two existing
lines despite the comment there describing them as "diagnostic under the game's
log file".

Purity
------

No LLM call, no game state, no ``graphia`` imports, no I/O beyond
:mod:`logging`, and exactly one piece of module state: the sink reference.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

logger = logging.getLogger(__name__)

__all__ = [
    "SubstitutionKind",
    "SubstitutionEvent",
    "SubstitutionSink",
    "default_sink",
    "note_attempt",
    "note_substitution",
    "reset_sink",
    "set_sink",
]


class SubstitutionKind(StrEnum):
    """The seven answer kinds a substitution can stand in for.

    Held 1:1 with :data:`graphia.llm.GAMEPLAY_SCHEMAS` — see the module
    docstring for why that correspondence is asserted by test rather than
    produced by derivation, and why these values are keys with a longer
    lifetime than the schema classes they shadow.

    Each member names the schema whose answer failed, not the helper that asked
    for it, because the schema is what the ledger reader recognises and what
    the negative controls in Task 6.4 are written against. The helpers, for the
    record: ``ROSTER`` is ``_generate_names``, ``PERSONA`` is
    ``_generate_one_persona`` (both ``nodes/setup.py``), ``POINTING`` is
    ``_ai_pick_target`` (``nodes/night.py``), and ``BALLOT``, ``DAY_ACTION``,
    ``REFLECTION`` and ``DIARY`` are ``_ai_ballot``, ``_ai_day_action``,
    ``_ai_reflect`` and ``_ai_diary`` (all ``nodes/day.py``).

    A :class:`~enum.StrEnum` rather than a plain :class:`~enum.Enum` so a
    member IS its ledger key: ``f"{kind}"``, ``json.dumps`` and a YAML dump all
    produce ``"diary"`` with no ``.value`` at the call site to forget, and a
    member hashes and compares equal to its own string, so a dict keyed by
    members cannot grow a second bucket for the same kind spelled as a string.
    """

    ROSTER = "roster"
    PERSONA = "persona"
    POINTING = "pointing"
    BALLOT = "ballot"
    DAY_ACTION = "day_action"
    REFLECTION = "reflection"
    DIARY = "diary"


class SubstitutionEvent(StrEnum):
    """Which of the two things happened — the sink's discriminator.

    Both :func:`note_attempt` and :func:`note_substitution` reach the same
    single callable, so the callable has to be told which one called it. This
    enum is that answer, in preference to two alternatives:

    - **A boolean** (``sink(kind, True)``) — the classic unreadable flag
      argument, and unreadable twice over here, since neither "True means
      substituted" nor "True means attempted" is the obvious reading.
    - **Two sink callables**, or a two-method Protocol — a wider seam for no
      gain, and one a harness can half-install.

    Naming an event also keeps a recording sink's captured list legible, which
    is what Task 6.3's seven-way sweep asserts against: the pairs read
    ``(ATTEMPT, DIARY), (SUBSTITUTION, DIARY)`` rather than ``(DIARY, False),
    (DIARY, True)``.
    """

    ATTEMPT = "attempt"
    SUBSTITUTION = "substitution"


type SubstitutionSink = Callable[[SubstitutionEvent, SubstitutionKind], None]
"""What :func:`set_sink` accepts: ``(event, kind) -> None``, positional.

Discriminator first, subject second — the tag leads, as it does in every other
tagged payload in the project, and it reads in call order: "attempt, of a
diary". The parameter *names* are not part of the contract; a sink may spell
them however it likes, including as a two-argument lambda.

Deliberately NOT a richer event object. A frozen dataclass carrying kind, event
and room for a player id or a timestamp would extend more gracefully, but
nothing in Slices 6 or 7 needs a third field, and inventing the extension point
before the need is exactly the capability-for-its-own-sake this project
rejects. Two positional arguments are also what makes a throwaway recording
sink a one-liner in a test.

A sink is called on the thread that substituted and must not raise — see
:func:`set_sink`.
"""


def default_sink(event: SubstitutionEvent, kind: SubstitutionKind) -> None:
    """The production sink: emit a log line, accumulate nothing.

    Only :data:`SubstitutionEvent.SUBSTITUTION` is worth a line. Logging every
    attempt would be one record per model call across the whole game — noise
    that would bury the events this module exists to surface.

    Public, and not only as documentation of the default: a harness sink that
    wants the log line as well as the count can chain to this rather than
    reproducing the message, which keeps "``note_substitution`` owns the log
    line" true even when something else is counting.
    """
    match event:
        case SubstitutionEvent.SUBSTITUTION:
            logger.warning(
                "substitution: no usable %s answer from the model; the "
                "deterministic stand-in was used instead",
                kind.value,
            )
        case _:
            # ATTEMPT, and anything a chaining caller invents. Silent by
            # design; see above.
            return None


_sink: SubstitutionSink = default_sink


def set_sink(fn: SubstitutionSink) -> None:
    """Install ``fn`` as the sink for every subsequent notification.

    Process-wide and immediate. The intended caller is the eval harness, which
    installs an accumulating sink around one game and calls :func:`reset_sink`
    afterwards; the intended non-caller is the game itself, which never touches
    this and runs on :func:`default_sink`.

    ``fn`` must not raise. Nothing here catches it — see :func:`note_attempt`
    for why swallowing would be worse than crashing, and for the one placement
    rule that keeps a raising sink from being laundered into a false
    substitution.

    Returns ``None`` rather than the sink it displaced. Nesting sinks is not a
    need anyone has: a harness that acquires one can hold its own reference.

    Raises:
        TypeError: if ``fn`` is not callable. Cheap, and it puts the error at
            the mistake instead of at the next notification, which may be a
            whole game away.
    """
    global _sink
    if not callable(fn):
        raise TypeError(
            f"set_sink expects a callable (event, kind) -> None, got "
            f"{type(fn).__name__}. To silence notifications, install a sink "
            f"that does nothing; to restore the logging default, call "
            f"reset_sink()."
        )
    _sink = fn


def reset_sink() -> None:
    """Restore :func:`default_sink` — the production default, not a no-op.

    "Reset" means back to the factory setting. Clearing to a silent no-op was
    the alternative and is wrong on three counts:

    - The autouse fixture in Task 6.3 calls this in teardown. If it cleared to
      a no-op, every test that did not install a sink would run with the
      instrumentation dead, and :func:`default_sink` — production's actual
      behaviour — would be exercised only by the tests that went out of their
      way to reinstate it.
    - A single stray call in the app would otherwise cost the game its
      substitution logging permanently, with nothing left to notice the loss.
    - Restoring a fixed point makes :func:`set_sink` / ``reset_sink`` an
      order-insensitive pair: two resets in a row, or a reset with no prior
      set, both leave the same state.

    Silence, where somebody genuinely wants it, is ``set_sink`` with a sink
    that does nothing — which says so at the call site.
    """
    global _sink
    _sink = default_sink


def _validated(kind: SubstitutionKind) -> SubstitutionKind:
    """Coerce ``kind`` to a member, or fail loudly.

    This is where the "no silent eighth bucket" guarantee actually lives, and
    it is worth being exact about how little the :class:`~enum.StrEnum` gives
    for free. The annotation is not a runtime check: ``note_attempt("dairy")``
    from an untyped caller — a test, the harness, anything assembling a kind
    from data — would otherwise sail through and reach an accumulating sink as
    a brand-new key, producing an eighth row in a ledger block that claims to
    have seven.

    What ``StrEnum`` DOES contribute is that the check costs nothing for
    legitimate callers: ``SubstitutionKind(SubstitutionKind.DIARY)`` returns the
    member unchanged, and ``SubstitutionKind("diary")`` resolves by value. So
    the two cases separate cleanly — a known kind is normalised to a member
    however it was spelled, an unknown one raises — and the sink is guaranteed
    to receive a member, never a bare string.

    Raises:
        ValueError: if ``kind`` is not one of the seven. The enum's own
            constructor is the validator; the message is widened to name the
            vocabulary and where it comes from, because "'dairy' is not a valid
            SubstitutionKind" tells a reader what broke but not what to do.
    """
    try:
        return SubstitutionKind(kind)
    except ValueError as exc:
        known = ", ".join(k.value for k in SubstitutionKind)
        raise ValueError(
            f"{kind!r} is not a SubstitutionKind. The vocabulary is closed and "
            f"held 1:1 with llm.GAMEPLAY_SCHEMAS: {known}. A new answer kind "
            f"needs a member on the enum, not a free-form string."
        ) from exc


def note_attempt(kind: SubstitutionKind) -> None:
    """Record that a ``kind`` answer was asked of the model — the denominator.

    Called on entry to each of the seven helpers, once per answer wanted. It
    counts ITEMS, not fan-outs: a Day on which four AI players each write a
    diary entry books four ``DIARY`` attempts, because the four can fail
    independently and a rate over "Days" would hide three of them.

    Emits no log line. Every attempt logged would be every model call logged.

    **Placement rule for the call sites (Task 6.2): put this call before the
    helper's ``try``, not inside it.** A defective sink that raises should
    surface as a crash naming the sink, and inside the ``try`` it would instead
    be swallowed by the helper's own ``except Exception`` and returned as a
    substitution — the measurement manufacturing the event it is there to
    measure. Notifications are not wrapped in a guard here precisely so that
    failure stays loud; the production default cannot raise (:mod:`logging`
    absorbs its own handler errors), so the exposure is a harness bug, and
    hiding a harness bug produces the silent miscount this whole slice exists
    to prevent.

    Raises:
        ValueError: if ``kind`` is not one of the seven (see :func:`_validated`).
    """
    _sink(SubstitutionEvent.ATTEMPT, _validated(kind))


def note_substitution(kind: SubstitutionKind) -> None:
    """Record that a ``kind`` answer was replaced by its stand-in, and log it.

    Called on the substitution branch — the one that returns the deterministic
    fallback — and therefore always AFTER any retry, so a kind that retries
    once and a kind that does not produce the identical pair of events. Each
    call is a subset of one :func:`note_attempt`; the two are never paired or
    correlated by this module, only counted, which is what keeps the totals
    correct when several substitutions overlap.

    This function owns the substitution log line, by way of
    :func:`default_sink`. That is the point of routing the log through the sink
    rather than emitting it here directly: there is one place that knows a
    substitution happened, so a count and a log cannot disagree, and a harness
    that replaces the sink is choosing what happens to both at once.

    Raises:
        ValueError: if ``kind`` is not one of the seven (see :func:`_validated`).
    """
    _sink(SubstitutionEvent.SUBSTITUTION, _validated(kind))
