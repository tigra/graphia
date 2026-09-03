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


def _cli_stub_config() -> object:
    """The thinnest config ``main``'s pre-run path accepts (was a bare ``object()``).

    ``main`` reads exactly ONE field off the resolved config before handing over
    to ``run_eval``: spec 039's ``private_diaries_enabled``, the ledger's arm
    label. ``_require_diaries_arm`` refuses to play without it — an unlabelled
    record is worthless and a wrongly-labelled one corrupts the comparison — so a
    bare ``object()`` is now (correctly) rejected before the preflight. These
    tests stub the preflights and ``run_eval``, so no other attribute is ever
    touched: this stays a one-field stub, not a real ``GraphiaConfig``.
    """

    class _CliCfg:
        private_diaries_enabled = True

    return _CliCfg()


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
def test_pre_run_setup_isolates_and_forces_provider_for_every_provider(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Driving ``main`` to the pre-game setup isolates + forces the provider.

    For **both** ``ollama`` and ``bedrock`` (Bedrock is the whole point — the
    config offline-gate only covers ollama, so the harness must pop the stores
    itself): all five cloud-store vars and ``GRAPHIA_REMOTE`` are popped and
    ``GRAPHIA_LLM_PROVIDER`` is forced to the chosen value.

    ``run_eval`` is stubbed to a sentinel that captures the live env at the
    moment the harness *would* start playing games — so no graph is built and no
    provider client is ever constructed. ``load_config`` is stubbed to the
    one-field ``_cli_stub_config`` (spec 039's arm label, the only field ``main``
    itself reads) and the ollama preflight is stubbed to a no-op, so nothing
    reaches a config branch that could touch AWS / Ollama / the network.
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
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)
    # The ollama branch imports run_ollama_preflight from graphia.preflight.
    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", lambda cfg: None)
    # Spec 035: the bedrock-claude branch likewise boots a preflight. This test
    # is about env isolation + provider forcing, not preflight behaviour (that
    # has its own tests below), so stub it out the same way — otherwise the
    # one-field stub config trips its real config attribute reads.
    monkeypatch.setattr("graphia.preflight.run_claude_preflight", lambda cfg: None)

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
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)
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
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)
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
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)
    monkeypatch.setattr(
        blunder_eval, "run_eval", lambda config, args: EvalResult(provider="bedrock")
    )

    def _boom(cfg: object) -> None:
        raise AssertionError("bedrock must not run the Ollama preflight")

    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", _boom)

    assert main(["--provider", "bedrock", "--games", "1"]) == 0


def test_invalid_provider_is_rejected_by_argparse() -> None:
    """Only the three real providers are accepted (argparse ``choices``)."""
    with pytest.raises(SystemExit):
        main(["--provider", "openai"])


# ---------------------------------------------------------------------------
# Spec 035: the ``bedrock-claude`` arm — selectable, and preflighted like the
# game boots, so an unreachable Claude stops the run BEFORE burning tokens on
# game 1 rather than failing partway through a paid batch.
# ---------------------------------------------------------------------------


def test_bedrock_claude_is_an_accepted_provider() -> None:
    """``bedrock-claude`` is in the provider vocabulary (spec 035)."""
    assert "bedrock-claude" in PROVIDERS


def test_bedrock_claude_runs_the_claude_preflight_before_any_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Claude arm preflights before the first game, and never plays if it fails."""
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)

    calls: list[str] = []

    def _fake_preflight(cfg: object) -> None:
        calls.append("preflight")

    def _fake_run_eval(config: object, args: argparse.Namespace) -> EvalResult:
        calls.append("run_eval")
        return EvalResult(provider=args.provider)

    monkeypatch.setattr("graphia.preflight.run_claude_preflight", _fake_preflight)
    monkeypatch.setattr(blunder_eval, "run_eval", _fake_run_eval)

    assert main(["--provider", "bedrock-claude", "--games", "1"]) == 0
    assert calls == ["preflight", "run_eval"], (
        "the Claude preflight must run, and must run BEFORE any game is played"
    )


def test_bedrock_claude_preflight_failure_stops_before_playing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable Claude aborts the run without playing a (paid) game."""
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)

    def _unreachable(cfg: object) -> None:
        raise SystemExit("Claude is unreachable: refresh your credentials.")

    def _never(config: object, args: argparse.Namespace) -> EvalResult:
        raise AssertionError("no game may be played when the preflight fails")

    monkeypatch.setattr("graphia.preflight.run_claude_preflight", _unreachable)
    monkeypatch.setattr(blunder_eval, "run_eval", _never)

    with pytest.raises(SystemExit):
        main(["--provider", "bedrock-claude", "--games", "1"])


def test_bedrock_claude_does_not_run_the_ollama_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Claude arm must never invoke the Ollama preflight."""
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)
    monkeypatch.setattr(
        blunder_eval,
        "run_eval",
        lambda config, args: EvalResult(provider="bedrock-claude"),
    )
    monkeypatch.setattr("graphia.preflight.run_claude_preflight", lambda cfg: None)

    def _boom(cfg: object) -> None:
        raise AssertionError("bedrock-claude must not run the Ollama preflight")

    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", _boom)

    assert main(["--provider", "bedrock-claude", "--games", "1"]) == 0


def test_nova_bedrock_does_not_run_the_claude_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: Nova keeps its existing no-preflight story (spec 035 §2.6)."""
    monkeypatch.setattr("graphia.config.load_config", _cli_stub_config)
    monkeypatch.setattr(
        blunder_eval, "run_eval", lambda config, args: EvalResult(provider="bedrock")
    )

    def _boom(cfg: object) -> None:
        raise AssertionError("the Nova arm must not run the Claude preflight")

    monkeypatch.setattr("graphia.preflight.run_claude_preflight", _boom)

    assert main(["--provider", "bedrock", "--games", "1"]) == 0


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
# 3b. Spec 036 — the record KIND is purely additive to the renderer
#     (functional-spec 036 §2, tech-spec 036 §4 "Renderer, additive-only")
# ===========================================================================
#
# ``run.kind`` names which KIND of measurement a record describes, so a
# persona-generation measurement can join the same append-only ledger a played
# game writes to without being mistaken for an interrupted game. The committed
# ledger is a DATA CONTRACT, so the acceptance bar for the addition is
# *byte-identity*: a game-shaped record must render exactly the text it rendered
# before spec 036 existed.
#
# These tests pin three things:
#
# 1. **Byte-identity.** A fully-populated game-shaped ``EvalResult`` renders
#    character-for-character equal to :data:`_PRE_036_GAME_RECORD` — a frozen
#    golden captured from the pre-036 renderer (verified by rendering the same
#    synthetic result under the ``HEAD`` copy of the module during authoring).
#    No ``run.kind`` line, ``outcomes`` / ``vote_activity`` present.
# 2. **The bench shape.** A ``kind='persona-bench'`` result emits
#    ``run.kind`` — immediately after ``run.date`` — and OMITS ``outcomes`` /
#    ``vote_activity`` / ``run.transcript_dir``. Those three were already
#    conditional; the assertions exist so a future refactor cannot quietly make
#    them unconditional and start writing hollow game blocks into a bench record.
# 3. **The value-type write-back regression.** ``render_record`` filtered metric
#    sub-keys through a fixed tuple that contained neither ``mean`` nor ``peak``,
#    so every value-type facet specs 031-033 measured reached the ledger as a
#    bare ``denominator:`` with the measured value DROPPED. Committed data was
#    silently corrupted for three specs and cannot be backfilled. The
#    round-trip is pinned per facet family below.


def _game_shaped_result() -> EvalResult:
    """A fully-populated GAME-shaped ``EvalResult`` — every conditional block present.

    Deliberately maximal, because the golden it renders is the byte-identity
    guard: ``code`` / ``provider_block`` (with the bedrock ``note``) /
    ``settings`` (with ``scripted_player`` and the nested ``lineup``) /
    ``outcomes`` / ``vote_activity`` / ``transcript_dir`` are all populated, so
    the golden exercises every conditional emission the renderer owns. ``kind``
    is left at its default empty string — a played game — which is what makes the
    ``run.kind`` key absent.
    """
    result = EvalResult(
        provider="bedrock",
        large_model="us.amazon.nova-pro-v1:0",
        small_model="qwen2.5:3b",
        games_attempted=5,
        games_completed=4,
        games_failed_early=1,
        ai_speeches=["line one", "line two"],
        metrics={
            "repetition": {"rate": 0.4, "count": 2, "denominator": 5},
            "self_vote.initiation": {"rate": 0.0, "count": 0, "denominator": 3},
        },
        outcomes={
            "games": 4,
            "law_abiding": {"wins": 3, "rate": 0.75},
            "mafia": {"wins": 1, "rate": 0.25},
            "runaway": 0,
            "draw": 0,
            "no_winner": 0,
        },
        vote_activity={
            "by_side": {"law_abiding": 2, "mafia": 1},
            "by_day": {"day_1": 3},
        },
        code={"commit": "abc123", "branch": "main", "dirty": False},
        provider_block={
            "name": "bedrock",
            "large_model": "us.amazon.nova-pro-v1:0",
            "small_model": "qwen2.5:3b",
            "note": "bedrock ids are stable; model weights may change invisibly",
        },
        settings={
            "large_model": "us.amazon.nova-pro-v1:0",
            "small_model": "qwen2.5:3b",
            "base_url": None,
            "games": 5,
            "seed": None,
            "max_days": 12,
            "scripted_player": "active",
            "lineup": {"num_citizens": 5, "num_mafia": 2},
        },
        duration_seconds=343.364,
        transcript_dir="2026-06-13T10-00-00",
    )
    _attach_ci(result.metrics)
    return result


def _bench_shaped_result() -> EvalResult:
    """A BENCH-shaped ``EvalResult`` — ``kind`` set, every game block left empty.

    The shape ``persona_bench.build_bench_record`` produces: a record kind, the
    roster counts under the ``quality`` keys, and only value-type persona
    facets — no ``outcomes``, no ``vote_activity``, no ``transcript_dir``,
    because a bench run plays no game, casts no vote and writes no transcript.
    Built here (rather than imported from the bench) so this file tests the
    RENDERER against the shape, independent of the mapping that produces it.
    """
    return EvalResult(
        provider="ollama",
        large_model="qwen3-coder:30b",
        small_model="qwen2.5:3b",
        games_attempted=5,
        games_completed=5,
        games_failed_early=0,
        metrics={
            "persona_lex_mean": {"mean": 0.1234, "denominator": 75},
            "persona_lex_peak": {"peak": 0.5, "denominator": 75},
        },
        duration_seconds=31.5,
        kind="persona-bench",
    )


# The frozen PRE-036 rendering of :func:`_game_shaped_result`, captured from the
# ``HEAD`` copy of ``blunder_eval`` (the last commit before ``run.kind`` existed)
# and asserted byte-for-byte below. Its whole purpose is to fail loudly if a
# future additive field ever changes what a GAME record's text looks like — the
# committed ledger is a data contract, and every existing record must keep
# rendering exactly this way. Note the absent ``kind:`` line between ``date`` and
# ``duration_seconds``: that absence IS the "this was a played game" signal.
_PRE_036_GAME_RECORD = """\
run:
  date: '2026-06-13'
  duration_seconds: 343.364
  metrics_version: 1
  transcript_dir: '2026-06-13T10-00-00'
code:
  commit: 'abc123'
  branch: 'main'
  dirty: false
provider:
  name: 'bedrock'
  large_model: 'us.amazon.nova-pro-v1:0'
  small_model: 'qwen2.5:3b'
  note: 'bedrock ids are stable; model weights may change invisibly'
settings:
  large_model: 'us.amazon.nova-pro-v1:0'
  small_model: 'qwen2.5:3b'
  base_url: null
  games: 5
  seed: null
  max_days: 12
  scripted_player: 'active'
  lineup:
    num_citizens: 5
    num_mafia: 2
quality:
  games_attempted: 5
  games_completed: 4
  games_failed_early: 1
  duration_seconds: 343.364
outcomes:
  games: 4
  law_abiding:
    wins: 3
    rate: 0.75
  mafia:
    wins: 1
    rate: 0.25
  runaway: 0
  draw: 0
  no_winner: 0
  note: 'win-rate is measured against an active rule-based scripted human (deterministic, no model call; \
when Law-abiding it supplies the vote a correct town majority needs) — a consistent comparable measure, \
not true game balance.'
vote_activity:
  by_side:
    law_abiding: 2
    mafia: 1
  by_day:
    day_1: 3
metrics:
  repetition:
    rate: 0.4
    count: 2
    denominator: 5
    ci_low: 0.1176182311592533
    ci_high: 0.769280067791163
  self_vote.initiation:
    rate: 0.0
    count: 0
    denominator: 3
    ci_low: 0.0
    ci_high: 0.5615060804490177
notes: ''
"""


def test_render_record_game_shape_is_byte_identical_to_pre_036_text() -> None:
    """A game-shaped record renders EXACTLY its pre-036 text — the data contract.

    The headline acceptance bar of spec 036 (tech-spec §3, "Risk: silently
    corrupting a committed data file"): ``run.kind`` is conditional, so adding it
    must leave every already-committed record's rendering untouched. Asserted as
    full-document equality rather than substring anchors, because a *missing* or
    *reordered* line is exactly the failure mode a substring assertion would miss.
    """
    doc = render_record(_game_shaped_result(), "2026-06-13")

    assert doc == _PRE_036_GAME_RECORD


def test_render_record_game_shaped_record_omits_the_run_kind_key() -> None:
    """A played game carries NO ``kind`` key — absence is what means "a game".

    Called out separately from the golden so the intent survives any future edit
    to that fixture: nothing is backfilled, and ``run.kind`` must never appear on
    a record whose ``EvalResult.kind`` is empty (the ``transcript_dir`` /
    ``settings.lineup`` conditional-emission precedent).
    """
    doc = render_record(_game_shaped_result(), "2026-06-13")

    assert "kind:" not in doc
    assert "persona-bench" not in doc


@pytest.mark.parametrize(
    ("factory", "expected_keys"),
    [
        pytest.param(
            _game_shaped_result,
            [
                "run",
                "code",
                "provider",
                "settings",
                "quality",
                "outcomes",
                "vote_activity",
                "metrics",
                "notes",
            ],
            id="game-shaped-keeps-the-game-blocks",
        ),
        pytest.param(
            _bench_shaped_result,
            [
                "run",
                "code",
                "provider",
                "settings",
                "quality",
                "metrics",
                "notes",
            ],
            id="bench-shaped-omits-the-game-blocks",
        ),
    ],
)
def test_render_record_top_level_key_order_per_record_kind(
    factory: Callable[[], EvalResult],
    expected_keys: list[str],
) -> None:
    """The top-level key order is fixed per shape; the game blocks omit themselves.

    ``outcomes`` and ``vote_activity`` were ALREADY conditional before spec 036,
    so a bench record needs no renderer change to drop them — but that is exactly
    why it needs pinning: a refactor that made either unconditional would start
    writing a hollow ``0-of-0`` win block into every bench record, which is the
    "a bench record read as a game run" hazard the ``Kind`` column exists to
    prevent. ``notes`` stays last in both shapes.
    """
    doc = render_record(factory(), "2026-06-13")

    assert _top_level_keys(doc) == expected_keys


def test_render_record_bench_shape_emits_kind_right_after_the_date() -> None:
    """A bench ``run`` block reads date → kind → duration → metrics_version.

    Placement matters because ``_yaml_block`` renders in insertion order, so the
    build sequence IS the key order: ``kind`` is inserted between ``date`` and
    ``duration_seconds`` rather than appended, which keeps the record's identity
    facts together and mirrors the viewer's ``Kind``-after-``Date`` column.
    """
    doc = render_record(_bench_shaped_result(), "2026-06-13")
    lines = doc.splitlines()

    assert "  kind: 'persona-bench'" in lines
    date_i = lines.index("  date: '2026-06-13'")
    kind_i = lines.index("  kind: 'persona-bench'")
    duration_i = lines.index("  duration_seconds: 31.5")
    version_i = lines.index(f"  metrics_version: {METRICS_VERSION}")
    assert date_i < kind_i < duration_i < version_i
    # Immediately after the date — no key slipped in between.
    assert kind_i == date_i + 1


def test_render_record_bench_shaped_record_omits_the_game_only_keys() -> None:
    """A bench record carries no ``outcomes`` / ``vote_activity`` / ``transcript_dir``.

    A bench run plays no game, casts no vote and writes no transcript, so all
    three must be ABSENT rather than zeroed — the "absent, not zero" rule that
    lets a reader see the blanks are expected instead of reading a 0-of-0 win rate
    as a failed game.
    """
    doc = render_record(_bench_shaped_result(), "2026-06-13")

    assert "outcomes:" not in doc
    assert "vote_activity:" not in doc
    assert "transcript_dir" not in doc
    # No ledger record is ever complete without the trailing human-mutable note.
    assert doc.rstrip("\n").splitlines()[-1] == "notes: ''"


def test_render_record_bench_shaped_record_still_carries_a_metrics_version() -> None:
    """A bench record stamps the SAME ``metrics_version`` — no bump for a new kind.

    Spec 036 adds a record kind and reuses every existing scorer unchanged, so
    rates measured before and after stay directly comparable and the version must
    not move (the ``ci_low`` / ``lineup`` / ``scripted_player`` precedent).
    """
    doc = render_record(_bench_shaped_result(), "2026-06-13")

    assert f"  metrics_version: {METRICS_VERSION}" in doc


@pytest.mark.parametrize(
    ("facet_key", "value", "rendered"),
    [
        pytest.param("mean", 0.1234, "    mean: 0.1234", id="mean"),
        pytest.param("peak", 0.5, "    peak: 0.5", id="peak"),
    ],
)
def test_render_record_value_type_facet_round_trips_its_measured_value(
    facet_key: str,
    value: float,
    rendered: str,
) -> None:
    """REGRESSION: a value-type facet's ``mean``/``peak`` reaches the ledger text.

    The writer's fixed metric sub-key filter contained neither ``mean`` nor
    ``peak``, so every value-type facet specs 031-033 measured was written as a
    bare ``denominator:`` with the measured value silently DROPPED — the viewer
    had been taught to read a number the writer never emitted, and the affected
    committed records cannot be backfilled because those values exist nowhere
    else. This is the round-trip that was missing: the value is emitted, it
    precedes its ``denominator``, and the facet is not reduced to its own n.
    """
    result = EvalResult(
        provider="ollama",
        metrics={"persona_lex_mean": {facet_key: value, "denominator": 30}},
    )

    doc = render_record(result, "2026-06-13")
    lines = doc.splitlines()

    assert rendered in lines
    value_i = lines.index(rendered)
    denom_i = lines.index("    denominator: 30")
    # The value comes FIRST, then the n behind it (the on-disk shape spec 032
    # already committed: ``mean:`` then ``denominator:``).
    assert lines.index("  persona_lex_mean:") < value_i < denom_i


def test_render_record_value_type_facet_carries_no_rate_count_or_ci_band() -> None:
    """A similarity value gets no ``rate``/``count`` and no Wilson band.

    A mean/peak cosine is not a binomial proportion, so the Wilson CI does not
    apply — and the sub-key filter must not invent one. Pinned alongside the
    round-trip above so the fix that ADDED ``mean``/``peak`` cannot drift into
    also emitting rate-family keys for a value-type facet.
    """
    result = EvalResult(
        provider="ollama",
        metrics={
            "persona_sem_peak": {"peak": 0.87, "denominator": 30},
        },
    )

    doc = render_record(result, "2026-06-13")

    assert "    peak: 0.87" in doc
    assert "rate:" not in doc
    assert "count:" not in doc
    assert "ci_low:" not in doc
    assert "ci_high:" not in doc


def test_render_record_metric_carrying_neither_mean_nor_peak_is_unchanged() -> None:
    """Adding ``mean``/``peak`` to the sub-key filter is additive for rate metrics.

    The other half of the regression fix: a RATE-type metric (which carries
    neither new key) must render exactly as before — which the byte-identity
    golden above already covers for the whole document, and this pins at the
    single-metric level so the failure localises.
    """
    result = EvalResult(
        provider="ollama",
        metrics={"repetition": {"rate": 0.4, "count": 2, "denominator": 5}},
    )

    doc = render_record(result, "2026-06-13")
    lines = doc.splitlines()

    metric_i = lines.index("  repetition:")
    assert lines[metric_i + 1 : metric_i + 4] == [
        "    rate: 0.4",
        "    count: 2",
        "    denominator: 5",
    ]
    assert "    mean:" not in doc
    assert "    peak:" not in doc


# ===========================================================================
# 3c. Spec 036, Slice 2 — the ``generation`` block + the persona CONDITIONS
#     (functional-spec 036 §2, tech-spec 036 §4)
# ===========================================================================
#
# Slice 2 adds the two things that make a bench record *comparable* rather than
# merely present:
#
# * a top-level ``generation`` block carrying ``collisions`` / ``regenerations``
#   — the generation-PROCESS counts that carried the spec-034 result (2-in-10
#   rosters shipping a near-duplicate → 0-in-10), which a similarity MEAN alone
#   would have lost; and
# * a ``settings.persona`` sub-map naming the conditions the measurement ran
#   under, so a diversity-ON record and a diversity-OFF record are readable as a
#   PAIR instead of two indistinguishable columns of numbers.
#
# Both are conditional and additive, so the pinning is symmetric: present with
# the right values and in the right place on a bench record, and wholly ABSENT
# on a game-shaped one (the byte-identity golden in 3b above is the other half
# of that guarantee).
#
# The one non-obvious contract, pinned hard below: inside a PRESENT
# ``generation`` block both counts ALWAYS render a visible integer — the
# ``vote_activity`` explicit-zero treatment, NOT ``metrics``' absent-≠-zero one.
# A measured ``collisions: 0`` is the headline finding of a diversity-on run, so
# no future "the dict is falsy anyway" refactor may turn it into an omitted key.


def _recorded_bench_result(
    *,
    collisions: int = 3,
    regenerations: int = 7,
    diversity_enabled: bool = True,
) -> EvalResult:
    """The FULL Slice-2 bench shape: kind + ``generation`` + ``settings.persona``.

    Deliberately a *separate* factory from :func:`_bench_shaped_result` (the
    Slice-1 minimum) so that one keeps pinning the narrower shape it was written
    for — a bench record whose ``generation`` block is empty must still render —
    while this one varies the two fields Slice 2 introduced.
    """
    result = _bench_shaped_result()
    result.generation = {"collisions": collisions, "regenerations": regenerations}
    result.settings = {
        "large_model": "qwen3-coder:30b",
        "small_model": "qwen2.5:3b",
        # Recorded TEXT only — the renderer never dereferences it, and no test in
        # this suite may reach a local ollama server.
        "base_url": "http://localhost:11434",
        # The unit is ROSTERS here; ``run.kind`` is what says so.
        "games": 5,
        # Genuinely inapplicable to a run that plays no game — recorded null
        # rather than borrowing an ambient config value that never applied.
        "seed": None,
        "max_days": None,
        "persona": {
            "diversity_enabled": diversity_enabled,
            "collision_threshold": 0.6,
            "regen_attempts": 2,
            "temperature": 1.0,
        },
    }
    return result


def _hybrid_result() -> EvalResult:
    """A DEFENSIVE shape: every game block present *and* a ``generation`` block.

    No live path produces this — a game run generates no roster in isolation and
    a bench run plays no game — so it exists purely to pin the ``generation``
    block's POSITION in the fixed key order against the two blocks it must
    follow. Without it, "generation comes after vote_activity" is untested,
    because on a real bench record there is no ``vote_activity`` to come after.
    """
    result = _game_shaped_result()
    result.generation = {"collisions": 1, "regenerations": 2}
    return result


@pytest.mark.parametrize(
    ("factory", "expected_keys"),
    [
        pytest.param(
            _game_shaped_result,
            [
                "run",
                "code",
                "provider",
                "settings",
                "quality",
                "outcomes",
                "vote_activity",
                "metrics",
                "notes",
            ],
            id="game-shaped-has-no-generation-block",
        ),
        pytest.param(
            _recorded_bench_result,
            [
                "run",
                "code",
                "provider",
                "settings",
                "quality",
                "generation",
                "metrics",
                "notes",
            ],
            id="bench-shaped-has-generation-and-no-game-blocks",
        ),
        pytest.param(
            _hybrid_result,
            [
                "run",
                "code",
                "provider",
                "settings",
                "quality",
                "outcomes",
                "vote_activity",
                "generation",
                "metrics",
                "notes",
            ],
            id="generation-follows-vote-activity-precedes-metrics",
        ),
    ],
)
def test_render_record_generation_block_sits_last_in_the_run_dynamics_band(
    factory: Callable[[], EvalResult],
    expected_keys: list[str],
) -> None:
    """``generation`` renders after ``vote_activity`` and before ``metrics``.

    The documented band is ``quality`` → ``outcomes`` → ``vote_activity`` →
    ``generation`` → ``metrics``: the block sits immediately beside the persona
    facets whose denominators it contextualises. Asserted over the whole
    top-level key list rather than a substring, so a block that moved (or one
    that stopped omitting itself) fails here rather than being silently written
    into a committed data file.
    """
    doc = render_record(factory(), "2026-06-13")

    assert _top_level_keys(doc) == expected_keys


def test_render_record_game_shaped_record_omits_the_generation_block() -> None:
    """A played game carries no ``generation`` block, and none of its keys.

    Stated separately from the byte-identity golden so the *intent* survives any
    later edit to that fixture: a game run generates no roster in isolation, so
    the whole block omits itself — nothing is backfilled and every committed
    record keeps rendering exactly as it does today.
    """
    doc = render_record(_game_shaped_result(), "2026-06-13")

    assert "generation:" not in doc
    assert "collisions" not in doc
    assert "regenerations" not in doc


@pytest.mark.parametrize(
    ("collisions", "regenerations"),
    [
        pytest.param(3, 7, id="both-non-zero"),
        pytest.param(0, 0, id="both-zero-the-diversity-on-headline"),
        pytest.param(0, 12, id="zero-collisions-after-twelve-regenerations"),
        pytest.param(5, 0, id="five-collisions-no-regeneration-fired"),
    ],
)
def test_render_record_generation_block_renders_both_counts_in_fixed_order(
    collisions: int,
    regenerations: int,
) -> None:
    """Both counts render as visible integers, ``collisions`` then ``regenerations``.

    The explicit-zero guarantee (the ``vote_activity.by_side`` treatment, not
    ``metrics``' absent-≠-zero one). ``0`` here is a *measured* figure — "no cast
    shipped an over-similar pair" is precisely the finding a diversity-on run
    exists to produce — so it must be written, never omitted as a falsy value.
    Sub-key order is pinned too: the two counts read the same way in every record.
    """
    doc = render_record(
        _recorded_bench_result(collisions=collisions, regenerations=regenerations),
        "2026-06-13",
    )
    lines = doc.splitlines()

    block_i = lines.index("generation:")
    assert lines[block_i + 1 : block_i + 3] == [
        f"  collisions: {collisions}",
        f"  regenerations: {regenerations}",
    ]


def test_render_record_zero_collisions_are_written_not_omitted() -> None:
    """REGRESSION GUARD: a 0/0 ``generation`` block is still a rendered block.

    Called out on its own because it is the single assertion a well-meaning
    refactor is most likely to break — "both counts are zero, so there is nothing
    to write" inverts the meaning of the record. A diversity-on run whose casts
    contained no over-similar pair MUST read ``collisions: 0``; an omitted key
    would read as "this run did not measure collisions", which is the opposite of
    the truth and would make the spec-034 comparison unrecoverable.
    """
    doc = render_record(
        _recorded_bench_result(collisions=0, regenerations=0), "2026-06-13"
    )

    assert "generation:" in doc.splitlines()
    assert "  collisions: 0" in doc
    assert "  regenerations: 0" in doc


def test_render_record_partial_generation_dict_fills_the_missing_count() -> None:
    """A half-populated block still renders both keys — the absent one as ``0``.

    ``render_record`` reads each count with a ``0`` default, so a caller that
    recorded only one of them cannot produce a block with a missing key: the
    on-disk shape stays fixed for every reader (the viewer's
    ``_render_generation_section`` reads both by name).
    """
    result = _bench_shaped_result()
    result.generation = {"collisions": 4}

    doc = render_record(result, "2026-06-13")
    lines = doc.splitlines()

    block_i = lines.index("generation:")
    assert lines[block_i + 1 : block_i + 3] == [
        "  collisions: 4",
        "  regenerations: 0",
    ]


def test_render_record_settings_persona_renders_after_the_flat_settings_keys() -> None:
    """``settings.persona`` is a nested sub-map following the flat settings keys.

    The ``settings.lineup`` precedent (spec 014): a one-level sub-map rendered
    after the flat keys, with its own fixed sub-key order so an A/B pair diffs
    cleanly line-for-line.
    """
    doc = render_record(_recorded_bench_result(), "2026-06-13")
    lines = doc.splitlines()

    persona_i = lines.index("  persona:")
    assert lines.index("settings:") < lines.index("  max_days: null") < persona_i
    assert lines[persona_i + 1 : persona_i + 5] == [
        "    diversity_enabled: true",
        "    collision_threshold: 0.6",
        "    regen_attempts: 2",
        "    temperature: 1.0",
    ]
    # The sub-map belongs to ``settings`` — it must precede the next top-level
    # block, not trail off after it.
    assert persona_i < lines.index("quality:")


@pytest.mark.parametrize(
    ("diversity_enabled", "rendered"),
    [
        pytest.param(True, "    diversity_enabled: true", id="arm-on"),
        pytest.param(False, "    diversity_enabled: false", id="arm-off"),
    ],
)
def test_render_record_settings_persona_records_either_diversity_arm(
    diversity_enabled: bool,
    rendered: str,
) -> None:
    """Both arms render as a YAML bool, so a flag-off record says so on its face.

    Whichever arm ran must be legible in the record: a comparison between a
    diversity-on and a diversity-off measurement only means anything when each
    side states which side it was (functional-spec §2, "the conditions the
    measurement ran under"). ``false`` is the arm most at risk — it is the one a
    config-default read would silently mislabel as ``true``.
    """
    doc = render_record(
        _recorded_bench_result(diversity_enabled=diversity_enabled), "2026-06-13"
    )

    assert rendered in doc.splitlines()


def test_render_record_settings_without_persona_omits_the_sub_map() -> None:
    """A game run's ``settings`` grows no ``persona`` sub-map.

    The conditional half of the additive contract: the block is emitted only when
    the run recorded the knobs, so every already-committed record's ``settings``
    renders byte-identically (the golden in 3b covers the full document; this
    localises the failure to the sub-map).
    """
    doc = render_record(_game_shaped_result(), "2026-06-13")

    assert "  persona:" not in doc
    assert "diversity_enabled" not in doc


def test_render_record_unrecorded_persona_knobs_render_as_nulls() -> None:
    """A knob the mapping could not resolve renders ``null``, not a borrowed value.

    ``build_bench_record`` reads the three config knobs with a ``None`` default
    (the ``max_days`` / ``lineup`` precedent), so a provenance gap costs the
    measurement nothing — but it must read as *genuinely absent* rather than as a
    plausible default nobody measured under, which would make the record's
    conditions a fiction.
    """
    result = _bench_shaped_result()
    result.settings = {
        "large_model": "qwen3-coder:30b",
        "small_model": "qwen2.5:3b",
        "base_url": None,
        "games": 5,
        "seed": None,
        "max_days": None,
        "persona": {"diversity_enabled": False},
    }

    doc = render_record(result, "2026-06-13")
    lines = doc.splitlines()

    persona_i = lines.index("  persona:")
    assert lines[persona_i + 1 : persona_i + 5] == [
        "    diversity_enabled: false",
        "    collision_threshold: null",
        "    regen_attempts: null",
        "    temperature: null",
    ]


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


# ===========================================================================
# Spec 035 follow-up — `bedrock-claude` carries the invisible-updates note.
#
# The provider match previously handled "ollama" and "bedrock" only, so the
# Claude arm fell through with no note at all. Observable in the committed
# 2026-08-31 Claude n=50 record, which has no `note` line where the Nova
# records have one.
# ===========================================================================


@pytest.mark.parametrize("provider", ["bedrock", "bedrock-claude"])
def test_both_bedrock_arms_carry_the_invisible_updates_note(provider: str) -> None:
    """Claude on Bedrock is as server-side-opaque as Nova, so it gets the caveat."""
    block = blunder_eval.collect_provider_provenance(
        provider, large_model="x", small_model="y", base_url=None
    )
    assert block["note"] == blunder_eval._BEDROCK_UPDATE_NOTE


def test_ollama_provenance_carries_no_invisible_updates_note() -> None:
    """The local path is not server-side-opaque — pinned digests identify it."""
    block = blunder_eval.collect_provider_provenance(
        "ollama", large_model="x", small_model="y", base_url="http://localhost:11434"
    )
    assert "note" not in block
