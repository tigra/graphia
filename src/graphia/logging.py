"""JSONL stream-trace logger for Graphia runtime events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from graphia.config import GraphiaConfig, resolved_tier_models


class StreamTraceLogger:
    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def record(self, event: dict) -> None:
        payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        line = json.dumps(payload, default=str, ensure_ascii=False)
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()


def setup_logger(config: GraphiaConfig) -> StreamTraceLogger:
    """Open the JSONL trace and stamp the run's provider provenance.

    The ``app_start`` record carries WHICH MODEL SERVED THIS RUN — provider,
    both resolved tier ids, and the endpoint that identifies where they ran
    (AWS region for the Bedrock arms, base URL for Ollama).

    Why it is here: the trace otherwise records only graph-stream deltas (node,
    keys, cycle, phase), so a finished local game left no evidence of which
    model played it. Confirming that meant an out-of-band CloudWatch query,
    while the deployed Runtime has been self-evidencing all along. Spec 035's
    verification hit exactly this — a local Claude game could only be proven
    from Bedrock ``Invocations`` metrics. One line at boot closes the gap.

    Deliberately NOT logged: credentials of any kind, and the AWS profile name
    (an identity detail, and the project keeps the profile env-driven rather
    than embedded anywhere). Model ids, region, and a local base URL are not
    secrets. The log is append-mode and long-lived, so a per-run stamp is what
    makes a window in it attributable at all.
    """
    logger = StreamTraceLogger(config.log_file)
    large_model, small_model = resolved_tier_models(config)
    logger.record(
        {
            "node": "boot",
            "event": "app_start",
            "provider": config.llm_provider,
            "large_model": large_model,
            "small_model": small_model,
            # Whichever locates the models for the active provider; the other
            # is None so a reader is never shown an irrelevant endpoint.
            "aws_region": (
                config.aws_region if config.llm_provider != "ollama" else None
            ),
            "ollama_base_url": (
                config.ollama_base_url if config.llm_provider == "ollama" else None
            ),
            # Remote mode still writes this local trace (the TUI drives the
            # deployed Runtime), so record which side actually ran the graph.
            "remote_mode": config.remote_mode,
        }
    )
    return logger
