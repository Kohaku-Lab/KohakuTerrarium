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
    name = "response_logger"
    priority = 95  # Run last — observe final state

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._log_path = Path(opts.get("path", "./logs/responses.log"))
        self._full = bool(opts.get("include_full_response", False))
        self._max_preview = int(opts.get("max_preview", 500))
        self._file: TextIO | None = None

    def _write(self, line: str) -> None:
        if self._file:
            ts = time.strftime("%H:%M:%S")
            self._file.write(f"[{ts}] {line}\n")
            self._file.flush()

    async def on_load(self, context: PluginContext) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._log_path, "a", encoding="utf-8")
        self._write(f"=== Session started: {context.agent_name} ===")

    async def on_unload(self) -> None:
        self._write("=== Session ended ===")
        if self._file:
            self._file.close()
            self._file = None

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        """Called after every LLM response. Observation only — cannot modify.

        Args:
            messages: the conversation that was sent
            response: the raw text response from the LLM
            usage: dict with prompt_tokens, completion_tokens, cached_tokens
            **kwargs: model (str)
        """
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
        """Called on every incoming trigger event (user input, tool result, etc.)."""
        event_type = getattr(event, "type", "unknown") if event else "unknown"
        self._write(f"EVENT {event_type}")

    async def on_interrupt(self) -> None:
        """Called when the user interrupts (Escape / Ctrl+C)."""
        self._write("INTERRUPT — user cancelled current operation")

    async def on_compact_end(self, summary: str, messages_removed: int) -> None:
        """Called after context compaction finishes."""
        self._write(
            f"COMPACT removed {messages_removed} messages | "
            f"summary: {summary[:200]}"
        )
