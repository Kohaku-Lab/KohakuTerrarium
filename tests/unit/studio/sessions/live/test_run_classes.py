"""Unit tests for :mod:`kohakuterrarium.studio.sessions.live.run_classes`."""

import asyncio

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.live.run_classes import (
    CONTINUE_NUDGE,
    apply_run_classes,
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
        ) == {"stopped": ["halt"], "interrupted": []}
        assert halt.is_running is False
        quiet = await apply_run_classes(
            engine, keep.graph_id, {"keep": "interrupted"}, nudge=False
        )
        assert quiet == {"stopped": [], "interrupted": ["keep"]}
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
        assert await apply_run_classes(None, "x", {}, nudge=True) == {
            "stopped": [],
            "interrupted": [],
        }
        assert await apply_run_classes(
            engine, "no-such-graph", {"keep": "stopped"}, nudge=True
        ) == {"stopped": [], "interrupted": []}
    finally:
        await engine.shutdown()
