"""Audit tests for the Laboratory transport layer.

Hypothesizes bugs from code reading and pins each one with a failing
test. Runs against :class:`InProcTransport` for speed.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.app import build_app_envelope
from kohakuterrarium.laboratory._internal.backpressure import BackpressureError
from kohakuterrarium.laboratory._internal.client import (
    ClientConnector,
    RequestAbortedError,
    RequestTimeoutError,
)
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import InProcTransport
from kohakuterrarium.laboratory.streams import RemoteStream, StreamDemux
from kohakuterrarium.laboratory.ws_proxy import WSFrameSink, WSProxyAdapter

pytestmark = pytest.mark.timeout(20)


@pytest.fixture(autouse=True)
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _start_host(
    *,
    port: int,
    token: str = "secret",
    heartbeat_timeout: float = 5.0,
    backpressure_buffer_size: int = 1000,
) -> HostEngine:
    cfg = HostConfig(
        bind_host="auditor",
        bind_port=port,
        token=token,
        heartbeat_timeout_seconds=heartbeat_timeout,
        backpressure_buffer_size=backpressure_buffer_size,
    )
    host = HostEngine(cfg, InProcTransport())
    await host.start()
    return host


async def _start_client(
    *,
    port: int,
    name: str = "worker-1",
    token: str = "secret",
    heartbeat_interval: float = 5.0,
    backpressure_buffer_size: int = 1000,
) -> ClientConnector:
    cfg = ClientConfig(
        client_name=name,
        host_url=f"auditor:{port}",
        token=token,
        reconnect_initial_delay_seconds=0.05,
        reconnect_max_delay_seconds=0.2,
        heartbeat_interval_seconds=heartbeat_interval,
        backpressure_buffer_size=backpressure_buffer_size,
    )
    client = ClientConnector(cfg, InProcTransport())
    await client.start()
    return client


# ──────────────────────────────────────────────────────────────────
# BUG #1 — Client-initiated request hangs full timeout when host
# disconnects mid-flight.  HostEngine fails its own pending requests
# on disconnect; ClientConnector does NOT, so client.request blocks
# until ``asyncio.wait_for`` fires.
# ──────────────────────────────────────────────────────────────────


class TestClientPendingRequestOnDisconnect:
    async def test_client_request_aborts_promptly_on_host_stop(self):
        host = await _start_host(port=101)
        release = asyncio.Event()

        async def slow_handler(msg):
            await release.wait()
            return {}

        host.register_app_extension("slow-ns", slow_handler)
        client = await _start_client(port=101)

        async def issue_request():
            return await client.request(
                to_node="_host",
                namespace="slow-ns",
                type="ping",
                body={},
                timeout=10.0,
            )

        try:
            req_task = asyncio.create_task(issue_request())
            # Give the request time to leave the wire.
            await asyncio.sleep(0.1)
            # Drop the host out from under it.
            await host.stop()
            # The client's pending request should resolve quickly with
            # an aborted-style error rather than burn the full 10s
            # ``timeout``.  Anything > 2s here is the bug.
            done, _ = await asyncio.wait({req_task}, timeout=2.0)
            assert req_task in done, (
                "client.request did not resolve within 2s after host "
                "stopped; future leak"
            )
            with pytest.raises(
                (RequestAbortedError, RequestTimeoutError, ConnectionError)
            ):
                await req_task
        finally:
            release.set()
            await client.stop()


# ──────────────────────────────────────────────────────────────────
# BUG #2 — RemoteStream hangs forever on producer disconnect.
# StreamDemux._dispatch never receives an end-of-stream when the
# producer's lab connection drops, so RemoteStream.__anext__ blocks
# on ``await self._queue.get()`` forever.
# ──────────────────────────────────────────────────────────────────


class _MiniDemuxNode:
    """A LabRegistrar-shaped wrapper around HostEngine for StreamDemux."""

    def __init__(self, host: HostEngine) -> None:
        self._host = host

    def register_app_extension(self, namespace, handler):
        self._host.register_app_extension(namespace, handler)

    def unregister_app_extension(self, namespace):
        return self._host.unregister_app_extension(namespace)

    def on_node_disconnect(self, callback):
        # Forward to the host so StreamDemux receives producer-gone
        # signals and can drain its queues.
        self._host.on_node_disconnect(callback)


class _MiniSender:
    def __init__(self, host: HostEngine, client_id: str) -> None:
        self._host = host
        self._client_id = client_id

    async def request(self, *, to_node=None, **kwargs):
        # Caller may supply ``to_node`` (e.g. via :meth:`RemoteStream.open`);
        # honour it but default to the wrapped client_id if omitted.
        target = to_node if to_node is not None else self._client_id
        return await self._host.request(to_node=target, **kwargs)


class TestRemoteStreamSurvivesDisconnect:
    async def test_remote_stream_terminates_when_producer_disconnects(self):
        host = await _start_host(port=102)
        client = await _start_client(port=102, name="producer")

        # The client side hosts the stream start handler; it accepts the
        # start request and then never sends any frames.
        async def start_stream(msg):
            return {"started": True}

        client.register_app_extension("test.stream", start_stream)

        demux = StreamDemux(_MiniDemuxNode(host))
        sender = _MiniSender(host, "producer")
        try:
            rs = await RemoteStream.open(
                demux=demux,
                sender=sender,
                target_node="producer",
                start_namespace="test.stream",
                start_type="start",
                cancel_namespace="test.stream",
                body={},
                timeout=2.0,
            )
            # Drop the producer.  Any consumer of the stream must
            # surface the disconnect — either raise, return EOF, or
            # otherwise terminate iteration — within a bounded delay.
            await client.stop()

            async def first_frame():
                return await rs.__anext__()

            done, _ = await asyncio.wait(
                {asyncio.create_task(first_frame())}, timeout=2.0
            )
            assert done, (
                "RemoteStream.__anext__ did not return within 2s after "
                "producer disconnected — queue leaks and iteration hangs"
            )
        finally:
            await host.stop()


# ──────────────────────────────────────────────────────────────────
# BUG #3 — Host's response message dropped silently when the
# original requesting client has already disconnected.  Routing logs
# a debug line but never resolves anything client-side.  This is the
# expected behavior FOR THE HOST.  But the *client* never registered
# any way to detect the gap.  More interesting: re-using the same
# client_name after disconnect lets the new client receive responses
# meant for the OLD session.  Test re-binding name to a fresh client
# while a pending APP request is still tracked by the (disconnected)
# host.
# ──────────────────────────────────────────────────────────────────


class TestOrphanedResponseDoesNotCorruptClient:
    async def test_orphaned_in_reply_to_envelope_is_dropped_safely(self):
        # Confirm that an envelope with in_reply_to pointing at a
        # request the client never made is dropped silently and
        # doesn't break the client's pending-request table.
        host = await _start_host(port=103)
        client = await _start_client(port=103, name="solo")
        try:
            env = build_app_envelope(
                from_node="_host",
                to_node="solo",
                namespace="audit-ns",
                type="late-response",
                body={"stale": True},
                in_reply_to="orphan-id-never-issued",
            )
            await host._route_send(env)
            await asyncio.sleep(0.1)

            async def echo(msg):
                return {"ok": True}

            host.register_app_extension("audit-echo", echo)
            resp = await client.request(
                to_node="_host",
                namespace="audit-echo",
                type="ping",
                body={},
                timeout=2.0,
            )
            assert resp == {"ok": True}
        finally:
            await client.stop()
            await host.stop()


# ──────────────────────────────────────────────────────────────────
# BUG #4 — WSProxyAdapter.detach() schedules teardown tasks but
# never awaits them.  When detach returns control, sinks may still
# be flushing, ``on_close`` may still be running, and any caller
# that relies on "detach = fully torn down" is racing.
# ──────────────────────────────────────────────────────────────────


class _FakeLabNode:
    """In-memory LabRegistrar + LabNotifier.

    Captures extension registrations and ``notify`` calls so the test
    can observe what the proxy did.
    """

    def __init__(self) -> None:
        self.extensions: dict = {}
        self.notifies: list = []

    def register_app_extension(self, namespace, handler):
        self.extensions[namespace] = handler

    def unregister_app_extension(self, namespace):
        return self.extensions.pop(namespace, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        self.notifies.append((to_node, namespace, type, body))


class _SimpleProxy(WSProxyAdapter):
    NAMESPACE = "test.proxy"

    def __init__(self, lab_node):
        super().__init__(lab_node)
        self.on_close_finished = asyncio.Event()

    async def on_start(self, body, sink):
        return None

    async def on_close(self, stream_id):
        # Simulate a teardown that takes a tick.
        await asyncio.sleep(0.05)
        self.on_close_finished.set()


class TestWSProxyAdapterDetach:
    async def test_detach_completes_teardown_before_returning(self):
        node = _FakeLabNode()
        proxy = _SimpleProxy(node)

        # Pretend a stream is active.
        sink = WSFrameSink(node, consumer="ctrl", stream_id="s1")
        sink.start()
        proxy._sinks["s1"] = sink

        await proxy.adetach()
        # After adetach() returns, the contract should be that all
        # teardown is complete: on_close has finished and the sink
        # has been closed.  The sync ``detach()`` retains a softer
        # contract (schedules teardowns) for callers outside any
        # event loop; in-loop callers must use ``adetach``.
        assert proxy.on_close_finished.is_set(), (
            "adetach() returned before on_close finished; teardown is "
            "fire-and-forget"
        )


# ──────────────────────────────────────────────────────────────────
# BUG #5 — Backpressure on host → client outbox is `wait=False` so
# excess envelopes are dropped silently with only a DEBUG log on the
# CLIENT (the host side increments overflow_count).  Test that a
# flood of broadcasts is either delivered fully or fails LOUDLY.
# In-proc transport never blocks at the wire level, so the only
# bound is the per-client send_buffer.
# ──────────────────────────────────────────────────────────────────


class TestHostBackpressureDrops:
    async def test_burst_traffic_does_not_silently_drop(self):
        # Tiny buffer to force the issue.
        host = await _start_host(port=105, backpressure_buffer_size=4)
        # Capture frames received on the client.
        received: list = []

        async def capture_handler(msg):
            received.append(msg.body)
            return None

        client = await _start_client(port=105)
        client.register_app_extension("burst", capture_handler)
        try:
            # Stall the client's read loop by NOT awaiting — actually,
            # the client read loop is already running.  We need to
            # stall it.  Instead, fill the host's outbox before the
            # write loop can drain.  Cancel the host-side write task
            # for this client to freeze drainage.
            connected = host._clients["worker-1"]
            if connected.write_task is not None:
                connected.write_task.cancel()
                try:
                    await connected.write_task
                except BaseException:
                    pass
            # Now flood notifies (host → client). Anything past the
            # 4-slot buffer must be either delivered later OR raise
            # to the caller. Silent drop is the bug.
            raised = False
            for i in range(20):
                try:
                    await host.notify(
                        to_node="worker-1",
                        namespace="burst",
                        type="frame",
                        body={"i": i},
                    )
                except BackpressureError:
                    raised = True
                    break
            assert raised, (
                "host silently dropped envelopes to a slow client; "
                "1.5.0 routing has no surface for the caller to detect "
                "this — _enqueue must surface BackpressureError"
            )
        finally:
            await client.stop()
            await host.stop()


# ──────────────────────────────────────────────────────────────────
# BUG #6 — Heartbeat-interval > heartbeat_timeout means the client
# is reaped before its first heartbeat.  Client config validation
# does NOT prevent this misconfiguration.
# ──────────────────────────────────────────────────────────────────


class TestHeartbeatTimeoutShorterThanInterval:
    async def test_client_first_heartbeat_arrives_before_timeout(self):
        # heartbeat_timeout < heartbeat_interval — client never gets
        # to send its first heartbeat before host reaps it.
        host = await _start_host(port=106, heartbeat_timeout=0.3)
        client = await _start_client(port=106, heartbeat_interval=2.0)
        try:
            # Wait one reaper cycle plus a hair.
            await asyncio.sleep(0.6)
            # If the host reaped, the client gets disconnected (then
            # auto-reconnects forever).  ``alive_clients`` should
            # still include the worker because the lab link is
            # genuinely up — no real network problem, just a
            # misconfigured heartbeat.  This guards against a UX
            # footgun: silent reconnect-loops behind a healthy WS.
            assert "worker-1" in host.alive_clients(), (
                "host reaped a client that was actually alive — "
                "heartbeat config invariant is not enforced"
            )
        finally:
            await client.stop()
            await host.stop()


# ──────────────────────────────────────────────────────────────────
# BUG #7 — host.alive_clients() uses Membership, but
# _handle_new_connection inserts into _clients BEFORE calling
# _membership.join.  A request that arrives in this tiny window
# from another client targeted at the new client_id could attempt
# routing while membership still says "not alive".  More important:
# `is_new` from membership.join is logged but unused.  Test the
# ordering invariant: alive_clients() ⊆ _clients (every alive node
# is also in _clients).  Inverse should NOT hold during disconnect
# (we permit it briefly) but should hold during connect.
# ──────────────────────────────────────────────────────────────────


class TestAliveClientsConsistency:
    async def test_alive_clients_subset_of_clients(self):
        host = await _start_host(port=107)
        try:
            # Connect 3 clients then check the invariant repeatedly
            # during churn.
            clients = []
            for i in range(3):
                c = await _start_client(port=107, name=f"churn-{i}")
                clients.append(c)
            # Disconnect / reconnect a few times.
            for c in clients:
                await c.stop()
            # Right after stop, the host's accept side should have
            # cleaned up; assert clients tracked correctly.
            for _ in range(40):
                if not host._clients:
                    break
                await asyncio.sleep(0.05)
            # Invariant: alive_clients() is a subset of _clients.
            alive = host.alive_clients()
            tracked = set(host._clients.keys())
            assert alive <= tracked, (
                f"alive_clients ({alive}) not subset of tracked clients "
                f"({tracked}) — membership/clients drift"
            )
        finally:
            await host.stop()


# ──────────────────────────────────────────────────────────────────
# BUG #8 — Auth: ``token`` field type.  HelloPayload's token field
# is declared as str. Passing a non-str slips past validate() (which
# returns False, so reject) but the *host* uses `is_disabled` —
# meaning if host.token == "" any token (including None / wrong)
# would pass.  Test the documented behavior holds: with host token
# == "" but client token == "wrong", connection is accepted.
# ──────────────────────────────────────────────────────────────────


class TestAuthEmptyTokenAcceptsAnything:
    async def test_empty_host_token_accepts_mismatched_client_token(self):
        host = await _start_host(port=108, token="")
        try:
            client = await _start_client(port=108, token="wrong-token")
            try:
                assert client.is_connected
            finally:
                await client.stop()
        finally:
            await host.stop()
