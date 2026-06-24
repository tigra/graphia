"""Spec 034 (Diversified Persona Generation), Slice 3 task 2 — bench test (mocked).

The isolated persona bench (``graphia.tools.persona_bench``) generates N rosters
via the REAL generation path (``generate_roster`` → ``assign_roles`` →
``generate_personas``) WITHOUT playing a game, scores them with the spec-032/033
scorers, and prints a summary. This test drives it entirely under the autouse
``safe_llm`` fakes — the ``fake_small`` roster fake + the unified ``fake_large``
(which also covers ``get_persona_model`` and the semantic ``get_embeddings`` seam,
per ``tests/conftest.py``) — so it reaches **no real model** and touches no
ledger (the bench never writes one).

Concerns:

1. ``run_bench`` generates N rosters and computes the lexical scores + the
   collision/regen counts; the batch ``persona_lex_mean`` / ``persona_lex_peak``
   are populated (a real 0..1).
2. ``--semantic`` populates ``persona_sem_mean`` / ``persona_sem_peak`` via the
   faked embedder; without it they stay ``None``.
3. ``--diversity on|off`` is honoured — off reproduces the spec-031 path (a
   colliding cast is NOT regenerated; on regenerates).
4. The CLI ``main`` runs end-to-end and prints the summary; the real ledger file
   is untouched (the bench has no ledger path at all).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphia.llm import Persona, Roster
from graphia.tools.persona_bench import BenchSummary, main, run_bench

# Default 5+2 table → 6 AI names. ``fake_small`` requires exactly that count.
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]


def _rosters(n: int) -> list[Roster]:
    """``n`` scripted roster outputs — one per bench roster.

    ``FakeSmall`` is a one-shot-per-entry queue (no replay), and ``generate_roster``
    calls ``get_small`` once per roster, so a multi-roster bench needs ``n`` Roster
    outputs scripted up front. The same 6 names per roster is fine — each roster is
    an independent generation.
    """
    return [Roster(names=list(AI_NAMES)) for _ in range(n)]

# Distinct personas (below the default bar) so a diversity-on run finds no
# residual collisions and makes no regeneration calls.
_DISTINCT_PERSONAS = [
    Persona(
        personality="boisterous and quick to laugh",
        manner="speaks in loud sweeping declarations",
        public_backstory="the village blacksmith with soot on his hands",
        secret_backstory="",
    ),
    Persona(
        personality="withdrawn and watchful, trusting no one",
        manner="murmurs short clipped phrases",
        public_backstory="a reclusive clockmaker in the old tower",
        secret_backstory="",
    ),
    Persona(
        personality="dreamy and forever distracted by birds",
        manner="trails off mid-sentence and hums",
        public_backstory="the herbalist who forages the eastern marsh",
        secret_backstory="",
    ),
]


def test_run_bench_generates_rosters_and_scores_lexically(
    env: Path,
    fake_small,
    fake_large,
) -> None:
    """``run_bench`` produces N scored rosters with a populated lexical mean/peak."""
    fake_small(outputs=_rosters(3))
    # A FIFO of distinct personas; the unified fake replays the last once drained,
    # so every AI seat across every roster gets a (distinct-enough) persona.
    fake_large(personas=_DISTINCT_PERSONAS)

    summary = run_bench(
        provider="bedrock",
        rosters=3,
        diversity_enabled=True,
        semantic=False,
    )

    assert isinstance(summary, BenchSummary)
    assert summary.rosters_attempted == 3
    assert summary.rosters_completed == 3
    # The lexical metrics are computed (a real similarity in 0..1).
    assert summary.persona_lex_mean is not None
    assert 0.0 <= summary.persona_lex_mean <= 1.0
    assert summary.persona_lex_peak is not None
    assert 0.0 <= summary.persona_lex_peak <= 1.0 + 1e-9
    # Semantic omitted without --semantic.
    assert summary.persona_sem_mean is None
    assert summary.persona_sem_peak is None
    # Per-roster results carried.
    assert len(summary.per_roster) == 3
    assert all(r.error is None for r in summary.per_roster)


def test_run_bench_semantic_populates_semantic_metrics(
    env: Path,
    fake_small,
    fake_large,
) -> None:
    """``--semantic`` fills persona_sem_mean/peak via the faked embedder (no Bedrock)."""
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    summary = run_bench(
        provider="bedrock",
        rosters=2,
        diversity_enabled=True,
        semantic=True,
    )

    # The faked embedder is a char-frequency bag, so a replayed-persona cast reads
    # as near-identical (cosine ≈ 1.0); allow a tiny FP slop above 1.0 since the
    # scorer returns the raw cosine (spec-033 contract — the bench does not clamp).
    assert summary.persona_sem_mean is not None
    assert 0.0 <= summary.persona_sem_mean <= 1.0 + 1e-9
    assert summary.persona_sem_peak is not None
    assert 0.0 <= summary.persona_sem_peak <= 1.0 + 1e-9


def test_diversity_off_does_not_regenerate_a_colliding_cast(
    env: Path,
    fake_small,
    fake_large,
) -> None:
    """OFF: a verbatim-duplicate cast ships with residual collisions, no regen.

    The fake returns the SAME persona for every seat (the unified fake replays its
    one scripted output), so with diversity OFF the cast is a pile of verbatim
    duplicates: many residual collisions and ZERO regenerations (no collision
    check runs). This pins the spec-031 A/B baseline the bench exists to compare
    against.
    """
    fake_small(AI_NAMES)
    fake_large(
        personas=[
            Persona(
                personality="calm and observant",
                manner="speaks slowly and plainly",
                public_backstory="the town librarian who reads mystery novels",
                secret_backstory="",
            )
        ]
    )

    summary = run_bench(
        provider="bedrock",
        rosters=1,
        diversity_enabled=False,
        semantic=False,
    )

    assert summary.rosters_completed == 1
    # An all-identical 6-AI cast → C(6,2)=15 colliding pairs, none regenerated.
    assert summary.total_collisions == 15
    assert summary.total_regenerations == 0
    # The lexical peak is a verbatim-copy 1.0.
    assert summary.persona_lex_peak == pytest.approx(1.0)


def test_diversity_on_regenerates_a_colliding_cast(
    env: Path,
    fake_small,
    fake_large,
) -> None:
    """ON: an always-colliding fake drives regeneration attempts (bounded).

    The fake returns the SAME persona for every call, so with diversity ON every
    seat after the first collides and the regen loop exhausts its attempts — the
    cast still ships (least-similar kept = the same persona), and the
    regeneration count is non-zero. This proves the on-path actually runs the
    regen loop (the off-path above proves it does not).
    """
    fake_small(AI_NAMES)
    fake_large(
        personas=[
            Persona(
                personality="calm and observant",
                manner="speaks slowly and plainly",
                public_backstory="the town librarian who reads mystery novels",
                secret_backstory="",
            )
        ]
    )

    summary = run_bench(
        provider="bedrock",
        rosters=1,
        diversity_enabled=True,
        semantic=False,
    )

    assert summary.rosters_completed == 1
    # Diversity-on ran the regen loop: with an always-colliding fake, seats 2..6
    # each exhaust their attempts → strictly more generation calls than the 6
    # one-per-seat baseline.
    assert summary.total_regenerations > 0


def test_main_runs_end_to_end_and_writes_no_ledger(
    env: Path,
    fake_small,
    fake_large,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` runs the bench, prints a summary, returns 0 — and writes no ledger."""
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    # The bench has no ledger path; assert it never even imports/writes one by
    # confirming the blunder-eval ledger module's LEDGER_PATH is not touched.
    from graphia.tools.blunder_eval import LEDGER_PATH

    before = LEDGER_PATH.read_bytes() if LEDGER_PATH.exists() else None

    rc = main(["--provider", "bedrock", "--rosters", "2", "--diversity", "on"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "BATCH SUMMARY" in out
    assert "persona_lex_mean" in out

    after = LEDGER_PATH.read_bytes() if LEDGER_PATH.exists() else None
    assert before == after, "the bench must not touch the blunder-eval ledger"


def test_main_rejects_zero_rosters(env: Path) -> None:
    """``--rosters 0`` is rejected before any generation."""
    rc = main(["--provider", "bedrock", "--rosters", "0"])
    assert rc == 2
