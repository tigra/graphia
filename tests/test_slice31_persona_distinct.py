"""Offline tests for spec 031 Slice 1 — the persona-distinctiveness metric.

Spec 031 (*Distinct AI Personas Across the Roster*), Slice 1, Task 3 (tech-spec
§4, *Testing Strategy*). All-mocked: the pure scorer needs no model and no RNG,
and the ledger/``run_eval`` integration test stubs ``_play_one_game`` (so no
graph or provider is built) and redirects both the ledger path and the
transcripts dir into ``tmp_path`` — the real ``evals/blunder-ledger.yaml`` /
``evals/transcripts/`` are never touched. The autouse ``safe_llm`` net
(``tests/conftest.py``) is left intact; these tests never reach an LLM call site.

Two concerns:

1. **The pure scorer** ``score_persona_near_dup`` over hand-built ``players``
   maps — a clearly-distinct roster (``count == 0`` / rate ``0.0``), near-identical
   personas (high ``count`` / rate), a single (or zero) AI persona
   (``denominator == 0`` / ``rate is None``), the **human excluded**, **name-masking**
   (personas differing only by an embedded self-name still count near-dup), and
   ``true_self`` **never** participating (identical table-facing text but different
   ``true_self`` is still near-dup).

2. **Ledger / ``run_eval`` integration** — a mocked run whose written record's
   ``metrics`` block carries ``persona_near_dup`` with the full
   ``rate``/``count``/``denominator``/``ci_low``/``ci_high`` shape;
   ``eval_ledger.METRIC_ORDER`` surfaces it in ``render_detail``; and
   ``metrics_version`` is unchanged.

The scorer's contract (tech-spec §2, *Component B*): over the AI players (human
skipped, ``persona is None`` skipped), build table-facing text
``personality + " " + manner + " " + public_persona`` (never ``true_self``),
mask + normalise via the spec-009 helpers, and count unordered pairs whose
``difflib`` ratio ``>= 0.85`` over ``C(n, 2)`` pairs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from graphia.eval_ledger import METRIC_ORDER, render_detail
from graphia.state import PlayerPersona, PlayerState
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    METRICS_VERSION,
    _GameCapture,
    render_record,
    run_eval,
    score_persona_near_dup,
)

# The ledger key + the dotted-key the metric is recorded under in the record's
# ``metrics`` block — the single source of truth this file asserts against.
_PERSONA_KEY = "persona_near_dup"


# ===========================================================================
# Roster builders — hand-built ``PlayerState`` / ``PlayerPersona`` maps, no model.
#
# ``PlayerPersona(personality, manner, public_persona, true_self)`` — the
# state.py dataclass the scorer reads off ``PlayerState.persona``. The scorer
# builds table-facing text from ``personality`` + ``manner`` + ``public_persona``
# only, so ``true_self`` is set deliberately (and asserted never to leak in).
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


# Three personas with clearly different wording in every table-facing field — far
# below the 0.85 near-dup threshold pairwise.
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

# Two personas whose table-facing text is character-for-character identical — a
# guaranteed near-duplicate pair (difflib ratio 1.0).
_CLONE = PlayerPersona(
    personality="calm and observant",
    manner="speaks plainly and listens more than talks",
    public_persona="a steady hand who tends the orchard",
    true_self="",
)


# ===========================================================================
# 1. Pure scorer — score_persona_near_dup
# ===========================================================================


def test_distinct_roster_scores_zero_count_and_zero_rate() -> None:
    """Three clearly-different personas → no near-dup pairs, rate 0.0 over C(3,2)=3."""
    players = {
        "p-1": _ai("p-1", "Ada", _DISTINCT_A),
        "p-2": _ai("p-2", "Bram", _DISTINCT_B),
        "p-3": _ai("p-3", "Cleo", _DISTINCT_C),
    }

    facets = score_persona_near_dup(players)

    assert facets["count"] == 0
    assert facets["denominator"] == 3  # C(3, 2)
    assert facets["rate"] == 0.0


def test_identical_personas_score_high_count_and_rate() -> None:
    """Three byte-identical table-facing personas → all 3 pairs near-dup, rate 1.0."""
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
        "p-3": _ai("p-3", "Cleo", _CLONE),
    }

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 3  # C(3, 2)
    assert facets["count"] == 3  # every unordered pair is a near-duplicate
    assert facets["rate"] == 1.0


def test_mixed_roster_counts_only_the_near_duplicate_pair() -> None:
    """Two clones + one distinct → exactly the clone pair counts (1 of 3)."""
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
        "p-3": _ai("p-3", "Cleo", _DISTINCT_A),
    }

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 3  # C(3, 2)
    assert facets["count"] == 1  # only the (Ada, Bram) clone pair
    assert facets["rate"] == pytest.approx(1 / 3)


def test_single_ai_persona_has_no_pairs_rate_none() -> None:
    """A roster with one AI persona offers no pairs → denominator 0, rate None."""
    players = {
        "p-1": _ai("p-1", "Ada", _DISTINCT_A),
        "h": _human("h", "Human"),
    }

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 0
    assert facets["count"] == 0
    assert facets["rate"] is None  # absent, not a misleading 0.0


def test_zero_ai_personas_has_no_pairs_rate_none() -> None:
    """No AI persona at all (only a personaless human) → denominator 0, rate None."""
    players = {"h": _human("h", "Human")}

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 0
    assert facets["rate"] is None


def test_human_is_excluded_from_the_pair_count() -> None:
    """A human seat never contributes a persona — only the two AI clones pair.

    The human carries no persona (spec-016 invariant), so even alongside two
    identical AI personas the denominator is C(2, 2) == 1, not C(3, 2): the human
    is skipped, never counted as a third persona.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
        "h": _human("h", "Human"),
    }

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 1  # C(2, 2) — only the two AI personas pair
    assert facets["count"] == 1
    assert facets["rate"] == 1.0


def test_persona_with_none_is_skipped_like_the_human() -> None:
    """An AI seat whose persona is None is skipped — only personaed AI pairs.

    Mirrors the human-exclusion path for the other skip condition the scorer
    applies (``p.persona is not None``): a fallback that never populated a persona
    must not be counted as a (blank) persona pair.
    """
    players = {
        "p-1": _ai("p-1", "Ada", _CLONE),
        "p-2": _ai("p-2", "Bram", _CLONE),
        "p-3": _ai("p-3", "Cleo", None),  # AI, but no persona yet
    }

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 1  # only the two personaed AI seats pair
    assert facets["count"] == 1


def test_name_masking_neutralises_an_embedded_self_name() -> None:
    """Two personas identical except for each one's embedded own-name still pair.

    Proves the spec-009 name-mask runs over the table-facing text: the ONLY
    textual difference between the two personas is each carrying its own player
    name verbatim in its ``public_persona``. Without masking the differing name
    tokens would lower the difflib ratio; with masking both names collapse to the
    same placeholder, so the pair is a near-duplicate. ``ai_names`` is the set of
    non-human player names, exactly what the scorer masks against.
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

    facets = score_persona_near_dup(players)

    assert facets["denominator"] == 1  # C(2, 2)
    assert facets["count"] == 1, (
        "embedded self-names should be masked to the same token, so the otherwise-"
        "identical personas are a near-duplicate pair"
    )
    assert facets["rate"] == 1.0


def test_true_self_never_participates_in_the_comparison() -> None:
    """Identical table-facing text + DIFFERENT true_self is still a near-dup pair.

    The scorer builds text from ``personality``/``manner``/``public_persona`` only.
    Two personas share that table-facing text exactly but carry wildly different
    ``true_self`` backstories (a Mafioso's hidden legend). If ``true_self`` leaked
    into the comparison the pair would diverge and not count; because it does not,
    the pair is a near-duplicate — confirming the hidden field is excluded by
    construction (the spec-016 / §2.4 allegiance-hiding invariant).
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

    facets = score_persona_near_dup(players)

    assert facets["count"] == 1, (
        "true_self must not enter the comparison — identical table-facing text "
        "should be a near-duplicate regardless of differing hidden backstories"
    )
    assert facets["rate"] == 1.0


# ===========================================================================
# 2. Ledger / run_eval integration (mocked — no graph, no provider, temp ledger)
#
# Mirrors test_blunder_eval.py's storage/ledger-link pattern: stub the provenance
# collectors + the model-name resolver, redirect ``LEDGER_PATH`` to a temp file,
# stub ``_play_one_game`` to return hand-built ``_GameCapture``s, and inject a
# ``transcripts_root`` + pinned ``run_id`` under ``tmp_path``. The real ledger and
# the real ``evals/transcripts/`` are never written.
# ===========================================================================


def _capture_with_personas(personas: list[PlayerPersona]) -> _GameCapture:
    """A ``_GameCapture`` whose final roster carries the given AI personas.

    All other ``_GameCapture`` inputs are empty/minimal — the persona scorer reads
    only ``cap.players`` — so the run scores ``persona_near_dup`` over exactly this
    roster with no graph, model, or messages. A non-empty ``events`` log lets
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


def _run_eval_with_personas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    personas: list[PlayerPersona],
):
    """Drive a 1-game mocked ``run_eval`` whose roster carries ``personas``.

    Returns the populated ``EvalResult``. The single game's ``_GameCapture`` is
    hand-built (no graph / no provider); transcripts land under ``tmp_path``.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: _capture_with_personas(personas),
    )
    return run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=tmp_path / "transcripts",
        run_id="2026-06-19T00-00-00",
    )


def test_run_eval_records_persona_near_dup_with_full_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mocked run records ``metrics.persona_near_dup`` with rate/count/denom + CI.

    Two byte-identical AI personas → one near-dup pair over C(2,2)=1 → rate 1.0. The
    metric lands in ``result.metrics`` with the full action-metric shape, and
    ``_attach_ci`` adds the Wilson band (``ci_low``/``ci_high``) like every present
    metric.
    """
    result = _run_eval_with_personas(monkeypatch, tmp_path, [_CLONE, _CLONE])

    assert _PERSONA_KEY in result.metrics
    facets = result.metrics[_PERSONA_KEY]
    assert facets["count"] == 1
    assert facets["denominator"] == 1
    assert facets["rate"] == 1.0
    # ``_attach_ci`` gives every present metric a Wilson reliability band.
    assert "ci_low" in facets and "ci_high" in facets
    assert 0.0 <= float(facets["ci_low"]) <= float(facets["ci_high"]) <= 1.0


def test_persona_near_dup_renders_in_the_ledger_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The rendered record's ``metrics`` block carries the full facet sub-keys.

    ``render_record`` emits ``rate``/``count``/``denominator`` then the
    ``ci_low``/``ci_high`` band under ``persona_near_dup:`` — the same shape as
    every other present metric.
    """
    result = _run_eval_with_personas(monkeypatch, tmp_path, [_CLONE, _CLONE])

    doc = render_record(result, "2026-06-19")

    assert f"  {_PERSONA_KEY}:" in doc
    # The metric sub-block is inside ``metrics:`` and carries the facet sub-keys.
    metrics_i = doc.index("metrics:")
    key_i = doc.index(f"  {_PERSONA_KEY}:")
    assert key_i > metrics_i
    block = doc[key_i:]
    for subkey in ("rate:", "count:", "denominator:", "ci_low:", "ci_high:"):
        assert f"    {subkey}" in block, f"missing {subkey} under {_PERSONA_KEY}"


def test_metric_order_surfaces_persona_near_dup_in_render_detail() -> None:
    """``METRIC_ORDER`` carries the metric, so ``render_detail`` lists it by label.

    The viewer's detail view iterates ``METRIC_ORDER``; a record carrying the metric
    shows its rate/count/denominator under the ``persona dup`` label, while the
    surrounding metrics still render. We assert against a plain dict-shaped record
    (the on-disk ``metrics`` shape ``eval_ledger`` reads).
    """
    # The (dotted_key, label) tuple is registered in the canonical column order.
    assert (_PERSONA_KEY, "persona dup") in METRIC_ORDER

    record = {
        "metrics": {
            _PERSONA_KEY: {
                "rate": 0.5,
                "count": 1,
                "denominator": 2,
                "ci_low": 0.09,
                "ci_high": 0.91,
            },
        },
    }

    detail = render_detail(record)

    # The metric appears under its METRIC_ORDER label with its facets, not as "—".
    assert "persona dup:" in detail
    persona_line = next(
        line for line in detail.splitlines() if "persona dup:" in line
    )
    assert "1/2" in persona_line  # count/denominator
    assert "—" not in persona_line  # present, not the absent-metric em-dash


def test_metrics_version_is_unchanged_by_the_new_metric(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The additive metric does NOT bump ``METRICS_VERSION`` (tech-spec §2, B).

    A brand-new orthogonal metric is additive — old records simply lack the key —
    so the version stays put (the ``outcomes`` / ``vote_activity`` precedent). The
    constant is 1, and the rendered record stamps that same value under ``run``.
    """
    assert METRICS_VERSION == 1

    result = _run_eval_with_personas(monkeypatch, tmp_path, [_CLONE, _CLONE])
    doc = render_record(result, "2026-06-19")

    assert f"  metrics_version: {METRICS_VERSION}" in doc


def test_run_eval_does_not_touch_the_real_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The run writes only the redirected temp ledger; the real one is untouched.

    Belt-and-braces over the ``LEDGER_PATH`` redirect: the temp ledger gains the
    run's record (carrying ``persona_near_dup``), while the repo's real
    ``evals/blunder-ledger.yaml`` is neither created here nor modified — the redirect
    in ``_stub_run_eval_env`` is what keeps the committed ledger safe.
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

    # The record landed in the temp ledger, with our new metric.
    assert ledger.exists()
    assert _PERSONA_KEY in ledger.read_text(encoding="utf-8")
