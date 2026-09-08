"""Spec 041 Slice 11, Task 11.2 — the offline net under ADR-013's live gate.

``src/graphia/tools/ollama_smoke.py`` is the acceptance gate for ADR 013's
grammar-constrained decoding, and until this module it **had no test at all**.
That is how a gate which observed a genuine **80% ``Diary`` failure rate** —
four times its own advisory bound — printed ``RELIABLE`` and exited ``0``. Four
defects sat in nine lines of that file, and every one of them was an *absence*
rather than a wrong answer, which is exactly the class of defect a suite
catches and a reader does not.

What is under test
------------------

Four **pure** functions, all side-effect free and all reading only their
arguments plus (deliberately) one module constant:

* ``SCHEMA_NAMES`` — the schema vocabulary, now **derived** from
  ``llm.GAMEPLAY_SCHEMAS`` rather than hand-typed.
* ``_schema_rows`` — the ``(name, stats)`` rows the printed table, the JSON
  record and the verdict all read. One function, so those three cannot
  disagree again.
* ``_unreliable_reasons`` — the four gate conditions, each as printable text.
  **This is the interesting one**, and the one the listed coverage items are
  written against: it is what names *why*, and the reason text is what a
  developer actually reads at 2am.
* ``_judge`` — one word over that tuple. The least interesting member; it gets
  a delegation test and nothing more.

Plus the two reporting surfaces those four feed — ``_print_pair_report``
and ``_json_payload``, checked against each other rather than snapshotted —
``_parse_pairs`` (the CLI's only rejection), and the module's **import
inertness**, which became a fact worth asserting the moment the module started
importing ``graphia.llm`` at module scope.

Derivation or rule — stated per test, on purpose
------------------------------------------------

``_schema_rows`` / ``_unreliable_reasons`` / ``_judge`` all take
``schema_names: Sequence[str] | None = None``. ``None`` reads the module tuple
at **call** time — never bound as a signature default, because that
early-binding shape is what once let a redirected constant be ignored and wrote
synthetic records into a committed ledger. Passing a tuple states the
vocabulary at the call site.

So every test below says in its docstring which of two things it pins:

* **the derivation** — omits ``schema_names`` (or monkeypatches
  ``SCHEMA_NAMES``), and is therefore *about* the coupling to
  ``llm.GAMEPLAY_SCHEMAS``;
* **the rule** — passes an explicit vocabulary, and is therefore about the
  condition itself over a fixed, locally-visible set of names.

A test that silently depends on the enum's contents is how the four-of-seven
hole survived in the first place, so the choice is never left implicit.

Anti-vacuity: the old ``_judge``, reconstructed
-----------------------------------------------

A regression test for a fixed hole is worthless if it would also pass against
the code that had the hole. So :func:`_legacy_judge` below is the pre-Task-11.1
body, verbatim, with only the module constant lifted to a parameter — and the
two central regression tests assert the **contrast**: for the same
``PairReport``, the old judge says ``RELIABLE`` where the new one says
``UNRELIABLE`` and names why.

:func:`test_the_reconstructed_legacy_judge_is_not_a_stub` is the control on
that contrast. Without it, a reconstruction that returned ``"RELIABLE"``
unconditionally would make every contrast above look like evidence.

What this module must not do
----------------------------

**Invoke nothing.** ``ollama_smoke`` is imported *lazily inside* each test —
via :func:`_harness` — so a moved or renamed symbol is a test failure rather
than a collection error that takes the whole file down. ``main`` is never
called: it mutates ``os.environ`` in eight places directly (not through
``monkeypatch``) before it ever reaches ``_parse_pairs``, so calling it would
leak forced provider/flag settings into every test that ran after it. The
argument-rejection tests therefore drive ``_parse_pairs`` itself, which is
where the rejection lives.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import inspect
import io
import json
import os
import subprocess
import sys
import types
from argparse import Namespace
from contextlib import redirect_stdout
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Lazy access to the harness — never a module-scope import of it.
# ---------------------------------------------------------------------------

_HARNESS_MODULE = "graphia.tools.ollama_smoke"

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _harness() -> Any:
    """Import the harness at call time.

    Inside the tests rather than at module scope so that a symbol this file
    reaches for — ``_unreliable_reasons``, ``_schema_rows``, ``SCHEMA_NAMES`` —
    going missing fails the tests that use it, instead of erroring during
    collection and hiding the rest of the file behind an import error.
    """
    return importlib.import_module(_HARNESS_MODULE)


# The attempt count a "clean" schema row carries. Any positive number will do;
# named so no test has to explain why it chose 4.
_CLEAN_ATTEMPTS = 4


def _stats(
    *,
    attempts: int = 0,
    failures: int = 0,
    fallbacks: int = 0,
    last_error: str | None = None,
) -> Any:
    """One ``SchemaStats``, reached through the harness's own binding.

    Through ``ollama_smoke.SchemaStats`` rather than a direct import of
    ``graphia.tools.instrument``, so this file also pins that the harness still
    reads its stats type from the shared instrument module — the extraction the
    counting behaviour was moved through.
    """
    return _harness().SchemaStats(
        attempts=attempts,
        failures=failures,
        fallbacks=fallbacks,
        last_error=last_error,
    )


def _census(
    *,
    drop: Sequence[str] = (),
    substituted: dict[str, int] | None = None,
    attempted: dict[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    """A substitution census, built from the canonical clean one and then bent.

    ``_SubstitutionCensus().as_report()`` is the canonical clean census — all
    seven kinds present, every count zero — and the cheapest way to build one
    without retyping seven keys. ``drop`` simulates an instrument that was
    never installed; ``substituted`` simulates a game that fell back.
    """
    report = _harness()._SubstitutionCensus().as_report()
    for kind in drop:
        del report[kind]
    for kind, count in (substituted or {}).items():
        report[kind]["substituted"] = count
    for kind, count in (attempted or {}).items():
        report[kind]["attempted"] = count
    return report


def _clean_report(*, schema_names: Sequence[str], **overrides: Any) -> Any:
    """A ``PairReport`` that breaches **none** of the four gate conditions.

    ``schema_names`` is **required and has no default**, which is the same
    discipline the production seam keeps: a test that wants the derived
    vocabulary passes ``SCHEMA_NAMES`` explicitly and *says so*, rather than
    inheriting it invisibly from a helper.

    Clean means all four conditions, not just the interesting one — and that is
    the fail-closed design rather than an accident:

    * every name in ``schema_names`` booked with ``attempts > 0`` and no
      failures (coverage + failures);
    * ``structured_output_method="json_schema"`` — an **unobserved** method is
      treated as a breach, because "the instrument was never installed" and
      "the instrument read zero" are precisely what the original zero-attempts
      hole conflated;
    * a **full** zero census, for the same reason.
    """
    ollama_smoke = _harness()
    fields: dict[str, Any] = {
        "large": "large-model:test",
        "small": "small-model:test",
        "preflight_ok": True,
        "game_completed": True,
        "stop_reason": "natural-win",
        "duration_seconds": 12.5,
        "stats": {
            name: _stats(attempts=_CLEAN_ATTEMPTS) for name in schema_names
        },
        "structured_output_method": ollama_smoke.EXPECTED_STRUCTURED_OUTPUT_METHOD,
        "substitutions": _census(),
    }
    fields.update(overrides)
    return ollama_smoke.PairReport(**fields)


def _legacy_judge(
    report: Any, threshold: float, schema_names: Sequence[str]
) -> str:
    """The pre-Task-11.1 ``_judge``, reconstructed for contrast.

    Verbatim body as it stood in ``ollama_smoke.py``::

        for name in SCHEMA_NAMES:
            s = report.stats.get(name)
            if s is not None and s.attempts and s.failure_rate > threshold:
                return "UNRELIABLE"
        return "RELIABLE"

    The single edit: the module constant is a **parameter**, so a contrast test
    can feed the old and the new rule the *same* vocabulary and attribute the
    difference to the rule rather than to the tuple. That neutralises the first
    of the four holes (a tuple missing three of seven names) and leaves the
    other three exposed, which is what these tests are about:

    * ``s is not None`` — a schema absent from ``stats`` was skipped entirely.
    * ``and s.attempts`` — a schema present with **zero attempts** short-circuited
      to falsy and therefore *passed*.
    * ``failure_rate > threshold`` — a rate under 0.20 passed, where the gate
      now requires ``failures == 0``.
    """
    for name in schema_names:
        s = report.stats.get(name)
        if s is not None and s.attempts and s.failure_rate > threshold:
            return "UNRELIABLE"
    return "RELIABLE"


# ===========================================================================
# 1. The vocabulary — the derivation, and only the derivation.
# ===========================================================================


def test_the_schema_names_are_exactly_the_gameplay_schema_vocabulary() -> None:
    """Pins **the derivation**: ``SCHEMA_NAMES`` *is* ``GAMEPLAY_SCHEMAS``' names.

    The one test in this file that is allowed to depend on the enum's contents,
    because that dependency is its subject. Order included: the vocabulary
    declares its order to be display order, and ``_schema_rows`` presents it as
    such.
    """
    ollama_smoke = _harness()
    llm = importlib.import_module("graphia.llm")

    assert ollama_smoke.SCHEMA_NAMES == tuple(
        schema.__name__ for schema in llm.GAMEPLAY_SCHEMAS
    )
    assert len(ollama_smoke.SCHEMA_NAMES) == 7, (
        "the gate judges seven structured-output surfaces; a change in that "
        "count is a change in what the gate means and should be deliberate."
    )
    assert len(set(ollama_smoke.SCHEMA_NAMES)) == 7


@pytest.mark.parametrize("name", ["Persona", "Reflection", "Diary"])
def test_the_three_schemas_the_hand_typed_tuple_omitted_are_in_the_vocabulary(
    name: str,
) -> None:
    """Pins **the derivation**, at the three names whose absence was the defect.

    The tuple was literally ``("Roster", "Pointing", "Ballot", "DayAction")``.
    ``Diary`` is the one that failed at 80% and was not judged; ``Persona``
    never appeared in the stats map at all. Named individually rather than
    covered by the length assertion above so a failure says *which* surface
    stopped being measured.
    """
    assert name in _harness().SCHEMA_NAMES


@pytest.mark.parametrize(
    "function_name", ["_schema_rows", "_unreliable_reasons", "_judge"]
)
def test_the_vocabulary_seam_is_never_bound_as_a_signature_default(
    function_name: str,
) -> None:
    """Pins **the derivation**'s late-binding shape, structurally.

    ``schema_names=SCHEMA_NAMES`` as a signature default would evaluate the
    tuple **once, at import**, so a test (or a future caller) that redirected
    the constant would be silently ignored while appearing to have taken
    effect. That exact early-binding shape, in a different module, is what once
    let synthetic records into a committed ledger — so the default is ``None``
    and the tuple is read in the body, and this asserts it rather than trusting
    it.

    Keyword-only as well: a positional third argument would make the two seam
    parameters order-dependent and easy to pass by accident.
    """
    parameter = inspect.signature(
        getattr(_harness(), function_name)
    ).parameters["schema_names"]

    assert parameter.default is None, (
        f"{function_name}'s schema_names default is {parameter.default!r} — a "
        "non-None default binds the vocabulary at import time."
    )
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_derived_vocabulary_is_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins **the derivation**: redirecting ``SCHEMA_NAMES`` redirects the rule.

    The behavioural half of the signature check above, and the reason
    monkeypatching the constant is a legitimate tool for the vocabulary tests
    while the parameter is the tool for the rule tests. A report clean over the
    *real* seven is judged unreliable the moment the vocabulary names something
    it never booked.
    """
    ollama_smoke = _harness()
    report = _clean_report(schema_names=ollama_smoke.SCHEMA_NAMES)
    assert ollama_smoke._unreliable_reasons(report, 0.20) == ()

    monkeypatch.setattr(
        ollama_smoke, "SCHEMA_NAMES", ("Roster", "AnEighthSchema")
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20)
    assert len(reasons) == 1
    assert "1 of 2 schemas went unexercised" in reasons[0]
    assert "AnEighthSchema" in reasons[0]


# ===========================================================================
# 2. ``_schema_rows`` — one row set, so the two surfaces cannot disagree.
# ===========================================================================


def test_schema_rows_reports_every_schema_in_the_vocabulary_even_unbooked_ones() -> (
    None
):
    """Pins **the derivation** — the no-argument call both surfaces make.

    ``_print_pair_report`` and ``_json_payload`` each call ``_schema_rows(r)``
    with no vocabulary argument, so the derived tuple is the thing under test
    here. The measured defect was four printed rows against six JSON keys; the
    property that forecloses it is that the row set is a function of the
    vocabulary alone, **not** of what ``stats`` happens to contain.

    A schema the run never bound gets a zeroed ``SchemaStats`` and still
    appears, because "never exercised" *is* the finding — the original gate's
    silence about ``Persona`` was the whole problem.
    """
    ollama_smoke = _harness()
    report = ollama_smoke.PairReport(
        large="l",
        small="s",
        stats={"Roster": _stats(attempts=3), "Ballot": _stats(attempts=9)},
    )

    rows = ollama_smoke._schema_rows(report)

    assert [name for name, _ in rows] == list(ollama_smoke.SCHEMA_NAMES)
    booked = dict(rows)
    assert booked["Roster"].attempts == 3
    assert booked["Ballot"].attempts == 9
    for name in set(ollama_smoke.SCHEMA_NAMES) - {"Roster", "Ballot"}:
        assert booked[name].attempts == 0
        assert booked[name].failures == 0


def test_schema_rows_places_an_extra_booked_key_after_the_vocabulary() -> None:
    """Pins **the rule** over a fixed two-name vocabulary.

    Blindness in the opposite direction: a key ``stats`` booked but the
    vocabulary does not name would be silently dropped by iterating the tuple
    alone, where the old JSON path would at least have surfaced it. Extras come
    last, sorted, so the ordering is stable and the vocabulary still leads.
    """
    ollama_smoke = _harness()
    report = ollama_smoke.PairReport(
        large="l",
        small="s",
        stats={
            "Roster": _stats(attempts=1),
            "Zebra": _stats(attempts=2),
            "Aardvark": _stats(attempts=3),
        },
    )

    rows = ollama_smoke._schema_rows(report, schema_names=("Roster", "Ballot"))

    assert [name for name, _ in rows] == ["Roster", "Ballot", "Aardvark", "Zebra"]


def test_the_printed_table_and_the_json_record_name_the_same_schemas() -> None:
    """Pins **the derivation**, at both surfaces rather than at the shared helper.

    The property is already a property of ``_schema_rows`` (tested above and
    testable without touching either surface), so this is the belt to that
    braces: it catches someone re-inlining the iteration into one surface and
    not the other, which is the precise shape of the defect — a run that
    printed four rows on screen while its JSON carried six.

    Nothing is invoked and nothing is written: ``_print_pair_report`` prints to
    stdout (captured here) and ``_json_payload`` returns a dict.
    """
    ollama_smoke = _harness()
    report = _clean_report(
        schema_names=("Roster", "Ballot"),  # a deliberately SHORT stats map
    )
    args = Namespace(max_rounds=7, seed=20260611, threshold=0.20)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ollama_smoke._print_pair_report(report, 0.20)
    printed = buffer.getvalue()

    payload = ollama_smoke._json_payload([report], args)
    json.dumps(payload)  # the record has to be serialisable to be a record

    # Every data row's first token is the schema name; the header, the summary
    # lines and any wrapped "last error" continuation carry none of them.
    vocabulary = set(ollama_smoke.SCHEMA_NAMES)
    table_names = [
        tokens[0]
        for line in printed.splitlines()
        for tokens in [line.split()]
        if tokens and tokens[0] in vocabulary
    ]
    json_names = list(payload["pairs"][0]["schemas"])
    expected = [name for name, _ in ollama_smoke._schema_rows(report)]

    assert table_names == expected
    assert json_names == expected
    assert payload["schemas_required"] == list(ollama_smoke.SCHEMA_NAMES)
    assert (
        payload["expected_structured_output_method"]
        == ollama_smoke.EXPECTED_STRUCTURED_OUTPUT_METHOD
    )


@pytest.mark.parametrize(
    "override,expected_fragments",
    [
        (
            {},
            (
                "  structured-output method: json_schema",
                "  substitutions (substituted/attempted): roster=0/0",
                "diary=0/0",
            ),
        ),
        (
            {"structured_output_method": None, "substitutions": {}},
            (
                "  structured-output method: not observed",
                "(the gate requires json_schema)",
                "  substitutions: not observed (no census was installed)",
            ),
        ),
    ],
    ids=["clean", "neither-observed"],
)
def test_the_printed_report_carries_the_method_line_and_the_census_line(
    override: dict[str, Any], expected_fragments: tuple[str, ...]
) -> None:
    """Pins **the derivation** at the printed surface — the two newest gate lines.

    The gate grew two conditions that were previously invisible on screen, and
    the live verification step is written as "confirm the two new output lines".
    Their **shape** is pinned here, offline, so the live run has only to confirm
    the *values*: neither line can go missing while the condition behind it
    still gates the exit code, and a wording drift cannot quietly turn either
    into something a reader skims past.

    The second row is the honest counterpart: when neither was observed, each
    line has to say so in words — "not observed", plus, for the method, what
    the gate required instead — rather than printing a bare ``None`` or nothing
    at all. That is the same distinction the zero-attempts hole erased, carried
    through to the surface a human reads.

    Exactly-one-line assertions on each prefix, because two census lines (or
    two method lines) would mean the clean and the not-observed branches both
    fired, and a reader would have no way to know which one the verdict used.
    """
    ollama_smoke = _harness()
    report = _clean_report(schema_names=ollama_smoke.SCHEMA_NAMES, **override)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ollama_smoke._print_pair_report(report, 0.20)
    printed = buffer.getvalue()
    lines = printed.splitlines()

    for fragment in expected_fragments:
        assert fragment in printed, (
            f"{fragment!r} is not in the printed report:\n{printed}"
        )
    assert (
        len([ln for ln in lines if ln.startswith("  structured-output method:")])
        == 1
    )
    assert len([ln for ln in lines if ln.startswith("  substitutions")]) == 1


# ===========================================================================
# 3. The coverage condition — the two central holes, each against the old code.
# ===========================================================================


def test_a_schema_absent_from_the_stats_map_is_named_as_unexercised() -> None:
    """Pins **the rule** over a fixed two-name vocabulary. THE regression test.

    Hole (b): the old loop's ``s is not None`` skipped any schema the run never
    booked, so a surface that went entirely unexercised contributed nothing to
    the verdict — it simply was not judged. On the live run that motivated this
    slice, ``Persona`` was exactly this: absent from the stats map, and absent
    from the printed table.

    The vocabulary is passed explicitly so this case is about the rule and not
    about the enum: ``("Roster", "Diary")``, ``Roster`` booked clean, ``Diary``
    never booked at all.

    **Old versus new**, on the identical report and the identical vocabulary —
    the contrast is the evidence that this test would not have passed against
    the code it exists to pin.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(schema_names=names)
    del report.stats["Diary"]

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert len(reasons) == 1
    assert "1 of 2 schemas went unexercised" in reasons[0]
    assert "Diary" in reasons[0]
    assert "no structured-output attempt was made" in reasons[0]
    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"

    assert _legacy_judge(report, 0.20, names) == "RELIABLE", (
        "the reconstructed pre-fix judge is supposed to call this RELIABLE — "
        "if it does not, the contrast below is not evidence of anything."
    )


def test_a_schema_booked_with_zero_attempts_is_named_as_unexercised() -> None:
    """Pins **the rule** over a fixed two-name vocabulary. The second central hole.

    Hole (c), and the subtler of the two: ``and s.attempts`` short-circuits on
    a falsy ``0``, so a schema **present** in the stats map with zero attempts
    *passed*. Present-with-zero and absent are different states of the
    instrument and the old rule waved both through; the new rule treats
    ``attempts == 0`` as the finding regardless of which state produced it.

    **Old versus new** on the identical report, as above.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(schema_names=names)
    report.stats["Diary"] = _stats(attempts=0)  # present, and read zero

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert len(reasons) == 1
    assert "1 of 2 schemas went unexercised" in reasons[0]
    assert "Diary" in reasons[0]
    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"

    assert _legacy_judge(report, 0.20, names) == "RELIABLE", (
        "a zero-attempt schema is the falsy short-circuit the old rule fell "
        "through; if the reconstruction rejects it, it is not the old rule."
    )


def test_the_reconstructed_legacy_judge_is_not_a_stub() -> None:
    """The control on every old-versus-new contrast in this file.

    Neither derivation nor rule — it tests the *test helper*. A reconstruction
    that returned ``"RELIABLE"`` unconditionally would make all three contrasts
    above look like proof while proving nothing, which is the same class of
    vacuity those contrasts exist to rule out.

    So: fed the case it *was* built to catch — a booked schema failing at 80%,
    well past the 0.20 bound, inside its own vocabulary — the reconstruction
    returns ``UNRELIABLE``. The old gate was not blind to everything; it was
    blind to absence, to zero, and to small nonzero rates.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(schema_names=names)
    report.stats["Diary"] = _stats(attempts=20, failures=16)

    assert _legacy_judge(report, 0.20, names) == "UNRELIABLE"
    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"


def test_the_unexercised_count_denominates_against_the_vocabulary_not_the_rows() -> (
    None
):
    """Pins **the rule** over a fixed vocabulary, with an extra key present.

    Coverage is judged over the vocabulary; the rows may carry extras booked
    outside it. Denominating "1 of 3" against a set the gate never required
    would report a coverage fraction nobody asked for, and would let an
    irrelevant extra key dilute a real gap.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(schema_names=names)
    del report.stats["Diary"]
    report.stats["Mystery"] = _stats(attempts=5)

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert len(reasons) == 1
    assert "1 of 2 schemas went unexercised" in reasons[0]
    assert "Mystery" not in reasons[0]


def test_require_all_schemas_false_isolates_the_coverage_condition() -> None:
    """Pins **the rule** over a fixed vocabulary — the flag's off-path parity.

    ``require_all_schemas=False`` turns the coverage condition off and must
    leave the other three exactly as they were: it isolates, it does not
    weaken. Asserted in both directions on one report, so a flag that
    accidentally suppressed the failure or census reasons too would be caught.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(schema_names=names)
    del report.stats["Diary"]

    assert (
        ollama_smoke._unreliable_reasons(
            report, 0.20, require_all_schemas=False, schema_names=names
        )
        == ()
    )
    assert (
        ollama_smoke._judge(
            report, 0.20, require_all_schemas=False, schema_names=names
        )
        == "RELIABLE"
    )

    report.structured_output_method = "function_calling"
    reasons = ollama_smoke._unreliable_reasons(
        report, 0.20, require_all_schemas=False, schema_names=names
    )
    assert len(reasons) == 1
    assert "structured-output method" in reasons[0]


# ===========================================================================
# 4. The failures condition — zero, not a rate under the advisory bound.
# ===========================================================================


@pytest.mark.parametrize(
    "failures,attempts,legacy_verdict",
    [
        (1, 1000, "RELIABLE"),  # 0.1% — the old rule waved this through
        (1, 100, "RELIABLE"),  # 1%
        (1, 20, "RELIABLE"),  # 5%
        (4, 20, "RELIABLE"),  # exactly 20% — not *greater than* the bound
        (16, 20, "UNRELIABLE"),  # 80% — the rate that disqualified a real arm
    ],
    ids=["0.1pct", "1pct", "5pct", "at-the-bound", "80pct"],
)
def test_any_nonzero_failure_count_is_unreliable_however_low_the_rate(
    failures: int, attempts: int, legacy_verdict: str
) -> None:
    """Pins **the rule** over a fixed one-name vocabulary.

    The gate's bound is ``failures == 0``, which is *strictly stricter* than
    ``failure_rate > 0.20``. A test written against the threshold as the bound
    would pass for the wrong reason — satisfied incidentally by a rule that no
    longer consults it for the verdict at all — so this sweeps failure counts
    whose rates sit far **under** the advisory bound and asserts they are still
    unreliable.

    The ``legacy_verdict`` column is hole (d) made visible: four of these five
    reports were ``RELIABLE`` under the old rule, and the fifth is the control
    showing the old rule did catch something. Note the exactly-at-the-bound row
    — ``>`` is not ``>=``, so even a rate *equal* to the threshold passed.
    """
    ollama_smoke = _harness()
    names = ("Roster",)
    report = _clean_report(schema_names=names)
    report.stats["Roster"] = _stats(attempts=attempts, failures=failures)

    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"
    assert _legacy_judge(report, 0.20, names) == legacy_verdict


@pytest.mark.parametrize(
    "failures,attempts,threshold,frame",
    [
        (1, 20, 0.20, "under the 20% advisory bound, but the gate requires zero"),
        (4, 20, 0.20, "under the 20% advisory bound, but the gate requires zero"),
        (16, 20, 0.20, "past the 20% advisory bound"),
        (1, 20, 0.01, "past the 1% advisory bound"),
    ],
    ids=["5pct-under", "at-the-bound-under", "80pct-past", "reframed-by-threshold"],
)
def test_the_failure_reason_frames_the_rate_against_the_advisory_bound(
    failures: int, attempts: int, threshold: float, frame: str
) -> None:
    """Pins **the rule** over a fixed one-name vocabulary — the reason *text*.

    The honest other half of "any nonzero failure is unreliable": ``threshold``
    still does a job, and it is a framing job. A 5% failure and an 80% failure
    are the same verdict and must not read the same, because the developer
    reading the line decides what to do next from it — "under the bound but the
    gate requires zero" is a flake to chase, "past the bound" is a broken arm.

    The last row is the proof that ``threshold`` selects the frame and only the
    frame: the same 5% report reads "past the 1% advisory bound" under a
    tightened threshold, and is unreliable either way.
    """
    ollama_smoke = _harness()
    names = ("Roster",)
    report = _clean_report(schema_names=names)
    report.stats["Roster"] = _stats(attempts=attempts, failures=failures)

    reasons = ollama_smoke._unreliable_reasons(
        report, threshold, schema_names=names
    )

    assert len(reasons) == 1
    assert frame in reasons[0]
    assert f"booked {failures} failure(s) in {attempts} attempt(s)" in reasons[0]
    assert f"{round(100 * failures / attempts)}%" in reasons[0]
    assert "Roster" in reasons[0]


def test_an_extra_booked_schema_outside_the_vocabulary_is_still_checked() -> None:
    """Pins **the rule** over a fixed vocabulary that does not name the failure.

    Extras are excluded from the coverage *denominator* but not from the
    failure check — they are displayed, so they are judged. A key present in
    ``stats``, outside the required vocabulary, and failing, must not be
    reported on screen while passing the verdict.
    """
    ollama_smoke = _harness()
    names = ("Roster",)
    report = _clean_report(schema_names=names)
    report.stats["Mystery"] = _stats(attempts=10, failures=3)

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert len(reasons) == 1
    assert "Mystery booked 3 failure(s) in 10 attempt(s)" in reasons[0]


# ===========================================================================
# 5. Preflight, the incomplete game, the method, the census, and a clean run.
# ===========================================================================


@pytest.mark.parametrize(
    "message,expected_tail",
    [
        ("ollama serve is not reachable", "ollama serve is not reachable"),
        (None, "no message recorded"),
    ],
    ids=["with-message", "without-message"],
)
def test_a_failed_preflight_collapses_to_a_single_reason(
    message: str | None, expected_tail: str
) -> None:
    """Pins **neither** — the rule returns before the vocabulary is consulted.

    Deliberately stated: a failed preflight means nothing downstream was
    measured, so all four conditions would fire for one cause and the reader
    would have to work out that the other three are consequences. One reason,
    and it names the cause. The ``None`` row matters because a breach with no
    message still has to print something a reader can act on.

    Called with no ``schema_names`` and with a stats map that would breach
    coverage, method and census on every count — proof the short-circuit really
    is a short-circuit rather than a coincidence of a clean report.
    """
    ollama_smoke = _harness()
    report = ollama_smoke.PairReport(
        large="l", small="s", preflight_ok=False, preflight_message=message
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20)

    assert len(reasons) == 1
    assert reasons[0].startswith("preflight failed: ")
    assert reasons[0].endswith(expected_tail)
    assert ollama_smoke._judge(report, 0.20) == "UNRELIABLE"


@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("max-rounds", "the scripted game did not complete (stopped at: max-rounds)"),
        ("", "the scripted game did not complete"),
    ],
    ids=["with-stop-reason", "without-stop-reason"],
)
def test_an_incomplete_game_is_unreliable_and_names_where_it_stopped(
    stop_reason: str, expected: str
) -> None:
    """Pins **the rule** over a fixed vocabulary, with everything else clean.

    A run that stopped early is not a measurement of the surfaces it never
    reached, and ``max-rounds`` is the live case: the scripted driver returns
    as soon as the round cap is hit, which for the old default ended the script
    before the first Day-to-Night hinge where the diary fires.

    Everything else on the report is clean, so the reason count is exactly one
    and the assertion cannot be satisfied by some other condition's text.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(
        schema_names=names, game_completed=False, stop_reason=stop_reason
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert reasons == (expected,)
    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"


def test_an_all_clean_run_is_reliable() -> None:
    """Pins **the derivation** — the full seven, as the production call judges them.

    The positive control for this whole file: without it, every condition test
    above could be satisfied by a rule that returns a reason for literally any
    report. Built over ``SCHEMA_NAMES`` and judged with no ``schema_names``
    argument, i.e. exactly the call ``_smoke_one_pair`` makes.

    And note what "clean" has to include to get here — the fail-closed design
    rather than an accident. An observed ``json_schema`` method **and** a full
    seven-kind zero census are gate conditions in their own right: a hand-built
    report that omits either is not a clean measurement, because
    ``attempts > 0, failures == 0`` does not imply no substitution, and an
    unobserved method means a clean decode is not evidence about
    grammar-constrained decoding.
    """
    ollama_smoke = _harness()
    report = _clean_report(schema_names=ollama_smoke.SCHEMA_NAMES)

    assert ollama_smoke._unreliable_reasons(report, 0.20) == ()
    assert ollama_smoke._judge(report, 0.20) == "RELIABLE"


@pytest.mark.parametrize(
    "method,expected_fragment",
    [
        (None, "nothing (not observed)"),
        ("function_calling", "'function_calling'"),
        ("json_mode", "'json_mode'"),
    ],
    ids=["not-observed", "function_calling", "json_mode"],
)
def test_the_structured_output_method_must_be_observed_as_json_schema(
    method: str | None, expected_fragment: str
) -> None:
    """Pins **the rule**; the vocabulary is the derived one and is not consulted.

    ADR 013's whole claim is about *grammar-constrained* decoding, so a clean
    decode under tool use would be a true number about the wrong mechanism.
    ``None`` — not observed — is a breach for the same reason a zero-attempt
    schema is: an instrument that was never installed is not a reading.
    """
    ollama_smoke = _harness()
    report = _clean_report(
        schema_names=ollama_smoke.SCHEMA_NAMES, structured_output_method=method
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20)

    assert len(reasons) == 1
    assert "the provider seam resolved structured-output method" in reasons[0]
    assert expected_fragment in reasons[0]
    assert repr(ollama_smoke.EXPECTED_STRUCTURED_OUTPUT_METHOD) in reasons[0]
    assert ollama_smoke._judge(report, 0.20) == "UNRELIABLE"


@pytest.mark.parametrize(
    "drop,expected_count,expected_named",
    [
        ((), 7, ()),  # control: the canonical clean census is complete
        (("diary",), 6, ("diary",)),
        (("diary", "persona"), 5, ("diary", "persona")),
    ],
    ids=["complete", "missing-diary", "missing-two"],
)
def test_a_short_substitution_census_is_unreliable(
    drop: tuple[str, ...], expected_count: int, expected_named: tuple[str, ...]
) -> None:
    """Pins **the rule**; the vocabulary is derived and is not consulted.

    The census reports all seven kinds *including* zeros — the opposite of the
    ledger's "absent, never a misleading zero" rule, and deliberately so,
    because this number is asserted against rather than published. A missing
    key means the sink was never installed, and the gate has to tell that apart
    from a genuine zero: the same distinction the zero-attempts hole erased.

    The first row is the control — ``_SubstitutionCensus().as_report()`` really
    does carry all seven at zero, so the two failing rows fail for the reason
    stated and not because the helper is short to begin with.
    """
    ollama_smoke = _harness()
    report = _clean_report(
        schema_names=ollama_smoke.SCHEMA_NAMES, substitutions=_census(drop=drop)
    )

    assert len(report.substitutions) == expected_count
    reasons = ollama_smoke._unreliable_reasons(report, 0.20)

    if not drop:
        assert reasons == ()
        return

    assert len(reasons) == 1
    assert f"only {expected_count} of 7 answer kinds" in reasons[0]
    for kind in expected_named:
        assert kind in reasons[0]
    assert "an instrument that was not installed" in reasons[0]


def test_an_empty_substitution_census_names_every_missing_kind() -> None:
    """Pins **the rule**; the vocabulary is derived and is not consulted.

    The default ``PairReport.substitutions`` is ``{}`` — "the census was never
    installed" — and that must read as a breach naming all seven kinds rather
    than as a silent pass. This is the census-shaped twin of the zero-attempts
    hole, pinned before it can be introduced.
    """
    ollama_smoke = _harness()
    report = _clean_report(
        schema_names=ollama_smoke.SCHEMA_NAMES, substitutions={}
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20)

    assert len(reasons) == 1
    assert "only 0 of 7 answer kinds" in reasons[0]
    for kind in ollama_smoke.SubstitutionKind:
        assert kind.value in reasons[0]


def test_a_nonzero_substitution_census_is_unreliable() -> None:
    """Pins **the rule**; the vocabulary is derived and is not consulted.

    ``attempts > 0, failures == 0`` does **not** imply no substitution: the
    raw-invoke counter sees the transport, while the seven helpers' own
    substitution branches see the game deciding no usable answer arrived. A
    validator rejecting a well-formed, cleanly-decoded answer books a
    substitution and no parse failure whatsoever — so a report that is
    spotless on the first three conditions can still have fallen back, and the
    reason has to say which kind and how often.
    """
    ollama_smoke = _harness()
    report = _clean_report(
        schema_names=ollama_smoke.SCHEMA_NAMES,
        substitutions=_census(
            substituted={"diary": 2}, attempted={"diary": 3}
        ),
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20)

    assert len(reasons) == 1
    assert "the substitution census is nonzero" in reasons[0]
    assert "diary 2/3" in reasons[0]
    assert ollama_smoke._judge(report, 0.20) == "UNRELIABLE"


def test_every_breached_condition_gets_its_own_reason() -> None:
    """Pins **the rule** over a fixed two-name vocabulary.

    The reasons tuple is a list of *all* breaches, not the first one found: a
    reader who fixes the method and re-runs should not discover a second and a
    third breach one run at a time. Six independent breaches, six reasons — the
    incomplete game, the unexercised schema, the failing schema, the wrong
    method, the short census and its nonzero count.

    Task 11.3's deliberate break is the live instance of this: pointing the
    wrapper at ``function_calling`` fails the method condition off the provider
    seam *and* fails again on the parse failures that follow, and one red reason
    is not the whole story.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = ollama_smoke.PairReport(
        large="l",
        small="s",
        preflight_ok=True,
        game_completed=False,
        stop_reason="max-rounds",
        stats={"Roster": _stats(attempts=10, failures=2)},
        structured_output_method="function_calling",
        substitutions={"roster": {"attempted": 2, "substituted": 1}},
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert len(reasons) == 6, "\n".join(reasons)
    joined = "\n".join(reasons)
    for fragment in (
        "the scripted game did not complete (stopped at: max-rounds)",
        "1 of 2 schemas went unexercised",
        "Roster booked 2 failure(s) in 10 attempt(s)",
        "the provider seam resolved structured-output method 'function_calling'",
        "only 1 of 7 answer kinds",
        "the substitution census is nonzero",
    ):
        assert fragment in joined


# ===========================================================================
# 6. ``_judge`` — one word, and nothing of its own.
# ===========================================================================


@pytest.mark.parametrize(
    "override,expected",
    [
        ({}, "RELIABLE"),
        ({"game_completed": False}, "UNRELIABLE"),
        ({"structured_output_method": None}, "UNRELIABLE"),
        ({"substitutions": {}}, "UNRELIABLE"),
    ],
    ids=["clean", "incomplete-game", "no-method", "no-census"],
)
def test_judge_is_one_word_over_the_reasons(
    override: dict[str, Any], expected: str
) -> None:
    """Pins **the derivation** — and mostly pins that ``_judge`` adds no rule.

    ``_judge`` is the least interesting of the four pure functions: it exists
    because a bare word is what ``PairReport.verdict`` holds, what the summary
    block prints and what the exit code is derived from. Its only obligation is
    to agree with ``_unreliable_reasons`` — so that is what is asserted, rather
    than re-deriving the four conditions a second time here.
    """
    ollama_smoke = _harness()
    report = _clean_report(schema_names=ollama_smoke.SCHEMA_NAMES, **override)

    verdict = ollama_smoke._judge(report, 0.20)

    assert verdict == expected
    reasons = ollama_smoke._unreliable_reasons(report, 0.20)
    assert verdict == ("UNRELIABLE" if reasons else "RELIABLE")


def test_judge_forwards_both_seam_arguments() -> None:
    """Pins **the rule**, and the pass-through of both keyword seams.

    ``require_all_schemas`` and ``schema_names`` are only useful if ``_judge``
    hands them on; a delegation that dropped either would silently judge
    against module scope while the caller believed otherwise — the same class
    of "appears to have taken effect" bug as an early-bound default.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Diary")
    report = _clean_report(schema_names=names)
    del report.stats["Diary"]

    # schema_names forwarded: judged over ("Roster", "Diary"), Diary missing.
    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"
    # require_all_schemas forwarded: the same report, coverage off.
    assert (
        ollama_smoke._judge(
            report, 0.20, require_all_schemas=False, schema_names=names
        )
        == "RELIABLE"
    )


# ===========================================================================
# 7. ``_parse_pairs`` — the CLI's only rejection.
# ===========================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        ("qwen2.5:7b,qwen2.5:3b", ("qwen2.5:7b", "qwen2.5:3b")),
        ("  llama3.1:8b , qwen2.5:3b  ", ("llama3.1:8b", "qwen2.5:3b")),
        ("a,b", ("a", "b")),
    ],
    ids=["plain", "padded", "minimal"],
)
def test_a_well_formed_pair_parses_and_strips_whitespace(
    value: str, expected: tuple[str, str]
) -> None:
    """A ``LARGE,SMALL`` string becomes one tuple, surrounding space removed.

    Neither derivation nor rule — ``_parse_pairs`` does not touch the schema
    vocabulary. Model ids carry colons and dots, so the split is on the comma
    only; the padded row is the realistic one, since a shell-quoted flag
    routinely arrives with spaces around the comma.
    """
    assert _harness()._parse_pairs([value]) == [expected]


def test_repeated_models_flags_accumulate_pairs_in_order() -> None:
    """``--models`` is ``action="append"``, so several pairs arrive as a list.

    Order preserved, because the printed report and the JSON record present the
    pairs in the order the operator asked for them.
    """
    assert _harness()._parse_pairs(["a,b", "c,d", "e,f"]) == [
        ("a", "b"),
        ("c", "d"),
        ("e", "f"),
    ]


@pytest.mark.parametrize("value", [None, []], ids=["absent", "empty"])
def test_no_models_flag_defers_to_the_configured_pair(value: Any) -> None:
    """``None`` means "the operator did not choose" — not "zero pairs".

    ``main`` distinguishes the two: a ``None`` return sends it to
    ``load_config()`` for the configured ``GRAPHIA_OLLAMA_*`` pair, whereas an
    empty list would run zero games and, since ``all(...)`` over nothing is
    true, **exit zero having measured nothing** — a fifth way to pass without
    evidence. So the sentinel has to be ``None`` for both input shapes.
    """
    assert _harness()._parse_pairs(value) is None


@pytest.mark.parametrize(
    "value",
    ["qwen2.5:7b", "a,b,c", "a,", ",b", ",", "  ,  "],
    ids=["no-comma", "three-parts", "empty-small", "empty-large", "bare-comma", "spaces-only"],
)
def test_malformed_models_is_rejected(value: str) -> None:
    """A pair that is not exactly two non-empty names exits, with the shape quoted.

    ``SystemExit`` rather than a swallowed default, because silently reading
    ``"qwen2.5:7b"`` as some half-specified pair would run a gate against
    models the operator did not name and stamp the result as if they had.

    Driven through ``_parse_pairs`` rather than ``main``: ``main`` writes to
    ``os.environ`` in eight places directly — forcing the provider, clearing the
    remote and store ids, forcing two ablation flags — before it ever reaches
    the parse, and those writes are not ``monkeypatch``-tracked, so calling it
    would leak forced settings into every test that ran afterwards.
    """
    with pytest.raises(SystemExit) as excinfo:
        _harness()._parse_pairs([value])

    message = str(excinfo.value)
    assert "--models expects 'LARGE,SMALL'" in message
    assert repr(value) in message


# ===========================================================================
# 8. The import is inert — asserted, not assumed.
# ===========================================================================


class _RecordingEnviron(dict):
    """A stand-in for ``os.environ`` that records every access.

    ``os.getenv`` resolves ``environ`` as a module global, so replacing
    ``os.environ`` also covers ``os.getenv``. Reads and writes are recorded
    separately: an import that *wrote* env would be a worse defect than one
    that read it, and the message should say which happened.
    """

    def __init__(self, real: Any) -> None:
        super().__init__(real)
        self.reads: list[str] = []
        self.writes: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self.reads.append(key)
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        self.reads.append(str(key))
        return super().__contains__(key)

    def __setitem__(self, key: str, value: str) -> None:
        self.writes.append(key)
        super().__setitem__(key, value)

    def setdefault(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self.writes.append(key)
        return super().setdefault(key, default)

    def pop(self, key: str, *args: Any) -> Any:  # type: ignore[override]
        self.writes.append(key)
        return super().pop(key, *args)


def _boom(name: str) -> Any:
    """A callable that fails loudly and names itself.

    Installed over the ``graphia.llm`` surfaces an inert import must not touch,
    so a regression lands as a traceback pointing at the offending line rather
    than as an opaque assertion at the end of the test.
    """

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            f"importing {_HARNESS_MODULE} called {name} — the module-scope "
            "import of graphia.llm is supposed to import NAMES and nothing "
            "more: no env read, no provider resolution, no client construction."
        )

    return _explode


def test_importing_the_harness_reads_no_env_and_touches_no_provider_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module body is inert — the fact behind "invoke nothing".

    ``ollama_smoke`` imports ``graphia.llm`` at **module scope**, a deliberate
    exception to its own local-import discipline: ``SCHEMA_NAMES`` is derived
    from ``GAMEPLAY_SCHEMAS`` and ``_CountingOllamaProvider`` subclasses
    ``OllamaProvider``, and neither can wait for ``main``. The discipline
    exists to keep provider *selection* out of import time, and importing names
    does not select — but that is now a claim this file asserts rather than
    repeats.

    Method: re-execute the module body with ``os.environ`` swapped for a
    recorder and every provider/client surface in ``graphia.llm`` replaced by a
    loud failure. The fresh module is **not** installed in ``sys.modules``, so
    nothing later in the session sees a second copy.

    Scope, stated honestly: this covers *this module's own body*. Its
    transitive imports are already resolved and cached by the time the fixture
    runs, and the whole-graph version of the claim is
    :func:`test_a_bare_interpreter_import_of_the_harness_resolves_no_provider`
    below.
    """
    ollama_smoke = _harness()
    llm = importlib.import_module("graphia.llm")

    recorder = _RecordingEnviron(os.environ)
    monkeypatch.setattr(os, "environ", recorder)
    for name in (
        "_resolve_provider",
        "load_config",
        "get_large",
        "get_small",
        "get_persona_model",
        "get_embeddings",
        "ChatOllama",
        "ChatBedrockConverse",
        "BedrockEmbeddings",
    ):
        monkeypatch.setattr(llm, name, _boom(f"graphia.llm.{name}"))

    spec = importlib.util.spec_from_file_location(
        "_fresh_ollama_smoke_for_inertness_test", ollama_smoke.__file__
    )
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    # Registered in ``sys.modules`` for the duration of the exec and removed
    # immediately after. Not optional: this file uses
    # ``from __future__ import annotations``, so ``dataclasses`` resolves each
    # field's annotation as a string and looks the defining module up in
    # ``sys.modules[cls.__module__]`` to do it. Under a private name, so
    # nothing else in the session can reach it, and the assertion below checks
    # it is gone.
    sys.modules[spec.name] = fresh
    try:
        spec.loader.exec_module(fresh)
    finally:
        del sys.modules[spec.name]

    assert recorder.reads == [], f"the import read env: {recorder.reads}"
    assert recorder.writes == [], f"the import wrote env: {recorder.writes}"
    assert llm._large is None and llm._small is None, (
        "the import populated a tier cache — that is client construction."
    )
    assert "_fresh_ollama_smoke_for_inertness_test" not in sys.modules

    # Positive control: the body really did run under those sentinels. Without
    # this, a spec/loader that silently produced an empty module would make
    # every assertion above pass for no reason at all.
    assert isinstance(fresh, types.ModuleType)
    assert fresh.SCHEMA_NAMES == ollama_smoke.SCHEMA_NAMES
    assert fresh._CountingOllamaProvider is not ollama_smoke._CountingOllamaProvider
    assert issubclass(fresh._CountingOllamaProvider, llm.OllamaProvider)


def test_the_harness_import_leaves_the_safe_llm_backstop_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``safe_llm``'s two layers survive the harness import, both of them.

    The restatement Task 11.1 asked for: the concern behind "invoke nothing" is
    not only that no request goes out during this file's tests, but that
    importing a module which reaches ``graphia.llm`` cannot disarm the net the
    other 80 test modules run behind — Layer 2 lives on
    ``graphia.llm._active_provider``, exactly the object an import-time
    provider resolution would overwrite.

    Asserted by identity across the import, and then on all five call-site
    bindings of Layer 1. Nothing is invoked: the bindings are called to see
    *what they hand back*, and a ``_LoudFailureLLM`` raises on ``invoke``, not
    on construction.
    """
    llm = importlib.import_module("graphia.llm")
    before = llm._active_provider
    assert type(before).__name__ == "_LoudFailureProvider", (
        "this test reads the conftest backstop; if it is not installed the "
        "assertion below proves nothing."
    )

    ollama_smoke = _harness()
    assert ollama_smoke.SCHEMA_NAMES  # the module is really loaded

    assert llm._active_provider is before
    assert llm._large is None and llm._small is None

    for module_name, attribute in (
        ("graphia.nodes.setup", "get_small"),
        ("graphia.nodes.setup", "get_large"),
        ("graphia.nodes.night", "get_large"),
        ("graphia.nodes.day", "get_large"),
    ):
        factory = getattr(importlib.import_module(module_name), attribute)
        assert type(factory()).__name__ == "_LoudFailureLLM", (
            f"{module_name}.{attribute} is no longer netted by safe_llm."
        )
    setup = importlib.import_module("graphia.nodes.setup")
    assert type(setup.get_persona_model(0.9)).__name__ == "_LoudFailureLLM"


def test_a_bare_interpreter_import_of_the_harness_resolves_no_provider() -> None:
    """The whole-graph version: a cold interpreter, a scrubbed env, nothing resolved.

    The in-process test above can only speak for ``ollama_smoke``'s own body,
    because every module it imports is already cached. This one imports it into
    a **fresh** interpreter — so ``graphia.llm``, ``graphia.tools.instrument``,
    ``graphia.tools.eval_dialogue`` and ``graphia.substitution`` all execute
    their bodies for real — with every ``GRAPHIA_*`` and ``AWS_*`` variable
    removed, and then reads the provider seam.

    Three ``None``s is the claim: no provider was selected and neither tier
    cache was populated, so no vendor client was constructed and no
    credential-bearing env var was needed to get that far. Roughly half a
    second; kept unmarked because it is the only end-to-end statement of the
    property that "importing names does not select".
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GRAPHIA_", "AWS_"))
    }
    script = (
        "import json, sys\n"
        f"assert 'graphia.llm' not in sys.modules\n"
        f"import {_HARNESS_MODULE} as harness\n"
        "import graphia.llm as llm\n"
        "print(json.dumps({\n"
        "    'provider': repr(llm._active_provider),\n"
        "    'large': repr(llm._large),\n"
        "    'small': repr(llm._small),\n"
        "    'names': list(harness.SCHEMA_NAMES),\n"
        "}))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, (
        f"a bare import of {_HARNESS_MODULE} failed:\n{completed.stderr}"
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    observed = json.loads(lines[-1])

    assert observed["provider"] == "None", (
        "importing the harness resolved a provider — import-time provider "
        f"selection is what the module-scope graphia.llm import must not do; "
        f"got {observed['provider']}"
    )
    assert observed["large"] == "None"
    assert observed["small"] == "None"
    assert observed["names"] == list(_harness().SCHEMA_NAMES)


# ===========================================================================
# 8. Ballot reachability — Task 11.4's probe, and the refusal it does NOT relax.
#
# The sixth hole: the gate was CORRECT and the script was INCOMPLETE. A vote is
# initiated only by the human typing ``/vote <name>`` or an AI returning
# ``DayAction(kind="vote")``, and the scripted human's lines are deliberately
# non-committal — so ``Ballot`` had no guaranteed path and the finished gate
# passed one live run in four. The three failures were not the gate crying
# wolf about the transport; they were the gate correctly refusing a run that
# had measured six of seven surfaces.
#
# Nothing here invokes a model. ``_ballot_probe_pair`` is pure, and
# ``_probe_ballot_if_unexercised``'s "already exercised" branch returns before
# it imports ``nodes.day``, so it is reachable with ``None`` standing in for
# every live argument — which is itself part of the claim: the probe must not
# touch the graph when the game already voted.
# ===========================================================================


def _player(
    player_id: str,
    *,
    is_human: bool = False,
    is_alive: bool = True,
    name: str | None = None,
) -> Any:
    """One ``PlayerState``, imported at call time like the harness itself."""
    from graphia.state import PlayerState

    return PlayerState(
        id=player_id,
        name=name or player_id.title(),
        role="law_abiding",
        is_human=is_human,
        is_alive=is_alive,
    )


def _roster(*players: Any) -> dict[str, Any]:
    """A ``players`` map in insertion order — the order the probe reads."""
    return {player.id: player for player in players}


def test_the_ballot_schema_name_is_derived_from_the_schema_class() -> None:
    """Pins **the derivation**: the probe's key is ``Ballot.__name__``.

    Typed, it would be a lookup that silently stops matching the moment the
    schema is renamed — and a probe that never fires puts the gate straight
    back to passing one run in four, with nothing on screen to say why. The
    name must also be IN the vocabulary the verdict iterates, or the probe
    would be filling a row nobody judges.
    """
    ollama_smoke = _harness()
    llm = importlib.import_module("graphia.llm")

    assert ollama_smoke.BALLOT_SCHEMA_NAME == llm.Ballot.__name__
    assert ollama_smoke.BALLOT_SCHEMA_NAME in ollama_smoke.SCHEMA_NAMES


def test_the_probe_pair_prefers_an_alive_ai_voter_and_an_alive_target() -> None:
    """Pins **the rule**: alive-first, and never the human as voter.

    ``_ai_ballot`` is the AI branch of ``collect_votes`` — a human voter has no
    model call to make — so a roster whose first entry is the human must still
    yield an AI voter.
    """
    ollama_smoke = _harness()
    players = _roster(
        _player("p1", is_human=True),
        _player("p2", is_alive=False),
        _player("p3"),
        _player("p4"),
    )

    voter, target = ollama_smoke._ballot_probe_pair(players)

    assert voter.id == "p3", "the first ALIVE AI player should be the voter"
    assert not voter.is_human
    assert target.id == "p1", (
        "the target is the first alive player other than the voter, human or "
        "not — the probe renders a Ballot prompt, it does not stage a legal vote"
    )


def test_the_probe_pair_falls_back_to_a_dead_stand_in_rather_than_declining() -> (
    None
):
    """Pins **the rule**: alive-FIRST, not alive-only.

    At a natural game end the survivors can be one side or one player. The
    probe's job is to exercise the *decode*; declining would leave ``Ballot``
    at zero attempts and fail the gate for a reason that says nothing about
    the transport under test, which is the exact failure mode being fixed.
    """
    ollama_smoke = _harness()

    dead_ai = _roster(_player("p1", is_alive=False), _player("p2", is_alive=False))
    voter, target = ollama_smoke._ballot_probe_pair(dead_ai)
    assert (voter.id, target.id) == ("p1", "p2")

    lone_survivor = _roster(_player("p1"), _player("p2", is_alive=False))
    voter, target = ollama_smoke._ballot_probe_pair(lone_survivor)
    assert (voter.id, target.id) == ("p1", "p2")


@pytest.mark.parametrize(
    "players,why",
    [
        ({}, "an empty roster"),
        ("human-only", "a roster with no AI player at all"),
        ("single-ai", "an AI player with nobody to vote on"),
    ],
    ids=["empty", "human-only", "single-ai"],
)
def test_the_probe_pair_is_none_when_no_usable_pair_exists(
    players: Any, why: str
) -> None:
    """Pins **the rule**: no pair -> ``None``, so the caller can say so.

    ``None`` is not a failure path that needs its own gate condition: it leaves
    ``Ballot`` at zero attempts, and ``require_all_schemas`` already refuses
    that. The probe supplies a missing attempt; it never excuses one.
    """
    ollama_smoke = _harness()
    if players == "human-only":
        players = _roster(_player("p1", is_human=True), _player("p2", is_human=True))
    elif players == "single-ai":
        players = _roster(_player("p1"))

    assert ollama_smoke._ballot_probe_pair(players) is None, why


def test_the_probe_does_nothing_when_the_game_already_reached_a_vote() -> None:
    """The probe is a BACKSTOP: in-game evidence is preferred and left alone.

    ``None`` for the graph, run config and settings is the assertion, not a
    convenience: on this branch the probe must not read the graph state, must
    not import ``nodes.day`` and must not invoke anything. Any of those would
    raise on ``None`` here.
    """
    ollama_smoke = _harness()
    stats = {ollama_smoke.BALLOT_SCHEMA_NAME: _stats(attempts=7)}

    note = ollama_smoke._probe_ballot_if_unexercised(None, None, None, stats)

    assert note.startswith("not needed")
    assert "7 in-game Ballot attempt(s)" in note
    assert stats[ollama_smoke.BALLOT_SCHEMA_NAME].attempts == 7, (
        "the backstop must not touch the counts it read"
    )


@pytest.mark.parametrize(
    "stats_map",
    [{}, {"Ballot": None}],
    ids=["absent-from-the-map", "booked-with-zero-attempts"],
)
def test_a_missing_ballot_row_is_what_sends_the_probe_looking(
    stats_map: dict[str, Any]
) -> None:
    """Both readings of "unexercised" reach the probe, not just one.

    A schema ABSENT from the stats map and one PRESENT with zero attempts are
    the same finding — that pair of shapes is holes (b) and (c) of the original
    defect — so the probe must not be reachable from only one of them. Proven
    by the exception: with ``None`` for the graph, a probe that decided to look
    fails on ``graph.get_state``, and one that returned early would not.
    """
    ollama_smoke = _harness()
    stats = {
        name: (_stats() if value is None else value)
        for name, value in stats_map.items()
    }

    with pytest.raises(AttributeError):
        ollama_smoke._probe_ballot_if_unexercised(None, None, None, stats)


def test_a_probe_that_could_not_run_still_leaves_the_gate_refusing() -> None:
    """The forbidden fix, asserted as NOT taken: the note is not an excuse.

    A report carrying a "could not run" probe note and ``Ballot`` at zero
    attempts must still be UNRELIABLE, and the reason must still NAME
    ``Ballot``. If the probe note had been wired into the verdict as a
    mitigating circumstance, this is the test that would go red.
    """
    ollama_smoke = _harness()
    names = ("Roster", "Ballot")
    report = _clean_report(
        schema_names=names,
        stats={"Roster": _stats(attempts=_CLEAN_ATTEMPTS), "Ballot": _stats()},
        ballot_probe="could not run — the final state carries no AI player",
    )

    reasons = ollama_smoke._unreliable_reasons(report, 0.20, schema_names=names)

    assert ollama_smoke._judge(report, 0.20, schema_names=names) == "UNRELIABLE"
    assert any("1 of 2 schemas went unexercised" in r for r in reasons)
    assert any("Ballot" in r for r in reasons)


def test_the_probe_note_is_reported_on_both_surfaces() -> None:
    """The note reaches the screen AND the record — a claim, not a nicety.

    "``Ballot`` was decoded" and "``Ballot`` was decoded IN A GAME" are
    different claims, and a run where the game initiated no vote supports only
    the weaker one. A reader of either surface has to be able to tell which.

    The printed label is lower-case ``ballot probe:`` on purpose: the table
    parity test reads a data row as "first token is a schema name", so a line
    opening with ``Ballot`` would be miscounted as an eighth row.
    """
    ollama_smoke = _harness()
    note = "ran — the game initiated no vote, so ONE direct Ballot decode"
    report = _clean_report(
        schema_names=ollama_smoke.SCHEMA_NAMES, ballot_probe=note
    )
    args = Namespace(max_rounds=7, seed=20260611, threshold=0.20)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ollama_smoke._print_pair_report(report, 0.20)
    printed = buffer.getvalue()

    assert f"  ballot probe: {note}" in printed
    assert ollama_smoke._json_payload([report], args)["pairs"][0][
        "ballot_probe"
    ] == note
    # The verdict is untouched by the note either way.
    assert report.verdict_reasons == ()
    assert ollama_smoke._judge(report, 0.20) == "RELIABLE"
    probe_lines = [
        line for line in printed.splitlines() if "ballot probe:" in line
    ]
    assert len(probe_lines) == 1, "exactly one probe line, whatever it says"
    assert probe_lines[0].split()[0] == "ballot", (
        "the label must not open with a schema name, or the table-parity test "
        "would read this line as an eighth data row"
    )


def test_an_unrecorded_probe_prints_as_unrecorded_rather_than_silently() -> None:
    """An absent note is visible, for the reason conditions (3) and (4) fail closed.

    "The probe was never reached" and "the probe found nothing to do" are the
    same conflation the zero-attempts hole was, one level up, so the line is
    printed unconditionally rather than under an ``if``.
    """
    ollama_smoke = _harness()
    report = _clean_report(schema_names=("Roster",))
    assert report.ballot_probe == ""

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ollama_smoke._print_pair_report(report, 0.20)

    assert "  ballot probe: not recorded" in buffer.getvalue()


def test_the_scripted_game_outcome_carries_the_probe_as_a_fourth_term() -> None:
    """The drive's result is a value object, and the probe note is part of it.

    ``_run_scripted_game`` used to return a bare 3-tuple; Task 11.4 needed a
    fourth term, and four strings and a bool unpacked positionally at the call
    site is where a silent reordering would live. ``ballot_probe`` defaults to
    empty so the error paths that never reach a probe stay constructible.
    """
    ollama_smoke = _harness()

    outcome = ollama_smoke.ScriptedGameOutcome(True, None, "natural game end")
    assert (outcome.completed, outcome.error, outcome.stop_reason) == (
        True,
        None,
        "natural game end",
    )
    assert outcome.ballot_probe == ""
    assert [
        field.name for field in dataclasses.fields(ollama_smoke.ScriptedGameOutcome)
    ] == ["completed", "error", "stop_reason", "ballot_probe"]


def test_the_harness_does_not_reach_into_the_shared_scripted_human() -> None:
    """The fix stays LOCAL: ``eval_dialogue.HUMAN_LINES`` is untouched.

    Three other harnesses read that pool — ``eval_dialogue`` itself,
    ``blunder_eval`` (which imports it) and ``repetition_experiment`` (its own
    copy) — and ``blunder_eval`` measures BEHAVIOUR. Injecting a forced
    ``/vote`` into the shared line pool would change what the ledger's records
    mean, which is why Task 11.4's fix is a probe in this module rather than an
    edit one level down. Asserted rather than trusted: the lines stay
    non-committal, and this file is where that goes red if someone "fixes"
    reachability by editing the pool.
    """
    lines = importlib.import_module("graphia.tools.eval_dialogue").HUMAN_LINES

    assert not any("/vote" in line for line in lines), (
        "a scripted /vote in the SHARED human line pool would change what "
        "blunder_eval measures; Task 11.4's probe is local to ollama_smoke"
    )
    source = (
        _REPO_ROOT / "src" / "graphia" / "tools" / "ollama_smoke.py"
    ).read_text()
    assert 'Command(resume="/vote' not in source, (
        "the harness drives no /vote turn: it cannot fix the run shape where "
        "the human is killed on Night 1 and there are no human turns at all"
    )
