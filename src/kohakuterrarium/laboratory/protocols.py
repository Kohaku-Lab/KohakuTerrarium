"""Structural protocols for Laboratory APP integration.

The capability-specific protocols keep callers decoupled from concrete host and
client implementations. They are runtime-checkable to support capability tests
without introducing import cycles.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LabSender(Protocol):
    """APP request/response capability."""

    async def request(
        self,
        *,
        to_node: str,
        namespace: str,
        type: str,
        body: Any = None,
        timeout: float = ...,
    ) -> Any: ...


@runtime_checkable
class LabNotifier(Protocol):
    """Fire-and-forget APP messaging capability."""

    async def notify(
        self,
        *,
        to_node: str,
        namespace: str,
        type: str,
        body: Any = None,
    ) -> None: ...


@runtime_checkable
class LabRegistrar(Protocol):
    """APP extension registration capability."""

    def register_app_extension(self, namespace: str, handler: Any) -> None: ...

    def unregister_app_extension(self, namespace: str) -> bool: ...


@runtime_checkable
class LabNode(LabSender, LabNotifier, LabRegistrar, Protocol):
    """Combined request, notification, and extension-registration capability."""


__all__ = ["LabNode", "LabNotifier", "LabRegistrar", "LabSender"]
