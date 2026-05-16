"""Unit tests for :mod:`kohakuterrarium.laboratory.streams`."""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.streams import (
    RemoteStream,
    RemoteStreamError,
    StreamDemux,
)

# ── fakes ─────────────────────────────────────────────────────────


class _FakeNode:
    """LabRegistrar + LabSender stand-in for stream tests."""

    def __init__(self, *, start_response=None, raise_on_request=None):
        self.registered: dict[str, callable] = {}
        self.unregistered: list[str] = []
        self.requests: list[tuple] = []
        self.start_response = start_response or {}
        self.raise_on_request = raise_on_request

    def register_app_extension(self, namespace, handler):
        self.registered[namespace] = handler

    def unregister_app_extension(self, namespace):
        self.unregistered.append(namespace)
        self.registered.pop(namespace, None)

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.requests.append((to_node, namespace, type, body))
        if self.raise_on_request is not None:
            raise self.raise_on_request
        if type == "cancel_stream":
            return {"cancelled": True}
        return dict(self.start_response)


def _make_msg(stream_id="abc", body=None) -> AppMessage:
    body = body or {}
    body.setdefault("stream_id", stream_id)
    return AppMessage(
        namespace=StreamDemux.NAMESPACE,
        type="frame",
        body=body,
        sender_node="w",
        request_id=None,
        in_reply_to=None,
    )


# ── RemoteStreamError ─────────────────────────────────────────────


class TestRemoteStreamError:
    def test_attrs(self):
        e = RemoteStreamError("engine", "boom")
        assert e.kind == "engine"
        assert e.message == "boom"
        assert "engine" in str(e)


# ── StreamDemux ──────────────────────────────────────────────────


class TestStreamDemux:
    def test_init_registers_handler(self):
        node = _FakeNode()
        StreamDemux(node)
        assert StreamDemux.NAMESPACE in node.registered

    def test_register_returns_queue(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        q = demux.register("abc")
        assert isinstance(q, asyncio.Queue)

    def test_register_duplicate_raises(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        demux.register("abc")
        with pytest.raises(ValueError, match="already registered"):
            demux.register("abc")

    def test_unregister(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        demux.register("abc")
        demux.unregister("abc")
        # Now we can register again.
        demux.register("abc")

    def test_unregister_unknown_silent(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        demux.unregister("never-registered")  # no raise

    def test_detach(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        demux.register("abc")
        demux.detach()
        assert demux._queues == {}
        assert StreamDemux.NAMESPACE in node.unregistered

    async def test_dispatch_routes_to_queue(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        q = demux.register("abc")
        await demux._dispatch(_make_msg("abc", {"token": "hi"}))
        frame = await q.get()
        assert frame["token"] == "hi"

    async def test_dispatch_no_stream_id_dropped(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        # Body has no stream_id → drop quietly.
        msg = AppMessage(
            namespace=StreamDemux.NAMESPACE,
            type="frame",
            body={"token": "x"},
            sender_node="w",
            request_id=None,
            in_reply_to=None,
        )
        out = await demux._dispatch(msg)
        assert out is None

    async def test_dispatch_unknown_stream_id_dropped(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        # Stream not registered → drop quietly.
        out = await demux._dispatch(_make_msg("ghost"))
        assert out is None


# ── RemoteStream.open ────────────────────────────────────────────


class TestRemoteStreamOpen:
    async def test_basic_open(self):
        node = _FakeNode(start_response={"started": True})
        demux = StreamDemux(node)
        rs = await RemoteStream.open(
            demux=demux,
            sender=node,
            target_node="w",
            start_namespace="ns",
            start_type="start",
            body={"k": "v"},
            timeout=1.0,
        )
        assert rs.stream_id
        assert rs.start_response == {"started": True}
        await rs.aclose()

    async def test_error_response_raises(self):
        node = _FakeNode(start_response={"error": {"kind": "engine", "message": "no"}})
        demux = StreamDemux(node)
        with pytest.raises(RemoteStreamError):
            await RemoteStream.open(
                demux=demux,
                sender=node,
                target_node="w",
                start_namespace="ns",
                start_type="start",
                body={},
                timeout=1.0,
            )

    async def test_request_exception_cleans_up(self):
        node = _FakeNode(raise_on_request=RuntimeError("link dead"))
        demux = StreamDemux(node)
        with pytest.raises(RuntimeError):
            await RemoteStream.open(
                demux=demux,
                sender=node,
                target_node="w",
                start_namespace="ns",
                start_type="start",
                body={},
                timeout=1.0,
            )
        # The registered queue was unregistered.
        assert demux._queues == {}


# ── RemoteStream iteration ──────────────────────────────────────


class TestRemoteStreamIteration:
    async def _make(self, node=None):
        node = node or _FakeNode()
        demux = StreamDemux(node)
        rs = await RemoteStream.open(
            demux=demux,
            sender=node,
            target_node="w",
            start_namespace="ns",
            start_type="start",
            body={},
            timeout=1.0,
        )
        return demux, rs

    async def test_iterates_until_eof(self):
        demux, rs = await self._make()
        # Push two frames + an eof.
        await demux._dispatch(_make_msg(rs.stream_id, {"token": "a"}))
        await demux._dispatch(_make_msg(rs.stream_id, {"token": "b"}))
        await demux._dispatch(_make_msg(rs.stream_id, {"eof": True}))
        out = []
        async for frame in rs:
            out.append(frame.get("token"))
        assert out == ["a", "b"]

    async def test_error_frame_raises(self):
        demux, rs = await self._make()
        await demux._dispatch(
            _make_msg(rs.stream_id, {"error": {"kind": "k", "message": "m"}})
        )
        with pytest.raises(RemoteStreamError):
            await rs.__anext__()

    async def test_closed_iterator_stops(self):
        demux, rs = await self._make()
        await rs.aclose()
        with pytest.raises(StopAsyncIteration):
            await rs.__anext__()


# ── aclose / context manager ────────────────────────────────────


class TestRemoteStreamCleanup:
    async def test_aclose_sends_cancel_request(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        rs = await RemoteStream.open(
            demux=demux,
            sender=node,
            target_node="w",
            start_namespace="ns",
            start_type="start",
            body={},
            timeout=1.0,
        )
        await rs.aclose()
        cancels = [r for r in node.requests if len(r) >= 3 and r[2] == "cancel_stream"]
        assert len(cancels) == 1

    async def test_aclose_idempotent(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        rs = await RemoteStream.open(
            demux=demux,
            sender=node,
            target_node="w",
            start_namespace="ns",
            start_type="start",
            body={},
            timeout=1.0,
        )
        await rs.aclose()
        await rs.aclose()  # second call no-op

    async def test_aclose_cancel_failure_swallowed(self):
        # Cancel request raises, but aclose doesn't re-raise.
        node = _FakeNode()
        demux = StreamDemux(node)
        rs = await RemoteStream.open(
            demux=demux,
            sender=node,
            target_node="w",
            start_namespace="ns",
            start_type="start",
            body={},
            timeout=1.0,
        )

        # Mutate the sender to start failing on subsequent requests.
        async def fail_request(**kwargs):
            raise RuntimeError("dead link")

        node.request = fail_request
        # aclose still returns cleanly.
        await rs.aclose()

    async def test_context_manager(self):
        node = _FakeNode()
        demux = StreamDemux(node)
        rs = await RemoteStream.open(
            demux=demux,
            sender=node,
            target_node="w",
            start_namespace="ns",
            start_type="start",
            body={},
            timeout=1.0,
        )
        async with rs:
            pass
        # On exit aclose ran.
        assert rs._closed
