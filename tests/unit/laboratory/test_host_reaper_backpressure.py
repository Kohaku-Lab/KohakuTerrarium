"""Coverage tests for the heartbeat reaper + backpressure paths in
:class:`HostEngine`.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.app import (
    build_app_envelope,
)
from kohakuterrarium.laboratory._internal.client import ClientConnector
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


async def _start_pair(port=1, heartbeat_timeout=0.2):
    cfg_h = HostConfig(
        bind_host="rb",
        bind_port=port,
        token="t",
        heartbeat_timeout_seconds=heartbeat_timeout,
    )
    host = HostEngine(cfg_h, InProcTransport())
    await host.start()
    cfg_c = ClientConfig(
        client_name="w1",
        host_url=f"rb:{port}",
        token="t",
        reconnect_initial_delay_seconds=0.05,
        heartbeat_interval_seconds=10.0,  # client won't actually heartbeat
    )
    client = ClientConnector(cfg_c, InProcTransport())
    await client.start()
    return host, client


# ── Reaper: missing heartbeat triggers disconnect ────────────


class TestReaper:
    async def test_missing_heartbeat_disconnects(self):
        host, client = await _start_pair(port=1, heartbeat_timeout=0.3)
        try:
            # Reaper polls every ``heartbeat_timeout / 2``; after the
            # timeout window expires the next sweep drops the client.
            # Poll for disconnect rather than a fixed sleep — fast
            # locally, robust on slow Windows CI runners where event-loop
            # scheduling sometimes pushes the reaper's first sweep past
            # an arbitrary 0.8s budget.
            assert "w1" in host.alive_clients()
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                if "w1" not in host.alive_clients():
                    break
                await asyncio.sleep(0.1)
            assert "w1" not in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── Backpressure: full send_buffer logs overflow ─────────────


class TestBackpressure:
    async def test_buffer_overflow_logged(self):
        host, client = await _start_pair(port=2, heartbeat_timeout=10.0)
        try:
            connected = list(host._clients.values())
            assert connected
            cc = connected[0]
            # Fill the buffer to capacity, then attempt one more — the
            # ``put(wait=False)`` inside ``_enqueue`` raises
            # ``BackpressureError`` which the host logs + swallows.
            # We exercise this by replacing the buffer with one of size 1.
            from kohakuterrarium.laboratory._internal.backpressure import (
                BoundedSendBuffer,
            )

            cc.send_buffer = BoundedSendBuffer(maxsize=1)
            # Fill it.
            env = Envelope(
                from_node="_host",
                to_node="w1",
                kind=EnvelopeKind.HEARTBEAT,
                stream_id=0,
                seq=0,
            )
            await cc.send_buffer.put(env, wait=False)
            assert cc.send_buffer.overflow_count == 0
            # Now request a host-initiated notify; the next envelope
            # hits backpressure inside ``_enqueue`` and is dropped.
            try:
                await host.notify(
                    to_node="w1",
                    namespace="ns",
                    type="x",
                    body={"k": "v"},
                )
            except Exception:
                pass
            # The dropped envelope incremented the overflow counter exactly
            # once — the buffer stays at capacity, nothing else queued.
            assert cc.send_buffer.overflow_count == 1
            assert cc.send_buffer.qsize() == 1
        finally:
            await client.stop()
            await host.stop()


# ── _handle_app: malformed APP envelope ─────────────────────


class TestMalformedAppEnvelope:
    async def test_garbage_app_logged_and_dropped(self):
        host, client = await _start_pair(port=3, heartbeat_timeout=10.0)
        try:
            # Build a malformed APP envelope (empty payload).
            env = Envelope(
                from_node="w1",
                to_node="_host",
                kind=EnvelopeKind.APP,
                stream_id=0,
                seq=0,
                payload=b"",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            # Host still up.
            assert "w1" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── _handle_app: handler raises, request times out ──────────


class TestHandlerException:
    async def test_app_handler_exception_swallowed(self):
        host, client = await _start_pair(port=4, heartbeat_timeout=10.0)
        try:

            async def bad_handler(msg):
                raise RuntimeError("boom")

            host.register_app_extension("api", bad_handler)
            # Client sends a request to the host's API.
            env = build_app_envelope(
                from_node="w1",
                to_node="_host",
                namespace="api",
                type="x",
                body={"k": "v"},
                request_id="r1",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            # Host didn't crash.
            assert "w1" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()

    async def test_app_handler_returns_none_no_response(self):
        host, client = await _start_pair(port=5, heartbeat_timeout=10.0)
        try:
            seen = asyncio.Event()

            async def fire_and_forget(msg):
                seen.set()
                return None  # No response.

            host.register_app_extension("ns", fire_and_forget)
            env = build_app_envelope(
                from_node="w1",
                to_node="_host",
                namespace="ns",
                type="x",
                body={},
                request_id="r1",
            )
            await client.send(env)
            await asyncio.wait_for(seen.wait(), timeout=2.0)
        finally:
            await client.stop()
            await host.stop()
