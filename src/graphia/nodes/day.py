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

import random
from typing import cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from graphia.llm import Ballot, DayAction, get_sonnet
from graphia.prompts import (
    AI_VOTE_SYSTEM,
    AI_VOTE_USER_TEMPLATE,
    DAY_OPEN_NO_VICTIM_TEMPLATE,
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
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
_CONTEXT_WINDOW = 10


def _role_label(role: str) -> str:
    return "Mafia" if role == "mafia" else "Law-abiding Citizen"


def _find_last_night_victim(
    kill_log: list[KillRecord], cycle: int
) -> KillRecord | None:
    """Return the most recent night-cause kill for ``cycle``, or None."""
    for record in reversed(kill_log):
        if record.get("cause") == "night" and record.get("cycle") == cycle:
            return record
    return None


def _shuffle_order(players: dict[str, PlayerState]) -> list[str]:
    """Return a shuffled list of alive-player ids."""
    ids = [p.id for p in players.values() if p.is_alive]
    random.shuffle(ids)
    return ids


def _alive_ids_in_roster_order(players: dict[str, PlayerState]) -> list[str]:
    """Return the alive-player ids in the original roster insertion order."""
    return [p.id for p in players.values() if p.is_alive]


def _render_context(messages: list) -> str:
    """Render the last ``_CONTEXT_WINDOW`` messages compactly for prompts."""
    if not messages:
        return "(no prior discussion)"
    recent = messages[-_CONTEXT_WINDOW:]
    lines: list[str] = []
    for msg in recent:
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
    context = _render_context(list(state.get("messages", [])))
    alive_ids = {p.id for p in players.values() if p.is_alive}

    llm = get_sonnet().with_structured_output(DayAction)
    base_messages: list = [
        SystemMessage(content=DAY_SPEAK_SYSTEM),
        HumanMessage(
            content=DAY_SPEAK_USER_TEMPLATE.format(
                speaker=speaker.name,
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


def day_turn(state: GameState) -> dict:
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
        new_rounds = rounds + 1
        next_order = _shuffle_order(players)
        return {
            "day_turn_index": 0,
            "day_rounds": new_rounds,
            "day_order": next_order,
        }

    player_id = order[turn_index]
    player = players.get(player_id)

    # Defensive: dead player in the queue. Skip without producing a message,
    # still advancing turn_index so we make progress.
    if player is None or not player.is_alive:
        new_turn_index = turn_index + 1
        if new_turn_index >= len(order):
            new_rounds = rounds + 1
            next_order = _shuffle_order(players)
            return {
                "day_turn_index": 0,
                "day_rounds": new_rounds,
                "day_order": next_order,
            }
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
            return {
                "active_vote": active,
                "day_turn_error": None,
                "human_votes_called": human_votes_called,
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
            return {"active_vote": active}
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
        new_rounds = rounds + 1
        next_order = _shuffle_order(players)
        return {
            "messages": [msg],
            "day_turn_index": 0,
            "day_rounds": new_rounds,
            "day_order": next_order,
            **clear_error,
        }

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
    """Ask Sonnet for a Yes/No ballot. Conservative fallback on failure."""
    context = _render_context(list(state.get("messages", [])))
    llm = get_sonnet().with_structured_output(Ballot)
    base_messages: list = [
        SystemMessage(content=AI_VOTE_SYSTEM),
        HumanMessage(
            content=AI_VOTE_USER_TEMPLATE.format(
                voter=voter.name,
                target=target.name,
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


def collect_votes(state: GameState) -> dict:
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
    else:
        if target is None:
            # Target missing (shouldn't happen); conservative no.
            yes = False
        else:
            ballot = _ai_ballot(voter, target, state)
            yes = ballot.yes

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


def resolve_vote(state: GameState) -> dict:
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
        # Flip the player's alive flag via a fresh dataclass-like dict.
        # PlayerState is a dataclass; mutate in place since we already copied
        # the outer dict.
        target.is_alive = False
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
    return {
        "messages": messages,
        "active_vote": None,
        "day_votes_called": votes_called,
        "day_order": next_order,
        "day_turn_index": 0,
    }


def day_close(state: GameState) -> dict:
    """Close the Day.

    When an execution just landed, the canonical ``VOTE_EXECUTED_TEMPLATE``
    line has already been posted by ``resolve_vote``; we do NOT repeat the
    "no one executed" phrasing in that case. Only the no-execution paths
    (rounds cap, failed-votes cap) emit the generic close line.
    """
    cycle = state.get("cycle", 1)
    kill_log = state.get("kill_log", [])
    executed_this_day = any(
        rec.get("cause") == "execution" and rec.get("cycle") == cycle
        for rec in kill_log
    )
    if executed_this_day:
        # The execution line already ended the Day publicly; no extra line.
        return {}
    return {
        "messages": [
            SystemMessage(content="The Day ends with no one executed.")
        ],
    }


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
