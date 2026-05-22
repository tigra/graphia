"""Driver-level regression tests for bad-``/vote`` re-prompting (spec 004).

These exercise the REAL ``graphia.driver.drive_graph`` resume pump — the
code path the running app uses — rather than hand-driving ``graph.stream``
and reading ``snapshot.tasks`` directly (which is what
``test_vote_validation.py`` does).

Background — the bug these tests pin:

    The old ``day_turn`` re-prompted a bad ``/vote`` by calling
    ``interrupt()`` a SECOND time inside the same node execution. When the
    graph re-paused on that second interrupt, LangGraph reported
    ``snapshot.next == ()`` (empty) while the pending interrupt still lived
    on ``snapshot.tasks[0].interrupts``. ``drive_graph`` checks
    ``snapshot.next`` BEFORE interrupts and treats an empty ``next`` as
    game-over — so it returned, ending the game instead of re-prompting.

    The hand-driven ``test_vote_validation.py`` tests never caught this
    because they read interrupts off ``snapshot.tasks`` and never consult
    ``snapshot.next``.

The fix restructures the human turn to use a single ``interrupt()`` per node
execution: an invalid ``/vote`` returns ``{"day_turn_error": <msg>}`` and the
conditional edge loops back to a fresh ``day_turn``, which re-prompts on its
single interrupt. With one interrupt per super-step, ``snapshot.next`` is
reliable again.

Bedrock is stubbed at the ``ChatBedrockConverse`` boundary; no real AWS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphia.config import load_config
from graphia.driver import drive_graph
from graphia.graph import build_graph, make_run_config
from graphia.llm import DayAction, Pointing
from graphia.logging import StreamTraceLogger

SEED = 0
AI_NAMES = ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"]
HUMAN_NAME = "Alice"


def _alive_law_abiding_ai_id(graph, run_config) -> str:
    players = graph.get_state(run_config).values.get("players", {})
    ids = [
        p.id
        for p in players.values()
        if p.is_alive and p.role == "law_abiding" and not p.is_human
    ]
    assert ids, "expected at least one alive AI Law-abiding"
    return ids[0]


async def _run_with_bad_first_vote(
    graph,
    run_config,
    config,
    fake,
    bad_input: str,
    expected_error: str,
) -> dict:
    """Drive the REAL ``drive_graph`` with a bad first ``/vote`` then silence.

    Returns a dict of observations the tests assert on:
      - ``reprompt_errors``: error strings the human was re-prompted with.
      - ``day_turn_prompt_count``: how many day_turn prompts were issued.
      - ``ended``: whether the graph reached END.
    """
    logger = StreamTraceLogger(config.log_file)

    # Night-1 Pointing resolves against live state (target UUIDs unknown up
    # front), mirroring test_vote_validation's _invoke_with_live_pointing.
    original_invoke = fake._invoke

    def _invoke_with_live_pointing(schema, messages):
        if schema is Pointing:
            return Pointing(
                target_id=_alive_law_abiding_ai_id(graph, run_config)
            )
        return original_invoke(schema, messages)

    fake._invoke = _invoke_with_live_pointing  # type: ignore[method-assign]

    # Plenty of AI speech so no AI turn starves before/after the human.
    fake._queues[DayAction] = [
        DayAction(kind="speak", text=f"AI speaks ({i}).") for i in range(80)
    ]
    fake._last.pop(DayAction, None)

    reprompt_errors: list[str] = []
    day_turn_prompt_count = {"n": 0}
    first_human_done = {"done": False}

    async def on_message(msg) -> None:  # pragma: no cover - not asserted
        pass

    async def on_state(update) -> None:  # pragma: no cover - not asserted
        pass

    async def request_resume(value) -> str:
        kind = value.get("kind") if isinstance(value, dict) else None
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            day_turn_prompt_count["n"] += 1
            if isinstance(value, dict) and "error" in value:
                reprompt_errors.append(value["error"])
            # The very first human day_turn prompt gets the bad /vote; every
            # subsequent prompt (including the re-prompt) gets benign silence
            # so the Day winds down naturally.
            if not first_human_done["done"]:
                first_human_done["done"] = True
                return bad_input
            return "(stays silent.)"
        if kind == "vote":
            return "n"
        return ""

    await drive_graph(
        graph,
        run_config,
        {"messages": []},
        logger,
        on_message,
        request_resume,
        config=config,
        on_state=on_state,
    )

    snapshot = graph.get_state(run_config)
    return {
        "reprompt_errors": reprompt_errors,
        "day_turn_prompt_count": day_turn_prompt_count["n"],
        "ended": snapshot.next == (),
        "expected_error": expected_error,
    }


@pytest.mark.parametrize(
    "bad_input,expected_error",
    [
        ("/vote zzz", "No such player. Try again."),
        ("/vote", "Usage: /vote <name>"),
    ],
    ids=["nonexistent_name", "bare_vote"],
)
async def test_bad_vote_through_driver_reprompts_not_ends(
    env: Path,
    fake_haiku,
    fake_sonnet,
    monkeypatch: pytest.MonkeyPatch,
    bad_input: str,
    expected_error: str,
) -> None:
    """A bad ``/vote`` on the human's first Day turn must re-prompt, not end.

    Drives the real ``drive_graph`` resume pump. The human's first day_turn
    receives ``bad_input``; the driver must re-prompt the human with
    ``expected_error`` rather than treating the re-prompt pause as game-over.

    This FAILED before the fix: ``drive_graph`` returned immediately after the
    bad-vote super-step (because the second in-node ``interrupt()`` emptied
    ``snapshot.next``), so the human was never re-prompted.
    """
    monkeypatch.setenv("GRAPHIA_SEED", str(SEED))
    fake_haiku(AI_NAMES)
    fake = fake_sonnet(day_actions=[], ballots=[], pointings=[])

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    obs = await _run_with_bad_first_vote(
        graph, run_config, config, fake, bad_input, expected_error
    )

    # The human was re-prompted at least once carrying the right error hint.
    assert expected_error in obs["reprompt_errors"], (
        f"human was NOT re-prompted with {expected_error!r} after a bad "
        f"{bad_input!r}; reprompt errors seen: {obs['reprompt_errors']!r}. "
        "The driver likely treated the re-prompt pause as game-over."
    )

    # The day continued well past the single rejected turn — proof the driver
    # kept pumping super-steps instead of returning early.
    assert obs["day_turn_prompt_count"] >= 2, (
        "expected the human to be prompted at least twice (initial bad vote "
        f"+ re-prompt); got {obs['day_turn_prompt_count']} day_turn prompts"
    )
