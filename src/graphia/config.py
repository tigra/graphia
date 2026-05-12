"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(slots=True, frozen=True)
class GraphiaConfig:
    bearer_token: str | None
    aws_region: str
    log_file: Path
    seed: int
    checkpoint_dir: Path
    remote_mode: bool
    runtime_invocation_url: str | None


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def load_config() -> GraphiaConfig:
    # Legacy / workshop-token path: if set, hand it through. Otherwise leave
    # None and rely on boto3's default credential chain (AWS_PROFILE / SSO /
    # instance role) at the call site.
    bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or None

    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    log_file = Path(os.environ.get("GRAPHIA_LOG_FILE", "./.graphia/graphia.log"))
    checkpoint_dir = Path(
        os.environ.get("GRAPHIA_CHECKPOINT_DIR", "./.graphia/checkpoints")
    )

    remote_mode = _env_truthy("GRAPHIA_REMOTE")
    runtime_invocation_url = os.environ.get("GRAPHIA_RUNTIME_URL") or None

    if remote_mode and not runtime_invocation_url:
        raise SystemExit(
            "Remote mode requested (--remote / GRAPHIA_REMOTE=1) but "
            "GRAPHIA_RUNTIME_URL is not set. Run "
            "`terraform output runtime_invocation_url` from infra/terraform/ "
            "and add the value to .env as `GRAPHIA_RUNTIME_URL=...`."
        )

    seed_raw = os.environ.get("GRAPHIA_SEED")
    if seed_raw is None:
        seed = time.time_ns()
    else:
        try:
            seed = int(seed_raw)
        except ValueError:
            print(
                f"GRAPHIA_SEED must be an integer, got {seed_raw!r}. Using time-based seed.",
                file=sys.stderr,
            )
            seed = time.time_ns()

    return GraphiaConfig(
        bearer_token=bearer_token,
        aws_region=aws_region,
        log_file=log_file,
        seed=seed,
        checkpoint_dir=checkpoint_dir,
        remote_mode=remote_mode,
        runtime_invocation_url=runtime_invocation_url,
    )
