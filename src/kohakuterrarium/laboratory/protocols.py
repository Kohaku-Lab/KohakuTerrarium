"""Structural typing protocols for Laboratory APP integration.

Adapters, services, and helpers all need slim references to a "lab
node" — sometimes a :class:`~kohakuterrarium.laboratory._internal.host.HostEngine`,
sometimes a :class:`~kohakuterrarium.laboratory._internal.client.ClientConnector`.
Both expose enough surface to satisfy structural duck-typing; this
module names that surface explicitly so each adapter / client / cache
doesn't redefine its own near-identical protocol.

Four runtime-checkable protocols, each capturing the *minimum* a
caller needs:

- :class:`LabSender` — request/response APP RPC initiator.
- :class:`LabNotifier` — fire-and-forget APP messaging.
- :class:`LabRegistrar` — APP-extension registration surface.
- :class:`LabNode` — union of the three; the typical "I am a lab node
  and I do everything" handle, satisfied by both HostEngine and
  ClientConnector.

All protocols are :func:`typing.runtime_checkable`, so ``isinstance``
checks work without import cycles between the typed callers and the
concrete implementations.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LabSender(Protocol):
    """Anything that can issue an APP request and await the response.

    Both :class:`HostEngine` and :class:`ClientConnector` satisfy this
    via their ``request()`` method.
    """

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
    """Anything that can fire-and-forget an APP message."""

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
    """Anything that hosts APP extension handlers."""

    def register_app_extension(self, namespace: str, handler: Any) -> None: ...

    def unregister_app_extension(self, namespace: str) -> bool: ...


@runtime_checkable
class LabNode(LabSender, LabNotifier, LabRegistrar, Protocol):
    """Full lab-node surface — request + notify + extension registration.

    Both :class:`HostEngine` and :class:`ClientConnector` implement
    every method named here.  Use this when an adapter / cache needs
    *all* three capabilities (e.g. a client that registers its own
    handler and also issues outbound requests).
    """


__all__ = ["LabNode", "LabNotifier", "LabRegistrar", "LabSender"]
