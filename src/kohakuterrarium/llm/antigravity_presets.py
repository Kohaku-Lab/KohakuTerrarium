"""Model limits and effort routing for the agy 1.2.9 consumer catalog."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSpec:
    max_context: int
    max_output: int
    efforts: tuple[str, ...] = ()
    mode: str = ""
    wire_models: dict[str, str] = field(default_factory=dict)


MODELS = {
    **{
        f"gemini-{version}-flash": ModelSpec(
            1048576,
            65536,
            ("low", "medium", "high"),
            "level",
            (
                {
                    effort: f"gemini-{version}-flash-tiered"
                    for effort in ("low", "medium", "high")
                }
                if version in ("3.7", "3.8")
                else {}
            ),
        )
        for version in ("3.6", "3.7", "3.8")
    },
    "gemini-3.1-pro": ModelSpec(
        1048576, 65535, ("low", "high"), "budget", {"high": "gemini-pro-agent"}
    ),
    "claude-sonnet-4-6": ModelSpec(250000, 64000, mode="fixed"),
    "claude-opus-4-6-thinking": ModelSpec(250000, 64000, mode="fixed"),
}

PRESETS = {
    ("google-antigravity", name): {
        "model": name,
        "max_context": spec.max_context,
        "max_output": spec.max_output,
        "provider_native_tools": [],
        **(
            {
                "reasoning_effort": "high",
                "variation_groups": {
                    "reasoning": {
                        effort: {"reasoning_effort": effort} for effort in spec.efforts
                    }
                },
            }
            if spec.efforts
            else {}
        ),
    }
    for name, spec in MODELS.items()
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
    family, fixed_effort = model, ""
    if model not in MODELS:
        for name, candidate in MODELS.items():
            if candidate.efforts and model.startswith(name + "-"):
                suffix = model[len(name) + 1 :]
                if suffix == "tiered" and candidate.mode == "level":
                    family = name
                elif suffix in candidate.efforts:
                    family, fixed_effort = name, suffix
                else:
                    raise ValueError("unsupported_reasoning_effort")
                break
    spec = MODELS.get(family)
    if fixed_effort and effort and fixed_effort != effort:
        raise ValueError("conflicting_reasoning_effort")
    if effort and (spec is None or effort not in spec.efforts):
        raise ValueError("unsupported_reasoning_effort")
    effort = effort or fixed_effort or ("high" if spec and spec.efforts else "")
    limit = spec.max_output if spec else 8192
    if max_tokens is not None:
        if (
            type(max_tokens) is not int
            or max_tokens <= 0
            or (spec and max_tokens > limit)
        ):
            raise ValueError("invalid_max_output_tokens")
        limit = max_tokens
    wire_model, thinking = model, {}
    if spec:
        if spec.efforts:
            wire_model = spec.wire_models.get(effort, family + "-" + effort)
        if spec.mode == "level":
            thinking = {"includeThoughts": True, "thinkingLevel": effort.upper()}
        elif spec.mode in ("budget", "fixed"):
            budget = {"low": 1001, "high": 10001}[effort] if spec.efforts else 1024
            if limit <= budget:
                raise ValueError("max_output_tokens_must_exceed_thinking_budget")
            thinking = {"includeThoughts": True, "thinkingBudget": budget}
    return ModelSettings(
        wire_model, effort, spec.max_context if spec else 120000, limit, thinking
    )
