"""Permission-gate plugin — Phase B canonical exemplar.

Pauses tool execution and asks the user to approve via a Phase B
``confirm`` :class:`OutputEvent`. Pluggable into any agent via the
standard plugin loader (``plugins: [{name: permgate, options: {...}}]``
in agent config).

The plugin uses **only** the output-event bus + the standard plugin
hook protocol. The bus has zero permgate-specific knowledge: this file
is the textbook for "how to build a plugin that interacts with the
user."

Configuration::

    plugins:
      - name: permgate
        options:
          # List of tool names that require approval. Empty list →
          # gate every tool. Special value ``"*"`` matches all tools.
          gated_tools: ["bash", "write", "edit"]
          # Tools that never require approval, even if listed in
          # gated_tools or matched by a pattern. Useful for CI
          # tools you want unconditionally allowed.
          allowlist: ["read", "glob", "grep"]
          # Seconds to wait for the user. Default ``None`` waits
          # forever — humans walking away from the keyboard is the
          # common case. Set a finite value for budget-sensitive
          # workflows where blocking forever is unacceptable.
          timeout_s: null
          # Surface for the prompt: "modal" (urgent) or "chat".
          surface: "modal"
"""

from typing import Any
from uuid import uuid4

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)


class PermGatePlugin(BasePlugin):
    """Block risky tool calls until the user explicitly approves."""

    name = "permgate"
    description = (
        "Pause tool execution and ask the user to approve via a confirm "
        "dialog. Default config gates every tool with a never-timeout "
        "wait — configure ``gated_tools`` / ``allowlist`` to scope it."
    )
    priority = 100  # late — runs after argument-rewriting plugins

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "gated_tools": {
                "type": "list",
                "item_type": "string",
                "default": [],
                "doc": (
                    "Tool names that require approval. Empty list = gate "
                    'every tool. Use ``"*"`` to match all tools.'
                ),
            },
            "allowlist": {
                "type": "list",
                "item_type": "string",
                "default": [],
                "doc": (
                    "Tools that never require approval, even if listed in "
                    "gated_tools."
                ),
            },
            "timeout_s": {
                "type": "float",
                "default": None,
                "min": 0,
                "doc": (
                    "Seconds to wait for the user. Null = wait forever "
                    "(default — humans walk away from keyboards)."
                ),
            },
            "surface": {
                "type": "enum",
                "values": ["modal", "chat"],
                "default": "modal",
                "doc": "Where the prompt appears.",
            },
        }

    def __init__(
        self,
        gated_tools: list[str] | None = None,
        allowlist: list[str] | None = None,
        timeout_s: float | None = None,
        surface: str = "modal",
        **_extra: Any,
    ) -> None:
        super().__init__()
        self.options = {
            "gated_tools": list(gated_tools or []),
            "allowlist": list(allowlist or []),
            "timeout_s": float(timeout_s) if timeout_s is not None else None,
            "surface": surface if surface in ("modal", "chat") else "modal",
        }
        self.refresh_options()
        # Cache of tools the user has approved "always" in this
        # session. The default flow ships only "allow once" / "deny",
        # but extending to remember choices is a one-liner change.
        self._session_approvals: set[str] = set()
        self._context: PluginContext | None = None

    # ── Options ──

    def refresh_options(self) -> None:
        """Re-derive cached fields from :attr:`options`."""
        self._gated = list(self.options.get("gated_tools") or [])
        self._allowlist = set(self.options.get("allowlist") or [])
        ts = self.options.get("timeout_s")
        self._timeout_s = float(ts) if ts is not None else None
        surface = self.options.get("surface", "modal")
        self._surface = surface if surface in ("modal", "chat") else "modal"

    # ── Lifecycle ──

    async def on_load(self, context: PluginContext) -> None:
        self._context = context

    # ── Tool gating ──

    def _gates_tool(self, tool_name: str) -> bool:
        """Return True if this tool must go through the gate."""
        if tool_name in self._allowlist:
            return False
        if not self._gated:
            return True  # empty gated list = gate everything
        if "*" in self._gated:
            return True
        return tool_name in self._gated

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        """Gate the tool call on user consent."""
        tool_name = kwargs.get("tool_name", "")
        if not tool_name or not self._gates_tool(tool_name):
            return None
        if tool_name in self._session_approvals:
            return None

        ctx = self._context
        if ctx is None or ctx.host_agent is None:
            # Plugin not yet bound — pass through silently.
            return None

        event_id = f"permgate_{uuid4().hex[:12]}"
        event = OutputEvent(
            type="confirm",
            interactive=True,
            surface=self._surface,
            id=event_id,
            timeout_s=self._timeout_s,
            payload={
                "prompt": f"Allow tool: {tool_name}?",
                "detail": _summarise_args(args),
                "options": [
                    {
                        "id": "allow_once",
                        "label": "Allow once",
                        "style": "primary",
                    },
                    {
                        "id": "allow_session",
                        "label": "Always allow",
                        "style": "secondary",
                    },
                    {
                        "id": "deny",
                        "label": "Deny",
                        "style": "danger",
                    },
                ],
                "default": "deny",
            },
        )

        try:
            reply = await ctx.emit_and_wait(event, timeout_s=self._timeout_s)
        except Exception as e:
            # Bus unavailable — fail safe: block the tool. A real
            # deployment may want a fail-open mode behind a flag.
            raise PluginBlockError(
                f"permgate: bus error while gating {tool_name}: {e}"
            ) from e

        action = reply.action_id

        if action == "allow_session":
            self._session_approvals.add(tool_name)
            return None
        if action == "allow_once":
            return None
        if action == "__timeout__":
            timeout_label = f"{self._timeout_s}s" if self._timeout_s else "timeout"
            raise PluginBlockError(
                f"permgate: no response for {tool_name} within "
                f"{timeout_label} — denied."
            )
        # action ∈ {"deny", "cancel", "__superseded__", ...}
        raise PluginBlockError(
            f"permgate: tool {tool_name} blocked by user ({action})."
        )


def _summarise_args(args: dict[str, Any]) -> str:
    """Build a single-line ``key=value`` summary of tool arguments."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        if k.startswith("_"):
            continue
        sval = str(v)
        if len(sval) > 80:
            sval = sval[:77] + "..."
        parts.append(f"{k}={sval}")
    return "  ".join(parts)
