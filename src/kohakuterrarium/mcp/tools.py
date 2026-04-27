"""MCP meta-tools: mcp_list, mcp_call, mcp_connect, mcp_disconnect.

These tools let agents interact with external MCP servers without
injecting MCP tools directly into the agent's tool list.
"""

import json
from typing import Any

from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.mcp.client import MCPClientManager, MCPServerConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _get_mcp_manager(context: Any) -> MCPClientManager:
    """Extract MCP manager from tool context."""
    if context and hasattr(context, "agent") and context.agent:
        mgr = getattr(context.agent, "_mcp_manager", None)
        if mgr:
            return mgr
    raise RuntimeError(
        "MCP is not available. No MCP manager found on the agent. "
        "Configure mcp_servers in creature config to enable MCP."
    )


@register_builtin("mcp_list")
class MCPListTool(BaseTool):
    """List connected MCP servers and their available tools."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "mcp_list"

    @property
    def description(self) -> str:
        return "List MCP servers and their tools (use before mcp_call)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Optional MCP server name for detailed tool info",
                }
            },
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        try:
            mgr = _get_mcp_manager(context)
        except RuntimeError as e:
            return ToolResult(error=str(e))

        server_name = args.get("server", "")
        servers = mgr.list_servers()

        if not servers:
            return ToolResult(
                output="No MCP servers connected. Use mcp_connect to add one.",
                exit_code=0,
            )

        if server_name:
            # Detailed view for one server
            try:
                tools = mgr.get_server_tools(server_name)
            except ValueError as e:
                return ToolResult(error=str(e))

            lines = [f"MCP server: {server_name}", f"Tools ({len(tools)}):", ""]
            for t in tools:
                lines.append(f"  {t['name']}")
                if t.get("description"):
                    lines.append(f"    {t['description']}")
                schema = t.get("input_schema", {})
                props = schema.get("properties", {})
                if props:
                    for pname, pinfo in props.items():
                        ptype = pinfo.get("type", "any")
                        pdesc = pinfo.get("description", "")
                        required = pname in schema.get("required", [])
                        req_mark = " (required)" if required else ""
                        lines.append(f"    - {pname}: {ptype}{req_mark}")
                        if pdesc:
                            lines.append(f"      {pdesc}")
                lines.append("")
            return ToolResult(output="\n".join(lines), exit_code=0)

        # Overview of all servers
        lines = ["Connected MCP servers:", ""]
        for s in servers:
            status = s["status"]
            tool_count = len(s["tools"])
            lines.append(
                f"  {s['name']} ({s['transport']}, {status}, {tool_count} tools)"
            )
            if s["error"]:
                lines.append(f"    Error: {s['error']}")
            for t in s["tools"]:
                desc = f" — {t['description']}" if t.get("description") else ""
                lines.append(f"    - {t['name']}{desc}")
            lines.append("")
        return ToolResult(output="\n".join(lines), exit_code=0)


@register_builtin("mcp_call")
class MCPCallTool(BaseTool):
    """Call a tool on a connected MCP server."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "mcp_call"

    @property
    def description(self) -> str:
        return (
            "Call a tool on an MCP server (use mcp_list first to see available tools)"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "MCP server name (see mcp_list)",
                },
                "tool": {
                    "type": "string",
                    "description": "Tool name on that MCP server",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments to pass to the MCP tool",
                },
            },
            "required": ["server", "tool"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        try:
            mgr = _get_mcp_manager(context)
        except RuntimeError as e:
            return ToolResult(error=str(e))

        server = args.get("server", "")
        tool = args.get("tool", "")
        tool_args_raw = args.get("args", {})

        if not server:
            return ToolResult(
                error="Missing 'server' argument. Specify which MCP server to call."
            )
        if not tool:
            return ToolResult(
                error="Missing 'tool' argument. Specify which tool to call."
            )

        # Parse args if string
        if isinstance(tool_args_raw, str):
            try:
                tool_args = json.loads(tool_args_raw)
            except json.JSONDecodeError:
                return ToolResult(error=f"Invalid JSON in 'args': {tool_args_raw}")
        else:
            tool_args = tool_args_raw

        try:
            result = await mgr.call_tool(server, tool, tool_args)
            return ToolResult(output=result, exit_code=0)
        except ValueError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            logger.error("MCP call failed", server=server, tool=tool, error=str(e))
            return ToolResult(error=f"MCP call failed: {e}")


@register_builtin("mcp_connect")
class MCPConnectTool(BaseTool):
    """Connect to a new MCP server at runtime."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "mcp_connect"

    @property
    def description(self) -> str:
        return "Connect to an MCP server (stdio, SSE, or streamable HTTP)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for this MCP server"},
                "transport": {
                    "type": "string",
                    "description": "Transport: stdio, http/sse, or streamable_http",
                },
                "command": {
                    "type": "string",
                    "description": "Executable command for stdio transport",
                },
                "args": {
                    "type": "array",
                    "description": "Command arguments for stdio transport",
                    "items": {"type": "string"},
                },
                "url": {
                    "type": "string",
                    "description": "URL for SSE or streamable_http transport",
                },
                "env": {
                    "type": "object",
                    "description": "Environment variables for stdio transport",
                    "additionalProperties": {"type": "string"},
                },
                "connect_timeout": {
                    "type": "number",
                    "description": "Optional timeout in seconds for the MCP connection",
                },
            },
            "required": ["name"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        try:
            mgr = _get_mcp_manager(context)
        except RuntimeError as e:
            return ToolResult(error=str(e))

        name = args.get("name", "")
        transport = args.get("transport", "")
        command = args.get("command", "")
        cmd_args = args.get("args", [])
        url = args.get("url", "")
        env = args.get("env", {})
        connect_timeout = args.get("connect_timeout", None)

        if not name:
            return ToolResult(error="Missing 'name' argument. Give this server a name.")
        if not command and not url:
            return ToolResult(
                error="Provide either 'command' (for stdio) or 'url' (for HTTP transports)."
            )

        if isinstance(cmd_args, str):
            cmd_args = cmd_args.split()

        if transport:
            resolved_transport = str(transport)
        elif command:
            resolved_transport = "stdio"
        else:
            resolved_transport = "streamable_http"

        try:
            timeout_value = (
                float(connect_timeout) if connect_timeout not in (None, "") else None
            )
        except (TypeError, ValueError):
            return ToolResult(error="'connect_timeout' must be a number if provided.")

        config = MCPServerConfig(
            name=name,
            transport=resolved_transport,
            command=command,
            args=cmd_args,
            env=env,
            url=url,
            connect_timeout=timeout_value,
        )

        try:
            info = await mgr.connect(config)
            tool_count = len(info.tools)
            tool_names = [t["name"] for t in info.tools[:10]]
            summary = ", ".join(tool_names)
            if len(info.tools) > 10:
                summary += f", ... ({tool_count - 10} more)"
            return ToolResult(
                output=f"Connected to {name} ({tool_count} tools available): {summary}",
                exit_code=0,
            )
        except ImportError:
            return ToolResult(
                error="MCP SDK not installed. Install with: pip install mcp"
            )
        except Exception as e:
            return ToolResult(error=f"Failed to connect to {name}: {e}")


@register_builtin("mcp_disconnect")
class MCPDisconnectTool(BaseTool):
    """Disconnect from an MCP server."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "mcp_disconnect"

    @property
    def description(self) -> str:
        return "Disconnect from an MCP server"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Name of the MCP server to disconnect",
                },
                "name": {
                    "type": "string",
                    "description": "Alias for server name",
                },
            },
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        try:
            mgr = _get_mcp_manager(context)
        except RuntimeError as e:
            return ToolResult(error=str(e))

        name = args.get("server", args.get("name", ""))
        if not name:
            return ToolResult(error="Missing 'server' or 'name' argument.")

        if await mgr.disconnect(name):
            return ToolResult(output=f"Disconnected from {name}", exit_code=0)
        return ToolResult(error=f"Server not found: {name}")
