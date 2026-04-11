"""MCP client manager — manages connections to multiple MCP servers."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str = "stdio"  # "stdio" or "http"
    command: str = ""  # For stdio: executable command
    args: list[str] = field(default_factory=list)  # For stdio: command arguments
    env: dict[str, str] = field(default_factory=dict)  # For stdio: env vars
    url: str = ""  # For http: server URL


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
        self._transports: dict[str, Any] = {}  # name -> (read, write) or context
        self._stdio_contexts: dict[str, Any] = {}  # name -> context manager
        self._lock = asyncio.Lock()

    @property
    def servers(self) -> dict[str, MCPServerInfo]:
        return self._servers

    async def connect(self, config: MCPServerConfig) -> MCPServerInfo:
        """Connect to an MCP server and discover its tools.

        Raises ImportError if the mcp package is not installed.
        """
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        name = config.name
        if name in self._sessions:
            logger.warning("MCP server already connected", server=name)
            return self._servers[name]

        info = MCPServerInfo(config=config, status="connecting")
        self._servers[name] = info

        try:
            if config.transport == "stdio":
                if not config.command:
                    raise ValueError(
                        f"MCP server {name}: stdio transport requires 'command'"
                    )

                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env if config.env else None,
                )

                # Enter the stdio context manager
                ctx = stdio_client(params)
                read_stream, write_stream = await ctx.__aenter__()
                self._stdio_contexts[name] = ctx

                # Create and initialize session
                session = ClientSession(read_stream, write_stream)
                await session.initialize()

                self._sessions[name] = session
                self._transports[name] = (read_stream, write_stream)

            elif config.transport == "http":
                # HTTP/SSE transport
                from mcp.client.sse import sse_client

                if not config.url:
                    raise ValueError(
                        f"MCP server {name}: http transport requires 'url'"
                    )

                ctx = sse_client(config.url)
                read_stream, write_stream = await ctx.__aenter__()
                self._stdio_contexts[name] = ctx

                session = ClientSession(read_stream, write_stream)
                await session.initialize()

                self._sessions[name] = session
                self._transports[name] = (read_stream, write_stream)
            else:
                raise ValueError(f"Unknown transport: {config.transport}")

            # Discover tools
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

        except Exception as e:
            info.status = "error"
            info.error = str(e)
            logger.error("MCP connect failed", server=name, error=str(e))
            raise

    async def disconnect(self, name: str) -> bool:
        """Disconnect from an MCP server."""
        if name not in self._servers:
            return False

        # Close session
        session = self._sessions.pop(name, None)
        if session:
            try:
                # ClientSession doesn't have a close method in all versions
                pass
            except Exception as e:
                logger.debug("Failed to close MCP session", error=str(e), exc_info=True)

        # Exit the transport context manager
        ctx = self._stdio_contexts.pop(name, None)
        if ctx:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(
                    "Failed to exit MCP transport context", error=str(e), exc_info=True
                )

        self._transports.pop(name, None)

        info = self._servers.pop(name, None)
        if info:
            info.status = "disconnected"

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

        # Convert MCP result to string
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
