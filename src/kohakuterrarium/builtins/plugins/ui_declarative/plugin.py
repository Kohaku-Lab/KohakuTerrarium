"""Opt-in declarative widgets: contributes ``show_ui`` (renderer ``kt.ui``).

The tool is visible to the model only while an attached client reports the
``kt.ui`` renderer; its guidance comes with it.
"""

from typing import Any

from kohakuterrarium.builtins.tools.show_ui import SURFACES, ShowUiTool
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.tool.base import BaseTool


class UiDeclarativePlugin(BasePlugin):
    """Contributes ``show_ui`` with this plugin's options."""

    name = "ui.declarative"
    description = "Declarative chat widgets: the show_ui tool (renderer kt.ui)."
    priority = 50

    def __init__(self, default_surface: str = "chat", **_extra: Any) -> None:
        super().__init__()
        self.options = {
            "default_surface": (
                default_surface if default_surface in SURFACES else "chat"
            )
        }

    def contribute_tools(self) -> list[BaseTool]:
        return [ShowUiTool(default_surface=self.options["default_surface"])]
