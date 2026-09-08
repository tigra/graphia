"""Spec 040 (The Moderator's Closing Story), Task 3.3 — the PROMPT ASSEMBLY.

WHAT THIS FILE OWNS. The five slots of ``STORY_USER_TEMPLATE`` as
``endgame:_ai_story`` fills them: that each is filled by the helper §2.2 names
(so the reused ones cannot drift from the facts message), that the human seat is
marked, that a persona survives the checkpoint ``dict`` shape, that the BUDGET
ORDER is private-first, and that no whisper can reach the prompt.

WHAT IT DOES NOT OWN. ``tests/test_spec040_story_private_block.py`` (Task 3.2)
owns the private renderer's own grouping / attribution / windowing rules and
``tests/test_spec040_private_record_walk.py`` (Task 3.1) owns the cursor walk;
neither is re-asserted here. Task 3.4 owns the whisper-exclusion assertion as a
deliverable and Task 3.6 owns the game-level material assertions (both channels
reach a driven game's story, an early-death contributor, the human's structural
absence). The whisper case below is deliberately present anyway, because Task
3.3 is what *implements* the exclusion and a privacy property should not sit
unproven for a task. **This line used to say Task 3.4's fuller version would
"replace rather than repeat" it; Task 3.4 deliberately KEPT it, 2026-09-08.**
Two measured reasons: it is the only witness anywhere for the absence of the
``"Moderator (private)"`` label ``_render_context`` gives an admitted whisper,
and it is an INDEPENDENT one — hand-typed sentinels over this file's own
fixtures, so a defect in
``tests/test_spec040_story_whisper_privacy.py``'s shared state builder cannot
hide behind it. It went red under two of that file's six mutations. What it
cannot catch is recorded there rather than here.

THE TEST LEVEL IS A DELIBERATE CHOICE, and Slice 3's preamble is why. It warns
against copying ``tests/test_spec040_story_ui_order.py``'s live-dispatcher
pattern — a ~30%-per-test flake in which the human is killed on Night 1 —
and points at ``dynamic_night_pointing`` installed before the name submit as the
race-free alternative for anything that must drive a game. **Nothing here drives
a game**: every property is a property of one pure assembly, so it is asserted
by calling ``_ai_story`` directly against a hand-built state with a recording
stand-in for the large tier. That puts the trap out of reach rather than
avoiding it, and keeps the file fast enough to run on every change.

WHY EVERY COMPOSITION GOES THROUGH :func:`_compose`. ``moderator_story`` and
``_ai_story`` are total by construction, so ANY mistake in a fixture here — a
persona shape that raises, a state key spelled wrong — degrades into a
legitimate no-story ending and would leave the assertions below vacuous. That
helper asserts the call was reached and the schema bound before it hands back a
prompt, which is the same reasoning ``tests/test_spec040_story_node.py`` records
for its ``calls_by_schema[Story]`` counters.

EVERY PROPERTY BELOW WAS MUTATION-VERIFIED. Each change was applied to the
production code for real, from 15 green in this file, measured, and reverted
(2026-09-08). Recorded because a test that passes against the opposite
behaviour is worthless, and this suite has been bitten by exactly that twice
(Task 1.7's inverted assertion, spec 041's three exception-stubbing tests):

* ``token_budget=STORY_TOKEN_BUDGET`` passed to ``_render_context`` instead of
  the remainder — **THE BUDGET ORDER REVERSED, the silent failure this task
  exists to prevent** — **3 failed**: both budget tests and the
  over-budget-private-material one.
* ``_persona_field`` swapped for bare attribute access (``persona.manner``) —
  **1 failed**, the post-checkpoint-``dict`` param, and it failed through
  ``_compose``'s "expected exactly one invoke, got 0" rather than on any text
  assertion. That is the real-world symptom written down: on a resumed game
  the ``AttributeError`` is swallowed and the ENDING SIMPLY HAS NO STORY.
* the ``(the person playing)`` marker dropped from ``_story_roster_block`` —
  **2 failed**.
* ``speaker_id`` changed from ``""`` to a real player id
  (``next(iter(players), "")``) — **1 failed**, on the human's own role-reveal
  whisper appearing in the prompt.
* ``_winner_line`` re-spelled as a local ``f"{winner} won."`` — **1 failed**.
* ``_format_kill_line`` re-implemented inside ``_story_events_block`` — **1
  failed**, the dedicated kill-line test, while the slot-equality test stayed
  GREEN. That is why both exist: an equality against the block's own helper
  cannot see the helper being replaced, so the reuse needs its own witness.
* ``STORY_CONTEXT_WINDOW`` frozen into a default argument at import time
  instead of read at call time — **1 failed**, the window test. Pinned because
  Task 3.5's config threading depends on the call-time read.
* the private block dropped from the prompt (``private_record=""``) — **6
  failed**, which is what says the material assertions here are not vacuous.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import graphia.nodes.endgame as endgame
from graphia.config import _DEFAULT_CONTEXT_WINDOW
from graphia.llm import Story
from graphia.nodes.day import _estimate_tokens
from graphia.nodes.endgame import (
    STORY_PRIVATE_LEGEND,
    STORY_ROSTER_HUMAN_MARKER,
    _format_kill_line,
    _story_events_block,
    _story_private_block,
    _story_roster_block,
    _winner_line,
)
from graphia.prompts import STORY_USER_TEMPLATE
from graphia.state import KillRecord, PlayerPersona, PlayerState

SCRIPTED_STORY = "A story about the game.\n\nAnd its second paragraph."

# The five slot names, taken as the vocabulary this file asserts over. Written
# once: :func:`_slot_text` derives every anchor it needs from the template
# itself, so a Slice 4 rewording of the LABELS breaks nothing here.
_SLOTS = ("outcome", "roster", "events", "context", "private_record")


# ==========================================================================
# Shared helpers
# ==========================================================================


def _persona(prefix: str) -> PlayerPersona:
    return PlayerPersona(
        personality=f"{prefix}-personality",
        manner=f"{prefix}-manner",
        public_persona=f"{prefix}-public",
        true_self=f"{prefix}-true-self",
    )


def _p(
    pid: str,
    name: str,
    *,
    role: str = "law_abiding",
    is_human: bool = False,
    is_alive: bool = True,
    persona: Any = None,
) -> PlayerState:
    """One player. ``persona`` is passed through untouched.

    Untouched ON PURPOSE: the checkpoint round-trip hands a ``PlayerPersona``
    back as a plain ``dict``, and this file has to be able to build that shape.
    """
    return PlayerState(
        id=pid,
        name=name,
        role=role,  # type: ignore[arg-type]
        is_human=is_human,
        is_alive=is_alive,
        persona=persona,
    )


def _roster(*players: PlayerState) -> dict[str, PlayerState]:
    return {p.id: p for p in players}


def _table() -> dict[str, PlayerState]:
    """The human plus two AI players, one per side, one of them dead."""
    return _roster(
        _p("h", "Alexey", is_human=True),
        _p("m1", "Ivy", role="mafia", persona=_persona("ivy")),
        _p("c1", "Bram", is_alive=False, persona=_persona("bram")),
    )


def _state(**overrides: Any) -> dict:
    state: dict = {
        "players": _table(),
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": {},
        "winner": "law_abiding",
    }
    state.update(overrides)
    return state


class _CapturingStoryFake:
    """A recording ``get_large()`` stand-in — the prompt is the measurement.

    Mirrors ``tests/test_slice39_diary_before_night.py:_CapturingDiaryFake``:
    three counters, because the assembly must be reached at all before any
    assertion on its output means anything.
    """

    def __init__(self, text: str = SCRIPTED_STORY) -> None:
        self._text = text
        self.messages_log: list[Any] = []
        self.bindings = 0
        self.schemas: list[type] = []

    def with_structured_output(
        self, schema: type, **kwargs: object
    ) -> "_CapturingStoryFake":
        self.bindings += 1
        self.schemas.append(schema)
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        return Story(text=self._text)


def _compose(monkeypatch: pytest.MonkeyPatch, state: dict) -> str:
    """Run ``_ai_story`` against ``state`` and return the rendered user prompt.

    Asserts the call was REACHED and ``Story`` was bound before returning, so a
    fixture mistake cannot masquerade as the legitimate no-story ending
    ``_ai_story``'s totality produces (see this module's docstring).
    """
    fake = _CapturingStoryFake()
    monkeypatch.setattr(endgame, "get_large", lambda: fake)
    text = endgame._ai_story(state)  # type: ignore[arg-type]
    assert fake.schemas == [Story], (
        "the large tier was never reached with the Story schema, so this test "
        f"would measure nothing — bindings={fake.bindings}, "
        f"schemas={fake.schemas}, story={text!r}"
    )
    assert len(fake.messages_log) == 1, (
        f"expected exactly one invoke, got {len(fake.messages_log)}"
    )
    prompt = fake.messages_log[0][1]
    assert isinstance(prompt, HumanMessage)
    assert isinstance(prompt.content, str)
    return prompt.content


def _slot_text(prompt: str, slot: str) -> str:
    """The rendered text of one ``STORY_USER_TEMPLATE`` slot.

    THE ANCHORS COME FROM THE TEMPLATE, NEVER RETYPED HERE. The template is
    formatted with a sentinel in the slot of interest and every other slot
    blank; the last line before the sentinel and the first non-blank line after
    it are then the slot's own delimiters in the real prompt. So Slice 4 (or
    anyone) may reword the labels and reorder the slots without touching this
    file — and a slot that stopped being interpolated at all fails loudly here
    rather than silently matching an empty string.
    """
    assert slot in _SLOTS, f"unknown slot {slot!r}"
    sentinel = "\x00SLOT\x00"
    skeleton = STORY_USER_TEMPLATE.format(
        **{name: (sentinel if name == slot else "") for name in _SLOTS}
    )
    before, after = skeleton.split(sentinel)
    label = before.rstrip("\n").splitlines()[-1]
    following = next((ln for ln in after.splitlines() if ln.strip()), "")
    assert label in prompt, f"slot {slot!r}'s label {label!r} is not in the prompt"
    rest = prompt[prompt.index(label) + len(label) :]
    end = rest.index(following) if following else len(rest)
    return rest[:end].strip("\n")


def _context_lines(prompt: str) -> list[str]:
    return [ln for ln in _slot_text(prompt, "context").splitlines() if ln.strip()]


def _public(index: int) -> SystemMessage:
    """One bulky, individually identifiable public Moderator line.

    ~300 chars each so a realistic number of them crosses the real
    ``STORY_TOKEN_BUDGET`` without the test having to move the governors — the
    budget arithmetic is then measured against the values the game ships.
    """
    # No trailing space: ``_render_context`` strips each rendered line and
    # ``_story_private_line`` strips each entry, so a fixture that ended in
    # whitespace would make every ``in``/equality assertion below fail for a
    # reason that has nothing to do with the property under test.
    return SystemMessage(content=f"public-{index:04d} " + "history " * 35 + "history")


# ==========================================================================
# 1 — EVERY SLOT IS FILLED, BY THE HELPER §2.2 NAMES
# ==========================================================================


def test_every_slot_is_filled_by_its_own_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All five slots, each equal to the block its named helper renders.

    The three REUSED helpers are what this pins hardest (§2.2): ``_winner_line``
    so the story's outcome and the facts message's cannot drift, and
    ``_format_kill_line`` (through ``_story_events_block``) so a death reads the
    same in both messages.
    """
    players = _table()
    kill_log: list[KillRecord] = [
        {"cycle": 1, "name": "Bram", "cause": "night", "role": "law_abiding"},
    ]
    thoughts = {"m1": ["Ivy's reflection"]}
    diaries = {"m1": [{"day": 1, "thoughts_before": 1, "text": "Ivy's diary"}]}
    state = _state(
        players=players,
        kill_log=kill_log,
        private_thoughts=thoughts,
        private_diaries=diaries,
        messages=[SystemMessage(content="Day 1 begins.")],
        winner="mafia",
    )

    prompt = _compose(monkeypatch, state)

    assert _slot_text(prompt, "outcome") == _winner_line("mafia")
    assert _slot_text(prompt, "roster") == _story_roster_block(players)
    assert _slot_text(prompt, "events") == _story_events_block(kill_log, players)
    assert _slot_text(prompt, "private_record") == _story_private_block(
        players, thoughts, diaries
    )
    assert _slot_text(prompt, "context") == "Moderator: Day 1 begins."
    # No unfilled slot survived — a ``KeyError`` would have been swallowed into
    # a no-story ending, but a template grown with a slot nobody fills would
    # not, and this is what catches it.
    for slot in _SLOTS:
        assert "{%s}" % slot not in prompt


def test_the_events_slot_is_the_facts_messages_own_kill_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_format_kill_line`` per record, verbatim — reuse, not re-implementation."""
    players = _table()
    kill_log: list[KillRecord] = [
        {"cycle": 1, "name": "Bram", "cause": "night", "role": "law_abiding"},
        {"cycle": 2, "name": "Ivy", "cause": "execution", "role": "mafia"},
    ]
    prompt = _compose(monkeypatch, _state(players=players, kill_log=kill_log))

    for record in kill_log:
        assert _format_kill_line(record, players) in prompt


def test_the_private_material_is_read_from_both_channels_keyed_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The block is handed ``players`` — no spec section says it needs the roster.

    Task 3.2 found it: the channels are keyed on ``player.id`` while the heading
    shows ``player.name``, so a block called without the roster could render
    neither the heading nor the grouping order. Asserting the NAME appears from
    material stored under the ID is what proves the roster was passed.
    """
    prompt = _compose(
        monkeypatch,
        _state(
            private_thoughts={"m1": ["ivy-reflection-text"]},
            private_diaries={
                "c1": [{"day": 2, "thoughts_before": 0, "text": "bram-diary-text"}]
            },
        ),
    )

    block = _slot_text(prompt, "private_record")
    assert "Ivy wrote:" in block
    assert "ivy-reflection-text" in block
    assert "Bram wrote:" in block
    assert "bram-diary-text" in block
    assert STORY_PRIVATE_LEGEND in block


# ==========================================================================
# 2 — THE HUMAN SEAT IS MARKED (Task 3.3's deliverable; Slice 4 depends on it)
# ==========================================================================


def test_the_human_seat_is_marked_and_no_other_seat_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one marked line, and it is the human's.

    Slice 1's roster emitted name / side / fate only, and a live run (Task 2.3)
    had the story narrate the reader in the third person as "Alexey, another
    innocent citizen". This marker is the only thing in the whole prompt that
    identifies which seat is the person being told the story, which is why
    Slice 4's human clause cannot be shown to DO anything without it.
    """
    prompt = _compose(monkeypatch, _state())

    marked = [
        line
        for line in _slot_text(prompt, "roster").splitlines()
        if STORY_ROSTER_HUMAN_MARKER in line
    ]
    assert len(marked) == 1, f"expected one marked seat, got {marked}"
    assert "Alexey" in marked[0]
    assert "Ivy" not in marked[0] and "Bram" not in marked[0]


def test_the_marker_moves_with_the_flag_not_with_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``is_human`` is what marks the seat — not position, not a name pattern."""
    players = _roster(
        _p("a1", "Ivy", role="mafia", persona=_persona("ivy")),
        _p("a2", "Bram", persona=_persona("bram")),
        _p("h", "Alexey", is_human=True),
    )
    prompt = _compose(monkeypatch, _state(players=players))

    roster = _slot_text(prompt, "roster")
    assert roster.count(STORY_ROSTER_HUMAN_MARKER) == 1
    assert roster.splitlines()[-1].startswith("• Alexey")
    assert STORY_ROSTER_HUMAN_MARKER in roster.splitlines()[-1]


# ==========================================================================
# 3 — THE PERSONA, THROUGH ``_persona_field`` (mandatory, not stylistic)
# ==========================================================================


@pytest.mark.parametrize(
    "shape",
    ["dataclass", "checkpoint_dict"],
    ids=["in-process PlayerPersona", "post-checkpoint dict"],
)
def test_a_persona_renders_in_either_shape(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """The SECOND reader of persona fields out of checkpointed state.

    ``_persona_field``'s own docstring records that the LangGraph serde returns
    a ``PlayerPersona`` as a plain ``dict`` after a round-trip and that
    ``end_screen`` was the first reader to have to accept both. This is the
    second, and a bare attribute access would ``AttributeError`` — swallowed by
    ``_ai_story`` into a MISSING ENDING on any resumed game, which is why
    ``_compose`` asserts the call was reached rather than only inspecting text.
    """
    persona: Any = _persona("ivy")
    if shape == "checkpoint_dict":
        persona = {
            "personality": "ivy-personality",
            "manner": "ivy-manner",
            "public_persona": "ivy-public",
            "true_self": "ivy-true-self",
        }
    players = _roster(
        _p("h", "Alexey", is_human=True),
        _p("m1", "Ivy", role="mafia", persona=persona),
    )

    roster = _slot_text(_compose(monkeypatch, _state(players=players)), "roster")

    assert "ivy-public" in roster
    assert "ivy-personality" in roster
    assert "ivy-manner" in roster
    assert "ivy-true-self" in roster


def test_a_citizens_true_self_is_never_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§2.2 excludes it — empty by construction, and a Citizen has no cover."""
    players = _roster(
        _p("h", "Alexey", is_human=True),
        _p("c1", "Bram", persona=_persona("bram")),
    )
    roster = _slot_text(_compose(monkeypatch, _state(players=players)), "roster")

    assert "bram-public" in roster
    assert "bram-true-self" not in roster


def test_a_seat_with_no_persona_still_gets_its_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The human (never given one) and a fallback-path AI (never got one)."""
    players = _roster(
        _p("h", "Alexey", is_human=True),
        _p("m1", "Ivy", role="mafia", persona=None),
    )
    roster = _slot_text(_compose(monkeypatch, _state(players=players)), "roster")

    assert len(roster.splitlines()) == 2
    assert "• Alexey — Law-abiding Citizen — alive at the end" in roster
    assert "• Ivy — Mafia — alive at the end" in roster


# ==========================================================================
# 4 — THE BUDGET ORDER: private measured FIRST, public gets the remainder
# ==========================================================================


def _budget_state(*, private: bool, lines: int = 170) -> dict:
    """A public history long enough to cross ``STORY_TOKEN_BUDGET`` on its own.

    ``private`` toggles a bulky private record on ONE author while leaving
    everything else identical, so the only difference between the two
    compositions is the private block's cost.
    """
    diaries = (
        {
            "m1": [
                {
                    "day": day,
                    "thoughts_before": day,
                    # No trailing space — see ``_public``.
                    "text": f"diary-{day:02d} " + "secret " * 59 + "secret",
                }
                for day in range(1, 9)
            ]
        }
        if private
        else {}
    )
    return _state(
        messages=[_public(i) for i in range(lines)],
        private_diaries=diaries,
    )


def test_the_private_block_is_measured_first_and_the_public_history_is_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE DIRECTION OF THE BUDGET, and reversing it fails this test.

    Two compositions differing only in whether a private record exists. The
    private material must arrive whole and the PUBLIC history must be the thing
    that shrinks to pay for it — §2.2's rationale that the story "earns its
    place from the private material".

    Reversed (the full ``STORY_TOKEN_BUDGET`` handed to ``_render_context``,
    ignoring what the private block costs) both compositions render an
    IDENTICAL public history and the strict inequality below fails. That is the
    silent failure this task exists to prevent: a well-formed prompt, a
    well-formed story, and nothing in it the player had not already read.
    """
    without = _compose(monkeypatch, _budget_state(private=False))
    with_private = _compose(monkeypatch, _budget_state(private=True))

    # The public history really is being trimmed in BOTH — otherwise the
    # comparison below would be measuring nothing.
    assert len(_context_lines(without)) < 170
    assert len(_context_lines(with_private)) < len(_context_lines(without)), (
        "the private block cost the public history nothing, so the public "
        "history was budgeted as if the private material were free"
    )
    # And the private material is whole: every entry, uncut.
    for day in range(1, 9):
        assert f"diary-{day:02d} " + "secret " * 59 + "secret" in with_private


def test_the_public_historys_budget_is_the_remainder_after_the_private_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arithmetic, measured with the same estimator the code uses.

    ``_estimate_tokens`` is imported rather than re-derived, so this asserts the
    relation §2.2 states — public budget == ``story_token_budget`` MINUS the
    measured private block — and not a re-guess of the chars-per-token ratio.
    """
    state = _budget_state(private=True)
    prompt = _compose(monkeypatch, state)

    private_block = _slot_text(prompt, "private_record")
    private_tokens = _estimate_tokens(private_block)
    remainder = endgame.STORY_TOKEN_BUDGET - private_tokens
    assert private_tokens > 0 and remainder > 0, (
        "fixture no longer exercises the subtraction"
    )
    rendered_context = _slot_text(prompt, "context")
    assert _estimate_tokens(rendered_context) <= remainder


def test_the_oldest_public_lines_are_the_ones_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oldest-first, so a long game loses early DIALOGUE and keeps every diary.

    §2.2's accepted trade-off, and the reason a head-and-tail keep was rejected
    (it needs a second trimming implementation plus an elision marker the model
    may narrate as an event).
    """
    prompt = _compose(monkeypatch, _budget_state(private=True))
    context = _slot_text(prompt, "context")

    assert "public-0169" in context, "the newest public line must survive"
    assert "public-0000" not in context, "the oldest public line must be dropped"


def test_private_material_larger_than_the_whole_budget_is_still_not_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural protection, at the limit: the private block is never trimmed.

    A private record on its own bigger than ``STORY_TOKEN_BUDGET`` leaves the
    public history nothing. ``_render_context``'s own ``len(lines) > 1`` guard
    then keeps exactly the newest line — strictly better than an empty history —
    and the private material still arrives whole.
    """
    # ~54k chars ≈ 18k tokens, past the whole budget on its own. No trailing
    # space — see ``_public``.
    huge = "enormous " * 5999 + "enormous"
    state = _state(
        messages=[_public(i) for i in range(20)],
        private_diaries={"m1": [{"day": 1, "thoughts_before": 0, "text": huge}]},
    )
    prompt = _compose(monkeypatch, state)

    assert huge in prompt
    assert _context_lines(prompt) == ["Moderator: " + _public(19).content]


def test_the_story_window_is_its_own_and_is_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the Day's window (§2.2), and overridable — which Task 3.5 needs.

    The Day's count expresses "the recent discussion"; a retrospective often
    needs Day 1, so the story gets its own, larger one. Reading it at call time
    is what lets Task 3.5 replace the constant with a bound config value without
    touching the assembly, and is why the override below is asserted rather than
    the literal value: the number moves in Task 3.5, the relation does not.
    """
    assert endgame.STORY_CONTEXT_WINDOW > _DEFAULT_CONTEXT_WINDOW

    monkeypatch.setattr(endgame, "STORY_CONTEXT_WINDOW", 2)
    prompt = _compose(
        monkeypatch, _state(messages=[_public(i) for i in range(5)])
    )

    lines = _context_lines(prompt)
    assert len(lines) == 2
    assert "public-0003" in lines[0] and "public-0004" in lines[1]


# ==========================================================================
# 5 — NO WHISPER REACHES THE PROMPT (implemented here; Task 3.4 asserts it)
# ==========================================================================


def test_no_whisper_text_reaches_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``speaker_id=""`` matches no player, so EVERY whisper is dropped.

    A PRIVACY REQUIREMENT rather than a convenience (§2.2): the human's private
    role reveal and the Mafia teammate intros must not enter a prompt whose
    output is shown to the player. The Moderator gets the conspiracy from the
    Mafiosos' DIARIES instead, which is the point of the feature — so the diary
    text below must be present in the very same prompt the whispers are absent
    from, or this test would pass on a prompt with no material at all.
    """
    reveal = "ROLE-REVEAL-SECRET: you are a Law-abiding Citizen"
    intro = "MAFIA-INTRO-SECRET: your teammate is Ivy"
    state = _state(
        messages=[
            SystemMessage(content="Night falls."),
            SystemMessage(
                content=reveal, additional_kwargs={"private_to": "h"}
            ),
            SystemMessage(
                content=intro, additional_kwargs={"private_to": "m1"}
            ),
            AIMessage(content="I trust Bram.", name="Ivy"),
        ],
        private_diaries={
            "m1": [
                {
                    "day": 1,
                    "thoughts_before": 0,
                    "text": "DIARY-CONSPIRACY: Bram dies tonight",
                }
            ]
        },
    )

    prompt = _compose(monkeypatch, state)

    assert reveal not in prompt
    assert intro not in prompt
    assert "MAFIA-INTRO-SECRET" not in prompt
    assert "ROLE-REVEAL-SECRET" not in prompt
    # The label ``_render_context`` gives a whisper it DID admit. Its absence is
    # a second, independent witness: a leak through a matching speaker id would
    # show up here even if the secret text itself were reworded.
    assert "Moderator (private)" not in prompt
    # ...while the public history and the conspiracy-by-diary are both here.
    assert "Moderator: Night falls." in prompt
    assert "Ivy: I trust Bram." in prompt
    assert "DIARY-CONSPIRACY: Bram dies tonight" in prompt
