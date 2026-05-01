"""Per-creature plugin routes — list + toggle.

Plugin **option** mutation lives in the unified module system —
see :mod:`creatures_modules` (``/modules/plugin/{name}/options``).
This file keeps only the plugin-list / toggle routes that pre-date
the module unification.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import creature_plugins

router = APIRouter()


@router.get("/{session_id}/creatures/{creature_id}/plugins")
async def list_plugins(session_id: str, creature_id: str, engine=Depends(get_engine)):
    try:
        return creature_plugins.list_plugins(engine, session_id, creature_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/plugins/{plugin_name}/toggle")
async def toggle_plugin(
    session_id: str,
    creature_id: str,
    plugin_name: str,
    engine=Depends(get_engine),
):
    try:
        return await creature_plugins.toggle_plugin(
            engine, session_id, creature_id, plugin_name
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(404, str(e))
