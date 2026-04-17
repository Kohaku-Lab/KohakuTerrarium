"""
Built-in LLM presets and model aliases.

A preset references a **provider by name** (codex, openai, openrouter,
anthropic, gemini, mimo, …). The provider owns the backend_type, base_url,
and api_key_env. Presets only carry model-facing metadata (model id, context
window, reasoning effort, extra_body, …).
"""

from typing import Any

from kohakuterrarium.packages import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# ── Built-in Presets ──────────────────────────────────────────

PRESETS: dict[str, dict[str, Any]] = {
    # ═══════════════════════════════════════════════════════
    #  OpenAI via Codex OAuth (ChatGPT subscription auth)
    # ═══════════════════════════════════════════════════════
    "gpt-5.4": {
        "provider": "codex",
        "model": "gpt-5.4",
        "max_context": 272000,
        "reasoning_effort": "high",
    },
    "gpt-5.3-codex": {
        "provider": "codex",
        "model": "gpt-5.3-codex",
        "max_context": 272000,
        "reasoning_effort": "high",
    },
    "gpt-5.1": {
        "provider": "codex",
        "model": "gpt-5.1",
        "max_context": 272000,
        "reasoning_effort": "high",
    },
    "gpt-4o": {
        "provider": "codex",
        "model": "gpt-4o",
        "max_context": 128000,
        "reasoning_effort": "high",
    },
    "gpt-4o-mini": {
        "provider": "codex",
        "model": "gpt-4o-mini",
        "max_context": 128000,
        "reasoning_effort": "high",
    },
    # ═══════════════════════════════════════════════════════
    #  OpenAI Direct API (api key auth)
    #  reasoning_effort: none | low | medium | high | xhigh
    # ═══════════════════════════════════════════════════════
    "gpt-5.4-direct": {
        "provider": "openai",
        "model": "gpt-5.4",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gpt-5.4-mini-direct": {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gpt-5.4-nano-direct": {
        "provider": "openai",
        "model": "gpt-5.4-nano",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gpt-5.3-codex-direct": {
        "provider": "openai",
        "model": "gpt-5.3-codex",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gpt-5.1-direct": {
        "provider": "openai",
        "model": "gpt-5.1",
        "max_context": 272000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gpt-4o-direct": {
        "provider": "openai",
        "model": "gpt-4o",
        "max_context": 128000,
    },
    "gpt-4o-mini-direct": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "max_context": 128000,
    },
    # ═══════════════════════════════════════════════════════
    #  OpenAI via OpenRouter (uses OR context windows, not Codex)
    # ═══════════════════════════════════════════════════════
    "or-gpt-5.4": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "max_context": 1050000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "or-gpt-5.4-mini": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4-mini",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "or-gpt-5.4-nano": {
        "provider": "openrouter",
        "model": "openai/gpt-5.4-nano",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "or-gpt-5.3-codex": {
        "provider": "openrouter",
        "model": "openai/gpt-5.3-codex",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "or-gpt-5.1": {
        "provider": "openrouter",
        "model": "openai/gpt-5.1",
        "max_context": 400000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "or-gpt-4o": {
        "provider": "openrouter",
        "model": "openai/gpt-4o",
        "max_context": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "or-gpt-4o-mini": {
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
        "max_context": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Anthropic Claude via OpenRouter (OpenAI-compat API)
    # ═══════════════════════════════════════════════════════
    "claude-opus-4.6": {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4.6",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
    },
    "claude-sonnet-4.6": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
    },
    "claude-sonnet-4.5": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.5",
        "max_context": 1000000,
        "extra_body": {
            "reasoning": {"enabled": True, "effort": "high"},
            "cache_control": {"type": "ephemeral"},
        },
    },
    "claude-haiku-4.5": {
        "provider": "openrouter",
        "model": "anthropic/claude-haiku-4.5",
        "max_context": 200000,
        "extra_body": {
            "cache_control": {"type": "ephemeral"},
        },
    },
    # Legacy aliases kept for backward compat
    "claude-sonnet-4": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4",
        "max_context": 200000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "claude-opus-4": {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4",
        "max_context": 200000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Anthropic Claude Direct API (non-OpenAI format)
    #  NOTE: backend_type="anthropic" requires dedicated client,
    #  not the OpenAI-compat provider. Adaptive thinking is
    #  the recommended mode for 4.6 models:
    #    thinking: {type: "adaptive"}, effort: low|medium|high|max
    #  Fast mode (Opus 4.6 only):
    #    speed="fast", betas=["fast-mode-2026-02-01"]
    # ═══════════════════════════════════════════════════════
    "claude-opus-4.6-direct": {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "max_context": 1000000,
    },
    "claude-sonnet-4.6-direct": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "max_context": 1000000,
    },
    "claude-haiku-4.5-direct": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "max_context": 200000,
    },
    # ═══════════════════════════════════════════════════════
    #  Google Gemini via OpenRouter
    # ═══════════════════════════════════════════════════════
    "gemini-3.1-pro": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-pro-preview",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gemini-3-flash": {
        "provider": "openrouter",
        "model": "google/gemini-3-flash-preview",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gemini-3.1-flash-lite": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite-preview",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "nano-banana": {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-image-preview",
        "max_context": 65536,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Google Gemini Direct API (OpenAI-compat endpoint)
    # ═══════════════════════════════════════════════════════
    "gemini-3.1-pro-direct": {
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
    },
    "gemini-3-flash-direct": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
    },
    "gemini-3.1-flash-lite-direct": {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite-preview",
        "max_context": 1048576,
        "extra_body": {"google": {"thinking_config": {"thinking_level": "HIGH"}}},
    },
    # ═══════════════════════════════════════════════════════
    #  Gemma 4 (open models, via OpenRouter)
    # ═══════════════════════════════════════════════════════
    "gemma-4-31b": {
        "provider": "openrouter",
        "model": "google/gemma-4-31b-it",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "gemma-4-26b": {
        "provider": "openrouter",
        "model": "google/gemma-4-26b-a4b-it",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Qwen 3.5 / 3.6 series (via OpenRouter)
    # ═══════════════════════════════════════════════════════
    "qwen3.5-plus": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-plus-02-15",
        "max_context": 1000000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "qwen3.5-flash": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-flash-02-23",
        "max_context": 1000000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "qwen3.5-397b": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-397b-a17b",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "qwen3.5-27b": {
        "provider": "openrouter",
        "model": "qwen/qwen3.5-27b",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "qwen3-coder": {
        "provider": "openrouter",
        "model": "qwen/qwen3-coder",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "qwen3-coder-plus": {
        "provider": "openrouter",
        "model": "qwen/qwen3-coder-plus",
        "max_context": 1000000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Moonshot Kimi K2.5 / K2 (via OpenRouter)
    #  K2.5 has built-in thinking (enabled by default).
    #  Disable via reasoning param if needed.
    # ═══════════════════════════════════════════════════════
    "kimi-k2.5": {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k2.5",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "kimi-k2-thinking": {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k2-thinking",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  MiniMax (via OpenRouter)
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
    #  Xiaomi MiMo (via OpenRouter)
    # ═══════════════════════════════════════════════════════
    "mimo-v2-pro": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2-pro",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "mimo-v2-flash": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2-flash",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Xiaomi MiMo Direct API (kt login mimo)
    # ═══════════════════════════════════════════════════════
    "mimo-v2-pro-direct": {
        "provider": "mimo",
        "model": "MiMo-V2-Pro",
        "max_context": 1048576,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "mimo-v2-flash-direct": {
        "provider": "mimo",
        "model": "MiMo-V2-Flash",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  GLM (Z.ai, via OpenRouter)
    # ═══════════════════════════════════════════════════════
    "glm-5": {
        "provider": "openrouter",
        "model": "z-ai/glm-5",
        "max_context": 80000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "glm-5-turbo": {
        "provider": "openrouter",
        "model": "z-ai/glm-5-turbo",
        "max_context": 202752,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  xAI Grok series (via OpenRouter)
    # ═══════════════════════════════════════════════════════
    "grok-4": {
        "provider": "openrouter",
        "model": "x-ai/grok-4",
        "max_context": 256000,
        # Reasoning is mandatory and not configurable
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-4.20": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.20",
        "max_context": 272000,  # 2M model, use 272K budget
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
        "max_context": 272000,  # 2M model, use 272K budget
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
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "grok-3-mini": {
        "provider": "openrouter",
        "model": "x-ai/grok-3-mini",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # ═══════════════════════════════════════════════════════
    #  Mistral series (via OpenRouter)
    #  Large = flagship, Small 4 = reasoning, Codestral/Devstral = coding
    # ═══════════════════════════════════════════════════════
    "mistral-large-3": {
        "provider": "openrouter",
        "model": "mistralai/mistral-large-2512",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "mistral-medium-3.1": {
        "provider": "openrouter",
        "model": "mistralai/mistral-medium-3.1",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "mistral-medium-3": {
        "provider": "openrouter",
        "model": "mistralai/mistral-medium-3",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "mistral-small-4": {
        "provider": "openrouter",
        "model": "mistralai/mistral-small-2603",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "mistral-small-3.2": {
        "provider": "openrouter",
        "model": "mistralai/mistral-small-3.2-24b-instruct",
        "max_context": 128000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # Magistral: dedicated reasoning models
    "magistral-medium": {
        "provider": "openrouter",
        "model": "mistralai/magistral-medium-2506",
        "max_context": 40960,
        # Reasoning is always-on (mandatory)
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "magistral-small": {
        "provider": "openrouter",
        "model": "mistralai/magistral-small-2506",
        "max_context": 40000,
        # Reasoning is always-on (mandatory)
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # Coding specialists
    "codestral": {
        "provider": "openrouter",
        "model": "mistralai/codestral-2508",
        "max_context": 256000,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "devstral-2": {
        "provider": "openrouter",
        "model": "mistralai/devstral-2512",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "devstral-medium": {
        "provider": "openrouter",
        "model": "mistralai/devstral-medium",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "devstral-small": {
        "provider": "openrouter",
        "model": "mistralai/devstral-small",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # Multimodal
    "pixtral-large": {
        "provider": "openrouter",
        "model": "mistralai/pixtral-large-2411",
        "max_context": 131072,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    # Small/edge models
    "ministral-3-14b": {
        "provider": "openrouter",
        "model": "mistralai/ministral-14b-2512",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
    "ministral-3-8b": {
        "provider": "openrouter",
        "model": "mistralai/ministral-8b-2512",
        "max_context": 262144,
        "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
    },
}

# Aliases: short names -> canonical preset names
ALIASES: dict[str, str] = {
    # OpenAI
    "gpt5": "gpt-5.4",
    "gpt54": "gpt-5.4",
    "gpt53": "gpt-5.3-codex",
    "gpt4o": "gpt-4o",
    # Gemini
    "gemini": "gemini-3.1-pro",
    "gemini-pro": "gemini-3.1-pro",
    "gemini-flash": "gemini-3-flash",
    "gemini-lite": "gemini-3.1-flash-lite",
    # Claude (via OpenRouter)
    "claude": "claude-sonnet-4.6",
    "claude-sonnet": "claude-sonnet-4.6",
    "claude-opus": "claude-opus-4.6",
    "claude-haiku": "claude-haiku-4.5",
    "sonnet": "claude-sonnet-4.6",
    "opus": "claude-opus-4.6",
    "haiku": "claude-haiku-4.5",
    # Gemma
    "gemma": "gemma-4-31b",
    "gemma-4": "gemma-4-31b",
    # Qwen
    "qwen": "qwen3.5-plus",
    "qwen-coder": "qwen3-coder",
    # Kimi
    "kimi": "kimi-k2.5",
    # MiniMax
    "minimax": "minimax-m2.7",
    # MiMo
    "mimo": "mimo-v2-pro",
    # GLM
    "glm": "glm-5-turbo",
    # Grok
    "grok": "grok-4",
    "grok-fast": "grok-4-fast",
    "grok-code": "grok-code-fast",
    # Mistral
    "mistral": "mistral-large-3",
    "mistral-large": "mistral-large-3",
    "mistral-medium": "mistral-medium-3.1",
    "mistral-small": "mistral-small-4",
    "magistral": "magistral-medium",
    "devstral": "devstral-2",
    "ministral": "ministral-3-14b",
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
