"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class GraphiaConfig:
    bearer_token: str
    aws_region: str
    log_file: Path
    seed: int
    checkpoint_dir: Path


def load_config() -> GraphiaConfig:
    bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if not bearer_token:
        raise SystemExit(
            "AWS_BEARER_TOKEN_BEDROCK is not set. "
            "Add it to your .env file or export it in your shell before launching Graphia."
        )

    aws_region = os.environ.get("AWS_REGION", "eu-north-1")
    log_file = Path(os.environ.get("GRAPHIA_LOG_FILE", "./.graphia/graphia.log"))
    checkpoint_dir = Path(
        os.environ.get("GRAPHIA_CHECKPOINT_DIR", "./.graphia/checkpoints")
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
    )
