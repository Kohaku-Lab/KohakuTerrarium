"""MCP initialization and prompt wiring helpers for Agent.

Extracted from ``core/agent.py`` to keep the main Agent module under the
file-size cap while keeping MCP-specific startup logic in one place.
"""

from typing import Any

from kohakuterrarium.mcp.client import MCPClientManager, MCPServerConfig
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MCP_CONNECT_TIMEOUT = 20


async def init_mcp(agent: Any) -> None:
    """Initialize MCP client manager and connect configured servers."""
    mcp_configs = agent.config.mcp_servers
    if not mcp_configs:
        agent._mcp_manager = None
        return

    agent._mcp_manager = MCPClientManager()

    for srv_data in mcp_configs:
        if not isinstance(srv_data, dict):
            continue
        try:
            config = MCPServerConfig(
                name=srv_data.get("name", ""),
                transport=srv_data.get("transport", "stdio"),
                command=srv_data.get("command", ""),
                args=srv_data.get("args", []),
                env=srv_data.get("env", {}),
                url=srv_data.get("url", ""),
                connect_timeout=srv_data.get("connect_timeout", None),
            )
            if config.name:
                logger.info(
                    "MCP connecting",
                    server=config.name,
                    transport=config.transport,
                    timeout_seconds=(
                        config.connect_timeout or _DEFAULT_MCP_CONNECT_TIMEOUT
                    ),
                )
                info = await agent._mcp_manager.connect(config)
                logger.info(
                    "MCP connected",
                    server=config.name,
                    transport=config.transport,
                    tools=len(info.tools),
                )
        except Exception as e:
            server_name = srv_data.get("name", "") if isinstance(srv_data, dict) else ""
            if agent._mcp_manager and server_name:
                try:
                    await agent._mcp_manager.disconnect(server_name)
                except Exception:
                    pass
            logger.warning(
                "Failed to connect MCP server",
                server=server_name,
                error=str(e),
            )


def inject_mcp_tools_into_prompt(agent: Any) -> None:
    """Inject available MCP tool descriptions into the system prompt."""
    if not agent._mcp_manager:
        return
    servers = agent._mcp_manager.list_servers()
    if not servers:
        return

    lines = ["\n## Available MCP Tools\n"]
    lines.append(
        "Call these with: mcp_call(server=<server>, tool=<tool>, args={...})\n"
    )

    for srv in servers:
        if srv["status"] != "connected":
            continue
        lines.append(f"### Server: {srv['name']}")
        for tool in srv["tools"]:
            desc = f" — {tool['description']}" if tool.get("description") else ""
            lines.append(f"- **{tool['name']}**{desc}")
            schema = tool.get("input_schema", {})
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                req = " (required)" if pname in required else ""
                param_line = f"  - `{pname}`: {ptype}{req}"
                if pdesc:
                    param_line += f" — {pdesc}"
                lines.append(param_line)
        lines.append("")

    if len(lines) <= 2:
        return

    mcp_section = "\n".join(lines)
    agent.update_system_prompt(mcp_section)
    logger.info(
        "MCP tools injected into prompt",
        servers=len(servers),
        tools=sum(len(s["tools"]) for s in servers),
    )
