"""Output-wiring helpers for the Terrarium engine.

A creature exposes its text + activity stream through an
``OutputRouter`` (default + secondary outputs).  The engine's
``wire_output`` / ``unwire_output`` add and remove secondary sinks.
This file centralises the bookkeeping so the engine doesn't have to
poke at agent internals directly.

Static and hot-plug ops share the same surface — there is no separate
"live" code path.
"""

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


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
