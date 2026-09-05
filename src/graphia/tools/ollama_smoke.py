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
boots with, then (b) drives ONE full scripted game against the real local
provider while counting, per structured-output schema (``Roster``,
``Pointing``, ``Ballot``, ``DayAction``), how many raw
``with_structured_output(...).invoke(...)`` attempts parsed cleanly vs
failed. The game's own retry-then-deterministic-fallback logic masks parse
failures from the *game's* perspective — the counting proxy sits underneath
that logic, so masked failures stay visible in the report.

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

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from graphia.config import GraphiaConfig

# The four structured-output surfaces under test (tech-spec §2.6 / ADR-010).
SCHEMA_NAMES = ("Roster", "Pointing", "Ballot", "DayAction")

# Advisory failure-rate threshold for the RELIABLE/UNRELIABLE call. The table
# is the substance; this just gives the one-word verdict a definition.
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


def _install_counting_provider(stats: dict[str, SchemaStats]) -> None:
    """Point ``graphia.llm``'s module-level seams at counted Ollama clients.

    Uses the documented override seams (``_active_provider`` / ``_large`` /
    ``_small``) — the same ones ``repetition_experiment`` rebuilds — so no
    production code changes and no Bedrock client is ever constructed. The
    proxy counts only (``stats`` supplied, no ``speaker_resolver``); raw capture
    is a different consumer's concern (Slice 3, Task 2).
    """
    import graphia.llm as llm_mod

    provider = llm_mod.OllamaProvider()
    llm_mod._active_provider = provider
    llm_mod._large = InstrumentedModel(provider.large(), stats=stats)
    llm_mod._small = InstrumentedModel(provider.small(), stats=stats)


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
    # Local imports for the same reason every other production import in this
    # module is local: nothing that touches the game package should be pulled in
    # before ``main`` has forced ``GRAPHIA_LLM_PROVIDER=ollama`` and cleared the
    # cloud store ids.
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
    verdict: str = "UNRELIABLE"


def _run_scripted_game(
    max_rounds: int, seed: int, *, budget_override: int | None = None
) -> tuple[bool, str | None, str]:
    """Drive one game on the (already-installed) counted provider.

    Scripted human: law-abiding (no night-point interrupts), neutral day
    lines, always votes "no" so the game runs long enough to exercise the
    Day-phase schemas. Returns (completed, error, stop_reason). "Completed"
    means the script ran its course — to the round cap or a natural game end —
    without an exception or an unexpected interrupt; ``stop_reason`` says which,
    because those two endings mean different things about what was measured.

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
                    # Script ran its course. NOTE: at the default max_rounds=3
                    # this fires INSIDE Day 1 (DAY_MAX_ROUNDS is 6), i.e. before
                    # the first Day -> Night hinge — so the diary and the last
                    # reflection never run. Named in the stop reason because
                    # "which schemas got exercised" is read off this report.
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


def _judge(report: PairReport, threshold: float) -> str:
    """Advisory RELIABLE/UNRELIABLE call. The table is the substance."""
    if not report.preflight_ok or not report.game_completed:
        return "UNRELIABLE"
    for name in SCHEMA_NAMES:
        s = report.stats.get(name)
        if s is not None and s.attempts and s.failure_rate > threshold:
            return "UNRELIABLE"
    return "RELIABLE"


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
        return report
    report.preflight_ok = True

    stats: dict[str, SchemaStats] = {name: SchemaStats() for name in SCHEMA_NAMES}
    _install_counting_provider(stats)
    report.stats = stats

    started = time.monotonic()
    (
        report.game_completed,
        report.game_error,
        report.stop_reason,
    ) = _run_scripted_game(max_rounds, seed)
    report.duration_seconds = time.monotonic() - started
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
    for name in SCHEMA_NAMES:
        s = report.stats.get(name, SchemaStats())
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
    print(f"  verdict: {report.verdict}  (advisory threshold: fail% > {100 * threshold:.0f}%)")


def _json_payload(reports: list[PairReport], args: argparse.Namespace) -> dict:
    return {
        "max_rounds": args.max_rounds,
        "seed": args.seed,
        "threshold": args.threshold,
        "pairs": [
            {
                "large": r.large,
                "small": r.small,
                "preflight_ok": r.preflight_ok,
                "preflight_message": r.preflight_message,
                "game_completed": r.game_completed,
                "game_error": r.game_error,
                "stop_reason": r.stop_reason,
                "duration_seconds": round(r.duration_seconds, 2),
                "verdict": r.verdict,
                "schemas": {
                    name: {
                        "attempts": s.attempts,
                        "failures": s.failures,
                        "fallbacks": s.fallbacks,
                        "failure_rate": round(s.failure_rate, 4),
                        "last_error": s.last_error,
                    }
                    for name, s in r.stats.items()
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
        default=3,
        help="cap on scripted-human Day rounds per game (local model time)",
    )
    ap.add_argument("--seed", type=int, default=20260611, help="seed for game structure")
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="advisory per-schema failure-rate bound for the RELIABLE call",
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

    from graphia.config import load_config

    pairs = _parse_pairs(args.models)
    if pairs is None:
        config = load_config()
        pairs = [(config.ollama_large_model, config.ollama_small_model)]

    print(
        f"Ollama structured-output smoke: {len(pairs)} pair(s), one scripted game "
        f"each (max {args.max_rounds} Day rounds). Local model time; non-deterministic."
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
