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
from dataclasses import dataclass, field
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
]


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


class LocalFileStatsStore:
    """File-backed career stats store for local mode.

    The career aggregate is persisted as a single JSON object at ``path``.
    ``load`` is forward-tolerant (missing keys default to zero/empty) and
    never raises — a missing, unreadable, or unparseable file yields a
    zeroed :class:`CareerStats`.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

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
        raise NotImplementedError(
            "LocalFileStatsStore.record is implemented in a later task "
            "(depends on fold())."
        )


def make_stats_store(config: GraphiaConfig) -> StatsStore:
    """Select the career stats store implementation for ``config``.

    Local-only for now: returns a :class:`LocalFileStatsStore` over
    ``config.stats_file``. A later task adds the AgentCore long-term-memory
    branch keyed on ``config.memory_id``.
    """
    return LocalFileStatsStore(config.stats_file)


def render_greeting(stats: CareerStats) -> str:
    """Produce the launch greeting summarising the player's career.

    Returns the first-run welcome line when no games have been recorded.
    The non-empty branch is intentionally minimal here — richer formatting
    (win-rate-by-role, streaks, and the like) is a later task.
    """
    if stats.games_total == 0:
        return "Welcome — this is your first game, so there's no history yet."
    games = stats.games_total
    plural = "game" if games == 1 else "games"
    return f"Welcome back — you've played {games} {plural} so far."
