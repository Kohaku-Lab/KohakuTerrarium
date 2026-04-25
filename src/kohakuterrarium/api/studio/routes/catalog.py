"""Catalog routes — read-only module directory for the pool.

Each module kind merges three sources:

1. **Builtins** — framework-registered classes (``read``, ``bash``,
   ``explore``, universal setup-tool triggers, etc.).
2. **Workspace kohaku.yaml** — if the open workspace declares its
   own tools / subagents / triggers / plugins / io, those get
   listed here so authors can wire them into creatures.
3. **Installed packages** — every kt package in
   ``~/.kohakuterrarium/packages/`` contributes its manifest
   entries. kt-biome's ``cost_tracker`` plugin and ``database``
   tool arrive here.

Entries carry ``source`` (``builtin`` | ``workspace`` |
``package:<name>``) and enough wiring info (``type``, ``module``,
``class_name``) that the frontend can produce a valid creature
config entry on click.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.studio.catalog_sources import (
    dedupe_preserve_order,
    package_entries,
    workspace_manifest_entries,
)
from kohakuterrarium.api.studio.deps import get_workspace_optional
from kohakuterrarium.api.studio.plugin_hooks import PLUGIN_HOOKS
from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.builtin_skills import (
    get_builtin_subagent_doc,
    get_builtin_tool_doc,
)
from kohakuterrarium.builtins.subagent_catalog import (
    get_builtin_subagent_config,
    list_builtin_subagents,
)
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool, list_builtin_tools
from kohakuterrarium.llm.profiles import list_all as list_all_models
from kohakuterrarium.modules.trigger.universal import list_universal_trigger_classes
from kohakuterrarium.session.embedding import (
    list_embedding_presets as _list_embedding_presets,
)

router = APIRouter()


# ---- Tools ----------------------------------------------------------


@router.get("/tools")
async def list_tools(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """Builtin + workspace + installed-package tools."""
    out: list[dict] = []

    for name in list_builtin_tools():
        tool = get_builtin_tool(name)
        if tool is None:
            continue
        try:
            execution_mode = tool.execution_mode.value
        except Exception:
            execution_mode = "direct"
        out.append(
            {
                "name": name,
                "description": tool.description,
                "source": "builtin",
                "type": "builtin",
                "module": None,
                "class_name": None,
                "execution_mode": execution_mode,
                "needs_context": bool(getattr(tool, "needs_context", False)),
                "require_manual_read": bool(
                    getattr(tool, "require_manual_read", False)
                ),
                "has_doc": get_builtin_tool_doc(name) is not None,
            }
        )

    # Workspace shadows packages on name collisions; both shadow
    # builtins if the manifest overrides one (rare, but predictable).
    out.extend(workspace_manifest_entries(ws, "tools"))
    out.extend(package_entries("tools"))

    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


@router.get("/tools/{name}/doc")
async def get_tool_doc(name: str) -> dict:
    doc = get_builtin_tool_doc(name)
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
    out: list[dict] = []

    for name in list_builtin_subagents():
        cfg = get_builtin_subagent_config(name)
        if cfg is None:
            continue
        out.append(
            {
                "name": name,
                "description": cfg.description,
                "source": "builtin",
                "type": "builtin",
                "module": None,
                "class_name": None,
                "can_modify": bool(cfg.can_modify),
                "interactive": bool(cfg.interactive),
                "tools": list(cfg.tools),
                "has_doc": get_builtin_subagent_doc(name) is not None,
            }
        )

    out.extend(workspace_manifest_entries(ws, "subagents"))
    out.extend(package_entries("subagents"))

    out = dedupe_preserve_order(out)
    out.sort(key=lambda x: x["name"])
    return out


@router.get("/subagents/{name}/doc")
async def get_subagent_doc(name: str) -> dict:
    doc = get_builtin_subagent_doc(name)
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
    out: list[dict] = []
    for cls in list_universal_trigger_classes():
        if not getattr(cls, "universal", False):
            continue
        out.append(
            {
                "name": cls.setup_tool_name,
                "description": cls.setup_description,
                "source": "builtin",
                "type": "trigger",
                "module": None,
                "class_name": None,
                "param_schema": cls.setup_param_schema,
                "require_manual_read": bool(cls.setup_require_manual_read),
            }
        )

    out.extend(workspace_manifest_entries(ws, "triggers"))
    out.extend(package_entries("triggers"))

    out = dedupe_preserve_order(out)
    return out


# ---- Plugins --------------------------------------------------------


@router.get("/plugins")
async def list_plugins(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """Plugins declared in workspace / installed packages.

    Plugins have no core-shipped builtins (BasePlugin is an abstract
    base); all discoverable plugins live in kohaku.yaml manifests.
    """
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
    """Custom input modules declared in manifests (e.g. discord_input).

    kohaku.yaml uses ``io:`` for both inputs and outputs; classification
    happens inside the shared helpers via :func:`classify_io`.
    """
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


# The frontend's plugin editor needs the full hook catalog.
# Kept in api.studio.plugin_hooks so routes and codegen share one source.


@router.get("/plugin_hooks")
async def list_plugin_hooks() -> list[dict]:
    return PLUGIN_HOOKS
