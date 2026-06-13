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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal

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
    # max_rounds (functional-spec 011 §2.3).
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
# max_rounds) → ``quality`` (attempted/completed/failed_early, duration) →
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

    Fixed key order (Slice 4 run-provenance shape; functional-spec 011 §2.3,
    tech-spec §2.5) — ``run`` → ``code`` → ``provider`` → ``settings`` →
    ``quality`` → ``metrics`` → ``notes`` (``notes`` always LAST):

        run:
          date: '<iso date>'
          duration_seconds: <float|null>
          metrics_version: <int>
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
          max_rounds: <int> | null
        quality:
          games_attempted: <int>
          games_completed: <int>
          games_failed_early: <int>
          duration_seconds: <float|null>
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
    lines += _yaml_block(
        {
            "date": run_date,
            "duration_seconds": result.duration_seconds,
            "metrics_version": METRICS_VERSION,
        },
        indent=1,
    )

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
        "max_rounds": None,
    }
    lines.append("settings:")
    lines += _yaml_block(
        {
            "large_model": settings.get("large_model", result.large_model),
            "small_model": settings.get("small_model", result.small_model),
            "base_url": settings.get("base_url"),
            "games": settings.get("games", result.games_attempted),
            "seed": settings.get("seed"),
            "max_rounds": settings.get("max_rounds"),
        },
        indent=1,
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
    """

    ai_lines: list[str]
    ai_names: set[str]
    ai_lines_with_speakers: list[tuple[str, str]]
    players: dict[str, PlayerState]
    messages: list
    # Raw structured-output captures intercepted by the proxy this game (Slice 3,
    # Task 2): every ``with_structured_output(...).invoke(...)`` payload with its
    # speaker attributed. ``run_eval`` filters these for AI-day-speaker
    # ``DayAction(kind="vote")`` to compute ``self_vote.initiation`` — the one
    # blunder no post-game state can see (``_accept`` rejects the self-vote).
    captures: list[CaptureRecord]


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

        # Roles are now dealt — read this game's ``players`` ONCE, at a quiescent
        # point BETWEEN super-steps (the same safe ``get_state`` the driver loop
        # below already uses), and install the capture proxy. The resolver
        # parses the speaker off each Day-speak invoke's PROMPT and maps the name
        # to an id via this map — no live ``get_state`` from inside a running
        # node, so attribution cannot go stale (the ``test_slice7_vote`` trap).
        # All Day-speak invokes happen after this point, so the map is ready.
        players_now = graph.get_state(run_config).values.get("players", {})
        captures: list[CaptureRecord] = []
        _install_capture_provider(
            captures, make_day_speaker_resolver(players_now)
        )

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
        lines, names = _ai_lines_with_names(state)
        return _GameCapture(
            ai_lines=lines,
            ai_names=names,
            ai_lines_with_speakers=_ai_lines_with_speakers(state),
            players=state.get("players", {}),
            messages=list(state.get("messages", [])),
            captures=captures,
        )


def run_eval(config: object, args: argparse.Namespace) -> EvalResult:
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
    """
    large_model, small_model = _resolved_model_names(config)

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
        "max_rounds": args.max_rounds,
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
        pooled_lines.extend(cap.ai_lines)
        pooled_names.update(cap.ai_names)
        pooled_speaker_lines.extend(cap.ai_lines_with_speakers)

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

    # Attach a Wilson 95% CI (ci_low/ci_high) to every PRESENT metric so each
    # rate carries its own reliability band — a wide band flags a small-n rate
    # (e.g. self_vote.yes 0.50 @ n=2) as noise, a tight one (repetition 0.45 @
    # n=108) as solid. Derived/supplementary: reads count/denominator only, so it
    # does not change detection and does not bump METRICS_VERSION. Absent metrics
    # were already omitted above, so none gets a CI.
    _attach_ci(result.metrics)

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
