"""Agent lifecycle helpers.

Split out of :mod:`agent` to keep the main orchestrator file below the
repository file-size guard while keeping shutdown behavior centralized.
"""

import asyncio
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentLifecycleMixin:
    """Mixin providing agent shutdown behavior."""

    plugins: Any
    compact_manager: Any
    llm: Any
    output_router: Any
    subagent_manager: Any
    trigger_manager: Any
    input: Any
    config: Any
    _running: bool
    _shutdown_event: Any

    async def stop(self) -> None:
        """Stop all agent modules."""
        logger.info("Stopping agent", agent_name=self.config.name)

        if self.plugins:
            await self.plugins.notify("on_agent_stop")
            await self.plugins.unload_all()

        self._running = False
        self._shutdown_event.set()

        if hasattr(self, "_mcp_manager") and self._mcp_manager:
            await self._mcp_manager.shutdown()

        await self._cancel_executor_tasks()
        await self.subagent_manager.cancel_all()
        await self.trigger_manager.stop_all()
        await self.input.stop()
        if self.compact_manager:
            await self.compact_manager.cancel()
        await self.output_router.stop()
        compact_llm = (
            getattr(self.compact_manager, "_llm", None)
            if self.compact_manager
            else None
        )
        if (
            compact_llm is not None
            and compact_llm is not self.llm
            and hasattr(compact_llm, "close")
        ):
            await compact_llm.close()
        await self.llm.close()

    async def _cancel_executor_tasks(self) -> None:
        executor = getattr(self, "executor", None)
        if executor is None:
            return
        tasks = [task for task in executor._tasks.values() if not task.done()]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
