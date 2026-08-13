"""``@tool`` — turn a plain Python function into an agent tool (E7).

Function signatures supply names, descriptions, schemas, and optional tool context.
"""

import asyncio
import inspect
from typing import Any, Callable

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _annotation_to_json_type(annotation: Any) -> str:
    if annotation in _TYPE_MAP:
        return _TYPE_MAP[annotation]
    origin = getattr(annotation, "__origin__", None)
    if origin in _TYPE_MAP:
        return _TYPE_MAP[origin]
    return "string"


class FunctionTool(BaseTool):
    """Wrap a function with signature-derived schema and optional tool context."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        execution_mode: ExecutionMode = ExecutionMode.DIRECT,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._name = name or fn.__name__
        doc = inspect.getdoc(fn) or ""
        self._description = description or (
            doc.splitlines()[0] if doc else f"call {self._name}"
        )
        self._execution_mode = execution_mode
        self._signature = inspect.signature(fn)
        self.needs_context = "context" in self._signature.parameters

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def execution_mode(self) -> ExecutionMode:
        return self._execution_mode

    def get_parameters_schema(self) -> dict:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in self._signature.parameters.items():
            if pname == "context":
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            properties[pname] = {"type": _annotation_to_json_type(param.annotation)}
            if param.default is param.empty:
                required.append(pname)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        kwargs: dict[str, Any] = {}
        for pname, param in self._signature.parameters.items():
            if pname == "context":
                kwargs["context"] = context
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            if pname in args:
                kwargs[pname] = args[pname]
            elif param.default is param.empty:
                return ToolResult(error=f"missing required argument: {pname}")
        try:
            if inspect.iscoroutinefunction(self._fn):
                out = await self._fn(**kwargs)
            else:
                out = await asyncio.to_thread(self._fn, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(error=f"{type(exc).__name__}: {exc}")
        if isinstance(out, ToolResult):
            return out
        return ToolResult(output="" if out is None else str(out))


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    execution_mode: ExecutionMode = ExecutionMode.DIRECT,
) -> "FunctionTool | Callable[[Callable[..., Any]], FunctionTool]":
    """Return a function-backed tool through decorator or direct-call syntax."""

    def _wrap(f: Callable[..., Any]) -> FunctionTool:
        return FunctionTool(
            f, name=name, description=description, execution_mode=execution_mode
        )

    if fn is not None:
        return _wrap(fn)
    return _wrap
