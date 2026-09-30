"""Discover agent namespaces from a session's event keys.

Event keys look like ``<namespace>:e<sequence>``. Attached agents use the
namespace ``<host>:attached:<role>:<attach sequence>``.
"""

from collections.abc import Iterable
from typing import Any

_FRAMEWORK_NAMESPACES = {"terrarium"}
_ATTACHED = ":attached:"


def _namespaces(event_keys: Iterable[str]) -> Iterable[str]:
    for key in event_keys:
        parts = key.rsplit(":e", 1)
        if len(parts) == 2:
            yield parts[0]


def discover_agents(event_keys: Iterable[str]) -> list[str]:
    """Return standalone agent names in first-seen order.

    Framework and attached-agent namespaces are excluded so resume does not
    rebuild them as standalone creatures.
    """
    seen: list[str] = []
    for namespace in _namespaces(event_keys):
        if _ATTACHED in namespace or namespace in _FRAMEWORK_NAMESPACES:
            continue
        if namespace not in seen:
            seen.append(namespace)
    return seen


def discover_attached_agents(event_keys: Iterable[str]) -> list[dict[str, Any]]:
    """Return attached-agent namespaces in first-seen order."""
    seen: dict[str, dict[str, Any]] = {}
    for namespace in _namespaces(event_keys):
        host, marker, remainder = namespace.partition(_ATTACHED)
        if not marker or namespace in seen:
            continue
        # Roles may contain colons, so split the sequence from the right.
        role, _, sequence = remainder.rpartition(":")
        if not role or not sequence.lstrip("-").isdigit():
            continue
        seen[namespace] = {
            "host": host,
            "role": role,
            "attach_seq": int(sequence),
            "namespace": namespace,
        }
    return list(seen.values())
