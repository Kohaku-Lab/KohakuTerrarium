"""The ``## Presenting your answer`` framework section.

Plugins contribute parts through ``BasePlugin.presentation_contribution``
(inline components, full-page apps). The section exists only when at least
one part does, and sits right after the base prompt.
"""

from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.plugin.manager.manager import PluginManager

HEADING = "## Presenting your answer"
OPENING = (
    "This chat shows more than text. Before you answer, choose the form that "
    "serves the user best."
)
CLOSING = "Plain Markdown is for short answers and prose."


def build_presentation_section(
    plugins: PluginManager | None, context: PluginContext | None
) -> str:
    """The section text, or ``""`` when no plugin contributes a part."""
    if plugins is None or context is None:
        return ""
    parts = plugins.collect_presentation_contributions(context)
    if not parts:
        return ""
    return "\n\n".join([HEADING, OPENING, *parts, CLOSING])


__all__ = ["CLOSING", "HEADING", "OPENING", "build_presentation_section"]
