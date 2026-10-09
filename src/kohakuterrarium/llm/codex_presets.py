"""GPT-6 family presets for the Codex subscription provider."""

from typing import Any

from kohakuterrarium.llm.preset_groups import (
    _CODEX_SPEED_GROUP,
    _GPT56_REASONING_GROUP,
    _GPT6_REASONING_GROUP,
    _GPT5X_CONTEXT_GROUP,
)

PRESETS: dict[str, dict[str, Any]] = {
    "gpt-6-astra": {
        "provider": "codex",
        "model": "gpt-6-astra",
        "max_context": 1000000,
        "max_output": 128000,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "context": _GPT5X_CONTEXT_GROUP,
            "reasoning": _GPT6_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-6.1-sol": {
        "provider": "codex",
        "model": "gpt-6.1-sol",
        "max_context": 272000,
        "max_output": 128000,
        "reasoning_effort": "medium",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "context": _GPT5X_CONTEXT_GROUP,
            "reasoning": _GPT6_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-6-sol": {
        "provider": "codex",
        "model": "gpt-6-sol",
        "max_context": 272000,
        "max_output": 128000,
        "reasoning_effort": "medium",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "context": _GPT5X_CONTEXT_GROUP,
            "reasoning": _GPT6_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-6-luna": {
        "provider": "codex",
        "model": "gpt-6-luna",
        "max_context": 272000,
        "max_output": 128000,
        "reasoning_effort": "medium",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "context": _GPT5X_CONTEXT_GROUP,
            "reasoning": _GPT56_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
}
