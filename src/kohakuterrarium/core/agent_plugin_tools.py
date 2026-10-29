"""Agent side of plugin-contributed tools (``BasePlugin.contribute_tools``)."""

from typing import Any

from kohakuterrarium.errors import PluginToolConflictError
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentPluginToolsMixin:
    """Keep plugin-contributed tools in step with the enabled plugins."""

    registry: Any
    executor: Any

    def refresh_plugin_tools(self) -> None:
        """Register tools of enabled plugins and remove tools of disabled ones.

        Raises :class:`PluginToolConflictError` before the registry changes
        when a contributed name is already taken by another tool.
        """
        plugins = getattr(self, "plugins", None)
        desired: dict[str, tuple[str, Any]] = {}
        if plugins is not None:
            for origin, tool in plugins.collect_contributed_tools():
                name = tool.tool_name
                if name in desired:
                    raise PluginToolConflictError(
                        f"plugins {desired[name][0]!r} and {origin!r} both "
                        f"contribute tool {name!r}"
                    )
                desired[name] = (origin, tool)
        installed: dict[str, tuple[str, Any]] = self.__dict__.setdefault(
            "_plugin_tools", {}
        )
        # A name re-registered by someone else (add_tool) is no longer ours.
        for name in [
            n for n, (_, t) in installed.items() if self.registry.get_tool(n) is not t
        ]:
            del installed[name]
        removed = {
            name
            for name, (_, tool) in installed.items()
            if name not in desired or desired[name][1] is not tool
        }
        for name, (origin, tool) in desired.items():
            if name in installed and name not in removed:
                continue
            current = self.registry.get_tool(name)
            if current is not None and current is not tool and name not in removed:
                raise PluginToolConflictError(
                    f"plugin {origin!r} contributes tool {name!r}, "
                    "which is already registered"
                )
        for name in removed:
            self.registry.unregister_tool(name)
            self.executor.unregister_tool(name)
            del installed[name]
            logger.info("Plugin tool removed", tool_name=name)
        for name, (origin, tool) in desired.items():
            if name in installed:
                continue
            self.registry.register_tool(tool)
            self.executor.register_tool(tool)
            installed[name] = (origin, tool)
            logger.info("Plugin tool added", tool_name=name, plugin_name=origin)
