"""Serializable DTOs shared by the terrarium service implementations.

Defines the identity/topology :class:`CreatureInfo` snapshot and the small
serialization helpers Local, Remote, and MultiNode services share at their
boundary. The live
:class:`~kohakuterrarium.terrarium.creature.host.Creature` is not serializable
(it holds the Agent, channels, …), so the service Protocol returns these DTOs.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict

from kohakuterrarium.core.token_ledger import token_snapshot


class _BranchExecutionResult(TypedDict, total=False):
    execution_error: str


class BranchMutationResult(_BranchExecutionResult):
    """Completed branch mutation returned across service boundaries."""

    status: Literal["completed"]
    request_id: str
    turn_index: int
    branch_id: int
    parent_branch_path: list[list[int]]


def completed_branch_result(agent: Any, request_id: str | None) -> BranchMutationResult:
    """Serialize an accepted branch mutation and its request-scoped execution error."""
    parent_path = getattr(agent, "_parent_branch_path", ()) or ()
    failure = getattr(agent, "_branch_execution_error", None)
    return {
        "status": "completed",
        **(
            {"execution_error": failure[1]}
            if request_id and failure and failure[0] == request_id
            else {}
        ),
        "request_id": request_id or uuid.uuid4().hex,
        "turn_index": int(getattr(agent, "_turn_index", 0)),
        "branch_id": int(getattr(agent, "_branch_id", 0)),
        "parent_branch_path": [list(pair) for pair in parent_path],
    }


class RewindResult(TypedDict):
    """Completed conversation rewind returned across service boundaries."""

    status: Literal["rewound"]
    position: int
    request_id: str


class CreatureLike(Protocol):
    """Structural subset required to build a serializable creature snapshot."""

    creature_id: str
    name: str
    graph_id: str
    is_running: bool
    is_privileged: bool
    parent_creature_id: str | None
    listen_channels: list[str]
    send_channels: list[str]
    agent: Any


@dataclass(frozen=True)
class CreatureInfo:
    """Identity + topology snapshot of a single creature.

    Serializable; safe to send over Lab. The live
    :class:`~kohakuterrarium.terrarium.creature.host.Creature` object
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
    # Empty values preserve deferred model resolution.
    model: str = ""
    llm_name: str = ""
    config_name: str = ""
    # Running usage counters are telemetry, not identity: excluded from eq/hash.
    token_usage: dict[str, int] = field(default_factory=dict, compare=False)
    context_tokens: int = field(default=0, compare=False)


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


def creature_to_info(creature: CreatureLike) -> CreatureInfo:
    """Build a :class:`CreatureInfo` snapshot from a live Creature."""
    agent = getattr(creature, "agent", None)
    llm = getattr(agent, "llm", None) if agent is not None else None
    model = (
        getattr(llm, "model", "")
        or getattr(getattr(llm, "config", None), "model", "")
        or (getattr(agent, "config", None) and getattr(agent.config, "model", ""))
        or ""
    )
    # Fall back to the raw model when no canonical provider/name is available.
    llm_name = ""
    get_ident = getattr(agent, "llm_identifier", None) if agent is not None else None
    if callable(get_ident):
        try:
            llm_name = get_ident() or ""
        except Exception:
            pass
    llm_name = llm_name or str(model or "")
    config = getattr(creature, "config", None) or getattr(agent, "config", None)
    config_name = getattr(config, "name", "") if config is not None else ""
    tokens = token_snapshot(agent)
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
        config_name=str(config_name or ""),
        token_usage=tokens["token_usage"],
        context_tokens=tokens["context_tokens"],
    )


__all__ = [
    "BranchMutationResult",
    "CreatureInfo",
    "RewindResult",
    "creature_to_info",
]
