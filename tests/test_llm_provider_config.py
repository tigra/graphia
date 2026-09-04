"""Provider-selection config tests for the LLM provider surface.

Covers the ``GraphiaConfig`` fields that decide which model tiers a game runs
on, and how they are reached:

- ``llm_provider`` (env ``GRAPHIA_LLM_PROVIDER``, default ``"bedrock"``,
  case/whitespace-normalized, empty string falls back to the default,
  anything outside {bedrock, bedrock-claude, ollama} is a ``SystemExit``);
- ``large_model`` / ``small_model`` (envs ``GRAPHIA_LARGE_MODEL`` /
  ``GRAPHIA_SMALL_MODEL``), whose documented defaults track the selected
  Bedrock profile (spec 035);
- ``ollama_base_url`` / ``ollama_large_model`` / ``ollama_small_model`` /
  ``ollama_num_ctx`` (envs ``GRAPHIA_OLLAMA_BASE_URL`` /
  ``GRAPHIA_OLLAMA_LARGE_MODEL`` / ``GRAPHIA_OLLAMA_SMALL_MODEL`` /
  ``GRAPHIA_OLLAMA_NUM_CTX`` with documented defaults);
- the remote-mode contradiction guard (``GRAPHIA_REMOTE=1`` +
  ``GRAPHIA_LLM_PROVIDER=ollama`` must fail loudly *before* the
  missing-runtime-URL guard);
- ``graphia.llm._resolve_provider`` mapping: ``bedrock`` →
  ``BedrockProvider``, ``bedrock-claude`` → ``ClaudeBedrockProvider``,
  ``ollama`` → ``OllamaProvider``;
- the offline gate (spec 010 follow-up): provider ``ollama`` forces the
  cloud-store config fields (``memory_id``, ``career_memory_id``,
  ``gateway_id``, ``gateway_url``, ``stats_strategy_id``) to ``None`` so a
  wire-env'd ``.env`` can't pull a local game onto cloud stores; ``bedrock``
  passes them through unchanged.

All tests are config-only and offline: no LLM client is ever constructed
(``BedrockProvider.large()/small()`` and the ``OllamaProvider`` equivalents are
never called), so the autouse ``safe_llm`` fixture is never tripped and no
network is reached. Client construction — what these values actually flow
*into* — is asserted in ``test_llm_provider_construction.py``.

Following the ``test_config_auth.py`` convention, each test starts from the
developer's real environment and explicitly sets/deletes only the env vars
under test — the module-autouse ``provider_env_clean`` fixture wipes every
spec-010 variable (plus the remote-mode pair that interacts with them) so a
developer's ``.env`` leakage can't flip a branch.
"""

from __future__ import annotations

import pytest

from graphia.config import _DEFAULT_OLLAMA_NUM_CTX, load_config

_PROVIDER_ENV_VARS = (
    "GRAPHIA_LLM_PROVIDER",
    "GRAPHIA_LARGE_MODEL",
    "GRAPHIA_SMALL_MODEL",
    "GRAPHIA_OLLAMA_BASE_URL",
    "GRAPHIA_OLLAMA_LARGE_MODEL",
    "GRAPHIA_OLLAMA_SMALL_MODEL",
    "GRAPHIA_OLLAMA_NUM_CTX",
    "GRAPHIA_REMOTE",
    "GRAPHIA_RUNTIME_URL",
)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_LARGE_MODEL = "qwen3-coder:30b"
_DEFAULT_SMALL_MODEL = "qwen2.5:3b"
# Spec 041 §2.2 — documented per-request context length for the local tiers.
_DEFAULT_NUM_CTX = 32768

# Spec 035 — documented per-tier Bedrock model-id defaults.
_DEFAULT_NOVA_LARGE = "amazon.nova-pro-v1:0"
_DEFAULT_NOVA_SMALL = "amazon.nova-lite-v1:0"
_DEFAULT_CLAUDE_LARGE = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_DEFAULT_CLAUDE_SMALL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.fixture(autouse=True)
def provider_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test in this module from a provider-neutral environment."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. Defaults
# ---------------------------------------------------------------------------


def test_defaults_select_bedrock_and_documented_ollama_fields() -> None:
    """Nothing set → provider is bedrock; ollama + Bedrock fields carry defaults.

    Spec 035: the default ``bedrock`` provider's per-tier ids must be the Nova
    pair — byte-identical to the ids ``llm.py`` previously hardcoded — so the
    baseline is untouched.
    """
    cfg = load_config()

    assert cfg.llm_provider == "bedrock"
    assert cfg.ollama_base_url == _DEFAULT_BASE_URL
    assert cfg.ollama_large_model == _DEFAULT_LARGE_MODEL
    assert cfg.ollama_small_model == _DEFAULT_SMALL_MODEL
    assert cfg.ollama_num_ctx == _DEFAULT_NUM_CTX
    # Spec 035 — Bedrock per-tier defaults under the default provider are Nova.
    assert cfg.large_model == _DEFAULT_NOVA_LARGE
    assert cfg.small_model == _DEFAULT_NOVA_SMALL


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
        # Spec 035 — the new third provider normalizes the same way.
        pytest.param(
            " Bedrock-Claude ", "bedrock-claude", id="claude_mixed_case_ws"
        ),
        pytest.param("BEDROCK-CLAUDE", "bedrock-claude", id="claude_upper"),
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
    """An unknown provider is a SystemExit naming all three allowed values."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "nope")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_LLM_PROVIDER" in message
    assert "'bedrock'" in message
    assert "'bedrock-claude'" in message
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
    """GRAPHIA_LLM_PROVIDER=ollama resolves to OllamaProvider.

    Only the provider object is instantiated — ``large()``/``small()`` are
    never called, so no ``ChatOllama`` client is ever created and the test
    stays offline.
    """
    import graphia.llm as llm

    monkeypatch.setattr(llm, "_active_provider", None)
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")

    provider = llm._resolve_provider()

    assert isinstance(provider, llm.OllamaProvider)


def test_resolve_provider_bedrock_claude_resolves_to_claude_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRAPHIA_LLM_PROVIDER=bedrock-claude resolves to ClaudeBedrockProvider (spec 035).

    Only the provider object is instantiated — ``large()``/``small()`` are
    never called, so no ``ChatBedrockConverse`` client (and no boto3 session)
    is ever created and the test stays offline.
    """
    import graphia.llm as llm

    monkeypatch.setattr(llm, "_active_provider", None)
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")

    provider = llm._resolve_provider()

    assert isinstance(provider, llm.ClaudeBedrockProvider)


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


# ---------------------------------------------------------------------------
# 8. Spec 035 — bedrock-claude per-tier model-id defaults and overrides
# ---------------------------------------------------------------------------


def test_bedrock_claude_resolves_documented_haiku_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting bedrock-claude with no override → both tiers default to Haiku 4.5.

    The exact ``us.``-prefixed id is ADR-012's verify-at-runtime selection; the
    config layer only needs to resolve the documented default constant.
    """
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")

    cfg = load_config()

    assert cfg.llm_provider == "bedrock-claude"
    assert cfg.large_model == _DEFAULT_CLAUDE_LARGE
    assert cfg.small_model == _DEFAULT_CLAUDE_SMALL


def test_per_tier_overrides_are_honored_under_bedrock_claude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRAPHIA_LARGE_MODEL / GRAPHIA_SMALL_MODEL independently override each tier."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")
    monkeypatch.setenv("GRAPHIA_LARGE_MODEL", "us.anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("GRAPHIA_SMALL_MODEL", "us.anthropic.claude-opus-4-8")

    cfg = load_config()

    assert cfg.large_model == "us.anthropic.claude-sonnet-4-6"
    assert cfg.small_model == "us.anthropic.claude-opus-4-8"


def test_one_tier_override_leaves_the_other_at_its_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overriding only the large tier keeps the small tier on the Haiku default."""
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")
    monkeypatch.setenv("GRAPHIA_LARGE_MODEL", "us.anthropic.claude-sonnet-4-6")

    cfg = load_config()

    assert cfg.large_model == "us.anthropic.claude-sonnet-4-6"
    assert cfg.small_model == _DEFAULT_CLAUDE_SMALL


def test_overrides_apply_to_the_nova_bedrock_provider_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-tier override env vars are provider-agnostic on the Bedrock paths.

    Under the default ``bedrock`` provider an operator can still pin a specific
    Nova-family id — confirming the generalisation didn't special-case Claude.
    """
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("GRAPHIA_LARGE_MODEL", "amazon.nova-premier-v1:0")

    cfg = load_config()

    assert cfg.large_model == "amazon.nova-premier-v1:0"
    # Untouched tier keeps the Nova default.
    assert cfg.small_model == _DEFAULT_NOVA_SMALL


def test_bedrock_claude_keeps_cloud_store_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bedrock-claude is a CLOUD provider — the ollama-only offline gate must not fire.

    Only ``ollama`` blanks the cloud-store ids; a Claude game runs on the cloud
    (locally or on the hosted runtime), so its wire-env'd ids must pass through
    exactly as they do for Nova.
    """
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")
    for var, value in _CLOUD_ID_ENV.items():
        monkeypatch.setenv(var, value)

    cfg = load_config()

    assert cfg.memory_id == "mem-deadbeef"
    assert cfg.career_memory_id == "career-deadbeef"
    assert cfg.gateway_id == "gw-deadbeef"
    assert cfg.gateway_url == "https://example.invalid/mcp"
    assert cfg.stats_strategy_id == "strat-deadbeef"


# ---------------------------------------------------------------------------
# 9. Spec 041 — the local per-request context length (``GRAPHIA_OLLAMA_NUM_CTX``)
# ---------------------------------------------------------------------------


def test_ollama_num_ctx_defaults_to_the_documented_context_length() -> None:
    """Unset → the documented 32768, and that default is the module constant.

    Asserted against both the literal and ``_DEFAULT_OLLAMA_NUM_CTX`` on
    purpose: the literal is what the ``_DEFAULT_CONTEXT_TOKEN_BUDGET``
    derivation was computed from, and the constant is what
    ``OllamaProvider`` actually sends — a drift between the two is exactly
    the thing that would leave the arithmetic quietly wrong.
    """
    cfg = load_config()

    assert cfg.ollama_num_ctx == _DEFAULT_NUM_CTX
    assert cfg.ollama_num_ctx == _DEFAULT_OLLAMA_NUM_CTX


def test_ollama_num_ctx_env_override_parses_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A memory-tight box can lower the context; the value parses to an int.

    ``8192`` is nobody's default, so this distinguishes a threaded setting
    from a hard-coded one, and ``is not`` a string checks the ``_parse_count``
    conversion rather than just the plumbing.
    """
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", "8192")

    num_ctx = load_config().ollama_num_ctx

    assert num_ctx == 8192
    assert isinstance(num_ctx, int)


def test_ollama_num_ctx_tolerates_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_parse_count`` strips, so a padded ``.env`` value still parses."""
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", "  16384\n")

    assert load_config().ollama_num_ctx == 16384


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
    ],
)
def test_ollama_num_ctx_blank_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """An empty or blank value means "unset", not zero — the sibling-count idiom."""
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", raw)

    assert load_config().ollama_num_ctx == _DEFAULT_NUM_CTX


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
        pytest.param("-32768", id="negative_large"),
    ],
)
def test_ollama_num_ctx_below_one_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """A non-positive context can hold no prompt at all, so it fails fast.

    Same posture as ``GRAPHIA_MAX_DAYS`` / ``GRAPHIA_CONTEXT_WINDOW``: the
    message names the variable and echoes the offending value, so the operator
    can fix their ``.env`` without reading the source.
    """
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", raw)

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_OLLAMA_NUM_CTX" in message
    assert raw in message


def test_ollama_num_ctx_non_numeric_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-numeric value is refused by ``_parse_count`` with the name shown."""
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", "lots")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_OLLAMA_NUM_CTX" in message
    assert "lots" in message


def test_the_context_token_budget_derivation_inputs_are_intact() -> None:
    """The two numbers ``_DEFAULT_CONTEXT_TOKEN_BUDGET`` is derived from.

    ``config._DEFAULT_CONTEXT_TOKEN_BUDGET``'s comment spells out
    ``(32768 − ~1.5K scaffold − ~1K completion) × 0.75 ≈ 22700 → 20000``,
    where the 32768 is ``_DEFAULT_OLLAMA_NUM_CTX`` and the ~1K completion is
    ``llm._OLLAMA_NUM_PREDICT`` — the constant spec 041 **renamed rather than
    deleted** precisely so this arithmetic stays reconstructable (``ChatOllama``
    has no ``max_tokens`` field, so its old name described nothing).

    Pinned here because a derivation whose inputs can move silently is not a
    derivation. Either number changing should force a deliberate edit of this
    test and a re-run of that arithmetic — not a stale comment. Deliberately
    the constants themselves, not a recomputation of the budget: re-deriving
    ``20000`` in a test would only restate the source's own formula.
    """
    import graphia.llm as llm
    from graphia.config import _DEFAULT_CONTEXT_TOKEN_BUDGET

    assert _DEFAULT_OLLAMA_NUM_CTX == 32768
    assert llm._OLLAMA_NUM_PREDICT == 1024
    assert _DEFAULT_CONTEXT_TOKEN_BUDGET == 20000
