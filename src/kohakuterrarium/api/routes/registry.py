"""Registry routes - browse local configs and install/uninstall packages via git."""

import json
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.packages import (
    install_package,
    list_packages,
    uninstall_package,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Path to the bundled registry.json
_REGISTRY_JSON = Path(__file__).resolve().parent.parent.parent / "registry.json"


class InstallRequest(BaseModel):
    """Request body for installing a package."""

    url: str
    name: str | None = None


class UninstallRequest(BaseModel):
    """Request body for uninstalling a package."""

    name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_creature_detail(config_dir: Path) -> dict | None:
    """Parse a creature config directory and return detail dict, or None on failure."""
    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        config_file = config_dir / "config.yml"
    if not config_file.exists():
        return None

    try:
        cfg = load_agent_config(config_dir)
        tools_list = [t.name for t in cfg.tools]
        return {
            "name": cfg.name,
            "type": "creature",
            "description": getattr(cfg, "system_prompt", "")[:200],
            "model": cfg.model,
            "tools": tools_list,
            "path": str(config_dir),
        }
    except Exception as e:
        # Fallback: parse raw YAML for basic info
        _ = e  # full config parse failed, try raw YAML
        try:
            data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            return {
                "name": data.get("name", config_dir.name),
                "type": "creature",
                "description": data.get("description", ""),
                "model": data.get("model", data.get("controller", {}).get("model", "")),
                "tools": [
                    t.get("name", "")
                    for t in data.get("tools", [])
                    if isinstance(t, dict)
                ],
                "path": str(config_dir),
            }
        except Exception as e:
            _ = e  # config unreadable, skip
            return None


def _parse_terrarium_detail(config_dir: Path) -> dict | None:
    """Parse a terrarium config directory and return detail dict, or None on failure."""
    config_file = config_dir / "terrarium.yaml"
    if not config_file.exists():
        config_file = config_dir / "terrarium.yml"
    if not config_file.exists():
        return None

    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        terrarium = data.get("terrarium", data)
        creatures = terrarium.get("creatures", [])
        creature_names = [c.get("name", "") for c in creatures if isinstance(c, dict)]
        return {
            "name": terrarium.get("name", config_dir.name),
            "type": "terrarium",
            "description": terrarium.get("description", ""),
            "model": "",
            "tools": [],
            "creatures": creature_names,
            "path": str(config_dir),
        }
    except Exception as e:
        _ = e  # config unreadable, skip
        return None


def _scan_all_configs() -> list[dict]:
    """Scan all known creatures and terrariums directories for configs.

    Uses list_packages() which handles both direct dirs and .link (editable) installs.
    Also scans local project directories (cwd/creatures, cwd/terrariums).
    """
    results: list[dict] = []
    seen_paths: set[str] = set()

    def _add_creature(config_dir: Path, source: str = "") -> None:
        key = str(config_dir.resolve())
        if key in seen_paths:
            return
        seen_paths.add(key)
        detail = _parse_creature_detail(config_dir)
        if detail:
            detail["source"] = source
            results.append(detail)

    def _add_terrarium(config_dir: Path, source: str = "") -> None:
        key = str(config_dir.resolve())
        if key in seen_paths:
            return
        seen_paths.add(key)
        detail = _parse_terrarium_detail(config_dir)
        if detail:
            detail["source"] = source
            results.append(detail)

    # Scan installed packages (handles .link editable installs)
    for pkg in list_packages():
        pkg_path = Path(pkg["path"])
        pkg_name = pkg["name"]
        for c in pkg.get("creatures", []):
            _add_creature(pkg_path / c["path"], source=pkg_name)
        for t in pkg.get("terrariums", []):
            _add_terrarium(pkg_path / t["path"], source=pkg_name)

    # Scan local project directories
    cwd = Path.cwd()
    for creatures_dir in [cwd / "creatures"]:
        if creatures_dir.is_dir():
            for child in sorted(creatures_dir.iterdir()):
                if child.is_dir():
                    _add_creature(child, source="local")

    for terrariums_dir in [cwd / "terrariums"]:
        if terrariums_dir.is_dir():
            for child in sorted(terrariums_dir.iterdir()):
                if child.is_dir():
                    _add_terrarium(child, source="local")

    # Filter out entries with empty names (broken configs)
    return [r for r in results if r.get("name")]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_local():
    """List all locally available creature and terrarium configs with details."""
    return _scan_all_configs()


@router.get("/remote")
async def list_remote():
    """List known remote repos from the bundled registry.json."""
    if not _REGISTRY_JSON.exists():
        return {"repos": []}
    try:
        data = json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        logger.warning("Failed to read registry.json", error=str(e))
        return {"repos": []}


@router.post("/install")
async def install(req: InstallRequest):
    """Install a package from a git URL."""
    try:
        name = install_package(source=req.url, name_override=req.name)
        return {"status": "installed", "name": name}
    except Exception as e:
        logger.error("Install failed", url=req.url, error=str(e))
        raise HTTPException(400, f"Install failed: {e}")


@router.post("/uninstall")
async def uninstall(req: UninstallRequest):
    """Uninstall a package by name."""
    removed = uninstall_package(req.name)
    if not removed:
        raise HTTPException(404, f"Package not found: {req.name}")
    return {"status": "uninstalled", "name": req.name}
