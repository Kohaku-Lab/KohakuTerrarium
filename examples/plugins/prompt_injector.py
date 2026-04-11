"""Prompt Injector Plugin — inject context into every LLM call.

Demonstrates:
  - pre_llm_call: modify the messages list before it reaches the LLM
  - Dynamic context: inject timestamps, scratchpad data, or environment info
  - Message format: how to add system messages to the conversation

Usage in config.yaml:
    plugins:
      - name: prompt_injector
        type: custom
        module: examples.plugins.prompt_injector
        class: PromptInjectorPlugin
        options:
          rules:
            - "Always respond in formal English."
            - "Include source references when making factual claims."
          inject_timestamp: true
          inject_cwd: true
"""

import time
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class PromptInjectorPlugin(BasePlugin):
    name = "prompt_injector"
    priority = 90  # Run late in pre_llm_call so other plugins inject first

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._rules: list[str] = opts.get("rules", [])
        self._inject_timestamp = bool(opts.get("inject_timestamp", False))
        self._inject_cwd = bool(opts.get("inject_cwd", False))
        self._ctx: PluginContext | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._ctx = context

    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None:
        """Inject additional context as a system message before the LLM call.

        The messages list is the full conversation that will be sent.
        We prepend or append system messages with extra context.

        Return the modified list to replace it, or None to keep unchanged.
        """
        parts: list[str] = []

        # Static rules from config
        if self._rules:
            parts.append("Rules:\n" + "\n".join(f"- {r}" for r in self._rules))

        # Dynamic context
        if self._inject_timestamp:
            parts.append(f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        if self._inject_cwd and self._ctx:
            parts.append(f"Working directory: {self._ctx.working_dir}")

        if not parts:
            return None

        # Create a system message with the injected context
        injection = {
            "role": "system",
            "content": "[Plugin: prompt_injector]\n" + "\n".join(parts),
        }

        # Insert after the first system message (the main system prompt)
        # so it doesn't override the agent's personality.
        insert_idx = 1
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
                break

        modified = list(messages)
        modified.insert(insert_idx, injection)

        logger.debug(
            "Injected context",
            parts=len(parts),
            insert_at=insert_idx,
        )
        return modified
