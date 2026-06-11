"""Null output module — discards everything.

The output-side mirror of :class:`builtins.inputs.none.NoneInput`.
Used by headless / batch runs (``io="headless"``) where agent text is
consumed through the typed event stream or a session store, and any
console writes would just interleave garbage across N concurrent
agents.
"""

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class NoneOutput(BaseOutputModule):
    """Output module that silently discards all writes.

    Observers (session stores, attach streams, ``run_stream``
    consumers) tap the output *router*, not the default module — so a
    NoneOutput default loses nothing except console spam.
    """

    async def _on_start(self) -> None:
        logger.debug("None output started (all writes discarded)")

    async def _on_stop(self) -> None:
        logger.debug("None output stopped")

    async def write(self, content: str) -> None:
        """Discard complete content."""

    async def write_stream(self, chunk: str) -> None:
        """Discard a streaming chunk."""

    async def flush(self) -> None:
        """Nothing buffered — nothing to flush."""
