"""Entry point for `python -m graphia`: reconfigure streams, load .env, launch the Textual app."""

from __future__ import annotations

import sys

# Reconfigure stdio to UTF-8 BEFORE importing anything that may emit output,
# so Unicode narration never blows up on Windows code pages or odd terminals.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import argparse
import os

from dotenv import load_dotenv

from graphia.config import load_config
from graphia.ui.app import GraphiaApp

load_dotenv()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="graphia",
        description="Launch Graphia. Use --remote to run against a deployed AgentCore Runtime.",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Run against the deployed AgentCore Runtime instead of local mode.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if args.remote:
        os.environ["GRAPHIA_REMOTE"] = "1"
    # Surface invalid GRAPHIA_ROLE / missing GRAPHIA_RUNTIME_URL on stderr before Textual takes the screen.
    load_config()
    GraphiaApp().run()
