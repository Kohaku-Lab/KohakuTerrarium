"""Session persistence public facade."""

import importlib

__all__ = ["SessionStore"]


def __getattr__(name: str):
    if name != "SessionStore":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module("kohakuterrarium.session.store"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
