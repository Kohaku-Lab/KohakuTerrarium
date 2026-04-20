from dataclasses import dataclass, field
from typing import Any

_LEGACY_BACKEND_TYPES = {"openai", "codex", "codex-oauth", "anthropic"}


@dataclass
class LLMBackend:
    """Reusable concrete provider profile."""

    name: str
    backend_type: str
    base_url: str = ""
    api_key_env: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"backend_type": self.backend_type}
        if self.base_url:
            data["base_url"] = self.base_url
        if self.api_key_env:
            data["api_key_env"] = self.api_key_env
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMBackend":
        return cls(
            name=name,
            backend_type=data.get("backend_type") or data.get("provider", "openai"),
            base_url=data.get("base_url", ""),
            api_key_env=data.get("api_key_env", ""),
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
    variation_groups: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": self.model,
            "max_context": self.max_context,
            "max_output": self.max_output,
        }
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
        if self.variation_groups:
            data["variation_groups"] = self.variation_groups
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMPreset":
        provider = data.get("provider", "") or data.get("backend", "")
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
            variation_groups=data.get("variation_groups", {}) or {},
        )


@dataclass
class LLMProfile:
    """Resolved runtime LLM configuration."""

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
    selected_variations: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMProfile":
        provider = data.get("provider", "") or data.get("backend", "")
        backend_type = data.get("backend_type", "")
        if provider in _LEGACY_BACKEND_TYPES and not backend_type:
            backend_type = provider
            provider = ""
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
            selected_variations=data.get("selected_variations", {}) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": self.model,
            "max_context": self.max_context,
            "max_output": self.max_output,
        }
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
        if self.selected_variations:
            data["selected_variations"] = self.selected_variations
        return data
