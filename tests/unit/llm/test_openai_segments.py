"""Unit tests for OpenAIProvider ordered reasoning segments."""

import pytest

from kohakuterrarium.llm.openai import OpenAIProvider


class _Delta:
    def __init__(self, content=None, tool_calls=None, model_extra=None):
        self.content = content
        self.tool_calls = tool_calls
        self.model_extra = model_extra or {}


class _ToolDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = type(
            "Function",
            (),
            {"name": name, "arguments": arguments},
        )()


class _Chunk:
    def __init__(self, delta):
        self.choices = [type("Choice", (), {"delta": delta})()]
        self.usage = None


class _Completions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs

        async def _stream():
            for chunk in self.chunks:
                yield chunk

        return _stream()


class _Client:
    def __init__(self, chunks):
        self.chat = type("Chat", (), {"completions": _Completions(chunks)})()


@pytest.mark.asyncio
async def test_stream_builds_interleaved_reasoning_segments():
    chunks = [
        _Chunk(_Delta(model_extra={"reasoning_content": "think 1"})),
        _Chunk(_Delta(content="answer 1")),
        _Chunk(_Delta(tool_calls=[_ToolDelta(0, id="call_1")])),
        _Chunk(_Delta(tool_calls=[_ToolDelta(0, name="read", arguments='{"path"')])),
        _Chunk(_Delta(tool_calls=[_ToolDelta(0, arguments=': "a.md"}')])),
        _Chunk(_Delta(model_extra={"reasoning_content": "think 2"})),
        _Chunk(_Delta(content="answer 2")),
    ]
    provider = OpenAIProvider(api_key="sk-test", model="gpt-x")
    provider._client = _Client(chunks)

    text = []
    async for piece in provider._raw_stream_chat([{"role": "user", "content": "q"}]):
        text.append(piece)

    assert "".join(text) == "answer 1answer 2"
    assert [tc.id for tc in provider.last_tool_calls] == ["call_1"]
    assert provider._last_assistant_extra_fields == {
        "reasoning_content": "think 1think 2",
        "_kt_assistant_segments": [
            {"type": "reasoning", "source": "reasoning_content", "text": "think 1"},
            {"type": "text", "text": "answer 1"},
            {"type": "tool_call_ref", "call_id": "call_1"},
            {"type": "reasoning", "source": "reasoning_content", "text": "think 2"},
            {"type": "text", "text": "answer 2"},
        ],
    }
