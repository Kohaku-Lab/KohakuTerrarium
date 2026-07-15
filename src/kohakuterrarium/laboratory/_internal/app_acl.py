"""Prevent clients from forwarding privileged APP messages to other clients.

Workers cannot authenticate an envelope's claimed ``from_node`` after a
client-to-client hop. Namespaces whose payloads carry host-derived authority
must therefore travel only from the host to a worker; worker adapters also
reject non-host origins as defense in depth.
"""

from kohakuterrarium.laboratory._internal.app import (
    AppMessageError,
    parse_app_envelope,
)
from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind

# These control-plane namespaces carry authority derived by the host.
HOST_ONLY_APP_NAMESPACES: frozenset[str] = frozenset(
    {"studio.settings", "terrarium.runtime"}
)


def is_host_only_client_forward(env: Envelope) -> bool:
    """Return whether an APP envelope belongs to a host-only namespace."""
    if env.kind is not EnvelopeKind.APP:
        return False
    try:
        msg = parse_app_envelope(env)
    except AppMessageError:
        return False
    return msg.namespace in HOST_ONLY_APP_NAMESPACES


__all__ = ["HOST_ONLY_APP_NAMESPACES", "is_host_only_client_forward"]
