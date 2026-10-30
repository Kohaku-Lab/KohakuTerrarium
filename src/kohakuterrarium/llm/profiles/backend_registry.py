"""Package-provided LLM backend types declared under ``llm_backends:``.

A package entry names a provider class; its ``name`` becomes a valid
``backend_type``. Scanning reads manifests only; the implementation module is
imported on first use by :func:`load_backend_class`.
"""

import importlib
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.errors import ConfigError, LLMNotConfiguredError
from kohakuterrarium.llm.profiles.types import LLMBackend
from kohakuterrarium.packages.resolve import ensure_package_importable
from kohakuterrarium.packages.slots.slots import iter_package_llm_backend_entries
from kohakuterrarium.packages.walk import packages_fingerprint
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

BUILTIN_BACKEND_TYPES = frozenset(
    {"openai", "anthropic", "codex", "grok-subscription", "google-antigravity"}
)
# Types bootstrap builds that are not offered for new backend records.
INTERNAL_BACKEND_TYPES = frozenset({"litellm", "fake_test", "codex-oauth"})
CREDENTIAL_MODES = ("api_key", "external", "none")


class PackageBackendError(LLMNotConfiguredError):
    """A package backend cannot be imported or does not follow the contract."""


@dataclass(frozen=True)
class PackageBackendDescriptor:
    """One ``llm_backends:`` manifest entry, validated but not imported."""

    name: str
    module: str
    class_name: str
    package: str
    display_name: str = ""
    description: str = ""
    credentials: str = "api_key"
    provider_native_tools: tuple[str, ...] = ()

    @classmethod
    def from_manifest(
        cls, package: str, entry: dict[str, Any]
    ) -> "PackageBackendDescriptor":
        """Validate a raw manifest entry; raises ``ConfigError`` naming the package."""
        name = entry.get("name")
        module = entry.get("module")
        class_name = entry.get("class") or entry.get("class_name")
        where = f"llm_backends entry {name!r} in package {package!r}"
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{where} needs a 'name'")
        if not isinstance(module, str) or not module:
            raise ConfigError(f"{where} needs a 'module'")
        if not isinstance(class_name, str) or not class_name:
            raise ConfigError(f"{where} needs a 'class'")
        credentials = entry.get("credentials") or "api_key"
        if credentials not in CREDENTIAL_MODES:
            raise ConfigError(
                f"{where} has credentials={credentials!r}; "
                f"expected one of {CREDENTIAL_MODES}"
            )
        native = entry.get("provider_native_tools") or []
        if not isinstance(native, list):
            raise ConfigError(f"{where} provider_native_tools must be a list")
        return cls(
            name=name,
            module=module,
            class_name=class_name,
            package=package,
            display_name=str(entry.get("display_name") or ""),
            description=str(entry.get("description") or ""),
            credentials=credentials,
            provider_native_tools=tuple(str(t) for t in native if t),
        )

    @property
    def manages_own_credentials(self) -> bool:
        """True when KT must not ask for or require an API key."""
        return self.credentials != "api_key"

    def to_backend(self) -> LLMBackend:
        """The provider record this backend contributes to ``load_backends``."""
        return LLMBackend(
            name=self.name,
            backend_type=self.name,
            provider_name=self.name,
            provider_native_tools=list(self.provider_native_tools),
        )


_ScanResult = tuple[dict[str, PackageBackendDescriptor], dict[str, list[str]]]
_scan_cache: tuple[tuple, _ScanResult] | None = None


def _scan() -> _ScanResult:
    """:func:`_scan_uncached`, reused until the installed-package set changes."""
    global _scan_cache
    key = packages_fingerprint()
    if _scan_cache is None or _scan_cache[0] != key:
        _scan_cache = (key, _scan_uncached())
    valid, collisions = _scan_cache[1]
    return dict(valid), dict(collisions)


def _scan_uncached() -> _ScanResult:
    """Valid descriptors by name, plus names declared by more than one package."""
    owners: dict[str, list[str]] = {}
    valid: dict[str, PackageBackendDescriptor] = {}
    for package, entry in iter_package_llm_backend_entries():
        name = entry["name"]
        if name in BUILTIN_BACKEND_TYPES:
            logger.warning(
                "Package backend skipped (built-in type)", backend=name, package=package
            )
            continue
        owners.setdefault(name, []).append(package)
        try:
            valid[name] = PackageBackendDescriptor.from_manifest(package, entry)
        except ConfigError as exc:
            logger.warning("Package backend skipped", error=str(exc))
    collisions = {name: pkgs for name, pkgs in owners.items() if len(pkgs) > 1}
    for name in collisions:
        valid.pop(name, None)
    return valid, collisions


def _collision_error(name: str, packages: list[str]) -> ConfigError:
    return ConfigError(
        f"Collision for llm_backends name {name!r}: declared by packages "
        f"[{', '.join(packages)}]. Uninstall one or rename the entry."
    )


def package_backends() -> dict[str, PackageBackendDescriptor]:
    """Every usable package backend keyed by name.

    Built-in type names, malformed entries and names declared by more than
    one package are left out (with a log line), so one broken package does
    not disable the others.
    """
    valid, collisions = _scan()
    for name, packages in collisions.items():
        logger.error(str(_collision_error(name, packages)))
    return valid


def package_backend(name: str) -> PackageBackendDescriptor | None:
    """The descriptor for ``name``, or ``None`` when no package declares it.

    Raises:
        ConfigError: More than one package declares ``name``.
    """
    if not name or name in BUILTIN_BACKEND_TYPES:
        return None
    valid, collisions = _scan()
    if name in collisions:
        raise _collision_error(name, collisions[name])
    return valid.get(name)


def load_backend_class(name: str) -> type:
    """Import the provider class for a package backend.

    Raises:
        PackageBackendError: Unknown name, import failure, missing class, or
            a class without a callable ``from_profile``.
    """
    descriptor = package_backend(name)
    if descriptor is None:
        raise PackageBackendError(f"No installed package provides backend {name!r}")
    where = (
        f"backend {name!r} (package {descriptor.package!r}, "
        f"{descriptor.module}.{descriptor.class_name})"
    )
    ensure_package_importable(descriptor.package)
    try:
        module = importlib.import_module(descriptor.module)
    except Exception as exc:
        raise PackageBackendError(f"Cannot import {where}: {exc}") from exc
    cls = getattr(module, descriptor.class_name, None)
    if not isinstance(cls, type):
        raise PackageBackendError(f"Class not found for {where}")
    if not callable(getattr(cls, "from_profile", None)):
        raise PackageBackendError(f"{where} has no from_profile(profile) classmethod")
    return cls


def backend_doctor(name: str) -> str | None:
    """Run the provider class's optional ``doctor()`` status check.

    Returns its status line, or ``None`` when the class defines no check.
    Exceptions from the check propagate as the failure detail.
    """
    check = getattr(load_backend_class(name), "doctor", None)
    if not callable(check):
        return None
    result = check()
    return str(result) if result is not None else None
