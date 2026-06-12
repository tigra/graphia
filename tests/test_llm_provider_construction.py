"""Offline client-construction tests for spec 010 (Local Ollama Provider), Slice 3.

Where ``test_llm_provider_config.py`` stops at provider *resolution* (no client
is ever built there), this module goes one step further and exercises the
``get_large()`` / ``get_small()`` factories end-to-end through both concrete
providers — still strictly offline:

- constructing a ``ChatAnthropic`` or ``ChatBedrockConverse`` instance never
  performs a network call (verified: ``ChatBedrockConverse`` builds its boto3
  client lazily enough that no credentials are required at construction time,
  and ``ChatAnthropic`` only validates fields);
- no model is ever invoked, so the autouse ``safe_llm`` fixture is never
  tripped.

Every test resets the documented module-level seam (``_active_provider`` /
``_large`` / ``_small`` → ``None`` via monkeypatch) so the lazy resolution and
singleton caching are observed from a clean slate, and the developer's real
environment is neutralized the same way as in ``test_llm_provider_config.py``.

``ChatAnthropic`` assertion targets were confirmed by introspection of
langchain-anthropic 1.4.x: the constructor kwargs ``base_url`` / ``api_key``
are *aliases* for the fields ``anthropic_api_url`` / ``anthropic_api_key``;
``model``, ``temperature`` and ``max_tokens`` are the field names themselves.
"""

from __future__ import annotations

from typing import Callable

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel

import graphia.llm as llm

_PROVIDER_ENV_VARS = (
    "GRAPHIA_LLM_PROVIDER",
    "GRAPHIA_OLLAMA_BASE_URL",
    "GRAPHIA_OLLAMA_LARGE_MODEL",
    "GRAPHIA_OLLAMA_SMALL_MODEL",
    "GRAPHIA_REMOTE",
    "GRAPHIA_RUNTIME_URL",
)

# Credential-bearing variables that must be irrelevant on the ollama path.
_CREDENTIAL_ENV_VARS = (
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "ANTHROPIC_API_KEY",
)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_LARGE_MODEL = "qwen3-coder:30b"
_DEFAULT_SMALL_MODEL = "qwen2.5:3b"


@pytest.fixture(autouse=True)
def provider_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test in this module from a provider-neutral environment."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def reset_llm_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the documented module-level seam before (and after) each test.

    ``_active_provider`` / ``_large`` / ``_small`` are the lazy-resolution and
    singleton-cache slots; monkeypatching them to ``None`` both gives the test
    a clean slate and restores whatever was there on teardown, so this module
    can never leak a constructed client into other tests.
    """
    monkeypatch.setattr(llm, "_active_provider", None)
    monkeypatch.setattr(llm, "_large", None)
    monkeypatch.setattr(llm, "_small", None)


def _reset_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-reset the seam mid-test (e.g. after an env change)."""
    monkeypatch.setattr(llm, "_active_provider", None)
    monkeypatch.setattr(llm, "_large", None)
    monkeypatch.setattr(llm, "_small", None)


def _select_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")


# ---------------------------------------------------------------------------
# 1. Ollama path: factories build ChatAnthropic carrying config values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory, model_env, expected_model, expected_temperature",
    [
        pytest.param(
            llm.get_large,
            "GRAPHIA_OLLAMA_LARGE_MODEL",
            "llama3.1:70b",
            0.7,
            id="large",
        ),
        pytest.param(
            llm.get_small,
            "GRAPHIA_OLLAMA_SMALL_MODEL",
            "llama3.2:1b",
            0.8,
            id="small",
        ),
    ],
)
def test_ollama_factory_builds_chat_anthropic_from_custom_env(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], BaseChatModel],
    model_env: str,
    expected_model: str,
    expected_temperature: float,
) -> None:
    """Custom env values flow onto the constructed ``ChatAnthropic`` client."""
    _select_ollama(monkeypatch)
    monkeypatch.setenv("GRAPHIA_OLLAMA_BASE_URL", "http://gpu-box:11434")
    monkeypatch.setenv(model_env, expected_model)

    client = factory()

    assert isinstance(client, ChatAnthropic)
    assert client.anthropic_api_url == "http://gpu-box:11434"
    assert client.model == expected_model
    assert client.temperature == expected_temperature
    assert client.max_tokens == 1024
    assert client.anthropic_api_key.get_secret_value() == "ollama"


@pytest.mark.parametrize(
    "factory, expected_model",
    [
        pytest.param(llm.get_large, _DEFAULT_LARGE_MODEL, id="large"),
        pytest.param(llm.get_small, _DEFAULT_SMALL_MODEL, id="small"),
    ],
)
def test_ollama_factory_uses_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], BaseChatModel],
    expected_model: str,
) -> None:
    """With only the provider selected, clients carry the documented defaults."""
    _select_ollama(monkeypatch)

    client = factory()

    assert isinstance(client, ChatAnthropic)
    assert client.anthropic_api_url == _DEFAULT_BASE_URL
    assert client.model == expected_model


# ---------------------------------------------------------------------------
# 2. Bedrock path: factories still build ChatBedrockConverse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory, expected_model_id, expected_temperature",
    [
        pytest.param(llm.get_large, "amazon.nova-pro-v1:0", 0.7, id="large"),
        pytest.param(llm.get_small, "amazon.nova-lite-v1:0", 0.8, id="small"),
    ],
)
def test_bedrock_factory_still_builds_chat_bedrock_converse(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], BaseChatModel],
    expected_model_id: str,
    expected_temperature: float,
) -> None:
    """Default (bedrock) provider keeps producing ``ChatBedrockConverse``.

    Construction-only: ``ChatBedrockConverse.__init__`` builds a boto3 client
    object but performs no network call and requires no credentials, so this
    stays green in a CI-like environment with no AWS identity at all.
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    client = factory()

    assert isinstance(client, ChatBedrockConverse)
    assert client.model_id == expected_model_id
    assert client.temperature == expected_temperature
    assert client.region_name == llm.load_config().aws_region


# ---------------------------------------------------------------------------
# 3. Singleton caching and the seam-reset contract
# ---------------------------------------------------------------------------


def test_get_large_is_cached_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls return the very same object; the tiers are distinct objects."""
    _select_ollama(monkeypatch)

    first = llm.get_large()
    second = llm.get_large()

    assert first is second
    assert llm.get_small() is not first


def test_seam_reset_after_env_change_builds_fresh_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider/env changes take effect only through the documented seam reset.

    Without a reset the cached client survives an env flip (lazy resolution
    happened already); after resetting ``_active_provider``/``_large``/
    ``_small`` the factory re-resolves from config and builds a fresh client
    of the new provider's type.
    """
    _select_ollama(monkeypatch)
    monkeypatch.setenv("GRAPHIA_OLLAMA_BASE_URL", "http://first-box:11434")

    stale = llm.get_large()
    assert isinstance(stale, ChatAnthropic)
    assert stale.anthropic_api_url == "http://first-box:11434"

    # Env changes alone do not invalidate the cache...
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
    assert llm.get_large() is stale

    # ...but after the documented seam reset, a fresh client is built from
    # the new config.
    _reset_seam(monkeypatch)
    fresh = llm.get_large()

    assert fresh is not stale
    assert isinstance(fresh, ChatBedrockConverse)


# ---------------------------------------------------------------------------
# 4. Ollama path needs no credentials of any kind
# ---------------------------------------------------------------------------


def test_ollama_path_constructs_without_any_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No AWS identity or Anthropic key is needed to build the ollama clients."""
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _select_ollama(monkeypatch)

    large = llm.get_large()
    small = llm.get_small()

    assert isinstance(large, ChatAnthropic)
    assert isinstance(small, ChatAnthropic)
    assert large.anthropic_api_key.get_secret_value() == "ollama"
    assert small.anthropic_api_key.get_secret_value() == "ollama"
