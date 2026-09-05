"""The provider seam, driven through the REAL call sites (spec 041 Task 3.4).

What this module is for, and how it differs from its sibling
------------------------------------------------------------
``tests/test_structured_output_method.py`` (Task 3.3) tests the *wrapper*:
it hands ``StructuredMethodModel`` a schema and asserts what the wrapper asks
the vendor client for. That module never touches production code, so a call
site that hard-coded ``method="function_calling"`` — or a ninth call site
added with no wrapper between it and the client — would sail straight past
every test in it.

This module tests the *call sites*. It installs a fake provider whose tiers
return the **real production wrapper** around a recording model, re-points the
five tier bindings back at the real ``graphia.llm`` factories, and then runs
the genuine production functions — ``_generate_names``, ``_generate_one_persona``,
``generate_personas``, ``_ai_pick_target``, ``_ai_day_action``, ``_ai_reflect``,
``_ai_ballot``, ``_ai_diary`` and ``tools.claude_spike.run_spike`` — asserting
what each one's ``with_structured_output`` call actually carried. Nothing here
constructs ``StructuredMethodModel`` by hand: the provider's concrete template
methods do it, which is the whole point of them (``LLMProvider``'s docstring),
and is what makes the object under test the production article rather than a
reconstruction of it.

This module deliberately switches off the autouse net — read this
------------------------------------------------------------------
``safe_llm`` exists so a forgotten stub fails loudly instead of falling
through to real boto3 and hanging pytest teardown on retry loops. Driving the
real call sites means undoing it, so the undo is fenced on four sides and each
fence is **asserted rather than assumed**:

1. ``llm._active_provider`` is set to the fake provider, so
   ``_resolve_provider()`` short-circuits and **never consults config**. Proved
   by ``test_the_resolved_provider_is_the_fake_and_config_is_never_consulted``,
   which replaces ``llm.load_config`` with a raising sentinel for the duration.
2. The recording model is a plain object with a ``with_structured_output``
   method. It has no transport, no client, no credentials and no network code
   of any kind.
3. The three vendor constructors *as bound in* ``graphia.llm`` —
   ``ChatOllama``, ``ChatBedrockConverse``, ``BedrockEmbeddings`` — are
   replaced with sentinels that raise on call, so a real vendor client is not
   merely unused but **unconstructible** while the seam is installed. Proved by
   ``test_no_real_vendor_client_is_constructible_while_the_seam_is_installed``.
4. Only the FIVE tier bindings are re-pointed. ``safe_llm``'s sixth patch,
   ``graphia.tools.blunder_eval.get_embeddings``, is **not** a tier factory —
   the spec-033 embeddings client sits outside the provider seam on purpose
   (always Bedrock Titan, so the persona metric stays comparable across
   providers) — and re-pointing it would send the offline suite at real Bedrock
   embeddings. Proved by
   ``test_the_embeddings_net_survives_this_modules_undo``.

The undo is per-test, through the same ``monkeypatch`` surface ``safe_llm``
uses, so it is unwound at teardown;
``test_the_undo_does_not_leak_past_this_modules_tests`` (last in the file)
checks that the backstop is back in force afterwards.

The recording stub: a deliberate sibling, not an accidental duplicate
---------------------------------------------------------------------
Task 3.3's ``_RecordingModel`` returns ONE fixed ``result`` for every schema.
That is right for a wrapper test and wrong here: the production functions
*consume* what they get back. ``_generate_names`` reads ``roster.names`` with
only ``ValidationError`` caught, so a ``None`` there is an uncaught
``AttributeError``; ``_ai_day_action`` re-invokes and then falls back unless
the answer passes ``_accept``; ``_ai_pick_target`` falls to ``random.choice``
unless the id is a live one, which would consume module-global RNG for no
reason. So the recorder here answers **per schema**, from ``_CANNED`` — and
``test_the_canned_answers_cover_exactly_GAMEPLAY_SCHEMAS`` keeps that map from
drifting out of step with the vocabulary. Promoting Task 3.3's stub to
``conftest.py`` was the alternative: rejected because its extra fields
(``vendor_marker`` / ``vendor_method`` / a shadowing ``inner``) exist purely
for that module's passthrough assertions, and exporting them to all ~2000
tests would spread test-specific scaffolding to net a difference the two stubs
do not actually share.

Anti-vacuity rules
------------------
- **The expected kwargs are literals** (``_OLLAMA_DEFAULTS`` /
  ``_BEDROCK_DEFAULTS``), never ``provider.structured_output_defaults()``.
  Deriving them would assert that the code equals itself; as literals, changing
  ``OllamaProvider.structured_output_defaults`` turns this module red — which
  is exactly the check Task 3.5 performs by hand.
- **The fake provider SUBCLASSES the real provider class** and overrides only
  the three ``_build_*`` hooks. ``structured_output_defaults`` and all three
  template methods are inherited, unmodified, from production. A fake that
  declared its own defaults would be green under a broken provider.
- **The schema vocabulary comes from ``llm.GAMEPLAY_SCHEMAS``**, never retyped.

Strictly offline: no vendor client is constructed (or constructible), no
network call is made, and the only thing ever invoked is the recording stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

import graphia.llm as llm
import graphia.nodes.day as day_nodes
import graphia.nodes.night as night_nodes
import graphia.nodes.setup as setup_nodes
import graphia.preflight as preflight_mod
from graphia.llm import (
    GAMEPLAY_SCHEMAS,
    Ballot,
    BedrockProvider,
    ClaudeBedrockProvider,
    DayAction,
    Diary,
    LLMProvider,
    OllamaProvider,
    Persona,
    Pointing,
    Reflection,
    Roster,
    StructuredMethodModel,
)
from graphia.nodes.day import _ai_ballot, _ai_day_action, _ai_diary, _ai_reflect
from graphia.nodes.night import _ai_pick_target
from graphia.nodes.setup import (
    _fallback_persona,
    _generate_names,
    _generate_one_persona,
    generate_personas,
)
from graphia.state import GameState, PlayerState
from graphia.tools import blunder_eval
from graphia.tools.claude_spike import run_spike

# The suite's own conftest: the loud-failure classes under test in the two
# backstop tests, and the deterministic embedder whose net must SURVIVE this
# module's undo.
from conftest import _FakeEmbeddings, _LoudFailureLLM, _LoudFailureProvider

# Reuse the spec-018 hand-built-state helpers verbatim, as
# ``tests/test_role_guidance.py`` and its neighbours already do.
from test_slice_day_round_recap import _player, _roster

# The two providers' structured-output defaults, as LITERALS. See the
# anti-vacuity note in the module docstring for why these are not read off
# ``structured_output_defaults()``.
_OLLAMA_DEFAULTS = {"method": "json_schema"}
_BEDROCK_DEFAULTS: dict[str, Any] = {}

_BEDROCK_PROVIDERS = (BedrockProvider, ClaudeBedrockProvider)

# Env vars that would decide which client a provider builds. Wiped so nothing
# here depends on the developer's ``.env``. Construction is impossible while
# the seam is installed either way; this is about reproducibility.
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

# The FIVE tier bindings ``safe_llm`` patches — the exact set this module
# re-points at the real factories, named as (module, attribute, factory) so the
# undo cannot silently drift from the net it undoes. NOT included, and it must
# not be: ``graphia.tools.blunder_eval.get_embeddings``, ``safe_llm``'s sixth
# patch, which is not a tier factory at all.
_TIER_BINDINGS: tuple[tuple[Any, str, str], ...] = (
    (setup_nodes, "get_small", "get_small"),
    (setup_nodes, "get_large", "get_large"),
    (setup_nodes, "get_persona_model", "get_persona_model"),
    (night_nodes, "get_large", "get_large"),
    (day_nodes, "get_large", "get_large"),
)

# The vendor constructors as bound in ``graphia.llm``. Replaced with raising
# sentinels while the seam is installed, so "no real client is reachable" is an
# enforced property rather than a claim about what the code happens to do.
_VENDOR_CONSTRUCTORS = ("ChatOllama", "ChatBedrockConverse", "BedrockEmbeddings")


class _ForbiddenInThisModule(AssertionError):
    """Raised if anything reaches a vendor constructor or ``llm.load_config``."""


def _forbid(name: str) -> Callable[..., Any]:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise _ForbiddenInThisModule(
            f"graphia.llm.{name} was reached while the recording seam was "
            "installed. This module drives production call sites with the "
            "autouse LLM net switched off, so a real vendor client (or a "
            "config-driven provider resolution) here is the exact hole "
            "`safe_llm` exists to close."
        )

    return _raise


# ---------------------------------------------------------------------------
# The recording model: what did each call site ask the vendor client for?
# ---------------------------------------------------------------------------

# One usable answer per schema. Every value is chosen so the production
# function ACCEPTS it and returns through its happy path — no retry, no
# deterministic fallback, and (for ``Pointing``) no ``random.choice``, so this
# module consumes no module-global RNG on the pointing path. ``target_id`` and
# the ``Roster`` length are matched to ``_state()`` / the ``_generate_names``
# drive below.
_CANNED: dict[type, Any] = {
    Roster: Roster(names=["Ada", "Bo", "Cyd"]),
    Persona: Persona(
        personality="watchful and dry",
        manner="speaks in short sentences",
        public_backstory="Keeps the town's ledgers.",
        secret_backstory="",
    ),
    Pointing: Pointing(target_id="la-0"),
    Ballot: Ballot(yes=True),
    DayAction: DayAction(kind="speak", text="I'd like to hear from Citizen1."),
    Reflection: Reflection(thought="Citizen2 dodged the question."),
    Diary: Diary(entry="A long day; I still trust Citizen0 least of all."),
}


@dataclass
class _Binding:
    """One recorded ``with_structured_output`` call at a real call site."""

    schema: Any
    kwargs: dict[str, Any]


class _RecordedRunnable:
    """Stand-in for a bound runnable; answers with the schema's canned value."""

    def __init__(self, schema: Any) -> None:
        self.schema = schema

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return _CANNED.get(self.schema)


@dataclass
class _RecordingModel:
    """Vendor-client stand-in that records every schema binding.

    Duck-typed rather than a ``BaseChatModel`` subclass — the same posture
    ``graphia.tools.instrument.InstrumentedModel`` takes, and all
    ``StructuredMethodModel`` requires of what it wraps. It holds no transport
    of any kind, which is half of why this module cannot reach a network.
    """

    bindings: list[_Binding] = field(default_factory=list)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> _RecordedRunnable:
        self.bindings.append(_Binding(schema=schema, kwargs=dict(kwargs)))
        return _RecordedRunnable(schema)

    @property
    def schemas(self) -> set[Any]:
        return {b.schema for b in self.bindings}


def _recording_provider(
    base: type[LLMProvider], recorder: _RecordingModel
) -> LLMProvider:
    """Subclass ``base``, overriding ONLY the three abstract ``_build_*`` hooks.

    Everything above the hooks is inherited from production untouched: the three
    concrete template methods, and — load-bearing for this module's whole claim
    — ``structured_output_defaults``. So the object a call site receives is a
    real :class:`~graphia.llm.StructuredMethodModel` carrying the real
    provider's real defaults, and a change to those defaults changes what this
    module records.
    """

    class _Recording(base):  # type: ignore[valid-type,misc]
        def _build_large(self) -> Any:
            return recorder

        def _build_large_at_temperature(self, temperature: float) -> Any:
            return recorder

        def _build_small(self) -> Any:
            return recorder

    _Recording.__name__ = f"_Recording{base.__name__}"
    return _Recording()


@pytest.fixture(autouse=True)
def provider_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a provider-neutral environment."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _RecordingModel]:
    """Return an installer for the recording seam, parametrised by provider.

    Called from the test body (so it provably runs after every autouse fixture,
    ``safe_llm`` included), it:

    1. asserts ``safe_llm``'s net is currently in force — the ordering claim
       this module rests on, checked rather than trusted;
    2. installs a provider built by :func:`_recording_provider` on
       ``llm._active_provider``, and nulls ``_large`` / ``_small`` so no cached
       client from an earlier test can be resolved;
    3. forbids the three vendor constructors and ``llm.load_config``, making a
       real client unconstructible and a config-driven resolution impossible;
    4. re-points the FIVE tier bindings — and only those five — at the real
       ``graphia.llm`` factories.

    Everything goes through the ``monkeypatch`` fixture, so all of it is undone
    at teardown.
    """

    def _install(provider_cls: type[LLMProvider]) -> _RecordingModel:
        # (1) The ordering this module depends on, asserted. ``safe_llm`` is an
        # autouse conftest fixture and this installer runs from the test body,
        # so the tier bindings must currently be the loud-failure stand-ins.
        for module, attribute, _ in _TIER_BINDINGS:
            netted = getattr(module, attribute)
            assert netted is not getattr(llm, attribute), (
                f"{module.__name__}.{attribute} is already the real factory — "
                "safe_llm did not run before this installer, so this module's "
                "undo is not the deliberate, scoped one it claims to be."
            )

        recorder = _RecordingModel()
        provider = _recording_provider(provider_cls, recorder)

        # (2) The provider seam. ``_resolve_provider`` short-circuits on a
        # non-None ``_active_provider``, so config is never read.
        monkeypatch.setattr(llm, "_active_provider", provider)
        monkeypatch.setattr(llm, "_large", None)
        monkeypatch.setattr(llm, "_small", None)

        # (3) No vendor client is constructible, and no config is consultable,
        # from inside ``graphia.llm`` for the duration.
        for name in _VENDOR_CONSTRUCTORS:
            monkeypatch.setattr(llm, name, _forbid(name))
        monkeypatch.setattr(llm, "load_config", _forbid("load_config"))

        # (4) The five tier bindings, back to the real factories.
        for module, attribute, factory in _TIER_BINDINGS:
            monkeypatch.setattr(module, attribute, getattr(llm, factory))

        return recorder

    return _install


# ---------------------------------------------------------------------------
# The call sites, and how to drive each one for real.
# ---------------------------------------------------------------------------


def _state() -> GameState:
    """A hand-built Day state with living players of both roles.

    ``_roster(law_alive=3, mafia_alive=2)`` yields ids ``la-0``..``la-2`` and
    ``maf-3``/``maf-4``; ``_CANNED[Pointing]`` names ``la-0`` so the pointing
    call site accepts the first answer.
    """
    return {
        "cycle": 1,
        "players": _roster(law_alive=3, mafia_alive=2),
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": {},
    }


def _living(state: GameState, role: str) -> PlayerState:
    return next(
        p for p in state["players"].values() if p.is_alive and p.role == role
    )


# Every driver takes ``monkeypatch`` even though only ``_drive_claude_spike``
# needs it, so the parametrised tests can call ``site.drive(monkeypatch)``
# uniformly. Each driver asserts on its own return value as well as being
# recorded: a call site that silently fell through to its retry-then-fallback
# path would still have recorded a binding, so the recorded kwargs alone would
# not prove the drive reached the model's answer.


def _drive_generate_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``_CANNED[Roster]`` carries three names, so ``count=3`` is accepted on the
    # first invoke and no corrective retry is issued.
    assert _generate_names(3).names == ["Ada", "Bo", "Cyd"]


def _drive_generate_one_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``model=None`` is spec-034's flag-OFF route to this call site: the cached
    # gameplay ``get_large()``.
    state = _state()
    player = _living(state, "law_abiding")
    persona = _generate_one_persona(player, [], model=None)
    # Not the deterministic fallback — i.e. the recorded answer was USED, so the
    # drive went through the model rather than round the node's except branch.
    assert persona != _fallback_persona(player)
    assert persona.personality == _CANNED[Persona].personality


def _drive_generate_personas(monkeypatch: pytest.MonkeyPatch) -> None:
    # The flag-ON route to the SAME call site, through ``get_persona_model`` —
    # i.e. the third tier factory, ``large_at_temperature``. One AI player keeps
    # the drive to a single generation (no prior personas, so no collision and
    # no regeneration).
    players = {
        "human": _player("human", "You", "law_abiding", is_human=True),
        "ai-0": _player("ai-0", "Citizen0", "law_abiding"),
    }
    delta = generate_personas({"players": players})  # type: ignore[arg-type]
    generated = delta["players"]["ai-0"].persona
    assert generated is not None
    assert generated.personality == _CANNED[Persona].personality


def _drive_ai_pick_target(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    law_abiding = [
        p for p in state["players"].values() if p.role == "law_abiding" and p.is_alive
    ]
    chosen = _ai_pick_target(law_abiding, _living(state, "mafia"))
    # A valid answer means the RNG fallback was not reached.
    assert chosen == "la-0"


def _drive_ai_day_action(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    action = _ai_day_action(_living(state, "law_abiding"), state)
    assert action == _CANNED[DayAction]


def _drive_ai_reflect(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    assert _ai_reflect(_living(state, "law_abiding"), state) == (
        _CANNED[Reflection].thought
    )


def _drive_ai_ballot(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    voter = _living(state, "law_abiding")
    target = _living(state, "mafia")
    assert _ai_ballot(voter, target, state) == _CANNED[Ballot]


def _drive_ai_diary(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    assert _CANNED[Diary].entry in _ai_diary(_living(state, "law_abiding"), state)


def _drive_claude_spike(monkeypatch: pytest.MonkeyPatch) -> None:
    """The eighth call site — and the one with no binding to re-point.

    ``tools/claude_spike.py`` does a function-local
    ``from graphia.llm import get_large``, so it never had a module attribute
    for ``safe_llm`` to patch. That is precisely the shape the provider-level
    backstop was added for, and driving it here is what makes this module's
    "per call site" claim cover all eight rather than the seven with bindings.

    The spike forces ``GRAPHIA_LLM_PROVIDER`` by assigning ``os.environ``
    directly, so it is ``monkeypatch.setenv``-ed first to guarantee the value is
    restored at teardown. Its preflight is the one thing here that would reach
    the network, so it is stubbed out. Note that the parametrisation drives this
    Bedrock-flavoured tool through the Ollama provider too: the call site is
    provider-blind by design, and that blindness is the property under test.
    """
    monkeypatch.setenv("GRAPHIA_LLM_PROVIDER", "bedrock-claude")
    monkeypatch.setattr(preflight_mod, "run_claude_preflight", lambda config: None)
    probes = run_spike(large_model=None, small_model=None)
    assert [p.schema for p in probes] == ["Ballot", "DayAction", "Pointing", "Roster"]
    assert all(p.ok for p in probes)


@dataclass(frozen=True)
class _CallSite:
    """One production ``with_structured_output`` call site, and how to reach it."""

    id: str
    drive: Callable[[pytest.MonkeyPatch], None]
    # Schemas this drive is expected to bind.
    schemas: tuple[type, ...]
    # Kwargs the CALL SITE itself passes, per schema. **Empty for every site
    # as of spec 041 Slice 4**: the diary call was the only one that ever had
    # any (``include_raw=True``, spec 039), and the withdrawal of the interim
    # prose recovery took it — together with the dedicated
    # ``test_the_diary_call_site_keeps_include_raw_beside_the_pinned_method``,
    # which scripted it here. The merge itself stays covered at wrapper level by
    # ``test_structured_output_method.py::test_caller_kwargs_are_merged_with_the_provider_default``;
    # "no production call site passes any caller kwargs" is asserted across all
    # eight sites by Task 4.3's ``tests/test_spec041_withdrawal.py``. The field
    # stays because ``_assert_recorded``'s ``{**defaults, **site.caller_kwargs}``
    # is the honest expression of what a call site is *allowed* to do — a future
    # site that legitimately needs a kwarg declares it here rather than
    # weakening the sweep.
    caller_kwargs: dict[type, dict[str, Any]] = field(default_factory=dict)


_CALL_SITES: tuple[_CallSite, ...] = (
    _CallSite(
        id="setup._generate_names",
        drive=_drive_generate_names,
        schemas=(Roster,),
    ),
    _CallSite(
        id="setup._generate_one_persona",
        drive=_drive_generate_one_persona,
        schemas=(Persona,),
    ),
    _CallSite(
        id="setup.generate_personas[get_persona_model]",
        drive=_drive_generate_personas,
        schemas=(Persona,),
    ),
    _CallSite(
        id="night._ai_pick_target",
        drive=_drive_ai_pick_target,
        schemas=(Pointing,),
    ),
    _CallSite(
        id="day._ai_day_action",
        drive=_drive_ai_day_action,
        schemas=(DayAction,),
    ),
    _CallSite(
        id="day._ai_reflect",
        drive=_drive_ai_reflect,
        schemas=(Reflection,),
    ),
    _CallSite(
        id="day._ai_ballot",
        drive=_drive_ai_ballot,
        schemas=(Ballot,),
    ),
    _CallSite(
        id="day._ai_diary",
        drive=_drive_ai_diary,
        schemas=(Diary,),
    ),
    _CallSite(
        id="tools.claude_spike.run_spike",
        drive=_drive_claude_spike,
        schemas=(Ballot, DayAction, Pointing, Roster),
    ),
)

_call_site_sweep = pytest.mark.parametrize(
    "site", _CALL_SITES, ids=[s.id for s in _CALL_SITES]
)
_bedrock_sweep = pytest.mark.parametrize(
    "provider_cls", _BEDROCK_PROVIDERS, ids=[c.__name__ for c in _BEDROCK_PROVIDERS]
)


def _assert_recorded(
    recorder: _RecordingModel, site: _CallSite, defaults: dict[str, Any]
) -> None:
    """Every binding this drive made carries ``defaults`` plus its own kwargs.

    Exact-dict equality per binding, not a containment check: a stray injected
    kwarg is as much of a defect as a missing one — the proxy's job is to add
    the provider's defaults, never to invent arguments a call site did not ask
    for. Asserted over EVERY recorded binding rather than the last, because a
    call site that binds more than once (``claude_spike`` binds four schemas
    through one cached client) must carry the pin on all of them.
    """
    assert recorder.bindings, (
        f"{site.id} recorded no schema binding at all — the drive did not reach "
        "the call site, so this test proves nothing."
    )
    assert recorder.schemas == set(site.schemas)
    for binding in recorder.bindings:
        expected = {**defaults, **site.caller_kwargs.get(binding.schema, {})}
        assert binding.kwargs == expected, (
            f"{site.id} bound {binding.schema.__name__} with "
            f"{binding.kwargs!r}, expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# 1. The fences round the undo.
# ---------------------------------------------------------------------------


def test_safe_llm_nets_every_tier_binding_before_this_module_undoes_it() -> None:
    """Layer 1 of the net is in force by default in this module.

    Requests no ``seam``, so nothing has been undone. Each of the five tier
    bindings must be a loud-failure stand-in that raises on ``invoke`` — which
    is both the baseline the rest of this module deliberately departs from, and
    the statement that the departure is scoped to the tests that ask for it.
    """
    for module, attribute, _ in _TIER_BINDINGS:
        factory = getattr(module, attribute)
        # ``get_persona_model`` takes a temperature; the other two take nothing.
        model = factory(1.0) if attribute == "get_persona_model" else factory()
        assert isinstance(model, _LoudFailureLLM)
        with pytest.raises(RuntimeError, match="Unstubbed LLM call"):
            model.with_structured_output(DayAction).invoke([])


def test_the_provider_backstop_nets_a_module_that_imports_get_large_directly() -> None:
    """Layer 2: reaching ``graphia.llm.get_large`` fails LOUDLY, not remotely.

    The situation the backstop exists for, constructed: production code that
    imports a tier factory straight from ``graphia.llm`` has no binding in
    ``safe_llm``'s list, so before spec 041 §3.4 it resolved a provider from
    config, built a real ``ChatBedrockConverse``, and the first ``invoke``
    started boto3 retry loops that outlive the test. ``tools/claude_spike.py``
    takes exactly that route today.

    Asserted on all three factories, and on the shape as well as the failure:
    the object handed back is the production ``StructuredMethodModel`` over a
    loud-failure stand-in — never a vendor client — so nothing constructed here
    is capable of a network call in the first place.
    """
    assert isinstance(llm._active_provider, _LoudFailureProvider)
    assert llm._large is None
    assert llm._small is None

    for model in (llm.get_large(), llm.get_small(), llm.get_persona_model(1.0)):
        assert isinstance(model, StructuredMethodModel)
        assert isinstance(model.inner, _LoudFailureLLM)
        with pytest.raises(RuntimeError, match="add its binding to `safe_llm`"):
            model.with_structured_output(DayAction).invoke([])


def test_the_resolved_provider_is_the_fake_and_config_is_never_consulted(
    seam: Callable[..., _RecordingModel],
) -> None:
    """``_resolve_provider()`` short-circuits on the installed provider.

    The first hazard of this module is a fake provider installed somewhere that
    still lets config decide, because config's default is ``bedrock`` and a real
    client one line later. Installing on ``_active_provider`` is what prevents
    it, and the proof is that ``llm.load_config`` — a raising sentinel for the
    duration — is never called: ``_resolve_provider`` reads it only when
    ``_active_provider`` is ``None``.
    """
    seam(OllamaProvider)

    provider = llm._resolve_provider()
    assert provider is llm._active_provider
    assert isinstance(provider, OllamaProvider)
    assert type(provider) is not OllamaProvider  # the recording subclass
    # The sentinel is live: had resolution consulted config, it would have
    # raised rather than returned.
    with pytest.raises(_ForbiddenInThisModule):
        llm.load_config()


@pytest.mark.parametrize("name", _VENDOR_CONSTRUCTORS)
def test_no_real_vendor_client_is_constructible_while_the_seam_is_installed(
    name: str, seam: Callable[..., _RecordingModel]
) -> None:
    """A real vendor client is unconstructible, not merely unused.

    ``safe_llm``'s whole purpose is that the suite cannot reach real AWS. This
    module switches its call-site layer off, so "we don't happen to build one"
    is not good enough — the three vendor constructors bound in
    ``graphia.llm`` are replaced with raising sentinels, so a future edit that
    tried to build one fails here instead of dialling out.
    """
    seam(OllamaProvider)

    with pytest.raises(_ForbiddenInThisModule):
        getattr(llm, name)(model="anything", region_name="us-east-1")


def test_the_five_tier_bindings_are_re_pointed_at_the_real_factories(
    seam: Callable[..., _RecordingModel],
) -> None:
    """The undo is an identity swap back to ``graphia.llm``, not a lookalike.

    If the bindings were re-pointed at anything other than the real factories,
    every "the call site requests the method" assertion below would be testing
    this module's own plumbing instead of production's.
    """
    seam(OllamaProvider)

    for module, attribute, factory in _TIER_BINDINGS:
        assert getattr(module, attribute) is getattr(llm, factory)


def test_the_embeddings_net_survives_this_modules_undo(
    seam: Callable[..., _RecordingModel],
) -> None:
    """``safe_llm``'s SIXTH patch is not a tier binding and must stay patched.

    ``graphia.tools.blunder_eval.get_embeddings`` is netted by ``safe_llm`` with
    a deterministic fake, not a loud failure, because the spec-033 embeddings
    client sits deliberately OUTSIDE the provider seam — always Bedrock Titan,
    independent of ``GRAPHIA_LLM_PROVIDER``, so the persona-similarity metric
    stays a comparable measuring stick across providers. Counting it among the
    tier bindings and re-pointing it here would send the offline suite at real
    Bedrock embeddings, which is why the count that matters for the undo is
    five and not six.
    """
    seam(OllamaProvider)

    embeddings = blunder_eval.get_embeddings()
    assert isinstance(embeddings, _FakeEmbeddings)
    assert len(embeddings.embed_documents(["a b c"])[0]) == 26


def test_every_tier_factory_hands_back_the_production_wrapper(
    seam: Callable[..., _RecordingModel],
) -> None:
    """All three factories return a real ``StructuredMethodModel`` over the recorder.

    Nothing in this module constructs the proxy: the provider's three concrete
    template methods do, because the fake overrides only the ``_build_*`` hooks.
    So this is also the assertion that a provider cannot opt out of wrapping —
    checked at the factory level, where production reads it.
    """
    recorder = seam(OllamaProvider)

    for model in (llm.get_large(), llm.get_small(), llm.get_persona_model(1.0)):
        assert isinstance(model, StructuredMethodModel)
        assert model.inner is recorder


# ---------------------------------------------------------------------------
# 2. The core: what each REAL call site requests.
# ---------------------------------------------------------------------------


@_call_site_sweep
def test_the_call_site_requests_the_local_method(
    site: _CallSite,
    seam: Callable[..., _RecordingModel],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every production call site carries ``method="json_schema"`` on the local path.

    This is the assertion the wrapper-level module cannot make: it drives the
    genuine functions, so a call site that hard-coded a method, unwrapped the
    proxy via ``.inner`` before binding, or built its own client would fail
    here. Swept over all eight call sites (plus the second, flag-on route to
    the persona one) so no site is exempt, and every one of them knows nothing
    about which provider it is talking to.
    """
    recorder = seam(OllamaProvider)

    site.drive(monkeypatch)

    _assert_recorded(recorder, site, _OLLAMA_DEFAULTS)


@_bedrock_sweep
@_call_site_sweep
def test_the_call_site_requests_no_method_on_the_cloud_twin(
    site: _CallSite,
    provider_cls: type[LLMProvider],
    seam: Callable[..., _RecordingModel],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same call sites request NO method on either cloud provider.

    Cloud parity, expressed where it can actually leak: the local path's
    reliable decoding must not reach ``ChatBedrockConverse``, whose own
    ``function_calling`` default is what the cloud wants — so the correct
    request is no request at all. Both Bedrock profiles are swept because they
    INHERIT the empty defaults rather than declaring them, and an inherited
    guarantee is worth checking on both heirs.

    Empty-except-the-caller's-own-kwargs, not merely "no ``method`` key": an
    injected ``strict`` or ``include_raw`` would be just as much of a leak.
    """
    recorder = seam(provider_cls)

    site.drive(monkeypatch)

    _assert_recorded(recorder, site, _BEDROCK_DEFAULTS)


# ``test_the_diary_call_site_keeps_include_raw_beside_the_pinned_method`` stood
# here until spec 041 Slice 4. It scripted ``_ai_diary``'s ``include_raw=True``
# (spec 039) beside the injected method to prove both halves of
# ``{**defaults, **kwargs}`` were live at a real call site — the one place
# production exercised the merge. The withdrawal of the interim prose recovery
# removed that kwarg, and with it the *subject* of the coverage rather than the
# coverage: no production call site passes any caller kwargs now, so a test that
# scripted one into ``_CALL_SITES`` would assert a fiction. The merge stays
# covered at wrapper level by
# ``test_structured_output_method.py::test_caller_kwargs_are_merged_with_the_provider_default``,
# and Task 4.3's ``tests/test_spec041_withdrawal.py`` asserts the stronger
# replacement invariant — *no* caller kwargs at *any* of the eight sites, which
# also catches a new site hard-coding one.


# ---------------------------------------------------------------------------
# 3. The vocabulary the call sites actually bind.
# ---------------------------------------------------------------------------


def test_the_recorded_schema_set_is_exactly_GAMEPLAY_SCHEMAS(
    seam: Callable[..., _RecordingModel],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving every call site binds every schema in the vocabulary, and no other.

    Not a duplicate of the sibling module's recorded-set assertion, which is
    made at the WRAPPER level where the test itself supplies the schemas. This
    one asserts what the *call sites* bind, so it is the only version that can
    catch a schema added to ``GAMEPLAY_SCHEMAS`` with no call site driven here
    — an eighth call site arriving without coverage fails a red test instead of
    none.

    Both directions matter. A missing schema means a call site exists that this
    module never reaches (its pin is untested); a surplus one means a call site
    binds something the declared vocabulary does not contain, which is how the
    live ADR-013 gate came to report on four of seven schemas.
    """
    recorder = seam(OllamaProvider)

    for site in _CALL_SITES:
        site.drive(monkeypatch)

    assert recorder.schemas == set(GAMEPLAY_SCHEMAS)


def test_the_canned_answers_cover_exactly_GAMEPLAY_SCHEMAS() -> None:
    """The recorder's per-schema answers are in step with the vocabulary.

    Every drive above needs an answer its production function will accept; a
    schema missing from ``_CANNED`` would answer ``None``, sending that call
    site down its retry-then-fallback path where the recorded binding still
    looks fine. So the map is pinned against ``GAMEPLAY_SCHEMAS`` rather than
    trusted — the same hazard the tuple itself exists to close, one level down.
    """
    assert set(_CANNED) == set(GAMEPLAY_SCHEMAS)
    for schema, answer in _CANNED.items():
        assert isinstance(answer, schema)


# ---------------------------------------------------------------------------
# 4. And the net is back.
# ---------------------------------------------------------------------------


def test_the_undo_does_not_leak_past_this_modules_tests() -> None:
    """After the seam tests, ``safe_llm``'s net is in force again.

    Every patch above goes through the ``monkeypatch`` fixture, so this should
    be true by construction — but "this module switches the safety net off" is
    the kind of claim that deserves a check at the far end rather than an
    argument. Placed last on purpose (pytest runs a module's tests in file
    order), so it observes the state the earlier tests leave behind.
    """
    assert isinstance(llm._active_provider, _LoudFailureProvider)
    assert llm._large is None
    assert llm._small is None
    for module, attribute, _ in _TIER_BINDINGS:
        assert getattr(module, attribute) is not getattr(llm, attribute)
    # The forbidding sentinels are gone too, so the vendor names are the real
    # classes again for whatever module runs next.
    for name in _VENDOR_CONSTRUCTORS:
        assert getattr(llm, name).__module__.startswith("langchain")
    assert llm.load_config.__module__ == "graphia.config"
