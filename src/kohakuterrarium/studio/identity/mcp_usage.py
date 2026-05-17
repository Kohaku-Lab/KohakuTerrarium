"""Scan installed creature / terrarium configs for MCP server references.

Used by ``GET /api/settings/mcp/{name}/usage`` to surface "this server
is used by N creatures" on the MCP settings tab.

Synchronous + IO-bound; callers should ``to_thread`` this.
"""

from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.config_dir import config_dir

logger = get_logger(__name__)


def _candidate_roots() -> list[Path]:
    """Where to look for installed creature / terrarium configs.

    Today these all live under ``~/.kohakuterrarium/packages/`` (the
    package-manager root). The CLI's installed-package shape mirrors
    a checkout: ``packages/<pkg>/creatures/<name>/config.yaml`` etc.
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
    """Walk ``root/*/<kind>s/*/config.yaml`` and yield (path, parsed config)."""
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
                        logger.debug(
                            "mcp_usage: scan parse failed",
                            path=str(cfg_path),
                            error=str(e),
                        )
                        continue
                    if isinstance(data, dict):
                        out.append((cfg_path, data))
                    break
    return out


def _references_server(config: dict[str, Any], server_name: str) -> bool:
    """Does ``config`` declare a dependency on the named MCP server?

    Looks at the ``mcp_servers:`` top-level list (the canonical
    shape) plus a nested ``tools.mcp_servers`` fallback for older
    configs. Each entry may be a bare string or a dict with ``name``.
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
    """Return a list of ``{name, kind, path}`` refs that depend on the server.

    Sorted by ``(kind, name)`` so the UI rendering is stable across calls.
    """
    refs: list[dict[str, str]] = []
    for root in _candidate_roots():
        for kind in ("creature", "terrarium"):
            for path, config in _scan_kind(root, kind):
                if _references_server(config, server_name):
                    name = config.get("name") or path.parent.name or path.stem
                    refs.append({"name": str(name), "kind": kind, "path": str(path)})
    refs.sort(key=lambda r: (r["kind"], r["name"]))
    # Deduplicate by path — same file may appear twice if both home and
    # KT_CONFIG_DIR resolve to the same place.
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for ref in refs:
        if ref["path"] in seen:
            continue
        seen.add(ref["path"])
        deduped.append(ref)
    return deduped


__all__ = ["find_creatures_using_server"]
