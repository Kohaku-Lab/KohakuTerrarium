"""Engine TUI launcher.

Mounts the Textual-based TUI on top of a running :class:`Terrarium`
engine. ``run_engine_with_tui`` is the single entry point shared
between ``kt run creature.yaml`` (solo creature), ``kt run
terrarium.yaml`` (recipe), and ``kt resume``. The TUI is uniform
across all three — there is no creature-vs-terrarium fork at the
runtime layer. Solo sessions are graphs with one creature; the same
tab strip + channel-tab plumbing applies.

The TUI tabs are: focus creature first, then every other creature in
the graph, then one ``#channel`` tab per shared channel. The TUI
subscribes to engine topology events so creatures spawned at runtime
(via ``group_add_node``) and channels created at runtime (via
``group_channel(action="create")``) are surfaced as new tabs without
the user having to restart.
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
from kohakuterrarium.terrarium.events import EventFilter, EventKind
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

    routed_creatures: set[str] = {focus_creature_id}
    for creature in graph_creatures:
        if creature.creature_id == focus_creature_id:
            continue
        creature_out = TUIOutput(session_key=creature.creature_id)
        creature_out._tui = tui
        creature_out._running = True
        creature_out._default_target = creature.creature_id
        creature.agent.output_router.default_output = creature_out
        routed_creatures.add(creature.creature_id)

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
    wired_channels: set[str] = set()
    _wire_new_channels(env, tui, wired_channels)
    refresh_task = asyncio.create_task(
        _refresh_tui_on_topology_change(
            engine,
            tui,
            graph_id,
            focus_creature_id,
            wired_channels,
            routed_creatures,
        )
    )

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
        refresh_task.cancel()
        try:
            await refresh_task
        except (asyncio.CancelledError, Exception):
            pass
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


def _wire_new_channels(env, tui: "TUISession", wired: set[str]) -> None:
    """Install on_send callbacks on every channel not already wired.

    Called once at startup and again on every topology change so
    channels added at runtime (via ``group_channel(action="create")``)
    show up as transcript-emitting tabs without a TUI restart.
    ``wired`` is mutated in place so re-entry is idempotent.
    """
    for ch in env.shared_channels._channels.values():
        if ch.name in wired:
            continue
        wire_channel_registry_callbacks([ch], tui)
        wired.add(ch.name)


async def _refresh_tui_on_topology_change(
    engine: Terrarium,
    tui: "TUISession",
    graph_id: str,
    focus_creature_id: str,
    wired_channels: set[str],
    routed_creatures: set[str],
) -> None:
    """Re-render the tab strip on every topology change in our graph.

    Subscribes to ``CREATURE_STARTED`` / ``CREATURE_STOPPED`` /
    ``TOPOLOGY_CHANGED`` (which fires on add/remove channel and on
    cross-graph wires) so a creature spawning a peer mid-conversation
    surfaces as a new tab on the next event tick. Channel callbacks
    are also re-wired so the new ``#channel`` tab actually renders
    incoming sends.
    """
    filt = EventFilter(
        kinds={
            EventKind.CREATURE_STARTED,
            EventKind.CREATURE_STOPPED,
            EventKind.TOPOLOGY_CHANGED,
            EventKind.SESSION_KIND_CHANGED,
        }
    )
    try:
        async for _ev in engine.subscribe(filt):
            graph = engine._topology.graphs.get(graph_id)
            if graph is None:
                continue
            env = engine._environments.get(graph_id)
            if env is None:
                continue
            graph_creatures = []
            for cid in graph.creature_ids:
                try:
                    graph_creatures.append(engine.get_creature(cid))
                except KeyError:
                    continue
            tabs = [focus_creature_id]
            tabs.extend(
                c.creature_id
                for c in graph_creatures
                if c.creature_id != focus_creature_id
            )
            tabs.extend(f"#{name}" for name in graph.channels)
            try:
                tui.set_terrarium_tabs(tabs)
            except Exception as exc:
                logger.debug("TUI tab refresh failed", error=str(exc))
            _update_terrarium_panel(tui, graph_creatures, env, focus_creature_id)
            _wire_new_channels(env, tui, wired_channels)
            for creature in graph_creatures:
                if creature.creature_id in routed_creatures:
                    continue
                creature_out = TUIOutput(session_key=creature.creature_id)
                creature_out._tui = tui
                creature_out._running = True
                creature_out._default_target = creature.creature_id
                creature.agent.output_router.default_output = creature_out
                routed_creatures.add(creature.creature_id)
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.debug("topology subscriber crashed", error=str(exc), exc_info=True)


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
