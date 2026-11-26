"""The MCP transports every surface offers: API schemas, ``kt config mcp``,
the rich CLI and ktui settings, and (mirrored) the web UI.

``MCP_TRANSPORT_NAMES`` is what a registry entry may store;
``MCP_TRANSPORT_CHOICES`` is what pickers show, where the old name
``http`` appears as ``sse``. The web UI copy lives in
``src/kohakuterrarium-frontend/src/utils/mcpTransports.js``.
"""

from typing import Literal, get_args

from kohakuterrarium.errors import MCPConfigError

MCPTransportName = Literal["stdio", "streamable_http", "sse", "http"]
MCP_TRANSPORT_NAMES: tuple[str, ...] = get_args(MCPTransportName)
MCP_TRANSPORT_CHOICES: tuple[str, ...] = ("stdio", "streamable_http", "sse")

_STREAMABLE_ALIASES = {"streamable_http", "streamablehttp", "http_streamable"}


def _clean(transport: str | None) -> str:
    return (transport or "stdio").strip().lower().replace("-", "_")


def normalize_mcp_transport(transport: str | None) -> str:
    """Map a stored transport name to its runtime kind: stdio, sse or streamable_http."""
    raw = _clean(transport)
    if raw == "stdio":
        return "stdio"
    if raw in {"http", "sse"}:
        return "sse"
    if raw in _STREAMABLE_ALIASES:
        return "streamable_http"
    raise MCPConfigError(f"Unknown transport: {transport}")


def parse_transport(transport: str | None) -> str:
    """Validate a user-typed transport and return the name to store.

    Accepted spellings are stored as one of ``MCP_TRANSPORT_NAMES``; an
    unknown one raises :class:`MCPConfigError` listing the choices.
    """
    raw = _clean(transport)
    if raw in MCP_TRANSPORT_NAMES:
        return raw
    if raw in _STREAMABLE_ALIASES:
        return "streamable_http"
    raise MCPConfigError(
        f"Unknown transport '{(transport or '').strip()}'; choose one of: "
        f"{', '.join(MCP_TRANSPORT_CHOICES)} (http is the old name of sse)"
    )


def choice_for_transport(transport: str | None) -> str:
    """Return the picker choice that shows a stored transport (``stdio`` if unknown)."""
    try:
        return normalize_mcp_transport(transport)
    except MCPConfigError:
        return "stdio"
