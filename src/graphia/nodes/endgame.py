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
  It also sets ``phase="end"``. After this node, the graph reaches END.
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from graphia.career_events import (
    KIND_GAME_ENDED,
    CareerEvent,
    CareerEventEmitter,
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
)
from graphia.state import GameState, KillRecord, PlayerState

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
