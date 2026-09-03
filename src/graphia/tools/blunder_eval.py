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
    make blunder-eval ARGS="--provider ollama --games 1 --diaries off --ledger-path /tmp/smoke.yaml --transcripts-root /tmp/smoke-transcripts"

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
from collections.abc import Sequence
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

# Spec-033 semantic persona similarity: the Bedrock embeddings factory, imported
# at module scope so it is a single patchable seam — the offline suite's autouse
# ``safe_llm`` fixture (tests/conftest.py) replaces
# ``graphia.tools.blunder_eval.get_embeddings`` with a deterministic fake, so the
# eval never reaches real Bedrock. ``run_eval`` calls it through the module-level
# binding (``get_embeddings``), not a local import, precisely so that patch lands.
from graphia.llm import get_embeddings
from graphia.prompts import (
    DAY_OPEN_NO_VICTIM_TEMPLATE,
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    DAY_SPEAK_USER_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
)
from graphia.state import DiaryRecord, PlayerState

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
#
# NOT a bump: purely ADDITIVE record-shape fields that change no detection rule
# and no denominator. The derived ``ci_low``/``ci_high`` band, the spec-013
# ``outcomes`` / ``vote_activity`` blocks, the conditional ``settings.lineup`` /
# ``settings.scripted_player`` keys, spec 036's conditional ``run.kind``
# record-kind label — and, spec 039, the conditional
# ``settings.private_diaries_enabled`` arm label — all leave every scorer
# untouched, so rates measured before and after stay directly comparable.
# Bumping for any of them would falsely flag every prior rate as incomparable.
# An ARM LABEL is the clearest case of the rule: no scorer reads ``settings``,
# ``tally_outcomes`` is untouched, and the field NARROWS the comparability
# contract (these rates were measured with diaries on/off) rather than
# invalidating it — which is the whole reason the field exists.
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
type Provider = Literal["ollama", "bedrock", "bedrock-claude"]
# Spec 035: ``bedrock-claude`` (Claude Haiku 4.5, ADR-012) joins the two
# original arms. Records stay comparable WITHIN a provider, so a third value
# is exactly what the ledger contract anticipates — never compare a
# bedrock-claude record against a bedrock one as if they were the same run.
PROVIDERS: tuple[Provider, ...] = ("ollama", "bedrock", "bedrock-claude")

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
    # --- Spec-036 generation-process block (functional-spec §2, tech §2 A/B) ---
    # ``generation`` — the persona-generation PROCESS counts of a bench run:
    # ``{"collisions": <int>, "regenerations": <int>}``. It is deliberately its
    # own small block rather than being folded into ``quality`` (which is run
    # HEALTH — how many units were attempted/completed) or into ``metrics``
    # (which is the versioned scored family): a collision count is neither. It
    # is the count that carried the spec-034 result — 2-in-10 rosters shipping a
    # near-duplicate → 0-in-10 — which a similarity MEAN alone would have lost,
    # so it belongs beside the persona facets and not inside them.
    # CONDITIONAL: empty ⇒ ``render_record`` omits the whole block, so a game
    # run (which generates no roster in isolation) renders byte-identically to a
    # pre-036 record. Additive/orthogonal, so NO ``METRICS_VERSION`` bump — the
    # ``outcomes``/``vote_activity``/``ci_low`` precedent.
    generation: dict[str, int] = field(default_factory=dict)
    # --- Spec-039 diary-fallback run health (§2.10 fold-in) ---
    # ``{"count": <placeholder entries>, "denominator": <entries attempted>}``
    # summed over the run's completed games, or EMPTY when no diary entry was
    # attempted (a diaries-off run; an on-arm run where no Day ever closed) — in
    # which case ``render_record`` omits the ``quality.diary_*`` keys entirely.
    # ABSENT, never a misleading zero. A run-HEALTH pair, not an AI-quality
    # metric, which is why it lives here and not in ``metrics``; see
    # :func:`score_diary_fallback`'s section banner for that argument and for
    # why it carries no Wilson band. Additive ⇒ NO ``METRICS_VERSION`` bump.
    diary_fallback: dict[str, int] = field(default_factory=dict)
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
    # --- Spec-036 record kind (functional-spec §2, tech-spec §2 Component A) ---
    # WHICH KIND of measurement this record describes, written into the record as
    # ``run.kind``. Empty ⇒ a played game — the only kind that existed before
    # spec 036 — and ``render_record`` then omits the key ENTIRELY, so every
    # committed record and every synthetic ``EvalResult`` in the tests renders
    # byte-identically and nothing is backfilled. ``run_eval`` leaves it empty;
    # only the persona-bench recording path sets it (``'persona-bench'``).
    # ``run.kind`` also defines the UNIT of the ``quality`` counts — games for a
    # game run, rosters for a bench run. Additive/conditional, so NO
    # ``METRICS_VERSION`` bump (the ``transcript_dir``/``lineup`` precedent).
    kind: str = ""


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


# ===========================================================================
# Spec 039 §2.10 — the diaries ARM LABEL.
#
# ``settings.private_diaries_enabled`` is what makes a diaries-on / diaries-off
# pair of records readable AS A PAIR; without it the two are indistinguishable
# columns of numbers. It is read off the RESOLVED config — the same values every
# game will build its graph from — through a ``None`` SENTINEL, deliberately NOT
# the permissive ``getattr(config, ..., True)`` shape ``settings.scripted_player``
# uses one field over: a config that cannot answer would then record the ON arm
# for an OFF run, and unlike a ``null`` a ``true`` is indistinguishable from a
# real measurement.
#
# This is NOT a graceful-degradation violation. That rule protects *instruments*
# — an unavailable embeddings client omits its metric and the run continues.
# This is the label saying WHICH ARM THE RUN IS: a record without it is
# worthless, and a wrongly-labelled one corrupts the comparison it exists to
# serve. So ``main`` refuses the run outright, before game 1.
# ===========================================================================


def _diaries_arm(config: object) -> bool | None:
    """The run's private-diaries arm as the record will state it, or ``None``.

    ``None`` means *this config cannot answer* — the field is absent (a thin
    stub object, or a rename this harness has not followed). Callers must treat
    that as "unlabelled"; never as the on arm.
    """
    arm = getattr(config, "private_diaries_enabled", None)
    return None if arm is None else bool(arm)


def _require_diaries_arm(config: object) -> bool:
    """The arm, or abort the run — the fail-fast half of the sentinel.

    Called by ``main`` immediately after ``load_config()``, so an unanswerable
    config costs nothing: the run stops *before* the provider preflight and long
    before game 1, rather than after half an hour of Bedrock tokens has bought a
    record nobody can attribute to an arm. The ledger is append-only and
    repo-committed — a mislabelled record cannot be rewritten, so "no record"
    beats "a record whose arm is a guess".

    Raises:
        SystemExit: when the resolved config reports no
            ``private_diaries_enabled`` field.
    """
    arm = _diaries_arm(config)
    if arm is None:
        raise SystemExit(
            "blunder-eval: the resolved config does not report "
            "private_diaries_enabled, so this run could not say which arm of "
            "the diaries A/B it measured — refusing to play. An unlabelled "
            "record is worthless and a wrongly-labelled one corrupts the "
            "comparison. Check that GraphiaConfig.private_diaries_enabled "
            "(spec 039) still exists and that load_config() returns it."
        )
    return arm


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

    Reads them off the resolved ``GraphiaConfig``: ``ollama_*_model`` on the
    ollama path, ``large_model`` / ``small_model`` on the Bedrock paths
    (``bedrock`` Nova or, spec 035, ``bedrock-claude`` Claude Haiku) — both now
    env-overridable and resolved on the config. Falls back to ``graphia.llm``'s
    Nova default constants if a config without the spec-035 fields is passed.
    Done at run time so a missing dependency surfaces with a clear message
    rather than at import.
    """
    provider = getattr(config, "llm_provider", None)
    if provider == "ollama":
        return (
            getattr(config, "ollama_large_model", ""),
            getattr(config, "ollama_small_model", ""),
        )
    import graphia.llm as llm_mod

    return (
        getattr(config, "large_model", None) or llm_mod._LARGE_MODEL_ID,
        getattr(config, "small_model", None) or llm_mod._SMALL_MODEL_ID,
    )


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

# The scripted-human caveat (spec-013 §2.1): every eval game is played against
# an automated stand-in in the human seat, so win-rate is a CONSISTENT
# comparable measure across runs — not a true game-balance figure.
# Machine-emitted as ``outcomes.note`` (immutable, like ``_BEDROCK_UPDATE_NOTE``)
# and distinct from the human-mutable top-level ``notes`` field.
#
# TWO VARIANTS, because spec 026 replaced the stand-in and MADE THE ACTIVE ONE
# THE DEFAULT. A single hard-coded note went stale the moment that landed: it
# kept asserting a passive seat while ``settings.scripted_player`` on the very
# same record read ``active``. Seventeen committed records carry that
# contradiction (2026-06-21 onward); they are left as-is because the ledger is
# append-only, and the discrepancy is documented in ``evals/README.md``. The
# lesson worth keeping: a machine-emitted note describing a CONFIGURABLE
# condition has to be derived from that condition, never restated beside it.
_OUTCOMES_HUMAN_CAVEAT_PASSIVE = (
    "win-rate is measured against a passive scripted human (always votes No, never "
    "initiates) — a consistent comparable measure, not true game balance."
)
_OUTCOMES_HUMAN_CAVEAT_ACTIVE = (
    "win-rate is measured against an active rule-based scripted human "
    "(deterministic, no model call; when Law-abiding it supplies the vote a "
    "correct town majority needs) — a consistent comparable measure, not true "
    "game balance."
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
        case "bedrock" | "bedrock-claude":
            # Spec 035 follow-up: the Claude profile is as server-side-opaque as
            # Nova — Anthropic/AWS can update the served weights behind a model
            # id with no signal to the caller — so it carries the same caveat.
            # Before this, `bedrock-claude` fell through the match with no note
            # at all, which is why the 2026-08-31 Claude n=50 record has none.
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


def score_persona_sim_sum(
    players: dict[str, PlayerState],
) -> dict[str, float | int]:
    """Pure scorer for ``persona_lex_mean`` + ``persona_lex_peak`` — SUM and MAX of pairwise persona similarity.

    The two continuous companions to :func:`score_persona_near_dup`. Where the latter
    *thresholds* each pair (count it when the ``difflib`` ratio ``>= 0.85``) and so
    floors at 0 — missing graded distinctness below the threshold — this returns the
    **raw sum of every pair's ratio** AND the **max** pair ratio, plus the pair count,
    so ``run_eval`` can fold a batch and report both the **mean** pairwise similarity
    (``sim_sum / denominator`` — how alike the cast is overall) and the **peak**
    (most-similar-pair) similarity (``max`` of per-game maxes — how alike the closest
    pair got). Both are continuous signals, not near-duplicate proportions.

    Setup is IDENTICAL to :func:`score_persona_near_dup` by construction: over a
    game's **AI** players (the human is skipped, ``persona is None`` skipped), build
    each persona's **table-facing** text — ``personality + " " + manner + " " +
    public_persona`` — and **never** ``true_self``, name-mask
    (:func:`_spec009_mask_names` against the AI names) and normalize
    (:func:`_spec009_normalize`), then walk the same unordered
    :func:`itertools.combinations` pairs. (It deliberately recomputes the same pairs
    ``score_persona_near_dup`` walks — personas are few, and keeping the two scorers
    separate leaves that function's signature/return untouched so its tests stay
    green.) ``sim_sum`` and ``sim_max`` are computed in a SINGLE pass over the pairs.

    Returns ``{"sim_sum": <sum of ALL pairwise difflib SequenceMatcher ratios>,
    "sim_max": <MAX pairwise ratio>, "denominator": <C(n, 2) pairs>}``. Fewer than 2
    AI personas offers no pairs, so ``denominator == 0`` yields
    ``{"sim_sum": 0.0, "sim_max": 0.0, "denominator": 0}`` — ``run_eval`` then omits
    both ``persona_lex_mean`` and ``persona_lex_peak`` (the same opportunity-based
    omission as ``persona_near_dup``).

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
    denominator = len(masked) * (len(masked) - 1) // 2
    if denominator == 0:
        return {"sim_sum": 0.0, "sim_max": 0.0, "denominator": 0}
    # Single pass over the same unordered pairs: accumulate the sum (for the batch
    # MEAN) and track the running max (for the batch PEAK — the closest-pair signal
    # the near-dup count floors away).
    sim_sum = 0.0
    sim_max = 0.0
    for a, b in combinations(masked, 2):
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        sim_sum += ratio
        if ratio > sim_max:
            sim_max = ratio
    return {"sim_sum": sim_sum, "sim_max": sim_max, "denominator": denominator}


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (pure Python, no numpy).

    The dot product over the product of the L2 norms. A zero-norm vector (an
    all-zeros embedding, which Bedrock never returns but the fake/edge cases
    could) yields ``0.0`` rather than dividing by zero. Used by
    :func:`score_persona_semantic_sim` over the ≤7 short persona vectors of one
    game — small enough that plain Python beats pulling numpy into the hot path
    (numpy is a dependency, but unneeded here, mirroring the difflib lexical
    scorers' no-heavy-dep posture).
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def score_persona_semantic_sim(
    players: dict[str, PlayerState],
    embed_fn: Callable[[list[str]], list[Sequence[float]]],
) -> dict[str, float | int | None]:
    """Pure-ish scorer for ``persona_sem_mean`` + ``persona_sem_peak`` — MEANING-based persona similarity (spec 033).

    The semantic counterpart to the LEXICAL :func:`score_persona_sim_sum`. Where
    the lexical mean compares the persona *texts* word-for-word (``difflib``),
    this embeds each persona's table-facing text into a meaning vector and takes
    the **cosine** of each unordered pair — so two characters written in
    different words but the same *kind* (calm, watchful, even-tempered) read as
    similar where the lexical measure reads them as distinct.

    ``embed_fn`` is **INJECTED** — a callable taking a list of texts and
    returning one vector per text (production passes
    ``get_embeddings().embed_documents``; the offline tests pass a deterministic
    fake). It is called **once per game** (a single batch), never per pair.

    Text construction is **IDENTICAL** to the lexical scorers by design: over a
    game's **AI** players only (the human is skipped, ``persona is None``
    skipped), build ``personality + " " + manner + " " + public_persona`` —
    **never** a Mafioso's ``true_self``, so no hidden content enters the
    comparison — then name-mask (:func:`_spec009_mask_names` against the AI
    names) and normalize (:func:`_spec009_normalize`), so a shared name token
    can't drive the cosine between two otherwise-different characters.

    Returns ``{"mean": <average cosine over C(n, 2) pairs> | None,
    "peak": <MAX cosine over the pairs> | None, "denominator": <C(n, 2)>}`` — both
    VALUE-type facets (a similarity, NOT a binomial proportion), the semantic
    parallels to the lexical ``persona_lex_mean``/``persona_lex_peak``. The
    ``mean`` and ``peak`` are computed in a SINGLE pass over the pairs. Fewer than
    2 AI personas offers no pair, so it returns ``{"mean": None, "peak": None,
    "denominator": 0}`` — *absent, not a misleading 0* — and ``run_eval`` then
    omits BOTH semantic metrics, the same opportunity-based omission as the
    lexical persona metrics.

    DRIVER-INDEPENDENT BY DESIGN: takes a plain ``players`` map + an injected
    ``embed_fn``, so the offline tests build synthetic rosters and pass a fake
    embedder with no live model.
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
    denominator = len(masked) * (len(masked) - 1) // 2
    if denominator == 0:
        return {"mean": None, "peak": None, "denominator": 0}
    # ONE batch embed call per game (not per pair) — production hits Bedrock once
    # for the whole roster, tests hit the fake once.
    vectors = embed_fn(masked)
    # Single pass over the unordered pairs: accumulate the cosine sum (for the
    # batch MEAN) and track the running max (for the batch PEAK — the closest-pair
    # cosine, the semantic parallel of ``score_persona_sim_sum``'s ``sim_max``).
    cos_sum = 0.0
    cos_max = 0.0
    for a, b in combinations(vectors, 2):
        cos = _cosine(a, b)
        cos_sum += cos
        if cos > cos_max:
            cos_max = cos
    return {
        "mean": cos_sum / denominator,
        "peak": cos_max,
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

    A present RATE metric (``denominator > 0`` with a ``count`` — the only kind
    ``run_eval`` keeps with that shape) gets two float siblings AFTER
    ``denominator`` from :func:`wilson_ci` on its own ``count``/``denominator``,
    giving each rate a reliability band (a wide one flags a small-``n`` noise
    rate). Absent metrics never reach here — ``run_eval`` already omitted them —
    so the absent-omission convention is untouched and no CI is invented for a 0/0
    metric. The CI is derived/supplementary: it reads existing fields only and
    does NOT bump ``METRICS_VERSION``.

    A VALUE-type metric (the persona similarity facets ``persona_lex_mean`` /
    ``persona_lex_peak`` / ``persona_sem_mean`` / ``persona_sem_peak`` —
    ``mean``/``peak`` + ``denominator``, no ``count``) is SKIPPED: a mean/peak is
    not a binomial rate, so a Wilson band is
    meaningless for it. The ``count``-absence guard is what excludes it, leaving
    the band behaviour for every existing rate metric unchanged.
    """
    for facets in metrics.values():
        denominator = facets.get("denominator")
        if not isinstance(denominator, int) or denominator <= 0:
            continue  # defensive — present metrics always have denominator > 0
        count = facets.get("count")
        if not isinstance(count, int):
            continue  # mean-type metric (no ``count``) — Wilson CI does not apply
        low, high = wilson_ci(count, denominator)
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
    *,
    scripted_active: bool = True,
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
    # Derived from the run's actual stand-in, never restated independently of
    # it — see the constants' banner for why.
    block["note"] = (
        _OUTCOMES_HUMAN_CAVEAT_ACTIVE
        if scripted_active
        else _OUTCOMES_HUMAN_CAVEAT_PASSIVE
    )
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


# ===========================================================================
# Spec 039 §2.10 (author-approved fold-in) — the DIARY FALLBACK RATE.
#
# WHY IT EXISTS, and it is not hypothetical. A 1-game ollama diaries-ON smoke
# produced 9 of 11 byte-identical ``_DIARY_FALLBACK`` entries and NOTHING said
# so: the ledger record read like any other on-arm run, the transcript showed
# eleven diary elements without distinguishing them, and the harness installs no
# logging handler, so ``day_diary``'s per-player ``logger.exception`` left no
# trace either. A run that measured the PLACEHOLDER four times out of five was
# indistinguishable from a clean measurement of the feature.
#
# This makes the on arm SELF-VALIDATING: the record states, per run, how many of
# the diary entries it played are the deterministic placeholder, out of how many
# were attempted. (The cause of that smoke is established and is NOT model
# incapability — probed directly, diary structured output succeeds 5/5 on
# ollama; most likely a cold-start timeout on the first fan-out, since Day 1 was
# all fallback and Day 2 partly real, the opposite of what context growth would
# predict. This is the SIGNAL, not the fix.)
#
# WHERE IT SITS, AND WHY NOT IN ``metrics``. Under ``quality.``, beside
# ``games_failed_early``: it is RUN HEALTH — "did this run measure the thing it
# claims to?" — exactly like "3 of 10 games failed early". No model is judged
# well or badly by it, so it must never enter ``eval_ledger.METRIC_ORDER`` or the
# record's metric tail. Purely additive record shape, no changed detection rule
# and no changed denominator ⇒ ``METRICS_VERSION`` is NOT bumped (the
# ``outcomes`` / ``vote_activity`` / ``ci_low`` precedent).
#
# NO WILSON BAND, deliberately — the one place this departs from the ledger's
# rate convention, and the reason is the block it lives in. ``quality`` is a
# CENSUS of one run, not a sample of a population: ``games_attempted`` /
# ``games_completed`` / ``games_failed_early`` are exact counts of what happened,
# and none of them carries a rate, let alone an interval. 9-of-11 placeholder
# entries is not an ESTIMATE of an underlying placeholder rate — it is the
# complete, exact composition of this run's diary content, so there is no
# sampling uncertainty for an interval to express. Two supporting reasons: the
# observed failures CLUSTER (a whole Day's fan-out at once), so the independent-
# Bernoulli assumption a Wilson band rests on is violated in exactly the
# direction that would make the band a lie; and ``_attach_ci`` operates over
# ``result.metrics`` and keys off ``count``, so banding a ``quality`` field would
# mean either leaking it into the metric map or restating that contract in a
# second place. What the ledger's rule actually protects against is honoured:
# the rate is never written without its denominator beside it.
#
# ABSENT, NEVER A MISLEADING ZERO. The keys are emitted only when at least one
# entry was attempted. A diaries-OFF run attempts none (``day_diary`` returns
# ``{}`` above its fan-out), so it records nothing here — a
# ``diary_fallback_rate: 0.0`` would assert a clean measurement of a feature
# that never ran. The gate is on the DATA (denominator > 0), not on the arm
# label: an on-arm run whose games all ended before any Day closed therefore
# records nothing too, and an off-arm run that somehow DID write entries (an
# ADR-011 parity break) is counted rather than hidden.
# ===========================================================================


def _diary_fallback_text() -> str | None:
    """The node's deterministic diary placeholder text, or ``None`` (spec 039).

    THE COUPLING, STATED PLAINLY. ``_DIARY_FALLBACK`` is a module-PRIVATE
    constant of ``graphia.nodes.day`` and this harness reaches in for it. The
    alternative on one side — a COPY of the sentence in this module — is
    silently wrong the day someone rewords the fallback: the counter would read
    a clean ``0.0`` for a run that was all placeholder, which is precisely the
    failure this signal exists to prevent. Importing the one definition is the
    discipline this module already applies to the vote templates and the
    spec-009 near-duplicate scorer: IMPORT the thing, never copy it, so a reword
    breaks loudly in the offline suite instead of drifting a measurement.

    The alternative on the other side — having the NODE surface the fact (a flag
    on ``DiaryRecord``, or a second return value) — was rejected as out of
    proportion: it would change gameplay state shape, the checkpoint payload,
    the transcript renderer's input and the other read sites, all to avoid one
    private import in a measurement tool. The measurement must not reshape the
    thing measured.

    Imported LAZILY, not at module scope: ``graphia.ui.ledger_viewer`` imports
    ``LEDGER_PATH`` from this module, and ``graphia.nodes.day`` drags in the
    whole gameplay stack (``diary_store``, ``career_events``, the prompts) —
    which a no-model ledger viewer has no business loading. The same reason
    ``_make_scripted_seat`` imports that module lazily.

    GRACEFUL DEGRADATION: a rename or removal yields ``None``, the caller logs
    once, and the run omits the diary-fallback keys and completes. A measurement
    must never take down the thing it measures — a 30-minute Bedrock batch does
    not die because a constant moved. The LOUD half of the contract lives in the
    offline suite, which imports the constant directly and fails on a rename.
    """
    try:
        from graphia.nodes.day import _DIARY_FALLBACK
    except Exception:  # noqa: BLE001 - the instrument is optional, the run is not
        return None
    return _DIARY_FALLBACK if isinstance(_DIARY_FALLBACK, str) else None


def score_diary_fallback(
    private_diaries: dict[str, list[DiaryRecord]] | None,
    fallback_text: str,
) -> dict[str, int]:
    """Pure scorer for the diary-fallback run health — ``{count, denominator}``.

    Over ONE game's ``private_diaries`` channel (the per-player entry lists as
    accumulated by ``state._merge_private_diaries``): ``denominator`` is every
    entry the fan-out produced — the entries ATTEMPTED, because ``day_diary``
    accumulates exactly one record per surviving AI writer whether the model
    answered or not — and ``count`` is the entries whose text is EXACTLY
    ``fallback_text``.

    EXACT EQUALITY is the right rule, not a fuzzy match. ``_DIARY_FALLBACK`` is
    stored verbatim (``_clamp_diary_entry`` runs on model text only, and would
    leave the constant unchanged anyway), so an entry either IS the placeholder
    or is a real one. A model that independently produced the identical sentence
    would be miscounted; that is not a real risk, whereas a looser rule would
    manufacture false positives out of short honest entries — and this figure is
    read as a tripwire, so a false positive is the expensive direction.

    Defensive over the channel's shape — a non-list value, a non-dict entry, or a
    missing/non-string ``text`` is skipped rather than raising, because this runs
    inside a measured batch that must finish.

    DRIVER-INDEPENDENT BY DESIGN: takes a plain channel map plus the placeholder
    text as an ARGUMENT (it never reaches for the constant itself), so the
    offline tests score synthetic entries with no live model.
    """
    count = 0
    denominator = 0
    for entries in (private_diaries or {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            if not isinstance(text, str):
                continue
            denominator += 1
            if text == fallback_text:
                count += 1
    return {"count": count, "denominator": denominator}


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

    Fixed key order (spec-013 §2.3 shape, extended by spec 036) — ``run`` →
    ``code`` → ``provider`` → ``settings`` → ``quality`` → ``outcomes`` →
    ``vote_activity`` → ``generation`` → ``metrics`` → ``notes``. The
    run-dynamics blocks all sit in the same band after ``quality`` and before
    ``metrics`` — the two game-dynamics ones (``outcomes`` / ``vote_activity``)
    first, then the bench-only ``generation`` last in that band, immediately
    before the metric family whose denominators it contextualises. Each is
    conditional and they are mutually exclusive in practice, so a record carries
    the pair OR ``generation``, never both. ``notes`` is always LAST:

        run:
          date: '<iso date>'
          kind: '<record kind>'  # spec 036 — e.g. 'persona-bench'; OMITTED ⇒ a played game
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
          private_diaries_enabled: <bool>  # spec 039 — the diaries ARM this run measured; OMITTED (never false) when the run could not state it
          lineup:                  # spec 014 — the configured whole-table counts
            num_citizens: <int>
            num_mafia: <int>
          persona:                 # spec 036 — persona-bench only: the conditions the measurement ran under
            diversity_enabled: <bool>       # the ARM actually run (--diversity on/off), not the ambient default
            collision_threshold: <float>    # the bar two personas are judged too alike at
            regen_attempts: <int>
            temperature: <float>
        quality:
          games_attempted: <int>
          games_completed: <int>
          games_failed_early: <int>
          # spec 039 — RUN HEALTH, not a metric. All three OMITTED together when the
          # run attempted no diary entry (a diaries-off arm) — absent, never a 0.0.
          diary_fallback_rate: <float>      # placeholder entries / entries attempted
          diary_fallback_entries: <int>     # entries that are graphia.nodes.day._DIARY_FALLBACK
          diary_entries_attempted: <int>    # the denominator — the rate is never written alone
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
        generation:            # spec 036 — persona-bench only; WHOLE BLOCK omitted for a game run
          collisions: <int>    # casts that ended with an over-similar persona pair
          regenerations: <int> # regeneration attempts that fired
        metrics:
          repetition:
            rate: <float>
            count: <int>
            denominator: <int>
            ci_low: <float>   # Wilson 95% lower bound (every present RATE metric)
            ci_high: <float>  # Wilson 95% upper bound
          persona_lex_mean:   # VALUE-type facet (specs 031-033): mean|peak + denominator, no rate/CI
            mean: <float>
            denominator: <int>
        notes: '<free text, or empty>'

    Absent provenance fields render as YAML ``null`` (an unreached git commit, a
    missing ollama digest, a bedrock run's empty server version) so they read as
    genuinely absent rather than as the empty string. ``run.kind`` (spec 036) is
    the opposite treatment — a CONDITIONAL key, omitted outright when the run
    recorded no kind, so a game run's record is byte-identical to a pre-036 one
    and *absent* is what means "a played game". The ``generation`` block (spec
    036) is conditional the same way, but as a WHOLE BLOCK: a game run populates
    none, so nothing is emitted; a bench run emits both of its counts with a
    visible integer, because a measured ``collisions: 0`` is the finding.
    ``notes`` is always
    emitted LAST — present even when empty (``notes: ''``) so the record visibly
    invites hand-editing. A note with no newline renders as a single
    safely-quoted scalar; a multi-line note renders as a YAML literal block
    scalar (``notes: |`` then indented lines). It is the one human-mutable
    field; the machine fields above stay immutable.
    """
    lines: list[str] = []

    lines.append("run:")
    # Built key-by-key rather than as one literal because ``kind`` is
    # CONDITIONAL yet must land immediately after ``date``: ``_yaml_block``
    # renders in insertion order, so the insertion sequence *is* the key order.
    run_block: dict[str, object] = {"date": run_date}
    # ``kind`` (spec 036 §2 Component A) — which kind of measurement this record
    # is, emitted ONLY when the run recorded one (the ``transcript_dir`` /
    # ``settings.lineup`` conditional-emission pattern: *only emitted when the
    # run recorded it*). An empty ``kind`` — every game run, and every bare
    # synthetic ``EvalResult`` — omits the key entirely, so existing records stay
    # byte-identical and absent keeps meaning "a played game". No
    # ``METRICS_VERSION`` bump: no scoring rule changed.
    if result.kind:
        run_block["kind"] = result.kind
    run_block["duration_seconds"] = result.duration_seconds
    run_block["metrics_version"] = METRICS_VERSION
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
    # Spec 039 §2.10: the diaries ARM LABEL — which side of the on/off A/B this
    # run measured — rendered flat, straight after ``scripted_player`` and before
    # the nested ``lineup``. CONDITIONAL for a reason peculiar to this key:
    # ABSENCE IS A THIRD CASE, not a falsy one. The ledger already has
    # absent ⇒ prior-default readings (``scripted_player``, ``run.kind``); this
    # is neither of those. A pre-039 record was played by a build with NO diary
    # feature at all — behaviourally the off arm insofar as ADR-011 parity holds,
    # but NOT the claim "this run measured the off arm". So an unstated arm is
    # omitted and renders blank downstream; rendering ``false`` would assert a
    # measurement nobody made. Additive ⇒ no ``METRICS_VERSION`` bump.
    private_diaries_enabled = settings.get("private_diaries_enabled")
    if private_diaries_enabled is not None:
        flat_settings["private_diaries_enabled"] = private_diaries_enabled
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
    # ``settings.persona`` (spec 036 §2 Component B) — the resolved persona knobs
    # the measurement actually ran under, as a one-level nested sub-map (the
    # ``settings.lineup`` precedent, rendered after it). CONDITIONAL and
    # ADDITIVE: only emitted when the run recorded them, so a game run — and
    # every already-committed record — renders byte-identically, and no
    # ``METRICS_VERSION`` bump is warranted (no scoring rule changed).
    #
    # This block is what makes a diversity-ON / diversity-OFF pair readable AS A
    # PAIR: the two records are otherwise indistinguishable columns of numbers,
    # and a similarity mean compared across a changed collision bar or a changed
    # temperature is not a comparison at all. ``diversity_enabled`` is the arm
    # the run was actually invoked with (the bench's ``--diversity`` flag), NOT
    # the ambient config default — recording the default would silently mislabel
    # every flag-off arm.
    persona = settings.get("persona")
    if isinstance(persona, dict):
        lines.append("  persona:")
        lines += _yaml_block(
            {
                "diversity_enabled": persona.get("diversity_enabled"),
                "collision_threshold": persona.get("collision_threshold"),
                "regen_attempts": persona.get("regen_attempts"),
                "temperature": persona.get("temperature"),
            },
            indent=2,
        )

    lines.append("quality:")
    # Built key-by-key (the ``run_block`` pattern) rather than as one literal,
    # because spec 039's diary-fallback keys are CONDITIONAL yet must land beside
    # ``games_failed_early`` — the other "did this run measure anything?" count —
    # rather than trailing after ``duration_seconds``. ``_yaml_block`` renders in
    # insertion order, so the insertion sequence IS the key order.
    quality_block: dict[str, object] = {
        "games_attempted": result.games_attempted,
        "games_completed": result.games_completed,
        "games_failed_early": result.games_failed_early,
    }
    # Spec 039 §2.10 fold-in — the DIARY FALLBACK share, so a diaries-on run that
    # actually measured the deterministic placeholder says so in its own record
    # instead of reading like a clean measurement (the 9-of-11 smoke). Emitted as
    # THREE FLAT KEYS, matching ``quality``'s existing flat-scalar shape rather
    # than importing ``metrics``' nested ``{rate, count, denominator}`` facet:
    # this is a run-health census, not a scored metric, and it must not read as
    # one. The rate is derived HERE (the result carries only the count/
    # denominator pair) so there is one definition of it, and it is never emitted
    # without its denominator beside it. NO Wilson band — see
    # :func:`score_diary_fallback`'s banner. Additive ⇒ no ``METRICS_VERSION``
    # bump; and because the whole trio is conditional on a denominator the
    # feature only produces, every one of the committed records renders
    # byte-identically.
    diary_attempted = int(result.diary_fallback.get("denominator", 0))
    if diary_attempted > 0:
        diary_placeholders = int(result.diary_fallback.get("count", 0))
        quality_block["diary_fallback_rate"] = diary_placeholders / diary_attempted
        quality_block["diary_fallback_entries"] = diary_placeholders
        quality_block["diary_entries_attempted"] = diary_attempted
    quality_block["duration_seconds"] = result.duration_seconds
    lines += _yaml_block(quality_block, indent=1)

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
            {"note": result.outcomes.get("note", _OUTCOMES_HUMAN_CAVEAT_ACTIVE)}, indent=1
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

    # ``generation`` (spec-036 §2 A/B) — the persona-generation PROCESS counts,
    # rendered LAST in the after-``quality``/before-``metrics`` run-dynamics band
    # so it sits immediately beside the persona facets whose denominators it
    # contextualises (a similarity mean alone loses "how many casts shipped a
    # near-duplicate" — the count that carried the spec-034 2-in-10 → 0-in-10
    # result). CONDITIONAL as a WHOLE BLOCK: a game run populates no
    # ``generation``, so the section omits itself entirely and every existing
    # record renders byte-identically — the ``outcomes``/``vote_activity``
    # pattern. Within a PRESENT block both keys are always emitted with a visible
    # integer, the ``vote_activity`` explicit-zero treatment rather than
    # ``metrics``' absent-≠-0 one: here ``collisions: 0`` is the measured
    # headline finding, not a no-opportunity absence.
    if result.generation:
        lines.append("generation:")
        lines += _yaml_block(
            {
                "collisions": int(result.generation.get("collisions", 0)),
                "regenerations": int(result.generation.get("regenerations", 0)),
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
        # Fixed sub-key order for clean diffs across runs and metrics. A
        # RATE-type metric carries ``rate``/``count``; a VALUE-type one (the
        # persona similarity facets of specs 031-033) carries ``mean`` OR
        # ``peak`` instead — never both families — and both land before the
        # shared ``denominator``, which is the shape the committed records
        # already use (``mean:`` then ``denominator:``). ``ci_low`` / ``ci_high``
        # are the Wilson 95% reliability band, rendered as floats right after
        # ``denominator`` whenever they were attached (every present RATE
        # metric; a mean/peak is not a binomial proportion, so it has none); a
        # synthetic record without them simply omits the two lines.
        #
        # ``mean`` / ``peak`` were MISSING from this tuple until spec 036: specs
        # 032/033 recorded the value-type facets on ``EvalResult`` and taught the
        # viewer to render them, but the ledger writer silently dropped them, so
        # the affected records carry a bare ``denominator`` with no measured
        # value. Fixing it here is purely additive — a metric that carries
        # neither key renders byte-identically — and it is a precondition for
        # spec 036's bench record, whose whole payload is these four facets.
        ordered = {
            key: facets[key]
            for key in (
                "rate",
                "count",
                "mean",
                "peak",
                "denominator",
                "ci_low",
                "ci_high",
            )
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
    # Spec-039: this game's ``private_diaries`` channel — every player's
    # before-Night entries in write order, as accumulated by
    # ``state._merge_private_diaries``. Read from the SAME final state as
    # ``players``/``messages``, which is safe here — unlike the per-Night
    # pointing channels ``night_open`` resets — precisely because that reducer
    # CONCATENATES: the final snapshot holds every Day's entries, not just the
    # last one's. Feeds the run-health :func:`score_diary_fallback` count and
    # nothing else; no metric reads it, and an entry never enters ``messages``
    # (spec 039's privacy invariant). Defaults to ``{}`` so a hand-built
    # ``_GameCapture`` in an offline test needs no diary wiring.
    private_diaries: dict[str, list[DiaryRecord]] = field(default_factory=dict)


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
    # games mid-Day as "no winner" is gone; the loop below only watches
    # ``snapshot.next``.
    #
    # ``--max-days`` USED TO BE ASSIGNED HERE, and that was the live
    # ``settings.max_days`` MISLABEL (fixed in spec 039 Slice 5). Setting
    # ``GRAPHIA_MAX_DAYS`` at this point is *after* ``main()`` resolved the one
    # config ``run_eval`` builds the recorded ``settings`` block from — so a
    # ``--max-days 4`` run was HONOURED in game (each game's own
    # ``load_config()`` below saw the env var) yet RECORDED as the default 12.
    # Two Slice-5 smoke records carry exactly that contradiction: ``max_days:
    # 12`` beside ``runaway: 1`` — a record that is self-consistent and wrong.
    #
    # The assignment now lives in ``main()``, beside ``--scripted-player``'s and
    # ``--diaries``', before the single ``load_config()`` the record is built
    # from. IN-GAME BEHAVIOUR IS UNCHANGED: the env var persists for the whole
    # process, so the ``load_config()`` below still resolves the override into
    # ``config.max_days`` — which is what binds the runaway cap into the graph
    # and sizes the anti-hang backstop. Only the RECORDED value moved.
    #
    # The consequence to record (the same CLI-layer/writer-layer split spec 039
    # §2.12 documents for the diaries sentinel): a DIRECT ``run_eval(...)`` call
    # that bypasses ``main()`` no longer applies ``args.max_days`` to the games.
    # That is the correct direction — a caller resolving its own config must set
    # the env before doing so, or the record would disagree with the run.

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
            # Spec-039 run health: the accumulated diary channel (see
            # ``_GameCapture.private_diaries``), so ``run_eval`` can count how
            # many of this game's entries the fan-out filled with the
            # deterministic placeholder instead of a model answer.
            private_diaries=state.get("private_diaries", {}) or {},
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
    transcripts from the ledger. Spec 039 §5 adds the CLI equivalents:
    ``args.transcripts_root`` and ``args.ledger_path`` redirect the same two
    seams for a real run (a smoke run into a scratch dir; one ledger file per
    arm), with the injected kwarg still winning for tests.

    Spec 039 §2.10: the run's diaries ARM (``settings.private_diaries_enabled``,
    plus a ``diaries=on|off`` part in every transcript header) is read off
    ``config`` through the ``_diaries_arm`` sentinel, so a config that cannot
    answer records NOTHING rather than the on arm. ``main`` refuses such a run
    outright; a direct call with a thin stub config simply omits the key.
    """
    large_model, small_model = _resolved_model_names(config)

    # Spec-017: resolve the transcript store + the once-per-run id. The base dir
    # defaults to the repo's ``evals/transcripts/`` but is injectable; the run-id
    # is a fresh fs-safe timestamp unless a test pins one. Generated here, before
    # the loop, so every game in the run shares one directory.
    # Precedence: the injected kwarg (tests, a ``tmp_path``) beats the CLI
    # ``--transcripts-root`` (spec 039 §5), which beats the repo's
    # ``evals/transcripts/``. The CLI arm exists because ``collect_code_provenance``
    # runs ``git status --porcelain``, which SEES an untracked transcript dir — so
    # a smoke run left in the tree stamps the NEXT record ``code.dirty: true``.
    # Writing outside the repo removes that cross-contamination without a
    # worktree. Read off ``args`` with ``getattr``, like the settings block below,
    # so the thin ``argparse.Namespace``s built directly in tests stay valid.
    cli_transcripts_root = getattr(args, "transcripts_root", None)
    if transcripts_root is not None:
        transcripts_base = transcripts_root
    elif cli_transcripts_root is not None:
        transcripts_base = Path(cli_transcripts_root)
    else:
        transcripts_base = TRANSCRIPTS_ROOT
    transcript_run_id = run_id if run_id is not None else make_run_id()
    # ``run_meta`` feeds the per-transcript header (provider + resolved models +
    # game count); read defensively by the renderer so a thin mapping is fine.
    # Spec 039 §2.10: this run's diaries ARM, resolved ONCE off the config the
    # games themselves will load. ``main`` set ``GRAPHIA_PRIVATE_DIARIES`` from
    # ``--diaries`` BEFORE ``load_config()``, so this is the arm actually played
    # rather than the ambient default. ``None`` only for a thin/stub config
    # passed to ``run_eval`` directly — ``main`` refuses such a run before game 1.
    diaries_arm = _diaries_arm(config)
    run_meta: dict[str, object] = {
        "provider": args.provider,
        "large_model": large_model,
        "small_model": small_model,
        "games": args.games,
    }
    # The arm goes into every transcript header too, as the STRING "on"/"off":
    # ``eval_transcript._meta_get`` is truthiness-gated, so a raw ``False`` would
    # be silently dropped here — and the off arm's transcripts, the very files
    # that evidence the flag-off path wrote no diary, would carry no label.
    if diaries_arm is not None:
        run_meta["diaries"] = "on" if diaries_arm else "off"
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
    # Annotated ``dict[str, object]`` (like ``render_record``'s ``flat_settings``)
    # because the conditional spec-039 arm key below adds a ``bool`` to a literal
    # of strings / ints / ``None`` / a nested map.
    settings: dict[str, object] = {
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
    # Spec 039 §2.10: the diaries ARM LABEL. FLAT because there is one knob
    # (spec 036's ``settings.persona`` sub-map earned its nesting with four) and
    # BOOLEAN because the direct analogue ``persona.diversity_enabled`` is one,
    # while ``scripted_player``'s strings name two real policies. Emitted after
    # ``scripted_player`` and before the nested ``lineup``.
    #
    # CONDITIONAL, and absence is a distinct third case here — see
    # ``render_record``: an absent key means the run could not state its arm (or
    # predates the feature), which is NOT the claim ``false`` makes. Additive ⇒
    # no ``METRICS_VERSION`` bump.
    if diaries_arm is not None:
        settings["private_diaries_enabled"] = diaries_arm

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

    # Spec-031 (additive) persona similarity MEAN: the continuous companion to
    # ``persona_near_dup``. Where that thresholds each pair and floors at 0, this
    # sums every pair's raw difflib ratio across games (``sim_sum``) over the SAME
    # pair count (``denominator``), so the batch reports the MEAN pairwise
    # similarity (``sim_sum / denominator``) — a graded distinctness signal the
    # near-dup count misses. Summed via the same per-game-then-sum pattern; a new
    # orthogonal metric, so ``METRICS_VERSION`` is NOT bumped.
    persona_sim_total: dict[str, float] = {"sim_sum": 0.0, "denominator": 0.0}

    # Spec-032 (additive) persona similarity PEAK: the running MAX of each game's
    # ``sim_max`` (the most-similar pair in any game), so the batch reports the
    # closest-pair similarity. The peak generalizes ``persona_near_dup``: when no
    # pair clears the 0.85 near-dup threshold the count is 0, yet the peak still
    # says how close the closest pair got — and reaches the top of its range if a
    # few characters collapse to the same template (the case the mean smooths over).
    # A value-type metric (no rate/count), so ``METRICS_VERSION`` is NOT bumped.
    persona_sim_max_run: float = 0.0

    # Spec-033 (additive) SEMANTIC persona similarity: the meaning-based companion
    # to the lexical mean. Resolve the embeddings client ONCE up front — it is
    # ALWAYS Bedrock (a fixed measuring instrument), so an ollama gameplay run with
    # no AWS creds, an unavailable embeddings model, or a constructor error must
    # NOT crash the measured run. GRACEFUL DEGRADATION: on any failure here the
    # embed fn is left ``None`` (logged once to stderr), every game's semantic
    # scoring is skipped, and the metric is simply OMITTED from the record — the
    # eval continues. ``get_embeddings`` is reached through the module-level
    # binding so the offline suite's ``safe_llm`` fake lands (no real Bedrock).
    embed_fn: Callable[[list[str]], list[Sequence[float]]] | None = None
    try:
        embed_fn = get_embeddings().embed_documents
    except Exception as exc:  # noqa: BLE001 - never fail the run on the optional metric
        print(
            "  persona_sem_mean/persona_sem_peak: embeddings unavailable "
            f"({type(exc).__name__}: {exc}) — omitting the semantic metrics",
            file=sys.stderr,
        )
    # Σ of per-game cosines + total pairs across games (same per-game-then-sum
    # pattern as the lexical mean), folded into the batch MEAN cosine below.
    persona_sem_total: dict[str, float] = {"cos_sum": 0.0, "denominator": 0.0}

    # Spec-033 (additive) SEMANTIC persona similarity PEAK: the running MAX of each
    # game's peak cosine (the most-similar pair in any game), the semantic parallel
    # of ``persona_sim_max_run`` — so the batch reports the closest-pair cosine
    # anywhere in the run. A value-type metric (no rate/count), recorded under the
    # SAME gate as the semantic mean (embeddings resolved AND ≥1 pair scored).
    persona_sem_max_run: float = 0.0

    # Spec-039 (§2.10 fold-in) DIARY FALLBACK RUN HEALTH: how much of this run's
    # diary content is the node's deterministic placeholder rather than a model
    # answer. The placeholder text is resolved ONCE, up front — a lazy, guarded
    # reach into ``graphia.nodes.day``'s private constant (see
    # :func:`_diary_fallback_text` for the coupling argument, and
    # :func:`score_diary_fallback`'s banner for why this is ``quality`` and not a
    # metric). GRACEFUL DEGRADATION: unavailable ⇒ log once here and omit the
    # keys; the measured run completes regardless. Deliberately NOT gated on the
    # diaries arm, so an off-arm run that unexpectedly wrote entries is counted
    # rather than hidden.
    diary_fallback_text = _diary_fallback_text()
    if diary_fallback_text is None:
        print(
            "  quality.diary_fallback_rate: graphia.nodes.day._DIARY_FALLBACK "
            "is unavailable — omitting the diary-fallback run-health counts",
            file=sys.stderr,
        )
    # Σ placeholder entries and Σ entries attempted across the completed games —
    # the same per-game-then-sum pattern as the action metrics, so the recorded
    # rate is Σplaceholder / Σattempted and never a mean-of-rates.
    diary_fallback_total: dict[str, int] = {"count": 0, "denominator": 0}

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

        # Spec-031 (additive): fold this game's summed pairwise persona similarity
        # and pair count into the batch totals (same per-game-then-sum pattern), so
        # the batch reports the MEAN similarity below. Spec-032 (additive): also
        # fold this game's ``sim_max`` into the running batch MAX, so the batch
        # reports the PEAK (most-similar-pair) similarity — the max over all pairs
        # is the max of the per-game maxes.
        persona_sim_facets = score_persona_sim_sum(cap.players)
        persona_sim_total["sim_sum"] += float(persona_sim_facets["sim_sum"])
        persona_sim_total["denominator"] += int(persona_sim_facets["denominator"])
        persona_sim_max_run = max(
            persona_sim_max_run, float(persona_sim_facets["sim_max"])
        )

        # Spec-033 (additive): the SEMANTIC (Bedrock-embedding cosine) companion.
        # Only when the embeddings client resolved (graceful-degradation gate) —
        # if it is unavailable the whole metric is omitted. Wrapped per game so a
        # single transient embed failure (throttling, a bad response) skips THAT
        # game's contribution and logs once, rather than crashing the batch; the
        # mean folds over whatever games did score. The scorer recovers ``mean ===
        # None`` for a <2-AI roster (no pair), contributing nothing.
        if embed_fn is not None:
            try:
                sem_facets = score_persona_semantic_sim(cap.players, embed_fn)
                sem_mean = sem_facets["mean"]
                sem_denom = int(sem_facets["denominator"])
                if sem_mean is not None and sem_denom > 0:
                    # ``mean`` is the per-game average cosine over ``sem_denom``
                    # pairs; recover the per-game cosine SUM so the batch mean is a
                    # true Σcosines / Σpairs (never a mean-of-means).
                    persona_sem_total["cos_sum"] += float(sem_mean) * sem_denom
                    persona_sem_total["denominator"] += sem_denom
                    # Fold this game's peak cosine into the running batch MAX — the
                    # most-similar pair anywhere is the max of the per-game maxes.
                    sem_peak = sem_facets["peak"]
                    if sem_peak is not None:
                        persona_sem_max_run = max(
                            persona_sem_max_run, float(sem_peak)
                        )
            except Exception as exc:  # noqa: BLE001 - optional metric, never fatal
                print(
                    f"  game {game_index}: persona_sem_mean/persona_sem_peak "
                    f"scoring FAILED ({type(exc).__name__}: {exc}) — skipping "
                    "this game's semantic contribution",
                    file=sys.stderr,
                )
                # A persistent failure (e.g. expired creds) would repeat per game;
                # disable further attempts so the run stays quiet and fast.
                embed_fn = None

        # Spec-039 run health: fold this game's placeholder-vs-attempted diary
        # entry counts into the batch totals. Skipped entirely when the
        # placeholder text could not be resolved (the counts then stay at zero
        # and the record omits the keys).
        if diary_fallback_text is not None:
            diary_facets = score_diary_fallback(
                cap.private_diaries, diary_fallback_text
            )
            diary_fallback_total["count"] += diary_facets["count"]
            diary_fallback_total["denominator"] += diary_facets["denominator"]

    # Spec-013 game-dynamics blocks, folded over the completed games. ``outcomes``
    # partitions the winners (``games`` = completed games denominator); the side
    # win-rates carry a Wilson CI, ``draw``/``no_winner`` are bare counts.
    # ``vote_activity`` carries the explicit-zero ``by_side`` (both keys always)
    # and the sparse ``by_day`` — already summed across games above.
    # Spec-027: pass the parallel per-game scripted seat sides so ``outcomes``
    # also carries the scripted stand-in's-side win rate (omitted when no game
    # resolved a side — the absent-metric posture).
    result.outcomes = tally_outcomes(
        winners,
        scripted_sides,
        scripted_active=bool(getattr(config, "scripted_player_active", True)),
    )
    result.vote_activity = {"by_side": vote_by_side, "by_day": vote_by_day}

    # Spec-039 (§2.10 fold-in): the diary-fallback run-health pair, recorded ONLY
    # when at least one entry was attempted — ABSENT, never a misleading zero, so
    # a diaries-off run (which attempts none) records nothing here rather than a
    # ``0.0`` asserting a clean measurement of a feature that never ran.
    # ``render_record`` derives the rate from this pair.
    if diary_fallback_total["denominator"] > 0:
        result.diary_fallback = dict(diary_fallback_total)

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

    # Spec-031 (additive) persona similarity MEAN: record the batch mean pairwise
    # similarity, present only when the batch offered at least one persona pair
    # (total pairs > 0) — the same opportunity-based omission as ``persona_near_dup``.
    # This is a MEAN, NOT a proportion: it carries ``mean``/``denominator`` (the pair
    # count), deliberately NO ``rate``/``count`` — so ``_attach_ci`` (which keys off
    # ``count``) skips it and attaches no Wilson band (a mean is not a binomial rate).
    total_persona_pairs = persona_sim_total["denominator"]
    if total_persona_pairs > 0:
        result.metrics["persona_lex_mean"] = {
            "mean": persona_sim_total["sim_sum"] / total_persona_pairs,
            "denominator": int(total_persona_pairs),
        }
        # Spec-032 (additive) persona similarity PEAK: the batch's most-similar-pair
        # similarity, present under the SAME opportunity gate as the mean (≥1 pair).
        # Like the mean it is a VALUE-type facet — ``peak``/``denominator``, NO
        # ``rate``/``count`` — so ``_attach_ci`` (which keys off ``count``) skips it
        # (a peak is not a binomial rate, no Wilson band).
        result.metrics["persona_lex_peak"] = {
            "peak": persona_sim_max_run,
            "denominator": int(total_persona_pairs),
        }

    # Spec-033 (additive) SEMANTIC persona similarity: the batch MEAN cosine,
    # Σcosines / Σpairs across games (a true pair-weighted mean, never a
    # mean-of-means). Recorded ONLY when the embeddings client resolved AND the
    # batch offered ≥1 persona pair — so it is OMITTED for an ollama run with no
    # AWS creds (the graceful-degradation gate left ``embed_fn`` None, no pairs
    # accumulated) exactly as it is omitted for a roster that never had ≥2 AI
    # personas. A VALUE-type facet — ``mean``/``denominator``, deliberately NO
    # ``rate``/``count`` — so ``_attach_ci`` (which keys off ``count``) skips it
    # (a cosine mean is not a binomial rate, no Wilson band); the viewer's
    # value-type render shows ``~<mean> (n=<pairs>)``, the same branch as the
    # lexical mean/peak.
    total_sem_pairs = persona_sem_total["denominator"]
    if total_sem_pairs > 0:
        result.metrics["persona_sem_mean"] = {
            "mean": persona_sem_total["cos_sum"] / total_sem_pairs,
            "denominator": int(total_sem_pairs),
        }
        # Spec-033 (additive) SEMANTIC persona similarity PEAK: the batch's
        # most-similar-pair cosine, present under the SAME gate as the semantic
        # mean (≥1 pair scored). Like the mean it is a VALUE-type facet —
        # ``peak``/``denominator``, NO ``rate``/``count`` — so ``_attach_ci``
        # (which keys off ``count``) skips it (a cosine peak is not a binomial
        # rate, no Wilson band); the viewer's value-type render shows
        # ``~<peak> (n=<pairs>)``, the same branch as the lexical peak.
        result.metrics["persona_sem_peak"] = {
            "peak": persona_sem_max_run,
            "denominator": int(total_sem_pairs),
        }

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
    # ``--ledger-path`` (spec 039 §5) redirects this write — a smoke run into a
    # scratch file outside the repo, or one file per arm so two concurrent runs
    # never share a writer (there is no lock). Absent ⇒ ``append_record``
    # resolves the module-global ``LEDGER_PATH`` AT CALL TIME, so the suite's
    # ``monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ...)`` still lands and
    # the committed ledger is never touched by a test.
    ledger = append_record(
        result,
        date.today().isoformat(),
        ledger_path=getattr(args, "ledger_path", None),
    )
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
        help=(
            "which real provider to measure: 'ollama' (local, free), "
            "'bedrock' (cloud Amazon Nova), or 'bedrock-claude' (cloud Claude "
            "Haiku 4.5 — costs more per token than Nova)"
        ),
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
        "--diaries",
        choices=("on", "off"),
        default=None,
        help=(
            "the per-AI private-diaries ARM for this run (spec 039): 'on' (the "
            "default) writes a diary before each Night and feeds each player its "
            "own recent entries; 'off' makes the day_diary node a no-op. Sets "
            "GRAPHIA_PRIVATE_DIARIES before the config is resolved and is "
            "recorded as settings.private_diaries_enabled, so the two arms of a "
            "comparison can be told apart in the ledger. Omit to record whatever "
            "the ambient default resolves to. Prefer this flag over exporting the "
            "env var around 'make': the Makefile does 'include .env' then "
            "'export', and a makefile assignment overrides the shell value."
        ),
    )
    ap.add_argument(
        "--ledger-path",
        type=Path,
        default=None,
        help=(
            "append this run's record here instead of the repo ledger "
            "(evals/blunder-ledger.yaml) — a scratch file for a smoke run, or "
            "one file per arm so two concurrent runs never share a writer "
            "(there is no lock). The parent dir is created; records are always "
            "appended, never rewritten. Merge by appending afterwards."
        ),
    )
    ap.add_argument(
        "--transcripts-root",
        type=Path,
        default=None,
        help=(
            "write this run's per-game transcripts under <root>/<run-id>/ "
            "instead of evals/transcripts/. Keeps a smoke run's untracked dir "
            "OUT of the repo, where 'git status --porcelain' would otherwise "
            "see it and stamp the NEXT run's record code.dirty: true."
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
    # Spec 023's runaway Day cap, spec 039 Slice 5's placement. This assignment
    # was made inside ``_play_one_game`` — AFTER the ``load_config()`` just
    # below, the one whose values ``run_eval`` records as ``settings`` — so the
    # override was honoured in game and recorded as the default 12. Assigning it
    # HERE, with the other pre-``load_config`` env knobs, makes the recorded
    # ``settings.max_days`` the value actually played. The games' own
    # ``load_config()`` calls still see it (the env var persists for the
    # process), so the cap fires exactly as before.
    if args.max_days is not None:
        os.environ["GRAPHIA_MAX_DAYS"] = str(args.max_days)
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
    # Spec 039 §2.10: the diaries ARM for this run. Assigned HERE — in ``main``,
    # beside ``--scripted-player``'s, BEFORE ``load_config()`` — because the
    # config resolved just below is what the recorded ``settings`` block is built
    # from. ``--max-days`` is the live defect of exactly this class: it assigns
    # ``GRAPHIA_MAX_DAYS`` inside ``_play_one_game``, AFTER that config was
    # resolved, so a ``--max-days 6`` run still records ``12`` (not this spec's
    # to fix, but it is the rule this assignment obeys).
    #
    # A CLI arm, NOT an env var around ``make``: the ``Makefile`` does
    # ``include .env`` then ``export``, and a makefile assignment OVERRIDES the
    # environment-derived value (reproduced experimentally). Today's ``.env``
    # holds no ``GRAPHIA_*`` gameplay flag, so ``GRAPHIA_PRIVATE_DIARIES=0 make
    # blunder-eval`` works only BY ACCIDENT — and would flip the moment anyone
    # adds one, producing a record that correctly says ``true`` while the
    # operator believed they had run the off arm.
    if args.diaries is not None:
        os.environ["GRAPHIA_PRIVATE_DIARIES"] = (
            "1" if args.diaries == "on" else "0"
        )

    # Imported here (after the env is set) so config picks up the forced
    # provider and the isolation, and so a missing dependency fails with a clear
    # message at run time rather than at module import.
    from graphia.config import load_config

    config = load_config()

    # Spec 039 §2.10: read the diaries arm back off the resolved config and
    # REFUSE the run if it cannot be stated — before the preflight, before game
    # 1, before a single token. This is the sentinel's fail-fast half; the
    # record's label is then guaranteed for every run that reaches the CLI.
    diaries_on = _require_diaries_arm(config)

    # Ollama path: same fail-fast boot preflight the game uses — verify the
    # server is up and both configured models are installed before any games.
    # Raises SystemExit with an actionable message on failure; bedrock has its
    # own credential/connectivity story and is not preflighted here.
    if args.provider == "ollama":
        from graphia.preflight import run_ollama_preflight

        run_ollama_preflight(config)
    elif args.provider == "bedrock-claude":
        # Spec 035: the same fail-fast Claude preflight the game boots with, so
        # an unreachable model (expired creds / missing model access / wrong
        # region) stops the run with a plain message BEFORE burning tokens on
        # game 1 — rather than failing partway through a paid batch. Nova
        # (``bedrock``) keeps its existing no-preflight story unchanged.
        from graphia.preflight import run_claude_preflight

        run_claude_preflight(config)

    print(
        f"Blunder-eval: provider={args.provider}, {args.games} game(s)"
        + (f", base seed={args.seed}" if args.seed is not None else ", unseeded structure")
        + (
            f", runaway Day cap {args.max_days}"
            if args.max_days is not None
            else ", default 12-Day runaway cap"
        )
        # Spec 039 §2.10: echo the ARM before the batch starts, so the operator
        # sees which side of the A/B is about to be measured rather than reading
        # it off the record afterwards.
        + f", diaries {'on' if diaries_on else 'off'}"
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
    # Spec 039 §2.10 fold-in: the diary-fallback share, printed as soon as the
    # batch ends. The ledger record carries the same figure, but a run worth
    # discarding should be visible at the console rather than having to be read
    # back out of a file. Absent (no entry attempted) ⇒ nothing printed, the same
    # absent-not-zero rule the record follows.
    diary_health = result.diary_fallback
    diary_attempted = int(diary_health.get("denominator", 0))
    if diary_attempted:
        diary_placeholders = int(diary_health.get("count", 0))
        print(
            f"diary entries: {diary_placeholders}/{diary_attempted} are the "
            "deterministic fallback "
            f"(rate={diary_placeholders / diary_attempted:.2f})"
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
