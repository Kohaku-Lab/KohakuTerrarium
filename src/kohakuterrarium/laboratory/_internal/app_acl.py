"""Host-side ACL for control-plane APP namespaces (design §13, R1-04).

Some APP namespaces carry control-plane authority — settings mutation
(``studio.settings``) and Drive runtime operations whose payloads name an
``actor`` / ``is_privileged`` / ``operator`` (``terrarium.runtime``). These are
strictly host→worker: the authenticated host derives authority from its own
request context and issues the envelope with ``from_node = "_host"``.

The host forwards APP envelopes between authenticated clients verbatim, and a
worker cannot verify ``from_node`` on that hop — a malicious peer could forge
``from_node = "_host"``. So the host must never forward these namespaces
client-to-client; a worker adapter additionally rejects any non-host origin.
"""

from kohakuterrarium.laboratory._internal.app import (
    AppMessageError,
    parse_app_envelope,
)
from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind

# Namespaces that must never travel worker→worker (host→worker control plane).
HOST_ONLY_APP_NAMESPACES: frozenset[str] = frozenset(
    {"studio.settings", "terrarium.runtime"}
)


def is_host_only_client_forward(env: Envelope) -> bool:
    """True when ``env`` is an APP envelope in a host-only namespace whose
    payload would be forwarded to a non-host target (a client-to-client hop).

    The host drops these instead of forwarding; a malformed APP payload or a
    non-APP envelope is not our concern here and returns ``False``.
    """
    if env.kind is not EnvelopeKind.APP:
        return False
    try:
        msg = parse_app_envelope(env)
    except AppMessageError:
        return False
    return msg.namespace in HOST_ONLY_APP_NAMESPACES


__all__ = ["HOST_ONLY_APP_NAMESPACES", "is_host_only_client_forward"]
