"""Canonical-name + alias tables for built-in LLM presets.

Map flat preset keys and user-facing aliases to canonical provider/name pairs.
"""

# Provider identity disambiguates flat keys whose public names drop route suffixes.
_CANONICAL_NAMES: dict[str, str] = {
    # OpenAI Direct API — ``-api`` suffix.
    "gpt-5.6-sol-api": "gpt-5.6-sol",
    "gpt-5.6-terra-api": "gpt-5.6-terra",
    "gpt-5.6-luna-api": "gpt-5.6-luna",
    "gpt-5.5-api": "gpt-5.5",
    "gpt-5.4-api": "gpt-5.4",
    "gpt-5.4-mini-api": "gpt-5.4-mini",
    "gpt-5.4-nano-api": "gpt-5.4-nano",
    # OpenAI via OpenRouter — ``-or`` suffix.
    "gpt-5.6-sol-or": "gpt-5.6-sol",
    "gpt-5.6-terra-or": "gpt-5.6-terra",
    "gpt-5.6-luna-or": "gpt-5.6-luna",
    "gpt-5.5-or": "gpt-5.5",
    "gpt-5.4-or": "gpt-5.4",
    "gpt-5.4-mini-or": "gpt-5.4-mini",
    "gpt-5.4-nano-or": "gpt-5.4-nano",
    # Anthropic Claude via OpenRouter — ``-or`` suffix.
    "claude-fable-5-or": "claude-fable-5",
    "claude-opus-4.8-or": "claude-opus-4.8",
    "claude-opus-4.7-or": "claude-opus-4.7",
    "claude-opus-4.6-or": "claude-opus-4.6",
    "claude-sonnet-5-or": "claude-sonnet-5",
    "claude-sonnet-4.6-or": "claude-sonnet-4.6",
    "claude-haiku-4.5-or": "claude-haiku-4.5",
    # Gemini via OpenRouter — ``-or`` suffix.
    "gemini-3.1-pro-or": "gemini-3.1-pro",
    "gemini-3.5-flash-or": "gemini-3.5-flash",
    "gemini-3.1-flash-lite-or": "gemini-3.1-flash-lite",
    # MiMo via OpenRouter — ``-or`` suffix.
    "mimo-v2.5-pro-or": "mimo-v2.5-pro",
    "mimo-v2.5-or": "mimo-v2.5",
    # GLM via OpenRouter — ``-or`` suffix.
    "glm-5.2-or": "glm-5.2",
}


# Aliases cover both convenient short names and backward-compatible identifiers.
ALIASES: dict[str, tuple[str, str]] = {
    # Common short names.
    "gpt5": ("codex", "gpt-5.5"),
    "gpt56": ("codex", "gpt-5.6-sol"),
    "sol": ("codex", "gpt-5.6-sol"),
    "terra": ("codex", "gpt-5.6-terra"),
    "luna": ("codex", "gpt-5.6-luna"),
    "gpt55": ("codex", "gpt-5.5"),
    "gpt54": ("codex", "gpt-5.4"),
    "gemini": ("gemini", "gemini-3.1-pro"),
    "gemini-pro": ("gemini", "gemini-3.1-pro"),
    "gemini-flash": ("gemini", "gemini-3.5-flash"),
    "gemini-lite": ("gemini", "gemini-3.1-flash-lite"),
    "claude": ("anthropic", "claude-sonnet-5"),
    "claude-sonnet": ("anthropic", "claude-sonnet-5"),
    "claude-opus": ("anthropic", "claude-opus-4.8"),
    "claude-haiku": ("anthropic", "claude-haiku-4.5"),
    "claude-fable": ("anthropic", "claude-fable-5"),
    "sonnet": ("anthropic", "claude-sonnet-5"),
    "opus": ("anthropic", "claude-opus-4.8"),
    "haiku": ("anthropic", "claude-haiku-4.5"),
    "fable": ("anthropic", "claude-fable-5"),
    "gemma": ("openrouter", "gemma-4-31b"),
    "gemma-4": ("openrouter", "gemma-4-31b"),
    "qwen": ("openrouter", "qwen3.7-max"),
    "qwen-max": ("openrouter", "qwen3.7-max"),
    "qwen-flash": ("openrouter", "qwen3.6-flash"),
    "qwen-coder": ("openrouter", "qwen3-coder-plus"),
    "kimi": ("openrouter", "kimi-k2.6"),
    "kimi-coder": ("openrouter", "kimi-k2.7-code"),
    "kimi-code": ("kimi-code", "kimi-for-coding"),
    "minimax": ("openrouter", "minimax-m3"),
    "mimo": ("mimo", "mimo-v2.5-pro"),
    "glm": ("openrouter", "glm-5.2"),
    "glm-code": ("glm-coding", "glm-5.2"),
    "glm-coding": ("glm-coding", "glm-5.2"),
    "grok": ("openrouter", "grok-4.5"),
    "grok-fast": ("openrouter", "grok-4.1-fast"),
    "grok-code": ("openrouter", "grok-code-fast"),
    "mistral": ("openrouter", "mistral-large-3"),
    "mistral-large": ("openrouter", "mistral-large-3"),
    "mistral-medium": ("openrouter", "mistral-medium-3.5"),
    "mistral-small": ("openrouter", "mistral-small-4"),
    "devstral": ("openrouter", "devstral-2"),
    "ministral": ("openrouter", "ministral-3-14b"),
    # Historical route-suffixed names.
    # OpenAI direct (``-direct`` / ``-api`` both → openai bare name).
    "gpt-5.6-sol-api": ("openai", "gpt-5.6-sol"),
    "gpt-5.6-terra-api": ("openai", "gpt-5.6-terra"),
    "gpt-5.6-luna-api": ("openai", "gpt-5.6-luna"),
    "gpt-5.5-api": ("openai", "gpt-5.5"),
    "gpt-5.4-api": ("openai", "gpt-5.4"),
    "gpt-5.4-mini-api": ("openai", "gpt-5.4-mini"),
    "gpt-5.4-nano-api": ("openai", "gpt-5.4-nano"),
    "gpt-5.5-direct": ("openai", "gpt-5.5"),
    "gpt-5.4-direct": ("openai", "gpt-5.4"),
    "gpt-5.4-mini-direct": ("openai", "gpt-5.4-mini"),
    "gpt-5.4-nano-direct": ("openai", "gpt-5.4-nano"),
    # OpenAI via OpenRouter (``or-`` prefix / ``-or`` suffix).
    "gpt-5.6-sol-or": ("openrouter", "gpt-5.6-sol"),
    "gpt-5.6-terra-or": ("openrouter", "gpt-5.6-terra"),
    "gpt-5.6-luna-or": ("openrouter", "gpt-5.6-luna"),
    "gpt-5.5-or": ("openrouter", "gpt-5.5"),
    "gpt-5.4-or": ("openrouter", "gpt-5.4"),
    "gpt-5.4-mini-or": ("openrouter", "gpt-5.4-mini"),
    "gpt-5.4-nano-or": ("openrouter", "gpt-5.4-nano"),
    "or-gpt-5.5": ("openrouter", "gpt-5.5"),
    "or-gpt-5.4": ("openrouter", "gpt-5.4"),
    "or-gpt-5.4-mini": ("openrouter", "gpt-5.4-mini"),
    "or-gpt-5.4-nano": ("openrouter", "gpt-5.4-nano"),
    # Anthropic direct (``-direct`` → bare under anthropic).
    "claude-opus-4.6-direct": ("anthropic", "claude-opus-4.6"),
    "claude-sonnet-4.6-direct": ("anthropic", "claude-sonnet-4.6"),
    "claude-haiku-4.5-direct": ("anthropic", "claude-haiku-4.5"),
    # Anthropic via OpenRouter.
    "claude-fable-5-or": ("openrouter", "claude-fable-5"),
    "claude-opus-4.8-or": ("openrouter", "claude-opus-4.8"),
    "claude-opus-4.7-or": ("openrouter", "claude-opus-4.7"),
    "claude-opus-4.6-or": ("openrouter", "claude-opus-4.6"),
    "claude-sonnet-5-or": ("openrouter", "claude-sonnet-5"),
    "claude-sonnet-4.6-or": ("openrouter", "claude-sonnet-4.6"),
    "claude-haiku-4.5-or": ("openrouter", "claude-haiku-4.5"),
    # Gemini direct (``-direct``) + OR.
    "gemini-3.1-pro-direct": ("gemini", "gemini-3.1-pro"),
    "gemini-3.5-flash-direct": ("gemini", "gemini-3.5-flash"),
    "gemini-3.1-flash-lite-direct": ("gemini", "gemini-3.1-flash-lite"),
    "gemini-3.1-pro-or": ("openrouter", "gemini-3.1-pro"),
    "gemini-3.5-flash-or": ("openrouter", "gemini-3.5-flash"),
    "gemini-3.1-flash-lite-or": ("openrouter", "gemini-3.1-flash-lite"),
    # MiMo direct + OR.
    "mimo-v2.5-pro-direct": ("mimo", "mimo-v2.5-pro"),
    "mimo-v2.5-direct": ("mimo", "mimo-v2.5"),
    "mimo-v2.5-pro-or": ("openrouter", "mimo-v2.5-pro"),
    "mimo-v2.5-or": ("openrouter", "mimo-v2.5"),
    # GLM direct + OR.
    "glm-5.2-coding": ("glm-coding", "glm-5.2"),
    "glm-5.2-1m-coding": ("glm-coding", "glm-5.2-1m"),
    "glm-5.2-or": ("openrouter", "glm-5.2"),
}
