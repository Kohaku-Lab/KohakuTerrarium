"""Runtime plugin prompt-contribution tests for the prompt aggregator."""

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.prompt.aggregator import aggregate_system_prompt


class _PromptPlugin(BasePlugin):
    name = "prompt"

    def __init__(self, name: str, priority: int, content: str | None):
        super().__init__()
        self.name = name
        self.priority = priority
        self._content = content

    def get_prompt_content(self, context: PluginContext) -> str | None:
        return self._content


class _BuggyPromptPlugin(BasePlugin):
    name = "buggy"
    priority = 5

    def get_prompt_content(self, context: PluginContext) -> str | None:
        raise RuntimeError("boom")


def _manager(*plugins: BasePlugin) -> PluginManager:
    manager = PluginManager()
    for plugin in plugins:
        manager.register(plugin)
    return manager


def test_runtime_prompt_contributions_are_priority_ordered():
    manager = _manager(
        _PromptPlugin("late", 20, "late contribution"),
        _PromptPlugin("early", 10, "early contribution"),
    )
    prompt = aggregate_system_prompt(
        base_prompt="base",
        include_tools=False,
        include_hints=False,
        runtime_plugins=manager,
        plugin_context=PluginContext(agent_name="a"),
    )

    assert prompt.index("early contribution") < prompt.index("late contribution")


def test_empty_runtime_prompt_contributions_are_skipped():
    manager = _manager(_PromptPlugin("empty", 10, ""), _PromptPlugin("none", 20, None))
    prompt = aggregate_system_prompt(
        base_prompt="base",
        include_tools=False,
        include_hints=False,
        runtime_plugins=manager,
        plugin_context=PluginContext(agent_name="a"),
    )

    assert prompt == "base"


def test_runtime_prompt_contribution_errors_are_skipped():
    manager = _manager(
        _BuggyPromptPlugin(),
        _PromptPlugin("good", 10, "good contribution"),
    )
    prompt = aggregate_system_prompt(
        base_prompt="base",
        include_tools=False,
        include_hints=False,
        runtime_plugins=manager,
        plugin_context=PluginContext(agent_name="a"),
    )

    assert "good contribution" in prompt
    assert "boom" not in prompt
