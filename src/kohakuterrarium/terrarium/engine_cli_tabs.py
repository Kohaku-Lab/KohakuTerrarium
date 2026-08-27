"""Tab-strip, panel, and channel-tab helpers for the Textual engine TUI.

Split out of :mod:`kohakuterrarium.terrarium.engine_cli` to keep both
modules under the repository's per-file size cap. ``run_engine_with_tui``
calls into these helpers when seeding identity/model state, wiring
channel callbacks, and reacting to live topology changes.
"""

import asyncio
from collections.abc import Iterable
from typing import Any

from kohakuterrarium.builtins.tui.output import TUIOutput
from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.core.channel import BaseChannel, ChannelMessage
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.utils.logging import get_logger

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


def _agent_model_identifier(agent: Any) -> str:
    """Canonical ``provider/name[@variations]`` for ``agent``, falling
    back to the raw model id — same string the web pill and ``/model``
    show, so every surface displays one identifier."""
    try:
        identifier = agent.llm_identifier()
    except Exception:
        identifier = ""
    return (
        identifier
        or getattr(getattr(agent, "llm", None), "model", "")
        or getattr(getattr(getattr(agent, "llm", None), "config", None), "model", "")
        or ""
    )


def _seed_tab_models(tui: TUISession, graph_creatures: Iterable[Any]) -> None:
    """Seed per-tab model and identity state from live creatures."""
    for creature in graph_creatures:
        agent = creature.agent
        model = _agent_model_identifier(agent)
        tui.update_target_identity(
            creature.creature_id,
            "",
            creature.name,
            getattr(creature, "config_name", ""),
            getattr(creature, "config_ref", None) or "",
        )
        if not model:
            continue
        max_ctx = 0
        compact_at = 0
        compact_mgr = getattr(agent, "compact_manager", None)
        if compact_mgr and compact_mgr.config.max_tokens:
            max_ctx = compact_mgr.config.max_tokens
            compact_at = int(max_ctx * compact_mgr.config.threshold)
        tui.update_target_model(creature.creature_id, model, max_ctx, compact_at)


def _update_session_info(tui: TUISession, creature, store: SessionStore | None) -> None:
    focus = creature.agent
    model = _agent_model_identifier(focus)
    session_id = ""
    if store:
        try:
            meta = store.load_meta()
            session_id = meta.get("session_id", "")
        except Exception as e:
            logger.warning(
                "Failed to load session meta for TUI", error=str(e), exc_info=True
            )
    tui.update_session_info(
        session_id=session_id,
        model=model,
        agent_name=creature.name,
        config_name=getattr(creature, "config_name", ""),
        config_ref=getattr(creature, "config_ref", None) or "",
    )
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
            EventKind.CREATURE_ADDED,
            EventKind.CREATURE_STARTED,
            EventKind.CREATURE_STOPPED,
            EventKind.TOPOLOGY_CHANGED,
            EventKind.SESSION_KIND_CHANGED,
        }
    )
    try:
        async for _ev in engine.subscribe(filt):
            graph_id = engine._topology.creature_to_graph.get(focus_creature_id)
            if graph_id is None:
                continue
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
                logger.warning("TUI tab refresh failed", error=str(exc), exc_info=True)
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
                tui.watch_command_agent(creature.creature_id, creature.agent)
                # New tab → seed its model line so the panel is right
                # the first time the user switches to it.
                _seed_tab_models(tui, [creature])
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("topology subscriber crashed", error=str(exc), exc_info=True)


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
