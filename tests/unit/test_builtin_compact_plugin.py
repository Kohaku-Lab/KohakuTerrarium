"""Unit tests for the built-in auto-compact runtime plugin."""

import pytest

from kohakuterrarium.builtins.plugins.compact.auto import AutoCompactPlugin
from kohakuterrarium.modules.plugin.base import PluginContext


class _Manager:
    def __init__(self):
        self.checked: list[int] = []
        self.trigger_calls = 0
        self.should = True

    def should_compact(self, prompt_tokens: int) -> bool:
        self.checked.append(prompt_tokens)
        return self.should

    def trigger_compact(self) -> bool:
        self.trigger_calls += 1
        return True


class _Host:
    def __init__(self, manager):
        self.compact_manager = manager


@pytest.mark.asyncio
async def test_auto_compact_triggers_when_manager_says_yes():
    manager = _Manager()
    plugin = AutoCompactPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(manager)))

    await plugin.post_llm_call([], "", {"prompt_tokens": 900})

    assert manager.checked == [900]
    assert manager.trigger_calls == 1


@pytest.mark.asyncio
async def test_auto_compact_does_not_trigger_below_threshold():
    manager = _Manager()
    manager.should = False
    plugin = AutoCompactPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(manager)))

    await plugin.post_llm_call([], "", {"prompt_tokens": 10})

    assert manager.checked == [10]
    assert manager.trigger_calls == 0


@pytest.mark.asyncio
async def test_auto_compact_ignores_missing_usage_or_manager():
    manager = _Manager()
    plugin = AutoCompactPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(manager)))

    await plugin.post_llm_call([], "", None)  # type: ignore[arg-type]
    assert manager.trigger_calls == 0

    plugin = AutoCompactPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(None)))
    await plugin.post_llm_call([], "", {"prompt_tokens": 999})
