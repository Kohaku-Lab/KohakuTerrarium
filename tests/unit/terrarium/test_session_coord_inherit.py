"""Verify split/merge inherit the resumable subset of parent meta.

Pre-fix bug: ``split_session_store`` / ``merge_session_stores`` only
stamped ``session_id`` + ``parent_session_ids`` + ``split_at`` /
``merged_at`` on the new store(s).  The new file then had no
``config_type`` / ``config_path`` / ``config_snapshot`` / ``pwd`` —
so a resume off the split file failed with "Session has no
config_path or config_snapshot in metadata".
"""

from kohakuterrarium.session.store import SessionStore
import pytest
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.core.config import build_agent_config
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.resume import (
    prepare_resume_workspace,
    resume_into_engine,
)
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.terrarium.session_coord import (
    merge_session_stores,
    split_session_store,
)


def test_split_inherits_resumable_meta(tmp_path):
    parent_path = tmp_path / "parent.kohakutr"
    parent = SessionStore(str(parent_path))
    parent.init_meta(
        session_id="g1",
        config_type="agent",
        config_path="/cfg/path",
        pwd="/work",
        agents=["alice"],
        config_snapshot={"name": "alice", "system_prompt": "hi"},
    )
    child_paths = [tmp_path / "child_a.kohakutr", tmp_path / "child_b.kohakutr"]
    children = split_session_store(parent, child_paths)
    try:
        for child in children:
            meta = child.load_meta()
            assert meta["format_version"] == 2
            assert meta.get("config_type") == "agent"
            assert meta.get("config_path") == "/cfg/path"
            assert meta.get("config_snapshot", {}).get("name") == "alice"
            # Split bookkeeping is preserved.
            assert meta.get("split_at") is not None
            assert meta.get("parent_session_ids")
    finally:
        for c in children:
            c.close()
        parent.close()


def test_merge_inherits_resumable_meta_from_first_old_store(tmp_path):
    a = SessionStore(str(tmp_path / "a.kohakutr"))
    a.init_meta(
        session_id="g1",
        config_type="agent",
        config_path="/cfg/a",
        pwd="/work",
        agents=["alice"],
        config_snapshot={"name": "alice"},
    )
    b = SessionStore(str(tmp_path / "b.kohakutr"))
    b.init_meta(
        session_id="g2",
        config_type="agent",
        config_path="/cfg/b",
        pwd="/work",
        agents=["bob"],
        config_snapshot={"name": "bob"},
    )
    merged_path = tmp_path / "merged.kohakutr"
    merged = merge_session_stores([a, b], str(merged_path))
    try:
        meta = merged.load_meta()
        assert meta["format_version"] == 2
        # First store's config wins for the resumable subset.
        assert meta.get("config_type") == "agent"
        assert meta.get("config_path") == "/cfg/a"
        assert meta.get("config_snapshot", {}).get("name") == "alice"
        assert meta.get("merged_at") is not None
        # Parent lineage covers BOTH old stores.
        parents = list(meta.get("parent_session_ids") or [])
        assert set(parents) == {"g1", "g2"}
    finally:
        merged.close()
        a.close()
        b.close()


async def test_old_source_resume_reaches_latest_merged_conversation(
    tmp_path, monkeypatch
):
    for module in ("bootstrap.llm", "bootstrap.agent_init"):
        monkeypatch.setattr(
            f"kohakuterrarium.{module}.create_llm_provider",
            lambda *_args, **_kwargs: ScriptedLLM(["first", "latest"]),
        )
    async with Terrarium(session_dir=tmp_path) as engine:
        creatures = []
        original_paths = []
        for name in ("alice", "bob"):
            cfg = build_agent_config(
                {
                    "name": name,
                    "llm": "openai/gpt-5.4",
                    "tools": [],
                    "input": {"type": "none"},
                    "output": {"type": "none"},
                    "compact": {"enabled": False},
                },
                tmp_path,
            )
            creature = await engine.add_creature(
                cfg,
                start=True,
                io="headless",
                pwd=tmp_path,
            )
            creatures.append(creature)
            original_paths.append(engine._session_stores[creature.graph_id].path)
            await creature.run("before merge", timeout=5)
        stale_plan = prepare_resume_workspace(original_paths[0])
        merged = await engine.connect(*creatures, channel="together")
        target = engine._session_stores[merged.graph_id]
        for path in original_paths:
            if str(path) != str(target.path):
                assert read_session_meta(path)["resume_successor"]["state"] == "ready"
        for creature in creatures:
            await creature.run("after merge", timeout=5)
        expected_ids = {creature.creature_id for creature in creatures}
    for path in original_paths:
        resumed = await Terrarium.resume(path)
        async with resumed:
            assert {c.creature_id for c in resumed.list_creatures()} == expected_ids
            for creature in resumed.list_creatures():
                assert "after merge" in str(
                    creature.agent.controller.conversation.to_messages()
                )
                assert creature.agent._turn_index == 2
    async with Terrarium() as resumed:
        await resume_into_engine(
            resumed, original_paths[0], prepared_workspace=stale_plan
        )
        assert {c.creature_id for c in resumed.list_creatures()} == expected_ids
        live = next(iter(resumed._session_stores.values()))
        with pytest.raises(SessionNotResumableError, match="already running"):
            await resumed.adopt_session(live, pwd=str(tmp_path))
        assert live._closed is False
        assert await resumed.adopt_session(live) in resumed._session_stores
        assert live._closed is False
