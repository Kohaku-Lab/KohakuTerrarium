"""Load, render, and compose system prompts."""

from kohakuterrarium.prompt.aggregator import (
    aggregate_system_prompt,
    build_context_message,
)
from kohakuterrarium.prompt.loader import (
    load_prompt,
    load_prompt_with_fallback,
    load_prompts_folder,
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
    "build_context_message",
]
