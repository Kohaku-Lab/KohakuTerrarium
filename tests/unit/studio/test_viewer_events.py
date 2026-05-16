"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.events`."""

import pytest
from fastapi import HTTPException

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


class TestBuildEventsPayload:
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
            with pytest.raises(HTTPException) as exc:
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
            assert exc.value.status_code == 404
        finally:
            s.close()

    def test_no_agents_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", [])
            with pytest.raises(HTTPException) as exc:
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
            assert exc.value.status_code == 404
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
