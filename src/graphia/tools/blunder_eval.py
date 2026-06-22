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
import difflib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Literal, cast

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

# The game's own public vote lines are our parse anchors for the three exact
# action detectors (tech-spec 011 §2.1): the announce names the initiator +
# target, each per-ballot line names the voter + Yes/No. We IMPORT the format
# strings (never hardcode copies) and derive the parsing regexes from them, so a
# template reword in ``graphia.prompts`` breaks extraction loudly (and the
# offline tests, which build synthetic histories from these same constants)
# rather than letting a metric drift silently (tech-spec 011 §3, template-
# coupling risk).
from graphia.llm import DayAction
from graphia.prompts import (
    DAY_OPEN_NO_VICTIM_TEMPLATE,
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    DAY_SPEAK_USER_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
)
from graphia.state import PlayerState

# The shared structured-output proxy (tech-spec 011 §2.2): Slice 3 Task 2 uses
# it in CAPTURE mode — a ``captures`` list + a prompt-parse ``speaker_resolver``
# — to intercept every raw ``DayAction`` with its speaker attributed, so the
# ``_accept``-rejected self-vote initiation is countable. ``ollama_smoke`` is the
# count-only consumer of the same proxy; the two paths are independent.
from graphia.tools.instrument import CaptureRecord, InstrumentedModel

# The pure transcript renderer (spec 017 Slice 1 Task 2): per game, its ordered
# ``_GameCapture.events`` stream log + final ``players`` → a tagged, human-
# readable document. ``run_eval`` calls it once per game and writes the result
# under ``evals/transcripts/<run-id>/``.
from graphia.tools.eval_transcript import render_transcript

# The active scripted-player policy (spec 026): a pure, deterministic, no-LLM /
# no-RNG rule-based stand-in for the human seat in a measured run. ``_play_one_game``
# constructs the seat once per game (after the deal) and the resume branches call
# the role-matched decision instead of the passive defaults — gated by
# ``--scripted-player`` (``GRAPHIA_ACTIVE_SCRIPTED_PLAYER``). The module never
# imports ``graphia.llm`` (the structural no-model-call guarantee), so the
# scripted seat never routes through the AI capture provider.
from graphia.tools.scripted_player import (
    Decision,
    law_abiding_decision,
    mafia_decision,
    reconstruct_public_view,
    score_suspicion,
)

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
# 011 §2.3): the SINGLE SOURCE OF TRUTH for the rule set behind every metric.
# Any change to a detection rule or denominator — the near-dup threshold, the
# third-person own-name rule, a denominator definition — MUST bump this, so rates
# measured under different rules are visibly incomparable in the ledger itself.
# ``render_record`` reads this constant directly (no local default lives
# anywhere else). Slice 2 owns this constant and the rule set it stamps.
METRICS_VERSION = 1

# Wilson score confidence-interval constants for the per-metric reliability band
# (spec 011). The CI is DERIVED/SUPPLEMENTARY — it reads off each metric's
# count/denominator and does NOT change any detection rule or denominator, so it
# does NOT bump ``METRICS_VERSION``; rates measured under one rule set stay
# comparable, the interval just annotates how trustworthy each one is.
_CI_LEVEL = 0.95
# 95% two-sided z-quantile (Φ⁻¹(0.975)).
_CI_Z = 1.96


def wilson_ci(
    count: int, denominator: int, z: float = _CI_Z
) -> tuple[float, float]:
    """The 95% Wilson score interval for a proportion, clamped to ``[0, 1]``.

    A closed-form, any-``n`` confidence interval for the underlying rate of a
    metric given its ``count`` near-duplicates / blunders out of ``denominator``
    opportunities — so a reader can tell a solid ``repetition 0.45 @ n=108`` from
    a noisy ``self_vote.yes 0.50 @ n=2`` by the *width* of the band. Closed-form
    (no resampling), which is why it is the Wilson interval and not a bootstrap.

    Standard formula with p̂ = count / n::

        center = (p̂ + z²/2n) / (1 + z²/n)
        half   = z·√(p̂(1−p̂)/n + z²/4n²) / (1 + z²/n)
        (low, high) = (center − half, center + half), clamped to [0, 1]

    Edge handling: ``count == 0`` pins ``low`` to exactly ``0.0`` and
    ``count == denominator`` pins ``high`` to exactly ``1.0`` (the Wilson bound is
    already ≈ there; we make it exact). A 0 denominator yields ``(0.0, 1.0)`` —
    total ignorance — but present metrics always have ``denominator > 0``.

    Wilson score interval; **treats each line/ballot as an independent Bernoulli
    trial — for ``repetition`` (near-dup is correlated within a game) this
    UNDERSTATES uncertainty; accepted tradeoff for a closed-form any-n
    interval.** Pure (no I/O, no global state) so it is unit-testable on known
    values.
    """
    if denominator <= 0:
        return (0.0, 1.0)

    n = denominator
    p_hat = count / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4 * n * n))) / denom

    low = 0.0 if count == 0 else max(0.0, center - half)
    high = 1.0 if count == denominator else min(1.0, center + half)
    return (low, high)


# The word-boundary pattern used by ``score_third_person_self_talk`` to decide
# whether a speaker names *themselves* in their own line. A player name is
# embedded with ``re.escape`` (names are free strings and could in theory carry
# regex metacharacters), wrapped in ``\b…\b`` word boundaries, and matched
# case-insensitively — so "Mira" hits "I think Mira lied" but never "Miranda"
# or "admire". Kept as a named, documented constant beside the version stamp so
# the one speech rule and any offline test share a single definition; changing
# it is a rule change and bumps ``METRICS_VERSION``.
_OWN_NAME_BOUNDARY = r"\b{}\b"

# The repo-committed quality ledger (tech-spec 011 §2.5). Top-level ``evals/``
# dir at the repo root; one ``---``-separated YAML document is appended per run.
# Resolved from this module's location (``src/graphia/tools/blunder_eval.py`` →
# four parents up is the repo root) so it is correct regardless of the cwd a
# ``make blunder-eval`` run is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER_PATH = _REPO_ROOT / "evals" / "blunder-ledger.yaml"

# The transcript store (spec 017 §2.3): a sibling of the ledger under ``evals/``,
# resolved from the SAME repo root so the viewer can derive the absolute path
# from the ledger's parent. One ``<run-id>`` directory per run, holding the
# rendered ``game-NN.txt`` files. Deliberately NOT gitignored — the transcripts
# are ordinary untracked files curated commit-or-delete by convention (functional
# -spec §2.3); ``make clean-transcripts`` drops the untracked smoke runs.
TRANSCRIPTS_ROOT = LEDGER_PATH.parent / "transcripts"

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
    """Outcome of one harness run — provenance + run-quality + the metric family.

    Slice 1, Task 2 fills ``ai_speeches`` and the ``repetition`` metric it feeds
    (``{rate, count, denominator}``), plus the resolved model names; Task 3
    turns this into the appended ledger record. Slice 4 grows it with the
    run-provenance blocks that make a record *attributable to a code version and
    a model fingerprint* (functional-spec 011 §2.3): :attr:`code` (commit /
    branch / dirty), the enriched :attr:`provider_block` (ollama digests +
    server version, or the bedrock full-ids + invisible-updates note),
    :attr:`settings` (effective resolved values), and the wall-clock
    :attr:`duration_seconds` on the run/quality block. ``run_eval`` returns this
    object and the CLI/ledger task persists it.
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
    # entries to this same map. ``run_eval`` then post-processes each PRESENT
    # metric through ``wilson_ci`` to attach ``ci_low``/``ci_high`` siblings (the
    # derived 95% reliability band; spec 011).
    metrics: dict[str, dict[str, float | int]] = field(default_factory=dict)
    # --- Spec-013 game-dynamics blocks (tech-spec 013 §2.1, §2.2) ---
    # Orthogonal new measurements OUTSIDE the versioned ``metrics`` map (rendered
    # after ``quality``, before ``metrics``), so they do NOT bump
    # ``METRICS_VERSION`` — the ``ci_low``/``ci_high`` precedent.
    # ``outcomes`` — win-rate by side over the completed games (the four
    # partitioning buckets + the passive-human caveat note); from
    # :func:`tally_outcomes`. Empty until ``run_eval`` folds the per-game winners.
    outcomes: dict[str, object] = field(default_factory=dict)
    # ``vote_activity`` — AI vote-initiation counts by side and by game-day
    # (``{"by_side": {law_abiding, mafia}, "by_day": {day_N: ...}}``), summed
    # across completed games; from :func:`score_vote_activity`. The explicit-zero
    # inverse of ``metrics``: ``by_side`` always carries both keys with a visible
    # integer (zero included), ``by_day`` is sparse / ``{}`` when no initiations.
    vote_activity: dict[str, dict[str, int]] = field(default_factory=dict)
    # --- Slice 4 run-provenance blocks (functional-spec 011 §2.3, tech §2.4) ---
    # The code provenance: ``{"commit": <sha|None>, "branch": <str|None>,
    # "dirty": <bool>}`` from :func:`collect_code_provenance`. A clean record is
    # fully attributable to its commit; a dirty one is unmistakably marked.
    code: dict[str, object] = field(default_factory=dict)
    # The enriched provider identification — the per-model digest + server
    # version (ollama) or the full ids + invisible-updates note (bedrock), from
    # :func:`collect_provider_provenance`. ``name`` / ``large_model`` /
    # ``small_model`` mirror the flat fields above; the nested ``models`` /
    # ``server_version`` / ``note`` carry the fingerprint detail.
    provider_block: dict[str, object] = field(default_factory=dict)
    # The effective resolved settings actually used (post-env-override), so a run
    # can be repeated like-for-like: model names, base url (ollama), games, seed,
    # max_days (the runaway Day cap; spec 023 renamed it from max_rounds)
    # (functional-spec 011 §2.3).
    settings: dict[str, object] = field(default_factory=dict)
    # Wall-clock run duration in seconds (``time.monotonic()`` delta), surfaced
    # on the ``run`` and ``quality`` blocks so a degenerate run cannot masquerade
    # as a clean baseline. ``None`` until the run finishes.
    duration_seconds: float | None = None
    # Free-text run annotation (tech-spec 011 §2.5): the ONE human-mutable field.
    # Populated from ``--note`` at run time or left empty so the rendered record
    # invites hand-editing; multi-line notes render as a YAML block scalar. The
    # machine-measured fields above stay append-only/immutable.
    notes: str = ""
    # --- Spec-017 transcript link (functional-spec §2.3, tech §2.3) ---
    # The run's transcript directory NAME (the ``<run-id>`` under
    # ``evals/transcripts/``, NOT an absolute path) — written into the record as
    # ``run.transcript_dir`` so the viewer derives the absolute path from the
    # ledger's sibling ``transcripts/`` dir. A NEW additive field: empty when the
    # run wrote no transcripts, in which case ``render_record`` omits the key —
    # so OLDER records (and bare synthetic ones) simply don't carry it.
    transcript_dir: str = ""


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


def _apply_lineup_overrides(citizens: int | None, mafia: int | None) -> None:
    """Route ``--citizens`` / ``--mafia`` through the game's lineup env vars.

    Sets ``GRAPHIA_NUM_CITIZENS`` / ``GRAPHIA_NUM_MAFIA`` (spec 014) *before*
    ``load_config()`` is called — mirroring :func:`_apply_model_overrides` — so
    the configured lineup flows through the same single config choke point both
    the game and the eval read. There is deliberately NO separate CLI
    validation: an invalid lineup (e.g. ``--mafia 0`` or mafia ≥ citizens) is
    caught by the Slice-1 fail-fast guard in ``load_config`` and exits with the
    broken rule named, exactly as a bad ``.env`` would. Either flag unset leaves
    its env var untouched, so the per-var ``.env``/default (today's 5 + 2) wins.
    """
    if citizens is not None:
        os.environ["GRAPHIA_NUM_CITIZENS"] = str(citizens)
    if mafia is not None:
        os.environ["GRAPHIA_NUM_MAFIA"] = str(mafia)


def _apply_scripted_role(role: str | None) -> None:
    """Route ``--scripted-role`` onto the seat's ``GRAPHIA_ROLE`` (spec 026 D3).

    Set *before* ``load_config()`` (config reads ``GRAPHIA_ROLE`` at load time):

    - ``"random"`` → **unset** ``GRAPHIA_ROLE`` so the seat is dealt a role like
      any other player (``human_role=None`` → the game-default random deal); both
      the Law-abiding and Mafioso scripted policies then fire within one batch and
      the spec-027 ``scripted_side`` rate genuinely varies per game.
    - ``"law-abiding"`` / ``"mafia"`` → pin that role for the run.
    - omitted (``None``) → the prior behaviour: ``setdefault`` to ``law-abiding``
      so an explicit ``GRAPHIA_ROLE`` already in the environment still wins.
    """
    if role == "random":
        os.environ.pop("GRAPHIA_ROLE", None)
    elif role is not None:
        os.environ["GRAPHIA_ROLE"] = role
    else:
        os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")


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


# ===========================================================================
# Run-provenance collection (Slice 4, Task 1; functional-spec 011 §2.3,
# tech-spec §2.4).
#
# Collected ONCE per run, before any game starts, and rendered into the record
# so a record is attributable to a code version and a model fingerprint. Every
# collector DEGRADES GRACEFULLY — a missing git binary, a non-repo cwd, an
# unreachable Ollama server — records ``None`` for the unavailable field rather
# than crashing the run (a measurement run must still produce its record). The
# collectors are PURE/INJECTABLE (they take the repo root / base url / model
# names as arguments) so Task 3 can unit-test them with stubbed git/HTTP.
# ===========================================================================

# Short timeout for the Ollama provenance GETs — the same fail-fast posture as
# the boot preflight (``preflight._PREFLIGHT_TIMEOUT_SECONDS``): generous for a
# cold local server, short enough that an unreachable server degrades promptly.
_PROVENANCE_HTTP_TIMEOUT_SECONDS = 3.0

# The fixed bedrock caveat (tech-spec 011 §2.4): provider-side model weights can
# change under a stable id with no client-visible signal, so the record states
# the run date is the only proxy for "which weights answered".
_BEDROCK_UPDATE_NOTE = (
    "provider-side model updates are not observable; run date is the only proxy."
)

# The fixed passive-scripted-human caveat (spec-013 §2.1): every eval game is
# played against the scripted law-abiding human who always votes No and never
# initiates a vote, so win-rate is a CONSISTENT comparable measure across runs —
# not a true game-balance figure. Machine-emitted as ``outcomes.note`` (immutable,
# like ``_BEDROCK_UPDATE_NOTE``) and distinct from the human-mutable top-level
# ``notes`` field. Stated here once so the one caveat and any offline test share
# a single source of truth.
_OUTCOMES_HUMAN_CAVEAT = (
    "win-rate is measured against a passive scripted human (always votes No, never "
    "initiates) — a consistent comparable measure, not true game balance."
)


def collect_code_provenance(repo_root: Path) -> dict[str, object]:
    """Collect git code provenance — ``{"commit", "branch", "dirty"}``.

    Runs ``git rev-parse HEAD`` (commit), ``git rev-parse --abbrev-ref HEAD``
    (branch), and ``git status --porcelain`` (dirty = any output) via
    ``subprocess`` with ``cwd=repo_root`` (functional-spec 011 §2.3). A clean
    record is fully attributable to its commit, since prompts, detection rules,
    and settings all live in the code.

    Degrades gracefully: if ``git`` is missing or ``repo_root`` is not a git
    repository, ``commit`` / ``branch`` are recorded as ``None`` and ``dirty``
    as ``False`` (nothing to attribute, but the run still records) — never
    raises. ``dirty`` is the load-bearing flag: it is ``True`` only when a
    porcelain status genuinely reported uncommitted changes.

    PURE/INJECTABLE: ``repo_root`` is an argument so Task 3 can point it at a
    throwaway repo (or a non-repo dir) and assert the clean/dirty/unknown paths
    without touching the real working copy.
    """
    commit = _git_output(repo_root, "rev-parse", "HEAD")
    branch = _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _git_output(repo_root, "status", "--porcelain")
    # ``dirty`` is only meaningfully True when git answered AND the tree had
    # changes. A failed status (None) means "unknown" → not flagged dirty, so a
    # non-repo run is not spuriously marked modified.
    dirty = bool(porcelain) if porcelain is not None else False
    return {"commit": commit, "branch": branch, "dirty": dirty}


def _git_output(repo_root: Path, *args: str) -> str | None:
    """Run ``git <args>`` in ``repo_root`` and return stripped stdout, or ``None``.

    Returns ``None`` on any failure — a non-zero exit (not a repo), a missing
    ``git`` binary (``FileNotFoundError``), or a timeout — so a provenance gap
    degrades to ``None`` rather than propagating. ``check=False`` because a
    non-zero ``git`` exit (e.g. "not a git repository") is an expected,
    handled outcome here, not an exceptional one.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=_PROVENANCE_HTTP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def warn_if_dirty(code: dict[str, object]) -> None:
    """Print the up-front dirty-tree warning to stderr (functional-spec 011 §2.3).

    Given a working copy with unrecorded local changes, the maintainer is warned
    *before games start* that the results will not be attributable to any
    recorded version — the run proceeds regardless (iterating before committing
    is normal), and its ledger record carries ``code.dirty: true``. A clean
    (or unknown) tree prints nothing.
    """
    if code.get("dirty"):
        print(
            "WARNING: working copy has uncommitted changes — results will not "
            "be attributable to a recorded version (the record is marked "
            "code.dirty: true).",
            file=sys.stderr,
        )


def _ollama_get_json(base_url: str, path: str) -> dict[str, object] | None:
    """GET ``<base_url><path>`` and return the parsed JSON object, or ``None``.

    Mirrors the preflight HTTP posture (stdlib ``urllib`` + ``json``, short
    timeout): no httpx/requests dependency for a single GET. Returns ``None`` on
    any failure — unreachable server (``OSError`` covers URLError / socket
    timeout), a non-JSON body (``ValueError``), or a non-mapping payload — so a
    provenance gap degrades to ``None`` rather than crashing the run.
    """
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(
            url, timeout=_PROVENANCE_HTTP_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def collect_ollama_model_provenance(
    base_url: str, models: list[str]
) -> dict[str, object]:
    """Collect Ollama model fingerprints + server version (functional-spec 011 §2.3).

    Identifies the local models by *more than their names*: GETs ``/api/tags``
    for each configured model's **content digest** (a re-pulled tag with
    silently changed weights is then distinguishable) and ``/api/version`` for
    the local server's version. Returns::

        {
          "models": {"<name>": {"name": "<name>", "digest": "<sha256:...|None>"}, ...},
          "server_version": "<x.y.z|None>",
        }

    Tag-matching mirrors :func:`graphia.preflight._model_installed`: a tagless
    configured name (``qwen2.5``) resolves against any installed tag of that
    model; a tagged name (``qwen2.5:7b``) requires an exact match. A model the
    server doesn't report (or an unreachable server) yields ``digest: None`` —
    the run still records, just without that fingerprint.

    PURE/INJECTABLE: ``base_url`` and ``models`` are arguments and the only I/O
    goes through :func:`_ollama_get_json`, so Task 3 stubs that one seam to feed
    synthetic ``/api/tags`` / ``/api/version`` payloads with no live server. The
    input order of ``models`` is preserved (de-duplicated) in the result.
    """
    tags = _ollama_get_json(base_url, "/api/tags")
    installed: list[dict[str, object]] = []
    if tags is not None:
        raw = tags.get("models")
        if isinstance(raw, list):
            installed = [m for m in raw if isinstance(m, dict)]

    model_block: dict[str, object] = {}
    for name in dict.fromkeys(models):  # de-dupe, preserve first-seen order
        model_block[name] = {
            "name": name,
            "digest": _digest_for(name, installed),
        }

    version_payload = _ollama_get_json(base_url, "/api/version")
    server_version: str | None = None
    if version_payload is not None:
        candidate = version_payload.get("version")
        server_version = candidate if isinstance(candidate, str) else None

    return {"models": model_block, "server_version": server_version}


def _digest_for(
    configured: str, installed: list[dict[str, object]]
) -> str | None:
    """Find the content digest for a configured model name among installed models.

    Applies the same tag-matching rule as the preflight
    (:func:`graphia.preflight._model_installed`): an exact match for a tagged
    name, or any tag of the same base model for a tagless name. Returns the
    matched entry's ``digest`` (a ``sha256:...`` string) or ``None`` when no
    installed model matches or the matched entry carries no string digest.
    """
    has_tag = ":" in configured
    for model in installed:
        name = model.get("name")
        if not isinstance(name, str):
            continue
        matches = (
            name == configured
            if has_tag
            else name.split(":", 1)[0] == configured
        )
        if matches:
            digest = model.get("digest")
            return digest if isinstance(digest, str) else None
    return None


def collect_provider_provenance(
    provider: Provider,
    large_model: str,
    small_model: str,
    base_url: str,
) -> dict[str, object]:
    """Collect the enriched provider identification for the record.

    For ``ollama`` the models are fingerprinted by content digest plus the local
    server version (:func:`collect_ollama_model_provenance`). For ``bedrock``
    the full model ids are recorded with the fixed invisible-updates note
    (:data:`_BEDROCK_UPDATE_NOTE`) — provider-side weight changes leave no
    client-visible signal, so the run date is the only proxy (functional-spec
    011 §2.3). Both shapes carry ``name`` / ``large_model`` / ``small_model``
    so the flat identity is in the block; ollama adds ``models`` (digests) +
    ``server_version``, bedrock adds ``note``.

    Degrades gracefully via the collectors it delegates to — an unreachable
    Ollama server yields ``None`` digests / version, never a crash.
    """
    block: dict[str, object] = {
        "name": provider,
        "large_model": large_model,
        "small_model": small_model,
    }
    match provider:
        case "ollama":
            ollama = collect_ollama_model_provenance(
                base_url, [large_model, small_model]
            )
            block["models"] = ollama["models"]
            block["server_version"] = ollama["server_version"]
        case "bedrock":
            block["note"] = _BEDROCK_UPDATE_NOTE
    return block


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


def _ai_lines_with_speakers(state: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract per-line ``(speaker_name, text)`` pairs for the AI-spoken Day lines.

    The same AI-line predicate as :func:`_ai_lines_with_names` — an ``AIMessage``
    whose ``name`` is a non-human player's name with non-empty string content,
    so the scripted human's lines are excluded — but it keeps the speaker's name
    *attached to each line* rather than collapsing to a pooled list + name-set.
    ``score_third_person_self_talk`` needs that pairing to ask "does this line
    name its *own* speaker?"; the pooled ``(lines, names)`` shape that
    :func:`score_repetition` consumes cannot answer that. Both shapes are read
    from the same messages, so the speech metrics stay consistent over one game.

    The pooled extraction is left untouched so ``score_repetition``'s inputs do
    not change; a caller that wants both derives names/lines from this list when
    convenient, or calls each extractor directly.
    """
    players = state.get("players", {})
    ai_names = {p.name for p in players.values() if not p.is_human}
    return [
        (name, m.content.strip())
        for m in state.get("messages", [])
        if isinstance(m, AIMessage)
        and (name := getattr(m, "name", None)) in ai_names
        and isinstance(m.content, str)
        and m.content.strip()
    ]


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


def score_third_person_self_talk(
    lines_with_speakers: list[tuple[str, str]],
) -> dict[str, float | int]:
    """Pure scorer for ``third_person_self_talk`` — ``{rate, count, denominator}``.

    Counts AI spoken lines in which the *speaker names themselves*: the line is
    a blunder when the speaker's own name appears in their own ``text`` as a
    whole word, case-insensitively (tech-spec §2.1, ``third_person_self_talk``
    row). The name is escaped (:data:`_OWN_NAME_BOUNDARY` wraps ``re.escape``-d
    name in ``\\b…\\b``) so an own-name match never spuriously fires on a
    substring ("Mira" ≠ "Miranda") and a name with regex-special characters is
    matched literally. ``denominator`` is the total AI spoken lines (the same
    denominator as ``repetition`` — both are per-AI-spoken-line speech rates);
    ``count`` is the lines that self-name; ``rate`` = count / denominator, 0.0
    when there are no lines (no ``ZeroDivisionError`` on an empty game).

    Self-accusation (own name within a suspicion-keyword window) was deliberately
    **dropped** as too fragile to compare across runs/models (functional-spec
    §2.1) — this rule needs only the speaker's own name, no lexicon.

    DRIVER-INDEPENDENT BY DESIGN: takes plain ``(name, text)`` pairs, so Slice 2
    Task 3 can unit-test it on synthetic data with no live model.
    """
    denominator = len(lines_with_speakers)
    if denominator == 0:
        return {"rate": 0.0, "count": 0, "denominator": 0}
    count = sum(
        1
        for speaker, text in lines_with_speakers
        if re.search(
            _OWN_NAME_BOUNDARY.format(re.escape(speaker)), text, re.IGNORECASE
        )
    )
    return {
        "rate": count / denominator,
        "count": count,
        "denominator": denominator,
    }


def score_persona_near_dup(
    players: dict[str, PlayerState],
) -> dict[str, float | int | None]:
    """Pure scorer for ``persona_near_dup`` — how alike a roster's AI personas are.

    The spec-031 persona-distinctiveness measure (functional-spec §2.3; tech-spec
    §2, *Component B*). Over a game's **AI** players (the human is skipped), build
    each persona's **table-facing** text — ``personality + " " + manner + " " +
    public_persona`` — and **never** include a Mafioso's ``true_self``, so no hidden
    content enters the comparison. Each text is then name-masked
    (:func:`_spec009_mask_names` against the AI names) and normalized
    (:func:`_spec009_normalize`), exactly as ``repetition`` treats its lines — so a
    self-name token embedded in a backstory can't inflate the similarity between two
    otherwise-different characters.

    Over all **unordered pairs** of AI personas, a pair is a near-duplicate when its
    ``difflib.SequenceMatcher`` ratio is ``>= _NEAR_DUP_THRESHOLD`` (0.85) — the same
    near-duplicate definition behind ``repetition``. Returns the ``_facets``-shaped
    ``{rate, count, denominator}`` where **``denominator`` is the number of pairs
    (``C(n, 2)``)** and **``count`` is the near-duplicate pairs**. A roster with
    fewer than 2 AI personas offers no pairs (``denominator == 0``), so :func:`_facets`
    yields ``rate=None`` — *absent, not a misleading 0* — exactly as the action
    metrics do; ``run_eval`` then omits the metric from the record.

    Higher rate = personas more alike = *less* distinct (a near-duplication badness
    rate, like ``repetition``); read "distinctiveness" as ``1 − rate``.

    DRIVER-INDEPENDENT BY DESIGN: takes a plain ``players`` map, so the offline tests
    build synthetic rosters with no live model.
    """
    ai_personas = [
        p.persona
        for p in players.values()
        if not p.is_human and p.persona is not None
    ]
    ai_names = {p.name for p in players.values() if not p.is_human}
    masked = [
        _spec009_normalize(
            _spec009_mask_names(
                f"{persona.personality} {persona.manner} {persona.public_persona}",
                ai_names,
            )
        )
        for persona in ai_personas
    ]
    count = sum(
        1
        for a, b in combinations(masked, 2)
        if difflib.SequenceMatcher(None, a, b).ratio() >= _NEAR_DUP_THRESHOLD
    )
    denominator = len(masked) * (len(masked) - 1) // 2
    return _facets(count, denominator)


# ===========================================================================
# The three exact, game-record ACTION detectors (Slice 2, Task 2).
#
# These read the game's OWN public vote lines — the announce and per-ballot
# ``SystemMessage``s ``day.py`` emits — and the final ``players`` roles, with no
# LLM-output parsing (tech-spec 011 §2.1). Vote-initiation and Yes-ballot stay
# SEPARATE metrics, a clean {self, peer} × {initiation, yes} family; this slice
# owns three of the four — ``self_vote.yes``, ``peer_vote.initiation``,
# ``peer_vote.yes`` — and ``self_vote.initiation`` (the proxy-only one) is
# Slice 3.
#
# Denominator-0 representation (the spec §2.1 "absent, not a misleading 0"
# choice): an action metric whose denominator is 0 means *the game offered no
# opportunity* for that blunder (e.g. no ballot was ever cast on a mafia
# target → ``peer_vote.yes`` has no bussing opportunities). Reporting that as
# ``rate: 0.0`` would read as "the AI never bussed" when in fact it was never
# tested — a misleading 0. So a no-opportunity metric is reported ABSENT: the
# scorer returns ``rate=None`` with its 0/0 facets for unit-test introspection,
# and ``run_eval`` OMITS the metric from ``result.metrics`` entirely when the
# denominator is 0 — the renderer iterates only the entries present, so an
# omitted metric simply does not appear in that run's record. (The speech
# metrics differ: their denominator is "AI spoken lines", always > 0 in a real
# game, so they stay present with a real 0.0 when clean.)
# ===========================================================================

# The literal Yes/No labels ``day.py`` formats into ``VOTE_PER_BALLOT_TEMPLATE``
# (``vote_label = "Yes" if yes else "No"``). Kept as a named constant so the
# ballot parse anchors on the SAME label spelling the node emits; a label
# reword in ``day.py`` would fail the offline ballot-parse tests.
_BALLOT_YES_LABEL = "Yes"
_BALLOT_NO_LABEL = "No"


def _template_to_regex(template: str, fields: dict[str, str]) -> re.Pattern[str]:
    """Compile a ``str.format`` template into a named-group capture regex.

    Splits the template on its ``{field}`` placeholders and re-joins the literal
    spans (``re.escape``-d) with each field replaced by a named capture group
    whose body is supplied in ``fields`` — e.g. ``{"initiator": r"(?P<initiator>.+?)"}``.
    Deriving the regex FROM the imported format string (rather than hand-writing
    a parallel pattern) is what makes a template reword break extraction loudly:
    the literal spans must still match, so a changed announce/ballot wording
    stops parsing and the offline tests (built from the same constant) catch it.

    Every ``{...}`` placeholder in the template MUST have a matching entry in
    ``fields`` or this raises ``KeyError`` — a guard that a new placeholder in a
    reworded template is noticed at import-anchor time, not silently dropped.
    """
    pattern_parts: list[str] = []
    pos = 0
    for match in re.finditer(r"\{(\w+)\}", template):
        literal = template[pos : match.start()]
        pattern_parts.append(re.escape(literal))
        field_name = match.group(1)
        pattern_parts.append(fields[field_name])  # KeyError if unanchored
        pos = match.end()
    pattern_parts.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(pattern_parts) + "$")


# Announce: "{initiator} has called for a vote to execute {target}." Names are
# captured non-greedily so the trailing literal (" has called for a vote to
# execute ") anchors the boundary between the two free-text names. Anchored
# ``^...$`` against each ``SystemMessage`` content.
_VOTE_ANNOUNCE_RE = _template_to_regex(
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    {"initiator": r"(?P<initiator>.+?)", "target": r"(?P<target>.+?)"},
)

# Per-ballot: "{voter}: {vote_label}". The label is constrained to the exact
# Yes/No spellings ``day.py`` emits so a context-render line of the shape
# "Name: <free text>" (which never enters ``state['messages']`` anyway) could
# not be mistaken for a ballot even if one did. Voter captured non-greedily.
_VOTE_BALLOT_RE = _template_to_regex(
    VOTE_PER_BALLOT_TEMPLATE,
    {
        "voter": r"(?P<voter>.+?)",
        "vote_label": rf"(?P<vote_label>{re.escape(_BALLOT_YES_LABEL)}|{re.escape(_BALLOT_NO_LABEL)})",
    },
)

# ---------------------------------------------------------------------------
# Speaker attribution for the proxy-captured ``DayAction``s (self_vote.initiation).
#
# A captured ``DayAction`` is attributed to its speaker by reading the SPEAKER
# off the invoke prompt — ``DAY_SPEAK_USER_TEMPLATE`` opens "You are {speaker}."
# — and mapping that NAME to an id via this game's ``players``. This reads only
# the prompt the call was handed (never live graph state), so attribution cannot
# go stale or re-enter the running graph — the documented robust mechanism
# (instrument.py), and the deliberate avoidance of the mid-stream ``get_state``
# trap that bit ``tests/test_slice7_vote.py``.
#
# The anchor is DERIVED from the imported ``DAY_SPEAK_USER_TEMPLATE`` (literal
# spans before/after ``{speaker}``, ``re.escape``-d), so a reword of that
# template breaks the parse loudly in the offline tests rather than silently
# mis-attributing — the same template-coupling discipline the announce/ballot
# anchors use. The speaker name is captured non-greedily up to the first literal
# span that follows it. Only the prompt's leading line carries this, so the
# regex is matched (not full-anchored) against the ``HumanMessage`` content.
# ===========================================================================


def _speaker_anchor_regex() -> re.Pattern[str]:
    """Compile a regex that captures the ``{speaker}`` name from a Day prompt.

    Splits ``DAY_SPEAK_USER_TEMPLATE`` on its first ``{speaker}`` placeholder and
    anchors on the ``re.escape``-d literal text immediately before and after it
    (``"You are "`` … ``". Alive players at the table…"``), so the speaker name —
    captured non-greedily — is bounded by the real template text. Deriving FROM
    the imported template (not a hardcoded copy) is what makes a reword fail the
    offline attribution test loudly.
    """
    marker = "{speaker}"
    idx = DAY_SPEAK_USER_TEMPLATE.index(marker)
    before = DAY_SPEAK_USER_TEMPLATE[:idx]
    after_full = DAY_SPEAK_USER_TEMPLATE[idx + len(marker) :]
    # Anchor on the literal text up to the NEXT placeholder (or end) so the
    # trailing capture boundary is real template prose, not another field.
    next_field = re.search(r"\{\w+\}", after_full)
    after = after_full[: next_field.start()] if next_field else after_full
    return re.compile(
        re.escape(before) + r"(?P<speaker>.+?)" + re.escape(after),
        re.DOTALL,
    )


_DAY_SPEAKER_RE = _speaker_anchor_regex()


def _message_text(msg: object) -> str:
    """Return a message's string content, or '' (str content only; defensive)."""
    content = getattr(msg, "content", msg)
    return content if isinstance(content, str) else ""


def make_day_speaker_resolver(
    players: dict[str, PlayerState],
) -> "Callable[[Any], str | None]":
    """Build a prompt-parse speaker resolver bound to one game's ``players``.

    The returned callable is the proxy's ``speaker_resolver``: given an invoke's
    ``messages``, it scans them for the ``DAY_SPEAK_USER_TEMPLATE`` "You are
    {speaker}." line, extracts the speaker NAME, and maps it to that player's id
    via this game's name→id index (:func:`_name_index`). Returns ``None`` when no
    message carries the Day-speak prompt (a ``Ballot`` / ``Pointing`` / ``Roster``
    invoke, or the retry reminder alone) or the name resolves to no unique
    player — so capture stays attributed only to genuine Day-speaker turns.

    Reads ONLY the prompt it is handed — never live graph state — so attribution
    cannot go stale or re-enter the running graph (the ``get_state`` trap). Bound
    to one game because names are unique only within a game.
    """
    index = _name_index(players)

    def _resolve(messages: Any) -> str | None:
        if not isinstance(messages, (list, tuple)):
            return None
        for msg in messages:
            match = _DAY_SPEAKER_RE.search(_message_text(msg))
            if match is None:
                continue
            speaker = index.get(match.group("speaker").strip())
            return speaker.id if speaker is not None else None
        return None

    return _resolve


def score_self_vote_initiation(
    captures: "list[Any]",
) -> dict[str, float | int | None]:
    """Pure scorer for ``self_vote.initiation`` from raw proxy captures.

    The ONE vote metric no post-game state can see: a self-targeted AI vote is
    rejected by ``day._ai_day_action._accept`` (``target_id != speaker.id``)
    before it reaches game state, so it must be counted from the raw
    structured-output payload the proxy intercepts at invoke time (tech-spec
    011 §2.1). Over a list of :class:`~graphia.tools.instrument.CaptureRecord`:

    - **Denominator** — every raw ``DayAction(kind="vote")`` produced by an AI
      day-speaker (the capture's ``speaker_id`` resolved): all raw AI
      vote-initiation ATTEMPTS, accepted or rejected.
    - **Numerator** — those whose ``target_id`` equals the resolving speaker's
      own id: a self-targeted vote initiation, counted EVEN THOUGH ``_accept``
      rejects it.

    A capture with no resolved ``speaker_id`` (an unattributed payload, or a
    non-Day-speak schema) is skipped — it is not an AI day-speaker vote attempt.
    ``kind != "vote"`` captures (speaks) are not initiation attempts and are not
    in the denominator. Denominator-0 (no AI ever attempted a vote) returns
    ``rate=None`` — absent, not a misleading 0 — exactly as the Slice-2 action
    metrics do (:func:`_facets`); ``run_eval`` then OMITS it from the record.

    DRIVER-INDEPENDENT BY DESIGN: takes a plain list of capture records, so the
    offline tests build synthetic captures with no live model.
    """
    num = den = 0
    for cap in captures:
        action = getattr(cap, "raw_result", None)
        speaker_id = getattr(cap, "speaker_id", None)
        # Only AI day-speaker vote attempts enter the denominator: a resolved
        # speaker, a DayAction, and kind == "vote".
        if speaker_id is None or not isinstance(action, DayAction):
            continue
        if action.kind != "vote":
            continue
        den += 1
        if action.target_id is not None and action.target_id == speaker_id:
            num += 1
    return _facets(num, den)


@dataclass(slots=True)
class _ParsedInitiation:
    """One parsed vote-initiation announce: the initiator + target players.

    ``None`` for either side means the announced name did not resolve to a
    unique alive-or-dead player (defensive — names are validated distinct, so
    this should not happen, but an unresolved line is simply not counted, which
    keeps the metric honest rather than guessing).
    """

    initiator: PlayerState | None
    target: PlayerState | None


@dataclass(slots=True)
class _ParsedBallot:
    """One parsed per-ballot line: the voter player + their Yes/No."""

    voter: PlayerState | None
    yes: bool


def _name_index(players: dict[str, PlayerState]) -> dict[str, PlayerState]:
    """Map each UNIQUELY-held name to its player (for announce/ballot resolution).

    Names are validated distinct (case-insensitive) at roster generation, so in
    practice this is one entry per player. Defensively, a name held by more than
    one player is dropped from the index (resolves to ``None`` downstream and is
    not counted) rather than resolving ambiguously. Keyed on the exact name the
    templates format in, so resolution is an exact-string lookup.
    """
    index: dict[str, PlayerState] = {}
    seen_twice: set[str] = set()
    for player in players.values():
        if player.name in index:
            seen_twice.add(player.name)
        index[player.name] = player
    for name in seen_twice:
        index.pop(name, None)
    return index


def _is_ai(player: PlayerState | None) -> bool:
    """True for a resolved, non-human player — the AI-only filter all three
    action metrics apply (the human voter/initiator is always excluded; tech-
    spec 011 §2.1)."""
    return player is not None and not player.is_human


def _parse_vote_lines(
    messages: list,
    players: dict[str, PlayerState],
) -> tuple[list[_ParsedInitiation], list[_ParsedBallot]]:
    """Parse a game's message history into vote initiations + ballots.

    Walks every ``SystemMessage`` (the Moderator voice that carries the announce
    and per-ballot lines), matching each against the template-derived anchors
    and resolving the named initiator/target/voter back to players via
    :func:`_name_index`. Non-``SystemMessage``s and lines matching neither anchor
    are ignored. Pure over ``(messages, players)`` — no game, no model — so each
    derived scorer is unit-testable on a synthetic history built from the real
    templates.
    """
    index = _name_index(players)
    initiations: list[_ParsedInitiation] = []
    ballots: list[_ParsedBallot] = []
    for msg in messages:
        if not isinstance(msg, SystemMessage):
            continue
        content = msg.content
        if not isinstance(content, str):
            continue
        content = content.strip()
        announce = _VOTE_ANNOUNCE_RE.match(content)
        if announce is not None:
            initiations.append(
                _ParsedInitiation(
                    initiator=index.get(announce.group("initiator")),
                    target=index.get(announce.group("target")),
                )
            )
            continue
        ballot = _VOTE_BALLOT_RE.match(content)
        if ballot is not None:
            ballots.append(
                _ParsedBallot(
                    voter=index.get(ballot.group("voter")),
                    yes=ballot.group("vote_label") == _BALLOT_YES_LABEL,
                )
            )
    return initiations, ballots


def _facets(count: int, denominator: int) -> dict[str, float | int | None]:
    """Shape one action metric as ``{rate, count, denominator}``.

    A 0 denominator (no opportunity for this blunder) yields ``rate=None`` — the
    "absent, not a misleading 0" representation (spec §2.1; see the section
    header). ``run_eval`` omits such a metric from the record entirely; the
    ``None`` rate is here so a direct unit test can assert "absent" introspectively.
    """
    if denominator == 0:
        return {"rate": None, "count": count, "denominator": 0}
    return {"rate": count / denominator, "count": count, "denominator": denominator}


def _attach_ci(metrics: dict[str, dict[str, float | int | None]]) -> None:
    """Attach a Wilson ``ci_low``/``ci_high`` to every PRESENT metric, in place.

    A present metric (``denominator > 0`` — the only kind ``run_eval`` keeps in
    the record) gets two float siblings AFTER ``denominator`` from
    :func:`wilson_ci` on its own ``count``/``denominator``, giving each rate a
    reliability band (a wide one flags a small-``n`` noise rate). Absent metrics
    never reach here — ``run_eval`` already omitted them — so the
    absent-omission convention is untouched and no CI is invented for a 0/0
    metric. The CI is derived/supplementary: it reads existing fields only and
    does NOT bump ``METRICS_VERSION``.
    """
    for facets in metrics.values():
        denominator = facets.get("denominator")
        if not isinstance(denominator, int) or denominator <= 0:
            continue  # defensive — present metrics always have denominator > 0
        low, high = wilson_ci(int(facets["count"]), denominator)
        facets["ci_low"] = low
        facets["ci_high"] = high


# ===========================================================================
# Spec-013 outcome + vote-activity blocks: orthogonal new measurements that sit
# OUTSIDE the versioned ``metrics`` map (after ``quality``, before ``metrics``),
# so they do NOT bump ``METRICS_VERSION`` — exactly the ``ci_low``/``ci_high``
# precedent (a derived/supplementary measurement is not a change to a blunder-
# detection rule, so bumping would falsely flag every prior rate as
# incomparable). Both helpers are PURE over plain inputs (a winners list /
# messages + players), so Task 4 unit-tests them with no live model.
# ===========================================================================

# The ``winner`` buckets in fixed render order: the two SIDES (which carry a
# Wilson win-rate + CI), then ``runaway``, ``draw`` and ``no_winner`` (bare
# counts — none is a side, so none gets a rate). Spec 023: ``"runaway"`` (the
# in-game Day-cap hit) is its own bucket, distinct from a real win and from a
# ``draw``; ``None`` (a game that ended without any winner set — e.g. the
# anti-hang backstop) maps to ``no_winner``.
_OUTCOME_SIDES: tuple[str, str] = ("law_abiding", "mafia")


def tally_outcomes(
    winners: list[str | None],
    scripted_sides: list[str | None] | None = None,
) -> dict[str, object]:
    """Tally per-game ``winner`` values into the ``outcomes`` block (pure).

    Partitions the COMPLETED games (one entry per finished game; failed-early
    games never produce a winner and are excluded — they are already counted in
    ``quality.games_failed_early``) into mutually-exclusive buckets over the
    same ``games`` denominator (spec-013 §2.1; runaway added in spec 023)::

        law_abiding / mafia  → {wins, rate, ci_low, ci_high}   (a side win-rate)
        scripted_side        → {side, wins, rate, ci_low, ci_high}  (spec 027 —
                               the scripted stand-in's-OWN-side win rate; a
                               derived view, NOT a partition bucket)
        runaway              → bare int count (the in-game Day cap was hit —
                               a stuck/looping game, NOT a legitimate result)
        draw                 → bare int count (legacy; no live path emits it)
        no_winner            → bare int count (winner is None — never resolved)

    The four partition buckets (``law_abiding`` / ``mafia`` / ``runaway`` /
    ``draw`` / ``no_winner``) PARTITION the run, so the README-stated invariant
    holds: ``law_abiding.wins + mafia.wins + runaway + draw + no_winner ==
    games``. The two side win-rates carry a **Wilson 95% CI** over
    ``(wins, games)`` — derived/supplementary, no ``METRICS_VERSION`` bump (the
    ``ci_low``/``ci_high`` precedent). ``games == 0`` emits the block with zero
    counts and OMITS the rates/CI on the two sides (no ``ZeroDivisionError``).

    **Spec-027 ``scripted_side``** (inserted after ``mafia``, before
    ``runaway``): the win rate of *the side the scripted stand-in was on*,
    computed PER GAME from the parallel ``scripted_sides`` list — the game's
    dealt seat side (``"law_abiding"`` / ``"mafia"``, the same token ``winner``
    uses), or ``None`` when a game's side was unresolvable. A game counts as a
    scripted-side WIN iff ``winner == that game's seat side`` — so a
    ``no_winner`` / ``runaway`` game (whose ``winner`` is not a side) is a
    NON-win, yet still counts toward the **all-games** denominator (``games``,
    identical to the side rates). Shape: ``{side, wins, rate, ci_low, ci_high}``,
    where ``side`` is the run's (constant, pinned) seat-side label; reuses
    :func:`wilson_ci` for the band. Behaviour at the edges:

    - ``games == 0`` — emits ``{side, wins: 0}`` with rate/CI omitted (mirroring
      the side-rate ``games == 0`` path), provided a ``side`` label is known.
    - **No resolved side** (``scripted_sides`` absent, or every entry ``None``)
      — the entry is OMITTED ENTIRELY (absent, never a misleading ``0``), so a
      passive/older fold that did not thread sides simply has no ``scripted_side``.

    It is a *derived view* of the same games (it equals one of the side rates in
    the pinned case), NOT a new partition bucket, so the partition invariant is
    untouched.

    Returns a render-ready mapping with the fixed key order
    ``games → law_abiding → mafia → [scripted_side] → runaway → draw → no_winner
    → note``; ``note`` is the immutable :data:`_OUTCOMES_HUMAN_CAVEAT`
    passive-human caveat. PURE: takes plain lists, so the offline tests assert
    the buckets / invariant / CI / ``games==0`` / scripted-side paths on a
    synthetic list with no live model.
    """
    games = len(winners)
    counts = {side: 0 for side in _OUTCOME_SIDES}
    runaway = 0
    draw = 0
    no_winner = 0
    for winner in winners:
        match winner:
            case "law_abiding" | "mafia":
                counts[winner] += 1
            case "runaway":
                runaway += 1
            case "draw":
                draw += 1
            case _:  # None or any unrecognised value → unresolved
                no_winner += 1

    block: dict[str, object] = {"games": games}
    for side in _OUTCOME_SIDES:
        wins = counts[side]
        if games == 0:
            # No denominator: emit the bare count only, omit rate/CI so a 0/0
            # never raises and never reads as a real 0.0 rate.
            block[side] = {"wins": wins}
            continue
        low, high = wilson_ci(wins, games)
        block[side] = {
            "wins": wins,
            "rate": wins / games,
            "ci_low": low,
            "ci_high": high,
        }

    # Spec-027: the scripted-side win rate — inserted after ``mafia`` and before
    # ``runaway`` (it is a side-shaped rate, so it belongs with the side rates).
    # A derived VIEW of the same ``games``, not a partition bucket.
    scripted = _tally_scripted_side(winners, scripted_sides, games)
    if scripted is not None:
        block["scripted_side"] = scripted

    block["runaway"] = runaway
    block["draw"] = draw
    block["no_winner"] = no_winner
    block["note"] = _OUTCOMES_HUMAN_CAVEAT
    return block


def _tally_scripted_side(
    winners: list[str | None],
    scripted_sides: list[str | None] | None,
    games: int,
) -> dict[str, object] | None:
    """The ``scripted_side`` sub-block, or ``None`` when no side resolved (spec 027).

    PER-GAME numerator over the **all-games** denominator (``games``): a game is
    a scripted-side win iff its recorded ``winner`` equals THAT game's seat side
    (``scripted_sides[i]``). A ``no_winner`` / ``runaway`` game has a ``winner``
    that is not a side, so it is automatically a non-win — yet still counts toward
    ``games``. The ``side`` label is the run's pinned seat side: the single
    resolved value when the run pinned one side (the spec-026 default), else the
    most-common resolved side (a representative label for a genuinely-mixed run;
    the rate is the per-game count regardless). Returns ``None`` — entry omitted —
    when ``scripted_sides`` is absent, length-mismatched, or every entry is
    ``None`` (no side ever resolved), so an absent metric never reads as ``0``.
    The ``games == 0`` path emits ``{side, wins: 0}`` with rate/CI omitted,
    mirroring the side-rate path.
    """
    if not scripted_sides:
        return None
    resolved = [side for side in scripted_sides if side]
    if not resolved:
        return None
    # The pinned/representative label: the most-common resolved seat side. With a
    # single pinned side (the default) this is just that one label; ``Counter``
    # ties break on first-seen via ``most_common``'s stable ordering.
    side_label = Counter(resolved).most_common(1)[0][0]

    # Per-game wins: pair each winner with its own seat side (zip stops at the
    # shorter; a length mismatch defensively counts only the paired prefix).
    wins = sum(
        1
        for winner, seat in zip(winners, scripted_sides)
        if seat and winner == seat
    )
    if games == 0:
        # Mirror the side-rate games==0 path: bare {side, wins} with no rate/CI.
        return {"side": side_label, "wins": wins}
    low, high = wilson_ci(wins, games)
    return {
        "side": side_label,
        "wins": wins,
        "rate": wins / games,
        "ci_low": low,
        "ci_high": high,
    }


# The two day-open markers, full-anchored ``^...$`` regexes derived from the
# imported templates (the same template-coupling discipline the announce/ballot
# anchors use — a reword breaks the offline tests loudly). Both placeholders are
# rendered as plain non-capturing ``.+?`` (we only need to KNOW a line is a day
# boundary, never to capture its fields), which also sidesteps the duplicate
# ``{name}`` group the victim template would otherwise create.
#
# ⚠ PREFIX TRAP (spec-013 §2.2): ``DAY_OPEN_NO_VICTIM_TEMPLATE`` ("Day breaks.")
# is a strict prefix of ``DAY_OPEN_VICTIM_REVEAL_TEMPLATE`` ("Day breaks. {name}
# was…"). ``score_vote_activity`` therefore tests the VICTIM regex FIRST (full-
# anchored, so it only matches a complete victim line) and falls back to
# EXACT-EQUALITY for the no-victim line — so each day boundary increments the
# counter exactly once, never twice.
_DAY_OPEN_VICTIM_RE = _template_to_regex(
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    {"name": r".+?", "role_label": r".+?"},
)


def score_vote_activity(
    messages: list,
    players: dict[str, PlayerState],
) -> dict[str, dict[str, int]]:
    """Pure scorer for the ``vote_activity`` block — AI vote initiations by side × day.

    Mirrors :func:`score_vote_blunders`'s message-log walk (same ``SystemMessage``
    filter, ``_name_index``, AI-only via :func:`_is_ai`, template-derived anchors)
    but counts a different thing: how many vote initiations each AI SIDE makes on
    each game-day (spec-013 §2.2). Walks the history once tracking a
    ``current_day`` counter that starts at 0 and increments on every day-open
    marker; for each ``VOTE_INITIATE_ANNOUNCE`` line it resolves the initiator,
    keeps only AI initiators, reads their ``role`` (side) off the final
    ``players``, and increments ``counts[(side, day)]``. Returns::

        {"by_side": {"law_abiding": <int>, "mafia": <int>},
         "by_day":  {"day_1": <int>, "day_2": <int>, ...}}

    ⚠ EXPLICIT-ZERO — the deliberate INVERSE of ``metrics``' absent-omission
    (``_facets`` reports ``rate=None`` for a no-opportunity metric, which
    ``run_eval`` then OMITS, because a 0.0 there would misleadingly read as "the
    AI never bussed"). Here the absence of activity is ITSELF the signal (the
    Nova-silent-Day pathology must read as a committed, visible ``0``), so
    ``by_side`` ALWAYS emits BOTH side keys with integer counts by literal
    construction — a run with zero initiations renders
    ``by_side: {law_abiding: 0, mafia: 0}`` / ``by_day: {}``, never an omitted
    block. ``by_day`` is naturally SPARSE (only days with ≥1 initiation appear);
    do NOT pre-seed ``day_N: 0`` (the day count varies per game). ``by_side`` and
    ``by_day`` are independent marginals of one grand total, so
    ``sum(by_side.values()) == sum(by_day.values())``.

    The day-open prefix trap (no-victim "Day breaks." is a prefix of the victim
    line) is handled by testing :data:`_DAY_OPEN_VICTIM_RE` first and falling
    back to exact-equality with ``DAY_OPEN_NO_VICTIM_TEMPLATE`` — each boundary
    increments ``current_day`` exactly once.

    DRIVER-INDEPENDENT BY DESIGN: takes a plain message list + players map, so
    Task 4 unit-tests it on synthetic histories built from the real templates.
    """
    index = _name_index(players)
    counts: dict[tuple[str, int], int] = {}
    current_day = 0

    for msg in messages:
        if not isinstance(msg, SystemMessage):
            continue
        content = msg.content
        if not isinstance(content, str):
            continue
        content = content.strip()

        # Day-open boundary: victim regex first (full-anchored), else exact
        # no-victim equality — so the prefix never double-counts.
        if _DAY_OPEN_VICTIM_RE.match(content) is not None:
            current_day += 1
            continue
        if content == DAY_OPEN_NO_VICTIM_TEMPLATE:
            current_day += 1
            continue

        announce = _VOTE_ANNOUNCE_RE.match(content)
        if announce is None:
            continue
        initiator = index.get(announce.group("initiator"))
        if not _is_ai(initiator):
            continue
        side = initiator.role
        counts[(side, current_day)] = counts.get((side, current_day), 0) + 1

    # ``by_side`` — ALWAYS both keys, zero included (the explicit-zero guarantee).
    by_side = {
        side: sum(n for (s, _day), n in counts.items() if s == side)
        for side in _OUTCOME_SIDES
    }
    # ``by_day`` — sparse: only days with ≥1 initiation, summed across sides.
    by_day: dict[str, int] = {}
    for (_side, day), n in counts.items():
        by_day[f"day_{day}"] = by_day.get(f"day_{day}", 0) + n
    return {"by_side": by_side, "by_day": by_day}


def score_vote_blunders(
    messages: list,
    players: dict[str, PlayerState],
) -> dict[str, dict[str, float | int | None]]:
    """Pure scorer for the three exact game-record vote-blunder metrics.

    Parses the game's own announce + per-ballot lines (template-derived anchors)
    and the final ``players`` roles into the three rates, each as
    ``{rate, count, denominator}`` (tech-spec 011 §2.1). All counts are AI-only
    (the human voter/initiator is excluded). Returns a map keyed by metric name:

    - ``self_vote.yes`` — numerator: an AI Yes ballot where the voter IS the
      vote's target; denominator: AI ballots where voter == target (self-
      execution opportunities). [The vote in scope is the one whose target the
      ballot is being cast on; ``collect_votes`` polls every alive player on the
      single active target, so a voter whose name equals the active target's name
      is voting on their own execution.]
    - ``peer_vote.initiation`` — numerator: a mafia-AI initiation whose target is
      a fellow mafioso; denominator: all mafia-AI vote initiations.
    - ``peer_vote.yes`` — numerator: a mafia-AI Yes ballot whose target is a
      fellow mafioso; denominator: mafia-AI ballots cast on a mafia target
      (bussing opportunities).

    The per-ballot line names only the VOTER and their Yes/No — not the target —
    so each ballot is attributed to the vote announced most recently before it
    (the active vote ``collect_votes`` is polling). We therefore walk the history
    once, tracking the current active vote's target from each announce, and
    classify each subsequent ballot against that target until the next announce.

    Denominator-0 metrics return ``rate=None`` (absent, not a misleading 0; see
    :func:`_facets`). DRIVER-INDEPENDENT BY DESIGN: takes a plain message list +
    players map, so Slice 2 Task 3 unit-tests it on synthetic histories built
    from the real templates with no live model.
    """
    index = _name_index(players)

    self_yes_num = self_yes_den = 0
    peer_init_num = peer_init_den = 0
    peer_yes_num = peer_yes_den = 0

    # The target of the vote currently being polled — set by each announce,
    # consumed by the ballots that follow it until the next announce. Held as a
    # player so role/identity comparisons are by-id, not by-name.
    active_target: PlayerState | None = None

    for msg in messages:
        if not isinstance(msg, SystemMessage):
            continue
        content = msg.content
        if not isinstance(content, str):
            continue
        content = content.strip()

        announce = _VOTE_ANNOUNCE_RE.match(content)
        if announce is not None:
            initiator = index.get(announce.group("initiator"))
            target = index.get(announce.group("target"))
            active_target = target
            # peer_vote.initiation — mafia-AI initiations only.
            if _is_ai(initiator) and initiator.role == "mafia":
                peer_init_den += 1
                if (
                    target is not None
                    and target.role == "mafia"
                    and target.id != initiator.id
                ):
                    peer_init_num += 1
            continue

        ballot = _VOTE_BALLOT_RE.match(content)
        if ballot is None:
            continue
        voter = index.get(ballot.group("voter"))
        yes = ballot.group("vote_label") == _BALLOT_YES_LABEL
        if not _is_ai(voter) or active_target is None:
            continue

        # self_vote.yes — opportunity = an AI voting on its OWN execution
        # (voter is the active vote's target). Numerator = that ballot is Yes.
        if voter.id == active_target.id:
            self_yes_den += 1
            if yes:
                self_yes_num += 1

        # peer_vote.yes — opportunity = a mafia AI casting a ballot on a MAFIA
        # target (a fellow mafioso, not themselves). Numerator = that ballot is
        # Yes (bussing a teammate). A mafioso voting on their own execution is a
        # self-vote, not a peer-vote, so the self-target is excluded here.
        if (
            voter.role == "mafia"
            and active_target.role == "mafia"
            and active_target.id != voter.id
        ):
            peer_yes_den += 1
            if yes:
                peer_yes_num += 1

    return {
        "self_vote.yes": _facets(self_yes_num, self_yes_den),
        "peer_vote.initiation": _facets(peer_init_num, peer_init_den),
        "peer_vote.yes": _facets(peer_yes_num, peer_yes_den),
    }


# --- The repo-committed quality ledger: hand-rendered, WRITE-ONLY YAML.
#
# We render YAML by hand for our one known, flat-ish record shape rather than
# take a PyYAML dependency (tech-spec 011 §2.5): the ledger is a format we only
# ever *write*; a reader/comparison tool — and the parser dependency it needs —
# is a deliberate later increment (functional-spec 011 §2.3, Notes for the
# implementer). Key order is FIXED here so successive records diff cleanly.
#
# Record shape (Slice 4): ``run`` (date, duration, metrics_version) → ``code``
# (commit/branch/dirty) → ``provider`` (name, models-with-digests-or-ids,
# server_version/note) → ``settings`` (resolved models, base url, games, seed,
# max_days [spec 023; was max_rounds]) → ``quality`` (attempted/completed/
# failed_early, duration) →
# ``metrics`` → ``notes`` (always LAST). Key order is fixed so successive
# records diff cleanly.

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
    """Render a flat mapping of scalars as indented ``key: scalar`` YAML lines.

    A ``None`` value renders as the YAML ``null`` (an unquoted ``key: null``) so
    an absent provenance field — an unreached git commit, a missing digest, a
    bedrock run's empty server version — reads as genuinely absent, not as the
    empty string ``''``. Every non-``None`` primitive goes through
    :func:`_yaml_scalar`.
    """
    pad = "  " * indent
    return [
        f"{pad}{key}: {'null' if val is None else _yaml_scalar(val)}"
        for key, val in mapping.items()
    ]


def _yaml_nested_map(
    mapping: dict[str, object], indent: int
) -> list[str]:
    """Render a mapping whose values are themselves flat scalar sub-maps.

    Each ``key`` becomes a block header (``key:``) followed by its sub-map
    rendered by :func:`_yaml_block` one level deeper — the shape the provider
    block's ``models`` map needs (``<model-name>: {name, digest}``). Sub-map
    values are scalars (or ``None``); this is deliberately one level of nesting,
    not arbitrary recursion, matching the one known record shape.
    """
    pad = "  " * indent
    lines: list[str] = []
    for key, sub in mapping.items():
        lines.append(f"{pad}{key}:")
        assert isinstance(sub, dict)  # the one known shape; not arbitrary nesting
        lines += _yaml_block(sub, indent + 1)
    return lines


def _yaml_int_map(key: str, mapping: dict[str, int], indent: int) -> list[str]:
    """Render ``key`` over a flat ``sub: int`` map — inline ``key: {}`` when empty.

    Spec-013 needs ``vote_activity.by_day`` to render as a PRESENT-but-empty map
    (the literal inline ``key: {}``, not an omitted key) when a run had no vote
    initiations — so the explicit-zero guarantee survives into the viewport
    rather than reading as "absent". An empty map collapses onto the key line
    (``by_day: {}``), matching the tech-spec §2.2 shape and the YAML flow-mapping
    spelling a reader expects. A non-empty map emits the ``key:`` header followed
    by one ``sub: <int>`` line per entry, with the ``day_N`` keys sorted by their
    INTEGER suffix (so ``day_2`` precedes ``day_10``, not lexicographically);
    non-``day_N`` keys (if any) sort after by their string. Values are plain ints.
    """
    pad = "  " * indent
    if not mapping:
        return [f"{pad}{key}: {{}}"]
    lines = [f"{pad}{key}:"]
    lines += [
        f"{'  ' * (indent + 1)}{sub}: {_yaml_scalar(mapping[sub])}"
        for sub in sorted(mapping, key=_day_sort_key)
    ]
    return lines


def _day_sort_key(key: str) -> tuple[int, int | str]:
    """Sort key ordering ``day_N`` keys by their integer ``N`` (``day_2`` < ``day_10``).

    A ``day_<int>`` key sorts in band 0 by its integer suffix; anything else
    sorts in band 1 by its raw string, so a non-conforming key never crashes the
    integer parse and simply trails the numeric days deterministically.
    """
    if key.startswith("day_") and key[4:].isdigit():
        return (0, int(key[4:]))
    return (1, key)


def _yaml_block_scalar(key: str, value: str, indent: int) -> list[str]:
    """Render ``key`` with a multi-line string as a YAML literal block scalar.

    Emits the ``key: |`` literal-block indicator (which preserves newlines
    verbatim) followed by each content line indented one level deeper than the
    key, per the YAML spec. A trailing newline in ``value`` is dropped before
    splitting so the default block-chomping (clip: exactly one final newline)
    matches what the source string carried — and an empty final line never
    produces a stray over-indented blank that some readers reject.

    Used only by :func:`render_record` for the ``notes`` field when the note
    contains a newline; single-line notes go through the quoted-scalar path.
    """
    pad = "  " * indent
    content_pad = "  " * (indent + 1)
    lines = [f"{pad}{key}: |"]
    lines += [f"{content_pad}{line}" for line in value.rstrip("\n").split("\n")]
    return lines


def render_record(result: EvalResult, run_date: str) -> str:
    """Render ONE ledger YAML document (no ``---`` separator) for a finished run.

    Pure and self-contained — takes the populated ``EvalResult`` plus the run
    date string (caller passes ``date.today().isoformat()``) and returns the
    document text with a FIXED top-level key order, so the rendering and key
    stability are unit-testable with no live run. The ``append_record`` thin
    wrapper is what writes it (with the ``---`` separator) to the ledger file.

    Fixed key order (spec-013 §2.3 shape) — ``run`` → ``code`` → ``provider`` →
    ``settings`` → ``quality`` → ``outcomes`` → ``vote_activity`` → ``metrics``
    → ``notes`` (the two game-dynamics blocks sit after ``quality``, before
    ``metrics``; ``notes`` always LAST):

        run:
          date: '<iso date>'
          duration_seconds: <float|null>
          metrics_version: <int>
          transcript_dir: '<run-id>'   # spec 017 — the run's dir under evals/transcripts/; omitted on older runs
        code:
          commit: '<sha>' | null
          branch: '<name>' | null
          dirty: <bool>
        provider:
          name: '<ollama|bedrock>'
          large_model: '<id>'
          small_model: '<id>'
          # ollama only:
          models:
            '<name>':
              name: '<name>'
              digest: '<sha256:...>' | null
          server_version: '<x.y.z>' | null
          # bedrock only:
          note: '<invisible-updates caveat>'
        settings:
          large_model: '<id>'
          small_model: '<id>'
          base_url: '<url>' | null
          games: <int>
          seed: <int> | null
          max_days: <int> | null   # spec 023 — runaway Day cap (was max_rounds)
          scripted_player: 'active' | 'passive'  # spec 026 — human-seat stand-in (omitted on pre-026 records → passive)
          lineup:                  # spec 014 — the configured whole-table counts
            num_citizens: <int>
            num_mafia: <int>
        quality:
          games_attempted: <int>
          games_completed: <int>
          games_failed_early: <int>
          duration_seconds: <float|null>
        outcomes:
          games: <int>
          law_abiding: {wins: <int>, rate: <float>, ci_low: <float>, ci_high: <float>}
          mafia:       {wins: <int>, rate: <float>, ci_low: <float>, ci_high: <float>}
          scripted_side:       # spec 027 — the scripted stand-in's-OWN-side win rate (omitted when no side resolved / pre-027 records)
            side: '<law_abiding|mafia>'
            wins: <int>
            rate: <float>
            ci_low: <float>
            ci_high: <float>
          runaway: <int>       # spec 023 — in-game Day cap hit (NOT a win)
          draw: <int>          # bare count — legacy; no live path emits it
          no_winner: <int>     # winner=None (never resolved / backstop)
          note: '<passive-scripted-human caveat>'
        vote_activity:
          by_side: {law_abiding: <int>, mafia: <int>}   # ALWAYS both keys, zero included
          by_day:  {day_1: <int>, day_2: <int>, ...}     # sparse; {} when none
        metrics:
          repetition:
            rate: <float>
            count: <int>
            denominator: <int>
            ci_low: <float>   # Wilson 95% lower bound (every present metric)
            ci_high: <float>  # Wilson 95% upper bound
        notes: '<free text, or empty>'

    Absent provenance fields render as YAML ``null`` (an unreached git commit, a
    missing ollama digest, a bedrock run's empty server version) so they read as
    genuinely absent rather than as the empty string. ``notes`` is always
    emitted LAST — present even when empty (``notes: ''``) so the record visibly
    invites hand-editing. A note with no newline renders as a single
    safely-quoted scalar; a multi-line note renders as a YAML literal block
    scalar (``notes: |`` then indented lines). It is the one human-mutable
    field; the machine fields above stay immutable.
    """
    lines: list[str] = []

    lines.append("run:")
    run_block: dict[str, object] = {
        "date": run_date,
        "duration_seconds": result.duration_seconds,
        "metrics_version": METRICS_VERSION,
    }
    # ``transcript_dir`` (spec 017 §2.3) — the run-id directory NAME under
    # ``evals/transcripts/``, emitted ONLY when this run wrote transcripts. A new
    # additive field: an empty ``transcript_dir`` (a run that wrote none, or a
    # bare synthetic ``EvalResult``) omits the key entirely, so OLDER records are
    # defensively absent it — never backfilled, never a misleading empty link.
    if result.transcript_dir:
        run_block["transcript_dir"] = result.transcript_dir
    lines += _yaml_block(run_block, indent=1)

    # ``code`` — git provenance (commit / branch / dirty). ``commit`` / ``branch``
    # are ``null`` when git was unavailable; ``dirty`` is a bool. Defaults to the
    # all-degraded shape if the run never collected it, so the renderer stays
    # total over a bare ``EvalResult`` (the synthetic-record tests).
    code = result.code or {"commit": None, "branch": None, "dirty": False}
    lines.append("code:")
    lines += _yaml_block(
        {
            "commit": code.get("commit"),
            "branch": code.get("branch"),
            "dirty": bool(code.get("dirty")),
        },
        indent=1,
    )

    # ``provider`` — the enriched identification. When the run collected a
    # ``provider_block`` (the live path) we render it; otherwise we fall back to
    # the flat identity off the result so a bare synthetic ``EvalResult`` still
    # renders. The nested ``models`` map (ollama digests) goes through
    # ``_yaml_nested_map``; ``server_version`` / ``note`` are flat scalars.
    lines.append("provider:")
    block = result.provider_block or {
        "name": result.provider,
        "large_model": result.large_model,
        "small_model": result.small_model,
    }
    lines += _yaml_block(
        {
            "name": block.get("name", result.provider),
            "large_model": block.get("large_model", result.large_model),
            "small_model": block.get("small_model", result.small_model),
        },
        indent=1,
    )
    models = block.get("models")
    if isinstance(models, dict):
        lines.append("  models:")
        lines += _yaml_nested_map(models, indent=2)
    if "server_version" in block:
        lines += _yaml_block(
            {"server_version": block.get("server_version")}, indent=1
        )
    if "note" in block:
        lines += _yaml_block({"note": block.get("note")}, indent=1)

    # ``settings`` — the effective resolved values for a like-for-like rerun.
    # Falls back to a minimal shape (games from the quality counts) if absent.
    settings = result.settings or {
        "large_model": result.large_model,
        "small_model": result.small_model,
        "base_url": None,
        "games": result.games_attempted,
        "seed": None,
        "max_days": None,
    }
    lines.append("settings:")
    flat_settings: dict[str, object] = {
        "large_model": settings.get("large_model", result.large_model),
        "small_model": settings.get("small_model", result.small_model),
        "base_url": settings.get("base_url"),
        "games": settings.get("games", result.games_attempted),
        "seed": settings.get("seed"),
        # Spec 023: renamed ``max_rounds`` → ``max_days`` (the runaway Day
        # cap). Fall back to the legacy key so a synthetic/older settings map
        # still renders a value.
        "max_days": settings.get("max_days", settings.get("max_rounds")),
    }
    # Spec 026 §2.4: the human-seat stand-in mode (``active``/``passive``),
    # rendered after the flat keys and before the nested ``lineup``. ADDITIVE /
    # conditional — like ``lineup``, only emitted when the run recorded it, so a
    # synthetic/older settings map without the key renders without the line
    # (pre-026 records read as implicitly ``passive``). No ``METRICS_VERSION``
    # bump (the ``lineup``/``ci_low`` precedent).
    scripted_player = settings.get("scripted_player")
    if scripted_player is not None:
        flat_settings["scripted_player"] = scripted_player
    lines += _yaml_block(flat_settings, indent=1)
    # ``settings.lineup`` (spec 014 §2.4) — the configured whole-table counts,
    # rendered after the flat settings keys as a one-level nested sub-map (the
    # ``provider.models`` / ``outcomes`` per-block path). Only emitted when the
    # run recorded a lineup, so a bare synthetic ``EvalResult`` (no lineup) omits
    # it — pre-014 records simply lack the sub-map.
    lineup = settings.get("lineup")
    if isinstance(lineup, dict):
        lines.append("  lineup:")
        lines += _yaml_block(
            {
                "num_citizens": lineup.get("num_citizens"),
                "num_mafia": lineup.get("num_mafia"),
            },
            indent=2,
        )

    lines.append("quality:")
    lines += _yaml_block(
        {
            "games_attempted": result.games_attempted,
            "games_completed": result.games_completed,
            "games_failed_early": result.games_failed_early,
            "duration_seconds": result.duration_seconds,
        },
        indent=1,
    )

    # ``outcomes`` (spec-013 §2.1) — win-rate by side, after ``quality`` and
    # before ``metrics``. ``games`` then the two sides (each ``{wins, rate?,
    # ci_low?, ci_high?}`` — rate/CI omitted when ``games == 0``), then the bare
    # ``draw``/``no_winner`` counts, then the immutable caveat ``note``. Only
    # rendered when the run actually produced an outcomes block (a bare synthetic
    # ``EvalResult`` without it simply omits the section).
    if result.outcomes:
        lines.append("outcomes:")
        lines += _yaml_block({"games": result.outcomes.get("games", 0)}, indent=1)
        for side in _OUTCOME_SIDES:
            facets = result.outcomes.get(side)
            if not isinstance(facets, dict):
                continue
            lines.append(f"  {side}:")
            # Fixed sub-key order; rate/ci omitted on the games==0 path.
            ordered = {
                key: facets[key]
                for key in ("wins", "rate", "ci_low", "ci_high")
                if key in facets
            }
            lines += _yaml_block(ordered, indent=2)
        # Spec-027: the scripted stand-in's-side win rate — rendered AFTER the
        # two side rates and BEFORE the bare ``runaway``/``draw``/``no_winner``
        # counts (it is a side-shaped rate). CONDITIONAL/additive — only when the
        # run recorded it, so a synthetic/pre-027 ``EvalResult`` (no
        # ``scripted_side``) omits the key entirely (back-compat; no
        # ``METRICS_VERSION`` bump). Sub-key order ``side → wins → rate → ci_low
        # → ci_high``, each emitted only ``if key in facets`` so the
        # ``games == 0`` path (``{side, wins}``) drops rate/CI exactly like the
        # side rates.
        scripted_side = result.outcomes.get("scripted_side")
        if isinstance(scripted_side, dict):
            lines.append("  scripted_side:")
            ordered = {
                key: scripted_side[key]
                for key in ("side", "wins", "rate", "ci_low", "ci_high")
                if key in scripted_side
            }
            lines += _yaml_block(ordered, indent=2)
        lines += _yaml_block(
            {
                # Spec 023: ``runaway`` (the in-game Day-cap hit) is its own bare
                # count, rendered before ``draw``/``no_winner`` and visibly
                # distinct from a real win.
                "runaway": result.outcomes.get("runaway", 0),
                "draw": result.outcomes.get("draw", 0),
                "no_winner": result.outcomes.get("no_winner", 0),
            },
            indent=1,
        )
        lines += _yaml_block(
            {"note": result.outcomes.get("note", _OUTCOMES_HUMAN_CAVEAT)}, indent=1
        )

    # ``vote_activity`` (spec-013 §2.2) — AI vote-initiation counts by side ×
    # day. ``by_side`` ALWAYS emits both side keys with a visible integer (the
    # explicit-zero guarantee); ``by_day`` is sparse and renders the literal
    # ``{}`` (present-but-empty) when no day saw an initiation, with ``day_N``
    # keys sorted by integer suffix. Only rendered when the run produced the block.
    if result.vote_activity:
        lines.append("vote_activity:")
        by_side = result.vote_activity.get("by_side", {})
        lines.append("  by_side:")
        lines += _yaml_block(
            {side: int(by_side.get(side, 0)) for side in _OUTCOME_SIDES},
            indent=2,
        )
        by_day = result.vote_activity.get("by_day", {})
        lines += _yaml_int_map("by_day", dict(by_day), indent=1)

    # ``metrics`` is a map of metric-name → {rate, count, denominator}. Slice 1
    # carries only ``repetition``; Slice 2's detectors add sibling entries here
    # under the same nested shape, each rendered in this same fixed sub-key
    # order. Iterating ``result.metrics`` preserves insertion order, so the
    # metrics appear in the order the run computed them.
    lines.append("metrics:")
    for metric_name, facets in result.metrics.items():
        lines.append(f"  {metric_name}:")
        # Fixed sub-key order for clean diffs across runs and metrics. ``ci_low``
        # / ``ci_high`` are the Wilson 95% reliability band, rendered as floats
        # right after ``denominator`` whenever they were attached (every present
        # metric); a synthetic record without them simply omits the two lines.
        ordered = {
            key: facets[key]
            for key in ("rate", "count", "denominator", "ci_low", "ci_high")
            if key in facets
        }
        lines += _yaml_block(ordered, indent=2)

    # ``notes`` — always LAST, always present (the one human-mutable field). A
    # multi-line note is a YAML block scalar; everything else (incl. empty) is a
    # single quoted scalar, so an unset note renders as ``notes: ''`` — present
    # but empty, visibly inviting hand-editing.
    if "\n" in result.notes:
        lines += _yaml_block_scalar("notes", result.notes, indent=0)
    else:
        lines.append(f"notes: {_yaml_str(result.notes)}")

    return "\n".join(lines) + "\n"


def append_record(
    result: EvalResult,
    run_date: str,
    ledger_path: Path | None = None,
) -> Path:
    """Append one ``---``-separated record for ``result`` to the ledger; return its path.

    Thin I/O wrapper over the pure :func:`render_record`: writes a ``---``
    document-separator line, then the rendered document, in append mode — so
    records accumulate and history is never rewritten (functional-spec 011
    §2.3). Creates the ``evals/`` directory and the ledger file on first use.
    ``ledger_path`` is injectable so a temp file can be used; it defaults to
    ``None`` and is resolved to the module-global ``LEDGER_PATH`` *at call time*
    (NOT bound as a signature default), so a ``monkeypatch.setattr(LEDGER_PATH)``
    in tests reaches even the no-arg call inside :func:`run_eval` — the early-bound
    default that silently leaked synthetic records into the real ledger is gone.
    """
    if ledger_path is None:
        ledger_path = LEDGER_PATH
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    document = render_record(result, run_date)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write("---\n")
        fh.write(document)
    return ledger_path


# ===========================================================================
# Spec-017 transcript storage + one-command cleanup.
#
# Layout (tech-spec §2.3): each measured game's rendered transcript is written to
# ``evals/transcripts/<run-id>/game-NN.txt`` — ONE directory per run, with a
# zero-padded game index so a 10-game run sorts game-01 … game-10 lexically and
# the run/game relationship is obvious from the names alone. ``<run-id>`` is a
# filesystem-safe, sortable timestamp generated ONCE per ``run_eval``.
#
# The store is NOT gitignored: the rendered files are ordinary untracked files,
# curated by the developer commit-or-delete (functional-spec §2.3). The smoke
# runs are dropped with ``make clean-transcripts`` → :func:`clean_transcripts`,
# which removes only the untracked run dirs (a committed/tracked run is kept).
# ===========================================================================


def make_run_id(now: datetime | None = None) -> str:
    """A filesystem-safe, sortable ``<run-id>`` for one run's transcript dir.

    An ISO-ish local timestamp with the ``:`` separators (illegal in a path on
    Windows, awkward everywhere) swapped for ``-`` — e.g. ``2026-06-18T14-32-05``.
    Lexical sort order matches chronological order, and the form is safe on
    macOS / Linux. Generated ONCE per ``run_eval`` (real-run-only; tests inject a
    fixed id), so ``datetime.now()`` here is fine — this is the eval harness, not
    the determinism-sensitive graph code (architecture §6). ``now`` is injectable
    purely so a test can pin the timestamp.
    """
    moment = now if now is not None else datetime.now()
    return moment.strftime("%Y-%m-%dT%H-%M-%S")


def transcript_path(
    transcripts_root: Path, run_id: str, game_index: int, *, pad: int = 2
) -> Path:
    """The path for one game's transcript: ``<root>/<run-id>/game-NN.txt``.

    ``game_index`` is 1-based and zero-padded to ``pad`` digits (at least 2 —
    ``game-01.txt``); a run with more than 99 games keeps growing the width
    naturally (``game-100.txt``), so the ordering still reads correctly. Pure
    path arithmetic — no directory is created here; the writer makes the run dir.
    """
    name = f"game-{game_index:0{pad}d}.txt"
    return transcripts_root / run_id / name


def write_transcript(
    text: str,
    transcripts_root: Path,
    run_id: str,
    game_index: int,
    *,
    pad: int = 2,
) -> Path:
    """Write one game's rendered transcript and return the file path.

    Creates the per-run directory (``<root>/<run-id>/``) on first use and writes
    ``text`` to ``game-NN.txt`` (zero-padded ``game_index``). ``transcripts_root``
    is injectable so tests write into a ``tmp_path`` and never touch the real
    ``evals/transcripts/``. Returns the written path for the caller to log/track.
    """
    path = transcript_path(transcripts_root, run_id, game_index, pad=pad)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _git_tracks_anything_under(repo_root: Path, directory: Path) -> bool:
    """True iff git tracks ≥1 file under ``directory`` (``git ls-files`` non-empty).

    Asks git, relative to ``repo_root``, whether the directory holds any tracked
    path — the test :func:`clean_transcripts` uses to decide "committed (keep)"
    vs "untracked (drop)". Degrades to ``False`` (treat as untracked → eligible
    for removal) only when git is genuinely unavailable; a directory git tracks
    is never removed. Uses ``git ls-files -- <dir>``: empty stdout ⇒ nothing
    tracked there.
    """
    out = _git_output(repo_root, "ls-files", "--", str(directory))
    return bool(out)


def clean_transcripts(
    transcripts_root: Path = TRANSCRIPTS_ROOT,
    *,
    repo_root: Path = _REPO_ROOT,
) -> list[Path]:
    """Remove the UNTRACKED run dirs under ``transcripts_root``; keep committed ones.

    The one-command cleanup behind ``make clean-transcripts`` (functional-spec
    §2.3): drops the few-game smoke runs that were never committed, leaving the
    curated keepers (the runs a developer ``git add``-ed + committed) untouched.
    "Untracked" is decided by git (``git ls-files`` over each run dir via
    :func:`_git_tracks_anything_under`): a run dir with ANY tracked file is
    preserved; one with none is removed wholesale.

    SAFE BY CONSTRUCTION: only ever operates on direct child directories of
    ``transcripts_root`` (never files outside it, never the root itself). A
    missing ``transcripts_root`` is a no-op. Returns the list of removed run-dir
    paths so the caller / test can report what it dropped.

    ``transcripts_root`` and ``repo_root`` are arguments so the testing task runs
    it against a ``tmp_path`` — a tracked run is simulated by making git report it
    tracked — never against the real ``evals/transcripts/``.
    """
    import shutil

    removed: list[Path] = []
    if not transcripts_root.is_dir():
        return removed
    for run_dir in sorted(transcripts_root.iterdir()):
        if not run_dir.is_dir():
            continue
        if _git_tracks_anything_under(repo_root, run_dir):
            continue  # committed/tracked keeper — leave it
        shutil.rmtree(run_dir)
        removed.append(run_dir)
    return removed


@dataclass(slots=True)
class _GameCapture:
    """The per-game data ``run_eval`` scores — read from one final state.

    Slice-1 speech inputs (pooled across games):
    - ``ai_lines`` / ``ai_names`` — the AI-spoken Day lines (human excluded) +
      AI names for the spec-009 repetition measure.
    - ``ai_lines_with_speakers`` — per-line ``(speaker, text)`` pairs for the
      third-person self-talk measure.

    Slice-2 action inputs (scored PER GAME, then summed — names are only unique
    within a game, so the vote scorer must resolve against this game's own map):
    - ``players`` — the final ``players`` map (roles for the action detectors).
    - ``messages`` — the full message history (announce + per-ballot lines).

    Spec-013 outcome input:
    - ``winner`` — this game's ``state["winner"]`` (∈ ``{"law_abiding", "mafia",
      "runaway", "draw", None}``). Spec 023: a measured game now runs to its
      NATURAL conclusion, so a real side win is the norm; ``"runaway"`` is the
      in-game Day-cap hit (a stuck/looping game — flagged distinctly, NOT a
      win); ``"draw"`` is legacy (no live path emits it); ``None`` means the
      game ended without any winner set (e.g. the anti-hang backstop). Folded by
      ``run_eval`` into the ``outcomes`` block via :func:`tally_outcomes`.

    Spec-027 scripted-side input:
    - ``human_id`` — this game's ``state["human_id"]`` (the scripted seat's id).
      Threaded so ``run_eval`` can resolve the seat's per-game DEALT side via
      ``players[human_id].role`` (the underscore ``"law_abiding"`` / ``"mafia"``
      token, identical to the ``winner`` vocabulary) and tally the scripted
      stand-in's-side win rate (:func:`tally_outcomes`'s ``scripted_side`` entry).
      Defaulted to ``""`` so a hand-built ``_GameCapture`` in an offline test
      needs no extra wiring — an empty/unresolvable id simply yields no side for
      that game.

    Spec-017 transcript input:
    - ``events`` — the ORDERED per-super-step ``graph.stream(stream_mode=
      "updates")`` log this game emitted, each entry a ``{node: delta}`` dict
      captured **as it streamed** (via the ``on_update`` sink threaded into
      :func:`eval_dialogue._drive`). This — NOT the final ``state`` snapshot — is
      the transcript renderer's source of truth: the per-Night pointing channels
      (``night_round_picks`` / ``night_rounds_log``) are reset every Night in
      ``night_open``, so a final-state read holds only the *last* Night's picks,
      while this log preserves every Night's pointing (and every message, role,
      persona, vote, ballot, and kill) in strict chronological order. The
      Slice-1-Task-2 pure renderer consumes this list; the existing metrics
      scoring does NOT read it (it stays additive). The deltas are stored raw —
      the renderer, not the capture, decides what to surface — so nothing a
      transcript needs (ordering, a message's ``private_to`` tag) is pre-summarized
      away here.
    """

    ai_lines: list[str]
    ai_names: set[str]
    ai_lines_with_speakers: list[tuple[str, str]]
    players: dict[str, PlayerState]
    messages: list
    winner: str | None
    # Raw structured-output captures intercepted by the proxy this game (Slice 3,
    # Task 2): every ``with_structured_output(...).invoke(...)`` payload with its
    # speaker attributed. ``run_eval`` filters these for AI-day-speaker
    # ``DayAction(kind="vote")`` to compute ``self_vote.initiation`` — the one
    # blunder no post-game state can see (``_accept`` rejects the self-vote).
    captures: list[CaptureRecord]
    # Spec-017: the ordered per-super-step ``{node: delta}`` stream log (see the
    # class docstring). Defaults to an empty list so a ``_GameCapture`` built by
    # hand (an offline scorer test) needs no transcript wiring. ``_play_one_game``
    # populates it via the ``on_update`` sink it threads into every ``_drive``.
    events: list[dict[str, Any]] = field(default_factory=list)
    # Spec-027: this game's scripted-seat (human) id, read from the same final
    # state. ``run_eval`` resolves the per-game side via
    # ``players.get(human_id).role`` (defensive). Defaulted to ``""`` so a
    # hand-built capture needs no extra wiring; an empty/unresolvable id yields no
    # side for that game (excluded from the scripted-side numerator).
    human_id: str = ""


def _install_capture_provider(
    captures: list[CaptureRecord],
    speaker_resolver: Callable[[Any], str | None],
) -> None:
    """Point ``graphia.llm``'s seams at a CAPTURE proxy over the active provider.

    Installs through the documented in-process seams (``_active_provider`` /
    ``_large`` / ``_small``) — the same seams ``ollama_smoke`` and
    ``repetition_experiment`` use, identical for both providers because the seam
    sits ABOVE the provider branch (the ADR-009 dividend), so no production code
    changes and the provider is whichever ``load_config`` already resolved. The
    proxy CAPTURES (a ``captures`` list + the prompt-parse ``speaker_resolver``)
    rather than counts — the orthogonal mode ``instrument`` exposes. The inner
    clients are the active provider's real ones, so the games still hit the real
    model; the proxy only observes.
    """
    import graphia.llm as llm_mod

    provider = llm_mod._resolve_provider()
    llm_mod._large = InstrumentedModel(
        provider.large(), captures=captures, speaker_resolver=speaker_resolver
    )
    llm_mod._small = InstrumentedModel(
        provider.small(), captures=captures, speaker_resolver=speaker_resolver
    )


# ===========================================================================
# Active scripted player seat (spec 026): the human-seat stand-in's three
# resume values, computed from the public game so far (+ a Mafioso's known
# teammates) with no LLM call and no RNG. The seat is constructed ONCE per game
# after the deal (``_make_scripted_seat``), and the per-interrupt resume value
# is a pure function of the live public state (``_scripted_resume``). When
# PASSIVE, the resume helper is bypassed entirely and the driver keeps the
# byte-for-byte prior defaults — the ADR-011 flag-off parity guarantee.
# ===========================================================================

# "Final discussion round of the Day" = the speaking turn during round
# ``DAY_MAX_ROUNDS`` (resolved D2). ``day_rounds`` counts COMPLETED rounds and is
# bumped only at a round wrap, so during round N's speaking turns
# ``day_rounds == N - 1``; the final round (``DAY_MAX_ROUNDS``) is therefore the
# turn taken while ``day_rounds == DAY_MAX_ROUNDS - 1``. Imported lazily in the
# seat builder (``nodes.day`` pulls in the gameplay stack) to keep this module's
# import side-effect-free.


@dataclass(slots=True)
class _ScriptedSeat:
    """The constructed scripted-player seat for one game (spec 026).

    Built once after the deal from the human seat's OWN dealt role and (if Mafia)
    its OWN teammates — the only place true roles are read, and only the seat's
    legitimate self-knowledge. Holds nothing that re-enters the running graph;
    every resume value is recomputed from the live public state per interrupt.

    ``role`` is the seat's dealt side (``"mafia"`` / ``"law_abiding"``);
    ``teammate_ids`` is the other living-or-dead ``role=="mafia"`` ids (empty for
    a Law-abiding seat); ``self_id`` is the human id; ``day_max_rounds`` is the
    final-round threshold (``DAY_MAX_ROUNDS``), captured at build time so the
    pure resume helper needs no further imports.
    """

    self_id: str
    role: str
    teammate_ids: set[str]
    day_max_rounds: int


def _make_scripted_seat(state: dict[str, Any]) -> _ScriptedSeat:
    """Construct the per-game scripted seat from the post-deal state (spec 026).

    Reads the human seat's OWN dealt role and — only for a Mafioso — its OWN
    teammate ids (the other ``role=="mafia"`` players). This is the SINGLE place
    the policy is allowed to read true roles, and only the seat's own legitimate
    self-knowledge (its role + its team), never another living player's side.

    ``DAY_MAX_ROUNDS`` is read from ``graphia.nodes.day`` here (a local import so
    the module stays free of the gameplay stack at import time) and captured on
    the seat, so the pure resume helper can decide "final round" without any
    further import.
    """
    from graphia.nodes.day import DAY_MAX_ROUNDS

    players = state.get("players", {})
    human_id = state.get("human_id", "")
    human = players.get(human_id)
    role = human.role if human is not None else "law_abiding"
    teammate_ids: set[str] = set()
    if role == "mafia":
        teammate_ids = {
            p.id
            for p in players.values()
            if p.role == "mafia" and p.id != human_id
        }
    return _ScriptedSeat(
        self_id=human_id,
        role=role,
        teammate_ids=teammate_ids,
        day_max_rounds=DAY_MAX_ROUNDS,
    )


def _decision_to_resume(decision: Decision) -> str:
    """Map a scripted :class:`Decision` to the human-seat resume string (spec 026).

    Mirrors the existing human-seat resume protocol exactly (VERIFIED finding,
    tech-spec §3):

    - a ``speak`` decision resumes with its text (the human speech path);
    - a ``vote`` (day_turn vote-initiation) resumes with ``f"/vote {name}"`` —
      the human ``/vote`` slash-command branch fuzzy-matches the display NAME, so
      the decision carries the target's name, not its id;
    - a ``ballot`` decision resumes ``"yes"``/``"no"``;
    - a ``point`` decision resumes the chosen target's id directly.
    """
    match decision.action:
        case "speak":
            return decision.text or "(stays silent.)"
        case "vote":
            return f"/vote {decision.target_name}"
        case "ballot":
            return "yes" if decision.yes else "no"
        case "point":
            return decision.target_id or ""
    return ""  # unreachable; defensive


def _scripted_resume(
    seat: _ScriptedSeat, interrupt_value: dict[str, Any], state: dict[str, Any]
) -> str:
    """The active scripted seat's resume value for one interrupt (spec 026).

    Pure over ``(seat, interrupt_value, state)`` — reconstructs the public view
    from the live ``messages`` + ``players``, scores suspicion, and dispatches to
    the role-matched policy (``law_abiding_decision`` for a Law-abiding seat,
    ``mafia_decision`` for a Mafioso). No LLM, no RNG, no live ``get_state`` from
    inside a node — so it is unit-testable offline and adds zero token cost.

    "Final round" is read from the public ``day_rounds`` (= completed rounds): the
    final discussion round (``DAY_MAX_ROUNDS``) is the speaking turn taken while
    ``day_rounds == DAY_MAX_ROUNDS - 1`` (D2). The open ballot's target id (for a
    ``vote`` interrupt) comes from the interrupt payload's ``target_id``.
    """
    players = state.get("players", {})
    messages = list(state.get("messages", []))
    kind = interrupt_value.get("kind")

    view = reconstruct_public_view(messages, players, seat.self_id)
    scores = score_suspicion(view, players, seat.self_id)
    last_round = state.get("day_rounds", 0) >= seat.day_max_rounds - 1
    open_vote_target = interrupt_value.get("target_id")

    if seat.role == "mafia":
        decision = mafia_decision(
            view,
            scores,
            players,
            seat.self_id,
            seat.teammate_ids,
            kind=cast(Any, kind),
            last_round=last_round,
            open_vote_target=open_vote_target,
        )
    else:
        decision = law_abiding_decision(
            view,
            scores,
            players,
            seat.self_id,
            kind=cast(Any, kind),
            last_round=last_round,
            open_vote_target=open_vote_target,
        )
    return _decision_to_resume(decision)


def _play_one_game(args: argparse.Namespace, game_index: int) -> _GameCapture:
    """Drive one unattended scripted game on an isolated checkpoint; return its
    :class:`_GameCapture` — the speech inputs (pooled lines + AI names + per-line
    speaker pairs) for the Slice-1 repetition / third-person measures, the final
    ``players`` map and full message history for the Slice-2 action detectors,
    PLUS the raw proxy captures for the Slice-3 ``self_vote.initiation`` metric,
    all read from the one game. Raises on any failure — the caller in
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

    # Spec 023: a measured game now runs to its NATURAL conclusion — a real
    # win/loss, or (only for a stuck/looping game) the in-game runaway Day cap
    # routing to ``end_screen``. The old fixed Day-speaking-turn cut that ended
    # games mid-Day as "no winner" is gone. ``--max-days`` overrides the cap for
    # this run (set via ``GRAPHIA_MAX_DAYS`` so ``load_config`` picks it up);
    # the loop below only watches ``snapshot.next``.
    if args.max_days is not None:
        os.environ["GRAPHIA_MAX_DAYS"] = str(args.max_days)

    with tempfile.TemporaryDirectory(prefix=f"graphia-blunder-{game_index}-") as ckpt:
        os.environ["GRAPHIA_CHECKPOINT_DIR"] = ckpt
        config = load_config()
        max_days = config.max_days
        graph, thread_id = build_graph(config)
        run_config = make_run_config(thread_id)

        # Spec-017 transcript capture: an ordered per-super-step event log this
        # game accumulates AS IT STREAMS. ``_drive`` is told to push every
        # ``stream_mode="updates"`` payload here via ``on_update`` — so the per-
        # Night pointing (``night_round_picks`` / ``night_rounds_log``, reset
        # each Night in ``night_open``) is recorded before the reset, which the
        # final ``get_state`` read below would have lost for all but the last
        # Night. The deltas are appended raw, in stream order, for the renderer.
        events: list[dict[str, Any]] = []

        def _capture(update: dict) -> None:
            events.append(update)

        # Stream to the name interrupt, then resume with the scripted name.
        _drive(graph, run_config, {"messages": []}, on_update=_capture)
        first = _collect_interrupt(graph, run_config)
        if not first or first.get("kind") != "name":
            raise RuntimeError(f"no name interrupt: {first!r}")
        _drive(graph, run_config, Command(resume=HUMAN_NAME), on_update=_capture)

        # Roles are now dealt — read this game's ``players`` ONCE, at a quiescent
        # point BETWEEN super-steps (the same safe ``get_state`` the driver loop
        # below already uses), and install the capture proxy. The resolver
        # parses the speaker off each Day-speak invoke's PROMPT and maps the name
        # to an id via this map — no live ``get_state`` from inside a running
        # node, so attribution cannot go stale (the ``test_slice7_vote`` trap).
        # All Day-speak invokes happen after this point, so the map is ready.
        post_deal_state = graph.get_state(run_config).values
        players_now = post_deal_state.get("players", {})
        captures: list[CaptureRecord] = []
        _install_capture_provider(
            captures, make_day_speaker_resolver(players_now)
        )

        # Spec 026: construct the active scripted-player seat ONCE, here, from the
        # post-deal state — the only place its OWN dealt role and (if Mafia) its
        # OWN teammates are read. ``active`` selects the deterministic rule-based
        # stand-in; ``passive`` keeps the byte-for-byte prior defaults below. The
        # seat NEVER routes through the capture proxy above (the policy makes no
        # model call), so the scripted seat adds zero invokes.
        scripted_active = getattr(config, "scripted_player_active", True)
        seat = _make_scripted_seat(post_deal_state) if scripted_active else None

        line_idx = 0
        # Spec 023: answer interrupts until the game ends NATURALLY (no
        # ``.next`` — a real win/loss, or the in-game runaway Day cap routing to
        # ``end_screen``). The old ``if rounds >= max_rounds: break`` mid-Day cut
        # is gone; the only stop is ``snapshot.next`` emptying. The ``range`` is
        # purely an anti-hang backstop sized off the Day cap: each Day costs at
        # most a few dozen super-steps (Night pointing rounds + the Day's
        # speaking/vote sub-graph at the largest table), so ``max_days * 60 + 40``
        # comfortably exceeds the longest natural game while still bounding a
        # genuinely stuck graph.
        for _ in range(max_days * 60 + 40):  # anti-hang backstop, Day-cap-derived
            snapshot = graph.get_state(run_config)
            if not snapshot.next:
                break  # reached end_screen / END
            iv = _collect_interrupt(graph, run_config)
            if iv is None:
                _drive(graph, run_config, None, on_update=_capture)
                continue
            kind = iv.get("kind")
            # Spec 026: in ACTIVE mode the role-matched policy supplies all three
            # resume values from the live public state (``snapshot.values``,
            # already read this iteration — no extra ``get_state``). In PASSIVE
            # mode the seat is ``None`` and the byte-for-byte prior defaults run
            # (neutral ``HUMAN_LINES`` speech, ``"no"`` ballot, ``options[0]``
            # point) — the ADR-011 flag-off parity baseline.
            if kind == "day_turn":
                if seat is not None:
                    resume: str = _scripted_resume(seat, iv, snapshot.values)
                else:
                    resume = HUMAN_LINES[line_idx % len(HUMAN_LINES)]
                    line_idx += 1
            elif kind == "vote":
                if seat is not None:
                    resume = _scripted_resume(seat, iv, snapshot.values)
                else:
                    resume = "no"  # never execute, so games run long enough to sample
            elif kind == "point":
                # Only reached when the seat is dealt Mafia. Active → the policy's
                # chosen non-teammate target id; passive → the first option.
                options = iv.get("options") or []
                if seat is not None:
                    resume = _scripted_resume(seat, iv, snapshot.values)
                else:
                    resume = options[0]["id"] if options else ""
            else:
                raise RuntimeError(f"unexpected interrupt {kind!r}")
            _drive(graph, run_config, Command(resume=resume), on_update=_capture)

        state = graph.get_state(run_config).values
        lines, names = _ai_lines_with_names(state)
        return _GameCapture(
            ai_lines=lines,
            ai_names=names,
            ai_lines_with_speakers=_ai_lines_with_speakers(state),
            players=state.get("players", {}),
            messages=list(state.get("messages", [])),
            captures=captures,
            winner=state.get("winner"),
            events=events,
            # Spec-027: the scripted seat's id, read from the same final state, so
            # ``run_eval`` can resolve this game's dealt seat side and tally the
            # scripted-side win rate.
            human_id=state.get("human_id", ""),
        )


def _seat_side(cap: _GameCapture) -> str | None:
    """This game's scripted seat side — ``players[human_id].role``, or ``None`` (spec 027).

    Resolves the dealt side of the scripted stand-in (the human seat) from the
    game's final ``players`` map via ``cap.human_id``. The ``role`` token is the
    underscore form (``"law_abiding"`` / ``"mafia"``), identical to the ``winner``
    vocabulary ``tally_outcomes`` matches, so no remapping is needed. Defensive:
    a missing/empty ``human_id``, a seat absent from the map, or a ``None`` role
    all resolve to ``None`` — that game contributes to the denominator (via
    ``winners``) but never to the scripted-side numerator.
    """
    seat = cap.players.get(cap.human_id) if cap.human_id else None
    role = getattr(seat, "role", None)
    return role if isinstance(role, str) and role else None


def run_eval(
    config: object,
    args: argparse.Namespace,
    *,
    transcripts_root: Path | None = None,
    run_id: str | None = None,
) -> EvalResult:
    """Play the games and score them — the harness's substance.

    The provider is already forced, the cloud stores are isolated, the ollama
    preflight has passed, and ``config`` is the resolved ``GraphiaConfig``. This
    function owns the per-game loop and scoring; it returns the populated
    ``EvalResult`` for the ledger task to persist.

    Plays ``args.games`` unattended scripted games against the real provider,
    accumulates each finished game's AI-spoken lines, and computes the one
    ``repetition`` metric (Slice 1) via the imported spec-009 measure. A game
    that raises mid-run counts as failed-early (logged to stderr, run continues).

    Run-provenance (Slice 4, functional-spec 011 §2.3) is collected ONCE here,
    before any game starts, so the record is attributable to a code version and
    a model fingerprint: the git ``code`` block (with the up-front dirty
    warning), the enriched ``provider`` block (ollama digests + server version,
    or the bedrock full-ids + invisible-updates note), the effective
    ``settings``, and — measured around the game loop — the wall-clock duration.

    Spec-017 transcripts: a ``<run-id>`` is generated ONCE (a sortable, fs-safe
    timestamp; both ``transcripts_root`` and ``run_id`` are injectable so tests
    write into a ``tmp_path`` with a pinned id and NEVER touch the real
    ``evals/transcripts/``). Each completed game's ordered event log is rendered
    (:func:`~graphia.tools.eval_transcript.render_transcript`) and written to
    ``<transcripts_root>/<run-id>/game-NN.txt``; the run-id is then recorded on
    the result as ``run.transcript_dir`` so the viewer can locate the run's
    transcripts from the ledger.
    """
    large_model, small_model = _resolved_model_names(config)

    # Spec-017: resolve the transcript store + the once-per-run id. The base dir
    # defaults to the repo's ``evals/transcripts/`` but is injectable; the run-id
    # is a fresh fs-safe timestamp unless a test pins one. Generated here, before
    # the loop, so every game in the run shares one directory.
    transcripts_base = (
        transcripts_root if transcripts_root is not None else TRANSCRIPTS_ROOT
    )
    transcript_run_id = run_id if run_id is not None else make_run_id()
    # ``run_meta`` feeds the per-transcript header (provider + resolved models +
    # game count); read defensively by the renderer so a thin mapping is fine.
    run_meta = {
        "provider": args.provider,
        "large_model": large_model,
        "small_model": small_model,
        "games": args.games,
    }
    # Zero-pad the game index to the width of the run's game count (≥ 2), so a
    # 10-game run writes game-01 … game-10 and a 100-game run game-001 … game-100,
    # both sorting lexically in chronological order.
    pad = max(2, len(str(max(args.games, 1))))
    # Set only if ≥1 transcript is actually written, so a run that wrote none
    # (all games failed early) leaves ``transcript_dir`` empty and the record
    # omits the link — never a dangling reference to an empty dir.
    wrote_any_transcript = False

    # --- Run-provenance, collected once before games start (functional-spec
    # §2.3, tech §2.4). All collectors degrade gracefully (null on failure), so
    # an unavailable source never fails the run.
    code = collect_code_provenance(_REPO_ROOT)
    warn_if_dirty(code)  # up-front stderr warning when the tree is dirty
    base_url = config.ollama_base_url  # load_config always sets this
    provider_block = collect_provider_provenance(
        args.provider, large_model, small_model, base_url
    )
    settings = {
        "large_model": large_model,
        "small_model": small_model,
        # base_url is only meaningful for ollama; recorded null for bedrock.
        "base_url": base_url if args.provider == "ollama" else None,
        "games": args.games,
        "seed": args.seed,
        # Spec 023: the recorded game-length control is now the runaway Day cap
        # (``max_days``, default 12), replacing the old per-game Day-speaking-turn
        # cut (``max_rounds``). ``config.max_days`` reflects any ``--max-days`` /
        # ``GRAPHIA_MAX_DAYS`` override for a like-for-like rerun. ``getattr``
        # (like the lineup below) so a minimal stub config without the attr is
        # tolerated.
        "max_days": getattr(config, "max_days", None),
        # The configured lineup (spec 014 §2.4), read off the resolved config so a
        # custom ``--citizens``/``--mafia`` (or a ``.env`` override) is recorded
        # for a like-for-like rerun. Nested sub-map rendered after the flat keys.
        "lineup": {
            "num_citizens": getattr(config, "num_citizens", None),
            "num_mafia": getattr(config, "num_mafia", None),
        },
        # The human-seat stand-in mode (spec 026 §2.4) as a readable
        # ``active``/``passive`` label, so records self-describe across the
        # deliberate baseline shift (the default flips to ``active``; every
        # committed pre-026 baseline is implicitly ``passive``). Additive /
        # back-compatible — like ``lineup``, a synthetic/older settings map
        # without the key renders without it; no ``METRICS_VERSION`` bump.
        "scripted_player": (
            "active"
            if getattr(config, "scripted_player_active", True)
            else "passive"
        ),
    }

    result = EvalResult(
        provider=args.provider,
        large_model=large_model,
        small_model=small_model,
        code=code,
        provider_block=provider_block,
        settings=settings,
        # ``--note`` (or "" when unset) — the run annotation, rendered last in
        # the ledger record; a maintainer can extend it by hand afterwards.
        notes=args.note,
    )

    # Wall-clock start; the duration is stamped onto the result just before the
    # record is appended (monotonic delta — immune to wall-clock adjustments).
    started = time.monotonic()

    # Accumulate AI lines across games, plus the union of AI player names so the
    # spec-009 name-masking still fires across the pooled set (a name dealt in
    # one game masks that name everywhere it appears in the pool). The per-line
    # ``(speaker, text)`` pairs accumulate alongside for the third-person measure
    # — each pair self-contained (a line is scored against its own speaker), so
    # pooling across games is sound without any cross-game name-resolution.
    pooled_lines: list[str] = []
    pooled_names: set[str] = set()
    pooled_speaker_lines: list[tuple[str, str]] = []

    # Spec-013: the per-completed-game winners (folded into ``outcomes`` once) and
    # the vote-activity marginals summed across games (``by_side`` both-keys, and
    # the sparse per-game-day ``by_day`` — day_1 across all games, etc.).
    winners: list[str | None] = []
    # Spec-027: the parallel per-completed-game scripted seat sides (the dealt
    # ``players[human_id].role``, or ``None`` when unresolvable), index-aligned to
    # ``winners`` so ``tally_outcomes`` can score the scripted-side rate per game.
    scripted_sides: list[str | None] = []
    vote_by_side: dict[str, int] = {side: 0 for side in _OUTCOME_SIDES}
    vote_by_day: dict[str, int] = {}

    # Action-metric numerators/denominators summed ACROSS games. Each game is
    # scored against its OWN ``players`` map (names are unique only within a
    # game; the same name could be dealt a different role next game), then its
    # raw count/denominator are added in — so the batch rate is
    # total_num/total_den, never a mean-of-rates (Slice 2, Task 2 aggregation).
    # ``self_vote.initiation`` is summed the same way but sourced from the PROXY
    # captures (Slice 3), not the message history — the metric no game state can
    # see. The full canonical {self,peer}x{initiation,yes} family now exists.
    action_totals: dict[str, dict[str, int]] = {
        metric: {"count": 0, "denominator": 0}
        for metric in (
            "self_vote.initiation",
            "self_vote.yes",
            "peer_vote.initiation",
            "peer_vote.yes",
        )
    }

    # Spec-031 persona-distinctiveness: the near-duplicate-pair count/denominator
    # summed ACROSS games via the SAME action-metric pattern — score per game over
    # its own roster (``cap.players``; ``C(n,2)`` pairs), add the raw count/
    # denominator in, then ``_facets(total_count, total_denominator)`` below so the
    # batch rate is total_num/total_den (never a mean-of-rates). A new orthogonal
    # metric — additive, so ``METRICS_VERSION`` is NOT bumped (the ``outcomes`` /
    # ``vote_activity`` precedent).
    persona_total: dict[str, int] = {"count": 0, "denominator": 0}

    for game_index in range(args.games):
        result.games_attempted += 1
        try:
            cap = _play_one_game(args, game_index)
        except Exception as exc:  # noqa: BLE001 - record and continue the batch
            result.games_failed_early += 1
            print(
                f"  game {game_index}: FAILED ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            continue
        result.games_completed += 1

        # Spec-017: render this completed game's ordered event log (NOT a final-
        # state snapshot — that loses every Night's pointing but the last) into a
        # tagged, human-readable transcript and write it to
        # ``<run-id>/game-NN.txt`` (1-based, zero-padded). Best-effort: a render/
        # write hiccup must not fail the measured run, so it is logged and the
        # game still counts as completed and is scored as normal.
        try:
            text = render_transcript(
                cap.events,
                cap.players,
                game_index=game_index + 1,
                run_meta=run_meta,
            )
            write_transcript(
                text, transcripts_base, transcript_run_id, game_index + 1, pad=pad
            )
            wrote_any_transcript = True
        except Exception as exc:  # noqa: BLE001 - never fail the run on a transcript
            print(
                f"  game {game_index}: transcript write FAILED "
                f"({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )

        pooled_lines.extend(cap.ai_lines)
        pooled_names.update(cap.ai_names)
        pooled_speaker_lines.extend(cap.ai_lines_with_speakers)

        # Spec-013: record this game's winner and fold its vote-activity
        # marginals into the batch totals (``by_side`` summed per side, ``by_day``
        # summed per per-game day number — day_1 of this game adds to day_1 of the
        # batch). The block is scored against this game's own ``players`` because
        # names are unique only within a game, exactly like the vote blunders.
        winners.append(cap.winner)
        # Spec-027: resolve this game's scripted seat side (the dealt role of the
        # human seat) and append it index-aligned to ``winners``, so the
        # scripted-side win rate is scored per game. Defensive: a missing/None
        # seat yields ``None`` (excluded from the scripted-side numerator).
        scripted_sides.append(_seat_side(cap))
        activity = score_vote_activity(cap.messages, cap.players)
        for side, n in activity["by_side"].items():
            vote_by_side[side] = vote_by_side.get(side, 0) + n
        for day_key, n in activity["by_day"].items():
            vote_by_day[day_key] = vote_by_day.get(day_key, 0) + n

        # Score this game's vote blunders against its own roster, then fold the
        # raw count/denominator of each metric into the batch totals.
        per_game = score_vote_blunders(cap.messages, cap.players)
        # ``self_vote.initiation`` comes from the proxy captures, not the
        # message history — a self-vote is rejected by ``_accept`` before it can
        # reach ``cap.messages``, so this is the only place it is countable. Its
        # speaker id was attributed at invoke time by the prompt-parse resolver.
        per_game["self_vote.initiation"] = score_self_vote_initiation(cap.captures)
        for metric, facets in per_game.items():
            action_totals[metric]["count"] += int(facets["count"])
            action_totals[metric]["denominator"] += int(facets["denominator"])

        # Spec-031: score this game's persona near-duplication over its OWN roster
        # (names are unique only within a game), then fold the raw count/
        # denominator into the batch total — the same per-game-then-sum pattern as
        # the action metrics above.
        persona_facets = score_persona_near_dup(cap.players)
        persona_total["count"] += int(persona_facets["count"])
        persona_total["denominator"] += int(persona_facets["denominator"])

    # Spec-013 game-dynamics blocks, folded over the completed games. ``outcomes``
    # partitions the winners (``games`` = completed games denominator); the side
    # win-rates carry a Wilson CI, ``draw``/``no_winner`` are bare counts.
    # ``vote_activity`` carries the explicit-zero ``by_side`` (both keys always)
    # and the sparse ``by_day`` — already summed across games above.
    # Spec-027: pass the parallel per-game scripted seat sides so ``outcomes``
    # also carries the scripted stand-in's-side win rate (omitted when no game
    # resolved a side — the absent-metric posture).
    result.outcomes = tally_outcomes(winners, scripted_sides)
    result.vote_activity = {"by_side": vote_by_side, "by_day": vote_by_day}

    result.ai_speeches = pooled_lines
    # Both speech metrics share the AI-spoken-line denominator; they are computed
    # together so one run records the full speech family (functional-spec §2.1).
    result.metrics["repetition"] = score_repetition(pooled_lines, pooled_names)
    result.metrics["third_person_self_talk"] = score_third_person_self_talk(
        pooled_speaker_lines
    )

    # The four vote action metrics, in the canonical {self,peer}x{initiation,yes}
    # family order. Each enters the record only when the batch offered at least
    # one opportunity (total denominator > 0); a no-opportunity metric is OMITTED
    # — reported absent, not as a misleading 0.0 (functional-spec §2.1; see
    # ``score_vote_blunders`` / ``score_self_vote_initiation`` / ``_facets``).
    # ``self_vote.initiation`` (the proxy-only one) completes the family in
    # Slice 3.
    for metric in (
        "self_vote.initiation",
        "self_vote.yes",
        "peer_vote.initiation",
        "peer_vote.yes",
    ):
        totals = action_totals[metric]
        if totals["denominator"] > 0:
            result.metrics[metric] = _facets(totals["count"], totals["denominator"])

    # Spec-031 persona-distinctiveness: build the batch ``persona_near_dup`` metric
    # from the summed pair counts via ``_facets``, recorded only when the batch
    # offered at least one persona pair (total denominator > 0) — a roster that
    # never had ≥2 AI personas is reported absent, not as a misleading 0.0 (the
    # same opportunity-based omission as the action metrics). ``_attach_ci`` below
    # then adds the Wilson band, like every other present metric.
    if persona_total["denominator"] > 0:
        result.metrics["persona_near_dup"] = _facets(
            persona_total["count"], persona_total["denominator"]
        )

    # Attach a Wilson 95% CI (ci_low/ci_high) to every PRESENT metric so each
    # rate carries its own reliability band — a wide band flags a small-n rate
    # (e.g. self_vote.yes 0.50 @ n=2) as noise, a tight one (repetition 0.45 @
    # n=108) as solid. Derived/supplementary: reads count/denominator only, so it
    # does not change detection and does not bump METRICS_VERSION. Absent metrics
    # were already omitted above, so none gets a CI.
    _attach_ci(result.metrics)

    # Spec-017: record the run's transcript dir NAME on the result (the viewer
    # derives the absolute path from the ledger's sibling ``transcripts/``), but
    # ONLY when ≥1 transcript was actually written — a run that wrote none leaves
    # it empty and ``render_record`` omits the ``run.transcript_dir`` key.
    if wrote_any_transcript:
        result.transcript_dir = transcript_run_id

    # Stamp the wall-clock duration (monotonic delta) onto the run/quality
    # block before rendering, so a degenerate (e.g. all-failed) run cannot
    # masquerade as a clean baseline (functional-spec §2.3).
    result.duration_seconds = round(time.monotonic() - started, 3)

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
        "--max-days",
        type=int,
        default=None,
        help=(
            "runaway Day cap for this run — overrides GRAPHIA_MAX_DAYS (default "
            "12). A safeguard only: a measured game runs to its natural win/loss "
            "and reaches this cap only if it's stuck/looping. Set lower to "
            "reproduce a shorter-game ablation."
        ),
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
    ap.add_argument(
        "--citizens",
        type=int,
        default=None,
        help=(
            "number of Citizens in the lineup (sets GRAPHIA_NUM_CITIZENS before "
            "config load; default 5). An invalid lineup is rejected by the same "
            "fail-fast config guard the game uses."
        ),
    )
    ap.add_argument(
        "--mafia",
        type=int,
        default=None,
        help=(
            "number of Mafiosos in the lineup (sets GRAPHIA_NUM_MAFIA before "
            "config load; default 2; must be strictly fewer than --citizens)"
        ),
    )
    ap.add_argument(
        "--scripted-player",
        choices=("active", "passive"),
        default=None,
        help=(
            "the human-seat stand-in (spec 026): 'active' (default) plays the "
            "deterministic rule-based policy that lets a correct town majority "
            "form; 'passive' reproduces the prior baseline (never proposes, "
            "always votes No). Overrides GRAPHIA_ACTIVE_SCRIPTED_PLAYER for this "
            "run; recorded as settings.scripted_player. Omit to use the default."
        ),
    )
    ap.add_argument(
        "--scripted-role",
        choices=("random", "law-abiding", "mafia"),
        default=None,
        help=(
            "the scripted seat's dealt role (spec 026 D3): 'random' leaves it to "
            "the game-default deal (GRAPHIA_ROLE unset) so both the Law-abiding and "
            "Mafioso policies fire within one batch (and the spec-027 scripted_side "
            "rate varies per game); 'law-abiding'/'mafia' pin it. Omit to keep the "
            "prior default (law-abiding unless GRAPHIA_ROLE is set in the env)."
        ),
    )
    ap.add_argument(
        "--note",
        type=str,
        default="",
        help=(
            "free-text annotation for this run (why it was made / what you observed); "
            "recorded as the ledger record's last key. The one human-mutable field — "
            "leave it off to hand-edit (incl. multi-line) into the YAML afterwards"
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
    # Route --citizens/--mafia onto the lineup env vars before load_config, so
    # the Slice-1 fail-fast guard validates them (a bad lineup exits there).
    _apply_lineup_overrides(args.citizens, args.mafia)
    # Spec 026: the human-seat stand-in mode. ``--scripted-player`` maps to the
    # default-on ``GRAPHIA_ACTIVE_SCRIPTED_PLAYER`` env flag (active ⇒ truthy,
    # passive ⇒ falsy) and overrides any inherited value for this run; omitted,
    # the env / the default-active flag wins. Set before ``load_config``.
    if args.scripted_player is not None:
        os.environ["GRAPHIA_ACTIVE_SCRIPTED_PLAYER"] = (
            "1" if args.scripted_player == "active" else "0"
        )
    # Spec 026 (D3): the seat's role is a per-run selectable value, DEFAULT
    # ``law-abiding`` — so the primary town-win measurement works out of the box,
    # while ``--scripted-role mafia`` exercises the Mafioso policy and
    # ``--scripted-role random`` leaves it to the game-default deal (both policies
    # within one batch). Set before ``load_config`` (config reads it at load time).
    _apply_scripted_role(args.scripted_role)

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
        + (
            f", runaway Day cap {args.max_days}"
            if args.max_days is not None
            else ", default 12-Day runaway cap"
        )
        + ". Real model; runs to natural end; non-deterministic dialogue.",
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
    # Spec-027 headline KPI: the scripted stand-in's-side win rate — the one
    # comparable number across an LA batch and a Mafia batch. Read defensively
    # (``.get``) so a run that resolved no seat side simply prints nothing.
    scripted_line = _scripted_side_summary(result.outcomes)
    if scripted_line:
        print(scripted_line)
    return 0


def _scripted_side_summary(outcomes: dict[str, object]) -> str:
    """The console scripted-side line for ``main()``'s summary, or ``""`` (spec 027).

    Reads ``outcomes["scripted_side"]`` defensively: an absent entry (no seat
    side resolved, or a pre-027 fold) yields the empty string so the caller's
    ``print`` adds nothing. When present, formats the headline KPI::

        scripted side (law_abiding): won 11/20 (rate=0.55, 95% CI [0.34–0.74])

    The rate/CI clause is dropped on the ``games == 0`` path (where the entry
    carries only ``side``/``wins``), mirroring the side-rate omission.
    """
    block = outcomes.get("scripted_side")
    if not isinstance(block, dict):
        return ""
    side = block.get("side", "?")
    wins = block.get("wins", 0)
    games = outcomes.get("games", 0)
    rate = block.get("rate")
    if rate is None:
        return f"scripted side ({side}): won {wins}/{games}"
    ci_low = block.get("ci_low")
    ci_high = block.get("ci_high")
    band = (
        f", 95% CI [{float(ci_low):.2f}–{float(ci_high):.2f}]"
        if ci_low is not None and ci_high is not None
        else ""
    )
    return (
        f"scripted side ({side}): won {wins}/{games} "
        f"(rate={float(rate):.2f}{band})"
    )


if __name__ == "__main__":
    sys.exit(main())
