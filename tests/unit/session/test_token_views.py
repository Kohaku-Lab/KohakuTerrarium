"""Unit tests for :mod:`kohakuterrarium.session.token_views`."""

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.token_views import (
    _as_int,
    _by_turn_from_events,
    _decode_key,
    _empty_usage,
    _iter_subagent_runs,
    _own_usage_for_namespace,
    _state_usage_to_shape,
    _subagent_name_from_event,
    _subagent_tokens_from_events,
    _subagent_usage_map,
    _usage_to_shape,
    _with_usage_fallback,
    token_usage,
    token_usage_all_loops,
)

# ── small helpers ─────────────────────────────────────────────────


class TestDecodeKey:
    def test_bytes_decoded(self):
        assert _decode_key(b"x") == "x"

    def test_str_unchanged(self):
        assert _decode_key("x") == "x"

    def test_other_coerced_to_str(self):
        assert _decode_key(42) == "42"


class TestAsInt:
    def test_basic(self):
        assert _as_int(5) == 5
        assert _as_int("3") == 3

    def test_falsy_returns_zero(self):
        assert _as_int(None) == 0
        assert _as_int("") == 0
        assert _as_int(0) == 0

    def test_bad_input_returns_zero(self):
        assert _as_int("not-a-number") == 0


class TestEmptyUsage:
    def test_shape(self):
        u = _empty_usage()
        assert u == {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
        }


# ── _usage_to_shape ───────────────────────────────────────────────


class TestUsageToShape:
    def test_total_input_output_keys(self):
        out = _usage_to_shape(
            {
                "total_input_tokens": 10,
                "total_output_tokens": 7,
                "total_cached_tokens": 3,
            }
        )
        assert out["prompt_tokens"] == 10
        assert out["completion_tokens"] == 7
        assert out["cached_tokens"] == 3
        # Implicit total = prompt + completion.
        assert out["total_tokens"] == 17

    def test_prompt_completion_keys(self):
        out = _usage_to_shape(
            {"prompt_tokens": 5, "completion_tokens": 4, "cached_tokens": 1}
        )
        assert out["prompt_tokens"] == 5
        assert out["completion_tokens"] == 4
        assert out["cached_tokens"] == 1
        assert out["total_tokens"] == 9

    def test_tokens_in_out_aliases(self):
        out = _usage_to_shape({"tokens_in": 2, "tokens_out": 3, "tokens_cached": 1})
        assert out["prompt_tokens"] == 2
        assert out["completion_tokens"] == 3
        assert out["cached_tokens"] == 1
        assert out["total_tokens"] == 5

    def test_explicit_total_wins(self):
        out = _usage_to_shape(
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 99}
        )
        assert out["total_tokens"] == 99

    def test_explicit_total_zero_falls_back_to_sum(self):
        # When explicit total <= 0, sum kicks in.
        out = _usage_to_shape(
            {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 0}
        )
        assert out["total_tokens"] == 9


class TestStateUsageToShape:
    def test_delegates_to_usage_to_shape(self):
        assert _state_usage_to_shape({"prompt_tokens": 1}) == _usage_to_shape(
            {"prompt_tokens": 1}
        )


# ── _with_usage_fallback ──────────────────────────────────────────


class TestWithUsageFallback:
    def test_no_fallback_returns_input(self):
        evt = {"prompt_tokens": 5}
        out = _with_usage_fallback(evt, None)
        assert out is evt

    def test_fills_missing_fields(self):
        evt = {"prompt_tokens": 0}
        fallback = {"prompt_tokens": 7, "completion_tokens": 4}
        out = _with_usage_fallback(evt, fallback)
        assert out["prompt_tokens"] == 7
        assert out["completion_tokens"] == 4

    def test_existing_positive_kept(self):
        evt = {"prompt_tokens": 5}
        fallback = {"prompt_tokens": 99}
        out = _with_usage_fallback(evt, fallback)
        # Original positive value preserved.
        assert out["prompt_tokens"] == 5

    def test_does_not_mutate_input(self):
        evt = {"prompt_tokens": 0}
        fallback = {"prompt_tokens": 7}
        _with_usage_fallback(evt, fallback)
        assert evt == {"prompt_tokens": 0}


# ── _subagent_name_from_event ─────────────────────────────────────


class TestSubagentNameFromEvent:
    def test_explicit_name(self):
        assert _subagent_name_from_event({"name": "explore"}) == "explore"

    def test_subagent_field(self):
        assert _subagent_name_from_event({"subagent": "plan"}) == "plan"

    def test_subagent_name_field(self):
        assert _subagent_name_from_event({"subagent_name": "critic"}) == "critic"

    def test_from_job_id(self):
        # ``agent_<name>_<seq>`` → name part.
        assert _subagent_name_from_event({"job_id": "agent_critic_3"}) == "critic"

    def test_none_when_no_match(self):
        assert _subagent_name_from_event({}) is None

    def test_empty_string_ignored(self):
        assert _subagent_name_from_event({"name": ""}) is None


# ── public API: token_usage / token_usage_all_loops ───────────────


def _store_with_usage(tmp_path, agent="alice", usage=None):
    s = SessionStore(str(tmp_path / "x.kohakutr"))
    if usage is not None:
        s.save_state(agent, token_usage=usage)
    return s


class TestPublicTokenUsage:
    def test_requires_agent(self, tmp_path):
        s = _store_with_usage(tmp_path)
        try:
            with pytest.raises(ValueError):
                token_usage(s, None)
        finally:
            s.close()

    def test_returns_empty_when_no_state(self, tmp_path):
        s = _store_with_usage(tmp_path)
        try:
            out = token_usage(s, "missing")
            assert out["total_tokens"] == 0
            assert out["prompt_tokens"] == 0
            assert out["completion_tokens"] == 0
            assert out["cached_tokens"] == 0
        finally:
            s.close()

    def test_reads_state_usage(self, tmp_path):
        s = _store_with_usage(
            tmp_path,
            agent="alice",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        try:
            out = token_usage(s, "alice")
            assert out["prompt_tokens"] == 10
            assert out["completion_tokens"] == 5
            assert out["total_tokens"] == 15
        finally:
            s.close()

    def test_include_subagents_empty(self, tmp_path):
        s = _store_with_usage(tmp_path, "alice", usage={"prompt_tokens": 1})
        try:
            out = token_usage(s, "alice", include_subagents=True)
            assert "subagents" in out
            # No subagents recorded → empty dict.
            assert out["subagents"] == {}
        finally:
            s.close()

    def test_include_attached_empty(self, tmp_path):
        s = _store_with_usage(tmp_path, "alice", usage={"prompt_tokens": 1})
        try:
            out = token_usage(s, "alice", include_attached=True)
            assert "attached" in out
            assert out["attached"] == {}
        finally:
            s.close()

    def test_by_turn_uses_rollups(self, tmp_path):
        s = _store_with_usage(tmp_path, "alice", usage={"prompt_tokens": 1})
        try:
            s.save_turn_rollup(
                "alice",
                0,
                {
                    "tokens_in": 5,
                    "tokens_out": 6,
                    "tokens_cached": 1,
                },
            )
            out = token_usage(s, "alice", by_turn=True)
            assert "by_turn" in out
            assert len(out["by_turn"]) == 1
            row = out["by_turn"][0]
            assert row["turn_index"] == 0
            assert row["prompt"] == 5
            assert row["completion"] == 6
            assert row["cached"] == 1
        finally:
            s.close()


class TestTokenUsageAllLoops:
    def test_no_state_returns_empty(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            assert token_usage_all_loops(s) == []
        finally:
            s.close()

    def test_main_agent_listed(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.save_state("alice", token_usage={"prompt_tokens": 4})
            loops = token_usage_all_loops(s)
            names = [n for n, _ in loops]
            assert "alice" in names
        finally:
            s.close()

    def test_discovers_agents_from_events(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            # No meta — agent emerges purely from event keys.
            s.append_event("bob", "x", {})
            s.save_state("bob", token_usage={"prompt_tokens": 2})
            s.flush()
            names = [n for n, _ in token_usage_all_loops(s)]
            assert "bob" in names
        finally:
            s.close()

    def test_attached_agent_listed(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            # Stamp an attached namespace event so discovery picks it up.
            s.append_event("alice:attached:rev:0", "x", {})
            s.save_state("alice:attached:rev:0", token_usage={"prompt_tokens": 3})
            s.flush()
            loops = token_usage_all_loops(s)
            namespaces = [n for n, _ in loops]
            assert "alice:attached:rev:0" in namespaces
        finally:
            s.close()


class TestByTurnFromEvents:
    def test_uses_events_when_no_rollup(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.append_event(
                "alice",
                "token_usage",
                {"prompt_tokens": 5, "completion_tokens": 4},
                turn_index=1,
            )
            s.append_event(
                "alice",
                "token_usage",
                {"prompt_tokens": 2, "completion_tokens": 1},
                turn_index=2,
            )
            s.flush()
            out = token_usage(s, "alice", by_turn=True)
            rows = out["by_turn"]
            assert len(rows) == 2
            assert rows[0]["turn_index"] == 1
            assert rows[0]["prompt"] == 5
            assert rows[1]["turn_index"] == 2
        finally:
            s.close()


class TestSubagentUsageMap:
    def test_records_subagent_run(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.save_subagent("alice", "explore", 0, {"task": "t"})
            # Emit a subagent_result event with token fields.
            s.append_event(
                "alice",
                "subagent_result",
                {
                    "name": "explore",
                    "job_id": "j1",
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                },
            )
            s.flush()
            out = token_usage(s, "alice", include_subagents=True)
            sub = out["subagents"]
            path = "alice:subagent:explore:0"
            assert path in sub
            assert sub[path]["prompt_tokens"] == 5
        finally:
            s.close()


# -- _usage_to_shape defensive total-coercion ---------------------


class TestUsageToShapeDefensive:
    def test_unparseable_explicit_total_falls_back_to_sum(self):
        # An explicit total_tokens that can't be coerced to int -> the
        # shape falls back to prompt + completion rather than crashing.
        out = _usage_to_shape(
            {
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "total_tokens": ["not", "a", "number"],
            }
        )
        assert out["total_tokens"] == 10


# -- _own_usage_for_namespace state.get failure -------------------


class TestOwnUsageForNamespace:
    def test_state_get_raising_returns_empty_shape(self, tmp_path, monkeypatch):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:

            def _boom(key, default=None):
                raise TypeError("state backend down")

            monkeypatch.setattr(s.state, "get", _boom)
            out = _own_usage_for_namespace(s, "alice")
            # Defensive: a failed state read yields the zero shape.
            assert out == _empty_usage()
        finally:
            s.close()

    def test_non_dict_state_value_returns_empty_shape(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            # token_usage stored as a non-dict (corrupt) -> zero shape.
            s.state["alice:token_usage"] = ["junk"]
            s.flush()
            assert _own_usage_for_namespace(s, "alice") == _empty_usage()
        finally:
            s.close()


# -- _iter_subagent_runs key-shape filtering ----------------------


class TestIterSubagentRuns:
    def test_skips_malformed_keys(self, tmp_path):
        # Only well-formed ``<parent>:<name>:<run>:meta`` keys with an
        # int run are counted; non-meta keys, wrong segment counts, and
        # non-int runs are all skipped.
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.save_subagent("alice", "explore", 0, {"task": "t"})
            # Inject malformed neighbours straight into the table.
            s.subagents["alice:explore:notanint:meta"] = {"x": 1}
            s.subagents["alice:explore:0:state"] = {"x": 1}  # not :meta
            s.subagents["alice:onlytwo:meta"] = {"x": 1}  # rsplit -> 2 parts
            s.flush()
            runs = _iter_subagent_runs(s, "alice")
            # Only the clean (name, run) pair survives.
            assert runs == [("explore", 0)]
        finally:
            s.close()

    def test_dedupes_repeated_runs(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.save_subagent("alice", "explore", 0, {"task": "a"})
            # Re-save same (name, run) -- must not produce a duplicate.
            s.save_subagent("alice", "explore", 0, {"task": "b"})
            s.flush()
            assert _iter_subagent_runs(s, "alice") == [("explore", 0)]
        finally:
            s.close()


# -- _subagent_tokens_from_events edge cases ----------------------


class TestSubagentTokensFromEvents:
    def test_zero_usage_successful_result_is_skipped(self):
        # A successful subagent_result carrying zero tokens contributes
        # nothing -- it is filtered before the name lookup.
        events = [
            {
                "type": "subagent_result",
                "name": "explore",
                "job_id": "j1",
                "success": True,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        ]
        assert _subagent_tokens_from_events(events) == {}

    def test_event_without_name_is_skipped(self):
        # A subagent_result with tokens but no resolvable name is dropped.
        events = [{"type": "subagent_result", "job_id": "j1", "prompt_tokens": 5}]
        assert _subagent_tokens_from_events(events) == {}

    def test_anonymous_event_without_job_id_still_grouped(self):
        # A result with a name but NO job_id lands in the anonymous
        # bucket and is still surfaced under its name.
        events = [
            {
                "type": "subagent_result",
                "name": "explore",
                "prompt_tokens": 7,
                "completion_tokens": 1,
            }
        ]
        out = _subagent_tokens_from_events(events)
        assert "explore" in out
        assert out["explore"][0]["prompt_tokens"] == 7

    def test_token_usage_update_then_result_merges(self):
        # A subagent_token_usage update arriving before the final
        # subagent_result is merged into it via the job_id fallback.
        events = [
            {
                "type": "subagent_token_usage",
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 9,
            },
            {
                "type": "subagent_result",
                "name": "explore",
                "job_id": "j1",
                "completion_tokens": 2,
            },
        ]
        out = _subagent_tokens_from_events(events)
        # The result row inherited the earlier prompt_tokens.
        assert out["explore"][0]["prompt_tokens"] == 9
        assert out["explore"][0]["completion_tokens"] == 2

    def test_lower_token_usage_update_does_not_replace_higher(self):
        # Two token_usage updates for the same job_id: the smaller one
        # must not clobber the larger running total.
        events = [
            {
                "type": "subagent_token_usage",
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 50,
            },
            {
                "type": "subagent_token_usage",
                "name": "explore",
                "job_id": "j1",
                "prompt_tokens": 10,
            },
        ]
        out = _subagent_tokens_from_events(events)
        assert out["explore"][0]["prompt_tokens"] == 50


# -- _subagent_usage_map run/event alignment ----------------------


class TestSubagentUsageMapAlignment:
    def test_run_with_no_event_gets_empty_shape(self, tmp_path):
        # A recorded subagent run with no matching token event still
        # appears in the map, carrying the zero shape.
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.save_subagent("alice", "explore", 0, {"task": "t"})
            s.flush()
            out = _subagent_usage_map(s, "alice")
            assert out["alice:subagent:explore:0"] == _empty_usage()
        finally:
            s.close()

    def test_extra_event_without_meta_row_is_surfaced(self, tmp_path):
        # A token event for a run with no meta row is still surfaced
        # under a synthesised path (defensive plugin/attach path).
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            # No save_subagent -- only the event exists.
            s.append_event(
                "alice",
                "subagent_result",
                {"name": "ghost", "job_id": "j9", "prompt_tokens": 11},
            )
            s.flush()
            out = _subagent_usage_map(s, "alice")
            # Surfaced at index 0 even with no meta row.
            assert out.get("alice:subagent:ghost:0", {}).get("prompt_tokens") == 11
        finally:
            s.close()


# -- _by_turn_from_events type filter -----------------------------


class TestByTurnFromEventsFilter:
    def test_non_token_events_are_ignored(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            # A non-token event must not create a turn bucket.
            s.append_event("alice", "user_message", {"content": "hi"}, turn_index=1)
            s.append_event(
                "alice",
                "token_usage",
                {"prompt_tokens": 3, "completion_tokens": 1},
                turn_index=2,
            )
            s.flush()
            rows = _by_turn_from_events(s, "alice")
            # Only the token_usage event's turn (2) shows up.
            assert [r["turn_index"] for r in rows] == [2]
        finally:
            s.close()


# -- token_usage_all_loops extra-main + subagent path -------------


class TestAllLoopsExtraMains:
    def test_event_discovered_main_with_subagent(self, tmp_path):
        # A main agent that exists only in event keys (not meta) is
        # picked up, AND its sub-agents are enumerated right after it.
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.append_event("carol", "x", {})
            s.save_state("carol", token_usage={"prompt_tokens": 4})
            s.save_subagent("carol", "explore", 0, {"task": "t"})
            s.append_event(
                "carol",
                "subagent_result",
                {"name": "explore", "job_id": "j1", "prompt_tokens": 2},
            )
            s.flush()
            loops = token_usage_all_loops(s)
            names = [n for n, _ in loops]
            assert "carol" in names
            # The sub-agent path is listed right after its parent.
            assert "carol:subagent:explore:0" in names
        finally:
            s.close()
