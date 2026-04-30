"""Reasoning-field passthrough — capture, echo, persistence, opt-out.

Covers the stateful-chain reasoning story used by DeepSeek V4, MiMo V2.5
(OpenRouter), Qwen, Grok, and other OpenAI-compat backends that add
non-standard assistant fields (``reasoning_content`` /
``reasoning_details`` / ``reasoning``) and expect them echoed back on
the next turn.
"""

import pytest

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.message import AssistantMessage, Message
from kohakuterrarium.llm.openai import (
    OpenAIProvider,
    _delta_field,
    _pack_reasoning_fields,
)
from kohakuterrarium.llm.openai_helpers import normalize_stateful_assistant_fields

# ───────────────────────────── Message shape ─────────────────────────────


def test_to_dict_echoes_extra_fields():
    msg = AssistantMessage(
        "reply",
        extra_fields={
            "reasoning_content": "step-by-step thinking",
            "reasoning_details": [{"type": "reasoning.text", "text": "..."}],
        },
    )
    payload = msg.to_dict()
    assert payload["content"] == "reply"
    assert payload["reasoning_content"] == "step-by-step thinking"
    assert payload["reasoning_details"] == [{"type": "reasoning.text", "text": "..."}]


def test_from_dict_captures_non_standard_fields_into_extras():
    payload = {
        "role": "assistant",
        "content": "reply",
        "reasoning_content": "hidden reasoning",
        "reasoning_details": [{"type": "reasoning.text", "text": "x"}],
        "custom_future_field": 42,
    }
    msg = Message.from_dict(payload)
    assert msg.extra_fields == {
        "reasoning_content": "hidden reasoning",
        "reasoning_details": [{"type": "reasoning.text", "text": "x"}],
        "custom_future_field": 42,
    }


def test_extra_fields_never_clobber_standard_keys():
    # An ill-formed server response that shoves "content" into the
    # extras pocket must not overwrite the real content during echo.
    msg = AssistantMessage("real reply", extra_fields={"content": "evil"})
    payload = msg.to_dict()
    assert payload["content"] == "real reply"


# ──────────────────────── Conversation persistence ───────────────────────


def test_conversation_to_json_round_trips_extra_fields():
    conv = Conversation()
    conv.append("user", "hi")
    conv.append(
        "assistant",
        "answer",
        extra_fields={"reasoning_content": "let me think"},
    )

    reloaded = Conversation.from_json(conv.to_json())
    wire = reloaded.to_messages()
    assistant = wire[1]
    # Outgoing wire format echoes the field back on the next turn.
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "let me think"


# ─────────────────────── Provider stream accumulators ───────────────────


class _FakeModelExtra:
    """Stand-in for a pydantic SDK delta with an ``model_extra`` pocket."""

    def __init__(self, extra: dict):
        self.model_extra = extra


def test_delta_field_pulls_from_model_extra():
    delta = _FakeModelExtra({"reasoning_content": "a chunk"})
    assert _delta_field(delta, "reasoning_content") == "a chunk"


def test_delta_field_falls_back_to_attr_and_dict():
    class Plain:
        reasoning = "v"

    assert _delta_field(Plain(), "reasoning") == "v"
    assert _delta_field({"reasoning": "v"}, "reasoning") == "v"


def test_pack_reasoning_fields_drops_unseen_empties():
    packed = _pack_reasoning_fields("", [], {})
    assert packed == {}
    packed = _pack_reasoning_fields("content", [{"a": 1}], {"reasoning": "narrative"})
    assert packed == {
        "reasoning_content": "content",
        "reasoning_details": [{"a": 1}],
        "reasoning": "narrative",
    }


def test_pack_reasoning_fields_preserves_seen_empties():
    packed = _pack_reasoning_fields(
        "",
        [],
        {"reasoning": ""},
        include_text=True,
        include_details=True,
    )
    assert packed == {
        "reasoning_content": "",
        "reasoning_details": [],
        "reasoning": "",
    }


# ───────────────────────────── Provider wiring ──────────────────────────


def test_openai_provider_default_echo_on():
    p = OpenAIProvider(api_key="x", model="m")
    assert p.echo_reasoning is True
    assert p.last_assistant_extra_fields == {}


def test_openai_provider_echo_can_be_disabled():
    p = OpenAIProvider(api_key="x", model="m", echo_reasoning=False)
    assert p.echo_reasoning is False


def test_provider_exposes_captured_extra_fields():
    """Simulate what ``_stream_chat`` / ``_complete_chat`` store on the
    provider instance and confirm the property surfaces it unchanged."""
    p = OpenAIProvider(api_key="x", model="m")
    p._last_assistant_extra_fields = {"reasoning_content": "captured"}
    assert p.last_assistant_extra_fields == {"reasoning_content": "captured"}


def test_provider_extra_fields_default_empty_without_capture():
    p = OpenAIProvider(api_key="x", model="m")
    assert p.last_assistant_extra_fields == {}


# ───────────────────────────── End-to-end smoke ─────────────────────────


def test_round_trip_deepseek_style_reasoning():
    """Minimal happy path: a conversation carrying reasoning_content
    survives append → persistence → next-turn wire format."""
    conv = Conversation()
    conv.append("system", "be brief")
    conv.append("user", "question")
    conv.append(
        "assistant",
        "answer",
        extra_fields={"reasoning_content": "DeepSeek-V4 style hidden reasoning"},
    )

    wire = conv.to_messages()
    last = wire[-1]
    assert last["role"] == "assistant"
    assert last["content"] == "answer"
    assert last["reasoning_content"] == "DeepSeek-V4 style hidden reasoning"


def test_round_trip_openrouter_reasoning_details():
    """MiMo-via-OpenRouter shape uses reasoning_details (array of
    blocks). Same pipeline, different field name."""
    conv = Conversation()
    conv.append("user", "q")
    conv.append(
        "assistant",
        "a",
        extra_fields={
            "reasoning_details": [
                {"type": "reasoning.text", "text": "step 1"},
                {"type": "reasoning.text", "text": "step 2"},
            ],
        },
    )
    wire = conv.to_messages()
    assert wire[-1]["reasoning_details"][1]["text"] == "step 2"


def test_normalize_stateful_fields_is_noop_without_seen_fields():
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    assert normalize_stateful_assistant_fields(messages) is messages


def test_normalize_stateful_fields_fills_missing_assistant_defaults():
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "first",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "tool", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]
    normalized = normalize_stateful_assistant_fields(messages)
    assert normalized is not messages
    assert normalized[1]["reasoning_content"] == "first"
    assert normalized[3]["reasoning_content"] == ""
    assert "reasoning_content" not in normalized[0]
    assert "reasoning_content" not in normalized[2]


# ─────────────────────────── pytest asyncio marker ──────────────────────

# The file imports no async fixtures — no global asyncio mark needed.
# Keeping this here avoids a naked "unused import" reminder if future
# tests land in the same module.
__all__: list[str] = []

# Silence pytest collection warnings if the repo's default is strict.
pytestmark = pytest.mark.filterwarnings("default")
