"""
TerrariumToolManager - shared state for terrarium management tools.

Separated from terrarium_tools.py to avoid circular imports between
builtins.tools and terrarium.runtime.
"""

import asyncio
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Key used to store/retrieve the TerrariumToolManager in the environment
TERRARIUM_MANAGER_KEY = "terrarium_manager"


class TerrariumToolManager:
    """
    Lightweight manager for terrarium tools.

    Holds references to running TerrariumRuntimes. The root agent's
    initialization code registers an instance of this in the environment
    context so that tools can access it.
    """

    def __init__(self, runtime_factory: Any = None, runtime_class: Any = None) -> None:
        self._runtimes: dict[str, Any] = {}  # terrarium_id -> TerrariumRuntime
        self._tasks: dict[str, asyncio.Task] = {}
        self._runtime_factory = runtime_factory
        self._runtime_class = runtime_class

    def create_runtime(self, config_path: str) -> Any:
        """Create a TerrariumRuntime from a config path.

        Local import keeps the manager independent from the runtime at module
        load time while allowing lifecycle tools to stay out of the runtime
        dependency cycle.
        """
        if self._runtime_factory is None or self._runtime_class is None:
            raise RuntimeError("No terrarium runtime factory configured")
        return self._runtime_factory(config_path, self._runtime_class)

    def register_runtime(self, terrarium_id: str, runtime: Any) -> None:
        """Register a running terrarium runtime."""
        self._runtimes[terrarium_id] = runtime

    def get_runtime(self, terrarium_id: str) -> Any:
        """Get a runtime by ID. Raises KeyError if not found."""
        if terrarium_id not in self._runtimes:
            available = list(self._runtimes.keys())
            raise KeyError(
                f"Terrarium '{terrarium_id}' not found. "
                f"Available: {available or '(none)'}"
            )
        return self._runtimes[terrarium_id]

    def list_terrariums(self) -> list[str]:
        """List all registered terrarium IDs."""
        return list(self._runtimes.keys())

    def register_task(self, terrarium_id: str, task: asyncio.Task) -> None:
        """Track the asyncio task running a terrarium."""
        self._tasks[terrarium_id] = task

    async def stop_terrarium(self, terrarium_id: str) -> None:
        """Stop a terrarium and clean up."""
        runtime = self.get_runtime(terrarium_id)
        await runtime.stop()
        task = self._tasks.pop(terrarium_id, None)
        if task and not task.done():
            task.cancel()
        del self._runtimes[terrarium_id]
