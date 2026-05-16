"""Unit: ``MCPClientManager`` cross-task teardown — pins BUG B-mcp-1.

``mcp/client.py``'s ``disconnect`` is documented to return a ``bool``
and tear the connection down. The manager opens the stdio transport's
``anyio``-task-group-backed context manager in ``_open_transport_session``
(running on the ``connect()`` caller's task) and exits it in
``_cleanup_connection`` (running on the ``disconnect()`` caller's task).
``anyio`` cancel scopes are task-bound, so when those two tasks differ
the exit raises ``CancelledError``.

This test drives the *real* manager against a *real* in-process stdio
MCP server (the smallest real transport — faking it would not exhibit
the ``anyio`` task-binding the bug is about) and confirms the
same-task path is clean while the cross-task path is broken. Pinned
``xfail(strict=True)`` until ``mcp/client.py`` keeps the transport
CM's enter+exit on one task.
"""

import sys

import anyio
import pytest

from kohakuterrarium.mcp.client import MCPClientManager, MCPServerConfig

_SERVER_SOURCE = '''\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kt-unit-mcp")


@mcp.tool()
def ping() -> str:
    """Return pong."""
    return "pong"


if __name__ == "__main__":
    mcp.run()
'''


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "kt_unit_mcp_server.py"
    path.write_text(_SERVER_SOURCE, encoding="utf-8")
    return path


def _config(server_script) -> MCPServerConfig:
    return MCPServerConfig(
        name="srv",
        transport="stdio",
        command=sys.executable,
        args=[str(server_script)],
        connect_timeout=30,
    )


class TestCrossTaskCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_connection_from_same_task_succeeds(self, server_script):
        """Baseline: ``_cleanup_connection`` on the connect task tears
        the connection down and drops the server — confirms the
        teardown path itself is sound."""
        manager = MCPClientManager()
        await manager.connect(_config(server_script))
        assert "srv" in manager.servers
        await manager._cleanup_connection("srv", remove_server=True)
        assert "srv" not in manager.servers
        assert "srv" not in manager._sessions

    @pytest.mark.asyncio
    async def test_cleanup_connection_from_other_task_succeeds(self, server_script):
        """Regression guard for B-mcp-1 (FIXED): ``disconnect`` / the
        underlying ``_cleanup_connection`` must tear the connection down
        regardless of which task invokes it. Connecting on this task and
        cleaning up from a child task completes and drops the server —
        not ``CancelledError``. Before the fix, the transport's anyio
        context manager was entered on connect()'s task and exited on
        the cleanup task; anyio cancel scopes are task-bound. Now a
        per-connection owner task keeps the CM enter+exit on one task."""
        manager = MCPClientManager()
        await manager.connect(_config(server_script))
        assert "srv" in manager.servers

        async def _cleanup_child() -> None:
            await manager._cleanup_connection("srv", remove_server=True)

        # Child task inside an anyio task group — the structural
        # equivalent of disconnect() being called from a task other
        # than connect()'s task.
        async with anyio.create_task_group() as tg:
            tg.start_soon(_cleanup_child)

        assert "srv" not in manager.servers
