"""Offline tests for a measured run's **identity** — ``run.id`` (spec 043, Slice 2).

Pins what Slice 2 landed: :func:`~graphia.tools.blunder_eval.make_run_id`'s
collision-proof id, the ``run.id`` key ``render_record`` emits from it, the
single draw each of the ledger's **two** record producers takes
(``blunder_eval.run_eval`` and ``persona_bench.main``), and the fact that the
new suffix leaves ``eval_ledger``'s transcript browser resolving a run directory
exactly as before. No model, no network, no game: every test here is a string, a
hand-built dataclass, or a file under ``tmp_path``.

WHY A NEW MODULE RATHER THAN ``tests/test_ledger_write.py`` OR
``tests/test_blunder_eval.py``. Slice 1's ``test_ledger_write.py`` is about the
*write primitives* — a text splitter, its inverse, and a durable file replace,
none of which know what a record is. This module's subject is one level up and
orthogonal: **who wrote a record**, spread across three source modules
(``blunder_eval``, ``persona_bench``, ``eval_ledger``) that Slice 2 touched
together. ``test_blunder_eval.py`` is 2200 lines across six numbered concern
sections its own docstring enumerates, all of them about the *harness's*
surface; a seventh section spanning two other modules would be filed under the
wrong heading. The repo's precedent is topic-scoped ledger test modules —
``test_ledger_model.py``, ``test_ledger_viewer.py``,
``test_substitution_ledger.py``, ``test_blunder_eval_provenance.py``,
``test_ledger_write.py`` — and this is one more of them.

THE SCAFFOLDS ARE IMPORTED, NOT RE-DECLARED. Driving ``run_eval`` in-process
needs five seams stubbed (both provenance collectors, the model-name resolver,
``LEDGER_PATH``, ``_play_one_game``) and ``test_blunder_eval.py`` already owns
that list as ``_stub_run_eval_env``. Re-typing it here would let the two copies
drift, and a stale copy of an LLM/subprocess stub list is how a test starts
reaching a real boto3 client. Cross-module helper imports are an established
pattern in this suite (``test_career_stats.py``, ``test_dual_mode_smoke.py``,
``test_recap_aware_reasoning.py`` and six others do it), so the helpers are
imported from the modules that own them. Note that importing a module does NOT
import its fixtures: ``test_slice34_persona_bench.py``'s autouse process-env
guard does not apply here, which is why every ``persona_bench.main`` test below
stubs ``run_bench`` — that is the function which mutates ``os.environ``, so
stubbing it means nothing to restore.

THE COMMITTED LEDGER AND THE REAL TRANSCRIPT STORE ARE NEVER WRITTEN.
``evals/blunder-ledger.yaml`` is the project's only long-term measurement
history and an early-bound signature default once leaked ~25 synthetic records
into it. Every test that can reach the appender redirects
``blunder_eval.LEDGER_PATH`` at ``tmp_path`` (``_stub_run_eval_env`` does it for
the eval path; ``_stub_bench_record_env`` below does it for the bench path), and
every ``run_eval`` call injects ``transcripts_root=tmp_path/...`` so no directory
is ever created under ``evals/transcripts/``.

WHAT IS PINNED, AND WHY EACH ASSERTION EARNS ITS PLACE. The id's three
implementation choices are all load-bearing and all argued at the function, so
each gets a test that goes red if it is quietly undone:

* **Six lowercase hex characters.** The width diverges deliberately from
  technical-considerations §2.2's four-character illustration, because a
  collision is *silent*: the OCC splice replaces the chunk carrying this run's
  id, so a colliding run overwrites another run's record and the read-back —
  which only checks that exactly one chunk carries the id — passes. Width and
  character class are pinned by ``_ID_PATTERN`` over many draws, which also
  catches a switch to base32/base62 or to uppercase (case-insensitive APFS
  would then let two *distinct* ids name one directory).
* **:mod:`secrets`, not the seeded module-global :mod:`random`.** This module
  *seeds* that stream (``_seed_game`` calls ``random.seed(base_seed + i)``), so
  a suffix drawn from it would be reproducible and two same-second runs sharing
  a ``--seed`` would share a suffix. Today's safety is call *ordering*, which
  breaks silently when code moves — so two tests below assert the draw is
  provably decoupled from that stream: reseeding does not reproduce an id, and
  the draw does not advance the global state at all.
* **The suffix sits after the complete timestamp.** Lexical sort must stay
  chronological, including across the suffix-free legacy ids still on disk.

And the renderer's **two gates must not be collapsed into one condition**,
however alike they look: ``run.id`` is an IDENTITY, emitted whenever the run has
one; ``run.transcript_dir`` is a LINK, emitted only when a transcript was
actually written, so it never dangles. Two standing cases separate them, and
both are asserted — a run whose every game failed early (an id, no transcript),
and a ``persona-bench`` record (an id, and no transcript because a bench run
plays no game).

WHAT IS DELIBERATELY NOT HERE. Nothing tests ``quality.ended_early``: that is
Slice 4 and does not exist yet. ``upsert_record``, the progressive per-game
write and the pre-run backup DO now exist — Slice 3 landed them on 2026-09-12,
after this module was written, so the sentence above used to list them among the
not-yet-built alongside the marker. They are pinned in
``tests/test_progressive_ledger_write.py``, including the one place this
module's subject meets that one: the upsert recognises its own document by
``run.id``, so a ledger holding a near-twin id is tested there rather than here
(the id's *shape* is this module's, its *use as a match key* is that one's). The
ledger write primitives are ``test_ledger_write.py``'s. ``run.id``'s effect on
the viewer is not tested because it has none — Slice 5 changes the ``Games``
cell, not the id.
"""

from __future__ import annotations

import argparse
import random
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

# The ``run_eval`` scaffold, the renderer's synthetic fixtures and the bench
# summary/config stubs live with the test modules that own them (see the module
# docstring): re-declaring them here would let the copies drift apart, and a
# stale copy of a stub list is how a test starts reaching a real client. These
# sort above the ``graphia`` block because a sibling test module is not
# first-party to the package, not because they matter more.
from test_blunder_eval import (
    _capture_for_storage,
    _storage_args,
    _stub_run_eval_env,
    _synthetic_result,
    _top_level_keys,
)
from test_slice34_persona_bench import _BenchConfigStub, _summary

from graphia.eval_ledger import list_transcripts, transcript_dir_for
from graphia.tools import blunder_eval, persona_bench
from graphia.tools.blunder_eval import (
    EvalResult,
    make_run_id,
    render_record,
    run_eval,
)
from graphia.tools.persona_bench import RECORD_KIND, build_bench_record

# ---------------------------------------------------------------------------
# The id's documented shape, as one regex: the fixed-width ISO-ish local
# timestamp with ``:`` swapped for ``-``, then a ``-`` and SIX LOWERCASE HEX
# characters. ``fullmatch`` against this is the width assertion AND the
# character-class assertion at once, so it goes red both for
# ``token_hex(3)`` → ``token_hex(2)`` and for any richer/uppercase alphabet.
# 26 characters total (19 timestamp + 1 separator + 6 suffix).
# ---------------------------------------------------------------------------
_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[0-9a-f]{6}$")
_ID_LENGTH = 26

# A pinned moment, so "two ids from the SAME second" is expressed exactly rather
# than raced for. This is the collision the suffix exists to close: before spec
# 043 both calls below returned one identical string.
#
# NAIVE (no ``tzinfo``) DELIBERATELY, here and in every ``datetime(...)`` below.
# ``make_run_id`` calls ``datetime.now()`` and documents the id as a LOCAL
# timestamp — a caller who injects a ``now`` owns its timezone. Handing it an
# aware datetime would pin a form production never produces, and the id carries
# no offset for one to be read back out of, so there would be nothing to assert.
# (A tz-aware value renders the same string through ``strftime``, which is why
# this would be an invisible drift rather than a red test.) The date chosen is
# the real 2026-09-10 session whose three killed runs motivated spec 043, and
# whose committed transcript store holds the two same-second-ish pairs
# (``...18-41-01``/``-05``, ``...13-31-38``/``-42``) this collision is measured
# from rather than feared.
_PINNED = datetime(2026, 9, 10, 13, 31, 38)

# How many independent draws the character-class sweep takes. Six hex digits per
# draw ⇒ 384 characters, so an alphabet change (or an ``.upper()``) that left
# *some* digits numeric is still caught with overwhelming probability.
_DRAWS = 64


def _run_block_keys(doc: str) -> list[str]:
    """The keys of a rendered record's ``run:`` block, in document order.

    Key ORDER is the thing under test (``_yaml_block`` renders in insertion
    order, so insertion order *is* key order), and ``run.id`` must land
    immediately after ``date``. Reads the two-space-indented ``key:`` lines that
    follow the top-level ``run:`` header and stops at the next unindented key, so
    a leading ``---`` separator and the blocks below ``run`` are both ignored.
    """
    keys: list[str] = []
    inside = False
    for line in doc.splitlines():
        if not line.startswith(" "):
            inside = line.startswith("run:")
            continue
        if not inside or not line.startswith("  ") or line.startswith("    "):
            continue
        head, _, _ = line.partition(":")
        keys.append(head.strip())
    return keys


@pytest.fixture
def restore_global_random() -> Iterator[None]:
    """Snapshot and restore :mod:`random`'s global state around a test.

    Two tests below deliberately ``random.seed(...)`` the module-global stream to
    prove the id's suffix does not come from it. That stream is process-global
    and this project has **no** seed env var and no seeding fixture
    (architecture §6, ADR-006) — mechanical RNG decisions are pinned by
    monkeypatching the helper that uses them, never by a seed. So a test that
    seeds it must put it back, or it hands every later test in the session a
    pinned stream and turns a real non-determinism bug invisible.
    """
    state = random.getstate()
    try:
        yield
    finally:
        random.setstate(state)


# ===========================================================================
# 1. ``make_run_id`` — the id itself
#
# Three choices are argued at the function and all three are pinned here: the
# six-lowercase-hex suffix, its entropy source (``secrets``, NOT the seeded
# module-global ``random``), and its POSITION after the complete timestamp so
# lexical order stays chronological.
# ===========================================================================


def test_two_ids_from_the_same_pinned_second_differ() -> None:
    """The collision this function exists to close: one second, two runs, two ids.

    Before spec 043 the id was the timestamp alone, so both calls returned the
    same string — and ``write_transcript``'s ``mkdir(exist_ok=True)`` then let
    the second run overwrite the first's ``game-01.txt``. Under the OCC upsert
    the consequence is worse and silent: the splice replaces *the chunk carrying
    this run's id*, so a shared id lets one run overwrite another run's RECORD
    with no exception raised anywhere.
    """
    first = make_run_id(now=_PINNED)
    second = make_run_id(now=_PINNED)

    assert first != second
    # And the difference is the suffix alone — the timestamp halves are equal,
    # which is what makes this a same-second pair rather than two moments.
    assert first[:19] == second[:19] == "2026-09-10T13-31-38"


def test_many_ids_from_one_pinned_second_are_all_distinct() -> None:
    """Distinctness is not a lucky pair: 64 same-second draws collide not at all."""
    ids = [make_run_id(now=_PINNED) for _ in range(_DRAWS)]

    assert len(set(ids)) == _DRAWS


@pytest.mark.parametrize("draw", range(8))
def test_the_id_matches_its_documented_shape(draw: int) -> None:
    """``<timestamp>-<six lowercase hex>``, 26 characters — the documented form.

    The width diverges deliberately from technical-considerations §2.2's
    four-character illustration (``...-a7f3``): §2.2 fixes the SHAPE and leaves
    the width to implementation, and it landed at six because a collision is
    silent, so the probability was sized against the consequence rather than
    against annoyance — ≈6e-8 per same-second pair at 24 bits, against ≈1.5e-5
    at 16. A ``token_hex(3)`` → ``token_hex(2)`` "simplification" fails here.
    """
    run_id = make_run_id()

    assert _ID_PATTERN.fullmatch(run_id), f"unexpected id shape: {run_id!r}"
    assert len(run_id) == _ID_LENGTH


def test_the_suffix_alphabet_is_lowercase_hex_and_nothing_wider() -> None:
    """Every suffix character over 64 draws is in ``[0-9a-f]`` — no wider alphabet.

    Lowercase hex is a correctness choice, not a style one: macOS's default APFS
    is **case-insensitive**, so a mixed-case suffix could let two DISTINCT ids
    name one directory — reintroducing through the alphabet the very failure the
    suffix exists to close. Hex also excludes every ambiguous glyph (no
    ``l``/``1``, ``O``/``0``), which matters because this id is read off a
    record by eye and typed into a ``cd``. A switch to base32/base62, or an
    ``.upper()``, fails here: 64 draws is 384 characters, so a wider alphabet
    that happened to produce only digits in one draw cannot hide.
    """
    seen: set[str] = set()
    for _ in range(_DRAWS):
        seen.update(make_run_id()[20:])

    assert seen <= set("0123456789abcdef"), f"suffix alphabet widened: {sorted(seen)}"


def test_reseeding_the_module_global_stream_does_not_reproduce_an_id(
    restore_global_random: None,
) -> None:
    """The id is provably NOT a function of the seeded ``random`` stream.

    This is the assertion that catches someone "simplifying" :mod:`secrets` back
    to the module-global :mod:`random`. The point is not unpredictability — this
    is no security boundary — it is that ``blunder_eval`` deliberately SEEDS that
    stream (``_seed_game`` calls ``random.seed(base_seed + game_index)``) so
    ``--seed`` makes game structure reproducible. A suffix drawn from it would be
    reproducible too, and two same-second runs sharing a ``--seed`` — the
    multi-arm baseline pattern — would share a suffix and so an identity. Today
    the draw merely happens *before* the first ``_seed_game``; that is safety by
    call ORDERING, which breaks silently when code moves.
    """
    random.seed(4242)
    first = make_run_id(now=_PINNED)
    random.seed(4242)
    second = make_run_id(now=_PINNED)

    assert first != second, (
        "the suffix is reproducible from a re-seeded random stream — it must "
        "come from secrets, which cannot be seeded"
    )


def test_the_draw_does_not_consume_the_seeded_module_global_stream(
    restore_global_random: None,
) -> None:
    """The draw does not even ADVANCE ``random``'s global state.

    Sharper than the reseeding test above and for a different reason: a caller
    that seeded the stream for its own purposes must get the sequence it asked
    for, whether or not an id happens to be minted in the middle. ``_seed_game``
    is exactly such a caller.
    """
    random.seed(99)
    before = random.getstate()

    make_run_id(now=_PINNED)

    assert random.getstate() == before


def test_lexical_sort_of_ids_stays_chronological_regardless_of_suffix() -> None:
    """Increasing timestamps sort chronologically even with adversarial suffixes.

    The constraint that decides WHERE the suffix goes. It sits after the complete
    fixed-width zero-padded timestamp, so a comparison between two different
    seconds is settled inside the first 19 characters and the suffix never
    reaches it. Pinned with the worst possible suffixes: the earliest moment
    carries the lexically largest tail and the latest the smallest, so a
    suffix-first form (``<suffix>-<timestamp>``) inverts the order outright.
    """
    chronological = [
        make_run_id(now=datetime(2026, 9, 10, 13, 31, 38), suffix="ffffff"),
        make_run_id(now=datetime(2026, 9, 10, 13, 31, 42), suffix="aaaaaa"),
        make_run_id(now=datetime(2026, 9, 10, 13, 32, 1), suffix="555555"),
        make_run_id(now=datetime(2026, 9, 11, 0, 0, 0), suffix="000000"),
    ]

    assert sorted(chronological) == chronological


def test_a_same_second_pair_is_ordered_only_by_its_suffix() -> None:
    """Two ids from one second order arbitrarily — the right answer for a tie.

    The other half of the ordering claim, stated so the previous test is not read
    as promising an order it cannot deliver: runs that started in the same second
    have no chronological order to preserve.
    """
    low = make_run_id(now=_PINNED, suffix="000000")
    high = make_run_id(now=_PINNED, suffix="ffffff")

    assert low < high
    assert low[:19] == high[:19]


def test_a_legacy_suffix_free_id_still_interleaves_correctly() -> None:
    """Both forms are live in the tree, and they sort together correctly.

    Nothing was renamed: every directory already under ``evals/transcripts/`` and
    every older record's ``run.transcript_dir`` keeps its suffix-free name. A
    string sorts before itself plus a suffix, so a legacy id can only precede a
    suffixed id of its OWN second — never one from an earlier second.
    """
    legacy = "2026-09-10T13-31-38"
    chronological = [
        make_run_id(now=datetime(2026, 9, 10, 13, 31, 37), suffix="ffffff"),
        legacy,
        make_run_id(now=_PINNED, suffix="000000"),
        make_run_id(now=datetime(2026, 9, 10, 13, 31, 39), suffix="000000"),
    ]

    assert sorted(chronological) == chronological


def test_the_timestamp_half_is_the_path_safe_swapped_iso_form() -> None:
    """``:`` (illegal in a Windows path, awkward everywhere) is swapped for ``-``."""
    run_id = make_run_id(now=datetime(2026, 6, 18, 14, 32, 5), suffix="9f3a1c")

    assert run_id == "2026-06-18T14-32-05-9f3a1c"
    assert ":" not in run_id


def test_an_injected_suffix_is_used_verbatim_so_a_test_can_pin_the_whole_string() -> (
    None
):
    """``now`` + ``suffix`` together make the id fully deterministic.

    The test seam Slice 3's upsert tests will lean on. Both are documented as
    used verbatim and unvalidated — a caller who injects a ``suffix`` owns its
    legality as a path segment, just as one who injects a ``now`` owns its
    timezone — so an empty suffix is passed through rather than defaulted, which
    is the behaviour a future "helpfully fall back to a draw" edit would break.
    """
    assert make_run_id(now=_PINNED, suffix="abc") == "2026-09-10T13-31-38-abc"
    assert make_run_id(now=_PINNED, suffix="") == "2026-09-10T13-31-38-"


# ===========================================================================
# 2. The renderer's TWO GATES — ``run.id`` is an IDENTITY, ``run.transcript_dir``
#    is a LINK, and they must not be collapsed into one condition.
#
# They carry the same string on a game run (``run_eval`` sets both from one
# draw) which is exactly why the temptation exists. But they answer different
# questions, and two standing cases separate them: a run whose every game failed
# early has an identity and wrote no transcript, and a ``persona-bench`` record
# has an identity and plays no game at all.
# ===========================================================================


def test_an_eval_results_run_id_defaults_to_empty() -> None:
    """The field's default is the absent value, so nothing has to opt out of it.

    Half of why absence stays TWO-valued: a producer that does not know its own
    identity (the renderer's own unit fixtures, every hand-built record in the
    suite) gets the pre-043 rendering by default rather than by remembering to
    clear a field. A default of ``None`` would work as well for the gate and
    worse for the type, since every consumer treats it as a string.
    """
    assert EvalResult(provider="bedrock").run_id == ""


def test_a_bare_synthetic_result_renders_no_run_id_line() -> None:
    """Absence is TWO-VALUED: no ``run.id`` ⇒ a record written before spec 043.

    ``run_id`` is conditional on the ``kind`` / ``transcript_dir`` pattern, so
    every one of the 50 committed records and every synthetic-record test renders
    byte-identically and nothing is backfilled. An unconditional
    ``run_block["id"] = result.run_id`` would write ``id: ''`` into a record
    whose run never had an identity — an empty-string third value where the
    contract has two.
    """
    doc = render_record(_synthetic_result(), "2026-06-13")

    assert "id" not in _run_block_keys(doc)
    assert "  id:" not in doc


def test_a_bare_synthetic_results_key_order_is_unchanged() -> None:
    """Both the top-level order and the ``run`` block's own order are untouched.

    The addition is purely additive, which is what makes it safe without a
    ``METRICS_VERSION`` bump (the ``run.kind`` / ``transcript_dir`` precedent).
    The top-level order is separately pinned in ``test_blunder_eval.py``; what is
    asserted here is the pair TOGETHER for the one input that must render as it
    always did.
    """
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
    assert _run_block_keys(doc) == ["date", "duration_seconds", "metrics_version"]


def test_run_id_renders_immediately_after_date_and_before_kind() -> None:
    """``id`` sits between ``date`` and ``kind`` — insertion order IS key order.

    ``_yaml_block`` renders in insertion order, so position is decided by where
    the key is inserted rather than by a sort. It goes first among the
    conditionals because it names the run everything below it describes.
    """
    result = _synthetic_result()
    result.run_id = "2026-09-10T13-31-38-9f3a1c"
    result.kind = RECORD_KIND

    doc = render_record(result, "2026-06-13")

    assert _run_block_keys(doc)[:3] == ["date", "id", "kind"]
    assert "  id: '2026-09-10T13-31-38-9f3a1c'" in doc


def test_an_id_is_rendered_even_when_no_transcript_was_written() -> None:
    """The two gates, separated: an IDENTITY with no LINK.

    This is the case that stops the two conditions being collapsed into one. A
    run whose every game failed early wrote no transcript — so
    ``transcript_dir`` is correctly absent, and a dangling link is what that gate
    exists to prevent — but it still HAS an identity, and still needs its
    document findable by the upsert that will rewrite it. Gating ``id`` on
    ``transcript_dir`` fails here.
    """
    result = _synthetic_result()
    result.run_id = "2026-09-10T13-31-38-9f3a1c"
    result.transcript_dir = ""

    doc = render_record(result, "2026-06-13")

    assert "  id: '2026-09-10T13-31-38-9f3a1c'" in doc
    assert "transcript_dir" not in _run_block_keys(doc)


def test_the_link_is_still_rendered_beside_the_id_when_both_are_present() -> None:
    """On a game run the two carry the SAME string, at their own two positions.

    The pair a reader cross-references by eye: ``run.id`` names the run and
    ``run.transcript_dir`` names the directory under ``evals/transcripts/``, and
    on a game run they are identical BY CONSTRUCTION (one draw feeds both).
    Asserted so a future gate change cannot make ``id`` displace the link.
    """
    run_id = "2026-09-10T13-31-38-9f3a1c"
    result = _synthetic_result()
    result.run_id = run_id
    result.transcript_dir = run_id

    doc = render_record(result, "2026-06-13")

    assert _run_block_keys(doc) == [
        "date",
        "id",
        "duration_seconds",
        "metrics_version",
        "transcript_dir",
    ]
    assert doc.count(f"'{run_id}'") == 2


# ===========================================================================
# 3. ``run_eval`` — ONE draw per run, and ``run.id`` == ``run.transcript_dir``
#    == the directory actually created under the injected transcripts root.
#
# Driven in-process through ``test_blunder_eval.py``'s ``_stub_run_eval_env``
# scaffold with a stubbed ``_play_one_game``: no graph, no provider, no git, and
# both the ledger and the transcripts root under ``tmp_path``.
# ===========================================================================


def _one_completed_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``_play_one_game`` to a hand-built capture that COMPLETES."""
    monkeypatch.setattr(
        blunder_eval,
        "_play_one_game",
        lambda args, game_index: _capture_for_storage("Cara"),
    )


def _counting_make_run_id(monkeypatch: pytest.MonkeyPatch, module: object) -> list[str]:
    """Wrap ``module.make_run_id`` in a counter, returning the list of ids drawn.

    A COUNTING wrapper rather than a fixed-value stub: the real function still
    runs, so a test can assert the number of draws AND the shape of what was
    drawn from the same run. ``module`` is the namespace whose name is being
    rebound — ``blunder_eval`` for the eval path, ``persona_bench`` for the bench
    path, which binds its own reference via a module-level ``from ... import``.
    """
    drawn: list[str] = []
    real = blunder_eval.make_run_id

    def _counting(
        now: datetime | None = None, *, suffix: str | None = None
    ) -> str:
        run_id = real(now=now, suffix=suffix)
        drawn.append(run_id)
        return run_id

    monkeypatch.setattr(module, "make_run_id", _counting)
    return drawn


def test_run_eval_records_the_id_it_gave_its_transcript_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``run.id`` == ``run.transcript_dir`` == the directory on disk, by construction.

    ``run_eval`` takes ONE ``make_run_id`` draw before game 1 and feeds it to
    both the transcript writer and the record, so the by-eye cross-reference from
    a ledger record to ``evals/transcripts/<run-id>/`` cannot drift. Asserted on
    all three at once — the result's two fields, the rendered document's two
    lines, and the name of the directory that actually appeared — because the
    claim is that they are one string, not three agreeing ones.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    transcripts_root = tmp_path / "transcripts"
    run_id = "2026-09-10T13-31-38-9f3a1c"
    _one_completed_game(monkeypatch)

    result = run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=transcripts_root,
        run_id=run_id,
    )

    assert result.run_id == run_id
    assert result.run_id == result.transcript_dir
    assert (transcripts_root / result.run_id).is_dir()

    text = ledger.read_text(encoding="utf-8")
    assert f"  id: '{run_id}'" in text
    assert f"  transcript_dir: '{run_id}'" in text


def test_run_eval_draws_a_real_suffixed_id_and_names_its_directory_with_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With nothing injected, the drawn id is a real suffixed one AND the dir name.

    The previous test pins the plumbing with a hand-written id; this one pins that
    the string a REAL run mints is the same string the store is keyed by — the
    half a pinned id cannot show. The transcripts root holds exactly one
    directory and its name is the record's ``run.id``.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    transcripts_root = tmp_path / "transcripts"
    _one_completed_game(monkeypatch)

    result = run_eval(
        config, _storage_args(games=1), transcripts_root=transcripts_root
    )

    assert _ID_PATTERN.fullmatch(result.run_id), f"not a spec-043 id: {result.run_id!r}"
    assert result.run_id == result.transcript_dir
    assert [p.name for p in transcripts_root.iterdir()] == [result.run_id]
    assert f"  id: '{result.run_id}'" in ledger.read_text(encoding="utf-8")


def test_run_eval_draws_its_run_id_exactly_once_across_a_multi_game_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exactly ONE draw per run, however many games it plays.

    The discipline the whole identity rests on. A second
    ``make_run_id()`` — to fill the record rather than reusing the local the
    transcript writer already has — would mint a second random suffix and point
    the record at a directory that is not its own. The count is the assertion a
    value comparison cannot make: two draws in the same second differ only in
    their suffix, so the bug would look like a typo rather than a re-draw.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    drawn = _counting_make_run_id(monkeypatch, blunder_eval)
    _one_completed_game(monkeypatch)

    result = run_eval(
        config, _storage_args(games=3), transcripts_root=tmp_path / "transcripts"
    )

    assert len(drawn) == 1, f"expected one draw per run, got {len(drawn)}: {drawn}"
    assert drawn == [result.run_id]


def test_run_eval_with_an_injected_run_id_draws_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An injected id fully replaces the draw — the seam the suite runs on.

    Every transcript-storage test in the suite injects ``run_id=`` to keep its
    files under ``tmp_path`` with a pinned name. If the record ever took its own
    draw instead of reusing the injected local, those tests would keep passing
    while the record silently pointed elsewhere; this asserts the draw does not
    happen at all.
    """
    config, _ledger = _stub_run_eval_env(monkeypatch, tmp_path)
    drawn = _counting_make_run_id(monkeypatch, blunder_eval)
    _one_completed_game(monkeypatch)

    result = run_eval(
        config,
        _storage_args(games=1),
        transcripts_root=tmp_path / "transcripts",
        run_id="2026-09-10T13-31-38-9f3a1c",
    )

    assert drawn == []
    assert result.run_id == "2026-09-10T13-31-38-9f3a1c"


def test_a_run_whose_every_game_failed_early_still_records_an_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The live case that separates the two gates, end to end through ``run_eval``.

    Section 2 pins this on the renderer with a hand-built result; this pins that
    the harness actually produces it. Every game raises, so no transcript is
    written and ``run.transcript_dir`` is correctly absent — yet the record must
    still name the run that wrote it, because that is the string the upsert will
    match on. Gating ``run.id`` on ``transcript_dir`` fails here on real bytes.
    """
    config, ledger = _stub_run_eval_env(monkeypatch, tmp_path)

    def _boom(args: argparse.Namespace, game_index: int) -> object:
        raise RuntimeError("game exploded")

    monkeypatch.setattr(blunder_eval, "_play_one_game", _boom)

    result = run_eval(
        config, _storage_args(games=2), transcripts_root=tmp_path / "transcripts"
    )

    assert result.games_completed == 0
    assert result.games_failed_early == 2
    assert result.transcript_dir == ""
    assert _ID_PATTERN.fullmatch(result.run_id)

    text = ledger.read_text(encoding="utf-8")
    assert f"  id: '{result.run_id}'" in text
    assert "transcript_dir" not in _run_block_keys(text)


# ===========================================================================
# 4. ``persona_bench`` — the id is universal for NEW records, not eval-only
#    (spec 043 Task 2.2b).
#
# ``persona_bench`` is the ledger's OTHER record producer: it builds its own
# ``EvalResult`` and reaches the same ``render_record`` through
# ``append_record``. Technical-considerations §2.2 ("always present, on every
# record from now on") and ADR 014 §5 ("every record gains a permanent
# ``run.id``") were made true in CODE rather than carved out in prose, so a bench
# record carries an id too — and is the standing second proof that ``id`` and
# ``transcript_dir`` are separate gates, since a bench run writes no transcripts.
#
# ``run_bench`` is stubbed in every test here: it is the function that mutates
# ``os.environ`` (``GRAPHIA_LLM_PROVIDER`` plus the cloud-store pops), so
# stubbing it leaves nothing to restore and needs no model.
# ===========================================================================


def _stub_bench_record_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Stub everything ``persona_bench.main --record`` touches; return the ledger.

    Four seams, each for a reason the suite cares about: ``run_bench`` (no model,
    and no ``os.environ`` mutation to undo), ``load_config`` (no ``.env``, no
    provider resolution — ``main`` imports it at CALL time, so the patch lands on
    ``graphia.config``), the two provenance collectors (no ``git`` subprocess and
    no GET to ``localhost:11434``), and ``LEDGER_PATH`` at ``tmp_path`` so the
    committed ledger is never written. ``append_record`` itself is left REAL,
    because the rendered bytes it writes are what these tests read back.
    """
    monkeypatch.setattr(
        persona_bench, "run_bench", lambda **kwargs: _summary(attempted=2, completed=2)
    )
    monkeypatch.setattr("graphia.config.load_config", _BenchConfigStub)
    monkeypatch.setattr(
        persona_bench,
        "collect_code_provenance",
        lambda repo_root: {"commit": None, "branch": None, "dirty": False},
    )
    monkeypatch.setattr(
        persona_bench,
        "collect_provider_provenance",
        lambda provider, large_model, small_model, base_url: {
            "name": provider,
            "large_model": large_model,
            "small_model": small_model,
        },
    )
    ledger = tmp_path / "ledger.yaml"
    monkeypatch.setattr(blunder_eval, "LEDGER_PATH", ledger)
    return ledger


def test_a_bench_record_with_an_injected_id_renders_it_and_no_transcript_link() -> None:
    """An IDENTITY with no LINK — the bench path's deliberate non-pair.

    The game path documents ``run.id`` and ``run.transcript_dir`` as the same
    string by construction, so a reader arriving from there expects a pair here
    too. There is none, and that is correct: a bench run plays no game and writes
    no transcripts, so a link would dangle while the identity is as real as any
    other run's. This is the assertion Task 2.2b asked for beside the
    bare-synthetic case in section 2, and the two are needed together — one pins
    that an id is emitted whenever a run HAS one, the other that nothing is
    backfilled onto a run that never did.
    """
    result = build_bench_record(
        _summary(),
        config=_BenchConfigStub(),
        run_id="2026-09-10T13-31-38-9f3a1c",
    )

    assert result.run_id == "2026-09-10T13-31-38-9f3a1c"
    assert result.transcript_dir == ""

    doc = render_record(result, "2026-09-10")
    assert _run_block_keys(doc)[:3] == ["date", "id", "kind"]
    assert "  id: '2026-09-10T13-31-38-9f3a1c'" in doc
    assert "transcript_dir" not in _run_block_keys(doc)
    assert f"  kind: '{RECORD_KIND}'" in doc


def test_build_bench_record_without_an_injected_id_renders_no_run_id() -> None:
    """Un-injected ⇒ empty ⇒ the key is omitted, keeping the mapping pure.

    The id is injected rather than drawn inside ``build_bench_record`` for the
    same reason the two provenance blocks are: the function is a pure mapping and
    promises nothing about how many times it is called, while the draw must happen
    exactly ONCE per run. So its default has to render as honestly absent — which
    is also what keeps the builder unit-testable with no clock and no entropy
    source.
    """
    result = build_bench_record(_summary(), config=_BenchConfigStub())

    assert result.run_id == ""
    assert "id" not in _run_block_keys(render_record(result, "2026-09-10"))


def test_persona_bench_main_with_record_writes_a_run_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``--record`` ⇒ the appended bench document carries a real ``run.id``.

    The universality claim on real bytes: ``main`` draws an id and injects it, so
    EVERY new record in the ledger is self-identifying and correlatable to its run
    without inference. Dropping the injection leaves a record that reads like a
    pre-spec-043 one — and absence is two-valued, so it would be MIS-read as one.
    """
    ledger = _stub_bench_record_env(monkeypatch, tmp_path)

    rc = persona_bench.main(["--provider", "ollama", "--rosters", "2", "--record"])

    assert rc == 0
    text = ledger.read_text(encoding="utf-8")
    ids = re.findall(r"^  id: '(.+)'$", text, re.MULTILINE)
    assert len(ids) == 1, f"expected exactly one run.id in the record, got {ids}"
    assert _ID_PATTERN.fullmatch(ids[0]), f"not a spec-043 id: {ids[0]!r}"
    # Immediately after ``date``, and with NO link beside it.
    assert _run_block_keys(text)[:3] == ["date", "id", "kind"]
    assert "transcript_dir" not in _run_block_keys(text)


def test_persona_bench_main_draws_its_run_id_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One draw on the record path — the same discipline ``run_eval`` applies.

    Two draws would mean two random suffixes and so two identities for one run.
    The count is asserted rather than inferred because the id ``main`` keeps and
    the id it writes would differ only in six characters.
    """
    ledger = _stub_bench_record_env(monkeypatch, tmp_path)
    drawn = _counting_make_run_id(monkeypatch, persona_bench)

    persona_bench.main(["--provider", "ollama", "--rosters", "2", "--record"])

    assert len(drawn) == 1, f"expected one draw per run, got {len(drawn)}: {drawn}"
    assert f"  id: '{drawn[0]}'" in ledger.read_text(encoding="utf-8")


def test_persona_bench_main_without_record_draws_no_run_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The throwaway dev-loop run draws nothing and writes nothing.

    ``--record`` is off by default because most bench runs are throwaway, and the
    whole recording branch — provenance collection, the id draw, the append — is
    inside it. So the common run stays free of a git subprocess, two HTTP GETs and
    an entropy draw, and leaves no ledger behind to be mistaken for a measurement.
    """
    ledger = _stub_bench_record_env(monkeypatch, tmp_path)
    drawn = _counting_make_run_id(monkeypatch, persona_bench)

    rc = persona_bench.main(["--provider", "ollama", "--rosters", "2"])

    assert rc == 0
    assert drawn == []
    assert not ledger.exists()


# ===========================================================================
# 5. ``eval_ledger`` — the suffix cannot break the transcript browser
#    (technical-considerations §2.2's verify-at-implementation item).
#
# §2.2 left exactly one open question: does anything read this id as a
# TIMESTAMP? A strict parser would reject ``...-9f3a1c``, and the break would
# surface in the browser rather than at the mint. It does not: the id is an
# OPAQUE DIRECTORY NAME to every consumer — joined into a path by
# ``transcript_path`` and resolved back by name by ``transcript_dir_for``. These
# tests are what stop a future "tidy the run id into a datetime" edit from
# silently orphaning every suffixed run's transcripts.
# ===========================================================================


@pytest.mark.parametrize(
    "recorded_id",
    [
        # A legacy suffix-free name, still on disk and in 50 committed records —
        # nothing was renamed, so BOTH forms are live and read by one code path.
        "2026-06-13T14-32-05",
        # The spec-043 form.
        "2026-09-10T13-31-38-9f3a1c",
        # A suffix that is all letters, so a ``rsplit("-")``-and-parse edit
        # cannot coincidentally survive on digits.
        "2026-09-10T13-31-38-abcdef",
    ],
)
def test_transcript_dir_for_resolves_a_recorded_id_of_either_form(
    tmp_path: Path, recorded_id: str
) -> None:
    """The recorded NAME is joined onto the ledger's sibling ``transcripts/``.

    No parsing, no reformatting: whatever the record says is what gets joined, so
    a suffixed id resolves exactly as a suffix-free one does.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    record = {"run": {"transcript_dir": recorded_id}}

    assert transcript_dir_for(record, ledger) == tmp_path / "transcripts" / recorded_id


def test_transcript_dir_for_resolves_a_freshly_minted_id(tmp_path: Path) -> None:
    """The two ends meet: an id from ``make_run_id`` resolves through the browser.

    The parametrized cases above use literals, which a change to the mint could
    outlive. This one draws a real id, so mint and resolve are pinned as one
    round trip.
    """
    run_id = make_run_id()
    ledger = tmp_path / "blunder-ledger.yaml"

    assert transcript_dir_for({"run": {"transcript_dir": run_id}}, ledger) == (
        tmp_path / "transcripts" / run_id
    )


def test_list_transcripts_reads_a_suffixed_run_directory(tmp_path: Path) -> None:
    """The full read path over real files in a real suffixed directory.

    ``transcript_dir_for`` only computes a path; this walks the rest of what the
    viewer does — glob ``game-*.txt`` inside the resolved dir and label each entry
    from the FILE stem, never from the directory name. The per-game labels and
    their ``game-01 … game-NN`` order are unaffected by the suffix.
    """
    run_id = make_run_id()
    ledger = tmp_path / "blunder-ledger.yaml"
    run_dir = tmp_path / "transcripts" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "game-01.txt").write_text("first game", encoding="utf-8")
    (run_dir / "game-02.txt").write_text("second game", encoding="utf-8")

    entries = list_transcripts({"run": {"transcript_dir": run_id}}, ledger)

    assert [entry.label for entry in entries] == ["game-01", "game-02"]
    assert [entry.path.parent.name for entry in entries] == [run_id, run_id]
    assert entries[0].path.read_text(encoding="utf-8") == "first game"


def test_a_record_with_no_transcript_dir_still_resolves_to_none(
    tmp_path: Path,
) -> None:
    """An id without a link resolves to ``None``, not to the ledger's parent.

    The bench record and the all-failed run both carry ``run.id`` and no
    ``run.transcript_dir``, which makes this the shape the browser now meets
    routinely rather than only on pre-017 records. It must stay the documented
    "no transcripts" state — a ``run.id`` fallback here would point the browser
    at a directory that was never created.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    record = {"run": {"id": "2026-09-10T13-31-38-9f3a1c"}}

    assert transcript_dir_for(record, ledger) is None
    assert list_transcripts(record, ledger) == []
