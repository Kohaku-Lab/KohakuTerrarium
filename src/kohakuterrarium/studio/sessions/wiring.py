"""Engine-backed output wiring between live creatures.

``wire_output`` / ``unwire_output`` mutate the same per-agent
``config.output_wiring`` list that static creature configs use.  The
lower-level secondary-output-sink helpers stay available for IO attach
and websocket observers under explicit ``*_sink`` names.
"""

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.engine import Terrarium


async def wire_output(
    engine: Terrarium,
    creature_id: str,
    target: str | dict[str, Any],
) -> str:
    """Add a runtime ``config.output_wiring`` edge from a creature."""
    return await engine.wire_output(creature_id, target)


async def unwire_output(engine: Terrarium, creature_id: str, edge_id: str) -> bool:
    """Detach a previously-wired runtime output edge."""
    return await engine.unwire_output(creature_id, edge_id)


def list_output_wiring(engine: Terrarium, creature_id: str) -> list[dict[str, Any]]:
    """List runtime/static output-wiring edges for a creature."""
    return engine.list_output_wiring(creature_id)


async def wire_output_sink(
    engine: Terrarium,
    creature_id: str,
    sink: OutputModule,
) -> str:
    """Attach a low-level secondary output sink to a creature."""
    return await engine.wire_output_sink(creature_id, sink)


async def unwire_output_sink(engine: Terrarium, creature_id: str, sink_id: str) -> bool:
    """Detach a previously-wired secondary sink."""
    return await engine.unwire_output_sink(creature_id, sink_id)
