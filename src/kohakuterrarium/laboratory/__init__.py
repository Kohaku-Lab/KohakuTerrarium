"""Laboratory layer — cross-node coordination for KohakuTerrarium.

The Laboratory layer sits between Studio (managing framework) and
Terrarium (multi-agent runtime). It enables creatures to live on
multiple nodes coordinated by a single host, with creature ↔ creature
channels transparently spanning the network.

Public API surface (L4):
    - :class:`HostEngine` — accepts client connections, routes envelopes
    - :class:`ClientConnector` — connects out to a host
    - :class:`Channel` — point-to-point Send verb (lab.verbs)
    - :class:`Topic` — pub-sub Broadcast verb (lab.verbs)
    - :class:`HostConfig` / :class:`ClientConfig` — dataclass configuration

The 4-layer wire stack is L1 transport, L2 framing, L3 coordination,
L4 user verbs.

L1–L3 modules live under :mod:`kohakuterrarium.laboratory._internal` and
are framework-internal. User code imports only from this top-level
package.
"""

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.app import (
    AppMessage,
    AppMessageError,
    ExtensionHandler,
    ExtensionNotFoundError,
)
from kohakuterrarium.laboratory.verbs import (
    AckTimeoutError,
    Channel,
    LabNode,
    Topic,
)

__all__ = [
    "AckTimeoutError",
    "AppMessage",
    "AppMessageError",
    "Channel",
    "ClientConfig",
    "ExtensionHandler",
    "ExtensionNotFoundError",
    "HostConfig",
    "LabNode",
    "Topic",
]
