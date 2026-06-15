"""Offline unit tests for the four Slice-2 blunder detectors (spec 011, Slice 2).

Locks in the four self-consistency detectors added in Slice 2 Tasks 1-2 of
``src/graphia/tools/blunder_eval.py`` — **without ever reaching a real model,
the network, or a live game**. Two pure scorers are covered:

1. **The third-person self-talk speech scorer** — ``score_third_person_self_talk``
   on synthetic ``(speaker, text)`` pairs: a speaker naming *themselves* counts,
   naming *another* player does not, the match is case-insensitive and
   word-bounded (``Mira`` never fires on ``Miranda`` / ``admire``), an empty list
   is all-zeros with no ``ZeroDivisionError``, and a mixed list resolves to the
   right count over the right denominator.

2. **The three exact vote-blunder action scorers** — ``score_vote_blunders`` over
   a synthetic ``(messages, players)`` history: ``self_vote.yes`` (an AI voting
   Yes on its OWN execution, over self-execution opportunities), and the Mafioso
   "bussing" pair ``peer_vote.initiation`` (a mafioso calling a vote on a fellow
   mafioso) and ``peer_vote.yes`` (a mafioso voting Yes on a fellow mafioso). The
   {self, peer} boundary is pinned: a mafioso voting Yes on its OWN execution is a
   ``self_vote``, **never** a ``peer_vote``. The "absent, not a misleading 0"
   representation is pinned too: a metric with no opportunity returns
   ``{rate: None, count: 0, denominator: 0}`` and is OMITTED from a rendered
   record. The human initiator/voter is always excluded.

**Every vote history is built from the REAL imported templates via ``.format``**
(``VOTE_INITIATE_ANNOUNCE_TEMPLATE`` / ``VOTE_PER_BALLOT_TEMPLATE`` from
``graphia.prompts``) wrapped in the real ``SystemMessage`` the node emits, never
from a hardcoded copy — so a template reword in ``graphia.prompts`` (or a Yes/No
label reword in ``day.py``) breaks extraction loudly here, exactly as it would
in the harness, rather than letting a metric drift silently. ``PlayerState`` is
imported from ``graphia.state`` so a field rename breaks these tests honestly.

Everything is stubbed and offline: no provider client is ever constructed and
the autouse ``safe_llm`` net is left intact — these tests never go near an LLM
call site.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from graphia.llm import DayAction
from graphia.nodes.day import _role_label, _team_line, _win_condition_line
from graphia.prompts import (
    DAY_SPEAK_SYSTEM,
    DAY_SPEAK_USER_TEMPLATE,
    VOTE_INITIATE_ANNOUNCE_TEMPLATE,
    VOTE_PER_BALLOT_TEMPLATE,
)
from graphia.state import PlayerState
from graphia.tools.blunder_eval import (
    EvalResult,
    _attach_ci,
    make_day_speaker_resolver,
    render_record,
    score_self_vote_initiation,
    score_third_person_self_talk,
    score_vote_blunders,
    wilson_ci,
)
from graphia.tools.instrument import CaptureRecord, InstrumentedModel

# ===========================================================================
# Synthetic-history builders — every vote line is rendered from the REAL
# imported template via ``.format`` and wrapped in the real ``SystemMessage``
# the node emits. A reword of either template (or the Yes/No labels) breaks
# these helpers, and every test that uses them, immediately.
# ===========================================================================

# The exact Yes/No labels ``day.py`` formats into the per-ballot template
# (``vote_label = "Yes" if yes else "No"``). Mirrored here so a ballot is
# rendered with the SAME spelling the node emits and the scorer anchors on.
_YES = "Yes"
_NO = "No"


def _announce(initiator: PlayerState, target: PlayerState) -> SystemMessage:
    """A vote-initiation announce ``SystemMessage`` from the real template."""
    return SystemMessage(
        content=VOTE_INITIATE_ANNOUNCE_TEMPLATE.format(
            initiator=initiator.name,
            target=target.name,
        )
    )


def _ballot(voter: PlayerState, yes: bool) -> SystemMessage:
    """A per-ballot ``SystemMessage`` from the real template + the node's labels."""
    return SystemMessage(
        content=VOTE_PER_BALLOT_TEMPLATE.format(
            voter=voter.name,
            vote_label=_YES if yes else _NO,
        )
    )


def _player(
    pid: str,
    name: str,
    role: str = "law_abiding",
    is_human: bool = False,
) -> PlayerState:
    """A ``PlayerState`` built from the real dataclass (mirrors existing tests)."""
    return PlayerState(id=pid, name=name, role=role, is_human=is_human)


def _roster(*players: PlayerState) -> dict[str, PlayerState]:
    """The ``players`` map keyed by id, as the harness surfaces from a final state."""
    return {p.id: p for p in players}


# ===========================================================================
# 1. third_person_self_talk — the AI-spoken-line speech rate
# ===========================================================================


def test_third_person_self_talk_counts_speaker_naming_self() -> None:
    """A speaker whose own name appears as a whole word in their line is counted."""
    result = score_third_person_self_talk(
        [("Mira", "Mira thinks the quiet one is mafia.")]
    )

    assert result["count"] == 1
    assert result["denominator"] == 1
    assert result["rate"] == 1.0


def test_third_person_self_talk_ignores_speaker_naming_another_player() -> None:
    """Naming a DIFFERENT player (not oneself) is not a self-talk blunder."""
    result = score_third_person_self_talk(
        [("Mira", "I think Bo is bluffing about last night.")]
    )

    assert result["count"] == 0
    assert result["denominator"] == 1
    assert result["rate"] == 0.0


def test_third_person_self_talk_is_case_insensitive() -> None:
    """The own-name match is case-insensitive (``MIRA`` / ``mira`` both fire)."""
    result = score_third_person_self_talk(
        [
            ("Mira", "MIRA already told you who to trust."),
            ("Bo", "honestly, bo would never lie to this table."),
        ]
    )

    assert result["count"] == 2
    assert result["denominator"] == 2
    assert result["rate"] == 1.0


def test_third_person_self_talk_does_not_fire_on_substring_match() -> None:
    """Word boundaries: ``Mira`` must not match inside ``Miranda`` or ``admire``.

    The scorer wraps the escaped own-name in ``\\b...\\b`` so a name that is a
    substring of another word (or of a longer name another player happens to
    use) never spuriously fires — only a whole-word self-mention counts.
    """
    result = score_third_person_self_talk(
        [
            ("Mira", "I really admire how Miranda played that round."),
        ]
    )

    assert result["count"] == 0
    assert result["denominator"] == 1
    assert result["rate"] == 0.0


def test_third_person_self_talk_empty_list_is_all_zeros_no_zero_division() -> None:
    """The empty list returns all-zeros and never raises ZeroDivisionError."""
    result = score_third_person_self_talk([])

    assert result == {"rate": 0.0, "count": 0, "denominator": 0}


def test_third_person_self_talk_mixed_list_counts_only_self_mentions() -> None:
    """A mixed list resolves to the right count over the full denominator.

    Two of four lines self-name (one of those only as a substring, so it must
    NOT count — leaving one true self-mention), one names another player, one is
    a clean first-person line. Count 1 over denominator 4.
    """
    result = score_third_person_self_talk(
        [
            ("Mira", "Mira says we hang the doctor."),          # self → counts
            ("Mira", "Miranda is clearly the mafia here."),     # substring → no
            ("Bo", "I trust Mira more than the rest of you."),  # names another → no
            ("Bo", "I have nothing to hide, vote me last."),    # clean → no
        ]
    )

    assert result["count"] == 1
    assert result["denominator"] == 4
    assert result["rate"] == 0.25


# ===========================================================================
# 2. score_vote_blunders — the three exact game-record action detectors
# ===========================================================================


def test_self_vote_yes_counts_yes_on_own_execution() -> None:
    """A player voting Yes on their OWN execution is the ``self_vote.yes`` numerator.

    The active vote targets the voter, and the voter votes Yes — a self-execution
    opportunity (denominator) that the voter took (numerator). The unrelated peer
    metrics see no opportunity here, so they are absent (rate None).
    """
    aria = _player("p-1", "Aria")
    bo = _player("p-2", "Bo")
    messages = [
        _announce(bo, aria),  # the vote is on Aria
        _ballot(aria, yes=True),  # Aria votes Yes on her own execution
    ]

    result = score_vote_blunders(messages, _roster(aria, bo))

    assert result["self_vote.yes"] == {"rate": 1.0, "count": 1, "denominator": 1}
    # No mafia present → the peer family had no opportunity → absent, not 0.
    assert result["peer_vote.initiation"] == {
        "rate": None,
        "count": 0,
        "denominator": 0,
    }
    assert result["peer_vote.yes"] == {"rate": None, "count": 0, "denominator": 0}


def test_self_vote_no_on_self_is_denominator_only() -> None:
    """Voting No on one's own execution is a self-execution opportunity not taken.

    The voter is the active target (so it lands in the denominator) but votes No
    (so it is NOT in the numerator) — rate 0.0 over denominator 1.
    """
    aria = _player("p-1", "Aria")
    bo = _player("p-2", "Bo")
    messages = [
        _announce(bo, aria),
        _ballot(aria, yes=False),  # Aria votes No on her own execution
    ]

    result = score_vote_blunders(messages, _roster(aria, bo))

    assert result["self_vote.yes"] == {"rate": 0.0, "count": 0, "denominator": 1}


def test_peer_vote_initiation_counts_mafioso_calling_vote_on_fellow_mafioso() -> None:
    """A mafioso initiating a vote against a fellow mafioso is a bussing initiation.

    Numerator: a mafia-AI initiation whose target is a different mafioso.
    Denominator: all mafia-AI initiations. One such initiation here → 1/1.
    """
    don = _player("p-1", "Don", role="mafia")
    vito = _player("p-2", "Vito", role="mafia")
    citizen = _player("p-3", "Cara", role="law_abiding")
    messages = [
        _announce(don, vito),  # mafioso calls a vote on his own teammate
    ]

    result = score_vote_blunders(messages, _roster(don, vito, citizen))

    assert result["peer_vote.initiation"] == {
        "rate": 1.0,
        "count": 1,
        "denominator": 1,
    }


def test_peer_vote_initiation_against_law_abiding_is_denominator_only() -> None:
    """A mafioso initiating against a LAW-ABIDING target is the correct play.

    It is still a mafia-AI initiation (denominator) but the target is not a
    teammate, so it is not a bussing initiation (not numerator) — rate 0.0/1.
    """
    don = _player("p-1", "Don", role="mafia")
    vito = _player("p-2", "Vito", role="mafia")
    cara = _player("p-3", "Cara", role="law_abiding")
    messages = [
        _announce(don, cara),  # mafioso calls a vote on a townsperson — correct
    ]

    result = score_vote_blunders(messages, _roster(don, vito, cara))

    assert result["peer_vote.initiation"] == {
        "rate": 0.0,
        "count": 0,
        "denominator": 1,
    }


def test_peer_vote_yes_counts_mafioso_voting_yes_on_fellow_mafioso() -> None:
    """A mafioso voting Yes on a fellow mafioso's execution is bussing.

    The active vote targets a mafioso; a *different* mafioso votes Yes on it —
    a bussing opportunity (denominator: mafia ballots on a mafia target) that was
    taken (numerator). 1/1.
    """
    don = _player("p-1", "Don", role="mafia")
    vito = _player("p-2", "Vito", role="mafia")
    cara = _player("p-3", "Cara", role="law_abiding")
    messages = [
        _announce(cara, vito),  # the vote is on Vito (a mafioso)
        _ballot(don, yes=True),  # his teammate Don votes Yes to execute him
    ]

    result = score_vote_blunders(messages, _roster(don, vito, cara))

    assert result["peer_vote.yes"] == {"rate": 1.0, "count": 1, "denominator": 1}


def test_peer_vote_yes_mafioso_voting_no_on_fellow_is_denominator_only() -> None:
    """A mafioso voting No on a fellow mafioso is the loyal play — denominator only.

    Still a mafia ballot on a mafia target (opportunity → denominator) but a No
    ballot is not bussing (not numerator) — 0.0/1.
    """
    don = _player("p-1", "Don", role="mafia")
    vito = _player("p-2", "Vito", role="mafia")
    cara = _player("p-3", "Cara", role="law_abiding")
    messages = [
        _announce(cara, vito),
        _ballot(don, yes=False),  # Don loyally votes to spare his teammate
    ]

    result = score_vote_blunders(messages, _roster(don, vito, cara))

    assert result["peer_vote.yes"] == {"rate": 0.0, "count": 0, "denominator": 1}


def test_mafioso_yes_on_own_execution_is_self_vote_not_peer_vote() -> None:
    """THE BOUNDARY: a mafioso voting Yes on its OWN execution is self_vote, not peer_vote.

    The active vote targets Vito (a mafioso) and Vito himself votes Yes. Because
    ``active_target.id == voter.id``, this is a self-execution (counts under
    ``self_vote.yes``), and the peer-vote opportunity explicitly EXCLUDES the
    self-target (``active_target.id != voter.id``), so ``peer_vote.yes`` sees no
    opportunity at all here → absent, not a 0/1. Pinning this proves the self
    ballot never leaks into the peer denominator.
    """
    vito = _player("p-1", "Vito", role="mafia")
    cara = _player("p-2", "Cara", role="law_abiding")
    messages = [
        _announce(cara, vito),  # the vote is on Vito
        _ballot(vito, yes=True),  # Vito votes Yes on his OWN execution
    ]

    result = score_vote_blunders(messages, _roster(vito, cara))

    # Counted as a self-vote.
    assert result["self_vote.yes"] == {"rate": 1.0, "count": 1, "denominator": 1}
    # NOT counted as a peer-vote — and the peer denominator is 0 (absent), so the
    # self ballot did not even create a bussing opportunity.
    assert result["peer_vote.yes"] == {"rate": None, "count": 0, "denominator": 0}


def test_peer_vote_yes_absent_when_no_mafia_ballot_on_a_mafia_target() -> None:
    """ABSENT-NOT-ZERO: no mafia ballot on a mafia target → ``peer_vote.yes`` absent.

    The only vote is on a law-abiding target, so no bussing opportunity ever
    arises. ``peer_vote.yes`` is reported absent — ``{rate: None, count: 0,
    denominator: 0}`` — NOT a misleading ``rate: 0.0`` that would read as "the AI
    never bussed" when in fact it was never tested.
    """
    don = _player("p-1", "Don", role="mafia")
    cara = _player("p-2", "Cara", role="law_abiding")
    eve = _player("p-3", "Eve", role="law_abiding")
    messages = [
        _announce(don, cara),  # vote on a townsperson
        _ballot(don, yes=True),  # mafioso votes Yes — but on a law-abiding target
    ]

    result = score_vote_blunders(messages, _roster(don, cara, eve))

    assert result["peer_vote.yes"] == {"rate": None, "count": 0, "denominator": 0}


def test_absent_metric_is_omitted_from_a_rendered_record() -> None:
    """A denominator-0 metric is OMITTED from the ledger record (the run_eval seam).

    ``run_eval`` only puts a metric into ``result.metrics`` when its batch
    denominator > 0; the renderer iterates exactly the entries present, so an
    absent metric simply does not appear in the run's record. We reproduce that
    seam: a result carrying only the metrics that HAD an opportunity renders a
    record that mentions ``self_vote.yes`` but never ``peer_vote.yes`` — proving
    "absent" means "not in the record", not "rate: 0.0 in the record".
    """
    # Mirror run_eval: only present-opportunity metrics make it into result.metrics.
    result = EvalResult(
        provider="ollama",
        metrics={
            "self_vote.yes": {"rate": 0.5, "count": 1, "denominator": 2},
            # peer_vote.yes had a 0 denominator this batch → OMITTED on purpose.
        },
    )

    doc = render_record(result, "2026-06-13")

    assert "self_vote.yes:" in doc
    assert "peer_vote.yes" not in doc


def test_human_initiator_and_voter_are_never_counted() -> None:
    """The human voter/initiator is excluded from every action metric.

    The human (a mafioso here, to make exclusion the only reason they drop out)
    both initiates a vote on a fellow mafioso AND votes Yes on a fellow mafioso —
    blunders that would be counted for an AI. Because they are human, NONE of the
    three metrics counts them, and with no AI activity at all every metric is
    absent.
    """
    human = _player("p-1", "You", role="mafia", is_human=True)
    vito = _player("p-2", "Vito", role="mafia")
    cara = _player("p-3", "Cara", role="law_abiding")
    messages = [
        _announce(human, vito),  # human mafioso calls a vote on a teammate
        _ballot(human, yes=True),  # human mafioso votes Yes on the teammate
    ]

    result = score_vote_blunders(messages, _roster(human, vito, cara))

    # The human's initiation is not in the mafia-AI initiation denominator.
    assert result["peer_vote.initiation"] == {
        "rate": None,
        "count": 0,
        "denominator": 0,
    }
    # The human's ballot is not in any AI ballot denominator.
    assert result["peer_vote.yes"] == {"rate": None, "count": 0, "denominator": 0}
    assert result["self_vote.yes"] == {"rate": None, "count": 0, "denominator": 0}


def test_ballots_attach_to_the_most_recent_announce_target() -> None:
    """Each ballot is classified against the vote announced most recently before it.

    Two sequential votes: first on a law-abiding target (a mafia Yes there is the
    correct play — no peer opportunity), then on a fellow mafioso (a mafia Yes
    there IS bussing). The scorer must re-target on the second announce, so only
    the second mafia ballot counts as ``peer_vote.yes`` — 1 over a denominator of
    1 (only the second vote was a mafia-on-mafia opportunity).
    """
    don = _player("p-1", "Don", role="mafia")
    vito = _player("p-2", "Vito", role="mafia")
    cara = _player("p-3", "Cara", role="law_abiding")
    messages = [
        _announce(cara, cara),  # vote 1: on the townsperson Cara
        _ballot(don, yes=True),  # mafia Yes on a townsperson — correct, not peer
        _announce(cara, vito),  # vote 2: on the mafioso Vito
        _ballot(don, yes=True),  # mafia Yes on a teammate — bussing
    ]

    result = score_vote_blunders(messages, _roster(don, vito, cara))

    assert result["peer_vote.yes"] == {"rate": 1.0, "count": 1, "denominator": 1}


def test_non_system_and_unmatched_messages_are_ignored() -> None:
    """Only the template-shaped ``SystemMessage`` lines are parsed; the rest pass through.

    An ``AIMessage`` Day speech and a ``HumanMessage`` that happen to mention
    names, plus a ``SystemMessage`` that matches neither anchor, must not be
    mistaken for a vote line — the real announce/ballot pair around them still
    scores exactly 1/1, proving the noise was ignored rather than mis-parsed.
    """
    aria = _player("p-1", "Aria")
    bo = _player("p-2", "Bo")
    messages = [
        AIMessage(content="Aria: I vote yes, honestly.", name="Bo"),  # not a ballot
        HumanMessage(content="Bo has called for a vote? no idea"),  # not an announce
        SystemMessage(content="Night falls over the town."),  # matches no anchor
        _announce(bo, aria),  # the real announce
        _ballot(aria, yes=True),  # the real self-vote ballot
    ]

    result = score_vote_blunders(messages, _roster(aria, bo))

    assert result["self_vote.yes"] == {"rate": 1.0, "count": 1, "denominator": 1}


# ===========================================================================
# 3. self_vote.initiation — the PROXY-ONLY metric (Slice 3): a self-targeted AI
#    vote, counted from the raw structured-output payload because ``_accept``
#    rejects it before it reaches game state. Speaker attribution is via the
#    prompt-parse resolver — read off THIS invoke's prompt, never from a live
#    ``get_state`` snapshot (the stale-snapshot trap from test_slice7_vote).
#
#    NOTE: a focused attribution proof lives here now to pin correctness of the
#    capture+resolver+scorer wiring; Task 3 (the testing agent) expands it.
# ===========================================================================


def _day_prompt(speaker: PlayerState) -> list:
    """The exact messages ``day._ai_day_action`` builds for ``speaker``'s turn.

    Rendered from the REAL imported ``DAY_SPEAK_USER_TEMPLATE`` (opens
    "You are {speaker}."), so the resolver parses the same line the production
    node produces — a reword of that template breaks this attribution test
    loudly, exactly as it would the harness.
    """
    return [
        SystemMessage(content=DAY_SPEAK_SYSTEM),
        HumanMessage(
            content=DAY_SPEAK_USER_TEMPLATE.format(
                speaker=speaker.name,
                role_label=_role_label(speaker.role),
                win_condition=_win_condition_line(speaker.role),
                team_line=_team_line(speaker, {speaker.id: speaker}),
                roster="(roster)",
                context="(ctx)",
            )
        ),
    ]


class _ScriptedStructured:
    """A ``with_structured_output(...)`` stand-in returning a fixed payload."""

    def __init__(self, action: object) -> None:
        self._action = action

    def invoke(self, *args: object, **kwargs: object) -> object:
        return self._action


class _ScriptedInner:
    """A fake tier client: each ``with_structured_output`` yields the next
    scripted action, so a sequence of Day turns can be driven with NO model."""

    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self._i = 0

    def with_structured_output(
        self, schema: object, **kwargs: object
    ) -> _ScriptedStructured:
        action = self._actions[self._i]
        self._i += 1
        return _ScriptedStructured(action)


def test_self_vote_initiation_attributes_to_the_prompt_speaker() -> None:
    """A captured vote attributes to the SPEAKER named in its own invoke prompt.

    The load-bearing correctness check for the proxy-only metric: a captured
    ``DayAction(kind="vote", target_id=X)`` produced during speaker S's turn must
    attribute to S — so ``target == S`` is a self-vote (numerator) and
    ``target != S`` is denominator-only. Ids deliberately differ from names so a
    name-vs-id confusion would fail. The speaker is read off the prompt the call
    was handed (``make_day_speaker_resolver``), never from live graph state, so
    attribution can never go stale — the deliberate avoidance of the mid-stream
    ``get_state`` trap.
    """
    mira = _player("p-1", "Mira")
    bo = _player("p-2", "Bo", role="mafia")
    you = _player("p-3", "You", is_human=True)
    players = _roster(mira, bo, you)

    inner = _ScriptedInner(
        [
            DayAction(kind="vote", target_id="p-1"),  # Mira votes herself → self
            DayAction(kind="vote", target_id="p-1"),  # Bo votes Mira → denom only
            DayAction(kind="speak", text="hmm."),  # Mira speaks → not an attempt
        ]
    )
    captures: list[CaptureRecord] = []
    proxy = InstrumentedModel(
        inner,
        captures=captures,
        speaker_resolver=make_day_speaker_resolver(players),
    )

    # Drive three turns exactly as ``day._ai_day_action`` does: fetch the
    # structured runnable, then invoke it with that speaker's prompt.
    proxy.with_structured_output(DayAction).invoke(_day_prompt(mira))
    proxy.with_structured_output(DayAction).invoke(_day_prompt(bo))
    proxy.with_structured_output(DayAction).invoke(_day_prompt(mira))

    # Attribution is exact: each capture carries the SPEAKER's id (not name).
    assert [c.speaker_id for c in captures] == ["p-1", "p-2", "p-1"]

    facets = score_self_vote_initiation(captures)
    # Denominator = the two vote attempts (the speak is excluded); numerator =
    # the one whose target == its own speaker (Mira self-voting). Bo→Mira is a
    # real attempt (denominator) but not self-targeted (not numerator).
    assert facets == {"rate": 0.5, "count": 1, "denominator": 2}


def test_self_vote_initiation_resolver_ignores_non_day_speak_prompts() -> None:
    """The resolver returns ``None`` for a prompt with no "You are {speaker}" line.

    A ``Ballot`` / ``Pointing`` / ``Roster`` invoke carries no Day-speak line, so
    the resolver yields ``None`` (the capture is unattributed and the scorer
    skips it) — capture attribution fires only on genuine Day-speaker turns. An
    unknown speaker name likewise resolves to ``None``, never a false match.
    """
    mira = _player("p-1", "Mira")
    resolve = make_day_speaker_resolver(_roster(mira))

    # A non-day-speak prompt (no "You are {speaker}.") → unattributed.
    assert resolve([HumanMessage(content="Vote yes or no on executing Bo.")]) is None
    # A name not on this game's roster → None, never a wrong-player attribution.
    ghost = HumanMessage(
        content=DAY_SPEAK_USER_TEMPLATE.format(
            speaker="Ghost",
            role_label="Law-abiding Citizen",
            win_condition="",
            team_line="",
            roster="r",
            context="c",
        )
    )
    assert resolve([ghost]) is None
    # Defensive: a non-list invoke argument → None.
    assert resolve("not a message list") is None


def test_self_vote_initiation_absent_when_no_ai_vote_attempt() -> None:
    """No AI vote attempt → ``self_vote.initiation`` absent (rate None, 0/0).

    Matches the Slice-2 "absent, not a misleading 0" convention (``_facets``): a
    batch where no AI ever attempted a vote offered no opportunity for the
    blunder, so the metric is reported absent and ``run_eval`` omits it from the
    record — never a misleading ``rate: 0.0`` that reads as "the AI never
    self-voted" when it was simply never tested. Speaks and unattributed
    captures are not vote attempts and do not enter the denominator.
    """
    captures = [
        CaptureRecord(
            schema=DayAction,
            raw_result=DayAction(kind="speak", text="I'm watching."),
            speaker_id="p-1",
        ),
        # An unattributed capture (e.g. a Ballot invoke, no speaker) — skipped.
        CaptureRecord(schema=object, raw_result=object(), speaker_id=None),
    ]

    facets = score_self_vote_initiation(captures)

    assert facets == {"rate": None, "count": 0, "denominator": 0}


# ===========================================================================
# 4. wilson_ci — the per-metric Wilson 95% reliability band (spec 011).
#
# Closed-form (no resampling): a tight band on a large-n rate, a WIDE band on a
# tiny-n rate, clamped to [0, 1]. The whole point is that a reader can tell a
# solid repetition 0.45 @ n=108 from a noisy self_vote.yes 0.50 @ n=2 by width.
# ===========================================================================


def test_wilson_ci_50_of_100_is_centered_and_tight() -> None:
    """A balanced large-n proportion (50/100) gives the textbook ≈(0.404, 0.596)."""
    low, high = wilson_ci(50, 100)

    assert low == pytest.approx(0.404, abs=1e-3)
    assert high == pytest.approx(0.596, abs=1e-3)


def test_wilson_ci_zero_count_pins_low_to_zero() -> None:
    """``count == 0`` pins ci_low to exactly 0.0 (and ci_high stays below 1)."""
    low, high = wilson_ci(0, 10)

    assert low == 0.0
    assert high < 1.0
    assert high > 0.0  # 0/10 is not certainty of 0 — the upper bound is positive


def test_wilson_ci_full_count_pins_high_to_one() -> None:
    """``count == denominator`` pins ci_high to exactly 1.0 (ci_low stays above 0)."""
    low, high = wilson_ci(10, 10)

    assert high == 1.0
    assert low > 0.0
    assert low < 1.0  # 10/10 is not certainty of 1 — the lower bound is below 1


def test_wilson_ci_tiny_denominator_is_wide() -> None:
    """The whole point: 1/2 yields a WIDE band (high − low > 0.6) — noise, not signal."""
    low, high = wilson_ci(1, 2)

    assert high - low > 0.6
    assert 0.0 <= low < high <= 1.0


def test_wilson_ci_large_denominator_is_tight() -> None:
    """A large-n rate (49/108) gives a narrow band — a reliable, comparable rate."""
    low, high = wilson_ci(49, 108)

    # Centered near the point estimate, and clearly tighter than the 1/2 case.
    assert low == pytest.approx(0.362, abs=1e-2)
    assert high == pytest.approx(0.546, abs=1e-2)
    assert high - low < 0.2


def test_wilson_ci_is_always_clamped_to_unit_interval() -> None:
    """Every (count, denominator) yields a band within [0, 1] with low ≤ high."""
    for count, denominator in [(0, 1), (1, 1), (0, 3), (3, 3), (1, 2), (49, 108)]:
        low, high = wilson_ci(count, denominator)
        assert 0.0 <= low <= high <= 1.0


def test_wilson_ci_zero_denominator_is_total_ignorance() -> None:
    """A 0 denominator (no opportunity) returns the full (0.0, 1.0) — defensive."""
    assert wilson_ci(0, 0) == (0.0, 1.0)


# ===========================================================================
# 5. CI attachment + rendering: present metrics carry ci_low/ci_high in order;
#    absent (omitted) metrics stay CI-free.
# ===========================================================================


def test_attach_ci_adds_band_to_present_metric_only() -> None:
    """``_attach_ci`` annotates a present metric in place and skips a 0/0 metric.

    A present metric (denominator > 0) gains ci_low/ci_high equal to
    ``wilson_ci`` of its own count/denominator; a degenerate 0/0 entry (which
    ``run_eval`` would already have omitted) is left untouched — no CI invented.
    """
    metrics: dict[str, dict[str, float | int | None]] = {
        "repetition": {"rate": 1 / 2, "count": 1, "denominator": 2},
        "degenerate": {"rate": None, "count": 0, "denominator": 0},
    }

    _attach_ci(metrics)

    low, high = wilson_ci(1, 2)
    assert metrics["repetition"]["ci_low"] == low
    assert metrics["repetition"]["ci_high"] == high
    assert "ci_low" not in metrics["degenerate"]
    assert "ci_high" not in metrics["degenerate"]


def test_render_present_metric_carries_ci_absent_metric_omitted() -> None:
    """A rendered record shows a present metric's CI in order; an absent one has none.

    Mirrors the run_eval seam: only present-opportunity metrics make it into
    ``result.metrics`` (and through ``_attach_ci``). The rendered record names
    ``repetition`` with ci_low/ci_high right after denominator, and never
    mentions the omitted ``peer_vote.yes`` — so an absent metric carries no CI.
    """
    result = EvalResult(
        provider="ollama",
        metrics={
            "repetition": {"rate": 49 / 108, "count": 49, "denominator": 108},
            # peer_vote.yes had a 0 denominator this batch → OMITTED (no CI).
        },
    )
    _attach_ci(result.metrics)

    doc = render_record(result, "2026-06-13")
    lines = doc.splitlines()

    denom_i = lines.index("    denominator: 108")
    low_i = next(i for i, ln in enumerate(lines) if ln.startswith("    ci_low: "))
    high_i = next(i for i, ln in enumerate(lines) if ln.startswith("    ci_high: "))
    assert denom_i < low_i < high_i

    low, high = wilson_ci(49, 108)
    assert f"    ci_low: {low!r}" in doc
    assert f"    ci_high: {high!r}" in doc

    assert "peer_vote.yes" not in doc
