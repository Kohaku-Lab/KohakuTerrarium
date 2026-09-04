"""Resolve how much of a tool's documentation reaches the model.

Three tiers of progressive disclosure, selected per creature and overridable
per tool:

``brief``
    Name and one-line description only. Parameter schemas keep their shape (a
    native schema without parameters is uncallable) but lose their prose, and
    first use is gated behind an ``info`` call.
``standard``
    Name, description, and the full parameter schema. The default.
``full``
    Everything ``standard`` carries, plus the tool's usage tier inlined into the
    system prompt. Reference material still lives behind ``info``.
"""

from typing import Any

from kohakuterrarium.errors import ConfigError

DOC_MODE_BRIEF = "brief"
DOC_MODE_STANDARD = "standard"
DOC_MODE_FULL = "full"

DOC_MODES: tuple[str, ...] = (DOC_MODE_BRIEF, DOC_MODE_STANDARD, DOC_MODE_FULL)

DEFAULT_DOC_MODE = DOC_MODE_STANDARD


def validate_doc_mode(value: str, *, where: str) -> str:
    """Return a valid documentation mode or raise naming the offending site."""
    if value not in DOC_MODES:
        raise ConfigError(
            f"Invalid doc_mode {value!r} for {where}. "
            f"Expected one of: {', '.join(DOC_MODES)}"
        )
    return value


def resolve_doc_mode(tool: Any, default: str = DEFAULT_DOC_MODE) -> str:
    """Resolve a tool's effective documentation mode.

    Per-tool configuration wins over the creature default; an unset or
    unrecognized per-tool value falls back rather than raising, so a stale
    third-party tool cannot break prompt assembly.
    """
    if tool is None:
        return default
    config = getattr(tool, "config", None)
    mode = getattr(config, "doc_mode", None)
    if mode in DOC_MODES:
        return mode
    return default if default in DOC_MODES else DEFAULT_DOC_MODE
