"""Plugin-manager user-command surface (design §11.3).

Plugin membership changes must update command and prompt inventories together.
"""

from typing import Any

from kohakuterrarium.modules.user_command.aggregate import (
    CommandContribution,
    CommandProvenance,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class PluginCommandRefreshMixin:
    """User-command collection + runtime membership refresh for PluginManager."""

    def collect_user_commands(self) -> list[Any]:
        """Collect active user commands with provenance for collision handling."""
        out: list[Any] = []
        for plugin in self._applicable_plugins():
            try:
                contributed = plugin.contribute_user_commands() or {}
            except Exception as e:
                logger.warning(
                    "Plugin contribute_user_commands raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                continue
            origin = getattr(plugin, "name", "?")
            for name, cmd in contributed.items():
                out.append(
                    CommandContribution(
                        name=name,
                        command=cmd,
                        provenance=CommandProvenance(source="plugin", origin=origin),
                        override=bool(getattr(cmd, "override", False)),
                    )
                )
        return out

    def mark_default_on(self, name: str) -> None:
        """Record that ``name`` is enabled only by default, not by the user's config."""
        self._default_on.add(name)

    def default_on_names(self) -> set[str]:
        """Enabled plugins that are on only by default."""
        return {name for name in self._default_on if name not in self._disabled}

    def stand_down(self, name: str) -> None:
        """Disable a default-on plugin whose tools clash, without refreshing hosts."""
        if name in self._disabled:
            return
        self._disabled.add(name)
        logger.warning(
            "Default-on plugin disabled: its tool name is taken", plugin_name=name
        )
        self._notify_selection_change()

    def collect_contributed_tools(self) -> list[tuple[str, Any]]:
        """Return ``(plugin_name, tool)`` for every tool active plugins contribute."""
        cache = self._contributed_tools
        out: list[tuple[str, Any]] = []
        for plugin in self._applicable_plugins():
            origin = getattr(plugin, "name", "?")
            cached = cache.get(id(plugin))
            if cached is None or cached[0] is not plugin:
                try:
                    tools = list(plugin.contribute_tools() or [])
                except Exception as e:
                    logger.warning(
                        "Plugin contribute_tools raised",
                        plugin_name=origin,
                        error=str(e),
                        exc_info=True,
                    )
                    tools = []
                cached = (plugin, tools)
                cache[id(plugin)] = cached
            out.extend((origin, tool) for tool in cached[1])
        live = {id(plugin) for plugin in self._plugins}
        for key in [key for key in cache if key not in live]:
            del cache[key]
        return out

    def unregister(self, name: str) -> bool:
        """Remove same-named plugins, clear toggle state, and refresh inventories."""
        before = len(self._plugins)
        self._plugins = [p for p in self._plugins if getattr(p, "name", "") != name]
        removed = len(self._plugins) != before
        self._disabled.discard(name)
        self._needs_load.discard(name)
        if removed:
            logger.info("Plugin unregistered", plugin_name=name)
            self._notify_command_change()
        return removed

    def bind_host(self, host_agent: Any) -> None:
        """Name the agent whose tools, commands and prompt follow plugin toggles.

        Until ``load_all`` supplies a load context, enable / disable refresh
        this agent, so toggles before ``start()`` take effect too.
        """
        self._bound_host = host_agent

    def _refresh_host_inventories(self) -> None:
        """Rebuild host commands and prompt together after plugin state changes."""
        context = self._load_context
        host_agent = context._host_agent if context is not None else None
        if host_agent is None:
            host_agent = getattr(self, "_bound_host", None)
        if host_agent is None:
            return
        refresh_tools = getattr(host_agent, "refresh_plugin_tools", None)
        if callable(refresh_tools):
            refresh_tools()
        refresh_cmds = getattr(host_agent, "refresh_user_commands", None)
        if callable(refresh_cmds):
            refresh_cmds()
        refresh_prompt = getattr(host_agent, "refresh_system_prompt", None)
        if callable(refresh_prompt):
            refresh_prompt()

    def _notify_command_change(self) -> None:
        """Best-effort refresh after removal, where rollback is unnecessary."""
        try:
            self._refresh_host_inventories()
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "refresh after plugin membership change failed",
                error=str(e),
                exc_info=True,
            )

    def _restore_host_inventories(self) -> None:
        """Best-effort rebuild after rollback so commands and prompt match state."""
        try:
            self._refresh_host_inventories()
        except Exception as e:  # pragma: no cover — defensive
            logger.error(
                "failed to restore host inventories after plugin toggle rollback",
                error=str(e),
                exc_info=True,
            )


__all__ = ["PluginCommandRefreshMixin"]
