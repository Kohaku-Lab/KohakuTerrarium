"""Validation routes — dry-run schema + reference validation."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from kohakuterrarium.api.studio.deps import get_workspace
from kohakuterrarium.api.studio.validators import AgentConfigIn
from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.builtins.subagent_catalog import list_builtin_subagents
from kohakuterrarium.builtins.tool_catalog import list_builtin_tools

router = APIRouter()


class ValidateCreatureBody(BaseModel):
    config: dict = Field(default_factory=dict)


class ValidateModuleBody(BaseModel):
    kind: str
    source: str


@router.post("/creature")
async def validate_creature(
    body: ValidateCreatureBody, ws: Workspace = Depends(get_workspace)
) -> dict:
    """Run pydantic + reference validation over a creature config."""
    errors: list[dict] = []

    # 1. Schema validation
    try:
        cfg = AgentConfigIn(**body.config)
    except ValidationError as e:
        for err in e.errors():
            errors.append(
                {
                    "code": "schema_error",
                    "field": ".".join(str(p) for p in err["loc"]),
                    "message": err["msg"],
                }
            )
        return {"ok": False, "errors": errors}

    # 2. Reference validation
    builtin_tools = set(list_builtin_tools())
    builtin_subagents = set(list_builtin_subagents())

    for i, t in enumerate(cfg.tools):
        if t.type == "builtin" and t.name not in builtin_tools:
            errors.append(
                {
                    "code": "unknown_builtin_tool",
                    "field": f"tools[{i}].name",
                    "value": t.name,
                    "message": f"no builtin tool named {t.name!r}",
                }
            )
        elif t.type == "custom" and not t.module:
            errors.append(
                {
                    "code": "missing_module",
                    "field": f"tools[{i}].module",
                    "message": "custom tool missing `module` path",
                }
            )

    for i, s in enumerate(cfg.subagents):
        if s.type == "builtin" and s.name not in builtin_subagents:
            errors.append(
                {
                    "code": "unknown_builtin_subagent",
                    "field": f"subagents[{i}].name",
                    "value": s.name,
                    "message": f"no builtin sub-agent named {s.name!r}",
                }
            )

    # 3. Prompt file existence (best-effort — when we have a workspace)
    if cfg.system_prompt_file and getattr(ws, "root_path", None):
        # We don't know which creature this config belongs to; just
        # flag a warning rather than an error if the path is absolute
        # or doesn't match the expected shape.
        if cfg.system_prompt_file.startswith("/") or cfg.system_prompt_file.startswith(
            "\\"
        ):
            errors.append(
                {
                    "code": "absolute_prompt_path",
                    "field": "system_prompt_file",
                    "value": cfg.system_prompt_file,
                    "message": "system_prompt_file should be relative to the creature folder",
                }
            )

    return {"ok": not errors, "errors": errors}


@router.post("/module")
async def validate_module(body: ValidateModuleBody) -> dict:
    """Parse the module source. Currently just a syntax check."""
    if body.kind not in (
        "tools",
        "subagents",
        "triggers",
        "plugins",
        "inputs",
        "outputs",
    ):
        raise HTTPException(
            400,
            detail={
                "code": "unknown_kind",
                "message": f"unknown module kind: {body.kind!r}",
            },
        )
    try:
        compile(body.source, "<module>", "exec")
    except SyntaxError as e:
        return {
            "ok": False,
            "errors": [
                {
                    "code": "syntax_error",
                    "line": e.lineno,
                    "col": e.offset,
                    "message": str(e.msg),
                }
            ],
        }
    return {"ok": True, "errors": []}
