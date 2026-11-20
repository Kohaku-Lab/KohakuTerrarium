"""Backend (provider) persistence + the YAML store shared with presets.

Load provider backends and the shared LLM profile YAML store.

Backend types select OpenAI-compatible, Anthropic-compatible, or Codex OAuth
transport. Legacy ``codex-oauth`` values normalize to ``codex``.
"""

import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from itertools import chain
from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.llm.catalog_endpoints import (
    CATALOG_ENDPOINTS,
    CATALOG_KEY_ENVS,
    MIMO_ENDPOINT,
    catalog_kind,
)
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.profiles.api_keys import KT_DIR, PROVIDER_KEY_MAP
from kohakuterrarium.llm.profiles.backend_registry import (
    BUILTIN_BACKEND_TYPES,
    package_backend,
    package_backends,
)
from kohakuterrarium.llm.profiles.types import LLMBackend
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.file.lock import FileLock, FileLockBusy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Preserve the display constant while live I/O resolves the current config root.
PROFILES_PATH = KT_DIR / "llm_profiles.yaml"
_SCHEMA_VERSION = 3


def _profiles_path() -> Path:
    """Resolve the profile store against the current configuration root."""
    return config_dir() / "llm_profiles.yaml"


def catalog_backend(name: str) -> LLMBackend:
    return LLMBackend(
        name=name,
        backend_type="openai",
        base_url=CATALOG_ENDPOINTS[name],
        api_key_env=CATALOG_KEY_ENVS[name],
    )


_BUILTIN_PROVIDER_NAMES: set[str] = {
    *CATALOG_ENDPOINTS,
    "codex",
    "grok-subscription",
    "google-antigravity",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "mimo",
    "kimi-code",
    "glm-coding",
}


def is_builtin_backend(backend: LLMBackend) -> bool:
    """Whether settings should present this record as a managed built-in."""
    if backend.name not in CATALOG_ENDPOINTS:
        return backend.name in _BUILTIN_PROVIDER_NAMES
    default = catalog_backend(backend.name)
    return catalog_kind(backend.base_url) == backend.name and backend == replace(
        default, base_url=backend.base_url
    )


# Legacy presets sometimes placed transport types in the provider field.
_LEGACY_BACKEND_TYPE_VALUES: set[str] = {"openai", "codex", "codex-oauth", "anthropic"}


def _normalize_backend_type(value: str) -> str:
    """Normalize legacy transport names and default empty values to OpenAI."""
    if value == "codex-oauth":
        return "codex"
    return value or "openai"


def load_yaml_store() -> dict[str, Any]:
    """Read the shared ``llm_profiles.yaml`` — returns ``{}`` on missing/bad file."""
    path = _profiles_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load LLM profiles", error=str(e))
        return {}


@contextmanager
def profile_transaction():
    """Serialize the whole profile read/modify/write operation across processes."""
    lock = FileLock(_profiles_path().with_suffix(".yaml.lock"))
    deadline = time.monotonic() + 10
    while True:
        try:
            lock.acquire()
            break
        except FileLockBusy:
            if time.monotonic() >= deadline:
                raise ValueError("Model settings are busy. Retry saving.") from None
            time.sleep(0.025)
    try:
        yield
    finally:
        lock.release()


def write_profile_bytes(path: Path, content: bytes) -> None:
    """Replace the file under the caller's transaction; readers see complete YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


def write_yaml_locked(data: dict[str, Any]) -> None:
    content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    write_profile_bytes(_profiles_path(), content.encode("utf-8"))


def save_yaml_store(data: dict[str, Any]) -> None:
    """Atomically replace the complete profile store."""
    with profile_transaction():
        write_yaml_locked(data)


def _built_in_providers() -> dict[str, LLMBackend]:
    """Return built-in provider transports and native-tool compatibility metadata."""
    return {
        **{name: catalog_backend(name) for name in CATALOG_ENDPOINTS},
        "codex": LLMBackend(
            name="codex",
            backend_type="codex",
            provider_name="codex",
            provider_native_tools=["image_gen"],
        ),
        "google-antigravity": LLMBackend(
            name="google-antigravity",
            backend_type="google-antigravity",
            provider_name="google-antigravity",
        ),
        "grok-subscription": LLMBackend(
            name="grok-subscription",
            backend_type="grok-subscription",
            provider_name="grok-subscription",
            provider_native_tools=["grok_image_gen", "video_gen"],
        ),
        "openai": LLMBackend(
            name="openai",
            backend_type="openai",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        ),
        "openrouter": LLMBackend(
            name="openrouter",
            backend_type="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        ),
        "anthropic": LLMBackend(
            name="anthropic",
            backend_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        "gemini": LLMBackend(
            name="gemini",
            backend_type="openai",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key_env="GEMINI_API_KEY",
        ),
        "mimo": LLMBackend(
            name="mimo",
            backend_type="openai",
            base_url=MIMO_ENDPOINT,
            api_key_env="MIMO_API_KEY",
        ),
        "kimi-code": LLMBackend(
            name="kimi-code",
            backend_type="anthropic",
            base_url="https://api.kimi.com/coding/",
            api_key_env="KIMI_CODE_API_KEY",
        ),
        "glm-coding": LLMBackend(
            name="glm-coding",
            backend_type="anthropic",
            base_url="https://open.bigmodel.cn/api/anthropic",
            api_key_env="GLM_CODING_API_KEY",
        ),
    }


def legacy_provider_from_data(data: dict[str, Any]) -> str:
    """Infer a built-in provider from legacy transport and endpoint fields."""
    value = data.get("provider", "")
    if value and value not in _LEGACY_BACKEND_TYPE_VALUES:
        return value

    raw_backend_type = data.get("backend_type") or data.get("provider", "openai")
    backend_type = _normalize_backend_type(raw_backend_type)
    base_url = data.get("base_url", "")
    api_key_env = data.get("api_key_env", "")

    inferred = catalog_kind(base_url)
    if inferred in CATALOG_ENDPOINTS:
        return inferred
    if backend_type == "codex":
        return "codex"
    if backend_type == "grok-subscription":
        return "grok-subscription"
    if "openrouter.ai" in base_url:
        return "openrouter"
    if "generativelanguage.googleapis.com" in base_url:
        return "gemini"
    if "api.openai.com" in base_url:
        return "openai"
    if "mimo" in base_url:
        return "mimo"
    if "api.kimi.com/coding" in base_url:
        return "kimi-code"
    if "open.bigmodel.cn/api/anthropic" in base_url:
        return "glm-coding"
    if raw_backend_type == "anthropic" or "api.anthropic.com" in base_url:
        return "anthropic"
    if api_key_env in {
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "MIMO_API_KEY",
        "KIMI_CODE_API_KEY",
        "GLM_CODING_API_KEY",
    }:
        reverse = {v: k for k, v in PROVIDER_KEY_MAP.items()}
        return reverse[api_key_env]
    return ""


_remote_backends: dict[str, LLMBackend] = {}


def set_remote_backend(backend: LLMBackend) -> None:
    """Cache a host-resolved backend so worker-side synchronous lookup can resolve it."""
    _remote_backends[backend.name] = backend


def clear_remote_backends() -> None:
    """Invalidate all host-resolved backend entries."""
    _remote_backends.clear()


def _legacy_entries(section):
    if isinstance(section, dict):
        for entry in section.values():
            if isinstance(entry, dict) and isinstance(entry.get("model"), str):
                yield entry


def load_backends(*, include_remote: bool = True) -> dict[str, LLMBackend]:
    """Resolve explicit transports before falling back to new catalog defaults."""
    data = load_yaml_store()
    backends = _built_in_providers()
    # These names used to be user-defined. Apply their defaults last so an
    # upgrade cannot redirect an existing profile, including on lab workers.
    defaults = {name: backends.pop(name) for name in CATALOG_ENDPOINTS}
    for name, backend in package_provider_records().items():
        backends.setdefault(name, backend)
    user_backends = data.get("backends") or data.get("providers") or {}
    if isinstance(user_backends, dict):
        for name, bdata in user_backends.items():
            if isinstance(bdata, dict):
                backends[name] = LLMBackend.from_dict(name, bdata)

    # Both flat schemas carried transport settings inline. A nested provider
    # bucket has preset dictionaries rather than a string model ID.
    for pdata in chain(
        _legacy_entries(data.get("presets")), _legacy_entries(data.get("profiles"))
    ):
        name = legacy_provider_from_data(pdata)
        if name and name not in backends:
            default = defaults.get(name)
            if default and not (pdata.get("base_url") or pdata.get("api_key_env")):
                continue
            endpoint = pdata.get("base_url") or (default.base_url if default else "")
            official_env = (
                default.api_key_env
                if default and catalog_kind(endpoint) == name
                else ""
            )
            legacy_type = (
                default.backend_type if default else pdata.get("provider", "openai")
            )
            backends[name] = LLMBackend(
                name=name,
                backend_type=_normalize_backend_type(
                    pdata.get("backend_type") or legacy_type
                ),
                base_url=endpoint,
                api_key_env=pdata.get("api_key_env") or official_env,
            )
    if include_remote:
        for name, backend in _remote_backends.items():
            backends.setdefault(name, replace(backend))
    for name, default in defaults.items():
        backends.setdefault(name, default)
    for name, backend in backends.items():
        if not is_builtin_backend(backend) and not backend.provider_name:
            backend.provider_name = name
    return {**defaults, **backends}


def package_provider_records() -> dict[str, LLMBackend]:
    """Provider records contributed by installed packages' ``llm_backends:``."""
    try:
        return {name: d.to_backend() for name, d in package_backends().items()}
    except ConfigError as exc:
        logger.error("Package LLM backends unavailable", error=str(exc))
        return {}


def validate_backend_type(backend_type: str) -> str:
    """Validate and return a canonical provider transport type."""
    normalized = _normalize_backend_type(backend_type)
    if normalized in BUILTIN_BACKEND_TYPES or package_backend(normalized):
        return normalized
    raise ValueError(f"Unsupported backend_type: {backend_type}")
