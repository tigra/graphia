"""Real-Ollama structured-output smoke — ADR-010's verify-at-implementation gate.

The Ollama provider reaches the model over Ollama's **native** surface
(``langchain_ollama.ChatOllama``) under a JSON-schema ``format`` — a decoding
grammar the server enforces, not a request it may decline. The open question
ADR-010 deliberately deferred was whether **structured output** works reliably
over the local path with small models, and this harness is what answered it:
on the shim ADR-010 originally chose (Ollama's Anthropic-compatible
``/v1/messages`` endpoint, where structured output was tool use) the answer was
no — ``tool_choice`` was accepted and silently dropped — and spec 041 Slices 2
and 3 replaced that transport on this evidence (ADR 013). The harness keeps
reporting on whatever transport is current — it *reports*, it does not decide:
if a model pair comes back UNRELIABLE, changing transport again is a deliberate
follow-up decision, not something this tool performs silently.

Like ``eval_dialogue`` (whose driver pattern this reuses), it is deliberately
**not** a pytest test: it reaches a real local LLM server and is run on
demand, outside the mocked suite:

    make ollama-smoke                              # configured/default pair
    make ollama-smoke ARGS="--models qwen2.5:7b,qwen2.5:3b --models llama3.1:8b,qwen2.5:3b"
    make ollama-smoke ARGS="--max-rounds 2 --json smoke.json"

For each LARGE,SMALL pair it (a) runs the same fail-fast preflight the game
boots with, then (b) drives ONE scripted game against the real local provider
while counting, per structured-output schema, how many raw
``with_structured_output(...).invoke(...)`` attempts parsed cleanly vs failed.
The game's own retry-then-deterministic-fallback logic masks parse failures
from the *game's* perspective — the counting proxy sits underneath that logic,
so masked failures stay visible in the report.

What the verdict requires, and why it is four conditions rather than one
------------------------------------------------------------------------

Spec 041 Slice 11 — ADR-013's own acceptance condition. Until Slice 11 the
verdict was a failure-RATE check over a hand-typed tuple of FOUR schema names,
and it passed a full game whose ``Diary`` failure rate was 80% — four times its
own advisory threshold — with exit code 0. Four independent holes sat in one
``if``: the tuple omitted ``Persona``, ``Reflection`` and ``Diary``; a schema
missing from the stats map was skipped (``s is not None``); a schema present
with ZERO attempts was treated as passing (a falsy ``and s.attempts``); and the
bound asserted was a rate under 0.20 rather than no failures at all. A fifth
hole sat one level down, in the install: the counting proxies were assigned to
``llm._large`` / ``llm._small``, which is not where persona generation reads
its model from — see :class:`_CountingOllamaProvider`.

So the verdict is now the conjunction of four conditions, each of which was
separately absent, and each of which prints its own reason when it fails:

1. **Every schema in** :data:`graphia.llm.GAMEPLAY_SCHEMAS` **was exercised** —
   ``attempts > 0``, with a schema absent from the stats map read as
   un-exercised rather than skipped, and the un-exercised ones NAMED. A gate
   that never asked for a ``Diary`` has not reported on diary decoding; the
   old code called that RELIABLE.
2. **Zero failures per schema**, not a rate under the advisory threshold. The
   threshold survives as reported context — it separates "past the advisory
   bound" from "under it but still nonzero" in the reason text — but it no
   longer decides. Under a server-enforced grammar the honest expectation is
   that nothing fails, so any failure is a finding.
3. **The provider seam resolved** ``method="json_schema"``: the mechanism, read
   off the provider actually installed for this run. Zero failures under
   tool-use decoding would be a weaker claim than zero failures under a
   grammar, and telling the two apart is the whole of ADR-013.
4. **The substitution census reads zero** for all seven answer kinds. NOT
   implied by (1) and (2): the raw-invoke counter sees a *transport* failure,
   while ``graphia.substitution`` sees the *game* deciding no usable answer
   arrived. A validator rejecting a well-formed answer substitutes without any
   parse failure at all. Two instruments, two questions, both required.

Conditions (3) and (4) fail CLOSED — an unobserved method or an absent census
is not a clean measurement, because "the instrument was never installed" and
"the instrument read zero" are exactly the two things hole (c) above conflated.

Reachability: why one schema needs a probe rather than luck
-----------------------------------------------------------

Condition (1) is only usable if the scripted game can actually REACH all seven
surfaces, and one of them it cannot. A vote-to-execute is initiated in exactly
two ways — the human typing ``/vote <name>``, or an AI turn returning
``DayAction(kind="vote")`` — and the scripted human's line pool
(``eval_dialogue.HUMAN_LINES``) is deliberately non-committal, so ``Ballot``
has NO guaranteed path. Measured over four live runs of the finished gate: it
passed ONE. The other three reported ``Ballot`` at zero attempts — once with
the human alive through seven answered Day rounds and no vote initiated, and
twice with the human killed on Night 1, which leaves the game AI-only with ZERO
human turns to script. Those runs made 149 combined ``DayAction`` invokes and
not one of them chose to vote.

So a scripted ``/vote`` turn would fix the first shape and CANNOT fix the
second: when the Mafia's LLM-chosen Night-1 target is the scripted human, there
is no human turn to script. And a gate that cries wolf three times in four
trains its reader to ignore it — which is exactly how the
0.80-``Diary``-returns-RELIABLE defect survived a whole measured campaign. A
gate that cannot pass when everything is fine defeats its own value statement
as thoroughly as one that passes when nothing is.

The fix is :func:`_probe_ballot_if_unexercised`: when — and ONLY when — the
game left ``Ballot`` unexercised, the harness makes one direct call to
production's own ``nodes.day._ai_ballot`` on the game's FINAL state, through the
same counted client and the same substitution census, with the six prompt flags
bound off the same ``GraphiaConfig`` the graph binds them from. So the attempt
is a real structured-output decode of a real ``Ballot`` prompt rather than a
synthetic one, and it is reported AS a probe (``ballot probe:`` on screen,
``ballot_probe`` in the JSON) so nobody reads it as in-game evidence.

This does NOT weaken condition (1), and the difference matters: the probe
SUPPLIES the missing attempt, it does not excuse its absence.
``require_all_schemas`` still demands ``attempts > 0`` with zero failures, and a
probe that cannot run — no AI player, no second player, an exception before the
counted invoke — leaves ``Ballot`` at zero and the gate still refuses. Two
residuals remain, stated on the ``make help`` line where the operator meets
them: on a run where the game initiated no vote, ``Ballot``'s evidence is ONE
out-of-game decode rather than a whole in-game poll, and the other six schemas
are reachable only because ``--max-rounds`` defaults past the Day->Night hinge.

Instrumentation is entirely tool-side: the production ``graphia.llm`` module
already exposes ``_active_provider`` / ``_large`` / ``_small`` as in-process
override seams (the same seams ``repetition_experiment`` uses), so we install
counting proxies there without touching production code. No AWS path is ever
constructed — ``GRAPHIA_LLM_PROVIDER=ollama`` is forced in-process and the
``OllamaProvider`` clients are built directly.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langgraph.types import Command

# Reuse the established make-gated harness driver (scripted human, stream-to-
# interrupt pump) rather than re-implementing it.
from graphia.tools.eval_dialogue import (
    HUMAN_LINES,
    HUMAN_NAME,
    _collect_interrupt,
    _drive,
)

# The counting proxy + per-schema stats live in the shared instrument module
# (tech-spec 011 §2.2): ``ollama_smoke`` is just one consumer — it installs the
# proxy with a ``stats`` map and NO speaker resolver, so it counts only. The
# counting semantics here are byte-for-byte the ones this harness was built on;
# they simply moved (Slice 3, Task 1).
from graphia.tools.instrument import InstrumentedModel, SchemaStats

# THE ONE PRODUCTION IMPORT THIS MODULE MAKES AT IMPORT TIME, and a deliberate
# exception to the local-import discipline the rest of the file keeps (see
# ``_derive_interrupt_budget``). Three names are needed at module scope and
# none can wait for ``main``: ``GAMEPLAY_SCHEMAS`` because ``SCHEMA_NAMES``
# below is derived from it rather than typed, ``Ballot`` because
# ``BALLOT_SCHEMA_NAME`` is derived from it for the same reason, and
# ``OllamaProvider`` because ``_CountingOllamaProvider`` subclasses it.
#
# Safe, checked rather than assumed: ``graphia.llm`` reads NO environment at
# module scope, resolves no provider (``_active_provider`` starts ``None``) and
# constructs no client — ``_resolve_provider`` runs on the first factory call,
# which happens long after ``main`` has forced ``GRAPHIA_LLM_PROVIDER=ollama``
# and cleared the cloud store ids. The discipline exists to keep provider
# SELECTION out of import time, and importing names does not select. The
# sibling harness ``tools/blunder_eval.py`` imports this same module at module
# scope for the same reason and has done since spec 011.
from graphia.llm import (
    GAMEPLAY_SCHEMAS,
    Ballot,
    OllamaProvider,
    StructuredModel,
)

# The substitution census's source (spec 041 Slice 6). A LEAF module — no
# ``graphia`` imports at all, no env, no I/O beyond ``logging`` — so this costs
# nothing at import and raises no discipline question. ``default_sink`` is
# public precisely so ``_SubstitutionCensus`` can chain to it and keep
# production's substitution log line while counting.
from graphia.substitution import (
    SubstitutionEvent,
    SubstitutionKind,
    default_sink,
    reset_sink,
    set_sink,
)

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from graphia.config import GraphiaConfig
    from graphia.state import PlayerState

# ---------------------------------------------------------------------------
# The structured-output surfaces under test — DERIVED, never typed.
#
# This tuple was hand-typed as ``("Roster", "Pointing", "Ballot", "DayAction")``
# and was missing THREE of the seven (``Persona``, ``Reflection``, ``Diary``),
# which is how ADR-013's gate returned RELIABLE on a game whose ``Diary``
# failure rate was 0.80. Deriving it from ``llm.GAMEPLAY_SCHEMAS`` — the
# project's single declaration of the schema vocabulary — makes an eighth
# schema appear here automatically instead of silently going unmeasured.
#
# ``__name__`` because that is exactly the key ``instrument._InstrumentedStructured``
# books stats under (``schema.__name__`` for a class), so the two ends of the
# instrument cannot drift. Declaration order is preserved because
# ``GAMEPLAY_SCHEMAS`` states that its order is display order and this table is
# the display it means.
# ---------------------------------------------------------------------------
SCHEMA_NAMES: tuple[str, ...] = tuple(schema.__name__ for schema in GAMEPLAY_SCHEMAS)

# The ONE schema in that vocabulary with no guaranteed path through a scripted
# game — see the docstring's reachability section and
# :func:`_probe_ballot_if_unexercised`. Derived from the class for the same
# reason ``SCHEMA_NAMES`` is derived: a rename must not silently turn the probe
# into a lookup that never matches, which would put the gate straight back to
# passing one run in four.
BALLOT_SCHEMA_NAME: str = Ballot.__name__

# The structured-output method the gate requires the provider seam to have
# resolved — ADR-013's grammar-constrained decoding. Named here rather than
# spelled inline so the reason text, the printed line and the JSON payload all
# quote the same string.
EXPECTED_STRUCTURED_OUTPUT_METHOD = "json_schema"

# Advisory failure-rate threshold. NO LONGER THE PASS/FAIL BOUND: the gate
# requires ``failures == 0`` per schema (see ``_unreliable_reasons``), because
# 0.20 is four times the rate that disqualified a real arm while the old
# ``failure_rate > threshold`` test called it RELIABLE. What the threshold still
# does is give a nonzero failure count a stated frame of reference — "past the
# 20% advisory bound" reads differently from "under it, but the gate requires
# zero" — so it is reported, not enforced.
DEFAULT_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Counting proxy install — the instrumentation seam.
#
# The proxy pair (``InstrumentedModel`` / per-schema ``SchemaStats``) lives in
# ``graphia.tools.instrument`` now (tech-spec 011 §2.2); this harness is the
# count-only consumer. It builds the proxy with a ``stats`` map and no speaker
# resolver, so the counting behavior — and the RELIABLE/UNRELIABLE verdict it
# feeds — is byte-for-byte what it was before the extraction.
# ---------------------------------------------------------------------------


class _CountingOllamaProvider(OllamaProvider):
    """``OllamaProvider`` whose THREE tier factories hand back counted clients.

    **Why a provider subclass and not two assignments to ``llm._large`` /
    ``llm._small``** — which is what this harness did until spec 041 Slice 11,
    and it is the fifth hole in the gate. ``llm.get_persona_model`` does not
    read those cache slots: it calls
    ``_resolve_provider().large_at_temperature(t)`` and builds a FRESH client
    per persona-generation pass, because spec 034 runs personas hotter than
    gameplay. So a harness that instruments only the two cached slots counts
    every schema EXCEPT ``Persona`` — which is exactly why ``Persona`` was
    absent from the stats map of the run that motivated this slice, and why
    "absent" had to be read as un-exercised rather than skipped. Deriving
    ``SCHEMA_NAMES`` from the enum without this fix would have made the gate
    unsatisfiable rather than correct.

    Wrapping at the provider covers the persona path in BOTH flag arms:
    ``GRAPHIA_PERSONA_DIVERSITY`` on routes through ``large_at_temperature``,
    off routes through the cached ``large()``.

    Overriding the three CONCRETE template methods rather than the ``_build_*``
    hooks is deliberate on two counts. It yields
    ``InstrumentedModel(StructuredMethodModel(ChatOllama))`` — the exact
    arrangement ``graphia.llm``'s own docstring documents for this harness and
    that ``tests/test_structured_output_method.py`` pins as transparent — so no
    new nesting order is invented here. And it leaves the provider's own
    wrapping untouched, so what gets counted is a client built exactly the way
    production builds it, with the method pinned by production code rather than
    by the measurement.
    """

    def __init__(self, stats: dict[str, SchemaStats]) -> None:
        super().__init__()
        self._stats = stats

    def large(self) -> StructuredModel:
        return InstrumentedModel(super().large(), stats=self._stats)

    def large_at_temperature(self, temperature: float) -> StructuredModel:
        return InstrumentedModel(
            super().large_at_temperature(temperature), stats=self._stats
        )

    def small(self) -> StructuredModel:
        return InstrumentedModel(super().small(), stats=self._stats)


def _install_counting_provider(
    stats: dict[str, SchemaStats],
) -> _CountingOllamaProvider:
    """Point ``graphia.llm``'s module-level seams at counted Ollama clients.

    Uses the documented override seams (``_active_provider`` / ``_large`` /
    ``_small``) — the same ones ``repetition_experiment`` rebuilds — so no
    production code changes and no Bedrock client is ever constructed. The
    proxies count only (``stats`` supplied, no ``speaker_resolver``); raw
    capture is a different consumer's concern (Slice 3, Task 2).

    ``_active_provider`` now carries the counting provider itself, so the one
    tier factory that bypasses the cache slots (``get_persona_model``) is
    counted too. Returns the installed provider so the caller can record the
    structured-output method it resolved.
    """
    import graphia.llm as llm_mod

    provider = _CountingOllamaProvider(stats)
    llm_mod._active_provider = provider
    llm_mod._large = provider.large()
    llm_mod._small = provider.small()
    return provider


def _resolved_structured_output_method(provider: OllamaProvider) -> str | None:
    """The structured-output method the INSTALLED provider seam resolved.

    Read off the provider object this run actually installed — not a freshly
    constructed one — because the question the gate asks is "what did the
    clients this game used carry?", and a fresh construction would answer a
    different question that cannot fail.

    ``None`` when the seam resolved no method at all, which is what a Bedrock
    provider returns and what a dropped pin would look like. The gate treats
    that as a failure rather than as "no data".

    Deliberately NOT a wire-level assertion. Proving that the bound runnable
    carries ``format == Schema.model_json_schema()`` for each of the seven is
    ``tests/test_structured_output_method.py``'s job, it needs no live model,
    and re-doing it here would couple this harness to LangChain's internal
    runnable shape for a fact already pinned offline. What this harness adds is
    the live half: seven schemas exercised with zero failures, under the method
    named here.
    """
    return provider.structured_output_defaults().get("method")


@dataclass(slots=True)
class _SubstitutionCensus:
    """An accumulating substitution sink for ONE scripted game, chaining on.

    The harness half of ``graphia.substitution``'s injectable-sink design, and
    the fourth of the gate's four conditions. It exists because ``attempts > 0,
    failures == 0`` does **not** imply no substitution: the raw-invoke counter
    above sees the transport, while the seven helpers' own substitution
    branches see the game deciding no usable answer arrived. A validator
    rejecting a well-formed, cleanly-decoded answer books a substitution and no
    parse failure whatsoever.

    IT CHAINS TO :func:`~graphia.substitution.default_sink`, which is public
    precisely so it can. Replacing the sink replaces what happens to the count
    AND to the log line at once (``note_substitution`` owns both), so a sink
    that only counted would take production's substitution warning away from
    the one run that most wants it. The count is booked BEFORE the chain, so
    even a logging failure cannot lose it.

    Reports all seven kinds INCLUDING zeros, which is the opposite of the
    ledger's "absent, never a misleading zero" rule — deliberately, and the
    difference is what the number is for. The ledger PUBLISHES a rate, so a
    ``0.0`` over no attempts would assert a clean measurement of something that
    never ran. This census is ASSERTED against, so every kind has to appear:
    a missing key here means the instrument was not installed, and the gate has
    to be able to tell that apart from a genuine zero. Hence
    ``_unreliable_reasons`` requires the full seven and fails closed on a short
    census.

    NO LOCKING, stated rather than assumed: ``counts[kind] += 1`` is a
    read-modify-write. The whole synchronous graph runs inside one
    ``asyncio.to_thread`` worker, so no two of the seven helpers overlap today.
    """

    attempted: Counter[str] = field(default_factory=Counter)
    substituted: Counter[str] = field(default_factory=Counter)

    def __call__(self, event: SubstitutionEvent, kind: SubstitutionKind) -> None:
        """Count, then chain — the sink contract's ``(event, kind) -> None``.

        Must not raise: ``substitution.py`` guards no notification, so an
        exception here would surface from inside a game node and, worse, be
        swallowed by the helper's own ``except Exception`` and returned as a
        fallback — the measurement manufacturing the event it measures.
        """
        match event:
            case SubstitutionEvent.ATTEMPT:
                self.attempted[kind.value] += 1
            case SubstitutionEvent.SUBSTITUTION:
                self.substituted[kind.value] += 1
            case _:  # pragma: no cover - a future event kind, chained on only
                pass
        default_sink(event, kind)

    def as_report(self) -> dict[str, dict[str, int]]:
        """All seven kinds, each with its two counts — the report-ready shape.

        Iterates :class:`~graphia.substitution.SubstitutionKind` rather than the
        counters, so a kind that never fired is present with zeros instead of
        missing. A fresh census's ``as_report()`` is therefore the canonical
        "clean" census, which is also the cheapest way for an offline test to
        build one without retyping seven keys.
        """
        return {
            kind.value: {
                "attempted": self.attempted[kind.value],
                "substituted": self.substituted[kind.value],
            }
            for kind in SubstitutionKind
        }


# ---------------------------------------------------------------------------
# The driver loop's backstop budget — derived from the lineup, never remembered.
#
# ``_run_scripted_game`` answers one interrupt per iteration, so it needs a
# bound: a genuinely wedged graph must end the run rather than spin against a
# real local model forever. That bound used to be ``max_rounds * 12 + 20`` —
# sized per *Day round*, with the player count nowhere in it. That is the wrong
# unit twice over. A Day round costs one super-step per ALIVE SPEAKER, and the
# number of Days a game runs before the Mafia reach parity is itself a function
# of the lineup (``total - 2 * num_mafia`` Nights), so the cost of driving a
# whole game is **quadratic** in table size, not linear. Spec 042's Task 4.1
# measured it against production's real caps:
#
#     players      6    7    8    9   10   11   12
#     super-steps  8   17   63  115  179  240  313
#
# The 7 -> 8 jump is the one this spec causes: at five-and-two the Mafia reach
# parity during Night before any Day runs to its round cap, whereas at
# six-and-two a fourth Night is needed with full six-round Days in between. Any
# budget expressed as a constant — or as a function of rounds alone — therefore
# re-tightens every time the table grows.
#
# So the budget below is derived from the resolved lineup and production's OWN
# caps (``DAY_MAX_ROUNDS`` / ``DAY_MAX_VOTES`` / ``NIGHT_ROUND_CAP`` /
# ``config.max_days``), and it is expressed in **super-steps**. That unit is
# deliberate: it bounds loop iterations without any assumption about how many
# super-steps one ``_drive`` batch absorbs, because every iteration either ends
# the loop or advances the graph by at least one super-step. "Super-steps in
# the longest legal game" is therefore an upper bound on the iterations the
# loop can need. Derived vs. measured across the whole legal range:
#
#     players      6    7    8    9   10   11   12
#     derived    312  420  540  672  816  972 1140
#
# The headroom is deliberate and costs nothing on a healthy run: the loop stops
# on the round cap or the natural game end after a few dozen iterations, so the
# backstop only ever binds when something is actually wrong — and when it does,
# the exhaustion message says so in those terms rather than blaming the budget.
#
# NOTE FOR THE RECORD, so nobody re-opens it: ``blunder_eval`` needs **no**
# equivalent change. Its anti-hang loop is already Day-cap-derived
# (``range(max_days * 60 + 40)``), carrying a comment that reasons explicitly
# about the cost of a Day's speaking/vote sub-graph "at the largest table".
# Spec 014's pre-registered concern about "the eval per-interrupt budget sized
# for 7 players" is **closed, not live** for that harness — it was only ever
# live here, in this module.
# ---------------------------------------------------------------------------

# Fixed per-cycle graph nodes that cost one super-step each whatever the table
# size: ``day_open``, both win-checks, ``day_close``, ``day_diary``,
# ``night_open``, ``night_resolve``, the reflect/round-wrap steps and the end
# screen. Twelve is the allowance ``tests/conftest.py`` independently settled on
# for the same node set.
_PHASE_STEPS_PER_CYCLE = 12


@dataclass(frozen=True, slots=True)
class InterruptBudget:
    """A super-step backstop for driving one scripted game at one lineup.

    Kept as a value object rather than a bare ``int`` so the exhaustion message
    can name every term of the derivation. A future failure there should be
    diagnosable from the message alone — "which term was too small at this
    lineup?" — instead of a bare "budget exhausted".
    """

    total_players: int
    num_mafia: int
    cycles: int
    speech: int
    ballots: int
    night: int
    phase: int

    @property
    def total(self) -> int:
        return self.speech + self.ballots + self.night + self.phase

    def describe(self) -> str:
        citizens = self.total_players - self.num_mafia
        return (
            f"{self.total} super-steps derived for lineup {citizens}+"
            f"{self.num_mafia} = {self.total_players} players over <= "
            f"{self.cycles} Day/Night cycles (speech {self.speech} + ballots "
            f"{self.ballots} + night {self.night} + phase {self.phase})"
        )


def _derive_interrupt_budget(config: GraphiaConfig) -> InterruptBudget:
    """Size the driver loop's backstop from the lineup and production's caps.

    Every term reads a production constant rather than a remembered table, so a
    larger lineup widens the budget instead of silently tightening it.
    """
    # Local imports for the reason nearly every production import in this
    # module is local: nothing that touches the game package should be pulled in
    # before ``main`` has forced ``GRAPHIA_LLM_PROVIDER=ollama`` and cleared the
    # cloud store ids. The two module-scope exceptions (``graphia.llm``'s schema
    # vocabulary and provider class, and the leaf ``graphia.substitution``) are
    # argued at the import site; both read no environment at import.
    from graphia.nodes.day import DAY_MAX_ROUNDS, DAY_MAX_VOTES
    from graphia.nodes.night import NIGHT_ROUND_CAP

    total = config.num_citizens + config.num_mafia
    # Nobody need ever be executed — the scripted human always votes "no" below
    # — so game length is bounded by the Mafia killing one law-abiding player
    # per Night until parity: ``total - 2 * num_mafia`` Nights. Two extra cycles
    # for the partial Day the loop may start inside and the Day the win-check
    # finally fires on. Clamped by production's own runaway Day cap, so this can
    # never claim more Days than the graph will actually run.
    cycles = min(max(1, total - 2 * config.num_mafia) + 2, config.max_days)
    # One player dies per Night, so the speaking roster shrinks by one each
    # cycle: the SUM of alive players across the Days is what a shrinking table
    # costs, which "players x rounds" understates and "one more speaker per
    # round forever" overstates. Floored at two — a Day with fewer than two
    # players alive cannot begin.
    alive_speaker_sum = sum(max(2, total - offset) for offset in range(cycles))

    return InterruptBudget(
        total_players=total,
        num_mafia=config.num_mafia,
        cycles=cycles,
        # One ``day_turn`` super-step per alive speaker per round, plus the
        # round-wrap/reflect step each round ends with.
        speech=DAY_MAX_ROUNDS * (alive_speaker_sum + cycles),
        # Each of a Day's ``DAY_MAX_VOTES`` votes polls the whole table and
        # costs its own open + resolve step.
        ballots=DAY_MAX_VOTES * cycles * (total + 2),
        # Each Night pointing round costs one pick super-step per Mafioso plus a
        # round-wrap; a Night runs at most ``NIGHT_ROUND_CAP`` rounds.
        night=NIGHT_ROUND_CAP * cycles * (config.num_mafia + 1),
        phase=cycles * _PHASE_STEPS_PER_CYCLE,
    )


# ---------------------------------------------------------------------------
# One scripted game per model pair (eval_dialogue's driver pattern).
# ---------------------------------------------------------------------------


@dataclass
class PairReport:
    large: str
    small: str
    preflight_ok: bool = False
    preflight_message: str | None = None
    game_completed: bool = False
    game_error: str | None = None
    # WHY the drive stopped, in the same field for every outcome. Without it a
    # completed run that hit the sampling round cap and a completed run that
    # played to a natural end are indistinguishable in the report — and both are
    # easy to confuse with a run that quietly ran out of backstop budget.
    stop_reason: str = ""
    duration_seconds: float = 0.0
    stats: dict[str, SchemaStats] = field(default_factory=dict)
    # The structured-output method the provider seam resolved for THIS run's
    # clients (gate condition 3). ``None`` means "not observed", which the
    # verdict treats as a failure rather than as no data — see
    # ``_resolved_structured_output_method``.
    structured_output_method: str | None = None
    # ``{kind: {"attempted": n, "substituted": m}}`` for all seven answer kinds
    # (gate condition 4), from the ``_SubstitutionCensus`` installed around the
    # game. Empty means the census was never installed, which the verdict also
    # treats as a failure.
    substitutions: dict[str, dict[str, int]] = field(default_factory=dict)
    # What happened to the ONE schema a scripted game cannot be relied on to
    # reach (Task 11.4). Either "not needed" with the in-game attempt count, or
    # what :func:`_probe_ballot_if_unexercised` did. Reported beside the table
    # rather than folded into it, because "``Ballot`` was decoded" and
    # "``Ballot`` was decoded IN A GAME" are different claims and the reader
    # has to be able to tell which one this run supports.
    ballot_probe: str = ""
    verdict: str = "UNRELIABLE"
    # WHY the verdict reads as it does, one string per breached condition, in
    # the same field for the printed report and the JSON record. A one-word
    # UNRELIABLE that does not say which of the four conditions failed sends
    # the reader back to the table to guess — and on the run that motivated
    # this slice the table did not contain the answer.
    verdict_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScriptedGameOutcome:
    """What one scripted game did, in the four terms the report reads.

    A value object rather than the 3-tuple this used to be, because Task 11.4
    added a fourth term and a four-tuple unpacked at the call site is exactly
    where two silently swapped strings would live. ``ballot_probe`` is
    always populated on a path that ran a game — it says either "not needed"
    with the in-game attempt count, or what the probe did.
    """

    completed: bool
    error: str | None
    stop_reason: str
    ballot_probe: str = ""


def _ballot_probe_pair(
    players: dict[str, PlayerState],
) -> tuple[PlayerState, PlayerState] | None:
    """Pick ``(voter, target)`` for the ballot probe — alive-first, deterministic.

    Pure, and deterministic in roster insertion order (which ``--seed`` fixes),
    so a probe is reproducible for a given game structure. ``None`` means no
    usable pair exists, which the caller reports and the verdict then refuses
    on — the probe adds no gate condition of its own.

    ALIVE-FIRST rather than alive-only, on purpose. At a natural game end the
    survivors can be one side or a single player, and the probe's job is to
    exercise the *decode*, not to stage a legal vote — a dead stand-in still
    renders a real ``Ballot`` prompt, whereas declining to probe would leave
    ``Ballot`` at zero attempts and fail the gate for a reason that says
    nothing about the transport under test.

    The voter must be an AI: ``_ai_ballot`` is the AI branch of
    ``collect_votes``, and a human voter has no model call to make at all.
    """
    ais = [player for player in players.values() if not player.is_human]
    voter = next((p for p in ais if p.is_alive), None) or next(iter(ais), None)
    if voter is None:
        return None
    others = [player for player in players.values() if player.id != voter.id]
    target = next((p for p in others if p.is_alive), None) or next(iter(others), None)
    if target is None:
        return None
    return voter, target


def _probe_ballot_if_unexercised(
    graph,
    run_config,
    config: GraphiaConfig,
    stats: dict[str, SchemaStats],
) -> str:
    """Reach ``Ballot`` when the game did not, and say which of those happened.

    The sixth hole, one level up from the fifth: Task 11.1 made the instrument
    able to SEE ``Persona``; nothing made the script able to REACH ``Ballot``.
    A vote is initiated only by the human typing ``/vote <name>`` or an AI
    returning ``DayAction(kind="vote")``, and the scripted human's lines are
    deliberately non-committal — so across four live runs of the finished gate
    ``Ballot`` came back at zero attempts three times, twice because the Mafia
    killed the scripted human on Night 1 and there were no human turns left to
    script at all. See the module docstring for why a scripted ``/vote`` cannot
    fix that second shape and this can.

    Calls production's ``nodes.day._ai_ballot`` — the private helper that owns
    the ``Ballot`` structured-output call, reached the way this module already
    reaches ``llm``'s override seams — on the game's FINAL state, through the
    counted client and inside the installed substitution census. So the attempt
    lands in the same two instruments an in-game ballot would, and a probe
    whose decode fails is a finding rather than a silent skip.

    Returns a human-readable note, never raising: it is called after the drive
    has already decided the outcome, and a measurement that can take down the
    run it is reporting on is worse than a missing note.
    """
    booked = stats.get(BALLOT_SCHEMA_NAME)
    if booked is not None and booked.attempts:
        return (
            "not needed — the game initiated a vote on its own "
            f"({booked.attempts} in-game {BALLOT_SCHEMA_NAME} attempt(s))"
        )

    # Local import for the same reason every other production import here is
    # local, and after the early return so the cheap path stays cheap.
    from graphia.nodes.day import _ai_ballot

    state = dict(graph.get_state(run_config).values)
    pair = _ballot_probe_pair(state.get("players") or {})
    if pair is None:
        # Leaves ``Ballot`` at zero attempts, so ``require_all_schemas``
        # refuses: fail-closed by construction rather than by a new condition.
        return (
            "could not run — the final state carries no AI player with a "
            f"second player to vote on, so {BALLOT_SCHEMA_NAME} stays "
            "unexercised and the verdict refuses"
        )
    voter, target = pair
    try:
        ballot = _ai_ballot(
            voter,
            target,
            state,
            # The SAME six values ``graph.py`` binds into ``collect_votes``,
            # off the same config object, so the probe renders production's
            # prompt rather than the helper's test-facing defaults. A shorter
            # context window would make the probe an easier decode than the
            # thing it stands in for.
            recap_aware_reasoning_enabled=config.recap_aware_reasoning_enabled,
            role_guidance_enabled=config.role_guidance_enabled,
            context_window=config.context_window,
            context_token_budget=config.context_token_budget,
            private_thoughts=(state.get("private_thoughts") or {}).get(voter.id, []),
            private_thoughts_enabled=config.private_thoughts_enabled,
            diaries=(state.get("private_diaries") or {}).get(voter.id, []),
            diaries_enabled=config.private_diaries_enabled,
        )
    except Exception as exc:
        return (
            f"raised {type(exc).__name__}: {exc} — any attempt it did make is "
            "still counted, so a parse failure shows in the table above"
        )
    return (
        "ran — the game initiated no vote, so ONE direct "
        f"{BALLOT_SCHEMA_NAME} decode was made off the final state "
        f"({voter.name} on {target.name} -> {'yes' if ballot.yes else 'no'}); "
        "that schema's evidence is out-of-game, not an in-game poll"
    )


def _drive_script(
    graph,
    run_config,
    config: GraphiaConfig,
    max_rounds: int,
    budget_override: int | None,
) -> tuple[bool, str | None, str]:
    """The scripted-human drive loop — everything between name and last turn.

    Extracted from :func:`_run_scripted_game` verbatim (Task 11.4) so the
    ballot probe can run AFTER the drive and BEFORE the checkpoint directory
    goes away, without threading a post-step through this loop's six return
    sites.
    """
    try:
        _drive(graph, run_config, {"messages": []})
        first = _collect_interrupt(graph, run_config)
        if not first or first.get("kind") != "name":
            return False, f"no name interrupt: {first!r}", "no name interrupt"
        _drive(graph, run_config, Command(resume=HUMAN_NAME))

        # The backstop is derived from THIS game's resolved lineup, so a
        # bigger table widens it rather than tightening it. See the
        # derivation block above for the unit and the measured comparison.
        budget = _derive_interrupt_budget(config)
        limit = budget.total if budget_override is None else budget_override
        # Observed counts, so an exhaustion message can say what the loop
        # actually did rather than only what it was allowed to do.
        answered = {"day_turn": 0, "vote": 0, "point": 0, "advance": 0}
        used = 0

        rounds = 0
        line_idx = 0
        # NOTE (spec 023): this ``max_rounds`` is a smoke-test *sampling* cap —
        # it bounds how many Day rounds run to exercise the structured-output
        # schemas quickly — NOT the whole-game runaway Day cap
        # (``config.max_days`` / GRAPHIA_MAX_DAYS, the in-game safeguard).
        # Deliberately left as a fast schema-exercise budget; only the
        # blunder-eval harness drives games to their natural end.
        #
        # Spec 042: the loop's ``range`` is a THIRD, distinct cap — the
        # derived anti-hang backstop, not a sampling cap. ``max_rounds``
        # decides how much of the game is measured; the backstop only
        # decides when to give up on a graph that has stopped progressing.
        # Conflating them is what produced the old ``max_rounds * 12 + 20``.
        for _ in range(limit):
            used += 1
            if rounds >= max_rounds:
                # Script ran its course. NOTE: this cap decides how much of
                # the game is measured, and a cap at or below
                # ``DAY_MAX_ROUNDS`` (6) fires INSIDE Day 1 — before the
                # first Day -> Night hinge, so ``day_diary``, the last
                # reflection and the whole Night never run, and ``Diary``,
                # ``Reflection`` and ``Pointing`` go unexercised. That is
                # why ``main`` derives the default as ``DAY_MAX_ROUNDS + 1``
                # instead of the old literal 3: the gate now REQUIRES all
                # seven schemas exercised, so a default that cannot reach
                # three of them would make it unsatisfiable. Named in the
                # stop reason because "which schemas got exercised" is read
                # off this report.
                return (
                    True,
                    None,
                    f"sampling round cap ({rounds}/{max_rounds} Day rounds "
                    f"answered; {used}/{limit} backstop steps used)",
                )
            snapshot = graph.get_state(run_config)
            if not snapshot.next:
                return (
                    True,
                    None,
                    f"natural game end ({rounds}/{max_rounds} Day rounds "
                    f"answered; {used}/{limit} backstop steps used)",
                )
            iv = _collect_interrupt(graph, run_config)
            if iv is None:
                answered["advance"] += 1
                _drive(graph, run_config, None)
                continue
            kind = iv.get("kind")
            if kind == "day_turn":
                resume: str = HUMAN_LINES[line_idx % len(HUMAN_LINES)]
                line_idx += 1
                rounds += 1
            elif kind == "vote":
                resume = "no"  # keep the game alive across rounds
            elif kind == "point":
                options = iv.get("options") or []  # defensive; human is law-abiding
                resume = options[0]["id"] if options else ""
            else:
                return False, f"unexpected interrupt {kind!r}", "unexpected interrupt"
            answered[kind] += 1
            _drive(graph, run_config, Command(resume=resume))
        return (
            False,
            (
                f"interrupt budget exhausted after {used} steps: "
                f"{budget.describe()}"
                + ("" if budget_override is None else f", forced to {limit}")
                + f". Observed: {answered['day_turn']}/{max_rounds} Day "
                f"rounds answered, {answered['vote']} ballots, "
                f"{answered['point']} night points, "
                f"{answered['advance']} non-interrupt advances. The game "
                "neither reached the sampling round cap nor ended "
                "naturally, so read this as a wedged graph first and a "
                "budget too small second — the budget tracks the lineup."
            ),
            "backstop budget exhausted",
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", "exception"


def _run_scripted_game(
    max_rounds: int,
    seed: int,
    *,
    budget_override: int | None = None,
    stats: dict[str, SchemaStats] | None = None,
) -> ScriptedGameOutcome:
    """Drive one game on the (already-installed) counted provider.

    Scripted human: law-abiding (no night-point interrupts), neutral day
    lines, always votes "no" so the game runs long enough to exercise the
    Day-phase schemas. Reports whether the script ran its course — to the round
    cap or a natural game end — without an exception or an unexpected
    interrupt, and ``stop_reason`` says which, because those two endings mean
    different things about what was measured.

    ``stats`` is the SAME counted map the provider books into. Supplied, this
    runs the ballot probe once the drive is done — the fix for the one schema a
    scripted game cannot be relied on to reach (see
    :func:`_probe_ballot_if_unexercised`). Left ``None``, no probe runs and the
    note says so; the verdict then refuses on the zero attempts, unweakened.

    ``budget_override`` exists only to make the exhaustion path reachable on
    demand (forcing a tiny budget proves the message is informative). Leave it
    ``None`` and the backstop is derived from the resolved lineup.
    """
    from graphia.config import load_config
    from graphia.graph import build_graph, make_run_config

    random.seed(seed)  # reproducible game *structure*; LLM output is not

    with tempfile.TemporaryDirectory(prefix="graphia-ollama-smoke-") as ckpt:
        os.environ["GRAPHIA_CHECKPOINT_DIR"] = ckpt
        config = load_config()
        graph, thread_id = build_graph(config)
        run_config = make_run_config(thread_id)

        completed, error, stop_reason = _drive_script(
            graph, run_config, config, max_rounds, budget_override
        )
        # Inside the tempdir context on purpose: the probe reads the game's
        # final state off THIS thread's checkpoint, which vanishes with the
        # directory.
        if stats is None:
            probe = "not run — no stats map was supplied to count into"
        elif not completed:
            probe = (
                "not run — the scripted game did not complete, so the run is "
                "already UNRELIABLE and a probe would only add model time"
            )
        else:
            probe = _probe_ballot_if_unexercised(graph, run_config, config, stats)
        return ScriptedGameOutcome(completed, error, stop_reason, probe)


def _schema_rows(
    report: PairReport, *, schema_names: Sequence[str] | None = None
) -> list[tuple[str, SchemaStats]]:
    """The ``(name, stats)`` rows every surface reads — table, JSON and verdict.

    ONE function, because the human table and the machine record disagreeing is
    a measured defect rather than a hypothetical: the run that motivated this
    slice printed FOUR rows while its JSON carried SIX, since
    ``_print_pair_report`` iterated the hand-typed tuple and ``_json_payload``
    iterated ``report.stats``. Both now call this, and so does the verdict, so
    all three judge and display exactly the same set.

    Vocabulary order first (``GAMEPLAY_SCHEMAS``'s order, which it declares to
    be display order), with a zeroed :class:`SchemaStats` standing in for a
    schema the run never bound — the row has to appear, because "never
    exercised" IS the finding. Then any EXTRA key present in ``stats`` but not
    in the vocabulary, sorted for stability: the old JSON path would at least
    have surfaced such a key, and iterating the tuple alone would silently drop
    it, which is the same class of blindness in the opposite direction.
    """
    names = SCHEMA_NAMES if schema_names is None else tuple(schema_names)
    rows: list[tuple[str, SchemaStats]] = []
    for name in names:
        booked = report.stats.get(name)
        rows.append((name, booked if booked is not None else SchemaStats()))
    for name in sorted(set(report.stats) - set(names)):
        rows.append((name, report.stats[name]))
    return rows


def _unreliable_reasons(
    report: PairReport,
    threshold: float,
    *,
    require_all_schemas: bool = True,
    schema_names: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Every gate condition this report breaches, as printable strings.

    The verdict's single source of truth: :func:`_judge` is one word over
    exactly this tuple, so the word and the stated reasons cannot disagree.
    Empty tuple means RELIABLE. Pure — reads only its arguments and, unless
    ``schema_names`` says otherwise, module scope.

    ``require_all_schemas`` (default ``True``, the gate) is the fix for the hole
    that let a run reporting on four of seven schemas pass. Setting it
    ``False`` isolates the failure/method/substitution conditions from the
    coverage one; it does not weaken them.

    ``schema_names`` is the seam for the coupling this module now has to
    ``llm.GAMEPLAY_SCHEMAS``. ``None`` — the production path — reads the derived
    module tuple at CALL time (never bound as a signature default, which is how
    a redirected constant gets ignored). Passing a tuple states the vocabulary
    at the call site instead. A caller therefore chooses explicitly between
    "test the derivation" and "test the rule over a fixed vocabulary"; a test
    that silently depends on the enum's contents is how the four-of-seven hole
    survived in the first place.
    """
    reasons: list[str] = []

    if not report.preflight_ok:
        # Nothing downstream was measured, so every other condition would fire
        # for the same single cause. One reason, not seven.
        return (
            "preflight failed: "
            + (report.preflight_message or "no message recorded"),
        )

    if not report.game_completed:
        reasons.append(
            "the scripted game did not complete"
            + (f" (stopped at: {report.stop_reason})" if report.stop_reason else "")
        )

    names = SCHEMA_NAMES if schema_names is None else tuple(schema_names)
    rows = _schema_rows(report, schema_names=names)

    if require_all_schemas:
        # Coverage is judged over the VOCABULARY, not over ``rows`` — the rows
        # may carry extra keys ``stats`` booked outside it, and denominating
        # "1 of 7" against those would report a coverage fraction nobody asked
        # for. Extras are still displayed, and still checked for failures below.
        booked = dict(rows)
        unexercised = [name for name in names if not booked[name].attempts]
        if unexercised:
            reasons.append(
                f"{len(unexercised)} of {len(names)} schemas went unexercised "
                f"(no structured-output attempt was made, so this run reports "
                f"nothing about them): " + ", ".join(unexercised)
            )

    for name, s in rows:
        if not s.failures:
            continue
        frame = (
            f"past the {100 * threshold:.0f}% advisory bound"
            if s.failure_rate > threshold
            else f"under the {100 * threshold:.0f}% advisory bound, but the "
            f"gate requires zero"
        )
        reasons.append(
            f"{name} booked {s.failures} failure(s) in {s.attempts} attempt(s) "
            f"— {100 * s.failure_rate:.0f}%, {frame}"
        )

    if report.structured_output_method != EXPECTED_STRUCTURED_OUTPUT_METHOD:
        reasons.append(
            "the provider seam resolved structured-output method "
            + (
                repr(report.structured_output_method)
                if report.structured_output_method is not None
                else "nothing (not observed)"
            )
            + f", not {EXPECTED_STRUCTURED_OUTPUT_METHOD!r} — so a clean "
            "decode here would not be evidence about grammar-constrained "
            "decoding"
        )

    missing_kinds = [
        kind.value
        for kind in SubstitutionKind
        if kind.value not in report.substitutions
    ]
    if missing_kinds:
        reasons.append(
            f"the substitution census covers only "
            f"{len(report.substitutions)} of {len(SubstitutionKind)} answer "
            f"kinds (missing: " + ", ".join(missing_kinds) + ") — an "
            "instrument that was not installed is not a clean measurement"
        )
    nonzero = [
        (kind, facets)
        for kind, facets in report.substitutions.items()
        if facets.get("substituted")
    ]
    if nonzero:
        reasons.append(
            "the substitution census is nonzero — the game fell back to a "
            "deterministic stand-in despite the transport: "
            + ", ".join(
                f"{kind} {facets['substituted']}/{facets.get('attempted', 0)}"
                for kind, facets in nonzero
            )
        )

    return tuple(reasons)


def _judge(
    report: PairReport,
    threshold: float,
    *,
    require_all_schemas: bool = True,
    schema_names: Sequence[str] | None = None,
) -> str:
    """The one-word RELIABLE/UNRELIABLE call over :func:`_unreliable_reasons`.

    Kept as a separate function returning a bare word because that word is what
    ``PairReport.verdict`` holds, what the summary block prints and what the
    process exit code is derived from. It delegates rather than re-deriving, so
    there is exactly one place the four conditions are expressed.
    """
    return (
        "UNRELIABLE"
        if _unreliable_reasons(
            report,
            threshold,
            require_all_schemas=require_all_schemas,
            schema_names=schema_names,
        )
        else "RELIABLE"
    )


def _smoke_one_pair(
    large: str, small: str, *, max_rounds: int, seed: int, threshold: float
) -> PairReport:
    report = PairReport(large=large, small=small)

    # Per-pair model selection flows through the same env vars the game uses.
    os.environ["GRAPHIA_OLLAMA_LARGE_MODEL"] = large
    os.environ["GRAPHIA_OLLAMA_SMALL_MODEL"] = small

    # Same fail-fast gate the game boots with: clean message, no model time
    # burned, when the server is down or a model isn't pulled.
    from graphia.config import load_config
    from graphia.preflight import run_ollama_preflight

    try:
        run_ollama_preflight(load_config())
    except SystemExit as exc:
        report.preflight_message = str(exc)
        # Stamp the reason on this path too. ``_print_pair_report`` says
        # "(preflight)" on screen, but the JSON record would otherwise carry an
        # empty ``verdict_reasons`` beside an UNRELIABLE verdict — the printed
        # and machine surfaces disagreeing again, in the one case where nothing
        # downstream ran.
        report.verdict_reasons = _unreliable_reasons(report, threshold)
        return report
    report.preflight_ok = True

    stats: dict[str, SchemaStats] = {name: SchemaStats() for name in SCHEMA_NAMES}
    provider = _install_counting_provider(stats)
    report.stats = stats
    report.structured_output_method = _resolved_structured_output_method(provider)

    # The substitution sink is installed around the GAME, not around the
    # process: ``set_sink`` is process-global by design and only the harness
    # knows where one measured game ends. Restored in ``finally`` so a raising
    # drive cannot leave the game package counting into a dead census.
    census = _SubstitutionCensus()
    set_sink(census)
    started = time.monotonic()
    try:
        # ``stats`` is handed down so the ballot probe can ask whether the game
        # reached a vote and, if it did not, book its own attempt into the SAME
        # counted map the table and the verdict read.
        outcome = _run_scripted_game(max_rounds, seed, stats=stats)
    finally:
        reset_sink()
        report.duration_seconds = time.monotonic() - started
        report.substitutions = census.as_report()
    report.game_completed = outcome.completed
    report.game_error = outcome.error
    report.stop_reason = outcome.stop_reason
    report.ballot_probe = outcome.ballot_probe

    # Computed twice, on purpose. ``_judge`` is the decision function and must
    # be the one production calls — a verdict derived here from a local variable
    # would leave ``_judge`` tested but unused. Both readings come from the same
    # pure predicate over the same report, so they cannot disagree.
    report.verdict_reasons = _unreliable_reasons(report, threshold)
    report.verdict = _judge(report, threshold)
    return report


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------


def _print_pair_report(report: PairReport, threshold: float) -> None:
    print(f"\n=== pair: large={report.large}  small={report.small} ===")
    if not report.preflight_ok:
        print(f"  preflight FAILED:\n    {report.preflight_message}")
        print("  verdict: UNRELIABLE (preflight)")
        return
    print(f"  {'schema':<10} {'attempts':>8} {'failures':>8} {'fallbacks':>9} {'fail%':>6}")
    # ``_schema_rows`` — the SAME rows ``_json_payload`` and the verdict read,
    # so the printed table and the machine record cannot disagree again.
    for name, s in _schema_rows(report):
        note = "" if s.attempts else "   (not exercised)"
        print(
            f"  {name:<10} {s.attempts:>8} {s.failures:>8} {s.fallbacks:>9}"
            f" {100 * s.failure_rate:>5.0f}%{note}"
        )
        if s.last_error:
            print(f"             last error: {s.last_error[:120]}")
    completed = "yes" if report.game_completed else "NO"
    print(
        f"  game completed: {completed}   stopped at: "
        f"{report.stop_reason or 'unknown'}   "
        f"duration: {report.duration_seconds:.1f}s"
    )
    if report.game_error:
        # Its own line: the backstop-exhaustion message is a paragraph, and
        # squeezing it onto the summary line is what made it unreadable before.
        print(f"    {report.game_error}")
    # Lower-cased label on purpose: ``test_the_printed_table_and_the_json_
    # record_name_the_same_schemas`` reads a data row as "first token is a
    # schema name", so a line opening with ``Ballot`` would be parsed as a
    # table row.
    print(f"  ballot probe: {report.ballot_probe or 'not recorded'}")
    method = report.structured_output_method
    print(
        f"  structured-output method: {method or 'not observed'}"
        + (
            ""
            if method == EXPECTED_STRUCTURED_OUTPUT_METHOD
            else f"   (the gate requires {EXPECTED_STRUCTURED_OUTPUT_METHOD})"
        )
    )
    if report.substitutions:
        print(
            "  substitutions (substituted/attempted): "
            + ", ".join(
                f"{kind}={facets.get('substituted', 0)}"
                f"/{facets.get('attempted', 0)}"
                for kind, facets in report.substitutions.items()
            )
        )
    else:
        print("  substitutions: not observed (no census was installed)")
    print(
        f"  verdict: {report.verdict}  (requires: all {len(SCHEMA_NAMES)} "
        f"schemas exercised, zero failures, method "
        f"{EXPECTED_STRUCTURED_OUTPUT_METHOD}, zero substitutions; the "
        f"{100 * threshold:.0f}% rate bound is reported, not enforced)"
    )
    for reason in report.verdict_reasons:
        print(f"    - {reason}")


def _json_payload(reports: list[PairReport], args: argparse.Namespace) -> dict:
    return {
        "max_rounds": args.max_rounds,
        "seed": args.seed,
        "threshold": args.threshold,
        # What the run judged ITSELF against, stated in the record rather than
        # left for a reader to infer from the row count. The whole defect this
        # payload once hid was a vocabulary nobody could see.
        "schemas_required": list(SCHEMA_NAMES),
        "expected_structured_output_method": EXPECTED_STRUCTURED_OUTPUT_METHOD,
        "pairs": [
            {
                "large": r.large,
                "small": r.small,
                "preflight_ok": r.preflight_ok,
                "preflight_message": r.preflight_message,
                "game_completed": r.game_completed,
                "game_error": r.game_error,
                "stop_reason": r.stop_reason,
                # Whether this record's ``Ballot`` row is in-game evidence or a
                # probe's single out-of-game decode — a distinction a machine
                # reader needs as much as the screen does.
                "ballot_probe": r.ballot_probe,
                "duration_seconds": round(r.duration_seconds, 2),
                "verdict": r.verdict,
                "verdict_reasons": list(r.verdict_reasons),
                "structured_output_method": r.structured_output_method,
                "substitutions": r.substitutions,
                # ``_schema_rows`` — the SAME rows the printed table and the
                # verdict read. Previously this iterated ``r.stats`` while the
                # table iterated a shorter hand-typed tuple, so one run
                # reported four schemas on screen and six in JSON.
                "schemas": {
                    name: {
                        "attempts": s.attempts,
                        "failures": s.failures,
                        "fallbacks": s.fallbacks,
                        "failure_rate": round(s.failure_rate, 4),
                        "last_error": s.last_error,
                    }
                    for name, s in _schema_rows(r)
                },
            }
            for r in reports
        ],
    }


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _parse_pairs(values: list[str] | None) -> list[tuple[str, str]] | None:
    if not values:
        return None
    pairs: list[tuple[str, str]] = []
    for value in values:
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 2 or not all(parts):
            raise SystemExit(
                f"--models expects 'LARGE,SMALL' (got {value!r}); "
                "repeat the flag for multiple pairs."
            )
        pairs.append((parts[0], parts[1]))
    return pairs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Smoke-test structured output on real local Ollama models over the "
            "native surface (ADR-010 gate). Reports per-schema parse "
            "reliability; it does not switch transports."
        )
    )
    ap.add_argument(
        "--models",
        action="append",
        metavar="LARGE,SMALL",
        help=(
            "model pair to test as 'LARGE,SMALL' (repeatable for multiple "
            "pairs); default = the configured GRAPHIA_OLLAMA_* pair"
        ),
    )
    ap.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help=(
            "cap on scripted-human Day rounds per game (local model time); "
            "default = DAY_MAX_ROUNDS + 1, the smallest cap that closes one "
            "full Day and crosses the Day->Night hinge, so the diary, the "
            "final reflection and the Night pointing are all exercised"
        ),
    )
    ap.add_argument("--seed", type=int, default=20260611, help="seed for game structure")
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=(
            "advisory per-schema failure-rate bound, REPORTED not enforced: "
            "the verdict requires zero failures per schema, and this only "
            "frames a nonzero count as past or under the bound"
        ),
    )
    ap.add_argument("--json", type=str, default=None, help="write a JSON report to this path")
    args = ap.parse_args(argv)

    # Force the local provider in-process: no Bedrock client is constructed
    # on this path and no AWS credentials are read. Remote mode contradicts
    # the ollama provider (config rejects the combination), so clear it in
    # case the make-included .env carries GRAPHIA_REMOTE.
    os.environ["GRAPHIA_LLM_PROVIDER"] = "ollama"
    os.environ.pop("GRAPHIA_REMOTE", None)
    os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")  # no night-point interrupts
    # Isolate from the cloud stats/diary stores: a wire-env'd .env carries
    # AgentCore Memory / Gateway ids, and the career emitter gates on the id
    # alone — an "offline" smoke game would emit events to AWS (and die on an
    # expired SSO token, as observed). The smoke measures structured output,
    # not the stats pipeline, so force the local/no-op store implementations.
    # (Whether provider=ollama should force this in PRODUCTION is a deferred
    # spec-010 follow-up — see tasks.md.)
    for var in (
        "GRAPHIA_MEMORY_ID",
        "GRAPHIA_CAREER_MEMORY_ID",
        "GRAPHIA_GATEWAY_ID",
        "GRAPHIA_GATEWAY_URL",
        "GRAPHIA_STATS_STRATEGY_ID",
    ):
        os.environ.pop(var, None)
    # Force ON the two ADR-011 ablation flags that GATE two of the seven
    # schemas: ``private_thoughts_enabled`` gates ``day_round_reflect``
    # (``Reflection``) and ``private_diaries_enabled`` gates ``day_diary``
    # (``Diary``). Both default on, so this only bites when a wired-in ``.env``
    # has turned one off — and then the gate would fail for a reason that says
    # nothing about the transport it exists to measure. Forced rather than
    # defaulted because this is a TRANSPORT gate, not an ablation measurement:
    # ``blunder_eval`` is where those flags are a variable, and it reads them
    # from config as normal.
    os.environ["GRAPHIA_PRIVATE_THOUGHTS"] = "1"
    os.environ["GRAPHIA_PRIVATE_DIARIES"] = "1"

    # ``--max-rounds``'s default, DERIVED from production's own Day cap rather
    # than typed. The old literal 3 could not reach the first Day -> Night
    # hinge (``DAY_MAX_ROUNDS`` is 6), so ``Diary``, ``Reflection`` and
    # ``Pointing`` went unexercised on a default run — invisible while the
    # verdict iterated four schema names, unsatisfiable now that it requires
    # all seven. Resolved HERE, after the env forcing above, so the production
    # import stays on the far side of it; ``argparse`` carries ``None`` and the
    # help text names the derivation.
    from graphia.nodes.day import DAY_MAX_ROUNDS

    if args.max_rounds is None:
        args.max_rounds = DAY_MAX_ROUNDS + 1

    from graphia.config import load_config

    pairs = _parse_pairs(args.models)
    if pairs is None:
        config = load_config()
        pairs = [(config.ollama_large_model, config.ollama_small_model)]

    print(
        f"Ollama structured-output smoke: {len(pairs)} pair(s), one scripted game "
        f"each (max {args.max_rounds} Day rounds). Local model time; non-deterministic."
    )
    print(
        f"  verdict requires all {len(SCHEMA_NAMES)} schemas exercised "
        f"({', '.join(SCHEMA_NAMES)}) with zero failures, method "
        f"{EXPECTED_STRUCTURED_OUTPUT_METHOD}, and a zero substitution census."
    )
    print(
        f"  {BALLOT_SCHEMA_NAME} has no guaranteed path through a scripted "
        "game — the human may be killed on Night 1 (no turns left to script) "
        "and no AI may ever call a vote — so when the game initiates none, "
        "ONE direct decode is made off the final state. Which of the two "
        "happened is on the 'ballot probe:' line."
    )

    reports: list[PairReport] = []
    for large, small in pairs:
        report = _smoke_one_pair(
            large,
            small,
            max_rounds=args.max_rounds,
            seed=args.seed,
            threshold=args.threshold,
        )
        reports.append(report)
        _print_pair_report(report, args.threshold)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(_json_payload(reports, args), fh, indent=2)
        print(f"\nWrote JSON report to {args.json}")

    print("\n=== SUMMARY ===")
    for r in reports:
        print(f"  {r.verdict:<10} large={r.large}  small={r.small}")

    return 0 if all(r.verdict == "RELIABLE" for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
