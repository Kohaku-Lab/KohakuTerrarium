from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_mcp import init_mcp


class _SlowConnectManager:
    def __init__(self):
        self.calls = []
        self.disconnects = []

    async def connect(self, config):
        self.calls.append(config)
        if config.name == "slow":
            raise TimeoutError("MCP server slow: Timed out after 0.01s")
        return SimpleNamespace(tools=[{"name": "ok"}])

    async def disconnect(self, name):
        self.disconnects.append(name)
        return True


class TestInitMCP:
    @pytest.mark.asyncio
    async def test_passes_timeout_and_continues_after_failure(self, monkeypatch):
        manager = _SlowConnectManager()
        agent = SimpleNamespace(
            config=SimpleNamespace(
                mcp_servers=[
                    {
                        "name": "slow",
                        "transport": "streamable_http",
                        "url": "http://bad/mcp",
                        "connect_timeout": 0.01,
                    },
                    {
                        "name": "good",
                        "transport": "stdio",
                        "command": "demo-cmd",
                    },
                ]
            ),
            _mcp_manager=None,
        )

        import kohakuterrarium.core.agent_mcp as agent_mcp_mod

        monkeypatch.setattr(agent_mcp_mod, "MCPClientManager", lambda: manager)
        await init_mcp(agent)

        assert len(manager.calls) == 2
        assert manager.calls[0].connect_timeout == 0.01
        assert manager.calls[1].connect_timeout is None
        assert manager.disconnects == ["slow"]

    @pytest.mark.asyncio
    async def test_missing_timeout_remains_none_on_config(self, monkeypatch):
        manager = _SlowConnectManager()
        agent = SimpleNamespace(
            config=SimpleNamespace(
                mcp_servers=[{"name": "good", "transport": "stdio", "command": "demo"}]
            ),
            _mcp_manager=None,
        )

        import kohakuterrarium.core.agent_mcp as agent_mcp_mod

        monkeypatch.setattr(agent_mcp_mod, "MCPClientManager", lambda: manager)
        await init_mcp(agent)

        assert manager.calls[0].connect_timeout is None
