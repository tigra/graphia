"""Spec 034 (Diversified Persona Generation), Slice 2 tests — lexical regen.

Spec 034's safety net (functional-spec §2, tech-spec §2 B): a freshly-created
persona too **word-level**-similar to one already created this game is discarded
and regenerated (fresh shuffle + fresh archetype) up to a bounded number of
attempts; on cap-exhaustion the **least-similar** attempt is kept; the
deterministic ``_fallback_persona`` is the final last resort. "Too similar" is
the max ``difflib`` ratio of the candidate's table-facing text against the
accepted personas (reusing the spec-009 ``_mask_names`` / ``_normalize`` so a
"collision" speaks the same language as the recorded ``persona_lex_*`` metric),
above a configurable bar (default ~0.6).

All-mocked, no model, no RNG dependence beyond the seam draws (architecture §6).
The pure ``_persona_collision`` needs neither; the regen-loop tests drive the
REAL ``generate_personas`` with a scripted capturing fake (the same seam
``test_slice34_persona_diversity.py`` uses).

Test map (Slice 2):

A.  Pure ``_persona_collision`` — identical → 1.0; same-archetype (identical
    personality/manner, different backstory) → above the default bar;
    genuinely-different → below it; ``true_self`` never participates; name-masking
    applied; empty-accepted → 0.0.
B.  Regen loop — colliding-then-distinct → the collided candidate is REPLACED and
    the distinct one kept; cap-exhaustion (always-colliding) → the LEAST-similar
    attempt is kept and setup completes; generation failure → ``_fallback_persona``.
C.  Flag-OFF ⇒ no regeneration (a colliding candidate is kept as-is, spec 031).
"""

from __future__ import annotations

import random

import pytest

import graphia.nodes.setup as setup_nodes
from graphia.llm import Persona
from graphia.nodes.setup import (
    _fallback_persona,
    _persona_collision,
    generate_personas,
)
from graphia.state import GameState, PlayerPersona, PlayerState

BASE_SEED = 20250619


# --------------------------------------------------------------------------
# Persona builders for the pure-helper tests.
# --------------------------------------------------------------------------


def _pp(personality: str, manner: str, public: str, true_self: str = "") -> PlayerPersona:
    return PlayerPersona(
        personality=personality,
        manner=manner,
        public_persona=public,
        true_self=true_self,
    )


_DISTINCT_A = _pp(
    "boisterous and quick to laugh",
    "speaks in loud sweeping declarations",
    "the village blacksmith with soot on his hands",
)
_DISTINCT_B = _pp(
    "withdrawn and watchful, trusting no one",
    "murmurs short clipped phrases and avoids eye contact",
    "a reclusive clockmaker who rarely leaves the tower",
)
# Same-archetype twin of A: identical personality + manner, only the backstory
# differs (the recurring "honest, slow-talking librarian" failure mode).
_TWIN_A = _pp(
    "boisterous and quick to laugh",
    "speaks in loud sweeping declarations",
    "the harbour fisherman who mends his own nets",
)


# ==========================================================================
# A. Pure _persona_collision.
# ==========================================================================


def test_collision_empty_accepted_is_zero() -> None:
    """The first player can never collide — no accepted personas to compare."""
    assert _persona_collision(_DISTINCT_A, []) == 0.0


def test_collision_identical_text_is_one() -> None:
    """Identical table-facing text → ratio 1.0 (verbatim copy)."""
    assert _persona_collision(_DISTINCT_A, [_DISTINCT_A]) == pytest.approx(1.0)


def test_collision_same_archetype_above_default_bar() -> None:
    """Same-archetype twins (identical personality/manner) → above the ~0.6 bar."""
    score = _persona_collision(_TWIN_A, [_DISTINCT_A])
    assert score >= 0.6, (
        f"same-archetype twin should trip the default bar; got {score:.3f}"
    )
    assert score < 1.0, "different backstory should keep it below a verbatim copy"


def test_collision_genuinely_different_below_default_bar() -> None:
    """Two genuinely-different characters → below the default bar."""
    score = _persona_collision(_DISTINCT_B, [_DISTINCT_A])
    assert score < 0.6, f"genuinely-different pair should not trip the bar; got {score:.3f}"


def test_collision_takes_max_over_accepted() -> None:
    """The collision is the MAX ratio over the accepted set (worst pair wins)."""
    # B is distinct from A; the twin collides with A. Candidate = twin; the max
    # over [A, B] is the high A-vs-twin ratio, not the low B-vs-twin ratio.
    score = _persona_collision(_TWIN_A, [_DISTINCT_B, _DISTINCT_A])
    assert score >= 0.6


def test_collision_ignores_true_self() -> None:
    """``true_self`` never participates — only table-facing text is compared.

    Two personas with IDENTICAL table-facing text but wildly different
    ``true_self`` still collide at 1.0; conversely, sharing only a ``true_self``
    does not raise the score.
    """
    a = _pp("calm", "measured", "the baker", true_self="ZZ secret ring boss ZZ")
    b = _pp("calm", "measured", "the baker", true_self="totally unrelated secret")
    assert _persona_collision(a, [b]) == pytest.approx(1.0)

    # Different table-facing text, identical secret → low score (secret ignored).
    c = _pp("loud", "rambling", "the sailor", true_self="ZZ secret ring boss ZZ")
    d = _pp("quiet", "terse", "the monk", true_self="ZZ secret ring boss ZZ")
    assert _persona_collision(c, [d]) < 0.6


def test_collision_applies_name_masking() -> None:
    """AI names are masked before comparison (spec-009 parity).

    Two personas whose ONLY textual difference is the embedded player name read as
    near-identical once names are masked — so a shared format can't dodge the bar
    by swapping a name. Pass the AI names so masking fires.
    """
    a = _pp("watchful", "speaks plainly", "Marco runs the mill by the river")
    b = _pp("watchful", "speaks plainly", "Priya runs the mill by the river")
    masked_score = _persona_collision(a, [b], ai_names={"Marco", "Priya"})
    assert masked_score == pytest.approx(1.0), (
        f"name-masked identical-format personas should collide; got {masked_score:.3f}"
    )


# --------------------------------------------------------------------------
# B/C. Regen-loop integration — a scripted capturing persona fake.
# --------------------------------------------------------------------------


class _ScriptedPersonaFake:
    """A FIFO ``get_persona_model`` stand-in for the regen-loop tests.

    Serves scripted ``Persona`` outputs in call order; replays the last when
    drained. Records ``call_count`` so a test can assert how many generation
    attempts the regen loop made.
    """

    def __init__(self, outputs: list[Persona | Exception]) -> None:
        self._outputs: list[Persona | Exception] = list(outputs)
        self._last: Persona | None = None
        self.call_count = 0

    def with_structured_output(self, schema: type) -> "_ScriptedPersonaFake":
        return self

    def invoke(self, messages) -> Persona:
        self.call_count += 1
        if not self._outputs:
            if self._last is None:
                raise AssertionError("no scripted outputs remain")
            return self._last
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        self._last = out
        return out


# Persona-schema (model-output) shapes. ``public_backstory`` → ``public_persona``.
def _persona_out(personality: str, manner: str, public: str) -> Persona:
    return Persona(
        personality=personality,
        manner=manner,
        public_backstory=public,
        secret_backstory="",
    )


_OUT_A = _persona_out(
    "boisterous and quick to laugh",
    "speaks in loud sweeping declarations",
    "the village blacksmith with soot on his hands",
)
# A verbatim copy of A (collides at 1.0).
_OUT_A_COPY = _persona_out(
    "boisterous and quick to laugh",
    "speaks in loud sweeping declarations",
    "the village blacksmith with soot on his hands",
)
# Genuinely-different from A (below the bar).
_OUT_DISTINCT = _persona_out(
    "withdrawn and watchful, trusting no one",
    "murmurs short clipped phrases and avoids eye contact",
    "a reclusive clockmaker who rarely leaves the tower",
)
# A second distinct one (for least-similar tracking).
_OUT_LESS_SIMILAR = _persona_out(
    "sharp-tongued and impatient with fools",
    "fires off rapid sarcastic asides",
    "a market trader who haggles over every coin",
)


def _two_ai_state() -> GameState:
    """Human + two AI Citizens — seat 2 can collide with seat 1."""
    roster = [
        PlayerState(id="p-human", name="Alice", role="law_abiding", is_human=True),
        PlayerState(id="p-1", name="Marco", role="law_abiding", is_human=False),
        PlayerState(id="p-2", name="Priya", role="law_abiding", is_human=False),
    ]
    return {"human_id": "p-human", "players": {p.id: p for p in roster}}


def _install(monkeypatch: pytest.MonkeyPatch, fake: _ScriptedPersonaFake) -> None:
    monkeypatch.setattr(setup_nodes, "get_persona_model", lambda temperature: fake)


def test_colliding_then_distinct_keeps_the_distinct_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat 1 → A; seat 2 → (A-copy collides) then a distinct one → distinct kept.

    The collided candidate is REPLACED by the regenerated distinct persona — the
    over-similar one never ships (functional-spec §2 AC).
    """
    fake = _ScriptedPersonaFake([_OUT_A, _OUT_A_COPY, _OUT_DISTINCT])
    _install(monkeypatch, fake)

    random.seed(BASE_SEED)
    result = generate_personas(
        _two_ai_state(),
        persona_diversity_enabled=True,
        persona_collision_threshold=0.6,
        persona_regen_attempts=2,
    )

    seat1 = result["players"]["p-1"].persona
    seat2 = result["players"]["p-2"].persona
    assert seat1 is not None and seat2 is not None
    # Seat 2 kept the DISTINCT regeneration, not the colliding copy.
    assert seat2.public_persona == _OUT_DISTINCT.public_backstory
    # And it is genuinely below the bar against seat 1.
    assert _persona_collision(seat2, [seat1], ai_names={"Marco", "Priya"}) < 0.6
    # Seat 1 (1 call) + seat 2 (collide + regen = 2 calls) = 3 generation calls.
    assert fake.call_count == 3


def test_cap_exhaustion_keeps_least_similar_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Always-colliding seat 2 → setup still completes, keeping the least-similar.

    Seat 2 always returns an over-similar persona, but the attempts differ in how
    similar: a verbatim copy (1.0) then two less-similar-but-still-colliding ones.
    On cap-exhaustion the LEAST-similar attempt is kept (never the verbatim copy),
    and the node returns a full roster — it never hangs.
    """
    # A same-archetype-but-not-verbatim twin (collides ≥ 0.6 but < 1.0).
    twin = _persona_out(
        "boisterous and quick to laugh",
        "speaks in loud sweeping declarations",
        "the harbour fisherman who mends his own nets",
    )
    # Seat 1 → A. Seat 2: verbatim copy (1.0), then twin, then twin again — all
    # colliding, but the twin is strictly less similar than the verbatim copy.
    fake = _ScriptedPersonaFake([_OUT_A, _OUT_A_COPY, twin, twin])
    _install(monkeypatch, fake)

    random.seed(BASE_SEED)
    result = generate_personas(
        _two_ai_state(),
        persona_diversity_enabled=True,
        persona_collision_threshold=0.6,
        persona_regen_attempts=2,
    )

    seat1 = result["players"]["p-1"].persona
    seat2 = result["players"]["p-2"].persona
    assert seat1 is not None and seat2 is not None
    # Setup completed with a full roster (both seats carry a persona).
    assert all(
        p.persona is not None
        for p in result["players"].values()
        if not p.is_human
    )
    # The least-similar attempt (the twin) was kept, NOT the verbatim copy.
    assert seat2.public_persona == twin.public_backstory
    # 1 (seat1) + 3 (seat2: initial + 2 regens) = 4 generation calls — bounded.
    assert fake.call_count == 4


def test_generation_failure_falls_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When generation itself fails (model always raises), seat 2 → fallback.

    A true generation failure is distinct from a collision: every attempt raises,
    so ``_generate_one_persona`` returns the deterministic ``_fallback_persona``,
    and the regen loop keeps that fallback (the last resort). The node never
    raises and setup completes.
    """
    # Seat 1 succeeds (A); seat 2 always raises (every attempt + its retry).
    fake = _ScriptedPersonaFake(
        [_OUT_A] + [RuntimeError("model down")] * 20
    )
    _install(monkeypatch, fake)

    random.seed(BASE_SEED)
    result = generate_personas(
        _two_ai_state(),
        persona_diversity_enabled=True,
        persona_collision_threshold=0.6,
        persona_regen_attempts=2,
    )

    seat2_player = result["players"]["p-2"]
    assert seat2_player.persona == _fallback_persona(seat2_player), (
        "a true generation failure must fall to the deterministic fallback"
    )


def test_flag_off_does_not_regenerate_a_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFF: a colliding seat-2 candidate is KEPT as-is (spec-031 — no regen).

    With diversity off there is no collision check, so the very first persona the
    model returns for seat 2 is kept even if it is a verbatim copy of seat 1 —
    exactly the pre-034 behaviour. Exactly one generation call per seat.
    """
    fake = _ScriptedPersonaFake([_OUT_A, _OUT_A_COPY, _OUT_DISTINCT])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    def _boom_persona(temperature: float):
        raise AssertionError("get_persona_model must not be used on the OFF path")

    monkeypatch.setattr(setup_nodes, "get_persona_model", _boom_persona)

    result = generate_personas(
        _two_ai_state(), persona_diversity_enabled=False
    )

    seat2 = result["players"]["p-2"].persona
    assert seat2 is not None
    # The colliding copy was kept (no regeneration) — spec-031 behaviour.
    assert seat2.public_persona == _OUT_A_COPY.public_backstory
    # Exactly one call per seat — no regeneration attempts.
    assert fake.call_count == 2
