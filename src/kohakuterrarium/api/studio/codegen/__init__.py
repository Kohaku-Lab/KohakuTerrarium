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

from kohakuterrarium.api.studio.codegen import io_mod, plugin, subagent, tool, trigger
from kohakuterrarium.api.studio.codegen.base import Codegen, RoundTripError

_DISPATCH = {
    "tools": tool,
    "subagents": subagent,
    "plugins": plugin,
    "triggers": trigger,
    "inputs": io_mod,
    "outputs": io_mod,
}


def get_codegen(kind: str) -> Codegen:
    """Return the codegen module for *kind* or raise ValueError."""
    if kind not in _DISPATCH:
        raise ValueError(f"unknown module kind: {kind!r}")
    return _DISPATCH[kind]


__all__ = ["RoundTripError", "get_codegen"]
