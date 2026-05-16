"""Unit tests for :class:`TerrariumOutputWireAdapter`."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_output_wire import (
    TerrariumOutputWireAdapter,
)
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeNotifier:
    def __init__(self, fail=False):
        self.app_extensions = {}
        self.notifications = []
        self._fail = fail

    def register_app_extension(self, ns, handler):
        self.app_extensions[ns] = handler

    def unregister_app_extension(self, ns):
        return self.app_extensions.pop(ns, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        if self._fail:
            raise RuntimeError("delivery failed")
        self.notifications.append(
            {"to": to_node, "namespace": namespace, "type": type, "body": body}
        )


def _app_msg(type_, body, sender="ctrl"):
    return AppMessage(
        sender_node=sender,
        namespace="terrarium.output_wire",
        type=type_,
        body=body,
        request_id="r1",
        in_reply_to=None,
    )


# ── construction / detach ────────────────────────────────────


class TestLifecycle:
    async def test_init_stashes_on_engine(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            assert "terrarium.output_wire" in notifier.app_extensions
            assert t._output_wire_adapter is adapter
            adapter.detach()
            assert t._output_wire_adapter is None
        finally:
            await t.shutdown()

    async def test_detach_idempotent(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.detach()
            # Second detach is a no-op.
            adapter.detach()
        finally:
            await t.shutdown()


# ── peer_for_target ──────────────────────────────────────────


class TestPeerForTarget:
    async def test_no_resolver_means_worker_delegates_to_host(self):
        # On a worker (which never installs a resolver), an unresolved
        # local target forwards to the host — the cluster relay routes
        # it from there.  "Lab host = transparent relay" UX invariant.
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            assert adapter.peer_for_target("anyone") == "_host"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_resolver_returns_none(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.set_target_resolver(lambda name: None)
            assert adapter.peer_for_target("anyone") is None
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_resolver_returns_host_treated_as_local(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.set_target_resolver(lambda n: ("_host", "cid-x"))
            assert adapter.peer_for_target("a") is None
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_resolver_returns_empty_node_id(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.set_target_resolver(lambda n: ("", "cid-x"))
            assert adapter.peer_for_target("a") is None
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_resolver_returns_worker(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.set_target_resolver(lambda n: ("worker-1", "cid-x"))
            assert adapter.peer_for_target("a") == "worker-1"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_resolver_exception_returns_none(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)

            def _boom(n):
                raise RuntimeError("bad")

            adapter.set_target_resolver(_boom)
            assert adapter.peer_for_target("a") is None
        finally:
            adapter.detach()
            await t.shutdown()


# ── forward_event ────────────────────────────────────────────


class TestForwardEvent:
    async def test_successful_forward(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            ok = await adapter.forward_event("worker-1", {"target_name": "x"})
            assert ok is True
            assert notifier.notifications
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_failed_forward_returns_false(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier(fail=True)
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            ok = await adapter.forward_event("worker-1", {"target_name": "x"})
            assert ok is False
        finally:
            adapter.detach()
            await t.shutdown()


# ── _dispatch / _handle ──────────────────────────────────────


class TestDispatch:
    async def test_unknown_type(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            resp = await adapter._dispatch(_app_msg("garbage", {}))
            assert resp["error"]["kind"] == "unknown_type"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_missing_target_name(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            resp = await adapter._dispatch(_app_msg("inject", {}))
            assert resp["error"]["kind"] == "invalid"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_unknown_creature(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            resp = await adapter._dispatch(_app_msg("inject", {"target_name": "ghost"}))
            assert resp["error"]["kind"] == "not_found"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_target_not_running(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            # Make alice not running.
            t.get_creature("alice").agent._running = False
            resp = await adapter._dispatch(_app_msg("inject", {"target_name": "alice"}))
            assert resp["delivered"] is False
            assert "not_running" in resp["reason"]
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_success(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            agent = t.get_creature("alice").agent
            agent._running = True
            agent._process_event = AsyncMock()
            resp = await adapter._dispatch(
                _app_msg(
                    "inject",
                    {
                        "target_name": "alice",
                        "source": "bob",
                        "content": "hello",
                        "with_content": True,
                        "source_event_type": "tool_result",
                        "turn_index": 3,
                        "prompt_override": "[wire]",
                    },
                )
            )
            assert resp["delivered"] is True
            # Give the asyncio task a moment to run.
            await asyncio.sleep(0.05)
            agent._process_event.assert_called_once()
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_with_router_activity_notification(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            agent = t.get_creature("alice").agent
            agent._running = True
            agent._process_event = AsyncMock()
            agent.output_router = SimpleNamespace(notify_activity=MagicMock())
            resp = await adapter._dispatch(
                _app_msg(
                    "inject",
                    {
                        "target_name": "alice",
                        "source": "bob",
                        "content": "x" * 300,  # exercise the truncation path
                    },
                )
            )
            assert resp["delivered"] is True
            agent.output_router.notify_activity.assert_called_once()
            # Verify truncation suffix landed.
            args, kw = agent.output_router.notify_activity.call_args
            preview = kw["metadata"]["content_preview"]
            assert preview.endswith("…")
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_router_notify_failure_swallowed(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            agent = t.get_creature("alice").agent
            agent._running = True
            agent._process_event = AsyncMock()

            def _boom(*a, **kw):
                raise RuntimeError("bad router")

            agent.output_router = SimpleNamespace(notify_activity=_boom)
            resp = await adapter._dispatch(_app_msg("inject", {"target_name": "alice"}))
            assert resp["delivered"] is True
        finally:
            adapter.detach()
            await t.shutdown()


# ── _resolve_local_agent ─────────────────────────────────────


class TestResolveLocalAgent:
    async def test_by_creature_id(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            agent = adapter._resolve_local_agent("alice")
            assert agent is t.get_creature("alice").agent
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_by_config_name(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            creature = t.get_creature("alice")
            # Rename so creature_id != name; mark config.name as the
            # canonical lookup key.
            creature.name = "other"
            creature.agent.config = SimpleNamespace(name="alpha")
            agent = adapter._resolve_local_agent("alpha")
            assert agent is creature.agent
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_by_display_name(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            creature = t.get_creature("alice")
            # creature_id stays "alice" but the display name differs;
            # lookup by the display name must still resolve the agent.
            creature.name = "display-name"
            agent = adapter._resolve_local_agent("display-name")
            assert agent is creature.agent
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_unknown(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            assert adapter._resolve_local_agent("ghost") is None
        finally:
            adapter.detach()
            await t.shutdown()


# ── host-side cluster relay on local-miss ────────────────────────


class TestHostRelay:
    """The host's _op_inject must re-route to the peer that owns the
    target when the local engine doesn't have it.  This is what makes
    a worker→host→worker output-wiring path actually deliver: workers
    forward unresolved emissions to the host, and the host re-forwards
    to the right peer based on its cluster name resolver.
    """

    async def test_local_miss_relays_to_peer(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            # Host-side: install a resolver that knows the target lives
            # on worker-2.  No matching local creature.
            adapter.set_target_resolver(lambda name: ("worker-2", "cid-x"))
            msg = _app_msg(
                "inject",
                {
                    "target_name": "alpha",
                    "source": "bravo",
                    "content": "hello",
                    "with_content": True,
                    "source_event_type": "user_message",
                    "turn_index": 0,
                },
            )
            out = await adapter._dispatch(msg)
            assert out == {"delivered": True, "relayed": "worker-2"}
            # The host forwarded an APP ``inject`` to worker-2 with the
            # ``relayed`` flag so the worker won't double-bounce on a
            # second miss.
            assert len(notifier.notifications) == 1
            n = notifier.notifications[0]
            assert n["to"] == "worker-2"
            assert n["type"] == "inject"
            assert n["body"]["relayed"] is True
            assert n["body"]["target_name"] == "alpha"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_already_relayed_does_not_re_relay(self):
        # Loop guard: a worker that receives a host-relayed inject and
        # STILL can't resolve locally must raise rather than forwarding
        # again — otherwise an inject for a vanished creature ping-pongs.
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.set_target_resolver(lambda name: ("worker-2", "cid-x"))
            msg = _app_msg(
                "inject",
                {
                    "target_name": "alpha",
                    "source": "bravo",
                    "content": "hello",
                    "relayed": True,  # already a relayed payload
                },
            )
            out = await adapter._dispatch(msg)
            assert out.get("error", {}).get("kind") == "not_found"
            # No further forwarding.
            assert notifier.notifications == []
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_resolver_returns_none_raises_not_found(self):
        t = await TestTerrariumBuilder().build()
        notifier = _FakeNotifier()
        try:
            adapter = TerrariumOutputWireAdapter(t, notifier)
            adapter.set_target_resolver(lambda name: None)
            msg = _app_msg(
                "inject",
                {"target_name": "ghost", "source": "bravo", "content": ""},
            )
            out = await adapter._dispatch(msg)
            assert out.get("error", {}).get("kind") == "not_found"
            assert notifier.notifications == []
        finally:
            adapter.detach()
            await t.shutdown()
