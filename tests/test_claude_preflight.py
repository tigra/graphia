"""Offline unit tests for the Claude (Bedrock) boot preflight (spec 035, Slice 2).

Covers ``graphia.preflight.run_claude_preflight`` and its error-mapping helper:

- **Error-family mapping** — the single probe seam (``_probe_claude_access``) is
  monkeypatched to raise a representative member of each boto3/Bedrock error
  family (a faked ``ClientError`` carrying the family's error *code*, plus a
  credential-resolution error matched by class *name*). Each maps to a distinct
  plain-language, actionable ``SystemExit`` — and the exit is a *string* code
  (message-to-stderr, exit 1, NO stack trace), matching functional-spec 035
  §2.5's no-stack-trace requirement. The underlying boto3 error is chained for
  log forensics.
- **Happy path** — a probe that returns cleanly ⇒ ``run_claude_preflight``
  returns ``None``.
- **No-op guards** — provider ``bedrock`` (Nova) / ``ollama``, or
  ``bedrock-claude`` under remote mode, must never invoke the probe (the seam
  explodes if reached).
- **Model id in the message** — access / region errors name the *configured*
  model id (incl. an override), not a hardcoded default.

Everything is stubbed and offline: the probe seam is faked, so no boto3 client
is ever built and real Bedrock is never reached — the autouse ``safe_llm``
fixture is irrelevant here (the preflight doesn't go through the LLM factories),
and the probe fake guarantees no AWS call.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from graphia.config import GraphiaConfig
from graphia import preflight
from graphia.preflight import run_claude_preflight

_DEFAULT_CLAUDE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Config factory and error stubs
# ---------------------------------------------------------------------------


def _make_config(
    *,
    llm_provider: str = "bedrock-claude",
    remote_mode: bool = False,
    aws_region: str = _REGION,
    large_model: str = _DEFAULT_CLAUDE_MODEL,
    small_model: str = _DEFAULT_CLAUDE_MODEL,
) -> GraphiaConfig:
    """Build a ``GraphiaConfig`` directly, bypassing env (frozen dataclass)."""
    return GraphiaConfig(
        bearer_token=None,
        aws_region=aws_region,
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
        large_model=large_model,
        small_model=small_model,
    )


def _client_error(code: str, message: str = "boom") -> ClientError:
    """Build a faithful botocore ``ClientError`` carrying a chosen error code."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}}, "Converse"
    )


class _FakeExpiredSSOTokenError(Exception):
    """Stand-in for a botocore credential-resolution error (raised pre-request).

    The mapping classifies these by class *name*, so the name must match one of
    the recognised credential-resolution exception names.
    """


# Name it exactly so the class-name match fires.
_FakeExpiredSSOTokenError.__name__ = "UnauthorizedSSOTokenError"


def _stub_probe(monkeypatch: pytest.MonkeyPatch, exc: Exception | None) -> list:
    """Replace the probe seam: raise ``exc`` (or return cleanly if ``None``).

    Returns a call log so tests can assert the probe ran exactly once (or, on
    the no-op paths, not at all).
    """
    calls: list = []

    def _fake_probe(config: GraphiaConfig) -> None:
        calls.append(config)
        if exc is not None:
            raise exc

    monkeypatch.setattr(preflight, "_probe_claude_access", _fake_probe)
    return calls


@pytest.fixture
def exploding_probe(monkeypatch: pytest.MonkeyPatch) -> list:
    """Any probe attempt is a test failure; returns the (must-stay-empty) log."""
    attempts: list = []

    def _boom(config: GraphiaConfig) -> None:
        attempts.append(config)
        raise AssertionError(
            "preflight probed Bedrock for a config it must no-op on"
        )

    monkeypatch.setattr(preflight, "_probe_claude_access", _boom)
    return attempts


# ---------------------------------------------------------------------------
# 1. Error-family mapping (faked client, no stack trace)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_code, expected_substrings",
    [
        pytest.param(
            "UnrecognizedClientException",
            ["credentials", "expired", "relaunch"],
            id="unrecognized_client_to_credentials",
        ),
        pytest.param(
            "ExpiredTokenException",
            ["credentials", "expired"],
            id="expired_token_to_credentials",
        ),
        pytest.param(
            "AccessDeniedException",
            ["not allowed to invoke", "Model access", _DEFAULT_CLAUDE_MODEL],
            id="access_denied_to_model_access",
        ),
        pytest.param(
            "ValidationException",
            ["rejected", "inference profile", _DEFAULT_CLAUDE_MODEL],
            id="validation_to_region_or_model",
        ),
        pytest.param(
            "ResourceNotFoundException",
            ["rejected", _DEFAULT_CLAUDE_MODEL],
            id="resource_not_found_to_region_or_model",
        ),
    ],
)
def test_error_family_maps_to_plain_message(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    expected_substrings: list[str],
) -> None:
    """Each representative Bedrock error code maps to its actionable message."""
    _stub_probe(monkeypatch, _client_error(error_code))

    with pytest.raises(SystemExit) as exc_info:
        run_claude_preflight(_make_config())

    message = str(exc_info.value)
    for fragment in expected_substrings:
        assert fragment in message


def test_credential_resolution_error_maps_to_credentials_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-request credential-resolution error (expired SSO) → the creds fix.

    These are raised before any HTTP call (so they carry no ``response`` and are
    not ``ClientError``s) — classified by exception class name instead.
    """
    _stub_probe(monkeypatch, _FakeExpiredSSOTokenError("sso token expired"))

    with pytest.raises(SystemExit) as exc_info:
        run_claude_preflight(_make_config())

    message = str(exc_info.value)
    assert "credentials" in message
    assert "expired" in message
    assert "relaunch" in message


def test_unknown_error_maps_to_generic_plain_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unclassified boto3 error still yields a plain (no-traceback) message."""
    _stub_probe(monkeypatch, _client_error("ThrottlingException", "slow down"))

    with pytest.raises(SystemExit) as exc_info:
        run_claude_preflight(_make_config())

    message = str(exc_info.value)
    assert _DEFAULT_CLAUDE_MODEL in message
    assert _REGION in message
    # The generic branch surfaces the underlying detail but stays plain prose.
    assert "Couldn't reach" in message


def test_exit_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SystemExit with a string code: message to stderr, exit 1, no traceback.

    And the underlying boto3 error is chained (``from exc``) for log forensics —
    but it is the string-coded SystemExit, not the ClientError, that propagates.
    """
    underlying = _client_error("AccessDeniedException")
    _stub_probe(monkeypatch, underlying)

    with pytest.raises(SystemExit) as exc_info:
        run_claude_preflight(_make_config())

    assert isinstance(exc_info.value.code, str)
    assert exc_info.value.code  # non-empty → exit status 1, not 0
    assert exc_info.value.__cause__ is underlying


def test_access_message_names_the_overridden_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message points at the *configured* (possibly overridden) model id."""
    _stub_probe(monkeypatch, _client_error("AccessDeniedException"))
    overridden = "us.anthropic.claude-sonnet-4-6"

    with pytest.raises(SystemExit) as exc_info:
        run_claude_preflight(_make_config(large_model=overridden))

    assert overridden in str(exc_info.value)


def test_region_message_names_the_configured_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-default region is reflected in the region/model error message."""
    _stub_probe(monkeypatch, _client_error("ValidationException"))

    with pytest.raises(SystemExit) as exc_info:
        run_claude_preflight(_make_config(aws_region="eu-west-1"))

    assert "eu-west-1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Happy path
# ---------------------------------------------------------------------------


def test_clean_probe_passes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that returns cleanly ⇒ the preflight returns None after one probe."""
    calls = _stub_probe(monkeypatch, None)

    assert run_claude_preflight(_make_config()) is None
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 3. No-op guards: never probe Bedrock for Nova / Ollama / remote play
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param("bedrock", id="nova_is_a_no_op"),
        pytest.param("ollama", id="ollama_is_a_no_op"),
    ],
)
def test_other_providers_are_a_no_op(
    exploding_probe: list, provider: str
) -> None:
    """Default Nova and Ollama providers ⇒ preflight returns without probing."""
    run_claude_preflight(_make_config(llm_provider=provider))

    assert exploding_probe == []


def test_bedrock_claude_under_remote_mode_is_a_no_op(
    exploding_probe: list,
) -> None:
    """Remote play skips the local Claude preflight even with provider selected.

    The deployed runtime authenticates via its own role (proven by the Slice 3
    deployed spike), so the local boot preflight must not probe.
    """
    run_claude_preflight(
        _make_config(llm_provider="bedrock-claude", remote_mode=True)
    )

    assert exploding_probe == []
