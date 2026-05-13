"""Inspect diary entries in the deployed AgentCore Memory.

Reads ``GRAPHIA_MEMORY_ID`` from ``.env`` / the environment (mirrors how
the Runtime resolves it) and walks the Memory's actor / session / event
tree, decoding each event's JSON-encoded diary body and pretty-printing
the (night, player, game, content) tuple.

Usage:

    uv run python -m graphia.tools.inspect_diary
    uv run python -m graphia.tools.inspect_diary --game-id <thread_id>
    uv run python -m graphia.tools.inspect_diary --player-id <uuid>
    uv run python -m graphia.tools.inspect_diary --json     # for piping
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import boto3
from dotenv import load_dotenv


def _events_for_pair(client: Any, memory_id: str, actor_id: str, session_id: str) -> list[dict]:
    out: list[dict] = []
    token: str | None = None
    while True:
        kwargs = dict(memoryId=memory_id, actorId=actor_id, sessionId=session_id, maxResults=100)
        if token:
            kwargs["nextToken"] = token
        resp = client.list_events(**kwargs)
        out.extend(resp.get("events", []))
        token = resp.get("nextToken")
        if not token:
            return out


def _decode_event(event: dict) -> dict | None:
    """Extract the diary-entry body from one Memory event, or None if it isn't one."""
    for msg in event.get("payload", []):
        text = msg.get("conversational", {}).get("content", {}).get("text", "")
        if not text:
            continue
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(body, dict) and body.get("kind") == "diary_entry":
            return body
    return None


def collect_entries(
    client: Any,
    memory_id: str,
    *,
    game_id: str | None = None,
    player_id: str | None = None,
) -> list[dict]:
    """Walk the Memory and return all diary entries matching the filters."""
    entries: list[dict] = []
    actors = client.list_actors(memoryId=memory_id).get("actorSummaries", [])
    for actor in actors:
        actor_id = actor["actorId"]
        if player_id and actor_id != player_id:
            continue
        sessions = client.list_sessions(memoryId=memory_id, actorId=actor_id).get("sessionSummaries", [])
        for session in sessions:
            session_id = session["sessionId"]
            if game_id and session_id != game_id:
                continue
            for event in _events_for_pair(client, memory_id, actor_id, session_id):
                body = _decode_event(event)
                if body is None:
                    continue
                entries.append(body)
    entries.sort(key=lambda e: (e.get("game_id", ""), e.get("player_id", ""), e.get("night_index", 0)))
    return entries


def _print_table(entries: list[dict]) -> None:
    if not entries:
        print("(no diary entries found)", file=sys.stderr)
        return
    print(f"{'night':>5}  {'player':<12}  {'game':<22}  content")
    print(f"{'-----':>5}  {'-' * 12}  {'-' * 22}  {'-' * 40}")
    for e in entries:
        night = e.get("night_index", "?")
        player = (e.get("player_id") or "")[:12]
        game = (e.get("game_id") or "")[:22]
        content = (e.get("content") or "").replace("\n", " ")
        if len(content) > 60:
            content = content[:57] + "..."
        print(f"{night:>5}  {player:<12}  {game:<22}  {content}")
    print(f"\n{len(entries)} entries", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inspect-diary", description=__doc__.splitlines()[0])
    parser.add_argument("--memory-id", help="Memory id (defaults to GRAPHIA_MEMORY_ID env).")
    parser.add_argument("--region", help="AWS region (defaults to AWS_REGION env or us-east-1).")
    parser.add_argument("--game-id", help="Filter to one game (session_id).")
    parser.add_argument("--player-id", help="Filter to one player (actor_id).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args(argv)

    load_dotenv()

    memory_id = args.memory_id or os.environ.get("GRAPHIA_MEMORY_ID")
    if not memory_id:
        print("error: GRAPHIA_MEMORY_ID is not set; pass --memory-id or set it in .env", file=sys.stderr)
        return 2
    region = args.region or os.environ.get("AWS_REGION") or "us-east-1"

    client = boto3.client("bedrock-agentcore", region_name=region)
    entries = collect_entries(client, memory_id, game_id=args.game_id, player_id=args.player_id)

    if args.json:
        json.dump(entries, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_table(entries)

    return 0


if __name__ == "__main__":
    sys.exit(main())
