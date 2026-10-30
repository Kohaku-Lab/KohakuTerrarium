"""Sub-agent service forwarding and side-thread message boundaries."""

import asyncio

import pytest

from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.conversation.context_snapshot import ContextSnapshot
from kohakuterrarium.errors import ConflictError, InvalidRequestError
from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_runtime import (
    TerrariumRuntimeAdapter,
)
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.terrarium.remote_service import (
    CreatureNotHostedHere,
    RemoteTerrariumService,
)
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


class _ForkingLLM(ScriptedLLM):
    def fork(self, **kwargs):
        return _ForkingLLM(self.script)


class _Wire:
    def register_app_extension(self, namespace, handler):
        self.handler = handler

    async def request(self, *, to_node, namespace, type, body, timeout):
        assert to_node == "worker"
        return await self.handler(
            AppMessage(
                namespace=namespace,
                type=type,
                body=body,
                sender_node="host",
                request_id=None,
                in_reply_to=None,
            )
        )


@pytest.mark.parametrize("remote", [False, True])
async def test_side_read_send_snapshot_and_task_boundary(tmp_path, remote):
    engine = Terrarium(session_dir=str(tmp_path))
    creature = await engine.add_creature(
        AgentConfig(name="parent", system_prompt="source context"),
        llm=_ForkingLLM(["side answer"]),
        io="headless",
        start=True,
    )
    wire = _Wire()
    TerrariumRuntimeAdapter(engine, wire, node_id="worker")
    service = (
        RemoteTerrariumService(wire, "worker", demux=None)
        if remote
        else LocalTerrariumService(engine)
    )
    manager = creature.agent.subagent_manager
    cid = creature.creature_id
    try:
        snapshot = await service.capture_context(cid)
        assert isinstance(snapshot, ContextSnapshot)
        assert snapshot.to_dict() == creature.capture_context().to_dict()
        assert snapshot.to_dict() == engine.capture_context(cid).to_dict()
        detached = snapshot.messages
        detached.clear()
        assert snapshot.messages == creature.capture_context().messages
        job = await manager.start_side("private question")
        result = await manager.wait_for(job)
        assert result.success, result.error
        read = await service.read_subagent_conversation(cid, job_id=job)
        assert read["can_receive"] is True
        assert read["meta"]["audience"] == "user"
        assert read["meta"]["title"] == "private question"
        assert read["meta"]["status"] == "done"
        assert read["meta"]["source"]["agent"] == "parent"
        assert "private question" in str(read["messages"])
        saved = creature.agent.session_store.find_subagent_run(
            job, parent=manager._parent_name
        )
        creature.agent.session_store.save_subagent(
            manager._parent_name,
            "fork",
            saved["run"],
            {**saved["meta"], "title": "Refined side title", "title_source": "llm"},
        )
        refreshed = await service.read_subagent_conversation(cid, job_id=job)
        assert refreshed["meta"]["title"] == "Refined side title"
        sent = await service.send_to_subagent(cid, "wrong-name", "followup", job_id=job)
        assert sent["job_id"] == job
        saved_run = creature.agent.session_store.find_subagent_run(
            job, parent=manager._parent_name
        )
        during_followup = await service.read_subagent_conversation(
            cid, name="fork", run=saved_run["run"]
        )
        assert during_followup["can_receive"] is False
        await manager.wait_for(job)
        manager._jobs.pop(job)
        persisted = await service.read_subagent_conversation(cid, job_id=job)
        assert persisted["can_receive"] is True
        assert persisted["live"] is False
        assert persisted["meta"]["title"] == "Refined side title"
        assert "followup" in str(persisted["messages"])
        await service.send_to_subagent(cid, "fork", "persisted followup", job_id=job)
        await manager.wait_for(job)
        assert "persisted followup" in str(
            (await service.read_subagent_conversation(cid, job_id=job))["messages"]
        )
        creature.agent.config.fork_enabled = False
        assert (await service.read_subagent_conversation(cid, job_id=job))[
            "can_receive"
        ] is False
        with pytest.raises(InvalidRequestError):
            await service.send_to_subagent(cid, "fork", "disabled", job_id=job)
        creature.agent.config.fork_enabled = True
        task = await manager.spawn("fork", "ordinary task", background=False)
        with pytest.raises(ConflictError):
            await service.send_to_subagent(
                cid, "fork", "not a side thread", job_id=task
            )
        with pytest.raises(InvalidRequestError):
            await service.send_to_subagent(cid, "fork", "  ", job_id=job)
        with pytest.raises(KeyError):
            await service.read_subagent_conversation(cid, job_id="missing")
        with pytest.raises(CreatureNotHostedHere if remote else KeyError):
            await service.read_subagent_conversation("missing", job_id=job)
    finally:
        await engine.shutdown()


async def test_busy_side_never_receives_inbox_message():
    engine = Terrarium()
    creature = await engine.add_creature(
        AgentConfig(name="parent"),
        llm=_ForkingLLM([ScriptEntry(response="answer", delay_per_chunk=0.05)]),
        io="headless",
    )
    service = LocalTerrariumService(engine)
    manager = creature.agent.subagent_manager
    try:
        job = await manager.start_side("question")
        read = await service.read_subagent_conversation(
            creature.creature_id, job_id=job
        )
        assert read["can_receive"] is False
        with pytest.raises(ConflictError):
            await service.send_to_subagent(
                creature.creature_id, "fork", "must not queue", job_id=job
            )
        await asyncio.sleep(0.01)
        assert manager.get_live_subagent(job).is_running
        with pytest.raises(ConflictError):
            await service.send_to_subagent(
                creature.creature_id, "fork", "must not queue"
            )
        await manager.wait_for(job)
        assert "must not queue" not in str(
            manager.get_live_subagent(job).conversation.to_messages()
        )
    finally:
        await engine.shutdown()
