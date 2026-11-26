"""Interfaces of the policy services a sandbox publishes to tool contexts.

``ToolContext.runtime_services["network_policy"]`` and ``["write_policy"]``
satisfy these; consumers depend on them rather than on a sandbox class.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class NetworkPolicy(Protocol):
    """Decide connections made outside the tool hooks, such as by a proxy."""

    @property
    def mode(self) -> str:
        """The network level in force: ``"allow"`` or ``"deny"``."""

    @property
    def requires_os_confinement(self) -> bool:
        """Whether every spawned process must run under kernel confinement."""

    def check(self, host: str, port: int) -> str | None:
        """``None`` to allow a connection to ``host:port``, or why it is refused."""


@runtime_checkable
class WritePolicy(Protocol):
    """Decide file writes made outside a tool call, such as a saved download."""

    def check(self, path: str | Path) -> str | None:
        """``None`` to allow writing ``path``, or why it is refused."""
