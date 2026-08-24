"""Unit tests for the built-in eager compaction plugin."""

from kohakuterrarium.builtins.plugins.compact.auto import AutoCompactPlugin
from kohakuterrarium.modules.plugin.base import PluginContext


class _CompactManager:
    def __init__(self, should_compact: bool) -> None:
        self.should = should_compact
        self.checked_tokens: list[int] = []
        self.trigger_count = 0

    def should_compact(self, prompt_tokens: int) -> bool:
        self.checked_tokens.append(prompt_tokens)
        return self.should

    def trigger_compact(self) -> bool:
        self.trigger_count += 1
        return True


class _Host:
    def __init__(self, compact_manager: _CompactManager) -> None:
        self.compact_manager = compact_manager


def test_description_explains_eager_and_deferred_compaction() -> None:
    description = AutoCompactPlugin.description.lower()

    assert "eager" in description
    assert "deferred" in description
    assert "sub-agents require it" in description
    assert "compact.enabled" in description


async def test_post_llm_hook_triggers_eager_compaction() -> None:
    manager = _CompactManager(should_compact=True)
    plugin = AutoCompactPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(manager)))

    await plugin.post_llm_call([], "", {"prompt_tokens": 900})

    assert manager.checked_tokens == [900]
    assert manager.trigger_count == 1


async def test_post_llm_hook_leaves_below_threshold_calls_alone() -> None:
    manager = _CompactManager(should_compact=False)
    plugin = AutoCompactPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(manager)))

    await plugin.post_llm_call([], "", {"prompt_tokens": 10})

    assert manager.checked_tokens == [10]
    assert manager.trigger_count == 0
