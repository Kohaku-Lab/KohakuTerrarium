"""Engine-aware slash-command dispatch for the TUI runner (design §12.4, R1-32).

Extracted from :mod:`engine_cli` (which sits at the file-size cap) so the
trusted-context construction and the ``needs_engine`` dispatch stay one unit.
Typed ``/drives`` / ``/goal`` subcommands must run against a trusted local-console
context (a live service + focused creature + explicit operator authority) so they
reach the Drive service instead of the engine-agnostic agent pipeline, which
returns "unavailable".
"""

from typing import Any

from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    parse_slash_command,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService


def _engine_command_context(
    focus: Any, engine: Terrarium, focus_creature_id: str, commands: dict
) -> UserCommandContext:
    """Trusted local-console context for engine-aware TUI slash commands (R1-32).

    Mirrors the Rich CLI dispatch (``cli_rich/app_multi.dispatch_topology_command``):
    a ready ``service`` + focused ``creature_id`` + local ``principal`` + EXPLICIT
    operator authority. ``engine_cli`` is a terrarium-layer module, so importing
    :class:`LocalTerrariumService` is legal here. The operator flag is set
    explicitly because the command default is unprivileged (R1-21) — a context
    that is not a trusted console (any adapter that does not set the flag) stays
    unprivileged.
    """
    ctx = UserCommandContext(agent=focus, session=focus.session)
    ctx.extra["command_registry"] = commands
    ctx.extra["service"] = LocalTerrariumService(engine)
    ctx.extra["engine"] = engine
    ctx.extra["creature_id"] = focus_creature_id
    ctx.extra["principal"] = "user:local"
    ctx.extra["is_operator"] = True
    return ctx


def _build_command_aliases(commands: dict) -> dict[str, str]:
    """Alias -> canonical-name map for the given registry."""
    aliases: dict[str, str] = {}
    for name, cmd in commands.items():
        for alias in getattr(cmd, "aliases", None) or []:
            aliases[alias] = name
    return aliases


def _active_command_target(
    tui: Any, engine: Terrarium, focus: Any, focus_creature_id: str, graph_id: str
) -> tuple[str, Any]:
    """The ``(creature_id, agent)`` the next engine-aware command must act on.

    Engine-aware slash commands target the ACTIVE tab's creature, not the launch
    focus, so ``/drives`` / ``/goal`` typed on a sibling tab operate on and render
    to that creature (R1-32 §7). Channel tabs, unknown tabs, and tabs outside the
    focus graph fall back to the focus creature.
    """
    getter = getattr(tui, "get_active_tab", None)
    active = getter() if callable(getter) else None
    if active and not active.startswith("#"):
        try:
            creature = engine.get_creature(active)
        except KeyError:
            creature = None
        if creature is not None and creature.graph_id == graph_id:
            return active, creature.agent
    return focus_creature_id, focus


def _live_command_registry(agent: Any, fallback: dict) -> dict:
    """The agent's LIVE aggregated slash registry (design §8.9, R1-32 §6).

    Reads ``list_user_commands()`` so plugin-contributed commands (e.g. ``/goal``)
    are present and drop out again when the plugin is disabled — a built-in-only
    snapshot never sees them. ``fallback`` covers bare agent stubs that do not
    aggregate their own registry.
    """
    lister = getattr(agent, "list_user_commands", None)
    if callable(lister):
        live = lister()
        if live:
            return dict(live)
    return dict(fallback)


async def _dispatch_active_engine_command(
    text: str,
    tui: Any,
    engine: Terrarium,
    focus: Any,
    focus_creature_id: str,
    graph_id: str,
    fallback_commands: dict,
) -> bool:
    """Dispatch an engine-aware slash command bound to the ACTIVE tab (R1-32).

    Resolves the currently-focused ``(creature_id, agent)``, reads that agent's
    live aggregated registry (so plugin commands like ``/goal`` are found — §6),
    and builds the trusted context targeting that creature so both the service
    call and the rendered notice follow the active tab (§7).
    """
    creature_id, agent = _active_command_target(
        tui, engine, focus, focus_creature_id, graph_id
    )
    commands = _live_command_registry(agent, fallback_commands)
    aliases = _build_command_aliases(commands)
    ctx = _engine_command_context(agent, engine, creature_id, commands)
    return await _dispatch_engine_command(text, tui, commands, aliases, ctx)


async def _dispatch_engine_command(
    text: str,
    tui: Any,
    commands: dict,
    aliases: dict[str, str],
    cmd_context: UserCommandContext,
) -> bool:
    """Run an engine-aware (`needs_engine`) slash command with the trusted context.

    Returns True when the command was handled here (the runner should
    ``continue``), False to fall through to the normal per-tab injection. Only
    commands whose registry entry declares ``needs_engine`` are intercepted;
    everything else (``/model``, ``/clear``, …) still routes to the active
    creature's own pipeline. The result renders as a system notice on the focused
    creature's tab.
    """
    name, args = parse_slash_command(text)
    name = aliases.get(name, name)
    cmd = commands.get(name)
    if cmd is None or not getattr(cmd, "needs_engine", False):
        return False
    target = cmd_context.extra.get("creature_id", "")
    try:
        result = await cmd.execute(args or "", cmd_context)
    except Exception as exc:
        tui.add_system_notice(str(exc), command=f"/{name}", error=True, target=target)
        return True
    if result is None:
        tui.add_system_notice(
            f"Unknown command: /{name}", command=f"/{name}", error=True, target=target
        )
    elif getattr(result, "error", None):
        tui.add_system_notice(
            result.error, command=f"/{name}", error=True, target=target
        )
    elif getattr(result, "output", None):
        tui.add_system_notice(result.output, command=f"/{name}", target=target)
    return True


__all__ = [
    "_build_command_aliases",
    "_dispatch_active_engine_command",
    "_dispatch_engine_command",
    "_engine_command_context",
]
