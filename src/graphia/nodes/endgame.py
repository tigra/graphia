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
    ENDGAME_WINNER_DRAW,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
)
from graphia.state import GameState, KillRecord, PlayerState

# Mirrors graphia.ui.app.GraphiaApp._OUTCOME_BY_WINNER. The end-of-game event
# carries the same outcome string the local-mode summary uses, so consumer
# folds are byte-identical to ``stats_store.summarize`` (spec 006 §2.x).
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
    if winner == "draw":
        return ENDGAME_WINNER_DRAW
    # Defensive: end_screen invoked without a winner set. Produce a generic
    # line rather than crashing.
    return "The game has ended."


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
