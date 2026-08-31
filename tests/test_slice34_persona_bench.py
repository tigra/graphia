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
   is untouched (without ``--record`` the bench writes nothing at all).

Spec 036 (*Persona-Generation Measurements Join the Tracked Quality History*),
Slice 1 Task 4 EXTENDS this file with the OPT-IN recording path — still entirely
offline, and now with the mandatory ``blunder_eval.LEDGER_PATH`` redirect at
``tmp_path`` on every test that can reach the appender:

5. **Flag gating** — ``--record`` absent leaves the ledger untouched (concern 4
   above, extended into a live guard now that a ledger path exists); present
   appends EXACTLY one ``run.kind: 'persona-bench'`` document and leaves every
   prior document byte-unchanged, twice over accumulating two.
6. **The mapping** — ``build_bench_record`` maps a hand-built ``BenchSummary``
   onto one ``EvalResult``: the record kind, the roster counts (attempted /
   completed / not-completed), the resolved tier model ids, empty game-only
   blocks, and the four persona facets in their value-type
   ``{mean|peak, denominator}`` shape — with the semantic pair ABSENT (never a
   misleading 0.0) when it was not measured.

Slice 2 Task 8 adds what makes such a record *comparable* rather than merely
present, still entirely offline:

7. **The generation counts** — ``generation`` carries the pooled collision /
   regeneration figures, with a measured ``0`` written EXPLICITLY (it is the
   diversity-on headline, not an absence).
8. **The A/B conditions** — ``settings.persona`` carries the arm the run was
   *invoked* with plus the three resolved knobs, so a flag-on and a flag-off
   record are readable as a pair.
9. **Provenance** — ``main`` gathers ``code`` / ``provider`` with the EVAL's own
   collectors and injects them, and the blocks render exactly as a game
   record's. The collectors are **spied, never called**: the real ones shell out
   to ``git`` and (on the ollama path) GET the local ollama endpoints.
10. **The note** — ``--note`` reaches the record's last key; its absence renders
    the empty-but-present ``notes: ''``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path



import pytest

from graphia.llm import Persona, Roster
from graphia.tools import blunder_eval, persona_bench
from graphia.tools.persona_bench import (
    RECORD_KIND,
    BenchSummary,
    build_bench_record,
    main,
    run_bench,
)


@pytest.fixture(autouse=True)
def _isolate_bench_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the process env every bench test mutates (spec 036 follow-up).

    ``run_bench`` sets ``GRAPHIA_LLM_PROVIDER`` and ``_isolate_cloud_stores``
    pops ``GRAPHIA_REMOTE`` plus the cloud-store vars — directly on
    ``os.environ``, so nothing undoes them when the test ends. Registering the
    keys with ``monkeypatch`` here makes teardown restore them.

    Why this is load-bearing rather than tidy: a leaked
    ``GRAPHIA_LLM_PROVIDER=ollama`` makes ``load_config`` reject ANY later
    remote-mode test with the documented ollama-is-local-only contradiction. The
    suite passed before only because the last ``run_bench`` call in this file
    happened to use ``bedrock``, overwriting the leak before the remote badge and
    failure-modal tests ran — an ordering accident, not isolation. Appending an
    ollama test at the end broke it.
    """
    from graphia.tools.blunder_eval import _CLOUD_STORE_ENV_VARS

    for var in ("GRAPHIA_LLM_PROVIDER", "GRAPHIA_REMOTE", *_CLOUD_STORE_ENV_VARS):
        monkeypatch.delenv(var, raising=False)

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` runs the bench, prints a summary, returns 0 — and writes no ledger.

    Spec 036 made this a LIVE GUARD rather than a statement about a capability
    the bench lacks: ``--record`` (default OFF) now gives it a ledger path, so
    the flag-off path must be asserted to still write nothing. Extended with the
    mandatory ``LEDGER_PATH`` redirect, which lets the assertion be made twice
    over — the ledger the bench WOULD have used is never created, and the repo's
    real committed ledger is byte-unchanged.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    # Snapshot the REAL committed ledger before redirecting, so the untouched
    # assertion below is about the file that actually matters.
    real_ledger = blunder_eval.LEDGER_PATH
    before = real_ledger.read_bytes() if real_ledger.exists() else None
    ledger = tmp_path / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)

    rc = main(["--provider", "bedrock", "--rosters", "2", "--diversity", "on"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "BATCH SUMMARY" in out
    assert "persona_lex_mean" in out

    # Without --record the run writes nothing: not even the redirected ledger
    # exists (``append_record`` creates the file on first use, so its absence is
    # proof the appender was never called).
    assert not ledger.exists()
    after = real_ledger.read_bytes() if real_ledger.exists() else None
    assert before == after, "the bench must not touch the ledger without --record"


def test_main_rejects_zero_rosters(env: Path) -> None:
    """``--rosters 0`` is rejected before any generation."""
    rc = main(["--provider", "bedrock", "--rosters", "0"])
    assert rc == 2


def test_bedrock_claude_is_an_accepted_bench_provider(
    env: Path,
    fake_small,
    fake_large,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Spec 035: the bench accepts ``bedrock-claude`` alongside ollama/bedrock.

    Driven with the ``safe_llm`` fakes, so it reaches no real model — this locks
    the provider *vocabulary*, not a live Claude call.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    rc = main(["--provider", "bedrock-claude", "--rosters", "2", "--diversity", "on"])

    assert rc == 0
    assert "BATCH SUMMARY" in capsys.readouterr().out


def test_bench_rejects_an_unknown_provider(env: Path) -> None:
    """Only the three real providers are accepted (argparse ``choices``)."""
    with pytest.raises(SystemExit):
        main(["--provider", "openai", "--rosters", "1"])


# ===========================================================================
# Spec 036 (Persona-Generation Measurements Join the Tracked Quality History),
# Slice 1 — the OPT-IN ledger record.
#
# The bench could not touch the ledger at all before this spec; now it can, so
# every test below redirects ``blunder_eval.LEDGER_PATH`` at ``tmp_path``. That
# redirect is MANDATORY, not hygiene: ~25 synthetic records have already leaked
# into the repo-committed ``evals/blunder-ledger.yaml`` from exactly this bug
# class, and ``append_record`` resolves the module global at CALL time precisely
# so a ``monkeypatch.setattr`` reaches the no-arg call inside ``main``.
#
# Two concerns:
#
# 1. **Flag gating.** ``--record`` absent ⇒ nothing is written anywhere (the
#    live guard the pre-036 no-write test became); present ⇒ EXACTLY one document
#    is appended and every prior document is byte-unchanged.
# 2. **The mapping.** ``build_bench_record`` is pure (given a config), so the
#    roster counts, the record kind, the resolved tier model ids, and the
#    value-type metric shape are asserted without generating a single roster —
#    including the absent-not-zero rule for an unmeasured facet.
# ===========================================================================


class _BenchConfigStub:
    """The minimal config ``build_bench_record`` reads — an ollama-tier pair.

    ``_resolved_model_names`` (the harness's own resolver, deliberately reused
    rather than hand-rolled) branches on ``llm_provider`` and reads the
    ``ollama_*`` tier fields on the ollama path, so this stub exercises the real
    resolver while keeping the mapping tests pure: no ``load_config``, no env, no
    provider client.
    """

    llm_provider = "ollama"
    ollama_large_model = "qwen3-coder:30b"
    ollama_small_model = "qwen2.5:3b"


def _summary(
    *,
    lex: tuple[float, float, int] | None = (0.1234, 0.5, 75),
    sem: tuple[float, float, int] | None = None,
    attempted: int = 5,
    completed: int = 5,
    collisions: int = 3,
    regenerations: int = 7,
) -> BenchSummary:
    """A finished ``BenchSummary`` with the given lexical / semantic facets.

    ``lex`` / ``sem`` are ``(mean, peak, denominator)`` triples, or ``None`` for
    "this pair was never measured" — the state a run without ``--semantic`` (or
    one whose embeddings instrument was unavailable) ends in, and the one the
    record must render as ABSENT rather than 0.0.

    ``collisions`` / ``regenerations`` are the pooled generation-process counts
    (spec 036 Slice 2). They are parameters rather than constants because ``0``
    is a *meaningful* value here — the diversity-on headline — and must be shown
    to render as an explicit zero rather than to disappear.
    """
    summary = BenchSummary(
        provider="ollama",
        diversity_enabled=True,
        rosters_attempted=attempted,
        rosters_completed=completed,
        total_collisions=collisions,
        total_regenerations=regenerations,
        duration_seconds=31.5004,
    )
    if lex is not None:
        mean, peak, denominator = lex
        summary.persona_lex_mean = mean
        summary.persona_lex_peak = peak
        summary.lex_denominator = denominator
    if sem is not None:
        mean, peak, denominator = sem
        summary.persona_sem_mean = mean
        summary.persona_sem_peak = peak
        summary.sem_denominator = denominator
    return summary


def _seed_ledger(ledger: Path) -> str:
    """Write one PRIOR document into ``ledger`` and return its exact text.

    The append-only guarantee is only testable against something already there:
    the returned text is asserted to survive as a byte-exact PREFIX after the
    bench appends, which is what "every prior document is unchanged" means for a
    file the appender opens in ``'a'`` mode.
    """
    prior = "---\nrun:\n  date: '2026-06-13'\n  metrics_version: 1\nnotes: 'prior'\n"
    ledger.write_text(prior, encoding="utf-8")
    return prior


def test_build_bench_record_labels_the_kind_and_maps_the_roster_counts() -> None:
    """The record is labelled ``persona-bench`` and its counts are ROSTERS.

    ``run.kind`` is what makes a bench record readable as a measurement rather
    than an interrupted game, and (per the unit-follows-kind rule) what
    reinterprets the ``quality`` attempted/completed keys as rosters. The
    not-completed count must be carried too: ``games_failed_early`` renders
    UNCONDITIONALLY, so leaving it 0 beside 5-attempted/4-completed would print a
    flat contradiction.
    """
    result = build_bench_record(
        _summary(attempted=5, completed=4), config=_BenchConfigStub()
    )

    assert result.kind == RECORD_KIND == "persona-bench"
    assert result.games_attempted == 5
    assert result.games_completed == 4
    assert result.games_failed_early == 1
    assert result.duration_seconds == pytest.approx(31.5, abs=1e-3)


def test_build_bench_record_resolves_the_tier_model_ids_off_the_config() -> None:
    """Provider + both tier model ids come from the harness's own resolver.

    A bench record must name the same models a game record would, so the mapping
    reuses ``blunder_eval._resolved_model_names`` instead of hand-rolling the
    tier lookup — asserted here through the ollama branch of that resolver.
    """
    result = build_bench_record(_summary(), config=_BenchConfigStub())

    assert result.provider == "ollama"
    assert result.large_model == "qwen3-coder:30b"
    assert result.small_model == "qwen2.5:3b"


def test_build_bench_record_leaves_every_game_only_block_empty() -> None:
    """No ``outcomes`` / ``vote_activity`` / ``transcript_dir`` is populated.

    A bench run plays no game, casts no vote and writes no transcript. All three
    are already conditional in ``render_record``, so leaving them empty is the
    whole mechanism by which the rendered record omits them — pinned at the
    mapping layer here and at the renderer layer in ``tests/test_blunder_eval.py``.
    """
    result = build_bench_record(_summary(), config=_BenchConfigStub())

    assert result.outcomes == {}
    assert result.vote_activity == {}
    assert result.transcript_dir == ""


@pytest.mark.parametrize(
    ("sem", "expected_keys"),
    [
        pytest.param(
            None,
            ["persona_lex_mean", "persona_lex_peak"],
            id="lexical-only-semantic-pair-absent",
        ),
        pytest.param(
            (0.42, 0.9, 75),
            [
                "persona_lex_mean",
                "persona_lex_peak",
                "persona_sem_mean",
                "persona_sem_peak",
            ],
            id="both-pairs-measured",
        ),
    ],
)
def test_build_bench_record_records_only_the_facets_that_were_measured(
    sem: tuple[float, float, int] | None,
    expected_keys: list[str],
) -> None:
    """The free lexical pair always lands; the paid semantic pair only when taken.

    The absent-not-zero rule (functional-spec §2): a measurement that took only
    the free word-level figures must show NOTHING for the meaning-based ones —
    not a zero, not an error — so the whole path stays usable by someone with no
    cloud access. Key order is asserted too: insertion order IS the rendered
    order, and lexical-then-semantic matches ``METRIC_ORDER``.
    """
    result = build_bench_record(_summary(sem=sem), config=_BenchConfigStub())

    assert list(result.metrics) == expected_keys


@pytest.mark.parametrize(
    ("metric_key", "value_key"),
    [
        pytest.param("persona_lex_mean", "mean", id="lex-mean"),
        pytest.param("persona_lex_peak", "peak", id="lex-peak"),
        pytest.param("persona_sem_mean", "mean", id="sem-mean"),
        pytest.param("persona_sem_peak", "peak", id="sem-peak"),
    ],
)
def test_build_bench_record_metric_facets_keep_the_value_type_shape(
    metric_key: str,
    value_key: str,
) -> None:
    """Each facet is ``{mean|peak, denominator}`` — no ``rate``/``count``/CI.

    A similarity is not a binomial proportion, so the four persona facets keep
    the value-type shape specs 032/033 established (and which the viewer's
    value-type render branch already reads) rather than being forced into the
    rate shape. The denominator is the pooled pair count behind the value —
    without it a mean is unreadable.
    """
    result = build_bench_record(
        _summary(sem=(0.42, 0.9, 75)), config=_BenchConfigStub()
    )
    facets = result.metrics[metric_key]

    assert set(facets) == {value_key, "denominator"}
    assert isinstance(facets[value_key], float)
    assert facets["denominator"] == 75


def test_build_bench_record_of_a_run_that_measured_nothing_has_no_metrics() -> None:
    """A degenerate run (no pairs scored) records NO facets rather than 0.0s.

    The zero-denominator edge: every roster failed, so there is no pooled pair
    count and therefore no measurement. Recording ``0.0`` here would read as
    "perfectly varied personas", which is the exact opposite of the truth — so
    the facets are omitted and the attempted/completed pair is what says the run
    was degenerate.
    """
    result = build_bench_record(
        _summary(lex=None, attempted=3, completed=0), config=_BenchConfigStub()
    )

    assert result.metrics == {}
    assert (result.games_attempted, result.games_completed) == (3, 0)
    assert result.games_failed_early == 3


def test_bench_record_renders_with_the_kind_and_without_the_game_blocks() -> None:
    """End-to-end through the REAL renderer: labelled kind, no hollow game blocks.

    The mapping and the renderer only matter together — this is the joint
    assertion that a mapped bench record produces a ledger document a reviewer
    can tell apart from a played game at a glance. Pure: renders the text, writes
    nothing.
    """
    result = build_bench_record(
        _summary(sem=(0.42, 0.9, 75)), config=_BenchConfigStub()
    )

    doc = blunder_eval.render_record(result, "2026-08-31")

    assert "  kind: 'persona-bench'" in doc
    assert "outcomes:" not in doc
    assert "vote_activity:" not in doc
    assert "transcript_dir" not in doc
    # The measured values survive the write (the value-type write-back the
    # renderer used to drop — see tests/test_blunder_eval.py).
    assert "    mean: 0.1234" in doc
    assert "    denominator: 75" in doc


def test_main_with_record_appends_exactly_one_document(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stub_provenance: _ProvenanceCalls,
) -> None:
    """``--record`` present ⇒ ONE labelled document is appended to the ledger.

    Exactly one: the record count rises by one document separator, the appended
    document carries ``run.kind: 'persona-bench'``, and the run still reports its
    findings on screen. Driven under the ``safe_llm`` fakes against a ``tmp_path``
    ledger, so no model and no committed file is reached.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    ledger = tmp_path / "ledger.yaml"
    prior = _seed_ledger(ledger)
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)

    rc = main(
        ["--provider", "bedrock", "--rosters", "2", "--diversity", "on", "--record"]
    )

    assert rc == 0
    text = ledger.read_text(encoding="utf-8")
    # One prior document + exactly one appended = two separators.
    assert text.count("---\n") == prior.count("---\n") + 1
    # The appended document is a labelled bench record.
    appended = text[len(prior) :]
    assert "  kind: 'persona-bench'" in appended
    assert "outcomes:" not in appended
    # And the run said where it wrote.
    assert str(ledger) in capsys.readouterr().out


def test_main_with_record_leaves_every_prior_document_unchanged(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stub_provenance: _ProvenanceCalls,
) -> None:
    """The ledger is append-only: a prior document survives byte-for-byte.

    The committed-data rule (functional-spec §2, "Recording never disturbs what is
    already in the history"): the appender opens in ``'a'`` mode, so the prior
    text must remain an exact PREFIX of the file — no rewrite, no reorder, no
    re-render of anything already recorded.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    ledger = tmp_path / "ledger.yaml"
    prior = _seed_ledger(ledger)
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)

    main(["--provider", "bedrock", "--rosters", "2", "--diversity", "on", "--record"])

    text = ledger.read_text(encoding="utf-8")
    assert text.startswith(prior)
    assert len(text) > len(prior)


def test_main_with_record_twice_accumulates_two_documents(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stub_provenance: _ProvenanceCalls,
) -> None:
    """Several kept runs each add their OWN entry; none replaces an earlier one.

    Functional-spec §2, third acceptance criterion of the recording requirement.
    Also covers the first-use path: the ledger file does not exist before the
    first append, so the appender creates it (and its parent) rather than failing.
    """
    fake_small(outputs=_rosters(4))
    fake_large(personas=_DISTINCT_PERSONAS)

    ledger = tmp_path / "fresh" / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)
    argv = ["--provider", "bedrock", "--rosters", "2", "--diversity", "on", "--record"]

    main(argv)
    after_first = ledger.read_text(encoding="utf-8")
    main(argv)
    after_second = ledger.read_text(encoding="utf-8")

    assert after_first.count("---\n") == 1
    assert after_second.count("---\n") == 2
    # The first record was not rewritten by the second append.
    assert after_second.startswith(after_first)
    assert after_second.count("kind: 'persona-bench'") == 2


def test_main_with_record_does_not_touch_the_real_committed_ledger(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stub_provenance: _ProvenanceCalls,
) -> None:
    """Belt-and-braces over the redirect: the repo's committed ledger is untouched.

    The mirror of the same guard in the spec-031/032/033 suites. If the redirect
    ever stops reaching ``append_record`` (an early-bound signature default was
    the original leak), this test is the one that catches it before ~25 more
    synthetic records land in a committed file.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    real_ledger = blunder_eval.LEDGER_PATH
    before = real_ledger.read_bytes() if real_ledger.exists() else None
    ledger = tmp_path / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)
    # The redirect must point away from the repo's committed ledger.
    assert blunder_eval.LEDGER_PATH != real_ledger
    assert blunder_eval.LEDGER_PATH.parent == tmp_path

    main(["--provider", "bedrock", "--rosters", "2", "--diversity", "on", "--record"])

    # The record landed in the temp ledger...
    assert ledger.exists()
    assert "kind: 'persona-bench'" in ledger.read_text(encoding="utf-8")
    # ...and nowhere else.
    after = real_ledger.read_bytes() if real_ledger.exists() else None
    assert before == after


# ===========================================================================
# Spec 036, Slice 2 — the generation counts, the A/B conditions, provenance
# and the note.
#
# Slice 1 got a labelled bench record into the ledger; Slice 2 makes it
# *comparable*:
#
# 1. **The generation counts.** ``generation`` carries ``collisions`` /
#    ``regenerations`` — the process figures that carried the spec-034 result
#    (2-in-10 rosters shipping a near-duplicate → 0-in-10). Both are ALWAYS
#    written, explicit zeroes included: a measured ``collisions: 0`` is the
#    headline finding of a diversity-on run, not an absence.
# 2. **The A/B conditions.** ``settings.persona`` names the arm and the three
#    knobs, so a flag-on record and a flag-off record are readable AS A PAIR.
#    ``diversity_enabled`` is the arm the run was *invoked* with — reading the
#    ambient config default instead would silently mislabel every flag-off arm,
#    which is the one failure that would make the whole pair meaningless.
# 3. **Provenance.** ``main`` gathers ``code`` / ``provider`` with the EVAL's own
#    collectors and injects them, so a bench record is attributable exactly the
#    way a game record is.
# 4. **The note.** ``--note`` reaches the record's last key.
#
# **Provenance is INJECTED, never collected, throughout.**
# ``collect_code_provenance`` shells out to ``git`` three times and
# ``collect_provider_provenance`` reaches the local ollama HTTP endpoints on the
# ollama path — neither belongs in the offline suite, so every ``main`` test
# below installs spies (:func:`stub_provenance`) and asserts on what the
# harness *did with* the collected blocks. And every test that can reach the
# appender redirects ``blunder_eval.LEDGER_PATH`` at ``tmp_path``
# (:func:`tmp_ledger`).
# ===========================================================================


class _PersonaConfigStub(_BenchConfigStub):
    """The ollama-tier stub plus the four spec-034 persona knobs.

    Values deliberately DIFFER from the shipped config defaults (0.6 / 2 / 1.0)
    so an assertion can only pass if the mapping genuinely read this config
    rather than hard-coding the defaults. ``persona_diversity_enabled`` is the
    *ambient default* — the value the mapping must NOT record when the run was
    invoked with the other arm.
    """

    persona_diversity_enabled = True
    persona_collision_threshold = 0.55
    persona_regen_attempts = 3
    persona_temperature = 1.2


def _config_with_diversity_default(default: bool) -> _PersonaConfigStub:
    """A persona config stub whose ambient diversity default is ``default``."""
    stub = _PersonaConfigStub()
    stub.persona_diversity_enabled = default
    return stub


# An injected ``code`` block, shaped exactly as ``collect_code_provenance``
# returns one (commit / branch / dirty). Injected rather than collected: the real
# collector runs three ``git`` subprocesses.
_INJECTED_CODE: dict[str, object] = {
    "commit": "abc123def4567890abc123def4567890abc123de",
    "branch": "spec-036",
    "dirty": False,
}

# An injected ``provider`` block in the ENRICHED ollama shape
# ``collect_provider_provenance`` returns (flat identity + per-model digests +
# server version). Injected rather than collected: the real collector issues two
# HTTP GETs against the local ollama server, which the suite must never do.
_INJECTED_PROVIDER: dict[str, object] = {
    "name": "ollama",
    "large_model": "qwen3-coder:30b",
    "small_model": "qwen2.5:3b",
    "models": {
        "qwen3-coder:30b": {
            "name": "qwen3-coder:30b",
            "digest": "sha256:1111111111111111",
        },
        "qwen2.5:3b": {"name": "qwen2.5:3b", "digest": "sha256:2222222222222222"},
    },
    "server_version": "0.12.3",
}


@pytest.fixture
def tmp_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ``blunder_eval.LEDGER_PATH`` at ``tmp_path``; return the path.

    MANDATORY for any test that can reach ``append_record``, not hygiene: ~25
    synthetic records have already leaked into the repo-committed
    ``evals/blunder-ledger.yaml`` from exactly this bug class. The path is
    resolved by ``append_record`` at CALL time, so this ``setattr`` reaches even
    the no-arg call inside ``main``.
    """
    ledger = tmp_path / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)
    return ledger


@dataclass(slots=True)
class _ProvenanceCalls:
    """What the spied provenance collectors were asked for."""

    code_args: list[Path] = field(default_factory=list)
    provider_args: list[tuple[str, str, str, str]] = field(default_factory=list)


@pytest.fixture
def stub_provenance(monkeypatch: pytest.MonkeyPatch) -> _ProvenanceCalls:
    """Replace both provenance collectors in ``persona_bench`` with spies.

    The collectors are bound INTO the ``persona_bench`` namespace by its
    module-level ``from ... import``, so this patches the call site the harness
    actually uses. Two things it buys: the suite never runs ``git`` and never
    reaches ``localhost:11434``, and the returned blocks are known values, so an
    assertion can prove they travelled all the way into the written record.
    """
    calls = _ProvenanceCalls()

    def _fake_code(repo_root: Path) -> dict[str, object]:
        calls.code_args.append(repo_root)
        return dict(_INJECTED_CODE)

    def _fake_provider(
        provider: str, large_model: str, small_model: str, base_url: str
    ) -> dict[str, object]:
        calls.provider_args.append((provider, large_model, small_model, base_url))
        return dict(_INJECTED_PROVIDER)

    monkeypatch.setattr(persona_bench, "collect_code_provenance", _fake_code)
    monkeypatch.setattr(persona_bench, "collect_provider_provenance", _fake_provider)
    return calls


def _section_lines(doc: str, name: str) -> list[str]:
    """The lines of ONE top-level block of a rendered record, header included.

    A top-level block runs from its unindented ``name:`` header to the next
    unindented line, so this slices out e.g. the whole ``code:`` section for a
    byte-exact comparison between two records.
    """
    lines = doc.splitlines()
    start = lines.index(f"{name}:")
    end = start + 1
    while end < len(lines) and lines[end].startswith(" "):
        end += 1
    return lines[start:end]


def _game_shaped_result() -> blunder_eval.EvalResult:
    """A GAME-shaped ``EvalResult`` carrying the same injected provenance blocks.

    The comparison target for "a bench record carries the same ``code`` and
    ``provider`` block shape a game run does": same injected blocks, no
    ``kind``, rate-shaped metrics and a populated ``outcomes``.
    """
    return blunder_eval.EvalResult(
        provider="ollama",
        large_model="qwen3-coder:30b",
        small_model="qwen2.5:3b",
        games_attempted=5,
        games_completed=5,
        games_failed_early=0,
        metrics={"repetition": {"rate": 0.4, "count": 2, "denominator": 5}},
        outcomes={
            "games": 5,
            "law_abiding": {"wins": 3, "rate": 0.6},
            "mafia": {"wins": 2, "rate": 0.4},
            "runaway": 0,
            "draw": 0,
            "no_winner": 0,
        },
        code=dict(_INJECTED_CODE),
        provider_block=dict(_INJECTED_PROVIDER),
        duration_seconds=900.0,
    )


# ---------------------------------------------------------------------------
# The ``generation`` block — the counts, and the explicit zero
# ---------------------------------------------------------------------------


def test_build_bench_record_maps_the_pooled_generation_counts() -> None:
    """``generation`` carries the summary's pooled collision / regeneration counts.

    Its own small block rather than a fold into ``quality`` (run health) or
    ``metrics`` (the versioned scored family): "how many casts shipped an
    over-similar pair" is neither, and it is the figure that carried the
    spec-034 comparison a similarity mean alone would have lost.
    """
    result = build_bench_record(
        _summary(collisions=4, regenerations=9), config=_PersonaConfigStub()
    )

    assert result.generation == {"collisions": 4, "regenerations": 9}


def test_build_bench_record_generation_counts_are_ints() -> None:
    """Both counts are plain ``int`` — the on-disk type every reader expects."""
    result = build_bench_record(_summary(), config=_PersonaConfigStub())

    assert [type(v) for v in result.generation.values()] == [int, int]


def test_build_bench_record_records_a_measured_zero_collision_count() -> None:
    """A zero-collision run records ``collisions: 0`` — the finding, not an absence.

    The whole point of the block: with diversity on, casts containing a
    near-duplicate fell from two-in-ten to NONE in ten. If a zero count were
    omitted (the ``metrics`` absent-≠-zero rule, wrongly applied here) the
    decisive half of that comparison would be unrecordable — the record would say
    "collisions were not measured" where the truth is "collisions were measured
    and there were none".
    """
    result = build_bench_record(
        _summary(collisions=0, regenerations=0), config=_PersonaConfigStub()
    )

    assert result.generation == {"collisions": 0, "regenerations": 0}
    # ...and it survives the write rather than being dropped as a falsy value.
    doc = blunder_eval.render_record(result, "2026-08-31")
    assert "generation:" in doc.splitlines()
    assert "  collisions: 0" in doc
    assert "  regenerations: 0" in doc


# ---------------------------------------------------------------------------
# ``settings.persona`` — the conditions that make an A/B pair readable
# ---------------------------------------------------------------------------


def test_build_bench_record_settings_carry_the_resolved_persona_knobs() -> None:
    """The three tunables are read off the resolved config, not hard-coded.

    A similarity mean compared across a changed collision bar or a changed
    persona temperature is not a comparison at all, so the record states the bar
    it ran under. The stub's values deliberately differ from the shipped defaults
    (0.6 / 2 / 1.0), so this can only pass by genuinely reading the config.
    """
    result = build_bench_record(_summary(), config=_PersonaConfigStub())

    assert result.settings["persona"] == {
        "diversity_enabled": True,
        "collision_threshold": 0.55,
        "regen_attempts": 3,
        "temperature": 1.2,
    }


def test_build_bench_record_persona_knobs_sit_in_a_settings_sub_map() -> None:
    """The knobs are nested under ``settings.persona``, beside the flat keys.

    The ``settings.lineup`` precedent: a one-level sub-map, so the flat settings
    keys a game record carries keep their exact meaning and a reader can tell the
    persona conditions apart from the run settings at a glance.
    """
    result = build_bench_record(_summary(), config=_PersonaConfigStub())

    assert isinstance(result.settings["persona"], dict)
    # The flat keys a game record carries are all still there...
    flat_keys = {"large_model", "small_model", "base_url", "games", "seed", "max_days"}
    assert flat_keys <= set(result.settings)
    # ...and the persona knobs did NOT leak into them.
    assert "diversity_enabled" not in result.settings


@pytest.mark.parametrize(
    "arm",
    [pytest.param(True, id="invoked-on"), pytest.param(False, id="invoked-off")],
)
def test_build_bench_record_records_the_invoked_arm_not_the_config_default(
    arm: bool,
) -> None:
    """``diversity_enabled`` is the ARM the run ran, never the ambient default.

    ``run_bench`` passes the ``--diversity`` value straight into
    ``generate_personas``, so the config default can disagree with what actually
    ran. Both arms are swept against a config whose default is the OPPOSITE
    value, so the recorded flag can only be right by coming from the summary —
    reading ``config.persona_diversity_enabled`` would mislabel *every* record
    here and make an A/B pair read as two runs of the same arm.
    """
    summary = _summary()
    summary.diversity_enabled = arm

    result = build_bench_record(
        summary, config=_config_with_diversity_default(not arm)
    )

    assert result.settings["persona"]["diversity_enabled"] is arm


def test_build_bench_record_unresolvable_persona_knobs_are_null_not_defaulted() -> None:
    """A config without the knobs records ``None``, never a plausible default.

    ``getattr(..., None)`` (the ``max_days`` / ``lineup`` precedent) keeps the
    harness from crashing on a provenance gap — but the gap must read as
    genuinely absent. Recording 0.6 / 2 / 1.0 for a run whose bar nobody knows
    would turn the record's conditions into a fiction, which is worse than a
    blank.
    """
    result = build_bench_record(_summary(), config=_BenchConfigStub())
    persona = result.settings["persona"]

    assert persona["collision_threshold"] is None
    assert persona["regen_attempts"] is None
    assert persona["temperature"] is None
    # The arm itself always comes from the summary, so it is never null.
    assert persona["diversity_enabled"] is True


# ---------------------------------------------------------------------------
# Provenance — injected, and identical in shape to a game record's
# ---------------------------------------------------------------------------


def test_build_bench_record_carries_the_injected_provenance_blocks_verbatim() -> None:
    """The two blocks travel into the record unmodified — no second implementation.

    ``build_bench_record`` stays pure: ``main`` collects with the eval's own
    collectors and hands the blocks in, exactly as ``run_eval`` gathers them once
    and hands them to its ``EvalResult``.
    """
    result = build_bench_record(
        _summary(),
        config=_PersonaConfigStub(),
        code=dict(_INJECTED_CODE),
        provider_block=dict(_INJECTED_PROVIDER),
    )

    assert result.code == _INJECTED_CODE
    assert result.provider_block == _INJECTED_PROVIDER


def test_build_bench_record_without_provenance_degrades_gracefully() -> None:
    """No injected blocks ⇒ empty dicts and the renderer's degraded shape.

    What keeps the mapping unit-testable with no git and no server: an omitted
    block leaves the field empty and ``render_record`` falls back to null
    commit/branch plus the flat provider identity, rather than the mapping
    hand-rolling a second collector that would drift from the game path.
    """
    result = build_bench_record(_summary(), config=_PersonaConfigStub())

    assert result.code == {}
    assert result.provider_block == {}

    doc = blunder_eval.render_record(result, "2026-08-31")
    assert _section_lines(doc, "code") == [
        "code:",
        "  commit: null",
        "  branch: null",
        "  dirty: false",
    ]
    # The flat identity still names the models that ran.
    assert "  name: 'ollama'" in doc


def test_bench_record_code_and_provider_sections_match_a_game_records() -> None:
    """A bench record's ``code`` / ``provider`` blocks render EXACTLY as a game's.

    Tech-spec §4, "Provenance: a recorded bench run carries the same ``code`` and
    ``provider`` block shape a game run does". Asserted as byte-equal section
    text with the same blocks fed to both shapes, which is what proves the
    renderer applies no kind-conditional treatment to provenance: a bench record
    is attributable to a commit and a model fingerprint on exactly the same terms
    as a played game — including the enriched ollama ``models`` digests and
    ``server_version``, the parts a hand-rolled collector would have dropped.
    """
    bench_doc = blunder_eval.render_record(
        build_bench_record(
            _summary(),
            config=_PersonaConfigStub(),
            code=dict(_INJECTED_CODE),
            provider_block=dict(_INJECTED_PROVIDER),
        ),
        "2026-08-31",
    )
    game_doc = blunder_eval.render_record(_game_shaped_result(), "2026-08-31")

    assert _section_lines(bench_doc, "code") == _section_lines(game_doc, "code")
    assert _section_lines(bench_doc, "provider") == _section_lines(game_doc, "provider")
    # And the enriched detail is really there, not an empty match of two blanks.
    assert "  server_version: '0.12.3'" in bench_doc
    assert "    digest: 'sha256:1111111111111111'" in bench_doc


def test_persona_bench_reuses_the_evals_own_provenance_collectors() -> None:
    """The bench imports the eval's collectors rather than re-implementing them.

    "Do not hand-roll" is the load-bearing instruction (tech-spec §2 B): a second
    collector would drift, and the two kinds of record would stop being
    comparable. An identity check is the cheapest possible guard and needs no
    ``git`` and no server.
    """
    assert persona_bench.collect_code_provenance is blunder_eval.collect_code_provenance
    assert (
        persona_bench.collect_provider_provenance
        is blunder_eval.collect_provider_provenance
    )
    assert persona_bench.warn_if_dirty is blunder_eval.warn_if_dirty


def test_main_with_record_collects_provenance_with_the_evals_collectors(
    env: Path,
    fake_small,
    fake_large,
    stub_provenance: _ProvenanceCalls,
    tmp_ledger: Path,
) -> None:
    """``--record`` gathers both blocks through the eval's collectors and writes them.

    The spies stand in for a ``git`` subprocess and two HTTP GETs, and prove
    three things at once: the code collector is pointed at the repo root, the
    provider collector is handed the run's provider plus the resolved tier ids,
    and both returned blocks reach the ledger document verbatim.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    rc = main(
        ["--provider", "bedrock", "--rosters", "2", "--diversity", "on", "--record"]
    )

    assert rc == 0
    # Collected once each, the code one against the repo root the eval uses.
    assert stub_provenance.code_args == [blunder_eval._REPO_ROOT]
    (provider, large_model, small_model, _base_url) = (
        stub_provenance.provider_args[0]
    )
    assert len(stub_provenance.provider_args) == 1
    assert provider == "bedrock"
    assert large_model and small_model

    # ...and both blocks landed in the written record.
    doc = tmp_ledger.read_text(encoding="utf-8")
    assert f"  commit: '{_INJECTED_CODE['commit']}'" in doc
    assert "  branch: 'spec-036'" in doc
    assert "  dirty: false" in doc
    assert "  server_version: '0.12.3'" in doc


def test_main_without_record_collects_no_provenance(
    env: Path,
    fake_small,
    fake_large,
    stub_provenance: _ProvenanceCalls,
    tmp_ledger: Path,
) -> None:
    """The throwaway path pays for no provenance at all — no git, no HTTP.

    Collection happens only on the ``--record`` branch, deliberately: the bench's
    value is dev-loop speed, and most runs are throwaway. This also keeps the
    default path (and any test that drives it) provably away from ``git`` and
    from the ollama endpoints the provider collector would GET.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    rc = main(["--provider", "bedrock", "--rosters", "2", "--diversity", "on"])

    assert rc == 0
    assert stub_provenance.code_args == []
    assert stub_provenance.provider_args == []
    assert not tmp_ledger.exists()


# ---------------------------------------------------------------------------
# ``--note`` — the one human-authored field, rendered last
# ---------------------------------------------------------------------------


def test_build_bench_record_renders_the_note_as_the_last_key() -> None:
    """A ``--note`` reaches ``notes``, the record's LAST key.

    Numbers alone do not say why a measurement was taken; the note is what tells
    a reader months later which question it answered. Last-key placement is the
    existing renderer's, so this pins that the bench feeds the same field rather
    than inventing a second one.
    """
    result = build_bench_record(
        _summary(), config=_PersonaConfigStub(), note="spec 036 first recorded bench"
    )

    assert result.notes == "spec 036 first recorded bench"
    doc = blunder_eval.render_record(result, "2026-08-31")
    assert doc.rstrip("\n").splitlines()[-1] == (
        "notes: 'spec 036 first recorded bench'"
    )


def test_build_bench_record_without_a_note_renders_an_empty_last_key() -> None:
    """No note ⇒ ``notes: ''`` last — present but empty, nothing missing.

    Functional-spec §2: "a measurement recorded without a note reads normally with
    no note and nothing missing". Empty-but-present is also what visibly invites
    hand-editing the one human-mutable field afterwards.
    """
    result = build_bench_record(_summary(), config=_PersonaConfigStub())

    assert result.notes == ""
    doc = blunder_eval.render_record(result, "2026-08-31")
    assert doc.rstrip("\n").splitlines()[-1] == "notes: ''"


@pytest.mark.parametrize(
    ("argv_extra", "expected_last_line"),
    [
        pytest.param(
            ["--note", "why this run existed"],
            "notes: 'why this run existed'",
            id="note-given",
        ),
        pytest.param([], "notes: ''", id="note-omitted"),
    ],
)
def test_main_with_record_writes_the_note_into_the_ledger_last(
    env: Path,
    fake_small,
    fake_large,
    stub_provenance: _ProvenanceCalls,
    tmp_ledger: Path,
    argv_extra: list[str],
    expected_last_line: str,
) -> None:
    """``--note`` travels through ``main`` to the appended document's last line.

    Both arms are swept because the absence case is the one a pass-through bug
    would break silently: an unset note must still render its empty-but-present
    last key rather than dropping the field.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    rc = main(
        [
            "--provider",
            "bedrock",
            "--rosters",
            "2",
            "--diversity",
            "on",
            "--record",
            *argv_extra,
        ]
    )

    assert rc == 0
    doc = tmp_ledger.read_text(encoding="utf-8")
    assert doc.rstrip("\n").splitlines()[-1] == expected_last_line


# ---------------------------------------------------------------------------
# End-to-end: the recorded document carries the arm and the counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arm", "rendered"),
    [
        pytest.param("on", "    diversity_enabled: true", id="arm-on"),
        pytest.param("off", "    diversity_enabled: false", id="arm-off"),
    ],
)
def test_main_with_record_writes_the_invoked_arm_not_the_env_default(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
    stub_provenance: _ProvenanceCalls,
    tmp_ledger: Path,
    arm: str,
    rendered: str,
) -> None:
    """End-to-end: the written record states the ``--diversity`` arm that ran.

    ``GRAPHIA_PERSONA_DIVERSITY`` is pinned to the OPPOSITE value for each arm,
    so the recorded flag can only be correct by coming from the invocation. This
    is the assertion that protects the A/B: get it wrong and every flag-off record
    claims to be a flag-on one, making the two sides of the spec-034 comparison
    indistinguishable after the fact.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)
    # The ambient default disagrees with the arm under test. ``run_bench`` passes
    # the CLI value into ``generate_personas`` directly, so this changes nothing
    # about what runs — only what a config-reading bug would record.
    monkeypatch.setenv("GRAPHIA_PERSONA_DIVERSITY", "0" if arm == "on" else "1")

    rc = main(
        ["--provider", "bedrock", "--rosters", "2", "--diversity", arm, "--record"]
    )

    assert rc == 0
    doc = tmp_ledger.read_text(encoding="utf-8")
    assert rendered in doc.splitlines()


def test_main_with_record_writes_the_generation_block_and_the_persona_knobs(
    env: Path,
    fake_small,
    fake_large,
    stub_provenance: _ProvenanceCalls,
    tmp_ledger: Path,
) -> None:
    """The end-to-end document carries ``generation`` and ``settings.persona``.

    The joint check that Slice 2's two blocks survive the whole path — real
    ``run_bench`` under the offline fakes → ``build_bench_record`` → the real
    renderer → ``append_record`` — and land in the documented band: ``generation``
    after ``quality`` and before ``metrics``, the persona knobs inside
    ``settings``. Everything a reviewer needs to compare this record against
    another is present in the file, not just in the objects.
    """
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    rc = main(
        ["--provider", "bedrock", "--rosters", "2", "--diversity", "on", "--record"]
    )

    assert rc == 0
    lines = tmp_ledger.read_text(encoding="utf-8").splitlines()

    generation_i = lines.index("generation:")
    assert lines.index("quality:") < generation_i < lines.index("metrics:")
    # Both counts present as integers (the run's real, offline-fake-driven values).
    assert lines[generation_i + 1].startswith("  collisions: ")
    assert lines[generation_i + 2].startswith("  regenerations: ")
    assert int(lines[generation_i + 1].split(": ")[1]) >= 0
    assert int(lines[generation_i + 2].split(": ")[1]) >= 0

    persona_i = lines.index("  persona:")
    assert lines.index("settings:") < persona_i < lines.index("quality:")
    knob_lines = lines[persona_i + 1 : persona_i + 5]
    assert [ln.split(":")[0].strip() for ln in knob_lines] == [
        "diversity_enabled",
        "collision_threshold",
        "regen_attempts",
        "temperature",
    ]


# ===========================================================================
# Spec 036 §2 — the paid semantic instrument degrades gracefully.
#
# The embeddings client is a CLOUD dependency on an otherwise-local run, so
# expired credentials must not destroy a bench whose lexical measurement already
# succeeded. `run_eval` has had this treatment since spec 033; the bench never
# inherited it, and the gap was invisible because the paid arm is rarely run.
# ===========================================================================


def _raise_no_creds(_texts: list[str]) -> list[list[float]]:
    raise RuntimeError("no AWS credentials — embeddings unavailable")


def test_semantic_unavailable_still_completes_and_reports(
    env: Path, fake_small, fake_large, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run finishes, prints its summary, and keeps the free lexical figures."""
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)
    monkeypatch.setattr(persona_bench, "_embed_documents", _raise_no_creds)

    summary = persona_bench.run_bench(
        provider="ollama", rosters=2, diversity_enabled=True, semantic=True
    )

    assert summary.rosters_completed == 2, "a cloud failure must not fail the rosters"
    assert summary.persona_lex_mean is not None, "the free measurement still stands"
    assert summary.persona_sem_mean is None, "absent, not a misleading zero"
    assert summary.persona_sem_peak is None
    assert "UNAVAILABLE" in capsys.readouterr().err, "the degradation is announced"


def test_semantic_unavailable_is_not_retried_on_every_roster(
    env: Path, fake_small, fake_large, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failure disables the instrument — a credential timeout per roster is slow."""
    fake_small(outputs=_rosters(4))
    fake_large(personas=_DISTINCT_PERSONAS)
    calls: list[int] = []

    def _counting_raise(texts: list[str]) -> list[list[float]]:
        calls.append(1)
        raise RuntimeError("no AWS credentials")

    monkeypatch.setattr(persona_bench, "_embed_documents", _counting_raise)
    persona_bench.run_bench(
        provider="ollama", rosters=4, diversity_enabled=True, semantic=True
    )
    assert len(calls) == 1, f"asked {len(calls)} times; must stop after the first failure"


def test_semantic_unavailable_record_omits_the_pair_entirely(
    env: Path, fake_small, fake_large, monkeypatch: pytest.MonkeyPatch, tmp_ledger: Path,
) -> None:
    """A degraded run is still recordable, with the semantic facets simply absent."""
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)
    monkeypatch.setattr(persona_bench, "_embed_documents", _raise_no_creds)

    summary = persona_bench.run_bench(
        provider="ollama", rosters=2, diversity_enabled=True, semantic=True
    )
    result = persona_bench.build_bench_record(summary, _BenchConfigStub())

    assert "persona_lex_mean" in result.metrics
    assert "persona_sem_mean" not in result.metrics
    assert "persona_sem_peak" not in result.metrics


def test_not_requesting_semantic_is_not_reported_as_unavailable(
    env: Path, fake_small, fake_large, capsys: pytest.CaptureFixture[str],
) -> None:
    """The normal free path must not look like a degraded one."""
    fake_small(outputs=_rosters(2))
    fake_large(personas=_DISTINCT_PERSONAS)

    summary = persona_bench.run_bench(
        provider="ollama", rosters=2, diversity_enabled=True, semantic=False
    )

    assert summary.persona_sem_mean is None
    assert not any(r.sem_unavailable for r in summary.per_roster)
    assert "UNAVAILABLE" not in capsys.readouterr().err
