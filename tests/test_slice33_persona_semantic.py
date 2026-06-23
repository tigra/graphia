"""Offline tests for spec 033 Slice 1 — the SEMANTIC (meaning-based) persona metric.

Spec 033 (*Semantic (Meaning-Based) Persona Similarity*), Slice 1, Tests task
(tech-spec §4, *Testing Strategy*). The meaning-based counterpart to the LEXICAL
persona measures (spec 031 near-dup count, spec 032 mean + peak): where those
compare persona *text* word-for-word (``difflib``), this embeds each AI persona's
table-facing text into a meaning vector and takes the **cosine** of each unordered
pair, recording the batch **mean** cosine as the value-type metric
``persona_sem_sim {mean, denominator}`` — so two differently-worded but same-kind
characters read as similar where the lexical measures read them as distinct.

All-mocked — the embeddings boundary is faked, never real Bedrock:

- **Pure scorer** ``score_persona_semantic_sim(players, embed_fn)`` with an
  **injected** fake ``embed_fn`` we fully control: identical vectors → mean cosine
  ≈ 1.0, orthogonal → ≈ 0.0, a known mix → the expected average, ``<2`` AI personas
  → ``{None, 0}``, the human excluded, ``true_self`` never embedded (we capture the
  texts the fake receives and assert no hidden backstory leaks in), name-masking
  applied. The cosines are driven entirely by the fake's *returned* vectors — no
  real embedding values are asserted.

- **Aggregation** (mocked ``run_eval``): the written record carries
  ``persona_sem_sim {mean, denominator}`` (value-type, NO ``ci_low``/``ci_high``);
  ``mean`` = Σ cosines / total pairs across games; a ``<2``-everywhere batch omits
  it; ``metrics_version`` is unchanged. The suite-wide ledger/transcript redirect
  keeps the real ``evals/blunder-ledger.yaml`` / ``evals/transcripts/`` untouched.

- **Graceful degradation**: when the (faked) ``get_embeddings`` raises (an ollama
  run with no AWS creds, an unavailable model), ``run_eval`` OMITS the metric and
  the run still completes — no crash.

- **Viewer**: ``_metric_cell`` / ``render_detail`` render ``persona_sem_sim`` via
  the value-type ``~{mean:.2f} (n=…)`` branch (shared with the lexical mean/peak);
  absent → blank; ``METRIC_ORDER`` carries ``("persona_sem_sim", "persona sem")``.

The autouse ``safe_llm`` net (``tests/conftest.py``) patches
``graphia.tools.blunder_eval.get_embeddings`` with a deterministic char-frequency
fake; the mocked-``run_eval`` tests below lean on that fake (so a flag-on eval gets
a reproducible ``persona_sem_sim`` and never reaches Bedrock), while the pure-scorer
tests bypass it entirely by injecting their own ``embed_fn``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

import pytest

from graphia.eval_ledger import METRIC_ORDER, _metric_cell, render_detail
from graphia.state import PlayerPersona, PlayerState
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    METRICS_VERSION,
    _GameCapture,
    render_record,
    run_eval,
    score_persona_semantic_sim,
)

# The flat key the semantic value-type metric is recorded under in a record's
# ``metrics`` block, and the dotted key + label ``METRIC_ORDER`` registers — the
# single source of truth this file asserts against.
_SEM_KEY = "persona_sem_sim"
_SEM_LABEL = "persona sem"


# ===========================================================================
# Roster builders — hand-built ``PlayerState`` / ``PlayerPersona`` maps, no model.
#
# Mirrors tests/test_slice32_persona_sim.py: the scorer builds table-facing text
# from ``personality`` + ``manner`` + ``public_persona`` only, so ``true_self`` is
# set deliberately (and asserted never to leak into ``embed_fn``).
# ===========================================================================


def _ai(
    pid: str,
    name: str,
    persona: PlayerPersona | None,
    *,
    role: str = "law_abiding",
) -> PlayerState:
    """One AI seat with the given (possibly absent) persona."""
    return PlayerState(
        id=pid, name=name, role=role, is_human=False, persona=persona
    )


def _human(pid: str, name: str) -> PlayerState:
    """The human seat — no persona, by the spec-016 invariant the scorer relies on."""
    return PlayerState(
        id=pid, name=name, role="law_abiding", is_human=True, persona=None
    )


def _persona(personality: str, manner: str, public_persona: str, true_self: str = "") -> PlayerPersona:
    """A ``PlayerPersona`` — the table-facing fields plus the hidden ``true_self``."""
    return PlayerPersona(
        personality=personality,
        manner=manner,
        public_persona=public_persona,
        true_self=true_self,
    )


# Four personas with clearly different wording in every table-facing field. Their
# wording does NOT drive the cosines below — the cosines come entirely from the
# fake embedder's RETURNED vectors. These exist only to populate distinct seats.
_PERSONA_A = _persona(
    "boisterous and quick to laugh",
    "speaks in loud sweeping declarations",
    "the village blacksmith with soot on his hands",
)
_PERSONA_B = _persona(
    "meticulous, reserved, slow to trust",
    "weighs each word and pauses before answering",
    "a retired schoolteacher who keeps a tidy ledger",
)
_PERSONA_C = _persona(
    "warm, gossipy, endlessly curious about neighbours",
    "rambles cheerfully and circles back to old stories",
    "the baker whose ovens scent the whole square at dawn",
)


# ===========================================================================
# Injected fake embedders — the cosines are driven by these RETURNED vectors, NOT
# by any real embedding model. Each is a plain callable matching the
# ``embed_fn(texts) -> list[vector]`` contract the scorer calls once per game.
# ===========================================================================


class _CapturingEmbedder:
    """An ``embed_fn`` returning preset vectors and recording the texts it received.

    Constructed with the exact list of vectors to return (one per persona, in the
    order the scorer passes them) so a test fully controls the resulting cosines.
    The ``texts`` attribute captures the (single) batch of texts the scorer handed
    over, so a test can assert the embedded text was name-masked and excluded
    ``true_self`` — the scorer calls ``embed_fn`` exactly ONCE per game (a batch),
    so ``calls`` should be 1.
    """

    def __init__(self, vectors: list[Sequence[float]]) -> None:
        self._vectors = vectors
        self.texts: list[str] | None = None
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[Sequence[float]]:
        self.calls += 1
        self.texts = list(texts)
        # One vector per text — the scorer expects ``len(vectors) == len(texts)``.
        assert len(self._vectors) == len(texts), (
            "test fake must supply exactly one vector per persona text"
        )
        return self._vectors


# ===========================================================================
# 1. Pure scorer — score_persona_semantic_sim (injected fake embed_fn)
# ===========================================================================


def test_identical_vectors_yield_mean_cosine_of_one() -> None:
    """Two personas embedded to IDENTICAL vectors → mean cosine == 1.0 over C(2,2)=1.

    The cosine of a vector with itself is 1.0; with one pair the mean is that lone
    cosine. The personas' *wording* differs, but the fake returns the same vector
    for both, so the meaning-based measure rates them maximally alike — the core
    spec-033 case (differently-worded, same-kind → similar).
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "p-2": _ai("p-2", "Bram", _PERSONA_B),
    }
    embed = _CapturingEmbedder([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])

    facets = score_persona_semantic_sim(players, embed)

    assert facets["denominator"] == 1  # C(2, 2)
    assert facets["mean"] == pytest.approx(1.0)


def test_orthogonal_vectors_yield_mean_cosine_of_zero() -> None:
    """Two personas embedded to ORTHOGONAL vectors → mean cosine ≈ 0.0.

    Orthogonal vectors have a zero dot product, so cosine 0.0 — the lone pair's
    cosine is the mean. The meaning-based measure rates two maximally-different
    characters as unrelated, the opposite extreme from identical.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "p-2": _ai("p-2", "Bram", _PERSONA_B),
    }
    embed = _CapturingEmbedder([[1.0, 0.0], [0.0, 1.0]])

    facets = score_persona_semantic_sim(players, embed)

    assert facets["denominator"] == 1
    assert facets["mean"] == pytest.approx(0.0)


def test_known_mix_yields_the_expected_average_cosine() -> None:
    """A three-persona roster with hand-chosen vectors → the expected mean cosine.

    Three personas over C(3,2)=3 pairs with vectors we control: two identical
    (cosine 1.0), one orthogonal to both (cosine 0.0 with each). So the three
    pairwise cosines are {1.0, 0.0, 0.0} and the mean is 1/3 — recomputed
    independently here, asserting the scorer averages the cosines over the pair
    count rather than e.g. summing or maxing.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "p-2": _ai("p-2", "Bram", _PERSONA_B),
        "p-3": _ai("p-3", "Cleo", _PERSONA_C),
    }
    # v1 == v2 (cos 1.0); v3 orthogonal to both (cos 0.0 each).
    embed = _CapturingEmbedder([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])

    facets = score_persona_semantic_sim(players, embed)

    assert facets["denominator"] == 3  # C(3, 2)
    # cosines: (v1,v2)=1.0, (v1,v3)=0.0, (v2,v3)=0.0 → mean = 1/3.
    assert facets["mean"] == pytest.approx(1.0 / 3.0)


def test_single_ai_persona_has_no_pairs_returns_none_mean() -> None:
    """One AI persona (+ a human) offers no pair → ``{"mean": None, "denominator": 0}``.

    Fewer than two AI personas means no pair to compare — the not-applicable case
    (functional-spec §2: reported blank, not a misleading zero). The scorer returns
    ``mean is None`` so ``run_eval`` OMITS the metric. The embedder is never even
    invoked (no pair to embed for).
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "h": _human("h", "Human"),
    }
    embed = _CapturingEmbedder([])  # zero personas to embed — never called

    facets = score_persona_semantic_sim(players, embed)

    assert facets == {"mean": None, "denominator": 0}
    assert embed.calls == 0  # no pair → no batch embed call


def test_zero_ai_personas_returns_none_mean() -> None:
    """No AI persona at all (only a personaless human) → ``{"mean": None, "denominator": 0}``."""
    players = {"h": _human("h", "Human")}
    embed = _CapturingEmbedder([])

    facets = score_persona_semantic_sim(players, embed)

    assert facets == {"mean": None, "denominator": 0}
    assert embed.calls == 0


def test_human_is_excluded_from_the_embedded_set() -> None:
    """A human seat never contributes a persona — only the two AI personas embed.

    Three seats (two AI, one human) yield C(2,2)=1 pair, not C(3,2)=3: the human is
    skipped, never embedded as a third persona — the same spec-031/032 exclusion. We
    also assert the embedder received exactly TWO texts (the two AI personas), never
    a third for the human.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "p-2": _ai("p-2", "Bram", _PERSONA_B),
        "h": _human("h", "Human"),
    }
    embed = _CapturingEmbedder([[1.0, 0.0], [1.0, 0.0]])

    facets = score_persona_semantic_sim(players, embed)

    assert facets["denominator"] == 1  # C(2, 2) — only the two AI personas pair
    assert embed.calls == 1
    assert embed.texts is not None and len(embed.texts) == 2  # never the human


def test_persona_with_none_is_skipped_like_the_human() -> None:
    """An AI seat whose persona is None is skipped — only personaed AI pairs embed.

    Mirrors the human-exclusion path for the other skip condition the scorer applies
    (``p.persona is not None``): a fallback that never populated a persona must not
    enter the embedded set as a (blank) persona. Three AI seats, one personaless →
    C(2,2)=1 pair, two embedded texts.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "p-2": _ai("p-2", "Bram", _PERSONA_B),
        "p-3": _ai("p-3", "Cleo", None),  # AI, but no persona yet
    }
    embed = _CapturingEmbedder([[1.0, 0.0], [1.0, 0.0]])

    facets = score_persona_semantic_sim(players, embed)

    assert facets["denominator"] == 1  # only the two personaed AI seats pair
    assert embed.texts is not None and len(embed.texts) == 2


def test_true_self_is_never_embedded() -> None:
    """A Mafioso's hidden ``true_self`` text never appears in what ``embed_fn`` receives.

    The scorer builds embed text from ``personality``/``manner``/``public_persona``
    only. We hand a Mafioso a vivid, unmistakable hidden ``true_self`` token and
    assert that token appears in NONE of the captured texts — the spec-016 /
    allegiance-hiding invariant enforced at the embedding boundary, not merely the
    cosine (we capture and inspect exactly what was sent to the model).
    """
    secret_token = "zzqxwellpoisonerzzqx"  # an unmistakable hidden-only marker
    mafioso = _persona(
        "calm and observant",
        "speaks plainly",
        "a steady orchard-keeper",
        true_self=f"secretly the ringleader, the {secret_token} of the town",
    )
    citizen = _persona(
        "warm and chatty",
        "rambles cheerfully",
        "the baker at the square",
        true_self="",
    )
    players = {
        "p-1": _ai("p-1", "Ada", mafioso, role="mafia"),
        "p-2": _ai("p-2", "Bram", citizen),
    }
    embed = _CapturingEmbedder([[1.0, 0.0], [0.0, 1.0]])

    score_persona_semantic_sim(players, embed)

    assert embed.texts is not None
    for text in embed.texts:
        assert secret_token not in text, (
            "true_self must NEVER be embedded — the hidden backstory token leaked "
            "into the text passed to embed_fn"
        )


def test_name_masking_is_applied_to_the_embedded_text() -> None:
    """Each persona's own name is masked in the text handed to ``embed_fn``.

    The scorer name-masks the table-facing text (``_spec009_mask_names`` against the
    AI names) before embedding, so a shared/own name token can't drive the cosine.
    Each persona carries its own player name verbatim in ``public_persona``; after
    masking, neither raw name should survive into the captured embed texts (they
    collapse to the spec-009 placeholder), proving the mask runs at the embedding
    boundary.
    """
    persona_ada = _persona(
        "calm and observant",
        "speaks plainly and listens",
        "Ada, who tends the orchard with a steady hand",
    )
    persona_bram = _persona(
        "calm and observant",
        "speaks plainly and listens",
        "Bram, who tends the orchard with a steady hand",
    )
    players = {
        "p-1": _ai("p-1", "Ada", persona_ada),
        "p-2": _ai("p-2", "Bram", persona_bram),
    }
    embed = _CapturingEmbedder([[1.0, 0.0], [1.0, 0.0]])

    score_persona_semantic_sim(players, embed)

    assert embed.texts is not None and len(embed.texts) == 2
    joined = " ".join(embed.texts).lower()
    # The raw player names are masked out of the embedded text (case-insensitive,
    # since the masker normalizes) — they never reach the embedding model verbatim.
    assert "ada" not in joined and "bram" not in joined, (
        "embedded self-names should be masked to the spec-009 placeholder before "
        "embedding, so no raw name token reaches embed_fn"
    )


def test_scorer_is_reproducible_for_the_same_vectors() -> None:
    """The same personas + the same (deterministic) embedder → the same mean twice.

    Embeddings are deterministic (same text + model → same vector), so the metric is
    reproducible — not a varying free-form judgment (functional-spec §2). Driving the
    scorer twice with a fresh deterministic embedder yields the identical mean.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _PERSONA_A),
        "p-2": _ai("p-2", "Bram", _PERSONA_B),
        "p-3": _ai("p-3", "Cleo", _PERSONA_C),
    }
    vectors: list[Sequence[float]] = [[2.0, 1.0], [1.0, 0.0], [0.0, 3.0]]

    first = score_persona_semantic_sim(players, _CapturingEmbedder(list(vectors)))
    second = score_persona_semantic_sim(players, _CapturingEmbedder(list(vectors)))

    assert first["denominator"] == second["denominator"] == 3
    assert first["mean"] == pytest.approx(second["mean"])


# ===========================================================================
# 2. Aggregation — mocked run_eval (no graph, no provider, temp ledger)
#
# Mirrors tests/test_slice32_persona_sim.py's storage/ledger-link pattern exactly:
# stub the provenance collectors + the model-name resolver, redirect ``LEDGER_PATH``
# to a temp file, stub ``_play_one_game`` to return hand-built ``_GameCapture``s
# (one per game), and inject a ``transcripts_root`` + pinned ``run_id`` under
# ``tmp_path``. The real ledger and the real ``evals/transcripts/`` are never
# written. The autouse ``safe_llm`` fake (``_FakeEmbeddings`` — a deterministic
# char-frequency embedder) supplies a reproducible ``embed_fn`` here, so the batch
# mean = Σ cosines / total pairs ACROSS games is deterministic and never Bedrock.
# ===========================================================================


def _capture_with_personas(personas: list[PlayerPersona]) -> _GameCapture:
    """A ``_GameCapture`` whose final roster carries the given AI personas.

    All other inputs are empty/minimal — the persona scorers read only
    ``cap.players`` — so the run scores ``persona_sem_sim`` over exactly this roster
    with no graph, model, or messages. A non-empty ``events`` log lets
    ``render_transcript`` produce a real document (the transcript write happens
    against the injected ``transcripts_root``).
    """
    players: dict[str, PlayerState] = {}
    for i, persona in enumerate(personas, start=1):
        pid = f"p-{i}"
        players[pid] = _ai(pid, f"AI{i}", persona)
    events: list[dict[str, Any]] = [
        {"night_open": {"night_round_picks": {}, "night_rounds_log": []}},
    ]
    return _GameCapture(
        ai_lines=[],
        ai_names={p.name for p in players.values()},
        ai_lines_with_speakers=[],
        players=players,
        messages=[],
        captures=[],
        winner="law_abiding",
        events=events,
    )


def _storage_args(games: int) -> argparse.Namespace:
    """The ``argparse.Namespace`` ``run_eval`` reads — a bedrock, no-seed run."""
    return argparse.Namespace(
        provider="bedrock",
        games=games,
        seed=None,
        max_days=None,
        note="",
    )


def _stub_run_eval_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[object, Path]:
    """Stub provenance + redirect the ledger, returning (config, ledger_path).

    The provenance collectors are stubbed to degraded values (no git / no HTTP),
    the model-name resolver returns a fixed pair, and ``LEDGER_PATH`` is redirected
    to a temp file — so neither the real ledger nor any real provenance source is
    touched. The returned config is a bare stub (``run_eval`` reads only
    ``ollama_base_url`` / ``num_citizens`` / ``num_mafia`` defensively).
    """
    monkeypatch.setattr(
        blunder_eval,
        "collect_code_provenance",
        lambda root: {"commit": None, "branch": None, "dirty": False},
    )
    monkeypatch.setattr(
        blunder_eval,
        "collect_provider_provenance",
        lambda provider, large, small, base: {
            "name": provider,
            "large_model": large,
            "small_model": small,
        },
    )
    monkeypatch.setattr(
        blunder_eval,
        "_resolved_model_names",
        lambda config: ("nova-pro", "nova-lite"),
    )
    ledger = tmp_path / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)

    class _Cfg:
        ollama_base_url = "http://localhost:11434"
        num_citizens = 5
        num_mafia = 2

    return _Cfg(), ledger


def _run_eval_over_games(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rosters: list[list[PlayerPersona]],
    run_id: str = "2026-06-19T00-00-00",
):
    """Drive a mocked ``run_eval`` over one ``_GameCapture`` per roster in ``rosters``.

    Returns the populated ``EvalResult``. ``_play_one_game`` is stubbed to serve the
    ``game_index``-th roster (no graph / no provider); transcripts land under
    ``tmp_path``. The number of games == ``len(rosters)``. The embeddings boundary is
    the autouse ``safe_llm`` deterministic fake (never Bedrock).
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    captures = [_capture_with_personas(roster) for roster in rosters]
    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: captures[game_index],
    )
    return run_eval(
        config,
        _storage_args(games=len(rosters)),
        transcripts_root=tmp_path / "transcripts",
        run_id=run_id,
    )


def _expected_game_mean(personas: list[PlayerPersona]) -> dict[str, float | int | None]:
    """Recompute a game's ``persona_sem_sim`` facets through the same fake embedder.

    Uses the SAME deterministic embedder the autouse ``safe_llm`` installs
    (``conftest._FakeEmbeddings().embed_documents``), so the expected per-game mean
    and pair count match exactly what ``run_eval`` accumulated — the aggregation
    contract (Σ cosines / total pairs) without re-deriving cosines by hand.
    """
    players = {
        f"p-{i}": _ai(f"p-{i}", f"AI{i}", p)
        for i, p in enumerate(personas, start=1)
    }
    embed = blunder_eval.get_embeddings().embed_documents
    return score_persona_semantic_sim(players, embed)


def test_run_eval_records_persona_sem_sim_value_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mocked run records ``persona_sem_sim`` as a ``{mean, denominator}`` value shape.

    A single game with three AI personas → C(3,2)=3 pairs. The metric lands in
    ``result.metrics`` with the value-type shape (``mean``/``denominator``) and NO
    ``rate``/``count`` — the same facet shape as the lexical ``persona_mean_sim``.
    """
    roster = [_PERSONA_A, _PERSONA_B, _PERSONA_C]
    result = _run_eval_over_games(monkeypatch, tmp_path, [roster])

    assert _SEM_KEY in result.metrics
    facets = result.metrics[_SEM_KEY]
    assert set(facets) == {"mean", "denominator"}
    assert facets["denominator"] == 3  # C(3, 2), one game
    assert "rate" not in facets and "count" not in facets


def test_aggregation_mean_is_sum_of_cosines_over_total_pairs_across_games(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """mean == Σ per-game cosines / Σ per-game pairs, summed ACROSS games.

    Two games with different rosters: the batch mean must be the total cosine sum
    over the total pair count (a true pair-weighted mean, never a mean-of-means). We
    recompute the expected per-game facets through the SAME fake embedder and assert
    the recorded ``mean``/``denominator`` match the across-games aggregation.
    """
    game1 = [_PERSONA_A, _PERSONA_B, _PERSONA_C]  # C(3,2)=3 pairs
    game2 = [_PERSONA_A, _PERSONA_B]  # C(2,2)=1 pair

    result = _run_eval_over_games(monkeypatch, tmp_path, [game1, game2])

    f1 = _expected_game_mean(game1)
    f2 = _expected_game_mean(game2)
    # Recover each game's cosine SUM (mean * pairs) and fold into the batch mean.
    total_cos = float(f1["mean"]) * f1["denominator"] + float(f2["mean"]) * f2["denominator"]
    total_pairs = f1["denominator"] + f2["denominator"]

    sem_facets = result.metrics[_SEM_KEY]
    assert sem_facets["denominator"] == total_pairs == 4  # 3 + 1
    assert sem_facets["mean"] == pytest.approx(total_cos / total_pairs)


def test_fewer_than_two_personas_everywhere_omits_the_metric(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A batch where no game ever had ≥2 AI personas omits ``persona_sem_sim``.

    Every game offers no pair (the scorer returns ``mean is None`` everywhere → total
    pairs 0), so the opportunity gate never fires — the not-applicable case is
    reported by OMISSION, not a misleading zero (functional-spec §2, mirroring the
    lexical persona metrics).
    """
    single = [_PERSONA_A]  # one AI persona → no pair

    result = _run_eval_over_games(monkeypatch, tmp_path, [single, single])

    assert _SEM_KEY not in result.metrics


def test_persona_sem_sim_carries_no_wilson_ci_band(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The value metric gets no ``ci_low``/``ci_high`` — a cosine mean is not a rate.

    ``_attach_ci`` keys off ``count``; the value-type facet carries no ``count``, so
    the Wilson-band attachment is skipped (tech-spec §2 C). A Wilson interval is a
    proportion's reliability band — meaningless for a continuous cosine mean.
    """
    roster = [_PERSONA_A, _PERSONA_B, _PERSONA_C]
    result = _run_eval_over_games(monkeypatch, tmp_path, [roster])

    facets = result.metrics[_SEM_KEY]
    assert "ci_low" not in facets
    assert "ci_high" not in facets


def test_metrics_version_is_unchanged_by_the_semantic_metric(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The additive semantic metric does NOT bump ``METRICS_VERSION`` (tech-spec §2).

    A new value metric is additive — old records simply lack the key — so the version
    stays put (the ``persona_near_dup`` / ``persona_mean_sim`` precedent). The constant
    is 1, and the rendered record stamps that same value.
    """
    assert METRICS_VERSION == 1

    result = _run_eval_over_games(monkeypatch, tmp_path, [[_PERSONA_A, _PERSONA_B]])
    doc = render_record(result, "2026-06-19")

    assert f"  metrics_version: {METRICS_VERSION}" in doc


def test_run_eval_does_not_touch_the_real_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The run writes only the redirected temp ledger; the real one is untouched.

    Belt-and-braces over the ``LEDGER_PATH`` redirect: the temp ledger gains the
    run's record (carrying ``persona_sem_sim``), while the repo's real
    ``evals/blunder-ledger.yaml`` is neither created here nor modified — the redirect
    in ``_stub_run_eval_env`` is what keeps the committed ledger safe (the suite-wide
    redirect spec 031/032 reuse).
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    real_ledger = blunder_eval.LEDGER_PATH
    # The redirect must point away from the repo's committed ledger.
    assert real_ledger == ledger
    assert ledger.parent == tmp_path

    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: _capture_with_personas([_PERSONA_A, _PERSONA_B]),
    )
    run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=tmp_path / "transcripts",
        run_id="2026-06-19T00-00-01",
    )

    assert ledger.exists()
    assert _SEM_KEY in ledger.read_text(encoding="utf-8")


# ===========================================================================
# 3. Graceful degradation — embeddings unavailable → metric omitted, run completes
#
# The metric is model-dependent and ALWAYS Bedrock; an ollama run with no AWS creds
# (or an unavailable embeddings model / constructor error) must NOT crash the
# measured run. ``run_eval`` resolves ``embed_fn = get_embeddings().embed_documents``
# inside a try/except — on any failure ``embed_fn`` stays None, every game's
# semantic scoring is skipped, and the metric is OMITTED. We drive this by patching
# the module-level ``blunder_eval.get_embeddings`` binding to one that raises (the
# same binding the autouse ``safe_llm`` patches, re-patched here AFTER it runs).
# ===========================================================================


def test_embeddings_unavailable_omits_metric_and_run_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A raising ``get_embeddings`` → ``persona_sem_sim`` omitted, the run still completes.

    Patch the ``get_embeddings`` factory to raise at construction (the no-AWS-creds /
    unavailable-model path). ``run_eval`` must catch it, leave ``embed_fn`` None, omit
    ``persona_sem_sim``, and complete the batch normally — the LEXICAL persona metrics
    (which need no model) still record, proving only the semantic metric degraded.
    """

    def _raising_get_embeddings():
        raise RuntimeError("no AWS credentials — embeddings unavailable")

    monkeypatch.setattr(
        blunder_eval, "get_embeddings", _raising_get_embeddings
    )

    # A roster with ≥2 AI personas: the LEXICAL metrics WILL record (no model
    # needed), so their presence proves the run completed and only the SEMANTIC
    # metric was dropped.
    roster = [_PERSONA_A, _PERSONA_B, _PERSONA_C]
    result = _run_eval_over_games(monkeypatch, tmp_path, [roster])

    # The semantic metric is omitted — no crash.
    assert _SEM_KEY not in result.metrics
    # ...while the model-free lexical companion still recorded (the run completed).
    assert "persona_mean_sim" in result.metrics
    assert result.games_completed == 1


def test_embeddings_embed_call_failure_omits_metric_and_run_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A ``get_embeddings()`` that builds but whose ``embed_documents`` raises degrades.

    A subtler failure than a constructor error: the client constructs, but the batch
    embed call throws per game (a throttling / bad-response path). ``run_eval`` wraps
    each game's semantic scoring in try/except, skips that game's contribution, and —
    since no game contributed a pair — omits the metric. The run still completes.
    """

    class _BuildsButEmbedRaises:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("Bedrock throttling — embed call failed")

    monkeypatch.setattr(
        blunder_eval, "get_embeddings", lambda: _BuildsButEmbedRaises()
    )

    roster = [_PERSONA_A, _PERSONA_B, _PERSONA_C]
    result = _run_eval_over_games(monkeypatch, tmp_path, [roster])

    assert _SEM_KEY not in result.metrics
    assert result.games_completed == 1  # run completed despite the embed failure


# ===========================================================================
# 4. Viewer — _metric_cell / render_detail value-type rendering + METRIC_ORDER
#
# Mirrors tests/test_slice32_persona_sim.py's viewer-cell assertions: hand-built
# dict-shaped records (the on-disk ``metrics`` shape ``eval_ledger`` reads), no
# parsing. ``persona_sem_sim`` reuses spec 032's value-type ``~{mean:.2f} (n=…)``
# branch (no new render code); an absent metric renders blank.
# ===========================================================================


def test_metric_order_carries_the_semantic_entry() -> None:
    """``METRIC_ORDER`` registers ``("persona_sem_sim", "persona sem")``, after the lexical ones.

    The viewer derives its columns + detail lines from this tuple, so the label and
    its order are the single source of truth: ``persona sem`` follows the spec-032
    ``persona max`` (peak) entry.
    """
    assert (_SEM_KEY, _SEM_LABEL) in METRIC_ORDER

    keys = [key for key, _ in METRIC_ORDER]
    peak_i = keys.index("persona_peak_sim")
    sem_i = keys.index(_SEM_KEY)
    assert sem_i == peak_i + 1  # contiguous, right after the lexical peak


def test_metric_cell_renders_semantic_mean_as_value_type_form() -> None:
    """``_metric_cell`` renders ``persona_sem_sim`` as ``~<mean> (n=<pairs>)``.

    The value-type branch (shared with the lexical mean/peak): a facet with
    ``mean``/``denominator`` and no ``rate`` renders with the ``~`` similarity prefix
    and the ``n=`` pair count, two-decimal — distinct from the rate form.
    """
    record = {"metrics": {_SEM_KEY: {"mean": 0.7361, "denominator": 10}}}

    assert _metric_cell(record, _SEM_KEY) == "~0.74 (n=10)"


def test_metric_cell_absent_semantic_metric_is_blank() -> None:
    """A record lacking ``persona_sem_sim`` renders the empty string (blank, not zero).

    A run with no semantic metric (an ollama run with no creds, or a pre-033 record)
    renders blank in that column — visibly distinct from a real value — without error.
    """
    record = {"metrics": {"repetition": {"rate": 0.5, "count": 10, "denominator": 20}}}

    assert _metric_cell(record, _SEM_KEY) == ""


def test_metric_cell_semantic_value_carries_no_ci_band() -> None:
    """A value-type cell never renders a ``[lo–hi]`` band, even if CI keys leaked in.

    The cell format is ``~<value> (n=…)`` — no Wilson band. Defensive: even a
    malformed facet carrying stray ``ci_*`` keys (which the writer never emits for a
    value metric) must still render the bare value form, never a bracketed band.
    """
    record = {
        "metrics": {
            _SEM_KEY: {"mean": 0.5, "denominator": 6, "ci_low": 0.1, "ci_high": 0.9}
        }
    }

    cell = _metric_cell(record, _SEM_KEY)
    assert cell == "~0.50 (n=6)"
    assert "[" not in cell and "]" not in cell


def test_render_detail_shows_semantic_metric_with_its_label() -> None:
    """``render_detail`` lists ``persona_sem_sim`` under its ``persona sem`` label.

    The detail view iterates ``METRIC_ORDER``; a record carrying the semantic metric
    shows ``persona sem: ~<mean> (n=…)`` on its own labelled line (full precision, no
    ``—`` placeholder).
    """
    record = {"metrics": {_SEM_KEY: {"mean": 0.42, "denominator": 10}}}

    detail = render_detail(record)

    sem_line = next(
        line for line in detail.splitlines() if f"{_SEM_LABEL}:" in line
    )
    assert sem_line.strip() == f"{_SEM_LABEL}: ~0.42 (n=10)"
    assert "—" not in sem_line  # present, not the absent-metric em-dash


def test_render_detail_absent_semantic_metric_shows_em_dash() -> None:
    """A record lacking the semantic metric shows ``—`` on the ``persona sem`` line.

    The absent-metric placeholder (distinct from the table cell's blank): a pre-033
    record renders ``persona sem: —`` so a never-recorded value stays visibly
    distinct from a real one — and the surrounding present metric still renders.
    """
    record = {"metrics": {"repetition": {"rate": 0.5, "count": 10, "denominator": 20}}}

    detail = render_detail(record)

    assert f"{_SEM_LABEL}: —" in detail
    assert "repetition:" in detail  # the present metric still renders alongside
