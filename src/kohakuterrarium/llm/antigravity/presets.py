"""Model limits and effort routing for the agy CLI agent catalog."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """An agy model family or a fixed wire model with its supported settings."""

    context_windows: tuple[int, ...]
    max_output: int
    thinking: dict[str, dict]
    wire_model: str = ""

    @property
    def max_context(self) -> int:
        return self.context_windows[0]

    @property
    def efforts(self) -> tuple[str, ...]:
        return tuple(self.thinking)

    @property
    def default_effort(self) -> str:
        return "medium" if "medium" in self.thinking else self.efforts[0]


LEVELS = {
    effort: {"thinkingLevel": effort.upper()} for effort in ("low", "medium", "high")
}

MODELS = {
    "gemini-4-hydrogen": ModelSpec(
        (256000, 1048576), 262144, {**LEVELS, "xhigh": {"thinkingLevel": "XHIGH"}}
    ),
    "gemini-pro-orchestrator-g1-pro": ModelSpec(
        (256000, 1048576),
        65536,
        {"high": LEVELS["high"]},
        wire_model="gemini-pro-orchestrator-g1-pro",
    ),
    "gemini-4-carbon": ModelSpec((256000, 512000, 900000), 65536, LEVELS),
    "gemini-4-argon": ModelSpec((256000, 512000, 900000), 65536, LEVELS),
    "gemini-3.8-flash": ModelSpec(
        (256000, 1048576),
        65536,
        {
            "low": {"thinkingBudget": 1000},
            "medium": {"thinkingBudget": 4000},
            "high": {"thinkingBudget": -1},
        },
    ),
    "claude-opus-5-5": ModelSpec((256000, 840000), 128000, LEVELS),
    "claude-sonnet-5-5": ModelSpec((256000, 840000), 128000, LEVELS),
    "gpt-oss-120b": ModelSpec(
        (80000, 131072), 32768, {"medium": {"thinkingBudget": 8192}}
    ),
}

DEPRECATED = {
    "gemini-3-flash": "gemini-3.8-flash",
    "gemini-3.6-flash": "gemini-3.8-flash",
    "gemini-3.7-flash": "gemini-3.8-flash",
    "gemini-3.8-flash-tiered": "gemini-3.8-flash",
    "gemini-3.1-pro": "gemini-4-argon",
    "gemini-pro-agent": "gemini-4-argon",
    "claude-sonnet-4-6": "claude-sonnet-5-5",
    "claude-opus-4-6-thinking": "claude-opus-5-5",
}


def _preset(name: str, spec: ModelSpec) -> dict:
    """Build the KT preset, exposing each multi-option agy selector as a group."""
    groups = {
        "reasoning": {effort: {"reasoning_effort": effort} for effort in spec.efforts},
        "context": {
            f"{tokens // 1000}k": {"max_context": tokens}
            for tokens in spec.context_windows
        },
    }
    preset = {
        "model": name,
        "max_context": spec.max_context,
        "max_output": spec.max_output,
        "provider_native_tools": [],
        "reasoning_effort": spec.default_effort,
    }
    offered = {group: options for group, options in groups.items() if len(options) > 1}
    if offered:
        preset["variation_groups"] = offered
    return preset


PRESETS = {
    ("google-antigravity", name): _preset(name, spec) for name, spec in MODELS.items()
}


@dataclass(frozen=True)
class ModelSettings:
    wire_model: str
    reasoning_effort: str
    max_context: int
    max_output: int
    thinking_config: dict


def model_settings(
    model: str, effort: str = "", max_tokens: int | None = None
) -> ModelSettings:
    """Resolve family selectors and explicit agy SKUs into validated wire settings."""
    for old, replacement in DEPRECATED.items():
        if model == old or model.startswith(old + "-"):
            raise ValueError(f"deprecated_model, use {replacement}")
    family, fixed_effort = model, ""
    if model not in MODELS:
        for name, candidate in MODELS.items():
            if model.startswith(name + "-"):
                suffix = model[len(name) + 1 :]
                if candidate.wire_model or suffix not in candidate.thinking:
                    raise ValueError("unsupported_reasoning_effort")
                family, fixed_effort = name, suffix
                break
    spec = MODELS.get(family)
    if fixed_effort and effort and fixed_effort != effort:
        raise ValueError("conflicting_reasoning_effort")
    if effort and (spec is None or effort not in spec.thinking):
        raise ValueError("unsupported_reasoning_effort")
    limit = spec.max_output if spec else 8192
    if max_tokens is not None:
        if (
            type(max_tokens) is not int
            or max_tokens <= 0
            or (spec and max_tokens > limit)
        ):
            raise ValueError("invalid_max_output_tokens")
        limit = max_tokens
    if spec is None:
        return ModelSettings(model, "", 120000, limit, {})
    effort = effort or fixed_effort or spec.default_effort
    thinking = {"includeThoughts": True, **spec.thinking[effort]}
    if 0 < thinking.get("thinkingBudget", 0) and limit <= thinking["thinkingBudget"]:
        raise ValueError("max_output_tokens_must_exceed_thinking_budget")
    return ModelSettings(
        spec.wire_model or f"{family}-{effort}",
        effort,
        spec.max_context,
        limit,
        thinking,
    )
