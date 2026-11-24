"""Regenerate the README's ledger-viewer screenshots (``make screenshots``).

Drives :class:`~graphia.ui.ledger_viewer.LedgerViewerApp` headlessly through
Textual's ``App.run_test()`` pilot and saves three SVG screenshots with
``App.save_screenshot()``:

- ``ledger-viewer-table.svg``: the runs table, with the cursor on the chosen run
  (shot extra-wide so the win-rate and vote columns are visible);
- ``ledger-viewer-run.svg``: that run's record, with focus on its games list;
- ``ledger-viewer-transcript.svg``: one game's transcript, with a search active.

No model, no AWS: it only reads the committed ledger and preserved transcripts.
By default it picks the **newest run that has preserved transcripts**, so a
re-run after new evals refreshes the pictures without arguments::

    make screenshots
    make screenshots ARGS="--row 53 --game 5 --search 'executed'"
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from textual.widgets import DataTable

from graphia.eval_ledger import list_transcripts, load_ledger
from graphia.tools.blunder_eval import LEDGER_PATH
from graphia.ui.ledger_viewer import LedgerViewerApp

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"

# Terminal sizes (columns, rows). The table is shot wider than the others so
# its right-hand metric columns fit; the other views read better narrower.
TABLE_SIZE = (240, 42)
VIEW_SIZE = (150, 42)


def newest_row_with_transcripts(ledger_path: Path) -> int:
    """Index of the last ledger record whose transcripts exist on disk."""
    records = load_ledger(ledger_path)
    for index in range(len(records) - 1, -1, -1):
        if list_transcripts(records[index], ledger_path):
            return index
    raise SystemExit(f"No run in {ledger_path} has preserved transcripts.")


async def _shoot_table(ledger_path: Path, row: int, out_dir: Path) -> None:
    app = LedgerViewerApp(path=ledger_path)
    async with app.run_test(size=TABLE_SIZE) as pilot:
        await pilot.pause(0.5)
        app.screen.query_one(DataTable).move_cursor(row=row, column=0)
        await pilot.pause(0.3)
        app.save_screenshot(str(out_dir / "ledger-viewer-table.svg"))


async def _shoot_run_and_transcript(
    ledger_path: Path, row: int, game: int, search: str, out_dir: Path
) -> None:
    app = LedgerViewerApp(path=ledger_path)
    async with app.run_test(size=VIEW_SIZE) as pilot:
        await pilot.pause(0.5)
        app.screen.query_one(DataTable).move_cursor(row=row, column=0)
        await pilot.press("enter")  # open the run's detail screen
        await pilot.pause(0.5)
        await pilot.press("t")  # focus its games list
        for _ in range(game - 1):
            await pilot.press("down")
        await pilot.pause(0.3)
        app.save_screenshot(str(out_dir / "ledger-viewer-run.svg"))

        await pilot.press("enter")  # open the game's transcript
        await pilot.pause(1.0)
        if search:
            await pilot.press("slash")
            await pilot.press(*("space" if ch == " " else ch for ch in search))
            await pilot.press("enter")
            await pilot.pause(0.5)
        app.save_screenshot(str(out_dir / "ledger-viewer-transcript.svg"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="graphia.tools.ledger_screenshots",
        description="Regenerate the ledger-viewer SVG screenshots used in the README.",
    )
    parser.add_argument("--path", type=Path, default=LEDGER_PATH,
                        help="Ledger YAML to view (default: the committed ledger).")
    parser.add_argument("--row", type=int, default=None,
                        help="0-based table row (default: newest run with transcripts).")
    parser.add_argument("--game", type=int, default=1,
                        help="1-based game in that run to open (default: 1).")
    parser.add_argument("--search", default="status",
                        help="Text to search for in the transcript ('' for none; "
                        "default: 'status', which highlights the Moderator recaps).")
    parser.add_argument("--out", type=Path, default=DOCS_DIR,
                        help="Output directory (default: docs/).")
    args = parser.parse_args(argv)

    row = args.row if args.row is not None else newest_row_with_transcripts(args.path)
    args.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(_shoot_table(args.path, row, args.out))
    asyncio.run(_shoot_run_and_transcript(args.path, row, args.game, args.search, args.out))
    print(f"Wrote 3 screenshots for ledger row {row} to {args.out}")


if __name__ == "__main__":
    main()
