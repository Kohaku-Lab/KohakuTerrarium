"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.timeline`."""

import pytest
from kohakuterrarium.errors import NotFoundError

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.timeline import build_timeline_payload


def _store(tmp_path) -> SessionStore:
    return SessionStore(str(tmp_path / "s.kohakutr"))


class TestBuildTimelinePayload:
    def test_basic(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_message", {"content": "hi"}, turn_index=1)
            s.append_event(
                "alice",
                "tool_call",
                {"tool": "bash", "duration_ms": 42},
                turn_index=1,
            )
            s.append_event("alice", "tool_error", {"error": "boom"}, turn_index=1)
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            assert out["agent"] == "alice"
            assert out["count"] == 3
            assert out["truncated"] is False
            spans = out["spans"]
            assert [sp["type"] for sp in spans] == [
                "user_message",
                "tool_call",
                "tool_error",
            ]
            tool = spans[1]
            assert tool["dur"] == 42
            assert tool["label"] == "bash"
            assert tool["turn"] == 1
            assert tool["err"] is False
            assert spans[2]["err"] is True
            # Heavy fields never leak into the projection.
            for sp in spans:
                assert "content" not in sp
                assert "output" not in sp
        finally:
            s.close()

    def test_elapsed_ms_fallback(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "plugin_hook_timing", {"elapsed_ms": 7})
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            assert out["spans"][0]["dur"] == 7
        finally:
            s.close()

    def test_unknown_agent_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            with pytest.raises(NotFoundError):
                build_timeline_payload(s, "sess", agent="ghost", limit=100)
        finally:
            s.close()

    def test_no_agents_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", [])
            with pytest.raises(NotFoundError):
                build_timeline_payload(s, "sess", agent=None, limit=100)
        finally:
            s.close()

    def test_truncation_keeps_latest(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            for i in range(5):
                s.append_event("alice", "text_chunk", {"text": f"chunk {i}"})
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=2)
            assert out["truncated"] is True
            assert out["count"] == 2
            ids = [sp["eid"] for sp in out["spans"]]
            assert ids == [4, 5]
        finally:
            s.close()

    def test_spawned_in_turn_used_as_turn(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "subagent_call", {"name": "w"}, spawned_in_turn=3)
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            assert out["spans"][0]["turn"] == 3
        finally:
            s.close()

    def test_failed_tool_result_marked_as_error(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "tool_result", {"tool": "bash", "output": "ok"})
            s.append_event(
                "alice",
                "tool_result",
                {
                    "tool": "bash",
                    "error": "Command exited with code 1",
                    "exit_code": "1",
                },
            )
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            assert [sp["err"] for sp in out["spans"]] == [False, True]
        finally:
            s.close()


class TestPairDurations:
    def test_tool_call_result_pairing(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice", "tool_call", {"tool": "read", "call_id": "c1", "ts": 100.0}
            )
            s.append_event("alice", "tool_result", {"call_id": "c1", "ts": 102.5})
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            call = next(sp for sp in out["spans"] if sp["type"] == "tool_call")
            assert call["dur"] == 2500
        finally:
            s.close()

    def test_tool_error_closes_span(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice", "tool_call", {"tool": "bash", "call_id": "c1", "ts": 10.0}
            )
            s.append_event("alice", "tool_error", {"call_id": "c1", "ts": 11.0})
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            call = next(sp for sp in out["spans"] if sp["type"] == "tool_call")
            assert call["dur"] == 1000
        finally:
            s.close()

    def test_subagent_duration_field_preferred(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice", "subagent_call", {"job_id": "j1", "name": "w", "ts": 100.0}
            )
            # ts diff is 50s but the authoritative duration says 42s.
            s.append_event(
                "alice",
                "subagent_result",
                {"job_id": "j1", "duration": 42.0, "ts": 150.0},
            )
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            call = next(sp for sp in out["spans"] if sp["type"] == "subagent_call")
            assert call["dur"] == 42000
        finally:
            s.close()

    def test_background_result_pairs_via_job_id(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice",
                "subagent_call",
                {"job_id": "j9", "name": "w", "background": True, "ts": 200.0},
            )
            s.append_event("alice", "background_result", {"job_id": "j9", "ts": 260.0})
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            call = next(sp for sp in out["spans"] if sp["type"] == "subagent_call")
            assert call["dur"] == 60000
        finally:
            s.close()

    def test_processing_pairing_per_turn(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "processing_start", {"ts": 300.0}, turn_index=1)
            s.append_event("alice", "processing_end", {"ts": 305.0}, turn_index=1)
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            start = next(sp for sp in out["spans"] if sp["type"] == "processing_start")
            assert start["dur"] == 5000
        finally:
            s.close()

    def test_tool_wait_uses_wait_ms(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "tool_wait", {"tool": "bash", "wait_ms": 84.25})
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            assert out["spans"][0]["dur"] == 84.25
        finally:
            s.close()

    def test_noise_types_excluded(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "cache_stats", {"cache_hit_ratio": 0.5})
            s.append_event("alice", "text_chunk", {"text": "hi"}, turn_index=1)
            s.flush()
            out = build_timeline_payload(s, "sess", agent=None, limit=100)
            assert [sp["type"] for sp in out["spans"]] == ["text_chunk"]
        finally:
            s.close()
