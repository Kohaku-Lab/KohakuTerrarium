"""Engine TUI launcher.

Mounts the Textual-based TUI on top of a running :class:`Terrarium`
engine. ``run_engine_with_tui`` is the single entry point shared
between ``kt run creature.yaml`` (solo creature, focus on the
privileged creature), ``kt run terrarium.yaml`` (recipe, focus on the
declared root), and ``kt resume`` (focus on whatever creature in the
resumed graph is privileged, falling back to the first creature).

The TUI tabs are: focus creature first, then every other creature in
the graph, then one ``#channel`` tab per shared channel. Channel send
callbacks are wired here so each ``send`` lands as a transcript line
on the channel's tab.
"""

import asyncio
from collections.abc import Iterable

from kohakuterrarium.builtins.tui.output import TUIOutput
from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.builtins.tui.widgets import ChatInput
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.core.channel import BaseChannel, ChannelMessage
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger, restore_logging, suppress_logging

logger = get_logger(__name__)


def wire_channel_registry_callbacks(
    channels: Iterable[BaseChannel], tui: "TUISession"
) -> None:
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


async def run_engine_with_tui(
    engine: Terrarium,
    focus_creature_id: str,
    store: SessionStore | None = None,
    *,
    handle_command=None,
) -> None:
    """Run the engine TUI with focus on ``focus_creature_id``.

    The focus creature is the one whose tab the TUI opens to and whose
    inputs route from the user prompt by default. For solo ``kt run``
    this is the lone creature; for a recipe it's the privileged root.
    """
    focus_creature = engine.get_creature(focus_creature_id)
    focus = focus_creature.agent
    graph_id = focus_creature.graph_id
    graph = engine.get_graph(graph_id)
    env = engine._environments[graph_id]

    graph_creatures = [engine.get_creature(cid) for cid in graph.creature_ids]
    tui_tabs = [focus_creature_id]
    tui_tabs.extend(
        c.creature_id for c in graph_creatures if c.creature_id != focus_creature_id
    )
    tui_tabs.extend(f"#{ch_info.name}" for ch_info in graph.channels.values())

    tui = TUISession(agent_name=graph_id)
    tui.set_terrarium_tabs(tui_tabs)

    focus_output = TUIOutput(session_key=focus_creature_id)
    focus_output._tui = tui
    focus_output._running = True
    focus_output._default_target = focus_creature_id
    focus.output_router.default_output = focus_output

    for creature in graph_creatures:
        if creature.creature_id == focus_creature_id:
            continue
        creature_out = TUIOutput(session_key=creature.creature_id)
        creature_out._tui = tui
        creature_out._running = True
        creature_out._default_target = creature.creature_id
        creature.agent.output_router.default_output = creature_out

    if tui._app:
        tui._app.on_interrupt = focus.interrupt
    tui.on_cancel_job = focus._cancel_job
    tui.on_promote_job = focus._promote_handle

    await tui.start()
    suppress_logging()
    app_task = asyncio.create_task(tui.run_app())
    await tui.wait_ready()

    _update_session_info(tui, focus, graph_id, store)
    _update_terrarium_panel(tui, graph_creatures, env, focus_creature_id)
    wire_channel_registry_callbacks(env.shared_channels._channels.values(), tui)

    commands = {n: get_builtin_user_command(n) for n in list_builtin_user_commands()}
    aliases = _build_command_aliases(commands)
    cmd_context = UserCommandContext(agent=focus, session=focus.session)
    cmd_context.extra["command_registry"] = commands
    _set_command_hints(tui, commands)

    try:
        while True:
            text = await tui.get_input()
            if not text:
                break
            if text.startswith("/") and handle_command is not None:
                cmd_result = await handle_command(
                    text, tui, commands, aliases, cmd_context, None
                )
                if cmd_result is False:
                    break
                if cmd_result is True:
                    continue
            active_tab = tui.get_active_tab()
            if not active_tab or active_tab == focus_creature_id:
                tui.set_active_target(focus_creature_id)
                await focus.inject_input(text, source="tui")
            elif active_tab.startswith("#"):
                await _send_to_channel_tab(tui, env, active_tab, text)
            else:
                tui.set_active_target(active_tab)
                await focus.inject_input(
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


def _update_session_info(
    tui: TUISession, focus, graph_id: str, store: SessionStore | None
) -> None:
    model = getattr(focus.llm, "model", "") or getattr(
        getattr(focus.llm, "config", None), "model", ""
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
    tui.update_session_info(session_id=session_id, model=model, agent_name=graph_id)
    compact_mgr = getattr(focus, "compact_manager", None)
    if compact_mgr:
        max_ctx = compact_mgr.config.max_tokens
        compact_at = int(max_ctx * compact_mgr.config.threshold) if max_ctx else 0
        tui.set_context_limits(max_ctx, compact_at)


def _update_terrarium_panel(
    tui: TUISession, graph_creatures, env, focus_creature_id: str
) -> None:
    creature_info = [
        {
            "name": creature.creature_id,
            "running": creature.is_running,
            "listen": creature.listen_channels,
            "send": creature.send_channels,
        }
        for creature in graph_creatures
        if creature.creature_id != focus_creature_id
    ]
    tui.update_terrarium(creature_info, env.shared_channels.get_channel_info())


def _build_command_aliases(commands: dict) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for name, cmd in commands.items():
        for alias in getattr(cmd, "aliases", []):
            aliases[alias] = name
    return aliases


def _set_command_hints(tui: TUISession, commands: dict) -> None:
    if not tui._app:
        return
    try:
        inp = tui._app.query_one("#input-box", ChatInput)
        inp.command_names = list(commands.keys())
    except Exception as e:
        logger.debug(
            "Failed to set command hints on TUI input", error=str(e), exc_info=True
        )


async def _send_to_channel_tab(
    tui: TUISession, env, active_tab: str, text: str
) -> None:
    ch_name = active_tab[1:]
    channel = env.shared_channels.get(ch_name)
    if channel is None:
        tui.add_trigger_message(
            "[error]",
            f"Channel '{ch_name}' not found",
            target=active_tab,
        )
        return
    tui.add_user_message(text, target=active_tab)
    await channel.send(ChannelMessage(sender="human", content=text))
