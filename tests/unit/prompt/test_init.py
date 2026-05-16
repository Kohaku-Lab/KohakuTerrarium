"""Unit tests for :mod:`kohakuterrarium.prompt` package re-exports.

``__init__`` is the public surface other modules import from; the
``__all__`` list must stay in sync with the actual bound names so a
typo'd re-export fails here rather than at some distant import site.
"""

import kohakuterrarium.prompt as prompt_pkg


class TestPromptPackageExports:
    def test_all_names_are_actually_bound(self):
        for name in prompt_pkg.__all__:
            assert hasattr(prompt_pkg, name), f"{name} in __all__ but not bound"

    def test_aggregator_functions_exported(self):
        from kohakuterrarium.prompt.aggregator import aggregate_system_prompt

        assert prompt_pkg.aggregate_system_prompt is aggregate_system_prompt

    def test_loader_functions_exported(self):
        from kohakuterrarium.prompt.loader import load_prompt

        assert prompt_pkg.load_prompt is load_prompt

    def test_template_symbols_exported(self):
        from kohakuterrarium.prompt.template import PromptTemplate, render_template

        assert prompt_pkg.PromptTemplate is PromptTemplate
        assert prompt_pkg.render_template is render_template

    def test_plugin_symbols_exported(self):
        from kohakuterrarium.prompt.plugins import ToolListPlugin, get_default_plugins

        assert prompt_pkg.ToolListPlugin is ToolListPlugin
        assert prompt_pkg.get_default_plugins is get_default_plugins
