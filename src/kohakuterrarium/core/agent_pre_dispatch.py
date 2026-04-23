"""Pre-tool-dispatch plugin chain.

Extracted from ``agent_handlers.py`` to keep that file under the
600-line soft cap. Implements cluster B.2 of the extension-point spec:
plugins can rewrite a ``ToolCallEvent`` between parser emission and
executor dispatch, or veto the call entirely via ``PluginBlockError``.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.core.events import TriggerEvent, create_tool_complete_event
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.core.controller import Controller

logger = get_logger(__name__)


async def run_pre_tool_dispatch(
    agent: "Agent", parse_event: ToolCallEvent, controller: "Controller"
) -> ToolCallEvent | None:
    """Run the ``pre_tool_dispatch`` plugin chain.

    Returns the (possibly rewritten) ToolCallEvent to dispatch, or
    ``None`` if the call was vetoed — in which case the block error
    is synthesised into the conversation as the tool result so the
    model sees a sensible next turn.
    """
    plugins = getattr(agent, "plugins", None)
    if plugins is None or not plugins._plugins:
        return parse_event

    applicable = plugins._applicable_plugins()
    base_method = getattr(BasePlugin, "pre_tool_dispatch", None)
    hook_plugins = [
        p
        for p in applicable
        if getattr(type(p), "pre_tool_dispatch", None) not in (None, base_method)
    ]
    if not hook_plugins:
        return parse_event

    current = parse_event
    for plugin in hook_plugins:
        plugin_name = getattr(plugin, "name", "?")
        wd = (
            Path(getattr(agent.executor, "_working_dir", "."))
            if agent.executor
            else Path(".")
        )
        ctx = PluginContext(
            agent_name=agent.config.name,
            working_dir=wd,
            model=getattr(agent.llm, "model", ""),
            _host_agent=agent,
            _plugin_name=plugin_name,
        )
        try:
            rewritten = await plugin.pre_tool_dispatch(current, ctx)
        except PluginBlockError as block:
            logger.info(
                "Tool call vetoed by plugin",
                plugin_name=plugin_name,
                tool_name=current.name,
            )
            _synthesize_blocked_tool_result(
                current, controller, str(block), plugin_name
            )
            return None
        except Exception as e:
            logger.warning(
                "pre_tool_dispatch raised",
                plugin_name=plugin_name,
                error=str(e),
                exc_info=True,
            )
            continue
        if rewritten is not None:
            if not isinstance(rewritten, ToolCallEvent):
                logger.warning(
                    "pre_tool_dispatch returned non-ToolCallEvent; ignoring",
                    plugin_name=plugin_name,
                    returned_type=type(rewritten).__name__,
                )
                continue
            current = rewritten

    # Verify the (possibly renamed) tool still resolves against the
    # registry — otherwise treat it as a veto with a descriptive
    # error so the model doesn't hit a generic "unknown tool".
    if current.name != parse_event.name:
        known = agent.registry.list_tools() if agent.registry else []
        if current.name not in known:
            logger.warning(
                "pre_tool_dispatch rewrote to unknown tool",
                original=parse_event.name,
                rewritten=current.name,
            )
            _synthesize_blocked_tool_result(
                parse_event,
                controller,
                f"unknown tool after rewrite: {current.name}",
                "pre_tool_dispatch",
            )
            return None
    return current


def _synthesize_blocked_tool_result(
    original_event: ToolCallEvent,
    controller: "Controller",
    message: str,
    plugin_name: str,
) -> None:
    """Inject a synthetic tool result when pre_tool_dispatch vetoes."""
    tool_call_id = original_event.args.get("_tool_call_id") or ""
    native_mode = getattr(controller.config, "tool_format", None) == "native"
    error_text = f"[{plugin_name}] {message}"
    if native_mode and tool_call_id:
        controller.conversation.append(
            "tool",
            error_text,
            tool_call_id=tool_call_id,
            name=original_event.name,
        )
        controller.push_event_sync(TriggerEvent(type="tool_complete", content=""))
    else:
        controller.push_event_sync(
            create_tool_complete_event(
                job_id=f"blocked_{original_event.name}",
                content=error_text,
                exit_code=1,
                error=error_text,
            )
        )


__all__: tuple[str, ...] = ("run_pre_tool_dispatch",)
