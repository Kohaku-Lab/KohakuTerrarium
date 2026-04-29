from dataclasses import dataclass, field
from typing import Any

_LEGACY_BACKEND_TYPES = {"openai", "codex", "codex-oauth", "anthropic"}


@dataclass
class LLMBackend:
    """Reusable concrete provider profile.

    ``provider_name`` is the compatibility key that provider-native tools
    match against (``BaseTool.provider_support``). Built-in backends
    default to their own name (``codex``, ``openai``, …); custom
    backends default to the backend's own ``name`` unless the user
    explicitly sets something else (e.g. ``codex`` to masquerade as
    Codex for tool-compat purposes on a ChatGPT-Enterprise endpoint).

    ``provider_native_tools`` is the set of builtin tool names the user
    has opted into for this backend. Runtime auto-injects these tools
    when the active LLM profile resolves through this backend; tools
    not listed here are never injected, even if the active provider
    class (e.g. ``CodexOAuthProvider``) advertises them globally.
    """

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
        if self.retry_policy:
            data["retry_policy"] = self.retry_policy
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
            retry_policy=data.get("retry_policy"),
            variation_groups=data.get("variation_groups", {}) or {},
        )


@dataclass
class LLMProfile:
    """Resolved runtime LLM configuration.

    ``backend_provider_name`` and ``backend_native_tools`` are carried
    through from :class:`LLMBackend` so ``bootstrap/llm.py`` can stamp
    them onto the constructed LLM provider instance (see
    :meth:`LLMBackend.provider_name` and
    :meth:`LLMBackend.provider_native_tools`). These control which
    provider-native tools auto-inject into the agent's tool registry.
    """

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
        if self.retry_policy:
            data["retry_policy"] = self.retry_policy
        if self.selected_variations:
            data["selected_variations"] = self.selected_variations
        return data
