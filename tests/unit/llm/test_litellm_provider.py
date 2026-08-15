"""Unit tests for ``llm/litellm_provider.py`` reasoning-field capture.

LiteLLM is an optional dependency, so the SDK module is replaced with a small
fake that exposes the same ``acompletion`` surface used by the provider.
"""

import importlib
import sys

import pytest


class _FakeLitellm:
    acompletion = None


class _ModelExtra:
    def __init__(self, model_extra=None, model_fields_set=None, **attrs):
        self.model_extra = model_extra
        self.model_fields_set = model_fields_set
        self.__dict__.update(attrs)


class _Delta(_ModelExtra):
    def __init__(self, content=None, tool_calls=None, **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


class _Message(_ModelExtra):
    def __init__(self, content, **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.tool_calls = None


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _Response:
    def __init__(self, message):
        self.choices = [
            type("Choice", (), {"message": message, "finish_reason": "stop"})()
        ]
        self.usage = _Usage()
        self.model = "fake"


_fake_litellm = _FakeLitellm()


@pytest.fixture
def lpm(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm)
    module = importlib.import_module("kohakuterrarium.llm.litellm_provider")
    return module, _fake_litellm


class TestStreamingReasoningCapture:
    async def test_reasoning_fields_packed(self, lpm, monkeypatch):
        module, fake = lpm

        async def _stream():
            yield _Chunk(
                _Delta(
                    model_extra={"reasoning_content": "think "},
                    model_fields_set={"reasoning_content"},
                )
            )
            yield _Chunk(
                _Delta(
                    content="answer",
                    model_extra={"reasoning_content": "hard"},
                    model_fields_set={"reasoning_content"},
                )
            )

        async def acompletion(**params):
            return _stream()

        monkeypatch.setattr(fake, "acompletion", acompletion)
        provider = module.LiteLLMProvider(model="openai/gpt-4o", api_key="k")
        chunks = []
        async for chunk in provider._stream_chat([{"role": "user", "content": "q"}]):
            chunks.append(chunk)

        assert chunks == ["answer"]
        assert provider._last_assistant_extra_fields == {
            "reasoning_content": "think hard"
        }

    async def test_empty_reasoning_not_packed(self, lpm, monkeypatch):
        module, fake = lpm

        async def _stream():
            yield _Chunk(_Delta(content="answer"))

        async def acompletion(**params):
            return _stream()

        monkeypatch.setattr(fake, "acompletion", acompletion)
        provider = module.LiteLLMProvider(model="openai/gpt-4o", api_key="k")
        async for _ in provider._stream_chat([{"role": "user", "content": "q"}]):
            pass

        assert provider._last_assistant_extra_fields == {}


class TestCompleteReasoningCapture:
    async def test_reasoning_fields_packed(self, lpm, monkeypatch):
        module, fake = lpm
        message = _Message(
            content="answer",
            model_extra={"reasoning_content": "private"},
            model_fields_set={"reasoning_content"},
        )

        async def acompletion(**params):
            return _Response(message)

        monkeypatch.setattr(fake, "acompletion", acompletion)
        provider = module.LiteLLMProvider(model="openai/gpt-4o", api_key="k")
        response = await provider._complete_chat([{"role": "user", "content": "q"}])

        assert response.content == "answer"
        assert provider._last_assistant_extra_fields == {"reasoning_content": "private"}
        assert provider._last_usage["prompt_tokens"] == 10
