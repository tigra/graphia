"""Spec 034 (Diversified Persona Generation), Slice 1 tests — randomization.

Spec 034 randomizes persona creation so the cast starts varied (functional-spec
§2). Three diversity levers are injected before each ``_generate_one_persona``
call, all gated by the default-on ``GRAPHIA_PERSONA_DIVERSITY`` flag (ADR 011):

1. the already-created personas are shown in a **shuffled** order
   (``graphia.nodes.setup._shuffle_personas`` over the module-global ``random``,
   mirroring ``_shuffle_night_roster``'s seam — disabled returns ``list(prior)``
   BEFORE any ``random.*`` call);
2. the character is steered toward a **random target temperament** drawn WITHOUT
   replacement within the game (``_draw_archetypes`` over the same RNG), appended
   as a SEPARATE ``HumanMessage`` built from
   ``PERSONA_ARCHETYPE_HINT_TEMPLATE`` (NOT a ``{...}`` slot);
3. the persona model runs at a **higher temperature**
   (``graphia.llm.get_persona_model``).

Every assertion is structural / prompt-capture (architecture §6); no Bedrock is
reached — the seams are pure stdlib RNG, and the generation tests use a capturing
``get_large`` / ``get_persona_model`` fake the autouse ``safe_llm`` net allows.

The seam/fairness harness mirrors ``test_slice30_night_roster.py``; the
prompt-capture style mirrors ``test_slice31_roster_aware_gen.py``; the flag /
threading shape mirrors ``test_slice30_night_roster.py``'s ``load_config`` /
``build_runtime_graph`` blocks.

Test map (Slice 1):

A.  ``_shuffle_personas`` seam — set preserved, order varies, no mutation,
    flag-OFF = insertion order + ZERO RNG draw (the load-bearing contract),
    seed-reproducible.
B.  ``_draw_archetypes`` — distinct within a game (no-replacement), flag-OFF =
    all-None + ZERO RNG draw.
C.  Prompt-capture (flag ON) — each player's prompt carries a random archetype
    hint (even the first); the prior personas appear SHUFFLED; the persona model
    is built at the higher temperature.
D.  Flag-OFF parity — no archetype hint, no shuffle draw, the cached gameplay
    model (``get_persona_model`` never called), no regeneration.
E.  ``load_config`` default-on semantics for the four settings.
F.  Threading anti-drift — ``build_runtime_graph`` + ``_assemble_graph`` carry
    the flag + tunables.
"""

from __future__ import annotations

import inspect
import random
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import graphia.nodes.setup as setup_nodes
from graphia.config import load_config
from graphia.graph import _assemble_graph
from graphia.llm import Persona
from graphia.nodes.setup import (
    _draw_archetypes,
    _shuffle_personas,
    generate_personas,
)
from graphia.prompts import (
    PERSONA_ARCHETYPE_HINT_TEMPLATE,
    PERSONA_ARCHETYPES,
    PERSONA_DISTINCT_FROM_TEMPLATE,
)
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.state import GameState, PlayerPersona, PlayerState

BASE_SEED = 20250619


# --------------------------------------------------------------------------
# Builders + a content-recording persona fake (mirrors spec 031's).
# --------------------------------------------------------------------------


def _persona(tag: str) -> PlayerPersona:
    """A distinctive in-state persona whose fields share no substring with names."""
    return PlayerPersona(
        personality=f"{tag}-temperament",
        manner=f"{tag}-manner",
        public_persona=f"{tag}-backstory",
        true_self="",
    )


def _prior(n: int) -> list[PlayerPersona]:
    return [_persona(f"P{i}") for i in range(n)]


class _CapturingPersonaFake:
    """A content-recording ``get_large`` / ``get_persona_model`` stand-in.

    Records every ``messages`` list handed to ``.invoke`` (one per generation
    call, in order) and serves scripted ``Persona`` outputs FIFO, replaying the
    last once drained — so a test can drive the REAL ``generate_personas`` and
    inspect the actual prompt each AI player's persona call received.
    """

    def __init__(self, outputs: list[Persona | Exception]) -> None:
        self._outputs: list[Persona | Exception] = list(outputs)
        self._last: Persona | None = None
        self.messages_log: list = []
        self.call_count = 0

    def with_structured_output(self, schema: type, **kwargs: object) -> "_CapturingPersonaFake":
        return self

    def invoke(self, messages) -> Persona:
        self.call_count += 1
        self.messages_log.append(messages)
        if not self._outputs:
            if self._last is None:
                raise AssertionError("no scripted outputs remain")
            return self._last
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        self._last = out
        return out


# Three distinctive personas so a LATER player's distinct-from block is
# unambiguous, and no two collide under the default lexical bar.
_P1 = Persona(
    personality="boisterous and quick to laugh",
    manner="speaks in loud sweeping declarations",
    public_backstory="the village blacksmith with soot on his hands",
    secret_backstory="",
)
_P2 = Persona(
    personality="meticulous and slow to trust",
    manner="weighs each word and pauses before answering",
    public_backstory="a retired schoolteacher who keeps a tidy ledger",
    secret_backstory="",
)
_P3 = Persona(
    personality="dreamy and forever distracted by birds",
    manner="trails off mid-sentence and hums to herself",
    public_backstory="the herbalist who forages the eastern marsh at dawn",
    secret_backstory="",
)


def _state_with_roles() -> GameState:
    """A hand-built post-``assign_roles`` state: human first, three AI Citizens.

    Drives ``generate_personas`` directly off a hand-built state (no graph, no
    role-deal shuffle), so the only module-global RNG draws are the spec-034 ones
    under test. Names share no substring with the persona prose.
    """
    roster = [
        PlayerState(id="p-human", name="Alice", role="law_abiding", is_human=True),
        PlayerState(id="p-1", name="Marco", role="law_abiding", is_human=False),
        PlayerState(id="p-2", name="Priya", role="law_abiding", is_human=False),
        PlayerState(id="p-3", name="Silas", role="law_abiding", is_human=False),
    ]
    return {"human_id": "p-human", "players": {p.id: p for p in roster}}


def _human_messages(messages) -> list[str]:
    return [m.content for m in messages if isinstance(m, HumanMessage)]


_ARCHETYPE_MARKER = PERSONA_ARCHETYPE_HINT_TEMPLATE.split("{archetype}")[0].strip()
_DISTINCT_MARKER = PERSONA_DISTINCT_FROM_TEMPLATE.split("{others}")[0].strip()


def _archetype_hint_in(messages) -> str | None:
    """Return the archetype-hint ``HumanMessage`` content, or ``None``."""
    for content in _human_messages(messages):
        if _ARCHETYPE_MARKER in content:
            return content
    return None


def _distinct_block_in(messages) -> str | None:
    for content in _human_messages(messages):
        if _DISTINCT_MARKER in content:
            return content
    return None


# ==========================================================================
# A. _shuffle_personas seam.
# ==========================================================================


def test_shuffle_personas_preserves_the_set() -> None:
    """ON: the shuffled prior list holds exactly the input personas (set + len)."""
    prior = _prior(6)
    random.seed(BASE_SEED)
    shuffled = _shuffle_personas(prior, enabled=True)

    assert set(id(p) for p in shuffled) == set(id(p) for p in prior)
    assert len(shuffled) == len(prior)


def test_shuffle_personas_does_not_mutate_input() -> None:
    prior = _prior(6)
    before = list(prior)
    random.seed(BASE_SEED)
    _shuffle_personas(prior, enabled=True)
    assert prior == before, "the input list must not be mutated in place"


def test_shuffle_personas_produces_more_than_one_order() -> None:
    """ON: over many seeds the seam yields > 1 distinct order — it really reorders."""
    prior = _prior(6)
    master = random.Random(BASE_SEED)
    orders: set[tuple[int, ...]] = set()
    for _ in range(300):
        random.seed(master.randrange(2**31))
        shuffled = _shuffle_personas(prior, enabled=True)
        orders.add(tuple(id(p) for p in shuffled))
    assert len(orders) > 1, "expected the persona shuffle to produce > 1 order"


def test_shuffle_personas_flag_off_returns_insertion_order() -> None:
    prior = _prior(6)
    result = _shuffle_personas(prior, enabled=False)
    assert [id(p) for p in result] == [id(p) for p in prior]


def test_shuffle_personas_flag_off_takes_no_rng_draw() -> None:
    """OFF consumes ZERO module-global RNG state (the load-bearing contract).

    Flag-OFF must reproduce the spec-031 seeded trajectory byte-for-byte; that
    only holds if the disabled call draws nothing. The ``not enabled`` guard must
    sit ahead of ``random.shuffle`` — the dual-mode byte-equal smoke depends on it.
    """
    prior = _prior(6)
    random.seed(BASE_SEED)
    state_before = random.getstate()

    _shuffle_personas(prior, enabled=False)

    assert random.getstate() == state_before, (
        "flag-OFF _shuffle_personas drew from the module-global RNG — it must "
        "return before any random.* call (spec-031 trajectory preserved)."
    )


def test_shuffle_personas_flag_off_never_calls_random_shuffle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _prior(6)

    def _boom(_seq: list) -> None:
        raise AssertionError("random.shuffle must not be called when disabled")

    monkeypatch.setattr(setup_nodes.random, "shuffle", _boom)

    _shuffle_personas(prior, enabled=False)  # OFF: must not call shuffle.
    with pytest.raises(AssertionError, match="must not be called when disabled"):
        _shuffle_personas(prior, enabled=True)


def test_shuffle_personas_same_seed_reproduces_order() -> None:
    prior = _prior(6)
    random.seed(BASE_SEED)
    first = [id(p) for p in _shuffle_personas(prior, enabled=True)]
    random.seed(BASE_SEED)
    second = [id(p) for p in _shuffle_personas(prior, enabled=True)]
    assert first == second


# ==========================================================================
# B. _draw_archetypes — distinct within a game, OFF = all-None + no draw.
# ==========================================================================


def test_draw_archetypes_distinct_within_a_game() -> None:
    """ON: each player's drawn temperament is distinct (without-replacement)."""
    random.seed(BASE_SEED)
    drawn = _draw_archetypes(5, enabled=True)

    assert len(drawn) == 5
    assert all(a in PERSONA_ARCHETYPES for a in drawn)
    assert len(set(drawn)) == 5, f"archetypes must be distinct within a game: {drawn}"


def test_draw_archetypes_flag_off_all_none_and_no_draw() -> None:
    """OFF: all-None, with ZERO module-global RNG draw (paired OFF contract)."""
    random.seed(BASE_SEED)
    state_before = random.getstate()

    drawn = _draw_archetypes(5, enabled=False)

    assert drawn == [None] * 5
    assert random.getstate() == state_before, (
        "flag-OFF _draw_archetypes drew from the module-global RNG — it must "
        "return before any random.* call."
    )


def test_draw_archetypes_more_than_pool_still_draws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A roster larger than the pool refills (defensive) — still all-strings."""
    random.seed(BASE_SEED)
    big = len(PERSONA_ARCHETYPES) + 3
    drawn = _draw_archetypes(big, enabled=True)
    assert len(drawn) == big
    assert all(a in PERSONA_ARCHETYPES for a in drawn)


# ==========================================================================
# C. Prompt-capture (flag ON): archetype hint per player (incl. first),
#    prior personas shuffled, persona model at the higher temperature.
# ==========================================================================


def test_every_player_prompt_carries_a_random_archetype_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ON: each AI player's generation prompt carries an archetype-hint message —
    including the FIRST player (no prior to differ from, but still steered)."""
    fake = _CapturingPersonaFake([_P1, _P2, _P3])
    monkeypatch.setattr(setup_nodes, "get_persona_model", lambda temperature: fake)

    random.seed(BASE_SEED)
    generate_personas(_state_with_roles(), persona_diversity_enabled=True)

    # One invoke per AI seat (no collisions among the distinct fakes → no regen).
    assert fake.call_count == 3
    for idx, prompt in enumerate(fake.messages_log):
        hint = _archetype_hint_in(prompt)
        assert hint is not None, f"seat {idx} prompt carries no archetype hint"
        # The hint names one of the pool's temperaments.
        assert any(a in hint for a in PERSONA_ARCHETYPES)


def test_first_player_has_archetype_hint_but_no_distinct_from_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ON: the first seat is steered (archetype) yet has nothing to differ from."""
    fake = _CapturingPersonaFake([_P1, _P2, _P3])
    monkeypatch.setattr(setup_nodes, "get_persona_model", lambda temperature: fake)

    random.seed(BASE_SEED)
    generate_personas(_state_with_roles(), persona_diversity_enabled=True)

    first_prompt = fake.messages_log[0]
    assert _archetype_hint_in(first_prompt) is not None
    assert _distinct_block_in(first_prompt) is None, (
        "the first AI player has no prior persona to differ from"
    )
    assert isinstance(first_prompt[0], SystemMessage)


def test_later_player_sees_prior_personas_in_a_shuffled_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ON: the distinct-from block for a later seat reflects a SHUFFLED prior order.

    Pin ``_shuffle_personas`` to a known reordering (reverse) and assert the
    rendered distinct-from block lists the prior characters' table-facing text in
    that pinned order — proving the node feeds the shuffled list, not insertion
    order. (The seam itself is exercised in block A; here we prove generation
    routes through it.)
    """
    fake = _CapturingPersonaFake([_P1, _P2, _P3])
    monkeypatch.setattr(setup_nodes, "get_persona_model", lambda temperature: fake)
    # Pin the shuffle to reverse-insertion so the assertion is exact.
    monkeypatch.setattr(
        setup_nodes,
        "_shuffle_personas",
        lambda prior, *, enabled: list(reversed(prior)),
    )

    random.seed(BASE_SEED)
    generate_personas(_state_with_roles(), persona_diversity_enabled=True)

    # Third seat: prior = [seat1, seat2]; reversed → seat2 then seat1. The block
    # lists seat2's table-facing text BEFORE seat1's.
    third_block = _distinct_block_in(fake.messages_log[2])
    assert third_block is not None
    # _P1/_P2 map to PlayerPersona via public_backstory → public_persona.
    pos_p1 = third_block.find(_P1.public_backstory)
    pos_p2 = third_block.find(_P2.public_backstory)
    assert pos_p1 != -1 and pos_p2 != -1
    assert pos_p2 < pos_p1, "prior personas must appear in the shuffled (reversed) order"


def test_persona_model_built_at_higher_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ON: ``get_persona_model`` is built at ``persona_temperature`` (not gameplay).

    Capture the temperature ``generate_personas`` requests, and assert
    ``get_large`` is NEVER consulted for generation on the diversity-on path.
    """
    captured_temps: list[float] = []

    fake = _CapturingPersonaFake([_P1, _P2, _P3])

    def _capture(temperature: float):
        captured_temps.append(temperature)
        return fake

    monkeypatch.setattr(setup_nodes, "get_persona_model", _capture)

    def _boom_large():
        raise AssertionError("get_large must not be used on the diversity-ON path")

    monkeypatch.setattr(setup_nodes, "get_large", _boom_large)

    random.seed(BASE_SEED)
    generate_personas(
        _state_with_roles(),
        persona_diversity_enabled=True,
        persona_temperature=1.3,
    )

    assert captured_temps, "get_persona_model was never called"
    assert all(t == 1.3 for t in captured_temps), captured_temps


# ==========================================================================
# D. Flag-OFF parity: no archetype hint, no shuffle draw, cached gameplay model.
# ==========================================================================


def test_flag_off_uses_get_large_and_no_archetype_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFF: generation routes through ``get_large`` (NOT ``get_persona_model``),
    and no prompt carries an archetype hint (spec-031 shape)."""
    fake = _CapturingPersonaFake([_P1, _P2, _P3])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    def _boom_persona(temperature: float):
        raise AssertionError("get_persona_model must not be used on the OFF path")

    monkeypatch.setattr(setup_nodes, "get_persona_model", _boom_persona)

    generate_personas(_state_with_roles(), persona_diversity_enabled=False)

    assert fake.call_count == 3
    for prompt in fake.messages_log:
        assert _archetype_hint_in(prompt) is None, (
            "flag-OFF prompts must carry NO archetype hint"
        )


def test_flag_off_takes_no_rng_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """OFF: ``generate_personas`` consumes ZERO module-global RNG (parity)."""
    fake = _CapturingPersonaFake([_P1, _P2, _P3])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    def _boom(*a, **k):
        raise AssertionError("random.shuffle/sample reached on the flag-OFF path")

    monkeypatch.setattr(setup_nodes.random, "shuffle", _boom)

    random.seed(BASE_SEED)
    state_before = random.getstate()
    generate_personas(_state_with_roles(), persona_diversity_enabled=False)
    assert random.getstate() == state_before, (
        "flag-OFF generate_personas drew from the module-global RNG"
    )


# ==========================================================================
# E. load_config default-on semantics for the four settings.
# ==========================================================================


def test_load_config_persona_diversity_default_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPHIA_PERSONA_DIVERSITY", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().persona_diversity_enabled is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off"])
def test_load_config_persona_diversity_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPHIA_PERSONA_DIVERSITY", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().persona_diversity_enabled is False


def test_load_config_persona_tunable_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "GRAPHIA_PERSONA_COLLISION_THRESHOLD",
        "GRAPHIA_PERSONA_REGEN_ATTEMPTS",
        "GRAPHIA_PERSONA_TEMPERATURE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    cfg = load_config()
    assert cfg.persona_collision_threshold == pytest.approx(0.6)
    assert cfg.persona_regen_attempts == 2
    assert cfg.persona_temperature == pytest.approx(1.0)


def test_load_config_persona_tunables_parse_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPHIA_PERSONA_COLLISION_THRESHOLD", "0.72")
    monkeypatch.setenv("GRAPHIA_PERSONA_REGEN_ATTEMPTS", "4")
    monkeypatch.setenv("GRAPHIA_PERSONA_TEMPERATURE", "0.9")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    cfg = load_config()
    assert cfg.persona_collision_threshold == pytest.approx(0.72)
    assert cfg.persona_regen_attempts == 4
    assert cfg.persona_temperature == pytest.approx(0.9)


# ==========================================================================
# F. Threading anti-drift — both builders carry the flag + tunables.
# ==========================================================================


@pytest.mark.parametrize(
    "param,default",
    [
        ("persona_diversity_enabled", True),
        ("persona_collision_threshold", 0.6),
        ("persona_regen_attempts", 2),
        ("persona_temperature", 1.0),
    ],
)
def test_assemble_graph_carries_persona_params(param: str, default) -> None:
    sig = inspect.signature(_assemble_graph)
    assert param in sig.parameters
    assert sig.parameters[param].default == default


@pytest.mark.parametrize(
    "param,default",
    [
        ("persona_diversity_enabled", True),
        ("persona_collision_threshold", 0.6),
        ("persona_regen_attempts", 2),
        ("persona_temperature", 1.0),
    ],
)
def test_build_runtime_graph_carries_persona_params(param: str, default) -> None:
    sig = inspect.signature(build_runtime_graph)
    assert param in sig.parameters
    assert sig.parameters[param].default == default


def test_build_runtime_graph_compiles_with_diversity_off(tmp_path: Path) -> None:
    """The Runtime builder compiles with the diversity flag off (anti-drift smoke)."""
    graph = build_runtime_graph(
        "thread-persona-off",
        tmp_path / "checkpoints",
        persona_diversity_enabled=False,
    )
    assert graph is not None
