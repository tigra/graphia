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
from pathlib import Path

import pytest

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    METRICS_VERSION,
    PROVIDERS,
    _CLOUD_STORE_ENV_VARS,
    _apply_model_overrides,
    _isolate_cloud_stores,
    append_record,
    main,
    render_record,
    score_repetition,
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
    return EvalResult(
        provider="bedrock",
        large_model="us.amazon.nova-pro-v1:0",
        small_model="qwen2.5:3b",
        games_attempted=5,
        games_completed=4,
        games_failed_early=1,
        ai_speeches=["line one", "line two"],
        metrics={"repetition": {"rate": 0.4, "count": 2, "denominator": 5}},
    )


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
    """Top-level keys appear in the documented order: run → provider → quality → metrics → notes."""
    doc = render_record(_synthetic_result(), "2026-06-13")

    assert _top_level_keys(doc) == ["run", "provider", "quality", "metrics", "notes"]


def test_render_record_emits_notes_as_the_last_top_level_key() -> None:
    """A single-line ``--note`` renders as the LAST top-level key, safely quoted.

    The note carries a ``:`` and an apostrophe so the quoting/escaping path is
    exercised, and the run round-trips with the stable key order ending in
    ``notes`` (run → provider → quality → metrics → notes).
    """
    result = _synthetic_result()
    result.notes = "baseline run: it's the pre-change Y measurement"

    doc = render_record(result, "2026-06-13")

    assert _top_level_keys(doc) == ["run", "provider", "quality", "metrics", "notes"]
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
    assert _top_level_keys(doc) == ["run", "provider", "quality", "metrics", "notes"]
    assert doc.rstrip("\n").splitlines()[-1] == "  third line"


def test_render_record_metric_subkeys_are_rate_then_count_then_denominator() -> None:
    """The repetition metric's sub-keys keep the fixed rate → count → denominator order."""
    doc = render_record(_synthetic_result(), "2026-06-13")
    lines = doc.splitlines()

    rate_i = lines.index("    rate: 0.4")
    count_i = lines.index("    count: 2")
    denom_i = lines.index("    denominator: 5")
    assert rate_i < count_i < denom_i
    # And the metric sits under its name, under the metrics block.
    assert "metrics:" in lines
    assert "  repetition:" in lines
    assert lines.index("  repetition:") < rate_i


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
