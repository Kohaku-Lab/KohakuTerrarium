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
``agent.native_tool_options``) the other surfaces use, so behaviour
stays consistent.
"""

from dataclasses import dataclass, field
from typing import Any

# Tab order. Plugins first because they're the most-edited type;
# native tools next; future types append in alphabetical order at
# runtime. Tabs with zero modules are still rendered (greyed) so the
# layout is predictable across creatures.
TAB_LABELS = {
    "plugin": "Plugins",
    "native_tool": "Native tools",
}
DEFAULT_TAB_ORDER: list[str] = ["plugin", "native_tool"]


@dataclass
class ModuleEntry:
    """One row in the picker's per-tab list.

    A direct projection of the studio dispatcher's inventory shape.
    The picker keeps a list of these per type and rebuilds it on
    every reload (cheap; the inventory is bounded by however many
    plugins + native tools the agent registered).
    """

    type: str  # "plugin" | "native_tool" | …
    name: str
    description: str = ""
    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool | None = None  # None ⇒ no toggle (e.g. native tool)
    priority: int | None = None


# Form-field shape used while editing. Mirrors ``settings.FormField``
# closely so the render code can stay symmetric, but we widen
# ``options`` to support enums and we add ``kind`` so the renderer
# can decorate list / dict / numeric fields without re-parsing the
# schema.
@dataclass
class ModuleFormField:
    """Single editable row inside the edit form."""

    label: str
    key: str
    kind: str  # "string" | "int" | "float" | "bool" | "enum" | "list" | "dict"
    value: str = ""  # always stored as text; coerced on submit
    options: list[str] | None = None  # enum values
    doc: str = ""
    minimum: float | None = None
    maximum: float | None = None
    error: str = ""  # transient per-field validation message


@dataclass
class ModuleFormState:
    """Edit form for one module."""

    module_key: str  # "type/name" — uniquely identifies the module
    title: str
    fields: list[ModuleFormField]
    cursor: int = 0
    message: str = ""  # form-level error / status (e.g. server validation)


def module_key(entry: ModuleEntry) -> str:
    """Stable composite key — ``type/name``.

    Used by the cursor map (one cursor per tab) and by the
    selection / form-target tracking. Native tool and plugin can
    legally share a name (e.g. both could be called ``image_gen``)
    and the picker must distinguish them.
    """
    return f"{entry.type}/{entry.name}"
