"""Spec 039 (Per-AI Private Diaries) — Slice 5: the measurement plumbing.

Locks in the harness-and-ledger half of the spec: **a run says which arm of the
diaries A/B it was**, and a run that cannot say so is refused rather than
recorded. Tech-spec §2.10 (the arm label), §2.12 (where the sentinel lives),
§2.13 (the instrument must measure its own liveness), §2.14 (the ``max_days``
mislabel) and §2.15 (the three render clarifications).

Five concerns, all offline — **no model, no network, no AWS, and never the
committed ledger as a WRITE target**:

1. **The recorded arm is the invoked arm, end to end on BOTH arms.** ``main`` is
   driven with a REAL ``load_config`` (the ``--diaries`` env assignment → the
   resolved config → ``settings.private_diaries_enabled`` → the YAML line) while
   ``_play_one_game``, the preflight and the provenance collectors are stubbed,
   and ``--ledger-path`` / ``--transcripts-root`` (this slice's own CLI fold-in)
   point at ``tmp_path``. The arm is asserted in three places it has to agree:
   the ledger record, every preserved transcript's header, and the pre-run
   banner.

2. **The sentinel.** A config that cannot answer stops the run *before* the
   preflight and before game 1, and writes nothing — paired with a positive
   control, because "no record was written" is vacuous for a harness that never
   ran. The §2.12 split is pinned too: the refusal is a **CLI-layer** guarantee,
   so a direct ``run_eval`` call with an unanswerable config writes an
   *unlabelled* record instead.

3. **``--max-days N`` records ``N`` and still caps at ``N``** — both halves. The
   pre-fix defect recorded ``12`` while honouring ``4``, so the recording half
   pins the invoked value *and* excludes the ambient default, and the behavioural
   half resolves a real ``load_config()`` from inside the per-game path.

4. **The diary-fallback liveness signal** — absent, never zero, when no entry
   was attempted (a diaries-off arm; an on-arm run whose short ``--max-days``
   trips ``day_diary``'s runaway guard, composed from the REAL node rather than
   asserted by hand), while ``{count: 0, denominator: 11}`` **does** render
   ``0.0``, because a clean measurement is a measurement.

5. **The renders.** The ``Diaries`` column (``on`` / ``off`` / **blank**, never
   ``off`` for absence), the two conditional drill-down lines, the three
   ``quality.diary_*`` keys gated on the **denominator** rather than the rate's
   truthiness, ``METRIC_ORDER`` untouched — and a read-only sweep over the
   **committed** ledger proving the new renders add nothing to a record that does
   not carry the fields.

**The committed ledger is never written.** Every write goes to ``tmp_path``,
via ``--ledger-path`` and a belt-and-braces ``monkeypatch.setattr`` of both
``LEDGER_PATH`` and ``TRANSCRIPTS_ROOT`` — the redirect convention
``tests/test_ledger_viewer.py`` and the spec-031/032/033 harness tests
established. The one place the real file is touched is
:func:`test_the_diaries_renders_add_nothing_to_a_committed_record_without_them`,
which only ever *reads* it.

Anti-vacuity notes are attached where they are load-bearing. The shapes this
spec has already had proven against it: an assertion that reads the same
constant production reads goes vacuous when that constant is mutated (so the
``on``/``off`` spellings are pinned to bare literals as well as to the
constants); a negative ("nothing was written") is vacuous for a component that
never ran (so every negative here is paired with a positive control); and a test
that asserts "entries exist" while silently measuring ``_DIARY_FALLBACK`` proves
nothing (so the fallback text is IMPORTED from the node, never copied).
"""

from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

import graphia.eval_ledger as ledger_mod
from graphia.eval_ledger import (
    METRIC_ORDER,
    _DIARIES_OFF,
    _DIARIES_ON,
    _DIARY_QUALITY_FIELDS,
    _DIARY_QUALITY_GATE,
    _FIXED_COLUMNS,
    _diaries_cell,
    _stand_in_cell,
    build_table_model,
    load_ledger,
    render_detail,
)
# The node's deterministic placeholder, IMPORTED (never copied): the loud half of
# the coupling ``blunder_eval._diary_fallback_text`` documents. A reword or a
# rename breaks this file at import time, in the offline suite, instead of
# quietly turning a run that was ALL placeholder into a clean-looking 0.0.
from graphia.nodes.day import _DIARY_FALLBACK, day_diary
from graphia.state import GameState, PlayerPersona, PlayerState
from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    _GameCapture,
    _diaries_arm,
    _require_diaries_arm,
    main,
    render_record,
    run_eval,
    score_diary_fallback,
)

# ``tests/`` sits directly under the repo root — the locator
# ``tests/test_transcript_highlight.py`` already uses for its corpus sweep.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMITTED_LEDGER = _REPO_ROOT / "evals" / "blunder-ledger.yaml"

# The ambient runaway Day cap, as a BARE LITERAL rather than
# ``config._DEFAULT_MAX_DAYS``: the pre-fix defect recorded exactly this value
# while honouring the invoked one, so an assertion that read the constant would
# have agreed with the defect.
_DEFAULT_MAX_DAYS_LITERAL = 12

_DIARIES_ENV = "GRAPHIA_PRIVATE_DIARIES"
_MAX_DAYS_ENV = "GRAPHIA_MAX_DAYS"


@pytest.fixture(autouse=True)
def blunder_env_snapshot(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Snapshot-and-restore ``os.environ`` around every test in this file.

    ``main`` mutates the process environment DIRECTLY (``os.environ[...] =``) —
    that direct mutation is the behaviour under test, and it is exactly what the
    ``--diaries`` / ``--max-days`` placement tests assert on — so a targeted
    ``monkeypatch.delenv`` cannot undo it. Without the full restore an
    un-restored ``GRAPHIA_PRIVATE_DIARIES=0`` would silently ablate diaries for
    every later test in the session. Mirrors
    ``tests/test_blunder_eval.py:blunder_env_clean``.
    """
    saved = dict(os.environ)
    for var in (
        _DIARIES_ENV,
        _MAX_DAYS_ENV,
        "GRAPHIA_LLM_PROVIDER",
        "GRAPHIA_REMOTE",
        "GRAPHIA_ROLE",
        "GRAPHIA_ACTIVE_SCRIPTED_PLAYER",
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ===========================================================================
# Shared offline scaffolding: a capture with no game behind it, and a ``main``
# driven to the ledger write with a REAL ``load_config``.
# ===========================================================================


def _capture(
    *,
    private_diaries: dict[str, list[dict[str, Any]]] | None = None,
) -> _GameCapture:
    """A minimal ``_GameCapture`` — no graph, no provider, no model.

    Two AI players with personas (so the persona scorers have something real to
    read) and a one-Night ``events`` log, so ``render_transcript`` produces a
    genuine document with the header this file asserts the arm on.
    """
    mafia = PlayerState(
        id="p-1",
        name="Don",
        role="mafia",
        is_human=False,
        persona=PlayerPersona("sly", "smooth", "the tavern keeper", "the boss"),
    )
    victim = PlayerState(
        id="p-2",
        name="Cara",
        role="law_abiding",
        is_human=False,
        is_alive=False,
        persona=PlayerPersona("kind", "gentle", "the baker", ""),
    )
    events: list[dict[str, Any]] = [
        {"night_open": {"night_round_picks": {}, "night_rounds_log": []}},
        {"mafia_point": {"night_round_picks": {"p-1": "p-2"}}},
        {
            "resolve_night_kill": {
                "kill_log": [{"cycle": 1, "name": "Cara", "cause": "night"}],
            }
        },
    ]
    return _GameCapture(
        ai_lines=[],
        ai_names={"Don", "Cara"},
        ai_lines_with_speakers=[],
        players={"p-1": mafia, "p-2": victim},
        messages=[],
        captures=[],
        winner="mafia",
        events=events,
        private_diaries=private_diaries or {},
    )


def _stub_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    play: Callable[[argparse.Namespace, int], _GameCapture] | None = None,
) -> dict[str, Any]:
    """Stub everything ``main`` → ``run_eval`` touches except the paths under test.

    Left REAL on purpose: ``graphia.config.load_config`` (the whole point — the
    ``--diaries`` env assignment has to travel through it), the settings
    assembly, ``render_record`` and ``append_record``.

    Stubbed: the ollama preflight (no server), the git / HTTP provenance
    collectors, and ``_play_one_game`` (no graph, no provider, no model). Both
    module-global paths are redirected at ``tmp_path`` as belt-and-braces on top
    of the CLI arguments, so **no code path can reach the committed ledger or
    the committed transcripts dir.**

    Returns a dict of spies: ``ledger`` / ``transcripts`` paths, the
    ``preflight`` and ``games`` call counters, and the ``order`` list the
    placement tests read.
    """
    spies: dict[str, Any] = {
        "ledger": tmp_path / "ledger" / "smoke-ledger.yaml",
        "transcripts": tmp_path / "scratch-transcripts",
        "preflight": [],
        "games": [],
        "order": [],
    }

    def _preflight(config: object) -> None:
        spies["preflight"].append(config)
        spies["order"].append("preflight")

    def _play(args: argparse.Namespace, game_index: int) -> _GameCapture:
        spies["games"].append(game_index)
        spies["order"].append("game")
        return _capture()

    monkeypatch.setattr("graphia.preflight.run_ollama_preflight", _preflight)
    monkeypatch.setattr("graphia.preflight.run_claude_preflight", _preflight)
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
    monkeypatch.setattr(blunder_eval, "_play_one_game", play or _play)
    # Belt-and-braces: even a bug that ignored ``--ledger-path`` /
    # ``--transcripts-root`` cannot reach the repo's curated files.
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", tmp_path / "fallback-ledger.yaml")
    monkeypatch.setattr(
        blunder_eval, "TRANSCRIPTS_ROOT", tmp_path / "fallback-transcripts"
    )
    return spies


def _run_main(spies: dict[str, Any], *extra: str, games: int = 1) -> int:
    """Drive ``main`` at the stubbed harness, writing only into ``tmp_path``."""
    return main(
        [
            "--provider",
            "ollama",
            "--games",
            str(games),
            "--ledger-path",
            str(spies["ledger"]),
            "--transcripts-root",
            str(spies["transcripts"]),
            *extra,
        ]
    )


def _transcripts(spies: dict[str, Any]) -> list[str]:
    """Every preserved transcript's text under the scratch transcripts root."""
    root = spies["transcripts"]
    if not root.is_dir():
        return []
    return [p.read_text(encoding="utf-8") for p in sorted(root.rglob("game-*.txt"))]


# ===========================================================================
# 1. The recorded arm is the invoked arm — end to end, on BOTH arms.
# ===========================================================================


@pytest.mark.parametrize(
    ("flag", "recorded", "not_recorded", "header", "not_header"),
    [
        pytest.param("on", "true", "false", "diaries=on", "diaries=off", id="on-arm"),
        pytest.param("off", "false", "true", "diaries=off", "diaries=on", id="off-arm"),
    ],
)
def test_the_recorded_arm_is_the_invoked_arm_on_both_arms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    env: Path,
    flag: str,
    recorded: str,
    not_recorded: str,
    header: str,
    not_header: str,
) -> None:
    """``--diaries on|off`` reaches the record, the transcripts and the banner.

    The end-to-end path with nothing about the arm faked: ``main`` sets
    ``GRAPHIA_PRIVATE_DIARIES``, the REAL ``load_config`` resolves it,
    ``_diaries_arm`` reads it back off that config, and ``render_record`` writes
    it. Only the game, the preflight and the provenance collectors are stubs.

    **Both directions are asserted, not just the invoked one.** A harness that
    hard-coded the on arm — the exact failure a permissive
    ``getattr(config, ..., True)`` produces, and the reason §2.10 rejects that
    shape — would pass an ``"true" in text`` assertion on the on arm and fail
    only here, on the off arm's ``not_recorded`` line.
    """
    spies = _stub_harness(monkeypatch, tmp_path)

    rc = _run_main(spies, "--diaries", flag)

    assert rc == 0
    assert spies["games"] == [0], "the run must actually have played its game"

    text = spies["ledger"].read_text(encoding="utf-8")
    # Bare-literal YAML, not ``str(bool)``: the record says ``true``/``false``.
    assert f"  private_diaries_enabled: {recorded}" in text
    assert f"  private_diaries_enabled: {not_recorded}" not in text

    # Every preserved transcript header carries the same arm, as the STRING
    # "on"/"off" — ``eval_transcript._meta_get`` is truthiness-gated, so a raw
    # ``False`` would be silently dropped and the off arm's transcripts (the
    # very files that evidence the flag-off path wrote no diary) would be
    # unlabelled.
    documents = _transcripts(spies)
    assert len(documents) == 1
    assert header in documents[0]
    assert not_header not in documents[0]

    # And the operator sees it BEFORE the batch starts, not only afterwards.
    assert f"diaries {flag}" in capsys.readouterr().out


def test_the_arm_line_sits_between_scripted_player_and_the_lineup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Path
) -> None:
    """The arm is emitted after ``scripted_player`` and before the nested ``lineup``.

    §2.10 fixes the position, and the ledger's own reader mirrors it (both the
    ``Diaries`` column and the drill-down line are placed by the record's key
    order). Pinned by index rather than by presence, so a key that drifted into
    the nested lineup sub-map — where it would read as a lineup field — fails.
    """
    spies = _stub_harness(monkeypatch, tmp_path)

    _run_main(spies, "--diaries", "on")

    text = spies["ledger"].read_text(encoding="utf-8")
    assert text.index("  scripted_player: ") < text.index(
        "  private_diaries_enabled: "
    ) < text.index("  lineup:")


# ===========================================================================
# 2. The sentinel — refuse rather than record an unlabelled run.
# ===========================================================================


class _AnswerableConfig:
    """The one field ``main`` itself reads, plus what ``run_eval`` digs for."""

    private_diaries_enabled = True
    ollama_base_url = "http://localhost:11434"
    num_citizens = 5
    num_mafia = 2
    llm_provider = "ollama"
    ollama_large_model = "qwen3-coder:30b"
    ollama_small_model = "qwen2.5:3b"
    max_days = 12
    scripted_player_active = True


class _UnanswerableConfig(_AnswerableConfig):
    """A config that cannot state the arm — a rename this harness has not followed."""

    private_diaries_enabled = None  # type: ignore[assignment]


def test_the_sentinel_refuses_an_unlabelled_run_before_the_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A config that cannot state the arm stops the run and writes NOTHING.

    Fail-fast means fail *before* the preflight and before game 1 — so the three
    assertions are the exit, the untouched ledger, and the two call counters at
    zero. The ledger is append-only and repo-committed: a mislabelled record
    cannot be rewritten, so "no record" has to beat "a record whose arm is a
    guess".

    Paired with :func:`test_an_answerable_config_writes_the_record_it_refused`
    because **"nothing was written" is vacuous on its own** — a harness that
    exits for an unrelated reason, or one whose whole path is stubbed out, would
    pass this and prove nothing.
    """
    spies = _stub_harness(monkeypatch, tmp_path)
    monkeypatch.setattr("graphia.config.load_config", _UnanswerableConfig)

    with pytest.raises(SystemExit) as excinfo:
        _run_main(spies, "--diaries", "on")

    assert "private_diaries_enabled" in str(excinfo.value)
    assert not spies["ledger"].exists(), "an unlabelled record was written"
    assert spies["preflight"] == [], "the refusal must precede the preflight"
    assert spies["games"] == [], "the refusal must precede game 1"


def test_an_answerable_config_writes_the_record_it_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positive control: the SAME harness, one field different, records the arm.

    Everything but ``private_diaries_enabled`` is identical to the refusal
    above, so the refusal cannot be an artifact of the stubbing.
    """
    spies = _stub_harness(monkeypatch, tmp_path)
    monkeypatch.setattr("graphia.config.load_config", _AnswerableConfig)

    rc = _run_main(spies, "--diaries", "on")

    assert rc == 0
    assert spies["preflight"] != []
    assert spies["games"] == [0]
    assert "  private_diaries_enabled: true" in spies["ledger"].read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(True, True, id="on"),
        pytest.param(False, False, id="off"),
        # A PRESENT falsy value is a measurement, not an absence. ``0`` must read
        # as the off arm; a sentinel written ``None if not arm else bool(arm)``
        # would call it unanswerable and refuse a run that could be labelled.
        pytest.param(0, False, id="present-falsy"),
        pytest.param(1, True, id="present-truthy"),
    ],
)
def test_diaries_arm_reads_a_present_value_including_a_falsy_one(
    value: object, expected: bool
) -> None:
    """``_diaries_arm`` coerces a present value; only absence yields ``None``."""

    class _Cfg:
        private_diaries_enabled = value

    assert _diaries_arm(_Cfg()) is expected
    assert _require_diaries_arm(_Cfg()) is expected


def test_diaries_arm_is_none_only_when_the_field_is_absent_or_none() -> None:
    """Absence and an explicit ``None`` are the two unanswerable shapes."""

    class _NoField:
        pass

    class _NoneField:
        private_diaries_enabled = None

    assert _diaries_arm(_NoField()) is None
    assert _diaries_arm(_NoneField()) is None
    for cfg in (_NoField(), _NoneField()):
        with pytest.raises(SystemExit):
            _require_diaries_arm(cfg)


def _writer_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """The ``argparse.Namespace`` a DIRECT ``run_eval`` call reads."""
    fields: dict[str, Any] = {
        "provider": "ollama",
        "games": 1,
        "seed": None,
        "max_days": None,
        "note": "",
        "ledger_path": tmp_path / "direct-ledger.yaml",
        "transcripts_root": tmp_path / "direct-transcripts",
    }
    fields.update(overrides)
    return argparse.Namespace(**fields)


@pytest.mark.parametrize(
    ("config_cls", "labelled"),
    [
        pytest.param(_AnswerableConfig, True, id="answerable"),
        pytest.param(_UnanswerableConfig, False, id="unanswerable"),
    ],
)
def test_a_direct_run_eval_omits_the_arm_rather_than_refusing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_cls: type,
    labelled: bool,
) -> None:
    """The §2.12 split: the refusal is a CLI-layer guarantee, not a writer one.

    A hard raise inside ``run_eval`` would break the ~15–18 pre-existing tests
    whose ``_Cfg`` stubs document that ``run_eval`` reads config defensively, so
    the sentinel lives in ``main``. The recorded consequence: a **direct**
    ``run_eval`` call with an unanswerable config writes an *unlabelled* record
    rather than refusing — honest (blank, never a wrong label), and the reason
    this behaviour is pinned here rather than left to be rediscovered.

    Swept over both configs so the assertion is a contrast, not a single
    negative: the answerable one proves the key is written on this very path.
    """
    _stub_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval, "_play_one_game", lambda args, index: _capture()
    )
    args = _writer_args(tmp_path)

    result = run_eval(config_cls(), args)

    doc = render_record(result, "2026-09-02")
    assert ("private_diaries_enabled" in result.settings) is labelled
    assert ("  private_diaries_enabled: " in doc) is labelled
    # Never a wrong label on the unlabelled path — blank, not ``false``.
    if not labelled:
        assert "private_diaries_enabled" not in doc


# ===========================================================================
# 3. The flag reaches ``load_config`` before any game.
# ===========================================================================


@pytest.mark.parametrize(
    ("flag", "env_value", "resolved"),
    [
        pytest.param("on", "1", True, id="on"),
        pytest.param("off", "0", False, id="off"),
    ],
)
def test_the_diaries_flag_reaches_load_config_before_any_game(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: Path,
    flag: str,
    env_value: str,
    resolved: bool,
) -> None:
    """``--diaries`` sets the env var BEFORE ``load_config``, before game 1.

    This is the placement the whole label depends on, and its defect twin is in
    the same file: ``--max-days`` used to assign its env var inside
    ``_play_one_game``, *after* the config the record is built from had been
    resolved, so the run was honoured at one value and recorded at another.

    Three assertions, because presence alone would not pin the ordering: the env
    var as ``load_config`` OBSERVED it, the flag on the config it RETURNED, and
    the call order — ``load_config`` first, then the preflight, then the game. A
    one-line move of the assignment below ``config = load_config()`` leaves the
    observed value ``None``.
    """
    spies = _stub_harness(monkeypatch, tmp_path)
    import graphia.config as config_mod

    real_load_config = config_mod.load_config
    observed: list[str | None] = []
    resolved_configs: list[Any] = []

    def _spy_load_config() -> Any:
        observed.append(os.environ.get(_DIARIES_ENV))
        spies["order"].append("load_config")
        cfg = real_load_config()
        resolved_configs.append(cfg)
        return cfg

    monkeypatch.setattr(config_mod, "load_config", _spy_load_config)

    _run_main(spies, "--diaries", flag)

    assert observed == [env_value]
    assert resolved_configs[-1].private_diaries_enabled is resolved
    assert spies["order"] == ["load_config", "preflight", "game"]


# ===========================================================================
# 4. ``--max-days N`` records N and still caps at N (the §2.14 fold-in).
# ===========================================================================


def test_max_days_records_the_invoked_value_and_still_reaches_the_games(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Path
) -> None:
    """``--max-days 4`` records ``4`` **and** the per-game path still resolves ``4``.

    Both halves, because the defect this fixes was self-consistent and wrong in
    only one of them: a ``--max-days 4`` run was HONOURED in game (each game's
    own ``load_config()`` saw the env var) and RECORDED as the ambient default
    ``12``. So the recording half excludes ``12`` explicitly — a test that only
    asserted ``"max_days: 4" in text`` would also have passed against a record
    carrying both lines — and the behavioural half runs a REAL ``load_config()``
    from inside the per-game callback, which is the exact call the runaway cap
    and the anti-hang backstop are bound from.
    """
    from graphia.config import load_config

    per_game: list[tuple[str | None, int]] = []

    def _play(args: argparse.Namespace, game_index: int) -> _GameCapture:
        per_game.append((os.environ.get(_MAX_DAYS_ENV), load_config().max_days))
        return _capture()

    spies = _stub_harness(monkeypatch, tmp_path, play=_play)

    _run_main(spies, "--diaries", "on", "--max-days", "4")

    text = spies["ledger"].read_text(encoding="utf-8")
    assert "  max_days: 4" in text
    assert f"  max_days: {_DEFAULT_MAX_DAYS_LITERAL}" not in text
    # The games still see it — only the RECORDED value moved.
    assert per_game == [("4", 4)]


def test_max_days_omitted_records_the_ambient_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Path
) -> None:
    """No ``--max-days`` ⇒ the record states the ambient default, not ``null``.

    The other side of the contrast above, so ``max_days: 4`` cannot be passing
    for a reason unrelated to the flag.
    """
    spies = _stub_harness(monkeypatch, tmp_path)

    _run_main(spies, "--diaries", "on")

    text = spies["ledger"].read_text(encoding="utf-8")
    assert f"  max_days: {_DEFAULT_MAX_DAYS_LITERAL}" in text


# ===========================================================================
# 5. The diary-fallback liveness signal (§2.13) — absent, never zero.
# ===========================================================================


def _entry(text: str, *, day: int = 1) -> dict[str, Any]:
    """One ``DiaryRecord``-shaped entry as the state channel holds it."""
    return {"day": day, "thoughts_before": 0, "text": text}


def _players_at_the_hinge() -> dict[str, PlayerState]:
    """2 AI players and 1 human — ``day_diary``'s writer predicate."""
    return {
        "p-ava": PlayerState(id="p-ava", name="Ava", role="law_abiding", is_human=False),
        "p-mara": PlayerState(id="p-mara", name="Mara", role="mafia", is_human=False),
        "p-hugo": PlayerState(id="p-hugo", name="Hugo", role="law_abiding", is_human=True),
    }


def _hinge_state(**overrides: Any) -> GameState:
    """A state positioned where ``day_diary`` runs (the Day→Night hinge)."""
    state: GameState = {
        "cycle": 3,
        "phase": "day",
        "players": _players_at_the_hinge(),
        "day_turn_index": 0,
        "day_rounds": 6,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": {},
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_the_fallback_signal_is_recorded_when_entries_were_attempted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Path
) -> None:
    """An on-arm run with entries records rate + numerator + denominator.

    The placeholder text is the node's OWN ``_DIARY_FALLBACK``, imported at the
    top of this file — the shape §3 warned about is a test that asserts "entries
    were written" while every one of them is the placeholder, which reads as a
    clean measurement of a feature that never produced a word. Here two of four
    entries are the real constant and two are model prose, so the rate is a
    contentful ``0.5`` rather than a degenerate 0 or 1.
    """
    diaries = {
        "p-ava": [_entry(_DIARY_FALLBACK, day=1), _entry("Ava suspects the baker.", day=2)],
        "p-mara": [_entry(_DIARY_FALLBACK, day=1), _entry("Mara will point at Ben.", day=2)],
    }
    spies = _stub_harness(
        monkeypatch,
        tmp_path,
        play=lambda args, index: _capture(private_diaries=diaries),
    )

    _run_main(spies, "--diaries", "on")

    text = spies["ledger"].read_text(encoding="utf-8")
    assert "  diary_fallback_rate: 0.5" in text
    assert "  diary_fallback_entries: 2" in text
    assert "  diary_entries_attempted: 4" in text
    # Run health, not an AI-quality metric: it lives beside the other
    # "did this run measure anything?" count, never in ``metrics``. Indices are
    # taken FROM the ``quality`` header — ``run`` carries a ``duration_seconds``
    # of its own, so an unscoped search would compare against the wrong block.
    quality_at = text.index("\nquality:\n")
    assert (
        text.index("  games_failed_early:", quality_at)
        < text.index("  diary_fallback_rate:", quality_at)
        < text.index("  duration_seconds:", quality_at)
    )


def test_the_off_arm_records_no_fallback_signal_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Path
) -> None:
    """A diaries-off run attempts no entry, so all three keys are ABSENT.

    The empty channel is composed from the REAL node rather than hand-waved: the
    flag-off guard returns ``{}`` before any model call, which is exactly what
    ``_play_one_game`` reads off the final state as ``private_diaries``. Absent,
    never ``0.0`` — a zero would assert a clean measurement of a feature that
    never ran.
    """
    off_delta = day_diary(_hinge_state(), private_diaries_enabled=False)
    assert off_delta == {}, "the flag-off guard must write nothing"
    channel = off_delta.get("private_diaries", {}) or {}

    spies = _stub_harness(
        monkeypatch,
        tmp_path,
        play=lambda args, index: _capture(private_diaries=channel),
    )

    _run_main(spies, "--diaries", "off")

    text = spies["ledger"].read_text(encoding="utf-8")
    for key in _DIARY_QUALITY_FIELDS:
        assert key not in text, f"{key} recorded for a run that attempted none"


def test_an_on_arm_run_with_no_opportunity_records_no_fallback_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Path
) -> None:
    """The third case: diaries ON, but a short ``--max-days`` left no opportunity.

    ``day_diary``'s runaway guard is ``cycle + 1 >= max_days``, so a short-capped
    run loses the final Days' entries and at ``--max-days 2`` writes **none** —
    the §2.14 note that ``--max-days`` silently shrinks the diary denominator.
    The gate is the DENOMINATOR, not the arm label, so this on-arm run records
    nothing rather than a misleading ``0.0``: absence means "no opportunity",
    exactly as it does for a denominator-0 metric.

    The empty channel again comes from the real node, with the cap-tripping state
    the graph would hand it.
    """
    capped = day_diary(
        _hinge_state(cycle=1), private_diaries_enabled=True, max_days=2
    )
    assert capped == {}, "the runaway guard must fire before the fan-out"

    spies = _stub_harness(
        monkeypatch,
        tmp_path,
        play=lambda args, index: _capture(
            private_diaries=capped.get("private_diaries", {}) or {}
        ),
    )

    _run_main(spies, "--diaries", "on", "--max-days", "2")

    text = spies["ledger"].read_text(encoding="utf-8")
    assert "  private_diaries_enabled: true" in text, "the arm still says ON"
    for key in _DIARY_QUALITY_FIELDS:
        assert key not in text


def test_a_clean_zero_fallback_rate_is_recorded_not_omitted() -> None:
    """``{count: 0, denominator: 11}`` renders ``0.0`` — a measurement is a measurement.

    The trap §2.15 names from the writing side: the gate is the **denominator**,
    never the rate's truthiness. A one-line change to ``if diary_placeholders:``
    or to a rate-truthiness gate would silently drop the single most valuable
    reading this signal produces — a diaries-on arm that measured eleven real
    entries and zero placeholders — and make it indistinguishable from a run
    that attempted none.
    """
    result = EvalResult(provider="ollama")
    result.diary_fallback = {"count": 0, "denominator": 11}

    doc = render_record(result, "2026-09-02")

    assert "  diary_fallback_rate: 0.0" in doc
    assert "  diary_fallback_entries: 0" in doc
    assert "  diary_entries_attempted: 11" in doc


def test_an_empty_fallback_pair_omits_the_keys_and_never_divides_by_zero() -> None:
    """The default (no entries attempted) omits all three and raises nothing."""
    doc = render_record(EvalResult(provider="ollama"), "2026-09-02")

    for key in _DIARY_QUALITY_FIELDS:
        assert key not in doc


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        pytest.param({}, {"count": 0, "denominator": 0}, id="empty-channel"),
        pytest.param(
            {"p-1": [_entry(_DIARY_FALLBACK)]},
            {"count": 1, "denominator": 1},
            id="all-placeholder",
        ),
        pytest.param(
            {"p-1": [_entry("A real note.")]},
            {"count": 0, "denominator": 1},
            id="all-real",
        ),
        # EXACT equality, not a fuzzy match: one trailing character makes it a
        # real entry. A looser rule would manufacture false positives out of
        # short honest entries, and this figure is read as a tripwire.
        pytest.param(
            {"p-1": [_entry(_DIARY_FALLBACK + "!")]},
            {"count": 0, "denominator": 1},
            id="near-miss-is-real",
        ),
        # Defensive over the channel's shape — this runs inside a measured batch
        # that must finish. A malformed entry is skipped, not counted, and does
        # not raise.
        pytest.param(
            {"p-1": "not-a-list", "p-2": [None, {"day": 1}, _entry("Real.")]},
            {"count": 0, "denominator": 1},
            id="malformed-shapes-skipped",
        ),
    ],
)
def test_score_diary_fallback_counts_exact_placeholder_entries(
    channel: dict[str, Any], expected: dict[str, int]
) -> None:
    """The pure scorer: denominator = entries attempted, count = exact placeholders."""
    assert score_diary_fallback(channel, _DIARY_FALLBACK) == expected
    assert score_diary_fallback(None, _DIARY_FALLBACK) == {
        "count": 0,
        "denominator": 0,
    }


def test_the_harness_reads_the_nodes_own_fallback_constant() -> None:
    """``_diary_fallback_text`` returns the node's constant, never a copy of it.

    The loud half of the coupling: a copy in the harness would read a clean
    ``0.0`` for a run that was all placeholder the day someone rewords the
    sentence. A rename breaks this file at import; a reword breaks this
    assertion.
    """
    assert blunder_eval._diary_fallback_text() == _DIARY_FALLBACK
    assert blunder_eval._diary_fallback_text() is _DIARY_FALLBACK


# ===========================================================================
# 6. The renders — the table cell, the drill-down lines, and the gate.
# ===========================================================================

_ARM_LINE = "  private_diaries_enabled: {value}\n"
_QUALITY_LINES = (
    "  diary_fallback_rate: {rate}\n"
    "  diary_fallback_entries: {entries}\n"
    "  diary_entries_attempted: {attempted}\n"
)


def _doc(*, arm: str = "", quality: str = "") -> str:
    """A spec-039-era record document, with the two conditional blocks optional."""
    return textwrap.dedent(
        """\
        run:
          date: '2026-09-02'
          metrics_version: 1
        code:
          commit: '1111222233334444555566667777888899990000'
          branch: 'spec-039-ai-private-diaries'
          dirty: false
        provider:
          name: 'ollama'
          large_model: 'qwen3-coder:30b'
          small_model: 'qwen2.5:3b'
        settings:
          large_model: 'qwen3-coder:30b'
          small_model: 'qwen2.5:3b'
          base_url: 'http://localhost:11434'
          games: 1
          seed: null
          max_days: 12
          scripted_player: 'active'
        {arm}  lineup:
            num_citizens: 5
            num_mafia: 2
        quality:
          games_attempted: 1
          games_completed: 1
          games_failed_early: 0
        {quality}  duration_seconds: 400.0
        metrics:
          repetition:
            rate: 0.5
            count: 10
            denominator: 20
        notes: ''
        """
    ).format(arm=arm, quality=quality)


def _write(tmp_path: Path, *docs: str) -> Path:
    """Write a ``---``-separated multi-document ledger into ``tmp_path``.

    Never the committed file: this is the temp-ledger convention
    ``tests/test_ledger_viewer.py`` established for exactly this reason.
    """
    path = tmp_path / "blunder-ledger.yaml"
    path.write_text("".join(f"---\n{doc}" for doc in docs), encoding="utf-8")
    return path


def _one(tmp_path: Path, doc: str) -> dict[str, Any]:
    """Load the single record from a one-document temp ledger."""
    (record,) = load_ledger(_write(tmp_path, doc))
    return record


def _section(detail: str, name: str) -> list[str]:
    """One ``render_detail`` section — its header plus its fields."""
    lines = detail.splitlines()
    start = lines.index(name)
    end = start + 1
    while end < len(lines) and lines[end].strip():
        end += 1
    return lines[start:end]


def test_the_on_off_cell_spellings_are_the_cli_vocabulary() -> None:
    """``_DIARIES_ON`` / ``_DIARIES_OFF`` are ``"on"`` / ``"off"``, as bare literals.

    Every other assertion in this section reads the constants OR the literals.
    A guard that only imported the constants would rename along with them and
    stay green — the vacuity shape this spec proved by renaming ``KIND_DIARY``
    and watching the style-map guard pass.
    """
    assert _DIARIES_ON == "on"
    assert _DIARIES_OFF == "off"


@pytest.mark.parametrize(
    ("arm", "expected"),
    [
        pytest.param("true", "on", id="on-arm"),
        pytest.param("false", "off", id="off-arm"),
    ],
)
def test_the_diaries_cell_renders_the_arm_as_cli_vocabulary(
    tmp_path: Path, arm: str, expected: str
) -> None:
    """A present arm renders ``on`` / ``off`` — read at a glance, in the table."""
    record = _one(tmp_path, _doc(arm=_ARM_LINE.format(value=arm)))

    assert _diaries_cell(record) == expected


@pytest.mark.parametrize(
    "record",
    [
        pytest.param({}, id="no-record-keys"),
        pytest.param({"settings": {}}, id="settings-without-the-key"),
        pytest.param({"settings": {"scripted_player": "active"}}, id="pre-039-shape"),
        pytest.param({"settings": "not-a-map"}, id="malformed-settings"),
    ],
)
def test_an_absent_arm_renders_a_blank_cell_never_off(record: dict[str, Any]) -> None:
    """**Absence renders blank, never ``off``** — the one thing this cell exists for.

    Deliberately NOT ``_stand_in_cell``'s posture (absent ⇒ the prior default) or
    ``_kind_cell``'s (absent ⇒ "a played game"): a pre-039 record was played by a
    build with **no diary feature at all**, so it is neither arm. Rendering it
    ``off`` would assert a measurement nobody made and would silently offer every
    committed record as the control half of an A/B pair.

    The one-line change this kills: ``if arm is None`` → ``if not arm``, which
    blanks the genuine **off** arm and is invisible to any test that only checks
    the on arm and the absent case.
    """
    assert _diaries_cell(record) == ""


def test_the_off_arm_and_an_absent_arm_stay_distinct_in_the_table(
    tmp_path: Path,
) -> None:
    """One table, three records: ``on`` / ``off`` / blank, all three distinct.

    The positional half of the column: the cell is placed by index in the row
    tuple, so this also catches a mutation that swapped ``_diaries_cell`` with
    its neighbour — ``Stand-in`` is asserted on the same rows.
    """
    path = _write(
        tmp_path,
        _doc(arm=_ARM_LINE.format(value="true")),
        _doc(arm=_ARM_LINE.format(value="false")),
        _doc(),
    )
    model = build_table_model(load_ledger(path))
    diaries = model.columns.index("Diaries")
    stand_in = model.columns.index("Stand-in")

    assert [row[diaries] for row in model.rows] == ["on", "off", ""]
    # Not the neighbour's value: all three records carry ``active``.
    assert [row[stand_in] for row in model.rows] == ["active"] * 3


def test_the_diaries_column_is_a_head_column_and_metric_order_is_untouched() -> None:
    """``Diaries`` sits between ``Stand-in`` and ``Lineup``, ahead of the metric tail.

    Diaries is a **setting, not a metric**, so the UI's right-justify split
    (``len(columns) - len(METRIC_ORDER)``) must be undisturbed and no metric cell
    may move. Pinned three ways: the neighbours by index, the column's position
    strictly inside the fixed head, and ``METRIC_ORDER`` carrying no diary entry
    at all.
    """
    columns = build_table_model([]).columns
    fixed_count = len(columns) - len(METRIC_ORDER)

    assert fixed_count == len(_FIXED_COLUMNS)
    assert (
        columns.index("Stand-in")
        == columns.index("Diaries") - 1
        == columns.index("Lineup") - 2
    )
    assert columns.index("Diaries") < fixed_count
    assert "Diaries" not in [label for _, label in METRIC_ORDER]
    assert not [key for key, _ in METRIC_ORDER if "diar" in key]


@pytest.mark.parametrize(
    ("arm", "rendered"),
    [
        pytest.param("true", "True", id="on-arm"),
        # A present ``False`` still renders — ``_text(False)`` is ``"False"``, not
        # blank — so the diaries-OFF arm reads as the measurement it is.
        pytest.param("false", "False", id="off-arm"),
    ],
)
def test_the_drill_down_shows_the_records_own_value(
    tmp_path: Path, arm: str, rendered: str
) -> None:
    """The drill-down line shows the record's ACTUAL value, not the cell's label.

    The split is deliberate (§2.15): a drill-down line shows the on-disk value,
    like every other settings line, so cell text and stored value cannot drift;
    the table cell is read at a glance. A reviewer seeing ``on`` in the table and
    ``True`` in the detail should know they are one field, not two.
    """
    record = _one(tmp_path, _doc(arm=_ARM_LINE.format(value=arm)))

    section = _section(render_detail(record), "settings")

    assert f"  private_diaries_enabled: {rendered}" in section
    # And in the record's own key order — after the stand-in, before the lineup.
    labels = [line.split(":", 1)[0].strip() for line in section[1:]]
    assert labels == [
        "large_model",
        "small_model",
        "base_url",
        "games",
        "seed",
        "max_days",
        "scripted_player",
        "private_diaries_enabled",
        "citizens",
        "mafia",
    ]


def test_the_arm_render_adds_exactly_one_line_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Absence gains NO line, and presence gains exactly one.

    §2.15's provable claim, pinned at the unit level: filter the arm line out of
    the with-arm render and the two are byte-identical. That is what "the diaries
    renders add nothing to a record that does not carry the field" means, and it
    is stronger than a substring check — an accidentally unconditional ``—`` line
    on every pre-039 record shows up here as a differing section, not as a
    passing test.
    """
    with_arm = render_detail(_one(tmp_path, _doc(arm=_ARM_LINE.format(value="false"))))
    without_arm = render_detail(_one(tmp_path, _doc()))

    assert "private_diaries_enabled" not in without_arm
    filtered = [
        line
        for line in with_arm.splitlines()
        if not line.startswith("  private_diaries_enabled:")
    ]
    assert filtered == without_arm.splitlines()


def test_the_scripted_player_render_adds_exactly_one_line(tmp_path: Path) -> None:
    """The adjacent gap this slice closed: ``scripted_player`` gains its own line.

    Written to every record since spec 026 and, until this slice, visible only as
    the table's ``Stand-in`` column. Asserted the same way as the arm — one added
    line, nothing else — because §2.15 records that "all 30 render byte-
    identically" and "close the ``scripted_player`` gap" cannot both hold.
    """
    without = _doc().replace("  scripted_player: 'active'\n", "")
    with_line = render_detail(_one(tmp_path, _doc()))
    without_line = render_detail(_one(tmp_path, without))

    assert "  scripted_player: active" in with_line
    assert "scripted_player" not in without_line
    filtered = [
        line
        for line in with_line.splitlines()
        if not line.startswith("  scripted_player:")
    ]
    assert filtered == without_line.splitlines()


def test_the_quality_diary_trio_renders_in_order_when_the_gate_is_present(
    tmp_path: Path,
) -> None:
    """Present denominator ⇒ three lines, in writer order, in the right band."""
    record = _one(
        tmp_path,
        _doc(quality=_QUALITY_LINES.format(rate="0.25", entries="1", attempted="4")),
    )

    section = _section(render_detail(record), "quality")

    labels = [line.split(":", 1)[0].strip() for line in section[1:]]
    assert labels == [
        "games_attempted",
        "games_completed",
        "games_failed_early",
        "diary_fallback_rate",
        "diary_fallback_entries",
        "diary_entries_attempted",
        "duration_seconds",
    ]
    assert "  diary_fallback_rate: 0.25" in section
    assert list(_DIARY_QUALITY_FIELDS) == labels[3:6]


def test_a_clean_zero_rate_renders_and_stays_distinct_from_absence(
    tmp_path: Path,
) -> None:
    """A stored ``0.0`` renders; an absent trio renders nothing. Both, in one test.

    §2.15's sharpest clarification: **the liveness gate is read off the
    DENOMINATOR, never the rate's truthiness**, because ``diary_fallback_rate:
    0.0`` is the genuinely CLEAN measurement and must stay distinct from absence.
    The one-line change this kills is ``_DIARY_QUALITY_GATE`` pointed at
    ``quality.diary_fallback_rate`` (or the guard rewritten as a truthiness
    test): the clean reading then vanishes and reads as "no diaries ran".
    """
    clean = _one(
        tmp_path,
        _doc(quality=_QUALITY_LINES.format(rate="0.0", entries="0", attempted="11")),
    )
    absent = _one(tmp_path, _doc())

    clean_detail = render_detail(clean)
    assert "  diary_fallback_rate: 0.0" in clean_detail
    assert "  diary_fallback_entries: 0" in clean_detail
    assert "  diary_entries_attempted: 11" in clean_detail

    absent_detail = render_detail(absent)
    for key in _DIARY_QUALITY_FIELDS:
        assert key not in absent_detail
    # "Adds nothing": filter the trio out of the clean render and the two agree.
    filtered = [
        line
        for line in clean_detail.splitlines()
        if not any(line.startswith(f"  {key}:") for key in _DIARY_QUALITY_FIELDS)
    ]
    assert filtered == absent_detail.splitlines()


def test_the_gate_is_the_denominator_not_the_rate(tmp_path: Path) -> None:
    """A rate written WITHOUT its denominator renders nothing — the gate is the pair.

    ``_DIARY_QUALITY_GATE`` names the denominator on purpose: a rate is by
    contract never written without it, so a record carrying only a rate is
    malformed and the reader must not present it as a measurement. This pins
    WHICH key is the gate, which a well-formed record cannot distinguish.
    """
    assert _DIARY_QUALITY_GATE == "quality.diary_entries_attempted"
    record = _one(tmp_path, _doc(quality="  diary_fallback_rate: 0.75\n"))

    section = _section(render_detail(record), "quality")

    assert not [line for line in section if "diary_" in line]


def test_the_rendered_rate_is_read_from_the_record_never_recomputed(
    tmp_path: Path,
) -> None:
    """A record whose stored rate disagrees with its operands renders the STORED rate.

    ``render_record`` derives the rate once, from the count/denominator pair,
    precisely so there is one definition of it. A second derivation in the reader
    would render a self-contradicting record as though it agreed — hiding the
    contradiction instead of showing it.
    """
    record = _one(
        tmp_path,
        _doc(quality=_QUALITY_LINES.format(rate="0.9", entries="1", attempted="10")),
    )

    detail = render_detail(record)

    assert "  diary_fallback_rate: 0.9" in detail
    assert "  diary_fallback_rate: 0.1" not in detail


# ===========================================================================
# 7. A read-only sweep over the COMMITTED ledger.
#
# Never a write target. The claim: the two new renders are CONDITIONAL, so a
# record that does not carry the fields gains not one line — proven over the
# real curated corpus rather than over a fixture that agrees with itself.
# ===========================================================================

_NO_LEDGER_REASON = (
    f"no committed ledger at {_COMMITTED_LEDGER} — the read-only corpus sweep "
    "needs the repo's curated records"
)
_requires_ledger = pytest.mark.skipif(
    not _COMMITTED_LEDGER.is_file(), reason=_NO_LEDGER_REASON
)


@_requires_ledger
def test_the_diaries_renders_add_nothing_to_a_committed_record_without_them() -> None:
    """Every committed record renders a diaries line **iff** it carries the field.

    Read-only: this test opens the repo-committed, append-only ledger and never
    writes to it. Written as an *iff* rather than as today's census (which is 0
    of 30 on both counts) so it stays true once spec 039's own measured campaign
    appends records that DO carry the arm — at which point the same rule is what
    proves those records render it.

    The ``at least one`` guard is the anti-vacuity half: a conditional render is
    trivially "correct" over an empty selection, so the sweep asserts that the
    absent branch is genuinely exercised by the corpus.
    """
    records = load_ledger(_COMMITTED_LEDGER)
    assert records, "the committed ledger parsed to no records"

    without_arm = 0
    without_trio = 0
    for record in records:
        settings = record.get("settings")
        quality = record.get("quality")
        has_arm = isinstance(settings, dict) and (
            settings.get("private_diaries_enabled") is not None
        )
        has_trio = isinstance(quality, dict) and (
            "diary_entries_attempted" in quality
        )
        detail = render_detail(record)
        date = (record.get("run") or {}).get("date")

        assert ("  private_diaries_enabled: " in detail) is has_arm, date
        assert ("  diary_fallback_rate: " in detail) is has_trio, date
        if not has_arm:
            without_arm += 1
            assert "private_diaries_enabled" not in detail, date
        if not has_trio:
            without_trio += 1
            assert not [
                line for line in detail.splitlines() if line.startswith("  diary_")
            ], date

    assert without_arm >= 1, "no committed record exercises the absent-arm branch"
    assert without_trio >= 1, "no committed record exercises the absent-trio branch"


@_requires_ledger
def test_every_committed_records_diaries_cell_matches_its_own_settings() -> None:
    """The ``Diaries`` column reads blank for a record with no arm, ``on``/``off`` else.

    The table half of the same conditional rule, over the same corpus — and the
    reason the column is safe to have added: a pre-039 record must not appear as
    the control half of an A/B pair.
    """
    records = load_ledger(_COMMITTED_LEDGER)
    model = build_table_model(records)
    column = model.columns.index("Diaries")

    blanks = 0
    for record, row in zip(records, model.rows, strict=True):
        settings = record.get("settings")
        arm = (
            settings.get("private_diaries_enabled")
            if isinstance(settings, dict)
            else None
        )
        if arm is None:
            blanks += 1
            assert row[column] == ""
        else:
            assert row[column] == (_DIARIES_ON if arm else _DIARIES_OFF)

    assert blanks >= 1, "no committed record exercises the blank cell"


@_requires_ledger
def test_the_scripted_player_line_tracks_exactly_the_records_carrying_it() -> None:
    """A ``scripted_player`` line appears iff the record carries the key.

    Both branches are exercised by the corpus — pre-026 records carry no
    ``settings.scripted_player`` and post-026 ones do — which is what makes the
    §2.15 claim provable: the render adds exactly one line to exactly the records
    that have the field, and nothing anywhere else.
    """
    records = load_ledger(_COMMITTED_LEDGER)

    with_key = 0
    without_key = 0
    for record in records:
        settings = record.get("settings")
        has = isinstance(settings, dict) and settings.get("scripted_player") is not None
        detail = render_detail(record)
        assert ("  scripted_player: " in detail) is has, (record.get("run") or {}).get(
            "date"
        )
        if has:
            with_key += 1
        else:
            without_key += 1

    assert with_key >= 1 and without_key >= 1


@_requires_ledger
def test_the_read_only_sweep_never_writes_the_committed_ledger(
    tmp_path: Path,
) -> None:
    """Belt-and-braces: the corpus sweep leaves the committed file byte-identical.

    The rule this file exists under is that the ledger is append-only, curated
    and repo-committed. A snapshot comparison is cheap and it turns "we were
    careful" into "we checked".
    """
    before = _COMMITTED_LEDGER.read_bytes()

    records = load_ledger(_COMMITTED_LEDGER)
    build_table_model(records)
    for record in records:
        render_detail(record)

    assert _COMMITTED_LEDGER.read_bytes() == before
