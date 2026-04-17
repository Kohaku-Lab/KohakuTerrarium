"""Callable-trigger adapter.

Wraps a setup-able :class:`BaseTrigger` subclass (``universal = True``,
with ``setup_*`` metadata filled in) as a :class:`BaseTool` so that the
agent can install it at runtime by making a normal tool call.

The adapter keeps the "trigger-ness" of the action visible in the short
description (``**Trigger** — …``) so the LLM knows calling it produces
a long-lived side-effect rather than an immediate result. The tool
itself always runs in ``DIRECT`` mode — it validates args against the
trigger class's schema, instantiates the trigger, wires any
context-derived state via ``post_setup``, registers it with the agent's
``TriggerManager``, and returns a confirmation message with the
installed trigger id.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class CallableTriggerTool(BaseTool):
    """Adapter exposing a setup-able trigger class as a callable tool."""

    needs_context = True

    def __init__(self, trigger_cls: type[BaseTrigger]):
        if not getattr(trigger_cls, "universal", False):
            raise ValueError(
                f"{trigger_cls.__name__} is not universal "
                f"(set `universal = True` on the class to expose as a tool)."
            )
        if not getattr(trigger_cls, "setup_tool_name", ""):
            raise ValueError(
                f"{trigger_cls.__name__} is universal but did not declare "
                "`setup_tool_name`."
            )
        super().__init__()
        self._cls = trigger_cls

    # ------------------------------------------------------------------
    # Tool metadata
    # ------------------------------------------------------------------

    @property
    def tool_name(self) -> str:
        return self._cls.setup_tool_name

    @property
    def description(self) -> str:
        base = self._cls.setup_description or self._cls.__doc__ or ""
        return f"**Trigger** — {base.strip()}"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    @property
    def require_manual_read(self) -> bool:  # type: ignore[override]
        return bool(self._cls.setup_require_manual_read)

    @require_manual_read.setter
    def require_manual_read(self, _value: bool) -> None:
        # Derived from the trigger class; ignore external writes but accept
        # BaseTool's init assignment silently.
        return None

    # Every setup-able trigger tool also exposes an optional `name` arg so
    # the agent can pick a stable, memorable `trigger_id` (used by
    # `stop_task` and visible on resume) instead of getting an
    # auto-generated hex id back.
    _NAME_ARG_SCHEMA: Any = {
        "type": "string",
        "description": (
            "Optional stable id for the installed trigger. If omitted, an "
            "auto-generated id like `trigger_<hex>` is returned. Pick a "
            "memorable name if you may need to `stop_task` it later."
        ),
    }

    def get_parameters_schema(self) -> dict[str, Any]:
        base = self._cls.setup_param_schema or {"type": "object", "properties": {}}
        schema = {
            "type": base.get("type", "object"),
            "properties": {
                "name": self._NAME_ARG_SCHEMA,
                **base.get("properties", {}),
            },
        }
        if "required" in base:
            schema["required"] = list(base["required"])
        return schema

    def get_full_documentation(self, tool_format: str = "native") -> str:
        doc = self._cls.setup_full_doc.strip() or self._cls.setup_description.strip()
        schema = self._cls.setup_param_schema or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", []) if isinstance(schema, dict) else [])
        lines = [f"# {self.tool_name}", "", f"**Trigger tool.** {doc}", ""]
        if props:
            lines.append("## Parameters")
            lines.append("")
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                req = " (required)" if pname in required else ""
                line = f"- `{pname}` ({ptype}){req}"
                if pdesc:
                    line += f" — {pdesc}"
                lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        if context is None or getattr(context, "agent", None) is None:
            return ToolResult(
                error="Trigger setup requires a ToolContext with an attached agent.",
                exit_code=1,
            )

        # `name` is an adapter-level arg, not part of the trigger class's own
        # setup schema — strip it before constructing the trigger.
        args = dict(args)
        requested_id = args.pop("name", None) or None
        if isinstance(requested_id, str):
            requested_id = requested_id.strip() or None

        missing = self._missing_required_args(args)
        if missing:
            return ToolResult(
                error=(
                    f"Missing required arg(s) for {self.tool_name}: "
                    f"{', '.join(sorted(missing))}"
                ),
                exit_code=1,
            )

        try:
            trigger = self._cls.from_setup_args(args)
        except Exception as e:  # noqa: BLE001 — surface class-level failures to the LLM
            return ToolResult(
                error=f"Failed to build {self._cls.__name__}: {e}",
                exit_code=1,
            )

        try:
            self._cls.post_setup(trigger, context)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                error=f"post_setup failed for {self._cls.__name__}: {e}",
                exit_code=1,
            )

        try:
            trigger_id = await context.agent.trigger_manager.add(
                trigger, trigger_id=requested_id
            )
        except ValueError as e:
            # Most likely cause: trigger_id already in use.
            return ToolResult(
                error=(f"Failed to register trigger with name={requested_id!r}: {e}"),
                exit_code=1,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                error=f"Failed to register trigger: {e}",
                exit_code=1,
            )

        logger.info(
            "Trigger installed",
            trigger_id=trigger_id,
            trigger_class=self._cls.__name__,
            tool_name=self.tool_name,
        )

        summary = _format_setup_summary(args)
        return ToolResult(
            output=(
                f"Trigger `{self.tool_name}` correctly set up and running in the "
                f"background with {summary}. trigger_id={trigger_id}"
            ),
            exit_code=0,
            metadata={
                "trigger_id": trigger_id,
                "trigger_class": self._cls.__name__,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _missing_required_args(self, args: dict[str, Any]) -> set[str]:
        schema = self._cls.setup_param_schema or {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        return {
            name for name in required if name not in args or args[name] in (None, "")
        }


def _format_setup_summary(args: dict[str, Any]) -> str:
    """Render the args dict as a short, LLM-readable setup summary."""
    if not args:
        return "no parameters"
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str):
            preview = v if len(v) <= 60 else v[:57] + "…"
            parts.append(f"{k}={preview!r}")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
