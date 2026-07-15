"""Flatten package-contributed extensions into a read-only catalog view.

Package installation and removal remain on the package routes; this module only
projects manifest entries into extension records.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# Only declared extension slots are projected; other manifest keys remain metadata.
_EXTENSION_SLOTS: dict[str, str] = {
    "plugins": "plugin",
    "tools": "tool",
    "triggers": "trigger",
    "io": "io",
    "llm_presets": "llm-preset",
    "skills": "skill",
    "commands": "command",
    "user_commands": "user-command",
    "prompts": "prompt",
    "drive_registrations": "drive-registration",
}


ExtensionKind = Literal[
    "plugin",
    "tool",
    "trigger",
    "io",
    "llm-preset",
    "skill",
    "command",
    "user-command",
    "prompt",
    "drive-registration",
]


class ExtensionEntry(BaseModel):
    name: str
    kind: ExtensionKind
    package: str
    package_version: str
    description: str = ""
    module: str | None = None
    editable: bool = False


def _entry_name(item: object) -> str:
    """Extract the identifier from supported string or mapping entries."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name") or item.get("id") or "")
    return ""


def _entry_module(item: object) -> str | None:
    if isinstance(item, dict):
        m = item.get("module")
        if isinstance(m, str):
            return m
    return None


def _entry_description(item: object) -> str:
    if isinstance(item, dict):
        d = item.get("description")
        if isinstance(d, str):
            return d
    return ""


def _collect_sync() -> list[ExtensionEntry]:
    out: list[ExtensionEntry] = []
    for pkg in list_packages():
        for slot, kind in _EXTENSION_SLOTS.items():
            for item in pkg.get(slot) or []:
                name = _entry_name(item)
                if not name:
                    continue
                out.append(
                    ExtensionEntry(
                        name=name,
                        kind=kind,  # type: ignore[arg-type]
                        package=pkg.get("name", ""),
                        package_version=str(pkg.get("version") or "?"),
                        description=_entry_description(item),
                        module=_entry_module(item),
                        editable=bool(pkg.get("editable")),
                    )
                )
    # Deterministic ordering keeps API results and UI rows stable.
    out.sort(key=lambda e: (e.kind, e.package, e.name))
    return out


@router.get("", response_model=list[ExtensionEntry])
async def list_extensions() -> list[ExtensionEntry]:
    return await run_in_io_executor(_collect_sync)


@router.get("/{kind}/{name}", response_model=ExtensionEntry)
async def get_extension(kind: str, name: str) -> ExtensionEntry:
    """Return the first extension matching ``(kind, name)``.

    The catalog does not enforce uniqueness, so earlier sorted entries shadow later
    matches.
    """
    entries = await run_in_io_executor(_collect_sync)
    for entry in entries:
        if entry.kind == kind and entry.name == name:
            return entry
    raise HTTPException(404, f"Extension not found: {kind}/{name}")


__all__ = ["router"]
