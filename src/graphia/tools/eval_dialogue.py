"""Dialogue-diversity eval — play N games on the REAL gameplay model and measure
how repetitive the AI Day speeches are.

This is deliberately **not** a pytest unit test. It reaches the real Bedrock
gameplay model (``get_large`` -> Amazon Nova) to observe genuine dialogue, so it
opts out of the mocked ``safe_llm`` suite and is run on demand:

    make eval-dialogue                      # 5 games, default settings
    make eval-dialogue ARGS="--games 8 --threshold 0.82 --json out.json"

It exists to catch (and A/B) dialogue-quality regressions like the
repeating-phrase spiral observed after specs 008/009: run it on the current
tree, then on a pre-change checkout (``git stash`` / a prior ref), and compare
the diversity numbers. Because it hits a live, paid, non-deterministic model,
keep ``--games`` modest and expect run-to-run variation in the LLM output (the
*game structure* is seeded and reproducible; the dialogue is not).

Requires AWS credentials for Bedrock (the same ``AWS_PROFILE`` the game uses).
The human player is scripted with a small pool of varied, neutral lines (which
are themselves excluded from the metric — we measure only the AI's speeches).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.messages import AIMessage
from langgraph.types import Command

# Neutral, distinct human lines so the scripted human never *itself* injects the
# repetition we're trying to measure in the AI. Cycled deterministically.
HUMAN_LINES = [
    "I'm still watching everyone carefully before I commit.",
    "Hard to say yet — I want to hear another round first.",
    "I don't have a strong read on anyone right now.",
    "Let's not rush; something will give itself away soon.",
    "I'm keeping an open mind, but a few of you worry me.",
    "Nothing's obvious to me yet — keep talking.",
]
HUMAN_NAME = "Avery"


@dataclass
class GameResult:
    index: int
    speeches: list[str]
    error: str | None = None


@dataclass
class DiversityStats:
    total: int = 0
    distinct: int = 0
    exact_dup_rate: float = 0.0
    near_dup_rate: float = 0.0
    max_cluster: int = 0
    top_clusters: list[tuple[int, str]] = field(default_factory=list)


def _normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .!?,;:'\"")


def _drive(
    graph,
    run_config,
    payload,
    *,
    recursion_limit: int = 400,
    on_update: Callable[[dict], None] | None = None,
) -> None:
    """Stream until the next pause/interrupt; swallow the recursion cap.

    ``on_update`` is an OPTIONAL per-super-step sink (default ``None`` — every
    existing caller is untouched). When given, it is called with each
    ``stream_mode="updates"`` payload as it streams — a ``{node: delta}`` dict
    for one super-step. This is the seam the spec-017 transcript capture taps to
    accumulate an ordered per-game event log: it must read the deltas *as they
    stream*, because the per-Night pointing channels (``night_round_picks`` /
    ``night_rounds_log``) are reset every Night in ``night_open``, so the final
    state holds only the last Night's picks. With no ``on_update`` the updates
    are discarded exactly as before (the historical ``for _ in stream: pass``).
    """
    from langgraph.errors import GraphRecursionError

    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", recursion_limit)
    try:
        for update in graph.stream(payload, bounded, stream_mode="updates"):
            if on_update is not None:
                on_update(update)
    except GraphRecursionError:
        return


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for obj in task.interrupts or ():
            return obj.value
    return None


def _play_one_game(game_index: int, base_seed: int, max_rounds: int) -> GameResult:
    """Drive a single real-LLM game; return the AI Day speeches it produced."""
    # Imports are local so a missing Bedrock cred fails here (per game) with a
    # clear message rather than at module import.
    from graphia.config import load_config
    from graphia.graph import build_graph, make_run_config

    # Seed the module-global RNG so the role deal / shuffles / tie-breaks are
    # reproducible per game (the LLM dialogue stays non-deterministic — that's
    # exactly what we're measuring).
    random.seed(base_seed + game_index)

    with tempfile.TemporaryDirectory(prefix=f"graphia-eval-{game_index}-") as ckpt:
        os.environ["GRAPHIA_CHECKPOINT_DIR"] = ckpt
        config = load_config()
        graph, thread_id = build_graph(config)
        run_config = make_run_config(thread_id)

        # Stream to the name interrupt, then resume with the human name.
        _drive(graph, run_config, {"messages": []})
        first = _collect_interrupt(graph, run_config)
        if not first or first.get("kind") != "name":
            return GameResult(game_index, [], error=f"no name interrupt: {first!r}")
        _drive(graph, run_config, Command(resume=HUMAN_NAME))

        rounds = 0
        line_idx = 0
        # Answer interrupts until the game ends (no `.next`) or the round cap.
        for _ in range(max_rounds * 12 + 20):  # generous per-interrupt budget
            if rounds >= max_rounds:
                break
            snapshot = graph.get_state(run_config)
            if not snapshot.next:
                break  # reached end_screen / END
            iv = _collect_interrupt(graph, run_config)
            if iv is None:
                _drive(graph, run_config, None)
                continue
            kind = iv.get("kind")
            if kind == "day_turn":
                resume: str = HUMAN_LINES[line_idx % len(HUMAN_LINES)]
                line_idx += 1
                rounds += 1
            elif kind == "vote":
                resume = "no"  # never execute, so games run long enough to sample
            elif kind == "point":
                options = iv.get("options") or []  # human is law-abiding; defensive
                resume = options[0]["id"] if options else ""
            else:
                return GameResult(game_index, [], error=f"unexpected interrupt {kind!r}")
            _drive(graph, run_config, Command(resume=resume))

        state = graph.get_state(run_config).values
        players = state.get("players", {})
        ai_names = {p.name for p in players.values() if not p.is_human}
        speeches = [
            m.content.strip()
            for m in state.get("messages", [])
            if isinstance(m, AIMessage)
            and getattr(m, "name", None) in ai_names
            and isinstance(m.content, str)
            and m.content.strip()
        ]
        return GameResult(game_index, speeches)


def _cluster_near_dups(texts: list[str], threshold: float) -> list[list[int]]:
    """Greedy union-find clustering of near-identical messages (difflib ratio)."""
    n = len(texts)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    for i in range(n):
        for j in range(i + 1, n):
            if difflib.SequenceMatcher(None, texts[i], texts[j]).ratio() >= threshold:
                union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _analyze(speeches: list[str], threshold: float) -> DiversityStats:
    norm = [_normalize(s) for s in speeches]
    total = len(norm)
    if total == 0:
        return DiversityStats()
    counts = Counter(norm)
    distinct = len(counts)
    clusters = _cluster_near_dups(norm, threshold)
    in_dup = sum(len(c) for c in clusters if len(c) > 1)
    max_cluster = max((len(c) for c in clusters), default=0)
    # Representative text of the biggest clusters (original, not normalized).
    big = sorted((c for c in clusters if len(c) > 1), key=len, reverse=True)[:3]
    top = [(len(c), speeches[c[0]]) for c in big]
    return DiversityStats(
        total=total,
        distinct=distinct,
        exact_dup_rate=1 - distinct / total,
        near_dup_rate=in_dup / total,
        max_cluster=max_cluster,
        top_clusters=top,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure AI Day-dialogue repetition on real Nova.")
    ap.add_argument("--games", type=int, default=5, help="number of games to play (real LLM cost)")
    ap.add_argument("--seed", type=int, default=20260610, help="base seed for game structure")
    ap.add_argument("--max-rounds", type=int, default=10, help="cap Day rounds per game (cost control)")
    ap.add_argument("--threshold", type=float, default=0.85, help="difflib ratio for 'near-duplicate'")
    ap.add_argument("--min-distinct", type=float, default=None, help="if set, exit 1 when pooled distinct-ratio falls below this")
    ap.add_argument("--json", type=str, default=None, help="write a JSON report to this path")
    args = ap.parse_args(argv)

    os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")  # avoid night-point interrupts

    print(f"Playing {args.games} game(s) on the real gameplay model (Nova). This costs tokens and is non-deterministic.\n")
    results: list[GameResult] = []
    pooled: list[str] = []
    print(f"{'game':>4} {'speeches':>8} {'distinct%':>9} {'exactdup%':>9} {'neardup%':>8} {'maxclust':>8}")
    for i in range(args.games):
        r = _play_one_game(i, args.seed, args.max_rounds)
        results.append(r)
        if r.error:
            print(f"{i:>4}  ERROR: {r.error}")
            continue
        pooled.extend(r.speeches)
        s = _analyze(r.speeches, args.threshold)
        dpct = 100 * s.distinct / s.total if s.total else 0
        print(f"{i:>4} {s.total:>8} {dpct:>8.0f}% {100*s.exact_dup_rate:>8.0f}% {100*s.near_dup_rate:>7.0f}% {s.max_cluster:>8}")

    agg = _analyze(pooled, args.threshold)
    print("\n=== AGGREGATE (pooled across games) ===")
    print(f"  speeches:        {agg.total}")
    if agg.total:
        print(f"  distinct ratio:  {100*agg.distinct/agg.total:.0f}%  ({agg.distinct} unique)")
        print(f"  exact-dup rate:  {100*agg.exact_dup_rate:.0f}%")
        print(f"  near-dup rate:   {100*agg.near_dup_rate:.0f}%  (threshold {args.threshold})")
        print(f"  largest cluster: {agg.max_cluster}")
        if agg.top_clusters:
            print("  most-repeated lines:")
            for n, text in agg.top_clusters:
                print(f"    x{n}: {text[:100]}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(
                {
                    "games": args.games,
                    "seed": args.seed,
                    "threshold": args.threshold,
                    "aggregate": {
                        "speeches": agg.total,
                        "distinct": agg.distinct,
                        "distinct_ratio": (agg.distinct / agg.total) if agg.total else None,
                        "exact_dup_rate": agg.exact_dup_rate,
                        "near_dup_rate": agg.near_dup_rate,
                        "max_cluster": agg.max_cluster,
                    },
                    "per_game": [
                        {"index": r.index, "speeches": len(r.speeches), "error": r.error}
                        for r in results
                    ],
                },
                fh,
                indent=2,
            )
        print(f"\nWrote JSON report to {args.json}")

    if args.min_distinct is not None and agg.total:
        ratio = agg.distinct / agg.total
        if ratio < args.min_distinct:
            print(f"\nFAIL: pooled distinct ratio {ratio:.2f} < --min-distinct {args.min_distinct}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
