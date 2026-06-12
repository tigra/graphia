"""Provider-selection config tests for spec 010 (Local Ollama Provider), Slice 2.

Covers the new ``GraphiaConfig`` surface introduced for LLM provider
selection:

- ``llm_provider`` (env ``GRAPHIA_LLM_PROVIDER``, default ``"bedrock"``,
  case/whitespace-normalized, empty string falls back to the default,
  anything outside {bedrock, ollama} is a ``SystemExit``);
- ``ollama_base_url`` / ``ollama_large_model`` / ``ollama_small_model``
  (envs ``GRAPHIA_OLLAMA_BASE_URL`` / ``GRAPHIA_OLLAMA_LARGE_MODEL`` /
  ``GRAPHIA_OLLAMA_SMALL_MODEL`` with documented defaults);
- the remote-mode contradiction guard (``GRAPHIA_REMOTE=1`` +
  ``GRAPHIA_LLM_PROVIDER=ollama`` must fail loudly *before* the
  missing-runtime-URL guard);
- ``graphia.llm._resolve_provider`` mapping: ``bedrock`` selects
  ``BedrockProvider``; ``ollama`` currently raises the temporary
  not-implemented ``SystemExit`` (the real provider lands in Slice 3);
- the offline gate (spec 010 follow-up): provider ``ollama`` forces the
  cloud-store config fields (``memory_id``, ``career_memory_id``,
  ``gateway_id``, ``gateway_url``, ``stats_strategy_id``) to ``None`` so a
  wire-env'd ``.env`` can't pull a local game onto cloud stores; ``bedrock``
  passes them through unchanged.

All tests are config-only and offline: no LLM client is ever constructed
(``BedrockProvider.large()/small()`` are never called), so the autouse
``safe_llm`` fixture is never tripped and no network is reached.

Following the ``test_config_auth.py`` convention, each test starts from the
developer's real environment and explicitly sets/deletes only the env vars
under test — the module-autouse ``provider_env_clean`` fixture wipes every
spec-010 variable (plus the remote-mode pair that interacts with them) so a
developer's ``.env`` leakage can't flip a branch.
"""

from __future__ import annotations

import pytest

from graphia.config import load_config

_PROVIDER_ENV_VARS = (
    "GRAPHIA_LLM_PROVIDER",
    "GRAPHIA_OLLAMA_BASE_URL",
    "GRAPHIA_OLLAMA_LARGE_MODEL",
    "GRAPHIA_OLLAMA_SMALL_MODEL",
    "GRAPHIA_REMOTE",
    "GRAPHIA_RUNTIME_URL",
)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_LARGE_MODEL = "qwen3-coder:30b"
_DEFAULT_SMALL_MODEL = "qwen2.5:3b"


@pytest.fixture(autouse=True)
def provider_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test in this module from a provider-neutral environment."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. Defaults
# ---------------------------------------------------------------------------


def test_defaults_select_bedrock_and_documented_ollama_fields() -> None:
    """Nothing set → provider is bedrock; ollama fields carry their defaults."""
    cfg = load_config()

    assert cfg.llm_provider == "bedrock"
    assert cfg.ollama_base_url == _DEFAULT_BASE_URL
    assert cfg.ollama_large_model == _DEFAULT_LARGE_MODEL
    assert cfg.ollama_small_model == _DEFAULT_SMALL_MODEL


# ---------------------------------------------------------------------------
# 2. Explicit ollama selection with custom field overrides
# ---------------------------------------------------------------------------


def test_ollama_provider_and_custom_fields_parse_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four spec-010 env vars flow verbatim onto the config."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GRAPHIA_OLLAMA_BASE_URL", "http://gpu-box:11434")
    monkeypatch.setenv("GRAPHIA_OLLAMA_LARGE_MODEL", "llama3.1:70b")
    monkeypatch.setenv("GRAPHIA_OLLAMA_SMALL_MODEL", "llama3.2:1b")

    cfg = load_config()

    assert cfg.llm_provider == "ollama"
    assert cfg.ollama_base_url == "http://gpu-box:11434"
    assert cfg.ollama_large_model == "llama3.1:70b"
    assert cfg.ollama_small_model == "llama3.2:1b"


# ---------------------------------------------------------------------------
# 3. Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param(" OLLAMA ", "ollama", id="upper_with_whitespace"),
        pytest.param("Ollama", "ollama", id="mixed_case"),
        pytest.param("\tbedrock\n", "bedrock", id="bedrock_whitespace"),
        pytest.param("BEDROCK", "bedrock", id="bedrock_upper"),
        pytest.param("", "bedrock", id="empty_falls_back_to_default"),
        pytest.param("   ", "bedrock", id="blank_falls_back_to_default"),
    ],
)
def test_provider_value_is_normalized(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    """Case and surrounding whitespace are stripped; empty means default."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", raw)

    assert load_config().llm_provider == expected


# ---------------------------------------------------------------------------
# 4. Invalid value
# ---------------------------------------------------------------------------


def test_invalid_provider_raises_naming_allowed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown provider is a SystemExit that names both allowed values."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "nope")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_LLM_PROVIDER" in message
    assert "'bedrock'" in message
    assert "'ollama'" in message
    assert "'nope'" in message


# ---------------------------------------------------------------------------
# 5. Remote-mode contradiction
# ---------------------------------------------------------------------------


def test_remote_mode_plus_ollama_is_a_contradiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRAPHIA_REMOTE=1`` + ollama must hit the contradiction guard.

    ``GRAPHIA_RUNTIME_URL`` is set so the missing-URL guard cannot be the
    one firing — the failure must be the local-only contradiction.
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.setenv("GRAPHIA_RUNTIME_URL", "https://example.invalid/runtime")
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_LLM_PROVIDER=ollama" in message
    assert "remote" in message.lower()
    # Must be the contradiction message, not the missing-runtime-URL one.
    assert "GRAPHIA_RUNTIME_URL is not set" not in message


def test_contradiction_guard_fires_before_missing_url_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With *both* problems present, the contradiction is reported first.

    Guard order in ``load_config`` puts the remote+ollama check ahead of the
    missing-URL check; the player should learn about the impossible
    combination before being told to wire up a runtime URL.
    """
    monkeypatch.setenv("GRAPHIA_REMOTE", "1")
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")
    # GRAPHIA_RUNTIME_URL deliberately absent (cleared by provider_env_clean).

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    assert "GRAPHIA_LLM_PROVIDER=ollama" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Provider resolution in graphia.llm (no client construction)
# ---------------------------------------------------------------------------


def test_resolve_provider_maps_bedrock_to_bedrock_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default config resolves to ``BedrockProvider``.

    Only the provider object is instantiated — ``large()``/``small()`` are
    never called, so no ``ChatBedrockConverse`` client (and no boto3
    session) is ever created.
    """
    import graphia.llm as llm

    monkeypatch.setattr(llm, "_active_provider", None)

    provider = llm._resolve_provider()

    assert isinstance(provider, llm.BedrockProvider)


def test_resolve_provider_ollama_resolves_to_ollama_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRAPHIA_LLM_PROVIDER=ollama resolves to OllamaProvider (Slice 3).

    Only the provider object is instantiated — ``large()``/``small()`` are
    never called, so no ``ChatAnthropic`` client is ever created and the
    test stays offline.
    """
    import graphia.llm as llm

    monkeypatch.setattr(llm, "_active_provider", None)
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")

    provider = llm._resolve_provider()

    assert isinstance(provider, llm.OllamaProvider)


# ---------------------------------------------------------------------------
# 7. Offline gate — ollama blanks the cloud-store ids (spec 010 follow-up)
# ---------------------------------------------------------------------------

_CLOUD_ID_ENV = {
    "GRAPHIA_MEMORY_ID": "mem-deadbeef",
    "GRAPHIA_CAREER_MEMORY_ID": "career-deadbeef",
    "GRAPHIA_GATEWAY_ID": "gw-deadbeef",
    "GRAPHIA_GATEWAY_URL": "https://example.invalid/mcp",
    "GRAPHIA_STATS_STRATEGY_ID": "strat-deadbeef",
}


def test_ollama_provider_blanks_all_cloud_store_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wire-env'd ``.env`` must not leak cloud stores into an Ollama game.

    Functional-spec 010 §2.2 requires an Ollama game to complete without
    reaching any cloud service; the diary/career factories gate on these ids
    alone, so the config gate must force all five to ``None``.
    """
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")
    for var, value in _CLOUD_ID_ENV.items():
        monkeypatch.setenv(var, value)

    cfg = load_config()

    assert cfg.memory_id is None
    assert cfg.career_memory_id is None
    assert cfg.gateway_id is None
    assert cfg.gateway_url is None
    assert cfg.stats_strategy_id is None


def test_bedrock_provider_passes_cloud_store_ids_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline gate is ollama-only — cloud play keeps its store ids."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
    for var, value in _CLOUD_ID_ENV.items():
        monkeypatch.setenv(var, value)

    cfg = load_config()

    assert cfg.memory_id == "mem-deadbeef"
    assert cfg.career_memory_id == "career-deadbeef"
    assert cfg.gateway_id == "gw-deadbeef"
    assert cfg.gateway_url == "https://example.invalid/mcp"
    assert cfg.stats_strategy_id == "strat-deadbeef"
