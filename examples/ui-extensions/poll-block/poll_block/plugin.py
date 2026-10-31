"""Teaches the model the ``demo.poll`` fence while a client that draws it is attached."""

from typing import Any

from kohakuterrarium.builtins.plugins.ui_declarative.plugin import attached_renderers
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext

RENDERER = "demo.poll"

PROMPT = f"""\
To ask the user a multiple-choice question, write a fenced block named `{RENDERER}`
holding JSON with a `question` and an `options` list of short strings:

```{RENDERER}
{{"question": "Which database?", "options": ["SQLite", "Postgres"]}}
```

The user sees buttons; the one they click comes back as their next message. Write
the question in the same JSON, not as separate prose."""


class PollBlockPlugin(BasePlugin):
    """Offers the poll format only when a client that renders it is attached."""

    name = "poll_block"
    description = "The model can ask multiple-choice questions as clickable polls."
    priority = 50
    prompt_renderers = frozenset({RENDERER})

    def __init__(self, **_extra: Any) -> None:
        super().__init__()

    def presentation_contribution(self, context: PluginContext) -> str | None:
        if RENDERER not in attached_renderers(context.host_agent):
            return None
        return PROMPT
