"""Scripted provider that fails after delivering a partial response."""

from kohakuterrarium.errors import ResponseStreamError
from kohakuterrarium.testing.llm import ScriptedLLM


class InterruptedResponseLLM(ScriptedLLM):
    async def chat(self, messages, **kwargs):
        async for chunk in super().chat(messages, **kwargs):
            yield chunk
        if self.last_user_message == "incomplete response":
            raise ResponseStreamError()
