"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.summary`."""

import pytest
from kohakuterrarium.errors import NotFoundError

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.summary import (
    _agents_for_summary,
    _aggregate_rollups,
    _scan_events_for_summary,
    _subagent_failed,
    build_summary_payload,
)

# ── helpers ──────────────────────────────────────────────────────


class TestSubagentFailed:
    def test_success_false(self):
        assert _subagent_failed({"type": "subagent_result", "success": False})

    def test_clean(self):
        assert not _subagent_failed({"type": "subagent_result", "success": True})

    def test_wrong_type(self):
        assert not _subagent_failed({"type": "tool_call"})


# ── _aggregate_rollups ──────────────────────────────────────────


class TestAggregateRollups:
    def test_basic(self):
        rows = [
            {"turn_index": 1, "tokens_in": 5, "tokens_out": 3, "tokens_cached": 1},
            {"turn_index": 2, "tokens_in": 2, "tokens_out": 1, "cost_usd": 0.01},
        ]
        out = _aggregate_rollups(rows)
        assert out["tokens"]["prompt"] == 7
        assert out["tokens"]["completion"] == 4
        assert out["cost_usd"] == 0.01

    def test_dedupe_turn_index(self):
        # Same turn appearing twice (e.g., one main + one attached)
        # counts as ONE turn for the totals.
        rows = [
            {"turn_index": 1, "tokens_in": 5, "tokens_out": 3},
            {"turn_index": 1, "tokens_in": 2, "tokens_out": 1},
        ]
        out = _aggregate_rollups(rows)
        assert out["turns"] == 1

    def test_count_by_agent_with_breakdown(self):
        rows = [
            {
                "turn_index": 1,
                "tokens_in": 0,
                "tokens_out": 0,
                "breakdown": [
                    {"kind": "main"},
                    {"kind": "subagent"},
                    {"kind": "main"},
                ],
            }
        ]
        out = _aggregate_rollups(rows, count_by_agent=True)
        # Subagent breakdown entry doesn't count.
        assert out["turns"] == 2

    def test_count_by_agent_no_breakdown(self):
        rows = [{"turn_index": 1}]
        out = _aggregate_rollups(rows, count_by_agent=True)
        # No breakdown → counted as 1.
        assert out["turns"] == 1

    def test_no_cost_returns_none(self):
        rows = [{"turn_index": 1, "tokens_in": 5}]
        out = _aggregate_rollups(rows)
        assert out["cost_usd"] is None

    def test_invalid_cost_skipped(self):
        rows = [{"turn_index": 1, "cost_usd": "bad"}]
        out = _aggregate_rollups(rows)
        assert out["cost_usd"] is None


# ── _scan_events_for_summary ───────────────────────────────────


class TestScanEvents:
    def test_counts_tool_calls(self):
        events = [
            {"type": "tool_call", "turn_index": 1},
            {"type": "tool_call", "turn_index": 1},
        ]
        out = _scan_events_for_summary(events)
        assert out["tool_calls"] == 2

    def test_tracks_error_turns(self):
        events = [
            {"type": "tool_error", "turn_index": 1},
            {"type": "tool_error", "turn_index": 1},  # same turn — dedup
            {"type": "subagent_error", "turn_index": 2},
        ]
        out = _scan_events_for_summary(events)
        assert sorted(out["error_turns"]) == [1, 2]

    def test_tracks_compact_turns(self):
        events = [
            {"type": "compact_complete", "turn_index": 1},
            {"type": "compact_replace", "turn_index": 2},
        ]
        out = _scan_events_for_summary(events)
        assert sorted(out["compact_turns"]) == [1, 2]

    def test_subagent_failed_counts_as_error(self):
        events = [
            {
                "type": "subagent_result",
                "turn_index": 1,
                "success": False,
            }
        ]
        out = _scan_events_for_summary(events)
        assert 1 in out["error_turns"]

    def test_spawned_in_turn_fallback(self):
        events = [{"type": "tool_error", "spawned_in_turn": 5}]
        out = _scan_events_for_summary(events)
        assert 5 in out["error_turns"]


# ── _agents_for_summary ────────────────────────────────────────


def _store(tmp_path, name="s.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


class TestAgentsForSummary:
    def test_default_returns_main_agents(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice", "bob"])
            meta = s.load_meta()
            out = _agents_for_summary(meta, s, None)
            assert "alice" in out
            assert "bob" in out
        finally:
            s.close()

    def test_explicit_known(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            meta = s.load_meta()
            assert _agents_for_summary(meta, s, "alice") == ["alice"]
        finally:
            s.close()

    def test_explicit_unknown_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            meta = s.load_meta()
            with pytest.raises(NotFoundError):
                _agents_for_summary(meta, s, "ghost")
        finally:
            s.close()

    def test_event_only_creature_appears_in_agent_list(self, tmp_path):
        # A creature that wrote events but was never added to meta["agents"]
        # is counted in the graph token total; it must also appear in the
        # agent list. load_meta() folds discover_agents_from_events into
        # meta["agents"], so both sources agree — this pins that guarantee.
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "terrarium", "/p", "/w", ["root"])
            s.append_event(
                "worker",
                "turn_token_usage",
                {"prompt_tokens": 5, "completion_tokens": 2},
                turn_index=1,
            )
            s.flush()
            meta = s.load_meta()
            out = _agents_for_summary(meta, s, None)
            assert "root" in out
            assert "worker" in out
        finally:
            s.close()

    def test_viewer_default_at_front(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice:attached:rev:0", "x", {})
            s.set_viewer_default_agent("alice:attached:rev:0")
            s.flush()
            meta = s.load_meta()
            out = _agents_for_summary(meta, s, None)
            assert out[0] == "alice:attached:rev:0"
        finally:
            s.close()


# ── build_summary_payload ──────────────────────────────────────


class TestBuildSummaryPayload:
    def test_basic_session(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("my-sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice",
                "turn_token_usage",
                {"prompt_tokens": 5, "completion_tokens": 3},
                turn_index=1,
            )
            s.append_event("alice", "tool_call", {"name": "bash"}, turn_index=1)
            s.append_event("alice", "tool_error", {"name": "bash"}, turn_index=1)
            s.flush()
            out = build_summary_payload(s, "my-sess", None)
            assert out["session_name"] == "my-sess"
            assert "alice" in out["agents"]
            assert out["totals"]["tool_calls"] == 1
            assert out["totals"]["errors"] == 1
        finally:
            s.close()

    def test_specific_agent(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice", "bob"])
            s.append_event(
                "alice",
                "turn_token_usage",
                {"prompt_tokens": 10},
                turn_index=1,
            )
            s.flush()
            out = build_summary_payload(s, "sess", "alice")
            assert out["agents"] == ["alice"]
        finally:
            s.close()

    def test_unknown_agent_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            with pytest.raises(NotFoundError):
                build_summary_payload(s, "sess", "ghost")
        finally:
            s.close()

    def test_multi_creature_total_includes_turnless_creature(self, tmp_path):
        # UXI-03: a channel-driven creature never bumps turn_index, so its
        # usage is turn-less and the turn-bucketed aggregate drops it —
        # the graph TOTAL would then count only the (user-driven) root.
        # The total must sum EVERY creature.
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "terrarium", "/p", "/w", ["root", "worker"])
            s.append_event(
                "root",
                "turn_token_usage",
                {"prompt_tokens": 100, "completion_tokens": 40},
                turn_index=1,
            )
            # worker: no turn_index (channel-driven) — per-call token_usage.
            s.append_event(
                "worker",
                "token_usage",
                {"prompt_tokens": 50, "completion_tokens": 20},
            )
            s.flush()
            out = build_summary_payload(s, "sess", None)
            assert out["totals"]["tokens"]["prompt"] == 150
            assert out["totals"]["tokens"]["completion"] == 60
        finally:
            s.close()

    def test_straddling_subagent_job_not_double_counted_in_total(self, tmp_path):
        # UXI-03: a sub-agent job with a turn-ful snapshot AND a turn-less
        # result must be counted ONCE in the graph total — the turn-bucketed
        # aggregate and the turn-less fold used to each count it.
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "terrarium", "/p", "/w", ["root"])
            s.append_event(
                "root",
                "turn_token_usage",
                {"prompt_tokens": 100, "completion_tokens": 40},
                turn_index=1,
            )
            s.append_event(
                "root",
                "subagent_token_usage",
                {
                    "job_id": "j1",
                    "name": "explore",
                    "prompt_tokens": 30,
                    "completion_tokens": 20,
                    "total_tokens": 50,
                },
                turn_index=1,
            )
            # Final result recorded turn-less (job straddles the boundary).
            s.append_event(
                "root",
                "subagent_result",
                {
                    "job_id": "j1",
                    "name": "explore",
                    "prompt_tokens": 48,
                    "completion_tokens": 32,
                    "total_tokens": 80,
                },
            )
            s.flush()
            out = build_summary_payload(s, "sess", None)
            # parent 100/40 + sub-agent ONCE at its max 48/32 = 148/72
            # (NOT 100/40 + 30/20 + 48/32 = 178/92).
            assert out["totals"]["tokens"]["prompt"] == 148
            assert out["totals"]["tokens"]["completion"] == 72
        finally:
            s.close()

    def test_multi_creature_turned_total_not_double_counted(self, tmp_path):
        # Regression: when both creatures DO have turn_index, the turn-less
        # fold-in adds nothing — the total must not double count.
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "terrarium", "/p", "/w", ["root", "worker"])
            s.append_event(
                "root",
                "turn_token_usage",
                {"prompt_tokens": 100, "completion_tokens": 40},
                turn_index=1,
            )
            s.append_event(
                "worker",
                "turn_token_usage",
                {"prompt_tokens": 50, "completion_tokens": 20},
                turn_index=1,
            )
            s.flush()
            out = build_summary_payload(s, "sess", None)
            assert out["totals"]["tokens"]["prompt"] == 150
            assert out["totals"]["tokens"]["completion"] == 60
        finally:
            s.close()

    def test_includes_hot_turns(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            for i in range(1, 7):
                s.append_event(
                    "alice",
                    "turn_token_usage",
                    {"prompt_tokens": i * 10},
                    turn_index=i,
                )
            s.flush()
            out = build_summary_payload(s, "sess", "alice")
            hot = out["hot_turns"]
            # Top-5 turns ranked by token usage, heaviest first — turn 6
            # (60 tokens) leads, turn 1 (10 tokens) is dropped.
            assert [t["turn_index"] for t in hot] == [6, 5, 4, 3, 2]
            assert hot[0]["tokens_in"] == 60
        finally:
            s.close()
