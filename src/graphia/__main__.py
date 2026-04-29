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

from dotenv import load_dotenv

from graphia.ui.app import GraphiaApp

load_dotenv()


if __name__ == "__main__":
    GraphiaApp().run()
