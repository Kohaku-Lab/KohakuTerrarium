"""Custom webhook alert output module."""

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class WebhookAlertOutput(OutputModule):
    """Buffer monitor output and post each flush to an optional webhook."""

    def __init__(self, webhook_url: str = "", **options: Any):
        self.webhook_url = webhook_url
        self._buffer: list[str] = []

    async def start(self) -> None:
        logger.info("Alert output initialized", webhook_url=self.webhook_url)

    async def stop(self) -> None:
        pass

    async def write(self, text: str) -> None:
        self._buffer.append(text)

    async def write_stream(self, chunk: str) -> None:
        self._buffer.append(chunk)

    async def flush(self) -> None:
        if not self._buffer:
            return

        content = "".join(self._buffer)
        self._buffer.clear()

        if not self.webhook_url:
            logger.warning("No webhook URL configured, alert logged only")
            logger.info("ALERT: %s", content[:200])
            return

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    self.webhook_url,
                    json={"text": content, "source": "monitor_agent"},
                )
            logger.info("Alert sent to webhook")
        except Exception as e:
            logger.error("Failed to send alert", error=str(e))
