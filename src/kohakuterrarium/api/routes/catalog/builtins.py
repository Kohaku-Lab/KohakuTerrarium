"""Catalog routes — read-only module directory for the pool.

Each module kind merges three sources: builtins, workspace
``kohaku.yaml``, and installed packages. Entries carry ``source``
(``builtin`` | ``workspace-manifest`` | ``package:<name>``) and enough
wiring info (``type``, ``module``, ``class_name``) that the frontend
can produce a valid creature config entry on click.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.routes.catalog._deps import get_workspace_optional
from kohakuterrarium.llm.profiles import list_all as list_all_models
from kohakuterrarium.session.embedding import (
    list_embedding_presets as _list_embedding_presets,
)
from kohakuterrarium.studio.catalog.builtins import (
    get_subagent_doc,
    get_tool_doc,
    list_builtin_subagent_entries,
    list_builtin_tool_entries,
    list_universal_trigger_entries,
)
from kohakuterrarium.studio.catalog.catalog_sources import (
    dedupe_preserve_order,
    package_entries,
    workspace_manifest_entries,
)
from kohakuterrarium.studio.editors.plugin_hooks import PLUGIN_HOOKS
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


# ---- Tools ----------------------------------------------------------


@router.get("/tools")
async def list_tools(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """Builtin + workspace + installed-package tools."""
    out: list[dict] = list(list_builtin_tool_entries())

    out.extend(workspace_manifest_entries(ws, "tools"))
    out.extend(package_entries("tools"))

    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


@router.get("/tools/{name}/doc")
async def get_tool_doc_route(name: str) -> dict:
    doc = get_tool_doc(name)
    if doc is None:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"no doc for tool {name!r}",
            },
        )
    return {"name": name, "doc": doc}


# ---- Sub-agents -----------------------------------------------------


@router.get("/subagents")
async def list_subagents(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    out: list[dict] = list(list_builtin_subagent_entries())
    out.extend(workspace_manifest_entries(ws, "subagents"))
    out.extend(package_entries("subagents"))
    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


@router.get("/subagents/{name}/doc")
async def get_subagent_doc_route(name: str) -> dict:
    doc = get_subagent_doc(name)
    if doc is None:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"no doc for subagent {name!r}",
            },
        )
    return {"name": name, "doc": doc}


# ---- Triggers -------------------------------------------------------


@router.get("/triggers")
async def list_triggers(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """Universal setup-tool triggers + workspace + package triggers."""
    out: list[dict] = list(list_universal_trigger_entries())
    out.extend(workspace_manifest_entries(ws, "triggers"))
    out.extend(package_entries("triggers"))
    out = dedupe_preserve_order(out)
    return out


# ---- Plugins --------------------------------------------------------


@router.get("/plugins")
async def list_plugins(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """Plugins declared in workspace / installed packages."""
    out: list[dict] = []
    out.extend(workspace_manifest_entries(ws, "plugins"))
    out.extend(package_entries("plugins"))
    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


# ---- Inputs / Outputs ----------------------------------------------


@router.get("/inputs")
async def list_inputs(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    out = workspace_manifest_entries(ws, "inputs") + package_entries("inputs")
    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


@router.get("/outputs")
async def list_outputs(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    out = workspace_manifest_entries(ws, "outputs") + package_entries("outputs")
    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


# ---- Models + plugin hooks (unchanged) -----------------------------


@router.get("/models")
async def list_models() -> list[dict]:
    """LLM profiles (reuses core llm.profiles.list_all)."""
    return list_all_models()


@router.get("/embedding_presets")
async def list_embedding_presets() -> dict:
    """Grouped embedding presets (model2vec / sentence-transformer)."""
    return _list_embedding_presets()


@router.get("/plugin_hooks")
async def list_plugin_hooks() -> list[dict]:
    return PLUGIN_HOOKS
