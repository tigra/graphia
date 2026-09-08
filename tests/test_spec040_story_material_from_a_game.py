"""Spec 040 (The Moderator's Closing Story) — Task 3.6: the material, AT GAME LEVEL.

Slice 3's four preceding tasks are all unit-level over pure functions, each for
the reason Task 3.3 first recorded and Task 3.4 repeated: *"nothing here drives
a game, so Slice 3's live-dispatcher flake is out of reach rather than avoided
— Task 3.6 owns the game-level material assertions."* Three of Task 3.2's own
docstrings hand this file a named deliverable (verified 2026-09-08):
``test_a_dead_player_is_an_author_like_any_other`` (*"Spec 040 Task 3.6 owns the
game-level version of this"*), ``test_the_human_gets_no_section_because_neither
_channel_holds_them`` (*"Task 3.6 owns the game-level assertion that the
channels really are empty for the human"*) and
``test_nothing_written_says_so_in_words`` (*"Task 3.6 owns the game-level
version"*). This file is that.

WHAT MAKES THIS A DIFFERENT CLAIM, NOT A SECOND COPY
-----------------------------------------------------

``tests/test_spec040_story_private_block.py`` proves the RENDERER attributes a
hand-built dict correctly. This file proves the COMPOSITION: that a game
actually played leaves material the writers wrote, merged by the production
reducers, in the prompt ``_ai_story`` hands the model. Four links, and every one
of them can break without any of Slice 3's other tests going red:

1. the graph routes to ``day_round_reflect`` and ``day_diary`` at all;
2. what those nodes write survives ``state:_merge_private_thoughts`` /
   ``_merge_private_diaries`` into committed state;
3. ``moderator_story`` is reached with that state, not an earlier one;
4. ``_ai_story`` reads both channels out of it.

The unit-level alternative — calling the two writer nodes in sequence against a
hand-built state and merging their deltas by hand — was considered and
rejected. It would exercise the writers' real predicates, but the test would
then be *choosing* the call order and *performing* the merge, so it would
hand-assemble the very composition it exists to prove: a graph that stopped
routing to ``day_diary``, or an ``_ai_story`` that stopped reading
``private_diaries``, would leave it green.

THE FLAKE TRAP IS OUT OF REACH HERE, NOT MITIGATED
---------------------------------------------------

Slice 3's preamble warns against ``tests/test_spec040_story_ui_order.py``'s
live-dispatcher pattern — a ~30%-per-test flake in which the human is killed on
Night 1 because ``fake._invoke`` is installed AFTER the name is submitted and
the Textual worker can reach the Night pointing call first. Nothing in that
race exists here:

- **No Textual and no worker thread.** The drive is the headless
  ``graph.stream`` + ``Command(resume=…)`` pump, which is this suite's dominant
  whole-game mechanism (nine files) and this slice's own: ``_run_to_end`` /
  ``_passive_human`` are IMPORTED from ``tests/test_spec040_story_topology.py``
  rather than copied, exactly as ``tests/test_spec040_story_node.py`` imports
  them, so a fix to the pump cannot miss one of the three files.
- **Every fake is installed before the first super-step**, so there is no
  ordering to lose.
- **The Night target is resolved at invoke time from live state** by the
  documented race-free fixture ``dynamic_night_pointing`` — the alternative the
  preamble points at — and the human's survival is ASSERTED rather than assumed.

WHAT THE TRAJECTORY IS, AND WHY EACH PIN IS THERE
--------------------------------------------------

A six-seat table, two Nights and one full Day, ending in a Mafia win::

    Night 1  Priya killed          -> she never writes anything, ever
    Day 1    six speaking rounds   -> five reflections each for the four AIs
             day_close -> day_diary -> one Day-1 diary each
    Night 2  Silas killed          -> 2 Mafia vs 2 Law-abiding, Mafia win
             end_screen -> moderator_story

That yields, in ONE game, every seat this task must assert on: two SURVIVING
Mafiosos (Ivy, Marco), a contributor who DIED before the end (Silas), a
Night-1 victim who wrote NOTHING (Priya), a surviving Citizen (Yuki), and a
HUMAN who took six Day turns (Alice).

**Two surviving Mafiosos rather than one, deliberately** — Task 3.4's finding
(d) recorded that a one-of-a-kind fixture cannot distinguish "every one of them
is present" from "the first one is present", and the conspiracy's presence is
this feature's stated point.

**RNG is pinned by monkeypatching the helper that uses it, never by a seed**
(architecture §6, ADR-006): ``setup:_shuffle_deck`` becomes a no-op so the deal
is the deck's own order, and the human's side is pinned with ``GRAPHIA_ROLE``.
``day:_shuffle_order`` is deliberately left ALONE — speaking order changes who
talks when, not who writes, and every writer fan-out iterates
``players.values()`` insertion order.

ANTI-VACUITY: WHY THE SENTINELS ARE PER AUTHOR AND PER ENTRY
-------------------------------------------------------------

There is a specific trap here, and it is in the shared fake rather than in the
production code. ``conftest:FakeLargeUnified._invoke`` REPLAYS its last value
once a queue drains (``tests/conftest.py``, the ``if not queue:`` branch's
``return last``).
So ``fake_large(reflections=[Reflection(thought="X")])`` serves *"X"* to every
author for the rest of the game, and an assertion that *"the Mafioso's
reflection is in the prompt"* would then pass with all four authors' material
misattributed to each other — the exact failure
``tests/test_spec040_story_private_block.py`` says it is weighted against.

So this file installs :class:`_PerAuthorLarge`, which answers ``Reflection`` and
``Diary`` with a sentinel derived from the ``{speaker}`` slot of the REAL prompt
template and records what it served. Every assertion below is then positive AND
negative: each author's sentinel under that author's heading, and under nobody
else's. The speaker is extracted from the template rather than retyped, so a
Slice 4 reword of the prompts breaks nothing here.

WHAT IS REUSED FROM TASK 3.2, AND WHAT IS DELIBERATELY SKIPPED
---------------------------------------------------------------

Reused as a game-level counterpart, because the composed claim is stronger than
the rendered one: the dead author, the human's structural absence, the
Day-on-diaries-only attribution, and the nothing-written empty block.

**Skipped as near-duplicates, recorded rather than added** (the discipline Task
3.4 used):

- ``test_authors_appear_in_roster_order``, ``test_a_line_never_carries_a
  _trailing_space``, ``test_a_blank_or_malformed_entry_is_dropped_not
  _placeheld``, ``test_neither_argument_is_mutated``, ``test_the_block
  _consumes_the_shared_walk``, ``test_the_block_does_not_delegate_to_the
  _players_renderer`` — properties of the renderer's own text and structure,
  with no survival, authorship or routing dimension. A game adds nothing.
- ``test_an_unrecognised_kind_is_neither_a_diary_nor_a_reflection`` and the
  ``DIARY_DAY_UNKNOWN`` placeholder sweep — **unreachable from a real game.**
  The walk emits exactly two kinds and ``day_diary`` always writes an ``int``
  ``day``; both exist for a future channel and for resumed-checkpoint shapes.
- ``test_the_whole_diary_is_rendered_with_no_window`` — the §2.3 no-window
  constraint needs more than ``DIARY_WINDOW`` (3) diary entries for one author,
  i.e. a four-Day game, at roughly four times this drive's cost. Task 3.2 owns
  it over the renderer and ``tests/test_spec040_story_assembly.py`` owns what
  ``_ai_story`` passes it, so the game would only re-compose two proven facts.
- The writers' ``is_alive and not is_human`` predicates themselves —
  ``tests/test_slice28_private_thoughts.py`` and
  ``tests/test_slice39_diary_before_night.py::test_human_seat_never_writes``
  already own those at unit level. What this file adds is the CLOSED
  enumeration (section 4) plus the composed consequence.

WHICH ACCEPTANCE CRITERIA THIS FILE IS THE WITNESS FOR
-------------------------------------------------------

Four of the functional spec's criteria are written at exactly this level — "given
a finished game … when the story is read" — and had no game-level witness before
this file:

- *"it draws on that private writing — it is not a retelling of only what was
  said aloud"* -> ``test_a_channel_reaches_the_story_from_a_game``
- *"the surviving Mafiosos' private writing is available to it — the conspiracy
  that won is not sealed off"* ->
  ``test_the_surviving_mafiosos_writing_is_in_the_story``
- *"a player died early … their private writing, if any exists, is available too
  — nothing is withheld on the grounds that they did not survive"* ->
  ``test_a_player_killed_before_the_end_still_contributes`` and
  ``test_the_night_one_victim_wrote_nothing_at_all``
- *"it may draw on both the whole-day diaries and the shorter end-of-round
  reflections"* -> the parametrised pair above

The human criterion (*"it has no private writing for them, and never claims to
know what they were thinking"*) is only PARTLY testable and the split matters:
that no private material EXISTS is what section 4 pins, and it is a fact about
the code; that the story never claims otherwise is prompt text, which §2.7 says
cannot be guaranteed and Task 4.3 owns as an instruction assertion.

EVERY PROPERTY BELOW WAS MUTATION-VERIFIED — see the record at the foot of this
module.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from graphia.config import load_config
from graphia.graph import build_graph, make_run_config
from graphia.llm import DayAction, Diary, Reflection, Story
from graphia.nodes.day import (
    DIARY_DAY_UNKNOWN,
    PRIVATE_RECORD_KIND_DIARY,
    PRIVATE_RECORD_KIND_THOUGHT,
)
from graphia.nodes.endgame import (
    STORY_PRIVATE_AUTHOR_TEMPLATE,
    STORY_PRIVATE_DIARY_MARKER,
    STORY_PRIVATE_EMPTY,
    STORY_PRIVATE_LEGEND,
    STORY_PRIVATE_THOUGHT_MARKER,
)
from graphia.prompts import DIARY_USER_TEMPLATE, REFLECTION_USER_TEMPLATE
from graphia.state import PlayerState

from conftest import FakeLargeUnified  # type: ignore[import-not-found]

# Task 3.3's slot reader, imported rather than re-typed so the two files cannot
# drift in how a ``STORY_USER_TEMPLATE`` slot is delimited. It derives both
# anchors from the template itself, so a Slice 4 reword of the labels leaves
# every assertion below intact.
from test_spec040_story_assembly import (  # type: ignore[import-not-found]
    _slot_text,
)

# The pump and the passive human, imported from this slice's topology file on
# the precedent ``tests/test_spec040_story_node.py`` set. Duplicating the
# interrupt pump a third time inside one slice is what that file's comment
# warns against.
from test_spec040_story_topology import (  # type: ignore[import-not-found]
    HUMAN_NAME,
    NAME_POOL,
    _passive_human,
    _run_to_end,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "graphia"

# The two private channels, and their ONE writer each. Asserted as a set in
# section 4, so this is the enumeration rather than a comment about one.
_CHANNEL_WRITERS = {
    "private_thoughts": ("nodes/day.py", "day_round_reflect"),
    "private_diaries": ("nodes/day.py", "day_diary"),
}

# What the human says on every Day turn. A DISTINCT sentinel rather than
# ``_passive_human``'s ``"..."`` because section 5 needs to prove the person
# really took turns — a generic ellipsis could be anybody's line, and "the
# human participated" is the premise that makes their empty private record
# meaningful instead of merely true of an absent seat.
HUMAN_SPOKE = "Alice-said-this-out-loud."

SCRIPTED_STORY = "The two who lived had been writing it down all along."

# Every seat's name, so the assertions can name a player rather than reach for
# ``players.values()`` positions. ``fake_small``'s pool form consumes the list
# in order and is deterministic (``conftest:_pooled_names``), and
# ``setup:_shuffle_deck`` is pinned to a no-op below, so the deal is fixed:
# the human takes seat 0 with the role ``GRAPHIA_ROLE`` names and the deck's
# remaining cards fall in order onto seats 1..n.
MAFIA_A, MAFIA_B = "Ivy", "Marco"
NIGHT_ONE_VICTIM = "Priya"
NIGHT_TWO_VICTIM = "Silas"
SURVIVING_CITIZEN = "Yuki"

# ==========================================================================
# The per-author large tier
# ==========================================================================

# The eight slots of both writer templates. Written once; ``_speaker_of``
# derives its anchors by formatting the template with a distinct sentinel per
# slot, so neither the labels nor the slot ORDER is hard-coded here.
_WRITER_SLOTS = (
    "speaker",
    "role_label",
    "win_condition",
    "team_line",
    "persona",
    "standings",
    "context",
    "private_thoughts",
)


def _speaker_of(messages: Any, template: str) -> str:
    """Which player ``template`` is being rendered for, read off the prompt.

    ``_ai_reflect`` and ``_ai_diary`` both format their template with
    ``speaker=speaker.name`` (verified), and the name is the only thing in the
    prompt that identifies the writer to a stand-in model. Anchoring on the
    literal text either side of the ``{speaker}`` slot — recovered from the
    template, never retyped — is what keeps this immune to a prompt reword and
    what stops it matching one of the OTHER names the roster and the standings
    put in the same prompt.
    """
    skeleton = template.format(**{name: f"\x00{name}\x00" for name in _WRITER_SLOTS})
    head, tail = skeleton.split("\x00speaker\x00")
    suffix = tail.split("\x00")[0]
    body = messages[1].content
    assert isinstance(body, str) and body.startswith(head), (
        f"the writer prompt does not start with {head!r}; got {body[:120]!r}"
    )
    rest = body[len(head) :]
    assert suffix and suffix in rest, (
        f"the text following the speaker slot ({suffix!r}) is not in the prompt"
    )
    return rest[: rest.index(suffix)]


class _PerAuthorLarge(FakeLargeUnified):
    """The unified fake, with ``Reflection`` and ``Diary`` answered PER AUTHOR.

    THE REASON THIS EXISTS IS ANTI-VACUITY, and it is worth stating precisely
    because the alternative looks adequate. ``FakeLargeUnified`` replays a
    drained queue's last value, so any fixed script makes every author's
    material identical text; "the Mafioso's diary is in the prompt" then holds
    no matter which heading it landed under, and the misattribution
    ``tests/test_spec040_story_private_block.py`` is weighted against becomes
    invisible. Deriving each answer from the ``{speaker}`` the writer is
    actually asking for makes every entry in the finished prompt traceable to
    one author and one channel.

    ``Story`` is left to the inherited queue and only OBSERVED, so the story
    itself stays scripted and the failure vocabulary is unchanged; the recorded
    ``story_messages`` is what the prompt assertions read.

    Everything else — ``DayAction``, ``Ballot``, ``Pointing``, ``Persona`` — is
    delegated to ``super()``, including its loud unknown-schema failure and its
    ``calls_by_schema`` ledger, which the assertions use to prove the writers
    were REACHED rather than silently substituted.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reflections_by_author: dict[str, list[str]] = {}
        self.diaries_by_author: dict[str, list[str]] = {}
        self.story_messages: list[Any] = []

    def _book(self, schema: type) -> None:
        self.call_count += 1
        self.calls_by_schema[schema] = self.calls_by_schema.get(schema, 0) + 1

    def _invoke(self, schema: type, messages: Any) -> Any:
        if schema is Reflection:
            self._book(schema)
            who = _speaker_of(messages, REFLECTION_USER_TEMPLATE)
            served = self.reflections_by_author.setdefault(who, [])
            text = f"REFLECTION-OF-{who.upper()}-{len(served) + 1}"
            served.append(text)
            return Reflection(thought=text)
        if schema is Diary:
            self._book(schema)
            who = _speaker_of(messages, DIARY_USER_TEMPLATE)
            served = self.diaries_by_author.setdefault(who, [])
            text = f"DIARY-OF-{who.upper()}-{len(served) + 1}"
            served.append(text)
            return Diary(entry=text)
        if schema is Story:
            self.story_messages.append(messages)
        return super()._invoke(schema, messages)


# ==========================================================================
# The driven game
# ==========================================================================


class _Game:
    """One finished game, and the story prompt it produced.

    Deliberately a plain container: every field is read straight off the
    finished graph state or off the fake, so no assertion below depends on this
    class computing anything.
    """

    def __init__(
        self,
        graph: Any,
        run_config: dict,
        fake: _PerAuthorLarge,
        trajectory: list[str],
    ) -> None:
        self.state: dict = graph.get_state(run_config).values
        self.fake = fake
        self.trajectory = trajectory
        self.players: dict[str, PlayerState] = self.state.get("players", {})
        self.by_name: dict[str, PlayerState] = {
            p.name: p for p in self.players.values()
        }
        assert len(self.fake.story_messages) == 1, (
            "expected exactly one Story call from one finished game, got "
            f"{len(self.fake.story_messages)} — the assertions would measure "
            "nothing"
        )
        prompt = self.fake.story_messages[0][1].content
        assert isinstance(prompt, str)
        self.prompt: str = prompt
        self.private: str = _slot_text(self.prompt, "private_record")
        self.public: str = _slot_text(self.prompt, "context")

    def section(self, name: str) -> str:
        """The one author section of the private block belonging to ``name``.

        Fails loudly rather than returning ``""``, matching
        ``tests/test_spec040_story_private_block.py:_section``: a helper that
        silently yielded nothing would make every negative assertion vacuous.
        """
        heading = STORY_PRIVATE_AUTHOR_TEMPLATE.format(name=name)
        chunks = [c for c in self.private.split("\n\n") if c.startswith(heading)]
        assert len(chunks) == 1, (
            f"expected exactly one {name!r} section in:\n{self.private}"
        )
        return chunks[0]

    def has_section(self, name: str) -> bool:
        heading = STORY_PRIVATE_AUTHOR_TEMPLATE.format(name=name)
        return heading in self.private

    def channel(self, name: str, key: str) -> list:
        return list((self.state.get(key) or {}).get(self.by_name[name].id) or [])


def _install_table(
    monkeypatch: pytest.MonkeyPatch,
    fake_small: Callable[..., Any],
    *,
    citizens: int,
    mafia: int,
) -> None:
    """Pin the lineup, the deal and the roster names.

    ``_shuffle_deck`` is monkeypatched rather than seeded — there is no seed
    env var in this project and mechanical RNG is pinned at the helper that
    uses it (architecture §6, ADR-006). With the shuffle a no-op the deck is
    dealt in its literal order onto seats 1..n behind the human's pinned card,
    so the role of every named seat is fixed without a magic number anywhere.
    """
    monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", str(citizens))
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", str(mafia))
    monkeypatch.setattr("graphia.nodes.setup._shuffle_deck", lambda deck: None)
    fake_small(NAME_POOL)


def _install_large(monkeypatch: pytest.MonkeyPatch) -> _PerAuthorLarge:
    """Point all five large-tier call sites at one per-author fake.

    The five are exactly the ones ``fake_large`` patches: ``get_large`` in
    ``nodes.day`` / ``nodes.night`` / ``nodes.setup`` / ``nodes.endgame``, plus
    ``setup.get_persona_model`` for the spec-034 diversified persona path.
    Done here rather than through ``fake_large`` because the fixture installs
    ``FakeLargeUnified`` itself and this file needs the subclass; the surface
    patched is deliberately identical, so ``safe_llm``'s net is replaced
    everywhere it was installed and nothing can fall through to real boto3.
    """
    fake = _PerAuthorLarge(
        # Deep enough that no AI speaking turn starves across two Nights and
        # six rounds; every entry is a plain speech, so no AI ever initiates a
        # vote and the Day ends on the round cap.
        day_actions=[
            DayAction(kind="speak", text=f"AI-line-{i}.") for i in range(200)
        ],
        stories=[Story(text=SCRIPTED_STORY)],
    )
    for module in ("day", "night", "setup", "endgame"):
        monkeypatch.setattr(f"graphia.nodes.{module}.get_large", lambda: fake)
    monkeypatch.setattr(
        "graphia.nodes.setup.get_persona_model", lambda temperature: fake
    )
    return fake


def _talkative_human(interrupt_value: dict[str, Any]) -> Any:
    """``_passive_human``, except the Day turn says something recognisable.

    Delegating for every other kind keeps the one shared responder as the
    single description of how a human who never leads behaves; only the Day
    turn needs to be attributable, and only because section 5 asserts the
    person really played.
    """
    if interrupt_value.get("kind") == "day_turn":
        return HUMAN_SPOKE
    return _passive_human(interrupt_value)


@pytest.fixture
def game(
    env: Path,
    fake_small: Callable[..., Any],
    dynamic_night_pointing: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_Game]:
    """One two-Night Mafia-win game, driven to END, with its story prompt.

    Function-scoped on purpose: the whole drive costs well under a tenth of a
    second with the fakes in place, so every test gets its own game and the
    autouse guards (``safe_llm``, ``structured_output_tripwire``,
    ``reset_substitution_sink``) keep their per-test meaning.

    The trajectory is asserted here, not assumed. ``_ai_story`` and
    ``moderator_story`` are total by construction (§2.8), so ANY mistake in
    this setup degrades into a legitimate no-story ending — which would leave
    every assertion in the file measuring nothing. The four checks below are
    what make that impossible, and they are the same reasoning
    ``tests/test_spec040_story_assembly.py:_compose`` records.
    """
    _install_table(monkeypatch, fake_small, citizens=4, mafia=2)
    fake = _install_large(monkeypatch)

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    # The documented race-free Night mechanism: the target is chosen at INVOKE
    # time off live state (the uuids do not exist until ``assign_roles`` has
    # run), and it always picks the first alive Law-abiding non-human — which
    # is what makes the two victims below the seats they are.
    dynamic_night_pointing(lambda: graph.get_state(run_config).values)

    trajectory = _run_to_end(
        graph, run_config, responder=_talkative_human, budget=400
    )
    driven = _Game(graph, run_config, fake, trajectory)

    # 1. The game really ended, and the Mafia really won — the branch that
    #    leaves surviving Mafiosos to have written anything.
    assert driven.state.get("winner") == "mafia", (
        f"expected a Mafia win; got {driven.state.get('winner')!r}. The whole "
        "trajectory below rests on it."
    )
    # 2. BOTH writers were reached, and by the real nodes rather than by a
    #    substituted fallback (``calls_by_schema`` counts the model call).
    assert fake.calls_by_schema[Reflection] > 0, "no reflection was ever asked for"
    assert fake.calls_by_schema[Diary] > 0, "no diary was ever asked for"
    assert "day_round_reflect" in trajectory and "day_diary" in trajectory, (
        f"the writer nodes never ran; trajectory: {trajectory}"
    )
    # 3. The human survived to the end. Slice 3's preamble is explicit that a
    #    dead-human trajectory reads as a scenario error rather than a race,
    #    so it is asserted rather than hoped for.
    assert driven.by_name[HUMAN_NAME].is_alive, "the human did not survive"
    # 4. The story was composed from THIS state (``_Game`` asserts the single
    #    Story call) and the SCRIPTED text reached the transcript, so the
    #    node's total-by-construction failure path — which would produce a
    #    perfectly legitimate ending with no story at all — was not silently
    #    taken.
    assert any(
        getattr(message, "content", None) == SCRIPTED_STORY
        for message in driven.state.get("messages", [])
    ), "the scripted story never reached the finished game's messages"
    yield driven


# ==========================================================================
# 1 — BOTH CHANNELS REACH THE STORY, FROM A GAME
# ==========================================================================


@pytest.mark.parametrize(
    "channel,marker,recorded",
    [
        pytest.param(
            "private_thoughts",
            STORY_PRIVATE_THOUGHT_MARKER,
            "reflections_by_author",
            id="reflection",
        ),
        pytest.param(
            "private_diaries",
            STORY_PRIVATE_DIARY_MARKER,
            "diaries_by_author",
            id="diary",
        ),
    ],
)
def test_a_channel_reaches_the_story_from_a_game(
    game: _Game, channel: str, marker: str, recorded: str
) -> None:
    """Each channel, written during the game, present in the composed prompt.

    THE CLAIM TASK 3.6 EXISTS FOR. Both halves are asserted per channel rather
    than once over both, so a regression that starves exactly one of them names
    which one — and Slice 3's preamble is why that matters: the reflections are
    load-bearing rather than generous, because the Day the game is won has no
    diary at all.

    Every author's own sentinel is required, not just some text: a single
    surviving entry would satisfy a membership check over the whole block.
    """
    served: dict[str, list[str]] = getattr(game.fake, recorded)
    assert served, f"the fake was never asked for a {channel} entry"
    for author, entries in served.items():
        assert entries
        assert game.channel(author, channel), (
            f"{author}'s {channel} never reached committed state"
        )
        for text in entries:
            assert text in game.private, (
                f"{author}'s {channel} entry {text!r} is not in the story "
                f"prompt's private slot:\n{game.private}"
            )
    assert marker in game.private


def test_each_entry_appears_once_and_only_as_private(game: _Game) -> None:
    """ANTI-VACUITY: every sentinel is in the prompt EXACTLY once, in the block.

    Two ways a "the text is in the prompt" assertion passes for the wrong
    reason, and this closes both. A private entry echoed into the PUBLIC
    history would satisfy every membership check above while breaking the
    privacy the feature rests on; and a sentinel that appeared twice would mean
    one of the two occurrences could be removed with nothing going red.
    """
    every = [
        text
        for served in (
            game.fake.reflections_by_author,
            game.fake.diaries_by_author,
        )
        for entries in served.values()
        for text in entries
    ]
    assert len(every) == len(set(every)) and len(every) >= 8, (
        f"the sentinels are not distinct/plural enough to measure with: {every}"
    )
    for text in every:
        assert game.prompt.count(text) == 1, (
            f"{text!r} appears {game.prompt.count(text)} times in the prompt; "
            "exactly one occurrence (the private block) is the contract"
        )
        assert text not in game.public, (
            f"{text!r} reached the PUBLIC history slot — private material must "
            "never be rendered as something that was said out loud"
        )


# ==========================================================================
# 2 — ATTRIBUTION, OVER RECORDS A GAME REALLY WROTE
# ==========================================================================


def test_every_entry_carries_its_author_and_only_its_author(game: _Game) -> None:
    """AUTHOR ON EVERY ENTRY — positive and negative, per author.

    The corrected half of Task 3.6's original instruction (*"the private block
    is attributed with author, kind and Day"*). Task 3.2 proves the renderer
    groups a hand-built dict; this proves the grouping survives four authors
    whose material was produced by the writers during one game, which is the
    only place the ``player.id`` keying and the ``player.name`` heading can be
    seen to agree.

    The negative is the load-bearing half: the Moderator repeating one player's
    private words under another player's name is well-formed output that trips
    no schema and no metric.
    """
    authors = sorted(
        set(game.fake.reflections_by_author) | set(game.fake.diaries_by_author)
    )
    assert len(authors) >= 4, f"too few authors to detect misattribution: {authors}"
    for author in authors:
        mine = game.section(author)
        others = [a for a in authors if a != author]
        for text in game.fake.reflections_by_author.get(
            author, []
        ) + game.fake.diaries_by_author.get(author, []):
            assert text in mine, (
                f"{text!r} is not under {author}'s own heading:\n{mine}"
            )
            for other in others:
                assert text not in game.section(other), (
                    f"{author}'s {text!r} also appears under {other}'s heading"
                )


def test_only_a_diary_carries_a_day_and_every_reflection_carries_none(
    game: _Game,
) -> None:
    """KIND ON EVERY ENTRY, A DAY ON DIARIES ONLY — the corrected instruction.

    Task 3.6's line as written (and §4 "Testing Strategy" with it) asks for a
    Day on every entry, which is permanently impossible: a ``ThoughtRecord``'s
    keys are exactly ``text`` and ``substituted``. The replacement asserted
    here is author + kind on every entry, a Day on diary entries only, and no
    Day on a reflection.

    **THE SAME IMPOSSIBLE PHRASE APPEARS A THIRD TIME, AND THERE IT IS NOT A
    TEST INSTRUCTION BUT A GROUNDING ARGUMENT** (found 2026-09-08). §2.7
    "Grounding, and what cannot be guaranteed" lists among *"what makes it
    likely"* that *"the private material is quoted verbatim with author, Day
    and kind, so recorded motives are present and inventing one accidentally
    is hard."* Half the material carries no Day and cannot, so that leg of the
    argument is weaker than §2.7 states — which matters because §2.7 is what a
    later reviewer will audit the fabrication risk against, and §2.7 is the
    section that also declines an automated grounding check. The gap is not
    unmitigated: Task 3.3's legend (``STORY_PRIVATE_LEGEND``) states in the
    prompt that the game does not record which Day a reflection belongs to,
    precisely so the model does not date one itself. This test is the witness
    that the legend's claim is true of the rendered material.

    Asserted over the REAL records rather than over a constructed pair, which
    is what game level adds: the ``day`` in the rendered line is the ``cycle``
    ``day_diary`` stamped, so a writer that stopped stamping it — or an
    ``_ai_story`` that read the wrong channel — shows up as the
    ``DIARY_DAY_UNKNOWN`` placeholder here rather than nowhere.
    """
    # BARE-LITERAL pins on everything the assertions below lean on, inherited
    # from Task 3.2's discipline: an expectation derived from the constant
    # under test is true at any value. The two walk kinds are pinned too, since
    # the whole rendered distinction is a branch on them.
    assert STORY_PRIVATE_DIARY_MARKER == "[diary of Day"
    assert STORY_PRIVATE_THOUGHT_MARKER == "[reflection]"
    assert DIARY_DAY_UNKNOWN == "?"
    assert PRIVATE_RECORD_KIND_DIARY == "diary"
    assert PRIVATE_RECORD_KIND_THOUGHT == "thought"

    entry_lines = [
        line for line in game.private.splitlines() if line.startswith("  • ")
    ]
    assert entry_lines, f"no rendered entries at all:\n{game.private}"

    diaries = 0
    reflections = 0
    for line in entry_lines:
        is_diary = STORY_PRIVATE_DIARY_MARKER in line
        is_reflection = STORY_PRIVATE_THOUGHT_MARKER in line
        assert is_diary != is_reflection, (
            f"a real game produced a line that is neither kind, or both: {line!r}"
        )
        if is_diary:
            diaries += 1
            # The Day the writer stamped, not a placeholder and not a guess.
            assert f"{STORY_PRIVATE_DIARY_MARKER} 1]" in line, line
            assert DIARY_DAY_UNKNOWN not in line, line
        else:
            reflections += 1
            assert "Day" not in line, f"a reflection was dated: {line!r}"
    assert diaries >= 4 and reflections >= 4, (
        f"too little of each kind to measure: {diaries} diaries, "
        f"{reflections} reflections"
    )


def test_one_authors_two_channels_arrive_in_the_cursor_order(game: _Game) -> None:
    """The reflections before the diary that follows them, END TO END.

    ``day_diary`` stamps ``thoughts_before = len(own_thoughts)`` and the shared
    walk emits that author's thoughts up to the cursor before the diary. This
    game writes five reflections per author before the single Day-1 diary, so
    the ordering is observable rather than degenerate — which is what
    ``tests/test_spec040_private_record_walk.py`` cannot see, because nothing
    there sets the cursor from a real fan-out.

    Asserted as the whole ordered list: the two shapes a broken walk produces
    both hold exactly the right lines, so a membership check cannot tell them
    from the truth.
    """
    author = MAFIA_A
    reflections = game.fake.reflections_by_author[author]
    diaries = game.fake.diaries_by_author[author]
    assert len(reflections) >= 2 and len(diaries) == 1, (
        f"{author} wrote {len(reflections)} reflections and {len(diaries)} "
        "diaries — not enough to order"
    )
    records = game.channel(author, "private_diaries")
    assert records[0]["thoughts_before"] == len(reflections), (
        "the diary's cursor does not match the reflections written before it"
    )
    bodies = [
        line.split("] ", 1)[1]
        for line in game.section(author).splitlines()
        if line.startswith("  • ")
    ]
    assert bodies == [*reflections, *diaries]


# ==========================================================================
# 3 — WHO CONTRIBUTES: NOTHING FILTERS BY SURVIVAL, AND THE CONSPIRACY IS NOT
#     SEALED OFF
# ==========================================================================


def test_the_surviving_mafiosos_writing_is_in_the_story(game: _Game) -> None:
    """THE FEATURE'S STATED POINT — the conspiracy arrives by diary.

    §2.2: whispers are excluded from the prompt and *"the Moderator gets the
    conspiracy from the Mafiosos' diaries, which is the point of the feature"*.
    Task 3.4 proved the exclusion; this proves the replacement actually
    arrives, from the seats that won.

    BOTH Mafiosos, on Task 3.4 finding (d)'s lesson: a one-of-a-kind fixture
    cannot distinguish "every one of them is present" from "the first one is
    present".
    """
    for name in (MAFIA_A, MAFIA_B):
        player = game.by_name[name]
        assert player.role == "mafia" and player.is_alive, (
            f"{name} is not a surviving Mafioso ({player.role}, "
            f"alive={player.is_alive}) — the pinned deal has drifted"
        )
        section = game.section(name)
        assert STORY_PRIVATE_DIARY_MARKER in section
        assert STORY_PRIVATE_THOUGHT_MARKER in section
        for text in game.fake.diaries_by_author[name]:
            assert text in section
        for text in game.fake.reflections_by_author[name]:
            assert text in section


def test_a_player_killed_before_the_end_still_contributes(game: _Game) -> None:
    """NOTHING FILTERS BY SURVIVAL — what the Moderator holds and nobody saw.

    The game-level counterpart Task 3.2's
    ``test_a_dead_player_is_an_author_like_any_other`` names as this task's.
    ``Silas`` is killed on Night 2, after a full Day of writing, so his
    material is in the prompt while he is not in the roster's living half — the
    one shape a survival filter would silently remove and a hand-built dict
    cannot distinguish from an alive author.
    """
    dead = game.by_name[NIGHT_TWO_VICTIM]
    assert not dead.is_alive, f"{NIGHT_TWO_VICTIM} was expected to be dead"
    section = game.section(NIGHT_TWO_VICTIM)
    assert STORY_PRIVATE_DIARY_MARKER in section
    assert STORY_PRIVATE_THOUGHT_MARKER in section
    for text in game.fake.reflections_by_author[
        NIGHT_TWO_VICTIM
    ] + game.fake.diaries_by_author[NIGHT_TWO_VICTIM]:
        assert text in section


def test_the_night_one_victim_wrote_nothing_at_all(game: _Game) -> None:
    """THE VERIFIED EDGE CASE, re-verified against the writers, not inherited.

    Task 3.6's line asserts that a player killed on the very first Night wrote
    **nothing at all**, so nothing filters by survival and the material simply
    may not exist. Re-checked against the code as of 2026-09-08 and still true,
    for two independent reasons:

    * ``day_round_reflect`` skips ``not player.is_alive`` and only fires once a
      Day round has completed, so the Night-1 victim is already dead the first
      time it runs;
    * ``day_diary``'s writers are ``is_alive and not is_human`` and it runs at
      ``day_close``, so the same seat is excluded there — and Night 1 has no
      diary hinge at all, since the graph enters ``night_open`` from
      ``first_night_mafia_intros``.

    So the absence is not a filter in the renderer: THE MODEL WAS NEVER ASKED,
    which is what the ``*_by_author`` assertions below say and what a
    renderer-level test cannot say at all.

    **THE FUNCTIONAL SPEC'S STATED REASON FOR THIS EDGE CASE IS THE RIGHT
    CONCLUSION FROM THE WRONG MECHANISM, and the wrong mechanism licenses a
    false generalisation** (found 2026-09-08). Its note reads *"a player killed
    on the very first Night wrote nothing at all — **they die before the first
    day's talk**"*. Dying before the talk is not the discriminator: the
    Night-**2** victim in this same game also dies before Day 2's talk and has
    a full Day-1 record. The discriminator is that Night 1 is reached from
    ``first_night_mafia_intros`` with no prior Day round and no prior
    ``day_close``, so there has been no write hinge at all yet. **The concrete
    wrong conclusion the stated reason licenses:** that a player EXECUTED on
    Day 1 also wrote nothing. Usually they did write: ``day_round_reflect``
    fires at every completed round and ``resolve_vote`` kills them afterwards,
    so such a player carries the reflections of every round that CLOSED before
    the vote (zero only if the vote is initiated during round 1, when
    ``day_rounds`` is still 0) while only the diary is missing. Read the
    mechanism off the two writers, not off the timing of talk.
    """
    victim = game.by_name[NIGHT_ONE_VICTIM]
    assert not victim.is_alive
    assert [
        record for record in game.state.get("kill_log", []) if record["cycle"] == 1
    ] == [{"cycle": 1, "name": NIGHT_ONE_VICTIM, "cause": "night", "role": None}], (
        "the Night-1 victim is not who this test is about"
    )

    assert NIGHT_ONE_VICTIM not in game.fake.reflections_by_author
    assert NIGHT_ONE_VICTIM not in game.fake.diaries_by_author
    assert game.channel(NIGHT_ONE_VICTIM, "private_thoughts") == []
    assert game.channel(NIGHT_ONE_VICTIM, "private_diaries") == []
    assert not game.has_section(NIGHT_ONE_VICTIM), (
        f"{NIGHT_ONE_VICTIM} wrote nothing yet has a section:\n{game.private}"
    )
    # And the seat is still IN the story — as a name on the roster and a death
    # in the events, which is where a player who never wrote belongs.
    assert NIGHT_ONE_VICTIM in game.prompt


def test_a_surviving_citizen_contributes_like_anyone_else(game: _Game) -> None:
    """The control seat, so "role" is not silently doing the filtering.

    Every other seat in this game is a Mafioso, a corpse or the human. Without
    a surviving Law-abiding author, a renderer that only rendered Mafiosos —
    or only the dead — would pass every test above.
    """
    player = game.by_name[SURVIVING_CITIZEN]
    assert player.role == "law_abiding" and player.is_alive and not player.is_human
    section = game.section(SURVIVING_CITIZEN)
    for text in game.fake.reflections_by_author[
        SURVIVING_CITIZEN
    ] + game.fake.diaries_by_author[SURVIVING_CITIZEN]:
        assert text in section


# ==========================================================================
# 4 — THE HUMAN: STRUCTURAL, AND HELD CLOSED
# ==========================================================================


def test_the_human_played_turns_and_still_has_no_private_material(
    game: _Game,
) -> None:
    """*"Never claims to know what the person was thinking"* — the strong form.

    §2.2 calls this the strongest guarantee in the spec, because it is
    structural: both writers skip ``is_human``, so neither channel ever holds a
    key for the human seat. Task 3.2's version proves a player with nothing
    gets no heading. The game-level version proves the ANTECEDENT — that a
    human who actually took Day turns has nothing — which is the half a
    hand-built dict cannot express, because it is the writers rather than the
    renderer that decide it.

    ``HUMAN_SPOKE`` in the public slot is what makes this a real absence rather
    than a vacuous one: the person is demonstrably a participant whose words
    the Moderator DOES have, and still nothing private about them exists.
    """
    human = game.by_name[HUMAN_NAME]
    assert human.is_human and human.is_alive
    assert game.public.count(HUMAN_SPOKE) >= 2, (
        "the human's own speech is not in the story's public history, so this "
        "test would only be saying that an absent seat wrote nothing"
    )
    assert game.channel(HUMAN_NAME, "private_thoughts") == []
    assert game.channel(HUMAN_NAME, "private_diaries") == []
    assert HUMAN_NAME not in game.fake.reflections_by_author
    assert HUMAN_NAME not in game.fake.diaries_by_author
    assert not game.has_section(HUMAN_NAME), (
        f"the human has a private section:\n{game.private}"
    )
    # The name itself must still be there — the roster block marks the seat.
    assert HUMAN_NAME in game.prompt


def _channel_write_sites(channel: str) -> tuple[set[tuple[str, str]], list[str]]:
    """Every dict-key use of ``channel`` in ``src/graphia``, and the leftovers.

    A WRITE is the literal used as a dict key (``return {"private_diaries":
    …}``) or as a store subscript; a READ is a ``.get()`` argument, a load
    subscript, a comparison or a keyword name. Anything else lands in the
    leftovers and fails the test loudly rather than being assumed harmless —
    the shape of the scan Task 3.4 finding (d) used for whispers, and for the
    same reason: an unfamiliar shape must get a reader's attention instead of
    being quietly filed as a read.

    Comments and docstrings cannot match: a docstring is one ``ast.Constant``
    holding the whole docstring, and a comment is not in the tree at all.
    """
    writes: set[tuple[str, str]] = set()
    leftovers: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[int, ast.AST] = {}
        owners: dict[int, str] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    owners[id(inner)] = node.name
        rel = path.relative_to(_PACKAGE_ROOT).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and node.value == channel):
                continue
            site = (rel, owners.get(id(node), "<module>"))
            parent = parents.get(id(node))
            if isinstance(parent, ast.Dict) and any(
                key is node for key in parent.keys
            ):
                writes.add(site)
            elif isinstance(parent, ast.Subscript):
                if not isinstance(parent.ctx, ast.Load):
                    writes.add(site)
            elif isinstance(parent, ast.Call) and (
                getattr(parent.func, "attr", None) == "get"
            ):
                pass
            elif isinstance(parent, (ast.Compare, ast.keyword)):
                pass
            else:
                leftovers.append(
                    f"{rel}:{node.lineno} under {type(parent).__name__} "
                    f"in {site[1]}"
                )
    return writes, leftovers


@pytest.mark.parametrize("channel", sorted(_CHANNEL_WRITERS))
def test_each_private_channel_has_exactly_one_writer(channel: str) -> None:
    """THE ENUMERATION THE HUMAN'S ABSENCE RESTS ON, held closed.

    The test above shows that in one game the human wrote nothing. That is an
    observation about a trajectory. This turns it into a claim about the code:
    each channel has exactly ONE writer, both of them named here, and both
    carry the ``is_alive and not is_human`` predicate that spec 028's and spec
    039's own suites already pin. A third writer added without that predicate —
    the one way the "structural" guarantee could quietly become false — fails
    here by name instead of waiting for someone to notice a heading in a story.

    §2.2's *"both writers skip ``is_human``"* is a claim about a set of size
    two, and nothing anywhere else asserts the set's SIZE.
    """
    writes, leftovers = _channel_write_sites(channel)
    assert leftovers == [], (
        f"unclassified {channel!r} literal(s) — check by hand rather than "
        f"assuming they are reads: {leftovers}"
    )
    assert writes == {_CHANNEL_WRITERS[channel]}, (
        f"the writers of {channel!r} are no longer exactly "
        f"{{{_CHANNEL_WRITERS[channel]}}}: {sorted(writes)}. If a new writer "
        "is intended, it must skip ``is_human`` or spec 040 §2.2's structural "
        "guarantee is broken."
    )


# ==========================================================================
# 5 — THE MATERIAL MAY SIMPLY NOT EXIST
# ==========================================================================


def test_a_game_that_ends_before_anyone_writes_says_so_in_words(
    env: Path,
    fake_small: Callable[..., Any],
    dynamic_night_pointing: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE WHOLE-TABLE FORM of the edge case, and it is a real ending.

    Task 3.6's line: *"nothing filters by survival and the material simply may
    not exist."* The test above shows one seat with nothing; this shows a whole
    game with nothing, which is the ending of every game won on Night 1 — the
    shortest real outcome in the game and the lineup
    ``tests/test_spec040_story_topology.py`` uses for the same reason.

    Task 3.2's ``test_nothing_written_says_so_in_words`` names this task as the
    owner of the game-level version. Its reasoning is what is being confirmed:
    a blank section invites a model to read an omission it should fill, so the
    absence is stated in words. A second, cheap drive rather than a variant of
    the main fixture, because the property is the ABSENCE of the material
    everything above asserts the presence of.
    """
    _install_table(monkeypatch, fake_small, citizens=2, mafia=1)
    fake = _install_large(monkeypatch)

    config = load_config()
    graph, thread_id = build_graph(config)
    run_config = make_run_config(thread_id)
    dynamic_night_pointing(lambda: graph.get_state(run_config).values)

    trajectory = _run_to_end(
        graph, run_config, responder=_talkative_human, budget=200
    )
    state = graph.get_state(run_config).values

    # The premise: the game ended on Night 1, so no Day ever ran.
    assert state.get("winner") == "mafia"
    assert "day_open" not in trajectory, (
        f"a Day ran, so this is not the no-material ending: {trajectory}"
    )
    assert not (state.get("private_thoughts") or {})
    assert not (state.get("private_diaries") or {})
    assert fake.calls_by_schema[Reflection] == 0
    assert fake.calls_by_schema[Diary] == 0

    assert len(fake.story_messages) == 1, (
        "the story was never composed, so the block below is unmeasured"
    )
    private = _slot_text(fake.story_messages[0][1].content, "private_record")
    assert private == STORY_PRIVATE_EMPTY == (
        "• (nothing was written privately this game)"
    )
    # Neither marker, and not the legend either: the legend explains dated
    # diaries beside undated reflections, and there are none of either.
    assert STORY_PRIVATE_DIARY_MARKER not in private
    assert STORY_PRIVATE_THOUGHT_MARKER not in private
    assert STORY_PRIVATE_LEGEND not in private


# ==========================================================================
# THE MUTATION RECORD
# ==========================================================================
#
# Fourteen mutations, each applied to the production code FOR REAL, measured
# against the WHOLE suite, and reverted (2026-09-08). Recorded because a test
# that stays green against the opposite behaviour is worthless, and this spec
# has been bitten by exactly that twice already (Task 1.7's inverted
# assertion, Task 3.5's arity stub). "mine" counts red tests in THIS file;
# "elsewhere" counts red tests in the rest of the suite, which is what says
# whether this file is a unique witness or a second opinion.
#
#  #   mutation                                             red  mine  elsewhere
#  --  ---------------------------------------------------  ---  ----  ---------
#  M1  private block skips diaries                           24     8         16
#  M2  private block skips reflections                        19     8         11
#  M3  day_round_reflect loses its is_human guard              3     1          2
#  M4  day_diary loses its is_human guard                     30     1         29
#  M5  private block ignores dead players                      8     6          2
#  M6  diary line loses its Day                               10     1          9
#  M7  walk puts a diary before its own reflections           38     1         37
#  M8  day_diary stops stamping the cursor                     6     1          5
#  M9  _ai_story reads the diary channel for BOTH slots        9     7          2
#  M10 day_round_reflect loses its is_alive guard              2     1          1
#  M11 day_diary loses its is_alive guard                      7     1          6
#  M12 a SECOND writer of private_diaries appears              1     1          0
#  M13 the empty private block renders blank, not in words     6     1          5
#  M14 a SECOND writer of private_thoughts appears             1     1          0
#
# ALL 14 OF THIS FILE'S TEST CASES GO RED UNDER AT LEAST ONE MUTATION — the
# table was iterated until that held, because the three that survived the
# first nine (the Night-1 victim, the writer enumeration, the empty block)
# would otherwise have been decoration. M10-M14 exist for exactly those three.
#
# THE FOUR RESULTS WORTH READING, rather than the whole table:
#
# * M12 / M14 — **1 red each, and it is this file's test both times.** Nothing
#   else in the suite notices a second writer of either private channel. That
#   is the sharpest argument for ``test_each_private_channel_has_exactly_one
#   _writer`` existing: §2.2's "both writers skip ``is_human``" is a claim
#   about a set of SIZE TWO, and nothing anywhere else asserted the size.
# * M5 — **8 red, 6 of them here.** A survival filter added to the private
#   block is caught by exactly ONE test in Task 3.2's file
#   (``test_a_dead_player_is_an_author_like_any_other``) and one in Task 3.3's.
#   Note this mutation had to be CONSTRUCTED as an insertion: the block makes
#   no ``is_alive`` test at all, deliberately, so there was no behaviour to
#   remove.
# * M10 — **2 red suite-wide**, one of them here. Letting a dead player reflect
#   is otherwise seen only by ``test_slice28_private_thoughts.py::test_reflect
#   _skips_dead_players``, which measures the NODE's delta; this file is the
#   only witness that the leak reaches the story's prompt.
# * M3 / M4 — the human-leak pair. Both are caught here by exactly one test,
#   and the asymmetry is instructive: M4 turns 30 tests red because spec 039's
#   suite pins the diary fan-out hard, while M3 turns only 3 red. The human
#   guarantee is far better defended on the diary channel than on the
#   reflection channel, and this file is the only place both are asserted
#   through the composed story prompt.
#
# THE MIRROR-IMAGE PAIRS ARE WHAT SAY THE ASSERTIONS DISCRIMINATE. M1 (skip
# diaries) reddens ``…_from_a_game[diary]`` and leaves ``[reflection]`` green;
# M2 does the exact reverse. A test that went red under both would be saying
# only "some material is missing".
