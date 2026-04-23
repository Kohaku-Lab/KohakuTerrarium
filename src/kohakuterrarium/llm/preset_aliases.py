"""Canonical-name + alias tables for built-in LLM presets.

Split out of :mod:`presets` to keep that module under the 1000-line
hard cap. The tables define two mappings:

``_CANONICAL_NAMES``
    Legacy preset key (``gpt-5.4-api`` / ``gpt-5.4-or`` / …) → bare
    canonical name under the new ``(provider, name)`` hierarchy. Used
    when transforming the flat ``PRESETS`` dict into the nested view
    that the rest of the codebase consumes.

``ALIASES``
    Free-form alias (short friendly names, pre-2026 preset names)
    → ``(provider, canonical_name)`` tuple. Used by
    :func:`kohakuterrarium.llm.profiles.resolve_controller_llm` to
    let user configs and CLI input keep referencing old identifiers
    after the suffix clean-up.
"""

# ── Canonical name mapping ────────────────────────────────────
#
# The flat ``PRESETS`` dict in :mod:`presets` is keyed by legacy
# disambiguation names (``gpt-5.4-api`` / ``gpt-5.4-or`` /
# ``gpt-4o-codex`` / …) because Python dict keys must be unique.
# The *canonical* bare name — the name users see in the UI, the CLI,
# and when they type ``controller.llm: gpt-5.4`` — drops those
# suffixes. Disambiguation is provided by the preset's ``provider``
# field.
#
# This table only contains legacy entries whose canonical name differs
# from their dict key. Entries not listed here keep their key as their
# canonical name (e.g. ``qwen3.5-plus``, ``grok-4``).
_CANONICAL_NAMES: dict[str, str] = {
    # OpenAI Codex (ChatGPT-subscription) — was suffixed to distinguish from -api.
    "gpt-4o-codex": "gpt-4o",
    "gpt-4o-mini-codex": "gpt-4o-mini",
    # OpenAI Direct API — ``-api`` suffix.
    "gpt-5.4-api": "gpt-5.4",
    "gpt-5.4-mini-api": "gpt-5.4-mini",
    "gpt-5.4-nano-api": "gpt-5.4-nano",
    "gpt-5.3-codex-api": "gpt-5.3-codex",
    "gpt-5.1-api": "gpt-5.1",
    "gpt-4o-api": "gpt-4o",
    "gpt-4o-mini-api": "gpt-4o-mini",
    # OpenAI via OpenRouter — ``-or`` suffix.
    "gpt-5.4-or": "gpt-5.4",
    "gpt-5.4-mini-or": "gpt-5.4-mini",
    "gpt-5.4-nano-or": "gpt-5.4-nano",
    "gpt-5.3-codex-or": "gpt-5.3-codex",
    "gpt-5.1-or": "gpt-5.1",
    "gpt-4o-or": "gpt-4o",
    "gpt-4o-mini-or": "gpt-4o-mini",
    # Anthropic Claude via OpenRouter — ``-or`` suffix.
    "claude-opus-4.7-or": "claude-opus-4.7",
    "claude-opus-4.6-or": "claude-opus-4.6",
    "claude-sonnet-4.6-or": "claude-sonnet-4.6",
    "claude-sonnet-4.5-or": "claude-sonnet-4.5",
    "claude-haiku-4.5-or": "claude-haiku-4.5",
    "claude-sonnet-4-or": "claude-sonnet-4",
    "claude-opus-4-or": "claude-opus-4",
    # Gemini via OpenRouter — ``-or`` suffix.
    "gemini-3.1-pro-or": "gemini-3.1-pro",
    "gemini-3-flash-or": "gemini-3-flash",
    "gemini-3.1-flash-lite-or": "gemini-3.1-flash-lite",
    # MiMo via OpenRouter — ``-or`` suffix.
    "mimo-v2-pro-or": "mimo-v2-pro",
    "mimo-v2-flash-or": "mimo-v2-flash",
}


# ── Aliases ────────────────────────────────────────────────────
# Two roles:
#   1. Short/friendly names for frequent picks (``gpt5``, ``opus``).
#   2. Backward-compat for pre-2026 preset names — legacy identifiers
#      resolve to their ``(provider, canonical_name)`` pair.
ALIASES: dict[str, tuple[str, str]] = {
    # ── Short / friendly names ──
    "gpt5": ("codex", "gpt-5.4"),
    "gpt54": ("codex", "gpt-5.4"),
    "gpt53": ("codex", "gpt-5.3-codex"),
    "gpt4o": ("codex", "gpt-4o"),
    "gemini": ("gemini", "gemini-3.1-pro"),
    "gemini-pro": ("gemini", "gemini-3.1-pro"),
    "gemini-flash": ("gemini", "gemini-3-flash"),
    "gemini-lite": ("gemini", "gemini-3.1-flash-lite"),
    "claude": ("anthropic", "claude-sonnet-4.6"),
    "claude-sonnet": ("anthropic", "claude-sonnet-4.6"),
    "claude-opus": ("anthropic", "claude-opus-4.7"),
    "claude-haiku": ("anthropic", "claude-haiku-4.5"),
    "sonnet": ("anthropic", "claude-sonnet-4.6"),
    "opus": ("anthropic", "claude-opus-4.7"),
    "haiku": ("anthropic", "claude-haiku-4.5"),
    "gemma": ("openrouter", "gemma-4-31b"),
    "gemma-4": ("openrouter", "gemma-4-31b"),
    "qwen": ("openrouter", "qwen3.5-plus"),
    "qwen-coder": ("openrouter", "qwen3-coder"),
    "kimi": ("openrouter", "kimi-k2.5"),
    "minimax": ("openrouter", "minimax-m2.7"),
    "mimo": ("mimo", "mimo-v2-pro"),
    "glm": ("openrouter", "glm-5-turbo"),
    "grok": ("openrouter", "grok-4"),
    "grok-fast": ("openrouter", "grok-4-fast"),
    "grok-code": ("openrouter", "grok-code-fast"),
    "mistral": ("openrouter", "mistral-large-3"),
    "mistral-large": ("openrouter", "mistral-large-3"),
    "mistral-medium": ("openrouter", "mistral-medium-3.1"),
    "mistral-small": ("openrouter", "mistral-small-4"),
    "magistral": ("openrouter", "magistral-medium"),
    "devstral": ("openrouter", "devstral-2"),
    "ministral": ("openrouter", "ministral-3-14b"),
    # ── Back-compat: pre-2026-04 preset names ──
    # OpenAI codex.
    "gpt-4o-codex": ("codex", "gpt-4o"),
    "gpt-4o-mini-codex": ("codex", "gpt-4o-mini"),
    # OpenAI direct (``-direct`` / ``-api`` both → openai bare name).
    "gpt-5.4-api": ("openai", "gpt-5.4"),
    "gpt-5.4-mini-api": ("openai", "gpt-5.4-mini"),
    "gpt-5.4-nano-api": ("openai", "gpt-5.4-nano"),
    "gpt-5.3-codex-api": ("openai", "gpt-5.3-codex"),
    "gpt-5.1-api": ("openai", "gpt-5.1"),
    "gpt-4o-api": ("openai", "gpt-4o"),
    "gpt-4o-mini-api": ("openai", "gpt-4o-mini"),
    "gpt-5.4-direct": ("openai", "gpt-5.4"),
    "gpt-5.4-mini-direct": ("openai", "gpt-5.4-mini"),
    "gpt-5.4-nano-direct": ("openai", "gpt-5.4-nano"),
    "gpt-5.3-codex-direct": ("openai", "gpt-5.3-codex"),
    "gpt-5.1-direct": ("openai", "gpt-5.1"),
    "gpt-4o-direct": ("openai", "gpt-4o"),
    "gpt-4o-mini-direct": ("openai", "gpt-4o-mini"),
    # OpenAI via OpenRouter (``or-`` prefix / ``-or`` suffix).
    "gpt-5.4-or": ("openrouter", "gpt-5.4"),
    "gpt-5.4-mini-or": ("openrouter", "gpt-5.4-mini"),
    "gpt-5.4-nano-or": ("openrouter", "gpt-5.4-nano"),
    "gpt-5.3-codex-or": ("openrouter", "gpt-5.3-codex"),
    "gpt-5.1-or": ("openrouter", "gpt-5.1"),
    "gpt-4o-or": ("openrouter", "gpt-4o"),
    "gpt-4o-mini-or": ("openrouter", "gpt-4o-mini"),
    "or-gpt-5.4": ("openrouter", "gpt-5.4"),
    "or-gpt-5.4-mini": ("openrouter", "gpt-5.4-mini"),
    "or-gpt-5.4-nano": ("openrouter", "gpt-5.4-nano"),
    "or-gpt-5.3-codex": ("openrouter", "gpt-5.3-codex"),
    "or-gpt-5.1": ("openrouter", "gpt-5.1"),
    "or-gpt-4o": ("openrouter", "gpt-4o"),
    "or-gpt-4o-mini": ("openrouter", "gpt-4o-mini"),
    # Anthropic direct (``-direct`` → bare under anthropic).
    "claude-opus-4.6-direct": ("anthropic", "claude-opus-4.6"),
    "claude-sonnet-4.6-direct": ("anthropic", "claude-sonnet-4.6"),
    "claude-haiku-4.5-direct": ("anthropic", "claude-haiku-4.5"),
    # Anthropic via OpenRouter.
    "claude-opus-4.7-or": ("openrouter", "claude-opus-4.7"),
    "claude-opus-4.6-or": ("openrouter", "claude-opus-4.6"),
    "claude-sonnet-4.6-or": ("openrouter", "claude-sonnet-4.6"),
    "claude-sonnet-4.5-or": ("openrouter", "claude-sonnet-4.5"),
    "claude-haiku-4.5-or": ("openrouter", "claude-haiku-4.5"),
    "claude-sonnet-4-or": ("openrouter", "claude-sonnet-4"),
    "claude-opus-4-or": ("openrouter", "claude-opus-4"),
    "claude-sonnet-4": ("openrouter", "claude-sonnet-4"),
    "claude-opus-4": ("openrouter", "claude-opus-4"),
    # Gemini direct (``-direct``) + OR.
    "gemini-3.1-pro-direct": ("gemini", "gemini-3.1-pro"),
    "gemini-3-flash-direct": ("gemini", "gemini-3-flash"),
    "gemini-3.1-flash-lite-direct": ("gemini", "gemini-3.1-flash-lite"),
    "gemini-3.1-pro-or": ("openrouter", "gemini-3.1-pro"),
    "gemini-3-flash-or": ("openrouter", "gemini-3-flash"),
    "gemini-3.1-flash-lite-or": ("openrouter", "gemini-3.1-flash-lite"),
    # MiMo direct + OR.
    "mimo-v2-pro-direct": ("mimo", "mimo-v2-pro"),
    "mimo-v2-flash-direct": ("mimo", "mimo-v2-flash"),
    "mimo-v2-pro-or": ("openrouter", "mimo-v2-pro"),
    "mimo-v2-flash-or": ("openrouter", "mimo-v2-flash"),
}
