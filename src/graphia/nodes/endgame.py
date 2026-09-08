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
    STORY_SYSTEM,
    STORY_USER_TEMPLATE,
)
from graphia.state import (
    CLOSING_STORY_KWARG,
    DiaryRecord,
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

    final_msg = SystemMessage(content="\n".join(lines))

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
# (Task 3.3). Two later slices still grow this: Slice 4 writes the real system
# prompt (``STORY_SYSTEM``'s seven moves — none of them is present yet) and
# Slice 5 adds the paragraph-preserving clamp and its truncation flag.
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


def _ai_story(
    state: GameState,
    *,
    story_context_window: int = _DEFAULT_STORY_CONTEXT_WINDOW,
    story_token_budget: int = _DEFAULT_STORY_TOKEN_BUDGET,
) -> str | None:
    """Ask the large tier for the closing story; ``None`` when there is none.

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
            # Slice 5 replaces this ``strip`` with the paragraph-preserving
            # clamp. A plain strip is the forward-compatible placeholder: it
            # leaves the internal blank lines that ARE the story's structure,
            # which is precisely what 039's ``_clamp_diary_entry`` would fold
            # away and why that clamp must not be reused here.
            return result.text.strip()
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

    WHY THERE ARE TWO GUARD LAYERS, and what each catches that the other
    cannot — because the second looks redundant and §2.8's stated reason for it
    does not survive checking. §2.8 calls this one "belt-and-braces for a
    malformed ``players`` map or a persona shape surprise", but both of those
    are read INSIDE :func:`_ai_story`'s own ``try``, so that is not what this
    catch adds. What it adds is SCOPE — every line beside the call, which
    ``_ai_story`` cannot reach:

    - **Measured, not argued:** feed :func:`_ai_story` a return that breaks its
      ``str | None`` contract (an ``int``, a ``dict``, a bare ``object``) and
      the ``SystemMessage`` construction two lines below raises Pydantic
      validation. Only this catch is between that and the graph. So the outer
      layer's real job is guarding against the HELPER, not against the model —
      which is a different failure than the one §2.8 named, and a live one.
    - Slice 5's truncation-flag computation lands here too, on the story that
      comes back (§2.5), so this catch grows in coverage while the inner one
      does not.

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
        text = _ai_story(
            state,
            story_context_window=story_context_window,
            story_token_budget=story_token_budget,
        )
        if text is None:
            # The OTHER place a placeholder would be injected (``text =
            # _STORY_FALLBACK`` here, instead of in the helper). It must not
            # be, for the three reasons argued at :func:`_ai_story`'s
            # ``except``. An ending with no story is the designed outcome.
            return {}
        story_msg = SystemMessage(
            content=text,
            additional_kwargs={CLOSING_STORY_KWARG: True},
        )
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
