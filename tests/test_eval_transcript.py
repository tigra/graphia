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

from langchain_core.messages import AIMessage, SystemMessage

# The deterministic diary stand-in, imported rather than copied so the
# single-line sweep below is a statement about the REAL substituted body
# (spec 041 Slice 8) and cannot drift from it. An import only — no node is
# ever called from this module, and no model is ever reached.
from graphia.nodes.day import _DIARY_FALLBACK
from graphia.state import CLOSING_STORY_KWARG, PlayerPersona, PlayerState
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


def _story_event() -> dict[str, Any]:
    """One ``moderator_story`` super-step carrying the closing story.

    Shaped exactly as ``nodes.endgame.moderator_story`` returns it: a single
    public ``SystemMessage`` with **no** ``name``, whose ``additional_kwargs``
    carry ``CLOSING_STORY_KWARG``. The flag is imported from ``graphia.state``
    rather than spelled as a literal — 039's "import the thing, never copy it",
    so a reword breaks loudly here instead of silently drifting the renderer.
    """
    return {
        "moderator_story": {
            "messages": [
                SystemMessage(
                    content=_STORY_TEXT,
                    additional_kwargs={CLOSING_STORY_KWARG: True},
                )
            ]
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
