import json
from typing import Any

from kohakuterrarium.api.routes.settings import _load_mcp_config, _save_mcp_config


def prompt_mcp(existing: dict[str, Any] | None, prompt) -> dict[str, Any]:
    existing = existing or {}
    name = prompt("Name", existing.get("name", ""))
    transport = prompt("Transport", existing.get("transport", "stdio"))
    command = prompt("Command", existing.get("command", ""))
    args_raw = prompt(
        "Args JSON array", json.dumps(existing.get("args", []), ensure_ascii=False)
    )
    env_raw = prompt(
        "Env JSON object", json.dumps(existing.get("env", {}), ensure_ascii=False)
    )
    url = prompt("URL", existing.get("url", ""))

    try:
        args = json.loads(args_raw) if args_raw else []
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid args JSON: {e}")
    if not isinstance(args, list):
        raise ValueError("Args must be a JSON array")

    try:
        env = json.loads(env_raw) if env_raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid env JSON: {e}")
    if not isinstance(env, dict):
        raise ValueError("Env must be a JSON object")
    if not name:
        raise ValueError("Name is required")

    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "url": url,
    }


def list_mcp(config_paths: dict[str, Any]) -> int:
    servers = _load_mcp_config()
    path = config_paths["mcp_servers"]
    print(f"MCP config file: {path}")
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
        if server.get("env"):
            print(f"  env keys:  {list((server.get('env') or {}).keys())}")
    return 0


def add_or_update_mcp(name: str | None, prompt) -> int:
    servers = _load_mcp_config()
    existing = None
    if name:
        for server in servers:
            if server.get("name") == name:
                existing = server
                break
    try:
        server = prompt_mcp(existing, prompt)
    except ValueError as e:
        print(str(e))
        return 1
    servers = [s for s in servers if s.get("name") != server["name"]]
    servers.append(server)
    _save_mcp_config(servers)
    print(f"Saved MCP server: {server['name']}")
    return 0


def delete_mcp(name: str) -> int:
    servers = _load_mcp_config()
    filtered = [s for s in servers if s.get("name") != name]
    if len(filtered) == len(servers):
        print(f"MCP server not found: {name}")
        return 1
    _save_mcp_config(filtered)
    print(f"Deleted MCP server: {name}")
    return 0
