"""Auth-posture tests for ``graphia.config.load_config``.

Spec 002, §2.1: ``load_config()`` must accept any of three auth postures
(bearer-only, profile-only, both-set) and must raise on the one
contradictory combination (remote mode requested with no runtime URL).

Pre-existing harness detail: ``tests/conftest.py::env`` and other fixtures
sometimes set ``AWS_BEARER_TOKEN_BEDROCK=dummy`` — but those fixtures are
*not* autouse. The autouse fixture in this file is ``safe_llm`` (LLM call
sites only), which does not touch env vars. So each test here starts from
the developer's real environment and explicitly sets/deletes only the env
vars under test, leaving everything else alone.

Even so, ``AWS_BEARER_TOKEN_BEDROCK`` may already be present in the
developer's shell (it is in ``.env`` for local Bedrock use). The
``profile_only`` scenario therefore explicitly ``delenv``-s it to exercise
the genuine no-bearer branch rather than silently falling through to the
bearer-set path.
"""

from __future__ import annotations

import pytest

from graphia.config import load_config


@pytest.mark.parametrize(
    "bearer, profile, remote, runtime_url",
    [
        pytest.param("xyz", None, None, None, id="bearer_only"),
        pytest.param(None, "fake-profile", None, None, id="profile_only"),
        pytest.param("xyz", "fake-profile", None, None, id="both_set"),
        pytest.param(
            None, None, "1", "https://example.invalid/runtime", id="remote_with_url"
        ),
    ],
)
def test_load_config_accepts_valid_auth_postures(
    monkeypatch: pytest.MonkeyPatch,
    bearer: str | None,
    profile: str | None,
    remote: str | None,
    runtime_url: str | None,
) -> None:
    """The four non-contradictory auth postures should all load cleanly.

    Each posture sets only the env vars it cares about and deletes any
    leftover values for the others so the scenario actually exercises the
    intended branch of ``load_config``.
    """
    if bearer is None:
        monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    else:
        monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", bearer)

    if profile is None:
        monkeypatch.delenv("AWS_PROFILE", raising=False)
    else:
        monkeypatch.setenv("AWS_PROFILE", profile)

    if remote is None:
        monkeypatch.delenv("GRAPHIA_REMOTE", raising=False)
    else:
        monkeypatch.setenv("GRAPHIA_REMOTE", remote)

    if runtime_url is None:
        monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
    else:
        monkeypatch.setenv("GRAPHIA_RUNTIME_URL", runtime_url)

    cfg = load_config()

    # Bearer presence on the resulting config mirrors the env state.
    assert cfg.bearer_token == bearer

    # Remote mode is a boolean projection of GRAPHIA_REMOTE.
    expected_remote = remote is not None
    assert cfg.remote_mode is expected_remote
    assert cfg.runtime_invocation_url == runtime_url


def test_load_config_remote_without_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario (e): ``GRAPHIA_REMOTE=1`` with no ``GRAPHIA_RUNTIME_URL``.

    Must raise ``SystemExit`` whose message names the offending env var(s)
    and points the developer at the ``terraform output`` fix so they know
    what to do next.
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
    # Leave the rest of the env alone — the contradiction must be detected
    # regardless of bearer/profile state.

    with pytest.raises(SystemExit, match=r"GRAPHIA_RUNTIME_URL"):
        load_config()

    # Re-raise to inspect the message for the actionable hint. ``match``
    # only checks one substring; the spec requires the error to also point
    # at the terraform-output fix, so assert that separately.
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.delenv("GRAPHIA_RUNTIME_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_REMOTE" in message
    assert "GRAPHIA_RUNTIME_URL" in message
    assert "terraform output" in message
