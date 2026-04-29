"""JSONL stream-trace logger for Graphia runtime events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from graphia.config import GraphiaConfig


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
    logger = StreamTraceLogger(config.log_file)
    logger.record({"node": "boot", "event": "app_start"})
    return logger
