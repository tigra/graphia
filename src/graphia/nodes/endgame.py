"""Endgame nodes: win-condition detection and final recap screen.

Topology contract (Slice 8):

- ``check_win_condition`` is a pure read: inspects alive counts and returns a
  winner key ("law_abiding" | "mafia") or an empty update when neither side
  has yet prevailed. No messages are emitted here; the Moderator speaks in
  ``end_screen``. The same function is registered in the graph under two
  node names (``check_win_night`` / ``check_win_day``) so the conditional
  fan-out can differ per site.
- ``end_screen`` composes a single Moderator SystemMessage containing the
  winner announcement, the chronological kill log, and a full roster reveal.
  It also sets ``phase="end"``.
- ``moderator_story`` (spec 040) then tells one short closing story from the
  model, as its OWN super-step, and after it the graph reaches END. It is
  TOTAL — it returns a story message or nothing at all, and never raises — and
  it does not touch ``phase``, which ``end_screen`` has already set.

Note the ORDER in which spec 040 landed that second bullet: this module gained
the node in Task 1.4 and ``graph.py`` gained the ``end_screen`` ->
``moderator_story`` -> END edge in Task 1.6. **Both have landed — the node is
reachable from every ending now**, and the tense here is corrected rather than
deleted because the ordering explains a shape in the tests: for one task the
node existed and was importable but was UNREACHABLE from any graph, so the
tests written for it drive it directly, and a direct-call test is still the
right instrument for its failure path. The three ARRIVAL paths — the two win
checks and the spec-023 runaway short-circuit — are pinned through real games
in ``tests/test_spec040_story_topology.py``.
"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from graphia.career_events import (
    KIND_GAME_ENDED,
    CareerEvent,
    CareerEventEmitter,
)
# The story's two size governors (spec 040 §2.2, Task 3.5). Imported as the
# keyword DEFAULTS for :func:`moderator_story` / :func:`_ai_story` so the
# numbers live in exactly one place — ``graphia.config`` — and a direct call
# from a test or a probe renders the same prompt the graph does. The LIVE values
# come from ``GraphiaConfig`` through both graph builders' ``partial`` binds;
# these defaults exist only so a direct call is well-defined, which is why they
# are the config defaults rather than some pre-spec baseline (unlike
# ``nodes.day:_render_context``'s ``window=_CONTEXT_WINDOW``, whose 30 is a
# deliberate pre-025 ablation baseline — the story has no pre-spec behaviour to
# reproduce). ``graphia.config`` imports nothing from ``graphia``, so this is
# not a cycle; ``nodes/setup.py`` already imports from it.
from graphia.config import (
    _DEFAULT_STORY_CONTEXT_WINDOW,
    _DEFAULT_STORY_TOKEN_BUDGET,
)
# Spec 040 Task 1.2 — THE PATCH TARGET, landed one task ahead of its consumer.
#
# **The consumer has since landed (Task 1.4): ``_ai_story`` below is what calls
# ``get_large``.** This note is kept, and its tense corrected rather than
# deleted, because the ORDERING is the part worth remembering. The import stood
# here for one task with nothing calling it, ON PURPOSE. ``monkeypatch.setattr``
# raises on a missing attribute, so ``tests/conftest.py``'s autouse ``safe_llm``
# net cannot cover ``graphia.nodes.endgame.get_large`` until the name exists
# here — and the net had to exist BEFORE any model reached this module, per
# CLAUDE.md's standing convention ("if you add a new module that calls an LLM,
# extend ``safe_llm`` to patch it too, otherwise a forgotten stub falls through
# to real boto3 and hangs pytest teardown"). This import is the whole of what
# makes that possible; the corresponding patch, and one correction to spec 040
# §3's account of what an unnetted call would actually do, are at that fixture.
#
# The alternative was ``monkeypatch.setattr(..., raising=False)`` in the
# fixture, needing no production change — rejected because it nets nothing
# until this import lands anyway, so the net could not be shown to catch a call
# today, which is exactly the failure mode this ordering exists to prevent.
from graphia.llm import Story, get_large

# THE WALK'S VOCABULARY IS IMPORTED, NEVER RETYPED (spec 040 — The Moderator's
# Closing Story, Task 3.2; the rule spec 039 — Per-AI Private Diaries paid for).
# ``_story_private_block`` below is the SECOND consumer of the shared cursor
# walk, and the whole reason the two kind constants exist as constants: a copied
# ``"diary"`` in this module that drifted from ``nodes/day.py``'s would attribute
# a whole-Day diary as a Day-round reflection, in well-formed output, with
# NOTHING RAISING. Two names on the forbidden list are deliberately absent from
# this import and must stay absent (§2.3): ``DIARY_WINDOW``, because the
# Moderator is the one reader for whom the FULL diary is the point, and
# ``_private_record_block``, because it windows and renders under a
# second-person label addressed to the writer.
#
# Import direction: ``nodes/day.py`` imports nothing from this module, so this
# is not a cycle — ``nodes/night.py`` already reaches into ``nodes/day.py`` the
# same way for ``_private_record_block``.
# ``_estimate_tokens`` and ``_render_context`` are Task 3.3's additions to this
# import, and they are the SAME two functions the Day and diary prompts use —
# imported rather than re-derived, so the story's public-history rendering
# (labels, the whisper filter, the oldest-first trim) cannot drift from what
# every other prompt in the game shows. §2.2 names both by their ``nodes.day``
# path for exactly that reason. What the story does NOT inherit is the Day's
# ``context_window`` / ``context_token_budget`` VALUES; the story's own pair is
# imported from ``graphia.config`` below and bound per-node by both graph
# builders.
from graphia.nodes.day import (
    DIARY_DAY_UNKNOWN,
    PRIVATE_RECORD_KIND_DIARY,
    PRIVATE_RECORD_KIND_THOUGHT,
    PrivateRecordEntry,
    _estimate_tokens,
    _render_context,
    _walk_private_record,
)
from graphia.prompts import (
    ENDGAME_HEADER_KILLS,
    ENDGAME_HEADER_ROSTER,
    ENDGAME_PERSONA_CITIZEN_TEMPLATE,
    ENDGAME_PERSONA_HEADER,
    ENDGAME_PERSONA_MAFIA_TEMPLATE,
    ENDGAME_WINNER_DRAW,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
    ENDGAME_WINNER_RUNAWAY,
    # The paragraph bound the prompt STATES. Imported so
    # ``STORY_MAX_PARAGRAPHS`` — the number this module ENFORCES — is derived
    # from it rather than restated; see that constant for the argument.
    STORY_PARAGRAPH_BOUND,
    STORY_SYSTEM,
    STORY_USER_TEMPLATE,
)
from graphia.state import (
    CLOSING_STORY_KWARG,
    CLOSING_STORY_TRUNCATED_KWARG,
    DiaryRecord,
    ENDGAME_FACTS_KWARG,
    GameState,
    KillRecord,
    PlayerState,
    ThoughtEntry,
)

# Mirrors graphia.ui.app.GraphiaApp._OUTCOME_BY_WINNER. The end-of-game event
# carries the same outcome string the local-mode summary uses, so consumer
# folds are byte-identical to ``stats_store.summarize`` (spec 006 §2.x).
# ``"runaway"`` (spec 023) is deliberately absent: a Day-cap runaway is not a
# legitimate result, so it folds no career outcome (an unmapped winner yields
# ``outcome=None`` and is skipped), exactly as in the UI's mirror.
_OUTCOME_BY_WINNER: dict[str, str] = {
    "law_abiding": "law_abiding_win",
    "mafia": "mafia_win",
    "draw": "draw",
}


def _role_label(role: str) -> str:
    return "Mafia" if role == "mafia" else "Law-abiding Citizen"


def check_win_condition(state: GameState) -> dict:
    """Pure read: evaluate the win condition from alive counts.

    Rules (Functional §2.7):
      - Law-abiding wins when no Mafia remain.
      - Mafia wins when the Mafia count is greater than or equal to the
        Law-abiding count.
      - Otherwise, the game continues — return an empty update so the graph
        routes to its normal fallthrough (night_close / day_turn / day_close).
    """
    players = state.get("players", {})
    alive_mafia = sum(
        1 for p in players.values() if p.is_alive and p.role == "mafia"
    )
    alive_law = sum(
        1 for p in players.values() if p.is_alive and p.role == "law_abiding"
    )

    if alive_mafia == 0:
        return {"winner": "law_abiding"}
    if alive_mafia >= alive_law:
        return {"winner": "mafia"}
    return {}


def _lookup_role_by_name(
    players: dict[str, PlayerState], name: str
) -> str | None:
    """Resolve a player's role by display name; None if not found."""
    for p in players.values():
        if p.name == name:
            return p.role
    return None


def _format_kill_line(
    record: KillRecord, players: dict[str, PlayerState]
) -> str:
    cycle = record.get("cycle", 0)
    name = record.get("name", "(unknown)")
    cause = record.get("cause")
    role = record.get("role")
    if role is None:
        # Night kills are recorded before the Day reveal and so may carry
        # role=None. Look the current role up from the roster.
        role = _lookup_role_by_name(players, name)
    role_label = _role_label(role) if role else "unknown role"
    if cause == "night":
        return f"• Night {cycle}: {name} ({role_label})"
    if cause == "execution":
        return f"• Day {cycle}: {name} ({role_label}) executed"
    # Defensive: unknown cause still gets a line.
    return f"• Cycle {cycle}: {name} ({role_label})"


def _winner_line(winner: str | None) -> str:
    if winner == "law_abiding":
        return ENDGAME_WINNER_LAW
    if winner == "mafia":
        return ENDGAME_WINNER_MAFIA
    # Runaway / unresolved Day-cap hit (spec 023) — distinct from a real win
    # and visibly not a normal draw.
    if winner == "runaway":
        return ENDGAME_WINNER_RUNAWAY
    if winner == "draw":
        return ENDGAME_WINNER_DRAW
    # Defensive: end_screen invoked without a winner set. Produce a generic
    # line rather than crashing.
    return "The game has ended."


def _persona_field(persona: object, name: str) -> str:
    """Read one persona attribute, tolerating either shape.

    A persona arrives as a :class:`~graphia.state.PlayerPersona` in-process, but
    after a checkpoint round-trip the LangGraph serde returns it as a plain
    ``dict`` (``PlayerPersona`` is not on the serde allow-list). ``end_screen``
    is the first reader of persona fields out of checkpointed state, so it must
    accept both. Missing/blank fields degrade to an empty string rather than
    crashing.
    """
    if isinstance(persona, dict):
        value = persona.get(name, "")
    else:
        value = getattr(persona, name, "")
    return value if isinstance(value, str) else ""


def _persona_reveal_line(player: PlayerState) -> str | None:
    """Format one AI player's end-of-game persona reveal line.

    Returns None when there is nothing to reveal — the human (no persona) or a
    fallback-path player whose ``persona`` is None — so the caller skips it
    rather than emitting an empty bullet. For a Mafioso the cover legend it
    performed is contrasted against its true self; a Law-abiding player shows
    its single honest persona.
    """
    persona = player.persona
    if persona is None:
        return None
    role_label = _role_label(player.role)
    personality = _persona_field(persona, "personality")
    manner = _persona_field(persona, "manner")
    public_persona = _persona_field(persona, "public_persona")
    if player.role == "mafia":
        return ENDGAME_PERSONA_MAFIA_TEMPLATE.format(
            name=player.name,
            role_label=role_label,
            personality=personality,
            manner=manner,
            public_persona=public_persona,
            true_self=_persona_field(persona, "true_self"),
        )
    return ENDGAME_PERSONA_CITIZEN_TEMPLATE.format(
        name=player.name,
        role_label=role_label,
        personality=personality,
        manner=manner,
        public_persona=public_persona,
    )


def end_screen(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
    """Emit the final Moderator message and flip phase to "end"."""
    players = state.get("players", {})
    kill_log = list(state.get("kill_log", []))
    winner = state.get("winner")

    lines: list[str] = [_winner_line(winner), "", ENDGAME_HEADER_KILLS]
    if kill_log:
        for record in kill_log:
            lines.append(_format_kill_line(record, players))
    else:
        lines.append("• (no kills this game)")

    lines.append("")
    roster_entries = [
        f"{p.name} ({_role_label(p.role)})" for p in players.values()
    ]
    lines.append(f"{ENDGAME_HEADER_ROSTER} {', '.join(roster_entries)}")

    # Persona reveal (Spec 016 §2.4): a public section, after the role reveal,
    # showing who each AI player really was — survivors and eliminated alike.
    # The human (no persona) and any fallback-path player without one are
    # skipped. For a Mafioso, the cover legend is contrasted with its true self.
    persona_lines: list[str] = []
    for p in players.values():
        if p.is_human:
            continue
        line = _persona_reveal_line(p)
        if line is not None:
            persona_lines.append(line)
    if persona_lines:
        lines.append("")
        lines.append(ENDGAME_PERSONA_HEADER)
        lines.extend(persona_lines)

    # MARKED AS THE FACTS (spec 040 §2.11, Slice 7). ``ENDGAME_FACTS_KWARG``
    # exists so a reader can recognise this message without knowing what it
    # SAYS: ``GraphiaApp._track_story_wait`` arms the "the Moderator is writing"
    # beat the instant it lands, because a chunk under ``stream_mode="updates"``
    # arrives only after a node returns and ``moderator_story`` is its own
    # super-step (§2.1) — so this message is on screen for the whole of the
    # story call. See ``graphia.state`` for why the three rejected alternatives
    # (any public ``SystemMessage``, a content sniff for
    # ``ENDGAME_HEADER_ROSTER``, arming in the driver) are each worse.
    #
    # The flag is machine-only and changes nothing a person sees: every reader
    # in the codebase selects on a NAMED key (``private_to``, ``speaker``,
    # ``substituted``, ``closing_story``), never on "has any kwargs", so
    # ``_render_context`` still labels this ``Moderator`` and the transcript
    # renderer still renders it as ``<endgame>``.
    final_msg = SystemMessage(
        content="\n".join(lines),
        additional_kwargs={ENDGAME_FACTS_KWARG: True},
    )

    if career_emitter is not None and game_id is not None and isinstance(winner, str):
        outcome = _OUTCOME_BY_WINNER.get(winner)
        if outcome is not None:
            career_emitter.emit(
                game_id,
                CareerEvent(
                    kind=KIND_GAME_ENDED,
                    session_id=game_id,
                    outcome=outcome,
                    human_role=state.get("human_role"),
                    rounds=state.get("cycle", 0),
                ),
            )

    return {"messages": [final_msg], "phase": "end"}


# ---------------------------------------------------------------------------
# The Moderator's closing story (spec 040)
#
# One extra super-step after ``end_screen``, and NOT a fold into it (§2.1):
# ``end_screen`` stays a pure deterministic string builder that has already
# committed and checkpointed the end-of-game career event, so the 5-15s
# non-deterministic call below cannot stand between "the winner is known" and
# "the career event is emitted"; the transcript can label the story separately
# from the facts; and with a second super-step the facts are already on screen
# while the call is in flight.
#
# SLICE 3 HAS NOW LANDED, so the material below is the real material: the
# shared cursor walk (Task 3.1), the third-party private renderer (Task 3.2),
# and the five-slot assembly with its budget ordering and its whisper exclusion
# (Task 3.3). SLICE 4 TASK 4.1 HAS ALSO LANDED: ``STORY_SYSTEM``'s seven moves
# are now present — this comment used to read "none of them is present yet" and
# was corrected when they arrived rather than left contradicting the prompt it
# describes. Read them at ``prompts.py``, above ``STORY_PARAGRAPH_BOUND``, which
# is where the STATED paragraph bound now lives (the 039 split: the bound the
# prompt states sits with the prompt). TASK 4.2 HAS ALSO LANDED, and it was
# never outstanding "HERE" — this comment used to list its runaway wording as
# work pending in this module, but it is PROMPT TEXT and shipped as move 5's
# third sentence in ``prompts.py``; the five-reference outcome audit sits above
# ``STORY_SYSTEM`` there. What that audit turned up in THIS module is recorded
# at the ``_render_context`` call in ``_ai_story`` below. STILL OUTSTANDING
# HERE: Slice 5's paragraph-preserving clamp with its truncation flag — whose
# ``STORY_MAX_PARAGRAPHS`` / ``STORY_MAX_CHARS`` are the ENFORCEMENT half and
# belong in this module, beside that clamp.
# ---------------------------------------------------------------------------

# The human seat's marker, and it is the deliverable of Task 3.3 rather than a
# flourish: Slice 1's roster emitted name / role / alive-or-dead only, and a
# live run (Task 2.3) had the story narrate the reader in the third person as
# "Alexey, another innocent citizen". Slice 4's human clause ("never claims to
# know what the person was thinking") can be asserted as PROMPT TEXT without
# this, but cannot be shown to DO anything until the seat is marked.
#
# A constant rather than an inline literal because two later tasks assert on the
# exact string (Slice 4's pairing of move 6 with the marker, and Slice 3's own
# material tests), and a retyped literal is how those drift apart.
STORY_ROSTER_HUMAN_MARKER = "(the person playing)"


def _story_persona_tail(player: PlayerState) -> str:
    """The persona half of one roster line; ``""`` when there is none to show.

    **:func:`_persona_field` IS MANDATORY HERE, NOT STYLISTIC** (spec 040 §2.2).
    Its docstring records why: after a checkpoint round-trip the LangGraph serde
    hands a :class:`~graphia.state.PlayerPersona` back as a plain ``dict``,
    ``end_screen`` is the FIRST reader of persona fields out of checkpointed
    state, and ``moderator_story`` is the SECOND — so a bare ``persona.manner``
    here would ``AttributeError`` on a resumed game and take the whole story
    with it (silently, into ``_ai_story``'s ``except``, as a missing ending).
    Every read below goes through it.

    WHY :func:`_persona_reveal_line` IS NOT REUSED, though it renders the same
    four fields. It exists for the FACTS message and its two templates
    (``ENDGAME_PERSONA_CITIZEN_TEMPLATE`` / ``ENDGAME_PERSONA_MAFIA_TEMPLATE``)
    open with ``"• {name} ({role_label}) — "`` — the name and the side, which
    the roster line already carries and would then carry twice, in two
    different spellings, in one prompt. Reuse of the *field accessor* is the
    requirement; reuse of the player-facing reveal prose would be a
    duplication. It is also player-facing text the facts message showed the
    reader a super-step ago, and §2.2 excludes the rendered facts prose from
    this prompt precisely because passing it invites paraphrase.

    A Mafioso's ``true_self`` is shown; a Citizen's is not, because §2.2 lists
    it as excluded "empty by construction" (:class:`PlayerPersona` documents
    the same). It degrades to nothing if a fallback-path Mafioso has none.
    """
    persona = player.persona
    if persona is None:
        # The human seat (personas are generated for AI players only), or a
        # fallback-path player whose persona never materialised.
        return ""
    personality = _persona_field(persona, "personality")
    manner = _persona_field(persona, "manner")
    public_persona = _persona_field(persona, "public_persona")
    # "; " rather than the facts templates' bare space. Both fields arrive as
    # unpunctuated phrases ("coldly calculating" / "speaks in short, flat
    # sentences"), so a space-join reads as one run-on phrase and ``manner``
    # frequently contains commas of its own. The facts message's space-join is
    # a player-visible byte and stays exactly as it is; this block is the
    # story's own presentation, and the separator is where the two differ.
    shown = "; ".join(part for part in (personality, manner) if part).strip()
    fragments: list[str] = []
    if public_persona:
        fragments.append(f"showed the table {public_persona}")
    if shown:
        fragments.append(f"({shown})" if public_persona else shown)
    if player.role == "mafia":
        true_self = _persona_field(persona, "true_self")
        if true_self:
            fragments.append(f"but was really {true_self}")
    return " ".join(fragments).strip()


def _story_roster_block(players: dict[str, PlayerState]) -> str:
    """One line per player: name, side, fate, the human's seat, the persona.

    THE FOUR FIELDS SPEC 040 §2.2 SPECIFIES, all of them present now (Task
    3.3): name, side, alive-or-dead, :data:`STORY_ROSTER_HUMAN_MARKER` on the
    human seat, and each AI's persona through :func:`_story_persona_tail` (which
    carries the mandatory :func:`_persona_field` reads and the argument for
    them).

    **THE HUMAN MARKER IS WHAT MAKES SLICE 4's HUMAN CLAUSE MEAN ANYTHING.**
    Slice 1's roster emitted the first three fields only, and a live run
    narrated the reader in the third person as one more citizen at the table.
    The marker is the *only* thing in the whole prompt that identifies which
    seat is the person being told the story — the private block cannot do it,
    because the human's absence from that block is structural (both writers
    skip ``is_human``) and an absence is not an identification.

    ``is_human`` IS TESTED HERE AND DELIBERATELY NOT IN
    :func:`_story_private_block` — the two are not inconsistent. There, a test
    would convert a structural guarantee into a filter a reader has to trust.
    Here there is no structural fact to lean on: the seat has to be named, so
    the flag is read directly, once, in the one place a reader will look for it.

    :func:`_role_label` is reused rather than re-spelled, so the side names the
    story reads are the ones the facts message just showed the player.

    Authors come out in ``players`` insertion order, the same order
    :func:`_story_private_block` groups by, so the two blocks read as one
    roster.
    """
    lines: list[str] = []
    for p in players.values():
        line = (
            f"• {p.name} — {_role_label(p.role)} — "
            f"{'alive at the end' if p.is_alive else 'dead'}"
        )
        if p.is_human:
            line = f"{line} — {STORY_ROSTER_HUMAN_MARKER}"
        tail = _story_persona_tail(p)
        if tail:
            line = f"{line} — {tail}"
        lines.append(line)
    return "\n".join(lines) if lines else "• (no players)"


def _story_events_block(
    kill_log: list[KillRecord], players: dict[str, PlayerState]
) -> str:
    """The game's deaths, rendered exactly as the facts message renders them.

    :func:`_format_kill_line` is REUSED per record (spec 040 §2.2) rather than
    re-implemented, so the story and the facts describe the same events in the
    same words and a later change to how a death reads lands in both at once.

    The empty case says so in words instead of rendering nothing: a blank
    section invites a model to read an omission it should fill, which is the
    one fabrication route this block can open on its own.
    """
    if not kill_log:
        return "• (no one died this game)"
    return "\n".join(_format_kill_line(record, players) for record in kill_log)


# ---------------------------------------------------------------------------
# The third-party private record — the ``{private_record}`` slot (Task 3.2)
#
# WHY THE RENDERING VOCABULARY LIVES HERE rather than in ``prompts.py``: the
# closest precedent is ``nodes/day.py``'s ``PRIVATE_THOUGHTS_LABEL`` and
# ``DIARY_LINE_MARKER``, which are prompt-visible and still sit with the
# renderer that owns them, while ``prompts.py`` holds the whole-prompt
# templates. ``STORY_USER_TEMPLATE``'s slot LABEL is Task 3.3's and
# ``STORY_SYSTEM``'s prose is Slice 4 Task 4.1's; what is below is this block's
# own presentation.
#
# ``_ai_story`` NOW CALLS THIS (Task 3.3), and the assembly ORDER there is
# load-bearing: this block is rendered FIRST and measured, and the public
# history gets what is left of ``story_token_budget`` (Task 3.5 turned that
# provisional module constant into a config-threaded argument, so the name is
# now a parameter of ``_ai_story`` rather than a module global). The tense is
# corrected rather than deleted because the boundary explains the shape: for
# one task this renderer existed with nothing calling it, on purpose, so it
# could be proven as a pure function before any prompt depended on it.
# ---------------------------------------------------------------------------

# The block's own legend, and it is a FABRICATION GUARD rather than decoration —
# it states the two facts the Day-attribution decision below rests on, so do not
# delete it as duplication of Task 3.3's slot label without moving those two
# facts somewhere the model still reads them:
#
# 1. Grouping is PER AUTHOR and carries no cross-player order. The cursor walk
#    orders ONE player's two channels against each other and gives NO ordering
#    across players — there is no cross-player cursor, and thought *indices*
#    cannot substitute because ``day_round_reflect`` reflects only SURVIVING
#    players, so index *i* in a dead player's list is a different round from
#    index *i* in a survivor's. The public history carries the game-wide
#    chronology; this block must not read as one game-wide timeline.
# 2. A reflection carries NO DAY AT ALL, stated in words, because the model is
#    about to read dated diary entries beside undated reflections and would
#    otherwise be free to date the reflections itself.
STORY_PRIVATE_LEGEND = (
    "What each player wrote privately, grouped by author, in the order that "
    "player wrote it; entries by different players are not ordered against "
    "each other. A diary entry sums up a whole Day. A reflection was written "
    "during a Day, at the close of one round — the game does not record which "
    "Day, so none is given."
)

# One author's heading. Third-person and past-tense: this block is read by the
# Moderator ABOUT the players, which is the whole difference from
# ``_private_record_block``'s ``PRIVATE_THOUGHTS_LABEL`` ("Your private notes so
# far (yours alone):"), a second-person label addressed to the writer.
STORY_PRIVATE_AUTHOR_TEMPLATE = "{name} wrote:"

# The two attribution tags. Deliberately NOT ``nodes/day.py``'s
# ``DIARY_LINE_MARKER``, and the reason is the same one that keeps
# ``_private_record_block`` out of this module: that marker is the PLAYER's, its
# docstring ties its exact prefix shape to spec 039's flag-off assertions, and
# sharing it would make a change to what a player sees silently rewrite the
# story's prompt. Two readers, two vocabularies, deliberately.
#
# BOTH ARE INVARIANT PREFIXES, NOT ``"…{day}]"`` TEMPLATES — the lesson recorded
# at ``DIARY_LINE_MARKER``. A template constant would make the negative
# assertion ``MARKER not in prompt`` VACUOUSLY true, because a literal ``{day}``
# never appears in a rendered prompt. As prefixes they are present in every line
# of their kind and absent whenever no line of that kind is shown. They are also
# textually distinct from the player marker at a glance (``[diary of Day`` vs
# ``[Diary — end of Day``), so an ``in`` assertion cannot confuse the two.
STORY_PRIVATE_DIARY_MARKER = "[diary of Day"
STORY_PRIVATE_THOUGHT_MARKER = "[reflection]"

# Said in words rather than rendered blank, on ``_story_events_block``'s
# reasoning: a blank section invites a model to read an omission it should fill.
# NOT a hypothetical case — a game won on the first Night ends before any
# reflection or diary is written, so this is the real ending of a short game.
#
# MEASURED CONSEQUENCE, AND THE MITIGATION IS NOT SUFFICIENT ON ITS OWN
# (Task 4.4, 2026-09-08 — the answer to the question Task 4.1 (v) routed to it).
# The reasoning above is right about the mechanism and incomplete about the
# remedy. Saying the emptiness IN WORDS does not close the void-fill, because
# ``STORY_SYSTEM``'s move 2 has already told the model that the private writing
# "is the substance of this story" — so on an empty record the INSTRUCTION names
# an absent substance while the MATERIAL says it is absent, and the instruction
# is the half a model weighs. Live evidence: a three-seat Ollama game that ended
# at ``check_win_night`` with no Day played, zero AI spoken lines and this exact
# constant in ``{private_record}`` produced a story narrating a whole speech
# phase — "Imane sat across from Avery at the table. She spoke with her usual
# earnest conviction…" — in a game that never opened one. That is Task 4.4's
# fourth fabrication category (a phase or process the game never ran).
#
# DO NOT "FIX" THIS BY BLANKING THE SECTION — that is strictly worse and is what
# the paragraph above already rules out. The fix is prompt-side: qualify move 2
# for the empty case, so the instruction never names a substance the material
# has just said does not exist. Recorded as named follow-up (ii) in tech spec
# §2.7 for Slice 10 to log; it is prompt text, not this constant's business.
STORY_PRIVATE_EMPTY = "• (nothing was written privately this game)"


def _story_private_day(day: object) -> str:
    """Render one diary entry's Day for a third-party reader.

    ONLY an ``int`` is a Day. Anything else degrades to
    :data:`~graphia.nodes.day.DIARY_DAY_UNKNOWN`, and that includes the values
    the walk hands over already interpolated: a ``DiaryRecord`` is
    ``total=False``, so a MISSING ``day`` arrives as the placeholder itself and a
    present-but-unusable one (``None``, a ``bool``, a container) arrives as its
    ``f"{…}"`` rendering.

    THIS IS A DELIBERATE, NARROW DIVERGENCE FROM WHAT A PLAYER SEES, not a
    tidy-up that leaked (Task 3.2). ``_walk_private_record`` documents the
    inherited oddity it preserved: because the ``"?"`` placeholder fires only on
    a MISSING key, a record carrying an explicit ``"day": None`` renders
    ``[Diary — end of Day None]`` in the player's block. Task 3.1 pinned that
    as-is rather than move a player-visible byte, and it stays pinned — this
    function changes only the STORY's rendering, where ``Day None`` would be a
    meaningless date handed to a model that will try to make sense of it,
    against a ``?`` that reads as "unknown" on sight.

    The type test cannot be recovered after the walk (a stringified ``None`` is
    indistinguishable from a genuine ``"None"``), so "not an ``int``" is the only
    honest reading available here — and it costs nothing real, because the
    channel's only writer, ``nodes.day.day_diary``, writes ``state["cycle"]``,
    an ``int``. A digit-bearing ``str`` day is not producible by any live writer.
    ``bool`` is excluded explicitly because ``True`` is an ``int`` in Python and
    ``Day True`` would be nonsense — the same guard
    ``eval_transcript._diary_day_attr`` carries, for the same reason it gives:
    "inventing one would mislead a reviewer about which Day an entry sums up".
    """
    if isinstance(day, bool) or not isinstance(day, int):
        return DIARY_DAY_UNKNOWN
    return f"{day}"


def _story_private_line(entry: PrivateRecordEntry) -> str:
    """Render ONE walk entry as one attributed line; ``""`` to drop it.

    THE DAY-ATTRIBUTION DECISION LIVES HERE, and it was a choice between two
    named options rather than an improvisation (Task 3.2). Task 3.2's brief asks
    for "per-author attribution carrying kind and Day", and Task 3.1 measured
    that HALF OF THAT IS UNACHIEVABLE: a :class:`graphia.state.DiaryRecord`
    carries ``day``, but a :class:`graphia.state.ThoughtRecord`'s keys are
    exactly ``text`` and ``substituted``, because a thought is written per Day
    *round* and the eval transcript renders it positionally inside its
    ``<round>``. So the walk returns ``None`` for a thought's Day honestly,
    not as a lost value. The two options were **attribute by kind alone** or
    **infer** the Day from the flanking diary Days (before the first diary →
    Day 1; between ``day=d`` and ``day=d+1`` → Day ``d+1``; after the last →
    ``d+1``).

    **CHOSEN: KIND ALONE.** A reflection is tagged
    :data:`STORY_PRIVATE_THOUGHT_MARKER` and carries no Day, and
    :data:`STORY_PRIVATE_LEGEND` says in words that the game does not record
    one. Three reasons, the first decisive on its own:

    1. **FABRICATION IS THIS SPEC'S OVERRIDING RISK, and an inferred Day is
       indistinguishable from a recorded one inside a prompt.** The model reads
       both as ground truth, so a wrong inferred Day comes back as a confidently
       misdated event in the story — a small fabrication, and a strictly worse
       failure than an honest absence, which costs only precision. The project
       already settled this exact trade-off once, in
       ``eval_transcript._diary_day_attr``: it omits the ``day`` attribute
       rather than guess, because "inventing one would mislead a reviewer about
       which Day an entry sums up".
    2. **THE INFERENCE HAS NO FLANK EXACTLY WHERE THIS SLICE SAYS THE MATERIAL
       MATTERS MOST.** Slice 3's preamble: the whole-Day diaries are absent for
       the Day the game is WON, because a winning move ends the game before that
       day's diaries are written, and that is the day a story most wants to land
       its reveal on — the reflections are the only private material there. A
       player who died during Day 1 likewise has reflections and NO DIARY AT
       ALL, so there is nothing to interpolate between.
    3. **AND WHERE IT HAS NO FLANK IT DOES NOT DEGRADE GRACEFULLY, IT MISDATES
       WHOLESALE.** With ``GRAPHIA_PRIVATE_DIARIES=0`` (ADR 011) the diary
       channel is empty by construction — ``day_diary`` returns ``{}`` before
       writing anything, so nothing reaches ``private_diaries`` — while
       ``private_thoughts`` fills up as usual. The "before the first diary →
       Day 1" limb would then date a whole five-Day record to Day 1, in
       well-formed output, produced by a config flag, with nothing raising.
       A ``DIARY_DAY_UNKNOWN`` day defeats the arithmetic outright as well:
       ``d + 1`` has no meaning over ``"?"``.

    THE THREE-WAY BRANCH IS THE MISATTRIBUTION GUARD. An
    ``if diary … else thought`` would be exactly how a third kind added to the
    walk one day becomes silently attributed as a reflection; an unrecognised
    kind instead renders under its own name, so it arrives in the prompt
    visibly unfinished rather than wrong.

    A BLANK ENTRY IS DROPPED, and this is a considered divergence from the
    player's block, which renders a malformed element as a bare ``"- "`` bullet
    (whose trailing space spec 039's suite pins). A tag with nothing after it is
    an omission a model can read as something to fill in — the same reasoning
    :func:`_story_events_block` gives for naming its empty case in words. The
    entry is dropped, never replaced by a placeholder.

    Both kinds are ``rstrip``ed uniformly. The player block's asymmetry (only
    the diary line is stripped) is a byte-identity obligation to spec 028 that
    this reader does not carry.
    """
    kind, day, text = entry
    body = (text if isinstance(text, str) else f"{text}").strip()
    if not body:
        return ""
    if kind == PRIVATE_RECORD_KIND_DIARY:
        tag = f"{STORY_PRIVATE_DIARY_MARKER} {_story_private_day(day)}]"
    elif kind == PRIVATE_RECORD_KIND_THOUGHT:
        tag = STORY_PRIVATE_THOUGHT_MARKER
    else:
        # Neither of the two known kinds. Rendered under its own name rather
        # than folded into one of them, so a future third channel shows up as
        # unhandled instead of misattributed.
        tag = f"[{kind}]"
    return f"  • {tag} {body}".rstrip()


def _story_private_block(
    players: dict[str, PlayerState],
    private_thoughts: dict[str, list[ThoughtEntry]] | None,
    private_diaries: dict[str, list[DiaryRecord]] | None,
) -> str:
    """Render every player's private writing for the ``{private_record}`` slot.

    THE THIRD-PARTY COUNTERPART OF ``nodes.day:_private_record_block``, and the
    two must not be confused (spec 040 §2.3). That one renders what ONE player
    reasons from: their own record, windowed to :data:`DIARY_WINDOW`, under a
    second-person label addressed to them. This one renders what the MODERATOR
    knows about EVERYONE: every player's full record, grouped and attributed by
    author for a reader who wrote none of it. Neither is a template for the
    other, and this function must never call that one.

    **THE FULL DIARY, NEVER WINDOWED (§2.3).** ``DIARY_WINDOW`` is deliberately
    not imported into this module: it exists so a *player* reasons from three
    entries, and the Moderator is the one reader for whom the WHOLE diary is the
    point. That is exactly why ``_walk_private_record`` never windows — it walks
    whatever list it is handed — so passing the state list through is the whole
    of it.

    **THE ORDER IS THE SHARED WALK'S; ONLY THE PRESENTATION IS HERE.** The
    ``thoughts_before`` cursor walk is the load-bearing correctness rule of the
    two-channel design and its failure mode is silent — well-formed output in
    the wrong order — so it is CALLED, not repeated (its docstring carries the
    rule and the argument for sharing it).

    **GROUPED PER AUTHOR, NOT AS ONE GAME-WIDE TIMELINE**, and that is a limit
    on what this material can honestly claim rather than a layout preference —
    see :data:`STORY_PRIVATE_LEGEND` reason 1 for why no cross-player order
    exists to render. Authors come out in ``players`` insertion order, the same
    order :func:`_story_roster_block` uses, so the two blocks read as one roster.

    **A PLAYER WITH NOTHING GETS NO HEADING**, which is what leaves THE HUMAN
    out of this block entirely. That absence is structural rather than filtered:
    ``day_round_reflect`` and ``day_diary`` both skip ``is_human``, so neither
    channel ever holds a key for the human seat, and §2.2 calls that "the
    strongest guarantee in the spec" for the functional criterion that the story
    "never claims to know what the person was thinking". **No ``is_human`` test
    is made here, deliberately**: adding one would move that guarantee from a
    structural fact to a filter a reader has to trust, and would make a
    block-level test of it pass whether the writers still skip the human or not.
    A future spec that gives the human a private channel of their own must
    revisit this paragraph rather than assume the omission was an oversight.

    **NOTHING FILTERS BY SURVIVAL.** A dead player's writing is precisely what
    the Moderator holds and the living never saw. A player killed on the very
    first Night simply wrote nothing, so there is nothing to filter.

    The ``substituted`` marking on either channel is NOT surfaced, matching both
    the player's block and the walk: the eval transcript and the ledger viewer
    are where a substitution is marked.

    PURE, and it does not mutate its arguments: the walk copies the thoughts
    before slicing and only iterates the diaries, so a live state list may be
    handed straight over. No RNG, no LLM, no clock, and no ``set`` iteration, so
    ``tests/test_dual_mode_smoke.py``'s byte-equality is unaffected.
    """
    thoughts_by_player = private_thoughts or {}
    diaries_by_player = private_diaries or {}
    sections: list[str] = []
    for player in players.values():
        lines = [
            line
            for line in (
                _story_private_line(entry)
                for entry in _walk_private_record(
                    thoughts_by_player.get(player.id, []),
                    # The FULL list. No window, by design — see above.
                    diaries_by_player.get(player.id),
                )
            )
            if line
        ]
        if not lines:
            continue
        heading = STORY_PRIVATE_AUTHOR_TEMPLATE.format(
            # ``name`` is the key every other block identifies a player by, and
            # the roster block beside this one carries their side and fate, so
            # repeating those here would only give them a second place to drift.
            # The id is a defensive fallback for a nameless seat, never seen in
            # a real game.
            name=player.name or player.id
        )
        sections.append("\n".join([heading, *lines]))
    if not sections:
        return STORY_PRIVATE_EMPTY
    return "\n\n".join([STORY_PRIVATE_LEGEND, *sections])


# ==========================================================================
# THE LENGTH BOUND, ENFORCEMENT HALF (spec 040 §2.5, Task 5.2)
# ==========================================================================
#
# PLACEMENT IS A DECISION, AND THE 039 PRECEDENT IT RESTS ON IS SPLIT
# --------------------------------------------------------------------------
# Verified rather than assumed: ``DIARY_SENTENCE_BOUND = 6`` lives in
# ``prompts.py`` beside the prompt that interpolates it, while
# ``DIARY_MAX_CHARS = 900`` lives in ``nodes/day.py`` beside
# ``_clamp_diary_entry``, its only reader. That split is spec 039's discipline
# rather than an accident — the number a prompt STATES is part of the prompt,
# and the number a clamp ENFORCES is part of the clamp — so this pair follows
# it: both constants live here, next to :func:`_clamp_story`, and only
# ``STORY_PARAGRAPH_BOUND`` (the number the prompt states) stays in
# ``prompts.py``.
#
# Two things settle it beyond the precedent. ``prompts.py`` is a LEAF module
# whose whole job is prompt text, and a number that no prompt interpolates has
# no reader there; and these two have exactly ONE reader in the codebase,
# :func:`_clamp_story` twenty lines below, so co-location is what a maintainer
# changing the bound actually needs.
#
# MODULE CONSTANTS, NOT CONFIG. The functional spec fixes the length, and
# ADR 011 asks for a feature flag rather than a tunable — verbatim spec 039's
# argument for ``DIARY_WINDOW``. Deliberately NOT ``GraphiaConfig`` fields and
# deliberately NOT threaded through the graph builders' ``partial`` binds: that
# is what Task 3.5 did for ``story_context_window`` / ``story_token_budget``,
# which are PROVIDER-CAPABILITY knobs (they exist so a smaller local model can
# be fitted), and a length bound on the prose is not one.

# The enforced paragraph cap, DERIVED from the stated one rather than restated.
#
# WHY DERIVED, WHICH IS A DEPARTURE FROM 039 AND DELIBERATELY SO. Spec 039's
# two numbers are in different DIMENSIONS — six sentences stated, nine hundred
# characters enforced — so they cannot silently disagree and independence is
# correct there. This pair is the SAME number in the SAME dimension in two
# modules, which is a drift risk 039 does not carry (flagged for this task at
# ``prompts:STORY_PARAGRAPH_BOUND``). Equality is also the only correct
# relationship: enforcing fewer paragraphs than the prompt requests would clip
# a compliant story, and enforcing more would not enforce the stated bound at
# all. Making it structural beats pinning it with a test, which can only report
# a disagreement after someone has shipped one. No import cycle: ``prompts`` is
# a leaf (its only import is ``__future__.annotations``) and this module
# already imports from it.
#
# The separate NAME is still worth having: it is the enforcement role, it is
# what :func:`_clamp_story` reads, and it is the seam if the clamp should ever
# need slack over the request.
STORY_MAX_PARAGRAPHS = STORY_PARAGRAPH_BOUND

# The character backstop. INDEPENDENT of the paragraph cap on purpose — a
# second dimension, which is exactly the shape of 039's ``DIARY_MAX_CHARS``:
# four paragraphs of any length still bound nothing.
#
# WHY 1800, AND WHY THERE IS A CEILING ON IT. Two live stories measured 843 and
# 911 characters over three paragraphs each (spec 040 Task 2.3), so 1800 is
# roughly double the observed size — it is a runaway guard, not a target, and
# real stories are not expected to reach it (which is why Task 5.4's evidence
# is necessarily constructed input).
#
# The CEILING is the local path's own output cap: ``llm._OLLAMA_NUM_PREDICT``
# is 1024 tokens, sent as ``options.num_predict`` on every native
# ``/api/chat`` request — renamed from ``_OLLAMA_MAX_TOKENS`` by spec 041 /
# ADR 013 at an unchanged value, so cite the live name. At the project's own
# conservative ``day._CHARS_PER_TOKEN = 3`` proxy that is about 3000
# characters of response, and the response is JSON (``{"text": …}``) whose
# wrapper and escapes come out of the same allowance. So a cap set anywhere
# near that number would be reached by an Ollama TRUNCATION rather than by a
# long story: the provider would stop mid-sentence, this clamp would then cut
# at the last whole paragraph boundary, and the ending would silently lose its
# final sentence with nothing to distinguish that from a story the model chose
# to end there. §2.5's "keep under ~2500" is the margin under that ceiling;
# 1800 sits well inside it.
#
# NOT derived from ``_OLLAMA_NUM_PREDICT``. It is a different dimension
# (characters against tokens) bridged only by a heuristic ratio, so an
# expression would be fake precision — and the constant is private to
# ``llm.py``. The relationship is a citation, which is what 039 does too.
STORY_MAX_CHARS = 1800

# What the clamp rejoins with, and what ``STORY_SYSTEM``'s move 7 asks the
# model for in the same words: "separated by blank lines".
_STORY_PARAGRAPH_SEPARATOR = "\n\n"

# ONE BLANK LINE — a newline, then anything blank, then a newline. §2.5 words
# the split as "runs of two-or-more newlines" and this is deliberately WIDER,
# by exactly the whitespace a run can carry between its newlines: ``\s``
# includes ``\n``, so this collapses ``"\n\n\n\n"`` to a single break the way
# ``\n{2,}`` would, and ALSO breaks on ``"\n \n"`` and ``"\n\t\n"``, which
# ``\n{2,}`` reads as a paragraph's interior.
#
# WHY THE WIDENING RATHER THAN THE LITERAL WORDING. A trailing space before a
# newline is invisible in the model's own output and common in generated prose,
# and under ``\n{2,}`` one of them anywhere in the answer silently merges two
# paragraphs into one: the paragraph cap then measures too few paragraphs and
# the fold below runs the two together into a wall of text. So the narrow form
# fails in the direction of NOT enforcing the bound while destroying the
# structure this clamp exists to keep. The widening cannot over-split — it
# still requires two newlines — so it is a superset with no new failure mode.
_STORY_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _clamp_story(text: str) -> tuple[str, bool]:
    """Bound the story to whole paragraphs; return it and whether any were cut.

    Returns ``(story, clipped)``. ``clipped`` is ``True`` iff at least one
    whole paragraph was dropped — the fact Task 5.3 needs to mark the message,
    and one only this function can know, because it is the difference between
    the paragraphs the answer contained and the paragraphs kept.

    NOT 039's ``_clamp_diary_entry``, AND THE DIFFERENCE IS THE POINT (§2.5).
    That one is ``" ".join(text.split())[:DIARY_MAX_CHARS].rstrip()``: it folds
    ALL whitespace, ``\\n\\n`` included. Correct there, because a ``<diary>`` is
    an inline transcript element and its own docstring records the measurement
    behind that (zero multi-line inline elements across the committed corpus).
    Wrong here, because **paragraphs are the story's structure** — reusing it
    would deliver one wall of text and nothing would raise.
    ``test_spec040_story_node``'s ``"\\n\\n" in story.content`` is the standing
    witness against that substitution, and it stays green by design here.

    TRUNCATION IS AT A PARAGRAPH BOUNDARY, NEVER A CHARACTER CUT, and §2.5
    calls that a difference in kind rather than degree. A diary cut mid-word is
    read by a model and a maintainer; **the story is the last thing the player
    reads**, and a sentence that stops mid-clause reads as a bug in the game
    rather than as a long answer that was trimmed. That is also why the char
    bound accumulates WHOLE paragraphs instead of slicing: the only two honest
    answers for a paragraph that will not fit are "keep it" and "drop it", and
    this drops it.

    THE ORDER, and each step is load-bearing:

    1. **Normalise line endings** first, so a ``\\r\\n`` answer is not one
       paragraph whose interior happens to contain carriage returns.
    2. **Split on blank lines** — see ``_STORY_PARAGRAPH_BREAK`` for why the
       pattern is one step wider than §2.5's wording.
    3. **Fold each paragraph's own whitespace to single spaces and strip.**
       Within a paragraph a line wrap carries no meaning, and folding here is
       what lets step 5 measure the text as it will actually be rendered.
    4. **Drop empties**, so leading, trailing or doubled breaks cannot produce
       a paragraph that is nothing, or spend a slot in the cap on one.
    5. **Bound in two dimensions** — at most ``STORY_MAX_PARAGRAPHS``, and
       accumulate while the joined length stays within ``STORY_MAX_CHARS``.
       The separators are counted, because they are part of what is stored and
       rendered.

    THE FIRST PARAGRAPH IS EXEMPT FROM THE CHARACTER BOUND, and this asymmetry
    is deliberate — it is the one place a naive reading of §2.5 produces a real
    bug. A single paragraph longer than ``STORY_MAX_CHARS`` cannot be shortened
    at a paragraph boundary at all, so "accumulate while the length fits" would
    accumulate NOTHING and return ``""``. That would then travel as absence
    (:func:`_ai_story` returns ``None`` on an unusable answer), and absence is
    a signal this feature spends real care on: §2.10's ``story_missing_rate``
    counts endings with no flagged message and, with no ablation arm, reads
    absence as **failure**. A model that answered perfectly well in one long
    block would be recorded as a failed call. So the char bound is a runaway
    guard that yields to the paragraph contract, not the reverse — the
    functional criterion is stated in paragraphs, and one paragraph satisfies
    it. Nothing is dropped in that case, so ``clipped`` is correctly ``False``.

    There is no matching floor under the PARAGRAPH cap: it is derived from
    ``STORY_PARAGRAPH_BOUND`` and cannot be zero without someone deliberately
    setting the number the prompt states to zero, which already means "no
    story".

    PURE: no state, no RNG, no clock, so replay and the dual-mode byte-equal
    smoke are unaffected — the same property 039's clamp records.
    """
    paragraphs = [
        # Fold the paragraph's OWN whitespace. ``str.split()`` with no
        # argument splits on any run of whitespace and discards the empties,
        # so this both folds the interior and strips the ends.
        " ".join(paragraph.split())
        for paragraph in _STORY_PARAGRAPH_BREAK.split(
            text.replace("\r\n", "\n").replace("\r", "\n")
        )
    ]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    kept: list[str] = []
    length = 0
    for paragraph in paragraphs:
        if len(kept) >= STORY_MAX_PARAGRAPHS:
            break
        # The length the story WOULD have with this paragraph joined on,
        # separator included — measured on the rendered form, not the sum of
        # the parts.
        grown = length + len(paragraph)
        if kept:
            grown += len(_STORY_PARAGRAPH_SEPARATOR)
            if grown > STORY_MAX_CHARS:
                break
        kept.append(paragraph)
        length = grown
    # ``clipped`` compares COUNTS, so it is true for a drop by either bound and
    # false when the first-paragraph exemption above kept an over-long one.
    return _STORY_PARAGRAPH_SEPARATOR.join(kept), len(kept) < len(paragraphs)


def _ai_story(
    state: GameState,
    *,
    story_context_window: int = _DEFAULT_STORY_CONTEXT_WINDOW,
    story_token_budget: int = _DEFAULT_STORY_TOKEN_BUDGET,
) -> tuple[str, bool] | None:
    """Ask the large tier for the closing story; ``None`` when there is none.

    Returns ``(story, clipped)`` — the clamped story and whether the cap
    actually fired — or ``None`` when there is no usable story at all.

    THE RETURN WAS WIDENED FROM ``str | None`` BY TASK 5.3, and the alternative
    is worth knowing because the old contract was deliberate and directly
    tested. ``clipped`` is knowable in exactly one place, inside
    :func:`_clamp_story` between the split and the join (an exactly-four-
    paragraph story is indistinguishable from a five-paragraph one that lost
    its last), and it is needed in exactly one other, beside the
    ``SystemMessage`` construction in :func:`moderator_story`. The only two
    shapes are this one and out-of-band state that is neither returned nor a
    parameter — which this module has none of and the node is written to
    avoid. So the tuple travels, and the cost is paid honestly at
    ``test_a_helper_that_breaks_its_contract_yields_no_story_rather_than_a_raise``,
    whose stated mechanism changed with it: see :func:`moderator_story`'s
    unpack for what that guard now catches and what it no longer does.

    THE ASSEMBLY ORDER IS LOAD-BEARING, AND IT IS THE POINT OF TASK 3.3
    -------------------------------------------------------------------

    The private block is rendered FIRST and measured with
    :func:`~graphia.nodes.day._estimate_tokens`; the public history is then
    rendered with ``story_token_budget - private_tokens`` as ITS budget. So the
    private material is structurally protected and the public history is what
    gets cut — which is the spec's own rationale (§2.2): the story "earns its
    place from the private material". **Reversing this starves the thing the
    feature exists for, and it fails silently** — a well-formed prompt, a
    well-formed story, and nothing in it the player had not already read. That
    is why a test pins the direction rather than the docstring alone.

    ``_render_context``'s trim is oldest-first, so a very long game loses early
    public *dialogue* while keeping every early *diary* (§2.2's accepted
    trade-off; a head-and-tail keep was considered and rejected because it needs
    a second trimming implementation plus an elision marker the model may
    narrate as an event — a fabrication risk introduced to solve a completeness
    problem).

    ONLY THE PUBLIC HISTORY IS BUDGETED. The outcome sentence, the roster and
    the events block are O(table size) and O(deaths) — a handful of lines each,
    bounded by the game's own shape — so §2.2 budgets neither, and neither does
    this. Should a future table make the roster large enough to matter, it is
    the roster that would need a bound, not this ordering.

    THE TWO GOVERNORS ARE ARGUMENTS, NOT MODULE CONSTANTS (Task 3.5), and they
    come from ``GraphiaConfig.story_context_window`` /
    ``GraphiaConfig.story_token_budget`` through both graph builders'
    ``partial`` binds. They replaced a pair of provisional module constants
    read at call time; the numbers, their sizing arithmetic and the argument
    for the joint reading now live at ``config._DEFAULT_STORY_CONTEXT_WINDOW``
    / ``config._DEFAULT_STORY_TOKEN_BUDGET``, which is where an operator
    setting ``GRAPHIA_STORY_*`` will look. Three things worth carrying here:

    - **``story_token_budget`` IS JOINT** — it bounds the private block and the
      public history TOGETHER, which is why the subtraction below is a
      subtraction and not a second budget. §2.2 can be read either way; the
      config comment records why joint is the only reading under which the
      knob is a real ceiling (the private block is never windowed and never
      trimmed, so a public-only reading bounds nothing).
    - **The two are NOT independent.** ``_render_context`` slices by count
      first and applies the token trim afterwards, so ``story_context_window``
      is the outer bound and ``story_token_budget`` can only cut further.
      Raising the budget above what that many messages render does nothing at
      all, and fitting a smaller model is the *budget's* job — lowering the
      window bounds the message count, never the tokens per message.
    - Not the Day's ``context_window`` (150), deliberately (§2.2): that number
      expresses "the recent discussion", and a retrospective often needs Day 1.

    **``speaker_id=""`` IS A PRIVACY REQUIREMENT, NOT A CONVENIENCE** (§2.2,
    Task 3.4 owns the assertion). ``_render_context``'s filter is ``if
    private_to and private_to != speaker_id: continue``, so an id matching no
    player drops EVERY whisper: the human's private role reveal and the Mafia
    teammate intros cannot enter a prompt whose output is shown to the player.
    The Moderator gets the conspiracy from the Mafiosos' *diaries* instead,
    which is the whole point of the feature.

    ``state["players"]`` IS PASSED TO THE PRIVATE BLOCK, and no spec section
    says it needs the roster (found by Task 3.2): the two channels are keyed on
    ``player.id`` while the heading shows ``player.name``, and the per-author
    grouping order comes from ``players`` insertion order. It is the same object
    :func:`_story_roster_block` reads, so the two blocks agree by construction.

    TOTAL BY CONSTRUCTION (spec 040 §2.8). The whole body — the state reads,
    the four material blocks, the prompt build, the ``invoke`` and the
    validation of what comes back — sits inside one broad
    ``except Exception: return None``. ``None`` is the entire failure
    vocabulary: there is no fallback text and no substitution branch, which is
    a deliberate asymmetry from every sibling model-answered feature in the
    game (Task 1.5 records the argument at the failure site, and
    :class:`graphia.llm.Story` records why the schema is therefore absent from
    ``GAMEPLAY_SCHEMAS``).

    **Empty or whitespace-only counts as failure**, so a model that answers
    with a valid ``Story`` carrying nothing usable produces exactly the same
    absent ending a raised call does — which is the point, because absence is a
    legitimate outcome here and presence is what Slice 9 measures.

    ``get_large`` per architecture §4, which already names "the end-of-game
    creative recap (Phase 6)" as a use of the primary heavyweight tier, and
    ``with_structured_output`` rather than ``bind_tools`` — the ninth such call
    site in the game, and the first outside setup / Night / Day.

    NOTHING IS LOGGED ON FAILURE, deliberately. The obvious move is
    ``_ai_diary``'s ``logger.exception``, and spec 041 Task 6.5 established
    what that is actually worth here: nothing in the local game path installs a
    ``logging`` handler, ``GRAPHIA_LOG_FILE`` is the graph-stream JSONL rather
    than a ``logging`` destination, and Textual redirects stderr while the TUI
    is up. So the line would reach ``logging.lastResort`` and be invisible in
    the one mode this node runs in most. The designed instrument is Slice 9's
    ``quality.story_missing_rate`` census over a measured run, which is exactly
    the right shape for a once-per-game call.

    THE ONE COUNTER-ARGUMENT TO THAT WAS CHECKED RATHER THAN ASSUMED (Task
    1.5). A measured eval run is not a TUI session, so it could have had a
    handler — and a log line would then have had a real reader on the one path
    where a failed story actually costs somebody a measurement. It does not.
    ``graphia.tools.blunder_eval`` never imports ``logging`` at all: it
    ``print``s every harness diagnostic to stderr, and states why beside the
    diary-instrument disagreement warning — "``blunder_eval`` installs no
    logging handler (Task 6.5 established that), so a ``logger.warning`` would
    reach ``logging.lastResort`` — visible, but by accident, and silent the
    moment anything configures logging." The only ``addHandler`` in ``src/``
    is ``runtime/observability.py``'s, on the AgentCore side. So spec 041's
    finding holds on the measured path too, and the census really is the only
    instrument this failure has. If a handler ever lands, revisit this rather
    than assuming the omission was an oversight.
    """
    try:
        players = state.get("players", {})
        kill_log = list(state.get("kill_log", []))
        # ORDER: the private block first, and measured, so the budget the
        # public history is given is what the private material LEAVES OVER.
        # Both channels, dead and surviving writers alike, and the full diary —
        # never windowed (see ``_story_private_block``).
        private_record = _story_private_block(
            players,
            state.get("private_thoughts"),
            state.get("private_diaries"),
        )
        private_tokens = _estimate_tokens(private_record)
        # ``max(0, …)`` only so a reader never sees a negative "budget": a
        # private block bigger than the whole budget leaves nothing for the
        # public history, and ``_render_context``'s own ``len(lines) > 1``
        # guard then keeps exactly the newest line, which is strictly better
        # than an empty history. The private material is NOT trimmed to fit —
        # that is the protection this ordering exists to give it.
        context_budget = max(0, story_token_budget - private_tokens)
        # =================================================================
        # AUDIT FINDING (Task 4.2, 2026-09-08) — MEASURED, NOT FIXED HERE.
        # ``end_screen``'s facts message RE-ENTERS THE PROMPT THROUGH THIS
        # SLOT, in every game, guaranteed. The chain is short and each link
        # was checked: ``end_screen`` returns ``{"messages": [final_msg]}``
        # where ``final_msg`` is a PUBLIC ``SystemMessage`` (no
        # ``private_to``); ``graph.py`` wires ``add_edge("end_screen",
        # "moderator_story")``, so it is in ``state["messages"]`` by the time
        # this call runs; and ``_render_context`` keeps public
        # ``SystemMessage``s and labels them ``Moderator``. It is the NEWEST
        # message, so ``visible[-window:]`` always keeps it and the
        # token-budget trim — which drops the OLDEST lines — can never drop
        # it. Rendered output confirms it: the outcome line, the events list,
        # the full roster with every role, AND ``_persona_reveal_line``'s
        # cover-vs-true-self reveal all appear under
        # ``STORY_USER_TEMPLATE``'s heading "What was said out loud, oldest
        # first" — text that was never said out loud during play.
        #
        # WHY IT IS NOT FIXED HERE: the slot is Task 3.3's, the fabrication
        # measurement is Task 4.4's, and a filter here would also move the
        # byte-equality baseline ``test_spec040_story_whisper_privacy`` pins.
        # Scope discipline — reported, not fixed.
        #
        # WHY IT MATTERS, and it cuts two ways. (a) For the OUTCOME it is
        # benign, even helpful: ``_winner_line``'s sentence lands a second
        # time in the tail-most public position, so a runaway's "did not
        # resolve" is stated twice. (b) For §2.6's CENTRAL THESIS it is the
        # opposite. Move 1 tells the model the reader "has just read those
        # facts … they do not need any of that again", while this slot hands
        # it those exact facts as the most recent thing anyone said. §2.6's
        # account of the flat summary is "handed a fact sheet … a model
        # produces a chronological recap of the facts the player just read" —
        # and the fact sheet is in this prompt TWICE, once as slots and once
        # verbatim. That is a candidate explanation for the two live flat
        # recaps standing alongside Task 4.4's material-void one, and unlike
        # the void it is NOT closed by Slice 3: Slice 3 introduced it.
        # (c) It is the same SHAPE as Task 3.4's finding — non-play material
        # admitted into the public-history slot — but NOT the same route, so
        # ``speaker_id=""`` does nothing about it: the facts message is
        # public, not a whisper. Task 4.4 should distinguish a third cause.
        #
        # -----------------------------------------------------------------
        # DECIDED (Task 4.4, 2026-09-08): EXCLUDE IT. STILL NOT DONE HERE —
        # it is a named follow-up for Slice 10 to log, because the change is
        # to the assembly and this task's remit was to measure. The full
        # seven-reason argument is in tech spec §2.7 ("THE DECISION TASK 4.2
        # ASSIGNED TO TASK 4.4"); the two reasons that turn a redundancy
        # into a FABRICATION ROUTE, and that were not known when the block
        # above was written, are recorded here because this is the call site
        # a maintainer reads:
        #
        # 1. THE SLOT BOUNDARY BREAKS. ``_render_context`` prefixes only a
        #    message's FIRST line (see ``day.py``'s ``lines.append(
        #    f"{speaker}: {content}")``), so the fact sheet's own
        #    sub-headings — "Events this game:", "Full roster:", "Who they
        #    really were:" — land FLUSH-LEFT inside this slot and are
        #    typographically indistinguishable from
        #    ``STORY_USER_TEMPLATE``'s own slot labels. Measured on a
        #    composed prompt: EIGHT heading-like sections — five the
        #    template's own labels and three the facts message's
        #    sub-headings — and nothing marks which, because only the
        #    message's FIRST line carries the "Moderator: " prefix that
        #    would have shown the rest is quoted speech. So it is not "the
        #    fact sheet twice"; it is the fact sheet once as slots and once
        #    as three unlabelled pseudo-slots.
        # 2. IT PRESENTS THE REVEAL AS SPEECH. ``_persona_reveal_line``'s
        #    cover-vs-true-self text is the furthest thing in the prompt
        #    from anything anyone said in play, and this slot labels it
        #    "said out loud". Both live games that had no vote produced a
        #    story containing the word "revealed" for an event that never
        #    happened, which is Task 4.4's fourth fabrication category
        #    (a phase or process the game never ran) arriving through the
        #    MATERIAL. That is the third cause the block above asks for.
        #
        # SIZE, MEASURED RATHER THAN ARGUED, because "it is only one extra
        # message" is the natural objection: the block is ONE ATOMIC entry
        # that the trim cannot partially cut, and it was 48% of this slot's
        # estimated tokens in a live four-seat game with a full Day of
        # speech (1342 of ~2813), 74% in a short synthetic one, and 100% at
        # ``token_budget=1``. The tighter the budget — i.e. the smaller the
        # local model the budget exists to serve — the more this slot
        # degenerates into nothing but the fact sheet.
        #
        # NOTHING IS LOST: every fact in the message already has its own
        # slot from the SAME helper — ``_winner_line`` into ``{outcome}``,
        # ``_format_kill_line`` into ``{events}``, and
        # ``_story_roster_block`` into ``{roster}`` carrying side, fate, the
        # ``(the person playing)`` marker and each persona through
        # ``_persona_field``. Reason (a) above also survives: ``{outcome}``
        # still states a runaway's negative in full, and Task 4.3's outcome
        # tests assert PRESENCE, never a count, exactly so this decision
        # cannot break them.
        #
        # THE SHAPE THE FIX SHOULD TAKE, so the next reader does not pick
        # the worse of the two: mark the facts message the way
        # ``CLOSING_STORY_KWARG`` marks the story message and filter
        # ``state["messages"]`` HERE, before the call below. Do NOT teach
        # ``_render_context`` a skip-kwarg — the Day path shares it. And
        # prefixing every line of a multi-line message with its speaker was
        # considered and rejected: it fixes 1 alone, leaves 2 and the sizing
        # standing, and changes the Day path too.
        # ``tests/test_spec040_story_instruction.py``'s
        # ``test_the_facts_message_reaches_the_public_history_slot`` is the
        # standing witness and goes RED ON PURPOSE when this lands.
        # =================================================================
        context = _render_context(
            state.get("messages", []),
            # An id matching NO player: every whisper is dropped. A privacy
            # requirement — see the docstring.
            "",
            window=story_context_window,
            token_budget=context_budget,
        )
        llm = get_large().with_structured_output(Story)
        messages: list = [
            SystemMessage(content=STORY_SYSTEM),
            HumanMessage(
                content=STORY_USER_TEMPLATE.format(
                    # ``_winner_line`` is REUSED (§2.2) so the story's outcome
                    # and the facts message's cannot drift, and it already
                    # renders a whole sentence for the runaway and draw cases.
                    # Reference 4 of Task 4.2's outcome audit (above
                    # ``STORY_SYSTEM`` in ``prompts.py``): of its five branches
                    # only LAW and MAFIA name a side, and the defensive
                    # ``None`` fall-through names nothing at all.
                    outcome=_winner_line(state.get("winner")),
                    roster=_story_roster_block(players),
                    events=_story_events_block(kill_log, players),
                    context=context,
                    private_record=private_record,
                )
            ),
        ]
        result = llm.invoke(messages)
        if isinstance(result, Story) and result.text.strip():
            # Slice 1's placeholder ``.strip()`` replaced by Slice 5's
            # paragraph-preserving clamp (Task 5.1). The truthiness test above
            # is UNCHANGED and stays the single gate on an unusable answer:
            # ``test_an_unusable_answer_yields_no_story_at_all`` parametrises
            # ``""``, ``"   "`` and ``"\n\n\t "``, and its docstring warns in
            # as many words that a clamp returning ``""`` with that guard
            # dropped would emit an EMPTY story message — a blank section in
            # the ending, which the functional spec forbids.
            #
            # No second guard on the clamp's output, deliberately: it cannot
            # empty a non-empty answer. A truthy ``strip()`` means at least one
            # non-whitespace character, which lands in some paragraph, which
            # survives the fold, and the clamp's first-paragraph exemption
            # keeps it whatever its length. An ``if not story: return None``
            # here would be unreachable — untestable, so Task 5.4 could not
            # witness it and a later refactor could delete it silently, which
            # is the vacuous-guard trap Task 10.1 recorded. The invariant is
            # worth a test; a dead branch is not.
            #
            # TASK 5.3 LANDED HERE, and it is one line: the clamp's second
            # element is FORWARDED rather than discarded. The tuple is returned
            # exactly as the clamp produced it and the flag is never re-derived
            # downstream — a story that is exactly ``STORY_MAX_PARAGRAPHS``
            # paragraphs long is indistinguishable from a longer one that lost
            # its last, which is the entire reason §2.5 wants it marked. The
            # widening of this helper's return is argued in the docstring.
            return _clamp_story(result.text)
        return None
    # =====================================================================
    # NO FALLBACK TEXT. Both ``return None``s above and the one below are
    # the whole failure vocabulary, and this is the one place a reader will
    # "fix" that back: §2.8 predicts in as many words that "a reader will
    # otherwise add one to match the pattern". Hence the argument here
    # rather than in a spec nobody re-reads. Three legs; the third is the
    # one that costs real money if it is lost.
    #
    # 1. A PLACEHOLDER STORY IS THE THING THE FUNCTIONAL SPEC FORBIDS, not
    #    a mitigation of it. Two criteria, and they sit under two DIFFERENT
    #    headings — worth knowing, because only the first is about failure
    #    and it is the decisive one on its own. "A game whose story cannot
    #    be told still ends properly" requires that "no error and no apology
    #    about a missing story is shown", and says what the good outcome is:
    #    "that section is simply absent" and "everything else about the
    #    ending appears exactly as it does today". A deterministic sentence
    #    in the story's place is an apology in all but name and it is not
    #    "simply absent" — it ADDS a section, so the ending stops reading as
    #    it does today. Under the waiting-beat heading ("While the story is
    #    being produced, the person can see that something is coming") the
    #    ending is enumerated as "the facts, the story and the career
    #    summary", with its third criterion carrying that through to the
    #    no-story case explicitly. That clause is about leftover WAITING
    #    text, so read it as corroborating rather than as the finding: a
    #    placeholder would be a fourth thing in a three-thing ending, and
    #    not one of the three.
    #
    # 2. THE ASYMMETRY FROM SPEC 028 (Per-AI Day-Round Private Thoughts)
    #    AND SPEC 039 (Per-AI Private Diaries) IS DELIBERATE, AND THIS IS
    #    THE INCONSISTENCY THAT IS CORRECT. ``_REFLECTION_FALLBACK`` and
    #    ``_DIARY_FALLBACK`` exist for a reason this node does not share,
    #    and both say so at their definitions: "so one model hiccup never
    #    blanks that player's channel". Those channels are READ BACK — by
    #    the writing player, into a later prompt — so a hole in one changes
    #    play, and a deterministic stand-in is cheaper than a gap. Nothing
    #    in the game reads the story: it is the last content produced, no
    #    prompt is built from it, no decision turns on it, no later node
    #    consumes it. Their second stated reason ("the tests stay
    #    non-flaky") does not transfer either — absence here is
    #    deterministic and Task 1.8 asserts it directly. So the reason the
    #    siblings carry a constant is absent, and the reason not to carry
    #    one (leg 1) is present. Matching the pattern would import the cost
    #    without the benefit.
    #
    # 3. SLICE 9's MEASUREMENT DEPENDS ON THE ABSENCE, AND THE COUPLING IT
    #    AVOIDS HAS ALREADY BEEN PAID FOR ONCE — go and look at it before
    #    changing this. 039's instrument had to be the fallback TEXT,
    #    because ``day_diary`` accumulates one ``DiaryRecord`` per surviving
    #    AI writer whether the model answered or not, so a failed diary is
    #    indistinguishable from a real one BY PRESENCE. The bill, all of it
    #    live in ``graphia.tools.blunder_eval`` today:
    #    ``_diary_fallback_text()`` reaches LAZILY into the module-PRIVATE
    #    ``graphia.nodes.day._DIARY_FALLBACK`` behind a ``try/except ->
    #    None``, under fifteen docstring lines justifying it; and
    #    ``score_diary_fallback`` then has to match that sentence EXACTLY
    #    and admits the false positive in its own docstring — a model that
    #    writes the placeholder unprompted is counted as a failure, which
    #    spec 041 Task 7.1 went on to demonstrate live (a scripted answer of
    #    ``_DIARY_FALLBACK``'s own text recorded ``diary_fallback_rate:
    #    1.0`` beside ``substitutions.diary`` at 0.0).
    #
    #    ONE NUANCE, CHECKED, AND IT CUTS THIS WAY: §2.10 gives 039's
    #    reason in the present tense, but spec 041 Slice 8 added
    #    ``DiaryRecord["substituted"]``, so a substituted diary IS
    #    distinguishable today — ``_diary_fallback_text``'s own docstring
    #    says the old argument "HAS BEEN OVERTAKEN — read it as history,
    #    not as guidance". The reach-in nevertheless survives, because a
    #    player who WRITES the placeholder sentence has not substituted and
    #    the two cases cannot be told apart post-hoc. So the honest reading
    #    is not "039 was stuck" but "once placeholder TEXT exists, you own
    #    it for good": a text instrument, a state-shape flag, and a warning
    #    reconciling the two. That is the road a placeholder story puts 040
    #    on, from a standing start.
    #
    #    040 pays none of that, and only because the story is ABSENT on
    #    failure: ``story_missing_rate`` counts endings carrying no message
    #    flagged with the PUBLIC ``CLOSING_STORY_KWARG``, so PRESENCE IS THE
    #    SIGNAL — no private reach-in, no text match, no false positive.
    #    §2.10 states the reversal plainly: "if a placeholder story is ever
    #    added, this evaporates and 040 inherits 039's whole coupling
    #    problem." §2.12's no-ablation-flag decision sharpens it further —
    #    with no off arm, absence in a post-040 record is UNAMBIGUOUS, it
    #    means failure — which makes the signal more valuable, not less.
    #    And there is no second instrument to fall back on: ``Story`` is
    #    deliberately outside ``GAMEPLAY_SCHEMAS`` (Task 1.1), so there is
    #    no ``SubstitutionKind.STORY`` and the substitution sink cannot see
    #    this call at all.
    # =====================================================================
    except Exception:
        return None


def moderator_story(
    state: GameState,
    *,
    story_context_window: int = _DEFAULT_STORY_CONTEXT_WINDOW,
    story_token_budget: int = _DEFAULT_STORY_TOKEN_BUDGET,
) -> dict:
    """Tell the Moderator's closing story, or say nothing at all.

    Returns ``{"messages": [story_msg]}`` or ``{}``, and **never raises**.

    THE TWO SIZE GOVERNORS ARE BOUND HERE, NOT READ (spec 040 Task 3.5). Both
    graph builders bind ``GraphiaConfig.story_context_window`` /
    ``GraphiaConfig.story_token_budget`` into this node with ``partial``, the
    same service-injection shape ``day_turn`` / ``day_diary`` use for the Day
    pair, and this node's only job with them is to forward them to
    :func:`_ai_story` — which is where the argument for the joint budget and
    the count-then-tokens dependency between the two is written down. The
    keyword defaults are the config defaults so the direct-call tests (and the
    unbound registration a future refactor might leave behind) render the same
    prompt the graph does.

    IT DOES NOT RETURN ``phase``. ``end_screen`` set it one super-step ago;
    returning it again would re-assert a value this node has no opinion about.

    THE MESSAGE IS A ``SystemMessage`` WITH NO ``name``, AND THAT IS A
    LEDGER-CORRUPTION GUARD RATHER THAN A STYLE CHOICE (§2.6, verified).
    ``blunder_eval._ai_lines_with_names`` and ``_ai_lines_with_speakers`` both
    admit a message only when ``isinstance(m, AIMessage)`` **and** its ``name``
    is a non-human player's, so an ``AIMessage`` named after a player would
    drop several hundred words of Moderator prose into the repetition pool and
    corrupt an append-only quality ledger. A ``SystemMessage`` fails the first
    test and no ``name`` fails the second, so the guard is doubly redundant on
    purpose. The story is the Moderator's voice in any case, which is why the
    correct type and the safe type happen to agree.

    ``additional_kwargs={CLOSING_STORY_KWARG: True}`` is how every downstream
    reader identifies it: spec 040 §2.9 discriminates on the MESSAGE, never on
    the node or the position, and the constant lives in ``graphia.state``
    precisely so ``eval_transcript`` and ``blunder_eval`` can import it instead
    of reaching into a private ``nodes`` constant the way 039 had to.

    A SECOND, INDEPENDENT MACHINE FLAG JOINS IT ONLY WHEN THE CAP FIRED (Task
    5.3): ``CLOSING_STORY_TRUNCATED_KWARG``, added when :func:`_clamp_story`
    dropped at least one whole paragraph and ABSENT otherwise, never ``False``.
    It exists so a reviewer judging a story that stops early attributes the
    stop to the cap rather than to the storyteller — the wrong conclusion being
    exactly the one the whole feature is meant to be judged against. It cannot
    be recovered downstream (an exactly-``STORY_MAX_PARAGRAPHS`` story and a
    longer one that lost its last look identical), so it is read from the clamp
    and plumbed, never measured off the story. The transcript attribute it
    feeds, ``<story truncated="true">``, belongs to **Slice 8**, which is the
    slice that builds the element. **THE READER NOW EXISTS (Task 8.7,
    2026-09-09) and this paragraph's last clause said "today the flag has one
    writer and no reader" until then** — true when Task 5.3 wrote it, and
    nearly permanent, because Slice 8 built the element (Task 8.3) with no task
    line for the attribute. The reader is
    ``tools.eval_transcript._split_endgame_texts``, which lifts this flag off
    the story message in the same pass that partitions the endgame delta.

    WHY THERE ARE TWO GUARD LAYERS, and what each catches that the other
    cannot — because the second looks redundant and §2.8's stated reason for it
    does not survive checking. §2.8 calls this one "belt-and-braces for a
    malformed ``players`` map or a persona shape surprise", but both of those
    are read INSIDE :func:`_ai_story`'s own ``try``, so that is not what this
    catch adds. What it adds is SCOPE — every line beside the call, which
    ``_ai_story`` cannot reach:

    - **Measured, not argued:** feed :func:`_ai_story` a return that breaks its
      contract and the graph sees an empty update instead of a raise. Only this
      catch is between the two. So the outer layer's real job is guarding
      against the HELPER, not against the model — which is a different failure
      than the one §2.8 named, and a live one. **The RAISE it catches moved
      when Task 5.3 widened the helper's return to ``tuple[str, bool] | None``:
      the test's ``17`` now fails at the unpack (``TypeError``) rather than at
      the ``SystemMessage`` construction (Pydantic validation). Same catch,
      same outcome, one line earlier — and the coverage is narrower than it
      was, because a two-element return now unpacks cleanly. The unpack site
      records exactly what that costs; do not restate the pre-5.3 mechanism
      here or in the test.**
    - Slice 5's truncation-flag computation lands here too, on the story that
      comes back (§2.5), so this catch grows in coverage while the inner one
      does not. **Half-true as written, corrected by Task 5.1 and now settled
      by Task 5.3:** the clamp that COMPUTES the flag went inside
      :func:`_ai_story`, under the INNER layer, so this catch gained nothing
      from it. What did land out here is the flag's PLUMBING — the unpack and
      the conditional ``additional_kwargs`` entry, both beside the
      ``SystemMessage`` construction and therefore genuinely only this layer's
      to catch. The prediction was right about the layer and wrong about which
      half arrives.

    "The node never raises" is a property the graph and the UI depend on, and
    it must not be one that a later refactor of a private helper can quietly
    revoke. Neither layer is broadened past that: both catch ``Exception``, not
    ``BaseException``, so ``KeyboardInterrupt`` and the suite's
    ``pytest.fail`` still travel.

    TWO CAREER PATHS DEPEND ON THAT, not one, and the second is the likelier
    way the data is actually lost (§2.8). The in-graph ``career_emitter`` emit
    is already safe by node separation. But ``GraphiaApp._record_career`` sits
    INSIDE ``_drive``'s ``try``, after ``await drive_graph(...)``, so a raising
    story node would drop the UI-side career fold too and show an error banner
    where the ending should be. Do not weaken either layer.

    ONE COST OF TOTALITY, worth knowing rather than fixing: ``conftest``'s
    ``_LoudFailureLLM`` raises ``RuntimeError``, an ordinary ``Exception``, so a
    test that forgets ``fake_large``'s re-patch is swallowed here into a
    perfectly legitimate no-story ending. That is why the story tests assert
    ``calls_by_schema[Story]`` — it separates "the call failed" from "the call
    was never made", which no inspection of the ending can. The queue's own
    unknown-schema failure is NOT affected: spec 041 made it ``pytest.fail``,
    whose ``Failed`` derives from ``BaseException`` and walks straight through
    both layers.
    """
    try:
        # TASK 5.3's PLUMBING DECISION, TAKEN: SHAPE 1, the widening.
        # :func:`_ai_story` returns ``(story, clipped) | None`` and this
        # unpacks it. The alternative was to leave the ``str | None`` contract
        # alone and have the helper record the flag out of band — rejected
        # because out-of-band state in this module has NO precedent and would
        # be the first, whereas the widening's whole cost is one test's
        # docstring. The clamp's signature was built for this shape.
        told = _ai_story(
            state,
            story_context_window=story_context_window,
            story_token_budget=story_token_budget,
        )
        if told is None:
            # The OTHER place a placeholder would be injected (``told =
            # (_STORY_FALLBACK, False)`` here, instead of in the helper). It
            # must not be, for the three reasons argued at :func:`_ai_story`'s
            # ``except``. An ending with no story is the designed outcome.
            return {}
        # WHAT THE OUTER CATCH NOW GUARDS, AND WHAT IT NO LONGER DOES — read
        # this before trusting the test's name. The measured claim behind
        # ``test_a_helper_that_breaks_its_contract_yields_no_story_rather_than_a_raise``
        # was that a contract-breaking return raises Pydantic validation at the
        # ``SystemMessage`` construction below, outside the inner ``try``. Two
        # corrections, both verified 2026-09-09:
        #
        # - THE MECHANISM MOVED ONE LINE UP. A non-iterable return (the test's
        #   ``17``) now raises ``TypeError`` at THIS unpack, not Pydantic
        #   validation below. Same catch, same empty update, different raise —
        #   the docstring says so, because a stale stated mechanism is how this
        #   spec has already produced four wrong in-source instructions.
        # - THE GUARANTEE IS GENUINELY NARROWER, and the exact shape of the
        #   hole was MEASURED rather than reasoned, because reasoning got it
        #   wrong first. Two layers still catch, in this order: a return that
        #   will not unpack into two raises here (``17``, ``object()``, any
        #   string but a two-character one), and a two-element return whose
        #   first element is not valid ``content`` still raises Pydantic
        #   validation below (``[1, 2]`` -> ``content=1`` -> caught). What
        #   slips through is precisely a two-element return whose FIRST element
        #   is a valid ``content``: ``{"a": 1, "b": 2}`` (dict unpacking yields
        #   its keys) and the old contract's own two-character ``"ab"`` both
        #   produce a story message with ``content="a"``. No guard was added
        #   for it, deliberately: ``_ai_story`` sits in this module and returns
        #   either ``None`` or :func:`_clamp_story`'s tuple, so the case is
        #   unreachable without a monkeypatch, and this module's own rule
        #   (Task 10.1's vacuous-guard finding) is that a branch no production
        #   path can enter is worse than the hole it closes. Recorded rather
        #   than defended.
        text, clipped = told
        # ONLY WHEN THE CAP ACTUALLY FIRED. The flag is added, never set to
        # ``False``: every reader is ``extra.get(KEY)``, so absent and ``False``
        # are one answer, and an unclipped story's ``additional_kwargs`` stays
        # byte-identical to what Slice 1 shipped — which is what
        # ``test_spec040_story_node``'s exact ``== {CLOSING_STORY_KWARG: True}``
        # equality pins, and it is right to pin it. Full argument at
        # ``state.CLOSING_STORY_TRUNCATED_KWARG``.
        extra: dict[str, bool] = {CLOSING_STORY_KWARG: True}
        if clipped:
            extra[CLOSING_STORY_TRUNCATED_KWARG] = True
        story_msg = SystemMessage(content=text, additional_kwargs=extra)
        return {"messages": [story_msg]}
    except Exception:
        return {}


def route_after_win_night(state: GameState) -> str:
    """After night-side win check: end_screen if someone won, else night_close."""
    if state.get("winner") is not None:
        return "end_screen"
    return "night_close"


def route_after_win_day(state: GameState) -> str:
    """After day-side win check.

    Priority:
      1. Winner set → ``end_screen``.
      2. Else delegate to the Slice 7 logic: an execution this cycle or
         cap-hits (votes / rounds) end the Day; otherwise resume speaking.
    """
    from graphia.nodes.day import DAY_MAX_ROUNDS, DAY_MAX_VOTES

    if state.get("winner") is not None:
        return "end_screen"

    cycle = state.get("cycle", 1)
    for rec in reversed(state.get("kill_log", [])):
        if rec.get("cycle") != cycle:
            break
        if rec.get("cause") == "execution":
            return "day_close"
    if state.get("day_votes_called", 0) >= DAY_MAX_VOTES:
        return "day_close"
    if state.get("day_rounds", 0) >= DAY_MAX_ROUNDS:
        return "day_close"
    return "day_turn"
