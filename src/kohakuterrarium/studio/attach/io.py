"""Provide engine-backed bidirectional websocket attachment for creatures.

A connection translates output events into frontend frames and accepts input for
its bound creature or named siblings. Multi-creature attachments also expose shared
channel history and live messages through the same websocket.
"""

import asyncio
import time
from typing import Any

from fastapi import WebSocket

from kohakuterrarium.core.pending_input import new_pending_id
from kohakuterrarium.laboratory.ws_proxy import proxy_ws_to_lab
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.attach._event_stream import StreamOutput, get_event_log
from kohakuterrarium.studio.attach.input_ops import (
    _handle_pending_op,
    _normalize_input_content,
    _process_input,
    _resolve_target,
)
from kohakuterrarium.studio.attach.io_cluster import attach_io_cluster
from kohakuterrarium.studio.sessions.cluster_fold import cluster_groups
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _session_info_frame(creature: Any) -> dict[str, Any]:
    """Build the per-creature ``session_info`` frame sent at attachment.

    The shape matches model-switch events so initial and updated tab state use the
    same canonical LLM name, raw model ID, and context limits. ``source`` must be the
    creature display name because the client keys model metadata by tab name.
    """
    agent = creature.agent
    try:
        llm_name = agent.llm_identifier()
    except Exception:
        llm_name = ""
    llm = getattr(agent, "llm", None)
    model = getattr(llm, "model", "") or getattr(agent.config, "model", "") or ""
    max_context = getattr(llm, "_profile_max_context", 0) or 0
    compact_manager = getattr(agent, "compact_manager", None)
    compact_at = 0
    if compact_manager is not None and max_context:
        try:
            compact_at = int(max_context * compact_manager.config.threshold)
        except Exception:
            compact_at = 0
    return {
        "type": "activity",
        "activity_type": "session_info",
        "source": creature.name,
        "model": model,
        "llm_name": llm_name,
        "agent_name": creature.name,
        "max_context": max_context,
        "compact_threshold": compact_at,
        "ts": time.time(),
    }


def _handle_ui_reply(
    data: dict[str, Any],
    engine: Any,
    creature: Any,
    session_id: str,
    queue: asyncio.Queue,
) -> None:
    """Route an inbound ``ui_reply`` frame to the addressed creature's router.

    Interactive prompts may originate from a sibling rather than the creature bound
    to this connection, so replies follow the frame's ``target``. Misrouting leaves
    the sibling's pending reply unresolved. The acknowledgement uses the resolved
    creature name and is emitted even when the event is unknown or superseded.
    """
    event_id = data.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return
    try:
        target = _resolve_target(engine, creature, session_id, data)
    except KeyError:
        # Preserve replies from clients without a valid target by using the bound bus.
        target = creature
    action_id = data.get("action_id", "")
    values = data.get("values") or {}
    user = data.get("user")
    ts = data.get("ts") or time.time()

    reply = UIReply(
        event_id=event_id,
        action_id=action_id,
        values=values if isinstance(values, dict) else {},
        user=user if isinstance(user, str) else None,
        timestamp=float(ts) if isinstance(ts, (int, float)) else time.time(),
    )

    try:
        _accepted, ack_status = target.agent.output_router.submit_reply_with_status(
            reply
        )
    except Exception as e:
        logger.warning("submit_reply failed", error=str(e), exc_info=True)
        ack_status = "unknown"

    ack = {
        "type": "ui_reply_ack",
        "event_id": event_id,
        "status": ack_status,
        "source": target.name,
        "ts": time.time(),
    }
    try:
        queue.put_nowait(ack)
    except asyncio.QueueFull:
        logger.debug("ui_reply_ack dropped — queue full")


async def _forward_queue(queue: asyncio.Queue, ws: WebSocket) -> None:
    """Drain queued frames while isolating failures to the individual send.

    A malformed or non-serializable frame must not terminate the forwarding task;
    later valid frames would otherwise accumulate indefinitely with no visible
    websocket failure.
    """
    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break
            try:
                await ws.send_json(msg)
            except Exception as send_exc:
                logger.warning(
                    "WS send_json failed — dropping frame, forward task continues",
                    msg_type=(msg.get("type") if isinstance(msg, dict) else None),
                    activity_type=(
                        msg.get("activity_type") if isinstance(msg, dict) else None
                    ),
                    error=str(send_exc),
                    exc_info=True,
                )
    except Exception as e:
        logger.warning("WS forward queue error", error=str(e), exc_info=True)


def _register_channel_callbacks(
    env: Any, queue: asyncio.Queue
) -> list[tuple[Any, Any]]:
    """Subscribe the outbound queue to every shared channel in an environment."""
    out: list[tuple[Any, Any]] = []

    def make_cb(ch_name: str):
        def cb(channel_name, message):
            ts = (
                message.timestamp.isoformat()
                if hasattr(message.timestamp, "isoformat")
                else str(message.timestamp)
            )
            queue.put_nowait(
                {
                    "type": "channel_message",
                    "source": "channel",
                    "channel": channel_name,
                    "sender": message.sender,
                    "content": message.content,
                    "message_id": message.message_id,
                    "timestamp": ts,
                    "ts": time.time(),
                }
            )

        return cb

    for ch in env.shared_channels._channels.values():
        cb = make_cb(ch.name)
        ch.on_send(cb)
        out.append((ch, cb))
    return out


async def _send_channel_history(ws: WebSocket, env: Any) -> None:
    """Replay shared-channel messages that predate this websocket attachment."""
    for ch in env.shared_channels._channels.values():
        for msg in ch.history:
            ts = (
                msg.timestamp.isoformat()
                if hasattr(msg.timestamp, "isoformat")
                else str(msg.timestamp)
            )
            await ws.send_json(
                {
                    "type": "channel_message",
                    "source": "channel",
                    "channel": ch.name,
                    "sender": msg.sender,
                    "content": msg.content,
                    "message_id": msg.message_id,
                    "timestamp": ts,
                    "ts": time.time(),
                    "history": True,
                }
            )


async def _attach_io_remote(
    websocket: WebSocket,
    service: "TerrariumService",
    creature_info: Any,
    session_id: str,
) -> None:
    """Proxy a full-fidelity attachment to the creature's remote worker.

    The worker adapter mirrors local output, sibling, channel, and interactive-reply
    behavior, so the controller only needs to relay the websocket protocol.
    """
    cid = creature_info.creature_id
    home = await _resolve_creature_home(service, cid)
    if home is None or home == "_host":
        raise KeyError(cid)
    await proxy_ws_to_lab(
        websocket=websocket,
        sender=service.host,
        demux=service.demux,
        target_node=home,
        namespace="terrarium.attach",
        body={"creature_id": cid, "session_id": session_id},
    )


async def _resolve_creature_home(service: "TerrariumService", cid: str) -> str | None:
    """Return the creature's worker ID, ``"_host"``, or ``None`` on failure.

    ``"_host"`` denotes a service without multi-node routing. Resolver failures are
    converted to ``None`` so the attachment boundary can report a uniform missing
    creature error.
    """
    resolver = getattr(service, "_resolve_home", None)
    if resolver is None:
        return "_host"
    try:
        return await resolver(cid)
    except Exception:
        return None


async def attach_io(
    websocket: WebSocket,
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
) -> None:
    """Run a local or proxied creature attachment until disconnection.

    Local creatures receive secondary output sinks directly. Remote creatures are
    attached through their worker because the controller has no local agent router.
    """
    # Lab hosts have no local engine, while standalone services attach directly to
    # the creature's output router.
    engine = host_engine_or_none(service)
    creature = None
    if engine is not None:
        try:
            creature = find_creature(engine, session_id, creature_id)
        except KeyError:
            creature = None
    if creature is None:
        # Cluster attachments multiplex one upstream per worker. Targeted inputs are
        # routed to the owning worker, while channel replicas converge by message ID.
        groups = cluster_groups(service)
        primary = None
        for prim, member_sids in groups.items():
            if session_id == prim or session_id in member_sids:
                primary = prim
                break
        if primary is not None:
            try:
                await attach_io_cluster(websocket, service, primary, creature_id)
                return
            except KeyError:
                # A stale cluster view degrades to a single-worker attachment.
                pass
        # Service lookup locates remote creatures when no host-local instance exists.
        info = await service.get_creature_info(creature_id)
        if info is None:
            # Client routes may carry a display name, while direct service lookup is
            # ID-only; the cluster-wide listing supplies the equivalent name lookup.
            try:
                for candidate in await service.list_creatures():
                    if candidate.name == creature_id:
                        info = candidate
                        break
            except Exception:  # pragma: no cover - remote listing failure is handled
                info = None
        if info is None:
            raise KeyError(creature_id)
        await _attach_io_remote(websocket, service, info, session_id)
        return
    agent = creature.agent

    queue: asyncio.Queue = asyncio.Queue()
    log = get_event_log(f"{session_id}:{creature.creature_id}")
    out_module = StreamOutput(creature.name, queue, log, agent=agent)
    agent.output_router.add_secondary(out_module)

    # One graph websocket serves every creature tab, so sibling output must share
    # the bound creature's outbound queue.
    sibling_modules: list[tuple[Any, Any]] = []
    siblings: list[Any] = []
    if creature.graph_id and creature.graph_id in engine._topology.graphs:
        graph = engine._topology.graphs[creature.graph_id]
        for cid in graph.creature_ids:
            if cid == creature.creature_id:
                continue
            try:
                sibling = engine.get_creature(cid)
            except KeyError:
                continue
            sib_module = StreamOutput(sibling.name, queue, log, agent=sibling.agent)
            sibling.agent.output_router.add_secondary(sib_module)
            sibling_modules.append((sibling.agent, sib_module))
            siblings.append(sibling)

    # Channel history and live sends share the same ordered websocket stream.
    env = engine._environments.get(creature.graph_id)
    channel_cbs: list[tuple[Any, Any]] = []
    if env is not None and env.shared_channels.list_channels():
        channel_cbs = _register_channel_callbacks(env, queue)
        await _send_channel_history(websocket, env)

    # Initialization events predate attachment, so each tab needs a fresh model-state
    # frame before live forwarding begins.
    await websocket.send_json(_session_info_frame(creature))
    for sibling in siblings:
        try:
            await websocket.send_json(_session_info_frame(sibling))
        except Exception:  # pragma: no cover - one sibling must not block attachment
            logger.debug("sibling session_info frame failed", exc_info=True)

    fwd_task = asyncio.create_task(_forward_queue(queue, websocket))

    # Input runs independently so the receive loop can deliver replies awaited by
    # interactive tools. Turns belong to the engine rather than the viewer and must
    # survive websocket refreshes or disconnects.
    input_tasks: list[asyncio.Task] = []

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ui_reply":
                # The addressed router resolves the pending prompt and supersedes peers.
                _handle_ui_reply(data, engine, creature, session_id, queue)
                continue
            if msg_type == "ui_dismiss":
                # Display-only dismissals require no pending-future resolution.
                continue
            if msg_type in ("input_edit", "input_cancel"):
                # Pending operations are valid only until the mid-turn drain claims the ID.
                _handle_pending_op(data, msg_type, engine, creature, session_id, queue)
                continue
            if msg_type != "input":
                continue
            content = _normalize_input_content(data)
            if not content:
                continue
            # A single graph websocket accepts input for every named creature tab.
            target_name = (data.get("target") or "").strip()
            target_creature = creature
            target_agent = agent
            if target_name and target_name != creature.name:
                try:
                    target_creature = find_creature(
                        engine, creature.graph_id or session_id, target_name
                    )
                    target_agent = target_creature.agent
                except KeyError:
                    queue.put_nowait(
                        {
                            "type": "error",
                            "source": target_name or creature.name,
                            "content": (
                                f"Cannot route to creature {target_name!r}: not "
                                "found in this session."
                            ),
                            "ts": time.time(),
                        }
                    )
                    continue
            # A stable ID keeps buffered input addressable for edit or cancellation.
            pending_id = (data.get("event_id") or "").strip() or new_pending_id()
            user_evt = {
                "type": "user_input",
                "source": target_creature.name,
                "content": content,
                "event_id": pending_id,
                "ts": time.time(),
            }
            log.append(user_evt)
            await queue.put(user_evt)
            # Do not block delivery of replies that the injected turn may await.
            task = asyncio.create_task(
                _process_input(
                    target_agent, content, queue, target_creature.name, pending_id
                )
            )
            input_tasks.append(task)
            # Retain only live tasks needed to preserve their ownership.
            input_tasks[:] = [t for t in input_tasks if not t.done()]
    finally:
        queue.put_nowait(None)
        fwd_task.cancel()
        # Engine-owned turns outlive the viewer. Their final frames may enter the
        # unbounded orphaned queue, which is released after the tasks finish.
        try:
            agent.output_router.remove_secondary(out_module)
        except Exception as e:
            logger.warning(
                "Failed to remove secondary output",
                error=str(e),
                exc_info=True,
            )
        # Every per-connection sibling sink must be removed on disconnect.
        for sib_agent, sib_module in sibling_modules:
            try:
                sib_agent.output_router.remove_secondary(sib_module)
            except Exception:
                logger.warning(
                    "Failed to remove sibling secondary output",
                    exc_info=True,
                )
        for ch, cb in channel_cbs:
            try:
                ch.remove_on_send(cb)
            except Exception as e:
                logger.warning(
                    "Failed to remove channel callback",
                    error=str(e),
                    exc_info=True,
                )
