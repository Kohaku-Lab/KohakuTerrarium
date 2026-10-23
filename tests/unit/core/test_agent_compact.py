"""Compact metadata restoration across real agent resume/start/attach lifetimes."""

import gc
import time
from weakref import ref

import pytest

import kohakuterrarium.core.agent_compact as compact_mod
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_message_history import replay_with_settled_tools
from kohakuterrarium.core.branch_continuation import persist_branch_view
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.session.resume import inject_saved_state
from kohakuterrarium.session.resume_build import build_conversation
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.fixture(autouse=True)
def compact_llm(monkeypatch):
    monkeypatch.setattr(
        compact_mod, "create_llm_from_profile_name", lambda _: ScriptedLLM(["summary"])
    )


class _CountingStore(SessionStore):
    def __init__(self, path):
        super().__init__(str(path))
        self.event_reads = 0

    def get_events(self, agent, since_event_id=None):
        self.event_reads += 1
        return super().get_events(agent, since_event_id)


def _save_history(
    store,
    *,
    namespace="alice",
    kind="branch",
    count=4,
    cooldown=0.0,
    event_type="user_message",
):
    messages = [{"role": "system", "content": "test"}]
    messages.append(
        {
            "role": "assistant",
            "content": f"[Previous context summary (compact round {count})]\nsummary",
        }
    )
    for turn in range(1, 5):
        store.append_event(
            namespace,
            event_type,
            {"content": f"question {turn}"},
            turn_index=turn,
            branch_id=1,
            parent_branch_path=[(t, 1) for t in range(1, turn)],
        )
        messages.extend(
            [
                {
                    "role": "user",
                    "content": f"question {turn}",
                    "metadata": {"turn_index": turn, "branch_id": 1},
                },
                {"role": "assistant", "content": f"answer {turn}"},
            ]
        )
    store.save_conversation(namespace, messages)
    store.save_state(namespace, compact_count=count)
    store.state[f"{namespace}:last_compact_time"] = cooldown
    if kind == "branch":
        store.save_branch_snapshot(
            namespace,
            messages,
            branch={
                "turn_index": 4,
                "branch_id": 1,
                "parent_branch_path": [[t, 1] for t in range(1, 4)],
            },
            watermark=store.max_event_id(namespace),
            compact_state={"compact_count": count, "last_compact_time": cooldown},
        )
    elif kind == "empty":
        store.state[f"{namespace}:branch_snapshots"] = []
    else:
        store.state.pop(f"{namespace}:branch_snapshots", None)


async def _agent():
    return await Agent.build(
        AgentConfig(
            name="alice",
            include_tools_in_prompt=False,
            include_hints_in_prompt=False,
            compact={
                "cooldown_seconds": 30,
                "keep_recent_turns": 1,
                "compact_model": "test-compact",
            },
        ),
        llm=ScriptedLLM(["continued"]),
        io="headless",
    )


@pytest.mark.parametrize("kind", ["branch", "empty", "legacy"])
async def test_detached_start_and_repeat_attach_reuse_compact_metadata(tmp_path, kind):
    store = _CountingStore(tmp_path / "resume.kohakutr")
    cooldown = time.time()
    _save_history(store, kind=kind, cooldown=cooldown)
    agent = await _agent()
    try:
        inject_saved_state(agent, store, "alice")
        assert store.event_reads == 1
        await agent.start()
        manager = agent.compact_manager
        assert manager._compact_count == 4
        assert manager._last_compact_time == (0.0 if kind == "empty" else cooldown)
        if kind != "empty":
            assert not manager.should_compact(manager.config.max_tokens)
        for _ in range(2):
            agent.attach_session_store(store)
            assert manager._compact_count == 4
            assert manager._last_compact_time == (0.0 if kind == "empty" else cooldown)
        assert store.event_reads == 1
    finally:
        await agent.stop()
        store.close()


async def test_live_compaction_before_attach_keeps_new_round_and_cooldown(tmp_path):
    store = _CountingStore(tmp_path / "resume.kohakutr")
    _save_history(store)
    agent = await _agent()
    try:
        inject_saved_state(agent, store, "alice")
        await agent.start()
        manager = agent.compact_manager
        manager._llm = ScriptedLLM(["A new context summary."])
        assert manager.trigger_compact()
        await manager.wait_for_current()
        assert manager._compact_count == 5
        cooldown = manager._last_compact_time
        assert cooldown > 0
        assert (await agent.run("a new descendant turn")).text == "continued"
        for _ in range(2):
            agent.attach_session_store(store)
            assert manager._compact_count == 5
            assert manager._last_compact_time == cooldown
            assert not manager.should_compact(manager.config.max_tokens)
        assert store.event_reads == 1
    finally:
        await agent.stop()
        store.close()


async def test_unprepared_exact_checkpoint_preserves_count_and_cooldown(tmp_path):
    store = _CountingStore(tmp_path / "resume.kohakutr")
    cooldown = time.time()
    _save_history(store, cooldown=cooldown, event_type="user_input")
    messages = build_conversation(
        replay_with_settled_tools(
            store.get_events("alice"), include_metadata=True, resumable=True
        )
    ).snapshot_messages(preserve_pending_tail=True)
    store.save_branch_snapshot(
        "alice",
        messages,
        branch={
            "turn_index": 4,
            "branch_id": 1,
            "parent_branch_path": [[t, 1] for t in range(1, 4)],
        },
        watermark=store.max_event_id("alice"),
        compact_state={"compact_count": 4, "last_compact_time": cooldown},
    )
    store.event_reads = 0
    agent = await _agent()
    try:
        inject_saved_state(agent, store, "alice")
        await agent.start()
        agent.attach_session_store(store)
        assert agent.compact_manager._compact_count == 4
        assert agent.compact_manager._last_compact_time == cooldown
        assert store.event_reads == 1
    finally:
        await agent.stop()
        store.close()


@pytest.mark.parametrize(
    "change",
    [
        "store",
        "namespace",
        "conversation",
        "branch",
        "persisted",
        "manager",
        "reinject",
    ],
)
@pytest.mark.parametrize("started", [False, True])
async def test_resume_handoff_does_not_outlive_its_context(tmp_path, change, started):
    store = _CountingStore(tmp_path / "resume.kohakutr")
    _save_history(store, kind="legacy", cooldown=11.0)
    agent = await _agent()
    other_store = None
    try:
        inject_saved_state(agent, store, "alice")
        if started:
            await agent.start()
        store.state["alice:compact_count"] = 8
        store.state["alice:last_compact_time"] = 22.0
        target = store
        match change:
            case "store":
                store.flush()
                other_store = _CountingStore(store.path)
                target = other_store
            case "namespace":
                agent.config.name = "bob"
                store.state["bob:compact_count"] = 8
                store.state["bob:last_compact_time"] = 22.0
            case "conversation":
                old_conversation = ref(agent.controller.conversation)
                agent.controller.conversation = Conversation()
                gc.collect()
                assert old_conversation() is None
            case "branch":
                agent._branch_id = 2
            case "persisted":
                persist_branch_view(store, "alice", {4: 2})
            case "manager":
                agent._init_compact_manager()
            case "reinject":
                inject_saved_state(agent, store, "alice")
        agent.attach_session_store(target)
        if not started:
            await agent.start()
        assert agent.compact_manager._compact_count == 8
        assert agent.compact_manager._last_compact_time == 22.0
    finally:
        await agent.stop()
        if other_store is not None:
            other_store.close()
        store.close()


async def test_restart_loads_new_metadata_for_the_new_manager(tmp_path):
    store = _CountingStore(tmp_path / "resume.kohakutr")
    _save_history(store, kind="legacy", cooldown=11.0)
    agent = await _agent()
    try:
        inject_saved_state(agent, store, "alice")
        agent.attach_session_store(store)
        await agent.start()
        first_manager = ref(agent.compact_manager)
        assert agent.compact_manager._compact_count == 4
        await agent.stop()
        store.state["alice:compact_count"] = 8
        store.state["alice:last_compact_time"] = 22.0
        await agent.start()
        assert first_manager() is not agent.compact_manager
        agent.attach_session_store(store)
        assert agent.compact_manager._compact_count == 8
        assert agent.compact_manager._last_compact_time == 22.0
    finally:
        await agent.stop()
        store.close()


@pytest.mark.parametrize("message_idx", [0, 1])
async def test_rewind_to_empty_context_restores_reset_compact_state(
    tmp_path, message_idx
):
    store = _CountingStore(tmp_path / "resume.kohakutr")
    _save_history(store, cooldown=11.0)
    agent = await _agent()
    try:
        inject_saved_state(agent, store, "alice")
        agent.attach_session_store(store)
        await agent.start()
        manager = agent.compact_manager
        assert (manager._compact_count, manager._last_compact_time) == (4, 11.0)
        await agent.rewind_to(message_idx)
        assert all(
            message.role == "system"
            for message in agent.controller.conversation.get_messages()
        )
        assert store.state["alice:compact_count"] == 0
        assert store.state["alice:last_compact_time"] == 0.0
        for _ in range(2):
            agent.attach_session_store(store)
            assert (manager._compact_count, manager._last_compact_time) == (0, 0.0)
        assert store.event_reads == 1
    finally:
        await agent.stop()
        store.close()
