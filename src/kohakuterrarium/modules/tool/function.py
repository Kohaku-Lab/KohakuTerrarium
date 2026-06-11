"""``@tool`` — turn a plain Python function into an agent tool (E7).

The zero-boilerplate adapter behind instance injection::

    import kohakuterrarium as kt

    @kt.tool
    def score_notebook(path: str, rubric: str = "default") -> str:
        \"\"\"Grade a notebook file against the rubric.\"\"\"
        return do_grading(path, rubric)

    agent = await kt.Agent.build(cfg, tools=[score_notebook], ...)

Both sync and async functions work (sync runs via ``asyncio.to_thread``
so it never blocks the loop).  The tool name defaults to the function
name, the description to the first docstring line, and the parameter
schema is derived from the signature (annotations → JSON-schema
types).  A ``context`` parameter receives the :class:`ToolContext`.

(The old ``core.registry.tool`` decorator registered classes into a
global registry nothing ever read — this module repurposes the name
for something that actually reaches an agent.)
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
    """A :class:`BaseTool` wrapping a plain (a)sync function."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        execution_mode: ExecutionMode = ExecutionMode.DIRECT,
    ) -> None:
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
        except Exception as exc:  # noqa: BLE001 - tool surface
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
    """Wrap a function as a tool — decorator or direct call.

    ``@tool`` / ``@tool(name=..., description=...)`` /
    ``tool(existing_fn)`` all return a :class:`FunctionTool` instance
    ready for ``Agent.build(tools=[...])`` or ``agent.add_tool(...)``.
    """

    def _wrap(f: Callable[..., Any]) -> FunctionTool:
        return FunctionTool(
            f, name=name, description=description, execution_mode=execution_mode
        )

    if fn is not None:
        return _wrap(fn)
    return _wrap
