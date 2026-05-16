"""Unit tests for :mod:`kohakuterrarium.laboratory.ws_proxy`.

Covers the three pieces of the unified WS forwarder:

- :class:`WSFrameSink` — the bidirectional bridge a worker-side
  producer / consumer uses as if it were a WebSocket.
- :class:`WSProxyAdapter` — the worker-side APP-extension base class
  driving ``start`` / ``input`` / ``cancel`` and per-stream sink
  lifecycle.
- :func:`proxy_ws_to_lab` — the controller-side helper, exercised
  end-to-end over :class:`InProcTransport` with a real host + client.

Behaviour is asserted on observable outcomes: which frames land on the
consumer node, which frames reach the controller WebSocket, what the
``start`` response carries, and whether teardown actually stops the
pump and runs the subclass hook.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)
from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory.streams import StreamDemux
from kohakuterrarium.laboratory.ws_proxy import (
    WSFrameSink,
    WSProxyAdapter,
    proxy_ws_to_lab,
)

# ── fakes ────────────────────────────────────────────────────────


class _RecordingNotifier:
    """LabNotifier that records every ``notify`` call body."""

    def __init__(self):
        self.frames: list[dict] = []
        self.fail = False

    async def notify(self, *, to_node, namespace, type, body):
        if self.fail:
            raise RuntimeError("transport blip")
        self.frames.append(
            {"to": to_node, "namespace": namespace, "type": type, "body": body}
        )


class _FakeRegistrar:
    """LabRegistrar that captures the registered handler."""

    def __init__(self, sender_node="ctrl"):
        self.sender_node = sender_node
        self.handlers: dict[str, object] = {}
        self.unregistered: list[str] = []
        self.notifier = _RecordingNotifier()

    def register_app_extension(self, namespace, handler):
        self.handlers[namespace] = handler

    def unregister_app_extension(self, namespace):
        self.unregistered.append(namespace)
        return self.handlers.pop(namespace, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        await self.notifier.notify(
            to_node=to_node, namespace=namespace, type=type, body=body
        )


def _app_msg(type_, body, sender="ctrl"):
    return AppMessage(
        namespace="x.proxy",
        type=type_,
        body=body,
        sender_node=sender,
        request_id=None,
        in_reply_to=None,
    )


# ── WSFrameSink ──────────────────────────────────────────────────


class TestWSFrameSink:
    async def test_send_json_pumps_frame_to_consumer(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s1")
        sink.start()
        try:
            await sink.send_json({"type": "token", "data": "hi"})
            # The pump delivers the frame to the consumer node, stamped
            # with the stream id and on the stream demux namespace.
            for _ in range(50):
                if notifier.frames:
                    break
                await asyncio.sleep(0.01)
            assert len(notifier.frames) == 1
            frame = notifier.frames[0]
            assert frame["to"] == "ctrl"
            assert frame["namespace"] == StreamDemux.NAMESPACE
            assert frame["type"] == "frame"
            assert frame["body"]["stream_id"] == "s1"
            assert frame["body"]["data"] == "hi"
        finally:
            await sink.close()

    async def test_close_flushes_eof_then_stops_pump(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s2")
        sink.start()
        await sink.close()
        # close() emits a sentinel eof frame the controller iterator
        # uses to terminate cleanly.
        for _ in range(50):
            if notifier.frames:
                break
            await asyncio.sleep(0.01)
        assert any(f["body"].get("eof") is True for f in notifier.frames)

    async def test_close_is_idempotent(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s3")
        sink.start()
        await sink.close()
        # Second close must be a no-op — no second eof, no crash.
        await sink.close()
        eofs = [f for f in notifier.frames if f["body"].get("eof")]
        assert len(eofs) == 1

    async def test_send_json_after_close_is_dropped(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s4")
        sink.start()
        await sink.close()
        before = len(notifier.frames)
        # A producer that races the close must not stuff more frames.
        await sink.send_json({"type": "late"})
        await asyncio.sleep(0.02)
        assert len(notifier.frames) == before

    async def test_send_json_nowait_delivers_when_space(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s5")
        sink.start()
        try:
            sink.send_json_nowait({"type": "evt", "n": 1})
            for _ in range(50):
                if notifier.frames:
                    break
                await asyncio.sleep(0.01)
            assert notifier.frames[0]["body"]["n"] == 1
        finally:
            await sink.close()

    async def test_send_json_nowait_drops_when_outbox_full(self):
        notifier = _RecordingNotifier()
        # Tiny outbox, pump NOT started — so nothing drains.
        sink = WSFrameSink(notifier, "ctrl", "s6", outbox_cap=1)
        sink.send_json_nowait({"type": "a"})
        # Second frame has nowhere to go; it is dropped, not raised.
        sink.send_json_nowait({"type": "b"})
        # Drain the outbox manually: only the first frame is present.
        assert sink._outbox.qsize() == 1

    async def test_send_json_nowait_after_close_is_dropped(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s7")
        sink.start()
        await sink.close()
        # No exception, frame ignored.
        sink.send_json_nowait({"type": "late"})

    async def test_inject_input_then_receive_json_round_trip(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s8")
        # inject_input is the adapter's hook for inbound RPC frames;
        # receive_json is what the worker-side consumer awaits.
        await sink.inject_input({"type": "input", "content": "ping"})
        got = await sink.receive_json()
        assert got == {"type": "input", "content": "ping"}

    async def test_start_is_idempotent(self):
        notifier = _RecordingNotifier()
        sink = WSFrameSink(notifier, "ctrl", "s9")
        sink.start()
        first = sink._pump
        sink.start()
        # A second start() must not spawn a second pump task.
        assert sink._pump is first
        await sink.close()

    async def test_drain_outbox_survives_delivery_failure(self):
        # A transient notify failure must NOT tear the pump down — the
        # next frame still gets a delivery attempt.
        notifier = _RecordingNotifier()
        notifier.fail = True
        sink = WSFrameSink(notifier, "ctrl", "s10")
        sink.start()
        try:
            await sink.send_json({"type": "lost"})
            await asyncio.sleep(0.03)
            # Failure swallowed: nothing delivered, pump still alive.
            assert notifier.frames == []
            assert sink._pump is not None and not sink._pump.done()
            # Recover the transport — the next frame goes through.
            notifier.fail = False
            await sink.send_json({"type": "ok"})
            for _ in range(50):
                if notifier.frames:
                    break
                await asyncio.sleep(0.01)
            assert notifier.frames[0]["body"]["type"] == "ok"
        finally:
            await sink.close()

    def test_stream_id_property(self):
        sink = WSFrameSink(_RecordingNotifier(), "ctrl", "the-id")
        assert sink.stream_id == "the-id"


# ── WSProxyAdapter ───────────────────────────────────────────────


class _DemoProxyAdapter(WSProxyAdapter):
    """Minimal concrete proxy: echoes inbound frames back through the
    sink and records lifecycle calls so tests can assert on them."""

    NAMESPACE = "x.proxy"

    def __init__(self, registrar, *, fail_on_start=False):
        self.fail_on_start = fail_on_start
        self.started: list[str] = []
        self.closed: list[str] = []
        super().__init__(registrar)

    async def on_start(self, body, sink):
        self.started.append(sink.stream_id)
        if self.fail_on_start:
            raise RuntimeError("producer boot failed")

        async def _echo():
            while True:
                frame = await sink.receive_json()
                await sink.send_json({"echo": frame})

        self._sessions[sink.stream_id] = asyncio.create_task(_echo())
        return {"setup": {"type": "ready"}}

    async def on_close(self, stream_id):
        self.closed.append(stream_id)
        task = self._sessions.get(stream_id)
        if task is not None and not task.done():
            task.cancel()


class TestWSProxyAdapter:
    def test_missing_namespace_raises(self):
        # The base class refuses to register without a NAMESPACE — a
        # subclass that forgot to set it is a programming error.
        class _NoNamespace(WSProxyAdapter):
            pass

        with pytest.raises(ValueError, match="must set NAMESPACE"):
            _NoNamespace(_FakeRegistrar())

    async def test_register_and_detach(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        assert "x.proxy" in reg.handlers
        adapter.detach()
        assert "x.proxy" in reg.unregistered

    async def test_start_spawns_session_and_returns_setup(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        try:
            resp = await adapter._dispatch(_app_msg("start", {"stream_id": "a1"}))
            # start() opens a sink keyed by stream_id and merges the
            # subclass's setup dict into the response.
            assert resp["started"] is True
            assert resp["stream_id"] == "a1"
            assert resp["setup"] == {"type": "ready"}
            assert "a1" in adapter._sinks
            assert adapter.started == ["a1"]
        finally:
            await adapter._dispatch(_app_msg("cancel", {"stream_id": "a1"}))
            adapter.detach()

    async def test_input_routes_frame_into_sink_and_subclass_echoes(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        try:
            await adapter._dispatch(_app_msg("start", {"stream_id": "a2"}))
            resp = await adapter._dispatch(
                _app_msg("input", {"stream_id": "a2", "frame": {"k": "v"}})
            )
            assert resp == {"accepted": True}
            # The echo consumer pumps the frame back out through the sink.
            for _ in range(50):
                if reg.notifier.frames:
                    break
                await asyncio.sleep(0.01)
            echoed = [f["body"] for f in reg.notifier.frames if "echo" in f["body"]]
            assert echoed and echoed[0]["echo"] == {"k": "v"}
        finally:
            await adapter._dispatch(_app_msg("cancel", {"stream_id": "a2"}))
            adapter.detach()

    async def test_input_on_unknown_stream_is_not_found(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        try:
            resp = await adapter._dispatch(
                _app_msg("input", {"stream_id": "ghost", "frame": {}})
            )
            assert resp["error"]["kind"] == "not_found"
        finally:
            adapter.detach()

    async def test_cancel_runs_on_close_and_drops_sink(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        try:
            await adapter._dispatch(_app_msg("start", {"stream_id": "a3"}))
            resp = await adapter._dispatch(_app_msg("cancel", {"stream_id": "a3"}))
            assert resp == {"cancelled": True, "stream_id": "a3"}
            # Teardown ran the subclass hook AND removed the sink.
            assert adapter.closed == ["a3"]
            assert "a3" not in adapter._sinks
        finally:
            adapter.detach()

    async def test_start_failure_tears_down_and_reraises(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg, fail_on_start=True)
        try:
            # on_start raised — _dispatch maps the generic exception to a
            # ``proxy``-kind error AND the partially-created sink is
            # cleaned up (no leak).
            resp = await adapter._dispatch(_app_msg("start", {"stream_id": "a4"}))
            assert resp["error"]["kind"] == "proxy"
            assert "a4" not in adapter._sinks
            assert adapter.closed == ["a4"]
        finally:
            adapter.detach()

    async def test_unknown_type_returns_structured_error(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        try:
            resp = await adapter._dispatch(_app_msg("bogus", {}))
            assert resp["error"]["kind"] == "unknown_type"
            assert "bogus" in resp["error"]["message"]
        finally:
            adapter.detach()

    async def test_dispatch_maps_keyerror_and_valueerror(self):
        reg = _FakeRegistrar()
        adapter = _DemoProxyAdapter(reg)
        try:
            # ``input`` with no stream_id key → KeyError → not_found.
            resp = await adapter._dispatch(_app_msg("input", {}))
            assert resp["error"]["kind"] == "not_found"
        finally:
            adapter.detach()

    async def test_dispatch_maps_valueerror_to_invalid(self):
        # A ValueError raised inside a handler is translated to the
        # structured ``invalid`` error kind, not surfaced raw.
        class _ValueErrAdapter(WSProxyAdapter):
            NAMESPACE = "x.proxy"

            async def on_start(self, body, sink):
                raise ValueError("bad start args")

        reg = _FakeRegistrar()
        adapter = _ValueErrAdapter(reg)
        try:
            resp = await adapter._dispatch(_app_msg("start", {"stream_id": "v1"}))
            assert resp["error"]["kind"] == "invalid"
            assert "bad start args" in resp["error"]["message"]
        finally:
            adapter.detach()

    async def test_base_on_start_is_abstract(self):
        # ``WSProxyAdapter.on_start`` is a contract stub — a subclass
        # that forgets to override it and reaches the base body gets a
        # NotImplementedError, not a silent no-op stream.
        class _UnimplAdapter(WSProxyAdapter):
            NAMESPACE = "x.proxy"

        reg = _FakeRegistrar()
        adapter = _UnimplAdapter(reg)
        try:
            sink = WSFrameSink(reg.notifier, "ctrl", "u1")
            with pytest.raises(NotImplementedError):
                await adapter.on_start({}, sink)
        finally:
            adapter.detach()

    async def test_teardown_swallows_on_close_failure(self):
        # If the subclass's on_close raises, teardown logs and keeps
        # going — the sink is still closed and bookkeeping cleared.
        class _BadCloseAdapter(WSProxyAdapter):
            NAMESPACE = "x.proxy"

            async def on_start(self, body, sink):
                return {"setup": {}}

            async def on_close(self, stream_id):
                raise RuntimeError("teardown blew up")

        reg = _FakeRegistrar()
        adapter = _BadCloseAdapter(reg)
        try:
            await adapter._dispatch(_app_msg("start", {"stream_id": "b1"}))
            resp = await adapter._dispatch(_app_msg("cancel", {"stream_id": "b1"}))
            # cancel still reports success and the sink is gone.
            assert resp == {"cancelled": True, "stream_id": "b1"}
            assert "b1" not in adapter._sinks
        finally:
            adapter.detach()


# ── proxy_ws_to_lab (controller-side, end-to-end over InProc) ─────


class _FakeWebSocket:
    """Minimal WebSocket double for the controller side.

    ``send_json`` records outbound frames; ``receive_json`` yields the
    queued inbound frames then blocks forever (simulating an idle but
    open socket) so the helper's input loop stays alive until the
    forward task ends.
    """

    def __init__(self, inbound=None):
        self.sent: list[dict] = []
        self._inbound = asyncio.Queue()
        for frame in inbound or []:
            self._inbound.put_nowait(frame)

    async def send_json(self, frame):
        self.sent.append(frame)

    async def receive_json(self):
        return await self._inbound.get()


@pytest.fixture
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _host_client_pair(port):
    host = HostEngine(
        HostConfig(bind_host="h", bind_port=port, token="t"),
        InProcTransport(),
    )
    await host.start()
    client = ClientConnector(
        ClientConfig(
            client_name="worker",
            host_url=f"h:{port}",
            token="t",
            reconnect_initial_delay_seconds=0.1,
        ),
        InProcTransport(),
    )
    await client.start()
    return host, client


class TestProxyWsToLab:
    async def test_streams_worker_frames_to_websocket(self, _reset_inproc):
        host, client = await _host_client_pair(601)
        # Worker registers the proxy adapter; host installs a demux and
        # bridges a fake WebSocket through proxy_ws_to_lab.
        adapter = _DemoProxyAdapter(client)
        demux = StreamDemux(host)
        ws = _FakeWebSocket(inbound=[{"k": "from-ws"}])
        try:
            bridge = asyncio.create_task(
                proxy_ws_to_lab(
                    websocket=ws,
                    sender=host,
                    demux=demux,
                    target_node="worker",
                    namespace="x.proxy",
                    body={},
                    timeout=5.0,
                    input_timeout=5.0,
                )
            )
            # The start response's ``setup`` dict is forwarded as the
            # first WS frame; the inbound WS frame is echoed back by the
            # worker's consumer and arrives as a streamed frame.
            for _ in range(100):
                echoed = [f for f in ws.sent if "echo" in f]
                if echoed:
                    break
                await asyncio.sleep(0.02)
            assert ws.sent[0] == {"type": "ready"}
            echoed = [f for f in ws.sent if "echo" in f]
            assert echoed and echoed[0]["echo"] == {"k": "from-ws"}
            # The streamed frame had its demux routing wrapper stripped.
            assert "stream_id" not in echoed[0]
        finally:
            bridge.cancel()
            try:
                await bridge
            except asyncio.CancelledError:
                pass
            adapter.detach()
            demux.detach()
            await client.stop()
            await host.stop()

    async def test_start_error_propagates_as_exception(self, _reset_inproc):
        host, client = await _host_client_pair(602)
        # Worker's proxy fails on_start → the worker returns an error
        # body → RemoteStream.open raises → proxy_ws_to_lab propagates.
        adapter = _DemoProxyAdapter(client, fail_on_start=True)
        demux = StreamDemux(host)
        ws = _FakeWebSocket()
        try:
            with pytest.raises(Exception):
                await proxy_ws_to_lab(
                    websocket=ws,
                    sender=host,
                    demux=demux,
                    target_node="worker",
                    namespace="x.proxy",
                    body={},
                    timeout=5.0,
                )
        finally:
            adapter.detach()
            demux.detach()
            await client.stop()
            await host.stop()
