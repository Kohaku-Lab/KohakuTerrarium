"""
Built-in LLM presets and model aliases.

A preset references a **provider by name** (codex, openai, openrouter,
anthropic, gemini, mimo, …). The provider owns the backend_type, base_url,
and api_key_env. Presets only carry model-facing metadata (model id, context
window, reasoning effort, extra_body, variation groups).

Variation groups let one preset expose multiple knobs (reasoning effort,
fast mode, thinking level, …) without duplicating the entry. Selection is
``preset_name@group=option,group2=option2``. Patches target one of:
``temperature``, ``reasoning_effort``, ``service_tier``, ``max_context``,
``max_output``, ``extra_body``. Variation values were researched against
the relevant provider docs (effective 2026-04-20) — see the per-provider
notes inline below.

Naming convention (post-2026-04 refactor):
    - The **direct / native-API** variant is the primary name
      (``claude-opus-4.7``, ``gemini-3.1-pro``, ``mimo-v2-pro``).
    - The **OpenRouter-routed** variant uses the ``-or`` suffix
      (``claude-opus-4.7-or``).
    - OpenAI is an exception: the primary ``gpt-5.4`` stays bound to
      the **codex OAuth** provider (ChatGPT-subscription path — the
      headline feature); the direct OpenAI API variant uses ``-api``,
      and OpenRouter uses ``-or``.
    - Legacy names (``claude-opus-4.6-direct``, ``or-gpt-5.4``, …) are
      preserved via ``ALIASES`` at the bottom of this file.
"""

from typing import Any

from kohakuterrarium.packages import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# ── Reusable variation blocks ────────────────────────────────────
# Declaring the canonical shapes once avoids copy-paste drift. Each block
# is inlined into the presets below via ``**`` splat or nested lookup.

# Codex OAuth (ChatGPT-subscription): top-level ``reasoning_effort`` field.
# GPT-5.4 docs: effort = ``none | low | medium | high | xhigh`` (no
# ``minimal`` — that's OpenRouter-only terminology; and no ``max`` — that's
# Anthropic-only).
_CODEX_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "none": {"reasoning_effort": "none"},
    "low": {"reasoning_effort": "low"},
    "medium": {"reasoning_effort": "medium"},
    "high": {"reasoning_effort": "high"},
    "xhigh": {"reasoning_effort": "xhigh"},
}

# Codex "fast mode" on GPT-5.4.
#
# The API accepts ``service_tier="priority"`` (Priority processing — the only
# valid non-default tier values are ``priority`` and ``default``). The Codex
# CLI's own ``config.toml`` uses a separate ``[features].fast_mode = true``
# flag + ``service_tier = "fast"`` literal that the CLI translates into the
# API-level priority header — the literal ``"fast"`` value is NOT accepted
# by the OpenAI API itself (observed: 400 ``Unsupported service_tier: fast``).
_CODEX_SPEED_GROUP: dict[str, dict[str, Any]] = {
    "normal": {},
    "fast": {"service_tier": "priority"},
}

# OpenAI direct API reasoning: extra_body.reasoning.effort. Full scale per the
# 2026-04 GPT-5.4 docs: ``none | low | medium | high | xhigh``. OpenAI's docs
# use ``none`` (not ``minimal`` — ``minimal`` is OpenRouter's unified name).
_OPENAI_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "none": {"extra_body.reasoning.effort": "none"},
    "low": {"extra_body.reasoning.effort": "low"},
    "medium": {"extra_body.reasoning.effort": "medium"},
    "high": {"extra_body.reasoning.effort": "high"},
    "xhigh": {"extra_body.reasoning.effort": "xhigh"},
}

# OpenRouter unified reasoning. ``xhigh`` is only accepted by Claude Opus 4.7+,
# GPT-5.x, and a handful of recent models — most models silently clamp to
# ``high``. Including it in the common block is fine: per-model behavior is
# the user's concern.
_OR_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "minimal": {"extra_body.reasoning.effort": "minimal"},
    "low": {"extra_body.reasoning.effort": "low"},
    "medium": {"extra_body.reasoning.effort": "medium"},
    "high": {"extra_body.reasoning.effort": "high"},
}

# Same as above plus xhigh, for the models that actually honor it.
_OR_REASONING_GROUP_WITH_XHIGH: dict[str, dict[str, Any]] = {
    **_OR_REASONING_GROUP,
    "xhigh": {"extra_body.reasoning.effort": "xhigh"},
}

# Anthropic direct (via Anthropic's OpenAI-compat endpoint).
#
# We route through ``backend_type=openai`` against ``api.anthropic.com/v1``.
# Anthropic's compat layer explicitly supports ``extra_body.thinking``
# (including adaptive mode and ``budget_tokens``) but silently ignores
# top-level ``reasoning_effort`` and ``service_tier`` plus compat-unknown
# fields like ``speed`` / ``betas``. See the project-level docstring in
# ``profiles.py`` for the full compatibility caveat.
#
# Effort via compat is best-effort: Anthropic's native API places ``effort``
# on a top-level ``output_config`` dict, which the compat layer *does*
# forward through ``extra_body`` when recognized. Availability by model:
#   - Opus 4.6 / Sonnet 4.6:   low / medium / high / max
#   - Opus 4.7 (2026-04-16):   low / medium / high / xhigh / max
# For fast mode (Opus-only) use the ``-or`` OpenRouter variants — the
# native ``speed: "fast"`` + beta header combo is not surfaced by Anthropic's
# OpenAI-compat layer and would be silently dropped.
_ANTHROPIC_EFFORT_46_GROUP: dict[str, dict[str, Any]] = {
    "low": {"extra_body.output_config.effort": "low"},
    "medium": {"extra_body.output_config.effort": "medium"},
    "high": {"extra_body.output_config.effort": "high"},
    "max": {"extra_body.output_config.effort": "max"},
}

_ANTHROPIC_EFFORT_47_GROUP: dict[str, dict[str, Any]] = {
    "low": {"extra_body.output_config.effort": "low"},
    "medium": {"extra_body.output_config.effort": "medium"},
    "high": {"extra_body.output_config.effort": "high"},
    "xhigh": {"extra_body.output_config.effort": "xhigh"},
    "max": {"extra_body.output_config.effort": "max"},
}

# Gemini direct ``thinking_level``.
#   Gemini 3.1 Pro:         LOW / MEDIUM / HIGH
#   Gemini 3 Flash:         MINIMAL / LOW / MEDIUM / HIGH
#   Gemini 3.1 Flash-Lite:  MINIMAL / LOW / MEDIUM / HIGH
# (All per the 2026-04 Google AI for Developers docs.)
_GEMINI_THINKING_GROUP: dict[str, dict[str, Any]] = {
    "low": {"extra_body.google.thinking_config.thinking_level": "LOW"},
    "medium": {"extra_body.google.thinking_config.thinking_level": "MEDIUM"},
    "high": {"extra_body.google.thinking_config.thinking_level": "HIGH"},
}

_GEMINI_THINKING_GROUP_WITH_MINIMAL: dict[str, dict[str, Any]] = {
    "minimal": {"extra_body.google.thinking_config.thinking_level": "MINIMAL"},
    **_GEMINI_THINKING_GROUP,
}


# ── Built-in Presets ──────────────────────────────────────────

PRESETS: dict[str, dict[str, Any]] = {
    # ═══════════════════════════════════════════════════════
    #  OpenAI via Codex OAuth (ChatGPT subscription auth)
    #  reasoning_effort is a top-level field consumed directly
    #  by CodexOAuthProvider. GPT-5.4 additionally supports
    #  fast mode via ``service_tier="fast"`` (ChatGPT-sub only).
    # ═══════════════════════════════════════════════════════
    "gpt-5.4": {
        "provider": "codex",
        "model": "gpt-5.4",
        "max_context": 400000,
        "reasoning_effort": "xhigh",
        "variation_groups": {
            "reasoning": _CODEX_REASONING_GROUP,
            "speed": _CODEX_SPEED_GROUP,
        },
    },
    "gpt-5.3-codex": {
        "provider": "codex",
        "model": "gpt-5.3-codex",
        "max_context": 400000,
        "reasoning_effort": "xhigh",
        "variation_groups": {"reasoning": _CODEX_REASONING_GROUP},
    },
    "gpt-5.1": {
        "provider": "codex",
        "model": "gpt-5.1",
        "max_context": 400000,
        "reasoning_effort": "xhigh",
        "variation_groups": {"reasoning": _CODEX_REASONING_GROUP},
    },
    "gpt-4o-codex": {
        "provider": "codex",
        "model": "gpt-4o",
        "max_context": 128000,
        # gpt-4o is not a reasoning model.
    },
    "gpt-4o-mini-codex": {
        "provider": "codex",
        "model": "gpt-4o-mini",
        "max_context": 128000,
    },
    # ═══════════════════════════════════════════════════════
    #  OpenAI Direct API (-api suffix, api-key auth).
    #  reasoning.effort = minimal | low | medium | high | xhigh
    # ═══════════════════════════════════════════════════════
    "gpt-5.4-api": {
        "provider": "openai",
        "model": "gpt-5.4",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    "gpt-5.4-mini-api": {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    "gpt-5.4-nano-api": {
        "provider": "openai",
        "model": "gpt-5.4-nano",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    "gpt-5.3-codex-api": {
        "provider": "openai",
        "model": "gpt-5.3-codex",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    "gpt-5.1-api": {
        "provider": "openai",
        "model": "gpt-5.1",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OPENAI_REASONING_GROUP},
    },
    "gpt-4o-api": {
        "provider": "openai",
        "model": "gpt-4o",
        "max_context": 128000,
    },
    "gpt-4o-mini-api": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "max_context": 128000,
    },
    # ═══════════════════════════════════════════════════════
    #  OpenAI via OpenRouter (-or suffix).
    #  Uses OR context windows, not Codex's.
    # ═══════════════════════════════════════════════════════
    "gpt-5.4-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "max_context": 1050000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.4-mini-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4-mini",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.4-nano-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4-nano",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.3-codex-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.3-codex",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-5.1-or": {
        "provider": "openrouter",
        "model": "openai/gpt-5.1",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP_WITH_XHIGH},
    },
    "gpt-4o-or": {
        "provider": "openrouter",
        "model": "openai/gpt-4o",
        "max_context": 128000,
    },
    "gpt-4o-mini-or": {
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
        "max_context": 128000,
    },
    # ═══════════════════════════════════════════════════════
    #  Anthropic Claude Direct API (primary — non-OpenAI format,
    #  requires the dedicated ``anthropic`` backend_type client).
    #
    #  Adaptive thinking is the recommended mode for 4.6+ models:
    #    Opus 4.7:  effort = low / medium / high / xhigh / max
    #    Opus 4.6:  effort = low / medium / high / max
    #    Sonnet 4.6: effort = low / medium / high / max
    #  Fast mode is Opus-only (speed=fast + betas header).
    # ═══════════════════════════════════════════════════════
    "claude-opus-4.7": {
        "provider": "anthropic",
        "model": "claude-opus-4-7",
        "max_context": 1000000,
        # Opus 4.7 defaults ``thinking.display`` to ``"omitted"`` — we explicitly
        # opt in to summarized thinking so the UI can show the reasoning trace.
        # Fast mode is not exposed via Anthropic's OpenAI-compat layer; use the
        # ``claude-opus-4.7-or`` OpenRouter preset if you need it.
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
    #  xhigh is only honored by Opus 4.7.
    # ═══════════════════════════════════════════════════════
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
    "claude-sonnet-4.5-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.5",
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
    # Legacy 4.0 aliases kept for backward compat.
    "claude-sonnet-4-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4",
        "max_context": 200000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "claude-opus-4-or": {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4",
        "max_context": 200000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Google Gemini Direct API (primary — OpenAI-compat endpoint).
    #  Pro / Flash:      thinking_level = LOW / MEDIUM / HIGH
    #  Flash-Lite (3.1): thinking_level = MINIMAL / LOW / MEDIUM / HIGH
    # ═══════════════════════════════════════════════════════
    "gemini-3.1-pro": {
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
        "variation_groups": {"thinking": _GEMINI_THINKING_GROUP},
    },
    "gemini-3-flash": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
        # Flash supports the full set MINIMAL/LOW/MEDIUM/HIGH (verified
        # 2026-04 Google AI for Developers docs).
        "variation_groups": {"thinking": _GEMINI_THINKING_GROUP_WITH_MINIMAL},
    },
    "gemini-3.1-flash-lite": {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
        "variation_groups": {"thinking": _GEMINI_THINKING_GROUP_WITH_MINIMAL},
    },
    # ═══════════════════════════════════════════════════════
    #  Google Gemini via OpenRouter (-or suffix).
    # ═══════════════════════════════════════════════════════
    "gemini-3.1-pro-or": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-pro-preview",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "gemini-3-flash-or": {
        "provider": "openrouter",
        "model": "google/gemini-3-flash-preview",
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
    "nano-banana": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-image-preview",
        "max_context": 65536,
        # Image-generation model — reasoning doesn't apply.
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
    #  Qwen 3.5 / 3.6 series (OpenRouter only).
    # ═══════════════════════════════════════════════════════
    "qwen3.5-plus": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-plus-02-15",
        "max_context": 1000000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "qwen3.5-flash": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-flash-02-23",
        "max_context": 1000000,
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
    "qwen3-coder": {
        "provider": "openrouter",
        "model": "qwen/qwen3-coder",
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
    #  Moonshot Kimi K2.5 / K2-thinking (OpenRouter).
    #   K2.5:          configurable reasoning via OR unified.
    #   K2-thinking:   always-on thinking — no variation group.
    # ═══════════════════════════════════════════════════════
    "kimi-k2.5": {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k2.5",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "kimi-k2-thinking": {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k2-thinking",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  MiniMax (OpenRouter). Reasoning is mandatory on both
    #  endpoints as of April 2026 — no variation group.
    # ═══════════════════════════════════════════════════════
    "minimax-m2.7": {
        "provider": "openrouter",
        "model": "minimax/minimax-m2.7",
        "max_context": 204800,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "minimax-m2.5": {
        "provider": "openrouter",
        "model": "minimax/minimax-m2.5",
        "max_context": 197000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Xiaomi MiMo Direct API (primary — ``kt login mimo``).
    # ═══════════════════════════════════════════════════════
    "mimo-v2-pro": {
        "provider": "mimo",
        "model": "MiMo-V2-Pro",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "mimo-v2-flash": {
        "provider": "mimo",
        "model": "MiMo-V2-Flash",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  Xiaomi MiMo via OpenRouter (-or suffix).
    # ═══════════════════════════════════════════════════════
    "mimo-v2-pro-or": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2-pro",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "mimo-v2-flash-or": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2-flash",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  GLM (Z.ai, OpenRouter).
    # ═══════════════════════════════════════════════════════
    "glm-5": {
        "provider": "openrouter",
        "model": "z-ai/glm-5",
        "max_context": 80000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    "glm-5-turbo": {
        "provider": "openrouter",
        "model": "z-ai/glm-5-turbo",
        "max_context": 202752,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {"reasoning": _OR_REASONING_GROUP},
    },
    # ═══════════════════════════════════════════════════════
    #  xAI Grok series (OpenRouter).
    #   - grok-4:       reasoning mandatory and NOT configurable.
    #   - grok-4.20:    reasoning on/off only — not a variation scale.
    #   - grok-*-fast:  reasoning mandatory on the fast endpoints.
    #   - grok-3/3-mini: legacy, no reasoning.
    # ═══════════════════════════════════════════════════════
    "grok-4": {
        "provider": "openrouter",
        "model": "x-ai/grok-4",
        "max_context": 256000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-4.20": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.20",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-4.20-multi": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.20-multi-agent",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-4-fast": {
        "provider": "openrouter",
        "model": "x-ai/grok-4-fast",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-4.1-fast": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.1-fast",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-code-fast": {
        "provider": "openrouter",
        "model": "x-ai/grok-code-fast-1",
        "max_context": 256000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-3": {
        "provider": "openrouter",
        "model": "x-ai/grok-3",
        "max_context": 131072,
    },
    "grok-3-mini": {
        "provider": "openrouter",
        "model": "x-ai/grok-3-mini",
        "max_context": 131072,
    },
    # ═══════════════════════════════════════════════════════
    #  Mistral (OpenRouter).
    #  Most Mistral instruct / coding models are NOT reasoning
    #  models. Mistral Small 4 (2026-03) is the first to expose
    #  a ``reasoning_effort`` param — values are "none" and "high"
    #  only (no "low" / "medium" per Mistral docs).
    #  Magistral Small/Medium have always-on reasoning — no variation.
    # ═══════════════════════════════════════════════════════
    "mistral-large-3": {
        "provider": "openrouter",
        "model": "mistralai/mistral-large-2512",
        "max_context": 262144,
    },
    "mistral-medium-3.1": {
        "provider": "openrouter",
        "model": "mistralai/mistral-medium-3.1",
        "max_context": 131072,
    },
    "mistral-medium-3": {
        "provider": "openrouter",
        "model": "mistralai/mistral-medium-3",
        "max_context": 131072,
    },
    "mistral-small-4": {
        "provider": "openrouter",
        "model": "mistralai/mistral-small-2603",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        "variation_groups": {
            "reasoning": {
                "none": {"extra_body.reasoning.enabled": False},
                "high": {
                    "extra_body.reasoning.enabled": True,
                    "extra_body.reasoning.effort": "high",
                },
            }
        },
    },
    "mistral-small-3.2": {
        "provider": "openrouter",
        "model": "mistralai/mistral-small-3.2-24b-instruct",
        "max_context": 128000,
    },
    "magistral-medium": {
        "provider": "openrouter",
        "model": "mistralai/magistral-medium-2506",
        "max_context": 40960,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "magistral-small": {
        "provider": "openrouter",
        "model": "mistralai/magistral-small-2506",
        "max_context": 40000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # Non-reasoning Mistral specialists.
    "codestral": {
        "provider": "openrouter",
        "model": "mistralai/codestral-2508",
        "max_context": 256000,
    },
    "devstral-2": {
        "provider": "openrouter",
        "model": "mistralai/devstral-2512",
        "max_context": 262144,
    },
    "devstral-medium": {
        "provider": "openrouter",
        "model": "mistralai/devstral-medium",
        "max_context": 131072,
    },
    "devstral-small": {
        "provider": "openrouter",
        "model": "mistralai/devstral-small",
        "max_context": 131072,
    },
    "pixtral-large": {
        "provider": "openrouter",
        "model": "mistralai/pixtral-large-2411",
        "max_context": 131072,
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

# ── Aliases ────────────────────────────────────────────────────
# Three roles:
#   1. Short/friendly names for frequent picks (e.g. ``gpt5``, ``opus``).
#   2. Backward-compat for the pre-2026-04 preset naming scheme — the
#      old ``-direct`` / ``or-`` prefixed names are preserved so existing
#      user configs keep working.
#   3. Legacy model-name spellings (e.g. ``gpt-4o`` → ``gpt-4o-codex``).
ALIASES: dict[str, str] = {
    # ── Short / friendly names ──
    "gpt5": "gpt-5.4",
    "gpt54": "gpt-5.4",
    "gpt53": "gpt-5.3-codex",
    "gpt4o": "gpt-4o-codex",
    "gemini": "gemini-3.1-pro",
    "gemini-pro": "gemini-3.1-pro",
    "gemini-flash": "gemini-3-flash",
    "gemini-lite": "gemini-3.1-flash-lite",
    "claude": "claude-sonnet-4.6",
    "claude-sonnet": "claude-sonnet-4.6",
    "claude-opus": "claude-opus-4.7",
    "claude-haiku": "claude-haiku-4.5",
    "sonnet": "claude-sonnet-4.6",
    "opus": "claude-opus-4.7",
    "haiku": "claude-haiku-4.5",
    "gemma": "gemma-4-31b",
    "gemma-4": "gemma-4-31b",
    "qwen": "qwen3.5-plus",
    "qwen-coder": "qwen3-coder",
    "kimi": "kimi-k2.5",
    "minimax": "minimax-m2.7",
    "mimo": "mimo-v2-pro",
    "glm": "glm-5-turbo",
    "grok": "grok-4",
    "grok-fast": "grok-4-fast",
    "grok-code": "grok-code-fast",
    "mistral": "mistral-large-3",
    "mistral-large": "mistral-large-3",
    "mistral-medium": "mistral-medium-3.1",
    "mistral-small": "mistral-small-4",
    "magistral": "magistral-medium",
    "devstral": "devstral-2",
    "ministral": "ministral-3-14b",
    # ── Back-compat: pre-2026-04 preset names ──
    # OpenAI codex (moved gpt-4o → gpt-4o-codex for consistency).
    "gpt-4o": "gpt-4o-codex",
    "gpt-4o-mini": "gpt-4o-mini-codex",
    # OpenAI direct (was ``-direct`` → now ``-api``).
    "gpt-5.4-direct": "gpt-5.4-api",
    "gpt-5.4-mini-direct": "gpt-5.4-mini-api",
    "gpt-5.4-nano-direct": "gpt-5.4-nano-api",
    "gpt-5.3-codex-direct": "gpt-5.3-codex-api",
    "gpt-5.1-direct": "gpt-5.1-api",
    "gpt-4o-direct": "gpt-4o-api",
    "gpt-4o-mini-direct": "gpt-4o-mini-api",
    # OpenAI OR (was ``or-`` prefix → now ``-or`` suffix).
    "or-gpt-5.4": "gpt-5.4-or",
    "or-gpt-5.4-mini": "gpt-5.4-mini-or",
    "or-gpt-5.4-nano": "gpt-5.4-nano-or",
    "or-gpt-5.3-codex": "gpt-5.3-codex-or",
    "or-gpt-5.1": "gpt-5.1-or",
    "or-gpt-4o": "gpt-4o-or",
    "or-gpt-4o-mini": "gpt-4o-mini-or",
    # Anthropic direct (was ``-direct`` → now unsuffixed primary).
    "claude-opus-4.6-direct": "claude-opus-4.6",
    "claude-sonnet-4.6-direct": "claude-sonnet-4.6",
    "claude-haiku-4.5-direct": "claude-haiku-4.5",
    # Legacy 4.0 Anthropic (were unsuffixed OR → now ``-or``).
    "claude-sonnet-4": "claude-sonnet-4-or",
    "claude-opus-4": "claude-opus-4-or",
    # Gemini direct (was ``-direct`` → now unsuffixed primary).
    "gemini-3.1-pro-direct": "gemini-3.1-pro",
    "gemini-3-flash-direct": "gemini-3-flash",
    "gemini-3.1-flash-lite-direct": "gemini-3.1-flash-lite",
    # MiMo direct (was ``-direct`` → now unsuffixed primary).
    "mimo-v2-pro-direct": "mimo-v2-pro",
    "mimo-v2-flash-direct": "mimo-v2-flash",
}


# ── Package preset merging ───────────────────────────────────
_package_presets_merged: bool = False
_all_presets_cache: dict[str, dict[str, Any]] | None = None


def _merge_package_presets() -> dict[str, dict[str, Any]]:
    """Scan installed packages for llm_presets and merge into PRESETS.

    Package presets do NOT override built-in presets; they only add new entries.
    """
    global _package_presets_merged
    if _package_presets_merged:
        return {}

    _package_presets_merged = True
    merged: dict[str, dict[str, Any]] = {}

    try:
        for pkg in list_packages():
            for preset in pkg.get("llm_presets", []):
                if not isinstance(preset, dict):
                    continue
                preset_name = preset.get("name")
                if not preset_name:
                    continue
                if preset_name in PRESETS:
                    logger.debug(
                        "Package preset skipped (builtin exists)",
                        preset=preset_name,
                        package=pkg["name"],
                    )
                    continue
                if preset_name in merged:
                    logger.debug(
                        "Package preset skipped (duplicate)",
                        preset=preset_name,
                        package=pkg["name"],
                    )
                    continue
                # Build preset dict from the entry (exclude 'name' key)
                preset_data = {k: v for k, v in preset.items() if k != "name"}
                merged[preset_name] = preset_data
    except Exception as e:
        logger.debug("Failed to load package presets", error=str(e), exc_info=True)

    return merged


def get_all_presets() -> dict[str, dict[str, Any]]:
    """Return PRESETS merged with package presets, cached after first call."""
    global _all_presets_cache
    if _all_presets_cache is not None:
        return _all_presets_cache

    package_presets = _merge_package_presets()
    _all_presets_cache = {**PRESETS, **package_presets}
    return _all_presets_cache
