"""Starting points for new creatures and modules, and what each would write."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.routes.catalog._deps import get_workspace_optional
from kohakuterrarium.studio.editors.codegen_init import get_codegen
from kohakuterrarium.studio.editors.creatures_crud import render_creature
from kohakuterrarium.studio.editors.modules_crud import render_module
from kohakuterrarium.studio.editors.starters import (
    UnknownStarterError,
    list_starters,
    starter_form,
)
from kohakuterrarium.studio.editors.utils_paths import sanitize_name
from kohakuterrarium.studio.editors.wiring import (
    config_var_of,
    first_class_name,
    wiring_entry,
)
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class PreviewBody(BaseModel):
    kind: str
    id: str | None = None
    name: str
    base_config: str | None = None
    description: str = ""
    purpose: str = ""
    model: str = ""


def _bad(code: str, e: Exception) -> HTTPException:
    return HTTPException(400, detail={"code": code, "message": str(e)})


@router.get("")
async def get_starters(kind: str | None = None) -> list[dict]:
    try:
        return [s.as_dict() for s in list_starters(kind)]
    except UnknownStarterError as e:
        raise _bad("unknown_kind", e)


def _creature_preview(body: PreviewBody) -> dict:
    name = sanitize_name(body.name)
    rendered = render_creature(
        name,
        body.base_config,
        starter=body.id,
        description=body.description,
        purpose=body.purpose,
        model=body.model,
    )
    return {
        "files": [
            {"path": f"creatures/{name}/{rel}", "content": text}
            for rel, text in rendered.items()
        ]
    }


def _module_preview(body: PreviewBody, ws: Workspace | None) -> dict:
    name = sanitize_name(body.name)
    source = render_module(body.kind, name, body.id)
    rel = f"modules/{body.kind}/{name}.py"
    files = [{"path": rel, "content": source}]
    sidecars = getattr(get_codegen(body.kind), "sidecar_files", None)
    for suffix, text in (
        sidecars(starter_form(body.kind, body.id)) if sidecars else {}
    ).items():
        files.append({"path": f"modules/{body.kind}/{name}{suffix}", "content": text})
    prefix = ws.ref_prefix() if ws is not None else None  # type: ignore[attr-defined]
    ref = f"{prefix}/{rel}" if prefix else rel
    entry = wiring_entry(
        body.kind,
        name,
        ref,
        class_name=first_class_name(source),
        config_var=config_var_of(source) if body.kind == "subagents" else None,
    )
    return {"files": files, "ref": ref, "entry": entry}


@router.post("/preview")
async def preview(
    body: PreviewBody, ws: Workspace | None = Depends(get_workspace_optional)
) -> dict:
    try:
        if body.kind == "creatures":
            return _creature_preview(body)
        return _module_preview(body, ws)
    except UnknownStarterError as e:
        raise _bad("unknown_starter", e)
    except ValueError as e:
        raise _bad("invalid_name", e)
