"""Pure agent-touching helpers shared by service.py and studio.sessions.

The :class:`LocalTerrariumService` and the per-creature ``studio.sessions.creature_*``
modules both need to perform the same reads/writes against a live
``Agent`` (scratchpad, triggers, system prompt, plugins, etc.). The
service lives in the terrarium tier and cannot import from studio
(dep-graph tier ordering). Both call into this module so the agent
logic stays consistent in one place.

Every function here takes either:

- an ``Agent`` (most per-creature reads), or
- a :class:`Terrarium` ``engine`` + ``creature_id`` (when the resolution
  step matters — e.g. chat history reaches the agent's session_store
  AND the engine's lifecycle-attached store as a fallback).

No function in this module imports from ``studio`` or ``api``.
"""

import os
import time
from typing import Any

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.core.scratchpad import is_reserved_scratchpad_key
from kohakuterrarium.modules.user_command.base import UserCommandContext
import kohakuterrarium.terrarium.channels as _terrarium_channels
import kohakuterrarium.terrarium.topology as _terrarium_topology
import kohakuterrarium.terrarium.topology_snapshot as _terrarium_topology_snap
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.topology import GraphTopology
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Env / system prompt / working dir
# ---------------------------------------------------------------------------

_REDACTED_KEYS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")


def _redact_env() -> dict[str, str]:
    """Subset of os.environ safe to surface to the UI."""
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        upper = k.upper()
        if any(s in upper for s in _REDACTED_KEYS):
            continue
        out[k] = v
    return out


def agent_env(agent: Any) -> dict[str, Any]:
    # The working directory lives on ``agent.executor._working_dir`` (or
    # the workspace helper) — never on a bare ``agent._working_dir``.
    # Resolve it the same way ``agent_working_dir`` does so the ``/env``
    # and ``/working-dir`` accessors can never disagree.
    pwd = agent_working_dir(agent) or os.getcwd()
    return {"pwd": str(pwd), "env": _redact_env()}


def agent_system_prompt(agent: Any) -> dict[str, str]:
    return {"text": agent.get_system_prompt()}


def agent_working_dir(agent: Any) -> str:
    ws = getattr(agent, "workspace", None)
    if ws is None:
        return str(getattr(agent.executor, "_working_dir", ""))
    return ws.get()


def agent_set_working_dir(agent: Any, new_path: str) -> str:
    ws = getattr(agent, "workspace", None)
    if ws is None:
        raise RuntimeError("agent has no workspace helper")
    return ws.set(new_path)


# ---------------------------------------------------------------------------
# Scratchpad
# ---------------------------------------------------------------------------


def agent_scratchpad(agent: Any) -> dict[str, str]:
    return agent.scratchpad.to_dict()


def agent_patch_scratchpad(
    agent: Any,
    updates: dict[str, str | None],
) -> dict[str, str]:
    pad = agent.scratchpad
    for key, value in updates.items():
        if is_reserved_scratchpad_key(key):
            raise ValueError(f"Reserved scratchpad key: {key}")
        if value is None:
            pad.delete(key)
        else:
            pad.set(key, value)
    result = pad.to_dict()
    # Persist the scratchpad snapshot to the session store immediately.
    # ``load_scratchpad`` (used by resume) reads the ``{agent}:scratchpad``
    # state snapshot — and the ``scratchpad_write`` events the observer
    # records carry no value, so they can't reconstruct it. The turn-end
    # ``SessionOutput`` save only captures mutations made *before* a turn;
    # a patch made through this API path (no turn after it) would be lost
    # on resume without an explicit snapshot here.
    store = getattr(agent, "session_store", None)
    if store is not None and hasattr(store, "save_state"):
        try:
            store.save_state(agent.config.name, scratchpad=result)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(
                "scratchpad snapshot persist skipped",
                agent=getattr(getattr(agent, "config", None), "name", "?"),
                error=str(e),
            )
    return result


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def agent_triggers(agent: Any) -> list[dict[str, Any]]:
    tm = getattr(agent, "trigger_manager", None)
    if tm is None:
        return []
    return [
        {
            "trigger_id": info.trigger_id,
            "trigger_type": info.trigger_type,
            "running": info.running,
            "created_at": info.created_at.isoformat(),
        }
        for info in tm.list()
    ]


# ---------------------------------------------------------------------------
# Native tool options
# ---------------------------------------------------------------------------


def agent_native_tool_inventory(agent: Any) -> list[dict[str, Any]]:
    registry = getattr(agent, "registry", None)
    if registry is None:
        return []
    helper = getattr(agent, "native_tool_options", None)
    out: list[dict[str, Any]] = []
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        if tool is None or not getattr(tool, "is_provider_native", False):
            continue
        schema_fn = getattr(type(tool), "provider_native_option_schema", None)
        try:
            schema = schema_fn() if callable(schema_fn) else {}
        except Exception:
            schema = {}
        values = helper.get(name) if helper else {}
        out.append(
            {
                "name": name,
                "description": getattr(tool, "description", "") or "",
                "option_schema": schema,
                "values": values,
            }
        )
    out.sort(key=lambda entry: entry["name"])
    return out


def agent_get_native_tool_options(agent: Any) -> dict[str, dict[str, Any]]:
    helper = getattr(agent, "native_tool_options", None)
    if helper is None:
        return {}
    return helper.list()


def agent_set_native_tool_options(
    agent: Any,
    tool: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    helper = getattr(agent, "native_tool_options", None)
    if helper is None:
        raise ValueError("agent has no native_tool_options helper")
    return helper.set(tool, values or {})


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


def agent_list_plugins(agent: Any) -> list[dict[str, Any]]:
    pm = getattr(agent, "plugin_manager", None) or getattr(agent, "plugins", None)
    if pm is None:
        return []
    list_fn = getattr(pm, "list_plugins", None) or getattr(pm, "list", None)
    if not callable(list_fn):
        return []
    out: list[dict[str, Any]] = []
    for entry in list_fn():
        if isinstance(entry, dict):
            out.append(entry)
        else:
            out.append(
                {
                    "name": getattr(entry, "name", str(entry)),
                    "enabled": bool(getattr(entry, "enabled", True)),
                }
            )
    return out


def agent_plugin_inventory(agent: Any) -> list[dict[str, Any]]:
    """Enriched plugin list with option schema + current values.

    Drives the schema-aware options editor in the frontend.
    """
    pm = getattr(agent, "plugins", None)
    if pm is None or not hasattr(pm, "list_plugins_with_options"):
        return []
    return pm.list_plugins_with_options()


def agent_get_plugin_options(agent: Any, plugin_name: str) -> dict[str, Any]:
    """Return ``{name, schema, options}`` for one plugin.  Raises
    ``KeyError`` when the plugin isn't registered."""
    pm = getattr(agent, "plugins", None)
    if pm is None:
        raise KeyError(plugin_name)
    plugin = pm.get_plugin(plugin_name)
    if plugin is None:
        raise KeyError(plugin_name)
    schema_fn = getattr(type(plugin), "option_schema", None)
    schema: dict[str, dict[str, Any]] = {}
    if callable(schema_fn):
        try:
            schema = schema_fn() or {}
        except Exception:
            schema = {}
    values = plugin.get_options() if hasattr(plugin, "get_options") else {}
    return {"name": plugin_name, "schema": schema, "options": values}


def agent_set_plugin_options(
    agent: Any,
    plugin_name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    helper = getattr(agent, "plugin_options", None)
    if helper is None:
        raise ValueError("agent has no plugin_options helper")
    return helper.set(plugin_name, values or {})


async def agent_toggle_plugin(
    agent: Any, plugin_name: str, enabled: bool | None = None
) -> dict[str, Any]:
    """Set a plugin's enabled state.  Returns ``{name, enabled}``.

    ``enabled=None`` flips the current state; an explicit bool sets it
    to that target (idempotent — the studio/HTTP surface posts a desired
    state, not a flip request). Raises ``KeyError`` if the creature has
    no plugin registered under ``plugin_name`` — a name the creature
    doesn't have is a 404, never a fabricated success.
    """
    pm = getattr(agent, "plugins", None)
    if pm is None:
        raise ValueError("No plugins loaded")
    if pm.get_plugin(plugin_name) is None:
        raise KeyError(plugin_name)
    target = (not pm.is_enabled(plugin_name)) if enabled is None else enabled
    if target:
        pm.enable(plugin_name)
        if hasattr(pm, "load_pending"):
            await pm.load_pending()
    else:
        pm.disable(plugin_name)
    return {"name": plugin_name, "enabled": target}


# ---------------------------------------------------------------------------
# Module catalog — unified plugin / native_tool / future MCP dispatch.
# Pure agent-touch; mirrors ``studio.sessions.creature_modules`` layout
# so studio shims can re-export from here without re-implementing.
# ---------------------------------------------------------------------------


def agent_list_modules(agent: Any) -> list[dict[str, Any]]:
    """Return every configurable module across all known types."""
    out: list[dict[str, Any]] = []
    out.extend(_inventory_plugins(agent))
    out.extend(_inventory_native_tools(agent))
    return out


def agent_get_module_options(agent: Any, module_type: str, name: str) -> dict[str, Any]:
    if module_type == "plugin":
        payload = agent_get_plugin_options(agent, name)
        return {
            "type": "plugin",
            "name": payload["name"],
            "schema": payload["schema"],
            "options": payload["options"],
        }
    if module_type == "native_tool":
        for entry in agent_native_tool_inventory(agent):
            if entry["name"] == name:
                return {
                    "type": "native_tool",
                    "name": name,
                    "schema": entry.get("option_schema", {}),
                    "options": entry.get("values", {}),
                }
        raise KeyError(name)
    raise ValueError(f"Unknown module type: {module_type!r}")


def agent_set_module_options(
    agent: Any,
    module_type: str,
    name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    if module_type == "plugin":
        return agent_set_plugin_options(agent, name, values or {})
    if module_type == "native_tool":
        return agent_set_native_tool_options(agent, name, values or {})
    raise ValueError(f"Unknown module type: {module_type!r}")


async def agent_toggle_module(
    agent: Any, module_type: str, name: str
) -> dict[str, Any]:
    if module_type == "plugin":
        return await agent_toggle_plugin(agent, name)
    if module_type == "native_tool":
        raise ValueError("Module type does not support toggle")
    raise ValueError(f"Unknown module type: {module_type!r}")


def _inventory_plugins(agent: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": "plugin",
            "name": entry["name"],
            "description": entry.get("description", ""),
            "schema": entry.get("schema", {}),
            "options": entry.get("options", {}),
            "enabled": entry.get("enabled", True),
            "priority": entry.get("priority"),
        }
        for entry in agent_plugin_inventory(agent)
    ]


def _inventory_native_tools(agent: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": "native_tool",
            "name": entry["name"],
            "description": entry.get("description", ""),
            "schema": entry.get("option_schema", {}),
            "options": entry.get("values", {}),
            "enabled": None,
        }
        for entry in agent_native_tool_inventory(agent)
    ]


# ---------------------------------------------------------------------------
# Slash-command dispatch — built-in user_commands registry.
# Mirrors ``studio.sessions.creature_command.execute_command`` but
# without the studio tier dep so service.py can call it directly.
# ---------------------------------------------------------------------------


def wire_creature_on_engine(
    engine: Terrarium,
    graph_id: str,
    creature_id: str,
    channel: str,
    direction: str,
    *,
    enabled: bool = True,
) -> None:
    """Toggle a single creature's listen / send edge on a channel.

    Pure engine-level op: mutates topology state, registers / removes
    the channel trigger, and updates the creature's ``listen_channels``
    / ``send_channels`` lists.  Mirrors the body of
    ``studio.sessions.topology.wire_creature`` but lives in the
    terrarium tier so the service Protocol's ``wire_creature`` method
    can call it without a tier crossing.

    Resolves ``creature_id == "root"`` to the graph's privileged
    creature (same precedence rule as the studio helper).
    """
    _channels = _terrarium_channels
    _topo = _terrarium_topology

    if creature_id == "root":
        graph = engine.get_graph(graph_id)
        privileged: list = []
        for cid in sorted(graph.creature_ids):
            try:
                c = engine.get_creature(cid)
            except KeyError:
                continue
            if getattr(c, "is_privileged", False):
                privileged.append(c)
        if not privileged:
            raise KeyError(f"session {graph_id!r} has no privileged creature")
        chosen = (
            next((c for c in privileged if c.creature_id == "root"), None)
            or next((c for c in privileged if c.name == "root"), None)
            or privileged[0]
        )
        creature_id = chosen.creature_id

    graph = engine.get_graph(graph_id)
    if creature_id not in graph.creature_ids:
        raise KeyError(f"creature {creature_id!r} not in session {graph_id!r}")
    creature = engine.get_creature(creature_id)
    if channel not in graph.channels:
        raise KeyError(f"channel {channel!r} not in session {graph_id!r}")
    if direction == "listen":
        _topo.set_listen(engine._topology, creature_id, channel, listening=enabled)
        if enabled:
            env = engine._environments.get(graph_id)
            registry = (
                getattr(env, "shared_channels", None) if env is not None else None
            )
            if registry is None:
                raise KeyError(f"session {graph_id!r} has no shared channel registry")
            _channels.register_channel_in_environment(
                registry, graph.channels[channel], engine=engine, graph_id=graph_id
            )
            _channels.inject_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=channel,
                registry=registry,
                ignore_sender=creature.name,
                ignore_sender_id=creature.creature_id,
            )
            if channel not in creature.listen_channels:
                creature.listen_channels.append(channel)
        else:
            _channels.remove_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=channel,
            )
            if channel in creature.listen_channels:
                creature.listen_channels.remove(channel)
    elif direction == "send":
        _topo.set_send(engine._topology, creature_id, channel, sending=enabled)
        if enabled and channel not in creature.send_channels:
            creature.send_channels.append(channel)
        elif not enabled and channel in creature.send_channels:
            creature.send_channels.remove(channel)
    else:
        raise ValueError(f"direction must be 'listen' or 'send', got {direction!r}")

    # Emit TOPOLOGY_CHANGED so engine subscribers (notably the runtime
    # graph prompt block) refresh the affected creature's listen/send
    # lists.  Graph membership doesn't change here, so the delta kind is
    # "nothing" — listeners only care that the wire surface changed.
    engine._emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            creature_id=creature_id,
            graph_id=graph_id,
            payload={
                "kind": "nothing",
                "old_graph_ids": [graph_id],
                "new_graph_ids": [graph_id],
                "affected": [creature_id],
            },
        )
    )
    # Resume needs the post-mutation snapshot to replay this wire.
    _terrarium_topology_snap.snapshot(engine, graph_id)


async def agent_execute_command(
    agent: Any,
    command: str,
    args: str = "",
) -> dict[str, Any]:
    """Run a built-in slash command against ``agent``.

    Raises ``ValueError`` for an unknown command name; the
    ``UserCommandResult`` is normalized to a plain dict suitable for
    JSON response / Lab wire transit.
    """
    cmd = get_builtin_user_command(command)
    if cmd is None:
        raise ValueError(f"Unknown command: /{command}")
    ctx = UserCommandContext(agent=agent, session=getattr(agent, "session", None))
    result = await cmd.execute(args, ctx)
    resp: dict[str, Any] = {
        "command": command,
        "output": result.output,
        "error": result.error,
        "success": result.success,
    }
    if result.data is not None:
        resp["data"] = result.data
    return resp


# ---------------------------------------------------------------------------
# Chat history / branches — touches agent + session_store
# ---------------------------------------------------------------------------


def _resumable_events(
    store: Any, name: str, live_jobs: set[str]
) -> list[dict[str, Any]]:
    if store is None:
        return []
    try:
        return store.get_resumable_events(name, live_job_ids=live_jobs)
    except Exception:
        return []


def chat_history_for(engine: Terrarium, creature_id: str) -> dict[str, Any]:
    creature = engine.get_creature(creature_id)
    agent = creature.agent
    live_jobs = set(getattr(agent, "_direct_job_meta", {}).keys())
    # Agent-attached store is primary; the engine's lifecycle-attached
    # store (``_session_stores[graph_id]``) is the fallback for agents
    # that never got an agent-level attach (older terrarium recipes).
    events = _resumable_events(
        getattr(agent, "session_store", None), creature.name, live_jobs
    )
    if not events:
        fallback = engine._session_stores.get(creature.graph_id)
        events = _resumable_events(fallback, creature.name, live_jobs)
    return {
        "creature_id": creature_id,
        "session_id": creature.graph_id,
        "messages": list(getattr(agent, "conversation_history", []) or []),
        "events": events,
        "is_processing": bool(getattr(agent, "_processing_task", None)),
    }


def chat_branches_for(engine: Terrarium, creature_id: str) -> list[dict[str, Any]]:
    creature = engine.get_creature(creature_id)
    agent = creature.agent
    fn = getattr(agent, "list_branches", None)
    if callable(fn):
        return list(fn())
    return []


# ---------------------------------------------------------------------------
# Attach policies — controller advertises what live streams each
# creature / session supports.  Pure agent + engine introspection.
# ---------------------------------------------------------------------------


def attach_policies_for(engine: Terrarium, creature_id: str) -> list[str]:
    policies: list[str] = ["log", "trace"]
    try:
        creature = engine.get_creature(creature_id)
    except KeyError:
        return policies
    agent = creature.agent
    inp = getattr(agent, "input_module", None) or getattr(agent, "_input", None)
    if inp is not None:
        policies.insert(0, "io")
    env = engine._environments.get(creature.graph_id)
    if env is not None and env.shared_channels.list_channels():
        policies.append("observer")
    return policies


def session_attach_policies_for(engine: Terrarium, session_id: str) -> list[str]:
    policies: list[str] = ["log", "observer", "trace"]
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
            policies.insert(0, "io")
            break
    return policies


# ---------------------------------------------------------------------------
# Runtime graph snapshot.  Pure engine introspection — same shape the
# graph editor API returns.
# ---------------------------------------------------------------------------


def build_runtime_graph_snapshot_for(
    engine: Terrarium,
    *,
    meta_lookup: Any = None,
) -> dict[str, Any]:
    """Build a normalized runtime-graph snapshot.

    ``meta_lookup`` is an optional callable ``(graph_id) -> dict``
    returning the studio-layer session metadata (kind / name /
    created_at / config_path / pwd / has_root).  The terrarium tier
    can't reach studio, so callers in api/route layer pass it in.  When
    absent, the snapshot still works — meta fields are filled with
    defaults derived from the graph itself.
    """
    graphs = []
    for graph in engine.list_graphs():
        graphs.append(_graph_to_dict(engine, graph, meta_lookup))
    return {
        "version": int(time.time() * 1000),
        "graphs": graphs,
    }


def _graph_to_dict(
    engine: Terrarium,
    graph: GraphTopology,
    meta_lookup: Any = None,
) -> dict[str, Any]:
    creatures = _creatures_for_graph(engine, graph)
    meta = meta_lookup(graph.graph_id) if callable(meta_lookup) else {}
    return {
        "graph_id": graph.graph_id,
        "kind": meta.get("kind") or ("terrarium" if len(creatures) > 1 else "creature"),
        "name": meta.get("name") or graph.graph_id,
        "created_at": meta.get("created_at", ""),
        "config_path": meta.get("config_path", ""),
        "pwd": meta.get("pwd", ""),
        "has_root": bool(meta.get("has_root", False)),
        "creatures": creatures,
        "channels": _channels_for_graph(engine, graph),
        "output_edges": _output_edges_for_graph(engine, graph, creatures),
    }


def _creatures_for_graph(engine: Terrarium, graph: GraphTopology) -> list[dict]:
    """Serialize creatures with ``is_root`` annotation.

    With multiple privileged creatures possible (post-merge, post-hot-plug),
    pick the one with ``creature_id == "root"`` first, then ``name == "root"``,
    then the lowest-sorted privileged id.  Mirrors the legacy graph-editor
    shape so ``api/routes/runtime_graph._creatures_for_graph`` can be removed.
    """
    creatures: list[dict[str, Any]] = []
    privileged_ids: list[str] = []
    raw: list[tuple[str, Any]] = []
    for creature_id in sorted(graph.creature_ids):
        try:
            creature = engine.get_creature(creature_id)
        except KeyError:
            continue
        raw.append((creature_id, creature))
        if getattr(creature, "is_privileged", False):
            privileged_ids.append(creature_id)
    root_id = ""
    if privileged_ids:
        for cid in privileged_ids:
            if cid == "root":
                root_id = cid
                break
        if not root_id:
            for cid in privileged_ids:
                c = engine._creatures.get(cid)
                if c is not None and getattr(c, "name", "") == "root":
                    root_id = cid
                    break
        if not root_id:
            root_id = privileged_ids[0]
    for creature_id, creature in raw:
        status = dict(creature.get_status())
        status["is_privileged"] = bool(getattr(creature, "is_privileged", False))
        status["is_root"] = creature_id == root_id
        status["parent_creature_id"] = getattr(creature, "parent_creature_id", None)
        status["graph_id"] = graph.graph_id
        creatures.append(status)
    return creatures


def _channels_for_graph(engine: Terrarium, graph: GraphTopology) -> list[dict]:
    env = engine._environments.get(graph.graph_id)
    registry = getattr(env, "shared_channels", None) if env is not None else None
    names = set(graph.channels)
    if registry is not None:
        names.update(registry.list_channels())
    out: list[dict[str, Any]] = []
    for name in sorted(names):
        topo_info = graph.channels.get(name)
        runtime_channel = registry.get(name) if registry is not None else None
        description = (
            getattr(topo_info, "description", "") if topo_info is not None else ""
        )
        history = list(getattr(runtime_channel, "history", []) or [])
        out.append(
            {
                "name": name,
                "type": "broadcast",
                "description": description or "",
                "qsize": int(getattr(runtime_channel, "qsize", 0) or 0),
                "message_count": len(history),
            }
        )
    return out


def _output_edges_for_graph(
    engine: Terrarium,
    graph: GraphTopology,
    creatures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for creature in creatures:
        creature_id = creature.get("creature_id") or creature.get("agent_id")
        if not creature_id:
            continue
        try:
            output_edges = engine.list_output_wiring(creature_id)
        except Exception:
            output_edges = []
        for edge in output_edges:
            edge_dict = dict(edge)
            edge_id = edge_dict.get("edge_id") or edge_dict.get("id", "")
            target = edge_dict.get("to", "")
            edge_dict["edge_id"] = edge_id
            edge_dict["from"] = creature_id
            edge_dict["from_name"] = creature.get("name", "")
            edge_dict["to_creature_id"] = _resolve_target_creature_id(
                graph, creatures, target
            )
            edge_dict["graph_id"] = graph.graph_id
            edges.append(edge_dict)
    return edges


def _resolve_target_creature_id(
    graph: GraphTopology,
    creatures: list[dict[str, Any]],
    target: str,
) -> str:
    if not target:
        return ""
    by_id = {
        c.get("creature_id") or c.get("agent_id"): c
        for c in creatures
        if c.get("creature_id") or c.get("agent_id")
    }
    if target in by_id:
        return target
    for creature in creatures:
        creature_id = creature.get("creature_id") or creature.get("agent_id") or ""
        if target == creature.get("name"):
            return creature_id
        if target == "root" and creature.get("is_root"):
            return creature_id
    if target in graph.creature_ids:
        return target
    return ""


def normalize_command_args(args: str | dict[str, Any] | None) -> str:
    """Coerce ``execute_command`` args to the canonical ``str`` form.

    The HTTP schema uses ``args: str = ""`` (e.g. ``"--flag value"``).
    Programmatic Python callers occasionally pass a dict; we accept
    both shapes consistently across LocalImpl, RemoteImpl, and the
    worker adapter so a dict-arg call doesn't silently degrade to an
    empty string on the wire path.
    """
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        # Preferred convention: ``{"args": "..."}`` (back-compat).
        # Coerce the canonical value to ``str`` so an int / bool /
        # other primitive doesn't get silently re-routed through the
        # key=value fallback (which would produce ``"args=42"`` for
        # ``{"args": 42}`` — semantically different from passing
        # ``"42"`` directly).
        if "args" in args:
            return str(args["args"])
        return " ".join(f"{k}={v}" for k, v in args.items())
    return str(args)


__all__ = [
    "agent_env",
    "agent_execute_command",
    "agent_get_module_options",
    "agent_get_native_tool_options",
    "agent_get_plugin_options",
    "agent_list_modules",
    "agent_list_plugins",
    "agent_native_tool_inventory",
    "agent_patch_scratchpad",
    "agent_plugin_inventory",
    "agent_scratchpad",
    "agent_set_module_options",
    "agent_set_native_tool_options",
    "agent_set_plugin_options",
    "agent_set_working_dir",
    "agent_system_prompt",
    "agent_toggle_module",
    "agent_toggle_plugin",
    "agent_triggers",
    "agent_working_dir",
    "attach_policies_for",
    "build_runtime_graph_snapshot_for",
    "chat_branches_for",
    "chat_history_for",
    "normalize_command_args",
    "session_attach_policies_for",
]
