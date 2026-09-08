"""Spec 040 (The Moderator's Closing Story), Task 3.4 — WHISPER EXCLUSION.

WHAT THIS FILE OWNS. The one privacy property of the story prompt: no text the
game ever addressed to a single player can reach the prompt whose output is
shown to the person playing. Task 3.3 *implemented* it (``speaker_id=""`` into
``nodes.day:_render_context``, whose filter is ``if private_to and private_to
!= speaker_id: continue``, so an id matching no player drops every whisper);
this file is what makes the claim real rather than intended.

THE WHISPER KINDS WERE ENUMERATED FROM THE WRITERS, NOT GUESSED. Measured by
AST over ``src/graphia/`` (2026-09-08): the string ``"private_to"`` occurs as a
**dict key** in exactly **two** places, and every other occurrence in the whole
package is a read:

* ``nodes/setup.py:reveal_role`` — the human's private role reveal, one
  whisper, ``private_to = state["human_id"]``.
* ``nodes/night.py:first_night_mafia_intros`` — the Mafia teammate intro,
  **one whisper per mafioso** (so two at the shipped 6-and-2 lineup), each
  ``private_to`` that mafioso's own id.

So §2.2's two named kinds ARE the whole set, which the tech spec asserts but
never establishes. :func:`test_the_game_writes_exactly_two_kinds_of_whisper`
holds that closed: a third writer added later fails there and sends its author
here, rather than quietly shipping an uncovered whisper kind.

WHAT IS DELIBERATELY NOT REPEATED FROM TASK 3.3.
``tests/test_spec040_story_assembly.py::test_no_whisper_text_reaches_the_prompt``
already pins, on hand-typed sentinel whispers: one whisper per kind absent from
the user prompt, the absence of the ``"Moderator (private)"`` label
``_render_context`` gives an admitted whisper, and the public history and the
conspiracy-by-diary both present in the same prompt. **None of those three is
re-asserted here.**

That test's own docstring said this file's version would "replace rather than
repeat" it. **It was KEPT instead** (2026-09-08), and its docstring now records
why: it is the only witness anywhere for the ``"Moderator (private)"`` label,
and it is an *independent* one — hand-typed sentinels over its own fixtures, so
a defect in the shared state builder below cannot hide behind it. It went red
under two of the six mutations at the end of this docstring, so it is not dead
weight.

What this file adds is the part that cannot be got from a hand-typed fixture:

1. The whispers are built **by calling the real writers**, so the marker key
   itself is under test. A writer that stopped setting ``private_to`` — or set
   some other key — leaves Task 3.3's test green and this file red.
2. Every intro at a **two-Mafia** table, not one, since the writer emits one
   per mafioso and each carries a *different* id.
3. **Admissibility is proven before absence is asserted**, which is the
   anti-vacuity move: a negative over a fixture whose whisper was never
   renderable in the first place proves nothing, and this spec has already been
   bitten twice by exactly that shape (Task 1.7's inverted assertion; Task
   1.8's ``isinstance`` test that was green with and without the guard).
4. Absence over the **whole string handed to the model** — the ``STORY_SYSTEM``
   message included — rather than over the user prompt alone.
5. The **route claim** (:func:`test_the_whispers_change_nothing_in_the_prompt`):
   the prompt is byte-identical with and without the whispers, so no slot
   carries them, not merely the one that filters them.

IS ``_render_context`` THE ONLY ROUTE FROM ``messages`` INTO THE PROMPT? Yes —
read across all five slots of ``STORY_USER_TEMPLATE`` as ``_ai_story`` fills
them: ``{outcome}`` is ``_winner_line(state["winner"])``, ``{roster}`` is
``_story_roster_block(state["players"])``, ``{events}`` is
``_story_events_block(state["kill_log"], state["players"])``,
``{private_record}`` is ``_story_private_block`` over ``private_thoughts`` /
``private_diaries``, and ``{context}`` is the single ``_render_context`` call.
``state["messages"]`` is read **once**, in that call. ``STORY_SYSTEM`` is a
literal. So the filter is the only gate, and byte-equality is the assertion
that keeps it the only gate as slots are added.

WHAT THE EXCLUSION DOES AND DOES NOT BUY, stated because the brief for this
task overstates it. It keeps whisper **text** out of the prompt. It does not
keep the *information* out: ``_story_roster_block`` renders every seat's side
by design (§2.2), and ``end_screen`` has already shown the player every role
before the story is even requested — so the reveal is public by then and the
teammate list is derivable from the roster block. The property this file pins
is therefore the narrow, real one: the Moderator learns the conspiracy from the
Mafiosos' **diaries**, never by being handed the private channel, which is what
§2.2 means by "the point of the feature".

THE TEST LEVEL IS A DELIBERATE CHOICE, and Slice 3's preamble is why. It warns
against copying ``tests/test_spec040_story_ui_order.py``'s live-dispatcher
pattern (a ~30%-per-test flake in which the human is killed on Night 1) and
names ``dynamic_night_pointing`` installed before the name submit as the
race-free alternative for anything that must drive a game. **Nothing here
drives a game**: every property is a property of one pure assembly, so each is
asserted by calling ``_ai_story`` directly against a hand-built state with a
recording stand-in for the large tier. That puts the trap out of reach rather
than avoiding it. (Task 3.6 owns the game-level material assertions.)

MUTATION-VERIFIED. Six mutations, each applied to the production code for real
from 5 green here, measured, and reverted (2026-09-08). Counts are over this
file **and** ``tests/test_spec040_story_assembly.py`` together, so what Task
3.3's single guard does and does not catch is visible:

* ``speaker_id=""`` → ``state.get("human_id", "")`` — **2 failed, both here**:
  the ``setup.reveal_role`` case and the route test. **Task 3.3's test stayed
  GREEN**, and the reason is the sharpest argument for this file existing: its
  ``_state`` fixture carries no ``human_id`` key, so the mutated expression
  evaluates to ``""`` and the filter goes on dropping everything. A privacy
  regression that reads the human's seat out of state would have shipped.
* ``speaker_id=""`` → the first mafioso's id
  (``next((p.id for p in players.values() if p.role == "mafia"), "")``) —
  **4 failed**: the ``night.first_night_mafia_intros`` case, the
  conspiracy-route test, the route test, and Task 3.3's.
* ``speaker_id=""`` → ``next(iter(players), "")`` (Task 3.3's own recorded
  mutation, re-run) — **3 failed**: the ``setup.reveal_role`` case, the route
  test, and Task 3.3's. It admits the human's whisper because the human is
  first in ``players`` insertion order in both files' tables.
* ``additional_kwargs={"private_to": human_id}`` **deleted** from
  ``setup.reveal_role`` — **7 failed over the whole suite**, three of them
  here: the enumeration guard, the ``setup.reveal_role`` case (its
  ``private_to`` assertion) and the route test. Task 3.3's stayed green. This
  is the mutation that says the MARKER KEY is under test rather than assumed.
* a third writer added (a ``{"private_to": …}`` dict literal inside
  ``endgame:_winner_line``) — **1 failed**: the enumeration guard alone, which
  is exactly its job.
* a ``"private_to"`` literal in a shape the classifier does not know
  (``("private_to",)``, a tuple element) — **1 failed**: the enumeration
  guard, on its ``unclassified`` assertion. Run because that assertion would
  otherwise be vacuous-by-construction — the failure mode this spec has
  already deleted one test for (Task 1.8).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import graphia.nodes.endgame as endgame
from graphia.llm import Story
from graphia.nodes.day import _render_context
from graphia.nodes.night import first_night_mafia_intros
from graphia.nodes.setup import reveal_role
from graphia.prompts import MAFIA_TEAMMATE_INTRO_TEMPLATE

# Task 3.3's fixtures, imported rather than re-typed so the two files cannot
# drift in what a "player" or a "state" is. ``_CapturingStoryFake`` is the
# recording ``get_large()`` stand-in; the composer below is this file's own
# because it needs the WHOLE message list handed to the model, not just the
# user prompt.
from test_spec040_story_assembly import (  # type: ignore[import-not-found]
    _CapturingStoryFake,
    _p,
    _persona,
    _roster,
    _state,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_ROOT = _REPO_ROOT / "src" / "graphia"

# The two writers, as (module path relative to ``src/graphia``, function).
# Asserted as a set below, so this is the enumeration — not a comment about it.
_WHISPER_WRITERS = {
    ("nodes/night.py", "first_night_mafia_intros"),
    ("nodes/setup.py", "reveal_role"),
}

HUMAN_ID = "h"
MAFIA_IDS = ("m1", "m2")


# ==========================================================================
# Shared helpers
# ==========================================================================


def _two_mafia_table() -> dict[str, Any]:
    """The human, two mafiosos and one dead citizen.

    Two mafiosos on purpose: ``first_night_mafia_intros`` emits one whisper per
    mafioso, each addressed to a DIFFERENT id, so a one-mafioso table cannot
    tell "every intro is dropped" from "the first intro is dropped".
    """
    return _roster(
        _p(HUMAN_ID, "Alexey", is_human=True),
        _p(MAFIA_IDS[0], "Ivy", role="mafia", persona=_persona("ivy")),
        _p(MAFIA_IDS[1], "Mallory", role="mafia", persona=_persona("mallory")),
        _p("c1", "Bram", is_alive=False, persona=_persona("bram")),
    )


def _public_history() -> list[Any]:
    """Public traffic, so every prompt below has a real history to render."""
    return [
        SystemMessage(content="Welcome to Graphia."),
        SystemMessage(content="Night falls."),
        AIMessage(content="I trust Bram.", name="Ivy"),
    ]


def _game_state(**overrides: Any) -> dict:
    """A finished-game state carrying the diary the story is meant to use."""
    state = _state(
        players=_two_mafia_table(),
        human_id=HUMAN_ID,
        cycle=1,
        messages=list(_public_history()),
        private_diaries={
            MAFIA_IDS[0]: [
                {
                    "day": 1,
                    "thoughts_before": 0,
                    "text": "DIARY-CONSPIRACY: Bram dies tonight",
                }
            ]
        },
    )
    state.update(overrides)
    return state


def _whispers_of_the_role_reveal(state: dict) -> list[Any]:
    """The real whisper ``setup.reveal_role`` writes — never a hand-typed one."""
    return list(reveal_role(state)["messages"])


def _whispers_of_the_mafia_intros(state: dict) -> list[Any]:
    """The real whispers ``night.first_night_mafia_intros`` writes."""
    return list(first_night_mafia_intros(state)["messages"])


def _full_prompt(monkeypatch: pytest.MonkeyPatch, state: dict) -> str:
    """Every message ``_ai_story`` hands the model, joined — system included.

    Task 3.3's ``_compose`` returns the user prompt alone. The privacy property
    is a property of the whole request, so this returns all of it; and it keeps
    ``_compose``'s non-vacuity discipline, asserting the call was REACHED with
    the ``Story`` schema before returning, because ``_ai_story`` is total by
    construction and any fixture mistake would otherwise degrade into the
    legitimate no-story ending and leave every assertion below vacuous.
    """
    fake = _CapturingStoryFake()
    monkeypatch.setattr(endgame, "get_large", lambda: fake)
    text = endgame._ai_story(state)  # type: ignore[arg-type]
    assert fake.schemas == [Story], (
        "the large tier was never reached with the Story schema, so this test "
        f"would measure nothing — bindings={fake.bindings}, "
        f"schemas={fake.schemas}, story={text!r}"
    )
    assert len(fake.messages_log) == 1, (
        f"expected exactly one invoke, got {len(fake.messages_log)}"
    )
    sent = fake.messages_log[0]
    assert isinstance(sent[0], SystemMessage) and isinstance(sent[1], HumanMessage)
    return "\n".join(str(msg.content) for msg in sent)


def _owner_of(whisper: Any) -> str:
    """The id a whisper is addressed to, read off the message itself."""
    owner = (getattr(whisper, "additional_kwargs", {}) or {}).get("private_to")
    assert isinstance(owner, str) and owner, (
        f"not a whisper — no private_to route on {whisper!r}"
    )
    return owner


# ==========================================================================
# 1 — THE ENUMERATION, HELD CLOSED
# ==========================================================================


def _private_to_sites() -> tuple[
    set[tuple[str, str]], set[tuple[str, str]], list[str]
]:
    """Classify every ``"private_to"`` string literal in ``src/graphia``.

    Returns ``(writes, reads, unclassified)``. A WRITE is the literal used as a
    dict key (``additional_kwargs={"private_to": …}``) or as a subscript target
    (``kwargs["private_to"] = …``); a READ is a ``.get()`` argument, a load
    subscript or a comparison. Anything else lands in ``unclassified`` and
    fails the test loudly rather than being assumed harmless — the point of the
    scan is that an unfamiliar shape gets a reader's attention, and a shape this
    classifier silently filed as a read would defeat that.

    Comments and docstrings cannot match: a docstring is one ``ast.Constant``
    holding the whole docstring, and a comment is not in the tree at all.
    """
    writes: set[tuple[str, str]] = set()
    reads: set[tuple[str, str]] = set()
    unclassified: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[int, ast.AST] = {}
        owners: dict[int, str] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        # ``ast.walk`` is breadth-first, so an outer function is visited before
        # any function nested inside it and the inner name wins.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    owners[id(inner)] = node.name
        rel = path.relative_to(_PACKAGE_ROOT).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and node.value == "private_to"):
                continue
            site = (rel, owners.get(id(node), "<module>"))
            parent = parents.get(id(node))
            if isinstance(parent, ast.Dict) and any(
                key is node for key in parent.keys
            ):
                writes.add(site)
            elif isinstance(parent, ast.Subscript):
                if isinstance(parent.ctx, ast.Load):
                    reads.add(site)
                else:
                    writes.add(site)
            elif (
                isinstance(parent, ast.Call)
                and getattr(parent.func, "attr", None) == "get"
            ):
                reads.add(site)
            elif isinstance(parent, ast.Compare):
                reads.add(site)
            else:
                unclassified.append(f"{rel}:{node.lineno} under {type(parent).__name__}")
    return writes, reads, unclassified


def test_the_game_writes_exactly_two_kinds_of_whisper() -> None:
    """The enumeration this whole file rests on, measured rather than assumed.

    §2.2 names the human's role reveal and the Mafia teammate intros. It does
    not establish that those are the only two, and every assertion below is
    only as complete as that claim — so it is checked against the package
    itself. A third whisper writer added by a later spec fails HERE, which is
    the one place that sends its author to extend the exclusion tests rather
    than letting an uncovered kind ship green.
    """
    writes, reads, unclassified = _private_to_sites()

    assert not unclassified, (
        "a ``private_to`` literal in a shape this scan does not know how to "
        f"classify — inspect it and extend the classifier: {unclassified}"
    )
    assert writes == _WHISPER_WRITERS, (
        "the set of whisper WRITERS changed. Every kind of private message the "
        "game writes must be covered by the exclusion tests in this file, so "
        "add the new writer to a parametrisation below (and to this module's "
        f"docstring) before updating this expectation. writes={sorted(writes)}"
    )
    # Not pinned as a set (a new reader is harmless and there is no privacy
    # claim to make about one), but the scan must have found the readers it
    # found on 2026-09-08 — otherwise it is matching nothing and the write set
    # above is a vacuous empty-vs-empty comparison waiting to happen.
    assert ("nodes/day.py", "_render_context") in reads


# ==========================================================================
# 2 — EACH REAL WHISPER: ADMISSIBLE, AND STILL ABSENT
# ==========================================================================


@pytest.mark.parametrize(
    ("writer", "count"),
    [
        pytest.param(_whispers_of_the_role_reveal, 1, id="setup.reveal_role"),
        pytest.param(
            _whispers_of_the_mafia_intros, 2, id="night.first_night_mafia_intros"
        ),
    ],
)
def test_a_real_whisper_is_admissible_and_still_never_reaches_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
    writer: Callable[[dict], list[Any]],
    count: int,
) -> None:
    """Built by the real writer; proven renderable; then proven absent.

    THE THREE STEPS ARE THE ANTI-VACUITY ARGUMENT, and the middle one is the
    one a hand-typed fixture cannot make. Step 1 asserts the writer really
    produced the whispers and that they carry a ``private_to`` route — so the
    marker key is under test, and a writer that stopped setting it fails here
    instead of passing an absence test for free. Step 2 renders the very same
    message list through ``_render_context`` with the OWNER's id and asserts
    the text comes back: the whisper is admissible, so its absence in step 3
    is the filter's doing and not the window's, the token budget's, an empty
    fixture's or a typo's. Only then is step 3 — absence from the whole string
    handed to the model — worth anything.
    """
    state = _game_state()
    whispers = writer(state)

    # 1 — the whispers exist, and they are whispers.
    assert len(whispers) == count, (
        f"the writer produced {len(whispers)} message(s), not {count} — the "
        "fixture no longer exercises what this case is named for"
    )
    owners = [_owner_of(w) for w in whispers]
    assert len(set(owners)) == count, f"expected {count} distinct routes: {owners}"
    bodies = [str(w.content) for w in whispers]
    assert all(body.strip() for body in bodies), f"an empty whisper: {bodies}"

    state["messages"] = list(_public_history()) + list(whispers)

    # 2 — ADMISSIBLE: each whisper renders for the player it is addressed to.
    for body, owner in zip(bodies, owners):
        rendered = _render_context(state["messages"], owner)
        assert body in rendered, (
            f"the whisper addressed to {owner!r} is not renderable at all, so "
            "asserting its absence downstream would prove nothing"
        )

    # 3 — and absent from the whole request, system message included.
    prompt = _full_prompt(monkeypatch, state)
    for body in bodies:
        assert body not in prompt, f"a whisper reached the story prompt: {body!r}"


def test_the_mafia_intros_conspiracy_is_absent_as_text_and_present_as_diary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exclusion and the substitute for it, in one prompt.

    §2.2's claim is not merely that the intro is dropped — it is that "the
    Moderator gets the conspiracy from the Mafiosos' DIARIES, which is the
    point of the feature". Both halves are asserted together because either
    alone is misleading: an absent intro in a prompt with no private material
    is a prompt that knows nothing, and this spec would then be excluding the
    channel it depends on.

    The intro's own template is IMPORTED rather than re-typed, so a reword of
    ``MAFIA_TEAMMATE_INTRO_TEMPLATE`` cannot leave a stale literal passing
    here. Its distinctive head is checked for absence too, guarded by first
    asserting the head is not something the prompt says on its own — otherwise
    a Slice 4 rewrite of ``STORY_SYSTEM`` that happened to use those words
    would turn a real assertion into a coincidence.
    """
    state = _game_state()
    intros = _whispers_of_the_mafia_intros(state)
    state["messages"] = list(_public_history()) + list(intros)

    baseline = _full_prompt(monkeypatch, _game_state())
    head = MAFIA_TEAMMATE_INTRO_TEMPLATE.split("{names}")[0]
    assert head.strip(), "the template no longer has a head to match on"
    assert head not in baseline, (
        f"the prompt says {head!r} on its own, so asserting its absence below "
        "would be a coincidence rather than a privacy check"
    )

    prompt = _full_prompt(monkeypatch, state)

    assert head not in prompt
    for intro in intros:
        assert str(intro.content) not in prompt
    # Every teammate NAME the intro would have handed over is still in the
    # prompt — from the roster, by design (§2.2) — so this is not a test that
    # the Moderator is kept ignorant. It is a test of the ROUTE.
    assert "Ivy" in prompt and "Mallory" in prompt
    # And the conspiracy arrives the way the feature intends it to.
    assert "DIARY-CONSPIRACY: Bram dies tonight" in prompt


# ==========================================================================
# 3 — THE ROUTE: NO SLOT CARRIES A WHISPER, NOT JUST THE FILTERED ONE
# ==========================================================================


def test_the_whispers_change_nothing_in_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-identical with and without every whisper the game writes.

    THE ONLY ASSERTION HERE THAT SURVIVES A NEW SLOT. Every other test in this
    file names the text it expects to be absent, which can only ever check the
    routes someone has already thought of. This one compares two whole prompts
    that differ **only** in whether the whispers are in ``state["messages"]``,
    so it covers every slot at once — including a sixth slot added by a later
    spec that reads ``messages`` for some other purpose and quietly reopens the
    route. Read across the five slots today, ``_render_context`` is the single
    reader of ``messages`` (see this module's docstring); this is what keeps
    that true rather than merely recording it.

    Both whisper kinds together, from the real writers, at a two-Mafia table:
    three whispers on three distinct routes.
    """
    state = _game_state()
    whispers = _whispers_of_the_role_reveal(state) + _whispers_of_the_mafia_intros(
        state
    )
    owners = [_owner_of(w) for w in whispers]
    assert len(whispers) == 3 and len(set(owners)) == 3, (
        f"expected three whispers on three routes, got {owners}"
    )

    with_whispers = _game_state(
        messages=list(_public_history()) + list(whispers),
    )
    without_whispers = _game_state(messages=list(_public_history()))

    # Admissible, so the comparison below is not between two prompts that were
    # always going to match because there was nothing to exclude.
    for whisper, owner in zip(whispers, owners):
        assert str(whisper.content) in _render_context(
            with_whispers["messages"], owner
        )

    prompt_with = _full_prompt(monkeypatch, with_whispers)
    prompt_without = _full_prompt(monkeypatch, without_whispers)

    # The prompt really does carry material, so this is a comparison of two
    # substantial strings rather than of two failures.
    assert "Moderator: Night falls." in prompt_with
    assert "Ivy: I trust Bram." in prompt_with
    assert "DIARY-CONSPIRACY: Bram dies tonight" in prompt_with

    assert prompt_with == prompt_without, (
        "the whispers changed the prompt, so something in it reads "
        "``messages`` without the privacy filter"
    )
