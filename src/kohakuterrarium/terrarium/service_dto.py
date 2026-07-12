"""Serializable DTOs shared by the terrarium service implementations.

Split out of ``terrarium/service.py`` (file-size cap): the identity/topology
snapshot :class:`CreatureInfo` and the small serialization helpers Local /
Remote / MultiNode services all use at their boundary. The live
:class:`~kohakuterrarium.terrarium.creature_host.Creature` is not serializable
(it holds the Agent, channels, …), so the service Protocol returns these DTOs.
"""

from dataclasses import dataclass
from typing import Any

from kohakuterrarium.terrarium.creature_host import Creature


@dataclass(frozen=True)
class CreatureInfo:
    """Identity + topology snapshot of a single creature.

    Serializable; safe to send over Lab. The live
    :class:`~kohakuterrarium.terrarium.creature_host.Creature` object
    is *not* serializable (holds Agent, channels, etc.), so the
    Protocol returns this DTO instead.
    """

    creature_id: str
    name: str
    graph_id: str
    is_running: bool
    is_privileged: bool
    parent_creature_id: str | None
    listen_channels: tuple[str, ...]
    send_channels: tuple[str, ...]
    # Resolved LLM model + canonical id (B3/B4: empty == deferred).
    model: str = ""
    llm_name: str = ""


def _channel_message_to_dict(m: Any) -> dict[str, Any]:
    """Serialize a :class:`ChannelMessage` to a JSON-friendly dict.

    Used by ``channel_history`` so the API surface returns the same
    shape on both local and remote service paths.  ``timestamp`` is
    ISO-8601 (or empty when the field isn't a datetime); ``content``
    is passed through verbatim (string or list-of-parts).
    """
    ts = getattr(m, "timestamp", None)
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
    return {
        "message_id": getattr(m, "message_id", ""),
        "sender": getattr(m, "sender", ""),
        "sender_id": getattr(m, "sender_id", None),
        "content": getattr(m, "content", ""),
        "channel": getattr(m, "channel", None),
        "timestamp": ts_str,
    }


def creature_to_info(creature: Creature) -> CreatureInfo:
    """Build a :class:`CreatureInfo` snapshot from a live Creature."""
    agent = getattr(creature, "agent", None)
    llm = getattr(agent, "llm", None) if agent is not None else None
    model = (
        getattr(llm, "model", "")
        or getattr(getattr(llm, "config", None), "model", "")
        or (getattr(agent, "config", None) and getattr(agent.config, "model", ""))
        or ""
    )
    # Canonical "provider/name" — falls back to raw model so the modal
    # never shows "No model" when one IS bound (B3/B4).
    llm_name = ""
    get_ident = getattr(agent, "llm_identifier", None) if agent is not None else None
    if callable(get_ident):
        try:
            llm_name = get_ident() or ""
        except Exception:
            pass
    llm_name = llm_name or str(model or "")
    return CreatureInfo(
        creature_id=creature.creature_id,
        name=creature.name,
        graph_id=creature.graph_id,
        is_running=creature.is_running,
        is_privileged=creature.is_privileged,
        parent_creature_id=creature.parent_creature_id,
        listen_channels=tuple(creature.listen_channels),
        send_channels=tuple(creature.send_channels),
        model=str(model or ""),
        llm_name=str(llm_name or ""),
    )


__all__ = ["CreatureInfo", "creature_to_info"]
