"""Register and expose built-in terminal, null, TTS, and TUI outputs."""

import importlib
import threading
from typing import Any

from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.builtins.outputs.stdout import PrefixedStdoutOutput, StdoutOutput
from kohakuterrarium.builtins.outputs.tts import (
    ConsoleTTS,
    DummyTTS,
    TTSConfig,
    TTSModule,
)

_BUILTIN_OUTPUTS: dict[str, type] = {
    "stdout": StdoutOutput,
    "stdout_prefixed": PrefixedStdoutOutput,
    "none": NoneOutput,
    "console_tts": ConsoleTTS,
    "dummy_tts": DummyTTS,
}

_BUILTIN_OUTPUT_FACTORIES: dict[str, Any] = {}
_LAZY_OUTPUTS = {"tui": ("kohakuterrarium.builtins.tui.output", "TUIOutput")}
_OUTPUTS_LOCK = threading.Lock()


def register_builtin_output(name: str, cls: type) -> None:
    """Register a builtin output type."""
    with _OUTPUTS_LOCK:
        _BUILTIN_OUTPUTS[name] = cls


def register_builtin_output_factory(name: str, factory: Any) -> None:
    """Register a factory function for a builtin output type."""
    _BUILTIN_OUTPUT_FACTORIES[name] = factory


def get_builtin_output(name: str) -> type | None:
    """Get a builtin output class by name."""
    cls = _BUILTIN_OUTPUTS.get(name)
    if cls is None and name in _LAZY_OUTPUTS:
        module_name, class_name = _LAZY_OUTPUTS[name]
        resolved = getattr(importlib.import_module(module_name), class_name)
        with _OUTPUTS_LOCK:
            cls = _BUILTIN_OUTPUTS.setdefault(name, resolved)
    return cls


def get_builtin_output_factory(name: str) -> Any | None:
    """Get a builtin output factory by name."""
    return _BUILTIN_OUTPUT_FACTORIES.get(name)


def is_builtin_output(name: str) -> bool:
    """Check if name is a builtin output type."""
    return (
        name in _BUILTIN_OUTPUTS
        or name in _BUILTIN_OUTPUT_FACTORIES
        or name in _LAZY_OUTPUTS
    )


def list_builtin_outputs() -> list[str]:
    """List all builtin output type names."""
    return list(
        set(_BUILTIN_OUTPUTS) | set(_BUILTIN_OUTPUT_FACTORIES) | set(_LAZY_OUTPUTS)
    )


def create_builtin_output(name: str, options: dict[str, Any] | None = None) -> Any:
    """Create a registered output module or raise for an unknown name."""
    options = options or {}

    # Factories take precedence so registrations can override class construction.
    if name in _BUILTIN_OUTPUT_FACTORIES:
        factory = _BUILTIN_OUTPUT_FACTORIES[name]
        return factory(options)

    cls = get_builtin_output(name)
    if cls is not None:
        return cls(**options)

    raise ValueError(f"Unknown builtin output type: {name}")


def __getattr__(name: str):
    if name != "TUIOutput":
        raise AttributeError(name)
    module_name, class_name = _LAZY_OUTPUTS["tui"]
    value = getattr(importlib.import_module(module_name), class_name)
    globals()[name] = value
    return value


__all__ = [
    # Registry
    "register_builtin_output",
    "register_builtin_output_factory",
    "get_builtin_output",
    "get_builtin_output_factory",
    "is_builtin_output",
    "list_builtin_outputs",
    "create_builtin_output",
    # Implementations
    "StdoutOutput",
    "PrefixedStdoutOutput",
    "NoneOutput",
    "TTSModule",
    "TTSConfig",
    "ConsoleTTS",
    "DummyTTS",
    "TUIOutput",
]
