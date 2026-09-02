"""Spec 039 (Per-AI Private Diaries) — the prose-recovery defect fix.

WHAT WAS BROKEN
---------------

``_ai_diary`` reached the model through ``with_structured_output(Diary)``, which
is tool use — and **tool use is a request, not a guarantee**. Bedrock Converse
enforces its ``toolConfig``, so Nova and Claude comply (measured fallback rates
1/101 and 0/66). Ollama's Anthropic-compatible endpoint *accepts* ``tool_choice``
and silently drops it, so a model handed ``DIARY_SYSTEM`` — a long, deliberately
open invitation to write freely — answered in PROSE roughly half the time:
``stop_reason='end_turn'``, no tool call at all.

The entry was never missing in that case. It sat in the reply's content, in the
player's own voice, the right shape and the right length, because the model did
the writing and merely skipped the envelope. The old code threw it away and
stored ``_DIARY_FALLBACK`` instead — 45 of 90 entries across the spec-039
on-arm campaign, whose spec-028 thoughts on the SAME model in the SAME runs
failed 0 times in 391. That left the measured on arm only half-treated.

Measured after the fix, on six real captured prompts replayed against
qwen3-coder:30b: 11 of 11 prose replies carried a complete, usable entry.

WHAT THIS FILE PINS
-------------------

1. ``_recovered_diary_text``'s shaping, case by case, including the cases it
   must REFUSE (nothing usable → ``None``, so the caller still reaches the
   deterministic fallback rather than storing an empty entry).
2. That a parsed tool call still WINS — recovery is the fallback path, not the
   primary one, so this fix cannot mask a regression on the Bedrock route.
3. The behaviour through the real ``day_diary`` node: a prose reply yields the
   prose, and an unusable reply still yields ``_DIARY_FALLBACK``.
4. That a recovered entry is normalised by the SAME clamp as a parsed one, so
   the two routes cannot disagree about folding or the length cap.

Anti-vacuity discipline
-----------------------

* ``_DIARY_FALLBACK`` is itself non-empty, in-voice prose, so **no assertion
  here settles for "an entry was written"**. Every positive case pins the
  scripted text by value AND asserts it is not the sentinel; a fix that
  silently kept falling back would otherwise pass.
* The negative cases (nothing recoverable) pin the sentinel by value too —
  "not the prose" would be satisfied by an empty string, which is the one
  outcome the ``or None`` guard exists to prevent.
* The clamp case scripts prose LONGER than ``DIARY_MAX_CHARS`` and containing
  newlines, so a fix that recovered the text but skipped ``_clamp_diary_entry``
  fails on both axes rather than neither.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import graphia.nodes.day as day_nodes
from graphia.diary_store import InProcessDiaryStore
from graphia.llm import Diary
from graphia.nodes.day import (
    DIARY_MAX_CHARS,
    _DIARY_FALLBACK,
    _clamp_diary_entry,
    _diary_entry_from,
    _recovered_diary_text,
    day_diary,
)
from graphia.state import GameState, PlayerState

DAY_LOGGER = "graphia.nodes.day"

# In-voice prose a model actually produced on the ollama path, trimmed. Chosen
# over lorem so the "is this a diary entry?" question the fix answers stays
# legible in the test.
PROSE = (
    "Izzet's twitching ain't from guilt — it's from trying to keep my mouth "
    "shut while everyone else talks nonsense."
)


def _player(
    pid: str, name: str, role: str, *, is_human: bool = False
) -> PlayerState:
    return PlayerState(
        id=pid,
        name=name,
        role=role,  # type: ignore[arg-type]
        is_human=is_human,
        is_alive=True,
    )


def _state() -> GameState:
    """One AI writer plus a human, positioned at the Day→Night hinge.

    ``cycle`` is 3 so the entry's day is 3 — distinct from any small constant.
    """
    return {  # type: ignore[return-value]
        "cycle": 3,
        "phase": "day",
        "players": {
            "p-ava": _player("p-ava", "Ava", "law_abiding"),
            "p-human": _player("p-human", "Hugo", "law_abiding", is_human=True),
        },
        "day_turn_index": 0,
        "day_rounds": 6,
        "day_votes_initiated": 0,
        "kill_log": [],
        "messages": [],
        "private_thoughts": {},
        "private_diaries": {},
    }


class _RepliesWith:
    """A large-model fake honouring ``include_raw`` the way production asks.

    ``reply`` is returned as the ``raw`` message with ``parsed`` set to None —
    the exact shape ollama produces when it answers without calling the tool.
    """

    def __init__(self, reply: AIMessage) -> None:
        self._reply = reply

    def with_structured_output(self, schema: type, **kwargs: object) -> "_RepliesWith":
        assert kwargs.get("include_raw") is True, (
            "production must ask for the raw reply, or a prose answer is "
            "unrecoverable by construction"
        )
        return self

    def invoke(self, messages: Any) -> Any:
        return {"raw": self._reply, "parsed": None, "parsing_error": None}


# ==========================================================================
# 1 — the shaping helper, case by case
# ==========================================================================


@pytest.mark.parametrize(
    "content,expected",
    [
        pytest.param(PROSE, PROSE, id="plain-prose-passes-through"),
        pytest.param(
            f"Diary entry:\n{PROSE}", PROSE, id="short-label-line-is-dropped"
        ),
        pytest.param(
            '{"entry": "Hand-rolled JSON."}',
            "Hand-rolled JSON.",
            id="json-yields-its-entry-field",
        ),
        pytest.param(
            '```json\n{"entry": "Fenced JSON."}\n```',
            "Fenced JSON.",
            id="fenced-json-is-unwrapped-first",
        ),
        pytest.param(f'"{PROSE}"', PROSE, id="wrapping-double-quotes-removed"),
        pytest.param(f"'{PROSE}'", PROSE, id="wrapping-single-quotes-removed"),
        pytest.param(
            [{"type": "text", "text": PROSE}], PROSE, id="text-blocks-flattened"
        ),
        pytest.param(
            [{"type": "thinking", "thinking": "ignore me"}],
            None,
            id="non-text-blocks-contribute-nothing",
        ),
        pytest.param("", None, id="empty-content-refused"),
        pytest.param("   \n  ", None, id="whitespace-only-refused"),
        pytest.param(
            '{"other": "no entry key"}', None, id="json-without-entry-refused"
        ),
        pytest.param('{"entry": "   "}', None, id="json-with-blank-entry-refused"),
    ],
)
def test_recovered_diary_text_shaping(content: object, expected: str | None) -> None:
    """Each shape the ollama path actually produces, and each refusal."""
    message = AIMessage(content=content)  # type: ignore[arg-type]
    assert _recovered_diary_text(message) == expected


def test_a_missing_raw_message_is_refused_rather_than_crashing() -> None:
    """``raw`` is absent whenever the provider ignored ``include_raw``."""
    assert _recovered_diary_text(None) is None


def test_a_long_first_sentence_ending_in_a_colon_is_not_eaten_as_a_label() -> None:
    """The label guard is bounded, so it cannot swallow a real opening line.

    The dangerous shape is a genuine first sentence that happens to end in a
    colon. Bounding the drop at ``_DIARY_LABEL_MAX`` keeps the guard useful for
    ``Diary entry:`` without letting it eat prose.
    """
    long_opening = (
        "There is only one thing worth setting down about today, and it is this "
        "single stubborn fact that I cannot talk myself out of no matter how "
        "hard I try:"
    )
    assert len(long_opening) > day_nodes._DIARY_LABEL_MAX
    recovered = _recovered_diary_text(AIMessage(content=f"{long_opening}\n{PROSE}"))
    assert recovered is not None
    assert recovered.startswith("There is only one thing")


# ==========================================================================
# 2 — a parsed tool call still wins
# ==========================================================================


def test_a_parsed_entry_wins_over_recoverable_prose() -> None:
    """Recovery is the FALLBACK route, never the primary one.

    Pinned because the Bedrock path depends on it: if recovery could shadow a
    successful tool call, this fix would quietly change the provider that was
    already working.
    """
    result = {
        "raw": AIMessage(content="PROSE-THAT-MUST-LOSE"),
        "parsed": Diary(entry="PARSED-THAT-MUST-WIN"),
        "parsing_error": None,
    }
    assert _diary_entry_from(result) == "PARSED-THAT-MUST-WIN"


def test_a_bare_diary_is_tolerated_for_a_provider_ignoring_include_raw() -> None:
    """``include_raw`` is a request too; the unpack does not assume it landed."""
    assert _diary_entry_from(Diary(entry="BARE-SHAPE")) == "BARE-SHAPE"


def test_a_blank_parsed_entry_falls_through_to_the_prose() -> None:
    """An empty tool call must not beat usable prose sitting beside it."""
    result = {
        "raw": AIMessage(content=PROSE),
        "parsed": Diary(entry="   "),
        "parsing_error": None,
    }
    assert _diary_entry_from(result) == PROSE


# ==========================================================================
# 3 — through the real node
# ==========================================================================


def test_a_prose_reply_reaches_the_channel_and_the_store_as_the_real_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect, end to end: this used to store ``_DIARY_FALLBACK``."""
    monkeypatch.setattr(
        day_nodes, "get_large", lambda: _RepliesWith(AIMessage(content=PROSE))
    )
    store = InProcessDiaryStore()

    delta = day_diary(_state(), diary_store=store, game_id="g")

    records = delta["private_diaries"]["p-ava"]
    assert [r["text"] for r in records] == [PROSE]
    assert records[0]["text"] != _DIARY_FALLBACK
    # The dual write carries the recovered entry too, at night_index day + 1.
    assert [(e.night_index, e.content) for e in store.read("g", "p-ava")] == [
        (4, PROSE)
    ]


def test_a_reply_with_nothing_usable_still_yields_the_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recovery must not become a way to store an empty entry.

    Paired with the test above against the SAME node and the same store shape,
    so "the fallback appeared" cannot be explained by the node never running.
    """
    monkeypatch.setattr(
        day_nodes, "get_large", lambda: _RepliesWith(AIMessage(content="   "))
    )
    caplog.set_level(logging.DEBUG, logger=DAY_LOGGER)
    store = InProcessDiaryStore()

    delta = day_diary(_state(), diary_store=store, game_id="g")

    records = delta["private_diaries"]["p-ava"]
    assert [r["text"] for r in records] == [_DIARY_FALLBACK]
    assert [(e.night_index, e.content) for e in store.read("g", "p-ava")] == [
        (4, _DIARY_FALLBACK)
    ]
    # And it is no longer silent: the bare ``except: pass`` this fix removed is
    # why the campaign's 50% fallback rate could not be diagnosed afterwards.
    assert any(
        record.levelno == logging.WARNING and "p-ava" in str(record.args)
        for record in caplog.records
        if record.name == DAY_LOGGER
    )


# ==========================================================================
# 4 — one clamp for both routes
# ==========================================================================


def test_recovered_prose_is_folded_and_capped_by_the_same_clamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovered entry is normalised exactly like a parsed one.

    Scripts prose that is BOTH multi-line and over-long, so skipping the clamp
    fails on folding and on the cap rather than on neither. The expected value
    is written through ``_clamp_diary_entry`` and also pinned structurally
    (single line, at the cap), so mutating the clamp cannot move both sides of
    the assertion together.
    """
    sprawling = ("Line one about the vote.\n\n   Line two about the night.  " * 40)
    monkeypatch.setattr(
        day_nodes, "get_large", lambda: _RepliesWith(AIMessage(content=sprawling))
    )

    delta = day_diary(_state(), diary_store=None, game_id="g")

    text = delta["private_diaries"]["p-ava"][0]["text"]
    assert text == _clamp_diary_entry(sprawling)
    assert "\n" not in text
    assert len(text) == DIARY_MAX_CHARS
