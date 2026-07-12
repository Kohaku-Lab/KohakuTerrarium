"""Plugin-manager user-command surface (design §11.3).

Split from :mod:`manager` to keep that file under the size cap. Provides the
mixin the :class:`~kohakuterrarium.modules.plugin.manager.PluginManager` uses to
(a) collect active plugins' ``contribute_user_commands()`` output with
provenance, (b) drop a plugin at runtime (``unregister``), and (c) refresh the
host Agent's command inventory whenever plugin membership/enabled-state changes.

These are grouped because they all exist to keep the CLI / TUI / web slash-command
inventory truthful as plugins toggle; the collection method mirrors the other
``collect_*`` collectors on the manager.
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
        """Collect active plugins' ``contribute_user_commands()`` output.

        Returns a list of
        :class:`~kohakuterrarium.modules.user_command.aggregate.CommandContribution`
        tagged with ``plugin:<name>`` provenance so the Agent registry can run
        the collision policy. Errors in an individual plugin are logged and the
        plugin is skipped. The command's own ``override`` attribute names it as
        an explicit collision winner (design §11.5).
        """
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

    def unregister(self, name: str) -> bool:
        """Remove every plugin registered under ``name`` + clear its toggle state.

        The runtime "remove" path (design §11.3): dropping a plugin must strip
        its user-command contributions from the inventory. Also used by
        ``Agent.add_plugin`` to replace a same-named instance (e.g. one a
        package registered disabled) instead of leaving a stale duplicate.
        Fires the host command-inventory refresh. Returns True if anything was
        removed.
        """
        before = len(self._plugins)
        self._plugins = [p for p in self._plugins if getattr(p, "name", "") != name]
        removed = len(self._plugins) != before
        self._disabled.discard(name)
        self._needs_load.discard(name)
        if removed:
            logger.info("Plugin unregistered", plugin_name=name)
            self._notify_command_change()
        return removed

    def _refresh_host_inventories(self) -> None:
        """Rebuild the host Agent's user-command registry AND system prompt.

        A plugin toggle changes both surfaces at once: its
        ``contribute_user_commands()`` output and its ``get_prompt_content()``
        prose must appear/disappear together (design §11.3, R1-23). Propagates
        the aggregation's typed error (e.g. ``UserCommandCollisionError``) so a
        toggle caller can roll back. Only fires once a runtime load context with
        a host agent exists (never during boot registration), so the initial
        inventory + prompt are built once by the Agent itself.
        """
        context = self._load_context
        host_agent = context._host_agent if context is not None else None
        if host_agent is None:
            return
        refresh_cmds = getattr(host_agent, "refresh_user_commands", None)
        if callable(refresh_cmds):
            refresh_cmds()
        refresh_prompt = getattr(host_agent, "refresh_system_prompt", None)
        if callable(refresh_prompt):
            refresh_prompt()

    def _notify_command_change(self) -> None:
        """Best-effort inventory + prompt refresh after a membership change.

        Used by ``unregister``, where a removed plugin cannot introduce a name
        collision; a refresh failure here is logged rather than raised. Toggle
        paths (enable/disable) instead call :meth:`_refresh_host_inventories`
        directly so a collision rolls the toggle back with a typed error.
        """
        try:
            self._refresh_host_inventories()
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "refresh after plugin membership change failed",
                error=str(e),
                exc_info=True,
            )

    def _restore_host_inventories(self) -> None:
        """Rebuild BOTH host inventories after a rolled-back toggle (R1-23).

        A toggle refreshes commands THEN prompt; if the prompt refresh fails the
        caller restores the plugin's enabled state, but the command registry was
        already rebuilt for the *rejected* state and would be left stale. Called
        AFTER the state is restored, this re-runs the full refresh so commands and
        prompt both match the restored membership. Best-effort: the restored state
        was live before the toggle, so rebuilding to it should succeed; a failure
        is logged, not raised, so the original toggle error still propagates.
        """
        try:
            self._refresh_host_inventories()
        except Exception as e:  # pragma: no cover — defensive
            logger.error(
                "failed to restore host inventories after plugin toggle rollback",
                error=str(e),
                exc_info=True,
            )


__all__ = ["PluginCommandRefreshMixin"]
