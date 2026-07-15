"""Forward channel messages between Laboratory nodes.

Each node owns its local half of a cross-node channel and subscribes to sends
from the remote half. Injected messages retain normal local listener behavior
but are marked so the persistence hook does not broadcast them again.
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
    """Per-node channel subscription and message-forwarding extension.

    ``_subs`` records peers receiving this node's sends; ``_my_subs`` records
    this node's remote subscriptions for teardown. Injected messages are marked
    to prevent forwarding loops.
    """

    NAMESPACE = "terrarium.broadcast"
    REQUEST_TIMEOUT = 10.0

    def __init__(self, engine: Terrarium, lab_node: LabNode) -> None:
        self._engine = engine
        self._node = lab_node
        self._subs: dict[tuple[str, str], set[str]] = {}
        self._my_subs: dict[tuple[str, str], set[str]] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        # Engine discovery avoids an import cycle in the persistence hook.
        engine._broadcast_adapter = self
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        if getattr(self._engine, "_broadcast_adapter", None) is self:
            self._engine._broadcast_adapter = None
        self._node.unregister_app_extension(self.NAMESPACE)
        self._subs.clear()
        self._my_subs.clear()
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    def peers_for(self, graph_id: str, channel: str) -> set[str]:
        return self._subs.get((graph_id, channel), set())

    async def forward_send(
        self,
        graph_id: str,
        channel: str,
        wire_message: dict[str, Any],
    ) -> None:
        """Forward a local channel message to every subscribed peer."""
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
                # Stop retrying a peer that failed and remove empty buckets.
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
        """Subscribe this node to a peer's channel sends.

        A request confirms that the remote subscription state exists before it
        is recorded locally for teardown.
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
            # Clear local state even when the peer has already disappeared.
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
                # The proxy must subscribe itself so the peer records the
                # receiving node, not the controller, as the subscriber.
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
        """Replay a peer's send through the matching local channel.

        Local listeners run normally; the injected marker prevents the
        persistence callback from forwarding the message again.
        """
        channel_name = body["channel"]
        message = body.get("message") or {}
        # The transmitted graph ID belongs to the sender and differs from the
        # receiver's graph ID. Channel names are cluster-unique, so search local
        # graphs by name and use the first match.
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
        # Preserve the local message shape for listener filters. ChannelMessage
        # is not slotted, allowing the persistence hook's runtime marker.
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
