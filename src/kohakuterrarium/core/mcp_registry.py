"""Global MCP server registry shared by config assembly and the identity surface.

The registry file lives at ``config_dir() / "mcp_servers.yaml"``. Keeping the
reader here (instead of under ``studio``) lets the core config assembler merge
global servers into creature configs without crossing the core -> studio
dependency boundary.
"""

from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.utils.config_dir import config_dir


def mcp_config_path() -> Path:
    """Return the global MCP registry path for the current config dir."""
    return config_dir() / "mcp_servers.yaml"


def load_global_mcp_servers() -> list[dict[str, Any]]:
    """Load the global registry, returning an empty list on any read error."""
    path = mcp_config_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []
