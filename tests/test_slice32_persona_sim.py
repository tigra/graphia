"""Offline tests for spec 032 Slice 1 — the continuous persona-similarity metrics.

Spec 032 (*Continuous Persona-Similarity Metrics (Average + Peak)*), Slice 1,
Tests task (tech-spec §4, *Testing Strategy*). The direct follow-up to spec 031
(*Distinct AI Personas Across the Roster*): spec 031's persona-distinctiveness
measure is a near-duplicate **count** that sits at zero on virtually every run;
032 adds two **continuous companions** over the same masked-text + pairwise
machinery — the **average** pairwise similarity (``persona_mean_sim``, how alike
the cast is overall) and the **peak** most-similar-pair similarity
(``persona_peak_sim``, the closest pair — flagging a collapse the average
smooths over).

All-mocked, no model, no RNG (architecture §6). The pure scorer needs neither;
the ``run_eval`` integration test stubs ``_play_one_game`` (so no graph or
provider is built) and redirects both the ledger path and the transcripts dir
into ``tmp_path`` — the real ``evals/blunder-ledger.yaml`` / ``evals/transcripts/``
are never touched (the same suite-wide redirect ``test_slice31_persona_distinct.py``
and ``test_blunder_eval.py`` use). The autouse ``safe_llm`` net
(``tests/conftest.py``) is left intact; these tests never reach an LLM call site.

Three concerns:

1. **The pure scorer** ``score_persona_sim_sum`` over hand-built ``players``
   maps — a clearly-distinct roster (low ``sim_sum`` / low ``sim_max``), a
   **collapsed/identical pair** (``sim_max == 1.0`` while the average
   ``sim_sum/denominator`` stays low — the case the peak exists to catch),
   a ``<2``-AI-persona roster (``{sim_sum 0.0, sim_max 0.0, denominator 0}``),
   the **human excluded**, ``true_self`` **never** participating, and
   **name-masking** confirmed.

2. **Aggregation** (mocked ``run_eval``) — the written record carries
   ``persona_mean_sim {mean, denominator}`` where ``mean`` = Σ ratios / total
   pairs across games, and ``persona_peak_sim {peak, denominator}`` where
   ``peak`` = max ``sim_max`` across games; a ``<2``-everywhere batch omits both;
   **neither** carries ``ci_low``/``ci_high`` (a mean/peak is not a binomial
   proportion); ``metrics_version`` is unchanged.

3. **Viewer** — ``_metric_cell`` and ``render_detail`` render both metrics as the
   value-type ``~{value:.2f} (n=…)`` form; an absent metric renders blank; and
   ``METRIC_ORDER`` carries both new entries, in order, after ``persona_near_dup``.

The scorer's contract (tech-spec §2, *Component A*) mirrors
``score_persona_near_dup`` EXACTLY for its inputs: over the AI players (human
skipped, ``persona is None`` skipped), build table-facing text
``personality + " " + manner + " " + public_persona`` (never ``true_self``),
mask + normalise via the spec-009 helpers, walk the unordered ``C(n, 2)`` pairs —
but instead of thresholding it returns ``sim_sum`` (Σ of all difflib ratios),
``sim_max`` (the max ratio), and ``denominator`` (the pair count).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from graphia.eval_ledger import METRIC_ORDER, _metric_cell, render_detail
from graphia.state import PlayerPersona, PlayerState
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    METRICS_VERSION,
    _GameCapture,
    render_record,
    run_eval,
    score_persona_sim_sum,
)

# The flat keys the two value-type metrics are recorded under in a record's
# ``metrics`` block (tech-spec §2 B) — the single source of truth this file
# asserts against, and the dotted keys ``METRIC_ORDER`` registers.
_MEAN_KEY = "persona_mean_sim"
_PEAK_KEY = "persona_peak_sim"

# The near-dup threshold spec 031's COUNT uses (difflib ratio). The distinct
# roster below is built so every pairwise ratio sits well under this — so the
# spec-031 count would read zero, the very case these continuous companions
# exist to make legible.
_NEAR_DUP_THRESHOLD = blunder_eval._NEAR_DUP_THRESHOLD


# ===========================================================================
# Roster builders — hand-built ``PlayerState`` / ``PlayerPersona`` maps, no model.
#
# Mirrors ``tests/test_slice31_persona_distinct.py``: the scorer builds
# table-facing text from ``personality`` + ``manner`` + ``public_persona`` only,
# so ``true_self`` is set deliberately (and asserted never to leak in).
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


# Four personas with clearly different wording in every table-facing field — every
# pairwise difflib ratio sits far below the 0.85 near-dup threshold.
_DISTINCT_A = PlayerPersona(
    personality="boisterous and quick to laugh",
    manner="speaks in loud sweeping declarations",
    public_persona="the village blacksmith with soot on his hands",
    true_self="",
)
_DISTINCT_B = PlayerPersona(
    personality="meticulous, reserved, slow to trust",
    manner="weighs each word and pauses before answering",
    public_persona="a retired schoolteacher who keeps a tidy ledger",
    true_self="",
)
_DISTINCT_C = PlayerPersona(
    personality="warm, gossipy, endlessly curious about neighbours",
    manner="rambles cheerfully and circles back to old stories",
    public_persona="the baker whose ovens scent the whole square at dawn",
    true_self="",
)
_DISTINCT_D = PlayerPersona(
    personality="grim and taciturn, haunted by old wars",
    manner="answers in clipped one-word grunts",
    public_persona="a scarred mercenary turned night watchman",
    true_self="",
)

# Two personas whose table-facing text is character-for-character identical — a
# guaranteed COLLAPSED pair (difflib ratio 1.0).
_CLONE = PlayerPersona(
    personality="calm and observant",
    manner="speaks plainly and listens more than talks",
    public_persona="a steady hand who tends the orchard",
    true_self="",
)


# ===========================================================================
# 1. Pure scorer — score_persona_sim_sum
# ===========================================================================


def test_distinct_roster_scores_low_sum_and_low_max() -> None:
    """Four clearly-different personas → low sim_sum, low sim_max over C(4,2)=6.

    The continuous signal the near-dup COUNT cannot give: no pair clears the 0.85
    threshold (the count would read 0), yet ``sim_max`` is a real, graded value
    well below 1.0 — telling a reviewer *how close* the closest pair got.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _DISTINCT_A),
        "p-2": _ai("p-2", "Bram", _DISTINCT_B),
        "p-3": _ai("p-3", "Cleo", _DISTINCT_C),
        "p-4": _ai("p-4", "Dane", _DISTINCT_D),
    }

    facets = score_persona_sim_sum(players)

    assert facets["denominator"] == 6  # C(4, 2)
    # No pair is near-identical: the peak stays well below the near-dup threshold.
    assert facets["sim_max"] < _NEAR_DUP_THRESHOLD
    # The average (sum / pairs) is a low continuous value, distinctly below 1.0.
    mean = facets["sim_sum"] / facets["denominator"]
    assert 0.0 < mean < 0.5
    # The sum cannot exceed the pair count (each ratio is in [0, 1]).
    assert 0.0 < facets["sim_sum"] < facets["denominator"]


def test_collapsed_pair_peaks_at_one_while_mean_stays_low() -> None:
    """A single collapsed pair in a varied roster → sim_max == 1.0, low mean.

    THE case the peak exists to catch (functional-spec §1): four distinct personas
    plus one byte-identical CLONE of the first. Exactly one of the C(5,2)=10 pairs
    is identical, so ``sim_max`` reaches the top of its range (1.0) and flags the
    collapse, while the average — diluted across the nine genuinely-different pairs
    — stays low. The peak surfaces what the average smooths over.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _DISTINCT_A),
        "p-2": _ai("p-2", "Bram", _DISTINCT_B),
        "p-3": _ai("p-3", "Cleo", _DISTINCT_C),
        "p-4": _ai("p-4", "Dane", _DISTINCT_D),
        "p-5": _ai("p-5", "Echo", _DISTINCT_A),  # byte-identical clone of Ada
    }

    facets = score_persona_sim_sum(players)

    assert facets["denominator"] == 10  # C(5, 2)
    # The peak flags the collapsed pair: the closest pair is identical.
    assert facets["sim_max"] == 1.0
    # ...yet the average stays low — the collapse does NOT dominate the mean.
    mean = facets["sim_sum"] / facets["denominator"]
    assert mean < 0.5
    # The whole point: the peak is strictly, dramatically above the average.
    assert facets["sim_max"] > mean


def test_two_identical_personas_peak_and_mean_both_one() -> None:
    """A roster that is JUST one identical pair → both sim_sum and sim_max == 1.0.

    The degenerate single-pair collapse: C(2,2)=1 pair, identical, so the lone
    ratio is 1.0 — the sum, the max, and (since denominator is 1) the mean all
    pin to 1.0. The opposite extreme from the distinct roster.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
    }

    facets = score_persona_sim_sum(players)

    assert facets["denominator"] == 1  # C(2, 2)
    assert facets["sim_sum"] == 1.0
    assert facets["sim_max"] == 1.0


def test_single_ai_persona_has_no_pairs_zero_denominator() -> None:
    """One AI persona (+ a human) offers no pairs → {sim_sum 0.0, sim_max 0.0, denom 0}.

    Fewer than two AI personas means no pair to compare — the not-applicable case
    (functional-spec §2: reported blank, not a misleading zero). The scorer returns
    the explicit all-zero shape so ``run_eval`` omits both metrics.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _DISTINCT_A),
        "h": _human("h", "Human"),
    }

    facets = score_persona_sim_sum(players)

    assert facets == {"sim_sum": 0.0, "sim_max": 0.0, "denominator": 0}


def test_zero_ai_personas_has_no_pairs_zero_denominator() -> None:
    """No AI persona at all (only a personaless human) → the all-zero shape."""
    players = {"h": _human("h", "Human")}

    facets = score_persona_sim_sum(players)

    assert facets == {"sim_sum": 0.0, "sim_max": 0.0, "denominator": 0}


def test_human_is_excluded_from_the_pair_set() -> None:
    """A human seat never contributes a persona — only the two AI clones pair.

    The human carries no persona (spec-016 invariant), so even alongside two
    identical AI personas the denominator is C(2, 2) == 1, not C(3, 2): the human
    is skipped, never counted as a third persona — exactly the spec-031 exclusion.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
        "h": _human("h", "Human"),
    }

    facets = score_persona_sim_sum(players)

    assert facets["denominator"] == 1  # C(2, 2) — only the two AI personas pair
    assert facets["sim_max"] == 1.0
    assert facets["sim_sum"] == 1.0


def test_persona_with_none_is_skipped_like_the_human() -> None:
    """An AI seat whose persona is None is skipped — only personaed AI pairs.

    Mirrors the human-exclusion path for the other skip condition the scorer
    applies (``p.persona is not None``): a fallback that never populated a persona
    must not enter the pair set as a (blank) persona.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
        "p-3": _ai("p-3", "Cleo", None),  # AI, but no persona yet
    }

    facets = score_persona_sim_sum(players)

    assert facets["denominator"] == 1  # only the two personaed AI seats pair
    assert facets["sim_max"] == 1.0


def test_name_masking_neutralises_an_embedded_self_name() -> None:
    """Two personas identical except for each one's embedded own-name → ratio 1.0.

    Proves the spec-009 name-mask runs over the table-facing text: the ONLY
    textual difference between the two personas is each carrying its own player
    name verbatim in its ``public_persona``. Without masking the differing name
    tokens would drop the difflib ratio below 1.0; with masking both names collapse
    to the same placeholder, so the pair scores as identical (peak == sum == 1.0).
    """
    persona_ada = PlayerPersona(
        personality="calm and observant",
        manner="speaks plainly and listens more than talks",
        public_persona="Ada, who tends the orchard with a steady hand",
        true_self="",
    )
    persona_bram = PlayerPersona(
        personality="calm and observant",
        manner="speaks plainly and listens more than talks",
        public_persona="Bram, who tends the orchard with a steady hand",
        true_self="",
    )
    players = {
        "p-1": _ai("p-1", "Ada", persona_ada),
        "p-2": _ai("p-2", "Bram", persona_bram),
    }

    facets = score_persona_sim_sum(players)

    assert facets["denominator"] == 1  # C(2, 2)
    assert facets["sim_max"] == 1.0, (
        "embedded self-names should be masked to the same token, so the otherwise-"
        "identical personas score as character-for-character identical"
    )
    assert facets["sim_sum"] == 1.0


def test_true_self_never_participates_in_the_comparison() -> None:
    """Identical table-facing text + DIFFERENT true_self still scores as identical.

    The scorer builds text from ``personality``/``manner``/``public_persona`` only.
    Two personas share that table-facing text exactly but carry wildly different
    ``true_self`` backstories (a Mafioso's hidden legend). If ``true_self`` leaked
    into the comparison the pair would diverge and ``sim_max`` would fall below 1.0;
    because it does not, the peak pins to 1.0 — confirming the hidden field is
    excluded by construction (the spec-016 / §2.4 allegiance-hiding invariant).
    """
    cover = ("calm and observant", "speaks plainly", "a steady orchard-keeper")
    mafioso = PlayerPersona(
        personality=cover[0],
        manner=cover[1],
        public_persona=cover[2],
        true_self="secretly the ringleader who poisons the well at midnight",
    )
    citizen = PlayerPersona(
        personality=cover[0],
        manner=cover[1],
        public_persona=cover[2],
        true_self="",  # honest citizen — empty hidden self
    )
    players = {
        "p-1": _ai("p-1", "Ada", mafioso, role="mafia"),
        "p-2": _ai("p-2", "Bram", citizen),
    }

    facets = score_persona_sim_sum(players)

    assert facets["sim_max"] == 1.0, (
        "true_self must not enter the comparison — identical table-facing text "
        "should score as identical regardless of differing hidden backstories"
    )
    assert facets["sim_sum"] == 1.0


# ===========================================================================
# 2. Aggregation — mocked run_eval (no graph, no provider, temp ledger)
#
# Mirrors test_slice31_persona_distinct.py's storage/ledger-link pattern exactly:
# stub the provenance collectors + the model-name resolver, redirect ``LEDGER_PATH``
# to a temp file, stub ``_play_one_game`` to return hand-built ``_GameCapture``s
# (one per game), and inject a ``transcripts_root`` + pinned ``run_id`` under
# ``tmp_path``. The real ledger and the real ``evals/transcripts/`` are never
# written. The mean = Σ ratios / total pairs ACROSS games; the peak = max
# ``sim_max`` across games.
# ===========================================================================


def _capture_with_personas(personas: list[PlayerPersona]) -> _GameCapture:
    """A ``_GameCapture`` whose final roster carries the given AI personas.

    All other ``_GameCapture`` inputs are empty/minimal — the persona scorers read
    only ``cap.players`` — so the run scores ``persona_mean_sim`` / ``persona_peak_sim``
    over exactly this roster with no graph, model, or messages. A non-empty
    ``events`` log lets ``render_transcript`` produce a real document (the transcript
    write happens against the injected ``transcripts_root``).
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
    ``tmp_path``. The number of games == ``len(rosters)``.
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


def test_run_eval_records_mean_and_peak_value_shapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mocked run records both metrics as ``{mean|peak, denominator}`` value shapes.

    A single game with one collapsed pair in a varied roster: the mean is the low
    average over all pairs, the peak is the closest-pair value (1.0). Both land in
    ``result.metrics`` with the value-type shape — ``mean``/``denominator`` and
    ``peak``/``denominator`` — and NO ``rate``/``count``.
    """
    collapsed_roster = [_DISTINCT_A, _DISTINCT_B, _DISTINCT_C, _DISTINCT_D, _DISTINCT_A]
    result = _run_eval_over_games(monkeypatch, tmp_path, [collapsed_roster])

    assert _MEAN_KEY in result.metrics
    assert _PEAK_KEY in result.metrics
    mean_facets = result.metrics[_MEAN_KEY]
    peak_facets = result.metrics[_PEAK_KEY]

    # Value-type shape: the similarity value + the pair count, no rate/count.
    assert set(mean_facets) == {"mean", "denominator"}
    assert set(peak_facets) == {"peak", "denominator"}
    assert mean_facets["denominator"] == 10  # C(5, 2), one game
    assert peak_facets["denominator"] == 10

    # The peak is the closest (identical) pair; the mean stays low.
    assert peak_facets["peak"] == 1.0
    assert 0.0 < mean_facets["mean"] < 0.5
    assert peak_facets["peak"] > mean_facets["mean"]


def test_aggregation_mean_is_sum_over_total_pairs_across_games(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """mean == Σ per-game sim_sum / Σ per-game pairs, summed ACROSS games.

    Two games with different rosters: the batch mean must be the total similarity
    sum over the total pair count (not an average of per-game means). We recompute
    the expected mean from the same pure scorer over each game's roster and assert
    the recorded ``mean`` / ``denominator`` match.
    """
    game1 = [_DISTINCT_A, _DISTINCT_B, _DISTINCT_C]  # C(3,2)=3 pairs
    game2 = [_CLONE, _CLONE]  # C(2,2)=1 pair, identical

    result = _run_eval_over_games(monkeypatch, tmp_path, [game1, game2])

    # Recompute the batch totals from the same pure scorer (the aggregation contract).
    f1 = score_persona_sim_sum(
        {f"p-{i}": _ai(f"p-{i}", f"AI{i}", p) for i, p in enumerate(game1, 1)}
    )
    f2 = score_persona_sim_sum(
        {f"p-{i}": _ai(f"p-{i}", f"AI{i}", p) for i, p in enumerate(game2, 1)}
    )
    total_sum = f1["sim_sum"] + f2["sim_sum"]
    total_pairs = f1["denominator"] + f2["denominator"]

    mean_facets = result.metrics[_MEAN_KEY]
    assert mean_facets["denominator"] == total_pairs == 4  # 3 + 1
    assert mean_facets["mean"] == pytest.approx(total_sum / total_pairs)


def test_aggregation_peak_is_max_across_games(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """peak == the MAX sim_max across games (the most-similar pair anywhere in the run).

    Game 1 is a varied roster (peak well below 1.0); game 2 carries an identical
    pair (peak 1.0). The batch peak is the across-games max — 1.0 — even though the
    average over all 4 pairs stays low. The collapse in any single game lifts the
    run peak.
    """
    game1 = [_DISTINCT_A, _DISTINCT_B, _DISTINCT_C]  # no near-identical pair
    game2 = [_CLONE, _CLONE]  # one identical pair → sim_max 1.0

    result = _run_eval_over_games(monkeypatch, tmp_path, [game1, game2])

    assert result.metrics[_PEAK_KEY]["peak"] == 1.0  # max across the two games
    # The average over all 4 pairs is NOT dominated by the single collapsed pair.
    assert result.metrics[_MEAN_KEY]["mean"] < 1.0


def test_fewer_than_two_personas_everywhere_omits_both_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A batch where no game ever had ≥2 AI personas omits BOTH value metrics.

    Every game offers no pair (denominator 0 everywhere → total pairs 0), so the
    opportunity gate never fires — the not-applicable case is reported by OMISSION,
    not a misleading zero (functional-spec §2, mirroring ``persona_near_dup``).
    """
    single = [_DISTINCT_A]  # one AI persona → no pair

    result = _run_eval_over_games(monkeypatch, tmp_path, [single, single])

    assert _MEAN_KEY not in result.metrics
    assert _PEAK_KEY not in result.metrics


def test_mean_and_peak_carry_no_wilson_ci_band(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Neither value metric gets a ``ci_low``/``ci_high`` — a mean/peak is not a rate.

    ``_attach_ci`` keys off ``count``; the value-type facets carry no ``count``, so
    the Wilson-band attachment is skipped for both (tech-spec §2 B). A Wilson
    interval is a proportion's reliability band — meaningless for a continuous
    mean or peak.
    """
    roster = [_DISTINCT_A, _DISTINCT_B, _CLONE, _CLONE]
    result = _run_eval_over_games(monkeypatch, tmp_path, [roster])

    for key in (_MEAN_KEY, _PEAK_KEY):
        facets = result.metrics[key]
        assert "ci_low" not in facets
        assert "ci_high" not in facets


def test_rate_metrics_still_carry_a_ci_band(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CI-skip is scoped to value metrics — the existing rate metrics still get a band.

    Belt-and-braces over the ``_attach_ci`` ``count``-presence guard: the same run
    that omits a CI on the mean/peak must STILL attach the Wilson band to the
    rate-type ``persona_near_dup`` (an identical pair → rate 1.0, count 1/1) — the
    guard leaves the rate metrics' band behaviour untouched.
    """
    roster = [_CLONE, _CLONE]  # one identical pair → persona_near_dup rate 1.0
    result = _run_eval_over_games(monkeypatch, tmp_path, [roster])

    near_dup = result.metrics["persona_near_dup"]
    assert "ci_low" in near_dup and "ci_high" in near_dup


def test_metrics_version_is_unchanged_by_the_new_value_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The additive value metrics do NOT bump ``METRICS_VERSION`` (tech-spec §2 B).

    Two brand-new orthogonal value metrics are additive — old records simply lack
    the keys — so the version stays put (the ``persona_near_dup`` / ``outcomes``
    precedent). The constant is 1, and the rendered record stamps that same value.
    """
    assert METRICS_VERSION == 1

    result = _run_eval_over_games(monkeypatch, tmp_path, [[_CLONE, _CLONE]])
    doc = render_record(result, "2026-06-19")

    assert f"  metrics_version: {METRICS_VERSION}" in doc


def test_run_eval_does_not_touch_the_real_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The run writes only the redirected temp ledger; the real one is untouched.

    Belt-and-braces over the ``LEDGER_PATH`` redirect: the temp ledger gains the
    run's record, while the repo's real ``evals/blunder-ledger.yaml`` is neither
    created here nor modified — the redirect in ``_stub_run_eval_env`` is what keeps
    the committed ledger safe (the suite-wide redirect spec 032 reuses).
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    real_ledger = blunder_eval.LEDGER_PATH
    # The redirect must point away from the repo's committed ledger.
    assert real_ledger == ledger
    assert ledger.parent == tmp_path

    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: _capture_with_personas([_CLONE, _CLONE]),
    )
    run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=tmp_path / "transcripts",
        run_id="2026-06-19T00-00-01",
    )

    # The record landed in the temp ledger (carrying the value metric keys).
    assert ledger.exists()
    assert _MEAN_KEY in ledger.read_text(encoding="utf-8")


# ===========================================================================
# 3. Viewer — _metric_cell / render_detail value-type rendering + METRIC_ORDER
#
# Mirrors tests/test_ledger_model.py's viewer-cell assertions: hand-built
# dict-shaped records (the on-disk ``metrics`` shape ``eval_ledger`` reads), no
# parsing. The value-type branch renders ``~<value> (n=<pairs>)`` for a facet
# carrying ``mean`` OR ``peak`` (and no ``rate``); an absent metric renders blank.
# ===========================================================================


def test_metric_order_carries_both_new_entries_in_order() -> None:
    """``METRIC_ORDER`` registers both value metrics, after ``persona_near_dup``.

    The viewer derives its columns + detail lines from this tuple, so the labels
    and their order are the single source of truth: ``persona sim`` (mean) then
    ``persona max`` (peak) immediately follow the spec-031 ``persona dup`` count.
    """
    keys = [key for key, _ in METRIC_ORDER]

    assert (_MEAN_KEY, "persona sim") in METRIC_ORDER
    assert (_PEAK_KEY, "persona max") in METRIC_ORDER

    # Order: persona_near_dup → persona_mean_sim → persona_peak_sim, contiguous.
    dup_i = keys.index("persona_near_dup")
    mean_i = keys.index(_MEAN_KEY)
    peak_i = keys.index(_PEAK_KEY)
    assert dup_i < mean_i < peak_i
    assert mean_i == dup_i + 1
    assert peak_i == mean_i + 1


def test_metric_cell_renders_mean_as_value_type_form() -> None:
    """``_metric_cell`` renders ``persona_mean_sim`` as ``~<mean> (n=<pairs>)``.

    The value-type branch (tech-spec §2 C): a facet with ``mean``/``denominator``
    and no ``rate`` renders with the ``~`` similarity prefix and the ``n=`` pair
    count, two-decimal — distinct from the ``rate [ci] count/denom`` rate form.
    """
    record = {"metrics": {_MEAN_KEY: {"mean": 0.4237, "denominator": 10}}}

    assert _metric_cell(record, _MEAN_KEY) == "~0.42 (n=10)"


def test_metric_cell_renders_peak_as_value_type_form() -> None:
    """``_metric_cell`` renders ``persona_peak_sim`` as ``~<peak> (n=<pairs>)``.

    The same value-type branch keys off ``peak`` as well as ``mean`` — a collapsed
    pair's 1.0 peak renders ``~1.00 (n=10)``.
    """
    record = {"metrics": {_PEAK_KEY: {"peak": 1.0, "denominator": 10}}}

    assert _metric_cell(record, _PEAK_KEY) == "~1.00 (n=10)"


def test_metric_cell_value_metric_carries_no_ci_band() -> None:
    """A value-type cell never renders a ``[lo–hi]`` band, even if CI keys leaked in.

    The cell format is ``~<value> (n=…)`` — no Wilson band. Defensive: even a
    malformed facet carrying stray ``ci_*`` keys (which the writer never emits for
    a value metric) must still render the bare value form, never a bracketed band.
    """
    record = {
        "metrics": {
            _MEAN_KEY: {"mean": 0.42, "denominator": 10, "ci_low": 0.1, "ci_high": 0.9}
        }
    }

    cell = _metric_cell(record, _MEAN_KEY)
    assert cell == "~0.42 (n=10)"
    assert "[" not in cell and "]" not in cell


def test_metric_cell_absent_value_metric_is_blank() -> None:
    """A record lacking the value metric renders the empty string (blank, not zero).

    Mirrors the absent-rate-metric blank: a run with no ``persona_mean_sim`` /
    ``persona_peak_sim`` (a pre-032 record, or one with <2 AI personas everywhere)
    renders blank in those columns — visibly distinct from a real value — without
    error.
    """
    record = {"metrics": {"repetition": {"rate": 0.5, "count": 10, "denominator": 20}}}

    assert _metric_cell(record, _MEAN_KEY) == ""
    assert _metric_cell(record, _PEAK_KEY) == ""


def test_render_detail_shows_both_value_metrics_with_their_labels() -> None:
    """``render_detail`` lists both metrics under their labels in the value form.

    The detail view iterates ``METRIC_ORDER``; a record carrying both value
    metrics shows ``persona sim: ~<mean> (n=…)`` and ``persona max: ~<peak> (n=…)``
    on their own labelled lines (full precision, no ``—`` placeholder).
    """
    record = {
        "metrics": {
            _MEAN_KEY: {"mean": 0.42, "denominator": 10},
            _PEAK_KEY: {"peak": 1.0, "denominator": 10},
        },
    }

    detail = render_detail(record)

    mean_line = next(
        line for line in detail.splitlines() if "persona sim:" in line
    )
    peak_line = next(
        line for line in detail.splitlines() if "persona max:" in line
    )
    # The value-type ``~…`` form with the pair count, on the labelled lines.
    assert mean_line.strip() == "persona sim: ~0.42 (n=10)"
    assert peak_line.strip() == "persona max: ~1.0 (n=10)"
    # Present, not the absent-metric em-dash.
    assert "—" not in mean_line and "—" not in peak_line


def test_render_detail_absent_value_metric_shows_em_dash() -> None:
    """A record lacking the value metrics shows ``—`` on those detail lines.

    The absent-metric placeholder (distinct from the table cell's blank): a
    pre-032 record renders ``persona sim: —`` / ``persona max: —`` so a
    never-recorded value stays visibly distinct from a real one — and the
    surrounding present metric still renders.
    """
    record = {
        "metrics": {"repetition": {"rate": 0.5, "count": 10, "denominator": 20}},
    }

    detail = render_detail(record)

    assert "persona sim: —" in detail
    assert "persona max: —" in detail
    # The present metric still renders alongside the absent ones.
    assert "repetition:" in detail


def test_render_detail_rate_metric_unaffected_by_value_branch() -> None:
    """A rate metric still renders the ``rate [ci] count/denom`` form (no regression).

    The value-type branch is strictly additive: a record whose ``persona_near_dup``
    is a real rate (with a Wilson band) must STILL render the rate form in the
    detail view — the new ``mean``/``peak`` arm never intercepts a ``rate`` facet.
    """
    record = {
        "metrics": {
            "persona_near_dup": {
                "rate": 0.5,
                "count": 1,
                "denominator": 2,
                "ci_low": 0.09,
                "ci_high": 0.91,
            },
        },
    }

    detail = render_detail(record)

    dup_line = next(
        line for line in detail.splitlines() if "persona dup:" in line
    )
    assert "1/2" in dup_line  # count/denominator
    assert "~" not in dup_line  # NOT the value-type prefix
    assert "[" in dup_line and "]" in dup_line  # the Wilson band is present
