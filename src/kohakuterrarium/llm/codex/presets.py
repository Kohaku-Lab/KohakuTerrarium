"""GPT-6 family presets for the Codex subscription provider."""

from typing import Any

from kohakuterrarium.llm.presets.groups import (
    CODEX_CONTEXT_WINDOW,
    _CODEX_SPEED_GROUP,
    _GPT56_REASONING_GROUP,
    _GPT6_REASONING_GROUP,
)

PRESETS: dict[str, dict[str, Any]] = {
    "gpt-6-astra": {
        "provider": "codex",
        "model": "gpt-6-astra",
        "max_context": 1000000,
        "max_output": 128000,
        "context_window": CODEX_CONTEXT_WINDOW,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _GPT6_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-6.1-sol": {
        "provider": "codex",
        "model": "gpt-6.1-sol",
        "max_context": 272000,
        "max_output": 128000,
        "context_window": CODEX_CONTEXT_WINDOW,
        "reasoning_effort": "medium",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _GPT6_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-6-sol": {
        "provider": "codex",
        "model": "gpt-6-sol",
        "max_context": 272000,
        "max_output": 128000,
        "context_window": CODEX_CONTEXT_WINDOW,
        "reasoning_effort": "medium",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _GPT6_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-6-luna": {
        "provider": "codex",
        "model": "gpt-6-luna",
        "max_context": 272000,
        "max_output": 128000,
        "context_window": CODEX_CONTEXT_WINDOW,
        "reasoning_effort": "medium",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _GPT56_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
}

# ``max_context`` is the catalog's ``context_window``; the reasoning default
# follows the same-family preset this one succeeds.
PRESETS.update(
    {
        model: {
            "provider": "codex",
            "model": model,
            "max_context": max_context,
            "max_output": 128000,
            "context_window": CODEX_CONTEXT_WINDOW,
            "reasoning_effort": effort,
            "extra_body": {"websocket_mode": True},
            "variation_groups": {
                "reasoning": _GPT6_REASONING_GROUP,
                "speed": _CODEX_SPEED_GROUP,
            },
        }
        for model, max_context, effort in (
            ("gpt-6.2-sol", 500000, "medium"),
            ("gpt-6.2-astra", 272000, "xhigh"),
            ("gpt-6.1-terra", 500000, "xhigh"),
        )
    }
)
