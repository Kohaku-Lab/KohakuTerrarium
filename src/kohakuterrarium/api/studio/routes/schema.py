"""Schema route — returns the param list for a module entry.

Called from the frontend's module-slot accordion to render a proper
option form for both builtin and custom modules. See
``kohakuterrarium/api/studio/introspect.py`` for how each source is
resolved.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kohakuterrarium.api.studio.deps import get_workspace
from kohakuterrarium.api.studio.introspect import (
    builtin_schema,
    custom_schema,
    resolve_module_source,
)
from kohakuterrarium.api.studio.workspace.base import Workspace

router = APIRouter()


class ModuleSchemaRequest(BaseModel):
    kind: str  # tools | subagents | triggers | plugins | inputs | outputs
    name: str = ""
    type: str = "builtin"  # builtin | custom | package | trigger
    module: str | None = None
    class_name: str | None = None


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
        source = resolve_module_source(ws.root_path, req.module)
        if source is None:
            return {
                "params": [],
                "warnings": [
                    {
                        "code": "module_not_found",
                        "message": f"could not resolve {req.module!r}",
                    }
                ],
            }
        return custom_schema(source, req.class_name)

    return {"params": [], "warnings": []}
