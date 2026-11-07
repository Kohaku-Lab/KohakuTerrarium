"""Slash commands in ktui: the shell's own (``LOCAL_COMMANDS``) and the focused creature's
registry, listed in the composer's ``/`` popup and run for the focused creature."""

from typing import Any

from kohakutui.ui import Confirm, Picker

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.builtins.ktui.models import model_id
from kohakuterrarium.builtins.ktui.servers import stop_session
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
    {"name": "attach", "desc": "attach a file to the next message: /attach <path>"},
    {"name": "copy", "desc": "copy the last reply to the clipboard"},
    {"name": "keys", "desc": "the keys (also ? or F1)"},
    {"name": "resume", "desc": "continue another saved session (this one is saved)"},
    {"name": "older", "desc": "load the older turns of a resumed session"},
    {"name": "login", "desc": "sign in to a provider: /login <provider>"},
    {"name": "rename", "desc": "title this session: /rename <title>"},
    {
        "name": "export",
        "desc": "write the conversation to a file: /export [md|html|jsonl] [path]",
    },
    {"name": "exit", "desc": "leave (attached to a server: the session keeps running)"},
    {
        "name": "stop",
        "desc": "attached to a server: stop the session there, then leave",
    },
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


async def stop_remote(shell: Any) -> None:
    """``/stop`` attached to a server: the session stops there (it stays saved), then ktui
    leaves; ``/exit`` would leave it running."""
    session = shell.engine.session
    sure = await shell.app.run_dialog_async(
        Confirm(question=f"Stop {session.session_id} in the server, then leave?")
    )
    if not sure:
        return
    error = await stop_session(session.base_url, session.session_id)
    if error:
        shell.app.notify(f"not stopped: {error}", "error")
        return
    shell.exit()


async def run_command(shell: Any, text: str, echo: bool = True) -> None:
    """Runs a slash command for ``shell``'s focused creature and shows its result
    (``echo``: the command is shown as typed; a follow-up from a picker is not)."""
    entry = shell.focused
    view = entry.view
    name, args = parse_slash_command(text)
    if echo:
        view.render("user_message", text, None)
    remote = getattr(shell.engine, "keeps_running", False)
    match name:
        case "exit" | "quit" | "q":
            shell.exit()
            return
        case "stop" if remote:
            await stop_remote(shell)
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
        case "attach":
            shell.composing.attach(args)
            return
        case "keys":
            await shell.show_help()
            return
        case "resume" if not args.strip():
            await shell.sessions.resume()
            return
        case "login":
            provider = args.strip()
            if not provider:
                shell.app.notify(
                    "usage: /login <provider> (codex signs in in the browser)", "info"
                )
            else:
                await shell.config_edit.login(provider)
            return
        case "older":
            shell.messages.load_older(entry)
            return
        case "rename":
            await shell.sessions.rename(args)
            return
        case "export":
            await shell.sessions.export(args.strip())
            return
        case "copy":
            replies = [m for m in view.messages.values() if m.kind == "assistant"]
            if replies:
                shell.app.copy(replies[-1].text)
            else:
                shell.app.notify("no reply to copy yet", "info")
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

    data = getattr(result, "data", None) if result is not None else None
    if isinstance(data, dict) and await _command_data(shell, data):
        pass
    elif result is None:
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


async def _command_data(shell: Any, data: dict) -> bool:
    """A command's structured result drawn natively, as kt-tui does: ``select`` as a picker
    and ``confirm`` as a question, the choice running the command's follow-up action;
    ``notify`` as a notice. False for kinds shown as the command's text."""
    action = str(data.get("action", "") or "")
    match data.get("type"):
        case "select" if data.get("options"):
            current = str(data.get("current", "") or "")
            items = [
                {
                    "label": str(o.get("label", o.get("value", ""))),
                    "value": str(o.get("value", "")),
                    "detail": " · ".join(
                        str(p) for p in (o.get("status"), o.get("description")) if p
                    )
                    or None,
                    "current": bool(current) and str(o.get("value", "")) == current,
                }
                for o in data["options"]
            ]
            spec = Picker(
                items=items,
                title=str(data.get("title", "Select")),
                placeholder="type to filter",
                max_visible=10,
            )
            picked = await shell.panel(spec)
            value = picked.get("value") if isinstance(picked, dict) else picked
            if value and action:
                await run_command(shell, f"/{action} {value}", echo=False)
            return True
        case "confirm":
            message = str(data.get("message", "Confirm?"))
            if await shell.panel(Confirm(question=message)) and action:
                args = str(data.get("action_args", "") or "")
                await run_command(shell, f"/{action} {args}".rstrip(), echo=False)
            return True
        case "notify":
            level = str(data.get("level", "info"))
            shell.app.notify(one_line(str(data.get("message", "")), 120), level)
            return True
    return False


__all__ = ["LOCAL_COMMANDS", "command_list", "registry_of", "run_command"]
