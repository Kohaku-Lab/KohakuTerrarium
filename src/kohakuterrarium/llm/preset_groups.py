"""Reusable variation-group blocks for built-in LLM presets.

Split out of :mod:`presets` to keep that module under the file-size
guard (same treatment as :mod:`preset_aliases`). Each block declares the
canonical knob shape once — presets inline them via ``**`` splat or
direct reference — which avoids copy-paste drift across entries.

Values were researched against the relevant provider docs (effective
2026-07-10); per-block provenance notes inline.
"""

from typing import Any

# Codex OAuth (ChatGPT-subscription): top-level ``reasoning_effort`` field.
# Per the 2026-07 Codex models docs the effort scale is
# ``low | medium | high | xhigh`` (``medium`` is the default; ``none`` is
# no longer listed on the Codex side — it remains a direct-API value).
_CODEX_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "low": {"reasoning_effort": "low"},
    "medium": {"reasoning_effort": "medium"},
    "high": {"reasoning_effort": "high"},
    "xhigh": {"reasoning_effort": "xhigh"},
}

# GPT-5.6 adds ``max`` and, on Sol/Terra only, ``ultra`` ("maximum
# reasoning with automatic task delegation"). Verified against the Codex
# CLI model catalog (codex-rs/models-manager/models.json, 2026-07-10):
#   Sol / Terra: low | medium | high | xhigh | max | ultra
#   Luna:        low | medium | high | xhigh | max   (no ultra)
#
# ``ultra`` is NOT a wire value — the API 400s on the literal string
# (``Invalid value: 'ultra'``). The Codex CLI rewrites Ultra → Max before
# sending (core/src/client.rs ``reasoning_effort_for_request``) and uses
# the ultra *setting* only to flip its client-side multi-agent mode to
# "proactive" (core/src/session/multi_agents.rs). We mirror that: the
# ``ultra`` option sends ``max``; the delegation half lives in the Codex
# harness and cannot be requested via the API.
_GPT56_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "low": {"reasoning_effort": "low"},
    "medium": {"reasoning_effort": "medium"},
    "high": {"reasoning_effort": "high"},
    "xhigh": {"reasoning_effort": "xhigh"},
    "max": {"reasoning_effort": "max"},
}

_GPT56_LUNA_REASONING_GROUP: dict[str, dict[str, Any]] = {
    key: patch for key, patch in _GPT56_REASONING_GROUP.items() if key != "ultra"
}

# Codex "fast mode" on GPT-5.4 / GPT-5.5 / GPT-5.6.
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

# OpenAI direct API reasoning: extra_body.reasoning.effort. Full scale per
# the 2026-07 GPT-5.4/5.5 docs: ``none | low | medium | high | xhigh``.
# OpenAI's docs use ``none`` (not ``minimal`` — that's OpenRouter's unified
# name). Defaults: ``medium`` on gpt-5.5, ``none`` on the gpt-5.4 family.
_OPENAI_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "none": {"extra_body.reasoning.effort": "none"},
    "low": {"extra_body.reasoning.effort": "low"},
    "medium": {"extra_body.reasoning.effort": "medium"},
    "high": {"extra_body.reasoning.effort": "high"},
    "xhigh": {"extra_body.reasoning.effort": "xhigh"},
}

# GPT-5.6 direct API: the 5.6 model guide lists
# ``none | low | medium | high | xhigh | max`` (no ``minimal``; ``max`` is
# "for the hardest quality-first workloads"). ``ultra`` is Codex-only —
# it is NOT accepted by the direct API.
_OPENAI_56_REASONING_GROUP: dict[str, dict[str, Any]] = {
    **_OPENAI_REASONING_GROUP,
    "max": {"extra_body.reasoning.effort": "max"},
}

# OpenRouter unified reasoning. ``xhigh`` is only accepted by recent
# frontier models (GPT-5.4/5.5, Claude Opus 4.7+/Sonnet 5/Fable 5,
# GLM-5.2, …) — most models silently clamp to ``high``. Including it in
# the common block is fine: per-model behavior is the user's concern.
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

# GPT-5.6 via OpenRouter: OR's unified ``reasoning.effort`` scale is
# ``max > xhigh > high > medium > low > minimal > none`` (the newest
# ``max`` level exists only on the nested ``reasoning.effort``, NOT the
# legacy top-level ``reasoning_effort`` enum), and OpenAI effort values
# pass through natively — ``xhigh``+ is supported on gpt-5.2 and newer.
_OR_REASONING_GROUP_56: dict[str, dict[str, Any]] = {
    **_OR_REASONING_GROUP_WITH_XHIGH,
    "max": {"extra_body.reasoning.effort": "max"},
}

# On/off toggle via OpenRouter's unified param — for hybrid-thinking models
# whose knob is binary (MiMo 2.5, MiniMax M3, Grok 4.1 Fast) rather than an
# effort scale.
_OR_REASONING_TOGGLE_GROUP: dict[str, dict[str, Any]] = {
    "off": {"extra_body.reasoning.enabled": False},
    "on": {"extra_body.reasoning.enabled": True},
}

# Anthropic direct (native Anthropic-compatible Messages API).
#
# The built-in ``anthropic`` provider uses ``backend_type=anthropic`` and the
# official ``anthropic`` SDK. Claude-specific request fields such as
# ``thinking`` and ``output_config`` are passed through from ``extra_body``.
# Effort availability by model (2026-07 docs):
#   - Opus 4.6 / Sonnet 4.6:                  low / medium / high / max
#   - Opus 4.7 / 4.8, Sonnet 5, Fable 5:      low / medium / high / xhigh / max
# Fast mode (Opus 4.7/4.8) requires beta headers and is not enabled here.
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

# Gemini direct ``thinking_level`` (enum — replaced ``thinking_budget``).
#   Gemini 3.1 Pro:         LOW / MEDIUM / HIGH        (default HIGH)
#   Gemini 3.5 Flash:       MINIMAL / LOW / MEDIUM / HIGH  (default MEDIUM)
#   Gemini 3.1 Flash-Lite:  MINIMAL / LOW / MEDIUM     (default MINIMAL)
# (All per the 2026-07 Google AI for Developers docs.)
_GEMINI_THINKING_GROUP: dict[str, dict[str, Any]] = {
    "low": {"extra_body.google.thinking_config.thinking_level": "LOW"},
    "medium": {"extra_body.google.thinking_config.thinking_level": "MEDIUM"},
    "high": {"extra_body.google.thinking_config.thinking_level": "HIGH"},
}

_GEMINI_THINKING_GROUP_WITH_MINIMAL: dict[str, dict[str, Any]] = {
    "minimal": {"extra_body.google.thinking_config.thinking_level": "MINIMAL"},
    **_GEMINI_THINKING_GROUP,
}

# Flash-Lite does not accept HIGH.
_GEMINI_THINKING_LITE_GROUP: dict[str, dict[str, Any]] = {
    "minimal": {"extra_body.google.thinking_config.thinking_level": "MINIMAL"},
    "low": {"extra_body.google.thinking_config.thinking_level": "LOW"},
    "medium": {"extra_body.google.thinking_config.thinking_level": "MEDIUM"},
}

# Xiaomi MiMo 2.5 direct API: hybrid thinking is a binary toggle
# ``thinking.type = enabled | disabled`` (default disabled). There is no
# effort scale on Xiaomi's own platform — third-party ``reasoning_effort``
# shims are not official.
_MIMO_THINKING_GROUP: dict[str, dict[str, Any]] = {
    "off": {"extra_body.thinking.type": "disabled"},
    "on": {"extra_body.thinking.type": "enabled"},
}

# Mistral hybrid models (Medium 3.5, Small 4): ``reasoning_effort`` accepts
# ``none | low | medium | high`` as of 2026-03 (Small 4 launched the scale;
# it was previously none/high only). Routed through OR's unified param.
_MISTRAL_REASONING_GROUP: dict[str, dict[str, Any]] = {
    "none": {"extra_body.reasoning.enabled": False},
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

# GLM-5.2 exposes exactly two thinking-effort levels: high / xhigh.
_GLM_EFFORT_GROUP: dict[str, dict[str, Any]] = {
    "high": {"extra_body.reasoning.effort": "high"},
    "xhigh": {"extra_body.reasoning.effort": "xhigh"},
}

# Grok 4.5: effort = low / medium / high (default high); reasoning cannot
# be disabled.
_GROK_EFFORT_GROUP: dict[str, dict[str, Any]] = {
    "low": {"extra_body.reasoning.effort": "low"},
    "medium": {"extra_body.reasoning.effort": "medium"},
    "high": {"extra_body.reasoning.effort": "high"},
}
