"""Unit tests for :mod:`kohakuterrarium.session.sync`."""

import asyncio
import base64


from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.sync import (
    NAMESPACE,
    SessionEventTee,
    SessionMirrorWriter,
    _agent_from_key,
    _json_safe,
)

# ── helpers ───────────────────────────────────────────────────────


class TestAgentFromKey:
    def test_normal_key(self):
        assert _agent_from_key("alice:e000003") == "alice"

    def test_no_colon_returns_unknown(self):
        assert _agent_from_key("garbage") == "unknown"


class TestJsonSafe:
    def test_primitives_passthrough(self):
        assert _json_safe(None) is None
        assert _json_safe(1) == 1
        assert _json_safe(1.5) == 1.5
        assert _json_safe(True) is True
        assert _json_safe("hi") == "hi"

    def test_bytes_b64_wrapped(self):
        out = _json_safe(b"raw")
        assert out == {"__bytes_b64__": base64.b64encode(b"raw").decode("ascii")}

    def test_dict_recurses(self):
        out = _json_safe({"k": b"v"})
        assert out["k"]["__bytes_b64__"] == base64.b64encode(b"v").decode("ascii")

    def test_list_recurses(self):
        out = _json_safe([b"a", "b", 3])
        assert out[0]["__bytes_b64__"] == base64.b64encode(b"a").decode("ascii")
        assert out[1] == "b"
        assert out[2] == 3

    def test_tuple_becomes_list(self):
        out = _json_safe(("x", 1))
        assert out == ["x", 1]

    def test_unknown_type_repr(self):
        class _Junk:
            def __repr__(self):
                return "_Junk()"

        out = _json_safe(_Junk())
        assert out == "_Junk()"


# ── SessionMirrorWriter ───────────────────────────────────────────


class _FakeNode:
    """Stands in for a Lab host or client node."""

    def __init__(self):
        self.registered: dict[str, callable] = {}
        self.unregistered: list[str] = []
        self.client_id = "_host"

    def register_app_extension(self, namespace, handler):
        self.registered[namespace] = handler

    def unregister_app_extension(self, namespace):
        self.unregistered.append(namespace)
        self.registered.pop(namespace, None)

    async def notify(self, *, to_node, namespace, type, body):
        # Recorded but no-op.
        return None


class TestSessionMirrorWriter:
    def test_init_registers_handler(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            assert NAMESPACE in node.registered
            # Mirror dir created.
            assert (tmp_path / "mirror").is_dir()
        finally:
            writer.close()

    def test_close_unregisters_and_idempotent(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        writer.close()
        assert NAMESPACE in node.unregistered

    def test_store_for_opens_and_reuses(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            s1 = writer.store_for("sess-1")
            s2 = writer.store_for("sess-1")
            # Same session id → the cached store is reused, not reopened.
            assert s1 is s2
            assert isinstance(s1, SessionStore)
            # The store is backed by a file inside the mirror dir.
            assert str(tmp_path / "mirror") in s1.path
        finally:
            writer.close()

    def test_lru_eviction(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror", max_open_stores=2)
        try:
            s1 = writer.store_for("a")
            writer.store_for("b")
            # Third session evicts the oldest (a).
            writer.store_for("c")
            assert "a" not in writer._stores
            assert "b" in writer._stores
            assert "c" in writer._stores
            # New open for a — different object.
            s1_new = writer.store_for("a")
            assert s1_new is not s1
        finally:
            writer.close()

    def test_max_open_stores_at_least_one(self, tmp_path):
        node = _FakeNode()
        # Passing 0 should be clamped to 1.
        writer = SessionMirrorWriter(node, tmp_path / "mirror", max_open_stores=0)
        try:
            assert writer._max_open_stores == 1
        finally:
            writer.close()

    async def test_dispatch_wrong_type_noop(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            msg = AppMessage(
                namespace=NAMESPACE,
                type="other",
                body={"session_id": "s", "key": "alice:e000000", "data": {}},
                sender_node="w1",
                request_id=None,
                in_reply_to=None,
            )
            out = await writer._dispatch(msg)
            assert out is None
            # No store opened.
            assert "s" not in writer._stores
        finally:
            writer.close()

    async def test_dispatch_malformed_body_noop(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            msg = AppMessage(
                namespace=NAMESPACE,
                type="event",
                body={"session_id": 42, "key": "alice:e0", "data": {}},
                sender_node="w1",
                request_id=None,
                in_reply_to=None,
            )
            out = await writer._dispatch(msg)
            assert out is None
        finally:
            writer.close()

    async def test_dispatch_appends_event(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            msg = AppMessage(
                namespace=NAMESPACE,
                type="event",
                body={
                    "session_id": "sess-1",
                    "key": "alice:e000000",
                    "data": {"type": "user_message", "content": "hi"},
                },
                sender_node="worker-1",
                request_id=None,
                in_reply_to=None,
            )
            out = await writer._dispatch(msg)
            assert out is None
            # Mirror store now contains an event.
            store = writer.store_for("sess-1")
            events = list(store.get_events("alice"))
            assert len(events) == 1
            assert events[0]["type"] == "user_message"
            assert events[0]["content"] == "hi"
        finally:
            writer.close()

    async def test_dispatch_applies_meta(self, tmp_path):
        # A ``meta`` message initialises the mirror store's meta from
        # the worker's snapshot. Without this the mirror ``.kohakutr``
        # has no config_type / config_path and a resume off it fails
        # ("Session is a None, not an agent").
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            msg = AppMessage(
                namespace=NAMESPACE,
                type="meta",
                body={
                    "session_id": "sess-1",
                    "meta": {
                        "config_type": "agent",
                        "config_path": "/cfg",
                        "agents": ["alice"],
                    },
                },
                sender_node="worker-1",
                request_id=None,
                in_reply_to=None,
            )
            out = await writer._dispatch(msg)
            assert out is None
            store = writer.store_for("sess-1")
            meta = store.load_meta()
            assert meta["config_type"] == "agent"
            assert meta["config_path"] == "/cfg"
            assert meta["agents"] == ["alice"]
        finally:
            writer.close()

    async def test_dispatch_meta_malformed_noop(self, tmp_path):
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            msg = AppMessage(
                namespace=NAMESPACE,
                type="meta",
                body={"session_id": "sess-1", "meta": "not-a-dict"},
                sender_node="worker-1",
                request_id=None,
                in_reply_to=None,
            )
            out = await writer._dispatch(msg)
            assert out is None
            assert "sess-1" not in writer._stores
        finally:
            writer.close()

    async def test_dispatch_event_with_complex_payload_shapes(self, tmp_path):
        """Guard against the user-reported "session-sync mirror: append
        failed for graph_…/<name>:e000000" error.

        Exercises the wire-payload shapes most likely to trip up
        ``store.append_event`` on the controller mirror: nested dicts,
        mixed-type lists, ``None`` values, base64-wrapped bytes (the
        ``__bytes_b64__`` shape :func:`_json_safe` produces), empty
        content.  Each dispatch must complete without raising and the
        event must land in the mirror store.
        """
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            cases = [
                {
                    "type": "user_message",
                    "content": "hi",
                    "ts": 1.0,
                    "nested": {"a": {"b": [1, "two", None]}},
                },
                {
                    "type": "tool_call",
                    "ts": 2.0,
                    "tool_name": "bash",
                    "args": {"cmd": ["ls", "-la"], "cwd": None},
                    "blob": {"__bytes_b64__": base64.b64encode(b"data").decode()},
                },
                {
                    "type": "text_chunk",
                    "ts": 3.0,
                    "content": "",
                    "tags": [],
                },
                {
                    "type": "topology_changed",
                    "ts": 4.0,
                    "old_graph_ids": [],
                    "new_graph_ids": ["g_abc"],
                    "affected": ["alpha", "bravo"],
                },
            ]
            for i, data in enumerate(cases):
                msg = AppMessage(
                    namespace=NAMESPACE,
                    type="event",
                    body={
                        "session_id": "sess-edge",
                        "key": f"alice:e{i:06d}",
                        "data": data,
                    },
                    sender_node="worker-1",
                    request_id=None,
                    in_reply_to=None,
                )
                out = await writer._dispatch(msg)
                assert out is None, f"dispatch returned non-None for case {i}: {out}"
            store = writer.store_for("sess-edge")
            events = list(store.get_events("alice"))
            # Every case stored; nothing silently dropped.
            assert len(events) == len(
                cases
            ), f"expected {len(cases)} events in mirror, found {len(events)}"
        finally:
            writer.close()

    def test_checkpoint_open_store(self, tmp_path):
        # ``checkpoint`` flushes an open mirror store so a raw byte read
        # (the resume route pushing a session to a worker) sees its
        # meta + events. No-op for an unknown session id.
        node = _FakeNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            store = writer.store_for("sess-1")
            store.init_meta(
                session_id="sess-1",
                config_type="agent",
                config_path="/cfg",
                pwd="/work",
                agents=["alice"],
            )
            # Does not raise, and the on-disk file is readable fresh.
            writer.checkpoint("sess-1")
            writer.checkpoint("never-seen")  # unknown id → clean no-op
            reopened = SessionStore(store.path)
            try:
                assert reopened.load_meta()["config_type"] == "agent"
            finally:
                reopened.close()
        finally:
            writer.close()


# ── SessionEventTee ───────────────────────────────────────────────


class _AsyncFakeNode:
    """A minimal LabNotifier — records notifies."""

    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    async def notify(self, *, to_node, namespace, type, body):
        if self._fail:
            raise RuntimeError("link dead")
        self.calls.append((to_node, namespace, type, body))


class TestSessionEventTee:
    # ``attach`` resolves the event loop via ``get_running_loop()`` (it
    # spawns the pump task) — so these tests run inside a loop, matching
    # production where ``attach`` is called from the async runtime adapter.
    async def test_attach_idempotent(self, tmp_path):
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        node = _AsyncFakeNode()
        tee = SessionEventTee("sess", store, node)
        try:
            tee.attach()
            tee.attach()  # second call no-ops.
            assert tee._attached is True
        finally:
            tee.detach()
            store.close()

    async def test_detach_idempotent(self, tmp_path):
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        node = _AsyncFakeNode()
        tee = SessionEventTee("sess", store, node)
        try:
            tee.detach()  # no-op when not attached.
            tee.attach()
            tee.detach()
            tee.detach()  # extra detach no-op.
            assert tee._attached is False
        finally:
            store.close()

    async def test_meta_sent_first(self, tmp_path):
        # ``attach`` snapshots the store's meta and enqueues it BEFORE
        # subscribing, so the controller's mirror store is initialised
        # with config_type / config_path / agents ahead of any event.
        # Without this the mirror ``.kohakutr`` has empty meta and a
        # resume off it fails ("Session is a None, not an agent").
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        store.init_meta(
            session_id="sess",
            config_type="agent",
            config_path="/cfg/path",
            pwd="/work",
            agents=["alice"],
        )
        node = _AsyncFakeNode()
        tee = SessionEventTee("sess", store, node)
        try:
            tee.attach()
            for _ in range(20):
                await asyncio.sleep(0.01)
                if node.calls:
                    break
            assert len(node.calls) >= 1
            to_node, namespace, type_, body = node.calls[0]
            assert namespace == NAMESPACE
            assert type_ == "meta", "meta snapshot must be the first wire message"
            assert body["session_id"] == "sess"
            assert body["meta"]["config_type"] == "agent"
            assert body["meta"]["config_path"] == "/cfg/path"
            assert body["meta"]["agents"] == ["alice"]
        finally:
            tee.detach()
            store.close()

    async def test_event_forwarded(self, tmp_path):
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        node = _AsyncFakeNode()
        tee = SessionEventTee("sess", store, node)
        try:
            tee.attach()
            store.append_event("alice", "user_message", {"content": "hi"})
            # Pump runs on the loop — give it a tick. The meta snapshot
            # is wire message #0, the event follows it.
            for _ in range(40):
                await asyncio.sleep(0.01)
                if len(node.calls) >= 2:
                    break
            assert len(node.calls) >= 2
            assert node.calls[0][2] == "meta", "meta must precede events"
            to_node, namespace, type_, body = node.calls[1]
            assert namespace == NAMESPACE
            assert type_ == "event"
            assert body["session_id"] == "sess"
            assert body["key"].startswith("alice:e")
        finally:
            tee.detach()
            store.close()


# ── NAMESPACE constant ────────────────────────────────────────────


class TestModuleConstants:
    def test_namespace(self):
        assert NAMESPACE == "terrarium.session.sync"
