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
        when a contributed name is already taken by another tool. A tool of
        the same class that is already configured is kept and stays its owner's.
        A plugin that is on only by default never raises: on a clash it stands
        down (is disabled) and the other tool keeps the name.
        """
        plugins = getattr(self, "plugins", None)
        installed: dict[str, tuple[str, Any]] = self.__dict__.setdefault(
            "_plugin_tools", {}
        )
        desired = self._desired_plugin_tools(plugins, installed)
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
        configured: set[str] = set()
        for name, (origin, tool) in desired.items():
            if name in installed and name not in removed:
                continue
            current = self.registry.get_tool(name)
            if current is None or current is tool or name in removed:
                continue
            if type(current) is type(tool):
                configured.add(name)
            else:
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
            if name in installed or name in configured:
                continue
            self.registry.register_tool(tool)
            self.executor.register_tool(tool)
            installed[name] = (origin, tool)
            logger.info("Plugin tool added", tool_name=name, plugin_name=origin)

    def _desired_plugin_tools(
        self, plugins: Any, installed: dict[str, tuple[str, Any]]
    ) -> dict[str, tuple[str, Any]]:
        """``name -> (plugin, tool)`` to install; default-on plugins yield on a clash."""
        if plugins is None:
            return {}
        yielding = plugins.default_on_names()
        pairs = plugins.collect_contributed_tools()
        by_origin: dict[str, list[Any]] = {}
        for origin, tool in pairs:
            by_origin.setdefault(origin, []).append(tool)
        desired: dict[str, tuple[str, Any]] = {}
        for origin, tool in pairs:
            if origin in yielding:
                continue
            name = tool.tool_name
            if name in desired:
                raise PluginToolConflictError(
                    f"plugins {desired[name][0]!r} and {origin!r} both "
                    f"contribute tool {name!r}"
                )
            desired[name] = (origin, tool)
        for origin in [o for o in by_origin if o in yielding]:
            tools = by_origin[origin]
            if any(self._name_taken(t, desired, installed) for t in tools):
                plugins.stand_down(origin)
                continue
            for tool in tools:
                desired[tool.tool_name] = (origin, tool)
        return desired

    def _name_taken(
        self,
        tool: Any,
        desired: dict[str, tuple[str, Any]],
        installed: dict[str, tuple[str, Any]],
    ) -> bool:
        name = tool.tool_name
        if name in desired:
            return True
        current = self.registry.get_tool(name)
        if current is None or current is tool:
            return False
        if name in installed and installed[name][1] is current:
            return False
        return type(current) is not type(tool)
