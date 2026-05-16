"""Cluster-aware multi-worker attach multiplexer (CF-1 / CF-2).

The single-worker IO attach (``studio.attach.io.attach_io`` →
``_attach_io_remote``) routes a chat WS to ONE worker via
``proxy_ws_to_lab``. That is correct for non-cluster sessions but
fails the multi-node cluster invariant in two ways:

- **CF-1 (B11)**: when the user types with ``target=<cross-worker
  creature>``, the bound worker's ``_find_sibling_by_name`` searches
  its local engine graph only — cross-cluster siblings miss and the
  WS surfaces ``"Cannot route to creature X: not found in this
  session."`` even though the cluster is meant to look like ONE
  session.
- **CF-2**: channel callbacks + history come from the bound worker's
  replica. Channels in a cluster are replicated on every member
  worker's engine; the bound worker only sees its own replica, so
  messages sent via a peer worker never reach the chat WS history.

This module hosts :func:`attach_io_cluster`, the cluster-aware
multiplexer. It opens one upstream :class:`RemoteStream` per cluster
member worker via the existing ``terrarium.attach`` APP namespace —
every worker's ``TerrariumAttachAdapter`` runs its full producer
behaviour (``StreamOutput`` + sibling subscribe + channel callbacks)
and pumps frames into the host's per-worker upstream. The host
merges every upstream onto the single client WS, deduping
``channel_message`` frames by ``message_id``, and routes inbound
input frames by ``target=`` to whichever worker hosts the named
creature.
"""

import asyncio
import time
from typing import Any

from fastapi import WebSocket

from kohakuterrarium.laboratory.streams import RemoteStream
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


async def _forward_queue(queue: asyncio.Queue, ws: WebSocket) -> None:
    """Drain ``queue`` onto ``ws`` until a ``None`` sentinel arrives."""
    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break
            await ws.send_json(msg)
    except Exception as e:
        logger.debug("cluster mux: forward queue error", error=str(e), exc_info=True)


async def attach_io_cluster(
    websocket: WebSocket,
    service: "TerrariumService",
    primary_sid: str,
    bound_creature_id: str,
) -> None:
    """Run the cluster IO multiplexer until ``websocket`` disconnects.

    Inbound frames:
    - ``input`` with ``target=<name>``: dispatched to the upstream whose
      worker hosts ``<name>``. If unknown, surface the same
      "not found in this session" error frame the single-worker path
      emits so a genuine miss is still distinguishable.
    - ``input`` with no target: dispatched to the upstream of the
      URL-bound creature (default route).
    - ``ui_reply`` / ``ui_dismiss``: broadcast to every upstream — only
      the originating worker has the matching ``event_id`` in its
      ``output_router``; peers reply with ``ack_status="unknown"`` and
      the originating worker's ack reaches the client.

    Outbound frames from every upstream are merged onto the client WS
    in arrival order. ``channel_message`` frames are deduped by
    ``message_id`` (with a fallback synthetic key on
    ``(channel, sender, content, timestamp)``) so cluster-replicated
    channels emit each message once.
    """
    members_fn = getattr(service, "_cluster_members_for", None)
    members: list[tuple[str, str]] = []
    if callable(members_fn):
        try:
            members = list(members_fn(primary_sid))
        except Exception:
            members = []
    if not members:
        raise KeyError(primary_sid)

    queue: asyncio.Queue = asyncio.Queue()
    upstreams: list[tuple[str, str, RemoteStream]] = []
    target_routes: dict[str, tuple[str, str, RemoteStream]] = {}
    default_route: tuple[str, str, RemoteStream] | None = None

    try:
        all_creatures = await service.list_creatures()
    except Exception:
        all_creatures = ()
    home_map: dict[str, str] = dict(getattr(service, "_home", {}) or {})
    by_node: dict[str, list[Any]] = {}
    for info in all_creatures:
        node = home_map.get(info.creature_id, "_host")
        by_node.setdefault(node, []).append(info)

    async def _open_upstream(node_id: str, member_sid: str) -> None:
        nonlocal default_route
        worker_creatures = by_node.get(node_id, [])
        if not worker_creatures:
            return
        bound_local = None
        for info in worker_creatures:
            if (
                getattr(info, "creature_id", None) == bound_creature_id
                or getattr(info, "name", None) == bound_creature_id
            ):
                bound_local = info
                break
        if bound_local is None:
            bound_local = worker_creatures[0]
        cid = bound_local.creature_id
        rs = await RemoteStream.open(
            demux=service.demux,
            sender=service.host,
            target_node=node_id,
            start_namespace="terrarium.attach",
            start_type="start",
            cancel_namespace="terrarium.attach",
            body={"creature_id": cid, "session_id": member_sid},
        )
        upstreams.append((node_id, member_sid, rs))
        for info in worker_creatures:
            target_routes[info.creature_id] = (node_id, member_sid, rs)
            if getattr(info, "name", None):
                target_routes[info.name] = (node_id, member_sid, rs)
        if (
            bound_local.creature_id == bound_creature_id
            or bound_local.name == bound_creature_id
        ):
            default_route = (node_id, member_sid, rs)
        setup = (rs.start_response or {}).get("setup")
        if isinstance(setup, dict):
            try:
                queue.put_nowait(setup)
            except asyncio.QueueFull:
                logger.debug("cluster mux: setup queue full")

    await asyncio.gather(
        *(_open_upstream(node_id, sid) for node_id, sid in members),
        return_exceptions=True,
    )
    if not upstreams:
        raise KeyError(primary_sid)
    if default_route is None:
        default_route = upstreams[0]

    seen_msg_ids: set[str] = set()

    async def _pump_upstream(node_id: str, rs: RemoteStream) -> None:
        try:
            async for frame in rs:
                if "eof" in frame:
                    break
                ws_frame = {k: v for k, v in frame.items() if k != "stream_id"}
                if ws_frame.get("type") == "channel_message":
                    mid = ws_frame.get("message_id")
                    if isinstance(mid, str) and mid:
                        if mid in seen_msg_ids:
                            continue
                        seen_msg_ids.add(mid)
                    else:
                        key = (
                            ws_frame.get("channel"),
                            ws_frame.get("sender"),
                            str(ws_frame.get("content")),
                            ws_frame.get("timestamp"),
                        )
                        skey = "|".join(str(x) for x in key)
                        if skey in seen_msg_ids:
                            continue
                        seen_msg_ids.add(skey)
                try:
                    queue.put_nowait(ws_frame)
                except asyncio.QueueFull:
                    logger.debug("cluster mux: outbox queue full")
        except Exception as exc:
            logger.debug("cluster mux upstream ended", node=node_id, error=str(exc))

    pump_tasks = [
        asyncio.create_task(_pump_upstream(node_id, rs))
        for node_id, _sid, rs in upstreams
    ]
    fwd_task = asyncio.create_task(_forward_queue(queue, websocket))

    async def _send_input(route: tuple[str, str, RemoteStream], frame: dict) -> None:
        node_id, _sid, rs = route
        try:
            await service.host.request(
                to_node=node_id,
                namespace="terrarium.attach",
                type="input",
                body={"stream_id": rs.stream_id, "frame": frame},
                timeout=10.0,
            )
        except Exception as exc:
            logger.debug("cluster mux input forward failed", error=str(exc))

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "input":
                target_name = (data.get("target") or "").strip()
                route = default_route
                if target_name:
                    if target_name not in target_routes:
                        try:
                            queue.put_nowait(
                                {
                                    "type": "error",
                                    "source": target_name,
                                    "content": (
                                        f"Cannot route to creature {target_name!r}: "
                                        "not found in this session."
                                    ),
                                    "ts": time.time(),
                                }
                            )
                        except asyncio.QueueFull:
                            logger.debug("cluster mux: error queue full")
                        continue
                    route = target_routes[target_name]
                if route is not None:
                    await _send_input(route, dict(data))
            elif msg_type in ("ui_reply", "ui_dismiss"):
                for node_id, _sid, rs in upstreams:
                    try:
                        await service.host.request(
                            to_node=node_id,
                            namespace="terrarium.attach",
                            type="input",
                            body={"stream_id": rs.stream_id, "frame": data},
                            timeout=10.0,
                        )
                    except Exception:
                        logger.debug(
                            "cluster mux: ui_reply forward failed",
                            node=node_id,
                            exc_info=True,
                        )
    finally:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        fwd_task.cancel()
        for t in pump_tasks:
            t.cancel()
        for _node_id, _sid, rs in upstreams:
            try:
                await rs.aclose()
            except Exception:
                logger.debug("cluster mux: aclose failed", exc_info=True)


__all__ = ["attach_io_cluster"]
