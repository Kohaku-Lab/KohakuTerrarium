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
    session_id: str | None = None,
) -> str:
    """Return the canonical ``creature_id`` for either form.

    Matches the exact ``creature_id`` first (the common case once a
    tab is opened), then falls back to ``name`` match — matching the
    pre-v2 ``find_creature`` semantics.

    ``session_id`` (the graph_id from the URL) MUST be passed by
    every route that owns a session-scoped path.  Without it, the
    name-fallback walks every creature visible through the service
    and returns the first hit — which means two running sessions
    that share a creature ``name`` (the common case: two
    ``creative-art`` instances of the same config) both resolve to
    whichever session was created first.  The downstream
    ``service.chat_history(cid)`` then returns the FIRST session's
    transcript for both, producing the "newly-created session
    shares the old session's chat content" bug.

    The fix: when ``session_id`` is provided, filter the creature
    roster to that graph before either match phase.  Exact
    ``creature_id`` matches are also filtered — a creature with the
    target id BUT in a different graph means the URL was tampered
    with (or the frontend kept a stale handle); 404 is the right
    answer in both cases.

    ``session_id=None`` retains the global-search semantics for
    legacy callers + tests that pre-date the v2 session-scoped
    routes; new code paths SHOULD pass the session_id.

    Raises :class:`HTTPException` 404 when neither id nor name
    matches any creature visible through the service (filtered by
    session if provided).
    """
    try:
        creatures = await service.list_creatures()
    except Exception as exc:  # noqa: BLE001 — service errors map to 503
        raise HTTPException(503, f"service unavailable: {exc}") from exc

    if session_id:
        creatures = tuple(c for c in creatures if c.graph_id == session_id)

    # Exact id match wins.
    for info in creatures:
        if info.creature_id == name_or_id:
            return info.creature_id
    # Display-name fallback (now scoped to the requested session when
    # session_id is set — see the docstring rationale above).
    for info in creatures:
        if info.name == name_or_id:
            return info.creature_id
    raise HTTPException(404, f"creature {name_or_id!r} not found")


__all__ = ["resolve_creature_id"]
