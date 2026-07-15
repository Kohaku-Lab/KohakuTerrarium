"""Packages route — read-only browser for installed kt packages.

Used by the creature editor's ``base_config`` autocomplete, the
"copy template from package" flow, and the package-detail cards
shown in the Studio UI. Reuses the existing
``kohakuterrarium.packages.walk.list_packages``,
``kohakuterrarium.packages.locations.get_package_root``, and
``kohakuterrarium.packages.walk.get_package_modules`` helpers.
"""

from fastapi import APIRouter, HTTPException

from kohakuterrarium.packages.locations import get_package_root
from kohakuterrarium.packages.manifest import _load_manifest
from kohakuterrarium.packages.walk import get_package_modules
from kohakuterrarium.studio.catalog.packages import list_installed_packages

router = APIRouter()


# These manifest keys map directly to discovery endpoints. ``skills`` remains
# included so packages without that optional slot consistently return an empty list.
_EXTENSION_KINDS: tuple[str, ...] = (
    "plugins",
    "tools",
    "triggers",
    "io",
    "skills",
)


def _require_package_root(name: str):
    """Return an installed package root or raise the route's canonical 404."""
    root = get_package_root(name)
    if root is None:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"package {name!r} not installed",
            },
        )
    return root


@router.get("")
async def list_all_packages() -> list[dict]:
    """Return a summary of every installed kt package."""
    return list_installed_packages()


@router.get("/{name}")
async def get_package_summary(name: str) -> dict:
    """Return package metadata and extension counts for one Studio card."""
    root = _require_package_root(name)
    manifest = _load_manifest(root)

    def _count(key: str) -> int:
        """Count list-valued manifest entries and ignore malformed values."""
        value = manifest.get(key)
        return len(value) if isinstance(value, list) else 0

    return {
        "name": manifest.get("name", name),
        "version": manifest.get("version", "?"),
        "description": manifest.get("description", ""),
        "path": str(root),
        "creatures": _count("creatures"),
        "terrariums": _count("terrariums"),
        "plugins": _count("plugins"),
        "tools": _count("tools"),
        "triggers": _count("triggers"),
        "io": _count("io"),
        "skills": _count("skills"),
        "has_python_dependencies": bool(manifest.get("python_dependencies"))
        or (root / "requirements.txt").exists(),
    }


@router.get("/{name}/creatures")
async def list_package_creatures(name: str) -> list[dict]:
    """List config-backed creatures exported by an installed package."""
    root = _require_package_root(name)
    results: list[dict] = []
    creatures_dir = root / "creatures"
    if not creatures_dir.is_dir():
        return results
    for child in sorted(creatures_dir.iterdir()):
        if not child.is_dir():
            continue
        cfg = child / "config.yaml"
        if not cfg.exists():
            cfg = child / "config.yml"
        if not cfg.exists():
            continue
        results.append(
            {
                "name": child.name,
                "ref": f"@{name}/creatures/{child.name}",
            }
        )
    return results


@router.get("/{name}/modules/{kind}")
async def list_package_modules(name: str, kind: str) -> list[dict]:
    """List file-backed modules of a requested package kind."""
    root = _require_package_root(name)
    kind_dir = root / "modules" / kind
    if not kind_dir.is_dir():
        return []
    results: list[dict] = []
    for child in sorted(kind_dir.iterdir()):
        if child.is_file() and child.suffix in (".py", ".yaml", ".yml"):
            results.append(
                {
                    "name": child.stem,
                    "ref": f"@{name}/modules/{kind}/{child.name}",
                }
            )
    return results


def _normalize_extension_entry(kind: str, entry: object) -> dict:
    """Normalize hand-authored extension entries for Studio consumers.

    Legacy string entries and ``class_name`` aliases are accepted, while unknown
    keys remain available for advanced package details.
    """
    if isinstance(entry, str):
        return {"name": entry, "module": None, "class": None, "description": ""}
    if not isinstance(entry, dict):
        return {"name": str(entry), "module": None, "class": None, "description": ""}

    normalized: dict = dict(entry)
    if "class" not in normalized and "class_name" in normalized:
        normalized["class"] = normalized["class_name"]
    normalized.setdefault("name", "")
    normalized.setdefault("description", "")
    if kind == "skills":
        normalized.setdefault("path", normalized.get("path") or "")
    else:
        normalized.setdefault("module", None)
        normalized.setdefault("class", None)
    return normalized


async def _list_extension(name: str, kind: str) -> list[dict]:
    """Return normalized manifest entries for one extension kind."""
    # Missing packages must remain distinguishable from installed packages that
    # declare no extensions of the requested kind.
    _require_package_root(name)
    entries = get_package_modules(name, kind)
    if not isinstance(entries, list):
        return []
    return [_normalize_extension_entry(kind, e) for e in entries]


@router.get("/{name}/plugins")
async def list_package_plugins(name: str) -> list[dict]:
    """Plugins declared in the package's ``kohaku.yaml``."""
    return await _list_extension(name, "plugins")


@router.get("/{name}/tools")
async def list_package_tools(name: str) -> list[dict]:
    """Tools declared in the package's ``kohaku.yaml``."""
    return await _list_extension(name, "tools")


@router.get("/{name}/triggers")
async def list_package_triggers(name: str) -> list[dict]:
    """Triggers declared in the package's ``kohaku.yaml``."""
    return await _list_extension(name, "triggers")


@router.get("/{name}/io")
async def list_package_io(name: str) -> list[dict]:
    """Input / output modules declared in the package's ``kohaku.yaml``."""
    return await _list_extension(name, "io")


@router.get("/{name}/skills")
async def list_package_skills(name: str) -> list[dict]:
    """Skills declared in the package's optional ``kohaku.yaml`` slot."""
    return await _list_extension(name, "skills")
