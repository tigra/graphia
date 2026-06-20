"""Day-phase nodes: victim reveal, round-robin speaking, and vote-to-execute.

Topology contract (Slice 7):

- ``day_open`` runs once per Day, emits the victim reveal, seeds the first
  round's speaking order, resets counters, and sets ``phase="day"``.
- ``day_turn`` runs ONE player's turn per super-step. This is critical for
  replay-safety: a human ``interrupt()`` in one turn must not replay AI
  speaking calls from earlier in the same round. A human turn may also
  start with ``/vote <name>`` to initiate a vote-to-execute, and an AI turn
  may return ``DayAction(kind="vote", target_id=...)``.
- When a round completes (turn index wraps back to 0), ``day_turn`` itself
  bumps ``day_rounds`` and reshuffles ``day_order`` for the next round using
  the module-global ``random`` RNG.
- On vote initiation, ``day_turn`` sets ``active_vote`` and routes to
  ``vote_prompt``, which announces the vote. ``collect_votes`` then polls
  each alive player in roster order, ONE voter per super-step, and
  ``resolve_vote`` tallies the result.
- The Day ends when any of:
    * A vote succeeds (target executed) → ``day_close`` → ``night_open``.
    * ``day_votes_called >= 3`` (3 votes used, last one failed) → ``day_close``.
    * 6 speaking rounds with no successful vote → ``day_close``.
"""

from __future__ import annotations

import dataclasses
import random
from typing import cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from graphia.career_events import (
    KIND_BALLOT_CAST,
    KIND_VOTE_INITIATED,
    KIND_VOTE_RESOLVED,
    CareerEvent,
    CareerEventEmitter,
)
from graphia.llm import Ballot, DayAction, get_large
from graphia.prompts import (
    AI_VOTE_SYSTEM,
    AI_VOTE_USER_TEMPLATE,
    DAY_OPEN_NO_VICTIM_TEMPLATE,
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    DAY_ROUND_RECAP_TEMPLATE,
    DAY_SPEAK_SYSTEM,
    DAY_SPEAK_USER_TEMPLATE,
    VOTE_EXECUTED_TEMPLATE,
    VOTE_FAILED_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
    VOTE_TALLY_TEMPLATE,
)
from graphia.state import ActiveVote, GameState, KillRecord, PlayerState

# The Day ends automatically after this many rounds with no vote called.
DAY_MAX_ROUNDS = 6

# The Day ends after this many failed votes.
DAY_MAX_VOTES = 3

# Number of recent public messages to include as context for AI speakers.
_CONTEXT_WINDOW = 30


def _role_label(role: str) -> str:
    return "Mafia" if role == "mafia" else "Law-abiding Citizen"


def _win_condition_line(role: str) -> str:
    """Return the actor's win condition, worded to match ``check_win_condition``.

    The mechanical rule (``graphia.nodes.endgame.check_win_condition``):
    Law-abiding wins when no Mafia remain; Mafia wins when the Mafia count is
    greater than or equal to the Law-abiding count. We lift that wording
    verbatim here so the prompt objective the model optimises matches the rule
    the engine actually enforces (load-bearing — spec 013 §2.5).
    """
    if role == "mafia":
        return (
            "Your side, the Mafia, wins when the Mafia count is greater than "
            "or equal to the Law-abiding count."
        )
    return (
        "Your side, the Law-abiding Citizens, wins when no Mafia remain."
    )


def _teammates_str(actor: PlayerState, players: dict[str, PlayerState]) -> str:
    """Return the alive Mafia names (excluding ``actor``) for a teammate line.

    Mafia-only by design (spec 013 §2.5 knowledge-boundary invariant): this is
    called only for a Mafioso, and it enumerates *Mafia* peers — the only other
    players whose allegiance a Mafioso legitimately knows. A Law-abiding Citizen
    never receives a teammate list (its ``team_line`` is empty); disclosing
    fellow Citizens would collapse the deduction game. Returns a sentinel when
    the actor is the last Mafioso standing.
    """
    names = [
        p.name
        for p in players.values()
        if p.is_alive and p.role == "mafia" and p.id != actor.id
    ]
    if not names:
        return "(none — you are the last Mafioso)"
    return ", ".join(names)


def _team_line(actor: PlayerState, players: dict[str, PlayerState]) -> str:
    """Mafia-only fellow-Mafiosi disclosure line; ``""`` for a Citizen.

    Knowledge-boundary invariant (spec 013 §2.5): a Citizen learns nothing
    about any other player's side, so its team line is the empty string.
    """
    if actor.role != "mafia":
        return ""
    return (
        f"Your fellow Mafiosi (keep this secret): "
        f"{_teammates_str(actor, players)}."
    )


def _persona_block(speaker: PlayerState) -> str:
    """Render the speaker's persona as a voice-layer block for its OWN prompt.

    Persona is the *voice/temperament* layer atop the spec-013 *role-facts*
    grounding (`_role_label` / `_win_condition_line` / `_team_line`). It is
    injected ONLY into this speaker's own Day-speech prompt — never broadcast,
    never threaded into another player's prompt (privacy invariant, spec 016
    §2.3): a Mafioso's ``true_self`` would otherwise leak its allegiance.

    For ALL AI speakers we surface the persona's ``personality``, ``manner``,
    and the ``public_persona`` it projects, framed as "play this character;
    speak in this voice." For a Mafioso we ADDITIONALLY surface its
    ``true_self`` plus an explicit stay-in-cover, never-reveal instruction —
    the cover is the face shown to the table; the true self stays hidden.

    Defensive: ``persona`` should always be populated for an AI player (set in
    ``generate_personas`` at setup), but if it is ``None`` we render an empty
    block so ``.format`` never breaks.
    """
    persona = speaker.persona
    if persona is None:
        return ""
    lines = [
        "You are playing this character — speak in this voice throughout:",
        f"- Personality: {persona.personality}",
        f"- Manner of speaking: {persona.manner}",
        f"- The public face you present at the table: {persona.public_persona}",
    ]
    if speaker.role == "mafia":
        lines.append(
            f"- YOUR SECRET TRUTH (never reveal): {persona.true_self} "
            "This public face is a cover. Maintain it at all times and NEVER "
            "reveal that you are Mafia or that your persona is a front."
        )
    return "\n".join(lines)


def _ballot_relationship(voter: PlayerState, target: PlayerState) -> str:
    """Return the node-computed ballot relationship NUDGE (never an imperative).

    Both phrasings are persuasion, not a mechanical ban (spec 013 §2.6): each of
    self-execution and teammate-execution can be a rare legitimate strategy
    (self-sacrifice; bussing a teammate to deflect suspicion). Emits only:

    * "is YOU" — when the voter is the target;
    * "is your fellow Mafioso" — only when BOTH voter and target are Mafia;
    * "" — otherwise.

    It never labels a target as a fellow Citizen or discloses any other-player
    allegiance, so a Law-abiding voter's ballot leaks nothing (its relationship
    is only ever "is YOU" on a self-targeted ballot, else "").
    """
    if voter.id == target.id:
        return (
            f"** {target.name} is YOU. Executing yourself normally loses you "
            f"the game — vote No unless this is a deliberate self-sacrifice "
            f"play. **"
        )
    if voter.role == "mafia" and target.role == "mafia":
        return (
            f"** {target.name} is your fellow Mafioso. Executing a teammate "
            f"normally costs you the game — vote No unless you have a "
            f"deliberate bus-the-teammate reason. **"
        )
    return ""


def _find_last_night_victim(
    kill_log: list[KillRecord], cycle: int
) -> KillRecord | None:
    """Return the most recent night-cause kill for ``cycle``, or None."""
    for record in reversed(kill_log):
        if record.get("cause") == "night" and record.get("cycle") == cycle:
            return record
    return None


def _executed_this_cycle(
    kill_log: list[KillRecord], cycle: int
) -> KillRecord | None:
    """Return the execution-cause KillRecord for ``cycle``, or None.

    Shared by ``day_close`` (which only needs the boolean "did an execution
    happen this cycle", derived from ``is not None``) and the recap renderer
    (which names the executed player and their revealed side). Lifting this out
    of the inline ``day_close`` predicate keeps the two callers DRY.
    """
    for record in reversed(kill_log):
        if record.get("cause") == "execution" and record.get("cycle") == cycle:
            return record
    return None


def _render_standings(state: GameState) -> str:
    """Render the decision-relevant standings BODY (spec 019), as a plain string.

    Returns ONLY the standings text — the
    ``"{law_clause} and {mafia_clause} remain. {votes_clause} {executed_clause}"``
    body — with **NO clock** (spec 020's, recap-only) and **NO "Day N status:"
    framing prefix** (that stays in ``render_day_round_recap``). This is the
    single source of the standings text: ``render_day_round_recap`` (the public
    recap) and the two AI Day-turn prompts (``_ai_day_action`` / ``_ai_ballot``)
    all consume it, so the standings shown publicly and fed to the AI can never
    drift.

    PURE: counts alive players by role from the insertion-ordered ``players``
    dict, reads ``day_votes_initiated``, and derives the executed-today clause
    from ``_executed_this_cycle`` (keyed on ``cycle``). It mutates nothing and
    uses NO randomness and no hash-order-dependent ``set`` iteration (iterating
    the ordered ``players`` dict only) so the dual-mode byte-equal smoke test
    stays green.

    Discloses nothing hidden: only aggregate living counts by side, the
    votes-called-today count, and the already-public executed player's revealed
    side — never a living player's secret side.
    """
    cycle = state.get("cycle", 1)
    players = state.get("players", {})

    law_count = sum(
        1 for p in players.values() if p.is_alive and p.role == "law_abiding"
    )
    mafia_count = sum(
        1 for p in players.values() if p.is_alive and p.role == "mafia"
    )

    law_noun = "Law-abiding Citizen" if law_count == 1 else "Law-abiding Citizens"
    mafia_noun = "Mafioso" if mafia_count == 1 else "Mafiosos"
    law_clause = f"{law_count} {law_noun}"
    mafia_clause = f"{mafia_count} {mafia_noun}"

    votes = state.get("day_votes_initiated", 0)
    if votes == 0:
        votes_clause = "No execution votes called yet today."
    elif votes == 1:
        votes_clause = "1 execution vote called today."
    else:
        votes_clause = f"{votes} execution votes called today."

    executed = _executed_this_cycle(state.get("kill_log", []), cycle)
    if executed is None:
        executed_clause = "No one has been executed today."
    else:
        executed_clause = (
            f"{executed['name']} was executed today and was revealed to be "
            f"{_role_label(executed['role'])}."
        )

    return (
        f"{law_clause} and {mafia_clause} remain. "
        f"{votes_clause} {executed_clause}"
    )


# In-world clock for the Day's rounds (spec 020): round 1 is morning, advancing
# one step per round toward midnight at round 6, so the recap reads like the Day
# burning down toward Night. Indexed by ``round - 1`` after clamping.
_ROUND_CLOCKS = ("9 AM", "12 PM", "3 PM", "6 PM", "9 PM", "12 AM (midnight)")


def _round_clock(day_round: int) -> str:
    """Map a 1-based Day round to its in-world clock time (spec 020).

    PURE display-only helper: round 1 → ``9 AM``, advancing one step per round
    (``12 PM``, ``3 PM``, ``6 PM``, ``9 PM``) to ``12 AM (midnight)`` at round 6.
    Clamps both ends so the time never runs past midnight and never before
    morning: ``< 1`` maps to ``9 AM`` and ``> 6`` maps to midnight. The
    ``(midnight)`` parenthetical is kept so the "Night falls" reading is clear.

    No RNG, no LLM, NO wall-clock — the time is purely a reading of the round the
    Day is already on, so the dual-mode byte-equal smoke stays green.
    """
    return _ROUND_CLOCKS[max(1, min(day_round, 6)) - 1]


def render_day_round_recap(state: GameState, *, day_round: int) -> SystemMessage:
    """Render the end-of-round public Moderator status recap (spec 018).

    A thin composer over ``_render_standings`` (spec 019): it keeps the
    ``"Day {day}, {clock} status: …"`` framing and wraps the standings body in
    the public ``SystemMessage``. The standings text itself is owned by
    ``_render_standings`` so the public recap and the AI Day-turn prompts share
    one string by construction.

    ``day_round`` is REQUIRED and keyword-only (spec 020) so every call site
    consciously supplies the round the recap covers — the load-bearing decision.
    ``day_rounds`` is a count of *completed* rounds (it's not yet committed at
    the round-wrap render site), so the round must be passed in, never read from
    state here. Its in-world clock (``_round_clock``) is shown beside the day
    number; the clock is recap-only and is NOT part of the ``_render_standings``
    body fed to the AI prompts.

    PURE: inherits ``_render_standings``' purity (no mutation, no randomness, no
    hash-order-dependent ``set`` iteration) and only adds the ``cycle`` day-number
    prefix and the pure ``_round_clock`` token, so the dual-mode byte-equal smoke
    test stays green.

    Returns a PUBLIC ``SystemMessage`` (no ``additional_kwargs["private_to"]``)
    so it reaches both the human UI and every AI player's scrolling context for
    free — ``_render_context`` already folds public Moderator lines in. It
    discloses nothing hidden: living-player counts and the executed player's
    revealed side are all derivable from public play.
    """
    day = state.get("cycle", 1)
    content = DAY_ROUND_RECAP_TEMPLATE.format(
        day=day,
        clock=_round_clock(day_round),
        standings=_render_standings(state),
    )
    return SystemMessage(content=content)


def _shuffle_order(players: dict[str, PlayerState]) -> list[str]:
    """Return a shuffled list of alive-player ids."""
    ids = [p.id for p in players.values() if p.is_alive]
    random.shuffle(ids)
    return ids


def _round_complete_update(
    state: GameState,
    rounds: int,
    *,
    recap_enabled: bool,
    extra: dict | None = None,
) -> dict:
    """Build the state update for a completed Day round (spec 018).

    Centralises the three round-wrap return sites in ``day_turn`` (the
    empty/out-of-bounds defensive path, the dead-player wrap path, and the
    normal speak/vote wrap path). Bumps ``day_rounds``, reshuffles
    ``day_order`` for the next round, and resets ``day_turn_index``.

    ``extra`` may carry the caller's own ``messages`` (e.g. the normal path's
    speech ``AIMessage``) and/or ``clear_error`` keys, which are merged in.

    The end-of-round recap is appended IFF ``recap_enabled AND new_rounds <
    DAY_MAX_ROUNDS``: at the round-cap boundary (``new_rounds ==
    DAY_MAX_ROUNDS``) ``day_turn`` stays silent so ``day_close`` owns that
    single recap (the no-double-post gate). The recap is placed AFTER any
    caller-supplied ``messages`` (the ``add_messages`` reducer takes a list)
    so it renders at the end of the round, following the speech.
    """
    extra = extra or {}
    new_rounds = rounds + 1
    update: dict = {
        "day_turn_index": 0,
        "day_rounds": new_rounds,
        "day_order": _shuffle_order(state["players"]),
        **extra,
    }
    if recap_enabled and new_rounds < DAY_MAX_ROUNDS:
        prior_messages = list(extra.get("messages", []))
        # ``new_rounds`` is the just-completed 1-based round (spec 020): posted
        # only for rounds 1..5 here (the cap boundary is gated out above and
        # owned by ``day_close``), so the clock reads 9 AM … 9 PM.
        update["messages"] = [
            *prior_messages,
            render_day_round_recap(state, day_round=new_rounds),
        ]
    return update


def _alive_ids_in_roster_order(players: dict[str, PlayerState]) -> list[str]:
    """Return the alive-player ids in the original roster insertion order."""
    return [p.id for p in players.values() if p.is_alive]


def _render_context(messages: list, speaker_id: str) -> str:
    """Render the last ``_CONTEXT_WINDOW`` speaker-visible messages for prompts.

    Privacy filter (gameplay integrity): a message carrying
    ``additional_kwargs["private_to"] == other_id`` is a whisper addressed to
    a *different* player (e.g. the Mafia teammate-intro sent to each mafioso,
    or the human's private role reveal). Those must NOT enter ``speaker_id``'s
    context — otherwise a Law-abiding AI could read the Mafia roster. We keep
    public messages (no ``private_to``) and the speaker's OWN whispers; the
    latter is load-bearing because a mafioso's only record of its team is its
    own intro whisper (the Day/vote prompts never re-inject the role).

    Labels match the human UI (``ui/app.py``): a public ``SystemMessage`` is
    the Moderator's voice and is labelled ``Moderator``; the speaker's own
    private whisper is labelled ``Moderator (private)``. Player messages keep
    their ``name``.

    Windowing order: filter to speaker-visible messages FIRST, then take the
    last ``_CONTEXT_WINDOW`` — so other players' whispers never consume the
    visible-context budget and a full Day round's public speeches stay visible
    (spec 008, "Same-Round Message Visibility").
    """
    if not messages:
        return "(no prior discussion)"
    visible: list = []
    for msg in messages:
        extra = getattr(msg, "additional_kwargs", {}) or {}
        private_to = extra.get("private_to")
        if private_to and private_to != speaker_id:
            # Whisper addressed to someone else — never visible to this speaker.
            continue
        visible.append(msg)
    recent = visible[-_CONTEXT_WINDOW:]
    lines: list[str] = []
    for msg in recent:
        extra = getattr(msg, "additional_kwargs", {}) or {}
        private_to = extra.get("private_to")
        if isinstance(msg, SystemMessage):
            # Moderator voice. The speaker's own whisper is labelled to match
            # the UI's "Moderator (private)"; public lines are "Moderator".
            speaker = "Moderator (private)" if private_to else "Moderator"
        else:
            speaker = getattr(msg, "name", None) or msg.__class__.__name__
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines) if lines else "(no prior discussion)"


def _render_alive_roster(players: dict[str, PlayerState]) -> str:
    """Render 'Name: id' lines for every alive player."""
    return "\n".join(
        f"{p.name}: {p.id}" for p in players.values() if p.is_alive
    )


def _fuzzy_match_alive(
    players: dict[str, PlayerState], needle: str
) -> str | None:
    """Case-insensitive substring match over alive-player names.

    Returns the unique matching player's id, or None if zero or multiple
    alive players' names contain ``needle`` as a substring.
    """
    needle_lc = needle.strip().lower()
    if not needle_lc:
        return None
    matches = [
        p.id
        for p in players.values()
        if p.is_alive and needle_lc in p.name.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def day_open(state: GameState) -> dict:
    """Open the Day: reveal last-night's victim and seed the speaking order."""
    cycle = state.get("cycle", 1)
    players = state.get("players", {})
    kill_log = state.get("kill_log", [])

    victim_record = _find_last_night_victim(kill_log, cycle)
    if victim_record is not None:
        victim_name = victim_record["name"]
        victim = next(
            (p for p in players.values() if p.name == victim_name), None
        )
        if victim is not None:
            content = DAY_OPEN_VICTIM_REVEAL_TEMPLATE.format(
                name=victim.name,
                role_label=_role_label(victim.role),
            )
        else:
            content = DAY_OPEN_NO_VICTIM_TEMPLATE
    else:
        content = DAY_OPEN_NO_VICTIM_TEMPLATE

    order = _shuffle_order(players)

    return {
        "messages": [SystemMessage(content=content)],
        "day_order": order,
        "day_turn_index": 0,
        "day_rounds": 0,
        "day_votes_called": 0,
        "day_votes_initiated": 0,
        "active_vote": None,
        "day_turn_error": None,
        "phase": "day",
    }


def _ai_day_action(
    speaker: PlayerState,
    state: GameState,
) -> DayAction:
    """Call Sonnet for the AI's speaking turn. Returns a validated DayAction.

    The AI may return ``kind='speak'`` (with text) or ``kind='vote'`` (with
    ``target_id``). Target validation is performed by the caller: an invalid
    target triggers a single retry, then falls back to a generic speak.
    """
    players = state.get("players", {})
    roster = _render_alive_roster(players)
    context = _render_context(list(state.get("messages", [])), speaker.id)
    alive_ids = {p.id for p in players.values() if p.is_alive}

    # Role/team/win-condition grounding (spec 013 §2.5). Computed for ALL
    # actors and injected directly into the prompt — never via the scrolling
    # ``context`` whisper. ``_team_line`` is Mafia-only (a Citizen's is "").
    role_label = _role_label(speaker.role)
    win_condition = _win_condition_line(speaker.role)
    team_line = _team_line(speaker, players)

    # Persona is the voice/temperament layer atop the role-facts grounding,
    # injected ONLY into this speaker's own prompt (spec 016 §2.3 privacy
    # invariant). Composes with — does not replace — the spec-013 grounding.
    persona = _persona_block(speaker)

    llm = get_large().with_structured_output(DayAction)
    base_messages: list = [
        SystemMessage(content=DAY_SPEAK_SYSTEM),
        HumanMessage(
            content=DAY_SPEAK_USER_TEMPLATE.format(
                speaker=speaker.name,
                role_label=role_label,
                win_condition=win_condition,
                team_line=team_line,
                persona=persona,
                standings=_render_standings(state),
                roster=roster,
                context=context,
            )
        ),
    ]

    def _accept(action: object) -> DayAction | None:
        if not isinstance(action, DayAction):
            return None
        if action.kind == "speak":
            if action.text and action.text.strip():
                return action
            return None
        # kind == "vote"
        if (
            action.target_id
            and action.target_id.strip()
            and action.target_id in alive_ids
            and action.target_id != speaker.id
        ):
            return action
        return None

    try:
        first = llm.invoke(base_messages)
        accepted = _accept(first)
        if accepted is not None:
            return accepted
    except Exception:
        pass

    # Retry once with a reminder about the schema and valid targets.
    retry_messages = [
        *base_messages,
        HumanMessage(
            content=(
                "Return a valid DayAction: either kind='speak' with non-empty "
                "text, or kind='vote' with a target_id drawn from the roster "
                "(and not your own id)."
            )
        ),
    ]
    try:
        second = llm.invoke(retry_messages)
        accepted = _accept(second)
        if accepted is not None:
            return accepted
    except Exception:
        pass

    # Deterministic fallback so tests aren't flaky.
    return DayAction(kind="speak", text="I'm not sure who to trust yet.")


def _begin_vote(
    initiator_id: str,
    target_id: str,
    players: dict[str, PlayerState],
) -> ActiveVote:
    """Construct a fresh ActiveVote with the pending voter list."""
    active: ActiveVote = {
        "initiator": initiator_id,
        "target": target_id,
        "ballots": {},
        "pending": _alive_ids_in_roster_order(players),
    }
    return active


def day_turn(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
    recap_enabled: bool = True,
) -> dict:
    """Run exactly one player's Day turn, then advance bookkeeping.

    A turn may either emit a speech (AIMessage) or initiate a vote (setting
    ``active_vote``). Vote initiation does NOT consume the speaker's turn in
    the sense of producing a speech message, but the turn index does advance
    so we return to round-robin rhythm after the vote resolves.

    When the turn index wraps (end of a round), this node also reshuffles
    ``day_order`` and bumps ``day_rounds`` in the same update so that the
    next ``day_turn`` invocation starts the next round cleanly. The
    conditional edge then decides between looping, voting, or transitioning
    to ``day_close``.
    """
    players = state.get("players", {})
    order: list[str] = list(state.get("day_order", []))
    turn_index = state.get("day_turn_index", 0)
    rounds = state.get("day_rounds", 0)

    if not order or turn_index >= len(order):
        # Defensive: empty or out-of-bounds order; treat the round as complete.
        # No speech message — emits only the recap, when gated in.
        return _round_complete_update(state, rounds, recap_enabled=recap_enabled)

    player_id = order[turn_index]
    player = players.get(player_id)

    # Defensive: dead player in the queue. Skip without producing a message,
    # still advancing turn_index so we make progress.
    if player is None or not player.is_alive:
        new_turn_index = turn_index + 1
        if new_turn_index >= len(order):
            # Round wrap with no speech message — recap only, when gated in.
            return _round_complete_update(
                state, rounds, recap_enabled=recap_enabled
            )
        return {"day_turn_index": new_turn_index}

    # --------------------------------------------------------------
    # Human turn: may either speak or begin with `/vote <name>`.
    # --------------------------------------------------------------
    if player.is_human:
        # Single ``interrupt()`` per node execution (interrupt-as-first-
        # statement discipline). On an invalid ``/vote`` we do NOT call
        # ``interrupt()`` a second time inside this node — that would empty
        # ``snapshot.next`` while the second interrupt is still pending, and
        # the driver (which checks ``snapshot.next`` before interrupts) would
        # misread the pause as game-over and end the game. Instead we carry
        # the error forward in ``day_turn_error`` and return a state update
        # with no turn advance; the conditional edge loops back to a FRESH
        # ``day_turn`` execution, which surfaces the hint on its single
        # interrupt. This keeps exactly one interrupt per super-step.
        prior_error = state.get("day_turn_error")
        payload: dict = {
            "kind": "day_turn",
            "speaker_id": player.id,
            "speaker_name": player.name,
            "alive_names": [
                p.name for p in players.values() if p.is_alive
            ],
        }
        if prior_error:
            payload["error"] = prior_error

        raw = interrupt(payload)
        text = raw.strip() if isinstance(raw, str) else ""
        lowered = text.lower()
        tokens = lowered.split(maxsplit=1)
        if tokens and tokens[0] == "/vote":
            # Strict: only recognise "/vote" as a slash-command when it is
            # the whole input or followed by whitespace. Inputs like
            # "/voted", "/votefor Alice" fall through to the speech path.
            # Bare "/vote" (no target) emits a distinct usage hint.
            remainder = text[len("/vote"):].strip()
            if not remainder:
                # Re-prompt via a graph loop, not a second interrupt. Turn is
                # NOT consumed (turn_index unchanged).
                return {"day_turn_error": "Usage: /vote <name>"}
            target_id = _fuzzy_match_alive(players, remainder)
            if target_id is None:
                return {"day_turn_error": "No such player. Try again."}
            active = _begin_vote(player.id, target_id, players)
            # Do NOT advance turn_index — the turn is consumed by the
            # vote flow, but we want to resume speech rotation from the
            # same position after the vote resolves. Clear any pending error.
            human_votes_called = state.get("human_votes_called", 0) + 1
            day_votes_initiated = state.get("day_votes_initiated", 0) + 1
            if career_emitter is not None and game_id is not None:
                career_emitter.emit(
                    game_id,
                    CareerEvent(
                        kind=KIND_VOTE_INITIATED,
                        session_id=game_id,
                        initiator_is_human=True,
                    ),
                )
            return {
                "active_vote": active,
                "day_turn_error": None,
                "human_votes_called": human_votes_called,
                "day_votes_initiated": day_votes_initiated,
            }

        if not text:
            text = "(stays silent.)"

        msg = AIMessage(
            content=text,
            name=player.name,
            additional_kwargs={"speaker": player.name},
        )
        # Accepted human speech consumes the turn — clear any pending
        # re-prompt error so it doesn't resurface on the human's next turn.
        clear_error: dict = {"day_turn_error": None}
    else:
        # --------------------------------------------------------------
        # AI turn: may either speak or initiate a vote via DayAction.
        # --------------------------------------------------------------
        action = _ai_day_action(player, state)
        if action.kind == "vote":
            assert action.target_id is not None  # validated in _ai_day_action
            active = _begin_vote(player.id, action.target_id, players)
            day_votes_initiated = state.get("day_votes_initiated", 0) + 1
            if career_emitter is not None and game_id is not None:
                career_emitter.emit(
                    game_id,
                    CareerEvent(
                        kind=KIND_VOTE_INITIATED,
                        session_id=game_id,
                        initiator_is_human=False,
                    ),
                )
            return {
                "active_vote": active,
                "day_votes_initiated": day_votes_initiated,
            }
        # kind == "speak"
        assert action.text is not None
        msg = AIMessage(
            content=action.text.strip(),
            name=player.name,
            additional_kwargs={"speaker": player.name},
        )
        clear_error = {}

    new_turn_index = turn_index + 1
    if new_turn_index >= len(order):
        # Round wrap on a speaking turn: the speech message and (when gated in)
        # the recap end up in order — speech first, recap last.
        return _round_complete_update(
            state,
            rounds,
            recap_enabled=recap_enabled,
            extra={"messages": [msg], **clear_error},
        )

    return {
        "messages": [msg],
        "day_turn_index": new_turn_index,
        **clear_error,
    }


def vote_prompt(state: GameState) -> dict:
    """Announce the vote-to-execute. Reads ``active_vote`` from state."""
    active = state.get("active_vote")
    if not active:
        # Defensive: nothing to announce.
        return {}
    players = state.get("players", {})
    initiator = players.get(active["initiator"])
    target = players.get(active["target"])
    initiator_name = initiator.name if initiator else active["initiator"]
    target_name = target.name if target else active["target"]
    content = VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
        initiator=initiator_name,
        target=target_name,
    )
    return {"messages": [SystemMessage(content=content)]}


def _ai_ballot(
    voter: PlayerState,
    target: PlayerState,
    state: GameState,
) -> Ballot:
    """Ask the gameplay model for a Yes/No ballot. Conservative fallback."""
    players = state.get("players", {})
    context = _render_context(list(state.get("messages", [])), voter.id)

    # Role/team/win-condition grounding + the relationship NUDGE (spec 013
    # §2.5/§2.6), computed for ALL voters. ``_team_line`` is Mafia-only;
    # ``_ballot_relationship`` emits only "is YOU"/"is your fellow Mafioso"/""
    # so a Law-abiding voter's ballot discloses no other-player allegiance.
    role_label = _role_label(voter.role)
    win_condition = _win_condition_line(voter.role)
    team_line = _team_line(voter, players)
    relationship = _ballot_relationship(voter, target)

    llm = get_large().with_structured_output(Ballot)
    base_messages: list = [
        SystemMessage(content=AI_VOTE_SYSTEM),
        HumanMessage(
            content=AI_VOTE_USER_TEMPLATE.format(
                voter=voter.name,
                role_label=role_label,
                win_condition=win_condition,
                team_line=team_line,
                standings=_render_standings(state),
                target=target.name,
                relationship=relationship,
                context=context,
            )
        ),
    ]
    try:
        first = llm.invoke(base_messages)
        if isinstance(first, Ballot):
            return first
    except Exception:
        pass
    # Retry once.
    try:
        second = llm.invoke(base_messages)
        if isinstance(second, Ballot):
            return second
    except Exception:
        pass
    # Conservative fallback: No.
    return Ballot(yes=False)


def collect_votes(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
    """Poll ONE voter per super-step. Replay-safe like ``day_turn``.

    Reads ``active_vote["pending"][0]``, collects that voter's ballot
    (interrupting for a human, Bedrock-calling for an AI), records it, and
    pops from ``pending``. When ``pending`` empties, the conditional edge
    routes to ``resolve_vote``.
    """
    active = state.get("active_vote")
    if not active:
        return {}
    pending: list[str] = list(active.get("pending", []))
    if not pending:
        return {}

    players = state.get("players", {})
    voter_id = pending[0]
    voter = players.get(voter_id)
    target_id = active["target"]
    target = players.get(target_id)

    if voter is None or not voter.is_alive:
        # Skip dead voter (shouldn't happen mid-vote, but be defensive).
        new_active: ActiveVote = {
            "initiator": active["initiator"],
            "target": active["target"],
            "ballots": dict(active.get("ballots", {})),
            "pending": pending[1:],
        }
        return {"active_vote": new_active}

    target_name = target.name if target else target_id

    extra: dict = {}
    if voter.is_human:
        payload = {
            "kind": "vote",
            "target_id": target_id,
            "target_name": target_name,
            "voter_id": voter.id,
            "voter_name": voter.name,
            "target_role_unknown": True,
        }
        while True:
            raw = interrupt(payload)
            text = raw.strip().lower() if isinstance(raw, str) else ""
            if text in ("yes", "y"):
                yes = True
                break
            if text in ("no", "n"):
                yes = False
                break
            payload = {
                **payload,
                "error": "Answer yes or no.",
            }
        extra["human_ballots_cast"] = state.get("human_ballots_cast", 0) + 1
        if career_emitter is not None and game_id is not None:
            career_emitter.emit(
                game_id,
                CareerEvent(
                    kind=KIND_BALLOT_CAST,
                    session_id=game_id,
                    voter_is_human=True,
                ),
            )
    else:
        if target is None:
            # Target missing (shouldn't happen); conservative no.
            yes = False
        else:
            ballot = _ai_ballot(voter, target, state)
            yes = ballot.yes
        if career_emitter is not None and game_id is not None:
            career_emitter.emit(
                game_id,
                CareerEvent(
                    kind=KIND_BALLOT_CAST,
                    session_id=game_id,
                    voter_is_human=False,
                ),
            )

    vote_label = "Yes" if yes else "No"
    ballot_msg = SystemMessage(
        content=VOTE_PER_BALLOT_TEMPLATE.format(
            voter=voter.name,
            vote_label=vote_label,
        )
    )

    new_ballots: dict[str, str] = dict(active.get("ballots", {}))
    new_ballots[voter.id] = "yes" if yes else "no"
    new_active_out: ActiveVote = {
        "initiator": active["initiator"],
        "target": active["target"],
        "ballots": cast(dict, new_ballots),
        "pending": pending[1:],
    }
    return {
        "messages": [ballot_msg],
        "active_vote": new_active_out,
        **extra,
    }


def resolve_vote(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
    """Tally ballots and either execute or fail the vote.

    On execution: flip the target's ``is_alive``, append a KillRecord with
    ``cause='execution'``, emit the execution reveal. Clear ``active_vote``.
    The router will send the graph to ``day_close``.

    On failure: emit "The vote fails.", increment ``day_votes_called``,
    reshuffle ``day_order`` for a fresh speaking round, reset turn index.
    The router will loop back to ``day_turn`` unless ``day_votes_called``
    has hit the cap.
    """
    active = state.get("active_vote")
    if not active:
        return {}

    players = dict(state.get("players", {}))
    cycle = state.get("cycle", 1)
    ballots = active.get("ballots", {})
    target_id = active["target"]

    yes_count = sum(1 for v in ballots.values() if v == "yes")
    no_count = sum(1 for v in ballots.values() if v == "no")

    # Alive-at-start-of-vote is the size of the ballot set (we polled every
    # alive player exactly once — nobody dies mid-ballot in this game).
    alive_at_vote_start = yes_count + no_count

    messages: list = [
        SystemMessage(
            content=VOTE_TALLY_TEMPLATE.format(
                yes_count=yes_count,
                no_count=no_count,
            )
        )
    ]

    executed = yes_count > alive_at_vote_start / 2

    if executed:
        target = players.get(target_id)
        if target is None:
            # Shouldn't happen — defensive no-op.
            return {
                "messages": messages,
                "active_vote": None,
            }
        # Flip the player's alive flag. Only ``is_alive`` changes; every other
        # field (persona included) carries forward via ``replace``. We already
        # copied the outer ``players`` dict, so reassigning the rebuilt target
        # leaves the caller's state untouched.
        target = dataclasses.replace(target, is_alive=False)
        players[target_id] = target

        messages.append(
            SystemMessage(
                content=VOTE_EXECUTED_TEMPLATE.format(
                    name=target.name,
                    role_label=_role_label(target.role),
                )
            )
        )

        kill_record: KillRecord = {
            "cycle": cycle,
            "name": target.name,
            "cause": "execution",
            "role": target.role,
        }
        if career_emitter is not None and game_id is not None:
            career_emitter.emit(
                game_id,
                CareerEvent(
                    kind=KIND_VOTE_RESOLVED,
                    session_id=game_id,
                    was_executed=True,
                ),
            )
        return {
            "messages": messages,
            "players": players,
            "kill_log": [kill_record],
            "active_vote": None,
            "execution_count": state.get("execution_count", 0) + 1,
        }

    # Vote failed: bump counter, reshuffle, reset turn index.
    messages.append(SystemMessage(content=VOTE_FAILED_TEMPLATE))
    votes_called = state.get("day_votes_called", 0) + 1
    next_order = _shuffle_order(players)
    if career_emitter is not None and game_id is not None:
        career_emitter.emit(
            game_id,
            CareerEvent(
                kind=KIND_VOTE_RESOLVED,
                session_id=game_id,
                was_executed=False,
            ),
        )
    return {
        "messages": messages,
        "active_vote": None,
        "day_votes_called": votes_called,
        "day_order": next_order,
        "day_turn_index": 0,
    }


def day_close(state: GameState, *, recap_enabled: bool = True) -> dict:
    """Close the Day.

    When an execution just landed, the canonical ``VOTE_EXECUTED_TEMPLATE``
    line has already been posted by ``resolve_vote``; we do NOT repeat the
    "no one executed" phrasing in that case. Only the no-execution paths
    (rounds cap, failed-votes cap) emit the generic close line.

    When ``recap_enabled`` (spec 018), ``day_close`` owns the *closing* recap
    for the day-ending boundaries it covers (round-cap, vote-cap, and a
    mid-round execution that ends the Day but not the game). The recap is
    appended AFTER the close-line logic, so on the no-execution path it follows
    the "no one executed" line, and on the execution path it surfaces the
    post-execution standings + "executed today" line (``resolve_vote`` already
    posted the execution reveal, so that path's pre-recap messages are empty).

    This node is reached ONLY when the Day ends *and the game continues*: a
    winning move routes ``check_win_day`` / ``check_win_night`` to
    ``end_screen``, bypassing ``day_close`` — so the recap is never posted on a
    game-ending move (which keeps the endgame "last message" assertions green).
    No double-post: ``day_turn`` already suppresses its round-wrap recap at the
    ``DAY_MAX_ROUNDS`` boundary, so ``day_close`` owning the closing recap
    yields exactly one recap per boundary.
    """
    cycle = state.get("cycle", 1)
    kill_log = state.get("kill_log", [])
    day_rounds = state.get("day_rounds", 0)
    # Round the closing recap covers (spec 020). ``day_rounds`` counts COMPLETED
    # rounds. A round-cap close has ``day_rounds == DAY_MAX_ROUNDS`` (round 6
    # completed) → midnight. An early close (execution / vote-cap) lands
    # mid-round with ``day_rounds`` one short of the round in progress, so
    # ``day_rounds + 1`` is the round it stopped on (e.g. 3 → 3 PM), never
    # jumping ahead to midnight. ``_round_clock`` clamps both terms.
    ended_on_round = (
        day_rounds if day_rounds >= DAY_MAX_ROUNDS else day_rounds + 1
    )
    executed_this_day = _executed_this_cycle(kill_log, cycle) is not None
    messages: list = []
    if not executed_this_day:
        # The execution line already ended the Day publicly on the execution
        # path; only the no-execution paths (rounds cap, failed-votes cap) emit
        # the generic close line.
        messages.append(
            SystemMessage(content="The Day ends with no one executed.")
        )
    if recap_enabled:
        messages.append(render_day_round_recap(state, day_round=ended_on_round))
    if not messages:
        return {}
    return {"messages": messages}


def route_day_turn(state: GameState) -> str:
    """Conditional edge: loop day_turn until the round cap is hit.

    Kept for backwards compatibility with callers that don't need to know
    about votes — but the main router is now ``route_day_turn_or_vote``.
    """
    if state.get("day_rounds", 0) >= DAY_MAX_ROUNDS:
        return "day_close"
    return "day_turn"


def route_day_turn_or_vote(state: GameState) -> str:
    """Conditional edge off ``day_turn``.

    Priority order:
    1. If ``active_vote`` is set, a vote was just initiated → ``vote_prompt``.
    2. Else if round cap hit → ``day_close``.
    3. Else loop back to ``day_turn`` for the next speaker.
    """
    if state.get("active_vote"):
        return "vote_prompt"
    # A pending re-prompt error means the human's turn was rejected (bad
    # /vote) and NOT consumed — loop straight back to day_turn to re-prompt,
    # even if the round cap would otherwise close the Day. The turn index
    # was not advanced, so we have not actually completed the round.
    if state.get("day_turn_error"):
        return "day_turn"
    if state.get("day_rounds", 0) >= DAY_MAX_ROUNDS:
        return "day_close"
    return "day_turn"


def route_collect_votes(state: GameState) -> str:
    """Conditional edge off ``collect_votes``.

    If any voters remain in ``active_vote.pending``, loop back to collect
    the next ballot. Otherwise advance to ``resolve_vote``.
    """
    active = state.get("active_vote")
    if active and active.get("pending"):
        return "collect_votes"
    return "resolve_vote"


def route_after_resolve_vote(state: GameState) -> str:
    """Conditional edge off ``resolve_vote``.

    An execution this Day ends the Day; hitting the failed-vote cap also
    ends the Day; otherwise the graph returns to ``day_turn`` so speaking
    can continue.

    Execution-this-cycle is detected by scanning the kill_log tail for a
    record with ``cause='execution'`` and ``cycle == state['cycle']``. This
    is cleaner than threading a one-shot flag through state.
    """
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
