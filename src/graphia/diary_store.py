"""Per-game diary store abstraction with parallel local + remote impls.

Spec 002 §2.4. The diary store is the per-game, per-player private journal
surface — each AI player writes one entry per Night with hidden reasoning,
and only the owning player (plus the Moderator, conceptually) ever reads
it back. In Slice 6 the entries are placeholder text; Phase 6 will replace
them with the AI's real diary content.

Two implementations live behind one Protocol so the rest of the engine
stays mode-agnostic:

- ``InProcessDiaryStore`` — dict-backed, thread-safe. Used in local mode
  and inside the Runtime container as the Slice 4 placeholder. Lifted
  here from ``graphia.runtime.diary_store`` (which is now removed) so the
  schema and the local impl live in one place.

- ``AgentCoreMemoryDiaryStore`` — wraps :class:`bedrock_agentcore.memory.MemoryClient`
  (verified against bedrock-agentcore==1.9.0; the skill's reference names
  ``AgentCoreMemory``, which does not exist in this SDK version). One
  Memory *event* per diary entry; ``(memory_id, actor_id=player_id,
  session_id=game_id)`` is the natural mapping onto the SDK's session
  model. ``night_index`` is mirrored into the event metadata as a
  zero-padded ``StringValue`` so list-time filtering (which the SDK only
  exposes for strings via ``EventMetadataFilter``) stays exact-match.
  Reads use ``list_events`` with a server-side ``(kind == diary_entry)``
  filter and then sort client-side by ``night_index``; the per-pair
  scoping already happens via the actor/session keys.

Note on design deviation from spec 002 §2.4's hint that
``InProcessDiaryStore`` is "backed by ``PlayerState.diary_entries`` in
``GameState``": for Slice 6 we keep ``InProcessDiaryStore`` self-contained
(dict-backed, same as the Slice 4 placeholder). Coupling the diary
surface to the LangGraph reducer is out of scope here and lands later
if/when Phase 6's AI diary writes actually need to participate in the
graph's checkpoint.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx

    from graphia.config import GraphiaConfig


@dataclass(frozen=True, slots=True)
class DiaryEntry:
    """One private night-phase diary entry for a single player."""

    night_index: int
    content: str


class DiaryStore(Protocol):
    """Per-game diary surface; see module docstring."""

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        """Append a new diary entry for ``(game_id, player_id)``."""
        ...

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        """Return all entries for ``(game_id, player_id)``, sorted by night_index.

        Unknown pairs return ``[]``. The returned list is a fresh copy —
        callers cannot mutate the store via the result.
        """
        ...


class InProcessDiaryStore:
    """Dict-backed, thread-safe diary store. Local mode + Runtime placeholder.

    Keyed by ``(game_id, player_id)``. Sessions are ephemeral — the store
    has the lifetime of the host process (local: the Textual app; remote
    Slice 4-style placeholder: the Runtime microVM session).
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], list[DiaryEntry]] = {}
        self._lock = threading.Lock()

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        entry = DiaryEntry(night_index=night_index, content=content)
        with self._lock:
            self._entries.setdefault((game_id, player_id), []).append(entry)

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        with self._lock:
            stored = self._entries.get((game_id, player_id), [])
            return sorted(stored, key=lambda e: e.night_index)

    def clear(self, game_id: str | None = None) -> None:
        """Drop entries — for ``game_id`` if given, otherwise everything.

        Convenience for tests and session-end cleanup.
        """
        with self._lock:
            if game_id is None:
                self._entries.clear()
            else:
                stale = [k for k in self._entries if k[0] == game_id]
                for key in stale:
                    del self._entries[key]


_DIARY_ENTRY_KIND = "diary_entry"
_METADATA_KIND_KEY = "kind"
_METADATA_NIGHT_INDEX_KEY = "night_index"
# Zero-pad night_index so the StringValue-only metadata filter still gives
# a deterministic key shape; the actual ordering happens client-side
# against the parsed int from the message body.
_NIGHT_INDEX_WIDTH = 4


class AgentCoreMemoryDiaryStore:
    """AgentCore Memory-backed diary store for remote mode.

    ``memory_id`` identifies the provisioned Memory resource (one per
    Runtime workload, per spec 002 §2.6). The SDK client is constructed
    lazily on first call so importing this module does not require AWS
    credentials.
    """

    def __init__(self, memory_id: str, region_name: str | None = None) -> None:
        if not memory_id:
            raise ValueError("memory_id is required for AgentCoreMemoryDiaryStore")
        self._memory_id = memory_id
        self._region_name = region_name
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            from bedrock_agentcore.memory import MemoryClient

            self._client = MemoryClient(region_name=self._region_name)
        return self._client

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        body = {
            "kind": _DIARY_ENTRY_KIND,
            "game_id": game_id,
            "player_id": player_id,
            "night_index": night_index,
            "content": content,
        }
        client = self._get_client()
        client.create_event(
            memory_id=self._memory_id,
            actor_id=player_id,
            session_id=game_id,
            messages=[(json.dumps(body), "ASSISTANT")],
            metadata={
                _METADATA_KIND_KEY: {"stringValue": _DIARY_ENTRY_KIND},
                _METADATA_NIGHT_INDEX_KEY: {
                    "stringValue": f"{night_index:0{_NIGHT_INDEX_WIDTH}d}"
                },
            },
        )

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        from bedrock_agentcore.memory.models.filters import (
            EventMetadataFilter,
            LeftExpression,
            OperatorType,
            RightExpression,
        )

        client = self._get_client()
        # Server-side filter on kind keeps non-diary events (if any ever
        # share the same actor/session pair) out of the result; exact
        # (game_id, player_id) scoping already happens via the actor and
        # session identifiers.
        kind_filter: EventMetadataFilter = {
            "left": LeftExpression.build(_METADATA_KIND_KEY),
            "operator": OperatorType.EQUALS_TO.value,
            "right": RightExpression.build(_DIARY_ENTRY_KIND),
        }
        events = client.list_events(
            memory_id=self._memory_id,
            actor_id=player_id,
            session_id=game_id,
            event_metadata=[kind_filter],
            include_payload=True,
        )

        entries: list[DiaryEntry] = []
        for event in events:
            entry = _entry_from_event(event)
            if entry is not None:
                entries.append(entry)
        return sorted(entries, key=lambda e: e.night_index)


def _entry_from_event(event: dict) -> DiaryEntry | None:
    """Parse a Memory event back into a ``DiaryEntry``.

    Returns ``None`` if the event isn't a diary entry (shape we don't
    recognise) — defensive against shared-session storage future-proofing.
    """
    payload = event.get("payload") or []
    for item in payload:
        conv = item.get("conversational")
        if conv is None:
            continue
        text = (conv.get("content") or {}).get("text")
        if not text:
            continue
        try:
            body = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(body, dict) or body.get("kind") != _DIARY_ENTRY_KIND:
            continue
        night_index = body.get("night_index")
        content = body.get("content")
        if not isinstance(night_index, int) or not isinstance(content, str):
            continue
        return DiaryEntry(night_index=night_index, content=content)
    return None


# ---------------------------------------------------------------------------
# Gateway-MCP-fronted diary store.
#
# The agent invokes Gateway-published MCP tools (``diary_write`` /
# ``diary_read``) over a streamable-HTTP MCP client. Gateway is the front
# door; per ADR 005 it forwards each call to a Lambda function that
# instantiates ``AgentCoreMemoryDiaryStore`` against the provisioned Memory
# resource. From the agent's perspective the Gateway is just an MCP
# endpoint with SigV4 inbound auth — Lambda-vs-Runtime-loopback is invisible
# here.
#
# The MCP client is async; the engine's ``night_close`` call site is sync
# (the Runtime's ``graph.stream`` worker is synchronous). We bridge with
# ``asyncio.run`` per call — diary writes happen once per surviving AI per
# Night, so the per-call event-loop spin-up cost is dominated by network
# latency. A long-lived background loop is out of scope for v1.
# ---------------------------------------------------------------------------


class _SigV4HttpxAuth:
    """``httpx.Auth``-style hook that SigV4-signs each request.

    The MCP ``streamablehttp_client`` accepts a ``httpx.Auth`` instance via
    its ``auth=`` parameter. ``httpx`` invokes the auth flow as a generator
    that yields requests to send and receives back the response — for SigV4
    we only need the first yield (we sign the outbound request once; no
    retry-on-401 dance is required because Gateway's inbound IAM check is
    deterministic given a valid signature).

    Credentials come from the caller-supplied ``botocore`` credentials object,
    refreshed by ``boto3.Session().get_credentials()`` lazily on construction.
    Service name is ``bedrock-agentcore`` per the AgentCore Gateway IAM
    documentation; the signing region matches the Gateway's region.
    """

    def __init__(
        self,
        *,
        region: str,
        credentials: Any,
        service_name: str = "bedrock-agentcore",
    ) -> None:
        # Lazy-import botocore so ``import graphia.diary_store`` stays free
        # of an AWS-SDK dependency for the dict-backed local-mode flow.
        from botocore.auth import SigV4Auth

        self._signer = SigV4Auth(credentials, service_name, region)

    def auth_flow(
        self, request: "httpx.Request"
    ) -> "AsyncGenerator[httpx.Request, httpx.Response]":
        # ``httpx.Auth.auth_flow`` is a generator; yielding the (mutated)
        # request once is sufficient for non-challenge auth schemes.
        return self._sync_flow(request)

    def sync_auth_flow(
        self, request: "httpx.Request"
    ) -> "AsyncGenerator[httpx.Request, httpx.Response]":
        return self._sync_flow(request)

    def _sync_flow(self, request: "httpx.Request"):
        from botocore.awsrequest import AWSRequest

        body = request.read()
        aws_req = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers=dict(request.headers),
        )
        # SigV4 requires the host header; httpx already populates it, but the
        # signer reads from the AWSRequest copy we just built.
        self._signer.add_auth(aws_req)
        for header_name, header_value in aws_req.headers.items():
            request.headers[header_name] = header_value
        yield request


class GatewayMCPDiaryStore:
    """DiaryStore impl that calls Gateway-published MCP tools.

    Constructed by the Runtime entry-point (or local-mode caller) with the
    Gateway URL derived from ``GRAPHIA_GATEWAY_ID``. Each ``write`` /
    ``read`` opens a short-lived streamable-HTTP MCP session, calls the
    appropriate tool, and tears the session down. Gateway sees one
    SigV4-signed request per call and (per ADR 005) routes it to a Lambda
    function that delegates into ``AgentCoreMemoryDiaryStore``.

    The two MCP tool names are fixed by the Lambda handlers and match the
    Gateway targets Terraform creates (``graphia-diary-write`` /
    ``graphia-diary-read``).
    """

    WRITE_TOOL_NAME = "diary_write"
    READ_TOOL_NAME = "diary_read"

    def __init__(self, gateway_url: str, region: str) -> None:
        if not gateway_url:
            raise ValueError("gateway_url is required for GatewayMCPDiaryStore")
        if not region:
            raise ValueError("region is required for GatewayMCPDiaryStore")
        self._gateway_url = gateway_url
        self._region = region

    # -- public API ----------------------------------------------------

    def write(
        self, game_id: str, player_id: str, night_index: int, content: str
    ) -> None:
        self._run_sync(
            self._call_tool(
                self.WRITE_TOOL_NAME,
                {
                    "game_id": game_id,
                    "player_id": player_id,
                    "night_index": night_index,
                    "content": content,
                },
            )
        )

    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]:
        result = self._run_sync(
            self._call_tool(
                self.READ_TOOL_NAME,
                {"game_id": game_id, "player_id": player_id},
            )
        )
        entries_raw = (result or {}).get("entries", [])
        entries = [
            DiaryEntry(
                night_index=int(e["night_index"]),
                content=str(e["content"]),
            )
            for e in entries_raw
            if isinstance(e, dict) and "night_index" in e and "content" in e
        ]
        return sorted(entries, key=lambda e: e.night_index)

    # -- internals -----------------------------------------------------

    @staticmethod
    def _run_sync(coro):
        """Run an async coroutine from a sync caller, even inside an event loop.

        ``night_close`` calls ``write`` synchronously from inside
        ``graph.stream``, which itself runs inside the Runtime's
        ``@app.entrypoint`` async coroutine. ``asyncio.run`` raises
        ``RuntimeError: asyncio.run() cannot be called from a running event
        loop`` in that context. We detect the running loop and, when one
        exists, run the coroutine on a fresh loop in a dedicated thread —
        otherwise the simpler ``asyncio.run`` path applies (local-mode
        usage, tests).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()

    def _build_auth(self) -> _SigV4HttpxAuth:
        # Resolve credentials at call time via the standard chain so the
        # signer always sees fresh creds (relevant when the Runtime's
        # execution role rotates the assumed-role session).
        import boto3

        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError(
                "No AWS credentials resolved via boto3.Session().get_credentials(); "
                "GatewayMCPDiaryStore cannot SigV4-sign the MCP request."
            )
        return _SigV4HttpxAuth(region=self._region, credentials=credentials)

    async def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        # Local import keeps the dict-backed store free of an mcp dep when
        # ``GatewayMCPDiaryStore`` is never constructed.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        auth = self._build_auth()
        async with streamablehttp_client(
            self._gateway_url,
            auth=auth,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.call_tool(tool_name, arguments)
        return _parse_mcp_tool_result(response)


def _parse_mcp_tool_result(response: Any) -> dict:
    """Pull the structured payload out of an MCP ``CallToolResult``.

    FastMCP serialises a Python ``dict`` return value into a structured
    content block on the result; older MCP versions surface it as the
    parsed JSON body of a ``TextContent`` item. We accept both shapes so a
    runtime / library version skew doesn't silently break diary reads.

    An MCP ``isError=True`` result raises :class:`RuntimeError` — Gateway /
    tool-side failures must surface to the caller (e.g. ``night_close``)
    rather than be silently parsed as a success dict. The error message
    pulls from the first ``TextContent`` item if present, falling back to
    the response's ``repr`` so the failure isn't structurally invisible.
    """
    if getattr(response, "isError", False):
        message = ""
        content = getattr(response, "content", None) or []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                message = text
                break
        raise RuntimeError(
            f"MCP tool returned isError=True: {message or repr(response)}"
        )

    # Newer MCP: structured content block on the result.
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    # Fallback: parse the first text content block as JSON.
    content = getattr(response, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def make_diary_store(config: "GraphiaConfig") -> DiaryStore:
    """Select the diary store implementation by precedence.

    Order matters: the deployed Runtime container has both ``gateway_url``
    *and* ``memory_id`` set (Terraform plumbs both env vars). Gating on
    gateway first picks the Gateway-MCP path — the intended remote-mode
    route per ADR 005, where Gateway fronts Lambda-hosted diary tools.

    ``memory_id`` alone (no Gateway) covers ad-hoc local Memory inspection:
    a developer points at a real Memory by exporting ``GRAPHIA_MEMORY_ID``
    without provisioning a Gateway. The Lambda handler (separate sub-task)
    also uses this branch — it instantiates ``AgentCoreMemoryDiaryStore``
    directly, no Gateway loopback.

    Neither set → ``InProcessDiaryStore`` (pure local mode).
    """
    if config.gateway_url:
        return GatewayMCPDiaryStore(
            gateway_url=config.gateway_url, region=config.aws_region
        )
    if config.memory_id:
        return AgentCoreMemoryDiaryStore(
            memory_id=config.memory_id, region_name=config.aws_region
        )
    return InProcessDiaryStore()
