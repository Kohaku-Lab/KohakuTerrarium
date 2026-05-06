"""Group management tool surface.

Two registration tiers:

- :data:`ENGINE_BASIC_TOOL_NAMES` — ``send_channel`` (broadcast on a
  wired channel) and ``group_send`` (point-to-point direct event).
  Registered on **every** engine-backed creature regardless of
  privilege. Their bodies enforce per-call gates (send-edge required
  for ``send_channel``; non-privileged → privileged-target-only for
  ``group_send``), so handing them out is safe.

- :data:`PRIVILEGED_TOOL_NAMES` — graph-mutating tools (status / add /
  remove / start / stop / channel / wire). Registered only on
  privileged creatures.

The nine tools are split across sibling modules to keep each file
under the project's per-file budget:

- :mod:`tools_group_status` — ``group_status``
- :mod:`tools_group_lifecycle` — add / remove / start / stop node
- :mod:`tools_group_channel` — channel CRUD + per-creature wiring
- :mod:`tools_group_wire` — output-wire add / remove
- :mod:`tools_group_send` — ``group_send`` and ``send_channel``

Importing this module imports all of them, which fires every
``@register_builtin`` decorator.
"""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

# Importing the submodules registers every group_* tool via their
# ``@register_builtin`` decorators. Module-direct imports (rather than
# ``from kohakuterrarium.terrarium import ...``) avoid pulling the
# package __init__ into the dep graph, which would create a cycle with
# engine.py.
import kohakuterrarium.terrarium.tools_group_channel as _channel_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_lifecycle as _lifecycle_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_send as _send_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_status as _status_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_wire as _wire_mod  # noqa: F401

#: Tools every engine-backed creature gets — comm primitives that
#: gate themselves at call time.
ENGINE_BASIC_TOOL_NAMES: tuple[str, ...] = (
    "send_channel",
    "group_send",
)

#: Tools only privileged creatures get — graph-mutating surface.
PRIVILEGED_TOOL_NAMES: tuple[str, ...] = (
    "group_status",
    "group_add_node",
    "group_remove_node",
    "group_start_node",
    "group_stop_node",
    "group_channel",
    "group_wire",
)

#: Union of both tiers — used by registration tests / introspection.
GROUP_TOOL_NAMES: tuple[str, ...] = ENGINE_BASIC_TOOL_NAMES + PRIVILEGED_TOOL_NAMES


def _register_named(agent: Any, names: tuple[str, ...]) -> None:
    """Register every tool in ``names`` on ``agent``. Idempotent —
    tools already present are skipped. Silently no-ops when the
    agent's registry doesn't support ``register_tool`` (test fakes)."""
    registry = getattr(agent, "registry", None)
    executor = getattr(agent, "executor", None)
    if registry is None:
        return
    register = getattr(registry, "register_tool", None)
    get_tool = getattr(registry, "get_tool", None)
    if not callable(register):
        return
    for name in names:
        if callable(get_tool) and get_tool(name) is not None:
            continue
        tool = get_builtin_tool(name)
        if tool is None:
            continue
        try:
            register(tool)
        except Exception:
            continue
        if executor is not None:
            try:
                executor.register_tool(tool)
            except Exception:
                pass


def force_register_basic_tools(agent: Any) -> None:
    """Register the engine-tier comm tools (``send_channel``,
    ``group_send``) on every engine-backed creature. Called from
    :meth:`Terrarium.add_creature` for all creatures regardless of
    ``is_privileged``."""
    _register_named(agent, ENGINE_BASIC_TOOL_NAMES)


def force_register_privileged_tools(agent: Any) -> None:
    """Register the privileged graph-mutating tools on a creature's
    agent. Called from :meth:`Terrarium.add_creature` when
    ``is_privileged=True`` and from :meth:`Terrarium.assign_root`
    when post-creation elevation occurs."""
    _register_named(agent, PRIVILEGED_TOOL_NAMES)


def force_register_group_tools(agent: Any) -> None:
    """Backward-compat helper — registers both tiers on a privileged
    creature. New code should call :func:`force_register_basic_tools`
    and :func:`force_register_privileged_tools` separately based on
    privilege."""
    _register_named(agent, GROUP_TOOL_NAMES)
