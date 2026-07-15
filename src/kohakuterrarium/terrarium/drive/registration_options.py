"""Resolve, validate, and apply per-registration options.

These helpers merge operator selections with descriptor defaults, validate the
result, and apply it through an optional ``configure`` hook. This module avoids
importing :mod:`registration` to keep the dependency direction acyclic.
"""

import importlib.metadata
import json
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveValidationError

# Effective options are retained on the instance so runtime revisions include
# the configuration that actually governs behavior.
EFFECTIVE_OPTIONS_ATTR = "_kt_effective_options"

_OPTION_TYPES: dict[str, type | tuple[type, ...]] = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "list": list,
    "dict": dict,
}


def option_type_ok(value: object, expected: str) -> bool:
    """Check an option type without treating booleans as numeric values."""
    py = _OPTION_TYPES.get(expected)
    if py is None:
        return True
    if expected in ("int", "float"):
        return isinstance(value, py) and not isinstance(value, bool)
    return isinstance(value, py)


def apply_registration_options(
    instance: object,
    descriptor: Any,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve, validate, apply, and retain a registration's effective options."""
    effective = descriptor.normalized_options(options)
    descriptor.validate_options(effective)
    configure = getattr(instance, "configure", None)
    if callable(configure):
        configure(effective)
    elif effective:
        raise DriveValidationError(
            f"registration {descriptor.name!r} declares options {sorted(effective)} "
            "but has no configure() hook to apply them"
        )
    setattr(instance, EFFECTIVE_OPTIONS_ATTR, dict(effective))
    return effective


def effective_options(instance: object) -> dict[str, Any]:
    """Return the effective options applied to a registration instance."""
    return dict(getattr(instance, EFFECTIVE_OPTIONS_ATTR, {}) or {})


def implementation_fingerprint(instance: object) -> dict[str, str | None]:
    """Return a process-independent identity for a registration implementation."""
    cls = type(instance)
    module = getattr(cls, "__module__", None)
    return {
        "module": module,
        "qualname": getattr(cls, "__qualname__", None),
        "distribution": _distribution_version(module),
    }


def _distribution_version(module: str | None) -> str | None:
    """Return the installed version of a module's top-level distribution."""
    if not module:
        return None
    top = module.split(".", 1)[0]
    try:
        return importlib.metadata.version(top)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def json_type_equal(want: Any, got: Any) -> bool:
    """Compare compatibility markers using JSON type semantics recursively."""
    if isinstance(want, bool) or isinstance(got, bool):
        return type(want) is type(got) and want == got
    if isinstance(want, dict) and isinstance(got, dict):
        return want.keys() == got.keys() and all(
            json_type_equal(want[k], got[k]) for k in want
        )
    if isinstance(want, list) and isinstance(got, list):
        return len(want) == len(got) and all(
            json_type_equal(w, g) for w, g in zip(want, got)
        )
    return want == got


def json_bytes(obj: object) -> int:
    # Sizing tolerates projected non-JSON values; callers enforce JSON safety at
    # boundaries where serialization is required.
    return len(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))


def require_json_safe(obj: object, name: str) -> None:
    try:
        json.dumps(obj)
    except (TypeError, ValueError) as exc:
        raise DriveValidationError(f"{name} must be JSON-serializable: {exc}") from exc


__all__ = [
    "EFFECTIVE_OPTIONS_ATTR",
    "apply_registration_options",
    "effective_options",
    "implementation_fingerprint",
    "json_bytes",
    "json_type_equal",
    "option_type_ok",
    "require_json_safe",
]
