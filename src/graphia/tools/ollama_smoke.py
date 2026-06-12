"""Real-Ollama structured-output smoke — ADR-010's verify-at-implementation gate.

ADR-010 routes the Ollama provider through Ollama's Anthropic-compatible
``/v1/messages`` surface (``ChatAnthropic``). The open question that decision
deliberately deferred is whether **tool-use / structured output** works
reliably over that surface with small local models. This harness answers it
empirically — it *reports*, it does not decide: if a model pair comes back
UNRELIABLE, switching to a fallback transport (native ChatOllama / the
OpenAI-compatible surface) is a deliberate follow-up decision, not something
this tool performs silently.

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
from typing import Any

from langgraph.types import Command

# Reuse the established make-gated harness driver (scripted human, stream-to-
# interrupt pump) rather than re-implementing it.
from graphia.tools.eval_dialogue import (
    HUMAN_LINES,
    HUMAN_NAME,
    _collect_interrupt,
    _drive,
)

# The four structured-output surfaces under test (tech-spec §2.6 / ADR-010).
SCHEMA_NAMES = ("Roster", "Pointing", "Ballot", "DayAction")

# Advisory failure-rate threshold for the RELIABLE/UNRELIABLE call. The table
# is the substance; this just gives the one-word verdict a definition.
DEFAULT_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Counting proxy — the instrumentation seam.
# ---------------------------------------------------------------------------


@dataclass
class SchemaStats:
    """Raw attempt outcomes for one structured-output schema."""

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


class _CountingStructured:
    """Wraps one ``with_structured_output(schema)`` runnable, counting raw
    invoke outcomes underneath the game's own retry/fallback handling."""

    def __init__(self, inner: Any, schema: Any, stats: dict[str, SchemaStats]):
        self._inner = inner
        self._schema = schema
        self._stats = stats

    def _rec(self) -> SchemaStats:
        name = self._schema.__name__ if isinstance(self._schema, type) else str(self._schema)
        return self._stats.setdefault(name, SchemaStats())

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        rec = self._rec()
        try:
            result = self._inner.invoke(*args, **kwargs)
        except Exception as exc:
            rec.record_failure(f"{type(exc).__name__}: {exc}")
            raise  # preserve the game's own exception handling exactly
        if isinstance(self._schema, type) and not isinstance(result, self._schema):
            # e.g. the model produced no tool call and langchain returned
            # None / a raw message — a parse failure even though no exception
            # surfaced. Return it unchanged so the game's validators decide.
            rec.record_failure(f"non-instance result: {type(result).__name__}")
        else:
            rec.record_success()
        return result

    def __getattr__(self, name: str) -> Any:  # defensive passthrough
        return getattr(self._inner, name)


class _CountingModel:
    """Thin proxy over a tier client: intercepts ``with_structured_output``
    and delegates everything else untouched."""

    def __init__(self, inner: Any, stats: dict[str, SchemaStats]):
        self._inner = inner
        self._stats = stats

    def with_structured_output(self, schema: Any, **kwargs: Any) -> _CountingStructured:
        return _CountingStructured(
            self._inner.with_structured_output(schema, **kwargs), schema, self._stats
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _install_counting_provider(stats: dict[str, SchemaStats]) -> None:
    """Point ``graphia.llm``'s module-level seams at counted Ollama clients.

    Uses the documented override seams (``_active_provider`` / ``_large`` /
    ``_small``) — the same ones ``repetition_experiment`` rebuilds — so no
    production code changes and no Bedrock client is ever constructed.
    """
    import graphia.llm as llm_mod

    provider = llm_mod.OllamaProvider()
    llm_mod._active_provider = provider
    llm_mod._large = _CountingModel(provider.large(), stats)
    llm_mod._small = _CountingModel(provider.small(), stats)


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
    duration_seconds: float = 0.0
    stats: dict[str, SchemaStats] = field(default_factory=dict)
    verdict: str = "UNRELIABLE"


def _run_scripted_game(max_rounds: int, seed: int) -> tuple[bool, str | None]:
    """Drive one game on the (already-installed) counted provider.

    Scripted human: law-abiding (no night-point interrupts), neutral day
    lines, always votes "no" so the game runs long enough to exercise the
    Day-phase schemas. Returns (completed, error). "Completed" means the
    script ran its course — to the round cap or a natural game end — without
    an exception or an unexpected interrupt.
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
                return False, f"no name interrupt: {first!r}"
            _drive(graph, run_config, Command(resume=HUMAN_NAME))

            rounds = 0
            line_idx = 0
            for _ in range(max_rounds * 12 + 20):  # generous per-interrupt budget
                if rounds >= max_rounds:
                    return True, None  # script ran its course
                snapshot = graph.get_state(run_config)
                if not snapshot.next:
                    return True, None  # natural game end (end_screen / END)
                iv = _collect_interrupt(graph, run_config)
                if iv is None:
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
                    return False, f"unexpected interrupt {kind!r}"
                _drive(graph, run_config, Command(resume=resume))
            return False, "interrupt budget exhausted"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"


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
    report.game_completed, report.game_error = _run_scripted_game(max_rounds, seed)
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
    completed = "yes" if report.game_completed else f"NO ({report.game_error})"
    print(f"  game completed: {completed}   duration: {report.duration_seconds:.1f}s")
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
            "Anthropic-compatible surface (ADR-010 gate). Reports per-schema "
            "parse reliability; it does not switch transports."
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
