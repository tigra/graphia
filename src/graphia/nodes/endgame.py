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

Note the ORDER in which spec 040 lands that second bullet: this module gains
the node in Task 1.4 and ``graph.py`` gains the ``end_screen`` ->
``moderator_story`` -> END edge in Task 1.6. Between those two tasks the node
exists and is importable but is UNREACHABLE from any graph, which is why the
tests written for it drive it directly rather than through a game.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from graphia.career_events import (
    KIND_GAME_ENDED,
    CareerEvent,
    CareerEventEmitter,
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
    GameState,
    KillRecord,
    PlayerState,
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
# EVERYTHING BELOW IS SLICE 1's. Three later slices grow it and none has run:
# Slice 3 assembles the private material (the shared cursor walk, the
# third-party renderer, the token budgeting, the whisper exclusion), Slice 4
# writes the real prompt, and Slice 5 adds the paragraph-preserving clamp and
# its truncation flag. What is here is the thinnest thing that proves the node,
# the edge and the failure path.
# ---------------------------------------------------------------------------


def _story_roster_block(players: dict[str, PlayerState]) -> str:
    """One line per player: name, side, and whether they lived to the end.

    DELIBERATELY MINIMAL — Slice 1's roster, not the finished one. Spec 040
    §2.2 specifies the eventual block as name, side, alive-or-dead, ``(the
    person playing)`` for the human seat, and each AI's persona read through
    :func:`_persona_field`; **Task 3.3 owns all of that**, including the
    ``_persona_field`` requirement, which is mandatory rather than stylistic
    (that function's docstring records why: after a checkpoint round-trip the
    serde hands back a ``PlayerPersona`` as a plain ``dict``, and
    ``moderator_story`` is the second reader of persona fields out of
    checkpointed state, so a bare attribute access would ``AttributeError``).
    What is here is the three fields a story cannot do without.

    :func:`_role_label` is reused rather than re-spelled, so the side names the
    story reads are the ones the facts message just showed the player.
    """
    lines = [
        f"• {p.name} — {_role_label(p.role)} — "
        f"{'alive at the end' if p.is_alive else 'dead'}"
        for p in players.values()
    ]
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


def _ai_story(state: GameState) -> str | None:
    """Ask the large tier for the closing story; ``None`` when there is none.

    TOTAL BY CONSTRUCTION (spec 040 §2.8). The whole body — the state reads,
    the three material blocks, the prompt build, the ``invoke`` and the
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
    #    a mitigation of it. Under "A game whose story cannot be told still
    #    ends properly" the criteria are that "no error and no apology about
    #    a missing story is shown", and that "nothing shown during the wait
    #    remains in it" — no leftover text. A deterministic sentence in the
    #    story's place is an apology in all but name, and it is precisely a
    #    leftover: it occupies the ending without being the story. Absence
    #    satisfies both criteria trivially and a placeholder satisfies
    #    neither. "That section is simply absent" is the spec's own wording
    #    for the GOOD outcome, not for a degraded one.
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


def moderator_story(state: GameState) -> dict:
    """Tell the Moderator's closing story, or say nothing at all.

    Returns ``{"messages": [story_msg]}`` or ``{}``, and **never raises**.

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
        text = _ai_story(state)
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
