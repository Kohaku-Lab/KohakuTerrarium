"""Laboratory layer — cross-node coordination for KohakuTerrarium.

The Laboratory layer coordinates creatures across nodes and exposes
point-to-point channels, publish-subscribe topics, application messages,
and node configuration.

Transport, framing, and coordination modules under
:mod:`kohakuterrarium.laboratory._internal` are implementation details;
user code should import the public API from this package.
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
