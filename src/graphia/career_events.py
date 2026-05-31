"""Per-action career-stats events: wire format and pure aggregator.

Spec 006 / ADR 008. Remote mode emits one of these events from the Runtime at
each statistical moment (vote initiated, ballot cast, vote/night resolved,
game ended/abandoned) and a consumer Lambda folds them into the same
:class:`graphia.stats_store.GameSummary` that local mode produces from
``_latest_state`` via :func:`graphia.stats_store.summarize`. Both sides import
this module — it stays free of node / UI / framework imports so the Lambda
package is small and the local tests reach it without setting up AWS.

The shape mirrors the rest of the project: a single frozen, slotted
dataclass with *every* kind-specific field present as ``X | None``, plus the
two always-set discriminators ``kind`` and ``session_id``. Bedrock Converse
rejects discriminated unions, and keeping the wire model flat means the
consumer Lambda — which may never see the originating Pydantic types — can
parse one shape and dispatch on ``kind``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Literal, Protocol

from graphia.stats_store import GameSummary

if TYPE_CHECKING:
    from graphia.config import GraphiaConfig

logger = logging.getLogger(__name__)

__all__ = [
    "EventKind",
    "KIND_GAME_STARTED",
    "KIND_VOTE_INITIATED",
    "KIND_BALLOT_CAST",
    "KIND_VOTE_RESOLVED",
    "KIND_NIGHT_RESOLVED",
    "KIND_GAME_ENDED",
    "KIND_GAME_ABANDONED",
    "CareerEvent",
    "to_json",
    "from_json",
    "build_summary",
    "CareerEventEmitter",
    "NoOpCareerEventEmitter",
    "AgentCoreCareerEventEmitter",
    "make_career_emitter",
]

KIND_GAME_STARTED = "game_started"
KIND_VOTE_INITIATED = "vote_initiated"
KIND_BALLOT_CAST = "ballot_cast"
KIND_VOTE_RESOLVED = "vote_resolved"
KIND_NIGHT_RESOLVED = "night_resolved"
KIND_GAME_ENDED = "game_ended"
KIND_GAME_ABANDONED = "game_abandoned"

type EventKind = Literal[
    "game_started",
    "vote_initiated",
    "ballot_cast",
    "vote_resolved",
    "night_resolved",
    "game_ended",
    "game_abandoned",
]

_FINALIZER_KINDS: frozenset[str] = frozenset({KIND_GAME_ENDED, KIND_GAME_ABANDONED})


@dataclass(frozen=True, slots=True)
class CareerEvent:
    """One per-action career-stats event.

    Flat-with-all-optional-fields so a single dataclass carries every kind
    (same convention as the rest of the project). ``kind`` and ``session_id``
    are always set; every other field is populated only for the kinds that
    use it and is ``None`` otherwise. The compact wire format produced by
    :func:`to_json` drops the ``None`` fields entirely, and :func:`from_json`
    restores them.
    """

    kind: str
    session_id: str
    human_role: str | None = None
    initiator_is_human: bool | None = None
    voter_is_human: bool | None = None
    was_executed: bool | None = None
    victim_died: bool | None = None
    human_was_mafia_picker: bool | None = None
    human_picked_victim: bool | None = None
    outcome: str | None = None
    rounds: int | None = None
    rounds_so_far: int | None = None


_FIELD_NAMES: tuple[str, ...] = (
    "kind",
    "session_id",
    "human_role",
    "initiator_is_human",
    "voter_is_human",
    "was_executed",
    "victim_died",
    "human_was_mafia_picker",
    "human_picked_victim",
    "outcome",
    "rounds",
    "rounds_so_far",
)


def to_json(event: CareerEvent) -> dict[str, Any]:
    """Serialise ``event`` to a compact dict, omitting ``None`` fields.

    Inverse of :func:`from_json`: every set field round-trips by the same key.
    The two discriminators are always present; the rest appear only when the
    event's kind populated them.
    """
    return {
        name: value
        for name in _FIELD_NAMES
        if (value := getattr(event, name)) is not None
    }


def from_json(data: dict[str, Any]) -> CareerEvent:
    """Build a :class:`CareerEvent` from the compact dict :func:`to_json` emits.

    Missing fields default to ``None``; unknown extra keys are ignored so the
    parser stays forward-tolerant. ``kind`` and ``session_id`` are required —
    a malformed payload that lacks them raises :class:`KeyError` at construction.
    """
    return CareerEvent(
        kind=data["kind"],
        session_id=data["session_id"],
        human_role=data.get("human_role"),
        initiator_is_human=data.get("initiator_is_human"),
        voter_is_human=data.get("voter_is_human"),
        was_executed=data.get("was_executed"),
        victim_died=data.get("victim_died"),
        human_was_mafia_picker=data.get("human_was_mafia_picker"),
        human_picked_victim=data.get("human_picked_victim"),
        outcome=data.get("outcome"),
        rounds=data.get("rounds"),
        rounds_so_far=data.get("rounds_so_far"),
    )


def build_summary(events: Iterable[CareerEvent]) -> GameSummary:
    """Aggregate one game's events back into a :class:`GameSummary`.

    Callers feed the events for a single ``session_id``; this function never
    filters. Walks the iterable once, accumulating per-action counters, and
    on a finaliser (:data:`KIND_GAME_ENDED` or :data:`KIND_GAME_ABANDONED`)
    closes out the role / outcome / rounds / ``human_won`` fields the same way
    :func:`graphia.stats_store.summarize` does from ``_latest_state``.

    ``human_won`` mirrors ``summarize``'s rule: it is true only when the
    outcome string ``"law_abiding_win"`` or ``"mafia_win"`` matches the role,
    and false for ``"draw"`` / ``"abandoned"``. If the iterable contains no
    finaliser, the returned summary has ``outcome=""`` and ``human_won=False``
    — the Lambda treats that partial as "game still in progress, don't fold".
    """
    human_role = ""
    outcome = ""
    rounds = 0
    votes_called = 0
    ballots_cast = 0
    night_attempts = 0
    night_successes = 0
    night_victims = 0
    day_executions = 0

    for event in events:
        match event.kind:
            case "game_started":
                if event.human_role is not None:
                    human_role = event.human_role
            case "vote_initiated":
                if event.initiator_is_human:
                    votes_called += 1
            case "ballot_cast":
                if event.voter_is_human:
                    ballots_cast += 1
            case "vote_resolved":
                if event.was_executed:
                    day_executions += 1
            case "night_resolved":
                if event.human_was_mafia_picker:
                    night_attempts += 1
                if event.human_picked_victim:
                    night_successes += 1
                if event.victim_died:
                    night_victims += 1
            case "game_ended":
                if event.human_role is not None:
                    human_role = event.human_role
                if event.outcome is not None:
                    outcome = event.outcome
                if event.rounds is not None:
                    rounds = event.rounds
            case "game_abandoned":
                if event.human_role is not None:
                    human_role = event.human_role
                outcome = "abandoned"
                if event.rounds_so_far is not None:
                    rounds = event.rounds_so_far

    human_won = outcome in {"law_abiding_win", "mafia_win"} and outcome.startswith(
        human_role + "_"
    )

    return GameSummary(
        human_role=human_role,
        outcome=outcome,
        human_won=human_won,
        rounds=rounds,
        votes_called=votes_called,
        ballots_cast=ballots_cast,
        night_attempts=night_attempts,
        night_successes=night_successes,
        night_victims=night_victims,
        day_executions=day_executions,
    )


_CAREER_EVENT_KIND_KEY = "kind"
_CAREER_EVENT_KIND_TAG = "career_event"


class CareerEventEmitter(Protocol):
    """Fire-and-forget per-action career-stats event sink.

    Local mode binds :class:`NoOpCareerEventEmitter`; remote mode binds
    :class:`AgentCoreCareerEventEmitter`. Either way, ``emit`` never raises
    back into a game-mechanics node — emission failures are swallowed so a
    transient Memory hiccup never crashes gameplay (spec 006 §2.x).
    """

    def emit(self, session_id: str, event: CareerEvent) -> None:
        """Publish ``event`` for the game identified by ``session_id``."""
        ...


class NoOpCareerEventEmitter:
    """Silent no-op emitter — local mode and tests.

    No AWS reached. ``emit`` discards every event.
    """

    def emit(self, session_id: str, event: CareerEvent) -> None:
        return None


class AgentCoreCareerEventEmitter:
    """AgentCore Memory short-term event emitter for remote-mode careers.

    Each call lands as one ``CreateEvent`` against the configured Memory
    resource, scoped to ``(actor_id, session_id)`` so a downstream consumer
    Lambda can fold the events for a single game by listing that pair. The
    serialised :class:`CareerEvent` rides in the event ``payload`` as a JSON
    body inside a ``conversational`` content block (same wire shape the
    diary store uses), and ``kind`` is mirrored into the event ``metadata``
    so list-time filtering can pick career events out of any shared session.

    The boto3 ``bedrock-agentcore`` data-plane client is built lazily on
    first emit so importing this module — and the whole local-mode / test
    path — needs no AWS credentials or boto3 import. ``actor_id`` defaults
    to ``"human-career"`` so events are stable across games for one player.
    """

    def __init__(
        self,
        memory_id: str,
        actor_id: str = "human-career",
        region: str | None = None,
    ) -> None:
        if not memory_id:
            raise ValueError("memory_id is required for AgentCoreCareerEventEmitter")
        self._memory_id = memory_id
        self._actor_id = actor_id
        self._region = region
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "bedrock-agentcore", region_name=self._region
            )
        return self._client

    def emit(self, session_id: str, event: CareerEvent) -> None:
        client = self._get_client()
        body = json.dumps(to_json(event))
        client.create_event(
            memoryId=self._memory_id,
            actorId=self._actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "content": {"text": body},
                        "role": "ASSISTANT",
                    }
                }
            ],
        )


def make_career_emitter(config: "GraphiaConfig") -> CareerEventEmitter:
    """Select the career event emitter implementation for ``config``.

    ``career_memory_id`` set (remote mode) selects the AgentCore Memory
    short-term emitter; otherwise a :class:`NoOpCareerEventEmitter`.
    """
    if config.career_memory_id:
        return AgentCoreCareerEventEmitter(
            memory_id=config.career_memory_id,
            region=config.aws_region,
        )
    return NoOpCareerEventEmitter()
