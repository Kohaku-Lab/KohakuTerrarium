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
from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.builtin_skills import (
    get_builtin_subagent_doc,
    get_builtin_tool_doc,
)
from kohakuterrarium.builtins.subagent_catalog import (
    get_builtin_subagent_config,
    list_builtin_subagents,
)
from kohakuterrarium.builtins.tool_catalog import (
    get_builtin_tool,
    list_builtin_tools,
)
from kohakuterrarium.llm.profiles import list_all as list_all_models

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
    from kohakuterrarium.modules.trigger.channel import ChannelTrigger
    from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger
    from kohakuterrarium.modules.trigger.timer import TimerTrigger

    out: list[dict] = []
    for cls in (TimerTrigger, ChannelTrigger, SchedulerTrigger):
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
    from kohakuterrarium.session.embedding import (
        list_embedding_presets as _list,
    )

    return _list()


# The frontend's plugin editor needs the full hook catalog — each
# hook's name, signature, and whether it's a pre/post/lifecycle/
# event kind. We hard-code the list here because ``BasePlugin``
# doesn't expose it programmatically. Phase 3 codegen consumes the
# same spec when generating plugin source.

PLUGIN_HOOKS: list[dict] = [
    {
        "name": "on_load",
        "group": "lifecycle",
        "args_signature": ", context: PluginContext",
        "return_hint": " -> None",
        "description": "Called once when the plugin is loaded.",
    },
    {
        "name": "on_unload",
        "group": "lifecycle",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "Called when the agent shuts down.",
    },
    {
        "name": "on_agent_start",
        "group": "lifecycle",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "Called after agent.start() completes.",
    },
    {
        "name": "on_agent_stop",
        "group": "lifecycle",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "Called before agent.stop() begins.",
    },
    {
        "name": "pre_llm_call",
        "group": "llm",
        "args_signature": ", messages: list[dict], **kwargs",
        "return_hint": " -> list[dict] | None",
        "description": "Before an LLM call. Return modified messages or None.",
    },
    {
        "name": "post_llm_call",
        "group": "llm",
        "args_signature": ", messages: list[dict], response: str, usage: dict, **kwargs",
        "return_hint": " -> None",
        "description": "After LLM call. Observation only.",
    },
    {
        "name": "pre_tool_execute",
        "group": "tool",
        "args_signature": ", args: dict, **kwargs",
        "return_hint": " -> dict | None",
        "description": (
            "Before tool run. Return modified args, or raise PluginBlockError."
        ),
    },
    {
        "name": "post_tool_execute",
        "group": "tool",
        "args_signature": ", result, **kwargs",
        "return_hint": " -> object | None",
        "description": "After tool run. Return modified result or None.",
    },
    {
        "name": "pre_subagent_run",
        "group": "subagent",
        "args_signature": ", task: str, **kwargs",
        "return_hint": " -> str | None",
        "description": (
            "Before sub-agent run. Return modified task or raise PluginBlockError."
        ),
    },
    {
        "name": "post_subagent_run",
        "group": "subagent",
        "args_signature": ", result, **kwargs",
        "return_hint": " -> object | None",
        "description": "After sub-agent run.",
    },
    {
        "name": "on_event",
        "group": "event",
        "args_signature": ", event",
        "return_hint": " -> None",
        "description": "Incoming trigger event. Observation only.",
    },
    {
        "name": "on_interrupt",
        "group": "event",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "User interrupt fired.",
    },
    {
        "name": "on_task_promoted",
        "group": "event",
        "args_signature": ", job_id: str, tool_name: str",
        "return_hint": " -> None",
        "description": "A direct task was promoted to background.",
    },
    {
        "name": "on_compact_start",
        "group": "event",
        "args_signature": ", context_length: int",
        "return_hint": " -> bool | None",
        "description": (
            "Context compaction about to start. Return False to veto this "
            "cycle; any other return value (None, True) proceeds."
        ),
    },
    {
        "name": "on_compact_end",
        "group": "event",
        "args_signature": ", summary: str, messages_removed: int",
        "return_hint": " -> None",
        "description": ("Context compaction completed (not called when vetoed)."),
    },
]


@router.get("/plugin_hooks")
async def list_plugin_hooks() -> list[dict]:
    return PLUGIN_HOOKS
