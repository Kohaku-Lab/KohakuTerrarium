"""Plugin manager — pre/post hook wrapping and callback dispatch.

Hooks run linearly by priority around the original method; callbacks and
runtime contribution refresh share the same applicability rules.
"""

import functools
import time
from typing import Any, Callable

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.dispatch import (
    call_method as _call_method,
    has_override as _has_override,
)
from kohakuterrarium.modules.plugin.manager_commands import (
    PluginCommandRefreshMixin,
)
from kohakuterrarium.modules.plugin.tool_visibility import (
    ToolVisibilityCollectorMixin,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _plugin_applies(plugin: BasePlugin, context: PluginContext | None) -> bool:
    """Evaluate applicability, defaulting to enabled when context or evaluation fails."""
    if context is None:
        return True
    try:
        return bool(plugin.should_apply(context))
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "Plugin should_apply raised; defaulting to True",
            plugin_name=getattr(plugin, "name", "?"),
            error=str(e),
            exc_info=True,
        )
        return True


class PluginManager(PluginCommandRefreshMixin, ToolVisibilityCollectorMixin):
    """Manages plugin lifecycle, hook wrapping, and callback dispatch."""

    def __init__(self) -> None:
        self._plugins: list[BasePlugin] = []
        self._disabled: set[str] = set()
        self._needs_load: set[str] = set()
        self._load_context: PluginContext | None = None
        # Timing remains optional so sessions without observers pay no callback cost.
        self._on_hook_timing: Callable[[str, str, float, bool], None] | None = None

    def set_hook_timing_callback(
        self, cb: Callable[[str, str, float, bool], None] | None
    ) -> None:
        """Attach an observer receiving hook name, plugin, duration, and block state."""
        self._on_hook_timing = cb

    def _emit_hook_timing(
        self, hook: str, plugin: BasePlugin, start: float, blocked: bool
    ) -> None:
        """Call the hook-timing observer if wired. Pure observability."""
        cb = self._on_hook_timing
        if cb is None:
            return
        duration_ms = (time.perf_counter() - start) * 1000.0
        try:
            cb(hook, getattr(plugin, "name", "?"), duration_ms, blocked)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "plugin_hook_timing emit failed", error=str(e), exc_info=True
            )

    def __bool__(self) -> bool:
        return len(self._plugins) > 0

    def __len__(self) -> int:
        return len(self._plugins)

    def register(self, plugin: BasePlugin) -> None:
        if name := getattr(plugin, "name", ""):
            self.unregister(name)
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: getattr(p, "priority", 50))
        logger.info(
            "Plugin registered",
            plugin_name=getattr(plugin, "name", "?"),
            priority=getattr(plugin, "priority", 50),
        )

    def enable(self, name: str) -> bool:
        """Enable a plugin, rolling back state if host inventory refresh fails."""
        if name in self._disabled:
            self._disabled.discard(name)
            self._needs_load.add(name)
            try:
                self._refresh_host_inventories()
            except Exception:
                self._disabled.add(name)
                self._needs_load.discard(name)
                self._restore_host_inventories()
                raise
            logger.info("Plugin enabled", plugin_name=name)
            return True
        return any(getattr(p, "name", "") == name for p in self._plugins)

    def disable(self, name: str) -> bool:
        for p in self._plugins:
            if getattr(p, "name", "") == name:
                self._disabled.add(name)
                try:
                    self._refresh_host_inventories()
                except Exception:
                    self._disabled.discard(name)
                    self._restore_host_inventories()
                    raise
                logger.info("Plugin disabled", plugin_name=name)
                return True
        return False

    def is_enabled(self, name: str) -> bool:
        return name not in self._disabled and any(
            getattr(p, "name", "") == name for p in self._plugins
        )

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {
                "name": getattr(p, "name", "?"),
                "priority": getattr(p, "priority", 50),
                "enabled": getattr(p, "name", "") not in self._disabled,
                "description": getattr(p, "description", ""),
            }
            for p in self._plugins
        ]

    def get_plugin(self, name: str) -> "BasePlugin | None":
        """Return the registered plugin instance with this name, or None."""
        for p in self._plugins:
            if getattr(p, "name", "") == name:
                return p
        return None

    def list_plugins_with_options(self) -> list[dict[str, Any]]:
        """List plugins with option schemas and current values for runtime editors."""
        out: list[dict[str, Any]] = []
        for p in self._plugins:
            try:
                schema = type(p).option_schema()
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "Plugin option_schema raised; skipping",
                    plugin_name=getattr(p, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                schema = {}
            try:
                values = p.get_options() if hasattr(p, "get_options") else {}
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "Plugin get_options raised; skipping",
                    plugin_name=getattr(p, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                values = {}
            out.append(
                {
                    "name": getattr(p, "name", "?"),
                    "priority": getattr(p, "priority", 50),
                    "enabled": getattr(p, "name", "") not in self._disabled,
                    "description": getattr(p, "description", ""),
                    "schema": schema or {},
                    "options": values or {},
                }
            )
        return out

    def set_plugin_options(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        """Apply validated overrides and return the plugin's merged options."""
        plugin = self.get_plugin(name)
        if plugin is None:
            raise KeyError(name)
        return plugin.set_options(values or {})

    def _active_plugins(self) -> list[BasePlugin]:
        if not self._disabled:
            return list(self._plugins)
        return [
            p for p in self._plugins if getattr(p, "name", "") not in self._disabled
        ]

    def _applicable_plugins(self) -> list[BasePlugin]:
        """Return active plugins whose current context passes applicability checks."""
        ctx = self._load_context
        return [p for p in self._active_plugins() if _plugin_applies(p, ctx)]

    def collect_runtime_services(self, context: Any) -> dict[str, Any]:
        """Collect optional per-call services from active plugins."""
        services: dict[str, Any] = {}
        for plugin in self._applicable_plugins():
            try:
                contributed = plugin.runtime_services(context) or {}
            except Exception as e:
                logger.warning(
                    "Plugin runtime_services raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                continue
            services.update(contributed)
        return services

    def collect_prompt_contributions(self, context: PluginContext) -> list[str]:
        """Collect runtime prompt prose in plugin priority order."""
        out: list[str] = []
        for plugin in self._applicable_plugins():
            try:
                content = plugin.get_prompt_content(context)
            except Exception as e:
                logger.warning(
                    "Plugin get_prompt_content raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                continue
            if content:
                out.append(content)
        return out

    def collect_commands(self) -> list[tuple[BasePlugin, dict[str, Any]]]:
        """Collect command contributions while isolating individual plugin failures."""
        out: list[tuple[BasePlugin, dict[str, Any]]] = []
        for plugin in self._applicable_plugins():
            try:
                contributed = plugin.contribute_commands() or {}
            except Exception as e:
                logger.warning(
                    "Plugin contribute_commands raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                continue
            if contributed:
                out.append((plugin, contributed))
        return out

    def collect_termination_checkers(
        self,
    ) -> list[tuple[str, Callable[[Any], Any]]]:
        """Collect named termination checkers for per-turn evaluation."""
        checkers: list[tuple[str, Callable[[Any], Any]]] = []
        for plugin in self._applicable_plugins():
            try:
                fn = plugin.contribute_termination_check()
            except Exception as e:
                logger.warning(
                    "Plugin contribute_termination_check raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                continue
            if fn is None:
                continue
            checkers.append((getattr(plugin, "name", "?"), fn))
        return checkers

    async def load_all(self, context: PluginContext) -> None:
        """Call on_load for enabled plugins only."""
        self._load_context = context
        host_agent = context._host_agent
        for plugin in self._active_plugins():
            try:
                ctx = PluginContext(
                    agent_name=context.agent_name,
                    working_dir=context.working_dir,
                    session_id=context.session_id,
                    model=context.model,
                    _host_agent=host_agent,
                    _plugin_name=getattr(plugin, "name", "unnamed"),
                    _spawn_child_agent_helper=context._spawn_child_agent_helper,
                )
                await _call_method(plugin, "on_load", context=ctx)
            except Exception as e:
                logger.warning(
                    "Plugin on_load failed",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )

    async def load_pending(self) -> None:
        """Call on_load for plugins that were enabled at runtime."""
        if not self._needs_load or not self._load_context:
            return
        host_agent = self._load_context._host_agent
        for plugin in self._plugins:
            pname = getattr(plugin, "name", "")
            if pname not in self._needs_load:
                continue
            try:
                ctx = PluginContext(
                    agent_name=self._load_context.agent_name,
                    working_dir=self._load_context.working_dir,
                    session_id=self._load_context.session_id,
                    model=self._load_context.model,
                    _host_agent=host_agent,
                    _plugin_name=pname,
                    _spawn_child_agent_helper=(
                        self._load_context._spawn_child_agent_helper
                    ),
                )
                await _call_method(plugin, "on_load", context=ctx)
            except Exception as e:
                logger.warning(
                    "on_load failed for runtime-enabled plugin",
                    plugin_name=pname,
                    error=str(e),
                    exc_info=True,
                )
        self._needs_load.clear()

    async def unload_all(self) -> None:
        for plugin in reversed(self._plugins):
            try:
                await _call_method(plugin, "on_unload")
            except Exception as e:
                logger.warning(
                    "Plugin on_unload failed",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )

    def wrap_method(
        self,
        pre_hook: str,
        post_hook: str,
        original: Callable,
        *,
        input_kwarg: str = "",
        extra_kwargs: dict[str, Any] | None = None,
    ) -> Callable:
        """Wrap a method with linear transforming pre-hooks and post-hooks."""
        if not self._plugins:
            return original

        has_pre = any(_has_override(p, pre_hook) for p in self._plugins)
        has_post = any(_has_override(p, post_hook) for p in self._plugins)
        if not has_pre and not has_post:
            return original

        manager = self
        injected = extra_kwargs or {}

        @functools.wraps(original)
        async def wrapper(first_arg, *args, **kwargs):
            active = manager._applicable_plugins()
            hook_kw = {**kwargs, **injected}

            if has_pre:
                for plugin in active:
                    if not _has_override(plugin, pre_hook):
                        continue
                    start = time.perf_counter()
                    blocked = False
                    try:
                        modified = await _call_method(
                            plugin, pre_hook, first_arg, **hook_kw
                        )
                        if modified is not None:
                            first_arg = modified
                    except PluginBlockError:
                        blocked = True
                        raise
                    except Exception as e:
                        logger.warning(
                            "Plugin pre-hook failed",
                            plugin_name=getattr(plugin, "name", "?"),
                            hook=pre_hook,
                            error=str(e),
                            exc_info=True,
                        )
                    finally:
                        manager._emit_hook_timing(pre_hook, plugin, start, blocked)

            result = await original(first_arg, *args, **kwargs)

            if has_post:
                post_kwargs = {**hook_kw}
                if input_kwarg:
                    post_kwargs[input_kwarg] = first_arg
                for plugin in active:
                    if not _has_override(plugin, post_hook):
                        continue
                    start = time.perf_counter()
                    try:
                        modified = await _call_method(
                            plugin, post_hook, result, **post_kwargs
                        )
                        if modified is not None:
                            result = modified
                    except Exception as e:
                        logger.warning(
                            "Plugin post-hook failed",
                            plugin_name=getattr(plugin, "name", "?"),
                            hook=post_hook,
                            error=str(e),
                            exc_info=True,
                        )
                    finally:
                        manager._emit_hook_timing(
                            post_hook, plugin, start, blocked=False
                        )

            return result

        return wrapper

    async def run_pre_hooks(self, hook_name: str, value: Any, **kwargs: Any) -> Any:
        """Run transforming pre-hooks where method wrapping cannot apply."""
        if not self._plugins:
            return value
        for plugin in self._applicable_plugins():
            if not _has_override(plugin, hook_name):
                continue
            start = time.perf_counter()
            blocked = False
            try:
                modified = await _call_method(plugin, hook_name, value, **kwargs)
                if modified is not None:
                    value = modified
            except PluginBlockError:
                blocked = True
                raise
            except Exception as e:
                logger.warning(
                    "Plugin pre-hook failed",
                    plugin_name=getattr(plugin, "name", "?"),
                    hook=hook_name,
                    error=str(e),
                    exc_info=True,
                )
            finally:
                self._emit_hook_timing(hook_name, plugin, start, blocked)
        return value

    async def notify(self, callback_name: str, **kwargs: Any) -> None:
        """Fire a callback on all active plugins."""
        if not self._plugins:
            return
        for plugin in self._applicable_plugins():
            if not hasattr(plugin, callback_name):
                continue
            start = time.perf_counter()
            try:
                await _call_method(plugin, callback_name, **kwargs)
            except Exception as e:
                logger.warning(
                    "Plugin callback failed",
                    plugin_name=getattr(plugin, "name", "?"),
                    callback=callback_name,
                    error=str(e),
                    exc_info=True,
                )
            finally:
                self._emit_hook_timing(callback_name, plugin, start, blocked=False)

    async def should_proceed(self, callback_name: str, **kwargs: Any) -> bool:
        """Return false when any applicable plugin explicitly vetoes an action."""
        if not self._plugins:
            return True
        vetoed: list[str] = []
        for plugin in self._applicable_plugins():
            if not hasattr(plugin, callback_name):
                continue
            try:
                result = await _call_method(plugin, callback_name, **kwargs)
            except Exception as e:
                logger.warning(
                    "Plugin vetoable callback failed",
                    plugin_name=getattr(plugin, "name", "?"),
                    callback=callback_name,
                    error=str(e),
                    exc_info=True,
                )
                continue
            if result is False:
                vetoed.append(getattr(plugin, "name", "?"))
        if vetoed:
            logger.info(
                "Plugin vetoed action",
                callback=callback_name,
                plugins=vetoed,
            )
            return False
        return True
