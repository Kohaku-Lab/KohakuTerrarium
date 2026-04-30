"""show_card tool — emit a Phase B ``card`` :class:`OutputEvent`.

Lets the model display structured information beautifully (title +
body + fields + footer + accent color) without inventing ad-hoc
markdown formatting, and optionally collect user input via action
buttons (a card-shaped survey form).

The tool reads the same payload schema the renderers consume — see
``plans/ui-event/design-phase-b.md`` §4.6 for full details. Two
modes:

- **Display-only** (no ``actions``, or ``wait_for_reply=False``):
  emits the card and returns immediately. Useful for plan previews,
  cost summaries, sub-agent result cards, monitoring tiles, etc.
- **Interactive** (``actions`` non-empty and ``wait_for_reply=True``):
  emits the card and awaits the user's button click via the bus.
  Returns the chosen ``action_id``. Useful for "approve / edit /
  reject" gates and small forms.

Falls back gracefully (returns a status message) when no output
router is wired (programmatic / test contexts).
"""

from typing import Any
from uuid import uuid4

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_VALID_ACCENTS = {"primary", "info", "success", "warning", "danger", "neutral"}
_VALID_STYLES = {"primary", "secondary", "danger", "link"}


@register_builtin("show_card")
class ShowCardTool(BaseTool):
    """Render a styled card to the user (display or interactive).

    A card has a header (title + optional subtitle + icon + accent),
    a body (markdown), optional key/value fields, an optional footer,
    and optional action buttons. When actions are present and
    ``wait_for_reply=True`` (the default), the tool blocks until the
    user clicks one and returns the action id.
    """

    needs_context: bool = True
    # The schema is rich (fields, actions, accent enum, ...) and the
    # right call shape depends on intent (display vs interactive vs
    # link-out). Force the model to read the manual once before its
    # first call so it produces structured args rather than guessing
    # — same pattern as ``edit`` / ``multi_edit``.
    require_manual_read: bool = True

    @property
    def tool_name(self) -> str:
        return "show_card"

    @property
    def description(self) -> str:
        return (
            "Display a styled card with optional action buttons. Use for "
            "plan previews, structured summaries, or simple approval gates."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        title = args.get("title")
        if not title or not isinstance(title, str):
            return ToolResult(error="show_card requires a 'title' string")

        payload = self._build_payload(args)
        actions = payload.get("actions") or []
        wait_for_reply = bool(args.get("wait_for_reply", bool(actions)))
        timeout_s_arg = args.get("timeout_s")
        timeout_s = (
            float(timeout_s_arg) if isinstance(timeout_s_arg, (int, float)) else None
        )

        agent = getattr(context, "agent", None) if context else None
        router = getattr(agent, "output_router", None) if agent else None

        if router is None:
            # Programmatic / test mode — surface a textual fallback so the
            # caller still sees something useful in the tool result.
            return ToolResult(
                output=self._fallback_text(payload),
                exit_code=0,
            )

        event_id = f"card_{uuid4().hex[:12]}"
        interactive = bool(actions) and wait_for_reply
        event = OutputEvent(
            type="card",
            interactive=interactive,
            surface=args.get("surface", "chat"),
            id=event_id,
            timeout_s=timeout_s,
            payload=payload,
        )

        if not interactive:
            try:
                await router.emit(event)
                return ToolResult(output="card displayed", exit_code=0)
            except Exception as e:
                logger.debug("show_card emit failed", error=str(e), exc_info=True)
                return ToolResult(
                    error=f"failed to emit card: {e}",
                    output=self._fallback_text(payload),
                )

        try:
            reply = await router.emit_and_wait(event, timeout_s=timeout_s)
        except Exception as e:
            logger.debug("show_card emit_and_wait failed", error=str(e), exc_info=True)
            return ToolResult(error=f"card interaction failed: {e}")

        if reply.is_timeout:
            return ToolResult(output="card timed out without reply", exit_code=0)
        action_id = reply.action_id or ""
        # Echo any submitted values too (cards may grow form-style data
        # later; staying forward-compatible).
        values = reply.values or {}
        if values:
            return ToolResult(
                output=f"action: {action_id}\nvalues: {values}", exit_code=0
            )
        return ToolResult(output=f"action: {action_id}", exit_code=0)

    def _build_payload(self, args: dict[str, Any]) -> dict[str, Any]:
        """Construct a card payload from tool arguments, validating
        types and dropping unknown keys to keep the renderer schema
        stable.
        """
        payload: dict[str, Any] = {"title": args["title"]}
        for key in ("subtitle", "icon", "body", "footer"):
            val = args.get(key)
            if isinstance(val, str) and val:
                payload[key] = val
        accent = args.get("accent")
        if isinstance(accent, str) and accent in _VALID_ACCENTS:
            payload["accent"] = accent
        fields_raw = args.get("fields")
        if isinstance(fields_raw, list):
            cleaned_fields = []
            for f in fields_raw:
                if not isinstance(f, dict):
                    continue
                label = f.get("label")
                value = f.get("value")
                if label is None or value is None:
                    continue
                entry: dict[str, Any] = {
                    "label": str(label),
                    "value": str(value),
                }
                if f.get("inline"):
                    entry["inline"] = True
                cleaned_fields.append(entry)
            if cleaned_fields:
                payload["fields"] = cleaned_fields
        actions_raw = args.get("actions")
        if isinstance(actions_raw, list):
            cleaned_actions = []
            for a in actions_raw:
                if not isinstance(a, dict):
                    continue
                aid = a.get("id")
                if not isinstance(aid, str) or not aid:
                    continue
                style = a.get("style", "secondary")
                if style not in _VALID_STYLES:
                    style = "secondary"
                entry = {
                    "id": aid,
                    "label": str(a.get("label", aid)),
                    "style": style,
                }
                if style == "link":
                    url = a.get("url")
                    if isinstance(url, str) and url:
                        entry["url"] = url
                cleaned_actions.append(entry)
            if cleaned_actions:
                payload["actions"] = cleaned_actions
        return payload

    @staticmethod
    def _fallback_text(payload: dict[str, Any]) -> str:
        """Plain-text rendering used when no router is attached.

        Keeps the model's tool result informative even when the bus
        isn't available (test contexts, programmatic invocation).
        """
        parts = [f"# {payload.get('title', 'Card')}"]
        if payload.get("subtitle"):
            parts.append(payload["subtitle"])
        if payload.get("body"):
            parts.append("")
            parts.append(payload["body"])
        for f in payload.get("fields") or []:
            parts.append(f"- {f.get('label')}: {f.get('value')}")
        if payload.get("actions"):
            labels = [a.get("label", a.get("id", "?")) for a in payload["actions"]]
            parts.append("")
            parts.append("Actions: " + " / ".join(labels))
        if payload.get("footer"):
            parts.append("")
            parts.append(payload["footer"])
        return "\n".join(parts)
