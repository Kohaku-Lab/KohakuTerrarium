"""Unit tests for :mod:`kohakuterrarium.studio.sessions.live.run_classes`."""

import asyncio

import kohakuterrarium.session.job_reaper as job_reaper
import kohakuterrarium.session.run_state as rs
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.live.run_classes import (
    CONTINUE_NUDGE,
    apply_run_classes,
    nudge_text,
    read_killed_jobs,
    read_run_classes,
)
from kohakuterrarium.terrarium import Terrarium


def test_read_run_classes_reads_every_agent_of_a_file(tmp_path, monkeypatch):
    path = tmp_path / "s.kohakutr"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s",
        config_type="terrarium",
        config_path="",
        pwd=".",
        agents=["a", "b", "c", "d"],
    )
    monkeypatch.setattr(rs, "BOOT_ID", "earlier")
    rs.write_run(store, "a", rs.IDLE)
    rs.write_run(store, "b", rs.STOPPED)
    rs.write_run(store, "c", rs.ACTIVE)
    store.close(update_status=False)
    monkeypatch.setattr(rs, "BOOT_ID", "now")
    assert read_run_classes(path) == {
        "a": "idle",
        "b": "stopped",
        "c": "interrupted",
        "d": "idle",
    }


async def test_apply_stops_stopped_creatures_and_nudges_interrupted_ones(
    scripted, creature_dir
):
    scripted["script"] = ["Continuing."]
    engine = Terrarium()
    try:
        keep = await engine.add_creature(str(creature_dir), name="keep", io="none")
        halt = await engine.add_creature(str(creature_dir), name="halt", io="none")
        assert await apply_run_classes(
            engine, halt.graph_id, {"halt": "stopped"}, nudge=True
        ) == {"stopped": ["halt"], "interrupted": [], "killed": {}}
        assert halt.is_running is False
        quiet = await apply_run_classes(
            engine, keep.graph_id, {"keep": "interrupted"}, nudge=False
        )
        assert quiet == {"stopped": [], "interrupted": ["keep"], "killed": {}}
        assert [
            m
            for m in keep.agent.controller.conversation.to_messages()
            if m["role"] == "user"
        ] == []
        await apply_run_classes(
            engine, keep.graph_id, {"keep": "interrupted"}, nudge=True
        )
        for _ in range(100):
            users = [
                m["content"]
                for m in keep.agent.controller.conversation.to_messages()
                if m["role"] == "user"
            ]
            if users:
                break
            await asyncio.sleep(0.02)
        assert users == [CONTINUE_NUDGE]
        assert keep.is_running is True
        empty = {"stopped": [], "interrupted": [], "killed": {}}
        assert await apply_run_classes(None, "x", {}, nudge=True) == empty
        assert (
            await apply_run_classes(
                engine, "no-such-graph", {"keep": "stopped"}, nudge=True
            )
            == empty
        )
    finally:
        await engine.shutdown()


def _job(job_id, name="bash", kind="tool", detail="command='sleep 9'"):
    return {"job_id": job_id, "kind": kind, "name": name, "detail": detail, "ts": 0}


def test_read_killed_jobs_scopes_to_the_dead_boot(tmp_path, monkeypatch):
    path = tmp_path / "s.kohakutr"
    store = SessionStore(str(path))
    store.init_meta("s", "terrarium", "", ".", ["a", "b", "c"])
    monkeypatch.setattr(rs, "BOOT_ID", "earlier")
    store.append_event(
        "a", "tool_call", {"name": "bash", "call_id": "ancient", "ts": 1}
    )
    lifecycle = rs.set_lifecycle(store, live=True)
    hosted = lifecycle["hosted_at"]
    store.append_event(
        "a", "tool_call", {"name": "bash", "call_id": "bg", "ts": hosted + 1}
    )
    store.append_event(
        "b", "tool_call", {"name": "make", "call_id": "torn", "ts": hosted + 2}
    )
    rs.mark_shutdown(store)
    shutdown_at = rs.read_lifecycle(store)["updated_at"]
    store.append_event(
        "b",
        "tool_result",
        {"call_id": "torn", "cancelled": True, "ts": shutdown_at + 1},
    )
    rs.thaw(store)
    store.close(update_status=False)
    monkeypatch.setattr(rs, "BOOT_ID", "now")
    killed = read_killed_jobs(path)
    assert {agent: [j["job_id"] for j in jobs] for agent, jobs in killed.items()} == {
        "a": ["bg"],
        "b": ["torn"],
    }


def test_read_killed_jobs_without_a_hosting_mark_uses_the_last_turn(tmp_path):
    path = tmp_path / "legacy.kohakutr"
    store = SessionStore(str(path))
    store.init_meta("l", "agent", "", ".", ["a", "b"])
    store.append_event("a", "tool_call", {"name": "bash", "call_id": "old", "ts": 50})
    store.append_event("a", "tool_call", {"name": "bash", "call_id": "new", "ts": 150})
    store.append_event(
        "b", "tool_call", {"name": "bash", "call_id": "unknown", "ts": 150}
    )
    rs.write_run(store, "a", rs.IDLE, now=100.0)
    store.state["run:a"] = {**rs.read_run(store, "a"), "started_at": 100.0}
    store.close(update_status=False)
    assert {
        a: [j["job_id"] for j in js] for a, js in read_killed_jobs(path).items()
    } == {"a": ["new"]}


def test_nudge_text_names_the_cut_off_turn_and_each_killed_job():
    assert nudge_text(True, []) == CONTINUE_NUDGE
    both = nudge_text(True, [_job("bg")])
    assert both.startswith("[The server restarted while you were in the middle")
    assert "- tool `bash` (command='sleep 9'), job bg" in both
    assert both.endswith("Rerun any you still need, or carry on without them.]")
    idle = nudge_text(False, [_job("sa", "explore", "subagent", "map it")])
    assert idle.startswith("[The server restarted. These jobs you started were killed")
    assert "- sub-agent `explore` (map it), job sa" in idle


async def test_idle_creature_with_killed_jobs_is_told_once_and_jobs_marked(
    scripted, creature_dir, tmp_path
):
    scripted["script"] = ["Rerunning it."]
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    try:
        keep = await engine.add_creature(str(creature_dir), name="keep", io="none")
        halt = await engine.add_creature(
            str(creature_dir), name="halt", io="none", graph=keep.graph_id
        )
        await engine.attach_session(keep.graph_id, tmp_path / "s.kohakutr")
        store = engine._session_stores[keep.graph_id]
        applied = await apply_run_classes(
            engine,
            keep.graph_id,
            {"halt": "stopped"},
            nudge=True,
            jobs={"keep": [_job("bg")], "halt": [_job("lost")]},
        )
        assert applied["stopped"] == ["halt"]
        assert applied["killed"] == {
            "keep": [{"job_id": "bg", "kind": "tool", "name": "bash"}]
        }
        for _ in range(200):
            users = [
                m["content"]
                for m in keep.agent.controller.conversation.to_messages()
                if m["role"] == "user"
            ]
            if users and job_reaper.reaped_ids(store, "keep"):
                break
            await asyncio.sleep(0.02)
        assert len(users) == 1 and "job bg" in users[0]
        assert users[0].startswith("[The server restarted. These jobs")
        assert job_reaper.reaped_ids(store, "keep") == {"bg"}
        assert job_reaper.reaped_ids(store, "halt") == set()
        assert halt.is_running is False
    finally:
        await engine.shutdown()
