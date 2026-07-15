"""Advertise live attachment surfaces for creatures and graph sessions.

``IO`` requires an input-capable creature, ``LOG`` is process-wide, ``OBSERVER``
requires graph channels, and ``TRACE`` represents a live session event stream.
Advertisements are order-stable and may contain several policies at once.
"""

from enum import Enum
from typing import TYPE_CHECKING, Any

from kohakuterrarium.studio._runtime import host_engine_or_none

if TYPE_CHECKING:
    pass


class Policy(str, Enum):
    """Stable string codes for the four live attachment surfaces."""

    IO = "io"
    LOG = "log"
    OBSERVER = "observer"
    TRACE = "trace"


def get_policies(creature_id: str, manager: Any | None = None) -> list[Policy]:
    """Return order-stable policies for a manager-owned creature.

    Without a manager or matching live agent, only the engine-independent ``LOG`` and
    ``TRACE`` baseline can be advertised.
    """
    policies: list[Policy] = [Policy.LOG, Policy.TRACE]

    if manager is None:
        return policies

    agents = getattr(manager, "_agents", {}) or {}
    agent = agents.get(creature_id)
    if agent is None:
        return policies

    # Bidirectional attachment requires an input module to receive client messages.
    inp = getattr(agent, "input_module", None) or getattr(agent, "_input", None)
    if inp is not None:
        policies.insert(0, Policy.IO)

    # Observation is meaningful only when the creature participates in channels.
    channels = getattr(agent, "_channels", None) or getattr(agent, "channels", None)
    if channels:
        policies.append(Policy.OBSERVER)

    return policies


def get_graph_policies(session_id: str, manager: Any | None = None) -> list[Policy]:
    """Return policies for a manager-owned graph session.

    Graphs advertise observation and the engine-independent baseline. ``IO`` requires
    a root agent that owns the user-facing graph interaction.
    """
    policies: list[Policy] = [Policy.LOG, Policy.OBSERVER, Policy.TRACE]

    if manager is None:
        return policies

    runtimes = getattr(manager, "_terrariums", {}) or {}
    runtime = runtimes.get(session_id)
    if runtime is None:
        return policies

    root = getattr(runtime, "root", None) or getattr(runtime, "_root_agent", None)
    if root is not None:
        policies.insert(0, Policy.IO)

    return policies


def get_creature_policies(
    service: "TerrariumService", creature_id: str
) -> list[Policy]:
    """Return best-effort policies for an engine-hosted creature.

    ``IO`` reflects an input module and ``OBSERVER`` reflects shared graph channels;
    ``LOG`` and ``TRACE`` form the baseline. Lab hosts cannot inspect worker-local
    modules, so they return the safe baseline rather than treating hints as gates.
    """
    engine = host_engine_or_none(service)
    policies: list[Policy] = [Policy.LOG, Policy.TRACE]
    if engine is None:
        return policies

    try:
        creature = engine.get_creature(creature_id)
    except KeyError:
        return policies

    agent = creature.agent
    inp = getattr(agent, "input_module", None) or getattr(agent, "_input", None)
    if inp is not None:
        policies.insert(0, Policy.IO)

    env = engine._environments.get(creature.graph_id)
    if env is not None and env.shared_channels.list_channels():
        policies.append(Policy.OBSERVER)

    return policies


def get_session_policies(service: "TerrariumService", session_id: str) -> list[Policy]:
    """Return best-effort policies for an engine-hosted graph session.

    Graph sessions advertise ``LOG``, ``OBSERVER``, and ``TRACE``; a privileged root
    adds ``IO``. Lab hosts return the safe baseline because graph members live on
    workers and policy hints must not become authorization checks.
    """
    engine = host_engine_or_none(service)
    policies: list[Policy] = [Policy.LOG, Policy.OBSERVER, Policy.TRACE]
    if engine is None:
        return policies

    try:
        graph = engine.get_graph(session_id)
    except KeyError:
        return policies

    for cid in graph.creature_ids:
        try:
            c = engine.get_creature(cid)
        except KeyError:
            continue
        if getattr(c, "is_privileged", False):
            policies.insert(0, Policy.IO)
            break

    return policies
