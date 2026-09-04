"""Offline client-construction tests for the LLM provider boundary.

Where ``test_llm_provider_config.py`` stops at provider *resolution* (no client
is ever built there), this module goes one step further and exercises the
``get_large()`` / ``get_small()`` / ``get_persona_model()`` factories
end-to-end through all three concrete providers — still strictly offline:

- constructing a ``ChatOllama`` or ``ChatBedrockConverse`` instance never
  performs a network call (see the ``validate_model_on_init`` pin below for
  why that holds on the local path, and
  ``test_bedrock_factory_still_builds_chat_bedrock_converse`` for why it holds
  on the cloud one);
- no model is ever invoked, so the autouse ``safe_llm`` fixture is never
  tripped.

Every test resets the documented module-level seam (``_active_provider`` /
``_large`` / ``_small`` → ``None`` via monkeypatch) so the lazy resolution and
singleton caching are observed from a clean slate, and the developer's real
environment is neutralized the same way as in ``test_llm_provider_config.py``.

**What this module asserts, and what it deliberately does not.** The thing that
has actually regressed here before is *config failing to reach the client*, so
every assertion below is about a value flowing from ``GRAPHIA_*`` env → config →
a constructed client field: ``model``, ``base_url``, ``temperature``,
``num_ctx``, ``num_predict``. It does **not** assert ``keep_alive``,
``client_kwargs``, ``reasoning`` or the class names of a bound runnable's
steps — those mirror the implementation and would make this a change-detector
rather than a contract. (Structured-output *method* pinning is a separate
concern with its own module.)

Assertion targets were confirmed by introspection of **langchain-ollama
1.1.0** (the version Task 2.1 of spec 041 resolved): ``model``, ``base_url``,
``temperature``, ``num_predict``, ``num_ctx`` and ``validate_model_on_init``
are all first-class ``ChatOllama`` fields readable straight off the instance.
Two absences matter as much as those presences: ``ChatOllama`` has **no**
``max_tokens`` field at all (``num_predict`` is the native equivalent), and it
has no api-key concept — the native ``/api/chat`` surface takes neither, which
is why ADR-013's swap away from the Anthropic-compatible ``/v1/messages`` route
deleted the dummy-key assertions this module used to carry along with them.
"""

from __future__ import annotations

from typing import Callable

import pytest
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

import graphia.llm as llm

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

# Spec 035 — documented Claude Haiku 4.5 Bedrock default id (verify-at-runtime).
_DEFAULT_CLAUDE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# AWS-credential-bearing variables that must be irrelevant on the ollama path —
# and, per the Bedrock tests below, at Bedrock *construction* time too. There is
# no vendor-key variable to strip any more: the native Ollama surface has no
# api key (ADR-013), so "no credentials" now means exactly "no AWS identity".
_CREDENTIAL_ENV_VARS = (
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_LARGE_MODEL = "qwen3-coder:30b"
_DEFAULT_SMALL_MODEL = "qwen2.5:3b"
# Documented default per-request context length (spec 041 §2.2), sent as the
# native request's ``options.num_ctx``.
_DEFAULT_NUM_CTX = 32768
# Documented per-request output cap, sent as ``options.num_predict``. Pinned as
# a literal rather than imported from ``llm``: the value is cited by
# ``config._DEFAULT_CONTEXT_TOKEN_BUDGET``'s arithmetic, so moving it should
# cost a deliberate test edit (and a re-run of that derivation), not pass
# silently. See ``test_llm_provider_config.py`` for the citation guard itself.
_NUM_PREDICT = 1024
# A context length that is nobody's default, so a client carrying it proves the
# value was *threaded from config* rather than hard-coded at construction.
_CUSTOM_NUM_CTX = 8192


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
# 1. Ollama path: factories build ChatOllama carrying config values
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
def test_ollama_factory_builds_chat_ollama_from_custom_env(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], BaseChatModel],
    model_env: str,
    expected_model: str,
    expected_temperature: float,
) -> None:
    """Custom env values flow onto the constructed ``ChatOllama`` client.

    The four-way check that config *reaches the transport*: the tier model and
    the server root come from ``GRAPHIA_OLLAMA_*``, the context length from
    ``GRAPHIA_OLLAMA_NUM_CTX`` (set to a value that is nobody's default, so a
    hard-coded 32768 would fail here), and the output cap from the renamed
    ``llm._OLLAMA_NUM_PREDICT``. The temperatures are the shared
    ``_LARGE_TEMPERATURE`` / ``_SMALL_TEMPERATURE`` all three providers now
    read — same 0.7 / 0.8 the local tiers hard-coded before spec 041, so this
    is also the parity check on that rename.
    """
    _select_ollama(monkeypatch)
    monkeypatch.setenv("GRAPHIA_OLLAMA_BASE_URL", "http://gpu-box:11434")
    monkeypatch.setenv(model_env, expected_model)
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", str(_CUSTOM_NUM_CTX))

    client = factory()

    assert isinstance(client, ChatOllama)
    assert client.model == expected_model
    assert client.base_url == "http://gpu-box:11434"
    assert client.temperature == expected_temperature
    assert client.num_ctx == _CUSTOM_NUM_CTX
    assert client.num_predict == _NUM_PREDICT


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
    """With only the provider selected, clients carry the documented defaults.

    ``num_ctx`` is included because the default is the figure
    ``config._DEFAULT_CONTEXT_TOKEN_BUDGET``'s arithmetic is derived from: a
    request that silently omitted it would inherit Ollama's own small default
    and quietly undeliver the fuller multi-day window.
    """
    _select_ollama(monkeypatch)

    client = factory()

    assert isinstance(client, ChatOllama)
    assert client.model == expected_model
    assert client.base_url == _DEFAULT_BASE_URL
    assert client.num_ctx == _DEFAULT_NUM_CTX
    assert client.num_predict == _NUM_PREDICT


def test_ollama_persona_model_varies_only_the_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_persona_model`` moves the sampling and nothing else (spec 034).

    The third ``ChatOllama`` construction site — reached through
    ``LLMProvider.large_at_temperature`` — and the one most easily forgotten
    when a request option is added: it must carry the same large-tier model,
    server root, context length and output cap as ``get_large()``, differing
    only in ``temperature``. It is deliberately uncached, so it must also not
    be (or replace) the gameplay singleton.
    """
    _select_ollama(monkeypatch)
    monkeypatch.setenv("GRAPHIA_OLLAMA_BASE_URL", "http://gpu-box:11434")
    monkeypatch.setenv("GRAPHIA_OLLAMA_NUM_CTX", str(_CUSTOM_NUM_CTX))

    gameplay = llm.get_large()
    persona = llm.get_persona_model(1.0)

    assert isinstance(persona, ChatOllama)
    assert persona is not gameplay
    assert persona.temperature == 1.0
    assert gameplay.temperature == 0.7
    assert persona.model == _DEFAULT_LARGE_MODEL
    assert persona.base_url == "http://gpu-box:11434"
    assert persona.num_ctx == _CUSTOM_NUM_CTX
    assert persona.num_predict == _NUM_PREDICT
    # The uncached persona client did not displace the gameplay singleton.
    assert llm.get_large() is gameplay


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(llm.get_large, id="large"),
        pytest.param(llm.get_small, id="small"),
        pytest.param(lambda: llm.get_persona_model(1.0), id="persona"),
    ],
)
def test_chat_ollama_construction_performs_no_model_validation(
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[], BaseChatModel],
) -> None:
    """Every locally-constructed client keeps model validation off.

    **This test is load-bearing, not decorative.** This module's docstring
    promises that construction touches no network, and on the native Ollama
    client that promise rests entirely on one library default:
    ``validate_model_on_init`` is ``False``, so ``__init__`` does not call
    ``/api/show`` to check the model exists. Flip it True — in
    ``OllamaProvider``, or upstream in a langchain-ollama release — and the
    whole mocked suite starts issuing HTTP to ``localhost:11434`` at
    construction, which on a machine with no server is a connection error and
    on one with a server is a real request from a test. Pinned here so that
    failure arrives as a red assertion in three named tests rather than as a
    suite that hangs or flakes by environment.

    (Confirmed against langchain-ollama 1.1.0:
    ``ChatOllama.model_fields["validate_model_on_init"].default is False``.
    Graphia does not pass the flag, so it inherits that default — which is why
    the assertion is on the constructed client, not on the field default.)
    """
    _select_ollama(monkeypatch)

    client = build()

    assert isinstance(client, ChatOllama)
    assert client.validate_model_on_init is False


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

    The cheapest possible cloud-parity check at this layer: spec 041 swapped
    the *local* client, and the two Bedrock providers must be untouched — same
    class, same per-tier Nova id, same region, and the same 0.7 / 0.8 now that
    the temperature constants are shared with the local tiers rather than
    Bedrock-only. (The fuller parity proof — that no structured-output method
    leaks onto this path — belongs to its own module.)

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
# 2b. bedrock-claude path: factories build ChatBedrockConverse on Claude Haiku
#     (spec 035) — still construction-only, no live call.
# ---------------------------------------------------------------------------


def _select_bedrock_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")


@pytest.mark.parametrize(
    "factory, expected_temperature",
    [
        pytest.param(llm.get_large, 0.7, id="large"),
        pytest.param(llm.get_small, 0.8, id="small"),
    ],
)
def test_bedrock_claude_factory_builds_chat_bedrock_converse_on_haiku(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], BaseChatModel],
    expected_temperature: float,
) -> None:
    """bedrock-claude builds a ``ChatBedrockConverse`` on the Claude Haiku id.

    Asserts the constructed model id / region / temperature at the boundary —
    no model is invoked, so ``safe_llm`` is never tripped and no boto3 call is
    made (``ChatBedrockConverse.__init__`` builds its client lazily, requiring
    no credentials). Both tiers default to the same Haiku id; the temperatures
    match the Nova tiers (gameplay tone is provider-independent).
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _select_bedrock_claude(monkeypatch)

    client = factory()

    assert isinstance(client, ChatBedrockConverse)
    assert client.model_id == _DEFAULT_CLAUDE_MODEL
    assert client.temperature == expected_temperature
    assert client.region_name == llm.load_config().aws_region


def test_bedrock_claude_honors_per_tier_overrides_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-tier override env vars flow onto the constructed Claude clients."""
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _select_bedrock_claude(monkeypatch)
    monkeypatch.setenv("GRAPHIA_LARGE_MODEL", "us.anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("GRAPHIA_SMALL_MODEL", "us.anthropic.claude-opus-4-8")

    large = llm.get_large()
    small = llm.get_small()

    assert isinstance(large, ChatBedrockConverse)
    assert isinstance(small, ChatBedrockConverse)
    assert large.model_id == "us.anthropic.claude-sonnet-4-6"
    assert small.model_id == "us.anthropic.claude-opus-4-8"


def test_switching_among_three_providers_resolves_the_right_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each of the three providers builds its own client type after a seam reset.

    Exercises the mutual-exclusivity of provider selection end-to-end: a single
    env flip + seam reset re-resolves to the correct concrete client, with no
    cross-contamination. All construction-only — no model is invoked.
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    # bedrock (Nova) → ChatBedrockConverse on the Nova id.
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock")
    _reset_seam(monkeypatch)
    nova = llm.get_large()
    assert isinstance(nova, ChatBedrockConverse)
    assert nova.model_id == "amazon.nova-pro-v1:0"

    # bedrock-claude → ChatBedrockConverse on the Claude Haiku id.
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")
    _reset_seam(monkeypatch)
    claude = llm.get_large()
    assert isinstance(claude, ChatBedrockConverse)
    assert claude.model_id == _DEFAULT_CLAUDE_MODEL

    # ollama → ChatOllama.
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "ollama")
    _reset_seam(monkeypatch)
    ollama = llm.get_large()
    assert isinstance(ollama, ChatOllama)

    # Distinct instances per provider — no stale singleton leaked across flips.
    assert nova is not claude is not ollama


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
    assert isinstance(stale, ChatOllama)
    assert stale.base_url == "http://first-box:11434"

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
    """No AWS identity is needed to build the ollama clients — nor any key.

    Both tiers construct with every AWS credential variable stripped, and land
    pointed at the local server root. There is nothing left to assert about a
    vendor key here, and that absence *is* the finding: the native
    ``/api/chat`` surface has no api-key concept, so the dummy-key plumbing
    this test used to pin (and assert the value of) no longer exists.
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _select_ollama(monkeypatch)

    large = llm.get_large()
    small = llm.get_small()

    assert isinstance(large, ChatOllama)
    assert isinstance(small, ChatOllama)
    assert large.base_url == _DEFAULT_BASE_URL
    assert small.base_url == _DEFAULT_BASE_URL
