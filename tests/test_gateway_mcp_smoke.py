"""Slice 7 sub-task 4: smoke tests for the Runtime MCP tool surface.

Three concerns, ordered by fragility:

1. ``_parse_mcp_tool_result`` parses both the modern (``structuredContent``)
   and legacy (``TextContent`` with JSON body) MCP ``CallToolResult``
   shapes. This is the bit most likely to silently break under
   ``mcp`` package upgrades, so it gets a focused unit test.

2. The FastMCP server mounted at ``/mcp`` by ``graphia.runtime.__main__``
   exposes exactly two tools (``diary_write`` / ``diary_read``) whose
   handlers delegate into the module-level ``_impl_diary_store``. We
   invoke them through FastMCP's own tool-manager (bypassing the HTTP
   transport entirely) so the delegation logic is exercised in-process
   with no network involved.

3. :class:`GatewayMCPDiaryStore` calls the correct MCP tool names, with
   the correct arguments, wires SigV4 auth into the transport, and
   round-trips a real ``CallToolResult`` back into a sorted
   ``list[DiaryEntry]``. The transport is replaced with a fake at the
   ``streamablehttp_client`` factory boundary — the same seam the
   autouse ``safe_gateway_mcp_client`` guards.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from graphia.diary_store import (
    DiaryEntry,
    GatewayMCPDiaryStore,
    _parse_mcp_tool_result,
    _SigV4HttpxAuth,
)


# --------------------------------------------------------------------------
# 1. _parse_mcp_tool_result — MCP version-skew unit tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(
            CallToolResult(
                content=[TextContent(type="text", text='{"ok": true}')],
                structuredContent={"ok": True},
            ),
            {"ok": True},
            id="modern-structured-content-write-ack",
        ),
        pytest.param(
            CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"entries": [{"night_index": 0, "content": "hi"}]}
                        ),
                    )
                ],
                structuredContent={
                    "entries": [{"night_index": 0, "content": "hi"}]
                },
            ),
            {"entries": [{"night_index": 0, "content": "hi"}]},
            id="modern-structured-content-read-payload",
        ),
        pytest.param(
            CallToolResult(
                content=[TextContent(type="text", text='{"ok": true}')]
            ),
            {"ok": True},
            id="legacy-text-content-json-body",
        ),
        pytest.param(
            CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "entries": [
                                    {"night_index": 0, "content": "alpha"},
                                    {"night_index": 1, "content": "beta"},
                                ]
                            }
                        ),
                    )
                ],
            ),
            {
                "entries": [
                    {"night_index": 0, "content": "alpha"},
                    {"night_index": 1, "content": "beta"},
                ]
            },
            id="legacy-text-content-read-payload",
        ),
    ],
)
def test_parse_mcp_tool_result_handles_both_response_shapes(
    response: CallToolResult, expected: dict
) -> None:
    """Modern and legacy MCP ``CallToolResult`` shapes both parse to the same dict."""
    assert _parse_mcp_tool_result(response) == expected


def test_parse_mcp_tool_result_prefers_structured_content_when_both_present() -> None:
    """When ``structuredContent`` is set, it wins over the legacy text body.

    Pinning this stops a future "merge the two and let text override"
    refactor from silently breaking the modern path.
    """
    response = CallToolResult(
        content=[TextContent(type="text", text='{"ok": false}')],
        structuredContent={"ok": True},
    )
    assert _parse_mcp_tool_result(response) == {"ok": True}


def test_parse_mcp_tool_result_returns_empty_dict_for_unparseable_text() -> None:
    """Text content that is not valid JSON falls back to ``{}`` — never raises."""
    response = CallToolResult(
        content=[TextContent(type="text", text="not-json-at-all")]
    )
    assert _parse_mcp_tool_result(response) == {}


def test_parse_mcp_tool_result_returns_empty_dict_for_empty_response() -> None:
    """A result with no structured content and no text content yields ``{}``."""
    response = CallToolResult(content=[])
    assert _parse_mcp_tool_result(response) == {}


def test_parse_mcp_tool_result_raises_on_is_error() -> None:
    """``isError=True`` raises rather than silently returning the embedded dict.

    Without this guard, a server-side tool failure carrying a JSON-decodable
    structuredContent payload would be indistinguishable from success at
    the caller's site (e.g. ``night_close`` would happily continue as if
    the diary write succeeded). The raised ``RuntimeError`` carries the
    server's text-content message for diagnostics.
    """
    response = CallToolResult(
        isError=True,
        content=[TextContent(type="text", text="memory id not found")],
        structuredContent={"ok": False},
    )
    with pytest.raises(RuntimeError, match="memory id not found"):
        _parse_mcp_tool_result(response)


def test_parse_mcp_tool_result_raises_on_is_error_without_text() -> None:
    """``isError=True`` with no text-content still raises — falls back to repr."""
    response = CallToolResult(isError=True, content=[])
    with pytest.raises(RuntimeError, match="isError=True"):
        _parse_mcp_tool_result(response)


# --------------------------------------------------------------------------
# 2. End-to-end MCP server smoke — FastMCP handler delegation
# --------------------------------------------------------------------------


@pytest.fixture
def runtime_module(monkeypatch: pytest.MonkeyPatch):
    """Return the Runtime module with a fresh ``InProcessDiaryStore`` impl.

    ``graphia.runtime.__main__`` constructs its module-level
    ``_impl_diary_store`` exactly once on import (FastMCP tools close
    over that reference). To keep tests isolated we swap in a new
    ``InProcessDiaryStore`` per test and restore the original on
    teardown via ``monkeypatch``. The fresh store guarantees one test
    cannot read another test's writes through the global handler.
    """
    from graphia.diary_store import InProcessDiaryStore
    from graphia.runtime import __main__ as runtime_main

    fresh = InProcessDiaryStore()
    monkeypatch.setattr(runtime_main, "_impl_diary_store", fresh)
    return runtime_main


async def test_mcp_server_lists_exactly_diary_write_and_diary_read(
    runtime_module,
) -> None:
    """FastMCP exposes exactly two tools, with the right input schemas."""
    tools = await runtime_module.mcp.list_tools()

    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"diary_write", "diary_read"}

    write_schema = by_name["diary_write"].inputSchema
    assert set(write_schema["required"]) == {
        "game_id",
        "player_id",
        "night_index",
        "content",
    }
    assert write_schema["properties"]["night_index"]["type"] == "integer"
    for field in ("game_id", "player_id", "content"):
        assert write_schema["properties"][field]["type"] == "string"

    read_schema = by_name["diary_read"].inputSchema
    assert set(read_schema["required"]) == {"game_id", "player_id"}


async def _invoke_tool(runtime_module, name: str, args: dict) -> Any:
    """Call a FastMCP tool and return the raw Python return value.

    FastMCP's public ``call_tool`` serialises the return value into a
    ``list[ContentBlock]`` for over-the-wire shipping. We instead route
    through the tool manager with ``convert_result=False`` so the
    test sees the actual dict the handler returned. This is the same
    value the Runtime ships through ``structuredContent`` in the
    streamable-HTTP transport.
    """
    context = runtime_module.mcp.get_context()
    return await runtime_module.mcp._tool_manager.call_tool(
        name, args, context=context, convert_result=False
    )


async def test_mcp_diary_write_delegates_into_impl_store(runtime_module) -> None:
    """A ``diary_write`` tool-call lands in ``_impl_diary_store`` verbatim."""
    result = await _invoke_tool(
        runtime_module,
        "diary_write",
        {
            "game_id": "g-1",
            "player_id": "p-1",
            "night_index": 0,
            "content": "first entry",
        },
    )
    assert result == {"ok": True}

    entries = runtime_module._impl_diary_store.read("g-1", "p-1")
    assert entries == [DiaryEntry(night_index=0, content="first entry")]


async def test_mcp_diary_read_returns_entries_sorted_by_night_index(
    runtime_module,
) -> None:
    """``diary_read`` surfaces all entries for a pair, sorted ascending."""
    for ni, content in [(2, "third"), (0, "first"), (1, "second")]:
        await _invoke_tool(
            runtime_module,
            "diary_write",
            {
                "game_id": "g-1",
                "player_id": "p-1",
                "night_index": ni,
                "content": content,
            },
        )

    result = await _invoke_tool(
        runtime_module, "diary_read", {"game_id": "g-1", "player_id": "p-1"}
    )

    assert result == {
        "entries": [
            {"night_index": 0, "content": "first"},
            {"night_index": 1, "content": "second"},
            {"night_index": 2, "content": "third"},
        ]
    }


async def test_mcp_diary_read_unknown_pair_returns_empty_entries(
    runtime_module,
) -> None:
    """Reading a never-written pair returns ``{"entries": []}`` — no crash."""
    result = await _invoke_tool(
        runtime_module,
        "diary_read",
        {"game_id": "g-missing", "player_id": "p-missing"},
    )
    assert result == {"entries": []}


# --------------------------------------------------------------------------
# 3. GatewayMCPDiaryStore client-side — mocked transport + session
# --------------------------------------------------------------------------


class _FakeClientSession:
    """In-process stand-in for ``mcp.ClientSession``.

    Records ``initialize`` / ``call_tool`` invocations and returns a
    scripted ``CallToolResult`` per tool name. The instance plays the
    role of both the async context manager (returned by the
    ``ClientSession(read, write)`` constructor) and the session itself.

    A single fake is shared across one ``GatewayMCPDiaryStore`` call via
    the ``_install`` helper below; each call constructs a fresh
    transport context so the fake must remain idempotent across enters.
    """

    def __init__(self, scripted: dict[str, CallToolResult]) -> None:
        self._scripted = scripted
        self.initialize_calls = 0
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def initialize(self) -> Any:
        self.initialize_calls += 1
        return None

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        self.tool_calls.append((name, dict(arguments or {})))
        if name not in self._scripted:
            raise AssertionError(
                f"FakeClientSession received unexpected tool call: {name!r} "
                f"(scripted: {list(self._scripted)})"
            )
        return self._scripted[name]


@contextlib.asynccontextmanager
async def _fake_streamable_ctx(
    captured: dict[str, Any],
    auth: Any = None,
    **_: Any,
) -> Any:
    """Async-context fake mimicking ``streamablehttp_client``.

    Yields the ``(read_stream, write_stream, get_session_id)`` triple
    the real factory produces; the streams are throwaway sentinels —
    the fake ``ClientSession`` does not read from them.
    """
    captured["auth"] = auth
    yield (object(), object(), lambda: "fake-session-id")


def _install_fake_mcp(
    monkeypatch: pytest.MonkeyPatch,
    scripted: dict[str, CallToolResult],
    *,
    skip_boto: bool = True,
) -> tuple[dict[str, Any], list[_FakeClientSession]]:
    """Wire a fake transport + fake ``ClientSession`` for the store.

    Returns ``(transport_captured, sessions)`` so the test can assert on
    the ``auth=`` kwarg passed to the transport factory and inspect what
    the store called on the session.

    ``skip_boto`` short-circuits :meth:`GatewayMCPDiaryStore._build_auth`
    to skip the real ``boto3.Session().get_credentials()`` lookup —
    instead returning a sentinel marker so the test can confirm the
    auth flowed through to ``streamablehttp_client`` unchanged.
    """
    transport_captured: dict[str, Any] = {}

    def _factory(url: str, **kwargs: Any):
        transport_captured["url"] = url
        return _fake_streamable_ctx(transport_captured, **kwargs)

    import mcp.client.streamable_http as _streamable_http

    monkeypatch.setattr(_streamable_http, "streamablehttp_client", _factory)

    sessions: list[_FakeClientSession] = []

    def _session_factory(read_stream: Any, write_stream: Any):
        session = _FakeClientSession(scripted)
        sessions.append(session)
        return session

    import mcp as _mcp_pkg

    monkeypatch.setattr(_mcp_pkg, "ClientSession", _session_factory)

    if skip_boto:
        sentinel = object()

        def _fake_build_auth(self: GatewayMCPDiaryStore):
            transport_captured["build_auth_called"] = True
            return sentinel

        transport_captured["sentinel_auth"] = sentinel
        monkeypatch.setattr(
            GatewayMCPDiaryStore, "_build_auth", _fake_build_auth
        )

    return transport_captured, sessions


def test_gateway_store_write_sends_correct_tool_name_and_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``write`` invokes the ``diary_write`` tool with the four expected fields."""
    scripted = {
        "diary_write": CallToolResult(
            content=[TextContent(type="text", text='{"ok": true}')],
            structuredContent={"ok": True},
        )
    }
    captured, sessions = _install_fake_mcp(monkeypatch, scripted)

    store = GatewayMCPDiaryStore(
        gateway_url="https://gw.example.test/mcp", region="us-east-1"
    )
    store.write(
        game_id="g-1",
        player_id="p-1",
        night_index=2,
        content="secret reasoning",
    )

    assert captured["url"] == "https://gw.example.test/mcp"
    assert captured["auth"] is captured["sentinel_auth"], (
        "GatewayMCPDiaryStore must pass the SigV4 auth instance through to "
        "streamablehttp_client unchanged"
    )
    assert len(sessions) == 1
    session = sessions[0]
    assert session.initialize_calls == 1
    assert session.tool_calls == [
        (
            "diary_write",
            {
                "game_id": "g-1",
                "player_id": "p-1",
                "night_index": 2,
                "content": "secret reasoning",
            },
        )
    ]


def test_gateway_store_write_uses_class_level_tool_name_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP tool name comes from ``WRITE_TOOL_NAME`` and matches the server.

    Pinning this couples the client's tool name to the FastMCP-registered
    tool exposed by ``graphia.runtime.__main__`` — a rename in one place
    would break this test rather than silently mis-routing diary writes
    at runtime.
    """
    assert GatewayMCPDiaryStore.WRITE_TOOL_NAME == "diary_write"
    assert GatewayMCPDiaryStore.READ_TOOL_NAME == "diary_read"

    from graphia.runtime import __main__ as runtime_main
    import asyncio

    tools = asyncio.run(runtime_main.mcp.list_tools())
    names = {t.name for t in tools}
    assert GatewayMCPDiaryStore.WRITE_TOOL_NAME in names
    assert GatewayMCPDiaryStore.READ_TOOL_NAME in names


def test_gateway_store_read_parses_modern_response_into_diary_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read`` decodes a modern ``structuredContent`` response into entries."""
    scripted = {
        "diary_read": CallToolResult(
            content=[],
            structuredContent={
                "entries": [
                    {"night_index": 2, "content": "third"},
                    {"night_index": 0, "content": "first"},
                    {"night_index": 1, "content": "second"},
                ]
            },
        )
    }
    captured, sessions = _install_fake_mcp(monkeypatch, scripted)

    store = GatewayMCPDiaryStore(
        gateway_url="https://gw.example.test/mcp", region="us-east-1"
    )
    entries = store.read(game_id="g-1", player_id="p-1")

    # Sorted ascending by night_index, regardless of server return order.
    assert entries == [
        DiaryEntry(night_index=0, content="first"),
        DiaryEntry(night_index=1, content="second"),
        DiaryEntry(night_index=2, content="third"),
    ]
    assert sessions[0].tool_calls == [
        ("diary_read", {"game_id": "g-1", "player_id": "p-1"})
    ]


def test_gateway_store_read_parses_legacy_text_content_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read`` decodes a legacy ``TextContent``-only response too.

    Pairs with the parametrised ``_parse_mcp_tool_result`` test above:
    here the legacy shape is exercised through the full store API, not
    just the helper, so a future change that wires
    ``GatewayMCPDiaryStore.read`` around the helper but forgets to
    surface the legacy shape would fail this test.
    """
    scripted = {
        "diary_read": CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"entries": [{"night_index": 0, "content": "alpha"}]}
                    ),
                )
            ],
        )
    }
    _install_fake_mcp(monkeypatch, scripted)

    store = GatewayMCPDiaryStore(
        gateway_url="https://gw.example.test/mcp", region="us-east-1"
    )
    entries = store.read(game_id="g-1", player_id="p-1")
    assert entries == [DiaryEntry(night_index=0, content="alpha")]


def test_gateway_store_read_returns_empty_list_for_unknown_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read`` against a never-written pair returns ``[]`` cleanly."""
    scripted = {
        "diary_read": CallToolResult(
            content=[], structuredContent={"entries": []}
        )
    }
    _install_fake_mcp(monkeypatch, scripted)

    store = GatewayMCPDiaryStore(
        gateway_url="https://gw.example.test/mcp", region="us-east-1"
    )
    assert store.read(game_id="g-x", player_id="p-x") == []


def test_gateway_store_propagates_server_errors_to_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MCP-side exception during ``call_tool`` propagates, not swallowed.

    The store must not silently absorb a server-side failure (e.g. a
    rejected SigV4 signature surfacing as a transport error). Tests
    that asserted ``write`` is fire-and-forget would mask a real outage.
    """
    class _ExplodingSession(_FakeClientSession):
        async def call_tool(self, name: str, arguments: dict | None = None):
            raise RuntimeError("simulated MCP server error")

    import mcp.client.streamable_http as _streamable_http

    def _factory(url: str, **kwargs: Any):
        return _fake_streamable_ctx({}, **kwargs)

    monkeypatch.setattr(_streamable_http, "streamablehttp_client", _factory)

    import mcp as _mcp_pkg

    monkeypatch.setattr(
        _mcp_pkg,
        "ClientSession",
        lambda _r, _w: _ExplodingSession({}),
    )
    monkeypatch.setattr(
        GatewayMCPDiaryStore, "_build_auth", lambda self: object()
    )

    store = GatewayMCPDiaryStore(
        gateway_url="https://gw.example.test/mcp", region="us-east-1"
    )
    with pytest.raises(RuntimeError, match="simulated MCP server error"):
        store.write(game_id="g-1", player_id="p-1", night_index=0, content="x")


def test_gateway_store_constructor_validates_required_fields() -> None:
    """Missing ``gateway_url`` or ``region`` raises immediately, not on first call."""
    with pytest.raises(ValueError, match="gateway_url"):
        GatewayMCPDiaryStore(gateway_url="", region="us-east-1")
    with pytest.raises(ValueError, match="region"):
        GatewayMCPDiaryStore(gateway_url="https://gw.example.test/mcp", region="")


def test_sigv4_auth_signs_request_with_bedrock_agentcore_service() -> None:
    """``_SigV4HttpxAuth`` produces an ``Authorization`` header with the right scope.

    Pins the service name (``bedrock-agentcore``) and the signing region
    so a future "switch to bedrock-runtime" refactor would fail this
    test rather than silently producing 403s against the real Gateway.
    """
    import httpx
    from botocore.credentials import Credentials

    creds = Credentials(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    auth = _SigV4HttpxAuth(region="us-east-1", credentials=creds)

    request = httpx.Request(
        "POST",
        "https://gw.example.test/mcp",
        headers={"content-type": "application/json"},
        content=b'{"jsonrpc":"2.0"}',
    )
    flow = auth.sync_auth_flow(request)
    signed = next(flow)

    authz = signed.headers.get("Authorization")
    assert authz is not None
    assert "AWS4-HMAC-SHA256" in authz
    assert "us-east-1/bedrock-agentcore/aws4_request" in authz
