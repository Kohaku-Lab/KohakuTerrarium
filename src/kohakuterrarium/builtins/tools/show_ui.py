"""Open or update a declarative ``kt.ui`` widget and optionally wait for its messages."""

import json
from collections import OrderedDict
from typing import Any

from kohakuterrarium.builtins.kt_ui.expr import ExprError, interpolate, to_display
from kohakuterrarium.builtins.kt_ui.fallback import render_fallback
from kohakuterrarium.builtins.kt_ui.spec import (
    SpecError,
    ValidatedSpec,
    build_scope,
    validate_spec,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.errors import RendererUnavailable
from kohakuterrarium.modules.output.widgets import WidgetHandle
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolContext,
    ToolResult,
)

RENDERER = "kt.ui"
SURFACES = ("chat", "canvas")
MAX_REMEMBERED_SPECS = 64

_PARAMETERS = {
    "type": "object",
    "properties": {
        "spec": {
            "type": "object",
            "description": (
                "kt.ui document {title, layout, state, computed, root}; "
                "required unless update is set"
            ),
        },
        "update": {"type": "string", "description": "Widget id to change"},
        "state": {
            "type": "object",
            "description": "With update: values merged into the widget state",
        },
        "surface": {"type": "string", "enum": list(SURFACES)},
        "wait_for": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Message names to wait for; returns the first",
        },
        "timeout_s": {"type": "number", "description": "Bound the wait"},
    },
}

_GUIDANCE = (
    "Use a widget only when it helps more than text: numbers people tweak, "
    "comparisons, choices, data to explore. Keep it small and rare. "
    "Nodes: stack grid card section tabs/tab divider text heading badge stat kv "
    "list table image code chart number slider text_input select radio toggle "
    "checkbox date button form each. Inputs `bind` a `state` key; strings take "
    "`{{ expr }}`; `computed` holds named expressions (+ - * / % **, and or "
    "not, a ? b : c, round fmt pct sum avg min max len range). A `send` on a "
    "button or form reaches you as a widget_event."
)


def _error_text(exc: SpecError) -> str:
    lines = [f"- {path}: {message}" for path, message in exc.errors]
    return "invalid spec:\n" + "\n".join(lines)


def _decode_text_args(args: dict[str, Any]) -> dict[str, Any]:
    """Decode JSON-text arguments, as text tool formats pass every value as a string."""
    out = dict(args)
    for key in ("spec", "state", "wait_for"):
        value = out.get(key)
        if isinstance(value, str):
            text = value.strip()
            if key == "wait_for" and text and not text.startswith("["):
                out[key] = [n.strip() for n in text.split(",") if n.strip()]
                continue
            try:
                out[key] = json.loads(text) if text else None
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key} is not valid JSON: {exc}") from exc
    timeout = out.get("timeout_s")
    if isinstance(timeout, str) and timeout.strip():
        try:
            out["timeout_s"] = float(timeout)
        except ValueError as exc:
            raise ValueError("timeout_s must be a number") from exc
    return out


def _title(validated: ValidatedSpec) -> str:
    title = validated.spec.get("title") or ""
    try:
        return to_display(interpolate(title, build_scope(validated)))
    except ExprError:
        return title


@register_builtin("show_ui")
class ShowUiTool(BaseTool):
    """Show a declarative widget (form, table, chart, calculator) and read its messages."""

    needs_context: bool = True
    requires_renderers = frozenset({RENDERER})

    def __init__(
        self, config: ToolConfig | None = None, *, default_surface: str = "chat"
    ) -> None:
        super().__init__(config)
        self.default_surface = (
            default_surface if default_surface in SURFACES else "chat"
        )
        self._specs: OrderedDict[str, ValidatedSpec] = OrderedDict()

    @property
    def tool_name(self) -> str:
        return "show_ui"

    @property
    def description(self) -> str:
        return (
            "Show an interactive widget (form, table, chart, calculator) built from "
            "a JSON spec. Not for plain answers - write text."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return _PARAMETERS

    def prompt_contribution(self) -> str | None:
        return _GUIDANCE

    def _remember(self, widget_id: str, validated: ValidatedSpec) -> None:
        self._specs[widget_id] = validated
        self._specs.move_to_end(widget_id)
        while len(self._specs) > MAX_REMEMBERED_SPECS:
            self._specs.popitem(last=False)

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        widgets = context.widgets if context else None
        try:
            args = _decode_text_args(args)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        wait_for = args.get("wait_for") or []
        if not isinstance(wait_for, list) or not all(
            isinstance(n, str) for n in wait_for
        ):
            return ToolResult(error="wait_for must be a list of message names")
        timeout = args.get("timeout_s")
        timeout_s = float(timeout) if isinstance(timeout, (int, float)) else None
        if args.get("update"):
            return await self._update(widgets, args, wait_for, timeout_s)
        return await self._open(widgets, args, wait_for, timeout_s)

    async def _open(
        self, widgets: Any, args: dict, wait_for: list[str], timeout_s: float | None
    ) -> ToolResult:
        if "spec" not in args:
            return ToolResult(
                error="show_ui needs 'spec' (or 'update' with a widget id)"
            )
        try:
            validated = validate_spec(args["spec"])
        except SpecError as exc:
            return ToolResult(error=_error_text(exc))
        unknown = sorted(set(wait_for) - validated.send_names)
        if unknown:
            return ToolResult(
                error=f"wait_for names no button or form sends: {', '.join(unknown)}"
            )
        surface = args.get("surface") or (
            "canvas" if validated.layout == "page" else self.default_surface
        )
        if surface not in SURFACES:
            return ToolResult(error=f"surface must be one of {', '.join(SURFACES)}")
        fallback = render_fallback(validated)
        if widgets is None:
            return ToolResult(output=f"no UI is attached; text version:\n{fallback}")
        title = _title(validated)
        try:
            handle = await widgets.open(
                RENDERER,
                validated.spec,
                state=validated.spec.get("state") or {},
                title=title,
                surface=surface,
                fallback=fallback,
                routing="agent",
                interactive=validated.interactive,
            )
        except RendererUnavailable:
            return ToolResult(
                output=f"no UI can show this widget; text version:\n{fallback}"
            )
        self._remember(handle.id, validated)
        shown = f'ui {handle.id} "{title}" shown in {surface}'
        if not wait_for:
            return ToolResult(output=shown)
        return await self._wait(handle, wait_for, timeout_s, shown)

    async def _update(
        self, widgets: Any, args: dict, wait_for: list[str], timeout_s: float | None
    ) -> ToolResult:
        widget_id = args["update"]
        record = widgets.registry.get(widget_id) if widgets is not None else None
        if record is None or record.renderer != RENDERER:
            return ToolResult(error=f"no show_ui widget {widget_id!r}")
        if record.closed:
            return ToolResult(error=f"widget {widget_id} is closed")
        state = args.get("state")
        if state is not None and not isinstance(state, dict):
            return ToolResult(error="state must be an object")
        if "spec" not in args and not state:
            return ToolResult(error="update needs 'spec' or 'state'")
        validated = self._specs.get(widget_id)
        title = fallback = None
        if "spec" in args:
            try:
                validated = validate_spec(args["spec"])
            except SpecError as exc:
                return ToolResult(error=_error_text(exc))
            base = validated.spec.get("state") or {}
            kept = {k: record.state.get(k, v) for k, v in base.items()}
            state = {**kept, **(state or {})}
            title = _title(validated)
            self._remember(widget_id, validated)
        if validated is not None:
            unknown = sorted(set(wait_for) - validated.send_names)
            if unknown:
                return ToolResult(
                    error=f"wait_for names no button or form sends: {', '.join(unknown)}"
                )
            fallback = render_fallback(validated, {**record.state, **(state or {})})
        try:
            await widgets.update(
                widget_id,
                spec=validated.spec if "spec" in args else None,
                state=state or None,
                title=title,
                fallback=fallback,
            )
        except ValueError as exc:
            return ToolResult(error=str(exc))
        shown = f"ui {widget_id} updated"
        if not wait_for:
            return ToolResult(output=shown)
        return await self._wait(
            WidgetHandle(widgets, widget_id), wait_for, timeout_s, shown
        )

    async def _wait(
        self, handle: Any, wait_for: list[str], timeout_s: float | None, shown: str
    ) -> ToolResult:
        msg = await handle.wait(set(wait_for), timeout_s=timeout_s)
        if msg is None:
            reason = (
                "widget closed" if handle.closed else "no message before the timeout"
            )
            return ToolResult(output=f"{shown}; {reason}")
        data = json.dumps(msg.data, ensure_ascii=False, default=str)
        return ToolResult(output=f"{shown}; message {msg.name}: {data}")
