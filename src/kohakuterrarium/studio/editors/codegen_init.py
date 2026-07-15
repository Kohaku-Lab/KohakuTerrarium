"""Per-kind codegen dispatch.

Dispatches each module kind to a code generator that scaffolds new source,
updates supported structures, and extracts editor form state. Unsafe structured
updates raise ``RoundTripError`` so adapters can fall back to raw-source editing.
"""

from kohakuterrarium.studio.editors import (
    codegen_io,
    codegen_plugin,
    codegen_subagent,
    codegen_tool,
    codegen_trigger,
)
from kohakuterrarium.studio.editors.codegen_common import Codegen, RoundTripError

_DISPATCH = {
    "tools": codegen_tool,
    "subagents": codegen_subagent,
    "plugins": codegen_plugin,
    "triggers": codegen_trigger,
    "inputs": codegen_io,
    "outputs": codegen_io,
}


def get_codegen(kind: str) -> Codegen:
    """Return the code generator for a module kind, rejecting unknown kinds."""
    if kind not in _DISPATCH:
        raise ValueError(f"unknown module kind: {kind!r}")
    return _DISPATCH[kind]


__all__ = ["RoundTripError", "get_codegen"]
