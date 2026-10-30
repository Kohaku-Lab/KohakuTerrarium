"""``ui.apps``: let the agent show model-written HTML apps in the web UI.

The plugin contributes one :class:`ShowAppTool` configured from its options,
and the full-page part of ``## Presenting your answer``. Both stay hidden
until an attached client renders ``kt.app``.
"""

from typing import Any

from kohakuterrarium.builtins.tools.show_app import ShowAppTool
from kohakuterrarium.core.tools.gate import visible_tool_names
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.tool.base import BaseTool

FULL_PAGE_PART = (
    "### Full-page apps\n\n"
    "When the user asks for a page, dashboard, explorer, simulation, game, "
    "showcase, or anything interactive or visual at full size, build it with "
    "`show_app`; prefer this to a long Markdown document."
)
WITH_INLINE_LINE = (
    "When inline UI is not enough, use `show_app`: a full page, an app you "
    "will patch later, or results the user sends back. A small custom visual "
    "stays inline as `html`."
)


class AppsPlugin(BasePlugin):
    """Contributes ``show_app`` with the configured host allow-list and limits."""

    name = "ui.apps"
    description = "Instant apps: the agent can show interactive HTML apps."
    priority = 50
    prompt_renderers = frozenset({"kt.ui"})

    def __init__(
        self,
        *,
        allowed_hosts: Any = None,
        default_surface: str = "canvas",
        max_document_kb: int = 256,
        options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        opts = dict(options or {})
        opts.setdefault("allowed_hosts", allowed_hosts)
        opts.setdefault("default_surface", default_surface)
        opts.setdefault("max_document_kb", max_document_kb)
        self.tool = ShowAppTool(
            allowed_hosts=opts["allowed_hosts"],
            default_surface=opts["default_surface"],
            max_document_kb=opts["max_document_kb"],
        )
        self.options = {
            "allowed_hosts": list(self.tool.allowed_hosts),
            "default_surface": self.tool.default_surface,
            "max_document_kb": self.tool.max_document_bytes // 1024,
        }

    def contribute_tools(self) -> list[BaseTool]:
        return [self.tool]

    def presentation_contribution(self, context: PluginContext) -> str | None:
        agent = context.host_agent
        catalog = getattr(agent, "_visible_catalog", None)
        registry = getattr(agent, "registry", None)
        if not callable(catalog) or registry is None:
            return None
        if "show_app" not in visible_tool_names(registry, catalog()):
            return None
        router = getattr(agent, "output_router", None)
        renderers = getattr(router, "ui_renderers", None)
        if callable(renderers) and "kt.ui" in renderers():
            return f"{FULL_PAGE_PART} {WITH_INLINE_LINE}"
        return FULL_PAGE_PART
