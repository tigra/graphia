"""Spec 028 (Per-AI Day-Round Private Thoughts) — Slices 1 & 2 tests.

The production change gives every surviving AI player a private end-of-Day-round
reflection (a new ``day_round_reflect`` node calling ``get_large()`` once per
surviving non-human player), accumulates each player's notes per id in a new
``GameState.private_thoughts`` channel via the ``_merge_private_thoughts``
reducer, and feeds a player's OWN accumulated notes — in event order — into its
later Day-speech (``_ai_day_action``), vote (``_ai_ballot``), and Mafioso
Night-pointing (``_ai_pick_target``) prompts. The whole feature is gated by the
default-on ablation flag ``GRAPHIA_PRIVATE_THOUGHTS`` (ADR 011).

This file is the STRUCTURAL-invariant test of that change (the behavioural
effect — does a private reasoning channel improve play — is eval-measured OUT of
the suite, per the effort-not-results acceptance principle, CR 005; nothing here
asserts coherence/decisiveness/win-rate). Per architecture §6 every assertion is
structural — a thought was produced, it reached the next prompt, no cross-player
leak, the slot collapses on flag-off — never the verbatim LLM thought text.

Coverage:

Slice 1 — produced, accumulated, private, transcript:
- the reflection node writes ONE thought per surviving AI at a round close, and
  NONE for the human or a dead player;
- ``_merge_private_thoughts`` accumulates per player without clobber, and is pure
  (inputs unmutated, lists not aliased);
- the thought is PRIVATE: no public ``messages`` are emitted, and a player's note
  never appears in another player's prompt context;
- the eval transcript renders each thought as a private ``<thought player="…">``
  element, and is defensive on missing/empty/unknown input;
- flag-off ⇒ the node is a no-op, no thoughts, prompts unchanged (parity);
- the ``Reflection`` queue + ``reflections=`` kwarg net the new call site.

Slice 2 — own thoughts feed later decisions:
- a player's OWN thoughts reach its Day-speech, vote, and (Mafioso) Night-point
  prompts, in order;
- a player NEVER receives another player's thoughts (no cross-leak);
- the three template ``.format()`` sites render without ``KeyError``;
- flag-off parity (the three slots collapse to the pre-028 prompt form).

The autouse ``safe_llm`` net is left intact; the LLM boundary is a
content-recording fake patched AFTER it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import graphia.nodes.day as day_nodes
import graphia.nodes.night as night_nodes
from graphia.config import load_config
from graphia.llm import Ballot, DayAction, Pointing, Reflection
from graphia.nodes.day import (
    PRIVATE_THOUGHTS_LABEL,
    _ai_ballot,
    _ai_day_action,
    _private_thoughts_block,
    day_round_reflect,
)
from graphia.nodes.night import _ai_pick_target
from graphia.prompts import (
    AI_VOTE_USER_TEMPLATE,
    DAY_SPEAK_USER_TEMPLATE,
    MAFIA_POINT_USER_TEMPLATE,
)
from graphia.runtime.graph_builder import build_runtime_graph
from graphia.state import GameState, PlayerState, _merge_private_thoughts
from graphia.tools.eval_transcript import render_transcript


# ==========================================================================
# Shared hand-built helpers
# ==========================================================================


def _player(
    pid: str,
    name: str,
    role: str,
    *,
    is_human: bool = False,
    is_alive: bool = True,
) -> PlayerState:
    return PlayerState(
        id=pid,
        name=name,
        role=role,  # type: ignore[arg-type]
        is_human=is_human,
        is_alive=is_alive,
    )


def _mixed_table() -> dict[str, PlayerState]:
    """A small insertion-ordered table: 2 AI Citizens, 1 AI Mafioso, 1 human.

    Distinct ids and names so an id-vs-name confusion or a cross-player leak is
    unambiguous in the assertions.
    """
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-ben": _player("p-ben", "Ben", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
        "p-human": _player("p-human", "Hugo", "law_abiding", is_human=True),
    }


def _round_close_state() -> GameState:
    """A state positioned at a fresh-round start (a round just wrapped).

    ``day_turn_index == 0 and day_rounds == 1`` is the round-wrap signal the
    reflection node self-guards on; a non-trivial standings body (mixed living
    roster) makes the reflection prompt's injected helpers distinctive.
    """
    return {
        "cycle": 1,
        "players": _mixed_table(),
        "day_turn_index": 0,
        "day_rounds": 1,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
    }


class _CapturingFake:
    """A content-recording ``get_large()`` stand-in for the real decision paths.

    Returns a scripted output (a ``DayAction`` / ``Ballot`` / ``Pointing`` /
    ``Reflection`` — all share the ``with_structured_output(...).invoke(...)``
    shape) and records every prompt it was handed, so a test can drive the REAL
    node and inspect the actual rendered prompt — the template-wiring proof.
    Mirrors ``test_recap_aware_reasoning._CapturingDayFake``.
    """

    def __init__(self, output: Any) -> None:
        self._output = output
        self.messages_log: list[Any] = []

    def with_structured_output(self, schema: type) -> "_CapturingFake":
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages_log.append(messages)
        return self._output


def _human_prompt(messages: Any) -> str:
    """The rendered HumanMessage text from a captured ``[System, Human]`` prompt."""
    human = messages[1]
    assert isinstance(human, HumanMessage)
    return human.content


# ==========================================================================
# Slice 1.1 — the accumulating reducer (_merge_private_thoughts)
# ==========================================================================


def test_reducer_accumulates_per_player_in_order() -> None:
    """Two successive deltas for the same player concatenate in write order."""
    state1 = _merge_private_thoughts({}, {"p-ava": ["t1"]})
    state2 = _merge_private_thoughts(state1, {"p-ava": ["t2"]})
    assert state2 == {"p-ava": ["t1", "t2"]}


def test_reducer_adds_new_key_without_disturbing_others() -> None:
    """A delta for a new player adds that key, leaving existing keys intact."""
    prior = {"p-ava": ["a1", "a2"]}
    out = _merge_private_thoughts(prior, {"p-ben": ["b1"]})
    assert out == {"p-ava": ["a1", "a2"], "p-ben": ["b1"]}


def test_reducer_is_pure_inputs_not_mutated_or_aliased() -> None:
    """The reducer copies-not-mutates: neither input changes; lists are fresh."""
    prior = {"p-ava": ["a1"]}
    incoming = {"p-ava": ["a2"], "p-ben": ["b1"]}
    out = _merge_private_thoughts(prior, incoming)

    # Inputs unchanged.
    assert prior == {"p-ava": ["a1"]}
    assert incoming == {"p-ava": ["a2"], "p-ben": ["b1"]}
    # The output's lists are fresh objects, not aliases of either input's list.
    assert out["p-ava"] is not prior["p-ava"]
    assert out["p-ava"] is not incoming["p-ava"]
    assert out["p-ben"] is not incoming["p-ben"]


@pytest.mark.parametrize(
    ("prior", "incoming", "expected"),
    [
        (None, {"x": ["y"]}, {"x": ["y"]}),
        ({"x": ["y"]}, None, {"x": ["y"]}),
        (None, None, {}),
    ],
)
def test_reducer_tolerates_none_operands(
    prior: dict | None, incoming: dict | None, expected: dict
) -> None:
    """``None`` on either side is treated as the empty map (initial-state safe)."""
    assert _merge_private_thoughts(prior, incoming) == expected


# ==========================================================================
# Slice 1.2 — the reflection node: one thought per surviving AI, skips dead/human
# ==========================================================================


def test_reflect_writes_one_thought_per_surviving_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each surviving AI gets exactly one note; the human and dead get none."""
    state = _round_close_state()
    fake = _CapturingFake(Reflection(thought="A private musing."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_round_reflect(state)
    thoughts = delta["private_thoughts"]

    # The two AI Citizens and the AI Mafioso each get one note.
    assert set(thoughts.keys()) == {"p-ava", "p-ben", "p-mara"}
    for pid in ("p-ava", "p-ben", "p-mara"):
        assert len(thoughts[pid]) == 1
    # The human writes nothing.
    assert "p-human" not in thoughts


def test_reflect_skips_dead_players(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead AI player produces no private thought."""
    state = _round_close_state()
    # Kill Ben.
    players = dict(state["players"])
    players["p-ben"] = _player("p-ben", "Ben", "law_abiding", is_alive=False)
    state["players"] = players

    fake = _CapturingFake(Reflection(thought="Still thinking."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_round_reflect(state)
    thoughts = delta["private_thoughts"]
    assert "p-ben" not in thoughts
    assert set(thoughts.keys()) == {"p-ava", "p-mara"}


def test_reflect_emits_no_public_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reflection delta carries NO ``messages`` (the privacy invariant)."""
    state = _round_close_state()
    fake = _CapturingFake(Reflection(thought="Quietly noted."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_round_reflect(state)
    assert "messages" not in delta
    # And nothing in the produced delta carries a ``private_to`` route — it is a
    # plain ``private_thoughts`` map, never a routed whisper.
    assert set(delta.keys()) == {"private_thoughts"}


def test_reflect_falls_back_when_model_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model error never blanks the channel — a deterministic note is written."""

    class _Boom:
        def with_structured_output(self, schema: type) -> "_Boom":
            return self

        def invoke(self, messages: Any) -> Any:
            raise RuntimeError("model down")

    state = _round_close_state()
    monkeypatch.setattr(day_nodes, "get_large", lambda: _Boom())

    delta = day_round_reflect(state)
    thoughts = delta["private_thoughts"]
    # Every surviving AI still gets a (fallback) note — none is blank.
    for pid in ("p-ava", "p-ben", "p-mara"):
        assert len(thoughts[pid]) == 1
        assert thoughts[pid][0].strip()


def test_reflect_self_guard_no_op_mid_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The node is a safe no-op when NOT at a fresh-round start (self-guard)."""
    state = _round_close_state()
    state["day_turn_index"] = 2  # mid-round, not a wrap
    fake = _CapturingFake(Reflection(thought="should not run"))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    assert day_round_reflect(state) == {}
    assert fake.messages_log == []  # the model was never called


def test_reflect_self_guard_no_op_before_first_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No reflection before any round completes (``day_rounds == 0``)."""
    state = _round_close_state()
    state["day_rounds"] = 0
    fake = _CapturingFake(Reflection(thought="too early"))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    assert day_round_reflect(state) == {}
    assert fake.messages_log == []


# ==========================================================================
# Slice 1 — router topology: only a genuine round-wrap routes to reflection
# ==========================================================================


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        # A completed-round loop-back (game continues) → reflect.
        ({"day_turn_index": 0, "day_rounds": 1}, "day_round_reflect"),
        # Mid-round (positive cursor) → straight back to day_turn, no reflection.
        ({"day_turn_index": 2, "day_rounds": 1}, "day_turn"),
        # Before any round completes → day_turn (no reflection on day 1 round 1).
        ({"day_turn_index": 0, "day_rounds": 0}, "day_turn"),
        # Round cap → day_close (reflection never fires at the Day's end).
        ({"day_turn_index": 0, "day_rounds": 6}, "day_close"),
        # Vote initiated → vote_prompt (unchanged precedence).
        ({"active_vote": {"x": 1}, "day_turn_index": 0, "day_rounds": 1}, "vote_prompt"),
        # Pending re-prompt error → day_turn (unchanged precedence).
        ({"day_turn_error": "bad", "day_turn_index": 0, "day_rounds": 1}, "day_turn"),
    ],
)
def test_route_day_turn_routes_round_wrap_to_reflection(
    state: dict, expected: str
) -> None:
    """Only a genuine completed-round loop-back routes to ``day_round_reflect``.

    The vote / re-prompt / round-cap / mid-round branches are unchanged from
    pre-028; the round-wrap branch (``day_turn_index == 0 and day_rounds >= 1``,
    game continuing) is the sole new route into the reflection node.
    """
    assert day_nodes.route_day_turn_or_vote(state) == expected


# ==========================================================================
# Slice 1.3 — privacy: a player's note never reaches another player's prompt
# ==========================================================================


def test_reflect_prompt_carries_only_the_reflectors_own_prior_thoughts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reflection prompt for Ava carries Ava's own notes, never Ben's/Mara's."""
    state = _round_close_state()
    state["private_thoughts"] = {
        "p-ava": ["ava-secret-note"],
        "p-ben": ["ben-secret-note"],
        "p-mara": ["mara-secret-note"],
    }
    fake = _CapturingFake(Reflection(thought="next round plan"))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_round_reflect(state)

    # The fake recorded one prompt per surviving AI, in players-dict order:
    # Ava, Ben, Mara. Each prompt must carry only that player's own note.
    ava_prompt = _human_prompt(fake.messages_log[0])
    assert "ava-secret-note" in ava_prompt
    assert "ben-secret-note" not in ava_prompt
    assert "mara-secret-note" not in ava_prompt

    ben_prompt = _human_prompt(fake.messages_log[1])
    assert "ben-secret-note" in ben_prompt
    assert "ava-secret-note" not in ben_prompt


# ==========================================================================
# Slice 1.4 — _private_thoughts_block purity / shape
# ==========================================================================


def test_block_disabled_returns_empty() -> None:
    """Flag OFF ⇒ ``""`` regardless of notes (the ablation parity seam)."""
    assert _private_thoughts_block([], enabled=False) == ""
    assert _private_thoughts_block(["a", "b"], enabled=False) == ""


def test_block_enabled_but_empty_returns_empty() -> None:
    """Enabled with no prior notes ⇒ ``""`` (round-1 prompts unchanged)."""
    assert _private_thoughts_block([], enabled=True) == ""


def test_block_enabled_with_notes_is_labelled_and_ordered() -> None:
    """Enabled with notes ⇒ labelled block listing the notes in order."""
    block = _private_thoughts_block(["first", "second"], enabled=True)
    assert PRIVATE_THOUGHTS_LABEL in block
    assert "- first" in block
    assert "- second" in block
    # Order preserved.
    assert block.index("first") < block.index("second")
    # Framing newlines so it slots cleanly into the template seam.
    assert block.startswith("\n")
    assert block.endswith("\n")


# ==========================================================================
# Slice 1.5 — eval-transcript rendering
# ==========================================================================


def _transcript_players() -> dict[str, PlayerState]:
    return {
        "p-ava": _player("p-ava", "Ava", "law_abiding"),
        "p-mara": _player("p-mara", "Mara", "mafia"),
    }


def test_transcript_renders_thought_as_private_attributed_element() -> None:
    """A ``private_thoughts`` delta renders one ``<thought player="…">`` each.

    The synthetic Day stream: open → one utterance → a ``day_round_reflect``
    delta carrying two players' new thoughts. The renderer must surface each as a
    private/annotated element attributed to its author, inside the day's round.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {
            "day_turn": {
                "messages": [AIMessage(content="I suspect the fishmonger.", name="Ava")]
            }
        },
        {
            "day_round_reflect": {
                "private_thoughts": {
                    "p-ava": ["AVA_PRIVATE_TOKEN"],
                    "p-mara": ["MARA_PRIVATE_TOKEN"],
                }
            }
        },
    ]
    out = render_transcript(
        events, _transcript_players(), game_index=1, run_meta=None
    )

    assert '<thought player="Ava">AVA_PRIVATE_TOKEN</thought>' in out
    assert '<thought player="Mara">MARA_PRIVATE_TOKEN</thought>' in out
    # The thought is NOT rendered as a public utterance (no "Ava: AVA_..." line).
    assert "Ava: AVA_PRIVATE_TOKEN" not in out
    # It lands inside the day (after the day header), beside the speech.
    assert out.index("Day 1 breaks.") < out.index("AVA_PRIVATE_TOKEN")


@pytest.mark.parametrize(
    "thoughts_value",
    [
        {},  # empty channel
        {"p-ava": []},  # empty per-player list
        {"p-unknown": ["orphan note"]},  # id absent from the roster
        {"p-ava": [None, 123]},  # non-string notes (defensive)
        "not-a-dict",  # wrong type entirely
    ],
)
def test_transcript_thoughts_rendering_is_defensive(
    thoughts_value: Any,
) -> None:
    """Missing channel, empty list, unknown id, or bad type must never raise."""
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {"day_round_reflect": {"private_thoughts": thoughts_value}},
    ]
    # Must not raise.
    out = render_transcript(
        events, _transcript_players(), game_index=1, run_meta=None
    )
    assert "<transcript>" in out
    # An unknown id still resolves (to its raw id) — surfaced, not crashed.
    if thoughts_value == {"p-unknown": ["orphan note"]}:
        assert '<thought player="p-unknown">orphan note</thought>' in out


# ==========================================================================
# Slice 1.6 — flag-off parity for the reflection NODE
# ==========================================================================


def test_reflect_flag_off_is_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag OFF ⇒ the reflection node writes nothing and calls no model."""
    state = _round_close_state()
    fake = _CapturingFake(Reflection(thought="should not appear"))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_round_reflect(state, private_thoughts_enabled=False)
    assert delta == {}
    assert fake.messages_log == []


# ==========================================================================
# Slice 2.1 — own thoughts feed Day-speech, vote, and Night-point prompts
# ==========================================================================


def _speak_state_with_thoughts() -> GameState:
    """A Day state where the acting AI players hold distinct prior thoughts."""
    state = _round_close_state()
    state["private_thoughts"] = {
        "p-ava": ["ava-note-1", "ava-note-2"],
        "p-ben": ["ben-note-1"],
    }
    return state


def test_own_thoughts_reach_day_speak_prompt_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ava's own notes (in order) reach the prompt ``_ai_day_action`` builds."""
    state = _speak_state_with_thoughts()
    ava = state["players"]["p-ava"]
    fake = _CapturingFake(DayAction(kind="speak", text="A measured remark."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(
        ava,
        state,
        private_thoughts=state["private_thoughts"]["p-ava"],
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL in prompt
    assert "ava-note-1" in prompt
    assert "ava-note-2" in prompt
    assert prompt.index("ava-note-1") < prompt.index("ava-note-2")


def test_own_thoughts_reach_ai_ballot_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The voter's own notes reach the prompt ``_ai_ballot`` builds."""
    state = _speak_state_with_thoughts()
    ava = state["players"]["p-ava"]
    target = state["players"]["p-ben"]
    fake = _CapturingFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(
        ava,
        target,
        state,
        private_thoughts=state["private_thoughts"]["p-ava"],
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL in prompt
    assert "ava-note-1" in prompt


def test_own_thoughts_reach_mafia_point_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mafioso's own notes reach the prompt ``_ai_pick_target`` builds."""
    mafia = _player("p-mara", "Mara", "mafia")
    targets = [
        _player("t-1", "Priya", "law_abiding"),
        _player("t-2", "Silas", "law_abiding"),
    ]
    fake = _CapturingFake(Pointing(target_id="t-1"))
    monkeypatch.setattr(night_nodes, "get_large", lambda: fake)

    _ai_pick_target(
        targets,
        mafia,
        prior_picks="No teammate has pointed yet this Night.",
        private_thoughts=["mara-night-note"],
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL in prompt
    assert "mara-night-note" in prompt


def test_no_cross_player_leak_in_day_speak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A player never receives another player's thoughts in its Day-speak prompt.

    Drive Ava's speak turn passing ONLY Ava's notes (as the live call site does,
    keyed on the acting player's id); Ben's note text must be absent.
    """
    state = _speak_state_with_thoughts()
    ava = state["players"]["p-ava"]
    fake = _CapturingFake(DayAction(kind="speak", text="."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(
        ava,
        state,
        private_thoughts=state["private_thoughts"]["p-ava"],
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert "ben-note-1" not in prompt


def test_live_call_site_keys_on_acting_player_via_day_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``day_turn`` threads only the ACTING player's own thoughts (no cross-leak).

    Drives the real ``day_turn`` node (mid-round, so it returns a speech, not a
    wrap) for Ava with Ben's note also present in state — the captured prompt
    must carry Ava's note and not Ben's.
    """
    state = _speak_state_with_thoughts()
    # Position Ava as the current speaker mid-round so day_turn calls _ai_day_action.
    state["day_order"] = ["p-ava", "p-ben", "p-mara"]
    state["day_turn_index"] = 0
    fake = _CapturingFake(DayAction(kind="speak", text="Ava speaks."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    day_nodes.day_turn(state)

    prompt = _human_prompt(fake.messages_log[0])
    assert "ava-note-1" in prompt
    assert "ben-note-1" not in prompt


# ==========================================================================
# Slice 2.2 — template slot-guards (.format renders without KeyError)
# ==========================================================================


def test_day_speak_template_slot_guard_renders_with_private_thoughts() -> None:
    """``DAY_SPEAK_USER_TEMPLATE.format(...)`` with ``private_thoughts=`` is clean."""
    block = _private_thoughts_block(["my note"], enabled=True)
    rendered = DAY_SPEAK_USER_TEMPLATE.format(
        speaker="Ava",
        role_label="Law-abiding Citizen",
        win_condition="wc",
        team_line="",
        persona="",
        standings="",
        roster="Ava: p-ava",
        context="(no prior discussion)",
        private_thoughts=block,
        role_guidance="",
    )
    assert "{private_thoughts}" not in rendered
    assert "my note" in rendered
    assert "Ava" in rendered


def test_ai_vote_template_slot_guard_renders_with_private_thoughts() -> None:
    """``AI_VOTE_USER_TEMPLATE.format(...)`` with ``private_thoughts=`` is clean."""
    block = _private_thoughts_block(["my note"], enabled=True)
    rendered = AI_VOTE_USER_TEMPLATE.format(
        voter="Ava",
        role_label="Law-abiding Citizen",
        win_condition="wc",
        team_line="",
        standings="",
        target="Mara",
        relationship="",
        context="(no prior discussion)",
        private_thoughts=block,
        role_guidance="",
    )
    assert "{private_thoughts}" not in rendered
    assert "my note" in rendered


def test_mafia_point_template_slot_guard_renders_with_private_thoughts() -> None:
    """``MAFIA_POINT_USER_TEMPLATE.format(...)`` with ``private_thoughts=`` is clean."""
    block = _private_thoughts_block(["my note"], enabled=True)
    rendered = MAFIA_POINT_USER_TEMPLATE.format(
        roster="Priya: t1",
        mafia_persona="",
        prior_picks="No teammate has pointed yet this Night.",
        private_thoughts=block,
    )
    assert "{private_thoughts}" not in rendered
    assert "my note" in rendered


# ==========================================================================
# Slice 2.3 — flag-off parity for the three decision prompts (ADR 011)
# ==========================================================================


def test_flag_off_removes_block_from_day_speak_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the Day-speak prompt carries NO private-thoughts label/body."""
    state = _speak_state_with_thoughts()
    ava = state["players"]["p-ava"]
    fake = _CapturingFake(DayAction(kind="speak", text="."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_day_action(
        ava,
        state,
        private_thoughts=state["private_thoughts"]["p-ava"],
        private_thoughts_enabled=False,
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL not in prompt
    assert "ava-note-1" not in prompt


def test_flag_off_removes_block_from_ai_ballot_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the AI vote prompt carries NO private-thoughts label/body."""
    state = _speak_state_with_thoughts()
    ava = state["players"]["p-ava"]
    target = state["players"]["p-ben"]
    fake = _CapturingFake(Ballot(yes=False))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    _ai_ballot(
        ava,
        target,
        state,
        private_thoughts=state["private_thoughts"]["p-ava"],
        private_thoughts_enabled=False,
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL not in prompt
    assert "ava-note-1" not in prompt


def test_flag_off_removes_block_from_mafia_point_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF ⇒ the Night-point prompt carries NO private-thoughts label/body."""
    mafia = _player("p-mara", "Mara", "mafia")
    targets = [_player("t-1", "Priya", "law_abiding")]
    fake = _CapturingFake(Pointing(target_id="t-1"))
    monkeypatch.setattr(night_nodes, "get_large", lambda: fake)

    _ai_pick_target(
        targets,
        mafia,
        prior_picks="No teammate has pointed yet this Night.",
        private_thoughts=["mara-night-note"],
        private_thoughts_enabled=False,
    )

    prompt = _human_prompt(fake.messages_log[0])
    assert PRIVATE_THOUGHTS_LABEL not in prompt
    assert "mara-night-note" not in prompt


# ==========================================================================
# load_config default-on semantics for GRAPHIA_PRIVATE_THOUGHTS (ADR 011)
# ==========================================================================


def test_load_config_private_thoughts_default_on_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ⇒ private thoughts on (the documented default)."""
    monkeypatch.delenv("GRAPHIA_PRIVATE_THOUGHTS", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().private_thoughts_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_load_config_private_thoughts_blank_is_default_on(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank/whitespace value is treated as unset ⇒ on (``_env_flag``)."""
    monkeypatch.setenv("GRAPHIA_PRIVATE_THOUGHTS", blank)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().private_thoughts_enabled is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "On"])
def test_load_config_private_thoughts_truthy_enables(
    truthy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any truthy value keeps private thoughts on."""
    monkeypatch.setenv("GRAPHIA_PRIVATE_THOUGHTS", truthy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().private_thoughts_enabled is True


@pytest.mark.parametrize("falsy", ["0", "false", "FALSE", "no", "off", "Off"])
def test_load_config_private_thoughts_explicit_falsy_disables(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit falsy value disables the flag."""
    monkeypatch.setenv("GRAPHIA_PRIVATE_THOUGHTS", falsy)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy")
    assert load_config().private_thoughts_enabled is False


# ==========================================================================
# Anti-drift — build_runtime_graph carries the flag (both builders thread it)
# ==========================================================================


def test_build_runtime_graph_signature_carries_private_thoughts_flag() -> None:
    """``build_runtime_graph`` exposes a ``private_thoughts_enabled`` parameter.

    The named anti-drift requirement (ADR 011): both graph builders must thread
    the flag or local and remote diverge. Defaulted to True (matching config).
    """
    sig = inspect.signature(build_runtime_graph)
    assert "private_thoughts_enabled" in sig.parameters
    assert sig.parameters["private_thoughts_enabled"].default is True


# ==========================================================================
# safe_llm coverage — the new Reflection call site is netted (REQUIRED)
# ==========================================================================


def test_safe_llm_covers_reflection_call_site_via_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag-on reflection run with NO fake installed never reaches real Bedrock.

    The autouse ``safe_llm`` net leaves ``graphia.nodes.day.get_large`` bound to
    the loud-failure fake. The reflection node's try/except converts that loud
    failure into the deterministic fallback note — so a full-Day reflection run
    is covered (it does NOT raise the 'no scripted queue' assertion uncaught, and
    never falls through to boto3). Here we assert the node completes and writes a
    fallback note for each surviving AI without a test-supplied fake.
    """
    # NOTE: no monkeypatch of get_large here — the autouse safe_llm binding holds.
    state = _round_close_state()
    delta = day_round_reflect(state)
    thoughts = delta["private_thoughts"]
    for pid in ("p-ava", "p-ben", "p-mara"):
        assert thoughts[pid][0].strip()
