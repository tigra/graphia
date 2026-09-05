"""The interim prose recovery is GONE, not merely unused (spec 041 Task 4.3).

Slice 4 withdrew ``_ai_diary``'s application-layer prose recovery — the
``include_raw=True`` diary call, ``_DIARY_LABEL_MAX``, ``_content_text``,
``_recovered_diary_text`` and ``_diary_entry_from`` — because reliable
grammar-constrained decoding (Slices 2 and 3) replaced the compensation rather
than layering over it. With two mechanisms able to produce a readable entry no
reader can tell which one did, so the fallback rate stops being evidence of
anything (ADR 013 §3).

This module is what turns the functional spec's "**gone** rather than left
passing against nothing" into a checked criterion. Nothing here exercises
gameplay: it asserts absence, at two levels.

1. **Module attributes.** ``not hasattr(day, name)`` per withdrawn symbol,
   plus ``not hasattr(day, "json")`` — precise, because a module-scope ``import
   json`` creates exactly that attribute, so the probe distinguishes "the
   import was dropped" from "the import is still there, unused".
2. **A source sweep** over ``src/`` and ``tests/`` for the withdrawn names and
   for the retired Anthropic-compatible transport ADR-010 stood on
   (``langchain_anthropic`` / ``ChatAnthropic`` / the dummy api key), with
   ``langchain-anthropic`` absent from ``pyproject.toml``. ``context/`` is
   deliberately **excluded**: the ADRs and specs must keep naming what they
   retired, or the record of why loses its subject.

Why there is no behavioural diary assertion here
------------------------------------------------
Task 4.3 originally asked for a recording fake pinning the diary call as
``(Diary, {})``. It would be redundant, and the redundancy is exact rather
than approximate: ``tests/test_provider_seam_through_the_node.py`` drives the
real ``_ai_diary`` through the real production wrapper and asserts its
recorded binding kwargs with ``==``, and because ``_BEDROCK_DEFAULTS`` is
``{}`` the parametrisation
``test_the_call_site_requests_no_method_on_the_cloud_twin[day._ai_diary-*]``
already asserts *literally* ``(Diary, {})`` — twice, once per Bedrock twin —
while the Ollama twin asserts ``(Diary, {"method": "json_schema"})``. The one
way to weaken that was an entry in its ``_CallSite.caller_kwargs`` table, and
Task 4.3 closed it there with
``test_no_call_site_declares_any_caller_kwargs``, beside the table it guards.
Rebuilding that module's provider seam here to say the same thing about one of
its nine rows would have been a duplicated fixture, not extra coverage.

What this module keeps instead is
``test_the_diary_call_site_passes_no_kwargs_of_its_own``: a source-level check
of ``_ai_diary``'s body, in this module's own idiom, independent of both the
``_CallSite`` table and the guard over it. It is the assertion that survives
someone legitimately declaring a kwarg for a *different* call site.

Anti-vacuity rules
------------------
A test that greps for a name and finds nothing passes just as happily when it
is pointed at the wrong directory or uses a pattern that cannot match. So:

- **The sweep asserts it read the trees** — per-tree file-count floors and
  named landmark files (``test_the_sweep_actually_read_both_trees``). Without
  it, a sweep over an empty iterator is green.
- **The patterns are positively controlled** — each is matched against a
  sample of the exact line it exists to forbid, and against the legitimate
  strings it must NOT flag (the provider *name* ``"ollama"``, and identifiers
  that merely end in a withdrawn one). See
  ``test_the_forbidden_patterns_match_what_they_forbid`` and its twin.
- **The ``hasattr`` probes have a positive control** —
  ``test_the_probes_are_pointed_at_the_real_production_module``, because an
  absence probe against the wrong object is green for the wrong reason.
- **This file excludes itself from its own sweep**, necessarily: it names every
  forbidden string. The exclusion is asserted to have resolved to exactly one
  file, this one, rather than trusted.

Strictly offline and I/O-light: reads source files, imports one production
module, calls no model and builds no game state.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import graphia.nodes.day as day

# ---------------------------------------------------------------------------
# The trees under sweep, and this file's own exclusion from them.
# ---------------------------------------------------------------------------

_THIS_FILE = Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_REPO_ROOT = _TESTS_DIR.parent
_SRC_DIR = _REPO_ROOT / "src"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _collect(root: Path) -> tuple[tuple[Path, str], ...]:
    """Every source file under ``root``, paired with its text.

    Not restricted to ``*.py``: a stale name in a future ``.md`` or ``.toml``
    under these trees is the same defect, and today both trees are 100% Python
    so the wider net costs nothing. ``errors="replace"`` so a binary file
    someone drops in cannot turn the sweep into a collection error.
    """
    collected: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        if "__pycache__" in path.parts:
            continue
        if path.resolve() == _THIS_FILE:
            continue
        collected.append((path, path.read_text(encoding="utf-8", errors="replace")))
    return tuple(collected)


_SRC_FILES = _collect(_SRC_DIR)
_TEST_FILES = _collect(_TESTS_DIR)
_SWEPT_FILES = _SRC_FILES + _TEST_FILES

# ---------------------------------------------------------------------------
# The vocabulary of absence.
# ---------------------------------------------------------------------------

# The one contiguous cut Task 4.1 made in ``nodes/day.py``. Named once and used
# by BOTH the attribute probes and the source sweep, so the two levels can
# never drift apart.
_WITHDRAWN_SYMBOLS = (
    "_DIARY_LABEL_MAX",
    "_content_text",
    "_recovered_diary_text",
    "_diary_entry_from",
)

# The transport ADR-010 chose and spec 041 Slice 2 retired: Ollama's
# Anthropic-compatible ``/v1/messages`` shim, where structured output was tool
# use the server could decline. The client import, the class, and the dummy api
# key that surface required (Ollama ignored its value) all went with it.
_RETIRED_TRANSPORT_SYMBOLS = (
    "langchain_anthropic",
    "ChatAnthropic",
    "_OLLAMA_DUMMY_API_KEY",
)

# Word-bounded rather than plain substrings, so ``_content_text`` cannot be
# reported against a legitimate ``message_content_text`` — and the dummy key
# is matched at its USE site (``api_key="ollama"``) rather than by its value,
# because the bare string ``"ollama"`` is the provider's legitimate NAME and
# appears all over the config and test trees.
_FORBIDDEN: tuple[tuple[str, str], ...] = tuple(
    (name, rf"\b{re.escape(name)}\b")
    for name in _WITHDRAWN_SYMBOLS + _RETIRED_TRANSPORT_SYMBOLS
) + (("api_key=<dummy>", r"""api_key\s*=\s*['"]ollama['"]"""),)

_forbidden_sweep = pytest.mark.parametrize(
    "label,pattern", _FORBIDDEN, ids=[label for label, _ in _FORBIDDEN]
)

# Symbols the withdrawal deliberately KEPT. The positive control for the
# absence probes: if ``day`` were bound to the wrong object, or the module were
# a stub, every ``not hasattr`` below would pass for the wrong reason.
_STILL_PRESENT = (
    "_ai_diary",
    "_DIARY_FALLBACK",
    "_clamp_diary_entry",
    "_persist_diary",
    "day_diary",
)


# ---------------------------------------------------------------------------
# 1. The module attributes are gone.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _WITHDRAWN_SYMBOLS)
def test_the_withdrawn_helper_is_not_an_attribute_of_the_day_module(
    name: str,
) -> None:
    """Each recovery helper is absent from ``graphia.nodes.day``.

    The cheapest possible statement of "gone rather than unused": a helper left
    in place but no longer called is still a second mechanism able to produce a
    readable entry, which is the thing ADR 013 §3 rules out.
    """
    assert not hasattr(day, name), (
        f"graphia.nodes.day still defines {name!r} — spec 041 Slice 4 withdrew "
        "the interim prose recovery in one contiguous cut, so a surviving "
        "helper means the cut was partial."
    )


def test_the_json_import_is_gone_from_the_day_module() -> None:
    """``import json`` was the recovery's only reason to exist in this module.

    Precise rather than incidental: a module-scope ``import json`` creates
    exactly the attribute ``json`` on the module, so this distinguishes "the
    import was dropped with the code that needed it" from "the import is still
    sitting there, now unused". The recovery parsed a JSON blob out of prose;
    nothing else in ``nodes/day.py`` ever needed the module.
    """
    assert not hasattr(day, "json"), (
        "graphia.nodes.day still imports json — the interim prose recovery was "
        "its only consumer, so a surviving import is the withdrawal's leftover."
    )


def test_the_probes_are_pointed_at_the_real_production_module() -> None:
    """Positive control: the two tests above assert absence *from the right thing*.

    An ``hasattr`` probe is only as good as its object. If ``day`` resolved to a
    stub, a namespace package, or a stale ``.pyc``, every absence assertion in
    this module would be green and prove nothing. So: it is the real module, at
    the real path, and the symbols the withdrawal deliberately KEPT are all
    still there.
    """
    assert day.__name__ == "graphia.nodes.day"
    assert day.__file__ is not None
    assert Path(day.__file__).resolve().parts[-3:] == ("graphia", "nodes", "day.py")
    for name in _STILL_PRESENT:
        assert hasattr(day, name), (
            f"{name!r} is missing from graphia.nodes.day — the withdrawal cut "
            "further than Slice 4's scope, and this module's absence probes "
            "cannot be trusted until that is explained."
        )


def test_the_diary_call_site_passes_no_kwargs_of_its_own() -> None:
    """``_ai_diary``'s body binds ``Diary`` with nothing beside it.

    The behavioural inverse of the deleted
    ``test_the_diary_call_site_keeps_include_raw_beside_the_pinned_method``, and
    the assertion that catches "the withdrawal edited the docstring but left the
    kwarg". Source-level on purpose — this is the one check of the diary call
    site that consults neither
    ``tests/test_provider_seam_through_the_node.py``'s ``_CallSite`` table nor
    the guard over it, so it still holds the day a future call site legitimately
    declares a kwarg there.

    The docstring is stripped before the search: it is prose about the
    withdrawal and may well need to *name* ``include_raw`` to explain what went
    (as it names the retired transport today). Only executable lines are
    searched, which is where a re-added kwarg would actually live.
    """
    source = inspect.getsource(day._ai_diary)
    doc = day._ai_diary.__doc__
    assert doc, "_ai_diary lost its docstring; this test's stripping is unsound."
    body = source.replace(doc, "")

    assert "with_structured_output(Diary)" in body, (
        "_ai_diary no longer contains the bare `with_structured_output(Diary)` "
        "call this test reads — the call site moved or changed shape, so the "
        "assertion below is no longer looking at anything."
    )
    assert "include_raw" not in body, (
        "_ai_diary's body mentions include_raw again. Spec 041 Slice 4 withdrew "
        "the envelope request together with the prose recovery that consumed "
        "it; re-adding it restores the two-mechanism ambiguity ADR 013 §3 rules "
        "out."
    )


# ---------------------------------------------------------------------------
# 2. The names survive nowhere in src/ or tests/.
# ---------------------------------------------------------------------------


@_forbidden_sweep
def test_no_withdrawn_or_retired_name_survives_in_the_source_trees(
    label: str, pattern: str
) -> None:
    """Neither tree names the withdrawn recovery or the retired transport.

    Prose counts. A comment or docstring citing a function that no longer
    exists is exactly the "left passing against nothing" the functional spec
    names: the reader follows the citation, finds nothing, and cannot tell
    whether the argument around it still holds. Where such an argument DOES
    still hold — ``instrument.py``'s reason for not requiring ``raw`` — Slice 4
    kept the argument and dropped the citation.

    ``context/`` is not swept: ADR 010, ADR 013 and the specs must keep naming
    what they retired.
    """
    rx = re.compile(pattern)
    hits = [
        f"{path.relative_to(_REPO_ROOT).as_posix()}:{lineno}: {line.strip()}"
        for path, text in _SWEPT_FILES
        for lineno, line in enumerate(text.splitlines(), start=1)
        if rx.search(line)
    ]
    assert hits == [], (
        f"{label} survives in the source trees ({len(hits)} hit(s)):\n"
        + "\n".join(hits)
    )


def test_the_sweep_actually_read_both_trees() -> None:
    """The sweep above is not green over an empty iterator.

    The one failure mode a grep-for-absence test cannot detect by itself: point
    it at a directory that does not exist, or filter every file out, and it
    passes with total confidence. Floors rather than exact counts (42 src / 80
    test files at the time of writing) because files are added constantly and a
    guard that needs editing on every new module gets deleted.
    """
    assert _SRC_DIR.is_dir()
    assert len(_SRC_FILES) >= 35, f"only {len(_SRC_FILES)} files read under src/"
    assert len(_TEST_FILES) >= 70, f"only {len(_TEST_FILES)} files read under tests/"

    swept = {path.relative_to(_REPO_ROOT).as_posix() for path, _ in _SWEPT_FILES}
    # Landmarks: the module the withdrawal cut, the module whose citation it
    # fixed, and the suite's own conftest. A filter that silently dropped, say,
    # every file with an underscore would still clear the floors above.
    assert "src/graphia/nodes/day.py" in swept
    assert "src/graphia/tools/instrument.py" in swept
    assert "src/graphia/tools/ollama_smoke.py" in swept
    assert "tests/conftest.py" in swept
    assert "tests/test_instrument_capture.py" in swept

    # And the self-exclusion resolved to exactly one file: this one, which names
    # every forbidden string above and would otherwise fail its own sweep.
    assert _THIS_FILE.is_file()
    assert _THIS_FILE.parent == _TESTS_DIR
    assert "tests/test_spec041_withdrawal.py" not in swept
    all_test_files = [
        p
        for p in sorted(_TESTS_DIR.rglob("*"))
        if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts
    ]
    assert len(all_test_files) - len(_TEST_FILES) == 1


@_forbidden_sweep
def test_the_forbidden_patterns_match_what_they_forbid(
    label: str, pattern: str
) -> None:
    """Positive control: every pattern can actually fail.

    Word boundaries and escaped regexes are easy to get subtly wrong, and a
    pattern that matches nothing produces the same green as a clean tree. Each
    pattern is matched here against a sample of the exact line it exists to
    forbid — the withdrawn definitions and citations as they stood before Task
    4.1, and the retired transport as ``graphia.llm`` built it before Task 2.2.
    """
    samples = {
        "_DIARY_LABEL_MAX": "_DIARY_LABEL_MAX = 40",
        "_content_text": "def _content_text(message: BaseMessage) -> str:",
        "_recovered_diary_text": "text = _recovered_diary_text(result)",
        "_diary_entry_from": "# ``day._diary_entry_from`` already tolerates it",
        "langchain_anthropic": "from langchain_anthropic import ChatAnthropic",
        "ChatAnthropic": "        return ChatAnthropic(model=self.large_model)",
        "_OLLAMA_DUMMY_API_KEY": '_OLLAMA_DUMMY_API_KEY = "ollama"',
        # The retired code extracted the key to the constant above, so its use
        # sites named the constant; this pattern is aimed at the inlined form a
        # future re-introduction would most likely take.
        "api_key=<dummy>": '            api_key="ollama",',
    }
    assert set(samples) == {lbl for lbl, _ in _FORBIDDEN}, (
        "the sample map has drifted from the pattern table; a pattern with no "
        "sample is a pattern nobody proved can match."
    )
    assert re.search(pattern, samples[label]), (
        f"{label}'s pattern {pattern!r} does not match the line it exists "
        f"to forbid: {samples[label]!r}"
    )


@pytest.mark.parametrize(
    "legitimate",
    [
        pytest.param('GRAPHIA_LLM_PROVIDER = "ollama"', id="provider-env-value"),
        pytest.param('provider="ollama"', id="provider-kwarg"),
        pytest.param('assert config.llm_provider == "ollama"', id="provider-assert"),
        pytest.param("from langchain_ollama import ChatOllama", id="native-client"),
        pytest.param("def message_content_text(msg): ...", id="superstring-ident"),
        pytest.param("_diary_entry_from_store = None", id="prefixed-ident"),
        pytest.param("aws_api_key = os.environ[...]", id="unrelated-api-key"),
    ],
)
def test_the_forbidden_patterns_do_not_flag_legitimate_lines(
    legitimate: str,
) -> None:
    """Negative control: the sweep must not fire on what legitimately remains.

    ``"ollama"`` is the provider's NAME and appears throughout config, the
    preflight and the test tree — it is the dummy *api key* that went, and the
    two are the same four letters. Likewise ``\\b`` is what keeps
    ``_content_text`` from being reported against ``message_content_text``. A
    sweep that cries wolf gets weakened by the next person who has to make it
    pass, which is a slower version of deleting it.
    """
    for label, pattern in _FORBIDDEN:
        assert not re.search(pattern, legitimate), (
            f"{label}'s pattern {pattern!r} falsely flags {legitimate!r}"
        )


def test_langchain_anthropic_is_absent_from_pyproject() -> None:
    """The retired client is not a declared dependency, in either spelling.

    Both spellings, because the distribution name (``langchain-anthropic``) and
    the import name (``langchain_anthropic``) differ and a manifest can carry
    either. The two positive assertions first are the anti-vacuity half: they
    prove the real manifest was read, not an empty string from a mistyped path.
    """
    text = _PYPROJECT.read_text(encoding="utf-8")

    assert "langchain-ollama" in text, f"{_PYPROJECT} is not the real manifest"
    assert "langchain-aws" in text, f"{_PYPROJECT} is not the real manifest"

    assert "langchain-anthropic" not in text
    assert "langchain_anthropic" not in text
