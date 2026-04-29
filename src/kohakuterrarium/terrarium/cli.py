"""CLI commands for terrarium management."""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.builtins.tui.output import TUIOutput
from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.builtins.tui.widgets import ChatInput
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    parse_slash_command,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.cli_output import (
    CLIOutput,
    _format_ts,
    _print_channel_message,
)
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.observer import ChannelObserver
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.utils.logging import (
    get_logger,
    restore_logging,
    set_level,
    suppress_logging,
)

logger = get_logger(__name__)


async def _render_command_data(tui: "TUISession", data: dict) -> str | None:
    """Render interactive command data (modals) and return the user's choice."""
    data_type = data.get("type", "")

    if data_type == "select":
        options = data.get("options", [])
        if not options:
            return None
        selected = await tui.show_selection_modal(
            title=data.get("title", "Select"),
            options=options,
            current=data.get("current", ""),
        )
        if selected:
            action = data.get("action", "")
            if action:
                return selected
        return None

    if data_type == "confirm":
        confirmed = await tui.show_confirm_modal(data.get("message", "Confirm?"))
        if confirmed:
            action = data.get("action", "")
            args = data.get("action_args", "")
            if action:
                return args
        return None

    return None


def _setup_terrarium_tui(
    runtime: TerrariumRuntime,
) -> tuple["TUISession", "TUIOutput"]:
    """Create the TUI session, wire outputs to root and all creatures.

    Returns (tui, root_tui_output) so the caller can replay history and
    run the input loop.
    """
    root = runtime.root_agent

    # Build tab list
    tui_tabs = ["root"]
    tui_tabs.extend(h.name for h in runtime.creatures.values())
    for ch_info in runtime.list_channels():
        tui_tabs.append(f"#{ch_info['name']}")

    # Create terrarium-level TUI
    terrarium_name = getattr(runtime.config, "name", "terrarium")
    tui = TUISession(agent_name=terrarium_name)
    tui.set_terrarium_tabs(tui_tabs)

    # Wire root agent output to TUI "root" tab
    tui_output = TUIOutput(session_key="root")
    tui_output._tui = tui
    tui_output._running = True
    tui_output._default_target = "root"
    root.output_router.default_output = tui_output

    # Wire each creature's output to its TUI tab
    for name, handle in runtime.creatures.items():
        creature_out = TUIOutput(session_key=name)
        creature_out._tui = tui
        creature_out._running = True
        creature_out._default_target = name
        handle.agent.output_router.default_output = creature_out

    # Wire Escape interrupt, click-to-cancel, click-to-promote
    if tui._app:
        tui._app.on_interrupt = root.interrupt
    tui.on_cancel_job = root._cancel_job
    tui.on_promote_job = root._promote_handle

    return tui, tui_output


def _wire_channel_callbacks(runtime: TerrariumRuntime, tui: "TUISession") -> None:
    """Wire channel on_send callbacks to display messages in channel tabs."""
    _wire_channel_registry_callbacks(
        runtime.environment.shared_channels._channels.values(),
        tui,
    )


def _wire_channel_registry_callbacks(channels, tui: "TUISession") -> None:
    for ch in channels:
        ch_name = ch.name

        def _make_ch_cb(channel_name: str):
            def _cb(cn: str, message) -> None:
                sender = message.sender if hasattr(message, "sender") else ""
                content = (
                    message.content if hasattr(message, "content") else str(message)
                )
                tui.add_trigger_message(
                    f"[{channel_name}] {sender}",
                    str(content)[:500],
                    target=f"#{channel_name}",
                )

            return _cb

        ch.on_send(_make_ch_cb(ch_name))


async def _replay_terrarium_history(
    runtime: TerrariumRuntime, tui: "TUISession", tui_output: "TUIOutput"
) -> None:
    """Replay resume history from SessionStore into TUI tabs (if available)."""
    session_store = runtime.session_store
    if not session_store:
        return

    # Root agent events -> root tab
    root_events = session_store.get_events("root")
    if root_events and tui_output:
        await tui_output.on_resume(root_events)

    # Creature events -> creature tabs
    for name, handle in runtime.creatures.items():
        creature_events = session_store.get_events(name)
        if creature_events:
            creature_out = handle.agent.output_router.default_output
            if hasattr(creature_out, "on_resume"):
                await creature_out.on_resume(creature_events)

    # Channel messages -> channel tabs
    for ch_info in runtime.list_channels():
        ch_name = ch_info["name"]
        ch_messages = session_store.get_channel_messages(ch_name)
        if ch_messages:
            tab_target = f"#{ch_name}"
            for msg in ch_messages:
                sender = msg.get("sender", "")
                content = msg.get("content", "")
                tui.add_trigger_message(
                    f"[{ch_name}] {sender}",
                    str(content)[:500],
                    target=tab_target,
                )


async def _handle_terrarium_command(
    text: str,
    tui: "TUISession",
    commands: dict,
    aliases: dict[str, str],
    cmd_context: "UserCommandContext",
    runtime: "TerrariumRuntime | None" = None,
) -> bool | None:
    """Handle a slash command in the terrarium TUI.

    Returns:
        True if the command was consumed (skip sending to agent),
        False if the caller should break out of the main loop,
        None if the text was not a recognized command (fall through).

    Target resolution:
        If ``runtime`` is provided and the active tab corresponds to a
        creature, we rebuild the context with ``agent=<creature.agent>``
        + its own session so commands like ``/compact``, ``/clear``,
        ``/status`` operate on the visible conversation (Bugs 2/8/9).
        Previously the context was pinned to root at start-up and every
        slash command always operated on root.
    """
    cmd_name, cmd_args = parse_slash_command(text)
    canonical = aliases.get(cmd_name, cmd_name)
    cmd = commands.get(canonical)
    if not cmd:
        return None

    # Resolve target agent from the active tab. Channel tabs (prefixed
    # with ``#``) and the non-targeted default both keep root as the
    # target — commands don't make sense against a channel, and root is
    # the "meta" orchestrator the user is controlling via slash.
    target_ctx = cmd_context
    if runtime is not None:
        active_tab = tui.get_active_tab()
        if active_tab and active_tab != "root" and not active_tab.startswith("#"):
            creature_agent = runtime.get_creature_agent(active_tab)
            if creature_agent is not None:
                target_ctx = UserCommandContext(
                    agent=creature_agent,
                    session=creature_agent.session,
                )
                # Preserve the command registry reference so commands
                # like ``/help`` still see the full set.
                if "command_registry" in cmd_context.extra:
                    target_ctx.extra["command_registry"] = cmd_context.extra[
                        "command_registry"
                    ]

    result = await cmd.execute(cmd_args, target_ctx)
    if result.output:
        tui.add_system_notice(result.output, command=cmd_name)
    if result.error:
        tui.add_system_notice(result.error, command=cmd_name, error=True)

    # Handle interactive data (modals)
    if result.data and not result.consumed:
        chosen = await _render_command_data(tui, result.data)
        if chosen is not None:
            action = result.data.get("action", "")
            follow_cmd = commands.get(aliases.get(action, action))
            if follow_cmd:
                result2 = await follow_cmd.execute(chosen, cmd_context)
                if result2.output:
                    tui.add_system_notice(result2.output, command=cmd_name)
                if result2.error:
                    tui.add_system_notice(result2.error, command=cmd_name, error=True)

    # Check if exit was requested (e.g. /exit command)
    if canonical == "exit":
        return False
    if result.consumed:
        return True

    return None


async def run_terrarium_with_tui(runtime: TerrariumRuntime) -> None:
    """Run a terrarium with a full TUI (tabs, terrarium panel, etc.).

    This is the canonical way to run a terrarium interactively.
    Used by both 'kt terrarium run' and 'kt resume' for terrarium sessions.

    The runtime is started as a background task (same pattern as the web
    backend). The TUI handles all user I/O, routing input to the root
    agent via inject_input().
    """
    # Run runtime as background task (conversations/scratchpad restored inside)
    runtime_task = asyncio.create_task(runtime.run())

    # Wait for runtime to be fully started — including the root agent's
    # compact manager (which is initialised inside ``agent.start()`` →
    # ``_init_compact_manager``). The TUI context-bar readout below
    # reads ``root.compact_manager.config.max_tokens`` and will skip the
    # readout if the manager isn't ready yet, leaving the user staring
    # at a blank context bar until the next refresh. Waiting here keeps
    # the first render correct.
    for _ in range(40):
        await asyncio.sleep(0.125)
        if runtime.is_running and runtime.root_agent:
            if getattr(runtime.root_agent, "compact_manager", None) is not None:
                break

    root = runtime.root_agent
    if not root:
        runtime_task.cancel()
        raise RuntimeError("Root agent not available after runtime start")

    tui, tui_output = _setup_terrarium_tui(runtime)
    await tui.start()
    suppress_logging()

    # Start TUI app
    _app_task = asyncio.create_task(tui.run_app())  # noqa: F841
    await tui.wait_ready()

    # Emit session info for the root agent
    terrarium_name = getattr(runtime.config, "name", "terrarium")
    model = getattr(root.llm, "model", "") or getattr(
        getattr(root.llm, "config", None), "model", ""
    )
    session_id = ""
    if runtime.session_store:
        try:
            meta = runtime.session_store.load_meta()
            session_id = meta.get("session_id", "")
        except Exception as e:
            logger.debug(
                "Failed to load session meta for TUI", error=str(e), exc_info=True
            )
    tui.update_session_info(
        session_id=session_id, model=model, agent_name=terrarium_name
    )
    compact_mgr = getattr(root, "compact_manager", None)
    if compact_mgr:
        max_ctx = compact_mgr.config.max_tokens
        compact_at = int(max_ctx * compact_mgr.config.threshold) if max_ctx else 0
        tui.set_context_limits(max_ctx, compact_at)

    # Update terrarium panel
    creature_info = []
    for name, handle in runtime.creatures.items():
        creature_info.append(
            {
                "name": name,
                "running": handle.is_running,
                "listen": handle.listen_channels,
                "send": handle.send_channels,
            }
        )
    tui.update_terrarium(creature_info, runtime.list_channels())

    _wire_channel_callbacks(runtime, tui)
    await _replay_terrarium_history(runtime, tui, tui_output)

    # Build user command registry for slash commands
    _commands = {n: get_builtin_user_command(n) for n in list_builtin_user_commands()}
    _cmd_aliases: dict[str, str] = {}
    for n, cmd in _commands.items():
        for alias in getattr(cmd, "aliases", []):
            _cmd_aliases[alias] = n
    _cmd_context = UserCommandContext(agent=root, session=root.session)
    _cmd_context.extra["command_registry"] = _commands

    # Set command hints on the ChatInput widget
    if tui._app:
        try:
            inp = tui._app.query_one("#input-box", ChatInput)
            inp.command_names = list(_commands.keys())
        except Exception as e:
            logger.debug(
                "Failed to set command hints on TUI input", error=str(e), exc_info=True
            )

    # Main loop: TUI input -> root agent via inject_input
    try:
        while True:
            text = await tui.get_input()
            if not text:
                break

            # Handle slash commands
            if text.startswith("/"):
                cmd_result = await _handle_terrarium_command(
                    text, tui, _commands, _cmd_aliases, _cmd_context, runtime
                )
                if cmd_result is False:
                    break
                if cmd_result is True:
                    continue
                # None means unknown command — fall through to send as text

            active_tab = tui.get_active_tab()

            if not active_tab or active_tab == "root":
                tui.set_active_target("root")
                await root.inject_input(text, source="tui")
            elif active_tab.startswith("#"):
                ch_name = active_tab[1:]
                tui.add_user_message(text, target=active_tab)
                await runtime.api.send_to_channel(ch_name, text, sender="human")
            else:
                tui.set_active_target(active_tab)
                await root.inject_input(
                    f"Send this to {active_tab}: {text}", source="tui"
                )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        restore_logging()
        runtime_task.cancel()
        try:
            await runtime_task
        except (asyncio.CancelledError, Exception):
            pass
        await runtime.stop()
        tui.stop()


async def run_engine_terrarium_with_tui(
    engine: Terrarium,
    graph_id: str,
    store: SessionStore | None = None,
) -> None:
    root_creature = engine.get_creature("root")
    root = root_creature.agent
    graph = engine.get_graph(graph_id)
    env = engine._environments[graph_id]

    tui_tabs = ["root"]
    graph_creatures = [engine.get_creature(cid) for cid in graph.creature_ids]
    for creature in graph_creatures:
        if creature.creature_id != "root":
            tui_tabs.append(creature.creature_id)
    for ch_info in graph.channels.values():
        tui_tabs.append(f"#{ch_info.name}")

    terrarium_name = graph_id
    tui = TUISession(agent_name=terrarium_name)
    tui.set_terrarium_tabs(tui_tabs)

    root_output = TUIOutput(session_key="root")
    root_output._tui = tui
    root_output._running = True
    root_output._default_target = "root"
    root.output_router.default_output = root_output

    for creature in graph_creatures:
        if creature.creature_id == "root":
            continue
        creature_out = TUIOutput(session_key=creature.creature_id)
        creature_out._tui = tui
        creature_out._running = True
        creature_out._default_target = creature.creature_id
        creature.agent.output_router.default_output = creature_out

    if tui._app:
        tui._app.on_interrupt = root.interrupt
    tui.on_cancel_job = root._cancel_job
    tui.on_promote_job = root._promote_handle

    await tui.start()
    suppress_logging()
    app_task = asyncio.create_task(tui.run_app())
    await tui.wait_ready()

    model = getattr(root.llm, "model", "") or getattr(
        getattr(root.llm, "config", None), "model", ""
    )
    session_id = ""
    if store:
        try:
            meta = store.load_meta()
            session_id = meta.get("session_id", "")
        except Exception as e:
            logger.debug(
                "Failed to load session meta for TUI", error=str(e), exc_info=True
            )
    tui.update_session_info(
        session_id=session_id,
        model=model,
        agent_name=terrarium_name,
    )
    compact_mgr = getattr(root, "compact_manager", None)
    if compact_mgr:
        max_ctx = compact_mgr.config.max_tokens
        compact_at = int(max_ctx * compact_mgr.config.threshold) if max_ctx else 0
        tui.set_context_limits(max_ctx, compact_at)

    creature_info = [
        {
            "name": creature.creature_id,
            "running": creature.is_running,
            "listen": creature.listen_channels,
            "send": creature.send_channels,
        }
        for creature in graph_creatures
        if creature.creature_id != "root"
    ]
    tui.update_terrarium(creature_info, env.shared_channels.get_channel_info())
    _wire_channel_registry_callbacks(env.shared_channels._channels.values(), tui)

    commands = {n: get_builtin_user_command(n) for n in list_builtin_user_commands()}
    aliases: dict[str, str] = {}
    for n, cmd in commands.items():
        for alias in getattr(cmd, "aliases", []):
            aliases[alias] = n
    cmd_context = UserCommandContext(agent=root, session=root.session)
    cmd_context.extra["command_registry"] = commands

    if tui._app:
        try:
            inp = tui._app.query_one("#input-box", ChatInput)
            inp.command_names = list(commands.keys())
        except Exception as e:
            logger.debug(
                "Failed to set command hints on TUI input", error=str(e), exc_info=True
            )

    try:
        while True:
            text = await tui.get_input()
            if not text:
                break
            if text.startswith("/"):
                cmd_result = await _handle_terrarium_command(
                    text, tui, commands, aliases, cmd_context, None
                )
                if cmd_result is False:
                    break
                if cmd_result is True:
                    continue
            active_tab = tui.get_active_tab()
            if not active_tab or active_tab == "root":
                tui.set_active_target("root")
                await root.inject_input(text, source="tui")
            elif active_tab.startswith("#"):
                ch_name = active_tab[1:]
                channel = env.shared_channels.get(ch_name)
                if channel is None:
                    tui.add_trigger_message(
                        "[error]",
                        f"Channel '{ch_name}' not found",
                        target=active_tab,
                    )
                    continue
                tui.add_user_message(text, target=active_tab)
                await channel.send(ChannelMessage(sender="human", content=text))
            else:
                tui.set_active_target(active_tab)
                await root.inject_input(
                    f"Send this to {active_tab}: {text}", source="tui"
                )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        restore_logging()
        app_task.cancel()
        try:
            await app_task
        except (asyncio.CancelledError, Exception):
            pass
        tui.stop()


async def run_terrarium_with_rich_cli(runtime: TerrariumRuntime) -> None:
    """Run a terrarium with the rich single-agent CLI.

    Mounts the root agent if present; otherwise auto-picks the first
    creature and prints a warning. Other creatures keep running in the
    background but their output is not surfaced to the user.
    """
    runtime_task = asyncio.create_task(runtime.run())

    try:
        # Wait for runtime to be fully started
        for _ in range(20):
            await asyncio.sleep(0.25)
            if runtime.is_running:
                break

        # Pick the agent: root preferred, else first creature
        target_agent = runtime.root_agent
        target_label = "root"
        if target_agent is None:
            creatures = list(runtime.creatures.values())
            if not creatures:
                runtime_task.cancel()
                raise RuntimeError("Terrarium has no creatures to mount")
            handle = creatures[0]
            target_agent = handle.agent
            target_label = handle.name
            print(
                f"\nWarning: terrarium has no root agent. "
                f"Auto-mounting first creature '{target_label}' for rich CLI mode.\n"
                f"(Use --mode tui for the full multi-tab terrarium UI.)\n"
            )

        # Wire the rich CLI output to the chosen agent
        app = RichCLIApp(target_agent)
        rich_output = RichCLIOutput(app)
        target_agent.output_router.default_output = rich_output

        # Restore session events for the chosen agent
        session_store = runtime.session_store
        if session_store:
            events = session_store.get_events(target_label)
            if events:
                await rich_output.on_resume(events)

        await app.run()
    finally:
        runtime_task.cancel()
        try:
            await runtime_task
        except (asyncio.CancelledError, Exception):
            pass
        await runtime.stop()


async def run_engine_terrarium_with_cli(engine: Terrarium) -> None:
    root = engine.get_creature("root")
    root_output = CLIOutput("root")
    root_output._running = True
    root.agent.output_router.default_output = root_output

    while True:
        text = await _read_cli_input()
        if text is None:
            break
        text = text.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit", "/exit", "/quit"):
            break
        await root.agent.inject_input(text, source="cli")


async def run_engine_terrarium_with_rich_cli(engine: Terrarium) -> None:
    root = engine.get_creature("root")
    app = RichCLIApp(root.agent)
    root.agent.output_router.default_output = RichCLIOutput(app)
    await app.run()


async def run_terrarium_with_cli(
    runtime: TerrariumRuntime,
    *,
    observe: list[str] | None = None,
    no_observe: bool = False,
) -> None:
    """Run a terrarium with a headless stdin/stdout CLI (plain mode)."""
    runtime_task = asyncio.create_task(runtime.run())
    observer = None

    try:
        for _ in range(20):
            await asyncio.sleep(0.25)
            if runtime.is_running and runtime.root_agent:
                break

        root = runtime.root_agent
        if not root:
            runtime_task.cancel()
            raise RuntimeError("Root agent not available after runtime start")

        root_output = CLIOutput("root")
        root_output._running = True
        root.output_router.default_output = root_output

        creature_outputs: dict[str, CLIOutput] = {}
        for name, handle in runtime.creatures.items():
            creature_output = CLIOutput(name)
            creature_output._running = True
            handle.agent.output_router.default_output = creature_output
            creature_outputs[name] = creature_output

        if not no_observe:
            observer_args = argparse.Namespace(observe=observe, no_observe=no_observe)
            observer = await _setup_observer(runtime, observer_args, runtime.config)

        session_store = runtime.session_store
        if session_store:
            root_events = session_store.get_events("root")
            if root_events:
                await root_output.on_resume(root_events)

            for name, output in creature_outputs.items():
                creature_events = session_store.get_events(name)
                if creature_events:
                    await output.on_resume(creature_events)

            if not no_observe:
                for ch_info in runtime.list_channels():
                    ch_name = ch_info["name"]
                    for msg in session_store.get_channel_messages(ch_name):
                        _print_channel_message(
                            channel=ch_name,
                            sender=msg.get("sender", ""),
                            content=msg.get("content", ""),
                            ts=_format_ts(msg.get("ts")),
                        )

        while True:
            text = await _read_cli_input()
            if text is None:
                break

            text = text.strip()
            if not text:
                continue

            if text.lower() in ("exit", "quit", "/exit", "/quit"):
                break

            await root.inject_input(text, source="cli")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if observer is not None:
            await observer.stop()
        runtime_task.cancel()
        try:
            await runtime_task
        except (asyncio.CancelledError, Exception):
            pass
        await runtime.stop()


def add_terrarium_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add terrarium subcommands to the CLI parser."""
    terrarium_parser = subparsers.add_parser(
        "terrarium",
        help="Run and manage multi-agent terrariums",
    )
    terrarium_sub = terrarium_parser.add_subparsers(dest="terrarium_command")

    # terrarium run <path>
    run_p = terrarium_sub.add_parser("run", help="Run a terrarium")
    run_p.add_argument("terrarium_path", help="Path to terrarium config")
    run_p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    run_p.add_argument(
        "--seed",
        help="Seed prompt to inject into the 'seed' channel on startup",
    )
    run_p.add_argument(
        "--seed-channel",
        default="seed",
        help="Channel to send the seed prompt to (default: seed)",
    )
    run_p.add_argument(
        "--observe",
        nargs="*",
        help="Channels to observe. Omit to observe all channels.",
    )
    run_p.add_argument(
        "--no-observe",
        action="store_true",
        help="Disable channel observation",
    )
    run_p.add_argument(
        "--session",
        nargs="?",
        const="__auto__",
        default="__auto__",
        help="Session file path (default: auto in ~/.kohakuterrarium/sessions/)",
    )
    run_p.add_argument(
        "--no-session",
        action="store_true",
        help="Disable session persistence",
    )
    run_p.add_argument(
        "--llm",
        default=None,
        help="Override LLM profile for all creatures (e.g., mimo-v2-pro, gemini)",
    )
    run_p.add_argument(
        "--mode",
        choices=["cli", "plain", "tui"],
        default="tui",
        help=(
            "Input/output mode. tui=full multi-tab, cli=rich single-creature "
            "(auto-picks root or first creature), plain=dumb stdout"
        ),
    )

    # terrarium info <path>
    info_p = terrarium_sub.add_parser("info", help="Show terrarium info")
    info_p.add_argument("terrarium_path", help="Path to terrarium config")


def handle_terrarium_command(args: argparse.Namespace) -> int:
    """Dispatch terrarium subcommand."""
    match args.terrarium_command:
        case "run":
            return _run_terrarium_cli(args)
        case "info":
            return _info_terrarium_cli(args)
        case _:
            print("Usage: kohakuterrarium terrarium {run,info}")
            return 0


def _run_terrarium_cli(args: argparse.Namespace) -> int:
    """Run a terrarium from CLI."""
    set_level(args.log_level)

    path = Path(args.terrarium_path)
    if not path.exists():
        print(f"Error: Path not found: {args.terrarium_path}")
        return 1

    try:
        config = load_terrarium_config(str(path))
    except Exception as e:
        print(f"Error loading config: {e}")
        return 1

    print(f"Terrarium: {config.name}")
    print(f"Creatures: {[c.name for c in config.creatures]}")
    print(f"Channels: {[c.name for c in config.channels]}")
    if config.root:
        base = config.root.config_data.get("base_config", "(inline)")
        print(f"Root agent: {base}")

    # Session store setup
    session_arg = getattr(args, "session", None)
    no_session = getattr(args, "no_session", False)
    if no_session:
        session_arg = None
    store = None
    session_file = None

    _session_dir = Path.home() / ".kohakuterrarium" / "sessions"

    if session_arg is not None:
        if session_arg == "__auto__":
            _session_dir.mkdir(parents=True, exist_ok=True)
            session_file = _session_dir / f"{config.name}_{id(config):08x}.kohakutr"
        else:
            session_file = Path(session_arg)

        store = SessionStore(session_file)
        store.init_meta(
            session_id=uuid4().hex,
            config_type="terrarium",
            config_path=str(path),
            pwd=str(Path.cwd()),
            agents=[c.name for c in config.creatures]
            + (["root"] if config.root else []),
            terrarium_name=config.name,
            terrarium_channels=[
                {
                    "name": ch.name,
                    "type": ch.channel_type,
                    "description": ch.description,
                }
                for ch in config.channels
            ],
            terrarium_creatures=[
                {"name": c.name, "listen": c.listen_channels, "send": c.send_channels}
                for c in config.creatures
            ],
        )

    # When root agent is configured, launch via the unified engine. The legacy
    # TerrariumRuntime path does not model root as a graph creature and can route
    # root's channel output back into itself.
    if config.root:
        print()

        async def _run_with_mode() -> None:
            engine = Terrarium()
            graph = await engine.apply_recipe(
                config,
                llm_override=getattr(args, "llm", None),
            )
            if store:
                await engine.attach_session(graph.graph_id, store)
            try:
                if args.mode == "cli":
                    await run_engine_terrarium_with_rich_cli(engine)
                elif args.mode == "plain":
                    await run_engine_terrarium_with_cli(engine)
                else:
                    await run_engine_terrarium_with_tui(
                        engine,
                        graph.graph_id,
                        store,
                    )
            finally:
                await engine.shutdown()

        try:
            asyncio.run(_run_with_mode())
            return 0
        except KeyboardInterrupt:
            print("\nInterrupted")
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1
        finally:
            if store:
                store.close()
            if session_file and session_file.exists():
                print(f"\nSession saved. To resume:")
                print(f"  kt resume {session_file.stem}")

    # No root agent: basic seed/observe CLI
    seed_prompt = args.seed
    seed_channel = args.seed_channel
    has_seed_channel = any(c.name == seed_channel for c in config.channels)

    if has_seed_channel and not seed_prompt:
        print()
        try:
            seed_prompt = input(f"Enter seed prompt (for '{seed_channel}' channel): ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")
            return 0

    if seed_prompt:
        print(f"Seed: {seed_prompt[:80]}")
    print()

    async def _run() -> None:
        runtime = TerrariumRuntime(config)
        await runtime.start()

        # Setup observer
        observer = None
        if not args.no_observe:
            observer = await _setup_observer(runtime, args, config)

        # Inject seed prompt
        if seed_prompt and has_seed_channel:
            await runtime.api.send_to_channel(seed_channel, seed_prompt, sender="human")
            print(f"  Seed sent to '{seed_channel}' channel")
            print()

        # Run creature tasks
        try:
            for handle in runtime._creatures.values():
                task = asyncio.create_task(
                    runtime._run_creature(handle),
                    name=f"creature_{handle.name}",
                )
                runtime._creature_tasks.append(task)
            await asyncio.gather(*runtime._creature_tasks, return_exceptions=True)
        except KeyboardInterrupt:
            pass
        finally:
            if observer is not None:
                await observer.stop()
            await runtime.stop()

    try:
        asyncio.run(_run())
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


async def _setup_observer(runtime, args, config):
    """Setup channel observer and return it."""
    observer = ChannelObserver(runtime._session)

    def print_message(msg):
        _print_channel_message(
            channel=msg.channel,
            sender=msg.sender,
            content=msg.content,
            ts=msg.timestamp.strftime("%H:%M:%S"),
        )

    observer.on_message(print_message)

    # Determine which channels to observe
    if args.observe is not None:
        # Explicit list (--observe ideas outline)
        channels = args.observe if args.observe else []
    else:
        # Default: observe all channels
        channels = [c.name for c in config.channels]

    for ch_name in channels:
        await observer.observe(ch_name)

    if channels:
        print(f"  Observing: {', '.join(channels)}")

    return observer


async def _read_cli_input(prompt: str = "You: ") -> str | None:
    """Read one line from stdin without blocking the event loop."""
    if sys.stdin.isatty():
        try:
            return await asyncio.to_thread(input, prompt)
        except EOFError:
            return None

    line = await asyncio.to_thread(sys.stdin.readline)
    if line == "":
        return None
    return line.rstrip("\r\n")


def _info_terrarium_cli(args: argparse.Namespace) -> int:
    """Show terrarium information."""
    path = Path(args.terrarium_path)
    if not path.exists():
        print(f"Error: Path not found: {args.terrarium_path}")
        return 1

    try:
        config = load_terrarium_config(str(path))
    except Exception as e:
        print(f"Error: {e}")
        return 1

    print(f"Terrarium: {config.name}")
    print("=" * 40)

    print(f"\nCreatures ({len(config.creatures)}):")
    for c in config.creatures:
        print(f"  {c.name}")
        base = c.config_data.get("base_config", "(inline)")
        print(f"    base: {base}")
        if c.listen_channels:
            print(f"    listen: {c.listen_channels}")
        if c.send_channels:
            print(f"    send:   {c.send_channels}")
        if c.output_log:
            print(f"    log:    enabled (max {c.output_log_size})")

    print(f"\nChannels ({len(config.channels)}):")
    for ch in config.channels:
        desc = f" - {ch.description}" if ch.description else ""
        print(f"  {ch.name} ({ch.channel_type}){desc}")

    return 0
