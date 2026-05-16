"""Unit tests for :class:`TerrariumBroadcastAdapter`."""

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_broadcast import (
    TerrariumBroadcastAdapter,
)
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeLabNode:
    def __init__(self, request_resp=None, request_fail=False):
        self.app_extensions = {}
        self.notifications = []
        self.requests = []
        self._request_resp = request_resp or {"ok": True}
        self._request_fail = request_fail

    def register_app_extension(self, ns, handler):
        self.app_extensions[ns] = handler

    def unregister_app_extension(self, ns):
        return self.app_extensions.pop(ns, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        self.notifications.append(
            {"to": to_node, "namespace": namespace, "type": type, "body": body}
        )

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.requests.append(
            {"to": to_node, "namespace": namespace, "type": type, "body": body}
        )
        if self._request_fail:
            raise RuntimeError("request failed")
        return self._request_resp


def _app_msg(type_, body, sender="ctrl"):
    return AppMessage(
        sender_node=sender,
        namespace="terrarium.broadcast",
        type=type_,
        body=body,
        request_id="r1",
        in_reply_to=None,
    )


# ── construction / detach ────────────────────────────────────


class TestLifecycle:
    async def test_init_stashes_on_engine(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            assert t._broadcast_adapter is adapter
            adapter.detach()
            assert t._broadcast_adapter is None
        finally:
            await t.shutdown()


# ── subscribe / unsubscribe via APP ─────────────────────────


class TestSubscribeOps:
    async def test_subscribe_records_sender(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "subscribe",
                    {"graph_id": "g1", "channel": "chat"},
                    sender="peer-1",
                )
            )
            assert resp["subscribed"] is True
            assert "peer-1" in adapter.peers_for("g1", "chat")
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_unsubscribe_removes_sender(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            await adapter._dispatch(
                _app_msg(
                    "subscribe",
                    {"graph_id": "g1", "channel": "chat"},
                    sender="peer-1",
                )
            )
            resp = await adapter._dispatch(
                _app_msg(
                    "unsubscribe",
                    {"graph_id": "g1", "channel": "chat"},
                    sender="peer-1",
                )
            )
            assert resp["unsubscribed"] is True
            assert adapter.peers_for("g1", "chat") == set()
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_unsubscribe_unknown_silent(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "unsubscribe",
                    {"graph_id": "ghost", "channel": "x"},
                    sender="p",
                )
            )
            assert resp["unsubscribed"] is True
        finally:
            adapter.detach()
            await t.shutdown()


# ── forward_send ─────────────────────────────────────────────


class TestForwardSend:
    async def test_no_subscribers_returns_silently(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            await adapter.forward_send("g1", "chat", {"x": 1})
            assert node.notifications == []
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_fans_out_to_each_peer(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            adapter._subs[("g1", "chat")] = {"p1", "p2"}
            await adapter.forward_send("g1", "chat", {"sender": "a"})
            assert len(node.notifications) == 2
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_notify_failure_swallowed(self):
        t = await TestTerrariumBuilder().build()

        class _BadNode(_FakeLabNode):
            async def notify(self, **kw):
                raise RuntimeError("delivery failed")

        node = _BadNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            adapter._subs[("g1", "chat")] = {"p1"}
            # Shouldn't raise.
            await adapter.forward_send("g1", "chat", {"x": 1})
        finally:
            adapter.detach()
            await t.shutdown()


# ── subscribe_remote / unsubscribe_remote ────────────────────


class TestSubscribeRemote:
    async def test_subscribe_remote_records_my_subs(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_resp={"ok": True})
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            await adapter.subscribe_remote("peer-1", "g1", "chat")
            assert "peer-1" in adapter._my_subs[("g1", "chat")]
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_subscribe_remote_error_raises(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_resp={"error": "bad"})
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            with pytest.raises(RuntimeError):
                await adapter.subscribe_remote("peer-1", "g1", "chat")
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_unsubscribe_remote_clears_my_subs(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            adapter._my_subs[("g1", "chat")] = {"peer-1"}
            await adapter.unsubscribe_remote("peer-1", "g1", "chat")
            assert ("g1", "chat") not in adapter._my_subs
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_unsubscribe_remote_swallows_request_failure(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_fail=True)
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            adapter._my_subs[("g1", "chat")] = {"peer-1"}
            # Should not raise even when the RPC errors.
            await adapter.unsubscribe_remote("peer-1", "g1", "chat")
            # Local bookkeeping still cleared.
            assert ("g1", "chat") not in adapter._my_subs
        finally:
            adapter.detach()
            await t.shutdown()


# ── proxy_subscribe / proxy_unsubscribe ─────────────────────


class TestProxyOps:
    async def test_proxy_subscribe(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_resp={"ok": True})
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            await adapter.proxy_subscribe("proxy", "peer", "g", "c")
            # The proxy RPC is forwarded to the proxy node as a
            # proxy_subscribe request carrying the peer + graph + channel.
            assert len(node.requests) == 1
            req = node.requests[0]
            assert req["to"] == "proxy"
            assert req["namespace"] == "terrarium.broadcast"
            assert req["type"] == "proxy_subscribe"
            assert req["body"] == {
                "peer": "peer",
                "graph_id": "g",
                "channel": "c",
            }
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_proxy_subscribe_error_raises(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_resp={"error": "bad"})
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            with pytest.raises(RuntimeError):
                await adapter.proxy_subscribe("p", "x", "g", "c")
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_proxy_unsubscribe_failure_silent(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_fail=True)
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            # Should not raise.
            await adapter.proxy_unsubscribe("p", "x", "g", "c")
        finally:
            adapter.detach()
            await t.shutdown()


# ── _dispatch: unknown, proxy_subscribe, proxy_unsubscribe ───


class TestDispatchExtras:
    async def test_unknown_type(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(_app_msg("garbage", {}))
            assert resp["error"]["kind"] == "unknown_type"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_proxy_subscribe_dispatches_through(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode(request_resp={"ok": True})
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "proxy_subscribe",
                    {"peer": "p", "graph_id": "g", "channel": "c"},
                )
            )
            assert resp["subscribed"] is True
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_proxy_unsubscribe_dispatches_through(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "proxy_unsubscribe",
                    {"peer": "p", "graph_id": "g", "channel": "c"},
                )
            )
            assert resp["unsubscribed"] is True
        finally:
            adapter.detach()
            await t.shutdown()


# ── _op_inject ──────────────────────────────────────────────


class TestOpInject:
    async def test_inject_unknown_channel(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg("inject", {"channel": "ghost", "message": {}})
            )
            assert resp["error"]["kind"] == "not_found"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_into_local_channel(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "inject",
                    {
                        "graph_id": "g-anything",  # ignored on receiver
                        "channel": "chat",
                        "message": {
                            "sender": "peer-alice",
                            "content": "hi",
                            "message_id": "m1",
                            "timestamp": "2026-01-01T00:00:00",
                        },
                    },
                )
            )
            assert resp["injected"] is True
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_with_invalid_timestamp_falls_back(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "inject",
                    {
                        "channel": "chat",
                        "message": {
                            "sender": "p",
                            "content": "x",
                            "timestamp": "not-a-date",
                        },
                    },
                )
            )
            assert resp["injected"] is True
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_with_no_timestamp(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "inject",
                    {
                        "channel": "chat",
                        "message": {"sender": "p", "content": "x"},
                    },
                )
            )
            assert resp["injected"] is True
        finally:
            adapter.detach()
            await t.shutdown()


# ── error mapping + channel-scan resilience ─────────────────


class TestBroadcastErrorMapping:
    async def test_value_error_maps_to_invalid(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)

            async def _boom(body):
                raise ValueError("bad inject payload")

            adapter._op_inject = _boom
            resp = await adapter._dispatch(_app_msg("inject", {"channel": "c"}))
            assert resp["error"]["kind"] == "invalid"
            assert "bad inject payload" in resp["error"]["message"]
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_channel_scan_skips_env_without_shared_channels(self):
        # _find_channel scans every environment; an environment missing
        # its ``shared_channels`` registry must be skipped, not crash
        # the lookup.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumBroadcastAdapter(t, node)
            from types import SimpleNamespace

            # Inject a degenerate environment with no shared_channels.
            t._environments["broken-env"] = SimpleNamespace()
            resp = await adapter._dispatch(
                _app_msg(
                    "inject",
                    {
                        "channel": "no-such-channel",
                        "message": {"sender": "p", "content": "x"},
                    },
                )
            )
            # The broken env was skipped; the unknown channel still
            # surfaces as a clean error rather than an AttributeError.
            assert "error" in resp
        finally:
            adapter.detach()
            await t.shutdown()
