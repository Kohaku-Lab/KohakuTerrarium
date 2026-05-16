"""Per-creature state: scratchpad + triggers + env + system_prompt +
working_dir + native_tool_options.

Replaces a swathe of legacy route handlers + manager methods that
all pull state directly off the underlying ``Agent`` instance.
"""

import os
from typing import Any

from kohakuterrarium.core.scratchpad import is_reserved_scratchpad_key
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.studio._runtime import as_engine

# Env var keys that must be filtered out of /env responses.
_ENV_REDACT_SUBSTRINGS = (
    "secret",
    "key",
    "token",
    "password",
    "pass",
    "private",
    "auth",
    "credential",
)


def _redacted_env() -> dict[str, str]:
    """Return os.environ with credentials filtered."""
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        lk = k.lower()
        if any(sub in lk for sub in _ENV_REDACT_SUBSTRINGS):
            continue
        out[k] = v
    return out


def _get_agent(engine: Terrarium, session_id: str, creature_id: str) -> Any:
    return find_creature(engine, session_id, creature_id).agent


def _agent_working_dir(agent: Any) -> str:
    """Resolve a creature's working directory.

    The working directory is owned by the workspace helper, or by
    ``agent.executor._working_dir`` — never by a bare
    ``agent._working_dir`` attribute (``Agent`` never sets one). Both
    ``get_env`` and ``get_working_dir`` route through this helper so
    they can never report different paths for the same creature.
    """
    ws = getattr(agent, "workspace", None)
    if ws is None:
        return str(getattr(agent.executor, "_working_dir", ""))
    return ws.get()


# ---------------------------------------------------------------------------
# scratchpad
# ---------------------------------------------------------------------------


def get_scratchpad(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, str]:
    engine = as_engine(service)
    return _get_agent(engine, session_id, creature_id).scratchpad.to_dict()


def patch_scratchpad(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    updates: dict[str, str | None],
) -> dict[str, str]:
    engine = as_engine(service)
    pad = _get_agent(engine, session_id, creature_id).scratchpad
    for key, value in updates.items():
        if is_reserved_scratchpad_key(key):
            raise ValueError(f"Reserved scratchpad key: {key}")
        if value is None:
            pad.delete(key)
        else:
            pad.set(key, value)
    return pad.to_dict()


# ---------------------------------------------------------------------------
# triggers (read-only)
# ---------------------------------------------------------------------------


def list_triggers(
    service: "TerrariumService", session_id: str, creature_id: str
) -> list[dict[str, Any]]:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    tm = agent.trigger_manager
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
# env + system prompt
# ---------------------------------------------------------------------------


def get_env(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, Any]:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    pwd = _agent_working_dir(agent) or os.getcwd()
    return {"pwd": str(pwd), "env": _redacted_env()}


def get_system_prompt(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, str]:
    engine = as_engine(service)
    return {"text": _get_agent(engine, session_id, creature_id).get_system_prompt()}


# ---------------------------------------------------------------------------
# working dir
# ---------------------------------------------------------------------------


def get_working_dir(
    service: "TerrariumService", session_id: str, creature_id: str
) -> str:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    return _agent_working_dir(agent)


def set_working_dir(
    service: "TerrariumService", session_id: str, creature_id: str, new_path: str
) -> str:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    ws = getattr(agent, "workspace", None)
    if ws is None:
        raise RuntimeError(f"Creature {creature_id!r} has no workspace helper")
    return ws.set(new_path)


# ---------------------------------------------------------------------------
# native tool options
# ---------------------------------------------------------------------------


def native_tool_inventory(
    service: "TerrariumService", session_id: str, creature_id: str
) -> list[dict]:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    registry = agent.registry
    helper = getattr(agent, "native_tool_options", None)
    out: list[dict] = []
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


def get_native_tool_options(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, dict]:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    helper = getattr(agent, "native_tool_options", None)
    if helper is None:
        return {}
    return helper.list()


def set_native_tool_options(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    tool: str,
    values: dict[str, Any],
) -> dict:
    engine = as_engine(service)
    agent = _get_agent(engine, session_id, creature_id)
    helper = getattr(agent, "native_tool_options", None)
    if helper is None:
        raise ValueError(f"Creature {creature_id!r} has no native_tool_options helper")
    return helper.set(tool, values or {})
