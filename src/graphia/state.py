"""Typed state containers and reducers for the Graphia game graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def _merge_private_thoughts(
    prior: dict[str, list[str]] | None,
    incoming: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
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
    """
    merged: dict[str, list[str]] = {
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
    ``TypedDict``s) instead of bare strings: given the prior map and an
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

    The cursor, and why nothing simpler works
    -----------------------------------------

    Diaries and spec 028's ``private_thoughts`` have to render as ONE private
    record in event order, but 028's channel holds bare strings carrying no
    ordering metadata, and spec 039 must not touch that channel. So all the
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
    private_thoughts: Annotated[dict[str, list[str]], _merge_private_thoughts]
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
