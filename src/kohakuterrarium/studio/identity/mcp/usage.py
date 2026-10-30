"""Scan installed creature / terrarium configs for MCP server references.

Scans installed creature and terrarium configurations for references to a named
MCP server. The API exposes these results in the MCP settings view.

The scan is synchronous and I/O-bound; asynchronous callers should offload it to
a worker thread.
"""

from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.config_dir import config_dir

logger = get_logger(__name__)


def _candidate_roots() -> list[Path]:
    """Return distinct existing roots that may contain installed packages.

    Package contents mirror a checkout under ``packages/<pkg>/``, including
    ``creatures`` and ``terrariums`` directories.
    """
    roots = [
        config_dir() / "packages",
        Path.home() / ".kohakuterrarium" / "packages",
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        try:
            resolved = r.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        if resolved.exists():
            seen.add(resolved)
            out.append(r)
    return out


def _scan_kind(root: Path, kind: str) -> list[tuple[Path, dict[str, Any]]]:
    """Return valid mappings found under ``root/*/<kind>s/*`` configs."""
    subdir = f"{kind}s"
    out: list[tuple[Path, dict[str, Any]]] = []
    for pkg_dir in root.iterdir() if root.exists() else []:
        if not pkg_dir.is_dir():
            continue
        kind_dir = pkg_dir / subdir
        if not kind_dir.is_dir():
            continue
        for entry in kind_dir.iterdir():
            if not entry.is_dir():
                continue
            for cfg_name in ("config.yaml", "config.yml", "agent.yaml"):
                cfg_path = entry / cfg_name
                if cfg_path.exists():
                    try:
                        with open(cfg_path, encoding="utf-8") as f:
                            data = yaml.safe_load(f) or {}
                    except (OSError, yaml.YAMLError) as e:
                        logger.warning(
                            "mcp_usage: scan parse failed",
                            path=str(cfg_path),
                            error=str(e),
                            exc_info=True,
                        )
                        continue
                    if isinstance(data, dict):
                        out.append((cfg_path, data))
                    break
    return out


def _references_server(config: dict[str, Any], server_name: str) -> bool:
    """Return whether a config references the named MCP server.

    The canonical top-level ``mcp_servers`` list and legacy
    ``tools.mcp_servers`` list accept either names or mappings with ``name``.
    """
    candidates: list[Any] = []
    top = config.get("mcp_servers")
    if isinstance(top, list):
        candidates.extend(top)
    tools = config.get("tools")
    if isinstance(tools, dict):
        nested = tools.get("mcp_servers")
        if isinstance(nested, list):
            candidates.extend(nested)
    for entry in candidates:
        if isinstance(entry, str) and entry == server_name:
            return True
        if isinstance(entry, dict) and entry.get("name") == server_name:
            return True
    return False


def find_creatures_using_server(server_name: str) -> list[dict[str, str]]:
    """Return stable, path-deduplicated config references to an MCP server."""
    refs: list[dict[str, str]] = []
    for root in _candidate_roots():
        for kind in ("creature", "terrarium"):
            for path, config in _scan_kind(root, kind):
                if _references_server(config, server_name):
                    name = config.get("name") or path.parent.name or path.stem
                    refs.append({"name": str(name), "kind": kind, "path": str(path)})
    refs.sort(key=lambda r: (r["kind"], r["name"]))
    # A custom config directory may alias the legacy home root, so path identity
    # is the final deduplication key.
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for ref in refs:
        if ref["path"] in seen:
            continue
        seen.add(ref["path"])
        deduped.append(ref)
    return deduped


__all__ = ["find_creatures_using_server"]
