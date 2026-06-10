"""Rigorous AI Day-dialogue repetition experiment.

Implements `context/spec/009-ai-collusion-awareness/repetition-experiment-design.md`:
ranks candidate fixes against the HEAD regression and the pre-spec BASE anchor,
with a paired design (shared seed set), a length cap, name-masked similarity,
a threshold sweep, self-BLEU, and bootstrap confidence intervals.

Config is applied **in-process via setattr** — no source edits, no git checkout:
- 008 window  -> graphia.nodes.day._CONTEXT_WINDOW
- 009 line    -> graphia.nodes.day.DAY_SPEAK_SYSTEM   (collusion / none / anti-parrot)
- temperature -> rebuild the graphia.llm._large singleton at the chosen temp

Hits the REAL gameplay model (Nova) — costs tokens, runs for a while. Run via:
    make repetition-experiment ARGS="--games 10"
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import random
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage
from langgraph.types import Command

HUMAN_LINES = [
    "I'm still watching everyone carefully before I commit.",
    "Hard to say yet — I want to hear another round first.",
    "I don't have a strong read on anyone right now.",
    "Let's not rush; something will give itself away soon.",
    "I'm keeping an open mind, but a few of you worry me.",
    "Nothing's obvious to me yet — keep talking.",
]
HUMAN_NAME = "Avery"

# --------------------------------------------------------------------------
# 009-line variants, derived from the real prompt so the surrounding text
# stays in sync with whatever is committed.
# --------------------------------------------------------------------------
_COLLUSION_RE = re.compile(
    r"Identical or near-identical messages from different players can\s+hint at collusion\. "
)
_ANTIPARROT = "Say something new on your turn — don't repeat or echo a point another player has already made. "


def _line_variants() -> dict[str, str]:
    from graphia.prompts import DAY_SPEAK_SYSTEM as base  # collusion version (HEAD)

    if not _COLLUSION_RE.search(base):
        raise RuntimeError("collusion line not found in DAY_SPEAK_SYSTEM; variants out of sync")
    return {
        "collusion": base,
        "none": _COLLUSION_RE.sub("", base),
        "anti-parrot": _COLLUSION_RE.sub(_ANTIPARROT, base),
    }


@dataclass(frozen=True)
class Condition:
    id: str
    line: str       # collusion | none | anti-parrot
    window: int
    temp: float


CONDITIONS: list[Condition] = [
    Condition("HEAD", "collusion", 30, 0.7),          # anchor: the regression
    Condition("BASE", "none", 10, 0.7),               # anchor: pre-spec
    Condition("noline", "none", 30, 0.7),
    Condition("antiparrot", "anti-parrot", 30, 0.7),
    Condition("win15", "collusion", 15, 0.7),
    Condition("temp1", "collusion", 30, 1.0),
    Condition("noline+win15", "none", 15, 0.7),
    Condition("noline+temp1", "none", 30, 1.0),
    Condition("sink", "none", 15, 1.0),
]


def _apply_condition(cond: Condition, variants: dict[str, str]) -> None:
    """Apply a condition's three factors via in-process setattr."""
    import graphia.llm as llm
    import graphia.nodes.day as day
    from graphia.config import load_config

    day._CONTEXT_WINDOW = cond.window
    day.DAY_SPEAK_SYSTEM = variants[cond.line]
    # Rebuild the large-model singleton at the chosen temperature.
    llm._large = ChatBedrockConverse(
        model=llm._LARGE_MODEL_ID,
        region_name=load_config().aws_region,
        temperature=cond.temp,
    )


# --------------------------------------------------------------------------
# Game driver (real LLM, scripted human, capped at K speeches).
# --------------------------------------------------------------------------


def _drive(graph, run_config, payload, *, recursion_limit: int = 400) -> None:
    from langgraph.errors import GraphRecursionError

    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", recursion_limit)
    try:
        for _ in graph.stream(payload, bounded, stream_mode="updates"):
            pass
    except GraphRecursionError:
        return


def _collect_interrupt(graph, run_config):
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for obj in task.interrupts or ():
            return obj.value
    return None


def _ai_speeches(graph, run_config, ai_names: set[str]) -> list[str]:
    state = graph.get_state(run_config).values
    return [
        m.content.strip()
        for m in state.get("messages", [])
        if isinstance(m, AIMessage)
        and getattr(m, "name", None) in ai_names
        and isinstance(m.content, str)
        and m.content.strip()
    ]


@dataclass
class GameOutcome:
    speeches: list[str]
    names: set[str]
    ended_early: bool
    error: str | None = None


def _play_game(seed: int, max_speeches: int) -> GameOutcome:
    from graphia.config import load_config
    from graphia.graph import build_graph, make_run_config

    random.seed(seed)
    with tempfile.TemporaryDirectory(prefix="graphia-exp-") as ckpt:
        os.environ["GRAPHIA_CHECKPOINT_DIR"] = ckpt
        try:
            config = load_config()
            graph, thread_id = build_graph(config)
            run_config = make_run_config(thread_id)

            _drive(graph, run_config, {"messages": []})
            first = _collect_interrupt(graph, run_config)
            if not first or first.get("kind") != "name":
                return GameOutcome([], set(), True, f"no name interrupt: {first!r}")
            _drive(graph, run_config, Command(resume=HUMAN_NAME))

            players = graph.get_state(run_config).values.get("players", {})
            ai_names = {p.name for p in players.values() if not p.is_human}

            line_idx = 0
            for _ in range(300):  # hard budget
                if len(_ai_speeches(graph, run_config, ai_names)) >= max_speeches:
                    break
                snapshot = graph.get_state(run_config)
                if not snapshot.next:
                    break  # game ended (a side won) before K speeches
                iv = _collect_interrupt(graph, run_config)
                if iv is None:
                    _drive(graph, run_config, None)
                    continue
                kind = iv.get("kind")
                if kind == "day_turn":
                    resume = HUMAN_LINES[line_idx % len(HUMAN_LINES)]
                    line_idx += 1
                elif kind == "vote":
                    resume = "no"
                elif kind == "point":
                    opts = iv.get("options") or []
                    resume = opts[0]["id"] if opts else ""
                else:
                    return GameOutcome([], ai_names, True, f"unexpected interrupt {kind!r}")
                _drive(graph, run_config, Command(resume=resume))

            speeches = _ai_speeches(graph, run_config, ai_names)[:max_speeches]
            return GameOutcome(speeches, ai_names, len(speeches) < max_speeches)
        except Exception as exc:  # noqa: BLE001 - record and continue the sweep
            return GameOutcome([], set(), True, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Metrics.
# --------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" .!?,;:'\"")


def _mask_names(text: str, names: set[str]) -> str:
    out = text
    for n in sorted(names, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(n)}\b", "<NAME>", out, flags=re.IGNORECASE)
    return out


def _clusters(texts: list[str], threshold: float) -> list[list[int]]:
    n = len(texts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if difflib.SequenceMatcher(None, texts[i], texts[j]).ratio() >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _near_dup_rate(texts: list[str], threshold: float) -> float:
    if not texts:
        return 0.0
    in_dup = sum(len(c) for c in _clusters(texts, threshold) if len(c) > 1)
    return in_dup / len(texts)


def _max_cluster(texts: list[str], threshold: float) -> int:
    if not texts:
        return 0
    return max((len(c) for c in _clusters(texts, threshold)), default=0)


def _distinct2(texts: list[str]) -> float:
    bigrams = Counter()
    for t in texts:
        toks = t.split()
        for i in range(len(toks) - 1):
            bigrams[(toks[i], toks[i + 1])] += 1
    total = sum(bigrams.values())
    return (len(bigrams) / total) if total else 1.0


def _ngrams(toks, n):
    return Counter(tuple(toks[i:i + n]) for i in range(len(toks) - n + 1))


def _bleu(cand: list[str], refs: list[list[str]], max_n: int = 3) -> float:
    if not cand or not refs:
        return 0.0
    log_sum = 0.0
    for n in range(1, max_n + 1):
        cg = _ngrams(cand, n)
        total = sum(cg.values())
        if total == 0:
            continue
        max_ref: Counter = Counter()
        for r in refs:
            for g, c in _ngrams(r, n).items():
                if c > max_ref[g]:
                    max_ref[g] = c
        clipped = sum(min(c, max_ref.get(g, 0)) for g, c in cg.items())
        p = clipped / total if clipped else 1.0 / (2 * total)  # smoothing
        log_sum += (1.0 / max_n) * math.log(p)
    cand_len = len(cand)
    closest = min((len(r) for r in refs), key=lambda rl: (abs(rl - cand_len), rl))
    bp = 1.0 if cand_len > closest else (math.exp(1 - closest / cand_len) if cand_len else 0.0)
    return bp * math.exp(log_sum)


def _self_bleu(texts: list[str]) -> float:
    toks = [t.split() for t in texts if t.split()]
    if len(toks) < 2:
        return 0.0
    return mean(_bleu(toks[i], toks[:i] + toks[i + 1:]) for i in range(len(toks)))


@dataclass
class GameMetrics:
    n: int
    ended_early: bool
    primary: float          # name-masked near-dup @0.85 (the decision metric)
    near80: float
    near85: float
    near90: float
    exact_dup: float
    distinct: float
    distinct2: float
    self_bleu: float
    max_cluster: int


def _metrics(outcome: GameOutcome) -> GameMetrics | None:
    sp = outcome.speeches
    if not sp:
        return None
    norm = [_normalize(s) for s in sp]
    masked = [_normalize(_mask_names(s, outcome.names)) for s in sp]
    counts = Counter(norm)
    return GameMetrics(
        n=len(sp),
        ended_early=outcome.ended_early,
        primary=_near_dup_rate(masked, 0.85),
        near80=_near_dup_rate(norm, 0.80),
        near85=_near_dup_rate(norm, 0.85),
        near90=_near_dup_rate(norm, 0.90),
        exact_dup=1 - len(counts) / len(sp),
        distinct=len(counts) / len(sp),
        distinct2=_distinct2(norm),
        self_bleu=_self_bleu(sp),
        max_cluster=_max_cluster(norm, 0.85),
    )


# --------------------------------------------------------------------------
# Stats: bootstrap CIs + paired comparison vs HEAD + Holm correction.
# --------------------------------------------------------------------------

_RNG = random.Random(12345)  # fixed: stats reproducible, independent of game RNG


def _boot_ci(vals: list[float], n: int = 2000, alpha: float = 0.05):
    if not vals:
        return (0.0, 0.0, 0.0)
    m = mean(vals)
    boots = sorted(mean(vals[_RNG.randrange(len(vals))] for _ in vals) for _ in range(n))
    return (m, boots[int(alpha / 2 * n)], boots[int((1 - alpha / 2) * n)])


def _paired_vs_head(head: list[float], cond: list[float], n: int = 2000):
    diffs = [c - h for h, c in zip(head, cond)]  # negative => cond repeats LESS than HEAD
    if not diffs:
        return (0.0, 0.0, 0.0, 1.0)
    m = mean(diffs)
    boots = sorted(mean(diffs[_RNG.randrange(len(diffs))] for _ in diffs) for _ in range(n))
    lo, hi = boots[int(0.025 * n)], boots[int(0.975 * n)]
    frac_pos = sum(1 for b in boots if b > 0) / n
    p = 2 * min(frac_pos, 1 - frac_pos)
    return (m, lo, hi, p)


def _holm(pairs: list[tuple[str, float]]) -> dict[str, float]:
    ordered = sorted(pairs, key=lambda kv: kv[1])
    m = len(ordered)
    adj: dict[str, float] = {}
    running = 0.0
    for rank, (key, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - rank) * p))
        adj[key] = running
    return adj


# --------------------------------------------------------------------------
# Orchestration.
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rigorous AI dialogue-repetition experiment (real Nova).")
    ap.add_argument("--games", type=int, default=10, help="games per condition (N)")
    ap.add_argument("--max-speeches", type=int, default=24, help="AI Day speeches collected per game (K)")
    ap.add_argument("--seed", type=int, default=20260610, help="base seed; game i uses seed+i, shared across conditions")
    ap.add_argument("--conditions", type=str, default=None, help="comma list of condition ids (default: all)")
    ap.add_argument("--json", type=str, default="repetition_experiment.json", help="incremental results JSON path")
    args = ap.parse_args(argv)

    os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")
    variants = _line_variants()
    seeds = [args.seed + i for i in range(args.games)]
    wanted = set(args.conditions.split(",")) if args.conditions else None
    conds = [c for c in CONDITIONS if (wanted is None or c.id in wanted)]

    print(f"Experiment: {len(conds)} conditions x {args.games} games x {args.max_speeches} speeches "
          f"on real Nova. Paired seeds {seeds[0]}..{seeds[-1]}.\n")

    # per_condition[id] = list[GameMetrics | None]  (index aligned to seeds for pairing)
    per_condition: dict[str, list] = {}
    results_json: dict = {"args": vars(args), "conditions": {}}

    for cond in conds:
        _apply_condition(cond, variants)
        metrics: list = []
        early = errors = 0
        for i, seed in enumerate(seeds):
            outcome = _play_game(seed, args.max_speeches)
            if outcome.error:
                errors += 1
            if outcome.ended_early:
                early += 1
            gm = _metrics(outcome)
            metrics.append(gm)
            print(f"  {cond.id:14s} game {i+1}/{args.games} "
                  f"n={len(outcome.speeches):2d} primary={gm.primary if gm else float('nan'):.2f}"
                  f"{' EARLY' if outcome.ended_early else ''}{' ERR' if outcome.error else ''}", flush=True)
        per_condition[cond.id] = metrics
        ok = [m for m in metrics if m is not None]
        results_json["conditions"][cond.id] = {
            "factors": {"line": cond.line, "window": cond.window, "temp": cond.temp},
            "games_ok": len(ok), "ended_early": early, "errors": errors,
            "per_game": [vars(m) for m in ok],
        }
        with open(args.json, "w") as fh:   # incremental — survives a long/interrupted run
            json.dump(results_json, fh, indent=2)

    # ---- Aggregate table ----
    def col(cid, attr):
        return [getattr(m, attr) for m in per_condition[cid] if m is not None]

    print("\n=== RESULTS (mean [95% CI]) ===")
    print(f"{'condition':14s} {'n':>3s} {'primary(masked.85)':>20s} {'near.85':>9s} {'exact':>7s} {'selfBLEU':>9s} {'len':>5s} {'early':>6s}")
    for cond in conds:
        ok = [m for m in per_condition[cond.id] if m is not None]
        if not ok:
            print(f"{cond.id:14s}  (no successful games)")
            continue
        pm, plo, phi = _boot_ci(col(cond.id, "primary"))
        n85, _, _ = _boot_ci(col(cond.id, "near85"))
        ex, _, _ = _boot_ci(col(cond.id, "exact_dup"))
        sb, _, _ = _boot_ci(col(cond.id, "self_bleu"))
        ln = mean(col(cond.id, "n"))
        early = sum(1 for m in ok if m.ended_early)
        print(f"{cond.id:14s} {len(ok):>3d} {pm:>7.2f} [{plo:.2f},{phi:.2f}]   "
              f"{n85:>7.2f} {ex:>6.2f} {sb:>8.2f} {ln:>5.1f} {early:>6d}")

    # ---- Paired comparisons vs HEAD on the primary metric ----
    if "HEAD" in per_condition:
        print("\n=== PAIRED vs HEAD (primary metric; negative = less repetition) ===")
        raw: list[tuple[str, float]] = []
        details: dict[str, tuple] = {}
        for cond in conds:
            if cond.id == "HEAD":
                continue
            # pair only on seeds where BOTH HEAD and this condition produced metrics
            h, c = [], []
            for mh, mc in zip(per_condition["HEAD"], per_condition[cond.id]):
                if mh is not None and mc is not None:
                    h.append(mh.primary)
                    c.append(mc.primary)
            if not h:
                continue
            m, lo, hi, p = _paired_vs_head(h, c)
            raw.append((cond.id, p))
            details[cond.id] = (m, lo, hi, p, len(h))
        adj = _holm(raw)
        print(f"{'condition':14s} {'Δprimary':>9s} {'95% CI':>16s} {'p(Holm)':>8s} {'pairs':>5s}")
        for cid, (m, lo, hi, p, npair) in sorted(details.items(), key=lambda kv: kv[1][0]):
            star = "*" if adj[cid] < 0.05 and hi < 0 else " "
            print(f"{cid:14s} {m:>+8.2f} [{lo:>+5.2f},{hi:>+5.2f}] {adj[cid]:>8.3f} {npair:>5d} {star}")

    print(f"\nFull per-game JSON: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
