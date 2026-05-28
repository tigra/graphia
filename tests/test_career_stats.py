"""Spec 006 tests: cross-game career stats (Layer 1, local mode).

Slice 1 (scaffold) covered:

* ``render_greeting`` first-run welcome line for a zeroed aggregate.
* ``LocalFileStatsStore.load`` tolerance — a missing or corrupt JSON file
  yields a zeroed :class:`CareerStats` and never raises.
* The launch greeting reaching ``#public-log`` when an injected
  :class:`StatsStore` reports an empty career.

Slice 2 (win/loss by role; greeting + post-game panel) adds:

* ``fold`` — role splits, win-only-when-won, ``outcome_split`` per outcome,
  the ``draw`` special case (completed but not a win), accumulation, purity.
* The win-rate denominator surfaced through ``render_greeting`` — ``"—"`` for
  a role with no completed games, a real percentage otherwise.
* ``summarize`` — reading role/winner/cycle and defensive ``0`` counters.
* ``LocalFileStatsStore.record`` round-trip + accumulation, parent-dir
  creation, and no leftover ``.tmp`` file.
* The post-game career *panel* reaching ``#public-log`` after a real game
  end, plus the *greeting* of a second app over the same store path showing
  the cumulative (non-first-run) form.

Out of scope (later slices, all 0/absent now): action/night counters,
game-wide totals, average length, abandoned-game recording, and the
AgentCore remote store. No test touches real Bedrock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rich.text import Text
from textual.widgets import Input, RichLog

from graphia.llm import Ballot, DayAction, Pointing, Roster
from graphia.stats_store import (
    CareerStats,
    GameSummary,
    LocalFileStatsStore,
    fold,
    render_greeting,
    render_panel,
    summarize,
)
from graphia.ui.app import GraphiaApp

# Re-use the established RichLog flattening + polling helpers so the UI test
# matches the Slice-2 pattern for reading pane text.
from test_slice2_roster import _rich_log_text, _wait_for

# Stable identifying substring of the first-run greeting. Matched loosely so a
# wording tweak that keeps the "first game" sense doesn't break the test.
FIRST_RUN_MARKER = "first game"

# Stable substrings of the cumulative (returning-player) greeting and the
# post-game panel. Matched loosely so wording tweaks that keep the sense don't
# break the tests.
RETURNING_MARKER = "Welcome back"
PANEL_MARKER = "Career update"

AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]
HUMAN_NAME = "Alice"


def _summary(
    *,
    human_role: str,
    outcome: str,
    human_won: bool,
    rounds: int = 1,
    votes_called: int = 0,
    ballots_cast: int = 0,
) -> GameSummary:
    """Build a flat :class:`GameSummary` with zeroed night counters.

    Slice 2 only folds outcome/role/rounds (action counters pinned ``0``);
    Slice 3 adds the human day-action counters, so ``votes_called`` /
    ``ballots_cast`` are now overridable while the night fields stay ``0``.
    """
    return GameSummary(
        human_role=human_role,
        outcome=outcome,
        human_won=human_won,
        rounds=rounds,
        votes_called=votes_called,
        ballots_cast=ballots_cast,
        night_attempts=0,
        night_successes=0,
        night_victims=0,
        day_executions=0,
    )


def test_render_greeting_first_run() -> None:
    """A zeroed career yields the first-run welcome line."""
    greeting = render_greeting(CareerStats())
    assert FIRST_RUN_MARKER in greeting


def test_load_missing_file_returns_zeroed(tmp_path: Path) -> None:
    """``load`` on a non-existent path returns a zeroed aggregate, no raise."""
    store = LocalFileStatsStore(tmp_path / "does-not-exist.json")

    stats = store.load()

    assert stats == CareerStats()
    assert stats.games_total == 0


def test_load_corrupt_file_returns_zeroed(tmp_path: Path) -> None:
    """``load`` on unparseable JSON returns a zeroed aggregate, no raise."""
    path = tmp_path / "career.json"
    path.write_text("{bad json", encoding="utf-8")
    store = LocalFileStatsStore(path)

    stats = store.load()

    assert stats == CareerStats()
    assert stats.games_total == 0


# --------------------------------------------------------------------------
# fold — outcome/role dimensions, the draw special case, accumulation, purity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("human_role", "outcome"),
    [
        ("mafia", "mafia_win"),
        ("law_abiding", "law_abiding_win"),
    ],
)
def test_fold_counts_win_in_correct_role(human_role: str, outcome: str) -> None:
    """A win folds into ``games_by_role`` AND ``wins_by_role`` for that role only."""
    summary = _summary(human_role=human_role, outcome=outcome, human_won=True)

    result = fold(CareerStats(), summary)

    other = "law_abiding" if human_role == "mafia" else "mafia"
    assert result.role_games(human_role) == 1
    assert result.role_wins(human_role) == 1
    assert result.role_games(other) == 0
    assert result.role_wins(other) == 0


def test_fold_loss_counts_game_but_not_win() -> None:
    """A loss increments the role's games but leaves its win count at zero."""
    summary = _summary(
        human_role="mafia", outcome="law_abiding_win", human_won=False
    )

    result = fold(CareerStats(), summary)

    assert result.role_games("mafia") == 1
    assert result.role_wins("mafia") == 0


def test_fold_mafia_and_law_abiding_tracked_separately() -> None:
    """Two games in different roles split cleanly across the role maps."""
    after_one = fold(
        CareerStats(),
        _summary(human_role="mafia", outcome="mafia_win", human_won=True),
    )
    after_two = fold(
        after_one,
        _summary(
            human_role="law_abiding",
            outcome="law_abiding_win",
            human_won=True,
        ),
    )

    assert after_two.role_games("mafia") == 1
    assert after_two.role_games("law_abiding") == 1
    assert after_two.role_wins("mafia") == 1
    assert after_two.role_wins("law_abiding") == 1
    assert after_two.games_total == 2


@pytest.mark.parametrize(
    "outcome",
    ["mafia_win", "law_abiding_win", "draw"],
)
def test_fold_increments_outcome_split(outcome: str) -> None:
    """Each fold bumps the ``outcome_split`` entry for that outcome."""
    summary = _summary(
        human_role="law_abiding", outcome=outcome, human_won=False
    )

    result = fold(CareerStats(), summary)

    assert result.outcome_count(outcome) == 1


def test_fold_draw_is_completed_but_not_a_win() -> None:
    """A draw is a completed game (rounds counted) but never a win."""
    summary = _summary(
        human_role="law_abiding",
        outcome="draw",
        human_won=False,
        rounds=4,
    )

    result = fold(CareerStats(), summary)

    assert result.completed_games == 1
    assert result.sum_rounds_completed == 4
    assert result.outcome_count("draw") == 1
    assert result.games_by_role.get("law_abiding") == 1
    # The defining assertion: a draw must not register as a win in any role.
    assert result.wins_by_role == {}
    assert result.role_wins("law_abiding") == 0


def test_fold_accumulates_across_multiple_games() -> None:
    """Three folds accumulate totals, completed games, and summed rounds."""
    stats = CareerStats()
    stats = fold(
        stats,
        _summary(
            human_role="mafia", outcome="mafia_win", human_won=True, rounds=3
        ),
    )
    stats = fold(
        stats,
        _summary(
            human_role="mafia",
            outcome="law_abiding_win",
            human_won=False,
            rounds=5,
        ),
    )
    stats = fold(
        stats,
        _summary(
            human_role="law_abiding",
            outcome="law_abiding_win",
            human_won=True,
            rounds=2,
        ),
    )

    assert stats.games_total == 3
    assert stats.completed_games == 3
    assert stats.sum_rounds_completed == 10
    assert stats.role_games("mafia") == 2
    assert stats.role_wins("mafia") == 1
    assert stats.role_games("law_abiding") == 1
    assert stats.role_wins("law_abiding") == 1


def test_fold_does_not_mutate_input() -> None:
    """``fold`` is pure: the input aggregate and its dict fields are untouched."""
    original = CareerStats()
    games_before = dict(original.games_by_role)
    wins_before = dict(original.wins_by_role)
    outcome_before = dict(original.outcome_split)

    fold(
        original,
        _summary(human_role="mafia", outcome="mafia_win", human_won=True),
    )

    assert original == CareerStats()
    assert original.games_total == 0
    # The folded result must not alias / mutate the input's dict fields.
    assert original.games_by_role == games_before
    assert original.wins_by_role == wins_before
    assert original.outcome_split == outcome_before


# --------------------------------------------------------------------------
# Win-rate denominator surfaced through render_greeting
# --------------------------------------------------------------------------


def test_greeting_role_with_no_completed_games_renders_dash() -> None:
    """A role never played shows the ``"—"`` placeholder, not ``0%``."""
    # One game as Mafia; Law-abiding never played.
    stats = fold(
        CareerStats(),
        _summary(human_role="mafia", outcome="mafia_win", human_won=True),
    )

    greeting = render_greeting(stats)

    assert "—" in greeting
    # The role that *was* played should show a real percentage, not a dash,
    # for that role's segment.
    mafia_segment = greeting.split("Mafia:", 1)[1].split(";", 1)[0]
    assert "—" not in mafia_segment


def test_greeting_one_win_two_games_renders_fifty_percent() -> None:
    """1 win across 2 completed games in a role renders 50%."""
    stats = CareerStats()
    stats = fold(
        stats,
        _summary(human_role="mafia", outcome="mafia_win", human_won=True),
    )
    stats = fold(
        stats,
        _summary(
            human_role="mafia", outcome="law_abiding_win", human_won=False
        ),
    )

    greeting = render_greeting(stats)

    assert "50%" in greeting


# --------------------------------------------------------------------------
# summarize — read role / winner / cycle; defensive 0 counters
# --------------------------------------------------------------------------


class _RolePlayer:
    """Minimal ``PlayerState``-like stub exposing the ``.role`` ``summarize`` reads."""

    def __init__(self, role: str) -> None:
        self.role = role


@pytest.mark.parametrize(
    ("role", "winner", "outcome", "expect_won"),
    [
        ("mafia", "mafia", "mafia_win", True),
        ("law_abiding", "law_abiding", "law_abiding_win", True),
        ("mafia", "law_abiding", "law_abiding_win", False),
        ("law_abiding", "draw", "draw", False),
    ],
)
def test_summarize_reads_role_winner_and_won_flag(
    role: str, winner: str, outcome: str, expect_won: bool
) -> None:
    """``human_won`` is ``winner == role``; role and outcome flow straight through."""
    human_id = "p-human"
    latest_state = {
        "players": {human_id: _RolePlayer(role)},
        "winner": winner,
        "cycle": 3,
    }

    summary = summarize(latest_state, human_id, outcome)

    assert summary.human_role == role
    assert summary.outcome == outcome
    assert summary.human_won is expect_won
    assert summary.rounds == 3


def test_summarize_absent_counters_default_to_zero() -> None:
    """Missing action/night/cycle keys read as ``0`` (forward-compatible)."""
    human_id = "p-human"
    latest_state = {
        "players": {human_id: _RolePlayer("law_abiding")},
        "winner": "law_abiding",
        # No cycle, no action/night counters present.
    }

    summary = summarize(latest_state, human_id, "law_abiding_win")

    assert summary.rounds == 0
    assert summary.votes_called == 0
    assert summary.ballots_cast == 0
    assert summary.night_attempts == 0
    assert summary.night_successes == 0
    assert summary.night_victims == 0
    assert summary.day_executions == 0


# --------------------------------------------------------------------------
# Slice 3 — human day-action counters (votes called / ballots cast)
#
# Pure-function coverage only here (no graph): fold accumulates the lifetime
# vote/ballot totals, summarize reads the per-game GameState keys, and
# render_panel surfaces the per-game deltas. The graph-driven proof that the
# day_turn / collect_votes nodes actually populate these GameState keys lives
# in test_slice7_vote.py alongside the rest of the vote-flow drive harness.
# --------------------------------------------------------------------------


def test_fold_accumulates_votes_and_ballots_single_game() -> None:
    """A single fold carries the game's vote/ballot counters into the career."""
    summary = _summary(
        human_role="law_abiding",
        outcome="law_abiding_win",
        human_won=True,
        votes_called=2,
        ballots_cast=3,
    )

    result = fold(CareerStats(), summary)

    assert result.votes_called == 2
    assert result.ballots_cast == 3


def test_fold_accumulates_votes_and_ballots_across_games() -> None:
    """Vote/ballot totals sum across multiple folds (lifetime accumulation)."""
    stats = CareerStats()
    stats = fold(
        stats,
        _summary(
            human_role="mafia",
            outcome="mafia_win",
            human_won=True,
            votes_called=1,
            ballots_cast=4,
        ),
    )
    stats = fold(
        stats,
        _summary(
            human_role="law_abiding",
            outcome="law_abiding_win",
            human_won=True,
            votes_called=3,
            ballots_cast=2,
        ),
    )

    assert stats.votes_called == 1 + 3
    assert stats.ballots_cast == 4 + 2


def test_fold_zero_counters_leave_totals_untouched() -> None:
    """A game with no votes/ballots adds nothing to the lifetime totals."""
    base = fold(
        CareerStats(),
        _summary(
            human_role="mafia",
            outcome="mafia_win",
            human_won=True,
            votes_called=5,
            ballots_cast=6,
        ),
    )

    after = fold(
        base,
        _summary(
            human_role="mafia",
            outcome="law_abiding_win",
            human_won=False,
            votes_called=0,
            ballots_cast=0,
        ),
    )

    assert after.votes_called == 5
    assert after.ballots_cast == 6


def test_summarize_reads_human_vote_and_ballot_counters() -> None:
    """``summarize`` lifts ``human_votes_called`` / ``human_ballots_cast``."""
    human_id = "p-human"
    latest_state = {
        "players": {human_id: _RolePlayer("law_abiding")},
        "winner": "law_abiding",
        "cycle": 2,
        "human_votes_called": 4,
        "human_ballots_cast": 7,
    }

    summary = summarize(latest_state, human_id, "law_abiding_win")

    assert summary.votes_called == 4
    assert summary.ballots_cast == 7


def test_render_panel_shows_vote_and_ballot_deltas() -> None:
    """The post-game panel reports this game's vote/ballot deltas + totals."""
    last = _summary(
        human_role="law_abiding",
        outcome="law_abiding_win",
        human_won=True,
        votes_called=2,
        ballots_cast=3,
    )
    # The aggregate is the *updated* career (already folds ``last``), with a
    # prior history so the career totals exceed this game's deltas.
    stats = fold(
        CareerStats(votes_called=1, ballots_cast=5),
        last,
    )

    panel = render_panel(stats, last)

    # Per-game delta lines: this game's contribution beside the career total.
    assert "+2 this game" in panel
    assert "+3 this game" in panel
    # The career totals (prior 1/5 plus this game's 2/3).
    assert "career total: 3" in panel
    assert "career total: 8" in panel


def test_render_panel_singular_plural_vote_ballot_labels() -> None:
    """One vote/ballot uses singular noun labels; two or more pluralize them.

    The "You (career)" vote/ballot lines pluralize the noun on the count: a
    single vote/ballot reads ``day-vote called`` / ``day-ballot cast``, while
    two or more read ``day-votes called`` / ``day-ballots cast``.
    """
    # Singular: exactly one vote / one ballot keeps the noun singular.
    singular = _summary(
        human_role="mafia",
        outcome="mafia_win",
        human_won=True,
        votes_called=1,
        ballots_cast=1,
    )
    panel_singular = render_panel(fold(CareerStats(), singular), singular)

    assert "You (career) — day-vote called: +1 this game" in panel_singular
    assert "You (career) — day-ballot cast: +1 this game" in panel_singular

    # Plural: two votes / two ballots pluralize the noun.
    plural = _summary(
        human_role="mafia",
        outcome="mafia_win",
        human_won=True,
        votes_called=2,
        ballots_cast=2,
    )
    panel_plural = render_panel(fold(CareerStats(), plural), plural)

    assert "You (career) — day-votes called: +2 this game" in panel_plural
    assert "You (career) — day-ballots cast: +2 this game" in panel_plural


# --------------------------------------------------------------------------
# LocalFileStatsStore.record — round-trip, accumulation, parent dir, no .tmp
# --------------------------------------------------------------------------


def test_record_then_load_round_trips_the_folded_aggregate(
    tmp_path: Path,
) -> None:
    """``record`` returns the folded aggregate and ``load`` reads back the same."""
    path = tmp_path / "career.json"
    store = LocalFileStatsStore(path)
    summary = _summary(
        human_role="mafia", outcome="mafia_win", human_won=True, rounds=4
    )

    recorded = store.record(summary)
    loaded = store.load()

    assert recorded == loaded
    assert recorded == fold(CareerStats(), summary)
    assert loaded.games_total == 1
    assert loaded.role_wins("mafia") == 1
    assert loaded.sum_rounds_completed == 4


def test_record_accumulates_across_two_calls(tmp_path: Path) -> None:
    """Two successive ``record`` calls accumulate in the persisted aggregate."""
    path = tmp_path / "career.json"
    store = LocalFileStatsStore(path)

    store.record(
        _summary(human_role="mafia", outcome="mafia_win", human_won=True)
    )
    second = store.record(
        _summary(
            human_role="law_abiding",
            outcome="law_abiding_win",
            human_won=True,
        )
    )

    assert second.games_total == 2
    assert second.role_wins("mafia") == 1
    assert second.role_wins("law_abiding") == 1
    assert store.load() == second


def test_record_creates_missing_parent_dir(tmp_path: Path) -> None:
    """``record`` creates the parent directory if it does not yet exist."""
    path = tmp_path / "nested" / "dir" / "career.json"
    assert not path.parent.exists()
    store = LocalFileStatsStore(path)

    store.record(
        _summary(human_role="mafia", outcome="mafia_win", human_won=True)
    )

    assert path.exists()
    assert path.parent.is_dir()


def test_record_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    """The atomic temp file is renamed away — no ``.tmp`` residue remains."""
    path = tmp_path / "career.json"
    store = LocalFileStatsStore(path)

    store.record(
        _summary(human_role="mafia", outcome="mafia_win", human_won=True)
    )

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"unexpected temp files left behind: {leftovers!r}"
    # Only the canonical file should exist.
    assert [p.name for p in tmp_path.iterdir()] == ["career.json"]


class _FakeStatsStore:
    """In-memory ``StatsStore`` reporting an empty (first-run) career.

    ``load`` returns a zeroed :class:`CareerStats`; ``record`` is part of the
    Protocol surface but unused in Slice 1, so it is a placeholder that fails
    loudly if ever reached from this test.
    """

    def load(self) -> CareerStats:
        return CareerStats()

    def record(self, summary: GameSummary) -> CareerStats:  # pragma: no cover
        raise AssertionError("record() must not be called in Slice 1 tests")


async def test_ui_greeting_appears_on_launch(
    env: Path, fake_haiku, fake_sonnet, monkeypatch
) -> None:
    """The first-run greeting is written to ``#public-log`` at launch.

    The injected fake store keeps this off the filesystem. ``fake_haiku`` /
    ``fake_sonnet`` and the Law-abiding role pin guard against teardown hangs:
    the greeting writes before ``build_graph``, but the driver then advances
    the graph, and an unstubbed LLM call would strand a boto3 retry thread
    past ``app.exit()``.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    fake_haiku(["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"])
    fake_sonnet(
        pointings=[Pointing(target_id="placeholder")],
        day_actions=[DayAction(kind="speak", text="hello")],
    )
    app = GraphiaApp(stats_store=_FakeStatsStore())
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#public-log", RichLog)

        await _wait_for(
            pilot,
            lambda: FIRST_RUN_MARKER in _rich_log_text(log),
            timeout=5.0,
        )

        assert FIRST_RUN_MARKER in _rich_log_text(log)

        await pilot.press("q")
    assert app.is_running is False


# --------------------------------------------------------------------------
# UI end-to-end: a real game end writes the career PANEL; a second app over
# the same store path greets with the cumulative (non-first-run) form.
# --------------------------------------------------------------------------


async def _drive_law_abiding_loss_to_end(
    app: GraphiaApp, fake, pilot, *, timeout: float = 30.0
) -> str:
    """Drive a pinned-Law-abiding game to a forced Mafia win and return the log.

    Mirrors ``test_slice8_endgame.test_end_screen_visible_in_ui``: AIs always
    *speak* (no VoteModal ever pops), each Night the AI Mafia kills a
    Law-abiding AI, so after the third Night the Mafia reach parity and
    ``check_win_night`` ends the game with the human (Law-abiding) on the
    losing side — guaranteeing a recorded ``mafia_win`` with ``human_won`` False.
    """
    await pilot.pause()
    for _ in range(100):
        if app._graph is not None and app._run_config is not None:
            break
        await pilot.pause(0.05)
    assert app._graph is not None, "graph never initialised"

    graph = app._graph
    rc = app._run_config
    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            state = graph.get_state(rc).values
            law_ids = [
                p.id
                for p in state.get("players", {}).values()
                if p.is_alive and p.role == "law_abiding" and not p.is_human
            ]
            if not law_ids:
                return Pointing(target_id="missing")
            return Pointing(target_id=law_ids[0])
        if schema is DayAction:
            return DayAction(kind="speak", text="I'm watching carefully.")
        if schema is Ballot:
            return Ballot(yes=False)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    # Enter the human name once the input is enabled.
    for _ in range(100):
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001
            prompt = None  # type: ignore[assignment]
        if prompt is not None and prompt.disabled is False:
            break
        await pilot.pause(0.05)
    await pilot.press(*HUMAN_NAME)
    await pilot.press("enter")

    public_log = app.query_one("#public-log", RichLog)

    def _log_text() -> str:
        return _rich_log_text(public_log)

    # Interleave passing the human's day_turn with polling for game end.
    for _ in range(120):
        if "Game over." in _log_text():
            break
        try:
            prompt = app.query_one("#player-input", Input)
        except Exception:  # noqa: BLE001
            prompt = None  # type: ignore[assignment]
        if prompt is not None and prompt.disabled is False:
            await pilot.press(".")
            await pilot.press("enter")
        else:
            await pilot.pause(0.2)

    if "Game over." not in _log_text():
        await _wait_for(
            pilot, lambda: "Game over." in _log_text(), timeout=timeout
        )
    return _log_text()


async def test_ui_panel_written_then_second_app_greets_cumulative(
    env: Path, tmp_path: Path, fake_haiku, fake_sonnet, monkeypatch
) -> None:
    """A finished game writes the career panel; a fresh app then greets back.

    Two app lifetimes share one ``LocalFileStatsStore`` path. The first runs a
    real game to its end and must (a) emit the post-game *panel* to
    ``#public-log``, and (b) persist the folded aggregate. The second app, over
    the same path, must greet with the cumulative ("Welcome back") form rather
    than the first-run welcome — proving ``record`` reached the store and
    ``load`` picked it up.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    # Two rosters: one per game (the second app boots a fresh graph and calls
    # Haiku again before we read its greeting and exit).
    fake_haiku(outputs=[Roster(names=AI_NAMES), Roster(names=AI_NAMES)])
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    store_path = tmp_path / "shared" / "career.json"

    # --- First app: drive a real game to its end -----------------------
    app1 = GraphiaApp(stats_store=LocalFileStatsStore(store_path))
    async with app1.run_test() as pilot:
        rendered = await _drive_law_abiding_loss_to_end(app1, fake, pilot)

        assert "Game over." in rendered
        assert PANEL_MARKER in rendered, (
            f"career panel marker {PANEL_MARKER!r} missing from public log:\n"
            f"{rendered}"
        )
        assert app1._game_over is True
        await pilot.press("x")
    assert app1.is_running is False

    # The folded aggregate must have been persisted to the shared path.
    persisted = LocalFileStatsStore(store_path).load()
    assert persisted.games_total == 1, (
        f"expected 1 recorded game, got {persisted!r}"
    )

    # --- Second app: same store path → cumulative greeting --------------
    app2 = GraphiaApp(stats_store=LocalFileStatsStore(store_path))
    async with app2.run_test() as pilot:
        await pilot.pause()
        log = app2.query_one("#public-log", RichLog)

        await _wait_for(
            pilot,
            lambda: RETURNING_MARKER in _rich_log_text(log),
            timeout=10.0,
        )
        greeting_text = _rich_log_text(log)
        assert RETURNING_MARKER in greeting_text
        assert FIRST_RUN_MARKER not in greeting_text, (
            "second launch should not show the first-run welcome:\n"
            f"{greeting_text}"
        )

        await pilot.press("q")
    assert app2.is_running is False
