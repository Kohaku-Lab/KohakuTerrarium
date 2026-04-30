"""Multi-output router subclass.

A thin extension of :class:`OutputRouter` that owns an extra named
output map and exposes ``write_to(name, content)`` for direct writes
to a specific module. Lives in its own file so the main ``router.py``
stays small.
"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MultiOutputRouter(OutputRouter):
    """Router that can route to multiple output modules.

    Different content types can go to different destinations via
    :meth:`write_to`. Lifecycle (start / stop / flush) cascades to
    every output module owned by this router.
    """

    def __init__(
        self,
        default_output: OutputModule,
        outputs: dict[str, OutputModule] | None = None,
        **kwargs: Any,
    ):
        """Initialize multi-output router.

        Args:
            default_output: Default output module
            outputs: Named output modules for specific content types
            **kwargs: Additional arguments for base router
        """
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
        """Write to a specific named output.

        Args:
            name: Output module name
            content: Content to write
        """
        if name in self.outputs:
            await self.outputs[name].write(content)
        else:
            logger.warning("Unknown output module", output_name=name)

    async def flush(self) -> None:
        """Flush all output modules."""
        await super().flush()
        for output in self.outputs.values():
            await output.flush()
