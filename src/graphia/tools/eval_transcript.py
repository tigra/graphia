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

Structure — ``<transcript>`` wraps a ``<setup>`` roster block, then alternating
``<night>`` / ``<day>`` sections in the order they streamed; each ``<day>``
holds one ``<round>`` per speaking round. A new ``<round>`` opens on each
genuine round-robin wrap — keyed to the engine's own ``day_rounds`` counter
(``day_turn`` returns ``day_rounds`` only when a full speaking pass completes),
so the transcript's "Round N" matches the engine's true round number. A *failed*
execution vote stays inside the round it was called in (it does not bump
``day_rounds``, so it does not open a new block). The tags are **readability
markers** (so the future LLM-as-Judge can locate sections), not a format
requiring a strict parser; the content inside each tag is plain prose with ids
resolved to names.

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
    final ``players`` map, used to resolve every id to a display name and as the
    fallback source of roles/personas when the event log is sparse.
    ``game_index`` and ``run_meta`` feed a small header; ``run_meta`` is read
    defensively so a ``None`` / thin mapping still renders.

    Returns a flat ``str`` (no I/O). Structure: a header, then ``<transcript>``
    wrapping a ``<setup>`` roster, then alternating ``<night>`` / ``<day>``
    sections in stream order (each ``<day>`` holds one ``<round>`` per speaking
    round). Pure and defensive — never raises on missing/empty input.
    """
    events = events or []
    players = players or {}

    # id → display name, the single resolution surface used throughout. Falls
    # back to the raw id for an unknown player so a stray id is visible, not a
    # crash.
    names = _name_map(players)
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
    lines.extend(_render_phases(events, names))
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

    For a Mafioso, both the public legend AND the true self are shown; for a
    Citizen, the single honest persona. A player with ``persona=None`` renders
    its name + role with a "(no persona recorded)" note rather than raising.
    """
    lines = ["<setup>"]
    if not players:
        lines.append("  (no players recorded)")
        lines.append("</setup>")
        return "\n".join(lines)

    for player in players.values():
        name = getattr(player, "name", None) or _name_of(
            getattr(player, "id", ""), names
        )
        role = getattr(player, "role", "") or ""
        role_label = _ROLE_LABELS.get(role, role or "unknown role")
        lines.append(f"  {name} — {role_label}")
        lines.extend(_persona_lines(getattr(player, "persona", None), role))
    lines.append("</setup>")
    return "\n".join(lines)


def _persona_lines(persona: PlayerPersona | None, role: str) -> list[str]:
    """The indented persona prose for one roster entry (two-layer for Mafia).

    Surfaces ``personality`` / ``manner`` / the public face for everyone; for a
    Mafioso additionally the ``true_self`` behind the cover. Empty fields are
    skipped so a fallback / sparse persona renders cleanly; ``persona=None``
    yields a single "(no persona recorded)" note.
    """
    if persona is None:
        return ["    (no persona recorded)"]

    out: list[str] = []
    personality = (getattr(persona, "personality", "") or "").strip()
    manner = (getattr(persona, "manner", "") or "").strip()
    public = (getattr(persona, "public_persona", "") or "").strip()
    true_self = (getattr(persona, "true_self", "") or "").strip()

    if personality:
        out.append(f"    Personality: {personality}")
    if manner:
        out.append(f"    Manner: {manner}")
    if role == "mafia":
        # Two-layer: the legend shown to the table, then the hidden truth.
        if public:
            out.append(f"    Public legend: {public}")
        if true_self:
            out.append(f"    True self (hidden): {true_self}")
    else:
        if public:
            out.append(f"    Persona: {public}")
    if not out:
        out.append("    (persona has no recorded detail)")
    return out


# ---------------------------------------------------------------------------
# Alternating <night> / <day> sections
# ---------------------------------------------------------------------------


def _render_phases(
    events: list[dict[str, Any]], names: dict[str, str]
) -> list[str]:
    """Walk the event log in order, emitting ``<night>`` / ``<day>`` sections.

    Phase boundaries are the engine's own markers: a ``night_open`` delta opens
    a fresh ``<night>`` (it resets the per-Night pointing channels, so each one
    starts a new Night); a ``day_open`` delta opens a fresh ``<day>``. Every
    other delta's content (messages, picks, kills, ballots) accrues into the
    currently-open section. Anything streamed before the first boundary (the
    setup messages) is captured into a small preamble section so nothing is
    silently dropped.

    Inside a ``<day>`` the body is split into one ``<round>`` per speaking round
    (functional-spec §2.1), keyed to the engine's own ``day_rounds`` counter so
    the "Round N" label matches the engine's true round number — and, once
    sibling spec 020 ships, the round number that recap's in-world clock encodes.
    The first round opens at ``day_open``; a fresh round opens on each genuine
    round-robin wrap, which ``day_turn`` signals by returning ``day_rounds`` (via
    ``_round_complete_update``) — only on a completed pass, never on a mid-round
    step. A *failed* execution vote does NOT open a new round: ``resolve_vote``
    deliberately leaves ``day_rounds`` unchanged, so the failed vote's tally and
    "vote fails" line stay inside the round they were called in (this is the
    spec-020 compatibility requirement — the ``<round>`` label must equal the
    round number the contained recap's clock reads).

    The open is **lazy**: a ``day_rounds`` bump *ends* a round but does not
    always *begin* a visible one — the final round-cap wrap
    (``day_rounds == DAY_MAX_ROUNDS``, which carries no recap) is followed by
    ``day_close``, not another speech. So on a wrap we append that delta's
    messages (the speech plus the recap ``_round_complete_update`` attached, so
    the recap closes the round it summarizes) to the CURRENT round, then set a
    ``pending_round_break`` flag and defer opening the next body until the NEXT
    ``day_turn`` actually arrives. Only a ``day_turn`` event consumes the flag;
    ``day_close`` and the vote nodes (``vote_prompt`` / ``collect_votes`` /
    ``resolve_vote``) always append to the current round, so the Day-ending
    content lands in the LAST round and no spurious empty round follows
    (functional-spec §2.5). An empty trailing round body is dropped in
    ``flush`` (defensive: a degenerate all-dead ``day_order`` bumps
    ``day_rounds`` with no speech), with numbering kept contiguous.
    """
    out: list[str] = []
    # The currently-open section accumulator. ``kind`` ∈ {None, "preamble",
    # "night", "day"}; ``buf`` is its body lines (for night/preamble), while a
    # day uses ``day_rounds`` (a list of round-bodies) instead.
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
    day_round_bodies: list[list[str]] = []
    day_header: list[str] = []
    pending_round_break = False

    def flush() -> None:
        """Close the open section, folding any pending sub-structure into it."""
        nonlocal section_kind, buf, night_rounds, current_round
        nonlocal day_round_bodies, day_header, pending_round_break
        if section_kind is None:
            return
        if section_kind == "night":
            # Header line(s) first, then the picks that accrued for this Night,
            # then the body (which holds the kill announcement).
            pick_lines = _render_night_picks(
                night_rounds, current_round, names
            )
            header, rest = (buf[:1], buf[1:]) if buf else ([], [])
            out.append(_wrap("night", header + pick_lines + rest))
        elif section_kind == "day":
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

    for event in events:
        for node, delta in event.items():
            if not isinstance(delta, dict):
                continue

            if node == "night_open":
                flush()
                night_index += 1
                section_kind = "night"
                buf = [f"Night {night_index} begins."]
                _append_messages(buf, delta, names, skip_first_system=True)
                continue

            if node == "day_open":
                flush()
                day_index += 1
                section_kind = "day"
                day_header = [f"Day {day_index} begins."]
                _append_messages(day_header, delta, names)
                day_round_bodies = [[]]  # open the first speaking round
                pending_round_break = False
                continue

            if section_kind is None:
                # Streamed before any phase boundary — the setup messages.
                section_kind = "preamble"
                buf = []

            if section_kind == "night":
                _accumulate_night_picks(
                    delta, night_rounds, current_round, names
                )
                _append_messages(buf, delta, names)
            elif section_kind == "day":
                # Lazy open: a prior wrap set ``pending_round_break``; the next
                # ``day_turn`` actually begins the new body. Only ``day_turn``
                # consumes the flag — ``day_close`` and the vote nodes always
                # append to the current round (Day-ending content lands in the
                # last round, functional-spec §2.5).
                if pending_round_break and node == "day_turn":
                    day_round_bodies.append([])
                    pending_round_break = False
                _append_messages(day_round_bodies[-1], delta, names)
                # A genuine round-robin wrap ends the round: ``day_turn`` returns
                # ``day_rounds`` only on a completed pass (via
                # ``_round_complete_update``). Set the break AFTER appending this
                # delta — the wrap delta's speech + attached recap close the
                # round they summarize. A failed vote does NOT bump
                # ``day_rounds``, so it stays in the current round.
                if node == "day_turn" and "day_rounds" in delta:
                    pending_round_break = True
            else:  # preamble
                _append_messages(buf, delta, names)

    flush()
    return out


def _wrap(tag: str, body: list[str]) -> str:
    """Wrap a section body in ``<tag>`` / ``</tag>``, indenting non-empty lines."""
    inner = [f"  {line}" if line else line for line in body]
    return "\n".join([f"<{tag}>", *inner, f"</{tag}>"])


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
    picks at all) renders a single "(no Mafia pointing recorded)" line.
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
                f"  {_name_of(mafioso_id, names)} points at "
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
# Message rendering (Moderator announcements + player speech + ballots)
# ---------------------------------------------------------------------------


def _append_messages(
    buf: list[str],
    delta: dict[str, Any],
    names: dict[str, str],
    *,
    skip_first_system: bool = False,
) -> None:
    """Append the prose for any ``messages`` this delta added, in order.

    ``messages`` deltas are the NEW messages that super-step. Each is rendered
    by voice: an ``AIMessage`` (player speech / a human turn — both carry the
    speaker's ``name``) as ``<name>: <text>``; a public ``SystemMessage`` (the
    Moderator voice — announcements, vote lines, kills) as
    ``Moderator: <text>``; a private ``SystemMessage`` (carrying
    ``additional_kwargs["private_to"]`` — a role reveal or Mafia teammate intro)
    as ``Moderator (private to <name>): <text>`` (kept, because the transcript
    deliberately shows hidden material). ``skip_first_system`` drops the first
    Moderator line (the phase-open node already printed a "Night N begins."
    header that would duplicate the engine's "Night falls." line).
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
            else:
                buf.append(f"Moderator: {text}")
            continue
        # Any other message type (defensive) — surface its content under a
        # neutral label rather than dropping it.
        buf.append(f"Moderator: {text}")


def _message_text(msg: Any) -> str:
    """A message's string content, stripped; ``""`` for non-string content."""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content.strip()
    return ""
