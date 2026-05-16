"""Unit tests for :mod:`kohakuterrarium.core.agent_mcp`."""

import types
from unittest.mock import AsyncMock, MagicMock


from kohakuterrarium.core import agent_mcp as am
from kohakuterrarium.core.agent_mcp import (
    init_mcp,
    inject_mcp_tools_into_prompt,
)


class _FakeManager:
    def __init__(self, *, server_list=None, fail_on=None):
        self._servers = server_list or []
        self.connect = AsyncMock(side_effect=self._connect_impl)
        self.disconnect = AsyncMock()
        self._connect_log: list[str] = []
        self._fail_on = fail_on or set()

    async def _connect_impl(self, config):
        self._connect_log.append(config.name)
        if config.name in self._fail_on:
            raise RuntimeError("connect failed")
        return types.SimpleNamespace(tools=[{"name": "t1"}, {"name": "t2"}])

    def list_servers(self):
        return list(self._servers)


def _agent_with_mcp(*, mcp_configs=None):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(mcp_servers=mcp_configs or []),
        _mcp_manager=None,
        update_system_prompt=MagicMock(),
    )


# ── init_mcp ─────────────────────────────────────────────────────


class TestInitMCP:
    async def test_no_configs_sets_manager_to_none(self):
        a = _agent_with_mcp()
        await init_mcp(a)
        assert a._mcp_manager is None

    async def test_connects_all_servers(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                {"name": "s1", "transport": "stdio", "command": "echo"},
                {"name": "s2", "transport": "stdio", "command": "ls"},
            ]
        )
        await init_mcp(a)
        assert a._mcp_manager is fake
        assert fake._connect_log == ["s1", "s2"]

    async def test_skips_non_dict_entries(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                "not a dict",
                {"name": "s1", "transport": "stdio", "command": "echo"},
            ]
        )
        await init_mcp(a)
        assert fake._connect_log == ["s1"]

    async def test_empty_name_skipped(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(mcp_configs=[{"transport": "stdio", "command": "echo"}])
        await init_mcp(a)
        # Not connected — no name.
        assert fake._connect_log == []

    async def test_failed_connect_disconnects(self, monkeypatch):
        fake = _FakeManager(fail_on={"bad"})
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                {"name": "bad", "transport": "stdio", "command": "x"},
                {"name": "good", "transport": "stdio", "command": "y"},
            ]
        )
        await init_mcp(a)
        # Bad attempted; disconnect called for cleanup; good still connected.
        fake.disconnect.assert_awaited_with("bad")
        assert "good" in fake._connect_log


# ── inject_mcp_tools_into_prompt ─────────────────────────────────


class TestInjectMcpToolsIntoPrompt:
    def test_no_manager_no_op(self):
        a = _agent_with_mcp()
        inject_mcp_tools_into_prompt(a)
        a.update_system_prompt.assert_not_called()

    def test_no_servers_no_op(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(server_list=[])
        inject_mcp_tools_into_prompt(a)
        a.update_system_prompt.assert_not_called()

    def test_disconnected_server_skipped(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "s",
                    "status": "disconnected",
                    "tools": [{"name": "t"}],
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        # All servers disconnected → nothing added beyond the header.
        a.update_system_prompt.assert_not_called()

    def test_connected_server_renders_tools(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "fs",
                    "status": "connected",
                    "tools": [
                        {
                            "name": "read_file",
                            "description": "Read a file",
                            "input_schema": {
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "file path",
                                    },
                                    "binary": {"type": "boolean"},
                                },
                                "required": ["path"],
                            },
                        }
                    ],
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        a.update_system_prompt.assert_called_once()
        prompt = a.update_system_prompt.call_args[0][0]
        assert "Available MCP Tools" in prompt
        assert "### Server: fs" in prompt
        assert "**read_file**" in prompt
        assert "Read a file" in prompt
        # Required arg labelled.
        assert "`path`: string (required)" in prompt
        # Non-required arg present.
        assert "`binary`: boolean" in prompt

    def test_tool_without_description_or_schema(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "minimal",
                    "status": "connected",
                    "tools": [{"name": "x"}],  # no desc, no schema
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        prompt = a.update_system_prompt.call_args[0][0]
        # Tool listed without description.
        assert "**x**" in prompt
        # No "—" separator because no description.
        assert "**x** —" not in prompt


# ── disconnect failure path during connect cleanup (lines 60-61) ──


class TestFailedConnectDisconnectFailure:
    async def test_disconnect_failure_silently_swallowed(self, monkeypatch):
        from unittest.mock import AsyncMock

        fake = _FakeManager(fail_on={"bad"})
        # Make disconnect also raise.
        fake.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                {"name": "bad", "transport": "stdio", "command": "x"},
            ]
        )
        # Should not raise — disconnect failure is swallowed.
        await init_mcp(a)
