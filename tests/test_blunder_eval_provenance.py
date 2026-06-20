"""Offline unit tests for the run-provenance + run-quality block (spec 011, Slice 4).

Locks in the Slice-4 surface of ``src/graphia/tools/blunder_eval.py`` — the
collectors that make a ledger record *attributable to a code version and a model
fingerprint* — **without ever reaching real git, the network, or a live model**.
Four concerns are covered, each pinned by stubbing the documented injectable
seams (never running git or HTTP):

1. **Code provenance** (``collect_code_provenance`` / ``warn_if_dirty``) — the
   ``_git_output`` seam is monkeypatched to script the three git answers
   (``rev-parse HEAD``, ``rev-parse --abbrev-ref HEAD``, ``status --porcelain``):
   a clean tree (``dirty: False``, no warning), a dirty tree (``dirty: True`` +
   the up-front stderr warning captured via ``capsys``), and graceful
   degradation when git is missing / the dir is not a repo (seam returns
   ``None`` *or* raises) → ``{commit:None, branch:None, dirty:False}``, no crash.

2. **Ollama model provenance** (``collect_ollama_model_provenance``) — the
   ``_ollama_get_json`` seam is monkeypatched to feed a synthetic ``/api/tags``
   (digests) and ``/api/version`` payload, mirroring the preflight HTTP-stub
   idiom (``tests/test_ollama_preflight.py``) but at *this* module's seam. Each
   configured model resolves to its ``{name, digest}`` under the preflight
   tag-match rule (tagged → exact; tagless → any tag of the base; ``_digest_for``
   is also exercised directly), the server version is recorded, and an
   unreachable server (seam returns ``None`` / raises) degrades to ``None``
   digests + ``None`` version with no crash.

3. **Provider provenance** (``collect_provider_provenance``) — ollama enriches
   the flat identity with digests + ``server_version``; bedrock attaches the
   fixed invisible-updates ``note`` (``_BEDROCK_UPDATE_NOTE``) and makes **no**
   HTTP attempt (the ollama seam is asserted *not* called for bedrock).

4. **Full-record integration** — a synthetic ``EvalResult`` carrying stubbed
   provenance renders, via ``render_record``, with the full fixed top-level key
   order ``run → code → provider → settings → quality → metrics → notes``;
   ``code.dirty`` and the ``quality`` duration are reflected; ``notes`` is last;
   the ollama ``models`` digests and ``server_version`` land under ``provider``;
   the bedrock ``note`` lands under ``provider``. Appending twice to a
   ``tmp_path`` ledger accumulates two ``---``-separated docs (never the real
   ledger). The document parses under PyYAML when importable, else is asserted
   structurally (the repo ships no YAML parser).

Real functions/constants are imported, so a rename breaks these tests honestly.
Everything is stubbed and offline: no provider client is built, no git binary or
socket is touched, and the autouse ``safe_llm`` net is left intact.
"""

from __future__ import annotations

import io
import subprocess
import urllib.request
from pathlib import Path

import pytest

from graphia.tools import blunder_eval
from graphia.tools.blunder_eval import (
    EvalResult,
    METRICS_VERSION,
    _BEDROCK_UPDATE_NOTE,
    _digest_for,
    append_record,
    collect_code_provenance,
    collect_ollama_model_provenance,
    collect_provider_provenance,
    render_record,
    warn_if_dirty,
)

_REPO = Path("/some/repo/root")
_BASE_URL = "http://localhost:11434"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_BRANCH = "main"


# ===========================================================================
# Seam stubs — script the two injectable boundaries (git + ollama HTTP).
#
# The collectors are PURE/INJECTABLE: they reach the outside world only through
# ``blunder_eval._git_output`` and ``blunder_eval._ollama_get_json``. We replace
# those module attributes so no git binary runs and no socket is opened.
# ===========================================================================


def _stub_git(
    monkeypatch: pytest.MonkeyPatch,
    answers: dict[tuple[str, ...], str | None],
    *,
    default: str | None = None,
) -> list[tuple[str, ...]]:
    """Replace ``_git_output`` with a scripted lookup keyed by the git args.

    ``answers`` maps the exact ``*args`` tuple a collector passes
    (``("rev-parse", "HEAD")``, ``("status", "--porcelain")``, …) to the stripped
    stdout the real seam would return — or ``None`` for the "git failed / not a
    repo" outcome. Unscripted arg tuples fall back to ``default``. Returns the
    call log so a test can assert exactly which git invocations were made.
    """
    calls: list[tuple[str, ...]] = []

    def _fake_git_output(repo_root: Path, *args: str) -> str | None:
        calls.append(args)
        return answers.get(args, default)

    monkeypatch.setattr(blunder_eval, "_git_output", _fake_git_output)
    return calls


def _make_tags_payload(models: list[dict[str, object]]) -> dict[str, object]:
    """A realistic ``/api/tags`` body (extra per-model fields, like a real server)."""
    return {
        "models": [
            {
                "name": m["name"],
                "model": m["name"],
                "modified_at": "2026-06-01T00:00:00Z",
                "size": 4_683_087_332,
                "digest": m.get("digest"),
                "details": {"family": "qwen2", "parameter_size": "7B"},
            }
            for m in models
        ]
    }


def _stub_ollama(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, dict[str, object] | None],
) -> list[str]:
    """Replace ``_ollama_get_json`` with a scripted ``path -> payload`` lookup.

    Mirrors ``tests/test_ollama_preflight.py``'s HTTP stub, but one level up: it
    intercepts the JSON-decode seam directly (the collectors' only I/O) rather
    than ``urllib.request.urlopen`` — so the synthetic ``/api/tags`` and
    ``/api/version`` bodies are handed in already-parsed. A ``None`` value (or an
    unscripted path) models an unreachable server / bad body. Returns the path
    call log so a test can assert which endpoints were hit (and that bedrock hits
    none).
    """
    paths: list[str] = []

    def _fake_get_json(base_url: str, path: str) -> dict[str, object] | None:
        paths.append(path)
        return responses.get(path)

    monkeypatch.setattr(blunder_eval, "_ollama_get_json", _fake_get_json)
    return paths


def _exploding_ollama(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any call to the ollama seam is a test failure; returns the (empty) log.

    Used for the bedrock provider, which must fingerprint without HTTP.
    """
    paths: list[str] = []

    def _boom(base_url: str, path: str) -> dict[str, object] | None:
        paths.append(path)
        raise AssertionError(
            "bedrock provenance must not GET the Ollama API"
        )

    monkeypatch.setattr(blunder_eval, "_ollama_get_json", _boom)
    return paths


# ===========================================================================
# 1. Code provenance — clean / dirty / non-repo, plus the dirty warning
# ===========================================================================


def test_collect_code_provenance_clean_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean working copy → commit + branch recorded, ``dirty: False``.

    The porcelain status returns the empty string (git answered, no changes), so
    ``dirty`` is the load-bearing ``False`` — not the "git failed" ``None`` path.
    """
    calls = _stub_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): _COMMIT,
            ("rev-parse", "--abbrev-ref", "HEAD"): _BRANCH,
            ("status", "--porcelain"): "",
        },
    )

    code = collect_code_provenance(_REPO)

    assert code == {"commit": _COMMIT, "branch": _BRANCH, "dirty": False}
    # The three documented git invocations were made (and only those).
    assert calls == [
        ("rev-parse", "HEAD"),
        ("rev-parse", "--abbrev-ref", "HEAD"),
        ("status", "--porcelain"),
    ]


def test_collect_code_provenance_dirty_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A porcelain status reporting changes → ``dirty: True`` (commit still kept)."""
    _stub_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): _COMMIT,
            ("rev-parse", "--abbrev-ref", "HEAD"): _BRANCH,
            ("status", "--porcelain"): " M src/graphia/tools/blunder_eval.py",
        },
    )

    code = collect_code_provenance(_REPO)

    assert code["dirty"] is True
    assert code["commit"] == _COMMIT
    assert code["branch"] == _BRANCH


def test_collect_code_provenance_non_repo_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git unavailable / not a repo (seam returns ``None``) → all-degraded, no crash.

    A failed ``status`` (``None``, "unknown") must NOT be read as dirty — the
    record stays unattributable but is not spuriously flagged modified.
    """
    _stub_git(monkeypatch, {}, default=None)  # every git call "fails"

    code = collect_code_provenance(_REPO)

    assert code == {"commit": None, "branch": None, "dirty": False}


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(FileNotFoundError("git not found"), id="git_missing"),
        pytest.param(OSError("permission denied"), id="oserror"),
        pytest.param(subprocess.TimeoutExpired("git", 3.0), id="timeout"),
    ],
)
def test_git_output_swallows_subprocess_errors_to_none(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """The real ``_git_output`` seam swallows OS/subprocess errors → ``None``.

    This is the documented graceful-degradation mechanism: the seam (not the
    collector) absorbs a missing-git / timeout / OS error and returns ``None``,
    so :func:`collect_code_provenance` then folds that ``None`` into the
    all-degraded record. Exercised at the real ``subprocess.run`` boundary —
    mirroring how the preflight tests stub ``urllib.request.urlopen`` — so the
    seam's own try/except is what is under test, not a re-stub of itself.
    """

    def _boom(*args: object, **kwargs: object) -> object:
        raise exc

    monkeypatch.setattr(subprocess, "run", _boom)

    assert blunder_eval._git_output(_REPO, "rev-parse", "HEAD") is None


def test_git_output_nonzero_exit_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero git exit (e.g. "not a git repository") → ``None`` (handled, not raised)."""

    class _Proc:
        returncode = 128
        stdout = "fatal: not a git repository"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())

    assert blunder_eval._git_output(_REPO, "status", "--porcelain") is None


def test_collect_code_provenance_through_the_real_seam_when_git_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a failing real seam degrades the whole code block, no crash.

    Drives :func:`collect_code_provenance` through the genuine ``_git_output``
    seam (not a re-stubbed collector hook) with ``subprocess.run`` raising
    ``FileNotFoundError`` — the missing-git case — and asserts the all-degraded
    record without any exception propagating.
    """

    def _boom(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _boom)

    code = collect_code_provenance(_REPO)

    assert code == {"commit": None, "branch": None, "dirty": False}


def test_warn_if_dirty_prints_warning_for_dirty_tree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dirty code block prints the up-front un-attributable warning to stderr."""
    warn_if_dirty({"commit": _COMMIT, "branch": _BRANCH, "dirty": True})

    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout
    assert "WARNING" in captured.err
    assert "uncommitted changes" in captured.err
    # Names the very flag the record will carry, so the maintainer connects the
    # warning to the recorded fact.
    assert "code.dirty" in captured.err


def test_warn_if_dirty_is_silent_for_clean_tree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A clean tree prints nothing (no spurious warning before games start)."""
    warn_if_dirty({"commit": _COMMIT, "branch": _BRANCH, "dirty": False})

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_warn_if_dirty_is_silent_for_unknown_tree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-repo (``dirty`` absent/falsey) tree prints nothing."""
    warn_if_dirty({"commit": None, "branch": None, "dirty": False})

    assert capsys.readouterr().err == ""


# ===========================================================================
# 2. Ollama model provenance — digests + server version, tag-match, degradation
# ===========================================================================


def test_digest_for_tagged_exact_match() -> None:
    """A tagged config name resolves to the digest of the exact installed tag."""
    installed = [
        {"name": "qwen2.5:3b", "digest": "sha256:aaa"},
        {"name": "qwen2.5:7b", "digest": "sha256:bbb"},
    ]
    assert _digest_for("qwen2.5:7b", installed) == "sha256:bbb"


def test_digest_for_tagless_matches_any_tag_of_base() -> None:
    """A tagless config name resolves to any installed tag of the same base model."""
    installed = [{"name": "qwen2.5:latest", "digest": "sha256:ccc"}]
    assert _digest_for("qwen2.5", installed) == "sha256:ccc"


def test_digest_for_absent_model_is_none() -> None:
    """A model the server does not report yields ``None`` (no fingerprint, no crash)."""
    installed = [{"name": "llama3.2:1b", "digest": "sha256:ddd"}]
    assert _digest_for("qwen2.5:7b", installed) is None


def test_digest_for_tagless_does_not_prefix_match_a_different_base() -> None:
    """``qwen2.5`` must not match ``qwen2.5-coder`` — base equality, not prefix."""
    installed = [{"name": "qwen2.5-coder:7b", "digest": "sha256:eee"}]
    assert _digest_for("qwen2.5", installed) is None


def test_collect_ollama_model_provenance_records_digests_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each configured model → ``{name, digest}``; the server version is recorded.

    A tagged model takes an exact-tag digest, a tagless model takes any-tag — the
    preflight rule, end-to-end through the collector with a synthetic ``/api/tags``
    and ``/api/version`` body.
    """
    paths = _stub_ollama(
        monkeypatch,
        {
            "/api/tags": _make_tags_payload(
                [
                    {"name": "qwen2.5:7b", "digest": "sha256:large"},
                    {"name": "qwen2.5:3b", "digest": "sha256:small"},
                    {"name": "llama3.2:1b", "digest": "sha256:other"},
                ]
            ),
            "/api/version": {"version": "0.5.7"},
        },
    )

    prov = collect_ollama_model_provenance(
        _BASE_URL, ["qwen2.5:7b", "qwen2.5"]
    )

    assert prov["models"] == {
        "qwen2.5:7b": {"name": "qwen2.5:7b", "digest": "sha256:large"},
        # tagless: resolves against any installed tag of the base (7b, first seen)
        "qwen2.5": {"name": "qwen2.5", "digest": "sha256:large"},
    }
    assert prov["server_version"] == "0.5.7"
    # Exactly the two documented endpoints were GET-ed.
    assert paths == ["/api/tags", "/api/version"]


def test_collect_ollama_model_provenance_dedupes_model_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large==small config collapses to one ``models`` entry, order preserved."""
    _stub_ollama(
        monkeypatch,
        {
            "/api/tags": _make_tags_payload(
                [{"name": "llama3.1:8b", "digest": "sha256:dup"}]
            ),
            "/api/version": {"version": "0.6.0"},
        },
    )

    prov = collect_ollama_model_provenance(
        _BASE_URL, ["llama3.1:8b", "llama3.1:8b"]
    )

    assert list(prov["models"]) == ["llama3.1:8b"]
    assert prov["models"]["llama3.1:8b"]["digest"] == "sha256:dup"


def test_collect_ollama_model_provenance_missing_model_digest_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured model absent from ``/api/tags`` → ``digest: None`` (run records)."""
    _stub_ollama(
        monkeypatch,
        {
            "/api/tags": _make_tags_payload(
                [{"name": "qwen2.5:7b", "digest": "sha256:large"}]
            ),
            "/api/version": {"version": "0.5.7"},
        },
    )

    prov = collect_ollama_model_provenance(
        _BASE_URL, ["qwen2.5:7b", "not-installed:1b"]
    )

    assert prov["models"]["qwen2.5:7b"]["digest"] == "sha256:large"
    assert prov["models"]["not-installed:1b"]["digest"] is None


def test_collect_ollama_model_provenance_unreachable_server_is_all_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable server (seam returns ``None``) → null digests + null version.

    The record still forms — every configured model is present, just without a
    fingerprint — so a down server degrades the run rather than failing it.
    """
    _stub_ollama(monkeypatch, {"/api/tags": None, "/api/version": None})

    prov = collect_ollama_model_provenance(
        _BASE_URL, ["qwen2.5:7b", "qwen2.5:3b"]
    )

    assert prov["models"] == {
        "qwen2.5:7b": {"name": "qwen2.5:7b", "digest": None},
        "qwen2.5:3b": {"name": "qwen2.5:3b", "digest": None},
    }
    assert prov["server_version"] is None


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(OSError("connection refused"), id="connection_refused"),
        pytest.param(TimeoutError("timed out"), id="socket_timeout"),
    ],
)
def test_ollama_get_json_swallows_http_errors_to_none(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """The real ``_ollama_get_json`` seam swallows OS/decode errors → ``None``.

    The graceful-degradation mechanism, pinned at the genuine
    ``urllib.request.urlopen`` boundary (the same boundary the preflight tests
    stub): an unreachable server raises, the seam absorbs it and returns
    ``None``, and the collector then records null digests/version.
    """

    def _boom(url: str, timeout: float | None = None) -> object:
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    assert blunder_eval._ollama_get_json(_BASE_URL, "/api/tags") is None


def test_ollama_get_json_non_json_body_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON body (an impostor on the port) → ``None`` (``ValueError`` swallowed)."""

    def _fake_urlopen(url: str, timeout: float | None = None) -> io.BytesIO:
        return io.BytesIO(b"<html>not ollama</html>")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    assert blunder_eval._ollama_get_json(_BASE_URL, "/api/version") is None


def test_collect_ollama_model_provenance_through_the_real_seam_when_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the real HTTP seam: a down server → null digests/version.

    Drives :func:`collect_ollama_model_provenance` through the genuine
    ``_ollama_get_json`` seam with ``urllib.request.urlopen`` raising — so the
    seam's own try/except is what degrades the run, with no exception escaping.
    """

    def _boom(url: str, timeout: float | None = None) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    prov = collect_ollama_model_provenance(_BASE_URL, ["qwen2.5:7b"])

    assert prov["models"]["qwen2.5:7b"]["digest"] is None
    assert prov["server_version"] is None


def test_collect_ollama_model_provenance_non_string_version_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed ``/api/version`` (non-string version) degrades to ``None``."""
    _stub_ollama(
        monkeypatch,
        {
            "/api/tags": _make_tags_payload(
                [{"name": "qwen2.5:7b", "digest": "sha256:large"}]
            ),
            "/api/version": {"version": 57},  # not a string
        },
    )

    prov = collect_ollama_model_provenance(_BASE_URL, ["qwen2.5:7b"])

    assert prov["server_version"] is None


# ===========================================================================
# 3. Provider provenance — ollama enrichment vs. the fixed bedrock note
# ===========================================================================


def test_collect_provider_provenance_ollama_adds_digests_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ollama → flat identity PLUS the ``models`` digests + ``server_version``."""
    _stub_ollama(
        monkeypatch,
        {
            "/api/tags": _make_tags_payload(
                [
                    {"name": "qwen2.5:7b", "digest": "sha256:large"},
                    {"name": "qwen2.5:3b", "digest": "sha256:small"},
                ]
            ),
            "/api/version": {"version": "0.5.7"},
        },
    )

    block = collect_provider_provenance(
        "ollama", "qwen2.5:7b", "qwen2.5:3b", _BASE_URL
    )

    assert block["name"] == "ollama"
    assert block["large_model"] == "qwen2.5:7b"
    assert block["small_model"] == "qwen2.5:3b"
    assert block["models"]["qwen2.5:7b"]["digest"] == "sha256:large"
    assert block["models"]["qwen2.5:3b"]["digest"] == "sha256:small"
    assert block["server_version"] == "0.5.7"
    # No bedrock note on the ollama shape.
    assert "note" not in block


def test_collect_provider_provenance_bedrock_attaches_the_fixed_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bedrock → the fixed invisible-updates note, and NO HTTP is attempted.

    The ollama seam is wired to explode if reached, so this both pins the note
    text (imported, not hardcoded) and proves bedrock fingerprints without a GET.
    """
    paths = _exploding_ollama(monkeypatch)

    block = collect_provider_provenance(
        "bedrock",
        "us.amazon.nova-pro-v1:0",
        "us.amazon.nova-lite-v1:0",
        _BASE_URL,
    )

    assert block["name"] == "bedrock"
    assert block["large_model"] == "us.amazon.nova-pro-v1:0"
    assert block["small_model"] == "us.amazon.nova-lite-v1:0"
    assert block["note"] == _BEDROCK_UPDATE_NOTE
    # bedrock carries no ollama-only fingerprint fields.
    assert "models" not in block
    assert "server_version" not in block
    # The ollama HTTP seam was never called for bedrock.
    assert paths == []


def test_collect_provider_provenance_ollama_unreachable_still_returns_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable ollama server still yields a block (null digests/version)."""
    _stub_ollama(monkeypatch, {"/api/tags": None, "/api/version": None})

    block = collect_provider_provenance(
        "ollama", "qwen2.5:7b", "qwen2.5:3b", _BASE_URL
    )

    assert block["models"]["qwen2.5:7b"]["digest"] is None
    assert block["server_version"] is None


# ===========================================================================
# 4. Full-record integration — provenance through render_record + the ledger
# ===========================================================================


def _result_with_provenance(*, dirty: bool, provider: str) -> EvalResult:
    """A populated ``EvalResult`` carrying the Slice-4 provenance blocks.

    Built from the real dataclass + the real collectors' output shape, so a
    field/key rename breaks the integration assertions honestly. ``duration_seconds``
    is set (the run finished) so the ``quality`` duration is non-null.
    """
    if provider == "ollama":
        provider_block: dict[str, object] = {
            "name": "ollama",
            "large_model": "qwen2.5:7b",
            "small_model": "qwen2.5:3b",
            "models": {
                "qwen2.5:7b": {"name": "qwen2.5:7b", "digest": "sha256:large"},
                "qwen2.5:3b": {"name": "qwen2.5:3b", "digest": "sha256:small"},
            },
            "server_version": "0.5.7",
        }
        large, small, base = "qwen2.5:7b", "qwen2.5:3b", _BASE_URL
    else:
        provider_block = {
            "name": "bedrock",
            "large_model": "us.amazon.nova-pro-v1:0",
            "small_model": "us.amazon.nova-lite-v1:0",
            "note": _BEDROCK_UPDATE_NOTE,
        }
        large, small, base = (
            "us.amazon.nova-pro-v1:0",
            "us.amazon.nova-lite-v1:0",
            None,
        )

    return EvalResult(
        provider=provider,
        large_model=large,
        small_model=small,
        games_attempted=5,
        games_completed=4,
        games_failed_early=1,
        code={"commit": _COMMIT, "branch": _BRANCH, "dirty": dirty},
        provider_block=provider_block,
        settings={
            "large_model": large,
            "small_model": small,
            "base_url": base,
            "games": 5,
            "seed": 20260613,
            # Spec 023: the recorded game-length control is the runaway Day cap.
            "max_days": 12,
        },
        duration_seconds=42.5,
        metrics={"repetition": {"rate": 0.4, "count": 2, "denominator": 5}},
    )


def _top_level_keys(doc: str) -> list[str]:
    """Top-level YAML keys of a rendered record, in document order.

    An unindented ``key:`` / ``key: <scalar>`` line — mirrors the helper in
    ``tests/test_blunder_eval.py`` so the key-order assertion reads the same way.
    """
    keys: list[str] = []
    for ln in doc.splitlines():
        if not ln or ln.startswith(" "):
            continue
        head, _, _ = ln.partition(":")
        if head and head == head.strip():
            keys.append(head)
    return keys


_FIXED_KEY_ORDER = [
    "run",
    "code",
    "provider",
    "settings",
    "quality",
    "metrics",
    "notes",
]


@pytest.mark.parametrize("dirty", [False, True], ids=["clean", "dirty"])
def test_render_record_full_provenance_key_order(dirty: bool) -> None:
    """The full provenance record keeps the fixed top-level key order, both dirties."""
    doc = render_record(_result_with_provenance(dirty=dirty, provider="ollama"), "2026-06-13")

    assert _top_level_keys(doc) == _FIXED_KEY_ORDER


@pytest.mark.parametrize(
    "dirty, expected",
    [(False, "    dirty: false"), (True, "    dirty: true")],
    ids=["clean", "dirty"],
)
def test_render_record_reflects_code_dirty_flag(
    dirty: bool, expected: str
) -> None:
    """``code.dirty`` renders the actual flag, and commit/branch land under ``code``."""
    doc = render_record(_result_with_provenance(dirty=dirty, provider="ollama"), "2026-06-13")
    lines = doc.splitlines()

    code_i = lines.index("code:")
    assert lines[code_i + 1] == f"  commit: '{_COMMIT}'"
    assert lines[code_i + 2] == f"  branch: '{_BRANCH}'"
    assert lines[code_i + 3] == f"  dirty: {'true' if dirty else 'false'}"


def test_render_record_quality_block_carries_duration_and_counts() -> None:
    """The ``quality`` block carries attempted/completed/failed_early + duration."""
    doc = render_record(_result_with_provenance(dirty=False, provider="ollama"), "2026-06-13")
    lines = doc.splitlines()

    q_i = lines.index("quality:")
    assert lines[q_i + 1] == "  games_attempted: 5"
    assert lines[q_i + 2] == "  games_completed: 4"
    assert lines[q_i + 3] == "  games_failed_early: 1"
    assert lines[q_i + 4] == "  duration_seconds: 42.5"
    # The run block carries the duration + metrics_version too.
    assert "  duration_seconds: 42.5" in doc
    assert f"  metrics_version: {METRICS_VERSION}" in doc


def test_render_record_ollama_models_digests_render_under_provider() -> None:
    """The ollama ``models`` sub-map (digests) + ``server_version`` land under provider."""
    doc = render_record(_result_with_provenance(dirty=False, provider="ollama"), "2026-06-13")
    lines = doc.splitlines()

    prov_i = lines.index("provider:")
    notes_i = lines.index(next(l for l in lines if l.startswith("notes")))
    # The models nested map and the server version sit inside the provider block.
    assert "  models:" in lines
    assert "    qwen2.5:7b:" in lines or "    'qwen2.5:7b':" in lines
    assert "      digest: 'sha256:large'" in doc
    assert "      digest: 'sha256:small'" in doc
    assert "  server_version: '0.5.7'" in doc
    # All of it is between the provider header and the (later) notes key.
    assert prov_i < lines.index("  server_version: '0.5.7'") < notes_i
    # No bedrock note on an ollama record.
    assert _BEDROCK_UPDATE_NOTE not in doc


def test_render_record_bedrock_note_renders_under_provider() -> None:
    """A bedrock record carries the fixed invisible-updates note under provider."""
    doc = render_record(_result_with_provenance(dirty=False, provider="bedrock"), "2026-06-13")

    assert _top_level_keys(doc) == _FIXED_KEY_ORDER
    assert f"  note: '{_BEDROCK_UPDATE_NOTE}'" in doc
    # bedrock has no ollama-only fingerprint fields rendered.
    assert "  models:" not in doc
    assert "  server_version:" not in doc
    # base_url is null for bedrock in the settings block.
    assert "  base_url: null" in doc


def test_render_record_notes_is_last_with_full_provenance() -> None:
    """``notes`` stays the final top-level key even with the full provenance shape."""
    result = _result_with_provenance(dirty=True, provider="ollama")
    result.notes = "dirty baseline before the fix"

    doc = render_record(result, "2026-06-13")

    assert _top_level_keys(doc)[-1] == "notes"
    assert doc.rstrip("\n").splitlines()[-1] == "notes: 'dirty baseline before the fix'"


def test_append_record_with_provenance_accumulates_two_docs(
    tmp_path: Path,
) -> None:
    """Two appends of a provenance record → two ``---``-separated docs, history kept.

    The injectable ``ledger_path`` points at a temp file — never the real
    ``evals/blunder-ledger.yaml`` — and the second append must not rewrite the
    first.
    """
    ledger = tmp_path / "blunder-ledger.yaml"
    result = _result_with_provenance(dirty=False, provider="ollama")

    append_record(result, "2026-06-13", ledger_path=ledger)
    text_after_first = ledger.read_text(encoding="utf-8")
    append_record(result, "2026-06-14", ledger_path=ledger)
    text_after_second = ledger.read_text(encoding="utf-8")

    assert text_after_second.count("---\n") == 2
    assert text_after_second.startswith(text_after_first)
    # Both the commit fingerprint and both dates survive, in append order.
    assert text_after_second.count(f"commit: '{_COMMIT}'") == 2
    assert text_after_second.index("date: '2026-06-13'") < text_after_second.index(
        "date: '2026-06-14'"
    )


def test_render_record_full_provenance_is_yaml_parseable_if_pyyaml_present() -> None:
    """If PyYAML is importable, the document round-trips to the expected structure.

    Optional: the repo ships no YAML parser, so this SKIPS rather than fails when
    PyYAML is absent — the structural assertions above are the load-bearing ones.
    When a parser is present it gives an extra, stronger guarantee that the
    hand-rendered YAML is genuinely well-formed (digests, the dotted bedrock ids,
    the date) rather than merely string-matching.
    """
    yaml = pytest.importorskip("yaml")

    doc = render_record(_result_with_provenance(dirty=True, provider="ollama"), "2026-06-13")
    parsed = yaml.safe_load(doc)

    assert list(parsed) == _FIXED_KEY_ORDER
    assert parsed["code"]["commit"] == _COMMIT
    assert parsed["code"]["dirty"] is True
    assert parsed["provider"]["server_version"] == "0.5.7"
    assert parsed["provider"]["models"]["qwen2.5:7b"]["digest"] == "sha256:large"
    assert parsed["quality"]["duration_seconds"] == 42.5
    assert parsed["run"]["metrics_version"] == METRICS_VERSION
    assert parsed["metrics"]["repetition"] == {"rate": 0.4, "count": 2, "denominator": 5}
    assert parsed["notes"] == ""
