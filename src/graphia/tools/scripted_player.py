"""Active scripted-player policy for measured (eval) runs — spec 026.

The human seat in a blunder-eval run is filled by an automated stand-in. The
*passive* stand-in baked into the drive loop never proposes an execution and
always votes No — which, as the 024+025 transcript investigation showed, is the
arithmetic constraint that stalls a *correct* town vote at 3-3. This module is
the **active** replacement: a pure, deterministic, rule-based policy that
supplies the seat's three resume values (the ``day_turn`` action, the
``collect_votes`` ballot, and — when the seat is a Mafioso — the ``mafia_point``
night target) from the **public game so far** (plus, for a Mafioso, its
legitimately-known teammates), with **no LLM call and no RNG**.

It plays differently by the role it is dealt:

- **As a Law-abiding Citizen** (:func:`law_abiding_decision`) it scores every
  living player's suspicion from public reveals + vote behaviour, states its top
  suspicion aloud, and on the final discussion round proposes — and in any open
  ballot votes — against its top suspect.
- **As a Mafioso** (:func:`mafia_decision`) it protects its teammates (never
  nominates / points / Yes-votes a teammate, always spares one), and drives out
  the town's strongest hunter (the lowest-suspicion living non-teammate).

Determinism (architecture §6): no RNG, no clock, no LLM anywhere in this module.
Every choice is a pure function of the reconstructed public view (+ the
Mafioso's teammate set); ties break lexically by player id. This is what keeps a
measured game reproducible per-seed and adds zero token cost — and it is
structurally guaranteed by the rule that this module **never imports
``graphia.llm``**.

Knowledge-boundary invariant (load-bearing, mirrors spec-013 §2.5): the
Law-abiding view derives confirmed sides ONLY from public Moderator reveal lines
in ``messages`` — it must **never** read ``players[*].role`` for a living,
unrevealed player. The Mafioso path MAY additionally read its own
``teammate_ids`` (legitimate self-knowledge from the private teammate intro); it
still scores every *other* player from public signals only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import SystemMessage

# The same Moderator reveal/vote templates the blunder scorers parse. IMPORTED
# (never hardcoded copies) so a reword in ``graphia.prompts`` breaks the offline
# tests loudly rather than silently mis-reading a confirmed side.
from graphia.prompts import (
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    VOTE_EXECUTED_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
)
from graphia.state import PlayerState

# The Yes/No labels ``day.py`` formats into ``VOTE_PER_BALLOT_TEMPLATE`` (and the
# same spellings ``blunder_eval`` anchors its ballot parse on). Kept local so this
# module stays dependency-light — stdlib + ``graphia.state`` / ``graphia.prompts``
# only, NO ``graphia.llm`` import (the structural no-model-call guarantee; the
# tech-doc §2.1 import rule, asserted by an offline test). ``blunder_eval`` would
# have given us these, but importing it transitively pulls in ``graphia.llm``, so
# the few small helpers it shares (the template-derived regex + the name index)
# are re-derived here against the same imported templates instead.
_BALLOT_YES_LABEL = "Yes"
_BALLOT_NO_LABEL = "No"


def _template_to_regex(
    template: str, fields: dict[str, str]
) -> re.Pattern[str]:
    """Compile a ``str.format`` template into a named-group capture regex.

    Splits the template on its ``{field}`` placeholders and re-joins the literal
    spans (``re.escape``-d) with each field replaced by the named capture group
    supplied in ``fields``. Deriving the regex FROM the imported format string
    (not a hand-written parallel pattern) is what makes a template reword break
    parsing — and the offline tests built from the same constant — loudly rather
    than silently mis-reading a confirmed side. Mirrors
    ``blunder_eval._template_to_regex`` exactly (re-derived here to keep this
    module free of the ``graphia.llm`` transitive import that file carries).

    A duplicate placeholder (``VOTE_EXECUTED_TEMPLATE`` / the victim reveal carry
    ``{name}`` twice) reuses the same group body for each occurrence but emits a
    distinct group name on the second one (``<field>_2``) so Python's "redefined
    group name" error never fires; callers read the first occurrence's group.
    """
    pattern_parts: list[str] = []
    seen: set[str] = set()
    pos = 0
    for match in re.finditer(r"\{(\w+)\}", template):
        literal = template[pos : match.start()]
        pattern_parts.append(re.escape(literal))
        field_name = match.group(1)
        body = fields[field_name]  # KeyError if a placeholder is unanchored
        if field_name in seen:
            # A repeated placeholder: keep the SAME captured text shape but make
            # this occurrence non-capturing so the duplicate group name is legal.
            body = re.sub(r"\(\?P<\w+>", "(?:", body, count=1)
        seen.add(field_name)
        pattern_parts.append(body)
        pos = match.end()
    pattern_parts.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(pattern_parts) + "$")


def _name_index(players: dict[str, PlayerState]) -> dict[str, PlayerState]:
    """Map each UNIQUELY-held name to its player (for reveal/ballot resolution).

    Names are validated distinct at roster generation, so in practice this is one
    entry per player. Defensively, a name held by more than one player is dropped
    (resolves to ``None`` downstream and is simply not scored) rather than
    resolving ambiguously. Mirrors ``blunder_eval._name_index``.
    """
    index: dict[str, PlayerState] = {}
    seen_twice: set[str] = set()
    for player in players.values():
        if player.name in index:
            seen_twice.add(player.name)
        index[player.name] = player
    for name in seen_twice:
        index.pop(name, None)
    return index

# ---------------------------------------------------------------------------
# Suspicion-scoring weights (the tech-doc table, §2.1). All TUNABLE — kept as
# named module constants so the one scoring rule and any offline test share a
# single source of truth, and so a future re-weighting is a one-line edit. The
# ordering between them is what the tests assert (propose > Yes > spare), not the
# exact magnitudes, so the weights stay free to move.
#
# Sign convention: higher score = MORE suspect (more likely Mafia / less
# town-aligned). A town-aligned action lowers the score (negative Δ); an
# own-goal or Mafia-protecting action raises it (positive Δ).
# ---------------------------------------------------------------------------

# Proposing the execution of a confirmed Mafioso is the strongest town signal —
# weighted above a follow-on Yes ballot.
W_PROPOSE_EXECUTE_MAFIA = -3.0
# Voting Yes on a confirmed Mafioso is town-aligned, but less than initiating.
W_YES_EXECUTE_MAFIA = -1.5
# Proposing the execution of a confirmed Law-abiding is an active own-goal push.
W_PROPOSE_EXECUTE_LAW = +3.0
# Voting Yes on a confirmed Law-abiding helped an own-goal.
W_YES_EXECUTE_LAW = +1.5
# Sparing (voting No on) a confirmed Mafioso protected a known Mafioso.
W_SPARE_MAFIA = +1.5
# Sparing a confirmed Law-abiding correctly declined an own-goal — mildly town.
W_SPARE_LAW = -0.5
# Being a prior target of a night-killed player — the Mafia silence their hunters.
W_NIGHT_VICTIM_HUNTER = +1.5

# A living player must carry at least one net-suspicious public signal to be
# voted out: an all-quiet table yields No rather than a coin-flip own-goal. The
# LA ballot votes Yes iff the open target's score is at or above this bar.
# TUNABLE.
SUSPICION_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# Public-view reconstruction.
# ---------------------------------------------------------------------------

# Confirmed-side label: "mafia" or "law_abiding". A reveal's free-text role
# label is one of the two ``_role_label`` spellings; we map it to a side below.
type Side = Literal["mafia", "law_abiding"]


# Execution reveal: "{name} has been executed. {name} was a {role_label}." The
# template carries ``{name}`` twice; we capture the first occurrence as the
# executed player and the role label after "was a ". Both names are the same
# player, so the second ``{name}`` is anchored as a plain (non-capturing) name.
_EXECUTED_RE = _template_to_regex(
    VOTE_EXECUTED_TEMPLATE,
    {"name": r"(?P<name>.+?)", "role_label": r"(?P<role_label>.+?)"},
)

# Night-victim reveal: "Day breaks. {name} was killed last night. {name} was a
# {role_label}." A night victim is ALWAYS Law-abiding in this game, so we only
# need the victim's name (the role label is parsed but the side is fixed).
_VICTIM_REVEAL_RE = _template_to_regex(
    DAY_OPEN_VICTIM_REVEAL_TEMPLATE,
    {"name": r"(?P<name>.+?)", "role_label": r"(?P<role_label>.+?)"},
)

# Vote announce + per-ballot anchors — the SAME shapes ``blunder_eval`` parses.
_ANNOUNCE_RE = _template_to_regex(
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    {"initiator": r"(?P<initiator>.+?)", "target": r"(?P<target>.+?)"},
)
_BALLOT_RE = _template_to_regex(
    VOTE_PER_BALLOT_TEMPLATE,
    {
        "voter": r"(?P<voter>.+?)",
        "vote_label": rf"(?P<vote_label>{re.escape(_BALLOT_YES_LABEL)}|{re.escape(_BALLOT_NO_LABEL)})",
    },
)


def _side_from_role_label(role_label: str) -> Side:
    """Map a revealed free-text role label to a confirmed side.

    The Moderator's ``_role_label`` emits "Mafia" for a Mafioso and "Law-abiding
    Citizen" otherwise. We map any label containing "mafia" (case-insensitive) to
    ``"mafia"`` and everything else to ``"law_abiding"`` — robust to the exact
    spelling ("Mafia" / "Mafioso") while staying public-reveal-only.
    """
    return "mafia" if "mafia" in role_label.lower() else "law_abiding"


@dataclass(slots=True)
class _MoveAgainst:
    """One public move an actor made against a target: a proposal or a Yes ballot."""

    actor_name: str
    target_name: str
    kind: Literal["propose", "yes", "no"]


@dataclass(slots=True)
class PublicView:
    """Public-only facts reconstructed from the game's ``messages`` history.

    Built EXCLUSIVELY from public Moderator reveal/vote lines — never from
    ``players[*].role`` of a living, unrevealed player (the knowledge-boundary
    invariant). Holds everything the suspicion scorer needs:

    - ``confirmed_side`` — name → revealed side, from executions (revealed side)
      and night-kill victims (always Law-abiding).
    - ``night_victims`` — the names killed overnight (their prior targets become
      suspects).
    - ``moves`` — every public move-against (proposal / Yes / No ballot), in
      order, attributed to the most-recent announce's target exactly as the
      blunder scorers attribute ballots.
    """

    confirmed_side: dict[str, Side] = field(default_factory=dict)
    night_victims: list[str] = field(default_factory=list)
    moves: list[_MoveAgainst] = field(default_factory=list)


def reconstruct_public_view(
    messages: list,
    players: dict[str, PlayerState],
    human_id: str,
) -> PublicView:
    """Walk the ``messages`` history into a :class:`PublicView` of public facts.

    Parses the public Moderator ``SystemMessage`` lines the blunder scorers parse
    — executions (:data:`_EXECUTED_RE` → the executed player + revealed side),
    night-kill victims (:data:`_VICTIM_REVEAL_RE` → a confirmed Law-abiding), and
    the announce/ballot lines — into confirmed sides and per-player vote
    behaviours. Each ballot binds to the most-recent announce's target (one
    active vote at a time), exactly as ``score_vote_blunders`` attributes them.

    ``players`` / ``human_id`` are passed only so name resolution and the caller
    share one signature; this function reads NO ``.role`` field (the
    knowledge-boundary invariant — confirmed sides come from reveals alone).

    PURE over ``(messages, players, human_id)`` — no game, no model, no RNG — so
    it is unit-testable offline on a synthetic history built from the real
    templates.
    """
    view = PublicView()
    active_target_name: str | None = None

    for msg in messages:
        if not isinstance(msg, SystemMessage):
            continue
        content = msg.content
        if not isinstance(content, str):
            continue
        content = content.strip()

        # Execution reveal — confirmed side from the revealed role label.
        executed = _EXECUTED_RE.match(content)
        if executed is not None:
            name = executed.group("name")
            view.confirmed_side[name] = _side_from_role_label(
                executed.group("role_label")
            )
            continue

        # Night-victim reveal — always a confirmed Law-abiding. (Tested before
        # an announce/ballot could match; the victim template is its own shape.)
        victim = _VICTIM_REVEAL_RE.match(content)
        if victim is not None:
            name = victim.group("name")
            view.confirmed_side[name] = "law_abiding"
            view.night_victims.append(name)
            continue

        # Vote announce — opens a ballot on this target; the initiator made a
        # proposal move against it.
        announce = _ANNOUNCE_RE.match(content)
        if announce is not None:
            initiator = announce.group("initiator")
            target = announce.group("target")
            active_target_name = target
            view.moves.append(
                _MoveAgainst(
                    actor_name=initiator, target_name=target, kind="propose"
                )
            )
            continue

        # Per-ballot — a Yes/No against the open vote's target.
        ballot = _BALLOT_RE.match(content)
        if ballot is not None and active_target_name is not None:
            voter = ballot.group("voter")
            yes = ballot.group("vote_label") == _BALLOT_YES_LABEL
            view.moves.append(
                _MoveAgainst(
                    actor_name=voter,
                    target_name=active_target_name,
                    kind="yes" if yes else "no",
                )
            )

    return view


def _living_non_self(
    players: dict[str, PlayerState], self_id: str
) -> list[PlayerState]:
    """Living players other than the seat itself, in roster (insertion) order."""
    return [
        p for p in players.values() if p.is_alive and p.id != self_id
    ]


def score_suspicion(
    view: PublicView,
    players: dict[str, PlayerState],
    self_id: str,
) -> dict[str, float]:
    """Per-living-player suspicion score from the public view (higher = suspect).

    Applies the weighted table (the module ``W_*`` constants) over every public
    move-against in ``view.moves``, scored against the side that move's target
    was CONFIRMED to be — confirmation is retrospective (a vote cast before a
    side was revealed still scores once the side becomes confirmed, because the
    walk resolved all sides first and we read ``view.confirmed_side`` here).
    A night-victim's prior hunters gain :data:`W_NIGHT_VICTIM_HUNTER`.

    Returns a map over LIVING players excluding the seat itself (``self_id``) and
    excluding any player whose side is already publicly confirmed (a known
    Mafioso/Citizen is no longer a live suspect to weigh — a dead executed player
    carries no live score either, since it is not living). Names are resolved to
    ids via the uniquely-held-name index; an unresolved name is skipped.

    Reads NO ``.role`` field — purely the reconstructed public view. PURE/
    deterministic: same view ⇒ same scores.
    """
    index = _name_index(players)
    # Seed every living non-self, not-yet-confirmed player at 0.0 so an all-quiet
    # table still yields a full (all-zero) map — the LA "no suspect" path.
    scores: dict[str, float] = {}
    for player in _living_non_self(players, self_id):
        if player.name in view.confirmed_side:
            continue  # already publicly confirmed — not a live suspect
        scores[player.id] = 0.0

    def _add(name: str, delta: float) -> None:
        actor = index.get(name)
        if actor is None or actor.id not in scores:
            return
        scores[actor.id] += delta

    for move in view.moves:
        target_side = view.confirmed_side.get(move.target_name)
        if target_side is None:
            continue  # target's side not (yet) confirmed — no weight
        match (move.kind, target_side):
            case ("propose", "mafia"):
                _add(move.actor_name, W_PROPOSE_EXECUTE_MAFIA)
            case ("yes", "mafia"):
                _add(move.actor_name, W_YES_EXECUTE_MAFIA)
            case ("propose", "law_abiding"):
                _add(move.actor_name, W_PROPOSE_EXECUTE_LAW)
            case ("yes", "law_abiding"):
                _add(move.actor_name, W_YES_EXECUTE_LAW)
            case ("no", "mafia"):
                _add(move.actor_name, W_SPARE_MAFIA)
            case ("no", "law_abiding"):
                _add(move.actor_name, W_SPARE_LAW)

    # The Mafia silence their hunters: whoever a night victim had earlier moved
    # against (proposed or Yes-voted) gains suspicion. The bump is per
    # (victim, target) RELATIONSHIP, applied once — a hunter who both proposed
    # AND Yes-voted the same victim's target is still one hunter, not two.
    victim_names = set(view.night_victims)
    hunted_pairs: set[tuple[str, str]] = set()
    for move in view.moves:
        if move.actor_name in victim_names and move.kind in ("propose", "yes"):
            hunted_pairs.add((move.actor_name, move.target_name))
    for _victim_name, target_name in hunted_pairs:
        _add(target_name, W_NIGHT_VICTIM_HUNTER)

    return scores


@dataclass(slots=True)
class Decision:
    """One scripted-player decision, discriminated by the interrupt kind it answers.

    - ``day_turn`` → either ``action="speak"`` with ``text`` set, or
      ``action="vote"`` with ``target_name`` set (the driver resumes the human
      ``/vote`` slash-command branch, which fuzzy-matches the display NAME).
    - ``vote`` → ``action="ballot"`` with ``yes`` set (the driver resumes
      ``"yes"``/``"no"``).
    - ``point`` → ``action="point"`` with ``target_id`` set (the driver resumes
      the chosen target's id directly).
    """

    action: Literal["speak", "vote", "ballot", "point"]
    text: str | None = None
    target_name: str | None = None
    target_id: str | None = None
    yes: bool | None = None


def _highest_suspicion_id(scores: dict[str, float]) -> str | None:
    """The most-suspect living player's id; deterministic id tie-break.

    Highest score wins; ties broken by lexical player id (never RNG). Returns
    ``None`` for an empty map (no living suspects). Selecting ``max`` over a
    ``(score, -reverse-id)`` key is awkward to read, so we sort the candidate ids
    by ``(-score, id)`` and take the first — highest score, then lowest id.
    """
    if not scores:
        return None
    return sorted(scores, key=lambda pid: (-scores[pid], pid))[0]


def _name_of(players: dict[str, PlayerState], player_id: str) -> str:
    """Display name for a player id (falls back to the id if unresolved)."""
    player = players.get(player_id)
    return player.name if player is not None else player_id


def _confirmed_mafia_names(view: PublicView) -> list[str]:
    """Names publicly confirmed Mafia, in first-confirmed (dict insertion) order."""
    return [
        name for name, side in view.confirmed_side.items() if side == "mafia"
    ]


def law_abiding_decision(
    view: PublicView,
    scores: dict[str, float],
    players: dict[str, PlayerState],
    self_id: str,
    *,
    kind: Literal["day_turn", "vote"],
    last_round: bool,
    open_vote_target: str | None = None,
) -> Decision:
    """The Law-abiding-seat policy over the public view + suspicion scores.

    - ``day_turn``: on the FINAL discussion round (``last_round``), return a
      vote-INITIATION on the highest-suspicion living non-self player (so a
      correct town majority can actually form). Otherwise STATE the noted facts /
      current top suspicion as the speech text (visible reasoning in the
      transcript). With no living suspect at all, speaks a neutral observation.
    - ``vote``: on the open ballot's target (``open_vote_target``, an id), vote
      **Yes** iff ``scores[target] >= SUSPICION_THRESHOLD``, else **No** (the
      all-quiet table yields No, not a coin-flip own-goal).

    Selection ties break deterministically by player id (:func:`_highest_suspicion_id`).
    Reads NO ``.role`` — only the public view + the suspicion map.
    """
    if kind == "vote":
        if open_vote_target is None:
            return Decision(action="ballot", yes=False)
        yes = scores.get(open_vote_target, 0.0) >= SUSPICION_THRESHOLD
        return Decision(action="ballot", yes=yes)

    # kind == "day_turn"
    top_id = _highest_suspicion_id(scores)

    if last_round and top_id is not None:
        return Decision(
            action="vote", target_name=_name_of(players, top_id)
        )

    # Speak: state the noted facts and the current top suspicion.
    return Decision(action="speak", text=_la_speech(view, players, scores, top_id))


def _la_speech(
    view: PublicView,
    players: dict[str, PlayerState],
    scores: dict[str, float],
    top_id: str | None,
) -> str:
    """Compose the LA seat's spoken line — its noted facts + top suspicion.

    States any confirmed Mafia reveals it has noted and then its current top
    suspect by NAME (so the suspect's name appears in the transcript, the AC6
    "states facts" assertion). With no suspect carrying any signal, voices a
    neutral keep-watching line — still mentioning a noted reveal when one exists.
    """
    mafia_names = _confirmed_mafia_names(view)
    facts = ""
    if mafia_names:
        revealed = ", ".join(mafia_names)
        facts = f"{revealed} was revealed to be Mafia. "

    if top_id is not None and scores.get(top_id, 0.0) > 0.0:
        suspect = _name_of(players, top_id)
        return f"{facts}I most suspect {suspect} based on how the votes have gone."
    if top_id is not None:
        suspect = _name_of(players, top_id)
        return (
            f"{facts}Nothing is settled yet, but I'm watching {suspect} "
            "most closely."
        )
    return f"{facts}I don't have a firm read on anyone living yet."


def mafia_decision(
    view: PublicView,
    scores: dict[str, float],
    players: dict[str, PlayerState],
    self_id: str,
    teammate_ids: set[str],
    *,
    kind: Literal["day_turn", "vote", "point"],
    last_round: bool,
    open_vote_target: str | None = None,
) -> Decision:
    """The Mafioso-seat policy (push-parity, protect-teammates).

    Target choice = the strongest hunter: among LIVING NON-TEAMMATES, the player
    the *same public scoring* marks MOST town-aligned (lowest suspicion =
    ``argmin``; deterministic id tie-break). Killing/convicting that player is
    the biggest blow to the town's deduction.

    - ``day_turn``: on the final round, return a vote-INITIATION on that target;
      otherwise voice a (deceptive) suspicion of the target as speech text. The
      speech NEVER names a teammate and NEVER reveals the seat's side.
    - ``vote``: **Yes** on any NON-teammate open target; **No** (spare) on any
      teammate open target — unconditional teammate protection (overrides any
      suspicion threshold).
    - ``point`` (night): return the chosen target's id. The candidate set is
      non-teammates only, so a teammate is never pointed at by construction.

    ``teammate_ids`` is legitimate self-knowledge (the other ``role=="mafia"``
    ids, known from the private teammate intro). All OTHER players are still
    scored from the public view only.
    """
    target_id = _mafia_target_id(scores, players, self_id, teammate_ids)

    if kind == "vote":
        if open_vote_target is None:
            return Decision(action="ballot", yes=False)
        # Spare any teammate unconditionally; push any non-teammate.
        yes = open_vote_target not in teammate_ids
        return Decision(action="ballot", yes=yes)

    if kind == "point":
        # Night point at the chosen non-teammate hunter (id resume).
        return Decision(action="point", target_id=target_id)

    # kind == "day_turn"
    if last_round and target_id is not None:
        return Decision(
            action="vote", target_name=_name_of(players, target_id)
        )
    return Decision(
        action="speak", text=_mafia_speech(players, target_id)
    )


def _mafia_target_id(
    scores: dict[str, float],
    players: dict[str, PlayerState],
    self_id: str,
    teammate_ids: set[str],
) -> str | None:
    """The lowest-suspicion living non-teammate (strongest hunter); id tie-break.

    Candidate set = living players that are neither the seat itself nor a
    teammate. ``argmin`` over the public suspicion score (most town-aligned =
    lowest score = strongest anti-Mafia hunter), ties broken by lexical id. A
    player not present in ``scores`` (e.g. one whose side is publicly confirmed,
    so dropped from the suspect map) is treated as score 0.0 for ranking so a
    confirmed-Law-abiding hunter still ranks as a strong threat. Returns ``None``
    when no non-teammate target is alive.
    """
    candidates = [
        p.id
        for p in players.values()
        if p.is_alive and p.id != self_id and p.id not in teammate_ids
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda pid: (scores.get(pid, 0.0), pid))[0]


def _mafia_speech(
    players: dict[str, PlayerState], target_id: str | None
) -> str:
    """Compose the Mafioso seat's deceptive suspicion line.

    Voices suspicion of the chosen Law-abiding target by NAME only — it NEVER
    names a teammate and NEVER reveals the seat's own side or that it is Mafia
    (the never-reveal invariant, asserted by test). With no target it voices a
    neutral keep-watching line.
    """
    if target_id is None:
        return "I'm not sure who to trust yet — let's keep talking."
    suspect = _name_of(players, target_id)
    return (
        f"{suspect} has been steering things in a way that worries me — "
        "I think we should keep a close eye there."
    )
