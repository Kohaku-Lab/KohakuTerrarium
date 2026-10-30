"""Shared data classes + constants for the Rich CLI module picker.

Kept separate from the main overlay so :mod:`module_picker` and
:mod:`module_picker_render` stay under the 600-line file ceiling and
neither of them imports the other (they both import from here).

The module picker mirrors the runtime configurable-modules surface
that the Vue ``ModulesPanel`` and the TUI ``ModulesModal`` already
expose: per-type tabs (``Plugins`` / ``Native tools`` / future
types), a navigable list with a toggle column for plugins, and an
edit form whose fields are derived from each module's
``option_schema``. Every operation routes through the same agent
helpers (``agent.plugins`` / ``agent.plugin_options`` /
``agent.native_tool_options`` / ``agent.tool_options``) the other surfaces use, so behaviour
stays consistent.
"""

from dataclasses import dataclass, field
from typing import Any

# Fixed leading tabs keep layout stable; future module types append at runtime.
TAB_LABELS = {
    "plugin": "Plugins",
    "native_tool": "Native tools",
    "tool": "Tools",
}
DEFAULT_TAB_ORDER: list[str] = ["plugin", "native_tool", "tool"]


@dataclass
class ModuleEntry:
    """Represent one module inventory row in the picker."""

    type: str
    name: str
    description: str = ""
    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool | None = None
    priority: int | None = None


@dataclass
class ModuleFormField:
    """Single editable row inside the edit form."""

    label: str
    key: str
    kind: str
    value: str = ""
    options: list[str] | None = None
    unavailable: dict[str, str] = field(default_factory=dict)
    doc: str = ""
    minimum: float | None = None
    maximum: float | None = None
    error: str = ""


@dataclass
class ModuleFormState:
    """Edit form for one module."""

    module_key: str
    title: str
    fields: list[ModuleFormField]
    cursor: int = 0
    message: str = ""


def module_key(entry: ModuleEntry) -> str:
    """Return a stable type/name key that disambiguates shared names."""
    return f"{entry.type}/{entry.name}"
