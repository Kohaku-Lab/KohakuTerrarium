"""Synchronize provider-offered tools after a live model switch."""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _apply_saved_options(agent: Any, *, native: bool, local: bool) -> None:
    """Restore saved options for tools that just became available."""
    for enabled, attr in (
        (native, "native_tool_options"),
        (local, "tool_options"),
    ):
        options = getattr(agent, attr, None)
        if not enabled or options is None:
            continue
        try:
            options.apply()
        except Exception as exc:  # pragma: no cover - options must not break switching
            logger.warning(
                "provider_tool_option_apply_skipped",
                option_kind=attr,
                error=str(exc),
                exc_info=True,
            )


def sync_provider_tools(agent: Any) -> None:
    """Match automatic tools to the active provider and refresh the prompt."""
    registry = getattr(agent, "registry", None)
    executor = getattr(agent, "executor", None)
    if registry is None or executor is None:
        return

    llm = getattr(agent, "llm", None)
    provider = getattr(llm, "provider_name", "") if llm else ""
    offered = set(getattr(llm, "provider_native_tools", ()) if llm else ())
    offered.difference_update(agent.config.disable_provider_tools or ())
    automatic = set(getattr(agent, "_auto_provider_tools", set()))

    stale = automatic - offered
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        support = getattr(tool, "provider_support", frozenset()) if tool else ()
        if getattr(tool, "is_provider_native", False) and provider not in support:
            stale.add(name)

    changed = False
    for name in sorted(stale):
        registry.unregister_tool(name)
        executor.unregister_tool(name)
        automatic.discard(name)
        changed = True
        logger.info("provider_tool_removed", tool_name=name, active_provider=provider)

    added_native = False
    added_local = False
    for name in sorted(offered):
        if registry.get_tool(name) is not None:
            continue
        tool = get_builtin_tool(name)
        if tool is None:
            logger.warning(
                "provider_native_tool_not_in_catalog",
                tool_name=name,
                active_provider=provider,
            )
            continue
        registry.register_tool(tool)
        executor.register_tool(tool)
        automatic.add(name)
        changed = True
        is_native = bool(getattr(tool, "is_provider_native", False))
        added_native = added_native or is_native
        added_local = added_local or not is_native
        logger.info(
            "provider_tool_injected_after_switch",
            tool_name=name,
            active_provider=provider,
        )

    agent._auto_provider_tools = automatic
    if changed:
        _apply_saved_options(agent, native=added_native, local=added_local)
        agent.refresh_system_prompt()
