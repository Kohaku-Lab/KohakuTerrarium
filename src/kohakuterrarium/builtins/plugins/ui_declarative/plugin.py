"""Opt-in inline UI: the model writes ``kt.ui`` components inside its replies.

The plugin adds the inline-components part of ``## Presenting your answer``,
present only while an attached client reports the ``kt.ui`` renderer. Common
components are described inline; the rest are named, and ``info("ui:<name>")``
returns their props and an example. When a reply's block fails to convert or
validate, the next model request carries a short note naming the block and
its errors.
"""

import hashlib
import re
from typing import Any

from kohakuterrarium.builtins.kt_ui.markup import MarkupError, check_block
from kohakuterrarium.builtins.kt_ui.markup_lex import split_message
from kohakuterrarium.builtins.kt_ui.spec import SpecError
from kohakuterrarium.builtins.kt_ui.components import COMPONENTS
from kohakuterrarium.builtins.plugins.ui_declarative.reference import (
    EXAMPLES,
    component_doc,
    component_line,
    inline_ui_section,
)
from kohakuterrarium.core.tools.gate import visible_tool_names
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext

RENDERER = "kt.ui"
# ``html`` runs in the app frame, so it is taught only with a ``kt.app`` client.
APP_RENDERER = "kt.app"
MAX_NOTED = 256
NOTED_STATE_KEY = "noted"
MAX_REASON_CHARS = 300
MAX_NOTE_COMPONENTS = 3
_TAG_RE = re.compile(r"<([a-z_]+)[\s>]")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def block_failures(text: str) -> list[tuple[int, str]]:
    """``(block number, reason)`` for every ``<ui>`` block in ``text`` that fails."""
    failures: list[tuple[int, str]] = []
    number = 0
    for segment in split_message(text):
        if segment["kind"] != "ui":
            continue
        number += 1
        try:
            check_block(segment["source"])
        except (MarkupError, SpecError) as exc:
            reason = str(exc)
            if len(reason) > MAX_REASON_CHARS:
                reason = reason[: MAX_REASON_CHARS - 1] + "…"
            failures.append((number, reason))
    return failures


def failure_note(failures: list[tuple[int, str]]) -> str:
    """The note the model reads about its failed blocks, with the props of the components named."""
    lines = ["[inline UI] Some <ui> blocks in your last reply could not be drawn:"]
    lines += [f"- block {n}: {reason}" for n, reason in failures]
    named = [
        name
        for name in dict.fromkeys(
            m for _, reason in failures for m in _TAG_RE.findall(reason)
        )
        if name in COMPONENTS
    ][:MAX_NOTE_COMPONENTS]
    if named:
        lines.append("How to write them:")
        for name in named:
            lines.append(component_line(name))
            if name in EXAMPLES:
                lines.append(f"  e.g. {EXAMPLES[name].splitlines()[0]}")
    lines.append(
        "The user saw an error box for these; fix them when you write UI again."
    )
    return "\n".join(lines)


class UiDeclarativePlugin(BasePlugin):
    """Teaches inline ``<ui>`` components and reports blocks that failed."""

    name = "ui.declarative"
    description = "Inline UI: the model writes tables, charts, diagrams and controls in its replies."
    priority = 50
    prompt_renderers = frozenset({RENDERER, APP_RENDERER})
    doc_prefix = "ui"

    def __init__(self, **_extra: Any) -> None:
        super().__init__()
        self._noted: dict[str, None] = {}
        self._context: PluginContext | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._context = context
        saved = context.get_state(NOTED_STATE_KEY)
        if isinstance(saved, list):
            self._noted = {key: None for key in saved if isinstance(key, str)}

    def presentation_contribution(self, context: PluginContext) -> str | None:
        renderers = attached_renderers(context.host_agent)
        if RENDERER not in renderers:
            return None
        return inline_ui_section(
            on_demand=info_available(context.host_agent),
            html=APP_RENDERER in renderers,
        )

    def doc_topics(self, context: PluginContext | None) -> list[str]:
        return list(COMPONENTS)

    def doc_topic(self, topic: str, context: PluginContext | None) -> str | None:
        return component_doc(topic)

    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None:
        last_assistant = next(
            (
                i
                for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") == "assistant"
            ),
            None,
        )
        if last_assistant is None or last_assistant == len(messages) - 1:
            return None
        text = _text_of(messages[last_assistant].get("content"))
        if "<ui" not in text:
            return None
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in self._noted:
            return None
        self._noted[key] = None
        while len(self._noted) > MAX_NOTED:
            self._noted.pop(next(iter(self._noted)))
        if self._context is not None:
            self._context.set_state(NOTED_STATE_KEY, list(self._noted))
        failures = block_failures(text)
        if not failures:
            return None
        return _append_note(messages, failure_note(failures))


def attached_renderers(agent: Any) -> dict[str, int]:
    """Renderers the agent's attached outputs report (empty without a router)."""
    router = getattr(agent, "output_router", None)
    renderers = getattr(router, "ui_renderers", None)
    return renderers() if callable(renderers) else {}


def info_available(agent: Any) -> bool:
    """Whether the model can call ``info``: a text format, or a visible ``info`` tool."""
    config = getattr(agent, "config", None)
    if getattr(config, "tool_format", "native") != "native":
        return True
    catalog = getattr(agent, "_visible_catalog", None)
    registry = getattr(agent, "registry", None)
    if not callable(catalog) or registry is None:
        return False
    return "info" in visible_tool_names(registry, catalog())


def _append_note(messages: list[dict], note: str) -> list[dict]:
    """A copy of ``messages`` with ``note`` added to the last message."""
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, list):
        last["content"] = [*content, {"type": "text", "text": note}]
    else:
        last["content"] = f"{content or ''}\n\n{note}".lstrip()
    out[-1] = last
    return out
