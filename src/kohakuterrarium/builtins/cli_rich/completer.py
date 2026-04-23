"""Slash command completer for the rich CLI composer.

When the buffer starts with ``/``, the completer suggests builtin user
commands and their aliases (with descriptions in the meta column).

After the command name, if the command exposes an
``async def get_completions(arg_text, ctx) -> list[(text, description)]``
method, those suggestions are surfaced as well.
"""

from typing import Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from kohakuterrarium.llm.presets import iter_all_presets


def _model_completions(prefix: str, agent: Any | None = None) -> list[tuple[str, str]]:
    """Suggest LLM profile names for /model.

    Suggestions use ``provider/name`` form — the unambiguous identifier
    under the (provider, name) hierarchy. A bare-name prefix match still
    works because ``provider/name`` starts with the provider, so users
    typing ``claude-opus`` also see ``anthropic/claude-opus-4.7`` and
    ``openrouter/claude-opus-4.7``.
    """
    out: list[tuple[str, str]] = []
    for provider, name, data in iter_all_presets():
        identifier = f"{provider}/{name}"
        if not (identifier.startswith(prefix) or name.startswith(prefix)):
            continue
        meta = data.get("model", "") if isinstance(data, dict) else ""
        out.append((identifier, meta))
    return out


def _plugin_completions(prefix: str, agent: Any | None = None) -> list[tuple[str, str]]:
    """Suggest /plugin subcommands and plugin names from live agent state."""
    suggestions: list[tuple[str, str]] = []
    stripped = prefix.lstrip()

    subcommands = [
        ("enable", "Enable a plugin"),
        ("disable", "Disable a plugin"),
        ("toggle", "Toggle a plugin"),
    ]

    if not stripped or " " not in stripped:
        for name, meta in subcommands:
            if name.startswith(stripped):
                suggestions.append((name, meta))
        return suggestions

    subcmd, _, name_prefix = stripped.partition(" ")
    if subcmd not in {"enable", "disable", "toggle"}:
        return suggestions

    plugin_manager = getattr(agent, "plugins", None) if agent else None
    if not plugin_manager:
        return suggestions

    for plugin in plugin_manager.list_plugins():
        name = plugin.get("name", "")
        if not name.startswith(name_prefix):
            continue
        description = plugin.get("description") or ""
        status = "enabled" if plugin.get("enabled") else "disabled"
        suggestions.append(
            (f"{subcmd} {name}", f"{status} · {description}".strip(" ·"))
        )
    return suggestions


# Built-in argument completers — keyed by canonical command name.
_ARG_COMPLETERS = {
    "model": _model_completions,
    "plugin": _plugin_completions,
    "plugins": _plugin_completions,
}


class SlashCommandCompleter(Completer):
    """prompt_toolkit Completer for builtin slash commands and arguments."""

    def __init__(self, registry: dict | None = None, agent: Any | None = None):
        # registry: name -> command instance (with .name, .description, .aliases)
        self._registry = registry or {}
        self._agent = agent

    def set_registry(self, registry: dict) -> None:
        self._registry = registry

    def set_agent(self, agent: Any | None) -> None:
        self._agent = agent

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
                suggestions = arg_completer(arg_part, self._agent)
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
