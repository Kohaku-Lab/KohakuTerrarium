from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from kohakuterrarium.llm.profiles.context import (
    CONTEXT_SELECTION_KEY,
    context_aliases,
    fold_legacy_context,
)

_LEGACY_BACKEND_TYPES = {"openai", "codex", "codex-oauth", "anthropic"}


def _window(value: Any) -> int | None:
    """A stored ``context_window``, or ``None`` when absent or malformed."""
    return value if type(value) is int and value > 0 else None


@dataclass
class LLMBackend:
    """Provider transport and native-tool compatibility configuration."""

    name: str
    backend_type: str
    base_url: str = ""
    api_key_env: str = ""
    provider_name: str = ""
    provider_native_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"backend_type": self.backend_type}
        if self.base_url:
            data["base_url"] = self.base_url
        if self.api_key_env:
            data["api_key_env"] = self.api_key_env
        if self.provider_name:
            data["provider_name"] = self.provider_name
        if self.provider_native_tools:
            data["provider_native_tools"] = list(self.provider_native_tools)
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMBackend":
        native_tools = data.get("provider_native_tools") or []
        if not isinstance(native_tools, list):
            native_tools = []
        return cls(
            name=name,
            backend_type=data.get("backend_type") or data.get("provider", "openai"),
            base_url=data.get("base_url", ""),
            api_key_env=data.get("api_key_env", ""),
            provider_name=data.get("provider_name", ""),
            provider_native_tools=[str(tool) for tool in native_tools if tool],
        )


@dataclass
class LLMPreset:
    """Named model preset that resolves through a provider profile."""

    name: str
    model: str
    provider: str = ""
    max_context: int = 256000
    max_output: int = 65536
    temperature: float | None = None
    reasoning_effort: str = ""
    service_tier: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] | None = None
    provider_native_tools: list[str] | None = None
    variation_groups: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # Known size of the model's window; informational, never caps max_context.
    context_window: int | None = None
    # ``context=<name>`` selections that are not plain sizes, as token counts.
    context_aliases: dict[str, int] = field(default_factory=dict)
    # Groups, window and aliases as given, when a legacy context group was folded.
    folded_from: tuple[Any, int | None, dict[str, int]] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        folded = fold_legacy_context(
            self.variation_groups, self.context_window, self.context_aliases
        )
        if folded is None:
            return
        self.folded_from = (
            deepcopy(self.variation_groups),
            self.context_window,
            dict(self.context_aliases),
        )
        self.variation_groups, self.context_window, self.context_aliases = folded

    def inherit_context_fold(self, previous: "LLMPreset | None") -> None:
        """Keep the aliases and stored group of the preset this one edits."""
        if previous is None:
            return
        self.context_aliases = {**previous.context_aliases, **self.context_aliases}
        if self.folded_from is None:
            self.folded_from = previous.folded_from

    def to_stored_dict(self) -> dict[str, Any]:
        """``to_dict`` that keeps a folded legacy group as written while unchanged."""
        data = self.to_dict()
        if self.folded_from is None:
            return data
        groups, window, aliases = self.folded_from
        refolded = replace(
            self,
            variation_groups=deepcopy(groups),
            context_window=self.context_window,
            context_aliases=dict(aliases),
            folded_from=None,
        )
        if refolded != self:
            return data
        data["variation_groups"] = deepcopy(groups)
        data.pop("context_aliases", None)
        if aliases:
            data["context_aliases"] = dict(aliases)
        derived = max(
            patch["max_context"] for patch in groups[CONTEXT_SELECTION_KEY].values()
        )
        if window is None and self.context_window == derived:
            data.pop("context_window", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": self.model,
            "max_context": self.max_context,
            "max_output": self.max_output,
        }
        if self.context_window is not None:
            data["context_window"] = self.context_window
        if self.context_aliases:
            data["context_aliases"] = dict(self.context_aliases)
        if self.provider:
            data["provider"] = self.provider
        if self.temperature is not None:
            data["temperature"] = self.temperature
        if self.reasoning_effort:
            data["reasoning_effort"] = self.reasoning_effort
        if self.service_tier:
            data["service_tier"] = self.service_tier
        if self.extra_body:
            data["extra_body"] = self.extra_body
        if self.retry_policy:
            data["retry_policy"] = self.retry_policy
        if self.provider_native_tools is not None:
            data["provider_native_tools"] = list(self.provider_native_tools)
        if self.variation_groups:
            data["variation_groups"] = self.variation_groups
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMPreset":
        provider = data.get("provider", "") or data.get("backend", "")
        native_tools = data.get("provider_native_tools")
        if native_tools is not None and not isinstance(native_tools, list):
            native_tools = None
        return cls(
            name=name,
            model=data.get("model", ""),
            provider=provider,
            max_context=data.get("max_context", 256000),
            max_output=data.get("max_output", 65536),
            temperature=data.get("temperature"),
            reasoning_effort=data.get("reasoning_effort", ""),
            service_tier=data.get("service_tier", ""),
            extra_body=data.get("extra_body", {}),
            retry_policy=data.get("retry_policy"),
            provider_native_tools=(
                [str(tool) for tool in native_tools if tool]
                if native_tools is not None
                else None
            ),
            variation_groups=data.get("variation_groups", {}) or {},
            context_window=_window(data.get("context_window")),
            context_aliases=context_aliases(data.get("context_aliases")),
        )


@dataclass
class LLMProfile:
    """Resolved model, transport, variation, and native-tool runtime settings."""

    name: str
    model: str
    provider: str = ""
    backend_type: str = ""
    max_context: int = 256000
    max_output: int = 65536
    base_url: str = ""
    api_key_env: str = ""
    temperature: float | None = None
    reasoning_effort: str = ""
    service_tier: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] | None = None
    selected_variations: dict[str, str] = field(default_factory=dict)
    backend_provider_name: str = ""
    backend_native_tools: list[str] = field(default_factory=list)
    context_window: int | None = None

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMProfile":
        provider = data.get("provider", "") or data.get("backend", "")
        backend_type = data.get("backend_type", "")
        if provider in _LEGACY_BACKEND_TYPES and not backend_type:
            backend_type = provider
            provider = ""
        native_tools = data.get("backend_native_tools") or []
        if not isinstance(native_tools, list):
            native_tools = []
        return cls(
            name=name,
            model=data.get("model", ""),
            provider=provider,
            backend_type=backend_type,
            max_context=data.get("max_context", 256000),
            max_output=data.get("max_output", 65536),
            base_url=data.get("base_url", ""),
            api_key_env=data.get("api_key_env", ""),
            temperature=data.get("temperature"),
            reasoning_effort=data.get("reasoning_effort", ""),
            service_tier=data.get("service_tier", ""),
            extra_body=data.get("extra_body", {}),
            retry_policy=data.get("retry_policy"),
            selected_variations=data.get("selected_variations", {}) or {},
            backend_provider_name=data.get("backend_provider_name", ""),
            backend_native_tools=[str(tool) for tool in native_tools if tool],
            context_window=_window(data.get("context_window")),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": self.model,
            "max_context": self.max_context,
            "max_output": self.max_output,
        }
        if self.context_window is not None:
            data["context_window"] = self.context_window
        if self.provider:
            data["provider"] = self.provider
        if self.backend_type:
            data["backend_type"] = self.backend_type
        if self.base_url:
            data["base_url"] = self.base_url
        if self.api_key_env:
            data["api_key_env"] = self.api_key_env
        if self.temperature is not None:
            data["temperature"] = self.temperature
        if self.reasoning_effort:
            data["reasoning_effort"] = self.reasoning_effort
        if self.service_tier:
            data["service_tier"] = self.service_tier
        if self.extra_body:
            data["extra_body"] = self.extra_body
        if self.retry_policy:
            data["retry_policy"] = self.retry_policy
        if self.selected_variations:
            data["selected_variations"] = self.selected_variations
        return data
