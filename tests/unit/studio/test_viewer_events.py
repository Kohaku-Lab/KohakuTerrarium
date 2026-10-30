"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.events`."""

from contextlib import closing

import pytest
from kohakuvault import KVault

import kohakuterrarium.session.vault.io as vault_io
from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.events import (
    build_events_payload,
    parse_type_filter,
)

# ── parse_type_filter ───────────────────────────────────────────


class TestParseTypeFilter:
    def test_none(self):
        assert parse_type_filter(None) is None

    def test_empty(self):
        assert parse_type_filter("") is None

    def test_single(self):
        assert parse_type_filter("tool_call") == {"tool_call"}

    def test_multiple(self):
        assert parse_type_filter("a,b,c") == {"a", "b", "c"}

    def test_strips_whitespace(self):
        assert parse_type_filter("a, b , c") == {"a", "b", "c"}

    def test_empty_segments_dropped(self):
        assert parse_type_filter("a,,b") == {"a", "b"}

    def test_all_empty_returns_none(self):
        assert parse_type_filter(",,") is None


# ── build_events_payload ───────────────────────────────────────


def _store(tmp_path) -> SessionStore:
    return SessionStore(str(tmp_path / "s.kohakutr"))


def _count_event_reads(monkeypatch, store, bad=frozenset()):
    """Record every event key read through ``get_many``; ``bad`` keys raise."""
    reads = []
    original_many, original_item = KVault.get_many, KVault.__getitem__

    def text(key):
        return key.decode() if isinstance(key, bytes) else key

    def many(table, keys, *args, **kwargs):
        if table is store.events:
            reads.extend(text(key) for key in keys)
            if bad.intersection(text(key) for key in keys):
                raise ValueError("batch holds a bad value")
        return original_many(table, keys, *args, **kwargs)

    def item(table, key):
        if table is store.events and text(key) in bad:
            raise ValueError("bad:" + text(key))
        return original_item(table, key)

    monkeypatch.setattr(KVault, "get_many", many)
    monkeypatch.setattr(KVault, "__getitem__", item)
    return reads


def _page(store, **overrides):
    options = dict(
        agent="alice",
        turn_index=None,
        types=None,
        from_ts=None,
        to_ts=None,
        limit=200,
        cursor=None,
    )
    options.update(overrides)
    return build_events_payload(store, "sess", **options)


class TestBuildEventsPayload:
    def test_value_budget_and_full_page_cursor(self, tmp_path, monkeypatch):
        with closing(_store(tmp_path)) as store:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            for index in range(401):
                store.append_event("alice", "x", {"ts": index}, turn_index=index)
            expected = store.get_events("alice")
            assert len(_page(store, limit=1)["events"]) == 1
            reads = _count_event_reads(monkeypatch, store)
            _page(store, limit=1)
            assert len(reads) == vault_io.FIRST_READ_CHUNK
            reads.clear()
            monkeypatch.setattr(vault_io, "READ_CHUNK", 1)
            first = _page(store)
            assert first["events"] == expected[:200]
            assert first["next_cursor"] == 200
            assert len(reads) == 200
            reads.clear()
            second = _page(store, cursor=200)
            assert second["events"] == expected[200:400]
            assert second["next_cursor"] == 400
            assert len(reads) == 400
            reads.clear()
            last = _page(store, cursor=400, limit=1)
            assert last["events"] == expected[400:]
            assert last["next_cursor"] == 401
            assert len(reads) == 401
            reads.clear()
            assert _page(store, cursor=401)["events"] == []
            assert len(reads) == 401
            reads.clear()
            sparse = _page(
                store, turn_index=400, types=" x, x ", from_ts=400, to_ts=400
            )
            assert sparse["events"] == expected[400:]
            assert sparse["next_cursor"] is None
            assert sparse["filters"]["types"] == ["x"]
            assert len(reads) == 401
            reads.clear()
            assert store.get_events("alice") == expected
            assert len(reads) == 401

    def test_physical_order_and_cursor_types(self, tmp_path):
        with closing(_store(tmp_path)) as store:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            rows = [
                ("alice:e10", {"event_id": "7", "type": "x", "ts": "2"}),
                ("alice:e2", {"event_id": 3, "type": "x", "ts": 2}),
                ("alice:e9", {"event_id": True, "type": "y"}),
                ("alice:ez", {"type": "x"}),
            ]
            for key, value in reversed(rows):
                store.events[key] = value
            expected = [value for _, value in rows]
            assert store.get_events("alice") == expected
            assert store.get_events("alice", since_event_id=2) == [expected[1]]
            assert _page(store)["events"] == expected
            assert _page(store, limit=1)["next_cursor"] is None
            assert _page(store, cursor=4, from_ts=2, to_ts=2)["events"] == expected[:1]
            assert _page(store, cursor=0, limit=3)["next_cursor"] is True
            assert _page(store, types="absent")["events"] == []

    def test_consumed_bad_value_logs_but_unread_tail_does_not(
        self, tmp_path, monkeypatch, caplog
    ):
        with closing(_store(tmp_path)) as store:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            for _ in range(5):
                store.append_event("alice", "x", {})
            store.flush()
            monkeypatch.setattr(vault_io, "READ_CHUNK", 1)
            reads = _count_event_reads(
                monkeypatch, store, bad=frozenset({"alice:e000000", "alice:e000003"})
            )
            page = _page(store, limit=2)
            assert [ev["event_id"] for ev in page["events"]] == [2, 3]
            assert page["next_cursor"] == 3
            assert reads == [f"alice:e{index:06d}" for index in range(3)]
            assert "Failed to read event" in caplog.text
            assert {record.name for record in caplog.records} == {
                "kohakuterrarium.session.vault.io"
            }
            assert "bad:alice:e000000" in caplog.text
            assert "bad:alice:e000003" not in caplog.text
            caplog.clear()
            assert [ev["event_id"] for ev in store.get_events("alice")] == [2, 3, 5]
            assert "bad:alice:e000003" in caplog.text

    @pytest.mark.parametrize(
        "value, filters, error",
        [
            ({"event_id": "bad-id"}, {"cursor": 0}, ValueError),
            ({"ts": "bad-ts"}, {"from_ts": 0}, ValueError),
            ("not-a-dict", {"cursor": 0}, AttributeError),
        ],
    )
    def test_decoded_filter_errors_are_not_skipped(
        self, tmp_path, value, filters, error
    ):
        with closing(_store(tmp_path)) as store:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.events["alice:e000000"] = value
            assert store.get_events("alice") == [value]
            assert store.get_events("alice", since_event_id=0) == []
            with pytest.raises(error):
                _page(store, **filters)

    def test_enumeration_failure_propagates(self, tmp_path, monkeypatch):
        with closing(_store(tmp_path)) as store:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "x", {})
            original = KVault.keys

            def failed_keys():
                yield b"alice:e000000"
                raise RuntimeError("enumeration failed")

            def keys(table, *args, **kwargs):
                if table is store.events and kwargs.get("prefix") == "alice:e":
                    return failed_keys()
                return original(table, *args, **kwargs)

            monkeypatch.setattr(KVault, "keys", keys)
            with pytest.raises(RuntimeError, match="enumeration failed"):
                _page(store, limit=1)

    def test_basic(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_message", {"content": "hi"}, turn_index=1)
            s.flush()
            out = build_events_payload(
                s,
                "sess",
                agent=None,
                turn_index=None,
                types=None,
                from_ts=None,
                to_ts=None,
                limit=10,
                cursor=None,
            )
            assert out["agent"] == "alice"
            # Exactly the one user_message event was recorded.
            assert out["count"] == 1
            assert out["events"][0]["type"] == "user_message"
        finally:
            s.close()

    def test_unknown_agent_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            with pytest.raises(NotFoundError):
                build_events_payload(
                    s,
                    "sess",
                    agent="ghost",
                    turn_index=None,
                    types=None,
                    from_ts=None,
                    to_ts=None,
                    limit=10,
                    cursor=None,
                )
        finally:
            s.close()

    def test_no_agents_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", [])
            with pytest.raises(NotFoundError):
                build_events_payload(
                    s,
                    "sess",
                    agent=None,
                    turn_index=None,
                    types=None,
                    from_ts=None,
                    to_ts=None,
                    limit=10,
                    cursor=None,
                )
        finally:
            s.close()

    def test_viewer_default_used(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice:attached:rev:0", "x", {})
            s.set_viewer_default_agent("alice:attached:rev:0")
            s.flush()
            out = build_events_payload(
                s,
                "sess",
                agent=None,
                turn_index=None,
                types=None,
                from_ts=None,
                to_ts=None,
                limit=10,
                cursor=None,
            )
            assert out["agent"] == "alice:attached:rev:0"
        finally:
            s.close()

    def test_filter_by_turn(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "x", {}, turn_index=1)
            s.append_event("alice", "x", {}, turn_index=2)
            s.flush()
            out = build_events_payload(
                s,
                "sess",
                agent="alice",
                turn_index=2,
                types=None,
                from_ts=None,
                to_ts=None,
                limit=10,
                cursor=None,
            )
            # All returned events should be turn 2.
            for ev in out["events"]:
                assert ev["turn_index"] == 2
        finally:
            s.close()

    def test_filter_by_type(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "a", {}, turn_index=1)
            s.append_event("alice", "b", {}, turn_index=1)
            s.flush()
            out = build_events_payload(
                s,
                "sess",
                agent="alice",
                turn_index=None,
                types="a",
                from_ts=None,
                to_ts=None,
                limit=10,
                cursor=None,
            )
            for ev in out["events"]:
                assert ev["type"] == "a"
        finally:
            s.close()

    def test_filter_by_ts(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "a", {"ts": 100.0}, turn_index=1)
            s.append_event("alice", "b", {"ts": 200.0}, turn_index=1)
            s.flush()
            out = build_events_payload(
                s,
                "sess",
                agent="alice",
                turn_index=None,
                types=None,
                from_ts=150.0,
                to_ts=None,
                limit=10,
                cursor=None,
            )
            for ev in out["events"]:
                assert ev["ts"] >= 150.0
        finally:
            s.close()

    def test_limit_and_cursor(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            for _ in range(5):
                s.append_event("alice", "x", {}, turn_index=1)
            s.flush()
            out = build_events_payload(
                s,
                "sess",
                agent="alice",
                turn_index=None,
                types=None,
                from_ts=None,
                to_ts=None,
                limit=2,
                cursor=None,
            )
            assert out["count"] == 2
            assert out["next_cursor"] is not None
            # Next page using cursor.
            out2 = build_events_payload(
                s,
                "sess",
                agent="alice",
                turn_index=None,
                types=None,
                from_ts=None,
                to_ts=None,
                limit=2,
                cursor=out["next_cursor"],
            )
            assert out2["count"] == 2
        finally:
            s.close()
