"""
Define built-in model presets, provider routing, variations, and aliases.

Direct API variants use the primary name, OpenRouter variants use ``-or``,
and direct OpenAI variants use ``-api`` because the primary GPT names route
through Codex OAuth. ``max_context`` records the chosen operating limit, which
may be lower than the vendor-advertised maximum for reliability.
"""

from typing import Any

from kohakuterrarium.llm.preset_aliases import _CANONICAL_NAMES, ALIASES
from kohakuterrarium.llm.preset_groups import (
    _ANTHROPIC_EFFORT_46_GROUP,
    _ANTHROPIC_EFFORT_47_GROUP,
    _CODEX_REASONING_GROUP,
    _CODEX_SPEED_GROUP,
    _GEMINI_THINKING_GROUP,
    _GEMINI_THINKING_GROUP_WITH_MINIMAL,
    _GEMINI_THINKING_LITE_GROUP,
    _GLM_EFFORT_GROUP,
    _GPT56_LUNA_REASONING_GROUP,
    _GPT56_MODE_GROUP,
    _GPT56_REASONING_GROUP,
    _GROK_EFFORT_GROUP,
    _MIMO_THINKING_GROUP,
    _MISTRAL_REASONING_GROUP,
    _OPENAI_56_REASONING_GROUP,
    _OPENAI_REASONING_GROUP,
    _OPENAI_SPEED_GROUP,
    _OR_REASONING_GROUP,
    _OR_REASONING_GROUP_56,
    _OR_REASONING_GROUP_WITH_XHIGH,
    _OR_REASONING_TOGGLE_GROUP,
)
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import get_logger

__all__ = [
    "ALIASES",
    "PRESETS",
    "get_all_presets",
    "iter_all_presets",
    "resolve_alias",
]

logger = get_logger(__name__)

PRESETS: dict[str, dict[str, Any]] = {
    # ═══════════════════════════════════════════════════════
    #  OpenAI via Codex OAuth (ChatGPT subscription auth)
    #  reasoning_effort is a top-level field consumed directly
    #  by CodexOAuthProvider. GPT-5.4/5.5 additionally support
    #  fast mode (priority tier). The Codex/GPT lines merged at
    #  5.4 — there is no separate ``-codex`` model anymore.
    # ═══════════════════════════════════════════════════════
    "gpt-5.6-sol": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "max_context": 372000,
        "max_output": 128000,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _GPT56_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
            "mode": _GPT56_MODE_GROUP,
        },
    },
    "gpt-5.6-terra": {
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "max_context": 372000,
        "max_output": 128000,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _GPT56_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
            "mode": _GPT56_MODE_GROUP,
        },
    },
    "gpt-5.6-luna": {
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "max_context": 372000,
        "max_output": 128000,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            # Luna exposes max but not the CLI-only ultra mode.
            "reasoning": _GPT56_LUNA_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
            "mode": _GPT56_MODE_GROUP,
        },
    },
    "gpt-5.5": {
        "provider": "codex",
        "model": "gpt-5.5",
        # Use the documented 272K input capacity rather than aggregator estimates.
        "max_context": 272000,
        "max_output": 128000,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _CODEX_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-5.4": {
        "provider": "codex",
        "model": "gpt-5.4",
        # Cap below the advertised maximum to avoid long-context quality degradation.
        "max_context": 400000,
        "max_output": 128000,
        "reasoning_effort": "xhigh",
        "extra_body": {"websocket_mode": True},
        "variation_groups": {
            "reasoning": _CODEX_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-5.4-mini": {
        "provider": "codex",
        "model": "gpt-5.4-mini",
        "max_context": 400000,
        "max_output": 128000,
        "reasoning_effort": "high",
        "extra_body": {"websocket_mode": True},
        # Mini does not expose the priority-backed fast-mode variation.
        "variation_groups": {"reasoning": _CODEX_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  OpenAI Direct API (-api suffix, api-key auth).
    #  5.6 family: reasoning.effort = none…xhigh | max (no ultra)
    #  5.4/5.5:    reasoning.effort = none…xhigh
    # ═══════════════════════════════════════════════════════
    # GPT-5.6 (Sol/Terra/Luna): API context is 1.05M per the OpenAI model
    # pages (the 372K on the codex presets is the Codex-app operating
    # point, not the API limit). All three: 128K output, priority/flex
    # service tiers, limited preview (approved orgs) as of 2026-07-10.
    "gpt-5.6-sol-api": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "max_context": 1050000,
        "max_output": 128000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "websocket_mode": True,
        },
        "variation_groups": {
            "reasoning": _OPENAI_56_REASONING_GROUP,
            "mode": _GPT56_MODE_GROUP,
            "speed": _OPENAI_SPEED_GROUP,
        },
    },
    "gpt-5.6-terra-api": {
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "max_context": 1050000,
        "max_output": 128000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "websocket_mode": True,
        },
        "variation_groups": {
            "reasoning": _OPENAI_56_REASONING_GROUP,
            "mode": _GPT56_MODE_GROUP,
            "speed": _OPENAI_SPEED_GROUP,
        },
    },
    "gpt-5.6-luna-api": {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "max_context": 1050000,
        "max_output": 128000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "websocket_mode": True,
        },
        "variation_groups": {
            "reasoning": _OPENAI_56_REASONING_GROUP,
            "mode": _GPT56_MODE_GROUP,
            "speed": _OPENAI_SPEED_GROUP,
        },
    },
    "gpt-5.5-api": {
        "provider": "openai",
        "model": "gpt-5.5",
        "max_context": 272000,
        "max_output": 128000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "websocket_mode": True,
        },
        "variation_groups": {
            "reasoning": _OPENAI_REASONING_GROUP,
            "speed": _OPENAI_SPEED_GROUP,
        },
    },
    "gpt-5.4-api": {
        "provider": "openai",
        "model": "gpt-5.4",
        "max_context": 400000,
        "max_output": 128000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "websocket_mode": True,
        },
        "variation_groups": {
            "reasoning": _OPENAI_REASONING_GROUP,
            "speed": _OPENAI_SPEED_GROUP,
        },
    },
    "gpt-5.4-mini-api": {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "max_context": 400000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    "gpt-5.4-nano-api": {
        "provider": "openai",
        "model": "gpt-5.4-nano",
        "max_context": 400000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  OpenAI via OpenRouter (-or suffix).
    #  Same deliberate context caps as the direct variants.
    # ═══════════════════════════════════════════════════════
    "gpt-5.6-sol-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-sol",
        "max_context": 1050000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {
            "reasoning": _OR_REASONING_GROUP_56,
            "mode": _GPT56_MODE_GROUP,
        },
    },
    "gpt-5.6-terra-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-terra",
        "max_context": 1050000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {
            "reasoning": _OR_REASONING_GROUP_56,
            "mode": _GPT56_MODE_GROUP,
        },
    },
    "gpt-5.6-luna-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "max_context": 1050000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {
            "reasoning": _OR_REASONING_GROUP_56,
            "mode": _GPT56_MODE_GROUP,
        },
    },
    "gpt-5.5-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.5",
        "max_context": 272000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.4-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "max_context": 400000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.4-mini-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4-mini",
        "max_context": 400000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.4-nano-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4-nano",
        "max_context": 400000,
        "max_output": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    # ═══════════════════════════════════════════════════════
    #  Anthropic Claude Direct API (primary — non-OpenAI format,
    #  requires the dedicated ``anthropic`` backend_type client).
    #
    #  Adaptive thinking is the only thinking mode on 4.7+:
    #    Fable 5:    thinking always on; effort low…xhigh/max
    #    Opus 4.7/4.8, Sonnet 5: adaptive; effort low…xhigh/max
    #    Opus 4.6 / Sonnet 4.6:  adaptive; effort low…high/max
    #  ``thinking.display`` defaults to "omitted" on Fable 5 /
    #  Opus 4.7/4.8 / Sonnet 5 — we opt in to "summarized" so the
    #  UI can show the reasoning trace.
    # ═══════════════════════════════════════════════════════
    "claude-fable-5": {
        "provider": "anthropic",
        "model": "claude-fable-5",
        "max_context": 1000000,
        # Thinking is always on for Fable 5 (explicit ``adaptive`` is
        # accepted; ``disabled`` is rejected with a 400). The raw chain of
        # thought is never returned — "summarized" is the visible option.
        # NOTE: requires 30-day data retention on the org (no ZDR).
        "extra_body": {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "high"},
        },
        "variation_groups": {"reasoning": _ANTHROPIC_EFFORT_47_GROUP},
    },
    "claude-opus-4.8": {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "max_context": 1000000,
        # 4.8 guidance: start at ``high`` and sweep — reflexive xhigh is
        # no longer the best default (higher ceiling than 4.7).
        "extra_body": {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "high"},
        },
        "variation_groups": {"reasoning": _ANTHROPIC_EFFORT_47_GROUP},
    },
    "claude-opus-4.7": {
        "provider": "anthropic",
        "model": "claude-opus-4-7",
        "max_context": 1000000,
        # Opus 4.7 defaults ``thinking.display`` to ``"omitted"`` — we
        # explicitly opt in to summarized thinking for the UI trace.
        "extra_body": {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "xhigh"},
        },
        "variation_groups": {"reasoning": _ANTHROPIC_EFFORT_47_GROUP},
    },
    "claude-opus-4.6": {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "max_context": 1000000,
        "extra_body": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        },
        "variation_groups": {"reasoning": _ANTHROPIC_EFFORT_46_GROUP},
    },
    "claude-sonnet-5": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "max_context": 1000000,
        # Sonnet 5 runs adaptive thinking even when the field is omitted;
        # we keep it explicit + summarized for a visible trace. Full effort
        # scale incl. xhigh (first Sonnet with it).
        "extra_body": {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "high"},
        },
        "variation_groups": {"reasoning": _ANTHROPIC_EFFORT_47_GROUP},
    },
    "claude-sonnet-4.6": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "max_context": 1000000,
        "extra_body": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        },
        "variation_groups": {"reasoning": _ANTHROPIC_EFFORT_46_GROUP},
    },
    "claude-haiku-4.5": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "max_context": 200000,
        # Haiku 4.5 uses the older extended-thinking (budget_tokens), not the
        # adaptive effort scale — not exposed as a variation group here.
    },
    # ═══════════════════════════════════════════════════════
    #  Anthropic Claude via OpenRouter (-or suffix).
    #  OR normalizes reasoning knobs via its unified param.
    #  xhigh is honored by Fable 5 / Opus 4.7+ / Sonnet 5.
    # ═══════════════════════════════════════════════════════
    "claude-fable-5-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-fable-5",
        "max_context": 1000000,
        "max_output": 128000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "claude-opus-4.8-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4.8",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "claude-opus-4.7-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4.7",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "claude-opus-4.6-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4.6",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "claude-sonnet-5-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-5",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "claude-sonnet-4.6-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "claude-haiku-4.5-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-haiku-4.5",
        "max_context": 200000,
        "max_output": 64000,
        "extra_body": {
            "cache_control": {"type": "ephemeral"},
        },
        "variation_groups": {
            "reasoning": {
                "off": {"extra_body.reasoning.enabled": False},
                "low": {
                    "extra_body.reasoning.enabled": True,
                    "extra_body.reasoning.effort": "low",
                },
                "medium": {
                    "extra_body.reasoning.enabled": True,
                    "extra_body.reasoning.effort": "medium",
                },
                "high": {
                    "extra_body.reasoning.enabled": True,
                    "extra_body.reasoning.effort": "high",
                },
            }
        },
    },
    # ═══════════════════════════════════════════════════════
    #  Google Gemini Direct API (primary — OpenAI-compat endpoint).
    #  3.1 Pro is still the Pro tier (3.5 Pro not GA yet, ~mid-July
    #  2026); Flash moved to 3.5 (GA); Flash-Lite 3.1 id is stable.
    # ═══════════════════════════════════════════════════════
    "gemini-3.1-pro": {
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
        "variation_groups": {"thinking": _GEMINI_THINKING_GROUP},
    },
    "gemini-3.5-flash": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "max_context": 1048576,
        "max_output": 65536,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
        # Flash supports the full set MINIMAL/LOW/MEDIUM/HIGH (default
        # MEDIUM per the 2026-07 docs).
        "variation_groups": {"thinking": _GEMINI_THINKING_GROUP_WITH_MINIMAL},
    },
    "gemini-3.1-flash-lite": {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "max_context": 1048576,
        "max_output": 65536,
        # Lite accepts MINIMAL/LOW/MEDIUM only (no HIGH; default MINIMAL).
        "extra_body": {"google": {"thinking_config": {"thinking_level": "MEDIUM"}}},
        "variation_groups": {"thinking": _GEMINI_THINKING_LITE_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Google Gemini via OpenRouter (-or suffix).
    #  OR keeps the ``-preview`` suffix on flash-lite even though
    #  the direct API id is stable.
    # ═══════════════════════════════════════════════════════
    "gemini-3.1-pro-or": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-pro-preview",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "gemini-3.5-flash-or": {
        "provider": "openrouter",
        "model": "google/gemini-3.5-flash",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "gemini-3.1-flash-lite-or": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite-preview",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # Image generation — reasoning doesn't apply.
    "nano-banana": {
        "provider": "openrouter",
        # "Nano Banana 2" (Gemini 3.1 Flash Image).
        "model": "google/gemini-3.1-flash-image-preview",
        "max_context": 131072,
        "max_output": 32768,
    },
    "nano-banana-pro": {
        "provider": "openrouter",
        # "Nano Banana Pro" (Gemini 3 Pro Image) — 2K/4K output support.
        "model": "google/gemini-3-pro-image",
        "max_context": 65536,
        "max_output": 32768,
    },
    # ═══════════════════════════════════════════════════════
    #  Gemma 4 (open models, OpenRouter).
    #  Gemma 4 supports a thinking mode; OR's unified reasoning
    #  param maps onto it.
    # ═══════════════════════════════════════════════════════
    "gemma-4-31b": {
        "provider": "openrouter",
        "model": "google/gemma-4-31b-it",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "gemma-4-26b": {
        "provider": "openrouter",
        "model": "google/gemma-4-26b-a4b-it",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Qwen (OpenRouter only). 3.7 is the closed flagship tier,
    #  3.6-flash the fast tier; the 3.5 open-weight releases are
    #  still the current open models. Coder line stays qwen3-*.
    # ═══════════════════════════════════════════════════════
    "qwen3.7-max": {
        "provider": "openrouter",
        "model": "qwen/qwen3.7-max",
        "max_context": 1000000,
        "max_output": 65536,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "qwen3.7-plus": {
        "provider": "openrouter",
        "model": "qwen/qwen3.7-plus",
        "max_context": 1000000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "qwen3.6-flash": {
        "provider": "openrouter",
        "model": "qwen/qwen3.6-flash",
        "max_context": 1000000,
        "max_output": 65536,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "qwen3.5-397b": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-397b-a17b",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "qwen3.5-27b": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-27b",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "qwen3-coder-plus": {
        "provider": "openrouter",
        "model": "qwen/qwen3-coder-plus",
        "max_context": 1000000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Moonshot Kimi Code Direct API (Anthropic-compatible).
    #  Kimi Code's documented fixed model id is ``kimi-for-coding``
    #  (now served by K2.7 Code under the hood).
    # ═══════════════════════════════════════════════════════
    "kimi-for-coding": {
        "provider": "kimi-code",
        "model": "kimi-for-coding",
        "max_context": 262144,
        "max_output": 32768,
    },
    # ═══════════════════════════════════════════════════════
    #  Moonshot Kimi K2.6 / K2.7 Code (OpenRouter).
    #   K2.6:       latest general model — configurable reasoning.
    #   K2.7 Code:  coding-specialized — always-on thinking, no
    #               variation group. (No plain "k2.7" exists.)
    # ═══════════════════════════════════════════════════════
    "kimi-k2.7-code": {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k2.7-code",
        "max_context": 262144,
        "max_output": 32768,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "kimi-k2.6": {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k2.6",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  MiniMax M3 (OpenRouter). 1M context; thinking is a toggle
    #  (disabled / adaptive / budget) — adaptive is the API default,
    #  we pin it on and expose an off switch.
    # ═══════════════════════════════════════════════════════
    "minimax-m3": {
        "provider": "openrouter",
        "model": "minimax/minimax-m3",
        "max_context": 1048576,
        "max_output": 65536,
        "extra_body": {"reasoning": {"enabled": True}},
        "variation_groups": {"reasoning": _OR_REASONING_TOGGLE_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  GLM Coding Plan Direct API (Anthropic-compatible).
    #  GLM's Anthropic-compatible endpoint uses Bearer-token auth.
    #  GLM-5.2 ids are lowercase; the 1M-context variant is a
    #  SEPARATE model id with a literal ``[1m]`` suffix.
    # ═══════════════════════════════════════════════════════
    "glm-5.2": {
        "provider": "glm-coding",
        "model": "glm-5.2",
        "max_context": 262144,
        "max_output": 131072,
        "extra_body": {"auth_as_bearer": True},
    },
    "glm-5.2-1m": {
        "provider": "glm-coding",
        "model": "glm-5.2[1m]",
        "max_context": 1000000,
        "max_output": 131072,
        "extra_body": {"auth_as_bearer": True},
    },
    # ═══════════════════════════════════════════════════════
    #  GLM (Z.ai, OpenRouter). Thinking effort is high / xhigh only.
    # ═══════════════════════════════════════════════════════
    "glm-5.2-or": {
        "provider": "openrouter",
        "model": "z-ai/glm-5.2",
        "max_context": 1000000,
        "max_output": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _GLM_EFFORT_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Xiaomi MiMo 2.5 Direct API (primary — ``kt login mimo``).
    #  MiMo-V2 was deprecated by the official API on 2026-06-30.
    #  Model ids are lowercase on the platform; thinking is a
    #  binary toggle (default disabled — we enable it).
    # ═══════════════════════════════════════════════════════
    "mimo-v2.5-pro": {
        "provider": "mimo",
        "model": "mimo-v2.5-pro",
        "max_context": 1048576,
        "extra_body": {"thinking": {"type": "enabled"}},
        "variation_groups": {"thinking": _MIMO_THINKING_GROUP},
    },
    "mimo-v2.5": {
        "provider": "mimo",
        "model": "mimo-v2.5",
        "max_context": 1048576,
        "extra_body": {"thinking": {"type": "enabled"}},
        "variation_groups": {"thinking": _MIMO_THINKING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Xiaomi MiMo via OpenRouter (-or suffix).
    # ═══════════════════════════════════════════════════════
    "mimo-v2.5-pro-or": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2.5-pro",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True}},
        "variation_groups": {"reasoning": _OR_REASONING_TOGGLE_GROUP},
    },
    "mimo-v2.5-or": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2.5",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True}},
        "variation_groups": {"reasoning": _OR_REASONING_TOGGLE_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  xAI Grok via a reusable local subscription login.
    #  Keep this explicit suffix separate from OpenRouter/API billing.
    # ═══════════════════════════════════════════════════════
    "grok-4.6-subscription": {
        "provider": "grok-subscription",
        "model": "grok-4.6",
        "max_context": 500000,
        "max_output": 65536,
    },
    "grok-4.5-subscription": {
        "provider": "grok-subscription",
        "model": "grok-4.5",
        "max_context": 500000,
        "max_output": 65536,
    },
    "grok-4.3-subscription": {
        "provider": "grok-subscription",
        "model": "grok-4.3",
        "max_context": 1000000,
        "max_output": 65536,
    },
    "grok-4.20-0309-reasoning-subscription": {
        "provider": "grok-subscription",
        "model": "grok-4.20-0309-reasoning",
        "max_context": 1000000,
        "max_output": 65536,
    },
    "grok-4.20-0309-non-reasoning-subscription": {
        "provider": "grok-subscription",
        "model": "grok-4.20-0309-non-reasoning",
        "max_context": 1000000,
        "max_output": 65536,
    },
    "grok-4.20-multi-agent-0309-subscription": {
        "provider": "grok-subscription",
        "model": "grok-4.20-multi-agent-0309",
        "max_context": 1000000,
        "max_output": 65536,
    },
    "grok-build-0.1-subscription": {
        "provider": "grok-subscription",
        "model": "grok-build-0.1",
        "max_context": 256000,
        "max_output": 65536,
    },
    # ═══════════════════════════════════════════════════════
    #  xAI Grok series (OpenRouter).
    #   - grok-4.5:      the agent-oriented flagship. Reasoning
    #                    effort low/medium/high (default high),
    #                    cannot be disabled.
    #   - grok-4.1-fast: cheap 2M-context agentic/tool-calling
    #                    model; reasoning is an on/off toggle.
    #   - grok-code-fast: cheap coding model, reasoning mandatory.
    # ═══════════════════════════════════════════════════════
    "grok-4.5": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.5",
        "max_context": 500000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _GROK_EFFORT_GROUP},
    },
    "grok-4.1-fast": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.1-fast",
        "max_context": 2000000,
        "extra_body": {"reasoning": {"enabled": True}},
        "variation_groups": {"reasoning": _OR_REASONING_TOGGLE_GROUP},
    },
    "grok-code-fast": {
        "provider": "openrouter",
        "model": "x-ai/grok-code-fast-1",
        "max_context": 256000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Mistral (OpenRouter), post-2026-03 consolidation:
    #  Small 4 + Medium 3.5 are hybrid models that folded in
    #  Magistral (reasoning) and Pixtral (vision); the standalone
    #  Magistral/Pixtral lines are retiring. ``reasoning_effort``
    #  on the hybrids = none | low | medium | high. Large 3 is
    #  instruct-only (no reasoning knob).
    # ═══════════════════════════════════════════════════════
    "mistral-medium-3.5": {
        "provider": "openrouter",
        "model": "mistralai/mistral-medium-3-5",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _MISTRAL_REASONING_GROUP},
    },
    "mistral-large-3": {
        "provider": "openrouter",
        "model": "mistralai/mistral-large-2512",
        "max_context": 262144,
    },
    "mistral-small-4": {
        "provider": "openrouter",
        "model": "mistralai/mistral-small-2603",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _MISTRAL_REASONING_GROUP},
    },
    # Non-reasoning Mistral specialists.
    "devstral-2": {
        "provider": "openrouter",
        "model": "mistralai/devstral-2512",
        "max_context": 262144,
    },
    "codestral": {
        "provider": "openrouter",
        "model": "mistralai/codestral-2508",
        "max_context": 262144,
    },
    "ministral-3-14b": {
        "provider": "openrouter",
        "model": "mistralai/ministral-14b-2512",
        "max_context": 262144,
    },
    "ministral-3-8b": {
        "provider": "openrouter",
        "model": "mistralai/ministral-8b-2512",
        "max_context": 262144,
    },
}

# Aliases stay separate from the canonical provider/name view.
_package_presets_merged: bool = False
_all_presets_cache: dict[tuple[str, str], dict[str, Any]] | None = None


def _canonical_entry(
    legacy_name: str, data: dict[str, Any]
) -> tuple[str, str, dict[str, Any]] | None:
    """Map a preset into the canonical provider/name key space."""
    provider = data.get("provider", "") or ""
    if not provider:
        return None
    canonical = _CANONICAL_NAMES.get(legacy_name, legacy_name)
    body = {k: v for k, v in data.items() if k != "provider"}
    return provider, canonical, body


def _merge_package_presets() -> dict[tuple[str, str], dict[str, Any]]:
    """Load valid package presets without allowing them to override built-ins."""
    global _package_presets_merged
    if _package_presets_merged:
        return {}

    _package_presets_merged = True
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    builtin_keys = {
        (data.get("provider", ""), _CANONICAL_NAMES.get(name, name))
        for name, data in PRESETS.items()
        if data.get("provider")
    }

    try:
        for pkg in list_packages():
            for preset in pkg.get("llm_presets", []):
                if not isinstance(preset, dict):
                    continue
                preset_name = preset.get("name")
                provider = preset.get("provider", "")
                if not preset_name or not provider:
                    continue
                key = (provider, preset_name)
                if key in builtin_keys:
                    logger.debug(
                        "Package preset skipped (builtin exists)",
                        preset=preset_name,
                        provider=provider,
                        package=pkg["name"],
                    )
                    continue
                if key in merged:
                    logger.debug(
                        "Package preset skipped (duplicate)",
                        preset=preset_name,
                        provider=provider,
                        package=pkg["name"],
                    )
                    continue
                preset_data = {
                    k: v for k, v in preset.items() if k not in {"name", "provider"}
                }
                merged[key] = preset_data
    except Exception as e:
        logger.warning("Failed to load package presets", error=str(e), exc_info=True)

    return merged


def get_all_presets() -> dict[tuple[str, str], dict[str, Any]]:
    """Return cached built-in and package presets keyed by provider and name."""
    global _all_presets_cache
    if _all_presets_cache is not None:
        return _all_presets_cache

    flat: dict[tuple[str, str], dict[str, Any]] = {}
    for legacy_name, data in PRESETS.items():
        canonical = _canonical_entry(legacy_name, data)
        if canonical is None:
            continue
        provider, bare_name, body = canonical
        flat[(provider, bare_name)] = body

    flat.update(_merge_package_presets())
    _all_presets_cache = flat
    return _all_presets_cache


def iter_all_presets() -> list[tuple[str, str, dict[str, Any]]]:
    """Yield every built-in + package preset as ``(provider, name, data)``."""
    return [(p, n, d) for (p, n), d in get_all_presets().items()]


def resolve_alias(name: str) -> tuple[str, str] | None:
    """Resolve a legacy alias, returning ``None`` for canonical inputs."""
    return ALIASES.get(name)
