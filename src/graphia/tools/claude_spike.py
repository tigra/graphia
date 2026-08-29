"""Real-Bedrock Claude verification spike — spec 035's verify-at-implementation gate.

Spec 035 adds ``bedrock-claude`` as an opt-in third provider (Nova stays the
default). Its technical considerations sequence the work **spike-first**,
because two things are asserted by the wiring but not yet proven against live
Bedrock:

1. **The model id / inference profile is reachable.** ``config.py`` carries
   ``us.anthropic.claude-haiku-4-5-20251001-v1:0`` behind an explicit
   VERIFY-AT-RUNTIME comment (ADR-012 named Haiku 4.5; Claude 4.x has no
   single-region on-demand id, hence the ``us.`` system inference profile).
   Check-don't-guess: this harness is where that id stops being a guess.
2. **The flat Pydantic schemas round-trip.** Graphia keeps ``Roster`` /
   ``Pointing`` / ``Ballot`` / ``DayAction`` deliberately flat with primitive
   fields because Bedrock Converse rejects discriminated unions. That
   constraint was established against **Nova**; whether Claude-via-Converse
   accepts the same shapes is the structured-output contract this proves.

Like ``ollama_smoke`` (whose reporting posture this mirrors), it is deliberately
**not** a pytest test: it reaches real Bedrock, costs tokens, and is run on
demand outside the mocked suite:

    make claude-spike
    make claude-spike ARGS="--large-model us.anthropic.claude-sonnet-4-5-20250929-v1:0"

It **reports, it does not decide**. A FAIL here is a finding to act on (wrong
id, missing model access, wrong region), not something the tool silently works
around. No game is played, no ledger record is written, and nothing in the
repo is modified.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

__all__ = ["SchemaProbe", "main", "run_spike"]


# One probe per flat schema Graphia relies on. The prompts are deliberately
# trivial — this measures whether Converse + Claude honour the *schema*, not
# whether the model reasons well. Kept short to keep the spend negligible.
_PROBES: tuple[tuple[str, str], ...] = (
    ("Ballot", "A vote is open to execute Dara. Cast your ballot: vote yes."),
    (
        "DayAction",
        "It is the Day phase. Say one short sentence to the table — "
        "use the 'speak' action.",
    ),
    ("Pointing", "The living players are: Mira, Dara, Sanaa. Point at Dara."),
    (
        "Roster",
        "Invent exactly three distinct first names for townsfolk characters.",
    ),
)


@dataclass
class SchemaProbe:
    """The outcome of round-tripping one flat schema against the live model."""

    schema: str
    ok: bool
    detail: str
    seconds: float


def _schema_types() -> dict[str, Any]:
    """Map probe names to the real Pydantic classes (imported lazily).

    Deferred so ``--help`` works without touching config or boto3.
    """
    from graphia.llm import Ballot, DayAction, Pointing, Roster

    return {
        "Ballot": Ballot,
        "DayAction": DayAction,
        "Pointing": Pointing,
        "Roster": Roster,
    }


def run_spike(*, large_model: str | None, small_model: str | None) -> list[SchemaProbe]:
    """Run the preflight, then round-trip every flat schema on the live model.

    Forces ``GRAPHIA_LLM_PROVIDER=bedrock-claude`` for this process only, so the
    spike proves the *configured* path rather than a hand-built client. Per-tier
    model-id overrides are honoured through the same env vars the game reads.
    """
    os.environ["GRAPHIA_LLM_PROVIDER"] = "bedrock-claude"
    if large_model:
        os.environ["GRAPHIA_LARGE_MODEL"] = large_model
    if small_model:
        os.environ["GRAPHIA_SMALL_MODEL"] = small_model

    from graphia.config import load_config
    from graphia.llm import get_large
    from graphia.preflight import run_claude_preflight

    config = load_config()
    print(
        f"provider: {config.llm_provider}   region: {config.aws_region}\n"
        f"large tier: {config.large_model}\n"
        f"small tier: {config.small_model}\n"
    )

    # Slice 2's preflight, exercised on the real path it was written for. A
    # SystemExit here is the plain-language mapping doing its job — let it
    # propagate uncaught so the operator sees exactly what a player would.
    print("Preflight (credentials / model access / region) …")
    run_claude_preflight(config)
    print("  OK — the configured Claude model is reachable.\n")

    model = get_large()
    print(f"get_large() -> {type(model).__name__}\n")

    schema_types = _schema_types()
    results: list[SchemaProbe] = []
    for name, prompt in _PROBES:
        started = time.monotonic()
        try:
            parsed = model.with_structured_output(schema_types[name]).invoke(prompt)
            elapsed = time.monotonic() - started
            if parsed is None:
                results.append(
                    SchemaProbe(name, False, "returned None (no parse)", elapsed)
                )
            else:
                results.append(SchemaProbe(name, True, repr(parsed), elapsed))
        except Exception as exc:  # noqa: BLE001 — a probe failure is the finding
            elapsed = time.monotonic() - started
            results.append(
                SchemaProbe(name, False, f"{type(exc).__name__}: {exc}", elapsed)
            )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-spike",
        description=(
            "Prove the bedrock-claude provider reaches a live Claude model and "
            "that Graphia's flat structured-output schemas round-trip on it."
        ),
    )
    parser.add_argument(
        "--large-model",
        default=None,
        help="override the large-tier Bedrock model id for this run",
    )
    parser.add_argument(
        "--small-model",
        default=None,
        help="override the small-tier Bedrock model id for this run",
    )
    args = parser.parse_args(argv)

    print(
        "Claude verification spike (spec 035) — reaches REAL Bedrock and costs "
        "tokens.\nNo game is played; no ledger record is written.\n"
    )
    results = run_spike(
        large_model=args.large_model, small_model=args.small_model
    )

    print("=== STRUCTURED-OUTPUT ROUND-TRIP ===")
    width = max(len(r.schema) for r in results)
    for probe in results:
        status = "PASS" if probe.ok else "FAIL"
        print(
            f"  {probe.schema:<{width}}  {status}  {probe.seconds:5.2f}s  "
            f"{probe.detail}"
        )

    failed = [r for r in results if not r.ok]
    print()
    if failed:
        print(
            f"RESULT: FAIL — {len(failed)}/{len(results)} schema(s) did not "
            "round-trip. Claude-via-Converse does not honour Graphia's flat "
            "schema contract as configured; treat this as a finding for spec "
            "035, not something to work around."
        )
        return 1
    print(
        f"RESULT: PASS — all {len(results)} flat schemas round-tripped on the "
        "configured Claude model. The model id / inference profile is reachable "
        "and the structured-output contract holds."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
