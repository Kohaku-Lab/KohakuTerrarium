"""Engine-backed output wiring — secondary sinks per creature.

Replaces the per-route output-wiring helpers that today live inside
the ``Agent`` factory paths.  Used by the IO attach (Step 11) to wire
WS output sinks onto a creature.
"""

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.engine import Terrarium


async def wire_output(engine: Terrarium, creature_id: str, sink: OutputModule) -> str:
    """Attach a secondary output sink to a creature.

    Returns a sink id usable with :func:`unwire_output`.
    Raises :class:`KeyError` when ``creature_id`` is not in the engine.
    """
    return await engine.wire_output(creature_id, sink)


async def unwire_output(engine: Terrarium, creature_id: str, sink_id: str) -> bool:
    """Detach a previously-wired sink.  Returns True if found."""
    return await engine.unwire_output(creature_id, sink_id)
