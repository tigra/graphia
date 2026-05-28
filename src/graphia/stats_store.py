"""Cross-game career stats store: data models, Protocol, and greeting.

Spec 006 §Career Stats / ADR 007. The career is a persistent rolling
aggregate that accumulates across game sessions: a pre-game greeting
summarising the player's history, and (a later task) a post-game panel of
deltas. Per ADR 007 this is **Layer 1 only** — exact integer counters, no
LLM/semantic memory.

The module mirrors the dual-mode store shape of :mod:`graphia.diary_store`:
flat, primitive, frozen data models plus a :class:`typing.Protocol` so the
rest of the engine stays mode-agnostic. The concrete local / AgentCore
long-term implementations, the factory, and the fold/summarise/render-panel
helpers are deliberately *not* here — they land in later tasks. This module
is the scaffold: the two data models, the store Protocol, and the launch
greeting.

``GameSummary`` is one finished game's contribution (the per-game delta);
``CareerStats`` is the persisted rolling aggregate, with a zeroed default so
a first run or missing data yields an all-zero aggregate.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from graphia.config import GraphiaConfig

logger = logging.getLogger(__name__)

__all__ = [
    "GameSummary",
    "CareerStats",
    "StatsStore",
    "LocalFileStatsStore",
    "make_stats_store",
    "render_greeting",
    "render_panel",
    "fold",
    "summarize",
]

_ROLE_LABELS: dict[str, str] = {"mafia": "Mafia", "law_abiding": "Law-abiding"}


@dataclass(frozen=True, slots=True)
class GameSummary:
    """One finished game's contribution to the career aggregate.

    Flat primitive fields so the value folds cleanly into a
    :class:`CareerStats` and serialises without nested shapes. ``human_role``
    is ``"mafia"`` or ``"law_abiding"``; ``outcome`` is one of
    ``"law_abiding_win"``, ``"mafia_win"``, ``"draw"``, or ``"abandoned"``.
    """

    human_role: str
    outcome: str
    human_won: bool
    rounds: int
    votes_called: int
    ballots_cast: int
    night_attempts: int
    night_successes: int
    night_victims: int
    day_executions: int


@dataclass(frozen=True, slots=True)
class CareerStats:
    """Persisted rolling aggregate over every recorded game.

    The default ``CareerStats()`` is a valid all-zero aggregate, used on a
    first run or when persisted data is missing. Dict fields are keyed by
    role (``"mafia"`` / ``"law_abiding"``) or by outcome
    (``"law_abiding_win"`` / ``"mafia_win"`` / ``"draw"`` / ``"abandoned"``);
    missing keys read as ``0`` via the accessors below.
    """

    games_total: int = 0
    games_by_role: dict[str, int] = field(default_factory=dict)
    wins_by_role: dict[str, int] = field(default_factory=dict)
    abandoned_by_role: dict[str, int] = field(default_factory=dict)
    outcome_split: dict[str, int] = field(default_factory=dict)
    night_attempts: int = 0
    night_successes: int = 0
    votes_called: int = 0
    ballots_cast: int = 0
    total_day_executions: int = 0
    total_night_victims: int = 0
    completed_games: int = 0
    sum_rounds_completed: int = 0

    def role_games(self, role: str) -> int:
        """Games played in ``role`` (``0`` for an unseen role)."""
        return self.games_by_role.get(role, 0)

    def role_wins(self, role: str) -> int:
        """Wins in ``role`` (``0`` for an unseen role)."""
        return self.wins_by_role.get(role, 0)

    def role_abandoned(self, role: str) -> int:
        """Abandoned games in ``role`` (``0`` for an unseen role)."""
        return self.abandoned_by_role.get(role, 0)

    def outcome_count(self, outcome: str) -> int:
        """Number of games ending in ``outcome`` (``0`` if never seen)."""
        return self.outcome_split.get(outcome, 0)


def fold(aggregate: CareerStats, summary: GameSummary) -> CareerStats:
    """Fold one finished game into the career aggregate, purely.

    Returns a NEW :class:`CareerStats` — the input is never mutated and its
    dict fields are copied before update. The role/outcome dimensions, the
    human's lifetime day-action counters (``votes_called`` / ``ballots_cast``)
    and night-kill counters (``night_attempts`` / ``night_successes``), and the
    game-wide totals (``total_day_executions`` / ``total_night_victims``) all
    accumulate for *every* recorded game — including an abandoned one, since
    actions the player completed and events that occurred before the quit still
    count (spec §2.4, §2.5).

    An abandoned game (``summary.outcome == "abandoned"``) diverges only in what
    it does *not* fold: it never increments ``wins_by_role`` (it is neither a
    win nor a loss), and it is excluded from ``completed_games`` /
    ``sum_rounds_completed`` (a quit has no meaningful final length), so it
    drops out of both the win-rate denominator (``role_games - role_abandoned``)
    and the average-game-length average (spec §2.7). Instead it bumps
    ``abandoned_by_role`` for the role the player held. A ``draw`` is a completed
    game (counted in ``completed_games`` / ``sum_rounds_completed``) but not a
    win. Average game length is *derived* at render time, not stored here.
    """
    is_abandoned = summary.outcome == "abandoned"

    games_by_role = dict(aggregate.games_by_role)
    wins_by_role = dict(aggregate.wins_by_role)
    abandoned_by_role = dict(aggregate.abandoned_by_role)
    outcome_split = dict(aggregate.outcome_split)

    games_by_role[summary.human_role] = games_by_role.get(summary.human_role, 0) + 1
    outcome_split[summary.outcome] = outcome_split.get(summary.outcome, 0) + 1
    if is_abandoned:
        abandoned_by_role[summary.human_role] = (
            abandoned_by_role.get(summary.human_role, 0) + 1
        )
    elif summary.human_won:
        wins_by_role[summary.human_role] = wins_by_role.get(summary.human_role, 0) + 1

    return replace(
        aggregate,
        games_total=aggregate.games_total + 1,
        games_by_role=games_by_role,
        wins_by_role=wins_by_role,
        abandoned_by_role=abandoned_by_role,
        outcome_split=outcome_split,
        votes_called=aggregate.votes_called + summary.votes_called,
        ballots_cast=aggregate.ballots_cast + summary.ballots_cast,
        night_attempts=aggregate.night_attempts + summary.night_attempts,
        night_successes=aggregate.night_successes + summary.night_successes,
        total_day_executions=aggregate.total_day_executions + summary.day_executions,
        total_night_victims=aggregate.total_night_victims + summary.night_victims,
        completed_games=aggregate.completed_games + (0 if is_abandoned else 1),
        sum_rounds_completed=(
            aggregate.sum_rounds_completed + (0 if is_abandoned else summary.rounds)
        ),
    )


def summarize(latest_state: dict, human_id: str, outcome: str) -> GameSummary:
    """Build the per-game :class:`GameSummary` from the final graph state.

    ``human_role`` and the win flag come from the human's ``PlayerState`` and
    the game ``winner`` (both faction strings, so equality is meaningful);
    ``rounds`` is the ``cycle`` counter. The action/night counters are read
    defensively with a ``0`` default so this stays forward-compatible — the
    ``GameState`` fields they map to are added by later slices, and reading
    them now keeps ``summarize`` re-edit-free when they land.
    """
    human_role = latest_state["players"][human_id].role
    return GameSummary(
        human_role=human_role,
        outcome=outcome,
        human_won=(latest_state.get("winner") == human_role),
        rounds=latest_state.get("cycle", 0),
        votes_called=latest_state.get("human_votes_called", 0),
        ballots_cast=latest_state.get("human_ballots_cast", 0),
        night_attempts=latest_state.get("human_night_attempts", 0),
        night_successes=latest_state.get("human_night_successes", 0),
        night_victims=latest_state.get("night_victim_count", 0),
        day_executions=latest_state.get("execution_count", 0),
    )


class StatsStore(Protocol):
    """Cross-game career stats surface; see module docstring.

    One game session reads the career at launch (``load``) and folds its
    finished game into the aggregate at end (``record``). Implementations
    are mode-specific (local file / AgentCore long-term memory) and land in
    later tasks.
    """

    def load(self) -> CareerStats:
        """Return the current career aggregate, zeroed if none persisted."""
        ...

    def record(self, summary: GameSummary) -> CareerStats:
        """Fold ``summary`` into the career and return the updated aggregate."""
        ...


def _int_field(raw: Any, key: str) -> int:
    """Read ``key`` from a parsed JSON object as an int, defaulting to ``0``."""
    value = raw.get(key, 0)
    return value if isinstance(value, int) else 0


def _str_int_dict(raw: Any, key: str) -> dict[str, int]:
    """Read ``key`` as a ``{str: int}`` map, dropping any malformed entries."""
    value = raw.get(key)
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, int)}


def _career_from_json(raw: Any) -> CareerStats:
    """Parse a JSON object into a ``CareerStats``, tolerating missing keys.

    Forward-tolerant: any field absent from an older persisted file reads as
    ``0`` / ``{}``, and unknown extra keys are ignored. A non-object payload
    yields a zeroed aggregate.
    """
    if not isinstance(raw, dict):
        return CareerStats()
    return CareerStats(
        games_total=_int_field(raw, "games_total"),
        games_by_role=_str_int_dict(raw, "games_by_role"),
        wins_by_role=_str_int_dict(raw, "wins_by_role"),
        abandoned_by_role=_str_int_dict(raw, "abandoned_by_role"),
        outcome_split=_str_int_dict(raw, "outcome_split"),
        night_attempts=_int_field(raw, "night_attempts"),
        night_successes=_int_field(raw, "night_successes"),
        votes_called=_int_field(raw, "votes_called"),
        ballots_cast=_int_field(raw, "ballots_cast"),
        total_day_executions=_int_field(raw, "total_day_executions"),
        total_night_victims=_int_field(raw, "total_night_victims"),
        completed_games=_int_field(raw, "completed_games"),
        sum_rounds_completed=_int_field(raw, "sum_rounds_completed"),
    )


def _career_to_json(stats: CareerStats) -> dict[str, Any]:
    """Serialise a ``CareerStats`` to a plain JSON object.

    The inverse of :func:`_career_from_json`: every field round-trips by the
    same key, so ``_career_from_json(_career_to_json(s)) == s``. Dict fields
    are copied so the returned payload never aliases the aggregate's maps.
    """
    return {
        "games_total": stats.games_total,
        "games_by_role": dict(stats.games_by_role),
        "wins_by_role": dict(stats.wins_by_role),
        "abandoned_by_role": dict(stats.abandoned_by_role),
        "outcome_split": dict(stats.outcome_split),
        "night_attempts": stats.night_attempts,
        "night_successes": stats.night_successes,
        "votes_called": stats.votes_called,
        "ballots_cast": stats.ballots_cast,
        "total_day_executions": stats.total_day_executions,
        "total_night_victims": stats.total_night_victims,
        "completed_games": stats.completed_games,
        "sum_rounds_completed": stats.sum_rounds_completed,
    }


class LocalFileStatsStore:
    """File-backed career stats store for local mode.

    The career aggregate is persisted as a single JSON object at ``path``.
    ``load`` is forward-tolerant (missing keys default to zero/empty) and
    never raises — a missing, unreadable, or unparseable file yields a
    zeroed :class:`CareerStats`.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> CareerStats:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.info("No career stats file at %s; starting fresh.", self._path)
            return CareerStats()
        except OSError:
            logger.warning(
                "Could not read career stats file %s; starting fresh.",
                self._path,
                exc_info=True,
            )
            return CareerStats()
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            logger.warning(
                "Career stats file %s is unparseable; starting fresh.",
                self._path,
                exc_info=True,
            )
            return CareerStats()
        return _career_from_json(raw)

    def record(self, summary: GameSummary) -> CareerStats:
        """Fold ``summary`` into the persisted career and return the new total.

        Read-modify-write under a lock so concurrent records don't interleave.
        The write is atomic: the new JSON lands in a sibling temp file, then
        :func:`os.replace` swaps it into place — a crash mid-write leaves the
        previous file intact. The parent dir is created if missing.
        """
        with self._lock:
            new = fold(self.load(), summary)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(_career_to_json(new), indent=2)
            tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
            return new


def make_stats_store(config: GraphiaConfig) -> StatsStore:
    """Select the career stats store implementation for ``config``.

    Local-only for now: returns a :class:`LocalFileStatsStore` over
    ``config.stats_file``. A later task adds the AgentCore long-term-memory
    branch keyed on ``config.memory_id``.
    """
    return LocalFileStatsStore(config.stats_file)


def _win_rate(stats: CareerStats, role: str) -> str:
    """Win rate in ``role`` as a percentage, or ``"—"`` if none completed.

    The denominator is completed games in the role
    (``role_games - role_abandoned``); a zero denominator means the player has
    no win/loss-resolved game in that role yet, which reads as ``"—"`` rather
    than a misleading ``0%`` (spec §2.4).
    """
    completed = stats.role_games(role) - stats.role_abandoned(role)
    if completed <= 0:
        return "—"
    return f"{round(100 * stats.role_wins(role) / completed)}%"


def _avg_game_length(stats: CareerStats) -> str:
    """Average rounds over completed games, or ``"—"`` if none (spec §2.5).

    Derived from ``sum_rounds_completed`` / ``completed_games`` (abandoned
    games never enter either total, so they are excluded automatically).
    """
    if stats.completed_games <= 0:
        return "—"
    return f"{stats.sum_rounds_completed / stats.completed_games:.1f}"


def render_greeting(stats: CareerStats) -> str:
    """Produce the launch greeting summarising the player's career.

    Returns the first-run welcome line when no games have been recorded;
    otherwise a one-paragraph cumulative summary. The "you" sentence covers
    the player's personal numbers — total games played, win rate by role (as
    Mafia / as Law-abiding), night kills attempted vs. successful, and
    day-votes initiated; a separate "Across all games" sentence carries the
    game-wide totals (day executions, night victims, average game length) so
    the player can tell personal figures from world-wide ones (spec §2.2,
    §2.4, §2.5). A role with no completed games shows ``"—"`` rather than
    ``0%``.
    """
    if stats.games_total == 0:
        return "Welcome — this is your first game, so there's no history yet."
    games = stats.games_total
    plural = "game" if games == 1 else "games"
    mafia = _win_rate(stats, "mafia")
    law = _win_rate(stats, "law_abiding")
    votes = stats.votes_called
    votes_plural = "vote" if votes == 1 else "votes"
    return (
        f"Welcome back — you've played {games} {plural} so far. "
        f"Win rate as Mafia: {mafia}; as Law-abiding: {law}. "
        f"Your night kills: {stats.night_successes} successful "
        f"of {stats.night_attempts} attempted. "
        f"You've initiated {votes} day-{votes_plural}. "
        f"Across all games: {stats.total_day_executions} day executions, "
        f"{stats.total_night_victims} night victims, "
        f"average game length {_avg_game_length(stats)} rounds."
    )


def render_panel(stats: CareerStats, last: GameSummary) -> str:
    """Produce the post-game career panel: this game's delta plus the totals.

    Shown after the Moderator recap on a win/loss. ``stats`` is the *updated*
    aggregate (i.e. already includes ``last``); the panel reports the role the
    human played and the outcome of the just-finished game, then the new
    cumulative totals with the contribution this game made marked as a delta.
    "You (career)" lines carry the player's personal counters — win rate by
    role, day-votes called / ballots cast, and night kills attempted vs.
    successful; "All games (career)" lines carry the game-wide totals — day
    executions, night victims, and average game length — so personal and
    world-wide figures stay clearly separable (spec §2.3, §2.4, §2.5). Each
    counter shows this game's delta beside the career total.
    """
    role_label = _ROLE_LABELS.get(last.human_role, last.human_role)
    result = "won" if last.human_won else "did not win"
    games = stats.games_total
    plural = "game" if games == 1 else "games"
    mafia = _win_rate(stats, "mafia")
    law = _win_rate(stats, "law_abiding")
    votes_plural = "vote" if last.votes_called == 1 else "votes"
    ballots_plural = "ballot" if last.ballots_cast == 1 else "ballots"
    return (
        "Career update — "
        f"This game: you played as {role_label} and {result}.\n"
        f"Games played (career): {games} {plural} (+1 this game).\n"
        f"You (career) — win rate as Mafia: {mafia}; as Law-abiding: {law}.\n"
        f"You (career) — day-{votes_plural} called: +{last.votes_called} this game "
        f"(career total: {stats.votes_called}).\n"
        f"You (career) — day-{ballots_plural} cast: +{last.ballots_cast} this game "
        f"(career total: {stats.ballots_cast}).\n"
        f"You (career) — night kills: +{last.night_successes} successful / "
        f"+{last.night_attempts} attempted this game "
        f"(career total: {stats.night_successes}/{stats.night_attempts}).\n"
        f"All games (career) — day executions: +{last.day_executions} this game "
        f"(career total: {stats.total_day_executions}).\n"
        f"All games (career) — night victims: +{last.night_victims} this game "
        f"(career total: {stats.total_night_victims}).\n"
        f"All games (career) — average game length: {_avg_game_length(stats)} rounds."
    )
