"""Built-in input modules.

Contains terminal, TUI, and no-input implementations only. Audio examples
(ASR/Whisper) live under ``examples/`` so core imports stay audio-free.
"""

from typing import Any

from kohakuterrarium.builtins.inputs.cli import CLIInput, NonBlockingCLIInput
from kohakuterrarium.builtins.inputs.none import NoneInput

# Registry of builtin input types
_BUILTIN_INPUTS: dict[str, type] = {
    "cli": CLIInput,
    "cli_nonblocking": NonBlockingCLIInput,
    "none": NoneInput,
}

# Factory functions for inputs that need special handling
_BUILTIN_INPUT_FACTORIES: dict[str, Any] = {}


def register_builtin_input(name: str, cls: type) -> None:
    """Register a builtin input type."""
    _BUILTIN_INPUTS[name] = cls


def register_builtin_input_factory(name: str, factory: Any) -> None:
    """Register a factory function for a builtin input type."""
    _BUILTIN_INPUT_FACTORIES[name] = factory


def get_builtin_input(name: str) -> type | None:
    """Get a builtin input class by name."""
    return _BUILTIN_INPUTS.get(name)


def get_builtin_input_factory(name: str) -> Any | None:
    """Get a builtin input factory by name."""
    return _BUILTIN_INPUT_FACTORIES.get(name)


def is_builtin_input(name: str) -> bool:
    """Check if name is a builtin input type."""
    return name in _BUILTIN_INPUTS or name in _BUILTIN_INPUT_FACTORIES


def list_builtin_inputs() -> list[str]:
    """List all builtin input type names."""
    return list(set(_BUILTIN_INPUTS.keys()) | set(_BUILTIN_INPUT_FACTORIES.keys()))


def create_builtin_input(name: str, options: dict[str, Any] | None = None) -> Any:
    """
    Create a builtin input instance.

    Args:
        name: Builtin input type name
        options: Configuration options

    Returns:
        Input module instance

    Raises:
        ValueError: If input type not found
    """
    options = options or {}

    # Check for factory first
    if name in _BUILTIN_INPUT_FACTORIES:
        factory = _BUILTIN_INPUT_FACTORIES[name]
        return factory(options)

    # Fall back to class
    if name in _BUILTIN_INPUTS:
        cls = _BUILTIN_INPUTS[name]
        return cls(**options)

    raise ValueError(f"Unknown builtin input type: {name}")


# Register TUI input
from kohakuterrarium.builtins.tui.input import TUIInput

register_builtin_input("tui", TUIInput)


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
