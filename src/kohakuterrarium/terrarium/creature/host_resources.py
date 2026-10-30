"""Read the engine's host-resources flag from inside a creature."""

import weakref
from typing import Any

from kohakuterrarium.terrarium.channels.channels import TERRARIUM_ENGINE_KEY


def host_resources_allowed(source: Any) -> bool:
    """Return whether the creature behind ``source`` may use host-wide resources.

    ``source`` may be a ``ToolContext``, a ``PluginContext``, a bound trigger,
    an ``Agent`` or a ``Creature``. A source outside any Terrarium (a solo
    agent, or one whose engine is gone) counts as allowed.
    """
    environment = _environment_of(source)
    if environment is None:
        return True
    engine = environment.get(TERRARIUM_ENGINE_KEY)
    if isinstance(engine, weakref.ref):
        engine = engine()
    if engine is None:
        return True
    return bool(getattr(engine, "host_resources", True))


def _environment_of(source: Any) -> Any:
    environment = getattr(source, "environment", None)
    if environment is not None:
        return environment
    agent = getattr(source, "host_agent", None) or getattr(source, "agent", None)
    return getattr(agent, "environment", None) if agent is not None else None
