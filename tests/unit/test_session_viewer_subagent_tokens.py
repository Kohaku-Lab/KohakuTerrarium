"""Session viewer rollups include vertical sub-agent token usage."""

from types import SimpleNamespace

from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.rollups import (
    aggregate_turn_rollups,
    rollups_or_derived,
)
from kohakuterrarium.studio.persistence.viewer.summary import build_summary_payload


class _AgentStub:
    _turn_index = 1
    _branch_id = 1
    _parent_branch_path: list[tuple[int, int]] = []
    controller = None
    session = None


def _store(tmp_path):
    store = SessionStore(tmp_path / "viewer-subagents.kohakutr")
    store.init_meta(
        session_id="viewer-subagents",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    return store


def test_turn_rollup_includes_failed_subagent_tokens(tmp_path):
    store = _store(tmp_path)
    try:
        out = SessionOutput("host", store, _AgentStub())
        out.on_activity_with_metadata(
            "token_usage",
            "",
            {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "cached_tokens": 1,
            },
        )
        out.on_activity_with_metadata(
            "turn_token_usage",
            "",
            {
                "turn_index": 1,
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "cached_tokens": 1,
            },
        )
        out.on_activity_with_metadata(
            "subagent_error",
            "[explore] interrupted",
            {
                "job_id": "agent_explore_1",
                "error": "interrupted",
                "result": "partial",
                "interrupted": True,
                "final_state": "interrupted",
                "turns": 2,
                "duration": 0.5,
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cached_tokens": 40,
            },
        )

        row = rollups_or_derived(store, "host")[0]
        assert row["tokens_in"] == 110
        assert row["tokens_out"] == 23
        assert row["tokens_cached"] == 41
        assert row["has_error"] is True
        assert row["subagent_breakdown"][0]["tokens_in"] == 100

        agg = aggregate_turn_rollups(store)[0]
        assert agg["tokens_in"] == 110
        assert agg["tokens_out"] == 23
        assert agg["tokens_cached"] == 41
        assert agg["has_error"] is True
        assert any(b["kind"] == "subagent" for b in agg["breakdown"])
    finally:
        store.close(update_status=False)


def test_summary_uses_aggregate_rollups_for_subagents(tmp_path):
    store = _store(tmp_path)
    try:
        out = SessionOutput("host", store, _AgentStub())
        out.on_activity_with_metadata(
            "turn_token_usage",
            "",
            {"turn_index": 1, "prompt_tokens": 5, "completion_tokens": 2},
        )
        out.on_activity_with_metadata(
            "subagent_done",
            "[plan] done",
            {
                "job_id": "agent_plan_1",
                "result": "ok",
                "prompt_tokens": 50,
                "completion_tokens": 8,
                "cached_tokens": 7,
            },
        )

        payload = build_summary_payload(store, "viewer-subagents", None)
        tokens = payload["totals"]["tokens"]
        assert tokens == {"prompt": 55, "completion": 10, "cached": 7}
        assert payload["totals"]["turns"] == 1
    finally:
        store.close(update_status=False)


def test_token_usage_all_loops_accepts_token_aliases(tmp_path):
    store = _store(tmp_path)
    try:
        out = SessionOutput(
            "host", store, SimpleNamespace(controller=None, session=None)
        )
        run = store.next_subagent_run("host", "review")
        store.save_subagent("host", "review", run, {"task": "x"})
        out.on_activity_with_metadata(
            "subagent_error",
            "[review] failed",
            {
                "job_id": "agent_review_1",
                "tokens_in": 70,
                "tokens_out": 9,
                "tokens_cached": 11,
                "interrupted": True,
            },
        )
        usage = dict(store.token_usage_all_loops())
        assert usage["host:subagent:review:0"]["prompt_tokens"] == 70
        assert usage["host:subagent:review:0"]["completion_tokens"] == 9
        assert usage["host:subagent:review:0"]["cached_tokens"] == 11
    finally:
        store.close(update_status=False)


def test_live_subagent_token_update_used_when_final_result_missing(tmp_path):
    store = _store(tmp_path)
    try:
        out = SessionOutput("host", store, _AgentStub())
        out.on_activity_with_metadata(
            "turn_token_usage",
            "",
            {"turn_index": 1, "prompt_tokens": 5, "completion_tokens": 2},
        )
        out.on_activity_with_metadata(
            "subagent_token_update",
            "[explore] tokens",
            {
                "subagent": "explore",
                "job_id": "agent_explore_1",
                "prompt_tokens": 200,
                "completion_tokens": 30,
                "total_tokens": 230,
                "cached_tokens": 80,
            },
        )

        row = rollups_or_derived(store, "host")[0]
        assert row["tokens_in"] == 205
        assert row["tokens_out"] == 32
        assert row["tokens_cached"] == 80
        assert row["subagent_breakdown"][0]["job_id"] == "agent_explore_1"

        usage = dict(store.token_usage_all_loops())
        assert usage["host:subagent:explore:0"]["prompt_tokens"] == 200
        assert usage["host:subagent:explore:0"]["completion_tokens"] == 30
    finally:
        store.close(update_status=False)


def test_subagent_token_update_not_double_counted_with_final_result(tmp_path):
    store = _store(tmp_path)
    try:
        out = SessionOutput("host", store, _AgentStub())
        out.on_activity_with_metadata(
            "subagent_token_update",
            "[review] tokens",
            {
                "subagent": "review",
                "job_id": "agent_review_1",
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
        )
        out.on_activity_with_metadata(
            "subagent_error",
            "[review] failed",
            {
                "job_id": "agent_review_1",
                "prompt_tokens": 120,
                "completion_tokens": 15,
                "total_tokens": 135,
                "error": "boom",
            },
        )

        row = rollups_or_derived(store, "host")[0]
        assert row["tokens_in"] == 120
        assert row["tokens_out"] == 15
        assert len(row["subagent_breakdown"]) == 1
    finally:
        store.close(update_status=False)
