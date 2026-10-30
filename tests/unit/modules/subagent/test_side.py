"""Context forks and user-only side conversation lifecycle."""

import asyncio
from contextlib import closing

from kohakuterrarium import Agent
from kohakuterrarium.builtins.user_commands.btw import BtwCommand
from kohakuterrarium.core.config.types import AgentConfig, ToolConfigItem
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


class ReadingForkLLM(ScriptedLLM):
    def fork(self):
        return ScriptedLLM(self.script)


async def test_fork_read_tracking_does_not_authorize_parent_writes(tmp_path):
    target = tmp_path / "private.txt"
    target.write_text("private evidence", encoding="utf-8")
    agent = await Agent.build(
        AgentConfig(
            name="parent", tools=[ToolConfigItem(name="read")], tool_format="bracket"
        ),
        llm=ReadingForkLLM([f"[/read]\n@@path={target}\n[read/]", "Checked."]),
        io="headless",
        pwd=tmp_path,
    )
    try:
        job_id = await agent.subagent_manager.start_side("Read private.txt")
        result = await agent.subagent_manager.wait_for(job_id)
        assert result.success
        child = agent.subagent_manager._jobs[job_id].subagent
        assert "private evidence" in str(child.conversation.to_messages())
        assert agent._file_read_state.get(str(target)) is None
    finally:
        await agent.stop()


class BlockingParentLLM(ScriptedLLM):
    def __init__(self):
        super().__init__(["Parent complete."])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def fork(self):
        return ForkingLLM(["Side complete."])

    async def chat(self, messages, **kwargs):
        self.entered.set()
        await self.release.wait()
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


async def test_btw_finishes_while_parent_remains_busy(tmp_path):
    provider = BlockingParentLLM()
    agent = await Agent.build(
        AgentConfig(name="parent"), llm=provider, io="headless", pwd=tmp_path
    )
    await agent.start()
    task = asyncio.create_task(agent.run("Parent task", timeout=30))
    try:
        await provider.entered.wait()
        before = agent.capture_context().messages
        command = await BtwCommand().execute(
            "Side question", UserCommandContext(agent=agent)
        )
        assert command.success
        result = await agent.subagent_manager.wait_for(command.data["job_id"])
        assert result.output == "Side complete."
        assert not task.done()
        assert agent.capture_context().messages == before
        provider.release.set()
        assert (await task).text == "Parent complete."
    finally:
        provider.release.set()
        await task
        await agent.stop()


class ForkingLLM(ScriptedLLM):
    def fork(self):
        return ForkingLLM(
            [
                ScriptEntry(
                    "Side answer.",
                    usage={
                        "total_tokens": 5,
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                    },
                )
            ]
        )


async def test_default_fork_captures_context_and_btw_does_not_mutate_parent(tmp_path):
    agent = await Agent.build(
        AgentConfig(name="parent"),
        llm=ForkingLLM(["Parent answer."]),
        io="headless",
        pwd=tmp_path,
    )
    try:
        assert "fork" in agent.registry.list_subagents()
        agent.controller.conversation.append("user", "Remember the blue bicycle.")
        before = agent.capture_context().messages
        result = await BtwCommand().execute(
            "What bicycle?", UserCommandContext(agent=agent)
        )
        assert result.success
        assert result.data["title"] == "What bicycle?"
        assert result.data["status"] == "running"
        job_id = result.data["job_id"]
        child_result = await agent.subagent_manager.wait_for(job_id)
        assert child_result.output == "Side answer."
        assert agent.capture_context().messages == before
        child = agent.subagent_manager._jobs[job_id].subagent
        assert child.llm is not agent.llm
        assert "blue bicycle" in str(child.conversation.to_messages())
        assert child._audience == "user"
        assert child.iteration_budget is None
        assert agent.executor.job_store.get_result(job_id) is None
        assert agent.executor.job_store.get_status(job_id).preview == ""
        opened = await BtwCommand().execute(
            f"--open {job_id}", UserCommandContext(agent=agent)
        )
        assert "Side answer." in opened.output
        assert "blue bicycle" not in opened.output
        assert "Private context fork instructions" not in opened.output
        follow = await BtwCommand().execute(
            f"--continue {job_id} What color?", UserCommandContext(agent=agent)
        )
        assert follow.success
        await agent.subagent_manager.wait_for(job_id)
        conversation, meta = agent.subagent_manager.side_conversation(job_id)
        assert "What bicycle?" in str(conversation.to_messages())
        assert "What color?" in str(conversation.to_messages())
        assert agent.capture_context().messages == before
        assert meta["inherited_message_count"] == len(before)
        assert meta["title"] == "What bicycle?"
        assert meta["status"] == "done"
        assert meta["side_turn"] == 2
    finally:
        await agent.stop()


async def test_opt_out_and_nonforking_provider_fail_cleanly(tmp_path):
    for enabled in (False, True):
        agent = await Agent.build(
            AgentConfig(name="parent", fork_enabled=enabled),
            llm=ScriptedLLM(["unused"]),
            io="headless",
            pwd=tmp_path,
        )
        try:
            assert ("fork" in agent.registry.list_subagents()) is enabled
            result = await BtwCommand().execute(
                "question", UserCommandContext(agent=agent)
            )
            assert not result.success
            assert ("isolated" if enabled else "disabled") in result.error
            assert agent.llm.call_count == 0
        finally:
            await agent.stop()


async def test_side_saved_context_can_continue_after_new_parent_instance(tmp_path):
    with closing(SessionStore(str(tmp_path / "side.kohakutr"))) as store:
        agent = await Agent.build(
            AgentConfig(name="parent"), llm=ForkingLLM(), io="headless", pwd=tmp_path
        )
        agent.attach_session_store(store)
        try:
            job_id = await agent.subagent_manager.start_side("first question")
            await agent.subagent_manager.wait_for(job_id)
            row = store.find_subagent_run(job_id, parent="parent")
            assert row["meta"]["task"] == "first question"
            assert row["meta"]["audience"] == "user"
            assert row["meta"]["title"] == "first question"
            store.save_subagent(
                parent="parent",
                name="fork",
                run=row["run"],
                meta={
                    **row["meta"],
                    "title": "A meaningful summary",
                    "title_source": "llm",
                },
            )
            agent.subagent_manager._jobs.clear()
            agent.subagent_manager._tasks.clear()
            await agent.subagent_manager.continue_side(job_id, "follow-up")
            await agent.subagent_manager.wait_for(job_id)
            conversation, meta = agent.subagent_manager.side_conversation(job_id)
            assert "first question" in str(conversation.to_messages())
            assert "follow-up" in str(conversation.to_messages())
            assert meta["run"] == row["run"]
            assert meta["title"] == "A meaningful summary"
            assert meta["total_tokens"] == 10
            assert (
                store.find_subagent_run(job_id, parent="parent")["meta"]["total_tokens"]
                == 10
            )
        finally:
            await agent.stop()
