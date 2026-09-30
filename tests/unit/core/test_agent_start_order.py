"""Agent.start(): plugins are loaded before the first queued event is processed."""

import asyncio

from kohakuterrarium import Agent
from kohakuterrarium.core.event_inbox import EventEnvelope
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.testing.llm import ScriptedLLM


class RecordingPlugin(BasePlugin):
    name = "recording"

    def __init__(self, log: list[str]):
        super().__init__()
        self.log = log

    async def on_load(self, context) -> None:
        self.log.append("load:begin")
        await asyncio.sleep(0.05)
        self.log.append("load:end")

    async def pre_llm_call(self, messages, **kwargs):
        self.log.append("llm")
        return None


async def test_event_queued_before_start_waits_for_plugins_to_load(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "name: order\nsystem_prompt: offline\ninput: {type: none}\noutput: {type: stdout}\n"
    )
    log: list[str] = []
    agent = await Agent.build(
        str(config),
        llm=ScriptedLLM(["done"]),
        io="headless",
        pwd=tmp_path,
        plugins=[RecordingPlugin(log)],
    )
    agent._event_inbox.put(EventEnvelope(create_user_input_event("hello")))

    await agent.start()
    try:
        for _ in range(100):
            if "llm" in log:
                break
            await asyncio.sleep(0.01)
    finally:
        await agent.stop()

    assert log[:3] == ["load:begin", "load:end", "llm"]
