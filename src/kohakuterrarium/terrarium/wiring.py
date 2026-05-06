"""Output-wiring helpers for the Terrarium engine.

A creature can be wired to another creature in two different ways:

- **Round-output wiring**: equivalent to ``config.output_wiring``.  At
  turn end, the source creature emits one ``creature_output`` event to a
  target creature.  This is the semantic graph-editor edge.
- **Secondary output sinks**: low-level renderer/observer attachments
  (for websocket attach, logs, etc.).  These are still available through
  explicit ``*_sink`` helpers.
"""

from typing import Any

from kohakuterrarium.core.output_wiring import OutputWiringEntry, parse_wiring_entry
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.output_wiring import TerrariumOutputWiringResolver
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# terrarium-aware resolver installation
# ---------------------------------------------------------------------------


def install_output_wiring_resolver(engine: Any) -> Any:
    """Install a live terrarium resolver on every creature in ``engine``.

    The resolver points at ``engine._creatures`` by reference, so target
    lookup follows later hot-plug changes. Reinstalling is cheap and is
    used after root changes so the magic ``root`` target stays current.
    """
    return _install_resolver(engine._creatures, root_agent=None)


def _install_resolver(creatures: dict[str, Any], *, root_agent: Any | None) -> Any:
    resolver = TerrariumOutputWiringResolver(
        creatures=creatures,
        root_agent=root_agent,
    )
    for name, creature in creatures.items():
        creature.agent._wiring_resolver = resolver
        creature.agent._creature_id = getattr(creature, "creature_id", name)
    return resolver


# ---------------------------------------------------------------------------
# config.output_wiring-equivalent runtime edges
# ---------------------------------------------------------------------------


def add_output_edge(
    agent: Any, target: str | OutputWiringEntry | dict[str, Any]
) -> str:
    """Add a runtime ``config.output_wiring`` entry to an agent.

    ``target`` accepts the same shapes as one YAML entry: a bare target
    string, a mapping, or a pre-built :class:`OutputWiringEntry`.
    Returns a stable edge id that can be passed to :func:`remove_output_edge`.
    """
    entry = _coerce_output_entry(target)
    entries = _agent_output_wiring(agent)
    entries.append(entry)
    edge_id = output_edge_id(entry)
    logger.debug(
        "Wired output edge",
        edge_id=edge_id,
        target=entry.to,
        with_content=entry.with_content,
    )
    return edge_id


def remove_output_edge(agent: Any, edge_id: str) -> bool:
    """Remove a runtime output-wiring entry by id. Returns True if found."""
    entries = _agent_output_wiring(agent)
    for idx, entry in enumerate(entries):
        if output_edge_id(entry) == edge_id:
            entries.pop(idx)
            logger.debug("Unwired output edge", edge_id=edge_id, target=entry.to)
            return True
    return False


def list_output_edges(agent: Any) -> list[dict[str, Any]]:
    """List currently-configured output-wiring entries for an agent."""
    return [output_edge_to_dict(entry) for entry in _agent_output_wiring(agent)]


def output_edge_id(entry: OutputWiringEntry) -> str:
    """Stable id for one runtime wiring entry.

    The id is value-derived rather than object-id-derived so API callers
    can list edges, persist the id in UI state, and delete the same edge
    later without needing the Python object identity.
    """
    return "wire_" + "_".join(
        (
            _slug(entry.to),
            "content" if entry.with_content else "ping",
            entry.prompt_format,
            "self" if entry.allow_self_trigger else "noself",
            _short_hash(entry.prompt or ""),
        )
    )


def output_edge_to_dict(entry: OutputWiringEntry) -> dict[str, Any]:
    return {
        "id": output_edge_id(entry),
        "to": entry.to,
        "with_content": entry.with_content,
        "prompt": entry.prompt,
        "prompt_format": entry.prompt_format,
        "allow_self_trigger": entry.allow_self_trigger,
    }


def _coerce_output_entry(
    target: str | OutputWiringEntry | dict[str, Any],
) -> OutputWiringEntry:
    if isinstance(target, OutputWiringEntry):
        return target
    return parse_wiring_entry(target)


def _agent_output_wiring(agent: Any) -> list[OutputWiringEntry]:
    config = getattr(agent, "config", None)
    if config is None:
        raise AttributeError("agent has no config for output_wiring")
    entries = getattr(config, "output_wiring", None)
    if entries is None:
        entries = []
        setattr(config, "output_wiring", entries)
    return entries


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned or "target"


def _short_hash(value: str) -> str:
    # FNV-1a 32-bit: deterministic across processes without importing hashlib.
    h = 2166136261
    for byte in value.encode("utf-8"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


# ---------------------------------------------------------------------------
# low-level secondary output sinks
# ---------------------------------------------------------------------------


def add_secondary_sink(agent: Any, sink: OutputModule) -> str:
    """Attach a secondary sink to an agent's :class:`OutputRouter`.

    Returns a sink id derived from the sink's identity so callers can
    later remove it.  The id is just the python ``id()`` formatted as
    hex — sinks don't need a stable identity beyond "this object".
    """
    agent.output_router.add_secondary(sink)
    sink_id = f"sink_{id(sink):x}"
    logger.debug(
        "Wired output sink",
        sink_id=sink_id,
        sink_type=type(sink).__name__,
    )
    return sink_id


def remove_secondary_sink(agent: Any, sink_id: str) -> bool:
    """Remove a previously-attached sink by id.  Returns True if found."""
    target_hex = sink_id.removeprefix("sink_")
    secondaries = list(getattr(agent.output_router, "_secondary_outputs", []))
    matched: OutputModule | None = None
    for s in secondaries:
        if f"{id(s):x}" == target_hex:
            matched = s
            break
    if matched is None:
        return False
    # OutputRouter exposes remove_secondary on recent codebases; fall
    # back to direct list mutation if not available.
    remove = getattr(agent.output_router, "remove_secondary", None)
    if callable(remove):
        remove(matched)
    else:
        agent.output_router._secondary_outputs = [
            s for s in secondaries if s is not matched
        ]
    logger.debug("Unwired output sink", sink_id=sink_id)
    return True
