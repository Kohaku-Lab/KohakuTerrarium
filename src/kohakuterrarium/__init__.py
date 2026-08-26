"""Public API for building and running agent systems with KohakuTerrarium."""

import importlib

_EXPORTS = {
    "Activity": "kohakuterrarium.core.turn",
    "Agent": "kohakuterrarium.core.agent",
    "ConnectionResult": "kohakuterrarium.terrarium",
    "Creature": "kohakuterrarium.terrarium",
    "DisconnectionResult": "kohakuterrarium.terrarium",
    "EngineEvent": "kohakuterrarium.terrarium",
    "EventFilter": "kohakuterrarium.terrarium",
    "EventKind": "kohakuterrarium.terrarium",
    "FunctionTool": "kohakuterrarium.modules.tool.function",
    "SessionReader": "kohakuterrarium.session.reader",
    "SessionStore": "kohakuterrarium.session.store",
    "Studio": "kohakuterrarium.studio",
    "Terrarium": "kohakuterrarium.terrarium",
    "TextChunk": "kohakuterrarium.core.turn",
    "TurnEnded": "kohakuterrarium.core.turn",
    "TurnResult": "kohakuterrarium.core.turn",
    "errors": "kohakuterrarium.errors",
    "tool": "kohakuterrarium.modules.tool.function",
    "validate": "kohakuterrarium.validate",
}

__version__ = "2.1.0"

__all__ = [
    "Activity",
    "Agent",
    "ConnectionResult",
    "FunctionTool",
    "Creature",
    "DisconnectionResult",
    "EngineEvent",
    "EventFilter",
    "EventKind",
    "SessionReader",
    "SessionStore",
    "Studio",
    "Terrarium",
    "TextChunk",
    "TurnEnded",
    "TurnResult",
    "errors",
    "tool",
    "validate",
    "__version__",
]


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if module_path.startswith("kohakuterrarium.terrarium"):
        hooks = importlib.import_module("kohakuterrarium.studio.hooks")
        hooks.register_group_hooks()
    module = importlib.import_module(module_path)
    value = module if name in {"errors", "validate"} else getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
