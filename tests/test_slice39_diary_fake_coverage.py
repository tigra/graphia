"""Spec 039 (Per-AI Private Diaries) — Slice 1 prerequisite: the fake covers
the new ``Diary`` call site, and a flag-on run writes the SCRIPTED entry.

Why this file exists, and why it is the first thing in the spec
---------------------------------------------------------------

``day_diary`` (Slice 1) mirrors ``nodes/day.py:_ai_reflect``: it wraps its
``get_large().with_structured_output(Diary).invoke(...)`` call in a broad
``try/except`` and substitutes a deterministic ``_DIARY_FALLBACK`` on any
failure, so one model hiccup can never blank the channel.

That defensive posture has a nasty interaction with the test double.
``FakeLargeUnified.with_structured_output`` raises
``AssertionError("... no scripted queue for schema ...")`` for a schema it does
not know. **The node's ``except`` swallows that assertion**, writes the
fallback, and returns a perfectly well-formed delta. A suite with no ``Diary``
queue would therefore be *green while measuring the fallback everywhere* — every
later assertion in spec 039 (the interleave cursor, the window, the clamp, the
transcript tag, privacy) would be made against a constant string the model never
produced. Tech-spec 039 §3 names this the spec's hard prerequisite.

The detector is ``calls_by_schema[Diary]``, not the entry text
--------------------------------------------------------------

"An entry was produced" does not distinguish the two worlds — the fallback path
produces one too. Two independent signals are asserted together, and only the
pair is conclusive:

1. ``fake.calls_by_schema[Diary] >= 1`` — the scripted queue was actually
   *reached*. A swallowed unknown-schema ``AssertionError`` never gets as far as
   ``_invoke``, so this counter stays at 0. This is the assertion that fails if
   the ``Diary`` queue is ever removed from ``tests/conftest.py``.
2. Every recorded entry equals the scripted sentinel, and none is
   ``_DIARY_FALLBACK`` — the value the node kept is the one the fake handed it,
   not something it substituted after eating an exception.

Written before the code it tests
--------------------------------

``graphia.nodes.day.day_diary`` and the ``private_diaries`` state channel land
in Slice 1's later tasks. The full-game regression test below is therefore
RED-BY-DESIGN until they do. It resolves every not-yet-existing symbol **at call
time** (see :func:`_require_diary_node`), never with a module-level
``from graphia.nodes.day import day_diary``: a module-level ``ImportError`` is a
*collection* error, and pytest aborts the whole session on one — a single
red-by-design file would take the other ~1550 tests down with it. This is the
idiom ``tests/test_transcript_highlight.py`` established for spec 038's
tokenizer, for exactly the same reason.

The two ``FakeLargeUnified`` tests above it are green today: they pin the
conftest change itself, independent of any production code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.types import Command

import graphia.nodes.day as day_nodes
from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import Ballot, DayAction, Diary, Pointing

HUMAN_NAME = "Alice"
AI_NAMES = ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"]

# A sentinel no production string could collide with: it is not a sentence any
# prompt, fallback or placeholder in the codebase would ever emit, so an exact
# match proves the value travelled from the scripted queue into state untouched.
SCRIPTED_ENTRY = (
    "SCRIPTED-DIARY-SENTINEL: today the baker contradicted himself twice."
)


# ==========================================================================
# Deferred symbol resolution — call time, never import time
# ==========================================================================


def _require_diary_node() -> Callable[..., dict]:
    """Return ``graphia.nodes.day.day_diary``, resolved when the test runs.

    Deliberately **not** a module-level ``from graphia.nodes.day import
    day_diary`` (this repo's normal idiom — see
    ``tests/test_slice28_private_thoughts.py``). This test is written *before*
    the node exists, and a module-level ``ImportError`` is a **collection**
    error: pytest aborts the entire session on one, so a red-by-design file
    would take every other test in the suite with it. Resolving the attribute
    here keeps the missing node a loud failure of exactly this test. A later
    rename still breaks it, so the rename-detection property of the direct
    import is preserved.
    """
    node = getattr(day_nodes, "day_diary", None)
    if node is None:
        pytest.fail(
            "graphia.nodes.day.day_diary does not exist. Spec 039 Slice 1 "
            "(task 4) must provide the before-Night diary node, wired "
            "day_close -> day_diary -> night_open, writing a "
            "`private_diaries` delta keyed per player. Until it lands this "
            "regression test is red BY DESIGN — that is what makes every "
            "other spec-039 assertion trustworthy."
        )
    return node


def _require_diary_fallback() -> str:
    """Return ``graphia.nodes.day._DIARY_FALLBACK``, resolved at call time.

    Same collection-error reasoning as :func:`_require_diary_node`. The
    fallback constant is the *thing this test exists to rule out*, so it is read
    from production rather than duplicated as a literal here — a later reword of
    the fallback must not silently disarm the check.
    """
    fallback = getattr(day_nodes, "_DIARY_FALLBACK", None)
    if fallback is None:
        pytest.fail(
            "graphia.nodes.day._DIARY_FALLBACK does not exist. Spec 039 "
            "Slice 1 (task 4) must provide the deterministic fallback note "
            "(the analogue of `_REFLECTION_FALLBACK`) written when the diary "
            "model call fails or yields an empty entry."
        )
    if not isinstance(fallback, str) or not fallback.strip():
        pytest.fail(
            "graphia.nodes.day._DIARY_FALLBACK must be a non-empty string; "
            f"got {fallback!r}."
        )
    return fallback


def _diary_texts(state: dict[str, Any]) -> list[str]:
    """Flatten the ``private_diaries`` channel into a list of entry texts.

    ``DiaryRecord`` (Slice 1, task 2) is a ``TypedDict`` carrying ``day``,
    ``thoughts_before`` and ``text``. A bare string is accepted too so a wobble
    in that shape surfaces as a *shape* failure rather than masking the finding
    this test is actually about (fallback vs scripted).
    """
    channel = state.get("private_diaries")
    if channel is None:
        pytest.fail(
            "final game state has no `private_diaries` channel. Spec 039 "
            "Slice 1 (task 2) must add it to `graphia.state.GameState` with "
            "the `_merge_private_diaries` reducer."
        )
    assert isinstance(channel, dict), (
        f"`private_diaries` must be a dict keyed by player id; "
        f"got {type(channel).__name__}: {channel!r}"
    )
    texts: list[str] = []
    for player_id, records in channel.items():
        assert isinstance(records, list), (
            f"`private_diaries[{player_id!r}]` must be a list of DiaryRecord; "
            f"got {type(records).__name__}: {records!r}"
        )
        for record in records:
            if isinstance(record, str):
                texts.append(record)
            elif isinstance(record, dict) and isinstance(
                record.get("text"), str
            ):
                texts.append(record["text"])
            else:
                pytest.fail(
                    f"`private_diaries[{player_id!r}]` holds {record!r}, which "
                    "is neither a string nor a DiaryRecord with a string "
                    "`text` field."
                )
    return texts


# ==========================================================================
# The conftest change itself — green today, no production code involved
# ==========================================================================


def test_fake_large_serves_a_scripted_diary_through_the_day_call_site(
    fake_large,
) -> None:
    """``fake_large(diaries=[...])`` satisfies a ``Diary`` binding, and counts it.

    Exercised through ``graphia.nodes.day.get_large`` — the binding
    ``day_diary`` will read (the node lives in ``nodes/day.py`` precisely so
    ``safe_llm``'s existing patch target covers it and no new one is needed).
    """
    fake = fake_large(diaries=[Diary(entry=SCRIPTED_ENTRY)])

    bound = day_nodes.get_large().with_structured_output(Diary)
    result = bound.invoke([])

    assert isinstance(result, Diary)
    assert result.entry == SCRIPTED_ENTRY
    assert fake.calls_by_schema[Diary] == 1

    # Replays its last value once drained, like every other queue — so one
    # scripted entry serves every surviving AI on every Day of a full game.
    assert bound.invoke([]).entry == SCRIPTED_ENTRY
    assert fake.calls_by_schema[Diary] == 2


def test_fake_large_unsupported_schema_message_lists_diary(fake_large) -> None:
    """The unknown-schema ``AssertionError`` advertises ``Diary`` as supported.

    That message is the only signpost a developer gets when a new call site
    binds a schema the fake does not know, so it must stay in step with
    ``_queues``.
    """
    fake = fake_large()

    class _UnknownSchema:
        pass

    with pytest.raises(AssertionError) as excinfo:
        fake.with_structured_output(_UnknownSchema)

    message = str(excinfo.value)
    assert "no scripted queue" in message
    for schema_name in (
        "DayAction",
        "Ballot",
        "Pointing",
        "Persona",
        "Reflection",
        "Diary",
    ):
        assert schema_name in message, (
            f"the supported-schema message must name {schema_name}; got: "
            f"{message}"
        )


# ==========================================================================
# The regression test — RED BY DESIGN until Slice 1's node lands
# ==========================================================================


def _collect_interrupt(graph, run_config) -> dict[str, Any] | None:
    snapshot = graph.get_state(run_config)
    for task in snapshot.tasks:
        for interrupt_obj in task.interrupts or ():
            return interrupt_obj.value
    return None


def _drive(graph, run_config, payload) -> None:
    """Stream the graph with ``payload`` until the next pause.

    Mirrors ``tests/test_slice8_endgame.py:_drive`` — a bounded
    ``recursion_limit`` so a runaway loop fails fast instead of burning minutes.
    """
    bounded = dict(run_config)
    bounded.setdefault("recursion_limit", 200)
    for _ in graph.stream(payload, bounded, stream_mode="updates"):
        pass


def _advance_until(
    graph,
    run_config,
    *,
    stop: Callable[[], bool],
    interrupt_responder: Callable[[dict[str, Any]], Any],
    budget: int = 300,
) -> None:
    """Drive the graph one super-step at a time until ``stop()`` is True.

    Mirrors ``tests/test_slice8_endgame.py:_advance_until``.
    """
    for _ in range(budget):
        if stop():
            return
        snapshot = graph.get_state(run_config)
        if not snapshot.next:
            return
        interrupt_value = _collect_interrupt(graph, run_config)
        if interrupt_value is None:
            _drive(graph, run_config, None)
            continue
        _drive(graph, run_config, Command(resume=interrupt_responder(interrupt_value)))


def test_flag_on_full_game_writes_the_scripted_diary_not_the_fallback(
    env: Path,
    fake_small,
    fake_large,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag-on full game records the SCRIPTED diary entry, never the fallback.

    The trajectory is the well-trodden Mafia-parity scenario from
    ``tests/test_slice8_endgame.py:test_mafia_wins_when_parity_reached``: the
    human is pinned Mafia via ``GRAPHIA_ROLE`` (never a magic seed — ADR 006),
    AI Mafia kill one Law-abiding each Night, AIs only ever speak so no Day
    vote opens, and the game ends at parity. Days 1 and 2 therefore both close
    into a Night, so ``day_diary`` fans out at least twice.

    The two conclusive assertions are described at the top of this module: the
    ``Diary`` queue was reached (``calls_by_schema``), and what reached state is
    the scripted sentinel rather than ``_DIARY_FALLBACK``.
    """
    # Resolve the not-yet-existing production symbols FIRST, so the failure
    # names the missing node instead of surfacing as a confusing KeyError three
    # minutes into a driven game.
    _require_diary_node()
    fallback = _require_diary_fallback()

    # Explicit rather than relying on the default-on flag, so a future default
    # flip cannot silently turn this regression test into a no-op.
    monkeypatch.setenv("GRAPHIA_PRIVATE_DIARIES", "1")
    monkeypatch.setenv("GRAPHIA_ROLE", "mafia")
    fake_small(AI_NAMES)

    fake = fake_large(
        day_actions=[],
        ballots=[],
        pointings=[],
        diaries=[Diary(entry=SCRIPTED_ENTRY)],
    )

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)

    _drive(graph, run_config, {"messages": []})
    assert _collect_interrupt(graph, run_config) == {"kind": "name"}

    # Live dispatcher: Pointing/DayAction/Ballot resolve against live state
    # (uuid ids only exist once roles are assigned). Diary deliberately falls
    # through to ``original_invoke`` so it goes through the real scripted queue
    # and increments ``calls_by_schema`` — the whole point of this test.
    original_invoke = fake._invoke

    def _invoke_live(schema, messages):
        if schema is Pointing:
            state = graph.get_state(run_config).values
            law_ids = [
                p.id
                for p in state.get("players", {}).values()
                if p.is_alive and p.role == "law_abiding" and not p.is_human
            ]
            return Pointing(target_id=law_ids[0] if law_ids else "missing")
        if schema is DayAction:
            return DayAction(kind="speak", text="Nothing suspicious here.")
        if schema is Ballot:
            return Ballot(yes=False)
        return original_invoke(schema, messages)

    fake._invoke = _invoke_live  # type: ignore[method-assign]

    def _respond(interrupt_value: dict[str, Any]) -> str:
        kind = interrupt_value.get("kind")
        if kind == "name":
            return HUMAN_NAME
        if kind == "day_turn":
            return "..."
        if kind == "vote":
            return "no"
        if kind == "point":
            options = interrupt_value.get("options") or []
            return options[0]["id"] if options else ""
        raise AssertionError(f"Unexpected interrupt kind: {kind!r}")

    _advance_until(
        graph,
        run_config,
        stop=lambda: graph.get_state(run_config).values.get("winner")
        is not None,
        interrupt_responder=_respond,
    )

    state = graph.get_state(run_config).values
    assert state.get("winner") is not None, (
        "the scripted trajectory never reached a winner; the run below is "
        "inconclusive about diaries"
    )

    # (1) The scripted queue was REACHED. A swallowed unknown-schema
    #     AssertionError never gets to ``_invoke``, so this counter would be 0
    #     while every other assertion in the file still passed.
    assert fake.calls_by_schema[Diary] >= 1, (
        "the Diary queue was never invoked. Either `day_diary` never ran, or "
        "`with_structured_output(Diary)` raised the unknown-schema "
        "AssertionError and the node's try/except swallowed it into "
        "_DIARY_FALLBACK. This is the silent failure tech-spec 039 §3 names: "
        "restore the `Diary` queue in tests/conftest.py."
    )

    # (2) What landed in state is the scripted value, not a substitute.
    texts = _diary_texts(state)
    assert texts, (
        "a flag-on full game wrote no diary entries at all; expected at least "
        "one per surviving AI for each of Days 1 and 2"
    )
    assert fallback not in texts, (
        f"_DIARY_FALLBACK ({fallback!r}) reached state even though the Diary "
        f"queue was scripted — the model call is failing and being swallowed. "
        f"Entries were: {texts!r}"
    )
    assert set(texts) == {SCRIPTED_ENTRY}, (
        "every diary entry should be the scripted sentinel; got "
        f"{sorted(set(texts))!r}"
    )
