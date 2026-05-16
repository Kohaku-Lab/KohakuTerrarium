"""Unit tests for ``mcp/tools.py`` — the four MCP meta-tools.

CLAUDE.md: MCP tools are NOT injected as native tools — the agent uses
``mcp_list`` / ``mcp_call`` / ``mcp_connect`` / ``mcp_disconnect`` which
route to the per-agent ``MCPClientManager``. These tests stub the manager
(a deterministic fake) and assert that each meta-tool routes correctly,
formats its output as documented, and surfaces precise errors.
"""

import pytest

from kohakuterrarium.mcp.tools import (
    MCPCallTool,
    MCPConnectTool,
    MCPDisconnectTool,
    MCPListTool,
    _get_mcp_manager,
)

# ---------------------------------------------------------------------------
# Fake manager + context
# ---------------------------------------------------------------------------


class _FakeManager:
    """Deterministic stand-in for MCPClientManager.

    Records every routed call so tests can assert the meta-tool delegated
    with the exact arguments.
    """

    def __init__(self, *, servers=None, server_tools=None, connect_result=None):
        self._servers = servers if servers is not None else []
        self._server_tools = server_tools or {}
        self._connect_result = connect_result
        self.connect_calls = []
        self.call_tool_calls = []
        self.disconnect_calls = []
        self.disconnect_return = True

    def list_servers(self):
        return self._servers

    def get_server_tools(self, name):
        if name not in self._server_tools:
            raise ValueError(f"MCP server not found: {name}")
        return self._server_tools[name]

    async def connect(self, config):
        self.connect_calls.append(config)
        if isinstance(self._connect_result, Exception):
            raise self._connect_result
        return self._connect_result

    async def call_tool(self, server, tool, args):
        self.call_tool_calls.append((server, tool, args))
        if isinstance(self._call_result, Exception):
            raise self._call_result
        return self._call_result

    _call_result = "tool-output"

    async def disconnect(self, name):
        self.disconnect_calls.append(name)
        return self.disconnect_return


class _FakeAgent:
    def __init__(self, manager):
        self._mcp_manager = manager


class _FakeContext:
    def __init__(self, agent):
        self.agent = agent


def _ctx(manager):
    return _FakeContext(_FakeAgent(manager))


class _ConnInfo:
    def __init__(self, tools):
        self.tools = tools


# ---------------------------------------------------------------------------
# _get_mcp_manager
# ---------------------------------------------------------------------------


class TestGetMcpManager:
    def test_returns_manager_from_context(self):
        mgr = _FakeManager()
        assert _get_mcp_manager(_ctx(mgr)) is mgr

    def test_none_context_raises_runtimeerror(self):
        with pytest.raises(RuntimeError, match="MCP is not available"):
            _get_mcp_manager(None)

    def test_context_without_agent_raises(self):
        class _NoAgent:
            agent = None

        with pytest.raises(RuntimeError, match="No MCP manager found"):
            _get_mcp_manager(_NoAgent())

    def test_agent_without_manager_raises(self):
        class _Agent:
            _mcp_manager = None

        with pytest.raises(RuntimeError, match="Configure mcp_servers"):
            _get_mcp_manager(_FakeContext(_Agent()))


# ---------------------------------------------------------------------------
# mcp_list
# ---------------------------------------------------------------------------


class TestMcpListTool:
    async def test_no_manager_returns_error_result(self):
        result = await MCPListTool().execute({}, context=None)
        assert result.error is not None
        assert "MCP is not available" in result.error

    async def test_empty_server_list_prompts_connect(self):
        mgr = _FakeManager(servers=[])
        result = await MCPListTool().execute({}, context=_ctx(mgr))
        assert result.error is None
        assert result.exit_code == 0
        assert result.output == "No MCP servers connected. Use mcp_connect to add one."

    async def test_overview_lists_each_server_with_tools(self):
        mgr = _FakeManager(
            servers=[
                {
                    "name": "fs",
                    "transport": "stdio",
                    "status": "connected",
                    "error": "",
                    "tools": [
                        {"name": "read", "description": "Read a file"},
                        {"name": "write", "description": ""},
                    ],
                }
            ]
        )
        result = await MCPListTool().execute({}, context=_ctx(mgr))
        assert result.exit_code == 0
        assert result.output == (
            "Connected MCP servers:\n"
            "\n"
            "  fs (stdio, connected, 2 tools)\n"
            "    - read — Read a file\n"
            "    - write\n"
        )

    async def test_overview_shows_server_error_line(self):
        mgr = _FakeManager(
            servers=[
                {
                    "name": "bad",
                    "transport": "sse",
                    "status": "error",
                    "error": "connection refused",
                    "tools": [],
                }
            ]
        )
        result = await MCPListTool().execute({}, context=_ctx(mgr))
        assert "  bad (sse, error, 0 tools)" in result.output
        assert "    Error: connection refused" in result.output

    async def test_detailed_view_renders_tool_parameters(self):
        mgr = _FakeManager(
            servers=[
                {
                    "name": "fs",
                    "transport": "stdio",
                    "status": "connected",
                    "error": "",
                    "tools": [],
                }
            ],
            server_tools={
                "fs": [
                    {
                        "name": "read",
                        "description": "Read a file",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "File path",
                                },
                                "limit": {"type": "integer"},
                            },
                            "required": ["path"],
                        },
                    }
                ]
            },
        )
        result = await MCPListTool().execute({"server": "fs"}, context=_ctx(mgr))
        assert result.exit_code == 0
        assert result.output == (
            "MCP server: fs\n"
            "Tools (1):\n"
            "\n"
            "  read\n"
            "    Read a file\n"
            "    - path: string (required)\n"
            "      File path\n"
            "    - limit: integer\n"
        )

    async def test_detailed_view_unknown_server_returns_error(self):
        mgr = _FakeManager(
            servers=[
                {
                    "name": "fs",
                    "transport": "stdio",
                    "status": "connected",
                    "error": "",
                    "tools": [],
                }
            ],
            server_tools={},
        )
        result = await MCPListTool().execute({"server": "ghost"}, context=_ctx(mgr))
        assert result.error == "MCP server not found: ghost"

    def test_metadata_is_stable(self):
        tool = MCPListTool()
        assert tool.tool_name == "mcp_list"
        assert tool.needs_context is True


# ---------------------------------------------------------------------------
# mcp_call
# ---------------------------------------------------------------------------


class TestMcpCallTool:
    async def test_routes_to_manager_with_exact_args(self):
        mgr = _FakeManager()
        mgr._call_result = "the result"
        result = await MCPCallTool().execute(
            {"server": "fs", "tool": "read", "args": {"path": "/x"}},
            context=_ctx(mgr),
        )
        assert result.output == "the result"
        assert result.exit_code == 0
        assert mgr.call_tool_calls == [("fs", "read", {"path": "/x"})]

    async def test_missing_server_arg_rejected(self):
        mgr = _FakeManager()
        result = await MCPCallTool().execute({"tool": "read"}, context=_ctx(mgr))
        assert result.error == (
            "Missing 'server' argument. Specify which MCP server to call."
        )
        assert mgr.call_tool_calls == []

    async def test_missing_tool_arg_rejected(self):
        mgr = _FakeManager()
        result = await MCPCallTool().execute({"server": "fs"}, context=_ctx(mgr))
        assert result.error == "Missing 'tool' argument. Specify which tool to call."

    async def test_json_string_args_parsed(self):
        mgr = _FakeManager()
        await MCPCallTool().execute(
            {"server": "fs", "tool": "read", "args": '{"path": "/y"}'},
            context=_ctx(mgr),
        )
        assert mgr.call_tool_calls == [("fs", "read", {"path": "/y"})]

    async def test_invalid_json_string_args_rejected(self):
        mgr = _FakeManager()
        result = await MCPCallTool().execute(
            {"server": "fs", "tool": "read", "args": "{not json}"},
            context=_ctx(mgr),
        )
        assert result.error == "Invalid JSON in 'args': {not json}"
        assert mgr.call_tool_calls == []

    async def test_missing_args_defaults_to_empty_dict(self):
        mgr = _FakeManager()
        await MCPCallTool().execute({"server": "fs", "tool": "read"}, context=_ctx(mgr))
        assert mgr.call_tool_calls == [("fs", "read", {})]

    async def test_manager_valueerror_surfaced_as_error(self):
        mgr = _FakeManager()
        mgr._call_result = ValueError("tool not found")
        result = await MCPCallTool().execute(
            {"server": "fs", "tool": "ghost"}, context=_ctx(mgr)
        )
        assert result.error == "tool not found"

    async def test_manager_generic_exception_wrapped(self):
        mgr = _FakeManager()
        mgr._call_result = RuntimeError("boom")
        result = await MCPCallTool().execute(
            {"server": "fs", "tool": "t"}, context=_ctx(mgr)
        )
        assert result.error == "MCP call failed: boom"

    async def test_no_manager_returns_error(self):
        result = await MCPCallTool().execute(
            {"server": "fs", "tool": "t"}, context=None
        )
        assert "MCP is not available" in result.error


# ---------------------------------------------------------------------------
# mcp_connect
# ---------------------------------------------------------------------------


class TestMcpConnectTool:
    async def test_connects_stdio_and_summarizes_tools(self):
        info = _ConnInfo(tools=[{"name": "read"}, {"name": "write"}])
        mgr = _FakeManager(connect_result=info)
        result = await MCPConnectTool().execute(
            {"name": "fs", "command": "fs-server", "args": ["--root", "/"]},
            context=_ctx(mgr),
        )
        assert result.exit_code == 0
        assert result.output == "Connected to fs (2 tools available): read, write"
        cfg = mgr.connect_calls[0]
        assert cfg.name == "fs"
        assert cfg.command == "fs-server"
        assert cfg.args == ["--root", "/"]
        # transport inferred from command presence
        assert cfg.transport == "stdio"

    async def test_transport_inferred_streamable_http_when_only_url(self):
        mgr = _FakeManager(connect_result=_ConnInfo(tools=[]))
        await MCPConnectTool().execute(
            {"name": "remote", "url": "http://h/mcp"}, context=_ctx(mgr)
        )
        assert mgr.connect_calls[0].transport == "streamable_http"

    async def test_explicit_transport_preserved(self):
        mgr = _FakeManager(connect_result=_ConnInfo(tools=[]))
        await MCPConnectTool().execute(
            {"name": "s", "transport": "sse", "url": "http://h/sse"},
            context=_ctx(mgr),
        )
        assert mgr.connect_calls[0].transport == "sse"

    async def test_string_args_split_into_list(self):
        mgr = _FakeManager(connect_result=_ConnInfo(tools=[]))
        await MCPConnectTool().execute(
            {"name": "s", "command": "c", "args": "--a --b"}, context=_ctx(mgr)
        )
        assert mgr.connect_calls[0].args == ["--a", "--b"]

    async def test_missing_name_rejected(self):
        mgr = _FakeManager()
        result = await MCPConnectTool().execute({"command": "c"}, context=_ctx(mgr))
        assert result.error == "Missing 'name' argument. Give this server a name."
        assert mgr.connect_calls == []

    async def test_missing_command_and_url_rejected(self):
        mgr = _FakeManager()
        result = await MCPConnectTool().execute({"name": "s"}, context=_ctx(mgr))
        assert result.error == (
            "Provide either 'command' (for stdio) or 'url' (for HTTP transports)."
        )
        assert mgr.connect_calls == []

    async def test_non_numeric_timeout_rejected(self):
        mgr = _FakeManager()
        result = await MCPConnectTool().execute(
            {"name": "s", "command": "c", "connect_timeout": "soon"},
            context=_ctx(mgr),
        )
        assert result.error == "'connect_timeout' must be a number if provided."
        assert mgr.connect_calls == []

    async def test_numeric_timeout_passed_through(self):
        mgr = _FakeManager(connect_result=_ConnInfo(tools=[]))
        await MCPConnectTool().execute(
            {"name": "s", "command": "c", "connect_timeout": "12.5"},
            context=_ctx(mgr),
        )
        assert mgr.connect_calls[0].connect_timeout == 12.5

    async def test_empty_timeout_string_becomes_none(self):
        mgr = _FakeManager(connect_result=_ConnInfo(tools=[]))
        await MCPConnectTool().execute(
            {"name": "s", "command": "c", "connect_timeout": ""},
            context=_ctx(mgr),
        )
        assert mgr.connect_calls[0].connect_timeout is None

    async def test_more_than_ten_tools_summary_truncated(self):
        info = _ConnInfo(tools=[{"name": f"t{i}"} for i in range(13)])
        mgr = _FakeManager(connect_result=info)
        result = await MCPConnectTool().execute(
            {"name": "big", "command": "c"}, context=_ctx(mgr)
        )
        assert result.output == (
            "Connected to big (13 tools available): "
            "t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, ... (3 more)"
        )

    async def test_import_error_gives_install_hint(self):
        mgr = _FakeManager(connect_result=ImportError("no mcp"))
        result = await MCPConnectTool().execute(
            {"name": "s", "command": "c"}, context=_ctx(mgr)
        )
        assert result.error == "MCP SDK not installed. Install with: pip install mcp"

    async def test_connect_failure_wrapped(self):
        mgr = _FakeManager(connect_result=RuntimeError("refused"))
        result = await MCPConnectTool().execute(
            {"name": "s", "command": "c"}, context=_ctx(mgr)
        )
        assert result.error == "Failed to connect to s: refused"

    async def test_no_manager_returns_error(self):
        result = await MCPConnectTool().execute(
            {"name": "s", "command": "c"}, context=None
        )
        assert "MCP is not available" in result.error


# ---------------------------------------------------------------------------
# mcp_disconnect
# ---------------------------------------------------------------------------


class TestMcpDisconnectTool:
    async def test_disconnect_by_server_arg(self):
        mgr = _FakeManager()
        result = await MCPDisconnectTool().execute({"server": "fs"}, context=_ctx(mgr))
        assert result.output == "Disconnected from fs"
        assert result.exit_code == 0
        assert mgr.disconnect_calls == ["fs"]

    async def test_disconnect_by_name_alias(self):
        mgr = _FakeManager()
        result = await MCPDisconnectTool().execute({"name": "fs"}, context=_ctx(mgr))
        assert result.output == "Disconnected from fs"
        assert mgr.disconnect_calls == ["fs"]

    async def test_server_arg_wins_over_name(self):
        mgr = _FakeManager()
        await MCPDisconnectTool().execute(
            {"server": "primary", "name": "secondary"}, context=_ctx(mgr)
        )
        assert mgr.disconnect_calls == ["primary"]

    async def test_missing_identifier_rejected(self):
        mgr = _FakeManager()
        result = await MCPDisconnectTool().execute({}, context=_ctx(mgr))
        assert result.error == "Missing 'server' or 'name' argument."
        assert mgr.disconnect_calls == []

    async def test_unknown_server_returns_error(self):
        mgr = _FakeManager()
        mgr.disconnect_return = False
        result = await MCPDisconnectTool().execute(
            {"server": "ghost"}, context=_ctx(mgr)
        )
        assert result.error == "Server not found: ghost"

    async def test_no_manager_returns_error(self):
        result = await MCPDisconnectTool().execute({"server": "fs"}, context=None)
        assert "MCP is not available" in result.error


# ---------------------------------------------------------------------------
# Tool metadata / schema declarations
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_all_four_meta_tools_are_direct_mode(self):
        from kohakuterrarium.modules.tool.base import ExecutionMode

        for cls in (MCPListTool, MCPCallTool, MCPConnectTool, MCPDisconnectTool):
            assert cls().execution_mode == ExecutionMode.DIRECT

    def test_all_four_meta_tools_need_context(self):
        for cls in (MCPListTool, MCPCallTool, MCPConnectTool, MCPDisconnectTool):
            assert cls().needs_context is True

    def test_tool_names_match_the_documented_meta_tool_surface(self):
        assert MCPListTool().tool_name == "mcp_list"
        assert MCPCallTool().tool_name == "mcp_call"
        assert MCPConnectTool().tool_name == "mcp_connect"
        assert MCPDisconnectTool().tool_name == "mcp_disconnect"

    def test_descriptions_are_nonempty_one_liners(self):
        for cls in (MCPListTool, MCPCallTool, MCPConnectTool, MCPDisconnectTool):
            desc = cls().description
            assert desc and "\n" not in desc

    def test_mcp_call_schema_requires_server_and_tool(self):
        schema = MCPCallTool().get_parameters_schema()
        assert schema["required"] == ["server", "tool"]
        assert set(schema["properties"]) == {"server", "tool", "args"}

    def test_mcp_connect_schema_requires_name(self):
        schema = MCPConnectTool().get_parameters_schema()
        assert schema["required"] == ["name"]
        # the full stdio + HTTP transport surface is declared
        assert {
            "name",
            "transport",
            "command",
            "args",
            "url",
            "env",
            "connect_timeout",
        } <= set(schema["properties"])

    def test_mcp_list_schema_has_optional_server(self):
        schema = MCPListTool().get_parameters_schema()
        assert "server" in schema["properties"]
        assert "required" not in schema

    def test_mcp_disconnect_schema_accepts_server_and_name(self):
        schema = MCPDisconnectTool().get_parameters_schema()
        assert set(schema["properties"]) == {"server", "name"}
