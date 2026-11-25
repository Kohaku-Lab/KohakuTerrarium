"""Tool origin: which contributor put a tool into an agent's registry."""

from dataclasses import dataclass

TOOL_ORIGIN_KINDS = ("builtin", "package", "custom", "plugin", "mcp", "runtime")


@dataclass(frozen=True)
class ToolOrigin:
    """The contributor of one registered tool, shown to users as ``label``.

    ``kind`` is one of :data:`TOOL_ORIGIN_KINDS`; ``name`` names the package,
    creature, plugin or MCP server, and is empty for ``builtin`` / ``runtime``.
    """

    kind: str
    name: str = ""

    def __post_init__(self) -> None:
        if self.kind not in TOOL_ORIGIN_KINDS:
            raise ValueError(
                f"Unknown tool origin kind {self.kind!r} "
                f"(expected one of: {', '.join(TOOL_ORIGIN_KINDS)})"
            )

    @property
    def label(self) -> str:
        """Display form: ``kind`` alone, or ``kind:name``."""
        return f"{self.kind}:{self.name}" if self.name else self.kind

    def to_dict(self) -> dict[str, str]:
        """JSON form used by module inventories."""
        return {"kind": self.kind, "name": self.name, "label": self.label}


BUILTIN_ORIGIN = ToolOrigin("builtin")
RUNTIME_ORIGIN = ToolOrigin("runtime")
