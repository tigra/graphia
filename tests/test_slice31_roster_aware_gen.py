"""Spec 031 Slice 2 — roster-aware persona generation (prompt-capture test).

Spec 031 (*Distinct AI Personas Across the Roster*), Slice 2, Task 2 (tech-spec
§4, *Testing Strategy*; functional-spec §2.1 *Verifiability*). All-mocked: no
real model, no RNG.

The Slice-2 generation change (already implemented) makes ``generate_personas``
(`src/graphia/nodes/setup.py`) accumulate the personas created so far this game
and feed each subsequent ``_generate_one_persona(player, prior_personas)`` an
extra ``HumanMessage`` — the spec-031 "make this one clearly different" block,
built by ``_distinct_from_message`` from ``PERSONA_DISTINCT_FROM_TEMPLATE`` over
each prior persona's **table-facing** text only (``personality`` + ``manner`` +
``public_persona``; **never ``true_self``**). The block rides on BOTH the first
attempt and the corrective retry; the first AI player gets no block.

The generation prompt is not part of the public game stream/transcript, so
"verifiable in recorded data" (functional-spec §2.1 AC1) is realised here as a
**prompt-capture test at the ``get_large`` LLM boundary** — the same seam spec
019 used for its standings injection (``tests/test_recap_aware_reasoning.py``'s
``_CapturingDayFake``) and spec 016 used for persona generation
(``tests/test_personas.py``). A content-recording fake patched onto
``graphia.nodes.setup.get_large`` records the messages each persona call
receives; the assertions read those captured prompts.

Assertions (per the task + tech-spec §4):

1. The **2nd-and-later** AI player's generation messages **contain the
   distinct-from block**, and that block **lists the earlier characters'
   table-facing text** (their personality / manner / public_persona).
2. The **1st** AI player's messages do **NOT** carry the block (nothing yet to
   differ from).
3. **No ``true_self`` text appears in ANY generation prompt** — a Mafioso seeded
   first carries a distinctive ``true_self`` sentinel (its returned persona's
   ``secret_backstory``), and that sentinel never surfaces in any captured
   message (the §2.4 / spec-016 allegiance-hiding invariant, by construction).
4. The **corrective retry path** also carries the block — a forced first-attempt
   failure fires the retry, and the retry messages still include the
   distinct-from block.

The capturing fake replaces the ``graphia.nodes.setup.get_large`` binding the
autouse ``safe_llm`` net installs (persona generation is an existing call site —
see ``tests/conftest.py``), so the loud-failure default never fires here and real
Bedrock is never reached.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import graphia.nodes.setup as setup_nodes
from graphia.llm import Persona
from graphia.nodes.setup import generate_personas
from graphia.prompts import PERSONA_DISTINCT_FROM_TEMPLATE, PERSONA_SYSTEM
from graphia.state import GameState, PlayerState

# A sentinel that can ONLY reach a prompt via a Mafioso's hidden ``true_self``.
# It is the first AI's returned persona's ``secret_backstory`` (→ ``true_self``);
# if the threading ever leaked the hidden field into a later character's
# distinct-from block, this exact string would show up in a captured prompt.
_TRUE_SELF_SENTINEL = "ZZsecretZZ runs the smuggling ring beneath the old mill"


# Three personas the fake returns in call order, one per AI seat. Every
# table-facing field is distinctive (and shares no substring with the roster
# names) so its presence in a LATER player's distinct-from block is unambiguous.
# The FIRST persona is a Mafioso shape — its ``secret_backstory`` carries the
# true_self sentinel that must never thread forward.
_PERSONA_1 = Persona(
    personality="boisterous and quick to laugh",
    manner="speaks in loud sweeping declarations",
    public_backstory="the village blacksmith with soot on his hands",
    secret_backstory=_TRUE_SELF_SENTINEL,
)
_PERSONA_2 = Persona(
    personality="meticulous and slow to trust",
    manner="weighs each word and pauses before answering",
    public_backstory="a retired schoolteacher who keeps a tidy ledger",
    secret_backstory="",
)
_PERSONA_3 = Persona(
    personality="warm and endlessly curious about neighbours",
    manner="rambles cheerfully and circles back to old stories",
    public_backstory="the baker whose ovens scent the whole square at dawn",
    secret_backstory="",
)


class _CapturingPersonaFake:
    """A content-recording ``get_large()`` stand-in for the persona path.

    Production call shape is ``get_large().with_structured_output(Persona)
    .invoke(messages)`` inside ``_generate_one_persona``. This fake records every
    ``messages`` list it is handed (one ``.invoke`` per generation call, in
    insertion order) and returns scripted outputs popped FIFO — so a test can
    drive the REAL ``generate_personas`` and then inspect the actual prompts each
    AI player's persona call received.

    Each scripted output is either a ``Persona`` to return or an ``Exception`` to
    raise (e.g. to force a first-attempt failure and exercise the retry path).
    Once the queue drains the last ``Persona`` is replayed, like the conftest
    unified fake, so a test need not pre-script every seat.
    """

    def __init__(self, outputs: list[Persona | Exception]) -> None:
        self._outputs: list[Persona | Exception] = list(outputs)
        self._last: Persona | None = None
        self.messages_log: list[Any] = []
        self.call_count = 0

    def with_structured_output(self, schema: type) -> "_CapturingPersonaFake":
        return self

    def invoke(self, messages: Any) -> Persona:
        self.call_count += 1
        self.messages_log.append(messages)
        if not self._outputs:
            if self._last is None:
                raise AssertionError(
                    "_CapturingPersonaFake.invoke called but no scripted "
                    "outputs remain and no prior output to repeat"
                )
            return self._last
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        self._last = out
        return out


def _state_with_roles() -> GameState:
    """A hand-built post-``assign_roles`` state — human first, roles pinned.

    Driving ``generate_personas`` directly off a hand-built state keeps the test
    free of the role-deal shuffle (no module-global RNG) and of the graph: the
    node loops ``players`` in insertion order, skips the human, and calls
    ``_generate_one_persona`` per AI seat. The first AI is a Mafioso (so its
    generated persona carries the ``true_self`` sentinel); the rest are Citizens.
    Names share no substring with the persona prose so a threaded persona string
    is unambiguous to assert against.
    """
    roster = [
        PlayerState(id="p-human", name="Alice", role="law_abiding", is_human=True),
        PlayerState(id="p-1", name="Marco", role="mafia", is_human=False),
        PlayerState(id="p-2", name="Priya", role="law_abiding", is_human=False),
        PlayerState(id="p-3", name="Silas", role="law_abiding", is_human=False),
    ]
    return {"human_id": "p-human", "players": {p.id: p for p in roster}}


def _human_messages(messages: Any) -> list[str]:
    """Every ``HumanMessage`` content string in a captured prompt, in order."""
    return [m.content for m in messages if isinstance(m, HumanMessage)]


def _distinct_block_in(messages: Any) -> str | None:
    """Return the distinct-from ``HumanMessage`` content from a captured prompt.

    ``_distinct_from_message`` renders ``PERSONA_DISTINCT_FROM_TEMPLATE``, whose
    fixed leading sentence (the text before the ``{others}`` slot) is the stable
    structural marker the threading change is keyed on — a reword of the template
    intro breaks this finder, which is the point. Returns ``None`` when no
    HumanMessage carries that marker.
    """
    marker = PERSONA_DISTINCT_FROM_TEMPLATE.split("{others}")[0].strip()
    for content in _human_messages(messages):
        if marker in content:
            return content
    return None


# ===========================================================================
# 1. The distinct-from block is present for the 2nd+ player, absent for the 1st,
#    and lists the earlier characters' table-facing text.
# ===========================================================================


def test_first_player_has_no_distinct_from_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FIRST AI player's generation prompt carries no distinct-from block.

    There is nothing yet to differ from, so ``_distinct_from_message`` returns
    ``None`` and the node appends no extra message — the prompt is the plain
    spec-016 ``[SystemMessage(PERSONA_SYSTEM), HumanMessage(user_template)]``.
    """
    fake = _CapturingPersonaFake([_PERSONA_1, _PERSONA_2, _PERSONA_3])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    generate_personas(_state_with_roles())

    # First AI seat = first captured invoke.
    first_prompt = fake.messages_log[0]
    assert _distinct_block_in(first_prompt) is None, (
        "the first AI player must get NO distinct-from block"
    )
    # It is still a well-formed persona prompt: system + one user message only.
    assert isinstance(first_prompt[0], SystemMessage)
    assert first_prompt[0].content == PERSONA_SYSTEM
    assert len(_human_messages(first_prompt)) == 1


def test_second_player_block_lists_first_characters_table_facing_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SECOND AI player's prompt carries the block listing the 1st character.

    The block lists the earlier character's table-facing text — its personality,
    manner, and public_persona (the ``public_backstory`` the model returned).
    """
    fake = _CapturingPersonaFake([_PERSONA_1, _PERSONA_2, _PERSONA_3])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    generate_personas(_state_with_roles())

    # Second AI seat = second captured invoke.
    second_block = _distinct_block_in(fake.messages_log[1])
    assert second_block is not None, (
        "the second AI player must get the distinct-from block"
    )
    # It lists the FIRST character's table-facing fields.
    assert _PERSONA_1.personality in second_block
    assert _PERSONA_1.manner in second_block
    assert _PERSONA_1.public_backstory in second_block


def test_third_player_block_lists_both_earlier_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The THIRD AI player's block accumulates BOTH earlier characters.

    ``generate_personas`` accumulates prior personas as it loops, so by the third
    seat the block carries the table-facing text of seats one AND two.
    """
    fake = _CapturingPersonaFake([_PERSONA_1, _PERSONA_2, _PERSONA_3])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    generate_personas(_state_with_roles())

    third_block = _distinct_block_in(fake.messages_log[2])
    assert third_block is not None
    # Both earlier characters' table-facing text is present.
    for persona in (_PERSONA_1, _PERSONA_2):
        assert persona.personality in third_block
        assert persona.manner in third_block
        assert persona.public_backstory in third_block


# ===========================================================================
# 2. No true_self ever reaches any generation prompt.
# ===========================================================================


def test_no_true_self_text_appears_in_any_generation_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Mafioso's ``true_self`` sentinel never surfaces in ANY captured prompt.

    The first AI is a Mafioso whose returned persona carries the
    ``_TRUE_SELF_SENTINEL`` in its ``secret_backstory`` (→ ``true_self`` after
    conversion). ``_distinct_from_message`` threads only table-facing text
    (personality + manner + public_persona), so the secret must not appear in the
    second or third players' distinct-from blocks — nor anywhere in any prompt.
    This is the §2.4 / spec-016 allegiance-hiding invariant, enforced by
    construction (the hidden field is simply never rendered).
    """
    fake = _CapturingPersonaFake([_PERSONA_1, _PERSONA_2, _PERSONA_3])
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    result = generate_personas(_state_with_roles())

    # Sanity: the sentinel really did become the Mafioso's stored true_self —
    # otherwise the no-leak assertion below would be vacuously true.
    mafioso = result["players"]["p-1"]
    assert mafioso.persona is not None
    assert mafioso.persona.true_self == _TRUE_SELF_SENTINEL, (
        "the Mafioso's secret_backstory should have become its true_self"
    )

    # The sentinel appears in NO message of ANY captured generation prompt.
    for prompt in fake.messages_log:
        for message in prompt:
            assert _TRUE_SELF_SENTINEL not in str(message.content), (
                "a Mafioso true_self leaked into a generation prompt:\n"
                f"{message.content!r}"
            )


# ===========================================================================
# 3. The corrective retry path also carries the distinct-from block.
# ===========================================================================


def test_retry_path_also_carries_the_distinct_from_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced first-attempt failure fires the retry, which still carries the block.

    For the SECOND AI seat the fake raises on the first ``.invoke`` (the
    first-attempt persona call) and returns a valid persona on the second (the
    corrective retry). ``_generate_one_persona`` appends the distinct-from block
    to BOTH the first-attempt and the retry message lists, so the retry prompt —
    the LAST captured invoke for that seat — must still contain the block listing
    the first character.
    """
    # Seat 1: succeeds first try (1 invoke). Seat 2: raises, then succeeds
    # (2 invokes — the second is the corrective retry). Seat 3: succeeds.
    fake = _CapturingPersonaFake(
        [
            _PERSONA_1,
            RuntimeError("forced first-attempt failure on seat 2"),
            _PERSONA_2,
            _PERSONA_3,
        ]
    )
    monkeypatch.setattr(setup_nodes, "get_large", lambda: fake)

    result = generate_personas(_state_with_roles())

    # The forced failure fired the retry: seat 1 (1) + seat 2 (2) + seat 3 (1).
    assert fake.call_count == 4

    # The retry for seat 2 is the THIRD captured invoke (index 2): seat-1 attempt,
    # seat-2 first attempt (raised), seat-2 retry.
    retry_prompt = fake.messages_log[2]
    retry_block = _distinct_block_in(retry_prompt)
    assert retry_block is not None, (
        "the corrective retry prompt must still carry the distinct-from block"
    )
    assert _PERSONA_1.personality in retry_block
    assert _PERSONA_1.manner in retry_block
    assert _PERSONA_1.public_backstory in retry_block

    # And the retry never leaks the Mafioso true_self either.
    for message in retry_prompt:
        assert _TRUE_SELF_SENTINEL not in str(message.content)

    # Never-block guarantee held: seat 2 still got a valid persona from the retry.
    priya = result["players"]["p-2"]
    assert priya.persona is not None
    assert priya.persona.personality == _PERSONA_2.personality
