import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kohakuterrarium.llm.anthropic_format import (
    KT_CONTENT_KEY,
    anthropic_tools,
    apply_delta,
    merge_usage,
    ordered_blocks,
    prepare_messages,
    tool_calls_from_blocks,
)
from kohakuterrarium.llm.base import ToolSchema


class DummyAsyncAnthropic:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = SimpleNamespace(create=None)

    async def close(self):
        return None


class DummyOmit:
    pass


@pytest.fixture()
def anthropic_provider():
    with (
        patch(
            "kohakuterrarium.llm.anthropic_provider.AsyncAnthropic",
            DummyAsyncAnthropic,
        ),
        patch("kohakuterrarium.llm.anthropic_provider.HAS_ANTHROPIC", True),
        patch("kohakuterrarium.llm.anthropic_provider.Omit", DummyOmit),
    ):
        from kohakuterrarium.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key="sk-test", model="claude-test")


def test_prepare_messages_moves_system_and_translates_tools():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"a.txt"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": "file text"},
    ]

    system, body = prepare_messages(messages)

    assert system == "SYS"
    assert body[0] == {"role": "user", "content": "hello"}
    assert body[1]["role"] == "assistant"
    assert body[1]["content"][0] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "read",
        "input": {"path": "a.txt"},
    }
    assert body[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file text"}
        ],
    }


def test_prepare_messages_round_trips_anthropic_content_blocks():
    blocks = [
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {"type": "text", "text": "answer"},
    ]

    _, body = prepare_messages(
        [{"role": "assistant", "content": "answer", KT_CONTENT_KEY: blocks}]
    )

    assert body == [{"role": "assistant", "content": blocks}]
    assert body[0]["content"] is not blocks


def test_prepare_messages_filters_stale_native_tool_use_blocks():
    blocks = [
        {"type": "text", "text": "I will inspect the file."},
        {"type": "tool_use", "id": "toolu_keep", "name": "read", "input": {}},
        {"type": "tool_use", "id": "toolu_drop", "name": "bash", "input": {}},
    ]
    message = {
        "role": "assistant",
        "content": "I will inspect the file.",
        KT_CONTENT_KEY: blocks,
        "tool_calls": [
            {
                "id": "toolu_keep",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }
        ],
    }

    _, body = prepare_messages([message])

    assert body == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect the file."},
                {"type": "tool_use", "id": "toolu_keep", "name": "read", "input": {}},
            ],
        }
    ]


def test_prepare_messages_drops_native_tool_use_when_tool_calls_sanitized():
    blocks = [
        {"type": "text", "text": "I will inspect the file."},
        {"type": "tool_use", "id": "toolu_orphan", "name": "read", "input": {}},
    ]
    message = {
        "role": "assistant",
        "content": "I will inspect the file.",
        KT_CONTENT_KEY: blocks,
        "tool_calls": [],
    }

    _, body = prepare_messages([message])

    assert body == [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "I will inspect the file."}],
        }
    ]


def test_anthropic_tools_convert_openai_style_schema():
    schema = ToolSchema(
        name="grep",
        description="Search files",
        parameters={"type": "object", "properties": {"pattern": {"type": "string"}}},
    )

    assert anthropic_tools([schema]) == [
        {
            "name": "grep",
            "description": "Search files",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
            },
        }
    ]


def test_stream_delta_helpers_collect_tool_use():
    blocks = {
        0: {"type": "text", "text": ""},
        1: {"type": "tool_use", "id": "toolu_2", "name": "bash", "input": {}},
    }

    assert apply_delta(blocks[0], SimpleNamespace(type="text_delta", text="hi")) == "hi"
    assert (
        apply_delta(
            blocks[1],
            SimpleNamespace(type="input_json_delta", partial_json='{"command"'),
        )
        == ""
    )
    assert (
        apply_delta(
            blocks[1], SimpleNamespace(type="input_json_delta", partial_json=':"ls"}')
        )
        == ""
    )

    ordered = ordered_blocks(blocks)
    calls = tool_calls_from_blocks(ordered)

    assert ordered[0] == {"type": "text", "text": "hi"}
    assert ordered[1]["input"] == {"command": "ls"}
    assert calls[0].id == "toolu_2"
    assert calls[0].name == "bash"
    assert json.loads(calls[0].arguments) == {"command": "ls"}


def test_build_create_kwargs_maps_extra_body_and_prompt_cache(anthropic_provider):
    anthropic_provider.extra_body = {
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "output_config": {"effort": "high"},
        "extra_headers": {"anthropic-beta": "example-beta"},
        "vendor_field": 1,
    }

    kwargs = anthropic_provider._build_create_kwargs(
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hello"},
        ],
        stream=True,
    )

    assert kwargs["model"] == "claude-test"
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert kwargs["extra_headers"] == {"anthropic-beta": "example-beta"}
    assert kwargs["extra_body"] == {
        "output_config": {"effort": "high"},
        "vendor_field": 1,
    }
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][0]["cache_control"] == {
        "type": "ephemeral"
    }


def test_merge_usage_preserves_streaming_input_tokens():
    start = SimpleNamespace(input_tokens=10, cache_read_input_tokens=5)
    delta = SimpleNamespace(output_tokens=7)

    usage = merge_usage({}, start)
    merged = merge_usage(usage, delta)

    assert merged == {
        "prompt_tokens": 15,
        "completion_tokens": 7,
        "total_tokens": 22,
        "cache_read_input_tokens": 5,
        "input_tokens": 10,
        "output_tokens": 7,
    }


def test_minimax_endpoint_defaults_to_bearer_auth():
    with (
        patch(
            "kohakuterrarium.llm.anthropic_provider.AsyncAnthropic",
            DummyAsyncAnthropic,
        ),
        patch("kohakuterrarium.llm.anthropic_provider.HAS_ANTHROPIC", True),
        patch("kohakuterrarium.llm.anthropic_provider.Omit", DummyOmit),
    ):
        from kohakuterrarium.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key="minimax-key",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/anthropic",
        )

    assert provider.auth_as_bearer is True
    assert provider._client.kwargs["api_key"] is None
    assert provider._client.kwargs["auth_token"] == "minimax-key"
    assert "X-Api-Key" in provider._client.kwargs["default_headers"]


def test_inline_provider_anthropic_keeps_openai_compat_without_auth_mode():
    from kohakuterrarium.bootstrap.llm import _create_from_inline
    from kohakuterrarium.core.config_types import AgentConfig

    config = AgentConfig(
        name="test-agent",
        model="claude-openai-compat",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com/v1",
        auth_mode="api-key",
    )

    with (
        patch.object(config, "get_api_key", return_value="key"),
        patch("kohakuterrarium.bootstrap.llm.OpenAIProvider") as mock_openai,
        patch("kohakuterrarium.bootstrap.llm.AnthropicProvider") as mock_anthropic,
    ):
        result = _create_from_inline(config)

    assert result is mock_openai.return_value
    mock_anthropic.assert_not_called()
    mock_openai.assert_called_once_with(
        api_key="key",
        base_url="https://api.anthropic.com/v1",
        model="claude-openai-compat",
        temperature=0.7,
        max_tokens=None,
        extra_body=None,
        retry_policy=None,
    )


def test_inline_auth_mode_anthropic_uses_native_provider():
    from kohakuterrarium.bootstrap.llm import _create_from_inline
    from kohakuterrarium.core.config_types import AgentConfig

    config = AgentConfig(
        name="test-agent",
        model="claude-native",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com",
        auth_mode="anthropic",
    )

    with (
        patch.object(config, "get_api_key", return_value="key"),
        patch("kohakuterrarium.bootstrap.llm.OpenAIProvider") as mock_openai,
        patch("kohakuterrarium.bootstrap.llm.AnthropicProvider") as mock_anthropic,
    ):
        result = _create_from_inline(config)

    assert result is mock_anthropic.return_value
    mock_openai.assert_not_called()
    mock_anthropic.assert_called_once_with(
        api_key="key",
        base_url="https://api.anthropic.com",
        model="claude-native",
        temperature=0.7,
        max_tokens=None,
        extra_body=None,
        service_tier=None,
        retry_policy=None,
    )


def test_bootstrap_uses_anthropic_provider_for_anthropic_profile():
    from kohakuterrarium.bootstrap.llm import _create_from_profile
    from kohakuterrarium.llm.profile_types import LLMProfile

    profile = LLMProfile(
        name="mini",
        provider="minimax-anthropic",
        backend_type="anthropic",
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/anthropic",
        api_key_env="MINIMAX_API_KEY",
        max_output=2048,
    )

    with (
        patch("kohakuterrarium.bootstrap.llm.AnthropicProvider") as mock_anthropic,
        patch("kohakuterrarium.bootstrap.llm.get_api_key", return_value="key"),
    ):
        instance = mock_anthropic.return_value
        result = _create_from_profile(profile)

    assert result is instance
    mock_anthropic.assert_called_once_with(
        api_key="key",
        base_url="https://api.minimax.io/anthropic",
        model="MiniMax-M2.7",
        temperature=None,
        max_tokens=2048,
        extra_body=None,
        service_tier=None,
        retry_policy=None,
    )


def test_builtin_anthropic_backend_is_native():
    from kohakuterrarium.llm.backends import (
        legacy_provider_from_data,
        load_backends,
        validate_backend_type,
    )

    backend = load_backends()["anthropic"]

    assert backend.backend_type == "anthropic"
    assert backend.base_url == "https://api.anthropic.com"
    assert validate_backend_type("anthropic") == "anthropic"
    assert (
        legacy_provider_from_data({"base_url": "https://api.anthropic.com/v1/"})
        == "anthropic"
    )
