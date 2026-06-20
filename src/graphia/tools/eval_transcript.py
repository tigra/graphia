"""Pure renderer: an eval game's ordered event log → a readable transcript.

Spec 017 (*Eval Transcript Preservation*), Slice 1 Task 2. The eval harness
(``blunder_eval._play_one_game``) taps each game's
``graph.stream(stream_mode="updates")`` into an ordered ``events`` list on
:class:`~graphia.tools.blunder_eval._GameCapture` — one ``{node: delta}`` dict
per super-step, in strict chronological order. This module turns that log (plus
the final ``players`` map for id→name resolution) into a single human-readable,
tagged document.

The transcript is a **maintainer-facing eval artifact** (never shown to players
in-game), so it deliberately includes the normally-hidden material:

- each player's **true role** and persona — a Mafioso's *public legend* AND its
  *true self*; a Citizen's single honest persona;
- the Mafiosos' **private Night picks**, by name, per pointing round;
- the resulting Night kill; and the full public Day discussion + every vote
  (initiator, each ballot, outcome).

Structure (spec 022 — *Structured Eval-Transcript Format*) — ``<transcript>``
wraps a ``<setup>`` roster block, then alternating ``<night>`` / ``<day>``
sections in the order they streamed, and finally a single ``<endgame>`` block.
Each ``<day>`` holds one ``<round>`` per speaking round (spec 021). A new
``<round>`` opens on each genuine round-robin wrap — keyed to the engine's own
``day_rounds`` counter (``day_turn`` returns ``day_rounds`` only when a full
speaking pass completes), so the transcript's "Round N" matches the engine's
true round number. A *failed* execution vote stays inside the round it was
called in (it does not bump ``day_rounds``, so it does not open a new block).

Spec 022 gives each distinct event type a consistent, **flush-left, delimited**
shape so a human reviewer (and the future Phase-7 LLM-as-Judge parser) can locate
each one as a self-contained labeled block:

- a **vote** is one ``<vote initiator="X" target="Y">`` block — each surviving
  player's ballot as a plain ``Name: Yes/No`` line (no ``Moderator:`` prefix),
  then ``tally: N Yes, M No``, then ``outcome: …`` (failed, or the executed
  player + revealed side);
- a **night kill** is an inline ``<kill>Name — Side</kill>`` (side from the
  final roster), distinct from the pointing rounds;
- an **end-of-round recap** is an inline ``<recap>…</recap>`` element (content
  reproduced as-is, including the spec-020 in-world clock), distinct from
  ordinary Moderator / utterance lines;
- the **setup** is structured per-player entries (name, role, persona), with no
  2-/4-space alignment indentation;
- the **endgame** (winner + full roster + persona reveal) is one ``<endgame>``
  block;
- formatting is uniform — content is flush-left with zero indent; a single-line
  section is written inline as ``<tag>…</tag>``; a multi-line section opens and
  closes on its own lines. Player **utterances** stay plain ``Name: text`` lines.

Contract — a **pure** :func:`render_transcript` returning a flat ``str`` (same
posture as :func:`graphia.eval_ledger.render_detail` /
``blunder_eval.render_record``): no I/O, no global state, fully unit-testable. A
later task writes the returned string to ``evals/transcripts/<run-id>/game-NN.txt``.

Defensive throughout (mirroring ``eval_ledger._dig``): an empty ``events``, a
missing channel, a player with ``persona=None``, or a thin / ``None``
``run_meta`` must never raise — the renderer surfaces what is present and omits
the rest gracefully. The real eval always has roles + personas; tests and older
captures may be sparse.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage

from graphia.state import PlayerPersona, PlayerState

__all__ = ["render_transcript"]

# Role → human-readable label, matching ``nodes.setup._ROLE_LABELS`` /
# ``nodes.day._role_label`` so the transcript names roles exactly as the game's
# own public lines do.
_ROLE_LABELS: dict[str, str] = {
    "mafia": "Mafia",
    "law_abiding": "Law-abiding Citizen",
}

# --- Moderator-line shapes the renderer must recognise to restructure them ---
# These mirror the engine templates in ``graphia.prompts`` (kept as anchored
# regexes so an ordinary speech/announcement that merely *mentions* a vote or a
# kill never trips them). They are read-only pattern matches — the renderer never
# rewrites the wording, only relabels the line into a structured block.

# ``VOTE_INITIATE_ANNOUNCE_TEMPLATE``: "X has called for a vote to execute Y."
_VOTE_INITIATE_RE = re.compile(
    r"^(?P<initiator>.+?) has called for a vote to execute (?P<target>.+?)\.$"
)
# ``VOTE_PER_BALLOT_TEMPLATE``: "Voter: Yes" / "Voter: No" (the ``Moderator:``
# prefix is already peeled off before this is matched).
_BALLOT_RE = re.compile(r"^(?P<voter>.+?): (?P<vote>Yes|No)$")
# ``VOTE_TALLY_TEMPLATE``: "The tally: N Yes, M No."
_TALLY_RE = re.compile(
    r"^The tally: (?P<yes>\d+) Yes, (?P<no>\d+) No\.$"
)
# ``VOTE_EXECUTED_TEMPLATE``: "Name has been executed. Name was a Role."
_EXECUTED_RE = re.compile(r"^(?P<name>.+?) has been executed\. .+$")
# ``VOTE_FAILED_TEMPLATE``: "The vote fails."
_VOTE_FAILED = "The vote fails."
# ``resolve_night_kill``: "During the night, Name was killed."
_NIGHT_KILL_RE = re.compile(r"^During the night, (?P<name>.+?) was killed\.$")
# ``DAY_ROUND_RECAP_TEMPLATE``: "Day N, <clock> status: <standings>" — the
# spec-018/020 end-of-round recap (the only Moderator line carrying " status:").
_RECAP_RE = re.compile(r"^Day \d+, .+ status: ")


def render_transcript(
    events: list[dict[str, Any]],
    players: dict[str, PlayerState],
    *,
    game_index: int,
    run_meta: Any,
) -> str:
    """Render one eval game's ordered event log into a tagged transcript ``str``.

    ``events`` is the ordered per-super-step ``{node: delta}`` log captured by
    the harness (the transcript's source of truth — NOT a final-state snapshot,
    which would lose every Night's pointing but the last). ``players`` is the
    final ``players`` map, used to resolve every id to a display name, as the
    fallback source of roles/personas when the event log is sparse, and as the
    source of the revealed side for a night-kill block.
    ``game_index`` and ``run_meta`` feed a small header; ``run_meta`` is read
    defensively so a ``None`` / thin mapping still renders.

    Returns a flat ``str`` (no I/O). Structure (spec 022): a header, then
    ``<transcript>`` wrapping a ``<setup>`` roster, then alternating ``<night>``
    / ``<day>`` sections in stream order (each ``<day>`` holds one ``<round>``
    per speaking round), then one ``<endgame>`` block. Flush-left, no alignment
    spaces; single-line sections inline. Pure and defensive — never raises on
    missing/empty input.
    """
    events = events or []
    players = players or {}

    # id → display name, the single resolution surface used throughout. Falls
    # back to the raw id for an unknown player so a stray id is visible, not a
    # crash.
    names = _name_map(players)
    # role-by-name, so a night-kill line ("During the night, X was killed.")
    # can be tagged with X's revealed side straight off the final roster.
    roles_by_name = _roles_by_name(players)
    # The authoritative roles/personas come from the final ``players`` map; the
    # ``assign_roles`` / ``generate_personas`` deltas would also carry them, but
    # the final map is the most complete (later kills only flip ``is_alive``,
    # carrying role + persona forward), so we read the roster from it directly.
    roster_players = _roster_players(events, players)

    lines: list[str] = []
    lines.append(_header(game_index, run_meta))
    lines.append("")
    lines.append("<transcript>")
    lines.append(_render_setup(roster_players, names))
    lines.extend(_render_phases(events, names, roles_by_name))
    lines.append("</transcript>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Header + id→name resolution
# ---------------------------------------------------------------------------


def _header(game_index: int, run_meta: Any) -> str:
    """A small, defensive header line block — game index + handy run metadata.

    ``run_meta`` may be ``None``, a mapping, or any object; only cleanly
    available scalars (``provider``, ``large_model`` / ``small_model``,
    ``games``) are surfaced, each omitted when absent so a thin / ``None``
    ``run_meta`` still renders a header.
    """
    parts = [f"Game {game_index}"]
    provider = _meta_get(run_meta, "provider")
    if provider:
        parts.append(f"provider={provider}")
    large = _meta_get(run_meta, "large_model")
    if large:
        parts.append(f"large_model={large}")
    small = _meta_get(run_meta, "small_model")
    if small:
        parts.append(f"small_model={small}")
    games = _meta_get(run_meta, "games")
    if games:
        parts.append(f"games={games}")
    return " | ".join(parts)


def _meta_get(run_meta: Any, key: str) -> str:
    """Pull ``key`` from a thin/None ``run_meta`` as display text, else ``""``.

    Accepts a mapping (``run_meta[key]``) or any object with an attribute
    (``getattr``); a missing key/attr, a ``None`` value, or a ``None``
    ``run_meta`` all resolve to ``""`` so the caller simply omits the part.
    """
    if run_meta is None:
        return ""
    value: Any = None
    if isinstance(run_meta, dict):
        value = run_meta.get(key)
    else:
        value = getattr(run_meta, key, None)
    return "" if value is None else str(value)


def _name_map(players: dict[str, PlayerState]) -> dict[str, str]:
    """Build an id → display-name map; an unknown id later resolves to itself."""
    out: dict[str, str] = {}
    for pid, player in players.items():
        name = getattr(player, "name", None)
        out[pid] = name if isinstance(name, str) and name else pid
    return out


def _roles_by_name(players: dict[str, PlayerState]) -> dict[str, str]:
    """name → human-readable role label, for tagging a night-kill block's side.

    Built off the final roster (kills only flip ``is_alive``, so the role is
    intact). A name with no recorded role, or one absent from the roster, simply
    won't appear here — the kill block then omits the side rather than guessing.
    """
    out: dict[str, str] = {}
    for player in players.values():
        name = getattr(player, "name", None)
        role = getattr(player, "role", "") or ""
        if isinstance(name, str) and name and role:
            out[name] = _ROLE_LABELS.get(role, role)
    return out


def _name_of(player_id: str, names: dict[str, str]) -> str:
    """Resolve a player id to its display name, falling back to the raw id."""
    return names.get(player_id, player_id)


# ---------------------------------------------------------------------------
# Roster / <setup>
# ---------------------------------------------------------------------------


def _roster_players(
    events: list[dict[str, Any]], players: dict[str, PlayerState]
) -> dict[str, PlayerState]:
    """The roster to render in ``<setup>`` — prefer the final ``players`` map.

    The final map carries every player with their dealt role and persona (kills
    only flip ``is_alive``). When it is empty (a thin synthetic ``events`` list
    with no final map), fall back to the last ``players`` delta seen in the
    event log (``generate_personas`` / ``assign_roles`` / ``collect_name`` all
    emit one) so a roster still renders.
    """
    if players:
        return players
    fallback: dict[str, PlayerState] = {}
    for event in events:
        for delta in event.values():
            if isinstance(delta, dict):
                candidate = delta.get("players")
                if isinstance(candidate, dict) and candidate:
                    fallback = candidate
    return fallback


def _render_setup(
    players: dict[str, PlayerState], names: dict[str, str]
) -> str:
    """Render the ``<setup>`` roster block: each player's name, role, persona.

    Each player is a flush-left structured entry — a ``Name — Role`` header line
    followed by its persona ``Field: value`` lines (spec 022: no 2-/4-space
    alignment indentation). For a Mafioso, both the public legend AND the true
    self are shown; for a Citizen, the single honest persona. A player with
    ``persona=None`` renders its name + role with a "(no persona recorded)" note
    rather than raising.
    """
    lines = ["<setup>"]
    if not players:
        lines.append("(no players recorded)")
        lines.append("</setup>")
        return "\n".join(lines)

    for player in players.values():
        name = getattr(player, "name", None) or _name_of(
            getattr(player, "id", ""), names
        )
        role = getattr(player, "role", "") or ""
        role_label = _ROLE_LABELS.get(role, role or "unknown role")
        lines.append(f"{name} — {role_label}")
        lines.extend(_persona_lines(getattr(player, "persona", None), role))
    lines.append("</setup>")
    return "\n".join(lines)


def _persona_lines(persona: PlayerPersona | None, role: str) -> list[str]:
    """The flush-left persona prose for one roster entry (two-layer for Mafia).

    Surfaces ``personality`` / ``manner`` / the public face for everyone; for a
    Mafioso additionally the ``true_self`` behind the cover. Each is a flush-left
    ``Field: value`` line (spec 022: no alignment indentation). Empty fields are
    skipped so a fallback / sparse persona renders cleanly; ``persona=None``
    yields a single "(no persona recorded)" note.
    """
    if persona is None:
        return ["(no persona recorded)"]

    out: list[str] = []
    personality = (getattr(persona, "personality", "") or "").strip()
    manner = (getattr(persona, "manner", "") or "").strip()
    public = (getattr(persona, "public_persona", "") or "").strip()
    true_self = (getattr(persona, "true_self", "") or "").strip()

    if personality:
        out.append(f"Personality: {personality}")
    if manner:
        out.append(f"Manner: {manner}")
    if role == "mafia":
        # Two-layer: the legend shown to the table, then the hidden truth.
        if public:
            out.append(f"Public legend: {public}")
        if true_self:
            out.append(f"True self (hidden): {true_self}")
    else:
        if public:
            out.append(f"Persona: {public}")
    if not out:
        out.append("(persona has no recorded detail)")
    return out


# ---------------------------------------------------------------------------
# Alternating <night> / <day> sections + a trailing <endgame> block
# ---------------------------------------------------------------------------


def _render_phases(
    events: list[dict[str, Any]],
    names: dict[str, str],
    roles_by_name: dict[str, str],
) -> list[str]:
    """Walk the event log in order, emitting ``<night>`` / ``<day>`` sections.

    Phase boundaries are the engine's own markers: a ``night_open`` delta opens
    a fresh ``<night>`` (it resets the per-Night pointing channels, so each one
    starts a new Night); a ``day_open`` delta opens a fresh ``<day>``; the
    ``end_screen`` delta closes whatever section is open and emits the single
    ``<endgame>`` block (so the endgame is its own labeled block, never folded
    into the last Night/Day). Every other delta's content (messages, picks,
    kills, ballots) accrues into the currently-open section. Anything streamed
    before the first boundary (the setup messages) is captured into a small
    preamble section so nothing is silently dropped.

    Inside a ``<day>`` the body is split into one ``<round>`` per speaking round
    (spec 021), keyed to the engine's own ``day_rounds`` counter so the
    "Round N" label matches the engine's true round number — and the round
    number that recap's spec-020 in-world clock encodes. The first round opens
    at ``day_open``; a fresh round opens on each genuine round-robin wrap, which
    ``day_turn`` signals by returning ``day_rounds`` (via
    ``_round_complete_update``) — only on a completed pass, never on a mid-round
    step. A *failed* execution vote does NOT open a new round: ``resolve_vote``
    deliberately leaves ``day_rounds`` unchanged, so the failed vote's block
    stays inside the round it was called in.

    The open is **lazy**: a ``day_rounds`` bump *ends* a round but does not
    always *begin* a visible one — the final round-cap wrap is followed by
    ``day_close``, not another speech. So on a wrap we append that delta's
    content to the CURRENT round, then set a ``pending_round_break`` flag and
    defer opening the next body until the NEXT ``day_turn`` actually arrives.
    Only a ``day_turn`` event consumes the flag; ``day_close`` and the vote
    nodes always append to the current round, so the Day-ending content lands in
    the LAST round and no spurious empty round follows. An empty trailing round
    body is dropped in ``flush`` (defensive), with numbering kept contiguous.

    The day's vote super-steps (``vote_prompt`` → ``collect_votes`` →
    ``resolve_vote``) are buffered by a small per-Day :class:`_VoteAssembler` and
    flushed as one ``<vote>`` block into whichever round body is current, so a
    vote reads as a single delimited block rather than a run of Moderator lines.
    """
    out: list[str] = []
    # The currently-open section accumulator. ``kind`` ∈ {None, "preamble",
    # "night", "day"}; ``buf`` is its body lines (for night/preamble), while a
    # day uses ``day_round_bodies`` (a list of round-bodies) instead.
    section_kind: str | None = None
    buf: list[str] = []
    night_index = 0
    day_index = 0

    # Per-Night pointing accumulator: each Mafioso pick (id→target id) seen in
    # this Night's ``mafia_point`` / ``mafia_round_start`` deltas, grouped by
    # round, so multi-round consensus (spec 015) is shown round-by-round before
    # the kill — captured BEFORE the next ``night_open`` resets the channels.
    night_rounds: list[dict[str, str]] = []
    current_round: dict[str, str] = {}

    # Per-Day speaking-round accumulator: a list of round bodies; the last entry
    # is the round currently filling. ``day_header`` holds the day-open line(s)
    # that precede the first round. ``pending_round_break`` is set when a
    # ``day_turn`` wrap (a delta carrying ``day_rounds``) ends a round; the next
    # ``day_turn`` event then opens a fresh body (lazy open — see docstring).
    # ``vote`` buffers a vote's super-steps into one ``<vote>`` block.
    day_round_bodies: list[list[str]] = []
    day_header: list[str] = []
    pending_round_break = False
    vote = _VoteAssembler(names, roles_by_name)

    def current_day_body() -> list[str]:
        """The round body currently filling (defensively open one if absent)."""
        if not day_round_bodies:
            day_round_bodies.append([])
        return day_round_bodies[-1]

    def flush() -> None:
        """Close the open section, folding any pending sub-structure into it."""
        nonlocal section_kind, buf, night_rounds, current_round
        nonlocal day_round_bodies, day_header, pending_round_break, vote
        if section_kind is None:
            return
        if section_kind == "night":
            # Header line(s) first, then the picks that accrued for this Night,
            # then the body (which holds the kill block).
            pick_lines = _render_night_picks(
                night_rounds, current_round, names
            )
            header, rest = (buf[:1], buf[1:]) if buf else ([], [])
            out.append(_wrap("night", header + pick_lines + rest))
        elif section_kind == "day":
            # Flush any still-open vote into the current round before closing.
            vote.flush_into(current_day_body())
            body = list(day_header)
            # Drop an empty trailing round body (defensive: a degenerate
            # all-dead ``day_order`` bumps ``day_rounds`` with no speech, which
            # would otherwise emit a spurious empty ``Round N.`` block). Filter
            # before ``enumerate`` so numbering stays contiguous.
            non_empty = [rb for rb in day_round_bodies if rb]
            for number, round_body in enumerate(non_empty, start=1):
                body.append(
                    _wrap("round", [f"Round {number}.", *round_body])
                )
            out.append(_wrap("day", body))
        else:  # preamble
            out.append(_wrap("preamble", buf))
        section_kind = None
        buf = []
        night_rounds = []
        current_round = {}
        day_round_bodies = []
        day_header = []
        pending_round_break = False
        vote = _VoteAssembler(names, roles_by_name)

    for event in events:
        for node, delta in event.items():
            if not isinstance(delta, dict):
                continue

            if node == "end_screen":
                # The endgame is its own labeled block, never folded into the
                # last Night/Day. Close whatever is open, then emit <endgame>.
                flush()
                out.append(_render_endgame(delta, names))
                continue

            if node == "night_open":
                flush()
                night_index += 1
                section_kind = "night"
                buf = [f"Night {night_index} begins."]
                _append_messages(
                    buf, delta, names, roles_by_name, skip_first_system=True
                )
                continue

            if node == "day_open":
                flush()
                day_index += 1
                section_kind = "day"
                day_header = [f"Day {day_index} begins."]
                _append_messages(day_header, delta, names, roles_by_name)
                day_round_bodies = [[]]  # open the first speaking round
                pending_round_break = False
                vote = _VoteAssembler(names, roles_by_name)
                continue

            if section_kind is None:
                # Streamed before any phase boundary — the setup messages.
                section_kind = "preamble"
                buf = []

            if section_kind == "night":
                _accumulate_night_picks(
                    delta, night_rounds, current_round, names
                )
                _append_messages(buf, delta, names, roles_by_name)
            elif section_kind == "day":
                # Lazy open: a prior wrap set ``pending_round_break``; the next
                # ``day_turn`` actually begins the new body. Flush any open vote
                # into the round it belongs to BEFORE the break opens a new one.
                if pending_round_break and node == "day_turn":
                    vote.flush_into(current_day_body())
                    day_round_bodies.append([])
                    pending_round_break = False
                # The vote super-steps (vote_prompt / collect_votes /
                # resolve_vote) are buffered into one <vote> block instead of
                # appending raw Moderator lines; ``resolve_vote`` finalises the
                # block, which lands in the round it was called in.
                if node in _VOTE_NODES:
                    block = vote.feed(node, delta)
                    if block is not None:
                        current_day_body().append(block)
                else:
                    _append_messages(
                        current_day_body(), delta, names, roles_by_name
                    )
                # A genuine round-robin wrap ends the round: ``day_turn`` returns
                # ``day_rounds`` only on a completed pass. Set the break AFTER
                # appending this delta — the wrap delta's speech + attached recap
                # close the round they summarize. A failed vote does NOT bump
                # ``day_rounds``, so it stays in the current round.
                if node == "day_turn" and "day_rounds" in delta:
                    pending_round_break = True
            else:  # preamble
                _append_messages(buf, delta, names, roles_by_name)

    flush()
    return out


def _wrap(tag: str, body: list[str]) -> str:
    """Wrap a section body in ``<tag>`` markers, flush-left (spec 022).

    A single content line collapses to an inline ``<tag>content</tag>``; an
    empty body to a bare ``<tag></tag>``; otherwise the tag opens and closes on
    its own lines with the body flush-left between them (zero indent — no
    alignment spaces).
    """
    content = [line for line in body if line != ""]
    if not content:
        return f"<{tag}></{tag}>"
    if len(content) == 1:
        return f"<{tag}>{content[0]}</{tag}>"
    return "\n".join([f"<{tag}>", *body, f"</{tag}>"])


def _inline(tag: str, content: str) -> str:
    """A single-line section written inline as ``<tag>content</tag>`` (spec 022)."""
    return f"<{tag}>{content}</{tag}>"


def _render_night_picks(
    rounds: list[dict[str, str]],
    current_round: dict[str, str],
    names: dict[str, str],
) -> list[str]:
    """Render a Night's Mafioso picks by name, round-by-round (spec 015).

    ``rounds`` are the completed pointing rounds (archived into
    ``night_rounds_log`` as the loop advanced); ``current_round`` is the
    deciding round still in ``night_round_picks`` when the Night resolved. Each
    pick is ``<mafioso name> points at <target name>``; an empty Night (no
    picks at all) renders a single "(no Mafia pointing recorded)" line. These are
    flush-left lines, distinct from the night's ``<kill>`` block (spec 022).
    """
    all_rounds = [r for r in rounds if r]
    if current_round:
        all_rounds = [*all_rounds, current_round]
    if not all_rounds:
        return ["(no Mafia pointing recorded this Night)"]

    out: list[str] = []
    for round_number, picks in enumerate(all_rounds, start=1):
        out.append(f"Pointing round {round_number}:")
        for mafioso_id, target_id in picks.items():
            out.append(
                f"{_name_of(mafioso_id, names)} points at "
                f"{_name_of(target_id, names)}"
            )
    return out


def _accumulate_night_picks(
    delta: dict[str, Any],
    rounds: list[dict[str, str]],
    current_round: dict[str, str],
    names: dict[str, str],
) -> None:
    """Fold one night-phase delta's pointing channels into the Night accumulator.

    ``mafia_round_start`` archives the just-finished round into
    ``night_rounds_log`` and resets ``night_round_picks`` to ``{}`` for the new
    round; ``mafia_point`` grows ``night_round_picks`` by one pointer. We mirror
    that: when a delta carries ``night_rounds_log``, snap our completed-rounds
    list to it (it is the cumulative archive); when it carries
    ``night_round_picks``, replace the current round's picks with it (it is the
    cumulative dict for the round-in-progress). Reading both channels straight
    from the streamed deltas is what preserves multi-round picks the final
    snapshot would have lost.
    """
    rounds_log = delta.get("night_rounds_log")
    if isinstance(rounds_log, list):
        # Cumulative archive of completed rounds — adopt it wholesale.
        rounds.clear()
        rounds.extend(
            dict(r) for r in rounds_log if isinstance(r, dict)
        )
    round_picks = delta.get("night_round_picks")
    if isinstance(round_picks, dict):
        current_round.clear()
        current_round.update(round_picks)


# ---------------------------------------------------------------------------
# Vote assembly — one <vote> block per execution vote (spec 022)
# ---------------------------------------------------------------------------

# The three day nodes whose super-steps make up one execution vote, in order.
_VOTE_NODES = frozenset({"vote_prompt", "collect_votes", "resolve_vote"})


class _VoteAssembler:
    """Buffer a vote's super-steps and emit one delimited ``<vote>`` block.

    A vote streams as three node kinds (spec 022): ``vote_prompt`` announces who
    called the vote on whom; each ``collect_votes`` super-step contributes one
    voter's ballot; ``resolve_vote`` carries the tally and the outcome (failed,
    or the executed player + revealed side). This collector reads those raw
    Moderator lines, strips the ``Moderator:`` voice off the ballots, and renders

        <vote initiator="X" target="Y">
        Voter: Yes
        Voter: No
        tally: N Yes, M No
        outcome: failed — The vote fails.
        </vote>

    The assembler is *per-Day* and self-resetting: ``resolve_vote`` finalises the
    current block, so a Day with several votes (failed ones, then a passing one)
    yields several ``<vote>`` blocks. A vote left open when a round/day flush
    happens (defensive — a partial capture) is still emitted by ``flush_into``.
    """

    def __init__(
        self, names: dict[str, str], roles_by_name: dict[str, str]
    ) -> None:
        self._names = names
        self._roles_by_name = roles_by_name
        self._reset()

    def _reset(self) -> None:
        self._open = False
        self._initiator = ""
        self._target = ""
        self._ballots: list[str] = []
        self._tally = ""
        self._outcome = ""

    def feed(self, node: str, delta: dict[str, Any]) -> str | None:
        """Consume one vote super-step; return a finished ``<vote>`` block or None.

        ``vote_prompt`` opens a block and records initiator/target;
        ``collect_votes`` appends each ballot; ``resolve_vote`` records the tally
        + outcome and returns the rendered block (then resets for any next vote
        this Day). The caller appends the returned block to the current round.
        """
        for text in _system_texts(delta):
            self._consume_line(text)
        if node == "resolve_vote":
            return self._finish()
        return None

    def _consume_line(self, text: str) -> None:
        """Classify one Moderator line into the open vote block's fields."""
        m = _VOTE_INITIATE_RE.match(text)
        if m:
            # A fresh initiation: open (or re-open) the block.
            self._open = True
            self._initiator = m.group("initiator")
            self._target = m.group("target")
            self._ballots = []
            self._tally = ""
            self._outcome = ""
            return
        m = _TALLY_RE.match(text)
        if m:
            self._open = True
            self._tally = f"tally: {m.group('yes')} Yes, {m.group('no')} No"
            return
        m = _EXECUTED_RE.match(text)
        if m:
            # The execution reveal already names the executed player + side; keep
            # its wording verbatim (spec 022: only structure changes).
            self._open = True
            self._outcome = f"outcome: executed — {text}"
            return
        if text == _VOTE_FAILED:
            self._open = True
            self._outcome = f"outcome: failed — {text}"
            return
        m = _BALLOT_RE.match(text)
        if m:
            # A plain ballot line — drop the Moderator voice (spec 022).
            self._open = True
            self._ballots.append(f"{m.group('voter')}: {m.group('vote')}")

    def _finish(self) -> str | None:
        """Render and reset the current block; None if nothing was buffered."""
        if not self._open:
            return None
        block = self._render()
        self._reset()
        return block

    def flush_into(self, body: list[str]) -> None:
        """Emit any still-open vote block into ``body`` (defensive partial flush)."""
        block = self._finish()
        if block is not None:
            body.append(block)

    def _render(self) -> str:
        attrs = f'initiator="{self._initiator}" target="{self._target}"'
        inner: list[str] = [*self._ballots]
        if self._tally:
            inner.append(self._tally)
        if self._outcome:
            inner.append(self._outcome)
        if not inner:
            return f"<vote {attrs}></vote>"
        return "\n".join([f"<vote {attrs}>", *inner, "</vote>"])


# ---------------------------------------------------------------------------
# Endgame block (spec 022)
# ---------------------------------------------------------------------------


def _render_endgame(delta: dict[str, Any], names: dict[str, str]) -> str:
    """Render the ``end_screen`` delta as one ``<endgame>`` block (spec 022).

    ``end_screen`` emits a single Moderator ``SystemMessage`` whose content holds
    the winner line, the events list, the full roster, and the persona reveal —
    all already plain prose. This groups the whole thing inside one ``<endgame>``
    block (flush-left, content reproduced as-is), so the reader/parser locates
    the game's outcome as one self-contained labeled block rather than a
    Moderator line buried in the last Night.
    """
    texts = _system_texts(delta)
    body: list[str] = []
    for text in texts:
        body.extend(text.split("\n"))
    if not body:
        body = ["(no endgame recorded)"]
    return _wrap("endgame", body)


# ---------------------------------------------------------------------------
# Message rendering (Moderator announcements + player speech + ballots)
# ---------------------------------------------------------------------------


def _system_texts(delta: dict[str, Any]) -> list[str]:
    """The stripped string content of each ``SystemMessage`` this delta added.

    Used by the vote assembler and endgame renderer, which only ever care about
    the Moderator (``SystemMessage``) voice. Order-preserving; non-string /
    empty content is skipped.
    """
    out: list[str] = []
    messages = delta.get("messages")
    if not isinstance(messages, (list, tuple)):
        return out
    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = _message_text(msg)
            if text:
                out.append(text)
    return out


def _append_messages(
    buf: list[str],
    delta: dict[str, Any],
    names: dict[str, str],
    roles_by_name: dict[str, str],
    *,
    skip_first_system: bool = False,
) -> None:
    """Append the prose for any ``messages`` this delta added, in order.

    ``messages`` deltas are the NEW messages that super-step. Each is rendered
    by voice (spec 022):

    - an ``AIMessage`` (player speech / a human turn — both carry the speaker's
      ``name``) as a plain ``<name>: <text>`` utterance, unchanged;
    - a public ``SystemMessage`` (the Moderator voice):
        * a **night-kill** line ("During the night, X was killed.") becomes an
          inline ``<kill>X — Side</kill>`` block (side off the final roster);
        * an **end-of-round recap** line ("Day N, <clock> status: …") becomes an
          inline ``<recap>…</recap>`` element, content reproduced as-is;
        * any other Moderator line stays ``Moderator: <text>``;
    - a private ``SystemMessage`` (carrying ``additional_kwargs["private_to"]`` —
      a role reveal or Mafia teammate intro) as
      ``Moderator (private to <name>): <text>`` (kept, because the transcript
      deliberately shows hidden material).

    ``skip_first_system`` drops the first Moderator line (the phase-open node
    already printed a "Night N begins." header that would duplicate the engine's
    "Night falls." line). The vote lines never reach here — they are routed to
    the ``<vote>`` assembler upstream.
    """
    messages = delta.get("messages")
    if not isinstance(messages, (list, tuple)):
        return
    skipped = False
    for msg in messages:
        text = _message_text(msg)
        if not text:
            continue
        if isinstance(msg, AIMessage):
            speaker = getattr(msg, "name", None) or "Unknown"
            buf.append(f"{speaker}: {text}")
            continue
        if isinstance(msg, SystemMessage):
            if skip_first_system and not skipped:
                skipped = True
                continue
            extra = getattr(msg, "additional_kwargs", None) or {}
            private_to = extra.get("private_to")
            if private_to:
                target = _name_of(private_to, names)
                buf.append(f"Moderator (private to {target}): {text}")
                continue
            kill = _NIGHT_KILL_RE.match(text)
            if kill:
                buf.append(_kill_block(kill.group("name"), roles_by_name))
                continue
            if _RECAP_RE.match(text):
                buf.append(_inline("recap", text))
                continue
            buf.append(f"Moderator: {text}")
            continue
        # Any other message type (defensive) — surface its content under a
        # neutral label rather than dropping it.
        buf.append(f"Moderator: {text}")


def _kill_block(name: str, roles_by_name: dict[str, str]) -> str:
    """An inline ``<kill>Name — Side</kill>`` for a night kill (spec 022).

    The revealed side is looked up off the final roster; if the name is unknown
    there (defensive), the side is omitted rather than guessed.
    """
    side = roles_by_name.get(name)
    content = f"{name} — {side}" if side else name
    return _inline("kill", content)


def _message_text(msg: Any) -> str:
    """A message's string content, stripped; ``""`` for non-string content."""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content.strip()
    return ""
