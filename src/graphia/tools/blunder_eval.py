"""AI Blunder Tracking harness — the make-gated quality-ledger run (spec 011).

One run plays a batch of *real*-provider games against a chosen provider,
counts a family of self-consistency blunders (self-vote, Mafioso peer-vote,
third-person self-talk) plus the spec-009 repetition measure, and appends one
dated record to ``evals/blunder-ledger.yaml`` — so AI quality becomes a
tracked, comparable, history-backed property of the repo rather than an
anecdote (functional-spec 011 §1).

Like ``eval_dialogue`` and ``ollama_smoke``, it reaches a **real** model and so
lives *outside* ``pytest``: the mocked suite never runs it. It is invoked
deliberately, behind ``make blunder-eval``::

    make blunder-eval ARGS="--provider ollama --games 5"
    make blunder-eval ARGS="--provider bedrock --games 5 --seed 20260613"

Bedrock runs need live AWS credentials and cost real tokens; the Ollama path
needs the verified local model pair installed (the boot preflight enforces
this fail-fast, before any game time is burned).

This module carries the CLI + provider isolation (Slice 1, Task 1), the
scripted-game driver + repetition scorer (Task 2), and the hand-rendered
write-only YAML ledger writer (Task 3 — :func:`render_record` /
:func:`append_record`, appending one ``---``-separated document per run to
``evals/blunder-ledger.yaml``). The blunder/action detectors and full
provenance block land in later slices. The module imports with no side effects
and ``--help`` works on its own.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langgraph.types import Command

# Reuse the established make-gated harness driver (scripted human + stream-to-
# interrupt pump) rather than re-implementing it — same import ``ollama_smoke``
# uses. ``HUMAN_LINES`` is the neutral, distinct human-turn pool whose lines are
# *excluded* from the AI metric; ``HUMAN_NAME`` is the scripted human's name.
from graphia.tools.eval_dialogue import (
    HUMAN_LINES,
    HUMAN_NAME,
    _collect_interrupt,
    _drive,
)

# The spec-009 name-masked near-duplicate measure — IMPORTED, never
# reimplemented (functional-spec 011 §2.1; tech-spec §2.1 ``repetition`` row).
# ``_mask_names`` + ``_normalize`` produce the name-masked normalized text and
# ``_clusters`` at ratio 0.85 is the exact near-duplicate definition behind
# ``repetition_experiment``'s decision metric (its ``_near_dup_rate(masked,
# 0.85)``). We reuse ``_clusters`` directly (not ``_near_dup_rate``) so we can
# surface the near-duplicate *count* and *denominator* next to the rate, while
# the count stays identical to ``_near_dup_rate``'s own ``sum(len(c) ... > 1)``.
from graphia.tools.repetition_experiment import (
    _clusters as _spec009_clusters,
    _mask_names as _spec009_mask_names,
    _normalize as _spec009_normalize,
)

# The spec-009 near-duplicate similarity threshold (difflib ratio). Kept as a
# module constant so the one repetition rule sits beside the version stamp the
# later slices add, and so the pure scorer and any offline test share one value.
_NEAR_DUP_THRESHOLD = 0.85

# Metric-definitions version stamped into every ledger record (functional-spec
# 011 §2.3): any change to a detection rule or denominator bumps it, so rates
# measured under different rules are visibly incomparable in the ledger itself.
# Slice 2 (the game-record detectors) is the task that *owns* this constant and
# the rule set behind it; until it lands here we default to 1 and read whatever
# value that slice eventually defines, so the record always carries the live
# version without this task pinning it.
METRICS_VERSION = 1

# The repo-committed quality ledger (tech-spec 011 §2.5). Top-level ``evals/``
# dir at the repo root; one ``---``-separated YAML document is appended per run.
# Resolved from this module's location (``src/graphia/tools/blunder_eval.py`` →
# four parents up is the repo root) so it is correct regardless of the cwd a
# ``make blunder-eval`` run is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER_PATH = _REPO_ROOT / "evals" / "blunder-ledger.yaml"

# Provider literals kept as a module constant so the CLI choices and any later
# type narrowing share one source of truth.
type Provider = Literal["ollama", "bedrock"]
PROVIDERS: tuple[Provider, ...] = ("ollama", "bedrock")

# Cloud-store env vars an eval run must never touch. A make-included / wire-env'd
# ``.env`` carries a deployed stack's AgentCore Memory / Gateway / career-stats
# ids, and the diary/career factories gate on those ids alone — so an eval game
# would emit career events to AWS (and die on an expired SSO token, as observed
# in ollama_smoke). The offline config gate (config.py) blanks these *only* for
# the ollama provider, so the harness pops them itself for BOTH providers:
# eval games must never pollute the real career-stats stores. Mirrors the exact
# isolation set ``ollama_smoke.main`` applies.
_CLOUD_STORE_ENV_VARS: tuple[str, ...] = (
    "GRAPHIA_MEMORY_ID",
    "GRAPHIA_CAREER_MEMORY_ID",
    "GRAPHIA_GATEWAY_ID",
    "GRAPHIA_GATEWAY_URL",
    "GRAPHIA_STATS_STRATEGY_ID",
)

# A small, sensible default batch size: enough to see a rate, cheap enough to
# run on a whim (and, for Bedrock, not burn many tokens by accident).
_DEFAULT_GAMES = 5


@dataclass(slots=True)
class EvalResult:
    """Outcome of one harness run — grows in later Slice-1 tasks.

    Slice 1, Task 2 fills ``ai_speeches`` and the ``repetition`` metric it feeds
    (``{rate, count, denominator}``), plus the resolved model names; Task 3
    turns this into the appended ledger record. ``run_eval`` returns this object
    and the CLI/ledger task persists it.
    """

    provider: Provider
    large_model: str = ""
    small_model: str = ""
    games_attempted: int = 0
    games_completed: int = 0
    games_failed_early: int = 0
    ai_speeches: list[str] = field(default_factory=list)
    # The one metric this slice computes, shaped as ``{rate, count, denominator}``
    # (functional-spec 011 §2.1: every behaviour is a rate with its denominator
    # visible). Slice 2's detectors add their own ``{rate, count, denominator}``
    # entries to this same map.
    metrics: dict[str, dict[str, float | int]] = field(default_factory=dict)


def _isolate_cloud_stores() -> None:
    """Pop the cloud-store env vars so eval games stay off the career stores.

    Applied for **both** providers (the config offline-gate only covers
    ollama). Also clears ``GRAPHIA_REMOTE`` — an eval run is always local-mode
    against a real provider, and the ollama provider contradicts remote mode in
    ``load_config``; mirrors ``ollama_smoke.main``.
    """
    os.environ.pop("GRAPHIA_REMOTE", None)
    for var in _CLOUD_STORE_ENV_VARS:
        os.environ.pop(var, None)


def _apply_model_overrides(
    large_model: str | None, small_model: str | None
) -> None:
    """Route ``--large-model`` / ``--small-model`` through the game's env vars.

    These map onto the Ollama tier env (``GRAPHIA_OLLAMA_LARGE_MODEL`` /
    ``GRAPHIA_OLLAMA_SMALL_MODEL``), exactly as ``ollama_smoke`` selects a pair.
    For the Bedrock provider the tier model ids are fixed in ``graphia.llm`` and
    not env-driven, so these overrides are inert there (a no-op by design); the
    CLI surfaces that in the flag help.
    """
    if large_model:
        os.environ["GRAPHIA_OLLAMA_LARGE_MODEL"] = large_model
    if small_model:
        os.environ["GRAPHIA_OLLAMA_SMALL_MODEL"] = small_model


def _seed_game(base_seed: int | None, game_index: int) -> None:
    """Seed the module-global RNG for one game's *structure* (the driver hook).

    The variance-reduction pattern shared by ``eval_dialogue`` and
    ``repetition_experiment``: game ``i`` uses ``base_seed + i`` so the role
    deal / speaking order / tie-breaks are reproducible per game across runs,
    while the LLM dialogue stays non-deterministic — which is exactly the thing
    being measured (architecture §6). A no-op when no ``--seed`` is given, so
    games vary freely.

    The Slice-1-Task-2 driver calls this once per game, before building the
    graph. It lives here (not in the driver) so the seed policy sits beside the
    CLI that owns ``--seed``.
    """
    if base_seed is not None:
        random.seed(base_seed + game_index)


def _resolved_model_names(config: object) -> tuple[str, str]:
    """Resolved (large, small) gameplay/mechanical model names for the record.

    Reads them off the resolved ``GraphiaConfig`` for ollama (where they are
    env-overridable, post-override here), and off ``graphia.llm``'s fixed tier
    ids for bedrock (where the tier ids are not env-driven). Done at run time so
    a missing dependency surfaces with a clear message rather than at import.
    """
    provider = getattr(config, "llm_provider", None)
    if provider == "ollama":
        return (
            getattr(config, "ollama_large_model", ""),
            getattr(config, "ollama_small_model", ""),
        )
    import graphia.llm as llm_mod

    return (llm_mod._LARGE_MODEL_ID, llm_mod._SMALL_MODEL_ID)


def _ai_lines_with_names(state: dict[str, Any]) -> tuple[list[str], set[str]]:
    """Extract the AI-spoken Day lines and the AI player names from a final state.

    Mirrors ``repetition_experiment._ai_speeches`` / ``eval_dialogue``'s
    extraction exactly: a line counts only when it is an ``AIMessage`` whose
    ``name`` is a *non-human* player's name (so the scripted human's lines are
    excluded) with non-empty string content. The AI names travel alongside
    because the spec-009 measure name-masks before comparing.
    """
    players = state.get("players", {})
    ai_names = {p.name for p in players.values() if not p.is_human}
    lines = [
        m.content.strip()
        for m in state.get("messages", [])
        if isinstance(m, AIMessage)
        and getattr(m, "name", None) in ai_names
        and isinstance(m.content, str)
        and m.content.strip()
    ]
    return lines, ai_names


def score_repetition(
    ai_lines: list[str], ai_names: set[str]
) -> dict[str, float | int]:
    """Pure scorer for the ``repetition`` metric — ``{rate, count, denominator}``.

    The spec-009 name-masked near-duplicate rate at 0.85 (tech-spec §2.1): each
    AI line is name-masked (``_mask_names``) and normalized (``_normalize``),
    then greedily clustered by difflib ratio ≥ 0.85 (``_clusters``). A line is a
    near-duplicate when it lands in a cluster of size > 1 — the *exact* numerator
    ``repetition_experiment._near_dup_rate`` uses. ``denominator`` is the total
    AI spoken lines; ``rate`` = count / denominator (0.0 when no lines).

    DRIVER-INDEPENDENT BY DESIGN: takes a plain list of AI lines + names, so
    Slice 1 Task 4 can unit-test it on a synthetic list with no live model.
    """
    denominator = len(ai_lines)
    if denominator == 0:
        return {"rate": 0.0, "count": 0, "denominator": 0}
    masked = [
        _spec009_normalize(_spec009_mask_names(line, ai_names)) for line in ai_lines
    ]
    clusters = _spec009_clusters(masked, _NEAR_DUP_THRESHOLD)
    count = sum(len(c) for c in clusters if len(c) > 1)
    return {
        "rate": count / denominator,
        "count": count,
        "denominator": denominator,
    }


# --- The repo-committed quality ledger: hand-rendered, WRITE-ONLY YAML.
#
# We render YAML by hand for our one known, flat-ish record shape rather than
# take a PyYAML dependency (tech-spec 011 §2.5): the ledger is a format we only
# ever *write*; a reader/comparison tool — and the parser dependency it needs —
# is a deliberate later increment (functional-spec 011 §2.3, Notes for the
# implementer). Key order is FIXED here so successive records diff cleanly.
#
# Slice-1 record subset: ``run`` / ``provider`` / ``quality`` / ``metrics``.
# Slice 4 adds the ``code`` (commit/branch/dirty) and ``settings`` (seed,
# max_rounds, base url) provenance blocks and the ollama digests / bedrock note
# — the gap below the existing top-level keys is the room left for them.

# YAML scalars that must be quoted to round-trip as plain strings (a date like
# ``2026-06-13`` is unambiguous unquoted, but a model id like ``nova-pro`` or
# ``qwen2.5:7b`` carries a ``:`` / digits that a YAML reader could mis-type).
# We single-quote every string value defensively and escape embedded quotes,
# which is always valid YAML regardless of content.
def _yaml_str(value: str) -> str:
    """Render a string as a single-quoted YAML scalar (always-valid, write-only)."""
    return "'" + value.replace("'", "''") + "'"


def _yaml_scalar(value: object) -> str:
    """Render one primitive (str / int / float / bool) as a YAML scalar.

    ``bool`` is checked before ``int`` (``bool`` is a subclass of ``int``) so a
    flag renders as ``true``/``false``. Floats use ``repr`` for a stable,
    round-trippable shortest form (e.g. ``0.4`` not ``0.40000000000000002``);
    a whole-valued float still carries its ``.0`` so the type stays a float in
    the text. Ints render bare. Everything else is treated as a string.
    """
    match value:
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case float():
            return repr(value)
        case str():
            return _yaml_str(value)
        case _:
            return _yaml_str(str(value))


def _yaml_block(mapping: dict[str, object], indent: int) -> list[str]:
    """Render a flat mapping of scalars as indented ``key: scalar`` YAML lines."""
    pad = "  " * indent
    return [f"{pad}{key}: {_yaml_scalar(val)}" for key, val in mapping.items()]


def render_record(result: EvalResult, run_date: str) -> str:
    """Render ONE ledger YAML document (no ``---`` separator) for a finished run.

    Pure and self-contained — takes the populated ``EvalResult`` plus the run
    date string (caller passes ``date.today().isoformat()``) and returns the
    document text with a FIXED key order, so Slice 1 Task 4 can unit-test the
    rendering and key stability with no live run. The ``append_record`` thin
    wrapper is what writes it (with the ``---`` separator) to the ledger file.

    Slice-1 record shape (a subset; Slice 4 grows it with ``code`` / ``settings``
    provenance — see the module note above):

        run:
          date: '<iso date>'
          games: <int>
          metrics_version: <int>
        provider:
          name: '<ollama|bedrock>'
          large_model: '<id>'
          small_model: '<id>'
        quality:
          games_attempted: <int>
          games_completed: <int>
          games_failed_early: <int>
        metrics:
          repetition:
            rate: <float>
            count: <int>
            denominator: <int>
    """
    lines: list[str] = []

    lines.append("run:")
    lines += _yaml_block(
        {
            "date": run_date,
            "games": result.games_attempted,
            "metrics_version": METRICS_VERSION,
        },
        indent=1,
    )

    lines.append("provider:")
    lines += _yaml_block(
        {
            "name": result.provider,
            "large_model": result.large_model,
            "small_model": result.small_model,
        },
        indent=1,
    )

    lines.append("quality:")
    lines += _yaml_block(
        {
            "games_attempted": result.games_attempted,
            "games_completed": result.games_completed,
            "games_failed_early": result.games_failed_early,
        },
        indent=1,
    )

    # ``metrics`` is a map of metric-name → {rate, count, denominator}. Slice 1
    # carries only ``repetition``; Slice 2's detectors add sibling entries here
    # under the same nested shape, each rendered in this same fixed sub-key
    # order. Iterating ``result.metrics`` preserves insertion order, so the
    # metrics appear in the order the run computed them.
    lines.append("metrics:")
    for metric_name, facets in result.metrics.items():
        lines.append(f"  {metric_name}:")
        # Fixed sub-key order for clean diffs across runs and metrics.
        ordered = {
            key: facets[key]
            for key in ("rate", "count", "denominator")
            if key in facets
        }
        lines += _yaml_block(ordered, indent=2)

    return "\n".join(lines) + "\n"


def append_record(
    result: EvalResult,
    run_date: str,
    ledger_path: Path = LEDGER_PATH,
) -> Path:
    """Append one ``---``-separated record for ``result`` to the ledger; return its path.

    Thin I/O wrapper over the pure :func:`render_record`: writes a ``---``
    document-separator line, then the rendered document, in append mode — so
    records accumulate and history is never rewritten (functional-spec 011
    §2.3). Creates the ``evals/`` directory and the ledger file on first use.
    ``ledger_path`` is injectable so Slice 1 Task 4 can append to a temp file.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    document = render_record(result, run_date)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write("---\n")
        fh.write(document)
    return ledger_path


def _play_one_game(
    args: argparse.Namespace, game_index: int
) -> tuple[list[str], set[str]]:
    """Drive one unattended scripted game on an isolated checkpoint; return its
    ``(ai_lines, ai_names)`` — lines with the human's already excluded, names so
    the spec-009 measure can name-mask. Raises on any failure — the caller in
    ``run_eval`` catches, logs, and counts it as failed-early.

    Reuses the ``eval_dialogue`` / ``ollama_smoke`` driver pattern verbatim:
    ``_seed_game`` for reproducible game *structure*, a per-game
    ``TemporaryDirectory`` wired through ``GRAPHIA_CHECKPOINT_DIR`` for checkpoint
    isolation, the scripted law-abiding human, and the stream-to-interrupt pump.
    Imports are local so a missing real-provider dependency fails here, per game.
    """
    from graphia.config import load_config
    from graphia.graph import build_graph, make_run_config

    # Reproducible game structure (role deal / speaking order / tie-breaks);
    # the LLM dialogue stays non-deterministic — that is the thing measured.
    _seed_game(args.seed, game_index)

    max_rounds = args.max_rounds if args.max_rounds is not None else 10

    with tempfile.TemporaryDirectory(prefix=f"graphia-blunder-{game_index}-") as ckpt:
        os.environ["GRAPHIA_CHECKPOINT_DIR"] = ckpt
        config = load_config()
        graph, thread_id = build_graph(config)
        run_config = make_run_config(thread_id)

        # Stream to the name interrupt, then resume with the scripted name.
        _drive(graph, run_config, {"messages": []})
        first = _collect_interrupt(graph, run_config)
        if not first or first.get("kind") != "name":
            raise RuntimeError(f"no name interrupt: {first!r}")
        _drive(graph, run_config, Command(resume=HUMAN_NAME))

        rounds = 0
        line_idx = 0
        # Answer interrupts until the game ends (no ``.next``) or the round cap.
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
                raise RuntimeError(f"unexpected interrupt {kind!r}")
            _drive(graph, run_config, Command(resume=resume))

        state = graph.get_state(run_config).values
        return _ai_lines_with_names(state)


def run_eval(config: object, args: argparse.Namespace) -> EvalResult:
    """Play the games and score them — the harness's substance.

    The provider is already forced, the cloud stores are isolated, the ollama
    preflight has passed, and ``config`` is the resolved ``GraphiaConfig``. This
    function owns the per-game loop and scoring; it returns the populated
    ``EvalResult`` for the ledger task to persist.

    Plays ``args.games`` unattended scripted games against the real provider,
    accumulates each finished game's AI-spoken lines, and computes the one
    ``repetition`` metric (Slice 1) via the imported spec-009 measure. A game
    that raises mid-run counts as failed-early (logged to stderr, run continues)
    — full provenance / run-quality lands in Slice 4; here we just keep the
    attempted / completed counts honest.
    """
    large_model, small_model = _resolved_model_names(config)
    result = EvalResult(
        provider=args.provider,
        large_model=large_model,
        small_model=small_model,
    )

    # Accumulate AI lines across games, plus the union of AI player names so the
    # spec-009 name-masking still fires across the pooled set (a name dealt in
    # one game masks that name everywhere it appears in the pool).
    pooled_lines: list[str] = []
    pooled_names: set[str] = set()

    for game_index in range(args.games):
        result.games_attempted += 1
        try:
            lines, names = _play_one_game(args, game_index)
        except Exception as exc:  # noqa: BLE001 - record and continue the batch
            result.games_failed_early += 1
            print(
                f"  game {game_index}: FAILED ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            continue
        result.games_completed += 1
        pooled_lines.extend(lines)
        pooled_names.update(names)

    result.ai_speeches = pooled_lines
    result.metrics["repetition"] = score_repetition(pooled_lines, pooled_names)

    # Persist one ``---``-separated record to the repo-committed ledger so this
    # run becomes a tracked, comparable, history-backed datapoint (functional-
    # spec 011 §2.3). ``run_eval`` still returns the result for the CLI summary.
    # ``date.today()`` is the run date (no forbidden ``Date.now()``-style call).
    ledger = append_record(result, date.today().isoformat())
    print(f"Appended one record to {ledger}")
    return result


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m graphia.tools.blunder_eval",
        description=(
            "AI Blunder Tracking harness (spec 011): play N real-provider games, "
            "count self-consistency blunders + repetition, and append one record "
            "to evals/blunder-ledger.yaml. Reaches a real model (Bedrock costs "
            "tokens; Ollama needs the verified local pair) — run it deliberately, "
            "never in the mocked test suite."
        ),
    )
    ap.add_argument(
        "--provider",
        required=True,
        choices=PROVIDERS,
        help="which real provider to measure: 'ollama' (local) or 'bedrock' (cloud Nova)",
    )
    ap.add_argument(
        "--games",
        type=int,
        default=_DEFAULT_GAMES,
        help=f"number of unattended games to play (default {_DEFAULT_GAMES}; Bedrock cost)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "base seed for game STRUCTURE (role deal / speaking order / tie-breaks); "
            "game i uses seed+i for like-for-like reruns. Omit to let structure vary. "
            "LLM dialogue stays non-deterministic regardless — that's what's measured."
        ),
    )
    ap.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="cap on scripted-human Day rounds per game (cost/time control); unset = no cap",
    )
    ap.add_argument(
        "--large-model",
        type=str,
        default=None,
        help=(
            "override the large/gameplay model (sets GRAPHIA_OLLAMA_LARGE_MODEL for "
            "ollama; ignored for bedrock, whose tier ids are fixed in graphia.llm)"
        ),
    )
    ap.add_argument(
        "--small-model",
        type=str,
        default=None,
        help=(
            "override the small/mechanical model (sets GRAPHIA_OLLAMA_SMALL_MODEL for "
            "ollama; ignored for bedrock)"
        ),
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # --- In-process provider selection + isolation, BEFORE any LLM client is
    # imported or constructed. The provider is forced via env so load_config()
    # and the production graphia.llm provider branch both observe the choice.
    os.environ["GRAPHIA_LLM_PROVIDER"] = args.provider
    _isolate_cloud_stores()
    _apply_model_overrides(args.large_model, args.small_model)
    # Deal the scripted human a law-abiding role so they never face a night-point
    # interrupt — the same default the other evals set, keeping the scripted
    # driver on the day_turn / vote / point happy path. ``setdefault`` so an
    # explicit GRAPHIA_ROLE in the environment still wins. Set before
    # ``load_config`` (config reads it at load time).
    os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")

    # Imported here (after the env is set) so config picks up the forced
    # provider and the isolation, and so a missing dependency fails with a clear
    # message at run time rather than at module import.
    from graphia.config import load_config

    config = load_config()

    # Ollama path: same fail-fast boot preflight the game uses — verify the
    # server is up and both configured models are installed before any games.
    # Raises SystemExit with an actionable message on failure; bedrock has its
    # own credential/connectivity story and is not preflighted here.
    if args.provider == "ollama":
        from graphia.preflight import run_ollama_preflight

        run_ollama_preflight(config)

    print(
        f"Blunder-eval: provider={args.provider}, {args.games} game(s)"
        + (f", base seed={args.seed}" if args.seed is not None else ", unseeded structure")
        + (f", max {args.max_rounds} Day rounds" if args.max_rounds is not None else "")
        + ". Real model; non-deterministic dialogue.",
    )

    result = run_eval(config, args)

    # Brief console summary (the durable record is the ledger — Slice 1 Task 3).
    rep = result.metrics.get("repetition", {})
    print(
        f"\nGames: {result.games_completed}/{result.games_attempted} completed"
        + (f" ({result.games_failed_early} failed early)" if result.games_failed_early else "")
        + f"; AI spoken lines: {len(result.ai_speeches)}"
    )
    if rep:
        print(
            f"repetition: rate={rep['rate']:.2f} "
            f"({rep['count']}/{rep['denominator']} near-duplicate lines @ {_NEAR_DUP_THRESHOLD})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
