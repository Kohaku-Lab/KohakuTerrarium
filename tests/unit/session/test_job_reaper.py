"""Unit tests for :mod:`kohakuterrarium.session.job_reaper`."""

import pytest

from kohakuterrarium.session import job_reaper
from kohakuterrarium.session.store import SessionStore


def _ev(etype, ts, **fields):
    return {"type": etype, "ts": ts, **fields}


EVENTS = [
    _ev("tool_call", 10, name="bash", call_id="old", args={"command": "ls"}),
    _ev("tool_call", 100, name="bash", call_id="done", args={"command": "pwd"}),
    _ev("tool_result", 101, call_id="done"),
    _ev("tool_call", 110, name="bash", call_id="bg", args={"command": "sleep 120"}),
    _ev("subagent_call", 120, name="explore", job_id="sa", task="map the repo"),
    _ev("subagent_call", 130, name="worker", job_id="sa-done", task="t"),
    _ev("subagent_result", 140, job_id="sa-done"),
    _ev("tool_call", 150, name="python", call_id="bgres"),
    _ev("background_result", 160, job_id="bgres"),
    _ev("tool_call", 170, name="grep", call_id="cancelled-by-user"),
    _ev("tool_result", 171, call_id="cancelled-by-user", cancelled=True),
    _ev("tool_call", 180, name="bash", call_id="torn", args={"command": "make"}),
    _ev("tool_result", 200, call_id="torn", interrupted=True),
    _ev("text", 190, content="no job id"),
    "not a dict",
]


def test_unfinished_jobs_are_started_since_the_boot_and_never_ended():
    jobs = job_reaper.unfinished_jobs(EVENTS, since=100)
    assert [(j["job_id"], j["kind"], j["name"]) for j in jobs] == [
        ("bg", "tool", "bash"),
        ("sa", "subagent", "explore"),
    ]
    assert jobs[0]["detail"] == "command='sleep 120'"
    assert jobs[1]["detail"] == "map the repo"
    assert [j["job_id"] for j in job_reaper.unfinished_jobs(EVENTS)][0] == "old"


def test_a_cancel_written_by_a_graceful_shutdown_counts_as_killed():
    jobs = job_reaper.unfinished_jobs(EVENTS, since=100, shutdown_at=195)
    assert [j["job_id"] for j in jobs] == ["bg", "sa", "torn"]
    assert "cancelled-by-user" not in [
        j["job_id"] for j in job_reaper.unfinished_jobs(EVENTS, 100, shutdown_at=172)
    ]


def test_detail_is_bounded_and_empty_detail_is_blank():
    long = [_ev("tool_call", 1, name="bash", call_id="x", args={"c": "y" * 500})]
    detail = job_reaper.unfinished_jobs(long)[0]["detail"]
    assert len(detail) == job_reaper.ARGS_LIMIT and detail.endswith("…")
    bare = [_ev("tool_call", 1, name="info", call_id="z", args={})]
    assert job_reaper.unfinished_jobs(bare)[0]["detail"] == ""


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    s.init_meta("s", "agent", "", str(tmp_path), ["main"])
    yield s
    s.close(update_status=False)


def test_reported_jobs_are_not_reported_again(store):
    store.append_event("main", "tool_call", {"name": "bash", "call_id": "j1", "ts": 5})
    store.append_event("main", "tool_call", {"name": "bash", "call_id": "j2", "ts": 6})
    assert [j["job_id"] for j in job_reaper.killed_jobs(store, "main")] == ["j1", "j2"]
    job_reaper.mark_reaped(store, "main", ["j1"])
    job_reaper.mark_reaped(store, "main", [])
    assert job_reaper.reaped_ids(store, "main") == {"j1"}
    assert [j["job_id"] for j in job_reaper.killed_jobs(store, "main")] == ["j2"]
    job_reaper.mark_reaped(store, "main", ["j2"])
    assert job_reaper.killed_jobs(store, "main") == []
    store.state["jobs:reaped:other"] = "garbage"
    assert job_reaper.reaped_ids(store, "other") == set()


def test_describe_lists_kind_name_detail_and_age():
    jobs = [
        {
            "job_id": "bg",
            "kind": "tool",
            "name": "bash",
            "detail": "command='sleep 120'",
            "ts": 1000,
        },
        {"job_id": "sa", "kind": "subagent", "name": "explore", "detail": "", "ts": 0},
    ]
    text = job_reaper.describe(jobs, now=1000 + 3725)
    assert text.splitlines() == [
        "- tool `bash` (command='sleep 120'), job bg, started 1h02m ago",
        "- sub-agent `explore`, job sa",
    ]
    assert job_reaper._duration(42) == "42s"
    assert job_reaper._duration(125) == "2m"
