"""Graph node implementations grouped by phase."""

from __future__ import annotations

from graphia.nodes.day import (
    DAY_MAX_ROUNDS,
    DAY_MAX_VOTES,
    collect_votes,
    day_close,
    day_open,
    day_turn,
    resolve_vote,
    route_after_resolve_vote,
    route_collect_votes,
    route_day_turn,
    route_day_turn_or_vote,
    vote_prompt,
)
from graphia.nodes.endgame import (
    check_win_condition,
    end_screen,
    route_after_win_day,
    route_after_win_night,
)
from graphia.nodes.night import (
    first_night_mafia_intros,
    mafia_point,
    mafia_round_start,
    night_close,
    night_open,
    resolve_night_kill,
    route_after_mafia_point,
    route_after_night_open,
)
from graphia.nodes.setup import (
    assign_roles,
    collect_name,
    generate_roster,
    introduce_roster,
    reveal_role,
)

__all__ = [
    "DAY_MAX_ROUNDS",
    "DAY_MAX_VOTES",
    "assign_roles",
    "check_win_condition",
    "collect_name",
    "collect_votes",
    "day_close",
    "day_open",
    "day_turn",
    "end_screen",
    "first_night_mafia_intros",
    "generate_roster",
    "introduce_roster",
    "mafia_point",
    "mafia_round_start",
    "night_close",
    "night_open",
    "resolve_night_kill",
    "resolve_vote",
    "reveal_role",
    "route_after_mafia_point",
    "route_after_night_open",
    "route_after_resolve_vote",
    "route_after_win_day",
    "route_after_win_night",
    "route_collect_votes",
    "route_day_turn",
    "route_day_turn_or_vote",
    "vote_prompt",
]
