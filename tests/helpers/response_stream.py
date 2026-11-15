"""Deterministic response streams for incomplete-turn integration tests."""

from kohakuterrarium.errors import ResponseStreamError
from kohakuterrarium.testing.llm import ScriptedLLM


class InterruptedResponseLLM(ScriptedLLM):
    """Deliver one response before reporting an unconfirmed stream outcome."""

    async def chat(self, messages, **kwargs):
        async for chunk in super().chat(messages, **kwargs):
            yield chunk
        if self.last_user_message == "incomplete response":
            raise ResponseStreamError()
