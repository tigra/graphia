"""LLM provider abstraction (large + small tiers) and structured-output schemas.

Two capability tiers, **named by size, not by model family**, so swapping the
underlying model never requires renaming call sites:

- ``get_large()`` — the heavier gameplay model (AI dialogue, votes, pointing).
- ``get_small()`` — the lighter mechanical model (roster name generation).

Per ADR-009 the tiers are served by an :class:`LLMProvider` — an abstract
construction strategy with two concrete implementations:
:class:`BedrockProvider` (Amazon Nova Pro / Lite per ADR-003) and
:class:`OllamaProvider` (a local Ollama server reached through its
Anthropic-compatible ``/v1/messages`` surface per ADR-010).
The active provider is chosen from config (``GRAPHIA_LLM_PROVIDER``) lazily,
on first factory use; ``_active_provider`` remains a module-level override
seam that bypasses config-driven selection when assigned directly.

Caching stays at module level (``_large`` / ``_small``): each tier's client is
built at most once, on first use, by whichever provider is active. Keeping the
cache slots here (rather than inside the provider) preserves the established
in-process override seam — ``graphia.tools.repetition_experiment`` rebuilds
``llm._large`` directly to vary temperature without source edits.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, field_validator, model_validator

from graphia.config import load_config

# Model ids are operational choices (ADR-003: Nova over Claude). The *tier*
# names above are the stable interface; these ids can change without touching
# any caller.
_LARGE_MODEL_ID = "amazon.nova-pro-v1:0"
_SMALL_MODEL_ID = "amazon.nova-lite-v1:0"

# Anthropic Messages requires an explicit max_tokens on every request. Graphia
# turns are short — one-to-two-sentence speeches, or a single structured tool
# call (Pointing / Ballot / DayAction / Roster) — so 1024 is generous headroom
# without inviting rambling completions from small local models.
_OLLAMA_MAX_TOKENS = 1024

# Ollama's Anthropic-compatible endpoint requires an api key to be present but
# ignores its value (per Ollama's docs, ``api_key='ollama'``).
_OLLAMA_DUMMY_API_KEY = "ollama"


class LLMProvider(ABC):
    """Construction strategy for the two tier clients.

    Implementations build a structured-output-capable LangChain chat model
    per tier. They construct fresh clients — singleton caching is owned by
    the module-level ``get_large`` / ``get_small`` factories, not by the
    provider.
    """

    @abstractmethod
    def large(self) -> BaseChatModel:
        """Build the heavier gameplay-tier chat model."""

    @abstractmethod
    def small(self) -> BaseChatModel:
        """Build the lighter mechanical-tier chat model."""


class BedrockProvider(LLMProvider):
    """Bedrock-backed provider: Amazon Nova Pro (large) / Nova Lite (small)."""

    def large(self) -> BaseChatModel:
        return ChatBedrockConverse(
            model=_LARGE_MODEL_ID,
            region_name=load_config().aws_region,
            temperature=0.7,
        )

    def small(self) -> BaseChatModel:
        return ChatBedrockConverse(
            model=_SMALL_MODEL_ID,
            region_name=load_config().aws_region,
            temperature=0.8,
        )


class OllamaProvider(LLMProvider):
    """Local-Ollama provider via the Anthropic-compatible API (ADR-010).

    Ollama exposes an Anthropic Messages surface rooted at the server base
    URL (clients call ``<base_url>/v1/messages``), so both tiers are plain
    :class:`~langchain_anthropic.ChatAnthropic` instances pointed at
    ``ollama_base_url`` with a dummy api key. Model names and the base URL
    come from config (``GRAPHIA_OLLAMA_*``); temperatures mirror the Bedrock
    tiers so gameplay tone is provider-independent. No AWS credentials are
    read anywhere on this path.
    """

    def large(self) -> BaseChatModel:
        config = load_config()
        return ChatAnthropic(
            model=config.ollama_large_model,
            base_url=config.ollama_base_url,
            api_key=_OLLAMA_DUMMY_API_KEY,
            temperature=0.7,
            max_tokens=_OLLAMA_MAX_TOKENS,
        )

    def small(self) -> BaseChatModel:
        config = load_config()
        return ChatAnthropic(
            model=config.ollama_small_model,
            base_url=config.ollama_base_url,
            api_key=_OLLAMA_DUMMY_API_KEY,
            temperature=0.8,
            max_tokens=_OLLAMA_MAX_TOKENS,
        )


# The active provider. ``None`` means "not resolved yet" — the factories
# resolve it from config on first use via :func:`_resolve_provider`. Tests
# and tools may assign a provider here directly to bypass config selection.
_active_provider: LLMProvider | None = None

_large: BaseChatModel | None = None
_small: BaseChatModel | None = None


def _resolve_provider() -> LLMProvider:
    """Return the active provider, selecting it from config on first use."""
    global _active_provider
    if _active_provider is None:
        match load_config().llm_provider:
            case "bedrock":
                _active_provider = BedrockProvider()
            case "ollama":
                _active_provider = OllamaProvider()
            case other:  # pragma: no cover — load_config validates the value
                raise SystemExit(f"Unknown LLM provider {other!r}.")
    return _active_provider


def get_large() -> BaseChatModel:
    global _large
    if _large is None:
        _large = _resolve_provider().large()
    return _large


def get_small() -> BaseChatModel:
    global _small
    if _small is None:
        _small = _resolve_provider().small()
    return _small


class Roster(BaseModel):
    names: list[str] = Field(min_length=6, max_length=6)

    @field_validator("names")
    @classmethod
    def _distinct_nonempty(cls, v: list[str]) -> list[str]:
        stripped = [n.strip() for n in v]
        if any(not n for n in stripped):
            raise ValueError("every name must be a non-empty string after strip")
        lowered = [n.lower() for n in stripped]
        if len(set(lowered)) != len(lowered):
            raise ValueError("names must be distinct (case-insensitive)")
        return stripped


class Pointing(BaseModel):
    target_id: str = Field(min_length=1)


class Ballot(BaseModel):
    """A single Yes/No ballot cast during a vote-to-execute.

    Kept deliberately flat: Bedrock Converse tool-use schemas behave best with
    a single top-level primitive field. The boolean ``yes`` is the only signal.
    """

    yes: bool


class DayAction(BaseModel):
    """Flat schema for a Day-phase action.

    Bedrock Converse is finicky about discriminated unions, so we keep
    ``kind`` + ``text`` + ``target_id`` all at the top level and enforce
    the mutual-exclusion invariant via a model validator.
    """

    kind: Literal["speak", "vote"]
    text: str | None = None
    target_id: str | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> "DayAction":
        if self.kind == "speak":
            if self.text is None or not self.text.strip():
                raise ValueError("speak requires non-empty text")
        else:  # kind == "vote"
            if self.target_id is None or not self.target_id.strip():
                raise ValueError("vote requires non-empty target_id")
        return self
