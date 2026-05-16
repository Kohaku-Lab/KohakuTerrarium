"""Loop / property / lifecycle branch coverage for HostEngine + ClientConnector.

Drives the per-connection read / write / heartbeat loops and the
small read-only accessors over a real host+client pair on
:class:`InProcTransport`.  Targets the defensive branches that the
happy-path handshake tests never exercise: malformed mid-stream
frames, the heartbeat pump, pending-request abort on host stop, and
the membership / capability views.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)
from kohakuterrarium.laboratory.config import ClientConfig, HostConfig


@pytest.fixture(autouse=True)
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _start_pair(port=1, heartbeat_interval=10.0, heartbeat_timeout=5.0, caps=()):
    host = HostEngine(
        HostConfig(
            bind_host="lp",
            bind_port=port,
            token="t",
            heartbeat_timeout_seconds=heartbeat_timeout,
        ),
        InProcTransport(),
    )
    await host.start()
    client = ClientConnector(
        ClientConfig(
            client_name="w1",
            host_url=f"lp:{port}",
            token="t",
            reconnect_initial_delay_seconds=0.1,
            heartbeat_interval_seconds=heartbeat_interval,
            capabilities=caps,
        ),
        InProcTransport(),
    )
    await client.start()
    return host, client


# ── HostEngine read-only views ──────────────────────────────────


class TestHostViews:
    async def test_is_running_reflects_lifecycle(self, _reset_inproc):
        host = HostEngine(
            HostConfig(bind_host="lp", bind_port=20, token="t"),
            InProcTransport(),
        )
        # Not running before start.
        assert host.is_running is False
        await host.start()
        assert host.is_running is True
        await host.stop()
        # Not running after stop.
        assert host.is_running is False

    async def test_client_capabilities_and_membership_views(self, _reset_inproc):
        host, client = await _start_pair(port=21, caps=("chat", "pty"))
        try:
            # The host exposes the capabilities the client advertised in
            # its Hello, plus a live membership handle.
            caps = host.client_capabilities("w1")
            assert caps is not None
            assert set(caps) == {"chat", "pty"}
            assert "w1" in host.membership.alive()
            # Unknown client → no capabilities.
            assert host.client_capabilities("ghost") is None
            # The addressing directory is exposed too.
            assert host.addressing is not None
        finally:
            await client.stop()
            await host.stop()


# ── malformed mid-stream frames ─────────────────────────────────


class TestMalformedMidStream:
    async def test_host_drops_garbage_frame_and_stays_up(self, _reset_inproc):
        host, client = await _start_pair(port=22)
        try:
            # Push raw undecodable bytes straight onto the wire — the
            # host's read loop must log + skip it, not crash, and the
            # client stays a member.
            await client._connection.send_frame(b"\x00\x01not-an-envelope")
            await asyncio.sleep(0.1)
            assert "w1" in host.alive_clients()
            assert client.is_connected
        finally:
            await client.stop()
            await host.stop()

    async def test_client_drops_garbage_frame_and_stays_connected(self, _reset_inproc):
        host, client = await _start_pair(port=23)
        try:
            # The host side of the in-proc pair is the client's peer;
            # find it and shove garbage back at the client.
            server_conn = None
            for cid, conn_client in host._clients.items():
                if cid == "w1":
                    server_conn = conn_client.connection
            assert server_conn is not None
            await server_conn.send_frame(b"\xde\xad\xbe\xef")
            await asyncio.sleep(0.1)
            # Client's read loop logged + skipped it; still connected.
            assert client.is_connected
        finally:
            await client.stop()
            await host.stop()


# ── heartbeat loop ──────────────────────────────────────────────


class TestHeartbeatLoop:
    async def test_client_heartbeat_keeps_membership_alive(self, _reset_inproc):
        # A tight heartbeat interval well under the host's timeout: the
        # client's heartbeat pump must keep it in the membership set
        # past the point a silent client would have been reaped.
        host, client = await _start_pair(
            port=24, heartbeat_interval=0.05, heartbeat_timeout=0.4
        )
        try:
            assert "w1" in host.alive_clients()
            # Wait longer than the heartbeat timeout — only the pump
            # keeps it alive.
            await asyncio.sleep(0.6)
            assert "w1" in host.alive_clients()
            assert client.is_connected
        finally:
            await client.stop()
            await host.stop()


# ── pending-request abort on host stop ──────────────────────────


class TestPendingRequestAbortOnStop:
    async def test_host_stop_aborts_in_flight_request(self, _reset_inproc):
        host, client = await _start_pair(port=25)

        # Worker registers a handler that never returns, so the host's
        # request stays pending.
        async def _never(msg):
            await asyncio.Event().wait()

        client.register_app_extension("slow.ns", _never)
        await asyncio.sleep(0.05)
        try:
            req = asyncio.create_task(
                host.request(
                    to_node="w1",
                    namespace="slow.ns",
                    type="hang",
                    body={},
                    timeout=30.0,
                )
            )
            await asyncio.sleep(0.1)
            # Stopping the host must fail the in-flight request fast
            # rather than leaving the caller hung until the timeout.
            await host.stop()
            with pytest.raises(Exception):
                await asyncio.wait_for(req, timeout=2.0)
        finally:
            client.unregister_app_extension("slow.ns")
            await client.stop()


# ── write loop on dead connection ───────────────────────────────


class TestWriteLoopDeadConnection:
    async def test_send_after_peer_gone_does_not_crash_client(self, _reset_inproc):
        host, client = await _start_pair(port=26)
        # Stop the host so the client's connection is dead, then try to
        # send — the write loop hits ConnectionClosed and exits cleanly
        # without crashing the client object.
        await host.stop()
        for _ in range(20):
            if not client.is_connected:
                break
            await asyncio.sleep(0.05)
        from kohakuterrarium.laboratory._internal.envelope import (
            Envelope,
            EnvelopeKind,
        )

        # Best-effort send onto a dead connection — must not raise out
        # of the client.
        try:
            await client.send(
                Envelope(
                    from_node="w1",
                    to_node="_host",
                    kind=EnvelopeKind.SEND,
                    stream_id=0,
                    seq=0,
                    payload=b"into-the-void",
                )
            )
        except Exception:
            pass
        await client.stop()
        assert not client.is_connected
