"""Per-request tool catalog visibility contributions for PluginManager."""

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginContext,
    ToolVisibility,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _intersect_names(
    left: frozenset[str] | None, right: frozenset[str] | None
) -> frozenset[str] | None:
    """Intersect two per-category name restrictions; None means unrestricted."""
    if left is None:
        return right
    if right is None:
        return left
    return left & right


def _merge_visibility(
    current: ToolVisibility | None, extra: ToolVisibility
) -> ToolVisibility:
    """Merge an extra restriction into the running restriction set."""
    if current is None:
        return extra
    return ToolVisibility(
        allowed_tools=_intersect_names(current.allowed_tools, extra.allowed_tools),
        allowed_subagents=_intersect_names(
            current.allowed_subagents, extra.allowed_subagents
        ),
    )


def _request_context(base: PluginContext | None) -> PluginContext | None:
    """Copy a load context with the host's current model, when available."""
    if base is None:
        return None
    model = base.model
    host_agent = base._host_agent
    if host_agent is not None:
        llm = getattr(host_agent, "llm", None)
        current_model = getattr(llm, "model", "")
        if current_model:
            model = current_model
    return PluginContext(
        agent_name=base.agent_name,
        working_dir=base.working_dir,
        session_id=base.session_id,
        model=model,
        _host_agent=host_agent,
        _spawn_child_agent_helper=base._spawn_child_agent_helper,
    )


def _scoped_context(base: PluginContext | None, plugin: BasePlugin) -> PluginContext:
    """Build a plugin-scoped context so get_state/set_state are namespaced."""
    if base is None:
        return PluginContext(_plugin_name=getattr(plugin, "name", "unnamed"))
    return PluginContext(
        agent_name=base.agent_name,
        working_dir=base.working_dir,
        session_id=base.session_id,
        model=base.model,
        _host_agent=base._host_agent,
        _plugin_name=getattr(plugin, "name", "unnamed"),
        _spawn_child_agent_helper=base._spawn_child_agent_helper,
    )


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


class ToolVisibilityCollectorMixin:
    """Collect and merge enabled plugins' tool-visibility contributions."""

    def collect_tool_visibility(
        self, context: PluginContext | None = None
    ) -> ToolVisibility | None:
        """Merge per-plugin tool catalog restrictions.

        Multiple restrictions intersect per category so every contributor
        can only narrow, never widen, the catalog. Applicability and the
        plugin hook are evaluated against the current host model and a
        plugin-scoped context; failing plugins are skipped.
        """
        request_ctx = _request_context(
            context if context is not None else self._load_context
        )
        merged: ToolVisibility | None = None
        for plugin in self._active_plugins():
            if not _plugin_applies(plugin, request_ctx):
                continue
            plugin_ctx = _scoped_context(request_ctx, plugin)
            try:
                contributed = plugin.get_tool_visibility(plugin_ctx)
            except Exception as e:
                logger.warning(
                    "Plugin get_tool_visibility raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
                continue
            if contributed is None:
                continue
            if not isinstance(contributed, ToolVisibility):
                logger.warning(
                    "Plugin returned non-ToolVisibility; ignoring",
                    plugin_name=getattr(plugin, "name", "?"),
                    returned_type=type(contributed).__name__,
                )
                continue
            merged = _merge_visibility(merged, contributed)
        return merged
