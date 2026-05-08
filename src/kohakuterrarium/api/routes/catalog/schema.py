"""Schema route — returns the param list for a module entry.

Built-in schemas are pure-Python introspection (cheap). Custom/package
schemas read source files + optional ``<stem>.schema.json`` sidecars,
so the resolution path goes through ``asyncio.to_thread``.
"""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kohakuterrarium.api.routes.catalog._deps import get_workspace
from kohakuterrarium.studio.catalog.introspect import (
    builtin_schema,
    custom_schema,
    resolve_module_source,
)
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class ModuleSchemaRequest(BaseModel):
    kind: str  # tools | subagents | triggers | plugins | inputs | outputs
    name: str = ""
    type: str = "builtin"  # builtin | custom | package | trigger
    module: str | None = None
    class_name: str | None = None


def _resolve_custom_schema_sync(
    root,
    module: str,
    class_name: str | None,
    kind: str,
) -> dict:
    source = resolve_module_source(root, module)
    if source is None:
        return {
            "params": [],
            "warnings": [
                {
                    "code": "module_not_found",
                    "message": f"could not resolve {module!r}",
                }
            ],
        }
    sidecar_schema = None
    if kind == "plugins":
        sidecar_schema = _load_plugin_sidecar(root, module)
    return custom_schema(source, class_name, sidecar_schema=sidecar_schema)


@router.post("")
async def module_schema(
    req: ModuleSchemaRequest,
    ws: Workspace = Depends(get_workspace),
) -> dict:
    # Trigger-as-tool entries (``type: trigger``) aren't real builtins —
    # their identity is the setup_tool_name and they carry no options
    # the user would edit here (those are set via the add_* call at
    # runtime). Return the builtin-tools schema as a baseline.
    if req.type == "trigger":
        return builtin_schema("tools")

    if req.type == "builtin":
        return builtin_schema(req.kind)

    if req.type in ("custom", "package"):
        if not req.module:
            return {
                "params": [],
                "warnings": [
                    {
                        "code": "missing_module",
                        "message": "custom / package entry is missing `module`",
                    }
                ],
            }
        return await asyncio.to_thread(
            _resolve_custom_schema_sync,
            ws.root_path,
            req.module,
            req.class_name,
            req.kind,
        )

    return {"params": [], "warnings": []}


def _load_plugin_sidecar(root, module: str) -> list | None:
    """Best-effort read of ``<stem>.schema.json`` next to the module file.

    Returns None when the sidecar is missing or unreadable — callers
    fall back to the plain ``__init__`` signature. Kept here rather
    than in introspect.py so studio's workspace-rooted path resolution
    stays local to the route that understands it.
    """
    if not module:
        return None
    candidate = Path(root) / (module.replace(".", "/") + ".schema.json")
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None
