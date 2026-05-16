"""Unit tests for ``mcp/client.py`` — MCPClientManager + config dataclasses.

The ``mcp`` SDK transport is stubbed: a fake ``stdio_client`` /
``ClientSession`` pair stands in for the real I/O so the manager's
connection-state bookkeeping, tool routing, and error handling can be
asserted deterministically. Every assert checks an observable behavior
(state changed, value returned, error raised), not a return shape.
"""

import asyncio
import sys
import types

import pytest

from kohakuterrarium.mcp.client import (
    DEFAULT_MCP_CONNECT_TIMEOUT,
    MCPClientManager,
    MCPServerConfig,
    MCPServerInfo,
    normalize_mcp_transport,
)

# ---------------------------------------------------------------------------
# Fake mcp SDK transport
# ---------------------------------------------------------------------------


class _FakeToolDecl:
    def __init__(self, name, description="", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema if input_schema is not None else {}


class _FakeToolsResponse:
    def __init__(self, tools):
        self.tools = tools


class _FakeContentText:
    def __init__(self, text):
        self.text = text


class _FakeContentBinary:
    def __init__(self, data):
        self.data = data


class _FakeCallResult:
    def __init__(self, content, is_error=False):
        self.content = content
        self.isError = is_error


class _FakeClientSession:
    """Stand-in for ``mcp.ClientSession``."""

    def __init__(self, read_stream, write_stream, *, tools=None, call_result=None):
        self.read_stream = read_stream
        self.write_stream = write_stream
        self._tools = tools or []
        self._call_result = call_result
        self.initialized = False
        self.aexit_called = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.aexit_called = True
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return _FakeToolsResponse(self._tools)

    async def call_tool(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments))
        if isinstance(self._call_result, Exception):
            raise self._call_result
        return self._call_result


class _FakeTransportContext:
    """Stand-in for ``stdio_client(...)`` / ``sse_client(...)`` context mgr."""

    def __init__(self, *, yield_value=("read", "write"), raise_on_enter=None):
        self._yield_value = yield_value
        self._raise_on_enter = raise_on_enter
        self.aenter_called = False
        self.aexit_called = False

    async def __aenter__(self):
        self.aenter_called = True
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self._yield_value

    async def __aexit__(self, *exc):
        self.aexit_called = True
        return False


def _install_fake_mcp(monkeypatch, *, session_cls=None, transport_ctx=None):
    """Install a fake ``mcp`` package tree so the deferred imports resolve.

    Returns the ``_FakeTransportContext`` instance the manager will get.
    """
    session_cls = session_cls or _FakeClientSession
    transport_ctx = transport_ctx or _FakeTransportContext()

    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = session_cls

    stdio_mod = types.ModuleType("mcp.client.stdio")

    class _StdioServerParameters:
        def __init__(self, command, args=None, env=None):
            self.command = command
            self.args = args
            self.env = env

    def _stdio_client(params):
        transport_ctx.params = params
        return transport_ctx

    stdio_mod.StdioServerParameters = _StdioServerParameters
    stdio_mod.stdio_client = _stdio_client

    sse_mod = types.ModuleType("mcp.client.sse")

    def _sse_client(url):
        transport_ctx.url = url
        return transport_ctx

    sse_mod.sse_client = _sse_client

    http_mod = types.ModuleType("mcp.client.streamable_http")

    def _streamablehttp_client(url):
        transport_ctx.url = url
        return transport_ctx

    http_mod.streamablehttp_client = _streamablehttp_client

    client_pkg = types.ModuleType("mcp.client")

    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.client", client_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", http_mod)
    return transport_ctx


# ---------------------------------------------------------------------------
# normalize_mcp_transport
# ---------------------------------------------------------------------------


class TestNormalizeMcpTransport:
    def test_stdio_passthrough(self):
        assert normalize_mcp_transport("stdio") == "stdio"

    def test_empty_string_defaults_to_stdio(self):
        # docstring: "(transport or 'stdio')"
        assert normalize_mcp_transport("") == "stdio"

    def test_http_and_sse_both_map_to_sse(self):
        assert normalize_mcp_transport("http") == "sse"
        assert normalize_mcp_transport("sse") == "sse"

    def test_case_and_dash_insensitive(self):
        assert normalize_mcp_transport("  STREAMABLE-HTTP ") == "streamable_http"
        assert normalize_mcp_transport("StreamableHttp") == "streamable_http"
        assert normalize_mcp_transport("HTTP_STREAMABLE") == "streamable_http"

    def test_unknown_transport_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown transport: grpc"):
            normalize_mcp_transport("grpc")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestServerDataclasses:
    def test_config_defaults(self):
        cfg = MCPServerConfig(name="srv")
        assert cfg.transport == "stdio"
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.url == ""
        assert cfg.connect_timeout is None

    def test_config_args_env_are_independent_instances(self):
        a = MCPServerConfig(name="a")
        b = MCPServerConfig(name="b")
        a.args.append("--x")
        a.env["K"] = "V"
        assert b.args == []
        assert b.env == {}

    def test_info_defaults(self):
        cfg = MCPServerConfig(name="srv")
        info = MCPServerInfo(config=cfg)
        assert info.status == "disconnected"
        assert info.error == ""
        assert info.tools == []
        assert info.config is cfg

    def test_default_timeout_constant(self):
        assert DEFAULT_MCP_CONNECT_TIMEOUT == 20.0


# ---------------------------------------------------------------------------
# connect — happy path + state
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_stdio_connect_discovers_tools_and_marks_connected(self, monkeypatch):
        tools = [
            _FakeToolDecl("echo", "Echo text", {"type": "object", "properties": {}}),
            _FakeToolDecl("add", "", None),
        ]
        session = _FakeClientSession("r", "w", tools=tools)
        _install_fake_mcp(monkeypatch, session_cls=lambda r, w: session)

        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="srv", transport="stdio", command="run-me")
        info = await mgr.connect(cfg)

        assert info.status == "connected"
        assert info.error == ""
        # tools were discovered and normalized into dicts
        assert info.tools == [
            {
                "name": "echo",
                "description": "Echo text",
                "input_schema": {"type": "object", "properties": {}},
            },
            {"name": "add", "description": "", "input_schema": {}},
        ]
        # session was initialized + registered
        assert session.initialized is True
        assert mgr._sessions["srv"] is session
        assert mgr.servers["srv"] is info

    async def test_stdio_passes_command_args_env_to_params(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[])
        ctx = _FakeTransportContext()
        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: session, transport_ctx=ctx
        )

        mgr = MCPClientManager()
        cfg = MCPServerConfig(
            name="srv",
            transport="stdio",
            command="mytool",
            args=["--flag", "v"],
            env={"TOKEN": "abc"},
        )
        await mgr.connect(cfg)

        assert ctx.params.command == "mytool"
        assert ctx.params.args == ["--flag", "v"]
        assert ctx.params.env == {"TOKEN": "abc"}

    async def test_stdio_empty_env_passed_as_none(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[])
        ctx = _FakeTransportContext()
        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: session, transport_ctx=ctx
        )

        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="s", command="c"))
        # docstring intent: empty env dict -> None (don't override child env)
        assert ctx.params.env is None

    async def test_sse_transport_uses_url(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[])
        ctx = _FakeTransportContext()
        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: session, transport_ctx=ctx
        )

        mgr = MCPClientManager()
        await mgr.connect(
            MCPServerConfig(name="s", transport="sse", url="http://h/sse")
        )
        assert ctx.url == "http://h/sse"

    async def test_streamable_http_transport_uses_url(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[])
        ctx = _FakeTransportContext()
        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: session, transport_ctx=ctx
        )

        mgr = MCPClientManager()
        await mgr.connect(
            MCPServerConfig(name="s", transport="streamable_http", url="http://h/mcp")
        )
        assert ctx.url == "http://h/mcp"

    async def test_reconnect_same_name_is_noop_returns_existing(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[_FakeToolDecl("t")])
        _install_fake_mcp(monkeypatch, session_cls=lambda r, w: session)

        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="srv", command="c")
        first = await mgr.connect(cfg)
        second = await mgr.connect(cfg)
        # docstring: "already connected" → returns the same info, no 2nd session
        assert second is first
        assert len(mgr._sessions) == 1


class TestConnectErrors:
    async def test_stdio_without_command_raises_and_marks_error(self, monkeypatch):
        _install_fake_mcp(monkeypatch)
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="srv", transport="stdio", command="")
        with pytest.raises(ValueError, match="stdio transport requires 'command'"):
            await mgr.connect(cfg)
        # the failed server is still tracked with status=error
        assert mgr.servers["srv"].status == "error"
        assert "command" in mgr.servers["srv"].error
        # no leaked session
        assert "srv" not in mgr._sessions

    async def test_sse_without_url_raises(self, monkeypatch):
        _install_fake_mcp(monkeypatch)
        mgr = MCPClientManager()
        with pytest.raises(ValueError, match="SSE transport requires 'url'"):
            await mgr.connect(MCPServerConfig(name="s", transport="sse"))
        assert mgr.servers["s"].status == "error"

    async def test_streamable_http_without_url_raises(self, monkeypatch):
        _install_fake_mcp(monkeypatch)
        mgr = MCPClientManager()
        with pytest.raises(ValueError, match="streamable_http transport requires"):
            await mgr.connect(MCPServerConfig(name="s", transport="streamable_http"))

    async def test_transport_yielding_no_streams_raises_clean_error(self, monkeypatch):
        # transport context yields a non-subscriptable value
        ctx = _FakeTransportContext(yield_value=42)
        _install_fake_mcp(monkeypatch, transport_ctx=ctx)
        mgr = MCPClientManager()
        with pytest.raises(ValueError, match="did not yield read/write streams"):
            await mgr.connect(MCPServerConfig(name="s", command="c"))
        assert mgr.servers["s"].status == "error"

    async def test_connect_failure_marks_error_with_message(self, monkeypatch):
        boom = RuntimeError("transport exploded")
        ctx = _FakeTransportContext(raise_on_enter=boom)
        _install_fake_mcp(monkeypatch, transport_ctx=ctx)
        mgr = MCPClientManager()
        with pytest.raises(RuntimeError, match="transport exploded"):
            await mgr.connect(MCPServerConfig(name="s", command="c"))
        info = mgr.servers["s"]
        assert info.status == "error"
        assert info.error == "transport exploded"

    async def test_negative_timeout_rejected_before_connect(self, monkeypatch):
        _install_fake_mcp(monkeypatch)
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="s", command="c", connect_timeout=-1.0)
        with pytest.raises(ValueError, match="connect_timeout must be greater than 0"):
            await mgr.connect(cfg)
        # rejected at the gate — server never even registered
        assert "s" not in mgr.servers

    async def test_zero_timeout_rejected(self, monkeypatch):
        _install_fake_mcp(monkeypatch)
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="s", command="c", connect_timeout=0.0)
        with pytest.raises(ValueError, match="connect_timeout must be greater than 0"):
            await mgr.connect(cfg)

    async def test_timeout_raises_timeouterror_and_marks_error(self, monkeypatch):
        # session.initialize hangs forever -> wait_for trips
        class _HangingSession(_FakeClientSession):
            async def initialize(self):
                await asyncio.sleep(10)

        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: _HangingSession(r, w, tools=[])
        )
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="slow", command="c", connect_timeout=0.01)
        with pytest.raises(TimeoutError, match="slow: Timed out after"):
            await mgr.connect(cfg)
        assert mgr.servers["slow"].status == "error"
        assert "Timed out" in mgr.servers["slow"].error

    async def test_timeout_path_succeeds_when_fast(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[_FakeToolDecl("t")])
        _install_fake_mcp(monkeypatch, session_cls=lambda r, w: session)
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="s", command="c", connect_timeout=5.0)
        info = await mgr.connect(cfg)
        assert info.status == "connected"

    async def test_timeout_reuses_existing_info_record(self, monkeypatch):
        # _connect_impl registers the MCPServerInfo before hanging, so the
        # timeout handler must mutate THAT record, not create a second one.
        class _HangingSession(_FakeClientSession):
            async def initialize(self):
                await asyncio.sleep(10)

        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: _HangingSession(r, w, tools=[])
        )
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="slow", command="c", connect_timeout=0.01)
        with pytest.raises(TimeoutError):
            await mgr.connect(cfg)
        # exactly one server record, in error state
        assert list(mgr.servers) == ["slow"]
        assert mgr.servers["slow"].status == "error"


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_disconnect_unknown_returns_false(self):
        mgr = MCPClientManager()
        assert await mgr.disconnect("nope") is False

    async def test_disconnect_closes_session_and_removes_server(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[])
        ctx = _FakeTransportContext()
        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: session, transport_ctx=ctx
        )
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="srv", command="c"))

        result = await mgr.disconnect("srv")
        assert result is True
        # session + transport context both exited
        assert session.aexit_called is True
        assert ctx.aexit_called is True
        # server fully removed from registry
        assert "srv" not in mgr.servers
        assert "srv" not in mgr._sessions
        assert "srv" not in mgr._transports

    async def test_disconnect_swallows_session_close_error(self, monkeypatch):
        # a session whose __aexit__ raises must not break disconnect
        class _BadCloseSession(_FakeClientSession):
            async def __aexit__(self, *exc):
                raise RuntimeError("close failed")

        ctx = _FakeTransportContext()
        _install_fake_mcp(
            monkeypatch,
            session_cls=lambda r, w: _BadCloseSession(r, w, tools=[]),
            transport_ctx=ctx,
        )
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="srv", command="c"))
        # error swallowed -> disconnect still succeeds + cleans up
        assert await mgr.disconnect("srv") is True
        assert "srv" not in mgr.servers
        # transport ctx still got exited despite the session error
        assert ctx.aexit_called is True

    async def test_disconnect_swallows_transport_exit_error(self, monkeypatch):
        class _BadCtx(_FakeTransportContext):
            async def __aexit__(self, *exc):
                self.aexit_called = True
                raise RuntimeError("transport exit failed")

        session = _FakeClientSession("r", "w", tools=[])
        ctx = _BadCtx()
        _install_fake_mcp(
            monkeypatch, session_cls=lambda r, w: session, transport_ctx=ctx
        )
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="srv", command="c"))
        assert await mgr.disconnect("srv") is True
        assert "srv" not in mgr.servers

    async def test_shutdown_continues_past_a_failing_disconnect(self, monkeypatch):
        created = []

        def _factory(r, w):
            s = _FakeClientSession(r, w, tools=[])
            created.append(s)
            return s

        _install_fake_mcp(monkeypatch, session_cls=_factory)
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="a", command="c"))
        await mgr.connect(MCPServerConfig(name="b", command="c"))

        # make disconnect("a") raise; shutdown must still reach "b"
        original = mgr.disconnect

        async def _flaky(name):
            if name == "a":
                raise RuntimeError("disconnect a failed")
            return await original(name)

        monkeypatch.setattr(mgr, "disconnect", _flaky)
        await mgr.shutdown()
        # "b" was still disconnected despite "a" raising
        assert "b" not in mgr.servers

    async def test_shutdown_disconnects_every_server(self, monkeypatch):
        # each connect needs its own session instance
        created = []

        def _tracking_factory(r, w):
            s = _FakeClientSession(r, w, tools=[])
            created.append(s)
            return s

        _install_fake_mcp(monkeypatch, session_cls=_tracking_factory)
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="a", command="c"))
        await mgr.connect(MCPServerConfig(name="b", command="c"))
        assert len(mgr.servers) == 2

        await mgr.shutdown()
        assert mgr.servers == {}
        assert all(s.aexit_called for s in created)


# ---------------------------------------------------------------------------
# call_tool
# ---------------------------------------------------------------------------


class TestCallTool:
    async def _connected_mgr(self, monkeypatch, *, tools, call_result):
        session = _FakeClientSession("r", "w", tools=tools)
        session._call_result = call_result
        _install_fake_mcp(monkeypatch, session_cls=lambda r, w: session)
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="srv", command="c"))
        return mgr, session

    async def test_call_unknown_server_raises(self):
        mgr = MCPClientManager()
        with pytest.raises(ValueError, match="MCP server not connected: ghost"):
            await mgr.call_tool("ghost", "t", {})

    async def test_call_unknown_tool_lists_available(self, monkeypatch):
        mgr, _ = await self._connected_mgr(
            monkeypatch,
            tools=[_FakeToolDecl("real")],
            call_result=_FakeCallResult([_FakeContentText("x")]),
        )
        with pytest.raises(ValueError) as exc:
            await mgr.call_tool("srv", "fake", {})
        msg = str(exc.value)
        assert "Tool 'fake' not found on server 'srv'" in msg
        assert "Available: real" in msg

    async def test_call_tool_returns_joined_text_content(self, monkeypatch):
        result = _FakeCallResult([_FakeContentText("line1"), _FakeContentText("line2")])
        mgr, session = await self._connected_mgr(
            monkeypatch, tools=[_FakeToolDecl("echo")], call_result=result
        )
        out = await mgr.call_tool("srv", "echo", {"msg": "hi"})
        assert out == "line1\nline2"
        # args forwarded verbatim under the 'arguments' kwarg
        assert session.calls == [("echo", {"msg": "hi"})]

    async def test_call_tool_binary_content_summarized(self, monkeypatch):
        result = _FakeCallResult([_FakeContentBinary(b"\x00\x01\x02")])
        mgr, _ = await self._connected_mgr(
            monkeypatch, tools=[_FakeToolDecl("img")], call_result=result
        )
        out = await mgr.call_tool("srv", "img", {})
        assert out == "[binary data: 3 bytes]"

    async def test_call_tool_unknown_content_stringified(self, monkeypatch):
        class _Weird:
            def __str__(self):
                return "weird-content"

        result = _FakeCallResult([_Weird()])
        mgr, _ = await self._connected_mgr(
            monkeypatch, tools=[_FakeToolDecl("w")], call_result=result
        )
        out = await mgr.call_tool("srv", "w", {})
        assert out == "weird-content"

    async def test_call_tool_empty_content_says_no_output(self, monkeypatch):
        result = _FakeCallResult([])
        mgr, _ = await self._connected_mgr(
            monkeypatch, tools=[_FakeToolDecl("e")], call_result=result
        )
        out = await mgr.call_tool("srv", "e", {})
        assert out == "(no output)"

    async def test_call_tool_error_result_prefixed(self, monkeypatch):
        result = _FakeCallResult([_FakeContentText("bad input")], is_error=True)
        mgr, _ = await self._connected_mgr(
            monkeypatch, tools=[_FakeToolDecl("f")], call_result=result
        )
        out = await mgr.call_tool("srv", "f", {})
        assert out == "[MCP Error] bad input"


# ---------------------------------------------------------------------------
# list_servers / get_server_tools
# ---------------------------------------------------------------------------


class TestListing:
    async def test_list_servers_reports_each_server_state(self, monkeypatch):
        session = _FakeClientSession("r", "w", tools=[_FakeToolDecl("t", "desc")])
        _install_fake_mcp(monkeypatch, session_cls=lambda r, w: session)
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="srv", transport="stdio", command="c"))

        listing = mgr.list_servers()
        assert listing == [
            {
                "name": "srv",
                "transport": "stdio",
                "status": "connected",
                "error": "",
                "tools": [{"name": "t", "description": "desc", "input_schema": {}}],
            }
        ]

    def test_list_servers_empty_when_none_connected(self):
        assert MCPClientManager().list_servers() == []

    async def test_get_server_tools_returns_tool_dicts(self, monkeypatch):
        session = _FakeClientSession(
            "r", "w", tools=[_FakeToolDecl("a"), _FakeToolDecl("b")]
        )
        _install_fake_mcp(monkeypatch, session_cls=lambda r, w: session)
        mgr = MCPClientManager()
        await mgr.connect(MCPServerConfig(name="srv", command="c"))
        tools = mgr.get_server_tools("srv")
        assert [t["name"] for t in tools] == ["a", "b"]

    def test_get_server_tools_unknown_raises(self):
        mgr = MCPClientManager()
        with pytest.raises(ValueError, match="MCP server not found: ghost"):
            mgr.get_server_tools("ghost")
