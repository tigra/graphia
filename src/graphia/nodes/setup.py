"""Setup-phase nodes: collect human name, build roster, introduce it."""

from __future__ import annotations

import dataclasses
import difflib
import random
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import ValidationError

from graphia.career_events import (
    KIND_GAME_STARTED,
    CareerEvent,
    CareerEventEmitter,
)
from graphia.config import GraphiaConfig, load_config
from graphia.llm import Persona, Roster, get_large, get_persona_model, get_small
from graphia.prompts import (
    NAME_GEN_SYSTEM,
    NAME_GEN_USER_TEMPLATE,
    PERSONA_ARCHETYPE_HINT_TEMPLATE,
    PERSONA_ARCHETYPES,
    PERSONA_CITIZEN_USER_TEMPLATE,
    PERSONA_DISTINCT_FROM_TEMPLATE,
    PERSONA_MAFIA_USER_TEMPLATE,
    PERSONA_SYSTEM,
    ROSTER_INTRO_TEMPLATE,
)
from graphia.state import GameState, PlayerPersona, PlayerState

# Spec 034: the spec-009 lexical machinery, IMPORTED (never reimplemented) so the
# in-loop collision check and the recorded ``persona_lex_*`` metric speak the same
# language — a "collision" the regen loop catches is the same near-duplication the
# ledger measures. ``_mask_names`` blanks the AI names before comparing;
# ``_normalize`` lowercases / collapses whitespace. Aliased to the spec-009 names
# the blunder-eval scorers use for one shared vocabulary.
from graphia.tools.repetition_experiment import (
    _mask_names as _spec009_mask_names,
    _normalize as _spec009_normalize,
)

_ROLE_LABELS: dict[str, str] = {
    "mafia": "Mafia",
    "law_abiding": "Law-abiding Citizen",
}


def _shuffle_deck(deck: list[str]) -> None:
    """Shuffle the role deck in place via the module-global ``random`` RNG.

    The single role-deal shuffle surface (architecture §6), mirroring the Day
    phase's ``graphia.nodes.day._shuffle_order`` and the Night phase's
    ``graphia.nodes.night._shuffle_mafia_order``. Lifting the inline
    ``random.shuffle(deck)`` out of :func:`assign_roles` gives tests one
    monkeypatch point to pin the deal deterministically (substitute a no-op /
    identity so the deck keeps its constructed order) without seeding the
    module-global RNG — keeping a test's trajectory pinned by intent and immune
    to cross-test global-RNG state. Production behaviour is unchanged.
    """
    random.shuffle(deck)


def _shuffle_personas(
    prior: list[PlayerPersona], *, enabled: bool
) -> list[PlayerPersona]:
    """Return the already-created personas in a (possibly) randomized order.

    The single persona-order shuffle surface (Spec 034 §2 A), mirroring
    ``graphia.nodes.night._shuffle_night_roster`` exactly: copy the list, shuffle
    the copy over the module-global ``random`` RNG, return it; never mutate the
    input. Applied to the prior-persona list ``_distinct_from_message`` renders,
    so each new character sees the others in a fresh order with no fixed anchor —
    one of the three diversity levers (functional-spec §2). Keeping it the only
    place that order is randomized gives tests one monkeypatch point.

    **The load-bearing OFF contract (mirrors §3.1 of spec 030):** when ``enabled``
    is falsy the input order is returned **before any RNG call whatsoever**, so a
    flag-OFF build consumes ZERO module-global ``random`` state and reproduces the
    prior spec-031 (insertion-order) trajectory byte-for-byte. The ``not enabled``
    guard MUST stay ahead of ``random.shuffle`` for that promise to hold — the
    dual-mode byte-equal smoke depends on it.
    """
    if not enabled:
        # OFF: spec-031 insertion order, with no draw — preserves the seeded
        # trajectory byte-for-byte (the ablation flag's whole point).
        return list(prior)
    shuffled = list(prior)
    random.shuffle(shuffled)
    return shuffled


def _draw_archetypes(count: int, *, enabled: bool) -> list[str | None]:
    """Draw ``count`` target temperaments, one per AI player (Spec 034 §2 A).

    When ``enabled``, sample WITHOUT replacement within the game from
    :data:`graphia.prompts.PERSONA_ARCHETYPES` over the module-global ``random``
    RNG, so each player targets a *distinct* temperament and the cast starts
    spread across the range. When the roster is larger than the pool (it never is
    at the table cap, but be defensive), the pool is drawn down to empty and the
    remaining players get a fresh independent draw — still randomized, just no
    longer guaranteed-distinct. Returns a list of ``count`` archetype strings.

    **The load-bearing OFF contract:** when ``enabled`` is falsy, returns
    ``[None] * count`` **before any RNG call whatsoever** — no archetype hint, no
    module-global ``random`` draw — so flag-OFF preserves the spec-031 trajectory
    byte-for-byte (paired with :func:`_shuffle_personas`'s OFF guard). The
    ``not enabled`` guard MUST stay ahead of ``random.*``.
    """
    if not enabled:
        return [None] * count
    pool = list(PERSONA_ARCHETYPES)
    random.shuffle(pool)
    drawn: list[str | None] = []
    for _ in range(count):
        if not pool:
            # Roster exceeds the pool (defensive — not reachable at the table
            # cap): refill and reshuffle so the remaining players still get a
            # randomized steer (no longer guaranteed distinct).
            pool = list(PERSONA_ARCHETYPES)
            random.shuffle(pool)
        drawn.append(pool.pop())
    return drawn


def _persona_table_text(persona: PlayerPersona) -> str:
    """The table-facing text of a persona — never the Mafioso ``true_self``.

    ``personality + " " + manner + " " + public_persona``, IDENTICAL to the text
    ``score_persona_sim_sum`` / ``score_persona_near_dup`` build, so the in-loop
    collision check and the recorded ``persona_lex_*`` metric compare the same
    string. A Mafioso's hidden ``true_self`` is deliberately excluded (the
    spec-016 allegiance-hiding invariant) — only the public character the table
    sees is differentiated.
    """
    return f"{persona.personality} {persona.manner} {persona.public_persona}"


def _persona_collision(
    candidate: PlayerPersona,
    accepted: list[PlayerPersona],
    *,
    ai_names: set[str] | None = None,
) -> float:
    """Max lexical similarity of ``candidate`` against the accepted personas (Spec 034 §2 B).

    Builds each persona's **table-facing** text (:func:`_persona_table_text` —
    ``personality + " " + manner + " " + public_persona``, **never** the Mafioso
    ``true_self``), name-masks it (:func:`_spec009_mask_names` against ``ai_names``)
    and normalizes it (:func:`_spec009_normalize`), then returns the **max**
    ``difflib.SequenceMatcher`` ratio of the candidate's text against each accepted
    persona's text. EXACTLY the spec-009 machinery behind the recorded
    ``persona_lex_*`` metric — so a "collision" here is the same near-duplication
    the ledger measures, name-masked the same way (a shared self-name token can't
    inflate the similarity between two otherwise-different characters).

    Pure, deterministic, no model. Returns ``0.0`` when there are no accepted
    personas (the first player of a game can never collide). ``ai_names`` defaults
    to the empty set when the caller has no roster names handy (normalization still
    applies); ``generate_personas`` passes the game's AI names for metric parity.
    """
    if not accepted:
        return 0.0
    names = ai_names or set()

    def masked(persona: PlayerPersona) -> str:
        return _spec009_normalize(
            _spec009_mask_names(_persona_table_text(persona), names)
        )

    cand = masked(candidate)
    return max(
        difflib.SequenceMatcher(None, cand, masked(other)).ratio()
        for other in accepted
    )


def collect_name(state: GameState) -> dict:
    value = interrupt({"kind": "name"})
    name = value.strip() if isinstance(value, str) else ""
    if not name:
        name = "Player"
    player_id = str(uuid.uuid4())
    human = PlayerState(
        id=player_id,
        name=name,
        role="law_abiding",
        is_human=True,
        is_alive=True,
    )
    return {
        "human_id": player_id,
        "players": {player_id: human},
        "phase": "setup",
        "cycle": 1,
        "human_votes_called": 0,
        "human_ballots_cast": 0,
        "human_night_attempts": 0,
        "human_night_successes": 0,
        "night_victim_count": 0,
        "execution_count": 0,
        "messages": [SystemMessage(content=f"A new game begins. Welcome, {name}.")],
    }


def _coerce_to_count(roster: Roster | None, count: int) -> Roster:
    """Force a roster to exactly ``count`` distinct names.

    The pure, last-resort guarantee behind the deck/roster invariant: whatever
    the model returned (or failed to return), this yields exactly ``count``
    distinct names so the role-mapping loop in :func:`assign_roles` can never
    ``IndexError``. Trims to the first ``count`` distinct names when given too
    many; pads with deterministically-distinct ``Player-{k}`` placeholders —
    skipping any that would collide (case-insensitively) with a name already
    present — when given too few or ``None``.

    **Why distinctness is case-INSENSITIVE, and why that is deliberate rather
    than overzealous (spec 042, Task 7.1 — reviewed and KEPT).** ``Ivy`` and
    ``IVY`` collapse to one name here, so a response carrying both is one seat
    short and gets padded. Two reasons that is the right rule, not a defect:

    - The human's vote command resolves a target through
      ``graphia.nodes.day._fuzzy_match_alive``, which matches the typed needle
      as a **case-insensitive substring** of every alive player's name and
      refuses to act when two players match. ``Ivy`` and ``IVY`` at one table
      would therefore be *permanently unvotable* — every attempt at either
      returns "No such player. Try again." That is the same class of collision
      Task 3.3 removed from the roster fixture, and a harder break than a
      padded seat.
    - Names are spoken in dialogue, where case does not exist. Two players the
      table cannot tell apart by name break the deduction the game is built on.

    On the production parse path this branch is unreachable anyway: the same
    rule lives in ``Roster``'s validator, so a case-variant response raises
    :class:`~pydantic.ValidationError` and :func:`_generate_names` issues its
    corrective retry instead of silently padding. The dedup here is the
    defensive restatement that keeps the invariant unconditional for a roster
    that never went through the validator.
    """
    names = list(roster.names) if roster is not None else []
    # De-dup defensively (case-insensitive) while preserving order; the schema
    # validator already enforces this on parsed rosters, but a coerced result
    # must hold the invariant unconditionally.
    seen: set[str] = set()
    distinct: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            distinct.append(name.strip())
    if len(distinct) >= count:
        return Roster(names=distinct[:count])
    k = 1
    while len(distinct) < count:
        placeholder = f"Player-{k}"
        if placeholder.lower() not in seen:
            seen.add(placeholder.lower())
            distinct.append(placeholder)
        k += 1
    return Roster(names=distinct)


def _generate_names(count: int) -> Roster:
    """Return exactly ``count`` distinct AI names.

    Validation-retry-then-coerce: invoke the small model for ``count`` names;
    if it parses to exactly ``count``, return it. On a :class:`ValidationError`
    or a wrong count, do one corrective retry naming the exact count. If that
    still fails or is the wrong count, :func:`_coerce_to_count` trims/pads to a
    guaranteed ``count`` distinct names — the result is *always* exactly
    ``count`` (never an ``IndexError`` in :func:`assign_roles`).

    **Two consecutive validation failures give an ALL-placeholder roster, and
    that is accepted behaviour (spec 042, Task 7.1 — reviewed and KEPT).** The
    first ``except`` clears ``roster`` to ``None`` and the second has nothing
    to put back, so ``_coerce_to_count(None, count)`` returns
    ``Player-1 … Player-{count}`` and every AI seat is a placeholder. Reachable
    in production against a flaky local model. It is kept, for three reasons:

    - It is what the never-block guarantee *means* at its limit. This
      function's whole contract is that a flaky or missing small model must not
      stop the game (the posture :func:`_fallback_persona` mirrors for
      personas). Raising here would trade a degraded-but-fully-playable table
      for no game at all, in precisely the flaky-model scenario the fallback
      exists to survive.
    - The degradation is cosmetic, not mechanical. Every game mechanic still
      works on a ``Player-N`` table: the names are distinct, and each one
      resolves uniquely through the vote matcher when typed in full. Mafia is
      played on behaviour, not on names. The player also *sees* it immediately
      — :func:`introduce_roster` reads the whole roster out to the table — so
      the failure is visible, just not flagged as one. One precise caveat, at
      tables of eleven seats or more only: the placeholders stop being
      prefix-free of one another there (``Player-1`` opens ``Player-10``), and
      ``graphia.nodes.day._fuzzy_match_alive`` matches on **substring**, so
      the abbreviation ``Player-1`` becomes ambiguous and is refused. Typing
      the full name still resolves, and the default eight-seat table never
      reaches it — but it is the one place this table is worse than a
      model-named one, so do not read the bullet above as "no consequences at
      any lineup".
    - It is the consistent treatment of the failure this function already
      distinguishes. Only :class:`ValidationError` is caught, so a broken model
      or transport (throttling, a dead endpoint) propagates and surfaces as an
      error banner. Two validation failures mean the model *answered* twice
      with something unparseable — the recover-and-coerce category — so
      crashing on the second would contradict how the same function treats the
      first.

    What is genuinely missing is a **proactive** signal, and every mechanism
    for one lies outside this file: the JSONL trace and the AgentCore
    CloudWatch trace are both fed by the driver's ``graph.stream`` deltas and
    record node/keys rather than values, and a node holds no logger — so
    surfacing "this roster is entirely synthetic" would mean threading a
    diagnostic sink through ``graph.py`` and the driver. That is its own
    deliberate change, not a lineup spec's to make; recorded here so the next
    reader knows the gap was measured rather than missed.
    """
    llm = get_small().with_structured_output(Roster)
    user_prompt = NAME_GEN_USER_TEMPLATE.format(count=count)
    messages: list = [
        SystemMessage(content=NAME_GEN_SYSTEM),
        HumanMessage(content=user_prompt),
    ]
    try:
        roster = llm.invoke(messages)
        if len(roster.names) == count:
            return roster
    except ValidationError:
        roster = None

    retry_messages = [
        SystemMessage(content=NAME_GEN_SYSTEM),
        HumanMessage(content=user_prompt),
        HumanMessage(
            content=f"That was invalid: return exactly {count} distinct, "
            "non-empty first names via the Roster schema. Try again."
        ),
    ]
    try:
        retried = llm.invoke(retry_messages)
        if len(retried.names) == count:
            return retried
        roster = retried
    except ValidationError:
        pass
    return _coerce_to_count(roster, count)


def ai_name_count(config: GraphiaConfig) -> int:
    """How many AI names a roster needs at ``config``'s lineup.

    The configured counts are **whole-table** totals that INCLUDE the human
    seat: ``num_citizens + num_mafia`` is the number of players sitting down,
    exactly one of whom is the human. The ``- 1`` is that human seat — every
    other seat is an AI player needing a generated name. This is the ``count``
    :func:`_generate_names` is asked for, and the length
    :func:`_coerce_to_count` then guarantees.

    Named rather than inlined at its one production call site because a **test
    fixture calls this very function** (``fake_small`` in ``tests/conftest.py``,
    spec 042): the roster fake answers with exactly as many names as the game
    asked for, instead of a hard-coded list sized to whatever the default lineup
    happened to be when it was written. That is the seam's whole reason for
    existing — a second copy of the arithmetic living in the fixture could
    silently disagree with this one if the formula ever changed, and the fake
    would then starve its own queue on ``_generate_names``' corrective retry.
    So: do not inline it back.
    """
    return config.num_citizens + config.num_mafia - 1


def generate_roster(state: GameState) -> dict:
    config = load_config()
    ai_count = ai_name_count(config)
    roster = _generate_names(ai_count)
    new_players: dict[str, PlayerState] = {}
    for ai_name in roster.names:
        pid = str(uuid.uuid4())
        new_players[pid] = PlayerState(
            id=pid,
            name=ai_name,
            role="law_abiding",
            is_human=False,
            is_alive=True,
        )
    existing = state.get("players", {})
    return {"players": {**existing, **new_players}}


def assign_roles(
    state: GameState,
    *,
    career_emitter: CareerEventEmitter | None = None,
    game_id: str | None = None,
) -> dict:
    config = load_config()
    deck: list[str] = (
        ["mafia"] * config.num_mafia + ["law_abiding"] * config.num_citizens
    )
    if config.human_role is None:
        _shuffle_deck(deck)
        roles = deck
    else:
        # Human is always the first inserted player; surface mis-seating loudly.
        assert state["human_id"] == next(iter(state["players"]))
        pinned_role = config.human_role
        deck.remove(pinned_role)
        _shuffle_deck(deck)
        roles = [pinned_role, *deck]
    existing = state.get("players", {})
    # Companion to the mis-seating invariant above (spec 042, Task 7.1). The
    # deck is dealt onto the player map BY INDEX, so a map that disagrees with
    # the deck fails *silently* in one direction — a short map drops the surplus
    # cards, and the table it produces holds the wrong number of Mafiosos — and
    # obscurely in the other, where ``roles[index]`` raises a bare
    # ``IndexError`` naming neither number.
    #
    # NOT reachable from production: ``generate_roster`` mints exactly
    # ``ai_name_count(config)`` AI seats onto the single human seat, and
    # ``_coerce_to_count`` guarantees that length unconditionally, so the two
    # always agree. This is a robustness guard for hand-built states, and it is
    # loud because the silent direction is expensive to diagnose: a seven-seat
    # test map dealt against an eight-card deck dropped one card, and only the
    # runs where the dropped card happened to be a Mafioso failed at all (spec
    # 042, Task 3.6 — 38.7% of runs, missed entirely by two separate flip
    # trials that each read as green). An assertion here would have made that a
    # deterministic first-run failure naming both sizes.
    assert len(roles) == len(existing), (
        f"role deck ({len(roles)} cards) and player map ({len(existing)} "
        f"seats) disagree at lineup {config.num_citizens} law-abiding + "
        f"{config.num_mafia} mafia: a short map silently drops the surplus "
        "roles, a longer one IndexErrors on the deal."
    )
    human_id = state["human_id"]
    updated: dict[str, PlayerState] = {}
    human_role = "law_abiding"
    for index, (pid, player) in enumerate(existing.items()):
        role = roles[index]
        if pid == human_id:
            human_role = role
        # Only the dealt role changes; every other field (id, name, is_human,
        # is_alive, persona, …) carries forward via ``replace``.
        updated[pid] = dataclasses.replace(player, role=role)  # type: ignore[arg-type]
    if career_emitter is not None and game_id is not None:
        career_emitter.emit(
            game_id,
            CareerEvent(
                kind=KIND_GAME_STARTED,
                session_id=game_id,
                human_role=human_role,
            ),
        )
    return {"players": updated, "human_role": human_role}


def _fallback_persona(player: PlayerState) -> PlayerPersona:
    """A deterministic minimal persona derived only from the player's name.

    The last-resort guarantee behind :func:`generate_personas`: when the model
    fails (or returns a clearly-empty result) twice, this yields a valid,
    name-anchored persona so setup never blocks. A Mafioso gets a generic
    "secretly a Mafioso" ``true_self``; a Citizen's ``true_self`` is empty.
    """
    name = player.name
    is_mafia = player.role == "mafia"
    return PlayerPersona(
        personality=f"{name} is an ordinary, even-tempered townsperson.",
        manner=f"{name} speaks plainly and to the point.",
        public_persona=f"{name} is a familiar face around town, trusted by neighbours.",
        true_self=(
            f"{name} is secretly a Mafioso, hiding behind an ordinary cover."
            if is_mafia
            else ""
        ),
    )


def _persona_is_empty(persona: Persona | None) -> bool:
    """True when a parsed persona is missing the fields we need for a voice.

    A clearly-empty result (no personality, manner, or public backstory) is
    treated like a failure and triggers the corrective retry / fallback —
    mirroring ``_generate_names``' wrong-shape handling for free-prose output.
    """
    if persona is None:
        return True
    return not (
        persona.personality.strip()
        and persona.manner.strip()
        and persona.public_backstory.strip()
    )


def _distinct_from_message(
    prior_personas: list[PlayerPersona],
) -> HumanMessage | None:
    """Build the spec-031 "make this one clearly different" instruction.

    Renders each already-created persona's TABLE-FACING text only
    (``personality`` + ``manner`` + ``public_persona``, one per line) into
    :data:`PERSONA_DISTINCT_FROM_TEMPLATE`. A Mafioso's hidden ``true_self`` is
    deliberately NEVER threaded — the differentiation target is the public
    character the table sees (functional-spec §2.2), and excluding the secret
    keeps hidden content out of every other character's generation prompt by
    construction (the §2.4 / spec-016 allegiance-hiding invariant). Returns
    ``None`` when there are no prior personas (the first AI player of a game has
    nothing yet to differ from), so the caller appends no message.
    """
    if not prior_personas:
        return None
    others = "\n".join(
        f"- {p.personality} {p.manner} {p.public_persona}".strip()
        for p in prior_personas
    )
    return HumanMessage(content=PERSONA_DISTINCT_FROM_TEMPLATE.format(others=others))


def _archetype_message(archetype: str | None) -> HumanMessage | None:
    """Build the spec-034 "lean toward this temperament" steer, or ``None``.

    Renders :data:`PERSONA_ARCHETYPE_HINT_TEMPLATE` with the drawn ``archetype``
    into a SEPARATE :class:`HumanMessage` (NOT a ``{...}`` slot on the persona
    user templates — the same anti-``KeyError`` discipline as the spec-031
    distinct-from block). Returns ``None`` when no archetype was drawn (diversity
    flag off), so the caller appends no message and the prompt is the spec-031
    shape exactly. The hint carries no allegiance signal, so it rides safely on
    both Citizen and Mafioso prompts — even the FIRST player (no prior to differ
    from, but still steered to a random temperament).
    """
    if archetype is None:
        return None
    return HumanMessage(
        content=PERSONA_ARCHETYPE_HINT_TEMPLATE.format(archetype=archetype)
    )


def _generate_one_persona(
    player: PlayerState,
    prior_personas: list[PlayerPersona],
    *,
    model: BaseChatModel | None = None,
    archetype: str | None = None,
) -> PlayerPersona:
    """Generate a single AI player's persona, role-tailored, never raising.

    Validation-retry-then-fallback (mirrors :func:`_generate_names`, but with
    BROAD exception catching since free-prose personas have no exact-shape
    invariant to validate beyond non-emptiness): invoke the large model with a
    role-tailored prompt anchored on the player's name; on any failure or a
    clearly-empty result, do one corrective retry; if that still fails, return
    a deterministic :func:`_fallback_persona`. The result is *always* a valid
    :class:`PlayerPersona`, so a flaky or missing model never blocks setup.

    Spec 031 (option b): when ``prior_personas`` (the characters already created
    for this game) is non-empty, ONE additional :class:`HumanMessage` is
    appended — a "make this character clearly different from these" block built
    from the prior personas' table-facing text — so the creative model
    differentiates instead of reaching for the same modal townsperson. The block
    rides on BOTH the first attempt and the corrective retry, so a retried
    persona still differentiates. The first AI player of a game (empty
    ``prior_personas``) gets no block; the deterministic
    :func:`_fallback_persona` is unchanged.

    Spec 034: ``model`` is the persona model to invoke — the caller passes the
    higher-temperature :func:`graphia.llm.get_persona_model` instance when
    diversity is on, falling back to the cached gameplay ``get_large()`` when it
    is ``None`` (flag-off → spec-031 behaviour exactly). ``archetype`` is the
    drawn target temperament; when set, a SECOND extra :class:`HumanMessage` (the
    "lean toward this" steer) rides on BOTH the first attempt and the retry,
    alongside any distinct-from block. The caller already shuffled
    ``prior_personas`` (a fresh order per attempt) and drew a fresh ``archetype``
    per regeneration, so a retry diverges rather than repeating.
    """
    is_mafia = player.role == "mafia"
    template = (
        PERSONA_MAFIA_USER_TEMPLATE if is_mafia else PERSONA_CITIZEN_USER_TEMPLATE
    )
    user_prompt = template.format(name=player.name)
    distinct_from = _distinct_from_message(prior_personas)
    archetype_hint = _archetype_message(archetype)
    # Spec 034: flag-off passes ``model=None`` → the cached gameplay singleton,
    # so the disabled path is byte-identical to spec 031 (no separate model
    # built). Flag-on passes the higher-temperature persona model.
    base = model if model is not None else get_large()
    llm = base.with_structured_output(Persona)

    def _extras(messages: list) -> None:
        # The two spec-031/034 steer messages ride on BOTH attempts, appended in
        # a stable order (distinct-from then archetype) so the prompt shape is
        # deterministic for the capture tests.
        if distinct_from is not None:
            messages.append(distinct_from)
        if archetype_hint is not None:
            messages.append(archetype_hint)

    messages: list = [
        SystemMessage(content=PERSONA_SYSTEM),
        HumanMessage(content=user_prompt),
    ]
    _extras(messages)
    try:
        persona = llm.invoke(messages)
        if not _persona_is_empty(persona):
            return _to_player_persona(persona, is_mafia=is_mafia, player=player)
    except Exception:
        persona = None

    retry_messages = [
        SystemMessage(content=PERSONA_SYSTEM),
        HumanMessage(content=user_prompt),
        HumanMessage(
            content="That response was unusable: return a non-empty "
            "`personality`, `manner`, and `public_backstory` via the Persona "
            "schema. Try again."
        ),
    ]
    _extras(retry_messages)
    try:
        retried = llm.invoke(retry_messages)
        if not _persona_is_empty(retried):
            return _to_player_persona(retried, is_mafia=is_mafia, player=player)
    except Exception:
        pass
    return _fallback_persona(player)


def _to_player_persona(
    persona: Persona, *, is_mafia: bool, player: PlayerState
) -> PlayerPersona:
    """Convert a flat :class:`Persona` to the in-state :class:`PlayerPersona`.

    ``public_backstory`` becomes the table-facing ``public_persona``; a
    Mafioso's ``secret_backstory`` becomes ``true_self`` (falling back to a
    generic cover line if the model left it empty); a Citizen's ``true_self``
    is always empty.
    """
    secret = persona.secret_backstory.strip()
    if is_mafia and not secret:
        secret = f"{player.name} is secretly a Mafioso, hiding behind an ordinary cover."
    return PlayerPersona(
        personality=persona.personality.strip(),
        manner=persona.manner.strip(),
        public_persona=persona.public_backstory.strip(),
        true_self=secret if is_mafia else "",
    )


def _fresh_regen_archetype(primary: str | None) -> str:
    """Draw a regeneration archetype that differs from this player's primary.

    A regeneration must DIVERGE, not repeat (functional-spec §2: the mode-seeking
    re-collision risk). So a retry's archetype is drawn at random from
    :data:`PERSONA_ARCHETYPES` excluding the player's ``primary`` temperament — a
    different region of the range, the strongest lever to steer the retry
    somewhere new. Over the module-global RNG (seeded for evals).
    """
    pool = [a for a in PERSONA_ARCHETYPES if a != primary]
    return random.choice(pool or list(PERSONA_ARCHETYPES))


def _generate_with_regen(
    player: PlayerState,
    accepted: list[PlayerPersona],
    *,
    model: BaseChatModel,
    primary_archetype: str | None,
    collision_threshold: float,
    regen_attempts: int,
    ai_names: set[str],
) -> PlayerPersona:
    """Generate one persona, regenerating while it collides (Spec 034 §2 B).

    The diversity-on path for a single AI player. The INITIAL attempt is steered
    toward ``primary_archetype`` (this player's distinct, without-replacement
    draw). If the result is too word-level-similar to one already ``accepted``
    this game (``_persona_collision >= collision_threshold``), throw it away and
    generate again with a FRESH shuffle of the prior personas and a FRESH random
    archetype (:func:`_fresh_regen_archetype`, differing from the primary) — so
    the retry diverges rather than re-hitting the same mode — up to
    ``regen_attempts`` extra times. On cap-exhaustion keep the **least-similar**
    valid attempt seen (functional-spec §2: never blocks, never ships the
    over-similar one).

    Generation failure is distinct from collision: :func:`_generate_one_persona`
    returns the deterministic :func:`_fallback_persona` when the model fails
    twice. A fallback is NOT scored or kept as a "least-similar" candidate — the
    spec-031 contract is that a true generation failure falls to the fallback. If
    every attempt fails to generate, the fallback is returned (the last resort).
    """
    expected_fallback = _fallback_persona(player)

    best: PlayerPersona | None = None
    best_score = float("inf")
    # 1 initial attempt + ``regen_attempts`` regenerations.
    for attempt in range(regen_attempts + 1):
        archetype = (
            primary_archetype
            if attempt == 0
            else _fresh_regen_archetype(primary_archetype)
        )
        shuffled_prior = _shuffle_personas(accepted, enabled=True)
        persona = _generate_one_persona(
            player,
            shuffled_prior,
            model=model,
            archetype=archetype,
        )
        if persona == expected_fallback:
            # Generation itself failed (model returned empty twice). Don't score
            # or keep as a least-similar candidate; only fall to it if nothing
            # else is ever produced (handled after the loop).
            continue
        score = _persona_collision(persona, accepted, ai_names=ai_names)
        if score < best_score:
            best, best_score = persona, score
        if score < collision_threshold:
            # Accepted: distinct enough. Stop regenerating.
            return persona
    # Cap exhausted (or every attempt collided): keep the least-similar VALID
    # attempt; only the fallback if generation never succeeded.
    return best if best is not None else expected_fallback


def generate_personas(
    state: GameState,
    *,
    persona_diversity_enabled: bool = True,
    persona_collision_threshold: float = 0.6,
    persona_regen_attempts: int = 2,
    persona_temperature: float = 1.0,
) -> dict:
    """Attach a fresh persona to every AI player (skipping the human).

    Runs after :func:`assign_roles` so each persona can be role-tailored — one
    honest persona for a Law-abiding Citizen, a two-layer cover-legend-plus-true
    -self persona for a Mafioso. Per-player heavyweight calls (N ≤ table cap,
    one-time at startup). Each call is wrapped in the validation-retry-then-
    fallback in :func:`_generate_one_persona`, so this node NEVER raises — a
    failing or missing model yields fallback personas and setup proceeds.

    Spec 031 (option b): accumulate the personas already created THIS game and
    feed them to each subsequent generation so the new character can be made
    deliberately distinct. The first AI player sees an empty list.

    Spec 034 (ADR 011), gated by ``persona_diversity_enabled`` (default on):
    when ON, three diversity levers are injected before each generation — the
    prior personas are shown in a SHUFFLED order (:func:`_shuffle_personas`), the
    character is steered toward a randomly-drawn target temperament
    (:func:`_draw_archetypes`, without replacement within the game), and the
    persona model runs HOTTER (:func:`graphia.llm.get_persona_model` at
    ``persona_temperature``) — and a freshly-created character too word-level
    -similar (``>= persona_collision_threshold``) to one already accepted this
    game is regenerated (fresh shuffle + fresh archetype) up to
    ``persona_regen_attempts``, keeping the least-similar attempt on exhaustion
    (:func:`_generate_with_regen`). When OFF, the path is spec-031 EXACTLY:
    insertion order (no RNG draw — :func:`_shuffle_personas`'s OFF guard), no
    archetype hint, the cached gameplay model, and no regeneration.
    """
    players = state.get("players", {})
    # ``players`` is a plain replace channel (no merge reducer), so the return
    # must carry the *whole* map — the human (skipped below) included — or it
    # would be dropped. Start from the existing players and overwrite only the
    # AI entries with their persona-bearing copies.
    updated: dict[str, PlayerState] = dict(players)

    ai_players = [p for p in players.values() if not p.is_human]
    ai_names = {p.name for p in players.values() if not p.is_human}

    # Spec 034: draw one target temperament per AI player up front, without
    # replacement within the game (OFF → all-None, no RNG draw). Flag-off and the
    # disabled draw both consume ZERO module-global RNG, preserving the spec-031
    # seeded trajectory byte-for-byte (the dual-mode smoke depends on this).
    archetypes = _draw_archetypes(
        len(ai_players), enabled=persona_diversity_enabled
    )
    # Build the higher-temperature persona model ONCE per setup (flag-on only);
    # OFF passes ``None`` to ``_generate_one_persona`` so the cached gameplay
    # ``get_large()`` is used and no separate model is constructed.
    persona_model: BaseChatModel | None = (
        get_persona_model(persona_temperature)
        if persona_diversity_enabled
        else None
    )

    accepted: list[PlayerPersona] = []
    for player, archetype in zip(ai_players, archetypes):
        if persona_diversity_enabled:
            persona = _generate_with_regen(
                player,
                accepted,
                model=persona_model,  # type: ignore[arg-type]
                primary_archetype=archetype,
                collision_threshold=persona_collision_threshold,
                regen_attempts=persona_regen_attempts,
                ai_names=ai_names,
            )
        else:
            # Flag-off: spec-031 path EXACTLY — insertion order, no archetype, the
            # cached gameplay model, no regeneration, no RNG draw.
            persona = _generate_one_persona(
                player,
                _shuffle_personas(accepted, enabled=False),
                model=None,
                archetype=None,
            )
        updated[player.id] = dataclasses.replace(player, persona=persona)
        accepted.append(persona)
    return {"players": updated}


def introduce_roster(state: GameState) -> dict:
    players = state.get("players", {})
    names = ", ".join(p.name for p in players.values())
    line = ROSTER_INTRO_TEMPLATE.format(names=names)
    return {"messages": [SystemMessage(content=line)]}


def reveal_role(state: GameState) -> dict:
    human_id = state["human_id"]
    human = state["players"][human_id]
    role_label = _ROLE_LABELS[human.role]
    message = SystemMessage(
        content=f"You are {human.name}. Your role is {role_label}.",
        additional_kwargs={"private_to": human_id},
    )
    return {"messages": [message]}
