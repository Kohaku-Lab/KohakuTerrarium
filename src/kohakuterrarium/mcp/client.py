"""MCP client manager — manages connections to multiple MCP servers."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MCP_CONNECT_TIMEOUT = 20.0


def normalize_mcp_transport(transport: str) -> str:
    """Normalize user-facing transport names to runtime transport kinds."""
    raw = (transport or "stdio").strip().lower().replace("-", "_")
    if raw == "stdio":
        return "stdio"
    if raw in {"http", "sse"}:
        return "sse"
    if raw in {"streamable_http", "streamablehttp", "http_streamable"}:
        return "streamable_http"
    raise ValueError(f"Unknown transport: {transport}")


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str = "stdio"  # stdio / sse / streamable_http
    command: str = ""  # For stdio: executable command
    args: list[str] = field(default_factory=list)  # For stdio: command arguments
    env: dict[str, str] = field(default_factory=dict)  # For stdio: env vars
    url: str = ""  # For sse / streamable_http: server URL
    connect_timeout: float | None = None  # Optional per-server connection timeout


@dataclass
class MCPServerInfo:
    """Runtime info about a connected MCP server."""

    config: MCPServerConfig
    tools: list[dict[str, Any]] = field(default_factory=list)
    status: str = "disconnected"  # "connected", "disconnected", "error"
    error: str = ""


class MCPClientManager:
    """Manages connections to multiple MCP servers.

    Each server gets its own ClientSession. Tools are discovered on connect
    and cached. The manager routes tool calls to the correct session.
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerInfo] = {}
        self._sessions: dict[str, Any] = {}  # name -> ClientSession
        self._transports: dict[str, Any] = {}  # name -> (read, write)
        self._stdio_contexts: dict[str, Any] = {}  # name -> transport context manager
        self._lock = asyncio.Lock()

    @property
    def servers(self) -> dict[str, MCPServerInfo]:
        return self._servers

    async def connect(self, config: MCPServerConfig) -> MCPServerInfo:
        """Connect to an MCP server and discover its tools.

        Raises ImportError if the mcp package is not installed.
        """
        timeout = config.connect_timeout
        if timeout is not None and timeout <= 0:
            raise ValueError("connect_timeout must be greater than 0")

        if timeout is None:
            return await self._connect_impl(config)

        try:
            return await asyncio.wait_for(self._connect_impl(config), timeout=timeout)
        except asyncio.TimeoutError as e:
            message = f"Timed out after {timeout:g}s"
            info = self._servers.get(config.name)
            if info is None:
                info = MCPServerInfo(config=config)
                self._servers[config.name] = info
            info.status = "error"
            info.error = message
            logger.error(
                "MCP connect timed out",
                server=config.name,
                transport=config.transport,
                timeout_seconds=timeout,
            )
            raise TimeoutError(f"MCP server {config.name}: {message}") from e

    async def _connect_impl(self, config: MCPServerConfig) -> MCPServerInfo:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        name = config.name
        if name in self._sessions:
            logger.warning("MCP server already connected", server=name)
            return self._servers[name]

        info = MCPServerInfo(config=config, status="connecting")
        self._servers[name] = info
        transport = normalize_mcp_transport(config.transport)

        try:
            if transport == "stdio":
                if not config.command:
                    raise ValueError(
                        f"MCP server {name}: stdio transport requires 'command'"
                    )

                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env if config.env else None,
                )
                ctx = stdio_client(params)
            elif transport == "sse":
                from mcp.client.sse import sse_client

                if not config.url:
                    raise ValueError(f"MCP server {name}: SSE transport requires 'url'")
                ctx = sse_client(config.url)
            else:
                from mcp.client.streamable_http import streamablehttp_client

                if not config.url:
                    raise ValueError(
                        f"MCP server {name}: streamable_http transport requires 'url'"
                    )
                ctx = streamablehttp_client(config.url)

            session = await self._open_transport_session(name, ctx, ClientSession)

            tools_response = await session.list_tools()
            info.tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema if hasattr(t, "inputSchema") else {},
                }
                for t in tools_response.tools
            ]
            info.status = "connected"

            logger.info(
                "MCP server connected",
                server=name,
                transport=config.transport,
                tools=len(info.tools),
            )
            return info
        except asyncio.CancelledError:
            await self._cleanup_connection(name, remove_server=False)
            raise
        except Exception as e:
            info.status = "error"
            info.error = str(e)
            await self._cleanup_connection(name, remove_server=False)
            logger.error("MCP connect failed", server=name, error=str(e))
            raise

    async def _open_transport_session(
        self, name: str, ctx: Any, client_session_cls: Any
    ) -> Any:
        entered = await ctx.__aenter__()
        try:
            read_stream = entered[0]
            write_stream = entered[1]
        except (IndexError, TypeError) as e:
            raise ValueError(
                f"MCP server {name}: transport did not yield read/write streams"
            ) from e

        self._stdio_contexts[name] = ctx
        session = client_session_cls(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()

        self._sessions[name] = session
        self._transports[name] = (read_stream, write_stream)
        return session

    async def _cleanup_connection(self, name: str, *, remove_server: bool) -> None:
        session = self._sessions.pop(name, None)
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("Failed to close MCP session", error=str(e), exc_info=True)

        ctx = self._stdio_contexts.pop(name, None)
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(
                    "Failed to exit MCP transport context", error=str(e), exc_info=True
                )

        self._transports.pop(name, None)

        if remove_server:
            info = self._servers.pop(name, None)
            if info is not None:
                info.status = "disconnected"

    async def disconnect(self, name: str) -> bool:
        """Disconnect from an MCP server."""
        if name not in self._servers:
            return False

        await self._cleanup_connection(name, remove_server=True)
        logger.info("MCP server disconnected", server=name)
        return True

    async def call_tool(
        self, server_name: str, tool_name: str, args: dict[str, Any]
    ) -> str:
        """Call a tool on a specific MCP server.

        Returns the tool result as a string.
        """
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"MCP server not connected: {server_name}")

        info = self._servers.get(server_name)
        if info:
            tool_names = [t["name"] for t in info.tools]
            if tool_name not in tool_names:
                raise ValueError(
                    f"Tool '{tool_name}' not found on server '{server_name}'. "
                    f"Available: {', '.join(tool_names)}"
                )

        result = await session.call_tool(tool_name, arguments=args)

        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            elif hasattr(content, "data"):
                parts.append(f"[binary data: {len(content.data)} bytes]")
            else:
                parts.append(str(content))

        output = "\n".join(parts) if parts else "(no output)"

        if result.isError:
            return f"[MCP Error] {output}"

        return output

    def list_servers(self) -> list[dict[str, Any]]:
        """List all servers with their tools."""
        result = []
        for name, info in self._servers.items():
            result.append(
                {
                    "name": name,
                    "transport": info.config.transport,
                    "status": info.status,
                    "error": info.error,
                    "tools": info.tools,
                }
            )
        return result

    def get_server_tools(self, server_name: str) -> list[dict[str, Any]]:
        """Get detailed tool info for a specific server."""
        info = self._servers.get(server_name)
        if not info:
            raise ValueError(f"MCP server not found: {server_name}")
        return info.tools

    async def shutdown(self) -> None:
        """Disconnect all servers."""
        names = list(self._servers.keys())
        for name in names:
            try:
                await self.disconnect(name)
            except Exception as e:
                logger.warning(
                    "Error disconnecting MCP server", server=name, error=str(e)
                )
