"""Slash commands in ktui: the shell's own (``LOCAL_COMMANDS``) and the focused creature's
registry, listed in the composer's ``/`` popup and run for the focused creature."""

from typing import Any

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.builtins.ktui.models import model_id
from kohakuterrarium.modules.user_command.base import parse_slash_command
from kohakuterrarium.terrarium.engine.cli.commands import _engine_command_context

LOCAL_COMMANDS = [
    {
        "name": "agents",
        "desc": "creatures, sub-agents, jobs and approvals (↓ from the input)",
    },
    {"name": "focus", "desc": "show another creature inline: /focus <name>"},
    {"name": "config", "desc": "settings: display, models, keys, providers, MCP"},
    {"name": "theme", "desc": "colour theme (remembered)"},
    {"name": "renderer", "desc": "inline · hybrid · fullscreen (remembered)"},
    {"name": "logs", "desc": "the newest log lines, warnings and stray output"},
    {"name": "exit", "desc": "leave"},
]


def registry_of(entry: Any) -> dict:
    """The creature's user commands (name → command)."""
    lister = getattr(entry.agent, "list_user_commands", None)
    registry = lister() if callable(lister) else None
    return dict(registry or getattr(entry.agent.input, "_user_commands", {}) or {})


def command_list(entry: Any) -> list[dict[str, str]]:
    """The ``/`` popup: ktui's commands, then the creature's."""
    listed = list(LOCAL_COMMANDS)
    known = {c["name"] for c in listed}
    for name, cmd in sorted(registry_of(entry).items()):
        if name not in known:
            listed.append(
                {"name": name, "desc": one_line(getattr(cmd, "description", ""), 60)}
            )
    return listed


async def run_command(shell: Any, text: str) -> None:
    """Runs a slash command for ``shell``'s focused creature and shows its result."""
    entry = shell.focused
    view = entry.view
    name, args = parse_slash_command(text)
    view.render("user_message", text, None)
    match name:
        case "exit" | "quit" | "q":
            shell.exit()
            return
        case "theme":
            await shell.settings.choose_theme(args.strip())
            return
        case "model" if not args.strip():
            await shell.settings.pick_model(entry)
            return
        case "renderer" | "mode":
            shell.settings.set_renderer(args.strip())
            return
        case "agents":
            await shell.board.open()
            return
        case "logs":
            await shell.chrome.show_logs()
            return
        case "config" | "settings":
            await shell.settings.open()
            return
        case "focus":
            match = shell._by_name(args.strip())
            if match is None:
                shell.app.notify(f"no creature named {args.strip()!r}", "error")
            else:
                shell.focus(match.creature.creature_id)
            return

    registry = registry_of(entry)
    aliases = {
        a: n for n, c in registry.items() for a in getattr(c, "aliases", None) or []
    }
    cmd = registry.get(aliases.get(name, name))
    if cmd is not None and getattr(cmd, "needs_engine", False):
        ctx = _engine_command_context(
            entry.agent, shell.engine, entry.creature.creature_id, registry
        )
        result = await cmd.execute(args or "", ctx)
    else:
        result = await entry.agent._try_slash_command_text(text)

    if result is None:
        view.render("error", f"unknown command: /{name}")
    elif getattr(result, "error", None):
        view.render("error", str(result.error))
    elif result.output and result.consumed:
        view.render("markdown_notice", f"/{name}", str(result.output), False)
    elif result.output:
        shell.send(entry, str(result.output))
    view.usage.model = model_id(entry.agent) or view.usage.model
    shell._update_commands()
    shell.refresh()


__all__ = ["LOCAL_COMMANDS", "command_list", "registry_of", "run_command"]
