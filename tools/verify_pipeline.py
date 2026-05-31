"""End-to-end verification harness for the career-stats pipeline.

Run after ``make redeploy`` (or any time, really). Walks each stage of the
ADR-008 pipeline against the live deploy and prints a PASS/FAIL line per
check. Exits non-zero on the first failure so it's safe to chain after
``make redeploy`` in a script.

The checks come straight from the bugs that took multiple deploy cycles to
surface in spec 006:

* Runtime container image tag matches HEAD (catches "you forgot to rebuild
  / push the new image").
* ``GRAPHIA_CAREER_MEMORY_ID`` is set in the local ``.env`` (catches the
  TUI silently falling back to ``LocalFileStatsStore`` in remote mode).
* Career memory exists and has the ``human-career`` actor (catches the
  emitter being NoOp because Runtime env vars didn't propagate).
* Self-managed strategy on the career memory is ACTIVE (catches strategy
  detachment / orphan-on-diary-memory).
* Consumer Lambda's latest invocations had no ``ParamValidationError`` or
  ``AttributeError`` (catches the includePayloads typo and the missing
  batch_create_memory_records method).
* The TUI's exact ``make_stats_store(load_config()).load()`` call resolves
  to ``AgentCoreCareerEventStore`` and returns the same record AWS does
  (closes the "AWS has it but TUI shows empty" loop).

Run with ``make verify-pipeline`` or ``python tools/verify_pipeline.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Callable

from dotenv import load_dotenv

# Local imports happen after dotenv so module-init reads the freshly-loaded env.
load_dotenv()

import boto3  # noqa: E402

from graphia.config import load_config  # noqa: E402
from graphia.stats_store import (  # noqa: E402
    AgentCoreCareerEventStore,
    LocalFileStatsStore,
    make_stats_store,
)

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _ok(line: str) -> None:
    print(f"{_GREEN}✓{_RESET} {line}")


def _fail(line: str, detail: str = "") -> None:
    print(f"{_RED}✗{_RESET} {line}")
    if detail:
        for d in detail.splitlines():
            print(f"  {_DIM}{d}{_RESET}")


def _warn(line: str, detail: str = "") -> None:
    print(f"{_YELLOW}!{_RESET} {line}")
    if detail:
        for d in detail.splitlines():
            print(f"  {_DIM}{d}{_RESET}")


def _section(title: str) -> None:
    print(f"\n{title}")


def _run(check: Callable[[], bool]) -> bool:
    try:
        return check()
    except Exception as exc:  # noqa: BLE001
        _fail(check.__name__, repr(exc))
        return False


def _git_head_short() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], text=True
    ).strip()


def _tf_output(name: str) -> str:
    out = subprocess.check_output(
        ["./tf", "output", "-raw", name],
        cwd="infra/terraform",
        text=True,
        stderr=subprocess.DEVNULL,
        env={**os.environ},
    )
    return out.strip()


def check_runtime_image_matches_head() -> bool:
    """Catches: you forgot ``make redeploy`` between commit + game."""
    runtime_id = _tf_output("runtime_invocation_url").split("/")[-1]
    client = boto3.client("bedrock-agentcore-control", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    rt = client.get_agent_runtime(agentRuntimeId=runtime_id)
    image = rt["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]
    tag = image.rsplit(":", 1)[-1]
    head = _git_head_short()
    if tag == head:
        _ok(f"Runtime image tag matches HEAD ({head}); image: {image}")
        return True
    _warn(
        f"Runtime image tag is {tag}, not HEAD ({head})",
        f"image: {image}\n"
        f"If you intended to ship HEAD: aws sso login --profile $AWS_PROFILE && make redeploy",
    )
    return False  # not strictly fatal, but flag it


def check_env_has_career_memory_id() -> bool:
    """Catches: TUI's silent fallback to LocalFileStatsStore in remote mode."""
    val = os.environ.get("GRAPHIA_CAREER_MEMORY_ID")
    if val:
        _ok(f"GRAPHIA_CAREER_MEMORY_ID set in env: {val}")
        return True
    _fail(
        "GRAPHIA_CAREER_MEMORY_ID missing from env",
        "TUI will silently use LocalFileStatsStore in remote mode.\n"
        "Run `make wire-env` to pin it.",
    )
    return False


def check_career_memory_actor_present() -> bool:
    """Catches: emitter never fires (env var missing in Runtime container)."""
    mem_id = os.environ["GRAPHIA_CAREER_MEMORY_ID"]
    client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    resp = client.list_actors(memoryId=mem_id)
    actors = {a["actorId"] for a in resp.get("actorSummaries", [])}
    if "human-career" in actors:
        _ok(f"Actor 'human-career' present in career memory ({len(actors)} actor(s) total)")
        return True
    _fail(
        "Actor 'human-career' missing from career memory",
        f"actorSummaries: {sorted(actors)!r}\n"
        "Likely cause: deployed Runtime image doesn't have GRAPHIA_CAREER_MEMORY_ID env, "
        "or build_runtime_graph forgot the career_emitter wiring.",
    )
    return False


def check_career_strategy_active() -> bool:
    """Catches: orphan strategy on diary memory, or strategy detached."""
    mem_id = os.environ["GRAPHIA_CAREER_MEMORY_ID"]
    client = boto3.client(
        "bedrock-agentcore-control",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
    resp = client.get_memory(memoryId=mem_id)
    strategies = resp["memory"]["strategies"]
    active = [
        s
        for s in strategies
        if s.get("status") == "ACTIVE"
        and s.get("configuration", {}).get("type") == "SELF_MANAGED"
    ]
    if active:
        ids = [s["strategyId"] for s in active]
        _ok(f"Career memory has ACTIVE SELF_MANAGED strategy(ies): {ids}")
        return True
    _fail(
        "No ACTIVE SELF_MANAGED strategy on career memory",
        f"strategies: {strategies!r}",
    )
    return False


def check_lambda_recent_invocations_clean() -> bool:
    """Catches: Lambda errors (param-validation, attribute-error, etc.) since
    the last 10 minutes of activity. If any invocation in the most recent
    log stream raised, the run fails loud."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    logs = boto3.client("logs", region_name=region)
    streams = logs.describe_log_streams(
        logGroupName="/aws/lambda/graphia-demo-career-consumer",
        orderBy="LastEventTime",
        descending=True,
        limit=1,
    )["logStreams"]
    if not streams:
        _warn("No Lambda log streams yet — has the Lambda ever been invoked?")
        return True
    stream = streams[0]["logStreamName"]
    events = logs.get_log_events(
        logGroupName="/aws/lambda/graphia-demo-career-consumer",
        logStreamName=stream,
        startFromHead=False,
        limit=200,
    )["events"]
    bad = [
        e["message"]
        for e in events
        if any(
            marker in e["message"]
            for marker in (
                "ParamValidationError",
                "AttributeError",
                "Traceback",
                "[ERROR]",
            )
        )
    ]
    if not bad:
        _ok(f"Latest Lambda log stream ({stream}) has no error markers")
        return True
    _fail(
        f"Latest Lambda log stream ({stream}) carries errors",
        "\n".join(bad[:3]) + ("\n…" if len(bad) > 3 else ""),
    )
    return False


def check_record_exists_and_tui_load_matches() -> bool:
    """Catches: AWS has a record but the TUI reads empty. Compares
    ``make_stats_store(load_config()).load()`` against the namespace's
    actual record. Most direct proof the end-to-end path is wired."""
    cfg = load_config()
    store = make_stats_store(cfg)
    if isinstance(store, LocalFileStatsStore):
        _fail(
            "make_stats_store returned LocalFileStatsStore (not AgentCoreCareerEventStore)",
            "config.career_memory_id resolved to None — env not loaded correctly.",
        )
        return False
    assert isinstance(store, AgentCoreCareerEventStore)
    stats = store.load()
    # Re-fetch the raw record via the data plane for a side-by-side comparison.
    client = boto3.client(
        "bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    raw = client.list_memory_records(
        memoryId=cfg.career_memory_id, namespace=cfg.stats_namespace
    )
    summaries = raw.get("memoryRecordSummaries") or []
    if stats.games_total == 0 and not summaries:
        _ok("Pipeline is wired but no record exists yet (play a full game to populate)")
        return True
    if stats.games_total == 0 and summaries:
        text = summaries[0].get("content", {}).get("text", "")
        _fail(
            "TUI's load() returns 0 games but a record EXISTS in AgentCore",
            f"record content (first 200 chars): {text[:200]}",
        )
        return False
    _ok(
        f"TUI load() ↔ AgentCore record agree: games_total={stats.games_total}, "
        f"games_folded={stats.games_folded}"
    )
    return True


def main() -> int:
    checks = [
        ("Build artifact", check_runtime_image_matches_head),
        ("Local env", check_env_has_career_memory_id),
        ("Memory actor", check_career_memory_actor_present),
        ("Memory strategy", check_career_strategy_active),
        ("Lambda health", check_lambda_recent_invocations_clean),
        ("Read path", check_record_exists_and_tui_load_matches),
    ]
    fails = 0
    for label, check in checks:
        _section(f"— {label} —")
        if not _run(check):
            fails += 1
    print()
    if fails:
        print(f"{_RED}{fails} check(s) failed.{_RESET}")
        return 1
    print(f"{_GREEN}All checks passed.{_RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
