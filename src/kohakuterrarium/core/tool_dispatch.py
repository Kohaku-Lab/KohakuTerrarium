"""Common pre-dispatch policy, task start and promotion for tool callers."""

import asyncio

from kohakuterrarium.core.backgroundify import backgroundify
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ToolDispatchBlocked(PluginBlockError):
    """Policy rejection with caller-neutral metadata for result delivery."""

    def __init__(self, message, plugin_name, event):
        super().__init__(message)
        self.plugin_name = plugin_name
        self.event = event


async def dispatch_tool_hooks(plugins, parse_event, context, known_tools):
    """Apply the same transforming policy chain for every tool caller."""
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
        ctx = PluginContext(
            agent_name=context.agent_name,
            working_dir=context.working_dir,
            session_id=context.session_id,
            model=context.model,
            _host_agent=context.host_agent,
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
            raise ToolDispatchBlocked(str(block), plugin_name, current) from block
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
        known = known_tools
        if current.name not in known:
            logger.warning(
                "pre_tool_dispatch rewrote to unknown tool",
                original=parse_event.name,
                rewritten=current.name,
            )
            raise ToolDispatchBlocked(
                f"unknown tool after rewrite: {current.name}",
                "pre_tool_dispatch",
                parse_event,
            )
    return current


async def start_tool_task(executor, tool_call):
    """Start exactly one Executor job using the tool's declared mode."""
    try:
        logger.info("Running tool: %s", tool_call.name)
        tool = executor.get_tool(tool_call.name)
        is_direct = True
        if tool and isinstance(tool, BaseTool):
            is_direct = tool.execution_mode == ExecutionMode.DIRECT

        job_id = await executor.submit_from_event(tool_call, is_direct=is_direct)
        task = executor.get_task(job_id)
        if task is None:

            async def _get_result():
                return executor.get_result(job_id)

            task = asyncio.create_task(_get_result())

        return job_id, task, is_direct
    except Exception as e:
        logger.error("Failed to start tool", tool_name=tool_call.name, error=str(e))
        error_msg = str(e)
        error_job_id = f"error_{tool_call.name}"

        async def _error_result():
            return JobResult(job_id=error_job_id, error=error_msg)

        task = asyncio.create_task(_error_result())
        return error_job_id, task, True


def tool_background_handle(
    task, job_id, executor_direct, run_background, on_complete=None
):
    """Use one completion owner for declared-background and promoted jobs."""
    return backgroundify(
        task,
        job_id,
        on_bg_complete=on_complete if executor_direct else None,
        background_init=not executor_direct or bool(run_background),
    )
