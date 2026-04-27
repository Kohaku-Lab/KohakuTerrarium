import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from kohakuterrarium.mcp.client import (
    MCPClientManager,
    MCPServerConfig,
    normalize_mcp_transport,
)


class _DummyContextManager:
    def __init__(self, entered):
        self.entered = entered
        self.exited = False

    async def __aenter__(self):
        return self.entered

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True


class _DummySession:
    instances = []

    def __init__(self, read_stream, write_stream):
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.entered = False
        self.initialized_after_enter = False
        self.exited = False
        self.__class__.instances.append(self)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True

    async def initialize(self):
        self.initialized_after_enter = self.entered

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="hello",
                    description="say hello",
                    inputSchema={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                )
            ]
        )


@pytest.fixture(autouse=True)
def _restore_mcp_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name == "mcp" or name.startswith("mcp."):
            sys.modules.pop(name, None)
    _DummySession.instances.clear()


def _install_fake_mcp_modules(monkeypatch):
    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = _DummySession

    client_pkg = types.ModuleType("mcp.client")
    stdio_mod = types.ModuleType("mcp.client.stdio")
    sse_mod = types.ModuleType("mcp.client.sse")
    streamable_mod = types.ModuleType("mcp.client.streamable_http")

    class _StdioServerParameters:
        def __init__(self, command, args, env=None):
            self.command = command
            self.args = args
            self.env = env

    stdio_mod.StdioServerParameters = _StdioServerParameters
    stdio_mod.stdio_client = lambda params: _DummyContextManager(("r-stdio", "w-stdio"))
    sse_mod.sse_client = lambda url: _DummyContextManager(("r-sse", "w-sse"))
    streamable_mod.streamablehttp_client = lambda url: _DummyContextManager(
        ("r-http", "w-http", lambda: "sid")
    )

    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.client", client_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable_mod)


class TestNormalizeTransport:
    def test_aliases(self):
        assert normalize_mcp_transport("stdio") == "stdio"
        assert normalize_mcp_transport("http") == "sse"
        assert normalize_mcp_transport("sse") == "sse"
        assert normalize_mcp_transport("streamable_http") == "streamable_http"
        assert normalize_mcp_transport("streamable-http") == "streamable_http"
        assert normalize_mcp_transport("streamablehttp") == "streamable_http"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown transport"):
            normalize_mcp_transport("websocket")


class TestMCPClientManager:
    @pytest.mark.asyncio
    async def test_stdio_enters_session_before_initialize(self, monkeypatch):
        _install_fake_mcp_modules(monkeypatch)
        mgr = MCPClientManager()
        info = await mgr.connect(
            MCPServerConfig(name="demo", transport="stdio", command="demo-cmd")
        )

        assert info.status == "connected"
        session = _DummySession.instances[-1]
        assert session.entered is True
        assert session.initialized_after_enter is True
        assert mgr.list_servers()[0]["tools"][0]["name"] == "hello"

    @pytest.mark.asyncio
    async def test_sse_enters_session_before_initialize(self, monkeypatch):
        _install_fake_mcp_modules(monkeypatch)
        mgr = MCPClientManager()
        info = await mgr.connect(
            MCPServerConfig(name="demo", transport="http", url="http://localhost/sse")
        )

        assert info.status == "connected"
        session = _DummySession.instances[-1]
        assert session.read_stream == "r-sse"
        assert session.initialized_after_enter is True

    @pytest.mark.asyncio
    async def test_streamable_http_accepts_three_tuple(self, monkeypatch):
        _install_fake_mcp_modules(monkeypatch)
        mgr = MCPClientManager()
        info = await mgr.connect(
            MCPServerConfig(
                name="demo",
                transport="streamable_http",
                url="http://localhost/mcp",
            )
        )

        assert info.status == "connected"
        session = _DummySession.instances[-1]
        assert session.read_stream == "r-http"
        assert session.write_stream == "w-http"
        assert session.initialized_after_enter is True

    @pytest.mark.asyncio
    async def test_connect_timeout_uses_wait_for_wrapper(self, monkeypatch):
        _install_fake_mcp_modules(monkeypatch)
        mgr = MCPClientManager()

        async def fake_impl(config):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(mgr, "_connect_impl", fake_impl)

        with pytest.raises(TimeoutError, match="Timed out after 0.01s"):
            await mgr.connect(
                MCPServerConfig(
                    name="slow",
                    transport="stdio",
                    command="demo-cmd",
                    connect_timeout=0.01,
                )
            )

        assert mgr.servers["slow"].status == "error"
        assert "Timed out" in mgr.servers["slow"].error
