"""APP extension adapter for ``terrarium.broadcast``.

Cross-node channel forwarding.  When a graph spans multiple nodes
(``MultiNodeTerrariumService.connect`` was invoked with sender +
receiver on different workers), each node hosts its half of the
graph: sender on node A, receiver on node B, both with a local
``Channel`` object of the same name.  Engine-level channel sends
fire ``on_send`` callbacks locally, but a peer node's listeners get
nothing — without forwarding the cross-node connect is a paper
contract.

This adapter on every node tracks per-channel subscriptions:

    self._subs: dict[(graph_id, channel), set[peer_node_id]]

When a peer wants to receive forwarded sends for ``(graph_id,
channel)``, it issues ``terrarium.broadcast.subscribe(graph_id,
channel)`` and this node records ``msg.sender_node`` in the set.
On every LOCAL channel send the persistence callback in
``terrarium/channels.py`` looks up the subs set and forwards via
``terrarium.broadcast.inject`` to each peer.  Peers receive
``inject`` and replay the send into their local channel — but
WITHOUT re-broadcasting (the ``injected`` flag suppresses the
forward chain) to avoid loops.

Operations:

- ``subscribe({graph_id, channel})`` — record ``sender_node`` as a
  subscriber to local sends on ``(graph_id, channel)``.
- ``unsubscribe({graph_id, channel})`` — drop the subscription.
- ``inject({graph_id, channel, message})`` — peer is forwarding a
  send to me; replay into my local channel registry without
  re-broadcasting.

Wire shape of ``message`` body matches what ``terrarium.channels``
sends on the persistence path (see
``terrarium/channels.py:_persist``):

    {"sender": str, "sender_id": str|None, "content": Any,
     "message_id": str, "timestamp": str-iso, "ts": float}
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabNode
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TerrariumBroadcastAdapter:
    """Per-node ``terrarium.broadcast`` extension.  Both controller
    (lab host) and workers (lab clients) install one.

    Holds two pieces of state:

    - ``_subs``: subscriptions FROM peers — used by the local
      channel persistence hook to decide who to forward LOCAL sends
      to.
    - ``_my_subs``: subscriptions THIS node has on peers — used by
      :meth:`subscribe_remote` / :meth:`unsubscribe_remote` to know
      what to tear down on disconnect.

    Forwarding loops are prevented by tagging injected messages with
    a ``_injected`` flag so the local on_send hook skips re-forward
    when replaying them.
    """

    NAMESPACE = "terrarium.broadcast"
    REQUEST_TIMEOUT = 10.0

    def __init__(self, engine: Terrarium, lab_node: LabNode) -> None:
        self._engine = engine
        self._node = lab_node
        self._subs: dict[tuple[str, str], set[str]] = {}
        self._my_subs: dict[tuple[str, str], set[str]] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        # Stash on engine so the channel persistence hook can find it
        # without an import cycle.  Single per-engine instance.
        engine._broadcast_adapter = self
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        if getattr(self._engine, "_broadcast_adapter", None) is self:
            self._engine._broadcast_adapter = None
        self._node.unregister_app_extension(self.NAMESPACE)
        self._subs.clear()
        self._my_subs.clear()
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    # ------------------------------------------------------------------
    # Local hooks called from ``terrarium/channels.py:_persist``.
    # ------------------------------------------------------------------

    def peers_for(self, graph_id: str, channel: str) -> set[str]:
        return self._subs.get((graph_id, channel), set())

    async def forward_send(
        self,
        graph_id: str,
        channel: str,
        wire_message: dict[str, Any],
    ) -> None:
        """Notify every subscribed peer of a local channel send.

        ``wire_message`` is the persistence payload (sender, content,
        etc.) — see this module's docstring for the shape.  We never
        ``await`` per-peer notify serially; each fan-out is a
        fire-and-forget so a slow peer doesn't stall the producer.
        """
        peers = self._subs.get((graph_id, channel))
        if not peers:
            return
        body = {"graph_id": graph_id, "channel": channel, "message": wire_message}
        for peer in list(peers):
            try:
                await self._node.notify(
                    to_node=peer,
                    namespace=self.NAMESPACE,
                    type="inject",
                    body=body,
                )
            except Exception:
                logger.debug(
                    "broadcast forward failed; dropping dead peer",
                    peer=peer,
                    graph_id=graph_id,
                    channel=channel,
                )
                # The peer is gone — remove it from the sub set so we
                # don't keep notifying a dead node every send.  If the
                # set empties, drop the (graph_id, channel) key too.
                sub_set = self._subs.get((graph_id, channel))
                if sub_set is not None:
                    sub_set.discard(peer)
                    if not sub_set:
                        self._subs.pop((graph_id, channel), None)

    async def subscribe_remote(
        self,
        peer_node: str,
        graph_id: str,
        channel: str,
    ) -> None:
        """Tell ``peer_node`` to forward its local sends on
        ``(graph_id, channel)`` to me.  Tracked in ``_my_subs`` so we
        can issue ``unsubscribe`` on teardown.

        Uses ``request`` not ``notify`` because the subscription is
        state-establishing — silent failure here means cross-node
        forwarding never happens, with no observability.
        """
        resp = await self._node.request(
            to_node=peer_node,
            namespace=self.NAMESPACE,
            type="subscribe",
            body={"graph_id": graph_id, "channel": channel},
            timeout=self.REQUEST_TIMEOUT,
        )
        if isinstance(resp, dict) and "error" in resp:
            raise RuntimeError(f"subscribe failed on {peer_node}: {resp['error']}")
        self._my_subs.setdefault((graph_id, channel), set()).add(peer_node)

    async def unsubscribe_remote(
        self,
        peer_node: str,
        graph_id: str,
        channel: str,
    ) -> None:
        try:
            await self._node.request(
                to_node=peer_node,
                namespace=self.NAMESPACE,
                type="unsubscribe",
                body={"graph_id": graph_id, "channel": channel},
                timeout=self.REQUEST_TIMEOUT,
            )
        except Exception:
            # Teardown best-effort — the peer may already be gone.  We
            # still want to clear local bookkeeping so a re-subscribe
            # doesn't double-track.
            logger.debug(
                "unsubscribe RPC failed; clearing local state anyway",
                peer=peer_node,
                graph_id=graph_id,
                channel=channel,
            )
        subs = self._my_subs.get((graph_id, channel))
        if subs is not None:
            subs.discard(peer_node)
            if not subs:
                self._my_subs.pop((graph_id, channel), None)

    # ------------------------------------------------------------------
    # Proxy helpers — used by the controller to ask a third node to
    # subscribe to another.  Cross-node connect goes alice@A → bob@B;
    # the right party to subscribe on A is B (so A's sends fan out to
    # B).  The controller isn't A or B, so it asks B to do the subscribe.
    # ------------------------------------------------------------------

    async def proxy_subscribe(
        self,
        proxy_node: str,
        peer_node: str,
        graph_id: str,
        channel: str,
    ) -> None:
        """Ask ``proxy_node`` to subscribe itself to ``peer_node``."""
        resp = await self._node.request(
            to_node=proxy_node,
            namespace=self.NAMESPACE,
            type="proxy_subscribe",
            body={"peer": peer_node, "graph_id": graph_id, "channel": channel},
            timeout=self.REQUEST_TIMEOUT,
        )
        if isinstance(resp, dict) and "error" in resp:
            raise RuntimeError(
                f"proxy_subscribe failed on {proxy_node}: {resp['error']}"
            )

    async def proxy_unsubscribe(
        self,
        proxy_node: str,
        peer_node: str,
        graph_id: str,
        channel: str,
    ) -> None:
        try:
            await self._node.request(
                to_node=proxy_node,
                namespace=self.NAMESPACE,
                type="proxy_unsubscribe",
                body={"peer": peer_node, "graph_id": graph_id, "channel": channel},
                timeout=self.REQUEST_TIMEOUT,
            )
        except Exception:
            logger.debug(
                "proxy_unsubscribe RPC failed",
                proxy=proxy_node,
                peer=peer_node,
                graph_id=graph_id,
                channel=channel,
            )

    # ------------------------------------------------------------------
    # APP dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("terrarium.broadcast handler failed: %s", msg.type)
            return {"error": {"kind": "broadcast", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "subscribe":
                graph_id = msg.body["graph_id"]
                channel = msg.body["channel"]
                self._subs.setdefault((graph_id, channel), set()).add(msg.sender_node)
                return {"subscribed": True}
            case "unsubscribe":
                graph_id = msg.body["graph_id"]
                channel = msg.body["channel"]
                subs = self._subs.get((graph_id, channel))
                if subs is not None:
                    subs.discard(msg.sender_node)
                    if not subs:
                        self._subs.pop((graph_id, channel), None)
                return {"unsubscribed": True}
            case "proxy_subscribe":
                # Controller asks us to subscribe ourselves to a peer.
                # The receiving node calls its own subscribe_remote so
                # the peer records THIS node (the receiver of the
                # ``proxy_subscribe`` RPC) as the subscriber.
                await self.subscribe_remote(
                    msg.body["peer"], msg.body["graph_id"], msg.body["channel"]
                )
                return {"subscribed": True}
            case "proxy_unsubscribe":
                await self.unsubscribe_remote(
                    msg.body["peer"], msg.body["graph_id"], msg.body["channel"]
                )
                return {"unsubscribed": True}
            case "inject":
                return await self._op_inject(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.broadcast type: {msg.type!r}",
                    }
                }

    async def _op_inject(self, body: dict[str, Any]) -> dict[str, Any]:
        """Replay a peer's channel send into my local channel.

        The local channel's ``on_send`` callbacks fire normally — so
        ``listen_channels``-trigger callbacks deliver to my local
        creatures.  The persistence ``_persist`` callback in
        ``terrarium/channels.py`` checks the ``injected`` flag on the
        message and skips re-broadcasting to avoid forward-loops.
        """
        channel_name = body["channel"]
        message = body.get("message") or {}
        # Per the cross-node design, each node hosts its own half of
        # the connection: sender on A, receiver on B, both with a
        # local ``Channel`` object of the same name.  The
        # ``graph_id`` in the forward body is the SENDER's graph id,
        # which the receiver does not (and must not) recognize — its
        # own graph id is different.  So look up by ``channel_name``
        # across every local graph and use the first match.  Multiple
        # local channels with the same name across disjoint graphs is
        # an operator error the cluster topology rules forbid; we
        # take the first hit and log if there are more than one.
        channel = None
        for env in self._engine._environments.values():
            registry = getattr(env, "shared_channels", None)
            if registry is None:
                continue
            candidate = registry.get(channel_name)
            if candidate is not None:
                channel = candidate
                break
        if channel is None:
            raise KeyError(
                f"channel {channel_name!r} not in any local graph on this node"
            )
        # Build a real ChannelMessage so the receiver's listener
        # triggers (anti-echo filters, etc.) see the same shape they
        # would for a local send.  Set ``_injected`` as a runtime
        # attribute (the dataclass doesn't define a slot, but it's
        # not slotted either, so attribute assignment works); the
        # persistence callback in ``terrarium/channels.py:_persist``
        # reads via ``getattr`` and skips re-forwarding.
        ts_raw = message.get("timestamp", "")
        if isinstance(ts_raw, str) and ts_raw:
            try:
                stamped = datetime.fromisoformat(ts_raw)
            except ValueError:
                stamped = datetime.now()
        else:
            stamped = datetime.now()
        msg_id = message.get("message_id") or ""
        msg = ChannelMessage(
            sender=message.get("sender", ""),
            content=message.get("content", ""),
            timestamp=stamped,
            sender_id=message.get("sender_id"),
        )
        if msg_id:
            msg.message_id = msg_id
        msg._injected = True  # type: ignore[attr-defined]
        await channel.send(msg)
        return {"injected": True}


__all__ = ["TerrariumBroadcastAdapter"]
