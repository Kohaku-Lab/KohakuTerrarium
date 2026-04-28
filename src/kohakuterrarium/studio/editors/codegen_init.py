"""Per-kind codegen dispatch.

Each module kind (``tools``, ``subagents``, …) has its own
codegen module exposing three functions:

* ``render_new(form: dict) -> str`` — scaffold a brand-new file
* ``update_existing(source: str, form: dict, execute_body: str) -> str``
   — patch an existing file in place (via libcst), preserving
   formatting + comments
* ``parse_back(source: str) -> dict`` — read form state out of an
   existing file for the editor

``RoundTripError`` is raised when ``update_existing`` can't
patch the file safely; routes surface it as a 422 and the
frontend falls back to raw-Monaco mode.
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
    """Return the codegen module for *kind* or raise ValueError."""
    if kind not in _DISPATCH:
        raise ValueError(f"unknown module kind: {kind!r}")
    return _DISPATCH[kind]


__all__ = ["RoundTripError", "get_codegen"]
