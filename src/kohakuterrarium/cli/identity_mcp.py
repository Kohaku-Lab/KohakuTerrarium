"""CLI MCP management — global registry CRUD + per-agent lister.

Folds the legacy ``cli/config_mcp.py`` and ``cli/mcp.py`` into one module
that delegates to the canonical parser
:mod:`kohakuterrarium.studio.identity.mcp_servers`.
"""

from kohakuterrarium.cli.config_prompts import prompt as _prompt
from kohakuterrarium.studio.identity.mcp_servers import (
    MCP_SERVERS_PATH,
    delete_server,
    find_server,
    load_agent_mcp_servers,
    load_servers,
    prompt_server_dict,
    upsert_server,
)


def list_cli() -> int:
    """Show the global ``~/.kohakuterrarium/mcp_servers.yaml`` registry."""
    servers = load_servers()
    print(f"MCP config file: {MCP_SERVERS_PATH}")
    if not servers:
        print("No MCP servers configured.")
        return 0
    print()
    for server in servers:
        print(f"- {server.get('name', '')}")
        print(f"  transport: {server.get('transport', 'stdio')}")
        if server.get("command"):
            print(f"  command:   {server.get('command', '')}")
        if server.get("args"):
            print(f"  args:      {server.get('args', [])}")
        if server.get("url"):
            print(f"  url:       {server.get('url', '')}")
        if server.get("connect_timeout") not in (None, ""):
            print(f"  timeout:   {server.get('connect_timeout')}s")
        if server.get("env"):
            print(f"  env keys:  {list((server.get('env') or {}).keys())}")
    return 0


def add_or_update_cli(name: str | None) -> int:
    existing = find_server(name) if name else None
    try:
        server = prompt_server_dict(existing, _prompt)
    except ValueError as e:
        print(str(e))
        return 1
    upsert_server(server)
    print(f"Saved MCP server: {server['name']}")
    return 0


def delete_cli(name: str) -> int:
    if not delete_server(name):
        print(f"MCP server not found: {name}")
        return 1
    print(f"Deleted MCP server: {name}")
    return 0


def list_for_agent_cli(agent_path: str) -> int:
    """List MCP servers configured in a specific agent's ``config.yaml``.

    Was ``cli/mcp.py:mcp_list_cli``. Routes through
    :func:`mcp_servers.load_agent_mcp_servers` so there is exactly one
    yaml-walking implementation in the codebase.
    """
    servers, config_file, error = load_agent_mcp_servers(agent_path)
    if error:
        print(f"Error: {error}")
        return 1

    if not servers:
        print(f"No MCP servers configured in {config_file}")
        return 0

    assert config_file is not None
    print(f"MCP servers in {config_file.name}:")
    print("-" * 50)
    for i, server in enumerate(servers, 1):
        if isinstance(server, dict):
            server_name = server.get("name", f"server-{i}")
            server_type = server.get("transport", server.get("type", "?"))
            command = server.get("command", "")
            url = server.get("url", "")
            print(f"  {i}. {server_name}")
            print(f"     Type: {server_type}")
            if command:
                print(f"     Command: {command}")
            if url:
                print(f"     URL: {url}")
            args = server.get("args", [])
            if args:
                print(f"     Args: {args}")
            env = server.get("env", {})
            if env:
                print(f"     Env vars: {', '.join(env.keys())}")
        else:
            print(f"  {i}. {server}")
        print()

    return 0
