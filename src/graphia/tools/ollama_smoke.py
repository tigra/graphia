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
            # NOTE (spec 023): this ``max_rounds`` is a smoke-test *sampling* cap —
            # it bounds how many Day rounds run to exercise the structured-output
            # schemas quickly — NOT the whole-game runaway Day cap
            # (``config.max_days`` / GRAPHIA_MAX_DAYS, the in-game safeguard).
            # Deliberately left as a fast schema-exercise budget; only the
            # blunder-eval harness drives games to their natural end.
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
