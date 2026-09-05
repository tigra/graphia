"""Offline tests for the structured-output *method* seam (spec 041 Slice 3).

What this module is about
-------------------------
``graphia.llm.StructuredMethodModel`` is the provider-boundary proxy that makes
every ``with_structured_output`` call carry the active provider's
structured-output defaults — ``{"method": "json_schema"}`` on the local Ollama
path (ADR-013's grammar-constrained decoding), ``{}`` on both Bedrock
providers. This module pins that proxy and the three concrete
``LLMProvider.large`` / ``large_at_temperature`` / ``small`` template methods
that install it.

Two layers, deliberately:

1. **A recording stub in place of the vendor client** (``_RecordingModel``,
   installed over the provider's ``_build_*`` hooks) answers "*what did the
   wrapper ask for?*" — the exact schema and the exact kwargs, per provider,
   per tier.
2. **A real, offline ``ChatOllama``** answers the question that actually
   matters: "*did asking for it change the wire?*" A recording stub would
   happily accept ``method="json_schema"`` and prove nothing about decoding, so
   the assertions carrying the weight read the bound runnable's grammar off a
   genuine client — ``runnable.first.kwargs["format"] ==
   Schema.model_json_schema()`` for every schema in
   :data:`~graphia.llm.GAMEPLAY_SCHEMAS`, with ``method="function_calling"`` as
   the **negative control** so the positive assertion cannot be vacuous.

What the Bedrock half of this module is, and what it is not
-----------------------------------------------------------
``test_bedrock_wrapper_requests_no_method`` is the **offline expression of the
functional spec's cloud-parity requirement**: reliable decoding on the local
path must not leak into the cloud providers, whose own
``ChatBedrockConverse`` default (``function_calling``) is what the cloud path
wants. Nothing more is claimed for it. In particular it does **not** catch a
call site hard-coding ``method="json_schema"`` — this module never touches a
call site, so such a change would sail straight past every test here. The
module that drives the real call sites is
``tests/test_provider_seam_through_the_node.py`` (spec 041 Task 3.4).

Anti-vacuity rules this module follows
--------------------------------------
- **The schema vocabulary is derived from ``llm.GAMEPLAY_SCHEMAS``, never
  retyped.** That tuple exists precisely because a hand-typed schema list was
  short twice over, and on 2026-09-04 let ADR-013's live gate return
  ``RELIABLE`` with exit code 0 on a game whose ``Diary`` failure rate was 0.80
  (spec 041 §11.1). A test that re-lists the schemas would rebuild the same
  hazard one level down, so every sweep below is parametrized straight off the
  tuple.
- **``ls_structured_output_format`` is never asserted.** It is LangChain's own
  telemetry key; pinning it would mirror the vendor rather than the contract.
  Its presence is the reason the assertions below read the ``format`` key
  specifically instead of comparing the whole ``kwargs`` dict.
- **No vendor parser class is named.** The negative control asserts the two
  methods land on *different* parser classes rather than naming
  ``PydanticOutputParser`` / ``PydanticToolsParser``, so a vendor rename stays
  a non-event while a collapse of the two paths stays a failure.

Strictly offline, and nothing here trips ``safe_llm``: ``ChatOllama``
construction performs no HTTP (``validate_model_on_init`` defaults to
``False`` — pinned in ``tests/test_llm_provider_construction.py``), no Bedrock
client is constructed at all (the recording stub replaces the ``_build_*``
hooks), and no model is ever invoked except the recording stub's own runnable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest
from langchain_ollama import ChatOllama

from graphia.llm import (
    GAMEPLAY_SCHEMAS,
    Ballot,
    BedrockProvider,
    ClaudeBedrockProvider,
    Diary,
    LLMProvider,
    OllamaProvider,
    StructuredMethodModel,
)
from graphia.tools.instrument import InstrumentedModel, SchemaStats

# Env vars that decide which client the local provider builds. Wiped so the
# real-``ChatOllama`` tests below construct from documented defaults rather than
# from whatever the developer's ``.env`` happens to point at. (Construction is
# offline either way; this is about the tests being reproducible, not safe.)
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

# The provider's structured-output defaults, stated as literals on purpose:
# deriving the expectation from ``structured_output_defaults()`` would assert
# that the code equals itself. These two dicts ARE the contract — the local path
# pins the method, the cloud path requests nothing.
_OLLAMA_DEFAULTS = {"method": "json_schema"}
_BEDROCK_DEFAULTS: dict[str, Any] = {}

_BEDROCK_PROVIDERS = (BedrockProvider, ClaudeBedrockProvider)

# The three public tier methods, all of which are concrete template methods that
# must install the wrapper (see ``LLMProvider``'s docstring on why a provider
# cannot opt out). Swept as a unit so a tier that forgot to wrap — or that a
# future refactor moves off the template — fails here.
_TIERS: dict[str, Callable[[LLMProvider], Any]] = {
    "large": lambda p: p.large(),
    "large_at_temperature": lambda p: p.large_at_temperature(0.95),
    "small": lambda p: p.small(),
}

# Every parametrization over schemas is fed by ``GAMEPLAY_SCHEMAS`` itself.
_schema_sweep = pytest.mark.parametrize(
    "schema", GAMEPLAY_SCHEMAS, ids=[s.__name__ for s in GAMEPLAY_SCHEMAS]
)
_tier_sweep = pytest.mark.parametrize("tier", sorted(_TIERS))
_bedrock_sweep = pytest.mark.parametrize(
    "provider_cls", _BEDROCK_PROVIDERS, ids=[c.__name__ for c in _BEDROCK_PROVIDERS]
)
_all_providers_sweep = pytest.mark.parametrize(
    "provider_cls",
    (*_BEDROCK_PROVIDERS, OllamaProvider),
    ids=[c.__name__ for c in (*_BEDROCK_PROVIDERS, OllamaProvider)],
)


@pytest.fixture(autouse=True)
def provider_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a provider-neutral environment."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# The recording stub: what did the wrapper ask the vendor client for?
# ---------------------------------------------------------------------------


@dataclass
class _Binding:
    """One recorded ``with_structured_output`` call."""

    schema: Any
    kwargs: dict[str, Any]


class _RecordedRunnable:
    """Stand-in for a bound runnable; returns whatever the stub was given."""

    def __init__(self, schema: Any, result: Any) -> None:
        self.schema = schema
        self._result = result

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self._result


@dataclass
class _RecordingModel:
    """A vendor-client stand-in that records every schema binding.

    Duck-typed rather than a ``BaseChatModel`` subclass, matching what
    :class:`~graphia.llm.StructuredMethodModel` actually requires of the object
    it wraps (the one-method ``StructuredModel`` capability) — and matching
    ``InstrumentedModel``, which subclasses nothing either.

    ``inner`` is deliberately present as a plain attribute so the passthrough
    tests can prove the proxy's own ``inner`` property is never shadowed by a
    vendor client that happens to carry that name.
    """

    bindings: list[_Binding] = field(default_factory=list)
    result: Any = None
    vendor_marker: str = "from-the-vendor-client"
    inner: str = "not-the-proxys-inner"

    def with_structured_output(self, schema: Any, **kwargs: Any) -> _RecordedRunnable:
        self.bindings.append(_Binding(schema=schema, kwargs=dict(kwargs)))
        return _RecordedRunnable(schema, self.result)

    def vendor_method(self, value: int) -> int:
        return value * 2

    @property
    def last_kwargs(self) -> dict[str, Any]:
        return self.bindings[-1].kwargs

    @property
    def bound_schemas(self) -> tuple[Any, ...]:
        return tuple(b.schema for b in self.bindings)


def _recording_provider(
    provider_cls: type[LLMProvider],
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: Any = None,
) -> tuple[LLMProvider, _RecordingModel]:
    """Instantiate ``provider_cls`` with all three ``_build_*`` hooks stubbed.

    The stub replaces *vendor construction only*, which is exactly what the
    ``_build_*`` hooks are for — so the concrete template methods, the
    ``structured_output_defaults`` lookup and the proxy all run for real. No
    ``ChatOllama`` or ``ChatBedrockConverse`` is built on this path, so the
    Bedrock providers are exercised with no AWS identity present.
    """
    recorder = _RecordingModel(result=result)
    for hook in ("_build_large", "_build_large_at_temperature", "_build_small"):
        monkeypatch.setattr(
            provider_cls, hook, lambda self, *a, **k: recorder, raising=True
        )
    return provider_cls(), recorder


# ---------------------------------------------------------------------------
# The vocabulary itself.
# ---------------------------------------------------------------------------


def test_gameplay_schemas_carries_the_seven_the_adr_counted() -> None:
    """Seven distinct schema classes — the count ADR-013's gate corrected.

    Every sweep in this module is parametrized off ``GAMEPLAY_SCHEMAS``, so a
    schema silently dropped from the tuple would shrink this module's coverage
    while leaving it green — the same "a gate that could not fail" shape that
    let ``ollama_smoke``'s four-of-seven list return ``RELIABLE`` on an 0.80
    ``Diary`` failure rate. Pinning the count (never the names, which would
    reintroduce the duplicate vocabulary) makes that a red test instead.

    An eighth schema legitimately joining the game should therefore cost one
    deliberate edit here, with the live gate re-read.
    """
    assert len(GAMEPLAY_SCHEMAS) == 7
    assert len(set(GAMEPLAY_SCHEMAS)) == len(GAMEPLAY_SCHEMAS)


# ---------------------------------------------------------------------------
# Layer 1 — what the wrapper requests, per provider and per tier.
# ---------------------------------------------------------------------------


@_tier_sweep
@_schema_sweep
def test_ollama_wrapper_requests_json_schema(
    tier: str, schema: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every local-tier binding carries ``method="json_schema"``, and nothing else.

    Swept across all seven schemas and all three tiers: the pin is a property
    of the *provider*, so no schema and no tier may miss it. Exact-dict equality
    rather than a containment check, so a stray injected kwarg is a failure
    too — the proxy's job is to add the provider's defaults, not to invent
    arguments a call site never asked for.
    """
    provider, recorder = _recording_provider(OllamaProvider, monkeypatch)

    _TIERS[tier](provider).with_structured_output(schema)

    assert recorder.bound_schemas == (schema,)
    assert recorder.last_kwargs == _OLLAMA_DEFAULTS


@_bedrock_sweep
@_tier_sweep
@_schema_sweep
def test_bedrock_wrapper_requests_no_method(
    provider_cls: type[LLMProvider],
    tier: str,
    schema: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither cloud provider requests a method, for any schema on any tier.

    This is the **cloud-parity** assertion: the local path's reliable decoding
    must not reach ``ChatBedrockConverse``, whose own ``function_calling``
    default is what the cloud path wants — so the correct request here is no
    request at all. Both Bedrock profiles are swept because they inherit the
    empty defaults rather than declaring them (``LLMProvider`` deliberately
    makes ``structured_output_defaults`` non-abstract so the pair cannot
    drift), and an inherited guarantee is worth checking on both heirs.

    Empty-dict equality, not merely ``"method" not in kwargs``: an injected
    ``include_raw`` or ``strict`` would be just as much of a leak.
    """
    provider, recorder = _recording_provider(provider_cls, monkeypatch)

    _TIERS[tier](provider).with_structured_output(schema)

    assert recorder.bound_schemas == (schema,)
    assert recorder.last_kwargs == _BEDROCK_DEFAULTS


@_all_providers_sweep
def test_one_tier_client_serves_the_whole_vocabulary(
    provider_cls: type[LLMProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached tier client keeps the pin across every schema it is reused for.

    The tests above bind one schema per freshly-built client. Production does
    the opposite: ``get_large`` caches one client for the whole game and binds
    six different schemas through it. So bind all of ``GAMEPLAY_SCHEMAS``
    through a single model and assert the recorded sequence is exactly that
    tuple with the provider's defaults on *every* entry — the wrapper holds no
    per-binding state that a second call could exhaust.
    """
    provider, recorder = _recording_provider(provider_cls, monkeypatch)
    model = provider.large()
    expected = (
        _OLLAMA_DEFAULTS if provider_cls is OllamaProvider else _BEDROCK_DEFAULTS
    )

    for schema in GAMEPLAY_SCHEMAS:
        model.with_structured_output(schema)

    assert recorder.bound_schemas == GAMEPLAY_SCHEMAS
    assert [b.kwargs for b in recorder.bindings] == [expected] * len(GAMEPLAY_SCHEMAS)


# ---------------------------------------------------------------------------
# Layer 1 — caller kwargs: merged, never dropped, and they win.
# ---------------------------------------------------------------------------


def test_caller_kwargs_are_merged_with_the_provider_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``include_raw=True`` and the pinned method both reach the vendor client.

    Both halves of ``{**defaults, **kwargs}`` are live in production at once:
    spec 039's diary call passes ``include_raw=True`` while the local provider
    injects the method, and the inner client must receive *both*. Dropping
    either would be silent — one loses the raw envelope, the other quietly
    returns the game to tool-use decoding.
    """
    provider, recorder = _recording_provider(OllamaProvider, monkeypatch)

    provider.large().with_structured_output(Diary, include_raw=True)

    assert recorder.last_kwargs == {"method": "json_schema", "include_raw": True}


def test_caller_method_overrides_the_provider_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that names a method gets it — the documented precedence.

    ``{**defaults, **kwargs}`` puts the caller last on purpose: a call site that
    deliberately wants ``function_calling`` for one schema is the more specific
    instruction and should not be silently overridden by a provider-wide
    default it cannot see.
    """
    provider, recorder = _recording_provider(OllamaProvider, monkeypatch)

    provider.large().with_structured_output(Ballot, method="function_calling")

    assert recorder.last_kwargs == {"method": "function_calling"}


@_bedrock_sweep
def test_caller_kwargs_reach_the_cloud_client_unchanged(
    provider_cls: type[LLMProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrapping a cloud client adds nothing and removes nothing.

    The Bedrock providers are wrapped too — the template methods give a provider
    no way to opt out — so the proxy must be behaviourally invisible there:
    what the call site passes is exactly what the vendor client receives.
    """
    provider, recorder = _recording_provider(provider_cls, monkeypatch)

    provider.large().with_structured_output(Diary, include_raw=True)

    assert recorder.last_kwargs == {"include_raw": True}


# ---------------------------------------------------------------------------
# Layer 1 — the defaults snapshot, and passthrough of everything else.
# ---------------------------------------------------------------------------


@_all_providers_sweep
def test_structured_output_defaults_is_a_fresh_dict_per_call(
    provider_cls: type[LLMProvider],
) -> None:
    """Each call hands back its own dict, so one proxy cannot poison another.

    The template methods pass the result straight to a proxy that keeps it for
    the life of a cached tier singleton. A shared mutable return would leave one
    provider's accident one aliasing bug away from changing another's transport.

    The marker is a key no provider could ever legitimately return, so this
    asserts *freshness* and nothing else — writing a plausible value such as
    ``method="function_calling"`` would make the test pass or fail depending on
    what the defaults happen to contain.
    """
    provider = provider_cls()
    first = provider.structured_output_defaults()
    first["__freshness_marker__"] = object()

    assert provider.structured_output_defaults() is not first
    assert "__freshness_marker__" not in provider.structured_output_defaults()


def test_the_wrapper_snapshots_the_defaults_it_was_given() -> None:
    """Mutating the source mapping after construction cannot change the pin.

    ``StructuredMethodModel.__init__`` copies rather than aliases, because the
    proxy outlives the call that built it. Constructed directly here — this is
    the proxy's own constructor contract, not a provider behaviour.
    """
    recorder = _RecordingModel()
    defaults = {"method": "json_schema"}
    model = StructuredMethodModel(recorder, defaults)

    defaults["method"] = "function_calling"
    defaults["include_raw"] = True
    model.with_structured_output(Ballot)

    assert recorder.last_kwargs == _OLLAMA_DEFAULTS


def test_other_attributes_pass_through_to_the_vendor_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy intercepts one method and forwards everything else.

    Both an attribute read and a method call, because ``__getattr__``
    passthrough covers both and the eval stack reads real vendor fields through
    these proxies.
    """
    provider, recorder = _recording_provider(OllamaProvider, monkeypatch)
    model = provider.large()

    assert model.vendor_marker == "from-the-vendor-client"
    assert model.vendor_method(21) == 42


def test_a_missing_attribute_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo fails where it is written rather than yielding ``None``."""
    provider, _ = _recording_provider(OllamaProvider, monkeypatch)

    with pytest.raises(AttributeError):
        _ = provider.large().no_such_attribute


def test_inner_exposes_the_vendor_client_and_is_never_shadowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``.inner`` is a real property, so the passthrough cannot shadow it.

    The recording stub deliberately carries its own ``inner`` attribute — the
    exact collision a future vendor client could introduce. Attribute lookup
    finds the class-level property first, so tests that read the bound
    runnable's grammar off ``.inner`` keep reading the vendor client rather
    than one of its fields.
    """
    provider, recorder = _recording_provider(OllamaProvider, monkeypatch)
    model = provider.large()

    assert model.inner is recorder
    assert recorder.inner == "not-the-proxys-inner"


def test_inner_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rebinding ``.inner`` is refused, so a cached tier cannot be re-pointed."""
    provider, _ = _recording_provider(OllamaProvider, monkeypatch)
    model = provider.large()

    with pytest.raises(AttributeError):
        model.inner = _RecordingModel()


def test_repr_names_the_wrapped_client_and_the_pinned_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repr answers "which client, is the method pinned?" in one line.

    Two proxies deep (``InstrumentedModel(StructuredMethodModel(ChatOllama))``)
    the default ``object`` repr is two hex addresses, and the eval harnesses log
    it.
    """
    provider, _ = _recording_provider(OllamaProvider, monkeypatch)

    text = repr(provider.large())

    assert "StructuredMethodModel" in text
    assert _RecordingModel.__name__ in text
    assert "json_schema" in text


# ---------------------------------------------------------------------------
# Layer 1 — composition with the eval-harness instrumentation proxy.
# ---------------------------------------------------------------------------


def _assert_instrumentation_saw_one_success(stats: dict[str, SchemaStats]) -> None:
    assert stats["Ballot"].attempts == 1
    assert stats["Ballot"].failures == 0
    assert stats["Ballot"].fallbacks == 0


def test_instrumented_outside_the_wrapper_keeps_the_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``InstrumentedModel(StructuredMethodModel(client))`` — what ``ollama_smoke`` builds.

    The harness wraps what ``provider.large()`` already handed back, so this is
    the arrangement every measured local run actually reaches. Both directions
    of transparency are asserted: the pinned method still lands on the vendor
    client, and the instrumentation still counts the invoke — because a harness
    that eats the kwarg would disable reliable decoding *only while being
    measured*, and a wrapper that hid the runnable would silence the
    measurement.
    """
    provider, recorder = _recording_provider(
        OllamaProvider, monkeypatch, result=Ballot(yes=True)
    )
    stats: dict[str, SchemaStats] = {}
    model = InstrumentedModel(provider.large(), stats=stats)

    result = model.with_structured_output(Ballot).invoke(["prompt"])

    assert recorder.last_kwargs == _OLLAMA_DEFAULTS
    assert result == Ballot(yes=True)
    _assert_instrumentation_saw_one_success(stats)


def test_the_wrapper_outside_instrumented_keeps_the_pin() -> None:
    """``StructuredMethodModel(InstrumentedModel(client))`` — the other order.

    Not an arrangement production builds today, which is exactly why it is
    pinned: nothing in either proxy forbids it, so a future harness that
    installs its counter *beneath* the provider boundary must get the same
    transport. Constructed directly, since no provider produces this nesting.
    """
    recorder = _RecordingModel(result=Ballot(yes=True))
    stats: dict[str, SchemaStats] = {}
    model = StructuredMethodModel(
        InstrumentedModel(recorder, stats=stats),
        OllamaProvider().structured_output_defaults(),
    )

    result = model.with_structured_output(Ballot).invoke(["prompt"])

    assert recorder.last_kwargs == _OLLAMA_DEFAULTS
    assert result == Ballot(yes=True)
    _assert_instrumentation_saw_one_success(stats)


# ---------------------------------------------------------------------------
# Layer 2 — the assertions that carry the weight, against a real ChatOllama.
#
# ``.first`` / ``.last`` are ``RunnableSequence``'s step accessors: LangChain's
# ``with_structured_output`` returns the bound client piped into a parser, so
# ``.first.kwargs`` is what the client will send and ``.last`` is what will read
# the reply back. Reading them is the only offline way to see whether asking for
# a method changed the wire — a stub proves the request, these prove the effect.
# ---------------------------------------------------------------------------


def test_the_local_tier_wraps_a_real_offline_chat_ollama() -> None:
    """The layer-2 tests below really are looking at the production client.

    Without this, every grammar assertion that follows could be reading a
    stand-in. (What that client was constructed *with* — model, base url,
    temperature, ``num_ctx``, ``num_predict``, ``validate_model_on_init`` — is
    ``tests/test_llm_provider_construction.py``'s subject, not this module's.)
    """
    model = OllamaProvider().large()

    assert isinstance(model, StructuredMethodModel)
    assert isinstance(model.inner, ChatOllama)


@_tier_sweep
@_schema_sweep
def test_the_bound_runnable_carries_the_schema_grammar(
    tier: str, schema: type
) -> None:
    """Every schema, every tier: the request carries the schema's JSON grammar.

    This is ADR-013's assertable wire-level trace. ``format`` is Ollama's
    native grammar-constrained-decoding field, so a bound runnable carrying
    ``format == Schema.model_json_schema()`` is decoding masked to
    schema-valid tokens rather than a tool-use request the model may decline
    (45 of 90 diary entries, measured in spec 039).

    The ``format`` key specifically, not the whole ``kwargs`` dict, because
    LangChain also puts its own ``ls_structured_output_format`` telemetry key
    there and pinning that would mirror the vendor.

    Note the *expected* value is computed from the schema under test rather
    than transcribed, so this pins the routing rather than the shape of seven
    JSON documents — ``DayAction``'s validator-only mutual exclusion, which the
    grammar cannot express at all, stays out of scope by construction.
    """
    runnable = _TIERS[tier](OllamaProvider()).with_structured_output(schema)

    assert runnable.first.kwargs["format"] == schema.model_json_schema()


@_schema_sweep
def test_function_calling_is_the_negative_control(schema: type) -> None:
    """The other method produces a visibly different binding — so the pin matters.

    Without this, "``format`` is present and correct" could be true of almost
    any binding and the positive assertion above would be vacuous. Reached by
    the caller-override path, which doubles as proof that caller precedence
    survives against a real client and not just the recording stub.

    Three ways the two paths differ: no grammar, a tool declaration instead,
    and a different parser reading the reply. The parser classes are compared
    to each other rather than named, so a vendor rename is a non-event while a
    collapse of the two paths into one is a failure.
    """
    model = OllamaProvider().large()
    grammar_bound = model.with_structured_output(schema)
    tool_bound = model.with_structured_output(schema, method="function_calling")

    assert "format" not in tool_bound.first.kwargs
    assert "tools" in tool_bound.first.kwargs
    assert type(tool_bound.last) is not type(grammar_bound.last)
