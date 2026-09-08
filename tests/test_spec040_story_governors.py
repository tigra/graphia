"""Spec 040 (The Moderator's Closing Story), Task 3.5 — THE TWO SIZE GOVERNORS
as config, threaded through both graph builders.

WHAT THIS FILE OWNS. The PLUMBING of ``story_context_window`` /
``story_token_budget``, from the environment to the one function that consumes
them:

    GRAPHIA_STORY_* -> load_config() -> build_graph()/build_runtime_graph()
    -> _assemble_graph()'s ``partial`` on the ``moderator_story`` node
    -> moderator_story() -> _ai_story() -> nodes.day:_render_context()

WHAT IT DOES NOT OWN. ``tests/test_lineup_config.py`` §7 owns the config surface
itself (``_parse_count``, the two bounds checks, the shipped defaults —
including the ONE assertion in the suite that writes 400 / 12000 down), and
``tests/test_spec040_story_assembly.py`` owns what the two numbers DO to the
assembled prompt once they arrive (the count slice, the private-first
subtraction, the oldest-first trim). Neither is re-asserted here. The split is
spec 025's own: its config surface is in ``test_lineup_config.py`` and its
threading in ``tests/test_slice_day_context_window.py``.

WHY THE END-TO-END TEST EXISTS AND WHY IT IS THE POINT OF THE FILE. A config
test that asserts ``cfg.story_token_budget == 12000`` is VACUOUS on its own — it
passes against a dataclass field that no graph builder reads and no node
consumes, which is exactly the defect this task could most easily ship (the
governors already worked as module constants, so every prompt-level test stays
green whether or not the config value is ever bound). So
:func:`test_the_configured_governors_reach_render_context` walks the whole chain
with the env vars set to values that appear nowhere else, and asserts they
arrive at ``_render_context``'s ``window`` / ``token_budget`` keywords. Everything
above it in this file is a narrower witness for one seam of that chain, kept
because a failure in the narrow one localises the break.

THE TWO GOVERNORS ARE NOT INDEPENDENT, which shapes one assertion below.
``_render_context`` slices by COUNT first (``recent = visible[-window:]``) and
applies the token trim afterwards, so the window is the OUTER bound and the
budget can only cut further. The end-to-end test therefore asserts the two
keywords SEPARATELY at ``_render_context``'s boundary rather than asserting a
rendered line count for the budget — a budget assertion made through the
rendered output would be entangled with the window's slice.

NO FLAG IS THREADED, AND THAT IS A RECORDED DECISION rather than an omission
(§2.12): the closing story is post-game, changes no decision, no prompt read
during play and no metric, so ADR 011's display-only exemption applies and there
is no ``GRAPHIA_STORY`` on/off flag, no ``_assemble_graph`` flag parameter and no
ledger arm. These two are provider-capability knobs — Ollama's context, not
cost, is the binding constraint — which is a different category from an ablation
arm and the reason they ARE config while the feature has no flag.

MUTATION-VERIFIED (Task 3.5, 2026-09-08). Each mutation was applied to the
production code for real, the suite measured, and the change reverted. The
table is in the task's report; the two that this file alone catches are recorded
here because they are the ones a reader will doubt:

* ``graph.py`` binding ``context_window`` / ``context_token_budget`` (the Day's
  150 / 20000) into ``moderator_story`` instead of the story pair — the whole
  point of the task, and invisible to every prompt-level test because the
  node's own keyword DEFAULTS are the right numbers. Caught here.
* ``build_runtime_graph`` dropping the two parameters and letting
  ``_assemble_graph``'s defaults stand — the anti-drift gap, silent in local
  mode by construction. Caught here.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import SystemMessage

import graphia.nodes.endgame as endgame
from graphia.config import (
    _DEFAULT_STORY_CONTEXT_WINDOW,
    _DEFAULT_STORY_TOKEN_BUDGET,
    load_config,
)
from graphia.graph import build_graph
from graphia.llm import Story
from graphia.runtime.graph_builder import build_runtime_graph

# Values that appear nowhere else in the suite, so an assertion that sees one of
# them cannot be satisfied by a default, by the Day's pair, or by another test's
# fixture leaking through. Deliberately NOT round numbers for the same reason.
_WINDOW = 37
_BUDGET = 6111


# ==========================================================================
# 1 — build_graph reads the config fields (the local-mode seam)
# ==========================================================================


def test_build_graph_passes_the_story_governors_from_the_config(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``build_graph`` hands ``config.story_*`` to ``_assemble_graph``.

    The narrow witness for the local-mode seam: a ``build_graph`` that
    constructed the graph without these two kwargs would leave
    ``_assemble_graph``'s own literal defaults in place, so the operator's
    ``GRAPHIA_STORY_*`` would be parsed, stored on the config, and then
    silently ignored — a defect no prompt-level test can see, because those
    defaults are the correct numbers.
    """
    monkeypatch.setenv("GRAPHIA_STORY_CONTEXT_WINDOW", str(_WINDOW))
    monkeypatch.setenv("GRAPHIA_STORY_TOKEN_BUDGET", str(_BUDGET))
    config = load_config()

    with patch("graphia.graph._assemble_graph") as mock_assemble:
        build_graph(config)

    assert mock_assemble.call_count == 1
    kwargs = mock_assemble.call_args.kwargs
    assert kwargs["story_context_window"] == _WINDOW
    assert kwargs["story_token_budget"] == _BUDGET


# ==========================================================================
# 2 — build_runtime_graph threads them too (the anti-drift seam)
# ==========================================================================


def test_build_runtime_graph_threads_the_story_governors(tmp_path: Path) -> None:
    """The named anti-drift requirement: BOTH builders must thread them.

    Mirrors ``test_build_runtime_graph_threads_context_window`` (spec 025) and
    the spec-018/019/023/034/039 equivalents. Without this the deployed Runtime
    assembles its closing story from a different amount of material than local
    mode does, and nothing in local mode can notice.
    """
    with patch("graphia.runtime.graph_builder._assemble_graph") as mock_assemble:
        build_runtime_graph(
            "thread-story",
            tmp_path,
            story_context_window=_WINDOW,
            story_token_budget=_BUDGET,
        )

    assert mock_assemble.call_count == 1
    kwargs = mock_assemble.call_args.kwargs
    assert kwargs["story_context_window"] == _WINDOW
    assert kwargs["story_token_budget"] == _BUDGET


def test_build_runtime_graph_story_defaults_match_the_config_defaults(
    tmp_path: Path,
) -> None:
    """The runtime builder's defaults match the config's — no silent drift.

    A defaulted parameter that disagreed with the config default would make
    remote mode differ from local mode for every operator who never sets the
    env var, which is all of them.
    """
    with patch("graphia.runtime.graph_builder._assemble_graph") as mock_assemble:
        build_runtime_graph("thread-story-default", tmp_path)

    kwargs = mock_assemble.call_args.kwargs
    assert kwargs["story_context_window"] == _DEFAULT_STORY_CONTEXT_WINDOW
    assert kwargs["story_token_budget"] == _DEFAULT_STORY_TOKEN_BUDGET


def test_assemble_graph_story_defaults_match_the_config_defaults() -> None:
    """``_assemble_graph``'s own literal defaults match the config's.

    Both builders pass the pair explicitly, so these defaults are only reached
    by direct callers (tests, probes) — which is exactly why a drift here would
    be invisible until a test built a graph whose story read a different amount
    of material than the game does.
    """
    from graphia.graph import _assemble_graph

    params = inspect.signature(_assemble_graph).parameters
    assert params["story_context_window"].default == _DEFAULT_STORY_CONTEXT_WINDOW
    assert params["story_token_budget"].default == _DEFAULT_STORY_TOKEN_BUDGET


def test_the_runtime_entrypoint_forwards_both_governors_off_the_config() -> None:
    """``runtime/__main__`` passes ``_config.story_*`` into the runtime builder.

    THE LAST SEAM, and the one with no runtime test available: the AgentCore
    entrypoint builds its graph inside a module-scoped handler against a
    module-scoped ``_config``, so it cannot be called from the offline suite
    without standing up the whole Runtime import surface. Checked by AST over
    the entrypoint's own source instead — the ``build_runtime_graph(...)`` call
    is located and its keyword arguments read — so this asserts the WIRING
    (which keyword is fed from which config attribute) rather than matching a
    string. A ``build_runtime_graph`` that grew the parameters while the
    entrypoint kept passing nothing would leave the deployed Runtime on the
    defaults with local mode honouring the setting, which is the exact
    divergence ADR 001 forbids.
    """
    import graphia.runtime.__main__ as runtime_main

    tree = ast.parse(inspect.getsource(runtime_main))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_runtime_graph"
    ]
    assert len(calls) == 1, (
        f"expected exactly one build_runtime_graph call site, found {len(calls)}"
    )

    wiring = {
        kw.arg: kw.value.attr
        for kw in calls[0].keywords
        if kw.arg is not None
        and isinstance(kw.value, ast.Attribute)
        and isinstance(kw.value.value, ast.Name)
        and kw.value.value.id == "_config"
    }
    assert wiring.get("story_context_window") == "story_context_window"
    assert wiring.get("story_token_budget") == "story_token_budget"


# ==========================================================================
# 3 — THE ANTI-VACUITY TEST: the configured numbers reach _render_context
# ==========================================================================


class _StoryFake:
    """Minimal recording stand-in for the large tier."""

    def __init__(self) -> None:
        self.schemas: list[type] = []

    def with_structured_output(self, schema: type, **_: object) -> "_StoryFake":
        self.schemas.append(schema)
        return self

    def invoke(self, _messages: Any) -> Story:
        return Story(text="A closing story.")


def _story_state() -> dict:
    """Enough state for ``_ai_story`` to reach ``_render_context``.

    An empty ``players`` map is fine: the roster and private blocks render
    their empty forms, and neither is on the path under test. What matters is a
    non-empty ``messages`` list, because ``_render_context`` returns early on an
    empty one — and an early return would take the recorder with it.
    """
    return {
        "players": {},
        "kill_log": [],
        "winner": "law_abiding",
        "messages": [SystemMessage(content="Day 1 begins.")],
    }


def test_the_configured_governors_reach_render_context(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE WHOLE CHAIN, from two env vars to ``_render_context``'s keywords.

    This is the test that makes the rest of the file non-vacuous. It sets the
    two ``GRAPHIA_STORY_*`` variables to values used nowhere else, loads a real
    config, builds a real graph through ``build_graph``, pulls the compiled
    ``moderator_story`` node out of it, and invokes THAT — so the ``partial``
    bind in ``_assemble_graph`` is exercised rather than described — with
    ``_render_context`` replaced by a recorder.

    Two keywords, asserted separately and for different reasons:

    * ``window`` must be the configured 37 EXACTLY. Its mutation is binding the
      Day's ``context_window`` (150) or the node's own default (400).
    * ``token_budget`` must be the configured 6111 MINUS the measured private
      block, floored at zero — the joint reading §2.2's instruction states, and
      the reason it is a subtraction rather than a second budget. With an empty
      ``players`` map the private block is the empty form, so the arithmetic
      here is the identity case and the assembly file owns the case where the
      subtraction actually bites. Its mutations are dropping the ``-
      private_tokens`` term (which would make this the same number for a
      diary-heavy game, letting a joint knob behave as a public-only one) and
      binding ``context_token_budget`` (20000).

    Deliberately NOT asserted through the rendered prompt: the two governors are
    not independent (the count slice runs before the token trim), so a rendered
    line count cannot separate a wrong budget from a wrong window. Asserting at
    ``_render_context``'s own boundary can.
    """
    monkeypatch.setenv("GRAPHIA_STORY_CONTEXT_WINDOW", str(_WINDOW))
    monkeypatch.setenv("GRAPHIA_STORY_TOKEN_BUDGET", str(_BUDGET))
    config = load_config()
    # Sanity: the values under test are not the Day's, so an assertion below
    # cannot be satisfied by the wrong bind.
    assert _WINDOW != config.context_window
    assert _BUDGET != config.context_token_budget

    graph, _thread_id = build_graph(config)

    recorded: list[dict] = []
    real_render = endgame._render_context

    def _recording_render(messages: Any, speaker_id: str, **kwargs: Any) -> str:
        recorded.append(kwargs)
        return real_render(messages, speaker_id, **kwargs)

    fake = _StoryFake()
    monkeypatch.setattr(endgame, "get_large", lambda: fake)
    monkeypatch.setattr(endgame, "_render_context", _recording_render)

    result = graph.nodes["moderator_story"].bound.invoke(_story_state())

    # The node really ran the model path — ``moderator_story`` is total, so a
    # broken fixture would otherwise show up as an empty update and leave the
    # assertions below reading an empty ``recorded``.
    assert fake.schemas == [Story], (
        f"the large tier was not reached with the Story schema: {fake.schemas}"
    )
    assert result.get("messages"), f"no story message was emitted: {result!r}"
    assert len(recorded) == 1, (
        f"expected exactly one _render_context call, got {len(recorded)}"
    )

    kwargs = recorded[0]
    assert kwargs["window"] == _WINDOW, (
        "the story's public history was sliced with something other than "
        f"GRAPHIA_STORY_CONTEXT_WINDOW: {kwargs['window']}"
    )
    private_tokens = endgame._estimate_tokens(
        endgame._story_private_block({}, None, None)
    )
    assert kwargs["token_budget"] == max(0, _BUDGET - private_tokens), (
        "the public history's budget is not GRAPHIA_STORY_TOKEN_BUDGET minus "
        f"the measured private block: {kwargs['token_budget']}"
    )


def test_the_default_governors_reach_render_context_when_nothing_is_set(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same chain on the default path, so the bind is not override-only.

    Complements the test above: a ``build_graph`` that read the env var but
    bound a literal, or a node whose keyword default disagreed with the config
    default, would pass there and fail here.
    """
    monkeypatch.delenv("GRAPHIA_STORY_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("GRAPHIA_STORY_TOKEN_BUDGET", raising=False)
    config = load_config()
    graph, _thread_id = build_graph(config)

    recorded: list[dict] = []
    real_render = endgame._render_context

    def _recording_render(messages: Any, speaker_id: str, **kwargs: Any) -> str:
        recorded.append(kwargs)
        return real_render(messages, speaker_id, **kwargs)

    fake = _StoryFake()
    monkeypatch.setattr(endgame, "get_large", lambda: fake)
    monkeypatch.setattr(endgame, "_render_context", _recording_render)

    graph.nodes["moderator_story"].bound.invoke(_story_state())

    assert fake.schemas == [Story]
    assert len(recorded) == 1
    assert recorded[0]["window"] == _DEFAULT_STORY_CONTEXT_WINDOW
    private_tokens = endgame._estimate_tokens(
        endgame._story_private_block({}, None, None)
    )
    assert recorded[0]["token_budget"] == max(
        0, _DEFAULT_STORY_TOKEN_BUDGET - private_tokens
    )
