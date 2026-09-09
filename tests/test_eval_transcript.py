"""Offline unit tests for the pure eval-transcript renderer (spec 017, Slice 1).

Locks in ``src/graphia/tools/eval_transcript.render_transcript`` — the pure
``(events, players, *, game_index, run_meta) -> str`` that turns one eval game's
ordered ``{node: delta}`` super-step log (plus the final ``players`` map) into a
tagged, human-readable transcript — **without ever running a game, building a
graph, or reaching a model**.

The renderer's source of truth is the *ordered stream log*, NOT a final-state
snapshot: the per-Night pointing channels (``night_round_picks`` /
``night_rounds_log``) are reset every Night in ``night_open``, so a final-state
read would hold only the LAST Night's picks. These tests therefore build a
**synthetic ordered event log by hand** — a setup that yields roles + personas
on the final ``players`` map, **two Nights each with multi-round pointing**, and
**two Days** including a vote with ballots + an execution — and assert:

- the ``<transcript>``/``<setup>``/``<night>``/``<day>``/``<round>`` structure
  is present;
- **strict chronological order** (Night 1 < Day 1 < Night 2 < Day 2), asserted
  by ``.index(...)`` ordering of marker substrings, never verbatim prose;
- **secrets are present**: each player's true role; a Mafioso's ``true_self``
  AND its public legend; **every Night's picks by name** (a Night-2-only pick
  name must appear — the no-Night-lost property at the render layer); and the
  vote initiation + each ballot + the outcome;
- defensive inputs (empty ``events``, ``players={}``, a ``persona=None`` player,
  ``run_meta=None``) each render without raising.

Per architecture §6 (games are non-deterministic) the transcript is NEVER
asserted verbatim — only the tags and the exact tokens (names, role labels,
persona strings) fed into the synthetic inputs. The autouse ``safe_llm`` net is
left intact; no LLM call site is ever reached.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage

# The deterministic diary stand-in, imported rather than copied so the
# single-line sweep below is a statement about the REAL substituted body
# (spec 041 Slice 8) and cannot drift from it. An import only — no node is
# ever called from this module, and no model is ever reached.
from graphia.nodes.day import _DIARY_FALLBACK
from graphia.state import (
    CLOSING_STORY_KWARG,
    CLOSING_STORY_TRUNCATED_KWARG,
    PlayerPersona,
    PlayerState,
)
from graphia.tools import eval_transcript
from graphia.tools.eval_transcript import render_transcript

# The deterministic DAY-SPEECH stand-in (spec 041 Slice 10) — written out as a
# literal rather than imported, unlike ``_DIARY_FALLBACK`` above, for the only
# reason that matters: there is nothing to import. ``_ai_day_action`` returns it
# as an inline literal in its fallback ``DayAction``, so this is the same
# spelling ``tests/test_substitution_counts`` and
# ``tests/test_spec041_substitution_marking`` carry, and these are the bytes a
# committed transcript must hold.
_DAY_ACTION_FALLBACK = "I'm not sure who to trust yet."

# ===========================================================================
# Synthetic roster — three players carrying REAL personas off the final map.
# A Mafioso with both a public legend AND a true self; two honest Citizens.
# Ids deliberately differ from names so an id-vs-name confusion would fail the
# "by name" assertions.
# ===========================================================================

_MAFIA_PERSONA = PlayerPersona(
    personality="coldly calculating",
    manner="clipped and precise",
    public_persona="Dario the harbour fishmonger, up before dawn most days",
    true_self="Dario runs the smuggling ring the fish stall launders for",
)
_CITIZEN_PERSONA = PlayerPersona(
    personality="warm and talkative",
    manner="rambling, fond of anecdotes",
    public_persona="Mira the village schoolteacher who knows everyone",
    true_self="",
)
_CITIZEN2_PERSONA = PlayerPersona(
    personality="taciturn",
    manner="terse",
    public_persona="Bo the blacksmith, quiet at the forge all day",
    true_self="",
)


def _player(
    pid: str,
    name: str,
    role: str,
    *,
    persona: PlayerPersona | None,
    is_alive: bool = True,
) -> PlayerState:
    """A ``PlayerState`` from the real dataclass (mirrors the harness's map)."""
    return PlayerState(
        id=pid, name=name, role=role, is_human=False, is_alive=is_alive, persona=persona
    )


def _final_players() -> dict[str, PlayerState]:
    """The final ``players`` map the renderer reads roles/personas/names from.

    Dario (mafia) carries both layers; Mira and Bo are honest Citizens. Cara is a
    Citizen who dies on Night 1 (still in the final map, ``is_alive=False``) so the
    roster reveals her too — kills only flip ``is_alive``.
    """
    return {
        "p-dario": _player("p-dario", "Dario", "mafia", persona=_MAFIA_PERSONA),
        "p-mira": _player("p-mira", "Mira", "law_abiding", persona=_CITIZEN_PERSONA),
        "p-bo": _player("p-bo", "Bo", "law_abiding", persona=_CITIZEN2_PERSONA),
        "p-cara": _player(
            "p-cara", "Cara", "law_abiding", persona=_CITIZEN_PERSONA, is_alive=False
        ),
    }


# Markers (substrings we put INTO the synthetic inputs, so asserting them is
# matching our own tokens — never the renderer's prose). These mirror the real
# engine templates in ``graphia.prompts`` so the renderer's structural detection
# (vote / kill / recap) fires exactly as it does on a real game's stream.
_NIGHT1_VICTIM = "Cara"  # killed Night 1; pointed at by both Mafiosos round 1
_NIGHT2_ONLY_TARGET = "Bo"  # pointed at on Night 2 only — the no-Night-lost token
# ``VOTE_INITIATE_ANNOUNCE_TEMPLATE`` / ``VOTE_EXECUTED_TEMPLATE`` shapes.
_VOTE_INITIATE_LINE = "Mira has called for a vote to execute Dario."
_VOTE_EXECUTED_LINE = "Dario has been executed. Dario was a Mafia."
# ``resolve_night_kill`` shape — the real night-kill announcement the renderer
# relabels into a ``<kill>`` block.
_NIGHT1_KILL_LINE = "During the night, Cara was killed."
_NIGHT2_KILL_LINE = "During the night, Bo was killed."


def _setup_events() -> list[dict[str, Any]]:
    """Setup super-steps: a name-collect message + the role/persona reveal.

    These stream BEFORE the first ``night_open`` (the renderer folds them into a
    preamble). They carry a public Moderator line and a private role reveal — the
    transcript deliberately keeps private material.
    """
    return [
        {
            "collect_name": {
                "messages": [
                    SystemMessage(content="Welcome to Graphia. The table is set."),
                ]
            }
        },
        {
            "generate_personas": {
                "messages": [
                    SystemMessage(
                        content="You are secretly a Mafia.",
                        additional_kwargs={"private_to": "p-dario"},
                    ),
                ]
            }
        },
    ]


def _night_events(
    night_number: int,
    rounds_log: list[dict[str, str]],
    deciding_round: dict[str, str],
    victim_name: str,
) -> list[dict[str, Any]]:
    """One Night's super-steps: open → multi-round pointing → kill.

    ``night_open`` opens the Night (and in the real engine resets the pointing
    channels — the very reset these tests prove the stream log survives).
    ``mafia_round_start`` carries the cumulative ``night_rounds_log`` (completed
    rounds) and ``mafia_point`` carries the cumulative ``night_round_picks`` for
    the deciding round. ``resolve_night_kill`` announces the victim with the real
    ``"During the night, {name} was killed."`` template, which the renderer
    relabels into an inline ``<kill>{name} — {side}</kill>`` block.
    """
    return [
        {
            "night_open": {
                "messages": [SystemMessage(content="Night falls over the town.")],
                # The reset the engine performs — present so the renderer opening
                # a fresh Night here is exercised against the real delta shape.
                "night_round_picks": {},
                "night_rounds_log": [],
            }
        },
        {
            "mafia_round_start": {
                "night_rounds_log": rounds_log,
            }
        },
        {
            "mafia_point": {
                "night_round_picks": deciding_round,
            }
        },
        {
            "resolve_night_kill": {
                "messages": [
                    SystemMessage(
                        content=f"During the night, {victim_name} was killed."
                    ),
                ],
                "kill_log": [
                    {"cycle": night_number, "name": victim_name, "cause": "night"}
                ],
            }
        },
    ]


def _day_events(
    day_number: int,
    *,
    with_vote: bool,
) -> list[dict[str, Any]]:
    """One Day's super-steps: open → utterances → (optional) vote + execution.

    The Day-1 path includes the vote with ballots and the execution; the Day-2
    path is utterances only (it follows the SECOND Night, so its mere presence —
    after Night 2 — is what pins chronological order).
    """
    events: list[dict[str, Any]] = [
        {
            "day_open": {
                "messages": [SystemMessage(content=f"Day {day_number} breaks.")],
            }
        },
        {
            "day_turn": {
                "messages": [
                    AIMessage(content="I think the fishmonger is lying.", name="Mira"),
                ]
            }
        },
        {
            "day_turn": {
                "messages": [
                    AIMessage(content="The forge kept me busy all night.", name="Bo"),
                ]
            }
        },
    ]
    if with_vote:
        # The vote streams across the engine's three node kinds: ``vote_prompt``
        # (initiation), one ``collect_votes`` super-step per voter (each a single
        # ballot line), then ``resolve_vote`` (tally + outcome). The renderer
        # assembles these into one ``<vote>`` block.
        events += [
            {
                "vote_prompt": {
                    "messages": [SystemMessage(content=_VOTE_INITIATE_LINE)],
                }
            },
            {"collect_votes": {"messages": [SystemMessage(content="Mira: Yes")]}},
            {"collect_votes": {"messages": [SystemMessage(content="Bo: Yes")]}},
            {
                "resolve_vote": {
                    "messages": [
                        SystemMessage(content="The tally: 2 Yes, 0 No."),
                        SystemMessage(content=_VOTE_EXECUTED_LINE),
                    ],
                    "kill_log": [
                        {"cycle": day_number, "name": "Dario", "cause": "execution"}
                    ],
                    # NOTE: no ``day_votes_called`` — an EXECUTED vote ends the Day
                    # (a failed vote would bump it and open a fresh round).
                }
            },
        ]
    return events


def _two_night_two_day_events() -> list[dict[str, Any]]:
    """The full synthetic log: setup → Night 1 → Day 1 (vote+exec) → Night 2 → Day 2.

    Each Night carries a multi-round pointing history (a completed round in
    ``night_rounds_log`` plus a deciding round in ``night_round_picks``). Night 1
    points at Cara; Night 2 points at Bo (the ``_NIGHT2_ONLY_TARGET`` token that
    must survive the per-Night reset to appear in the render).
    """
    events: list[dict[str, Any]] = []
    events += _setup_events()
    # Night 1 — round 1 split (Dario→Cara, plus a stray self-point), deciding
    # round both on Cara.
    events += _night_events(
        night_number=1,
        rounds_log=[{"p-dario": "p-cara", "p-other": "p-mira"}],
        deciding_round={"p-dario": "p-cara"},
        victim_name=_NIGHT1_VICTIM,
    )
    events += _day_events(1, with_vote=True)
    # Night 2 — points at Bo. This Night's picks must survive the night_open
    # reset to appear in the transcript (the no-Night-lost property).
    events += _night_events(
        night_number=2,
        rounds_log=[{"p-dario": "p-bo"}],
        deciding_round={"p-dario": "p-bo"},
        victim_name=_NIGHT2_ONLY_TARGET,
    )
    events += _day_events(2, with_vote=False)
    return events


def _render() -> str:
    """Render the full synthetic two-Night/two-Day game once for the assertions."""
    return render_transcript(
        _two_night_two_day_events(),
        _final_players(),
        game_index=1,
        run_meta={"provider": "ollama", "large_model": "qwen2.5:7b", "games": 2},
    )


# ===========================================================================
# 1. Structure — the tag scaffold is present.
# ===========================================================================


def test_render_emits_the_transcript_setup_night_day_round_tags() -> None:
    """All five structural markers appear: transcript / setup / night / day / round."""
    doc = _render()

    for tag in ("<transcript>", "</transcript>", "<setup>", "<night>", "<day>", "<round>"):
        assert tag in doc, f"missing structural marker {tag!r}"


def test_render_has_two_nights_and_two_days() -> None:
    """Two ``<night>`` and two ``<day>`` sections — one per Night/Day streamed."""
    doc = _render()

    assert doc.count("<night>") == 2
    assert doc.count("<day>") == 2


def test_render_wraps_everything_in_a_single_transcript() -> None:
    """Exactly one ``<transcript>``/``</transcript>`` pair wraps the whole document."""
    doc = _render()

    assert doc.count("<transcript>") == 1
    assert doc.count("</transcript>") == 1
    # The setup roster opens inside the transcript wrapper, before any phase.
    assert doc.index("<transcript>") < doc.index("<setup>") < doc.index("<night>")


# ===========================================================================
# 2. Strict chronological order — Night 1 < Day 1 < Night 2 < Day 2.
# ===========================================================================


def test_render_preserves_strict_chronological_order() -> None:
    """The four phases appear in stream order, asserted by marker ``.index``.

    Night 1's victim (Cara, now an inline ``<kill>`` block) precedes Day 1's vote
    block (``initiator="Mira" target="Dario"``), which precedes Night 2's only
    target (Bo), which precedes Day 2's "Day 2 breaks." — so the Night/Day
    alternation is in strict chronological order, never reordered.
    """
    doc = _render()

    night1_i = doc.index(f"<kill>{_NIGHT1_VICTIM} — Law-abiding Citizen</kill>")
    day1_i = doc.index('<vote initiator="Mira" target="Dario">')
    night2_i = doc.index("Night 2 begins.")
    day2_i = doc.index("Day 2 breaks.")

    assert night1_i < day1_i < night2_i < day2_i


def test_render_first_night_precedes_first_day() -> None:
    """The Night-1 kill block precedes any Day-1 utterance (ordering spine)."""
    doc = _render()

    # Cara's Night-1 death (the <kill> block) is announced before Mira speaks.
    assert doc.index(f"<kill>{_NIGHT1_VICTIM} — Law-abiding Citizen</kill>") < doc.index(
        "I think the fishmonger is lying."
    )


# ===========================================================================
# 3. Secrets present — true roles, the Mafioso's two layers, every Night's
#    picks by name, and the full vote (initiation + ballots + outcome).
# ===========================================================================


def test_render_shows_every_player_true_role() -> None:
    """The setup roster reveals each player's true role label.

    Dario is a Mafia; Mira / Bo / Cara are Law-abiding Citizens — the labels the
    renderer maps from ``role`` (matching the game's own public spellings).
    """
    doc = _render()
    setup = doc[doc.index("<setup>") : doc.index("</setup>")]

    assert '<player name="Dario" role="Mafia">' in setup
    for citizen in ("Mira", "Bo", "Cara"):
        assert f'<player name="{citizen}" role="Law-abiding Citizen">' in setup


def test_render_shows_mafioso_true_self_and_public_legend() -> None:
    """A Mafioso's true self AND its public cover legend BOTH appear (eval secret)."""
    doc = _render()

    # The public legend the table sees.
    assert _MAFIA_PERSONA.public_persona in doc
    # The hidden truth behind the cover — the eval-only secret.
    assert _MAFIA_PERSONA.true_self in doc


def test_render_shows_citizen_honest_persona_without_a_hidden_layer() -> None:
    """A Citizen's single honest persona appears (no Mafioso-style hidden layer)."""
    doc = _render()

    assert _CITIZEN_PERSONA.public_persona in doc  # Mira's honest face
    assert _CITIZEN2_PERSONA.public_persona in doc  # Bo's honest face


def test_render_shows_every_nights_picks_by_name_including_night_two() -> None:
    """Every Night's Mafioso picks appear BY NAME — including a Night-2-only target.

    The no-Night-lost property at the render layer: the deciding pick on Night 2
    targets Bo, a name pointed at on NO other Night. Because the renderer reads
    each Night's pointing from the stream log (not a final snapshot that the
    per-Night reset would have emptied), ``Bo`` must appear as a Night-2 pick. The
    Night-1 victim Cara appears as a Night-1 pick the same way.
    """
    doc = _render()

    # Resolve the two <night> blocks to assert each Night's pick by name.
    first_night_start = doc.index("<night>")
    second_night_start = doc.index("<night>", first_night_start + 1)
    night1_block = doc[first_night_start:second_night_start]
    night2_block = doc[second_night_start:]

    # Night 1: Dario points at Cara (by name), in a pointing round.
    assert "Pointing round" in night1_block
    assert "Dario points at Cara" in night1_block
    # Night 2: Dario points at Bo (by name) — the Night-2-only token survived the
    # night_open reset.
    assert "Dario points at Bo" in night2_block
    # And Bo-as-a-pick is genuinely Night-2-only (never appears in Night 1).
    assert "Dario points at Bo" not in night1_block


def test_render_shows_vote_initiation_each_ballot_and_outcome() -> None:
    """The full Day-1 vote renders as one ``<vote>`` block (spec 022).

    The block names the initiator + target as attributes, lists each ballot as a
    plain ``Name: Yes/No`` line with NO ``Moderator:`` prefix, then the tally,
    then an ``outcome:`` line naming the executed player and revealed side.
    """
    doc = _render()

    # The vote is one delimited block; resolve it to assert its contents.
    start = doc.index("<vote ")
    end = doc.index("</vote>", start) + len("</vote>")
    block = doc[start:end]

    # Initiation is the block's attributes (who called the vote on whom).
    assert '<vote initiator="Mira" target="Dario">' in block
    # Each ballot, by voter, as a plain line — and NO Moderator prefix anywhere.
    assert "Mira: Yes" in block
    assert "Bo: Yes" in block
    assert "Moderator:" not in block
    # The tally line.
    assert "tally: 2 Yes, 0 No" in block
    # The outcome names the executed player and revealed side (verbatim reveal).
    assert "outcome:" in block
    assert _VOTE_EXECUTED_LINE in block


def test_render_keeps_a_private_role_reveal_in_the_transcript() -> None:
    """A private role-reveal message (``private_to``) is kept — it is eval material.

    The transcript deliberately surfaces the normally-hidden private channel,
    attributed to the target by name, rather than dropping it as the in-game UI
    would.
    """
    doc = _render()

    assert "private to Dario" in doc
    assert "You are secretly a Mafia." in doc


# ===========================================================================
# 4. Defensive inputs — empty / sparse / None never raise.
# ===========================================================================


def test_render_empty_events_does_not_raise() -> None:
    """An empty ``events`` log still renders the wrapper + roster, no exception."""
    doc = render_transcript(
        [], _final_players(), game_index=1, run_meta={"provider": "ollama"}
    )

    assert "<transcript>" in doc
    assert "</transcript>" in doc
    # The roster still renders off the final players map even with no phases.
    assert '<player name="Dario" role="Mafia">' in doc


def test_render_empty_players_map_does_not_raise() -> None:
    """``players={}`` renders a "(no players recorded)" roster, never raises.

    With no final map, the roster falls back to the last ``players`` delta in the
    log; here there is none, so the setup block is the empty-roster note.
    """
    doc = render_transcript(
        _two_night_two_day_events(), {}, game_index=1, run_meta=None
    )

    assert "<transcript>" in doc
    assert "(no players recorded)" in doc


def test_render_player_with_no_persona_does_not_raise() -> None:
    """A player with ``persona=None`` renders a "(no persona recorded)" note.

    Defensive: a sparse roster (a player dealt no persona) must surface its name
    + role and a graceful note rather than crashing on ``None.public_persona``.
    The single-line body collapses to an inline ``<player …>…</player>``.
    """
    players = {
        "p-1": _player("p-1", "Nil", "law_abiding", persona=None),
        "p-2": _player("p-2", "Don", "mafia", persona=None),
    }

    doc = render_transcript([], players, game_index=1, run_meta=None)

    assert (
        '<player name="Nil" role="Law-abiding Citizen">(no persona recorded)</player>'
        in doc
    )
    assert '<player name="Don" role="Mafia">(no persona recorded)</player>' in doc


def test_render_none_run_meta_does_not_raise() -> None:
    """``run_meta=None`` renders cleanly (the header simply omits run metadata)."""
    doc = render_transcript(
        _two_night_two_day_events(),
        _final_players(),
        game_index=7,
        run_meta=None,
    )

    # The header still carries the game index even with no run metadata.
    assert "Game 7" in doc
    assert "<transcript>" in doc


def test_render_all_defensive_inputs_at_once_does_not_raise() -> None:
    """Empty events, empty players, and ``run_meta=None`` together still render."""
    doc = render_transcript([], {}, game_index=1, run_meta=None)

    assert "<transcript>" in doc
    assert "</transcript>" in doc


# ===========================================================================
# 5. Per-round Day labels (spec 021) — round splitting keys on ``day_rounds``.
#
# These helpers build multi-round Days whose ``day_turn`` wrap deltas carry the
# engine's own ``day_rounds`` counter (the authoritative round-boundary signal),
# optionally with the closing recap ``SystemMessage`` the engine attaches to the
# same wrap delta. The renderer must split a Day into one ``<round>`` block per
# wrap, reset numbering per Day, hold each recap inside the round it closes, keep
# a *failed* vote inside its round, and land all Day-ending content in the last
# round with no empty trailing block.
# ===========================================================================

# Two-player surviving roster keeps each helper's per-round shape obvious; ids
# differ from names so an id-vs-name confusion would fail the by-name reads.
_ROUND_PLAYERS = {
    "p-mira": _player("p-mira", "Mira", "law_abiding", persona=_CITIZEN_PERSONA),
    "p-bo": _player("p-bo", "Bo", "law_abiding", persona=_CITIZEN2_PERSONA),
    "p-dario": _player("p-dario", "Dario", "mafia", persona=_MAFIA_PERSONA),
}


def _recap_for(round_number: int) -> str:
    """A UNIQUE per-round recap line, shaped like the real recap template.

    Mirrors ``DAY_ROUND_RECAP_TEMPLATE`` (``"Day {day}, {clock} status: …"``) so
    the renderer relabels it into an inline ``<recap>…</recap>`` element (spec
    022). The per-round count clause keeps each one a UNIQUE marker, so a given
    recap can be attributed to exactly one round block.
    """
    return (
        f"Day 1, round {round_number} status: 2 Law-abiding Citizens and "
        f"1 Mafioso remain. No execution votes called yet today. "
        f"No one has been executed today."
    )


def _recap_element_for(round_number: int) -> str:
    """The inline ``<recap>…</recap>`` element the renderer emits for that recap."""
    return f"<recap>{_recap_for(round_number)}</recap>"


def _wrap_round_delta(
    round_number: int, *, with_recap: bool
) -> dict[str, Any]:
    """One round-robin WRAP super-step: the round's last speech (+ optional recap).

    Mirrors ``_round_complete_update``: a single ``day_turn`` delta that carries
    the new ``day_rounds`` count AND, when recaps are enabled, the closing recap
    ``SystemMessage`` appended after the speech. As the renderer's docstring
    notes, one such delta can stand in for a whole speaking round — it both fills
    and CLOSES round ``round_number``.
    """
    messages: list[Any] = [
        AIMessage(content=f"My piece for round {round_number}.", name="Mira"),
    ]
    if with_recap:
        messages.append(SystemMessage(content=_recap_for(round_number)))
    return {"day_turn": {"messages": messages, "day_rounds": round_number}}


def _multi_round_day_events(
    day_number: int, round_count: int, *, with_recap: bool = True
) -> list[dict[str, Any]]:
    """A Day that runs ``round_count`` genuine speaking rounds, each one wrap.

    ``day_open`` opens the Day; each round is a single wrap delta carrying the
    next ``day_rounds`` value (1, 2, …) so the renderer opens a fresh ``<round>``
    per wrap. ``with_recap`` toggles whether the closing recap rides along (the
    recap-off / ``recap_enabled=False`` parity case).
    """
    events: list[dict[str, Any]] = [
        {
            "day_open": {
                "messages": [SystemMessage(content=f"Day {day_number} breaks.")],
            }
        },
    ]
    for round_number in range(1, round_count + 1):
        events.append(_wrap_round_delta(round_number, with_recap=with_recap))
    return events


def _round_blocks(day_block: str) -> list[str]:
    """Split one ``<day>`` block string into its ``<round>`` block substrings."""
    blocks: list[str] = []
    cursor = 0
    while True:
        start = day_block.find("<round>", cursor)
        if start == -1:
            break
        end = day_block.find("</round>", start)
        assert end != -1, "an opened <round> must be closed"
        blocks.append(day_block[start : end + len("</round>")])
        cursor = end + len("</round>")
    return blocks


def _render_one_day(events: list[dict[str, Any]]) -> str:
    """Render a single-Day synthetic log and return only its ``<day>`` block."""
    doc = render_transcript(
        events, _ROUND_PLAYERS, game_index=1, run_meta=None
    )
    start = doc.index("<day>")
    end = doc.index("</day>") + len("</day>")
    return doc[start:end]


# --- §2.1 — each speaking round is its own labeled block --------------------


def test_six_wraps_render_six_round_blocks_labeled_one_through_six() -> None:
    """§2.1: a Day with six wrap deltas renders six ``<round>`` blocks, Round 1..6.

    The count of ``<round>`` blocks equals the number of speaking rounds played,
    and the ``Round k.`` labels appear in ascending order — the reviewer can
    count the labels to recover the true round count.
    """
    day = _render_one_day(_multi_round_day_events(1, 6))

    assert day.count("<round>") == 6
    # Each label is present, and they appear in strict ascending order.
    label_positions = [day.index(f"Round {k}.") for k in range(1, 7)]
    assert label_positions == sorted(label_positions)
    # No Round 7 — numbering stops at the real round count.
    assert "Round 7." not in day


def test_round_block_count_equals_speaking_round_count() -> None:
    """§2.1: the ``<round>`` count tracks the number of wraps for several sizes."""
    for round_count in (1, 2, 3, 5):
        day = _render_one_day(_multi_round_day_events(1, round_count))
        assert day.count("<round>") == round_count
        assert f"Round {round_count}." in day
        assert f"Round {round_count + 1}." not in day


# --- §2.3 — each Moderator recap closes the round it summarizes -------------


def test_each_recap_is_the_last_line_inside_the_round_it_closes() -> None:
    """§2.3: each round's unique recap element is the final line of its own block.

    Each wrap carries a distinguishable recap string; the renderer relabels it
    into an inline ``<recap>…</recap>`` element (spec 022). That element must
    appear inside the block labeled with the same round number, must be that
    block's final content line (after the speech), and must NOT appear in any
    other block.
    """
    day = _render_one_day(_multi_round_day_events(1, 3))
    blocks = _round_blocks(day)
    assert len(blocks) == 3

    for index, block in enumerate(blocks, start=1):
        recap = _recap_for(index)
        element = _recap_element_for(index)
        # The recap belongs to THIS round's block, as a <recap> element...
        assert element in block, f"round {index} recap missing from its block"
        # ...and to no OTHER round's block.
        for other in range(1, 4):
            if other != index:
                assert _recap_for(other) not in block

        # The recap element is the LAST content line of the block (after the
        # speech): only the closing </round> tag follows it.
        content_lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip()
            and not line.strip().startswith("<round>")
            and not line.strip().startswith("</round>")
        ]
        assert content_lines[-1] == element
        # The speech precedes the recap inside the same block.
        assert block.index(f"round {index}") < block.index(recap)


def test_no_round_block_holds_another_rounds_recap() -> None:
    """§2.3: at most one closing recap per block; no cross-round recap leakage."""
    day = _render_one_day(_multi_round_day_events(1, 4))
    blocks = _round_blocks(day)

    for index, block in enumerate(blocks, start=1):
        recaps_present = [
            other for other in range(1, 5) if _recap_for(other) in block
        ]
        assert recaps_present == [index]


# --- §2.1 reset — numbering restarts each Day ------------------------------


def test_round_numbering_resets_at_the_start_of_each_day() -> None:
    """§2.1: the second Day's first block is ``Round 1.`` (numbering resets).

    Day 1 runs three rounds, Day 2 runs two; the second ``<day>`` section must
    begin its own ``Round 1.`` rather than continuing from Day 1's count.
    """
    events = _multi_round_day_events(1, 3) + _multi_round_day_events(2, 2)
    doc = render_transcript(
        events, _ROUND_PLAYERS, game_index=1, run_meta=None
    )

    first_day_start = doc.index("<day>")
    second_day_start = doc.index("<day>", first_day_start + 1)
    day1 = doc[first_day_start:second_day_start]
    day2 = doc[second_day_start:]

    assert day1.count("<round>") == 3
    assert day2.count("<round>") == 2
    # Day 2 restarts numbering at Round 1 and stops at its own count.
    assert "Round 1." in day2
    assert "Round 3." not in day2


# --- §2.2 — a failed vote stays inside its round ----------------------------


def test_failed_vote_stays_inside_its_round_without_opening_a_new_block() -> None:
    """§2.2: a failed vote (``day_votes_called``) does NOT open a new ``<round>``.

    A round contains speeches, then a failed-vote ``<vote>`` block, then more
    speech, then its wrap. The whole pass renders as ONE round block: the round
    count is unchanged by the failed vote, and the ``<vote>`` block sits inside
    the same round block as that round's speeches. The failed vote's ``outcome:``
    line reproduces "The vote fails." (spec 022 keeps the wording).
    """
    events: list[dict[str, Any]] = [
        {
            "day_open": {
                "messages": [SystemMessage(content="Day 1 breaks.")],
            }
        },
        {
            "day_turn": {
                "messages": [
                    AIMessage(content="Opening remarks.", name="Mira"),
                ]
            }
        },
        # A failed vote: initiation, ballots, then a tally + "The vote fails."
        {
            "vote_prompt": {
                "messages": [
                    SystemMessage(
                        content="Mira has called for a vote to execute Bo."
                    )
                ]
            }
        },
        {"collect_votes": {"messages": [SystemMessage(content="Mira: Yes")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Bo: No")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Dario: No")]}},
        {
            "resolve_vote": {
                "messages": [
                    SystemMessage(content="The tally: 1 Yes, 2 No."),
                    SystemMessage(content="The vote fails."),
                ],
                "day_votes_called": 1,
            }
        },
        # The same speaking pass continues, then wraps as round 1.
        _wrap_round_delta(1, with_recap=True),
    ]
    day = _render_one_day(events)
    blocks = _round_blocks(day)

    # The failed vote did NOT open a new round: exactly one block.
    assert day.count("<round>") == 1
    assert len(blocks) == 1
    only = blocks[0]
    # The failed vote is one <vote> block INSIDE that single round block.
    assert "<vote initiator=\"Mira\" target=\"Bo\">" in only
    assert "tally: 1 Yes, 2 No" in only
    assert "outcome: failed — The vote fails." in only
    # The ballots are plain, prefix-free lines inside the vote block.
    assert "Mira: Yes" in only
    assert "Bo: No" in only
    # Alongside that round's speech and its closing recap.
    assert "Opening remarks." in only
    assert _recap_for(1) in only


def test_failed_vote_then_two_more_rounds_renders_three_blocks() -> None:
    """§2.2: a failed vote inside round 1 leaves later round numbering intact.

    Round 1 holds a failed vote and wraps; rounds 2 and 3 follow. The failed
    vote must not inflate the count — exactly three blocks, labeled 1..3.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {
            "resolve_vote": {
                "messages": [
                    SystemMessage(content="Vote tally — Yes 1, No 2."),
                    SystemMessage(content="The vote fails."),
                ],
                "day_votes_called": 1,
            }
        },
        _wrap_round_delta(1, with_recap=True),
        _wrap_round_delta(2, with_recap=True),
        _wrap_round_delta(3, with_recap=True),
    ]
    day = _render_one_day(events)

    assert day.count("<round>") == 3
    for k in (1, 2, 3):
        assert f"Round {k}." in day
    assert "Round 4." not in day


# --- §2.5 — Day endings land in the last block -----------------------------


def test_executed_vote_reveal_lands_in_the_final_round_no_empty_block() -> None:
    """§2.5(a): an executed-vote ``<vote>`` block sits inside the final round.

    Two rounds wrap, then a passing vote ends the Day. The deciding ``<vote>``
    block (tally + execution reveal) must land inside the LAST round block
    (round 2), and there must be no spurious empty ``<round></round>`` after it.
    """
    reveal_line = "Dario has been executed. Dario was a Mafia."
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        _wrap_round_delta(1, with_recap=True),
        _wrap_round_delta(2, with_recap=True),
        {
            "vote_prompt": {
                "messages": [
                    SystemMessage(
                        content="Mira has called for a vote to execute Dario."
                    )
                ]
            }
        },
        {"collect_votes": {"messages": [SystemMessage(content="Mira: Yes")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Bo: Yes")]}},
        {
            "resolve_vote": {
                "messages": [
                    SystemMessage(content="The tally: 2 Yes, 0 No."),
                    SystemMessage(content=reveal_line),
                ],
                "kill_log": [
                    {"cycle": 1, "name": "Dario", "cause": "execution"}
                ],
            }
        },
    ]
    day = _render_one_day(events)
    blocks = _round_blocks(day)

    # Two rounds, not three — the post-wrap vote did not open an empty block.
    assert day.count("<round>") == 2
    assert len(blocks) == 2
    assert "<round></round>" not in day.replace("\n", "").replace(" ", "")
    # The deciding <vote> block (tally + execution reveal) is inside the LAST.
    last = blocks[-1]
    assert "<vote initiator=\"Mira\" target=\"Dario\">" in last
    assert "tally: 2 Yes, 0 No" in last
    assert reveal_line in last
    # And the vote block is not duplicated into the first block.
    assert reveal_line not in blocks[0]
    assert "<vote " not in blocks[0]


def test_no_execution_close_lands_in_the_final_round_no_empty_block() -> None:
    """§2.5(b): a no-execution close + final recap sit inside the final round.

    Three rounds wrap (the last is the round-cap wrap), then ``day_close`` emits
    the "Day ends with no one executed." line and the Day's final recap. Both
    must land inside the LAST round block, with no empty block following.
    """
    no_exec_line = "The Day ends with no one executed."
    final_recap = (
        "Day 1, 12 AM (midnight) status: 2 Law-abiding Citizens and "
        "1 Mafioso remain. No execution votes called yet today. "
        "No one has been executed today."
    )
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        _wrap_round_delta(1, with_recap=True),
        _wrap_round_delta(2, with_recap=True),
        _wrap_round_delta(3, with_recap=True),
        {
            "day_close": {
                "messages": [
                    SystemMessage(content=no_exec_line),
                    SystemMessage(content=final_recap),
                ]
            }
        },
    ]
    day = _render_one_day(events)
    blocks = _round_blocks(day)

    # Three rounds — the final wrap + day_close did not open a fourth, empty one.
    assert day.count("<round>") == 3
    assert len(blocks) == 3
    assert "<round></round>" not in day.replace("\n", "").replace(" ", "")
    # The day-ending content lands in the LAST round block: the no-execution
    # Moderator line, then the final recap relabeled as a <recap> element.
    last = blocks[-1]
    assert f"Moderator: {no_exec_line}" in last
    final_element = f"<recap>{final_recap}</recap>"
    assert final_element in last
    # The final recap element is the LAST content line of the final block.
    content_lines = [
        line.strip()
        for line in last.splitlines()
        if line.strip()
        and not line.strip().startswith("<round>")
        and not line.strip().startswith("</round>")
    ]
    assert content_lines[-1] == final_element


# --- recap-off parity — structure is recap-independent ----------------------


def test_round_structure_is_recap_independent_when_recaps_are_off() -> None:
    """recap-off parity: wraps with ``day_rounds`` but NO recap still split right.

    With ``recap_enabled=False`` the wrap deltas carry ``day_rounds`` but attach
    no recap ``SystemMessage``. The round structure keys on ``day_rounds`` alone,
    so a four-round Day still renders four ``<round>`` blocks labeled 1..4 — no
    recap text is required for the split.
    """
    day = _render_one_day(_multi_round_day_events(1, 4, with_recap=False))

    assert day.count("<round>") == 4
    for k in range(1, 5):
        assert f"Round {k}." in day
    # No recap text leaked in — the wraps carried none.
    for k in range(1, 5):
        assert _recap_for(k) not in day


def test_recap_on_and_off_produce_the_same_round_block_count() -> None:
    """recap-off parity: block count matches with recaps on vs. off (same wraps)."""
    day_on = _render_one_day(_multi_round_day_events(1, 3, with_recap=True))
    day_off = _render_one_day(_multi_round_day_events(1, 3, with_recap=False))

    assert day_on.count("<round>") == day_off.count("<round>") == 3


# ===========================================================================
# 6. Structured block format (spec 022) — each event type is its own
#    consistent, flush-left, delimited block; no alignment whitespace;
#    utterances unchanged; no information lost.
#
# These exercise the renderer against synthetic event logs shaped like the real
# engine templates (``graphia.prompts``) so the structural detection (vote /
# kill / recap / endgame) fires exactly as it does on a real game's stream.
# ===========================================================================

# The real ``end_screen`` Moderator content: a winner line, the events list, the
# full roster, and the persona reveal — all already plain prose on ONE message.
_ENDGAME_CONTENT = "\n".join(
    [
        "The Mafia have won.",
        "",
        "Events this game:",
        "• Night 1: Cara (Law-abiding Citizen)",
        "• Day 1: Dario (Mafia) executed",
        "",
        "Full roster: Dario (Mafia), Mira (Law-abiding Citizen), "
        "Bo (Law-abiding Citizen), Cara (Law-abiding Citizen)",
        "",
        "Who they really were:",
        "• Mira (Law-abiding Citizen) — the village schoolteacher who knows everyone",
        "• Dario (Mafia) — publicly the harbour fishmonger … but was really a "
        "Mafioso: runs the smuggling ring",
    ]
)


def _endgame_event() -> dict[str, Any]:
    """One ``end_screen`` super-step carrying the whole endgame Moderator message."""
    return {"end_screen": {"messages": [SystemMessage(content=_ENDGAME_CONTENT)]}}


def _full_game_events() -> list[dict[str, Any]]:
    """A complete synthetic game exercising every spec-022 block type at once.

    Setup → Night 1 (multi-round pointing, real ``<kill>`` line) → Day 1
    (utterances, a FAILED vote, more rounds with real recap lines, then an
    executed vote) → Night 2 → endgame. Shaped from the real engine templates so
    the renderer emits ``<setup>``/``<night>``/``<kill>``/``<day>``/``<round>``/
    ``<recap>``/``<vote>``/``<endgame>`` blocks.
    """
    events: list[dict[str, Any]] = []
    events += _setup_events()
    events += _night_events(
        night_number=1,
        rounds_log=[{"p-dario": "p-cara"}],
        deciding_round={"p-dario": "p-cara"},
        victim_name=_NIGHT1_VICTIM,
    )
    # Day 1: a failed vote in round 1, then a passing (executed) vote in round 2.
    failed_recap = (
        "Day 1, 9 AM status: 3 Law-abiding Citizens and 1 Mafioso remain. "
        "1 execution vote called today. No one has been executed today."
    )
    exec_recap = (
        "Day 1, 12 PM status: 2 Law-abiding Citizens and 1 Mafioso remain. "
        "1 execution vote called today. Dario was executed today and was "
        "revealed to be Mafia."
    )
    events += [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {
            "day_turn": {
                "messages": [
                    AIMessage(content="I think the fishmonger is lying.", name="Mira")
                ]
            }
        },
        # Failed vote (round 1).
        {
            "vote_prompt": {
                "messages": [
                    SystemMessage(
                        content="Bo has called for a vote to execute Mira."
                    )
                ]
            }
        },
        {"collect_votes": {"messages": [SystemMessage(content="Mira: No")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Bo: Yes")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Dario: No")]}},
        {
            "resolve_vote": {
                "messages": [
                    SystemMessage(content="The tally: 1 Yes, 2 No."),
                    SystemMessage(content="The vote fails."),
                ],
                "day_votes_called": 1,
            }
        },
        # Round 1 wraps with its recap.
        {
            "day_turn": {
                "messages": [
                    AIMessage(content="Let us regroup.", name="Mira"),
                    SystemMessage(content=failed_recap),
                ],
                "day_rounds": 1,
            }
        },
        # Round 2: a passing vote that executes Dario.
        {
            "day_turn": {
                "messages": [
                    AIMessage(content="The forge kept me busy.", name="Bo")
                ]
            }
        },
        {
            "vote_prompt": {
                "messages": [
                    SystemMessage(
                        content="Mira has called for a vote to execute Dario."
                    )
                ]
            }
        },
        {"collect_votes": {"messages": [SystemMessage(content="Mira: Yes")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Bo: Yes")]}},
        {"collect_votes": {"messages": [SystemMessage(content="Dario: No")]}},
        {
            "resolve_vote": {
                "messages": [
                    SystemMessage(content="The tally: 2 Yes, 1 No."),
                    SystemMessage(content=_VOTE_EXECUTED_LINE),
                ],
                "kill_log": [{"cycle": 1, "name": "Dario", "cause": "execution"}],
            }
        },
        # day_close emits the executed-today recap into the last round.
        {
            "day_close": {
                "messages": [SystemMessage(content=exec_recap)],
            }
        },
    ]
    events += _night_events(
        night_number=2,
        rounds_log=[{"p-dario": "p-bo"}],
        deciding_round={"p-dario": "p-bo"},
        victim_name=_NIGHT2_ONLY_TARGET,
    )
    events.append(_endgame_event())
    return events


def _render_full_game() -> str:
    """Render the full block-exercising synthetic game once for the assertions."""
    return render_transcript(
        _full_game_events(), _final_players(), game_index=1, run_meta=None
    )


def _block(doc: str, tag: str) -> str:
    """Slice the first ``<tag>…</tag>`` (inline or multi-line) out of ``doc``.

    Handles both a bare open tag (``<endgame>``) and an attributed one
    (``<vote initiator=… >``) by locating whichever appears first.
    """
    bare = doc.find(f"<{tag}>")
    attributed = doc.find(f"<{tag} ")
    candidates = [i for i in (bare, attributed) if i != -1]
    start = min(candidates)
    end = doc.index(f"</{tag}>", start) + len(f"</{tag}>")
    return doc[start:end]


# --- the vote block ---------------------------------------------------------


def test_executed_vote_block_names_the_revealed_side() -> None:
    """A vote that executed someone: ``outcome:`` names the player AND revealed side.

    The executed vote's outcome line reproduces the engine reveal verbatim
    ("Dario has been executed. Dario was a Mafia."), which names the executed
    player and their revealed side — inside the single ``<vote>`` block.
    """
    doc = _render_full_game()
    # The second (passing) vote block.
    second = doc.index('<vote initiator="Mira" target="Dario">')
    block = doc[second : doc.index("</vote>", second) + len("</vote>")]

    assert "outcome: executed — Dario has been executed. Dario was a Mafia." in block
    # The revealed side is named in that outcome line.
    assert "Mafia" in block


def test_failed_vote_block_outcome_reads_failed() -> None:
    """A failed vote's ``outcome:`` line reads "failed" and reproduces the wording."""
    doc = _render_full_game()
    first = doc.index('<vote initiator="Bo" target="Mira">')
    block = doc[first : doc.index("</vote>", first) + len("</vote>")]

    assert "outcome: failed — The vote fails." in block


def test_vote_ballots_are_plain_lines_with_no_moderator_prefix() -> None:
    """Each ballot is a plain ``Name: Yes/No`` line — no ``Moderator:`` prefix.

    The vote block carries the tally and outcome too; none of its lines wear the
    Moderator voice (spec 022 drops it on ballots, and the tally/outcome are
    relabeled, not Moderator lines).
    """
    doc = _render_full_game()
    second = doc.index('<vote initiator="Mira" target="Dario">')
    block = doc[second : doc.index("</vote>", second) + len("</vote>")]

    for ballot in ("Mira: Yes", "Bo: Yes", "Dario: No"):
        assert ballot in block
    assert "tally: 2 Yes, 1 No" in block
    # No Moderator voice anywhere inside the vote block.
    assert "Moderator:" not in block


# --- the night-kill block ---------------------------------------------------


def test_night_kill_is_inline_block_with_revealed_side() -> None:
    """A night kill is an inline ``<kill>Name — Side</kill>``, distinct from picks.

    Cara dies Night 1; her block names her revealed side (off the final roster)
    and sits inside the night, separate from the "points at" pointing-round
    lines.
    """
    doc = _render_full_game()
    first_night = doc.index("<night>")
    night_block = doc[first_night : doc.index("</night>", first_night)]

    assert "<kill>Cara — Law-abiding Citizen</kill>" in night_block
    # The kill block is distinct from the pointing rounds (both present, but the
    # kill is its own labeled element — the "points at" lines aren't inside it).
    assert "Dario points at Cara" in night_block
    assert "points at" not in "<kill>Cara — Law-abiding Citizen</kill>"


def test_night_kill_block_uses_the_final_roster_side() -> None:
    """The night-kill side comes from the final roster (Mafia vs. Law-abiding)."""
    doc = _render_full_game()
    # Night 2 kills Bo (Law-abiding); a Mafia victim would read "— Mafia".
    second_night = doc.index("<night>", doc.index("<night>") + 1)
    night2 = doc[second_night : doc.index("</night>", second_night)]
    assert "<kill>Bo — Law-abiding Citizen</kill>" in night2


# --- the recap element ------------------------------------------------------


def test_recap_is_its_own_element_carrying_day_clock_counts_votes_executed() -> None:
    """A recap is a ``<recap>…</recap>`` element, not an ordinary Moderator line.

    The element reproduces the spec-018/020 recap content as-is: the day + clock,
    the living counts by side, the votes-called clause, and the executed-today
    clause — and is NOT prefixed ``Moderator:``.
    """
    doc = _render_full_game()
    # The executed-today recap from day_close (round 2).
    recap_text = (
        "Day 1, 12 PM status: 2 Law-abiding Citizens and 1 Mafioso remain. "
        "1 execution vote called today. Dario was executed today and was "
        "revealed to be Mafia."
    )
    element = f"<recap>{recap_text}</recap>"
    assert element in doc
    # It is NOT rendered as an ordinary Moderator line.
    assert f"Moderator: {recap_text}" not in doc
    # Content carried as-is: day, in-world clock, counts, votes, executed.
    assert "Day 1, 12 PM status:" in element  # day + clock
    assert "2 Law-abiding Citizens and 1 Mafioso remain" in element  # counts
    assert "1 execution vote called today" in element  # votes
    assert "Dario was executed today" in element  # executed-today


# --- per-player flush-left setup --------------------------------------------


def test_setup_entries_are_flush_left_with_no_alignment_indentation() -> None:
    """Each ``<player>`` block + its persona fields are flush-left — no 2-/4-space.

    The per-player block opening tags and their persona ``Field: value`` lines all
    start at column 0 (spec 022), where the pre-022 format indented them by 2 and 4
    spaces.
    """
    doc = _render_full_game()
    setup = doc[doc.index("<setup>") : doc.index("</setup>") + len("</setup>")]
    lines = setup.splitlines()

    # No content line in <setup> carries leading whitespace.
    for line in lines:
        assert line == line.lstrip(), f"indented setup line: {line!r}"

    # The per-player blocks open flush-left; their persona fields sit flush-left
    # between the open/close tags.
    assert '<player name="Dario" role="Mafia">' in lines
    assert '<player name="Mira" role="Law-abiding Citizen">' in lines
    assert "Personality: coldly calculating" in lines
    assert "Public legend: Dario the harbour fishmonger, up before dawn most days" in lines
    assert "True self (hidden): Dario runs the smuggling ring the fish stall launders for" in lines


def test_setup_wraps_each_player_persona_in_its_own_player_block() -> None:
    """Each player's persona is its own delimited ``<player>`` block — separated.

    Regression for the format where every player's persona fields ran together
    flush-left under a bare ``Name — Role`` header, with nothing delimiting one
    persona from the next. Now each is wrapped in ``<player name="…" role="…">
    … </player>``, so a reader/parser can isolate one persona without bleeding
    into the next.
    """
    doc = _render()
    setup = doc[doc.index("<setup>") : doc.index("</setup>") + len("</setup>")]

    # One <player> open + close per roster member (Dario, Mira, Bo, Cara).
    assert setup.count("<player ") == 4
    assert setup.count("</player>") == 4

    # Dario's hidden true-self falls strictly INSIDE Dario's own block — between
    # Dario's opening <player …> and its closing </player>, not leaking past it.
    dario_open = setup.index('<player name="Dario" role="Mafia">')
    dario_close = setup.index("</player>", dario_open)
    assert dario_open < setup.index(_MAFIA_PERSONA.true_self) < dario_close


# --- the endgame block ------------------------------------------------------


def test_endgame_is_one_labeled_block_with_winner_roster_and_reveal() -> None:
    """The endgame (winner + full roster + persona reveal) is one ``<endgame>`` block."""
    doc = _render_full_game()
    block = _block(doc, "endgame")

    # Winner line, the full roster, and the persona reveal all inside the block.
    assert "The Mafia have won." in block
    assert "Full roster: Dario (Mafia)" in block
    assert "Who they really were:" in block
    assert "Dario (Mafia) — publicly the harbour fishmonger" in block


def test_endgame_block_is_top_level_not_folded_into_the_last_night() -> None:
    """The ``<endgame>`` block follows — and is outside — the final ``<night>``.

    The endgame is its own labeled block, never buried inside the last Night/Day
    section (spec 022). It opens after the last ``</night>`` and before
    ``</transcript>``.
    """
    doc = _render_full_game()

    last_night_close = doc.rindex("</night>")
    endgame_open = doc.index("<endgame>")
    assert last_night_close < endgame_open
    assert endgame_open < doc.index("</transcript>")
    # The winner line is NOT inside any <night> block.
    night_starts = [
        i for i in range(len(doc)) if doc.startswith("<night>", i)
    ]
    for start in night_starts:
        night = doc[start : doc.index("</night>", start)]
        assert "The Mafia have won." not in night


# --- flush-left / inline one-liners / utterances unchanged ------------------


def test_one_line_sections_are_written_inline() -> None:
    """Inline ``<tag …>…</tag>`` elements really are one line — specs 022, 028, 039, 041.

    Bare ``<kill>`` and ``<recap>`` are spec 022's, and were the whole of this
    test until spec 041 Slice 8 measured the gap: the two ATTRIBUTED inline
    shapes, ``<thought player="…">`` (spec 028) and ``<diary player="…"
    day="…">`` (spec 039), had **no single-line assertion anywhere in the
    suite**. Their inline-ness rested entirely on ``_clamp_diary_entry`` /
    ``_REFLECTION`` bounds upstream — that is, on the model's text happening to
    be short and newline-free, and on a clamp that no test here reads.

    That is not a cosmetic gap for a diary. ``eval_ledger.tokenize_transcript``
    decides a body's KIND per line, off the attributes on the tag that opens
    that same line, so an element broken across two lines gives a reviewer a tag
    with no body and a body with no tag — and spec 041's ``substituted="true"``
    marking, which is read off exactly that tag, would be silently lost for the
    entry it was written about. So the marked diary is swept here too: it is the
    one body the renderer takes VERBATIM (``nodes.day._DIARY_FALLBACK``, filed
    without passing through the clamp at all).

    **SPEC 041 SLICE 10 ADDS A FIFTH SHAPE, ``<speech speaker="…"
    substituted="true">``, AND IT IS THE ONE THE ARGUMENT ABOVE APPLIES TO MOST
    SHARPLY.** The other four are elements in every document that carries them;
    this one exists ONLY when a Day-speech line was substituted — an authored
    line stays the bare ``Speaker: text`` shape and is never an element at all
    — so it is the one inline shape whose single-line-ness cannot be inferred
    from any committed transcript, because no committed transcript contains
    one. Its body is also taken verbatim (the deterministic stand-in, past no
    clamp), exactly as the marked diary's is.

    The sweep is written as "every line that OPENS one of these tags closes it",
    which is the form that catches a break, and each shape is separately
    asserted to be PRESENT — without that a typo'd tag name would make the sweep
    vacuous, and the three attributed shapes do not occur in the base full game
    at all.
    """
    doc = _render_full_game()
    # The two PRIVATE attributed shapes need a Day carrying a reflection and a
    # diary trailer, which the base full game has none of — so they are
    # rendered as a second document rather than by changing a fixture 14 other
    # tests read. The marked SPEECH line needs a third for the same reason and
    # one more: the full game's Day rounds are full of authored utterances, and
    # a substituted one is not something a working game produces.
    doc += "\n" + _render_day_with_private_channels()
    doc += "\n" + _render_day_with_a_substituted_line()

    for line in doc.splitlines():
        for tag in ("kill", "recap", "thought", "diary", "speech"):
            if line.startswith(f"<{tag}>") or line.startswith(f"<{tag} "):
                assert line.endswith(f"</{tag}>"), f"{tag} not inline: {line!r}"

    # ...and every shape really is present as an inline one-liner, so none of
    # the five branches above is swept over an empty set.
    def _inline_count(tag: str) -> int:
        return sum(
            1
            for line in doc.splitlines()
            if (line.startswith(f"<{tag}>") or line.startswith(f"<{tag} "))
            and line.endswith(f"</{tag}>")
        )

    assert _inline_count("kill") >= 1
    assert _inline_count("recap") >= 1
    assert _inline_count("thought") >= 1
    # Two diaries: one the player's own writing, one the deterministic stand-in
    # carrying spec 041's marking. Both must be single-line, and the marked one
    # is the one whose text the renderer does not clamp.
    assert _inline_count("diary") == 2
    assert f'substituted="true">{_DIARY_FALLBACK}</diary>' in doc
    # ONE speech element, and exactly one: the third document's marked line.
    # Every other utterance in all three documents is an authored bare line, so
    # a count above one would mean the wrap had stopped being conditional and
    # the format's dominant line shape had changed.
    assert _inline_count("speech") == 1, (
        "the number of `<speech>` elements is not one, so either the marked "
        "line stopped being wrapped or an AUTHORED line started being — and an "
        "authored line must keep the bare `Speaker: text` shape byte for byte"
    )
    assert f'substituted="true">{_DAY_ACTION_FALLBACK}</speech>' in doc


def _render_day_with_a_substituted_line() -> str:
    """One Day whose round holds a SUBSTITUTED Day-speech line (spec 041 Slice 10).

    The only shape in this module's fixtures that a working game does not
    produce: ``day_turn``'s AI branch writes
    ``additional_kwargs["substituted"] = True`` only when ``_ai_day_action``
    could not decode the model's answer, and the renderer wraps only then. The
    authored line beside it is the shape every other utterance in this module
    has, so this one document holds the pair.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {
            "day_turn": {
                "messages": [
                    AIMessage(
                        content="I think the fishmonger is lying.",
                        name="Mira",
                        additional_kwargs={"speaker": "Mira"},
                    ),
                    AIMessage(
                        content=_DAY_ACTION_FALLBACK,
                        name="Bo",
                        additional_kwargs={
                            "speaker": "Bo",
                            "substituted": True,
                        },
                    ),
                ],
                "day_rounds": 1,
            }
        },
        {"day_close": {"messages": [SystemMessage(content="The Day ends.")]}},
    ]
    return render_transcript(
        events, _final_players(), game_index=1, run_meta=None
    )


def _render_day_with_private_channels() -> str:
    """One Day whose round holds a ``<thought>`` and whose trailer holds diaries.

    The two private channels the base full game does not exercise, rendered as a
    document of their own: a ``day_round_reflect`` delta (spec 028) inside the
    round, and a ``day_diary`` delta (spec 039) in the day-level trailer — one
    authored entry and one carrying spec 041's ``substituted`` flag.
    """
    events: list[dict[str, Any]] = [
        {"day_open": {"messages": [SystemMessage(content="Day 1 breaks.")]}},
        {
            "day_turn": {
                "messages": [AIMessage(content="I heard nothing.", name="Mira")],
                "day_rounds": 1,
            }
        },
        {
            "day_round_reflect": {
                "private_thoughts": {"p-mira": ["Bo will not meet my eye."]}
            }
        },
        {"day_close": {"messages": [SystemMessage(content="The Day ends.")]}},
        {
            "day_diary": {
                "private_diaries": {
                    "p-mira": [
                        {
                            "day": 1,
                            "thoughts_before": 1,
                            "text": "Bo folded the moment the vote was called.",
                        }
                    ],
                    "p-bo": [
                        {
                            "day": 1,
                            "thoughts_before": 0,
                            "text": _DIARY_FALLBACK,
                            "substituted": True,
                        }
                    ],
                }
            }
        },
    ]
    return render_transcript(
        events, _final_players(), game_index=1, run_meta=None
    )


def test_block_content_lines_are_flush_left_no_alignment_spaces() -> None:
    """No content line anywhere is indented — everything is flush-left (spec 022).

    The opening/closing tags and every content line begin at column 0; the old
    2-/4-space alignment indentation is gone throughout the document.
    """
    doc = _render_full_game()

    for line in doc.splitlines():
        if not line:  # blank separators are allowed (they carry no indent anyway)
            continue
        assert not line.startswith(" "), f"indented line found: {line!r}"


def test_player_utterances_are_plain_name_text_lines() -> None:
    """A player's speaking turn stays a plain ``Name: text`` line, exactly as before."""
    doc = _render_full_game()

    assert "Mira: I think the fishmonger is lying." in doc
    assert "Bo: The forge kept me busy." in doc
    # The utterance is not wrapped in any block tag of its own.
    for utterance in (
        "Mira: I think the fishmonger is lying.",
        "Bo: The forge kept me busy.",
    ):
        line = next(
            ln for ln in doc.splitlines() if ln == utterance
        )
        assert "<" not in line and ">" not in line


# --- no information lost -----------------------------------------------------


def test_no_information_lost_across_the_restructure() -> None:
    """Every speech, ballot, kill, recap, and reveal still appears (spec 022).

    The restructure changes only grouping/formatting — so the same player
    speeches, vote ballots, both night-kill victims, the recap content, and the
    endgame persona reveal are all still present in the rendered document.
    """
    doc = _render_full_game()

    # Player speeches.
    assert "Mira: I think the fishmonger is lying." in doc
    assert "Bo: The forge kept me busy." in doc
    assert "Mira: Let us regroup." in doc
    # Every ballot (across both votes), prefix-free.
    for ballot in ("Mira: No", "Bo: Yes", "Dario: No", "Mira: Yes"):
        assert ballot in doc
    # Both night kills, with their revealed sides.
    assert "<kill>Cara — Law-abiding Citizen</kill>" in doc
    assert "<kill>Bo — Law-abiding Citizen</kill>" in doc
    # The execution reveal (verbatim).
    assert _VOTE_EXECUTED_LINE in doc
    # Recap content (the executed-today recap).
    assert "Dario was executed today and was revealed to be Mafia" in doc
    # The private role reveal from setup.
    assert "private to Dario" in doc
    assert "You are secretly a Mafia." in doc
    # The endgame persona reveal.
    assert "Who they really were:" in doc
    assert "Dario (Mafia) — publicly the harbour fishmonger" in doc


# ===========================================================================
# 7. The human seat's marker on its `<player>` tag (spec 038, Slice 5).
#
# `render_transcript` has always received `players: dict[str, PlayerState]` and
# `PlayerState.is_human` has always existed — and until this slice the writer
# read neither. A preserved game could only ever *imply* which seat was the
# person's: from the opening welcome, from a private "you are…" line, or from
# that seat being the one with no character description. The reading view marks
# that seat in bold (spec 038), so the file now says it outright, as one
# additional attribute on one tag.
#
# What these tests are guarding is therefore mostly a NEGATIVE: the change is
# purely additive to the human seat's opening `<player>` tag, and **nothing else
# about the emitted text moves**. This file is 1400 lines of format contract and
# every one of those assertions is the other half of that guarantee — none of
# them needed editing for this slice, which is itself the strongest statement
# available that nothing moved. The diff pin below says it directly rather than
# by absence.
#
# Note "the seat", not "a live person": in a measured `blunder-eval` run the seat
# is played by the scripted stand-in (spec 026) and `is_human` is still `True` on
# it, which is exactly right — it holds the same position in the game, and
# functional-spec §2 asks for it to be marked the same way. Every roster in this
# module's other tests is all-AI (`is_human=False` on all four players), which is
# why none of them carries the marker and why the seat cases live here.
# ===========================================================================

# The attribute the writer appends, spelled out as a literal rather than imported
# from `eval_transcript._HUMAN_ATTR` — a test built from the constant under test
# cannot notice that constant changing. The reader's side of the same literal is
# tied to the writer's in
# `tests/test_transcript_highlight.py::test_the_writer_and_the_reader_agree_on_the_marker_literal`.
_HUMAN_ATTR_TEXT = 'human="true"'


def _human_seat_players(role: str) -> dict[str, PlayerState]:
    """A two-player roster whose FIRST seat is the person's, both on ``role``.

    Both players share a side deliberately: the marker's job is to pick one seat
    out of its own side-mates, so a roster where the seat is the only player of
    its role could not tell "marked the human" from "marked the Mafioso".
    """
    return {
        "p-seat": PlayerState(
            id="p-seat",
            name="Avery",
            role=role,
            is_human=True,
            is_alive=True,
            persona=None,
        ),
        "p-other": PlayerState(
            id="p-other",
            name="Bo",
            role=role,
            is_human=False,
            is_alive=True,
            persona=_CITIZEN2_PERSONA,
        ),
    }


def test_render_marks_a_law_abiding_human_seats_player_tag() -> None:
    """The human seat's cast entry carries ``human="true"``; nobody else's does.

    Appended after ``role=``, so the tag reads
    ``<player name="Avery" role="Law-abiding Citizen" human="true">``. The
    position matters to the reader as well as to the eye: the reading view lifts
    the five detail attributes out of the tag's punctuation and deliberately does
    NOT lift this one, so the marker has to sit in the tag's trailing punctuation
    rather than between two lifted values.
    """
    doc = render_transcript(
        [], _human_seat_players("law_abiding"), game_index=1, run_meta=None
    )

    assert (
        '<player name="Avery" role="Law-abiding Citizen" human="true">' in doc
    ), doc
    # The side-mate the writer did NOT mark is emitted exactly as before.
    assert '<player name="Bo" role="Law-abiding Citizen">' in doc
    assert doc.count(_HUMAN_ATTR_TEXT) == 1


def test_render_marks_a_mafia_human_seats_player_tag() -> None:
    """The same for a person dealt the Mafia side — the marker is role-blind.

    Worth its own case rather than a parametrize note: the seat marker and the
    role label are written by the same expression, and a change that derived one
    from the other would pass on a single side. Both sides are also what the
    reading view needs — the seat is bold *within its side's colour*, and there
    are two colours.
    """
    doc = render_transcript(
        [], _human_seat_players("mafia"), game_index=1, run_meta=None
    )

    assert '<player name="Avery" role="Mafia" human="true">' in doc, doc
    assert '<player name="Bo" role="Mafia">' in doc
    assert doc.count(_HUMAN_ATTR_TEXT) == 1


def test_render_changes_nothing_but_the_human_seats_own_tag() -> None:
    """The diff pin: one line differs, it is the seat's opening tag, and that is all.

    tech-spec §3 names this risk in as many words — "the writer change looks like
    a format change" — and answers it with "it is purely additive to one tag; the
    existing writer tests should pin that nothing else about the emitted text
    moves". This is that pin stated directly instead of inferred from the other
    tests' silence.

    Rendered twice off the SAME synthetic game, with the only difference being
    ``is_human`` on one player, and compared line by line. Line-level rather than
    a substring check because the failure it is written for is a *stray* change —
    a re-ordered attribute, a lost persona line, a moved block — and a substring
    assertion cannot see any of those. The whole two-Night/two-Day game is used
    rather than a bare roster so the comparison spans every section the renderer
    emits, not only ``<setup>``.
    """
    events = _two_night_two_day_events()
    ai_only = _final_players()
    with_seat = _final_players()
    with_seat["p-mira"] = _player(
        "p-mira", "Mira", "law_abiding", persona=_CITIZEN_PERSONA
    )
    with_seat["p-mira"].is_human = True

    before = render_transcript(events, ai_only, game_index=1, run_meta=None)
    after = render_transcript(events, with_seat, game_index=1, run_meta=None)

    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines), (
        "the marker changed the LINE COUNT of the transcript — it must be purely "
        "additive to one existing tag"
    )
    differing = [
        (old, new)
        for old, new in zip(before_lines, after_lines, strict=True)
        if old != new
    ]
    assert differing == [
        (
            '<player name="Mira" role="Law-abiding Citizen">',
            '<player name="Mira" role="Law-abiding Citizen" human="true">',
        )
    ], f"more than the seat's own tag moved: {differing}"
    # ...and the change really is an insertion into that one line, not a rewrite:
    # deleting the marker text restores the previous document byte for byte.
    assert after.replace(f" {_HUMAN_ATTR_TEXT}", "") == before


def test_render_marks_the_human_seat_even_with_no_persona_recorded() -> None:
    """The marker is sourced from ``is_human``, never from the persona side effect.

    ``(no persona recorded)`` identifies the same seat in every one of the 298
    committed transcripts, and it is a **side effect** rather than a marker: only
    AI players get personas, so the string encodes "no persona", not "the person".
    The two are decoupled here in both directions — a marked seat that HAS a
    persona is still marked, and an unmarked player with no persona is still
    unmarked — so a writer that reached for the correlation instead of the flag
    fails on one of the two.
    """
    players = {
        "p-seat": PlayerState(
            id="p-seat",
            name="Avery",
            role="mafia",
            is_human=True,
            is_alive=True,
            persona=_MAFIA_PERSONA,
        ),
        "p-other": PlayerState(
            id="p-other",
            name="Nil",
            role="mafia",
            is_human=False,
            is_alive=True,
            persona=None,
        ),
    }

    doc = render_transcript([], players, game_index=1, run_meta=None)

    # A human seat WITH a persona is still marked...
    assert '<player name="Avery" role="Mafia" human="true">' in doc
    assert _MAFIA_PERSONA.true_self in doc
    # ...and an AI player WITHOUT one is still not.
    assert (
        '<player name="Nil" role="Mafia">(no persona recorded)</player>' in doc
    )
    assert doc.count(_HUMAN_ATTR_TEXT) == 1


def test_render_roster_object_lacking_is_human_gets_no_marker() -> None:
    """The defensive ``getattr``: a sparse roster object simply has no seat.

    Every field in ``_render_setup`` is read defensively — ``name``, ``role`` and
    ``persona`` all go through ``getattr`` with a fallback — because the eval
    harness's roster can be reconstructed from a thin ``players`` delta in the
    stream log rather than from a full ``PlayerState``. ``is_human`` is read the
    same way, so an object that predates the field, or a stand-in built for a
    test, renders its cast entry unmarked instead of raising ``AttributeError``
    and losing the whole transcript.

    Deliberately a plain object rather than a ``PlayerState`` with the field set
    to ``False``: the two are different failures, and only this one exercises the
    fallback. ``False`` is covered by every other test in this module.
    """

    class _SparsePlayer:
        """A roster entry from before ``is_human`` existed — no such attribute."""

        def __init__(self, name: str, role: str) -> None:
            self.id = f"p-{name.lower()}"
            self.name = name
            self.role = role
            self.persona = None

    players: Any = {
        "p-nil": _SparsePlayer("Nil", "law_abiding"),
        "p-don": _SparsePlayer("Don", "mafia"),
    }

    assert not hasattr(players["p-nil"], "is_human"), "the premise: no such field"

    doc = render_transcript([], players, game_index=1, run_meta=None)

    assert "<transcript>" in doc
    assert _HUMAN_ATTR_TEXT not in doc
    assert (
        '<player name="Nil" role="Law-abiding Citizen">(no persona recorded)</player>'
        in doc
    )
    assert '<player name="Don" role="Mafia">(no persona recorded)</player>' in doc


def test_render_marks_only_one_seat_even_if_the_roster_claims_two() -> None:
    """A roster with two human seats emits two markers — the writer does not arbitrate.

    Deliberate, and worth pinning because it looks like an omission. The writer's
    job is to record what the engine's ``players`` map says; a game with two
    ``is_human`` seats is an engine bug, and silently emitting one marker would
    hide it inside a file that then read as coherent. The READER is where the
    never-guess rule lives: two contradicting markers resolve to no seat at all,
    so a corrupt roster degrades to "this game does not say" rather than to a
    confidently wrong bold. (See
    ``tests/test_transcript_highlight.py::test_two_contradicting_markers_yield_no_seat_and_no_fall_back_to_the_greeting``.)
    """
    players = {
        "p-one": PlayerState(
            id="p-one",
            name="Avery",
            role="mafia",
            is_human=True,
            is_alive=True,
            persona=None,
        ),
        "p-two": PlayerState(
            id="p-two",
            name="Bo",
            role="law_abiding",
            is_human=True,
            is_alive=True,
            persona=None,
        ),
    }

    doc = render_transcript([], players, game_index=1, run_meta=None)

    assert doc.count(_HUMAN_ATTR_TEXT) == 2
    assert '<player name="Avery" role="Mafia" human="true">' in doc
    assert '<player name="Bo" role="Law-abiding Citizen" human="true">' in doc


# ===========================================================================
# Spec 040 Slice 8 — the closing story's delta is the ENDING's, not a preamble.
# ===========================================================================
#
# `_ENDGAME_NODES` (Task 8.1) is the fix for the stray-`<preamble>` trap, and
# tech-spec §3 asks for its test explicitly because the defect is SILENT: the
# story was already reaching the transcript, correctly POSITIONED after
# `</endgame>` and wrongly LABELLED `<preamble>`, so nothing raised and every
# existing test in this module stayed green. A branch whose absence costs no red
# needs a test written against the branch itself.
#
# The mechanism, measured on a driven capture before the fix: `end_screen`'s
# branch calls `flush()`, which resets `section_kind` to `None`, so the NEXT
# delta lands in `if section_kind is None: section_kind = "preamble"` and its
# messages are appended by the generic preamble branch. `moderator_story`'s
# delta is always that next delta — spec 040 §2.1 made the story its own node
# and `stream_mode="updates"` yields one chunk per node return, so the facts and
# the story can never share a delta.
#
# The story text below is deliberately MULTI-PARAGRAPH: that is what the real
# node produces (`nodes.endgame._clamp_story` rejoins paragraphs with `"\n\n"`),
# and it is also the shape that made the old rendering worst — one `buf` entry
# holding `\n`, which `_wrap` collapses to an INLINE element spanning five
# physical lines.

_STORY_PARAGRAPHS = (
    "The baker had been counting the wrong things all along.",
    "Mira knew it by the second night, and said nothing.",
    "The harbour was quiet by morning.",
)
_STORY_TEXT = "\n\n".join(_STORY_PARAGRAPHS)


def _story_event(
    *,
    text: str = _STORY_TEXT,
    flagged: bool = True,
    truncated: bool = False,
) -> dict[str, Any]:
    """One ``moderator_story`` super-step carrying the closing story.

    Shaped exactly as ``nodes.endgame.moderator_story`` returns it: a single
    public ``SystemMessage`` with **no** ``name``, whose ``additional_kwargs``
    carry ``CLOSING_STORY_KWARG``. The flag is imported from ``graphia.state``
    rather than spelled as a literal — 039's "import the thing, never copy it",
    so a reword breaks loudly here instead of silently drifting the renderer.

    **The three keyword arguments are Task 8.6's, and the defaults are the
    engine's real shape** — a flagged, unclipped, multi-paragraph story — so
    every caller written before them is unchanged. Each names one axis the
    renderer branches on, and each is stated at the call site rather than
    defaulted into invisibility (the same reason ``_render_story``'s
    ``truncated`` is keyword-only with no default: "the caller forgot" and "the
    cap did not fire" must not be the same call):

    * ``text`` — the story's prose, so the multi-paragraph, single-paragraph and
      delimiter-bearing bodies can each be handed to the real renderer;
    * ``flagged`` — ``False`` drops ``CLOSING_STORY_KWARG`` entirely, which is
      the ``moderator_story`` delta the engine cannot emit but a hand-built
      capture can, and one of the two counterexamples to the broad byte-identity
      claim;
    * ``truncated`` — adds ``CLOSING_STORY_TRUNCATED_KWARG``, again imported.
      Combined with ``flagged=False`` it is the truncation key on an UNFLAGGED
      message, which must mark nothing.

    Both keys are added only when true, mirroring the writer: the engine never
    writes ``closing_story_truncated=False``, so neither does this.
    """
    extra: dict[str, Any] = {}
    if flagged:
        extra[CLOSING_STORY_KWARG] = True
    if truncated:
        extra[CLOSING_STORY_TRUNCATED_KWARG] = True
    return {
        "moderator_story": {
            "messages": [SystemMessage(content=text, additional_kwargs=extra)]
        }
    }


def _render_game_with_story() -> str:
    """The full block-exercising game, plus the closing story's own super-step."""
    return render_transcript(
        [*_full_game_events(), _story_event()],
        _final_players(),
        game_index=1,
        run_meta=None,
    )


def test_a_story_bearing_delta_from_an_unrecognised_node_yields_no_preamble() -> None:
    """``moderator_story``'s delta must never be filed as a ``<preamble>``.

    THE regression test for the stray-`<preamble>` trap (tech-spec §3). The
    assertion is on the count rather than on absence, because a real game has a
    genuine preamble — the setup messages streamed before the first phase
    boundary — so "no `<preamble>` in the document" would be false for the wrong
    reason. What must not happen is a SECOND one.

    Two further assertions pin the two things the count alone cannot see: the
    story's own words are still in the document (the branch must not silently
    drop the delta it now claims), and they are not sitting inside a
    ``<preamble>`` element under some other spelling.
    """
    doc = _render_game_with_story()

    assert doc.count("<preamble>") == 1, (
        "the closing story was filed as a second <preamble> — `_ENDGAME_NODES` "
        "is not claiming the `moderator_story` node, so its delta falls through "
        "to the generic preamble branch after `end_screen`'s `flush()` reset "
        f"`section_kind`. Document:\n{doc}"
    )
    # Restored by Task 8.3, which is the commit that gave the story a block of
    # its own to be present IN. (It was dropped for exactly one commit: Task
    # 8.2 narrowed `_render_endgame` to the facts half before `_render_story`
    # existed, so the story's words were briefly in no block at all. The gap is
    # forced by the plan's task order, not by a choice here.) It is worth
    # keeping: without it this test passes for a renderer that simply throws the
    # `moderator_story` delta away, which is a worse outcome than the mis-label
    # the count above catches.
    assert _STORY_PARAGRAPHS[0] in doc, (
        "the story's words vanished from the transcript entirely — a worse "
        "outcome than the mis-label this test exists to catch"
    )
    preamble = _block(doc, "preamble")
    assert _STORY_PARAGRAPHS[0] not in preamble, (
        "the story's words are inside the <preamble> block:\n" + preamble
    )


def test_the_endgame_nodes_whitelist_names_both_ending_super_steps() -> None:
    """``_ENDGAME_NODES`` is exactly the two nodes the ending streams from.

    A rot guard on the whitelist itself, in the spirit of
    ``test_transcript_highlight``'s ``_MARKER_TAGS`` check: the behavioural test
    above passes for a whitelist that is ``{"moderator_story"}`` alone (the
    facts would then be the stray preamble, and nothing above looks at
    ``<endgame>``), so the set is pinned as well as its effect. Reaches for the
    module-private name deliberately — it is the whitelist itself, and there is
    no public projection of it.
    """
    assert eval_transcript._ENDGAME_NODES == frozenset(
        {"end_screen", "moderator_story"}
    ), (
        "the ending streams from exactly two nodes — `end_screen` (the facts) "
        "and `moderator_story` (the closing story, spec 040 §2.1); found "
        f"{sorted(eval_transcript._ENDGAME_NODES)}"
    )


# ===========================================================================
# Spec 040, Slice 8 Task 8.6 — the closing `<story>` element
# ===========================================================================
#
# THE CLAIM THIS SECTION CARRIES, STATED CORRECTLY. Task 8.6's own line asks for
# *"the contract-stability claim that carries the whole slice: for any capture
# with no flagged message the document is byte-identical to today's"*. **That
# wording is measurably false and is not what these tests pin.** Measured by
# loading `47b483e`'s `eval_transcript` (the commit before Slice 8's first) beside
# the working one and rendering the same nine captures through both: identical for
# an `end_screen`-only delta, for no endgame delta at all, for a flagged message
# on some *other* node, and for an all-empty `end_screen` delta — but DIVERGENT
# for an `end_screen` + `moderator_story` pair whose story message is UNFLAGGED
# (pre-040 filed it as a stray `<preamble>`; today it is a second `<endgame>`) and
# for a `moderator_story` delta with an empty `messages` list (pre-040
# `<preamble></preamble>`; today `<endgame>(no endgame recorded)</endgame>`).
# Neither of those two carries a flagged message anywhere, so each is a
# counterexample to the claim exactly as worded.
#
# **The true statement — and the half that actually protects the 393 committed
# transcripts — turns on the `_ENDGAME_NODES` DELTAS, not on a flag:** *no
# `moderator_story` delta, and no flagged message inside an `end_screen` delta*.
# Every pre-040 capture satisfies both (the node did not exist and neither did
# the flag), so no committed byte can move. The two counterexamples are pinned
# below as behaviour, in
# `test_the_broad_form_of_the_byte_identity_claim_is_false`, so the false version
# cannot be re-derived from the task line; the three tests before it pin the true
# version in three independent places.
#
# **WHY NOT A DIFFERENTIAL TEST, given Task 8.7 proved its half that way.** A
# standing test that loads `47b483e:src/graphia/tools/eval_transcript.py` and
# diffs the two renders would pin a COMMIT HASH into the suite. The first
# legitimate post-040 change to this renderer turns it red with no defect to
# find, and the only available remedy is to bump the hash — at which point it has
# stopped being a statement about the pre-040 format and become a statement about
# "whatever HEAD~n happened to do", i.e. the same species of dated artifact as
# the three stale corpus counts this spec keeps tripping over. It would also make
# a git checkout WITH HISTORY a hard requirement of `pytest -q` (the corpus sweep
# in `test_transcript_highlight` deliberately skips when `evals/transcripts` is
# absent, so the suite is expected to survive a thin tree) and it buys nothing
# the three checks below do not already give:
#
# 1. **At the seam, computed rather than frozen.** `_system_texts` — the function
#    pre-040 `_render_endgame` read through — is still live in the module (the
#    vote assembler uses it), so the equivalence Slice 8 needs can be asserted
#    against it directly: for any delta with no flagged message,
#    `_split_endgame_texts(delta)[0] == _system_texts(delta)`, and the
#    `<endgame>` block is the pre-040 formula's output character for character.
#    Not a golden string, not a snapshot of a commit — an identity between two
#    functions that both still run.
# 2. **At the document, as additivity.** The story-bearing document is the
#    storyless one with one element spliced in before `</transcript>` and nothing
#    else moved, asserted as whole-string equality. Any drift anywhere else in
#    the renderer breaks it, which is the durability a differential was wanted
#    for, without the hash.
# 3. **At the corpus,** by the two facts that make the antecedent universal:
#    `<story` occurs 0 times in all 393 committed files, and the tokenizer
#    round-trip sweep in `test_transcript_highlight` is green over all of them.
#
# Task 8.6's remaining named deliverables are below in order: multi-paragraph
# renders as a block and single-paragraph inline; `<story>` follows `</endgame>`
# as its own top-level block; the adversarial `</story>` is neutralised. The
# tokenizer-side rows (`<story truncated="true">` as one marker span; an inline
# story body staying `plain`; the round trip over a story-bearing document) are
# in `tests/test_transcript_highlight.py`, which owns the reader.

# One paragraph, for the inline arm of `_wrap`'s collapse. Deliberately a
# different sentence from `_STORY_PARAGRAPHS[0]` so a test that renders the
# single-paragraph story cannot accidentally pass on the multi-paragraph one.
_ONE_PARAGRAPH_STORY = "The harbour went quiet, and stayed quiet."

# Task 8.4's adversarial story: model prose that spells the block's OWN
# delimiters. All three shapes `_STORY_TAG_RE` recognises appear, in the three
# positions that behave differently:
#
# * an ATTRIBUTED opening tag as the FIRST paragraph — eaten, which leaves a
#   leading blank entry that only the leading-blank trim removes;
# * a closing tag as a MIDDLE paragraph — eaten, and the `continue` that DROPS
#   the emptied line rather than keeping it as `""` is what stops a paragraph
#   break the model never wrote from being inserted (the line is dropped, so the
#   two blanks that surrounded it become the documented double blank, and no
#   third one appears);
# * a bare opening tag as the LAST paragraph — eaten, leaving a trailing blank
#   entry that only the trailing-blank trim removes.
#
# Every real word survives, in order, unaltered: that is what keeps this
# "structure only, wording untouched" rather than a second clamp.
_ADVERSARIAL_STORY_PARAGRAPHS = (
    '<story truncated="true">',
    "The baker had been counting the wrong things all along.",
    "</story>",
    "Mira knew it by the second night, and said nothing.",
    "<story>",
)
_ADVERSARIAL_STORY_TEXT = "\n\n".join(_ADVERSARIAL_STORY_PARAGRAPHS)


def _endgame_delta(*messages: Any) -> dict[str, Any]:
    """One `end_screen` super-step carrying exactly ``messages``."""
    return {"end_screen": {"messages": list(messages)}}


def _facts_message() -> SystemMessage:
    """The `end_screen` fact sheet, shaped as `nodes.endgame.end_screen` emits it.

    A public `SystemMessage` with NO `additional_kwargs` — which is the whole
    point: the partition's answer for it is "the facts" by default, never by a
    second flag. `state.py` forbids reading `ENDGAME_FACTS_KWARG` here for
    exactly that reason (a two-flag test would need a third answer for a message
    carrying neither), so a facts fixture must not carry one either.
    """
    return SystemMessage(content=_ENDGAME_CONTENT)


def _pre_040_endgame_block(delta: dict[str, Any]) -> str:
    """The `<endgame>` block the PRE-040 `_render_endgame` produced for ``delta``.

    Reconstructed from the two pieces of that function still live in the module —
    `_system_texts` (the vote assembler's, unchanged since spec 022) and `_wrap` —
    rather than frozen as a golden string. That distinction is the point: this is
    an identity between functions that both still run, so it keeps meaning
    something after the next reformat, where a snapshot of today's bytes would
    only mean "someone re-blessed it".

    The formula is `47b483e`'s verbatim: flatten every `SystemMessage` text on
    `"\\n"`, fall back to the placeholder when that is empty, `_wrap` it.
    """
    body: list[str] = []
    for text in eval_transcript._system_texts(delta):
        body.extend(text.split("\n"))
    if not body:
        body = ["(no endgame recorded)"]
    return eval_transcript._wrap("endgame", body)


# The delta shapes a PRE-040 capture can hold — i.e. every shape for which the
# byte-identity claim is true. None carries a flagged message; the last three are
# the near-misses that would make a careless partition answer differently.
_UNFLAGGED_ENDGAME_DELTAS = [
    pytest.param(
        {"messages": [SystemMessage(content=_ENDGAME_CONTENT)]}, id="fact-sheet"
    ),
    pytest.param({"messages": []}, id="empty-message-list"),
    pytest.param({}, id="no-messages-key"),
    pytest.param({"messages": None}, id="messages-not-a-list"),
    pytest.param(
        {"messages": [SystemMessage(content="first."), SystemMessage(content="second.")]},
        id="two-fact-messages",
    ),
    pytest.param(
        {
            "messages": [
                AIMessage(content="player speech"),
                SystemMessage(content="facts."),
            ]
        },
        id="a-non-system-entry-among-them",
    ),
    pytest.param(
        {"messages": [SystemMessage(content="   ")]}, id="whitespace-only-content"
    ),
    pytest.param(
        {
            "messages": [
                SystemMessage(
                    content="facts.",
                    additional_kwargs={CLOSING_STORY_TRUNCATED_KWARG: True},
                )
            ]
        },
        id="truncation-key-without-the-story-flag",
    ),
    pytest.param(
        {
            "messages": [
                SystemMessage(
                    content="facts.", additional_kwargs={CLOSING_STORY_KWARG: False}
                )
            ]
        },
        id="story-flag-present-but-false",
    ),
]


@pytest.mark.parametrize("delta", _UNFLAGGED_ENDGAME_DELTAS)
def test_the_facts_half_is_the_pre_040_endgame_body_when_nothing_is_flagged(
    delta: dict[str, Any],
) -> None:
    """THE CENTRAL FIXTURE, and it pins the TRUE claim rather than the task line's.

    What is asserted here is the narrow, provable half: **for a delta carrying no
    flagged message, `_split_endgame_texts`' facts half is exactly the list
    pre-040's `_render_endgame` read (`_system_texts`), there is no story half at
    all, nothing is reported clipped, and the `<endgame>` block that comes out is
    the pre-040 formula's output character for character.** Every pre-040 capture
    in the 393 committed transcripts is of one of these shapes, so this is the
    statement that says no committed byte can move.

    What is NOT asserted, because it is false: "for any capture with no flagged
    message the document is byte-identical to today's". See the section header —
    a `moderator_story` delta diverges whether its message is flagged, unflagged
    or absent, and two of those three carry no flag. The claim's real antecedent
    is about the DELTAS (`_ENDGAME_NODES`), not about a flag.

    The three shapes at the end of the parameter list are the near-misses, and
    each is here because a plausible partition answers it wrongly: a message
    carrying the truncation key but not the story flag must land in the FACTS
    (the `flagged and` guard in the split) and must not report truncation of a
    block it is no part of; a message whose `closing_story` is explicitly `False`
    is not a story (`bool(extra.get(...))`, not `in extra`); and whitespace-only
    content is skipped by both functions alike.
    """
    facts, story, clipped = eval_transcript._split_endgame_texts(delta)

    assert facts == eval_transcript._system_texts(delta), (
        "the facts half diverged from what pre-040's `_render_endgame` rendered "
        "for this delta — the byte-identity claim for every committed transcript "
        "rests on these being the same list"
    )
    assert story == [], f"an unflagged delta produced a story half: {story!r}"
    assert clipped is False, "an unflagged delta reported a clipped story"
    assert eval_transcript._render_endgame(delta, {}) == _pre_040_endgame_block(delta)


def test_a_capture_with_no_story_delta_renders_no_story_element_at_all() -> None:
    """A pre-040 capture gets no `<story>`, not an empty one — absence, not a stub.

    The `if not lines: return None` guard's witness. Delete it and every one of
    the 393 committed transcripts would gain a `<story></story>` on re-render:
    `_render_phases` calls `_render_story` on EVERY `_ENDGAME_NODES` delta, so an
    `end_screen`-only capture reaches it with an empty story half on every game.
    Nothing would raise and the round trip would still hold — the file would just
    quietly claim the Moderator told a story and said nothing.
    """
    doc = _render_full_game()

    assert "<story" not in doc, f"an empty `<story>` element was emitted:\n{doc}"
    assert "</story>" not in doc


def test_an_empty_endgame_delta_keeps_the_verbatim_placeholder() -> None:
    """`(no endgame recorded)` is the byte string spec 022 shipped, not a paraphrase.

    Inherited as a pre-existing zero-red row: nothing in the suite defended the
    placeholder's wording, so `(no endgame recorded)` could be reworded to
    anything with no red anywhere — and it is the one line a sparse capture's
    whole `<endgame>` consists of. Slice 8 gave the wording a second reason to be
    pinned: `_render_endgame`'s docstring now offers it as the thing that keeps a
    sparse capture rendering as it always has, and Task 8.3 routed the all-empty
    delta through the `not story` half of its gate specifically to preserve it.

    Asserted as the whole element, so a placeholder that survived inside a
    changed wrapper still fails.
    """
    doc = render_transcript(
        [*_setup_events(), _endgame_delta()],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert "<endgame>(no endgame recorded)</endgame>" in doc, (
        "the empty-capture placeholder moved. It is spec 022's shipped bytes and "
        f"a reword is a format change dressed up as a refactor. Document:\n{doc}"
    )


def test_the_closing_story_is_purely_additive_to_the_document_it_lands_in() -> None:
    """Adding the story's super-step moves nothing else in the document, at all.

    The document-level form of the contract-stability claim, and the one that
    makes it durable without pinning a commit hash: the story-bearing render is
    the storyless render with **one element spliced in before `</transcript>` and
    not one other byte touched**, asserted as whole-string equality over a game
    that exercises every block type spec 022 defines.

    This is what a differential against `47b483e` was really being asked for, in
    a form that keeps working: any drift anywhere in the renderer — a phantom
    `(no endgame recorded)` under the real endgame (the `facts or not story` gate
    reverted to `if True:`), the story folded into `<endgame>` instead of
    appended, a moved header, a changed `<setup>` — breaks the equality, and
    there is no hash to bump when it does.
    """
    without = _render_full_game()
    with_story = _render_game_with_story()

    tail = "\n</transcript>"
    assert without.endswith(tail)
    expected_block = "<story>\n" + _STORY_TEXT + "\n</story>"
    assert with_story == without[: -len(tail)] + "\n" + expected_block + tail, (
        "the closing story did not land as a pure addition — something else in "
        f"the document moved.\nwithout:\n{without}\n\nwith:\n{with_story}"
    )


def test_the_broad_form_of_the_byte_identity_claim_is_false() -> None:
    """The counterexamples, pinned so the false claim cannot be re-derived.

    Task 8.6's line, and the `_render_endgame` docstring it was written from, both
    say the document is byte-identical to pre-040's *"for any capture with no
    flagged message"*. It is not, and the two counterexamples are recorded here
    as behaviour rather than as prose because prose is what produced the wrong
    claim in the first place. Both shapes carry NO flagged message anywhere:

    * a `moderator_story` delta whose message is UNFLAGGED — pre-040 filed it
      through the `preamble` fall-through as `<preamble>Moderator: …</preamble>`;
      today `_ENDGAME_NODES` claims the node and the unflagged text is, correctly,
      the facts, so it renders a SECOND `<endgame>` block;
    * a `moderator_story` delta with an empty `messages` list — pre-040
      `<preamble></preamble>`; today the `not story` half of the gate gives it
      the `(no endgame recorded)` placeholder.

    Neither can come off the engine (`moderator_story` emits one flagged message,
    or `None` on failure — never an unflagged one and never an empty list), which
    is why no guard was added for either and why this test asserts what the
    renderer DOES rather than what it should do instead. Its job is to stop the
    next reader from writing the broad claim as a test that passes for the wrong
    reason, and to record that the divergence is confined to shapes no capture in
    `evals/transcripts` can contain.
    """
    without = _render_full_game()
    unflagged = render_transcript(
        [*_full_game_events(), _story_event(flagged=False)],
        _final_players(),
        game_index=1,
        run_meta=None,
    )
    empty = render_transcript(
        [*_full_game_events(), {"moderator_story": {"messages": []}}],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert unflagged != without and empty != without, (
        "both counterexamples now render identically to a capture without the "
        "delta — the broad claim may have become true, in which case this test "
        "and the section header above it should be rewritten rather than deleted"
    )
    # ...and this is HOW each diverges, so a future change is diagnosed rather
    # than merely reported.
    assert unflagged.count("<endgame>") == 2, unflagged
    assert "<story" not in unflagged
    assert empty.count("<endgame>") == 2, empty
    assert "<endgame>(no endgame recorded)</endgame>" in empty, empty
    assert "<story" not in empty


def test_the_story_is_a_top_level_block_of_its_own_after_the_endgame() -> None:
    """`</endgame>` … `<story>` — the position is the document's shape, not a habit.

    Task 8.3's structural deliverable. Four things, each of which a plausible
    "simplification" breaks: the story is its own element (not folded into
    `<endgame>`, which is what a `_wrap("endgame", …)` inside `_render_story`
    would produce); it opens AFTER the endgame closes; it is the last element
    inside `<transcript>`; and the facts block does not contain the story's words.

    "Last inside `<transcript>`" is asserted on the closing tag rather than on
    the opening one, because a story folded into the endgame would still satisfy
    "the story's words come after the last `</night>`".
    """
    doc = _render_game_with_story()

    endgame_close = doc.index("</endgame>")
    story_open = doc.index("<story>")
    assert endgame_close < story_open, (
        "the story is not a sibling AFTER the endgame — either it opened first "
        f"or it is inside the endgame block:\n{doc}"
    )
    assert doc.count("<story>") == 1 and doc.count("</story>") == 1
    assert doc.endswith("</story>\n</transcript>"), (
        f"the story is not the last element inside <transcript>:\n{doc[-300:]}"
    )
    endgame = _block(doc, "endgame")
    for paragraph in _STORY_PARAGRAPHS:
        assert paragraph not in endgame, (
            f"the story's prose is inside <endgame>:\n{endgame}"
        )


def test_a_combined_endgame_delta_keeps_facts_and_story_as_ordered_siblings() -> None:
    """One delta carrying BOTH voices still splits them, facts block first.

    A shape the engine cannot emit — `end_screen` and `moderator_story` are
    separate super-steps, verified on a driven capture — and the only shape in
    which two of the slice's decisions are observable at all, which is why it is
    tested rather than dismissed. `_split_endgame_texts` is written as a
    partition precisely so a synthetic or hand-built capture that hands it both
    at once is rendered rather than half-dropped.

    On the two-delta path the ordering inside the `_ENDGAME_NODES` branch is
    invisible (each delta contributes one block, and the deltas are already
    ordered), and so is the narrowing of `_render_endgame`'s input (an
    `end_screen` delta carries no flagged message, so the facts half and
    `_system_texts` are the same list). Here both bite: the story must not appear
    in `<endgame>`, and `</endgame>` must still precede `<story>`.
    """
    doc = render_transcript(
        [
            *_setup_events(),
            _endgame_delta(
                _facts_message(),
                SystemMessage(
                    content=_STORY_TEXT,
                    additional_kwargs={CLOSING_STORY_KWARG: True},
                ),
            ),
        ],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    endgame = _block(doc, "endgame")
    story = _block(doc, "story")

    assert "The Mafia have won." in endgame
    for paragraph in _STORY_PARAGRAPHS:
        assert paragraph not in endgame, (
            "the story's prose is in the facts block — `_render_endgame` is "
            f"reading every SystemMessage again, not the facts half:\n{endgame}"
        )
        assert paragraph in story
    assert doc.index("</endgame>") < doc.index("<story>"), (
        f"the story block was emitted before the facts block:\n{doc}"
    )


def test_a_multi_paragraph_story_renders_as_a_block_with_its_breaks_intact() -> None:
    """Three paragraphs → an opening tag on its own line and every blank kept.

    Two failures this catches, and they are the two the implementation notes warn
    about hardest:

    * handing `_wrap` ONE entry that holds `"\\n"` (`lines.append(text)` instead
      of `lines.extend(text.split("\\n"))`) — `_wrap` counts non-empty *entries*
      for its collapse, so the whole story becomes an "inline" element spanning
      five physical lines, which is the one shape spec 038 rules out for a
      claimed body;
    * `_wrap` joining its blank-filtered `content` instead of `body` — a one-word
      "tidy" of a function whose two locals differ by exactly the blank lines,
      which would delete every paragraph break from every multi-line block in the
      format, `<story>` included. The element is asserted whole for that reason:
      a per-paragraph containment check passes on both mutations.
    """
    story = _block(_render_game_with_story(), "story")

    assert story == "<story>\n" + "\n\n".join(_STORY_PARAGRAPHS) + "\n</story>", (
        "the multi-paragraph story is not a block with its paragraph breaks "
        f"intact:\n{story!r}"
    )
    assert story.startswith("<story>\n") and story.endswith("\n</story>")


def test_a_single_paragraph_story_renders_inline_like_any_one_line_block() -> None:
    """One paragraph → `<story>…</story>` on one physical line, and that is correct.

    The elegant consequence Task 8.3 asks nobody to "fix": `_wrap`'s single-entry
    collapse makes a one-paragraph story inline and a multi-paragraph one a
    block, exactly as `<night>`, `<day>` and `<preamble>` have behaved since spec
    022. The two forms are the format's own rule applied to a body whose length
    varies, not an inconsistency.

    Also the only witness for that collapse branch anywhere in the suite —
    inherited as a pre-existing zero-red row. Delete the `len(content) == 1`
    branch from `_wrap` and every one-line section in the format silently becomes
    three lines; nothing else in the suite notices.
    """
    doc = render_transcript(
        [*_full_game_events(), _story_event(text=_ONE_PARAGRAPH_STORY)],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert f"<story>{_ONE_PARAGRAPH_STORY}</story>" in doc, (
        f"a one-paragraph story did not collapse inline:\n{doc[-300:]}"
    )
    assert "<story>\n" not in doc


def test_a_tag_shaped_paragraph_in_the_story_prose_is_neutralised() -> None:
    """Model prose spelling `<story>` / `</story>` cannot break the block boundary.

    Task 8.4, asserted as the WHOLE element because every one of its four
    failure modes leaves the real paragraphs intact and only the punctuation
    around them wrong:

    * the neutralisation loop deleted — the delimiters survive verbatim, and a
      parser scanning for the close finds one mid-story;
    * the delimiter-only-line `continue` deleted — the emptied line stays as
      `""`, and an empty line IS a paragraph break in this format, so a
      structural fix would smuggle in a break the model never wrote (here: a
      third consecutive blank where the format's own rule gives two);
    * the pattern narrowed to the opening forms — the closing tag survives;
    * the pattern narrowed to bare tags — the attributed opening form survives,
      and that shape is real rather than hypothetical: it is what this renderer
      itself writes for a clipped story.

    The trims earn their place here too, and only in a multi-paragraph body: the
    eaten first and last paragraphs leave a leading and a trailing blank entry,
    and without the trims the block opens and closes on empty lines. (In a
    one-entry body they are invisible — `_wrap(["", "one para"])` collapses to
    `<story>one para</story>` either way, which is why the fixture has two real
    paragraphs.)

    Wording untouched is asserted separately from structure: both real sentences
    survive character for character, in order.
    """
    doc = render_transcript(
        [*_full_game_events(), _story_event(text=_ADVERSARIAL_STORY_TEXT)],
        _final_players(),
        game_index=1,
        run_meta=None,
    )
    story = _block(doc, "story")

    # Exactly one opening and one closing delimiter in the whole document, and
    # they are the renderer's own.
    assert doc.count("<story") == 1, f"a delimiter survived in the prose:\n{story!r}"
    assert doc.count("</story>") == 1
    # The documented double blank where the eaten paragraph stood — two, because
    # the format's own separators surrounded the dropped line, and NOT three,
    # which is what keeping the emptied line as `""` would give.
    assert story == (
        "<story>\n"
        + _ADVERSARIAL_STORY_PARAGRAPHS[1]
        + "\n\n\n"
        + _ADVERSARIAL_STORY_PARAGRAPHS[3]
        + "\n</story>"
    ), f"the neutralised story is not the expected shape:\n{story!r}"
    # Structure only, wording untouched (spec 022) — stated on its own so a
    # failure says which half broke.
    for real in (_ADVERSARIAL_STORY_PARAGRAPHS[1], _ADVERSARIAL_STORY_PARAGRAPHS[3]):
        assert real in story, f"a real paragraph was altered or dropped: {real!r}"


def test_prose_sharing_its_line_with_a_delimiter_keeps_every_word() -> None:
    """A paragraph that merely ENDS with `</story>` keeps its place and its words.

    The other half of Task 8.4: the drop applies to a line that was NOTHING but
    delimiters. A line still carrying prose is kept, in order, with only the
    tag-shaped punctuation removed — including the trailing space left behind,
    which is deliberately not tidied (a trailing space is neither structure nor
    wording, and removing it would be the second structural rule that "narrower
    than 039's clamp" exists to forbid).
    """
    tail = f"{_STORY_PARAGRAPHS[1]} </story>"
    doc = render_transcript(
        [
            *_full_game_events(),
            _story_event(text=f"{_STORY_PARAGRAPHS[0]}\n\n{tail}"),
        ],
        _final_players(),
        game_index=1,
        run_meta=None,
    )
    story = _block(doc, "story")

    assert story == (
        f"<story>\n{_STORY_PARAGRAPHS[0]}\n\n{_STORY_PARAGRAPHS[1]} \n</story>"
    ), f"prose sharing a line with a delimiter was not preserved:\n{story!r}"
    assert doc.count("</story>") == 1


def test_a_story_that_is_nothing_but_delimiters_yields_no_story_element() -> None:
    """Neutralise everything and the element is absent, never `<story></story>`.

    The sharpest witness for the `if not lines: return None` guard: here the
    story half is populated (so the `facts or not story` gate has already
    suppressed the `<endgame>`) and only the neutralisation empties it. Without
    the guard the document ends with an empty element asserting that a story was
    told, under a delta that also produced no endgame block — the worst of both
    absences.
    """
    doc = render_transcript(
        [*_full_game_events(), _story_event(text="</story>")],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert "<story" not in doc, f"an empty story element was emitted:\n{doc[-300:]}"
    assert "</story>" not in doc


# --- Task 8.7's attribute: `<story truncated="true">` ------------------------


def test_a_clipped_story_renders_the_truncated_attribute_on_its_own_tag() -> None:
    """A clipped story says so, on the opening tag, in the document.

    §2.5's deliverable: a reviewer judging a story that stops early must be able
    to attribute the stop to the CAP rather than to the storyteller, and the fact
    cannot be recomputed from the text (a story of exactly `STORY_MAX_PARAGRAPHS`
    paragraphs is indistinguishable from a longer one that lost its last).

    **The fixture is built from the IMPORTED `CLOSING_STORY_TRUNCATED_KWARG`**,
    not from a copied `"closing_story_truncated"` — 039's rule, and here it is
    the assertion that kills a misspelt literal on the reader side: a renderer
    reading the wrong key would still be handed the right one by this fixture and
    would silently emit the plain tag.
    """
    doc = render_transcript(
        [*_full_game_events(), _story_event(truncated=True)],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert '<story truncated="true">' in doc, (
        "a clipped story rendered no truncation marker — a reviewer would blame "
        f"the storyteller for the cap's cut. Document tail:\n{doc[-400:]}"
    )
    story = _block(doc, "story")
    assert story.startswith('<story truncated="true">\n')
    assert story.endswith("\n</story>")


def test_an_unclipped_story_renders_the_plain_story_tag() -> None:
    """THE ANTI-VACUITY CONTROL, and without it the test above proves nothing.

    "A clipped story renders the attribute" is satisfied by a renderer that
    emits the attribute ALWAYS — including on the ordinary, uncut story that is
    the overwhelming majority of games. That renderer teaches reviewers to ignore
    the marker, which is precisely the failure `_TRUNCATED_ATTR`'s
    "absent, never `truncated="false"`" note exists to prevent.

    Also the witness for the swapped arms and for the unflagged path being routed
    through `_wrap` rather than `_wrap_attr(…, "")`: the word `truncated` must not
    appear in the document at all.
    """
    doc = _render_game_with_story()

    assert "truncated" not in doc, (
        "an unclipped story carries a truncation marker somewhere — the marker "
        f"is conditional, never a field:\n{doc[-400:]}"
    )
    assert _block(doc, "story").startswith("<story>\n")


def test_the_truncated_attribute_is_exactly_the_literal_the_reader_leaves_alone() -> None:
    """`truncated="true"` — the literal, not a shape.

    Spelled out here rather than derived from `_TRUNCATED_ATTR`, which is the
    only way this row can say anything: a test built from the constant is green
    for `clipped="1"` too. The exact bytes matter to two readers — the tokenizer,
    which must find no attribute NAME it knows inside them (`truncated` is
    deliberately absent from `eval_ledger._ATTR_NAMES`, so the whole opening tag
    stays one marker span), and a future Phase-7 judge parser.
    """
    assert eval_transcript._TRUNCATED_ATTR == 'truncated="true"'


@pytest.mark.parametrize(
    ("story_text", "plain_open"),
    [
        pytest.param(_STORY_TEXT, "<story>\n", id="multi-paragraph-block"),
        pytest.param(_ONE_PARAGRAPH_STORY, "<story>", id="single-paragraph-inline"),
    ],
)
def test_the_clipped_document_differs_from_the_unclipped_one_in_the_opening_tag_only(
    story_text: str, plain_open: str
) -> None:
    """Same story, clipped or not: only the opening tag moves. Both `_wrap` forms.

    The differential statement of the attribute's blast radius, asserted as whole
    documents so it covers the body, the position, the closing tag and everything
    upstream of the story at once. Swept over both of `_wrap`'s arms, because the
    attribute-bearing path is `_wrap_attr` and the plain path is `_wrap` — two
    functions that agree today over an empty attribute run and are deliberately
    NOT one call, so that the byte-identity of an unclipped story does not rest
    on `_wrap_attr`'s implementation.

    This is the row that catches a truncated arm which is correct about the
    attribute and wrong about anything else — a body gaining a marker line, or
    the collapse skipped so a clipped one-paragraph story becomes a block while
    its unclipped twin stays inline.
    """
    def render(*, truncated: bool) -> str:
        return render_transcript(
            [
                *_full_game_events(),
                _story_event(text=story_text, truncated=truncated),
            ],
            _final_players(),
            game_index=1,
            run_meta=None,
        )

    plain = render(truncated=False)
    clipped = render(truncated=True)

    assert plain.count(plain_open) == 1
    clipped_open = plain_open.replace("<story>", '<story truncated="true">')
    assert plain.replace(plain_open, clipped_open, 1) == clipped, (
        "the clipped render differs from the unclipped one somewhere other than "
        f"the opening tag.\nplain:\n{plain[-400:]}\n\nclipped:\n{clipped[-400:]}"
    )


def test_the_truncation_key_on_an_unflagged_message_marks_nothing() -> None:
    """A facts message carrying the truncation key cannot mark a story it is not in.

    The `flagged and` guard's witness, on the shape that makes it load-bearing:
    ONE delta holding a facts message with the truncation key AND a story message
    without it. Drop the guard and the `<story>` block claims a cut that happened
    to some other text — the attribute is a claim about the prose inside THIS
    block, and this format has nowhere to say "the facts were clipped".
    """
    doc = render_transcript(
        [
            *_setup_events(),
            _endgame_delta(
                SystemMessage(
                    content=_ENDGAME_CONTENT,
                    additional_kwargs={CLOSING_STORY_TRUNCATED_KWARG: True},
                ),
                SystemMessage(
                    content=_STORY_TEXT,
                    additional_kwargs={CLOSING_STORY_KWARG: True},
                ),
            ),
        ],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert "truncated" not in doc, (
        "the truncation key on the FACTS message marked the story block:\n"
        f"{doc[-400:]}"
    )
    assert "<story>\n" in doc


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, ([_STORY_TEXT], [], False), id="unflagged-is-facts"),
        pytest.param(
            {CLOSING_STORY_KWARG: True},
            ([], [_STORY_TEXT], False),
            id="flagged-not-clipped",
        ),
        pytest.param(
            {CLOSING_STORY_KWARG: True, CLOSING_STORY_TRUNCATED_KWARG: True},
            ([], [_STORY_TEXT], True),
            id="flagged-and-clipped",
        ),
        pytest.param(
            {CLOSING_STORY_TRUNCATED_KWARG: True},
            ([_STORY_TEXT], [], False),
            id="clipped-key-without-the-flag",
        ),
        pytest.param(
            {CLOSING_STORY_KWARG: True, CLOSING_STORY_TRUNCATED_KWARG: False},
            ([], [_STORY_TEXT], False),
            id="flagged-clipped-explicitly-false",
        ),
    ],
)
def test_the_endgame_split_reports_truncation_only_for_a_flagged_and_truncated_message(
    kwargs: dict[str, Any], expected: tuple[list[str], list[str], bool]
) -> None:
    """`_split_endgame_texts` is `(facts, story, clipped)`, swept over both flags.

    The unit-level statement of the shape Task 8.7 widened — §2.9 pinned
    `(facts, story)` and was amended rather than left quietly false — and the
    reason the widening was chosen over a second `_story_truncated(delta)`
    scanner: both answers come off the SAME `additional_kwargs` dict in the SAME
    pass, so "which of these messages is the story" is answered once and the two
    answers cannot drift.

    Both keys are read through the imported constants, so a copied literal on
    either axis fails here rather than filing stories under `<endgame>` for ever
    after a reword.
    """
    delta = {"messages": [SystemMessage(content=_STORY_TEXT, additional_kwargs=kwargs)]}

    assert eval_transcript._split_endgame_texts(delta) == expected


def test_a_story_message_whose_additional_kwargs_is_not_a_dict_does_not_raise() -> None:
    """A thin or malformed message degrades; the renderer never raises on a read.

    `additional_kwargs` is a plain dict on every real message, and the defensive
    `isinstance(extra, dict)` read is there for a hand-built capture or an older
    log. Degradation, not a crash: with no readable flag the text is the FACTS,
    which is the same default an ordinary Moderator message gets, so the words
    still reach the transcript.
    """
    message = SystemMessage(content=_STORY_TEXT)
    message.additional_kwargs = None  # type: ignore[assignment]

    doc = render_transcript(
        [*_setup_events(), _endgame_delta(message)],
        _final_players(),
        game_index=1,
        run_meta=None,
    )

    assert _STORY_PARAGRAPHS[0] in doc, f"the text was dropped entirely:\n{doc}"
    assert "<story" not in doc
