"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.rollups`."""

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.rollups import (
    ERROR_EVENT_TYPES,
    _as_float,
    _as_int,
    _empty_aggregate,
    _empty_row,
    _event_turn_index,
    _is_subagent_token_event,
    _subagent_failed,
    _subagent_label,
    _subagent_name_from_event,
    _touch_time,
    _usage_from_event,
    _usage_has_value,
    _with_usage_fallback,
    aggregate_turn_rollups,
    derive_own_turns_from_events,
    derive_subagent_turns_from_events,
    derive_turns_from_events,
    list_agent_namespaces,
    rollups_or_derived,
)

# ── small helpers ────────────────────────────────────────────────


class TestAsInt:
    def test_basic(self):
        assert _as_int(5) == 5
        assert _as_int("3") == 3

    def test_none_zero(self):
        assert _as_int(None) == 0
        assert _as_int("") == 0

    def test_invalid(self):
        assert _as_int("bad") == 0


class TestAsFloat:
    def test_basic(self):
        assert _as_float(1.5) == 1.5

    def test_none(self):
        assert _as_float(None) is None

    def test_invalid(self):
        assert _as_float("bad") is None


class TestEventTurnIndex:
    def test_turn_index_field(self):
        assert _event_turn_index({"turn_index": 5}) == 5

    def test_spawned_in_turn_fallback(self):
        assert _event_turn_index({"spawned_in_turn": 3}) == 3

    def test_no_field(self):
        assert _event_turn_index({}) is None

    def test_zero_ignored(self):
        assert _event_turn_index({"turn_index": 0}) is None


# ── row helpers ──────────────────────────────────────────────────


class TestEmptyRow:
    def test_basic_shape(self):
        row = _empty_row("alice", 5)
        assert row["agent"] == "alice"
        assert row["turn_index"] == 5
        assert row["tokens_in"] == 0
        assert row["has_error"] is False


class TestTouchTime:
    def test_updates_started_and_ended(self):
        row = _empty_row("a", 1)
        _touch_time(row, {"ts": 100.0})
        _touch_time(row, {"ts": 50.0})
        _touch_time(row, {"ts": 200.0})
        assert row["started_at"] == 50.0
        assert row["ended_at"] == 200.0

    def test_no_ts_ignored(self):
        row = _empty_row("a", 1)
        _touch_time(row, {})
        assert row["started_at"] is None


# ── _usage_from_event / _usage_has_value ────────────────────────


class TestUsageFromEvent:
    def test_explicit_fields(self):
        u = _usage_from_event(
            {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "cached_tokens": 1,
                "cost_usd": 0.01,
            }
        )
        assert u["tokens_in"] == 5
        assert u["tokens_out"] == 3
        assert u["cost_usd"] == 0.01

    def test_alias_keys(self):
        u = _usage_from_event({"tokens_in": 2, "tokens_out": 1, "tokens_cached": 0})
        assert u["tokens_in"] == 2

    def test_total_overrides_sum(self):
        u = _usage_from_event(
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 99}
        )
        assert u["total_tokens"] == 99

    def test_total_falls_back_to_sum(self):
        u = _usage_from_event({"prompt_tokens": 1, "completion_tokens": 2})
        assert u["total_tokens"] == 3


class TestUsageHasValue:
    def test_with_tokens(self):
        assert _usage_has_value({"tokens_in": 1})

    def test_zero(self):
        assert not _usage_has_value(
            {"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0}
        )

    def test_cost_only(self):
        assert _usage_has_value({"cost_usd": 0.01})


class TestWithUsageFallback:
    def test_no_fallback(self):
        evt = {"prompt_tokens": 5}
        assert _with_usage_fallback(evt, None) is evt

    def test_fills_missing(self):
        evt = {"prompt_tokens": 0}
        fallback = {"prompt_tokens": 7}
        out = _with_usage_fallback(evt, fallback)
        assert out["prompt_tokens"] == 7

    def test_cost_fallback(self):
        evt = {"cost_usd": None}
        fallback = {"cost_usd": 0.05}
        out = _with_usage_fallback(evt, fallback)
        assert out["cost_usd"] == 0.05


# ── subagent helpers ───────────────────────────────────────────


class TestSubagentHelpers:
    def test_is_subagent_token_event(self):
        assert _is_subagent_token_event({"type": "subagent_token_usage"})
        assert _is_subagent_token_event({"type": "subagent_result"})
        assert not _is_subagent_token_event({"type": "token_usage"})

    def test_subagent_failed_success_false(self):
        assert _subagent_failed({"type": "subagent_result", "success": False})

    def test_subagent_failed_with_error(self):
        assert _subagent_failed({"type": "subagent_result", "error": "boom"})

    def test_subagent_failed_interrupted(self):
        assert _subagent_failed({"type": "subagent_result", "interrupted": True})

    def test_subagent_failed_final_state(self):
        assert _subagent_failed({"type": "subagent_result", "final_state": "error"})

    def test_subagent_failed_clean_run_not_failed(self):
        assert not _subagent_failed({"type": "subagent_result", "success": True})

    def test_subagent_failed_wrong_type(self):
        assert not _subagent_failed({"type": "token_usage"})

    def test_subagent_label(self):
        assert _subagent_label("alice", "explore", 2) == "alice:subagent:explore:2"

    def test_subagent_name_from_event_explicit(self):
        assert _subagent_name_from_event({"name": "explore"}) == "explore"

    def test_subagent_name_from_event_job_id(self):
        assert _subagent_name_from_event({"job_id": "agent_critic_3"}) == "critic"

    def test_subagent_name_default(self):
        assert _subagent_name_from_event({}) == "subagent"


# ── ERROR_EVENT_TYPES ──────────────────────────────────────────


class TestErrorEventTypes:
    def test_known(self):
        assert "tool_error" in ERROR_EVENT_TYPES
        assert "subagent_error" in ERROR_EVENT_TYPES
        assert "processing_error" in ERROR_EVENT_TYPES


# ── _empty_aggregate ───────────────────────────────────────────


class TestEmptyAggregate:
    def test_shape(self):
        a = _empty_aggregate(7)
        assert a["turn_index"] == 7
        assert a["breakdown"] == []
        assert a["tokens_in"] == 0


# ── derive_own_turns_from_events ───────────────────────────────


class TestDeriveOwnTurns:
    def test_turn_token_usage(self):
        events = [
            {
                "type": "turn_token_usage",
                "turn_index": 1,
                "prompt_tokens": 10,
                "completion_tokens": 4,
            }
        ]
        rows = derive_own_turns_from_events(events, "alice")
        assert len(rows) == 1
        assert rows[0]["tokens_in"] == 10
        assert rows[0]["tokens_out"] == 4

    def test_token_usage_fallback(self):
        events = [
            {
                "type": "token_usage",
                "turn_index": 1,
                "prompt_tokens": 5,
                "completion_tokens": 2,
            }
        ]
        rows = derive_own_turns_from_events(events, "alice")
        assert rows[0]["tokens_in"] == 5

    def test_tool_call_count(self):
        events = [
            {"type": "tool_call", "turn_index": 1, "name": "bash"},
            {"type": "tool_call", "turn_index": 1, "name": "ls"},
        ]
        rows = derive_own_turns_from_events(events, "alice")
        assert rows[0]["tool_calls"] == 2

    def test_error_event_marks_error(self):
        events = [{"type": "tool_error", "turn_index": 1}]
        rows = derive_own_turns_from_events(events, "alice")
        assert rows[0]["has_error"] is True

    def test_compact_marks_compacted(self):
        events = [{"type": "compact_complete", "turn_index": 1}]
        rows = derive_own_turns_from_events(events, "alice")
        assert rows[0]["compacted"] is True

    def test_no_turn_index_ignored(self):
        events = [{"type": "token_usage"}]
        assert derive_own_turns_from_events(events, "alice") == []


# ── derive_subagent_turns_from_events ──────────────────────────


class TestDeriveSubagentTurns:
    def test_simple_result(self):
        events = [
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 5,
                "completion_tokens": 3,
            }
        ]
        rows = derive_subagent_turns_from_events(events, "alice")
        assert len(rows) == 1
        assert rows[0]["tokens_in"] == 5
        assert rows[0]["subagent_name"] == "explore"

    def test_skip_clean_zero_tokens(self):
        events = [
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "job_id": "j1",
                "success": True,
            }
        ]
        rows = derive_subagent_turns_from_events(events, "alice")
        # No tokens → skipped.
        assert rows == []

    def test_failed_keeps_row(self):
        events = [
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "job_id": "j1",
                "error": "boom",
            }
        ]
        rows = derive_subagent_turns_from_events(events, "alice")
        assert len(rows) == 1
        assert rows[0]["has_error"] is True

    def test_clean_result_with_cost_but_zero_tokens_is_skipped(self):
        # A clean (non-failed) subagent_result that has a cost but zero
        # token totals is still skipped — it passes _usage_has_value
        # (cost is set) but the zero-token guard drops it so the row
        # count isn't inflated by cost-only no-op results.
        events = [
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "job_id": "j1",
                "success": True,
                "cost_usd": 0.001,
                # no prompt/completion/total/cached tokens
            }
        ]
        rows = derive_subagent_turns_from_events(events, "alice")
        assert rows == []


# ── derive_turns_from_events ────────────────────────────────────


class TestDeriveTurnsFromEvents:
    def test_combines_own_and_subagents(self):
        events = [
            {
                "type": "turn_token_usage",
                "turn_index": 1,
                "prompt_tokens": 10,
                "completion_tokens": 4,
            },
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 5,
                "completion_tokens": 2,
            },
        ]
        rows = derive_turns_from_events(events, "alice")
        assert len(rows) == 1
        # 10 + 5 = 15.
        assert rows[0]["tokens_in"] == 15
        # Sub-agent breakdown attached.
        assert "subagent_breakdown" in rows[0]


# ── rollups_or_derived ─────────────────────────────────────────


class TestRollupsOrDerived:
    def test_no_rollups_uses_events(self, tmp_path):
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.append_event(
                "alice",
                "turn_token_usage",
                {"prompt_tokens": 5, "completion_tokens": 3},
                turn_index=1,
            )
            s.flush()
            rows = rollups_or_derived(s, "alice")
            # No stored rollup → derived from the single turn event.
            assert len(rows) == 1
            assert rows[0]["turn_index"] == 1
            assert rows[0]["tokens_in"] == 5
            assert rows[0]["tokens_out"] == 3
        finally:
            s.close()

    def test_uses_stored_rollups_if_present(self, tmp_path):
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.save_turn_rollup(
                "alice",
                1,
                {"tokens_in": 100, "tokens_out": 50},
            )
            rows = rollups_or_derived(s, "alice")
            # The stored rollup is returned verbatim, not re-derived.
            assert len(rows) == 1
            assert rows[0]["turn_index"] == 1
            assert rows[0]["tokens_in"] == 100
            assert rows[0]["tokens_out"] == 50
        finally:
            s.close()


# ── list_agent_namespaces ──────────────────────────────────────


class TestListAgentNamespaces:
    def test_main_agents_first(self, tmp_path):
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            out = list_agent_namespaces(s)
            assert ("alice", "main") in out
        finally:
            s.close()

    def test_attached_listed(self, tmp_path):
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice:attached:rev:0", "x", {})
            s.flush()
            out = list_agent_namespaces(s)
            kinds = {k for _, k in out}
            assert "attached" in kinds
        finally:
            s.close()

    def test_agent_discovered_from_events_not_in_meta(self, tmp_path):
        # An agent that wrote events but is NOT listed in meta.agents
        # must still be discovered and classified as "main".
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", [])  # empty meta agents
            s.append_event("undeclared", "user_message", {"content": "hi"})
            s.flush()
            out = list_agent_namespaces(s)
            assert ("undeclared", "main") in out
        finally:
            s.close()


# ── aggregate_turn_rollups ─────────────────────────────────────


class TestAggregateTurnRollups:
    def test_basic(self, tmp_path):
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice",
                "turn_token_usage",
                {"prompt_tokens": 5, "completion_tokens": 3},
                turn_index=1,
            )
            s.flush()
            rows = aggregate_turn_rollups(s)
            assert any(r["turn_index"] == 1 for r in rows)
        finally:
            s.close()

    def test_subagent_contribution_appears_in_breakdown(self, tmp_path):
        # A failed sub-agent owns no event namespace, but its result
        # tokens must still surface in the aggregate's breakdown.
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice",
                "turn_token_usage",
                {"prompt_tokens": 10, "completion_tokens": 4},
                turn_index=1,
            )
            s.append_event(
                "alice",
                "subagent_result",
                {
                    "name": "explore",
                    "job_id": "j1",
                    "prompt_tokens": 7,
                    "completion_tokens": 2,
                },
                turn_index=1,
            )
            s.flush()
            rows = aggregate_turn_rollups(s)
            turn1 = next(r for r in rows if r["turn_index"] == 1)
            # The sub-agent tokens are folded into the turn total AND
            # itemised in the breakdown with kind="subagent".
            kinds = {b["kind"] for b in turn1["breakdown"]}
            assert "subagent" in kinds
            assert turn1["tokens_in"] == 17
        finally:
            s.close()

    def test_compacted_and_error_flags_propagate(self, tmp_path):
        # A turn carrying both a compaction and an error event must end
        # up with has_error AND compacted set on its aggregate row.
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event(
                "alice",
                "turn_token_usage",
                {"prompt_tokens": 1, "completion_tokens": 1},
                turn_index=2,
            )
            s.append_event("alice", "tool_error", {"error": "boom"}, turn_index=2)
            s.append_event("alice", "compact_complete", {}, turn_index=2)
            s.flush()
            rows = aggregate_turn_rollups(s)
            turn2 = next(r for r in rows if r["turn_index"] == 2)
            assert turn2["has_error"] is True
            assert turn2["compacted"] is True
        finally:
            s.close()

    def test_stored_rollup_cost_accumulates_into_aggregate(self, tmp_path):
        # A persisted rollup row carrying a real cost_usd must have that
        # cost summed into the aggregate turn.
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.save_turn_rollup(
                "alice",
                1,
                {"tokens_in": 10, "tokens_out": 5, "cost_usd": 0.42},
            )
            rows = aggregate_turn_rollups(s)
            turn1 = next(r for r in rows if r["turn_index"] == 1)
            assert turn1["cost_usd"] == 0.42
        finally:
            s.close()

    def test_stored_rollup_with_invalid_turn_index_is_skipped(self, tmp_path):
        # A persisted rollup row with a non-positive turn_index must be
        # skipped by the aggregator rather than producing a bogus turn.
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.save_turn_rollup("alice", 0, {"tokens_in": 99, "tokens_out": 9})
            s.save_turn_rollup("alice", 2, {"tokens_in": 4, "tokens_out": 1})
            rows = aggregate_turn_rollups(s)
            turn_indices = {r["turn_index"] for r in rows}
            # turn_index 0 dropped; only the valid turn 2 survives.
            assert 0 not in turn_indices
            assert 2 in turn_indices
        finally:
            s.close()

    def test_non_numeric_stored_cost_is_swallowed(self, tmp_path):
        # A corrupt rollup row whose cost_usd is non-numeric must not
        # crash the aggregator — the bad cost is swallowed.
        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.save_turn_rollup(
                "alice",
                1,
                {"tokens_in": 2, "tokens_out": 1, "cost_usd": "not-a-number"},
            )
            rows = aggregate_turn_rollups(s)
            turn1 = next(r for r in rows if r["turn_index"] == 1)
            # Tokens still aggregated; the unparseable cost left as None.
            assert turn1["tokens_in"] == 2
            assert turn1["cost_usd"] is None
        finally:
            s.close()


# ── merge / fallback branch coverage ───────────────────────────


class TestSubagentMergeBranches:
    def test_anonymous_subagent_event_without_job_id_still_rows(self):
        # A subagent token event with NO job_id goes through the
        # "anonymous" path and still produces a contribution row.
        events = [
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "prompt_tokens": 4,
                "completion_tokens": 1,
                # no job_id
            }
        ]
        rows = derive_subagent_turns_from_events(events, "alice")
        assert len(rows) == 1
        assert rows[0]["tokens_in"] == 4
        assert rows[0]["job_id"] == ""

    def test_live_snapshot_then_result_dedupe_by_job_id(self):
        # A live subagent_token_usage snapshot followed by the final
        # subagent_result for the SAME job_id must collapse to one row
        # (the result, with the live snapshot folded in as fallback) —
        # not double-count.
        events = [
            {
                "type": "subagent_token_usage",
                "turn_index": 1,
                "job_id": "j1",
                "name": "explore",
                "prompt_tokens": 6,
                "completion_tokens": 2,
                "total_tokens": 8,
            },
            {
                "type": "subagent_result",
                "turn_index": 1,
                "job_id": "j1",
                "name": "explore",
                "prompt_tokens": 6,
                "completion_tokens": 2,
                "total_tokens": 8,
            },
        ]
        rows = derive_subagent_turns_from_events(events, "alice")
        # Exactly one row for job j1 — not two.
        assert len(rows) == 1
        assert rows[0]["job_id"] == "j1"

    def test_subagent_only_turn_creates_a_merged_row(self):
        # derive_turns_from_events with ONLY a sub-agent event (no parent
        # turn_token_usage) — _merge_subagents must synthesise an empty
        # parent row for that turn and fold the sub-agent tokens in.
        events = [
            {
                "type": "subagent_result",
                "turn_index": 3,
                "name": "explore",
                "job_id": "j9",
                "prompt_tokens": 9,
                "completion_tokens": 1,
            }
        ]
        rows = derive_turns_from_events(events, "alice")
        assert len(rows) == 1
        assert rows[0]["turn_index"] == 3
        assert rows[0]["tokens_in"] == 9
        assert rows[0]["subagent_breakdown"][0]["subagent_name"] == "explore"

    def test_subagent_started_ended_times_widen_the_turn_window(self):
        # _merge_subagents must pull the parent row's started_at/ended_at
        # outward to cover the sub-agent's timestamps.
        events = [
            {
                "type": "turn_token_usage",
                "turn_index": 1,
                "ts": 100.0,
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
            {
                "type": "subagent_result",
                "turn_index": 1,
                "ts": 50.0,  # earlier than the parent turn event
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 2,
                "completion_tokens": 1,
            },
        ]
        rows = derive_turns_from_events(events, "alice")
        row = rows[0]
        # The sub-agent's earlier ts widened the window back to 50.0.
        assert row["started_at"] == 50.0

    def test_subagent_error_marks_the_merged_turn(self):
        events = [
            {
                "type": "turn_token_usage",
                "turn_index": 1,
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
            {
                "type": "subagent_result",
                "turn_index": 1,
                "name": "explore",
                "job_id": "j1",
                "error": "subagent blew up",
                "prompt_tokens": 1,
                "completion_tokens": 0,
            },
        ]
        rows = derive_turns_from_events(events, "alice")
        # The failed sub-agent propagates has_error onto its parent turn.
        assert rows[0]["has_error"] is True

    def test_subagent_event_without_turn_index_is_ignored(self):
        # A sub-agent token event with no resolvable turn bucket is
        # dropped rather than crashing the derivation.
        events = [
            {
                "type": "subagent_result",
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 5,
                "completion_tokens": 1,
                # no turn_index / spawned_in_turn
            }
        ]
        assert derive_subagent_turns_from_events(events, "alice") == []


# ── _merge_subagents direct branch coverage ────────────────────


class TestMergeSubagentsDirect:
    def test_subagent_turn_with_no_parent_row_synthesises_one(self):
        # When sub_rows reference a turn that parent_rows never produced,
        # _merge_subagents must synthesise a fresh empty parent row for
        # that turn and fold the sub-agent usage into it.
        from kohakuterrarium.studio.persistence.viewer.rollups import (
            _merge_subagents,
        )

        parent_rows = []  # no parent turns at all
        sub_rows = [
            {
                "agent": "alice:subagent:explore:0",
                "turn_index": 5,
                "subagent_name": "explore",
                "job_id": "j1",
                "tokens_in": 8,
                "tokens_out": 2,
                "tokens_cached": 0,
                "cost_usd": 0.01,
                "started_at": 10.0,
                "ended_at": 20.0,
                "has_error": True,
            }
        ]
        merged = _merge_subagents(parent_rows, sub_rows, "alice")
        assert len(merged) == 1
        row = merged[0]
        assert row["turn_index"] == 5
        # The synthesised parent row carries the sub-agent's usage,
        # time window, error flag, and a breakdown entry.
        assert row["tokens_in"] == 8
        assert row["started_at"] == 10.0
        assert row["ended_at"] == 20.0
        assert row["has_error"] is True
        assert row["cost_usd"] == 0.01

    def test_subagent_row_with_invalid_turn_index_is_skipped(self):
        # A sub_row whose turn_index is non-positive / non-int is
        # skipped — it never reaches a merge bucket.
        from kohakuterrarium.studio.persistence.viewer.rollups import (
            _merge_subagents,
        )

        parent_rows = [
            {
                "agent": "alice",
                "turn_index": 1,
                "tokens_in": 3,
                "tokens_out": 1,
                "tokens_cached": 0,
                "cost_usd": None,
                "started_at": None,
                "ended_at": None,
                "has_error": False,
            }
        ]
        sub_rows = [
            {"agent": "x", "turn_index": 0, "tokens_in": 99, "tokens_out": 0},
            {"agent": "y", "turn_index": None, "tokens_in": 99, "tokens_out": 0},
        ]
        merged = _merge_subagents(parent_rows, sub_rows, "alice")
        # Only the valid parent turn survives; the bad sub_rows added
        # nothing.
        assert len(merged) == 1
        assert merged[0]["tokens_in"] == 3


# ── _add_usage cost accumulation ───────────────────────────────


class TestAddUsageCost:
    def test_cost_usd_accumulates_onto_the_row(self):
        from kohakuterrarium.studio.persistence.viewer.rollups import _add_usage

        row = _empty_row("alice", 1)
        _add_usage(row, {"tokens_in": 1, "cost_usd": 0.5})
        _add_usage(row, {"tokens_in": 2, "cost_usd": 0.25})
        # Two usages with cost → the row's cost_usd is the running sum.
        assert row["cost_usd"] == 0.75
        assert row["tokens_in"] == 3


# ── _own_rollups_or_derived: stored-rollup short-circuit ───────


class TestOwnRollupsOrDerived:
    def test_stored_rollup_short_circuits_event_derivation(self, tmp_path):
        from kohakuterrarium.studio.persistence.viewer.rollups import (
            _own_rollups_or_derived,
        )

        s = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            # A persisted rollup row exists — _own_rollups_or_derived
            # must return it directly without re-deriving from events.
            s.save_turn_rollup("alice", 1, {"tokens_in": 42, "tokens_out": 7})
            rows = _own_rollups_or_derived(s, "alice")
            assert len(rows) == 1
            assert rows[0]["tokens_in"] == 42
        finally:
            s.close()
