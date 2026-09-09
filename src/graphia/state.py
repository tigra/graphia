"""Typed state containers and reducers for the Graphia game graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


# The ``additional_kwargs`` key that marks the Moderator's closing story
# (spec 040 — The Moderator's Closing Story) — a PUBLIC contract constant,
# and deliberately in THIS module rather than in ``nodes/endgame.py`` where the
# message is built.
#
# ONE FLAG, FOUR CONSUMERS IN THREE LAYERS, AND THEY MUST AGREE. **This
# inventory was wrong when it was written, and it is corrected rather than
# re-stated (Task 5.3, 2026-09-09): it said THREE, listed two readers that do
# not exist yet, and omitted the only one that does.** Present tense now means
# live; where it is not, the slice that builds it is named.
#
# - ``nodes.endgame.moderator_story`` SETS it on the story's public
#   ``SystemMessage`` (Moderator voice, no ``name``) — live.
# - ``ui.app.GraphiaApp._write_public`` READS it to wrap the one write whose
#   failure costs more than itself in a narrow markup guard (§2.11) — live,
#   and today the ONLY reader. Slice 2 added it after this list was written,
#   which is how it came to be missing from it.
# - ``tools.eval_transcript`` WILL read it to partition an endgame delta's
#   ``SystemMessage``s into ``(facts, story)`` and render the ``<story>``
#   block — **Slice 8** (Task 8.3 builds ``_render_story``, Task 8.5
#   registers the tag). Verified 2026-09-09: that module contains zero
#   occurrences of "story", case-insensitive.
# - ``tools.blunder_eval`` WILL read it to decide whether a game produced a
#   story at all — the ledger's ``quality.story_missing_*`` signal, **Slice 9,
#   Task 9.1**. Verified: zero occurrences there too.
#
# DISCRIMINATE ON THE MESSAGE, NOT ON THE NODE OR THE POSITION (spec 040 §2.9).
# Both readers see a stream of messages rather than a call graph: "the last
# ``SystemMessage`` of the endgame delta" holds only until something else is
# appended to the ending, and "whatever ``moderator_story`` emitted" is not
# recoverable from a capture that records the delta and not its author. A flag
# carried ON the message survives both.
#
# WHY PUBLIC, AND WHY HERE — A SPEC-039 LESSON ALREADY PAID FOR ONCE. Spec 039
# (Per-AI Private Diaries) left ``blunder_eval._diary_fallback_text`` reaching
# LAZILY into ``nodes.day._DIARY_FALLBACK``, a module-PRIVATE constant, and
# spending fifteen docstring lines justifying it: lazily because ``nodes.day``
# drags in the whole gameplay stack (``diary_store``, ``career_events``, the
# prompts) that the no-model ledger viewer has no business loading, and
# defensively — ``try/except`` returning ``None`` — so a rename degrades a
# measurement to silence rather than failing. None of that is needed here, and
# both halves of the reason were checked rather than assumed:
#
# - ``graphia.state`` drags in NO gameplay stack. Importing it pulls no
#   ``boto3``/``botocore``, no ``langchain_aws``, no ``langchain_ollama``, and
#   none of ``graphia.llm`` / ``nodes`` / ``prompts`` / ``graph``. It needs only
#   ``langchain_core.messages`` and ``langgraph.graph.message``.
# - BOTH eval-layer modules already import from it at module scope
#   (``eval_transcript``: ``PlayerPersona, PlayerState, thought_text``;
#   ``blunder_eval``: ``DiaryRecord, PlayerState``), and both already import
#   ``langchain_core.messages`` on their own account. So the marginal cost of
#   reaching this constant is zero new imports; measured, this module's own body
#   costs 1.4 ms atop the two it needs.
#
# IMPORT IT, NEVER COPY THE LITERAL — 039's rule, and the whole point of a
# shared name: a reword then breaks loudly in the offline suite instead of
# silently drifting a measurement. A copied ``"closing_story"`` in
# ``blunder_eval`` would report a clean ``story_missing_rate: 0.0`` for a run in
# which every single story was in fact missing.
#
# THE SIBLING FLAG HAS LANDED (Task 5.3) AND IT IS DECLARED BELOW, at the name
# and in the place this comment reserved for it. No container was built to hold
# the family — not a ``dict``, not an ``Enum``, not a namespace class — because
# every read is ``extra.get(KEY)`` against ``additional_kwargs``, where a bare
# ``str`` is exactly the right shape, and a container would buy an indirection
# at every call site above in exchange for holding a handful of short strings.
# (This clause said "the pair" and "two strings" until Slice 7 declared a third,
# ``ENDGAME_FACTS_KWARG``; the reasoning is per-flag and unaffected by the
# count, which is why the count is no longer written into it.) This project's
# precedent is prefix families, never containers: ``KIND_*`` in
# ``career_events``, and the ``DIARY_*`` family that spans two modules
# (``DIARY_LINE_MARKER`` / ``DIARY_WINDOW`` / ``DIARY_MAX_CHARS`` in
# ``nodes.day``, ``DIARY_SENTENCE_BOUND`` in ``prompts``) held together by
# nothing but the prefix. And the reason the sibling lives HERE rather than in
# ``nodes/endgame.py`` beside its writer is the same 039 lesson: it has a
# cross-layer reader coming (Slice 8's transcript renderer emits the
# attribute), so declaring it next to the message builder would re-create
# precisely the coupling this module exists to avoid.
CLOSING_STORY_KWARG = "closing_story"

# The second, INDEPENDENT machine flag on the same message: the story came back
# longer than the bound and ``nodes.endgame._clamp_story`` dropped at least one
# whole paragraph (spec 040 §2.5, Task 5.3).
#
# PRESENT ONLY WHEN THE CAP ACTUALLY FIRED, never ``False`` (§2.5's "when the
# cap actually fires"; Task 5.4's "marked only when the cap fired, and absent
# otherwise"). Three reasons, and the third is the one that costs something:
#
# 1. A reader is ``extra.get(KEY)``, so absent and ``False`` are the same
#    answer — a stored ``False`` is a key that means nothing.
# 2. The transcript half renders an ATTRIBUTE, and ``<story truncated="false">``
#    on all but a handful of stories is noise in a corpus a person reads.
# 3. ``tests/test_spec040_story_node.py`` asserts ``story.additional_kwargs ==
#    {CLOSING_STORY_KWARG: True}`` — exact dict equality — for an unclipped
#    story. Always-set turns that red, and it is right to: the unclipped story
#    is the one whose ending must read exactly as it did before this task.
#
# WHY IT IS NOT DERIVABLE DOWNSTREAM, which is the whole reason it is carried
# at all: a story that is exactly ``STORY_MAX_PARAGRAPHS`` paragraphs long is
# indistinguishable from a longer one that lost its last, so nothing that sees
# only the message can recover the fact. It is knowable in exactly one place —
# inside ``_clamp_story``, between the split and the join — and from there it is
# plumbed out through ``_ai_story``'s return.
#
# WHAT IT IS FOR (§2.5): a reviewer judging the prose of a story that stops
# early must attribute the stop to the CAP and not to the storyteller. Without
# the flag they draw exactly the wrong conclusion about the one thing the whole
# feature exists to be judged on.
#
# DELIBERATELY NOT IN ``eval_transcript``'s ``_ATTR_NAMES``, following spec
# 038's ratified treatment of ``human="true"``: a machine flag is not a detail
# a reviewer reads, so it stays part of the tag's punctuation. Task 8.5 says
# the same thing independently — the two lines agreed before they met.
#
# ZERO READERS TODAY, and that is a resequencing rather than dead weight. The
# transcript element that carries the attribute does not exist yet: Task 8.3
# builds ``_render_story``, Task 8.5 registers the tag, Task 8.6 tests it. An
# attribute cannot precede its element, so Task 5.3 shipped the message flag
# and handed ``<story truncated="true">`` to Slice 8.
CLOSING_STORY_TRUNCATED_KWARG = "closing_story_truncated"

# The THIRD member of the family, and the only one that rides on ``end_screen``'s
# message rather than on the story's: this marks the FACTS message — the winner
# line, the kill log, the roster reveal and the persona reveal that
# ``nodes.endgame.end_screen`` emits as one public ``SystemMessage`` (spec 040
# §2.11, Task 5 -> Slice 7).
#
# WHY A MARKER AT ALL, rather than recognising the facts some other way. The UI
# has to know the facts have landed, because that is the instant the closing
# story's call goes in flight and the "one moment…" beat must arm (Slice 7).
# ``GraphiaApp._track_story_wait`` is the reader. Three candidates were on the
# table and only this one is both precise and decoupled:
#
# 1. "Any public ``SystemMessage``" — NOT SPECIFIC ENOUGH, and measurably so:
#    ``nodes.day``'s Day-round recap is also a plain public ``SystemMessage``,
#    so the beat would arm several times a game, mid-Day, on a story that is
#    not coming.
# 2. Sniffing ``prompts.ENDGAME_HEADER_ROSTER`` out of the content — works
#    today, and couples the UI pane to the facts message's PROSE. A reword of a
#    display string would then silently disarm a behaviour that has nothing to
#    do with wording. (This is exactly the coupling ``CLOSING_STORY_KWARG``
#    exists to avoid one message later; the tests may select on content —
#    ``conftest.endgame_facts_message`` does — because a test asserting about
#    the reveal legitimately means "the message a person reads the reveal in".)
# 3. Arming on something in ``GraphiaApp._drive`` instead of on the message —
#    NOT AVAILABLE: ``_game_over`` flips inside ``_enter_endgame``, which runs
#    AFTER ``drive_graph`` returns, i.e. after the story has already arrived or
#    failed. There is nothing to arm on there but the end of the wait.
#
# A SECOND READER IS EXPECTED AND HAS ITS HOOK HERE (Task 4.4's recommended
# follow-up, logged for Slice 10): ``_ai_story`` should probably EXCLUDE the
# facts message from its ``{context}`` slot, because ``_render_context`` hands
# the whole fact sheet to the model under the heading "What was said out loud,
# oldest first" — text never said out loud — while the system prompt is telling
# it the reader needs none of that again. Task 4.4 named this flag's shape as
# how that filter would be written. **Slice 7 deliberately did NOT implement
# it**: ``tests/test_spec040_story_assembly.py``'s
# ``test_the_facts_message_reaches_the_public_history_slot`` is the standing
# witness for the present behaviour and goes red on purpose when someone does.
#
# NOT AN ``eval_transcript`` DISCRIMINATOR, and Slice 8 should not make it one.
# Task 8.2 partitions the endgame delta on ``CLOSING_STORY_KWARG`` — the story's
# own flag — which already yields ``(facts, story)`` from one test, and reading
# both flags would make a message with neither ambiguous instead of simply
# "the facts".
ENDGAME_FACTS_KWARG = "endgame_facts"


def _merge_private_thoughts(
    prior: dict[str, list[ThoughtEntry]] | None,
    incoming: dict[str, list[ThoughtEntry]] | None,
) -> dict[str, list[ThoughtEntry]]:
    """Accumulate per-player private thoughts (spec 028) — the channel reducer.

    The per-key analogue of ``operator.add`` on lists: given the prior map and
    an incoming delta map, return a NEW map where each player's list is
    ``prior + incoming`` (concatenation, preserving the order the notes were
    written). A plain ``dict`` merge would let a later delta's list CLOBBER an
    earlier one — the bug this reducer exists to prevent — so each per-player
    list is concatenated, not replaced.

    PURE (copy-not-mutate): neither input is mutated — the prior map is shallow-
    copied and each touched player's list is rebuilt as a fresh ``list`` — so
    checkpoint replay is stable. It iterates only ``dict`` insertion order
    (never a ``set``), so the order is deterministic and the dual-mode
    byte-equal smoke is unaffected. ``add_messages`` is the custom-reducer
    precedent; ``kill_log``'s ``operator.add`` the accumulation precedent.

    SHAPE-AGNOSTIC OVER THE ELEMENT, which is what spec 041's reshape of this
    channel turns from an accident into a property worth naming. The body
    concatenates and copies lists; it never subscripts an element, never reads a
    field off one, and never compares two. So widening the element from ``str``
    to :data:`ThoughtEntry` (``str | ThoughtRecord``) is a TYPE-LEVEL change
    here and nothing more — no branch, no behavioural difference, and a mixed
    list (a resumed checkpoint's strings followed by this session's records)
    merges exactly like a uniform one. That is precisely the property the
    incremental landing depends on: the reducer did not have to be sequenced
    against the writers.
    """
    merged: dict[str, list[ThoughtEntry]] = {
        player_id: list(notes) for player_id, notes in (prior or {}).items()
    }
    for player_id, notes in (incoming or {}).items():
        merged[player_id] = [*merged.get(player_id, []), *notes]
    return merged


def _merge_private_diaries(
    prior: dict[str, list[DiaryRecord]] | None,
    incoming: dict[str, list[DiaryRecord]] | None,
) -> dict[str, list[DiaryRecord]]:
    """Accumulate per-player private diaries (spec 039) — the channel reducer.

    The exact structural twin of :func:`_merge_private_thoughts` above, over
    :class:`DiaryRecord` values (defined below with the other state
    ``TypedDict``s) rather than that channel's :data:`ThoughtEntry`: given the
    prior map and an
    incoming delta map, return a NEW map where each player's list is
    ``prior + incoming`` (concatenation, preserving the order the entries were
    written). A plain ``dict`` merge would let a later Day's delta CLOBBER the
    earlier Days' entries — the bug both reducers exist to prevent.

    DELIBERATELY DUPLICATED, NOT GENERALIZED (spec 039 tech-spec §2.2). Spec
    028's ``private_thoughts`` is a separate channel that spec 039 leaves
    untouched, and its reducer is part of that channel: folding the two into
    one generic helper would give a future diary change the power to alter
    028's behaviour. Six duplicated lines is the cheaper side of that trade.
    Keep the pair in step by hand — a fix to one is almost certainly a fix to
    both.

    THE TEMPTATION TO FOLD THESE TWO GOT STRONGER, NOT WEAKER, WITH SPEC 041,
    which is why this warning is being reinforced rather than left as it stood.
    The original argument had an easy tell: one reducer was over ``str`` and the
    other over a record, so they merely looked similar. Now that 041 has
    reshaped 028's element into :class:`ThoughtRecord`, the two bodies are
    element-agnostic concatenations that differ in NOTHING but their type
    annotation — so "obviously the same function twice" is now the honest first
    impression and the reason to keep them apart is entirely the ownership one
    above. Do not fold them.

    PURE (copy-not-mutate): neither input is mutated — the prior map is shallow-
    copied and each touched player's list is rebuilt as a fresh ``list`` — so
    checkpoint replay is stable. It iterates only ``dict`` insertion order
    (never a ``set``), so the order is deterministic and the dual-mode
    byte-equal smoke is unaffected.
    """
    merged: dict[str, list[DiaryRecord]] = {
        player_id: list(entries) for player_id, entries in (prior or {}).items()
    }
    for player_id, entries in (incoming or {}).items():
        merged[player_id] = [*merged.get(player_id, []), *entries]
    return merged


@dataclass(frozen=True)
class PlayerPersona:
    """A player's persona: a personality, a manner of speaking, and a backstory.

    ``public_persona`` is the face shown to the table — a Mafioso's cover legend
    or a Citizen's honest self; ``true_self`` is a Mafioso's real backstory and
    is empty for Citizens. Pure in-game state attached to :class:`PlayerState`;
    it gets a clean default repr like the rest of the state and carries no
    serialization machinery.
    """

    personality: str
    manner: str
    public_persona: str
    true_self: str


@dataclass
class PlayerState:
    id: str
    name: str
    role: Literal["mafia", "law_abiding"]
    is_human: bool
    is_alive: bool = True
    persona: PlayerPersona | None = None


class KillRecord(TypedDict, total=False):
    cycle: int
    name: str
    cause: Literal["night", "execution"]
    role: str | None


class ActiveVote(TypedDict, total=False):
    initiator: str
    target: str
    ballots: dict[str, Literal["yes", "no"]]
    pending: list[str]


class DiaryRecord(TypedDict, total=False):
    """One before-Night diary entry for one player (spec 039).

    A ``TypedDict`` — A HARD CONSTRAINT, NOT A STYLE PREFERENCE. Do not
    "improve" this into a dataclass. :class:`KillRecord` and
    :class:`ActiveVote` above are ``TypedDict``s for the same reason.

    ``graph.py:make_checkpoint_serde`` builds the ``JsonPlusSerializer`` with
    ``allowed_msgpack_modules=[PlayerState, PlayerPersona]``, and passing an
    explicit allowlist switches OFF langgraph's permissive warn-and-allow
    default for custom classes — verified against the installed
    ``langgraph/checkpoint/serde/jsonplus.py``, whose ext hook takes the
    warn-and-allow branch only when the allowlist is the literal ``True`` that
    the no-argument default resolves to. Any custom class not on that list is
    refused on the way back in.

    The refusal is WORSE THAN AN EXCEPTION, which is why it is written down
    here: the ext hook logs "Blocked deserialization of ..." and then returns
    the raw kwargs ``dict``. A dataclass ``DiaryRecord`` would therefore come
    back from a checkpoint as a bare ``dict``, and ``record.text`` would raise
    ``AttributeError`` only AFTER an interrupt/resume — invisible to any test
    that never resumes. A ``TypedDict`` *is* a ``dict`` at runtime, so it never
    reaches the ext hook and round-trips exactly.

    ``substituted`` (spec 041 Slice 8) rides that same guarantee and needed NO
    change to ``make_checkpoint_serde``'s allowlist, for exactly the reason
    argued above: adding a key to a ``TypedDict`` adds a key to a ``dict``, and
    a ``dict`` never reaches the ext hook at all. ``total=False`` covers the
    other direction — a record that predates the key (one resumed from a
    checkpoint written before it existed, or hand-built in a test) is still a
    valid ``DiaryRecord``, and every read goes through ``.get``, so its absence
    reports the entry as authored, which is what it was.

    Fields
    ------

    ``day``
        The Day cycle this entry sums up — ``state["cycle"]`` at write time,
        before ``night_open`` bumps it. Labels the entry in the prompt block
        and in the transcript, and the diary store's ``night_index`` derives
        from it as ``day + 1``: the Night the entry precedes.

    ``thoughts_before``
        THE CROSS-CHANNEL CURSOR — load-bearing, not redundant bookkeeping.
        See below before removing it.

    ``text``
        The entry itself.

    ``substituted``
        PRESENT ONLY WHEN TRUE — the entry is the node's deterministic
        stand-in (``nodes.day._DIARY_FALLBACK``) rather than the player's own
        writing (spec 041 Slice 8). ``day_diary`` writes it from the mark
        ``_ai_diary`` stamps in its single substitution branch, so the fact is
        CARRIED out of the code that substituted; it is never recovered by
        comparing ``text`` against the placeholder sentence. Spec 041 Task 7.1
        measured why that distinction is not pedantry: a model scripted to
        answer with ``_DIARY_FALLBACK``'s exact sentence produced a ledger
        record reading ``diary_fallback_rate: 1.0`` (18/18 by text match)
        beside ``substitutions.diary`` at rate 0.0 (0/18 at the source).
        Nothing had substituted, and no amount of text inspection can tell the
        two cases apart — so this key is the ONLY record of its fact.

        An AUTHORED entry OMITS the key entirely rather than carrying
        ``False``, and that asymmetry is load-bearing downstream: the eval
        transcript emits ``substituted="true"`` as a conditional attribute, so
        an authored entry renders byte-identically to every transcript
        preserved before this key existed.

    The cursor, and why nothing simpler works
    -----------------------------------------

    Diaries and spec 028's ``private_thoughts`` have to render as ONE private
    record in event order, but 028's channel carries NO ORDERING METADATA, and
    spec 039 must not touch that channel. (It held bare ``str`` entries when
    this was written; spec 041 reshaped the element to
    :class:`ThoughtRecord`, which deliberately still carries no ordering field
    — see that class — so this whole argument is unchanged, and the phrasing
    above is now about metadata rather than about strings.) So all the
    ordering information lives on this side: each entry records
    ``len(state["private_thoughts"].get(player_id, []))`` as observed at write
    time. The merge then walks the diary list in order, emitting every
    not-yet-emitted thought at index ``< thoughts_before`` ahead of its entry,
    and the remainder after the last one.

    This is exact rather than approximate because the writing node reads
    committed post-reducer state — no wall clock, no RNG, so a replay
    recomputes the same number — because thoughts only ever accumulate (their
    reducer concatenates and never truncates) so the cursors are
    non-decreasing, and because the two writing nodes are never in the same
    super-step.

    Every simpler-looking alternative was checked and fails (tech-spec §2.3):
    a shared sequence number stamped on both channels edits 028's channel;
    wall-clock timestamps are non-deterministic and break both replay
    stability and ``tests/test_dual_mode_smoke.py``'s byte-equality; a
    ``(cycle, round)`` coordinate on diaries alone cannot be ordered against an
    untagged list of strings; and deriving the position at render time fails
    for the same reason, since the number of thoughts in a Day varies whenever
    an execution or the vote cap closes that Day early.
    """

    day: int
    thoughts_before: int
    text: str
    substituted: bool


class ThoughtRecord(TypedDict, total=False):
    """One Day-round private thought for one player (spec 028, reshaped by 041).

    DEFINED AFTER :class:`DiaryRecord` DELIBERATELY, though spec 028's channel
    is declared before spec 039's in :class:`GameState` below. This record's
    entire justification is *the argument already made in ``DiaryRecord``'s
    docstring* — read that one first; this one only records what differs. A
    future tidy-up that reorders these two to match the channel order would put
    the citation before the thing it cites.

    A ``TypedDict``, ON THE SAME HARD CONSTRAINT, for the same reason, with the
    same consequence — see :class:`DiaryRecord`. Restated only as far as the
    conclusion: a dataclass here would come back from an interrupt/resume as a
    bare ``dict`` and read ``.text`` as ``AttributeError``, and it would do so
    ONLY after a resume, invisible to any test that never resumes. Because a
    ``TypedDict`` *is* a ``dict`` at runtime it never reaches the ext hook, so
    adding this class needed NO change to ``graph.py:make_checkpoint_serde``'s
    ``allowed_msgpack_modules`` allowlist — verified by round trip, not
    inferred.

    WHAT DIFFERS FROM ``DiaryRecord``, AND WHY IT IS NOT A COPY OF IT:

    - NO ``day``. A thought is written per Day *round*; only a diary sums up a
      whole Day. The transcript renders a thought inside its ``<round>``, so
      the round is positional there and needs no field.
    - NO ``thoughts_before``. That cursor exists on the diary side precisely
      because THIS channel carries no ordering metadata (see "The cursor" in
      :class:`DiaryRecord`). Adding a counterpart here would create a second,
      independently-drifting source of order for the same merge.
    - THE COUNT-BASED CURSOR IS UNAFFECTED BY THIS RESHAPE, which is the one
      cross-spec claim worth stating outright rather than leaving to be
      rediscovered. ``thoughts_before`` is ``len(own_thoughts)`` — a count of
      LIST ELEMENTS, never of characters and never derived from any element's
      contents — and ``day_round_reflect`` appends exactly one element per
      player per round both before and after this change. ``len`` of a list of
      ``str`` and ``len`` of a list of ``ThoughtRecord`` are the same number,
      and every use of the cursor downstream is a slice bound over positions
      (``notes[emitted:stop]``, ``min(cursor, len(notes))``), so no arithmetic
      anywhere reads an element's value.

    Fields
    ------

    ``text``
        The thought itself. Read through :func:`thought_text`, NOT by
        subscripting: during and after this reshape the channel legitimately
        holds bare ``str`` entries as well (see that function).

    ``substituted``
        PRESENT ONLY WHEN TRUE — the thought is ``nodes.day._REFLECTION_FALLBACK``,
        the node's deterministic stand-in, rather than the player's own
        reflection. Carried out of ``_ai_reflect``'s single substitution branch
        rather than recovered by comparing ``text`` against the placeholder:
        spec 041 Task 7.1 measured a model scripted to answer with the
        placeholder's exact sentence and got a text-match rate of 1.0 against a
        true substitution rate of 0.0, so the text provably cannot report this
        fact. An AUTHORED thought OMITS the key rather than carrying ``False``,
        the same load-bearing asymmetry ``DiaryRecord.substituted`` documents:
        it is what lets the eval transcript's conditional attribute leave every
        honest thought rendering byte-identically to the preserved transcripts.
    """

    text: str
    substituted: bool


# What one entry in the ``private_thoughts`` channel may be. NOT a transitional
# convenience to be narrowed to ``ThoughtRecord`` later — an honest permanent
# type, because a bare ``str`` remains a valid entry by design:
#
# - A checkpoint resumed mid-game was written before the reshape.
# - Roughly a hundred references across the offline suite hand-build this
#   channel as lists of plain strings, and the backward-compatible read helper
#   below exists exactly so they stay correct rather than being rewritten.
#
# Nothing narrows this union without first converting every one of those.
ThoughtEntry = str | ThoughtRecord


def thought_text(entry: object) -> str:
    """Read the text out of ONE ``private_thoughts`` entry, whatever its shape.

    THE BACKWARD-COMPATIBILITY SEAM for spec 041's reshape of spec 028's
    channel, and the reason that reshape can land incrementally instead of as
    one unreviewable change. ``private_thoughts`` has ~225 references across
    ``src/`` and ``tests/`` (measured, not estimated); a shape change applied at
    all of them in one move is not reviewable. Every reader that only ever
    wanted the words routes through here and keeps working against BOTH shapes,
    so the diff shrinks to the writers plus the few readers that genuinely need
    the new key.

    Accepts, in order of what it is actually handed:

    - a ``str`` — returned as-is. The pre-041 shape, still what a resumed
      checkpoint and most of the offline suite hold.
    - a :class:`ThoughtRecord` (or any ``dict``) — returns ``text`` when that is
      a ``str``.
    - anything else, including a ``dict`` with a missing or non-``str`` ``text``
      — returns ``""``.

    DEGRADES RATHER THAN RAISES, and that posture is inherited, not invented:
    every caller is on a prompt-build or transcript-render path, where
    ``eval_transcript._append_diaries`` and ``nodes.day._private_record_block``
    already read a ``total=False`` record field-by-field through ``.get`` so a
    malformed entry costs one line of output instead of aborting a game. This
    is the same contract at the element level.

    ``""`` MEANS "NOTHING USABLE HERE", AND THE CALLER OWNS WHAT TO DO WITH IT.
    This function does not skip blanks, because its two families of caller want
    opposite things: the transcript renderers already drop empty text
    (``if not text: continue``), while ``_private_thoughts_block`` renders one
    ``- `` bullet per entry unconditionally and would today render a bare ``- ``
    for an empty ``str`` too. Filtering here would silently change the second
    behaviour under cover of a compatibility helper.

    PURE: no state read, no RNG, no LLM, no ``set`` iteration, so
    ``tests/test_dual_mode_smoke.py``'s byte-equality is unaffected.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        # ``ThoughtRecord`` is ``total=False`` and is a plain ``dict`` at
        # runtime, so ``text`` may be absent (an entry predating the key) or
        # unusable; both report as "nothing usable" rather than raising.
        text = entry.get("text")
        if isinstance(text, str):
            return text
    return ""


class GameState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    players: dict[str, PlayerState]
    human_id: str
    # The human's dealt faction ("mafia"/"law_abiding") lifted to a top-level
    # plain string so it survives remote-mode serialization: PlayerState is not
    # a LangChain Serializable, so client-side it crosses the wire as its repr
    # string and ``players[human_id].role`` is unavailable. summarize() reads
    # this field instead. Set server-side in assign_roles where roles are dealt.
    human_role: str
    phase: Literal["setup", "night", "day", "end"]
    cycle: int
    night_picks: dict[str, str]
    # Multi-round Mafia consensus (Spec 015 §2.2) — all plain-replace, reset in
    # night_open beside night_picks. night_round is the current round (1–3);
    # night_mafia_order is the round's shuffled living-Mafioso ids (empty ⇒
    # reshuffle on next mafia_point); night_pointer_index is the cursor within
    # it; night_round_picks is mafioso_id → target_id for the current (deciding)
    # round; night_rounds_log holds completed rounds' pick dicts.
    night_round: int
    night_mafia_order: list[str]
    night_pointer_index: int
    night_round_picks: dict[str, str]
    night_rounds_log: list[dict[str, str]]
    # Randomized Night-pointing roster order (spec 030, ADR 011). The per-round
    # frozen presentation order of the living Law-abiding candidate ids, computed
    # ONCE in the interrupt-free ``mafia_round_start`` super-step (so the order is
    # committed before any human pointer is prompted and never re-drawn on a
    # resume) and read back in ``mafia_point`` — applied at the single candidate-
    # assembly point so both the AI roster render and the human ``"point"``
    # interrupt ``options`` see one consistent, replay-safe order. Plain-replace
    # reducer, repopulated each round beside ``night_mafia_order``. When the
    # ablation flag is OFF the order is plain ``players``-dict insertion order
    # (no RNG draw) — the prior behaviour, byte-for-byte.
    night_law_order: list[str]
    day_order: list[str]
    day_turn_index: int
    day_rounds: int
    day_votes_called: int
    # Count of execution votes CALLED so far this Day (any initiator). Plain
    # replace, reset to 0 in day_open and incremented by 1 at BOTH vote-
    # initiation sites in day_turn (the human "/vote" branch and the AI
    # DayAction(kind="vote") branch). Counts INITIATIONS, not resolutions:
    # distinct from day_votes_called (which counts only FAILED votes) — a
    # successful execution returns from resolve_vote without bumping that,
    # and AI-initiated votes bump no per-Day counter, so neither is a correct
    # "votes called this Day" source. The recap renderer reads this field.
    day_votes_initiated: int
    human_votes_called: int
    human_ballots_cast: int
    human_night_attempts: int
    human_night_successes: int
    night_victim_count: int
    execution_count: int
    active_vote: ActiveVote | None
    # Carries a validation error (e.g. a bad ``/vote`` target) forward to the
    # NEXT ``day_turn`` execution so the human can be re-prompted with the hint
    # via a single ``interrupt()`` per node execution. A graph loop — not a
    # second in-node ``interrupt()`` — drives the re-prompt; this keeps
    # ``snapshot.next`` reliable for the driver (a second in-node interrupt
    # empties ``snapshot.next`` while the interrupt is still pending, which the
    # driver misreads as game-over). Cleared (set to None) on any accepted
    # human turn.
    day_turn_error: str | None
    kill_log: Annotated[list[KillRecord], operator.add]
    # ``"runaway"`` (spec 023) is the whole-game Day-cap hit — a stuck/looping
    # game flagged as unresolved, distinct from a real win and from ``"draw"``.
    # ``"draw"`` is retained for back-compat/defensive rendering though no live
    # path now produces it.
    winner: Literal["law_abiding", "mafia", "draw", "runaway"] | None
    # Per-AI Day-round private thoughts (spec 028). Each surviving AI player's
    # private end-of-round reflections accumulate here, keyed by player id, in
    # the order written, via the ``_merge_private_thoughts`` accumulating
    # reducer. A within-game working scratchpad: NEVER a public ``messages``
    # entry, NEVER carrying ``private_to`` (which would route a note into another
    # reader's context — the opposite of the privacy invariant). Read ONLY by the
    # per-player prompt builders (each player sees only its own
    # ``private_thoughts.get(player.id, [])``) and the eval-transcript renderer.
    #
    # Spec 041 reshaped the ELEMENT from a bare ``str`` to :class:`ThoughtRecord`
    # so a substituted thought can be marked at its source. The declared element
    # type is the UNION :data:`ThoughtEntry`, not ``ThoughtRecord`` alone, and
    # that is accurate rather than lax: a mid-game resumed checkpoint holds
    # strings, and the offline suite hand-builds this channel as strings in
    # ~19 modules. Read every entry through :func:`thought_text`, which accepts
    # either; do not subscript an entry directly.
    private_thoughts: Annotated[
        dict[str, list[ThoughtEntry]], _merge_private_thoughts
    ]
    # Per-AI before-Night diary entries (spec 039). One entry per surviving
    # AI player per Day, written at the Day→Night hinge and accumulated here
    # keyed by player id, in the order written, via the
    # ``_merge_private_diaries`` accumulating reducer. The same privacy
    # invariant as ``private_thoughts`` above: NEVER a public ``messages``
    # entry, NEVER carrying ``private_to`` (which would route one player's
    # diary into another reader's context — the opposite of the invariant).
    # Read ONLY by the per-player prompt builders (each keyed on the acting
    # player's own id) and the eval-transcript renderer. A channel parallel to
    # ``private_thoughts`` rather than folded into it: the two stay independent
    # and are merged for display only, on each entry's ``thoughts_before``
    # cursor (see :class:`DiaryRecord`).
    private_diaries: Annotated[
        dict[str, list[DiaryRecord]], _merge_private_diaries
    ]
