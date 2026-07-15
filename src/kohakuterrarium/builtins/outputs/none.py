"""Discard module-level output for headless and batch execution.

Router observers and session stores still receive events; only the default sink
is suppressed to prevent concurrent console interleaving.
"""

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class NoneOutput(BaseOutputModule):
    """Discard sink writes without suppressing router-level observers."""

    # No prompt can be rendered, so interactive tools must not wait for replies.
    supports_interactive: bool = False

    async def _on_start(self) -> None:
        logger.debug("None output started (all writes discarded)")

    async def _on_stop(self) -> None:
        logger.debug("None output stopped")

    async def write(self, content: str) -> None:
        """Discard complete content."""

    async def write_stream(self, chunk: str) -> None:
        """Discard a streaming chunk."""

    async def flush(self) -> None:
        """Complete the no-op flush contract."""
