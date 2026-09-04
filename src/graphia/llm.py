"""LLM provider abstraction (large + small tiers) and structured-output schemas.

Two capability tiers, **named by size, not by model family**, so swapping the
underlying model never requires renaming call sites:

- ``get_large()`` — the heavier gameplay model (AI dialogue, votes, pointing).
- ``get_small()`` — the lighter mechanical model (roster name generation).

Per ADR-009 the tiers are served by an :class:`LLMProvider` — an abstract
construction strategy with three concrete implementations:
:class:`BedrockProvider` (Amazon Nova Pro / Lite per ADR-003, the default
baseline), :class:`ClaudeBedrockProvider` (Claude Haiku 4.5 on Bedrock per
ADR-012 / spec 035), and :class:`OllamaProvider` (a local Ollama server
reached through its NATIVE ``/api/chat`` surface per ADR-013, which revised
ADR-010's Anthropic-compatible ``/v1/messages`` route after its
verify-at-implementation gate failed — see :class:`OllamaProvider`). The two
Bedrock providers share one ``ChatBedrockConverse``
construction helper, parameterised only by the per-tier model id resolved
from config — so the Claude profile is an additive config choice that leaves
Nova's observable behavior byte-identical.
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

from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field, field_validator, model_validator

from graphia.config import _MAX_TABLE_SIZE, load_config

# Largest AI-name list the roster schema accepts: one fewer than the maximum
# table size (the human occupies the remaining seat). Exact-count enforcement
# for a given lineup lives in the caller (``setup._generate_names`` +
# ``_coerce_to_count``); this is only the schema-level ceiling.
_MAX_AI_NAMES = _MAX_TABLE_SIZE - 1

# Model ids are operational choices and, as of spec 035, **config-driven**:
# the per-tier id each Bedrock provider builds is read from
# ``config.large_model`` / ``config.small_model`` (env
# ``GRAPHIA_LARGE_MODEL`` / ``GRAPHIA_SMALL_MODEL``), defaulting per provider
# (Nova ids under ``bedrock``, Claude Haiku 4.5 under ``bedrock-claude`` —
# see ``config.py``). These module-level aliases remain the *Nova* default
# ids (unchanged values) so the Nova-only eval harnesses
# (``tools.repetition_experiment``, ``tools.blunder_eval``) keep a stable
# fingerprint to reference; they are no longer read on the gameplay
# construction path.
_LARGE_MODEL_ID = "amazon.nova-pro-v1:0"
_SMALL_MODEL_ID = "amazon.nova-lite-v1:0"

# The embeddings model for the spec-033 semantic persona-similarity metric. This
# is a *measurement instrument*, NOT a gameplay tier: it is ALWAYS Bedrock,
# independent of ``GRAPHIA_LLM_PROVIDER``, so the metric is a consistent
# measuring stick comparable across ollama and bedrock gameplay runs (see
# ``get_embeddings``). Amazon Titan Text Embeddings v2 — the AWS-native,
# same-stack-as-Nova choice. The exact model id and its region availability are
# verified at LIVE run only (the offline test suite mocks ``get_embeddings`` and
# never reaches Bedrock — see ``tests/conftest.py``'s ``safe_llm``).
_EMBEDDINGS_MODEL_ID = "amazon.titan-embed-text-v2:0"

# Per-request output cap for the local tiers, sent as the native request's
# ``options.num_predict``. Graphia turns are short — a one-to-two-sentence
# speech, or a single structured answer (Roster / Persona / Pointing / Ballot /
# DayAction / Reflection / Diary) — so 1024 is generous headroom without
# inviting rambling completions from small local models.
#
# **Renamed from ``_OLLAMA_MAX_TOKENS``, never deleted** (ADR-013 / spec 041
# §2.2), for two independent reasons:
#
# 1. ``ChatOllama`` has no ``max_tokens`` field at all; the native equivalent
#    is ``num_predict``. The old name would have described nothing.
# 2. The constant is **load-bearing by citation**: ``config.py`` derives
#    ``_DEFAULT_CONTEXT_TOKEN_BUDGET`` by reserving this value as the
#    completion budget out of the assumed 32768-token context. Deleting the
#    name would leave that arithmetic unreconstructable, so the citation moves
#    with the rename.
#
# The value is unchanged at 1024, but its *role* shifted with the transport.
# Anthropic Messages **required** an explicit cap on every request; native
# ``/api/chat`` does not, and omitting it would fall back to Ollama's own
# ``num_predict`` default of **128** — which would truncate essentially every
# diary entry (spec 039 measured real entries at 155–693 tokens). So passing
# it explicitly is what keeps long answers intact, and it doubles as a runaway
# guard: grammar-constrained decoding pins the JSON *shape*, not termination —
# a schema-valid string can be arbitrarily long.
_OLLAMA_NUM_PREDICT = 1024


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
    def large_at_temperature(self, temperature: float) -> BaseChatModel:
        """Build a fresh large-tier client at an explicit ``temperature``.

        Spec 034: persona generation runs hotter than gameplay. The same
        large-tier MODEL, only a different sampling temperature — so behavioural
        variation stays "prompts + temperature, not more models" (architecture
        §4). Returns a FRESH instance (never the cached gameplay singleton) so
        the gameplay temperature is untouched.
        """

    @abstractmethod
    def small(self) -> BaseChatModel:
        """Build the lighter mechanical-tier chat model."""


# Per-tier temperatures, read by ALL THREE providers (Nova, Claude, Ollama) —
# gameplay tone is provider-independent; only the model behind it changes.
#
# **Renamed from ``_BEDROCK_LARGE_TEMPERATURE`` / ``_BEDROCK_SMALL_TEMPERATURE``
# (spec 041 §2.2).** The old names' own comment claimed the constants existed
# "so the two Bedrock providers can't drift apart" — while ``OllamaProvider``
# sat directly below them hard-coding 0.7 / 0.8 as literals. The drift the
# constants were meant to prevent was therefore already possible, hidden by a
# Bedrock-flavoured name that read as though the Ollama path were out of their
# scope. Dropping the prefix and having the local tiers read them too makes
# the original claim true for all three.
#
# **Same literals, so the observable behaviour delta is exactly zero** — and
# that is deliberate. A real temperature move is a *gameplay* change: it would
# need its own default-on ``GRAPHIA_*`` flag with a flag-off parity test
# (ADR-011) and a measured A/B, neither of which a transport swap carries.
_LARGE_TEMPERATURE = 0.7
_SMALL_TEMPERATURE = 0.8


def _build_bedrock(model: str, temperature: float) -> BaseChatModel:
    """Construct a ``ChatBedrockConverse`` for a Bedrock-backed tier.

    The single Bedrock construction seam shared by :class:`BedrockProvider`
    (Amazon Nova) and :class:`ClaudeBedrockProvider` (Claude Haiku 4.5), so a
    new Bedrock profile is a different ``model`` id, not a forked construction
    path. ``region_name`` is always ``config.aws_region`` (us-east-1 by
    default) — identical to the call the Nova path made before spec 035, so
    Nova's observable construction is byte-for-byte unchanged.
    """
    return ChatBedrockConverse(
        model=model,
        region_name=load_config().aws_region,
        temperature=temperature,
    )


class BedrockProvider(LLMProvider):
    """Bedrock-backed provider: Amazon Nova Pro (large) / Nova Lite (small).

    The default, untouched baseline (ADR-003). Model ids come from config
    (``large_model`` / ``small_model``), which default to the Nova pair under
    ``GRAPHIA_LLM_PROVIDER=bedrock`` — so behavior is identical to the
    pre-spec-035 hardcoded ids.
    """

    def large(self) -> BaseChatModel:
        return _build_bedrock(
            load_config().large_model, _LARGE_TEMPERATURE
        )

    def large_at_temperature(self, temperature: float) -> BaseChatModel:
        # Config-driven large id (honors GRAPHIA_LARGE_MODEL), via the shared
        # _build_bedrock seam — a fresh instance at the override temperature.
        # ChatBedrockConverse accepts a per-instance temperature (same arg
        # large/small already pass).
        return _build_bedrock(load_config().large_model, temperature)

    def small(self) -> BaseChatModel:
        return _build_bedrock(
            load_config().small_model, _SMALL_TEMPERATURE
        )


class ClaudeBedrockProvider(LLMProvider):
    """Bedrock-backed provider: Claude Haiku 4.5 for both tiers (ADR-012 / spec 035).

    Structurally identical to :class:`BedrockProvider` — same
    ``_build_bedrock`` seam, same per-tier temperatures, same
    ``config.aws_region`` — differing only in the resolved per-tier model id,
    which defaults to the ``us.``-prefixed Claude Haiku 4.5 inference profile
    under ``GRAPHIA_LLM_PROVIDER=bedrock-claude`` (each tier independently
    overridable via ``GRAPHIA_LARGE_MODEL`` / ``GRAPHIA_SMALL_MODEL``). The
    Claude Haiku Bedrock id / inference profile is **verify-at-runtime** (see
    ``config._DEFAULT_CLAUDE_LARGE_MODEL``); the offline suite asserts the
    constructed id/region at the boundary and never reaches Bedrock.
    """

    def large(self) -> BaseChatModel:
        return _build_bedrock(
            load_config().large_model, _LARGE_TEMPERATURE
        )

    def large_at_temperature(self, temperature: float) -> BaseChatModel:
        # Config-driven Claude large id, via the shared _build_bedrock seam
        # (the spec-034 persona-diversity path resolves a hotter instance here).
        return _build_bedrock(load_config().large_model, temperature)

    def small(self) -> BaseChatModel:
        return _build_bedrock(
            load_config().small_model, _SMALL_TEMPERATURE
        )


class OllamaProvider(LLMProvider):
    """Local-Ollama provider via the NATIVE Ollama API (ADR-013).

    Both tiers are :class:`~langchain_ollama.ChatOllama` instances pointed at
    ``ollama_base_url`` — the plain server root (``http://localhost:11434`` by
    default), which the native client resolves to ``/api/chat``. That is the
    same root ``graphia.preflight`` already probes for ``/api/tags`` /
    ``/api/show``, so one configured URL now serves the whole local path. No
    api key exists on this surface, and no AWS credentials are read anywhere
    on it.

    **Why the native surface, not ADR-010's Anthropic-compatible one.**
    Structured output over ``/v1/messages`` was Anthropic *tool use*, which
    that endpoint treats as a request it may decline. Spec 039 measured the
    cost: 45 of 90 diary entries were silently replaced by a deterministic
    placeholder while the model's own complete, in-voice prose sat unused in
    an ``end_turn`` reply — and ``tool_choice`` turned out to be accepted and
    ignored, so it could not be forced. Native ``/api/chat`` instead takes a
    JSON-schema ``format`` and performs **grammar-constrained decoding**: the
    sampler is masked at each step to tokens that can continue a schema-valid
    document, so a shape-invalid answer is *unrepresentable* rather than
    merely discouraged. That is a stronger guarantee than Bedrock Converse's
    ``toolConfig``, which asks the model to use a tool and trusts it to comply.

    **The tier swap alone is what delivers that** — no per-call argument, and
    none of the ``with_structured_output`` call sites change (ADR-009's
    abstraction absorbing a client swap is that abstraction working as
    designed). ``ChatOllama.with_structured_output`` defaults to
    ``method="json_schema"``, re-confirmed against the pinned
    langchain-ollama 1.1.0: the bound runnable carries
    ``format == Schema.model_json_schema()`` and no ``tools``. Spec 041's
    Slice 3 pins that method explicitly at the provider boundary, which is
    **defensive, not corrective** — immunity to a third-party default flip,
    plus proof the method cannot leak to ``ChatBedrockConverse`` (whose own
    default is ``function_calling``).

    Note what the grammar does NOT cover: it constrains JSON *shape*, never
    *content*. ``DayAction``'s mutual-exclusion rule, ``Roster``'s
    case-insensitive distinctness and ``Pointing``'s "names a living player"
    all live in Pydantic validators that JSON Schema cannot express, so those
    three keep a live substitution path on this transport too.

    Model names and the base URL come from config (``GRAPHIA_OLLAMA_*``);
    temperatures are the shared :data:`_LARGE_TEMPERATURE` /
    :data:`_SMALL_TEMPERATURE` both Bedrock providers read, so gameplay tone
    stays provider-independent (identical 0.7 / 0.8 to the literals this class
    hard-coded before the rename — the swap moves the transport, not the
    sampling).

    Construction stays fully OFFLINE: ``validate_model_on_init`` defaults to
    ``False``, so nothing here attempts HTTP. That default is what lets the
    mocked suite construct these clients with no server running — do not set
    it True.
    """

    def large(self) -> BaseChatModel:
        config = load_config()
        return ChatOllama(
            model=config.ollama_large_model,
            base_url=config.ollama_base_url,
            temperature=_LARGE_TEMPERATURE,
            num_predict=_OLLAMA_NUM_PREDICT,
        )

    def large_at_temperature(self, temperature: float) -> BaseChatModel:
        # ``ChatOllama`` accepts a per-instance ``temperature`` — the same arg
        # ``large`` / ``small`` pass, forwarded into the native request's
        # ``options`` alongside ``num_predict``. Fresh instance, hotter
        # sampling; the cached gameplay client keeps its own temperature (see
        # ``get_persona_model``).
        config = load_config()
        return ChatOllama(
            model=config.ollama_large_model,
            base_url=config.ollama_base_url,
            temperature=temperature,
            num_predict=_OLLAMA_NUM_PREDICT,
        )

    def small(self) -> BaseChatModel:
        config = load_config()
        return ChatOllama(
            model=config.ollama_small_model,
            base_url=config.ollama_base_url,
            temperature=_SMALL_TEMPERATURE,
            num_predict=_OLLAMA_NUM_PREDICT,
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
            case "bedrock-claude":
                _active_provider = ClaudeBedrockProvider()
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


def get_persona_model(temperature: float) -> BaseChatModel:
    """Build a large-tier chat model at a persona-generation ``temperature`` (spec 034).

    Persona generation (spec 016/031) used the cached gameplay ``get_large()``;
    spec 034 runs it HOTTER for more creative latitude. This routes through the
    active provider (``_resolve_provider`` — so a test override on
    ``_active_provider`` is honoured) and builds a FRESH client at ``temperature``
    via :meth:`LLMProvider.large_at_temperature`.

    Deliberately NOT module-cached: the gameplay ``_large`` singleton stays at its
    0.7 temperature, and persona generation is a one-time setup cost (a handful of
    calls), so a fresh instance per persona-gen pass is cheap and keeps the two
    temperatures cleanly separated. The same large-tier MODEL — variation comes
    from temperature + prompts, never a third model (architecture §4).
    """
    return _resolve_provider().large_at_temperature(temperature)


def get_embeddings() -> Embeddings:
    """Build the Bedrock embeddings client for the semantic persona metric (spec 033).

    Mirrors :func:`get_large` / :func:`get_small`'s ``ChatBedrockConverse``
    construction (``region_name=load_config().aws_region``), but returns a
    :class:`~langchain_aws.BedrockEmbeddings` over Amazon Titan Text Embeddings
    v2 (:data:`_EMBEDDINGS_MODEL_ID`). Its ``embed_documents(texts)`` is the
    batch API the scorer calls once per game.

    **Always Bedrock**, deliberately independent of the active
    ``GRAPHIA_LLM_PROVIDER`` (it does NOT route through ``_resolve_provider``):
    the metric's *instrument* is fixed so the number stays comparable across
    ollama and bedrock gameplay runs — a consistent measuring stick, not
    confounded by the gameplay model. This is the deliberate cross-provider
    dependency the spec-033 ADR records (a measured run now needs AWS creds +
    a small embedding cost to produce this metric; ``run_eval`` omits the metric
    gracefully when this client or the embed call is unavailable).

    Unlike ``get_large``/``get_small`` this is NOT module-cached — it is built on
    demand by the eval harness only, never on a hot gameplay path. The exact
    model id and region availability are confirmed at live run; the offline test
    suite patches this factory (``tests/conftest.py``'s ``safe_llm``) and never
    reaches real Bedrock.
    """
    return BedrockEmbeddings(
        model_id=_EMBEDDINGS_MODEL_ID,
        region_name=load_config().aws_region,
    )


# ---------------------------------------------------------------------------
# The gameplay structured-output schemas.
#
# **Why they are flat, and why that is a Bedrock fact rather than a structured-
# output fact.** Every schema below keeps primitive fields at the top level and
# pushes any cross-field rule into a Pydantic validator. The reason is
# **Bedrock Converse** specifically: it is finicky about nested and
# discriminated shapes in a tool-use schema. It is NOT a property of structured
# output in general — the Ollama path (ADR-013) derives a decoding grammar
# straight from ``model_json_schema()``, and a grammar handles nesting and
# unions perfectly well. The individual docstrings below used to read as though
# the constraint came with structured output itself; that attribution was wrong
# and is corrected here and in each of them.
#
# **The schemas nevertheless stay SHARED across all three providers**, flatness
# and all, rather than being specialised per provider. Two reasons. Provider
# difference would leak into game logic: a per-provider schema means the node
# that reads a ``DayAction`` has to know which transport produced it, which is
# precisely the coupling ADR-009's provider seam exists to prevent — the seam's
# whole value is that a client swap changes nothing above it. And the flat
# shape costs the local path nothing measurable, so the only thing a split
# would buy is two shapes to keep in step.
#
# **What flatness moves, rather than removes.** A rule JSON Schema cannot
# express lives in a validator, and a grammar cannot enforce a validator — it
# constrains the JSON *shape*, never the *content*. Verified:
# ``DayAction.model_json_schema()["required"] == ["kind"]``, so ``{"kind":
# "vote"}`` with no target is grammar-valid and model-invalid. ``Roster``'s
# case-insensitive distinctness is validator-only too, and ``Pointing`` can
# return a well-formed id naming nobody. Those three therefore keep a live
# substitution path on EVERY provider, reached by content rejection rather than
# by unreadable output — which bounds what ADR-013's "unrepresentable" language
# can promise.
# ---------------------------------------------------------------------------


class Roster(BaseModel):
    names: list[str] = Field(min_length=1, max_length=_MAX_AI_NAMES)

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


class Persona(BaseModel):
    """A generated character persona for one AI player.

    Kept deliberately flat (primitive string fields only) — the same
    **Bedrock Converse** constraint that shapes ``Roster``/``Ballot``/
    ``DayAction``: nested or discriminated shapes are rejected *by that
    transport*, not by structured output as such (the Ollama grammar would
    accept them; see the flatness note above ``Roster``, and the reason the
    schema stays shared anyway). ``public_backstory`` is the
    cover the character presents to the table; ``secret_backstory`` is a
    Mafioso's true self and is left empty for Citizens.
    """

    personality: str
    manner: str
    public_backstory: str
    secret_backstory: str = ""


class Pointing(BaseModel):
    target_id: str = Field(min_length=1)


class Ballot(BaseModel):
    """A single Yes/No ballot cast during a vote-to-execute.

    Kept deliberately flat: **Bedrock Converse** tool-use schemas behave best
    with a single top-level primitive field. That is a constraint of that one
    transport rather than of structured output — see the flatness note above
    ``Roster`` for why the schema is still shared across providers. The boolean
    ``yes`` is the only signal.
    """

    yes: bool


class Reflection(BaseModel):
    """A single AI player's private end-of-Day-round reflection (spec 028).

    Kept deliberately flat with one primitive field — the same **Bedrock
    Converse** constraint that shapes ``Roster`` / ``Pointing`` / ``Ballot`` /
    ``DayAction`` (no nested or discriminated shapes). That constraint belongs
    to that transport, not to structured output in general; the schema stays
    shared across providers regardless — see the flatness note above
    ``Roster``. ``thought`` is the short private note
    (one or two sentences) the player writes for itself, seen by no other
    player. The reflection node accepts a non-empty ``thought`` and otherwise
    falls back to a deterministic placeholder so a model hiccup never blanks the
    channel and tests stay non-flaky.
    """

    thought: str


class Diary(BaseModel):
    """A single AI player's private end-of-Day diary entry (spec 039).

    Kept deliberately flat with one primitive field — the same **Bedrock
    Converse** constraint that shapes ``Roster`` / ``Pointing`` / ``Ballot`` /
    ``DayAction`` / ``Reflection`` (no nested or discriminated shapes). That
    constraint belongs to that transport, not to structured output in general;
    the schema stays shared across providers regardless — see the flatness note
    above ``Roster``. ``entry`` is the
    once-per-day-cycle private note a surviving AI player writes just before
    Night: a summing-up of the whole day, deliberately LONGER than spec 028's
    per-round ``Reflection`` (which reacts to a single round), and seen by no
    other player and never by the human.

    **Three names, three things** — the collision is deliberate to record here
    because it is easy to conflate:

    - ``Diary`` (this class) — the structured-output schema the model fills in.
    - ``graphia.state.DiaryRecord`` — the ``GameState`` channel entry (the text
      plus its ``day`` and cross-channel interleave cursor).
    - ``graphia.diary_store.DiaryEntry`` — the persistence DTO the AgentCore
      Memory-backed store reads and writes. **Unchanged by spec 039.**

    The diary node accepts a non-empty ``entry`` and otherwise falls back to a
    deterministic placeholder, so a model hiccup never blanks the channel and
    the tests stay non-flaky (the posture ``Reflection`` already documents).
    """

    entry: str


class DayAction(BaseModel):
    """Flat schema for a Day-phase action.

    **Bedrock Converse** is finicky about discriminated unions, so we keep
    ``kind`` + ``text`` + ``target_id`` all at the top level and enforce
    the mutual-exclusion invariant via a model validator. The finickiness is
    that transport's, not structured output's — Ollama's JSON-schema grammar
    (ADR-013) would take a discriminated union — and the schema stays shared
    across providers anyway; see the flatness note above ``Roster``.

    This class is also the clearest case of what a grammar cannot do. Verified:
    ``model_json_schema()["required"] == ["kind"]``, because JSON Schema cannot
    express the mutual exclusion the validator enforces. So ``{"kind": "vote"}``
    with no ``target_id`` is grammar-valid and model-invalid, and this schema
    keeps a live substitution path on every provider.
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
