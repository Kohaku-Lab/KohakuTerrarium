"""Terrarium runtime engine and graph topology public facade."""

import importlib

_EXPORTS = {
    "ChannelConfig": "kohakuterrarium.terrarium.config",
    "ChannelInfo": "kohakuterrarium.terrarium.graph.topology",
    "ChannelObserver": "kohakuterrarium.terrarium.observer",
    "ConnectionResult": "kohakuterrarium.terrarium.engine.engine",
    "Creature": "kohakuterrarium.terrarium.creature.host",
    "CreatureConfig": "kohakuterrarium.terrarium.config",
    "CreatureInfo": "kohakuterrarium.terrarium.service",
    "DisconnectionResult": "kohakuterrarium.terrarium.engine.engine",
    "EngineEvent": "kohakuterrarium.terrarium.events",
    "EventFilter": "kohakuterrarium.terrarium.events",
    "EventKind": "kohakuterrarium.terrarium.events",
    "GraphTopology": "kohakuterrarium.terrarium.graph.topology",
    "LocalTerrariumService": "kohakuterrarium.terrarium.service",
    "LogEntry": "kohakuterrarium.terrarium.output_log",
    "MultiNodeTerrariumService": "kohakuterrarium.terrarium.multi_node_service",
    "ObservedMessage": "kohakuterrarium.terrarium.observer",
    "OutputLogCapture": "kohakuterrarium.terrarium.output_log",
    "RemoteTerrariumService": "kohakuterrarium.terrarium.remote_service",
    "RootAssignment": "kohakuterrarium.terrarium.events",
    "Terrarium": "kohakuterrarium.terrarium.engine.engine",
    "TerrariumConfig": "kohakuterrarium.terrarium.config",
    "TerrariumService": "kohakuterrarium.terrarium.service",
    "TopologyDelta": "kohakuterrarium.terrarium.graph.topology",
    "TopologyState": "kohakuterrarium.terrarium.graph.topology",
    "build_creature": "kohakuterrarium.terrarium.creature.host",
    "host_resources_allowed": "kohakuterrarium.terrarium.creature.host_resources",
    "load_terrarium_config": "kohakuterrarium.terrarium.config",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
