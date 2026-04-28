"""Canonical MCP server registry — the ONE parser for ``mcp_servers.yaml``.

P2 from the studio-cleanup investigation: there used to be three
independent readers (``routes/settings.py``, ``cli/config_mcp.py``,
``cli/mcp.py``) for the same on-disk file. This module is now the only
home for that read/write logic. CLI, HTTP, and the per-agent lister all
delegate here.

File location: ``~/.kohakuterrarium/mcp_servers.yaml``. Format: a YAML
list of server dicts. Tolerant: malformed files / missing files
collapse to ``[]``.
"""

import json
from pathlib import Path
from typing import Any, Callable

import yaml

KT_DIR = Path.home() / ".kohakuterrarium"
MCP_SERVERS_PATH = KT_DIR / "mcp_servers.yaml"


def mcp_config_path() -> Path:
    """Return the canonical path to ``~/.kohakuterrarium/mcp_servers.yaml``."""
    return MCP_SERVERS_PATH


def load_servers() -> list[dict[str, Any]]:
    """Load the global registry. Tolerant — returns ``[]`` on any error."""
    path = mcp_config_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_servers(servers: list[dict[str, Any]]) -> None:
    """Write the global registry, creating parent dirs as needed."""
    path = mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(servers, f, default_flow_style=False, sort_keys=False)


def upsert_server(server: dict[str, Any]) -> dict[str, Any]:
    """Add or replace ``server`` (matched by ``name``). Returns the saved dict."""
    if not server.get("name"):
        raise ValueError("Name is required")
    servers = load_servers()
    servers = [s for s in servers if s.get("name") != server["name"]]
    servers.append(server)
    save_servers(servers)
    return server


def delete_server(name: str) -> bool:
    """Remove a server by name. Returns False if not found."""
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


# CLI prompt helpers ---------------------------------------------------


def prompt_server_dict(
    existing: dict[str, Any] | None,
    prompt: Callable[[str, str], str],
) -> dict[str, Any]:
    """Interactively build an MCP server dict, validating each field.

    ``prompt(label, default)`` is the existing
    :func:`kohakuterrarium.cli.config_prompts.prompt` callable.
    Raises ``ValueError`` on bad input — the caller should catch and
    surface the message.
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


# Per-agent MCP lister (was ``cli/mcp.py:mcp_list_cli``) ---------------


def load_agent_mcp_servers(
    agent_path: str,
) -> tuple[list[dict[str, Any]], Path | None, str | None]:
    """Read an agent's ``config.yaml`` and return its declared MCP servers.

    Returns ``(servers, config_file, error_message)``. On success
    ``error_message`` is ``None`` and ``config_file`` is the path that
    was read.
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
