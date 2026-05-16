"""Integration-style unit tests for :class:`ClientConnector` and
:class:`HostEngine` via :class:`InProcTransport`.

Exercises the real handshake / membership / APP request paths without
WebSocket — InProcTransport short-circuits frames through queues.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import (
    AuthFailedError,
    ClientConnector,
    RequestTimeoutError,
)
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)


@pytest.fixture
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


def _addr(host: str, port: int) -> str:
    return f"{host}:{port}"


async def _start_host(
    host="testh",
    port=1,
    token="secret",
    heartbeat_timeout=5.0,
) -> HostEngine:
    cfg = HostConfig(
        bind_host=host,
        bind_port=port,
        token=token,
        heartbeat_timeout_seconds=heartbeat_timeout,
    )
    transport = InProcTransport()
    host_engine = HostEngine(cfg, transport)
    await host_engine.start()
    return host_engine


async def _start_client(
    name="worker-1",
    host="testh",
    port=1,
    token="secret",
) -> ClientConnector:
    cfg = ClientConfig(
        client_name=name,
        host_url=_addr(host, port),
        token=token,
        reconnect_initial_delay_seconds=0.1,
    )
    transport = InProcTransport()
    client = ClientConnector(cfg, transport)
    await client.start()
    return client


# ── Basic handshake ───────────────────────────────────────────


class TestHandshake:
    async def test_client_connects_to_host(self, _reset_inproc):
        host = await _start_host(port=1)
        try:
            client = await _start_client(name="w1", port=1)
            try:
                assert client.is_connected
                assert client.client_id is not None
                # Host sees the client.
                assert "w1" in host.alive_clients()
            finally:
                await client.stop()
        finally:
            await host.stop()

    async def test_auth_failed_raises(self, _reset_inproc):
        host = await _start_host(port=2, token="correct")
        try:
            with pytest.raises(AuthFailedError):
                await _start_client(name="w-bad", port=2, token="wrong")
        finally:
            await host.stop()

    async def test_no_token_at_host_accepts_any(self, _reset_inproc):
        host = await _start_host(port=3, token="")
        try:
            client = await _start_client(name="w-any", port=3, token="")
            try:
                assert client.is_connected
            finally:
                await client.stop()
        finally:
            await host.stop()


# ── APP messaging ─────────────────────────────────────────────


class TestAppMessaging:
    async def test_host_registers_and_handles_app_extension(self, _reset_inproc):
        host = await _start_host(port=4)
        # Register a handler on the host side.
        received = []

        async def handler(msg):
            received.append(msg)
            return {"echo": msg.body}

        host.register_app_extension("demo", handler)
        client = await _start_client(name="w-app", port=4)
        try:
            resp = await client.request(
                to_node="_host",
                namespace="demo",
                type="ping",
                body={"k": "v"},
                timeout=5.0,
            )
            assert resp == {"echo": {"k": "v"}}
        finally:
            await client.stop()
            await host.stop()

    async def test_request_timeout(self, _reset_inproc):
        host = await _start_host(port=5)

        async def slow_handler(msg):
            await asyncio.sleep(2.0)
            return {}

        host.register_app_extension("slow", slow_handler)
        client = await _start_client(name="w-slow", port=5)
        try:
            with pytest.raises(RequestTimeoutError):
                await client.request(
                    to_node="_host",
                    namespace="slow",
                    type="ping",
                    timeout=0.2,
                )
        finally:
            await client.stop()
            await host.stop()

    async def test_notify_fire_and_forget(self, _reset_inproc):
        host = await _start_host(port=6)
        received = asyncio.Event()

        async def handler(msg):
            received.set()
            return {}

        host.register_app_extension("notify-ns", handler)
        client = await _start_client(name="w-n", port=6)
        try:
            await client.notify(
                to_node="_host",
                namespace="notify-ns",
                type="hi",
                body={"x": 1},
            )
            await asyncio.wait_for(received.wait(), timeout=2.0)
        finally:
            await client.stop()
            await host.stop()


# ── Extension registry ────────────────────────────────────────


class TestExtensionRegistry:
    async def test_double_register_raises(self, _reset_inproc):
        host = await _start_host(port=7)
        try:

            async def h1(msg):
                return None

            host.register_app_extension("ns", h1)
            with pytest.raises(ValueError):
                host.register_app_extension("ns", h1)
        finally:
            await host.stop()

    async def test_client_double_register_raises(self, _reset_inproc):
        host = await _start_host(port=8)
        client = await _start_client(name="w-x", port=8)
        try:

            async def h1(msg):
                return None

            client.register_app_extension("ns", h1)
            with pytest.raises(ValueError):
                client.register_app_extension("ns", h1)
        finally:
            await client.stop()
            await host.stop()

    async def test_unregister_returns_bool(self, _reset_inproc):
        host = await _start_host(port=9)
        client = await _start_client(name="w-u", port=9)
        try:

            async def h(msg):
                return None

            client.register_app_extension("ns", h)
            assert client.unregister_app_extension("ns") is True
            assert client.unregister_app_extension("ghost") is False
        finally:
            await client.stop()
            await host.stop()


# ── Membership lifecycle ──────────────────────────────────────


class TestMembership:
    async def test_client_disconnect_drops_from_membership(self, _reset_inproc):
        host = await _start_host(port=10)
        try:
            client = await _start_client(name="w-leave", port=10)
            assert "w-leave" in host.alive_clients()
            await client.stop()
            # Give a moment for the host to notice.
            for _ in range(20):
                if "w-leave" not in host.alive_clients():
                    break
                await asyncio.sleep(0.05)
            assert "w-leave" not in host.alive_clients()
        finally:
            await host.stop()

    async def test_host_stop_disconnects_clients(self, _reset_inproc):
        host = await _start_host(port=11)
        client = await _start_client(name="w-h", port=11)
        try:
            assert client.is_connected
        finally:
            await host.stop()
        # Stopping the host disconnects the client.
        # Give it a moment.
        for _ in range(20):
            if not client.is_connected:
                break
            await asyncio.sleep(0.05)
        await client.stop()
        assert not client.is_connected


# ── Host already started / stopped guards ────────────────────


class TestHostLifecycleGuards:
    async def test_double_start_raises(self, _reset_inproc):
        host = await _start_host(port=12)
        try:
            with pytest.raises(RuntimeError):
                await host.start()
        finally:
            await host.stop()

    async def test_stop_idempotent(self, _reset_inproc):
        host = await _start_host(port=13)
        await host.stop()
        # Second stop is a no-op.
        await host.stop()


class TestClientLifecycleGuards:
    async def test_double_start_raises(self, _reset_inproc):
        host = await _start_host(port=14)
        client = await _start_client(name="w-c", port=14)
        try:
            with pytest.raises(RuntimeError):
                await client.start()
        finally:
            await client.stop()
            await host.stop()

    async def test_stop_idempotent(self, _reset_inproc):
        host = await _start_host(port=15)
        client = await _start_client(name="w-c2", port=15)
        await client.stop()
        await client.stop()  # idempotent
        await host.stop()
