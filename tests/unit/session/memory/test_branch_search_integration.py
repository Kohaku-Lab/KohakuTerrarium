"""M2 integration: selected branch visibility at the creature and Studio surfaces."""

from pathlib import Path
from types import SimpleNamespace

from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.memory.search import search_session_memory


def _seed(store):
    store.init_meta("s", "agent", "", str(Path(store.path).parent), ["alice"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle-first"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle-second"},
        turn_index=1,
        branch_id=2,
        parent_branch_path=[],
    )
    store.state["alice:branch_view"] = {"1": 1}


async def test_creature_uses_selected_branch_without_reindex(tmp_path):
    store = SessionStore(tmp_path / "live.kohakutr")
    _seed(store)
    agent = SimpleNamespace(
        session_store=store,
        config=SimpleNamespace(name="alice", memory=None),
        _turn_index=1,
        _branch_id=1,
        _parent_branch_path=(),
    )
    ctx = ToolContext(
        agent_name="alice",
        agent=agent,
        session=Session(key="s"),
        working_dir=tmp_path,
    )
    try:
        tool = SearchMemoryTool()
        first = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert first.error is None
        assert "needle-first" in first.output
        assert "needle-second" not in first.output

        memory = await store.run(lambda: store._live_memory._memory)
        generation = await store.run(memory._generation, "alice")
        await store.run(store.state.__setitem__, "alice:branch_view", {"1": 2})
        second = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert second.error is None
        assert "needle-second" in second.output
        assert "needle-first" not in second.output
        assert await store.run(memory._generation, "alice") == generation
        assert await store.run(memory.get_stats) == {
            "fts_blocks": 2,
            "vec_blocks": 0,
            "has_vectors": False,
            "dimensions": 0,
        }
    finally:
        store.close(update_status=False)


async def test_saved_studio_search_obeys_persisted_branch_selection(tmp_path):
    path = tmp_path / "saved.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store)
        store.flush()
    finally:
        store.close(update_status=False)

    first = await search_session_memory(path, q="needle", mode="fts", k=5)
    assert {item["content"] for item in first["results"]} == {"needle-first"}

    store = SessionStore(path)
    try:
        store.state["alice:branch_view"] = {"1": 2}
        store.flush()
    finally:
        store.close(update_status=False)

    second = await search_session_memory(path, q="needle", mode="fts", k=5)
    assert {item["content"] for item in second["results"]} == {"needle-second"}


async def test_creature_explicit_other_agent_is_scoped_to_target_lineage(tmp_path):
    store = SessionStore(tmp_path / "multi-agent.kohakutr")
    store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle-alice"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    store.append_event(
        "bob",
        "user_input",
        {"content": "needle-bob-old"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    store.append_event(
        "bob",
        "user_input",
        {"content": "needle-bob-new"},
        turn_index=1,
        branch_id=2,
        parent_branch_path=[],
    )
    store.state["bob:branch_view"] = {"1": 1}
    ctx = ToolContext(
        agent_name="alice",
        session=Session(key="s"),
        working_dir=tmp_path,
        agent=SimpleNamespace(
            session_store=store,
            config=SimpleNamespace(name="alice", memory=None),
            _turn_index=1,
            _branch_id=1,
            _parent_branch_path=(),
        ),
    )
    try:
        result = await SearchMemoryTool().execute(
            {"query": "needle", "mode": "fts", "agent": "bob"},
            context=ctx,
        )
        assert result.error is None
        assert "needle-bob-old" in result.output
        assert "needle-bob-new" not in result.output
        assert "needle-alice" not in result.output
    finally:
        store.close(update_status=False)
