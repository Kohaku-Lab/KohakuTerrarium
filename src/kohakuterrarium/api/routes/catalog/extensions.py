"""Catalog extensions — aggregated view of plugin / tool / trigger /
LLM-preset / IO modules contributed by installed packages.

CLI equivalent: ``kt extension list`` and ``kt extension info <name>``.

Pure read-only — write-side actions (install / uninstall) live on
:mod:`api.routes.catalog.packages`. This route only flattens the
package manifests so the Vue Extensions tab can render one row per
extension regardless of which package owns it.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# Manifest slot → extension-kind label. Anything not listed here is
# treated as opaque package metadata and skipped.
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
    """Manifest entries are either bare strings or ``{name, ...}`` dicts."""
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
    # Stable order: kind → package → name.
    out.sort(key=lambda e: (e.kind, e.package, e.name))
    return out


@router.get("", response_model=list[ExtensionEntry])
async def list_extensions() -> list[ExtensionEntry]:
    return await run_in_io_executor(_collect_sync)


@router.get("/{kind}/{name}", response_model=ExtensionEntry)
async def get_extension(kind: str, name: str) -> ExtensionEntry:
    """Lookup one specific extension by ``(kind, name)``.

    Returns the first match — extensions are expected to be unique
    per (kind, name) but the catalog doesn't enforce it, so this is
    "first wins" with no error on shadowing.
    """
    entries = await run_in_io_executor(_collect_sync)
    for entry in entries:
        if entry.kind == kind and entry.name == name:
            return entry
    raise HTTPException(404, f"Extension not found: {kind}/{name}")


__all__ = ["router"]
