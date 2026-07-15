"""Structured card output with optional interactive actions.

Display-only cards return after emission. Interactive cards wait for a
non-link action and return its identifier. Calls without an output router
receive a plain-text rendering instead.
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
    has_interactive_responder,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_VALID_ACCENTS = {"primary", "info", "success", "warning", "danger", "neutral"}
_VALID_STYLES = {"primary", "secondary", "danger", "link"}


@register_builtin("show_card")
class ShowCardTool(BaseTool):
    """Render a styled card and optionally wait for an action selection."""

    needs_context: bool = True
    # The manual distinguishes display, interactive, and link-only call shapes,
    # which cannot be inferred safely from the compact tool description.
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

    def prompt_contribution(self) -> str | None:
        return (
            "Show a styled card for structured display (plan preview, "
            "status, key/value facts) or a small **pick-one** gate. With "
            "non-`link` `actions` and `wait_for_reply` (on by default when "
            "actions exist) it blocks for the click and returns the "
            "chosen `action` id; with no actions it displays and returns "
            "at once. `link` actions only open a URL — a link-only card "
            "never waits. Use `ask_user` when the answer is free text."
        )

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        title = args.get("title")
        if not title or not isinstance(title, str):
            return ToolResult(error="show_card requires a 'title' string")

        payload = self._build_payload(args)
        actions = payload.get("actions") or []
        # Link actions open a URL client-side and never post a reply, so
        # only non-link buttons can resolve an interactive wait.
        actionable = [a for a in actions if a.get("style") != "link"]
        wait_for_reply = bool(args.get("wait_for_reply", bool(actions)))
        wants_reply = bool(actionable) and wait_for_reply
        timeout_s_arg = args.get("timeout_s")
        timeout_s = (
            float(timeout_s_arg) if isinstance(timeout_s_arg, (int, float)) else None
        )

        agent = getattr(context, "agent", None) if context else None
        router = getattr(agent, "output_router", None) if agent else None

        if router is None:
            # A textual fallback keeps headless callers from losing card content.
            return ToolResult(
                output=self._fallback_text(payload),
                exit_code=0,
            )

        interactive = wants_reply and has_interactive_responder(router)
        event_id = f"card_{uuid4().hex[:12]}"
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
            except Exception as e:
                logger.warning("show_card emit failed", error=str(e), exc_info=True)
                return ToolResult(
                    error=f"failed to emit card: {e}",
                    output=self._fallback_text(payload),
                )
            if wants_reply:
                # Emission can succeed even when no renderer can return an action.
                return ToolResult(
                    output=(
                        "card displayed; no interactive responder is attached "
                        "to collect a reply"
                    ),
                    exit_code=0,
                )
            return ToolResult(output="card displayed", exit_code=0)

        try:
            reply = await router.emit_and_wait(event, timeout_s=timeout_s)
        except Exception as e:
            logger.warning(
                "show_card emit_and_wait failed", error=str(e), exc_info=True
            )
            return ToolResult(error=f"card interaction failed: {e}")

        if reply.is_timeout:
            return ToolResult(output="card timed out without reply", exit_code=0)
        action_id = reply.action_id or ""
        # Preserve renderer-supplied values so richer card inputs are not discarded.
        values = reply.values or {}
        if values:
            return ToolResult(
                output=f"action: {action_id}\nvalues: {values}", exit_code=0
            )
        return ToolResult(output=f"action: {action_id}", exit_code=0)

    def _build_payload(self, args: dict[str, Any]) -> dict[str, Any]:
        """Build a renderer-safe card payload from validated arguments."""
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
        """Render card content as text for callers without an output router."""
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
