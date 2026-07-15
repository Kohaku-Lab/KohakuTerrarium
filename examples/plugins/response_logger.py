"""Response Logger Plugin — log every LLM response and key events to a file.

Demonstrates:
  - post_llm_call: observe LLM responses (cannot modify)
  - on_event: observe every incoming trigger event
  - on_interrupt: detect user interrupts
  - on_compact_end: detect context compaction

Usage in config.yaml:
    plugins:
      - name: response_logger
        type: custom
        module: examples.plugins.response_logger
        class: ResponseLoggerPlugin
        options:
          path: ./logs/responses.log
          include_full_response: false
          max_preview: 500
"""

import time
from pathlib import Path
from typing import Any, TextIO

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ResponseLoggerPlugin(BasePlugin):
    """Append LLM, event, interrupt, and compaction observations to a file."""

    name = "response_logger"
    priority = 95  # Late observation captures prior plugin transformations.

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._log_path = Path(opts.get("path", "./logs/responses.log"))
        self._full = bool(opts.get("include_full_response", False))
        self._max_preview = int(opts.get("max_preview", 500))
        self._file: TextIO | None = None

    def _write(self, line: str) -> None:
        """Append and flush one timestamped line when the log is open."""
        if self._file:
            ts = time.strftime("%H:%M:%S")
            self._file.write(f"[{ts}] {line}\n")
            self._file.flush()

    async def on_load(self, context: PluginContext) -> None:
        """Open the append-only response log and mark session start."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._log_path, "a", encoding="utf-8")
        self._write(f"=== Session started: {context.agent_name} ===")

    async def on_unload(self) -> None:
        """Mark session end and close the response log."""
        self._write("=== Session ended ===")
        if self._file:
            self._file.close()
            self._file = None

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        """Log model usage and a bounded or complete response preview."""
        model = kwargs.get("model", "unknown")
        prompt_tok = usage.get("prompt_tokens", 0)
        completion_tok = usage.get("completion_tokens", 0)

        preview = response
        if not self._full and len(response) > self._max_preview:
            preview = response[: self._max_preview] + "..."

        self._write(
            f"LLM [{model}] {prompt_tok}→{completion_tok} tokens | " f"{preview}"
        )

    async def on_event(self, event: Any = None) -> None:
        """Log the type of each incoming trigger event."""
        event_type = getattr(event, "type", "unknown") if event else "unknown"
        self._write(f"EVENT {event_type}")

    async def on_interrupt(self) -> None:
        """Record that the current operation was interrupted by the user."""
        self._write("INTERRUPT — user cancelled current operation")

    async def on_compact_end(self, summary: str, messages_removed: int) -> None:
        """Record compaction size and a bounded summary preview."""
        self._write(
            f"COMPACT removed {messages_removed} messages | "
            f"summary: {summary[:200]}"
        )
