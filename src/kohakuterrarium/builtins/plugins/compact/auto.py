"""Automatic compaction plugin."""

from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class AutoCompactPlugin(BasePlugin):
    """Trigger the host compact manager after high-token LLM calls."""

    name = "compact.auto"
    priority = 30

    def __init__(self) -> None:
        super().__init__()
        self._manager: Any = None

    async def on_load(self, context: PluginContext) -> None:
        self._manager = context.compact_manager

    async def post_llm_call(
        self,
        messages: list[dict],
        response: str,
        usage: dict,
        **kwargs: Any,
    ) -> None:
        if self._manager is None or not isinstance(usage, dict):
            return None
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        if self._manager.should_compact(prompt_tokens):
            self._manager.trigger_compact()
        return None
