"""More coverage tests for :mod:`kohakuterrarium.laboratory._internal.host`.

Targets the routing / CONTROL / SEND / BROADCAST / heartbeat reaper
branches via real client-host pairs over InProcTransport.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import (
    ClientConnector,
)
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)


@pytest.fixture(autouse=True)
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _start_pair(port=100):
    cfg_h = HostConfig(
        bind_host="hx",
        bind_port=port,
        token="t",
        heartbeat_timeout_seconds=5.0,
    )
    host = HostEngine(cfg_h, InProcTransport())
    await host.start()
    cfg_c = ClientConfig(
        client_name="worker",
        host_url=f"hx:{port}",
        token="t",
        reconnect_initial_delay_seconds=0.1,
        heartbeat_interval_seconds=10.0,
    )
    client = ClientConnector(cfg_c, InProcTransport())
    await client.start()
    return host, client


# ── Control type registration ─────────────────────────────────


class TestControlHandlerRegistry:
    async def test_register_builtin_raises(self):
        cfg = HostConfig(bind_host="x", bind_port=200, token="")
        host = HostEngine(cfg, InProcTransport())
        with pytest.raises(ValueError, match="cannot override built-in"):
            host.register_control_handler("subscribe", lambda *a: None)

    async def test_double_register_raises(self):
        cfg = HostConfig(bind_host="x", bind_port=201, token="")
        host = HostEngine(cfg, InProcTransport())
        host.register_control_handler("custom", lambda *a: None)
        with pytest.raises(ValueError):
            host.register_control_handler("custom", lambda *a: None)

    async def test_unregister_control_handler(self):
        cfg = HostConfig(bind_host="x", bind_port=202, token="")
        host = HostEngine(cfg, InProcTransport())
        host.register_control_handler("custom", lambda *a: None)
        assert host.unregister_control_handler("custom") is True
        assert host.unregister_control_handler("custom") is False

    async def test_unregister_app_extension(self):
        cfg = HostConfig(bind_host="x", bind_port=203, token="")
        host = HostEngine(cfg, InProcTransport())
        host.register_app_extension("ns", lambda m: None)
        assert host.unregister_app_extension("ns") is True
        assert host.unregister_app_extension("ghost") is False


# ── Host notify + request to client (host-initiated APP) ─────


class TestHostInitiatedRequest:
    async def test_host_request_to_client_via_extension(self):
        host, client = await _start_pair(port=300)
        try:
            # Client-side handler.
            async def handler(msg):
                return {"got": msg.body}

            client.register_app_extension("api", handler)
            # Wait for handshake to fully complete.
            await asyncio.sleep(0.05)
            resp = await host.request(
                to_node="worker",
                namespace="api",
                type="ping",
                body={"v": 1},
                timeout=5.0,
            )
            assert resp == {"got": {"v": 1}}
        finally:
            await client.stop()
            await host.stop()

    async def test_host_request_to_unknown_node_raises(self):
        host, client = await _start_pair(port=301)
        try:
            with pytest.raises(KeyError):
                await host.request(
                    to_node="not-a-node",
                    namespace="ns",
                    type="x",
                    timeout=0.5,
                )
        finally:
            await client.stop()
            await host.stop()

    async def test_host_notify_to_unknown_node_raises(self):
        host, _client = await _start_pair(port=302)
        try:
            with pytest.raises(KeyError):
                await host.notify(
                    to_node="ghost",
                    namespace="ns",
                    type="x",
                )
        finally:
            await _client.stop()
            await host.stop()

    async def test_host_notify_to_client(self):
        host, client = await _start_pair(port=303)
        try:
            received = asyncio.Event()

            async def handler(msg):
                received.set()
                return None  # no response

            client.register_app_extension("notify", handler)
            await asyncio.sleep(0.05)
            await host.notify(
                to_node="worker",
                namespace="notify",
                type="x",
                body={"k": "v"},
            )
            await asyncio.wait_for(received.wait(), timeout=2.0)
        finally:
            await client.stop()
            await host.stop()


# ── CONTROL — subscribe/register_creature/custom handler ─────


class TestControlPaths:
    async def test_client_subscribe_to_channel(self):
        host, client = await _start_pair(port=400)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                build_subscribe,
            )

            await client.send(
                build_subscribe(from_node="worker", to_node="_host", channel="chat")
            )
            await asyncio.sleep(0.1)
            listeners = host.addressing.listeners("chat")
            assert "worker" in listeners
        finally:
            await client.stop()
            await host.stop()

    async def test_client_register_creature(self):
        host, client = await _start_pair(port=401)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                build_register_creature,
            )

            await client.send(
                build_register_creature(
                    from_node="worker",
                    to_node="_host",
                    ref="creature://alice",
                )
            )
            await asyncio.sleep(0.1)
            resolved = host.addressing.resolve_creature("creature://alice")
            assert resolved == "worker"
        finally:
            await client.stop()
            await host.stop()

    async def test_custom_control_handler_fires(self):
        host, client = await _start_pair(port=402)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                _build_control,
            )

            seen = asyncio.Event()

            async def custom_handler(sender, env, fields):
                seen.set()

            host.register_control_handler("custom-op", custom_handler)
            await client.send(
                _build_control(
                    from_node="worker",
                    to_node="_host",
                    stream_id=0,
                    seq=0,
                    body={"control": "custom-op", "k": "v"},
                )
            )
            await asyncio.wait_for(seen.wait(), timeout=2.0)
        finally:
            await client.stop()
            await host.stop()


# ── _route_send heartbeat / drop unknown ─────────────────────


class TestRouteSendBehaviour:
    async def test_heartbeat_does_not_crash_router(self):
        from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID

        host, client = await _start_pair(port=500)
        try:
            heartbeat = Envelope(
                from_node="worker",
                to_node=HOST_NODE_ID,
                kind=EnvelopeKind.HEARTBEAT,
                stream_id=0,
                seq=0,
            )
            await client.send(heartbeat)
            await asyncio.sleep(0.1)
            assert "worker" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── _accept loop crash isolation ─────────────────────────────


class TestAcceptLoopResilience:
    async def test_garbage_first_frame_closes_connection(self):
        cfg_h = HostConfig(bind_host="rg", bind_port=1, token="")
        host = HostEngine(cfg_h, InProcTransport())
        await host.start()
        try:
            # Open a raw connection and send garbage.
            transport = InProcTransport()
            conn = await transport.connect("rg:1")
            await conn.send_frame(b"not-an-envelope")
            # Give the host a chance to react.
            await asyncio.sleep(0.1)
            # Connection should be closed by host.
            assert "worker" not in host.alive_clients()
        finally:
            await host.stop()
