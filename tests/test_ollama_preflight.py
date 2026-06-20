"""Offline unit tests for the Ollama boot preflight (spec 010, Slice 4).

Covers ``graphia.preflight.run_ollama_preflight`` and its two helpers:

- **Unreachable server** — ``urllib.request.urlopen`` is monkeypatched at the
  urllib boundary (not at ``_fetch_installed_models``) so the seam's own
  ``OSError``/``ValueError`` → ``SystemExit`` mapping is exercised. The exit
  carries the configured base URL plus the ``ollama serve`` fix-it line, and
  is a plain string ``SystemExit`` (message-to-stderr, exit code 1 — no
  traceback), matching functional-spec §2.4's no-stack-trace requirement.
- **Missing models** — one missing → exactly that model named; both missing
  → both named in a single exit (the player learns everything at once).
- **Happy path** — a realistic ``/api/tags`` JSON body with both models
  installed → the preflight returns silently after exactly one GET to
  ``<base_url>/api/tags`` with the 3-second timeout.
- **Tag-matching rule** (``_model_installed``) — tagged config names need an
  exact match; tagless names match any installed tag of the same base model;
  a different base never matches.
- **No-op guards** — provider ``bedrock``, or ``ollama`` under remote mode,
  must never touch HTTP (the stub explodes if reached).

Everything is stubbed and offline: no Ollama server, no network, no LLM
client — the autouse ``safe_llm`` fixture is never tripped.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from graphia.config import GraphiaConfig
from graphia.preflight import (
    _fetch_model_context_length,
    _model_installed,
    run_ollama_preflight,
    warn_if_ollama_context_too_small,
)

_BASE_URL = "http://localhost:11434"
_LARGE_MODEL = "qwen2.5:7b"
_SMALL_MODEL = "qwen2.5:3b"


# ---------------------------------------------------------------------------
# Config factory and HTTP stubs
# ---------------------------------------------------------------------------


def _make_config(
    *,
    llm_provider: str = "ollama",
    remote_mode: bool = False,
    ollama_base_url: str = _BASE_URL,
    ollama_large_model: str = _LARGE_MODEL,
    ollama_small_model: str = _SMALL_MODEL,
    context_token_budget: int = 20000,
) -> GraphiaConfig:
    """Build a ``GraphiaConfig`` directly, bypassing env (frozen dataclass)."""
    return GraphiaConfig(
        bearer_token=None,
        aws_region="us-east-1",
        log_file=Path("./.graphia/graphia.log"),
        checkpoint_dir=Path("./.graphia/checkpoints"),
        stats_file=Path("./.graphia/career.json"),
        human_role=None,
        remote_mode=remote_mode,
        runtime_invocation_url=(
            "https://example.invalid/runtime" if remote_mode else None
        ),
        memory_id=None,
        career_memory_id=None,
        gateway_id=None,
        gateway_url=None,
        cloudwatch_log_group=None,
        stats_strategy_id=None,
        stats_namespace="/career/human-career/",
        llm_provider=llm_provider,
        ollama_base_url=ollama_base_url,
        ollama_large_model=ollama_large_model,
        ollama_small_model=ollama_small_model,
        context_token_budget=context_token_budget,
    )


def _stub_tags_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    installed_models: list[str],
) -> list[tuple[str, float | None]]:
    """Replace ``urllib.request.urlopen`` with a canned ``/api/tags`` reply.

    Returns the call log so tests can assert on the URL and timeout. The
    body mirrors a real Ollama answer (extra per-model fields included) so
    the parser is exercised against realistic JSON, not a minimal shape.
    """
    calls: list[tuple[str, float | None]] = []
    body = json.dumps(
        {
            "models": [
                {
                    "name": name,
                    "model": name,
                    "size": 4_683_087_332,
                    "digest": "abc123",
                    "details": {"family": "qwen2", "parameter_size": "7B"},
                }
                for name in installed_models
            ]
        }
    ).encode("utf-8")

    def _fake_urlopen(url: str, timeout: float | None = None) -> io.BytesIO:
        calls.append((url, timeout))
        return io.BytesIO(body)  # IOBase: supports both `with` and `.read()`

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return calls


def _stub_raising_urlopen(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """Make ``urllib.request.urlopen`` raise, simulating a down/odd server."""

    def _fake_urlopen(url: str, timeout: float | None = None) -> io.BytesIO:
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


@pytest.fixture
def exploding_urlopen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any HTTP attempt is a test failure; returns the (must-stay-empty) log."""
    attempts: list[str] = []

    def _boom(url: str, timeout: float | None = None) -> None:
        attempts.append(url)
        raise AssertionError(
            "preflight touched the network for a config it must no-op on"
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    return attempts


# ---------------------------------------------------------------------------
# 1. Unreachable server (and the non-JSON impostor on the same port)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(
            urllib.error.URLError(ConnectionRefusedError(61, "refused")),
            id="connection_refused",
        ),
        pytest.param(TimeoutError("timed out"), id="socket_timeout"),
        pytest.param(OSError("network unreachable"), id="plain_oserror"),
    ],
)
def test_unreachable_server_exits_with_fixit_message(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """Server down → SystemExit naming the URL and the `ollama serve` fix."""
    _stub_raising_urlopen(monkeypatch, exc)

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config())

    assert exc_info.value.code == (
        f"Couldn't reach Ollama at {_BASE_URL}. Is it running? "
        "Start it with: ollama serve"
    )


def test_unreachable_message_carries_a_custom_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message points at the *configured* URL, not a hardcoded default."""
    _stub_raising_urlopen(monkeypatch, OSError("refused"))
    custom_url = "http://gpu-box:11434"

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config(ollama_base_url=custom_url))

    message = str(exc_info.value)
    assert custom_url in message
    assert "ollama serve" in message


def test_unreachable_exit_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SystemExit with a string code: Python prints the message to stderr
    and exits with status 1 — the player never sees a stack trace."""
    _stub_raising_urlopen(monkeypatch, OSError("refused"))

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config())

    # A non-empty *string* code is what makes the interpreter print the
    # plain message (no traceback) and exit non-zero.
    assert isinstance(exc_info.value.code, str)
    assert exc_info.value.code  # non-empty → exit status 1, not 0
    # The underlying network error is chained for log forensics, but it is
    # the SystemExit (not the OSError) that propagates.
    assert isinstance(exc_info.value.__cause__, OSError)


def test_non_json_response_maps_to_the_unreachable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Something non-Ollama answering on the port (HTML, say) → same exit."""

    def _fake_urlopen(url: str, timeout: float | None = None) -> io.BytesIO:
        return io.BytesIO(b"<html><body>It works!</body></html>")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config())

    message = str(exc_info.value)
    assert f"Couldn't reach Ollama at {_BASE_URL}" in message
    assert "ollama serve" in message
    assert isinstance(exc_info.value.__cause__, ValueError)


# ---------------------------------------------------------------------------
# 2. Missing models
# ---------------------------------------------------------------------------


def test_missing_small_model_names_exactly_that_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large installed, small missing → one line naming only the small one."""
    _stub_tags_endpoint(monkeypatch, [_LARGE_MODEL])

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config())

    assert exc_info.value.code == (
        f"The model '{_SMALL_MODEL}' isn't installed. "
        f"Pull it with: ollama pull {_SMALL_MODEL}"
    )


def test_missing_large_model_names_exactly_that_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Small installed, large missing → only the large one is reported."""
    _stub_tags_endpoint(monkeypatch, [_SMALL_MODEL])

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config())

    message = str(exc_info.value)
    assert f"ollama pull {_LARGE_MODEL}" in message
    assert _SMALL_MODEL not in message


def test_both_models_missing_are_reported_in_one_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty server → both pull commands in a single message, large first."""
    _stub_tags_endpoint(monkeypatch, [])

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(_make_config())

    message = str(exc_info.value)
    lines = message.splitlines()
    assert len(lines) == 2
    assert f"The model '{_LARGE_MODEL}' isn't installed" in lines[0]
    assert f"ollama pull {_LARGE_MODEL}" in lines[0]
    assert f"The model '{_SMALL_MODEL}' isn't installed" in lines[1]
    assert f"ollama pull {_SMALL_MODEL}" in lines[1]


def test_same_model_for_both_slots_is_reported_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large == small (a legal config) missing → de-duped to one line."""
    _stub_tags_endpoint(monkeypatch, [])
    config = _make_config(
        ollama_large_model="llama3.1:8b", ollama_small_model="llama3.1:8b"
    )

    with pytest.raises(SystemExit) as exc_info:
        run_ollama_preflight(config)

    message = str(exc_info.value)
    assert len(message.splitlines()) == 1
    assert message.count("llama3.1:8b") == 2  # once in prose, once in the command


# ---------------------------------------------------------------------------
# 3. Happy path
# ---------------------------------------------------------------------------


def test_both_models_present_passes_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realistic /api/tags body with both models → returns None.

    The first call is the ``/api/tags`` model-installed GET; spec 025 then adds
    a best-effort ``/api/show`` context probe (``warn_if_ollama_context_too_small``).
    The shared stub answers both with the tags body, which has no ``model_info``,
    so the context probe reads no signal and stays silent — the preflight still
    returns ``None``.
    """
    calls = _stub_tags_endpoint(
        monkeypatch, [_LARGE_MODEL, _SMALL_MODEL, "llama3.2:1b"]
    )

    assert run_ollama_preflight(_make_config()) is None

    # The model-installed check is the first call and hits /api/tags with the
    # 3-second timeout. (The spec-025 /api/show probe follows; it passes a
    # urllib.request.Request, not a plain URL string, and is covered by the
    # dedicated context-check tests below.)
    assert calls[0] == (f"{_BASE_URL}/api/tags", 3.0)


def test_trailing_slash_in_base_url_does_not_double_the_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`http://host:11434/` must still hit `/api/tags`, not `//api/tags`."""
    calls = _stub_tags_endpoint(monkeypatch, [_LARGE_MODEL, _SMALL_MODEL])

    run_ollama_preflight(_make_config(ollama_base_url=_BASE_URL + "/"))

    assert calls[0][0] == f"{_BASE_URL}/api/tags"


# ---------------------------------------------------------------------------
# 4. Tag-matching rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "configured, installed, expected",
    [
        pytest.param(
            "qwen2.5:7b", ["qwen2.5:7b"], True, id="tagged_exact_match_passes"
        ),
        pytest.param(
            "qwen2.5:7b",
            ["qwen2.5:3b", "qwen2.5:latest"],
            False,
            id="tagged_wrong_tag_fails",
        ),
        pytest.param(
            "qwen2.5", ["qwen2.5:latest"], True, id="tagless_matches_latest"
        ),
        pytest.param(
            "qwen2.5", ["qwen2.5:7b"], True, id="tagless_matches_any_tag"
        ),
        pytest.param(
            "qwen2.5", ["llama3.2:latest"], False, id="tagless_absent_base_fails"
        ),
        pytest.param(
            "qwen2.5",
            ["qwen2.5-coder:7b"],
            False,
            id="tagless_base_is_not_a_prefix_match",
        ),
        pytest.param("qwen2.5:7b", [], False, id="tagged_empty_server_fails"),
        pytest.param("qwen2.5", [], False, id="tagless_empty_server_fails"),
    ],
)
def test_model_installed_tag_rule(
    configured: str, installed: list[str], expected: bool
) -> None:
    """Tagged names need exact matches; tagless names match any tag."""
    assert _model_installed(configured, installed) is expected


def test_tagless_config_passes_preflight_against_latest_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through run_ollama_preflight: tagless config, tagged server."""
    _stub_tags_endpoint(monkeypatch, ["qwen2.5:latest", "llama3.2:1b"])
    config = _make_config(
        ollama_large_model="qwen2.5", ollama_small_model="llama3.2:1b"
    )

    assert run_ollama_preflight(config) is None


# ---------------------------------------------------------------------------
# 5. No-op guards: never touch HTTP for bedrock or remote play
# ---------------------------------------------------------------------------


def test_bedrock_provider_is_a_no_op(exploding_urlopen: list[str]) -> None:
    """Default provider → preflight returns without any HTTP attempt."""
    run_ollama_preflight(_make_config(llm_provider="bedrock"))

    assert exploding_urlopen == []


def test_ollama_under_remote_mode_is_a_no_op(
    exploding_urlopen: list[str],
) -> None:
    """Remote play skips the preflight even with provider ollama.

    ``load_config()`` rejects this combination, but the preflight's own
    guard must hold for a directly-constructed config too.
    """
    run_ollama_preflight(_make_config(llm_provider="ollama", remote_mode=True))

    assert exploding_urlopen == []


# ---------------------------------------------------------------------------
# 6. Spec 025 — startup context check (warn below budget, never raise)
# ---------------------------------------------------------------------------


def _stub_show_endpoint(
    monkeypatch: pytest.MonkeyPatch, model_info: dict | None
) -> list:
    """Stub ``urllib.request.urlopen`` with a canned ``/api/show`` reply.

    ``model_info`` becomes the response's ``model_info`` object (carrying e.g.
    ``qwen3.context_length``); ``None`` omits it entirely. Returns the call log.
    The check POSTs a ``urllib.request.Request``, so the log records that object.
    """
    calls: list = []
    payload: dict = {"details": {"family": "qwen3"}}
    if model_info is not None:
        payload["model_info"] = model_info
    body = json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout: float | None = None) -> io.BytesIO:
        calls.append((req, timeout))
        return io.BytesIO(body)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return calls


def test_context_check_warns_when_context_below_budget(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A small declared context (< budget) logs a warning pointing at the env var."""
    _stub_show_endpoint(monkeypatch, {"qwen3.context_length": 4096})

    with caplog.at_level("WARNING", logger="graphia.preflight"):
        warn_if_ollama_context_too_small(
            _make_config(context_token_budget=20000)
        )

    assert any(record.levelname == "WARNING" for record in caplog.records)
    message = caplog.text
    assert "4096" in message
    assert "OLLAMA_CONTEXT_LENGTH" in message


def test_context_check_silent_when_context_meets_budget(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A context at/above the budget logs nothing (the configured-server path)."""
    _stub_show_endpoint(monkeypatch, {"qwen3.context_length": 32768})

    with caplog.at_level("WARNING", logger="graphia.preflight"):
        warn_if_ollama_context_too_small(
            _make_config(context_token_budget=20000)
        )

    assert caplog.records == []


def test_context_check_silent_when_signal_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No ``model_info`` (or no context_length key) ⇒ no warning, no raise."""
    _stub_show_endpoint(monkeypatch, None)

    with caplog.at_level("WARNING", logger="graphia.preflight"):
        # Must not raise even though the signal couldn't be read.
        assert (
            warn_if_ollama_context_too_small(
                _make_config(context_token_budget=20000)
            )
            is None
        )

    assert caplog.records == []


def test_context_check_never_raises_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A down server during the context probe is swallowed (never raises)."""

    def _boom(req, timeout: float | None = None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    # No exception escapes — the check is best-effort.
    assert warn_if_ollama_context_too_small(_make_config()) is None


def test_context_check_is_a_no_op_for_bedrock(
    exploding_urlopen: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bedrock provider ⇒ the context check never touches HTTP and never warns."""
    with caplog.at_level("WARNING", logger="graphia.preflight"):
        warn_if_ollama_context_too_small(_make_config(llm_provider="bedrock"))

    assert exploding_urlopen == []
    assert caplog.records == []


def test_context_check_is_a_no_op_under_remote_mode(
    exploding_urlopen: list[str],
) -> None:
    """Ollama under remote mode ⇒ no HTTP for the context check either."""
    warn_if_ollama_context_too_small(
        _make_config(llm_provider="ollama", remote_mode=True)
    )

    assert exploding_urlopen == []


def test_fetch_model_context_length_reads_arch_keyed_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_fetch_model_context_length`` reads the ``<arch>.context_length`` field."""
    _stub_show_endpoint(monkeypatch, {"qwen3.context_length": 32768})

    value = _fetch_model_context_length(_BASE_URL, _LARGE_MODEL, 3.0)

    assert value == 32768


def test_fetch_model_context_length_returns_none_without_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No matching field ⇒ ``None`` (so the caller stays quiet)."""
    _stub_show_endpoint(monkeypatch, {"general.architecture": "qwen3"})

    assert _fetch_model_context_length(_BASE_URL, _LARGE_MODEL, 3.0) is None
