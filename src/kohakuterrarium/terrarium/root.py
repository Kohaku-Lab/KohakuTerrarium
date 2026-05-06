"""Root-assignment helper for the Terrarium engine.

Mirrors the legacy "root agent" pattern (one creature listens to every
peer's channel + a dedicated ``report_to_root`` channel) at the engine
layer in pure channel + wiring terms.

The body of :meth:`Terrarium.assign_root` lives here so ``engine.py``
stays under the file-size cap; everything channel-related is grouped
with its siblings (``channels.py``, ``wiring.py``).
"""

from typing import TYPE_CHECKING

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.terrarium.events import RootAssignment
from kohakuterrarium.terrarium.tools_group import force_register_privileged_tools

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import CreatureRef, Terrarium


async def assign_root_to(
    engine: "Terrarium",
    creature: "CreatureRef",
    *,
    report_channel: str = "report_to_root",
) -> RootAssignment:
    """Body of :meth:`Terrarium.assign_root` — see the engine docstring
    for semantics.
    """
    root = engine._creature(creature)
    gid = root.graph_id
    graph = engine._topology.graphs.get(gid)
    if graph is None:
        raise KeyError(f"graph {gid!r} does not exist")

    injected_channels: list[str] = []
    listened: list[str] = []
    senders: list[str] = []

    # 1. report_to_root channel.
    if report_channel not in graph.channels:
        await engine.add_channel(
            gid,
            report_channel,
            description=f"Reports back to {root.name}",
        )
        injected_channels.append(report_channel)

    env = engine._environments[gid]

    # 2. root listens on report_channel.
    _topo.set_listen(engine._topology, root.creature_id, report_channel, listening=True)
    _channels.inject_channel_trigger(
        root.agent,
        subscriber_id=root.name,
        channel_name=report_channel,
        registry=env.shared_channels,
        ignore_sender=root.name,
    )
    if report_channel not in root.listen_channels:
        root.listen_channels.append(report_channel)
    listened.append(report_channel)

    # 3. every other creature can send on report_channel.
    for cid in graph.creature_ids:
        if cid == root.creature_id:
            continue
        other = engine._creatures.get(cid)
        if other is None:
            continue
        _topo.set_send(engine._topology, cid, report_channel, sending=True)
        if report_channel not in other.send_channels:
            other.send_channels.append(report_channel)
        senders.append(cid)

    # 4. root listens on every other existing channel.
    for ch_name in list(graph.channels):
        if ch_name == report_channel:
            continue
        already = ch_name in graph.listen_edges.get(root.creature_id, set())
        if already:
            continue
        _topo.set_listen(engine._topology, root.creature_id, ch_name, listening=True)
        _channels.inject_channel_trigger(
            root.agent,
            subscriber_id=root.name,
            channel_name=ch_name,
            registry=env.shared_channels,
            ignore_sender=root.name,
        )
        if ch_name not in root.listen_channels:
            root.listen_channels.append(ch_name)
        listened.append(ch_name)

    # 5. mark the root as privileged for downstream callers (group tool
    # registration, UI mount). Privilege is creation-time/assignment-time
    # and immutable thereafter — see ``terrarium.creature_host.Creature``.
    root.is_privileged = True
    _wiring.install_output_wiring_resolver(engine)
    # Force-register the privileged graph-mutating tools on the
    # freshly-elevated creature. Basic comm tools (``send_channel`` /
    # ``group_send``) were already attached at ``add_creature`` time.
    force_register_privileged_tools(root.agent)

    return RootAssignment(
        graph_id=gid,
        root_id=root.creature_id,
        report_channel=report_channel,
        channels_created=injected_channels,
        channels_listened=listened,
        senders_added=senders,
    )
