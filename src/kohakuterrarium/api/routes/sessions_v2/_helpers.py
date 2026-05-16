"""Shared helpers for the v2 per-creature routes.

The headline helper is :func:`resolve_creature_id`: it accepts either
the engine's exact ``creature_id`` (``alice_4f69d138``) or the
display ``name`` the frontend prefers (``alice``), and returns the
canonical ``creature_id`` the service Protocol expects.

Without this resolution every panel that stores the name (chat tab
title, plugin / module / scratchpad / triggers / env panes) would
404 on the v2 routes — those route handlers call
``service.<op>(creature_id)`` directly and the underlying engine
uses strict-id lookup only.

The resolver consults :func:`service.list_creatures` (works on
``LocalTerrariumService`` and ``MultiNodeTerrariumService`` alike;
the latter caches a fan-out so per-route cost stays small).  Routes
that need this:

- ``creatures_state.py``   — scratchpad / triggers / env / system_prompt /
  working_dir / native_tool_options
- ``creatures_plugins.py`` — list_plugins / toggle_plugin
- ``creatures_modules.py`` — modules list / options / toggle
- ``creatures_chat.py``    — chat / regenerate / edit / rewind / history /
  branches
- ``creatures_ctl.py``     — interrupt / jobs / stop / promote
- ``creatures_command.py`` — slash command execution
- ``creatures_model.py``   — switch_model
- ``wiring.py``            — output_wiring CRUD
"""

from fastapi import HTTPException

from kohakuterrarium.terrarium.service import TerrariumService


async def resolve_creature_id(
    service: TerrariumService,
    name_or_id: str,
) -> str:
    """Return the canonical ``creature_id`` for either form.

    Matches the exact ``creature_id`` first (the common case once a
    tab is opened), then falls back to ``name`` match — matching the
    pre-v2 ``find_creature`` semantics.

    Raises :class:`HTTPException` 404 when neither id nor name
    matches any creature visible through the service.  Errors are
    raised at the route boundary so callers can use it inline without
    a second try/except.
    """
    try:
        creatures = await service.list_creatures()
    except Exception as exc:  # noqa: BLE001 — service errors map to 503
        raise HTTPException(503, f"service unavailable: {exc}") from exc

    # Exact id match wins.
    for info in creatures:
        if info.creature_id == name_or_id:
            return info.creature_id
    # Display-name fallback.
    for info in creatures:
        if info.name == name_or_id:
            return info.creature_id
    raise HTTPException(404, f"creature {name_or_id!r} not found")


__all__ = ["resolve_creature_id"]
