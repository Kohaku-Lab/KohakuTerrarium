"""Attach policies — per-creature / per-graph stream advertisement.

The Studio UI asks the backend "what live streams does this creature /
graph support?" so it can hide attach toggles that would 1011 close
anyway. This module is the canonical answer.

Four policies are defined (per ``plan.md §3.6``):

- ``IO`` — bidirectional chat (controller stream + user input).
  Available only for creatures with a configured input module.
- ``LOG`` — process log tail. Always available (engine-independent).
- ``OBSERVER`` — non-destructive channel observation.
  Available for graphs (terrariums) and for creatures attached to a
  graph; not for standalone creatures with no channels.
- ``TRACE`` — live append-only event stream from the session store.
  Available whenever a session is in-process (resumed live).

The advertisement helpers return a list because a creature can support
multiple policies simultaneously (e.g. a terrarium-attached creature
exposes IO + LOG + OBSERVER + TRACE all at once).
"""

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium


class Policy(str, Enum):
    """Attach policy codes — the four live-stream surfaces.

    Values are short ASCII strings so they round-trip cleanly through
    JSON without an explicit ``.value`` dance on the frontend.
    """

    IO = "io"
    LOG = "log"
    OBSERVER = "observer"
    TRACE = "trace"


def get_policies(creature_id: str, manager: Any | None = None) -> list[Policy]:
    """Return the attach policies a single creature supports.

    Args:
        creature_id: The agent / creature identifier as registered with
            ``KohakuManager._agents``.
        manager: Optional manager handle. When ``None`` the function
            reports the engine-independent baseline (``LOG`` + ``TRACE``)
            because there is no live agent to inspect for input modules
            or channel attachments.

    The returned list is order-stable so the frontend can render
    toggles deterministically.
    """
    policies: list[Policy] = [Policy.LOG, Policy.TRACE]

    if manager is None:
        return policies

    agents = getattr(manager, "_agents", {}) or {}
    agent = agents.get(creature_id)
    if agent is None:
        return policies

    # IO — only when the creature has an input module configured.
    inp = getattr(agent, "input_module", None) or getattr(agent, "_input", None)
    if inp is not None:
        policies.insert(0, Policy.IO)

    # OBSERVER — only when the creature is wired into a terrarium with
    # at least one channel.
    channels = getattr(agent, "_channels", None) or getattr(agent, "channels", None)
    if channels:
        policies.append(Policy.OBSERVER)

    return policies


def get_graph_policies(session_id: str, manager: Any | None = None) -> list[Policy]:
    """Return the attach policies a whole graph (terrarium) supports.

    Graphs always advertise ``OBSERVER`` (channels are the defining
    feature) plus the engine-independent baseline. ``IO`` is advertised
    only when the terrarium has a root agent (the user-facing creature
    that owns terrarium I/O).
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


# ---------------------------------------------------------------------------
# Engine-backed advertisement (Step 11)
# ---------------------------------------------------------------------------


def get_creature_policies(engine: "Terrarium", creature_id: str) -> list[Policy]:
    """Engine-backed counterpart to :func:`get_policies`.

    Returns the attach policies a single creature supports.  ``IO`` is
    advertised only when the creature has a configured input module;
    ``OBSERVER`` is advertised when the creature lives in a graph that
    has shared channels; ``LOG`` and ``TRACE`` are baseline.
    """
    policies: list[Policy] = [Policy.LOG, Policy.TRACE]

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


def get_session_policies(engine: "Terrarium", session_id: str) -> list[Policy]:
    """Engine-backed counterpart to :func:`get_graph_policies`.

    Sessions always advertise ``LOG`` + ``OBSERVER`` + ``TRACE``;
    ``IO`` is added when the session has a creature flagged as root.
    """
    policies: list[Policy] = [Policy.LOG, Policy.OBSERVER, Policy.TRACE]

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
