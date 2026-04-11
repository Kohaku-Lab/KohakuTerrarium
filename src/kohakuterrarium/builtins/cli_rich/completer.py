"""Slash command completer for the rich CLI composer.

When the buffer starts with ``/``, the completer suggests builtin user
commands and their aliases (with descriptions in the meta column).

After the command name, if the command exposes an
``async def get_completions(arg_text, ctx) -> list[(text, description)]``
method, those suggestions are surfaced as well.
"""

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from kohakuterrarium.llm.presets import PRESETS


def _model_completions(prefix: str) -> list[tuple[str, str]]:
    """Suggest LLM profile names for /model."""
    out: list[tuple[str, str]] = []
    for name, preset in PRESETS.items():
        if not name.startswith(prefix):
            continue
        meta = ""
        if isinstance(preset, dict):
            meta = preset.get("model", "") or preset.get("provider", "")
        out.append((name, meta))
    return out


# Built-in argument completers — keyed by canonical command name.
_ARG_COMPLETERS = {
    "model": _model_completions,
}


class SlashCommandCompleter(Completer):
    """prompt_toolkit Completer for builtin slash commands and arguments."""

    def __init__(self, registry: dict | None = None):
        # registry: name -> command instance (with .name, .description, .aliases)
        self._registry = registry or {}

    def set_registry(self, registry: dict) -> None:
        self._registry = registry

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # Past the command name → argument completion
        if " " in text:
            cmd_part, _, arg_part = text[1:].partition(" ")
            cmd_name = cmd_part.lower()
            arg_completer = _ARG_COMPLETERS.get(cmd_name)
            if arg_completer is None:
                return
            try:
                suggestions = arg_completer(arg_part)
            except Exception as e:
                _ = e  # fallback: completer must not raise into prompt_toolkit
                return
            for value, meta in suggestions:
                yield Completion(
                    text=value,
                    start_position=-len(arg_part),
                    display=value,
                    display_meta=meta,
                )
            return

        # Command name completion
        prefix = text[1:].lower()  # Strip leading "/"
        seen: set[str] = set()
        for name, cmd in self._registry.items():
            if name.startswith(prefix):
                seen.add(name)
                yield Completion(
                    text=name,
                    start_position=-len(prefix),
                    display=f"/{name}",
                    display_meta=getattr(cmd, "description", ""),
                )
            for alias in getattr(cmd, "aliases", []) or []:
                if alias in seen:
                    continue
                if alias.startswith(prefix):
                    seen.add(alias)
                    yield Completion(
                        text=alias,
                        start_position=-len(prefix),
                        display=f"/{alias}",
                        display_meta=f"alias for /{name}",
                    )
