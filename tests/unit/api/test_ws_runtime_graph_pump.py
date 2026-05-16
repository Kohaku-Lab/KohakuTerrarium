"""Drive the runtime_graph WS endpoint's engine_events pump.

The endpoint's hard-to-reach lines are inside ``engine_events`` (the
``async for event in engine.subscribe()`` pump) and the channel
observer sync.  TestClient runs the endpoint in a separate portal
thread with its own event loop, so a cross-thread ``_emit`` never
reaches the pump.  Instead we call the endpoint *coroutine directly*
with a fake WebSocket in the test's own loop — engine + pump share
the loop, so an emitted event flows straight through.
"""

import asyncio


from kohakuterrarium.api.ws import runtime_graph as ws_rg
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeWS:
    """In-loop fake WebSocket — every send is recorded synchronously."""

    def __init__(self):
        self.sent = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=None):
        self.closed = True


class TestRuntimeGraphPump:
    async def test_engine_event_flows_through_pump(self, monkeypatch):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        ws = _FakeWS()
        try:
            # Run the endpoint as a task so we can feed it an event
            # then cancel it.
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            # Give it a beat to send subscribed + snapshot + spawn pump.
            await asyncio.sleep(0.1)
            # Emit a real engine event — same loop, so the pump's
            # ``async for event in engine.subscribe()`` yields it.
            t._emit(
                EngineEvent(
                    kind=EventKind.CREATURE_STARTED,
                    creature_id="alice",
                    graph_id=t.get_creature("alice").graph_id,
                )
            )
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            kinds = [m.get("type") for m in ws.sent]
            # Handshake frames first.
            assert kinds[0] == "subscribed"
            assert kinds[1] == "snapshot"
            # The emitted CREATURE_STARTED engine event flowed through
            # the pump and out to the socket as its own frame, carrying
            # the creature + graph ids.
            started = [m for m in ws.sent if m.get("type") == "creature_started"]
            assert len(started) == 1
            assert started[0]["creature_id"] == "alice"
            assert started[0]["graph_id"] == t.get_creature("alice").graph_id
        finally:
            await t.shutdown()

    async def test_channel_message_flows_through_observer(self, monkeypatch):
        # Regression test for B-api-1 (fixed): the engine_events() pump
        # now skips CHANNEL_MESSAGE events, so a single channel send
        # produces exactly one channel_message frame — the richer flat
        # shape from the endpoint's _make_channel_callback hook.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        ws = _FakeWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            # Send a channel message — the client must observe it
            # exactly once.
            gid = t.get_creature("alice").graph_id
            env = t._environments[gid]
            chan = env.shared_channels.get("chat")
            await chan.send(ChannelMessage(sender="alice", content="hi"))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert ws.accepted
            kinds = [m.get("type") for m in ws.sent]
            assert kinds[0] == "subscribed"
            assert kinds[1] == "snapshot"
            # CORRECT behavior: exactly one channel_message frame per
            # send, carrying the sender / content / channel / id.
            msgs = [m for m in ws.sent if m.get("type") == "channel_message"]
            assert len(msgs) == 1
            assert msgs[0]["sender"] == "alice"
            assert msgs[0]["channel"] == "chat"
            assert msgs[0]["content"] == "hi"
            assert msgs[0]["message_id"]
        finally:
            await t.shutdown()

    async def test_endpoint_exception_path(self, monkeypatch):
        # The service-routed snapshot raising → the except branch fires.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)

        async def _boom():
            raise RuntimeError("snapshot crashed")

        monkeypatch.setattr(svc, "runtime_graph_snapshot", _boom)
        ws = _FakeWS()
        try:
            await ws_rg.runtime_graph_stream(ws)
            # Error frame was sent + close called.
            assert any(m.get("type") == "error" for m in ws.sent)
            assert ws.closed
        finally:
            await t.shutdown()

    async def test_websocket_disconnect_in_consume_loop_swallowed(self, monkeypatch):
        # A WebSocketDisconnect raised by send_json while draining the
        # event queue must be swallowed — the endpoint returns cleanly
        # (no error frame, no re-raise), just like a normal close.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)

        class _DisconnectingWS(_FakeWS):
            async def send_json(self, data):
                self.sent.append(data)
                # After subscribed + snapshot, the next frame (a real
                # engine event) triggers the client disconnect.
                if len(self.sent) >= 3:
                    raise WebSocketDisconnect()

        from fastapi import WebSocketDisconnect

        ws = _DisconnectingWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            t._emit(
                EngineEvent(
                    kind=EventKind.CREATURE_STARTED,
                    creature_id="alice",
                    graph_id=t.get_creature("alice").graph_id,
                )
            )
            # The endpoint should finish on its own (disconnect swallowed).
            await asyncio.wait_for(task, timeout=2.0)
            # No error frame — disconnect is a clean exit, not an error.
            assert not any(m.get("type") == "error" for m in ws.sent)
        finally:
            await t.shutdown()

    async def test_queue_full_events_are_dropped(self, monkeypatch):
        # When the WS client stalls (send_json blocks), the bounded
        # event queue fills. Further engine events must be DROPPED, not
        # crash the pump — the endpoint stays alive and the
        # already-delivered handshake frames are intact. Queue shrunk to
        # depth 1 so the overflow is deterministic.
        real_queue = asyncio.Queue
        monkeypatch.setattr(
            ws_rg.asyncio, "Queue", lambda maxsize=0: real_queue(maxsize=1)
        )
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        gid = t.get_creature("alice").graph_id

        release = asyncio.Event()

        class _StallingWS(_FakeWS):
            async def send_json(self, data):
                self.sent.append(data)
                # Block on the 3rd send (first real event) so the
                # consumer stops draining; the engine_events task is
                # already running and backs the depth-1 queue up.
                if len(self.sent) >= 3:
                    await release.wait()

        ws = _StallingWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            # Emit well past the 1-deep queue ceiling.
            for _ in range(20):
                t._emit(
                    EngineEvent(
                        kind=EventKind.CREATURE_STARTED,
                        creature_id="alice",
                        graph_id=gid,
                    )
                )
            await asyncio.sleep(0.2)
            # The pump survived the overflow — the task is still running.
            assert not task.done()
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            # Handshake frames went out before the stall.
            assert ws.sent[0]["type"] == "subscribed"
            assert ws.sent[1]["type"] == "snapshot"
        finally:
            release.set()
            await t.shutdown()

    async def test_phantom_channel_name_skipped(self, monkeypatch):
        # sync_channel_observers lists channel names then resolves each
        # via registry.get(); a name that lists but resolves to None
        # (a removal race) must be skipped, not crash the observer sync.
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        gid = t.get_creature("alice").graph_id
        registry = t._environments[gid].shared_channels
        real_list = registry.list_channels

        # Inject a phantom name that has no backing channel object.
        def _list_with_phantom():
            return list(real_list()) + ["phantom-never-created"]

        monkeypatch.setattr(registry, "list_channels", _list_with_phantom)
        ws = _FakeWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            # The phantom didn't crash the sync — handshake completed.
            assert ws.sent[0]["type"] == "subscribed"
            assert ws.sent[1]["type"] == "snapshot"
        finally:
            await t.shutdown()

    async def test_queue_full_drops_channel_message(self, monkeypatch):
        # The channel-callback path (enqueue_threadsafe) also drops on a
        # full queue rather than raising back into channel.send(). We
        # shrink the queue to maxsize 1 so it fills deterministically.
        from kohakuterrarium.core.channel import ChannelMessage

        real_queue = asyncio.Queue

        def _tiny_queue(maxsize=0):
            return real_queue(maxsize=1)

        monkeypatch.setattr(ws_rg.asyncio, "Queue", _tiny_queue)

        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        gid = t.get_creature("alice").graph_id

        release = asyncio.Event()

        class _StallingWS(_FakeWS):
            async def send_json(self, data):
                self.sent.append(data)
                # Stall after the handshake so the queue can't drain.
                if len(self.sent) >= 2:
                    await release.wait()

        ws = _StallingWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            chan = t._environments[gid].shared_channels.get("chat")
            # Fill the depth-1 queue, then send again — the second send's
            # enqueue_threadsafe put() hits QueueFull and drops cleanly.
            await chan.send(ChannelMessage(sender="alice", content="first"))
            for _ in range(5):
                await asyncio.sleep(0)
            await chan.send(ChannelMessage(sender="alice", content="dropped"))
            for _ in range(5):
                await asyncio.sleep(0)
            # The pump survived the drop — channel.send never raised.
            assert not task.done()
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            release.set()
            await t.shutdown()

    async def test_channel_message_forwarded_in_lab_host_mode(self, monkeypatch):
        """CF-8: in lab-host mode ``host_engine_or_none`` returns
        ``None`` so ``sync_channel_observers`` registers no per-channel
        callback. The endpoint MUST instead forward the service-routed
        engine-event copy of CHANNEL_MESSAGE events (which is normally
        filtered out to avoid a double-deliver in standalone). Without
        this branch, lab-host cluster channel messages never reach the
        graph editor.
        """
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        # Force the lab-host code path: pretend there is no host engine
        # so the endpoint's ``engine`` local is None and the dedupe
        # filter must invert (the service-routed engine event becomes
        # the ONLY source of the channel_message frame).
        monkeypatch.setattr(ws_rg, "host_engine_or_none", lambda s: None)
        ws = _FakeWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            gid = t.get_creature("alice").graph_id
            env = t._environments[gid]
            chan = env.shared_channels.get("chat")
            await chan.send(ChannelMessage(sender="alice", content="hi-lab-host"))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            msgs = [m for m in ws.sent if m.get("type") == "channel_message"]
            # Exactly one delivery — the service-routed copy. Without
            # CF-8's fix the endpoint dropped this frame entirely.
            assert len(msgs) == 1, ws.sent
            assert msgs[0]["channel"] == "chat"
            assert msgs[0]["sender"] == "alice"
            assert msgs[0]["content"] == "hi-lab-host"
        finally:
            await t.shutdown()

    async def test_graph_without_environment_skipped(self, monkeypatch):
        # sync_channel_observers must tolerate a graph whose environment
        # entry is missing (no shared-channel registry) — it skips that
        # graph instead of crashing, and the snapshot still goes out.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
        # Drop every environment so env is None for every listed graph.
        t._environments.clear()
        ws = _FakeWS()
        try:
            task = asyncio.create_task(ws_rg.runtime_graph_stream(ws))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            kinds = [m.get("type") for m in ws.sent]
            # Handshake still completes despite the missing environment.
            assert kinds[0] == "subscribed"
            assert kinds[1] == "snapshot"
        finally:
            await t.shutdown()
