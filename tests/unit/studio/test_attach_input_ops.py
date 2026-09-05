"""Attach terminal notices during a real turn handoff."""

import asyncio

from kohakuterrarium import Agent
from kohakuterrarium.studio.attach.input_ops import _process_input
from kohakuterrarium.testing.llm import ScriptedLLM


class HandoffLLM(ScriptedLLM):
    def __init__(self):
        super().__init__(["OK"])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.followup_started = asyncio.Event()
        self.followup_release = asyncio.Event()

    async def chat(self, messages, **kwargs):
        if not self.started.is_set():
            self.started.set()
            await self.release.wait()
        else:
            self.followup_started.set()
            await self.followup_release.wait()
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


async def test_previous_input_cannot_mark_a_new_turn_idle(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("name: handoff\nsystem_prompt: offline\ninput: {type: none}\n")
    llm = HandoffLLM()
    agent = await Agent.build(str(config), llm=llm, io="headless", pwd=tmp_path)
    await agent.start()
    queue = asyncio.Queue()
    first = asyncio.create_task(_process_input(agent, "first", queue, "handoff", "p1"))
    try:
        await asyncio.wait_for(llm.started.wait(), 2)
        second = asyncio.create_task(agent.run("second", timeout=5))
        # Let the awaiting input reach the inbox while the first turn is gated.
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(agent._event_inbox) == 1
        agent.interrupt()
        llm.release.set()
        await asyncio.wait_for(llm.followup_started.wait(), 2)
        await asyncio.wait_for(first, 2)
        assert agent.is_processing
        assert queue.empty(), "An old input emitted idle during the next turn"
        llm.followup_release.set()
        assert (await second).text == "OK"
    finally:
        llm.release.set()
        llm.followup_release.set()
        await agent.stop()
