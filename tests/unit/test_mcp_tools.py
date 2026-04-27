from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kohakuterrarium.mcp.tools import MCPConnectTool
from kohakuterrarium.modules.tool.base import ToolContext


class TestMCPConnectTool:
    @pytest.mark.asyncio
    async def test_runtime_connect_defaults_url_to_streamable_http(self):
        tool = MCPConnectTool()
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=SimpleNamespace(tools=[{"name": "hello"}]))
        context = ToolContext(
            agent_name="agent",
            session=None,
            working_dir=Path.cwd(),
            agent=SimpleNamespace(_mcp_manager=mgr),
        )

        result = await tool.execute(
            {"name": "demo", "url": "http://127.0.0.1:8000/mcp"},
            context=context,
        )

        assert result.success is True
        config = mgr.connect.await_args.args[0]
        assert config.transport == "streamable_http"
        assert config.url == "http://127.0.0.1:8000/mcp"

    @pytest.mark.asyncio
    async def test_runtime_connect_accepts_explicit_transport_and_timeout(self):
        tool = MCPConnectTool()
        mgr = MagicMock()
        mgr.connect = AsyncMock(return_value=SimpleNamespace(tools=[]))
        context = ToolContext(
            agent_name="agent",
            session=None,
            working_dir=Path.cwd(),
            agent=SimpleNamespace(_mcp_manager=mgr),
        )

        await tool.execute(
            {
                "name": "demo",
                "transport": "http",
                "url": "http://127.0.0.1:8000/sse",
                "connect_timeout": 9,
            },
            context=context,
        )

        config = mgr.connect.await_args.args[0]
        assert config.transport == "http"
        assert config.connect_timeout == 9.0
