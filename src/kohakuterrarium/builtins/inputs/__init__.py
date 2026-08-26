"""Built-in input modules.

Contains terminal, TUI, and no-input implementations only. Audio examples
(ASR/Whisper) live under ``examples/`` so core imports stay audio-free.
"""

import importlib
import threading
from typing import Any

from kohakuterrarium.builtins.inputs.cli import CLIInput, NonBlockingCLIInput
from kohakuterrarium.builtins.inputs.none import NoneInput

_BUILTIN_INPUTS: dict[str, type] = {
    "cli": CLIInput,
    "cli_nonblocking": NonBlockingCLIInput,
    "none": NoneInput,
}

_BUILTIN_INPUT_FACTORIES: dict[str, Any] = {}
_LAZY_INPUTS = {"tui": ("kohakuterrarium.builtins.tui.input", "TUIInput")}
_INPUTS_LOCK = threading.Lock()


def register_builtin_input(name: str, cls: type) -> None:
    """Register a builtin input type."""
    with _INPUTS_LOCK:
        _BUILTIN_INPUTS[name] = cls


def register_builtin_input_factory(name: str, factory: Any) -> None:
    """Register a factory function for a builtin input type."""
    _BUILTIN_INPUT_FACTORIES[name] = factory


def get_builtin_input(name: str) -> type | None:
    """Get a builtin input class by name."""
    cls = _BUILTIN_INPUTS.get(name)
    if cls is None and name in _LAZY_INPUTS:
        module_name, class_name = _LAZY_INPUTS[name]
        resolved = getattr(importlib.import_module(module_name), class_name)
        with _INPUTS_LOCK:
            cls = _BUILTIN_INPUTS.setdefault(name, resolved)
    return cls


def get_builtin_input_factory(name: str) -> Any | None:
    """Get a builtin input factory by name."""
    return _BUILTIN_INPUT_FACTORIES.get(name)


def is_builtin_input(name: str) -> bool:
    """Check if name is a builtin input type."""
    return (
        name in _BUILTIN_INPUTS
        or name in _BUILTIN_INPUT_FACTORIES
        or name in _LAZY_INPUTS
    )


def list_builtin_inputs() -> list[str]:
    """List all builtin input type names."""
    return list(
        set(_BUILTIN_INPUTS) | set(_BUILTIN_INPUT_FACTORIES) | set(_LAZY_INPUTS)
    )


def create_builtin_input(name: str, options: dict[str, Any] | None = None) -> Any:
    """Create a registered input module or raise for an unknown name."""
    options = options or {}

    # Factories take precedence so registrations can override class construction.
    if name in _BUILTIN_INPUT_FACTORIES:
        factory = _BUILTIN_INPUT_FACTORIES[name]
        return factory(options)

    cls = get_builtin_input(name)
    if cls is not None:
        return cls(**options)

    raise ValueError(f"Unknown builtin input type: {name}")


def __getattr__(name: str):
    if name != "TUIInput":
        raise AttributeError(name)
    module_name, class_name = _LAZY_INPUTS["tui"]
    value = getattr(importlib.import_module(module_name), class_name)
    globals()[name] = value
    return value


__all__ = [
    # Registry
    "register_builtin_input",
    "register_builtin_input_factory",
    "get_builtin_input",
    "get_builtin_input_factory",
    "is_builtin_input",
    "list_builtin_inputs",
    "create_builtin_input",
    # Implementations
    "CLIInput",
    "NonBlockingCLIInput",
    "NoneInput",
    "TUIInput",
]
