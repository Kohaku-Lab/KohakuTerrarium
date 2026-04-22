"""Packages route — read-only browser for installed kt packages.

Used by the creature editor's ``base_config`` autocomplete and
the "copy template from package" flow. Reuses the existing
``kohakuterrarium.packages.list_packages`` + ``_get_package_root``.
"""

from fastapi import APIRouter, HTTPException

from kohakuterrarium.packages import _get_package_root, list_packages

router = APIRouter()


@router.get("")
async def list_all_packages() -> list[dict]:
    """Return a summary of every installed kt package."""
    return list_packages()


@router.get("/{name}/creatures")
async def list_package_creatures(name: str) -> list[dict]:
    root = _get_package_root(name)
    if root is None:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"package {name!r} not installed",
            },
        )
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
    root = _get_package_root(name)
    if root is None:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"package {name!r} not installed",
            },
        )
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
