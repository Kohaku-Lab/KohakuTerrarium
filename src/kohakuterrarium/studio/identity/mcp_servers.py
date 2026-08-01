"""Canonical MCP server registry — the ONE parser for ``mcp_servers.yaml``.

This module is the shared persistence and validation surface used by CLI, HTTP,
and per-agent listing adapters. The registry is a YAML list at
``config_dir() / "mcp_servers.yaml"``; missing or malformed files degrade to an
empty registry.
"""

import json
from pathlib import Path
from typing import Any, Callable

import yaml

from kohakuterrarium.core.mcp_registry import load_global_mcp_servers
from kohakuterrarium.utils.config_dir import config_dir

# Retain legacy display constants; live persistence resolves through
# :func:`mcp_config_path` so configuration-directory changes remain visible.
KT_DIR = Path.home() / ".kohakuterrarium"
MCP_SERVERS_PATH = KT_DIR / "mcp_servers.yaml"


def mcp_config_path() -> Path:
    """Return the current global MCP registry path.

    Resolving the config directory per call preserves test isolation and runtime
    changes to ``KT_CONFIG_DIR``.
    """
    return config_dir() / "mcp_servers.yaml"


def load_servers() -> list[dict[str, Any]]:
    """Load the global registry, returning an empty list on any read error."""
    return load_global_mcp_servers()


def save_servers(servers: list[dict[str, Any]]) -> None:
    """Persist the global registry, creating its parent directory as needed."""
    path = mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(servers, f, default_flow_style=False, sort_keys=False)


def upsert_server(server: dict[str, Any]) -> dict[str, Any]:
    """Add or replace a server by name and return the persisted record."""
    if not server.get("name"):
        raise ValueError("Name is required")
    servers = load_servers()
    servers = [s for s in servers if s.get("name") != server["name"]]
    servers.append(server)
    save_servers(servers)
    return server


def delete_server(name: str) -> bool:
    """Remove a server by name and report whether it existed."""
    servers = load_servers()
    filtered = [s for s in servers if s.get("name") != name]
    if len(filtered) == len(servers):
        return False
    save_servers(filtered)
    return True


def find_server(name: str) -> dict[str, Any] | None:
    for server in load_servers():
        if server.get("name") == name:
            return server
    return None


def prompt_server_dict(
    existing: dict[str, Any] | None,
    prompt: Callable[[str, str], str],
) -> dict[str, Any]:
    """Build and validate an MCP server record through a prompt callback.

    ``prompt(label, default)`` supplies each raw field. Invalid JSON, field
    shapes, names, and timeouts raise ``ValueError`` for the caller to present.
    """
    existing = existing or {}
    name = prompt("Name", existing.get("name", ""))
    transport = prompt("Transport", existing.get("transport", "stdio"))
    command = prompt("Command", existing.get("command", ""))
    args_raw = prompt(
        "Args JSON array",
        json.dumps(existing.get("args", []), ensure_ascii=False),
    )
    env_raw = prompt(
        "Env JSON object",
        json.dumps(existing.get("env", {}), ensure_ascii=False),
    )
    url = prompt("URL", existing.get("url", ""))
    timeout_raw = prompt(
        "Connect timeout (seconds)",
        (
            ""
            if existing.get("connect_timeout") in (None, "")
            else str(existing.get("connect_timeout"))
        ),
    )

    try:
        args = json.loads(args_raw) if args_raw else []
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid args JSON: {e}") from e
    if not isinstance(args, list):
        raise ValueError("Args must be a JSON array")

    try:
        env = json.loads(env_raw) if env_raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid env JSON: {e}") from e
    if not isinstance(env, dict):
        raise ValueError("Env must be a JSON object")
    if not name:
        raise ValueError("Name is required")

    if timeout_raw:
        try:
            connect_timeout: float | None = float(timeout_raw)
        except ValueError as e:
            raise ValueError(f"Invalid connect timeout: {e}") from e
    else:
        connect_timeout = None

    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "url": url,
        "connect_timeout": connect_timeout,
    }


def load_agent_mcp_servers(
    agent_path: str,
) -> tuple[list[dict[str, Any]], Path | None, str | None]:
    """Return an agent config's declared MCP servers, source path, and error.

    Successful reads return ``None`` for the error. Missing paths, missing config
    files, and decoding failures return an empty server list with a message.
    """
    path = Path(agent_path)
    if not path.exists():
        return [], None, f"Agent path not found: {agent_path}"

    config_file: Path | None = None
    for name in ("config.yaml", "config.yml"):
        candidate = path / name
        if candidate.exists():
            config_file = candidate
            break

    if config_file is None:
        return [], None, f"No config.yaml found in {agent_path}"

    try:
        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        return [], config_file, f"Error reading config: {e}"

    servers = config.get("mcp_servers", []) or []
    return list(servers), config_file, None
