"""Offline unit tests for the AI Blunder Tracking harness (spec 011, Slice 1).

Locks in the Slice-1 surface of ``src/graphia/tools/blunder_eval.py`` — the
make-gated quality-ledger run — **without ever reaching a real model, the
network, or a live game**. Three concerns are covered:

1. **CLI provider forcing + cloud-store isolation** — invoking the in-process
   pre-run setup (the isolation helper directly, and ``main`` driven only as far
   as the pre-game env mutation with ``run_eval`` / ``load_config`` / the ollama
   preflight stubbed) pops **all** five cloud-store env vars *plus*
   ``GRAPHIA_REMOTE`` for **both** ``ollama`` and ``bedrock`` (Bedrock needs the
   isolation explicitly — the config offline-gate only covers ollama), forces
   ``GRAPHIA_LLM_PROVIDER`` to the chosen value, and routes the model overrides
   onto the ``GRAPHIA_OLLAMA_*`` tier env. ``_CLOUD_STORE_ENV_VARS`` is the
   single source of truth the tests assert against.
2. **The pure repetition scorer** — ``score_repetition`` on synthetic AI-line
   lists: a near-duplicate pair next to a distinct line, an all-distinct list,
   the empty list (no ``ZeroDivisionError``), and a *name-masking* case proving
   the spec-009 measure is name-masked (the same short sentence with two
   different player names counts as a near-dup only when the names are supplied
   to be masked).
3. **The hand-rendered write-only YAML ledger** — ``render_record`` emits the
   documented fixed key order with correctly-typed scalars (model ids carrying
   ``:`` / digits single-quoted, floats stable, ints bare, ``metrics_version``
   present), and a top-level ``notes`` free-text field rendered LAST: present-
   but-empty (``notes: ''``) when unset, a safely-quoted scalar for a single-
   line ``--note``, and a YAML literal block scalar (``notes: |``) when multi-
   line; ``append_record`` to a ``tmp_path`` ledger twice accumulates two
   ``---``-separated documents without rewriting the first.

Spec 017 (*Eval Transcript Preservation*), Slice 1 Task 4 EXTENDS this file with
three offline, model-free concerns:

4. **Streaming capture preserves multiple Nights' picks** — a mocked eval game
   (real graph built with ``fake_large`` / ``fake_small``, RNG-controlled
   pointing via a live-state dispatcher) is driven through MORE THAN ONE Night
   while taping the per-super-step ``graph.stream(stream_mode="updates")`` log
   into a ``_GameCapture.events`` list (the exact ``on_update`` sink
   ``_play_one_game`` threads). The regression: each Night's ``night_open``
   RESETS ``night_round_picks`` / ``night_rounds_log``, so a final-state read
   would hold only the LAST Night's picks — but the captured stream log still
   carries the EARLIER Night's pointing, and the rendered transcript shows both.

5. **Storage + ledger link** — a mocked ``run_eval`` (``_play_one_game`` stubbed
   to return hand-built ``_GameCapture``s, so no graph or provider is built)
   writes ``<transcripts_root>/<run-id>/game-NN.txt`` into a ``tmp_path`` with
   the per-run-dir + zero-padded naming, the files contain the rendered
   transcript text, and the ledger record carries ``run.transcript_dir ==
   "<run-id>"``. The real ``evals/transcripts/`` is never touched.

6. **Cleanup affordance** — ``clean_transcripts`` over a temp transcripts root
   inside a temp git repo: a committed (tracked) run dir is preserved while an
   untracked one is removed, and the returned list names exactly what it dropped.

The synthetic ``EvalResult`` is built from the real dataclass (imported), so a
field rename breaks these tests honestly. The repo ships no stdlib YAML and
deliberately adds no parser, so the renderer is asserted structurally
(line order / substring anchors), never round-tripped through PyYAML.

Everything is stubbed and offline: no provider client is ever constructed and
the autouse ``safe_llm`` net is left intact — these tests never go near an LLM
call site.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.types import Command

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    METRICS_VERSION,
    PROVIDERS,
    _CLOUD_STORE_ENV_VARS,
    _GameCapture,
    _apply_model_overrides,
    _attach_ci,
    _isolate_cloud_stores,
    append_record,
    clean_transcripts,
    main,
    render_record,
    render_transcript,
    run_eval,
    score_repetition,
    transcript_path,
    wilson_ci,
)

# The non-cloud-store env vars the harness also mutates on the pre-run path.
_PROVIDER_ENV = "GRAPHIA_LLM_PROVIDER"
_REMOTE_ENV = "GRAPHIA_REMOTE"
_OLLAMA_LARGE_ENV = "GRAPHIA_OLLAMA_LARGE_MODEL"
_OLLAMA_SMALL_ENV = "GRAPHIA_OLLAMA_SMALL_MODEL"


@pytest.fixture(autouse=True)
def blunder_env_clean(monkeypatch: pytest.MonkeyPatch):
    """Start each test from a clean slate and fully restore the env afterwards.

    These tests exercise the harness's *real* in-process env mutation: the
    helpers and ``main`` call ``os.environ[...] = ``, ``os.environ.pop(...)``,
    and ``os.environ.setdefault(...)`` **directly** (not via monkeypatch) —
    that direct mutation is the behaviour under test. So a targeted
    ``delenv`` cannot undo it, and an un-restored ``GRAPHIA_LLM_PROVIDER`` /
    ``GRAPHIA_REMOTE`` / ``GRAPHIA_ROLE`` would leak into later tests (e.g. the
    remote-mode / badge suites that rely on the bedrock default).

    The fix is a full snapshot-and-restore of ``os.environ`` around each test,
    on top of a targeted wipe of the vars under test (read off the module
    constant so a newly-isolated var is automatically covered) for a clean
    starting slate that no developer ``.env`` leakage can taint.
    """
    saved = dict(os.environ)
    for var in _CLOUD_STORE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in (
        _REMOTE_ENV,
        _PROVIDER_ENV,
        _OLLAMA_LARGE_ENV,
        _OLLAMA_SMALL_ENV,
        "GRAPHIA_ROLE",
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ===========================================================================
# 1. CLI provider forcing + cloud-store isolation (offline, no client built)
# ===========================================================================


def _set_all_cloud_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire-env a deployed stack: every cloud-store id + remote mode set."""
    for var in _CLOUD_STORE_ENV_VARS:
        monkeypatch.setenv(var, f"{var.lower()}-deadbeef")
    monkeypatch.setenv(_REMOTE_ENV, "1")


def test_isolate_cloud_stores_pops_every_constant_var_and_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_isolate_cloud_stores`` clears all five ids *and* ``GRAPHIA_REMOTE``.

    Asserts against ``_CLOUD_STORE_ENV_VARS`` as the single source of truth, so
    a var added to the constant is automatically required to be popped.
    """

    _set_all_cloud_stores(monkeypatch)
    # Sanity: the precondition really is "all set" before isolation runs.
    for var in _CLOUD_STORE_ENV_VARS:
        assert var in os.environ

    _isolate_cloud_stores()

    for var in _CLOUD_STORE_ENV_VARS:
        assert var not in os.environ, f"{var} should be popped"
    assert _REMOTE_ENV not in os.environ


def test_isolate_cloud_stores_is_idempotent_on_a_clean_env() -> None:
    """Popping when nothing is set is a silent no-op (no KeyError)."""

    _isolate_cloud_stores()  # blunder_env_clean already cleared everything

    for var in _CLOUD_STORE_ENV_VARS:
        assert var not in os.environ
    assert _REMOTE_ENV not in os.environ


@pytest.mark.parametrize("provider", PROVIDERS, ids=list(PROVIDERS))
def test_pre_run_setup_isolates_and_forces_provider_for_both_providers(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Driving ``main`` to the pre-game setup isolates + forces the provider.

    For **both** ``ollama`` and ``bedrock`` (Bedrock is the whole point — the
    config offline-gate only covers ollama, so the harness must pop the stores
    itself): all five cloud-store vars and ``GRAPHIA_REMOTE`` are popped and
    ``GRAPHIA_LLM_PROVIDER`` is forced to the chosen value.

    ``run_eval`` is stubbed to a sentinel that captures the live env at the
    moment the harness *would* start playing games — so no graph is built and no
    provider client is ever constructed. ``load_config`` is stubbed to a bare
    object and the ollama preflight is stubbed to a no-op, so nothing reaches a
    config branch that could touch AWS / Ollama / the network.
    """

    _set_all_cloud_stores(monkeypatch)

    captured: dict[str, str | None] = {}

    def _fake_run_eval(config: object, args: argparse.Namespace) -> EvalResult:
        # Snapshot the env exactly as the (real) game loop would observe it.
        captured["provider"] = os.environ.get(_PROVIDER_ENV)
        captured["remote"] = os.environ.get(_REMOTE_ENV)
        for var in _CLOUD_STORE_ENV_VARS:
            captured[var] = os.environ.get(var)
        return EvalResult(provider=args.provider)

    monkeypatch.setattr(blunder_eval, "run_eval", _fake_run_eval)
    # load_config is imported inside main() from graphia.config — stub the source.
    monkeypatch.setattr("graphia.config.load_config", lambda: object())
    # The ollama branch imports run_ollama_preflight from graphia.preflight.
    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", lambda cfg: None)

    rc = main(["--provider", provider, "--games", "1"])

    assert rc == 0
    assert captured["provider"] == provider
    assert captured["remote"] is None
    for var in _CLOUD_STORE_ENV_VARS:
        assert captured[var] is None, f"{var} not isolated before run_eval"


def test_apply_model_overrides_routes_onto_ollama_tier_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--large-model`` / ``--small-model`` set the GRAPHIA_OLLAMA_* tier env."""

    _apply_model_overrides("llama3.1:70b", "llama3.2:1b")

    assert os.environ[_OLLAMA_LARGE_ENV] == "llama3.1:70b"
    assert os.environ[_OLLAMA_SMALL_ENV] == "llama3.2:1b"


def test_apply_model_overrides_leaves_unset_env_untouched() -> None:
    """``None`` overrides are inert — neither tier env var is created."""

    _apply_model_overrides(None, None)

    assert _OLLAMA_LARGE_ENV not in os.environ
    assert _OLLAMA_SMALL_ENV not in os.environ


def test_model_overrides_flow_through_main_for_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving ``main --provider ollama --large-model ...`` sets the tier env.

    Confirms the override wiring is reached on the real pre-run path (not only
    via the helper called directly), while ``run_eval`` / ``load_config`` / the
    preflight are stubbed so no client or network is touched.
    """

    captured: dict[str, str | None] = {}

    def _fake_run_eval(config: object, args: argparse.Namespace) -> EvalResult:
        captured[_OLLAMA_LARGE_ENV] = os.environ.get(_OLLAMA_LARGE_ENV)
        captured[_OLLAMA_SMALL_ENV] = os.environ.get(_OLLAMA_SMALL_ENV)
        return EvalResult(provider=args.provider)

    monkeypatch.setattr(blunder_eval, "run_eval", _fake_run_eval)
    monkeypatch.setattr("graphia.config.load_config", lambda: object())
    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", lambda cfg: None)

    main(
        [
            "--provider",
            "ollama",
            "--large-model",
            "qwen3-coder:30b",
            "--small-model",
            "qwen2.5:3b",
        ]
    )

    assert captured[_OLLAMA_LARGE_ENV] == "qwen3-coder:30b"
    assert captured[_OLLAMA_SMALL_ENV] == "qwen2.5:3b"


def test_ollama_provider_runs_the_preflight_before_any_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ollama path calls the boot preflight before ``run_eval``.

    The preflight stub records that it ran and asserts ordering by failing if
    ``run_eval`` had already been entered — the fail-fast guarantee that no game
    time is burned before the local model pair is verified.
    """
    order: list[str] = []

    monkeypatch.setattr(
        "graphia.preflight.run_ollama_preflight",
        lambda cfg: order.append("preflight"),
    )
    monkeypatch.setattr("graphia.config.load_config", lambda: object())
    monkeypatch.setattr(
        blunder_eval,
        "run_eval",
        lambda config, args: (
            order.append("run_eval"),
            EvalResult(provider=args.provider),
        )[1],
    )

    main(["--provider", "ollama", "--games", "1"])

    assert order == ["preflight", "run_eval"]


def test_bedrock_provider_does_not_run_the_ollama_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bedrock path must never invoke the Ollama preflight."""
    monkeypatch.setattr("graphia.config.load_config", lambda: object())
    monkeypatch.setattr(
        blunder_eval, "run_eval", lambda config, args: EvalResult(provider="bedrock")
    )

    def _boom(cfg: object) -> None:
        raise AssertionError("bedrock must not run the Ollama preflight")

    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", _boom)

    assert main(["--provider", "bedrock", "--games", "1"]) == 0


def test_invalid_provider_is_rejected_by_argparse() -> None:
    """Only the two real providers are accepted (argparse ``choices``)."""
    with pytest.raises(SystemExit):
        main(["--provider", "openai"])


# ===========================================================================
# 2. The pure repetition scorer — name-masked spec-009 near-dup at 0.85
# ===========================================================================

# Two lines whose only difference is one trailing word — comfortably above the
# 0.85 difflib ratio once normalized — beside a sentence sharing no structure.
_NEAR_A = "I think we should vote out the suspicious player today."
_NEAR_B = "I think we should vote out the suspicious player right now."
_DISTINCT = "The weather is sunny and the birds are singing outside."


def test_score_repetition_counts_a_near_duplicate_pair() -> None:
    """Two near-dups + one distinct → count 2 over denominator 3."""
    result = score_repetition([_NEAR_A, _NEAR_B, _DISTINCT], set())

    assert result["count"] == 2
    assert result["denominator"] == 3
    assert result["rate"] == pytest.approx(2 / 3)


def test_score_repetition_all_distinct_is_zero_count_full_denominator() -> None:
    """An all-distinct list → count 0, rate 0.0, denominator still the line count."""
    distinct_lines = [
        _NEAR_A,
        _DISTINCT,
        "A completely orthogonal remark about the harbour at dawn.",
    ]

    result = score_repetition(distinct_lines, set())

    assert result["count"] == 0
    assert result["rate"] == 0.0
    assert result["denominator"] == 3


def test_score_repetition_empty_list_is_all_zeros_no_zero_division() -> None:
    """The empty list returns all-zeros and never raises ZeroDivisionError."""
    result = score_repetition([], set())

    assert result == {"rate": 0.0, "count": 0, "denominator": 0}


def test_score_repetition_is_name_masked() -> None:
    """The spec-009 measure is name-masked: same sentence, two different names.

    The two short lines differ only in the leading player name. Unmasked, the
    name is a large enough fraction of each line that the difflib ratio falls
    *below* 0.85 (not a near-dup). With the names supplied so ``_mask_names``
    replaces both with a single placeholder, the remaining text is identical and
    they cluster — count 2. Asserting both directions proves masking is the
    load-bearing step, not an incidental pass.
    """
    line_a = "Alexander betrayed us."
    line_b = "Bo betrayed us."

    masked = score_repetition([line_a, line_b], {"Alexander", "Bo"})
    assert masked["count"] == 2
    assert masked["denominator"] == 2

    unmasked = score_repetition([line_a, line_b], set())
    assert unmasked["count"] == 0


def test_score_repetition_facet_types_are_int_count_int_denominator() -> None:
    """``count`` / ``denominator`` are ints, ``rate`` a float — the record shape."""
    result = score_repetition([_NEAR_A, _NEAR_B], {"x"})

    assert isinstance(result["count"], int)
    assert isinstance(result["denominator"], int)
    assert isinstance(result["rate"], float)


# ===========================================================================
# 3. The hand-rendered write-only YAML ledger
# ===========================================================================


def _synthetic_result() -> EvalResult:
    """A populated ``EvalResult`` built from the real dataclass.

    Model ids deliberately carry a ``:`` and digits (``qwen2.5:3b``) and a
    dotted Bedrock id, so the renderer's single-quoting of string scalars is
    exercised against values a YAML reader could otherwise mis-type.
    """
    result = EvalResult(
        provider="bedrock",
        large_model="us.amazon.nova-pro-v1:0",
        small_model="qwen2.5:3b",
        games_attempted=5,
        games_completed=4,
        games_failed_early=1,
        ai_speeches=["line one", "line two"],
        metrics={"repetition": {"rate": 0.4, "count": 2, "denominator": 5}},
    )
    # Mirror run_eval: the Wilson CI is attached to every present metric, so the
    # rendered record carries ci_low/ci_high right after denominator.
    _attach_ci(result.metrics)
    return result


def _top_level_keys(doc: str) -> list[str]:
    """The top-level YAML keys of a rendered record, in document order.

    A top-level key is an unindented ``key:`` or ``key: <scalar>`` line. Picks
    up both the block headers (``run:``) and a leaf top-level key like
    ``notes: ''`` — so the ``notes``-is-last assertions read off one helper.
    """
    keys: list[str] = []
    for ln in doc.splitlines():
        if not ln or ln.startswith(" "):
            continue
        head, _, _ = ln.partition(":")
        if head and head == head.strip():
            keys.append(head)
    return keys


def test_render_record_emits_the_fixed_top_level_key_order() -> None:
    """Top-level keys appear in the documented order: run → code → provider → settings → quality → metrics → notes."""
    doc = render_record(_synthetic_result(), "2026-06-13")

    assert _top_level_keys(doc) == [
        "run",
        "code",
        "provider",
        "settings",
        "quality",
        "metrics",
        "notes",
    ]


def test_render_record_emits_notes_as_the_last_top_level_key() -> None:
    """A single-line ``--note`` renders as the LAST top-level key, safely quoted.

    The note carries a ``:`` and an apostrophe so the quoting/escaping path is
    exercised, and the run round-trips with the stable key order ending in
    ``notes`` (run → code → provider → settings → quality → metrics → notes).
    """
    result = _synthetic_result()
    result.notes = "baseline run: it's the pre-change Y measurement"

    doc = render_record(result, "2026-06-13")

    assert _top_level_keys(doc) == [
        "run",
        "code",
        "provider",
        "settings",
        "quality",
        "metrics",
        "notes",
    ]
    # Quoted scalar with the embedded apostrophe doubled per YAML single-quote rules.
    assert "notes: 'baseline run: it''s the pre-change Y measurement'" in doc
    # And it is genuinely the final top-level line (after the metrics block).
    assert doc.rstrip("\n").splitlines()[-1] == (
        "notes: 'baseline run: it''s the pre-change Y measurement'"
    )


def test_render_record_empty_note_is_present_but_empty() -> None:
    """An unset/empty note renders as ``notes: ''`` (present, not omitted)."""
    result = _synthetic_result()
    assert result.notes == ""  # the dataclass default

    doc = render_record(result, "2026-06-13")

    assert "notes: ''" in doc
    assert "notes" in _top_level_keys(doc)


def test_render_record_multiline_note_is_a_block_scalar() -> None:
    """A multi-line note renders as a YAML literal block scalar (``notes: |``).

    Assert structurally (the repo ships no YAML parser): the literal ``|`` block
    indicator opens the key, each content line appears indented one level deeper
    than the ``notes`` key, and ``notes`` is still the last top-level key.
    """
    result = _synthetic_result()
    result.notes = "first line\nsecond line\nthird line"

    doc = render_record(result, "2026-06-13")
    lines = doc.splitlines()

    # The literal block indicator opens the key (not a quoted/flow scalar).
    assert "notes: |" in lines
    header_i = lines.index("notes: |")
    # Each content line is indented one level (two spaces) under the key.
    assert lines[header_i + 1] == "  first line"
    assert lines[header_i + 2] == "  second line"
    assert lines[header_i + 3] == "  third line"
    # Still the last top-level key, and the body is the document's tail.
    assert _top_level_keys(doc) == [
        "run",
        "code",
        "provider",
        "settings",
        "quality",
        "metrics",
        "notes",
    ]
    assert doc.rstrip("\n").splitlines()[-1] == "  third line"


def test_render_record_metric_subkeys_are_rate_count_denominator_then_ci() -> None:
    """The metric sub-keys keep the fixed rate → count → denominator → ci_low → ci_high order."""
    doc = render_record(_synthetic_result(), "2026-06-13")
    lines = doc.splitlines()

    rate_i = lines.index("    rate: 0.4")
    count_i = lines.index("    count: 2")
    denom_i = lines.index("    denominator: 5")
    # The Wilson CI floats are siblings AFTER denominator, in low → high order.
    low_i = next(i for i, ln in enumerate(lines) if ln.startswith("    ci_low: "))
    high_i = next(i for i, ln in enumerate(lines) if ln.startswith("    ci_high: "))
    assert rate_i < count_i < denom_i < low_i < high_i
    # And the metric sits under its name, under the metrics block.
    assert "metrics:" in lines
    assert "  repetition:" in lines
    assert lines.index("  repetition:") < rate_i


def test_render_record_present_metric_carries_a_wilson_ci_band() -> None:
    """A present metric (2/5) renders ci_low/ci_high matching ``wilson_ci``.

    The CI is derived/supplementary — attached by ``_attach_ci`` from the
    metric's own count/denominator — so the rendered band must equal
    ``wilson_ci(2, 5)`` to the float ``repr`` the renderer emits.
    """
    doc = render_record(_synthetic_result(), "2026-06-13")
    low, high = wilson_ci(2, 5)

    assert f"    ci_low: {low!r}" in doc
    assert f"    ci_high: {high!r}" in doc


def test_render_record_scalar_typing_quotes_strings_keeps_numbers_bare() -> None:
    """Model ids with ``:`` / digits are single-quoted; ints bare; the float stable."""
    doc = render_record(_synthetic_result(), "2026-06-13")

    # Strings (incl. the model ids and the date) are single-quoted.
    assert "  date: '2026-06-13'" in doc
    assert "  name: 'bedrock'" in doc
    assert "  large_model: 'us.amazon.nova-pro-v1:0'" in doc
    assert "  small_model: 'qwen2.5:3b'" in doc
    # Ints render bare (no quotes), the metrics version is present.
    assert "  games: 5" in doc
    assert f"  metrics_version: {METRICS_VERSION}" in doc
    assert "  games_attempted: 5" in doc
    assert "  games_completed: 4" in doc
    assert "  games_failed_early: 1" in doc
    # The float renders stably as 0.4 (repr shortest-form), not 0.40000000000000002.
    assert "    rate: 0.4" in doc
    assert "    count: 2" in doc
    assert "    denominator: 5" in doc


def test_render_record_has_no_leading_document_separator() -> None:
    """``render_record`` returns the body only — the ``---`` is the appender's job."""
    doc = render_record(_synthetic_result(), "2026-06-13")

    assert not doc.startswith("---")
    assert doc.endswith("\n")


def test_render_record_whole_valued_float_keeps_its_decimal_point() -> None:
    """A 0.0 rate must stay a float in the text (``0.0``), not collapse to ``0``."""
    result = EvalResult(
        provider="ollama",
        metrics={"repetition": {"rate": 0.0, "count": 0, "denominator": 0}},
    )

    doc = render_record(result, "2026-06-13")

    assert "    rate: 0.0" in doc


def test_append_record_writes_two_separated_documents(tmp_path: Path) -> None:
    """Appending twice accumulates two ``---``-separated documents.

    The injectable ``ledger_path`` points at a temp file — never the real
    ``evals/blunder-ledger.yaml`` — and the second append must not rewrite the
    first (append-only history; functional-spec 011 §2.3).
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    result = _synthetic_result()

    first_path = append_record(result, "2026-06-13", ledger_path=ledger)
    text_after_first = ledger.read_text(encoding="utf-8")

    second_path = append_record(result, "2026-06-14", ledger_path=ledger)
    text_after_second = ledger.read_text(encoding="utf-8")

    assert first_path == ledger
    assert second_path == ledger
    # Exactly two document separators — one per appended record.
    assert text_after_second.count("---\n") == 2
    # The first append's full text is still a prefix — history was not rewritten.
    assert text_after_second.startswith(text_after_first)
    # Both run dates survive, in append order.
    first_date_i = text_after_second.index("date: '2026-06-13'")
    second_date_i = text_after_second.index("date: '2026-06-14'")
    assert first_date_i < second_date_i


def test_append_record_creates_parent_directory(tmp_path: Path) -> None:
    """The appender creates a missing ``evals/`` parent on first use."""
    ledger = tmp_path / "evals" / "blunder-ledger.yaml"
    assert not ledger.parent.exists()

    append_record(_synthetic_result(), "2026-06-13", ledger_path=ledger)

    assert ledger.exists()
    assert ledger.parent.is_dir()


def test_append_record_first_document_starts_with_the_separator(
    tmp_path: Path,
) -> None:
    """The very first record is itself ``---``-led, so all records are uniform."""
    ledger = tmp_path / "blunder-ledger.yaml"

    append_record(_synthetic_result(), "2026-06-13", ledger_path=ledger)

    assert ledger.read_text(encoding="utf-8").startswith("---\n")


# ===========================================================================
# 4. Spec 017 — streaming capture preserves MULTIPLE Nights' picks.
#
# This is the central regression of the slice: ``night_open`` resets the
# per-Night pointing channels (``night_round_picks`` / ``night_rounds_log``)
# every Night, so a final-state read holds only the LAST Night's picks. The
# harness instead taps the per-super-step ``graph.stream(stream_mode="updates")``
# log into ``_GameCapture.events`` (the ``on_update`` sink ``_play_one_game``
# threads), which preserves every Night's pointing in chronological order.
#
# Driven model-free against the REAL graph: ``fake_large`` / ``fake_small`` stub
# every LLM call site, and a live-state Pointing dispatcher (the slice-8
# pattern) targets a fresh law-abiding AI each Night — never the human — so the
# human survives Night 1 and a SECOND Night occurs. AIs only ``speak`` (never
# vote), so each Day exhausts its rounds and rolls into the next Night without
# any execution ending the game early.
# ===========================================================================

from graphia.config import load_config  # noqa: E402  (after the module docstring/imports)
from graphia.graph import build_graph, make_run_config  # noqa: E402
from graphia.llm import Ballot, DayAction, Pointing  # noqa: E402
from graphia.tools.eval_dialogue import _collect_interrupt, _drive  # noqa: E402

_HUMAN_NAME = "Alice"
_AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]


def _alive_ai_ids_by_role(graph, run_config, role: str) -> list[str]:
    """Alive non-human player ids of ``role``, read off live graph state."""
    players = graph.get_state(run_config).values.get("players", {})
    return [
        p.id
        for p in players.values()
        if p.is_alive and p.role == role and not p.is_human
    ]


def _drive_two_night_game(
    graph,
    run_config,
    fake,
    *,
    events_sink: Callable[[dict], None],
    target_nights: int = 2,
    budget: int = 400,
) -> None:
    """Drive a mocked game past ``target_nights`` Nights, taping every super-step.

    Mirrors ``_play_one_game``'s drive loop (name interrupt → resume → answer
    interrupts until done) but against the ``fake_large``-built graph, with two
    deliberate forcings so a clean multi-Night game results:

    - a live ``_invoke_live`` dispatch on the unified fake: Pointing targets a
      fresh ALIVE law-abiding AI each Night (so the law-abiding human is never
      the victim and survives into Night 2); every DayAction is a ``speak`` (so
      no AI ever calls a vote — the Day exhausts its rounds and rolls into the
      next Night without an execution); Ballots are No (defensive — no vote is
      ever opened anyway).
    - the human (pinned law-abiding via ``GRAPHIA_ROLE``) passes on ``day_turn``
      and votes No, never initiating a vote.

    Stops once ``cycle`` has advanced to ``target_nights`` AND that Night's kill
    has resolved, or the graph ends / the budget is exhausted — so the captured
    log spans at least two full Nights' pointing.
    """
    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            law_ids = _alive_ai_ids_by_role(graph, run_config, "law_abiding")
            if law_ids:
                return Pointing(target_id=law_ids[0])
            # No law-abiding AI left — fall back to any alive non-human so the
            # Night still resolves rather than hanging.
            alive = _alive_ai_ids_by_role(graph, run_config, "mafia")
            return Pointing(target_id=alive[0] if alive else "missing")
        if schema is DayAction:
            return DayAction(kind="speak", text="(nothing to add this round.)")
        if schema is Ballot:
            return Ballot(yes=False)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    # Stream to the name interrupt, then resume with the scripted name.
    _drive(graph, run_config, {"messages": []}, on_update=events_sink)
    first = _collect_interrupt(graph, run_config)
    assert first == {"kind": "name"}, f"expected name interrupt first, got {first!r}"
    _drive(graph, run_config, Command(resume=_HUMAN_NAME), on_update=events_sink)

    def _reached_target_night() -> bool:
        values = graph.get_state(run_config).values
        if values.get("winner") is not None:
            return True  # game ended (shouldn't, but stop cleanly)
        # A Night has fully resolved when we are at/past target cycle AND the
        # Day phase has opened for it (so that Night's kill is in the log).
        cycle = values.get("cycle", 1)
        return cycle >= target_nights and values.get("phase") == "day"

    for _ in range(budget):
        if _reached_target_night():
            return
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            return  # graph reached END
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            _drive(graph, run_config, None, on_update=events_sink)
            continue
        kind = iv.get("kind")
        if kind == "day_turn":
            resume: str = "..."
        elif kind == "vote":
            resume = "no"
        elif kind == "point":
            options = iv.get("options") or []  # human is law-abiding; defensive
            resume = options[0]["id"] if options else ""
        else:
            raise AssertionError(f"unexpected interrupt {kind!r}")
        _drive(graph, run_config, Command(resume=resume), on_update=events_sink)


def _picks_per_night(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The deciding ``night_round_picks`` captured for each Night, in order.

    Walks the streamed log: a ``night_open`` delta opens a fresh Night (and
    resets the channels — present as ``night_round_picks: {}``); each subsequent
    ``mafia_point`` delta carries the cumulative ``night_round_picks`` for the
    round in progress. We keep the LAST non-empty ``night_round_picks`` seen
    before the next ``night_open`` as that Night's deciding picks. This is the
    very read a final-state snapshot CANNOT do (it would hold only the last
    Night's picks); doing it off the stream proves the earlier Night survived.
    """
    per_night: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for event in events:
        for node, delta in event.items():
            if not isinstance(delta, dict):
                continue
            if node == "night_open":
                if current:
                    per_night.append(current)
                current = {}
                continue
            if current is None:
                continue
            picks = delta.get("night_round_picks")
            if isinstance(picks, dict) and picks:
                current = dict(picks)
    if current:
        per_night.append(current)
    return per_night


def test_capture_events_preserves_multiple_nights_pointing(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2+ Night mocked game's ``events`` log holds EARLIER Nights' picks.

    The no-Night-lost regression (tech-spec §4 capture bullet): drive the real
    graph past two Nights while taping each super-step into an ``events`` list,
    then assert at least two distinct Nights' deciding ``night_round_picks`` are
    present in that log — even though ``night_open`` reset the channel between
    them, so a final-state read would have lost the first. The captured picks
    name real player ids, and each Night points at a DIFFERENT victim (a fresh
    alive law-abiding AI), so the two Nights' picks are genuinely distinct, not a
    repeat of one surviving channel.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
    fake_small(_AI_NAMES)
    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    events: list[dict[str, Any]] = []
    _drive_two_night_game(
        graph, run_config, fake, events_sink=events.append, target_nights=2
    )

    # The stream log is an ordered list of {node: delta} super-steps.
    assert events, "no super-steps were captured"
    assert all(isinstance(e, dict) for e in events)

    # At least two Nights opened in the captured log (each night_open is a fresh
    # Night and a channel reset).
    night_opens = [e for e in events if "night_open" in e]
    assert len(night_opens) >= 2, (
        f"expected >=2 Nights captured, saw {len(night_opens)}"
    )

    # The deciding picks for each Night, recovered from the stream log.
    per_night = _picks_per_night(events)
    assert len(per_night) >= 2, (
        f"expected >=2 Nights' pointing in the log, recovered {per_night!r}"
    )

    # Night 1's picks survive in the log even though night_open reset the channel
    # before Night 2 — the failure mode a final-state read has.
    night1_picks, night2_picks = per_night[0], per_night[1]
    assert night1_picks, "Night 1 deciding picks were lost from the stream log"
    assert night2_picks, "Night 2 deciding picks missing from the stream log"

    # Each Night targeted a fresh law-abiding victim, so the two Nights' picked
    # targets differ — proof the earlier Night wasn't just the later one's
    # surviving channel echoed twice.
    night1_targets = set(night1_picks.values())
    night2_targets = set(night2_picks.values())
    assert night1_targets and night2_targets
    assert night1_targets != night2_targets, (
        f"both Nights point at the same target(s) — Night 1 may have been lost: "
        f"{night1_targets!r} vs {night2_targets!r}"
    )

    # And the renderer surfaces BOTH Nights' pointing by name (the end-to-end
    # proof the captured log → transcript keeps every Night).
    players = graph.get_state(run_config).values.get("players", {})
    id_to_name = {pid: p.name for pid, p in players.items()}
    transcript = render_transcript(
        events, players, game_index=1, run_meta={"provider": "bedrock"}
    )
    night1_victim_name = id_to_name[next(iter(night1_targets))]
    night2_victim_name = id_to_name[next(iter(night2_targets))]
    assert f"points at {night1_victim_name}" in transcript
    assert f"points at {night2_victim_name}" in transcript


def test_natural_mafia_win_runs_to_a_real_result_without_a_mid_day_cut(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 023: a game drives to a REAL win, not cut off mid-Day, not a runaway.

    The blunder-eval drive now stops only on ``snapshot.next`` emptying (a
    natural end), with no ``rounds >= max_rounds`` mid-Day cut. Here a mocked
    7-player game (5 law-abiding, 2 mafia) is driven to its natural conclusion:
    the Mafia kill a fresh law-abiding AI each Night and the law-abiding human
    passes / votes No (no executions), so the town thins 5→4→3 law-abiding until
    the Mafia reach parity — a genuine ``winner == "mafia"`` well within the
    default 12-Day runaway cap. The game must NOT be recorded as ``"runaway"``
    (the cap was never hit) nor end with no winner from a mid-Day cut.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
    fake_small(_AI_NAMES)
    fake = fake_large(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    assert config.max_days == 12  # default runaway cap, untouched
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    # Mafia always kill a living law-abiding AI; every AI just speaks (no vote is
    # ever called); the human passes on day_turn and votes No — so no execution
    # happens and the game ends purely by Night attrition reaching parity.
    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            law_ids = _alive_ai_ids_by_role(graph, run_config, "law_abiding")
            if law_ids:
                return Pointing(target_id=law_ids[0])
            alive = _alive_ai_ids_by_role(graph, run_config, "mafia")
            return Pointing(target_id=alive[0] if alive else "missing")
        if schema is DayAction:
            return DayAction(kind="speak", text="(nothing to add this round.)")
        if schema is Ballot:
            return Ballot(yes=False)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    # The exact blunder-eval drive shape: name interrupt → resume → answer
    # interrupts until ``snapshot.next`` empties. No round cap — the only stop is
    # the natural game end. The backstop mirrors the harness's Day-cap-derived
    # bound (max_days * 60 + 40) purely as an anti-hang guard.
    _drive(graph, run_config, {"messages": []})
    first = _collect_interrupt(graph, run_config)
    assert first == {"kind": "name"}
    _drive(graph, run_config, Command(resume=_HUMAN_NAME))

    line_idx = 0
    max_super_steps = config.max_days * 60 + 40
    for _ in range(max_super_steps):
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            break  # natural end
        iv = _collect_interrupt(graph, run_config)
        if iv is None:
            _drive(graph, run_config, None)
            continue
        kind = iv.get("kind")
        if kind == "day_turn":
            resume: str = "..."
            line_idx += 1
        elif kind == "vote":
            resume = "no"
        elif kind == "point":
            options = iv.get("options") or []
            resume = options[0]["id"] if options else ""
        else:
            raise AssertionError(f"unexpected interrupt {kind!r}")
        _drive(graph, run_config, Command(resume=resume))

    values = graph.get_state(run_config).values
    # The drive ended on a NATURAL conclusion (no pending nodes).
    assert not graph.get_state(run_config).next, "game did not run to a natural end"
    # A REAL side win — Mafia by attrition — NOT a runaway cap-hit and NOT None.
    assert values.get("winner") == "mafia"
    assert values.get("winner") != "runaway"
    # And it resolved well before the runaway cap — the cap was never the reason.
    assert values.get("cycle", 0) < config.max_days


# ===========================================================================
# 5. Spec 017 — per-run transcript storage + the ledger record link.
#
# Drive a MOCKED ``run_eval`` with ``_play_one_game`` stubbed to return
# hand-built ``_GameCapture``s (so no graph or provider is constructed — fully
# offline), an injected ``transcripts_root`` under ``tmp_path``, a pinned
# ``run_id``, and an injected ledger path. Assert the per-run-dir + zero-padded
# files exist, carry the rendered text, and the record links the run-id. The
# real ``evals/transcripts/`` is never written.
# ===========================================================================


def _capture_for_storage(victim_name: str) -> _GameCapture:
    """A minimal but realistic ``_GameCapture`` with a one-Night ``events`` log.

    Carries a final ``players`` map (roles + names) and an ordered ``events``
    log with a setup reveal, one Night's pointing + kill, and a Day open — enough
    that ``render_transcript`` produces a real tagged document with the stable
    ``<transcript>`` token and the victim's name, without running a game.
    """
    from graphia.state import PlayerPersona, PlayerState

    mafia = PlayerState(
        id="p-1",
        name="Don",
        role="mafia",
        is_human=False,
        persona=PlayerPersona("sly", "smooth", "the tavern keeper", "the boss"),
    )
    victim = PlayerState(
        id="p-2",
        name=victim_name,
        role="law_abiding",
        is_human=False,
        is_alive=False,
        persona=PlayerPersona("kind", "gentle", "the baker", ""),
    )
    players = {"p-1": mafia, "p-2": victim}
    events: list[dict[str, Any]] = [
        {"night_open": {"night_round_picks": {}, "night_rounds_log": []}},
        {"mafia_point": {"night_round_picks": {"p-1": "p-2"}}},
        {
            "resolve_night_kill": {
                "kill_log": [{"cycle": 1, "name": victim_name, "cause": "night"}],
            }
        },
    ]
    return _GameCapture(
        ai_lines=[],
        ai_names={"Don", victim_name},
        ai_lines_with_speakers=[],
        players=players,
        messages=[],
        captures=[],
        winner="mafia",
        events=events,
    )


def _storage_args(games: int) -> argparse.Namespace:
    """The ``argparse.Namespace`` ``run_eval`` reads — a bedrock, no-seed run."""
    return argparse.Namespace(
        provider="bedrock",
        games=games,
        seed=None,
        # Spec 023: the CLI control is now the day-denominated runaway cap;
        # None means "use GRAPHIA_MAX_DAYS / the default 12".
        max_days=None,
        note="",
    )


def _stub_run_eval_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[object, Path]:
    """Stub everything ``run_eval`` touches except the transcript path under test.

    Returns a bare config object (``run_eval`` only reads ``ollama_base_url`` /
    ``num_citizens`` / ``num_mafia`` defensively) and the temp ledger path the
    record is appended to — so neither the real ledger nor the real transcripts
    dir is written. Provenance collectors are stubbed to degraded values so no
    git/HTTP runs.
    """
    monkeypatch.setattr(
        blunder_eval, "collect_code_provenance", lambda root: {
            "commit": None, "branch": None, "dirty": False
        }
    )
    monkeypatch.setattr(
        blunder_eval,
        "collect_provider_provenance",
        lambda provider, large, small, base: {
            "name": provider, "large_model": large, "small_model": small
        },
    )
    monkeypatch.setattr(
        blunder_eval, "_resolved_model_names", lambda config: ("nova-pro", "nova-lite")
    )
    # Redirect the ledger write to a temp file (never the real one).
    ledger = tmp_path / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)

    class _Cfg:
        ollama_base_url = "http://localhost:11434"
        num_citizens = 5
        num_mafia = 2

    return _Cfg(), ledger


def test_run_eval_writes_per_run_transcript_files_into_tmp_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mocked 2-game run writes ``<run-id>/game-01.txt`` + ``game-02.txt``.

    ``_play_one_game`` is stubbed to return hand-built ``_GameCapture``s (no graph
    / no provider), ``transcripts_root`` + ``run_id`` are injected under
    ``tmp_path``, and the files land with the per-run-dir + zero-padded naming and
    carry the rendered transcript text (the stable ``<transcript>`` token + each
    game's victim name).
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    transcripts_root = tmp_path / "transcripts"
    run_id = "2026-06-18T09-00-00"

    victims = {0: "Cara", 1: "Eve"}

    def _fake_play(args: argparse.Namespace, game_index: int) -> _GameCapture:
        return _capture_for_storage(victims[game_index])

    monkeypatch.setattr(blunder_eval, "_play_one_game", _fake_play)

    result = run_eval(
        config,
        _storage_args(games=2),
        transcripts_root=transcripts_root,
        run_id=run_id,
    )

    run_dir = transcripts_root / run_id
    game1 = run_dir / "game-01.txt"
    game2 = run_dir / "game-02.txt"
    assert game1.exists(), "game-01.txt missing under the per-run dir"
    assert game2.exists(), "game-02.txt missing under the per-run dir"
    # No third file — exactly one per game.
    assert sorted(p.name for p in run_dir.iterdir()) == ["game-01.txt", "game-02.txt"]

    # The files carry the rendered transcript (stable tag + per-game victim name).
    text1 = game1.read_text(encoding="utf-8")
    text2 = game2.read_text(encoding="utf-8")
    assert "<transcript>" in text1
    assert "<transcript>" in text2
    assert "Cara" in text1
    assert "Eve" in text2

    # The injected path matches ``transcript_path``'s own arithmetic.
    assert game1 == transcript_path(transcripts_root, run_id, 1)
    assert game2 == transcript_path(transcripts_root, run_id, 2)

    # The result carries the run-id as its transcript dir.
    assert result.transcript_dir == run_id


def test_run_eval_record_carries_transcript_dir_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The ledger record renders ``run.transcript_dir: '<run-id>'`` for the run.

    The viewer maps a record → its transcripts via this link. We render the
    record ``run_eval`` produced and assert the run-id appears under ``run`` as
    ``transcript_dir`` — and that ``transcript_path`` would resolve a game file
    under exactly that dir name.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    transcripts_root = tmp_path / "transcripts"
    run_id = "2026-06-18T10-15-30"

    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: _capture_for_storage("Cara"),
    )

    result = run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=transcripts_root,
        run_id=run_id,
    )

    assert result.transcript_dir == run_id

    doc = render_record(result, "2026-06-18")
    # The link is rendered under the ``run`` block as a single-quoted scalar.
    assert f"  transcript_dir: '{run_id}'" in doc
    # And it is genuinely inside the ``run`` block (before ``code:``).
    run_i = doc.index("run:")
    code_i = doc.index("code:")
    link_i = doc.index(f"  transcript_dir: '{run_id}'")
    assert run_i < link_i < code_i


def test_run_eval_does_not_touch_the_real_transcripts_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An injected ``transcripts_root`` keeps the real ``evals/transcripts/`` untouched.

    Belt-and-braces: snapshot the real ``TRANSCRIPTS_ROOT``'s contents (if any)
    before the run and assert the run-id dir we wrote lives under ``tmp_path``,
    NOT under the repo's ``evals/transcripts/``. The real dir gains no new
    children from this run.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    transcripts_root = tmp_path / "transcripts"
    run_id = "2026-06-18T11-22-33"

    real_root = blunder_eval.TRANSCRIPTS_ROOT
    before = (
        sorted(p.name for p in real_root.iterdir()) if real_root.is_dir() else None
    )

    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: _capture_for_storage("Cara"),
    )

    run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=transcripts_root,
        run_id=run_id,
    )

    # Our run-id dir is under tmp_path, never under the real root.
    assert (transcripts_root / run_id).is_dir()
    assert not (real_root / run_id).exists()

    after = (
        sorted(p.name for p in real_root.iterdir()) if real_root.is_dir() else None
    )
    assert after == before, "the real evals/transcripts/ gained or lost children"


# ===========================================================================
# 6. Spec 017 — ``clean_transcripts`` drops only UNTRACKED run dirs.
#
# Exercised against a REAL temp git repo (``git init`` in ``tmp_path``): one run
# dir is committed (tracked → keep), one is left untracked (→ remove). The
# function asks git via ``git ls-files`` exactly as the make target does, so this
# runs the real tracked-vs-untracked decision — never against the repo's own
# ``evals/transcripts/``.
# ===========================================================================


def _git(repo: Path, *args: str) -> None:
    """Run one ``git`` command in ``repo`` (test-local helper, fail loudly)."""
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_temp_repo(repo: Path) -> None:
    """``git init`` a throwaway repo with a committable identity + default branch."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def test_clean_transcripts_keeps_tracked_drops_untracked(
    tmp_path: Path,
) -> None:
    """In a temp git repo: a committed run dir survives; an untracked one is removed.

    ``clean_transcripts`` decides tracked-vs-untracked with ``git ls-files`` over
    each run dir (via ``_git_tracks_anything_under``). We commit ``kept-run`` and
    leave ``smoke-run`` untracked, then call ``clean_transcripts`` with the temp
    repo as ``repo_root`` — the real decision path. Only the untracked dir is
    removed, and the returned list names exactly it.
    """
    repo = tmp_path / "repo"
    _init_temp_repo(repo)

    transcripts_root = repo / "evals" / "transcripts"
    kept = transcripts_root / "kept-run"
    smoke = transcripts_root / "smoke-run"
    kept.mkdir(parents=True)
    smoke.mkdir(parents=True)
    (kept / "game-01.txt").write_text("<transcript>kept</transcript>\n", "utf-8")
    (smoke / "game-01.txt").write_text("<transcript>smoke</transcript>\n", "utf-8")

    # Track + commit only the keeper; leave the smoke run untracked.
    _git(repo, "add", str(kept / "game-01.txt"))
    _git(repo, "commit", "-m", "keep this run")

    removed = clean_transcripts(transcripts_root, repo_root=repo)

    # The untracked smoke run is gone; the committed keeper survives intact.
    assert not smoke.exists(), "untracked smoke run should be removed"
    assert kept.is_dir(), "committed run must be preserved"
    assert (kept / "game-01.txt").read_text(encoding="utf-8") == (
        "<transcript>kept</transcript>\n"
    )
    # The returned list names exactly the removed dir.
    assert removed == [smoke]


def test_clean_transcripts_missing_root_is_a_noop(tmp_path: Path) -> None:
    """A missing ``transcripts_root`` is a silent no-op returning an empty list."""
    repo = tmp_path / "repo"
    _init_temp_repo(repo)

    removed = clean_transcripts(repo / "evals" / "transcripts", repo_root=repo)

    assert removed == []


def test_clean_transcripts_all_untracked_are_removed(tmp_path: Path) -> None:
    """With nothing committed, every run dir under the root is removed.

    The smoke-run-cleanup happy path: two untracked run dirs, both dropped, both
    named in the returned list (sorted, as the function iterates).
    """
    repo = tmp_path / "repo"
    _init_temp_repo(repo)

    transcripts_root = repo / "evals" / "transcripts"
    run_a = transcripts_root / "run-a"
    run_b = transcripts_root / "run-b"
    for run in (run_a, run_b):
        run.mkdir(parents=True)
        (run / "game-01.txt").write_text("x\n", "utf-8")

    removed = clean_transcripts(transcripts_root, repo_root=repo)

    assert not run_a.exists()
    assert not run_b.exists()
    assert sorted(removed) == sorted([run_a, run_b])
