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
from graphia.state import (
    GameState,
    PlayerState,
    _merge_private_thoughts,
    thought_text,
)
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

    def with_structured_output(
        self, schema: type, **kwargs: object
    ) -> "_CapturingFake":
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
        def with_structured_output(self, schema: type, **kwargs: object) -> "_Boom":
            return self

        def invoke(self, messages: Any) -> Any:
            raise RuntimeError("model down")

    state = _round_close_state()
    monkeypatch.setattr(day_nodes, "get_large", lambda: _Boom())

    delta = day_round_reflect(state)
    thoughts = delta["private_thoughts"]
    # Every surviving AI still gets a (fallback) note — none is blank. Read
    # through ``thought_text`` because spec 041 Slice 9 reshaped the element
    # from a bare ``str`` to a ``ThoughtRecord``; the property asserted is
    # unchanged (one non-blank note per surviving AI), only the read is.
    for pid in ("p-ava", "p-ben", "p-mara"):
        assert len(thoughts[pid]) == 1
        assert thought_text(thoughts[pid][0]).strip()


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
        # SPEC 041 SLICE 9 rows: the element is `str | ThoughtRecord` now, and
        # `ThoughtRecord` is `total=False`, so a record with no usable `text`
        # is a legal instance of the type and must be dropped exactly as a
        # `None` note was. These are the shapes the retired
        # `isinstance(note, str)` guard used to drop for the opposite reason —
        # it dropped EVERY record, which is why it had to go.
        {"p-ava": [{}]},  # a record carrying no keys at all
        {"p-ava": [{"substituted": True}]},  # the flag, but no words
        {"p-ava": [{"text": None}, {"text": 123}]},  # unusable `text`
        {"p-ava": [{"text": "   "}]},  # whitespace-only, dropped by the strip
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
    else:
        # ...and every OTHER row surfaces nothing at all, which is a stronger
        # claim than "did not raise" and the one that catches a reader emitting
        # an empty `<thought player="Ava"></thought>` for an unusable element.
        # Added with the spec-041 record rows, because a `ThoughtRecord` with no
        # `text` is the first shape that could plausibly render a tag with no
        # body: it has a player id and a place in the list, and only the seam
        # returning `""` stops it.
        assert "<thought" not in out, (
            f"an unusable thought element rendered a tag anyway: {out!r}"
        )


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
    # ``thought_text`` for the same reason as above (spec 041 Slice 9).
    for pid in ("p-ava", "p-ben", "p-mara"):
        assert thought_text(thoughts[pid][0]).strip()


# ==========================================================================
# Spec 041 Slice 9 — the element is a RECORD now (``str | ThoughtRecord``)
# ==========================================================================
#
# Slice 9 reshaped THIS channel's element from a bare ``str`` to
# :class:`graphia.state.ThoughtRecord`, so that a thought which is the node's
# deterministic stand-in rather than the player's own reflection can carry that
# fact out to the transcript. Three properties are new, and none of them
# existed to be tested before:
#
#   * ``day_round_reflect`` writes the record, and an AUTHORED one carries
#     exactly ``text`` — never ``substituted: False``;
#   * :func:`graphia.state.thought_text` is the seam every reader goes through,
#     so what it does with a malformed element is a contract rather than an
#     accident;
#   * :data:`graphia.state.ThoughtEntry` is a PERMANENT union, so one
#     accumulated list may legitimately hold both shapes at once — a resumed
#     checkpoint's strings followed by this session's records — and every
#     reader must render the two indistinguishably.
#
# THE REDUCER TESTS ABOVE ARE DELIBERATELY NOT RE-PARAMETRISED OVER THE NEW
# SHAPE, and that is a measured decision rather than an omission.
# ``_merge_private_thoughts`` never subscripts an element, never reads a field
# off one and never compares two — it concatenates and copies lists — so
# widening the element is a type-level change there and re-running those four
# tests with dicts in place of strings would assert nothing the ``str`` rows
# already assert. What IS new is the MIXED list, and that has a test of its own
# below.


def test_an_authored_thought_record_carries_exactly_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record-level guard, one layer UPSTREAM of any transcript.

    The direct sibling of
    ``test_slice39_diary_before_night.test_record_carries_exactly_day_cursor_and_text``,
    and it exists because spec 041's Slice 8 established BY MUTATION that the
    record layer and the transcript layer each catch a fault the other cannot
    see: a renderer mutation turned 24 transcript tests red while the diary's
    record guard stayed silent, and a NODE mutation turned that guard red while
    the entire marking module stayed green. The thought channel had no
    counterpart at all, so this is the missing half.

    IT IS NOT A DUPLICATE OF THE TRANSCRIPT-LEVEL ABSENCE TEST, and the reason
    is sharper here than a difference of altitude. An unconditional
    ``record["substituted"] = mark.substituted`` writes ``False`` onto every
    authored record — and the eval transcript emits its attribute for the
    literal ``True`` ALONE, so every rendered document stays byte-identical and
    the transcript layer reports nothing whatsoever. The bug would then sit in
    the checkpoint, in every ``stream_mode="updates"`` delta and in every diary
    prompt's context, visible to no test in the suite. This assertion is the
    only thing that sees it.

    ``substituted`` is the only key that could appear beyond ``text``:
    :class:`graphia.state.ThoughtRecord` declares exactly those two, and
    deliberately carries no ``day`` and no cursor of its own (that class's
    docstring gives the reason), so the set equality is a complete statement
    about the authored shape rather than a sample of it.
    """
    fake = _CapturingFake(Reflection(thought="A private musing."))
    monkeypatch.setattr(day_nodes, "get_large", lambda: fake)

    delta = day_round_reflect(_round_close_state())
    reflectors = ("p-ava", "p-ben", "p-mara")
    records = [delta["private_thoughts"][pid][0] for pid in reflectors]

    # The premise: the model really answered, so every record here is on the
    # AUTHORED path. Without this the set equality below would also pass on a
    # node whose model call never happened.
    assert [record["text"] for record in records] == ["A private musing."] * 3
    for pid, record in zip(reflectors, records, strict=True):
        assert set(record) == {"text"}, (
            f"{pid}'s authored thought record carries more than `text` "
            f"({sorted(record)}). A `substituted` key on this path means the "
            "spec-041 marking is written unconditionally instead of only on "
            "`_ai_reflect`'s substitution branch. Note that `False` here is "
            "INVISIBLE to every transcript test — the writer emits the "
            "attribute for the literal `True` alone — so this assertion is "
            "the only one that fails."
        )


def test_a_substituted_thought_record_carries_text_and_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction, without which the guard above is passable by nothing.

    A node that had never learned to write ``substituted`` at all would satisfy
    ``set(record) == {"text"}`` perfectly, so the authored guard is only worth
    something beside a test that the key IS written when it is true. The pair is
    the record-level statement of the asymmetry the whole slice rests on:
    present when true, absent otherwise.

    Driven through the real ``day_round_reflect`` against a tier that cannot
    answer, so the flag travels ``_ai_reflect``'s substitution branch and the
    ``_SubstitutionMark`` carrier rather than being handed in.
    """

    class _Boom:
        def with_structured_output(self, schema: type, **kwargs: object) -> "_Boom":
            return self

        def invoke(self, messages: Any) -> Any:
            raise RuntimeError("reflection model down")

    monkeypatch.setattr(day_nodes, "get_large", lambda: _Boom())

    delta = day_round_reflect(_round_close_state())
    for pid in ("p-ava", "p-ben", "p-mara"):
        record = delta["private_thoughts"][pid][0]
        # The premise: the model failed and the node substituted its stand-in.
        assert record["text"] == day_nodes._REFLECTION_FALLBACK
        assert set(record) == {"text", "substituted"}, (
            f"{pid}'s substituted record does not carry the marking "
            f"({sorted(record)}), so nothing downstream can mark it"
        )
        assert record["substituted"] is True, (
            "the flag must be the literal `True` — the eval transcript's "
            "writer tests identity against it, so any other truthy value "
            "renders as authored"
        )


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        # The pre-reshape shape, still what a resumed checkpoint and ~100
        # offline-suite sites hold. Returned AS-IS, including the blank cases:
        # `thought_text` deliberately does not strip and does not filter, and
        # both of those are load-bearing (its two families of caller want
        # opposite things about a blank — the transcript renderers drop it, the
        # prompt block renders a bare `- ` bullet for it).
        pytest.param("a plain string", "a plain string", id="str"),
        pytest.param("", "", id="empty-str"),
        pytest.param("   ", "   ", id="blank-str-not-stripped"),
        pytest.param("  padded  ", "  padded  ", id="str-not-stripped"),
        # The post-reshape shape.
        pytest.param({"text": "words"}, "words", id="record"),
        pytest.param(
            {"text": "words", "substituted": True},
            "words",
            id="record-marked-reads-the-same",
        ),
        # MALFORMED, which is the half that has no other home. `ThoughtRecord`
        # is `total=False`, so a record with no `text` is a legal instance of
        # the type and an illegal thing to render.
        pytest.param({}, "", id="record-with-no-keys"),
        pytest.param({"substituted": True}, "", id="record-with-only-the-flag"),
        pytest.param({"text": None}, "", id="record-text-None"),
        pytest.param({"text": 42}, "", id="record-text-int"),
        pytest.param({"text": ["words"]}, "", id="record-text-list"),
        pytest.param({"text": b"words"}, "", id="record-text-bytes"),
        pytest.param({"text": {"text": "words"}}, "", id="record-text-nested"),
        # Not an entry at all. Every one of these is what the guard this seam
        # replaced (`if not isinstance(note, str): continue`) used to drop, and
        # the contract is that they still contribute nothing.
        pytest.param(None, "", id="None"),
        pytest.param(42, "", id="int"),
        pytest.param(True, "", id="bool-is-not-a-str-despite-being-an-int"),
        pytest.param(["words"], "", id="list"),
        pytest.param(("words",), "", id="tuple"),
        pytest.param(object(), "", id="an-arbitrary-object"),
    ],
)
def test_thought_text_reads_every_shape_and_degrades_rather_than_raising(
    entry: Any, expected: str
) -> None:
    """The seam's whole contract, including the shapes that carry no text.

    ``thought_text`` is what lets Slice 9 land incrementally across ~225
    references instead of as one unreviewable change, so every reader in
    ``src/`` now depends on this table. It DEGRADES RATHER THAN RAISES because
    every caller is on a prompt-build or transcript-render path, where the
    house style is that a malformed element costs one line of output instead of
    aborting a game — the same posture ``_private_record_block`` and
    ``_append_diaries`` already take field-by-field, applied one level down at
    the element.

    The ``bool`` row is the one that looks like padding and is not: ``True`` is
    an ``int`` in Python, so a seam written with a truthiness or numeric test
    somewhere in it could plausibly return something for it.
    """
    assert thought_text(entry) == expected


def test_the_reducer_accumulates_a_resumed_checkpoints_strings_beside_new_records() -> None:
    """The MIXED list, across super-steps — the one genuinely new reducer case.

    :data:`graphia.state.ThoughtEntry` is permanent, not transitional, and the
    reason is this exact list: a game resumed from a checkpoint written before
    the reshape holds ``str`` entries, and ``day_round_reflect`` then appends
    ``ThoughtRecord``s to the same per-player list, one per round, through the
    reducer. So the mixed list is not a hypothetical hand-built shape — it is
    what the channel holds on the first resumed game, and no migration exists
    (none is needed, because no checkpoint crosses sessions; this is the
    property that makes that true).

    Two super-steps rather than one, because a single merge cannot show
    accumulation: the reducer is called once per delta and the claim is that the
    third round's record lands after the second round's, which lands after the
    checkpoint's string.

    The IDENTITY assertions are the sharp half. The reducer copies the LISTS and
    must pass the ELEMENTS through untouched — if it ever rebuilt an element (to
    normalise a ``str`` into a record, say) a substituted record's flag would be
    the thing most likely to be dropped in the rebuild, silently, on the merge
    rather than at the writer.
    """
    from_checkpoint = "written before the reshape"
    round_two = {"text": "this session's second round"}
    round_three = {"text": day_nodes._REFLECTION_FALLBACK, "substituted": True}

    resumed = {"p-ava": [from_checkpoint], "p-ben": [from_checkpoint]}
    after_two = _merge_private_thoughts(resumed, {"p-ava": [round_two]})
    after_three = _merge_private_thoughts(after_two, {"p-ava": [round_three]})

    assert after_three == {
        "p-ava": [from_checkpoint, round_two, round_three],
        "p-ben": [from_checkpoint],
    }
    # ...and the elements are the very objects handed in, not copies or
    # coercions of them, so the flag cannot be lost in a rebuild.
    assert after_three["p-ava"][1] is round_two
    assert after_three["p-ava"][2] is round_three
    assert after_three["p-ava"][2]["substituted"] is True
    # Purity over the new shape: the record dicts themselves are unmutated, and
    # the untouched player's list is still the pre-reshape one it started as.
    assert round_two == {"text": "this session's second round"}
    assert after_three["p-ben"] == [from_checkpoint]
    # The count-based cursor `DiaryRecord.thoughts_before` reads off this list,
    # so what matters to it is the LENGTH — and a mixed list counts exactly as a
    # uniform one does.
    assert len(after_three["p-ava"]) == 3


def test_a_mixed_list_renders_the_same_prompt_bullets_as_an_all_string_list() -> None:
    """One block, two element shapes, byte-identical output — and NO marking.

    Two claims in one equality, and both are deliberate:

    * the mixed list renders indistinguishably from the all-``str`` list the
      same words would have produced before the reshape, so a resumed
      checkpoint's prompts are unaffected and the ~100 offline-suite sites that
      hand-build this channel as strings stay correct;
    * THE SUBSTITUTION MARKING IS NOT RENDERED INTO THE PROMPT. That is settled
      design rather than an oversight (it matches how ``_private_record_block``
      renders a substituted ``DiaryRecord``): a prompt-visible mark would change
      what the player reasons from, which is a game-design change and not this
      spec's. The third entry below is substituted and its bullet is the
      stand-in's words and nothing more.

    The premise assertion keeps it non-vacuous: a block that had stopped
    rendering bullets at all would satisfy the equality trivially.
    """
    mixed = [
        "from the resumed checkpoint",
        {"text": "from this session"},
        {"text": "a deterministic stand-in", "substituted": True},
    ]
    all_strings = [
        "from the resumed checkpoint",
        "from this session",
        "a deterministic stand-in",
    ]

    block = _private_thoughts_block(mixed, enabled=True)

    # The premise: three bullets really are in there, in order.
    assert block.count("\n- ") == 3
    assert block.index("resumed checkpoint") < block.index("this session")
    assert block.index("this session") < block.index("stand-in")
    assert block == _private_thoughts_block(all_strings, enabled=True)
    # ...and nothing in the rendered prompt says which one was a stand-in.
    assert "substituted" not in block
    assert "{" not in block and "}" not in block, (
        "a dict repr reached the model's context — an element was rendered "
        "without going through `thought_text`"
    )


def test_the_transcript_renders_a_record_and_a_string_thought_alike() -> None:
    """``_append_thoughts`` over both element shapes, in one delta.

    The transcript-layer twin of the prompt-block test above, and it is a
    separate reader: ``_append_thoughts`` reads the words through
    ``thought_text`` but reads the FLAG off the raw entry, so it is the one
    reader that must handle both shapes and treat only one of them as capable
    of carrying a marking.

    Ava's entry is a bare ``str`` (a resumed checkpoint's), Mara's a record —
    and the two elements render byte-identically apart from the words, which is
    the guarantee that made the reshape landable across 225 references.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {
            "day_round_reflect": {
                "private_thoughts": {
                    "p-ava": ["A_STRING_ENTRY"],
                    "p-mara": [{"text": "A_RECORD_ENTRY"}],
                }
            }
        },
    ]
    out = render_transcript(
        events, _transcript_players(), game_index=1, run_meta=None
    )

    assert '<thought player="Ava">A_STRING_ENTRY</thought>' in out
    assert '<thought player="Mara">A_RECORD_ENTRY</thought>' in out
    # No dict repr leaked into the artifact, which is the failure mode of a
    # reader that skipped the seam: well-formed output, not a crash.
    assert "{" not in out and "'text'" not in out
    # An authored record carries no marking, whichever shape it arrived in.
    assert "substituted" not in out
