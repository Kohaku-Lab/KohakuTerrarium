"""Turn completion and cancellation boundaries with a real running agent."""

import asyncio

import pytest

from kohakuterrarium import Agent
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.testing.llm import ScriptedLLM


class CleanupLLM(ScriptedLLM):
    def __init__(self):
        super().__init__(["OK"])
        self.started = asyncio.Event()
        self.cancelling = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages, **kwargs):
        if not self.started.is_set():
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelling.set()
                await self.release.wait()
                raise
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


@pytest.fixture
async def running_agent(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "name: cleanup\nsystem_prompt: offline\ninput: {type: none}\noutput: {type: stdout}\n"
    )
    llm = CleanupLLM()
    agent = await Agent.build(str(config), llm=llm, io="headless", pwd=tmp_path)
    await agent.start()
    try:
        yield agent, llm
    finally:
        llm.release.set()
        await agent.stop()


async def test_interrupt_waits_for_cleanup_and_concurrent_edit(running_agent):
    agent, llm = running_agent
    turn = asyncio.create_task(agent.run("original", timeout=5, raise_on_error=False))
    await asyncio.wait_for(llm.started.wait(), 2)
    agent.interrupt()
    await asyncio.wait_for(llm.cancelling.wait(), 2)
    stop = asyncio.create_task(agent.interrupt_and_wait())
    edit = asyncio.create_task(agent.edit_and_rerun(1, "edited"))
    await asyncio.sleep(0)
    assert not stop.done()
    assert not edit.done()
    assert agent.is_processing
    llm.release.set()
    await asyncio.wait_for(stop, 2)
    await asyncio.wait_for(turn, 2)
    assert await asyncio.wait_for(edit, 2) is True
    assert not agent.is_processing
    assert llm.last_user_message == "edited"
    assert agent.controller.conversation.get_messages()[-1].content == "OK"


async def test_interrupt_timeout_preserves_cleanup_and_accepts_retry(running_agent):
    agent, llm = running_agent
    turn = asyncio.create_task(agent.run("original", timeout=5, raise_on_error=False))
    await asyncio.wait_for(llm.started.wait(), 2)
    with pytest.raises(ConflictError, match="still finishing"):
        await agent.interrupt_and_wait(timeout=0.01)
    assert llm.cancelling.is_set()
    completion = agent._turn_completion
    assert completion is not None and not completion.done()
    assert agent.is_processing
    retry = asyncio.create_task(agent.interrupt_and_wait())
    await asyncio.sleep(0)
    assert not retry.done()
    assert not completion.cancelled()
    llm.release.set()
    await asyncio.wait_for(retry, 2)
    assert (await asyncio.wait_for(turn, 2)).status == "interrupted"
    assert not agent.is_processing
    assert (await agent.run("follow up", timeout=2)).text == "OK"
    assert not agent._interrupt_requested


async def test_active_generation_still_rejects_history_edits(running_agent):
    agent, llm = running_agent
    turn = asyncio.create_task(agent.run("original", timeout=5, raise_on_error=False))
    await asyncio.wait_for(llm.started.wait(), 2)
    with pytest.raises(ConflictError):
        await agent.edit_and_rerun(1, "edited")
    assert llm.last_user_message != "edited"
    llm.release.set()
    await agent.interrupt_and_wait()
    await turn
