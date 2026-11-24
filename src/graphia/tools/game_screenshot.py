"""Regenerate the README's main game screenshot (``make screenshots``).

A **staged replay**: no model is called and no graph runs. A preserved eval
transcript (real AI dialogue from a measured game) is parsed back into the
messages the graph emitted, and fed through the real :class:`GraphiaApp` message
path (``_handle_graph_message``), so whispers, speech and Moderator lines render
exactly as they do in play. The replay runs from the seat of a player you choose
— by default a Mafioso — and stops at the moment that seat is asked to vote,
leaving the real :class:`~graphia.ui.widgets.VoteModal` open on screen.

Eval transcripts always seat the scripted human as a Law-abiding Citizen, so
replaying from an AI Mafioso's seat is what puts the Mafia whispers on screen.
The seat's own role whisper is synthesised in the game's own wording (the
transcript only records it for the scripted human); every other line is the
transcript's own.

    make screenshots
    uv run python -m graphia.tools.game_screenshot --seat Kenji --vote-target Soren
"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from graphia.prompts import (
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
    VOTE_TALLY_TEMPLATE,
)
from graphia.ui.app import GraphiaApp
from graphia.ui.widgets import VoteModal

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TRANSCRIPT = (
    _REPO_ROOT / "evals" / "transcripts" / "2026-09-14T19-45-34-e2318a" / "game-01.txt"
)
DEFAULT_OUT = _REPO_ROOT / "docs" / "screenshot.svg"
SIZE = (160, 50)

_PLAYER_RE = re.compile(r'<player name="([^"]+)" role="([^"]+)"')
_PRIVATE_RE = re.compile(r"^Moderator \(private to ([^)]+)\): (.*)$")
_VOTE_RE = re.compile(r'<vote initiator="([^"]+)" target="([^"]+)">')
_TALLY_RE = re.compile(r"^tally: (\d+) Yes, (\d+) No$")
_SPEECH_RE = re.compile(r"^([A-Z][\w'-]*): (.+)$")


@dataclass
class Replay:
    messages: list[BaseMessage]
    seat_role: str
    vote_target: str


def _seat_id(name: str) -> str:
    return f"seat-{name.lower()}"


def build_replay(text: str, seat: str, vote_target: str) -> Replay:
    """Turn a preserved transcript into the public/private messages ``seat`` saw.

    Stops when a vote against ``vote_target`` reaches ``seat``'s own ballot.
    """
    roles = dict(_PLAYER_RE.findall(text))
    if seat not in roles:
        raise SystemExit(f"{seat!r} is not a player in this transcript ({sorted(roles)}).")
    seat_role = roles[seat]
    private = {"private_to": _seat_id(seat)}

    def mod(content: str) -> SystemMessage:
        return SystemMessage(content=content)

    msgs: list[BaseMessage] = []
    in_vote: str | None = None  # the target of the vote block being read
    for raw in text.split("<preamble>", 1)[1].splitlines():
        line = raw.strip()
        if not line or line.startswith(("<thought", "<diary", "<kill>")):
            continue
        if m := _VOTE_RE.match(line):
            in_vote = m.group(2)
            msgs.append(mod(VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
                initiator=m.group(1), target=in_vote)))
            continue
        if line == "</vote>":
            in_vote = None
            continue
        if in_vote is not None:
            if m := _TALLY_RE.match(line):
                msgs.append(mod(VOTE_TALLY_TEMPLATE.format(
                    yes_count=m.group(1), no_count=m.group(2))))
            elif line.startswith("outcome: "):
                msgs.append(mod(line.split(" — ", 1)[-1]))
            elif m := _SPEECH_RE.match(line):
                if m.group(1) == seat and in_vote == vote_target:
                    return Replay(msgs, seat_role, vote_target)
                msgs.append(mod(VOTE_PER_BALLOT_TEMPLATE.format(
                    voter=m.group(1), vote_label=m.group(2))))
            continue
        if line.startswith("<recap>"):
            msgs.append(mod(re.sub(r"</?recap>", "", line)))
            continue
        if m := _PRIVATE_RE.match(line):
            if m.group(1) == seat:
                msgs.append(SystemMessage(content=m.group(2), additional_kwargs=private))
            continue
        if line.startswith("Moderator: "):
            body = line.removeprefix("Moderator: ")
            msgs.append(mod(body))
            if body.startswith("Players in this game:"):
                # The seat's own role whisper, in the game's wording (the
                # transcript records it only for the scripted human).
                msgs.append(SystemMessage(
                    content=f"You are {seat}. Your role is {seat_role}.",
                    additional_kwargs=private,
                ))
            continue
        if line.startswith("<") or line.endswith(":") or " points at " in line:
            continue  # structure tags and Night pointing (never public)
        if m := _SPEECH_RE.match(line):
            if m.group(1) in roles:
                msgs.append(AIMessage(content=m.group(2), name=m.group(1)))
    raise SystemExit(f"No vote against {vote_target!r} reached {seat}'s ballot.")


class _ReplayApp(GraphiaApp):
    """GraphiaApp whose driver replays messages instead of running the graph."""

    def __init__(self, replay: Replay, seat: str) -> None:
        super().__init__()
        self._replay = replay
        self._seat = seat

    async def _drive(self) -> None:
        # Show the real app's title, not this subclass's name.
        self.title = GraphiaApp.__name__
        # RichLog measures text against the app console's width, which a
        # headless run leaves at 80 columns; a real terminal reports its own
        # width. Match the screenshot's width so lines wrap as they do in play.
        self.console.width = SIZE[0]
        await asyncio.sleep(0.3)
        self._human_id = _seat_id(self._seat)
        for msg in self._replay.messages:
            if isinstance(msg, SystemMessage) and msg.content.startswith("A new game begins."):
                msg = SystemMessage(content=f"A new game begins. Welcome, {self._seat}.")
            await self._handle_graph_message(msg)
        self.push_screen(VoteModal(target_name=self._replay.vote_target))


async def shoot(transcript: Path, seat: str, vote_target: str, out: Path) -> None:
    replay = build_replay(transcript.read_text(encoding="utf-8"), seat, vote_target)
    app = _ReplayApp(replay, seat)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(1.0)
        app.save_screenshot(str(out))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="graphia.tools.game_screenshot",
        description="Regenerate the README's game screenshot from a preserved transcript.",
    )
    parser.add_argument("--transcript", type=Path, default=DEFAULT_TRANSCRIPT)
    parser.add_argument("--seat", default="Kenji",
                        help="Player whose seat the replay is seen from (default: Kenji).")
    parser.add_argument("--vote-target", default="Soren",
                        help="Stop when a vote against this player reaches the seat "
                        "(default: Soren, Kenji's Mafia teammate).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(shoot(args.transcript, args.seat, args.vote_target, args.out))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
