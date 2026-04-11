"""Webhook Notifier Plugin — send HTTP webhooks on key agent events.

Demonstrates:
  - Multiple callbacks: on_agent_start/stop, on_event, on_interrupt
  - on_compact_start/end: observe context compaction
  - inject_event: push synthetic events into the agent's queue
  - switch_model: change the LLM model at runtime
  - Async HTTP: use httpx for non-blocking webhook delivery

Usage in config.yaml:
    plugins:
      - name: webhook_notifier
        type: custom
        module: examples.plugins.webhook_notifier
        class: WebhookNotifierPlugin
        options:
          url: "https://hooks.example.com/agent-events"
          events: [agent_start, agent_stop, interrupt, compact]
          # Auto-downgrade model when context gets too large
          auto_downgrade_model: "gpt-5.4-mini"
          auto_downgrade_threshold: 100000  # tokens
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class WebhookNotifierPlugin(BasePlugin):
    name = "webhook_notifier"
    priority = 99  # Run last — observe final state

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._url = opts.get("url", "")
        self._enabled_events: set[str] = set(
            opts.get("events", ["agent_start", "agent_stop"])
        )
        self._downgrade_model = opts.get("auto_downgrade_model", "")
        self._downgrade_threshold = int(opts.get("auto_downgrade_threshold", 0))
        self._ctx: PluginContext | None = None
        self._original_model = ""

    async def _send(self, event_type: str, data: dict | None = None) -> None:
        """Fire-and-forget webhook delivery."""
        if not self._url:
            logger.debug("Webhook skipped (no URL)", event=event_type)
            return
        if event_type not in self._enabled_events:
            return

        payload = {
            "event": event_type,
            "agent": self._ctx.agent_name if self._ctx else "",
            **(data or {}),
        }

        # Non-blocking HTTP POST
        # Using httpx as an optional dependency — graceful fallback
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(self._url, json=payload)
                logger.debug("Webhook sent", event=event_type, status=resp.status_code)
        except ImportError:
            logger.warning("httpx not installed — webhook skipped")
        except Exception as e:
            logger.warning("Webhook failed", event=event_type, error=str(e))

    async def on_load(self, context: PluginContext) -> None:
        self._ctx = context
        self._original_model = context.model

    async def on_agent_start(self) -> None:
        await self._send("agent_start", {"model": self._original_model})

    async def on_agent_stop(self) -> None:
        await self._send("agent_stop")

    async def on_event(self, event: Any = None) -> None:
        """Observe every trigger event. Useful for external dashboards."""
        event_type = str(getattr(event, "type", "unknown")) if event else "unknown"
        await self._send("event", {"event_type": event_type})

    async def on_interrupt(self) -> None:
        await self._send("interrupt")

    async def on_compact_start(self, context_length: int) -> None:
        """Before compaction — optionally downgrade to a cheaper model."""
        await self._send(
            "compact", {"context_length": context_length, "phase": "start"}
        )

        # Auto-downgrade model when context is very large
        # This demonstrates switch_model(): you might want a cheaper model
        # for the compaction turn since it's just summarization.
        if (
            self._downgrade_model
            and self._downgrade_threshold
            and context_length > self._downgrade_threshold
            and self._ctx
        ):
            old = self._ctx.model
            self._ctx.switch_model(self._downgrade_model)
            logger.info(
                "Auto-downgraded model for compaction",
                old=old,
                new=self._downgrade_model,
                context_length=context_length,
            )

    async def on_compact_end(self, summary: str, messages_removed: int) -> None:
        """After compaction — restore original model if downgraded."""
        await self._send(
            "compact",
            {"phase": "end", "messages_removed": messages_removed},
        )

        # Restore original model after compaction
        if self._downgrade_model and self._ctx and self._original_model:
            self._ctx.switch_model(self._original_model)
            logger.info("Restored model after compaction", model=self._original_model)
