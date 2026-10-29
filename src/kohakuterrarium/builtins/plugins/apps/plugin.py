"""``ui.apps``: let the agent show model-written HTML apps in the web UI.

The plugin contributes one :class:`ShowAppTool` configured from its options.
The tool, and its prompt guidance, stay hidden until an attached client
renders ``kt.app``.
"""

from typing import Any

from kohakuterrarium.builtins.tools.show_app import ShowAppTool
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.tool.base import BaseTool


class AppsPlugin(BasePlugin):
    """Contributes ``show_app`` with the configured host allow-list and limits."""

    name = "ui.apps"
    description = "Instant apps: the agent can show interactive HTML apps."
    priority = 50

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
