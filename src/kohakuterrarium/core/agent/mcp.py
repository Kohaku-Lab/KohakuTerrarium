"""Initialize MCP connections and expose their tools to agents."""

from typing import Any

from kohakuterrarium.mcp.client import MCPClientManager, MCPServerConfig
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MCP_CONNECT_TIMEOUT = 20


async def init_mcp(agent: Any) -> None:
    """Initialize the MCP manager and connect configured servers.

    The manager exists even without configured servers so the ``mcp_connect``
    runtime tool can establish connections later.
    """
    agent._mcp_manager = MCPClientManager()
    mcp_configs = agent.config.mcp_servers
    if not mcp_configs:
        return

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
    servers = agent._mcp_manager.list_servers() if agent._mcp_manager else []
    servers = [server for server in servers if server["status"] == "connected"]
    if not servers:
        if "mcp" in getattr(agent, "_managed_prompt_sections", {}):
            agent.set_system_prompt_section("mcp", "")
        return

    lines = ["\n## Available MCP Tools\n"]
    lines.append(
        "Call these with: mcp_call(server=<server>, tool=<tool>, args={...})\n"
    )

    for srv in servers:
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

    mcp_section = "\n".join(lines)
    agent.set_system_prompt_section("mcp", mcp_section)
    logger.info(
        "MCP tools injected into prompt",
        servers=len(servers),
        tools=sum(len(s["tools"]) for s in servers),
    )
