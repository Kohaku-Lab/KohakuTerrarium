"""Load, render, and compose system prompts with optional plugins."""

from kohakuterrarium.prompt.aggregator import (
    aggregate_system_prompt,
    aggregate_with_plugins,
    build_context_message,
)
from kohakuterrarium.prompt.loader import (
    load_prompt,
    load_prompt_with_fallback,
    load_prompts_folder,
)
from kohakuterrarium.prompt.plugins import (
    BasePlugin,
    EnvInfoPlugin,
    FrameworkHintsPlugin,
    PluginContext,
    ProjectInstructionsPlugin,
    PromptPlugin,
    ToolListPlugin,
    create_plugin,
    get_default_plugins,
    get_swe_plugins,
)
from kohakuterrarium.prompt.template import (
    PromptTemplate,
    render_template,
    render_template_safe,
)

__all__ = [
    "load_prompt",
    "load_prompts_folder",
    "load_prompt_with_fallback",
    "render_template",
    "render_template_safe",
    "PromptTemplate",
    "aggregate_system_prompt",
    "aggregate_with_plugins",
    "build_context_message",
    "PromptPlugin",
    "BasePlugin",
    "PluginContext",
    "ToolListPlugin",
    "FrameworkHintsPlugin",
    "EnvInfoPlugin",
    "ProjectInstructionsPlugin",
    "create_plugin",
    "get_default_plugins",
    "get_swe_plugins",
]
