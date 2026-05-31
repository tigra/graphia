"""Spec 006 / ADR 008 — local-vs-remote career-stats pipeline equivalence.

This is the single test that locks ADR 008's central invariant: the
remote-mode pipeline (per-action ``CareerEvent`` emissions folded by
``build_summary`` + ``fold``) produces the SAME final ``CareerStats`` as
the local-mode pipeline (``summarize(_latest_state, ...)`` + ``fold``).

The Lambda's ``games_folded`` sidecar is the only field that diverges by
design: local mode never sets it (the file store doesn't deduplicate by
session id), while the remote consumer Lambda appends the session id after
a successful fold. The test compares aggregates with that field excluded
so any other drift fails loudly.

Three scenarios are swept: a Mafia-win game, a Law-abiding loss, and an
abandoned game.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from graphia.career_events import (
    KIND_BALLOT_CAST,
    KIND_GAME_ABANDONED,
    KIND_GAME_ENDED,
    KIND_GAME_STARTED,
    KIND_NIGHT_RESOLVED,
    KIND_VOTE_INITIATED,
    KIND_VOTE_RESOLVED,
    CareerEvent,
    build_summary,
)
from graphia.stats_store import CareerStats, fold, summarize


class _RolePlayer:
    """Minimal stand-in for the ``PlayerState`` ``role`` attribute access."""

    def __init__(self, role: str) -> None:
        self.role = role


# Each scenario is a tuple of:
#   - latest_state dict that ``summarize`` reads (local pipeline input)
#   - finishing outcome string passed to ``summarize``
#   - per-action event sequence ``build_summary`` aggregates (remote input)
SCENARIOS: list[tuple[str, dict, str, list[CareerEvent]]] = [
    (
        "mafia_win_human_mafia",
        {
            "players": {"H": _RolePlayer("mafia")},
            "human_role": "mafia",
            "winner": "mafia",
            "cycle": 3,
            "human_votes_called": 1,
            "human_ballots_cast": 2,
            "human_night_attempts": 2,
            "human_night_successes": 1,
            "night_victim_count": 1,
            "execution_count": 1,
        },
        "mafia_win",
        [
            CareerEvent(
                kind=KIND_GAME_STARTED, session_id="sess-A", human_role="mafia"
            ),
            # Day 1: human starts a vote, casts a ballot, vote executes.
            CareerEvent(
                kind=KIND_VOTE_INITIATED,
                session_id="sess-A",
                initiator_is_human=True,
            ),
            CareerEvent(
                kind=KIND_BALLOT_CAST, session_id="sess-A", voter_is_human=True
            ),
            CareerEvent(
                kind=KIND_BALLOT_CAST, session_id="sess-A", voter_is_human=True
            ),
            CareerEvent(
                kind=KIND_VOTE_RESOLVED, session_id="sess-A", was_executed=True
            ),
            # Night 2: human Mafia picked, success.
            CareerEvent(
                kind=KIND_NIGHT_RESOLVED,
                session_id="sess-A",
                victim_died=True,
                human_was_mafia_picker=True,
                human_picked_victim=True,
            ),
            # Night 3: human Mafia picked, miss (no death recorded).
            CareerEvent(
                kind=KIND_NIGHT_RESOLVED,
                session_id="sess-A",
                victim_died=False,
                human_was_mafia_picker=True,
                human_picked_victim=False,
            ),
            CareerEvent(
                kind=KIND_GAME_ENDED,
                session_id="sess-A",
                human_role="mafia",
                outcome="mafia_win",
                rounds=3,
            ),
        ],
    ),
    (
        "law_abiding_loss",
        {
            "players": {"H": _RolePlayer("law_abiding")},
            "human_role": "law_abiding",
            "winner": "mafia",
            "cycle": 4,
            "human_votes_called": 0,
            "human_ballots_cast": 2,
            "human_night_attempts": 0,
            "human_night_successes": 0,
            "night_victim_count": 2,
            "execution_count": 0,
        },
        "mafia_win",
        [
            CareerEvent(
                kind=KIND_GAME_STARTED,
                session_id="sess-B",
                human_role="law_abiding",
            ),
            # Two ballots cast, no vote initiated by the human, no execution.
            CareerEvent(
                kind=KIND_BALLOT_CAST, session_id="sess-B", voter_is_human=True
            ),
            CareerEvent(
                kind=KIND_BALLOT_CAST, session_id="sess-B", voter_is_human=True
            ),
            CareerEvent(
                kind=KIND_VOTE_RESOLVED,
                session_id="sess-B",
                was_executed=False,
            ),
            # Two night victims, neither picked by the human.
            CareerEvent(
                kind=KIND_NIGHT_RESOLVED,
                session_id="sess-B",
                victim_died=True,
                human_was_mafia_picker=False,
                human_picked_victim=False,
            ),
            CareerEvent(
                kind=KIND_NIGHT_RESOLVED,
                session_id="sess-B",
                victim_died=True,
                human_was_mafia_picker=False,
                human_picked_victim=False,
            ),
            CareerEvent(
                kind=KIND_GAME_ENDED,
                session_id="sess-B",
                human_role="law_abiding",
                outcome="mafia_win",
                rounds=4,
            ),
        ],
    ),
    (
        "abandoned_mafia_mid_game",
        {
            "players": {"H": _RolePlayer("mafia")},
            "human_role": "mafia",
            "winner": None,
            "cycle": 2,
            "human_votes_called": 0,
            "human_ballots_cast": 1,
            "human_night_attempts": 1,
            "human_night_successes": 1,
            "night_victim_count": 1,
            "execution_count": 0,
        },
        "abandoned",
        [
            CareerEvent(
                kind=KIND_GAME_STARTED, session_id="sess-C", human_role="mafia"
            ),
            CareerEvent(
                kind=KIND_BALLOT_CAST, session_id="sess-C", voter_is_human=True
            ),
            CareerEvent(
                kind=KIND_VOTE_RESOLVED,
                session_id="sess-C",
                was_executed=False,
            ),
            CareerEvent(
                kind=KIND_NIGHT_RESOLVED,
                session_id="sess-C",
                victim_died=True,
                human_was_mafia_picker=True,
                human_picked_victim=True,
            ),
            CareerEvent(
                kind=KIND_GAME_ABANDONED,
                session_id="sess-C",
                human_role="mafia",
                rounds_so_far=2,
            ),
        ],
    ),
]


@pytest.mark.parametrize(
    ("name", "latest_state", "outcome", "events"),
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_local_and_remote_pipelines_produce_identical_career_stats(
    name: str,
    latest_state: dict,
    outcome: str,
    events: list[CareerEvent],
) -> None:
    """``fold`` of ``summarize`` and ``fold`` of ``build_summary`` agree.

    Local pipeline: ``summarize(state, ...)`` → ``GameSummary`` → folded into
    a starting :class:`CareerStats`.

    Remote pipeline: ``build_summary(events)`` → ``GameSummary`` → folded into
    the same starting :class:`CareerStats`.

    They must agree exactly on every dimension EXCEPT ``games_folded`` (the
    Lambda's session-id idempotency sidecar), which local mode never sets.
    The remote-side aggregate is compared with that sidecar zeroed out so
    any other drift fails the test.
    """
    starting = CareerStats()

    local_summary = summarize(latest_state, human_id="H", outcome=outcome)
    local_career = fold(starting, local_summary)

    remote_summary = build_summary(events)
    remote_career_raw = fold(starting, remote_summary)
    # Strip the Lambda sidecar so the equivalence assertion focuses on the
    # statistical dimensions both pipelines actually compute.
    remote_career = replace(remote_career_raw, games_folded=[])

    # The per-game summaries must match (this is the equivalence anchor).
    assert local_summary == remote_summary, (
        f"scenario {name!r}: per-game summaries diverged.\n"
        f"local:  {local_summary!r}\n"
        f"remote: {remote_summary!r}"
    )
    # And so must the folded career aggregates.
    assert local_career == remote_career, (
        f"scenario {name!r}: folded career aggregates diverged.\n"
        f"local:  {local_career!r}\n"
        f"remote: {remote_career!r}"
    )
