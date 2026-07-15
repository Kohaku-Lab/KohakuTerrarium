"""Multi-output router subclass.

An additional named-output map supports direct writes with cascaded lifecycle.
"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MultiOutputRouter(OutputRouter):
    """Route direct named writes and cascade lifecycle to all owned outputs."""

    def __init__(
        self,
        default_output: OutputModule,
        outputs: dict[str, OutputModule] | None = None,
        **kwargs: Any,
    ):
        """Initialize the base router and its direct-write output map."""
        super().__init__(default_output, **kwargs)
        self.outputs = outputs or {}

    async def start(self) -> None:
        """Start all output modules."""
        await super().start()
        for output in self.outputs.values():
            await output.start()

    async def stop(self) -> None:
        """Stop all output modules."""
        for output in self.outputs.values():
            await output.stop()
        await super().stop()

    async def write_to(self, name: str, content: str) -> None:
        """Write content directly to a named output when registered."""
        if name in self.outputs:
            await self.outputs[name].write(content)
        else:
            logger.warning("Unknown output module", output_name=name)

    async def flush(self) -> None:
        """Flush all output modules."""
        await super().flush()
        for output in self.outputs.values():
            await output.flush()
