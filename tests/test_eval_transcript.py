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

from graphia.state import PlayerPersona, PlayerState
from graphia.tools.eval_transcript import render_transcript

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
# matching our own tokens — never the renderer's prose).
_NIGHT1_VICTIM = "Cara"  # killed Night 1; pointed at by both Mafiosos round 1
_NIGHT2_ONLY_TARGET = "Bo"  # pointed at on Night 2 only — the no-Night-lost token
_VOTE_INITIATE_LINE = "Mira has called for a vote to execute Dario."
_VOTE_EXECUTED_LINE = "Dario has been executed. Dario was a Mafia."


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
    the deciding round. ``resolve_night_kill`` announces the victim.
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
                        content=f"{victim_name} was killed in the night."
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
        events += [
            {
                "vote_prompt": {
                    "messages": [SystemMessage(content=_VOTE_INITIATE_LINE)],
                }
            },
            {
                "collect_votes": {
                    "messages": [
                        SystemMessage(content="Mira: Yes"),
                        SystemMessage(content="Bo: Yes"),
                    ]
                }
            },
            {
                "resolve_vote": {
                    "messages": [SystemMessage(content=_VOTE_EXECUTED_LINE)],
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

    Night 1's victim (Cara) precedes Day 1's vote initiation, which precedes
    Night 2's only target (Bo), which precedes Day 2's "Day 2 breaks." — so the
    Night/Day alternation is in strict chronological order, never reordered.
    """
    doc = _render()

    night1_i = doc.index("Night 1 begins.")
    day1_i = doc.index(_VOTE_INITIATE_LINE)
    night2_i = doc.index("Night 2 begins.")
    day2_i = doc.index("Day 2 breaks.")

    assert night1_i < day1_i < night2_i < day2_i


def test_render_first_night_precedes_first_day() -> None:
    """The Night-1 kill announcement precedes any Day-1 utterance (ordering spine)."""
    doc = _render()

    # Cara's Night-1 death is announced before Mira speaks on Day 1.
    assert doc.index(f"{_NIGHT1_VICTIM} was killed") < doc.index(
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

    assert "Dario — Mafia" in setup
    for citizen in ("Mira", "Bo", "Cara"):
        assert f"{citizen} — Law-abiding Citizen" in setup


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
    """The full Day-1 vote: who initiated it, each ballot, and the execution outcome."""
    doc = _render()

    # Initiation (who called the vote on whom).
    assert _VOTE_INITIATE_LINE in doc
    # Each ballot, by voter — both Yes ballots we scripted.
    assert "Mira: Yes" in doc
    assert "Bo: Yes" in doc
    # The outcome — Dario executed, role revealed.
    assert _VOTE_EXECUTED_LINE in doc


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
    assert "Dario — Mafia" in doc


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
    """
    players = {
        "p-1": _player("p-1", "Nil", "law_abiding", persona=None),
        "p-2": _player("p-2", "Don", "mafia", persona=None),
    }

    doc = render_transcript([], players, game_index=1, run_meta=None)

    assert "Nil — Law-abiding Citizen" in doc
    assert "Don — Mafia" in doc
    assert "(no persona recorded)" in doc


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
