"""Tests for LiteLLM provider."""

import ast
import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kohakuterrarium.llm.base import (
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)

PROVIDER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kohakuterrarium"
    / "llm"
    / "litellm_provider.py"
)
FACTORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kohakuterrarium"
    / "bootstrap"
    / "llm.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_response(text_chunks, tool_call_deltas=None):
    """Build an async generator that mimics litellm streaming."""

    async def _gen():
        for i, text in enumerate(text_chunks):

            class _Delta:
                content = text
                tool_calls = None

            if tool_call_deltas and i < len(tool_call_deltas):
                _Delta.tool_calls = tool_call_deltas[i]

            class _Choice:
                delta = _Delta()
                finish_reason = None

            class _Chunk:
                choices = [_Choice()]

            yield _Chunk()

    return _gen()


def _make_complete_response(content, tool_calls=None, usage=None):
    """Build a fake non-streaming response."""

    class _Message:
        pass

    msg = _Message()
    msg.content = content
    msg.tool_calls = tool_calls

    class _Choice:
        message = msg
        finish_reason = "stop"

    class _Usage:
        prompt_tokens = (usage or {}).get("prompt_tokens", 10)
        completion_tokens = (usage or {}).get("completion_tokens", 5)
        total_tokens = (usage or {}).get("total_tokens", 15)

    class _Response:
        choices = [_Choice()]

    resp = _Response()
    resp.usage = _Usage()
    resp.model = "test-model"
    return resp


async def _consume_stream(stream):
    """Consume an async iterator and return collected chunks."""
    chunks = []
    async for chunk in stream:
        if chunk:
            chunks.append(chunk)
    return chunks


@pytest.fixture()
def provider():
    from kohakuterrarium.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(model="anthropic/claude-sonnet-4-5")


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


class TestLiteLLMProviderStructure:
    def _parse(self):
        return ast.parse(PROVIDER_PATH.read_text())

    def test_file_exists(self):
        assert PROVIDER_PATH.exists()

    def test_has_litellm_provider_class(self):
        tree = self._parse()
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "LiteLLMProvider" in classes

    def test_inherits_base_llm_provider(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMProvider":
                base_names = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_names.append(base.id)
                assert "BaseLLMProvider" in base_names
                return
        pytest.fail("LiteLLMProvider class not found")

    def test_has_stream_chat(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMProvider":
                methods = [
                    n.name for n in node.body if isinstance(n, ast.AsyncFunctionDef)
                ]
                assert "_stream_chat" in methods
                assert "_complete_chat" in methods
                return

    def test_has_with_model(self):
        src = PROVIDER_PATH.read_text()
        assert "def with_model" in src

    def test_uses_drop_params_true(self):
        src = PROVIDER_PATH.read_text()
        assert '"drop_params": True' in src or "'drop_params': True" in src

    def test_uses_litellm_acompletion(self):
        src = PROVIDER_PATH.read_text()
        assert "litellm.acompletion" in src

    def test_provider_name_is_litellm(self):
        src = PROVIDER_PATH.read_text()
        assert "provider_name" in src
        assert '"litellm"' in src


class TestFactoryRegistration:
    def test_litellm_imported_in_factory(self):
        src = FACTORY_PATH.read_text()
        assert "LiteLLMProvider" in src

    def test_litellm_backend_type_branch(self):
        src = FACTORY_PATH.read_text()
        assert '"litellm"' in src


# ---------------------------------------------------------------------------
# Streaming text
# ---------------------------------------------------------------------------


class TestStreamChat:
    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_stream_yields_text_chunks(self, mock_litellm, provider):
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_stream_response(["Hello", " world", "!"])
        )

        async def _run():
            chunks = []
            async for chunk in provider._stream_chat(
                [{"role": "user", "content": "hi"}]
            ):
                chunks.append(chunk)
            return chunks

        result = asyncio.run(_run())
        assert result == ["Hello", " world", "!"]

    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_stream_passes_drop_params(self, mock_litellm, provider):
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_stream_response(["ok"])
        )

        asyncio.run(
            _consume_stream(
                provider._stream_chat([{"role": "user", "content": "hi"}])
            )
        )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["drop_params"] is True
        assert call_kwargs["stream"] is True


# ---------------------------------------------------------------------------
# Non-streaming completion
# ---------------------------------------------------------------------------


class TestCompleteChat:
    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_complete_returns_chat_response(self, mock_litellm, provider):
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_complete_response("Hello world")
        )

        result = asyncio.run(
            provider._complete_chat([{"role": "user", "content": "hi"}])
        )

        assert isinstance(result, ChatResponse)
        assert result.content == "Hello world"
        assert result.finish_reason == "stop"

    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_complete_extracts_usage(self, mock_litellm, provider):
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_complete_response(
                "ok",
                usage={
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            )
        )

        result = asyncio.run(
            provider._complete_chat([{"role": "user", "content": "hi"}])
        )

        assert result.usage["prompt_tokens"] == 20
        assert result.usage["completion_tokens"] == 10
        assert result.usage["total_tokens"] == 30


# ---------------------------------------------------------------------------
# Native tool calling
# ---------------------------------------------------------------------------


class TestNativeToolCalling:
    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_complete_extracts_tool_calls(self, mock_litellm, provider):
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "get_weather"
        tc.function.arguments = '{"city": "Tokyo"}'

        mock_litellm.acompletion = AsyncMock(
            return_value=_make_complete_response("", tool_calls=[tc])
        )

        asyncio.run(
            provider._complete_chat(
                [{"role": "user", "content": "weather?"}]
            )
        )

        assert len(provider.last_tool_calls) == 1
        assert provider.last_tool_calls[0].name == "get_weather"
        assert provider.last_tool_calls[0].arguments == '{"city": "Tokyo"}'
        assert provider.last_tool_calls[0].id == "call_123"

    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_stream_assembles_tool_call_deltas(self, mock_litellm, provider):
        delta1 = MagicMock()
        delta1.index = 0
        delta1.id = "call_456"
        delta1.function = MagicMock()
        delta1.function.name = "search"
        delta1.function.arguments = '{"q":'

        delta2 = MagicMock()
        delta2.index = 0
        delta2.id = None
        delta2.function = MagicMock()
        delta2.function.name = None
        delta2.function.arguments = ' "test"}'

        mock_litellm.acompletion = AsyncMock(
            return_value=_make_stream_response(
                [None, None],
                tool_call_deltas=[[delta1], [delta2]],
            )
        )

        asyncio.run(
            _consume_stream(
                provider._stream_chat(
                    [{"role": "user", "content": "search"}]
                )
            )
        )

        assert len(provider.last_tool_calls) == 1
        assert provider.last_tool_calls[0].name == "search"
        assert provider.last_tool_calls[0].arguments == '{"q": "test"}'

    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_tools_passed_to_acompletion(self, mock_litellm, provider):
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_stream_response(["ok"])
        )
        tools = [
            ToolSchema(
                name="get_time",
                description="Get current time",
                parameters={"type": "object", "properties": {}},
            )
        ]

        asyncio.run(
            _consume_stream(
                provider._stream_chat(
                    [{"role": "user", "content": "time?"}], tools=tools
                )
            )
        )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert "tools" in call_kwargs
        assert call_kwargs["tool_choice"] == "auto"


# ---------------------------------------------------------------------------
# Multimodal messages
# ---------------------------------------------------------------------------


class TestMultimodal:
    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_multimodal_messages_passed_through(self, mock_litellm, provider):
        """Multimodal messages (image content parts) flow to litellm unchanged."""
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_stream_response(["An image of a cat."])
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBOR..."
                        },
                    },
                ],
            }
        ]

        asyncio.run(_consume_stream(provider._stream_chat(messages)))

        call_kwargs = mock_litellm.acompletion.call_args[1]
        sent_messages = call_kwargs["messages"]
        assert len(sent_messages) == 1
        assert isinstance(sent_messages[0]["content"], list)
        assert sent_messages[0]["content"][0]["type"] == "text"
        assert sent_messages[0]["content"][1]["type"] == "image_url"


# ---------------------------------------------------------------------------
# State-machine text tool format compatibility
# ---------------------------------------------------------------------------


class TestStateMachineToolFormat:
    @patch("kohakuterrarium.llm.litellm_provider.litellm")
    def test_stream_yields_raw_text_for_parser(self, mock_litellm, provider):
        """When the LLM outputs text-based tool calls (##tool##...##tool##),
        the provider yields raw text that the framework's state-machine parser
        can process."""
        tool_text = [
            "Let me ",
            "check. ",
            "##tool##\n",
            "bash\n",
            "ls -la\n",
            "##tool##",
        ]
        mock_litellm.acompletion = AsyncMock(
            return_value=_make_stream_response(tool_text)
        )

        async def _run():
            chunks = []
            async for chunk in provider._stream_chat(
                [{"role": "user", "content": "list files"}]
            ):
                chunks.append(chunk)
            return chunks

        result = asyncio.run(_run())
        full_text = "".join(result)
        assert "##tool##" in full_text
        assert "bash" in full_text


# ---------------------------------------------------------------------------
# with_model
# ---------------------------------------------------------------------------


class TestWithModel:
    def test_with_model_returns_new_provider(self, provider):
        new = provider.with_model("openai/gpt-4o")
        assert new is not provider
        assert new.config.model == "openai/gpt-4o"

    def test_with_model_same_name_returns_self(self, provider):
        same = provider.with_model("anthropic/claude-sonnet-4-5")
        assert same is provider

    def test_with_model_preserves_config(self):
        from kohakuterrarium.llm.litellm_provider import LiteLLMProvider

        p = LiteLLMProvider(
            model="openai/gpt-4o",
            config=LLMConfig(
                model="openai/gpt-4o", temperature=0.5, max_tokens=200
            ),
        )
        new = p.with_model("anthropic/claude-sonnet-4-5")
        assert new.config.temperature == 0.5
        assert new.config.max_tokens == 200


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


class TestDependency:
    def test_litellm_in_pyproject_optional_deps(self):
        pyproject = (
            Path(__file__).resolve().parents[2] / "pyproject.toml"
        ).read_text()
        assert "litellm" in pyproject
