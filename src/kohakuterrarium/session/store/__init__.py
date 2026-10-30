"""SessionStore public facade; the class is assembled in :mod:`.assembly` on first use.

Leaf modules of this package are imported by modules that ``assembly`` itself
depends on, so the package import must stay light.
"""

import importlib

_EXPORTS = {"SessionStore": "kohakuterrarium.session.store.assembly"}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
