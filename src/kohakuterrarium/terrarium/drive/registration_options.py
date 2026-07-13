"""Per-registration option resolution + injection (design §8.1, R1-17).

Split from :mod:`registration` so that leaf module stays under the size cap.
These helpers resolve the effective options for a registration (defaults merged
with the operator's serialized selection), validate them against the descriptor
schema, and inject them into the live instance through its optional
``configure`` hook — the difference between "options stored and ignored" and
"options that actually change runtime behaviour".

Leaf module: imports only stdlib, the Drive errors, and duck-types the
descriptor (never importing :mod:`registration`, so there is no cycle).
"""

import importlib.metadata
import json
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveValidationError

# Attribute a configured registration instance carries so the running engine can
# report its effective options in the runtime revision (design §8.6, R1-29).
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
    """Whether ``value`` matches an option-schema ``type`` name (bools are not
    ints/floats here). An unknown type name does not constrain."""
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
    """Resolve, validate, and inject a registration's effective options (R1-17).

    Merges ``options`` over the descriptor defaults, validates them against the
    option schema, and configures the instance through its optional
    ``configure(effective)`` hook. Rejection is keyed on the EFFECTIVE options,
    not the raw caller options: a registration with no ``configure`` method whose
    effective set is non-empty is rejected even when the caller supplied nothing,
    because a non-empty default with nowhere to apply it is the exact
    stored-and-ignored silent-no-op the finding calls out. The effective options
    are stamped on the instance so the runtime revision can hash them."""
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
    """The effective options a configured registration was injected with."""
    return dict(getattr(instance, EFFECTIVE_OPTIONS_ATTR, {}) or {})


def implementation_fingerprint(instance: object) -> dict[str, str | None]:
    """Stable identity of the code behind a resolved registration (R1-29).

    Resolved module + qualified class name + installed distribution version, so
    swapping the implementation class behind a same-named descriptor moves the
    running runtime revision instead of being invisible. Deliberately excludes
    process-local reprs (``id()`` / the default ``object.__repr__`` address):
    those differ per process and would make the revision unstable across
    restarts, defeating the compare it feeds."""
    cls = type(instance)
    module = getattr(cls, "__module__", None)
    return {
        "module": module,
        "qualname": getattr(cls, "__qualname__", None),
        "distribution": _distribution_version(module),
    }


def _distribution_version(module: str | None) -> str | None:
    """Best-effort installed version of ``module``'s top-level distribution.

    The import name is used as the distribution name; a package whose metadata
    is not installed (an in-tree test module, a namespace package) simply
    contributes ``None`` — module + qualname already distinguish two classes."""
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
    """Recursive JSON-type-aware equality for compatibility markers (R1-18).

    Python treats ``True == 1`` and ``False == 0``, but a compatibility marker is
    a JSON contract, so a bool must never satisfy a number (or vice versa). A bool
    matches only a bool of the same value; dicts/lists compare structurally and
    recurse; every other scalar (str / int / float / None) uses plain equality.
    Mismatched container shapes (dict vs list, etc.) compare unequal."""
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
    # default=str keeps this a pure byte-sizer for non-JSON-native values
    # (e.g. datetimes in a projection context); JSON-safety is enforced
    # separately by require_json_safe where it matters.
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
