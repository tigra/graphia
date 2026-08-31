"""Isolated persona-generation bench (spec 034, Slice 3) — real model(s), no game.

The fast dev/test loop and effort-not-results vehicle for spec 034: it exercises
the **real persona-generation path** (``generate_roster`` → ``assign_roles`` →
``generate_personas`` against the chosen provider) for N rosters **without
playing any game** — seconds per roster, not the ~21 minutes a full eval game
takes — then scores how alike each roster's cast is.

It deliberately reaches a **real** provider (``get_large`` / ``get_persona_model``
→ Amazon Nova on bedrock, or a local Ollama model), so like ``eval_dialogue`` /
``blunder_eval`` it lives OUTSIDE the mocked ``pytest`` suite and is run on
demand::

    make persona-bench ARGS="--provider ollama --rosters 5 --diversity on"
    make persona-bench ARGS="--provider ollama --rosters 10 --diversity off --semantic"
    make persona-bench ARGS="--provider bedrock --rosters 5 --semantic"
    make persona-bench ARGS="--provider ollama --rosters 5 --record"

It reuses the spec-032/033 scorers (``score_persona_sim_sum`` for the free
lexical ``persona_lex_mean`` / ``persona_lex_peak``; ``score_persona_semantic_sim``
+ ``get_embeddings`` for the opt-in ``--semantic`` ``persona_sem_mean`` /
``persona_sem_peak``) and the same cloud-store isolation ``blunder_eval`` applies,
so it never pollutes the real career/diary stores.

By default it writes NOTHING — it only prints a per-run summary plus a count of
residual collisions and regenerations. Spec 036 adds an **opt-in** ``--record``
that maps the very same :class:`BenchSummary` onto the eval harness's own
``EvalResult`` and appends ONE labelled record (``run.kind: 'persona-bench'``) to
the repo-committed quality ledger through the EXISTING renderer, so a persona
measurement can be compared against its own history. Opt-in is deliberate, not a
technical limit: the bench's value is dev-loop speed (~30 s per roster), so most
runs are throwaway and auto-recording would bury the rare real measurement.

Spec 034 Slice 3 shipped the bench (task 1) + its mocked test (task 2); the
real-model bench run (task 3) and the flag-on-vs-off A/B (Slice 4) are
developer-run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

import graphia.nodes.setup as setup_nodes
from graphia.tools.blunder_eval import (
    _NEAR_DUP_THRESHOLD,
    _REPO_ROOT,
    EvalResult,
    _resolved_model_names,
    append_record,
    collect_code_provenance,
    collect_provider_provenance,
    score_persona_semantic_sim,
    score_persona_sim_sum,
    warn_if_dirty,
)

# The value of ``run.kind`` on every record this harness writes (spec 036 §2
# Component A). Absent ⇒ a played game, so a bench record is unmistakable — and
# because ``quality.attempted``/``completed`` count ROSTERS here rather than
# games, the kind is what defines the unit.
RECORD_KIND = "persona-bench"

# Cloud-store env vars a bench run must never touch — IDENTICAL to the set
# ``blunder_eval._CLOUD_STORE_ENV_VARS`` pops, plus ``GRAPHIA_REMOTE``. A
# wire-env'd ``.env`` carries a deployed stack's Memory/Gateway/career ids and
# the diary/career factories gate on those ids alone; a bench run is always
# local-mode against a real provider, so we pop them ourselves for BOTH
# providers (the config offline-gate only covers ollama).
_CLOUD_STORE_ENV_VARS: tuple[str, ...] = (
    "GRAPHIA_MEMORY_ID",
    "GRAPHIA_CAREER_MEMORY_ID",
    "GRAPHIA_GATEWAY_ID",
    "GRAPHIA_GATEWAY_URL",
    "GRAPHIA_STATS_STRATEGY_ID",
)


@dataclass(slots=True)
class RosterResult:
    """One generated roster's per-roster scores + collision/regen counts."""

    index: int
    sim_sum: float = 0.0
    sim_max: float = 0.0
    denominator: int = 0
    sem_mean: float | None = None
    sem_peak: float | None = None
    # Residual over-threshold pairs in the FINAL roster (collisions the loop did
    # not resolve — at the default ~0.6 bar, the same near-dup band the scorer
    # uses). A diversity-on run should drive this toward zero.
    residual_collisions: int = 0
    # Generation calls beyond one-per-AI-player — a proxy for regenerations +
    # any empty-result retries (rare on a healthy model).
    regenerations: int = 0
    error: str | None = None
    # Spec 036 §2: the semantic instrument was ASKED FOR but could not be
    # reached (expired credentials, unavailable embeddings model). Distinct from
    # ``sem_mean is None`` because that is also how "not requested" reads — and
    # the two must not be confused: one is a degraded run worth flagging, the
    # other is the normal free path.
    sem_unavailable: bool = False


@dataclass(slots=True)
class BenchSummary:
    """The whole-batch summary printed at the end."""

    provider: str
    diversity_enabled: bool
    rosters_attempted: int = 0
    rosters_completed: int = 0
    # Batch lexical MEAN = Σ sim_sum / Σ denominator (pooled across rosters), and
    # batch lexical PEAK = max per-roster sim_max — mirroring ``run_eval``'s fold.
    persona_lex_mean: float | None = None
    persona_lex_peak: float | None = None
    persona_sem_mean: float | None = None
    persona_sem_peak: float | None = None
    # The POOLED pair denominators behind those four facets: Σ per-roster pairs
    # (lexical) and Σ pairs actually embedded (semantic — 0 without
    # ``--semantic``). Carried on the summary because a value-type metric is
    # meaningless without its denominator — spec 036 records them as
    # ``{mean|peak, denominator}`` — and folded in ``run_bench`` so the gate
    # deciding which rosters count has exactly ONE definition.
    lex_denominator: int = 0
    sem_denominator: int = 0
    total_collisions: int = 0
    total_regenerations: int = 0
    duration_seconds: float = 0.0
    per_roster: list[RosterResult] = field(default_factory=list)


def _isolate_cloud_stores() -> None:
    """Pop the cloud-store env vars so a bench run stays off the career stores."""
    os.environ.pop("GRAPHIA_REMOTE", None)
    for var in _CLOUD_STORE_ENV_VARS:
        os.environ.pop(var, None)


def _residual_collisions(players: dict, threshold: float) -> int:
    """Count final AI-persona pairs still at/above ``threshold`` (post-regen).

    Reuses the bench's own ``_persona_collision`` helper text/masking via the
    scorer's machinery: over the AI personas' masked table-facing text, count the
    unordered pairs whose ``difflib`` ratio is ``>= threshold`` — the residual
    collisions the regen loop could not push apart (ideally zero with diversity
    on). Built on ``setup._persona_collision`` for one shared definition.
    """
    ai_personas = [
        p.persona
        for p in players.values()
        if not p.is_human and p.persona is not None
    ]
    ai_names = {p.name for p in players.values() if not p.is_human}
    count = 0
    for a, b in combinations(ai_personas, 2):
        if setup_nodes._persona_collision(a, [b], ai_names=ai_names) >= threshold:
            count += 1
    return count


def _generate_one_roster(
    index: int,
    *,
    diversity_enabled: bool,
    collision_threshold: float,
    regen_attempts: int,
    persona_temperature: float,
    semantic: bool,
) -> RosterResult:
    """Generate ONE roster via the real path and score it (no game).

    Builds the minimal post-``assign_roles`` state — ``generate_roster`` mints AI
    names, ``assign_roles`` deals roles — then runs the REAL ``generate_personas``
    with the spec-034 flags. Local imports keep a missing provider credential
    failing here (per roster) with a clear message rather than at module import.
    """
    from graphia.nodes.setup import (
        assign_roles,
        generate_personas,
        generate_roster,
    )
    from graphia.state import GameState, PlayerState

    # A minimal human-only seed state (the bench never collects a real name).
    state: GameState = {
        "human_id": "bench-human",
        "players": {
            "bench-human": PlayerState(
                id="bench-human",
                name="Bench",
                role="law_abiding",
                is_human=True,
            )
        },
    }
    state = {**state, **generate_roster(state)}
    state = {**state, **assign_roles(state)}

    # Count generation calls to derive a regenerations proxy: wrap the single
    # ``_generate_one_persona`` seam (the bench OWNS this instrumentation, like
    # the eval harness's InstrumentedModel). Restore it after the roster.
    real_gen_one = setup_nodes._generate_one_persona
    call_count = 0

    def _counting_gen_one(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_gen_one(*args, **kwargs)

    setup_nodes._generate_one_persona = _counting_gen_one  # type: ignore[assignment]
    try:
        result = generate_personas(
            state,
            persona_diversity_enabled=diversity_enabled,
            persona_collision_threshold=collision_threshold,
            persona_regen_attempts=regen_attempts,
            persona_temperature=persona_temperature,
        )
    except Exception as exc:  # noqa: BLE001 — record and continue the batch
        return RosterResult(index=index, error=f"{type(exc).__name__}: {exc}")
    finally:
        setup_nodes._generate_one_persona = real_gen_one  # type: ignore[assignment]

    players = result["players"]
    ai_count = sum(1 for p in players.values() if not p.is_human)

    lex = score_persona_sim_sum(players)
    roster = RosterResult(
        index=index,
        sim_sum=float(lex["sim_sum"]),
        sim_max=float(lex["sim_max"]),
        denominator=int(lex["denominator"]),
        residual_collisions=_residual_collisions(players, collision_threshold),
        # Calls beyond one-per-AI-player ≈ regenerations (+ rare empty retries).
        regenerations=max(0, call_count - ai_count),
    )
    if semantic:
        # Graceful degradation (spec 036 §2, mirroring ``run_eval``'s treatment
        # of the same instrument): the embeddings client is a CLOUD dependency
        # on an otherwise-local run, so expired credentials or an unavailable
        # model must not take down a bench whose lexical measurement already
        # succeeded. ``sem_mean``/``sem_peak`` stay ``None``, so the facets are
        # simply ABSENT from the record rather than a misleading zero, and the
        # run still reports and can still be recorded.
        try:
            sem = score_persona_semantic_sim(
                players, _embed_documents
            )
            roster.sem_mean = sem["mean"]  # type: ignore[assignment]
            roster.sem_peak = sem["peak"]  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001 — optional metric, never fatal
            roster.sem_unavailable = True
            print(
                f"  roster {index}: semantic persona scoring UNAVAILABLE "
                f"({type(exc).__name__}: {exc}) — continuing without it",
                file=sys.stderr,
            )
    return roster


def _embed_documents(texts: list[str]) -> list[list[float]]:
    """Resolve the spec-033 embeddings batch callable (the patchable seam).

    Routes through ``graphia.tools.blunder_eval.get_embeddings`` so the offline
    suite's autouse ``safe_llm`` fake lands here too — the bench's mocked test
    reaches NO real Bedrock embeddings. ``--semantic`` on a real run hits Bedrock
    Titan embeddings (always Bedrock, independent of the gameplay provider).
    """
    from graphia.tools.blunder_eval import get_embeddings

    return get_embeddings().embed_documents(texts)


def run_bench(
    *,
    provider: str,
    rosters: int,
    diversity_enabled: bool,
    semantic: bool,
) -> BenchSummary:
    """Generate + score ``rosters`` rosters on ``provider`` (no game). Pure-ish.

    Sets ``GRAPHIA_LLM_PROVIDER`` and isolates the cloud stores, then loops
    :func:`_generate_one_roster`, folding each roster's lexical SUM/MAX into the
    batch mean/peak exactly as ``blunder_eval.run_eval`` does. Returns the
    :class:`BenchSummary`; the CLI prints it. No ledger is written.
    """
    os.environ["GRAPHIA_LLM_PROVIDER"] = provider
    _isolate_cloud_stores()
    # Read the tunables off the resolved config so a bench run honours the same
    # ``.env`` knobs the game does (threshold / attempts / temperature).
    from graphia.config import load_config

    config = load_config()

    summary = BenchSummary(provider=provider, diversity_enabled=diversity_enabled)
    start = time.monotonic()

    lex_sum = 0.0
    lex_denominator = 0
    lex_peak = 0.0
    sem_sum = 0.0
    sem_denominator = 0
    sem_peak = 0.0

    for i in range(rosters):
        summary.rosters_attempted += 1
        roster = _generate_one_roster(
            i,
            diversity_enabled=diversity_enabled,
            collision_threshold=config.persona_collision_threshold,
            regen_attempts=config.persona_regen_attempts,
            persona_temperature=config.persona_temperature,
            semantic=semantic,
        )
        summary.per_roster.append(roster)
        if roster.sem_unavailable:
            # Mirrors ``run_eval`` disabling its ``embed_fn`` after a failure: a
            # missing credential fails the SAME way every roster, and each retry
            # can cost a full client timeout. Stop asking; the remaining rosters
            # still get their free lexical measurement.
            semantic = False
        if roster.error is not None:
            continue
        summary.rosters_completed += 1
        summary.total_collisions += roster.residual_collisions
        summary.total_regenerations += roster.regenerations
        # Fold the lexical pooled mean + running peak.
        lex_sum += roster.sim_sum
        lex_denominator += roster.denominator
        if roster.sim_max > lex_peak:
            lex_peak = roster.sim_max
        # Fold the semantic pooled mean + running peak (when present).
        if semantic and roster.sem_mean is not None and roster.denominator > 0:
            sem_sum += roster.sem_mean * roster.denominator
            sem_denominator += roster.denominator
            if roster.sem_peak is not None and roster.sem_peak > sem_peak:
                sem_peak = roster.sem_peak

    if lex_denominator > 0:
        summary.persona_lex_mean = lex_sum / lex_denominator
        summary.persona_lex_peak = lex_peak
    if semantic and sem_denominator > 0:
        summary.persona_sem_mean = sem_sum / sem_denominator
        summary.persona_sem_peak = sem_peak
    # Publish the pooled denominators the folds just accumulated (spec 036): the
    # ledger record needs the n behind each mean/peak, and a zero here is what
    # makes the facet ABSENT from the record rather than a misleading 0.0.
    summary.lex_denominator = lex_denominator
    summary.sem_denominator = sem_denominator

    summary.duration_seconds = time.monotonic() - start
    return summary


def build_bench_record(
    summary: BenchSummary,
    config: object | None = None,
    *,
    code: dict[str, object] | None = None,
    provider_block: dict[str, object] | None = None,
    note: str = "",
) -> EvalResult:
    """Map a finished :class:`BenchSummary` onto ONE ledger ``EvalResult``.

    Spec 036 §2 Component B. Pure apart from the optional config resolution: no
    model calls, no I/O — ``main`` hands the returned result to the eval
    harness's own :func:`~graphia.tools.blunder_eval.append_record`, so ONE
    renderer keeps owning the ledger's fixed key order.

    The two provenance blocks are INJECTED (``code`` / ``provider_block``)
    rather than gathered here, exactly as ``run_eval`` gathers them once in the
    harness and hands them to its ``EvalResult``: collecting them means a ``git``
    subprocess and (on the ollama path) two HTTP GETs, which do not belong inside
    a pure mapping. ``main`` collects them with the eval's OWN collectors —
    :func:`~graphia.tools.blunder_eval.collect_code_provenance` and
    :func:`~graphia.tools.blunder_eval.collect_provider_provenance` — so a bench
    record carries the identical provenance shape a game record does (commit /
    branch / dirty; model ids plus the ollama digests + server version, or the
    bedrock invisible-updates note). Omitting either leaves it empty and
    ``render_record`` falls back to its degraded shape, which is what keeps the
    builder unit-testable with no git and no server.

    The mapping, all off the existing summary:

    * ``kind`` = :data:`RECORD_KIND` — the label that makes this record readable
      as a bench run and (per the unit-follows-kind rule) reinterprets
      ``quality.attempted``/``completed`` as ROSTERS.
    * ``provider`` / ``large_model`` / ``small_model`` — the run's provider plus
      the tier ids resolved off the config by the harness's own
      ``_resolved_model_names`` (never hand-rolled), so a bench record names the
      same models a game record would.
    * ``quality`` — rosters attempted / completed (plus the not-completed count
      under the existing ``games_failed_early`` key) and the wall-clock duration,
      so a degenerate run (many attempted, few completed) cannot masquerade as a
      clean measurement.
    * ``metrics`` — the four persona facets in their EXISTING value-type shape
      (``{mean|peak, denominator}``, deliberately no ``rate``/``count`` and so no
      Wilson band: a similarity is not a binomial proportion). Each pair is
      recorded ONLY when its pooled denominator is non-zero, so an unmeasured
      facet is **absent, never a misleading 0.0** — the semantic pair simply
      does not appear without ``--semantic`` (or when the embeddings instrument
      was unavailable), exactly as ``run_eval`` omits it.
    * ``generation`` — the process counts ``{collisions, regenerations}`` from
      the summary's pooled ``total_collisions`` / ``total_regenerations``. Its own
      small block (spec 036 §2 A), NOT folded into ``quality`` or ``metrics``:
      "how many casts shipped an over-similar pair" is the figure that carried
      the spec-034 comparison (2-in-10 → 0-in-10) and a similarity mean alone
      would have lost it. Both counts are always written — a measured
      ``collisions: 0`` is the FINDING, not a no-opportunity absence — so a bench
      record always renders the block.

    * ``settings`` — the CONDITIONS the measurement ran under, so a flag-on and a
      flag-off record can be told apart by reading them rather than by
      remembering which run was which. The resolved tier ids and (ollama-only)
      base url mirror what ``run_eval`` records; ``games`` carries the ROSTER
      count, since ``run.kind`` is what defines the unit; ``seed`` and
      ``max_days`` are genuinely inapplicable to a run that plays no game and so
      stay ``null`` rather than borrowing a config value that never applied. The
      four persona knobs land under a nested ``settings.persona`` sub-map (the
      ``settings.lineup`` precedent) — and ``diversity_enabled`` is read off the
      SUMMARY, i.e. the ``--diversity`` arm the run was actually invoked with,
      never the ambient config default, which would mislabel every flag-off arm
      and make the A/B unreadable as a pair.
    * ``notes`` — the optional ``--note`` text, rendered LAST by the existing
      renderer; an absent note renders as the ``notes: ''`` a game run shows.

    ``outcomes`` / ``vote_activity`` / ``transcript_dir`` are deliberately left
    unpopulated: a bench run plays no game, casts no vote and writes no
    transcript, and all three are ALREADY conditional in ``render_record``, so
    they omit themselves rather than rendering as hollow game blocks.
    """
    if config is None:
        # Resolved at call time (not import) so a missing dependency or env
        # surfaces here with a clear message. ``run_bench`` has already pinned
        # ``GRAPHIA_LLM_PROVIDER``, so this resolves the provider actually used.
        from graphia.config import load_config

        config = load_config()
    large_model, small_model = _resolved_model_names(config)

    # ``settings`` — the conditions this measurement ran under (spec 036 §2 B).
    # Same flat keys ``run_eval`` records, so the two kinds of record read the
    # same way, plus the nested ``persona`` sub-map that makes an A/B pair
    # readable AS A PAIR.
    base_url = getattr(config, "ollama_base_url", None)
    settings: dict[str, object] = {
        "large_model": large_model,
        "small_model": small_model,
        # Meaningful only on the ollama path; null for the Bedrock providers —
        # the identical rule ``run_eval`` applies.
        "base_url": base_url if summary.provider == "ollama" else None,
        # The unit here is ROSTERS (``run.kind`` is what says so), so the
        # existing ``games`` key carries the roster count rather than inventing a
        # parallel key the renderer and the viewer would both have to learn.
        "games": summary.rosters_attempted,
        # Explicitly absent, not borrowed: a bench run takes no ``--seed`` and
        # plays no Day, so both game-length controls are genuinely inapplicable.
        # Rendered ``null``, which reads as "did not apply" — recording the
        # ambient ``config.max_days`` would imply a cap that never ran.
        "seed": None,
        "max_days": None,
        "persona": {
            # The ARM the run was actually invoked with (``--diversity on|off``,
            # carried on the summary), NOT ``config.persona_diversity_enabled``:
            # ``run_bench`` passes the CLI value straight into
            # ``generate_personas``, so the config default can disagree with what
            # ran — and recording the default would mislabel every flag-off arm,
            # which is precisely the pair this block exists to make readable.
            "diversity_enabled": summary.diversity_enabled,
            # The three knobs ``run_bench`` read off the resolved config and
            # passed into ``generate_personas``. ``getattr`` with a ``None``
            # default (the ``run_eval`` ``max_days`` / ``lineup`` precedent) so a
            # minimal stub config renders them as honestly-absent nulls instead
            # of raising — the harness must never crash on a provenance gap.
            "collision_threshold": getattr(
                config, "persona_collision_threshold", None
            ),
            "regen_attempts": getattr(config, "persona_regen_attempts", None),
            "temperature": getattr(config, "persona_temperature", None),
        },
    }

    result = EvalResult(
        provider=summary.provider,  # type: ignore[arg-type]
        large_model=large_model,
        small_model=small_model,
        # ``quality`` reuses the attempted/completed keys; ``run.kind`` is what
        # says the unit is rosters (spec 036 §2 A).
        games_attempted=summary.rosters_attempted,
        games_completed=summary.rosters_completed,
        # ``games_failed_early`` is rendered UNCONDITIONALLY by ``render_record``,
        # so leaving it 0 would print a flat contradiction beside a partial run
        # (5 attempted / 4 completed / 0 failed). A roster whose generation
        # raised is exactly this field's meaning — the unit that did not finish —
        # so it carries attempted − completed and the block stays self-consistent.
        games_failed_early=summary.rosters_attempted - summary.rosters_completed,
        duration_seconds=round(summary.duration_seconds, 3),
        kind=RECORD_KIND,
        settings=settings,
        # The eval's OWN provenance blocks, collected by ``main`` and injected
        # here. Empty when not supplied, in which case ``render_record`` falls
        # back to its degraded shape (null commit/branch, flat provider identity)
        # rather than a hand-rolled second implementation that would drift from
        # the game path.
        code=code or {},
        provider_block=provider_block or {},
        # ``--note``: the one human-authored field, rendered LAST. Empty renders
        # as ``notes: ''``, exactly as a game run with no note does.
        notes=note,
    )

    # Insertion order IS the rendered order, so insert lexical-then-semantic —
    # the same order ``run_eval`` uses and ``METRIC_ORDER`` lists.
    lex_mean, lex_peak = summary.persona_lex_mean, summary.persona_lex_peak
    if summary.lex_denominator > 0 and lex_mean is not None and lex_peak is not None:
        result.metrics["persona_lex_mean"] = {
            "mean": lex_mean,
            "denominator": int(summary.lex_denominator),
        }
        result.metrics["persona_lex_peak"] = {
            "peak": lex_peak,
            "denominator": int(summary.lex_denominator),
        }
    sem_mean, sem_peak = summary.persona_sem_mean, summary.persona_sem_peak
    if summary.sem_denominator > 0 and sem_mean is not None and sem_peak is not None:
        result.metrics["persona_sem_mean"] = {
            "mean": sem_mean,
            "denominator": int(summary.sem_denominator),
        }
        result.metrics["persona_sem_peak"] = {
            "peak": sem_peak,
            "denominator": int(summary.sem_denominator),
        }

    # ``generation`` (spec 036 §2 A/B) — the generation-PROCESS counts, pooled
    # across the batch by ``run_bench``. Written unconditionally on the bench
    # path (both keys, explicit zero), because a run with zero collisions is
    # precisely the result worth recording; ``render_record`` omits the whole
    # block only for a game run, which never populates it.
    result.generation = {
        "collisions": int(summary.total_collisions),
        "regenerations": int(summary.total_regenerations),
    }
    return result


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def print_summary(summary: BenchSummary) -> None:
    """Print the per-roster table + batch summary to stdout."""
    diversity = "on" if summary.diversity_enabled else "off"
    print(
        f"\nPersona bench — provider={summary.provider} diversity={diversity} "
        f"(near-dup threshold {_NEAR_DUP_THRESHOLD} for the metric; collision bar "
        "from config)\n"
    )
    print(
        f"{'roster':>6} {'lex_mean':>9} {'lex_peak':>9} {'sem_mean':>9} "
        f"{'sem_peak':>9} {'collide':>8} {'regen':>6}"
    )
    for r in summary.per_roster:
        if r.error is not None:
            print(f"{r.index:>6}  ERROR: {r.error}")
            continue
        roster_mean = (
            r.sim_sum / r.denominator if r.denominator else None
        )
        print(
            f"{r.index:>6} {_fmt(roster_mean):>9} {_fmt(r.sim_max):>9} "
            f"{_fmt(r.sem_mean):>9} {_fmt(r.sem_peak):>9} "
            f"{r.residual_collisions:>8} {r.regenerations:>6}"
        )

    print("\n=== BATCH SUMMARY ===")
    print(f"  rosters:            {summary.rosters_completed}/{summary.rosters_attempted}")
    print(f"  persona_lex_mean:   {_fmt(summary.persona_lex_mean)}")
    print(f"  persona_lex_peak:   {_fmt(summary.persona_lex_peak)}")
    print(f"  persona_sem_mean:   {_fmt(summary.persona_sem_mean)}")
    print(f"  persona_sem_peak:   {_fmt(summary.persona_sem_peak)}")
    print(f"  residual collisions:{summary.total_collisions:>5}")
    print(f"  regenerations:      {summary.total_regenerations}")
    print(f"  duration:           {summary.duration_seconds:.1f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Isolated persona-generation bench (spec 034): generate + score N "
            "rosters on a real provider WITHOUT playing a game."
        )
    )
    ap.add_argument(
        "--provider",
        choices=("ollama", "bedrock", "bedrock-claude"),
        default="ollama",
        help=(
            "LLM provider for persona generation: 'ollama' (local/free), "
            "'bedrock' (Amazon Nova), or 'bedrock-claude' (Claude Haiku 4.5, "
            "spec 035). Cloud providers cost tokens."
        ),
    )
    ap.add_argument(
        "--rosters",
        type=int,
        default=5,
        help="number of rosters to generate + score.",
    )
    ap.add_argument(
        "--diversity",
        choices=("on", "off"),
        default="on",
        help="spec-034 diversified generation on (default) or off (spec-031 A/B).",
    )
    ap.add_argument(
        "--semantic",
        action="store_true",
        help="also score persona_sem_mean/peak (Bedrock Titan embeddings — costs "
        "a little even on the ollama path).",
    )
    ap.add_argument(
        "--record",
        action="store_true",
        help=(
            "append ONE labelled record (run.kind: 'persona-bench') for this run "
            "to the repo-committed quality ledger evals/blunder-ledger.yaml. "
            "OFF by default: most bench runs are throwaway dev-loop runs, and "
            "auto-recording would bury the rare real measurement."
        ),
    )
    ap.add_argument(
        "--note",
        type=str,
        default="",
        help=(
            "free-text annotation for this run (what question it was answering); "
            "recorded as the ledger record's last key, exactly as blunder-eval's "
            "--note is. Only meaningful together with --record. The one "
            "human-mutable field — leave it off to hand-edit the YAML afterwards"
        ),
    )
    args = ap.parse_args(argv)

    if args.rosters < 1:
        print("--rosters must be at least 1.", file=sys.stderr)
        return 2

    diversity_enabled = args.diversity == "on"
    print(
        f"Generating {args.rosters} roster(s) on the real {args.provider} model "
        f"(diversity {args.diversity}). No game is played; this costs model time "
        f"{'and Bedrock tokens ' if args.provider == 'bedrock' else ''}"
        f"{'+ embedding tokens ' if args.semantic else ''}and is non-deterministic."
    )
    summary = run_bench(
        provider=args.provider,
        rosters=args.rosters,
        diversity_enabled=diversity_enabled,
        semantic=args.semantic,
    )
    print_summary(summary)

    # Opt-in ledger write (spec 036). Appended UNCONDITIONALLY once ``--record``
    # is given — including for a degenerate run — mirroring ``run_eval``: a run
    # with 0-of-N rosters completed is itself a finding worth keeping, and its
    # attempted/completed pair says so plainly.
    if args.record:
        # Run provenance, gathered with the EVAL HARNESS'S OWN collectors so a
        # bench record is attributable exactly the way a game record is — same
        # git commit/branch/dirty shape, same model ids plus ollama digests +
        # server version (or the bedrock invisible-updates note). Deliberately
        # NOT re-implemented here: a second collector would drift and the two
        # kinds of record would stop being comparable.
        #
        # Collected AFTER ``run_bench`` (not before) because ``run_bench`` is
        # what pins ``GRAPHIA_LLM_PROVIDER``, so only now does ``load_config``
        # resolve the provider the run actually used — and because gathering it
        # only on the ``--record`` path keeps the common throwaway run free of a
        # git subprocess and two HTTP GETs. Both collectors degrade to nulls
        # rather than raising, so a provenance gap never costs the measurement.
        from graphia.config import load_config

        config = load_config()
        code = collect_code_provenance(_REPO_ROOT)
        warn_if_dirty(code)  # the same stderr warning ``run_eval`` prints
        large_model, small_model = _resolved_model_names(config)
        provider_block = collect_provider_provenance(
            args.provider,  # type: ignore[arg-type]
            large_model,
            small_model,
            getattr(config, "ollama_base_url", ""),
        )
        ledger = append_record(
            build_bench_record(
                summary,
                config,
                code=code,
                provider_block=provider_block,
                note=args.note,
            ),
            date.today().isoformat(),
        )
        print(f"\nAppended one {RECORD_KIND} record to {ledger}")

    return 0 if summary.rosters_completed > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
