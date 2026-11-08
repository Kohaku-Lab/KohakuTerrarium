"""Scripted provider that fails after delivering a partial response."""

import asyncio

from kohakuterrarium.errors import ResponseStreamError
from kohakuterrarium.testing.llm import ScriptedLLM


class InterruptedResponseLLM(ScriptedLLM):
    async def chat(self, messages, **kwargs):
        async for chunk in super().chat(messages, **kwargs):
            yield chunk
        if self.last_user_message == "incomplete response":
            raise ResponseStreamError()


class PausedResponseLLM(ScriptedLLM):
    """Pause after the first partial reply and expose stream closure."""

    def __init__(self, text):
        super().__init__([text, "continued"])
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()
        self.delivered_text = ""

    async def chat(self, messages, **kwargs):
        try:
            async for chunk in super().chat(messages, **kwargs):
                self.delivered_text += chunk
                yield chunk
            if self.call_count == 1:
                self.waiting.set()
                await asyncio.Event().wait()
        finally:
            self.closed.set()
