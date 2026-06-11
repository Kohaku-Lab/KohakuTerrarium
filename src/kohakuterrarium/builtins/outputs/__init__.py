"""
Built-in output modules.

Contains:
- stdout: Terminal output (StdoutOutput, PrefixedStdoutOutput)
- none: Null output (NoneOutput — headless / batch runs)
- tts: TTS base classes and implementations (TTSModule, ConsoleTTS, DummyTTS)
"""

from typing import Any

from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.builtins.outputs.stdout import PrefixedStdoutOutput, StdoutOutput
from kohakuterrarium.builtins.outputs.tts import (
    ConsoleTTS,
    DummyTTS,
    TTSConfig,
    TTSModule,
)

# Registry of builtin output types
_BUILTIN_OUTPUTS: dict[str, type] = {
    "stdout": StdoutOutput,
    "stdout_prefixed": PrefixedStdoutOutput,
    "none": NoneOutput,
    "console_tts": ConsoleTTS,
    "dummy_tts": DummyTTS,
}

# Factory functions for outputs that need special handling
_BUILTIN_OUTPUT_FACTORIES: dict[str, Any] = {}


def register_builtin_output(name: str, cls: type) -> None:
    """Register a builtin output type."""
    _BUILTIN_OUTPUTS[name] = cls


def register_builtin_output_factory(name: str, factory: Any) -> None:
    """Register a factory function for a builtin output type."""
    _BUILTIN_OUTPUT_FACTORIES[name] = factory


def get_builtin_output(name: str) -> type | None:
    """Get a builtin output class by name."""
    return _BUILTIN_OUTPUTS.get(name)


def get_builtin_output_factory(name: str) -> Any | None:
    """Get a builtin output factory by name."""
    return _BUILTIN_OUTPUT_FACTORIES.get(name)


def is_builtin_output(name: str) -> bool:
    """Check if name is a builtin output type."""
    return name in _BUILTIN_OUTPUTS or name in _BUILTIN_OUTPUT_FACTORIES


def list_builtin_outputs() -> list[str]:
    """List all builtin output type names."""
    return list(set(_BUILTIN_OUTPUTS.keys()) | set(_BUILTIN_OUTPUT_FACTORIES.keys()))


def create_builtin_output(name: str, options: dict[str, Any] | None = None) -> Any:
    """
    Create a builtin output instance.

    Args:
        name: Builtin output type name
        options: Configuration options

    Returns:
        Output module instance

    Raises:
        ValueError: If output type not found
    """
    options = options or {}

    # Check for factory first
    if name in _BUILTIN_OUTPUT_FACTORIES:
        factory = _BUILTIN_OUTPUT_FACTORIES[name]
        return factory(options)

    # Fall back to class
    if name in _BUILTIN_OUTPUTS:
        cls = _BUILTIN_OUTPUTS[name]
        return cls(**options)

    raise ValueError(f"Unknown builtin output type: {name}")


# Register TUI output
from kohakuterrarium.builtins.tui.output import TUIOutput

register_builtin_output("tui", TUIOutput)


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
